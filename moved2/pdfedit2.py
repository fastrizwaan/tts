#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# PDF editor with text insertion and moving, using PyGObject (GTK4 + Libadwaita) + PyMuPDF

import gi, os, sys
from dataclasses import dataclass, field
from typing import List, Optional

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, Gdk, GdkPixbuf

try:
    import fitz  # PyMuPDF
except Exception:
    print("PyMuPDF (pymupdf) is required: pip install pymupdf", file=sys.stderr)
    raise

@dataclass
class ViewState:
    page_index: int = 0
    zoom: float = 1.2
    tool: str = "select"  # select|text|move-text

@dataclass
class DragState:
    dragging: bool = False
    start_x: float = 0.0
    start_y: float = 0.0
    cur_x: float = 0.0
    cur_y: float = 0.0

class PDFEditorWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_default_size(1280, 860)
        self.view = ViewState()
        self.drag = DragState()
        self.doc: Optional[fitz.Document] = None
        self.path: Optional[str] = None
        self.selected_annot = None
        self.page_pixbuf = None
        self._build_ui()

    def _build_ui(self):
        tv = Adw.ToolbarView(); self.set_content(tv)
        hb = Adw.HeaderBar(); tv.add_top_bar(hb)
        openb = Gtk.Button.new_from_icon_name("document-open-symbolic"); openb.connect("clicked", self.on_open); hb.pack_start(openb)
        self.tool_drop = Gtk.DropDown.new_from_strings(["select","text","move-text"])
        self.tool_drop.connect("notify::selected-item", self.on_tool_changed); hb.pack_end(self.tool_drop)
        self.scroller = Gtk.ScrolledWindow(); tv.set_content(self.scroller)
        self.darea = Gtk.DrawingArea(); self.darea.set_draw_func(self.on_draw)
        self.scroller.set_child(self.darea)
        click = Gtk.GestureClick(); click.connect("pressed", self.on_click); self.darea.add_controller(click)
        drag = Gtk.GestureDrag(); drag.connect("drag-begin", self.on_drag_begin); drag.connect("drag-update", self.on_drag_update); drag.connect("drag-end", self.on_drag_end); self.darea.add_controller(drag)

    def on_open(self, *_):
        dlg = Gtk.FileDialog(); dlg.open(self, None, self._open_done)
    def _open_done(self, dlg, res):
        try: f = dlg.open_finish(res)
        except: return
        path = f.get_path(); self.doc = fitz.open(path); self.path = path; self._render()

    def _render(self):
        if not self.doc: return
        page = self.doc[self.view.page_index]
        pm = page.get_pixmap(matrix=fitz.Matrix(self.view.zoom, self.view.zoom), alpha=False, annots=True)
        self.page_pixbuf = GdkPixbuf.Pixbuf.new_from_data(pm.samples, GdkPixbuf.Colorspace.RGB, False, 8, pm.width, pm.height, pm.stride, None, None)
        self.darea.set_content_width(pm.width); self.darea.set_content_height(pm.height)
        self.darea.queue_draw()

    def on_draw(self, area, cr, w, h):
        if self.page_pixbuf:
            Gdk.cairo_set_source_pixbuf(cr, self.page_pixbuf, 0, 0); cr.paint()
        if self.selected_annot:
            rect = self.selected_annot.rect * self.view.zoom
            cr.set_source_rgba(0,0,1,0.5); cr.rectangle(rect.x0, rect.y0, rect.width, rect.height); cr.stroke()

    def on_tool_changed(self, drop, pspec):
        item = drop.get_selected_item();
        if item: self.view.tool = item.get_string()

    def on_click(self, gesture, n_press, x, y):
        if not self.doc: return
        if self.view.tool == "text":
            self._prompt_text(x, y)
            return
        elif self.view.tool in ("select", "move-text"):
            page = self.doc[self.view.page_index]
            self.selected_annot = None
            for a in page.annots() or []:
                if a.rect.contains((x/self.view.zoom, y/self.view.zoom)):
                    self.selected_annot = a; break
            self.darea.queue_draw()

    def _prompt_text(self, vx, vy):
        dlg = Adw.AlertDialog()
        dlg.set_heading("Insert Text")
        entry = Gtk.Entry(); entry.set_hexpand(True); entry.set_placeholder_text("Type text…"); entry.set_activates_default(True)
        size_adj = Gtk.Adjustment(lower=6, upper=96, value=14, step_increment=1, page_increment=10)
        size_spin = Gtk.SpinButton(adjustment=size_adj, digits=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Size:")); row.append(size_spin)
        box.append(entry); box.append(row)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Insert")
        dlg.set_default_response("ok")
        dlg.connect("response", lambda d, r: self._place_text_response(d, r, vx, vy, entry, size_spin))
        dlg.present(self)

    def _place_text_response(self, dlg, response, vx, vy, entry, size_spin):
        if response != "ok":
            return
        text = entry.get_text() or ""
        size = size_spin.get_value_as_int()
        if not text:
            return
        page = self.doc[self.view.page_index]
        px, py = vx/self.view.zoom, vy/self.view.zoom
        rect = fitz.Rect(px, py, px + 300, py + size * 1.8)
        try:
            annot = page.add_freetext_annot(rect, text)
            annot.set_fontsize(size)
            annot.set_colors(text=(0,0,0))
            annot.update()
            self.selected_annot = annot
        except Exception:
            page.insert_textbox(rect, text, fontsize=size, color=(0,0,0))
        self._render()

    def on_drag_begin(self, g, x, y):
        self.drag = DragState(True, x, y, x, y)
    def on_drag_update(self, g, x, y):
        self.drag.cur_x, self.drag.cur_y = x, y
    def on_drag_end(self, g, x, y):
        if self.view.tool == "move-text" and self.selected_annot:
            dx = (x - self.drag.start_x)/self.view.zoom; dy = (y - self.drag.start_y)/self.view.zoom
            r = self.selected_annot.rect
            self.selected_annot.set_rect(fitz.Rect(r.x0+dx, r.y0+dy, r.x1+dx, r.y1+dy)); self.selected_annot.update()
            self._render()
        self.drag.dragging = False

class PDFEditorApp(Adw.Application):
    def __init__(self): super().__init__(application_id="dev.example.PDFEditor", flags=Gio.ApplicationFlags.FLAGS_NONE); Adw.init()
    def do_activate(self):
        win = self.props.active_window
        if not win: win = PDFEditorWindow(self)
        win.present()

def main():
    app = PDFEditorApp(); sys.exit(app.run(sys.argv))
if __name__ == "__main__": main()
