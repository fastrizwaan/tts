#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GtkSource
import mmap
import os

class LineIndexer:
    """Efficient line indexing for huge files using mmap"""
    def __init__(self, filepath):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.line_offsets = [0]  # Start of first line
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
        chunk_size = 1024 * 1024  # 1MB chunks
        offset = 0
        processed = 0
        
        while offset < self.file_size:
            chunk_end = min(offset + chunk_size, self.file_size)
            chunk = self.mmap_obj[offset:chunk_end]
            
            # Find newlines in chunk
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
            end = self.line_offsets[line_num + 1] - 1  # Exclude newline
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
    
    def get_chunk(self, offset, size):
        """Get chunk by byte offset"""
        end = min(offset + size, self.file_size)
        try:
            return self.mmap_obj[offset:end].decode('utf-8', errors='replace')
        except:
            return self.mmap_obj[offset:end].decode('latin-1', errors='replace')

class TextEditorWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(1200, 800)
        self.set_title("Buttery Smooth Editor")
        
        # Line indexer for huge files
        self.indexer = None
        self.is_large_file = False
        self.large_file_threshold = 10 * 1024 * 1024  # 10MB
        
        # Main layout using ToolbarView for automatic shadow
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)
        
        # Header bar with controls
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        
        # Open button
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)
        
        # Save button
        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.connect("clicked", self.on_save_clicked)
        header.pack_start(save_btn)
        
        # Reload button (for large files)
        self.reload_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.reload_btn.connect("clicked", self.on_reload_clicked)
        self.reload_btn.set_visible(False)
        header.pack_start(self.reload_btn)
        
        # Font size controls
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

        # Settings menu
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(self.create_settings_menu())
        header.pack_end(menu_btn)
        
        # Content box for progress bar, scrolled window, and status bar
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar_view.set_content(content_box)
        
        # Progress bar for indexing
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_visible(False)
        content_box.append(self.progress_bar)
        
        # Scrolled window with optimizations
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_kinetic_scrolling(True)
        scrolled.set_overlay_scrolling(True)
        content_box.append(scrolled)
        
        # Text view with performance optimizations
        self.text_view = GtkSource.View()
        self.text_view.set_show_line_numbers(True)
        self.text_view.set_highlight_current_line(True)
        self.text_view.set_show_right_margin(True)
        self.text_view.set_right_margin_position(80)
        self.text_view.set_tab_width(4)
        self.text_view.set_auto_indent(True)
        self.text_view.set_insert_spaces_instead_of_tabs(True)
        self.text_view.set_smart_backspace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_monospace(True)
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)
        self.text_view.set_top_margin(12)
        self.text_view.set_bottom_margin(12)
        
        # Enable pixel cache for smooth scrolling
        self.text_view.set_pixels_above_lines(1)
        self.text_view.set_pixels_below_lines(1)
        
        scrolled.set_child(self.text_view)
        
        # Text buffer
        self.buffer = self.text_view.get_buffer()
        
        # Apply high-performance font settings
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
        content_box.append(self.status_bar)
        
        # Track changes for status
        self.buffer.connect("changed", self.on_buffer_changed)
        
        # Current file
        self.current_file = None
        self.current_filepath = None
        
        # Settings state
        self.selected_theme_id = "auto"

        # Setup actions
        self.setup_actions()
        
        # Setup theme handling
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect('notify::dark', self.on_theme_changed)
        self.update_theme()

        # Focus the editor on startup with a slight delay to ensure correct rendering
        def on_startup_focus():
            self.text_view.set_editable(True)
            self.text_view.grab_focus()
            # Ensure line highlight is visible by placing cursor at start
            self.buffer.place_cursor(self.buffer.get_start_iter())
            return False
            
        GLib.idle_add(on_startup_focus)

    def setup_actions(self):
        # View Options
        self.add_toggle_action("show-line-numbers", True, 
            lambda action, state: self.text_view.set_show_line_numbers(state.get_boolean()))
        
        self.add_toggle_action("highlight-current-line", True,
            lambda action, state: self.text_view.set_highlight_current_line(state.get_boolean()))
            
        self.add_toggle_action("show-right-margin", True,
            lambda action, state: self.text_view.set_show_right_margin(state.get_boolean()))
            
        self.add_toggle_action("word-wrap", True, self.on_word_wrap_toggled)
        
        # Behavior Options
        self.add_toggle_action("auto-indent", True,
            lambda action, state: self.text_view.set_auto_indent(state.get_boolean()))
            
        self.add_toggle_action("smart-backspace", True,
            lambda action, state: self.text_view.set_smart_backspace(state.get_boolean()))

        # Theme Action
        action = Gio.SimpleAction.new_stateful("theme", GLib.VariantType.new("s"), GLib.Variant("s", "auto"))
        action.connect("change-state", self.on_theme_action_changed)
        self.add_action(action)

    def add_toggle_action(self, name, default, callback):
        action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(default))
        action.connect("change-state", self.create_toggle_callback(callback))
        self.add_action(action)
        # Initialize
        callback(action, GLib.Variant.new_boolean(default))
        
    def create_toggle_callback(self, real_callback):
        def wrapper(action, state):
            action.set_state(state)
            real_callback(action, state)
        return wrapper

    def on_word_wrap_toggled(self, action, state):
        wrap_mode = Gtk.WrapMode.WORD_CHAR if state.get_boolean() else Gtk.WrapMode.NONE
        self.text_view.set_wrap_mode(wrap_mode)

    def on_theme_action_changed(self, action, state):
        action.set_state(state)
        self.selected_theme_id = state.get_string()
        self.update_theme()

    def create_settings_menu(self):
        menu = Gio.Menu()
        
        # View Section
        view_section = Gio.Menu()
        view_section.append("Show Line Numbers", "win.show-line-numbers")
        view_section.append("Highlight Current Line", "win.highlight-current-line")
        view_section.append("Show Right Margin", "win.show-right-margin")
        view_section.append("Word Wrap", "win.word-wrap")
        menu.append_section("View", view_section)
        
        # Behavior Section
        behavior_section = Gio.Menu()
        behavior_section.append("Auto Indent", "win.auto-indent")
        behavior_section.append("Smart Backspace", "win.smart-backspace")
        menu.append_section("Behavior", behavior_section)
        
        # Theme Section
        theme_menu = Gio.Menu()
        theme_menu.append("Auto (System)", "win.theme::auto")
        
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        ids = scheme_manager.get_scheme_ids()
        # Sort nicely, maybe prioritize common ones?
        for scheme_id in sorted(ids):
            scheme = scheme_manager.get_scheme(scheme_id)
            name = scheme.get_name() if scheme else scheme_id
            theme_menu.append(name, f"win.theme::{scheme_id}")
            
        menu.append_submenu("Theme", theme_menu)
        
        return menu

    def on_theme_changed(self, manager, pspec):
        self.update_theme()
        
    def update_theme(self):
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = None
        
        if self.selected_theme_id != "auto":
            scheme = scheme_manager.get_scheme(self.selected_theme_id)
        
        if not scheme:
            # Auto logic or fallback
            style_manager = Adw.StyleManager.get_default()
            is_dark = style_manager.get_dark()
            
            if is_dark:
                scheme = scheme_manager.get_scheme('adwaita-dark')
                if not scheme:
                    scheme = scheme_manager.get_scheme('oblivion')
            else:
                scheme = scheme_manager.get_scheme('adwaita')
                if not scheme:
                    scheme = scheme_manager.get_scheme('classic')
            
        if scheme:
            self.buffer.set_style_scheme(scheme)
    
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
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.font_label.set_text(str(self.font_size))
    
    def change_font_size(self, delta):
        """Change font size smoothly"""
        self.font_size = max(8, min(32, self.font_size + delta))
        self.update_font()
    
    def on_buffer_changed(self, buffer):
        """Update status on text change"""
        if not self.is_large_file:
            char_count = buffer.get_char_count()
            line_count = buffer.get_line_count()
            self.status_bar.set_text(f"{line_count} lines, {char_count} characters")
    
    
    def update_language(self, filename):
        """Detect and set language for syntax highlighting"""
        lang_manager = GtkSource.LanguageManager.get_default()
        language = lang_manager.guess_language(filename, None)
        self.buffer.set_language(language)
    
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
        """Handle file open response"""
        try:
            file = dialog.open_finish(result)
            if file:
                self.load_file(file)
        except GLib.Error:
            pass
    
    def load_file(self, file):
        """Load file into buffer with mmap for large files"""
        try:
            filepath = file.get_path()
            if not filepath:
                self.status_bar.set_text("Cannot load remote files")
                return
            
            file_size = os.path.getsize(filepath)
            
            # Close previous indexer if exists
            if self.indexer:
                self.indexer.close()
                self.indexer = None
            
            self.current_file = file
            self.current_filepath = filepath
            
            # For small files, load normally
            if file_size < self.large_file_threshold:
                self.is_large_file = False
                self.reload_btn.set_visible(False)
                success, content, _ = file.load_contents(None)
                if success:
                    try:
                        text = content.decode('utf-8')
                    except UnicodeDecodeError:
                        text = content.decode('latin-1', errors='replace')
                    
                    self.buffer.set_text(text)
                    self.set_title(f"{file.get_basename()} - Buttery Smooth Editor")
                    self.update_language(file.get_basename())
                    self.update_status()
            else:
                # For large files, use mmap and indexing
                self.is_large_file = True
                self.reload_btn.set_visible(True)
                self.load_large_file(filepath, file.get_basename())
                
        except Exception as e:
            self.status_bar.set_text(f"Error loading file: {str(e)}")
    
    def load_large_file(self, filepath, filename):
        """Load large file with mmap and line indexing"""
        self.status_bar.set_text(f"Loading large file: {filename}...")
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0)
        
        # Create indexer
        self.indexer = LineIndexer(filepath)
        self.indexer.open()
        
        # Index lines in background (simulate with idle)
        def index_progress():
            def progress_callback(progress):
                self.progress_bar.set_fraction(progress)
            
            self.indexer.index_lines(progress_callback)
            
            # Load first chunk into buffer
            lines_to_load = min(1000, self.indexer.get_line_count())
            text = self.indexer.get_lines(0, lines_to_load - 1)
            
            if self.indexer.get_line_count() > lines_to_load:
                text += f"\n\n... [{self.indexer.get_line_count() - lines_to_load:,} more lines] ..."
                text += "\n... Use scroll or reload to view more ..."
            
            self.buffer.set_text(text)
            self.text_view.set_editable(False)  # Read-only for huge files
            
            self.progress_bar.set_visible(False)
            self.set_title(f"{filename} - Buttery Smooth Editor (Large File)")
            self.update_language(filename)
            self.update_status()
            
            return False  # Don't repeat
        
        GLib.idle_add(index_progress)
    
    def on_reload_clicked(self, button):
        """Reload large file section"""
        if self.current_file and self.is_large_file:
            self.load_file(self.current_file)
    
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
        """Handle file save response"""
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
            self.set_title(f"{file.get_basename()} - Buttery Smooth Editor")
            self.status_bar.set_text(f"Saved {file.get_basename()}")
        except GLib.Error as e:
            self.status_bar.set_text(f"Error saving file: {e.message}")

class TextEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.smootheditor',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
    
    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TextEditorWindow(application=self)
        win.present()

if __name__ == '__main__':
    app = TextEditorApp()
    app.run(None)
