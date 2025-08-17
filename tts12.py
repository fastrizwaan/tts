#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gtk, Adw, Gio, GtkSource

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.TTSApp",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self, *args):
        if not self.props.active_window:
            self.win = Adw.ApplicationWindow(application=self, title="TTS App")
            self.win.set_default_size(1000, 700)

            split_view = Adw.OverlaySplitView(
                sidebar_width_fraction=0.25,
                show_sidebar=True
            )
            self.win.set_content(split_view)

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
            toggle_button.connect(
                "clicked",
                lambda b: split_view.set_show_sidebar(
                    not split_view.get_show_sidebar()
                )
            )

            open_button = Gtk.Button()
            open_button.set_icon_name("document-open-symbolic")
            open_button.connect("clicked", self.on_open_file)

            headerbar = Adw.HeaderBar()
            headerbar.pack_start(toggle_button)
            headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))
            headerbar.pack_end(open_button)
            toolbar_view.add_top_bar(headerbar)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

            scrolled = Gtk.ScrolledWindow()
            self.textview = GtkSource.View()
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            self.textview.set_show_line_numbers(False)
            self.textview.set_monospace(True)

            # Buffer + highlighting
            self.buffer = GtkSource.Buffer()
            self.buffer.set_highlight_syntax(False)
            self.textview.set_buffer(self.buffer)

            # Respect Adwaita dark/light style
            style_scheme_manager = GtkSource.StyleSchemeManager.get_default()
            style_manager = Adw.StyleManager.get_default()
            def update_scheme(_sm, _pspec=None):
                if style_manager.get_dark():
                    scheme = style_scheme_manager.get_scheme("Adwaita-dark")
                else:
                    scheme = style_scheme_manager.get_scheme("Adwaita")
                if scheme:
                    self.buffer.set_style_scheme(scheme)
            style_manager.connect("notify::dark", update_scheme)
            update_scheme(style_manager)

            scrolled.set_vexpand(True)
            scrolled.set_child(self.textview)
            vbox.append(scrolled)

            # Controls
            controls = Gtk.Box(spacing=6)
            self.linenum_btn = Gtk.CheckButton(label="Line Numbers")
            self.linenum_btn.connect(
                "toggled",
                lambda b: self.textview.set_show_line_numbers(b.get_active())
            )
            controls.append(self.linenum_btn)

            self.syntax_btn = Gtk.CheckButton(label="Syntax Highlighting")
            self.syntax_btn.connect(
                "toggled",
                lambda b: self.buffer.set_highlight_syntax(b.get_active())
            )
            controls.append(self.syntax_btn)

            play_button = Gtk.Button(label="Play")
            controls.append(play_button)

            vbox.append(controls)

            toolbar_view.set_content(vbox)
            split_view.set_content(toolbar_view)

            self.win.present()

    def on_open_file(self, button):
        dialog = Gtk.FileChooserNative(
            title="Open File",
            transient_for=self.win,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel"
        )

        def response_cb(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = dlg.get_file()
                if file:
                    try:
                        content = file.load_contents()[1].decode("utf-8")
                        self.buffer.set_text(content)
                    except Exception as e:
                        print("Error:", e)
            dlg.destroy()

        dialog.connect("response", response_cb)
        dialog.show()

if __name__ == "__main__":
    app = TTSApplication()
    app.run()

