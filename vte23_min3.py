#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk, GLib, Adw, Gio, GObject, Pango, PangoCairo
# ===========================================================
#   LAZY FILE READER FOR HUGE FILES
# ===========================================================

class LazyFileLines:
    """
    Ultra-fast lazy line accessor.
    Does NOT split file initially.
    Does NOT read entire file into memory.
    Lines loaded only when needed.
    """

    def __init__(self, path):
        self.path = path
        self.file = open(path, "r", encoding="utf-8", errors="replace")
        # mmap gives near-zero overhead random access
        self.mm = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)

        self.index = [0]         # byte offsets for each line start
        self._built = False      # True when full index is finished
        self.size = self.mm.size()

    def _build_index_until(self, target_line):
        if self._built:
            return

        mm = self.mm
        idx = self.index

        pos = idx[-1]
        mm.seek(pos)

        while len(idx) <= target_line:
            line = mm.readline()
            if not line:
                self._built = True
                break
            idx.append(mm.tell())

    def __getitem__(self, i):
        self._build_index_until(i)
        if i >= len(self.index) - 1:
            return ""
        mm = self.mm
        mm.seek(self.index[i])
        return mm.readline().decode("utf-8", errors="replace").rstrip("\n")

    def __len__(self):
        # never build full index during load
        if not self._built:
            # return a small fake number to GTK
            return 1000000   # 1M visible virtual lines
        return len(self.index) - 1

# ----------------------------------------------------------
# Hooks / stubs (your real classes replace these)
# ----------------------------------------------------------

class SyntaxPatterns: pass
class UndoAction: pass
class UndoManager: pass
class SearchDialog(Gtk.Window): pass
class SettingsDialog(Gtk.Window): pass

class SyntaxEngine:
    def tokenize(self, line_number, text):
        return None

class SearchEngine: pass

class FoldingEngine:
    def is_visible(self, lines, i):
        return True
# ===========================================================
#   VirtualTextBuffer (works with LazyFileLines or list)
# ===========================================================

