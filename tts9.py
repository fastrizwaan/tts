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
        self.split_view = Adw.OverlaySplitView(
            sidebar_width_fraction=0.25,
            show_sidebar=True
        )
        self.set_content(self.split_view)

        # Sidebar
        sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6
        )
        sidebar.set_margin_top(12)
        sidebar.set_margin_bottom(12)
        sidebar.set_margin_start(12)
        sidebar.set_margin_end(12)

        menu_btn = Gtk.Button(label="Menu")
        sidebar.append(menu_btn)

        for voice in ["English", "Hindi", "Spanish"]:
            sidebar.append(Gtk.Label(label=voice, xalign=0))

        self.split_view.set_sidebar(sidebar)

        # Content with ToolbarView
        toolbar_view = Adw.ToolbarView()

        # Toggle button
        toggle_button = Gtk.Button()
        toggle_button.set_icon_name("sidebar-show-symbolic")
        toggle_button.connect("clicked", self.toggle_sidebar)

        # HeaderBar inside content
        headerbar = Adw.HeaderBar()
        headerbar.pack_start(toggle_button)
        headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))
        toolbar_view.add_top_bar(headerbar)

        # Main content area
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        scrolled = Gtk.ScrolledWindow()
        
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.textview)
        vbox.append(scrolled)

        play_button = Gtk.Button(label="Play")
        vbox.append(play_button)

        toolbar_view.set_content(vbox)

        self.split_view.set_content(toolbar_view)

    def toggle_sidebar(self, button):
        """Toggle sidebar visibility"""
        visible = self.split_view.get_show_sidebar()
        self.split_view.set_show_sidebar(not visible)


class TTSApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.TTSApp",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self, *args):
        win = self.props.active_window
        if not win:
            win = TTSWindow(self)
        win.present()


if __name__ == "__main__":
    app = TTSApp()
    app.run()

