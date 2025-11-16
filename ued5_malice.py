#!/usr/bin/env python3
import sys, os, mmap, gi, cairo

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
#   FULL INDEXING BUT MEMORY-SAFE
# ============================================================

class IndexedFile:
    """
    Fully indexes file once.
    Memory-safe: only stores offsets, not decoded lines.
    Works for UTF-8 and UTF-16 (LE/BE).
    """

    def __init__(self, path):
        self.path = path
        self.encoding = detect_encoding(path)
        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)

        self.index = []
        self.index_file()

    def index_file(self):
        enc = self.encoding

        if enc.startswith("utf-16"):
            self._index_utf16()
        else:
            self._index_utf8()

    def _index_utf8(self):
        mm = self.mm
        mm.seek(0)
        self.index = [0]

        while True:
            pos = mm.tell()
            line = mm.readline()
            if not line:
                break
            self.index.append(mm.tell())

    def _index_utf16(self):
        raw = self.mm[:]
        text = raw.decode(self.encoding, errors="replace")

        self.index = [0]
        byte_offset = 0
        encoder = self.encoding

        for ch in text:
            ch_bytes = ch.encode(encoder, errors="replace")
            byte_offset += len(ch_bytes)
            if ch == "\n":
                self.index.append(byte_offset)

        if not self.index or self.index[-1] != len(raw):
            self.index.append(len(raw))



    def total_lines(self):
        return len(self.index) - 1

    def __getitem__(self, line):
        if line < 0 or line >= self.total_lines():
            return ""

        start = self.index[line]
        end = self.index[line + 1]

        raw = self.mm[start:end]
        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")


# ============================================================
#   BUFFER
# ============================================================

class VirtualBuffer(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self):
        super().__init__()
        self.file = None            # IndexedFile
        self.edits = {}             # sparse: line_number → modified string
        self.cursor_line = 0
        self.cursor_col = 0

    def load(self, indexed_file):
        self.file = indexed_file
        self.edits.clear()
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def total(self):
        """Return total number of logical lines in the buffer.

        If a file is loaded, base it on file length and any edited lines.
        If no file is loaded, base it on edited lines (or at least 1).
        """
        if not self.file:
            # When editing an empty/new buffer, consider edits so added
            if not self.edits:
                return 1
            return max(1, max(self.edits.keys()) + 1)

        # File is present
        if not self.edits:
            return self.file.total_lines()

        max_edited = max(self.edits.keys())
        return max(self.file.total_lines(), max_edited + 1)


    def get_line(self, ln):
        if ln in self.edits:
            return self.edits[ln]
        if self.file:
            return self.file[ln] if 0 <= ln < self.file.total_lines() else ""
        return ""



    def set_cursor(self, ln, col):
        total = self.total()
        ln = max(0, min(ln, total - 1))
        line = self.get_line(ln)
        col = max(0, min(col, len(line)))
        self.cursor_line = ln
        self.cursor_col = col

    # ------- Editing ----------
    def insert_text(self, text):
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        new_line = line[:col] + text + line[col:]
        self.edits[ln] = new_line

        self.cursor_col = col + len(text)
        self.emit("changed")

    def backspace(self):
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        if col == 0:
            return

        new_line = line[:col-1] + line[col:]
        self.edits[ln] = new_line

        self.cursor_col = col - 1
        self.emit("changed")

    def insert_newline(self):
        ln = self.cursor_line
        col = self.cursor_col

        old_line = self.get_line(ln)
        left = old_line[:col]
        right = old_line[col:]

        # Put the left part into edits (replaces or creates this line)
        self.edits[ln] = left

        # Shift ONLY edited lines that come AFTER ln
        shifted = {}
        for k, v in self.edits.items():
            if k > ln:
                shifted[k + 1] = v
            else:
                shifted[k] = v

        # Insert new blank line or right side of old line
        shifted[ln + 1] = right

        self.edits = shifted

        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.emit("changed")




# ============================================================
#   INPUT
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf
        self.sel_start = None
        self.sel_end = None

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.sel_start = (ln, col)
        self.sel_end = (ln, col)

    def drag(self, ln, col):
        self.sel_end = (ln, col)

    def move_left(self):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        if col > 0:
            b.set_cursor(ln, col - 1)
        elif ln > 0:
            prev = b.get_line(ln - 1)
            b.set_cursor(ln - 1, len(prev))

    def move_right(self):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        if col < len(line):
            b.set_cursor(ln, col + 1)
        elif ln + 1 < b.total():
            b.set_cursor(ln + 1, 0)

    def move_up(self):
        b = self.buf
        ln = b.cursor_line
        if ln > 0:
            target = ln - 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)))

    def move_down(self):
        b = self.buf
        ln = b.cursor_line
        if ln + 1 < b.total():
            target = ln + 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)))


