#!/usr/bin/env python3
import sys, os, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk, GLib, Adw, Gio, GObject, Pango, PangoCairo
# ----------------------------------------------------------
# These are only *hooks* so your original classes work
# You will replace these with your real classes later.
# ----------------------------------------------------------

class SyntaxPatterns:
    pass

class UndoAction:
    pass

class UndoManager:
    pass

class SearchDialog(Gtk.Window):
    pass

class SettingsDialog(Gtk.Window):
    pass

# Optional engines — all empty, but fully hookable
class SyntaxEngine:
    def tokenize(self, line_number, text):
        return None  # None → no syntax applied

class SearchEngine:
    pass

class FoldingEngine:
    def is_visible(self, lines, i):
        return True
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

        self.undo = UndoManager()   # hook only

    def load_lines(self, lines):
        self.lines = lines if lines else [""]
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    # --------------------------
    # Cursor
    # --------------------------
    def set_cursor(self, line, col):
        line = max(0, min(line, len(self.lines)-1))
        col  = max(0, min(col, len(self.lines[line])))
        self.cursor_line = line
        self.cursor_col  = col
        self.emit("cursor-moved", line, col)

    # --------------------------
    # Editing
    # --------------------------
    def insert_text(self, text):
        line = self.lines[self.cursor_line]
        before = line[:self.cursor_col]
        after  = line[self.cursor_col:]
        self.lines[self.cursor_line] = before + text + after
        self.cursor_col += len(text)
        self.emit("changed")

    def insert_newline(self):
        line = self.lines[self.cursor_line]
        left = line[:self.cursor_col]
        right = line[self.cursor_col:]
        self.lines[self.cursor_line] = left
        self.lines.insert(self.cursor_line+1, right)
        self.cursor_line += 1
        self.cursor_col = 0
        self.emit("changed")

    def backspace(self):
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
        line = self.lines[self.cursor_line]
        if self.cursor_col < len(line):
            self.lines[self.cursor_line] = line[:self.cursor_col] + line[self.cursor_col+1:]
        else:
            if self.cursor_line < len(self.lines)-1:
                self.lines[self.cursor_line] += self.lines[self.cursor_line+1]
                del self.lines[self.cursor_line+1]
        self.emit("changed")
class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf  = buf
        self.selection_start = None
        self.selection_end   = None

    def on_button_press(self, line, col, event):
        self.buf.set_cursor(line, col)
        self.selection_start = (line, col)
        self.selection_end   = (line, col)

    def on_mouse_drag(self, line, col):
        self.selection_end = (line, col)

    # Key ops
    def insert_text(self, ch): self.buf.insert_text(ch)
    def insert_newline(self):  self.buf.insert_newline()
    def backspace(self):       self.buf.backspace()
    def delete_key(self):      self.buf.delete()

    def move_left(self):
        l = self.buf.cursor_line
        c = self.buf.cursor_col - 1
        if c < 0:
            if l > 0: l, c = l-1, len(self.buf.lines[l-1])
        self.buf.set_cursor(l, c)

    def move_right(self):
        l = self.buf.cursor_line
        c = self.buf.cursor_col + 1
        if c > len(self.buf.lines[l]):
            if l < len(self.buf.lines)-1:
                l, c = l+1, 0
        self.buf.set_cursor(l, c)

    def move_up(self):
        l = max(0, self.buf.cursor_line - 1)
        c = min(self.buf.cursor_col, len(self.buf.lines[l]))
        self.buf.set_cursor(l, c)

    def move_down(self):
        l = min(len(self.buf.lines)-1, self.buf.cursor_line + 1)
        c = min(self.buf.cursor_col, len(self.buf.lines[l]))
        self.buf.set_cursor(l, c)
class VirtualTextRenderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 14")
        self.line_height = 22
        self.char_width  = 10
        self.bg = (0.11, 0.11, 0.11)
        self.fg = (0.92, 0.92, 0.92)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sstart, send, syntax_engine=None):

        # Background
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        lines = buf.lines
        start = scroll_line
        max_vis = max(1, alloc.height // self.line_height)
        y = 0

        for ln in range(start, min(start+max_vis, len(lines))):
            text = lines[ln]

            # --------------------------
            # SYNTAX HOOK
            # --------------------------
            if syntax_engine:
                tokens = syntax_engine.tokenize(ln, text)
            else:
                tokens = None

            # No syntax engine → draw whole line
            if not tokens:
                layout.set_text(text)
                cr.set_source_rgb(*self.fg)
                cr.move_to(-scroll_x, y)
                PangoCairo.show_layout(cr, layout)
                y += self.line_height
                continue

            # Syntax mode: draw tokens
            for (start_col, end_col, color) in tokens:
                segment = text[start_col:end_col]
                layout.set_text(segment)
                cr.set_source_rgb(*color)
                cr.move_to(start_col * self.char_width - scroll_x, y)
                PangoCairo.show_layout(cr, layout)

            y += self.line_height

        # Draw cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if start <= cl < start+max_vis:
            cx = cc * self.char_width - scroll_x
            cy = (cl-start) * self.line_height
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_height)
            cr.fill()
