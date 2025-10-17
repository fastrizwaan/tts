#!/usr/bin/env python3
# EPUB viewer with libadwaita + GTK4 ListView sidebar TOC (nested, clickable)
# Library view (persistent), saves scaled covers, remembers progress, grid of recent books.
# Changes:
# - start with library and hidden sidebar
# - library button in sidebar header, open button in content header (swapped)
# - clicking library keeps opened book in memory and shows a back icon button in place of sidebar toggle
# - compact confirmation when removing ebook + "Open folder"
# - professional prompt when opening new epub while one is loaded
# - toggle sidebar disabled on startup library view; enabled when a book is opened
import gi, os, tempfile, traceback, shutil, urllib.parse, glob, re, json, hashlib
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

# cover target size for sidebar (small)
COVER_W, COVER_H = 70, 100

# persistent library locations & library cover save size
LIBRARY_DIR = os.path.join(GLib.get_user_data_dir(), "epubviewer")
LIBRARY_FILE = os.path.join(LIBRARY_DIR, "library.json")
COVERS_DIR = os.path.join(LIBRARY_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)

# persistent cover saved size (larger)
LIB_COVER_W, LIB_COVER_H = 200, 300

def _ensure_library_dir():
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)

def load_library():
    _ensure_library_dir()
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return []
    return []

