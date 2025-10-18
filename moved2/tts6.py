#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TTS App")
        self.set_default_size(1000, 700)

        split_view = Adw.OverlaySplitView(
            sidebar_width_fraction=0.25,
            show_sidebar=True
        )

        # Sidebar
        sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6
        )
        sidebar.set_margin_top(12)
        sidebar.set_margin_bottom(12)
        sidebar.set_margin_start(12)
        sidebar.set_margin_end(12)

        # Menu button
        menu_btn = Gtk.Button(label="Menu")
        sidebar.append(menu_btn)

        # Voice list
        for voice in ["English", "Hindi", "Spanish"]:
            sidebar.append(Gtk.Label(label=voice, xalign=0))

        split_view.set_sidebar(sidebar)

        # Main content
        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12
        )
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)

        # Title bar in content
        titlebar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6
        )
        titlebar.set_halign(Gtk.Align.FILL)

        title_label = Gtk.Label(label="TTS Editor", xalign=0)
        titlebar.append(title_label)

        close_btn = Gtk.Button(label="Close")
        titlebar.append(close_btn)

        vbox.append(titlebar)

        # Text area
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        vbox.append(self.textview)

        # Play button
        play_button = Gtk.Button(label="Play")
        vbox.append(play_button)

        split_view.set_content(vbox)
        self.set_content(split_view)

class TTSApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.TTSApp",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TTSWindow(self)
        win.present()

if __name__ == "__main__":
    app = TTSApp()
    app.run()
