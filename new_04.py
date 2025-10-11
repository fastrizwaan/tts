#!/usr/bin/env python3
# epub_viewer_complete.py
import os, sys, re, html, base64, json
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, Gio, GLib, WebKit, Pango, Gdk
from ebooklib import epub

Adw.init()

_FOLIATE_CSS = b"""
.sidebar-toc { background-color: @surface; padding-top: 0px; padding-bottom: 0px; }
.toc-contents-label { padding-left: 12px; padding-right: 12px; padding-bottom: 6px; font-weight: 600; }
.toc-expander-row {
  min-height: 30px;
  padding-top: 4px;
  padding-bottom: 4px;
  border-radius: 10px;
  margin-right: 4px;
}
.toc-leaf {
  min-height: 30px;
  border-radius: 8px;
  margin-right: 4px;
  padding-left: 8px;
  padding-top: 4px;
  padding-bottom: 4px;
}
.toc-chev { margin-left: 2px; margin-right: 8px; }
.adw-action-row:hover { background-color: rgba(0,0,0,0.03); }
.toc-active { background-color: rgba(20, 80, 160, 0.18); font-weight: 600; }
"""

_READER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style id="base-style">
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  height: 100%;
  overflow-x: auto;
  overflow-y: auto;
}
body {
  max-width: 100%;
  margin: 0 auto;
  padding: 40px 20px;
  font-family: -apple-system, system-ui, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.6;
  font-size: 18px;
  color: var(--text-color);
  background: var(--bg-color);
  transition: background-color 0.3s, color 0.3s;
}

/* Light theme (default) */
:root {
  --bg-color: #fafafa;
  --text-color: #333;
  --card-bg: white;
  --card-shadow: rgba(0,0,0,0.1);
  --link-color: #0066cc;
  --code-bg: #f4f4f4;
  --border-color: #ddd;
  --quote-border: #ddd;
  --quote-text: #666;
}

/* Dark theme */
body.dark-theme {
  --bg-color: #1e1e1e;
  --text-color: #e0e0e0;
  --card-bg: #2d2d2d;
  --card-shadow: rgba(0,0,0,0.3);
  --link-color: #66b3ff;
  --code-bg: #3a3a3a;
  --border-color: #444;
  --quote-border: #555;
  --quote-text: #aaa;
}

body.single-column {
  max-width: 800px;
  column-count: 1;
}
body.multi-column {
  height: calc(100vh - 80px);
  overflow-y: hidden;
  overflow-x: auto;
  column-fill: auto;
}
img { 
  max-width: 100%; 
  height: auto; 
  display: block;
  margin: 1em auto;
  break-inside: avoid;
}
.chapter { 
  margin-bottom: 3em;
  background: var(--card-bg);
  padding: 2em;
  border-radius: 8px;
  box-shadow: 0 1px 3px var(--card-shadow);
  break-inside: avoid-column;
}
h1, h2, h3, h4, h5, h6 {
  scroll-margin-top: 60px;
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  line-height: 1.3;
  color: var(--text-color);
  break-after: avoid;
}
h1 { font-size: 2em; }
h2 { font-size: 1.6em; }
h3 { font-size: 1.3em; }
p {
  margin: 0.8em 0;
  text-align: justify;
  hyphens: auto;
}
a {
  color: var(--link-color);
  text-decoration: none;
  pointer-events: none;
  cursor: default;
}
a:hover {
  text-decoration: underline;
}
blockquote {
  margin: 1em 2em;
  padding-left: 1em;
  border-left: 3px solid var(--quote-border);
  font-style: italic;
  color: var(--quote-text);
}
code {
  background: var(--code-bg);
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-family: "Courier New", monospace;
  font-size: 0.9em;
}
pre {
  background: var(--code-bg);
  padding: 1em;
  border-radius: 5px;
  overflow-x: auto;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  break-inside: avoid;
}
th, td {
  border: 1px solid var(--border-color);
  padding: 0.5em;
  text-align: left;
}
th {
  background: var(--code-bg);
  font-weight: 600;
}
</style>
<style id="epub-style">
__EPUB_CSS__
</style>
<style id="column-style">
</style>
</head>
<body class="single-column">
__CONTENT__
<script>
// Prevent navigation - disable all links
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('a').forEach(function(link) {
    link.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      return false;
    });
  });
});

// Track visible sections for TOC highlighting
let currentSection = null;

function findVisibleSection() {
  const sections = document.querySelectorAll('[data-toc-id]');
  let visible = null;
  const scrollTop = window.scrollY || window.pageYOffset;
  const viewHeight = window.innerHeight;
  
  for (let section of sections) {
    const rect = section.getBoundingClientRect();
    if (rect.top <= viewHeight * 0.3 && rect.bottom > 0) {
      visible = section.getAttribute('data-toc-id');
    }
  }
  
  if (visible && visible !== currentSection) {
    currentSection = visible;
    try {
      window.webkit.messageHandlers.sectionChanged.postMessage(visible);
    } catch(e) {
      console.log('Message handler not available:', e);
    }
  }
}

