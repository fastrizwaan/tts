#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")  # Add WebKit import
from gi.repository import Gtk, Adw, Gio, WebKit

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
        
        # Add columns dropdown to header
        columns_combo = Gtk.ComboBoxText()
        for i in range(1, 11):
            columns_combo.append(str(i), f"{i} Columns")
        columns_combo.set_active(0)  # Default to 1 column
        columns_combo.connect("changed", self.on_columns_changed)
        header.pack_end(columns_combo)
        
        header.set_title_widget(Gtk.Label(label="Header"))
        toolbar.add_top_bar(header)
        
        # Create WebView for content area
        self.webview = WebKit.WebView()
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an HTML file to view.</p></body></html>")
        
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
        # Create file dialog to open HTML files
        dialog = Gtk.FileChooserNative.new(
            title="Open HTML File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel"
        )
        
        # Add HTML file filter
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML files")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        dialog.add_filter(html_filter)
        
        # Add all files filter
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)
        
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()
    
    def on_file_dialog_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                try:
                    # Load the HTML file content
                    content = file.load_bytes(None)[0].get_data().decode('utf-8')
                    self.webview.load_html(content)
                except Exception as e:
                    print(f"Error loading file: {e}")
                    # Show error message
                    error_dialog = Gtk.MessageDialog(
                        transient_for=self,
                        flags=0,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.CLOSE,
                        text=f"Error loading file: {e}"
                    )
                    error_dialog.run()
                    error_dialog.destroy()
        dialog.destroy()
    
    def on_columns_changed(self, combo):
        # Get selected number of columns
        active = combo.get_active()
        if active >= 0:
            num_columns = int(combo.get_active_id())
            # Update webview content to reflect column changes
            # This is a placeholder - in a real app you would modify CSS or HTML
            html_content = f"""
            <html>
                <head>
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
                        }}
                    </style>
                </head>
                <body>
                    <h1>Column View: {num_columns} Columns</h1>
                    <div class="container">
            """
            for i in range(num_columns):
                html_content += f'<div class="column"><h3>Column {i+1}</h3><p>This is column {i+1} content.</p></div>'
            html_content += """
                    </div>
                </body>
            </html>
            """
            self.webview.load_html(html_content)
    
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
