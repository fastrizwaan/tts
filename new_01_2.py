#!/usr/bin/env python3
# epubviewer_foliate_flat_toc.py
import os, sys, re, html
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, Gio, GLib, WebKit, Pango, Gdk
from ebooklib import epub

Adw.init()

_FOLIATE_CSS = b"""
.sidebar-toc { background-color: @surface; padding-top: 6px; padding-bottom: 6px; }

/* header label */
.toc-contents-label { padding-left: 12px; padding-right: 12px; padding-bottom: 6px; font-weight: 600; }

/* expander rows */
.toc-expander-row {
  min-height: 30px;
  padding-top: 4px;
  padding-bottom: 4px;
  padding-left: 10px;
  border-radius: 10px;
}

/* leaf rows */
.toc-leaf {
  min-height: 30px;
  border-radius: 8px;
  margin-right: 4px;
  padding-left: 20px;
  padding-top: 4px;
  padding-bottom: 4px;
}

/* chevron spacing */
.toc-chev { margin-left: 2px; margin-right: 8px; }

/* hover/active */
.adw-action-row:hover { background-color: rgba(0,0,0,0.03); }
.toc-active { background-color: rgba(20, 80, 160, 0.08); border-radius: 6px; }
"""

class EPubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Epub Viewer")
        self.set_default_size(1100, 720)

        provider = Gtk.CssProvider()
        provider.load_from_data(_FOLIATE_CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.book = None
        self.book_path = None
        self._user_hid_sidebar = False
        self._responsive_enabled = False
        self._row_map = {}
        self._active_href = None

        self.split = Adw.OverlaySplitView(show_sidebar=False)
        self.set_content(self.split)

        self._toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._toc_box.add_css_class("sidebar-toc")
        self._toc_box.set_margin_top(6)
        self._toc_box.set_margin_bottom(6)
        self._toc_box.set_margin_start(6)
        self._toc_box.set_margin_end(6)

        self._toc_scroller = Gtk.ScrolledWindow()
        self._toc_scroller.set_min_content_width(320)
        self._toc_scroller.set_child(self._toc_box)
        self.split.set_sidebar(self._toc_scroller)

        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Gtk.Label(label="Epub Viewer"))
        self.toolbar.add_top_bar(self.header)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_placeholder = Gtk.Label(label="Library (no book loaded)")
        self.content_box.append(self.content_placeholder)
        self.toolbar.set_content(self.content_box)
        self.split.set_content(self.toolbar)

        self.connect("notify::default-width", self._on_window_width_changed)
        self.connect("notify::default-width", self._on_window_size_changed)

        self._build_header_actions()
        self.set_library_mode()

    # --- helpers ---
    def _clear_container(self, container):
        child = container.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            container.remove(child)
            child = next_child

    def _set_active(self, href):
        if self._active_href == href:
            return
        prev = self._row_map.get(self._active_href)
        if prev:
            prev.remove_css_class("toc-active")
        w = self._row_map.get(href)
        if w:
            w.add_css_class("toc-active")
            self._toc_scroller.scroll_to_child(w, 0.0, True, 0.0, 0.0)
            self._active_href = href

    # --- mode switching ---
    def set_library_mode(self):
        self.book = None
        self.book_path = None
        self._disable_responsive_sidebar()
        self.split.set_show_sidebar(False)
        self.split.set_collapsed(True)
        self._clear_container(self.content_box)
        self.content_box.append(Gtk.Label(label="Library — open an EPUB to start reading"))

    def set_reading_mode(self, epub_path):
        self._enable_responsive_sidebar()
        self.load_book(epub_path)

    # --- book loading ---
    def _parse_nav_toc_from_string(self, html_text):
        safe = re.sub(r'&(?!#?\w+;)', '&amp;', html_text)
        m = re.search(r'(<nav\b[^>]*>.*?</nav>)', safe, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        nav_html = m.group(1)
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(f"<root>{nav_html}</root>")
        except Exception:
            return None

        def strip_ns(tag): return tag.split("}")[-1].lower() if isinstance(tag, str) else ""

        list_elem = next((el for el in root.iter() if strip_ns(el.tag) in ("ol", "ul")), None)
        if list_elem is None:
            return None

        def parse_list(el):
            nodes = []
            for li in el:
                if strip_ns(li.tag) != "li": continue
                a = next((c for c in li if strip_ns(c.tag) == "a"), None)
                title = "".join(a.itertext()).strip() if a is not None else "".join(li.itertext()).strip()
                href = a.attrib.get("href") if a is not None else None
                sub = next((c for c in li if strip_ns(c.tag) in ("ol", "ul")), None)
                children = parse_list(sub) if sub is not None else []
                nodes.append({"title": title or None, "href": href, "children": children})
            return nodes

        return parse_list(list_elem) or None

    def load_book(self, path):
        try:
            self.book = epub.read_epub(path)
            self.book_path = path
        except Exception as e:
            self._show_error(f"Failed to read EPUB: {e}")
            return

        toc_nodes = None
        for item in self.book.get_items():
            try:
                s = item.get_content().decode("utf-8", errors="ignore")
                if "<nav" in s.lower():
                    toc_nodes = self._parse_nav_toc_from_string(s)
                    if toc_nodes:
                        break
            except Exception:
                continue

        if not toc_nodes:
            docs = [i for i in self.book.get_items() if i.get_type() == epub.ITEM_DOCUMENT]
            toc_nodes = []
            for i, item in enumerate(docs):
                href = getattr(item, "href", None) or f"doc-{i}"
                html_text = item.get_content().decode("utf-8", errors="ignore")
                title = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                title = title.group(1).strip() if title else href.split("/")[-1]
                toc_nodes.append({"href": href, "title": title, "children": []})

        self._populate_reader_ui(toc_nodes)

    def _populate_reader_ui(self, toc_nodes):
        self._clear_container(self.content_box)
        self.webview = WebKit.WebView()
        self.content_box.append(self.webview)

        # Load first spine doc
        if self.book:
            docs = [i for i in self.book.get_items() if i.get_type() == epub.ITEM_DOCUMENT]
            if docs:
                self.webview.load_html(docs[0].get_content().decode("utf-8"), "file://")

        # Build TOC
        self._clear_container(self._toc_box)
        self._row_map.clear()
        self._active_href = None

        hdr = Gtk.Label(label="Contents", xalign=0)
        hdr.add_css_class("toc-contents-label")
        self._toc_box.append(hdr)
        self._build_flat_toc(self._toc_box, toc_nodes)
        self.split.set_show_sidebar(True)
        self.split.set_collapsed(False)

    # --- toc building (flat, aligned) ---
    def _build_flat_toc(self, parent_box, nodes, level=0):
        for node in nodes:
            title = GLib.markup_escape_text(node.get("title") or node.get("href") or "Untitled")
            href = node.get("href")
            children = node.get("children") or []

            if children:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                header_row = Adw.ActionRow(activatable=True)
                header_row.add_css_class("toc-expander-row")

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
                chev.set_pixel_size(14)
                chev.add_css_class("toc-chev")

                lbl = Gtk.Label(label=title, xalign=0)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)

                header_box.append(chev)
                header_box.append(lbl)
                header_row.set_child(header_box)

                revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
                revealer.set_reveal_child(False)

                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                self._build_flat_toc(child_container, children, level + 1)
                revealer.set_child(child_container)

                def toggle_and_nav():
                    new_state = not revealer.get_reveal_child()
                    revealer.set_reveal_child(new_state)
                    chev.set_from_icon_name("go-down-symbolic" if new_state else "go-next-symbolic")
                    if href:
                        self._on_toc_clicked(None, href)
                        self._set_active(href)

                header_row.connect("activated", lambda *_: toggle_and_nav())
                outer.append(header_row)
                outer.append(revealer)
                parent_box.append(outer)
                if href:
                    self._row_map[href] = header_row

            else:
                row = Adw.ActionRow(activatable=True)
                lbl = Gtk.Label(label=title, xalign=0)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                row.set_child(lbl)
                row.add_css_class("toc-leaf")
                row.connect("activated", lambda w, h=href: (self._on_toc_clicked(w, h), self._set_active(h)))
                parent_box.append(row)
                if href:
                    self._row_map[href] = row

    # --- actions ---
    def _on_toc_clicked(self, widget, href):
        if not self.book or not href:
            return
        target = href.split("#")[0].lstrip("/")
        for item in self.book.get_items():
            ihref = getattr(item, "href", None)
            if ihref and (ihref.endswith(target) or ihref.split("/")[-1] == target):
                html_text = item.get_content().decode("utf-8")
                self.webview.load_html(html_text, "file://")
                return
        self._show_error("TOC target not found in book.")

    def _build_header_actions(self):
        open_btn = Gtk.Button.new_with_label("Open EPUB")
        open_btn.connect("clicked", self._on_open_clicked)
        self.header.pack_start(open_btn)

        close_btn = Gtk.Button.new_with_label("Close Book")
        close_btn.connect("clicked", lambda *_: self.set_library_mode())
        self.header.pack_end(close_btn)

        toggle_btn = Gtk.Button.new_with_label("Toggle Sidebar")
        toggle_btn.connect("clicked", lambda *_: self._on_sidebar_toggle())
        self.header.pack_end(toggle_btn)

    def _on_open_clicked(self, *_):
        dialog = Gtk.FileDialog(title="Open EPUB")
        f = Gtk.FileFilter()
        f.set_name("EPUB")
        f.add_pattern("*.epub")
        dialog.set_default_filter(f)

        def on_done(dlg, res, *_):
            try:
                file = dlg.open_finish(res)
                if file:
                    path = file.get_path()
                    if path:
                        self.set_reading_mode(path)
            except Exception as e:
                self._show_error(str(e))

        dialog.open(self, None, on_done)

    def _show_error(self, text):
        dlg = Gtk.AlertDialog(title="Error", body=text)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        dlg.present(self)

    def _on_sidebar_toggle(self):
        new = not self.split.get_show_sidebar()
        self.split.set_show_sidebar(new)
        self._user_hid_sidebar = not new

    def _on_window_width_changed(self, *args): pass

    def _on_window_size_changed(self, *args):
        if self._user_hid_sidebar:
            return
        width = self.get_width()
        if self._responsive_enabled and self.book:
            self.split.set_collapsed(width < 768)
            self.split.set_show_sidebar(width >= 768)
        else:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)

    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._user_hid_sidebar = False
        self._on_window_size_changed()

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._user_hid_sidebar = False
        self.split.set_collapsed(False)
        self.split.set_show_sidebar(False)


class EPubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        if not self.props.active_window:
            self.win = EPubViewerWindow(self)
        self.win.present()


def main(argv):
    app = EPubViewerApp()
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