let scrollTimeout;
window.addEventListener('scroll', () => {
  clearTimeout(scrollTimeout);
  scrollTimeout = setTimeout(findVisibleSection, 150);
}, { passive: true });

window.addEventListener('load', () => {
  setTimeout(findVisibleSection, 500);
  console.log('Page loaded, sections:', document.querySelectorAll('[data-toc-id]').length);
});

// Handle scroll-to commands from TOC
window.scrollToSection = function(tocId) {
  console.log('Scrolling to:', tocId);
  const element = document.querySelector('[data-toc-id="' + tocId + '"]');
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    currentSection = tocId;
    setTimeout(findVisibleSection, 600);
    return true;
  } else {
    console.log('Element not found for:', tocId);
    return false;
  }
};

// Column layout control
window.setColumnLayout = function(mode, value) {
  const body = document.body;
  const style = document.getElementById('column-style');
  
  body.className = body.className.replace(/single-column|multi-column/g, '').trim();
  
  if (mode === 'single') {
    body.classList.add('single-column');
    style.textContent = '';
  } else if (mode === 'count') {
    body.classList.add('multi-column');
    style.textContent = `
      body {
        column-count: ${value};
        column-gap: 2em;
        column-fill: auto;
      }
    `;
  } else if (mode === 'width') {
    body.classList.add('multi-column');
    style.textContent = `
      body {
        column-width: ${value}px;
        column-gap: 2em;
        column-fill: auto;
      }
    `;
  }
  
  setTimeout(findVisibleSection, 300);
};

