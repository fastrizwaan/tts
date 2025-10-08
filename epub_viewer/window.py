import gi
import os
import tempfile
import traceback
import shutil
import urllib.parse
import glob
import re
import json
import hashlib

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GObject, Gdk, GdkPixbuf
import cairo
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import zipfile

# --- Safe NCX monkey-patch (avoid crashes on some EPUBs) ---
import ebooklib.epub
def _safe_parse_ncx(self, ncxFile):
    self.book.toc = []
ebooklib.epub.EpubReader._parse_ncx = _safe_parse_ncx

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

from .constants import (
    APP_NAME,
    COVER_W, COVER_H,
    LIB_COVER_W, LIB_COVER_H,
    CSS_SIDEBAR,
    CSS_LIBRARY,
    CSS_HOVER_LIGHT,
    CSS_HOVER_DARK,
    DARK_OVERRIDE_CSS,
    THEME_INJECTION_CSS,
)
from .utils import highlight_markup, sanitize_path, create_rounded_cover_texture
from .library import load_library, save_library
from .toc import TocItem
from .webview_helpers import wrap_html
from .epub_loader import extract_css, find_cover_via_opf, update_library_entry


class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 800)
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
        self.href_map = {}
        self.last_cover_path = None
        self.book_path = None

        # NEW: column settings
        self.column_mode_use_width = False   # False => use column-count; True => use column-width
        self.column_count = 1                # 1..10
        self.column_width_px = 200           # 100..500
        self._column_gap = 32                # px gap between columns

        # library
        self.library = load_library()
        self.library_search_text = ""
        self._lib_search_handler_id = None

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

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        self.library_btn = Gtk.Button(icon_name="show-library-symbolic")
        self.library_btn.set_tooltip_text("Show Library")
        self.library_btn.add_css_class("flat")
        self.library_btn.connect("clicked", self.on_library_clicked)
        header.pack_start(self.library_btn)

        title_lbl = Gtk.Label(label=APP_NAME)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_title_widget(title_lbl)
        sidebar_box.append(header)

        # Book cover + metadata
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        book_box.set_valign(Gtk.Align.START)
        book_box.set_margin_top(6); book_box.set_margin_bottom(6)
        book_box.set_margin_start(8); book_box.set_margin_end(8)

        self.cover_image = Gtk.Image()
        placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
        placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
        self.cover_image.set_from_paintable(placeholder_tex)
        self.cover_image.set_valign(Gtk.Align.START)
        self.cover_image.set_halign(Gtk.Align.START)
        self.cover_image.set_size_request(COVER_W, COVER_H)
        book_box.append(self.cover_image)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_hexpand(True)

        self.book_title = Gtk.Label(label="")
        self.book_title.add_css_class("book-title")
        self.book_title.set_halign(Gtk.Align.START); self.book_title.set_xalign(0.0)
        self.book_title.set_max_width_chars(18); self.book_title.set_wrap(True); self.book_title.set_lines(2)
        self.book_title.set_ellipsize(Pango.EllipsizeMode.END)

        self.book_author = Gtk.Label(label="")
        self.book_author.add_css_class("book-author")
        self.book_author.set_halign(Gtk.Align.START); self.book_author.set_xalign(0.0)

        text_box.append(self.book_title); text_box.append(self.book_author)
        book_box.append(text_box)
        sidebar_box.append(book_box)

        # TOC / annotations / bookmarks stack
        self.side_stack = Gtk.Stack(); self.side_stack.set_vexpand(True)

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

        ann_list = Gtk.ListBox(); ann_list.append(Gtk.Label(label="No annotations"))
        ann_scrolled = Gtk.ScrolledWindow(); ann_scrolled.set_child(ann_list)
        self.side_stack.add_titled(ann_scrolled, "annotations", "Annotations")

        bm_list = Gtk.ListBox(); bm_list.append(Gtk.Label(label="No bookmarks"))
        bm_scrolled = Gtk.ScrolledWindow(); bm_scrolled.set_child(bm_list)
        self.side_stack.add_titled(bm_scrolled, "bookmarks", "Bookmarks")

        sidebar_box.append(self.side_stack)

        # bottom tabs
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs_box.set_margin_top(6); tabs_box.set_margin_bottom(6)
        tabs_box.set_margin_start(6); tabs_box.set_margin_end(6)

        def make_icon_tab(icon_name, tooltip, name):
            b = Gtk.ToggleButton(); b.add_css_class("flat")
            img = Gtk.Image.new_from_icon_name(icon_name)
            b.set_child(img); b.set_tooltip_text(tooltip); b.set_hexpand(True)
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
        self.content_header = Adw.HeaderBar(); self.content_header.add_css_class("flat")

        self.content_sidebar_toggle = Gtk.Button(); self.content_sidebar_toggle.add_css_class("flat")
        self._sidebar_img = Gtk.Image.new_from_icon_name("sidebar-show-symbolic")
        self.content_sidebar_toggle.set_child(self._sidebar_img)
        self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
        self.content_sidebar_toggle.connect("clicked", self._on_sidebar_toggle)
        self.content_header.pack_start(self.content_sidebar_toggle)

        self.open_btn = Gtk.Button(icon_name="document-open-symbolic"); self.open_btn.add_css_class("flat")
        self.open_btn.set_tooltip_text("Open EPUB"); self.open_btn.connect("clicked", self.open_file)
        self.content_header.pack_start(self.open_btn)

        self.content_title_label = Gtk.Label(label=APP_NAME)
        self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END); self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)

        # Columns menu
        self.columns_menu_button = Gtk.MenuButton()
        self.columns_menu_button.set_icon_name("columns-symbolic")
        self.columns_menu_button.add_css_class("flat")
        menu = Gio.Menu()
        columns_menu = Gio.Menu()
        for i in range(1, 11):
            columns_menu.append(f"{i} Column{'s' if i>1 else ''}", f"app.set-columns({i})")
        menu.append_submenu("Columns (fixed)", columns_menu)
        width_menu = Gio.Menu()
        for w in (50,100,150,200,300,350,400,450,500):
            width_menu.append(f"{w}px width", f"app.set-column-width({w})")
        menu.append_submenu("Use column width", width_menu)
        self.columns_menu_button.set_menu_model(menu)
        self.columns_menu_button.set_visible(False)
        self.content_header.pack_end(self.columns_menu_button)

        # Search
        self.library_search_revealer = Gtk.Revealer(reveal_child=False)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_bar.set_margin_start(6); search_bar.set_margin_end(6); search_bar.set_margin_top(6); search_bar.set_margin_bottom(6)
        self.library_search_entry = Gtk.SearchEntry()
        self.library_search_entry.set_placeholder_text("Search library (title, author, filename)")
        self._lib_search_handler_id = self.library_search_entry.connect("search-changed", lambda e: self._on_library_search_changed(e.get_text()))
        search_bar.append(self.library_search_entry)
        self.library_search_revealer.set_child(search_bar)

        self.search_toggle_btn = Gtk.Button(icon_name="system-search-symbolic"); self.search_toggle_btn.add_css_class("flat")
        self.search_toggle_btn.set_tooltip_text("Search library"); self.search_toggle_btn.connect("clicked", self._toggle_library_search)
        self.content_header.pack_end(self.search_toggle_btn)

        # Menu
        menu_model = Gio.Menu(); menu_model.append("About", "app.about")
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic"); self.menu_btn.set_popover(Gtk.PopoverMenu.new_from_model(menu_model))
        self.content_header.pack_end(self.menu_btn)

        self.toolbar.add_top_bar(self.content_header)
        self.toolbar.add_top_bar(self.library_search_revealer)

        self.scrolled = Gtk.ScrolledWindow(); self.scrolled.set_vexpand(True)

        # Bottom navigation
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
        self._reader_content_box = content_box
        self.toolbar.set_content(content_box)
        self.split.set_content(self.toolbar)

        # WebKit fallback
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

        # Responsive behavior
        try:
            bp = Adw.Breakpoint()
            bp.set_condition("max-width: 400sp")
            bp.add_setter(self.split, "collapsed", True)
            self.add(bp)
        except Exception:
            def on_size_allocate(win, alloc):
                w = alloc.width
                collapsed = w < 400
                if getattr(self.split, "get_collapsed", None):
                    if self.split.get_collapsed() != collapsed:
                        self.split.set_collapsed(collapsed)
                else:
                    self.split.set_show_sidebar(not collapsed)
            self.connect("size-allocate", on_size_allocate)

        self._setup_responsive_sidebar()
        self._setup_window_size_constraints()

        # Start in library mode
        self.content_sidebar_toggle.set_visible(False)
        self.split.set_show_sidebar(False)
        self.split.set_collapsed(False)
        self.open_btn.set_visible(True)
        self.search_toggle_btn.set_visible(True)
        self.show_library()

    # ---- App action handlers ----
    def set_columns(self, n):
        try:
            n = int(n)
        except Exception:
            return
        self.column_mode_use_width = False
        self.column_count = max(1, min(10, n))
        try:
            self.display_page()
        except Exception:
            pass

    def set_column_width(self, w):
        try:
            w = int(w)
        except Exception:
            return
        self.column_mode_use_width = True
        self.column_width_px = max(50, min(500, w))
        try:
            self.display_page()
        except Exception:
            pass

    # ---- Search helpers ----
    def _toggle_library_search(self, *_):
        reveal = not self.library_search_revealer.get_reveal_child()
        self.library_search_revealer.set_reveal_child(reveal)
        if not reveal:
            if self._lib_search_handler_id:
                self.library_search_entry.handler_block(self._lib_search_handler_id)
            self.library_search_entry.set_text("")
            self.library_search_text = ""
            self.show_library()
            if self._lib_search_handler_id:
                self.library_search_entry.handler_unblock(self._lib_search_handler_id)
        else:
            self.library_search_entry.grab_focus()

    def _safe_set_search_text(self, text: str):
        if text is None:
            text = ""
        if self.library_search_entry.get_has_focus():
            return
        cur = self.library_search_entry.get_text() or ""
        if cur == text:
            return
        self.library_search_entry.set_text(text)
        pos = len(text)
        self.library_search_entry.set_position(pos)

    def _on_library_search_changed(self, arg):
        text = arg.get_text() if hasattr(arg, "get_text") else str(arg or "")
        self.library_search_text = (text or "").strip()
        self.show_library()

    # ---- Library helpers ----
    def _get_library_entries_for_display(self):
        entries = list(reversed(self.library))
        if not entries:
            return entries
        if self.book_path:
            for i, e in enumerate(entries):
                if os.path.abspath(e.get("path", "")) == os.path.abspath(self.book_path or ""):
                    if i != 0:
                        entries.insert(0, entries.pop(i))
                    break
        return entries

    def _is_loaded_entry(self, entry):
        if not entry or not self.book_path:
            return False
        return os.path.abspath(entry.get("path", "")) == os.path.abspath(self.book_path or "")

    def on_library_clicked(self, *_):
        if self.book:
            self.content_sidebar_toggle.set_visible(False)
            self.split.set_show_sidebar(False)
            self.split.set_collapsed(False)
        self.show_library()

    def _stop_reading(self, path=None):
        if path and self.book_path and os.path.abspath(path) != os.path.abspath(self.book_path):
            return
        try:
            self._save_progress_for_library()
            self.cleanup()
            self.book_path = None
            self.open_btn.set_visible(True)
            self.search_toggle_btn.set_visible(True)
            self.content_sidebar_toggle.set_visible(False)
            self.show_library()
        except Exception:
            pass

    def show_library(self):
        self._disable_responsive_sidebar()
        self.split.set_show_sidebar(False)
        self.content_sidebar_toggle.set_visible(False)
        self.open_btn.set_visible(True)
        self.search_toggle_btn.set_visible(True)
        self.library_search_revealer.set_reveal_child(bool(self.library_search_text))
        if self._lib_search_handler_id:
            self.library_search_entry.handler_block(self._lib_search_handler_id)
        self._safe_set_search_text(self.library_search_text)
        if self._lib_search_handler_id:
            self.library_search_entry.handler_unblock(self._lib_search_handler_id)

        self.columns_menu_button.set_visible(False)

        query = (self.library_search_text or "").strip().lower()
        entries = self._get_library_entries_for_display()
        if query:
            entries = [e for e in entries if query in (e.get("title") or "").lower() or query in (e.get("author") or "").lower() or query in (os.path.basename(e.get("path","")).lower())]

        if not entries:
            lbl = Gtk.Label(label="No books in library\nOpen a book to add it here.")
            lbl.set_justify(Gtk.Justification.CENTER); lbl.set_margin_top(40)
            self.toolbar.set_content(lbl); self.content_title_label.set_text("Library")
            return

        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_max_children_per_line(30)
        flowbox.set_min_children_per_line(2)
        flowbox.set_row_spacing(10)
        flowbox.set_column_spacing(10)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        flowbox.set_homogeneous(True)
        flowbox.add_css_class("library-grid")
        flowbox.set_margin_start(12)
        flowbox.set_margin_end(12)
        flowbox.set_margin_top(12)
        flowbox.set_margin_bottom(12)

        for entry in entries:
            title = entry.get("title") or os.path.basename(entry.get("path",""))
            author = entry.get("author") or ""
            cover = entry.get("cover")
            path = entry.get("path")
            idx = entry.get("index", 0)
            progress = entry.get("progress", 0.0)

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            card.add_css_class("library-card")
            card.set_size_request(160, 320)

            img = Gtk.Picture()
            img.set_size_request(140, 210)
            img.set_can_shrink(True)

            if cover and os.path.exists(cover):
                texture = create_rounded_cover_texture(cover, 140, 210, radius=10)
                if texture:
                    img.set_paintable(texture)
                else:
                    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 200)
                    pb.fill(0xddddddff)
                    img.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
            else:
                pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 200)
                pb.fill(0xddddddff)
                img.set_paintable(Gdk.Texture.new_for_pixbuf(pb))

            img.add_css_class("cover")
            img.set_halign(Gtk.Align.CENTER)
            card.append(img)

            t = Gtk.Label()
            t.add_css_class("title"); t.set_ellipsize(Pango.EllipsizeMode.END)
            t.set_wrap(True); t.set_max_width_chars(16); t.set_lines(2)
            t.set_halign(Gtk.Align.CENTER); t.set_justify(Gtk.Justification.CENTER)
            t.set_margin_top(4)
            t.set_margin_bottom(0)
            t.set_markup(highlight_markup(title, self.library_search_text))
            card.append(t)

            meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            meta_row.set_hexpand(True)
            meta_row.set_valign(Gtk.Align.CENTER)
            meta_row.set_margin_top(0)
            meta_row.set_margin_bottom(0)

            prog_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            prog_box.set_halign(Gtk.Align.START)
            prog_lbl = Gtk.Label()
            prog_lbl.add_css_class("meta")
            prog_lbl.set_valign(Gtk.Align.CENTER)
            prog_lbl.set_label(f"{int(progress*100)}%")
            prog_box.append(prog_lbl)
            meta_row.append(prog_box)

            author_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            author_box.set_hexpand(True)
            author_box.set_halign(Gtk.Align.CENTER)
            a = Gtk.Label()
            a.add_css_class("author")
            a.set_ellipsize(Pango.EllipsizeMode.END)
            a.set_max_width_chars(18)
            a.set_halign(Gtk.Align.CENTER)
            a.set_justify(Gtk.Justification.CENTER)
            a.set_markup(highlight_markup(author, self.library_search_text))
            author_box.append(a)
            meta_row.append(author_box)

            right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); right_box.set_halign(Gtk.Align.END)
            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic"); menu_btn.add_css_class("flat")
            pop = Gtk.Popover(); pop.set_has_arrow(False)
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            pop_box.set_margin_top(6); pop_box.set_margin_bottom(6); pop_box.set_margin_start(6); pop_box.set_margin_end(6)
            open_folder_btn = Gtk.Button(label="Open folder"); open_folder_btn.add_css_class("flat")
            rem_btn = Gtk.Button(label="Remove ebook"); rem_btn.add_css_class("flat")
            pop_box.append(open_folder_btn); pop_box.append(rem_btn)
            pop.set_child(pop_box); menu_btn.set_popover(pop)

            open_folder_btn.connect("clicked", lambda b, p=path: self._open_parent_folder(p))

            def _remove_entry(btn, p=path, coverp=cover):
                try:
                    dlg = Adw.MessageDialog.new(self, "Remove", f"Remove «{os.path.basename(p)}» from library?")
                    dlg.add_response("cancel", "Cancel"); dlg.add_response("ok", "Remove")
                    def _on_resp(d, resp):
                        if resp == "ok":
                            self.library = [ee for ee in self.library if ee.get("path") != p]
                            if coverp and os.path.exists(coverp) and os.path.commonpath([os.path.abspath(COVERS_DIR)]) == os.path.commonpath([os.path.abspath(COVERS_DIR), os.path.abspath(coverp)]):
                                os.remove(coverp)
                            save_library(self.library)
                            self.show_library()
                        d.destroy()
                    dlg.connect("response", _on_resp)
                    dlg.present()
                except Exception:
                    pass
            rem_btn.connect("clicked", _remove_entry)

            right_box.append(menu_btn); meta_row.append(right_box)
            card.append(meta_row)

            gesture = Gtk.GestureClick.new()
            def _on_click(_gesture, _n, _x, _y, p=path, resume_idx=idx):
                if p and os.path.exists(p):
                    self._save_progress_for_library()
                    self.cleanup()
                    self.toolbar.set_content(self._reader_content_box)
                    self.load_epub(p, resume=True, resume_index=resume_idx)
            gesture.connect("released", _on_click)
            card.add_controller(gesture)
            card.add_css_class("clickable")
            flowbox.append(card)

        scroll = Gtk.ScrolledWindow(); scroll.set_child(flowbox); scroll.set_vexpand(True); scroll.set_hexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); container.append(scroll)
        self.toolbar.set_content(container); self.content_title_label.set_text("Library")

    # ---- UI helpers ----
    def _setup_window_size_constraints(self):
        self._is_snapping = False
        self._snap_timeout_id = None
        self.connect("notify::default-width", self._on_window_width_changed)

    def _on_window_width_changed(self, *args):
        if self._responsive_enabled and self.book and self.book_path:
            return
        if self._snap_timeout_id:
            GLib.source_remove(self._snap_timeout_id)
        self._snap_timeout_id = GLib.timeout_add(200, self._snap_window_to_cards)

    def _snap_window_to_cards(self):
        self._snap_timeout_id = None
        if self._is_snapping:
            return False
        try:
            card_width = 160
            card_spacing = 10
            min_cards = 2
            max_cards = 8
            current_width = self.get_width()
            content_padding = 24
            available_width = current_width - content_padding
            cards_per_row = max(min_cards, int((available_width + card_spacing) / (card_width + card_spacing)))
            cards_per_row = min(cards_per_row, max_cards)
            ideal_content_width = (cards_per_row * card_width) + ((cards_per_row - 1) * card_spacing)
            ideal_window_width = ideal_content_width + content_padding
            if abs(current_width - ideal_window_width) > 20:
                self._is_snapping = True
                self.set_default_size(ideal_window_width, self.get_height())
                GLib.timeout_add(100, lambda: setattr(self, '_is_snapping', False))
        except Exception as e:
            print(f"Error snapping window: {e}")
        return False    

    def _setup_responsive_sidebar(self):
        self._responsive_enabled = False
        self._last_width = 0
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self.connect("notify::default-width", self._on_window_size_changed)

    def _on_sidebar_toggle(self, btn):
        new = not self.split.get_show_sidebar()
        self.split.set_show_sidebar(new)
        self._user_hid_sidebar = not new

    def _on_window_size_changed(self, *args):
        if self._user_hid_sidebar:
            return
        width = self.get_width()
        if abs(width - self._last_width) < 10:
            return
        self._last_width = width
        is_narrow = width < 768
        if is_narrow == self._last_was_narrow:
            return
        self._last_was_narrow = is_narrow
        if self._responsive_enabled and self.book and self.book_path:
            if is_narrow:
                self.split.set_collapsed(True)
            else:
                self.split.set_collapsed(False)
                self.split.set_show_sidebar(True)
        else:
            if self._last_was_narrow is not None:
                self.split.set_collapsed(False)
                self.split.set_show_sidebar(False)

    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self._on_window_size_changed()

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self.split.set_collapsed(False)
        self.split.set_show_sidebar(False)

    # ---- TOC setup/bind ----
    def _toc_on_setup(self, factory, list_item):
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0); hbox.set_hexpand(True)
        disc = Gtk.Image.new_from_icon_name("pan-end-symbolic"); disc.set_visible(False); hbox.append(disc)
        actrow = Adw.ActionRow(); actrow.set_activatable(True); actrow.set_title(""); actrow.set_hexpand(True); hbox.append(actrow)
        wrapper.append(hbox)
        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0); nested.set_margin_start(18); nested.set_visible(False)
        wrapper.append(nested)

        def _toggle_only():
            item = list_item.get_item()
            if not item: return
            if item.children.get_n_items() > 0:
                visible = not nested.get_visible()
                nested.set_visible(visible)
                disc.set_from_icon_name("pan-down-symbolic" if visible else "pan-end-symbolic")
                nv = getattr(list_item, "_nested_view", None)
                if nv: nv.set_visible(visible)

        g = Gtk.GestureClick(); g.connect("pressed", lambda *_: _toggle_only()); disc.add_controller(g)

        def _open_only(_):
            item = list_item.get_item()
            if not item: return
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

        actrow.connect("activated", _open_only)
        g2 = Gtk.GestureClick(); g2.connect("pressed", lambda *_: _open_only(None)); actrow.add_controller(g2)

        list_item.set_child(wrapper)
        list_item._hbox = hbox; list_item._disc = disc; list_item._actrow = actrow
        list_item._nested = nested; list_item._nested_view = None; list_item._bound_item = None

    def _toc_on_bind(self, factory, list_item):
        item = list_item.get_item()
        disc = getattr(list_item, "_disc", None); actrow = getattr(list_item, "_actrow", None); nested = getattr(list_item, "_nested", None)
        if disc is None or actrow is None or nested is None:
            self._toc_on_setup(factory, list_item)
            disc = list_item._disc; actrow = list_item._actrow; nested = list_item._nested

        prev = getattr(list_item, "_bound_item", None)
        if prev is not None and prev in self._toc_actrows:
            self._toc_actrows.pop(prev, None)
        list_item._bound_item = item

        if not item:
            actrow.set_title(""); disc.set_visible(False)
            nv = getattr(list_item, "_nested_view", None)
            if nv: nv.set_visible(False)
            return

        self._toc_actrows[item] = actrow
        actrow.remove_css_class("selected")

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
                ch_nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0); ch_nested.set_margin_start(18); ch_nested.set_visible(False)
                cwrap.append(ch_nested)

                def _toggle_child():
                    it = li.get_item()
                    if not it: return
                    if it.children.get_n_items() > 0:
                        vis = not ch_nested.get_visible()
                        ch_nested.set_visible(vis)
                        ch_disc.set_from_icon_name("pan-down-symbolic" if vis else "pan-end-symbolic")
                        gv = getattr(li, "_nested_view", None)
                        if gv: gv.set_visible(vis)

                gch = Gtk.GestureClick(); gch.connect("pressed", lambda *_: _toggle_child()); ch_disc.add_controller(gch)

                def _open_child(_):
                    it = li.get_item()
                    if not it: return
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

                ch_act.connect("activated", _open_child)
                gch2 = Gtk.GestureClick(); gch2.connect("pressed", lambda *_: _open_child(None)); ch_act.add_controller(gch2)

                li.set_child(cwrap)
                li._row = ch_act; li._disc = ch_disc; li._nested = ch_nested; li._nested_view = None; li._bound_item = None

            def child_bind(f, li):
                it = li.get_item()
                if not it: return
                ch_act = getattr(li, "_row", None); ch_disc = getattr(li, "_disc", None); ch_nested = getattr(li, "_nested", None)
                if ch_act is None or ch_disc is None or ch_nested is None: return
                prevc = getattr(li, "_bound_item", None)
                if prevc is not None and prevc in self._toc_actrows:
                    self._toc_actrows.pop(prevc, None)
                li._bound_item = it
                self._toc_actrows[it] = ch_act
                ch_act.remove_css_class("selected")
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
                    gv.set_vexpand(False); ch_nested.append(gv); li._nested_view = gv
                if getattr(li, "_nested_view", None):
                    li._nested_view.set_visible(ch_nested.get_visible())

            nfactory = Gtk.SignalListItemFactory()
            nfactory.connect("setup", child_setup); nfactory.connect("bind", child_bind)
            nsel = Gtk.NoSelection(model=item.children)
            nested_view = Gtk.ListView(model=nsel, factory=nfactory); nested_view.set_vexpand(False)
            nested.append(nested_view); list_item._nested_view = nested_view
            nested_view.set_visible(nested.get_visible())

        nv = getattr(list_item, "_nested_view", None)
        if nv: nv.set_visible(nested.get_visible())

    # ---- Selection helpers ----
    def _clear_toc_selection(self):
        for act in list(self._toc_actrows.values()):
            try: act.remove_css_class("selected")
            except Exception: pass

    def _set_toc_selected(self, toc_item):
        self._clear_toc_selection()
        act = self._toc_actrows.get(toc_item)
        if act: act.add_css_class("selected")

    # ---- Href registration ----
    def _register_href_variants(self, node: TocItem):
        if not node or not getattr(node, "href", None):
            return
        href = (node.href or "").strip()
        if not href:
            return
        keys = set()
        keys.add(href); keys.add(href.lstrip("./"))
        try:
            uq = urllib.parse.unquote(href); keys.add(uq); keys.add(uq.lstrip("./"))
        except Exception:
            pass
        b = os.path.basename(href)
        if b:
            keys.add(b)
            try: keys.add(urllib.parse.unquote(b))
            except Exception: pass
        if "#" in href:
            doc, frag = href.split("#", 1)
            if frag:
                keys.add(f"#{frag}"); keys.add(f"{os.path.basename(doc)}#{frag}")
                try: keys.add(f"{urllib.parse.unquote(os.path.basename(doc))}#{frag}")
                except Exception: pass
        try:
            if isinstance(node.index, int) and node.index >= 0 and node.index < len(self.items):
                it = self.items[node.index]
                iname = (it.get_name() or "").replace("\\", "/")
                if iname:
                    keys.add(iname); keys.add(os.path.basename(iname))
                    try:
                        keys.add(urllib.parse.unquote(iname)); keys.add(urllib.parse.unquote(os.path.basename(iname)))
                    except Exception:
                        pass
        except Exception:
            pass
        extras = set()
        for k in list(keys):
            for pfx in ("OEBPS/", "OPS/", "oebps/", "ops/"):
                extras.add(pfx + k)
        keys.update(extras)
        for k in keys:
            if not k:
                continue
            if k not in self.href_map:
                self.href_map[k] = node

    # ---- Load EPUB ----
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
                target = f.get_path()
                self._save_progress_for_library()
                self.cleanup()
                self.open_btn.set_visible(False)
                self._enable_sidebar_for_reading()
                self.load_epub(target)
        except GLib.Error:
            pass

    def _enable_sidebar_for_reading(self):
        self.content_sidebar_toggle.set_visible(True)
        self.content_sidebar_toggle.set_sensitive(True)
        self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
        self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
        self.open_btn.set_visible(False)
        self.search_toggle_btn.set_visible(False)
        self.columns_menu_button.set_visible(True)

    def load_epub(self, path, resume=False, resume_index=None):
        try:
            self.toolbar.set_content(self._reader_content_box)
            self._enable_responsive_sidebar()
            self._enable_sidebar_for_reading()
            self.open_btn.set_visible(False)
            self.search_toggle_btn.set_visible(False)
            self.library_search_revealer.set_reveal_child(False)
            self.cleanup()

            self.book_path = path
            self.book = epub.read_epub(path)
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
                iid = getattr(it, "id", None) or (it.get_id() if hasattr(it, "get_id") else None)
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
                self.show_error("No document items found in EPUB"); return

            self.temp_dir = tempfile.mkdtemp()
            extracted_paths = set()
            try:
                with zipfile.ZipFile(path, "r") as z:
                    z.extractall(self.temp_dir)
            except Exception:
                pass

            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path: continue
                sanitized_path = sanitize_path(item_path)
                if sanitized_path is None: continue
                full = os.path.join(self.temp_dir, sanitized_path)
                try:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as fh:
                        fh.write(item.get_content())
                    extracted_paths.add(sanitized_path.replace("\\", "/"))
                except OSError:
                    continue

            self._extracted_paths_map = {p.lower(): p for p in extracted_paths}
            image_items = list(self.book.get_items_of_type(ebooklib.ITEM_IMAGE))
            image_names = { (im.get_name() or "").replace("\\", "/"): im for im in image_items }
            image_basenames = {}
            for im in image_items:
                bn = os.path.basename((im.get_name() or "")).replace("\\", "/")
                if bn:
                    image_basenames.setdefault(bn, []).append(im)

            self.item_map = {it.get_name(): it for it in self.items}
            self.css_content = extract_css(self.book, self.temp_dir)

            title = APP_NAME; author = ""
            try:
                meta = self.book.get_metadata("DC", "title")
                if meta and meta[0]: title = meta[0][0]
                m2 = self.book.get_metadata("DC", "creator")
                if m2 and m2[0]: author = m2[0][0]
            except Exception:
                pass

            self.book_title.set_text(title); self.book_author.set_text(author)
            self.content_title_label.set_text(title); self.set_title(title or APP_NAME)

            cover_path_to_use = None; cover_item_obj = None
            cpath, citem = find_cover_via_opf(self.temp_dir, extracted_paths, image_names, image_basenames)
            if cpath: cover_path_to_use = cpath
            elif citem: cover_item_obj = citem

            if not cover_path_to_use and not cover_item_obj:
                priority_names = ("ops/cover.xhtml", "oebps/cover.xhtml", "ops/cover.html", "cover.xhtml", "cover.html", "ops/title.xhtml", "title.xhtml")
                docs_list = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
                lower_map = { (d.get_name() or "").lower(): d for d in docs_list }
                for pn in priority_names:
                    if pn in lower_map:
                        cover_doc = lower_map[pn]; break
                else:
                    cover_doc = None
                if cover_doc:
                    try:
                        soup = BeautifulSoup(cover_doc.get_content(), "html.parser")
                        doc_dir = os.path.dirname(cover_doc.get_name() or "")
                        srcs = []
                        img = soup.find("img", src=True)
                        if img: srcs.append(img["src"])
                        for svg_im in soup.find_all("image"):
                            if svg_im.has_attr("xlink:href"): srcs.append(svg_im["xlink:href"])
                            elif svg_im.has_attr("href"): srcs.append(svg_im["href"])
                        for src in srcs:
                            if not src: continue
                            src = src.split("#", 1)[0]; src = urllib.parse.unquote(src)
                            candidate_rel = os.path.normpath(os.path.join(doc_dir, src)).replace("\\", "/")
                            found = None
                            if candidate_rel.lower() in self._extracted_paths_map:
                                found = self._extracted_paths_map[candidate_rel.lower()]
                            elif os.path.basename(candidate_rel).lower() in self._extracted_paths_map:
                                found = self._extracted_paths_map[os.path.basename(candidate_rel).lower()]
                            if found:
                                cover_path_to_use = os.path.join(self.temp_dir, found); break
                    except Exception:
                        pass

            if not cover_path_to_use and not cover_item_obj:
                for im_name, im in image_names.items():
                    if "cover" in im_name.lower() or "cover" in os.path.basename(im_name).lower():
                        cover_item_obj = im; break

            if not cover_path_to_use and not cover_item_obj:
                for p in extracted_paths:
                    if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                        cover_path_to_use = os.path.join(self.temp_dir, p); break

            if cover_item_obj and not cover_path_to_use:
                iname = (cover_item_obj.get_name() or "").replace("\\", "/")
                for cand in (iname, os.path.basename(iname)):
                    if cand in extracted_paths:
                        cover_path_to_use = os.path.join(self.temp_dir, cand); break
                    for pfx in ("OEBPS/", "OPS/", "oebps/", "ops/"):
                        if (pfx + cand) in extracted_paths:
                            cover_path_to_use = os.path.join(self.temp_dir, pfx + cand); break
                    if cover_path_to_use: break

            if not cover_path_to_use and cover_item_obj:
                try:
                    raw = cover_item_obj.get_content()
                    if raw:
                        tmpfn = os.path.join(self.temp_dir, "cover_from_item_" + os.urandom(6).hex())
                        with open(tmpfn, "wb") as fh: fh.write(raw)
                        cover_path_to_use = tmpfn
                except Exception:
                    pass

            if cover_path_to_use and os.path.exists(cover_path_to_use):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover_path_to_use, COVER_W, COVER_H, True)
                    tex = Gdk.Texture.new_for_pixbuf(pix); self.cover_image.set_from_paintable(tex)
                    self.cover_image.set_size_request(COVER_W, COVER_H)
                    self.last_cover_path = cover_path_to_use
                except Exception:
                    self.last_cover_path = None; cover_path_to_use = None

            if not cover_path_to_use and not self.last_cover_path:
                placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
                placeholder_pb.fill(0xddddddff)
                placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
                self.cover_image.set_from_paintable(placeholder_tex)
                self.cover_image.set_size_request(COVER_W, COVER_H)

            self._populate_toc_tree()

            if getattr(self, "toc_root_store", None) and self.toc_root_store.get_n_items() > 0:
                self.split.set_show_sidebar(True)

            if resume:
                if isinstance(resume_index, int) and 0 <= resume_index < len(self.items):
                    self.current_index = resume_index
                else:
                    for e in self.library:
                        if e.get("path") == path:
                            self.current_index = int(e.get("index", 0)) if isinstance(e.get("index", 0), int) else 0
                            break
            else:
                self.current_index = 0

            self.update_navigation(); self.display_page()
            self.library = update_library_entry(self.library, self.book_path, title, author, self.last_cover_path, self.current_index, self.progress.get_fraction())
            save_library(self.library)

        except Exception:
            print(traceback.format_exc()); self.show_error("Error loading EPUB — see console")

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
            try: self._register_href_variants(node)
            except Exception: pass
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
                            if child_ol: walk_list(child_ol, node.children)
                    ol = toc_nav.find("ol")
                    if ol:
                        walk_list(ol, root)
                        self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                        self.toc_listview.set_model(self.toc_sel); return
        except Exception:
            pass

        try:
            ncx_item = self.book.get_item_with_id("ncx")
            if ncx_item:
                soup = BeautifulSoup(ncx_item.get_content(), "xml")
                def walk_navpoints(parent, parent_store):
                    for np in parent.find_all("navPoint", recursive=False):
                        text_tag = np.find("text"); content_tag = np.find("content")
                        title = text_tag.get_text(strip=True) if text_tag else ""
                        href = content_tag["src"] if content_tag and content_tag.has_attr("src") else ""
                        node = add_node(title or os.path.basename(href), href or "", parent_store)
                        walk_navpoints(np, node.children)
                navmap = soup.find("navMap")
                if navmap:
                    walk_navpoints(navmap, root)
                    self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                    self.toc_listview.set_model(self.toc_sel); return
        except Exception:
            pass

        for i, it in enumerate(self.items):
            title = os.path.basename(it.get_name())
            add_node(title, it.get_name(), root)

        self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview.set_model(self.toc_sel)

    def on_decide_policy(self, webview, decision, decision_type):
        if not self.WebKit: return False
        if decision_type == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            try:
                nav_action = decision.get_navigation_action()
                request = nav_action.get_request() if hasattr(nav_action, 'get_request') else decision.get_request()
                uri = request.get_uri() if request else None
            except Exception as e:
                print(f"Error getting URI from decision: {e}"); return False
            if not uri: return False
            if uri in ("", "about:blank", "file://"): return False
            if uri.startswith("http://") or uri.startswith("https://"):
                try: decision.ignore()
                except Exception: pass
                return True
            if uri.startswith("file://"):
                current_uri = webview.get_uri()
                if current_uri and current_uri == uri: return False
                if self.handle_internal_link(uri):
                    try: decision.ignore()
                    except Exception: pass
                    return True
        return False

    def _find_tocitem_for_candidates(self, candidates, fragment=None):
        for c in candidates:
            if not c: continue
            t = self.href_map.get(c)
            if t: return t
            bn = os.path.basename(c)
            t = self.href_map.get(bn)
            if t: return t
        if fragment:
            frag_keys = [f"#{fragment}", fragment, os.path.basename(fragment)]
            for fk in frag_keys:
                t = self.href_map.get(fk)
                if t: return t
        return None

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

        toc_match = self._find_tocitem_for_candidates(candidates, fragment)
        if toc_match:
            if isinstance(toc_match.index, int) and toc_match.index >= 0:
                self.current_index = toc_match.index; self.update_navigation()
                frag = fragment or (toc_match.href.split("#", 1)[1] if "#" in (toc_match.href or "") else None)
                self.display_page(fragment=frag); return True
            else:
                href = toc_match.href or ""
                candidate_path = None
                try:
                    candidate_path = os.path.join(self.temp_dir or "", urllib.parse.unquote(href.split("#", 1)[0]))
                except Exception:
                    pass
                if candidate_path and os.path.exists(candidate_path):
                    return self._load_file_with_css(candidate_path, fragment)
                self._set_toc_selected(toc_match); return True

        for cand in candidates:
            if cand in self.item_map:
                for i, it in enumerate(self.items):
                    if it.get_name() == cand:
                        self.current_index = i; self.update_navigation(); self.display_page(fragment=fragment)
                        for ti in list(self.href_map.values()):
                            if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == i:
                                self._set_toc_selected(ti); break
                        return True

        possible_paths = []
        if self.temp_dir:
            possible_paths.append(os.path.join(self.temp_dir, rel))
            possible_paths.append(os.path.join(self.temp_dir, os.path.basename(rel)))
        possible_paths.append(path)

        for p in possible_paths:
            if not p: continue
            if os.path.exists(p):
                return self._load_file_with_css(p, fragment)

        return False

    def _load_file_with_css(self, file_path, fragment=None):
        if not os.path.exists(file_path): return False
        if not self.css_content: self.css_content = extract_css(self.book, self.temp_dir)
        ext = os.path.splitext(file_path)[1].lower()
        base_uri = "file://" + (os.path.dirname(file_path) or "/") + "/"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            img_uri = "file://" + file_path
            raw = f'<div style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;"><img src="{img_uri}" alt="image" style="max-width:100%;height:auto;"/></div>'
            html = wrap_html(raw, base_uri, self.css_content, self.column_mode_use_width, self.column_count, self.column_width_px, self._column_gap)
            if self.webview:
                self.webview.load_html(html, base_uri)
            else:
                self.textview.get_buffer().set_text(f"[Image] {file_path}")
            return True

        if ext in (".html", ".xhtml", ".htm"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh: content = fh.read()
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup.find_all(['style', 'link']): tag.decompose()
                body = soup.find("body")
                if body:
                    body_attrs = ' '.join([f'{k}="{v}"' if isinstance(v, str) else f'{k}="{" ".join(v)}"' for k, v in body.attrs.items()])
                    if body_attrs:
                        body_content = f'<div {body_attrs}>{"".join(str(child) for child in body.children)}</div>'
                    else:
                        body_content = "".join(str(child) for child in body.children)
                else:
                    body_content = str(soup)
                html_content = wrap_html(body_content, base_uri, self.css_content, self.column_mode_use_width, self.column_count, self.column_width_px, self._column_gap)
                if self.webview:
                    self.webview.load_html(html_content, base_uri)
                    if fragment: GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                else:
                    self.textview.get_buffer().set_text(soup.get_text())
                return True
            except Exception as e:
                print(f"Error loading HTML file {file_path}: {e}"); return False
        return False

    def display_page(self, fragment=None):
        if not self.book or not self.items or self.current_index >= len(self.items): return
        if not self.css_content: self.css_content = extract_css(self.book, self.temp_dir)
        item = self.items[self.current_index]
        if not item or not hasattr(item, 'get_content'): return

        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            for tag in soup.find_all(['style', 'link']): tag.decompose()
            body = soup.find("body")
            if body:
                body_attrs = ' '.join([f'{k}="{v}"' if isinstance(v, str) else f'{k}="{" ".join(v)}"' for k, v in body.attrs.items()])
                if body_attrs:
                    content = f'<div {body_attrs}>{"".join(str(child) for child in body.children)}</div>'
                else:
                    content = "".join(str(child) for child in body.children)
            else:
                content = str(soup)

            base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
            wrapped_html = wrap_html(content, base_uri, self.css_content, self.column_mode_use_width, self.column_count, self.column_width_px, self._column_gap)

            if self.webview:
                self.webview.load_html(wrapped_html, base_uri)
                if fragment: GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
            else:
                buf = self.textview.get_buffer(); buf.set_text(soup.get_text())

            total = len(self.items)
            self.progress.set_fraction((self.current_index + 1) / total)
            self.progress.set_text(f"{self.current_index + 1}/{total}")

            for ti in list(self.href_map.values()):
                if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == self.current_index:
                    self._set_toc_selected(ti); break

            self._save_progress_for_library()

        except Exception as e:
            print(f"Error displaying page: {e}"); self.show_error(f"Error displaying page: {e}")

    def _scroll_to_fragment(self, fragment):
        if self.webview and fragment:
            js_code = f"var element = document.getElementById('{fragment}'); if (element) {{ element.scrollIntoView({{behavior:'smooth', block:'start'}}); }}"
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception:
                try: self.webview.run_javascript(js_code, None, None, None)
                except Exception: pass
        return False

    # ---- Navigation ----
    def update_navigation(self):
        total = len(self.items) if hasattr(self, "items") and self.items else 0
        self.prev_btn.set_sensitive(getattr(self, "current_index", 0) > 0)
        self.next_btn.set_sensitive(getattr(self, "current_index", 0) < total - 1)

    def next_page(self, button):
        if self.current_index < len(self.items) - 1:
            self.current_index += 1; self.update_navigation(); self.display_page(); self._save_progress_for_library()

    def prev_page(self, button):
        if self.current_index > 0:
            self.current_index -= 1; self.update_navigation(); self.display_page(); self._save_progress_for_library()

    # ---- Cleanup & error ----
    def show_error(self, message):
        try:
            dialog = Adw.MessageDialog.new(self, "Error", message); dialog.add_response("ok", "OK"); dialog.present()
        except Exception:
            print("Error dialog:", message)

    def cleanup(self):
        if getattr(self, "temp_dir", None) and os.path.exists(self.temp_dir):
            try: shutil.rmtree(self.temp_dir)
            except Exception as e: print(f"Error cleaning up temp directory: {e}")
        self.temp_dir = None; self.book = None; self.items = []; self.item_map = {}; self.css_content = ""; self.current_index = 0

        if getattr(self, "toc_root_store", None):
            self.toc_root_store = Gio.ListStore(item_type=TocItem); self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
            self.toc_listview.set_model(self.toc_sel)

        self._toc_actrows = {}; self.href_map = {}
        self.update_navigation()

        if self.webview:
            try: blank = wrap_html("", "", "", False, 1, 200, 32); self.webview.load_html(blank, "")
            except Exception: pass
        elif hasattr(self, 'textview'):
            try: self.textview.get_buffer().set_text("")
            except Exception: pass

        self.book_title.set_text(""); self.book_author.set_text("")
        placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
        placeholder_pb.fill(0xddddddff)
        self.cover_image.set_from_paintable(Gdk.Texture.new_for_pixbuf(placeholder_pb))

        self.content_sidebar_toggle.set_visible(True)
        self.open_btn.set_visible(False)
        self.search_toggle_btn.set_visible(False)
        self.library_search_revealer.set_reveal_child(False)

    def _save_progress_for_library(self):
        if not self.book_path: return
        changed = False
        for e in self.library:
            if e.get("path") == self.book_path:
                e["index"] = int(self.current_index); e["progress"] = float(self.progress.get_fraction() or 0.0)
                changed = True; break
        if changed: save_library(self.library)

    def _open_parent_folder(self, path):
        try:
            if not path: return
            parent = os.path.dirname(path) or path
            uri = GLib.filename_to_uri(parent, None)
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            pass
