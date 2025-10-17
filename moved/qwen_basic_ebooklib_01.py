import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, GLib
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import tempfile
import webbrowser

class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(800, 600)
        self.set_title("EPUB Viewer")
        
        # Initialize variables
        self.book = None
        self.current_item = None
        self.temp_dir = None
        self.css_content = ""
        
        # Create UI
        self.setup_ui()
        
    def setup_ui(self):
        # Main content box
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)
        
        # Header bar
        self.header_bar = Adw.HeaderBar()
        self.main_box.append(self.header_bar)
        
        # Open button
        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.set_tooltip_text("Open EPUB")
        self.open_btn.connect("clicked", self.open_file)
        self.header_bar.pack_start(self.open_btn)
        
        # Previous button
        self.prev_btn = Gtk.Button(label="Previous")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)
        self.header_bar.pack_start(self.prev_btn)
        
        # Next button
        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)
        self.header_bar.pack_end(self.next_btn)
        
        # Progress bar
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.header_bar.set_title_widget(self.progress)
        
        # Scrolled window for content
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)  # Expand vertically to fill available space
        self.main_box.append(self.scrolled)
        
        # Web view for displaying content
        import sys
        try:
            gi.require_version('WebKit', '6.0')
            from gi.repository import WebKit
            self.webview = WebKit.WebView()
            self.scrolled.set_child(self.webview)
        except ValueError:
            # Fallback to TextView if WebKit is not available
            self.textview = Gtk.TextView()
            self.textview.set_editable(False)
            self.textview.set_cursor_visible(False)
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)
            self.webview = None

    def open_file(self, button):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        
        epub_filter = Gtk.FileFilter()
        epub_filter.add_pattern("*.epub")
        epub_filter.add_pattern("*.EPUB")
        epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)
        
        all_filter = Gtk.FileFilter()
        all_filter.add_pattern("*")
        all_filter.set_name("All Files")
        filter_list.append(all_filter)
        
        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            self.load_epub(path)
        except GLib.Error:
            pass

    def load_epub(self, path):
        try:
            self.book = epub.read_epub(path)
            self.items = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            
            if not self.items:
                self.show_error("No documents found in EPUB")
                return
            
            # Create temporary directory for resources
            if self.temp_dir:
                import shutil
                shutil.rmtree(self.temp_dir)
            self.temp_dir = tempfile.mkdtemp()
            
            # Extract resources
            for item in self.book.get_items_of_type(ebooklib.ITEM_IMAGE):
                with open(os.path.join(self.temp_dir, item.get_name()), 'wb') as f:
                    f.write(item.get_content())
            
            # Extract CSS content
            self.extract_css()
            
            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def extract_css(self):
        """Extract all CSS from the EPUB"""
        self.css_content = ""
        for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
            self.css_content += item.get_content().decode('utf-8') + "\n"

    def display_page(self):
        if not self.book or not self.items:
            return
            
        item = self.items[self.current_index]
        self.current_item = item
        
        # Process content
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        
        # Handle relative paths for images and resources
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('xlink:href')
            if src:
                # Convert to absolute path
                abs_path = os.path.join(self.temp_dir, src)
                if os.path.exists(abs_path):
                    img['src'] = f"file://{abs_path}"
        
        # Convert to string with proper formatting
        content = str(soup)
        
        # Combine CSS with content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ 
                    font-family: sans-serif; 
                    margin: 20px; 
                    line-height: 1.6;
                    color: #333;
                }}
                img {{ max-width: 100%; height: auto; }}
                h1, h2, h3, h4, h5, h6 {{ color: #222; }}
                {self.css_content}
            </style>
        </head>
        <body>{content}</body>
        </html>
        """
        
        if self.webview:
            self.webview.load_html(html_content)
        else:
            # Fallback to textview
            buffer = self.textview.get_buffer()
            buffer.set_text(content)

        # Update progress
        progress = (self.current_index + 1) / len(self.items)
        self.progress.set_fraction(progress)
        self.progress.set_text(f"Page {self.current_index + 1} of {len(self.items)}")

    def update_navigation(self):
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.items) - 1)

    def next_page(self, button):
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
            self.update_navigation()
            self.display_page()

    def prev_page(self, button):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_navigation()
            self.display_page()

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(
            self,
            "Error",
            message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.EpubViewer')
        self.create_action('quit', self.quit, ['<primary>q'])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPubViewer(self)
        win.present()

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

def main():
    app = Application()
    return app.run(None)

if __name__ == "__main__":
    main()
