#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject, GLib, Pango, PangoCairo


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
#   LAZY MMAP FILE (UTF8/UTF16 safe)
# ============================================================

class LazyFile:
    CHUNK = 2000

    def __init__(self, path):
        self.path = path
        self.encoding = detect_encoding(path)

        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)

        # For UTF-16, we need to decode the entire file and split by lines
        # because readline() doesn't work properly with multi-byte encodings
        if self.encoding.startswith("utf-16"):
            raw_data = self.mm.read()
            text = raw_data.decode(self.encoding, errors="replace")
            self.lines_cache = text.splitlines()
            self.index = list(range(len(self.lines_cache) + 1))
            self.eof = True
            self.is_utf16 = True
        else:
            self.lines_cache = None
            self.index = [0]
            self.eof = False
            self.is_utf16 = False

    def line_count_known(self):
        return len(self.index) - 1

    def total_lines(self):
        """Get total line count (forces full indexing if needed)"""
        if self.is_utf16:
            return len(self.lines_cache)
        
        if not self.eof:
            # Index the entire file
            self._index_to_end()
        return len(self.index) - 1

    def _index_to_end(self):
        """Index all remaining lines"""
        if self.eof:
            return

        mm = self.mm
        mm.seek(self.index[-1])
        while True:
            line = mm.readline()
            if not line:
                self.eof = True
                break
            self.index.append(mm.tell())

    def _index_up_to(self, target_line):
        if self.eof or self.is_utf16:
            return

        known = self.line_count_known()
        if target_line <= known:
            return

        need = target_line - known
        limit = min(need, LazyFile.CHUNK)

        mm = self.mm
        mm.seek(self.index[-1])
        for _ in range(limit):
            line = mm.readline()
            if not line:
                self.eof = True
                break
            self.index.append(mm.tell())

    def __getitem__(self, i):
        if self.is_utf16:
            if 0 <= i < len(self.lines_cache):
                return self.lines_cache[i]
            return ""

        self._index_up_to(i)
        if i >= self.line_count_known():
            return ""

        mm = self.mm
        mm.seek(self.index[i])
        raw = mm.readline()

        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")


