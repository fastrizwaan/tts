#!/usr/bin/env python3
# GTK4 + libadwaita TextView with HTML/EPUB support, multi-column view, and horizontal scrolling

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango
import re
from html.parser import HTMLParser
import tempfile
import os

try:
    import ebooklib
    from ebooklib import epub
    EPUB_AVAILABLE = True
except ImportError:
    EPUB_AVAILABLE = False


# ---------------------------- HTML -> Gtk.TextBuffer parser ----------------------------
class HTMLToTextParser(HTMLParser):
    def __init__(self, text_buffer):
        super().__init__()
        self.text_buffer = text_buffer
        self.tag_stack = []
        self.current_text = ""
        self._ensure_tags()

    def _ensure_tags(self):
        tag_table = self.text_buffer.get_tag_table()
        if tag_table.lookup("bold"):
            return
        bold = Gtk.TextTag.new("bold"); bold.set_property("weight", Pango.Weight.BOLD); tag_table.add(bold)
        italic = Gtk.TextTag.new("italic"); italic.set_property("style", Pango.Style.ITALIC); tag_table.add(italic)
        underline = Gtk.TextTag.new("underline"); underline.set_property("underline", Pango.Underline.SINGLE); tag_table.add(underline)
        strike = Gtk.TextTag.new("strikethrough"); strike.set_property("strikethrough", True); tag_table.add(strike)
        for i in range(1, 7):
            h = Gtk.TextTag.new(f"h{i}")
            h.set_property("weight", Pango.Weight.BOLD)
            h.set_property("scale", 2.0 - (i - 1) * 0.2)
            tag_table.add(h)
        p = Gtk.TextTag.new("paragraph"); p.set_property("pixels-below-lines", 12); tag_table.add(p)
        code = Gtk.TextTag.new("code"); code.set_property("family", "monospace"); code.set_property("background", "#f5f5f5"); tag_table.add(code)

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        map_ = {
            'b': 'bold', 'strong': 'bold',
            'i': 'italic', 'em': 'italic',
            'u': 'underline',
            'strike': 'strikethrough', 's': 'strikethrough', 'del': 'strikethrough',
            'code': 'code', 'pre': 'code',
            'p': 'paragraph'
        }
        if t in [f'h{i}' for i in range(1, 7)]:
            self.tag_stack.append(t)
        elif t in map_:
            self.tag_stack.append(map_[t])
        elif t == 'br':
            self.insert_current_text()
            self.insert_text('\n')

    def handle_endtag(self, tag):
        t = tag.lower()
        map_ = {
            'b': 'bold', 'strong': 'bold',
            'i': 'italic', 'em': 'italic',
            'u': 'underline',
            'strike': 'strikethrough', 's': 'strikethrough', 'del': 'strikethrough',
            'code': 'code', 'pre': 'code',
            'p': 'paragraph'
        }
        self.insert_current_text()
        if t in [f'h{i}' for i in range(1, 7)]:
            if t in self.tag_stack:
                self.tag_stack.remove(t)
        elif t in map_:
            mt = map_[t]
            if mt in self.tag_stack:
                self.tag_stack.remove(mt)
        if t in ['p'] + [f'h{i}' for i in range(1, 7)]:
            self.insert_text('\n\n')

    def handle_data(self, data):
        cleaned = re.sub(r'\s+', ' ', data)
        if cleaned:
            self.current_text += cleaned

    def insert_current_text(self):
        if self.current_text.strip():
            self.insert_text(self.current_text)
            self.current_text = ""

    def insert_text(self, text):
        if not text:
            return
        end_iter = self.text_buffer.get_end_iter()
        if self.tag_stack:
            tag_table = self.text_buffer.get_tag_table()
            tags = [tag_table.lookup(n) for n in self.tag_stack if tag_table.lookup(n)]
            if tags:
                self.text_buffer.insert_with_tags(end_iter, text, *tags)
                return
        self.text_buffer.insert(end_iter, text)

    def close(self):
        self.insert_current_text()
        super().close()


