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

    def click(self, line, col):
        self.buf.set_cursor(line, col)
        self.sel_start = (line, col)
        self.sel_end = (line, col)

    def drag(self, line, col):
        self.sel_end = (line, col)

    def move_left(self):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        if c > 0:
            b.set_cursor(l, c - 1)
        elif l > 0:
            prev = b.get_line(l - 1)
            b.set_cursor(l - 1, len(prev))

    def move_right(self):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        row = b.get_line(l)
        if c < len(row):
            b.set_cursor(l, c + 1)
        else:
            if l + 1 < b.known():
                b.set_cursor(l + 1, 0)

    def move_up(self):
        b = self.buf
        if b.cursor_line > 0:
            l = b.cursor_line - 1
            row = b.get_line(l)
            b.set_cursor(l, min(b.cursor_col, len(row)))

    def move_down(self):
        b = self.buf
        t = b.cursor_line + 1
        if t < b.known():
            row = b.get_line(t)
            b.set_cursor(t, min(b.cursor_col, len(row)))


# ============================================================
#   RENDERER (TEXT + LINE NUMBERS)
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 21
        self.char_w = 9

        self.bg = (0.10, 0.10, 0.10)
        self.fg = (0.90, 0.90, 0.90)
        self.ln_fg = (0.60, 0.60, 0.60)

        self.ln_width = 80  # line number column

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        max_vis = alloc.height // self.line_h
        total_lines = buf.total()
        y = 0

        for ln in range(scroll_line, min(scroll_line + max_vis, total_lines)):
            text = buf.get_line(ln)

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
#   CUSTOM VIRTUAL SCROLLBAR (VSCode style)
# ============================================================

class VirtualScrollbar(Gtk.DrawingArea):
    def __init__(self, text_view):
        super().__init__()

        self.view = text_view

        self.set_size_request(16, -1)
        self.set_hexpand(False)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        self.drag_active = False
        self.drag_offset = 0

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self.on_drag)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def on_draw(self, area, cr, w, h):
        v = self.view
        buf = v.buf

        cr.set_source_rgb(0.16, 0.16, 0.16)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        total = buf.total()
        if total < 1:
            return

        visible = max(1, v.get_allocated_height() // v.renderer.line_h)

        # thumb
        thumb_h = max(20, h * (visible / total))
        max_scroll = max(0, total - visible)
        
        if max_scroll == 0:
            y = 0
        else:
            cur = min(v.scroll_line, max_scroll)
            y = (cur / max_scroll) * (h - thumb_h)

        cr.set_source_rgb(0.50, 0.50, 0.50)
        cr.rectangle(0, y, w, thumb_h)
        cr.fill()

    def on_click(self, gesture, n, x, y):
        v = self.view
        h = self.get_allocated_height()

        buf = v.buf
        total = buf.total()

        visible = max(1, v.get_allocated_height() // v.renderer.line_h)

        thumb_h = max(20, h * (visible / total))
        max_scroll = max(0, total - visible)
        
        if max_scroll == 0:
            return
            
        thumb_y = (v.scroll_line / max_scroll) * (h - thumb_h)

        # drag
        if thumb_y <= y <= thumb_y + thumb_h:
            self.drag_active = True
            self.drag_offset = y - thumb_y
            return

        # page up/down
        if y < thumb_y:
            v.scroll_line = max(0, v.scroll_line - visible)
        else:
            v.scroll_line = min(max_scroll, v.scroll_line + visible)

        v.queue_draw()
        self.queue_draw()

    def on_drag(self, gesture, dx, dy):
        if not self.drag_active:
            return

        v = self.view
        h = self.get_allocated_height()
        visible = max(1, v.get_allocated_height() // v.renderer.line_h)

        buf = v.buf
        total = buf.total()
        max_scroll = max(0, total - visible)

        gesture_ok, sx, sy = gesture.get_start_point()
        thumb_h = max(20, h * (visible / total))

        new_y = sy + dy - self.drag_offset
        new_y = max(0, min(new_y, h - thumb_h))

        frac = new_y / (h - thumb_h) if h > thumb_h else 0
        v.scroll_line = int(frac * max_scroll)

        v.ensure_visible_indexed(v.scroll_line + visible)
        v.queue_draw()
        self.queue_draw()

    def on_drag_end(self, *args):
        self.drag_active = False


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
        drag.connect("drag-update", self.on_drag)
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
        self.queue_draw()

    # ---------------------------------------------------------
    # KEYBOARD
    # ---------------------------------------------------------
    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    def on_key(self, c, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)

        if name == "Left": self.ctrl.move_left()
        elif name == "Right": self.ctrl.move_right()
        elif name == "Up": self.ctrl.move_up()
        elif name == "Down": self.ctrl.move_down()
        else:
            return False

        self.ensure_visible_indexed(self.buf.cursor_line)
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
        self.set_title("UltraEditor v3.3 — UTF16 Fix + Display")
        self.set_default_size(1000, 700)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)
        self.scrollbar = VirtualScrollbar(self.view)

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
            self.scrollbar.queue_draw()

            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)


# ============================================================
#   APPLICATION
# ============================================================

class UltraEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v33")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    UltraEditorApp().run(sys.argv)