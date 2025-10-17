#!/usr/bin/env python3
# EPUB viewer with libadwaita + GTK4 ListView sidebar TOC (nested, clickable)
import gi, os, tempfile, traceback, shutil, urllib.parse
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GObject, Gdk

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


# --- global CSS for sidebar (semi-transparent) ---
_css = """
.epub-sidebar, .epub-sidebar * {
  background-color: rgba(242,242,242,0.0);
  /* ensure children respect background */
  background-image: none;
}
"""
_css_provider = Gtk.CssProvider()
_css_provider.load_from_data(_css.encode("utf-8"))
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _css_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
)


class TocItem(GObject.Object):
    title = GObject.Property(type=str)
    href = GObject.Property(type=str)
    index = GObject.Property(type=int, default=-1)

    def __init__(self, title, href="", index=-1, children=None):
        super().__init__()
        self.title = title or ""
        self.href = href or ""
        self.index = index if isinstance(index, int) else -1
        self.children = Gio.ListStore(item_type=TocItem)
        if children:
            for c in children:
                self.children.append(c)


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
        self.split.set_sidebar_width_fraction(0.4)
        main_vbox.append(self.split)

        # --- Sidebar ---
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # apply css class so sidebar and children get rgba background
        sidebar_box.add_css_class("epub-sidebar")

        sidebar_header = Adw.HeaderBar()
        # also give header same css class to ensure header gets styled
        sidebar_header.add_css_class("flat")

        sh_label = Gtk.Label(label=APP_NAME)
        sh_label.set_ellipsize(Pango.EllipsizeMode.END)
        sh_label.set_max_width_chars(24)
        sidebar_header.set_title_widget(sh_label)
        sidebar_box.append(sidebar_header)

        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open EPUB")
        open_btn.connect("clicked", self.open_file)
        sidebar_header.pack_start(open_btn)

        # ListView-based hierarchical TOC
        self.toc_factory = Gtk.SignalListItemFactory()
        self.toc_factory.connect("setup", self._toc_on_setup)
        self.toc_factory.connect("bind", self._toc_on_bind)

        # placeholder empty model
        self.toc_root_store = Gio.ListStore(item_type=TocItem)
        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview = Gtk.ListView(model=self.toc_sel, factory=self.toc_factory)
        self.toc_listview.set_vexpand(True)

        toc_scrolled = Gtk.ScrolledWindow()
        toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toc_scrolled.set_vexpand(True)
        toc_scrolled.set_child(self.toc_listview)
        # ensure scrolled child is also styled (class applied to parent covers children; this is defensive)

        sidebar_box.append(toc_scrolled)

        self.split.set_sidebar(sidebar_box)

        # --- Content area ---
        self.toolbar = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar()
        self.content_title_label = Gtk.Label(label=APP_NAME)
        self.content_header.add_css_class("flat")
        self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)
        self.toolbar.add_top_bar(self.content_header)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)

        # bottom navigation (created but NOT appended to main_vbox)
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6); bottom_bar.set_margin_bottom(6)
        bottom_bar.set_margin_start(6); bottom_bar.set_margin_end(6)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("flat")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)
        bottom_bar.append(self.prev_btn)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_hexpand(True)
        bottom_bar.append(self.progress)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("flat")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)
        bottom_bar.append(self.next_btn)

        # Put scrolled + bottom_bar into a vertical container and set as toolbar content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_vexpand(True)
        content_box.append(self.scrolled)
        content_box.append(bottom_bar)
        self.toolbar.set_content(content_box)

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




    # ---------- TOC ListView factory ----------
    def _toc_on_setup(self, factory, list_item):
        # wrapper with an HBox (icon + actionrow) and a nested VBox
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hbox.set_hexpand(True)

        # disclosure icon: toggles expansion only
        disc = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        disc.set_visible(False)
        hbox.append(disc)

        actrow = Adw.ActionRow()
        actrow.set_activatable(True)
        actrow.set_title("")          # populated in bind
        actrow.set_hexpand(True)
        hbox.append(actrow)

        wrapper.append(hbox)

        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nested.set_margin_start(18)
        nested.set_visible(False)
        wrapper.append(nested)

        # icon gesture: ONLY toggle expand/collapse (do NOT navigate)
        def _toggle_only():
            item = list_item.get_item()
            if not item:
                return
            if item.children.get_n_items() > 0:
                visible = not nested.get_visible()
                nested.set_visible(visible)
                disc.set_from_icon_name("pan-down-symbolic" if visible else "pan-end-symbolic")
                nv = getattr(list_item, "_nested_view", None)
                if nv:
                    nv.set_visible(visible)

        g = Gtk.GestureClick()
        g.connect("pressed", lambda *_: _toggle_only())
        disc.add_controller(g)

        # actionrow: ONLY open the href/index (do NOT toggle)
        def _open_only(_):
            item = list_item.get_item()
            if not item:
                return
            href = item.href or ""
            fragment = href.split("#", 1)[1] if "#" in href else None
            if isinstance(item.index, int) and item.index >= 0:
                self.current_index = item.index
                self.update_navigation()
                self.display_page(fragment=fragment)
            elif href:
                try:
                    base = urllib.parse.unquote(href.split("#", 1)[0])
                    candidate = os.path.join(self.temp_dir or "", base)
                    if self.handle_internal_link("file://" + candidate):
                        return
                except Exception:
                    pass

        try:
            actrow.connect("activated", _open_only)
        except Exception:
            pass
        g2 = Gtk.GestureClick()
        g2.connect("pressed", lambda *_: _open_only(None))
        actrow.add_controller(g2)

        list_item.set_child(wrapper)
        list_item._hbox = hbox
        list_item._disc = disc
        list_item._actrow = actrow
        list_item._nested = nested
        list_item._nested_view = None


    def _toc_on_bind(self, factory, list_item):
        item = list_item.get_item()

        # ensure setup widgets exist
        disc = getattr(list_item, "_disc", None)
        actrow = getattr(list_item, "_actrow", None)
        nested = getattr(list_item, "_nested", None)
        if disc is None or actrow is None or nested is None:
            self._toc_on_setup(factory, list_item)
            disc = list_item._disc; actrow = list_item._actrow; nested = list_item._nested

        if not item:
            actrow.set_title("")
            disc.set_visible(False)
            nv = getattr(list_item, "_nested_view", None)
            if nv:
                nv.set_visible(False)
            return

        has_children = item.children.get_n_items() > 0
        actrow.set_title(item.title or "")
        disc.set_visible(has_children)
        if has_children:
            disc.set_from_icon_name("pan-down-symbolic" if nested.get_visible() else "pan-end-symbolic")
        else:
            disc.set_from_icon_name(None)

        # create nested ListView once: children follow same pattern (icon toggles, actionrow opens)
        if has_children and not getattr(list_item, "_nested_view", None):
            def child_setup(f, li):
                cwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                ch_h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                ch_disc = Gtk.Image.new_from_icon_name("pan-end-symbolic")
                ch_disc.set_visible(False)
                ch_h.append(ch_disc)

                ch_act = Adw.ActionRow()
                ch_act.set_activatable(True)
                ch_act.set_title("")  # set in bind
                ch_act.set_hexpand(True)
                ch_h.append(ch_act)

                cwrap.append(ch_h)

                ch_nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                ch_nested.set_margin_start(18)
                ch_nested.set_visible(False)
                cwrap.append(ch_nested)

                # icon toggles grandchildren only (no navigation)
                def _toggle_child():
                    it = li.get_item()
                    if not it:
                        return
                    if it.children.get_n_items() > 0:
                        vis = not ch_nested.get_visible()
                        ch_nested.set_visible(vis)
                        ch_disc.set_from_icon_name("pan-down-symbolic" if vis else "pan-end-symbolic")
                        gv = getattr(li, "_nested_view", None)
                        if gv:
                            gv.set_visible(vis)

                gch = Gtk.GestureClick()
                gch.connect("pressed", lambda *_: _toggle_child())
                ch_disc.add_controller(gch)

                # child actionrow: open child target only
                def _open_child(_):
                    it = li.get_item()
                    if not it:
                        return
                    href = it.href or ""
                    fragment = href.split("#", 1)[1] if "#" in href else None
                    if isinstance(it.index, int) and it.index >= 0:
                        self.current_index = it.index
                        self.update_navigation()
                        self.display_page(fragment=fragment)
                    elif href:
                        try:
                            base = urllib.parse.unquote(href.split("#", 1)[0])
                            candidate = os.path.join(self.temp_dir or "", base)
                            if self.handle_internal_link("file://" + candidate):
                                return
                        except Exception:
                            pass

                try:
                    ch_act.connect("activated", _open_child)
                except Exception:
                    pass
                gch2 = Gtk.GestureClick()
                gch2.connect("pressed", lambda *_: _open_child(None))
                ch_act.add_controller(gch2)

                li.set_child(cwrap)
                li._row = ch_act
                li._disc = ch_disc
                li._nested = ch_nested
                li._nested_view = None

            def child_bind(f, li):
                it = li.get_item()
                if not it:
                    return
                ch_act = getattr(li, "_row", None)
                ch_disc = getattr(li, "_disc", None)
                ch_nested = getattr(li, "_nested", None)
                if ch_act is None or ch_disc is None or ch_nested is None:
                    return
                kids = it.children.get_n_items() > 0
                ch_act.set_title(it.title or "")
                ch_disc.set_visible(kids)
                if kids:
                    ch_disc.set_from_icon_name("pan-down-symbolic" if ch_nested.get_visible() else "pan-end-symbolic")
                else:
                    ch_disc.set_from_icon_name(None)

                # create grandchildren view lazily (recursive)
                if kids and not getattr(li, "_nested_view", None):
                    sub_factory = Gtk.SignalListItemFactory()
                    sub_factory.connect("setup", child_setup)
                    sub_factory.connect("bind", child_bind)
                    sub_sel = Gtk.NoSelection(model=it.children)
                    gv = Gtk.ListView(model=sub_sel, factory=sub_factory)
                    gv.set_vexpand(False)
                    ch_nested.append(gv)
                    li._nested_view = gv
                if getattr(li, "_nested_view", None):
                    li._nested_view.set_visible(ch_nested.get_visible())

            nfactory = Gtk.SignalListItemFactory()
            nfactory.connect("setup", child_setup)
            nfactory.connect("bind", child_bind)

            nsel = Gtk.NoSelection(model=item.children)
            nested_view = Gtk.ListView(model=nsel, factory=nfactory)
            nested_view.set_vexpand(False)
            nested.append(nested_view)
            list_item._nested_view = nested_view

            nested_view.set_visible(nested.get_visible())

        # sync nested visibility on re-bind
        nv = getattr(list_item, "_nested_view", None)
        if nv:
            nv.set_visible(nested.get_visible())





    def _navigate_nested(self, list_item):
        it = list_item.get_item()
        if not it:
            return
        href = it.href or ""
        fragment = None
        if "#" in href:
            fragment = href.split("#", 1)[1]
        if isinstance(it.index, int) and it.index >= 0:
            self.current_index = it.index
            self.update_navigation()
            self.display_page(fragment=fragment)

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

            # extract files to temp_dir with path sanitization
            self.temp_dir = tempfile.mkdtemp()
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue

                # Sanitize the path to prevent directory traversal
                sanitized_path = self.sanitize_path(item_path)
                if sanitized_path is None:
                    print(f"Skipping potentially dangerous path: {item_path}")
                    continue

                full = os.path.join(self.temp_dir, sanitized_path)
                try:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as fh:
                        fh.write(item.get_content())
                except OSError as e:
                    print(f"Failed to extract {item_path}: {e}")
                    continue

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

            # populate hierarchical TOC into ListView model
            self._populate_toc_tree()

            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception:
            print(traceback.format_exc())
            self.show_error("Error loading EPUB — see console")

    def sanitize_path(self, path):
        """
        Sanitize file paths to prevent directory traversal attacks.
        Returns None if the path is dangerous, otherwise returns a safe path.
        """
        if not path:
            return None

        # Normalize the path and remove any '..' components
        normalized = os.path.normpath(path)

        # Check if the normalized path tries to escape the base directory
        if normalized.startswith("..") or os.path.isabs(normalized):
            return None

        # Further check for any remaining '..' components
        if ".." in normalized.split(os.sep):
            return None

        return normalized

    # ---------- Populate hierarchical TOC (build TocItem tree) ----------
    def _populate_toc_tree(self):
        # helper: find index from href
        def href_to_index(href):
            if not href:
                return -1
            h = href.split("#")[0]
            candidates = [h, os.path.basename(h)]
            try:
                uq = urllib.parse.unquote(h)
                if uq != h:
                    candidates.append(uq); candidates.append(os.path.basename(uq))
            except Exception:
                pass
            for i, it in enumerate(self.items):
                if it.get_name() == h or it.get_name().endswith(h) or it.get_name() in candidates:
                    return i
            return -1

        # build a root Gio.ListStore of TocItem
        root = Gio.ListStore(item_type=TocItem)

        def add_node(title, href, parent_store):
            idx = href_to_index(href)
            node = TocItem(title=title or "", href=href or "", index=idx)
            parent_store.append(node)
            return node

        # 1) nav.xhtml (EPUB3)
        try:
            nav_item = self.book.get_item_with_id("nav")
            if nav_item:
                soup = BeautifulSoup(nav_item.get_content(), "html.parser")
                toc_nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", {"role": "doc-toc"})
                if toc_nav:
                    def walk_list(ol, parent_store):
                        for li in ol.find_all("li", recursive=False):
                            a = li.find("a", href=True)
                            title = a.get_text(strip=True) if a else li.get_text(strip=True)
                            href = a["href"] if a else ""
                            node = add_node(title, href, parent_store)
                            child_ol = li.find("ol", recursive=False)
                            if child_ol:
                                walk_list(child_ol, node.children)
                    ol = toc_nav.find("ol")
                    if ol:
                        walk_list(ol, root)
                        # set model
                        self.toc_root_store = root
                        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                        self.toc_listview.set_model(self.toc_sel)
                        return
        except Exception:
            pass

        # 2) toc.ncx (EPUB2)
        try:
            ncx_item = self.book.get_item_with_id("ncx")
            if ncx_item:
                soup = BeautifulSoup(ncx_item.get_content(), "xml")
                def walk_navpoints(parent, parent_store):
                    for np in parent.find_all("navPoint", recursive=False):
                        text_tag = np.find("text")
                        content_tag = np.find("content")
                        title = text_tag.get_text(strip=True) if text_tag else ""
                        href = content_tag["src"] if content_tag and content_tag.has_attr("src") else ""
                        node = add_node(title or os.path.basename(href), href or "", parent_store)
                        walk_navpoints(np, node.children)
                navmap = soup.find("navMap")
                if navmap:
                    walk_navpoints(navmap, root)
                    self.toc_root_store = root
                    self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                    self.toc_listview.set_model(self.toc_sel)
                    return
        except Exception:
            pass

        # 3) fallback: spine/file names
        for i, it in enumerate(self.items):
            title = os.path.basename(it.get_name())
            add_node(title, it.get_name(), root)

        self.toc_root_store = root
        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview.set_model(self.toc_sel)

    # ---------- Gesture and selection removed (ListView handles clicks) ----------
    # ---------- CSS extraction ----------
    def extract_css(self):
        """Extract CSS styles from the current book"""
        self.css_content = ""

        # Check if book exists and is valid
        if not self.book:
            return

        try:
            for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
                try:
                    self.css_content += item.get_content().decode("utf-8") + "\n"
                except Exception:
                    pass

            # Also try a few common CSS names in extracted dir
            if self.temp_dir and os.path.exists(self.temp_dir):
                for fn in ("flow0001.css", "core.css", "se.css", "style.css"):
                    p = os.path.join(self.temp_dir, fn)
                    if os.path.exists(p):
                        try:
                            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                                self.css_content += fh.read() + "\n"
                        except Exception:
                            pass
        except Exception as e:
            print(f"Error extracting CSS: {e}")

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
        # Extract fragment if present
        fragment = None
        if "#" in path:
            path, fragment = path.split("#", 1)

        base = path
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
                        self.display_page(fragment=fragment)
                        return True
        return False

    # ---------- Display ----------
    def display_page(self, fragment=None):
        """Display the current page with optional fragment/anchor"""
        # Safety check
        if not self.book or not self.items or self.current_index >= len(self.items):
            return

        self.extract_css()

        item = self.items[self.current_index]

        # Additional safety check for item content
        if not item or not hasattr(item, 'get_content'):
            return

        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            content = str(soup)
            html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{self.css_content}</style></head><body>{content}</body></html>"""

            if self.webview:
                base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
                # Load with fragment if present
                if fragment:
                    # Use run_javascript to scroll to the anchor after loading
                    self.webview.load_html(html_content, base_uri)
                    GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                else:
                    self.webview.load_html(html_content, base_uri)
            else:
                buf = self.textview.get_buffer()
                buf.set_text(soup.get_text())

            total = len(self.items)
            self.progress.set_fraction((self.current_index + 1) / total)
            self.progress.set_text(f"{self.current_index + 1}/{total}")

        except Exception as e:
            print(f"Error displaying page: {e}")
            self.show_error(f"Error displaying page: {e}")

    def _scroll_to_fragment(self, fragment):
        """Scroll to a specific anchor after page load"""
        if self.webview and fragment:
            js_code = f"""
                var element = document.getElementById('{fragment}');
                if (element) {{
                    element.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                }}
            """
            try:
                # evaluate_javascript signature varies; wrap in try/except
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js_code, None, None, None)
                except Exception:
                    pass
        return False

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
        """Clean up resources and reset state"""
        # Clean up temp directory first
        if getattr(self, "temp_dir", None) and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")

        # Reset all state variables
        self.temp_dir = None
        self.book = None
        self.items = []
        self.item_map = {}
        self.css_content = ""
        self.current_index = 0

        # Clear the TOC model
        try:
            if getattr(self, "toc_root_store", None):
                self.toc_root_store = Gio.ListStore(item_type=TocItem)
                self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                self.toc_listview.set_model(self.toc_sel)
        except Exception as e:
            print(f"Error clearing TOC store: {e}")

        # Update navigation state
        self.update_navigation()

        # Clear the display
        if self.webview:
            try:
                self.webview.load_html("<!DOCTYPE html><html><body></body></html>", "")
            except Exception:
                pass
        elif hasattr(self, 'textview'):
            try:
                buf = self.textview.get_buffer()
                buf.set_text("")
            except Exception:
                pass

        # Reset title
        self.content_title_label.set_text(APP_NAME)
        self.set_title(APP_NAME)


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

