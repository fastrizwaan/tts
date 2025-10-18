#!/usr/bin/env python3
"""
EPUB Viewer Application

A GTK4-based EPUB reader with support for customizable column layouts, 
typography settings, and navigation. Uses WebKit for rendering EPUB content
with CSS-based column formatting for a paginated reading experience.

Features:
- Table of Contents sidebar
- Multi-column and single-column layouts
- Customizable fonts, sizes, margins, and line heights
- Keyboard and mouse navigation
- Automatic chapter navigation
"""

import os
import tempfile
import shutil
import sys
import urllib.parse
from typing import Optional, List, Tuple

# Disable compositing mode for better WebKit compatibility
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')

from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Gdk
from ebooklib import epub


class EPUBViewer(Adw.Application):
    """
    Main application class for the EPUB viewer.
    
    Manages the GTK application lifecycle and stores global application state
    including the loaded EPUB book, table of contents, and user preferences
    for typography and layout.
    """
    
    def __init__(self):
        """
        Initialize the EPUB viewer application.
        
        Sets up the application ID, initializes all state variables including
        the book object, table of contents, temporary directory for extracted
        files, and default typography/layout settings.
        """
        super().__init__(application_id='com.example.EPUBViewer',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        
        # Book content and navigation state
        self.book: Optional[epub.EpubBook] = None  # Currently loaded EPUB
        self.toc: List[Tuple[str, str]] = []  # Table of contents [(title, href), ...]
        self.temp_dir: Optional[str] = None  # Temporary directory for extracted EPUB files
        self.current_href: Optional[str] = None  # Current chapter/file being displayed
        self.current_spine_index: int = -1  # Index in the spine (reading order)

        # Typography settings
        self.font_family = "Serif"  # Font family: Serif, Sans, or Monospace
        self.font_size = 16  # Base font size in pixels
        self.line_height = 1.6  # Line height multiplier
        self.margin = 30  # Page margin in pixels
        
        # Column layout settings
        self.columns = 2  # Number of columns (for fixed count mode)
        self.column_width = 400  # Column width in pixels (for fixed width mode)
        self.column_gap = 20  # Gap between columns in pixels
        self.use_fixed_columns = True  # True = fixed count, False = fixed width

    def do_activate(self):
        """
        Activate the application and show the main window.
        
        Called when the application is launched or activated. Creates a new
        window if one doesn't exist, or presents the existing window.
        This is part of the GTK application lifecycle.
        """
        win = self.props.active_window
        if not win:
            win = EPUBWindow(application=self)
        win.present()


class EPUBWindow(Adw.ApplicationWindow):
    """
    Main application window for the EPUB viewer.
    
    Provides the user interface including:
    - Table of contents sidebar
    - WebKit view for rendering EPUB content
    - Toolbar with file open, navigation, and settings controls
    - Keyboard and scroll event handlers for navigation
    """
    
    def __init__(self, **kwargs):
        """
        Initialize the main window and set up the user interface.
        
        Creates the split view layout with sidebar (TOC) and content area
        (toolbar + WebView). Sets up event controllers for keyboard and
        scroll navigation.
        """
        super().__init__(**kwargs)
        self.app = kwargs['application']
        self.set_default_size(1000, 700)
        self.set_title("EPUB Viewer")

        # Create split view with sidebar for TOC
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_max_sidebar_width(300)
        self.split_view.set_min_sidebar_width(200)
        self.split_view.set_show_sidebar(True)

        # Sidebar: Table of Contents
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        toc_header = Adw.HeaderBar()
        toc_label = Gtk.Label(label="Table of Contents")
        toc_label.add_css_class("title")
        toc_header.set_title_widget(toc_label)
        sidebar_box.append(toc_header)
        
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect("row-activated", self.on_toc_row_activated)
        sidebar_scrolled = Gtk.ScrolledWindow()
        sidebar_scrolled.set_child(self.toc_list)
        sidebar_scrolled.set_vexpand(True)
        sidebar_box.append(sidebar_scrolled)

        # Content area: Toolbar + WebView
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.setup_toolbar()
        self.setup_webview()

        self.split_view.set_sidebar(sidebar_box)
        self.split_view.set_content(self.content_box)
        self.set_content(self.split_view)
        
        # Set up keyboard navigation
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

    def setup_toolbar(self):
        """
        Create and configure the application toolbar.
        
        Sets up:
        - Sidebar toggle button
        - File open button
        - Previous/Next navigation buttons
        - Settings menu with typography and layout controls
        
        The settings menu includes controls for font family, size, line height,
        margins, column mode, column count, column width, and column gap.
        """
        header = Adw.HeaderBar()
        
        # Sidebar toggle
        toggle_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        toggle_btn.set_active(True)
        toggle_btn.connect("toggled", lambda btn: self.split_view.set_show_sidebar(btn.get_active()))
        header.pack_start(toggle_btn)
        
        # Open file button
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        # Navigation buttons
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.connect("clicked", lambda *_: self.scroll_viewport(-1))
        self.prev_btn.set_sensitive(False)
        header.pack_start(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.connect("clicked", lambda *_: self.scroll_viewport(1))
        self.next_btn.set_sensitive(False)
        header.pack_end(self.next_btn)

        # Settings menu
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        popover = Gtk.Popover()

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(10)
        grid.set_margin_end(10)
        grid.set_margin_top(10)
        grid.set_margin_bottom(10)

        # Font family selector
        font_label = Gtk.Label(label="Font:", halign=Gtk.Align.START)
        font_model = Gtk.StringList()
        for f in ["Serif", "Sans", "Monospace"]:
            font_model.append(f)
        self.font_dropdown = Gtk.DropDown(model=font_model)
        self.font_dropdown.set_selected(0)
        self.font_dropdown.connect("notify::selected", self.on_font_changed)
        grid.attach(font_label, 0, 0, 1, 1)
        grid.attach(self.font_dropdown, 1, 0, 1, 1)

        # Font size spinner
        size_label = Gtk.Label(label="Size:", halign=Gtk.Align.START)
        size_adj = Gtk.Adjustment(value=self.app.font_size, lower=8, upper=48, step_increment=1)
        self.size_spin = Gtk.SpinButton(adjustment=size_adj, numeric=True)
        self.size_spin.connect("value-changed", self.on_font_size_changed)
        grid.attach(size_label, 0, 1, 1, 1)
        grid.attach(self.size_spin, 1, 1, 1, 1)

        # Line height spinner
        lh_label = Gtk.Label(label="Line Height:", halign=Gtk.Align.START)
        lh_adj = Gtk.Adjustment(value=self.app.line_height, lower=0.8, upper=3.0, step_increment=0.1)
        self.lh_spin = Gtk.SpinButton(adjustment=lh_adj, digits=1, numeric=True)
        self.lh_spin.connect("value-changed", self.on_line_height_changed)
        grid.attach(lh_label, 0, 2, 1, 1)
        grid.attach(self.lh_spin, 1, 2, 1, 1)

        # Margin spinner
        margin_label = Gtk.Label(label="Margin:", halign=Gtk.Align.START)
        margin_adj = Gtk.Adjustment(value=self.app.margin, lower=0, upper=100, step_increment=5)
        self.margin_spin = Gtk.SpinButton(adjustment=margin_adj, numeric=True)
        self.margin_spin.connect("value-changed", self.on_margin_changed)
        grid.attach(margin_label, 0, 3, 1, 1)
        grid.attach(self.margin_spin, 1, 3, 1, 1)

        # Column mode selector
        mode_label = Gtk.Label(label="Col Mode:", halign=Gtk.Align.START)
        mode_model = Gtk.StringList()
        mode_model.append("Fixed Count")
        mode_model.append("Fixed Width")
        self.mode_dropdown = Gtk.DropDown(model=mode_model)
        self.mode_dropdown.set_selected(0 if self.app.use_fixed_columns else 1)
        self.mode_dropdown.connect("notify::selected", self.on_mode_changed)
        grid.attach(mode_label, 0, 4, 1, 1)
        grid.attach(self.mode_dropdown, 1, 4, 1, 1)

        # Column count spinner
        col_label = Gtk.Label(label="Columns:", halign=Gtk.Align.START)
        col_adj = Gtk.Adjustment(value=self.app.columns, lower=1, upper=5, step_increment=1)
        self.col_spin = Gtk.SpinButton(adjustment=col_adj, numeric=True)
        self.col_spin.connect("value-changed", self.on_columns_changed)
        grid.attach(col_label, 0, 5, 1, 1)
        grid.attach(self.col_spin, 1, 5, 1, 1)

        # Column width spinner
        cw_label = Gtk.Label(label="Col Width:", halign=Gtk.Align.START)
        cw_adj = Gtk.Adjustment(value=self.app.column_width, lower=200, upper=800, step_increment=10)
        self.cw_spin = Gtk.SpinButton(adjustment=cw_adj, numeric=True)
        self.cw_spin.connect("value-changed", self.on_column_width_changed)
        grid.attach(cw_label, 0, 6, 1, 1)
        grid.attach(self.cw_spin, 1, 6, 1, 1)
        
        # Column gap spinner
        gap_label = Gtk.Label(label="Col Gap:", halign=Gtk.Align.START)
        gap_adj = Gtk.Adjustment(value=self.app.column_gap, lower=5, upper=50, step_increment=5)
        self.gap_spin = Gtk.SpinButton(adjustment=gap_adj, numeric=True)
        self.gap_spin.connect("value-changed", self.on_column_gap_changed)
        grid.attach(gap_label, 0, 7, 1, 1)
        grid.attach(self.gap_spin, 1, 7, 1, 1)

        popover.set_child(grid)
        menu_btn.set_popover(popover)
        header.pack_end(menu_btn)

        self.content_box.append(header)

    def setup_webview(self):
        """
        Create and configure the WebKit web view for EPUB rendering.
        
        Sets up:
        - WebView widget for displaying HTML/XHTML content
        - ScrolledWindow container for scroll management
        - Event controllers for scroll and keyboard navigation
        - Adjustment listeners to update navigation button states
        
        The WebView is where the actual EPUB content is rendered using WebKit.
        """
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_child(self.webview)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        
        # Set up scroll event controller for mouse wheel navigation
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.scrolled_window.add_controller(scroll_controller)
        
        # Connect adjustment listeners to update navigation buttons
        self.scrolled_window.get_hadjustment().connect("value-changed", self.update_nav_buttons)
        self.scrolled_window.get_vadjustment().connect("value-changed", self.update_nav_buttons)
        
        self.content_box.append(self.scrolled_window)

    def on_key_pressed(self, controller, keyval, keycode, state):
        """
        Handle keyboard navigation events.
        
        Args:
            controller: The EventControllerKey that received the event
            keyval: The key that was pressed (from Gdk.KEY_* constants)
            keycode: Hardware key code
            state: Modifier state (Shift, Ctrl, etc.)
        
        Returns:
            bool: True if the event was handled, False otherwise
        
        Supported keys:
        - Page Down / Space: Next page
        - Page Up: Previous page
        - Right Arrow: Next page
        - Left Arrow: Previous page
        """
        if keyval == Gdk.KEY_Page_Down or keyval == Gdk.KEY_space:
            self.scroll_viewport(1)
            return True
        elif keyval == Gdk.KEY_Page_Up:
            self.scroll_viewport(-1)
            return True
        elif keyval == Gdk.KEY_Right:
            self.scroll_viewport(1)
            return True
        elif keyval == Gdk.KEY_Left:
            self.scroll_viewport(-1)
            return True
        return False

    def on_scroll_event(self, controller, dx, dy):
        """
        Handle mouse wheel scroll events for page navigation.
        
        Args:
            controller: The EventControllerScroll that received the event
            dx: Horizontal scroll delta
            dy: Vertical scroll delta
        
        Returns:
            bool: True if the event was handled, False otherwise
        
        Converts vertical scroll wheel movements into page-by-page navigation.
        Positive dy (scroll down) moves forward, negative (scroll up) moves back.
        """
        if abs(dy) > 0.1:
            direction = 1 if dy > 0 else -1
            self.scroll_viewport(direction)
            return True
        return False

    def on_open_clicked(self, button):
        """
        Handle the file open button click.
        
        Args:
            button: The button that was clicked
        
        Opens a file chooser dialog filtered to show EPUB files (.epub extension).
        When a file is selected, loads it using load_epub().
        """
        dialog = Gtk.FileDialog()
        
        # Create EPUB file filter
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        
        filter_store = Gio.ListStore.new(Gtk.FileFilter)
        filter_store.append(epub_filter)
        
        # Add "All files" filter
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filter_store.append(all_filter)
        
        dialog.set_filters(filter_store)
        dialog.set_default_filter(epub_filter)
        
        dialog.open(self, None, self.on_open_response)

    def on_open_response(self, dialog, result):
        """
        Handle the file chooser dialog response.
        
        Args:
            dialog: The FileDialog that was displayed
            result: The async result object
        
        Called when the user selects a file or cancels the dialog.
        If a file was selected, extracts its path and loads the EPUB.
        """
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                self.load_epub(path)
        except GLib.Error:
            pass  # User cancelled the dialog

    def load_epub(self, path: str):
        """
        Load an EPUB file and display its first chapter.
        
        Args:
            path: Filesystem path to the EPUB file
        
        Process:
        1. Read the EPUB file using ebooklib
        2. Extract the table of contents
        3. Populate the TOC sidebar
        4. Load the first chapter from the spine (reading order)
        5. If spine is empty, try the first TOC entry
        
        The EPUB spine defines the linear reading order of the book.
        """
        try:
            self.app.book = epub.read_epub(path)
            self.app.toc = self.extract_toc(self.app.book.toc)
            self.populate_toc()
            
            # Find first item in spine (reading order)
            first_href = None
            if self.app.book.spine:
                first_item_id = self.app.book.spine[0][0]
                first_item = self.app.book.get_item_with_id(first_item_id)
                if first_item:
                    first_href = first_item.get_name()
                    
            # Load first chapter
            if first_href:
                self.load_href(first_href)
            elif self.app.toc:
                self.load_href(self.app.toc[0][1])
            else:
                print("No content found in spine or TOC.", file=sys.stderr)

        except Exception as e:
            print(f"EPUB load error: {e}", file=sys.stderr)

    def extract_toc(self, toc_items, base="") -> List[Tuple[str, str]]:
        """
        Recursively extract table of contents from EPUB metadata.
        
        Args:
            toc_items: List of TOC items from the EPUB (may be nested)
            base: Base URL for resolving relative hrefs
        
        Returns:
            List of (title, href) tuples representing the flattened TOC
        
        EPUB TOC can be hierarchical with sections and subsections.
        This method flattens it into a simple list while preserving order.
        Handles both epub.Link objects and nested tuple structures.
        """
        result = []
        for item in toc_items:
            if isinstance(item, epub.Link):
                href = urllib.parse.urljoin(base, item.href)
                result.append((item.title, href))
            elif isinstance(item, tuple) and len(item) >= 2:
                # Nested section: (Link, [children])
                if isinstance(item[0], epub.Link):
                    href = urllib.parse.urljoin(base, item[0].href)
                    result.append((item[0].title, href))
                result.extend(self.extract_toc(item[1], base))
            elif isinstance(item, list):
                result.extend(self.extract_toc(item, base))
        return result

    def populate_toc(self):
        """
        Populate the TOC sidebar with entries from the loaded EPUB.
        
        Clears any existing TOC entries and creates new list rows for each
        entry in the table of contents. Each row stores the href as a custom
        attribute for navigation when clicked.
        """
        # Clear existing TOC entries
        while True:
            row = self.toc_list.get_row_at_index(0)
            if row:
                self.toc_list.remove(row)
            else:
                break
        
        # Add new TOC entries
        for title, href in self.app.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=title, xalign=0, margin_start=10, margin_top=5, margin_bottom=5, ellipsize=2, wrap=True)
            row.set_child(label)
            row.href = href  # Store href for navigation
            self.toc_list.append(row)

    def get_spine_index(self, href: str) -> int:
        """
        Find the spine index for a given href.
        
        Args:
            href: The href to search for (may include fragment identifier)
        
        Returns:
            int: The spine index (0-based), or -1 if not found
        
        The spine defines the linear reading order. This is used to enable
        sequential navigation (next/previous chapter) and determine position
        in the book for navigation button states.
        """
        if not self.app.book: return -1
        clean_href = href.split('#')[0].lstrip('./')

        for i, (item_id, _) in enumerate(self.app.book.spine):
            item = self.app.book.get_item_with_id(item_id)
            if item and item.get_name() == clean_href:
                return i
        return -1

    def load_href(self, href: str):
        """
        Load and display a specific EPUB content file.
        
        Args:
            href: The href to load (relative path within EPUB, may include #fragment)
        
        Process:
        1. Find the spine index for navigation tracking
        2. Locate the content item in the EPUB
        3. Extract ALL EPUB files to a temporary directory (needed for images, CSS, etc.)
        4. Load the HTML content in the WebView
        5. Apply custom layout CSS
        
        The temporary directory is recreated each time to ensure clean state.
        All EPUB resources are extracted so relative links work correctly.
        """
        if not self.app.book:
            return

        clean_href = href.split('#')[0]
        self.app.current_spine_index = self.get_spine_index(clean_href)

        # Find the content item
        item = self.app.book.get_item_with_href(clean_href)
        if not item:
            # Fallback: search by name
            for it in self.app.book.get_items():
                if it.get_name() == clean_href:
                    item = it
                    break
        if not item:
            print(f"Content item not found for href: {clean_href}", file=sys.stderr)
            return

        self.app.current_href = clean_href
        
        # Clean up old temp directory
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        self.app.temp_dir = tempfile.mkdtemp()

        # Extract all EPUB files to temp directory
        # This ensures images, stylesheets, and other resources are available
        for it in self.app.book.get_items():
            try:
                dest = os.path.join(self.app.temp_dir, it.get_name())
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    f.write(it.content) 
            except Exception as e:
                print(f"Error saving file {it.get_name()}: {e}", file=sys.stderr)

        # Build file:// URI for the content
        full_path = os.path.join(self.app.temp_dir, item.get_name())
        uri = f"file://{full_path}"
        
        # Preserve fragment identifier (for in-page anchors)
        fragment = href.split('#')[1] if '#' in href else ''
        if fragment:
            uri += f"#{fragment}"

        self.webview.load_uri(uri)

    def on_toc_row_activated(self, listbox, row):
        """
        Handle TOC entry selection.
        
        Args:
            listbox: The ListBox containing TOC entries
            row: The selected ListBoxRow
        
        When a user clicks a TOC entry, load the corresponding content.
        The href is stored as a custom attribute on the row.
        """
        if hasattr(row, 'href'):
            self.load_href(row.href)

    def on_webview_load_changed(self, webview, load_event):
        """
        Handle WebView load state changes.
        
        Args:
            webview: The WebView that finished loading
            load_event: The load event type (STARTED, REDIRECTED, COMMITTED, FINISHED)
        
        When content finishes loading:
        1. Apply custom CSS layout (columns, fonts, etc.)
        2. Reset scroll position to top-left
        3. Update navigation button states
        
        The slight delay ensures the DOM is fully ready before applying styles.
        """
        if load_event == WebKit.LoadEvent.FINISHED:
            self.apply_layout()
            GLib.timeout_add(100, self.reset_scroll_position)

    def reset_scroll_position(self):
        """
        Reset scroll position to the top-left corner (start of content).
        
        Returns:
            bool: False to prevent repeated timer callbacks
        
        Called after loading new content to ensure the user starts at the
        beginning. Also updates navigation buttons to reflect the new position.
        
        Behavior differs by layout mode:
        - Multi-column: Uses JavaScript for precise left-alignment (horizontal)
        - Single-column: Uses GTK adjustment for top-alignment (vertical)
        """
        if self.app.columns > 1 or not self.app.use_fixed_columns:
            # Multi-column mode: use JavaScript for precise horizontal reset
            js_code = """
            (function() {
                const body = document.scrollingElement || document.body;
                body.scrollTo({
                    left: 0,
                    top: 0,
                    behavior: 'auto'
                });
            })();
            """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except:
                # Fallback to GTK adjustments
                if self.scrolled_window.get_hadjustment().get_value() != 0:
                    self.scrolled_window.get_hadjustment().set_value(0)
                if self.scrolled_window.get_vadjustment().get_value() != 0:
                    self.scrolled_window.get_vadjustment().set_value(0)
        else:
            # Single-column mode: use GTK adjustment for vertical reset
            if self.scrolled_window.get_vadjustment().get_value() != 0:
                self.scrolled_window.get_vadjustment().set_value(0)
            # Also reset horizontal just in case
            if self.scrolled_window.get_hadjustment().get_value() != 0:
                self.scrolled_window.get_hadjustment().set_value(0)
        
        self.update_nav_buttons()
        return False

    def apply_layout(self):
        """
        Apply custom CSS styling to the WebView content.
        
        Injects CSS that controls:
        - Font family, size, and line height
        - Page margins
        - Column layout (single column vs multi-column)
        - Column width and gap
        - Image sizing and positioning
        - Heading and paragraph styling
        
        The CSS uses !important to override EPUB's built-in styles.
        Different CSS is generated based on column mode:
        - Single column: Vertical scrolling with normal text flow
        - Multi-column: Horizontal scrolling with CSS columns
        
        Column modes:
        - Fixed count: Uses column-count (e.g., always 2 columns)
        - Fixed width: Uses column-width (responsive column count)
        """
        margin = self.app.margin
        font_family = self.app.font_family
        font_size = self.app.font_size
        line_height = self.app.line_height
        columns = self.app.columns
        col_width = self.app.column_width
        column_gap = self.app.column_gap
        use_fixed = self.app.use_fixed_columns

        if columns > 1 or not use_fixed:
            # Multi-column layout for paginated reading experience
            if use_fixed:
                # Fixed column count mode
                css = f"""
                    html {{
                        margin: 0 !important;
                        padding: 0 !important;
                        width: 100% !important;
                        height: 100% !important;
                        overflow: hidden !important;
                    }}
                    
                    body {{
                        margin: 0 !important;
                        padding: {margin}px !important;
                        box-sizing: border-box !important;
                        font-family: "{font_family}", serif !important;
                        font-size: {font_size}px !important;
                        line-height: {line_height} !important;
                        
                        height: calc(100vh - {margin * 2}px) !important;
                        
                        column-count: {columns} !important;
                        column-gap: {column_gap}px !important;
                        column-fill: auto !important;
                        
                        word-wrap: normal !important;
                        overflow-wrap: normal !important;
                        hyphens: none !important;
                    }}
                """
            else:
                # Fixed column width mode (responsive column count)
                css = f"""
                    html {{
                        margin: 0 !important;
                        padding: 0 !important;
                        width: 100% !important;
                        height: 100% !important;
                        overflow: hidden !important;
                    }}
                    
                    body {{
                        margin: 0 !important;
                        padding: {margin}px !important;
                        box-sizing: border-box !important;
                        font-family: "{font_family}", serif !important;
                        font-size: {font_size}px !important;
                        line-height: {line_height} !important;
                        
                        height: calc(100vh - {margin * 2}px) !important;
                        
                        column-width: {col_width}px !important;
                        column-gap: {column_gap}px !important;
                        column-fill: auto !important;
                        
                        word-wrap: normal !important;
                        overflow-wrap: normal !important;
                        hyphens: none !important;
                    }}
                """
            
            # Common styles for multi-column layouts
            css += f"""
                * {{
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    box-sizing: border-box !important;
                }}
                
                img {{
                    max-width: 100% !important;
                    height: auto !important;
                    display: block !important;
                    margin: 10px auto !important;
                    -webkit-column-break-inside: avoid !important;
                    page-break-inside: avoid !important;
                    break-inside: avoid !important;
                }}
                
                h1, h2, h3, h4, h5, h6 {{
                    -webkit-column-break-after: avoid !important;
                    page-break-after: avoid !important;
                    break-after: avoid !important;
                    margin-top: 1em !important;
                    margin-bottom: 0.5em !important;
                }}
                
                p {{
                    margin: 0.5em 0 !important;
                    orphans: 3 !important;
                    widows: 3 !important;
                }}
                
                blockquote {{
                    margin: 1em 0 !important;
                    padding-left: 1em !important;
                    border-left: 3px solid #ccc !important;
                }}
                
                div, section, article {{
                    max-width: 100% !important;
                }}
            """
        else:
            # Single column vertical scrolling layout
            css = f"""
                html {{
                    margin: 0 !important;
                    padding: 0 !important;
                    width: 100% !important;
                    height: 100% !important;
                }}
                
                body {{
                    margin: 0 !important;
                    padding: {margin}px !important;
                    box-sizing: border-box !important;
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    width: 100% !important;
                }}
                
                * {{
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                }}
                
                img {{
                    max-width: 100% !important;
                    height: auto !important;
                    display: block !important;
                    margin: 10px 0 !important;
                }}
                
                h1, h2, h3, h4, h5, h6 {{
                    margin-top: 1em !important;
                    margin-bottom: 0.5em !important;
                }}
                
                p {{
                    margin: 0.5em 0 !important;
                }}
                
                blockquote {{
                    margin: 1em 0 !important;
                    padding-left: 1em !important;
                    border-left: 3px solid #ccc !important;
                }}
            """

        # Escape CSS for JavaScript injection
        css_escaped = css.replace("\\", "\\\\").replace("`", "\\`")

        # JavaScript to inject the CSS into the document
        js_inject = f"""
        (function() {{
            let old = document.getElementById('epub-viewer-style');
            if (old) old.remove();
            let style = document.createElement('style');
            style.id = 'epub-viewer-style';
            style.textContent = `{css_escaped}`;
            document.documentElement.appendChild(style);
        }})();
        """

        # Execute JavaScript (try different API versions for compatibility)
        try:
            self.webview.evaluate_javascript(js_inject, -1, None, None, None)
        except:
            try:
                self.webview.evaluate_javascript(js_inject, -1, None, None, None, None)
            except:
                pass

        # Set scroll policy based on layout mode
        if columns == 1 and use_fixed:
            # Single column: vertical scrolling
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        else:
            # Multi-column: horizontal scrolling
            self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            
        # Update navigation buttons after layout change
        GLib.timeout_add(100, self.update_nav_buttons)

    def on_font_changed(self, dropdown, _pspec):
        """Handle font family dropdown change."""
        families = ["Serif", "Sans", "Monospace"]
        self.app.font_family = families[dropdown.get_selected()]
        self.apply_layout()

    def on_font_size_changed(self, spin):
        """Handle font size spinner change."""
        self.app.font_size = int(spin.get_value())
        self.apply_layout()

    def on_line_height_changed(self, spin):
        """Handle line height spinner change."""
        self.app.line_height = spin.get_value()
        self.apply_layout()

    def on_margin_changed(self, spin):
        """Handle margin spinner change."""
        self.app.margin = int(spin.get_value())
        self.apply_layout()

    def on_mode_changed(self, dropdown, _pspec):
        """Handle column mode dropdown change (Fixed Count vs Fixed Width)."""
        self.app.use_fixed_columns = (dropdown.get_selected() == 0)
        self.apply_layout()

    def on_columns_changed(self, spin):
        """Handle column count spinner change (for Fixed Count mode)."""
        self.app.columns = int(spin.get_value())
        self.apply_layout()

    def on_column_width_changed(self, spin):
        """Handle column width spinner change (for Fixed Width mode)."""
        self.app.column_width = int(spin.get_value())
        self.apply_layout()
        
    def on_column_gap_changed(self, spin):
        """Handle column gap spinner change."""
        self.app.column_gap = int(spin.get_value())
        self.apply_layout()

    def scroll_viewport(self, direction: int):
        """
        Scroll one full viewport (page) and snap to viewport boundaries.
        
        Args:
            direction: 1 for forward (next page), -1 for backward (previous page)
        
        Behavior varies by layout mode:
        
        Multi-column (horizontal):
        - Scrolls one viewport width (showing exactly N columns)
        - Snaps to viewport boundaries for precise column alignment
        - Uses smooth scrolling animation followed by hard snap
        - Automatically advances to next/previous chapter at boundaries
        
        Single-column (vertical):
        - Scrolls 90% of viewport height
        - Uses GTK's adjustment system
        - Automatically advances to next/previous chapter at top/bottom
        
        The snap-to-boundary behavior ensures columns are always perfectly
        aligned after scrolling, creating a book-like pagination experience.
        """
        if not self.webview or not self.app.book:
            return

        horizontal_mode = (self.app.columns > 1 or not self.app.use_fixed_columns)
        webview = self.webview

        if horizontal_mode:
            # Multi-column horizontal scrolling with viewport snapping
            js_code = f"""
            (function() {{
                const body = document.scrollingElement || document.body;
                const vw = window.innerWidth;                   // viewport width = page size
                const cur = body.scrollLeft;
                const maxScroll = body.scrollWidth - vw;

                // Calculate next viewport boundary
                let rawTarget = cur + ({direction}) * vw;
                // Snap to exact viewport boundary
                let target = Math.round(rawTarget / vw) * vw;
                target = Math.max(0, Math.min(target, maxScroll));

                body.scrollTo({{ left: target, behavior: 'smooth' }});
                return JSON.stringify({{cur, target, maxScroll, vw}});
            }})();
            """

            def process_result(result):
                """Process JavaScript execution result and handle chapter boundaries."""
                try:
                    js_val = result.get_js_value()
                    if not js_val:
                        return
                    import json
                    data = json.loads(js_val.to_string())
                    target = data.get("target", 0)
                    max_scroll = data.get("maxScroll", 0)
                    vw = max(1, int(data.get("vw", 1)))

                    # Hard snap after smooth animation for pixel-perfect alignment
                    snap_js = f"""
                    (function() {{
                        const body = document.scrollingElement || document.body;
                        const vw = {vw};
                        const cur = body.scrollLeft;
                        let snapped = Math.round(cur / vw) * vw;
                        snapped = Math.max(0, Math.min(snapped, body.scrollWidth - vw));
                        body.scrollTo({{left: snapped, behavior: 'auto'}});
                    }})();
                    """
                    # Execute snap after animation completes
                    GLib.timeout_add(
                        420,  # Animation duration
                        lambda: GLib.idle_add(
                            lambda: webview.evaluate_javascript(snap_js, -1, None, None, None, None, None)
                        ),
                    )

                    # Handle chapter boundaries (only at true edges)
                    if direction > 0 and target >= max_scroll - 2:
                        GLib.timeout_add(300, self.load_next_spine_item)
                    elif direction < 0 and target <= 1:
                        GLib.timeout_add(300, self.load_prev_spine_item)

                except Exception as e:
                    print(f"scroll eval error: {e}", file=sys.stderr)

                self.update_nav_buttons()

            def run_js():
                """Execute JavaScript and process result asynchronously."""
                res = webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                if res:
                    result = webview.evaluate_javascript_finish(res)
                    process_result(result)
                return False

            GLib.idle_add(run_js)

        else:
            # Single-column vertical scrolling
            v_adj = self.scrolled_window.get_vadjustment()
            viewport_height = v_adj.get_page_size()
            current_scroll = v_adj.get_value()
            max_scroll = v_adj.get_upper() - viewport_height
            
            # Scroll 90% of viewport height for comfortable reading
            target_scroll = current_scroll + (direction * viewport_height * 0.9)
            target_scroll = max(0, min(target_scroll, max_scroll))
            v_adj.set_value(target_scroll)

            # Handle chapter boundaries
            if direction < 0 and current_scroll <= 1:
                GLib.timeout_add(200, self.load_prev_spine_item)
            elif direction > 0 and current_scroll >= max_scroll - 1:
                GLib.timeout_add(200, self.load_next_spine_item)

            self.update_nav_buttons()

    def load_next_spine_item(self):
        """
        Load the next chapter in the spine (reading order).
        
        Returns:
            None
        
        Called automatically when scrolling past the end of the current chapter.
        Does nothing if already at the last chapter.
        
        Behavior differs by layout mode:
        - Multi-column: Uses JavaScript to ensure precise left-alignment at start
        - Single-column: Uses GTK adjustment to scroll to top
        """
        if not self.app.book or self.app.current_spine_index < 0: return

        spine_length = len(self.app.book.spine)
        next_index = self.app.current_spine_index + 1
        
        if next_index < spine_length:
            item_id = self.app.book.spine[next_index][0]
            next_item = self.app.book.get_item_with_id(item_id)
            if next_item:
                self.load_href(next_item.get_name())
                # For multicolumn, ensure we snap to the beginning after load
                if self.app.columns > 1 or not self.app.use_fixed_columns:
                    GLib.timeout_add(200, self.scroll_to_start_of_page)
        
    def load_prev_spine_item(self):
        """
        Load the previous chapter in the spine (reading order).
        
        Returns:
            None
        
        Called automatically when scrolling past the beginning of the current chapter.
        After loading, scrolls to the end of the new chapter so the user continues
        reading backward. Does nothing if already at the first chapter.
        
        Behavior differs by layout mode:
        - Multi-column: Uses JavaScript to snap to rightmost column
        - Single-column: Uses GTK adjustment to scroll to bottom
        """
        if not self.app.book or self.app.current_spine_index < 0: return

        prev_index = self.app.current_spine_index - 1
        
        if prev_index >= 0:
            item_id = self.app.book.spine[prev_index][0]
            prev_item = self.app.book.get_item_with_id(item_id)
            if prev_item:
                self.load_href(prev_item.get_name())
                # Always scroll to end when going backwards
                GLib.timeout_add(200, self.scroll_to_end_of_page)

    def scroll_to_start_of_page(self):
        """
        Scroll to the first page/column of the current chapter.
        
        Returns:
            bool: False to prevent repeated timer callbacks
        
        Used when navigating forward to the next chapter, ensuring the user
        sees the beginning of the new chapter.
        
        Behavior:
        - Multi-column: Scrolls horizontally to the leftmost column with snapping
        - Single-column: Scrolls vertically to the top
        """
        if self.app.columns > 1 or not self.app.use_fixed_columns:
            # Multi-column: use JavaScript for precise horizontal snap to start
            js_code = """
            (function() {
                const body = document.scrollingElement || document.body;
                const vw = window.innerWidth;
                body.scrollTo({
                    left: 0,
                    behavior: 'auto'
                });
            })();
            """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except:
                # Fallback to GTK adjustment
                self.scrolled_window.get_hadjustment().set_value(0)
        else:
            # Single-column: scroll to top using GTK adjustment
            self.scrolled_window.get_vadjustment().set_value(0)
        
        self.update_nav_buttons()
        return False

    def scroll_to_end_of_page(self):
        """
        Scroll to the last page/column of the current chapter.
        
        Returns:
            bool: False to prevent repeated timer callbacks
        
        Used when navigating backward to the previous chapter, ensuring the
        user sees the end of that chapter (for continuous backward reading).
        
        Behavior:
        - Multi-column: Uses JavaScript to snap to rightmost column precisely
        - Single-column: Uses GTK adjustment to scroll to bottom
        """
        if self.app.columns > 1 or not self.app.use_fixed_columns:
            # Multi-column: use JavaScript to calculate and snap to last column
            js_code = """
            (function() {
                const body = document.scrollingElement || document.body;
                const vw = window.innerWidth;
                const maxScroll = body.scrollWidth - vw;
                
                // Snap to the rightmost viewport boundary
                let target = Math.floor(maxScroll / vw) * vw;
                target = Math.max(0, Math.min(target, maxScroll));
                
                body.scrollTo({
                    left: target,
                    behavior: 'auto'
                });
                
                return target;
            })();
            """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except:
                # Fallback to GTK adjustment
                h_adj = self.scrolled_window.get_hadjustment()
                viewport_width = h_adj.get_page_size()
                max_scroll = h_adj.get_upper() - viewport_width
                h_adj.set_value(max_scroll)
        else:
            # Single-column: scroll to bottom using GTK adjustment
            v_adj = self.scrolled_window.get_vadjustment()
            viewport_height = v_adj.get_page_size()
            max_scroll = v_adj.get_upper() - viewport_height
            v_adj.set_value(max_scroll)
        
        self.update_nav_buttons()
        return False

    def update_nav_buttons(self, *args):
        """
        Update the enabled/disabled state of navigation buttons.
        
        Args:
            *args: Variable arguments from signal callbacks (ignored)
        
        Returns:
            bool: True to continue receiving signals
        
        Navigation logic:
        - Previous button: Enabled if not at the start of the first chapter
        - Next button: Enabled if not at the end of the last chapter
        
        Considers both:
        - Current scroll position within the chapter
        - Current chapter position in the spine (reading order)
        
        Handles both horizontal (multi-column) and vertical (single-column) modes.
        """
        if not self.app.book or self.app.current_spine_index < 0:
            self.prev_btn.set_sensitive(False)
            self.next_btn.set_sensitive(False)
            return True

        # Determine scroll position based on layout mode
        if self.app.columns > 1 or not self.app.use_fixed_columns:
            # Multi-column: check horizontal scroll position
            h_adj = self.scrolled_window.get_hadjustment()
            current = h_adj.get_value()
            page_size = h_adj.get_page_size()
            upper = h_adj.get_upper()
            max_pos = max(0, upper - page_size)
            
            is_first_page = current <= 1
            is_last_page = current >= max_pos - 1
        else:
            # Single-column: check vertical scroll position
            v_adj = self.scrolled_window.get_vadjustment()
            current = v_adj.get_value()
            page_size = v_adj.get_page_size()
            upper = v_adj.get_upper()
            max_pos = max(0, upper - page_size)
            
            is_first_page = current <= 1
            is_last_page = current >= max_pos - 1

        spine_length = len(self.app.book.spine)
        
        # Can go previous if not at the start of first chapter
        can_go_prev = not is_first_page or self.app.current_spine_index > 0
        self.prev_btn.set_sensitive(can_go_prev)
        
        # Can go next if not at the end of last chapter
        can_go_next = not is_last_page or self.app.current_spine_index < spine_length - 1
        self.next_btn.set_sensitive(can_go_next)
        
        return True

    def do_close_request(self):
        """
        Handle window close request.
        
        Returns:
            bool: False to allow the window to close
        
        Cleans up the temporary directory containing extracted EPUB files
        before the window closes. This prevents disk space leaks from
        temporary files accumulating over multiple sessions.
        """
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        return False


if __name__ == "__main__":
    app = EPUBViewer()
    app.run(sys.argv)
