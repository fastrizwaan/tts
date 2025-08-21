#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gtk, Adw, Gio, GtkSource, Pango, Gdk

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
        self.highlight_line_toggle = None
        self.overview_map_toggle = None
        self.auto_indent_toggle = None
        self.insert_spaces_toggle = None
        self.font_dropdown = None
        self.font_store = None
        self.toast_overlay = None
        self.style_provider = None

        # For overview map
        self.main_container = None
        self.overview_map = None

    def do_activate(self, *args):
        if not self.win:
            Adw.init()
            self.win = Adw.ApplicationWindow(application=self, title="TTS App")
            self.win.set_default_size(1000, 700)
            self.win.add_css_class("background")

            toolbar_view = Adw.ToolbarView()
            headerbar = Adw.HeaderBar()
            headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))

            # File open button
            open_btn = Gtk.Button(icon_name="document-open-symbolic")
            open_btn.connect("clicked", self.on_open_file)
            headerbar.pack_end(open_btn)

            # Toggles
            self.syntax_toggle = Gtk.ToggleButton(label="Syntax")
            self.syntax_toggle.connect("toggled", self.on_toggle_syntax)
            headerbar.pack_end(self.syntax_toggle)

            self.line_number_toggle = Gtk.ToggleButton(label="Line #")
            self.line_number_toggle.connect("toggled", self.on_toggle_line_numbers)
            headerbar.pack_end(self.line_number_toggle)

            self.highlight_line_toggle = Gtk.ToggleButton(label="Highlight line")
            self.highlight_line_toggle.set_active(True)
            self.highlight_line_toggle.connect("toggled", self.on_toggle_highlight_line)
            headerbar.pack_end(self.highlight_line_toggle)

            self.overview_map_toggle = Gtk.ToggleButton(label="Overview Map")
            self.overview_map_toggle.connect("toggled", self.on_toggle_overview_map)
            headerbar.pack_end(self.overview_map_toggle)

            self.auto_indent_toggle = Gtk.ToggleButton(label="Auto Indent")
            self.auto_indent_toggle.set_active(True)
            self.auto_indent_toggle.connect("toggled", self.on_toggle_auto_indent)
            headerbar.pack_end(self.auto_indent_toggle)

            self.insert_spaces_toggle = Gtk.ToggleButton(label="Spaces for Tab")
            self.insert_spaces_toggle.set_active(True)
            self.insert_spaces_toggle.connect("toggled", self.on_toggle_insert_spaces)
            headerbar.pack_end(self.insert_spaces_toggle)

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

            # Font dropdown
            self.font_store = Gio.ListStore.new(Gtk.StringObject)
            for f in ["Monospace 10", "Monospace 12", "Monospace 14", "Monospace 16"]:
                self.font_store.append(Gtk.StringObject.new(f))
            self.font_dropdown = Gtk.DropDown(model=self.font_store)
            self.font_dropdown.set_selected(1)
            self.font_dropdown.connect("notify::selected", self.on_font_selected)
            headerbar.pack_end(self.font_dropdown)

            toolbar_view.add_top_bar(headerbar)

            # Main editor
            self.main_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            scrolled = Gtk.ScrolledWindow()
            self.textview = GtkSource.View()
            self.buffer = self.textview.get_buffer()
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            self.textview.set_auto_indent(True)
            self.textview.set_insert_spaces_instead_of_tabs(True)
            self.textview.set_tab_width(4)
            self.textview.set_highlight_current_line(True)
            scrolled.set_hexpand(True)
            scrolled.set_vexpand(True)
            scrolled.set_child(self.textview)
            self.main_container.append(scrolled)

            # CSS default font
            css = b"view { font-family: Monospace; font-size: 12pt; }"
            self.style_provider = Gtk.CssProvider()
            self.style_provider.load_from_data(css)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                self.style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            # Toast overlay with Play button
            self.toast_overlay = Adw.ToastOverlay()
            play_btn = Gtk.Button(label="Play")
            play_btn.connect("clicked", self.on_play_clicked)
            self.toast_overlay.set_child(play_btn)

            # GestureClick for line/column info
            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self.on_textview_click)
            self.textview.add_controller(gesture)

            # Combine editor + overlay
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            vbox.append(self.main_container)
            vbox.append(self.toast_overlay)
            toolbar_view.set_content(vbox)
            self.win.set_content(toolbar_view)

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

    # --- Toggles ---
    def on_toggle_syntax(self, button):
        if button.get_active():
            self.detect_language_from_content()
        else:
            self.buffer.set_language(None)

    def on_toggle_line_numbers(self, button):
        self.textview.set_show_line_numbers(button.get_active())

    def on_toggle_highlight_line(self, button):
        self.textview.set_highlight_current_line(button.get_active())

    def on_toggle_overview_map(self, button):
        if button.get_active() and not self.overview_map:
            self.overview_map = GtkSource.Map()
            self.overview_map.set_view(self.textview)
            self.overview_map.set_vexpand(True)
            self.main_container.append(self.overview_map)
        elif self.overview_map:
            self.main_container.remove(self.overview_map)
            self.overview_map = None

    def on_toggle_auto_indent(self, button):
        self.textview.set_auto_indent(button.get_active())

    def on_toggle_insert_spaces(self, button):
        self.textview.set_insert_spaces_instead_of_tabs(button.get_active())

    # --- Language ---
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

    # --- Theme ---
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

    # --- Font ---
    def on_font_selected(self, dropdown, *a):
        idx = dropdown.get_selected()
        if idx < 0:
            return
        font = self.font_store.get_item(idx).get_string()
        size = font.split()[-1]
        css = f"view {{ font-family: Monospace; font-size: {size}pt; }}"
        self.style_provider.load_from_data(css.encode())

    # --- Play / Toast ---
    def on_play_clicked(self, button):
        toast = Adw.Toast.new("Playing TTS...")
        self.toast_overlay.add_toast(toast)

    # --- Line/col click ---
    def on_textview_click(self, gesture, n_press, x, y):
        success, iter_at_pos = self.textview.get_iter_at_location(int(x), int(y))
        if success:
            ln = iter_at_pos.get_line() + 1
            col = iter_at_pos.get_line_offset() + 1
            toast = Adw.Toast.new(f"Ln {ln}, Col {col}")
            self.toast_overlay.add_toast(toast)

if __name__ == "__main__":
    app = TTSApplication()
    app.run()
