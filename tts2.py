#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GObject
import sys

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.TTSApp",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        self.window = TTSWindow(application=app)
        self.window.present()

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Window setup
        self.set_title("TTS Application")
        self.set_default_size(1000, 700)
        
        # Create toolbar view
        self.toolbar_view = Adw.ToolbarView()
        
        # Create header bar
        self.header_bar = Adw.HeaderBar()
        
        # Sidebar toggle button
        self.sidebar_button = Gtk.ToggleButton()
        self.sidebar_button.set_icon_name("sidebar-show-symbolic")
        self.sidebar_button.set_tooltip_text("Toggle Sidebar")
        self.sidebar_button.connect("toggled", self.on_sidebar_toggled)
        self.header_bar.pack_start(self.sidebar_button)
        
        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Main Menu")
        
        # Create menu model
        menu_model = Gio.Menu()
        menu_model.append("Preferences", "app.preferences")
        menu_model.append("About", "app.about")
        menu_button.set_menu_model(menu_model)
        self.header_bar.pack_end(menu_button)
        
        # Add header bar to toolbar view
        self.toolbar_view.add_top_bar(self.header_bar)
        
        # Create main split view
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_width_fraction(0.25)
        self.split_view.set_max_sidebar_width(350)
        self.split_view.set_min_sidebar_width(200)
        
        # Create sidebar content
        sidebar_content = self.create_sidebar()
        self.split_view.set_sidebar(sidebar_content)
        
        # Create main content
        main_content = self.create_main_content()
        self.split_view.set_content(main_content)
        
        # Set the split view as the toolbar view content
        self.toolbar_view.set_content(self.split_view)
        
        # Set the toolbar view as window content
        self.set_content(self.toolbar_view)
        
        # Initially show sidebar
        self.split_view.set_show_sidebar(True)
        self.sidebar_button.set_active(True)

    def create_sidebar(self):
        """Create the sidebar content"""
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar_box.set_margin_top(12)
        sidebar_box.set_margin_bottom(12)
        sidebar_box.set_margin_start(12)
        sidebar_box.set_margin_end(12)
        
        # Sidebar title
        sidebar_title = Gtk.Label(label="TTS Settings")
        sidebar_title.add_css_class("title-2")
        sidebar_title.set_halign(Gtk.Align.START)
        sidebar_box.append(sidebar_title)
        
        # Voice selection group
        voice_group = Adw.PreferencesGroup()
        voice_group.set_title("Voice Settings")
        voice_group.set_description("Configure text-to-speech voice options")
        
        # Voice selection row
        voice_row = Adw.ComboRow()
        voice_row.set_title("Voice")
        voice_row.set_subtitle("Select TTS voice")
        
        # Create string list for voices
        voice_list = Gtk.StringList()
        voice_list.append("Default Voice")
        voice_list.append("Male Voice 1")
        voice_list.append("Female Voice 1")
        voice_list.append("Female Voice 2")
        voice_row.set_model(voice_list)
        voice_group.add(voice_row)
        
        # Speed adjustment
        speed_row = Adw.ActionRow()
        speed_row.set_title("Speech Speed")
        speed_row.set_subtitle("Adjust speaking rate")
        
        speed_adjustment = Gtk.Adjustment(
            value=1.0,
            lower=0.5,
            upper=2.0,
            step_increment=0.1,
            page_increment=0.1,
            page_size=0
        )
        speed_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=speed_adjustment)
        speed_scale.set_digits(1)
        speed_scale.set_hexpand(True)
        speed_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, "Normal")
        speed_row.add_suffix(speed_scale)
        voice_group.add(speed_row)
        
        # Volume adjustment
        volume_row = Adw.ActionRow()
        volume_row.set_title("Volume")
        volume_row.set_subtitle("Adjust speech volume")
        
        volume_adjustment = Gtk.Adjustment(
            value=0.8,
            lower=0.0,
            upper=1.0,
            step_increment=0.1,
            page_increment=0.1,
            page_size=0
        )
        volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=volume_adjustment)
        volume_scale.set_digits(1)
        volume_scale.set_hexpand(True)
        volume_row.add_suffix(volume_scale)
        voice_group.add(volume_row)
        
        sidebar_box.append(voice_group)
        
        # Output settings group
        output_group = Adw.PreferencesGroup()
        output_group.set_title("Output Settings")
        
        # Save audio switch
        save_audio_row = Adw.SwitchRow()
        save_audio_row.set_title("Save Audio Files")
        save_audio_row.set_subtitle("Save generated speech as audio files")
        output_group.add(save_audio_row)
        
        # Auto-play switch
        autoplay_row = Adw.SwitchRow()
        autoplay_row.set_title("Auto-play")
        autoplay_row.set_subtitle("Automatically play generated speech")
        autoplay_row.set_active(True)
        output_group.add(autoplay_row)
        
        sidebar_box.append(output_group)
        
        return sidebar_box

    def create_main_content(self):
        """Create the main content area"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(24)
        main_box.set_margin_bottom(24)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)
        
        # Main title
        main_title = Gtk.Label(label="Text to Speech")
        main_title.add_css_class("title-1")
        main_title.set_halign(Gtk.Align.START)
        main_box.append(main_title)
        
        # Text input section
        input_group = Adw.PreferencesGroup()
        input_group.set_title("Input Text")
        input_group.set_description("Enter the text you want to convert to speech")
        
        # Text view in a scrolled window
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_min_content_height(200)
        scrolled_window.set_vexpand(True)
        
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_margin_top(12)
        self.text_view.set_margin_bottom(12)
        self.text_view.set_margin_start(12)
        self.text_view.set_margin_end(12)
        
        # Set placeholder text
        buffer = self.text_view.get_buffer()
        buffer.set_text("Enter your text here to convert to speech...")
        
        scrolled_window.set_child(self.text_view)
        main_box.append(scrolled_window)
        
        # Control buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(12)
        
        # Generate speech button
        generate_button = Gtk.Button(label="Generate Speech")
        generate_button.add_css_class("suggested-action")
        generate_button.add_css_class("pill")
        generate_button.set_size_request(150, -1)
        generate_button.connect("clicked", self.on_generate_clicked)
        button_box.append(generate_button)
        
        # Play button
        play_button = Gtk.Button()
        play_button.set_icon_name("media-playback-start-symbolic")
        play_button.set_tooltip_text("Play Generated Speech")
        play_button.connect("clicked", self.on_play_clicked)
        button_box.append(play_button)
        
        # Stop button
        stop_button = Gtk.Button()
        stop_button.set_icon_name("media-playback-stop-symbolic")
        stop_button.set_tooltip_text("Stop Speech")
        stop_button.connect("clicked", self.on_stop_clicked)
        button_box.append(stop_button)
        
        # Clear button
        clear_button = Gtk.Button(label="Clear")
        clear_button.connect("clicked", self.on_clear_clicked)
        button_box.append(clear_button)
        
        main_box.append(button_box)
        
        # Status label
        self.status_label = Gtk.Label(label="Ready to generate speech")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_margin_top(12)
        main_box.append(self.status_label)
        
        return main_box

    def on_sidebar_toggled(self, button):
        """Handle sidebar toggle button"""
        is_active = button.get_active()
        self.split_view.set_show_sidebar(is_active)
        
        # Update button icon
        if is_active:
            button.set_icon_name("sidebar-show-symbolic")
        else:
            button.set_icon_name("sidebar-hide-symbolic")

    def on_generate_clicked(self, button):
        """Handle generate speech button click"""
        buffer = self.text_view.get_buffer()
        start_iter = buffer.get_start_iter()
        end_iter = buffer.get_end_iter()
        text = buffer.get_text(start_iter, end_iter, False)
        
        if text.strip():
            self.status_label.set_text(f"Generating speech for {len(text)} characters...")
            # Here you would integrate your TTS engine
            print(f"Generating TTS for: {text[:50]}...")
        else:
            self.status_label.set_text("Please enter some text first")

    def on_play_clicked(self, button):
        """Handle play button click"""
        self.status_label.set_text("Playing speech...")
        # Here you would play the generated audio
        print("Playing speech...")

    def on_stop_clicked(self, button):
        """Handle stop button click"""
        self.status_label.set_text("Speech stopped")
        # Here you would stop the audio playback
        print("Stopping speech...")

    def on_clear_clicked(self, button):
        """Handle clear button click"""
        buffer = self.text_view.get_buffer()
        buffer.set_text("")
        self.status_label.set_text("Text cleared")

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == '__main__':
    main()
