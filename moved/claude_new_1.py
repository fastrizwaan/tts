#!/usr/bin/env python3
"""
Advanced EPUB Viewer with Libadwaita
Supports EPUB2/3, columns, navigation, and comprehensive settings
Uses ebooklib for robust EPUB parsing

Dependencies:
    pip install ebooklib
    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adwaita-1 gir1.2-webkit-6.0
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')

from gi.repository import Gtk, Adw, WebKit, Gdk, GLib, Gio, GObject
import ebooklib
from ebooklib import epub
import json
from pathlib import Path
from urllib.parse import unquote
import mimetypes

class EPUBParser:
    """Parse EPUB2 and EPUB3 files using ebooklib"""
    
    def __init__(self, epub_path):
        self.epub_path = epub_path
        self.book = epub.read_epub(epub_path)
        self.spine = []
        self.toc = []
        self.metadata = {}
        
        self._parse()
    
    def _parse(self):
        """Parse EPUB structure"""
        self._parse_metadata()
        self._parse_spine()
        self._parse_toc()
    
    def _parse_metadata(self):
        """Extract metadata"""
        # Get title
        title = self.book.get_metadata('DC', 'title')
        self.metadata['title'] = title[0][0] if title else 'Unknown'
        
        # Get author
        creator = self.book.get_metadata('DC', 'creator')
        self.metadata['author'] = creator[0][0] if creator else 'Unknown'
    
    def _parse_spine(self):
        """Parse spine for reading order"""
        for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            self.spine.append({
                'href': item.get_name(),
                'media-type': 'application/xhtml+xml',
                'full_path': item.get_name()
            })
    
    def _parse_toc(self):
        """Parse table of contents"""
        def parse_toc_item(item, level=0):
            """Recursively parse TOC items"""
            if isinstance(item, tuple):
                # (Section, [children])
                section, children = item
                if hasattr(section, 'title') and hasattr(section, 'href'):
                    self.toc.append({
                        'label': section.title,
                        'href': section.href,
                        'level': level
                    })
                    # Parse children
                    for child in children:
                        parse_toc_item(child, level + 1)
            elif isinstance(item, epub.Link):
                # Direct link
                self.toc.append({
                    'label': item.title,
                    'href': item.href,
                    'level': level
                })
            elif isinstance(item, list):
                # List of items
                for subitem in item:
                    parse_toc_item(subitem, level)
        
        # Parse TOC
        for item in self.book.toc:
            parse_toc_item(item)
    
    def get_resource(self, path):
        """Get resource from EPUB"""
        # Try to get item by href
        item = self.book.get_item_with_href(path)
        if item:
            return item.get_content()
        
        # Try to get item by id
        item = self.book.get_item_with_id(path)
        if item:
            return item.get_content()
        
        # Try direct path matching
        for item in self.book.get_items():
            if item.get_name() == path:
                return item.get_content()
        
        return None
    
    def close(self):
        """Close EPUB file (no-op for ebooklib, kept for compatibility)"""
        pass


class Settings:
    """Application settings"""
    
    def __init__(self):
        self.config_dir = Path.home() / '.config' / 'epub-viewer'
        self.config_file = self.config_dir / 'settings.json'
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        self.defaults = {
            'font_family': 'serif',
            'font_size': 16,
            'margin_top': 20,
            'margin_bottom': 20,
            'margin_left': 40,
            'margin_right': 40,
            'column_count': 1,
            'column_width': 300,
            'scroll_mode': 'horizontal'  # 'horizontal' or 'vertical'
        }
        
        self.settings = self.load()
    
    def load(self):
        """Load settings from file"""
        if self.config_file.exists():
            try:
                with open(self.config_file) as f:
                    return {**self.defaults, **json.load(f)}
            except:
                pass
        return self.defaults.copy()
    
    def save(self):
        """Save settings to file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.settings, f, indent=2)
    
    def get(self, key):
        """Get setting value"""
        return self.settings.get(key, self.defaults.get(key))
    
    def set(self, key, value):
        """Set setting value"""
        self.settings[key] = value
        self.save()