// Theme toggle
window.setTheme = function(theme) {
  if (theme === 'dark') {
    document.body.classList.add('dark-theme');
  } else {
    document.body.classList.remove('dark-theme');
  }
};
</script>
</body>
</html>
"""

class EPubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Epub Viewer")
        self.set_default_size(1200, 800)

        provider = Gtk.CssProvider()
        provider.load_from_data(_FOLIATE_CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.book = None
        self.book_path = None
        self._user_hid_sidebar = False
        self._responsive_enabled = False
        self._row_map = {}
        self._revealer_map = {}
        self._active_href = None
        self._toc_structure = []
        self._section_counter = 0

        # Column state
        self._column_mode = 'single'
        self._num_columns = 1
        self._column_width_px = 300
        self._dark_theme = False

        self.split = Adw.OverlaySplitView(show_sidebar=False)
        self.set_content(self.split)

        self._toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        try:
            self._toc_box.add_css_class("sidebar-toc")
        except Exception:
            pass
        self._toc_box.set_margin_top(6); self._toc_box.set_margin_bottom(6)
        self._toc_box.set_margin_start(6); self._toc_box.set_margin_end(6)

        self._toc_scroller = Gtk.ScrolledWindow()
        try:
            self._toc_scroller.set_min_content_width(320)
        except Exception:
            pass
        self._toc_scroller.set_child(self._toc_box)
        self.split.set_sidebar(self._toc_scroller)

        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Gtk.Label(label="Epub Viewer"))
        self.toolbar.add_top_bar(self.header)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.content_placeholder = Gtk.Label(label="Library (no book loaded)")
        self.content_box.append(self.content_placeholder)
        self.toolbar.set_content(self.content_box)

        self.split.set_content(self.toolbar)

        self.connect("notify::default-width", self._on_window_size_changed)

        self._build_header_actions()
        self.set_library_mode()

    def _clear_container(self, container):
        child = container.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            try:
                container.remove(child)
            except Exception:
                pass
            child = next_child

    def _set_active(self, toc_id):
        if self._active_href == toc_id:
            return
        
        # Remove previous highlight
        prev = self._row_map.get(self._active_href)
        if prev:
            try:
                prev.remove_css_class("toc-active")
            except Exception:
                pass
        
        # Add new highlight
        w = self._row_map.get(toc_id)
        if w:
            try:
                w.add_css_class("toc-active")
            except Exception:
                pass
            
            # Expand parent if needed
            self._expand_parent_revealers(toc_id)
            
            # Scroll to visible
            GLib.timeout_add(100, self._scroll_toc_to_widget, w)
            
            self._active_href = toc_id

    def _scroll_toc_to_widget(self, widget):
        try:
            # Use proper GTK4 method to scroll to child
            self._toc_scroller.scroll_to(widget, Gtk.ScrollFlag.FOCUS, None)
        except:
            pass
        return False

    def _expand_parent_revealers(self, toc_id):
        # Find and expand all parent revealers
        parent = self._find_parent_id(toc_id)
        while parent:
            if parent in self._revealer_map:
                revealer, chev = self._revealer_map[parent]
                revealer.set_reveal_child(True)
                chev.set_from_icon_name("go-down-symbolic")
            parent = self._find_parent_id(parent)

    def _find_parent_id(self, toc_id):
        def search(nodes, target, parent=None):
            for node in nodes:
                if node.get('toc_id') == target:
                    return parent
                if node.get('children'):
                    result = search(node['children'], target, node.get('toc_id'))
                    if result is not None:
                        return result
            return None
        return search(self._toc_structure, toc_id)

    def set_library_mode(self):
        self.book = None
        self.book_path = None
        self._disable_responsive_sidebar()
        try:
            self.split.set_show_sidebar(False)
            self.split.set_collapsed(True)
        except Exception:
            pass
        self._clear_container(self.content_box)
        self.content_placeholder = Gtk.Label(label="Library — open an EPUB to start reading")
        self.content_box.append(self.content_placeholder)

    def set_reading_mode(self, epub_path):
        self._enable_responsive_sidebar()
        self.load_book(epub_path)

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

    def load_book(self, path):
        try:
            book = epub.read_epub(path)
            self.book = book
            self.book_path = path
            # Reset image cache for new book
            if hasattr(self, '_image_cache'):
                del self._image_cache
        except Exception as e:
            self._show_error(f"Failed to read EPUB: {e}")
            return

        toc_nodes = None

        # Try to parse nav element
        try:
            for item in self.book.get_items():
                try:
                    raw = item.get_content()
                    if not raw:
                        continue
                    s = raw.decode("utf-8", errors="ignore")
                    if "<nav" in s.lower():
                        toc_nodes = self._parse_nav_toc_from_string(s)
                        if toc_nodes:
                            break
                except Exception:
                    continue
        except Exception:
            toc_nodes = None

        # Fallback to book.toc
        if not toc_nodes:
            raw = getattr(self.book, "toc", None)
            if hasattr(self.book, "get_toc") and (not raw):
                try:
                    raw = self.book.get_toc()
                except Exception:
                    pass
            if raw:
                def recurse_item(it):
                    node = {"href": None, "title": None, "children": []}
                    if isinstance(it, (list, tuple)):
                        if len(it) > 1 and isinstance(it[-1], (list, tuple)):
                            first = it[0]
                            node["href"] = getattr(first, "href", None) or getattr(first, "src", None)
                            node["title"] = getattr(first, "title", None) or getattr(first, "text", None) or (str(first) if first is not None else None)
                            for sub in it[-1]:
                                node["children"].append(recurse_item(sub))
                            return node
                        else:
                            for el in it:
                                if getattr(el, "href", None) and not node["href"]:
                                    node["href"] = getattr(el, "href", None)
                                if (getattr(el, "title", None) or getattr(el, "text", None)) and not node["title"]:
                                    node["title"] = getattr(el, "title", None) or getattr(el, "text", None)
                            return node
                    if isinstance(it, dict):
                        node["href"] = it.get("href") or it.get("src")
                        node["title"] = it.get("title") or it.get("text") or it.get("name")
                        for c in it.get("children", []) or it.get("subitems", []):
                            node["children"].append(recurse_item(c))
                        return node
                    node["href"] = getattr(it, "href", None) or getattr(it, "src", None)
                    node["title"] = getattr(it, "title", None) or getattr(it, "text", None) or (str(it) if it is not None else None)
                    children = getattr(it, "children", None) or getattr(it, "subitems", None) or []
                    if children and isinstance(children, (list, tuple)):
                        for c in children:
                            node["children"].append(recurse_item(c))
                    return node
                try:
                    toc_nodes = []
                    for it in raw:
                        toc_nodes.append(recurse_item(it))
                except Exception:
                    toc_nodes = None

        # Final fallback - all documents
        if not toc_nodes:
            toc_nodes = []
            try:
                docs = []
                for item in self.book.get_items():
                    item_type = item.get_type()
                    media_type = getattr(item, 'media_type', '')
                    if item_type == 9 or 'html' in str(media_type).lower() or 'xhtml' in str(media_type).lower():
                        docs.append(item)
                
                for i, item in enumerate(docs):
                    href = getattr(item, "href", None) or getattr(item, "id", None) or f"doc-{i}"
                    title = None
                    try:
                        html_text = item.get_content().decode("utf-8", errors="ignore")
                        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                        if m:
                            title = m.group(1).strip()
                    except Exception:
                        title = None
                    if not title:
                        title = href.split("/")[-1]
                    toc_nodes.append({"href": href, "title": title, "children": []})
            except Exception:
                toc_nodes = []

        self._section_counter = 0
        self._assign_toc_ids(toc_nodes)
        self._toc_structure = toc_nodes
        self._populate_reader_ui(toc_nodes)

    def _assign_toc_ids(self, nodes):
        for node in nodes:
            self._section_counter += 1
            node['toc_id'] = f"toc-{self._section_counter}"
            if node.get('children'):
                self._assign_toc_ids(node['children'])

    def _populate_reader_ui(self, toc_nodes):
        self._clear_container(self.content_box)
        
        # Show loading indicator
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_vexpand(True)
        
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        spinner_box.append(spinner)
        
        loading_label = Gtk.Label(label="Loading EPUB content...")
        spinner_box.append(loading_label)
        
        self.content_box.append(spinner_box)
        
        # Process in idle to not block UI
        GLib.idle_add(self._build_and_load_content, toc_nodes, spinner_box)
    
    def _build_and_load_content(self, toc_nodes, spinner_box):
        import time
        start_time = time.time()
        
        # Create WebView
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_javascript_can_access_clipboard(False)
        settings.set_enable_write_console_messages_to_stdout(True)
        
        # Register message handler
        manager = self.webview.get_user_content_manager()
        try:
            manager.register_script_message_handler("sectionChanged", None)
            manager.connect("script-message-received::sectionChanged", self._on_section_changed)
        except Exception as e:
            print(f"Message handler error: {e}")
        
        self.webview.connect("load-changed", self._on_load_changed)
        
        # Build HTML
        print("Building book HTML...")
        full_html = self._build_full_book_html(toc_nodes)
        build_time = time.time() - start_time
        print(f"HTML built in {build_time:.2f}s: {len(full_html)} chars")
        print(f"Sections in HTML: {full_html.count('data-toc-id=')}")
        
        # Load HTML
        self.webview.load_html(full_html, "file:///")
        
        # Remove spinner, add webview
        self.content_box.remove(spinner_box)
        self.content_box.append(self.webview)

        # Build TOC
        self._clear_container(self._toc_box)
        self._row_map.clear()
        self._revealer_map.clear()
        self._active_href = None

        if not toc_nodes:
            self._toc_box.append(Gtk.Label(label="NO TOC"))
        else:
            hdr = Gtk.Label(label="Contents", xalign=0)
            try:
                hdr.add_css_class("toc-contents-label")
            except Exception:
                pass
            self._toc_box.append(hdr)
            self._build_foliate_toc(self._toc_box, toc_nodes)

        try:
            self.split.set_show_sidebar(True)
            self.split.set_collapsed(False)
        except Exception:
            pass
        
        total_time = time.time() - start_time
        print(f"Total load time: {total_time:.2f}s")
        
        return False  # Don't repeat
    
    def _on_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            print("WebView load finished")
            # Give it a moment to settle
            GLib.timeout_add(500, self._check_webview_content)
    
    def _check_webview_content(self):
        js = """
        (function() {
            var sections = document.querySelectorAll('[data-toc-id]');
            return sections.length;
        })();
        """
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, self._on_check_finished, None)
        except Exception as e:
            print(f"Check error: {e}")
        return False
    
    def _on_check_finished(self, webview, result, user_data):
        try:
            js_result = webview.evaluate_javascript_finish(result)
            count = js_result.to_int32() if hasattr(js_result, 'to_int32') else 0
            print(f"Sections found in DOM: {count}")
        except Exception as e:
            print(f"Check finished error: {e}")

    def _build_full_book_html(self, toc_nodes):
        content_parts = []
        section_map = {}
        processed_hrefs = set()
        
        # Collect CSS
        css_content = self._collect_epub_styles()
        
        # Print all available items for debugging
        print("\n=== Available EPUB items ===")
        for item in self.book.get_items():
            print(f"  {getattr(item, 'href', 'NO_HREF')} (type: {item.get_type()})")
        print("=== End items ===\n")
        
        def process_node(node, level=0):
            toc_id = node.get('toc_id')
            title = node.get('title', 'Untitled')
            href = node.get('href')
            children = node.get('children', [])
            
            print(f"Processing node: {title} -> {href}")
            
            if href and self.book:
                href_base = href.split("#")[0]
                if href_base and href_base not in processed_hrefs:
                    processed_hrefs.add(href_base)
                    content = self._get_content_for_href(href)
                    if content:
                        content_parts.append(f'<div class="chapter" data-toc-id="{toc_id}"><h2>{html.escape(title)}</h2>{content}</div>')
                        section_map[toc_id] = title
                        print(f"  ✓ Added content for: {title}")
                    else:
                        print(f"  ✗ No content found for: {title}")
            
            for child in children:
                process_node(child, level + 1)
        
        if toc_nodes:
            for node in toc_nodes:
                process_node(node)
        
        # Fallback to spine
        if not content_parts:
            print("\n=== Falling back to spine ===")
            try:
                spine_items = []
                for item in self.book.get_items():
                    item_type = item.get_type()
                    media_type = getattr(item, 'media_type', '')
                    if item_type == 9 or 'html' in str(media_type).lower() or 'xhtml' in str(media_type).lower():
                        spine_items.append(item)
                
                print(f"Found {len(spine_items)} spine items")
                
                for idx, item in enumerate(spine_items):
                    try:
                        ihref = getattr(item, 'href', f'item-{idx}')
                        print(f"  Loading spine item: {ihref}")
                        html_text = item.get_content().decode("utf-8", errors="ignore")
                        html_text = self._process_images_in_html(html_text, ihref)
                        body_match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.IGNORECASE | re.DOTALL)
                        if body_match:
                            toc_id = f"toc-{idx+1}"
                            content_parts.append(f'<div class="chapter" data-toc-id="{toc_id}">{body_match.group(1)}</div>')
                            section_map[toc_id] = f"Chapter {idx+1}"
                            print(f"    ✓ Added spine item {idx+1}")
                    except Exception as e:
                        print(f"    ✗ Error loading spine item: {e}")
            except Exception as e:
                print(f"Error processing spine: {e}")
        
        full_content = '\n'.join(content_parts) if content_parts else '<div class="chapter"><p>No content could be loaded.</p></div>'
        section_map_json = json.dumps(section_map)
        
        print(f"\n=== Final stats ===")
        print(f"Content parts: {len(content_parts)}")
        print(f"Total content length: {len(full_content)} chars")
        print(f"Sections mapped: {len(section_map)}")
        
        final_html = _READER_TEMPLATE.replace('__CONTENT__', full_content)
        final_html = final_html.replace('__SECTION_MAP__', section_map_json)
        final_html = final_html.replace('__EPUB_CSS__', css_content)
        
        return final_html

    def _get_content_for_href(self, href):
        target = href.split("#")[0].lstrip("/")
        
        print(f"  Looking for href: '{href}' -> target: '{target}'")
        
        # Try exact match first
        for item in self.book.get_items():
            ihref = getattr(item, "href", None)
            if not ihref:
                continue
            
            if ihref == target:
                print(f"  ✓ Exact match: {ihref}")
                return self._extract_and_process_content(item, ihref)
        
        # Try filename match
        target_filename = target.split('/')[-1]
        for item in self.book.get_items():
            ihref = getattr(item, "href", None)
            if not ihref:
                continue
            
            if ihref.split('/')[-1] == target_filename:
                print(f"  ✓ Filename match: {ihref}")
                return self._extract_and_process_content(item, ihref)
        
        # Try ends-with match
        for item in self.book.get_items():
            ihref = getattr(item, "href", None)
            if not ihref:
                continue
            
            if ihref.endswith(target) or target.endswith(ihref):
                print(f"  ✓ Ends-with match: {ihref}")
                return self._extract_and_process_content(item, ihref)
        
        print(f"  ✗ No match found for: {target}")
        return ""
    
    def _extract_and_process_content(self, item, ihref):
        try:
            html_text = item.get_content().decode("utf-8", errors="ignore")
            
            # Count images before processing
            img_count_before = html_text.count('<img')
            if img_count_before > 0:
                print(f"    Found {img_count_before} <img> tags")
            
            html_text = self._process_images_in_html(html_text, ihref)
            
            # Count data URIs after processing
            data_uri_count = html_text.count('src="data:image')
            if data_uri_count > 0:
                print(f"    Converted {data_uri_count} images to data URIs")
            
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.IGNORECASE | re.DOTALL)
            if body_match:
                return body_match.group(1)
            return html_text
        except Exception as e:
            print(f"    ✗ Error extracting content: {e}")
            return ""

    def _collect_epub_styles(self):
        css_parts = []
        try:
            for item in self.book.get_items():
                item_type = item.get_type()
                media_type = getattr(item, 'media_type', '')
                if item_type == 9 or 'css' in str(media_type).lower():
                    try:
                        css_text = item.get_content().decode("utf-8", errors="ignore")
                        css_text = self._process_css_urls(css_text, item)
                        css_parts.append(css_text)
                    except Exception as e:
                        print(f"CSS error: {e}")
        except Exception as e:
            print(f"Error collecting styles: {e}")
        
        return '\n\n'.join(css_parts)

    def _process_css_urls(self, css_text, css_item):
        def replace_url(match):
            url = match.group(1).strip('\'"')
            if url.startswith('data:') or url.startswith('http'):
                return match.group(0)
            
            try:
                css_path = getattr(css_item, 'href', '')
                resource_path = self._resolve_path(url, css_path)
                
                for item in self.book.get_items():
                    item_href = getattr(item, 'href', '')
                    if self._paths_match(item_href, resource_path):
                        content = item.get_content()
                        mime_type = self._get_mime_type(item_href)
                        b64_data = base64.b64encode(content).decode('ascii')
                        return f"url('data:{mime_type};base64,{b64_data}')"
            except Exception:
                pass
            
            return match.group(0)
        
        return re.sub(r'url\((.*?)\)', replace_url, css_text)

    def _process_images_in_html(self, html_text, base_href):
        # Build a quick lookup cache for images
        if not hasattr(self, '_image_cache'):
            self._image_cache = {}
            print("    Building image cache...")
            for item in self.book.get_items():
                item_href = getattr(item, 'href', '')
                if any(ext in item_href.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']):
                    # Store by both full path and filename
                    self._image_cache[item_href] = item
                    self._image_cache[item_href.split('/')[-1]] = item
            print(f"    Cached {len(self._image_cache)} image items")
        
        images_found = 0
        images_converted = 0
        
        def replace_img(match):
            nonlocal images_found, images_converted
            images_found += 1
            
            src = match.group(1).strip('\'"')
            if src.startswith('data:') or src.startswith('http'):
                return match.group(0)
            
            try:
                # Try quick filename lookup first
                filename = src.split('/')[-1]
                if filename in self._image_cache:
                    item = self._image_cache[filename]
                    content = item.get_content()
                    mime_type = self._get_mime_type(filename)
                    b64_data = base64.b64encode(content).decode('ascii')
                    images_converted += 1
                    return f'src="data:{mime_type};base64,{b64_data}"'
                
                # Try resolved path
                img_path = self._resolve_path(src, base_href)
                if img_path in self._image_cache:
                    item = self._image_cache[img_path]
                    content = item.get_content()
                    mime_type = self._get_mime_type(img_path)
                    b64_data = base64.b64encode(content).decode('ascii')
                    images_converted += 1
                    return f'src="data:{mime_type};base64,{b64_data}"'
                
            except Exception:
                pass
            
            return match.group(0)
        
        # Replace both src and xlink:href for SVG
        result = re.sub(r'src=["\']([^"\']+)["\']', replace_img, html_text, flags=re.IGNORECASE)
        result = re.sub(r'xlink:href=["\']([^"\']+)["\']', replace_img, result, flags=re.IGNORECASE)
        
        if images_found > 0:
            print(f"      Images: {images_found} found, {images_converted} converted")
        
        return result

    def _paths_match(self, path1, path2):
        """Check if two paths refer to the same file"""
        if not path1 or not path2:
            return False
        p1 = path1.strip('/').lower()
        p2 = path2.strip('/').lower()
        return (p1 == p2 or 
                p1.endswith(p2) or 
                p2.endswith(p1) or
                p1.split('/')[-1] == p2.split('/')[-1])

    def _resolve_path(self, relative_path, base_href):
        """Resolve a relative path based on base href"""
        base_dir = '/'.join(base_href.split('/')[:-1]) if '/' in base_href else ''
        
        if relative_path.startswith('../'):
            parts = base_dir.split('/')
            rel_parts = relative_path.split('/')
            while rel_parts and rel_parts[0] == '..':
                rel_parts.pop(0)
                if parts:
                    parts.pop()
            return '/'.join(parts + rel_parts)
        elif relative_path.startswith('./'):
            return f"{base_dir}/{relative_path[2:]}"
        elif relative_path.startswith('/'):
            return relative_path[1:]
        else:
            return f"{base_dir}/{relative_path}" if base_dir else relative_path

    def _get_mime_type(self, filename):
        ext = filename.lower().split('.')[-1]
        mime_types = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
            'gif': 'image/gif', 'svg': 'image/svg+xml', 'webp': 'image/webp',
            'woff': 'font/woff', 'woff2': 'font/woff2', 'ttf': 'font/ttf',
            'otf': 'font/otf', 'eot': 'application/vnd.ms-fontobject'
        }
        return mime_types.get(ext, 'application/octet-stream')

    def _on_section_changed(self, manager, message):
        try:
            toc_id = str(message)
            if toc_id and toc_id.startswith('toc-'):
                GLib.idle_add(self._set_active, toc_id)
        except Exception as e:
            print(f"Section changed error: {e}")

    def _build_foliate_toc(self, parent_box, nodes, level=0):
        import html as _html
        for node in nodes:
            raw_title = node.get("title") or "Untitled"
            title = raw_title if not isinstance(raw_title, str) else _html.unescape(raw_title)
            safe_title = GLib.markup_escape_text(title)
            toc_id = node.get('toc_id')
            children = node.get("children") or []

            indent_px = 8 + (level * 10)

            if children:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

                header_row = Adw.ActionRow()
                header_row.set_activatable(True)
                try:
                    header_row.add_css_class("toc-expander-row")
                except Exception:
                    pass

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                header_box.set_margin_start(indent_px)
                header_box.set_hexpand(True)

                chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
                chev.set_pixel_size(14)
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
                lbl.set_max_width_chars(40)

                header_box.append(chev)
                header_box.append(lbl)
                header_row.set_child(header_box)

                revealer = Gtk.Revealer()
                revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                revealer.set_reveal_child(False)

                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                child_container.set_margin_start(indent_px + 8)
                self._build_foliate_toc(child_container, children, level=level+1)
                revealer.set_child(child_container)

                self._revealer_map[toc_id] = (revealer, chev)

                def _make_toggle(tid, rev, ch):
                    def _toggle():
                        new_state = not rev.get_reveal_child()
                        rev.set_reveal_child(new_state)
                        ch.set_from_icon_name("go-down-symbolic" if new_state else "go-next-symbolic")
                        self._scroll_to_section(tid)
                    return _toggle

                toggle_fn = _make_toggle(toc_id, revealer, chev)
                header_row.connect("activated", lambda w, fn=toggle_fn: fn())

                outer.append(header_row)
                outer.append(revealer)
                parent_box.append(outer)

                if toc_id:
                    self._row_map[toc_id] = header_row
            else:
                row = Adw.ActionRow()

                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                lbl.set_max_width_chars(40)

                row.set_child(lbl)
                row.set_activatable(True)
                row.connect("activated", lambda w, tid=toc_id: self._scroll_to_section(tid))

                if level:
                    cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    cont.set_margin_start(indent_px + 12)
                    cont.append(row)
                    parent_box.append(cont)
                else:
                    row.set_margin_start(indent_px + 22)
                    parent_box.append(row)

                if toc_id:
                    try:
                        row.add_css_class("toc-leaf")
                    except Exception:
                        pass
                    self._row_map[toc_id] = row

    def _scroll_to_section(self, toc_id):
        if not toc_id or not hasattr(self, 'webview'):
            return
        print(f"TOC clicked: {toc_id}")
        
        js = f"""
        (function() {{
            var el = document.querySelector('[data-toc-id="{toc_id}"]');
            if (el) {{
                el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                return true;
            }}
            return false;
        }})();
        """
        
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, self._on_scroll_finished, toc_id)
        except Exception as e:
            print(f"Scroll error: {e}")
    
    def _on_scroll_finished(self, webview, result, toc_id):
        try:
            js_result = webview.evaluate_javascript_finish(result)
            success = js_result.to_boolean() if hasattr(js_result, 'to_boolean') else False
            if success:
                print(f"  ✓ Scrolled to {toc_id}")
                self._set_active(toc_id)
            else:
                print(f"  ✗ Element not found: {toc_id}")
        except Exception as e:
            print(f"Scroll finish error: {e}")
    
    def _on_js_finished(self, webview, result, user_data):
        try:
            webview.evaluate_javascript_finish(result)
        except Exception as e:
            print(f"JS finished error: {e}")

    def _build_header_actions(self):
        # Left side
        load_btn = Gtk.Button.new_with_label("Open EPUB")
        load_btn.connect("clicked", self._on_open_clicked)
        self.header.pack_start(load_btn)

        # Theme toggle button
        theme_btn = Gtk.Button()
        theme_btn.set_icon_name("weather-clear-night-symbolic")
        theme_btn.set_tooltip_text("Toggle Dark/Light Theme")
        theme_btn.connect("clicked", self._on_theme_toggle)
        self.header.pack_start(theme_btn)

        # Column controls in a menu
        col_menu_btn = Gtk.MenuButton()
        col_menu_btn.set_icon_name("view-columns-symbolic")
        col_menu_btn.set_tooltip_text("Column Layout")
        
        col_popover = Gtk.Popover()
        col_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        col_box.set_margin_top(12)
        col_box.set_margin_bottom(12)
        col_box.set_margin_start(12)
        col_box.set_margin_end(12)
        
        # Single column button
        single_btn = Gtk.Button(label="Single Column")
        single_btn.connect("clicked", lambda w: (self._set_column_mode('single', 1), col_popover.popdown()))
        col_box.append(single_btn)
        
        col_box.append(Gtk.Separator())
        
        # Multi-column section
        multi_label = Gtk.Label(label="Column Count:")
        multi_label.set_xalign(0)
        col_box.append(multi_label)
        
        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(4)
        for i in range(1, 11):
            btn = Gtk.Button(label=str(i))
            btn.set_size_request(40, 32)
            btn.connect("clicked", lambda w, n=i: (self._set_column_mode('count', n), col_popover.popdown()))
            grid.attach(btn, (i-1) % 5, (i-1) // 5, 1, 1)
        col_box.append(grid)
        
        col_box.append(Gtk.Separator())
        
        # Pixel width section
        px_label = Gtk.Label(label="Column Width (50-500px):")
        px_label.set_xalign(0)
        col_box.append(px_label)
        
        px_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._width_entry = Gtk.Entry()
        self._width_entry.set_placeholder_text("e.g. 300")
        self._width_entry.set_width_chars(10)
        px_box.append(self._width_entry)
        
        px_btn = Gtk.Button(label="Apply")
        px_btn.connect("clicked", lambda w: (self._apply_pixel_width(), col_popover.popdown()))
        px_box.append(px_btn)
        col_box.append(px_box)
        
        col_popover.set_child(col_box)
        col_menu_btn.set_popover(col_popover)
        self.header.pack_start(col_menu_btn)

        # Right side
        toggle_btn = Gtk.Button.new_with_label("Toggle Sidebar")
        toggle_btn.connect("clicked", self._on_sidebar_toggle)
        self.header.pack_end(toggle_btn)

        close_btn = Gtk.Button.new_with_label("Close Book")
        close_btn.connect("clicked", lambda *_: self.set_library_mode())
        self.header.pack_end(close_btn)

    def _set_column_mode(self, mode, value):
        self._column_mode = mode
        if mode == 'count':
            self._num_columns = value
        
        if not hasattr(self, 'webview') or not self.webview:
            return
        
        js = f"window.setColumnLayout('{mode}', {value});"
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, self._on_js_finished, None)
            print(f"Applied layout: {mode} = {value}")
        except Exception as e:
            print(f"Layout error: {e}")

    def _apply_pixel_width(self):
        try:
            text = self._width_entry.get_text().strip()
            width = int(''.join(filter(str.isdigit, text)))
            if 50 <= width <= 500:
                self._column_width_px = width
                self._set_column_mode('width', width)
            else:
                self._show_error("Width must be between 50 and 500 pixels")
        except ValueError:
            self._show_error("Please enter a valid number")

    def _on_open_clicked(self, *_):
        dialog = Gtk.FileDialog(title="Open EPUB")
        filter_epub = Gtk.FileFilter()
        filter_epub.set_name("EPUB")
        filter_epub.add_pattern("*.epub")
        dialog.set_default_filter(filter_epub)
        
        def on_file_chosen(dlg, res):
            try:
                file = dlg.open_finish(res)
                if file:
                    path = file.get_path()
                    if path:
                        self.set_reading_mode(path)
            except Exception as e:
                self._show_error(f"Failed to open: {e}")
        
        dialog.open(self, None, on_file_chosen)

    def _show_error(self, text):
        try:
            dlg = Adw.AlertDialog(heading="Error", body=text)
            dlg.add_response("ok", "OK")
            dlg.set_default_response("ok")
            dlg.present(self)
        except Exception:
            print(f"ERROR: {text}")

    def _on_sidebar_toggle(self, btn):
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            self._user_hid_sidebar = not new
        except Exception:
            pass

    def _on_theme_toggle(self, btn):
        self._dark_theme = not self._dark_theme
        theme = 'dark' if self._dark_theme else 'light'
        
        if hasattr(self, 'webview') and self.webview:
            js = f"window.setTheme('{theme}');"
            try:
                self.webview.evaluate_javascript(js, -1, None, None, None, self._on_js_finished, None)
                print(f"Theme set to: {theme}")
            except Exception as e:
                print(f"Theme error: {e}")
        
        # Update icon
        if self._dark_theme:
            btn.set_icon_name("weather-clear-symbolic")
        else:
            btn.set_icon_name("weather-clear-night-symbolic")

    def _on_window_size_changed(self, *args):
        try:
            if self._user_hid_sidebar:
                return
            width = self.get_width()
            is_narrow = width < 768
            if self._responsive_enabled and self.book and self.book_path:
                if is_narrow:
                    self.split.set_collapsed(True)
                else:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(True)
            else:
                if is_narrow is not None:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(False)
        except Exception:
            pass

    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._user_hid_sidebar = False
        self._on_window_size_changed()

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._user_hid_sidebar = False
        try:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)
        except Exception:
            pass

class EPubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        if not self.props.active_window:
            self.win = EPubViewerWindow(self)
        self.win.present()

def main(argv):
    app = EPubViewerApp()
    return app.run(argv)

if __name__ == "__main__":
    sys.exit(main(sys.argv))
