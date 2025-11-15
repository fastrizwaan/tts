#!/usr/bin/env python3
import sys, os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk, Adw, Gio, Pango, PangoCairo, GObject
# ==========================================================
# TEXT BUFFER (DATA MODEL)
# ==========================================================

class VirtualTextBuffer(GObject.GObject):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()
        self.lines = [""]
        self.cursor_line = 0
        self.cursor_col = 0

    # ------------------------------------------------------
    # Cursor helpers
    # ------------------------------------------------------
    def set_cursor(self, line, col):
        line = max(0, min(line, len(self.lines)-1))
        col = max(0, min(col, len(self.lines[line])))
        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)

    # ------------------------------------------------------
    # Editing operations
    # ------------------------------------------------------
    def insert_text(self, text):
        line = self.lines[self.cursor_line]
        before = line[:self.cursor_col]
        after = line[self.cursor_col:]
        self.lines[self.cursor_line] = before + text + after
        self.cursor_col += len(text)
        self.emit("changed")

    def insert_newline(self):
        line = self.lines[self.cursor_line]
        before = line[:self.cursor_col]
        after = line[self.cursor_col:]
        self.lines[self.cursor_line] = before
        self.lines.insert(self.cursor_line+1, after)
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
                cur = self.lines[self.cursor_line]
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

    # ------------------------------------------------------
    # Loading text
    # ------------------------------------------------------
    def load_lines(self, lines):
        self.lines = lines if lines else [""]
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")
# ==========================================================
# INPUT CONTROLLER (KEYBOARD + MOUSE)
# ==========================================================

class InputController:
    def __init__(self, view, buffer):
        self.view = view
        self.buf = buffer

        self.selection_start = None
        self.selection_end = None

    # ------------------------------------------------------
    # Mouse
    # ------------------------------------------------------
    def on_button_press(self, line, col, event):
        self.buf.set_cursor(line, col)
        self.selection_start = (line, col)
        self.selection_end = (line, col)

    def on_mouse_drag(self, line, col):
        self.selection_end = (line, col)

    def on_button_release(self):
        pass

    # ------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------
    def insert_text(self, ch):
        self.buf.insert_text(ch)

    def insert_newline(self):
        self.buf.insert_newline()

    def backspace(self):
        self.buf.backspace()

    def delete_key(self):
        self.buf.delete()

    def move_left(self, shift):
        line = self.buf.cursor_line
        col = self.buf.cursor_col - 1
        if col < 0:
            if line > 0:
                line -= 1
                col = len(self.buf.lines[line])
        self.buf.set_cursor(line, col)

    def move_right(self, shift):
        line = self.buf.cursor_line
        col = self.buf.cursor_col + 1
        if col > len(self.buf.lines[line]):
            if line < len(self.buf.lines)-1:
                line += 1
                col = 0
            else:
                col = len(self.buf.lines[line])
        self.buf.set_cursor(line, col)

    def move_up(self, shift):
        l = max(0, self.buf.cursor_line - 1)
        c = min(self.buf.cursor_col, len(self.buf.lines[l]))
        self.buf.set_cursor(l, c)

    def move_down(self, shift):
        l = min(len(self.buf.lines)-1, self.buf.cursor_line + 1)
        c = min(self.buf.cursor_col, len(self.buf.lines[l]))
        self.buf.set_cursor(l, c)
# ==========================================================
# RENDERER (CAIRO + PANGO)
# ==========================================================

class VirtualTextRenderer:
    def __init__(self):
        self.font = Pango.FontDescription("monospace 14")
        self.line_height = 20
        self.char_width = 10
        self.bg = (0.1, 0.1, 0.1)
        self.fg = (0.9, 0.9, 0.9)
        self.text = (0.92, 0.92, 0.92)
        
        #self._init_theme()
    
    def _init_theme(self):
        style = Adw.StyleManager.get_default()
        dark = style.get_dark()

        if dark:
            self.bg = (0.18, 0.20, 0.21)
            self.text = (0.92, 0.92, 0.92)
            self.current_line_bg = (0.25, 0.27, 0.28)
            self.gutter_bg = (0.15, 0.17, 0.18)
            self.gutter_text = (0.55, 0.55, 0.55)
            self.selection_bg = (0.25, 0.35, 0.55)
            self.search_bg = (0.80, 0.80, 0.45)
            self.search_current_bg = (0.95, 0.95, 0.20)
            self.marked_bg = (0.50, 0.50, 0.65)
            self.bracket_bg = (0.20, 0.40, 0.20)
        else:
            self.bg = (1.0, 1.0, 1.0)
            self.text = (0.0, 0.0, 0.0)
            self.current_line_bg = (0.95, 0.95, 0.85)
            self.gutter_bg = (0.95, 0.95, 0.95)
            self.gutter_text = (0.40, 0.40, 0.40)
            self.selection_bg = (0.70, 0.80, 1.0)
            self.search_bg = (1.0, 1.0, 0.75)
            self.search_current_bg = (1.0, 0.9, 0.2)
            self.marked_bg = (0.85, 0.85, 1.0)
            self.bracket_bg = (0.80, 1.00, 0.80)

        # Syntax colors (theme-aware)
        self.syntax_colors = {
            'keywords': (0.75, 0.20, 0.75),
            'builtins': (0.00, 0.50, 0.50),
            'string': (0.00, 0.60, 0.00),
            'comment': (0.50, 0.50, 0.50),
            'decorator': (0.85, 0.45, 0.00),
            'number': (0.10, 0.10, 0.75),
            'function': (0.10, 0.10, 0.75),
            'class': (0.00, 0.30, 0.60),
            'types': (0.00, 0.50, 0.50),
            'tag': (0.10, 0.10, 0.75),
            'attribute': (0.60, 0.00, 0.60),
            'property': (0.60, 0.00, 0.60),
            'selector': (0.00, 0.30, 0.60),
            'color': (0.00, 0.60, 0.00),
            'entity': (0.80, 0.45, 0.00),
            'macro': (0.80, 0.45, 0.00),
            'personal': (0.60, 0.90, 1.0),
        }

        # React to theme changes
        style.connect("notify::dark", lambda *a: self._init_theme())
    def draw(self, cr, allocation, buffer, scroll_line, scroll_x, selection_start, selection_end):
        # Background
        cr.set_source_rgb(*self.bg)
        cr.paint()

        # Setup Pango
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        # Draw text lines
        lines = buffer.lines
        y = 0
        start = scroll_line
        max_lines = allocation.height // self.line_height

        for i in range(start, min(start + max_lines, len(lines))):
            text = lines[i]
            layout.set_text(text)
            cr.set_source_rgb(*self.fg)
            cr.move_to(-scroll_x, y)
            PangoCairo.show_layout(cr, layout)
            y += self.line_height

        # Draw cursor
        cur_line = buffer.cursor_line
        cur_col = buffer.cursor_col
        if start <= cur_line < start + max_lines:
            cx = cur_col * self.char_width - scroll_x
            cy = (cur_line - start) * self.line_height
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_height)
            cr.fill()
