#!/usr/bin/env python3
"""
EPUB viewer — patched to avoid previous gtk_box_append error by using a single
top-level container. Keeps monkey-patch for ebooklib NCX, sidebar TOC from
nav.xhtml, prev/next navigation, resource fixing, and CSS extraction.
"""
import gi, os, tempfile, traceback, shutil, urllib.parse
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

# --- Monkey-patch ebooklib to skip broken NCX parsing (minimal, safe) ---
import ebooklib.epub
def _safe_parse_ncx(self, ncxFile):
    self.book.toc = []
ebooklib.epub.EpubReader._parse_ncx = _safe_parse_ncx

APP_NAME = "EPUB Viewer"
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")


class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        self.set_title(APP_NAME)

        # state
        self.book = None
        self.items = []
        self.item_map = {}
        self.current_index = 0
        self.temp_dir = None
        self.css_content = ""

        # top-level container (only one set_content call)
        self.main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_vbox)

        # Split view (sidebar + content)
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.2)
        self.main_vbox.append(self.split)

        # Sidebar (with headerbar)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label=APP_NAME))
        sidebar_box.append(sidebar_header)

        # Open button in sidebar header
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open EPUB")
        open_btn.connect("clicked", self.open_file)
        sidebar_header.pack_start(open_btn)


        # TOC list inside a scrolled window
        self.toc_list = Gtk.ListBox()
        self.toc_list.add_css_class("navigation-sidebar")
        self.toc_list.connect("row-activated", self.on_toc_row_activated)

        toc_scrolled = Gtk.ScrolledWindow()
        toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toc_scrolled.set_vexpand(True)
        toc_scrolled.set_child(self.toc_list)

        sidebar_box.append(toc_scrolled)

        self.split.set_sidebar(sidebar_box)

        # Content area: toolbar view + headerbar + scrolled webview/textview
        self.toolbar = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.content_header)
        self.split.set_content(self.toolbar)

        # scrolled region
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.toolbar.set_content(self.scrolled)

        # WebKit or fallback
        try:
            gi.require_version('WebKit', '6.0')
            from gi.repository import WebKit
            self.WebKit = WebKit
            self.webview = WebKit.WebView()
            self.scrolled.set_child(self.webview)
            self.webview.connect("decide-policy", self.on_decide_policy)
        except Exception:
            self.WebKit = None
            self.webview = None
            self.textview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)

        # Bottom navigation controls
        self.bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.bottom_bar.set_margin_top(6); self.bottom_bar.set_margin_bottom(6)
        self.bottom_bar.set_margin_start(6); self.bottom_bar.set_margin_end(6)
        self.main_vbox.append(self.bottom_bar)

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

    # ---------- File open ----------
    def open_file(self, *_):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        epub_filter = Gtk.FileFilter(); epub_filter.add_pattern("*.epub"); epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)
        all_filter = Gtk.FileFilter(); all_filter.add_pattern("*"); all_filter.set_name("All Files")
        filter_list.append(all_filter)
        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                self.load_epub(f.get_path())
        except GLib.Error:
            pass

    # ---------- EPUB loading ----------
    def load_epub(self, path):
        try:
            self.cleanup()
            self.book = epub.read_epub(path)

            # build items: documents only, ordered by spine if available
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
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
                self.show_error("No document items found in EPUB")
                return

            # extract all files to temp_dir so resources are accessible via file://
            self.temp_dir = tempfile.mkdtemp()
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue
                full_path = os.path.join(self.temp_dir, item_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'wb') as f:
                    f.write(item.get_content())

            # map for internal link resolution
            self.item_map = {item.get_name(): item for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}

            # css
            self.extract_css()

            # set window title to book title if present
            title = APP_NAME
            try:
                meta = self.book.get_metadata('DC', 'title')
                if meta and meta[0]:
                    candidate = meta[0][0] if isinstance(meta[0], (list, tuple)) else meta[0]
                    if candidate:
                        title = candidate
            except Exception:
                try:
                    if getattr(self.book, 'title', None):
                        title = self.book.title
                except Exception:
                    pass
            self.set_title(title or APP_NAME)
            self.content_header.set_title_widget(Gtk.Label(label=title))

            # populate sidebar TOC from nav.xhtml primarily
            self._populate_toc_list()

            # nav state
            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception:
            print(traceback.format_exc())
            self.show_error("Error loading EPUB — see console for details")

    # ---------- TOC population (use nav.xhtml if available) ----------
    def _populate_toc_list(self):
        # clear existing rows
        child = self.toc_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.toc_list.remove(child)
            child = next_child

        # 1. Try nav.xhtml (EPUB 3)
        try:
            nav_item = self.book.get_item_with_id("nav")
            if nav_item:
                soup = BeautifulSoup(nav_item.get_content(), "html.parser")
                toc_nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", {"role": "doc-toc"})
                if toc_nav:
                    for a in toc_nav.find_all("a", href=True):
                        label = a.get_text(strip=True) or os.path.basename(a["href"])
                        href = a["href"].split("#")[0]
                        row = Gtk.ListBoxRow()
                        row.set_activatable(True)
                        row.set_child(Gtk.Label(label=label, xalign=0))
                        for i, it in enumerate(self.items):
                            if it.get_name().endswith(href):
                                row._chapter_index = i
                                break
                        self.toc_list.append(row)
                    return
        except Exception:
            pass

        # 2. Try toc.ncx (EPUB 2)
        try:
            ncx_item = self.book.get_item_with_id("ncx")
            if ncx_item:
                soup = BeautifulSoup(ncx_item.get_content(), "xml")
                for np in soup.find_all("navPoint"):
                    label = np.find("text")
                    href = np.find("content")["src"] if np.find("content") else None
                    if not href:
                        continue
                    title = label.get_text(strip=True) if label else os.path.basename(href)
                    href = href.split("#")[0]
                    row = Gtk.ListBoxRow()
                    row.set_activatable(True)
                    row.set_child(Gtk.Label(label=title, xalign=0))
                    for i, it in enumerate(self.items):
                        if it.get_name().endswith(href):
                            row._chapter_index = i
                            break
                    self.toc_list.append(row)
                return
        except Exception:
            pass

        # 3. Fallback: filenames
        for idx, it in enumerate(self.items):
            label = os.path.basename(it.get_name()) or f"Chapter {idx+1}"
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            row.set_child(Gtk.Label(label=label, xalign=0))
            row._chapter_index = idx
            self.toc_list.append(row)


    def on_toc_row_activated(self, listbox, row):
        if hasattr(row, "_chapter_index"):
            self.current_index = row._chapter_index
            self.update_navigation()
            self.display_page()

    # ---------- CSS extraction ----------
    def extract_css(self):
        self.css_content = ""
        for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
            try:
                self.css_content += item.get_content().decode("utf-8") + "\n"
            except Exception:
                pass
        try:
            for fn in ("core.css", "se.css", "style.css"):
                p = os.path.join(self.temp_dir or "", fn)
                if p and os.path.exists(p):
                    with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                        self.css_content += f.read() + "\n"
        except Exception:
            pass

    # ---------- Internal link handling for WebKit ----------
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
                candidates.append(uq); candidates.append(os.path.basename(uq))
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

    # ---------- Display page ----------
    def display_page(self):
        if not self.book or not self.items:
            return
        if self.current_index < 0: self.current_index = 0
        if self.current_index >= len(self.items): self.current_index = len(self.items) - 1

        item = self.items[self.current_index]
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        item_dir = os.path.dirname(item.get_name())

        for tag in soup.find_all(['img', 'link', 'script', 'source']):
            src = None; attr = None
            if tag.name in ('img', 'source'):
                src = tag.get('src') or tag.get('xlink:href'); attr = 'src'
            elif tag.name == 'link':
                if tag.get('rel') and 'stylesheet' in tag.get('rel'):
                    src = tag.get('href'); attr = 'href'
            elif tag.name == 'script':
                src = tag.get('src'); attr = 'src'
            if not src:
                continue
            if src.startswith(('http://', 'https://', 'data:', 'file://', 'mailto:')):
                continue
            normalized = os.path.normpath(os.path.join(item_dir, src)).replace(os.sep, '/')
            extracted_path = os.path.join(self.temp_dir or '', normalized)
            if os.path.exists(extracted_path):
                tag[attr] = f"file://{extracted_path}"
            else:
                alt = os.path.join(self.temp_dir or '', os.path.basename(src))
                if os.path.exists(alt):
                    tag[attr] = f"file://{alt}"

        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith(('http://', 'https://', 'mailto:')): continue
            if href.startswith('#'): continue
            parts = href.split('#', 1)
            target = parts[0]; frag = f"#{parts[1]}" if len(parts) > 1 else ""
            matched = None
            cand_list = [target, os.path.basename(target)]
            try:
                uq = urllib.parse.unquote(target)
                if uq != target:
                    cand_list.append(uq); cand_list.append(os.path.basename(uq))
            except Exception:
                pass
            for c in cand_list:
                if c in self.item_map:
                    matched = c; break
            if matched:
                extracted = os.path.join(self.temp_dir or '', matched)
                if os.path.exists(extracted):
                    link['href'] = f"file://{extracted}{frag}"

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
            base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
            try:
                self.webview.load_html(html_content, base_uri)
            except TypeError:
                try:
                    self.webview.load_html(html_content)
                except Exception:
                    pass
        else:
            buffer = self.textview.get_buffer()
            buffer.set_text(soup.get_text())

        total_items = len(self.items) if self.items else 1
        progress = (self.current_index + 1) / total_items if total_items > 0 else 0
        self.progress.set_fraction(progress)
        self.progress.set_text(f"{self.current_index + 1}/{total_items}")

    # ---------- Navigation controls ----------
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

    # ---------- Error dialog ----------
    def show_error(self, message):
        try:
            dialog = Adw.MessageDialog.new(self, "Error", message)
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception:
            print("Error dialog failed:", message)

    # ---------- Cleanup ----------
    def cleanup(self):
        if getattr(self, 'temp_dir', None) and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass
        self.temp_dir = None
        self.book = None
        self.items = []
        self.item_map = {}
        self.css_content = ""
        self.current_item = None
        self.current_index = 0
        child = getattr(self, 'toc_list', None) and self.toc_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.toc_list.remove(child)
            child = next_child
        self.update_navigation()


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer")
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

