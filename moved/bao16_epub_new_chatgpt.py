#!/usr/bin/env python3
import os
import gi
import zipfile
import xml.etree.ElementTree as ET
import base64
import mimetypes
from pathlib import Path
from lxml import html
import urllib.parse # <-- Import for handling URL-encoded paths

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, WebKit, GLib, Gdk
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

# XML Namespaces for parsing ePub files
NS = {
    'OPF': 'http://www.idpf.org/2007/opf',
    'DC': 'http://purl.org/dc/elements/1.1/',
    'CONTAINER': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'NCX': 'http://www.daisy.org/z3986/2005/ncx/',
    'XHTML': 'http://www.w3.org/1999/xhtml',
    'EPUB': 'http://www.idpf.org/2007/ops' # <-- FIXED: Added EPUB namespace for epub:type
}

class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="ePub Reader Demo")
        self.set_default_size(1024, 768)
        
        self.epub_zip = None
        self.opf_path = None
        self.toc = []

        # --- UI Setup ---
        self.split = Adw.OverlaySplitView(show_sidebar=True, min_sidebar_width=250)
        self.set_content(self.split)
        
        self.sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrolled_window = Gtk.ScrolledWindow(child=self.sidebar_content, hexpand=True, vexpand=True)
        self.split.set_sidebar(scrolled_window)
        
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        
        toggle_sidebar_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        toggle_sidebar_btn.connect("clicked", self.on_toggle_sidebar)
        header.pack_start(toggle_sidebar_btn)
        
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_file)
        header.pack_start(open_btn)
        
        string_list = Gtk.StringList()
        for i in range(1, 11):
            string_list.append(f"{i} Column{'s' if i > 1 else ''}")
        
        self.columns_combo = Gtk.DropDown(model=string_list, selected=1)
        self.columns_combo.connect("notify::selected", self.on_columns_changed)
        header.pack_end(self.columns_combo)
        
        self.title_widget = Adw.WindowTitle(title="ePub Reader", subtitle="Open an ePub file")
        header.set_title_widget(self.title_widget)
        toolbar.add_top_bar(header)
        
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        
        content_manager = self.webview.get_user_content_manager()
        content_manager.connect("script-message-received::scrollEvent", self.on_scroll_event_received)
        content_manager.register_script_message_handler("scrollEvent")
        
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an ePub file to view.</p></body></html>")
        
        self.original_html_content = "<h1>Welcome</h1><p>Select an ePub file to view.</p>"
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.webview.set_margin_top(10)
        self.webview.set_margin_bottom(10)
        self.webview.set_margin_start(10)
        self.webview.set_margin_end(10)
        content_box.append(self.webview)
        
        toolbar.set_content(content_box)
        
        self.split.set_content(toolbar)
        
        self.current_columns = 2
        self.columns_combo.set_selected(self.current_columns - 1)
        GLib.idle_add(lambda: self.apply_column_layout(self.current_columns - 1))

        self.connect("notify::default-width", self.on_size_changed)
        self.connect("notify::default-height", self.on_size_changed)
        
        self.breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 800px")
        )
        self.breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.breakpoint)
        
    def on_toggle_sidebar(self, button):
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        GLib.timeout_add(350, lambda: self.apply_column_layout(self.current_columns - 1))
    
    def on_scroll_event_received(self, content_manager, js_result):
        try:
            import json
            event_data = json.loads(js_result.to_string())
            event_type = event_data.get('type', 'unknown')
            scroll_x = event_data.get('scrollX', 0)
            scroll_y = event_data.get('scrollY', 0)
            column = event_data.get('column', 0)
            
            icons = {
                'wheel': '🖱️ ', 'wheel-y': '↕️ ', 'arrow-left': '⬅️ ',
                'arrow-right': '➡️', 'page-up': '⬆️ ', 'page-down': '⬇️',
                'home': '🏠', 'end': '🔚'
            }
            icon = icons.get(event_type, '📜')
            
            if event_type.startswith('wheel'):
                print(f"{icon} Scroll Event: {event_type:12s} | ScrollY: {scroll_y:5.0f}")
            else:
                print(f"{icon} Scroll Event: {event_type:12s} | ScrollX: {scroll_x:5.0f} | Column: {column}")
        except Exception as e:
            print(f"Error receiving scroll event: {e}")
    
    def on_open_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open ePub File")
        
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("ePub files")
        epub_filter.add_pattern("*.epub")
        
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        dialog.set_filters(filters)
        
        dialog.open(self, None, self.on_file_dialog_response)
    
    def on_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                def load_epub_in_thread():
                    try:
                        path = file.get_path()
                        self.load_epub(path)
                    except Exception as e:
                        print(f"Error reading file: {e}")
                        GLib.idle_add(lambda: self.show_error_dialog(f"Error loading ePub: {e}"))
                
                GLib.Thread.new(None, load_epub_in_thread)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                 self.show_error_dialog(f"Error opening file: {e.message}")

    def show_error_dialog(self, message):
        """FIXED: Use the modern API for Adw.MessageDialog."""
        error_dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Could not load file",
            body=str(message)
        )
        # The new way: add a button with a response ID, then connect to the response signal.
        error_dialog.add_button("Close", "close")
        error_dialog.connect("response", lambda d, r: d.close())
        error_dialog.present()

    def load_epub(self, path):
        try:
            self.epub_zip = zipfile.ZipFile(path, 'r')
            
            container_xml = self.epub_zip.read('META-INF/container.xml')
            container_root = ET.fromstring(container_xml)
            opf_path_element = container_root.find('CONTAINER:rootfiles/CONTAINER:rootfile', NS)
            if opf_path_element is None:
                raise ValueError("Could not find OPF file path in container.xml")
            self.opf_path = Path(opf_path_element.attrib['full-path'])

            opf_xml = self.epub_zip.read(self.opf_path.as_posix())
            opf_root = ET.fromstring(opf_xml)

            title_element = opf_root.find('.//DC:title', NS)
            book_title = title_element.text if title_element is not None else Path(path).stem

            manifest_items = {item.attrib['id']: item.attrib['href'] for item in opf_root.findall('.//OPF:item', NS)}
            spine_ids = [item.attrib['idref'] for item in opf_root.findall('.//OPF:spine/OPF:itemref', NS)]
            
            self.toc = self._parse_toc(opf_root, manifest_items)

            full_body_content = ""
            for item_id in spine_ids:
                html_path_str = manifest_items.get(item_id)
                if not html_path_str: continue

                html_path = self.opf_path.parent / html_path_str
                try:
                    html_bytes = self.epub_zip.read(html_path.as_posix())
                    processed_html = self._process_html_part(html_bytes, html_path)
                    full_body_content += processed_html
                except KeyError:
                    print(f"Warning: Could not find '{html_path.as_posix()}' in ePub archive.")
            
            self.original_html_content = full_body_content
            
            GLib.idle_add(self._update_ui_after_load, book_title)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            GLib.idle_add(self.show_error_dialog, f"Failed to parse ePub: {e}")

    def _parse_toc(self, opf_root, manifest_items):
        nav_item = opf_root.find(".//*[@properties='nav']", NS)
        if nav_item is not None:
            nav_path_str = nav_item.attrib['href']
            nav_path = (self.opf_path.parent / nav_path_str).as_posix()
            nav_xml = self.epub_zip.read(nav_path)
            return self._parse_nav_xhtml(nav_xml, Path(nav_path).parent)
        
        spine = opf_root.find('OPF:spine', NS)
        if spine is not None:
            ncx_id = spine.attrib.get('toc')
            if ncx_id and ncx_id in manifest_items:
                ncx_path_str = manifest_items[ncx_id]
                ncx_path = (self.opf_path.parent / ncx_path_str).as_posix()
                ncx_xml = self.epub_zip.read(ncx_path)
                return self._parse_ncx(ncx_xml, Path(ncx_path).parent)
        
        return []

    def _parse_nav_xhtml(self, nav_xml, base_path):
        toc = []
        root = ET.fromstring(nav_xml)
        # FIXED: Use the correct, specific epub:type attribute selector, which is supported.
        for nav_point in root.findall('.//XHTML:nav[@EPUB:type="toc"]//XHTML:a', NS):
            label = ''.join(nav_point.itertext()).strip()
            href = nav_point.attrib.get('href', '')
            if label and href:
                full_href = (base_path / href).as_posix()
                toc.append({'label': label, 'href': full_href})
        return toc

    def _parse_ncx(self, ncx_xml, base_path):
        toc = []
        root = ET.fromstring(ncx_xml)
        for nav_point in root.findall('.//NCX:navPoint', NS):
            label = nav_point.find('.//NCX:text', NS).text.strip()
            href = nav_point.find('.//NCX:content', NS).attrib.get('src', '')
            if label and href:
                full_href = (base_path / href).as_posix()
                toc.append({'label': label, 'href': full_href})
        return toc
        
    def _process_html_part(self, html_bytes, base_path):
        try:
            root = html.fromstring(html_bytes)
        except Exception as e:
            print(f"lxml failed to parse {base_path}, falling back. Error: {e}")
            return html_bytes.decode('utf-8', errors='ignore')

        body = root.find('body')
        if body is None:
            body = root

        for img in body.iter('img'):
            src = img.attrib.get('src')
            if not src or src.startswith('data:'):
                continue
            
            try:
                # FIXED: Do NOT use .resolve(). Paths in the zip are relative.
                # Use urllib.parse.unquote to handle URL-encoded characters like %20.
                decoded_src = urllib.parse.unquote(src)
                img_path = (base_path.parent / decoded_src).as_posix()
                
                img_data = self.epub_zip.read(img_path)
                mime_type, _ = mimetypes.guess_type(img_path)
                if not mime_type: mime_type = 'application/octet-stream'
                
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                img.attrib['src'] = f"data:{mime_type};base64,{img_base64}"
            except Exception as e:
                print(f"Could not embed image '{src}': {e}")

        if len(list(body)) > 0:
            first_element = body[0]
            first_element.attrib['id'] = base_path.as_posix().replace('/', '_').replace('.', '_')
        
        inner_html = "".join([html.tostring(child, encoding='unicode') for child in body])
        return inner_html

    def _update_ui_after_load(self, book_title):
        self.title_widget.set_subtitle(book_title)
        
        child = self.sidebar_content.get_first_child()
        while child:
            self.sidebar_content.remove(child)
            child = self.sidebar_content.get_first_child()

        if self.toc:
            group = Adw.PreferencesGroup(title="Table of Contents")
            self.sidebar_content.append(group)
            for item in self.toc:
                row = Adw.ActionRow(title=item['label'])
                row.set_activatable(True)
                row.connect("activated", self.on_toc_item_clicked, item['href'])
                group.add(row)
        else:
            self.sidebar_content.append(Gtk.Label(label="No Table of Contents found."))
        
        self.apply_column_layout(self.current_columns - 1)

    def on_toc_item_clicked(self, row, href):
        parts = href.split('#')
        file_part = parts[0]
        anchor_part = parts[1] if len(parts) > 1 else None
        
        target_id = anchor_part or file_part.replace('/', '_').replace('.', '_')
        
        js = f"""
        const targetElement = document.getElementById('{target_id}');
        if (targetElement) {{
            targetElement.scrollIntoView({{ behavior: 'smooth' }});
        }} else {{
            console.warn('Could not find element with ID: {target_id}');
        }}
        """
        self.webview.evaluate_javascript(js, -1, None, None, None)

        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def apply_column_layout(self, selected_column_index):
        num_columns = selected_column_index + 1
        self.current_columns = num_columns
        
        css = f"""
        <style>
            body {{
                font-family: sans-serif;
                margin: 0;
                width: 100%;
                height: 100%;
                overflow: {'auto' if num_columns == 1 else 'hidden'};
            }}
            .content-container {{
                column-count: {num_columns};
                column-gap: 40px;
                width: 100%;
                height: 100vh;
                box-sizing: border-box;
            }}
            .content-container h1, .content-container h2,
            .content-container h3, .content-container figure {{
                break-inside: avoid;
            }}
            .content-container img {{
                max-width: 100%;
                height: auto;
                display: block;
            }}
        </style>
        """
        
        js_script = f"""
        window.currentColumnCount = {num_columns};

        function getColumnWidth() {{
            const container = document.querySelector('.content-container');
            if (!container) return 0;
            const style = window.getComputedStyle(container);
            const colCount = window.currentColumnCount;
            const gap = parseFloat(style.columnGap) || 0;
            const totalGap = gap * (colCount - 1);
            return (container.offsetWidth - totalGap) / colCount + gap;
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
            if (window.currentColumnCount <= 1) return;
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const currentScroll = window.scrollX;
            const target = Math.round(currentScroll / colWidth) * colWidth;
            if (Math.abs(currentScroll - target) > 1) {{
                 window.scrollTo({{ top: window.scrollY, left: target, behavior: 'smooth' }});
            }}
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) snapScroll();
            }}, 150);
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
            const currentTarget = Math.round(window.scrollX / colWidth) * colWidth;
            const newTarget = currentTarget + scrollDist;
            smoothScrollTo(newTarget, window.scrollY);
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
                    case 'ArrowUp': e.preventDefault(); y = Math.max(0, y - 40); type = 'arrow-up'; break;
                    case 'ArrowDown': e.preventDefault(); y = Math.min(maxScrollY, y + 40); type = 'arrow-down'; break;
                    case 'PageUp': e.preventDefault(); y = Math.max(0, y - viewportH); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); y = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); y = maxScrollY; type = 'end'; break;
                }}
            }} else {{
                switch (e.key) {{
                    case 'ArrowLeft': e.preventDefault(); x = Math.max(0, Math.round((x - colWidth) / colWidth) * colWidth); type = 'arrow-left'; break;
                    case 'ArrowRight': e.preventDefault(); x = Math.min(maxScrollX, Math.round((x + colWidth) / colWidth) * colWidth); type = 'arrow-right'; break;
                    case 'PageUp': e.preventDefault(); x = Math.max(0, x - colWidth * 2); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); x = Math.min(maxScrollX, x + colWidth * 2); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); x = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); x = maxScrollX; type = 'end'; break;
                }}
            }}

            if (type) {{
                smoothScrollTo(x, y);
                setTimeout(() => sendScrollEvent(type), 450);
            }}
        }});
        """
        
        html_content = f"""
        <html>
            <head>
                <meta charset="UTF-8">
                {css}
            </head>
            <body>
                <div class="content-container">
                    {self.original_html_content}
                </div>
                <script>{js_script}</script>
            </body>
        </html>
        """
        
        self.webview.load_html(html_content)
    
    def on_columns_changed(self, combo, pspec):
        selected = combo.get_selected()
        GLib.timeout_add(50, lambda: self.apply_column_layout(selected))
    
    def on_size_changed(self, *args):
        if hasattr(self, '_size_change_timeout'):
            GLib.source_remove(self._size_change_timeout)
        self._size_change_timeout = GLib.timeout_add(200, lambda: self.apply_column_layout(self.current_columns - 1))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubReader",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
    
    def do_activate(self, *a):
        if not hasattr(self, 'win') or not self.win.is_visible():
            self.win = Win(self)
        self.win.present()

if __name__ == "__main__":
    import sys
    app = App()
    sys.exit(app.run(sys.argv))
