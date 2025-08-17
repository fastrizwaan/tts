#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TTS App")
        self.set_default_size(1200, 800)

        split_view = Adw.OverlaySplitView(
            sidebar_width_fraction=0.25,
            show_sidebar=True
        )

        # Sidebar
        sidebar_view = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label="Voices"))
        sidebar_view.add_top_bar(sidebar_header)

        listbox = Gtk.ListBox()
        for voice in ["English", "Hindi", "Spanish"]:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=voice, xalign=0))
            listbox.append(row)
        sidebar_view.set_content(listbox)

        # Content Area
        content_view = Adw.ToolbarView()
        content_header = Adw.HeaderBar()
        content_header.set_title_widget(Gtk.Label(label="Text-to-Speech"))
        content_view.add_top_bar(content_header)

        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_start=12,
            margin_end=12
        )

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        vbox.append(self.textview)

        play_button = Gtk.Button(label="Play")
        vbox.append(play_button)

        content_view.set_content(vbox)

        # Correct usage
        split_view.set_sidebar(sidebar_view)
        split_view.set_content(content_view)

        self.set_content(split_view)


class TTSApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.TTSApp",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self, *args):
        win = self.props.active_window
        if not win:
            win = TTSWindow(self)
        win.present()


if __name__ == "__main__":
    app = TTSApp()
    app.run()
