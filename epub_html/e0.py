#!/usr/bin/env python3
import os
import gi, json, zipfile, tempfile, shutil
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, PangoCairo, Gdk

class Writer(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.viewer")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = ViewerWindow(application=self)
        win.present()

class ViewerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("HTML/EPUB Viewer")
        self.set_default_size(1000, 700)

        # temp extract dir for EPUBs
        self._epub_tempdir = None

        # WebView
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.webview = WebKit.WebView()
        scroll.set_child(self.webview)
        self.webview.load_html("<!doctype html><html><body><p></p></body></html>", "file:///")

        # Minimal UI: header with Open button and font family dropdown
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)
        toolbar_view = Adw.ToolbarView()
        main_box.append(toolbar_view)
        header = Adw.HeaderBar()
        header.add_css_class("flat-header")
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(controls)

        open_btn = Gtk.Button(icon_name="document-open")
        open_btn.add_css_class("flat")
        open_btn.connect("clicked", self.on_open_clicked)
        controls.append(open_btn)

        # font dropdown (font-family)
        font_map = PangoCairo.FontMap.get_default()
        families = font_map.list_families()
        font_names = sorted([f.get_name() for f in families])
        font_store = Gtk.StringList(strings=font_names)
        self.font_dropdown = Gtk.DropDown(model=font_store)
        default_index = font_names.index("Sans") if "Sans" in font_names else 0
        self.font_dropdown.set_selected(default_index)
        self.font_dropdown.add_css_class("flat")
        self.font_dropdown.connect("notify::selected", self.on_font_family_changed)
        controls.append(self.font_dropdown)

        # content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(scroll)
        toolbar_view.set_content(content_box)

        # cleanup on close
        self.connect("close-request", self.on_close_request)

    def on_open_clicked(self, btn):
        dialog = Gtk.FileDialog()
        filter_all = Gtk.FileFilter()
        filter_all.set_name("HTML / EPUB")
        filter_all.add_pattern("*.html")
        filter_all.add_pattern("*.htm")
        filter_all.add_pattern("*.epub")
        dialog.set_default_filter(filter_all)
        dialog.open(self, None, self.on_open_file_dialog_response)

    def on_open_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file:
                return
            uri = file.get_uri()
            basename = file.get_basename().lower()
            if basename.endswith('.epub'):
                self.load_epub(file)
            else:
                # HTML/HTM load directly
                file.load_contents_async(None, self.load_html_callback)
        except GLib.Error as e:
            print("Open error:", e.message)

    def load_html_callback(self, file, result):
        try:
            ok, content, _ = file.load_contents_finish(result)
            if ok:
                html = content.decode()
                base = file.get_uri() or "file:///"
                self._cleanup_epub_tempdir()
                self.webview.load_html(html, base)
        except GLib.Error as e:
            print("Load error:", e.message)

    def load_epub(self, gio_file):
        # extract epub to a temporary directory and load the first HTML/XHTML found
        self._cleanup_epub_tempdir()
        path = gio_file.get_path()
        if not path:
            # fall back to copying stream to temp file
            try:
                fd, tmp_epub = tempfile.mkstemp(suffix=".epub")
                os.close(fd)
                stream = gio_file.read(None)
                with open(tmp_epub, "wb") as f:
                    f.write(stream.read_bytes(stream.get_size()).get_data())
                path = tmp_epub
            except Exception:
                path = None

        if not path:
            print("Cannot access EPUB path")
            return

        tempdir = tempfile.mkdtemp(prefix="epub_")
        try:
            with zipfile.ZipFile(path, 'r') as z:
                z.extractall(tempdir)
                # find first html/xhtml file
                candidates = [n for n in z.namelist() if n.lower().endswith(('.html', '.htm', '.xhtml'))]
                if not candidates:
                    print("No HTML file inside EPUB")
                    return
                # prefer first candidate that's likely content
                candidate = candidates[0]
                fullpath = os.path.join(tempdir, candidate)
                if not os.path.exists(fullpath):
                    # sometimes stored with folders, try to find file on disk
                    for c in candidates:
                        p = os.path.join(tempdir, c)
                        if os.path.exists(p):
                            fullpath = p
                            break
                with open(fullpath, "r", encoding="utf-8", errors="replace") as f:
                    html = f.read()
                base_uri = "file://" + os.path.dirname(fullpath) + "/"
                self._epub_tempdir = tempdir
                self.webview.load_html(html, base_uri)
        except Exception as e:
            print("EPUB load error:", e)
            shutil.rmtree(tempdir, ignore_errors=True)

    def _cleanup_epub_tempdir(self):
        if self._epub_tempdir and os.path.exists(self._epub_tempdir):
            try:
                shutil.rmtree(self._epub_tempdir)
            except Exception:
                pass
        self._epub_tempdir = None

    def on_font_family_changed(self, dropdown, *args):
        item = dropdown.get_selected_item()
        if not item:
            return
        font = item.get_string().replace("'", "\\'")
        css = f"* {{ font-family: '{font}' !important; }}"
        script = f"""
        (function() {{
            let s = document.getElementById('__font_override');
            if (!s) {{
                s = document.createElement('style');
                s.id = '__font_override';
                (document.head || document.documentElement).appendChild(s);
            }}
            s.textContent = {json.dumps(css)};
        }})();
        """
        try:
            # use the same call-signature that worked elsewhere in your file
            self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception:
            pass



    def on_close_request(self, *args):
        self._cleanup_epub_tempdir()
        return False

if __name__ == "__main__":
    app = Writer()
    app.run()

