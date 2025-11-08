#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')

from gi.repository import Gtk, Adw, WebKit, GLib, GObject
import os, sys
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
        
        # Create preferences group with updated title
        group = Adw.PreferencesGroup()
        group.set_title("Appearance")
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
        adjustment = Gtk.Adjustment(value=80, lower=0, upper=300, step_increment=5, page_increment=10)
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
        
        # Column Gap
        self.column_gap_row = Adw.SpinRow()
        self.column_gap_row.set_title("Column Gap")
        self.column_gap_row.set_subtitle("Pixels")
        adjustment = Gtk.Adjustment(value=32, lower=0, upper=100, step_increment=4, page_increment=10)
        self.column_gap_row.set_adjustment(adjustment)
        self.column_gap_row.set_digits(0)
        self.column_gap_row.connect("changed", self.on_column_gap_changed)
        group.add(self.column_gap_row)
        
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
        """Update margin and trigger layout check"""
        value = int(spin_row.get_value())
        
        # Update the global values
        js_code = f"""
            window.currentPadding.{margin_type} = {value};
            console.log('Updated {margin_type} margin to {value}px');
            checkAndApplyLayout();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
    
    def on_column_width_changed(self, spin_row):
        """Update column width and trigger layout check"""
        value = int(spin_row.get_value())
        js_code = f"""
            window.currentColumnWidth = {value};
            console.log('Updated column width to {value}px');
            checkAndApplyLayout();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
    
    def on_column_gap_changed(self, spin_row):
        """Update column gap and trigger layout check"""
        value = int(spin_row.get_value())
        js_code = f"""
            window.currentGap = {value};
            console.log('Updated column gap to {value}px');
            checkAndApplyLayout();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
    
    def load_html_content(self):
        """Load the HTML content with dynamic column switching"""
        html_content = """<!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
        /* --- CSS --- */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        html {
            height: 100vh;
            overflow: hidden;
        }
        
        body {
            margin: 0;
            padding: 0;
            height: 100vh;
            overflow: hidden;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        
        .page {
            /* Initial multi-column settings */
            column-width: 320px;
            -webkit-column-width: 320px;
            column-gap: 32px;
            -webkit-column-gap: 32px;
            column-fill: auto;
            -webkit-column-fill: auto;
            
            /* Padding (margins) */
            padding: 40px 80px 40px 80px;
            
            /* Container dimensions */
            width: 100vw;
            height: 100vh;
            
            /* Enable both scroll directions - JS will manage based on column count */
            overflow-x: auto;
            overflow-y: auto;
            
            box-sizing: border-box;
            position: relative;
        }
        
        /* Reset nested columns */
        .page * {
            -webkit-column-count: unset !important;
            column-count: unset !important;
        }
        
        /* Pseudo-element to ensure right margin is visible after last column */
        .page::after {
            content: '';
            display: block;
            height: 1px;
            width: 80px;
            break-before: column;
        }
        
        h1 {
            margin-top: 0;
            color: #1a1a1a;
            break-after: avoid;
        }
        
        h2 {
            margin-top: 1.5em;
            color: #2a2a2a;
            break-after: avoid;
        }
        
        p {
            text-align: justify;
            line-height: 1.6;
            margin-bottom: 1em;
            break-inside: auto;
        }
        
        img, svg {
            max-width: 100%;
            height: auto;
            break-inside: avoid;
        }
        /* --- END CSS --- */
        </style>
        <script>
        (function() {
            // Console logging setup
            const originalLog = console.log;
            console.log = function(...args) {
                originalLog.apply(console, args);
            };
            
            console.log('=== COLUMN SCRIPT LOADED ===');
            
            // Global state
            window.currentColumnWidth = 320;
            window.currentGap = 32;
            window.currentPadding = {
                top: 40,
                right: 80,
                bottom: 40,
                left: 80
            };
            window.isSingleColumnMode = false;
            
            // Get container metrics
            window.getContainerMetrics = function() {
                const container = document.querySelector('.page');
                if (!container) return null;
                
                const style = getComputedStyle(container);
                const paddingLeft = parseFloat(style.paddingLeft) || 0;
                const paddingRight = parseFloat(style.paddingRight) || 0;
                const gap = parseFloat(style.columnGap) || 0;
                
                const clientWidth = container.clientWidth;
                const clientHeight = container.clientHeight;
                const scrollWidth = container.scrollWidth;
                const availableWidth = clientWidth - paddingLeft - paddingRight;
                
                const colWidth = parseFloat(style.columnWidth) || window.currentColumnWidth || 300;
                const colCount = Math.max(1, Math.floor((availableWidth + gap) / (colWidth + gap)));
                
                return {
                    container: container,
                    clientWidth: clientWidth,
                    clientHeight: clientHeight,
                    scrollWidth: scrollWidth,
                    availableWidth: availableWidth,
                    paddingLeft: paddingLeft,
                    paddingRight: paddingRight,
                    gap: gap,
                    columnWidth: colWidth,
                    colCount: colCount
                };
            };
            
            // Check layout and switch between single/multi-column modes
            window.checkAndApplyLayout = function() {
                const container = document.querySelector('.page');
                if (!container) return;
                
                const colWidth = window.currentColumnWidth || 320;
                const gap = window.currentGap || 32;
                const padding = window.currentPadding;
                
                const style = getComputedStyle(container);
                const paddingLeft = parseFloat(style.paddingLeft) || padding.left;
                const paddingRight = parseFloat(style.paddingRight) || padding.right;
                const clientWidth = container.clientWidth;
                const availableWidth = clientWidth - paddingLeft - paddingRight;
                
                // Calculate how many columns would fit
                const wouldFitCols = Math.max(1, Math.floor((availableWidth + gap) / (colWidth + gap)));
                
                console.log('🔍 Layout check: ' + wouldFitCols + ' cols would fit (availW=' + availableWidth + 'px, colW=' + colWidth + 'px)');
                
                if (wouldFitCols === 1 && !window.isSingleColumnMode) {
                    // Switch to single-column mode
                    console.log('📖 Switching to single-column vertical scroll mode');
                    container.style.cssText = `
                        column-width: unset;
                        -webkit-column-width: unset;
                        column-count: unset;
                        -webkit-column-count: unset;
                        column-gap: unset;
                        -webkit-column-gap: unset;
                        column-fill: unset;
                        -webkit-column-fill: unset;
                        
                        padding: ${padding.top}px ${padding.right}px ${padding.bottom}px ${padding.left}px;
                        
                        width: 100%;
                        height: 100vh;
                        overflow-x: hidden;
                        overflow-y: auto;
                        box-sizing: border-box;
                        position: relative;
                    `;
                    window.isSingleColumnMode = true;
                } else if (wouldFitCols > 1 && window.isSingleColumnMode !== false) {
                    // Switch to multi-column mode
                    console.log('📰 Switching to multi-column mode (' + wouldFitCols + ' cols)');
                    container.style.cssText = `
                        column-width: ${colWidth}px;
                        -webkit-column-width: ${colWidth}px;
                        column-gap: ${gap}px;
                        -webkit-column-gap: ${gap}px;
                        column-fill: auto;
                        -webkit-column-fill: auto;
                        
                        padding: ${padding.top}px ${padding.right}px ${padding.bottom}px ${padding.left}px;
                        
                        width: 100vw;
                        height: 100vh;
                        overflow-x: auto;
                        overflow-y: hidden;
                        box-sizing: border-box;
                        position: relative;
                    `;
                    window.isSingleColumnMode = false;
                } else if (wouldFitCols > 1 && !window.isSingleColumnMode) {
                    // Just update the properties without full reset
                    container.style.columnWidth = colWidth + 'px';
                    container.style.webkitColumnWidth = colWidth + 'px';
                    container.style.columnGap = gap + 'px';
                    container.style.webkitColumnGap = gap + 'px';
                    container.style.padding = `${padding.top}px ${padding.right}px ${padding.bottom}px ${padding.left}px`;
                }
                
                // Update the ::after pseudo-element width for right margin
                const afterWidth = padding.right;
                const styleSheet = document.styleSheets[0];
                // Find and update the ::after rule
                for (let i = 0; i < styleSheet.cssRules.length; i++) {
                    const rule = styleSheet.cssRules[i];
                    if (rule.selectorText === '.page::after') {
                        rule.style.width = afterWidth + 'px';
                        break;
                    }
                }
            };
            
            // Window resize handler
            let resizeTimer;
            window.addEventListener('resize', function() {
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(function() {
                    checkAndApplyLayout();
                }, 400);
            });
            
            // Initial layout check on page load
            setTimeout(() => {
                checkAndApplyLayout();
            }, 100);
            
            // Initial metrics logging
            setTimeout(() => {
                const m = getContainerMetrics();
                if (m) {
                    console.log('📏 Initial Metrics:');
                    console.log('  Column width: ' + window.currentColumnWidth + 'px');
                    console.log('  Viewport cols: ' + m.colCount);
                    console.log('  Single-column mode: ' + (window.isSingleColumnMode ? 'YES' : 'NO'));
                    console.log('  clientW: ' + m.clientWidth + 'px');
                    console.log('  availableW: ' + m.availableWidth + 'px');
                    console.log('  gap: ' + m.gap + 'px');
                }
            }, 200);
            
            console.log('=== SCRIPT READY ===');
        })();
        </script>
        </head>
        <body>
        <div class="page">
            <h1>Responsive Multi-Column Layout</h1>
            
            <p>This is a Libadwaita-compliant GTK4 + WebKitGTK 6.0 application demonstrating a responsive multi-column layout. When the window is wide enough, content flows horizontally across multiple columns with horizontal scrolling. When the window is narrow (only fits one column), it automatically switches to single-column mode with vertical scrolling.</p>
            
            <h2>Dynamic Layout Switching</h2>
            
            <p>The layout intelligently detects the available width and switches between multi-column horizontal scrolling and single-column vertical scrolling. This provides the best reading experience regardless of window size.</p>
            
            <p>The switching logic calculates how many columns would fit based on the current column width, gap, and padding settings. When only one column fits, it disables the CSS multi-column properties and enables vertical scrolling.</p>
            
            <h2>Margin Control</h2>
            
            <p>All four margins (top, right, bottom, left) are now properly controlled through the sidebar. The margins work correctly in both single-column and multi-column modes.</p>
            
            <p>In multi-column mode, the right margin is implemented using a CSS ::after pseudo-element that ensures the margin space is included in the horizontal scroll width.</p>
            
            <h2>Benefits of Multi-Column Layouts</h2>
            
            <p>Multi-column layouts have been a staple of print design for centuries, offering readers an efficient and aesthetically pleasing way to consume text content. With CSS multi-column properties, we can now bring this elegant layout paradigm to digital interfaces.</p>
            
            <p>The CSS columns specification allows content to flow naturally from one column to the next, automatically balancing the text across available space. This creates a more compact presentation that can reduce scrolling and improve readability for certain types of content.</p>
            
            <h2>Horizontal Scrolling</h2>
            
            <p>While vertical scrolling is the dominant paradigm on the web, horizontal scrolling offers unique advantages for specific use cases. Magazine-style layouts, image galleries, and timeline presentations can benefit greatly from horizontal navigation.</p>
            
            <p>By combining multi-column CSS with horizontal scrolling, we create an interface that mimics the experience of reading a physical newspaper or magazine, where content spans multiple columns across a wide surface.</p>
            
            <h2>WebKit Integration</h2>
            
            <p>WebKitGTK provides a powerful rendering engine that brings modern web standards to GTK applications. Version 6.0 brings improved performance, better standards compliance, and enhanced developer tools for debugging and optimization.</p>
            
            <p>The developer extras enabled in this application allow you to inspect elements, view computed styles, and debug JavaScript directly within the GTK application. Console messages are visible in the terminal, making it easy to monitor the application's behavior during development.</p>
            
            <h2>Libadwaita Design Patterns</h2>
            
            <p>This application follows Libadwaita design guidelines, using the ToolbarView pattern for modern GNOME applications. The AdwHeaderBar provides a clean, platform-integrated title bar that respects system themes and user preferences.</p>
            
            <p>Libadwaita promotes consistency across GNOME applications, ensuring that users enjoy a cohesive experience regardless of which applications they use. By adhering to these patterns, developers contribute to the overall quality and polish of the GNOME ecosystem.</p>
            
            <h2>Interactive Controls</h2>
            
            <p>The left sidebar provides live adjustment controls using Adwaita SpinRow widgets. As you adjust the values, JavaScript dynamically updates the layout, allowing you to see changes in real-time without reloading the page.</p>
            
            <p>This demonstrates the powerful interoperability between GTK widgets and web content through WebKitGTK's JavaScript bridge. The evaluate_javascript method allows seamless communication from the GTK side to manipulate the web content programmatically.</p>
            
            <h2>Typography and Readability</h2>
            
            <p>Proper typography is essential for multi-column layouts. The line-height is set to 1.6 to provide comfortable spacing between lines, reducing eye strain during extended reading sessions.</p>
            
            <p>Text is justified (text-align: justify) to create clean, aligned edges on both sides of each column, mimicking traditional print layouts. Headers use a darker color for emphasis while maintaining readability.</p>
            
            <h2>Future Enhancements</h2>
            
            <p>This foundation could be extended with features like dynamic content loading, adjustable column counts, theme switching between light and dark modes, and integration with document formats like Markdown or reStructuredText.</p>
            
            <p>Additional enhancements might include keyboard shortcuts for navigation, bookmarking capabilities, search functionality, and export options to various formats. The possibilities are limited only by imagination and user needs.</p>
            
            <h2>Conclusion</h2>
            
            <p>Responsive multi-column layouts with intelligent mode switching offer a flexible and engaging way to present content in desktop applications. By leveraging modern web standards through WebKitGTK and following Libadwaita design patterns, we create applications that are both functional and beautiful.</p>
            
            <p>This example demonstrates that powerful, adaptive layouts are well within reach for GTK developers, opening new possibilities for content-rich applications in the GNOME ecosystem.</p>
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
