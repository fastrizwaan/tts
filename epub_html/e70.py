#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, PangoCairo

from ebooklib import epub

def _make_abs_for_resource(url: str, file_dir: str, tempdir: str):
    url = url.strip()
    if not url:
        return url
    if url.startswith(('#', 'http://', 'https://', 'data:', 'mailto:', 'javascript:', 'file://')):
        return url
    parts = url.split('#', 1)
    rel = parts[0]
    frag = ('#' + parts[1]) if len(parts) == 2 else ''
    candidates = []
    if os.path.isabs(rel):
        candidates.append(rel)
    else:
        if file_dir:
            candidates.append(os.path.normpath(os.path.join(file_dir, rel)))
        if tempdir:
            candidates.append(os.path.normpath(os.path.join(tempdir, rel)))
    suffixes = ['', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.html', '.htm', '.xhtml']
    for cand in candidates:
        for s in suffixes:
            path_try = cand if cand.endswith(s) else cand + s
            if os.path.exists(path_try):
                return "file://" + path_try + frag
    if tempdir:
        joined = urllib.parse.urljoin("file://" + tempdir.replace(os.sep, '/') + "/", url)
        return joined
    return url

def _rewrite_resource_urls(html: str, file_dir: str, tempdir: str) -> str:
    def attr_repl(m):
        attr = m.group('attr')
        quote = m.group('quote')
        url = m.group('url')
        new = _make_abs_for_resource(url, file_dir, tempdir)
        return f"{attr}{quote}{new}{quote}"
    html = re.sub(r'(?P<attr>\b(?:src|href)\s*=\s*)(?P<quote>["\'])(?P<url>[^"\']+)(?P=quote)',
                  attr_repl, html, flags=re.I)
    def cssurl_repl(m):
        url = m.group(2)
        new = _make_abs_for_resource(url, file_dir, tempdir)
        return f'url("{new}")'
    html = re.sub(r'url\((["\']?)([^)\'"]+)(["\']?)\)', cssurl_repl, html, flags=re.I)
    return html

_INJECT_CSS = """<style id="__epub_viewer_css">
html, body {
    margin: 0;
    padding: 0;
    background-color: var(--bg-color, #ffffff);
    color: var(--text-color, #000000);
    transition: background-color 0.3s ease, color 0.3s ease;
}

img, svg, video, iframe { 
    max-width: 100% !important; 
    height: auto !important; 
    object-fit: contain !important; 
}

img { 
    max-height: 80vh !important; 
}

/* Light mode (default) */
:root {
    --bg-color: #ffffff;
    --text-color: #000000;
    --heading-color: #1a1a1a;
    --link-color: #0066cc;
    --link-hover-color: #004499;
    --border-color: #e0e0e0;
    --code-bg: #f8f8f8;
    --code-color: #333333;
    --blockquote-bg: #f9f9f9;
    --blockquote-border: #cccccc;
    --table-border: #dddddd;
    --table-header-bg: #f5f5f5;
}

/* Dark mode */
.force-dark-mode {
    --bg-color: #2d2d2d !important;
    --text-color: #e6e6e6 !important;
    --heading-color: #ffffff !important;
    --link-color: #6db3f2 !important;
    --link-hover-color: #9cc9f7 !important;
    --border-color: #555555 !important;
    --code-bg: #1e1e1e !important;
    --code-color: #f8f8f2 !important;
    --blockquote-bg: #3a3a3a !important;
    --blockquote-border: #6db3f2 !important;
    --table-border: #555555 !important;
    --table-header-bg: #3a3a3a !important;
}

/* Apply theme variables */
html, body {
    background-color: var(--bg-color) !important;
    color: var(--text-color) !important;
}

div, p, span, article, section, main, aside, nav, header, footer {
    background-color: transparent !important;
    color: inherit !important;
}

h1, h2, h3, h4, h5, h6 {
    color: var(--heading-color) !important;
}

a, a:link, a:visited {
    color: var(--link-color) !important;
}

a:hover, a:active {
    color: var(--link-hover-color) !important;
}

code, pre, kbd, samp {
    background-color: var(--code-bg) !important;
    color: var(--code-color) !important;
    border: 1px solid var(--border-color) !important;
    padding: 2px 4px !important;
    border-radius: 3px !important;
}

pre {
    padding: 8px 12px !important;
}

table { 
    background-color: transparent !important; 
    border-color: var(--table-border) !important; 
}

th, td { 
    background-color: transparent !important; 
    color: inherit !important; 
    border-color: var(--table-border) !important; 
}

th { 
    background-color: var(--table-header-bg) !important; 
    color: var(--heading-color) !important; 
}

blockquote { 
    background-color: var(--blockquote-bg) !important; 
    color: inherit !important; 
    border-left: 4px solid var(--blockquote-border) !important;
    padding: 8px 16px !important;
    margin: 16px 0 !important;
}

input, textarea, select { 
    background-color: var(--code-bg) !important; 
    color: var(--text-color) !important; 
    border: 1px solid var(--border-color) !important; 
}

hr { 
    border-color: var(--border-color) !important; 
}

/* System dark mode support */
@media (prefers-color-scheme: dark) {
    :root:not(.force-light-mode) {
        --bg-color: #2d2d2d;
        --text-color: #e6e6e6;
        --heading-color: #ffffff;
        --link-color: #6db3f2;
        --link-hover-color: #9cc9f7;
        --border-color: #555555;
        --code-bg: #1e1e1e;
        --code-color: #f8f8f2;
        --blockquote-bg: #3a3a3a;
        --blockquote-border: #6db3f2;
        --table-border: #555555;
        --table-header-bg: #3a3a3a;
    }
}

/* Content padding for reading comfort */
body {
    padding: 20px !important;
    line-height: 1.6 !important;
}

/* Column mode adjustments */
.column-mode {
    padding: 20px 40px !important;
}

/* Prevent orphaned content in columns */
p, div, section, article {
    orphans: 2;
    widows: 2;
}

h1, h2, h3, h4, h5, h6 {
    break-after: avoid;
    page-break-after: avoid;
}
</style>
"""

def inject_css_into_html(html: str) -> str:
    if re.search(r'<head\b', html, re.I):
        return re.sub(r'(<head\b[^>]*>)', r'\1' + _INJECT_CSS, html, flags=re.I)
    if re.search(r'<html\b', html, re.I):
        return re.sub(r'(<html\b[^>]*>)', r'\1<head>' + _INJECT_CSS + '</head>', html, flags=re.I)
    return "<!doctype html><head>" + _INJECT_CSS + "</head><body>" + html + "</body></html>"

class Writer(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.viewer")
        
        # Column layout actions
        col_action = Gio.SimpleAction.new("set-columns", GLib.VariantType.new("s"))
        col_action.connect("activate", self._on_app_set_columns)
        self.add_action(col_action)
        
        pixel_col_action = Gio.SimpleAction.new("set-pixel-columns", GLib.VariantType.new("s"))
        pixel_col_action.connect("activate", self._on_app_set_pixel_columns)
        self.add_action(pixel_col_action)
        
        self.connect("activate", self.on_activate)

    def _on_app_set_columns(self, action, param):
        win = self.get_active_window()
        if win and hasattr(win, "set_column_count"):
            try:
                n = int(param.get_string())
            except Exception:
                n = 1
            win.set_column_count(n)

    def _on_app_set_pixel_columns(self, action, param):
        win = self.get_active_window()
        if win and hasattr(win, "set_column_width"):
            try:
                pixels = int(param.get_string())
            except Exception:
                pixels = 400
            win.set_column_width(pixels)

    def on_activate(self, app):
        win = ViewerWindow(application=self)
        win.present()

class ViewerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("HTML/EPUB Viewer")
        self.set_default_size(1200, 800)

        # EPUB state
        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self._flat_toc = []
        self._spine_hrefs = []
        self._sidebar_visible = False
        self._dark_mode_forced = False
        
        # Column settings inspired by the reference program
        self.column_mode = 'width'  # 'width' or 'fixed'
        self.fixed_column_count = 2
        self.desired_column_width = 400
        self.column_gap = 40
        self.column_padding = 20
        self.actual_column_width = self.desired_column_width
        
        # Timeout handling for smooth interactions
        self.snap_timeout_id = None
        self.resize_timeout_id = None

        self.setup_ui()

    def setup_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        toolbar_view = Adw.ToolbarView()
        main_box.append(toolbar_view)
        header = Adw.HeaderBar()
        header.add_css_class("flat-header")
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(controls)

        self.back_btn = Gtk.Button(icon_name="go-previous")
        self.back_btn.add_css_class("flat")
        self.back_btn.set_tooltip_text("Previous Page")
        self.back_btn.connect("clicked", self._on_page_previous)
        controls.append(self.back_btn)

        self.forward_btn = Gtk.Button(icon_name="go-next")
        self.forward_btn.add_css_class("flat")
        self.forward_btn.set_tooltip_text("Next Page")
        self.forward_btn.connect("clicked", self._on_page_next)
        controls.append(self.forward_btn)

        open_btn = Gtk.Button(icon_name="document-open")
        open_btn.add_css_class("flat")
        open_btn.connect("clicked", self.on_open_clicked)
        controls.append(open_btn)

        self.sidebar_btn = Gtk.Button(icon_name="view-dual-symbolic")
        self.sidebar_btn.add_css_class("flat")
        self.sidebar_btn.set_sensitive(False)
        self.sidebar_btn.connect("clicked", lambda btn: self.toggle_sidebar())
        controls.append(self.sidebar_btn)

        self.dark_mode_btn = Gtk.Button(icon_name="weather-clear-night-symbolic")
        self.dark_mode_btn.add_css_class("flat")
        self.dark_mode_btn.set_tooltip_text("Toggle Dark Mode")
        self.dark_mode_btn.connect("clicked", lambda btn: self.toggle_dark_mode())
        controls.append(self.dark_mode_btn)

        # Column layout menu - improved with small widths
        self.column_btn = Gtk.MenuButton()
        self.column_btn.set_icon_name("view-column-symbolic")
        self.column_btn.add_css_class("flat")
        self.column_btn.set_tooltip_text("Column Layout")
        header.pack_start(self.column_btn)

        column_menu = Gio.Menu()
        
        # Count-based columns section
        count_section = Gio.Menu()
        count_section.append("Single Column", "app.set-columns('1')")
        count_section.append("Two Columns", "app.set-columns('2')")
        count_section.append("Three Columns", "app.set-columns('3')")
        count_section.append("Four Columns", "app.set-columns('4')")
        column_menu.append_section("Column Count", count_section)
        
        # Pixel-based columns section - including small widths
        pixel_section = Gio.Menu()
        pixel_section.append("50px Columns", "app.set-pixel-columns('50')")
        pixel_section.append("100px Columns", "app.set-pixel-columns('100')")
        pixel_section.append("150px Columns", "app.set-pixel-columns('150')")
        pixel_section.append("200px Columns", "app.set-pixel-columns('200')")
        pixel_section.append("300px Columns", "app.set-pixel-columns('300')")
        pixel_section.append("400px Columns", "app.set-pixel-columns('400')")
        pixel_section.append("500px Columns", "app.set-pixel-columns('500')")
        column_menu.append_section("Column Width", pixel_section)
        
        # Reset section
        reset_section = Gio.Menu()
        reset_section.append("Remove Columns", "app.set-columns('0')")
        column_menu.append_section("Reset", reset_section)
        
        self.column_btn.set_menu_model(column_menu)

        font_map = PangoCairo.FontMap.get_default()
        families = font_map.list_families()
        font_names = sorted([f.get_name() for f in families])
        font_store = Gtk.StringList(strings=font_names)
        self.font_dropdown = Gtk.DropDown(model=font_store)
        default_index = font_names.index("Sans") if "Sans" in font_names else 0
        self.font_dropdown.set_selected(default_index)
        self.font_dropdown.add_css_class("flat")
        self.font_dropdown.connect("notify::selected", self.on_font_family_changed)
        header.pack_end(self.font_dropdown)

        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_paned.set_shrink_start_child(False)
        self.main_paned.set_shrink_end_child(False)
        self.main_paned.set_resize_start_child(False)
        self.main_paned.set_resize_end_child(True)
        toolbar_view.set_content(self.main_paned)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.set_size_request(300, -1)
        self.sidebar.add_css_class("sidebar")

        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_header.set_margin_top(6)
        sidebar_header.set_margin_bottom(6)
        sidebar_header.set_margin_start(12)
        sidebar_header.set_margin_end(12)
        sidebar_title = Gtk.Label(label="Table of Contents")
        sidebar_title.add_css_class("heading")
        sidebar_title.set_xalign(0)
        sidebar_header.append(sidebar_title)
        self.sidebar.append(sidebar_header)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar.append(separator)

        sidebar_scroll = Gtk.ScrolledWindow(vexpand=True)
        sidebar_scroll.set_margin_start(6)
        sidebar_scroll.set_margin_end(6)
        sidebar_scroll.set_margin_bottom(6)
        self.sidebar_listbox = Gtk.ListBox()
        self.sidebar_listbox.add_css_class("navigation-sidebar")
        sidebar_scroll.set_child(self.sidebar_listbox)
        self.sidebar.append(sidebar_scroll)

        content_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.webview = WebKit.WebView()
        content_scroll.set_child(self.webview)
        self.webview.load_html("<!doctype html><html><body><p>Open an EPUB file to begin reading...</p></body></html>", "file:///")

        self.main_paned.set_end_child(content_scroll)

        # Setup navigation and event handling
        self.setup_navigation()

        self.connect("close-request", self.on_close_request)
        self.webview.connect("decide-policy", self.on_decide_policy)
        
        # Window resize handling
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)

    def setup_navigation(self):
        """Setup keyboard and scroll event handling"""
        # Key navigation
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)
        
        # Mouse wheel navigation
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.webview.add_controller(scroll_controller)

    def is_single_column_mode(self):
        """Check if we're effectively in single column mode"""
        if self.column_mode == 'fixed' and self.fixed_column_count <= 1:
            return True
        elif self.column_mode == 'width':
            width = self.get_allocated_width()
            if width <= 0:
                width = 1200
            available = max(100, width - (2 * self.column_padding))
            if self.actual_column_width >= (available - self.column_gap):
                return True
        return False

    def calculate_column_dimensions(self):
        """Calculate actual column dimensions based on current window size"""
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            width = 1200
            height = 800
        
        available = max(100, width - (2 * self.column_padding))
        
        if self.column_mode == 'fixed':
            cols = max(1, int(self.fixed_column_count))
            total_gap = (cols - 1) * self.column_gap
            cw = max(50, (available - total_gap) // cols)
            self.actual_column_width = cw
        else:
            # width mode: keep desired, but ensure at least one fits
            self.actual_column_width = max(50, min(self.desired_column_width, available))

    def set_column_count(self, count):
        """Set fixed column count mode"""
        try:
            count = int(count)
            if count < 0:
                count = 1
        except Exception:
            count = 1
        
        self.column_mode = 'fixed'
        self.fixed_column_count = count
        
        if self._book:
            self.calculate_column_dimensions()
            self.apply_current_column_layout()

    def set_column_width(self, width):
        """Set pixel-based column width mode"""
        try:
            w = int(width)
            if w < 50:
                w = 50
        except Exception:
            w = 400
        
        self.column_mode = 'width'
        self.desired_column_width = w
        
        if self._book:
            self.calculate_column_dimensions()
            self.apply_current_column_layout()

    def apply_current_column_layout(self):
        """Apply the current column layout settings to the content"""
        self.calculate_column_dimensions()
        is_single = self.is_single_column_mode()

        js_template = """
        (function() {
            const config = __CONFIG__;
            let wrapper = document.getElementById('__viewer_column_wrapper');
            
            // Clean up previous event listeners
            if (wrapper && wrapper.__cleanup_handlers) {
                wrapper.__cleanup_handlers.forEach(cleanup => cleanup());
                wrapper.__cleanup_handlers = [];
            }

            // Create wrapper if it doesn't exist
            if (!wrapper) {
                wrapper = document.createElement('div');
                wrapper.id = '__viewer_column_wrapper';
                wrapper.__cleanup_handlers = [];
                
                // Move all body content into wrapper
                while (document.body.firstChild) {
                    wrapper.appendChild(document.body.firstChild);
                }
                document.body.appendChild(wrapper);
                
                // Set up basic document styles
                document.documentElement.style.height = '100%';
                document.documentElement.style.overflow = 'hidden';
                document.body.style.height = '100%';
                document.body.style.margin = '0';
                document.body.style.padding = '0';
                document.body.style.overflow = 'hidden';
            }

            const vw = window.innerWidth;
            const vh = window.innerHeight;

            // Reset to single column mode
            if (config.isSingle) {
                wrapper.style.cssText = '';
                wrapper.style.width = '100%';
                wrapper.style.height = '100%';
                wrapper.style.overflowX = 'hidden';
                wrapper.style.overflowY = 'auto';
                wrapper.style.boxSizing = 'border-box';
                wrapper.style.padding = config.padding + 'px';
                
                // Remove column mode class
                document.body.classList.remove('column-mode');
                
                document.body.style.overflow = 'hidden';
                return;
            }

            // Add column mode class for styling
            document.body.classList.add('column-mode');

            // Multi-column setup with improved calculations
            wrapper.style.cssText = '';
            wrapper.style.boxSizing = 'border-box';
            wrapper.style.height = vh + 'px';
            wrapper.style.overflowX = 'auto';
            wrapper.style.overflowY = 'hidden';
            wrapper.style.scrollBehavior = 'smooth';
            wrapper.style.padding = config.padding + 'px ' + (config.padding * 2) + 'px';
            
            let cols, columnWidth;
            const columnGap = config.gap;
            
            if (config.mode === 'pixel') {
                // Pixel-based columns - calculate how many fit
                columnWidth = config.value;
                const availableWidth = vw - (config.padding * 4); // Account for padding
                cols = Math.floor(availableWidth / (columnWidth + columnGap));
                cols = Math.max(1, cols);
            } else if (config.mode === 'fixed') {
                // Count-based columns
                cols = config.value;
                const availableWidth = vw - (config.padding * 4);
                const totalGapWidth = (cols - 1) * columnGap;
                columnWidth = Math.floor((availableWidth - totalGapWidth) / cols);
            } else {
                // Default to single column
                cols = 1;
                columnWidth = vw - (config.padding * 4);
            }
            
            // Apply CSS column properties
            wrapper.style.columnWidth = columnWidth + 'px';
            wrapper.style.columnCount = cols;
            wrapper.style.columnGap = columnGap + 'px';
            wrapper.style.columnRule = '1px solid var(--border-color)';
            wrapper.style.columnFill = 'auto';
            
            // Enhanced break handling
            const style = document.createElement('style');
            style.textContent = `
                .column-mode h1, .column-mode h2, .column-mode h3, 
                .column-mode h4, .column-mode h5, .column-mode h6 {
                    break-after: avoid !important;
                    break-inside: avoid !important;
                    page-break-after: avoid !important;
                    page-break-inside: avoid !important;
                }
                .column-mode p, .column-mode div, .column-mode blockquote {
                    break-inside: avoid-column;
                    orphans: 2;
                    widows: 2;
                }
                .column-mode img, .column-mode figure {
                    break-inside: avoid !important;
                }
            `;
            if (!document.getElementById('column-break-style')) {
                style.id = 'column-break-style';
                document.head.appendChild(style);
            }
            
            // Calculate total content width for scrolling
            const totalWidth = (columnWidth * cols) + ((cols - 1) * columnGap) + (config.padding * 4);
            wrapper.style.width = totalWidth + 'px';

            // Enhanced wheel handling with precise snapping
            function handleWheel(event) {
                if (config.isSingle || event.shiftKey) {
                    return;
                }
                
                if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
                    event.preventDefault();
                    
                    const currentScroll = wrapper.scrollLeft;
                    const viewportWidth = window.innerWidth;
                    const stepSize = columnWidth + columnGap;
                    
                    // Calculate current and target columns
                    const currentColumn = Math.round(currentScroll / stepSize);
                    const scrollDirection = event.deltaY > 0 ? 1 : -1;
                    const targetColumn = Math.max(0, currentColumn + scrollDirection);
                    const maxColumns = Math.floor((totalWidth - viewportWidth) / stepSize);
                    const finalColumn = Math.min(targetColumn, maxColumns);
                    
                    wrapper.scrollTo({
                        left: finalColumn * stepSize,
                        behavior: 'smooth'
                    });
                }
            }

            // Enhanced keyboard navigation with better step calculation
            function handleKeydown(event) {
                if (config.isSingle) return;
                
                if (document.activeElement && 
                    (document.activeElement.tagName === 'INPUT' || 
                     document.activeElement.tagName === 'TEXTAREA' ||
                     document.activeElement.isContentEditable)) {
                    return;
                }
                
                const currentScroll = wrapper.scrollLeft;
                const stepSize = columnWidth + columnGap;
                const viewportWidth = window.innerWidth;
                const currentColumn = Math.round(currentScroll / stepSize);
                const maxColumns = Math.floor((totalWidth - viewportWidth) / stepSize);
                const columnsPerView = Math.floor(viewportWidth / stepSize) || 1;
                
                switch(event.key) {
                    case 'ArrowRight':
                    case 'PageDown':
                    case ' ': // Spacebar
                        event.preventDefault();
                        const nextColumn = Math.min(currentColumn + columnsPerView, maxColumns);
                        wrapper.scrollTo({
                            left: nextColumn * stepSize,
                            behavior: 'smooth'
                        });
                        break;
                    case 'ArrowLeft':
                    case 'PageUp':
                        event.preventDefault();
                        const prevColumn = Math.max(currentColumn - columnsPerView, 0);
                        wrapper.scrollTo({
                            left: prevColumn * stepSize,
                            behavior: 'smooth'
                        });
                        break;
                    case 'Home':
                        event.preventDefault();
                        wrapper.scrollTo({ left: 0, behavior: 'smooth' });
                        break;
                    case 'End':
                        event.preventDefault();
                        wrapper.scrollTo({ 
                            left: maxColumns * stepSize, 
                            behavior: 'smooth' 
                        });
                        break;
                }
            }

            // Resize handler with improved dimension recalculation
            function handleResize() {
                const newVw = window.innerWidth;
                const newVh = window.innerHeight;
                
                let newCols, newColumnWidth;
                const availableWidth = newVw - (config.padding * 4);
                
                if (config.mode === 'pixel') {
                    newColumnWidth = config.value;
                    newCols = Math.floor(availableWidth / (newColumnWidth + columnGap));
                    newCols = Math.max(1, newCols);
                } else if (config.mode === 'fixed') {
                    newCols = config.value;
                    const totalGapWidth = (newCols - 1) * columnGap;
                    newColumnWidth = Math.floor((availableWidth - totalGapWidth) / newCols);
                } else {
                    newCols = 1;
                    newColumnWidth = availableWidth;
                }
                
                const newTotalWidth = (newColumnWidth * newCols) + ((newCols - 1) * columnGap) + (config.padding * 4);
                
                wrapper.style.height = newVh + 'px';
                wrapper.style.columnWidth = newColumnWidth + 'px';
                wrapper.style.columnCount = newCols;
                wrapper.style.width = newTotalWidth + 'px';
                
                // Maintain relative scroll position
                const newStepSize = newColumnWidth + columnGap;
                const currentColumn = Math.round(wrapper.scrollLeft / (columnWidth + columnGap));
                wrapper.scrollLeft = currentColumn * newStepSize;
                
                // Update variables for next calculations
                columnWidth = newColumnWidth;
            }

            // Add event listeners and store cleanup functions
            wrapper.addEventListener('wheel', handleWheel, { passive: false });
            wrapper.__cleanup_handlers.push(() => {
                wrapper.removeEventListener('wheel', handleWheel);
            });

            window.addEventListener('keydown', handleKeydown);
            wrapper.__cleanup_handlers.push(() => {
                window.removeEventListener('keydown', handleKeydown);
            });

            window.addEventListener('resize', handleResize);
            wrapper.__cleanup_handlers.push(() => {
                window.removeEventListener('resize', handleResize);
            });

        })();
        """
        
        # Prepare configuration object
        config = {
            'isSingle': is_single,
            'mode': 'fixed' if self.column_mode == 'fixed' else 'pixel',
            'value': self.fixed_column_count if self.column_mode == 'fixed' else self.actual_column_width,
            'gap': self.column_gap,
            'padding': self.column_padding
        }
        
        js_code = js_template.replace("__CONFIG__", json.dumps(config))
        
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"JavaScript execution error: {e}")

    def on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard navigation with improved snapping"""
        if not self._book:
            return False
            
        self.calculate_column_dimensions()
        is_single = self.is_single_column_mode()
        
        # In single column mode, handle vertical scrolling
        if is_single:
            if keyval == 65365:  # Page Up
                js_code = """
                (function() {
                    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                    const clientHeight = document.documentElement.clientHeight;
                    const targetScroll = Math.max(0, scrollTop - clientHeight * 0.9);
                    window.scrollTo({ top: targetScroll, behavior: 'smooth' });
                })();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                return True
            elif keyval == 65366:  # Page Down
                js_code = """
                (function() {
                    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                    const clientHeight = document.documentElement.clientHeight;
                    const maxScroll = Math.max(0, document.documentElement.scrollHeight - clientHeight);
                    const targetScroll = Math.min(maxScroll, scrollTop + clientHeight * 0.9);
                    window.scrollTo({ top: targetScroll, behavior: 'smooth' });
                })();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                return True
            return False
        
        # Multi-column mode - horizontal navigation with precise snapping
        step_size = int(self.actual_column_width + self.column_gap)
        
        if keyval in (65361, 65365):  # Left or PageUp
            js_code = f"""
            (function() {{
                const wrapper = document.getElementById('__viewer_column_wrapper');
                if (!wrapper) return;
                
                const stepSize = {step_size};
                const currentScroll = wrapper.scrollLeft;
                const viewportWidth = window.innerWidth;
                const columnsPerView = Math.floor(viewportWidth / stepSize) || 1;
                
                const currentColumn = Math.round(currentScroll / stepSize);
                const targetColumn = Math.max(0, currentColumn - columnsPerView);
                const newScroll = targetColumn * stepSize;
                
                wrapper.scrollTo({{ left: newScroll, behavior: 'smooth' }});
            }})();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            return True
            
        elif keyval in (65363, 65366):  # Right or PageDown
            js_code = f"""
            (function() {{
                const wrapper = document.getElementById('__viewer_column_wrapper');
                if (!wrapper) return;
                
                const stepSize = {step_size};
                const currentScroll = wrapper.scrollLeft;
                const viewportWidth = window.innerWidth;
                const maxScroll = Math.max(0, wrapper.scrollWidth - viewportWidth);
                const columnsPerView = Math.floor(viewportWidth / stepSize) || 1;
                
                const currentColumn = Math.round(currentScroll / stepSize);
                const targetColumn = currentColumn + columnsPerView;
                const newScroll = Math.min(maxScroll, targetColumn * stepSize);
                
                wrapper.scrollTo({{ left: newScroll, behavior: 'smooth' }});
            }})();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            return True
            
        return False

    def on_scroll_event(self, controller, dx, dy):
        """Handle scroll events with improved snapping"""
        if not self._book:
            return False
            
        is_single = self.is_single_column_mode()
        
        # In single column mode, allow normal vertical scrolling
        if is_single:
            return False
        
        # Multi-column mode - handle horizontal scrolling with snapping
        if abs(dx) > 0.1 or abs(dy) > 0.1:
            scroll_left = dx > 0.1 or dy < -0.1
            scroll_right = dx < -0.1 or dy > 0.1
            
            step_size = int(self.actual_column_width + self.column_gap)
            
            if scroll_left or scroll_right:
                direction = -1 if scroll_left else 1
                js_code = f"""
                (function() {{
                    const wrapper = document.getElementById('__viewer_column_wrapper');
                    if (!wrapper) return;
                    
                    const stepSize = {step_size};
                    const currentScroll = wrapper.scrollLeft;
                    const viewportWidth = window.innerWidth;
                    const maxScroll = Math.max(0, wrapper.scrollWidth - viewportWidth);
                    
                    const currentColumn = Math.round(currentScroll / stepSize);
                    const targetColumn = Math.max(0, Math.min(
                        Math.floor(maxScroll / stepSize), 
                        currentColumn + {direction}
                    ));
                    const newScroll = targetColumn * stepSize;
                    
                    wrapper.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                }})();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                return True
        
        # Handle snapping for small scroll movements
        if self.snap_timeout_id:
            try:
                GLib.source_remove(self.snap_timeout_id)
            except Exception:
                pass
        self.snap_timeout_id = GLib.timeout_add(200, self.snap_to_nearest_column)
        return False

    def snap_to_nearest_column(self):
        """Snap to the nearest column boundary"""
        if not self._book or self.is_single_column_mode():
            self.snap_timeout_id = None
            return False
            
        step_size = int(self.actual_column_width + self.column_gap)
        
        js_code = f"""
        (function() {{
            const wrapper = document.getElementById('__viewer_column_wrapper');
            if (!wrapper) return;
            
            const stepSize = {step_size};
            const currentScroll = wrapper.scrollLeft;
            const nearestColumn = Math.round(currentScroll / stepSize);
            const targetScroll = nearestColumn * stepSize;
            
            const viewportWidth = window.innerWidth;
            const maxScroll = Math.max(0, wrapper.scrollWidth - viewportWidth);
            const clampedScroll = Math.max(0, Math.min(targetScroll, maxScroll));
            
            if (Math.abs(currentScroll - clampedScroll) > 5) {{
                wrapper.scrollTo({{ left: clampedScroll, behavior: 'smooth' }});
            }}
        }})();
        """
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        self.snap_timeout_id = None
        return False

    def _on_page_previous(self, *args):
        """Handle previous page with improved snapping"""
        if not self._book:
            return
            
        is_single = self.is_single_column_mode()
        
        if is_single:
            js_code = """
            (function() {
                const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                const clientHeight = document.documentElement.clientHeight;
                const targetScroll = Math.max(0, scrollTop - clientHeight * 0.9);
                window.scrollTo({ top: targetScroll, behavior: 'smooth' });
            })();
            """
        else:
            step_size = int(self.actual_column_width + self.column_gap)
            js_code = f"""
            (function() {{
                const wrapper = document.getElementById('__viewer_column_wrapper');
                if (!wrapper) return;
                
                const stepSize = {step_size};
                const currentScroll = wrapper.scrollLeft;
                const viewportWidth = window.innerWidth;
                const columnsPerView = Math.floor(viewportWidth / stepSize) || 1;
                
                const currentColumn = Math.round(currentScroll / stepSize);
                const targetColumn = Math.max(0, currentColumn - columnsPerView);
                const newScroll = targetColumn * stepSize;
                
                wrapper.scrollTo({{ left: newScroll, behavior: 'smooth' }});
            }})();
            """
        
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"Page navigation error: {e}")

    def _on_page_next(self, *args):
        """Handle next page with improved snapping"""
        if not self._book:
            return
            
        is_single = self.is_single_column_mode()
        
        if is_single:
            js_code = """
            (function() {
                const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                const clientHeight = document.documentElement.clientHeight;
                const maxScroll = Math.max(0, document.documentElement.scrollHeight - clientHeight);
                const targetScroll = Math.min(maxScroll, scrollTop + clientHeight * 0.9);
                window.scrollTo({ top: targetScroll, behavior: 'smooth' });
            })();
            """
        else:
            step_size = int(self.actual_column_width + self.column_gap)
            js_code = f"""
            (function() {{
                const wrapper = document.getElementById('__viewer_column_wrapper');
                if (!wrapper) return;
                
                const stepSize = {step_size};
                const currentScroll = wrapper.scrollLeft;
                const viewportWidth = window.innerWidth;
                const maxScroll = Math.max(0, wrapper.scrollWidth - viewportWidth);
                const columnsPerView = Math.floor(viewportWidth / stepSize) || 1;
                
                const currentColumn = Math.round(currentScroll / stepSize);
                const targetColumn = currentColumn + columnsPerView;
                const newScroll = Math.min(maxScroll, targetColumn * stepSize);
                
                wrapper.scrollTo({{ left: newScroll, behavior: 'smooth' }});
            }})();
            """
        
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"Page navigation error: {e}")

    def on_window_resize(self, *args):
        """Handle window resize with debouncing"""
        if self.resize_timeout_id:
            try:
                GLib.source_remove(self.resize_timeout_id)
            except Exception:
                pass
        self.resize_timeout_id = GLib.timeout_add(250, self._delayed_resize_update)

    def _delayed_resize_update(self):
        """Perform delayed resize update"""
        self.resize_timeout_id = None
        
        if self._book:
            self.calculate_column_dimensions()
            self.apply_current_column_layout()
        
        return False

    def toggle_sidebar(self, *args):
        if self._sidebar_visible:
            self.main_paned.set_start_child(None)
            self._sidebar_visible = False
            self.sidebar_btn.set_icon_name("view-dual-symbolic")
        else:
            self.main_paned.set_start_child(self.sidebar)
            self.main_paned.set_position(300)
            self._sidebar_visible = True
            self.sidebar_btn.set_icon_name("view-restore-symbolic")

    def toggle_dark_mode(self):
        self._dark_mode_forced = not self._dark_mode_forced
        if self._dark_mode_forced:
            self.dark_mode_btn.set_icon_name("weather-clear-symbolic")
            self.dark_mode_btn.set_tooltip_text("Disable Dark Mode")
            js_code = """
            (function() {
                document.documentElement.classList.remove('force-light-mode');
                document.documentElement.classList.add('force-dark-mode');
                document.body.classList.add('force-dark-mode');
            })();
            """
        else:
            self.dark_mode_btn.set_icon_name("weather-clear-night-symbolic")
            self.dark_mode_btn.set_tooltip_text("Enable Dark Mode")
            js_code = """
            (function() {
                document.documentElement.classList.remove('force-dark-mode');
                document.documentElement.classList.add('force-light-mode');
                document.body.classList.remove('force-dark-mode');
            })();
            """
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception:
            pass

    def populate_sidebar_toc(self):
        while True:
            row = self.sidebar_listbox.get_first_child()
            if row is None:
                break
            self.sidebar_listbox.remove(row)
        def on_sidebar_row_activated(listbox, row):
            row_index = row.get_index()
            if 0 <= row_index < len(self._flat_toc):
                title, href, depth = self._flat_toc[row_index]
                GLib.idle_add(lambda: self.navigate_to_toc_item(href))
        for title, href, depth in self._flat_toc:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hbox.set_margin_top(6)
            hbox.set_margin_bottom(6)
            hbox.set_margin_start(12 + (depth * 16))
            hbox.set_margin_end(12)
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(30)
            hbox.append(label)
            row.set_child(hbox)
            self.sidebar_listbox.append(row)
        self.sidebar_listbox.connect("row-activated", on_sidebar_row_activated)

    def on_decide_policy(self, webview, decision, decision_type):
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            uri = nav_action.get_request().get_uri()
            if (uri.startswith('http://') or uri.startswith('https://') or 
                uri.startswith('mailto:') or uri.startswith('ftp://')):
                decision.ignore()
                self.open_external_link(uri)
                return True
        return False

    def open_external_link(self, uri):
        try:
            import subprocess
            subprocess.run(['xdg-open', uri], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=f"External link: {uri}"
            )
            dialog.format_secondary_text("Could not open the external link automatically. You can copy and paste it in your browser.")
            dialog.connect("response", lambda d, r: d.destroy())
            dialog.show()

    def on_open_clicked(self, btn):
        dialog = Gtk.FileDialog()
        filter_all = Gtk.FileFilter(); filter_all.set_name("HTML / EPUB")
        filter_all.add_pattern("*.html"); filter_all.add_pattern("*.htm"); filter_all.add_pattern("*.epub")
        dialog.set_default_filter(filter_all)
        dialog.open(self, None, self.on_open_file_dialog_response)

    def on_open_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file: return
            basename = file.get_basename().lower()
            self._cleanup_epub_tempdir()
            self._clear_webview_history()
            if basename.endswith('.epub'):
                self.load_epub_with_ebooklib(file)
            else:
                file.load_contents_async(None, self.load_html_callback)
        except GLib.Error as e:
            print("Open error:", e.message)

    def load_html_callback(self, file, result):
        try:
            ok, content, _ = file.load_contents_finish(result)
            if ok:
                html = content.decode(errors="replace")
                base = file.get_uri() or "file:///"
                html = inject_css_into_html(html)
                self._epub_tempdir = None
                self._base_href = base
                self._flat_toc = []
                self._spine_hrefs = []
                self.sidebar_btn.set_sensitive(False)
                self.webview.load_html(html, base)
                GLib.timeout_add(500, lambda: self.apply_current_column_layout())
        except GLib.Error as e:
            print("Load error:", e.message)

    def load_epub_with_ebooklib(self, gio_file):
        path = gio_file.get_path()
        if not path:
            try:
                fd, tmp_epub = tempfile.mkstemp(suffix=".epub"); os.close(fd)
                stream = gio_file.read(None)
                with open(tmp_epub, "wb") as f: f.write(stream.read_bytes(stream.get_size()).get_data())
                path = tmp_epub
            except Exception:
                path = None
        if not path:
            print("Cannot access EPUB path"); return

        tempdir = tempfile.mkdtemp(prefix="epub_")
        try:
            book = epub.read_epub(path)
            self._book = book

            for item in book.get_items():
                fn = getattr(item, 'file_name', None)
                if not fn: continue
                full = os.path.join(tempdir, fn)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                content = item.get_content()
                with open(full, "wb") as f:
                    if isinstance(content, str): f.write(content.encode("utf-8"))
                    else: f.write(content)

            spine_hrefs = []
            for idref, _ in getattr(book, 'spine', []):
                try:
                    item = book.get_item_with_id(idref)
                except Exception:
                    item = None
                if item is None: continue
                href = getattr(item, 'file_name', None)
                if href: spine_hrefs.append(href)
            if not spine_hrefs:
                for item in book.get_items_of_type(epub.ITEM_DOCUMENT):
                    if getattr(item, 'file_name', None): spine_hrefs.append(item.file_name)
            if not spine_hrefs:
                print("No document items found in EPUB"); shutil.rmtree(tempdir, ignore_errors=True); return

            self._spine_hrefs = spine_hrefs

            bodies = []; head_html = None
            for i, rel in enumerate(spine_hrefs):
                full = os.path.join(tempdir, rel)
                if not os.path.exists(full): continue
                with open(full, "r", encoding="utf-8", errors="replace") as f: txt = f.read()
                if head_html is None:
                    m = re.search(r'(<head\b[^>]*>.*?</head>)', txt, re.S | re.I)
                    head_html = m.group(1) if m else ''
                file_dir = os.path.dirname(full)
                full_txt = txt
                rewritten = _rewrite_resource_urls(full_txt, file_dir, tempdir)
                m2r = re.search(r'<body\b[^>]*>(.*?)</body>', rewritten, re.S | re.I)
                body_content = m2r.group(1) if m2r else rewritten
                chapter_anchor = f'<div id="chapter_{i}" data-file="{rel}"></div>'
                bodies.append(chapter_anchor + body_content)

            final_head = head_html or '<meta charset="utf-8">'
            base_href = "file://" + tempdir.replace(os.sep, '/') + "/"
            concatenated = f"<!doctype html>\n<html>\n{final_head}\n<base href=\"{base_href}\">\n<body>\n" + "\n<hr/>\n".join(bodies) + "\n</body>\n</html>"
            concatenated = inject_css_into_html(concatenated)

            self._epub_tempdir = tempdir
            self._base_href = base_href
            self._flat_toc = []
            self.sidebar_btn.set_sensitive(False)

            self._clear_webview_history()
            self.webview.load_html(concatenated, base_href)

            toc = getattr(book, "toc", []) or []
            def walk_toc(entries, depth=0):
                if not entries: return
                if isinstance(entries, (list, tuple)):
                    for e in entries:
                        if isinstance(e, tuple) and len(e) >= 2 and isinstance(e[1], str):
                            title = str(e[0]); href = e[1]; self._flat_toc.append((title, href, depth))
                            if len(e) >= 3 and e[2]: walk_toc(e[2], depth+1)
                        else:
                            walk_toc(e, depth)
                    return
                if hasattr(entries, "href"):
                    title = getattr(entries, "title", None) or getattr(entries, "label", None) or str(entries)
                    href = getattr(entries, "href", "") or getattr(entries, "src", "")
                    self._flat_toc.append((title, href, depth))
                    children = getattr(entries, "children", None) or getattr(entries, "subitems", None) or None
                    if children: walk_toc(children, depth+1)
                    return
                try:
                    for child in entries: walk_toc(child, depth)
                except Exception: return

            walk_toc(toc)
            seen = set(); dedup = []
            for t, h, d in self._flat_toc:
                if not h: continue
                if h not in seen: dedup.append((t, h, d)); seen.add(h)
            self._flat_toc = dedup
            self.sidebar_btn.set_sensitive(bool(self._flat_toc))

            if self._flat_toc:
                self.populate_sidebar_toc()
                if not self._sidebar_visible:
                    self.toggle_sidebar()

            GLib.timeout_add(500, self._setup_internal_link_handling)
            GLib.timeout_add(700, lambda: self.apply_current_column_layout())

        except Exception as e:
            print("EPUB load error:", e)
            shutil.rmtree(tempdir, ignore_errors=True)

    def _setup_internal_link_handling(self):
        js_code = """
        (function() {
            function handleInternalLink(event) {
                const target = event.target.closest('a[href]');
                if (!target) return;
                const href = target.getAttribute('href');
                if (!href) return;
                if (!href.startsWith('http://') && !href.startsWith('https://') && !href.startsWith('mailto:')) {
                    event.preventDefault();
                    const parser = document.createElement('a');
                    parser.href = href;
                    const filePath = parser.pathname.split('/').pop();
                    const fragment = parser.hash.substring(1);
                    const chapterDivs = document.querySelectorAll('div[data-file]');
                    for (let i = 0; i < chapterDivs.length; i++) {
                        const chapterDiv = chapterDivs[i];
                        const dataFile = chapterDiv.getAttribute('data-file');
                        if (dataFile && dataFile.includes(filePath)) {
                            chapterDiv.scrollIntoView({behavior: 'smooth', block: 'start'});
                            if (fragment) {
                                setTimeout(() => {
                                    const targetEl = document.getElementById(fragment) || document.querySelector(`[name="${fragment}"]`);
                                    if (targetEl) { targetEl.scrollIntoView({behavior: 'smooth', block: 'start'}); }
                                }, 300);
                            }
                            break;
                        }
                    }
                }
            }
            document.addEventListener('click', handleInternalLink, true);
        })();
        """
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            return False
        except Exception:
            return True

    def _clear_webview_history(self):
        try:
            bfl = self.webview.get_back_forward_list()
            if bfl and hasattr(bfl, "clear"):
                bfl.clear()
            else:
                self.webview.load_html("<!doctype html><html><body></body></html>", "file:///")
        except Exception:
            try: self.webview.load_html("<!doctype html><html><body></body></html>", "file:///")
            except Exception: pass

    def navigate_to_toc_item(self, href):
        print(f"DEBUG: Navigating to TOC item: '{href}'")
        if not href:
            print("DEBUG: Empty href, returning")
            return
        parsed = urllib.parse.urlparse(href)
        filename = parsed.path
        fragment = parsed.fragment
        if filename.startswith('/'):
            filename = filename[1:]
        chapter_index = None
        for i, spine_href in enumerate(self._spine_hrefs):
            if spine_href == filename or spine_href.endswith('/' + filename) or filename.endswith(spine_href):
                chapter_index = i
                break
        if chapter_index is not None:
            if fragment:
                js_code = f"""
                (function() {{
                    let chapterEl = document.getElementById('chapter_{chapter_index}');
                    if (chapterEl) {{
                        let targetEl = document.getElementById('{fragment}');
                        if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                        else {{
                            targetEl = document.querySelector('[name="{fragment}"]');
                            if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                            else {{ chapterEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                        }}
                    }}
                }})();
                """
            else:
                js_code = f"""
                (function() {{
                    let chapterEl = document.getElementById('chapter_{chapter_index}');
                    if (chapterEl) {{ chapterEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                }})();
                """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception as e:
                print(f"DEBUG: JavaScript navigation error: {e}")
        else:
            if fragment:
                js_code = f"""
                (function() {{
                    let targetEl = document.getElementById('{fragment}');
                    if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                    else {{
                        targetEl = document.querySelector('[name="{fragment}"]');
                        if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                    }}
                }})();
                """
                try:
                    self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                except Exception as e:
                    print(f"DEBUG: Fragment navigation error: {e}")

    def on_font_family_changed(self, dropdown, *args):
        item = dropdown.get_selected_item()
        if not item: return
        font = item.get_string().replace("'", "\\'")
        css = f"* {{ font-family: '{font}' !important; }}"
        script = f"""
        (function() {{
            let s = document.getElementById('__font_override');
            if (!s) {{
                s = document.createElement('style');
                s.id = '__font_override';
                (document.head || document.documentElement).appendChild(s);
            }}
            s.textContent = {json.dumps(css)};
        }})();
        """
        try: self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception: pass

    def _cleanup_epub_tempdir(self):
        if self._epub_tempdir and os.path.exists(self._epub_tempdir):
            try: shutil.rmtree(self._epub_tempdir)
            except Exception: pass
        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self.sidebar_btn.set_sensitive(False)
        self._flat_toc = []
        self._spine_hrefs = []

    def on_close_request(self, *args):
        self._cleanup_epub_tempdir()
        return False

if __name__ == "__main__":
    import sys, signal
    Adw.init()
    app = Writer()
    try:
        app.run(None)
    except KeyboardInterrupt:
        # clean shutdown on Ctrl-C
        try: app.quit()
        except Exception: pass
        sys.exit(0)
