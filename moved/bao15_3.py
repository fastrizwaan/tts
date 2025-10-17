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
        
        # Add fixed width dropdown (50px to 500px)
        width_string_list = Gtk.StringList()
        for i in range(50, 501, 50):
            width_string_list.append(f"{i}px")
        width_string_list.append("Auto")  # Add auto option to disable fixed width
        
        self.width_combo = Gtk.DropDown(model=width_string_list, selected=10)  # Default to Auto (index 10)
        self.width_combo.connect("notify::selected", self.on_width_changed)
        header.pack_end(self.width_combo)
        
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
        
        # Store current settings
        self.current_columns = 2
        self.current_width = None  # None means auto (disabled)
        self.columns_combo.set_selected(self.current_columns - 1)
        # Apply default column layout after window initializes
        GLib.idle_add(lambda: self.apply_layout())
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
        GLib.timeout_add(350, lambda: self.apply_layout(restore_position=True))
    
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
                        if (offset > 0 && fullText[offset - 1] && fullText[offset - 1].match(/\w/)) {
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
                        GLib.idle_add(lambda: self.apply_layout())
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
    
    def apply_layout(self, restore_position=False):
        # Determine which layout to use
        selected_width_index = self.width_combo.get_selected()
        width_options = 11  # 50px to 500px (10 options) + 1 "Auto"
        if selected_width_index < width_options - 1:  # Not "Auto"
            # Use fixed width
            width_value = 50 + selected_width_index * 50
            self.current_width = width_value
            self.current_columns = None
            self.apply_fixed_width_layout(width_value, restore_position)
        else:
            # Use columns layout
            selected_column_index = self.columns_combo.get_selected()
            num_columns = selected_column_index + 1
            self.current_columns = num_columns
            self.current_width = None
            self.apply_column_layout(num_columns, restore_position)
    
    def apply_column_layout(self, num_columns, restore_position=False):
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
        window.currentColumnCount = {num_columns};

        function getColumnWidth() {{
            const container = document.querySelector('.content-container');
            if (!container) return 0;
            const style = window.getComputedStyle(container);
            const colCount = window.currentColumnCount;
            const gap = parseFloat(style.columnGap) || 0;
            const totalGap = gap * (colCount - 1);
            const columnWidth = (container.offsetWidth - totalGap) / colCount;
            return columnWidth + gap;
        }}

        function getViewportWidth() {{
            return window.innerWidth || document.documentElement.clientWidth;
        }}

        function getViewportText() {{
            const testPoints = [[5, 5], [10, 10], [15, 15], [20, 20]];
            let bestText = '', bestLength = 0;
            for (let [x, y] of testPoints) {{
                const elem = document.elementFromPoint(x, y);
                if (!elem || !elem.textContent) continue;
                const range = document.caretRangeFromPoint(x, y);
                if (range && range.startContainer) {{
                    let node = range.startContainer;
                    let text = '';
                    if (node.nodeType === Node.TEXT_NODE) {{
                        const fullText = node.textContent;
                        const offset = range.startOffset;
                        text = fullText.substring(offset).trim();
                    }} else text = node.textContent.trim();
                    text = text.replace(/\s+/g, ' ');
                    if (text.length > bestLength && text.length > 5) {{
                        bestText = text;
                        bestLength = text.length;
                    }}
                }}
            }}
            if (bestText.length > 80) {{
                let truncated = bestText.substring(0, 80);
                const lastSpace = truncated.lastIndexOf(' ');
                if (lastSpace > 60) bestText = truncated.substring(0, lastSpace);
                else bestText = truncated;
            }}
            return bestText;
        }}

        function sendViewportText() {{
            const text = getViewportText();
            if (text && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.viewportText) {{
                window.webkit.messageHandlers.viewportText.postMessage(text);
            }}
        }}

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
                const t = progress < 0.5 ? 4 * progress * progress * progress : (progress - 1) * (2 * progress - 2) * (2 * progress - 2) + 1;
                window.scrollTo(startX + distanceX * t, startY + distanceY * t);
                if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
        }}

        function snapScroll() {{
            if (window.currentColumnCount === 1) return;
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const currentScroll = window.scrollX;
            const target = Math.round(currentScroll / colWidth) * colWidth;
            if (Math.abs(currentScroll - target) > 1) window.scrollTo(target, window.scrollY);
            sendViewportText();
        }}

        function highlightAnchorText(searchText) {{
            const words = searchText.trim().split(' ').slice(0, 6);
            const phrase = words.join(' ');
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {{
                const node = walker.currentNode;
                if (node.textContent.includes(phrase)) {{
                    const range = document.createRange();
                    const index = node.textContent.indexOf(phrase);
                    range.setStart(node, index);
                    range.setEnd(node, index + phrase.length);
                    const mark = document.createElement('mark');
                    mark.style.background = 'rgba(255, 230, 120, 0.7)';
                    mark.style.transition = 'background 1.5s ease';
                    range.surroundContents(mark);
                    setTimeout(() => (mark.style.background = 'transparent'), 1500);
                    return;
                }}
            }}
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) snapScroll();
                else sendViewportText();
            }}, 100);
        }});

        document.addEventListener('wheel', function(e) {{
            if (window.currentColumnCount === 1) {{
                sendScrollEvent('wheel-y');
                return;
            }}
            e.preventDefault();
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            const target = Math.round((window.scrollX + scrollDist) / colWidth) * colWidth;
            smoothScrollTo(target, window.scrollY);
            sendScrollEvent('wheel');
            setTimeout(sendViewportText, 50);
        }}, {{ passive: false }});

        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            const colWidth = getColumnWidth();
            const viewportH = window.innerHeight;
            const maxScrollX = document.body.scrollWidth - window.innerWidth;
            const maxScrollY = document.body.scrollHeight - viewportH;

            let x = window.scrollX, y = window.scrollY, type = null;

            if (window.currentColumnCount === 1) {{
                switch (e.key) {{
                    case 'ArrowUp': e.preventDefault(); y = Math.max(0, y - viewportH * 0.8); type = 'arrow-up'; break;
                    case 'ArrowDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH * 0.8); type = 'arrow-down'; break;
                    case 'PageUp': e.preventDefault(); y = Math.max(0, y - viewportH); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); y = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); y = maxScrollY; type = 'end'; break;
                }}
            }} else {{
                switch (e.key) {{
                    case 'ArrowLeft': e.preventDefault(); x = Math.max(0, x - colWidth); type = 'arrow-left'; break;
                    case 'ArrowRight': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth); type = 'arrow-right'; break;
                    case 'PageUp': e.preventDefault(); x = Math.max(0, x - colWidth * 2); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth * 2); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); x = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); x = maxScrollX; type = 'end'; break;
                }}
            }}

            if (type) {{
                smoothScrollTo(x, y);
                setTimeout(() => {{
                    sendScrollEvent(type);
                    sendViewportText();
                    highlightAnchorText(getViewportText());
                }}, 450);
            }}
        }});

        window.addEventListener('load', function() {{
            setTimeout(() => {{
                sendViewportText();
                highlightAnchorText(getViewportText());
            }}, 150);
        }});
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
    
    def apply_fixed_width_layout(self, width, restore_position=False):
        # Store the anchor data to restore after layout
        anchor_to_restore = None
        if restore_position and self.stored_viewport_anchor:
            import json
            anchor_to_restore = json.dumps(self.stored_viewport_anchor).replace("'", "\\'")
        
        # Create CSS for fixed width layout
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
                width: {width}px;
                height: 100%;
                box-sizing: border-box;
            }}
            /* Ensure all elements flow properly */
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
        window.currentColumnCount = 1; // Fixed width behaves like single column

        function getColumnWidth() {{
            const container = document.querySelector('.content-container');
            if (!container) return 0;
            return container.offsetWidth;
        }}

        function getViewportWidth() {{
            return window.innerWidth || document.documentElement.clientWidth;
        }}

        function getViewportText() {{
            const testPoints = [[5, 5], [10, 10], [15, 15], [20, 20]];
            let bestText = '', bestLength = 0;
            for (let [x, y] of testPoints) {{
                const elem = document.elementFromPoint(x, y);
                if (!elem || !elem.textContent) continue;
                const range = document.caretRangeFromPoint(x, y);
                if (range && range.startContainer) {{
                    let node = range.startContainer;
                    let text = '';
                    if (node.nodeType === Node.TEXT_NODE) {{
                        const fullText = node.textContent;
                        const offset = range.startOffset;
                        text = fullText.substring(offset).trim();
                    }} else text = node.textContent.trim();
                    text = text.replace(/\s+/g, ' ');
                    if (text.length > bestLength && text.length > 5) {{
                        bestText = text;
                        bestLength = text.length;
                    }}
                }}
            }}
            if (bestText.length > 80) {{
                let truncated = bestText.substring(0, 80);
                const lastSpace = truncated.lastIndexOf(' ');
                if (lastSpace > 60) bestText = truncated.substring(0, lastSpace);
                else bestText = truncated;
            }}
            return bestText;
        }}

        function sendViewportText() {{
            const text = getViewportText();
            if (text && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.viewportText) {{
                window.webkit.messageHandlers.viewportText.postMessage(text);
            }}
        }}

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
                const t = progress < 0.5 ? 4 * progress * progress * progress : (progress - 1) * (2 * progress - 2) * (2 * progress - 2) + 1;
                window.scrollTo(startX + distanceX * t, startY + distanceY * t);
                if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
        }}

        function snapScroll() {{
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const currentScroll = window.scrollX;
            const target = Math.round(currentScroll / colWidth) * colWidth;
            if (Math.abs(currentScroll - target) > 1) window.scrollTo(target, window.scrollY);
            sendViewportText();
        }}

        function highlightAnchorText(searchText) {{
            const words = searchText.trim().split(' ').slice(0, 6);
            const phrase = words.join(' ');
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {{
                const node = walker.currentNode;
                if (node.textContent.includes(phrase)) {{
                    const range = document.createRange();
                    const index = node.textContent.indexOf(phrase);
                    range.setStart(node, index);
                    range.setEnd(node, index + phrase.length);
                    const mark = document.createElement('mark');
                    mark.style.background = 'rgba(255, 230, 120, 0.7)';
                    mark.style.transition = 'background 1.5s ease';
                    range.surroundContents(mark);
                    setTimeout(() => (mark.style.background = 'transparent'), 1500);
                    return;
                }}
            }}
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                snapScroll();
                sendViewportText();
            }}, 100);
        }});

        document.addEventListener('wheel', function(e) {{
            e.preventDefault();
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            const target = Math.round((window.scrollX + scrollDist) / colWidth) * colWidth;
            smoothScrollTo(target, window.scrollY);
            sendScrollEvent('wheel');
            setTimeout(sendViewportText, 50);
        }}, {{ passive: false }});

        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            const colWidth = getColumnWidth();
            const viewportH = window.innerHeight;
            const maxScrollX = document.body.scrollWidth - window.innerWidth;
            const maxScrollY = document.body.scrollHeight - viewportH;

            let x = window.scrollX, y = window.scrollY, type = null;

            switch (e.key) {{
                case 'ArrowLeft': e.preventDefault(); x = Math.max(0, x - colWidth); type = 'arrow-left'; break;
                case 'ArrowRight': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth); type = 'arrow-right'; break;
                case 'PageUp': e.preventDefault(); x = Math.max(0, x - colWidth * 2); type = 'page-up'; break;
                case 'PageDown': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth * 2); type = 'page-down'; break;
                case 'Home': e.preventDefault(); x = 0; type = 'home'; break;
                case 'End': e.preventDefault(); x = maxScrollX; type = 'end'; break;
            }}

            if (type) {{
                smoothScrollTo(x, y);
                setTimeout(() => {{
                    sendScrollEvent(type);
                    sendViewportText();
                    highlightAnchorText(getViewportText());
                }}, 450);
            }}
        }});

        window.addEventListener('load', function() {{
            setTimeout(() => {{
                sendViewportText();
                highlightAnchorText(getViewportText());
            }}, 150);
        }});
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
        if self.current_columns:
            print(f"Columns: {self.current_columns}")
        elif self.current_width:
            print(f"Fixed Width: {self.current_width}px")
        else:
            print("Layout: Auto")
        print(f"Waiting for viewport text...")
        return False
    
    def restore_scroll_position(self, anchor_json):
        js_code = f"""
        (function() {{
            try {{
                const anchor = JSON.parse('{anchor_json}');
                const searchText = (anchor.text || '').trim().replace(/\s+/g, ' ');
                const fallbackY = anchor.scrollY || 0;

                // --- helpers already compatible with your page ---
                function getColumnWidth() {{
                    const container = document.querySelector('.content-container');
                    if (!container) return 0;
                    const style = getComputedStyle(container);
                    const colCount = window.currentColumnCount || 1;
                    const gap = parseFloat(style.columnGap) || 0;
                    const totalGap = gap * (colCount - 1);
                    const columnWidth = (container.offsetWidth - totalGap) / colCount;
                    return columnWidth + gap;
                }}

                function highlightRange(range) {{
                    try {{
                        const mark = document.createElement('mark');
                        mark.style.background = 'rgba(255, 230, 120, 0.8)';
                        mark.style.transition = 'background 1.5s ease';
                        range.surroundContents(mark);
                        setTimeout(() => (mark.style.background = 'transparent'), 1500);
                    }} catch {{ /* if DOM split not allowed, ignore */ }}
                }}

                function buildLooseRegex(text) {{
                    // escape regex specials, then allow small punctuation gaps between words
                    const words = text.split(' ').filter(Boolean).slice(0, 10).map(w =>
                        w.replace(/[.*+?^${{}}()|[\]\\]/g, '\\\\$&')
                    );
                    if (!words.length) return null;
                    const pattern = words.join('[^\w\n]{0,3}\s*');
                    return new RegExp(pattern, 'i');
                }}

                function findBestNode(regex) {{
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let best = null;
                    let bestDY = Infinity;
                    const targetY = fallbackY;

                    while (walker.nextNode()) {{
                        const node = walker.currentNode;
                        const t = node.textContent.replace(/\s+/g, ' ').trim();
                        if (!t) continue;
                        const m = regex ? t.match(regex) : null;
                        if (!m) continue;

                        // approximate absolute Y for proximity: use rect.top + scrollY
                        const range = document.createRange();
                        const startIndex = m.index;
                        range.setStart(node, Math.max(0, Math.min(startIndex, node.length)));
                        range.setEnd(node, Math.min(node.length, startIndex + (m[0]||'').length));
                        const rect = range.getBoundingClientRect();
                        const absY = rect.top + window.scrollY;
                        const dY = Math.abs(absY - targetY);

                        if (dY < bestDY) {{
                            best = {{ node, range, rect }};
                            bestDY = dY;
                            if (dY < 2) break; // perfect enough
                        }}
                    }}
                    return best;
                }}

                function snapElementToColumnLeft(rect) {{
                    const colW = getColumnWidth();
                    const absLeft = rect.left + window.scrollX;
                    if (colW <= 0) return {{ x: 0, col: 0 }};
                    const targetCol = Math.floor(absLeft / colW);
                    const targetX = Math.max(0, targetCol * colW);
                    return {{ x: targetX, col: targetCol }};
                }}

                // ---- restore flow ----
                const regex = buildLooseRegex(searchText);
                const found = regex ? findBestNode(regex) : null;

                if (found) {{
                    const snap = snapElementToColumnLeft(found.rect);
                    // y: align the found text near the top (with a small padding)
                    const targetY = Math.max(0, found.rect.top + window.scrollY - 8);

                    window.scrollTo(snap.x, targetY);

                    // highlight exactly what we matched
                    highlightRange(found.range);

                    // notify native side
                    setTimeout(() => {{
                        if (window.webkit?.messageHandlers?.viewportText) {{
                            window
