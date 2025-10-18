#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

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

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.webview = WebKit.WebView()
        scroll.set_child(self.webview)
        self.webview.load_html("<!doctype html><html><body><p></p></body></html>", "file:///")

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

        self.back_btn = Gtk.Button(icon_name="go-previous"); self.back_btn.add_css_class("flat")
        self.back_btn.connect("clicked", lambda *_: self.webview.go_back() if self.webview.can_go_back() else None)
        controls.append(self.back_btn)

        self.forward_btn = Gtk.Button(icon_name="go-next"); self.forward_btn.add_css_class("flat")
        self.forward_btn.connect("clicked", lambda *_: self.webview.go_forward() if self.webview.can_go_forward() else None)
        controls.append(self.forward_btn)

        open_btn = Gtk.Button(icon_name="document-open"); open_btn.add_css_class("flat")
        open_btn.connect("clicked", self.on_open_clicked)
        controls.append(open_btn)

        self.toc_btn = Gtk.Button(icon_name="view-list-symbolic"); self.toc_btn.add_css_class("flat")
        self.toc_btn.set_sensitive(False)
        self.toc_btn.connect("clicked", self.on_toc_clicked)
        controls.append(self.toc_btn)

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
        self.webview.connect("notify::title", self._update_nav_buttons)
        self.webview.connect("notify::uri", self._update_nav_buttons)

    def _update_nav_buttons(self, *a):
        self.back_btn.set_sensitive(self.webview.can_go_back())
        self.forward_btn.set_sensitive(self.webview.can_go_forward())

    def on_open_clicked(self, btn):
        dialog = Gtk.FileDialog()
        filter_all = Gtk.FileFilter(); filter_all.set_name("HTML / EPUB")
        filter_all.add_pattern("*.html"); filter_all.add_pattern("*.htm"); filter_all.add_pattern("*.epub")
        dialog.set_default_filter(filter_all)
        dialog.open(self, None, self.on_open_file_dialog_response)

    def on_open_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file: return
            basename = file.get_basename().lower()
            # reset state before loading new content
            self._cleanup_epub_tempdir()
            self._clear_webview_history()
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
                html = content.decode(errors="replace")
                base = file.get_uri() or "file:///"
                html = inject_css_into_html(html)
                self._epub_tempdir = None
                self._base_href = base
                self._flat_toc = []
                self.toc_btn.set_sensitive(False)
                self.webview.load_html(html, base)
        except GLib.Error as e:
            print("Load error:", e.message)

    def load_epub_with_ebooklib(self, gio_file):
        path = gio_file.get_path()
        if not path:
            try:
                fd, tmp_epub = tempfile.mkstemp(suffix=".epub"); os.close(fd)
                stream = gio_file.read(None)
                with open(tmp_epub, "wb") as f: f.write(stream.read_bytes(stream.get_size()).get_data())
                path = tmp_epub
            except Exception:
                path = None
        if not path:
            print("Cannot access EPUB path"); return

        tempdir = tempfile.mkdtemp(prefix="epub_")
        try:
            book = epub.read_epub(path)
            self._book = book

            for item in book.get_items():
                fn = getattr(item, 'file_name', None)
                if not fn: continue
                full = os.path.join(tempdir, fn)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                content = item.get_content()
                with open(full, "wb") as f:
                    if isinstance(content, str): f.write(content.encode("utf-8"))
                    else: f.write(content)

            spine_hrefs = []
            for idref, _ in getattr(book, 'spine', []):
                try:
                    item = book.get_item_with_id(idref)
                except Exception:
                    item = None
                if item is None: continue
                href = getattr(item, 'file_name', None)
                if href: spine_hrefs.append(href)
            if not spine_hrefs:
                for item in book.get_items_of_type(epub.ITEM_DOCUMENT):
                    if getattr(item, 'file_name', None): spine_hrefs.append(item.file_name)
            if not spine_hrefs:
                print("No document items found in EPUB"); shutil.rmtree(tempdir, ignore_errors=True); return

            bodies = []; head_html = None
            for rel in spine_hrefs:
                full = os.path.join(tempdir, rel)
                if not os.path.exists(full): continue
                with open(full, "r", encoding="utf-8", errors="replace") as f: txt = f.read()
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

            # commit state
            self._epub_tempdir = tempdir
            self._base_href = base_href
            self._flat_toc = []
            self.toc_btn.set_sensitive(False)

            self._clear_webview_history()
            self.webview.load_html(concatenated, base_href)

            # build robust flat TOC with depth
            toc = getattr(book, "toc", []) or []
            def walk_toc(entries, depth=0):
                if not entries: return
                if isinstance(entries, (list, tuple)):
                    for e in entries:
                        if isinstance(e, tuple) and len(e) >= 2 and isinstance(e[1], str):
                            title = str(e[0]); href = e[1]; self._flat_toc.append((title, href, depth))
                            if len(e) >= 3 and e[2]: walk_toc(e[2], depth+1)
                        else:
                            walk_toc(e, depth)
                    return
                if hasattr(entries, "href"):
                    title = getattr(entries, "title", None) or getattr(entries, "label", None) or str(entries)
                    href = getattr(entries, "href", "") or getattr(entries, "src", "")
                    self._flat_toc.append((title, href, depth))
                    children = getattr(entries, "children", None) or getattr(entries, "subitems", None) or None
                    if children: walk_toc(children, depth+1)
                    return
                try:
                    for child in entries: walk_toc(child, depth)
                except Exception: return

            walk_toc(toc)
            seen = set(); dedup = []
            for t, h, d in self._flat_toc:
                if not h: continue
                if h not in seen: dedup.append((t, h, d)); seen.add(h)
            self._flat_toc = dedup
            self.toc_btn.set_sensitive(bool(self._flat_toc))

        except Exception as e:
            print("EPUB load error:", e)
            shutil.rmtree(tempdir, ignore_errors=True)

    def _clear_webview_history(self):
        try:
            bfl = self.webview.get_back_forward_list()
            if bfl and hasattr(bfl, "clear"):
                bfl.clear()
            else:
                self.webview.load_html("<!doctype html><html><body></body></html>", "file:///")
        except Exception:
            try: self.webview.load_html("<!doctype html><html><body></body></html>", "file:///")
            except Exception: pass

    def resolve_href(self, href: str):
        if not href:
            return None
        href = href.strip()
        # already absolute URL
        if href.startswith(("file://", "http://", "https://")):
            return href

        p = urllib.parse.urlparse(href)
        frag = ('#' + p.fragment) if p.fragment else ''
        rel_path = p.path or ''

        # if only a fragment, navigate to current base with fragment
        if rel_path == '':
            return self._base_href + frag

        candidates = []
        # absolute fs path
        if os.path.isabs(rel_path):
            candidates.append(rel_path)
        else:
            # prefer tempdir (extracted epub files)
            if self._epub_tempdir:
                candidates.append(os.path.normpath(os.path.join(self._epub_tempdir, rel_path)))
            # fallback: relative to base_href directory
            try:
                base_dir = urllib.parse.urlparse(self._base_href).path
                if base_dir:
                    candidates.append(os.path.normpath(os.path.join(base_dir, rel_path)))
            except Exception:
                pass

        # try several common suffix variants
        suffixes = ['', '.html', '.htm', '.xhtml', '.html.html', '.htm.html']
        for cand in candidates:
            for s in suffixes:
                path_try = cand if cand.endswith(s) else cand + s
                if os.path.exists(path_try):
                    return "file://" + path_try + frag

        # final fallback: join with base_href (may include fragment)
        return urllib.parse.urljoin(self._base_href, href)

    def on_toc_clicked(self, btn):
        if not self._flat_toc: return
        dlg = Gtk.Window(title="Table of Contents", transient_for=self, modal=True)
        dlg.set_default_size(420, 520)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        v.set_margin_top(6); v.set_margin_bottom(6); v.set_margin_start(6); v.set_margin_end(6)
        sc = Gtk.ScrolledWindow(vexpand=True); v.append(sc)

        listbox = Gtk.ListBox(); sc.set_child(listbox)

        def on_row_activated(_row, href):
            uri = self.resolve_href(href)
            if not uri:
                dlg.close()
                return
            dlg.close()
            GLib.idle_add(lambda u=uri: self.webview.load_uri(u))

        for title, href, depth in self._flat_toc:
            row = Gtk.ListBoxRow(); row.set_activatable(True)
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hb.set_margin_top(6); hb.set_margin_bottom(6)
            indent = max(0, depth) * 12
            hb.set_margin_start(6 + indent); hb.set_margin_end(6)
            lbl = Gtk.Label(xalign=0, label=title); hb.append(lbl)
            row.set_child(hb); listbox.append(row)
            row.connect("activate", (lambda h: (lambda r: on_row_activated(r, h)))(href))

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_margin_top(6); btn_box.set_margin_bottom(6); btn_box.set_margin_start(6); btn_box.set_margin_end(6)
        close_btn = Gtk.Button(label="Close"); close_btn.connect("clicked", lambda *_: dlg.close())
        btn_box.append(close_btn); v.append(btn_box)
        dlg.set_child(v); dlg.present()

    def on_font_family_changed(self, dropdown, *args):
        item = dropdown.get_selected_item()
        if not item: return
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
        try: self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception: pass

    def _cleanup_epub_tempdir(self):
        if self._epub_tempdir and os.path.exists(self._epub_tempdir):
            try: shutil.rmtree(self._epub_tempdir)
            except Exception: pass
        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self.toc_btn.set_sensitive(False)
        self._flat_toc = []

    def on_close_request(self, *args):
        self._cleanup_epub_tempdir()
        return False

if __name__ == "__main__":
    app = Writer(); app.run()

