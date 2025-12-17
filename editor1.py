#!/usr/bin/env python3
import gi, cairo
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Pango, PangoCairo

FONT = "Monospace 11"
LINE_PADDING = 2
SCROLL_STEP = 40


class TextBuffer:
    def __init__(self, text):
        self.lines = text.splitlines() or [""]

    def line_count(self):
        return len(self.lines)

    def get_line(self, i):
        return self.lines[i]

    def insert_char(self, line, col, ch):
        s = self.lines[line]
        self.lines[line] = s[:col] + ch + s[col:]


class LayoutCache:
    def __init__(self):
        self.cache = {}

    def get(self, cr, text, width):
        key = (text, width)
        if key in self.cache:
            return self.cache[key]

        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)
        layout.set_width(width * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_font_description(Pango.FontDescription(FONT))

        self.cache[key] = layout
        return layout


class Editor(Gtk.DrawingArea):
    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer
        self.cache = LayoutCache()

        self.scroll_y = 0.0
        self.cursor_line = 0
        self.cursor_col = 0

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.set_draw_func(self.on_draw)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self.on_scroll)
        self.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def estimate_line_height(self, cr):
        layout = self.cache.get(cr, "Mg", 100)
        _, h = layout.get_pixel_size()
        return h + LINE_PADDING

    def total_height(self, cr, width):
        h = 0
        for i in range(self.buffer.line_count()):
            layout = self.cache.get(cr, self.buffer.get_line(i), width)
            _, lh = layout.get_pixel_size()
            h += lh + LINE_PADDING
        return h

    def on_scroll(self, controller, dx, dy):
        # GTK scroll direction is inverted relative to content
        self.scroll_y -= dy * SCROLL_STEP
        self.scroll_y = max(0, self.scroll_y)
        self.queue_draw()

    def on_key(self, controller, keyval, keycode, state):
        if keyval == 65362:  # Up
            self.cursor_line = max(0, self.cursor_line - 1)
        elif keyval == 65364:  # Down
            self.cursor_line = min(
                self.buffer.line_count() - 1,
                self.cursor_line + 1
            )
        elif 32 <= keyval <= 126:
            ch = chr(keyval)
            self.buffer.insert_char(
                self.cursor_line,
                self.cursor_col,
                ch
            )
            self.cursor_col += 1

        self.queue_draw()
        return True

    def on_draw(self, area, cr, width, height):
        cr.set_source_rgb(0.12, 0.12, 0.12)
        cr.paint()

        y = -self.scroll_y
        line_height = self.estimate_line_height(cr)

        first = max(0, int(self.scroll_y // line_height))
        visible = int(height // line_height) + 3

        for i in range(first, min(first + visible, self.buffer.line_count())):
            text = self.buffer.get_line(i)
            layout = self.cache.get(cr, text, width - 10)

            cr.move_to(5, y)
            cr.set_source_rgb(0.9, 0.9, 0.9)
            PangoCairo.show_layout(cr, layout)

            # Cursor
            if i == self.cursor_line:
                cursor_x = layout.index_to_pos(
                    self.cursor_col * 1
                ).x / Pango.SCALE
                _, lh = layout.get_pixel_size()

                cr.set_source_rgb(0.3, 0.7, 1.0)
                cr.rectangle(5 + cursor_x, y, 2, lh)
                cr.fill()

            _, lh = layout.get_pixel_size()
            y += lh + LINE_PADDING


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual GTK4 Editor")
        self.set_default_size(1200, 800)

        text = "\n".join(f"Line {i}" for i in range(100_000))
        buffer = TextBuffer(text)

        self.editor = Editor(buffer)
        self.set_content(self.editor)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.virtual.editor")

    def do_activate(self):
        win = Window(self)
        win.present()


if __name__ == "__main__":
    App().run()
