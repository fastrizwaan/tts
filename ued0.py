#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GLib, Gio, GObject, Pango, PangoCairo

# ============================================================
#   FILE LOADING WITH ENCODING DETECTION
# ============================================================

def detect_encoding(path):
    with open(path, "rb") as f:
        start = f.read(4)

    # BOM signatures
    if start.startswith(b'\xff\xfe'):
        return "utf-16le"
    if start.startswith(b'\xfe\xff'):
        return "utf-16be"
    if start.startswith(b'\xef\xbb\xbf'):
        return "utf-8-sig"      # UTF-8 with BOM

    # fallback: try UTF-8
    return "utf-8"

# ============================================================
#   LAZY FILE ACCESSOR USING MMAP + INCREMENTAL LINE INDEXING
# ============================================================

class LazyLines:
    """
    mmap + incremental newline indexing.
    Essential for huge files.
    """

    def __init__(self, path):
        self.path = path
        enc = detect_encoding(path)

        # read file in selected encoding
        self.file = open(path, "r", encoding=enc, errors="replace")
        raw = open(path, "rb")
        self.mm = mmap.mmap(raw.fileno(), 0, access=mmap.ACCESS_READ)

        # incremental index of newline positions
        self.index = [0]
        self._built = False

        self.size = self.mm.size()

    def _build_until(self, target_line):
        if self._built:
            return

        mm = self.mm
        idx = self.index

        mm.seek(idx[-1])

        # Build until we reach target line or EOF
        while len(idx) <= target_line:
            chunk = mm.readline()
            if not chunk:
                self._built = True
                break
            idx.append(mm.tell())

    def __getitem__(self, i):
        self._build_until(i)
        if i >= len(self.index) - 1:
            return ""  # beyond EOF → blank
        mm = self.mm
        mm.seek(self.index[i])
        return mm.readline().decode("utf-8", errors="replace").rstrip("\n")

    def __len__(self):
        """
        IMPORTANT:
        Never return real length until needed.
        Return a virtual length so GTK never freezes.
        """
        if not self._built:
            return 1_000_000
        return len(self.index) - 1

# ============================================================
#   VIRTUAL TEXT BUFFER (supports LazyLines or list)
# ============================================================

class TextBuffer(GObject.Object):

    __gsignals__ = {
        "changed":      (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int,int)),
    }

    def __init__(self):
        super().__init__()
        self.lines = [""]
        self.cursor_line = 0
        self.cursor_col = 0

    def load(self, lines):
        self.lines = lines
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def set_cursor(self, line, col):
        line = max(0, min(line, len(self.lines)-1))
        col  = max(0, min(col, len(self.lines[line])))
        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)

# ============================================================
#   INPUT CONTROLLER
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf  = buf
        self.sel_start = None
        self.sel_end   = None

    def click(self, line, col):
        self.buf.set_cursor(line, col)
        self.sel_start = (line, col)
        self.sel_end   = (line, col)

    def drag(self, line, col):
        self.sel_end = (line, col)

    def move_left(self):
        l = self.buf.cursor_line
        c = self.buf.cursor_col - 1
        if c < 0 and l > 0:
            l -= 1
            c = len(self.buf.lines[l])
        self.buf.set_cursor(l, c)

    def move_right(self):
        l = self.buf.cursor_line
        c = self.buf.cursor_col + 1
        if c > len(self.buf.lines[l]):
            if l < len(self.buf.lines)-1:
                l += 1
                c = 0
        self.buf.set_cursor(l, c)

    def move_up(self):
        l = max(0, self.buf.cursor_line - 1)
        c = min(self.buf.cursor_col, len(self.buf.lines[l]))
        self.buf.set_cursor(l, c)

    def move_down(self):
        l = min(len(self.buf.lines)-1, self.buf.cursor_line + 1)
        c = min(self.buf.cursor_col, len(self.buf.lines[l]))
        self.buf.set_cursor(l, c)

