#!/usr/bin/env python3
# epubviewer_foliate_expander_style_ellipsize_aligned.py
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

/* expander header style: slightly reduced padding + rounded */
.toc-expander-row {
  min-height: 30px;
  padding-top: 4px;
  padding-left: 10px;
  padding-bottom: 4px;
  border-radius: 10px;
}

/* leaf rows slightly shorter and tighter */
.toc-leaf {
  min-height: 30px;
  border-radius: 8px;
  margin-right: 4px;
  padding-left: 20px;
  margin-left: 0px;
  padding-top: 4px;
  padding-bottom: 4px;
}

/* chevron spacing */
.toc-chev { margin-left: 2px; margin-right: 8px; }

/* hover and active */
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
        try:
            self._toc_box.add_css_class("sidebar-toc")
        except Exception:
            pass
        self._toc_box.set_margin_top(6); self._toc_box.set_margin_bottom(6)
        self._toc_box.set_margin_start(6); self._toc_box.set_margin_end(6)

        self._toc_scroller = Gtk.ScrolledWindow()
        try:
            self._toc_scroller.set_min_content_width(320)
        except Exception:
            pass
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

    def _clear_container(self, container):
        child = container.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            try:
                container.remove(child)
            except Exception:
                pass
            child = next_child

    def _set_active(self, href):
        if self._active_href == href:
            return
        prev = self._row_map.get(self._active_href)
        if prev:
            try:
                prev.remove_css_class("toc-active")
            except Exception:
                pass
        w = self._row_map.get(href)
        if w:
            try:
                w.add_css_class("toc-active")
            except Exception:
                pass
            try:
                self._toc_scroller.scroll_to_child(w, 0.0, True, 0.0, 0.0)
            except Exception:
                pass
            self._active_href = href

    def set_library_mode(self):
        self.book = None
        self.book_path = None
        self._disable_responsive_sidebar()
        try:
            self.split.set_show_sidebar(False)
            self.split.set_collapsed(True)
        except Exception:
            pass
        self._clear_container(self.content_box)
        self.content_placeholder = Gtk.Label(label="Library — open an EPUB to start reading")
        self.content_box.append(self.content_placeholder)

    def set_reading_mode(self, epub_path):
        self._enable_responsive_sidebar()
        self.load_book(epub_path)

    def _parse_nav_toc_from_string(self, html_text):
        safe = re.sub(r'&(?!#?\w+;)', '&amp;', html_text)
        m = re.search(r'(<nav\b[^>]*>.*?</nav>)', safe, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        nav_html = m.group(1)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(f"<root>{nav_html}</root>")
        except Exception:
            try:
                root = ET.fromstring(nav_html)
            except Exception:
                return None

        def strip_ns(tag):
            return tag.split("}")[-1].lower() if isinstance(tag, str) else ""

        list_elem = None
        for el in root.iter():
            if strip_ns(el.tag) in ("ol", "ul"):
                list_elem = el
                break
        if list_elem is None:
            return None

        def parse_list(el):
            nodes = []
            for li in el:
                if strip_ns(li.tag) != "li":
                    continue
                a = None
                for child in li:
                    if strip_ns(child.tag) == "a":
                        a = child
                        break
                title = ""
                href = None
                if a is not None:
                    title = "".join(a.itertext()).strip()
                    href = a.attrib.get("href")
                else:
                    title = "".join(li.itertext()).strip()
                sub = None
                for child in li:
                    if strip_ns(child.tag) in ("ol", "ul"):
                        sub = child
                        break
                children = parse_list(sub) if sub is not None else []
                nodes.append({"title": title or None, "href": href, "children": children})
            return nodes

        toc = parse_list(list_elem)
        return toc if toc else None

    def load_book(self, path):
        try:
            book = epub.read_epub(path)
            self.book = book
            self.book_path = path
        except Exception as e:
            self._show_error(f"Failed to read EPUB: {e}")
            return

        toc_nodes = None

        try:
            for item in self.book.get_items():
                try:
                    raw = item.get_content()
                    if not raw:
                        continue
                    s = raw.decode("utf-8", errors="ignore")
                    if "<nav" in s.lower():
                        toc_nodes = self._parse_nav_toc_from_string(s)
                        if toc_nodes:
                            break
                except Exception:
                    continue
        except Exception:
            toc_nodes = None

        if not toc_nodes:
            raw = getattr(self.book, "toc", None)
            if hasattr(self.book, "get_toc") and (not raw):
                try:
                    raw = self.book.get_toc()
                except Exception:
                    raw = raw
            if raw:
                def recurse_item(it):
                    node = {"href": None, "title": None, "children": []}
                    if isinstance(it, (list, tuple)):
                        if len(it) > 1 and isinstance(it[-1], (list, tuple)):
                            first = it[0]
                            node["href"] = getattr(first, "href", None) or getattr(first, "src", None)
                            node["title"] = getattr(first, "title", None) or getattr(first, "text", None) or (str(first) if first is not None else None)
                            for sub in it[-1]:
                                node["children"].append(recurse_item(sub))
                            return node
                        else:
                            for el in it:
                                if getattr(el, "href", None) and not node["href"]:
                                    node["href"] = getattr(el, "href", None)
                                if (getattr(el, "title", None) or getattr(el, "text", None)) and not node["title"]:
                                    node["title"] = getattr(el, "title", None) or getattr(el, "text", None)
                            return node
                    if isinstance(it, dict):
                        node["href"] = it.get("href") or it.get("src")
                        node["title"] = it.get("title") or it.get("text") or it.get("name")
                        for c in it.get("children", []) or it.get("subitems", []):
                            node["children"].append(recurse_item(c))
                        return node
                    node["href"] = getattr(it, "href", None) or getattr(it, "src", None)
                    node["title"] = getattr(it, "title", None) or getattr(it, "text", None) or (str(it) if it is not None else None)
                    children = getattr(it, "children", None) or getattr(it, "subitems", None) or []
                    if children and isinstance(children, (list, tuple)):
                        for c in children:
                            node["children"].append(recurse_item(c))
                    return node
                try:
                    toc_nodes = []
                    for it in raw:
                        toc_nodes.append(recurse_item(it))
                except Exception:
                    toc_nodes = None

        if not toc_nodes:
            toc_nodes = []
            try:
                docs = [i for i in self.book.get_items() if i.get_type() == epub.ITEM_DOCUMENT]
                for i, item in enumerate(docs):
                    href = getattr(item, "href", None) or getattr(item, "id", None) or f"doc-{i}"
                    title = None
                    try:
                        html_text = item.get_content().decode("utf-8", errors="ignore")
                        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                        if m:
                            title = m.group(1).strip()
                    except Exception:
                        title = None
                    if not title:
                        title = href.split("/")[-1]
                    toc_nodes.append({"href": href, "title": title, "children": []})
            except Exception:
                toc_nodes = []

        try:
            print("[DEBUG] nav found:", bool(toc_nodes and any(n.get("children") for n in toc_nodes)))
        except Exception:
            pass

        self._populate_reader_ui(toc_nodes)

    def _populate_reader_ui(self, toc_nodes):
        self._clear_container(self.content_box)
        self.webview = WebKit.WebView()
        self.content_box.append(self.webview)

        loaded = False
        try:
            if self.book:
                spine = [i for i in self.book.get_items() if i.get_type() == epub.ITEM_DOCUMENT]
                if spine:
                    self.webview.load_html(spine[0].get_content().decode("utf-8"), "file://")
                    loaded = True
        except Exception:
            loaded = False

        if not loaded:
            self.content_box.append(Gtk.Label(label="(Could not render preview)"))

        self._clear_container(self._toc_box)
        self._row_map.clear()
        self._active_href = None

        if not toc_nodes:
            self._toc_box.append(Gtk.Label(label="NO TOC"))
        else:
            hdr = Gtk.Label(label="Contents", xalign=0)
            try:
                hdr.add_css_class("toc-contents-label")
            except Exception:
                pass
            self._toc_box.append(hdr)
            self._build_foliate_toc(self._toc_box, toc_nodes)

        try:
            self.split.set_show_sidebar(True)
            self.split.set_collapsed(False)
        except Exception:
            pass

    def _build_foliate_toc(self, parent_box, nodes, level=0):
        import html as _html
        for node in nodes:
            raw_title = node.get("title") or node.get("href") or "Untitled"
            title = raw_title if not isinstance(raw_title, str) else _html.unescape(raw_title)
            safe_title = GLib.markup_escape_text(title)
            href = node.get("href")
            children = node.get("children") or []

            indent_px = 8 + (level * 10)

            if children:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

                header_row = Adw.ActionRow()
                header_row.set_activatable(True)
                header_row.set_focusable(True)
                try:
                    header_row.add_css_class("toc-expander-row")
                except Exception:
                    pass

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                header_box.set_margin_start(0)
                try:
                    header_box.set_hexpand(True)
                except Exception:
                    pass

                chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
                try:
                    chev.set_pixel_size(14)
                except Exception:
                    pass
                try:
                    chev.add_css_class("toc-chev")
                except Exception:
                    pass

                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                try:
                    lbl.set_max_width_chars(40)
                except Exception:
                    pass

                header_box.append(chev)
                header_box.append(lbl)

                try:
                    header_row.set_child(header_box)
                except Exception:
                    try:
                        header_row.set_title(safe_title)
                    except Exception:
                        pass

                revealer = Gtk.Revealer()
                try:
                    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                except Exception:
                    pass
                revealer.set_reveal_child(False)

                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                # slightly smaller child indent
                child_container.set_margin_start(0 + 8)
                self._build_foliate_toc(child_container, children, level=level+1)
                revealer.set_child(child_container)

                def _make_toggle(href_local, revealer_local, chev_local):
                    def _toggle_and_nav():
                        try:
                            new_state = not revealer_local.get_reveal_child()
                            revealer_local.set_reveal_child(new_state)
                            chev_local.set_from_icon_name("go-down-symbolic" if new_state else "go-next-symbolic")
                            if href_local:
                                self._on_toc_clicked(None, href_local)
                                self._set_active(href_local)
                        except Exception:
                            pass
                    return _toggle_and_nav

                toggle_fn = _make_toggle(href, revealer, chev)

                try:
                    header_row.connect("activated", lambda w, fn=toggle_fn: fn())
                except Exception:
                    pass

                try:
                    gesture = Gtk.GestureClick.new()
                    gesture.connect("pressed", lambda g, n_press, x, y, fn=toggle_fn: fn())
                    header_box.add_controller(gesture)
                except Exception:
                    pass

                outer.append(header_row)
                outer.append(revealer)
                parent_box.append(outer)

                if href:
                    self._row_map[href] = header_row
            else:
                row = Adw.ActionRow()

                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                try:
                    lbl.set_max_width_chars(40)
                except Exception:
                    pass

                try:
                    row.set_child(lbl)
                except Exception:
                    try:
                        row.set_title(safe_title)
                    except Exception:
                        pass

                row.set_activatable(True)
                row.connect("activated", lambda w, h=href: (self._on_toc_clicked(w, h), self._set_active(h)))

                if level:
                    cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    cont.set_margin_start(0 + 0)
                    cont.append(row)
                    parent_box.append(cont)
                else:
                    # align top-level single items with expander labels (chev width + spacing ≈ 22)
                    try:
                        row.set_margin_start(0 + 0)
                    except Exception:
                        pass
                    parent_box.append(row)

                if href:
                    try:
                        row.add_css_class("toc-leaf")
                    except Exception:
                        pass
                    self._row_map[href] = row

    def _on_toc_clicked(self, widget, href):
        if not self.book or not href:
            return
        target = href.split("#")[0].lstrip("/")
        for item in self.book.get_items():
            ihref = getattr(item, "href", None)
            if not ihref:
                continue
            if ihref.endswith(target) or ihref.split("/")[-1] == target:
                try:
                    html_text = item.get_content().decode("utf-8")
                    self.webview.load_html(html_text, "file://")
                except Exception as e:
                    self._show_error(f"Cannot load fragment: {e}")
                return
        self._show_error("TOC target not found in book.")

    def _build_header_actions(self):
        load_btn = Gtk.Button.new_with_label("Open EPUB")
        load_btn.connect("clicked", self._on_open_clicked)
        self.header.pack_start(load_btn)

        close_btn = Gtk.Button.new_with_label("Close Book")
        close_btn.connect("clicked", lambda *_: self.set_library_mode())
        self.header.pack_end(close_btn)

        toggle_btn = Gtk.Button.new_with_label("Toggle Sidebar")
        toggle_btn.connect("clicked", lambda *_: self._on_sidebar_toggle(toggle_btn))
        self.header.pack_end(toggle_btn)

    def _on_open_clicked(self, *_):
        dialog = Gtk.FileDialog(title="Open EPUB")
        filter_epub = Gtk.FileFilter()
        filter_epub.set_name("EPUB"); filter_epub.add_pattern("*.epub")
        dialog.set_default_filter(filter_epub)
        def on_file_chosen(dlg, res, *a):
            try:
                file = dlg.open_finish(res)
                if file:
                    path = file.get_path()
                    if path:
                        self.set_reading_mode(path)
            except Exception as e:
                self._show_error(f"Failed to open file: {e}")
        dialog.open(self, None, on_file_chosen)

    def _show_error(self, text):
        try:
            dlg = Gtk.Dialog(title="Error", transient_for=self, modal=True)
            dlg.add_button("OK", Gtk.ResponseType.OK)
            content = dlg.get_content_area()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True, vexpand=True)
            box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12)
            label = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.LEFT)
            box.append(label)
            content.append(box)
            dlg.present()
        except Exception:
            pass

    def _on_sidebar_toggle(self, btn):
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            self._user_hid_sidebar = not new
        except Exception:
            pass

    def _on_window_width_changed(self, *args):
        pass

    def _on_window_size_changed(self, *args):
        try:
            if self._user_hid_sidebar:
                return
            width = self.get_width()
            is_narrow = width < 768
            if self._responsive_enabled and self.book and self.book_path:
                if is_narrow:
                    self.split.set_collapsed(True)
                else:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(True)
            else:
                if is_narrow is not None:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(False)
        except Exception:
            pass

    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._user_hid_sidebar = False
        self._on_window_size_changed()

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._user_hid_sidebar = False
        try:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)
        except Exception:
            pass

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

