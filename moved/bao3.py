#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")  # Add WebKit import
from gi.repository import Gtk, Adw, Gio, WebKit, GLib

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
        
        columns_combo = Gtk.DropDown(model=string_list, selected=0)
        columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(columns_combo)
        
        header.set_title_widget(Gtk.Label(label="Header"))
        toolbar.add_top_bar(header)
        
        # Create WebView for content area
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)  # Expand vertically
        self.webview.set_hexpand(True)  # Expand horizontally
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an HTML file to view.</p></body></html>")
        
        # Store the original content to be able to reformat it
        self.original_html_content = "<h1>Welcome</h1><p>Select an HTML file to view.</p>"
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content_box.append(self.webview)
        toolbar.set_content(content_box)
        
        self.split.set_content(toolbar)
        
        # Connect to size allocation to handle responsive behavior
        self.connect("notify::default-width", self.on_size_changed)
        
        # Breakpoint for responsive sidebar (768px is a common mobile/desktop breakpoint)
        self.breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 768px")
        )
        self.breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.breakpoint)
    
    def on_toggle_sidebar(self, button):
        # Toggle sidebar visibility
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
    
    def on_open_file(self, button):
        # Create file dialog to open HTML files using the new API
        dialog = Gtk.FileDialog()
        dialog.set_title("Open HTML File")
        
        # Create filters for the dialog
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
                # Load the HTML file content in a background thread to avoid freezing the UI
                def load_file_in_thread():
                    try:
                        # Read file content in a background thread
                        content_bytes = file.load_bytes(None)[0]
                        content = content_bytes.get_data().decode('utf-8')
                        
                        # Store the original content
                        self.original_html_content = content
                        
                        # Apply current column layout to the content
                        selected = getattr(self, 'current_columns', 1) - 1  # Default to 0 (1 column)
                        GLib.idle_add(lambda: self.apply_column_layout(selected))
                    except Exception as e:
                        print(f"Error reading file: {e}")
                        # Show error message in main thread
                        GLib.idle_add(lambda: self.show_error_dialog(f"Error loading file: {e}"))
                
                # Run file loading in a background thread
                GLib.Thread.new(None, load_file_in_thread)
        except GLib.Error:
            # User cancelled the dialog - this is expected behavior, so we just return
            pass
    
    def show_error_dialog(self, message):
        # Helper method to show error dialog in main thread
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
        num_columns = selected_column_index + 1  # Convert to 1-based
        self.current_columns = num_columns
        
        # Create CSS for column layout
        css = f"""
        <style>
            body {{
                font-family: sans-serif;
                margin: 20px;
            }}
            .container {{
                display: grid;
                grid-template-columns: repeat({num_columns}, 1fr);
                gap: 20px;
            }}
            .column {{
                border: 1px solid #ccc;
                padding: 10px;
                background-color: #f9f9f9;
                min-height: 200px;
            }}
        </style>
        """
        
        # Wrap the original content in the column layout
        original_html = self.original_html_content
        
        # Simple approach: if it's a full HTML document, extract the body content
        if '<body>' in original_html and '</body>' in original_html:
            start = original_html.find('<body>') + 6
            end = original_html.find('</body>')
            body_content = original_html[start:end]
        else:
            # If it's just body content
            body_content = original_html
        
        # For flow from left to right in the viewport, we'll split the content into multiple columns
        # This requires more sophisticated content splitting for proper text flow
        # For now, we'll put the same content in each column, but with CSS grid layout
        # In a real implementation, you'd want to properly split content
        
        html_content = f"""
        <html>
            <head>
                {css}
            </head>
            <body>
                <h1>Content in {num_columns} Columns</h1>
                <div class="container">
        """
        
        # Create the same content in each column for now
        for i in range(num_columns):
            html_content += f'<div class="column">{body_content}</div>'
        
        html_content += """
                </div>
            </body>
        </html>
        """
        
        self.webview.load_html(html_content)
    
    def on_columns_changed(self, combo, pspec):
        # Get selected number of columns from the dropdown
        selected = combo.get_selected()
        self.apply_column_layout(selected)
    
    def on_size_changed(self, *args):
        # This is called when window is resized
        # The breakpoint handles the actual sidebar visibility
        pass

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
