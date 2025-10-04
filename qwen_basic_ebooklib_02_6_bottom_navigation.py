#!/usr/bin/env python3
"""
EPUB viewer — corrected sidebar: hierarchical TOC (nav.xhtml / toc.ncx),
ellipsized titles, navigation-sidebar style, scrolled, expander-click navigates.
Keeps safe NCX monkey-patch, resource fixing, CSS extraction, prev/next nav.
"""
import gi, os, tempfile, traceback, shutil, urllib.parse
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

# --- Safe NCX monkey-patch ---
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

        # main layout
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_vbox)

        # split with 20% sidebar
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.2)
        main_vbox.append(self.split)

        # --- Sidebar ---
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_header = Adw.HeaderBar()
        sh_label = Gtk.Label(label=APP_NAME)
        sh_label.set_ellipsize(Pango.EllipsizeMode.END)
        sh_label.set_max_width_chars(24)
        sidebar_header.set_title_widget(sh_label)
        sidebar_box.append(sidebar_header)

        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open EPUB")
        open_btn.connect("clicked", self.open_file)
        sidebar_header.pack_start(open_btn)

        # TreeStore: title, href, item_index
        self.treestore = Gtk.TreeStore(str, str, int)
        self.treestore_view = Gtk.TreeView(model=self.treestore)
        self.treestore_view.add_css_class("navigation-sidebar")

        renderer = Gtk.CellRendererText()
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        renderer.set_property("xalign", 0.0)
        renderer.set_property("max-width-chars", 36)
        column = Gtk.TreeViewColumn("TOC", renderer, text=0)
        self.treestore_view.append_column(column)

        # selection (TreeView API)
        sel = self.treestore_view.get_selection()
        sel.set_mode(Gtk.SelectionMode.SINGLE)
        sel.connect("changed", self.on_tree_selection_changed)

        # gesture for expander-area clicks -> navigate
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self.on_tree_gesture_pressed)
        self.treestore_view.add_controller(gesture)

        toc_scrolled = Gtk.ScrolledWindow()
        toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toc_scrolled.set_vexpand(True)
        toc_scrolled.set_child(self.treestore_view)
        sidebar_box.append(toc_scrolled)

        self.split.set_sidebar(sidebar_box)

        # --- Content area ---
        self.toolbar = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar()
        self.content_title_label = Gtk.Label(label=APP_NAME)
        self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)
        self.toolbar.add_top_bar(self.content_header)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.toolbar.set_content(self.scrolled)
        self.split.set_content(self.toolbar)

        # WebKit or fallback
        try:
            gi.require_version("WebKit", "6.0")
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

        # bottom navigation
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6); bottom_bar.set_margin_bottom(6)
        bottom_bar.set_margin_start(6); bottom_bar.set_margin_end(6)
        main_vbox.append(bottom_bar)

        self.prev_btn = Gtk.Button(label="Previous")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)
        bottom_bar.append(self.prev_btn)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_hexpand(True)
        bottom_bar.append(self.progress)

        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)
        bottom_bar.append(self.next_btn)

    # ---------- File open ----------
    def open_file(self, *_):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        epub_filter = Gtk.FileFilter(); epub_filter.add_pattern("*.epub"); epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)
        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                self.load_epub(f.get_path())
        except GLib.Error:
            pass

    # ---------- Load EPUB ----------
    def load_epub(self, path):
        try:
            self.cleanup()
            self.book = epub.read_epub(path)

            # build docs and order by spine if possible
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
                try:
                    iid = getattr(it, "id", None) or (it.get_id() if hasattr(it, "get_id") else None)
                except Exception:
                    iid = None
                if not iid:
                    iid = it.get_name() or os.urandom(8).hex()
                id_map[iid] = it
            ordered = []
            try:
                spine = getattr(self.book, "spine", None) or []
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

            # extract files to temp_dir
            self.temp_dir = tempfile.mkdtemp()
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue
                full = os.path.join(self.temp_dir, item_path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as fh:
                    fh.write(item.get_content())

            # item_map by name
            self.item_map = {it.get_name(): it for it in self.items}

            # css
            self.extract_css()

            # title
            title = APP_NAME
            try:
                meta = self.book.get_metadata("DC", "title")
                if meta and meta[0]:
                    title = meta[0][0]
            except Exception:
                pass
            self.content_title_label.set_text(title)
            self.set_title(title or APP_NAME)

            # populate hierarchical TOC
            self._populate_toc_tree()

            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception:
            print(traceback.format_exc())
            self.show_error("Error loading EPUB — see console")

    # ---------- Populate hierarchical TOC ----------
    def _populate_toc_tree(self):
        self.treestore.clear()

        def href_to_index(href):
            if not href:
                return -1
            h = href.split("#")[0]
            # try exact match, basename, url-unquote variants
            candidates = [h, os.path.basename(h)]
            try:
                uq = urllib.parse.unquote(h)
                if uq != h:
                    candidates.append(uq); candidates.append(os.path.basename(uq))
            except Exception:
                pass
            for c in candidates:
                for i, it in enumerate(self.items):
                    if it.get_name() == c or it.get_name().endswith(c):
                        return i
            return -1

        # 1) nav.xhtml (EPUB3)
        try:
            nav_item = self.book.get_item_with_id("nav")
            if nav_item:
                soup = BeautifulSoup(nav_item.get_content(), "html.parser")
                toc_nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", {"role": "doc-toc"})
                if toc_nav:
                    def walk_list(ol, parent_iter=None):
                        for li in ol.find_all("li", recursive=False):
                            a = li.find("a", href=True)
                            title = a.get_text(strip=True) if a else li.get_text(strip=True)
                            href = a["href"].split("#")[0] if a else ""
                            idx = href_to_index(href)
                            iter_ = self.treestore.append(parent_iter, [title, href or "", idx])
                            child_ol = li.find("ol", recursive=False)
                            if child_ol:
                                walk_list(child_ol, iter_)
                    ol = toc_nav.find("ol")
                    if ol:
                        walk_list(ol, None)
                        return
        except Exception:
            pass

        # 2) toc.ncx (EPUB2)
        try:
            ncx_item = self.book.get_item_with_id("ncx")
            if ncx_item:
                soup = BeautifulSoup(ncx_item.get_content(), "xml")
                def walk_navpoints(parent, parent_iter=None):
                    for np in parent.find_all("navPoint", recursive=False):
                        text_tag = np.find("text")
                        content_tag = np.find("content")
                        title = text_tag.get_text(strip=True) if text_tag else ""
                        href = content_tag["src"].split("#")[0] if content_tag and content_tag.has_attr("src") else ""
                        idx = href_to_index(href)
                        iter_ = self.treestore.append(parent_iter, [title or os.path.basename(href), href or "", idx])
                        walk_navpoints(np, iter_)
                navmap = soup.find("navMap")
                if navmap:
                    walk_navpoints(navmap, None)
                    return
        except Exception:
            pass

        # 3) fallback: spine/file names
        for i, it in enumerate(self.items):
            title = os.path.basename(it.get_name())
            self.treestore.append(None, [title, it.get_name(), i])

    # ---------- Selection handler ----------
    def on_tree_selection_changed(self, selection):
        model, iter_ = selection.get_selected()
        if iter_:
            idx = model.get_value(iter_, 2)
            if isinstance(idx, int) and idx >= 0:
                self.current_index = idx
                self.update_navigation()
                self.display_page()

    # ---------- Gesture pressed (expander-area navigation) ----------
    def on_tree_gesture_pressed(self, gesture, n_press, x, y):
        info = self.treestore_view.get_path_at_pos(int(x), int(y))
        if not info:
            return
        path, column, cell_x, cell_y = info
        if cell_x is not None and cell_x < 22:
            iter_ = self.treestore.get_iter(path)
            if iter_:
                idx = self.treestore.get_value(iter_, 2)
                if isinstance(idx, int) and idx >= 0:
                    self.current_index = idx
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
        # also try a few common CSS names in extracted dir
        try:
            for fn in ("flow0001.css", "core.css", "se.css", "style.css"):
                p = os.path.join(self.temp_dir or "", fn)
                if p and os.path.exists(p):
                    with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                        self.css_content += fh.read() + "\n"
        except Exception:
            pass

    # ---------- Internal link handling ----------
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
        base = path.split("#", 1)[0]
        if self.temp_dir and base.startswith(self.temp_dir):
            rel = os.path.relpath(base, self.temp_dir).replace(os.sep, "/")
        else:
            rel = base.replace(os.sep, "/")
        candidates = [rel, os.path.basename(rel)]
        try:
            uq = urllib.parse.unquote(rel)
            if uq != rel:
                candidates.append(uq); candidates.append(os.path.basename(uq))
        except Exception:
            pass
        for cand in candidates:
            if cand in self.item_map:
                for i, it in enumerate(self.items):
                    if it.get_name() == cand:
                        self.current_index = i
                        self.update_navigation()
                        self.display_page()
                        return True
        return False

    # ---------- Display ----------
    def display_page(self):
        self.extract_css()
        if not self.items:
            return
        item = self.items[self.current_index]
        soup = BeautifulSoup(item.get_content(), "html.parser")
        content = str(soup)
        html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{self.css_content}</style></head><body>{content}</body></html>"""
        if self.webview:
            base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
            self.webview.load_html(html_content, base_uri)
        else:
            buf = self.textview.get_buffer()
            buf.set_text(soup.get_text())
        total = len(self.items)
        self.progress.set_fraction((self.current_index+1)/total)
        self.progress.set_text(f"{self.current_index+1}/{total}")

    # ---------- Navigation ----------
    def update_navigation(self):
        total = len(self.items) if hasattr(self, "items") and self.items else 0
        self.prev_btn.set_sensitive(getattr(self, "current_index", 0) > 0)
        self.next_btn.set_sensitive(getattr(self, "current_index", 0) < total - 1)

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

    # ---------- Error / Cleanup ----------
    def show_error(self, message):
        try:
            dialog = Adw.MessageDialog.new(self, "Error", message)
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception:
            print("Error dialog:", message)

    def cleanup(self):
        if getattr(self, "temp_dir", None) and os.path.exists(self.temp_dir):
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
        try:
            self.treestore.clear()
        except Exception:
            pass
        self.update_navigation()


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer")
        self.create_action("quit", self.quit, ["<primary>q"])

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