# ============================================================
#   RENDERER
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 22
        self.ln_width = 70

        # Clear semantic names
        self.editor_background_color = (0.10, 0.10, 0.10)
        self.text_foreground_color   = (0.50, 0.50, 0.50)
        self.linenumber_foreground_color = (0.60, 0.60, 0.60)

    def get_text_width(self, cr, text):
        """Calculate actual pixel width of text using Pango"""
        if not text:
            return 0
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_text(text, -1)
        width, _ = layout.get_pixel_size()
        return width

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        # Background
        cr.set_source_rgb(*self.editor_background_color)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        total = buf.total()
        max_vis = (alloc.height // self.line_h) + 1

        y = 0
        for ln in range(scroll_line, min(scroll_line + max_vis, total)):
            text = buf.get_line(ln)

            # Line number
            layout.set_text(str(ln + 1), -1)
            cr.set_source_rgb(*self.linenumber_foreground_color)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # Line text
            layout.set_text(text, -1)
            cr.set_source_rgb(*self.text_foreground_color)
            cr.move_to(self.ln_width - scroll_x, y)
            PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # Cursor - calculate actual text width
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cy = (cl - scroll_line) * self.line_h
            
            # Get text before cursor and measure it
            line_text = buf.get_line(cl)
            text_before_cursor = line_text[:cc]
            text_width = self.get_text_width(cr, text_before_cursor)
            
            cx = self.ln_width + text_width - scroll_x
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()

# ============================================================
#   VIEW
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
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_draw_func(self.draw_view)

        self.install_mouse()
        self.install_keys()
        self.install_scroll()

        # Setup IM context with preedit support
        self.im = Gtk.IMMulticontext()
        self.im.connect("commit", self.on_commit)
        self.im.connect("preedit-changed", self.on_preedit_changed)
        self.im.connect("preedit-start", self.on_preedit_start)
        self.im.connect("preedit-end", self.on_preedit_end)
        
        # Preedit state
        self.preedit_string = ""
        self.preedit_cursor = 0
        
        # Connect focus events
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self.on_focus_in)
        focus.connect("leave", self.on_focus_out)
        self.add_controller(focus)

    def on_commit(self, im, text):
        """Handle committed text from IM"""
        if text:
            self.buf.insert_text(text)
            self.keep_cursor_visible()
            self.queue_draw()
            # Update IM cursor AFTER buffer changes
            self.update_im_cursor_location()

    def on_preedit_start(self, im):
        """Preedit (composition) started"""
        self.queue_draw()

    def on_preedit_end(self, im):
        """Preedit (composition) ended"""
        self.preedit_string = ""
        self.preedit_cursor = 0
        self.queue_draw()

    def on_preedit_changed(self, im):
        """Preedit text changed - show composition"""
        try:
            preedit_str, attrs, cursor_pos = self.im.get_preedit_string()
            self.preedit_string = preedit_str or ""
            self.preedit_cursor = cursor_pos
            self.queue_draw()
        except Exception as e:
            print(f"Preedit error: {e}")

    def on_focus_in(self, controller):
        """Widget gained focus"""
        self.im.focus_in()
        self.im.set_client_widget(self)
        self.update_im_cursor_location()
        
    def on_focus_out(self, controller):
        """Widget lost focus"""
        self.im.focus_out()

    def update_im_cursor_location(self):
        """Tell IM where to display composition window"""
        try:
            # Get actual allocation
            alloc = self.get_allocation()
            if alloc.width <= 0 or alloc.height <= 0:
                return
                
            cl, cc = self.buf.cursor_line, self.buf.cursor_col
            
            # Get the actual line text up to cursor
            line_text = self.buf.get_line(cl)
            text_before_cursor = line_text[:cc]
            
            # Measure actual text width using Pango
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.renderer.font)
            layout.set_text(text_before_cursor, -1)
            
            # Get actual pixel width
            text_width, _ = layout.get_pixel_size()
            
            # Calculate screen position
            y = (cl - self.scroll_line) * self.renderer.line_h
            x = self.renderer.ln_width + text_width - self.scroll_x
            
            # Clamp to visible area
            x = max(self.renderer.ln_width, min(x, alloc.width - 50))
            y = max(0, min(y, alloc.height - self.renderer.line_h))
            
            # Create cursor rectangle
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 2
            rect.height = self.renderer.line_h
            
            self.im.set_cursor_location(rect)
        except Exception as e:
            print(f"IM cursor location error: {e}")

    def on_key(self, c, keyval, keycode, state):
        # Let IM filter the event FIRST
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True

        name = Gdk.keyval_name(keyval)

        # Editing keys
        if name == "BackSpace":
            self.buf.backspace()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Return":
            self.buf.insert_newline()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Arrow keys
        if name == "Up":
            self.ctrl.move_up()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Down":
            self.ctrl.move_down()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Left":
            self.ctrl.move_left()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Right":
            self.ctrl.move_right()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        return False

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        key.connect("key-released", self.on_key_release)
        self.add_controller(key)
        
    def on_key_release(self, c, keyval, keycode, state):
        """Filter key releases for IM"""
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True
        return False


    def install_mouse(self):
        g = Gtk.GestureClick()
        g.connect("pressed", self.on_click)
        self.add_controller(g)

        d = Gtk.GestureDrag()
        d.connect("drag-update", self.on_drag)
        self.add_controller(d)

    def on_click(self, g, n, x, y):
        self.grab_focus()
        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col = int((x - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        line = self.buf.get_line(ln)
        col = max(0, min(col, len(line)))

        self.ctrl.click(ln, col)
        self.queue_draw()

    def on_drag(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        ln = self.scroll_line + int((sy + dy) // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col = int((sx + dx - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        line = self.buf.get_line(ln)
        col = max(0, min(col, len(line)))

        self.ctrl.drag(ln, col)
        self.queue_draw()

    def keep_cursor_visible(self):
        max_vis = max(1, (self.get_height() // self.renderer.line_h) + 1)

        cl = self.buf.cursor_line
        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

        if self.scroll_line < 0:
            self.scroll_line = 0


        self.scroll_line = max(0, self.scroll_line)

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        key.connect("key-released", lambda *_: False)

        self.add_controller(key)
        
    def install_scroll(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        total = self.buf.total()
        max_vis = max(1, (self.get_allocated_height() // self.renderer.line_h) + 1)
        max_scroll = max(0, total - max_vis)

        if dy:
            self.scroll_line = max(
                0,
                min(self.scroll_line + int(dy * 4), max_scroll)
            )

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True


    def draw_view(self, area, cr, w, h):
        cr.set_source_rgb(0.10, 0.10, 0.10)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        alloc = type("Alloc", (), {"width": w, "height": h})

        self.renderer.draw(
            cr,
            alloc,
            self.buf,
            self.scroll_line,
            self.scroll_x,
            self.ctrl.sel_start,
            self.ctrl.sel_end
        )



# ============================================================
#   SCROLLBAR (simple)
# ============================================================

class VirtualScrollbar(Gtk.DrawingArea):
    def __init__(self, view):
        super().__init__()
        self.view = view

        self.set_size_request(14, -1)
        self.set_vexpand(True)
        self.set_hexpand(False)

        self.set_draw_func(self.draw_scrollbar)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self.on_drag)
        self.add_controller(drag)

        self.dragging = False

    def draw_scrollbar(self, area, cr, w, h):
        cr.set_source_rgb(0.20, 0.20, 0.20)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        view = self.view
        total = view.buf.total()

        max_vis = max(1, (view.get_height() // view.renderer.line_h) + 1)
        max_scroll = max(0, total - max_vis)

        thumb_h = max(20, h * (max_vis / total))
        pos = 0 if max_scroll == 0 else (view.scroll_line / max_scroll)
        y = pos * (h - thumb_h)

        cr.set_source_rgb(0.55, 0.55, 0.55)
        cr.rectangle(0, y, w, thumb_h)
        cr.fill()


    def on_click(self, g, n_press, x, y):
        self.start_y = y
        self.dragging = True

    def on_drag(self, g, dx, dy):
        if not self.dragging:
            return

        view = self.view
        h = self.get_allocated_height()
        total = view.buf.total()
        max_vis = max(1, view.get_allocated_height() // view.renderer.line_h)
        max_scroll = max(0, total - max_vis)

        thumb_h = max(20, h * (max_vis / total))
        track = h - thumb_h
        frac = (self.start_y + dy) / track
        frac = max(0, min(1, frac))

        view.scroll_line = int(frac * max_scroll)
        view.queue_draw()
        self.queue_draw()


# ============================================================
#   WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("UltraEditor v4 — Full Indexing, UTF16-Safe")
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

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return
            path = f.get_path()

            idx = IndexedFile(path)
            self.buf.load(idx)
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
        super().__init__(application_id="dev.ultraeditor.v4")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    UltraEditor().run(sys.argv)
