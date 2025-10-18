#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")  # workaround for DMABuf/EGL errors

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, PangoCairo

from ebooklib import epub

_INJECT_CSS = """<style id="__epub_viewer_css">
html,body{margin:0;padding:0;}
img, svg, video, iframe { max-width: 100% !important; height: auto !important; object-fit: contain !important; }
img { max-height: 80vh !important; }
</style>
"""

def inject_css_into_html(html: str) -> str:
    if re.search(r'<head\b', html, re.I):
        return re.sub(r'(<head\b[^>]*>)', r'\1' + _INJECT_CSS, html, flags=re.I)
    if re.search(r'<html\b', html, re.I):
        return re.sub(r'(<html\b[^>]*>)', r'\1<head>' + _INJECT_CSS + '</head>', html, flags=re.I)
    return "<!doctype html><head>" + _INJECT_CSS + "</head><body>" + html + "</body></html>"

class Writer(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.viewer")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = ViewerWindow(application=self)
        win.present()

class ViewerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("HTML/EPUB Viewer")
        self.set_default_size(1000, 700)

        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self._flat_toc = []

        # WebView
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.webview = WebKit.WebView()
        scroll.set_child(self.webview)
        self.webview.load_html("<!doctype html><html><body><p></p></body></html>", "file:///")

        # UI
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)
        toolbar_view = Adw.ToolbarView()
        main_box.append(toolbar_view)
        header = Adw.HeaderBar()
        header.add_css_class("flat-header")
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(controls)

        self.back_btn = Gtk.Button(icon_name="go-previous")
        self.back_btn.add_css_class("flat")
        self.back_btn.connect("clicked", lambda *_: self.webview.go_back() if self.webview.can_go_back() else None)
        controls.append(self.back_btn)

        self.forward_btn = Gtk.Button(icon_name="go-next")
        self.forward_btn.add_css_class("flat")
        self.forward_btn.connect("clicked", lambda *_: self.webview.go_forward() if self.webview.can_go_forward() else None)
        controls.append(self.forward_btn)

        open_btn = Gtk.Button(icon_name="document-open")
        open_btn.add_css_class("flat")
        open_btn.connect("clicked", self.on_open_clicked)
        controls.append(open_btn)

        self.toc_btn = Gtk.Button(icon_name="view-list-symbolic")
        self.toc_btn.add_css_class("flat")
        self.toc_btn.set_sensitive(False)
        self.toc_btn.connect("clicked", self.on_toc_clicked)
        controls.append(self.toc_btn)

        # font dropdown
        font_map = PangoCairo.FontMap.get_default()
        families = font_map.list_families()
        font_names = sorted([f.get_name() for f in families])
        font_store = Gtk.StringList(strings=font_names)
        self.font_dropdown = Gtk.DropDown(model=font_store)
        default_index = font_names.index("Sans") if "Sans" in font_names else 0
        self.font_dropdown.set_selected(default_index)
        self.font_dropdown.add_css_class("flat")
        self.font_dropdown.connect("notify::selected", self.on_font_family_changed)
        controls.append(self.font_dropdown)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(scroll)
        toolbar_view.set_content(content_box)

        self.connect("close-request", self.on_close_request)

        # update back/forward sensitivity when navigation happens
        self.webview.connect("notify::title", self._update_nav_buttons)
        self.webview.connect("notify::uri", self._update_nav_buttons)

    def _update_nav_buttons(self, *a):
        self.back_btn.set_sensitive(self.webview.can_go_back())
        self.forward_btn.set_sensitive(self.webview.can_go_forward())

    def on_open_clicked(self, btn):
        dialog = Gtk.FileDialog()
        filter_all = Gtk.FileFilter()
        filter_all.set_name("HTML / EPUB")
        filter_all.add_pattern("*.html")
        filter_all.add_pattern("*.htm")
        filter_all.add_pattern("*.epub")
        dialog.set_default_filter(filter_all)
        dialog.open(self, None, self.on_open_file_dialog_response)

    def on_open_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file:
                return
            basename = file.get_basename().lower()
            if basename.endswith('.epub'):
                self.load_epub_with_ebooklib(file)
            else:
                file.load_contents_async(None, self.load_html_callback)
        except GLib.Error as e:
            print("Open error:", e.message)

    def load_html_callback(self, file, result):
        try:
            ok, content, _ = file.load_contents_finish(result)
            if ok:
                html = content.decode()
                base = file.get_uri() or "file:///"
                self._cleanup_epub_tempdir()
                html = inject_css_into_html(html)
                self.webview.load_html(html, base)
        except GLib.Error as e:
            print("Load error:", e.message)

    def load_epub_with_ebooklib(self, gio_file):
        self._cleanup_epub_tempdir()
        path = gio_file.get_path()
        if not path:
            try:
                fd, tmp_epub = tempfile.mkstemp(suffix=".epub")
                os.close(fd)
                stream = gio_file.read(None)
                with open(tmp_epub, "wb") as f:
                    f.write(stream.read_bytes(stream.get_size()).get_data())
                path = tmp_epub
            except Exception:
                path = None
        if not path:
            print("Cannot access EPUB path")
            return

        tempdir = tempfile.mkdtemp(prefix="epub_")
        try:
            book = epub.read_epub(path)
            self._book = book

            # extract items to tempdir preserving file names
            for item in book.get_items():
                fn = getattr(item, 'file_name', None)
                if not fn:
                    continue
                full = os.path.join(tempdir, fn)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                content = item.get_content()
                with open(full, "wb") as f:
                    if isinstance(content, str):
                        f.write(content.encode("utf-8"))
                    else:
                        f.write(content)

            # build spine ordered list
            spine_hrefs = []
            for idref, _ in getattr(book, 'spine', []):
                try:
                    item = book.get_item_with_id(idref)
                except Exception:
                    item = None
                if item is None:
                    continue
                href = getattr(item, 'file_name', None)
                if href:
                    spine_hrefs.append(href)
            if not spine_hrefs:
                for item in book.get_items_of_type(epub.ITEM_DOCUMENT):
                    if getattr(item, 'file_name', None):
                        spine_hrefs.append(item.file_name)
            if not spine_hrefs:
                print("No document items found in EPUB")
                shutil.rmtree(tempdir, ignore_errors=True)
                return

            # concatenate bodies (capture head once) and inject CSS
            bodies = []
            head_html = None
            for rel in spine_hrefs:
                full = os.path.join(tempdir, rel)
                if not os.path.exists(full):
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read()
                if head_html is None:
                    m = re.search(r'(<head\b[^>]*>.*?</head>)', txt, re.S | re.I)
                    head_html = m.group(1) if m else ''
                m2 = re.search(r'<body\b[^>]*>(.*?)</body>', txt, re.S | re.I)
                bodies.append(m2.group(1) if m2 else txt)

            final_head = head_html or '<meta charset="utf-8">'
            first_rel = spine_hrefs[0]
            first_dir = os.path.dirname(os.path.join(tempdir, first_rel))
            base_href = "file://" + first_dir.replace(os.sep, '/') + "/"
            concatenated = f"<!doctype html>\n<html>\n{final_head}\n<base href=\"{base_href}\">\n<body>\n" + "\n<hr/>\n".join(bodies) + "\n</body>\n</html>"
            concatenated = inject_css_into_html(concatenated)

            self._epub_tempdir = tempdir
            self._base_href = base_href
            self.webview.load_html(concatenated, base_href)

            # enable TOC if present (ebooklib exposes as book.toc)
            toc = getattr(book, "toc", []) or []
            self._flat_toc = []
            if toc:
                def walk_toc(entries):
                    for e in entries:
                        # epub.Link objects
                        if isinstance(e, epub.Link):
                            title = e.title or e.href
                            href = e.href
                            self._flat_toc.append((title, href))
                        # tuples often: (title, href) or (title, href, children)
                        elif isinstance(e, (list, tuple)):
                            if len(e) >= 2 and isinstance(e[1], str):
                                self._flat_toc.append((str(e[0]), e[1]))
                            if len(e) >= 3:
                                walk_toc(e[2])
                        else:
                            # section-like: (epub.Section, children)
                            try:
                                # some nodes are (Section, [children])
                                if hasattr(e, 'title') and hasattr(e, 'href'):
                                    self._flat_toc.append((getattr(e, 'title', str(e)), getattr(e, 'href', '')))
                                else:
                                    walk_toc(e)
                            except Exception:
                                pass
                walk_toc(toc)
                # deduplicate preserving order
                seen = set()
                dedup = []
                for t, h in self._flat_toc:
                    if h and h not in seen:
                        dedup.append((t, h))
                        seen.add(h)
                self._flat_toc = dedup
                self.toc_btn.set_sensitive(bool(self._flat_toc))
            else:
                self.toc_btn.set_sensitive(False)

        except Exception as e:
            print("EPUB load error:", e)
            shutil.rmtree(tempdir, ignore_errors=True)

    def on_toc_clicked(self, btn):
        if not self._flat_toc:
            return
        dlg = Gtk.Window(title="Table of Contents", transient_for=self, modal=True)
        dlg.set_default_size(420, 520)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        v.set_margin_top(6); v.set_margin_bottom(6); v.set_margin_start(6); v.set_margin_end(6)
        sc = Gtk.ScrolledWindow()
        v.append(sc)
        listbox = Gtk.ListBox()
        sc.set_child(listbox)

        def on_row_activated(_row, href):
            parsed = urllib.parse.urlparse(href)
            frag = ('#' + parsed.fragment) if parsed.fragment else ''
            href_path = parsed.path or ''
            candidate = os.path.normpath(os.path.join(self._epub_tempdir, href_path))
            if os.path.exists(candidate):
                uri = "file://" + candidate.replace(os.sep, '/')
                uri += frag
            else:
                uri = urllib.parse.urljoin(self._base_href, href)
            self.webview.load_uri(uri)
            dlg.close()

        for title, href in self._flat_toc:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hb.set_margin_top(6); hb.set_margin_bottom(6); hb.set_margin_start(6); hb.set_margin_end(6)
            lbl = Gtk.Label(xalign=0, label=title)
            hb.append(lbl)
            row.set_child(hb)
            listbox.append(row)
            row.connect("activate", lambda r, h=href: on_row_activated(r, h))

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_margin_top(6); btn_box.set_margin_bottom(6); btn_box.set_margin_start(6); btn_box.set_margin_end(6)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: dlg.close())
        btn_box.append(close_btn)
        v.append(btn_box)

        dlg.set_child(v)
        dlg.present()

    def on_font_family_changed(self, dropdown, *args):
        item = dropdown.get_selected_item()
        if not item:
            return
        font = item.get_string().replace("'", "\\'")
        css = f"* {{ font-family: '{font}' !important; }}"
        script = f"""
        (function() {{
            let s = document.getElementById('__font_override');
            if (!s) {{
                s = document.createElement('style');
                s.id = '__font_override';
                (document.head || document.documentElement).appendChild(s);
            }}
            s.textContent = {json.dumps(css)};
        }})();
        """
        try:
            self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception:
            pass

    def _cleanup_epub_tempdir(self):
        if self._epub_tempdir and os.path.exists(self._epub_tempdir):
            try:
                shutil.rmtree(self._epub_tempdir)
            except Exception:
                pass
        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self.toc_btn.set_sensitive(False)
        self._flat_toc = []

    def on_close_request(self, *args):
        self._cleanup_epub_tempdir()
        return False

if __name__ == "__main__":
    app = Writer()
    app.run()

