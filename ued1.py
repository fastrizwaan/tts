#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GLib, Gio, GObject, Pango, PangoCairo
# ============================================================
#   ENCODING DETECTION
# ============================================================

def detect_encoding(path):
    with open(path, "rb") as f:
        start = f.read(4)

    if start.startswith(b"\xff\xfe"):
        return "utf-16le"
    if start.startswith(b"\xfe\xff"):
        return "utf-16be"
    if start.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"
# ============================================================
#   LAZY MMAP FILE WITH EXPANDING VIRTUAL SCROLL INDEX
# ============================================================

class LazyFile:
    """
    mmap-backed file with incremental newline index.
    Expands index only when user scrolls deeper.
    """

    def __init__(self, path):
        self.path = path
        self.encoding = detect_encoding(path)

        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)
        self.size = self.mm.size()

        # newline index (file offsets). Index[0] = 0 always.
        self.index = [0]

        self.eof_reached = False

    def _index_more(self, target_line, chunk=2000):
        """Index next chunk of newline positions until reaching target_line."""
        if self.eof_reached:
            return

        mm = self.mm

        # We start scanning from the last indexed position.
        pos = self.index[-1]
        mm.seek(pos)

        needed = target_line - (len(self.index) - 1)
        needed = max(needed, 0)

        limit = needed + chunk

        for _ in range(limit):
            line = mm.readline()
            if not line:
                self.eof_reached = True
                break
            self.index.append(mm.tell())

    def line_count_known(self):
        """How many lines are indexed so far."""
        return len(self.index) - 1

    def __getitem__(self, i):
        """Return decoded line i (empty beyond EOF)."""
        if i < 0:
            return ""

        # If needed, expand newline index to reach this line.
        if i >= self.line_count_known():
            self._index_more(i)

        if i >= self.line_count_known():
            return ""

        mm = self.mm
        mm.seek(self.index[i])
        raw = mm.readline()
        return raw.decode(self.encoding, errors="replace").rstrip("\n")
# ============================================================
#   TEXT BUFFER USING LAZYFILE OR LIST-OF-LINES
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
        
    def set_cursor(self, line, col):
        """
        Safe cursor movement respecting known indexed range.
        """
        # clamp line
        max_known = self.known_line_count() - 1
        if line < 0:
            line = 0
        elif line > max_known:
            # ask lazy file to index more
            if isinstance(self.lines, LazyFile):
                self.lines._index_more(line)
                max_known = self.known_line_count() - 1
                line = min(line, max_known)
            else:
                line = max_known

        # clamp column
        text = self.get_line(line)
        col = max(0, min(col, len(text)))

        self.cursor_line = line
        self.cursor_col  = col

        self.emit("cursor-moved", line, col)

    def load(self, lines):
        """
        lines = LazyFile OR list-of-str
        """
        self.lines = lines
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def known_line_count(self):
        lr = self.lines
        if isinstance(lr, LazyFile):
            return lr.line_count_known()
        return len(lr)

    def get_line(self, i):
        lr = self.lines
        if isinstance(lr, LazyFile):
            return lr[i]
        if 0 <= i < len(lr):
            return lr[i]
        return ""
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
        bl = self.buf
        l = bl.cursor_line
        c = bl.cursor_col - 1
        if c < 0:
            if l > 0:
                l -= 1
                c = len(bl.get_line(l))
            else:
                c = 0
        bl.set_cursor(l, c)

    def move_right(self):
        bl = self.buf
        l = bl.cursor_line
        c = bl.cursor_col + 1
        line = bl.get_line(l)
        if c > len(line):
            if l < bl.known_line_count() - 1:
                l += 1
                c = 0
            else:
                c = len(line)
        bl.set_cursor(l, c)

    def move_up(self):
        bl = self.buf
        l = max(0, bl.cursor_line - 1)
        line = bl.get_line(l)
        c = min(bl.cursor_col, len(line))
        bl.set_cursor(l, c)

    def move_down(self):
        bl = self.buf
        l = bl.cursor_line + 1
        if l >= bl.known_line_count():
            # Expand index so cursor can move down.
            if isinstance(bl.lines, LazyFile):
                bl.lines._index_more(l)
            if l >= bl.known_line_count():
                return
        line = bl.get_line(l)
        c = min(bl.cursor_col, len(line))
        bl.set_cursor(l, c)
# ============================================================
#   RENDERER
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 21
        self.char_w = 9
        self.bg = (0.11, 0.11, 0.11)
        self.fg = (0.92, 0.92, 0.92)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        max_vis = alloc.height // self.line_h
        y = 0

        for ln in range(scroll_line, scroll_line + max_vis):
            line = buf.get_line(ln)

            layout.set_text(line)
            cr.set_source_rgb(*self.fg)
            cr.move_to(-scroll_x, y)
            PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # Cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cx = cc * self.char_w - scroll_x
            cy = (cl - scroll_line) * self.line_h
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()
# ============================================================
#   ULTRA VIEW WITH EXPANDING VIRTUAL HEIGHT
# ============================================================

