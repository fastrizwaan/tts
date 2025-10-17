#!/usr/bin/env python3
"""
EPUB & HTML Viewer with Multi-Column Layout

Enhanced EPUB loading features:
- Uses ebooklib's native API for cleaner, more efficient resource loading
- Direct access to book.toc for table of contents
- Efficient resource lookup with get_item_with_href()
- Simplified spine (reading order) handling
- Better error handling and fallback mechanisms
- Removed unnecessary temp directory extraction
- Optimized image/CSS embedding with pattern matching
"""

import os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, WebKit, GLib, Gdk, Pango
import re
from ebooklib import epub
import base64

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

_FOLIATE_CSS = b"""
.sidebar-toc { background-color: @surface; padding-top: 6px; padding-bottom: 6px; }
.toc-contents-label { padding-left: 12px; padding-right: 12px; padding-bottom: 6px; font-weight: 600; }
.toc-expander-row {
  min-height: 30px;
  padding-top: 4px;
  padding-left: 10px;
  padding-bottom: 4px;
  border-radius: 10px;
}
.toc-leaf {
  min-height: 30px;
  border-radius: 8px;
  margin-right: 4px;
  padding-left: 20px;
  margin-left: 0px;
  padding-top: 4px;
  padding-bottom: 4px;
}
.toc-chev { margin-left: 2px; margin-right: 8px; }
.adw-action-row:hover { background-color: rgba(0,0,0,0.03); }
.toc-active { background-color: rgba(20, 80, 160, 0.08); border-radius: 6px; }
"""

# Define constants manually if not available
try:
    from ebooklib import ITEM_DOCUMENT, ITEM_STYLE, ITEM_IMAGE
except ImportError:
    # Fallback: use integer values as defined by ebooklib
    ITEM_DOCUMENT = 9
    ITEM_STYLE = 3
    ITEM_IMAGE = 2

