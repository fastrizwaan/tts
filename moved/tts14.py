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
        self.language_manager = GtkSource.LanguageManager.get_default()

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
            sidebar.append(Gtk.Button(label="Menu"))
            for voice in ["English", "Hindi", "Spanish"]:
                sidebar.append(Gtk.Label(label=voice, xalign=0))
            split_view.set_sidebar(sidebar)

            # Content area
            toolbar_view = Adw.ToolbarView()
            toggle_button = Gtk.Button(icon_name="sidebar-show-symbolic")
            toggle_button.connect(
                "clicked",
                lambda b: split_view.set_show_sidebar(
                    not split_view.get_show_sidebar()
                )
            )
            open_button = Gtk.Button(icon_name="document-open-symbolic")
            open_button.connect("clicked", self.on_open_file)

            headerbar = Adw.HeaderBar()
            headerbar.pack_start(toggle_button)
            headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))
            headerbar.pack_end(open_button)
            toolbar_view.add_top_bar(headerbar)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

            scrolled = Gtk.ScrolledWindow()
            self.textview = GtkSource.View(
                show_line_numbers=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD
            )
            self.buffer = GtkSource.Buffer(highlight_syntax=False)
            self.textview.set_buffer(self.buffer)

            # update language when buffer changes (for pasted code)
            self.buffer.connect("changed", self.on_buffer_changed)

            # Respect Adwaita dark/light style
            style_scheme_manager = GtkSource.StyleSchemeManager.get_default()
            style_manager = Adw.StyleManager.get_default()
            def update_scheme(_sm, _pspec=None):
                scheme = style_scheme_manager.get_scheme(
                    "Adwaita-dark" if style_manager.get_dark() else "Adwaita"
                )
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
            self.syntax_btn.connect("toggled", self.on_toggle_syntax)
            controls.append(self.syntax_btn)

            # Language chooser dropdown
            self.lang_store = Gio.ListStore.new(Gtk.StringObject)
            for lang_id in sorted(self.language_manager.get_language_ids()):
                lang = self.language_manager.get_language(lang_id)
                if lang:
                    self.lang_store.append(Gtk.StringObject.new(lang.get_name()))

            self.lang_dropdown = Gtk.DropDown(model=self.lang_store)
            self.lang_dropdown.set_sensitive(False)  # only enabled if syntax highlighting is ON
            self.lang_dropdown.connect("notify::selected-item", self.on_lang_selected)
            controls.append(self.lang_dropdown)

            controls.append(Gtk.Button(label="Play"))
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
                        path = file.get_path()
                        content = file.load_contents()[1].decode("utf-8")
                        self.buffer.set_text(content)

                        lang = self.language_manager.guess_language(path, None)
                        self.buffer.set_language(lang)
                    except Exception as e:
                        print("Error:", e)
            dlg.destroy()

        dialog.connect("response", response_cb)
        dialog.show()

    def on_toggle_syntax(self, btn):
        enabled = btn.get_active()
        self.buffer.set_highlight_syntax(enabled)
        self.lang_dropdown.set_sensitive(enabled)
        if enabled and not self.buffer.get_language():
            self.detect_language_from_content()

    def on_buffer_changed(self, buffer):
        if self.buffer.get_highlight_syntax() and not self.buffer.get_language():
            self.detect_language_from_content()

    def detect_language_from_content(self):
        text = self.buffer.get_text(
            self.buffer.get_start_iter(),
            self.buffer.get_end_iter(),
            True
        )
        if not text.strip():
            return
        content_type, uncertain = Gio.content_type_guess(data=text.encode())
        lang = self.language_manager.guess_language(None, content_type)
        self.buffer.set_language(lang)

    def on_lang_selected(self, dropdown, pspec):
        if not self.buffer.get_highlight_syntax():
            return
        item = dropdown.get_selected_item()
        if item:
            lang_name = item.get_string()
            # map back name -> language
            for lang_id in self.language_manager.get_language_ids():
                lang = self.language_manager.get_language(lang_id)
                if lang and lang.get_name() == lang_name:
                    self.buffer.set_language(lang)
                    break

if __name__ == "__main__":
    app = TTSApplication()
    app.run()

