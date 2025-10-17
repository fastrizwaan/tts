#!/usr/bin/env python3
import gi, os
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio

class Win(Gtk.Window):
    def __init__(self):
        super().__init__(title="Icon test", default_width=200, default_height=80)
        icon_name = "show-library-symbolic"
        gicon = Gio.ThemedIcon.new(icon_name)

        img = Gtk.Image.new_from_gicon(gicon)
        img.set_pixel_size(24)  # explicit size, since Gtk.IconSize.* removed

        self.library_btn = Gtk.Button()
        self.library_btn.set_child(img)
        self.set_child(self.library_btn)

if __name__ == "__main__":
    win = Win()
    win.present()
    Gtk.main()

