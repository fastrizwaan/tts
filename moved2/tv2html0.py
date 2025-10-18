#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango
import html
import re
from html.parser import HTMLParser

class HTMLToTextParser(HTMLParser):
    def __init__(self, text_buffer):
        super().__init__()
        self.text_buffer = text_buffer
        self.tag_stack = []
        self.current_text = ""
    
    def create_text_tags(self):
        """Create text tags for different HTML elements"""
        tag_table = self.text_buffer.get_tag_table()
        
        # Check if tags already exist before creating them
        if tag_table.lookup("bold"):
            return  # Tags already created
        
        # Bold tags
        bold_tag = Gtk.TextTag.new("bold")
        bold_tag.set_property("weight", Pango.Weight.BOLD)
        tag_table.add(bold_tag)
        
        # Italic tags
        italic_tag = Gtk.TextTag.new("italic")
        italic_tag.set_property("style", Pango.Style.ITALIC)
        tag_table.add(italic_tag)
        
        # Underline tags
        underline_tag = Gtk.TextTag.new("underline")
        underline_tag.set_property("underline", Pango.Underline.SINGLE)
        tag_table.add(underline_tag)
        
        # Strikethrough tags
        strike_tag = Gtk.TextTag.new("strikethrough")
        strike_tag.set_property("strikethrough", True)
        tag_table.add(strike_tag)
        
        # Header tags (H1-H6)
        for i in range(1, 7):
            h_tag = Gtk.TextTag.new(f"h{i}")
            h_tag.set_property("weight", Pango.Weight.BOLD)
            # Scale font size based on header level
            scale = 2.0 - (i - 1) * 0.2  # H1=2.0, H2=1.8, H3=1.6, etc.
            h_tag.set_property("scale", scale)
            tag_table.add(h_tag)
        
        # Paragraph spacing
        p_tag = Gtk.TextTag.new("paragraph")
        p_tag.set_property("pixels-below-lines", 12)
        tag_table.add(p_tag)
        
        # Code/pre formatting
        code_tag = Gtk.TextTag.new("code")
        code_tag.set_property("family", "monospace")
        code_tag.set_property("background", "#f5f5f5")
        tag_table.add(code_tag)
    
    def handle_starttag(self, tag, attrs):
        """Handle opening HTML tags"""
        tag_lower = tag.lower()
        
        # Map HTML tags to TextTag names
        tag_mapping = {
            'b': 'bold',
            'strong': 'bold',
            'i': 'italic',
            'em': 'italic',
            'u': 'underline',
            'strike': 'strikethrough',
            's': 'strikethrough',
            'del': 'strikethrough',
            'code': 'code',
            'pre': 'code',
            'p': 'paragraph'
        }
        
        # Handle headers
        if tag_lower in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            self.tag_stack.append(tag_lower)
        elif tag_lower in tag_mapping:
            self.tag_stack.append(tag_mapping[tag_lower])
        elif tag_lower in ['br']:
            self.insert_current_text()
            self.insert_text('\n')
    
    def handle_endtag(self, tag):
        """Handle closing HTML tags"""
        tag_lower = tag.lower()
        
        tag_mapping = {
            'b': 'bold',
            'strong': 'bold',
            'i': 'italic',
            'em': 'italic',
            'u': 'underline',
            'strike': 'strikethrough',
            's': 'strikethrough',
            'del': 'strikethrough',
            'code': 'code',
            'pre': 'code',
            'p': 'paragraph'
        }
        
        # Insert current text before closing tag
        self.insert_current_text()
        
        # Remove tag from stack
        if tag_lower in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            if tag_lower in self.tag_stack:
                self.tag_stack.remove(tag_lower)
        elif tag_lower in tag_mapping:
            mapped_tag = tag_mapping[tag_lower]
            if mapped_tag in self.tag_stack:
                self.tag_stack.remove(mapped_tag)
        
        # Add spacing after paragraphs and headers
        if tag_lower in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            self.insert_text('\n\n')
    
    def handle_data(self, data):
        """Handle text data between tags"""
        # Clean up whitespace but preserve intentional spacing
        cleaned_data = ' '.join(data.split())
        if cleaned_data:
            self.current_text += cleaned_data
    
    def insert_current_text(self):
        """Insert accumulated text with current formatting"""
        if self.current_text.strip():
            self.insert_text(self.current_text)
            self.current_text = ""
    
    def insert_text(self, text):
        """Insert text with current tags applied"""
        if not text:
            return
            
        end_iter = self.text_buffer.get_end_iter()
        
        if self.tag_stack:
            # Get tag objects
            tag_table = self.text_buffer.get_tag_table()
            tags = []
            for tag_name in self.tag_stack:
                tag = tag_table.lookup(tag_name)
                if tag:
                    tags.append(tag)
            
            # Insert text with tags
            if tags:
                self.text_buffer.insert_with_tags(end_iter, text, *tags)
            else:
                self.text_buffer.insert(end_iter, text)
        else:
            self.text_buffer.insert(end_iter, text)
    
    def close(self):
        """Finish parsing"""
        self.insert_current_text()

class HTMLTextViewApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.htmltextview")
        self.connect("activate", self.on_activate)
    
    def on_activate(self, app):
        # Create main window
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title("HTML TextView")
        self.window.set_default_size(800, 600)
        
        # Create toolbar view to contain header bar and content
        toolbar_view = Adw.ToolbarView()
        
        # Create header bar
        header = Adw.HeaderBar()
        
        # Open file button
        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open HTML File")
        open_button.connect("clicked", self.on_open_file)
        header.pack_start(open_button)
        
        # Paste HTML button
        paste_button = Gtk.Button()
        paste_button.set_icon_name("edit-paste-symbolic")
        paste_button.set_tooltip_text("Paste HTML from Clipboard")
        paste_button.connect("clicked", self.on_paste_html)
        header.pack_start(paste_button)
        
        # Clear button
        clear_button = Gtk.Button()
        clear_button.set_icon_name("edit-clear-symbolic")
        clear_button.set_tooltip_text("Clear Text")
        clear_button.connect("clicked", self.on_clear_text)
        header.pack_end(clear_button)
        
        # Add header bar to toolbar view
        toolbar_view.add_top_bar(header)
        
        # Create main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Create scrolled window for textview
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        
        # Create textview
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_left_margin(12)
        self.textview.set_right_margin(12)
        self.textview.set_top_margin(12)
        self.textview.set_bottom_margin(12)
        
        self.text_buffer = self.textview.get_buffer()
        
        # Create text tags once during initialization
        self.create_text_tags()
        
        scrolled.set_child(self.textview)
        main_box.append(scrolled)
        
        # Status bar
        self.status_bar = Adw.StatusPage()
        self.status_bar.set_title("Ready")
        self.status_bar.set_description("Open an HTML file or paste HTML content")
        self.status_bar.set_visible(True)
        
        # Initially show status instead of textview
        main_box.remove(scrolled)
        main_box.append(self.status_bar)
        self.scrolled = scrolled
        self.main_box = main_box
        
        # Set main content in toolbar view
        toolbar_view.set_content(main_box)
        
        # Set toolbar view as window content
        self.window.set_content(toolbar_view)
        
        # Present the window
        self.window.present()
        
    def create_text_tags(self):
        """Create text tags for different HTML elements"""
        tag_table = self.text_buffer.get_tag_table()
        
        # Check if tags already exist before creating them
        if tag_table.lookup("bold"):
            return  # Tags already created
        
        # Bold tags
        bold_tag = Gtk.TextTag.new("bold")
        bold_tag.set_property("weight", Pango.Weight.BOLD)
        tag_table.add(bold_tag)
        
        # Italic tags
        italic_tag = Gtk.TextTag.new("italic")
        italic_tag.set_property("style", Pango.Style.ITALIC)
        tag_table.add(italic_tag)
        
        # Underline tags
        underline_tag = Gtk.TextTag.new("underline")
        underline_tag.set_property("underline", Pango.Underline.SINGLE)
        tag_table.add(underline_tag)
        
        # Strikethrough tags
        strike_tag = Gtk.TextTag.new("strikethrough")
        strike_tag.set_property("strikethrough", True)
        tag_table.add(strike_tag)
        
        # Header tags (H1-H6)
        for i in range(1, 7):
            h_tag = Gtk.TextTag.new(f"h{i}")
            h_tag.set_property("weight", Pango.Weight.BOLD)
            # Scale font size based on header level
            scale = 2.0 - (i - 1) * 0.2  # H1=2.0, H2=1.8, H3=1.6, etc.
            h_tag.set_property("scale", scale)
            tag_table.add(h_tag)
        
        # Paragraph spacing
        p_tag = Gtk.TextTag.new("paragraph")
        p_tag.set_property("pixels-below-lines", 12)
        tag_table.add(p_tag)
        
        # Code/pre formatting
        code_tag = Gtk.TextTag.new("code")
        code_tag.set_property("family", "monospace")
        code_tag.set_property("background", "#f5f5f5")
        tag_table.add(code_tag)
    
    def show_textview(self):
        """Switch from status page to textview"""
        if self.status_bar.get_visible():
            self.main_box.remove(self.status_bar)
            self.main_box.append(self.scrolled)
            self.status_bar.set_visible(False)
    
    def show_status(self, title, description):
        """Switch to status page with message"""
        if not self.status_bar.get_visible():
            self.main_box.remove(self.scrolled)
            self.main_box.append(self.status_bar)
            self.status_bar.set_visible(True)
        self.status_bar.set_title(title)
        self.status_bar.set_description(description)
    
    def on_open_file(self, button):
        """Handle open file button click"""
        dialog = Gtk.FileChooserNative.new(
            title="Open HTML File",
            parent=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Open",
            cancel_label="Cancel"
        )
        
        # Add HTML file filter
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML Files")
        html_filter.add_mime_type("text/html")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        dialog.add_filter(html_filter)
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Files")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)
        
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()
    
    def on_file_dialog_response(self, dialog, response):
        """Handle file dialog response"""
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                self.load_html_file(file)
        dialog.destroy()
    
    def load_html_file(self, file):
        """Load and parse HTML file"""
        try:
            # Read file content
            file.load_contents_async(None, self.on_file_loaded, None)
        except Exception as e:
            self.show_error(f"Error opening file: {str(e)}")
    
    def on_file_loaded(self, file, result, user_data):
        """Handle file loading completion"""
        try:
            success, contents, etag = file.load_contents_finish(result)
            if success:
                # Decode content
                html_content = contents.decode('utf-8', errors='replace')
                self.parse_and_display_html(html_content)
            else:
                self.show_error("Failed to load file")
        except Exception as e:
            self.show_error(f"Error reading file: {str(e)}")
    
    def on_paste_html(self, button):
        """Handle paste HTML button click"""
        clipboard = Gdk.Display.get_default().get_clipboard()
        
        # Simple approach: always read as text first and detect HTML content
        clipboard.read_text_async(None, self.on_clipboard_content, None)
    
    def on_clipboard_content(self, clipboard, result, user_data):
        """Handle clipboard content - detect if HTML or plain text"""
        try:
            text = clipboard.read_text_finish(result)
            if text:
                # Check if content looks like HTML
                if self.is_html_content(text):
                    print(f"Detected HTML content, length: {len(text)}")  # Debug
                    self.parse_and_display_html(text)
                else:
                    print("Detected plain text content")  # Debug
                    # Insert as plain text
                    self.text_buffer.set_text(text)
                    self.show_textview()
            else:
                self.show_error("No content in clipboard")
        except Exception as e:
            self.show_error(f"Error reading clipboard: {str(e)}")
    
    def is_html_content(self, text):
        """Detect if text content is HTML"""
        if not text or len(text.strip()) < 3:
            return False
            
        # More comprehensive HTML detection
        html_patterns = [
            r'<html[^>]*>',
            r'<head[^>]*>',
            r'<body[^>]*>',
            r'<div[^>]*>',
            r'<p[^>]*>',
            r'<h[1-6][^>]*>',
            r'<b[^>]*>',
            r'<i[^>]*>',
            r'<strong[^>]*>',
            r'<em[^>]*>',
            r'<span[^>]*>',
            r'<a[^>]*>',
            r'<img[^>]*>',
            r'<br\s*/?>',
            r'<hr\s*/?>'
        ]
        
        # Count HTML tag matches
        tag_count = 0
        for pattern in html_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            tag_count += len(matches)
        
        # If we find multiple tags or specific HTML structure tags, it's likely HTML
        if tag_count >= 2:
            return True
        
        # Check for common HTML entities
        html_entities = ['&amp;', '&lt;', '&gt;', '&nbsp;', '&quot;', '&#']
        entity_count = sum(1 for entity in html_entities if entity in text)
        
        # If we have HTML tags and entities, definitely HTML
        if tag_count >= 1 and entity_count >= 1:
            return True
            
        # Check for DOCTYPE declaration
        if re.search(r'<!DOCTYPE\s+html', text, re.IGNORECASE):
            return True
            
        return False
    
    def parse_and_display_html(self, html_content):
        """Parse HTML content and display in TextView"""
        try:
            # Extract body content only, ignoring head/title
            body_content = self.extract_body_content(html_content)
            
            # Clear existing content
            self.text_buffer.set_text("")
            
            # Parse HTML (tags are already created during initialization)
            parser = HTMLToTextParser(self.text_buffer)
            parser.feed(body_content)
            parser.close()
            
            # Show textview
            self.show_textview()
            
            # Scroll to top
            start_iter = self.text_buffer.get_start_iter()
            mark = self.text_buffer.get_insert()
            self.text_buffer.place_cursor(start_iter)
            self.textview.scroll_mark_onscreen(mark)
            
        except Exception as e:
            self.show_error(f"Error parsing HTML: {str(e)}")
    
    def extract_body_content(self, html_content):
        """Extract content from body tag, ignoring head/title"""
        # Try to find body content
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
        if body_match:
            return body_match.group(1)
        
        # If no body tag found, remove head section if it exists
        head_removed = re.sub(r'<head[^>]*>.*?</head>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove html and doctype tags
        cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', head_removed, flags=re.IGNORECASE)
        cleaned = re.sub(r'</?html[^>]*>', '', cleaned, flags=re.IGNORECASE)
        
        return cleaned.strip()
    
    def on_clear_text(self, button):
        """Clear the text buffer"""
        self.text_buffer.set_text("")
        self.show_status("Ready", "Open an HTML file or paste HTML content")
    
    def show_error(self, message):
        """Show error message"""
        self.show_status("Error", message)

def main():
    app = HTMLTextViewApp()
    return app.run()

if __name__ == "__main__":
    main()
