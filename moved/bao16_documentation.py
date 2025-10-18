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
        # 
        # Gtk.StringList: A model that stores strings
        # WHY MODEL: GTK4 uses model-view architecture for lists/combos
        # BENEFIT: Separates data (StringList) from presentation (DropDown)
        # 
        # Gtk.DropDown: Modern GTK4 dropdown menu widget
        # selected=1: Start with "2 Columns" selected (index 1 = second item)
        # 
        # pack_end(): Places dropdown on the right side of header
        # notify::selected: Signal emitted when selection changes
        # -------------------------------------------------------------------
        string_list = Gtk.StringList()
        for i in range(1, 11):
            string_list.append(f"{i} Columns")
        
        self.columns_combo = Gtk.DropDown(model=string_list, selected=1)
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
        # WHY STORE: Used when reapplying layout after window resize or
        #            sidebar toggle
        # 
        # GLib.idle_add(): Schedule function to run when event loop is idle
        # WHY: Ensures window is fully initialized before applying layout
        # LAMBDA: Anonymous function that calls apply_column_layout
        # -------------------------------------------------------------------
        self.current_columns = 2
        self.columns_combo.set_selected(self.current_columns - 1)
        GLib.idle_add(lambda: self.apply_column_layout(self.current_columns - 1))

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
                self.current_columns - 1, 
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
        - wheel: Mouse wheel scroll
        - wheel-y: Vertical scroll in single-column mode
        - arrow-left/right: Keyboard arrow navigation
        - page-up/down: Page navigation
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
                'wheel': '🖱️ ',
                'wheel-y': '↕️ ',
                'arrow-left': '⬅️ ',
                'arrow-right': '➡️',
                'page-up': '⬆️ ',
                'page-down': '⬇️',
                'home': '🏠',
                'end': '🔚'
            }
            icon = icons.get(event_type, '📜')
            
            # Format and print event information
            # Different format for vertical vs horizontal scrolling
            if event_type.startswith('wheel'):
                print(f"{icon} Scroll Event: {event_type:12s} | "
                      f"ScrollY: {scroll_y:5.0f}")
            else:
                print(f"{icon} Scroll Event: {event_type:12s} | "
                      f"ScrollX: {scroll_x:5.0f} | Column: {column}")
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
                                self.current_columns - 1
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
    
    def apply_column_layout(self, selected_column_index, restore_position=False):
        """
        Apply Multi-Column Layout to HTML Content
        
        PURPOSE: Transform HTML content into CSS multi-column layout
        
        THIS IS THE CORE FUNCTION OF THE APPLICATION
        
        PROCESS:
        1. Calculate number of columns from dropdown selection
        2. Generate CSS for column layout
        3. Generate JavaScript for column navigation
        4. Extract body content from original HTML
        5. Combine everything into new HTML document
        6. Load into WebView
        
        CSS COLUMNS:
        column-count: Splits content into N columns
        column-gap: Space between columns
        break-inside: Controls how elements break across columns
        
        WHY REGENERATE ENTIRE HTML:
        - Changing column count requires new CSS
        - WebKit doesn't provide API to modify CSS dynamically
        - Easier to regenerate than manipulate DOM
        
        JAVASCRIPT FEATURES:
        1. Column-snapped scrolling (scroll exactly one column at a time)
        2. Smooth scroll animations (400ms ease-in-out)
        3. Keyboard navigation (arrows, page up/down, home/end)
        4. Mouse wheel column navigation
        5. Event reporting to Python
        
        PARAMETERS:
        - selected_column_index: Index from dropdown (0=1 column, 9=10 columns)
        - restore_position: Whether to maintain scroll position (unused currently,
                           but intended for future enhancement)
        """
        # Convert index to actual column count (index 0 = 1 column)
        num_columns = selected_column_index + 1
        self.current_columns = num_columns
        
        # ===================================================================
        # CSS GENERATION FOR COLUMN LAYOUT
        # ===================================================================
        # CSS column-count: Modern CSS property for newspaper-style columns
        # 
        # HOW IT WORKS:
        # - Browser automatically flows content into specified # of columns
        # - Content fills first column, then second, etc.
        # - Height adjusts automatically based on content
        # 
        # break-inside: avoid: Prevents elements from splitting across columns
        # break-inside: auto: Allows text to flow naturally
        # ===================================================================
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
        
        # ===================================================================
        # JAVASCRIPT GENERATION FOR INTERACTIVE NAVIGATION
        # ===================================================================
        # This JavaScript code runs in the WebView and provides:
        # 1. Column-snapped scrolling
        # 2. Smooth animations
        # 3. Keyboard navigation
        # 4. Event reporting to Python
        # ===================================================================
        js_script = f"""
        // ===============================================================
        // GLOBAL STATE
        // ===============================================================
        // Store current column count (injected from Python)
        window.currentColumnCount = {num_columns};

        // ===============================================================
        // COLUMN WIDTH CALCULATION
        // ===============================================================
        // PURPOSE: Calculate exact width of one column including gap
        // 
        // FORMULA: (containerWidth - totalGap) / columnCount + gap
        // 
        // WHY INCLUDE GAP: When scrolling, we want to scroll by 
        //                  (columnWidth + gap) to land exactly at 
        //                  start of next column
        // ===============================================================
        function getColumnWidth() {{
            const container = document.querySelector('.content-container');
            if (!container) return 0;
            
            const style = window.getComputedStyle(container);
            const colCount = window.currentColumnCount;
            const gap = parseFloat(style.columnGap) || 0;
            const totalGap = gap * (colCount - 1);
            const columnWidth = (container.offsetWidth - totalGap) / colCount;
            
            return columnWidth + gap;  // Return column + gap
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
        // - column: Current column number
        // ===============================================================
        function sendScrollEvent(eventType) {{
            if (window.webkit && 
                window.webkit.messageHandlers && 
                window.webkit.messageHandlers.scrollEvent) {{
                
                const colWidth = getColumnWidth();
                const currentColumn = colWidth > 0 
                    ? Math.round(window.scrollX / colWidth) 
                    : 0;
                
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
        // - t < 0.5: Accelerate (4t³)
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
        // SNAP SCROLL TO COLUMN BOUNDARY
        // ===============================================================
        // PURPOSE: Ensure scroll position aligns exactly with column start
        // 
        // WHY: When user scrolls freely (e.g., trackpad swipe), they may
        //      end up between columns. This snaps to nearest column.
        // 
        // WHEN: Called after scroll stops (100ms timeout)
        // 
        // HOW:
        // 1. Get current scroll position
        // 2. Divide by column width
        // 3. Round to nearest column
        // 4. Multiply back by column width
        // 5. Scroll to that position
        // ===============================================================
        function snapScroll() {{
            if (window.currentColumnCount === 1) return;
            
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
        // PURPOSE: Snap to column after scrolling stops
        // 
        // TIMEOUT PATTERN:
        // - Clear previous timeout on each scroll event
        // - Set new timeout for 100ms
        // - If no scroll for 100ms, snap to column
        // 
        // WHY: Prevents snapping during active scrolling
        //      Only snaps after user stops scrolling
        // ===============================================================
        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) snapScroll();
            }}, 100);
        }});

        // ===============================================================
        // MOUSE WHEEL NAVIGATION
        // ===============================================================
        // PURPOSE: Navigate one column per wheel event
        // 
        // SINGLE COLUMN MODE:
        // - Use default vertical scrolling
        // - Report event to Python
        // 
        // MULTI-COLUMN MODE:
        // - Prevent default scroll
        // - Scroll exactly one column left or right
        // - Use smooth animation
        // 
        // e.deltaY > 0: Scrolled down/forward -> go right
        // e.deltaY < 0: Scrolled up/backward -> go left
        // 
        // passive: false: Allows preventDefault() to work
        // ===============================================================
        document.addEventListener('wheel', function(e) {{
            if (window.currentColumnCount === 1) {{
                sendScrollEvent('wheel-y');
                return;
            }}
            
            e.preventDefault();
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            
            const scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            const target = Math.round(
                (window.scrollX + scrollDist) / colWidth
            ) * colWidth;
            
            smoothScrollTo(target, window.scrollY);
            sendScrollEvent('wheel');
        }}, {{ passive: false }});

        // ===============================================================
        // KEYBOARD NAVIGATION
        // ===============================================================
        // PURPOSE: Navigate columns with keyboard
        // 
        // SINGLE COLUMN MODE (vertical scrolling):
        // - Arrow Up/Down: Scroll 80% of viewport height
        // - Page Up/Down: Scroll full viewport height
        // - Home/End: Jump to top/bottom
        // 
        // MULTI-COLUMN MODE (horizontal navigation):
        // - Arrow Left/Right: Move one column
        // - Page Up/Down: Move two columns (faster navigation)
        // - Home/End: Jump to first/last column
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
            const viewportH = window.innerHeight;
            const maxScrollX = document.body.scrollWidth - window.innerWidth;
            const maxScrollY = document.body.scrollHeight - viewportH;

            let x = window.scrollX, y = window.scrollY, type = null;

            if (window.currentColumnCount === 1) {{
                // SINGLE COLUMN: Vertical scrolling
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
                // MULTI-COLUMN: Horizontal navigation
                switch (e.key) {{
                    case 'ArrowLeft': 
                        e.preventDefault(); 
                        x = Math.max(0, x - colWidth); 
                        type = 'arrow-left'; 
                        break;
                    case 'ArrowRight': 
                        e.preventDefault(); 
                        x = Math.min(maxScrollX, x + colWidth); 
                        type = 'arrow-right'; 
                        break;
                    case 'PageUp': 
                        e.preventDefault(); 
                        x = Math.max(0, x - colWidth * 2); 
                        type = 'page-up'; 
                        break;
                    case 'PageDown': 
                        e.preventDefault(); 
                        x = Math.min(maxScrollX, x + colWidth * 2); 
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
        
        PURPOSE: React when user selects different column count
        
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
        selected = combo.get_selected()
        
        # Small delay before applying layout
        GLib.timeout_add(
            50, 
            lambda: self.apply_column_layout(selected, restore_position=True)
        )
    
    def on_size_changed(self, *args):
        """
        Window Resize Handler
        
        PURPOSE: Reapply layout when window size changes
        
        WHY NEEDED: Column widths are calculated based on window width
        When window resizes, we must recalculate column widths
        
        DELAY:
        - 100ms delay prevents applying layout during resize animation
        - Waits for resize to complete
        - Reduces unnecessary recalculations
        
        SIGNALS:
        - notify::default-width: Window width changed
        - notify::default-height: Window height changed
        
        *args: Accepts any arguments (not used)
        """
        GLib.timeout_add(
            100, 
            lambda: self.apply_column_layout(
                self.current_columns - 1, 
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
