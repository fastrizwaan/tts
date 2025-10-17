#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js
Fixed: reliably send TOC as a JSON string from the page and robustly extract it in Python.
Put local jszip.min.js and epub.min.js next to this file to use offline copies.
"""
import os
import base64
import tempfile
import pathlib
import sys
import json

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, WebKit, GLib, Adw

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_JSZIP = HERE / "jszip.min.js"
LOCAL_EPUBJS = HERE / "epub.min.js"

class EpubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, application, file_path=None):
        super().__init__(application=application)
        self.set_default_size(1200, 800)
        self.set_title("EPUB/HTML Reader")
        self.temp_dir = None  # Store temp directory for cleanup
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_bar.pack_start(nav_box)

        self.prev_button = Gtk.Button(label="←")
        self.prev_button.set_tooltip_text("Previous chapter")
        self.prev_button.connect("clicked", self.go_prev)
        nav_box.append(self.prev_button)

        self.next_button = Gtk.Button(label="→")
        self.next_button.set_tooltip_text("Next chapter")
        self.next_button.connect("clicked", self.go_next)
        nav_box.append(self.next_button)

        self.toc_button = Gtk.Button(icon_name="view-list-symbolic")
        self.toc_button.set_tooltip_text("Table of Contents")
        self.toc_button.connect("clicked", self.toggle_toc)
        header_bar.pack_end(self.toc_button)

        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        self.split_view = Adw.OverlaySplitView()
        main_box.append(self.split_view)

        self.toc_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toc_sidebar.set_size_request(250, -1)
        self.toc_sidebar.set_visible(False)
        self.split_view.set_sidebar(self.toc_sidebar)

        toc_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toc_header.append(Gtk.Label(label="Table of Contents", hexpand=True))
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.connect("clicked", lambda b: self.split_view.set_collapsed(True))
        toc_header.append(close_btn)
        self.toc_sidebar.append(toc_header)

        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_vexpand(True)
        self.toc_sidebar.append(toc_scroll)
        self.toc_listbox = Gtk.ListBox()
        self.toc_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        toc_scroll.set_child(self.toc_listbox)

        scrolled = Gtk.ScrolledWindow()
        self.split_view.set_content(scrolled)
        self.webview = WebKit.WebView()
        self.setup_webview()
        self.webview.set_vexpand(True)
        scrolled.set_child(self.webview)

        self.user_manager = self.webview.get_user_content_manager()
        # register handlers before injecting script
        self.user_manager.register_script_message_handler("tocLoaded")
        self.user_manager.register_script_message_handler("navChanged")
        self.user_manager.connect("script-message-received::tocLoaded", self.on_toc_loaded)
        self.user_manager.connect("script-message-received::navChanged", self.on_nav_changed)

        # forward postMessage payloads to registered handlers if host uses postMessage
        forwarder = WebKit.UserScript(
            """
            (function(){
              window.addEventListener('message', function(event) {
                try {
                  if (!event.data) return;
                  // if host expects a JSON-string payload, forward as-is
                  if (event.data.type === 'tocLoaded') {
                    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tocLoaded) {
                      // ensure we send a string
                      var payload = event.data.payload;
                      if (typeof payload !== 'string') payload = JSON.stringify(payload);
                      window.webkit.messageHandlers.tocLoaded.postMessage(payload);
                    }
                  } else if (event.data.type === 'navChanged') {
                    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.navChanged) {
                      var payload = event.data.payload;
                      if (typeof payload !== 'string') payload = JSON.stringify(payload);
                      window.webkit.messageHandlers.navChanged.postMessage(payload);
                    }
                  }
                } catch(e) {}
              }, false);
            })();
            """,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.START,
            None,
            None
        )
        self.user_manager.add_script(forwarder)

        if file_path:
            self.load_file(file_path)

    def setup_webview(self):
        settings = self.webview.get_settings()
        try:
            settings.set_enable_javascript(True)
        except Exception:
            pass
        for name in ("set_allow_file_access_from_file_urls",
                     "set_allow_universal_access_from_file_urls",
                     "set_enable_write_console_messages_to_stdout"):
            try:
                getattr(settings, name)(True)
            except Exception:
                pass

    def _extract_message_string(self, message):
        try:
            # Try to get JSCValue directly if message is the JSCValue
            if hasattr(message, "to_string"):
                try:
                    return message.to_string()
                except Exception:
                    pass
                try:
                    return message.to_json(0)
                except Exception:
                    pass
            
            # Try get_js_value() method
            if hasattr(message, "get_js_value"):
                jsval = message.get_js_value()
                try:
                    return jsval.to_string()
                except Exception:
                    pass
                try:
                    return jsval.to_json(0)
                except Exception:
                    pass
            
            # Try GLib.Variant
            if isinstance(message, GLib.Variant):
                v = message.unpack()
                if isinstance(v, (str, bytes)):
                    return v.decode() if isinstance(v, bytes) else v
                return json.dumps(v)
            
            # Try get_string()
            if hasattr(message, "get_string"):
                try:
                    s = message.get_string()
                    if s is not None:
                        return s
                except Exception:
                    pass
            
            return str(message)
        except Exception as e:
            print("extract_message_string error:", e)
            import traceback
            traceback.print_exc()
            return None

    def on_toc_loaded(self, manager, message):
        try:
            raw = self._extract_message_string(message)
            if not raw:
                print("on_toc_loaded: empty payload")
                return
            print(f"[DEBUG] Raw TOC payload: {raw[:200]}...")  # Debug output
            
            # The page sends a JSON string payload; ensure we get an actual list
            # handle double-encoding: payload may be a JSON string inside a JSON wrapper
            try:
                # if raw itself is a quoted JSON string, unquote once
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass
            # now raw should be a JSON string or JSON array text
            toc = None
            try:
                toc = json.loads(raw)
            except Exception:
                # if raw is already a list/obj stringified via str(), attempt eval-like fallback
                try:
                    toc = json.loads(raw.replace("'", '"'))
                except Exception:
                    toc = None
            if toc is None:
                print("on_toc_loaded: failed to decode toc payload")
                return
            
            print(f"[DEBUG] Parsed TOC: {toc}")  # Debug output
            
            # epub.js gives nav.toc as array of entries {label, href, ...}
            if isinstance(toc, dict) and "toc" in toc:
                toc = toc["toc"]
            if not isinstance(toc, list):
                toc = [toc]
            
            print(f"[DEBUG] Final TOC list length: {len(toc)}")  # Debug output
            self.populate_toc(toc)
        except Exception as e:
            print(f"Error processing TOC: {e}")
            import traceback
            traceback.print_exc()

    def on_nav_changed(self, manager, message):
        try:
            raw = self._extract_message_string(message)
            if not raw:
                return
            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass
            try:
                nav = json.loads(raw)
            except Exception:
                try:
                    nav = json.loads(raw.replace("'", '"'))
                except Exception:
                    nav = None
            # handle nav as desired (no-op here)
        except Exception as e:
            print(f"Error processing nav: {e}")

    def populate_toc(self, toc_data, parent_box=None, level=0):
        """Recursively populate TOC with nested items"""
        if parent_box is None:
            print(f"[DEBUG] populate_toc called with {len(toc_data)} items")
            
            # GTK4 way to clear listbox - remove children one by one
            child = self.toc_listbox.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.toc_listbox.remove(child)
                child = next_child
            
            parent_box = self.toc_listbox
        
        for item in toc_data:
            label_text = item.get('label', '').strip() if isinstance(item, dict) else str(item).strip()
            href = item.get('href', '') if isinstance(item, dict) else ''
            subitems = item.get('subitems', []) if isinstance(item, dict) else []
            
            if not label_text:
                label_text = 'Unknown'
            
            print(f"[DEBUG] Adding TOC item (level {level}): {label_text} -> {href}")
            
            # Create expander for items with subitems
            if subitems and len(subitems) > 0:
                row = Gtk.ListBoxRow()
                row.set_activatable(bool(href))
                
                expander = Gtk.Expander()
                expander.set_label(label_text)
                expander.set_margin_start(12 + (level * 16))
                expander.set_margin_end(12)
                expander.set_margin_top(4)
                expander.set_margin_bottom(4)
                
                # Create a box for subitems
                subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                expander.set_child(subitems_box)
                
                row.set_child(expander)
                
                if href:
                    row.href = href
                    # Make the expander label clickable
                    expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))
                
                parent_box.append(row)
                
                # Recursively add subitems
                for subitem in subitems:
                    self.add_toc_subitem(subitems_box, subitem, level + 1)
                    
            else:
                # Simple row without subitems
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                
                label = Gtk.Label(
                    label=label_text,
                    halign=Gtk.Align.START,
                    margin_top=6,
                    margin_bottom=6,
                    margin_start=12 + (level * 16),
                    margin_end=12,
                    wrap=True,
                    xalign=0
                )
                
                row.set_child(label)
                
                if href:
                    row.href = href
                
                parent_box.append(row)
        
        if parent_box == self.toc_listbox:
            # Connect the activated signal once for the whole listbox (disconnect previous if exists)
            try:
                self.toc_listbox.disconnect_by_func(self.on_toc_row_activated)
            except:
                pass
            self.toc_listbox.connect('row-activated', self.on_toc_row_activated)
            
            self.toc_sidebar.set_visible(True)
            print(f"[DEBUG] TOC sidebar visible, {len(toc_data)} items added")
    
    def add_toc_subitem(self, parent_box, item, level):
        """Add a single TOC subitem"""
        label_text = item.get('label', '').strip() if isinstance(item, dict) else str(item).strip()
        href = item.get('href', '') if isinstance(item, dict) else ''
        subitems = item.get('subitems', []) if isinstance(item, dict) else []
        
        if not label_text:
            label_text = 'Unknown'
        
        if subitems and len(subitems) > 0:
            expander = Gtk.Expander()
            expander.set_label(label_text)
            expander.set_margin_start(level * 16)
            expander.set_margin_end(12)
            expander.set_margin_top(2)
            expander.set_margin_bottom(2)
            
            subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            expander.set_child(subitems_box)
            
            if href:
                expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))
            
            parent_box.append(expander)
            
            for subitem in subitems:
                self.add_toc_subitem(subitems_box, subitem, level + 1)
        else:
            button = Gtk.Button(label=label_text)
            button.set_has_frame(False)
            button.set_halign(Gtk.Align.START)
            button.set_margin_start(level * 16)
            button.set_margin_end(12)
            button.set_margin_top(2)
            button.set_margin_bottom(2)
            
            # Make label wrap and align left
            child = button.get_child()
            if child and isinstance(child, Gtk.Label):
                child.set_wrap(True)
                child.set_xalign(0)
            
            if href:
                button.connect('clicked', lambda b, h=href: self.go_to_chapter(h))
            
            parent_box.append(button)
    
    def on_toc_row_activated(self, listbox, row):
        """Handle TOC item click"""
        if hasattr(row, 'href') and row.href:
            print(f"[DEBUG] Navigating to: {row.href}")
            self.go_to_chapter(row.href)

    def go_to_chapter(self, href):
        """Navigate to a chapter by href"""
        # Escape single quotes in href for JavaScript
        escaped_href = href.replace("'", "\\'")
        js_code = f"if(window.rendition) {{ window.rendition.display('{escaped_href}'); }}"
        print(f"[DEBUG] Executing JS: {js_code}")
        try:
            self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)

    def toggle_toc(self, button):
        is_collapsed = self.split_view.get_collapsed()
        self.split_view.set_collapsed(not is_collapsed)

    def go_prev(self, button):
        js = "if(window.rendition) window.rendition.prev();"
        try:
            self.webview.evaluate_javascript(js, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js, len(js), None, None, None, None, None, None)

    def go_next(self, button):
        js = "if(window.rendition) window.rendition.next();"
        try:
            self.webview.evaluate_javascript(js, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js, len(js), None, None, None, None, None, None)

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        filter = Gtk.FileFilter()
        filter.set_name("EPUB and HTML files")
        filter.add_pattern("*.epub")
        filter.add_pattern("*.html")
        filter.add_pattern("*.htm")
        dialog.set_default_filter(filter)
        dialog.open(self, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            self.load_file(file.get_path())
        except GLib.Error:
            pass

    def load_file(self, file_path):
        file_ext = pathlib.Path(file_path).suffix.lower()
        if file_ext == '.epub':
            self.load_epub(file_path)
        elif file_ext in ['.html', '.htm']:
            self.load_html(file_path)
        else:
            print(f"Unsupported file type: {file_ext}")

    def load_epub(self, epub_path):
        import zipfile
        import shutil
        
        # Clean up previous temp directory
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"[warn] Failed to clean up temp dir: {e}")
        
        # Extract EPUB to temporary directory
        self.temp_dir = tempfile.mkdtemp(prefix="epub_reader_")
        try:
            with zipfile.ZipFile(epub_path, 'r') as zip_ref:
                zip_ref.extractall(self.temp_dir)
            print(f"[info] Extracted EPUB to: {self.temp_dir}")
        except Exception as e:
            print(f"[error] Failed to extract EPUB: {e}")
            return
        
        # Get the extracted path as file:// URI
        extracted_uri = pathlib.Path(self.temp_dir).as_uri()
        
        if LOCAL_JSZIP.exists():
            jszip_snippet = f"<script>{LOCAL_JSZIP.read_text(encoding='utf-8')}</script>"
            jszip_note = "[local]"
        else:
            jszip_snippet = '<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>'
            jszip_note = "[cdn]"

        if LOCAL_EPUBJS.exists():
            epubjs_snippet = f"<script>{LOCAL_EPUBJS.read_text(encoding='utf-8')}</script>"
            epubjs_note = "[local]"
        else:
            epubjs_snippet = '<script src="https://cdn.jsdelivr.net/npm/epubjs@0.3.92/dist/epub.min.js"></script>'
            epubjs_note = "[cdn]"

        # Use the original epub file path
        epub_uri = pathlib.Path(epub_path).as_uri()

        # This page sends TOC as a JSON string payload to the host handlers.
        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPUB Reader</title>
  <style>
    html, body {{ height: 100%; margin: 0; padding: 0; background: #fff; }}
    #viewer {{ width: 100vw; height: 100vh; }}
    .epubjs-navigation {{ display: none; }}
  </style>
</head>
<body>
  <div id="viewer"></div>

  {jszip_snippet}
  {epubjs_snippet}

  <script>
    (function(){{
      console.log("Libraries: JSZip {jszip_note}, epub.js {epubjs_note}");
      const epubUrl = "{epub_uri}";
      const extractedPath = "{extracted_uri}";

      function sendTOCString(toc) {{
        try {{
          var payload = JSON.stringify(toc);
          console.log("Sending TOC:", payload.substring(0, 200));
          if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tocLoaded) {{
            window.webkit.messageHandlers.tocLoaded.postMessage(payload);
          }} else {{
            window.postMessage({{ type: 'tocLoaded', payload: payload }}, '*');
          }}
        }} catch (e) {{ console.error('sendTOCString error', e); }}
      }}

      function sendNavString(nav) {{
        try {{
          var payload = JSON.stringify(nav);
          if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.navChanged) {{
            window.webkit.messageHandlers.navChanged.postMessage(payload);
          }} else {{
            window.postMessage({{ type: 'navChanged', payload: payload }}, '*');
          }}
        }} catch (e) {{ console.error('sendNavString error', e); }}
      }}

      try {{
        // Open EPUB from the original file path
        const book = ePub(epubUrl);
        
        window.rendition = book.renderTo("viewer", {{ 
          width: "100%", 
          height: "100%"
        }});
        
        window.rendition.display();

        book.loaded.navigation.then(function(nav){{
          console.log("epub.js nav:", nav);
          var toc = nav.toc || nav;
          console.log("Extracted TOC:", toc);
          sendTOCString(toc);
        }}).catch(function(err) {{
          console.error("Navigation loading error:", err);
        }});

        window.rendition.on('relocated', function(location) {{
          sendNavString({{ 
            current: location.start.cfi, 
            percent: Math.round(location.start.percentage * 100) 
          }});
        }});

        window.goToChapter = function(href) {{ 
          console.log("goToChapter called with:", href);
          window.rendition.display(href); 
        }};
      }} catch (e) {{
        console.error("EPUB rendering error:", e);
        document.body.innerHTML = "<h2>EPUB rendering error</h2><pre>" + String(e) + "</pre>";
      }}
    }})();
  </script>
</body>
</html>"""

        self.webview.load_html(html_content, "file:///")
    
    def __del__(self):
        """Cleanup temp directory on destruction"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                import shutil
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

    def load_html(self, html_path):
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            base_uri = f"file://{pathlib.Path(html_path).parent.absolute()}/"
            self.webview.load_html(html_content, base_uri)
        except Exception as e:
            print(f"Error loading HTML file: {e}")
            self.webview.load_html(f"<html><body><h1>Error loading file: {e}</h1></body></html>", "file:///")

    def encode_file(self, file_path):
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')

class EpubReader(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        file_path = None
        if len(sys.argv) > 1:
            file_path = sys.argv[1]
        win = EpubViewerWindow(application=app, file_path=file_path)
        win.present()

def main():
    app = EpubReader()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()
