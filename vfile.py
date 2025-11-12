#!/usr/bin/env python3
import gi, os, chardet
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib

CHUNK_LINES = 1000

class FastTextViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Fast Large File Viewer", default_width=1000, default_height=700)

        self.toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.toolbar.add_top_bar(header)
        self.set_content(self.toolbar)

        open_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        header.pack_start(open_btn)
        open_btn.connect("clicked", self.on_open_clicked)

        self.view = Gtk.TextView()
        self.view.set_monospace(True)
        self.view.set_editable(False)
        self.buffer = self.view.get_buffer()
        self.file = None
        self.line_offset = 0
        self.lines = []

        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.view)
        self.toolbar.set_content(scroller)

        # connect scroll for lazy loading
        vadj = scroller.get_vadjustment()
        vadj.connect("value-changed", self.on_scroll_bottom)

    def read_file_lazy(self, path):
        with open(path, "rb") as f:
            raw = f.read()
        enc = (chardet.detect(raw)["encoding"] or "utf-8").lower()
        text = raw.decode(enc, errors="replace")
        self.lines = text.splitlines()
        self.line_offset = 0
        self.buffer.set_text("")  # clear view
        self.append_chunk()

    def append_chunk(self):
        if self.line_offset >= len(self.lines):
            return
        end = min(self.line_offset + CHUNK_LINES, len(self.lines))
        chunk = "\n".join(self.lines[self.line_offset:end])
        self.buffer.insert(self.buffer.get_end_iter(), chunk + "\n")
        self.line_offset = end

    def on_scroll_bottom(self, adj):
        if adj.get_upper() - adj.get_value() - adj.get_page_size() < 200:
            GLib.idle_add(self.append_chunk)

    def on_open_clicked(self, *_):
        dialog = Gtk.FileChooserNative.new("Open File", self, Gtk.FileChooserAction.OPEN,
                                           "_Open", "_Cancel")
        dialog.connect("response", self.on_open_response)
        dialog.show()

    def on_open_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            self.read_file_lazy(path)
            self.set_title(f"Fast Large File Viewer — {os.path.basename(path)}")
        dialog.destroy()

class FastApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.FastLargeViewer",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        FastTextViewer(self).present()

if __name__ == "__main__":
    app = FastApp()
    app.run()
