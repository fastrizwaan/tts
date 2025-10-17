# library.py
import os, json, hashlib, shutil, glob, urllib.parse
from gi.repository import GLib, Gio, Gtk, Adw, GdkPixbuf, Gdk, Pango

# cover target size for sidebar (small)
COVER_W, COVER_H = 70, 100

# persistent library locations & library cover save size
LIBRARY_DIR = os.path.join(GLib.get_user_data_dir(), "epubviewer")
LIBRARY_FILE = os.path.join(LIBRARY_DIR, "library.json")
COVERS_DIR = os.path.join(LIBRARY_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)

# persistent cover saved size (bigger so library shows large covers)
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

def compute_cover_dst_for_path(book_path, cover_src):
    """Return a deterministic cover filename for a given book path and cover source path."""
    if not cover_src:
        return None
    try:
        h = hashlib.sha1(book_path.encode("utf-8")).hexdigest()[:12]
        ext = os.path.splitext(cover_src)[1].lower() or ".png"
        return os.path.join(COVERS_DIR, f"{h}{ext}")
    except Exception:
        return None

class LibraryMixin:
    """
    Mixin for EPubViewer. Expects the host to provide the UI attributes used below:
      - toolbar, cover_image, book_title, book_author, content_title_label,
      - open_btn, search_toggle_btn, library_search_revealer, library_search_entry,
      - progress, _reader_content_box, _safe_set_search_text is also implemented here.
    The mixin uses module-level helpers (load_library/save_library/etc).
    """

    # small convenience to get entries ordered (same logic as original)
    def _get_library_entries_for_display(self):
        entries = list(reversed(self.library or []))
        if not entries:
            return entries
        try:
            if getattr(self, "book_path", None):
                for i, e in enumerate(entries):
                    try:
                        if os.path.abspath(e.get("path", "")) == os.path.abspath(self.book_path or ""):
                            if i != 0:
                                entries.insert(0, entries.pop(i))
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        return entries

    def _is_loaded_entry(self, entry):
        try:
            if not entry: return False
            if not getattr(self, "book_path", None): return False
            return os.path.abspath(entry.get("path", "")) == os.path.abspath(self.book_path or "")
        except Exception:
            return False

    def _safe_set_search_text(self, text: str):
        try:
            if text is None:
                text = ""
            if getattr(self, "library_search_entry", None) and self.library_search_entry.get_has_focus():
                return
            cur = ""
            try:
                cur = self.library_search_entry.get_text() or ""
            except Exception:
                cur = ""
            if cur == text:
                return
            try:
                self.library_search_entry.set_text(text)
                pos = len(text)
                try: self.library_search_entry.set_position(pos)
                except Exception: pass
            except Exception:
                pass
        except Exception:
            pass

    def _on_library_search_changed(self, arg):
        try:
            if isinstance(arg, str):
                text = arg
            else:
                text = arg.get_text() if hasattr(arg, "get_text") else str(arg or "")
            self.library_search_text = (text or "").strip()
            self.show_library()
        except Exception:
            pass

    def _toggle_library_search(self, *_):
        reveal = not self.library_search_revealer.get_reveal_child()
        self.library_search_revealer.set_reveal_child(reveal)
        if not reveal:
            try:
                if getattr(self, "_lib_search_handler_id", None):
                    self.library_search_entry.handler_block(self._lib_search_handler_id)
                self.library_search_entry.set_text("")
                self.library_search_text = ""
                self.show_library()
            finally:
                try:
                    if getattr(self, "_lib_search_handler_id", None):
                        self.library_search_entry.handler_unblock(self._lib_search_handler_id)
                except Exception:
                    pass
        else:
            self.library_search_entry.grab_focus()

    def on_library_clicked(self, *_):
        try:
            if getattr(self, "book", None):
                try:
                    self.content_sidebar_toggle.set_visible(False)
                    self.split.set_show_sidebar(False)
                    self.split.set_collapsed(False)
                except Exception:
                    pass
            self.show_library()
        except Exception:
            pass

    def _stop_reading(self, path=None):
        try:
            if path and getattr(self, "book_path", None) and os.path.abspath(path) != os.path.abspath(self.book_path):
                return
            try: self._save_progress_for_library()
            except Exception: pass
            try: self.cleanup()
            except Exception: pass
            try:
                self.book_path = None
                self.open_btn.set_visible(True)
                self.search_toggle_btn.set_visible(True)
                try: self.content_sidebar_toggle.set_visible(False)
                except Exception: pass
            except Exception:
                pass
            try: self.show_library()
            except Exception: pass
        except Exception:
            pass

    def _create_rounded_cover_texture(self, cover_path, width, height, radius=10):
        try:
            original_pixbuf = GdkPixbuf.Pixbuf.new_from_file(cover_path)
            pixbuf = original_pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            import cairo
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            context = cairo.Context(surface)
            # draw rounded rectangle (approx)
            context.arc(radius, radius, radius, 3.14159, 3 * 3.14159 / 2)
            context.arc(width - radius, radius, radius, 3 * 3.14159 / 2, 0)
            context.arc(width - radius, height - radius, radius, 0, 3.14159 / 2)
            context.arc(radius, height - radius, radius, 3.14159 / 2, 3.14159)
            context.close_path()
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.clip()
            context.paint()
            surface_bytes = surface.get_data()
            gbytes = GLib.Bytes.new(surface_bytes)
            texture = Gdk.MemoryTexture.new(
                width, height,
                Gdk.MemoryFormat.B8G8R8A8,
                gbytes,
                surface.get_stride()
            )
            return texture
        except Exception as e:
            print(f"Error creating rounded texture: {e}")
            return None

    def show_library(self):
        # show library UI (keeps behaviour from original)
        self._disable_responsive_sidebar()
        try:
            self.split.set_show_sidebar(False)
        except Exception: pass
        try:
            self.content_sidebar_toggle.set_visible(False)
        except Exception: pass
        try:
            self.open_btn.set_visible(True)
        except Exception: pass
        try:
            self.search_toggle_btn.set_visible(True)
            self.library_search_revealer.set_reveal_child(bool(getattr(self, "library_search_text", "")))
            try:
                if getattr(self, "_lib_search_handler_id", None):
                    self.library_search_entry.handler_block(self._lib_search_handler_id)
                self._safe_set_search_text(getattr(self, "library_search_text", ""))
            finally:
                try:
                    if getattr(self, "_lib_search_handler_id", None):
                        self.library_search_entry.handler_unblock(self._lib_search_handler_id)
                except Exception:
                    pass
        except Exception: pass

        # hide columns menu in library mode
        try:
            self.columns_menu_button.set_visible(False)
        except Exception:
            pass

        query = (getattr(self, "library_search_text", "") or "").strip().lower()
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
                texture = self._create_rounded_cover_texture(cover, 140, 210, radius=10)
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
            # highlight_markup not in this module; host must provide it or use plain text
            try:
                t.set_markup(self.highlight_markup(title, getattr(self, "library_search_text", "")))
            except Exception:
                t.set_text(title)
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
            try:
                a.set_markup(self.highlight_markup(author, getattr(self, "library_search_text", "")))
            except Exception:
                a.set_text(author)
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
                        try:
                            if resp == "ok":
                                self.library = [ee for ee in self.library if ee.get("path") != p]
                                try:
                                    if coverp and os.path.exists(coverp):
                                        try: os.remove(coverp)
                                        except Exception: pass
                                except Exception:
                                    pass
                                save_library(self.library)
                                self.show_library()
                        finally:
                            try: d.destroy()
                            except Exception: pass
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
                    try: self._save_progress_for_library()
                    except Exception: pass
                    try: self.cleanup()
                    except Exception: pass
                    try: self.toolbar.set_content(self._reader_content_box)
                    except Exception: pass
                    self.load_epub(p, resume=True, resume_index=resume_idx)
            gesture.connect("released", _on_click)
            card.add_controller(gesture)
            card.add_css_class("clickable")

            flowbox.append(card)

        scroll = Gtk.ScrolledWindow(); scroll.set_child(flowbox); scroll.set_vexpand(True); scroll.set_hexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); container.append(scroll)
        self.toolbar.set_content(container); self.content_title_label.set_text("Library")

    def _update_library_entry(self):
        path = getattr(self, "book_path", "") or ""
        if not path: return
        title = getattr(self, "book_title", None).get_text() if getattr(self, "book_title", None) else os.path.basename(path)
        author = getattr(self, "book_author", None).get_text() if getattr(self, "book_author", None) else ""
        cover_src = getattr(self, "last_cover_path", None); cover_dst = None
        if cover_src and os.path.exists(cover_src):
            try:
                cover_dst = compute_cover_dst_for_path(path, cover_src)
                if cover_dst:
                    try:
                        pix = GdkPixbuf.Pixbuf.new_from_file(cover_src)
                        scaled = pix.scale_simple(LIB_COVER_W, LIB_COVER_H, GdkPixbuf.InterpType.BILINEAR)
                        ext = os.path.splitext(cover_dst)[1].lstrip(".")
                        scaled.savev(cover_dst, ext, [], [])
                    except Exception:
                        try: shutil.copy2(cover_src, cover_dst)
                        except Exception: pass
            except Exception:
                cover_dst = None
        found = False
        found_entry = None
        for e in list(self.library):
            if e.get("path") == path:
                e["title"] = title; e["author"] = author
                if cover_dst: e["cover"] = cover_dst
                e["index"] = int(getattr(self, "current_index", 0)); e["progress"] = float(getattr(self.progress, "get_fraction", lambda: 0.0)() or 0.0)
                found = True; found_entry = e; break
        if found and found_entry is not None:
            try:
                self.library = [ee for ee in self.library if ee.get("path") != path]
                self.library.append(found_entry)
            except Exception:
                pass
        if not found:
            entry = {"path": path, "title": title, "author": author, "cover": cover_dst, "index": int(getattr(self, "current_index", 0)), "progress": float(getattr(self.progress, "get_fraction", lambda: 0.0)() or 0.0)}
            self.library.append(entry)
        if len(self.library) > 200: self.library = self.library[-200:]
        save_library(self.library)

    def _save_progress_for_library(self):
        if not getattr(self, "book_path", None): return
        changed = False
        for e in self.library:
            if e.get("path") == self.book_path:
                e["index"] = int(getattr(self, "current_index", 0)); e["progress"] = float(getattr(self.progress, "get_fraction", lambda: 0.0)() or 0.0)
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

