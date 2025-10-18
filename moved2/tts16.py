#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gtk, Adw, Gio, GtkSource


class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.TTSApp",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None
        self.textview = None
        self.buffer = None
        self.language_manager = GtkSource.LanguageManager()
        self.style_manager = Adw.StyleManager.get_default()
        self.style_scheme_manager = GtkSource.StyleSchemeManager.get_default()
        self.lang_dropdown = None
        self.lang_store = None
        self.syntax_toggle = None
        self.theme_dropdown = None
        self.theme_store = None
        self.line_number_toggle = None

    def do_activate(self, *args):
        if not self.win:
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

            # Content with ToolbarView
            toolbar_view = Adw.ToolbarView()
            toggle_button = Gtk.Button()
            toggle_button.set_icon_name("sidebar-show-symbolic")
            toggle_button.connect("clicked",
                                  lambda b: split_view.set_show_sidebar(
                                      not split_view.get_show_sidebar()))

            headerbar = Adw.HeaderBar()
            headerbar.pack_start(toggle_button)
            headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))

            # File open
            open_btn = Gtk.Button()
            open_btn.set_icon_name("document-open-symbolic")
            open_btn.connect("clicked", self.on_open_file)
            headerbar.pack_end(open_btn)

            # Syntax toggle
            self.syntax_toggle = Gtk.ToggleButton(label="Syntax")
            self.syntax_toggle.connect("toggled", self.on_toggle_syntax)
            headerbar.pack_end(self.syntax_toggle)

            # Line number toggle
            self.line_number_toggle = Gtk.ToggleButton(label="Line #")
            self.line_number_toggle.connect("toggled", self.on_toggle_line_numbers)
            headerbar.pack_end(self.line_number_toggle)

            # Language dropdown
            self.lang_store = Gio.ListStore.new(Gtk.StringObject)
            for lang_id in sorted(self.language_manager.get_language_ids()):
                lang = self.language_manager.get_language(lang_id)
                if lang:
                    self.lang_store.append(Gtk.StringObject.new(lang.get_name()))
            self.lang_dropdown = Gtk.DropDown(model=self.lang_store)
            self.lang_dropdown.connect("notify::selected", self.on_language_selected)
            headerbar.pack_end(self.lang_dropdown)

            # Theme dropdown
            self.theme_store = Gio.ListStore.new(Gtk.StringObject)
            for scheme_id in sorted(self.style_scheme_manager.get_scheme_ids()):
                scheme = self.style_scheme_manager.get_scheme(scheme_id)
                if scheme:
                    self.theme_store.append(Gtk.StringObject.new(scheme.get_name()))
            self.theme_dropdown = Gtk.DropDown(model=self.theme_store)
            self.theme_dropdown.connect("notify::selected", self.on_theme_selected)
            headerbar.pack_end(self.theme_dropdown)

            toolbar_view.add_top_bar(headerbar)

            # Main editor
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            scrolled = Gtk.ScrolledWindow()
            self.textview = GtkSource.View()
            self.buffer = self.textview.get_buffer()
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            scrolled.set_vexpand(True)
            scrolled.set_child(self.textview)
            vbox.append(scrolled)
            vbox.append(Gtk.Button(label="Play"))
            toolbar_view.set_content(vbox)
            split_view.set_content(toolbar_view)

            # Dark/light detection
            self.apply_color_scheme()
            self.style_manager.connect("notify::color-scheme", self.on_theme_changed)

            # Language auto detect
            self.buffer.connect("changed", self.on_buffer_changed)

        self.win.present()

    # --- File open ---
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
                        self.set_dropdown_to_language(lang)
                    except Exception as e:
                        print("Error:", e)
            dlg.destroy()

        dialog.connect("response", response_cb)
        dialog.show()

    # --- Syntax toggle ---
    def on_toggle_syntax(self, button):
        if button.get_active():
            self.detect_language_from_content()
        else:
            self.buffer.set_language(None)

    # --- Line number toggle ---
    def on_toggle_line_numbers(self, button):
        self.textview.set_show_line_numbers(button.get_active())

    # --- Detect language ---
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
        self.set_dropdown_to_language(lang)

    # --- Manual lang select ---
    def on_language_selected(self, dropdown, *args):
        idx = dropdown.get_selected()
        if idx < 0:
            return
        lang_name = self.lang_store.get_item(idx).get_string()
        for lang_id in self.language_manager.get_language_ids():
            lang = self.language_manager.get_language(lang_id)
            if lang and lang.get_name() == lang_name:
                self.buffer.set_language(lang)
                break

    def set_dropdown_to_language(self, lang):
        if not lang:
            return
        lang_name = lang.get_name()
        for i in range(self.lang_store.get_n_items()):
            if self.lang_store.get_item(i).get_string() == lang_name:
                self.lang_dropdown.set_selected(i)
                break

    # --- Theme select ---
    def on_theme_selected(self, dropdown, *a):
        idx = dropdown.get_selected()
        if idx < 0:
            return
        theme_name = self.theme_store.get_item(idx).get_string()
        for scheme_id in self.style_scheme_manager.get_scheme_ids():
            scheme = self.style_scheme_manager.get_scheme(scheme_id)
            if scheme and scheme.get_name() == theme_name:
                self.buffer.set_style_scheme(scheme)
                break

    # --- Dark/Light system ---
    def apply_color_scheme(self):
        scheme_id = "Adwaita-dark" if self.style_manager.get_dark() else "Adwaita"
        scheme = self.style_scheme_manager.get_scheme(scheme_id)
        if scheme:
            self.buffer.set_style_scheme(scheme)

    def on_theme_changed(self, *a):
        self.apply_color_scheme()

    def on_buffer_changed(self, buf):
        if self.syntax_toggle.get_active():
            self.detect_language_from_content()


if __name__ == "__main__":
    app = TTSApplication()
    app.run()

