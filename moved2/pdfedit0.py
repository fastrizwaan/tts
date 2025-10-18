#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Minimal PDF editor using PyGObject (GTK4 + Libadwaita) + PyMuPDF (fitz)
# Features:
# - Open/Save/Save As
# - Page navigation
# - Zoom in/out
# - Tools: Pan, Text, Rectangle, Highlight
#   * Text: click to place a text box; choose font size and color
#   * Rectangle: drag to draw a stroked rectangle
#   * Highlight: drag to highlight a region (uses PDF highlight annotation)
# Notes:
# - Requires: PyGObject (GTK 4), libadwaita, PyMuPDF (fitz), pygobject, gobject-introspection, gdk-pixbuf
# - Install on Fedora: sudo dnf install python3-gobject gtk4 libadwaita gobject-introspection gdk-pixbuf2
# - Install PyMuPDF: pip install pymupdf
# - This is a minimal reference; production editors need more robust text selection & annotation handling.

import gi
import os
import sys
import math
from dataclasses import dataclass

# GI deps
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, GdkPixbuf, Pango

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
    zoom: float = 1.0  # 1.0 = 72 DPI base
    tool: str = "pan"  # pan|text|rect|highlight


@dataclass
class DragState:
    dragging: bool = False
    start_x: float = 0.0
    start_y: float = 0.0
    cur_x: float = 0.0
    cur_y: float = 0.0


