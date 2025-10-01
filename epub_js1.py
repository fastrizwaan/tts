#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js
Includes TOC, Prev/Next buttons, chapter label, keyboard navigation.
Place local copies of jszip.min.js and epub.min.js next to this script for offline usage.
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

        # Encode EPUB as data URI
        b64 = self.encode_file(epub_path)
        data_uri = f"data:application/epub+zip;base64,{b64}"

        # For large files, temp file fallback
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

        # Enhanced HTML with toolbar, TOC, and navigation
        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPUB Reader</title>
  <style>
    :root {{ --toolbar-h:44px; --gap:8px; }}
    html,body{{height:100%;margin:0;padding:0;background:#fff;font-family:system-ui,Segoe UI,Roboto,Arial}}
    #toolbar{{height:var(--toolbar-h);display:flex;align-items:center;gap:var(--gap);padding:6px;box-sizing:border-box;border-bottom:1px solid #ddd;background:#f7f7f7}}
    #viewer{{width:100vw; height:calc(100vh - var(--toolbar-h)); overflow:hidden}}
    button,select{{height:32px;padding:4px 8px}}
    #chapterLabel{{font-weight:600;margin-left:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:60%}}
    #status{{margin-left:auto;font-size:0.9em;color:#666;padding-right:8px}}
  </style>
</head>
<body>
  <div id="toolbar">
    <button id="prevBtn" title="Previous (Left arrow)">◀ Prev</button>
    <button id="nextBtn" title="Next (Right arrow)">Next ▶</button>
    <select id="tocSelect"><option>Loading TOC...</option></select>
    <div id="chapterLabel">—</div>
    <div id="status">Libraries: JSZip {jszip_note}, epub.js {epubjs_note}</div>
  </div>
  <div id="viewer"></div>

  <!-- Load JSZip first (required by epub.js) -->
  {jszip_snippet}
  <!-- Then epub.js -->
  {epubjs_snippet}

  <script>
    (function(){{
      console.log("JSZip loaded?", typeof JSZip !== "undefined");
      const src = "{source_to_open}";

      let book = null;
      let rendition = null;
      let nav = null;

      function setChapterLabel(text) {{
        const lbl = document.getElementById('chapterLabel');
        lbl.textContent = text || '—';
        lbl.title = text || '';
      }}

      function findTocForHref(href) {{
        if (!nav || !nav.toc) return null;
        // match by ending part of href (href sometimes contains leading path)
        const clean = href && href.split('#')[0];
        for (const item of nav.toc) {{
          if (!item.href) continue;
          // exact or contains
          if (item.href.split('#')[0] === clean || clean.includes(item.href.split('#')[0]) || item.href.includes(clean)) {{
            return item;
          }}
        }}
        return null;
      }}

      function updateControls(location) {{
        try {{
          const prevBtn = document.getElementById('prevBtn');
          const nextBtn = document.getElementById('nextBtn');
          if (location && location.atStart) prevBtn.disabled = true; else prevBtn.disabled = false;
          if (location && location.atEnd) nextBtn.disabled = true; else nextBtn.disabled = false;
          // chapter label from TOC if possible
          const href = location && location.start && location.start.href ? location.start.href : null;
          const tocItem = href ? findTocForHref(href) : null;
          setChapterLabel(tocItem ? tocItem.label : (href || '—'));
        }} catch (e) {{
          console.warn("updateControls error", e);
        }}
      }}

      function populateTOC() {{
        const select = document.getElementById('tocSelect');
        select.innerHTML = '';
        if (!nav || !nav.toc || nav.toc.length === 0) {{
          const opt = document.createElement('option');
          opt.textContent = 'No TOC available';
          select.appendChild(opt);
          return;
        }}
        nav.toc.forEach((item, idx) => {{
          const opt = document.createElement('option');
          opt.value = item.href || idx;
          opt.textContent = (item.label || ('Chapter ' + (idx+1)));
          select.appendChild(opt);
        }});
      }}

      function openFromArrayBuffer(ab) {{
        try {{
          book = ePub(ab);
          setupBookAndRendition();
        }} catch (e) {{
          console.error("ePub(arrayBuffer) failed:", e);
          document.body.innerHTML = "<h2>EPUB rendering error (see console)</h2><pre>" + String(e) + "</pre>";
        }}
      }}

      function setupBookAndRendition() {{
        if (!book) return;
        // render
        rendition = book.renderTo("viewer", {{ width: "100%", height: "100%" }});
        rendition.display();

        // populate nav/TOC when available
        book.loaded.navigation.then(n => {{
          nav = n;
          console.log("Navigation loaded, TOC length:", (nav.toc && nav.toc.length) || 0);
          populateTOC();
        }}).catch(err => {{
          console.warn("Navigation not available:", err);
          nav = null;
          populateTOC();
        }});

        // when relocated update UI
        rendition.on("relocated", (location) => {{
          console.log("relocated", location);
          updateControls(location);
          // try to select the matching TOC option
          try {{
            const sel = document.getElementById('tocSelect');
            if (sel && nav && nav.toc) {{
              const href = location && location.start && location.start.href ? location.start.href.split('#')[0] : null;
              if (href) {{
                for (let i=0;i<sel.options.length;i++) {{
                  const optVal = sel.options[i].value.split('#')[0];
                  if (optVal === href || href.includes(optVal) || optVal.includes(href)) {{
                    sel.selectedIndex = i;
                    break;
                  }}
                }}
              }}
            }}
          }} catch(e){{}}
        }});

        // Prev / Next button wiring
        document.getElementById('prevBtn').addEventListener('click', () => rendition.prev());
        document.getElementById('nextBtn').addEventListener('click', () => rendition.next());

        // TOC select jump
        document.getElementById('tocSelect').addEventListener('change', (ev) => {{
          const v = ev.target.value;
          if (!v) return;
          try {{
            rendition.display(v);
          }} catch (e) {{
            console.error("Failed to display TOC href:", v, e);
          }}
        }});

        // keyboard navigation
        document.addEventListener('keydown', (ev) => {{
          if (ev.key === 'ArrowLeft') {{ rendition.prev(); ev.preventDefault(); }}
          else if (ev.key === 'ArrowRight') {{ rendition.next(); ev.preventDefault(); }}
        }});
      }}

      // try fetch then arrayBuffer -> ePub
      fetch(src).then(r => {{
        if (!r.ok) throw new Error("Fetch failed: " + r.status + " " + r.statusText);
        return r.arrayBuffer();
      }}).then(ab => {{
        console.log("Fetched EPUB arrayBuffer, size=", ab.byteLength);
        openFromArrayBuffer(ab);
      }}).catch(fetchErr => {{
        console.error("Fetch->ArrayBuffer failed:", fetchErr);
        // fallback: try passing src directly
        try {{
          console.log("Fallback: passing src directly to ePub:", src);
          book = ePub(src);
          setupBookAndRendition();
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

