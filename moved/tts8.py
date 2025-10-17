#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TTS App")
        self.set_default_size(1000, 700)

        # Split view (sidebar + content)
        split_view = Adw.OverlaySplitView(
            sidebar_width_fraction=0.25,
            show_sidebar=True
        )
        self.set_content(split_view)

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





        # HeaderBar ONLY in content
        headerbar = Adw.HeaderBar()
        headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))
        vbox.append(headerbar)

        # Text area
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        vbox.append(self.textview)

        # Play button
        play_button = Gtk.Button(label="Play")
        vbox.append(play_button)

        split_view.set_content(vbox)

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
