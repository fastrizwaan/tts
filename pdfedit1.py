#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Robust PDF editor using PyGObject (GTK4 + Libadwaita) and PyMuPDF (fitz)
# Features:
# - Open / Save / Save As
# - Thumbnails sidebar with page navigation
# - Zoom, rotate page, add blank page, delete page
# - Tools: Select (annotations), Text, Rectangle, Highlight, Underline, Strikeout, Freehand (Ink)
# - Click existing annotation to select; Delete key removes it
# - Drag-select text area for highlight/underline/strikeout (word-aware using PyMuPDF words)
# - Undo/Redo (in-memory snapshots)
# - Autosave backup every 2 minutes next to the file
# Notes: Requires PyMuPDF (pip install pymupdf) and GI packages for GTK4/libadwaita.

import gi
import os
import sys
import math
from dataclasses import dataclass, field
from typing import List, Optional

# GI deps
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, GdkPixbuf

# PDF engine
try:
    import fitz  # PyMuPDF
except Exception as e:
    print("PyMuPDF (pymupdf) is required: pip install pymupdf", file=sys.stderr)
    raise


# ----------------------- Utility types -----------------------
@dataclass
class ViewState:
    page_index: int = 0
    zoom: float = 1.15
    tool: str = "select"  # select|text|rect|highlight|underline|strikeout|ink

@dataclass
class DragState:
    dragging: bool = False
    start_x: float = 0.0
    start_y: float = 0.0
    cur_x: float = 0.0
    cur_y: float = 0.0
    points: List[tuple] = field(default_factory=list)  # for ink


