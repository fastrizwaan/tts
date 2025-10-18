#!/usr/bin/env python3
import os
import tempfile
import shutil
import urllib.parse
from typing import Optional, List, Tuple
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, WebKit, GLib, Gdk
from ebooklib import epub
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Demo")
        self.set_default_size(800, 600)
        
        # EPUB-related attributes
        self.book: Optional[epub.EpubBook] = None
        self.toc: List[Tuple[str, str]] = []
        self.temp_dir: Optional[str] = None
        self.current_href: Optional[str] = None
        self.current_spine_index: int = -1
        
        # Split view
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.set_content(self.split)
        
        # Sidebar with TOC
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        # TOC Header
        toc_header = Adw.HeaderBar()
        toc_label = Gtk.Label(label="Table of Contents")
        toc_label.add_css_class("title")
        toc_header.set_title_widget(toc_label)
        sidebar.append(toc_header)
        
        # TOC List
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect("row-activated", self.on_toc_row_activated)
        toc_scrolled = Gtk.ScrolledWindow()
        toc_scrolled.set_child(self.toc_list)
        toc_scrolled.set_vexpand(True)
        sidebar.append(toc_scrolled)
        
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
        
        # Add previous/next chapter buttons
        self.prev_chapter_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_chapter_btn.connect("clicked", lambda *_: self.load_prev_spine_item())
        self.prev_chapter_btn.set_sensitive(False)
        header.pack_start(self.prev_chapter_btn)
        
        self.next_chapter_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_chapter_btn.connect("clicked", lambda *_: self.load_next_spine_item())
        self.next_chapter_btn.set_sensitive(False)
        header.pack_start(self.next_chapter_btn)
        
        # Add columns dropdown to header using StringList model
        string_list = Gtk.StringList()
        for i in range(1, 11):
            string_list.append(f"{i} Columns")
        
        self.columns_combo = Gtk.DropDown(model=string_list, selected=1)
        self.columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(self.columns_combo)
        
        header.set_title_widget(Gtk.Label(label="Header"))
        toolbar.add_top_bar(header)
        
        # Create WebView for content area
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        # Set up navigation policy to handle internal links
        self.webview.connect("decide-policy", self.on_decide_policy)
        self.webview.connect("load-changed", self.on_webview_load_changed)
        
        # Set up message handler to receive scrollevent from JavaScript
        content_manager = self.webview.get_user_content_manager()
        content_manager.connect("script-message-received::scrollEvent", self.on_scroll_event_received)
        content_manager.register_script_message_handler("scrollEvent")
        
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an HTML or EPUB file to view.</p></body></html>")
        
        # Store the original content to be able to reformat it
        self.original_html_content = "<h1>Welcome</h1><p>Select an HTML or EPUB file to view.</p>"
        self.is_epub_mode = False
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.webview.set_margin_top(10)
        self.webview.set_margin_bottom(10)
        self.webview.set_margin_start(10)
        self.webview.set_margin_end(10)
        content_box.append(self.webview)
        
        toolbar.set_content(content_box)
        
        self.split.set_content(toolbar)
        
        # Store current columns and connect to size allocation
        self.current_columns = 2
        self.columns_combo.set_selected(self.current_columns - 1)
        # Apply default column layout after window initializes
        GLib.idle_add(lambda: self.apply_column_layout(self.current_columns - 1))

        self.pending_column_change = None
        self.connect("notify::default-width", self.on_size_changed)
        self.connect("notify::default-height", self.on_size_changed)
        
        # Breakpoint for responsive sidebar
        self.breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 768px")
        )
        self.breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.breakpoint)
    
    # ==================== EPUB METHODS ====================
    
    def load_epub(self, path: str):
        """Main entry point - reads EPUB file, extracts TOC, loads first content"""
        try:
            self.book = epub.read_epub(path)
            self.toc = self.extract_toc(self.book.toc)
            self.populate_toc()
            self.is_epub_mode = True
            
            first_href = None
            if self.book.spine:
                first_item_id = self.book.spine[0][0]
                first_item = self.book.get_item_with_id(first_item_id)
                if first_item:
                    first_href = first_item.get_name()
                    
            if first_href:
                self.load_href(first_href)
            elif self.toc:
                self.load_href(self.toc[0][1])
            else:
                print("No content found in spine or TOC.")
        except Exception as e:
            print(f"EPUB load error: {e}")
    
    def extract_toc(self, toc_items, base="") -> List[Tuple[str, str]]:
        """Recursively parses nested TOC structure into flat list of (title, href) tuples"""
        result = []
        for item in toc_items:
            if isinstance(item, epub.Link):
                href = urllib.parse.urljoin(base, item.href)
                result.append((item.title, href))
            elif isinstance(item, tuple) and len(item) >= 2:
                if isinstance(item[0], epub.Link):
                    href = urllib.parse.urljoin(base, item[0].href)
                    result.append((item[0].title, href))
                result.extend(self.extract_toc(item[1], base))
            elif isinstance(item, list):
                result.extend(self.extract_toc(item, base))
        return result
    
    def populate_toc(self):
        """Populates the sidebar ListBox widget with TOC entries"""
        while True:
            row = self.toc_list.get_row_at_index(0)
            if row:
                self.toc_list.remove(row)
            else:
                break
                
        for title, href in self.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(
                label=title,
                xalign=0,
                margin_start=10,
                margin_top=5,
                margin_bottom=5,
                ellipsize=2,
                wrap=True
            )
            row.set_child(label)
            row.href = href
            self.toc_list.append(row)
    
    def get_spine_index(self, href: str) -> int:
        """Finds the position of an href in the EPUB spine (reading order)"""
        if not self.book:
            return -1
        clean_href = href.split('#')[0].lstrip('./')

        for i, (item_id, _) in enumerate(self.book.spine):
            item = self.book.get_item_with_id(item_id)
            if item and item.get_name() == clean_href:
                return i
        return -1
    
    def load_href(self, href: str):
        """Loads specific content by href - extracts all EPUB resources to temp directory"""
        if not self.book:
            return

        clean_href = href.split('#')[0]
        self.current_spine_index = self.get_spine_index(clean_href)

        item = self.book.get_item_with_href(clean_href)
        if not item:
            for it in self.book.get_items():
                if it.get_name() == clean_href:
                    item = it
                    break
        if not item:
            print(f"Content item not found for href: {clean_href}")
            return

        self.current_href = clean_href
        
        # Clean up old temp directory
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = tempfile.mkdtemp()

        # Extract all resources to temp directory
        for it in self.book.get_items():
            try:
                dest = os.path.join(self.temp_dir, it.get_name())
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    f.write(it.content)
            except Exception as e:
                print(f"Error saving file {it.get_name()}: {e}")

        # Read and modify the HTML content to inject our column layout
        full_path = os.path.join(self.temp_dir, item.get_name())
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract body content if present
            if '<body>' in content.lower() and '</body>' in content.lower():
                start = content.lower().find('<body>') + 6
                end = content.lower().find('</body>', start)
                if end != -1:
                    body_content = content[start:end]
                else:
                    body_content = content
            else:
                body_content = content
            
            # Inject our column layout into the HTML
            modified_html = self.create_column_html(body_content)
            
            # Save modified HTML to temp file
            modified_path = os.path.join(self.temp_dir, "_modified_" + os.path.basename(item.get_name()))
            with open(modified_path, 'w', encoding='utf-8') as f:
                f.write(modified_html)
            
            # Load via file:// URI to preserve relative links
            uri = f"file://{modified_path}"
            fragment = href.split('#')[1] if '#' in href else ''
            if fragment:
                uri += f"#{fragment}"
            
            self.webview.load_uri(uri)
            
        except Exception as e:
            print(f"Error reading content: {e}")
    
    def on_toc_row_activated(self, listbox, row):
        """Callback when user clicks a TOC entry"""
        if hasattr(row, 'href'):
            self.load_href(row.href)
    
    def load_next_spine_item(self):
        """Loads next chapter in reading order"""
        if not self.book or self.current_spine_index < 0:
            return

        spine_length = len(self.book.spine)
        next_index = self.current_spine_index + 1
        
        if next_index < spine_length:
            item_id = self.book.spine[next_index][0]
            next_item = self.book.get_item_with_id(item_id)
            if next_item:
                self.load_href(next_item.get_name())
        return False
    
    def load_prev_spine_item(self):
        """Loads previous chapter"""
        if not self.book or self.current_spine_index < 0:
            return

        prev_index = self.current_spine_index - 1
        
        if prev_index >= 0:
            item_id = self.book.spine[prev_index][0]
            prev_item = self.book.get_item_with_id(item_id)
            if prev_item:
                self.load_href(prev_item.get_name())
                # Scroll to end after loading previous chapter
                GLib.timeout_add(200, self.scroll_to_end_of_page)
        return False
    
    def scroll_to_end_of_page(self):
        """Scrolls to end when moving to previous chapter"""
        if not self.webview:
            return False
        
        if self.current_columns > 1:
            # Multi-column: scroll to end horizontally
            js_code = """
            (function() {
                const maxScrollX = document.body.scrollWidth - window.innerWidth;
                window.scrollTo({
                    left: maxScrollX,
                    behavior: 'auto'
                });
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None)
        else:
            # Single column: scroll to end vertically
            js_code = """
            (function() {
                const maxScrollY = document.body.scrollHeight - window.innerHeight;
                window.scrollTo({
                    top: maxScrollY,
                    behavior: 'auto'
                });
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None)
        
        return False
    
    def on_webview_load_changed(self, webview, load_event):
        """Applies layout after content loads"""
        if load_event == WebKit.LoadEvent.FINISHED:
            self.update_chapter_nav_buttons()
    
    def update_chapter_nav_buttons(self):
        """Update prev/next chapter button states"""
        if not self.book or self.current_spine_index < 0:
            self.prev_chapter_btn.set_sensitive(False)
            self.next_chapter_btn.set_sensitive(False)
            return
        
        spine_length = len(self.book.spine)
        
        self.prev_chapter_btn.set_sensitive(self.current_spine_index > 0)
        self.next_chapter_btn.set_sensitive(self.current_spine_index < spine_length - 1)
    
    def on_decide_policy(self, webview, decision, decision_type):
        """Handle navigation requests - intercept internal EPUB links"""
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            request = nav_action.get_request()
            uri = request.get_uri()
            
            # Only handle file:// URIs when in EPUB mode
            if self.is_epub_mode and uri.startswith("file://"):
                # Extract the path from the URI
                path = uri.replace("file://", "")
                
                # Check if it's in our temp directory
                if self.temp_dir and path.startswith(self.temp_dir):
                    # Get the relative path within the EPUB
                    rel_path = path.replace(self.temp_dir + "/", "")
                    
                    # Remove our _modified_ prefix if present
                    if rel_path.startswith("_modified_"):
                        rel_path = rel_path[10:]
                    
                    # Extract fragment if present
                    fragment = ""
                    if "#" in uri:
                        fragment = "#" + uri.split("#")[1]
                        rel_path = rel_path.split("#")[0]
                    
                    # If this is a different file, load it through our system
                    if rel_path != ("_modified_" + self.current_href) and rel_path != self.current_href:
                        self.load_href(rel_path + fragment)
                        decision.ignore()
                        return True
            
        return False
    
    # ==================== END EPUB METHODS ====================
    
    def on_toggle_sidebar(self, button):
        # Toggle sidebar
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        # Wait for sidebar animation, then reapply layout with position restore
        if self.is_epub_mode:
            GLib.timeout_add(350, lambda: self.reapply_epub_layout())
        else:
            GLib.timeout_add(350, lambda: self.apply_column_layout(self.current_columns - 1, restore_position=True))
    
    def reapply_epub_layout(self):
        """Reapply layout for current EPUB page"""
        if self.current_href:
            self.load_href(self.current_href)
        return False
    
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
    
    def on_open_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open File")
        
        # HTML filter
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML files")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        
        # EPUB filter
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        
        # All files filter
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        filters.append(html_filter)
        filters.append(all_filter)
        dialog.set_filters(filters)
        
        dialog.open(self, None, self.on_file_dialog_response)
    
    def on_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                
                # Check if it's an EPUB file
                if path.lower().endswith('.epub'):
                    self.load_epub(path)
                else:
                    # Load as HTML
                    self.is_epub_mode = False
                    self.prev_chapter_btn.set_sensitive(False)
                    self.next_chapter_btn.set_sensitive(False)
                    
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
    
    def create_column_html(self, body_content):
        """Create HTML with column layout for EPUB content"""
        num_columns = self.current_columns
        
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
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) snapScroll();
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
                }}, 450);
            }}
        }});
        """
        
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
        
        return html_content
    
    def apply_column_layout(self, selected_column_index, restore_position=False):
        num_columns = selected_column_index + 1
        self.current_columns = num_columns
        
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
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) snapScroll();
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
                }}, 450);
            }}
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
   
    def on_columns_changed(self, combo, pspec):
        selected = combo.get_selected()
        self.current_columns = selected + 1
        # Reapply layout based on mode
        if self.is_epub_mode:
            GLib.timeout_add(50, lambda: self.reapply_epub_layout())
        else:
            GLib.timeout_add(50, lambda: self.apply_column_layout(selected, restore_position=True))
    
    def on_size_changed(self, *args):
        if self.is_epub_mode:
            GLib.timeout_add(100, lambda: self.reapply_epub_layout())
        else:
            GLib.timeout_add(100, lambda: self.apply_column_layout(self.current_columns - 1, restore_position=True))
    
    def do_close_request(self):
        """Clean up temp directory on close"""
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        return False


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