# ==========================================================
# VIEW (Gtk.DrawingArea)
# ==========================================================

class VirtualTextView(Gtk.DrawingArea):
    def __init__(self, buffer):
        super().__init__()

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.buffer = buffer
        self.buffer.connect("changed", lambda *_: self.queue_draw())

        self.renderer = VirtualTextRenderer()
        self.input = InputController(self, buffer)

        self.scroll_line = 0
        self.scroll_x = 0

        self.set_draw_func(self.on_draw)

        self._install_pointer()
        self._install_keys()
        self._install_scroll()

    # ------------------------------------------------------
    # Event controllers
    # ------------------------------------------------------
    def _install_pointer(self):
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)

    def _install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    def _install_scroll(self):
        flags = Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        scroll = Gtk.EventControllerScroll.new(flags)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    # ------------------------------------------------------
    # Drawing
    # ------------------------------------------------------
    def on_draw(self, area, cr, width, height):
        alloc = area.get_allocation()
        self.renderer.draw(
            cr,
            alloc,
            self.buffer,
            self.scroll_line,
            self.scroll_x,
            self.input.selection_start,
            self.input.selection_end
        )

    # ------------------------------------------------------
    # Mouse
    # ------------------------------------------------------
    def _coords_to_line_col(self, x, y):
        line = self.scroll_line + int(y // self.renderer.line_height)
        col = int((x + self.scroll_x) // self.renderer.char_width)
        line = max(0, min(line, len(self.buffer.lines)-1))
        col = max(0, min(col, len(self.buffer.lines[line])))
        return line, col

    def _on_click(self, gesture, n_press, x, y):
        self.grab_focus()
        line, col = self._coords_to_line_col(x, y)
        self.input.on_button_press(line, col, None)
        self.queue_draw()

    def _on_drag(self, gesture, dx, dy):
        x, y = gesture.get_start_point()
        x += dx
        y += dy
        line, col = self._coords_to_line_col(x, y)
        self.input.on_mouse_drag(line, col)
        self.queue_draw()

    # ------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------
    def _on_key(self, controller, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        alt  = state & Gdk.ModifierType.ALT_MASK

        if name == "Return":
            self.input.insert_newline()
            return True
        if name == "BackSpace":
            self.input.backspace()
            return True
        if name == "Delete":
            self.input.delete_key()
            return True

        if name == "Left":
            self.input.move_left(False)
            return True
        if name == "Right":
            self.input.move_right(False)
            return True
        if name == "Up":
            self.input.move_up(False)
            return True
        if name == "Down":
            self.input.move_down(False)
            return True

        if not (ctrl or alt):
            ch = Gdk.keyval_to_unicode(keyval)
            if ch > 0:
                self.input.insert_text(chr(ch))
                return True

        return False

    # ------------------------------------------------------
    # Scroll
    # ------------------------------------------------------
    def _on_scroll(self, controller, dx, dy):
        if dy != 0:
            self.scroll_line += int(dy)
            self.scroll_line = max(0, min(self.scroll_line, len(self.buffer.lines)-1))
        if dx != 0:
            self.scroll_x = max(0, self.scroll_x + int(dx * 30))
        self.queue_draw()
        return True
# ==========================================================
# APPLICATION WINDOW
# ==========================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        self.set_title("Minimal Editor")

        buf = VirtualTextBuffer()
        self.view = VirtualTextView(buf)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.view)

        self.set_content(box)
# ==========================================================
# APPLICATION ENTRY POINT
# ==========================================================

class EditorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.MinimalEditor")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    app = EditorApplication()
    app.run(sys.argv)

