#!/usr/bin/env python3
"""
GTK4 Multi-Column HTML Viewer Application
OVERVIEW:
This is a desktop HTML document viewer built with Python using GTK4 (GNOME Toolkit 
version 4) and Adwaita (GNOME's adaptive widget library). It displays HTML content 
in a customizable multi-column layout, similar to newspaper or magazine layouts.
TECHNOLOGY STACK:
- GTK 4.0: Modern cross-platform GUI toolkit for creating native-looking interfaces
- Adwaita 1: GNOME's design library with responsive/adaptive widgets
- WebKit 6.0: Browser engine for rendering HTML/CSS/JavaScript content
- GObject Introspection (gi): Allows Python to use C libraries like GTK
KEY FEATURES:
1. Multi-column layout (1-10 columns) for HTML content
2. Responsive sidebar that collapses on small screens
3. Column-based navigation with keyboard shortcuts
4. Smooth scrolling animations
5. File loading with threading for non-blocking UI
6. JavaScript-to-Python communication for scroll events
"""
import os
import gi
# LIBRARY VERSION REQUIREMENTS
# gi.require_version() ensures we load the correct version of each library
# This must be done BEFORE importing from gi.repository
gi.require_version("Gtk", "4.0")      # GTK4 - Latest GUI toolkit
gi.require_version("Adw", "1")        # Adwaita - Modern GNOME widgets
gi.require_version("WebKit", "6.0")   # WebKit - Browser engine
from gi.repository import Gtk, Adw, Gio, WebKit, GLib, Gdk
# WEBKIT COMPOSITING MODE DISABLE
# WHY: Prevents potential rendering issues on some systems
# WHAT: Disables hardware-accelerated compositing in WebKit
# WHEN: Must be set before creating WebKit widgets
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
class Win(Adw.ApplicationWindow):
    """
    Main Application Window Class
    INHERITANCE:
    Inherits from Adw.ApplicationWindow (not Gtk.Window) to get:
    - Modern GNOME styling
    - Adaptive/responsive behavior
    - Breakpoint support for different screen sizes
    - Smooth animations
    ARCHITECTURE:
    The window uses a split-view layout with:
    - Sidebar: For navigation/controls (can be toggled)
    - Content: Main area with header bar and WebView
    RESPONSIVENESS:
    Uses Adwaita's Breakpoint system to automatically collapse the sidebar
    on screens narrower than 768px (similar to responsive web design)
    """
    def __init__(self, app):
        """
        Window Initialization
        PARAMETERS:
        - app: The parent Adw.Application instance
        PROCESS:
        1. Initialize the window with title and default size
        2. Create split-view layout (sidebar + content)
        3. Set up header bar with controls
        4. Initialize WebView for HTML rendering
        5. Configure JavaScript-to-Python communication
        6. Set up responsive breakpoints
        """
        super().__init__(application=app, title="Demo")
        self.set_default_size(800, 600)
        # ===================================================================
        # SPLIT VIEW LAYOUT
        # ===================================================================
        # Adw.OverlaySplitView provides a responsive two-pane layout
        # 
        # WHY: Allows sidebar to overlay content on small screens instead of 
        #      pushing content aside
        # HOW: When screen is wide, sidebar appears beside content
        #      When screen is narrow, sidebar overlays content
        # show_sidebar=True: Sidebar visible by default
        # ===================================================================
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.set_content(self.split)
        # ===================================================================
        # SIDEBAR CONSTRUCTION
        # ===================================================================
        # Gtk.Box is a container that arranges children in a row or column
        # 
        # orientation=VERTICAL: Stack children vertically
        # spacing=6: 6 pixels between each child widget
        # 
        # USAGE: Currently just shows a label, but could contain:
        #        - Navigation tree
        #        - Table of contents
        #        - Bookmarks
        #        - Settings panel
        # ===================================================================
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.append(Gtk.Label(label="Sidebar"))
        self.split.set_sidebar(sidebar)
        # ===================================================================
        # CONTENT AREA - TOOLBAR AND HEADER
        # ===================================================================
        # Adw.ToolbarView provides a content area with top/bottom toolbars
        # WHY: Separates header/footer controls from main content
        # ===================================================================
        toolbar = Adw.ToolbarView()
        # Adw.HeaderBar is a modern GNOME-style header bar
        # FEATURES:
        # - Automatically includes window controls (close, minimize, maximize)
        # - Provides pack_start() and pack_end() for adding buttons
        # - Supports title widgets
        header = Adw.HeaderBar()
        # -------------------------------------------------------------------
        # TOGGLE SIDEBAR BUTTON
        # -------------------------------------------------------------------
        # PURPOSE: Show/hide the sidebar
        # ICON: "sidebar-show-symbolic" - A standard GNOME icon
        # WHY SYMBOLIC: Symbolic icons adapt to the current theme colors
        # 
        # pack_start(): Places button on the left side of header
        # connect(): Attaches the on_toggle_sidebar method to "clicked" signal
        # -------------------------------------------------------------------
        toggle_sidebar_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        toggle_sidebar_btn.connect("clicked", self.on_toggle_sidebar)
        header.pack_start(toggle_sidebar_btn)
        # -------------------------------------------------------------------
        # OPEN FILE BUTTON
        # -------------------------------------------------------------------
        # PURPOSE: Opens file chooser dialog to load HTML files
        # ICON: "document-open-symbolic" - Standard "open file" icon
        # -------------------------------------------------------------------
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_file)
        header.pack_start(open_btn)
        # -------------------------------------------------------------------
        # COLUMNS DROPDOWN MENU
        # -------------------------------------------------------------------
        # PURPOSE: Select number of columns (1-10) for layout
        #          Also includes 1 column horizontal scroll mode.
        # 
        # Gtk.StringList: A model that stores strings
        # WHY MODEL: GTK4 uses model-view architecture for lists/combos
        # BENEFIT: Separates data (StringList) from presentation (DropDown)
        # 
        # Gtk.DropDown: Modern GTK4 dropdown menu widget
        # selected=0: Start with "1 Column" selected (index 0 = first item)
        # 
        # pack_end(): Places dropdown on the right side of header
        # notify::selected: Signal emitted when selection changes
        # -------------------------------------------------------------------
        string_list = Gtk.StringList()
        # Add 1 Column (Vertical) option
        string_list.append("1 Column")
        # Add 1 Column (Horizontal) option
        string_list.append("1 Column (Horizontal)")
        # Add 2-10 Column options
        for i in range(2, 11):
            string_list.append(f"{i} Columns")
        self.columns_combo = Gtk.DropDown(model=string_list, selected=0) # Start with 1 Column (Vertical)
        self.columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(self.columns_combo)
        # Set header title and add to toolbar
        header.set_title_widget(Gtk.Label(label="Header"))
        toolbar.add_top_bar(header)
        # ===================================================================
        # WEBKIT WEBVIEW - HTML RENDERING ENGINE
        # ===================================================================
        # WebKit.WebView is a full web browser engine embedded in the app
        # 
        # WHY WEBKIT:
        # - Renders HTML/CSS/JavaScript just like a web browser
        # - Supports modern web standards
        # - Provides JavaScript-to-Python communication
        # 
        # EXPAND PROPERTIES:
        # vexpand=True: WebView expands vertically to fill available space
        # hexpand=True: WebView expands horizontally to fill available space
        # ===================================================================
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        # -------------------------------------------------------------------
        # JAVASCRIPT-TO-PYTHON COMMUNICATION SETUP
        # -------------------------------------------------------------------
        # PURPOSE: Allow JavaScript code in WebView to send messages to Python
        # 
        # HOW IT WORKS:
        # 1. JavaScript calls: webkit.messageHandlers.scrollEvent.postMessage(data)
        # 2. WebKit converts the message and emits a signal
        # 3. Python receives signal in on_scroll_event_received() method
        # 
        # USE CASE: Track scroll events, keyboard navigation, user interactions
        # 
        # STEPS:
        # 1. Get the UserContentManager (manages scripts and messages)
        # 2. Connect signal handler for messages named "scrollEvent"
        # 3. Register the message handler name so JavaScript can use it
        # -------------------------------------------------------------------
        content_manager = self.webview.get_user_content_manager()
        content_manager.connect("script-message-received::scrollEvent", 
                              self.on_scroll_event_received)
        content_manager.register_script_message_handler("scrollEvent")
        # Load initial welcome message
        self.webview.load_html(
            "<html><body><h1>Welcome</h1>"
            "<p>Select an HTML file to view.</p></body></html>"
        )
        # -------------------------------------------------------------------
        # CONTENT STORAGE
        # -------------------------------------------------------------------
        # WHY STORE ORIGINAL: When changing column count, we need to regenerate
        #                     the HTML with new CSS column rules
        # WHAT'S STORED: Just the body content, not the full HTML document
        # -------------------------------------------------------------------
        self.original_html_content = (
            "<h1>Welcome</h1><p>Select an HTML file to view.</p>"
        )
        # -------------------------------------------------------------------
        # CONTENT BOX AND MARGINS
        # -------------------------------------------------------------------
        # Gtk.Box: Container for the WebView
        # MARGINS: Add padding around the WebView for visual spacing
        #          10px on all sides (top, bottom, start/left, end/right)
        # -------------------------------------------------------------------
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.webview.set_margin_top(10)
        self.webview.set_margin_bottom(10)
        self.webview.set_margin_start(10)
        self.webview.set_margin_end(10)
        content_box.append(self.webview)
        # Add content to toolbar and toolbar to split view
        toolbar.set_content(content_box)
        self.split.set_content(toolbar)
        # -------------------------------------------------------------------
        # COLUMN STATE MANAGEMENT
        # -------------------------------------------------------------------
        # current_columns: Tracks the currently active column count
        # scroll_mode: Tracks the current scrolling mode ('vertical', 'horizontal_1_col', 'horizontal_multi_col')
        # WHY STORE: Used when reapplying layout after window resize or
        #            sidebar toggle
        # 
        # GLib.idle_add(): Schedule function to run when event loop is idle
        # WHY: Ensures window is fully initialized before applying layout
        # LAMBDA: Anonymous function that calls apply_column_layout
        # -------------------------------------------------------------------
        self.current_columns = 1
        self.scroll_mode = 'vertical' # Default to vertical
        # Select the first item (1 Column Vertical) which corresponds to index 0
        self.columns_combo.set_selected(0)
        GLib.idle_add(lambda: self.apply_column_layout(0)) # Pass index 0 initially
        # -------------------------------------------------------------------
        # WINDOW RESIZE HANDLING
        # -------------------------------------------------------------------
        # pending_column_change: Stores pending layout updates (unused currently)
        # 
        # notify::default-width/height: Signals emitted when window is resized
        # WHY REAPPLY LAYOUT: Column widths depend on window width
        #                     Must recalculate when window size changes
        # -------------------------------------------------------------------
        self.pending_column_change = None
        self.connect("notify::default-width", self.on_size_changed)
        self.connect("notify::default-height", self.on_size_changed)
        # -------------------------------------------------------------------
        # RESPONSIVE BREAKPOINT
        # -------------------------------------------------------------------
        # Adw.Breakpoint: Automatically applies property changes at specific
        #                 screen sizes (like CSS media queries)
        # 
        # CONDITION: "max-width: 768px" - When window is 768px wide or less
        # ACTION: set_property(split, "collapsed", True) - Collapse sidebar
        # 
        # WHY 768px: Common tablet/mobile breakpoint
        # BENEFIT: Automatically adapts to small screens without manual code
        # -------------------------------------------------------------------
        self.breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 768px")
        )
        self.breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.breakpoint)
    def on_toggle_sidebar(self, button):
        """
        Toggle Sidebar Visibility
        PURPOSE: Show or hide the sidebar when button is clicked
        PROCESS:
        1. Get current sidebar state (shown/hidden)
        2. Toggle to opposite state
        3. Wait for animation to complete (350ms)
        4. Reapply column layout with scroll position restoration
        WHY WAIT: Sidebar animation takes 350ms. If we reapply layout 
                  immediately, column widths are wrong because sidebar 
                  is still animating.
        restore_position=True: Maintains scroll position when layout changes
        """
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        # GLib.timeout_add(milliseconds, function)
        # Schedules function to run after specified delay
        GLib.timeout_add(
            350,  # Wait for sidebar animation (350ms)
            lambda: self.apply_column_layout(
                self.columns_combo.get_selected(), # Use current combo selection index
                restore_position=True
            )
        )
    def on_scroll_event_received(self, content_manager, js_result):
        """
        JavaScript-to-Python Message Handler
        PURPOSE: Receives scroll/navigation events from JavaScript in WebView
        HOW IT WORKS:
        1. JavaScript in WebView sends JSON data via webkit.messageHandlers
        2. This method receives the data
        3. Parses JSON to extract event information
        4. Prints formatted event details with emojis
        EVENT TYPES:
        - wheel: Mouse wheel scroll (horizontal 1-col or multi-col)
        - wheel-y: Vertical scroll in single-column mode
        - wheel-x: Horizontal scroll in 1-col horizontal mode
        - arrow-left/right: Keyboard arrow navigation (horizontal)
        - arrow-up/down: Keyboard arrow navigation (vertical, 1-col horizontal)
        - page-up/down: Page navigation (vertical, horizontal)
        - home/end: Jump to start/end
        DATA STRUCTURE (JSON):
        {
            "type": "arrow-left",
            "scrollX": 800,      // Horizontal scroll position
            "scrollY": 0,        // Vertical scroll position  
            "column": 2          // Current column number
        }
        WHY NEEDED: 
        - Helps debug scrolling behavior
        - Could be extended to:
          * Update UI indicators (e.g., "Column 3 of 10")
          * Save reading position
          * Track user behavior analytics
        PARAMETERS:
        - content_manager: The UserContentManager that received the message
        - js_result: Contains the message data from JavaScript
        """
        try:
            import json
            # Convert JavaScript message to Python string
            event_data = json.loads(js_result.to_string())
            # Extract event details with defaults
            event_type = event_data.get('type', 'unknown')
            scroll_x = event_data.get('scrollX', 0)
            scroll_y = event_data.get('scrollY', 0)
            column = event_data.get('column', 0)
            # Icon mapping for visual console output
            # WHY ICONS: Makes console output easier to scan visually
            icons = {
                'wheel': 'ðŸ”ƒ ',  # Horizontal scroll (1-col horiz or multi-col)
                'wheel-x': 'â‡„ï¸ ', # Specifically for 1-col horizontal scroll
                'wheel-y': 'â‡•ï¸  ',
                'arrow-left': 'â¬… ',  # Horizontal navigation
                'arrow-right': 'âž¡ ',
                'arrow-up': 'â¬†ï¸ ', # Vertical/1-col horiz navigation
                'arrow-down': 'â¬‡ï¸ ',
                'page-up': 'PgUp',
                'page-down': 'PgDn',
                'home': 'ğŸ€',
                'end': 'ğŸ›‘'
            }
            icon = icons.get(event_type, 'ğŸ¤”')
            # Format and print event information
            # Different format for vertical vs horizontal scrolling
            if event_type.startswith('wheel'):
                print(f"{icon} Scroll Event: {event_type:12s} | "
                      f"ScrollX: {scroll_x:5.0f} | ScrollY: {scroll_y:5.0f}")
            else:
                print(f"{icon} Scroll Event: {event_type:12s} | "
                      f"ScrollX: {scroll_x:5.0f} | ScrollY: {scroll_y:5.0f} | Column: {column}")
        except Exception as e:
            print(f"Error receiving scroll event: {e}")
    def on_open_file(self, button):
        """
        Open File Dialog Handler
        PURPOSE: Show file chooser dialog to select HTML files
        GTK4 FILE DIALOGS:
        GTK4 uses asynchronous file dialogs (non-blocking)
        - Old GTK3: Gtk.FileChooserDialog (blocks UI)
        - New GTK4: Gtk.FileDialog (async, modern)
        FILE FILTERS:
        - HTML filter: Shows only .html and .htm files
        - All filter: Shows all files (fallback option)
        FILTER SYSTEM:
        - Gtk.FileFilter: Defines what files to show
        - Gio.ListStore: Stores multiple filters
        - User can switch between filters in dialog
        ASYNC PATTERN:
        1. Create dialog
        2. Call dialog.open() with callback
        3. Callback receives result when user chooses file
        4. Non-blocking: UI remains responsive while dialog is open
        """
        dialog = Gtk.FileDialog()
        dialog.set_title("Open HTML File")
        # Create HTML file filter
        # add_pattern(): Defines file extensions to show
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML files")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        # Create "all files" filter
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        # Store filters in a list model
        # WHY: GTK4 uses model-based architecture
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(html_filter)
        filters.append(all_filter)
        dialog.set_filters(filters)
        # Open dialog asynchronously
        # self: Parent window
        # None: No cancellable object
        # callback: Function to call when user responds
        dialog.open(self, None, self.on_file_dialog_response)
    def on_file_dialog_response(self, dialog, result):
        """
        File Dialog Response Handler
        PURPOSE: Process the file selected by user in dialog
        ASYNC PATTERN:
        1. dialog.open_finish(result): Get the selected file
        2. If file selected, load it
        3. If cancelled, do nothing
        THREADING:
        File I/O is done in a separate thread to prevent UI freezing
        WHY THREADING:
        - Large files take time to read
        - Reading in main thread would freeze UI
        - Separate thread keeps UI responsive
        THREAD PROCESS:
        1. Create thread function (load_file_in_thread)
        2. Read file in thread
        3. Use GLib.idle_add() to update UI in main thread
        IMPORTANT: GTK is not thread-safe
        - File reading: Safe in thread
        - UI updates: Must be in main thread
        - GLib.idle_add(): Schedules function in main thread
        PARAMETERS:
        - dialog: The file dialog that was shown
        - result: Async operation result (contains selected file)
        """
        try:
            # Get selected file from async result
            file = dialog.open_finish(result)
            if file:
                def load_file_in_thread():
                    """
                    Thread Function for File Loading
                    RUNS IN: Separate thread (not main UI thread)
                    PROCESS:
                    1. Load file bytes synchronously (blocks thread, not UI)
                    2. Decode bytes to UTF-8 string
                    3. Store in self.original_html_content
                    4. Schedule layout update in main thread
                    ERROR HANDLING:
                    - Catches decode errors (invalid UTF-8)
                    - Catches file read errors
                    - Shows error dialog in main thread
                    """
                    try:
                        # Load file bytes (blocking operation in thread)
                        content_bytes = file.load_bytes(None)[0]
                        # Decode to string
                        # UTF-8: Standard encoding for HTML files
                        content = content_bytes.get_data().decode('utf-8')
                        # Store content
                        self.original_html_content = content
                        # Schedule UI update in main thread
                        # WHY idle_add: Must update UI from main thread only
                        GLib.idle_add(
                            lambda: self.apply_column_layout(
                                self.columns_combo.get_selected() # Use current combo selection index
                            )
                        )
                    except Exception as e:
                        print(f"Error reading file: {e}")
                        # Schedule error dialog in main thread
                        GLib.idle_add(
                            lambda: self.show_error_dialog(
                                f"Error loading file: {e}"
                            )
                        )
                # Create and start thread
                # None: Thread name (optional)
                # load_file_in_thread: Function to run in thread
                GLib.Thread.new(None, load_file_in_thread)
        except GLib.Error:
            # User cancelled dialog
            pass
    def show_error_dialog(self, message):
        """
        Display Error Dialog
        PURPOSE: Show user-friendly error messages
        Gtk.MessageDialog: Simple dialog for messages
        - transient_for: Parent window (centers dialog over it)
        - message_type: ERROR (shows error icon)
        - buttons: CLOSE (single close button)
        NOTE: This uses deprecated GTK3-style dialog
        TODO: Update to modern GTK4 Adw.MessageDialog in production code
        PARAMETERS:
        - message: Error message text to display
        """
        error_dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message
        )
        error_dialog.run()
        error_dialog.destroy()
    def apply_column_layout(self, selected_index, restore_position=False):
        """
        Apply Multi-Column Layout to HTML Content
        PURPOSE: Transform HTML content into CSS multi-column layout
                 or single column layout with horizontal scrolling.
        THIS IS THE CORE FUNCTION OF THE APPLICATION
        PROCESS:
        1. Determine number of columns and scroll mode from dropdown selection
        2. Generate CSS for layout (column or single column with width)
        3. Generate JavaScript for navigation based on scroll mode
        4. Extract body content from original HTML
        5. Combine everything into new HTML document
        6. Load into WebView
        CSS LAYOUT:
        - For vertical scroll: Standard column-count layout.
        - For horizontal scroll (1-col or multi-col): Single column container
          with width calculated based on column count and gap.
        JAVASCRIPT FEATURES:
        1. Scrolling based on mode (vertical, horizontal 1-col, horizontal multi-col).
        2. Smooth scroll animations (400ms ease-in-out)
        3. Keyboard navigation (arrows, page up/down, home/end)
        4. Mouse wheel navigation (vertical or horizontal)
        5. Event reporting to Python
        PARAMETERS:
        - selected_index: Index from dropdown (0=1 Column vertical, 1=1 Column horizontal, 2=2 Columns, etc.)
        - restore_position: Whether to maintain scroll position (unused currently,
                           but intended for future enhancement)
        """
        # Determine column count and scroll mode from the selected index
        if selected_index == 0: # 1 Column (Vertical)
            num_columns = 1
            scroll_mode = 'vertical'
        elif selected_index == 1: # 1 Column (Horizontal)
            num_columns = 1
            scroll_mode = 'horizontal_1_col'
        else: # 2+ Columns (Horizontal)
            num_columns = selected_index # Index 2 -> 2 columns, etc.
            scroll_mode = 'horizontal_multi_col'

        self.current_columns = num_columns
        self.scroll_mode = scroll_mode

        # ===================================================================
        # CSS GENERATION FOR LAYOUT
        # ===================================================================
        # For vertical scrolling (1 column): Standard layout
        # For horizontal scrolling: Single column container with calculated width
        # to force horizontal overflow if content is long enough.
        # ===================================================================
        if scroll_mode == 'vertical':
            css = f"""
            <style>
                body {{
                    font-family: sans-serif;
                    margin-top: 0px;
                    margin-bottom: 0px;
                    margin-left: 0px;
                    margin-right: 0px;
                    width: 100%;
                    height: 100%;
                }}
                .content-container {{
                    column-count: {num_columns};     /* Number of columns */
                    column-gap: 20px;                /* Space between columns */
                    width: 100%;
                    height: 100%;
                    box-sizing: border-box;
                }}
                /* Prevent elements from splitting across columns */
                .content-container * {{
                    break-inside: avoid;              /* Don't split by default */
                    page-break-inside: avoid;         /* Older browser support */
                }}
                /* Allow text flow in text elements */
                .content-container p,
                .content-container div,
                .content-container span {{
                    break-inside: auto;               /* Allow text to flow */
                    page-break-inside: auto;
                }}
            </style>
            """
        else: # horizontal_1_col or horizontal_multi_col
             # Calculate total width needed for the content container
             # This forces the horizontal scrollbar if content is wider than viewport
             total_gap = (num_columns - 1) * 20 # Assuming 20px gap, same as CSS
             # We'll use a fixed width for the container based on viewport width
             # and the number of columns. A very wide single column or multi-column
             # layout will overflow horizontally.
             # A more robust way would be to calculate the natural content width,
             # but for simplicity, we'll assume a wide container.
             # Let's calculate based on a typical viewport width * number of columns
             # This is a simplification; ideally, content width determines this.
             # Using a large fixed width is a common trick for horizontal scrolling.
             # We'll use a placeholder calculation here, but it might need adjustment
             # depending on actual content behavior. Let's just set a very wide width
             # if it's horizontal, relying on CSS column layout for structure.
             # Actually, let's just use the standard column CSS but ensure overflow-x is allowed.
             # The JavaScript will handle the horizontal snapping and navigation.
             # The key is that the container itself needs to be wider than the viewport
             # for horizontal scrolling to occur, which the column layout inherently does
             # if the total calculated width exceeds the container width.
             # The original code's column CSS should work, but let's ensure overflow-x.
             # The container width itself should be 100% of the viewport width,
             # but the *content* inside flows into columns making the total scrollable width larger.
             css = f"""
            <style>
                body {{
                    font-family: sans-serif;
                    margin-top: 0px;
                    margin-bottom: 0px;
                    margin-left: 0px;
                    margin-right: 0px;
                    width: 100%;
                    height: 100%;
                    overflow-x: auto; /* Allow horizontal scrolling */
                    overflow-y: hidden; /* Hide vertical scrollbar if not needed */
                }}
                .content-container {{
                    column-count: {num_columns};     /* Number of columns */
                    column-gap: 20px;                /* Space between columns */
                    width: fit-content;              /* Expand width based on columns */
                    min-width: 100%;                 /* At least viewport width */
                    height: 100%;
                    box-sizing: border-box;
                    display: inline-block;           /* Behave like a single wide line */
                }}
                /* Prevent elements from splitting across columns */
                .content-container * {{
                    break-inside: avoid;              /* Don't split by default */
                    page-break-inside: avoid;         /* Older browser support */
                }}
                /* Allow text flow in text elements */
                .content-container p,
                .content-container div,
                .content-container span {{
                    break-inside: auto;               /* Allow text to flow */
                    page-break-inside: auto;
                }}
            </style>
            """

        # ===================================================================
        # JAVASCRIPT GENERATION FOR INTERACTIVE NAVIGATION
        # ===================================================================
        # This JavaScript code runs in the WebView and provides:
        # 1. Scrolling based on mode (vertical, horizontal 1-col, horizontal multi-col)
        # 2. Smooth animations
        # 3. Keyboard navigation
        # 4. Event reporting to Python
        # ===================================================================
        js_script = f"""
        // ===============================================================
        // GLOBAL STATE
        // ===============================================================
        // Store current column count and scroll mode (injected from Python)
        window.currentColumnCount = {num_columns};
        window.scrollMode = '{scroll_mode}'; // 'vertical', 'horizontal_1_col', 'horizontal_multi_col'
        // ===============================================================
        // COLUMN WIDTH CALCULATION (for horizontal modes)
        // ===============================================================
        // PURPOSE: Calculate exact width of one column including gap (for horizontal modes)
        //          For vertical mode, this is less critical for scrolling but kept for consistency.
        // 
        // FORMULA: (containerWidth - totalGap) / columnCount + gap
        // 
        // WHY INCLUDE GAP: When scrolling horizontally, we want to scroll by 
        //                  (columnWidth + gap) to land exactly at 
        //                  start of next column or viewport width for 1-col horizontal.
        // ===============================================================
        function getColumnWidth() {{
            if (window.scrollMode === 'vertical') {{
                 // For vertical mode, column width is effectively the viewport width
                 // or the width of the container if it's less than the viewport.
                 // But for snapping, we might not need this specific width calculation.
                 // Let's return the viewport width as the 'snap' unit if needed elsewhere,
                 // though vertical scroll doesn't use it for snapping currently.
                 return window.innerWidth; // Or document.querySelector('.content-container').offsetWidth;
            }} else {{
                 // For horizontal modes (1-col horiz or multi-col horiz)
                 const container = document.querySelector('.content-container');
                 if (!container) return 0;
                 const style = window.getComputedStyle(container);
                 const colCount = window.currentColumnCount;
                 const gap = parseFloat(style.columnGap) || 20; // Default gap if not found
                 const totalGap = gap * (colCount - 1);
                 const columnWidth = (container.offsetWidth - totalGap) / colCount;
                 return columnWidth + gap;  // Return column + gap
            }}
        }}
        // ===============================================================
        // PYTHON EVENT REPORTING
        // ===============================================================
        // PURPOSE: Send scroll/navigation events to Python
        // 
        // HOW: Uses WebKit's message handler API
        //      JavaScript -> webkit.messageHandlers -> Python signal
        // 
        // DATA SENT:
        // - type: Event type (wheel, arrow-left, etc.)
        // - scrollX/Y: Current scroll position
        // - column: Current column number (calculated)
        // ===============================================================
        function sendScrollEvent(eventType) {{
            if (window.webkit && 
                window.webkit.messageHandlers && 
                window.webkit.messageHandlers.scrollEvent) {{
                const colWidth = getColumnWidth();
                let currentColumn = 0;
                if (window.scrollMode !== 'vertical') {{
                    // Calculate column based on horizontal scroll position
                    currentColumn = colWidth > 0 
                        ? Math.round(window.scrollX / colWidth) 
                        : 0;
                }}
                window.webkit.messageHandlers.scrollEvent.postMessage(
                    JSON.stringify({{
                        type: eventType,
                        scrollX: window.scrollX,
                        scrollY: window.scrollY,
                        column: currentColumn
                    }})
                );
            }}
        }}
        // ===============================================================
        // SMOOTH SCROLL ANIMATION
        // ===============================================================
        // PURPOSE: Animate scroll from current position to target
        // 
        // ANIMATION:
        // - Duration: 400ms
        // - Easing: Cubic ease-in-out (slow start, fast middle, slow end)
        // 
        // HOW IT WORKS:
        // 1. Record start position and time
        // 2. Use requestAnimationFrame for smooth 60fps animation
        // 3. Calculate progress (0 to 1) over 400ms
        // 4. Apply easing function for natural motion
        // 5. Update scroll position each frame
        // 
        // EASING FUNCTION:
        // Cubic ease-in-out: 
        // - t < 0.5: Accelerate (4tÂ³)
        // - t >= 0.5: Decelerate (custom formula)
        // Result: Smooth, natural feeling motion
        // ===============================================================
        function smoothScrollTo(xTarget, yTarget) {{
            const startX = window.scrollX;
            const startY = window.scrollY;
            const distanceX = xTarget - startX;
            const distanceY = yTarget - startY;
            const duration = 400;  // milliseconds
            const startTime = performance.now();
            function step(time) {{
                const elapsed = time - startTime;
                const progress = Math.min(elapsed / duration, 1);
                // Cubic ease-in-out formula
                const t = progress < 0.5 
                    ? 4 * progress * progress * progress 
                    : (progress - 1) * (2 * progress - 2) * (2 * progress - 2) + 1;
                // Update scroll position
                window.scrollTo(
                    startX + distanceX * t,
                    startY + distanceY * t
                );
                // Continue animation if not complete
                if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
        }}
        // ===============================================================
        // SNAP SCROLL TO COLUMN BOUNDARY (for horizontal modes)
        // ===============================================================
        // PURPOSE: Ensure scroll position aligns exactly with column start (horizontal modes)
        // 
        // WHY: When user scrolls freely (e.g., trackpad swipe), they may
        //      end up between columns. This snaps to nearest column.
        // 
        // WHEN: Called after scroll stops (100ms timeout) AND in horizontal mode
        // 
        // HOW:
        // 1. Get current scroll position
        // 2. Divide by column width
        // 3. Round to nearest column
        // 4. Multiply back by column width
        // 5. Scroll to that position
        // ===============================================================
        function snapScroll() {{
            if (window.scrollMode === 'vertical') return; // Only snap in horizontal modes
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const currentScroll = window.scrollX;
            const target = Math.round(currentScroll / colWidth) * colWidth;
            // Only snap if we're more than 1px off
            if (Math.abs(currentScroll - target) > 1) 
                window.scrollTo(target, window.scrollY);
        }}
        // ===============================================================
        // SCROLL EVENT LISTENER
        // ===============================================================
        // PURPOSE: Snap to column after scrolling stops (horizontal modes)
        // 
        // TIMEOUT PATTERN:
        // - Clear previous timeout on each scroll event
        // - Set new timeout for 100ms
        // - If no scroll for 100ms, snap to column (horizontal modes)
        // 
        // WHY: Prevents snapping during active scrolling
        //      Only snaps after user stops scrolling
        // ===============================================================
        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.scrollMode !== 'vertical') snapScroll(); // Snap only in horizontal modes
            }}, 100);
        }});
        // ===============================================================
        // MOUSE WHEEL NAVIGATION (based on mode)
        // ===============================================================
        // PURPOSE: Navigate based on scroll mode
        // 
        // VERTICAL MODE:
        // - Allow default vertical scrolling
        // - Report event as 'wheel-y'
        // 
        // HORIZONTAL MODES (1-col horiz or multi-col horiz):
        // - Prevent default scroll
        // - Scroll horizontally based on mode:
        //   - 1-col horiz: Scroll by viewport width (one 'page')
        //   - multi-col horiz: Scroll by one column width
        // - Use smooth animation
        // 
        // e.deltaY > 0: Scrolled down/forward -> go right
        // e.deltaY < 0: Scrolled up/backward -> go left
        // 
        // passive: false: Allows preventDefault() to work
        // ===============================================================
        document.addEventListener('wheel', function(e) {{
            if (window.scrollMode === 'vertical') {{
                sendScrollEvent('wheel-y');
                return; // Allow default vertical scroll
            }}
            e.preventDefault(); // Prevent default browser scroll for horizontal modes
            const colWidth = getColumnWidth();
            const viewportW = window.innerWidth; // For 1-col horizontal mode
            if (colWidth <= 0) return;
            let scrollDist = 0;
            if (window.scrollMode === 'horizontal_1_col') {{
                // Scroll by one 'page' width (viewport width) in 1-col horizontal mode
                scrollDist = e.deltaY > 0 ? viewportW : -viewportW;
            }} else if (window.scrollMode === 'horizontal_multi_col') {{
                // Scroll by one column width in multi-column horizontal mode
                scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            }}
            const targetX = Math.round(
                (window.scrollX + scrollDist) / colWidth // Use colWidth for rounding logic in both horiz modes
            ) * colWidth; 
            // Apply smooth scroll animation
            smoothScrollTo(targetX, window.scrollY);
            // Report the event to Python - use 'wheel-x' for 1-col horiz, 'wheel' for multi-col horiz
            const eventType = window.scrollMode === 'horizontal_1_col' ? 'wheel-x' : 'wheel';
            sendScrollEvent(eventType);
        }}, {{ passive: false }}); // passive: false is required for preventDefault
        // ===============================================================
        // KEYBOARD NAVIGATION (based on mode)
        // ===============================================================
        // PURPOSE: Navigate based on scroll mode
        // 
        // VERTICAL MODE (scrolling):
        // - Arrow Up/Down: Scroll 80% of viewport height
        // - Page Up/Down: Scroll full viewport height
        // - Home/End: Jump to top/bottom
        // 
        // HORIZONTAL MODES (navigation):
        // - Arrow Left/Right: Move one column width (or viewport width for 1-col horiz)
        // - Page Up/Down:
        //   - 1-col horiz: Move by viewport width (one 'page')
        //   - multi-col horiz: Move by N column widths (N = current number of columns)
        // - Home/End: Jump to first/last column boundary
        // 
        // MODIFIERS:
        // - Ignore if Ctrl/Alt/Meta pressed (system shortcuts)
        // 
        // BOUNDARIES:
        // - Math.max(0, ...): Don't scroll before start
        // - Math.min(max, ...): Don't scroll past end
        // 
        // REPORT DELAY:
        // - 450ms after smooth scroll starts
        // - Allows animation to complete before reporting
        // ===============================================================
        document.addEventListener('keydown', function(e) {{
            // Ignore if modifier keys pressed
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            const colWidth = getColumnWidth();
            const viewportW = window.innerWidth;
            const viewportH = window.innerHeight;
            const maxScrollX = document.body.scrollWidth - window.innerWidth;
            const maxScrollY = document.body.scrollHeight - viewportH;
            let x = window.scrollX, y = window.scrollY, type = null;

            if (window.scrollMode === 'vertical') {{
                // VERTICAL SCROLLING
                switch (e.key) {{
                    case 'ArrowUp': 
                        e.preventDefault(); 
                        y = Math.max(0, y - viewportH * 0.8); 
                        type = 'arrow-up'; 
                        break;
                    case 'ArrowDown': 
                        e.preventDefault(); 
                        y = Math.min(maxScrollY, y + viewportH * 0.8); 
                        type = 'arrow-down'; 
                        break;
                    case 'PageUp': 
                        e.preventDefault(); 
                        y = Math.max(0, y - viewportH); 
                        type = 'page-up'; 
                        break;
                    case 'PageDown': 
                        e.preventDefault(); 
                        y = Math.min(maxScrollY, y + viewportH); 
                        type = 'page-down'; 
                        break;
                    case 'Home': 
                        e.preventDefault(); 
                        y = 0; 
                        type = 'home'; 
                        break;
                    case 'End': 
                        e.preventDefault(); 
                        y = maxScrollY; 
                        type = 'end'; 
                        break;
                }}
            }} else {{
                // HORIZONTAL SCROLLING (1-col horiz or multi-col horiz)
                let scrollUnitX = colWidth; // Default scroll unit is column width
                if (window.scrollMode === 'horizontal_1_col') {{
                     scrollUnitX = viewportW; // For 1-col horiz, use viewport width as scroll unit for arrows too
                }}

                switch (e.key) {{
                    case 'ArrowLeft': 
                        e.preventDefault(); 
                        x = Math.max(0, x - scrollUnitX); 
                        type = 'arrow-left'; 
                        break;
                    case 'ArrowRight': 
                        e.preventDefault(); 
                        x = Math.min(maxScrollX, x + scrollUnitX); 
                        type = 'arrow-right'; 
                        break;
                    case 'PageUp': 
                        e.preventDefault(); 
                        if (window.scrollMode === 'horizontal_1_col') {{
                            // In 1-col horizontal, Page Up scrolls back by one viewport width (one 'page')
                            x = Math.max(0, x - viewportW);
                        }} else {{ // horizontal_multi_col
                            // In multi-col horizontal, Page Up scrolls back by N column widths
                            x = Math.max(0, x - (colWidth * window.currentColumnCount));
                        }}
                        type = 'page-up'; 
                        break;
                    case 'PageDown': 
                        e.preventDefault(); 
                        if (window.scrollMode === 'horizontal_1_col') {{
                            // In 1-col horizontal, Page Down scrolls forward by one viewport width (one 'page')
                            x = Math.min(maxScrollX, x + viewportW);
                        }} else {{ // horizontal_multi_col
                            // In multi-col horizontal, Page Down scrolls forward by N column widths
                            x = Math.min(maxScrollX, x + (colWidth * window.currentColumnCount));
                        }}
                        type = 'page-down'; 
                        break;
                    case 'Home': 
                        e.preventDefault(); 
                        x = 0; 
                        type = 'home'; 
                        break;
                    case 'End': 
                        e.preventDefault(); 
                        x = maxScrollX; 
                        type = 'end'; 
                        break;
                }}
            }}
            // Execute scroll and report event
            if (type) {{
                smoothScrollTo(x, y);
                setTimeout(() => {{
                    sendScrollEvent(type);
                }}, 450);  // Report after animation starts
            }}
        }});
        """
        # ===================================================================
        # HTML CONTENT EXTRACTION
        # ===================================================================
        # PURPOSE: Extract just the body content from original HTML
        # 
        # WHY: User may load a full HTML document with <html>, <head>, etc.
        #      We only want the body content to put in our custom template
        # 
        # PROCESS:
        # 1. Check if original has <body> tags
        # 2. If yes, extract content between <body> and </body>
        # 3. If no, use entire original content (assume it's just body)
        # 
        # CASE-INSENSITIVE: Use .lower() to handle <body>, <BODY>, <Body>
        # ===================================================================
        original_html = self.original_html_content
        if '<body>' in original_html.lower() and '</body>' in original_html.lower():
            # Find body content
            start = original_html.lower().find('<body>') + 6
            end = original_html.lower().find('</body>', start)
            if end != -1:
                body_content = original_html[start:end]
            else:
                body_content = original_html
        else:
            # No body tags, use entire content
            body_content = original_html
        # ===================================================================
        # COMPLETE HTML ASSEMBLY
        # ===================================================================
        # Combine CSS, body content, and JavaScript into complete HTML
        # 
        # STRUCTURE:
        # <html>
        #   <head>
        #     <style>...</style>     CSS for columns
        #   </head>
        #   <body>
        #     <div class="content-container">
        #       {user's content}
        #     </div>
        #     <script>...</script>   JavaScript for navigation
        #   </body>
        # </html>
        # ===================================================================
        html_content = f"""
        <html>
            <head>
                {css}
            </head>
            <body>
                <div class="content-container">
                    {body_content}
                </div>
                <script>{js_script}</script>
            </body>
        </html>
        """
        # Load the generated HTML into WebView
        self.webview.load_html(html_content)
    def on_columns_changed(self, combo, pspec):
        """
        Column Dropdown Change Handler
        PURPOSE: React when user selects different column count / scroll mode
        SIGNAL: notify::selected
        - Emitted when dropdown selection changes
        - pspec: Property specification (not used here)
        DELAY PATTERN:
        - Wait 50ms before applying layout
        - WHY: Allows UI to update selection visually first
        - FUTURE: Could restore scroll position (parameter present but unused)
        PARAMETERS:
        - combo: The Gtk.DropDown widget
        - pspec: Property specification (unused)
        """
        selected_index = combo.get_selected()
        # Small delay before applying layout
        GLib.timeout_add(
            50, 
            lambda: self.apply_column_layout(selected_index, restore_position=True)
        )
    def on_size_changed(self, *args):
        """
        Window Resize Handler
        PURPOSE: Reapply layout when window size changes
        WHY NEEDED: Column widths are calculated based on window width
        When window resizes, we must recalculate column widths and potentially
        reapply the layout if the scroll mode depends on dimensions.
        DELAY:
        - 100ms delay prevents applying layout during resize animation
        - Waits for resize to complete
        - Reduces unnecessary recalculations
        SIGNALS:
        - notify::default-width: Window width changed
        - notify::default-height: Window height changed
        *args: Accepts any arguments (not used)
        """
        selected_index = self.columns_combo.get_selected()
        GLib.timeout_add(
            100, 
            lambda: self.apply_column_layout(
                selected_index, 
                restore_position=True
            )
        )
