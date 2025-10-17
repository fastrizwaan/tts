#!/usr/bin/env python3
"""
Simple EPUB Viewer with Libadwaita
Basic EPUB reading with TOC navigation
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')

from gi.repository import Gtk, Adw, WebKit, Gdk, GLib, Gio, GObject
import zipfile
import os
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import unquote
import mimetypes

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")


class EPUBParser:
    """Parse EPUB2 and EPUB3 files"""
    
    def __init__(self, epub_path):
        self.epub_path = epub_path
        self.zip_file = zipfile.ZipFile(epub_path)
        self.opf_path = None
        self.opf_root = None
        self.content_dir = None
        self.spine = []
        self.toc = []
        self.manifest = {}
        self.metadata = {}
        
        self._parse()
    
    def _parse(self):
        """Parse EPUB structure"""
        # Find OPF file from container.xml
        container = self.zip_file.read('META-INF/container.xml')
        container_root = ET.fromstring(container)
        
        # Handle namespaces
        ns = {'cnt': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        rootfile = container_root.find('.//cnt:rootfile', ns)
        self.opf_path = rootfile.get('full-path')
        self.content_dir = str(Path(self.opf_path).parent)
        
        # Parse OPF
        opf_content = self.zip_file.read(self.opf_path)
        self.opf_root = ET.fromstring(opf_content)
        
        # Get namespace
        self.ns = {'opf': 'http://www.idpf.org/2007/opf',
                   'dc': 'http://purl.org/dc/elements/1.1/'}
        
        self._parse_metadata()
        self._parse_manifest()
        self._parse_spine()
        self._parse_toc()
    
    def _parse_metadata(self):
        """Extract metadata"""
        metadata = self.opf_root.find('opf:metadata', self.ns)
        if metadata is not None:
            title = metadata.find('dc:title', self.ns)
            self.metadata['title'] = title.text if title is not None else 'Unknown'
            
            creator = metadata.find('dc:creator', self.ns)
            self.metadata['author'] = creator.text if creator is not None else 'Unknown'
    
    def _parse_manifest(self):
        """Parse manifest for all resources"""
        manifest = self.opf_root.find('opf:manifest', self.ns)
        for item in manifest.findall('opf:item', self.ns):
            item_id = item.get('id')
            href = item.get('href')
            media_type = item.get('media-type')
            self.manifest[item_id] = {
                'href': href,
                'media-type': media_type,
                'full_path': self._get_full_path(href)
            }
    
    def _parse_spine(self):
        """Parse spine for reading order"""
        spine = self.opf_root.find('opf:spine', self.ns)
        for itemref in spine.findall('opf:itemref', self.ns):
            idref = itemref.get('idref')
            if idref in self.manifest:
                self.spine.append(self.manifest[idref])
    
    def _parse_toc(self):
        """Parse table of contents"""
        # Try NCX first (EPUB2)
        for item_id, item in self.manifest.items():
            if item['media-type'] == 'application/x-dtbncx+xml':
                self._parse_ncx_toc(item['full_path'])
                return
        
        # Try nav document (EPUB3)
        for item_id, item in self.manifest.items():
            if 'nav' in item.get('properties', ''):
                self._parse_nav_toc(item['full_path'])
                return
    
    def _parse_ncx_toc(self, ncx_path):
        """Parse NCX table of contents (EPUB2)"""
        try:
            ncx_content = self.zip_file.read(ncx_path)
            ncx_root = ET.fromstring(ncx_content)
            ns = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
            
            def parse_navpoint(navpoint, level=0):
                label = navpoint.find('.//ncx:text', ns)
                content = navpoint.find('ncx:content', ns)
                
                if label is not None and content is not None:
                    href = content.get('src')
                    self.toc.append({
                        'label': label.text,
                        'href': self._get_full_path(href),
                        'level': level
                    })
                
                for child in navpoint.findall('ncx:navPoint', ns):
                    parse_navpoint(child, level + 1)
            
            nav_map = ncx_root.find('.//ncx:navMap', ns)
            if nav_map is not None:
                for navpoint in nav_map.findall('ncx:navPoint', ns):
                    parse_navpoint(navpoint)
        except Exception as e:
            print(f"Error parsing NCX: {e}")
    
    def _parse_nav_toc(self, nav_path):
        """Parse nav document (EPUB3)"""
        try:
            nav_content = self.zip_file.read(nav_path)
            nav_root = ET.fromstring(nav_content)
            
            # Find nav element with toc
            nav_elem = nav_root.find('.//{http://www.w3.org/1999/xhtml}nav[@*="toc"]')
            if nav_elem is None:
                nav_elem = nav_root.find('.//{http://www.w3.org/1999/xhtml}nav')
            
            if nav_elem is not None:
                def parse_nav_list(ol, level=0):
                    for li in ol.findall('.//{http://www.w3.org/1999/xhtml}li'):
                        a = li.find('.//{http://www.w3.org/1999/xhtml}a')
                        if a is not None and a.get('href'):
                            self.toc.append({
                                'label': ''.join(a.itertext()).strip(),
                                'href': self._get_full_path(a.get('href')),
                                'level': level
                            })
                
                ol = nav_elem.find('.//{http://www.w3.org/1999/xhtml}ol')
                if ol is not None:
                    parse_nav_list(ol)
        except Exception as e:
            print(f"Error parsing nav: {e}")
    
    def _get_full_path(self, href):
        """Get full path within ZIP"""
        if self.content_dir:
            return str(Path(self.content_dir) / href)
        return href
    
    def get_resource(self, path):
        """Get resource from EPUB"""
        try:
            return self.zip_file.read(path)
        except:
            return None
    
    def close(self):
        """Close EPUB file"""
        self.zip_file.close()


class EPUBContentView(Gtk.Box):
    """WebKit-based content viewer"""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.epub = None
        self.current_chapter = 0
        self.chapter_changed_callback = None
        self.scroll_changed_callback = None
        self.scroll_check_timeout = None
        
        # Create WebKit view
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        # Configure WebKit settings
        webkit_settings = self.webview.get_settings()
        webkit_settings.set_enable_javascript(True)
        webkit_settings.set_enable_page_cache(False)
        webkit_settings.set_allow_file_access_from_file_urls(True)
        webkit_settings.set_allow_universal_access_from_file_urls(True)
        
        # Add to scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.webview)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
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
            
            # Add basic styling
            html = self._add_basic_styles(html)
            
            # Load via custom URI scheme
            base_uri = f"epub:///{chapter['full_path']}"
            self.webview.load_html(html, base_uri)
            
            # Notify chapter changed
            if self.chapter_changed_callback:
                self.chapter_changed_callback(chapter['full_path'])
    
    def _add_basic_styles(self, html):
        """Add basic CSS for better readability"""
        css = """
        <style>
        body {
            max-width: 800px;
            margin: 20px auto;
            padding: 20px;
            font-family: Georgia, serif;
            font-size: 18px;
            line-height: 1.6;
        }
        img {
            max-width: 100%;
            height: auto;
            display: block;
            margin: 10px auto;
        }
        </style>
        """
        
        # Insert CSS
        if '<head>' in html:
            html = html.replace('<head>', f'<head>{css}')
        elif '<html>' in html:
            html = html.replace('<html>', f'<html><head>{css}</head>')
        else:
            html = f'<html><head>{css}</head><body>{html}</body></html>'
        
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
            
            # Handle internal EPUB links
            if uri.startswith('epub:///'):
                # Extract the path
                path = unquote(uri.replace('epub:///', ''))
                
                # Split path and fragment
                if '#' in path:
                    file_path, fragment = path.split('#', 1)
                else:
                    file_path = path
                    fragment = None
                
                # Check if it's the same chapter
                current_chapter = self.epub.spine[self.current_chapter]
                if file_path == current_chapter['full_path']:
                    # Same chapter, just scroll to fragment
                    if fragment:
                        decision.ignore()
                        GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                        return True
                    return False
                else:
                    # Different chapter, load it
                    decision.ignore()
                    self.navigate_to_href(path)
                    return True
            elif uri.startswith('#'):
                # Fragment-only link (same page)
                fragment = uri[1:]
                decision.ignore()
                GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                return True
            else:
                # External link - open in browser
                Gtk.show_uri(None, uri, Gdk.CURRENT_TIME)
                decision.ignore()
                return True
        
        return False
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard navigation"""
        if keyval == Gdk.KEY_Left or keyval == Gdk.KEY_Page_Up:
            self.previous_chapter()
            return True
        elif keyval == Gdk.KEY_Right or keyval == Gdk.KEY_Page_Down:
            self.next_chapter()
            return True
        
        return False
    
    def previous_chapter(self):
        """Go to previous chapter"""
        if self.current_chapter > 0:
            self._load_chapter(self.current_chapter - 1)
    
    def next_chapter(self):
        """Go to next chapter"""
        if self.current_chapter < len(self.epub.spine) - 1:
            self._load_chapter(self.current_chapter + 1)
    
    def navigate_to_href(self, href):
        """Navigate to a specific href (for TOC)"""
        if not self.epub:
            return
        
        # Find chapter containing this href
        for i, chapter in enumerate(self.epub.spine):
            if href.startswith(chapter['full_path']):
                # Check if it's the same chapter
                if i == self.current_chapter and '#' in href:
                    # Same chapter, just scroll to fragment
                    fragment = href.split('#')[1]
                    self._scroll_to_fragment(fragment)
                else:
                    # Different chapter, load it
                    self._load_chapter(i)
                    
                    # Handle fragment after load
                    if '#' in href:
                        fragment = href.split('#')[1]
                        GLib.timeout_add(500, lambda: self._scroll_to_fragment_and_track(fragment))
                break
    
    def _scroll_to_fragment_and_track(self, fragment):
        """Scroll to fragment and ensure tracking is updated"""
        js = f"""
        var element = document.getElementById('{fragment}');
        if (element) {{
            element.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }}
        """
        self.webview.evaluate_javascript(js, -1, None, None, None)
        # Trigger an immediate scroll check after scrolling
        GLib.timeout_add(600, self._check_scroll_position)
        return False
    
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
    
    def _on_load_changed(self, webview, load_event):
        """Handle page load events"""
        if load_event == WebKit.LoadEvent.FINISHED:
            # Set up scroll tracking
            self._setup_scroll_tracking()
    
    def _setup_scroll_tracking(self):
        """Set up JavaScript to track scrolling and visible sections"""
        js = """
        (function() {
            // Track all elements with IDs (potential scroll targets)
            window.epubSections = [];
            var elements = document.querySelectorAll('[id]');
            
            for (var i = 0; i < elements.length; i++) {
                var el = elements[i];
                var rect = el.getBoundingClientRect();
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                window.epubSections.push({
                    id: el.id,
                    top: rect.top + scrollTop
                });
            }
            
            window.epubSections.sort(function(a, b) { return a.top - b.top; });
            
            console.log('Found ' + window.epubSections.length + ' sections');
            
            // Function to get currently visible section
            window.getCurrentSection = function() {
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var viewportHeight = window.innerHeight;
                var threshold = scrollTop + 100; // Top 100px of viewport
                
                var currentSection = '';
                for (var i = 0; i < window.epubSections.length; i++) {
                    if (window.epubSections[i].top <= threshold) {
                        currentSection = window.epubSections[i].id;
                    } else {
                        break;
                    }
                }
                
                return currentSection;
            };
            
            return true;
        })();
        """
        self.webview.evaluate_javascript(js, -1, None, None, None)
        
        # Start periodic scroll checking
        if self.scroll_check_timeout:
            GLib.source_remove(self.scroll_check_timeout)
        self.scroll_check_timeout = GLib.timeout_add(300, self._check_scroll_position)
    
    def _check_scroll_position(self):
        """Periodically check scroll position and update TOC"""
        if not self.epub:
            return False
        
        js = """
        (function() {
            if (typeof window.getCurrentSection === 'function') {
                return window.getCurrentSection();
            }
            return '';
        })();
        """
        self.webview.evaluate_javascript(
            js, -1, None, None, None,
            self._on_scroll_section_result
        )
        return True  # Continue timeout
    
    def _on_scroll_section_result(self, webview, result, user_data):
        """Handle the result of scroll position check"""
        try:
            js_result = webview.evaluate_javascript_finish(result)
            if js_result:
                value = js_result.to_string()
                if value and value != '':
                    section_id = value
                    if section_id and self.scroll_changed_callback:
                        # Get current chapter path and append fragment
                        current_chapter = self.epub.spine[self.current_chapter]
                        full_path = f"{current_chapter['full_path']}#{section_id}"
                        self.scroll_changed_callback(full_path)
        except Exception as e:
            # Ignore JavaScript evaluation errors
            pass


