#!/usr/bin/env python3
import os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, WebKit, GLib, Gdk
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Demo")
        self.set_default_size(800, 600)
        
        # Split view
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.set_content(self.split)
        
        # Sidebar
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.append(Gtk.Label(label="Sidebar"))
        self.split.set_sidebar(sidebar)
        
        # Content
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        
        # Add toggle sidebar button to header
        toggle_sidebar_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        toggle_sidebar_btn.connect("clicked", self.on_toggle_sidebar)
        header.pack_start(toggle_sidebar_btn)
        
        # Add open file button to header
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_file)
        header.pack_start(open_btn)
        
        # Add columns dropdown to header using StringList model
        string_list = Gtk.StringList()
        for i in range(1, 11):
            string_list.append(f"{i} Columns")
        
        self.columns_combo = Gtk.DropDown(model=string_list, selected=0)
        self.columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(self.columns_combo)
        
        header.set_title_widget(Gtk.Label(label="Header"))
        toolbar.add_top_bar(header)
        
        # Create WebView for content area
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        # Set up message handler to receive viewport text from JavaScript
        content_manager = self.webview.get_user_content_manager()
        content_manager.connect("script-message-received::viewportText", self.on_viewport_text_received)
        content_manager.register_script_message_handler("viewportText")
        content_manager.connect("script-message-received::viewportAnchor", self.on_viewport_anchor_received)
        content_manager.register_script_message_handler("viewportAnchor")
        content_manager.connect("script-message-received::scrollEvent", self.on_scroll_event_received)
        content_manager.register_script_message_handler("scrollEvent")
        
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an HTML file to view.</p></body></html>")
        
        # Store the original content to be able to reformat it
        self.original_html_content = "<h1>Welcome</h1><p>Select an HTML file to view.</p>"
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.webview.set_margin_top(10)
        self.webview.set_margin_bottom(10)
        self.webview.set_margin_start(10)
        self.webview.set_margin_end(10)
        content_box.append(self.webview)
        
        toolbar.set_content(content_box)
        
        self.split.set_content(toolbar)
        
        # Store current columns and connect to size allocation
        self.current_columns = 1
        self.pending_column_change = None
        self.stored_viewport_anchor = None
        self.connect("notify::default-width", self.on_size_changed)
        self.connect("notify::default-height", self.on_size_changed)
        
        # Breakpoint for responsive sidebar
        self.breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 768px")
        )
        self.breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.breakpoint)
    
    def on_toggle_sidebar(self, button):
        # Store current viewport position before toggle
        self.request_viewport_anchor()
        # Toggle sidebar
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        # Wait for sidebar animation, then reapply layout with position restore
        GLib.timeout_add(350, lambda: self.apply_column_layout(self.current_columns - 1, restore_position=True))
    
    def on_viewport_text_received(self, content_manager, js_result):
        """Callback when viewport text is sent from JavaScript"""
        try:
            # js_result is already a JSCValue, just convert to string
            viewport_text = js_result.to_string()
            print(f"📄 Viewport Text: {viewport_text}")
        except Exception as e:
            print(f"Error receiving viewport text: {e}")
    
    def on_scroll_event_received(self, content_manager, js_result):
        """Callback when scroll event info is sent from JavaScript"""
        try:
            import json
            event_data = json.loads(js_result.to_string())
            event_type = event_data.get('type', 'unknown')
            scroll_x = event_data.get('scrollX', 0)
            scroll_y = event_data.get('scrollY', 0)
            column = event_data.get('column', 0)
            
            # Create emoji/icon for different event types
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
            
            if event_type.startswith('wheel'):
                print(f"{icon} Scroll Event: {event_type:12s} | ScrollY: {scroll_y:5.0f}")
            else:
                print(f"{icon} Scroll Event: {event_type:12s} | ScrollX: {scroll_x:5.0f} | Column: {column}")
        except Exception as e:
            print(f"Error receiving scroll event: {e}")
    
    def on_viewport_anchor_received(self, content_manager, js_result):
        """Callback when viewport anchor data is sent from JavaScript"""
        try:
            import json
            anchor_data = json.loads(js_result.to_string())
            self.stored_viewport_anchor = anchor_data
            print(f"🔖 Stored anchor text: {anchor_data.get('text', '')[:60]}...")
        except Exception as e:
            print(f"Error receiving viewport anchor: {e}")
    
    def request_viewport_anchor(self):
        """Request JavaScript to send us the current viewport anchor"""
        js_code = """
        (function() {
            // Try multiple points near top-left to get accurate viewport text
            const testPoints = [[10, 10], [20, 20], [30, 30], [15, 25]];
            let bestText = '';
            
            for (let [x, y] of testPoints) {
                const elem = document.elementFromPoint(x, y);
                if (elem && elem.textContent && elem.textContent.trim()) {
                    const range = document.caretRangeFromPoint(x, y);
                    if (range && range.startContainer && range.startContainer.nodeType === Node.TEXT_NODE) {
                        const fullText = range.startContainer.textContent;
                        const offset = range.startOffset;
                        let text = fullText.substring(offset).trim();
                        
                        // Back up to word start
                        if (offset > 0 && fullText[offset - 1] && fullText[offset - 1].match(/\\w/)) {
                            const beforeText = fullText.substring(0, offset);
                            const lastSpace = beforeText.lastIndexOf(' ');
                            if (lastSpace !== -1) {
                                text = fullText.substring(lastSpace + 1).trim();
                            } else {
                                text = fullText.trim();
                            }
                        }
                        
                        if (text.length > 5) {
                            bestText = text;
                            break;
                        }
                    }
                }
            }
            
            // Truncate at word boundary
            if (bestText.length > 100) {
                bestText = bestText.substring(0, 100);
                const lastSpace = bestText.lastIndexOf(' ');
                if (lastSpace > 80) {
                    bestText = bestText.substring(0, lastSpace);
                }
            }
            
            if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.viewportAnchor) {
                window.webkit.messageHandlers.viewportAnchor.postMessage(JSON.stringify({
                    text: bestText,
                    scrollX: window.scrollX,
                    scrollY: window.scrollY
                }));
            }
        })();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None)
    
    def on_open_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open HTML File")
        
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML files")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(html_filter)
        filters.append(all_filter)
        dialog.set_filters(filters)
        
        dialog.open(self, None, self.on_file_dialog_response)
    
    def on_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                def load_file_in_thread():
                    try:
                        content_bytes = file.load_bytes(None)[0]
                        content = content_bytes.get_data().decode('utf-8')
                        self.original_html_content = content
                        GLib.idle_add(lambda: self.apply_column_layout(self.current_columns - 1))
                    except Exception as e:
                        print(f"Error reading file: {e}")
                        GLib.idle_add(lambda: self.show_error_dialog(f"Error loading file: {e}"))
                
                GLib.Thread.new(None, load_file_in_thread)
        except GLib.Error:
            pass
    
    def show_error_dialog(self, message):
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
        num_columns = selected_column_index + 1
        self.current_columns = num_columns
        
        # Store the anchor data to restore after layout
        anchor_to_restore = None
        if restore_position and self.stored_viewport_anchor:
            import json
            anchor_to_restore = json.dumps(self.stored_viewport_anchor).replace("'", "\\'")
        
        # Create CSS for fixed column layout
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
                column-count: {num_columns};
                column-gap: 20px;
                width: 100%;
                height: 100%;
                box-sizing: border-box;
            }}
            /* Ensure all elements flow properly in columns */
            .content-container * {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}
            /* Allow text flow in paragraphs */
            .content-container p,
            .content-container div,
            .content-container span {{
                break-inside: auto;
                page-break-inside: auto;
            }}
        </style>
        """
        
        # Add JavaScript for snapping scroll positions and keyboard navigation
        js_script = f"""
        // Store current column count
        window.currentColumnCount = {num_columns};
        
        // Function to calculate column width dynamically
        function getColumnWidth() {{
            const container = document.querySelector('.content-container');
            if (!container) return 0;
            
            // Get the computed style for the container
            const style = window.getComputedStyle(container);
            const containerWidth = container.offsetWidth;
            const colCount = window.currentColumnCount;
            const gap = parseFloat(style.columnGap) || 0;
            
            // Calculate the effective width for columns
            const totalGap = gap * (colCount - 1);
            const columnWidth = (containerWidth - totalGap) / colCount;
            
            return columnWidth + gap;  // Include gap for full column width
        }}
        
        window.getColumnWidth = getColumnWidth;
        
        // Function to calculate viewport width (number of visible columns)
        function getViewportWidth() {{
            return window.innerWidth || document.documentElement.clientWidth;
        }}
        
        // Function to get text at viewport start
        function getViewportText() {{
            // Try points at the top-left of the visible area
            const testPoints = [[5, 5], [10, 10], [15, 15], [20, 20], [10, 25], [25, 10]];
            
            let bestText = '';
            let bestLength = 0;
            
            for (let [x, y] of testPoints) {{
                const elem = document.elementFromPoint(x, y);
                if (!elem || !elem.textContent) continue;
                
                const range = document.caretRangeFromPoint(x, y);
                if (range && range.startContainer) {{
                    let node = range.startContainer;
                    let text = '';
                    
                    if (node.nodeType === Node.TEXT_NODE) {{
                        // We hit a text node directly
                        const fullText = node.textContent;
                        const offset = range.startOffset;
                        text = fullText.substring(offset);
                        
                        // Back up to start of word if needed
                        if (offset > 0 && /\w/.test(fullText[offset - 1])) {{
                            const before = fullText.substring(0, offset);
                            const lastSpace = before.lastIndexOf(' ');
                            if (lastSpace !== -1) {{
                                text = fullText.substring(lastSpace + 1);
                            }} else {{
                                text = fullText;
                            }}
                        }}
                    }} else if (node.nodeType === Node.ELEMENT_NODE) {{
                        // We hit an element, get its text
                        text = node.textContent;
                    }}
                    
                    text = text.trim().replace(/\s+/g, ' ');
                    
                    // Prefer longer, more substantial text
                    if (text.length > bestLength && text.length > 5) {{
                        bestText = text;
                        bestLength = text.length;
                    }}
                }}
            }}
            
            // Truncate at word boundary
            if (bestText.length > 80) {{
                let truncated = bestText.substring(0, 80);
                const lastSpace = truncated.lastIndexOf(' ');
                if (lastSpace > 60) {{
                    bestText = truncated.substring(0, lastSpace);
                }} else {{
                    bestText = truncated;
                }}
            }}
            
            return bestText;
        }}
        
        // Function to send viewport text to Python
        function sendViewportText() {{
            const text = getViewportText();
            if (text && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.viewportText) {{
                window.webkit.messageHandlers.viewportText.postMessage(text);
            }}
        }}
        
        // Function to send scroll event info to Python
        function sendScrollEvent(eventType) {{
            if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.scrollEvent) {{
                const colWidth = getColumnWidth();
                const currentColumn = colWidth > 0 ? Math.round(window.scrollX / colWidth) : 0;
                window.webkit.messageHandlers.scrollEvent.postMessage(JSON.stringify({{
                    type: eventType,
                    scrollX: window.scrollX,
                    scrollY: window.scrollY,
                    column: currentColumn
                }}));
            }}
        }}

        function smoothScrollTo(xTarget, yTarget) {{
            const startX = window.scrollX;
            const startY = window.scrollY;
            const distanceX = xTarget - startX;
            const distanceY = yTarget - startY;
            const duration = 400;
            const startTime = performance.now();

            function step(time) {{
                const elapsed = time - startTime;
                const progress = Math.min(elapsed / duration, 1);
                
                const t = progress < 0.5 
                    ? 4 * progress * progress * progress 
                    : (progress - 1) * (2 * progress - 2) * (2 * progress - 2) + 1;
                
                const currentX = startX + distanceX * t;
                const currentY = startY + distanceY * t;
                window.scrollTo(currentX, currentY);
                
                if (progress < 1) {{
                    requestAnimationFrame(step);
                }}
            }}

            requestAnimationFrame(step);
        }}
        
        // Function to snap scroll position to column boundaries
        function snapScroll() {{
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            
            const currentScroll = window.scrollX;
            const currentColumn = Math.round(currentScroll / colWidth);
            const targetScroll = currentColumn * colWidth;
            
            if (Math.abs(currentScroll - targetScroll) > 1) {{
                window.scrollTo(targetScroll, window.scrollY);
            }}
            
            // Send viewport text to Python terminal
            sendViewportText();
        }}
        
        // Add scroll event listener to handle snapping
        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(snapScroll, 100);
        }});
        
        // Handle mouse wheel with column snapping for horizontal and normal scrolling for vertical
        document.addEventListener('wheel', function(e) {{
            if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {{
                // Vertical scrolling - allow normal scrolling
                sendScrollEvent('wheel-y');
            }} else if (Math.abs(e.deltaX) > 0) {{
                // Horizontal scrolling - snap to columns
                const colWidth = getColumnWidth();
                if (colWidth <= 0) return;
                
                const currentScroll = window.scrollX;
                const scrollDistance = e.deltaX > 0 ? colWidth : -colWidth;
                const targetScroll = Math.round((currentScroll + scrollDistance) / colWidth) * colWidth;
                
                window.scrollTo(targetScroll, window.scrollY);
                
                // Send scroll event to Python
                sendScrollEvent('wheel');
                setTimeout(sendViewportText, 50);
            }}
        }}, {{ passive: false }});
        
        // Add keyboard navigation
        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey || e.shiftKey || e.altKey || e.metaKey) return;

            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;

            const viewportWidth = getViewportWidth();
            const viewportHeight = window.innerHeight;
            const currentScrollX = window.scrollX;
            const currentScrollY = window.scrollY;
            const maxScrollX = Math.max(0, document.body.scrollWidth - viewportWidth);
            const maxScrollY = Math.max(0, document.body.scrollHeight - viewportHeight);

            let targetScrollX = currentScrollX;
            let targetScrollY = currentScrollY;
            let eventType = null;

            switch(e.key) {{
                case 'ArrowLeft':
                    e.preventDefault();
                    targetScrollX = Math.max(0, currentScrollX - colWidth);
                    eventType = 'arrow-left';
                    break;
                case 'ArrowRight':
                    e.preventDefault();
                    targetScrollX = Math.min(maxScrollX, currentScrollX + colWidth);
                    eventType = 'arrow-right';
                    break;
                case 'ArrowUp':
                    e.preventDefault();
                    targetScrollY = Math.max(0, currentScrollY - viewportHeight * 0.8);
                    eventType = 'arrow-up';
                    break;
                case 'ArrowDown':
                    e.preventDefault();
                    targetScrollY = Math.min(maxScrollY, currentScrollY + viewportHeight * 0.8);
                    eventType = 'arrow-down';
                    break;
                case 'PageUp':
                    e.preventDefault();
                    targetScrollY = Math.max(0, currentScrollY - viewportHeight);
                    eventType = 'page-up';
                    break;
                case 'PageDown':
                    e.preventDefault();
                    targetScrollY = Math.min(maxScrollY, currentScrollY + viewportHeight);
                    eventType = 'page-down';
                    break;
                case 'Home':
                    e.preventDefault();
                    targetScrollX = 0;
                    targetScrollY = 0;
                    eventType = 'home';
                    break;
                case 'End':
                    e.preventDefault();
                    targetScrollX = maxScrollX;
                    targetScrollY = maxScrollY;
                    eventType = 'end';
                    break;
                default:
                    return;
            }}

            // Smooth scroll for better UX
            smoothScrollTo(targetScrollX, targetScrollY);
            
            // Send scroll event to Python
            if (eventType) {{
                setTimeout(function() {{
                    sendScrollEvent(eventType);
                    sendViewportText();
                }}, 450); // Wait for smooth scroll to complete
            }}
        }});
        
        // Initial viewport text print
        window.addEventListener('load', function() {{
            setTimeout(function() {{
                sendViewportText();
            }}, 100);
        }});
        
        // Also send on column layout changes
        setTimeout(sendViewportText, 150);
        """
        
        original_html = self.original_html_content
        # Extract body content if it's a full HTML document
        if '<body>' in original_html.lower() and '</body>' in original_html.lower():
            start = original_html.lower().find('<body>') + 6
            end = original_html.lower().find('</body>', start)
            if end != -1:
                body_content = original_html[start:end]
            else:
                body_content = original_html
        else:
            body_content = original_html
        
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
        
        self.webview.load_html(html_content)
        
        # If we need to restore position, wait for page load then restore
        if restore_position and anchor_to_restore:
            # Use load-changed signal to detect when page is loaded
            def on_load_changed(webview, load_event):
                if load_event == WebKit.LoadEvent.FINISHED:
                    # Page is fully loaded, now restore position
                    GLib.timeout_add(100, lambda: self.restore_scroll_position(anchor_to_restore))
                    webview.disconnect(handler_id)
            
            handler_id = self.webview.connect("load-changed", on_load_changed)
        else:
            # Print to terminal after a short delay to let page load
            GLib.timeout_add(200, self.print_viewport_text)
    
    def print_viewport_text(self):
        """Request viewport text from JavaScript"""
        print(f"\n=== Viewport Status ===")
        print(f"Columns: {self.current_columns}")
        print(f"Waiting for viewport text...")
        return False
    
    def restore_scroll_position(self, anchor_json):
        """Restore scroll position after page load"""
        js_code = f"""
        (function() {{
            try {{
                const anchorData = JSON.parse('{anchor_json}');
                const searchText = anchorData.text.trim().replace(/\\s+/g, ' ');
                const targetScrollY = anchorData.scrollY || 0;
                
                console.log('🔍 Searching for:', searchText.substring(0, 60));
                console.log('🔍 Target scroll Y:', targetScrollY);
                
                // Create a more flexible search - use first 8-10 words
                const words = searchText.split(' ');
                const searchPhrase = words.slice(0, Math.min(8, words.length)).join(' ');
                
                console.log('🔍 Search phrase:', searchPhrase);
                
                let foundElement = null;
                
                // Method 1: Use TreeWalker to search all text nodes
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );
                
                while (walker.nextNode()) {{
                    const node = walker.currentNode;
                    const nodeText = node.textContent.trim().replace(/\\s+/g, ' ');
                    
                    // Check if this text node contains our search phrase
                    if (nodeText.includes(searchPhrase)) {{
                        foundElement = node.parentElement;
                        console.log('✓ Found in text node, parent:', foundElement.tagName);
                        break;
                    }}
                    
                    // Also try checking if search phrase is at the start
                    if (nodeText.startsWith(words[0]) && nodeText.includes(words[1] || '')) {{
                        foundElement = node.parentElement;
                        console.log('✓ Found partial match, parent:', foundElement.tagName);
                        break;
                    }}
                }}
                
                // Method 2: If not found, search all elements
                if (!foundElement) {{
                    const allElements = document.querySelectorAll('p, div, h1, h2, h3, h4, h5, h6, li, span, td, th, a, b, i, strong');
                    for (let elem of allElements) {{
                        const elemText = elem.textContent.trim().replace(/\\s+/g, ' ');
                        if (elemText.includes(searchPhrase) || elemText.startsWith(words.slice(0, 3).join(' '))) {{
                            foundElement = elem;
                            console.log('✓ Found in element:', elem.tagName);
                            break;
                        }}
                    }}
                }}
                
                if (foundElement) {{
                    // Wait a bit for layout to settle
                    setTimeout(function() {{
                        const rect = foundElement.getBoundingClientRect();
                        const absoluteX = window.scrollX + rect.left;
                        
                        console.log('📍 Element position - Left:', rect.left, 'Absolute:', absoluteX);
                        
                        // Snap to nearest column
                        const colWidth = window.getColumnWidth ? window.getColumnWidth() : window.innerWidth;
                        console.log('📏 Column width:', colWidth);
                        
                        if (colWidth > 0) {{
                            const targetColumn = Math.round(absoluteX / colWidth);
                            const targetScrollX = Math.max(0, targetColumn * colWidth);
                            
                            console.log('🎯 Target column:', targetColumn, 'Target scroll X:', targetScrollX);
                            
                            window.scrollTo(targetScrollX, targetScrollY);
                            
                            // Send updated viewport text after scroll
                            setTimeout(function() {{
                                const testPoints = [[5, 5], [10, 10], [15, 15]];
                                for (let [x, y] of testPoints) {{
                                    const vpElement = document.elementFromPoint(x, y);
                                    if (vpElement) {{
                                        const range = document.caretRangeFromPoint(x, y);
                                        if (range && range.startContainer) {{
                                            let node = range.startContainer;
                                            let vpText = '';
                                            
                                            if (node.nodeType === Node.TEXT_NODE) {{
                                                vpText = node.textContent.substring(range.startOffset);
                                            }} else {{
                                                vpText = node.textContent;
                                            }}
                                            
                                            vpText = vpText.trim().replace(/\\s+/g, ' ');
                                            
                                            if (vpText.length > 10) {{
                                                if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.viewportText) {{
                                                    window.webkit.messageHandlers.viewportText.postMessage(vpText.substring(0, 80));
                                                }}
                                                console.log('✅ Restored viewport text:', vpText.substring(0, 50));
                                                break;
                                            }}
                                        }}
                                    }}
                                }}
                            }}, 100);
                        }}
                    }}, 50);
                }} else {{
                    console.log('❌ Could not find text:', searchPhrase);
                    // Still restore vertical position if text not found
                    window.scrollTo(window.scrollX, targetScrollY);
                }}
            }} catch (e) {{
                console.error('Error restoring position:', e);
            }}
        }})();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None)
        return False
    
    def on_columns_changed(self, combo, pspec):
        selected = combo.get_selected()
        # Store current viewport before changing columns
        self.request_viewport_anchor()
        # Small delay to let anchor be stored, then apply layout
        GLib.timeout_add(50, lambda: self.apply_column_layout(selected, restore_position=True))
    
    def on_size_changed(self, *args):
        # Store viewport before resize, then update column count after a delay
        self.request_viewport_anchor()
        GLib.timeout_add(100, lambda: self.apply_column_layout(self.current_columns - 1, restore_position=True))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.Demo",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
    
    def do_activate(self, *a):
        if not self.props.active_window:
            self.win = Win(self)
        self.win.present()

if __name__ == "__main__":
    import sys
    app = App()
    sys.exit(app.run(sys.argv))
