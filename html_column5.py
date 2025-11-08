#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, WebKit

class HtmlViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(900, 700)
        self.set_title("HTML Horizontal Scrolling Viewer")
        
        # ---------------- ToolbarView + Header ----------------
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)
        header = Adw.HeaderBar(title_widget=Gtk.Label(label="HTML Horizontal Scrolling Viewer"))
        toolbar_view.add_top_bar(header)
        
        # ---------------- WebView in ScrolledWindow ----------------
        self.webview = WebKit.WebView()
        
        # Enable WebKit Inspector and logging
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_enable_write_console_messages_to_stdout(True)
        
        # Connect to load event
        self.webview.connect("load-changed", self.on_load_changed)
        
        self.load_html()
        
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.webview)
        # Allow horizontal scrolling only
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_kinetic_scrolling(True)
        scroller.set_overlay_scrolling(False)
        # Make the scroller expand to fill available space
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        
        # ---------------- Margin Controls ----------------
        control_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        control_box.set_margin_top(12)
        control_box.set_margin_bottom(12)
        control_box.set_margin_start(12)
        control_box.set_margin_end(12)
        # Add expand=False to prevent control_box from taking too much space
        control_box.set_hexpand(False)
        control_box.set_vexpand(False)
        
        # Column width control
        self.column_width_adj = Gtk.Adjustment(value=320, lower=50, upper=500, step_increment=10)
        self.column_width_spin = Adw.SpinRow(title="Column width (px)", adjustment=self.column_width_adj)
        self.column_width_spin.connect("notify::value", self.on_column_width_changed)
        control_box.append(self.column_width_spin)
        
        # Add a separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(6)
        separator.set_margin_bottom(6)
        control_box.append(separator)
        
        # Margin controls
        self.top_adj = Gtk.Adjustment(value=40, lower=0, upper=300, step_increment=5)
        self.bottom_adj = Gtk.Adjustment(value=40, lower=0, upper=300, step_increment=5)
        self.left_adj = Gtk.Adjustment(value=80, lower=0, upper=300, step_increment=5)
        self.right_adj = Gtk.Adjustment(value=150, lower=0, upper=300, step_increment=5)
        
        self.top_spin = Adw.SpinRow(title="Top margin", adjustment=self.top_adj)
        self.bottom_spin = Adw.SpinRow(title="Bottom margin", adjustment=self.bottom_adj)
        self.left_spin = Adw.SpinRow(title="Left margin", adjustment=self.left_adj)
        self.right_spin = Adw.SpinRow(title="Right margin", adjustment=self.right_adj)
        
        for spin in (self.top_spin, self.bottom_spin, self.left_spin, self.right_spin):
            spin.connect("notify::value", self.on_margin_changed)
            control_box.append(spin)
        
        # ---------------- Split Layout ----------------
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(control_box)
        paned.set_end_child(scroller)
        # Set initial position and resize/end expand properties
        paned.set_position(250)  # Set initial split position (pixels from left)
        # Ensure the end child (webview area) gets the expand behavior
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        
        toolbar_view.set_content(paned)
    
    def on_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            print("Page loaded successfully")
    
    def on_column_width_changed(self, spin_row, param):
        """Update column width when changed"""
        width = self.column_width_adj.get_value()
        
        js_code = f"""
        (function() {{
            var page = document.querySelector('.page');
            if (page) {{
                page.style.columnWidth = '{width}px';
                console.log('Column width updated to:', '{width}px');
            }}
        }})();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None)
    
    def on_margin_changed(self, spin_row, param):
        """Update CSS variables when margins change"""
        top = self.top_adj.get_value()
        bottom = self.bottom_adj.get_value()
        left = self.left_adj.get_value()
        right = self.right_adj.get_value()
        
        # Wrap in IIFE to avoid variable redeclaration errors
        js_code = f"""
        (function() {{
            var container = document.querySelector('.page-container');
            if (container) {{
                container.style.setProperty('--top-margin', '{top}px');
                container.style.setProperty('--bottom-margin', '{bottom}px');
                container.style.setProperty('--left-margin', '{left}px');
                container.style.setProperty('--right-margin', '{right}px');
                console.log('Margins updated:', {{top: {top}, bottom: {bottom}, left: {left}, right: {right}}});
            }}
        }})();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None)
    
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
            overflow: hidden;
            font-family: sans-serif;
            background: #f7f7f7;
        }
        
        /* Container wrapper to handle margins properly */
        .page-container {
            display: inline-block;
            height: 100vh;
            box-sizing: border-box;
            padding: var(--top-margin, 40px) var(--right-margin, 150px) var(--bottom-margin, 40px) var(--left-margin, 80px);
        }
        
        .page {
            column-width: 320px;
            column-gap: 48px;
            width: max-content;
            height: 100%;
            column-fill: auto;
            overflow-x: visible;
        }
        
        .content-block {
            break-inside: auto;
            page-break-inside: auto;
            margin-bottom: 1.5em;
        }
        
        h1 {
            margin-top: 0;
            margin-bottom: 0.5em;
            color: #1a1a1a;
        }
        
        h2 {
            margin-top: 0;
            margin-bottom: 0.5em;
            color: #2a2a2a;
        }
        
        p {
            text-align: justify;
            line-height: 1.6;
            margin-top: 0;
            margin-bottom: 0.8em;
        }
        
        p:last-child {
            margin-bottom: 0;
        }
        </style>
        <script>
        console.log("HTML loaded - check terminal for this message");
        window.addEventListener('DOMContentLoaded', function() {
            console.log("DOM loaded");
            var container = document.querySelector('.page-container');
            var page = document.querySelector('.page');
            var computed = window.getComputedStyle(container);
            var pageComputed = window.getComputedStyle(page);
            console.log("Container height:", container.offsetHeight + "px");
            console.log("Container padding right:", computed.paddingRight);
            console.log("Container padding left:", computed.paddingLeft);
            console.log("Container padding top:", computed.paddingTop);
            console.log("Container padding bottom:", computed.paddingBottom);
            console.log("Column width:", pageComputed.columnWidth);
            console.log("Column gap:", pageComputed.columnGap);
            console.log("Column fill:", pageComputed.columnFill);
            console.log("Page width:", page.scrollWidth + "px");
        });
        </script>
        </head>
        <body>
        <div class="page-container" style="
            --top-margin: 40px;
            --bottom-margin: 40px;
            --left-margin: 80px;
            --right-margin: 150px;">
            <div class="page">
                <div class="content-block">
                    <h1>Horizontally Scrolling Multi-Column Layout</h1>
                    <p>This is a Libadwaita-compliant GTK4 + WebKitGTK 6.0 application demonstrating a multi-column layout with horizontal scrolling. The content flows seamlessly across multiple columns, creating a magazine-style reading experience.</p>
                </div>
                
                <div class="content-block">
                    <h2>Introduction to Multi-Column Layouts</h2>
                    <p>Multi-column layouts have been a staple of print design for centuries, offering readers an efficient and aesthetically pleasing way to consume text content. With CSS multi-column properties, we can now bring this elegant layout paradigm to digital interfaces.</p>
                    <p>The CSS columns specification allows content to flow naturally from one column to the next, automatically balancing the text across available space. This creates a more compact presentation that can reduce scrolling and improve readability for certain types of content.</p>
                </div>
                
                <div class="content-block">
                    <h2>Benefits of Horizontal Scrolling</h2>
                    <p>While vertical scrolling is the dominant paradigm on the web, horizontal scrolling offers unique advantages for specific use cases. Magazine-style layouts, image galleries, and timeline presentations can benefit greatly from horizontal navigation.</p>
                    <p>By combining multi-column CSS with horizontal scrolling, we create an interface that mimics the experience of reading a physical newspaper or magazine, where content spans multiple columns across a wide surface.</p>
                </div>
                
                <div class="content-block">
                    <h2>Implementation Details</h2>
                    <p>This implementation uses modern CSS custom properties (CSS variables) to make margins easily configurable. The column-width property is adjustable from 50 to 500 pixels, with a 48-pixel gap between columns, creating a balanced and readable layout.</p>
                    <p>The wrapper container approach solves a common problem with inline-block elements and margins. By using padding on the container instead of margins on the columned element itself, we achieve predictable spacing that works reliably across different browsers and rendering engines.</p>
                </div>
                
                <div class="content-block">
                    <h2>Viewport Height Constraint</h2>
                    <p>The layout now uses 100% of the viewport height (100vh) without requiring vertical scrolling. Content flows horizontally across columns, with the column-fill property set to auto, ensuring content fills each column from top to bottom before moving to the next column.</p>
                    <p>This approach eliminates the need for vertical scrolling entirely, keeping the reading experience focused and preventing the confusion that can arise from two-dimensional scrolling patterns.</p>
                </div>
                
                <div class="content-block">
                    <h2>WebKit Integration</h2>
                    <p>WebKitGTK provides a powerful rendering engine that brings modern web standards to GTK applications. Version 6.0 brings improved performance, better standards compliance, and enhanced developer tools for debugging and optimization.</p>
                    <p>The developer extras enabled in this application allow you to inspect elements, view computed styles, and debug JavaScript directly within the GTK application. Console messages are redirected to stdout, making it easy to monitor the application's behavior during development.</p>
                </div>
                
                <div class="content-block">
                    <h2>Libadwaita Design Patterns</h2>
                    <p>This application follows Libadwaita design guidelines, using the ToolbarView pattern for modern GNOME applications. The AdwHeaderBar provides a clean, platform-integrated title bar that respects system themes and user preferences.</p>
                    <p>Libadwaita promotes consistency across GNOME applications, ensuring that users enjoy a cohesive experience regardless of which applications they use. By adhering to these patterns, developers contribute to the overall quality and polish of the GNOME ecosystem.</p>
                </div>
                
                <div class="content-block">
                    <h2>Interactive Controls</h2>
                    <p>The left sidebar provides live adjustment controls using Adwaita SpinRow widgets. You can adjust column width from 50 to 500 pixels and control all four margins independently. As you adjust the values, JavaScript dynamically updates the layout in real-time.</p>
                    <p>This demonstrates the powerful interoperability between GTK widgets and web content through WebKitGTK's JavaScript bridge. The evaluate_javascript method allows seamless communication from the GTK side to manipulate the web content programmatically.</p>
                </div>
                
                <div class="content-block">
                    <h2>Content Block Structure</h2>
                    <p>Each section is wrapped in a content-block div with break-inside: avoid, ensuring that headings and their related paragraphs stay together within the same column. This prevents awkward breaks and maintains the logical structure of the content.</p>
                    <p>This approach creates a smooth, magazine-like reading experience where each topic flows naturally from one column to the next without splitting related content across column boundaries.</p>
                </div>
                
                <div class="content-block">
                    <h2>Scrolling Behavior</h2>
                    <p>The horizontal scrollbar appears automatically when content exceeds the viewport width. Kinetic scrolling is enabled for smooth, momentum-based scrolling that feels natural on touchpads and touchscreens.</p>
                    <p>Users can scroll horizontally using the scrollbar, trackpad gestures, or mouse wheel (when available). This provides multiple interaction methods to accommodate different user preferences and hardware configurations.</p>
                </div>
                
                <div class="content-block">
                    <h2>Typography and Readability</h2>
                    <p>Proper typography is essential for multi-column layouts. The line-height is set to 1.6 to provide comfortable spacing between lines, reducing eye strain during extended reading sessions.</p>
                    <p>Text is justified (text-align: justify) to create clean, aligned edges on both sides of each column, mimicking traditional print layouts. Headers use a darker color for emphasis while maintaining readability.</p>
                </div>
                
                <div class="content-block">
                    <h2>Performance Considerations</h2>
                    <p>By constraining the layout to viewport height and using CSS columns with column-fill: auto, the browser can efficiently calculate and render the layout. This approach performs well even with large amounts of content.</p>
                    <p>The inline-block display on the container ensures that the total width is calculated based on the actual column layout, enabling proper horizontal scrolling without unnecessary whitespace.</p>
                </div>
                
                <div class="content-block">
                    <h2>Future Enhancements</h2>
                    <p>This foundation could be extended with features like dynamic content loading, adjustable column gaps, theme switching between light and dark modes, and integration with document formats like Markdown or reStructuredText.</p>
                    <p>Additional enhancements might include keyboard shortcuts for navigation (arrow keys to jump between columns), bookmarking capabilities, search functionality with column-aware highlighting, and export options to various formats.</p>
                </div>
                
                <div class="content-block">
                    <h2>Conclusion</h2>
                    <p>Multi-column layouts with horizontal scrolling and viewport height constraints offer a unique and engaging way to present content in desktop applications. By leveraging modern web standards through WebKitGTK and following Libadwaita design patterns, we create applications that are both functional and beautiful.</p>
                    <p>This example demonstrates that powerful, magazine-style layouts are well within reach for GTK developers, opening new possibilities for content-rich applications in the GNOME ecosystem.</p>
                </div>
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
