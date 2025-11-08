#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
import os
from gi.repository import Gtk, Adw, WebKit, GLib, GObject
import sys
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
class ColumnLayoutWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.set_default_size(1200, 800)
        self.set_title("Multi-Column Layout Viewer")
        
        # Create toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)
        
        # Header bar
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        
        # Create split view for sidebar
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_show_sidebar(True)
        self.split_view.set_sidebar_width_fraction(0.25)
        toolbar_view.set_content(self.split_view)
        
        # Create sidebar with controls
        sidebar = self.create_sidebar()
        self.split_view.set_sidebar(sidebar)
        
        # Create WebView
        self.webview = WebKit.WebView()
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_javascript_can_access_clipboard(True)
        
        # Load the HTML content
        self.load_html_content()
        
        # Add webview to scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.webview)
        self.split_view.set_content(scrolled)
        
        # Toggle button for sidebar
        toggle_button = Gtk.ToggleButton()
        toggle_button.set_icon_name("sidebar-show-symbolic")
        toggle_button.bind_property(
            "active", 
            self.split_view, 
            "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        )
        header.pack_start(toggle_button)
    
    def create_sidebar(self):
        """Create the sidebar with margin controls"""
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Create preferences group
        group = Adw.PreferencesGroup()
        group.set_title("Layout Controls")
        group.set_description("Adjust margins and column width")
        group.set_vexpand(True)
        # Top Margin
        self.top_margin_row = Adw.SpinRow()
        self.top_margin_row.set_title("Top Margin")
        self.top_margin_row.set_subtitle("Pixels")
        adjustment = Gtk.Adjustment(value=40, lower=0, upper=200, step_increment=5, page_increment=10)
        self.top_margin_row.set_adjustment(adjustment)
        self.top_margin_row.set_digits(0)
        self.top_margin_row.connect("changed", self.on_margin_changed, "top")
        group.add(self.top_margin_row)
        
        # Bottom Margin
        self.bottom_margin_row = Adw.SpinRow()
        self.bottom_margin_row.set_title("Bottom Margin")
        self.bottom_margin_row.set_subtitle("Pixels")
        adjustment = Gtk.Adjustment(value=40, lower=0, upper=200, step_increment=5, page_increment=10)
        self.bottom_margin_row.set_adjustment(adjustment)
        self.bottom_margin_row.set_digits(0)
        self.bottom_margin_row.connect("changed", self.on_margin_changed, "bottom")
        group.add(self.bottom_margin_row)
        
        # Left Margin
        self.left_margin_row = Adw.SpinRow()
        self.left_margin_row.set_title("Left Margin")
        self.left_margin_row.set_subtitle("Pixels")
        adjustment = Gtk.Adjustment(value=80, lower=0, upper=300, step_increment=5, page_increment=10)
        self.left_margin_row.set_adjustment(adjustment)
        self.left_margin_row.set_digits(0)
        self.left_margin_row.connect("changed", self.on_margin_changed, "left")
        group.add(self.left_margin_row)
        
        # Right Margin
        self.right_margin_row = Adw.SpinRow()
        self.right_margin_row.set_title("Right Margin")
        self.right_margin_row.set_subtitle("Pixels")
        adjustment = Gtk.Adjustment(value=250, lower=0, upper=500, step_increment=5, page_increment=10)
        self.right_margin_row.set_adjustment(adjustment)
        self.right_margin_row.set_digits(0)
        self.right_margin_row.connect("changed", self.on_margin_changed, "right")
        group.add(self.right_margin_row)
        
        # Column Width
        self.column_width_row = Adw.SpinRow()
        self.column_width_row.set_title("Column Width")
        self.column_width_row.set_subtitle("Pixels")
        adjustment = Gtk.Adjustment(value=320, lower=150, upper=600, step_increment=10, page_increment=20)
        self.column_width_row.set_adjustment(adjustment)
        self.column_width_row.set_digits(0)
        self.column_width_row.connect("changed", self.on_column_width_changed)
        group.add(self.column_width_row)
        
        # Add group to clamp
        clamp = Adw.Clamp()
        clamp.set_maximum_size(400)
        clamp.set_child(group)
        
        # Add to scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(clamp)
        
        sidebar_box.append(scrolled)
        
        
        return sidebar_box
    
    def on_margin_changed(self, spin_row, margin_type):
        """Update CSS custom property when margin changes"""
        value = int(spin_row.get_value())
        js_code = f"""
            var wrapper = document.querySelector('.content-wrapper');
            if (wrapper) {{
                wrapper.style.setProperty('--{margin_type}-margin', '{value}px');
                console.log('Updated {margin_type} margin to {value}px');
            }}
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None)
    
    def on_column_width_changed(self, spin_row):
        """Update column width when spinner changes"""
        value = int(spin_row.get_value())
        js_code = f"""
            var page = document.querySelector('.page');
            if (page) {{
                page.style.columnWidth = '{value}px';
                console.log('Updated column width to {value}px');
            }}
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None)
    
    def load_html_content(self):
        """Load the HTML content into the WebView"""
        html_content = """<!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <style>
        /* --- PSEUDO-ELEMENT APPROACH --- */
        html, body {
            margin: 0;
            padding: 0;
            font-family: sans-serif;
            background: #f7f7f7;
            height: 100%;
            overflow: hidden;
        }

        /* Scrollable container */
        .scroll-container {
            height: 100%;
            width: 100%;
            overflow-x: auto;
            overflow-y: hidden;
            box-sizing: border-box;
        }
        
        /* Wrapper */
        .content-wrapper {
            height: 100%;
            display: flex;
            align-items: flex-start;
        }
        
        /* The columned element */
        .page {
            column-width: 320px;
            column-gap: 48px;
            
            /* Left and vertical margins */
            margin-top: var(--top-margin, 40px);
            margin-bottom: var(--bottom-margin, 40px);
            margin-left: var(--left-margin, 80px);
            
            /* Calculate height accounting for top/bottom margins */
            height: calc(100% - var(--top-margin, 40px) - var(--bottom-margin, 40px));
            
            box-sizing: border-box;
        }
        
        /* Create physical space for right margin using ::after */
        .page::after {
            content: '';
            display: block;
            /* Creates extra space for right margin */
            width: calc(100% + var(--right-margin));
            height: 1px;
            /* Make it part of the column flow */
            break-inside: avoid;
        }
        
        h1 {
            margin-top: 0;
            color: #1a1a1a;
        }
        
        h2 {
            margin-top: 1.5em;
            color: #2a2a2a;
        }
        
        p {
            text-align: justify;
            line-height: 1.6;
        }
        /* --- END CSS --- */
        </style>
        <script>
        console.log("HTML loaded - check terminal for this message");
        window.addEventListener('DOMContentLoaded', function() {
            console.log("DOM loaded");
            var scrollContainer = document.querySelector('.scroll-container');
            var wrapper = document.querySelector('.content-wrapper');
            var page = document.querySelector('.page');
            
            var pageComputed = window.getComputedStyle(page);
            var afterComputed = window.getComputedStyle(page, '::after');
            
            console.log("Page margin-top:", pageComputed.marginTop);
            console.log("Page margin-bottom:", pageComputed.marginBottom);
            console.log("Page margin-left:", pageComputed.marginLeft);
            console.log("::after width:", afterComputed.width);
            console.log("Column width:", pageComputed.columnWidth);
            console.log("Page offset width:", page.offsetWidth + "px");
            console.log("Page offset height:", page.offsetHeight + "px");
            console.log("Wrapper scroll width:", wrapper.scrollWidth + "px");
            console.log("Scroll container scroll width:", scrollContainer.scrollWidth + "px");
        });
        </script>
        </head>
        <body>
        <div class="scroll-container">
            <div class="content-wrapper" style="
                --top-margin: 40px;
                --bottom-margin: 40px;
                --left-margin: 80px;
                --right-margin: 250px;">
                <div class="page">
                    <h1>Horizontally Scrolling Multi-Column Layout</h1>
                    
                    <p>This is a Libadwaita-compliant GTK4 + WebKitGTK 6.0 application demonstrating a multi-column layout with horizontal scrolling. The content flows seamlessly across multiple columns, creating a magazine-style reading experience.</p>
                    
                    <h2>Introduction to Multi-Column Layouts</h2>
                    
                    <p>Multi-column layouts have been a staple of print design for centuries, offering readers an efficient and aesthetically pleasing way to consume text content. With CSS multi-column properties, we can now bring this elegant layout paradigm to digital interfaces.</p>
                    
                    <p>The CSS columns specification allows content to flow naturally from one column to the next, automatically balancing the text across available space. This creates a more compact presentation that can reduce scrolling and improve readability for certain types of content.</p>
                    
                    <h2>Benefits of Horizontal Scrolling</h2>
                    
                    <p>While vertical scrolling is the dominant paradigm on the web, horizontal scrolling offers unique advantages for specific use cases. Magazine-style layouts, image galleries, and timeline presentations can benefit greatly from horizontal navigation.</p>
                    
                    <p>By combining multi-column CSS with horizontal scrolling, we create an interface that mimics the experience of reading a physical newspaper or magazine, where content spans multiple columns across a wide surface.</p>
                    
                    <h2>Implementation Details</h2>
                    
                    <p>This implementation uses a CSS ::after pseudo-element to create physical space for the right margin. Unlike regular margins which can be excluded from scroll calculations, the pseudo-element creates an actual block that becomes part of the column layout.</p>
                    
                    <p>The ::after element has a width equal to the right margin value and participates in the column flow. This ensures that the scrollable area always includes the right margin space, regardless of viewport height changes.</p>
                    
                    <h2>WebKit Integration</h2>
                    
                    <p>WebKitGTK provides a powerful rendering engine that brings modern web standards to GTK applications. Version 6.0 brings improved performance, better standards compliance, and enhanced developer tools for debugging and optimization.</p>
                    
                    <p>The developer extras enabled in this application allow you to inspect elements, view computed styles, and debug JavaScript directly within the GTK application. Console messages are redirected to stdout, making it easy to monitor the application's behavior during development.</p>
                    
                    <h2>Libadwaita Design Patterns</h2>
                    
                    <p>This application follows Libadwaita design guidelines, using the ToolbarView pattern for modern GNOME applications. The AdwHeaderBar provides a clean, platform-integrated title bar that respects system themes and user preferences.</p>
                    
                    <p>Libadwaita promotes consistency across GNOME applications, ensuring that users enjoy a cohesive experience regardless of which applications they use. By adhering to these patterns, developers contribute to the overall quality and polish of the GNOME ecosystem.</p>
                    
                    <h2>Interactive Margin Controls</h2>
                    
                    <p>The left sidebar provides live margin adjustment controls using Adwaita SpinRow widgets. As you adjust the values, JavaScript dynamically updates the CSS custom properties, allowing you to see changes in real-time without reloading the page.</p>
                    
                    <p>This demonstrates the powerful interoperability between GTK widgets and web content through WebKitGTK's JavaScript bridge. The evaluate_javascript method allows seamless communication from the GTK side to manipulate the web content programmatically.</p>
                    
                    <h2>Scrolling Behavior</h2>
                    
                    <p>The horizontal scrollbar appears automatically when content exceeds the viewport width. Kinetic scrolling is enabled for smooth, momentum-based scrolling that feels natural on touchpads and touchscreens.</p>
                    
                    <p>Users can scroll horizontally using the scrollbar, trackpad gestures, or mouse wheel (when available). This provides multiple interaction methods to accommodate different user preferences and hardware configurations.</p>
                    
                    <h2>Typography and Readability</h2>
                    
                    <p>Proper typography is essential for multi-column layouts. The line-height is set to 1.6 to provide comfortable spacing between lines, reducing eye strain during extended reading sessions.</p>
                    
                    <p>Text is justified (text-align: justify) to create clean, aligned edges on both sides of each column, mimicking traditional print layouts. Headers use a darker color for emphasis while maintaining readability.</p>
                    
                    <h2>Future Enhancements</h2>
                    
                    <p>This foundation could be extended with features like dynamic content loading, adjustable column widths, theme switching between light and dark modes, and integration with document formats like Markdown or reStructuredText.</p>
                    
                    <p>Additional enhancements might include keyboard shortcuts for navigation, bookmarking capabilities, search functionality, and export options to various formats. The possibilities are limited only by imagination and user needs.</p>
                    
                    <h2>Conclusion</h2>
                    
                    <p>Multi-column layouts with horizontal scrolling offer a unique and engaging way to present content in desktop applications. By leveraging modern web standards through WebKitGTK and following Libadwaita design patterns, we create applications that are both functional and beautiful.</p>
                    
                    <p>This example demonstrates that powerful, magazine-style layouts are well within reach for GTK developers, opening new possibilities for content-rich applications in the GNOME ecosystem.</p>
                </div>
            </div>
        </div>
        </body>
        </html>"""
        
        self.webview.load_html(html_content, "file:///")


class ColumnLayoutApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.ColumnLayout')
        
    def do_activate(self):
        win = ColumnLayoutWindow(application=self)
        win.present()


if __name__ == '__main__':
    app = ColumnLayoutApp()
    sys.exit(app.run(sys.argv))
