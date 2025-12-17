#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Pango
import mmap
import os
import re

class LineIndexer:
    """Efficient line indexing for huge files using mmap"""
    def __init__(self, filepath):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.line_offsets = [0]
        self.indexed = False
        self.mmap_obj = None
        self.file_obj = None
        
    def open(self):
        """Open file with mmap"""
        self.file_obj = open(self.filepath, 'r+b')
        self.mmap_obj = mmap.mmap(self.file_obj.fileno(), 0, access=mmap.ACCESS_READ)
        
    def close(self):
        """Close mmap and file"""
        if self.mmap_obj:
            self.mmap_obj.close()
        if self.file_obj:
            self.file_obj.close()
    
    def index_lines(self, progress_callback=None):
        """Build line index for fast random access"""
        if self.indexed:
            return
        
        self.line_offsets = [0]
        chunk_size = 1024 * 1024
        offset = 0
        processed = 0
        
        while offset < self.file_size:
            chunk_end = min(offset + chunk_size, self.file_size)
            chunk = self.mmap_obj[offset:chunk_end]
            
            pos = 0
            while True:
                nl_pos = chunk.find(b'\n', pos)
                if nl_pos == -1:
                    break
                self.line_offsets.append(offset + nl_pos + 1)
                pos = nl_pos + 1
            
            offset = chunk_end
            processed += len(chunk)
            
            if progress_callback and self.file_size > 0:
                progress = processed / self.file_size
                progress_callback(progress)
        
        self.indexed = True
    
    def get_line_count(self):
        """Get total number of lines"""
        return len(self.line_offsets)
    
    def get_line(self, line_num):
        """Get specific line by number (0-indexed)"""
        if line_num < 0 or line_num >= len(self.line_offsets):
            return ""
        
        start = self.line_offsets[line_num]
        if line_num + 1 < len(self.line_offsets):
            end = self.line_offsets[line_num + 1] - 1
        else:
            end = self.file_size
        
        try:
            return self.mmap_obj[start:end].decode('utf-8', errors='replace')
        except:
            return self.mmap_obj[start:end].decode('latin-1', errors='replace')
    
    def get_lines(self, start_line, end_line):
        """Get range of lines efficiently"""
        if start_line < 0:
            start_line = 0
        if end_line >= len(self.line_offsets):
            end_line = len(self.line_offsets) - 1
        
        if start_line > end_line:
            return ""
        
        start_offset = self.line_offsets[start_line]
        if end_line + 1 < len(self.line_offsets):
            end_offset = self.line_offsets[end_line + 1]
        else:
            end_offset = self.file_size
        
        try:
            return self.mmap_obj[start_offset:end_offset].decode('utf-8', errors='replace')
        except:
            return self.mmap_obj[start_offset:end_offset].decode('latin-1', errors='replace')
    
    def search(self, pattern, case_sensitive=False, max_results=1000):
        """Search for pattern in file, return list of (line_num, line_text, match_pos)"""
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE
        
        try:
            regex = re.compile(pattern.encode('utf-8'), flags)
        except:
            return results
        
        for i in range(len(self.line_offsets)):
            if len(results) >= max_results:
                break
            
            line = self.get_line(i)
            if regex.search(line.encode('utf-8')):
                match = regex.search(line.encode('utf-8'))
                results.append((i, line, match.start() if match else 0))
        
        return results

class TextEditorWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(1200, 800)
        self.set_title("Ultimate Buttery Smooth Editor")
        
        self.indexer = None
        self.is_large_file = False
        self.large_file_threshold = 10 * 1024 * 1024
        self.current_start_line = 0
        self.lines_per_page = 1000
        self.search_results = []
        self.current_search_index = 0
        
        # Main layout
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)
        
        # Header bar
        header = Adw.HeaderBar()
        box.append(header)
        
        # Left controls
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open File")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)
        
        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.set_tooltip_text("Save File")
        save_btn.connect("clicked", self.on_save_clicked)
        header.pack_start(save_btn)
        
        # Search button
        search_btn = Gtk.Button(icon_name="edit-find-symbolic")
        search_btn.set_tooltip_text("Search")
        search_btn.connect("clicked", self.on_search_clicked)
        header.pack_start(search_btn)
        
        # Jump to line button
        jump_btn = Gtk.Button(icon_name="go-jump-symbolic")
        jump_btn.set_tooltip_text("Jump to Line")
        jump_btn.connect("clicked", self.on_jump_clicked)
        header.pack_start(jump_btn)
        
        # Right controls
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        
        minus_btn = Gtk.Button(label="-")
        minus_btn.connect("clicked", lambda b: self.change_font_size(-1))
        font_box.append(minus_btn)
        
        self.font_label = Gtk.Label(label="12")
        font_box.append(self.font_label)
        
        plus_btn = Gtk.Button(label="+")
        plus_btn.connect("clicked", lambda b: self.change_font_size(1))
        font_box.append(plus_btn)
        
        header.pack_end(font_box)
        
        # Search bar (hidden by default)
        self.search_bar = Gtk.SearchBar()
        box.append(self.search_bar)
        
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)
        
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self.on_search_activate)
        search_box.append(self.search_entry)
        
        self.case_check = Gtk.CheckButton(label="Case")
        search_box.append(self.case_check)
        
        prev_btn = Gtk.Button(icon_name="go-up-symbolic")
        prev_btn.connect("clicked", lambda b: self.navigate_search(-1))
        search_box.append(prev_btn)
        
        next_btn = Gtk.Button(icon_name="go-down-symbolic")
        next_btn.connect("clicked", lambda b: self.navigate_search(1))
        search_box.append(next_btn)
        
        self.search_label = Gtk.Label(label="")
        search_box.append(self.search_label)
        
        self.search_bar.set_child(search_box)
        
        # Progress bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_visible(False)
        box.append(self.progress_bar)
        
        # Main content area with line numbers
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_box.set_vexpand(True)
        box.append(content_box)
        
        # Line numbers view
        line_scroll = Gtk.ScrolledWindow()
        line_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        line_scroll.set_size_request(70, -1)
        
        self.line_view = Gtk.TextView()
        self.line_view.set_editable(False)
        self.line_view.set_cursor_visible(False)
        self.line_view.set_monospace(True)
        self.line_view.set_right_margin(6)
        self.line_view.set_left_margin(6)
        self.line_view.add_css_class("line-numbers")
        line_scroll.set_child(self.line_view)
        content_box.append(line_scroll)
        
        self.line_buffer = self.line_view.get_buffer()
        
        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(sep)
        
        # Text view scrolled window
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_kinetic_scrolling(True)
        self.scrolled.set_overlay_scrolling(True)
        
        # Connect scroll event for lazy loading
        self.vadj = self.scrolled.get_vadjustment()
        self.vadj.connect("value-changed", self.on_scroll_changed)
        
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_monospace(True)
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)
        self.text_view.set_top_margin(12)
        self.text_view.set_bottom_margin(12)
        self.text_view.set_pixels_above_lines(1)
        self.text_view.set_pixels_below_lines(1)
        
        self.scrolled.set_child(self.text_view)
        content_box.append(self.scrolled)
        
        # Sync line numbers scroll with text scroll
        text_vadj = self.scrolled.get_vadjustment()
        line_scroll.set_vadjustment(text_vadj)
        
        self.buffer = self.text_view.get_buffer()
        
        # Navigation bar for large files
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        nav_box.set_margin_start(12)
        nav_box.set_margin_end(12)
        nav_box.set_margin_top(6)
        nav_box.set_margin_bottom(6)
        
        self.nav_prev = Gtk.Button(label="◄ Previous")
        self.nav_prev.connect("clicked", lambda b: self.navigate_pages(-1))
        self.nav_prev.set_visible(False)
        nav_box.append(self.nav_prev)
        
        self.nav_label = Gtk.Label(label="")
        self.nav_label.set_hexpand(True)
        nav_box.append(self.nav_label)
        
        self.nav_next = Gtk.Button(label="Next ►")
        self.nav_next.connect("clicked", lambda b: self.navigate_pages(1))
        self.nav_next.set_visible(False)
        nav_box.append(self.nav_next)
        
        box.append(nav_box)
        
        # Font settings
        self.font_size = 12
        self.update_font()
        
        # Status bar
        self.status_bar = Gtk.Label(label="Ready")
        self.status_bar.set_xalign(0)
        self.status_bar.add_css_class("dim-label")
        self.status_bar.set_margin_start(12)
        self.status_bar.set_margin_end(12)
        self.status_bar.set_margin_top(6)
        self.status_bar.set_margin_bottom(6)
        box.append(self.status_bar)
        
        self.buffer.connect("changed", self.on_buffer_changed)
        
        self.current_file = None
        self.current_filepath = None
        
        # Add CSS for line numbers
        css_provider = Gtk.CssProvider()
        css = """
        .line-numbers {
            background-color: alpha(@theme_fg_color, 0.05);
            color: alpha(@theme_fg_color, 0.5);
        }
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        self.load_sample_text()
    
    def update_font(self):
        """Update font with CSS"""
        css_provider = Gtk.CssProvider()
        css = f"""
        textview {{
            font-family: monospace;
            font-size: {self.font_size}pt;
        }}
        """
        css_provider.load_from_data(css.encode())
        self.text_view.get_style_context().add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.line_view.get_style_context().add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.font_label.set_text(str(self.font_size))
    
    def change_font_size(self, delta):
        """Change font size"""
        self.font_size = max(8, min(32, self.font_size + delta))
        self.update_font()
    
    def update_line_numbers(self, start_line, count):
        """Update line number display"""
        lines = "\n".join([str(start_line + i + 1) for i in range(count)])
        self.line_buffer.set_text(lines)
    
    def on_scroll_changed(self, adj):
        """Virtual scrolling - load different page window based on scroll position"""
        if not self.is_large_file or not self.indexer:
            return
        
        # Calculate which virtual line we're at based on scroll position
        value = adj.get_value()
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        
        if upper == 0:
            return
        
        # Map scroll position to line number in the entire file
        total_lines = self.indexer.get_line_count()
        scroll_ratio = value / max(upper - page_size, 1)
        virtual_line = int(scroll_ratio * total_lines)
        
        # Calculate which page window this falls into
        target_page = (virtual_line // self.lines_per_page) * self.lines_per_page
        
        # Only reload if we've moved to a significantly different page
        if abs(target_page - self.current_start_line) > self.lines_per_page // 2:
            self.load_page_virtual(target_page)
    
    def load_page_virtual(self, start_line):
        """Load specific page window without appending"""
        if not self.indexer:
            return
        
        total_lines = self.indexer.get_line_count()
        start_line = max(0, min(start_line, total_lines - self.lines_per_page))
        end_line = min(start_line + self.lines_per_page, total_lines)
        
        # Disconnect scroll handler temporarily to avoid triggering reload
        self.vadj.disconnect_by_func(self.on_scroll_changed)
        
        # Load new page window
        text = self.indexer.get_lines(start_line, end_line - 1)
        self.buffer.set_text(text)
        
        self.current_start_line = start_line
        
        self.update_line_numbers(start_line, end_line - start_line)
        self.update_navigation()
        
        # Reconnect scroll handler
        self.vadj.connect("value-changed", self.on_scroll_changed)
    
    def navigate_pages(self, direction):
        """Navigate through pages in large files"""
        if not self.is_large_file or not self.indexer:
            return
        
        total_lines = self.indexer.get_line_count()
        page_size = self.lines_per_page
        
        new_start = self.current_start_line + (direction * page_size)
        new_start = max(0, min(new_start, total_lines - page_size))
        
        if new_start != self.current_start_line:
            self.load_page_virtual(new_start)
    
    def load_page(self, start_line):
        """Load specific page of lines (for initial load and jump)"""
        self.load_page_virtual(start_line)
    
    def update_navigation(self):
        """Update navigation controls"""
        if not self.is_large_file or not self.indexer:
            return
        
        total_lines = self.indexer.get_line_count()
        end_line = min(self.current_start_line + self.lines_per_page, total_lines)
        
        self.nav_label.set_text(
            f"Lines {self.current_start_line + 1:,} - {end_line:,} of {total_lines:,}"
        )
        
        self.nav_prev.set_sensitive(self.current_start_line > 0)
        self.nav_next.set_sensitive(end_line < total_lines)
    
    def on_jump_clicked(self, button):
        """Show jump to line dialog"""
        if not self.is_large_file or not self.indexer:
            return
        
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Jump to Line",
            body="Enter line number:"
        )
        
        entry = Gtk.Entry()
        entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        dialog.set_extra_child(entry)
        
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("jump", "Jump")
        dialog.set_response_appearance("jump", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("jump")
        
        def on_response(dlg, response):
            if response == "jump":
                try:
                    line_num = int(entry.get_text()) - 1
                    total_lines = self.indexer.get_line_count()
                    if 0 <= line_num < total_lines:
                        page_start = (line_num // 1000) * 1000
                        self.load_page(page_start)
                        self.status_bar.set_text(f"Jumped to line {line_num + 1:,}")
                    else:
                        self.status_bar.set_text(f"Line number out of range (1-{total_lines:,})")
                except ValueError:
                    self.status_bar.set_text("Invalid line number")
        
        dialog.connect("response", on_response)
        dialog.present()
    
    def on_search_clicked(self, button):
        """Toggle search bar"""
        self.search_bar.set_search_mode(not self.search_bar.get_search_mode())
        if self.search_bar.get_search_mode():
            self.search_entry.grab_focus()
    
    def on_search_activate(self, entry):
        """Perform search"""
        pattern = entry.get_text()
        if not pattern:
            return
        
        self.status_bar.set_text("Searching...")
        self.search_results = []
        self.current_search_index = 0
        
        if self.is_large_file and self.indexer:
            # Search in mmap file
            case_sensitive = self.case_check.get_active()
            self.search_results = self.indexer.search(pattern, case_sensitive, max_results=1000)
            
            if self.search_results:
                self.search_label.set_text(f"1/{len(self.search_results)}")
                self.navigate_search(0)
            else:
                self.search_label.set_text("0/0")
                self.status_bar.set_text("No matches found")
        else:
            # Search in buffer for small files
            start = self.buffer.get_start_iter()
            match = start.forward_search(pattern, Gtk.TextSearchFlags.TEXT_ONLY, None)
            if match:
                match_start, match_end = match
                self.buffer.select_range(match_start, match_end)
                self.text_view.scroll_to_iter(match_start, 0.0, False, 0.0, 0.0)
                self.status_bar.set_text("Match found")
            else:
                self.status_bar.set_text("No matches found")
    
    def navigate_search(self, direction):
        """Navigate through search results"""
        if not self.search_results:
            return
        
        if direction == 0:
            self.current_search_index = 0
        else:
            self.current_search_index = (self.current_search_index + direction) % len(self.search_results)
        
        line_num, line_text, pos = self.search_results[self.current_search_index]
        
        # Load page containing this line
        page_start = (line_num // 1000) * 1000
        self.load_page(page_start)
        
        self.search_label.set_text(f"{self.current_search_index + 1}/{len(self.search_results)}")
        self.status_bar.set_text(f"Match at line {line_num + 1:,}")
    
    def on_buffer_changed(self, buffer):
        """Update status on text change"""
        if not self.is_large_file:
            char_count = buffer.get_char_count()
            line_count = buffer.get_line_count()
            self.status_bar.set_text(f"{line_count} lines, {char_count} characters")
            self.update_line_numbers(0, line_count)
    
    def load_sample_text(self):
        """Load sample text"""
        sample = """# Ultimate Buttery Smooth Editor

