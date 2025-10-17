#!/usr/bin/env python3
# epub_viewer_with_toc_sync.py
import os, sys, re, html, base64
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, Gio, GLib, WebKit, Pango, Gdk
from ebooklib import epub

Adw.init()

_FOLIATE_CSS = b"""
.sidebar-toc { background-color: @surface; padding-top: 6px; padding-bottom: 6px; }
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
.toc-active { background-color: rgba(20, 80, 160, 0.15); font-weight: 600; }
"""

_READER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
html, body {
  margin: 0;
  padding: 0;
  height: 100%;
}
body {
  max-width: 800px;
  margin: 0 auto;
  padding: 40px 20px;
  font-family: -apple-system, system-ui, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.6;
  font-size: 18px;
  color: #333;
  background: #fafafa;
}
img { 
  max-width: 100%; 
  height: auto; 
  display: block;
  margin: 1em auto;
}
.chapter { 
  margin-bottom: 3em;
  background: white;
  padding: 2em;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
h1, h2, h3, h4, h5, h6 {
  scroll-margin-top: 60px;
  margin-top: 1.5em;
  margin-bottom: 0.5em;
  line-height: 1.3;
  color: #222;
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
  color: #0066cc;
  text-decoration: none;
}
a:hover {
  text-decoration: underline;
}
blockquote {
  margin: 1em 2em;
  padding-left: 1em;
  border-left: 3px solid #ddd;
  font-style: italic;
  color: #666;
}
code {
  background: #f4f4f4;
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-family: "Courier New", monospace;
  font-size: 0.9em;
}
pre {
  background: #f4f4f4;
  padding: 1em;
  border-radius: 5px;
  overflow-x: auto;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
}
th, td {
  border: 1px solid #ddd;
  padding: 0.5em;
  text-align: left;
}
th {
  background: #f4f4f4;
  font-weight: 600;
}
</style>
</head>
<body>
__CONTENT__
<script>
// Track visible sections for TOC highlighting
let currentSection = null;
const sectionMap = __SECTION_MAP__;

function findVisibleSection() {
  const sections = document.querySelectorAll('[data-toc-id]');
  let visible = null;
  const scrollTop = window.scrollY;
  const viewHeight = window.innerHeight;
  
  for (let section of sections) {
    const rect = section.getBoundingClientRect();
    // Consider a section visible if its top is in the upper 30% of viewport
    if (rect.top <= viewHeight * 0.3 && rect.bottom > 0) {
      visible = section.getAttribute('data-toc-id');
    }
  }
  
  if (visible && visible !== currentSection) {
    currentSection = visible;
    try {
      window.webkit.messageHandlers.sectionChanged.postMessage(visible);
    } catch(e) {
      console.log('Could not send message:', e);
    }
  }
}

let scrollTimeout;
window.addEventListener('scroll', () => {
  clearTimeout(scrollTimeout);
  scrollTimeout = setTimeout(findVisibleSection, 100);
});

// Initial check after content loads
window.addEventListener('load', () => {
  setTimeout(findVisibleSection, 500);
});

// Handle scroll-to commands from TOC
window.scrollToSection = function(tocId) {
  const element = document.querySelector('[data-toc-id="' + tocId + '"]');
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    currentSection = tocId;
  }
};
</script>
</body>
</html>
"""

class EPubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Epub Viewer")
        self.set_default_size(1100, 720)

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
        self._active_href = None
        self._toc_structure = []
        self._section_counter = 0

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
        prev = self._row_map.get(self._active_href)
        if prev:
            try:
                prev.remove_css_class("toc-active")
            except Exception:
                pass
        w = self._row_map.get(toc_id)
        if w:
            try:
                w.add_css_class("toc-active")
            except Exception:
                pass
            try:
                # Expand parent if needed
                parent_id = self._get_parent_id(toc_id)
                if parent_id:
                    self._expand_to_id(parent_id)
                self._toc_scroller.get_vadjustment().set_value(
                    max(0, w.get_allocation().y - 100)
                )
            except Exception:
                pass
            self._active_href = toc_id

    def _get_parent_id(self, toc_id):
        # Find parent in structure
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

    def _expand_to_id(self, toc_id):
        # Recursively expand parents
        parent_id = self._get_parent_id(toc_id)
        if parent_id:
            self._expand_to_id(parent_id)
        # Expand this node if it has a revealer
        if hasattr(self, '_revealers') and toc_id in self._revealers:
            revealer, chev = self._revealers[toc_id]
            revealer.set_reveal_child(True)
            chev.set_from_icon_name("go-down-symbolic")

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
        except Exception as e:
            self._show_error(f"Failed to read EPUB: {e}")
            return

        toc_nodes = None

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

        if not toc_nodes:
            toc_nodes = []
            try:
                # Get document items - type 9 or html/xhtml media type
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
        
        # Create WebView with message handler
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_javascript_can_access_clipboard(True)
        
        # Add message handler for section changes
        manager = self.webview.get_user_content_manager()
        try:
            manager.register_script_message_handler("sectionChanged", None)
            manager.connect("script-message-received::sectionChanged", self._on_section_changed)
        except Exception as e:
            print(f"Error setting up message handler: {e}")
        
        # Build full book HTML
        print("Building full book HTML...")
        full_html = self._build_full_book_html(toc_nodes)
        print(f"HTML length: {len(full_html)} chars")
        
        # Load the HTML
        try:
            self.webview.load_html(full_html, "file:///")
            print("HTML loaded into WebView")
        except Exception as e:
            print(f"Error loading HTML: {e}")
        
        self.content_box.append(self.webview)

        # Build TOC sidebar
        self._clear_container(self._toc_box)
        self._row_map.clear()
        self._active_href = None
        self._revealers = {}

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

    def _build_full_book_html(self, toc_nodes):
        content_parts = []
        section_map = {}
        processed_hrefs = set()
        
        # Collect all CSS from the EPUB
        css_content = self._collect_epub_styles()
        
        def process_node(node, level=0):
            toc_id = node.get('toc_id')
            title = node.get('title', 'Untitled')
            href = node.get('href')
            children = node.get('children', [])
            
            # Add content from href if available and not already processed
            if href and self.book:
                href_base = href.split("#")[0]
                if href_base and href_base not in processed_hrefs:
                    processed_hrefs.add(href_base)
                    print(f"Processing node: {title} (href: {href})")
                    content = self._get_content_for_href(href)
                    if content:
                        # Add marker for TOC tracking
                        content_parts.append(f'<div class="chapter" data-toc-id="{toc_id}"><h2>{html.escape(title)}</h2>{content}</div>')
                        section_map[toc_id] = title
                        print(f"  -> Added content for {title}")
                    else:
                        print(f"  -> No content found for {href}")
            
            # Process children
            for child in children:
                process_node(child, level + 1)
        
        # If we have TOC structure, use it
        if toc_nodes:
            for node in toc_nodes:
                process_node(node)
        
        # If no content was added, fall back to all spine items
        if not content_parts:
            try:
                # Get spine items - they are document type (type 9 or media_type with html/xhtml)
                spine_items = []
                for item in self.book.get_items():
                    item_type = item.get_type()
                    media_type = getattr(item, 'media_type', '')
                    # Check if it's a document (type 9 or html/xhtml media type)
                    if item_type == 9 or 'html' in str(media_type).lower() or 'xhtml' in str(media_type).lower():
                        spine_items.append(item)
                
                for idx, item in enumerate(spine_items):
                    try:
                        html_text = item.get_content().decode("utf-8", errors="ignore")
                        body_match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.IGNORECASE | re.DOTALL)
                        if body_match:
                            toc_id = f"toc-{idx+1}"
                            content = body_match.group(1)
                            # Process images in this content
                            content = self._process_images_in_html(content, getattr(item, 'href', ''))
                            content_parts.append(f'<div class="chapter" data-toc-id="{toc_id}">{content}</div>')
                            section_map[toc_id] = f"Chapter {idx+1}"
                    except Exception as e:
                        print(f"Error loading spine item: {e}")
            except Exception as e:
                print(f"Error processing spine: {e}")
        
        full_content = '\n'.join(content_parts) if content_parts else '<p>No content could be loaded from this EPUB.</p>'
        section_map_json = str(section_map).replace("'", '"')
        
        # Build final HTML with embedded CSS
        final_html = _READER_TEMPLATE.replace('__CONTENT__', full_content).replace('__SECTION_MAP__', section_map_json)
        
        # Inject EPUB CSS before closing </head>
        if css_content:
            final_html = final_html.replace('</style>', f'</style>\n<style>\n{css_content}\n</style>')
        
        return final_html

    def _get_content_for_href(self, href):
        target = href.split("#")[0].lstrip("/")
        fragment = href.split("#")[1] if "#" in href else None
        
        for item in self.book.get_items():
            ihref = getattr(item, "href", None)
            if not ihref:
                continue
            if ihref.endswith(target) or ihref.split("/")[-1] == target:
                try:
                    html_text = item.get_content().decode("utf-8", errors="ignore")
                    
                    # Process images - convert to data URIs
                    html_text = self._process_images_in_html(html_text, ihref)
                    
                    # Extract body content
                    body_match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.IGNORECASE | re.DOTALL)
                    if body_match:
                        content = body_match.group(1)
                        # If there's a fragment, try to find and highlight it
                        if fragment:
                            content = f'<div id="{fragment}">{content}</div>'
                        return content
                    return html_text
                except Exception as e:
                    print(f"Error loading content for {href}: {e}")
        return ""

    def _collect_epub_styles(self):
        """Collect all CSS from the EPUB"""
        css_parts = []
        try:
            for item in self.book.get_items():
                item_type = item.get_type()
                # Check for CSS/stylesheet items
                if item_type == 9 or (hasattr(item, 'media_type') and 'css' in str(item.media_type).lower()):
                    try:
                        css_text = item.get_content().decode("utf-8", errors="ignore")
                        # Process font-face and url() references
                        css_text = self._process_css_urls(css_text, item)
                        css_parts.append(f"/* From: {item.get_name()} */\n{css_text}")
                    except Exception as e:
                        print(f"Error loading CSS {item.get_name()}: {e}")
        except Exception as e:
            print(f"Error collecting styles: {e}")
        
        return '\n\n'.join(css_parts)

    def _process_css_urls(self, css_text, css_item):
        """Convert relative URLs in CSS to data URIs or absolute paths"""
        def replace_url(match):
            url = match.group(1).strip('\'"')
            if url.startswith('data:') or url.startswith('http'):
                return match.group(0)
            
            # Try to find the font/resource in the EPUB
            try:
                # Get base path of CSS file
                css_path = getattr(css_item, 'href', '')
                base_dir = '/'.join(css_path.split('/')[:-1]) if '/' in css_path else ''
                
                # Resolve relative path
                if url.startswith('../'):
                    parts = base_dir.split('/')
                    url_parts = url.split('/')
                    while url_parts and url_parts[0] == '..':
                        url_parts.pop(0)
                        if parts:
                            parts.pop()
                    resource_path = '/'.join(parts + url_parts)
                elif url.startswith('./'):
                    resource_path = f"{base_dir}/{url[2:]}"
                else:
                    resource_path = f"{base_dir}/{url}" if base_dir else url
                
                # Find and convert to data URI
                for item in self.book.get_items():
                    item_href = getattr(item, 'href', '')
                    if item_href == resource_path or item_href.endswith(resource_path):
                        content = item.get_content()
                        mime_type = self._get_mime_type(item_href)
                        b64_data = base64.b64encode(content).decode('ascii')
                        return f"url('data:{mime_type};base64,{b64_data}')"
            except Exception as e:
                print(f"Error processing URL {url}: {e}")
            
            return match.group(0)
        
        # Replace url() references in CSS
        css_text = re.sub(r'url\((.*?)\)', replace_url, css_text)
        return css_text

    def _process_images_in_html(self, html_text, base_href):
        """Convert image src attributes to data URIs"""
        def replace_img(match):
            src = match.group(1).strip('\'"')
            if src.startswith('data:') or src.startswith('http'):
                return match.group(0)
            
            # Resolve relative path
            try:
                base_dir = '/'.join(base_href.split('/')[:-1]) if '/' in base_href else ''
                
                if src.startswith('../'):
                    parts = base_dir.split('/')
                    src_parts = src.split('/')
                    while src_parts and src_parts[0] == '..':
                        src_parts.pop(0)
                        if parts:
                            parts.pop()
                    img_path = '/'.join(parts + src_parts)
                elif src.startswith('./'):
                    img_path = f"{base_dir}/{src[2:]}"
                else:
                    img_path = f"{base_dir}/{src}" if base_dir else src
                
                # Find image in EPUB
                for item in self.book.get_items():
                    item_href = getattr(item, 'href', '')
                    if item_href == img_path or item_href.endswith(img_path):
                        content = item.get_content()
                        mime_type = self._get_mime_type(item_href)
                        b64_data = base64.b64encode(content).decode('ascii')
                        return f'src="data:{mime_type};base64,{b64_data}"'
            except Exception as e:
                print(f"Error processing image {src}: {e}")
            
            return match.group(0)
        
        # Replace img src attributes
        html_text = re.sub(r'src=["\']([^"\']+)["\']', replace_img, html_text, flags=re.IGNORECASE)
        return html_text

    def _get_mime_type(self, filename):
        """Get MIME type from filename"""
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
            toc_id = message.get_js_value().to_string()
            print(f"Section changed to: {toc_id}")
            GLib.idle_add(self._set_active, toc_id)
        except Exception as e:
            print(f"Error in section changed handler: {e}")
            # Try alternative
            try:
                toc_id = str(message.to_string())
                GLib.idle_add(self._set_active, toc_id)
            except Exception as e2:
                print(f"Alternative also failed: {e2}")

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
                header_row.set_focusable(True)
                try:
                    header_row.add_css_class("toc-expander-row")
                except Exception:
                    pass

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                header_box.set_margin_start(indent_px)
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
                    pass

                revealer = Gtk.Revealer()
                try:
                    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                except Exception:
                    pass
                revealer.set_reveal_child(False)

                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                child_container.set_margin_start(indent_px + 8)
                self._build_foliate_toc(child_container, children, level=level+1)
                revealer.set_child(child_container)

                self._revealers[toc_id] = (revealer, chev)

                def _make_toggle(tid, rev, ch):
                    def _toggle_and_nav():
                        try:
                            new_state = not rev.get_reveal_child()
                            rev.set_reveal_child(new_state)
                            ch.set_from_icon_name("go-down-symbolic" if new_state else "go-next-symbolic")
                            self._scroll_to_section(tid)
                        except Exception:
                            pass
                    return _toggle_and_nav

                toggle_fn = _make_toggle(toc_id, revealer, chev)

                try:
                    header_row.connect("activated", lambda w, fn=toggle_fn: fn())
                except Exception:
                    pass

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
                try:
                    lbl.set_max_width_chars(40)
                except Exception:
                    pass

                try:
                    row.set_child(lbl)
                except Exception:
                    pass

                row.set_activatable(True)
                row.connect("activated", lambda w, tid=toc_id: self._scroll_to_section(tid))

                if level:
                    cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    cont.set_margin_start(indent_px + 12)
                    cont.append(row)
                    parent_box.append(cont)
                else:
                    try:
                        row.set_margin_start(indent_px + 22)
                    except Exception:
                        pass
                    parent_box.append(row)

                if toc_id:
                    try:
                        row.add_css_class("toc-leaf")
                    except Exception:
                        pass
                    self._row_map[toc_id] = row

    def _scroll_to_section(self, toc_id):
        if not toc_id:
            return
        print(f"Scrolling to section: {toc_id}")
        js = f"window.scrollToSection('{toc_id}');"
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
            self._set_active(toc_id)
        except Exception as e:
            print(f"Error scrolling to section: {e}")
            # Try alternative approach
            try:
                self.webview.run_javascript(js, None, None, None)
                self._set_active(toc_id)
            except Exception as e2:
                print(f"Alternative scroll also failed: {e2}")

    def _build_header_actions(self):
        load_btn = Gtk.Button.new_with_label("Open EPUB")
        load_btn.connect("clicked", self._on_open_clicked)
        self.header.pack_start(load_btn)

        close_btn = Gtk.Button.new_with_label("Close Book")
        close_btn.connect("clicked", lambda *_: self.set_library_mode())
        self.header.pack_end(close_btn)

        toggle_btn = Gtk.Button.new_with_label("Toggle Sidebar")
        toggle_btn.connect("clicked", lambda *_: self._on_sidebar_toggle(toggle_btn))
        self.header.pack_end(toggle_btn)

    def _on_open_clicked(self, *_):
        dialog = Gtk.FileDialog(title="Open EPUB")
        filter_epub = Gtk.FileFilter()
        filter_epub.set_name("EPUB"); filter_epub.add_pattern("*.epub")
        dialog.set_default_filter(filter_epub)
        def on_file_chosen(dlg, res, *a):
            try:
                file = dlg.open_finish(res)
                if file:
                    path = file.get_path()
                    if path:
                        self.set_reading_mode(path)
            except Exception as e:
                self._show_error(f"Failed to open file: {e}")
        dialog.open(self, None, on_file_chosen)

    def _show_error(self, text):
        try:
            dlg = Adw.AlertDialog(heading="Error", body=text)
            dlg.add_response("ok", "OK")
            dlg.set_default_response("ok")
            dlg.present(self)
        except Exception as e:
            print(f"Error showing dialog: {e}")
            # Fallback to print
            print(f"ERROR: {text}")

    def _on_sidebar_toggle(self, btn):
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            self._user_hid_sidebar = not new
        except Exception:
            pass

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
