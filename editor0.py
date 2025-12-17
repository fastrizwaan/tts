#!/usr/bin/env python3
import gi, math, cairo
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, Pango, PangoCairo

LINE_PADDING = 2
FONT = "Monospace 11"

class TextBuffer:
    def __init__(self, text):
        self.lines = text.splitlines() or [""]

    def line_count(self):
        return len(self.lines)

    def get_line(self, i):
        return self.lines[i]


class LayoutCache:
    def __init__(self):
        self.cache = {}

    def get(self, cr, text, width):
        key = (text, width)
        layout = self.cache.get(key)
        if layout:
            return layout

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

        self.set_draw_func(self.on_draw)
        self.set_focusable(True)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self.on_scroll)
        self.add_controller(scroll)

    def on_scroll(self, controller, dx, dy):
        self.scroll_y += dy * 40
        self.scroll_y = max(0, self.scroll_y)
        self.queue_draw()

    def on_draw(self, area, cr, width, height):
        cr.set_source_rgb(0.12, 0.12, 0.12)
        cr.paint()

        y = -self.scroll_y
        line_height = self.estimate_line_height(cr)

        first = max(0, int(self.scroll_y // line_height))
        visible = int(height // line_height) + 3

        for i in range(first, min(first + visible, self.buffer.line_count())):
            line = self.buffer.get_line(i)
            layout = self.cache.get(cr, line, width - 10)

            cr.move_to(5, y)
            cr.set_source_rgb(0.9, 0.9, 0.9)
            PangoCairo.show_layout(cr, layout)

            _, h = layout.get_pixel_size()
            y += h + LINE_PADDING

    def estimate_line_height(self, cr):
        layout = self.cache.get(cr, "Mg", 100)
        _, h = layout.get_pixel_size()
        return h + LINE_PADDING


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
        self.win = Window(self)
        self.win.present()


if __name__ == "__main__":
    app = App()
    app.run()

