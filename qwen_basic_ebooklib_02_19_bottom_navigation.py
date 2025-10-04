#!/usr/bin/env python3
# EPUB viewer with libadwaita + GTK4 ListView sidebar TOC (nested, clickable)
import gi, os, tempfile, traceback, shutil, urllib.parse
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GObject, Gdk, GdkPixbuf

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

# small CSS for rounded hover band (scoped)
_css = """
.epub-sidebar .adw-action-row {
  margin: 4px 6px;
  padding: 6px;
  border-radius: 8px;
  background-color: transparent;
}
.epub-sidebar .adw-action-row:hover {
  background-color: rgba(0,0,0,0.06);
}
.epub-sidebar .adw-action-row.selected {
  background-color: rgba(0,0,0,0.12);
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
        self._toc_actrows = {}

        # main layout
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_vbox)

        # split with sidebar
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.32)
        main_vbox.append(self.split)

        # --- Sidebar (enhanced) ---
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.add_css_class("epub-sidebar")

        # header (open + search + menu + pin)
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        # left: open
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open EPUB")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        # center title
        title_lbl = Gtk.Label(label=APP_NAME)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_title_widget(title_lbl)

        # right: search, menu, pin
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # search revealer
        self.search_revealer = Gtk.Revealer(reveal_child=False)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search TOC")
        # quick filter hookup: naive text filter of visible TOC entries - placeholder behaviour
        self.search_entry.connect("search-changed", lambda e: self._filter_toc(e.get_text()))
        self.search_revealer.set_child(self.search_entry)

        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.set_tooltip_text("Show search")
        search_btn.connect("clicked", lambda *_: self.search_revealer.set_reveal_child(not self.search_revealer.get_reveal_child()))
        btn_box.append(search_btn)

        # menu button (popover)
        menu_model = Gio.Menu()
        menu_model.append("About", "app.about")

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        menu_btn.set_popover(popover)

        # add action handler
        self.get_application().create_action("about",
            lambda *_: self.show_error("EPUB Viewer — minimal menu"))

        # pin/unpin toggle
        self.pin_btn = Gtk.ToggleButton()
        self.pin_btn.add_css_class("flat")
        self.pin_btn.set_tooltip_text("Pin sidebar")
        self.pin_btn.set_icon_name("pin-symbolic")
        self.pin_btn.set_active(True)
        self.pin_btn.connect("toggled", self._on_pin_toggled)
        btn_box.append(self.pin_btn)

        header.pack_end(btn_box)
        sidebar_box.append(header)
        # below header: revealer (search) placed full-width
        sidebar_box.append(self.search_revealer)

        # Book image + metadata area
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        book_box.set_margin_top(6)
        book_box.set_margin_bottom(6)
        book_box.set_margin_start(8)
        book_box.set_margin_end(8)

        # placeholder cover image (use 64x90)
        self.cover_image = Gtk.Image()
        # create a simple blank pixbuf as placeholder to avoid theme-dependent icons
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 90)
        pb.fill(0xddddddff)  # light gray
        self.cover_image.set_from_pixbuf(pb)
        book_box.append(self.cover_image)

        md_v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.book_title = Gtk.Label(label="")
        self.book_title.set_valign(Gtk.Align.START)
        self.book_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_title.set_max_width_chars(20)
        self.book_author = Gtk.Label(label="")
        self.book_author.get_style_context().add_class("dim-label")
        self.book_author.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_author.set_max_width_chars(20)
        md_v.append(self.book_title)
        md_v.append(self.book_author)
        book_box.append(md_v)

        sidebar_box.append(book_box)

        # --- stack area with TOC / Annotations / Bookmarks ---
        self.side_stack = Gtk.Stack()
        self.side_stack.set_vexpand(True)

        # TOC view (scrolled) - reuse your ListView factory
        self.toc_factory = Gtk.SignalListItemFactory()
        self.toc_factory.connect("setup", self._toc_on_setup)
        self.toc_factory.connect("bind", self._toc_on_bind)

        self.toc_root_store = Gio.ListStore(item_type=TocItem)
        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview = Gtk.ListView(model=self.toc_sel, factory=self.toc_factory)
        self.toc_listview.set_vexpand(True)

        toc_scrolled = Gtk.ScrolledWindow()
        toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toc_scrolled.set_vexpand(True)
        toc_scrolled.set_child(self.toc_listview)

        self.side_stack.add_titled(toc_scrolled, "toc", "TOC")

        # annotations placeholder
        ann_list = Gtk.ListBox()
        ann_list.append(Gtk.Label(label="No annotations"))
        ann_scrolled = Gtk.ScrolledWindow()
        ann_scrolled.set_child(ann_list)
        self.side_stack.add_titled(ann_scrolled, "annotations", "Annotations")

        # bookmarks placeholder
        bm_list = Gtk.ListBox()
        bm_list.append(Gtk.Label(label="No bookmarks"))
        bm_scrolled = Gtk.ScrolledWindow()
        bm_scrolled.set_child(bm_list)
        self.side_stack.add_titled(bm_scrolled, "bookmarks", "Bookmarks")

        sidebar_box.append(self.side_stack)

        # bottom tabs (small bar)
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs_box.set_margin_top(6)
        tabs_box.set_margin_bottom(6)
        tabs_box.set_margin_start(6)
        tabs_box.set_margin_end(6)

        # small flat buttons that switch stack
        def make_tab(label, name):
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("flat")
            b.set_hexpand(True)
            b.connect("toggled", lambda btn, nm=name: btn.get_active() and self.side_stack.set_visible_child_name(nm))
            return b

        self.tab_toc = make_tab("TOC", "toc")
        self.tab_ann = make_tab("Annotations", "annotations")
        self.tab_bm = make_tab("Bookmarks", "bookmarks")
        self.tab_toc.set_active(True)

        tabs_box.append(self.tab_toc)
        tabs_box.append(self.tab_ann)
        tabs_box.append(self.tab_bm)

        sidebar_box.append(tabs_box)

        self.split.set_sidebar(sidebar_box)

        # --- Content area (unchanged except toolbar) ---
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

    # ---------- UI helpers ----------
    def _on_pin_toggled(self, btn):
        pinned = btn.get_active()
        # if pinned, ensure sidebar stays visible; if unpinned, allow overlay to hide
        self.split.set_show_sidebar(pinned)

    def _filter_toc(self, text):
        # simple placeholder: when text non-empty, switch to TOC and (future) filter model
        self.tab_toc.set_active(True)
        # TODO: implement actual filtering of the Gio.ListStore entries if desired.
        return

    # ---------- original TOC factory methods (unchanged logic) ----------
    def _toc_on_setup(self, factory, list_item):
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hbox.set_hexpand(True)

        disc = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        disc.set_visible(False)
        hbox.append(disc)

        actrow = Adw.ActionRow()
        actrow.set_activatable(True)
        actrow.set_title("")
        actrow.set_hexpand(True)
        hbox.append(actrow)

        wrapper.append(hbox)

        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nested.set_margin_start(18)
        nested.set_visible(False)
        wrapper.append(nested)

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
            self._set_toc_selected(item)

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
        list_item._bound_item = None

    def _toc_on_bind(self, factory, list_item):
        item = list_item.get_item()

        disc = getattr(list_item, "_disc", None)
        actrow = getattr(list_item, "_actrow", None)
        nested = getattr(list_item, "_nested", None)
        if disc is None or actrow is None or nested is None:
            self._toc_on_setup(factory, list_item)
            disc = list_item._disc; actrow = list_item._actrow; nested = list_item._nested

        prev = getattr(list_item, "_bound_item", None)
        if prev is not None and prev in self._toc_actrows:
            try:
                self._toc_actrows.pop(prev, None)
            except Exception:
                pass
        list_item._bound_item = item

        if not item:
            actrow.set_title("")
            disc.set_visible(False)
            nv = getattr(list_item, "_nested_view", None)
            if nv:
                nv.set_visible(False)
            return

        try:
            self._toc_actrows[item] = actrow
            actrow.remove_css_class("selected")
        except Exception:
            pass

        has_children = item.children.get_n_items() > 0
        actrow.set_title(item.title or "")
        disc.set_visible(has_children)
        if has_children:
            disc.set_from_icon_name("pan-down-symbolic" if nested.get_visible() else "pan-end-symbolic")
        else:
            disc.set_from_icon_name(None)

        if has_children and not getattr(list_item, "_nested_view", None):
            def child_setup(f, li):
                cwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                ch_h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                ch_disc = Gtk.Image.new_from_icon_name("pan-end-symbolic")
                ch_disc.set_visible(False)
                ch_h.append(ch_disc)

                ch_act = Adw.ActionRow()
                ch_act.set_activatable(True)
                ch_act.set_title("")
                ch_act.set_hexpand(True)
                ch_h.append(ch_act)

                cwrap.append(ch_h)

                ch_nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                ch_nested.set_margin_start(18)
                ch_nested.set_visible(False)
                cwrap.append(ch_nested)

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
                    self._set_toc_selected(it)

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
                li._bound_item = None

            def child_bind(f, li):
                it = li.get_item()
                if not it:
                    return
                ch_act = getattr(li, "_row", None)
                ch_disc = getattr(li, "_disc", None)
                ch_nested = getattr(li, "_nested", None)
                if ch_act is None or ch_disc is None or ch_nested is None:
                    return
                prevc = getattr(li, "_bound_item", None)
                if prevc is not None and prevc in self._toc_actrows:
                    try:
                        self._toc_actrows.pop(prevc, None)
                    except Exception:
                        pass
                li._bound_item = it

                try:
                    self._toc_actrows[it] = ch_act
                    ch_act.remove_css_class("selected")
                except Exception:
                    pass

                kids = it.children.get_n_items() > 0
                ch_act.set_title(it.title or "")
                ch_disc.set_visible(kids)
                if kids:
                    ch_disc.set_from_icon_name("pan-down-symbolic" if ch_nested.get_visible() else "pan-end-symbolic")
                else:
                    ch_disc.set_from_icon_name(None)

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

        nv = getattr(list_item, "_nested_view", None)
        if nv:
            nv.set_visible(nested.get_visible())

    # ---------- selection helpers ----------
    def _clear_toc_selection(self):
        try:
            for act in list(self._toc_actrows.values()):
                try:
                    act.remove_css_class("selected")
                except Exception:
                    pass
        except Exception:
            pass

    def _set_toc_selected(self, toc_item):
        try:
            self._clear_toc_selection()
            act = self._toc_actrows.get(toc_item)
            if act:
                act.add_css_class("selected")
        except Exception:
            pass

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

            self.item_map = {it.get_name(): it for it in self.items}
            self.extract_css()

            # title + metadata display
            title = APP_NAME
            author = ""
            try:
                meta = self.book.get_metadata("DC", "title")
                if meta and meta[0]:
                    title = meta[0][0]
                m2 = self.book.get_metadata("DC", "creator")
                if m2 and m2[0]:
                    author = m2[0][0]
            except Exception:
                pass
            self.book_title.set_text(title)
            self.book_author.set_text(author)
            self.content_title_label.set_text(title)
            self.set_title(title or APP_NAME)

            # try to load a cover image if present
            try:
                cov = None
                for it in self.book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    name = it.get_name() or ""
                    if "cover" in name.lower():
                        cov = it; break
                if cov:
                    # write to temp and load pixbuf
                    p = os.path.join(self.temp_dir, os.path.basename(cov.get_name()))
                    with open(p, "wb") as fh:
                        fh.write(cov.get_content())
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(p, 64, 90, True)
                    self.cover_image.set_from_pixbuf(pix)
            except Exception:
                pass

            # populate TOC
            self._populate_toc_tree()

            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception:
            print(traceback.format_exc())
            self.show_error("Error loading EPUB — see console")

    def sanitize_path(self, path):
        if not path:
            return None
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or os.path.isabs(normalized):
            return None
        if ".." in normalized.split(os.sep):
            return None
        return normalized

    def _populate_toc_tree(self):
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

        root = Gio.ListStore(item_type=TocItem)
        def add_node(title, href, parent_store):
            idx = href_to_index(href)
            node = TocItem(title=title or "", href=href or "", index=idx)
            parent_store.append(node)
            return node

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
                        self.toc_root_store = root
                        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                        self.toc_listview.set_model(self.toc_sel)
                        return
        except Exception:
            pass

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

        for i, it in enumerate(self.items):
            title = os.path.basename(it.get_name())
            add_node(title, it.get_name(), root)

        self.toc_root_store = root
        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview.set_model(self.toc_sel)

    # ---------- Internal link handling / display (unchanged) ----------
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
                        for ti in list(self._toc_actrows.keys()):
                            if isinstance(ti, TocItem) and ti.index == i:
                                self._set_toc_selected(ti)
                                break
                        return True
        return False

    def display_page(self, fragment=None):
        if not self.book or not self.items or self.current_index >= len(self.items):
            return
        self.extract_css()
        item = self.items[self.current_index]
        if not item or not hasattr(item, 'get_content'):
            return
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            content = str(soup)
            html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{self.css_content}</style></head><body>{content}</body></html>"""
            if self.webview:
                base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
                if fragment:
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
        if self.webview and fragment:
            js_code = f"""
                var element = document.getElementById('{fragment}');
                if (element) {{
                    element.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                }}
            """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js_code, None, None, None)
                except Exception:
                    pass
        return False

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

    def extract_css(self):
        self.css_content = ""
        if not self.book:
            return
        try:
            for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
                try:
                    self.css_content += item.get_content().decode("utf-8") + "\n"
                except Exception:
                    pass
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
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")
        self.temp_dir = None
        self.book = None
        self.items = []
        self.item_map = {}
        self.css_content = ""
        self.current_index = 0
        try:
            if getattr(self, "toc_root_store", None):
                self.toc_root_store = Gio.ListStore(item_type=TocItem)
                self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                self.toc_listview.set_model(self.toc_sel)
            self._toc_actrows = {}
        except Exception as e:
            print(f"Error clearing TOC store: {e}")
        self.update_navigation()
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
        self.book_title.set_text("")
        self.book_author.set_text("")
        # keep sidebar visible when pinned; otherwise hide
        if not getattr(self, "pin_btn", None) or not self.pin_btn.get_active():
            self.split.set_show_sidebar(False)

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