class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Demo")
        self.set_default_size(1100, 720)
        
        # Apply CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(_FOLIATE_CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        
        # EPUB-related attributes
        self.book = None
        self.book_path = None
        self.spine = []  # Reading order
        self._row_map = {}
        self._active_href = None
        self.is_epub_mode = False
        self.original_html_content = "<h1>Welcome</h1><p>Select an HTML file or EPUB to view.</p>"
        
        # Split view
        self.split = Adw.OverlaySplitView(show_sidebar=False)
        self.set_content(self.split)
        
        # Sidebar with TOC
        self._toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        try:
            self._toc_box.add_css_class("sidebar-toc")
        except Exception:
            pass
        self._toc_box.set_margin_top(6)
        self._toc_box.set_margin_bottom(6)
        self._toc_box.set_margin_start(6)
        self._toc_box.set_margin_end(6)
        
        self._toc_scroller = Gtk.ScrolledWindow()
        try:
            self._toc_scroller.set_min_content_width(320)
        except Exception:
            pass
        self._toc_scroller.set_child(self._toc_box)
        self.split.set_sidebar(self._toc_scroller)
        
        # Content
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        
        # Add toggle sidebar button to header
        toggle_sidebar_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        toggle_sidebar_btn.connect("clicked", self.on_toggle_sidebar)
        header.pack_start(toggle_sidebar_btn)
        
        # Add open HTML file button to header
        open_html_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_html_btn.set_tooltip_text("Open HTML File")
        open_html_btn.connect("clicked", self.on_open_html_file)
        header.pack_start(open_html_btn)
        
        # Add open EPUB button to header
        open_epub_btn = Gtk.Button(icon_name="book-open-symbolic")
        open_epub_btn.set_tooltip_text("Open EPUB File")
        open_epub_btn.connect("clicked", self.on_open_epub_file)
        header.pack_start(open_epub_btn)
        
        # Add columns dropdown to header using StringList model
        string_list = Gtk.StringList()
        for i in range(1, 11):
            string_list.append(f"{i} Columns")
        
        self.columns_combo = Gtk.DropDown(model=string_list, selected=1)
        self.columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(self.columns_combo)
        
        header.set_title_widget(Gtk.Label(label="Demo - EPUB & HTML Viewer"))
        toolbar.add_top_bar(header)
        
        # Create WebView for content area
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        # Set up message handler to receive scrollevent from JavaScript
        content_manager = self.webview.get_user_content_manager()
        content_manager.connect("script-message-received::scrollEvent", self.on_scroll_event_received)
        content_manager.register_script_message_handler("scrollEvent")
        
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an HTML file or EPUB to view.</p></body></html>")
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.webview.set_margin_top(10)
        self.webview.set_margin_bottom(10)
        self.webview.set_margin_start(10)
        self.webview.set_margin_end(10)
        content_box.append(self.webview)
        
        toolbar.set_content(content_box)
        
        self.split.set_content(toolbar)
        
        # Store current columns and connect to size allocation
        self.current_columns = 2
        self.columns_combo.set_selected(self.current_columns - 1)
        # Apply default column layout after window initializes
        GLib.idle_add(lambda: self.apply_column_layout(self.current_columns - 1))

        self.pending_column_change = None
        self.connect("notify::default-width", self.on_size_changed)
        self.connect("notify::default-height", self.on_size_changed)
        
        # Breakpoint for responsive sidebar
        self.breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 768px")
        )
        self.breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.breakpoint)
    
    def _clear_container(self, container):
        child = container.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            try:
                container.remove(child)
            except Exception:
                pass
            child = next_child
    
    def _set_active(self, href):
        if self._active_href == href:
            return
        prev = self._row_map.get(self._active_href)
        if prev:
            try:
                prev.remove_css_class("toc-active")
            except Exception:
                pass
        w = self._row_map.get(href)
        if w:
            try:
                w.add_css_class("toc-active")
            except Exception:
                pass
            try:
                self._toc_scroller.scroll_to_child(w, 0.0, True, 0.0, 0.0)
            except Exception:
                pass
            self._active_href = href
    
    def on_toggle_sidebar(self, button):
        # Toggle sidebar
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        # Wait for sidebar animation, then reapply layout with position restore
        # Always reapply layout now, regardless of mode
        GLib.timeout_add(350, lambda: self.apply_column_layout(self.current_columns - 1, restore_position=True))
    
    def on_scroll_event_received(self, content_manager, js_result):
        """Callback when scroll event info is sent from JavaScript"""
        try:
            import json
            event_data = json.loads(js_result.to_string())
            event_type = event_data.get('type', 'unknown')
            scroll_x = event_data.get('scrollX', 0)
            scroll_y = event_data.get('scrollY', 0)
            column = event_data.get('column', 0)
            
            # Create emoji/icon for different event types
            icons = {
                'wheel': '🖱️ ',
                'wheel-y': '↕️ ',
                'arrow-left': '⬅️ ',
                'arrow-right': '➡️',
                'page-up': '⬆️ ',
                'page-down': '⬇️',
                'home': '🏠',
                'end': '🔚'
            }
            icon = icons.get(event_type, '📜')
            
            if event_type.startswith('wheel'):
                print(f"{icon} Scroll Event: {event_type:12s} | ScrollY: {scroll_y:5.0f}")
            else:
                print(f"{icon} Scroll Event: {event_type:12s} | ScrollX: {scroll_x:5.0f} | Column: {column}")
        except Exception as e:
            print(f"Error receiving scroll event: {e}")
    
    def on_open_html_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open HTML File")
        
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
        
        dialog.open(self, None, self.on_html_file_dialog_response)
    
    def on_html_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                def load_file_in_thread():
                    try:
                        content_bytes = file.load_bytes(None)[0]
                        content = content_bytes.get_data().decode('utf-8')
                        self.original_html_content = content
                        self.is_epub_mode = False
                        self.book = None
                        self._clear_container(self._toc_box)
                        GLib.idle_add(lambda: (
                            self.split.set_show_sidebar(False),
                            self.apply_column_layout(self.current_columns - 1)
                        ))
                    except Exception as e:
                        print(f"Error reading file: {e}")
                        GLib.idle_add(lambda: self.show_error_dialog(f"Error loading file: {e}"))
                
                GLib.Thread.new(None, load_file_in_thread)
        except GLib.Error:
            pass
    
    def on_open_epub_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open EPUB File")
        
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        filters.append(all_filter)
        dialog.set_filters(filters)
        
        dialog.open(self, None, self.on_epub_file_dialog_response)
    
    def on_epub_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                if path:
                    self.load_epub(path)
        except GLib.Error:
            pass
    
    def _parse_nav_toc_from_string(self, html_text):
        safe = re.sub(r'&(?!#?\w+;)', '&amp;', html_text)
        m = re.search(r'(<nav\b[^>]*>.*?</nav>)', safe, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        nav_html = m.group(1)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(f"<root>{nav_html}</root>")
        except Exception:
            try:
                root = ET.fromstring(nav_html)
            except Exception:
                return None

        def strip_ns(tag):
            return tag.split("}")[-1].lower() if isinstance(tag, str) else ""

        list_elem = None
        for el in root.iter():
            if strip_ns(el.tag) in ("ol", "ul"):
                list_elem = el
                break
        if list_elem is None:
            return None

        def parse_list(el):
            nodes = []
            for li in el:
                if strip_ns(li.tag) != "li":
                    continue
                a = None
                for child in li:
                    if strip_ns(child.tag) == "a":
                        a = child
                        break
                title = ""
                href = None
                if a is not None:
                    title = "".join(a.itertext()).strip()
                    href = a.attrib.get("href")
                else:
                    title = "".join(li.itertext()).strip()
                sub = None
                for child in li:
                    if strip_ns(child.tag) in ("ol", "ul"):
                        sub = child
                        break
                children = parse_list(sub) if sub is not None else []
                nodes.append({"title": title or None, "href": href, "children": children})
            return nodes

        toc = parse_list(list_elem)
        return toc if toc else None
    
    def load_epub(self, path):
        """Load EPUB file using simplified ebooklib API"""
        try:
            self.book = epub.read_epub(path)
            self.book_path = path
            self.is_epub_mode = True
            print(f"\n=== Loading EPUB: {os.path.basename(path)} ===")
        except Exception as e:
            self.show_error_dialog(f"Failed to read EPUB: {e}")
            return

        # Get spine (reading order) - much simpler!
        self.spine = []
        try:
            for item in self.book.get_items_of_type(ITEM_DOCUMENT):
                self.spine.append({
                    'href': item.get_name(),
                    'item': item
                })
            print(f"Loaded {len(self.spine)} spine items")
        except Exception as e:
            print(f"Error loading spine: {e}")
            self.spine = []
        
        # Build lookup map for quick resource access
        self.item_map = {item.get_name(): item for item in self.book.get_items()}
        print(f"Built item_map with {len(self.item_map)} items")
        
        # Print some item names for debugging
        print("Sample items in item_map:")
        for idx, name in enumerate(list(self.item_map.keys())[:5]):
            print(f"  {idx+1}. {name}")
        
        # Parse TOC - use ebooklib's toc directly!
        toc_nodes = self._parse_toc()
        print(f"Parsed TOC with {len(toc_nodes) if toc_nodes else 0} top-level nodes")
        
        # Populate UI with TOC and load first chapter
        self._populate_epub_ui(toc_nodes)
    
    def _parse_toc(self):
        """Parse table of contents using ebooklib's built-in toc"""
        toc_nodes = []
        
        # Try to use book.toc directly (preferred method)
        try:
            if hasattr(self.book, 'toc') and self.book.toc:
                print("Using book.toc for TOC")
                toc_nodes = self._convert_toc_to_nodes(self.book.toc)
                if toc_nodes:
                    print(f"Found {len(toc_nodes)} TOC entries from book.toc")
                    # Print first few entries
                    for idx, node in enumerate(toc_nodes[:3]):
                        print(f"  TOC[{idx}]: '{node.get('title')}' -> {node.get('href')}")
                    return toc_nodes
        except Exception as e:
            print(f"Error parsing book.toc: {e}")
            import traceback
            traceback.print_exc()
        
        # Fallback: Search for nav document
        try:
            print("Searching for <nav> document...")
            for item in self.book.get_items():
                if item.get_type() != ITEM_DOCUMENT:
                    continue
                content = item.get_content().decode("utf-8", errors="ignore")
                if "<nav" in content.lower():
                    print(f"Found nav in: {item.get_name()}")
                    toc_nodes = self._parse_nav_toc_from_string(content)
                    if toc_nodes:
                        print(f"Parsed {len(toc_nodes)} entries from nav document")
                        return toc_nodes
        except Exception as e:
            print(f"Error parsing nav document: {e}")
        
        # Last resort: Generate TOC from spine
        if not toc_nodes and self.spine:
            print("Generating TOC from spine...")
            toc_nodes = []
            for i, spine_item in enumerate(self.spine):
                href = spine_item['href']
                item = spine_item['item']
                
                # Try to extract title from HTML
                title = None
                try:
                    content = item.get_content().decode("utf-8", errors="ignore")
                    m = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
                    if m:
                        title = m.group(1).strip()
                except Exception:
                    pass
                
                if not title:
                    title = href.split("/")[-1]
                
                toc_nodes.append({"href": href, "title": title, "children": []})
                print(f"  Spine[{i}]: '{title}' -> {href}")
        
        return toc_nodes
    
    def _convert_toc_to_nodes(self, toc_items):
        """Convert ebooklib toc format to our node format"""
        nodes = []
        
        def process_item(item):
            node = {"href": None, "title": None, "children": []}
            
            # Handle tuple format: (Section, Link) or (Link, [children])
            if isinstance(item, tuple):
                # Format: (Link/Section, [children])
                if len(item) >= 2 and isinstance(item[1], list):
                    first = item[0]
                    node["href"] = getattr(first, "href", None)
                    node["title"] = getattr(first, "title", None) or str(first)
                    # Process children
                    for child in item[1]:
                        node["children"].append(process_item(child))
                else:
                    # Just a link
                    node["href"] = getattr(item, "href", None)
                    node["title"] = getattr(item, "title", None) or str(item)
            # Handle Link/Section objects directly
            elif hasattr(item, "href"):
                node["href"] = item.href
                node["title"] = getattr(item, "title", None) or str(item)
            else:
                node["title"] = str(item)
            
            return node
        
        for item in toc_items:
            try:
                nodes.append(process_item(item))
            except Exception as e:
                print(f"Error processing TOC item: {e}")
                continue
        
        return nodes
    
    def get_resource(self, path):
        """Get resource content by path using ebooklib's API"""
        try:
            # Try direct lookup first
            if path in self.item_map:
                return self.item_map[path].get_content()
            
            # Try get_item_with_href (handles relative paths better)
            item = self.book.get_item_with_href(path)
            if item:
                return item.get_content()
            
            # Try basename lookup
            basename = os.path.basename(path)
            for href, item in self.item_map.items():
                if os.path.basename(href) == basename:
                    return item.get_content()
            
            return None
        except Exception as e:
            print(f"Error getting resource {path}: {e}")
            return None
    
    def _populate_epub_ui(self, toc_nodes):
        """Populate UI with TOC and load first chapter"""
        # Load first chapter from spine
        loaded = False
        if self.spine:
            try:
                first_item = self.spine[0]['item']
                content = first_item.get_content().decode("utf-8", errors="ignore")
                processed_content = self._process_epub_content(content, first_item)
                self.original_html_content = processed_content
                self.apply_column_layout(self.current_columns - 1)
                loaded = True
            except Exception as e:
                print(f"Error loading first chapter: {e}")
                import traceback
                traceback.print_exc()

        if not loaded:
            self.original_html_content = "<html><body><p>Could not render EPUB content.</p></body></html>"
            self.apply_column_layout(self.current_columns - 1)

        # Clear and rebuild TOC
        self._clear_container(self._toc_box)
        self._row_map.clear()
        self._active_href = None

        if not toc_nodes:
            self._toc_box.append(Gtk.Label(label="No Table of Contents"))
        else:
            hdr = Gtk.Label(label="Contents", xalign=0)
            try:
                hdr.add_css_class("toc-contents-label")
            except Exception:
                pass
            self._toc_box.append(hdr)
            self._build_foliate_toc(self._toc_box, toc_nodes)

        # Show sidebar
        try:
            self.split.set_show_sidebar(True)
            self.split.set_collapsed(False)
        except Exception:
            pass
    
    def _build_foliate_toc(self, parent_box, nodes, level=0):
        import html as _html
        for node in nodes:
            raw_title = node.get("title") or node.get("href") or "Untitled"
            title = raw_title if not isinstance(raw_title, str) else _html.unescape(raw_title)
            safe_title = GLib.markup_escape_text(title)
            href = node.get("href")
            children = node.get("children") or []

            if children:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

                header_row = Adw.ActionRow()
                header_row.set_activatable(True)
                header_row.set_focusable(True)
                try:
                    header_row.add_css_class("toc-expander-row")
                except Exception:
                    pass

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                header_box.set_margin_start(0)
                try:
                    header_box.set_hexpand(True)
                except Exception:
                    pass

                chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
                try:
                    chev.set_pixel_size(14)
                except Exception:
                    pass
                try:
                    chev.add_css_class("toc-chev")
                except Exception:
                    pass

                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                try:
                    lbl.set_max_width_chars(40)
                except Exception:
                    pass

                header_box.append(chev)
                header_box.append(lbl)

                try:
                    header_row.set_child(header_box)
                except Exception:
                    try:
                        header_row.set_title(safe_title)
                    except Exception:
                        pass

                revealer = Gtk.Revealer()
                try:
                    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                except Exception:
                    pass
                revealer.set_reveal_child(False)

                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                child_container.set_margin_start(8)
                self._build_foliate_toc(child_container, children, level=level+1)
                revealer.set_child(child_container)

                def _make_toggle(href_local, revealer_local, chev_local):
                    def _toggle_and_nav():
                        try:
                            new_state = not revealer_local.get_reveal_child()
                            revealer_local.set_reveal_child(new_state)
                            chev_local.set_from_icon_name("go-down-symbolic" if new_state else "go-next-symbolic")
                            if href_local:
                                self._on_toc_clicked(None, href_local)
                                self._set_active(href_local) # Ensure active state is set when expander is clicked too
                        except Exception:
                            pass
                    return _toggle_and_nav

                toggle_fn = _make_toggle(href, revealer, chev)

                try:
                    header_row.connect("activated", lambda w, fn=toggle_fn: fn())
                except Exception:
                    pass

                try:
                    gesture = Gtk.GestureClick.new()
                    gesture.connect("pressed", lambda g, n_press, x, y, fn=toggle_fn: fn())
                    header_box.add_controller(gesture)
                except Exception:
                    pass

                outer.append(header_row)
                outer.append(revealer)
                parent_box.append(outer)

                if href:
                    self._row_map[href] = header_row
            else:
                row = Adw.ActionRow()

                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                try:
                    lbl.set_max_width_chars(40)
                except Exception:
                    pass

                try:
                    row.set_child(lbl)
                except Exception:
                    try:
                        row.set_title(safe_title)
                    except Exception:
                        pass

                row.set_activatable(True)
                # Fixed: Now calls both _on_toc_clicked AND _set_active on direct click
                row.connect("activated", lambda w, h=href: (self._on_toc_clicked(w, h), self._set_active(h)))

                if level:
                    cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    cont.set_margin_start(0)
                    cont.append(row)
                    parent_box.append(cont)
                else:
                    try:
                        row.set_margin_start(0)
                    except Exception:
                        pass
                    parent_box.append(row)

                if href:
                    try:
                        row.add_css_class("toc-leaf")
                    except Exception:
                        pass
                    self._row_map[href] = row
    
    def _process_epub_content(self, html_content, item):
        """Process EPUB content to handle embedded resources (CSS, images)."""
        # Embed all CSS stylesheets
        css_content = ""
        try:
            for css_item in self.book.get_items_of_type(ITEM_STYLE):
                css_text = css_item.get_content().decode("utf-8", errors="ignore")
                css_content += f"\n{css_text}\n"
        except Exception as e:
            print(f"Error loading CSS: {e}")

        if css_content:
            if "<head>" in html_content:
                html_content = html_content.replace("<head>", f"<head><style>{css_content}</style>", 1)
            else:
                html_content = f"<html><head><style>{css_content}</style></head><body>{html_content}</body></html>"

        # Get base path for current item - use get_name() instead of file_name
        item_name = item.get_name()
        base_path = os.path.dirname(item_name)
        print(f"Processing content for: {item_name}, base_path: {base_path}")
        
        # Build image map for efficient lookup
        image_map = {}
        
        try:
            for img_item in self.book.get_items_of_type(ITEM_IMAGE):
                img_name = img_item.get_name()
                
                # Determine MIME type
                ext = img_name.lower().split('.')[-1]
                mime_types = {
                    'png': 'image/png',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'gif': 'image/gif',
                    'svg': 'image/svg+xml',
                    'webp': 'image/webp',
                    'bmp': 'image/bmp',
                }
                mime_type = mime_types.get(ext, 'image/jpeg')
                
                # Create data URI
                img_data = img_item.get_content()
                img_base64 = base64.b64encode(img_data).decode("utf-8")
                data_uri = f"data:{mime_type};base64,{img_base64}"
                
                # Generate all possible reference patterns
                basename = os.path.basename(img_name)
                
                # Calculate relative path from current document to image
                if base_path:
                    try:
                        rel_path = os.path.relpath(img_name, base_path)
                        rel_path_unix = rel_path.replace("\\", "/")
                    except ValueError:
                        rel_path = img_name
                        rel_path_unix = img_name
                else:
                    rel_path = img_name
                    rel_path_unix = img_name
                
                # All possible ways the image might be referenced
                patterns = [
                    img_name,                          # Full path: OEBPS/Images/cover.jpg
                    basename,                          # Just filename: cover.jpg
                    f"./{basename}",                   # Relative: ./cover.jpg
                    f"../{basename}",                  # Parent dir: ../cover.jpg
                    rel_path,                          # Relative path
                    rel_path_unix,                     # Unix-style relative path
                    img_name.replace("\\", "/"),       # Unix-style full path
                ]
                
                # Add common EPUB path variations
                if '/' in img_name:
                    parts = img_name.split('/')
                    if len(parts) > 1:
                        patterns.append('/'.join(parts[-2:]))  # Last 2 parts
                        patterns.append(f"../{parts[-1]}")     # ../filename
                        patterns.append(f"../Images/{parts[-1]}")  # ../Images/filename
                        patterns.append(f"../images/{parts[-1]}")  # ../images/filename (lowercase)
                
                for pattern in patterns:
                    image_map[pattern] = data_uri
                    
                print(f"Image mapped: {img_name} -> {len(patterns)} patterns")
        except Exception as e:
            print(f"Error building image map: {e}")
            import traceback
            traceback.print_exc()
        
        # Replace all image references with data URIs
        for pattern, data_uri in image_map.items():
            # Try simple string replacement first (faster)
            html_content = html_content.replace(f'src="{pattern}"', f'src="{data_uri}"')
            html_content = html_content.replace(f"src='{pattern}'", f"src='{data_uri}'")
            
        return html_content

    
    def _on_toc_clicked(self, widget, href):
        """Handle TOC item click - load the referenced content"""
        if not self.book or not href:
            return

        print(f"\n=== TOC Click: {href} ===")

        # Split into path and fragment (anchor)
        href_path, _, fragment = href.partition("#")
        href_path = href_path.lstrip("./")
        
        print(f"Looking for: {href_path}")
        print(f"Fragment: {fragment if fragment else 'None'}")

        # Find the item - try multiple strategies
        item = None
        
        # Strategy 1: Direct lookup from item_map
        if href_path in self.item_map:
            item = self.item_map[href_path]
            print(f"✓ Found via direct lookup")
        
        # Strategy 2: Try with common EPUB base directories
        if not item:
            common_bases = ['OEBPS/', 'OPS/', 'EPUB/', 'Text/', 'text/', 'content/', 'Content/']
            for base in common_bases:
                test_path = base + href_path
                if test_path in self.item_map:
                    item = self.item_map[test_path]
                    print(f"✓ Found with base path: {test_path}")
                    break
        
        # Strategy 3: Try basename match
        if not item:
            href_basename = os.path.basename(href_path)
            print(f"Trying basename: {href_basename}")
            for item_name, item_obj in self.item_map.items():
                if os.path.basename(item_name) == href_basename:
                    item = item_obj
                    print(f"✓ Found via basename match: {item_name}")
                    break
        
        # Strategy 4: Search spine
        if not item:
            print(f"Searching spine ({len(self.spine)} items)...")
            for spine_entry in self.spine:
                spine_href = spine_entry['href']
                spine_basename = os.path.basename(spine_href)
                
                if (href_path == spine_href or 
                    href_path == spine_basename or
                    os.path.basename(href_path) == spine_basename):
                    item = spine_entry['item']
                    print(f"✓ Found in spine: {spine_href}")
                    break
        
        # Strategy 5: Partial path matching (suffix match)
        if not item:
            print(f"Trying partial path matching...")
            href_parts = href_path.split('/')
            for item_name, item_obj in self.item_map.items():
                item_parts = item_name.split('/')
                # Check if the end of the paths match
                if len(href_parts) <= len(item_parts):
                    if item_parts[-len(href_parts):] == href_parts:
                        item = item_obj
                        print(f"✓ Found via partial match: {item_name}")
                        break
        
        # Strategy 6: Case-insensitive match
        if not item:
            href_lower = href_path.lower()
            href_basename_lower = os.path.basename(href_path).lower()
            for item_name, item_obj in self.item_map.items():
                if (item_name.lower() == href_lower or 
                    os.path.basename(item_name).lower() == href_basename_lower or
                    item_name.lower().endswith('/' + href_lower)):
                    item = item_obj
                    print(f"✓ Found via case-insensitive match: {item_name}")
                    break
        
        if item and item.get_type() == ITEM_DOCUMENT:
    
    def show_error_dialog(self, message):
        try:
            dlg = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text=message
            )
            dlg.present()
        except Exception:
            pass
    
    def apply_column_layout(self, selected_column_index, restore_position=False):
        # Removed the check for EPUB mode - now applies to both HTML and EPUB
        # if self.is_epub_mode:
        #     return
            
        num_columns = selected_column_index + 1
        self.current_columns = num_columns
        
        # Create CSS for fixed column layout
        css = f"""
        <style>
            body {{
                font-family: sans-serif;
                margin-top: 0px;
                margin-bottom: 0px;
                margin-left: 0px;
                margin-right: 0px;
                width: 100%;
                height: 100%;
            }}
            .content-container {{
                column-count: {num_columns};
                column-gap: 20px;
                width: 100%;
                height: 100%;
                box-sizing: border-box;
            }}
            .content-container * {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}
            .content-container p,
            .content-container div,
            .content-container span {{
                break-inside: auto;
                page-break-inside: auto;
            }}
        </style>
        """
        
        # Add JavaScript for snapping scroll positions and keyboard navigation
        js_script = f"""
        window.currentColumnCount = {num_columns};

        function getColumnWidth() {{
            const container = document.querySelector('.content-container');
            if (!container) return 0;
            const style = window.getComputedStyle(container);
            const colCount = window.currentColumnCount;
            const gap = parseFloat(style.columnGap) || 0;
            const totalGap = gap * (colCount - 1);
            const columnWidth = (container.offsetWidth - totalGap) / colCount;
            return columnWidth + gap;
        }}

        function sendScrollEvent(eventType) {{
            if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.scrollEvent) {{
                const colWidth = getColumnWidth();
                const currentColumn = colWidth > 0 ? Math.round(window.scrollX / colWidth) : 0;
                window.webkit.messageHandlers.scrollEvent.postMessage(JSON.stringify({{
                    type: eventType,
                    scrollX: window.scrollX,
                    scrollY: window.scrollY,
                    column: currentColumn
                }}));
            }}
        }}

        function smoothScrollTo(xTarget, yTarget) {{
            const startX = window.scrollX;
            const startY = window.scrollY;
            const distanceX = xTarget - startX;
            const distanceY = yTarget - startY;
            const duration = 400;
            const startTime = performance.now();
            function step(time) {{
                const elapsed = time - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const t = progress < 0.5 ? 4 * progress * progress * progress : (progress - 1) * (2 * progress - 2) * (2 * progress - 2) + 1;
                window.scrollTo(startX + distanceX * t, startY + distanceY * t);
                if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
        }}

        function snapScroll() {{
            if (window.currentColumnCount === 1) return;
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const currentScroll = window.scrollX;
            const target = Math.round(currentScroll / colWidth) * colWidth;
            if (Math.abs(currentScroll - target) > 1) window.scrollTo(target, window.scrollY);
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) snapScroll();
            }}, 100);
        }});

        document.addEventListener('wheel', function(e) {{
            if (window.currentColumnCount === 1) {{
                sendScrollEvent('wheel-y');
                return;
            }}
            e.preventDefault();
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            const target = Math.round((window.scrollX + scrollDist) / colWidth) * colWidth;
            smoothScrollTo(target, window.scrollY);
            sendScrollEvent('wheel');
        }}, {{ passive: false }});

        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            const colWidth = getColumnWidth();
            const viewportH = window.innerHeight;
            const maxScrollX = document.body.scrollWidth - window.innerWidth;
            const maxScrollY = document.body.scrollHeight - viewportH;

            let x = window.scrollX, y = window.scrollY, type = null;

            if (window.currentColumnCount === 1) {{
                switch (e.key) {{
                    case 'ArrowUp': e.preventDefault(); y = Math.max(0, y - viewportH * 0.8); type = 'arrow-up'; break;
                    case 'ArrowDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH * 0.8); type = 'arrow-down'; break;
                    case 'PageUp': e.preventDefault(); y = Math.max(0, y - viewportH); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); y = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); y = maxScrollY; type = 'end'; break;
                }}
            }} else {{
                switch (e.key) {{
                    case 'ArrowLeft': e.preventDefault(); x = Math.max(0, x - colWidth); type = 'arrow-left'; break;
                    case 'ArrowRight': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth); type = 'arrow-right'; break;
                    case 'PageUp': e.preventDefault(); x = Math.max(0, x - colWidth * 2); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth * 2); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); x = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); x = maxScrollX; type = 'end'; break;
                }}
            }}

            if (type) {{
                smoothScrollTo(x, y);
                setTimeout(() => {{
                    sendScrollEvent(type);
                }}, 450);
            }}
        }});
        """
        
        original_html = self.original_html_content
        # Extract body content if it's a full HTML document
        if '<body>' in original_html.lower() and '</body>' in original_html.lower():
            start = original_html.lower().find('<body>') + 6
            end = original_html.lower().find('</body>', start)
            if end != -1:
                body_content = original_html[start:end]
            else:
                body_content = original_html
        else:
            body_content = original_html
        
        html_content = f"""
        <html>
            <head>
                {css}
            </head>
            <body>
                <div class="content-container">
                    {body_content}
                </div>
                <script>{js_script}</script>
            </body>
        </html>
        """
        
        self.webview.load_html(html_content)
   
    def on_columns_changed(self, combo, pspec):
        # Removed the check for EPUB mode - now applies to both
        # if self.is_epub_mode:
        #     return
        selected = combo.get_selected()
        # Small delay to let anchor be stored, then apply layout
        GLib.timeout_add(50, lambda: self.apply_column_layout(selected, restore_position=True))
    
    def on_size_changed(self, *args):
        # Removed the check for EPUB mode - now applies to both
        # if not self.is_epub_mode:
        GLib.timeout_add(100, lambda: self.apply_column_layout(self.current_columns - 1, restore_position=True))


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
