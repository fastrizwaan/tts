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

    def populate_toc(self, toc_data):
        print(f"[DEBUG] populate_toc called with {len(toc_data)} items")
        
        # GTK4 way to clear listbox - remove children one by one
        child = self.toc_listbox.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.toc_listbox.remove(child)
            child = next_child
        
        for item in toc_data:
            label_text = item.get('label') if isinstance(item, dict) else str(item)
            href = item.get('href', '') if isinstance(item, dict) else ''
            print(f"[DEBUG] Adding TOC item: {label_text} -> {href}")
            
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=label_text or 'Unknown', halign=Gtk.Align.START, margin_top=6, margin_bottom=6)
            row.set_child(label)
            if href:
                row.connect('activate', lambda r, h=href: self.go_to_chapter(h))
            self.toc_listbox.append(row)
        
        self.toc_sidebar.set_visible(True)
        print(f"[DEBUG] TOC sidebar visible, {len(toc_data)} items added")

    def go_to_chapter(self, href):
        js_code = f"if(window.rendition) window.rendition.display('{href}');"
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

        b64 = self.encode_file(epub_path)
        data_uri = f"data:application/epub+zip;base64,{b64}"

        use_temp_file = len(b64) > (6 * 1024 * 1024)
        temp_file_url = None
        if use_temp_file:
            try:
                tf = tempfile.NamedTemporaryFile(delete=False, suffix=".epub")
                tf.write(base64.b64decode(b64))
                tf.flush()
                tf.close()
                temp_file_url = pathlib.Path(tf.name).as_uri()
                print(f"[info] using temp file for EPUB: {tf.name}")
            except Exception as e:
                print(f"[warn] temp file fallback failed: {e}")
                temp_file_url = None

        source_to_open = temp_file_url if temp_file_url else data_uri

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
      const src = "{source_to_open}";

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

      function openArrayBuffer(buf) {{
        try {{
          const book = ePub(buf);
          window.rendition = book.renderTo("viewer", {{ width: "100%", height: "100%" }});
          window.rendition.display();

          book.loaded.navigation.then(function(nav){{
            console.log("epub.js nav:", nav);
            var toc = nav.toc || nav;
            console.log("Extracted TOC:", toc);
            sendTOCString(toc);
          }});

          window.rendition.on('relocated', function(location) {{
            sendNavString({{ current: location.start.cfi, percent: Math.round(location.start.percentage * 100) }});
          }});

          window.goToChapter = function(href) {{ window.rendition.display(href); }};
        }} catch (e) {{
          console.error("EPUB rendering error:", e);
          document.body.innerHTML = "<h2>EPUB rendering error (see console)</h2><pre>" + String(e) + "</pre>";
        }}
      }}

      fetch(src).then(function(r){{
        if (!r.ok) throw new Error("Fetch failed: " + r.status + " " + r.statusText);
        return r.arrayBuffer();
      }}).then(function(ab){{ openArrayBuffer(ab); }}).catch(function(fetchErr){{
        try {{
          const book = ePub(src);
          window.rendition = book.renderTo("viewer", {{ width: "100%", height: "100%" }});
          window.rendition.display();
          book.loaded.navigation.then(function(nav){{
            console.log("epub.js nav (fallback):", nav);
            var toc = nav.toc || nav;
            sendTOCString(toc);
          }});
        }} catch (e) {{
          console.error("Fallback ePub(src) failed:", e);
          document.body.innerHTML = "<h2>Failed to open EPUB</h2><pre>" + String(fetchErr) + "\\n\\n" + String(e) + "</pre>";
        }}
      }});
    }})();
  </script>
</body>
</html>"""

        self.webview.load_html(html_content, "file:///")

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
        super().__init__(application_id="com.example.EpubReader")
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