# ----------------------- Main Window -----------------------
class PDFEditorWindow(Adw.ApplicationWindow):
    AUTOSAVE_SECS = 120

    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("PDF Editor")
        self.set_default_size(1280, 860)

        self.doc: Optional[fitz.Document] = None
        self.path: Optional[str] = None
        self.view = ViewState()
        self.drag = DragState()
        self.page_pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self.thumbs = []  # list of (pixbuf, page_index)
        self.selected_annot = None

        # style state
        self.text_color = (0, 0, 0)
        self.text_size = 12
        self.stroke_color = (1.0, 0.0, 0.0)
        self.stroke_width = 1.5
        self.hl_color = (1.0, 1.0, 0.0)
        self.hl_opacity = 0.35
        self.ink_color = (0.0, 0.0, 0.0)
        self.ink_width = 1.0

        # undo/redo (snapshots of full PDF bytes)
        self.undo_stack: List[bytes] = []
        self.redo_stack: List[bytes] = []

        self.autosave_id = None

        self._build_ui()
        self._bind_actions()

    # ----------------------- UI -----------------------
    def _build_ui(self):
        tv = Adw.ToolbarView()
        self.set_content(tv)

        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)

        # Left: file buttons
        btn_open = Gtk.Button.new_from_icon_name("document-open-symbolic")
        btn_open.set_tooltip_text("Open")
        btn_open.connect("clicked", self.on_open_clicked)
        hb.pack_start(btn_open)

        btn_save = Gtk.Button.new_from_icon_name("document-save-symbolic")
        btn_save.set_tooltip_text("Save")
        btn_save.connect("clicked", self.on_save_clicked)
        hb.pack_start(btn_save)

        btn_save_as = Gtk.Button.new_from_icon_name("document-save-as-symbolic")
        btn_save_as.set_tooltip_text("Save As…")
        btn_save_as.connect("clicked", self.on_save_as_clicked)
        hb.pack_start(btn_save_as)

        # Center: page nav
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.btn_prev = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.btn_prev.connect("clicked", self.on_prev_page)
        self.btn_next = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.btn_next.connect("clicked", self.on_next_page)
        self.page_label = Gtk.Label(label="– / –")
        nav_box.append(self.btn_prev)
        nav_box.append(self.page_label)
        nav_box.append(self.btn_next)
        hb.set_title_widget(nav_box)

        # Right: zoom & tools
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        b_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        b_out.connect("clicked", lambda *_: self._change_zoom(0.9))
        b_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        b_in.connect("clicked", lambda *_: self._change_zoom(1.1))
        self.tool_drop = Gtk.DropDown.new_from_strings([
            "select","text","rect","highlight","underline","strikeout","ink"
        ])
        self.tool_drop.connect("notify::selected-item", self.on_tool_changed)
        right_box.append(b_out); right_box.append(b_in); right_box.append(self.tool_drop)
        hb.pack_end(right_box)

        # Content: Paned -> left thumbnails, right canvas
        paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        # Wrap content in a ToastOverlay for non-blocking toasts
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(paned)
        tv.set_content(self.toast_overlay)

        # Thumbnails panel
        self.thumb_store = Gio.ListStore.new(GObjectPair)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._thumb_setup)
        factory.connect("bind", self._thumb_bind)
        # Gtk.ListView requires a GtkSelectionModel; wrap the ListStore
        self.thumb_sel = Gtk.SingleSelection(model=self.thumb_store)
        self.thumb_list = Gtk.ListView(model=self.thumb_sel, factory=factory)
        self.thumb_list.connect("activate", self._on_thumb_activate)
        self.thumb_list.set_vexpand(True)
        sc_left = Gtk.ScrolledWindow(); sc_left.set_min_content_width(180)
        sc_left.set_child(self.thumb_list)
        sc_left.set_vexpand(True)
        paned.set_start_child(sc_left)
        paned.set_resize_start_child(False)

        # Right: header for page ops + drawing area in scroller
        right_box2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        paned.set_end_child(right_box2)

        # Page ops row
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_add = Gtk.Button.new_from_icon_name("list-add-symbolic"); btn_add.set_tooltip_text("Add blank page")
        btn_add.connect("clicked", self.on_add_page)
        btn_del = Gtk.Button.new_from_icon_name("user-trash-symbolic"); btn_del.set_tooltip_text("Delete page")
        btn_del.connect("clicked", self.on_delete_page)
        btn_rot = Gtk.Button.new_from_icon_name("object-rotate-right-symbolic"); btn_rot.set_tooltip_text("Rotate 90°")
        btn_rot.connect("clicked", self.on_rotate_page)
        row.append(btn_add); row.append(btn_del); row.append(btn_rot)
        right_box2.append(row)

        # Scroller + DrawingArea
        self.scroller = Gtk.ScrolledWindow()
        right_box2.set_vexpand(True)
        right_box2.append(self.scroller)

        self.darea = Gtk.DrawingArea()
        self.darea.set_draw_func(self.on_draw)
        self.darea.set_vexpand(True); self.darea.set_hexpand(True)
        self.scroller.set_child(self.darea)
        self.scroller.set_vexpand(True); self.scroller.set_hexpand(True)

        # Gestures
        self.click = Gtk.GestureClick()
        self.click.connect("pressed", self.on_click)
        self.darea.add_controller(self.click)

        self.drag_g = Gtk.GestureDrag()
        self.drag_g.connect("drag-begin", self.on_drag_begin)
        self.drag_g.connect("drag-update", self.on_drag_update)
        self.drag_g.connect("drag-end", self.on_drag_end)
        self.darea.add_controller(self.drag_g)

        # Key events
        self.add_controller(self._key_controller())

    def _key_controller(self):
        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self.on_key)
        return kc

    # ----------------------- Actions -----------------------
    def _bind_actions(self):
        app = self.get_application()
        def add_action(name, cb, accels=None):
            act = Gio.SimpleAction.new(name, None); act.connect("activate", lambda *_: cb())
            app.add_action(act)
            if accels:
                app.set_accels_for_action(f"app.{name}", accels)
        add_action("open", lambda: self.on_open_clicked(None), ["<Primary>o"]) 
        add_action("save", lambda: self.on_save_clicked(None), ["<Primary>s"]) 
        add_action("undo", self.undo, ["<Primary>z"]) 
        add_action("redo", self.redo, ["<Primary><Shift>z"]) 
        add_action("zoom_in", lambda: self._change_zoom(1.1), ["<Primary>equal","<Primary>plus"]) 
        add_action("zoom_out", lambda: self._change_zoom(0.9), ["<Primary>minus"]) 
        add_action("next", lambda: self.on_next_page(None), ["Page_Down"]) 
        add_action("prev", lambda: self.on_prev_page(None), ["Page_Up"]) 

    # ----------------------- File ops -----------------------
    def on_open_clicked(self, _btn):
        dialog = Gtk.FileDialog()
        f = Gtk.FileFilter(); f.set_name("PDF"); f.add_suffix("pdf"); dialog.set_default_filter(f)
        dialog.open(self, None, self._open_done)

    def _open_done(self, dialog, res):
        try:
            file = dialog.open_finish(res)
        except GLib.Error:
            return
        path = file.get_path()
        if not path:
            return
        try:
            self._load_pdf(path)
        except Exception as e:
            self._error(str(e))

    def _load_pdf(self, path):
        self._stop_autosave()
        if self.doc:
            try: self.doc.close()
            except Exception: pass
        self.doc = fitz.open(path)
        self.path = path
        self.view.page_index = 0
        self.view.zoom = 1.15
        self.undo_stack.clear(); self.redo_stack.clear()
        self._render_page(); self._update_page_label(); self._set_title(); self._rebuild_thumbs()
        self._start_autosave()

    def on_save_clicked(self, _btn):
        if not self.doc: return
        if self.path:
            try:
                self.doc.save(self.path, incremental=True, deflate=True)
                self._toast("Saved")
            except Exception as e:
                self._error(str(e))
        else:
            self.on_save_as_clicked(_btn)

    def on_save_as_clicked(self, _btn):
        if not self.doc: return
        dialog = Gtk.FileDialog(); dialog.set_initial_name("edited.pdf")
        dialog.save(self, None, self._saveas_done)

    def _saveas_done(self, dialog, res):
        try:
            file = dialog.save_finish(res)
        except GLib.Error:
            return
        path = file.get_path()
        if not path: return
        try:
            self.doc.save(path)
            self.path = path
            self._set_title(); self._toast("Saved as")
        except Exception as e:
            self._error(str(e))

    # ----------------------- Autosave -----------------------
    def _start_autosave(self):
        if self.autosave_id:
            GLib.source_remove(self.autosave_id)
        self.autosave_id = GLib.timeout_add_seconds(self.AUTOSAVE_SECS, self._do_autosave)

    def _stop_autosave(self):
        if self.autosave_id:
            GLib.source_remove(self.autosave_id)
            self.autosave_id = None

    def _do_autosave(self):
        if not self.doc or not self.path: return True
        try:
            tmp = os.path.splitext(self.path)[0] + ".autosave.pdf"
            self.doc.save(tmp, incremental=False)
        except Exception:
            pass
        return True

    # ----------------------- Rendering -----------------------
    def _render_page(self):
        if not self.doc: return
        page = self.doc[self.view.page_index]
        m = fitz.Matrix(self.view.zoom, self.view.zoom)
        pm = page.get_pixmap(matrix=m, alpha=False)
        data = pm.samples
        width, height, stride = pm.width, pm.height, pm.stride
        self.page_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride, None, None
        )
        self.darea.set_content_width(width)
        self.darea.set_content_height(height)
        self.darea.queue_draw()

    def on_draw(self, area, ctx, w, h):
        if self.page_pixbuf:
            Gdk.cairo_set_source_pixbuf(ctx, self.page_pixbuf, 0, 0)
            ctx.paint()
        # Selection rectangle or ink preview
        if self.drag.dragging:
            if self.view.tool == "ink":
                ctx.set_line_width(self.ink_width)
                ctx.set_source_rgba(*self.ink_color, 0.85)
                pts = self.drag.points
                if len(pts) > 1:
                    ctx.move_to(*pts[0])
                    for p in pts[1:]:
                        ctx.line_to(*p)
                    ctx.stroke()
            elif self.view.tool in ("rect","highlight","underline","strikeout"):
                x0, y0 = self.drag.start_x, self.drag.start_y
                x1, y1 = self.drag.cur_x, self.drag.cur_y
                rx, ry = min(x0, x1), min(y0, y1)
                rw, rh = abs(x1 - x0), abs(y1 - y0)
                if self.view.tool == "rect":
                    ctx.set_source_rgba(*self.stroke_color, 0.9)
                    ctx.set_line_width(self.stroke_width)
                    ctx.rectangle(rx, ry, rw, rh)
                    ctx.stroke()
                else:
                    col = self.hl_color
                    alpha = self.hl_opacity
                    ctx.set_source_rgba(*col, alpha)
                    ctx.rectangle(rx, ry, rw, rh)
                    ctx.fill()
        # Annot selection
        if self.selected_annot:
            rect = self.selected_annot.rect * self.view.zoom
            ctx.set_source_rgba(0.2, 0.6, 1.0, 0.8)
            ctx.set_line_width(1.0)
            ctx.rectangle(rect.x0, rect.y0, rect.width, rect.height)
            ctx.stroke()

    # ----------------------- Thumbnails -----------------------
    def _rebuild_thumbs(self):
        self.thumb_store.remove_all()
        self.thumbs = []
        if not self.doc: return
        for i in range(len(self.doc)):
            page = self.doc[i]
            pm = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), alpha=False)
            pb = GdkPixbuf.Pixbuf.new_from_data(pm.samples, GdkPixbuf.Colorspace.RGB, False, 8, pm.width, pm.height, pm.stride, None, None)
            self.thumbs.append((pb, i))
            self.thumb_store.append(GObjectPair(pb, i))

    def _thumb_setup(self, factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        img = Gtk.Image(); lbl = Gtk.Label()
        box.append(img); box.append(lbl)
        list_item.set_child(box)

    def _thumb_bind(self, factory, list_item):
        pair: GObjectPair = list_item.get_item()
        box = list_item.get_child()
        img, lbl = box.get_first_child(), box.get_last_child()
        img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pair.pixbuf))
        lbl.set_text(str(pair.index + 1))

    def _on_thumb_activate(self, listview, position):
        self.view.page_index = position
        self.selected_annot = None
        self._render_page(); self._update_page_label()

    # ----------------------- Gestures -----------------------
    def on_tool_changed(self, drop, _pspec):
        item = drop.get_selected_item()
        if item:
            self.view.tool = item.get_string()
            self.selected_annot = None
            self.darea.queue_draw()

    def on_click(self, gesture, n_press, x, y):
        if not self.doc: return
        if self.view.tool == "text" and n_press == 1:
            self._place_text_dialog(x, y)
        elif self.view.tool == "select" and n_press == 1:
            self._hit_test_annotation(x, y)

    def on_drag_begin(self, gesture, x, y):
        if not self.doc: return
        self.drag = DragState(True, x, y, x, y, [])
        if self.view.tool == "ink":
            self.drag.points = [(x, y)]
        self.darea.queue_draw()

    def on_drag_update(self, gesture, x, y):
        if not self.doc or not self.drag.dragging: return
        self.drag.cur_x, self.drag.cur_y = x, y
        if self.view.tool == "ink":
            self.drag.points.append((x, y))
        self.darea.queue_draw()

    def on_drag_end(self, gesture, x, y):
        if not self.doc or not self.drag.dragging: return
        self.drag.cur_x, self.drag.cur_y = x, y
        self._commit_drag()
        self.drag.dragging = False
        self.darea.queue_draw()

    # ----------------------- Commit operations -----------------------
    def _snapshot(self):
        if not self.doc: return
        try:
            self.undo_stack.append(self.doc.tobytes())
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
        except Exception:
            pass

    def undo(self):
        if not self.undo_stack: return
        data = self.undo_stack.pop()
        try:
            cur = self.doc.tobytes(); self.redo_stack.append(cur)
        except Exception:
            pass
        self.doc = fitz.open(stream=data, filetype="pdf")
        # keep same page if possible
        self.view.page_index = min(self.view.page_index, len(self.doc)-1)
        self._render_page(); self._rebuild_thumbs(); self._update_page_label()

    def redo(self):
        if not self.redo_stack: return
        data = self.redo_stack.pop()
        try:
            self.undo_stack.append(self.doc.tobytes())
        except Exception:
            pass
        self.doc = fitz.open(stream=data, filetype="pdf")
        self.view.page_index = min(self.view.page_index, len(self.doc)-1)
        self._render_page(); self._rebuild_thumbs(); self._update_page_label()

    def _commit_drag(self):
        page = self.doc[self.view.page_index]
        if self.view.tool == "rect":
            x0, y0, x1, y1 = self._sel_rect()
            if x1 - x0 < 2 or y1 - y0 < 2: return
            self._snapshot()
            page.draw_rect(self._to_pdf_rect(page, x0, y0, x1-x0, y1-y0), color=self.stroke_color, width=self.stroke_width)
        elif self.view.tool in ("highlight","underline","strikeout"):
            x0, y0, x1, y1 = self._sel_rect()
            if x1 - x0 < 2 or y1 - y0 < 2: return
            self._snapshot()
            rect_pdf = self._to_pdf_rect(page, x0, y0, x1-x0, y1-y0)
            quads = self._quads_for_rect(page, rect_pdf)
            if not quads: return
            if self.view.tool == "highlight":
                annot = page.add_highlight_annot(quads)
                r,g,b = self.hl_color
                annot.set_colors(stroke=(r,g,b), fill=(r,g,b)); annot.set_opacity(self.hl_opacity); annot.update()
            elif self.view.tool == "underline":
                annot = page.add_underline_annot(quads); annot.update()
            else:
                annot = page.add_strikeout_annot(quads); annot.update()
        elif self.view.tool == "ink":
            pts_pdf = [self._to_pdf_point(page, px, py) for (px,py) in self.drag.points]
            if len(pts_pdf) < 2: return
            self._snapshot()
            annot = page.add_ink_annot([pts_pdf])
            annot.set_colors(stroke=self.ink_color); annot.set_border(width=self.ink_width); annot.update()
        self._render_page(); self._rebuild_thumbs()

    # ----------------------- Tools helpers -----------------------
    def _sel_rect(self):
        x0, y0 = self.drag.start_x, self.drag.start_y
        x1, y1 = self.drag.cur_x, self.drag.cur_y
        return min(x0,x1), min(y0,y1), max(x0,x1), max(y0,y1)

    def _quads_for_rect(self, page, rect_pdf):
        words = page.get_text("words")  # x0,y0,x1,y1,word, block, line, word_no
        quads = []
        for w in words:
            r = fitz.Rect(w[:4])
            if r.intersects(rect_pdf):
                quads.append(fitz.Quad(r))
        return quads

    def _place_text_dialog(self, x, y):
        dlg = Adw.AlertDialog()
        dlg.set_heading("Insert Text")
        entry = Gtk.Entry(); entry.set_hexpand(True); entry.set_activates_default(True)
        size_adj = Gtk.Adjustment(lower=6, upper=96, value=self.text_size, step_increment=1, page_increment=10)
        size_spin = Gtk.SpinButton(adjustment=size_adj, climb_rate=1, digits=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Size:")); row.append(size_spin)
        box.append(entry); box.append(row)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Insert")
        dlg.set_default_response("ok")
        dlg.connect("response", lambda d, r: self._place_text_response(d, r, x, y, entry, size_spin))
        dlg.present(self)

    def _place_text_response(self, dlg, response, x, y, entry, size_spin):
        if response != "ok":
            dlg.close(); return
        text = entry.get_text() or ""; size = size_spin.get_value_as_int(); dlg.close()
        if not text: return
        page = self.doc[self.view.page_index]
        self._snapshot()
        px, py = self._to_pdf_point(page, x, y)
        rect = fitz.Rect(px, py, px + 300, py + size * 1.6)
        page.insert_textbox(rect, text, fontsize=size, color=self.text_color)
        self._render_page(); self._rebuild_thumbs()

    def _hit_test_annotation(self, x, y):
        page = self.doc[self.view.page_index]
        p_pdf = self._to_pdf_point(page, x, y)
        self.selected_annot = None
        for a in page.annots() or []:
            if a.rect.contains(p_pdf):
                self.selected_annot = a
                break
        self.darea.queue_draw()

    # ----------------------- Page ops -----------------------
    def on_prev_page(self, _btn):
        if not self.doc: return
        if self.view.page_index > 0:
            self.view.page_index -= 1
            self.selected_annot = None
            self._render_page(); self._update_page_label()

    def on_next_page(self, _btn):
        if not self.doc: return
        if self.view.page_index < len(self.doc) - 1:
            self.view.page_index += 1
            self.selected_annot = None
            self._render_page(); self._update_page_label()

    def on_add_page(self, _btn):
        if not self.doc: return
        self._snapshot()
        rect = fitz.Rect(0, 0, 595, 842)  # A4 portrait in points
        self.doc.new_page(pno=self.view.page_index+1, width=rect.width, height=rect.height)
        self._rebuild_thumbs(); self._update_page_label(); self._toast("Page added")

    def on_delete_page(self, _btn):
        if not self.doc or len(self.doc) == 0: return
        self._snapshot()
        self.doc.delete_page(self.view.page_index)
        self.view.page_index = max(0, min(self.view.page_index, len(self.doc)-1))
        self._rebuild_thumbs(); self._render_page(); self._update_page_label()

    def on_rotate_page(self, _btn):
        if not self.doc: return
        self._snapshot()
        page = self.doc[self.view.page_index]
        page.set_rotation((page.rotation + 90) % 360)
        self._render_page(); self._rebuild_thumbs()

    # ----------------------- Helpers -----------------------
    def _to_pdf_point(self, page, vx, vy):
        z = self.view.zoom
        return (vx / z, vy / z)

    def _to_pdf_rect(self, page, vx, vy, vw, vh):
        z = self.view.zoom
        return fitz.Rect(vx / z, vy / z, (vx + vw) / z, (vy + vh) / z)

    def _change_zoom(self, factor):
        if not self.doc: return
        self.view.zoom = max(0.2, min(6.0, self.view.zoom * factor))
        self._render_page()

    def _update_page_label(self):
        if not self.doc:
            self.page_label.set_text("– / –")
        else:
            self.page_label.set_text(f"{self.view.page_index + 1} / {len(self.doc)}")

    def _set_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.set_title(f"PDF Editor — {name}")

    def _toast(self, text):
        if getattr(self, "toast_overlay", None) is None:
            return
        toast = Adw.Toast.new(text)
        self.toast_overlay.add_toast(toast)

    def _error(self, text):
        dlg = Adw.AlertDialog()
        dlg.set_heading("Error")
        dlg.set_body(text)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        dlg.present(self)

    def on_key(self, _ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Delete and self.selected_annot:
            self._snapshot()
            try:
                self.selected_annot.destroy()
            except Exception:
                pass
            self.selected_annot = None
            self._render_page(); self._rebuild_thumbs()
            return True
        if keyval == Gdk.KEY_Escape and self.drag.dragging:
            self.drag.dragging = False; self.darea.queue_draw(); return True
        return False


# Simple GObject pair to store thumbnail + index
from gi.repository import GObject
class GObjectPair(GObject.GObject):
    pixbuf = GObject.Property(type=GdkPixbuf.Pixbuf)
    index = GObject.Property(type=int)
    def __init__(self, pixbuf=None, index=0):
        super().__init__()
        self.pixbuf = pixbuf
        self.index = index


# ----------------------- Application -----------------------
class PDFEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.example.RobustPDFEditor", flags=Gio.ApplicationFlags.FLAGS_NONE)
        Adw.init()

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = PDFEditorWindow(self)
        win.present()


def main():
    app = PDFEditorApp()
    sys.exit(app.run(sys.argv))

if __name__ == "__main__":
    main()
