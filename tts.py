import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib
import sys

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.ttsapp')
        self.split_view = None
        self.sidebar_toggled = True

    def do_activate(self):
        # Create main window
        win = self.props.active_window
        if not win:
            win = Adw.ApplicationWindow(application=self, title="TTS App")
            win.set_default_size(800, 600)

        # Create toolbar view (no headerbar)
        toolbar_view = Adw.ToolbarView()
        win.set_content(toolbar_view)

        # Create sidebar content
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar_box.set_margin_top(12)
        sidebar_box.set_margin_bottom(12)
        sidebar_box.set_margin_start(12)
        sidebar_box.set_margin_end(12)

        sidebar_title = Gtk.Label(label="Text-to-Speech Settings")
        sidebar_title.add_css_class("title-1")
        sidebar_box.append(sidebar_title)

        voice_label = Gtk.Label(label="Voice:")
        voice_combo = Gtk.ComboBoxText()
        voice_combo.append("female", "Female")
        voice_combo.append("male", "Male")
        voice_combo.set_active(0)
        
        sidebar_box.append(voice_label)
        sidebar_box.append(voice_combo)

        rate_label = Gtk.Label(label="Speed:")
        rate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.0, 0.1)
        rate_scale.set_value(1.0)
        sidebar_box.append(rate_label)
        sidebar_box.append(rate_scale)

        # Create main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)

        text_buffer = Gtk.TextBuffer()
        text_view = Gtk.TextView(buffer=text_buffer)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(text_view)
        main_box.append(scrolled)

        # Create action buttons
        button_box = Gtk.Box(spacing=6)
        speak_button = Gtk.Button(label="Speak")
        stop_button = Gtk.Button(label="Stop")
        button_box.append(speak_button)
        button_box.append(stop_button)
        main_box.append(button_box)

        # Create OverlaySplitView
        self.split_view = Adw.OverlaySplitView(
            sidebar=sidebar_box,
            content=main_box,
            collapsed=False,
            show_sidebar=True
        )
        toolbar_view.set_content(self.split_view)

        # Create bottom toolbar
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_bar.add_css_class("toolbar")
        
        toggle_button = Gtk.Button(icon_name="sidebar-show-symbolic")
        toggle_button.set_tooltip_text("Toggle Sidebar")
        toggle_button.connect("clicked", self.toggle_sidebar)
        bottom_bar.append(toggle_button)
        
        toolbar_view.add_bottom_bar(bottom_bar)

        win.present()

    def toggle_sidebar(self, button):
        if self.split_view:
            self.sidebar_toggled = not self.sidebar_toggled
            self.split_view.set_show_sidebar(self.sidebar_toggled)
            button.set_icon_name(
                "sidebar-hide-symbolic" if self.sidebar_toggled 
                else "sidebar-show-symbolic"
            )

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == '__main__':
    main()
