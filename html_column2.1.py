#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, WebKit

class HtmlViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Multi-Column HTML Viewer")
        self.set_default_size(900, 600)
        
        # Create the main toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)
        
        # Header bar
        header = Adw.HeaderBar(title_widget=Gtk.Label(label="Multi-Column Viewer"))
        toolbar_view.add_top_bar(header)
        
        # WebView with settings
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        
        # Enable WebKit Inspector and logging
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_enable_write_console_messages_to_stdout(True)
        
        # Connect to console message signal
        self.webview.connect("load-changed", self.on_load_changed)
        
        # Scrolled window
        scroller = Gtk.ScrolledWindow()
        scroller.hscrollbar_policy = Gtk.PolicyType.ALWAYS
        scroller.vscrollbar_policy = Gtk.PolicyType.NEVER
        scroller.set_child(self.webview)
        
        toolbar_view.set_content(scroller)
        
        # Load HTML
        self.load_html()
    
    def on_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            print("Page loaded successfully")
    
    def load_html(self):
        html = """
        <html>
        <head>
        <meta charset="utf-8">
        <style>
        html, body {
            margin: 0;
            padding: 0;
            height: 100%;
            overflow-x: auto;
            font-family: sans-serif;
            background: #f7f7f7;
        }
        
        /* Container wrapper to handle margins properly */
        .page-container {
            display: inline-block;
            padding: var(--top-margin, 40px) var(--right-margin, 20px) var(--bottom-margin, 40px) var(--left-margin, 60px);
        }
        
        .page {
            column-width: 320px;
            column-gap: 48px;
            width: max-content;
        }
        </style>
        <script>
        console.log("HTML loaded - check terminal for this message");
        window.addEventListener('DOMContentLoaded', function() {
            console.log("DOM loaded");
            const container = document.querySelector('.page-container');
            const computed = window.getComputedStyle(container);
            console.log("Padding right:", computed.paddingRight);
            console.log("Padding left:", computed.paddingLeft);
        });
        </script>
        </head>
        <body>
        <div class="page-container" style="
            --top-margin: 40px;
            --bottom-margin: 40px;
            --left-margin: 60px;
            --right-margin: 320px;">
            <div class="page">
                <h1>Horizontally Scrolling Multi-Column Layout</h1>
                <p>This is a Libadwaita-compliant GTK4 + WebKitGTK 6.0 app with proper logging enabled.</p>
                <p>The content flows into multiple columns and scrolls horizontally, like a magazine spread.</p>
                <p>Top, bottom, left, and right margins are now working via padding on a wrapper container!</p>
                <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Suspendisse eget viverra orci. Donec porta, sapien vel volutpat ornare, metus nunc tincidunt magna, vitae tempus metus neque ut libero.</p>
                <p>Phasellus consequat urna a diam ultricies gravida. Aliquam erat volutpat. Cras condimentum est eget nunc ultrices, non viverra felis porta.</p>
                <p>Integer vitae tortor vel augue consequat bibendum. Sed vehicula magna et risus tempus, nec hendrerit eros facilisis.</p>
            </div>
        </div>
        </body>
        </html>
        """
        self.webview.load_html(html, "file:///")

class HtmlViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.htmlviewer")
        self.connect("activate", self.on_activate)
    
    def on_activate(self, app):
        win = HtmlViewerWindow(app)
        win.present()

if __name__ == "__main__":
    app = HtmlViewerApp()
    app.run(None)
