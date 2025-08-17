#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GObject, Pango
import sys
from datetime import datetime

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

class TTSHistoryItem:
    def __init__(self, text, timestamp=None):
        self.text = text
        self.timestamp = timestamp or datetime.now()
        self.audio_file = None

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Window setup
        self.set_title("TTS Studio")
        self.set_default_size(1200, 800)
        
        # History storage
        self.tts_history = []
        
        # Create toolbar view
        self.toolbar_view = Adw.ToolbarView()
        
        # Create header bar
        self.header_bar = Adw.HeaderBar()
        
        # Sidebar toggle button
        self.sidebar_button = Gtk.ToggleButton()
        self.sidebar_button.set_icon_name("sidebar-show-symbolic")
        self.sidebar_button.set_tooltip_text("Toggle History Sidebar")
        self.sidebar_button.connect("toggled", self.on_sidebar_toggled)
        self.header_bar.pack_start(self.sidebar_button)
        
        # Settings button
        settings_button = Gtk.Button()
        settings_button.set_icon_name("preferences-system-symbolic")
        settings_button.set_tooltip_text("TTS Settings")
        settings_button.connect("clicked", self.show_settings_popover)
        self.header_bar.pack_end(settings_button)
        
        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Main Menu")
        
        # Create menu model
        menu_model = Gio.Menu()
        menu_model.append("Export History", "app.export")
        menu_model.append("Clear History", "app.clear_history")
        menu_model.append("About", "app.about")
        menu_button.set_menu_model(menu_model)
        self.header_bar.pack_end(menu_button)
        
        # Add header bar to toolbar view
        self.toolbar_view.add_top_bar(self.header_bar)
        
        # Create main split view
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_width_fraction(0.3)
        self.split_view.set_max_sidebar_width(400)
        self.split_view.set_min_sidebar_width(280)
        
        # Create sidebar (history panel)
        sidebar_content = self.create_sidebar()
        self.split_view.set_sidebar(sidebar_content)
        
        # Create main content area
        main_content = self.create_main_content()
        self.split_view.set_content(main_content)
        
        # Set the split view as the toolbar view content
        self.toolbar_view.set_content(self.split_view)
        
        # Set the toolbar view as window content
        self.set_content(self.toolbar_view)
        
        # Initially show sidebar
        self.split_view.set_show_sidebar(True)
        self.sidebar_button.set_active(True)
        
        # Add some sample history items
        self.add_sample_history()

    def create_sidebar(self):
        """Create the history sidebar similar to chat interface"""
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.add_css_class("sidebar")
        
        # History header
        history_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        history_header.set_margin_top(12)
        history_header.set_margin_bottom(12)
        history_header.set_margin_start(16)
        history_header.set_margin_end(16)
        
        history_title = Gtk.Label(label="TTS History")
        history_title.add_css_class("title-4")
        history_title.set_halign(Gtk.Align.START)
        history_title.set_hexpand(True)
        history_header.append(history_title)
        
        # New TTS button
        new_button = Gtk.Button()
        new_button.set_icon_name("list-add-symbolic")
        new_button.set_tooltip_text("New TTS")
        new_button.add_css_class("flat")
        new_button.connect("clicked", self.on_new_tts)
        history_header.append(new_button)
        
        sidebar_box.append(history_header)
        
        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_box.append(separator)
        
        # Scrolled window for history list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        
        # History list box
        self.history_listbox = Gtk.ListBox()
        self.history_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.history_listbox.add_css_class("navigation-sidebar")
        self.history_listbox.connect("row-selected", self.on_history_selected)
        
        scrolled.set_child(self.history_listbox)
        sidebar_box.append(scrolled)
        
        return sidebar_box

    def create_main_content(self):
        """Create the main content area similar to chat interface"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Main content area (where generated text/results appear)
        content_scroll = Gtk.ScrolledWindow()
        content_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        content_scroll.set_vexpand(True)
        
        self.content_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content_area.set_margin_top(24)
        self.content_area.set_margin_bottom(24)
        self.content_area.set_margin_start(24)
        self.content_area.set_margin_end(24)
        
        # Welcome message
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        welcome_box.set_halign(Gtk.Align.CENTER)
        welcome_box.set_valign(Gtk.Align.CENTER)
        welcome_box.set_vexpand(True)
        
        welcome_title = Gtk.Label(label="Welcome to TTS Studio")
        welcome_title.add_css_class("title-1")
        welcome_box.append(welcome_title)
        
        welcome_subtitle = Gtk.Label(label="Enter text below to generate speech")
        welcome_subtitle.add_css_class("title-4")
        welcome_subtitle.add_css_class("dim-label")
        welcome_box.append(welcome_subtitle)
        
        self.content_area.append(welcome_box)
        
        content_scroll.set_child(self.content_area)
        main_box.append(content_scroll)
        
        # Bottom input area
        input_area = self.create_input_area()
        main_box.append(input_area)
        
        return main_box

    def create_input_area(self):
        """Create the bottom input area similar to chat interface"""
        input_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        input_container.add_css_class("toolbar")
        
        # Quick settings bar
        settings_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        settings_bar.set_margin_top(8)
        settings_bar.set_margin_bottom(8)
        settings_bar.set_margin_start(16)
        settings_bar.set_margin_end(16)
        
        # Voice selection
        voice_label = Gtk.Label(label="Voice:")
        voice_label.add_css_class("dim-label")
        settings_bar.append(voice_label)
        
        self.voice_dropdown = Gtk.DropDown()
        voice_list = Gtk.StringList()
        voice_list.append("Default Voice")
        voice_list.append("Neural Voice 1")
        voice_list.append("Neural Voice 2")
        voice_list.append("Robotic Voice")
        self.voice_dropdown.set_model(voice_list)
        settings_bar.append(self.voice_dropdown)
        
        # Speed control
        speed_label = Gtk.Label(label="Speed:")
        speed_label.add_css_class("dim-label")
        speed_label.set_margin_start(12)
        settings_bar.append(speed_label)
        
        speed_adjustment = Gtk.Adjustment(value=1.0, lower=0.5, upper=2.0, step_increment=0.1)
        self.speed_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=speed_adjustment)
        self.speed_scale.set_digits(1)
        self.speed_scale.set_size_request(100, -1)
        settings_bar.append(self.speed_scale)
        
        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        settings_bar.append(spacer)
        
        # Status indicator
        self.status_indicator = Gtk.Label(label="Ready")
        self.status_indicator.add_css_class("dim-label")
        settings_bar.append(self.status_indicator)
        
        input_container.append(settings_bar)
        
        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        input_container.append(separator)
        
        # Input box
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_box.set_margin_top(12)
        input_box.set_margin_bottom(12)
        input_box.set_margin_start(16)
        input_box.set_margin_end(16)
        
        # Text entry
        self.text_entry = Gtk.Entry()
        self.text_entry.set_placeholder_text("Enter text to convert to speech...")
        self.text_entry.set_hexpand(True)
        self.text_entry.connect("activate", self.on_generate_speech)
        input_box.append(self.text_entry)
        
        # Generate button
        self.generate_button = Gtk.Button()
        self.generate_button.set_icon_name("media-record-symbolic")
        self.generate_button.set_tooltip_text("Generate Speech")
        self.generate_button.add_css_class("suggested-action")
        self.generate_button.connect("clicked", self.on_generate_speech)
        input_box.append(self.generate_button)
        
        input_container.append(input_box)
        
        return input_container

    def add_sample_history(self):
        """Add some sample history items"""
        sample_texts = [
            "Welcome to the TTS Studio application",
            "The quick brown fox jumps over the lazy dog",
            "Artificial intelligence is transforming our world",
            "Today is a beautiful day for learning new things"
        ]
        
        for text in sample_texts:
            self.add_history_item(text)

    def add_history_item(self, text):
        """Add a new item to the history"""
        item = TTSHistoryItem(text)
        self.tts_history.append(item)
        
        # Create list box row
        row = Gtk.ListBoxRow()
        row.set_margin_top(4)
        row.set_margin_bottom(4)
        
        item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        item_box.set_margin_top(8)
        item_box.set_margin_bottom(8)
        item_box.set_margin_start(16)
        item_box.set_margin_end(16)
        
        # Preview text (truncated)
        preview_text = text[:50] + "..." if len(text) > 50 else text
        text_label = Gtk.Label(label=preview_text)
        text_label.set_halign(Gtk.Align.START)
        text_label.set_wrap(True)
        text_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_label.set_lines(2)
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        item_box.append(text_label)
        
        # Timestamp
        time_label = Gtk.Label(label=item.timestamp.strftime("%H:%M"))
        time_label.add_css_class("dim-label")
        time_label.add_css_class("caption")
        time_label.set_halign(Gtk.Align.START)
        item_box.append(time_label)
        
        row.set_child(item_box)
        row.item = item  # Store reference to item
        
        self.history_listbox.append(row)
        
        # Select the new item
        self.history_listbox.select_row(row)

    def on_history_selected(self, listbox, row):
        """Handle history item selection"""
        if row and hasattr(row, 'item'):
            self.text_entry.set_text(row.item.text)
            self.show_content_for_item(row.item)

    def show_content_for_item(self, item):
        """Show content in main area for selected item"""
        # Clear current content
        child = self.content_area.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.content_area.remove(child)
            child = next_child
        
        # Add item content
        item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        
        # Text display
        text_frame = Gtk.Frame()
        text_frame.set_margin_bottom(12)
        
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        text_box.set_margin_top(16)
        text_box.set_margin_bottom(16)
        text_box.set_margin_start(16)
        text_box.set_margin_end(16)
        
        text_label = Gtk.Label(label="Generated Text:")
        text_label.add_css_class("heading")
        text_label.set_halign(Gtk.Align.START)
        text_box.append(text_label)
        
        content_label = Gtk.Label(label=item.text)
        content_label.set_wrap(True)
        content_label.set_wrap_mode(Gtk.WrapMode.WORD)
        content_label.set_halign(Gtk.Align.START)
        content_label.set_selectable(True)
        text_box.append(content_label)
        
        text_frame.set_child(text_box)
        item_box.append(text_frame)
        
        # Control buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.START)
        
        play_button = Gtk.Button(label="Play")
        play_button.set_icon_name("media-playback-start-symbolic")
        play_button.add_css_class("suggested-action")
        play_button.connect("clicked", lambda btn: self.play_tts(item))
        button_box.append(play_button)
        
        regenerate_button = Gtk.Button(label="Regenerate")
        regenerate_button.set_icon_name("view-refresh-symbolic")
        regenerate_button.connect("clicked", lambda btn: self.regenerate_tts(item))
        button_box.append(regenerate_button)
        
        item_box.append(button_box)
        
        self.content_area.append(item_box)

    def on_sidebar_toggled(self, button):
        """Handle sidebar toggle"""
        is_active = button.get_active()
        self.split_view.set_show_sidebar(is_active)

    def on_new_tts(self, button):
        """Handle new TTS button"""
        self.text_entry.set_text("")
        self.text_entry.grab_focus()
        
        # Clear selection
        self.history_listbox.select_row(None)
        
        # Show welcome message
        self.show_welcome_content()

    def show_welcome_content(self):
        """Show welcome content in main area"""
        # Clear current content
        child = self.content_area.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.content_area.remove(child)
            child = next_child
        
        # Welcome message
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        welcome_box.set_halign(Gtk.Align.CENTER)
        welcome_box.set_valign(Gtk.Align.CENTER)
        welcome_box.set_vexpand(True)
        
        welcome_title = Gtk.Label(label="Ready to Generate Speech")
        welcome_title.add_css_class("title-1")
        welcome_box.append(welcome_title)
        
        welcome_subtitle = Gtk.Label(label="Enter text in the field below")
        welcome_subtitle.add_css_class("title-4")
        welcome_subtitle.add_css_class("dim-label")
        welcome_box.append(welcome_subtitle)
        
        self.content_area.append(welcome_box)

    def on_generate_speech(self, widget):
        """Handle speech generation"""
        text = self.text_entry.get_text().strip()
        if not text:
            return
        
        # Update status
        self.status_indicator.set_text("Generating...")
        
        # Add to history
        self.add_history_item(text)
        
        # Simulate processing
        self.status_indicator.set_text("Ready")
        
        # Clear input
        self.text_entry.set_text("")
        
        print(f"Generating TTS for: {text}")

    def play_tts(self, item):
        """Play TTS for item"""
        self.status_indicator.set_text("Playing...")
        print(f"Playing TTS: {item.text[:30]}...")

    def regenerate_tts(self, item):
        """Regenerate TTS for item"""
        self.status_indicator.set_text("Regenerating...")
        print(f"Regenerating TTS: {item.text[:30]}...")

    def show_settings_popover(self, button):
        """Show settings popover"""
        print("Show settings popover")

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == '__main__':
    main()
