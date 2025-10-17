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

    def do_activate(self, *args):
        if not self.win:
            self.win = Adw.ApplicationWindow(application=self, title="TTS App")
            self.win.set_default_size(1000, 700)

            # Split view (sidebar + content)
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

            # Toggle sidebar button
            toggle_button = Gtk.Button()
            toggle_button.set_icon_name("sidebar-show-symbolic")
            toggle_button.connect("clicked",
                                  lambda b: split_view.set_show_sidebar(
                                      not split_view.get_show_sidebar()))

            # HeaderBar
            headerbar = Adw.HeaderBar()
            headerbar.pack_start(toggle_button)
            headerbar.set_title_widget(Gtk.Label(label="TTS Editor"))

            # Open button
            open_btn = Gtk.Button()
            open_btn.set_icon_name("document-open-symbolic")
            open_btn.connect("clicked", self.on_open_file)
            headerbar.pack_end(open_btn)

            # Syntax toggle
            self.syntax_toggle = Gtk.ToggleButton(label="Syntax")
            self.syntax_toggle.connect("toggled", self.on_toggle_syntax)
            headerbar.pack_end(self.syntax_toggle)

            # Language dropdown
            self.lang_store = Gio.ListStore.new(Gtk.StringObject)
            langs = self.language_manager.get_language_ids()
            for lang_id in sorted(langs):
                lang = self.language_manager.get_language(lang_id)
                if lang:
                    self.lang_store.append(Gtk.StringObject.new(lang.get_name()))
            self.lang_dropdown = Gtk.DropDown(model=self.lang_store)
            self.lang_dropdown.connect("notify::selected", self.on_language_selected)
            headerbar.pack_end(self.lang_dropdown)

            toolbar_view.add_top_bar(headerbar)

            # Main content area
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            scrolled = Gtk.ScrolledWindow()
            self.textview = GtkSource.View()
            self.buffer = self.textview.get_buffer()
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            scrolled.set_vexpand(True)
            scrolled.set_child(self.textview)
            vbox.append(scrolled)

            play_button = Gtk.Button(label="Play")
            vbox.append(play_button)

            toolbar_view.set_content(vbox)
            split_view.set_content(toolbar_view)

            # Apply dark/light style scheme
            self.apply_color_scheme()
            self.style_manager.connect("notify::color-scheme", self.on_theme_changed)

            # Detect language when buffer changes (paste, typing)
            self.buffer.connect("changed", self.on_buffer_changed)

        self.win.present()

    # ---------- File open ----------
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

    # ---------- Syntax toggle ----------
    def on_toggle_syntax(self, button):
        if button.get_active():
            self.detect_language_from_content()
        else:
            self.buffer.set_language(None)

    # ---------- Detect language ----------
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

    # ---------- Manual dropdown selection ----------
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

    # ---------- Sync dropdown with detected language ----------
    def set_dropdown_to_language(self, lang):
        if not lang:
            return
        lang_name = lang.get_name()
        for i in range(self.lang_store.get_n_items()):
            if self.lang_store.get_item(i).get_string() == lang_name:
                self.lang_dropdown.set_selected(i)
                break

    # ---------- Dark/Light scheme ----------
    def apply_color_scheme(self):
        scheme_id = "Adwaita-dark" if self.style_manager.get_dark() else "Adwaita"
        scheme = self.style_scheme_manager.get_scheme(scheme_id)
        if scheme:
            self.buffer.set_style_scheme(scheme)

    def on_theme_changed(self, *a):
        self.apply_color_scheme()

    # ---------- Buffer change ----------
    def on_buffer_changed(self, buf):
        if self.syntax_toggle.get_active():
            self.detect_language_from_content()


if __name__ == "__main__":
    app = TTSApplication()
    app.run()