class VirtualTextBuffer(GObject.Object):

    __gsignals__ = {
        "changed":       (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved":  (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()
        self.lines = [""]
        self.cursor_line = 0
        self.cursor_col = 0
        self.undo = UndoManager()

    def load_lines(self, lines):
        """
        lines can be:
        - list[str]
        - LazyFileLines (recommended)
        """
        self.lines = lines
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def set_cursor(self, line, col):
        line = max(0, min(line, len(self.lines)-1))
        text = self.lines[line]
        col = max(0, min(col, len(text)))
        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)
    def insert_text(self, text):
        line = self.lines[self.cursor_line]
        before = line[:self.cursor_col]
        after  = line[self.cursor_col:]
        new = before + text + after

        # If it's LazyFileLines, convert that one line to real string
        if isinstance(self.lines, LazyFileLines):
            # convert LazyFileLines to full list for editing mode
            self.lines = list(self.lines)

        self.lines[self.cursor_line] = new
        self.cursor_col += len(text)
        self.emit("changed")
    def insert_newline(self):
        if isinstance(self.lines, LazyFileLines):
            self.lines = list(self.lines)

        line = self.lines[self.cursor_line]
        left = line[:self.cursor_col]
        right = line[self.cursor_col:]

        self.lines[self.cursor_line] = left
        self.lines.insert(self.cursor_line+1, right)

        self.cursor_line += 1
        self.cursor_col = 0
        self.emit("changed")
    def backspace(self):
        if isinstance(self.lines, LazyFileLines):
            self.lines = list(self.lines)

        if self.cursor_col > 0:
            line = self.lines[self.cursor_line]
            self.lines[self.cursor_line] = line[:self.cursor_col-1] + line[self.cursor_col:]
            self.cursor_col -= 1
        else:
            if self.cursor_line > 0:
                prev = self.lines[self.cursor_line-1]
                cur  = self.lines[self.cursor_line]
                self.cursor_col = len(prev)
                self.lines[self.cursor_line-1] = prev + cur
                del self.lines[self.cursor_line]
                self.cursor_line -= 1
        self.emit("changed")
    def delete(self):
        if isinstance(self.lines, LazyFileLines):
            self.lines = list(self.lines)

        line = self.lines[self.cursor_line]
        if self.cursor_col < len(line):
            self.lines[self.cursor_line] = line[:self.cursor_col] + line[self.cursor_col+1:]
        else:
            if self.cursor_line < len(self.lines)-1:
                self.lines[self.cursor_line] += self.lines[self.cursor_line+1]
                del self.lines[self.cursor_line+1]
        self.emit("changed")
# ===========================================================
#   InputController
# ===========================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf  = buf
        self.selection_start = None
        self.selection_end = None

    def on_button_press(self, line, col, event):
        self.buf.set_cursor(line, col)
        self.selection_start = (line, col)
        self.selection_end   = (line, col)

    def on_mouse_drag(self, line, col):
        self.selection_end = (line, col)

    def insert_text(self, ch): self.buf.insert_text(ch)
    def insert_newline(self):  self.buf.insert_newline()
    def backspace(self):       self.buf.backspace()
    def delete_key(self):      self.buf.delete()

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
# ===========================================================
#   InputEvents: dispatch GTK events → InputController
# ===========================================================

class InputEvents:
    def __init__(self, view, controller):
        self.view = view
        self.ctrl = controller

    def to_line_col(self, x, y):
        r = self.view.renderer
        buf = self.view.buf
        line = self.view.scroll_line + int(y // r.line_height)
        col = int((x + self.view.scroll_x) // r.char_width)
        line = max(0, min(line, len(buf.lines)-1))
        col = max(0, min(col, len(buf.lines[line])))
        return line, col

    def on_click(self, x, y):
        line, col = self.to_line_col(x, y)
        self.ctrl.on_button_press(line, col, None)

    def on_drag(self, sx, sy, dx, dy):
        x = sx + dx
        y = sy + dy
        line, col = self.to_line_col(x, y)
        self.ctrl.on_mouse_drag(line, col)

    def on_key(self, name, uni, ctrl, alt):
        c = self.ctrl
        if name == "Left": c.move_left(); return True
        if name == "Right": c.move_right(); return True
        if name == "Up": c.move_up(); return True
        if name == "Down": c.move_down(); return True
        if name == "Return": c.insert_newline(); return True
        if name == "BackSpace": c.backspace(); return True
        if name == "Delete": c.delete_key(); return True
        if not ctrl and not alt and uni:
            c.insert_text(uni)
            return True
        return False
# ===========================================================
#   Renderer (syntax-hooked)
# ===========================================================

class VirtualTextRenderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_height = 21
        self.char_width  = 9
        self.bg = (0.11, 0.11, 0.11)
        self.fg = (0.92, 0.92, 0.92)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x,
             sel_start, sel_end, syntax_engine=None):

        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        lines = buf.lines
        max_vis = max(1, alloc.height // self.line_height)
        y = 0

        for ln in range(scroll_line,
                        min(scroll_line + max_vis, len(lines))):

            text = lines[ln]

            tokens = syntax_engine.tokenize(ln, text) if syntax_engine else None

            if not tokens:
                layout.set_text(text)
                cr.set_source_rgb(*self.fg)
                cr.move_to(-scroll_x, y)
                PangoCairo.show_layout(cr, layout)
            else:
                for (s, e, color) in tokens:
                    layout.set_text(text[s:e])
                    cr.set_source_rgb(*color)
                    cr.move_to(s * self.char_width - scroll_x, y)
                    PangoCairo.show_layout(cr, layout)

            y += self.line_height

        # cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cx = cc * self.char_width - scroll_x
            cy = (cl - scroll_line) * self.line_height
            cr.set_source_rgb(1,1,1)
            cr.rectangle(cx, cy, 2, self.line_height)
            cr.fill()
# ===========================================================
#   VirtualTextView (optimized + lazy-height)
# ===========================================================

class VirtualTextView(Gtk.DrawingArea):
    def __init__(self, buf):
        super().__init__()

        self.buf = buf
        self.buf.connect("changed", lambda *_: self.queue_draw())

        self.syntax_engine = None
        self.search_engine = None
        self.folding_engine = None

        self.input = InputController(self, buf)
        self.renderer = VirtualTextRenderer()
        self.events = InputEvents(self, self.input)

        self.scroll_line = 0
        self.scroll_x = 0

        self._resize_pending = False

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        self._install_mouse()
        self._install_keys()
        self._install_scroll()
    # Schedule height update (safe for huge files)
    def schedule_content_resize(self):
        """
        Freeze-proof virtual height calculation for huge files.
        Never compute real line count. Never force LazyFileLines indexing.
        """
        if self._resize_pending:
            return

        self._resize_pending = True

        def apply():
            self._resize_pending = False

            # ------------------------------------------------------
            # Virtual height: always a fixed large number.
            # Never use len(self.buf.lines) here — it freezes lazy loaders.
            # ------------------------------------------------------
            VIRTUAL_LINES = 1_000_000            # editor behaves like 1M-line file
            line_h = self.renderer.line_height
            height = VIRTUAL_LINES * line_h

            # Give widget a virtual height
            self.set_content_height(height)

            # Safe big width for horizontal scroll
            self.set_content_width(4000)

            return False  # run once

        GLib.idle_add(apply)


        GLib.idle_add(apply)
    def on_draw(self, area, cr, w, h):
        alloc = area.get_allocation()
        self.renderer.draw(
            cr, alloc, self.buf,
            self.scroll_line, self.scroll_x,
            self.input.selection_start,
            self.input.selection_end,
            syntax_engine=self.syntax_engine
        )
    def _install_mouse(self):
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)

    def _on_click(self, gesture, n_press, x, y):
        self.grab_focus()
        self.events.on_click(x, y)
        self.queue_draw()

    def _on_drag(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        self.events.on_drag(sx, sy, dx, dy)
        self.queue_draw()
    def _install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    def _on_key(self, controller, keyval, keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        alt  = state & Gdk.ModifierType.ALT_MASK
        name = Gdk.keyval_name(keyval)

        uni = None
        u = Gdk.keyval_to_unicode(keyval)
        if u > 0:
            uni = chr(u)

        handled = self.events.on_key(name, uni, ctrl, alt)
        if handled:
            self.queue_draw()
        return handled
    def _install_scroll(self):
        flags = Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        scroll = Gtk.EventControllerScroll.new(flags)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def _on_scroll(self, controller, dx, dy):
        """
        Smooth virtual scrolling + incremental lazy indexing.
        Never forces full file indexing.
        """

        # Vertical scroll
        if dy:
            # Scroll 3 lines per wheel tick (like many editors)
            self.scroll_line += int(dy * 3)

            # Clamp scroll_line to our virtual model
            # Use the same virtual count as schedule_content_resize()
            VIRTUAL_LINES = 1_000_000
            self.scroll_line = max(0, min(self.scroll_line, VIRTUAL_LINES - 1))

            # If using lazy lines, pre-index ahead of where user is scrolling
            if isinstance(self.buf.lines, LazyFileLines):
                # Build index up to 2000 lines beyond current viewport
                target = self.scroll_line + 2000
                self.buf.lines._build_index_until(target)

        # Horizontal scroll
        if dx:
            # 40px per wheel tick feels right at 13px monospace
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True   # stop event, we handled it

# ===========================================================
#   Window (Open file → LazyFileLines)
# ===========================================================

class TextEditorWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        self.set_title("Huge File Editor")

        buf = VirtualTextBuffer()
        self.view = VirtualTextView(buf)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        btn_open = Gtk.Button(label="Open")
        btn_open.connect("clicked", lambda *_: self.open_file())
        header.pack_start(btn_open)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        scroller.set_child(self.view)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        self.set_child(box)

    def open_file(self):
        dialog = Gtk.FileDialog()

        def on_done(dialog, result):
            try:
                file = dialog.open_finish(result)
            except:
                return

            path = file.get_path()
            if not path:
                return

            lazy = LazyFileLines(path)

            self.view.buf.load_lines(lazy)
            self.view.schedule_content_resize()
            self.view.queue_draw()
            self.set_title(os.path.basename(path))

        dialog.open(self, None, on_done)
class TextEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.HugeLazyEditor")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TextEditorWindow(self)
        win.present()

if __name__ == "__main__":
    app = TextEditorApp()
    app.run(sys.argv)
