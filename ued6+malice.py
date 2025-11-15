#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo


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
    """mmap + UTF16-safe single-pass indexing"""

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
        e = self.index[line+1]
        raw = self.mm[s:e]
        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")


# ============================================================
#   HYBRID EDIT OVERLAY
# ============================================================

class EditOverlay:
    """Stores edits logically without mutating mmap file."""

    def __init__(self):
        # inserted text at (line → list of (col, text))
        self.inserts = {}       # { line: [(col, text), ...] }

        # deleted ranges: ((s_line, s_col), (e_line, e_col))
        self.deletes = []

    def add_insert(self, line, col, text):
        self.inserts.setdefault(line, []).append((col, text))

    def add_delete(self, s, e):
        self.deletes.append((s, e))

    def clear(self):
        self.inserts.clear()
        self.deletes.clear()


# ============================================================
#   VIRTUAL BUFFER (MERGED VIEW)
# ============================================================

class VirtualBuffer(GObject.Object):

    __gsignals__ = {
        "changed":      (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()
        self.indexed = None
        self.overlay = EditOverlay()
        self.cursor_line = 0
        self.cursor_col = 0

    # --------------------------------------------------------
    # LOADING
    # --------------------------------------------------------

    def load_indexed(self, indexed):
        self.indexed = indexed
        self.overlay.clear()
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    # --------------------------------------------------------
    # LINES & MERGE LOGIC
    # --------------------------------------------------------

    def total(self):
        if self.indexed is None:
            return 1
        return self.indexed.total_lines()

    def get_line(self, ln):
        if self.indexed is None:
            return ""
        base = self.indexed.get_line_raw(ln)

        # Apply deletions
        for (s_l, s_c), (e_l, e_c) in self.overlay.deletes:
            if s_l == e_l == ln:
                base = base[:s_c] + base[e_c:]
            elif s_l == ln:
                base = base[:s_c]
            elif e_l == ln:
                base = base[e_c:]
            elif s_l < ln < e_l:
                base = ""

        # Apply inserts
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


    # --------------------------------------------------------
    # CURSOR
    # --------------------------------------------------------

    def set_cursor(self, line, col):
        line = max(0, min(line, self.total()-1))
        row = self.get_line(line)
        col = max(0, min(col, len(row)))
        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)

    # --------------------------------------------------------
    # EDIT ACTIONS
    # --------------------------------------------------------

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
            self.overlay.add_delete((l, c-1), (l, c))
            self.cursor_col -= 1
        else:
            if l > 0:
                prev = l - 1
                prev_len = len(self.get_line(prev))
                self.overlay.add_delete((prev, prev_len), (l, 0))
                self.cursor_line = prev
                self.cursor_col = prev_len
        self.emit("changed")

    def delete(self):
        l, c = self.cursor_line, self.cursor_col
        row = self.get_line(l)
        if c < len(row):
            self.overlay.add_delete((l, c), (l, c+1))
        elif l < self.total()-1:
            self.overlay.add_delete((l, len(row)), (l+1, 0))
        self.emit("changed")


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
            b.set_cursor(l, c-1)
        elif l > 0:
            prev = b.get_line(l-1)
            b.set_cursor(l-1, len(prev))

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
            b.set_cursor(l, c+1)
        else:
            if l+1 < b.total():
                b.set_cursor(l+1, 0)

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
#   RENDERER
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
        for ln in range(scroll_line, min(scroll_line+max_vis, total)):
            text = buf.get_line(ln)

            # selection background
            if sel_range:
                (sl, sc), (el, ec) = sel_range
                if sl <= ln <= el:
                    if sl == el:
                        xs = self.ln_width + sc*self.char_w - scroll_x
                        xe = self.ln_width + ec*self.char_w - scroll_x
                    elif ln == sl:
                        xs = self.ln_width + sc*self.char_w - scroll_x
                        xe = self.ln_width + (len(text)+1)*self.char_w - scroll_x
                    elif ln == el:
                        xs = self.ln_width - scroll_x
                        xe = self.ln_width + ec*self.char_w - scroll_x
                    else:
                        xs = self.ln_width - scroll_x
                        xe = self.ln_width + max(1, len(text))*self.char_w - scroll_x

                    cr.set_source_rgb(*self.sel_bg)
                    cr.rectangle(xs, y, xe-xs, self.line_h)
                    cr.fill()

            # line number
            layout.set_text(str(ln+1))
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
            cx = self.ln_width + cc*self.char_w - scroll_x
            cy = (cl - scroll_line)*self.line_h
            cr.set_source_rgb(1,1,1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()


# ============================================================
#   ULTRA VIEW
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
        alloc = area.get_allocation()
        self.renderer.draw(cr, alloc, self.buf,
                           self.scroll_line, self.scroll_x,
                           self.ctrl.sel_start, self.ctrl.sel_end)

    # --------------------------------------------------------
    # MOUSE
    # --------------------------------------------------------

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
        col = int((sx + dx - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(ln))))

        self.ctrl.drag(ln, col)
        self.buf.set_cursor(ln, col)
        self.queue_draw()

    def on_drag_end(self, g, x, y):
        self.ctrl.selecting = False

    # --------------------------------------------------------
    # KEYBOARD
    # --------------------------------------------------------

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    def on_key(self, c, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        if name in ("Return", "KP_Enter"):
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            self.buf.insert_newline()

        elif name == "BackSpace":
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            else:
                self.buf.backspace()

        elif name == "Delete":
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            else:
                self.buf.delete()

        elif name == "Left":
            self.ctrl.move_left(shift)
        elif name == "Right":
            self.ctrl.move_right(shift)
        elif name == "Up":
            self.ctrl.move_up(shift)
        elif name == "Down":
            self.ctrl.move_down(shift)

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

    def keep_cursor_visible(self):
        max_vis = self.get_allocated_height() // self.renderer.line_h
        cl = self.buf.cursor_line
        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

    # --------------------------------------------------------
    # SCROLL
    # --------------------------------------------------------

    def install_scroll(self):
        flags = (Gtk.EventControllerScrollFlags.VERTICAL |
                 Gtk.EventControllerScrollFlags.HORIZONTAL)
        sc = Gtk.EventControllerScroll.new(flags)
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        total = self.buf.total()
        vis = max(1, self.get_allocated_height() // self.renderer.line_h)
        max_scroll = total - vis

        if dy:
            self.scroll_line = max(0, min(self.scroll_line + int(dy*4), max_scroll))
        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx*40))

        self.queue_draw()
        return True


# ============================================================
#   SIMPLE VERTICAL SCROLLBAR
# ============================================================

class SimpleScrollbar(Gtk.DrawingArea):
    def __init__(self, view):
        super().__init__()
        self.view = view

        self.set_size_request(12, -1)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

    def on_draw(self, area, cr, w, h):
        cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        v = self.view
        total = v.buf.total()
        vis = max(1, v.get_allocated_height() // v.renderer.line_h)
        max_scroll = total - vis

        if total <= 1:
            cr.set_source_rgb(0.5, 0.5, 0.5)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            return

        thumb_h = max(20, h*(vis/total))
        pos = 0 if max_scroll == 0 else v.scroll_line/max_scroll
        y = pos*(h-thumb_h)

        cr.set_source_rgb(0.6, 0.6, 0.6)
        cr.rectangle(0, y, w, thumb_h)
        cr.fill()

    def on_click(self, g, n, x, y):
        v = self.view
        vis = max(1, v.get_allocated_height() // v.renderer.line_h)
        total = v.buf.total()
        max_scroll = total - vis

        thumb_h = max(20, self.get_allocated_height()*(vis/total))
        pos = y / max(1, self.get_allocated_height() - thumb_h)
        v.scroll_line = int(pos * max_scroll)

        v.queue_draw()
        self.queue_draw()


# ============================================================
#   WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("UltraEditor v5 — mmap + editable overlay")
        self.set_default_size(1000, 700)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)
        self.scrollbar = SimpleScrollbar(self.view)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(self.view)
        box.append(self.scrollbar)

        layout.set_content(box)

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return

            path = f.get_path()
            idx = IndexedFile(path)
            self.buf.load_indexed(idx)

            self.view.scroll_line = 0
            self.view.scroll_x = 0

            self.view.queue_draw()
            self.scrollbar.queue_draw()

            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)


# ============================================================
#   APP
# ============================================================

class UltraEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v5")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    UltraEditor().run(sys.argv)