class EPUBContentView(Gtk.Box):
    """WebKit-based content viewer with column support"""
    
    def __init__(self, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.settings = settings
        self.epub = None
        self.current_chapter = 0
        
        # Create WebKit view
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        # Configure WebKit settings for better rendering
        webkit_settings = self.webview.get_settings()
        webkit_settings.set_enable_javascript(True)
        webkit_settings.set_enable_page_cache(False)
        webkit_settings.set_allow_file_access_from_file_urls(True)
        webkit_settings.set_allow_universal_access_from_file_urls(True)
        
        # Enable scrolling
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.webview)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)  # Disable GTK scrolling
        self.append(scrolled)
        
        # Setup custom URI scheme for loading resources
        context = self.webview.get_context()
        context.register_uri_scheme("epub", self._handle_epub_uri)
        
        # Handle navigation
        self.webview.connect('decide-policy', self._on_decide_policy)
        
        # Wait for load to finish before allowing interaction
        self.webview.connect('load-changed', self._on_load_changed)
        
        # Keyboard shortcuts
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.webview.add_controller(key_controller)
        
        # Scroll controller for mouse wheel
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll_controller.connect('scroll', self._on_scroll)
        self.webview.add_controller(scroll_controller)
    
    def load_epub(self, epub_path):
        """Load an EPUB file"""
        if self.epub:
            self.epub.close()
        
        self.epub = EPUBParser(epub_path)
        self.current_chapter = 0
        self._load_chapter(0)
    
    def _load_chapter(self, index):
        """Load a specific chapter"""
        if not self.epub or index < 0 or index >= len(self.epub.spine):
            return
        
        self.current_chapter = index
        chapter = self.epub.spine[index]
        
        # Load HTML content
        content = self.epub.get_resource(chapter['full_path'])
        if content:
            html = content.decode('utf-8', errors='ignore')
            
            # Inject custom CSS for columns and styling
            html = self._inject_styles(html, chapter['full_path'])
            
            # Load via custom URI scheme
            base_uri = f"epub:///{chapter['full_path']}"
            self.webview.load_html(html, base_uri)
    
    def _inject_styles(self, html, base_path):
        """Inject custom CSS for column layout and styling"""
        font_family = {
            'serif': 'Georgia, serif',
            'sans': 'Arial, sans-serif',
            'monospace': 'Courier New, monospace'
        }[self.settings.get('font_family')]
        
        column_count = self.settings.get('column_count')
        column_width = self.settings.get('column_width')
        scroll_mode = self.settings.get('scroll_mode')
        margin_top = self.settings.get('margin_top')
        margin_bottom = self.settings.get('margin_bottom')
        margin_left = self.settings.get('margin_left')
        margin_right = self.settings.get('margin_right')
        
        # Calculate container height for columns
        # We want columns to fill the viewport height
        container_height = f"calc(100vh - {margin_top + margin_bottom}px)"
        
        css = f"""
        <style>
        * {{
            box-sizing: border-box;
        }}
        
        html, body {{
            margin: 0 !important;
            padding: 0 !important;
            width: 100% !important;
            height: 100% !important;
            overflow: hidden !important;
        }}
        
        body {{
            font-family: {font_family} !important;
            font-size: {self.settings.get('font_size')}px !important;
            line-height: 1.6 !important;
        }}
        
        #epub-container {{
            padding-top: {margin_top}px !important;
            padding-bottom: {margin_bottom}px !important;
            padding-left: {margin_left}px !important;
            padding-right: {margin_right}px !important;
            height: {container_height} !important;
            overflow-x: {'auto' if column_count > 1 or scroll_mode == 'horizontal' else 'hidden'} !important;
            overflow-y: {'hidden' if column_count > 1 or scroll_mode == 'horizontal' else 'auto'} !important;
            {'column-count: ' + str(column_count) + ' !important;' if column_count > 1 else ''}
            {'column-width: ' + str(column_width) + 'px !important;' if column_count > 1 else ''}
            {'column-gap: 40px !important;' if column_count > 1 else ''}
            {'column-fill: auto !important;' if column_count > 1 else ''}
            scroll-snap-type: {'x mandatory' if column_count > 1 or scroll_mode == 'horizontal' else 'none'} !important;
        }}
        
        #epub-container > * {{
            scroll-snap-align: start;
        }}
        
        img {{
            max-width: {'calc(' + str(column_width) + 'px - 20px)' if column_count > 1 else '100%'} !important;
            height: auto !important;
            display: block !important;
            margin: 10px auto !important;
        }}
        
        p, div, section {{
            break-inside: avoid-column;
        }}
        </style>
        """
        
        # Wrap content in container div
        # Extract body content if it exists
        body_match = html.find('<body')
        if body_match != -1:
            body_end = html.find('>', body_match)
            close_body = html.rfind('</body>')
            if body_end != -1 and close_body != -1:
                body_content = html[body_end + 1:close_body]
                before_body = html[:body_end + 1]
                after_body = html[close_body:]
                html = before_body + f'<div id="epub-container">{body_content}</div>' + after_body
        else:
            # No body tag, wrap everything
            html = f'<html><head>{css}</head><body><div id="epub-container">{html}</div></body></html>'
            return html
        
        # Insert CSS after <head> or at beginning
        if '<head>' in html:
            html = html.replace('<head>', f'<head>{css}')
        elif '<html>' in html:
            html = html.replace('<html>', f'<html><head>{css}</head>')
        else:
            html = f'<html><head>{css}</head><body><div id="epub-container">{html}</div></body></html>'
        
        return html
    
    def _handle_epub_uri(self, request):
        """Handle custom epub:// URI scheme"""
        uri = request.get_uri()
        path = unquote(uri.replace('epub:///', ''))
        
        # Get resource from EPUB
        content = self.epub.get_resource(path)
        if content:
            # Determine MIME type
            mime_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            
            # Create stream
            stream = Gio.MemoryInputStream.new_from_data(content)
            request.finish(stream, len(content), mime_type)
        else:
            request.finish_error(GLib.Error(f"Resource not found: {path}"))
    
    def _on_decide_policy(self, webview, decision, decision_type):
        """Handle navigation decisions"""
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            uri = nav_action.get_request().get_uri()
            
            # Handle internal links
            if uri.startswith('epub:///'):
                # Allow navigation
                return False
            elif uri.startswith('#'):
                # Fragment link - allow
                return False
            else:
                # External link - open in browser
                Gtk.show_uri(None, uri, Gdk.CURRENT_TIME)
                decision.ignore()
                return True
        
        return False
    
    def _on_load_changed(self, webview, load_event):
        """Handle load events"""
        if load_event == WebKit.LoadEvent.FINISHED:
            # Content is loaded, ensure proper setup
            js = """
            // Ensure container exists and is scrollable
            var container = document.getElementById('epub-container');
            if (container) {
                console.log('Container found, dimensions:', container.scrollWidth, 'x', container.scrollHeight);
            } else {
                console.error('Container not found!');
            }
            """
            self.webview.evaluate_javascript(js, -1, None, None, None)
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard navigation"""
        column_count = self.settings.get('column_count')
        
        if keyval == Gdk.KEY_Left:
            self._scroll_columns(-1)
            return True
        elif keyval == Gdk.KEY_Right:
            self._scroll_columns(1)
            return True
        elif keyval == Gdk.KEY_Page_Up:
            self._scroll_viewport(-1)
            return True
        elif keyval == Gdk.KEY_Page_Down:
            self._scroll_viewport(1)
            return True
        
        return False
    
    def _on_scroll(self, controller, dx, dy):
        """Handle mouse wheel scrolling"""
        column_count = self.settings.get('column_count')
        scroll_mode = self.settings.get('scroll_mode')
        
        # For multi-column or horizontal scroll mode, handle horizontal scrolling
        if column_count > 1 or scroll_mode == 'horizontal':
            # Scroll by column
            if dy > 0:
                self._scroll_columns(1)
            elif dy < 0:
                self._scroll_columns(-1)
            return True  # Event handled
        
        # For single column vertical mode, let default scrolling work
        return False
    
    def _scroll_columns(self, direction):
        """Scroll by one column"""
        column_width = self.settings.get('column_width')
        gap = 40  # column-gap
        scroll_amount = column_width + gap
        
        js = f"""
        var container = document.getElementById('epub-container');
        if (container) {{
            container.scrollBy({{
                left: {scroll_amount * direction},
                behavior: 'smooth'
            }});
        }}
        """
        self.webview.evaluate_javascript(js, -1, None, None, None)
    
    def _scroll_viewport(self, direction):
        """Scroll by viewport (multiple columns)"""
        column_count = self.settings.get('column_count')
        
        if column_count == 1:
            # Single column: scroll by one screen
            js = f"""
            var container = document.getElementById('epub-container');
            if (container) {{
                container.scrollBy({{
                    top: {direction} * container.clientHeight,
                    behavior: 'smooth'
                }});
            }}
            """
        else:
            # Multiple columns: scroll by visible columns
            column_width = self.settings.get('column_width')
            gap = 40
            
            js = f"""
            var container = document.getElementById('epub-container');
            if (container) {{
                var viewportWidth = container.clientWidth;
                var columnWidth = {column_width};
                var gap = {gap};
                var visibleColumns = Math.floor(viewportWidth / (columnWidth + gap));
                var scrollAmount = visibleColumns * (columnWidth + gap);
                
                container.scrollBy({{
                    left: {direction} * scrollAmount,
                    behavior: 'smooth'
                }});
            }}
            """
        
        self.webview.evaluate_javascript(js, -1, None, None, None)
    
    def navigate_to_href(self, href):
        """Navigate to a specific href (for TOC)"""
        if not self.epub:
            return
        
        # Find chapter containing this href
        for i, chapter in enumerate(self.epub.spine):
            if href.startswith(chapter['full_path']):
                self._load_chapter(i)
                
                # Handle fragment
                if '#' in href:
                    fragment = href.split('#')[1]
                    GLib.timeout_add(500, lambda: self._scroll_to_fragment(fragment))
                break
    
    def _scroll_to_fragment(self, fragment):
        """Scroll to a fragment identifier"""
        js = f"""
        var element = document.getElementById('{fragment}');
        if (element) {{
            element.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }}
        """
        self.webview.evaluate_javascript(js, -1, None, None, None)
        return False
    
    def apply_settings(self):
        """Reapply settings to current chapter"""
        if self.epub and self.current_chapter >= 0:
            # Store current scroll position if possible
            self._load_chapter(self.current_chapter)
            
            # Add a small delay to ensure content is rendered
            GLib.timeout_add(100, self._snap_to_column)
    
    def _snap_to_column(self):
        """Snap scroll position to nearest column"""
        column_count = self.settings.get('column_count')
        
        if column_count > 1:
            column_width = self.settings.get('column_width')
            gap = 40
            
            js = f"""
            var container = document.getElementById('epub-container');
            if (container) {{
                var columnWidth = {column_width};
                var gap = {gap};
                var fullColumnWidth = columnWidth + gap;
                var currentScroll = container.scrollLeft;
                var nearestColumn = Math.round(currentScroll / fullColumnWidth);
                container.scrollLeft = nearestColumn * fullColumnWidth;
            }}
            """
            self.webview.evaluate_javascript(js, -1, None, None, None)
        
        return False


class SettingsDialog(Adw.PreferencesWindow):
    """Settings dialog"""
    
    def __init__(self, settings, parent):
        super().__init__()
        self.settings = settings
        self.parent_window = parent
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Settings")
        
        # Font settings
        font_page = Adw.PreferencesPage()
        font_page.set_title("Font")
        font_page.set_icon_name("font-x-generic-symbolic")
        
        font_group = Adw.PreferencesGroup()
        font_group.set_title("Font Settings")
        
        # Font family
        font_row = Adw.ComboRow()
        font_row.set_title("Font Family")
        font_row.set_model(Gtk.StringList.new(['Serif', 'Sans', 'Monospace']))
        font_row.set_selected(['serif', 'sans', 'monospace'].index(settings.get('font_family')))
        font_row.connect('notify::selected', self._on_font_family_changed)
        font_group.add(font_row)
        
        # Font size
        font_size_row = Adw.SpinRow()
        font_size_row.set_title("Font Size")
        font_size_row.set_adjustment(Gtk.Adjustment(
            value=settings.get('font_size'),
            lower=8, upper=48, step_increment=1
        ))
        font_size_row.connect('notify::value', self._on_font_size_changed)
        font_group.add(font_size_row)
        
        font_page.add(font_group)
        self.add(font_page)
        
        # Layout settings
        layout_page = Adw.PreferencesPage()
        layout_page.set_title("Layout")
        layout_page.set_icon_name("view-paged-symbolic")
        
        layout_group = Adw.PreferencesGroup()
        layout_group.set_title("Layout Settings")
        
        # Column count
        column_row = Adw.SpinRow()
        column_row.set_title("Column Count")
        column_row.set_subtitle("Number of columns (1-10)")
        column_row.set_adjustment(Gtk.Adjustment(
            value=settings.get('column_count'),
            lower=1, upper=10, step_increment=1
        ))
        column_row.connect('notify::value', self._on_column_count_changed)
        layout_group.add(column_row)
        
        # Column width
        width_row = Adw.SpinRow()
        width_row.set_title("Column Width")
        width_row.set_subtitle("Width in pixels (50-500)")
        width_row.set_adjustment(Gtk.Adjustment(
            value=settings.get('column_width'),
            lower=50, upper=500, step_increment=10
        ))
        width_row.connect('notify::value', self._on_column_width_changed)
        layout_group.add(width_row)
        
        # Scroll mode (only for single column)
        scroll_row = Adw.ComboRow()
        scroll_row.set_title("Scroll Mode (1 Column)")
        scroll_row.set_subtitle("Horizontal or vertical scrolling")
        scroll_row.set_model(Gtk.StringList.new(['Horizontal', 'Vertical']))
        scroll_row.set_selected(0 if settings.get('scroll_mode') == 'horizontal' else 1)
        scroll_row.connect('notify::selected', self._on_scroll_mode_changed)
        layout_group.add(scroll_row)
        
        layout_page.add(layout_group)
        self.add(layout_page)
        
        # Margin settings
        margin_page = Adw.PreferencesPage()
        margin_page.set_title("Margins")
        margin_page.set_icon_name("preferences-desktop-display-symbolic")
        
        margin_group = Adw.PreferencesGroup()
        margin_group.set_title("Page Margins")
        
        for margin in ['top', 'bottom', 'left', 'right']:
            row = Adw.SpinRow()
            row.set_title(f"{margin.capitalize()} Margin")
            row.set_adjustment(Gtk.Adjustment(
                value=settings.get(f'margin_{margin}'),
                lower=0, upper=200, step_increment=5
            ))
            row.connect('notify::value', self._on_margin_changed, margin)
            margin_group.add(row)
        
        margin_page.add(margin_group)
        self.add(margin_page)
    
    def _on_font_family_changed(self, combo_row, _param):
        families = ['serif', 'sans', 'monospace']
        self.settings.set('font_family', families[combo_row.get_selected()])
        self.parent_window.content_view.apply_settings()
    
    def _on_font_size_changed(self, spin_row, _param):
        self.settings.set('font_size', int(spin_row.get_value()))
        self.parent_window.content_view.apply_settings()
    
    def _on_column_count_changed(self, spin_row, _param):
        self.settings.set('column_count', int(spin_row.get_value()))
        self.parent_window.content_view.apply_settings()
    
    def _on_column_width_changed(self, spin_row, _param):
        self.settings.set('column_width', int(spin_row.get_value()))
        self.parent_window.content_view.apply_settings()
    
    def _on_scroll_mode_changed(self, combo_row, _param):
        modes = ['horizontal', 'vertical']
        self.settings.set('scroll_mode', modes[combo_row.get_selected()])
        self.parent_window.content_view.apply_settings()
    
    def _on_margin_changed(self, spin_row, _param, margin):
        self.settings.set(f'margin_{margin}', int(spin_row.get_value()))
        self.parent_window.content_view.apply_settings()


class EPUBViewerWindow(Adw.ApplicationWindow):
    """Main application window"""
    
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)
        
        self.settings = Settings()
        
        # Create main layout
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_show_sidebar(True)
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_min_sidebar_width(250)
        self.split_view.set_max_sidebar_width(400)
        
        # Create sidebar with tabs
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Tab view for sidebar
        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar()
        self.tab_bar.set_view(self.tab_view)
        sidebar_box.append(self.tab_bar)
        
        tab_overview = Adw.TabOverview()
        tab_overview.set_view(self.tab_view)
        
        # TOC tab
        toc_page = Adw.TabPage()
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect('row-activated', self._on_toc_activated)
        
        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_child(self.toc_list)
        toc_scroll.set_vexpand(True)
        
        toc_page = self.tab_view.append(toc_scroll)
        toc_page.set_title("TOC")
        toc_page.set_icon(Gio.ThemedIcon.new("view-list-symbolic"))
        
        # Bookmarks tab
        bookmarks_label = Gtk.Label(label="Bookmarks\n(Not implemented)")
        bookmarks_label.set_valign(Gtk.Align.CENTER)
        bookmarks_page = self.tab_view.append(bookmarks_label)
        bookmarks_page.set_title("Bookmarks")
        bookmarks_page.set_icon(Gio.ThemedIcon.new("bookmark-symbolic"))
        
        # Annotations tab
        annotations_label = Gtk.Label(label="Annotations\n(Not implemented)")
        annotations_label.set_valign(Gtk.Align.CENTER)
        annotations_page = self.tab_view.append(annotations_label)
        annotations_page.set_title("Annotations")
        annotations_page.set_icon(Gio.ThemedIcon.new("document-edit-symbolic"))
        
        sidebar_box.append(self.tab_view)
        self.split_view.set_sidebar(sidebar_box)
        
        # Content view
        self.content_view = EPUBContentView(self.settings)
        self.split_view.set_content(self.content_view)
        
        # Header bar
        header = Adw.HeaderBar()
        
        # Open button
        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.connect('clicked', self._on_open_clicked)
        header.pack_start(open_button)
        
        # Settings button
        settings_button = Gtk.Button()
        settings_button.set_icon_name("preferences-system-symbolic")
        settings_button.set_tooltip_text("Settings")
        settings_button.connect('clicked', self._on_settings_clicked)
        header.pack_end(settings_button)
        
        # Toggle sidebar button
        sidebar_button = Gtk.ToggleButton()
        sidebar_button.set_icon_name("sidebar-show-symbolic")
        sidebar_button.set_active(True)
        sidebar_button.bind_property(
            'active', self.split_view, 'show-sidebar',
            GObject.BindingFlags.BIDIRECTIONAL
        )
        header.pack_end(sidebar_button)
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(header)
        main_box.append(self.split_view)
        
        self.set_content(main_box)
    
    def _on_open_clicked(self, button):
        """Open EPUB file"""
        dialog = Gtk.FileDialog()
        
        # Create filter for EPUB files
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        dialog.set_filters(filters)
        
        dialog.open(self, None, self._on_file_selected)
    
    def _on_file_selected(self, dialog, result):
        """Handle file selection"""
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                self.content_view.load_epub(path)
                self._populate_toc()
        except Exception as e:
            print(f"Error opening file: {e}")
    
    def _populate_toc(self):
        """Populate table of contents"""
        # Clear existing
        while True:
            row = self.toc_list.get_row_at_index(0)
            if row:
                self.toc_list.remove(row)
            else:
                break
        
        # Add TOC entries
        if self.content_view.epub:
            for entry in self.content_view.epub.toc:
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_margin_start(entry['level'] * 20)
                box.set_margin_top(4)
                box.set_margin_bottom(4)
                
                label = Gtk.Label(label=entry['label'])
                label.set_xalign(0)
                label.set_wrap(True)
                box.append(label)
                
                row = Gtk.ListBoxRow()
                row.set_child(box)
                row.entry = entry
                self.toc_list.append(row)
    
    def _on_toc_activated(self, list_box, row):
        """Handle TOC selection"""
        if hasattr(row, 'entry'):
            self.content_view.navigate_to_href(row.entry['href'])
    
    def _on_settings_clicked(self, button):
        """Show settings dialog"""
        dialog = SettingsDialog(self.settings, self)
        dialog.present()


class EPUBViewerApp(Adw.Application):
    """Main application"""
    
    def __init__(self):
        super().__init__(application_id='org.example.epubviewer',
                        flags=Gio.ApplicationFlags.FLAGS_NONE)
    
    def do_activate(self):
        """Activate application"""
        win = self.props.active_window
        if not win:
            win = EPUBViewerWindow(self)
        win.present()


if __name__ == '__main__':
    import sys
    app = EPUBViewerApp()
    sys.exit(app.run(sys.argv))