class EPUBViewerWindow(Adw.ApplicationWindow):
    """Main application window"""
    
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)
        
        # TOC row mapping for highlighting
        self.toc_row_map = {}
        self.programmatic_toc_selection = False
        
        # Create main layout with split view
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_show_sidebar(True)
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_min_sidebar_width(250)
        self.split_view.set_max_sidebar_width(400)
        
        # Create TOC sidebar
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # TOC header
        toc_header = Gtk.Label(label="Table of Contents")
        toc_header.add_css_class("title-2")
        toc_header.set_margin_top(12)
        toc_header.set_margin_bottom(12)
        sidebar_box.append(toc_header)
        
        # TOC list
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect('row-activated', self._on_toc_activated)
        
        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_child(self.toc_list)
        toc_scroll.set_vexpand(True)
        sidebar_box.append(toc_scroll)
        
        self.split_view.set_sidebar(sidebar_box)
        
        # Content view
        self.content_view = EPUBContentView()
        self.content_view.chapter_changed_callback = self._on_chapter_changed
        self.content_view.scroll_changed_callback = self._on_scroll_changed
        self.split_view.set_content(self.content_view)
        
        # Header bar
        header = Adw.HeaderBar()
        
        # Open button
        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.connect('clicked', self._on_open_clicked)
        header.pack_start(open_button)
        
        # Toggle sidebar button
        sidebar_button = Gtk.ToggleButton()
        sidebar_button.set_icon_name("sidebar-show-symbolic")
        sidebar_button.set_active(True)
        sidebar_button.bind_property(
            'active', self.split_view, 'show-sidebar',
            GObject.BindingFlags.BIDIRECTIONAL
        )
        header.pack_end(sidebar_button)
        
        # Navigation buttons
        prev_button = Gtk.Button()
        prev_button.set_icon_name("go-previous-symbolic")
        prev_button.set_tooltip_text("Previous Chapter")
        prev_button.connect('clicked', self._on_prev_chapter)
        header.pack_end(prev_button)
        
        next_button = Gtk.Button()
        next_button.set_icon_name("go-next-symbolic")
        next_button.set_tooltip_text("Next Chapter")
        next_button.connect('clicked', self._on_next_chapter)
        header.pack_end(next_button)
        
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
                
                # Update title with book name
                if self.content_view.epub:
                    title = self.content_view.epub.metadata.get('title', 'Unknown')
                    self.set_title(f"{title} - EPUB Viewer")
        except Exception as e:
            print(f"Error opening file: {e}")
    
    def _populate_toc(self):
        """Populate table of contents"""
        # Clear existing
        self.toc_row_map.clear()
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
                box.set_margin_start(entry['level'] * 20 + 6)
                box.set_margin_top(4)
                box.set_margin_bottom(4)
                box.set_margin_end(6)
                
                label = Gtk.Label(label=entry['label'])
                label.set_xalign(0)
                label.set_wrap(True)
                box.append(label)
                
                row = Gtk.ListBoxRow()
                row.set_child(box)
                row.entry = entry
                self.toc_list.append(row)
                
                # Store mapping with full path (including fragment if present)
                self.toc_row_map[entry['href']] = row
                
                # Also store chapter-only path as fallback
                chapter_path = entry['href'].split('#')[0]
                if chapter_path not in self.toc_row_map:
                    self.toc_row_map[chapter_path] = row
    
    def _on_chapter_changed(self, chapter_path):
        """Highlight the current chapter in TOC"""
        # Don't update if we're in the middle of a programmatic TOC selection
        if self.programmatic_toc_selection:
            return
            
        # Find the TOC row for this chapter
        if chapter_path in self.toc_row_map:
            row = self.toc_row_map[chapter_path]
            self.toc_list.select_row(row)
            # Scroll to make the selected row visible
            GLib.idle_add(lambda: self._scroll_to_row(row))
    
    def _on_scroll_changed(self, full_path):
        """Update TOC highlighting based on scroll position"""
        # Don't update if we're in the middle of a programmatic TOC selection
        if self.programmatic_toc_selection:
            return
            
        # Try to find exact match with fragment
        if full_path in self.toc_row_map:
            row = self.toc_row_map[full_path]
            if self.toc_list.get_selected_row() != row:
                self.toc_list.select_row(row)
                GLib.idle_add(lambda: self._scroll_to_row(row))
        else:
            # Fall back to chapter-only match
            chapter_path = full_path.split('#')[0]
            if chapter_path in self.toc_row_map:
                row = self.toc_row_map[chapter_path]
                if self.toc_list.get_selected_row() != row:
                    self.toc_list.select_row(row)
                    GLib.idle_add(lambda: self._scroll_to_row(row))
    
    def _enable_scroll_tracking(self):
        """Re-enable scroll tracking after programmatic navigation"""
        self.programmatic_toc_selection = False
        return False
    
    def _on_prev_chapter(self, button):
        """Handle previous chapter button"""
        self.programmatic_toc_selection = True
        self.content_view.previous_chapter()
        GLib.timeout_add(1000, self._enable_scroll_tracking)
    
    def _on_next_chapter(self, button):
        """Handle next chapter button"""
        self.programmatic_toc_selection = True
        self.content_view.next_chapter()
        GLib.timeout_add(1000, self._enable_scroll_tracking)
    
    def _scroll_to_row(self, row):
        """Scroll the TOC to make a row visible"""
        # Get the adjustment from the scrolled window
        parent = row.get_parent()
        while parent and not isinstance(parent, Gtk.ScrolledWindow):
            parent = parent.get_parent()
        
        if parent:
            vadjustment = parent.get_vadjustment()
            # Get row allocation
            allocation = row.get_allocation()
            row_y = allocation.y
            row_height = allocation.height
            
            # Scroll to center the row
            viewport_height = vadjustment.get_page_size()
            target_value = row_y - (viewport_height / 2) + (row_height / 2)
            target_value = max(0, min(target_value, vadjustment.get_upper() - viewport_height))
            vadjustment.set_value(target_value)
        
        return False
    
    def _on_toc_activated(self, list_box, row):
        """Handle TOC selection"""
        if hasattr(row, 'entry'):
            # Temporarily disable scroll tracking updates
            self.programmatic_toc_selection = True
            self.content_view.navigate_to_href(row.entry['href'])
            # Re-enable after navigation
            GLib.timeout_add(1000, self._enable_scroll_tracking)


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