# ============================================================
#   VIRTUAL BUFFER
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

    def load(self, lazyfile):
        self.lines = lazyfile
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def known(self):
        lf = self.lines
        if isinstance(lf, LazyFile):
            return lf.line_count_known()
        return len(lf)

    def total(self):
        """Get total line count"""
        lf = self.lines
        if isinstance(lf, LazyFile):
            return lf.total_lines()
        return len(lf)

    def get_line(self, i):
        lf = self.lines
        if isinstance(lf, LazyFile):
            return lf[i]
        return lf[i] if 0 <= i < len(lf) else ""

    def set_cursor(self, line, col):
        lf = self.lines
        known = self.known()

        if isinstance(lf, LazyFile) and line >= known:
            lf._index_up_to(line)
            known = self.known()
            if line >= known:
                line = known - 1

        if line < 0:
            line = 0

        row = self.get_line(line)
        col = max(0, min(col, len(row)))

        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)


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

    def move_left(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        
        if not extend and self.has_selection():
            # Move to start of selection
            start, end = self.get_selection_range()
            b.set_cursor(start[0], start[1])
            self.sel_start = self.sel_end = (start[0], start[1])
            return
        
        if c > 0:
            b.set_cursor(l, c - 1)
        elif l > 0:
            prev = b.get_line(l - 1)
            b.set_cursor(l - 1, len(prev))
        
        if extend:
            self.sel_end = (b.cursor_line, b.cursor_col)
        else:
            self.sel_start = self.sel_end = (b.cursor_line, b.cursor_col)

    def move_right(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        
        if not extend and self.has_selection():
            # Move to end of selection
            start, end = self.get_selection_range()
            b.set_cursor(end[0], end[1])
            self.sel_start = self.sel_end = (end[0], end[1])
            return
        
        row = b.get_line(l)
        if c < len(row):
            b.set_cursor(l, c + 1)
        else:
            if l + 1 < b.known():
                b.set_cursor(l + 1, 0)
        
        if extend:
            self.sel_end = (b.cursor_line, b.cursor_col)
        else:
            self.sel_start = self.sel_end = (b.cursor_line, b.cursor_col)

    def move_up(self, extend=False):
        b = self.buf
        if b.cursor_line > 0:
            l = b.cursor_line - 1
            row = b.get_line(l)
            b.set_cursor(l, min(b.cursor_col, len(row)))
            
            if extend:
                self.sel_end = (b.cursor_line, b.cursor_col)
            else:
                self.sel_start = self.sel_end = (b.cursor_line, b.cursor_col)

    def move_down(self, extend=False):
        b = self.buf
        t = b.cursor_line + 1
        if t < b.known():
            row = b.get_line(t)
            b.set_cursor(t, min(b.cursor_col, len(row)))
            
            if extend:
                self.sel_end = (b.cursor_line, b.cursor_col)
            else:
                self.sel_start = self.sel_end = (b.cursor_line, b.cursor_col)


# ============================================================
#   RENDERER (TEXT + LINE NUMBERS + SELECTION)
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 21
        self.char_w = 9

        self.bg = (0.10, 0.10, 0.10)
        self.fg = (0.90, 0.90, 0.90)
        self.ln_fg = (0.60, 0.60, 0.60)
        self.sel_bg = (0.30, 0.40, 0.60)

        self.ln_width = 80  # line number column

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
                    # Determine selection bounds for this line
                    if ln == start_l and ln == end_l:
                        # Selection within single line
                        sel_start_x = self.ln_width + (start_c * self.char_w) - scroll_x
                        sel_end_x = self.ln_width + (end_c * self.char_w) - scroll_x
                    elif ln == start_l:
                        # First line of multi-line selection
                        sel_start_x = self.ln_width + (start_c * self.char_w) - scroll_x
                        sel_end_x = self.ln_width + (len(text) * self.char_w) - scroll_x
                    elif ln == end_l:
                        # Last line of multi-line selection
                        sel_start_x = self.ln_width - scroll_x
                        sel_end_x = self.ln_width + (end_c * self.char_w) - scroll_x
                    else:
                        # Middle line - select entire line
                        sel_start_x = self.ln_width - scroll_x
                        sel_end_x = self.ln_width + (len(text) * self.char_w) - scroll_x
                    
                    cr.set_source_rgb(*self.sel_bg)
                    cr.rectangle(sel_start_x, y, sel_end_x - sel_start_x, self.line_h)
                    cr.fill()

            # Draw line number
            layout.set_text(str(ln + 1))
            cr.set_source_rgb(*self.ln_fg)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # Draw text
            layout.set_text(text)
            cr.set_source_rgb(*self.fg)
            cr.move_to(self.ln_width - scroll_x, y)
            PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # Draw cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cx = self.ln_width + (cc * self.char_w) - scroll_x
            cy = (cl - scroll_line) * self.line_h
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()


# ============================================================
#   CUSTOM SCROLLBAR (GTK-style behavior)
# ============================================================

class CustomScrollbar(Gtk.DrawingArea):
    NORMAL_WIDTH = 8
    HOVER_WIDTH = 14
    
    def __init__(self, text_view, orientation=Gtk.Orientation.VERTICAL):
        super().__init__()

        self.view = text_view
        self.orientation = orientation

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
            # Update size request based on hover
            if self.orientation == Gtk.Orientation.VERTICAL:
                self.set_size_request(self.HOVER_WIDTH if state else self.NORMAL_WIDTH, -1)
            else:
                self.set_size_request(-1, self.HOVER_WIDTH if state else self.NORMAL_WIDTH)
            self.queue_draw()

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
        """Returns (position, size) of thumb"""
        if self.orientation == Gtk.Orientation.VERTICAL:
            return self.get_vthumb_bounds()
        else:
            return self.get_hthumb_bounds()

    def get_vthumb_bounds(self):
        """Vertical thumb bounds"""
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
        """Horizontal thumb bounds"""
        w = self.get_allocated_width()
        
        max_x = 2000  # Max scrollable width
        view_width = (self.view.get_allocated_width() - self.view.renderer.ln_width) // self.view.renderer.char_w
        
        if view_width >= max_x:
            return 0, w

        thumb_w = max(30, w * (view_width / max_x))
        current_char = self.view.scroll_x // self.view.renderer.char_w
        max_scroll = max_x - view_width
        
        if max_scroll <= 0:
            return 0, w
            
        x = (current_char / max_scroll) * (w - thumb_w)
        return x, thumb_w

    def on_draw(self, area, cr, w, h):
        # Background - only visible on hover
        if self.hover or self.drag_active:
            cr.set_source_rgba(0.2, 0.2, 0.2, 0.5)
            cr.rectangle(0, 0, w, h)
            cr.fill()

        # Thumb
        if self.orientation == Gtk.Orientation.VERTICAL:
            thumb_pos, thumb_size = self.get_vthumb_bounds()
            
            if self.drag_active:
                cr.set_source_rgba(0.75, 0.75, 0.75, 0.95)
            elif self.hover_thumb or self.hover:
                cr.set_source_rgba(0.65, 0.65, 0.65, 0.85)
            else:
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            
            # Rounded rectangle for thumb - thinner when not hovering
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
        """Draw rounded rectangle"""
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
        """Handle vertical scrollbar click"""
        v = self.view
        thumb_y, thumb_h = self.get_vthumb_bounds()

        # Click on thumb - will be handled by drag
        if thumb_y <= y <= thumb_y + thumb_h:
            return

        # Page up/down
        visible = max(1, v.get_allocated_height() // v.renderer.line_h)
        if y < thumb_y:
            v.scroll_line = max(0, v.scroll_line - visible)
        else:
            total = v.buf.total()
            max_scroll = max(0, total - visible)
            v.scroll_line = min(max_scroll, v.scroll_line + visible)

        v.ensure_visible_indexed(v.scroll_line)
        v.queue_draw()
        self.queue_draw()

    def handle_hclick(self, x):
        """Handle horizontal scrollbar click"""
        v = self.view
        thumb_x, thumb_w = self.get_hthumb_bounds()

        if thumb_x <= x <= thumb_x + thumb_w:
            return

        # Page left/right
        view_width = (v.get_allocated_width() - v.renderer.ln_width) // v.renderer.char_w
        scroll_amount = view_width * v.renderer.char_w
        
        if x < thumb_x:
            v.scroll_x = max(0, v.scroll_x - scroll_amount)
        else:
            v.scroll_x = v.scroll_x + scroll_amount

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
        """Handle vertical drag"""
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

        v.ensure_visible_indexed(v.scroll_line + visible)
        v.queue_draw()
        self.queue_draw()

    def handle_hdrag(self, gesture, dx):
        """Handle horizontal drag"""
        v = self.view
        w = self.get_allocated_width()

        gesture_ok, sx, sy = gesture.get_start_point()
        thumb_x, thumb_w = self.get_hthumb_bounds()

        new_x = sx + dx - self.drag_offset
        new_x = max(0, min(new_x, w - thumb_w))

        max_scroll_chars = 2000
        view_width = (v.get_allocated_width() - v.renderer.ln_width) // v.renderer.char_w
        max_scroll = max(0, max_scroll_chars - view_width)

        if max_scroll == 0:
            return

        frac = new_x / (w - thumb_w) if w > thumb_w else 0
        char_pos = int(frac * max_scroll)
        v.scroll_x = char_pos * v.renderer.char_w

        v.queue_draw()
        self.queue_draw()

    def on_drag_end(self, *args):
        self.drag_active = False
        self.queue_draw()


# ============================================================
#   ULTRAVIEW (TEXT AREA)
# ============================================================

class UltraView(Gtk.DrawingArea):
    SAFETY_MARGIN = 1500

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

    def ensure_visible_indexed(self, line):
        lf = self.buf.lines
        if isinstance(lf, LazyFile) and not lf.is_utf16:
            lf._index_up_to(line + UltraView.SAFETY_MARGIN)

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
        self.ensure_visible_indexed(ln)

        col = int((x - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(ln))))

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
        self.ensure_visible_indexed(ln)

        col = int((sx + dx - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(ln))))

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

        if name == "Left": self.ctrl.move_left(shift)
        elif name == "Right": self.ctrl.move_right(shift)
        elif name == "Up": self.ctrl.move_up(shift)
        elif name == "Down": self.ctrl.move_down(shift)
        elif name == "Page_Up": self.page_up(shift)
        elif name == "Page_Down": self.page_down(shift)
        elif name == "Home": self.go_home(shift)
        elif name == "End": self.go_end(shift)
        else:
            return False

        self.ensure_visible_indexed(self.buf.cursor_line)
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
            self.ctrl.sel_start = self.ctrl.sel_end = (self.buf.cursor_line, self.buf.cursor_col)

    def page_down(self, extend=False):
        visible = max(1, self.get_allocated_height() // self.renderer.line_h)
        total = self.buf.total()
        target = min(total - 1, self.buf.cursor_line + visible)
        self.buf.set_cursor(target, self.buf.cursor_col)
        
        if extend:
            self.ctrl.sel_end = (self.buf.cursor_line, self.buf.cursor_col)
        else:
            self.ctrl.sel_start = self.ctrl.sel_end = (self.buf.cursor_line, self.buf.cursor_col)

    def go_home(self, extend=False):
        self.buf.set_cursor(0, 0)
        
        if extend:
            self.ctrl.sel_end = (0, 0)
        else:
            self.ctrl.sel_start = self.ctrl.sel_end = (0, 0)

    def go_end(self, extend=False):
        total = self.buf.total()
        if total > 0:
            last_line = total - 1
            last_col = len(self.buf.get_line(last_line))
            self.buf.set_cursor(last_line, last_col)
            
            if extend:
                self.ctrl.sel_end = (last_line, last_col)
            else:
                self.ctrl.sel_start = self.ctrl.sel_end = (last_line, last_col)

    def keep_cursor_visible(self):
        max_vis = self.get_allocated_height() // self.renderer.line_h
        cl = self.buf.cursor_line

        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

        self.ensure_visible_indexed(self.scroll_line + max_vis)

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
            self.ensure_visible_indexed(self.scroll_line)

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
        self.set_title("UltraEditor v3.6 — Thin Scrollbars + Selection")
        self.set_default_size(1000, 700)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)
        
        self.vscrollbar = CustomScrollbar(self.view, Gtk.Orientation.VERTICAL)
        self.hscrollbar = CustomScrollbar(self.view, Gtk.Orientation.HORIZONTAL)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        # Main grid layout
        grid = Gtk.Grid()
        grid.attach(self.view, 0, 0, 1, 1)
        grid.attach(self.vscrollbar, 1, 0, 1, 1)
        grid.attach(self.hscrollbar, 0, 1, 1, 1)

        layout.set_content(grid)

        # Connect scroll events to update scrollbars
        self.view.connect("resize", self.on_view_resize)

    def on_view_resize(self, *args):
        self.vscrollbar.queue_draw()
        self.hscrollbar.queue_draw()

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, res):
            try:
                f = dialog.open_finish(res)
            except:
                return
            path = f.get_path()
            if not path:
                return

            lf = LazyFile(path)
            self.buf.load(lf)

            self.view.scroll_line = 0
            self.view.scroll_x = 0
            
            # Force initial draw
            self.view.queue_draw()
            self.vscrollbar.queue_draw()
            self.hscrollbar.queue_draw()

            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)









# ============================================================
#   APPLICATION
# ============================================================

class UltraEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v36")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    UltraEditorApp().run(sys.argv)