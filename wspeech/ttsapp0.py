#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio, Pango
import subprocess
import threading
import time
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.example.TTSApp')
        self.connect('activate', self.on_activate)
        
    def on_activate(self, app):
        self.win = TTSWindow(application=app)
        self.win.present()

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.set_title("Text to Speech Reader")
        self.set_default_size(900, 700)
        
        # State variables
        self.is_speaking = False
        self.current_text = ""
        self.lines = []
        self.line_positions = []  # Store (start, end) positions of each line
        self.current_line_index = 0
        self.current_sentence_in_line = 0
        
        self.setup_ui()
        
    def setup_ui(self):
        # Create main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)
        
        # Header bar
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Adw.WindowTitle(title="Text to Speech Reader"))
        
        # Add file menu button to header bar
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Menu")
        header_bar.pack_end(menu_button)
        
        # Create menu
        menu = Gio.Menu()
        menu.append("Open File", "win.open_file")
        menu.append("Clear Text", "win.clear_text")
        
        # Create action group
        action_group = Gio.SimpleActionGroup()
        
        # Open file action
        open_action = Gio.SimpleAction.new("open_file", None)
        open_action.connect("activate", self.on_open_file)
        action_group.add_action(open_action)
        
        # Clear text action
        clear_action = Gio.SimpleAction.new("clear_text", None)
        clear_action.connect("activate", self.on_clear_text)
        action_group.add_action(clear_action)
        
        self.insert_action_group("win", action_group)
        menu_button.set_menu_model(menu)
        
        main_box.append(header_bar)
        
        # Create toolbar
        toolbar = self.create_toolbar()
        main_box.append(toolbar)
        
        # Create main content area
        content_area = self.create_content_area()
        main_box.append(content_area)
        
        # Status bar
        self.status_label = Gtk.Label()
        self.status_label.set_text("Ready - Load a file or enter text to begin")
        self.status_label.set_margin_start(12)
        self.status_label.set_margin_end(12)
        self.status_label.set_margin_top(6)
        self.status_label.set_margin_bottom(6)
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)
        
    def create_toolbar(self):
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)
        
        # File operations
        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open File (TXT, EPUB)")
        open_button.connect("clicked", lambda x: self.on_open_file(None, None))
        toolbar.append(open_button)
        
        # Separator
        separator1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        toolbar.append(separator1)
        
        # Play/Pause button
        self.play_button = Gtk.Button()
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Play")
        self.play_button.connect("clicked", self.on_play_pause_clicked)
        self.play_button.add_css_class("suggested-action")
        toolbar.append(self.play_button)
        
        # Stop button
        self.stop_button = Gtk.Button()
        self.stop_button.set_icon_name("media-playback-stop-symbolic")
        self.stop_button.set_tooltip_text("Stop")
        self.stop_button.connect("clicked", self.on_stop_clicked)
        self.stop_button.set_sensitive(False)
        toolbar.append(self.stop_button)
        
        # Separator
        separator2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        toolbar.append(separator2)
        
        # Speed control
        speed_label = Gtk.Label(label="Speed:")
        toolbar.append(speed_label)
        
        self.speed_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 50, 200, 10
        )
        self.speed_scale.set_value(100)
        self.speed_scale.set_size_request(100, -1)
        self.speed_scale.set_tooltip_text("Speech speed (50-200%)")
        toolbar.append(self.speed_scale)
        
        # Voice selection
        voice_label = Gtk.Label(label="Voice:")
        toolbar.append(voice_label)
        
        self.voice_combo = Gtk.ComboBoxText()
        self.populate_voices()
        toolbar.append(self.voice_combo)
        
        return toolbar
        
    def populate_voices(self):
        """Populate voice selection combo box"""
        try:
            result = subprocess.run(['spd-say', '-L'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                voices = []
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        voices.append(line.strip())
                
                for voice in voices:
                    self.voice_combo.append_text(voice)
                
                if voices:
                    self.voice_combo.set_active(0)
            else:
                default_voices = ["default", "male1", "female1", "male2", "female2"]
                for voice in default_voices:
                    self.voice_combo.append_text(voice)
                self.voice_combo.set_active(0)
                    
        except FileNotFoundError:
            self.voice_combo.append_text("default")
            self.voice_combo.set_active(0)
            
    def create_content_area(self):
        # Scrolled window for text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_bottom(12)
        
        # Text view
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_margin_start(12)
        self.text_view.set_margin_end(12)
        self.text_view.set_margin_top(12)
        self.text_view.set_margin_bottom(12)
        
        # Set up text buffer
        self.text_buffer = self.text_view.get_buffer()
        
        # Connect to text buffer changes to auto-update lines
        self.text_buffer.connect("changed", self.on_text_changed)
        
        # Set up right-click context menu
        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right mouse button
        gesture.connect("pressed", self.on_right_click)
        self.text_view.add_controller(gesture)
        
        # Create text tags for highlighting
        self.line_highlight_tag = self.text_buffer.create_tag(
            "line_highlight",
            background="#3584e4",  # Blue background
            foreground="white",
            weight=Pango.Weight.BOLD
        )
        
        # Add some sample text
        sample_text = """Welcome to the Text to Speech Reader! This program reads text aloud with sentence-by-sentence highlighting.

You can load TXT files or EPUB books using the Open File button. The current sentence being read will be highlighted in blue. Use the speed control to adjust reading pace.

Select different voices from the dropdown menu. Right-click anywhere in the text to start reading from that position.

Try loading your favorite book or document to get started!"""

        self.text_buffer.set_text(sample_text)
        self.prepare_text_for_reading()
        
        scrolled.set_child(self.text_view)
        return scrolled
    
    def on_right_click(self, gesture, n_press, x, y):
        """Handle right-click to show context menu"""
        # Get the position in the text
        buffer_x, buffer_y = self.text_view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, int(x), int(y)
        )
        
        # Get the iterator at the clicked position
        click_result = self.text_view.get_iter_at_location(buffer_x, buffer_y)
        if click_result[0]:  # If successful
            iter_pos = click_result[1]
            click_offset = iter_pos.get_offset()
            
            # Find which sentence this position belongs to
            target_sentence = 0
            for i, (start, end) in enumerate(self.line_positions):
                if start <= click_offset <= end:
                    target_sentence = i
                    break
                elif click_offset < start:
                    target_sentence = max(0, i - 1)
                    break
            
            # Create simple popover with button
            popover = Gtk.Popover()
            popover.set_parent(self.text_view)
            
            # Create button for "Speak from here"
            button = Gtk.Button(label="Speak from here")
            button.connect("clicked", lambda b: self.on_speak_from_here_clicked(popover, target_sentence))
            button.set_margin_start(6)
            button.set_margin_end(6)
            button.set_margin_top(6)
            button.set_margin_bottom(6)
            
            popover.set_child(button)
            
            # Position the popover at the click location
            point = Gdk.Rectangle()
            point.x, point.y = int(x), int(y)
            point.width = point.height = 1
            popover.set_pointing_to(point)
            popover.popup()
    
    def on_speak_from_here_clicked(self, popover, sentence_index):
        """Handle speak from here button click"""
        popover.popdown()
        self.start_speech_from_position(sentence_index)
    
    def start_speech_from_position(self, sentence_index):
        """Start speech from a specific sentence position"""
        if not self.lines:
            self.prepare_text_for_reading()
        
        if sentence_index >= len(self.lines):
            sentence_index = len(self.lines) - 1
        
        self.current_line_index = sentence_index
        self.start_speech()
    
    def on_text_changed(self, text_buffer):
        """Called when text buffer content changes"""
        # Stop current speech if playing
        if self.is_speaking:
            self.stop_speech()
        
        # Re-prepare text for reading
        self.prepare_text_for_reading()
        
        # Update status
        if self.lines:
            self.status_label.set_text(f"Text updated - {len(self.lines)} sentences ready to read")
        else:
            self.status_label.set_text("Ready - Enter text or load a file to begin")
    
    def on_open_file(self, action, param):
        """Open file dialog to load TXT or EPUB files"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Open Text or EPUB File")
        
        # Create file filters
        txt_filter = Gtk.FileFilter()
        txt_filter.set_name("Text Files")
        txt_filter.add_pattern("*.txt")
        
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB Books")
        epub_filter.add_pattern("*.epub")
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Supported Files")
        all_filter.add_pattern("*.txt")
        all_filter.add_pattern("*.epub")
        
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        filter_list.append(all_filter)
        filter_list.append(txt_filter)
        filter_list.append(epub_filter)
        
        dialog.set_filters(filter_list)
        dialog.set_default_filter(all_filter)
        
        dialog.open(self, None, self.on_file_selected)
    
    def on_file_selected(self, dialog, result):
        """Handle file selection"""
        try:
            file = dialog.open_finish(result)
            if file:
                file_path = file.get_path()
                self.load_file(file_path)
        except Exception as e:
            self.show_error_dialog(f"Could not open file: {str(e)}")
    
    def load_file(self, file_path):
        """Load and display file content"""
        try:
            if file_path.lower().endswith('.epub'):
                content = self.extract_epub_text(file_path)
            elif file_path.lower().endswith('.txt'):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            else:
                raise ValueError("Unsupported file format")
            
            if content.strip():
                self.text_buffer.set_text(content)
                self.prepare_text_for_reading()
                filename = Path(file_path).name
                self.status_label.set_text(f"Loaded: {filename}")
            else:
                self.show_error_dialog("File appears to be empty or could not be read")
                
        except Exception as e:
            self.show_error_dialog(f"Error loading file: {str(e)}")
    
    def extract_epub_text(self, epub_path):
        """Extract text content from EPUB file"""
        try:
            with zipfile.ZipFile(epub_path, 'r') as zip_file:
                # Find content.opf or similar manifest file
                content_opf = None
                for filename in zip_file.namelist():
                    if filename.endswith('.opf') or 'content.opf' in filename:
                        content_opf = filename
                        break
                
                if not content_opf:
                    # Try common locations
                    possible_locations = ['OEBPS/content.opf', 'OPS/content.opf', 'content.opf']
                    for loc in possible_locations:
                        if loc in zip_file.namelist():
                            content_opf = loc
                            break
                
                if not content_opf:
                    # Fallback: just read all HTML/XHTML files
                    text_content = []
                    for filename in zip_file.namelist():
                        if filename.endswith(('.html', '.xhtml', '.htm')):
                            try:
                                content = zip_file.read(filename).decode('utf-8')
                                text = self.extract_text_from_html(content)
                                if text.strip():
                                    text_content.append(text)
                            except:
                                continue
                    return '\n\n'.join(text_content)
                
                # Parse content.opf to find reading order
                opf_content = zip_file.read(content_opf).decode('utf-8')
                root = ET.fromstring(opf_content)
                
                # Find namespace
                ns = {'opf': 'http://www.idpf.org/2007/opf'}
                if root.tag.startswith('{'):
                    ns_uri = root.tag.split('}')[0][1:]
                    ns = {'opf': ns_uri}
                
                # Get spine order
                spine_items = []
                spine = root.find('.//opf:spine', ns)
                if spine is not None:
                    for itemref in spine.findall('opf:itemref', ns):
                        idref = itemref.get('idref')
                        if idref:
                            spine_items.append(idref)
                
                # Get manifest items
                manifest_items = {}
                manifest = root.find('.//opf:manifest', ns)
                if manifest is not None:
                    for item in manifest.findall('opf:item', ns):
                        item_id = item.get('id')
                        href = item.get('href')
                        if item_id and href:
                            manifest_items[item_id] = href
                
                # Extract text in reading order
                text_content = []
                base_path = '/'.join(content_opf.split('/')[:-1])
                if base_path:
                    base_path += '/'
                
                for spine_id in spine_items:
                    if spine_id in manifest_items:
                        file_path = base_path + manifest_items[spine_id]
                        try:
                            content = zip_file.read(file_path).decode('utf-8')
                            text = self.extract_text_from_html(content)
                            if text.strip():
                                text_content.append(text)
                        except:
                            continue
                
                return '\n\n'.join(text_content)
                
        except Exception as e:
            raise Exception(f"Could not extract EPUB content: {str(e)}")
    
    def extract_text_from_html(self, html_content):
        """Extract plain text from HTML content"""
        # Simple HTML tag removal - could be improved with proper HTML parser
        import re
        
        # Remove script and style elements
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Convert common HTML entities
        html_content = html_content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        html_content = html_content.replace('&quot;', '"').replace('&apos;', "'").replace('&#39;', "'")
        html_content = html_content.replace('&nbsp;', ' ').replace('&#160;', ' ')
        
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', html_content)
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        return text.strip()
    
    def show_error_dialog(self, message):
        """Show error dialog"""
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "OK")
        dialog.present()
    
    def on_clear_text(self, action, param):
        """Clear the text buffer"""
        self.text_buffer.set_text("")
        self.lines = []
        self.line_positions = []
        self.status_label.set_text("Text cleared - Load a file or enter text to begin")
    
    def prepare_text_for_reading(self):
        """Prepare text by splitting into actual visual lines"""
        start_iter = self.text_buffer.get_start_iter()
        end_iter = self.text_buffer.get_end_iter()
        self.current_text = self.text_buffer.get_text(start_iter, end_iter, False)
        
        if not self.current_text.strip():
            self.lines = []
            self.line_positions = []
            return
        
        # Split text into sentences instead of lines for better granularity
        self.lines = []
        self.line_positions = []
        
        # Use regex to split into sentences
        sentence_pattern = r'(?<=[.!?])\s+'
        sentences = re.split(sentence_pattern, self.current_text)
        
        current_offset = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence:
                # Find the sentence position in the original text
                sentence_start = self.current_text.find(sentence, current_offset)
                if sentence_start != -1:
                    sentence_end = sentence_start + len(sentence)
                    self.lines.append(sentence)
                    self.line_positions.append((sentence_start, sentence_end))
                    current_offset = sentence_end
        
        self.current_line_index = 0
        print(f"Prepared {len(self.lines)} sentences for reading")  # Debug info
        
    def on_text_changed(self, text_buffer):
        """Called when text buffer content changes"""
        # Stop current speech if playing
        if self.is_speaking:
            self.stop_speech()
        
        # Re-prepare text for reading
        self.prepare_text_for_reading()
        
        # Update status
        if self.lines:
            self.status_label.set_text(f"Text updated - {len(self.lines)} lines ready to read")
        else:
            self.status_label.set_text("Ready - Enter text or load a file to begin")
        
    def on_play_pause_clicked(self, button):
        if not self.is_speaking:
            self.start_speech()
        else:
            self.pause_speech()
    
    def pause_speech(self):
        """Pause the current speech (maintains position)"""
        self.is_speaking = False
        
        # Kill any running spd-say processes
        try:
            subprocess.run(['pkill', '-f', 'spd-say'], timeout=2)
        except:
            pass
        
        # Update UI but don't reset position
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Resume")
        self.stop_button.set_sensitive(True)
        self.status_label.set_text(f"Paused at sentence {self.current_line_index + 1} of {len(self.lines)}")
            
    def on_stop_clicked(self, button):
        self.stop_speech()
        
    def start_speech(self):
        # Always re-prepare text before starting (in case it was modified)
        self.prepare_text_for_reading()
            
        if not self.lines:
            self.status_label.set_text("No text to read - please load a file or enter text")
            return
            
        # Clear any existing highlights
        self.clear_highlights()
        
        # Prepare for speech
        self.is_speaking = True
        self.play_button.set_icon_name("media-playback-pause-symbolic")
        self.play_button.set_tooltip_text("Pause")
        self.stop_button.set_sensitive(True)
        
        self.status_label.set_text("Reading...")
        
        # Start from current line or beginning
        if self.current_line_index >= len(self.lines):
            self.current_line_index = 0
        
        # Start speech in a separate thread
        thread = threading.Thread(target=self.speak_lines)
        thread.daemon = True
        thread.start()
        
    def speak_lines(self):
        """Speak lines one by one with highlighting"""
        try:
            speed = int(self.speed_scale.get_value())
            voice_text = self.voice_combo.get_active_text()
            
            for i in range(self.current_line_index, len(self.lines)):
                if not self.is_speaking:
                    break
                
                self.current_line_index = i
                line = self.lines[i]
                
                # Highlight current line
                GLib.idle_add(self.highlight_current_line)
                
                # Split line into sentences for natural speech
                sentences = re.split(r'(?<=[.!?])\s+', line)
                sentences = [s.strip() for s in sentences if s.strip()]
                
                if not sentences:
                    sentences = [line]
                
                # Speak each sentence in the line
                for sentence in sentences:
                    if not self.is_speaking:
                        break
                        
                    # Build spd-say command
                    cmd = ['spd-say', '-w']  # -w waits for completion
                    cmd.extend(['-r', str(speed)])
                    
                    if voice_text and voice_text != "default":
                        cmd.extend(['-o', voice_text])
                    
                    # Clean the sentence
                    clean_sentence = sentence.replace('"', "'").replace('•', '*')
                    cmd.append(clean_sentence)
                    
                    try:
                        result = subprocess.run(cmd, timeout=30, capture_output=True)
                        if result.returncode != 0:
                            print(f"spd-say error: {result.stderr.decode() if result.stderr else 'Unknown error'}")
                    except Exception as e:
                        print(f"Error speaking sentence: {e}")
                        continue
                    
                    # Small pause between sentences in same line
                    if self.is_speaking:
                        time.sleep(0.1)
                
                # Longer pause between lines
                if self.is_speaking and i < len(self.lines) - 1:
                    time.sleep(0.3)
            
            # Reading finished
            GLib.idle_add(self.on_speech_finished)
            
        except Exception as e:
            print(f"Error during speech: {e}")
            GLib.idle_add(self.on_speech_finished)
    
    def on_text_changed(self, text_buffer):
        """Called when text buffer content changes"""
        # Stop current speech if playing
        if self.is_speaking:
            self.stop_speech()
        
        # Re-prepare text for reading
        self.prepare_text_for_reading()
        
        # Update status
        if self.lines:
            self.status_label.set_text(f"Text updated - {len(self.lines)} lines ready to read")
        else:
            self.status_label.set_text("Ready - Enter text or load a file to begin")
    
    def highlight_current_line(self):
        """Highlight the current line being read"""
        if self.current_line_index >= len(self.line_positions):
            return
        
        # Clear previous highlights
        self.clear_highlights()
        
        # Get line position from stored positions
        start_offset, end_offset = self.line_positions[self.current_line_index]
        
        # Get iterators for the exact line
        start_iter = self.text_buffer.get_iter_at_offset(start_offset)
        end_iter = self.text_buffer.get_iter_at_offset(end_offset)
        
        # Apply highlight to the exact line
        self.text_buffer.apply_tag(self.line_highlight_tag, start_iter, end_iter)
        
        # Improved scrolling to ensure line is visible
        # Get the text view's visible rectangle
        visible_rect = self.text_view.get_visible_rect()
        
        # Get the line's rectangle
        line_rect = self.text_view.get_iter_location(start_iter)
        
        # Check if line is outside visible area
        if (line_rect.y < visible_rect.y or 
            line_rect.y + line_rect.height > visible_rect.y + visible_rect.height):
            
            # Scroll to show the line with some margin
            mark = self.text_buffer.create_mark(None, start_iter, False)
            self.text_view.scroll_mark_onscreen(mark)
            
            # Alternative: More precise scrolling with margin
            # self.text_view.scroll_to_iter(start_iter, 0.0, True, 0.0, 0.3)
        
        return False
    
    def clear_highlights(self):
        """Clear all text highlighting"""
        start_iter = self.text_buffer.get_start_iter()
        end_iter = self.text_buffer.get_end_iter()
        self.text_buffer.remove_tag(self.line_highlight_tag, start_iter, end_iter)
    
    def stop_speech(self):
        """Stop the current speech and reset position"""
        self.is_speaking = False
        
        # Kill any running spd-say processes
        try:
            subprocess.run(['pkill', '-f', 'spd-say'], timeout=2)
        except:
            pass
        
        # Reset position and clear highlights
        self.current_line_index = 0
        self.clear_highlights()
        self.on_speech_finished()
        
    def on_speech_finished(self):
        """Called when speech is finished or stopped"""
        self.is_speaking = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Play")
        self.stop_button.set_sensitive(False)
        
        if self.current_line_index >= len(self.lines):
            self.status_label.set_text("Reading complete")
            self.current_line_index = 0  # Reset for next reading
            self.clear_highlights()
        else:
            self.status_label.set_text(f"Ready - {len(self.lines)} sentences available")

def main():
    app = TTSApplication()
    return app.run(None)

if __name__ == '__main__':
    main()