Features:
✓ Memory-mapped file I/O (mmap)
✓ Line indexing for instant access
✓ Virtual scrolling for huge files
✓ Lazy loading on scroll
✓ Line numbers display
✓ Jump to line (Ctrl+G style)
✓ Fast search through millions of lines
✓ Smooth kinetic scrolling

Try opening a huge log file!

""" + "\n".join([f"Line {i}: Sample content for testing" for i in range(100)])
        
        self.buffer.set_text(sample)
        self.update_line_numbers(0, self.buffer.get_line_count())
        self.update_status()
    
    def update_status(self):
        """Update status bar"""
        if self.is_large_file and self.indexer:
            lines = self.indexer.get_line_count()
            size_mb = self.indexer.file_size / (1024 * 1024)
            self.status_bar.set_text(f"Large file: {lines:,} lines, {size_mb:.1f}MB (mmap active)")
        else:
            char_count = self.buffer.get_char_count()
            line_count = self.buffer.get_line_count()
            self.status_bar.set_text(f"{line_count} lines, {char_count} characters")
    
    def on_open_clicked(self, button):
        """Open file dialog"""
        dialog = Gtk.FileDialog()
        dialog.open(self, None, self.on_open_response)
    
    def on_open_response(self, dialog, result):
        """Handle file open"""
        try:
            file = dialog.open_finish(result)
            if file:
                self.load_file(file)
        except GLib.Error:
            pass
    
    def load_file(self, file):
        """Load file with mmap for large files"""
        try:
            filepath = file.get_path()
            if not filepath:
                self.status_bar.set_text("Cannot load remote files")
                return
            
            file_size = os.path.getsize(filepath)
            
            if self.indexer:
                self.indexer.close()
                self.indexer = None
            
            self.current_file = file
            self.current_filepath = filepath
            
            if file_size < self.large_file_threshold:
                self.is_large_file = False
                self.nav_prev.set_visible(False)
                self.nav_next.set_visible(False)
                
                success, content, _ = file.load_contents(None)
                if success:
                    try:
                        text = content.decode('utf-8')
                    except UnicodeDecodeError:
                        text = content.decode('latin-1', errors='replace')
                    
                    self.buffer.set_text(text)
                    self.set_title(f"{file.get_basename()} - Ultimate Editor")
                    self.update_line_numbers(0, self.buffer.get_line_count())
                    self.update_status()
            else:
                self.is_large_file = True
                self.nav_prev.set_visible(True)
                self.nav_next.set_visible(True)
                self.load_large_file(filepath, file.get_basename())
                
        except Exception as e:
            self.status_bar.set_text(f"Error: {str(e)}")
    
    def load_large_file(self, filepath, filename):
        """Load large file with mmap"""
        self.status_bar.set_text(f"Indexing {filename}...")
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0)
        
        self.indexer = LineIndexer(filepath)
        self.indexer.open()
        
        def index_progress():
            def progress_callback(progress):
                self.progress_bar.set_fraction(progress)
            
            self.indexer.index_lines(progress_callback)
            
            self.current_start_line = 0
            self.lines_per_page = 1000
            self.load_page_virtual(0)
            
            self.text_view.set_editable(False)
            self.progress_bar.set_visible(False)
            self.set_title(f"{filename} - Ultimate Editor (Large)")
            self.update_status()
            
            return False
        
        GLib.idle_add(index_progress)
    
    def on_save_clicked(self, button):
        """Save file"""
        if self.is_large_file:
            self.status_bar.set_text("Cannot save large files in read-only mode")
            return
        
        if self.current_file:
            self.save_file(self.current_file)
        else:
            dialog = Gtk.FileDialog()
            dialog.save(self, None, self.on_save_response)
    
    def on_save_response(self, dialog, result):
        """Handle save response"""
        try:
            file = dialog.save_finish(result)
            if file:
                self.save_file(file)
        except GLib.Error:
            pass
    
    def save_file(self, file):
        """Save buffer to file"""
        try:
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            text = self.buffer.get_text(start, end, False)
            
            file.replace_contents(
                text.encode('utf-8'),
                None,
                False,
                Gio.FileCreateFlags.NONE,
                None
            )
            
            self.current_file = file
            self.set_title(f"{file.get_basename()} - Ultimate Editor")
            self.status_bar.set_text(f"Saved {file.get_basename()}")
        except GLib.Error as e:
            self.status_bar.set_text(f"Error: {e.message}")

class TextEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.ultimateeditor',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
    
    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TextEditorWindow(application=self)
        win.present()

if __name__ == '__main__':
    app = TextEditorApp()
    app.run(None)
