#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, GLib
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import tempfile
import urllib.parse
import traceback
import zipfile
import shutil
from xml.etree import ElementTree as ET

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

APP_NAME = "app name"

def fix_epub_ncx(src_epub_path, dst_epub_path=None):
    if dst_epub_path is None:
        fd, dst_epub_path = tempfile.mkstemp(suffix='.epub')
        os.close(fd)
    tmpdir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(src_epub_path, 'r') as zin:
            zin.extractall(tmpdir)
            ncx_paths = []
            for root, _, files in os.walk(tmpdir):
                for fn in files:
                    if fn.lower().endswith('.ncx'):
                        ncx_paths.append(os.path.join(root, fn))

            for ncx in ncx_paths:
                try:
                    tree = ET.parse(ncx)
                    root = tree.getroot()
                    # iterate navPoint elements robustly
                    for navPoint in root.findall('.//'):
                        # check if this element is a navPoint
                        if navPoint.tag.split('}')[-1] != 'navPoint':
                            continue
                        # find navLabel child
                        navLabel = None
                        for c in navPoint:
                            if c.tag.split('}')[-1] == 'navLabel':
                                navLabel = c
                                break
                        if navLabel is None:
                            nl = ET.Element('navLabel')
                            text = ET.SubElement(nl, 'text')
                            text.text = 'item'
                            if len(navPoint):
                                navPoint.insert(0, nl)
                            else:
                                navPoint.append(nl)
                        else:
                            text_elem = None
                            for c2 in navLabel:
                                if c2.tag.split('}')[-1] == 'text':
                                    text_elem = c2
                                    break
                            if text_elem is None or (text_elem.text is None):
                                if text_elem is None:
                                    text_elem = ET.SubElement(navLabel, 'text')
                                text_elem.text = 'item'
                    tree.write(ncx, encoding='utf-8', xml_declaration=True)
                except Exception:
                    # skip problematic ncx but keep processing others
                    pass

        # rezip into dst_epub_path (preserve mimetype per EPUB spec)
        with zipfile.ZipFile(dst_epub_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            mimetype_path = os.path.join(tmpdir, 'mimetype')
            if os.path.exists(mimetype_path):
                zout.writestr('mimetype', open(mimetype_path, 'rb').read(), compress_type=zipfile.ZIP_STORED)
            for root, dirs, files in os.walk(tmpdir):
                for fn in files:
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, tmpdir)
                    if arc == 'mimetype':
                        continue
                    zout.write(full, arc)
        return dst_epub_path
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(800, 600)
        self.set_title(APP_NAME)
        self.book = None
        self.current_item = None
        self.temp_dir = None
        self.css_content = ""
        self.item_map = {}
        self.items = []
        self.current_index = 0
        self._fixed_epub_path = None
        self.setup_ui()

    def setup_ui(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        self.header_bar = Adw.HeaderBar()
        self.main_box.append(self.header_bar)

        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.set_tooltip_text("Open EPUB")
        self.open_btn.connect("clicked", self.open_file)
        self.header_bar.pack_start(self.open_btn)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.main_box.append(self.scrolled)

        try:
            gi.require_version('WebKit', '6.0')
            from gi.repository import WebKit
            self.WebKit = WebKit
            self.webview = WebKit.WebView()
            self.scrolled.set_child(self.webview)
            self.webview.connect("decide-policy", self.on_decide_policy)
        except Exception:
            self.textview = Gtk.TextView()
            self.textview.set_editable(False)
            self.textview.set_cursor_visible(False)
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)
            self.webview = None
            self.WebKit = None

        self.bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.bottom_bar.set_margin_top(6)
        self.bottom_bar.set_margin_bottom(6)
        self.bottom_bar.set_margin_start(6)
        self.bottom_bar.set_margin_end(6)
        self.main_box.append(self.bottom_bar)

        self.prev_btn = Gtk.Button(label="Previous")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)
        self.bottom_bar.append(self.prev_btn)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_valign(Gtk.Align.CENTER)
        self.progress.set_hexpand(True)
        self.bottom_bar.append(self.progress)

        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)
        self.bottom_bar.append(self.next_btn)

    def on_decide_policy(self, webview, decision, decision_type):
        if self.WebKit and decision_type == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            try:
                uri = decision.get_request().get_uri()
            except Exception:
                return False
            if uri and uri.startswith("file://"):
                if self.handle_internal_link(uri):
                    try:
                        decision.ignore()
                    except Exception:
                        pass
                    return True
        return False

    def handle_internal_link(self, uri):
        path = uri.replace("file://", "")
        base = path.split('#', 1)[0]
        if self.temp_dir and base.startswith(self.temp_dir):
            rel = os.path.relpath(base, self.temp_dir).replace(os.sep, '/')
        else:
            rel = base.replace(os.sep, '/')
        candidates = [rel, os.path.basename(rel)]
        try:
            uq = urllib.parse.unquote(rel)
            if uq != rel:
                candidates.append(uq)
                candidates.append(os.path.basename(uq))
        except Exception:
            pass
        for cand in candidates:
            if cand in self.item_map:
                target_name = cand
                for i, it in enumerate(self.items):
                    if it.get_name() == target_name:
                        self.current_index = i
                        self.update_navigation()
                        self.display_page()
                        return True
        return False

    def open_file(self, button):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)

        epub_filter = Gtk.FileFilter()
        epub_filter.add_pattern("*.epub")
        epub_filter.add_pattern("*.EPUB")
        epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)

        all_filter = Gtk.FileFilter()
        all_filter.add_pattern("*")
        all_filter.set_name("All Files")
        filter_list.append(all_filter)

        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            self.load_epub(path)
        except GLib.Error:
            pass

    def load_epub(self, path):
        try:
            self.cleanup()
            # create a fixed copy first to avoid ebooklib NCX parsing errors
            try:
                fixed = fix_epub_ncx(path)
                self._fixed_epub_path = fixed
                self.book = epub.read_epub(fixed)
            except Exception:
                # fallback to reading original if fix fails
                self.book = epub.read_epub(path)

            # build document items and order by spine (if available)
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
                iid = None
                try:
                    iid = getattr(it, 'id', None) or (it.get_id() if hasattr(it, 'get_id') else None)
                except Exception:
                    iid = None
                if not iid:
                    iid = it.get_name() or os.urandom(8).hex()
                id_map[iid] = it
            ordered = []
            try:
                spine = getattr(self.book, 'spine', None) or []
                for entry in spine:
                    sid = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
                    if sid in id_map:
                        ordered.append(id_map.pop(sid))
                ordered.extend(id_map.values())
                self.items = ordered
            except Exception:
                self.items = docs

            if not self.items:
                self.show_error("No documents found in EPUB")
                return

            self.temp_dir = tempfile.mkdtemp()
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue
                full_path = os.path.join(self.temp_dir, item_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'wb') as f:
                    f.write(item.get_content())

            self.item_map = {item.get_name(): item for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
            self.extract_css()

            title = APP_NAME
            try:
                meta = self.book.get_metadata('DC', 'title')
                if meta and isinstance(meta, list) and meta and meta[0]:
                    first = meta[0]
                    if isinstance(first, tuple) and first:
                        title_candidate = first[0]
                    else:
                        title_candidate = first
                    if title_candidate:
                        title = title_candidate
            except Exception:
                try:
                    if getattr(self.book, 'title', None):
                        title = self.book.title
                except Exception:
                    pass
            self.set_title(title or APP_NAME)

            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception as e:
            tb = traceback.format_exc()
            print(tb)
            self.show_error(f"Error loading EPUB: {str(e)}")

    def cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass
        self.temp_dir = None
        self.book = None
        self.items = []
        self.css_content = ""
        self.item_map = {}
        self.current_item = None
        self.current_index = 0
        # remove fixed epub copy if created
        if getattr(self, '_fixed_epub_path', None) and os.path.exists(self._fixed_epub_path):
            try:
                os.remove(self._fixed_epub_path)
            except Exception:
                pass
        self._fixed_epub_path = None
        self.set_title(APP_NAME)
        self.update_navigation()

    def extract_css(self):
        self.css_content = ""
        for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
            try:
                self.css_content += item.get_content().decode('utf-8') + "\n"
            except Exception:
                pass

    def _try_match_item(self, candidate, item_dir):
        cand_list = []
        if not candidate:
            return None
        cand_list.append(candidate)
        cand_list.append(os.path.normpath(os.path.join(item_dir, candidate)).replace(os.sep, '/'))
        cand_list.append(os.path.basename(candidate))
        cand_list.append(candidate.lstrip('./').lstrip('/'))
        try:
            uq = urllib.parse.unquote(candidate)
            cand_list.append(uq)
            cand_list.append(os.path.basename(uq))
        except Exception:
            pass
        for c in cand_list:
            if c in self.item_map:
                return c
        return None

    def display_page(self):
        if not self.book or not self.items:
            return
        if self.current_index < 0:
            self.current_index = 0
        if self.current_index >= len(self.items):
            self.current_index = len(self.items) - 1
        item = self.items[self.current_index]
        self.current_item = item
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        item_dir = os.path.dirname(item.get_name())

        for tag in soup.find_all(['img', 'link', 'script', 'source']):
            src = None
            attr = None
            if tag.name in ('img', 'source'):
                src = tag.get('src') or tag.get('xlink:href')
                attr = 'src'
            elif tag.name == 'link':
                if tag.get('rel') and 'stylesheet' in tag.get('rel'):
                    src = tag.get('href')
                    attr = 'href'
            elif tag.name == 'script':
                src = tag.get('src')
                attr = 'src'
            if not src:
                continue
            if src.startswith(('http://', 'https://', 'data:', 'file://', 'mailto:')):
                continue
            normalized = os.path.normpath(os.path.join(item_dir, src)).replace(os.sep, '/')
            extracted_path = os.path.join(self.temp_dir, normalized)
            if os.path.exists(extracted_path):
                tag[attr] = f"file://{extracted_path}"
            else:
                alt = os.path.join(self.temp_dir, os.path.basename(src))
                if os.path.exists(alt):
                    tag[attr] = f"file://{alt}"

        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith(('http://', 'https://', 'mailto:')):
                continue
            if href.startswith('#'):
                continue
            parts = href.split('#', 1)
            target = parts[0]
            frag = f"#{parts[1]}" if len(parts) > 1 else ""
            matched = self._try_match_item(target, item_dir)
            if matched:
                extracted = os.path.join(self.temp_dir, matched)
                if os.path.exists(extracted):
                    link['href'] = f"file://{extracted}{frag}"
                    continue

        content = str(soup)
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <style>
        body {{ font-family: sans-serif; margin: 20px; line-height: 1.6; color: #333; }}
        img {{ max-width: 100%; height: auto; }}
        h1,h2,h3,h4,h5,h6 {{ color: #222; }}
        {self.css_content}
    </style>
</head>
<body>{content}</body>
</html>
"""
        if self.webview:
            base_uri = f"file://{os.path.join(self.temp_dir, os.path.dirname(item.get_name()))}/"
            try:
                self.webview.load_html(html_content, base_uri)
            except TypeError:
                try:
                    self.webview.load_html(html_content)
                except Exception:
                    pass
        else:
            buffer = self.textview.get_buffer()
            buffer.set_text(content)

        total_items = len(self.items) if self.items else 1
        progress = (self.current_index + 1) / total_items if total_items > 0 else 0
        self.progress.set_fraction(progress)
        self.progress.set_text(f"{self.current_index + 1}/{total_items}")

    def update_navigation(self):
        total = len(self.items) if hasattr(self, 'items') and self.items else 0
        self.prev_btn.set_sensitive(getattr(self, 'current_index', 0) > 0)
        self.next_btn.set_sensitive(getattr(self, 'current_index', 0) < total - 1)

    def next_page(self, button):
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
            self.update_navigation()
            self.display_page()

    def prev_page(self, button):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_navigation()
            self.display_page()

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "OK")
        dialog.present()

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.EpubViewer')
        self.create_action('quit', self.quit, ['<primary>q'])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPubViewer(self)
        win.present()

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

def main():
    app = Application()
    return app.run(None)

if __name__ == "__main__":
    main()

