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

class EPUBViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        
        # Main layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)
        
        # Header bar
        self.header_bar = Adw.HeaderBar()
        self.main_box.append(self.header_bar)
        
        # Open file button
        self.open_button = Gtk.Button(icon_name="document-open-symbolic")
        self.open_button.connect("clicked", self.open_file_dialog)
        self.header_bar.pack_start(self.open_button)
        
        # Column selection button
        self.column_button = Gtk.Button(label="Columns: 1")
        self.column_button.connect("clicked", self.show_column_selector)
        self.header_bar.pack_start(self.column_button)
        
        # Navigation buttons
        self.prev_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_button.connect("clicked", self.prev_chapter)
        self.header_bar.pack_end(self.prev_button)
        
        self.next_button = Gtk.Button(icon_name="go-next-symbolic")
        self.next_button.connect("clicked", self.next_chapter)
        self.header_bar.pack_end(self.next_button)
        
        # Chapter label
        self.chapter_label = Gtk.Label(label="Chapter 1/1")
        self.header_bar.pack_end(self.chapter_label)
        
        # WebKit WebView for content
        self.webview = WebKit.WebView()
        
        # Scrolled window for column snapping
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_child(self.webview)
        self.main_box.append(self.scrolled)
        
        # Column settings
        self.columns = 1
        self.padding = 20  # pixels
        
        # EPUB data
        self.book = None
        self.current_spine_item = 0
        self.spine_items = []
        
        # Set up scrolling adjustment
        self.scrolling = False
        self.adjustment = self.scrolled.get_hadjustment()
        self.adjustment.connect("value-changed", self.on_scroll_changed)
        
        # Initialize with empty view
        self.webview.load_html("<html><body><p>Open an EPUB file to begin reading</p></body></html>")

    def open_file_dialog(self, button):
        """Open file dialog to select EPUB file"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Open EPUB File")
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        epub_filter = Gtk.FileFilter()
        epub_filter.add_pattern("*.epub")
        epub_filter.set_name("EPUB files")
        filter_list.append(epub_filter)
        all_filter = Gtk.FileFilter()
        all_filter.add_pattern("*")
        all_filter.set_name("All files")
        filter_list.append(all_filter)
        dialog.set_filters(filter_list)
        
        dialog.open(self, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        """Handle file selection"""
        try:
            file = dialog.open_finish(result)
            self.load_epub(file.get_path())
        except GLib.Error:
            pass  # User cancelled

    def load_epub(self, epub_path):
        """Load EPUB file and display first chapter"""
        try:
            self.book = epub.read_epub(epub_path)
            
            # Get all document items (compatible with older ebooklib versions)
            self.spine_items = []
            for item in self.book.get_items():
                if hasattr(item, 'get_type') and item.get_type() == epub.ITEM_DOCUMENT:
                    self.spine_items.append(item)
                elif hasattr(item, 'media_type') and 'xhtml' in item.media_type.lower():
                    self.spine_items.append(item)
            
            # If no items found, try using spine directly
            if not self.spine_items and hasattr(self.book, 'spine'):
                for spine_item in self.book.spine:
                    if isinstance(spine_item, tuple) and len(spine_item) > 0:
                        item = spine_item[0]
                        if hasattr(item, 'get_type') and item.get_type() == epub.ITEM_DOCUMENT:
                            self.spine_items.append(item)
                        elif hasattr(item, 'media_type') and 'xhtml' in item.media_type.lower():
                            self.spine_items.append(item)
            
            if self.spine_items:
                self.current_spine_item = 0
                self.update_navigation_labels()
                self.display_spine_item(0)
            else:
                self.webview.load_html("<html><body><p>No readable chapters found in this EPUB</p></body></html>")
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def display_spine_item(self, index):
        """Display a specific spine item (chapter)"""
        if not self.book or index >= len(self.spine_items):
            return
            
        item = self.spine_items[index]
        try:
            content = item.get_content().decode('utf-8')
        except Exception as e:
            content = f"<p>Error decoding content: {str(e)}</p>"
        
        # Wrap content in column-ready HTML
        wrapped_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    margin: 0;
                    padding: {self.padding}px;
                    font-family: serif;
                    line-height: 1.6;
                    width: {self.columns * 600}px;  /* Ensure enough width for columns */
                }}
                .container {{
                    column-count: {self.columns};
                    column-gap: {self.padding * 2}px;
                    width: 100%;
                    height: 100vh;
                }}
                .content {{
                    height: 100%;
                    column-break-inside: avoid;
                    page-break-inside: avoid;
                    break-inside: avoid;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="content">{content}</div>
            </div>
        </body>
        </html>
        """
        
        self.webview.load_html(wrapped_content)

    def show_column_selector(self, button):
        """Show column selection popover"""
        popover = Gtk.Popover()
        
        # Create grid for column options
        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(6)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)
        
        # Add column options (1-10)
        for i in range(1, 11):
            btn = Gtk.Button(label=str(i))
            btn.connect("clicked", self.change_columns, i, popover)
            col = (i - 1) % 5
            row = (i - 1) // 5
            grid.attach(btn, col, row, 1, 1)
        
        popover.set_child(grid)
        popover.set_parent(button)
        popover.popup()

    def change_columns(self, button, columns, popover):
        """Change number of columns"""
        self.columns = columns
        self.column_button.set_label(f"Columns: {columns}")
        self.display_spine_item(self.current_spine_item)
        popover.popdown()
        
        # Snap to current column after changing layout
        GLib.timeout_add(100, self.snap_to_column)

    def on_scroll_changed(self, adjustment):
        """Handle scroll changes for snapping"""
        if self.scrolling:
            return
            
        # Calculate current column based on scroll position
        page_width = adjustment.get_page_size()
        if page_width <= 0:
            return
            
        value = adjustment.get_value()
        column_width = page_width / self.columns
        if column_width <= 0:
            return
            
        target_column = int(value / column_width)
        
        # Snap to nearest column boundary
        target_pos = target_column * column_width
        if abs(value - target_pos) > column_width / 2:
            target_pos += column_width
            
        if abs(adjustment.get_value() - target_pos) > 1:
            self.scrolling = True
            adjustment.set_value(target_pos)
            GLib.timeout_add(100, self.reset_scrolling)

    def reset_scrolling(self):
        self.scrolling = False
        return False

    def snap_to_column(self):
        """Snap to current column position"""
        adj = self.scrolled.get_hadjustment()
        page_width = adj.get_page_size()
        if page_width <= 0:
            return False
            
        column_width = page_width / self.columns
        if column_width <= 0:
            return False
            
        current_col = int(adj.get_value() / column_width)
        target_pos = current_col * column_width
        adj.set_value(target_pos)
        return False

    def next_chapter(self, button):
        """Go to next chapter"""
        if self.current_spine_item < len(self.spine_items) - 1:
            self.current_spine_item += 1
            self.display_spine_item(self.current_spine_item)
            self.update_navigation_labels()

    def prev_chapter(self, button):
        """Go to previous chapter"""
        if self.current_spine_item > 0:
            self.current_spine_item -= 1
            self.display_spine_item(self.current_spine_item)
            self.update_navigation_labels()

    def update_navigation_labels(self):
        """Update chapter navigation labels"""
        if self.spine_items:
            self.chapter_label.set_text(f"Chapter {self.current_spine_item + 1}/{len(self.spine_items)}")
            self.prev_button.set_sensitive(self.current_spine_item > 0)
            self.next_button.set_sensitive(self.current_spine_item < len(self.spine_items) - 1)
        else:
            self.chapter_label.set_text("Chapter 0/0")
            self.prev_button.set_sensitive(False)
            self.next_button.set_sensitive(False)

    def show_error(self, message):
        """Show error message in dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Error",
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.EPUBViewer")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = EPUBViewer(app)
        win.present()

if __name__ == "__main__":
    app = Application()
    app.run(None)