# ---------------------------- Multi-column TextView ----------------------------
class MultiColumnTextView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.column_width = 300
        self.columns = []
        self.text_buffers = []
        self.set_spacing(20)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        
    def set_column_width(self, width):
        self.column_width = max(50, min(500, width))
        self.update_column_widths()
        
    def update_column_widths(self):
        """Update width of existing columns"""
        for col in self.columns:
            col.set_size_request(self.column_width, -1)
    
    def create_columns(self, num_columns=5):
        """Create column TextViews without individual scrollbars"""
        # Clear existing columns
        child = self.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child
        self.columns.clear()
        self.text_buffers.clear()
        
        # Create columns (no individual scrollbars)
        for i in range(num_columns):
            textview = Gtk.TextView()
            textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            textview.set_editable(False)
            textview.set_cursor_visible(False)
            textview.set_size_request(self.column_width, -1)
            textview.set_left_margin(12)
            textview.set_right_margin(12)
            textview.set_top_margin(12)
            textview.set_bottom_margin(12)
            
            self.append(textview)
            self.columns.append(textview)
            self.text_buffers.append(textview.get_buffer())
    
    def set_content(self, source_buffer):
        """Distribute content across columns with proper text flow and formatting"""
        if not self.columns:
            self.create_columns()
        
        # Ensure all column buffers have the same tags
        for buf in self.text_buffers:
            self._ensure_tags_in_buffer(buf)
            
        # Get all text
        start = source_buffer.get_start_iter()
        end = source_buffer.get_end_iter()
        full_text = source_buffer.get_text(start, end, True)
        
        if not full_text:
            return
        
        # Split by paragraphs for natural flow
        paragraphs = full_text.split('\n\n')
        col_count = len(self.columns)
        paras_per_col = max(1, len(paragraphs) // col_count)
        
        # Distribute paragraphs across columns
        for i, col_buffer in enumerate(self.text_buffers):
            col_buffer.set_text("")
            start_idx = i * paras_per_col
            
            # Last column gets remaining paragraphs
            if i == col_count - 1:
                end_idx = len(paragraphs)
            else:
                end_idx = start_idx + paras_per_col
            
            # Get paragraphs for this column
            col_paragraphs = paragraphs[start_idx:end_idx]
            
            if col_paragraphs:
                # Insert paragraphs with formatting preserved from source
                for j, para in enumerate(col_paragraphs):
                    if j > 0:
                        col_buffer.insert(col_buffer.get_end_iter(), '\n\n')
                    
                    # Find this paragraph in source and copy with tags
                    self._copy_paragraph_with_formatting(source_buffer, col_buffer, para)
    
    def _ensure_tags_in_buffer(self, target_buffer):
        """Ensure all formatting tags exist in buffer"""
        tag_table = target_buffer.get_tag_table()
        if tag_table.lookup("bold"):
            return
            
        bold = Gtk.TextTag.new("bold"); bold.set_property("weight", Pango.Weight.BOLD); tag_table.add(bold)
        italic = Gtk.TextTag.new("italic"); italic.set_property("style", Pango.Style.ITALIC); tag_table.add(italic)
        underline = Gtk.TextTag.new("underline"); underline.set_property("underline", Pango.Underline.SINGLE); tag_table.add(underline)
        strike = Gtk.TextTag.new("strikethrough"); strike.set_property("strikethrough", True); tag_table.add(strike)
        for i in range(1, 7):
            h = Gtk.TextTag.new(f"h{i}")
            h.set_property("weight", Pango.Weight.BOLD)
            h.set_property("scale", 2.0 - (i - 1) * 0.2)
            tag_table.add(h)
        p = Gtk.TextTag.new("paragraph"); p.set_property("pixels-below-lines", 12); tag_table.add(p)
        code = Gtk.TextTag.new("code"); code.set_property("family", "monospace"); code.set_property("background", "#f5f5f5"); tag_table.add(code)
    
    def _copy_paragraph_with_formatting(self, source_buffer, target_buffer, para_text):
        """Copy a paragraph from source to target with formatting"""
        # For now, simplified - just insert as plain text
        # Full implementation would iterate through source buffer finding matching text
        # and copying tags, but that's complex - this gives us the basic layout
        target_buffer.insert(target_buffer.get_end_iter(), para_text)


# ---------------------------- App ----------------------------
class HTMLTextViewApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.htmlepubviewer")
        self.connect("activate", self.on_activate)
        self.multi_column_mode = False

    # ---------- UI ----------
    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title("HTML/EPUB Viewer")
        self.window.set_default_size(1200, 700)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        # Open button
        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.set_tooltip_text("Open HTML/EPUB File")
        open_button.connect("clicked", self.on_open_file)
        header.pack_start(open_button)

        # Paste button
        paste_button = Gtk.Button(icon_name="edit-paste-symbolic")
        paste_button.set_tooltip_text("Paste HTML from Clipboard")
        paste_button.connect("clicked", self.on_paste_html)
        header.pack_start(paste_button)

        # Column mode toggle
        self.column_toggle = Gtk.ToggleButton(icon_name="view-columns-symbolic")
        self.column_toggle.set_tooltip_text("Toggle Multi-Column View")
        self.column_toggle.connect("toggled", self.on_toggle_columns)
        header.pack_start(self.column_toggle)

        # Column width adjustment
        column_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        column_label = Gtk.Label(label="Width:")
        self.column_adjustment = Gtk.Adjustment(value=300, lower=50, upper=500, step_increment=10)
        self.column_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.column_adjustment)
        self.column_scale.set_size_request(150, -1)
        self.column_scale.set_digits(0)
        self.column_scale.set_draw_value(True)
        self.column_scale.connect("value-changed", self.on_column_width_changed)
        column_box.append(column_label)
        column_box.append(self.column_scale)
        header.pack_start(column_box)

        # Clear button
        clear_button = Gtk.Button(icon_name="edit-clear-symbolic")
        clear_button.set_tooltip_text("Clear")
        clear_button.connect("clicked", self.on_clear_text)
        header.pack_end(clear_button)

        toolbar_view.add_top_bar(header)

        # Main content area
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Single view scrolled window
        self.single_scrolled = Gtk.ScrolledWindow()
        self.single_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.single_scrolled.set_vexpand(True)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_editable(True)
        self.textview.set_left_margin(12)
        self.textview.set_right_margin(12)
        self.textview.set_top_margin(12)
        self.textview.set_bottom_margin(12)
        self.text_buffer = self.textview.get_buffer()
        self._ensure_tags_once()

        self.single_scrolled.set_child(self.textview)
        
        # Multi-column view
        self.multi_scrolled = Gtk.ScrolledWindow()
        self.multi_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.multi_scrolled.set_vexpand(True)
        self.multi_scrolled.set_hexpand(True)
        
        self.multi_column_view = MultiColumnTextView()
        self.multi_column_view.create_columns(5)  # Create 5 columns initially
        self.multi_scrolled.set_child(self.multi_column_view)

        # Status page
        self.status_page = Adw.StatusPage(
            title="Ready", 
            description="Open an HTML/EPUB file or paste HTML content" + 
                       ("" if EPUB_AVAILABLE else "\n\n⚠️ ebooklib not installed - EPUB support disabled")
        )
        self.status_page.set_visible(True)
        self.main_box.append(self.status_page)

        toolbar_view.set_content(self.main_box)
        self.window.set_content(toolbar_view)
        self.window.present()

    def _ensure_tags_once(self):
        tag_table = self.text_buffer.get_tag_table()
        if tag_table.lookup("bold"):
            return
        bold = Gtk.TextTag.new("bold"); bold.set_property("weight", Pango.Weight.BOLD); tag_table.add(bold)
        italic = Gtk.TextTag.new("italic"); italic.set_property("style", Pango.Style.ITALIC); tag_table.add(italic)
        underline = Gtk.TextTag.new("underline"); underline.set_property("underline", Pango.Underline.SINGLE); tag_table.add(underline)
        strike = Gtk.TextTag.new("strikethrough"); strike.set_property("strikethrough", True); tag_table.add(strike)
        for i in range(1, 7):
            h = Gtk.TextTag.new(f"h{i}")
            h.set_property("weight", Pango.Weight.BOLD)
            h.set_property("scale", 2.0 - (i - 1) * 0.2)
            tag_table.add(h)
        p = Gtk.TextTag.new("paragraph"); p.set_property("pixels-below-lines", 12); tag_table.add(p)
        code = Gtk.TextTag.new("code"); code.set_property("family", "monospace"); code.set_property("background", "#f5f5f5"); tag_table.add(code)

    def on_toggle_columns(self, toggle):
        self.multi_column_mode = toggle.get_active()
        self.refresh_view()

    def on_column_width_changed(self, scale):
        width = int(scale.get_value())
        self.multi_column_view.set_column_width(width)

    def refresh_view(self):
        """Switch between single and multi-column view"""
        # Remove current view
        child = self.main_box.get_first_child()
        if child and child != self.status_page:
            self.main_box.remove(child)
        
        if self.multi_column_mode:
            self.main_box.append(self.multi_scrolled)
            self.multi_column_view.set_content(self.text_buffer)
        else:
            self.main_box.append(self.single_scrolled)

    def show_textview(self):
        if self.status_page.get_visible():
            self.main_box.remove(self.status_page)
            self.status_page.set_visible(False)
        self.refresh_view()

    def show_status(self, title, description):
        child = self.main_box.get_first_child()
        if child and child != self.status_page:
            self.main_box.remove(child)
        
        if not self.status_page.get_visible():
            self.main_box.append(self.status_page)
            self.status_page.set_visible(True)
        self.status_page.set_title(title)
        self.status_page.set_description(description)

    # ---------- File open ----------
    def on_open_file(self, _button):
        dialog = Gtk.FileChooserNative.new(
            title="Open HTML/EPUB File",
            parent=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Open",
            cancel_label="Cancel",
        )
        
        # HTML filter
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML Files")
        html_filter.add_mime_type("text/html")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        dialog.add_filter(html_filter)
        
        # EPUB filter
        if EPUB_AVAILABLE:
            epub_filter = Gtk.FileFilter()
            epub_filter.set_name("EPUB Files")
            epub_filter.add_mime_type("application/epub+zip")
            epub_filter.add_pattern("*.epub")
            dialog.add_filter(epub_filter)
        
        # All files filter
        any_filter = Gtk.FileFilter()
        any_filter.set_name("All Files")
        any_filter.add_pattern("*")
        dialog.add_filter(any_filter)

        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                path = file.get_path()
                if path and path.lower().endswith('.epub'):
                    self.load_epub_file(path)
                else:
                    self.load_html_file(file)
        dialog.destroy()

    def load_html_file(self, file: Gio.File):
        try:
            file.load_contents_async(None, self._on_file_loaded, None)
        except Exception as e:
            self.show_error(f"Error opening file: {e}")

    def _on_file_loaded(self, file, result, _user_data):
        try:
            ok, contents, _etag = file.load_contents_finish(result)
            if ok:
                html_content = contents.decode('utf-8', errors='replace')
                self.parse_and_display_html(html_content)
            else:
                self.show_error("Failed to load file")
        except Exception as e:
            self.show_error(f"Error reading file: {e}")

    def load_epub_file(self, filepath):
        if not EPUB_AVAILABLE:
            self.show_error("ebooklib not installed. Install with: pip install ebooklib")
            return
            
        try:
            book = epub.read_epub(filepath)
            html_parts = []
            
            # Extract text from all document items
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    content = item.get_content().decode('utf-8', errors='replace')
                    html_parts.append(content)
            
            if html_parts:
                combined_html = '\n\n'.join(html_parts)
                self.parse_and_display_html(combined_html)
            else:
                self.show_error("No readable content found in EPUB")
                
        except Exception as e:
            self.show_error(f"Error reading EPUB: {e}")

    # ---------- Clipboard paste (HTML first) ----------
    def on_paste_html(self, _button):
        cb = Gdk.Display.get_default().get_clipboard()
        
        # Try text/html first
        cb.read_text_async(None, self._on_clipboard_text_read, None)

    def _on_clipboard_text_read(self, cb: Gdk.Clipboard, res, _data):
        try:
            text = cb.read_text_finish(res)
            if text:
                # Try to detect if it's HTML
                if '<' in text and '>' in text:
                    self.parse_and_display_html(text)
                else:
                    self.text_buffer.set_text(text)
                    self.show_textview()
            else:
                self.show_error("Clipboard is empty")
        except Exception as e:
            # Fallback to generic read
            self._try_generic_clipboard_read(cb)

    def _try_generic_clipboard_read(self, cb):
        """Fallback method for clipboard reading"""
        try:
            formats = cb.get_formats()
            mime_types = formats.get_mime_types()
            
            # Prefer HTML formats
            html_types = [m for m in mime_types if 'html' in m.lower()]
            if html_types:
                cb.read_text_async(None, self._on_fallback_read, None)
            else:
                cb.read_text_async(None, self._on_plain_text_read, None)
        except Exception as e:
            self.show_error(f"Clipboard read failed: {e}")

    def _on_fallback_read(self, cb, res, _data):
        try:
            text = cb.read_text_finish(res)
            if text:
                self.parse_and_display_html(text)
            else:
                self.show_error("Could not read clipboard content")
        except Exception as e:
            self.show_error(f"Error: {e}")

    def _on_plain_text_read(self, cb, res, _data):
        try:
            text = cb.read_text_finish(res)
            if text:
                self.text_buffer.set_text(text)
                self.show_textview()
            else:
                self.show_error("Clipboard is empty")
        except Exception as e:
            self.show_error(f"Error: {e}")

    # ---------- HTML handling ----------
    def parse_and_display_html(self, html_content: str):
        try:
            body = self.extract_body_content(html_content)
            self.text_buffer.set_text("")
            
            # Ensure tags exist in all column buffers too
            for buf in self.multi_column_view.text_buffers:
                parser_temp = HTMLToTextParser(buf)
            
            parser = HTMLToTextParser(self.text_buffer)
            parser.feed(body)
            parser.close()
            
            self.show_textview()
            start_iter = self.text_buffer.get_start_iter()
            self.text_buffer.place_cursor(start_iter)
            self.textview.scroll_mark_onscreen(self.text_buffer.get_insert())
            
            # Update multi-column view if active
            if self.multi_column_mode:
                self.multi_column_view.set_content(self.text_buffer)
                
        except Exception as e:
            self.show_error(f"Error parsing HTML: {e}")

    def extract_body_content(self, html_content: str) -> str:
        m = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1)
        head_removed = re.sub(r'<head[^>]*>.*?</head>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', head_removed, flags=re.IGNORECASE)
        cleaned = re.sub(r'</?html[^>]*>', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    # ---------- misc ----------
    def on_clear_text(self, _button):
        self.text_buffer.set_text("")
        for buf in self.multi_column_view.text_buffers:
            buf.set_text("")
        self.show_status("Ready", "Open an HTML/EPUB file or paste HTML content" + 
                        ("" if EPUB_AVAILABLE else "\n\n⚠️ ebooklib not installed - EPUB support disabled"))

    def show_error(self, message: str):
        self.show_status("Error", message)


def main():
    app = HTMLTextViewApp()
    return app.run()


if __name__ == "__main__":
    main()