# ============================================================
#   RENDERER — draws only visible lines
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 21
        self.char_w = 9
        self.bg = (0.12, 0.12, 0.12)
        self.fg = (0.90, 0.90, 0.90)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        max_vis = alloc.height // self.line_h
        lines = buf.lines
        y = 0

        for ln in range(scroll_line, scroll_line + max_vis):
            if ln >= len(lines):
                break
            text = lines[ln]

            layout.set_text(text)
            cr.set_source_rgb(*self.fg)
            cr.move_to(-scroll_x, y)
            PangoCairo.show_layout(cr, layout)
            y += self.line_h

        # cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cx = cc * self.char_w - scroll_x
            cy = (cl - scroll_line) * self.line_h
            cr.set_source_rgb(1,1,1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()

# ============================================================
#   VIRTUAL TEXT VIEW — lazy, virtual, fast
# ============================================================

class TextView(Gtk.DrawingArea):
    def __init__(self, buf):
        super().__init__()
        self.buf = buf
        self.buf.connect("changed", lambda *_: self.queue_draw())

        self.renderer = Renderer()
        self.ctrl = InputController(self, buf)

        self.scroll_line = 0
        self.scroll_x = 0

        self._resize_pending = False

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        self.install_mouse()
        self.install_keys()
        self.install_scroll()

    def schedule_resize(self):
        if self._resize_pending:
            return
        self._resize_pending = True

        def apply():
            self._resize_pending = False
            VIRTUAL_LINES = 1_000_000
            height = VIRTUAL_LINES * self.renderer.line_h
            self.set_content_height(height)
            self.set_content_width(4000)
            return False

        GLib.idle_add(apply)

    def on_draw(self, area, cr, w, h):
        alloc = area.get_allocation()
        self.renderer.draw(
            cr, alloc, self.buf,
            self.scroll_line, self.scroll_x,
            self.ctrl.sel_start, self.ctrl.sel_end
        )

    # ---------------- MOUSE ----------------

    def install_mouse(self):
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self.on_drag)
        self.add_controller(drag)

    def on_click(self, gesture, n, x, y):
        self.grab_focus()
        line = self.scroll_line + int(y // self.renderer.line_h)
        col  = int((x + self.scroll_x) // self.renderer.char_w)
        line = max(0, min(line, len(self.buf.lines)-1))
        col  = max(0, min(col, len(self.buf.lines[line])))
        self.ctrl.click(line, col)
        self.queue_draw()

    def on_drag(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok: return
        x = sx + dx
        y = sy + dy
        line = self.scroll_line + int(y // self.renderer.line_h)
        col  = int((x + self.scroll_x) // self.renderer.char_w)
        line = max(0, min(line, len(self.buf.lines)-1))
        col  = max(0, min(col, len(self.buf.lines[line])))
        self.ctrl.drag(line, col)
        self.queue_draw()

    # ---------------- KEYS ----------------

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    def on_key(self, ctrlr, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        c = self.ctrl

        if name == "Up": c.move_up()
        elif name == "Down": c.move_down()
        elif name == "Left": c.move_left()
        elif name == "Right": c.move_right()
        else:
            return False

        self.queue_draw()
        return True

    # ---------------- SCROLL ----------------

    def install_scroll(self):
        flags = Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        sc = Gtk.EventControllerScroll.new(flags)
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, ctrl, dx, dy):
        if dy:
            self.scroll_line += int(dy * 3)
            self.scroll_line = max(0, min(self.scroll_line, 1_000_000 - 1))

            # LazyLines: incremental indexing
            if isinstance(self.buf.lines, LazyLines):
                self.buf.lines._build_until(self.scroll_line + 2000)

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True

# ============================================================
#   WINDOW + FILE OPEN
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Ultra Editor")
        self.set_default_size(1000, 700)

        # Text buffer + view
        buf = TextBuffer()
        self.view = TextView(buf)

        # -----------------------------------------
        # Libadwaita requires Adw.ToolbarView
        # -----------------------------------------
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)   # NOT set_child, not set_title_widget

        # -----------------------------------------
        # Proper Libadwaita header bar
        # -----------------------------------------
        header = Adw.HeaderBar()

        btn_open = Gtk.Button(label="Open")
        btn_open.connect("clicked", self.open_file)
        header.pack_start(btn_open)

        # add headerbar to top bar of the toolbar view
        toolbar.add_top_bar(header)

        # -----------------------------------------
        # Scroller + TextView
        # -----------------------------------------
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        scroller.set_child(self.view)

        toolbar.set_content(scroller)



    def open_file(self, *a):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                file = dialog.open_finish(result)
            except:
                return

            path = file.get_path()
            if not path:
                return

            lazy = LazyLines(path)
            self.view.buf.load(lazy)
            self.view.schedule_resize()
            self.view.queue_draw()
            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)

# ============================================================
#   APPLICATION ENTRY POINT
# ============================================================

class UltraEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultra.editor")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    app = UltraEditorApp()
    app.run(sys.argv)
