#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js
Fix: include JSZip before epub.min.js to avoid "JSZip lib not loaded".
Place local copies of jszip.min.js and epub.min.js next to this script if you want offline usage.
"""
import os
import base64
import tempfile
import pathlib
import sys

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

        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        scrolled = Gtk.ScrolledWindow()
        main_box.append(scrolled)

        self.webview = WebKit.WebView()
        self.setup_webview()
        self.webview.set_vexpand(True)
        scrolled.set_child(self.webview)

        if file_path:
            self.load_file(file_path)

    def setup_webview(self):
        settings = self.webview.get_settings()
        try:
            settings.set_enable_javascript(True)
        except Exception:
            pass
        # try enabling file access if available
        for name in ("set_allow_file_access_from_file_urls",
                     "set_allow_universal_access_from_file_urls",
                     "set_enable_write_console_messages_to_stdout"):
            try:
                getattr(settings, name)(True)
            except Exception:
                pass

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
        # Prepare JS library inclusion (JSZip must come before epub.js)
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

        # Encode EPUB as data URI (valid data: prefix)
        b64 = self.encode_file(epub_path)
        data_uri = f"data:application/epub+zip;base64,{b64}"

        # For large files, consider temp file fallback (try fetch on data URI first)
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

        # HTML that logs JSZip presence and fetches the source -> ArrayBuffer -> ePub(arrayBuffer)
        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPUB Reader</title>
  <style>html,body{{height:100%;margin:0;padding:0;background:#fff}}#viewer{{width:100vw;height:100vh}}</style>
</head>
<body>
  <div id="viewer"></div>

  <!-- Load JSZip first (required by epub.js) -->
  {jszip_snippet}
  <!-- Then epub.js -->
  {epubjs_snippet}

  <script>
    (function(){{
      console.log("Libraries: JSZip {jszip_note}, epub.js {epubjs_note}");
      console.log("JSZip available?", typeof JSZip !== "undefined");

      const src = "{source_to_open}";

      function openArrayBuffer(buf) {{
        try {{
          console.log("Calling ePub with ArrayBuffer (size:", buf.byteLength, ")");
          const book = ePub(buf);
          const rendition = book.renderTo("viewer", {{ width: "100%", height: "100%" }});
          rendition.display();
          book.loaded.navigation.then(nav => console.log("TOC length:", nav.toc ? nav.toc.length : nav));
        }} catch (e) {{
          console.error("EPUB rendering error:", e);
          document.body.innerHTML = "<h2>EPUB rendering error (see console)</h2><pre>" + String(e) + "</pre>";
        }}
      }}

      fetch(src).then(r => {{
        if (!r.ok) throw new Error("Fetch failed: " + r.status + " " + r.statusText);
        return r.arrayBuffer();
      }}).then(ab => {{
        console.log("Fetched EPUB arrayBuffer, size=", ab.byteLength);
        openArrayBuffer(ab);
      }}).catch(fetchErr => {{
        console.error("Fetch->ArrayBuffer failed:", fetchErr);
        // fallback: try passing src directly (works if WebKit can open file:// or if CDN URL)
        try {{
          console.log("Fallback: passing src directly to ePub:", src);
          const book = ePub(src);
          const rendition = book.renderTo("viewer", {{ width: "100%", height: "100%" }});
          rendition.display();
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

