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

        # Create the main toolbar view (replaces set_titlebar())
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar (goes inside toolbar_view)
        header = Adw.HeaderBar(title_widget=Gtk.Label(label="Multi-Column Viewer"))
        toolbar_view.add_top_bar(header)

        # WebView
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)

        # Scrolled window for horizontal scrolling
        scroller = Gtk.ScrolledWindow()
        scroller.hscrollbar_policy = Gtk.PolicyType.ALWAYS
        scroller.vscrollbar_policy = Gtk.PolicyType.NEVER
        scroller.set_child(self.webview)

        # Add scroller to toolbar_view content
        toolbar_view.set_content(scroller)

        # Load HTML
        self.load_html()

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
        .page {
            column-width: 320px;
            column-gap: 48px;
            padding-top: var(--top-margin, 40px);
            padding-bottom: var(--bottom-margin, 40px);
            padding-left: var(--left-margin, 60px);
            width: max-content;
            display: inline-block;
        }
        .page::after {
            content: "";
            display: inline-block;
            width: var(--right-margin, 120px);
        }
        </style>
        </head>
        <body>
        <div class="page" style="
            --top-margin:40px;
            --bottom-margin:40px;
            --left-margin:60px;
            margin-right:120px;">
            <h1>Horizontally Scrolling Multi-Column Layout</h1>
            <p>This is a Libadwaita-compliant GTK4 + WebKitGTK 6.0 app with a proper ToolbarView structure.</p>
            <p>The content flows into multiple columns and scrolls horizontally, like a magazine spread.</p>
            <p>Top, bottom, left, and right margins are CSS-driven. The right margin appears *after* the last column.</p>
            <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Suspendisse eget viverra orci. Donec porta, sapien vel volutpat ornare, metus nunc tincidunt magna, vitae tempus metus neque ut libero.</p>
            <p>Phasellus consequat urna a diam ultricies gravida. Aliquam erat volutpat. Cras condimentum est eget nunc ultrices, non viverra felis porta.</p>
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

