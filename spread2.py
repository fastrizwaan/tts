import sys
import csv
import string
import gi
import os

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango, PangoCairo, Gdk

class SpreadsheetModel:
    """Pure Python data model for sparse data."""
    def __init__(self, rows=100, cols=26):
        self.rows = rows
        self.cols = cols
        self.data = {} 

    def set_cell(self, r, c, value):
        if not value:
            self.data.pop((r, c), None)
        else:
            self.data[(r, c)] = value

    def get_cell(self, r, c):
        return self.data.get((r, c), "")

class CanvasView(Gtk.DrawingArea):
    """
    High-performance virtual grid.
    """
    def __init__(self, model):
        super().__init__()
        self.model = model
        
        # Grid settings
        self.row_height = 30
        self.col_width = 100
        self.header_height = 35
        self.row_header_width = 50
        
        # Scroll Adjustments (Virtual Scrolling)
        self.hadj = Gtk.Adjustment()
        self.vadj = Gtk.Adjustment()
        
        # Use lambda to discard the extra argument from "value-changed"
        self.hadj.connect("value-changed", lambda _: self.queue_draw())
        self.vadj.connect("value-changed", lambda _: self.queue_draw())

        self.selected_row = 0
        self.selected_col = 0
        self.editing = False
        
        # 1. Mouse Click (Selection)
        gesture_click = Gtk.GestureClick()
        gesture_click.connect("pressed", self.on_click)
        self.add_controller(gesture_click)

        # 2. Keyboard (Navigation)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_ctrl)
        
        # 3. Mouse Scroll (Fixes "No Scrolling")
        scroll_ctrl = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll_ctrl.connect("scroll", self.on_scroll)
        self.add_controller(scroll_ctrl)

        self.set_draw_func(self.draw_func)
        self.connect("resize", self.update_adjustments)

        # Hidden Entry for editing
        self.entry = Gtk.Entry()
        self.entry.set_visible(False)
        self.entry.add_css_class("flat")
        # Style to make it look distinct
        self.entry.add_css_class("spreadsheet-entry") 
        self.entry.connect("activate", self.on_entry_done)

    def update_adjustments(self, *args):
        # Total virtual size needed
        total_w = (self.model.cols * self.col_width) + self.row_header_width
        total_h = (self.model.rows * self.row_height) + self.header_height
        
        # Current viewport size
        page_w = self.get_width()
        page_h = self.get_height()

        # Configure scrollbars: value, lower, upper, step_increment, page_increment, page_size
        self.hadj.configure(self.hadj.get_value(), 0, total_w, self.col_width, page_w, page_w)
        self.vadj.configure(self.vadj.get_value(), 0, total_h, self.row_height, page_h, page_h)

    def on_scroll(self, controller, dx, dy):
        # Manual scroll handling
        # dx/dy are usually 1.0 or -1.0 per "tick"
        self.hadj.set_value(self.hadj.get_value() + (dx * self.col_width))
        self.vadj.set_value(self.vadj.get_value() + (dy * self.row_height))
        return True

    def draw_func(self, area, cr, w, h):
        # Background
        cr.set_source_rgb(1, 1, 1)
        cr.paint()

        off_x = self.hadj.get_value()
        off_y = self.vadj.get_value()
        
        # Visible Range Calculation
        start_col = max(0, int((off_x - self.row_header_width) // self.col_width))
        end_col = min(self.model.cols, int((off_x + w) // self.col_width) + 1)
        
        start_row = max(0, int((off_y - self.header_height) // self.row_height))
        end_row = min(self.model.rows, int((off_y + h) // self.row_height) + 1)

        layout = self.create_pango_layout("")
        
        # --- 1. Draw Grid Lines (1px sharp) ---
        cr.set_line_width(1.0)
        
        for r in range(start_row, end_row):
            y = self.header_height + (r * self.row_height) - off_y
            
            for c in range(start_col, end_col):
                x = self.row_header_width + (c * self.col_width) - off_x
                
                # Selection Highlight
                if r == self.selected_row and c == self.selected_col:
                    cr.set_source_rgba(0.2, 0.4, 1.0, 0.1) # Light blue fill
                    cr.rectangle(x, y, self.col_width, self.row_height)
                    cr.fill()
                    
                    # Blue border for selection
                    cr.set_source_rgb(0.2, 0.4, 1.0)
                    cr.rectangle(x + 0.5, y + 0.5, self.col_width, self.row_height)
                    cr.stroke()
                else:
                    # Standard Grid Line
                    cr.set_source_rgb(0.9, 0.9, 0.9) # Light gray
                    # Draw rectangle outline
                    cr.rectangle(x + 0.5, y + 0.5, self.col_width, self.row_height)
                    cr.stroke()

                # Text Content
                val = self.model.get_cell(r, c)
                if val:
                    cr.set_source_rgb(0, 0, 0)
                    layout.set_text(val)
                    
                    # Clip text to cell
                    cr.save()
                    cr.rectangle(x + 2, y + 2, self.col_width - 4, self.row_height - 4)
                    cr.clip()
                    cr.move_to(x + 5, y + 6)
                    PangoCairo.show_layout(cr, layout)
                    cr.restore()

        # --- 2. Draw Sticky Headers ---
        
        # Column Headers (Top)
        cr.set_source_rgb(0.96, 0.96, 0.96)
        cr.rectangle(0, 0, w, self.header_height)
        cr.fill()
        # Bottom border of header
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.move_to(0, self.header_height + 0.5)
        cr.line_to(w, self.header_height + 0.5)
        cr.stroke()

        for c in range(start_col, end_col):
            x = self.row_header_width + (c * self.col_width) - off_x
            # Right separator
            cr.set_source_rgb(0.8, 0.8, 0.8)
            cr.move_to(x + self.col_width + 0.5, 0)
            cr.line_to(x + self.col_width + 0.5, self.header_height)
            cr.stroke()
            
            # Text (A, B, C...)
            col_name = self._get_col_name(c)
            layout.set_text(col_name)
            extents = layout.get_pixel_extents()[1]
            # Center text
            text_x = x + (self.col_width - extents.width) / 2
            text_y = (self.header_height - extents.height) / 2
            
            cr.set_source_rgb(0.3, 0.3, 0.3)
            cr.move_to(text_x, text_y)
            PangoCairo.show_layout(cr, layout)

        # Row Headers (Left)
        cr.set_source_rgb(0.96, 0.96, 0.96)
        cr.rectangle(0, 0, self.row_header_width, h)
        cr.fill()
        # Right border of header
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.move_to(self.row_header_width + 0.5, 0)
        cr.line_to(self.row_header_width + 0.5, h)
        cr.stroke()

        for r in range(start_row, end_row):
            y = self.header_height + (r * self.row_height) - off_y
            # Bottom separator
            cr.set_source_rgb(0.8, 0.8, 0.8)
            cr.move_to(0, y + self.row_height + 0.5)
            cr.line_to(self.row_header_width, y + self.row_height + 0.5)
            cr.stroke()
            
            # Text (1, 2, 3...)
            layout.set_text(str(r + 1))
            extents = layout.get_pixel_extents()[1]
            text_x = (self.row_header_width - extents.width) / 2
            text_y = y + (self.row_height - extents.height) / 2
            
            cr.set_source_rgb(0.3, 0.3, 0.3)
            cr.move_to(text_x, text_y)
            PangoCairo.show_layout(cr, layout)

        # Top-Left Block (Empty)
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.rectangle(0, 0, self.row_header_width, self.header_height)
        cr.fill()
        # Borders
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.move_to(self.row_header_width + 0.5, 0)
        cr.line_to(self.row_header_width + 0.5, self.header_height)
        cr.stroke()
        cr.move_to(0, self.header_height + 0.5)
        cr.line_to(self.row_header_width, self.header_height + 0.5)
        cr.stroke()

    def _get_col_name(self, idx):
        if idx < 26: return string.ascii_uppercase[idx]
        return string.ascii_uppercase[(idx // 26) - 1] + string.ascii_uppercase[idx % 26]

    def on_click(self, gesture, n_press, x, y):
        # If we were editing, stop and save
        if self.editing:
            self.on_entry_done(self.entry)

        off_x = self.hadj.get_value()
        off_y = self.vadj.get_value()

        # Check if click is inside grid area
        if x > self.row_header_width and y > self.header_height:
            # Map click to col/row index
            c = int((x + off_x - self.row_header_width) // self.col_width)
            r = int((y + off_y - self.header_height) // self.row_height)
            
            self.selected_row = max(0, min(r, self.model.rows - 1))
            self.selected_col = max(0, min(c, self.model.cols - 1))
            self.queue_draw()

            if n_press == 2:
                self.start_editing()

    def on_key_pressed(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Up: self.selected_row = max(0, self.selected_row - 1)
        elif keyval == Gdk.KEY_Down: self.selected_row = min(self.model.rows - 1, self.selected_row + 1)
        elif keyval == Gdk.KEY_Left: self.selected_col = max(0, self.selected_col - 1)
        elif keyval == Gdk.KEY_Right: self.selected_col = min(self.model.cols - 1, self.selected_col + 1)
        elif keyval == Gdk.KEY_Return: self.start_editing(); return True
        else: return False
        
        self.queue_draw()
        return True

    def start_editing(self):
        self.editing = True
        
        off_x = self.hadj.get_value()
        off_y = self.vadj.get_value()
        
        # Calculate visual position (relative to the drawing area's top-left)
        x = self.row_header_width + (self.selected_col * self.col_width) - off_x
        y = self.header_height + (self.selected_row * self.row_height) - off_y

        # Call window method to move overlay widget
        root = self.get_root()
        if hasattr(root, "move_entry"):
            text = self.model.get_cell(self.selected_row, self.selected_col)
            root.move_entry(self.entry, x, y, self.col_width, self.row_height, text)

    def on_entry_done(self, entry):
        if not self.editing: return
        self.editing = False
        
        text = entry.get_text()
        self.model.set_cell(self.selected_row, self.selected_col, text)
        entry.set_visible(False)
        
        # Refocus the canvas so keyboard nav works immediately
        self.grab_focus()
        self.queue_draw()

class SpreadsheetWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        self.set_title("Spreadsheet")

        # Create Model: 65k rows
        self.model = SpreadsheetModel(rows=65535, cols=100)
        
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(vbox)

        # HeaderBar
        header = Adw.HeaderBar()
        vbox.append(header)
        
        self.lbl_status = Gtk.Label(label="65,535 Rows")
        self.lbl_status.add_css_class("title")
        header.set_title_widget(self.lbl_status)

        btn_open = Gtk.Button(icon_name="document-open-symbolic")
        btn_open.connect("clicked", self.on_open)
        header.pack_start(btn_open)

        btn_save = Gtk.Button(icon_name="document-save-symbolic")
        btn_save.connect("clicked", self.on_save)
        header.pack_start(btn_save)

        # --- Viewport Stack ---
        # 1. Overlay holds the Entry
        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        
        # 2. Canvas is the DrawingArea
        self.canvas = CanvasView(self.model)
        self.canvas.set_vexpand(True)
        self.canvas.set_hexpand(True)
        self.canvas.set_focusable(True) # Needed for keyboard nav

        # 3. ScrolledWindow provides the visual scrollbars
        # Note: We do NOT put the canvas inside as a child, because the canvas is "virtual"
        # and doesn't actually resize to 65k pixels tall.
        # Instead, we create independent scrollbars.
        
        # Better approach for GTK4:
        # Put the Canvas in the Overlay.
        # Put two Gtk.Scrollbars in a grid around it.
        
        grid = Gtk.Grid()
        self.overlay.set_child(grid)
        
        # Center: Canvas
        grid.attach(self.canvas, 0, 0, 1, 1)
        
        # Right: VScroll
        vscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL)
        vscroll.set_adjustment(self.canvas.vadj)
        grid.attach(vscroll, 1, 0, 1, 1)
        
        # Bottom: HScroll
        hscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL)
        hscroll.set_adjustment(self.canvas.hadj)
        hscroll.set_hexpand(True)
        grid.attach(hscroll, 0, 1, 1, 1)

        # Add Entry to Overlay
        self.canvas.entry.set_halign(Gtk.Align.START)
        self.canvas.entry.set_valign(Gtk.Align.START)
        self.overlay.add_overlay(self.canvas.entry)
        
        vbox.append(self.overlay)

    def move_entry(self, entry, x, y, w, h, text):
        """Positions the floating entry widget over the cell."""
        entry.set_text(text)
        entry.set_visible(True)
        entry.grab_focus()
        
        # In GtkOverlay, set_measure returns void, we use properties or margin
        # Since alignments are START, margins act as X/Y coordinates
        
        # Important: x and y must be relative to the overlay top-left.
        # Since our canvas takes up the whole overlay space (minus scrollbars),
        # coordinates from drawing area (canvas) map 1:1 to overlay.
        
        entry.set_margin_start(int(x))
        entry.set_margin_top(int(y))
        entry.set_size_request(int(w), int(h))

    # --- File Operations ---
    def on_save(self, btn):
        dialog = Gtk.FileDialog(title="Save CSV")
        dialog.save(self, None, self._save_cb)

    def _save_cb(self, dialog, res):
        try:
            f = dialog.save_finish(res)
            with open(f.get_path(), 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                max_r = 0
                for (r, c) in self.model.data.keys():
                    if r > max_r: max_r = r
                for r in range(max_r + 1):
                    row_data = []
                    for c in range(self.model.cols):
                        row_data.append(self.model.get_cell(r, c))
                    writer.writerow(row_data)
        except Exception as e: print(e)

    def on_open(self, btn):
        dialog = Gtk.FileDialog(title="Open CSV")
        dialog.open(self, None, self._open_cb)

    def _open_cb(self, dialog, res):
        try:
            f = dialog.open_finish(res)
            self.model.data.clear()
            with open(f.get_path(), newline='') as csvfile:
                reader = csv.reader(csvfile)
                for r_idx, row in enumerate(reader):
                    for c_idx, val in enumerate(row):
                        if c_idx < self.model.cols and r_idx < self.model.rows:
                            self.model.set_cell(r_idx, c_idx, val)
            self.canvas.queue_draw()
        except Exception as e: print(e)

class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.Spreadsheet', flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window()
        if not win: win = SpreadsheetWindow(self)
        win.present()
        # Add some custom CSS for the entry
        css = """
        .spreadsheet-entry {
            background: white;
            border: 2px solid #3584e4;
            border-radius: 0;
            color: black;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, 800)

if __name__ == "__main__":
    app = App()
    app.run(sys.argv)