def save_library(data):
    _ensure_library_dir()
    try:
        with open(LIBRARY_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Error saving library:", e)

# CSS: small sidebar tweaks + library author dim/center
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
.book-title { font-weight: 600; margin-bottom: 2px; }
.book-author { color: rgba(0,0,0,0.6); font-size: 12px; }

/* library card author dim + centered */
.library-card .author {
  color: rgba(0,0,0,0.55);
  font-size: 11px;
  text-align: center;
}
.dark .library-card .author {
  color: rgba(255,255,255,0.65);
}
"""
_css_provider = Gtk.CssProvider()
_css_provider.load_from_data(_css.encode("utf-8"))
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _css_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
)

_LIBRARY_CSS = b"""
.library-grid { padding: 12px; }
.library-card {
  background-color: @card_bg_color;
  border-radius: 10px;
  padding: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  transition: all 200ms ease;
  border: 1px solid rgba(0,0,0,0.08);
}
.library-card:hover { box-shadow: 0 6px 16px rgba(0,0,0,0.15); transform: translateY(-2px); }
.library-card .cover { margin-bottom: 6px; border-radius: 6px; }
.library-card .title { font-weight:600; font-size:12px; line-height:1.2; color:@theme_fg_color; }
.library-card .author { font-size:10px; opacity:0.7; color:@theme_fg_color; }
.library-card .meta { font-size:9px; font-weight:500; opacity:0.6; color:@theme_fg_color; }
.dark .library-card { background-color: alpha(@theme_fg_color, 0.05); box-shadow: 0 2px 8px rgba(255,255,255,0.05); border:1px solid alpha(@theme_fg_color,0.1); }
"""
_cssp = Gtk.CssProvider()
_cssp.load_from_data(_LIBRARY_CSS)
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(), _cssp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
)

THEME_INJECTION_CSS = """
@media (prefers-color-scheme: dark) {
    body { background-color:#242424; color:#e3e3e3; }
    blockquote { border-left-color:#62a0ea; }
    .tts-highlight { background:rgba(0,127,0,0.75); box-shadow:0 0 0 2px rgba(0,127,0,0.75); }
}
"""
_dark_override_css = """
.epub-sidebar .book-author { color: rgba(255,255,255,0.6); }
"""
_dark_provider = Gtk.CssProvider()
_dark_provider.load_from_data(_dark_override_css.encode("utf-8"))
settings = Gtk.Settings.get_default()
def _update_gtk_dark_provider(settings, pspec=None):
    try:
        if settings.get_property("gtk-application-prefer-dark-theme"):
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _dark_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        else:
            Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), _dark_provider)
    except Exception:
        pass
try: settings.connect("notify::gtk-application-prefer-dark-theme", _update_gtk_dark_provider)
except Exception: pass
_update_gtk_dark_provider(settings)

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

        # library
        self.library = load_library()

        # UI skeleton
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_vbox)

        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.32)
        main_vbox.append(self.split)

        # Sidebar
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.add_css_class("epub-sidebar")

        header = Adw.HeaderBar(); header.add_css_class("flat")
        # library button in sidebar header (requirement)
        self.library_btn = Gtk.Button(icon_name="view-grid-symbolic"); self.library_btn.add_css_class("flat")
        self.library_btn.set_tooltip_text("Show Library"); self.library_btn.connect("clicked", lambda *_: self.on_library_clicked())
        header.pack_start(self.library_btn)

        title_lbl = Gtk.Label(label=APP_NAME); title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_title_widget(title_lbl)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_revealer = Gtk.Revealer(reveal_child=False)
        self.search_entry = Gtk.SearchEntry(); self.search_entry.set_placeholder_text("Search TOC")
        self.search_entry.connect("search-changed", lambda e: self._filter_toc(e.get_text()))
        self.search_revealer.set_child(self.search_entry)
        search_btn = Gtk.Button(icon_name="system-search-symbolic"); search_btn.set_tooltip_text("Show search")
        search_btn.connect("clicked", lambda *_: self.search_revealer.set_reveal_child(not self.search_revealer.get_reveal_child()))
        btn_box.append(search_btn)
        menu_model = Gio.Menu(); menu_model.append("About", "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic"); menu_btn.set_popover(Gtk.PopoverMenu.new_from_model(menu_model))
        btn_box.append(menu_btn)
        app.create_action("about", lambda a, p: self.show_error("EPUB Viewer — minimal menu"))
        header.pack_end(btn_box)
        sidebar_box.append(header)
        sidebar_box.append(self.search_revealer)

        # cover + metadata
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        book_box.set_valign(Gtk.Align.START)
        book_box.set_margin_top(6); book_box.set_margin_bottom(6); book_box.set_margin_start(8); book_box.set_margin_end(8)
        self.cover_image = Gtk.Image()
        placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
        placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
        self.cover_image.set_from_paintable(placeholder_tex)
        try:
            self.cover_image.set_valign(Gtk.Align.START); self.cover_image.set_halign(Gtk.Align.START); self.cover_image.set_size_request(COVER_W, COVER_H)
        except Exception: pass
        book_box.append(self.cover_image)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4); text_box.set_valign(Gtk.Align.CENTER)
        self.book_title = Gtk.Label(label=""); self.book_title.add_css_class("book-title")
        self.book_title.set_halign(Gtk.Align.START); self.book_title.set_xalign(0.0); self.book_title.set_max_width_chars(18)
        self.book_title.set_wrap(True); self.book_title.set_lines(2); self.book_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_author = Gtk.Label(label=""); self.book_author.add_css_class("book-author")
        self.book_author.set_halign(Gtk.Align.START); self.book_author.set_xalign(0.0)
        text_box.append(self.book_title); text_box.append(self.book_author); book_box.append(text_box)
        sidebar_box.append(book_box)

        # side stack: TOC / annotations / bookmarks
        self.side_stack = Gtk.Stack(); self.side_stack.set_vexpand(True)
        self.toc_factory = Gtk.SignalListItemFactory(); self.toc_factory.connect("setup", self._toc_on_setup); self.toc_factory.connect("bind", self._toc_on_bind)
        self.toc_root_store = Gio.ListStore(item_type=TocItem); self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview = Gtk.ListView(model=self.toc_sel, factory=self.toc_factory); self.toc_listview.set_vexpand(True)
        toc_scrolled = Gtk.ScrolledWindow(); toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); toc_scrolled.set_vexpand(True); toc_scrolled.set_child(self.toc_listview)
        self.side_stack.add_titled(toc_scrolled, "toc", "TOC")
        ann_list = Gtk.ListBox(); ann_list.append(Gtk.Label(label="No annotations"))
        ann_scrolled = Gtk.ScrolledWindow(); ann_scrolled.set_child(ann_list); self.side_stack.add_titled(ann_scrolled, "annotations", "Annotations")
        bm_list = Gtk.ListBox(); bm_list.append(Gtk.Label(label="No bookmarks"))
        bm_scrolled = Gtk.ScrolledWindow(); bm_scrolled.set_child(bm_list); self.side_stack.add_titled(bm_scrolled, "bookmarks", "Bookmarks")
        sidebar_box.append(self.side_stack)

        # bottom tabs
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs_box.set_margin_top(6); tabs_box.set_margin_bottom(6); tabs_box.set_margin_start(6); tabs_box.set_margin_end(6)
        def make_icon_tab(icon_name, tooltip, name):
            b = Gtk.ToggleButton(); b.add_css_class("flat"); img = Gtk.Image.new_from_icon_name(icon_name); b.set_child(img)
            b.set_tooltip_text(tooltip); b.set_hexpand(True); self._tab_buttons.append((b, name))
            def on_toggled(btn, nm=name):
                if btn.get_active():
                    for sib, _nm in self._tab_buttons:
                        if sib is not btn:
                            try: sib.set_active(False)
                            except Exception: pass
                    self.side_stack.set_visible_child_name(nm)
            b.connect("toggled", on_toggled); return b
        self.tab_toc = make_icon_tab("view-list-symbolic", "TOC", "toc"); self.tab_ann = make_icon_tab("document-edit-symbolic", "Annotations", "annotations")
        self.tab_bm  = make_icon_tab("user-bookmarks-symbolic", "Bookmarks", "bookmarks")
        self.tab_toc.set_active(True)
        tabs_box.append(self.tab_toc); tabs_box.append(self.tab_ann); tabs_box.append(self.tab_bm)
        sidebar_box.append(tabs_box)

        self.split.set_sidebar(sidebar_box)
        # hide sidebar on fresh launch (requirement)
        try: self.split.set_show_sidebar(False)
        except Exception: pass

        # Content area
        self.toolbar = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar(); self.content_header.add_css_class("flat")

        # sidebar toggle button (single handler that can act as toggle or back)
        self.content_sidebar_toggle = Gtk.Button(); self.content_sidebar_toggle.add_css_class("flat")
        self._sidebar_img = Gtk.Image.new_from_icon_name("sidebar-show-symbolic")
        self.content_sidebar_toggle.set_child(self._sidebar_img)
        self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
        self._sidebar_is_back = False  # when True, button works as "back/resume" instead of toggle
        self.content_sidebar_toggle.connect("clicked", self._sidebar_button_clicked)
        self.content_header.pack_start(self.content_sidebar_toggle)

        # open button in content header (swapped)
        open_btn = Gtk.Button(icon_name="document-open-symbolic"); open_btn.set_tooltip_text("Open EPUB"); open_btn.connect("clicked", self.open_file); open_btn.add_css_class("flat")
        self.content_header.pack_start(open_btn)

        self.content_title_label = Gtk.Label(label=APP_NAME); self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END); self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)
        self.toolbar.add_top_bar(self.content_header)

        self.scrolled = Gtk.ScrolledWindow(); self.scrolled.set_vexpand(True)

        # bottom nav
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6); bottom_bar.set_margin_bottom(6); bottom_bar.set_margin_start(6); bottom_bar.set_margin_end(6)
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic"); self.prev_btn.add_css_class("flat"); self.prev_btn.set_sensitive(False); self.prev_btn.connect("clicked", self.prev_page)
        bottom_bar.append(self.prev_btn)
        self.progress = Gtk.ProgressBar(); self.progress.set_show_text(True); self.progress.set_hexpand(True)
        bottom_bar.append(self.progress)
        self.next_btn = Gtk.Button(icon_name="go-next-symbolic"); self.next_btn.add_css_class("flat"); self.next_btn.set_sensitive(False); self.next_btn.connect("clicked", self.next_page)
        bottom_bar.append(self.next_btn)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); content_box.set_vexpand(True)
        content_box.append(self.scrolled); content_box.append(bottom_bar)
        self._reader_content_box = content_box
        self.toolbar.set_content(content_box)
        self.split.set_content(self.toolbar)

        # WebKit fallback
        try:
            gi.require_version("WebKit", "6.0"); from gi.repository import WebKit
            self.WebKit = WebKit; self.webview = WebKit.WebView(); self.scrolled.set_child(self.webview); self.webview.connect("decide-policy", self.on_decide_policy)
        except Exception:
            self.WebKit = None; self.webview = None
            self.textview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)

        # responsive
        try:
            bp = Adw.Breakpoint()
            try: bp.set_condition("max-width: 400sp")
            except Exception: pass
            try: bp.add_setter(self.split, "collapsed", True)
            except Exception: pass
            try: self.add(bp)
            except Exception: pass
        except Exception:
            def on_size_allocate(win, alloc):
                try:
                    w = alloc.width; collapsed = w < 400
                    if getattr(self.split, "get_collapsed", None):
                        if self.split.get_collapsed() != collapsed:
                            self.split.set_collapsed(collapsed)
                    else:
                        self.split.set_show_sidebar(not collapsed)
                except Exception: pass
            self.connect("size-allocate", on_size_allocate)

        # start with library visible; disable toggle (no TOC)
        self.content_sidebar_toggle.set_sensitive(False)
        self.show_library()

    # ---------- sidebar button clicked handler (toggle or back) ----------
    def _sidebar_button_clicked(self, btn):
        try:
            if getattr(self, "_sidebar_is_back", False):
                # act as resume/back
                self.resume_book()
            else:
                # normal toggle
                new = not self.split.get_show_sidebar()
                try: self.split.set_show_sidebar(new)
                except Exception: pass
        except Exception:
            pass

    # ---------- Library UI ----------
    def show_library(self):
        wrap = Adw.WrapBox(); wrap.set_align(Gtk.Align.FILL); wrap.set_pack_direction(Gtk.Orientation.HORIZONTAL)
        wrap.set_child_spacing(10); wrap.set_line_spacing(10); wrap.add_css_class("library-grid")
        wrap.set_margin_start(0); wrap.set_margin_end(0); wrap.set_margin_top(0); wrap.set_margin_bottom(0)

        # ensure sidebar hidden when showing library
        try: self.split.set_show_sidebar(False)
        except Exception: pass

        # if a book is loaded, put back icon in sidebar-toggle place (and make it behave as back)
        if getattr(self, "book", None):
            try:
                self._sidebar_is_back = True
                self._sidebar_img.set_from_icon_name("edit-undo-symbolic")
                self.content_sidebar_toggle.set_tooltip_text("Return to current book")
                self.content_sidebar_toggle.set_sensitive(True)
            except Exception:
                pass
        else:
            # no book loaded: normal toggle disabled (no TOC)
            try:
                self._sidebar_is_back = False
                self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
                self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
                self.content_sidebar_toggle.set_sensitive(False)
            except Exception:
                pass

        # ensure resume button hidden because we use sidebar button as back
        if getattr(self, "_resume_btn", None):
            try: self._resume_btn.hide()
            except Exception: pass

        if not self.library:
            lbl = Gtk.Label(label="No books in library\nOpen a book to add it here.")
            lbl.set_justify(Gtk.Justification.CENTER); lbl.set_margin_top(40)
            self.toolbar.set_content(lbl); self.content_title_label.set_text("Library"); return

        for entry in list(reversed(self.library)):
            title = entry.get("title") or os.path.basename(entry.get("path",""))
            author = entry.get("author") or ""
            cover = entry.get("cover")
            path = entry.get("path")
            idx = entry.get("index", 0)
            progress = entry.get("progress", 0.0)

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); card.add_css_class("library-card")
            card.set_size_request(160, 160); card.set_hexpand(False); card.set_vexpand(False)

            img = Gtk.Image(); img.set_size_request(160, 160)
            if cover and os.path.exists(cover):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover, 180, 200, True); tex = Gdk.Texture.new_for_pixbuf(pix); img.set_from_paintable(tex)
                except Exception:
                    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 180, 200); pb.fill(0xddddddff); img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pb))
            else:
                pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 180, 200); pb.fill(0xddddddff); img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pb))
            img.add_css_class("cover"); img.set_halign(Gtk.Align.CENTER); card.append(img)

            t = Gtk.Label(label=title); t.add_css_class("title"); t.set_ellipsize(Pango.EllipsizeMode.END)
            t.set_wrap(True); t.set_max_width_chars(16); t.set_lines(2); t.set_halign(Gtk.Align.CENTER); t.set_justify(Gtk.Justification.CENTER)
            card.append(t)

            # meta row: progress (left), author (center), menu (right)
            meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6); meta_row.set_hexpand(True); meta_row.set_valign(Gtk.Align.CENTER)

            prog_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); prog_box.set_halign(Gtk.Align.START)
            prog_lbl = Gtk.Label(label=f"{int(progress*100)}%"); prog_lbl.add_css_class("meta"); prog_lbl.set_valign(Gtk.Align.CENTER); prog_box.append(prog_lbl)
            meta_row.append(prog_box)

            author_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); author_box.set_hexpand(True); author_box.set_halign(Gtk.Align.CENTER)
            a = Gtk.Label(label=author); a.add_css_class("author"); a.set_ellipsize(Pango.EllipsizeMode.END); a.set_max_width_chars(18)
            a.set_halign(Gtk.Align.CENTER); a.set_justify(Gtk.Justification.CENTER); author_box.append(a)
            meta_row.append(author_box)

            right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); right_box.set_halign(Gtk.Align.END)
            # menu with Open folder + Remove
            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic"); menu_btn.add_css_class("flat")
            pop = Gtk.Popover(); pop.set_has_arrow(False)
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            pop_box.set_margin_top(6); pop_box.set_margin_bottom(6); pop_box.set_margin_start(6); pop_box.set_margin_end(6)
            open_folder_btn = Gtk.Button(label="Open folder"); open_folder_btn.add_css_class("flat"); pop_box.append(open_folder_btn)
            rem_btn = Gtk.Button(label="Remove ebook"); rem_btn.add_css_class("flat"); pop_box.append(rem_btn)
            pop.set_child(pop_box); menu_btn.set_popover(pop)
            # open folder
            open_folder_btn.connect("clicked", lambda b, p=path: self._open_parent_folder(p))
            # compact confirmation remove
            def _confirm_remove(btn, p=path, coverp=cover):
                try:
                    dlg = Adw.MessageDialog.new(self, "Remove", f"Remove «{os.path.basename(p)}»?")
                    dlg.add_response("cancel", "Cancel"); dlg.add_response("ok", "Remove")
                    def _on_resp(d, resp):
                        try:
                            if resp == "ok":
                                self.library = [ee for ee in self.library if ee.get("path") != p]
                                try:
                                    if coverp and os.path.exists(coverp) and os.path.commonpath([os.path.abspath(COVERS_DIR)]) == os.path.commonpath([os.path.abspath(COVERS_DIR), os.path.abspath(coverp)]):
                                        os.remove(coverp)
                                except Exception:
                                    pass
                                save_library(self.library)
                                self.show_library()
                        finally:
                            try: d.destroy()
                            except Exception: pass
                    dlg.connect("response", _on_resp); dlg.present()
                except Exception:
                    pass
            rem_btn.connect("clicked", _confirm_remove)

            right_box.append(menu_btn)
            meta_row.append(right_box)
            card.append(meta_row)

            # clickable card: open book (restore reader content)
            gesture = Gtk.GestureClick.new()
            def _on_click(_gesture, _n, _x, _y, p=path, resume_idx=idx):
                if p and os.path.exists(p):
                    # If another epub is loaded, confirm replacement; else open directly
                    def do_open():
                        # ensure reader content replaces library immediately
                        try: self.toolbar.set_content(self._reader_content_box)
                        except Exception: pass
                        # when user chooses to open, we want to ensure toggle becomes active
                        self.load_epub(p, resume=True, resume_index=resume_idx)

                    if getattr(self, "book", None) and os.path.abspath(self.book_path or "") != os.path.abspath(p):
                        self._confirm_close_current_then(lambda: do_open())
                    else:
                        do_open()
            gesture.connect("released", _on_click)
            card.add_controller(gesture)
            card.add_css_class("clickable")
            wrap.append(card)

        scroll = Gtk.ScrolledWindow(); scroll.set_child(wrap); scroll.set_vexpand(True); scroll.set_hexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); container.append(scroll)
        self.toolbar.set_content(container); self.content_title_label.set_text("Library")

    # ---------- helpers: open parent folder, resume ----------
    def _open_parent_folder(self, path):
        try:
            if not path: return
            parent = os.path.dirname(path) or path
            uri = GLib.filename_to_uri(parent, None)
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            pass

    def resume_book(self, *_):
        try:
            # restore reader content and title & sidebar if TOC exists
            self.toolbar.set_content(self._reader_content_box)
            self.content_title_label.set_text(self.book_title.get_text() or APP_NAME)
            try:
                if getattr(self, "toc_root_store", None) and self.toc_root_store.get_n_items() > 0:
                    # restore sidebar button to normal toggle
                    self._sidebar_is_back = False
                    self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
                    self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
                    self.split.set_show_sidebar(True)
                else:
                    # If no TOC, keep sidebar hidden and keep toggle enabled (user can show if desired)
                    self._sidebar_is_back = False
                    self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
                    self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
                    self.content_sidebar_toggle.set_sensitive(True)
            except Exception: pass
            # hide any resume button if present
            if getattr(self, "_resume_btn", None):
                try: self._resume_btn.hide()
                except Exception: pass
        except Exception:
            pass

    # ---------- if opening a new epub while one is loaded: confirm professional prompt ----------
    def _confirm_close_current_then(self, continue_callback):
        try:
            title = self.book_title.get_text() or os.path.basename(self.book_path or "")
            msg = f"An EPUB is currently open — “{title}”. Opening another book will close the current one and return the library to its saved state. Do you want to continue?"
            dlg = Adw.MessageDialog.new(self, "Open another EPUB?", msg)
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Continue")
            def _on_resp(d, resp):
                try:
                    if resp == "ok":
                        # save progress/state then cleanup and continue
                        try: self._save_progress_for_library()
                        except Exception: pass
                        # cleanup temporary extraction but keep library intact
                        try: self.cleanup()
                        except Exception: pass
                        # allow sidebar toggle re-enabled after actual open
                        continue_callback()
                finally:
                    try: d.destroy()
                    except Exception: pass
            dlg.connect("response", _on_resp)
            dlg.present()
        except Exception:
            # fallback: proceed without prompt
            try:
                self._save_progress_for_library()
                self.cleanup()
                continue_callback()
            except Exception:
                pass

    # ---------- UI helpers ----------
    def _filter_toc(self, text):
        self.tab_toc.set_active(True); return

    # ---------- TOC ListView setup/bind (kept original logic) ----------
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
                visible = not nested.get_visible(); nested.set_visible(visible)
                disc.set_from_icon_name("pan-down-symbolic" if visible else "pan-end-symbolic")
                nv = getattr(list_item, "_nested_view", None)
                if nv: nv.set_visible(visible)
        g = Gtk.GestureClick(); g.connect("pressed", lambda *_: _toggle_only()); disc.add_controller(g)
        def _open_only(_):
            item = list_item.get_item()
            if not item: return
            href = item.href or ""; fragment = href.split("#", 1)[1] if "#" in href else None
            if isinstance(item.index, int) and item.index >= 0:
                self.current_index = item.index; self.update_navigation(); self.display_page(fragment=fragment)
            elif href:
                try:
                    base = urllib.parse.unquote(href.split("#", 1)[0]); candidate = os.path.join(self.temp_dir or "", base)
                    if self.handle_internal_link("file://" + candidate): return
                except Exception: pass
            self._set_toc_selected(item)
        try: actrow.connect("activated", _open_only)
        except Exception: pass
        g2 = Gtk.GestureClick(); g2.connect("pressed", lambda *_: _open_only(None)); actrow.add_controller(g2)
        list_item.set_child(wrapper)
        list_item._hbox = hbox; list_item._disc = disc; list_item._actrow = actrow
        list_item._nested = nested; list_item._nested_view = None; list_item._bound_item = None

    def _toc_on_bind(self, factory, list_item):
        item = list_item.get_item()
        disc = getattr(list_item, "_disc", None); actrow = getattr(list_item, "_actrow", None); nested = getattr(list_item, "_nested", None)
        if disc is None or actrow is None or nested is None:
            self._toc_on_setup(factory, list_item); disc = list_item._disc; actrow = list_item._actrow; nested = list_item._nested
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
        try: self._toc_actrows[item] = actrow; actrow.remove_css_class("selected")
        except Exception: pass
        has_children = item.children.get_n_items() > 0
        actrow.set_title(item.title or ""); disc.set_visible(has_children)
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
                        vis = not ch_nested.get_visible(); ch_nested.set_visible(vis)
                        ch_disc.set_from_icon_name("pan-down-symbolic" if vis else "pan-end-symbolic")
                        gv = getattr(li, "_nested_view", None)
                        if gv: gv.set_visible(vis)
                gch = Gtk.GestureClick(); gch.connect("pressed", lambda *_: _toggle_child()); ch_disc.add_controller(gch)
                def _open_child(_):
                    it = li.get_item()
                    if not it: return
                    href = it.href or ""; fragment = href.split("#", 1)[1] if "#" in href else None
                    if isinstance(it.index, int) and it.index >= 0:
                        self.current_index = it.index; self.update_navigation(); self.display_page(fragment=fragment)
                    elif href:
                        try:
                            base = urllib.parse.unquote(href.split("#", 1)[0]); candidate = os.path.join(self.temp_dir or "", base)
                            if self.handle_internal_link("file://" + candidate): return
                        except Exception: pass
                    self._set_toc_selected(it)
                try: ch_act.connect("activated", _open_child)
                except Exception: pass
                gch2 = Gtk.GestureClick(); gch2.connect("pressed", lambda *_: _open_child(None)); ch_act.add_controller(gch2)
                li.set_child(cwrap); li._row = ch_act; li._disc = ch_disc; li._nested = ch_nested; li._nested_view = None; li._bound_item = None

            def child_bind(f, li):
                it = li.get_item()
                if not it: return
                ch_act = getattr(li, "_row", None); ch_disc = getattr(li, "_disc", None); ch_nested = getattr(li, "_nested", None)
                if ch_act is None or ch_disc is None or ch_nested is None: return
                prevc = getattr(li, "_bound_item", None)
                if prevc is not None and prevc in self._toc_actrows:
                    try: self._toc_actrows.pop(prevc, None)
                    except Exception: pass
                li._bound_item = it
                try: self._toc_actrows[it] = ch_act; ch_act.remove_css_class("selected")
                except Exception: pass
                kids = it.children.get_n_items() > 0
                ch_act.set_title(it.title or ""); ch_disc.set_visible(kids)
                if kids:
                    ch_disc.set_from_icon_name("pan-down-symbolic" if ch_nested.get_visible() else "pan-end-symbolic")
                else:
                    ch_disc.set_from_icon_name(None)
                if kids and not getattr(li, "_nested_view", None):
                    sub_factory = Gtk.SignalListItemFactory(); sub_factory.connect("setup", child_setup); sub_factory.connect("bind", child_bind)
                    sub_sel = Gtk.NoSelection(model=it.children); gv = Gtk.ListView(model=sub_sel, factory=sub_factory); gv.set_vexpand(False)
                    ch_nested.append(gv); li._nested_view = gv
                if getattr(li, "_nested_view", None):
                    li._nested_view.set_visible(ch_nested.get_visible())

            nfactory = Gtk.SignalListItemFactory(); nfactory.connect("setup", child_setup); nfactory.connect("bind", child_bind)
            nsel = Gtk.NoSelection(model=item.children); nested_view = Gtk.ListView(model=nsel, factory=nfactory); nested_view.set_vexpand(False)
            nested.append(nested_view); list_item._nested_view = nested_view
            nested_view.set_visible(nested.get_visible())

        nv = getattr(list_item, "_nested_view", None)
        if nv: nv.set_visible(nested.get_visible())

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
            try:
                for li in self.toc_listview.get_children():
                    pass
            except Exception:
                pass
        except Exception:
            pass

    # ---------- href registration ----------
    def _register_href_variants(self, node: TocItem):
        if not node or not getattr(node, "href", None): return
        href = (node.href or "").strip()
        if not href: return
        keys = set()
        keys.add(href); keys.add(href.lstrip("./"))
        try:
            uq = urllib.parse.unquote(href); keys.add(uq); keys.add(uq.lstrip("./"))
        except Exception: pass
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
                it = self.items[node.index]; iname = (it.get_name() or "").replace("\\", "/")
                if iname:
                    keys.add(iname); keys.add(os.path.basename(iname))
                    try: keys.add(urllib.parse.unquote(iname)); keys.add(urllib.parse.unquote(os.path.basename(iname)))
                    except Exception: pass
        except Exception: pass
        extras = set()
        for k in list(keys):
            for pfx in ("OEBPS/", "OPS/"):
                extras.add(pfx + k)
        keys.update(extras)
        for k in keys:
            if not k: continue
            if k not in self.href_map:
                self.href_map[k] = node

    # ---------- HTML wrapper ----------
    def _wrap_html(self, raw_html, base_uri):
        page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS
        link_intercept_script = """
        <script>
        (function() {
            function updateDarkMode() {
                if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
                    document.documentElement.classList.add('dark-mode');
                    document.body.classList.add('dark-mode');
                } else {
                    document.documentElement.classList.remove('dark-mode');
                    document.body.classList.remove('dark-mode');
                }
            }
            updateDarkMode();
            if (window.matchMedia) {
                window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateDarkMode);
            }
            function interceptLinks() {
                document.addEventListener('click', function(e) {
                    var target = e.target;
                    while (target && target.tagName !== 'A') {
                        target = target.parentElement;
                        if (!target || target === document.body) break;
                    }
                    if (target && target.tagName === 'A' && target.href) {
                        var href = target.href;
                        e.preventDefault();
                        e.stopPropagation();
                        try {
                            window.location.href = href;
                        } catch(err) { console.error('[js] navigation error:', err); }
                        return false;
                    }
                }, true);
            }
            if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', interceptLinks); } else { interceptLinks(); }
        })();
        </script>
        """
        base_tag = ""
        try:
            if base_uri: base_tag = f'<base href="{base_uri}"/>'
        except Exception: base_tag = ""
        head = f"""<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><meta name="color-scheme" content="light dark"/>{base_tag}<style>{page_css}</style>{link_intercept_script}"""
        wrapped = f"<!DOCTYPE html><html><head>{head}</head><body>{raw_html}</body></html>"
        return wrapped

    # ---------- open file ----------
    def open_file(self, *_):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        epub_filter = Gtk.FileFilter(); epub_filter.add_pattern("*.epub"); epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter); dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if not f: return
            target = f.get_path()
            def do_open():
                # enable sidebar toggle (there is now a book)
                try:
                    self.content_sidebar_toggle.set_sensitive(True)
                    self._sidebar_is_back = False
                    self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
                    self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
                except Exception: pass
                # open selected epub
                self.load_epub(target)
            if getattr(self, "book", None) and os.path.abspath(self.book_path or "") != os.path.abspath(target):
                self._confirm_close_current_then(lambda: do_open())
            else:
                do_open()
        except GLib.Error:
            pass

    # ---------- cover detection via OPF ----------
    def _find_cover_via_opf(self, extracted_paths, image_names, image_basenames):
        if not self.temp_dir: return None, None
        pattern = os.path.join(self.temp_dir, "**", "*.opf")
        opf_files = sorted(glob.glob(pattern, recursive=True))
        for opf in opf_files:
            try:
                with open(opf, "rb") as fh: raw = fh.read()
                soup = BeautifulSoup(raw, "xml")
                cover_id = None
                meta = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "meta" and tag.has_attr("name") and tag["name"].lower() == "cover")
                if meta and meta.has_attr("content"): cover_id = meta["content"]
                href = None
                if cover_id:
                    item_tag = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("id") and tag["id"] == cover_id)
                    if item_tag and item_tag.has_attr("href"): href = item_tag["href"]
                if not href:
                    item_prop = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("properties") and "cover-image" in tag["properties"])
                    if item_prop and item_prop.has_attr("href"): href = item_prop["href"]
                if not href:
                    item_cover_href = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'cover.*\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                    if item_cover_href and item_cover_href.has_attr("href"): href = item_cover_href["href"]
                if not href:
                    first_img = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                    if first_img and first_img.has_attr("href"): href = first_img["href"]
                if not href: continue
                opf_dir = os.path.dirname(opf)
                candidate_abs = os.path.normpath(os.path.join(opf_dir, urllib.parse.unquote(href))); candidate_abs = os.path.abspath(candidate_abs)
                candidate_abs2 = os.path.abspath(os.path.normpath(os.path.join(self.temp_dir, urllib.parse.unquote(href))))
                try: rel_from_temp = os.path.relpath(candidate_abs, self.temp_dir).replace(os.sep, "/")
                except Exception: rel_from_temp = os.path.basename(candidate_abs)
                variants = [rel_from_temp, os.path.basename(rel_from_temp)]
                for pfx in ("OEBPS/", "OPS/"):
                    variants.append(pfx + rel_from_temp); variants.append(pfx + os.path.basename(rel_from_temp))
                try:
                    uq = urllib.parse.unquote(rel_from_temp); variants.append(uq); variants.append(os.path.basename(uq))
                except Exception: pass
                if os.path.exists(candidate_abs): return candidate_abs, None
                if os.path.exists(candidate_abs2): return candidate_abs2, None
                for v in variants:
                    if v in extracted_paths:
                        abs_p = os.path.abspath(os.path.join(self.temp_dir, v)); return abs_p, None
                    if v in image_names: return None, image_names[v]
                    bn = os.path.basename(v)
                    if bn in image_basenames: return None, image_basenames[bn][0]
                bn = os.path.basename(href)
                for p in extracted_paths:
                    if os.path.basename(p).lower() == bn.lower():
                        abs_p = os.path.abspath(os.path.join(self.temp_dir, p)); return abs_p, None
            except Exception:
                continue
        return None, None

    # ---------- Load EPUB ----------
    def load_epub(self, path, resume=False, resume_index=None):
        try:
            # ensure reader content replaces library immediately
            try: self.toolbar.set_content(self._reader_content_box)
            except Exception: pass

            # when loading a new book, enable toggle and ensure toggle behaves normally
            try:
                self.content_sidebar_toggle.set_sensitive(True)
                self._sidebar_is_back = False
                self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
                self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
            except Exception: pass

            # cleanup previous temporary extraction (we save state earlier if needed)
            try: self.cleanup()
            except Exception: pass

            self.book_path = path
            self.book = epub.read_epub(path)
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
                try: iid = getattr(it, "id", None) or (it.get_id() if hasattr(it, "get_id") else None)
                except Exception: iid = None
                if not iid: iid = it.get_name() or os.urandom(8).hex()
                id_map[iid] = it
            ordered = []
            try:
                spine = getattr(self.book, "spine", None) or []
                for entry in spine:
                    sid = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
                    if sid in id_map: ordered.append(id_map.pop(sid))
                ordered.extend(id_map.values()); self.items = ordered
            except Exception:
                self.items = docs

            if not self.items:
                self.show_error("No document items found in EPUB"); return

            self.temp_dir = tempfile.mkdtemp()
            extracted_paths = set()

            # unzip fallback
            opf_files = glob.glob(os.path.join(self.temp_dir, "**", "*.opf"), recursive=True)
            if not opf_files:
                try:
                    with zipfile.ZipFile(path, "r") as z: z.extractall(self.temp_dir)
                except Exception: pass

            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path: continue
                sanitized_path = self.sanitize_path(item_path)
                if sanitized_path is None: continue
                full = os.path.join(self.temp_dir, sanitized_path)
                try:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as fh: fh.write(item.get_content())
                    extracted_paths.add(sanitized_path.replace("\\", "/"))
                except OSError:
                    continue

            image_items = list(self.book.get_items_of_type(ebooklib.ITEM_IMAGE))
            image_names = { (im.get_name() or "").replace("\\", "/"): im for im in image_items }
            image_basenames = {}
            for im in image_items:
                bn = os.path.basename((im.get_name() or "")).replace("\\", "/")
                if bn: image_basenames.setdefault(bn, []).append(im)

            self.item_map = {it.get_name(): it for it in self.items}
            self.extract_css()

            # metadata
            title = APP_NAME; author = ""
            try:
                meta = self.book.get_metadata("DC", "title")
                if meta and meta[0]: title = meta[0][0]
                m2 = self.book.get_metadata("DC", "creator")
                if m2 and m2[0]: author = m2[0][0]
            except Exception: pass
            self.book_title.set_text(title); self.book_author.set_text(author)
            self.content_title_label.set_text(title); self.set_title(title or APP_NAME)

            # robust cover detection
            try:
                cover_path_to_use = None; cover_item_obj = None
                cpath, citem = self._find_cover_via_opf(extracted_paths, image_names, image_basenames)
                if cpath: cover_path_to_use = cpath
                elif citem: cover_item_obj = citem

                if not cover_path_to_use and not cover_item_obj:
                    priority_names = ("ops/cover.xhtml","oebps/cover.xhtml","ops/cover.html","cover.xhtml","cover.html","ops/title.xhtml","title.xhtml","ops/titlepage.xhtml","titlepage.xhtml")
                    docs_list = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)); lower_map = { (d.get_name() or "").lower(): d for d in docs_list }
                    cover_doc = None
                    for pn in priority_names:
                        if pn in lower_map:
                            cover_doc = lower_map[pn]; break
                    if not cover_doc:
                        for d in docs_list:
                            n = (d.get_name() or "").lower()
                            if "/ops/cover" in n or n.endswith("cover.xhtml") or n.endswith("cover.html") or "cover.xhtml" in n:
                                cover_doc = d; break
                    if cover_doc:
                        try:
                            soup = BeautifulSoup(cover_doc.get_content(), "html.parser"); doc_dir = os.path.dirname(cover_doc.get_name() or "")
                            srcs = []
                            img = soup.find("img", src=True)
                            if img: srcs.append(img["src"])
                            for svg_im in soup.find_all("image"):
                                if svg_im.has_attr("xlink:href"): srcs.append(svg_im["xlink:href"])
                                elif svg_im.has_attr("href"): srcs.append(svg_im["href"])
                            for src in srcs:
                                if not src: continue
                                src = src.split("#",1)[0]; src = urllib.parse.unquote(src)
                                candidate_rel = os.path.normpath(os.path.join(doc_dir, src)).replace("\\","/")
                                for v in (candidate_rel, os.path.basename(candidate_rel)):
                                    if v in extracted_paths: cover_path_to_use = os.path.join(self.temp_dir, v); break
                                    if v in image_names: cover_item_obj = image_names[v]; break
                                if cover_path_to_use or cover_item_obj: break
                        except Exception: pass

                if not cover_path_to_use and not cover_item_obj:
                    for im_name, im in image_names.items():
                        if "cover" in im_name.lower() or "cover" in os.path.basename(im_name).lower():
                            cover_item_obj = im; break

                if not cover_path_to_use and not cover_item_obj:
                    for p in extracted_paths:
                        if p.lower().endswith((".png",".jpg",".jpeg",".gif",".webp")):
                            cover_path_to_use = os.path.join(self.temp_dir, p); break

                if cover_item_obj and not cover_path_to_use:
                    iname = (cover_item_obj.get_name() or "").replace("\\","/")
                    for cand in (iname, os.path.basename(iname)):
                        if cand in extracted_paths:
                            cover_path_to_use = os.path.join(self.temp_dir, cand); break
                        for pfx in ("OEBPS/","OPS/"):
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
                    except Exception: pass

                if cover_path_to_use and os.path.exists(cover_path_to_use):
                    try:
                        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover_path_to_use, COVER_W, COVER_H, True)
                        tex = Gdk.Texture.new_for_pixbuf(pix); self.cover_image.set_from_paintable(tex)
                        try: self.cover_image.set_size_request(COVER_W, COVER_H)
                        except Exception: pass
                        self.last_cover_path = cover_path_to_use
                    except Exception:
                        self.last_cover_path = None; cover_path_to_use = None

                if not cover_path_to_use and not self.last_cover_path:
                    placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H); placeholder_pb.fill(0xddddddff)
                    placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb); self.cover_image.set_from_paintable(placeholder_tex)
                    try: self.cover_image.set_size_request(COVER_W, COVER_H)
                    except Exception: pass
            except Exception: pass

            # populate TOC and show first page
            self._populate_toc_tree()

            # If TOC exists, auto-show sidebar
            try:
                if getattr(self, "toc_root_store", None) and self.toc_root_store.get_n_items() > 0:
                    try: self.split.set_show_sidebar(True)
                    except Exception: pass
            except Exception: pass

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
            # after successful open, update library entry and copy cover into persistent dir
            self._update_library_entry()

        except Exception:
            print(traceback.format_exc()); self.show_error("Error loading EPUB — see console")

    def sanitize_path(self, path):
        if not path: return None
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or os.path.isabs(normalized): return None
        if ".." in normalized.split(os.sep): return None
        return normalized

    def _populate_toc_tree(self):
        def href_to_index(href):
            if not href: return -1
            h = href.split("#")[0]; candidates = [h, os.path.basename(h)]
            try:
                uq = urllib.parse.unquote(h)
                if uq != h: candidates.append(uq); candidates.append(os.path.basename(uq))
            except Exception: pass
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
                        self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store); self.toc_listview.set_model(self.toc_sel); return
        except Exception: pass

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
                    self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store); self.toc_listview.set_model(self.toc_sel); return
        except Exception: pass

        for i, it in enumerate(self.items):
            title = os.path.basename(it.get_name())
            add_node(title, it.get_name(), root)
        self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store); self.toc_listview.set_model(self.toc_sel)

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
            bn = os.path.basename(c); t = self.href_map.get(bn)
            if t: return t
        if fragment:
            frag_keys = [f"#{fragment}", fragment, os.path.basename(fragment)]
            for fk in frag_keys:
                t = self.href_map.get(fk)
                if t: return t
        return None

    def handle_internal_link(self, uri):
        path = uri.replace("file://", ""); fragment = None
        if "#" in path: path, fragment = path.split("#", 1)
        base = path
        if self.temp_dir and base.startswith(self.temp_dir):
            rel = os.path.relpath(base, self.temp_dir).replace(os.sep, "/")
        else:
            rel = base.replace(os.sep, "/")
        candidates = [rel, os.path.basename(rel)]
        try:
            uq = urllib.parse.unquote(rel)
            if uq != rel: candidates.append(uq); candidates.append(os.path.basename(uq))
        except Exception: pass

        toc_match = self._find_tocitem_for_candidates(candidates, fragment)
        if toc_match:
            if isinstance(toc_match.index, int) and toc_match.index >= 0:
                self.current_index = toc_match.index; self.update_navigation()
                frag = fragment or (toc_match.href.split("#", 1)[1] if "#" in (toc_match.href or "") else None)
                self.display_page(fragment=frag); return True
            else:
                href = toc_match.href or ""; candidate_path = None
                try: candidate_path = os.path.join(self.temp_dir or "", urllib.parse.unquote(href.split("#", 1)[0]))
                except Exception: pass
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
            possible_paths.append(os.path.join(self.temp_dir, rel)); possible_paths.append(os.path.join(self.temp_dir, os.path.basename(rel)))
        possible_paths.append(path)
        for p in possible_paths:
            if not p: continue
            if os.path.exists(p): return self._load_file_with_css(p, fragment)
        return False

    def _load_file_with_css(self, file_path, fragment=None):
        if not os.path.exists(file_path): return False
        if not self.css_content: self.extract_css()
        ext = os.path.splitext(file_path)[1].lower()
        base_uri = "file://" + (os.path.dirname(file_path) or "/") + "/"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            img_uri = "file://" + file_path
            raw = f'<div style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;"><img src="{img_uri}" alt="image" style="max-width:100%;height:auto;"/></div>'
            html = self._wrap_html(raw, base_uri)
            try:
                if self.webview: self.webview.load_html(html, base_uri)
                else: self.textview.get_buffer().set_text(f"[Image] {file_path}")
            except Exception as e: print(f"Error loading image: {e}")
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
                html_content = self._wrap_html(body_content, base_uri)
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
        if not self.css_content: self.extract_css()
        item = self.items[self.current_index]
        if not item or not hasattr(item, 'get_content'): return
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            for tag in soup.find_all(['style','link']): tag.decompose()
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
            wrapped_html = self._wrap_html(content, base_uri)
            if self.webview:
                self.webview.load_html(wrapped_html, base_uri)
                if fragment: GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
            else:
                buf = self.textview.get_buffer(); buf.set_text(soup.get_text())
            total = len(self.items)
            self.progress.set_fraction((self.current_index + 1) / total)
            self.progress.set_text(f"{self.current_index + 1}/{total}")
            try:
                for ti in list(self.href_map.values()):
                    if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == self.current_index:
                        self._set_toc_selected(ti); break
            except Exception: pass
            self._save_progress_for_library()
        except Exception as e:
            print(f"Error displaying page: {e}"); self.show_error(f"Error displaying page: {e}")

    def _scroll_to_fragment(self, fragment):
        if self.webview and fragment:
            js_code = f"var element = document.getElementById('{fragment}'); if (element) {{ element.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}"
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception:
                try: self.webview.run_javascript(js_code, None, None, None)
                except Exception: pass
        return False

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

    def extract_css(self):
        self.css_content = ""
        if not self.book: return
        try:
            for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
                try: self.css_content += item.get_content().decode("utf-8") + "\n"
                except Exception: pass
            if self.temp_dir and os.path.exists(self.temp_dir):
                for fn in ("flow0001.css","core.css","se.css","style.css"):
                    p = os.path.join(self.temp_dir, fn)
                    if os.path.exists(p):
                        try:
                            with open(p, "r", encoding="utf-8", errors="ignore") as fh: self.css_content += fh.read() + "\n"
                        except Exception: pass
        except Exception as e:
            print(f"Error extracting CSS: {e}")

    def show_error(self, message):
        try:
            dialog = Adw.MessageDialog.new(self, "Error", message)
            dialog.add_response("ok", "OK"); dialog.present()
        except Exception:
            print("Error dialog:", message)

    def cleanup(self):
        # remove temp dir only; keep library; when switching to library we intentionally keep the book metadata in memory if desired
        if getattr(self, "temp_dir", None) and os.path.exists(self.temp_dir):
            try: shutil.rmtree(self.temp_dir)
            except Exception as e: print(f"Error cleaning up temp directory: {e}")
        self.temp_dir = None
        # clear structures that depend on extracted files
        self.items = []
        self.item_map = {}
        self.css_content = ""
        self.current_index = 0
        try:
            if getattr(self, "toc_root_store", None):
                self.toc_root_store = Gio.ListStore(item_type=TocItem)
                self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                self.toc_listview.set_model(self.toc_sel)
            self._toc_actrows = {}; self.href_map = {}
        except Exception as e:
            print(f"Error clearing TOC store: {e}")
        self.update_navigation()
        if self.webview:
            try:
                blank = self._wrap_html("", ""); self.webview.load_html(blank, "")
            except Exception: pass
        elif hasattr(self, 'textview'):
            try: self.textview.get_buffer().set_text("")
            except Exception: pass
        self.book_title.set_text(""); self.book_author.set_text("")
        try:
            placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H); placeholder_pb.fill(0xddddddff)
            self.cover_image.set_from_paintable(Gdk.Texture.new_for_pixbuf(placeholder_pb))
        except Exception: pass
        # when cleanup happens (explicit close), ensure sidebar toggle is reset to normal mode
        try:
            self._sidebar_is_back = False
            self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
            self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
            # if no book left, disable toggle to avoid empty TOC confusion
            if not getattr(self, "book", None):
                self.content_sidebar_toggle.set_sensitive(False)
        except Exception:
            pass

    def _update_library_entry(self):
        path = self.book_path or ""
        if not path: return
        title = self.book_title.get_text() or os.path.basename(path)
        author = self.book_author.get_text() or ""
        cover_src = self.last_cover_path; cover_dst = None
        if cover_src and os.path.exists(cover_src):
            try:
                h = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
                ext = os.path.splitext(cover_src)[1].lower() or ".png"
                cover_dst = os.path.join(COVERS_DIR, f"{h}{ext}")
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file(cover_src)
                    scaled = pix.scale_simple(LIB_COVER_W, LIB_COVER_H, GdkPixbuf.InterpType.BILINEAR)
                    scaled.savev(cover_dst, ext.replace(".", ""), [], [])
                except Exception:
                    shutil.copy2(cover_src, cover_dst)
            except Exception:
                cover_dst = None
        found = False
        for e in list(self.library):
            if e.get("path") == path:
                e["title"] = title; e["author"] = author
                if cover_dst: e["cover"] = cover_dst
                e["index"] = int(self.current_index); e["progress"] = float(self.progress.get_fraction() or 0.0)
                found = True; break
        if not found:
            entry = {"path": path, "title": title, "author": author, "cover": cover_dst, "index": int(self.current_index), "progress": float(self.progress.get_fraction() or 0.0)}
            self.library.append(entry)
        if len(self.library) > 200: self.library = self.library[-200:]
        save_library(self.library)

    def _save_progress_for_library(self):
        if not self.book_path: return
        changed = False
        for e in self.library:
            if e.get("path") == self.book_path:
                e["index"] = int(self.current_index); e["progress"] = float(self.progress.get_fraction() or 0.0); changed = True; break
        if changed: save_library(self.library)

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer")
        self.create_action("quit", self.quit, ["<primary>q"])
    def do_activate(self):
        win = self.props.active_window
        if not win: win = EPubViewer(self)
        win.present()
    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None); action.connect("activate", callback); self.add_action(action)
        if shortcuts: self.set_accels_for_action(f"app.{name}", shortcuts)

def main():
    _ensure_library_dir()
    app = Application()
    return app.run(None)

if __name__ == "__main__":
    main()

