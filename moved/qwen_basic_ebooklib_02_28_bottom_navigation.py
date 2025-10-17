#!/usr/bin/env python3
# EPUB viewer with libadwaita + GTK4 ListView sidebar TOC (nested, clickable)
import gi, os, tempfile, traceback, shutil, urllib.parse, glob, re
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GObject, Gdk, GdkPixbuf

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import zipfile, pathlib

# --- Safe NCX monkey-patch (avoid crashes on some EPUBs) ---
import ebooklib.epub
def _safe_parse_ncx(self, ncxFile):
    self.book.toc = []
ebooklib.epub.EpubReader._parse_ncx = _safe_parse_ncx

APP_NAME = "EPUB Viewer"
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

# cover target size
COVER_W, COVER_H = 70, 100

# sidebar small CSS (rounded hover band, margin)
_css = """
.epub-sidebar .adw-action-row {
  margin: 5px;
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
.book-title {
  font-weight: 600;
  margin-bottom: 2px;
}
.book-author {
  color: rgba(0,0,0,0.6);
  font-size: 12px;
}
"""
_css_provider = Gtk.CssProvider()
_css_provider.load_from_data(_css.encode("utf-8"))
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _css_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
)

# Theme CSS injected into each displayed HTML page (user-provided dark-mode snippet included)
THEME_INJECTION_CSS = """
@media (prefers-color-scheme: dark) {
    body { background-color:#242424; color:#e3e3e3; }
    blockquote { border-left-color:#62a0ea; }
    .tts-highlight { background:rgba(0,127,0,0.75); box-shadow:0 0 0 2px rgba(0,127,0,0.75); }
}
"""
# GTK-only dark override for .book-author (applies to widget labels)
_dark_override_css = """
.epub-sidebar .book-author {
  color: rgba(255,255,255,0.6);
}
"""
_dark_provider = Gtk.CssProvider()
_dark_provider.load_from_data(_dark_override_css.encode("utf-8"))

settings = Gtk.Settings.get_default()
def _update_gtk_dark_provider(settings, pspec=None):
    try:
        if settings.get_property("gtk-application-prefer-dark-theme"):
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), _dark_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        else:
            Gtk.StyleContext.remove_provider_for_display(
                Gdk.Display.get_default(), _dark_provider)
    except Exception:
        pass

try:
    settings.connect("notify::gtk-application-prefer-dark-theme", _update_gtk_dark_provider)
except Exception:
    pass
