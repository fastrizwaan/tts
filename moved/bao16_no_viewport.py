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
        
        self.columns_combo = Gtk.DropDown(model=string_list, selected=1)
        self.columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(self.columns_combo)
        
        header.set_title_widget(Gtk.Label(label="Header"))
        toolbar.add_top_bar(header)
        
        # Create WebView for content area
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
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
        

    def on_toggle_sidebar(self, button):
        # Toggle sidebar
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        # Wait for sidebar animation, then reapply layout
        GLib.timeout_add(350, lambda: self.apply_column_layout(self.current_columns - 1))
    
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
    
    def apply_column_layout(self, selected_column_index):
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

        function getViewportWidth() {{
            return window.innerWidth || document.documentElement.clientWidth;
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
                return;
            }}
            e.preventDefault();
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            const target = Math.round((window.scrollX + scrollDist) / colWidth) * colWidth;
            smoothScrollTo(target, window.scrollY);
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
                    case 'ArrowUp': e.preventDefault(); y = Math.max(0, y - viewportH * 0.8); break;
                    case 'ArrowDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH * 0.8); break;
                    case 'PageUp': e.preventDefault(); y = Math.max(0, y - viewportH); break;
                    case 'PageDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH); break;
                    case 'Home': e.preventDefault(); y = 0; break;
                    case 'End': e.preventDefault(); y = maxScrollY; break;
                }}
            }} else {{
                switch (e.key) {{
                    case 'ArrowLeft': e.preventDefault(); x = Math.max(0, x - colWidth); break;
                    case 'ArrowRight': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth); break;
                    case 'PageUp': e.preventDefault(); x = Math.max(0, x - colWidth * 2); break;
                    case 'PageDown': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth * 2); break;
                    case 'Home': e.preventDefault(); x = 0; break;
                    case 'End': e.preventDefault(); x = maxScrollX; break;
                }}
            }}

            if (type) {{
                smoothScrollTo(x, y);
            }}
        }});

        window.addEventListener('load', function() {{
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
        self.apply_column_layout(selected)
    
    def on_size_changed(self, *args):
        # Update column count after a delay
        GLib.timeout_add(100, lambda: self.apply_column_layout(self.current_columns - 1))


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
