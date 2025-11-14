# virtual_view.py
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango, PangoCairo


class VirtualTextView(Gtk.DrawingArea):
    def __init__(self, buffer):
        super().__init__()
        self.buf = buffer

        self.font = Pango.FontDescription("Monospace 11")
        self.line_height = 18
        self.start_line = 0
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        self.add_controller(self._scroll_controller())
        self.connect("map", self.on_map)
        self.vadj = None

    def _scroll_controller(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        sc.connect("scroll", self._on_scroll)
        return sc

    def _on_scroll(self, ctrl, dx, dy):
        if self.vadj:
            self.vadj.set_value(self.vadj.get_value() + dy * 40)
        return True

    def on_map(self, *args):
        parent = self.get_parent()
        if isinstance(parent, Gtk.ScrolledWindow):
            self.vadj = parent.get_vadjustment()
            self.vadj.connect("value-changed", self._adj_changed)

    def _adj_changed(self, adj):
        new_start = int(adj.get_value() // self.line_height)
        if new_start != self.start_line:
            self.start_line = new_start
            self.queue_draw()

    def on_draw(self, area, cr, w, h):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        visible = h // self.line_height
        y = 0

        for i in range(visible):
            ln = self.start_line + i
            line = self.buf.get_line(ln)
            layout.set_text(f"{ln+1:6d}  {line}")
            cr.move_to(0, y)
            PangoCairo.show_layout(cr, layout)
            y += self.line_height

