import os
import re
import shutil
import tempfile
import traceback
import zipfile
import hashlib
from gi.repository import Adw, Gio, GLib, Gtk, Gdk, GdkPixbuf
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup


APP_NAME = "EPUB Reader"
COVER_W = 100
COVER_H = 140
LIB_COVER_W = 120
LIB_COVER_H = 160
COVERS_DIR = os.path.expanduser("~/.local/share/epub_reader/covers")


class EpubReaderWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        if not os.path.exists(COVERS_DIR):
            os.makedirs(COVERS_DIR, mode=0o700, parents=True)

        self.library = []
        self.book = None
        self.book_path = None
        self.items = []
        self.item_map = {}
        self.current_index = 0
        self.temp_dir = None
        self.css_content = ""
        self.toc_root_store = None
        self.toc_sel = None
        self._toc_actrows = {}
        self.reading_breakpoint = None
        self._responsive_enabled = False
        self._last_width = 0
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self._resize_timeout_id = None
        self._lib_search_handler_id = None
        self.library_search_text = ""
        self.last_cover_path = None
        self.book_title = Gtk.Label()
        self.book_author = Gtk.Label()
        self.cover_image = Gtk.Image()
        self.content_title_label = Gtk.Label()
        self.progress = Gtk.ProgressBar()
        self.webview = None
        self.toolbar = Adw.ToolbarView()
        self.scrolled = Gtk.ScrolledWindow()
        self.toc_listview = Gtk.ListView()
        self.split = Adw.NavigationSplitView()
        self._reader_content_box = None
        self.content_sidebar_toggle = Gtk.ToggleButton()
        self.open_btn = Gtk.Button()
        self.search_toggle_btn = Gtk.Button()
        self.library_search_revealer = Gtk.Revealer()
        self.library_search_entry = Gtk.SearchEntry()

        self._setup_ui()

    def _setup_ui(self):
        self.set_default_size(900, 700)
        self.set_title(APP_NAME)

        header = Adw.HeaderBar()
        self.content_title_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        header.set_title_widget(self.content_title_label)

        menu_model = Gio.Menu()
        menu_model.append("Settings", "win.settings")
        menu_model.append("About", "win.about")
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.set_popover(Gtk.PopoverMenu.new_from_model(menu_model))
        header.pack_end(self.menu_btn)

        self.toolbar.add_top_bar(header)
        self.toolbar.add_top_bar(self.library_search_revealer)

        self.split.set_sidebar(Gtk.ScrolledWindow())
        self.split.get_sidebar().set_size_request(250, -1)
        self.split.set_content(self.toolbar)
        self.set_content(self.split)

        self._setup_sidebar()
        self._setup_bottom_bar()
        self._setup_search()
        self._setup_responsive_sidebar()

        self.show_library()

    def _setup_sidebar(self):
        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar_content.set_margin_top(12)
        sidebar_content.set_margin_bottom(12)
        sidebar_content.set_margin_start(12)
        sidebar_content.set_margin_end(12)

        cover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        self.cover_image.set_size_request(COVER_W, COVER_H)
        cover_box.append(self.cover_image)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.book_title.set_halign(Gtk.Align.CENTER)
        self.book_title.set_wrap(True)
        self.book_title.set_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR
        meta_box.append(self.book_title)
        self.book_author.set_halign(Gtk.Align.CENTER)
        self.book_author.add_css_class("subtitle")
        meta_box.append(self.book_author)
        cover_box.append(meta_box)

        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.progress.set_show_text(True)
        progress_box.append(self.progress)
        progress_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        sidebar_content.append(cover_box)
        sidebar_content.append(progress_box)

        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_vexpand(True)
        toc_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.toc_listview.set_vexpand(True)
        toc_scroll.set_child(self.toc_listview)
        sidebar_content.append(toc_scroll)

        self.split.get_sidebar().set_child(sidebar_content)

    def _setup_bottom_bar(self):
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6)
        bottom_bar.set_margin_bottom(6)
        bottom_bar.set_margin_start(6)
        bottom_bar.set_margin_end(6)

        prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        prev_btn.connect("clicked", self.go_prev)
        bottom_bar.append(prev_btn)

        next_btn = Gtk.Button(icon_name="go-next-symbolic")
        next_btn.connect("clicked", self.go_next)
        bottom_bar.append(next_btn)

        bottom_bar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.content_sidebar_toggle = Gtk.ToggleButton(icon_name="view-sidebar-start-symbolic")
        self.content_sidebar_toggle.set_tooltip_text("Toggle Table of Contents")
        self.content_sidebar_toggle.connect("toggled", self._on_sidebar_toggle)
        bottom_bar.append(self.content_sidebar_toggle)

        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.set_tooltip_text("Open EPUB File")
        self.open_btn.connect("clicked", self.open_epub_dialog)
        bottom_bar.append(self.open_btn)

        self.search_toggle_btn = Gtk.Button(icon_name="system-search-symbolic")
        self.search_toggle_btn.set_tooltip_text("Toggle Search")
        self.search_toggle_btn.connect("clicked", self._on_search_toggle)
        bottom_bar.append(self.search_toggle_btn)

        self.scrolled.set_child(Gtk.Label(label="No book loaded."))
        self._reader_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._reader_content_box.set_vexpand(True)
        self._reader_content_box.append(self.scrolled)
        self._reader_content_box.append(bottom_bar)
        self.toolbar.set_content(self._reader_content_box)

    def _setup_search(self):
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_bar.set_margin_start(6)
        search_bar.set_margin_end(6)
        search_bar.set_margin_top(6)
        search_bar.set_margin_bottom(6)
        self.library_search_entry.set_placeholder_text("Search library (title, author, filename)")
        self._lib_search_handler_id = self.library_search_entry.connect("search-changed", lambda e: self._on_library_search_changed(e.get_text()))
        search_bar.append(self.library_search_entry)
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.connect("clicked", lambda b: self._on_search_toggle())
        search_bar.append(close_btn)
        self.library_search_revealer.set_child(search_bar)

    def _on_search_toggle(self, btn=None):
        currently_revealed = self.library_search_revealer.get_reveal_child()
        self.library_search_revealer.set_reveal_child(not currently_revealed)
        if not currently_revealed:
            self.library_search_entry.grab_focus()
        else:
            self.library_search_entry.set_text("")

    def _on_library_search_changed(self, text):
        self.library_search_text = text or ""
        self.show_library()

    def _setup_responsive_sidebar(self):
        self._last_width = 0
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self.connect("notify::default-width", self._on_window_size_changed)

    def _on_window_size_changed(self, *args):
        if self._resize_timeout_id:
            GLib.source_remove(self._resize_timeout_id)
        self._resize_timeout_id = GLib.timeout_add(150, self._apply_responsive_sidebar)

    def _apply_responsive_sidebar(self):
        self._resize_timeout_id = None
        try:
            width = self.get_width()
            if abs(width - self._last_width) < 10:
                return False
            self._last_width = width
            is_narrow = width < 768
            if is_narrow == self._last_was_narrow:
                return False
            was_narrow = self._last_was_narrow
            self._last_was_narrow = is_narrow
            if self._responsive_enabled and self.book and self.book_path:
                if is_narrow:
                    if was_narrow is False:
                        try:
                            self._user_hid_sidebar = not self.split.get_show_sidebar()
                        except Exception:
                            pass
                    self.split.set_collapsed(True)
                else:
                    self.split.set_collapsed(False)
                    if not self._user_hid_sidebar:
                        self.split.set_show_sidebar(True)
            else:
                if self._last_was_narrow is not None:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(False)
        except Exception as e:
            print(f"Error in responsive sidebar: {e}")
        return False

    def _on_sidebar_toggle(self, btn):
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            if not new:
                self._user_hid_sidebar = True
            else:
                self._user_hid_sidebar = False
        except Exception:
            pass

    def open_epub_dialog(self, btn):
        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file:
                    path = file.get_path()
                    if path and os.path.isfile(path):
                        self.load_epub(path)
            dialog.destroy()

        dialog = Gtk.FileDialog()
        dialog.set_title("Open EPUB File")
        filter_epub = Gtk.FileFilter()
        filter_epub.add_pattern("*.epub")
        filter_epub.set_name("EPUB Files")
        dialog.set_default_filter(filter_epub)
        dialog.open(self, None, on_response)

    def sanitize_path(self, path):
        if not path:
            return None
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or os.path.isabs(normalized):
            return None
        if ".." in normalized.split(os.sep):
            return None
        return normalized

    def load_epub(self, path, resume=False, resume_index=None):
        try:
            self.cleanup()
            self.book = epub.read_epub(path)
            self.book_path = path

            self.temp_dir = tempfile.mkdtemp()
            extracted_paths = set()
            with zipfile.ZipFile(path, "r") as z:
                z.extractall(self.temp_dir)

            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue
                sanitized_path = self.sanitize_path(item_path)
                if sanitized_path is None:
                    continue
                full = os.path.join(self.temp_dir, sanitized_path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as fh:
                    fh.write(item.get_content())
                extracted_paths.add(sanitized_path)

            self.items = list(self.book.get_items())
            self.item_map = {item.get_name(): item for item in self.items}

            self.extract_css()

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
            self.content_title_label.set_text(title or APP_NAME)
            self.set_title(title or APP_NAME)

            cover_path_to_use = None
            cover_item_obj = None
            cpath, citem = self._find_cover_via_opf(extracted_paths, {os.path.basename(p): p for p in extracted_paths}, {os.path.basename(p): p for p in extracted_paths})
            if cpath:
                cover_path_to_use = cpath
            elif citem:
                cover_item_obj = citem

            if not cover_path_to_use and not cover_item_obj:
                priority_names = ("ops/cover.xhtml", "oebps/cover.xhtml", "ops/cover.html", "cover.xhtml", "cover.html", "ops/title.xhtml", "title.xhtml")
                docs_list = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
                lower_map = { (d.get_name() or "").lower(): d for d in docs_list }
                cover_doc = None
                for pn in priority_names:
                    if pn in lower_map:
                        cover_doc = lower_map[pn]
                        break
                if cover_doc:
                    try:
                        soup = BeautifulSoup(cover_doc.get_content(), "html.parser")
                        images = soup.find_all("img", src=True)
                        if images:
                            cover_item_obj = self._resolve_image_href(images[0]["src"], extracted_paths)
                    except Exception:
                        pass

            if not cover_path_to_use and not cover_item_obj:
                for p in extracted_paths:
                    if "cover" in p.lower():
                        cover_path_to_use = os.path.join(self.temp_dir, p)
                        break

            if cover_item_obj and not cover_path_to_use:
                item_name = cover_item_obj.get_name()
                if item_name in extracted_paths:
                    cover_path_to_use = os.path.join(self.temp_dir, item_name)

            if cover_path_to_use:
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file(cover_path_to_use)
                    scaled = pix.scale_simple(COVER_W, COVER_H, GdkPixbuf.InterpType.BILINEAR)
                    texture = Gdk.Texture.new_for_pixbuf(scaled)
                    self.cover_image.set_from_paintable(texture)
                    self.last_cover_path = cover_path_to_use
                except Exception:
                    self.last_cover_path = None
                    placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
                    placeholder_pb.fill(0xddddddff)
                    placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
                    self.cover_image.set_from_paintable(placeholder_tex)
            else:
                self.last_cover_path = None
                placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
                placeholder_pb.fill(0xddddddff)
                placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
                self.cover_image.set_from_paintable(placeholder_tex)

            self._populate_toc_tree()
            if getattr(self, "toc_root_store", None) and self.toc_root_store.get_n_items() > 0:
                try:
                    self.split.set_show_sidebar(True)
                except Exception:
                    pass

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
            else:
                self.current_index = 0

            self._enable_responsive_sidebar()
            self.update_navigation()
            self.display_page()
            self._update_library_entry()

        except Exception as e:
            print(traceback.format_exc())
            self.show_error(f"Error loading EPUB – see console")

    def _find_cover_via_opf(self, extracted_paths, image_names, image_basenames):
        opf_files = [p for p in extracted_paths if p.endswith('.opf')]
        for opf in opf_files:
            try:
                with open(os.path.join(self.temp_dir, opf), "rb") as fh:
                    raw = fh.read()
                soup = BeautifulSoup(raw, "xml")
                cover_id = None
                meta = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "meta" and tag.has_attr("name") and tag["name"].lower() == "cover")
                if meta and meta.has_attr("content"):
                    cover_id = meta["content"]
                href = None
                if cover_id:
                    item_tag = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("id") and tag["id"] == cover_id)
                    if item_tag and item_tag.has_attr("href"):
                        href = item_tag["href"]
                if not href:
                    item_with_props = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("properties") and "cover-image" in tag["properties"])
                    if item_with_props and item_with_props.has_attr("href"):
                        href = item_with_props["href"]
                if href:
                    variants = [href, os.path.normpath(href), os.path.basename(href)]
                    for v in variants:
                        if v in image_names:
                            return None, image_names[v]
                        bn = os.path.basename(v)
                        if bn in image_basenames:
                            return os.path.join(self.temp_dir, image_basenames[bn]), None
                        for p in extracted_paths:
                            if os.path.basename(p).lower() == bn.lower():
                                return os.path.join(self.temp_dir, p), None
            except Exception:
                continue
        return None, None

    def _resolve_image_href(self, href, extracted_paths):
        if not href:
            return None
        variants = [href, os.path.normpath(href), os.path.basename(href)]
        for v in variants:
            for item in self.book.get_items_of_type(ebooklib.ITEM_IMAGE):
                if item.get_name() == v or os.path.basename(item.get_name()) == os.path.basename(v):
                    return item
        return None

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

    def _populate_toc_tree(self):
        def add_node(title, href, parent_store):
            node = TocItem(title or "", href or "")
            parent_store.append(node)
            return node

        def href_to_index(href):
            if not href:
                return -1
            h = href.split("#")[0]
            candidates = [h, os.path.basename(h)]
            try:
                import urllib.parse
                uq = urllib.parse.unquote(h)
                candidates.append(uq)
                candidates.append(os.path.basename(uq))
            except Exception:
                pass
            for c in candidates:
                for i, item in enumerate(self.items):
                    if (item.get_name() or "").lower() == c.lower():
                        return i
                    if os.path.basename(item.get_name() or "").lower() == os.path.basename(c).lower():
                        return i
            return -1

        root = Gio.ListStore(item_type=TocItem)
        self.href_map = {}

        nav_items = list(self.book.get_items_of_type(ebooklib.ITEM_NAVIGATION))
        for nav_item in nav_items:
            try:
                soup = BeautifulSoup(nav_item.get_content(), "html.parser")
                toc_nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", {"role": "doc-toc"})
                if toc_nav:
                    def walk_list(ol, parent_store):
                        for li in ol.find_all("li", recursive=False):
                            a = li.find("a", href=True)
                            title = a.get_text(strip=True) if a else li.get_text(strip=True)
                            href = a["href"] if a else ""
                            node = add_node(title, href, parent_store)
                            if href:
                                self.href_map[href] = node
                                self.href_map[os.path.basename(href)] = node
                            child_ol = li.find("ol", recursive=False)
                            if child_ol:
                                walk_list(child_ol, node.children)
                    ol = toc_nav.find("ol")
                    if ol:
                        walk_list(ol, root)
            except Exception:
                pass

        if root.get_n_items() == 0:
            ncx_item = None
            try:
                ncx_item = self.book.get_item_with_id("ncx")
            except Exception:
                pass
            if ncx_item:
                try:
                    soup = BeautifulSoup(ncx_item.get_content(), "xml")
                    def walk_navpoints(parent, parent_store):
                        for np in parent.find_all("navPoint", recursive=False):
                            text_tag = np.find("text")
                            content_tag = np.find("content")
                            title = text_tag.get_text(strip=True) if text_tag else ""
                            href = content_tag["src"] if content_tag and content_tag.has_attr("src") else ""
                            node = add_node(title or os.path.basename(href), href or "", parent_store)
                            if href:
                                self.href_map[href] = node
                                self.href_map[os.path.basename(href)] = node
                            walk_navpoints(np, node.children)
                    navmap = soup.find("navMap")
                    if navmap:
                        walk_navpoints(navmap, root)
                except Exception:
                    pass

        if root.get_n_items() == 0:
            for i, it in enumerate(self.items):
                title = os.path.basename(it.get_name())
                node = add_node(title, it.get_name(), root)
                self.href_map[it.get_name()] = node
                self.href_map[os.path.basename(it.get_name())] = node

        self.toc_root_store = root
        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview.set_model(self.toc_sel)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._toc_setup)
        factory.connect("bind", self._toc_bind)
        self.toc_listview.set_factory(factory)

    def _toc_setup(self, factory, list_item):
        row = Adw.ActionRow()
        disc = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        disc.set_visible(False)
        row.add_prefix(disc)
        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nested.set_margin_start(18)
        nested.set_visible(False)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrapper.append(row)
        wrapper.append(nested)
        list_item.set_child(wrapper)
        list_item.row_widget = row
        list_item.disc_widget = disc
        list_item.nested_widget = nested

    def _toc_bind(self, factory, list_item):
        item = list_item.get_item()
        if not item:
            return
        row = list_item.row_widget
        disc = list_item.disc_widget
        nested = list_item.nested_widget
        row.set_title(item.title or "")
        kids = item.children.get_n_items() > 0
        disc.set_visible(kids)
        if kids:
            disc.set_from_icon_name("pan-down-symbolic" if nested.get_visible() else "pan-end-symbolic")
        else:
            disc.set_from_icon_name(None)
        if kids and not hasattr(list_item, "_nested_view"):
            sub_factory = Gtk.SignalListItemFactory()
            sub_factory.connect("setup", self._toc_setup)
            sub_factory.connect("bind", self._toc_bind)
            sub_sel = Gtk.NoSelection(model=item.children)
            gv = Gtk.ListView(model=sub_sel, factory=sub_factory)
            gv.set_vexpand(False)
            nested.append(gv)
            list_item._nested_view = gv
        row.set_activatable(True)
        row.connect("activated", lambda r, it=item: self._on_toc_item_activated(it))

    def _on_toc_item_activated(self, toc_item):
        idx = self._find_tocitem_index(toc_item)
        if idx >= 0:
            self.current_index = idx
            self.update_navigation()
            self.display_page()
            self._set_toc_selected(toc_item)

    def _find_tocitem_index(self, toc_item):
        if not toc_item or not toc_item.href:
            return -1
        candidates = [toc_item.href, os.path.basename(toc_item.href)]
        for c in candidates:
            for i, item in enumerate(self.items):
                if (item.get_name() or "").lower() == c.lower():
                    return i
                if os.path.basename(item.get_name() or "").lower() == os.path.basename(c).lower():
                    return i
        return -1

    def _set_toc_selected(self, toc_item):
        self._clear_toc_selection()
        act = self._toc_actrows.get(toc_item)
        if act:
            act.add_css_class("selected")

    def _clear_toc_selection(self):
        for act in list(self._toc_actrows.values()):
            try:
                act.remove_css_class("selected")
            except Exception:
                pass

    def update_navigation(self):
        if not self.items or self.current_index < 0 or self.current_index >= len(self.items):
            return
        current_item = self.items[self.current_index]
        title = os.path.basename(current_item.get_name())
        self.content_title_label.set_text(title)
        if self.book_path:
            try:
                progress_fraction = self.current_index / max(len(self.items) - 1, 1)
                self.progress.set_fraction(progress_fraction)
                self.progress.set_text(f"Page {self.current_index + 1} of {len(self.items)}")
            except Exception:
                self.progress.set_fraction(0.0)
                self.progress.set_text("")

    def display_page(self):
        if not self.items or self.current_index < 0 or self.current_index >= len(self.items):
            self.scrolled.set_child(Gtk.Label(label="No content to display."))
            return
        try:
            item = self.items[self.current_index]
            content = item.get_content().decode("utf-8")
            content_type = item.get_type()
            if content_type == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(content, "html.parser")
                body = soup.find("body")
                if body:
                    content = str(body)
                else:
                    content = content
                full_html = f"""
                <html>
                <head>
                    <style>{self.css_content}</style>
                </head>
                <body>{content}</body>
                </html>
                """
                if self.webview:
                    self.webview.load_html(full_html)
                else:
                    label = Gtk.Label(label=content, wrap=True, max_width_chars=80)
                    self.scrolled.set_child(label)
            else:
                label = Gtk.Label(label=f"Cannot display item of type {content_type}.")
                self.scrolled.set_child(label)
        except Exception as e:
            print(f"Error displaying page: {e}")
            self.show_error(f"Error displaying page: {e}")

    def go_prev(self, btn):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_navigation()
            self.display_page()
            self._update_library_entry()

    def go_next(self, btn):
        if self.items and self.current_index < len(self.items) - 1:
            self.current_index += 1
            self.update_navigation()
            self.display_page()
            self._update_library_entry()

    def show_library(self):
        search_query = self.library_search_text.lower()
        entries = self.library
        if search_query:
            entries = [e for e in entries if search_query in (e.get("title") or "").lower() or search_query in (e.get("author") or "").lower() or search_query in (os.path.basename(e.get("path","")).lower())]

        if not entries:
            lbl = Gtk.Label(label="No books in library\nOpen a book to add it here.")
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.set_margin_top(40)
            self.toolbar.set_content(lbl)
            self.content_title_label.set_text("Library")
            return

        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_max_children_per_line(30)
        flowbox.set_min_children_per_line(2)
        flowbox.set_row_spacing(12)
        flowbox.set_column_spacing(12)
        flowbox.set_homogeneous(True)

        for entry in entries:
            path = entry.get("path", "")
            title = entry.get("title", os.path.basename(path))
            author = entry.get("author", "")
            cover_path = entry.get("cover", "")

            card = Gtk.Frame()
            card.set_has_frame(True)
            card_style = card.get_style_context()
            card_style.add_class("card")

            wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            card.set_child(wrapper)

            cover_area = Gtk.AspectFrame()
            cover_area.set_ratio(2.0 / 3.0)  # Standard book aspect ratio
            cover_img = Gtk.Image()
            cover_img.set_hexpand(True)
            cover_img.set_vexpand(True)
            cover_img.set_content_fit(Gtk.ContentFit.COVER)

            if cover_path and os.path.exists(cover_path):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_size(cover_path, LIB_COVER_W, LIB_COVER_H)
                    texture = Gdk.Texture.new_for_pixbuf(pix)
                    cover_img.set_from_paintable(texture)
                except Exception:
                    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, LIB_COVER_W, LIB_COVER_H)
                    pb.fill(0xf0f0f0ff)  # Light gray
                    texture = Gdk.Texture.new_for_pixbuf(pb)
                    cover_img.set_from_paintable(texture)
            else:
                pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, LIB_COVER_W, LIB_COVER_H)
                pb.fill(0xf0f0f0ff)
                texture = Gdk.Texture.new_for_pixbuf(pb)
                cover_img.set_from_paintable(texture)
            cover_area.set_child(cover_img)

            wrapper.append(cover_area)

            meta_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            meta_row.set_margin_start(6)
            meta_row.set_margin_end(6)
            meta_row.set_margin_top(6)
            meta_row.set_margin_bottom(6)

            title_label = Gtk.Label()
            title_label.set_label(self.highlight_markup(title, self.library_search_text))
            title_label.set_use_markup(True)
            title_label.set_xalign(0.0)
            title_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
            meta_row.append(title_label)

            author_label = Gtk.Label()
            author_label.set_label(self.highlight_markup(author, self.library_search_text))
            author_label.set_use_markup(True)
            author_label.add_css_class("subtitle")
            author_label.set_xalign(0.0)
            author_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
            meta_row.append(author_label)

            wrapper.append(meta_row)

            right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            right_box.set_halign(Gtk.Align.END)

            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic")
            menu_btn.add_css_class("flat")
            pop = Gtk.Popover()
            pop.set_has_arrow(False)
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            pop_box.set_margin_top(6)
            pop_box.set_margin_bottom(6)
            pop_box.set_margin_start(6)
            pop_box.set_margin_end(6)
            open_folder_btn = Gtk.Button(label="Open folder")
            open_folder_btn.add_css_class("flat")
            rem_btn = Gtk.Button(label="Remove ebook")
            rem_btn.add_css_class("flat")
            pop_box.append(open_folder_btn)
            pop_box.append(rem_btn)
            pop.set_child(pop_box)
            menu_btn.set_popover(pop)

            open_folder_btn.connect("clicked", lambda b, p=path: self._open_parent_folder(p))

            def _remove_entry(btn, p=path, coverp=cover_path):
                try:
                    dlg = Adw.MessageDialog.new(self, "Remove", f"Remove «{os.path.basename(p)}» from library?")
                    dlg.add_response("cancel", "Cancel")
                    dlg.add_response("ok", "Remove")
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
                            try:
                                d.destroy()
                            except Exception:
                                pass
                    dlg.connect("response", _on_resp)
                    dlg.present()
                except Exception:
                    pass

            rem_btn.connect("clicked", _remove_entry)
            right_box.append(menu_btn)
            meta_row.append(right_box)

            gesture = Gtk.GestureClick.new()
            def _on_click(_gesture, _n, _x, _y, p=path, resume_idx=entry.get("index", 0)):
                if p and os.path.exists(p):
                    try:
                        self._save_progress_for_library()
                    except Exception:
                        pass
                    try:
                        self.cleanup()
                    except Exception:
                        pass
                    try:
                        self.toolbar.set_content(self._reader_content_box)
                    except Exception:
                        pass
                    self.load_epub(p, resume=True, resume_index=resume_idx)

            gesture.connect("released", _on_click)
            card.add_controller(gesture)

            flowbox.append(card)

        scrolled_container = Gtk.ScrolledWindow()
        scrolled_container.set_vexpand(True)
        scrolled_container.set_child(flowbox)
        self.toolbar.set_content(scrolled_container)
        self.content_title_label.set_text("Library")
        self._disable_responsive_sidebar()

    def highlight_markup(self, text: str, query: str) -> str:
        """Return Pango markup with query substrings highlighted. Case-insensitive."""
        if not query:
            return GLib.markup_escape_text(text or "")
        q = re.escape(query)
        parts = []
        last = 0
        esc_text = text or ""
        for m in re.finditer(q, esc_text, flags=re.IGNORECASE):
            start, end = m.start(), m.end()
            parts.append(GLib.markup_escape_text(esc_text[last:start]))
            match = GLib.markup_escape_text(esc_text[start:end])
            parts.append(f'<span background="#ffd54f" foreground="#000000"><b>{match}</b></span>')
            last = end
        parts.append(GLib.markup_escape_text(esc_text[last:]))
        return "".join(parts)

    def _open_parent_folder(self, path):
        try:
            if not path:
                return
            parent = os.path.dirname(path) or path
            uri = GLib.filename_to_uri(parent, None)
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            pass

    def _save_progress_for_library(self):
        if self.book_path:
            self._update_library_entry()

    def _update_library_entry(self):
        path = self.book_path or ""
        if not path:
            return
        title = self.book_title.get_text() or os.path.basename(path)
        author = self.book_author.get_text() or ""
        cover_src = self.last_cover_path
        cover_dst = None
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
        found_entry = None
        for e in list(self.library):
            if e.get("path") == path:
                e["title"] = title
                e["author"] = author
                if cover_dst:
                    e["cover"] = cover_dst
                e["index"] = int(self.current_index)
                e["progress"] = float(self.progress.get_fraction() or 0.0)
                found = True
                found_entry = e
                break

        if not found:
            new_entry = {
                "path": path,
                "title": title,
                "author": author,
                "cover": cover_dst,
                "index": int(self.current_index),
                "progress": float(self.progress.get_fraction() or 0.0)
            }
            self.library.append(new_entry)
            save_library(self.library)
        else:
            if found_entry is not None:
                e = found_entry
                e["index"] = int(self.current_index)
                e["progress"] = float(self.progress.get_fraction() or 0.0)
                save_library(self.library)

    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._last_was_narrow = None

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        try:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)
        except Exception as e:
            print(f"Error disabling responsive sidebar: {e}")

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
        if getattr(self, "toc_root_store", None):
            try:
                self.toc_root_store.remove_all()
            except Exception:
                pass


class TocItem(GObject.Object):
    def __init__(self, title, href):
        super().__init__()
        self.title = title
        self.href = href
        self.children = Gio.ListStore(item_type=TocItem)


def save_library(library):
    lib_path = os.path.expanduser("~/.local/share/epub_reader/library.json")
    os.makedirs(os.path.dirname(lib_path), exist_ok=True)
    import json
    with open(lib_path, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2)


def load_library():
    lib_path = os.path.expanduser("~/.local/share/epub_reader/library.json")
    if os.path.exists(lib_path):
        import json
        try:
            with open(lib_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer")
        self.create_action("quit", self.quit, ["<primary>q"])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EpubReaderWindow(application=self)
            win.library = load_library()
        win.present()


def main():
    import sys
    app = Application()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