class InputEvents:
    """High-level event dispatcher between GTK events and InputController."""

    def __init__(self, view, controller):
        self.view = view              # VirtualTextView
        self.ctrl = controller         # InputController

    # ------------------------------------------------------
    # COORDINATES
    # ------------------------------------------------------
    def to_line_col(self, x, y):
        r = self.view.renderer
        buf = self.view.buf

        line = self.view.scroll_line + int(y // r.line_height)
        col  = int((x + self.view.scroll_x) // r.char_width)

        line = max(0, min(line, len(buf.lines)-1))
        col  = max(0, min(col, len(buf.lines[line])))

        return line, col

    # ------------------------------------------------------
    # CLICK
    # ------------------------------------------------------
    def on_click(self, x, y):
        line, col = self.to_line_col(x, y)
        self.ctrl.on_button_press(line, col, None)

    # ------------------------------------------------------
    # DRAG
    # ------------------------------------------------------
    def on_drag(self, start_x, start_y, dx, dy):
        x = start_x + dx
        y = start_y + dy
        line, col = self.to_line_col(x, y)
        self.ctrl.on_mouse_drag(line, col)

    # ------------------------------------------------------
    # KEY
    # ------------------------------------------------------
    def on_key(self, name, unicode_char, ctrl, alt):
        c = self.ctrl

        # arrows & special keys
        if name == "Left":  c.move_left();      return True
        if name == "Right": c.move_right();     return True
        if name == "Up":    c.move_up();        return True
        if name == "Down":  c.move_down();      return True
        if name == "Return": c.insert_newline(); return True
        if name == "BackSpace": c.backspace();   return True
        if name == "Delete":    c.delete_key();  return True

        # text input
        if not ctrl and not alt and unicode_char:
            c.insert_text(unicode_char)
            return True

        return False

class VirtualTextView(Gtk.DrawingArea):
    def __init__(self, buf):
        super().__init__()

        self.buf = buf
        self.buf.connect("changed", lambda *_: self.queue_draw())
        self._resize_pending = False
        
        # Plugins / hooks
        self.syntax_engine = None
        self.search_engine = None
        self.folding_engine = None

        # Core logic classes
        self.input = InputController(self, buf)
        self.renderer = VirtualTextRenderer()   # <--- THIS WAS MISSING
        self.events = InputEvents(self, self.input)

        # Scroll state
        self.scroll_line = 0
        self.scroll_x = 0

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)


        self._install_mouse()
        self._install_keys()
        self._install_scroll()

    def schedule_content_resize(self):
        if hasattr(self, "_resize_pending") and self._resize_pending:
            return  # already scheduled

        self._resize_pending = True

        def apply_resize():
            self._resize_pending = False
            height = max(200, len(self.buf.lines) * self.renderer.line_height)
            self.set_content_height(height)
            self.set_content_width(2000)
            return False  # run once

        GLib.idle_add(apply_resize)

    def _update_content_size(self):
        height = max(200, len(self.buf.lines) * self.renderer.line_height)
        self.set_content_height(height)
        self.set_content_width(2000)  # enough for horizontal scrolling

    # ------------------------------------------------------
    # Plugin hooks
    # ------------------------------------------------------
    def set_syntax_engine(self, engine):
        self.syntax_engine = engine
        self.queue_draw()

    def set_search_engine(self, engine):
        self.search_engine = engine

    def set_folding_engine(self, engine):
        self.folding_engine = engine
        self.queue_draw()

    # ------------------------------------------------------
    # Drawing
    # ------------------------------------------------------
    def on_draw(self, area, cr, w, h):
        alloc = area.get_allocation()
        self.renderer.draw(
            cr, alloc, self.buf,
            self.scroll_line, self.scroll_x,
            self.input.selection_start,
            self.input.selection_end,
            syntax_engine=self.syntax_engine
        )

    # ------------------------------------------------------
    # MOUSE
    # ------------------------------------------------------
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


    # ------------------------------------------------------
    # KEYBOARD
    # ------------------------------------------------------
    def _install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    def _on_key(self, controller, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        alt  = state & Gdk.ModifierType.ALT_MASK

        char = None
        u = Gdk.keyval_to_unicode(keyval)
        if u > 0:
            char = chr(u)

        handled = self.events.on_key(name, char, ctrl, alt)
        if handled:
            self.queue_draw()
        return handled

    # ------------------------------------------------------
    # SCROLL
    # ------------------------------------------------------
    def _install_scroll(self):
        flags = Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        scroll = Gtk.EventControllerScroll.new(flags)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def _on_scroll(self, controller, dx, dy):
        if dy != 0:
            self.scroll_line += int(dy)
            self.scroll_line = max(0, min(self.scroll_line, len(self.buf.lines)-1))

        if dx != 0:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True


class InputController:
    def __init__(self, view, buf):
        self.view = view        # VirtualTextView
        self.buf  = buf         # VirtualTextBuffer

        # Selection (line, col) or None
        self.selection_start = None
        self.selection_end   = None

    # ------------------------------------------------------
    # MOUSE
    # ------------------------------------------------------
    def on_button_press(self, line, col, event):
        """Start a new selection and move cursor."""
        self.buf.set_cursor(line, col)
        self.selection_start = (line, col)
        self.selection_end   = (line, col)

    def on_mouse_drag(self, line, col):
        """Extend selection."""
        self.selection_end = (line, col)

    # ------------------------------------------------------
    # BASIC EDITING
    # ------------------------------------------------------
    def insert_text(self, ch):
        self.buf.insert_text(ch)

    def insert_newline(self):
        self.buf.insert_newline()

    def backspace(self):
        self.buf.backspace()

    def delete_key(self):
        self.buf.delete()

    # ------------------------------------------------------
    # CURSOR MOVEMENT
    # ------------------------------------------------------
    def move_left(self):
        line = self.buf.cursor_line
        col  = self.buf.cursor_col - 1

        if col < 0:
            if line > 0:
                line -= 1
                col = len(self.buf.lines[line])
            else:
                col = 0

        self.buf.set_cursor(line, col)

    def move_right(self):
        line = self.buf.cursor_line
        col  = self.buf.cursor_col + 1

        if col > len(self.buf.lines[line]):
            if line < len(self.buf.lines) - 1:
                line += 1
                col = 0
            else:
                col = len(self.buf.lines[line])

        self.buf.set_cursor(line, col)

    def move_up(self):
        line = self.buf.cursor_line
        col  = self.buf.cursor_col

        if line > 0:
            line -= 1
            col = min(col, len(self.buf.lines[line]))

        self.buf.set_cursor(line, col)

    def move_down(self):
        line = self.buf.cursor_line
        col  = self.buf.cursor_col

        if line < len(self.buf.lines) - 1:
            line += 1
            col = min(col, len(self.buf.lines[line]))

        self.buf.set_cursor(line, col)

class TextEditorWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        self.set_title("Hooked Minimal Editor")

        buf = VirtualTextBuffer()
        self.view = VirtualTextView(buf)

        # --------------------------
        # Header bar with Open button
        # --------------------------
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        btn_open = Gtk.Button(label="Open")
        btn_open.connect("clicked", lambda *_: self.open_file())
        header.pack_start(btn_open)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.view)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        self.set_child(box)


    def open_file(self):
        dialog = Gtk.FileDialog()

        def on_response(dialog, result):
            try:
                file = dialog.open_finish(result)
            except Exception:
                return

            path = file.get_path()
            if not path:
                return

            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except Exception as e:
                print("Error reading file:", e)
                return

            self.view.buf.load_lines(lines)
            self.view.schedule_content_resize()
            self.view.queue_draw()
            self.set_title(os.path.basename(path))

        dialog.open(self, None, on_response)

class TextEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.HookedMinimalEditor")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TextEditorWindow(self)
        win.present()

if __name__ == "__main__":
    app = TextEditorApp()
    app.run(sys.argv)