# ----------------------- Main Window -----------------------
class PDFEditorWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("PDF Editor")
        self.set_default_size(1100, 780)

        self.doc = None  # fitz.Document
        self.path = None
        self.view = ViewState()
        self.drag = DragState()
        self.page_pixbuf = None  # GdkPixbuf for current page

        self.text_color = (0, 0, 0)
        self.text_size = 12
        self.rect_color = (1.0, 0.0, 0.0)
        self.rect_width = 2.0
        self.highlight_color = (1.0, 1.0, 0.0)
        self.highlight_opacity = 0.35

        self._build_ui()
        self._bind_actions()

    # ----------------------- UI -----------------------
    def _build_ui(self):
        self.set_content(Adw.ToolbarView())
        tv = self.get_content()

        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)

        # Left: Open, Save, Save As
        self.btn_open = Gtk.Button.new_from_icon_name("document-open-symbolic")
        self.btn_open.set_tooltip_text("Open PDF")
        self.btn_open.connect("clicked", self.on_open_clicked)
        hb.pack_start(self.btn_open)

        self.btn_save = Gtk.Button.new_from_icon_name("document-save-symbolic")
        self.btn_save.set_tooltip_text("Save")
        self.btn_save.connect("clicked", self.on_save_clicked)
        hb.pack_start(self.btn_save)

        self.btn_save_as = Gtk.Button.new_from_icon_name("document-save-as-symbolic")
        self.btn_save_as.set_tooltip_text("Save As…")
        self.btn_save_as.connect("clicked", self.on_save_as_clicked)
        hb.pack_start(self.btn_save_as)

        # Center: Page nav
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

        # Right: Zoom and Tools
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        self.btn_zoom_out.connect("clicked", lambda *_: self._change_zoom(0.9))
        self.btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        self.btn_zoom_in.connect("clicked", lambda *_: self._change_zoom(1.1))

        self.tool_store = Gtk.StringList.new(["pan", "text", "rect", "highlight"])
        self.tool_drop = Gtk.DropDown.new_from_strings(["pan", "text", "rect", "highlight"])
        self.tool_drop.connect("notify::selected-item", self.on_tool_changed)
        self.tool_drop.set_tooltip_text("Tool")

        right_box.append(self.btn_zoom_out)
        right_box.append(self.btn_zoom_in)
        right_box.append(self.tool_drop)
        hb.pack_end(right_box)

        # Content area: scroller + drawing area
        self.scroller = Gtk.ScrolledWindow()
        tv.set_content(self.scroller)

        self.darea = Gtk.DrawingArea()
        self.darea.set_content_width(900)
        self.darea.set_content_height(1200)
        self.darea.set_draw_func(self.on_draw)
        self.scroller.set_child(self.darea)

        # Gestures
        self.click = Gtk.GestureClick()
        self.click.connect("pressed", self.on_click)
        self.darea.add_controller(self.click)

        self.drag_g = Gtk.GestureDrag()
        self.drag_g.connect("drag-begin", self.on_drag_begin)
        self.drag_g.connect("drag-update", self.on_drag_update)
        self.drag_g.connect("drag-end", self.on_drag_end)
        self.darea.add_controller(self.drag_g)

        # Keyboard shortcuts
        self.add_controller(self._key_controller())

    def _key_controller(self):
        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self.on_key)
        return kc

    # ----------------------- Actions -----------------------
    def _bind_actions(self):
        # App actions for shortcuts
        act_open = Gio.SimpleAction.new("open", None)
        act_open.connect("activate", lambda *_: self.on_open_clicked(None))
        self.get_application().add_action(act_open)
        self.get_application().set_accels_for_action("app.open", ["<Primary>o"]) 

        act_save = Gio.SimpleAction.new("save", None)
        act_save.connect("activate", lambda *_: self.on_save_clicked(None))
        self.get_application().add_action(act_save)
        self.get_application().set_accels_for_action("app.save", ["<Primary>s"]) 

        act_zoom_in = Gio.SimpleAction.new("zoom_in", None)
        act_zoom_in.connect("activate", lambda *_: self._change_zoom(1.1))
        self.get_application().add_action(act_zoom_in)
        self.get_application().set_accels_for_action("app.zoom_in", ["<Primary>plus", "<Primary>equal"]) 

        act_zoom_out = Gio.SimpleAction.new("zoom_out", None)
        act_zoom_out.connect("activate", lambda *_: self._change_zoom(0.9))
        self.get_application().add_action(act_zoom_out)
        self.get_application().set_accels_for_action("app.zoom_out", ["<Primary>minus"]) 

        act_prev = Gio.SimpleAction.new("prev", None)
        act_prev.connect("activate", lambda *_: self.on_prev_page(None))
        self.get_application().add_action(act_prev)
        self.get_application().set_accels_for_action("app.prev", ["Page_Up"]) 

        act_next = Gio.SimpleAction.new("next", None)
        act_next.connect("activate", lambda *_: self.on_next_page(None))
        self.get_application().add_action(act_next)
        self.get_application().set_accels_for_action("app.next", ["Page_Down"]) 

    # ----------------------- File ops -----------------------
    def on_open_clicked(self, _btn):
        dialog = Gtk.FileDialog()
        f = Gtk.FileFilter()
        f.set_name("PDF Files")
        f.add_suffix("pdf")
        dialog.set_default_filter(f)
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
        if self.doc:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = fitz.open(path)
        self.path = path
        self.view.page_index = 0
        self.view.zoom = 1.0
        self._render_page()
        self._update_page_label()
        self._set_title()

    def on_save_clicked(self, _btn):
        if not self.doc:
            return
        if self.path:
            try:
                # Save in-place (incremental when possible)
                self.doc.save(self.path, incremental=True, deflate=True)
                self._toast("Saved")
            except Exception as e:
                self._error(str(e))
        else:
            self.on_save_as_clicked(_btn)

    def on_save_as_clicked(self, _btn):
        if not self.doc:
            return
        dialog = Gtk.FileDialog()
        dialog.set_initial_name("edited.pdf")
        dialog.save(self, None, self._saveas_done)

    def _saveas_done(self, dialog, res):
        try:
            file = dialog.save_finish(res)
        except GLib.Error:
            return
        path = file.get_path()
        if not path:
            return
        try:
            self.doc.save(path)
            self.path = path
            self._set_title()
            self._toast("Saved as")
        except Exception as e:
            self._error(str(e))

    # ----------------------- Rendering -----------------------
    def _render_page(self):
        if not self.doc:
            return
        page = self.doc[self.view.page_index]
        # base zoom: 1.0 => 72 DPI. Multiply
        m = fitz.Matrix(self.view.zoom, self.view.zoom)
        pm = page.get_pixmap(matrix=m, alpha=False)
        # Convert to GdkPixbuf
        # PyMuPDF pixmap is RGB when alpha=False.
        data = pm.samples  # bytes
        width, height = pm.width, pm.height
        rowstride = pm.stride
        has_alpha = False
        self.page_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            data,
            GdkPixbuf.Colorspace.RGB,
            has_alpha,
            8,
            width,
            height,
            rowstride,
            None,
            None,
        )
        self.darea.set_content_width(width)
        self.darea.set_content_height(height)
        self.darea.queue_draw()

    def on_draw(self, area, ctx, w, h):
        # Draw page image
        if self.page_pixbuf:
            Gdk.cairo_set_source_pixbuf(ctx, self.page_pixbuf, 0, 0)
            ctx.paint()
        # Draw drag overlay for rect/highlight
        if self.drag.dragging and self.view.tool in ("rect", "highlight"):
            x0, y0 = self.drag.start_x, self.drag.start_y
            x1, y1 = self.drag.cur_x, self.drag.cur_y
            rx, ry = min(x0, x1), min(y0, y1)
            rw, rh = abs(x1 - x0), abs(y1 - y0)
            if self.view.tool == "rect":
                ctx.set_source_rgba(*self.rect_color, 0.8)
                ctx.set_line_width(self.rect_width)
                ctx.rectangle(rx, ry, rw, rh)
                ctx.stroke()
            elif self.view.tool == "highlight":
                ctx.set_source_rgba(*self.highlight_color, self.highlight_opacity)
                ctx.rectangle(rx, ry, rw, rh)
                ctx.fill()

    # ----------------------- Gestures -----------------------
    def on_tool_changed(self, drop, _pspec):
        item = drop.get_selected_item()
        if item:
            self.view.tool = item.get_string()

    def on_click(self, gesture, n_press, x, y):
        if not self.doc:
            return
        if self.view.tool == "text" and n_press == 1:
            self._place_text_dialog(x, y)

    def on_drag_begin(self, gesture, x, y):
        if not self.doc:
            return
        if self.view.tool in ("rect", "highlight"):
            self.drag = DragState(True, x, y, x, y)
            self.darea.queue_draw()

    def on_drag_update(self, gesture, x, y):
        if not self.doc:
            return
        if self.drag.dragging:
            self.drag.cur_x, self.drag.cur_y = x, y
            self.darea.queue_draw()

    def on_drag_end(self, gesture, x, y):
        if not self.doc:
            return
        if self.drag.dragging:
            self.drag.cur_x, self.drag.cur_y = x, y
            self._commit_drag()
            self.drag.dragging = False
            self.darea.queue_draw()

    def _commit_drag(self):
        # Convert view-space rect to PDF-space and add annotation/shape
        x0, y0 = self.drag.start_x, self.drag.start_y
        x1, y1 = self.drag.cur_x, self.drag.cur_y
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        if rw < 2 or rh < 2:
            return
        page = self.doc[self.view.page_index]
        pdf_rect = self._to_pdf_rect(page, rx, ry, rw, rh)
        if self.view.tool == "rect":
            # Draw shape content (editable as vector, not as annot)
            page.draw_rect(pdf_rect, color=self.rect_color, width=self.rect_width)
        elif self.view.tool == "highlight":
            annot = page.add_highlight_annot(pdf_rect)
            r, g, b = self.highlight_color
            annot.set_colors(stroke=(r, g, b), fill=(r, g, b))
            annot.set_opacity(self.highlight_opacity)
            annot.update()
        self._render_page()

    def _place_text_dialog(self, x, y):
        dlg = Adw.MessageDialog.new(self, "Insert Text", "Enter text to place")
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_activates_default(True)
        # Size chooser
        size_adj = Gtk.Adjustment(lower=6, upper=96, value=self.text_size, step_increment=1, page_increment=10)
        size_spin = Gtk.SpinButton(adjustment=size_adj, climb_rate=1, digits=0)
        size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        size_row.append(Gtk.Label(label="Font size:"))
        size_row.append(size_spin)
        # Layout box
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12); box.set_margin_bottom(12)
        box.set_margin_start(12); box.set_margin_end(12)
        box.append(entry)
        box.append(size_row)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Insert")
        dlg.set_default_response("ok")
        dlg.connect("response", lambda d, r: self._place_text_response(d, r, x, y, entry, size_spin))
        dlg.present()

    def _place_text_response(self, dlg, response, x, y, entry, size_spin):
        if response != "ok":
            dlg.destroy(); return
        text = entry.get_text() or ""
        size = size_spin.get_value_as_int()
        dlg.destroy()
        if not text:
            return
        page = self.doc[self.view.page_index]
        # Convert view point to PDF point; place textbox with a reasonable width
        pdf_x, pdf_y = self._to_pdf_point(page, x, y)
        # Text box width: 300 PDF units at 72 DPI; adjust for zoom
        box_w = 300
        rect = fitz.Rect(pdf_x, pdf_y, pdf_x + box_w, pdf_y + size * 1.6)
        r, g, b = self.text_color
        page.insert_textbox(rect, text, fontsize=size, color=(r, g, b))
        self._render_page()

    # ----------------------- Helpers -----------------------
    def _to_pdf_point(self, page, vx, vy):
        # view (pixels) -> pdf (points)
        # page.rect gives page size in points (72 dpi)
        # Our rendering used matrix (zoom, zoom)
        z = self.view.zoom
        px = vx / z
        py = vy / z
        return (px, py)

    def _to_pdf_rect(self, page, vx, vy, vw, vh):
        z = self.view.zoom
        return fitz.Rect(vx / z, vy / z, (vx + vw) / z, (vy + vh) / z)

    def _change_zoom(self, factor):
        if not self.doc:
            return
        self.view.zoom = max(0.2, min(6.0, self.view.zoom * factor))
        self._render_page()

    def on_prev_page(self, _btn):
        if not self.doc:
            return
        if self.view.page_index > 0:
            self.view.page_index -= 1
            self._render_page()
            self._update_page_label()

    def on_next_page(self, _btn):
        if not self.doc:
            return
        if self.view.page_index < len(self.doc) - 1:
            self.view.page_index += 1
            self._render_page()
            self._update_page_label()

    def _update_page_label(self):
        if not self.doc:
            self.page_label.set_text("– / –")
        else:
            self.page_label.set_text(f"{self.view.page_index + 1} / {len(self.doc)}")

    def _set_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.set_title(f"PDF Editor — {name}")

    def _toast(self, text):
        # Simple banner via transient Snackbar-ish dialog
        dlg = Adw.MessageDialog.new(self, text, None)
        GLib.timeout_add_once(900, lambda *_: (dlg.close(), False))
        dlg.present()

    def _error(self, text):
        dlg = Adw.MessageDialog.new(self, "Error", text)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        dlg.present()

    def on_key(self, _ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape and self.drag.dragging:
            self.drag.dragging = False
            self.darea.queue_draw()
            return True
        return False


# ----------------------- Application -----------------------
class PDFEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.example.PDFEditor", flags=Gio.ApplicationFlags.FLAGS_NONE)
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

