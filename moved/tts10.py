#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.TTSApp",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self, *args):
        if not self.props.active_window:
            win = Adw.ApplicationWindow(application=self, title="TTS App")
            win.set_default_size(1000, 700)

            split_view = Adw.OverlaySplitView(
                sidebar_width_fraction=0.25,
                show_sidebar=True
            )
            win.set_content(split_view)

            # Sidebar
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            sidebar.set_margin_top(12)
            sidebar.set_margin_bottom(12)
            sidebar.set_margin_start(12)
            sidebar.set_margin_end(12)

            menu_btn = Gtk.Button(label="Menu")
            sidebar.append(menu_btn)

            for voice in ["English", "Hindi", "Spanish"]:
                sidebar.append(Gtk.Label(label=voice, xalign=0))

            split_view.set_sidebar(sidebar)

            # Content with ToolbarView
            toolbar_view = Adw.ToolbarView()

            toggle_button = Gtk.Button()
            toggle_button.set_icon_name("sidebar-show-symbolic")
            toggle_button.connect("clicked", lambda b: split_view.set_show_sidebar(not split_view.get_show_sidebar()))

            headerbar = Adw.HeaderBar()
            headerbar.pack_start(toggle_button)
            headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))
            toolbar_view.add_top_bar(headerbar)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

            scrolled = Gtk.ScrolledWindow()
            textview = Gtk.TextView()
            textview.set_wrap_mode(Gtk.WrapMode.WORD)
            scrolled.set_vexpand(True)
            scrolled.set_child(textview)
            vbox.append(scrolled)

            play_button = Gtk.Button(label="Play")
            vbox.append(play_button)

            toolbar_view.set_content(vbox)
            split_view.set_content(toolbar_view)

            win.present()

if __name__ == "__main__":
    app = TTSApplication()
    app.run()