class UltraView(Gtk.DrawingArea):
    INITIAL_VIRTUAL_LINES = 50000

    def __init__(self, buf):
        super().__init__()

        self.buf = buf
        self.buf.connect("changed", lambda *_: self.queue_draw())

        self.renderer = Renderer()
        self.ctrl = InputController(self, buf)

        self.scroll_line = 0
        self.scroll_x = 0
        self.virtual_lines = UltraView.INITIAL_VIRTUAL_LINES
        self.resize_pending = False

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        self.install_mouse()
        self.install_keys()
        self.install_scroll()

        # initial height
        self.schedule_resize()
    def schedule_resize(self):
        if self.resize_pending:
            return
        self.resize_pending = True

        def apply():
            self.resize_pending = False
            h = self.virtual_lines * self.renderer.line_h
            self.set_content_height(h)
            self.set_content_width(4000)
            return False

        GLib.idle_add(apply)
    def expand_virtual_if_needed(self, target_line):
        """
        If target_line reaches the last virtual region,
        grow virtual_lines by chunks.
        """
        if target_line >= self.virtual_lines - 2000:
            self.virtual_lines += 30000
            self.schedule_resize()

        # If using LazyFile, index more real lines as needed
        lr = self.buf.lines
        if isinstance(lr, LazyFile):
            lr._index_more(target_line)
    # ---------------------------------------------------------
    # DRAW
    # ---------------------------------------------------------
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

    def on_click(self, gest, n, x, y):
        self.grab_focus()
        line = self.scroll_line + int(y // self.renderer.line_h)
        self.expand_virtual_if_needed(line)
        col = int((x + self.scroll_x) // self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(line))))
        self.ctrl.click(line, col)
        self.queue_draw()

    def on_drag(self, gest, dx, dy):
        ok, sx, sy = gest.get_start_point()
        if not ok:
            return
        x = sx + dx
        y = sy + dy
        line = self.scroll_line + int(y // self.renderer.line_h)
        self.expand_virtual_if_needed(line)
        col = int((x + self.scroll_x) // self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(line))))
        self.ctrl.drag(line, col)
        self.queue_draw()
    # ---------------------------------------------------------
    # KEYBOARD
    # ---------------------------------------------------------
    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    def on_key(self, ctrlr, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)

        if name == "Left":
            self.ctrl.move_left()
        elif name == "Right":
            self.ctrl.move_right()
        elif name == "Up":
            self.ctrl.move_up()
        elif name == "Down":
            self.ctrl.move_down()
        else:
            return False

        # ensure scroll expanded
        self.expand_virtual_if_needed(self.buf.cursor_line)

        # auto scroll view when cursor moves
        if self.buf.cursor_line < self.scroll_line:
            self.scroll_line = self.buf.cursor_line
        else:
            # bottom visible
            max_vis = self.get_allocated_height() // self.renderer.line_h
            if self.buf.cursor_line >= self.scroll_line + max_vis:
                self.scroll_line = self.buf.cursor_line - max_vis + 1

        self.queue_draw()
        return True
    # ---------------------------------------------------------
    # SCROLL
    # ---------------------------------------------------------
    def install_scroll(self):
        flags = Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        sc = Gtk.EventControllerScroll.new(flags)
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, ctrl, dx, dy):
        if dy:
            self.scroll_line += int(dy * 3)
            if self.scroll_line < 0:
                self.scroll_line = 0

            # ensure line exists
            self.expand_virtual_if_needed(self.scroll_line + 500)

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True
# ============================================================
#   EDITOR WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Ultra Editor (Model B)")
        self.set_default_size(1100, 700)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)

        # Layout: ToolbarView + HeaderBar
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()

        btn_open = Gtk.Button(label="Open")
        btn_open.connect("clicked", self.open_file)
        header.pack_start(btn_open)

        toolbar.add_top_bar(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        scroller.set_child(self.view)

        toolbar.set_content(scroller)


    def open_file(self, *args):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                file = dialog.open_finish(result)
            except:
                return

            path = file.get_path()
            if not path:
                return

            lf = LazyFile(path)
            self.buf.load(lf)
            self.view.scroll_line = 0
            self.view.virtual_lines = UltraView.INITIAL_VIRTUAL_LINES
            self.view.schedule_resize()
            self.view.queue_draw()
            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)
# ============================================================
#   APPLICATION ENTRY POINT
# ============================================================

class UltraEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultra.editor.b")

    def do_activate(self):
        if not self.props.active_window:
            win = EditorWindow(self)
            win.present()
        else:
            self.props.active_window.present()


if __name__ == "__main__":
    app = UltraEditorApp()
    app.run(sys.argv)