class App(Adw.Application):
    """
    Application Class
    PURPOSE: Main application object that manages the app lifecycle
    INHERITS FROM: Adw.Application
    - Provides: Application lifecycle management
    - Handles: Activation, command-line arguments, single-instance behavior
    - Integrates: With desktop environment (taskbar, notifications, etc.)
    APPLICATION ID: "org.example.Demo"
    - FORMAT: Reverse domain notation (like Java packages)
    - PURPOSE: Uniquely identifies this application
    - USED FOR: D-Bus communication, desktop files, settings storage
    - EXAMPLE: org.gnome.Calculator, org.mozilla.Firefox
    FLAGS: FLAGS_NONE
    - No special flags set
    - Other options: HANDLES_OPEN (for file associations),
                    HANDLES_COMMAND_LINE (for CLI args)
    LIFECYCLE:
    1. __init__: Create application object
    2. run(): Start application, enter main loop
    3. do_activate(): Called when app activates (creates window)
    4. Main loop runs (handles events)
    5. Exit when all windows close
    """
    def __init__(self):
        """
        Application Initialization
        SUPER CALL:
        Calls parent class (Adw.Application) __init__ with parameters
        - application_id: Unique identifier
        - flags: Application behavior flags
        """
        super().__init__(
            application_id="org.example.Demo",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
    def do_activate(self, *a):
        """
        Activation Handler
        CALLED WHEN:
        - Application first starts
        - Application is already running and activated again
        SINGLE INSTANCE:
        - Checks if window already exists (props.active_window)
        - If no window, creates new one
        - If window exists, just presents it (brings to front)
        WHY: Prevents multiple windows when user launches app twice
        PROCESS:
        1. Check for existing window
        2. Create window if needed
        3. Present window (show and bring to front)
        *a: Accept any arguments (unused)
        """
        if not self.props.active_window:
            # No window exists, create one
            self.win = Win(self)
        # Show and bring window to front
        self.win.present()
# ===========================================================================
# APPLICATION ENTRY POINT
# ===========================================================================
# This code runs when script is executed directly (not imported)
#
# PROCESS:
# 1. Create App instance
# 2. Start application with command-line arguments
# 3. Enter GTK main event loop
# 4. Exit with proper status code when app closes
#
# sys.argv: Command-line arguments (list of strings)
# sys.exit(): Exit with status code (0 = success, non-zero = error)
# ===========================================================================
if __name__ == "__main__":
    import sys
    app = App()
    sys.exit(app.run(sys.argv))

