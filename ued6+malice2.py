#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo

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

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.sel_start = (ln, col)
        self.sel_end = (ln, col)

    def drag(self, ln, col):
        if self.selecting:
            self.sel_end = (ln, col)

    def has_selection(self):
        return self.sel_start and self.sel_end and self.sel_start != self.sel_end

    def get_selection_range(self):
        (sl, sc) = self.sel_start
        (el, ec) = self.sel_end
        if (sl, sc) <= (el, ec):
            return (sl, sc), (el, ec)
        return (el, ec), (sl, sc)

    def clear_selection(self):
        cl = self.buf.cursor_line
        cc = self.buf.cursor_col
        self.sel_start = (cl, cc)
        self.sel_end = (cl, cc)

    # --------------------------------------------------------
    # CURSOR MOTION
    # --------------------------------------------------------

    def move_left(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col

        if not extend and self.has_selection():
            s, e = self.get_selection_range()
            b.set_cursor(s[0], s[1])
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
        row = b.get_line(l)

        if not extend and self.has_selection():
            s, e = self.get_selection_range()
            b.set_cursor(e[0], e[1])
            self.clear_selection()
            return

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
#   RENDERER (continued from Part 1)
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 21
        self.char_w = 9
        self.ln_width = 80
        self.bg = (0.10, 0.10, 0.10)
        self.fg = (0.90, 0.90, 0.90)
        self.ln_fg = (0.60, 0.60, 0.60)
        self.sel_bg = (0.30, 0.40, 0.60)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        total = buf.total()
        max_vis = alloc.height // self.line_h

        sel_range = None
        if sel_s and sel_e and sel_s != sel_e:
            if sel_s <= sel_e:
                sel_range = (sel_s, sel_e)
            else:
                sel_range = (sel_e, sel_s)

        y = 0
        for ln in range(scroll_line, min(scroll_line + max_vis, total)):
            text = buf.get_line(ln)

            # selection background
            if sel_range:
                (sl, sc), (el, ec) = sel_range
                if sl <= ln <= el:
                    if sl == el:
                        xs = self.ln_width + sc * self.char_w - scroll_x
                        xe = self.ln_width + ec * self.char_w - scroll_x
                    elif ln == sl:
                        xs = self.ln_width + sc * self.char_w - scroll_x
                        xe = self.ln_width + (len(text) + 1) * self.char_w - scroll_x
                    elif ln == el:
                        xs = self.ln_width - scroll_x
                        xe = self.ln_width + ec * self.char_w - scroll_x
                    else:
                        xs = self.ln_width - scroll_x
                        xe = self.ln_width + max(1, len(text)) * self.char_w - scroll_x

                    cr.set_source_rgb(*self.sel_bg)
                    cr.rectangle(xs, y, xe - xs, self.line_h)
                    cr.fill()

            # line number
            layout.set_text(str(ln + 1))
            cr.set_source_rgb(*self.ln_fg)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # text
            layout.set_text(text)
            cr.set_source_rgb(*self.fg)
            cr.move_to(self.ln_width - scroll_x, y)
            PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cx = self.ln_width + cc * self.char_w - scroll_x
            cy = (cl - scroll_line) * self.line_h
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()


# ============================================================
#   ULTRA VIEW (remaining part)
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

    def on_draw(self, area, cr, w, h):
        alloc = self.get_allocation()
        self.renderer.draw(
            cr, alloc, self.buf,
            self.scroll_line, self.scroll_x,
            self.ctrl.sel_start, self.ctrl.sel_end
        )

    # ---------------------------------------------------------
    # MOUSE INPUT
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

        col = int((x - self.renderer.ln_width + self.scroll_x) // self.renderer.char_w)
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
        col = int((sx + dx - self.renderer.ln_width + self.scroll_x) // self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(ln))))

        self.ctrl.drag(ln, col)
        self.buf.set_cursor(ln, col)
        self.queue_draw()

    def on_drag_end(self, g, x, y):
        self.ctrl.selecting = False

    # ---------------------------------------------------------
    # KEY INPUT
    # ---------------------------------------------------------

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    def on_key(self, c, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        # ENTER
        if name in ("Return", "KP_Enter"):
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            self.buf.insert_newline()

        # BACKSPACE
        elif name == "BackSpace":
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            else:
                self.buf.backspace()

        # DELETE
        elif name == "Delete":
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            else:
                self.buf.delete()

        # LEFT/RIGHT/UP/DOWN
        elif name == "Left":
            self.ctrl.move_left(shift)
        elif name == "Right":
            self.ctrl.move_right(shift)
        elif name == "Up":
            self.ctrl.move_up(shift)
        elif name == "Down":
            self.ctrl.move_down(shift)

        # CHAR INPUT
        elif len(name) == 1 and not ctrl:
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            self.buf.insert_char(name)

        else:
            return False

        self.keep_cursor_visible()
        self.queue_draw()
        return True

    # ---------------------------------------------------------
    # KEEP CURSOR IN VIEW
    # ---------------------------------------------------------

    def keep_cursor_visible(self):
        alloc = self.get_allocation()
        max_vis = alloc.height // self.renderer.line_h
        cl = self.buf.cursor_line

        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

    # ---------------------------------------------------------
    # SCROLLING
    # ---------------------------------------------------------

    def install_scroll(self):
        flags = (
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc = Gtk.EventControllerScroll.new(flags)
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        alloc = self.get_allocation()
        total = self.buf.total()
        visible = max(1, alloc.height // self.renderer.line_h)
        max_scroll = max(0, total - visible)

        if dy:
            self.scroll_line = max(0, min(self.scroll_line + int(dy * 4), max_scroll))
        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True

# ============================================================
#   ENCODING DETECTION
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
#   INDEXED FILE (READ-ONLY)
# ============================================================

class IndexedFile:
    def __init__(self, path):
        self.path = path
        self.encoding = detect_encoding(path)
        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)
        self.index = []
        self._build()

    def _build(self):
        if self.encoding.startswith("utf-16"):
            self._index_utf16()
        else:
            self._index_utf8()

    def _index_utf8(self):
        mm = self.mm
        mm.seek(0)
        self.index = [0]
        while True:
            ln = mm.readline()
            if not ln:
                break
            self.index.append(mm.tell())

    def _index_utf16(self):
        raw = self.mm[:]
        text = raw.decode(self.encoding, errors="replace")
        w = 2
        offs = []
        for i, ch in enumerate(text):
            if ch == "\n":
                offs.append((i + 1) * w)
        offs.append(len(raw))
        self.index = [0] + offs

    def total_lines(self):
        return len(self.index) - 1

    def get_line_raw(self, line):
        if line < 0 or line >= self.total_lines():
            return ""
        s = self.index[line]
        e = self.index[line + 1]
        raw = self.mm[s:e]
        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")

# ============================================================
#   EDIT OVERLAY
# ============================================================

class EditOverlay:
    def __init__(self):
        self.inserts = {}
        self.deletes = []

    def add_insert(self, line, col, text):
        self.inserts.setdefault(line, []).append((col, text))

    def add_delete(self, s, e):
        self.deletes.append((s, e))

    def clear(self):
        self.inserts.clear()
        self.deletes.clear()

# ============================================================
#   VIRTUAL BUFFER
# ============================================================

class VirtualBuffer(GObject.Object):

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()
        self.indexed = None
        self.overlay = EditOverlay()
        self.cursor_line = 0
        self.cursor_col = 0

    def load_indexed(self, indexed):
        self.indexed = indexed
        self.overlay.clear()
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def total(self):
        if self.indexed is None:
            return 1
        return self.indexed.total_lines()

    def get_line(self, ln):
        if self.indexed is None:
            return ""
        base = self.indexed.get_line_raw(ln)

        for (s_l, s_c), (e_l, e_c) in self.overlay.deletes:
            if s_l == e_l == ln:
                base = base[:s_c] + base[e_c:]
            elif s_l == ln:
                base = base[:s_c]
            elif e_l == ln:
                base = base[e_c:]
            elif s_l < ln < e_l:
                base = ""

        if ln in self.overlay.inserts:
            parts = []
            cur = 0
            inserts = sorted(self.overlay.inserts[ln], key=lambda x: x[0])
            for col, text in inserts:
                parts.append(base[cur:col])
                parts.append(text)
                cur = col
            parts.append(base[cur:])
            return "".join(parts)

        return base

    def set_cursor(self, line, col):
        line = max(0, min(line, self.total() - 1))
        row = self.get_line(line)
        col = max(0, min(col, len(row)))
        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)

    def insert_char(self, ch):
        l, c = self.cursor_line, self.cursor_col
        self.overlay.add_insert(l, c, ch)
        self.cursor_col += len(ch)
        self.emit("changed")

    def insert_newline(self):
        l, c = self.cursor_line, self.cursor_col
        self.overlay.add_insert(l, c, "\n")
        self.cursor_line += 1
        self.cursor_col = 0
        self.emit("changed")

    def delete_selection(self, start, end):
        self.overlay.add_delete(start, end)
        (sl, sc) = start
        self.set_cursor(sl, sc)
        self.emit("changed")

    def backspace(self):
        l, c = self.cursor_line, self.cursor_col
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
        row = b.get_line(l)
        if not extend and self.has_selection():
            s, e = self.get_selection_range()
            b.set_cursor(e[0], e[1])
            self.clear_selection()
            return
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
#   RENDERER (continued)
# ============================================================

# ============================================================
#   SCROLLBAR SYSTEM — HOVER-ANIMATED VERTICAL + AUTO HORIZONTAL
# ============================================================

class HoverScrollbar(Gtk.DrawingArea):
    """Base class implementing hover-expand, animated scrollbars.
       Orientation is Gtk.Orientation.VERTICAL or Gtk.Orientation.HORIZONTAL.
    """

    NORMAL_THICKNESS = 6
    HOVER_THICKNESS = 14
    RADIUS = 3

    def __init__(self, view, orientation):
        super().__init__()
        self.view = view
        self.orientation = orientation
        self.hover = False
        self.dragging = False
        self.drag_offset = 0

        # CSS animation provider (smooth transitions)
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            * {
                transition: min-width 120ms ease, min-height 120ms ease;
            }
        """)
        self.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # size
        if orientation == Gtk.Orientation.VERTICAL:
            self.set_size_request(self.NORMAL_THICKNESS, -1)
            self.set_vexpand(True)
        else:
            self.set_size_request(-1, self.NORMAL_THICKNESS)
            self.set_hexpand(True)

        self.set_draw_func(self.on_draw)

        # hover
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.set_hover(True))
        motion.connect("leave", lambda *_: self.set_hover(False))
        self.add_controller(motion)

        # click
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        # drag
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    # ------------------------------------------------------------
    # Hover expansion
    # ------------------------------------------------------------

    def set_hover(self, val):
        if val == self.hover:
            return
        self.hover = val
        if self.orientation == Gtk.Orientation.VERTICAL:
            self.set_size_request(self.HOVER_THICKNESS if val else self.NORMAL_THICKNESS, -1)
        else:
            self.set_size_request(-1, self.HOVER_THICKNESS if val else self.NORMAL_THICKNESS)
        self.queue_draw()

    # ------------------------------------------------------------
    # Scrollbar thumb geometry helpers
    # ------------------------------------------------------------

    def _v_bounds(self):
        alloc = self.get_allocation()
        h = alloc.height
        v = self.view
        total = v.buf.total()
        vis = max(1, v.get_allocation().height // v.renderer.line_h)
        max_scroll = max(0, total - vis)
        if total <= 1 or max_scroll == 0:
            return 0, h
        thumb_h = max(20, h * (vis / total))
        y = (v.scroll_line / max_scroll) * (h - thumb_h)
        return y, thumb_h

    def _h_bounds(self):
        alloc = self.get_allocation()
        w = alloc.width
        v = self.view
        usable = v.get_allocation().width - v.renderer.ln_width
        vis_chars = max(1, usable // v.renderer.char_w)
        # estimate max based on longest visible row
        max_len = 0
        total = v.buf.total()
        start = v.scroll_line
        end = min(start + (v.get_allocation().height // v.renderer.line_h) + 1, total)
        for ln in range(start, end):
            max_len = max(max_len, len(v.buf.get_line(ln)))
        if max_len <= vis_chars:
            return 0, w  # full width = hidden thumb
        max_scroll = max_len - vis_chars
        thumb_w = max(30, w * (vis_chars / max_len))
        x = (v.scroll_x / v.renderer.char_w) / max_scroll * (w - thumb_w)
        return x, thumb_w

    # ------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------

    def on_draw(self, area, cr, w, h):
        cr.set_source_rgba(0.20, 0.20, 0.20, 0.35 if self.hover else 0.20)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        if self.orientation == Gtk.Orientation.VERTICAL:
            pos, size = self._v_bounds()
            x = 1
            r = self.RADIUS
            width = w - 2
            cr.set_source_rgba(0.8, 0.8, 0.8, 1.0 if self.hover else 0.7)
            cr.new_path()
            cr.rectangle(x, pos, width, size)
            cr.fill()
        else:
            pos, size = self._h_bounds()
            y = 1
            r = self.RADIUS
            height = h - 2
            cr.set_source_rgba(0.8, 0.8, 0.8, 1.0 if self.hover else 0.7)
            cr.new_path()
            cr.rectangle(pos, y, size, height)
            cr.fill()

    # ------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------

    def on_click(self, g, n, x, y):
        if self.orientation == Gtk.Orientation.VERTICAL:
            pos, size = self._v_bounds()
            v = self.view
            vis = max(1, v.get_allocation().height // v.renderer.line_h)
            total = v.buf.total()
            max_scroll = max(0, total - vis)
            if not (pos <= y <= pos + size):
                if y < pos:
                    v.scroll_line = max(0, v.scroll_line - vis)
                else:
                    v.scroll_line = min(max_scroll, v.scroll_line + vis)
                v.queue_draw()
                self.queue_draw()
        else:
            pos, size = self._h_bounds()
            v = self.view
            usable = v.get_allocation().width - v.renderer.ln_width
            vis_chars = max(1, usable // v.renderer.char_w)
            if not (pos <= x <= pos + size):
                if x < pos:
                    v.scroll_x = max(0, v.scroll_x - vis_chars * v.renderer.char_w)
                else:
                    v.scroll_x += vis_chars * v.renderer.char_w
                v.queue_draw()
                self.queue_draw()

    def on_drag_begin(self, g, x, y):
        self.dragging = True
        if self.orientation == Gtk.Orientation.VERTICAL:
            pos, size = self._v_bounds()
            if pos <= y <= pos + size:
                self.drag_offset = y - pos
        else:
            pos, size = self._h_bounds()
            if pos <= x <= pos + size:
                self.drag_offset = x - pos

    def on_drag(self, g, dx, dy):
        if not self.dragging:
            return
        v = self.view
        if self.orientation == Gtk.Orientation.VERTICAL:
            alloc = self.get_allocation()
            h = alloc.height
            pos, size = self._v_bounds()
            gesture_ok, sx, sy = g.get_start_point()
            new_y = sy + dy - self.drag_offset
            new_y = max(0, min(new_y, h - size))
            total = v.buf.total()
            vis = max(1, v.get_allocation().height // v.renderer.line_h)
            max_scroll = max(0, total - vis)
            if max_scroll > 0:
                frac = new_y / (h - size)
                v.scroll_line = int(frac * max_scroll)
            v.queue_draw()
            self.queue_draw()
        else:
            alloc = self.get_allocation()
            w = alloc.width
            pos, size = self._h_bounds()
            gesture_ok, sx, sy = g.get_start_point()
            new_x = sx + dx - self.drag_offset
            new_x = max(0, min(new_x, w - size))
            usable = v.get_allocation().width - v.renderer.ln_width
            vis_chars = max(1, usable // v.renderer.char_w)
            # estimate largest visible row
            max_len = 0
            total = v.buf.total()
            start = v.scroll_line
            end = min(start + (v.get_allocation().height // v.renderer.line_h) + 1, total)
            for ln in range(start, end):
                max_len = max(max_len, len(v.buf.get_line(ln)))
            if max_len > vis_chars:
                max_scroll = max_len - vis_chars
                frac = new_x / (w - size)
                v.scroll_x = int(frac * max_scroll) * v.renderer.char_w
            v.queue_draw()
            self.queue_draw()

    def on_drag_end(self, *args):
        self.dragging = False
        self.queue_draw()

# ============================================================
#   END OF PART 2
# ============================================================
# ============================================================
#   EDITOR WINDOW + SCROLLBAR INTEGRATION
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("UltraEditor v6 — mmap + overlay + hover scrollbars")
        self.set_default_size(1100, 750)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)

        # Scrollbars
        self.vscroll = HoverScrollbar(self.view, Gtk.Orientation.VERTICAL)
        self.hscroll = HoverScrollbar(self.view, Gtk.Orientation.HORIZONTAL)

        # Horizontal scrollbar auto-hide state
        self.h_visible = False

        # Layout
        mainbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # view + vertical scrollbar
        hbox.append(self.view)
        hbox.append(self.vscroll)

        # add vertical stack
        mainbox.append(hbox)
        mainbox.append(self.hscroll)     # horizontal scrollbar at bottom

        # Hook resize for dynamic scrollbar visibility
        self.view.connect("resize", self.on_view_resize)
        self.buf.connect("changed", self.on_buffer_changed)

        # Header toolbar
        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        layout.set_content(mainbox)

        # Initially hide horizontal scrollbar
        self.hscroll.set_visible(False)

    # ------------------------------------------------------------
    # Hscrollbar visibility logic
    # ------------------------------------------------------------

    def update_hscroll_visibility(self):
        """Show horizontal scrollbar only if lines exceed viewport width."""
        alloc_view = self.view.get_allocation()
        text_space = alloc_view.width - self.view.renderer.ln_width
        if text_space < 50:
            # window too small; hide to avoid flicker
            self.hscroll.set_visible(False)
            return

        visible_chars = max(1, text_space // self.view.renderer.char_w)

        max_len = 0
        total = self.buf.total()
        start = self.view.scroll_line
        end = min(start + (alloc_view.height // self.view.renderer.line_h) + 3, total)
        for ln in range(start, end):
            max_len = max(max_len, len(self.buf.get_line(ln)))

        needed = max_len > visible_chars

        if needed != self.h_visible:
            self.h_visible = needed
            self.hscroll.set_visible(needed)

    # Called on any buffer change (typing, deletion, insertion)
    def on_buffer_changed(self, *_):
        self.update_hscroll_visibility()

    # Called on any resize
    def on_view_resize(self, *_):
        self.vscroll.queue_draw()
        self.hscroll.queue_draw()
        self.update_hscroll_visibility()

    # ------------------------------------------------------------
    # File open
    # ------------------------------------------------------------

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except Exception:
                return

            path = f.get_path()
            idx = IndexedFile(path)
            self.buf.load_indexed(idx)

            self.view.scroll_line = 0
            self.view.scroll_x = 0

            self.view.queue_draw()
            self.vscroll.queue_draw()
            self.hscroll.queue_draw()
            self.update_hscroll_visibility()

            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)


# ============================================================
#   APPLICATION
# ============================================================

class UltraEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v6")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


# ============================================================
#   MAIN
# ============================================================

if __name__ == "__main__":
    UltraEditor().run(sys.argv)

