#!/usr/bin/env python3
import sys, os, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, Gtk, Adw, Gdk, GObject, GLib, Pango, PangoCairo


# ============================================================
#   ENCODING DETECTION (UTF8, UTF16 LE / BE, UTF8-BOM)
# ============================================================

def detect_encoding(path):
    with open(path, "rb") as f:
        b = f.read(4)
    if b.startswith(b"\xff\xfe"):
        return "utf-16le"
    if b.startswith(b"\xfe\xff"):
        return "utf-16be"
    if b.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


# ============================================================
#   VIRTUAL BUFFER (Editable)
# ============================================================

class VirtualBuffer(GObject.Object):

    __gsignals__ = {
        "changed":      (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()
        self.lines = [""]
        self.cursor_line = 0
        self.cursor_col = 0
        self.modified = False

    def load_from_file(self, path):
        """Load file into editable buffer"""
        encoding = detect_encoding(path)
        
        with open(path, "rb") as f:
            raw_data = f.read()
            text = raw_data.decode(encoding, errors="replace")
            self.lines = text.splitlines()
            if not self.lines:
                self.lines = [""]
        
        self.cursor_line = 0
        self.cursor_col = 0
        self.modified = False
        self.emit("changed")

    def total(self):
        return len(self.lines)

    def get_line(self, i):
        if 0 <= i < len(self.lines):
            return self.lines[i]
        return ""

    def set_cursor(self, line, col):
        line = max(0, min(line, len(self.lines) - 1))
        row = self.get_line(line)
        # Use character count, not byte count
        col = max(0, min(col, len(row)))

        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)

    def insert_text(self, text):
        """Insert text at cursor (handles multi-char like IME)"""
        line = self.lines[self.cursor_line]
        self.lines[self.cursor_line] = line[:self.cursor_col] + text + line[self.cursor_col:]
        self.cursor_col += len(text)
        self.modified = True
        self.emit("changed")

    def insert_newline(self):
        """Insert newline at cursor"""
        line = self.lines[self.cursor_line]
        before = line[:self.cursor_col]
        after = line[self.cursor_col:]
        
        self.lines[self.cursor_line] = before
        self.lines.insert(self.cursor_line + 1, after)
        
        self.cursor_line += 1
        self.cursor_col = 0
        self.modified = True
        self.emit("changed")

    def backspace(self):
        """Delete character before cursor"""
        if self.cursor_col > 0:
            line = self.lines[self.cursor_line]
            self.lines[self.cursor_line] = line[:self.cursor_col - 1] + line[self.cursor_col:]
            self.cursor_col -= 1
        elif self.cursor_line > 0:
            prev_line = self.lines[self.cursor_line - 1]
            curr_line = self.lines[self.cursor_line]
            self.lines[self.cursor_line - 1] = prev_line + curr_line
            del self.lines[self.cursor_line]
            self.cursor_line -= 1
            self.cursor_col = len(prev_line)
        
        self.modified = True
        self.emit("changed")

    def delete(self):
        """Delete character at cursor"""
        line = self.lines[self.cursor_line]
        if self.cursor_col < len(line):
            self.lines[self.cursor_line] = line[:self.cursor_col] + line[self.cursor_col + 1:]
        elif self.cursor_line < len(self.lines) - 1:
            next_line = self.lines[self.cursor_line + 1]
            self.lines[self.cursor_line] = line + next_line
            del self.lines[self.cursor_line + 1]
        
        self.modified = True
        self.emit("changed")

    def delete_selection(self, start, end):
        """Delete text in selection range"""
        (s_line, s_col), (e_line, e_col) = start, end
        
        if s_line == e_line:
            line = self.lines[s_line]
            self.lines[s_line] = line[:s_col] + line[e_col:]
        else:
            start_line = self.lines[s_line][:s_col]
            end_line = self.lines[e_line][e_col:]
            
            self.lines[s_line] = start_line + end_line
            del self.lines[s_line + 1:e_line + 1]
        
        self.cursor_line = s_line
        self.cursor_col = s_col
        self.modified = True
        self.emit("changed")

    def get_selected_text(self, start, end):
        """Get text in selection range"""
        (s_line, s_col), (e_line, e_col) = start, end
        
        if s_line == e_line:
            return self.lines[s_line][s_col:e_col]
        
        result = [self.lines[s_line][s_col:]]
        for i in range(s_line + 1, e_line):
            result.append(self.lines[i])
        result.append(self.lines[e_line][:e_col])
        
        return "\n".join(result)


# ============================================================
#   INPUT CONTROLLER
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf

        self.sel_start = None
        self.sel_end = None
        self.selecting = False

    def click(self, line, col):
        self.buf.set_cursor(line, col)
        self.sel_start = (line, col)
        self.sel_end = (line, col)
        self.selecting = True

    def drag(self, line, col):
        if self.selecting:
            self.sel_end = (line, col)

    def has_selection(self):
        return self.sel_start and self.sel_end and self.sel_start != self.sel_end

    def get_selection_range(self):
        """Returns normalized (start, end) where start <= end"""
        if not self.has_selection():
            return None
        
        s_line, s_col = self.sel_start
        e_line, e_col = self.sel_end
        
        if s_line < e_line or (s_line == e_line and s_col < e_col):
            return (s_line, s_col), (e_line, e_col)
        else:
            return (e_line, e_col), (s_line, s_col)

    def clear_selection(self):
        b = self.buf
        self.sel_start = self.sel_end = (b.cursor_line, b.cursor_col)

    def move_left(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        
        if not extend and self.has_selection():
            start, end = self.get_selection_range()
            b.set_cursor(start[0], start[1])
            self.clear_selection()
            return
        
        if c > 0:
            b.set_cursor(l, c - 1)
        elif l > 0:
            prev = b.get_line(l - 1)
            b.set_cursor(l - 1, len(prev))
        
        if extend:
            self.sel_end = (b.cursor_line, b.cursor_col)
        else:
            self.clear_selection()

    def move_right(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        
        if not extend and self.has_selection():
            start, end = self.get_selection_range()
            b.set_cursor(end[0], end[1])
            self.clear_selection()
            return
        
        row = b.get_line(l)
        if c < len(row):
            b.set_cursor(l, c + 1)
        else:
            if l + 1 < b.total():
                b.set_cursor(l + 1, 0)
        
        if extend:
            self.sel_end = (b.cursor_line, b.cursor_col)
        else:
            self.clear_selection()

    def move_up(self, extend=False):
        b = self.buf
        if b.cursor_line > 0:
            l = b.cursor_line - 1
            row = b.get_line(l)
            b.set_cursor(l, min(b.cursor_col, len(row)))
            
            if extend:
                self.sel_end = (b.cursor_line, b.cursor_col)
            else:
                self.clear_selection()

    def move_down(self, extend=False):
        b = self.buf
        t = b.cursor_line + 1
        if t < b.total():
            row = b.get_line(t)
            b.set_cursor(t, min(b.cursor_col, len(row)))
            
            if extend:
                self.sel_end = (b.cursor_line, b.cursor_col)
            else:
                self.clear_selection()


# ============================================================
#   RENDERER (TEXT + LINE NUMBERS + SELECTION)
# ============================================================

class Renderer:
    def __init__(self):
        self.font_family = "Monospace"
        self.font_size = 13
        self.update_font()
        
        self.bg = (0.10, 0.10, 0.10)
        self.fg = (0.90, 0.90, 0.90)
        self.ln_fg = (0.60, 0.60, 0.60)
        self.sel_bg = (0.30, 0.40, 0.60)

        self.ln_width = 80
        self.word_wrap = False

    def update_font(self):
        self.font = Pango.FontDescription(f"{self.font_family} {self.font_size}")
        # Measure actual character dimensions
        layout = Pango.Layout(Pango.Context())
        layout.set_font_description(self.font)
        layout.set_text("M")
        self.char_w, self.line_h = layout.get_pixel_size()
        self.line_h += 4  # Add some line spacing

    def get_cursor_x_for_col(self, text, col):
        """Get accurate X position for cursor at column"""
        if col == 0:
            return 0
        layout = Pango.Layout(Pango.Context())
        layout.set_font_description(self.font)
        layout.set_text(text[:col])
        w, h = layout.get_pixel_size()
        return w

    def get_col_at_x(self, text, x):
        """Get column position at X coordinate"""
        if not text or x <= 0:
            return 0
        
        layout = Pango.Layout(Pango.Context())
        layout.set_font_description(self.font)
        layout.set_text(text)
        
        # Use Pango to find the index
        inside, index, trailing = layout.xy_to_index(x * Pango.SCALE, 0)
        # Convert byte index to character index
        char_index = len(text[:index])
        return min(char_index + trailing, len(text))

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        max_vis = alloc.height // self.line_h
        total_lines = buf.total()
        
        # Get selection range
        sel_range = None
        if sel_s and sel_e and sel_s != sel_e:
            s_line, s_col = sel_s
            e_line, e_col = sel_e
            if s_line < e_line or (s_line == e_line and s_col < e_col):
                sel_range = ((s_line, s_col), (e_line, e_col))
            else:
                sel_range = ((e_line, e_col), (s_line, s_col))

        y = 0
        for ln in range(scroll_line, min(scroll_line + max_vis, total_lines)):
            text = buf.get_line(ln)

            # Draw selection background
            if sel_range:
                (start_l, start_c), (end_l, end_c) = sel_range
                
                if start_l <= ln <= end_l:
                    if ln == start_l and ln == end_l:
                        sel_start_x = self.ln_width + self.get_cursor_x_for_col(text, start_c) - scroll_x
                        sel_end_x = self.ln_width + self.get_cursor_x_for_col(text, end_c) - scroll_x
                    elif ln == start_l:
                        sel_start_x = self.ln_width + self.get_cursor_x_for_col(text, start_c) - scroll_x
                        layout.set_text(text)
                        w, h = layout.get_pixel_size()
                        sel_end_x = self.ln_width + max(w, self.get_cursor_x_for_col(text, start_c) + self.char_w) - scroll_x
                    elif ln == end_l:
                        sel_start_x = self.ln_width - scroll_x
                        sel_end_x = self.ln_width + self.get_cursor_x_for_col(text, end_c) - scroll_x
                    else:
                        sel_start_x = self.ln_width - scroll_x
                        layout.set_text(text)
                        w, h = layout.get_pixel_size()
                        sel_end_x = self.ln_width + max(w, self.char_w) - scroll_x
                    
                    cr.set_source_rgb(*self.sel_bg)
                    cr.rectangle(sel_start_x, y, sel_end_x - sel_start_x, self.line_h)
                    cr.fill()

            # Draw line number
            layout.set_text(str(ln + 1))
            cr.set_source_rgb(*self.ln_fg)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # Draw text
            if text:
                layout.set_text(text)
                cr.set_source_rgb(*self.fg)
                cr.move_to(self.ln_width - scroll_x, y)
                PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # Draw cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cursor_text = buf.get_line(cl)
            cx = self.ln_width + self.get_cursor_x_for_col(cursor_text, cc) - scroll_x
            cy = (cl - scroll_line) * self.line_h
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()


# ============================================================
#   CUSTOM SCROLLBAR
# ============================================================

class CustomScrollbar(Gtk.DrawingArea):
    NORMAL_WIDTH = 8
    HOVER_WIDTH = 14
    
    def __init__(self, text_view, orientation=Gtk.Orientation.VERTICAL):
        super().__init__()

        self.view = text_view
        self.orientation = orientation
        self.visible = True

        if orientation == Gtk.Orientation.VERTICAL:
            self.set_size_request(self.NORMAL_WIDTH, -1)
        else:
            self.set_size_request(-1, self.NORMAL_WIDTH)
            
        self.set_hexpand(orientation == Gtk.Orientation.HORIZONTAL)
        self.set_vexpand(orientation == Gtk.Orientation.VERTICAL)
        self.set_draw_func(self.on_draw)

        self.drag_active = False
        self.drag_offset = 0
        self.hover = False
        self.hover_thumb = False

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *args: self.set_hover(True))
        motion.connect("leave", lambda *args: self.set_hover(False))
        motion.connect("motion", self.on_motion)
        self.add_controller(motion)

    def set_hover(self, state):
        if self.hover != state:
            self.hover = state
            if self.orientation == Gtk.Orientation.VERTICAL:
                self.set_size_request(self.HOVER_WIDTH if state else self.NORMAL_WIDTH, -1)
            else:
                self.set_size_request(-1, self.HOVER_WIDTH if state else self.NORMAL_WIDTH)
            self.queue_draw()

    def set_visible_scrollbar(self, visible):
        """Show or hide scrollbar"""
        if self.visible != visible:
            self.visible = visible
            if self.orientation == Gtk.Orientation.HORIZONTAL:
                self.set_visible(visible)

    def on_motion(self, ctrl, x, y):
        if self.orientation == Gtk.Orientation.VERTICAL:
            thumb_y, thumb_h = self.get_thumb_bounds()
            was_hover = self.hover_thumb
            self.hover_thumb = thumb_y <= y <= thumb_y + thumb_h
            if was_hover != self.hover_thumb:
                self.queue_draw()
        else:
            thumb_x, thumb_w = self.get_thumb_bounds()
            was_hover = self.hover_thumb
            self.hover_thumb = thumb_x <= x <= thumb_x + thumb_w
            if was_hover != self.hover_thumb:
                self.queue_draw()

    def get_thumb_bounds(self):
        if self.orientation == Gtk.Orientation.VERTICAL:
            return self.get_vthumb_bounds()
        else:
            return self.get_hthumb_bounds()

    def get_vthumb_bounds(self):
        h = self.get_allocated_height()
        buf = self.view.buf
        total = buf.total()
        
        if total < 1:
            return 0, h

        visible = max(1, self.view.get_allocated_height() // self.view.renderer.line_h)
        
        if visible >= total:
            return 0, h

        thumb_h = max(30, h * (visible / total))
        max_scroll = total - visible
        cur = min(self.view.scroll_line, max_scroll)
        y = (cur / max_scroll) * (h - thumb_h) if max_scroll > 0 else 0

        return y, thumb_h

    def get_hthumb_bounds(self):
        w = self.get_allocated_width()
        max_line_width = self.view.get_max_line_width()
        view_width = self.view.get_allocated_width() - self.view.renderer.ln_width
        
        if max_line_width <= view_width:
            return 0, w

        thumb_w = max(30, w * (view_width / max_line_width))
        max_scroll = max_line_width - view_width
        
        if max_scroll <= 0:
            return 0, w
            
        x = (self.view.scroll_x / max_scroll) * (w - thumb_w)
        return x, thumb_w

    def on_draw(self, area, cr, w, h):
        if self.hover or self.drag_active:
            cr.set_source_rgba(0.2, 0.2, 0.2, 0.5)
            cr.rectangle(0, 0, w, h)
            cr.fill()

        if self.orientation == Gtk.Orientation.VERTICAL:
            thumb_pos, thumb_size = self.get_vthumb_bounds()
            
            if self.drag_active:
                cr.set_source_rgba(0.75, 0.75, 0.75, 0.95)
            elif self.hover_thumb or self.hover:
                cr.set_source_rgba(0.65, 0.65, 0.65, 0.85)
            else:
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            
            thumb_width = w - 4 if self.hover else w - 2
            x_offset = 2 if self.hover else 1
            self.rounded_rectangle(cr, x_offset, thumb_pos, thumb_width, thumb_size, 3)
            cr.fill()
        else:
            thumb_pos, thumb_size = self.get_hthumb_bounds()
            
            if self.drag_active:
                cr.set_source_rgba(0.75, 0.75, 0.75, 0.95)
            elif self.hover_thumb or self.hover:
                cr.set_source_rgba(0.65, 0.65, 0.65, 0.85)
            else:
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            
            thumb_height = h - 4 if self.hover else h - 2
            y_offset = 2 if self.hover else 1
            self.rounded_rectangle(cr, thumb_pos, y_offset, thumb_size, thumb_height, 3)
            cr.fill()

    def rounded_rectangle(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + r, y + r, r, 3.14159, 3 * 3.14159 / 2)
        cr.arc(x + w - r, y + r, r, 3 * 3.14159 / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 3.14159 / 2)
        cr.arc(x + r, y + h - r, r, 3.14159 / 2, 3.14159)
        cr.close_path()

    def on_click(self, gesture, n, x, y):
        if self.orientation == Gtk.Orientation.VERTICAL:
            self.handle_vclick(y)
        else:
            self.handle_hclick(x)

    def handle_vclick(self, y):
        v = self.view
        thumb_y, thumb_h = self.get_vthumb_bounds()

        if thumb_y <= y <= thumb_y + thumb_h:
            return

        visible = max(1, v.get_allocated_height() // v.renderer.line_h)
        if y < thumb_y:
            v.scroll_line = max(0, v.scroll_line - visible)
        else:
            total = v.buf.total()
            max_scroll = max(0, total - visible)
            v.scroll_line = min(max_scroll, v.scroll_line + visible)

        v.queue_draw()
        self.queue_draw()

    def handle_hclick(self, x):
        v = self.view
        thumb_x, thumb_w = self.get_hthumb_bounds()

        if thumb_x <= x <= thumb_x + thumb_w:
            return

        view_width = v.get_allocated_width() - v.renderer.ln_width
        
        if x < thumb_x:
            v.scroll_x = max(0, v.scroll_x - view_width // 2)
        else:
            v.scroll_x = v.scroll_x + view_width // 2

        v.queue_draw()
        self.queue_draw()

    def on_drag_begin(self, gesture, start_x, start_y):
        if self.orientation == Gtk.Orientation.VERTICAL:
            thumb_y, thumb_h = self.get_vthumb_bounds()
            if thumb_y <= start_y <= thumb_y + thumb_h:
                self.drag_active = True
                self.drag_offset = start_y - thumb_y
                self.queue_draw()
        else:
            thumb_x, thumb_w = self.get_hthumb_bounds()
            if thumb_x <= start_x <= thumb_x + thumb_w:
                self.drag_active = True
                self.drag_offset = start_x - thumb_x
                self.queue_draw()

    def on_drag(self, gesture, dx, dy):
        if not self.drag_active:
            return

        if self.orientation == Gtk.Orientation.VERTICAL:
            self.handle_vdrag(gesture, dy)
        else:
            self.handle_hdrag(gesture, dx)

    def handle_vdrag(self, gesture, dy):
        v = self.view
        h = self.get_allocated_height()
        visible = max(1, v.get_allocated_height() // v.renderer.line_h)

        buf = v.buf
        total = buf.total()
        max_scroll = max(0, total - visible)

        if max_scroll == 0:
            return

        gesture_ok, sx, sy = gesture.get_start_point()
        thumb_y, thumb_h = self.get_vthumb_bounds()

        new_y = sy + dy - self.drag_offset
        new_y = max(0, min(new_y, h - thumb_h))

        frac = new_y / (h - thumb_h) if h > thumb_h else 0
        v.scroll_line = int(frac * max_scroll)

        v.queue_draw()
        self.queue_draw()

    def handle_hdrag(self, gesture, dx):
        v = self.view
        w = self.get_allocated_width()

        gesture_ok, sx, sy = gesture.get_start_point()
        thumb_x, thumb_w = self.get_hthumb_bounds()

        new_x = sx + dx - self.drag_offset
        new_x = max(0, min(new_x, w - thumb_w))

        max_line_width = v.get_max_line_width()
        view_width = v.get_allocated_width() - v.renderer.ln_width
        max_scroll = max(0, max_line_width - view_width)

        if max_scroll == 0:
            return

        frac = new_x / (w - thumb_w) if w > thumb_w else 0
        v.scroll_x = int(frac * max_scroll)

        v.queue_draw()
        self.queue_draw()

    def on_drag_end(self, *args):
        self.drag_active = False
        self.queue_draw()


# ============================================================
#   ULTRAVIEW (TEXT AREA - EDITABLE)
# ============================================================

class UltraView(Gtk.DrawingArea):
    def __init__(self, buf):
        super().__init__()

        self.buf = buf
        self.renderer = Renderer()
        self.ctrl = InputController(self, buf)

        self.scroll_line = 0
        self.scroll_x = 0

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        self.install_mouse()
        self.install_keys()
        self.install_scroll()
        self.install_ime()

    def get_max_line_width(self):
        """Calculate maximum line width in pixels"""
        max_width = 0
        layout = Pango.Layout(Pango.Context())
        layout.set_font_description(self.renderer.font)
        
        for i in range(self.buf.total()):
            text = self.buf.get_line(i)
            if text:
                layout.set_text(text)
                w, h = layout.get_pixel_size()
                max_width = max(max_width, w)
        
        return max_width

    def install_ime(self):
        """Install IME (Input Method Editor) for multilingual text"""
        self.im_context = Gtk.IMMulticontext()
        self.im_context.connect("commit", self.on_im_commit)

    def on_im_commit(self, im_context, text):
        """Handle IME text input (Hindi, Chinese, etc.)"""
        if self.ctrl.has_selection():
            start, end = self.ctrl.get_selection_range()
            self.buf.delete_selection(start, end)
            self.ctrl.clear_selection()
        
        self.buf.insert_text(text)
        self.keep_cursor_visible()
        self.queue_draw()

    def on_draw(self, area, cr, w, h):
        alloc = area.get_allocation()
        self.renderer.draw(
            cr, alloc, self.buf,
            self.scroll_line, self.scroll_x,
            self.ctrl.sel_start, self.ctrl.sel_end
        )

    # ---------------------------------------------------------
    # MOUSE
    # ---------------------------------------------------------
    def install_mouse(self):
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def on_click(self, g, n, x, y):
        self.grab_focus()
        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = min(ln, self.buf.total() - 1)

        text = self.buf.get_line(ln)
        x_in_text = x - self.renderer.ln_width + self.scroll_x
        col = self.renderer.get_col_at_x(text, x_in_text)

        self.ctrl.click(ln, col)
        self.queue_draw()

    def on_drag_begin(self, g, x, y):
        self.ctrl.selecting = True

    def on_drag(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        ln = self.scroll_line + int((sy + dy) // self.renderer.line_h)
        ln = min(ln, self.buf.total() - 1)

        text = self.buf.get_line(ln)
        x_in_text = sx + dx - self.renderer.ln_width + self.scroll_x
        col = self.renderer.get_col_at_x(text, x_in_text)

        self.ctrl.drag(ln, col)
        self.buf.set_cursor(ln, col)
        self.queue_draw()

    def on_drag_end(self, g, x, y):
        self.ctrl.selecting = False

    # ---------------------------------------------------------
    # KEYBOARD
    # ---------------------------------------------------------
    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    def on_key(self, c, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        shift = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0

        # Let IME handle the key first
        if self.im_context.filter_keypress(c.get_current_event()):
            return True

        # Handle special keys
        if name == "Return" or name == "KP_Enter":
            if self.ctrl.has_selection():
                start, end = self.ctrl.get_selection_range()
                self.buf.delete_selection(start, end)
                self.ctrl.clear_selection()
            self.buf.insert_newline()
            
        elif name == "BackSpace":
            if self.ctrl.has_selection():
                start, end = self.ctrl.get_selection_range()
                self.buf.delete_selection(start, end)
                self.ctrl.clear_selection()
            else:
                self.buf.backspace()
                
        elif name == "Delete":
            if self.ctrl.has_selection():
                start, end = self.ctrl.get_selection_range()
                self.buf.delete_selection(start, end)
                self.ctrl.clear_selection()
            else:
                self.buf.delete()
                
        elif name == "Tab":
            if self.ctrl.has_selection():
                start, end = self.ctrl.get_selection_range()
                self.buf.delete_selection(start, end)
                self.ctrl.clear_selection()
            self.buf.insert_text("    ")
            
        # Navigation
        elif name == "Left": 
            self.ctrl.move_left(shift)
        elif name == "Right": 
            self.ctrl.move_right(shift)
        elif name == "Up": 
            self.ctrl.move_up(shift)
        elif name == "Down": 
            self.ctrl.move_down(shift)
        elif name == "Page_Up": 
            self.page_up(shift)
        elif name == "Page_Down": 
            self.page_down(shift)
        elif name == "Home": 
            self.go_home(shift)
        elif name == "End": 
            self.go_end(shift)
            
        # Select all
        elif ctrl_pressed and name == "a":
            total = self.buf.total()
            if total > 0:
                self.ctrl.sel_start = (0, 0)
                last_line = total - 1
                last_col = len(self.buf.get_line(last_line))
                self.ctrl.sel_end = (last_line, last_col)
                self.buf.set_cursor(last_line, last_col)
        else:
            return False

        self.keep_cursor_visible()
        self.queue_draw()
        return True

    def page_up(self, extend=False):
        visible = max(1, self.get_allocated_height() // self.renderer.line_h)
        target = max(0, self.buf.cursor_line - visible)
        self.buf.set_cursor(target, self.buf.cursor_col)
        
        if extend:
            self.ctrl.sel_end = (self.buf.cursor_line, self.buf.cursor_col)
        else:
            self.ctrl.clear_selection()

    def page_down(self, extend=False):
        visible = max(1, self.get_allocated_height() // self.renderer.line_h)
        total = self.buf.total()
        target = min(total - 1, self.buf.cursor_line + visible)
        self.buf.set_cursor(target, self.buf.cursor_col)
        
        if extend:
            self.ctrl.sel_end = (self.buf.cursor_line, self.buf.cursor_col)
        else:
            self.ctrl.clear_selection()

    def go_home(self, extend=False):
        self.buf.set_cursor(0, 0)
        
        if extend:
            self.ctrl.sel_end = (0, 0)
        else:
            self.ctrl.clear_selection()

    def go_end(self, extend=False):
        total = self.buf.total()
        if total > 0:
            last_line = total - 1
            last_col = len(self.buf.get_line(last_line))
            self.buf.set_cursor(last_line, last_col)
            
            if extend:
                self.ctrl.sel_end = (last_line, last_col)
            else:
                self.ctrl.clear_selection()

    def keep_cursor_visible(self):
        max_vis = self.get_allocated_height() // self.renderer.line_h
        cl = self.buf.cursor_line

        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

    # ---------------------------------------------------------
    # SCROLL
    # ---------------------------------------------------------
    def install_scroll(self):
        flags = Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        sc = Gtk.EventControllerScroll.new(flags)
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        total = self.buf.total()
        visible = max(1, self.get_allocated_height() // self.renderer.line_h)
        max_scroll = max(0, total - visible)
        
        if dy:
            self.scroll_line = max(0, min(self.scroll_line + int(dy * 4), max_scroll))

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True


# ============================================================
#   MAIN WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("UltraEditor v3.8 — IME + Auto-sizing")
        self.set_default_size(640, 480)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)
        
        self.vscrollbar = CustomScrollbar(self.view, Gtk.Orientation.VERTICAL)
        self.hscrollbar = CustomScrollbar(self.view, Gtk.Orientation.HORIZONTAL)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        # File menu
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        header.pack_end(menu_button)

        menu = Gio.Menu()
        
        menu.append("Open", "win.open")
        menu.append("Save", "win.save")
        menu.append("Save As...", "win.save-as")
        menu.append("Font...", "win.font")
        
        menu_button.set_menu_model(menu)

        # Actions
        open_action = Gio.SimpleAction.new("open", None)
        open_action.connect("activate", self.open_file)
        self.add_action(open_action)

        save_action = Gio.SimpleAction.new("save", None)
        save_action.connect("activate", self.save_file)
        self.add_action(save_action)

        save_as_action = Gio.SimpleAction.new("save-as", None)
        save_as_action.connect("activate", self.save_as_file)
        self.add_action(save_as_action)

        font_action = Gio.SimpleAction.new("font", None)
        font_action.connect("activate", self.choose_font)
        self.add_action(font_action)

        # Main grid layout
        grid = Gtk.Grid()
        grid.attach(self.view, 0, 0, 1, 1)
        grid.attach(self.vscrollbar, 1, 0, 1, 1)
        grid.attach(self.hscrollbar, 0, 1, 1, 1)

        layout.set_content(grid)

        # Connect events
        self.view.connect("resize", self.on_view_resize)
        self.buf.connect("changed", self.on_buffer_changed)
        
        self.current_file = None

    def on_view_resize(self, *args):
        self.update_horizontal_scrollbar()
        self.vscrollbar.queue_draw()
        self.hscrollbar.queue_draw()

    def on_buffer_changed(self, *args):
        self.update_horizontal_scrollbar()
        self.view.queue_draw()
        self.vscrollbar.queue_draw()
        self.hscrollbar.queue_draw()

    def update_horizontal_scrollbar(self):
        """Show/hide horizontal scrollbar based on content width"""
        max_width = self.view.get_max_line_width()
        view_width = self.view.get_allocated_width() - self.view.renderer.ln_width
        
        # Show scrollbar only if content is wider than view
        should_show = max_width > view_width
        self.hscrollbar.set_visible_scrollbar(should_show)

    def open_file(self, *args):
        dialog = Gtk.FileDialog()

        def done(dialog, res):
            try:
                f = dialog.open_finish(res)
            except:
                return
            path = f.get_path()
            if not path:
                return

            self.current_file = path
            self.buf.load_from_file(path)

            self.view.scroll_line = 0
            self.view.scroll_x = 0
            
            self.view.queue_draw()
            self.vscrollbar.queue_draw()
            self.hscrollbar.queue_draw()

            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)

    def save_file(self, *args):
        if not self.current_file:
            self.save_as_file()
        else:
            self.do_save(self.current_file)

    def save_as_file(self, *args):
        dialog = Gtk.FileDialog()
        
        def done(dialog, res):
            try:
                f = dialog.save_finish(res)
            except:
                return
            path = f.get_path()
            if path:
                self.current_file = path
                self.do_save(path)
        
        dialog.save(self, None, done)

    def do_save(self, path):
        """Actually save the file"""
        try:
            text = "\n".join(self.buf.lines)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.buf.modified = False
            self.set_title(os.path.basename(path))
        except Exception as e:
            print(f"Error saving file: {e}")

    def choose_font(self, *args):
        """Open font chooser dialog"""
        dialog = Gtk.FontDialog()
        
        # Create initial font description
        initial_desc = Pango.FontDescription(f"{self.view.renderer.font_family} {self.view.renderer.font_size}")
        
        def done(dialog, res):
            try:
                font_desc = dialog.choose_font_finish(res)
                if font_desc:
                    self.view.renderer.font_family = font_desc.get_family()
                    self.view.renderer.font_size = font_desc.get_size() // Pango.SCALE
                    self.view.renderer.update_font()
                    self.view.queue_draw()
                    self.update_horizontal_scrollbar()
            except:
                pass
        
        dialog.choose_font(self, initial_desc, None, done)


# ============================================================
#   APPLICATION
# ============================================================

class UltraEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v38")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    UltraEditorApp().run(sys.argv)