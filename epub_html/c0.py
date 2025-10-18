#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, PangoCairo
from ebooklib import epub

class EpubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)
        
        # EPUB related variables
        self.current_book = None
        self.chapters = []
        self.current_chapter = 0
        self.temp_dir = None
        
        # Column settings
        self.column_count = 2
        self.column_width = 400
        self.column_gap = 40
        self.column_padding = 20
        
        self.setup_ui()
        
    def setup_ui(self):
        # Use AdwToolbarView for proper Adwaita layout
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)
        
        # Create header bar
        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)
        
        # Create main content box
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.main_box)
        
        # Menu button with column options
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.add_css_class("flat")
        
        # Create menu
        menu = Gio.Menu()
        columns_menu = Gio.Menu()
        for i in range(1, 11):
            columns_menu.append(f"{i} Column{'s' if i > 1 else ''}", f"app.set-columns({i})")
        menu.append_submenu("Columns", columns_menu)
        menu_button.set_menu_model(menu)
        
        # Open button
        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.add_css_class("flat")
        open_button.connect("clicked", self.on_open_clicked)
        
        # Navigation buttons
        self.prev_button = Gtk.Button()
        self.prev_button.set_icon_name("go-previous-symbolic")
        self.prev_button.set_tooltip_text("Previous Chapter")
        self.prev_button.add_css_class("flat")
        self.prev_button.connect("clicked", self.on_prev_chapter)
        self.prev_button.set_sensitive(False)
        
        self.next_button = Gtk.Button()
        self.next_button.set_icon_name("go-next-symbolic")
        self.next_button.set_tooltip_text("Next Chapter")
        self.next_button.add_css_class("flat")
        self.next_button.connect("clicked", self.on_next_chapter)
        self.next_button.set_sensitive(False)
        
        # Add buttons to header bar using the title widget approach
        # This works around version compatibility issues
        try:
            # Try the new API first
            header_bar.pack_start(open_button)
            header_bar.pack_start(self.prev_button)  
            header_bar.pack_start(self.next_button)
            header_bar.pack_end(menu_button)
        except AttributeError:
            # Fall back to setting title widget with a box
            button_box_start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_start.set_spacing(6)
            button_box_start.append(open_button)
            button_box_start.append(self.prev_button)
            button_box_start.append(self.next_button)
            
            button_box_end = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)  
            button_box_end.append(menu_button)
            
            # Create a main box for the header content
            header_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            header_content.set_hexpand(True)
            header_content.append(button_box_start)
            
            # Add spacer
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            header_content.append(spacer)
            
            header_content.append(button_box_end)
            header_bar.set_title_widget(header_content)
        

        
        
        # Scrolled window for WebView
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled_window.set_vexpand(True)
        self.main_box.append(self.scrolled_window)
        
        # WebView for content
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        
        # Enable smooth scrolling
        settings = self.webview.get_settings()
        settings.set_enable_smooth_scrolling(True)
        settings.set_enable_javascript(True)
        
        self.scrolled_window.set_child(self.webview)
        
        # Chapter info bar with proper sizing
        self.info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.info_bar.set_margin_top(5)
        self.info_bar.set_margin_bottom(5)
        self.info_bar.set_margin_start(10)
        self.info_bar.set_margin_end(10)
        
        self.chapter_label = Gtk.Label()
        self.chapter_label.set_markup("<i>No EPUB loaded</i>")
        self.chapter_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.chapter_label.set_max_width_chars(80)  # Prevent layout issues
        self.info_bar.append(self.chapter_label)
        
        self.main_box.append(self.info_bar)
        
        # Connect scroll events for column snapping
        self.setup_scroll_snapping()
        
    def setup_scroll_snapping(self):
        """Setup column snapping functionality"""
        # Get horizontal adjustment
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        
        # Connect to scroll events
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.HORIZONTAL)
        scroll_controller.connect("scroll", self.on_scroll)
        self.webview.add_controller(scroll_controller)
        
        # Timer for delayed snapping
        self.snap_timeout_id = None
        
    def on_scroll(self, controller, dx, dy):
        """Handle scroll events for column snapping"""
        if self.snap_timeout_id:
            GLib.source_remove(self.snap_timeout_id)
        
        # Delay snapping to avoid interference during continuous scrolling
        self.snap_timeout_id = GLib.timeout_add(150, self.snap_to_column)
        return False
        
    def snap_to_column(self):
        """Snap to the nearest column"""
        if not self.current_book:
            return False
            
        current_pos = self.h_adjustment.get_value()
        column_step = self.column_width + self.column_gap
        
        # Calculate nearest column
        column_index = round(current_pos / column_step)
        target_pos = column_index * column_step
        
        # Smooth scroll to target position
        self.h_adjustment.set_value(target_pos)
        
        self.snap_timeout_id = None
        return False
        
    def on_open_clicked(self, button):
        """Handle open button click"""
        dialog = Gtk.FileChooserNative(
            title="Open EPUB File",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel"
        )
        
        # Add file filter
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB Files")
        epub_filter.add_pattern("*.epub")
        dialog.set_filter(epub_filter)
        
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()
        
    def on_file_dialog_response(self, dialog, response):
        """Handle file dialog response"""
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_files().get_item(0)
            if file:
                path = file.get_path()
                self.load_epub(path)
        dialog.destroy()
        
    def load_epub(self, filepath):
        """Load EPUB file"""
        try:
            # Clean up previous temp directory
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                
            # Create new temp directory
            self.temp_dir = tempfile.mkdtemp()
            
            # Load EPUB
            self.current_book = epub.read_epub(filepath)
            self.extract_chapters()
            
            if self.chapters:
                self.current_chapter = 0
                self.load_chapter()
                self.update_navigation()
                
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")
            
    def extract_chapters(self):
        """Extract chapters from EPUB"""
        self.chapters = []
        
        # Get spine items (reading order)
        spine_items = [item[0] for item in self.current_book.spine]
        
        for item_id in spine_items:
            # Find item by ID in the book's items
            item = None
            for book_item in self.current_book.get_items():
                if book_item.id == item_id:
                    item = book_item
                    break
            
            if item and hasattr(item, 'media_type') and item.media_type == 'application/xhtml+xml':
                # Extract chapter content
                content = item.get_content().decode('utf-8')
                
                # Save to temp file
                chapter_file = os.path.join(self.temp_dir, f"{item_id}.html")
                
                # Process content for column layout
                processed_content = self.process_chapter_content(content, item)
                
                with open(chapter_file, 'w', encoding='utf-8') as f:
                    f.write(processed_content)
                
                self.chapters.append({
                    'id': item_id,
                    'title': self.extract_title(content),
                    'file': chapter_file,
                    'item': item
                })
                
    def process_chapter_content(self, content, item):
        """Process chapter content for column layout"""
        # Extract CSS and images
        self.extract_resources()
        
        # Create column-based HTML
        css_styles = f"""
        <style>
        body {{
            margin: 0;
            padding: {self.column_padding}px;
            font-family: 'Cantarell', sans-serif;
            font-size: 16px;
            line-height: 1.6;
            background-color: #fafafa;
            color: #2e3436;
            column-count: {self.column_count};
            column-width: {self.column_width}px;
            column-gap: {self.column_gap}px;
            column-fill: auto;
        }}
        
        h1, h2, h3, h4, h5, h6 {{
            break-after: avoid;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            font-weight: bold;
        }}
        
        h1 {{ 
            font-size: 1.8em; 
            break-before: column;
        }}
        h2 {{ font-size: 1.5em; }}
        h3 {{ font-size: 1.3em; }}
        
        p {{
            margin: 0 0 1em 0;
            text-align: justify;
            hyphens: auto;
        }}
        
        img {{
            max-width: 100%;
            height: auto;
            break-inside: avoid;
        }}
        
        blockquote {{
            margin: 1em 2em;
            font-style: italic;
            border-left: 3px solid #3584e4;
            padding-left: 1em;
            break-inside: avoid;
        }}
        
        /* Dark mode support */
        @media (prefers-color-scheme: dark) {{
            body {{
                background-color: #242424;
                color: #ffffff;
            }}
            blockquote {{
                border-left-color: #62a0ea;
            }}
        }}
        </style>
        """
        
        # Clean and wrap content
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        if body_match:
            body_content = body_match.group(1)
        else:
            # If no body tag, use entire content
            body_content = content
            
        # Remove existing head/html tags if present
        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            {css_styles}
        </head>
        <body>
            {body_content}
        </body>
        </html>
        """
        
    def extract_resources(self):
        """Extract CSS and images from EPUB"""
        if not self.current_book or not self.temp_dir:
            return
            
        # Create resources directory
        resources_dir = os.path.join(self.temp_dir, 'resources')
        os.makedirs(resources_dir, exist_ok=True)
        
        # Extract CSS and images
        for item in self.current_book.get_items():
            if hasattr(item, 'media_type'):
                # Check for CSS and image types
                if item.media_type in ['text/css', 'image/jpeg', 'image/png', 'image/gif', 'image/svg+xml']:
                    resource_path = os.path.join(resources_dir, os.path.basename(item.get_name()))
                    with open(resource_path, 'wb') as f:
                        f.write(item.get_content())
                    
    def extract_title(self, content):
        """Extract title from chapter content"""
        # Try to find h1 tag first
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', content, re.IGNORECASE | re.DOTALL)
        if h1_match:
            title = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
            if title:
                return title
                
        # Try title tag
        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            if title:
                return title
                
        return "Untitled Chapter"
        
    def load_chapter(self):
        """Load current chapter in WebView"""
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return
            
        chapter = self.chapters[self.current_chapter]
        file_uri = f"file://{chapter['file']}"
        
        self.webview.load_uri(file_uri)
        
        # Update chapter info
        chapter_info = f"Chapter {self.current_chapter + 1} of {len(self.chapters)}: {chapter['title']}"
        self.chapter_label.set_text(chapter_info)
        
    def update_navigation(self):
        """Update navigation button states"""
        self.prev_button.set_sensitive(self.current_chapter > 0)
        self.next_button.set_sensitive(self.current_chapter < len(self.chapters) - 1)
        
    def on_prev_chapter(self, button):
        """Go to previous chapter"""
        if self.current_chapter > 0:
            self.current_chapter -= 1
            self.load_chapter()
            self.update_navigation()
            
    def on_next_chapter(self, button):
        """Go to next chapter"""
        if self.current_chapter < len(self.chapters) - 1:
            self.current_chapter += 1
            self.load_chapter()
            self.update_navigation()
            
    def set_column_count(self, count):
        """Set number of columns"""
        self.column_count = count
        if self.current_book:
            # Reload current chapter with new column count
            self.extract_chapters()
            self.load_chapter()
            
    def show_error(self, message):
        """Show error message"""
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "_OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.present()
        
    def cleanup(self):
        """Cleanup temporary files"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.epubviewer")
        
    def do_activate(self):
        window = EpubViewer(self)
        
        # Add column count actions
        for i in range(1, 11):
            action = Gio.SimpleAction.new(f"set-columns", GLib.VariantType.new("i"))
            action.connect("activate", self.on_set_columns)
            self.add_action(action)
        
        window.present()
        
    def on_set_columns(self, action, parameter):
        """Handle column count change"""
        count = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_count(count)

if __name__ == "__main__":
    import sys
    
    app = EpubViewerApp()
    
    # Handle cleanup on exit
    def cleanup_handler(signum, frame):
        window = app.get_active_window()
        if window:
            window.cleanup()
        sys.exit(0)
    
    import signal
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    
    try:
        app.run(sys.argv)
    finally:
        # Cleanup on normal exit
        window = app.get_active_window()
        if window:
            window.cleanup()