_update_gtk_dark_provider(settings)
# // end of dark override for .book-author

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
        self._tab_buttons = []

        # NEW: canonical href -> TocItem map (variants included)
        self.href_map = {}

        # main layout
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_vbox)

        # Overlay split view
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.32)
        main_vbox.append(self.split)

        # --- Sidebar ---
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.add_css_class("epub-sidebar")

        # header inside sidebar (open + search + menu + pin)
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open EPUB")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        title_lbl = Gtk.Label(label=APP_NAME)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_title_widget(title_lbl)

        # right-side header controls inside sidebar
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.search_revealer = Gtk.Revealer(reveal_child=False)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search TOC")
        self.search_entry.connect("search-changed", lambda e: self._filter_toc(e.get_text()))
        self.search_revealer.set_child(self.search_entry)

        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.set_tooltip_text("Show search")
        search_btn.connect("clicked", lambda *_: self.search_revealer.set_reveal_child(not self.search_revealer.get_reveal_child()))
        btn_box.append(search_btn)

        menu_model = Gio.Menu()
        menu_model.append("About", "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_popover(Gtk.PopoverMenu.new_from_model(menu_model))
        btn_box.append(menu_btn)

        app.create_action("about", lambda a, p: self.show_error("EPUB Viewer — minimal menu"))


        header.pack_end(btn_box)
        sidebar_box.append(header)
        sidebar_box.append(self.search_revealer)

        # Book cover + metadata: HORIZONTAL (cover on left, title/author on right in vertical layout)
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)  # Horizontal layout
        book_box.set_valign(Gtk.Align.START)
        book_box.set_margin_top(6)
        book_box.set_margin_bottom(6)
        book_box.set_margin_start(8)
        book_box.set_margin_end(8)
        book_box.set_valign(Gtk.Align.CENTER)

        # Cover image
        self.cover_image = Gtk.Image()
        placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
        placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
        self.cover_image.set_from_paintable(placeholder_tex)
        try:
            self.cover_image.set_valign(Gtk.Align.START)
            self.cover_image.set_halign(Gtk.Align.START)
            self.cover_image.set_size_request(COVER_W, COVER_H)
        except Exception:
            pass
        book_box.append(self.cover_image)
        book_box.set_valign(Gtk.Align.CENTER)
        # Title and author in vertical box (on the right side of the cover)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)  # Vertical layout for text
        text_box.set_valign(Gtk.Align.CENTER)

        self.book_title = Gtk.Label(label="")
        self.book_title.add_css_class("book-title")
        self.book_title.set_halign(Gtk.Align.START)  # Left align within the text box
        self.book_title.set_xalign(0.0)
        self.book_title.set_max_width_chars(50)
        self.book_title.set_wrap(True)
        self.book_title.set_lines(2)  # Limit to 2 lines
        self.book_title.set_ellipsize(Pango.EllipsizeMode.END)  # Add ellipsis for overflow



        self.book_author = Gtk.Label(label="")
        self.book_author.add_css_class("book-author")
        self.book_author.set_halign(Gtk.Align.START)  # Left align within the text box
        self.book_author.set_xalign(0.0)

        text_box.append(self.book_title)
        text_box.append(self.book_author)

        book_box.append(text_box)  # Add the vertical text box to the horizontal book box

        sidebar_box.append(book_box)

        # Stack with TOC / annotations / bookmarks
        self.side_stack = Gtk.Stack()
        self.side_stack.set_vexpand(True)

        # TOC ListView
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

        # placeholders for annotations/bookmarks
        ann_list = Gtk.ListBox(); ann_list.append(Gtk.Label(label="No annotations"))
        ann_scrolled = Gtk.ScrolledWindow(); ann_scrolled.set_child(ann_list)
        self.side_stack.add_titled(ann_scrolled, "annotations", "Annotations")

        bm_list = Gtk.ListBox(); bm_list.append(Gtk.Label(label="No bookmarks"))
        bm_scrolled = Gtk.ScrolledWindow(); bm_scrolled.set_child(bm_list)
        self.side_stack.add_titled(bm_scrolled, "bookmarks", "Bookmarks")

        sidebar_box.append(self.side_stack)

        # bottom tabs (icon-only toggle buttons) with exclusive behavior
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs_box.set_margin_top(6); tabs_box.set_margin_bottom(6)
        tabs_box.set_margin_start(6); tabs_box.set_margin_end(6)

        def make_icon_tab(icon_name, tooltip, name):
            b = Gtk.ToggleButton()
            b.add_css_class("flat")
            img = Gtk.Image.new_from_icon_name(icon_name)
            b.set_child(img)
            b.set_tooltip_text(tooltip)
            b.set_hexpand(True)
            self._tab_buttons.append((b, name))
            def on_toggled(btn, nm=name):
                if btn.get_active():
                    for sib, _nm in self._tab_buttons:
                        if sib is not btn:
                            try: sib.set_active(False)
                            except Exception: pass
                    self.side_stack.set_visible_child_name(nm)
            b.connect("toggled", on_toggled)
            return b

        self.tab_toc = make_icon_tab("view-list-symbolic", "TOC", "toc")
        self.tab_ann = make_icon_tab("document-edit-symbolic", "Annotations", "annotations")
        self.tab_bm  = make_icon_tab("user-bookmarks-symbolic", "Bookmarks", "bookmarks")
        self.tab_toc.set_active(True)
        tabs_box.append(self.tab_toc); tabs_box.append(self.tab_ann); tabs_box.append(self.tab_bm)
        sidebar_box.append(tabs_box)

        self.split.set_sidebar(sidebar_box)

        # --- Content area ---
        self.toolbar = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar()
        self.content_header.add_css_class("flat")
        self.content_title_label = Gtk.Label(label=APP_NAME)
        self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)

        # show/hide sidebar button in content header (left-of-end)
        self.content_sidebar_toggle = Gtk.Button()
        self.content_sidebar_toggle.add_css_class("flat")
        img = Gtk.Image.new_from_icon_name("sidebar-show-symbolic")
        self.content_sidebar_toggle.set_child(img)
        self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
        self.content_sidebar_toggle.connect("clicked", self._on_sidebar_toggle)
        self.content_header.pack_end(self.content_sidebar_toggle)

        self.toolbar.add_top_bar(self.content_header)

        self.scrolled = Gtk.ScrolledWindow(); self.scrolled.set_vexpand(True)

        # bottom navigation bar
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6); bottom_bar.set_margin_bottom(6)
        bottom_bar.set_margin_start(6); bottom_bar.set_margin_end(6)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic"); self.prev_btn.add_css_class("flat")
        self.prev_btn.set_sensitive(False); self.prev_btn.connect("clicked", self.prev_page)
        bottom_bar.append(self.prev_btn)

        self.progress = Gtk.ProgressBar(); self.progress.set_show_text(True); self.progress.set_hexpand(True)
        bottom_bar.append(self.progress)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic"); self.next_btn.add_css_class("flat")
        self.next_btn.set_sensitive(False); self.next_btn.connect("clicked", self.next_page)
        bottom_bar.append(self.next_btn)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); content_box.set_vexpand(True)
        content_box.append(self.scrolled); content_box.append(bottom_bar)
        self.toolbar.set_content(content_box)
        self.split.set_content(self.toolbar)

        # WebKit or fallback textview
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit
            self.WebKit = WebKit
            self.webview = WebKit.WebView()
            # note: we keep webview creation simple to avoid changing other behavior
            self.scrolled.set_child(self.webview)
            self.webview.connect("decide-policy", self.on_decide_policy)
        except Exception:
            self.WebKit = None
            self.webview = None
            self.textview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)

        # Try responsive Breakpoint usage; fallback to size-allocate
        try:
            bp = Adw.Breakpoint()
            try:
                bp.set_condition("max-width: 400sp")
            except Exception:
                pass
            try:
                bp.add_setter(self.split, "collapsed", True)
            except Exception:
                pass
            try:
                self.add(bp)
            except Exception:
                pass
        except Exception:
            def on_size_allocate(win, alloc):
                try:
                    w = alloc.width
                    collapsed = w < 400
                    if getattr(self.split, "get_collapsed", None):
                        if self.split.get_collapsed() != collapsed:
                            self.split.set_collapsed(collapsed)
                    else:
                        self.split.set_show_sidebar(not collapsed)
                except Exception:
                    pass
            self.connect("size-allocate", on_size_allocate)


    # ---------- UI helpers ----------
    def _on_sidebar_toggle(self, btn):
        new = not self.split.get_show_sidebar()
        try:
            self.split.set_show_sidebar(new)
        except Exception:
            pass
        # update icon
        icon = "sidebar-show-symbolic"
        child = btn.get_child()
        if isinstance(child, Gtk.Image):
            try:
                child.set_from_icon_name(icon)
            except Exception:
                pass

    def _filter_toc(self, text):
        self.tab_toc.set_active(True)
        # TODO: implement real filtering
        return

    # ---------- TOC ListView setup/bind (kept original logic) ----------
    def _toc_on_setup(self, factory, list_item):
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hbox.set_hexpand(True)

        disc = Gtk.Image.new_from_icon_name("pan-end-symbolic"); disc.set_visible(False)
        hbox.append(disc)

        actrow = Adw.ActionRow(); actrow.set_activatable(True); actrow.set_title(""); actrow.set_hexpand(True)
        hbox.append(actrow)

        wrapper.append(hbox)

        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nested.set_margin_start(18); nested.set_visible(False)
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

        g = Gtk.GestureClick(); g.connect("pressed", lambda *_: _toggle_only()); disc.add_controller(g)

        def _open_only(_):
            item = list_item.get_item()
            if not item:
                return
            href = item.href or ""
            fragment = href.split("#", 1)[1] if "#" in href else None
            if isinstance(item.index, int) and item.index >= 0:
                self.current_index = item.index; self.update_navigation(); self.display_page(fragment=fragment)
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
        g2 = Gtk.GestureClick(); g2.connect("pressed", lambda *_: _open_only(None)); actrow.add_controller(g2)

        list_item.set_child(wrapper)
        list_item._hbox = hbox; list_item._disc = disc; list_item._actrow = actrow
        list_item._nested = nested; list_item._nested_view = None; list_item._bound_item = None

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
            try: self._toc_actrows.pop(prev, None)
            except Exception: pass
        list_item._bound_item = item

        if not item:
            actrow.set_title(""); disc.set_visible(False)
            nv = getattr(list_item, "_nested_view", None)
            if nv: nv.set_visible(False)
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
                ch_disc = Gtk.Image.new_from_icon_name("pan-end-symbolic"); ch_disc.set_visible(False); ch_h.append(ch_disc)

                ch_act = Adw.ActionRow(); ch_act.set_activatable(True); ch_act.set_title(""); ch_act.set_hexpand(True); ch_h.append(ch_act)
                cwrap.append(ch_h)

                ch_nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                ch_nested.set_margin_start(18); ch_nested.set_visible(False)
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
                        if gv: gv.set_visible(vis)

                gch = Gtk.GestureClick(); gch.connect("pressed", lambda *_: _toggle_child()); ch_disc.add_controller(gch)

                def _open_child(_):
                    it = li.get_item()
                    if not it:
                        return
                    href = it.href or ""
                    fragment = href.split("#", 1)[1] if "#" in href else None
                    if isinstance(it.index, int) and it.index >= 0:
                        self.current_index = it.index; self.update_navigation(); self.display_page(fragment=fragment)
                    elif href:
                        try:
                            base = urllib.parse.unquote(href.split("#", 1)[0])
                            candidate = os.path.join(self.temp_dir or "", base)
                            if self.handle_internal_link("file://" + candidate):
                                return
                        except Exception:
                            pass
                    self._set_toc_selected(it)

                try: ch_act.connect("activated", _open_child)
                except Exception: pass
                gch2 = Gtk.GestureClick(); gch2.connect("pressed", lambda *_: _open_child(None)); ch_act.add_controller(gch2)

                li.set_child(cwrap)
                li._row = ch_act; li._disc = ch_disc; li._nested = ch_nested; li._nested_view = None; li._bound_item = None

            def child_bind(f, li):
                it = li.get_item()
                if not it: return
                ch_act = getattr(li, "_row", None)
                ch_disc = getattr(li, "_disc", None)
                ch_nested = getattr(li, "_nested", None)
                if ch_act is None or ch_disc is None or ch_nested is None: return
                prevc = getattr(li, "_bound_item", None)
                if prevc is not None and prevc in self._toc_actrows:
                    try: self._toc_actrows.pop(prevc, None)
                    except Exception: pass
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
                try: act.remove_css_class("selected")
                except Exception: pass
        except Exception: pass

    def _set_toc_selected(self, toc_item):
        try:
            self._clear_toc_selection()
            act = self._toc_actrows.get(toc_item)
            if act: act.add_css_class("selected")
            # expand parents in listview (best-effort): iterate all keys and open nested views that contain this item
            try:
                for li in self.toc_listview.get_children():
                    pass
            except Exception:
                pass
        except Exception:
            pass

    # ---------- canonical href registration ----------
    def _register_href_variants(self, node: TocItem):
        """
        Register multiple canonical variants for a TocItem.href into self.href_map.
        Makes matching robust: raw href, unquoted, basename, doc#frag, #frag, and spine-resolved names.
        """
        if not node or not getattr(node, "href", None):
            return
        href = (node.href or "").strip()
        if not href:
            return
        keys = set()

        # raw forms
        keys.add(href)
        keys.add(href.lstrip("./"))

        # unquoted forms
        try:
            uq = urllib.parse.unquote(href)
            keys.add(uq); keys.add(uq.lstrip("./"))
        except Exception:
            pass

        # basename
        b = os.path.basename(href)
        if b:
            keys.add(b)
            try:
                keys.add(urllib.parse.unquote(b))
            except Exception:
                pass

        # fragment variations
        if "#" in href:
            doc, frag = href.split("#", 1)
            if frag:
                keys.add(f"#{frag}")
                keys.add(f"{os.path.basename(doc)}#{frag}")
                try:
                    keys.add(f"{urllib.parse.unquote(os.path.basename(doc))}#{frag}")
                except Exception:
                    pass

        # If node.index points to a spine item, register that item's name and variants too
        try:
            if isinstance(node.index, int) and node.index >= 0 and node.index < len(self.items):
                it = self.items[node.index]
                iname = (it.get_name() or "").replace("\\", "/")
                if iname:
                    keys.add(iname)
                    keys.add(os.path.basename(iname))
                    try:
                        keys.add(urllib.parse.unquote(iname))
                        keys.add(urllib.parse.unquote(os.path.basename(iname)))
                    except Exception:
                        pass
        except Exception:
            pass

        # also register variants with common EPUB prefixes
        extras = set()
        for k in list(keys):
            for pfx in ("OEBPS/", "OPS/"):
                extras.add(pfx + k)
        keys.update(extras)

        for k in keys:
            if not k:
                continue
            if k not in self.href_map:
                self.href_map[k] = node

    # ---------- file dialog ----------
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
            if f: self.load_epub(f.get_path())
        except GLib.Error:
            pass

    # ---------- helper: OPF-first cover detection with debugging ----------
    def _find_cover_via_opf(self, extracted_paths, image_names, image_basenames):
        """
        Debug-friendly OPF-first cover detection: (unchanged)
        """
        if not self.temp_dir:
            print("[cover-debug] temp_dir not set")
            return None, None

        pattern = os.path.join(self.temp_dir, "**", "*.opf")
        opf_files = sorted(glob.glob(pattern, recursive=True))
        if not opf_files:
            print("[cover-debug] no .opf files found under", self.temp_dir)
        for opf in opf_files:
            try:
                print(f"[cover-debug] checking OPF: {opf}")
                with open(opf, "rb") as fh:
                    raw = fh.read()
                txt = raw.decode("utf-8", errors="ignore")
                # show lines that mention 'cover' (for debugging)
                for lineno, line in enumerate(txt.splitlines(), 1):
                    if "cover" in line.lower():
                        print(f"[cover-debug]  {os.path.basename(opf)}:{lineno}: {line.strip()}")
                soup = BeautifulSoup(raw, "xml")

                # 1) EPUB2 meta name="cover" content="ID"
                cover_id = None
                meta = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "meta" and tag.has_attr("name") and tag["name"].lower() == "cover")
                if meta and meta.has_attr("content"):
                    cover_id = meta["content"]
                    print(f"[cover-debug]  found meta cover id='{cover_id}' in {opf}")

                href = None
                if cover_id:
                    item_tag = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("id") and tag["id"] == cover_id)
                    if item_tag and item_tag.has_attr("href"):
                        href = item_tag["href"]
                        print(f"[cover-debug]  manifest item for id='{cover_id}' href='{href}'")

                # 2) EPUB3: item with properties="cover-image"
                if not href:
                    item_prop = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("properties") and "cover-image" in tag["properties"])
                    if item_prop and item_prop.has_attr("href"):
                        href = item_prop["href"]
                        print(f"[cover-debug]  found properties='cover-image' href='{href}' in {opf}")

                # 3) item href containing 'cover' and image extension
                if not href:
                    item_cover_href = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'cover.*\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                    if item_cover_href and item_cover_href.has_attr("href"):
                        href = item_cover_href["href"]
                        print(f"[cover-debug]  found item href with 'cover' pattern: '{href}' in {opf}")

                # 4) fallback: first manifest image href
                if not href:
                    first_img = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                    if first_img and first_img.has_attr("href"):
                        href = first_img["href"]
                        print(f"[cover-debug]  fallback to first manifest image href='{href}' in {opf}")

                if not href:
                    print(f"[cover-debug]  no candidate href found in {opf}, continuing")
                    continue

                opf_dir = os.path.dirname(opf)
                # resolve candidate absolute path from OPF dir
                candidate_abs = os.path.normpath(os.path.join(opf_dir, urllib.parse.unquote(href)))
                candidate_abs = os.path.abspath(candidate_abs)
                # alternative: resolve relative to temp_dir root
                candidate_abs2 = os.path.abspath(os.path.normpath(os.path.join(self.temp_dir, urllib.parse.unquote(href))))

                # Build list of variants to try (relative-to-temp entries and basenames)
                try:
                    rel_from_temp = os.path.relpath(candidate_abs, self.temp_dir).replace(os.sep, "/")
                except Exception:
                    rel_from_temp = os.path.basename(candidate_abs)
                variants = [rel_from_temp, os.path.basename(rel_from_temp)]
                for pfx in ("OEBPS/", "OPS/", ""):
                    variants.append(pfx + rel_from_temp)
                    variants.append(pfx + os.path.basename(rel_from_temp))
                try:
                    uq = urllib.parse.unquote(rel_from_temp)
                    variants.append(uq); variants.append(os.path.basename(uq))
                except Exception:
                    pass

                # check exact absolute match first
                if os.path.exists(candidate_abs):
                    print(f"[cover-debug]  candidate_abs exists: {candidate_abs} -> using as cover")
                    return candidate_abs, None
                if os.path.exists(candidate_abs2):
                    print(f"[cover-debug]  candidate_abs2 exists: {candidate_abs2} -> using as cover")
                    return candidate_abs2, None

                # check extracted_paths (they are relative to temp_dir)
                for v in variants:
                    if v in extracted_paths:
                        abs_p = os.path.abspath(os.path.join(self.temp_dir, v))
                        print(f"[cover-debug]  matched extracted_paths variant '{v}' -> {abs_p}")
                        return abs_p, None
                    if v in image_names:
                        print(f"[cover-debug]  matched image_names key '{v}' -> image item {image_names[v].get_name()}")
                        return None, image_names[v]
                    bn = os.path.basename(v)
                    if bn in image_basenames:
                        print(f"[cover-debug]  matched image_basenames basename '{bn}' -> image item {image_basenames[bn][0].get_name()}")
                        return None, image_basenames[bn][0]

                # try basename-only search across extracted_paths
                bn = os.path.basename(href)
                for p in extracted_paths:
                    if os.path.basename(p).lower() == bn.lower():
                        abs_p = os.path.abspath(os.path.join(self.temp_dir, p))
                        print(f"[cover-debug]  matched by basename '{bn}' -> {abs_p}")
                        return abs_p, None

                print(f"[cover-debug]  no matching extracted file for href='{href}' in {opf}, continuing")
            except Exception as e:
                print(f"[cover-debug] error reading OPF {opf}: {e}")
                continue

        print("[cover-debug] finished searching OPF files: no cover found via OPF heuristics")
        return None, None

    # ---------- Load EPUB (includes EPUB2/3 cover detection) ----------
    def load_epub(self, path):
        try:
            self.cleanup()
            self.book = epub.read_epub(path)

            # collect documents ordered by spine if possible
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

            # extract files safely to temp dir
            self.temp_dir = tempfile.mkdtemp()
            extracted_paths = set()
            for root, _, files in os.walk(self.temp_dir):
                for fn in files:
                    rel = os.path.relpath(os.path.join(root, fn), self.temp_dir).replace(os.sep, "/")
                    extracted_paths.add(rel)

            # if no .opf found, fallback to unzip the original epub to preserve folder layout
            opf_files = glob.glob(os.path.join(self.temp_dir, "**", "*.opf"), recursive=True)
            if not opf_files:
                try:
                    print(f"[cover-debug] no .opf under {self.temp_dir} — falling back to zip extraction of original epub")
                    with zipfile.ZipFile(path, "r") as z:
                        z.extractall(self.temp_dir)
                    # rebuild extracted_paths after full unzip
                    extracted_paths = set()
                    for root, _, files in os.walk(self.temp_dir):
                        for fn in files:
                            rel = os.path.relpath(os.path.join(root, fn), self.temp_dir).replace(os.sep, "/")
                            extracted_paths.add(rel)
                    opf_files = glob.glob(os.path.join(self.temp_dir, "**", "*.opf"), recursive=True)
                    print(f"[cover-debug] opf files after fallback unzip: {opf_files}")
                except Exception as e:
                    print(f"[cover-debug] fallback unzip failed: {e}")
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
                    extracted_paths.add(sanitized_path.replace("\\", "/"))
                except OSError as e:
                    print(f"Failed to extract {item_path}: {e}")
                    continue

            # maps for quick lookup
            image_items = list(self.book.get_items_of_type(ebooklib.ITEM_IMAGE))
            image_names = { (im.get_name() or "").replace("\\", "/"): im for im in image_items }
            image_basenames = {}
            for im in image_items:
                bn = os.path.basename((im.get_name() or "")).replace("\\", "/")
                if bn:
                    image_basenames.setdefault(bn, []).append(im)

            self.item_map = {it.get_name(): it for it in self.items}
            self.extract_css()

            # metadata
            title = APP_NAME; author = ""
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

            # ---------- robust cover loading (OPF-first, then other heuristics) ----------
            try:
                cover_path_to_use = None
                cover_item_obj = None

                def variants_for(relpath):
                    r = relpath.replace("\\", "/")
                    base = os.path.basename(r)
                    out = set([r, base])
                    for p in ("OEBPS/", "OPS/", ""):
                        out.add(p + r)
                        out.add(p + base)
                    try:
                        uq = urllib.parse.unquote(r)
                        out.add(uq); out.add(os.path.basename(uq))
                    except Exception:
                        pass
                    return out

                # 1) Try .opf first (with debug)
                cpath, citem = self._find_cover_via_opf(extracted_paths, image_names, image_basenames)
                if cpath:
                    cover_path_to_use = cpath
                    print(f"[cover-debug] chosen cover (from OPF) => {cover_path_to_use}")
                elif citem:
                    cover_item_obj = citem
                    print(f"[cover-debug] chosen cover item (from OPF) => {cover_item_obj.get_name()}")

                # 2) If not found via OPF, prefer explicit OPS/cover.xhtml or other title documents
                if not cover_path_to_use and not cover_item_obj:
                    cover_doc = None
                    priority_names = (
                        "ops/cover.xhtml", "oebps/cover.xhtml", "ops/cover.html", "cover.xhtml",
                        "cover.html", "ops/title.xhtml", "title.xhtml", "ops/titlepage.xhtml", "titlepage.xhtml",
                    )
                    docs_list = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
                    lower_map = { (d.get_name() or "").lower(): d for d in docs_list }
                    for pn in priority_names:
                        if pn in lower_map:
                            cover_doc = lower_map[pn]
                            break
                    if not cover_doc:
                        for d in docs_list:
                            n = (d.get_name() or "").lower()
                            if "/ops/cover" in n or n.endswith("cover.xhtml") or n.endswith("cover.html") or "cover.xhtml" in n:
                                cover_doc = d
                                break
                    if not cover_doc:
                        for d in docs_list:
                            try:
                                soup = BeautifulSoup(d.get_content(), "html.parser")
                                if soup.find(class_="cover") or soup.find("p", {"class": "cover"}):
                                    cover_doc = d
                                    break
                            except Exception:
                                continue

                    if cover_doc:
                        try:
                            soup = BeautifulSoup(cover_doc.get_content(), "html.parser")
                            doc_dir = os.path.dirname(cover_doc.get_name() or "")
                            srcs = []
                            img = soup.find("img", src=True)
                            if img:
                                srcs.append(img["src"])
                            for svg_im in soup.find_all("image"):
                                if svg_im.has_attr("xlink:href"):
                                    srcs.append(svg_im["xlink:href"])
                                elif svg_im.has_attr("href"):
                                    srcs.append(svg_im["href"])
                            for src in srcs:
                                if not src:
                                    continue
                                src = src.split("#", 1)[0]
                                src = urllib.parse.unquote(src)
                                candidate_rel = os.path.normpath(os.path.join(doc_dir, src)).replace("\\", "/")
                                for v in variants_for(candidate_rel):
                                    if v in extracted_paths:
                                        cover_path_to_use = os.path.join(self.temp_dir, v)
                                        break
                                    if v in image_names:
                                        cover_item_obj = image_names[v]
                                        break
                                    bn = os.path.basename(v)
                                    if bn in image_basenames:
                                        cover_item_obj = image_basenames[bn][0]
                                        break
                                if cover_path_to_use or cover_item_obj:
                                    break
                        except Exception:
                            pass

                # 3) fallback: try any image item whose name contains 'cover'
                if not cover_path_to_use and not cover_item_obj:
                    for im_name, im in image_names.items():
                        if "cover" in im_name.lower() or "cover" in os.path.basename(im_name).lower():
                            cover_item_obj = im
                            print(f"[cover-debug] fallback matched image item by name -> {im.get_name()}")
                            break

                # 4) final fallback: any extracted image file
                if not cover_path_to_use and not cover_item_obj:
                    for p in extracted_paths:
                        if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                            cover_path_to_use = os.path.join(self.temp_dir, p)
                            print(f"[cover-debug] final fallback using extracted image -> {cover_path_to_use}")
                            break

                # If we have an image item object but not an extracted path, try to find its extracted file
                if cover_item_obj and not cover_path_to_use:
                    iname = (cover_item_obj.get_name() or "").replace("\\", "/")
                    for cand in (iname, os.path.basename(iname)):
                        if cand in extracted_paths:
                            cover_path_to_use = os.path.join(self.temp_dir, cand)
                            print(f"[cover-debug] wrote cover from image item by found extracted path -> {cover_path_to_use}")
                            break
                        for pfx in ("OEBPS/", "OPS/"):
                            if (pfx + cand) in extracted_paths:
                                cover_path_to_use = os.path.join(self.temp_dir, pfx + cand)
                                print(f"[cover-debug] matched with prefix -> {cover_path_to_use}")
                                break
                        if cover_path_to_use:
                            break

                # If still no extracted file but image item has bytes, write them out
                if not cover_path_to_use and cover_item_obj:
                    try:
                        raw = cover_item_obj.get_content()
                        if raw:
                            tmpfn = os.path.join(self.temp_dir, "cover_from_item_" + os.urandom(6).hex())
                            with open(tmpfn, "wb") as fh:
                                fh.write(raw)
                            cover_path_to_use = tmpfn
                            print(f"[cover-debug] wrote cover bytes to {tmpfn}")
                    except Exception:
                        pass

                # load pixbuf if available
                if cover_path_to_use and os.path.exists(cover_path_to_use):
                    try:
                        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover_path_to_use, COVER_W, COVER_H, True)
                        tex = Gdk.Texture.new_for_pixbuf(pix)
                        self.cover_image.set_from_paintable(tex)
                        try: self.cover_image.set_size_request(COVER_W, COVER_H)
                        except Exception: pass
                    except Exception:
                        cover_path_to_use = None

                # placeholder if nothing worked
                if not cover_path_to_use:
                    placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
                    placeholder_pb.fill(0xddddddff)
                    placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
                    self.cover_image.set_from_paintable(placeholder_tex)
                    try: self.cover_image.set_size_request(COVER_W, COVER_H)
                    except Exception: pass

            except Exception:
                pass
            # ---------- end cover loading ----------

            # populate TOC and show first page
            self._populate_toc_tree()
            self.current_index = 0
            self.update_navigation()
            self.display_page()

        except Exception:
            print(traceback.format_exc())
            self.show_error("Error loading EPUB — see console")


    def sanitize_path(self, path):
        if not path: return None
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or os.path.isabs(normalized): return None
        if ".." in normalized.split(os.sep): return None
        return normalized

    def _populate_toc_tree(self):
        def href_to_index(href):
            if not href: return -1
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
            # NEW: register href variants for robust mapping
            try:
                self._register_href_variants(node)
            except Exception:
                pass
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

    # ---------- Internal link handling & display ----------
    def on_decide_policy(self, webview, decision, decision_type):
        if self.WebKit and decision_type == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            try:
                uri = decision.get_request().get_uri()
            except Exception:
                return False
            if uri and uri.startswith("file://"):
                if self.handle_internal_link(uri):
                    try: decision.ignore()
                    except Exception: pass
                    return True
        return False

    def _find_tocitem_for_candidates(self, candidates, fragment=None):
        """
        Try to find a TocItem for any of the candidate href strings or fragment.
        Returns TocItem or None.
        """
        # try exact candidates first
        for c in candidates:
            if not c: continue
            t = self.href_map.get(c)
            if t:
                return t
            # basename fallback
            bn = os.path.basename(c)
            t = self.href_map.get(bn)
            if t:
                return t
        # try fragment-only matches
        if fragment:
            frag_keys = [f"#{fragment}", fragment, os.path.basename(fragment)]
            for fk in frag_keys:
                t = self.href_map.get(fk)
                if t:
                    return t
        return None

    def handle_internal_link(self, uri):
        """
        Enhanced internal link handling:
        - If link points to a document that exists in spine (self.items) -> display_page
        - If link points to an extracted resource (image/html) -> load an HTML wrapper that injects extracted CSS so CSS is applied
        - If a matching TocItem exists for the link (or fragment), select it in the sidebar and sync current_index
        - Returns True when handled (prevents WebKit from directly loading the raw file)
        """
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

        # Try to find a TocItem that matches any of the candidate forms or fragment.
        toc_match = self._find_tocitem_for_candidates(candidates, fragment)
        if toc_match:
            # if the toc item points to a spine index, switch to that page and scroll to fragment
            if isinstance(toc_match.index, int) and toc_match.index >= 0:
                self.current_index = toc_match.index
                self.update_navigation()
                # If fragment present, pass it to display_page; else if toc_match.href has fragment, use that
                frag = fragment or (toc_match.href.split("#", 1)[1] if "#" in (toc_match.href or "") else None)
                self.display_page(fragment=frag)
            else:
                # toc refers to non-spine item (like cover.html) - try to resolve in temp_dir and load
                href = toc_match.href or ""
                candidate_path = None
                try:
                    candidate_path = os.path.join(self.temp_dir or "", urllib.parse.unquote(href.split("#",1)[0]))
                except Exception:
                    pass
                if candidate_path and os.path.exists(candidate_path):
                    return self.handle_internal_link("file://" + candidate_path + ("#" + fragment if fragment else ""))
                # if we couldn't resolve a file, still highlight sidebar
                self._set_toc_selected(toc_match)
            return True

        # regular spine match check (existing behavior)
        for cand in candidates:
            if cand in self.item_map:
                for i, it in enumerate(self.items):
                    if it.get_name() == cand:
                        self.current_index = i
                        self.update_navigation()
                        self.display_page(fragment=fragment)
                        # try to select a toc item that corresponds to this document
                        for ti in list(self._toc_actrows.keys()):
                            if isinstance(ti, TocItem) and ti.index == i:
                                self._set_toc_selected(ti)
                                break
                        return True

        possible_paths = []
        if self.temp_dir:
            possible_paths.append(os.path.join(self.temp_dir, rel))
            possible_paths.append(os.path.join(self.temp_dir, os.path.basename(rel)))
        possible_paths.append(path)
        for p in possible_paths:
            if not p:
                continue
            if os.path.exists(p):
                ext = os.path.splitext(p)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
                    page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS
                    img_uri = "file://" + p
                    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{page_css}</style></head>
                    <body style="margin:0;display:flex;align-items:center;justify-content:center;">
                      <img src="{img_uri}" alt="image" style="max-width:100%;height:auto;"/>
                    </body></html>"""
                    base_uri = "file://" + (os.path.dirname(p) or "/") + "/"
                    try:
                        if self.webview:
                            self.webview.load_html(html, base_uri)
                        else:
                            self.textview.get_buffer().set_text(f"[Image] {p}")
                    except Exception:
                        pass
                    return True
                if ext in (".html", ".xhtml", ".htm"):
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                            content = fh.read()
                        page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS
                        html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{page_css}</style></head><body>{content}</body></html>"""
                        base_uri = "file://" + (os.path.dirname(p) or "/") + "/"
                        if self.webview:
                            self.webview.load_html(html_content, base_uri)
                            if fragment:
                                GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                        else:
                            self.textview.get_buffer().set_text(BeautifulSoup(content, "html.parser").get_text())
                        return True
                    except Exception:
                        pass
                return False
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
            page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS
            html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{page_css}</style></head><body>{content}</body></html>"""
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
            # Try to highlight a matching TOC entry for this document if available
            try:
                for ti in list(self.href_map.values()):
                    if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == self.current_index:
                        self._set_toc_selected(ti)
                        break
            except Exception:
                pass
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

    # ---------- CSS extraction ----------
    def extract_css(self):
        self.css_content = ""
        if not self.book: return
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
            try: shutil.rmtree(self.temp_dir)
            except Exception as e: print(f"Error cleaning up temp directory: {e}")
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
            self.href_map = {}
        except Exception as e:
            print(f"Error clearing TOC store: {e}")
        self.update_navigation()
        if self.webview:
            try: self.webview.load_html("<!DOCTYPE html><html><body></body></html>", "")
            except Exception: pass
        elif hasattr(self, 'textview'):
            try: self.textview.get_buffer().set_text("")
            except Exception: pass
        self.book_title.set_text("")
        self.book_author.set_text("")

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

