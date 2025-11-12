#!/usr/bin/env python3
import gi, os, chardet
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib

class RawTextEditor(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Raw Text Editor", default_width=1000, default_height=700)

        # --- ToolbarView with HeaderBar ---
        toolbar_view = Adw.ToolbarView()
        headerbar = Adw.HeaderBar()
        toolbar_view.add_top_bar(headerbar)
        self.set_content(toolbar_view)

        # --- Header buttons ---
        open_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        saveas_btn = Gtk.Button.new_from_icon_name("document-save-as-symbolic")
        headerbar.pack_start(open_btn)
        headerbar.pack_end(saveas_btn)

        # --- Main box with vexpand text area ---
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True)
        vbox.set_margin_top(6)
        vbox.set_margin_bottom(6)
        vbox.set_margin_start(6)
        vbox.set_margin_end(6)
        toolbar_view.set_content(vbox)

        # --- Find/Replace bar ---
        findbar = Gtk.Box(spacing=6)
        vbox.append(findbar)
        self.search_entry = Gtk.Entry(placeholder_text="Find…")
        self.replace_entry = Gtk.Entry(placeholder_text="Replace with…")
        self.find_btn = Gtk.Button(label="Find Next")
        self.replace_btn = Gtk.Button(label="Replace")
        self.replace_all_btn = Gtk.Button(label="Replace All")
        for w in (self.search_entry, self.replace_entry, self.find_btn, self.replace_btn, self.replace_all_btn):
            findbar.append(w)

        # --- Text area ---
        self.view = Gtk.TextView()
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.view)
        vbox.append(scroller)

        self.buffer = self.view.get_buffer()
        self.current_path = None
        self.current_encoding = "utf-8"

        # --- Connect signals ---
        open_btn.connect("clicked", self.on_open_clicked)
        saveas_btn.connect("clicked", self.on_saveas_clicked)
        self.find_btn.connect("clicked", self.on_find)
        self.replace_btn.connect("clicked", self.on_replace)
        self.replace_all_btn.connect("clicked", self.on_replace_all)

    # -------- File Handling --------
    def read_file(self, path):
        with open(path, "rb") as f:
            raw = f.read()
        detected = chardet.detect(raw)
        enc = detected["encoding"] or "utf-8"
        self.current_encoding = enc
        if enc.lower().replace("-", "") == "utf16le":
            return raw.decode("utf-16le", errors="replace")
        return raw.decode("utf-8", errors="replace")

    def write_file(self, path, text, encoding):
        data = text.encode("utf-16le" if encoding == "utf-16le" else "utf-8")
        with open(path, "wb") as f:
            f.write(data)
        self.current_path = path
        self.current_encoding = encoding

    def on_open_clicked(self, *_):
        dialog = Gtk.FileChooserNative.new(
            "Open File", self, Gtk.FileChooserAction.OPEN,
            "_Open", "_Cancel"
        )
        dialog.set_modal(True)
        dialog.connect("response", self.on_open_response)
        dialog.show()

    def on_open_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            path = file.get_path()
            try:
                text = self.read_file(path)
                self.buffer.set_text(text)
                self.current_path = path
                self.set_title(f"Raw Text Editor — {os.path.basename(path)}")
            except Exception as e:
                print(f"Error opening file: {e}")
        dialog.destroy()

    def on_saveas_clicked(self, *_):
        dialog = Gtk.FileChooserNative.new(
            "Save As", self, Gtk.FileChooserAction.SAVE,
            "_Save", "_Cancel"
        )
        dialog.set_modal(True)
        dialog.set_do_overwrite_confirmation(True)

        # encoding dropdown
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label="Encoding:")
        enc_combo = Gtk.ComboBoxText()
        enc_combo.append_text("utf-8")
        enc_combo.append_text("utf-16le")
        enc_combo.set_active(0 if self.current_encoding.lower().startswith("utf-8") else 1)
        box.append(label)
        box.append(enc_combo)
        dialog.set_extra_widget(box)

        def response_cb(dlg, resp):
            if resp == Gtk.ResponseType.ACCEPT:
                file = dlg.get_file()
                path = file.get_path()
                start, end = self.buffer.get_bounds()
                text = self.buffer.get_text(start, end, False)
                encoding = enc_combo.get_active_text()
                try:
                    self.write_file(path, text, encoding)
                except Exception as e:
                    print(f"Error saving file: {e}")
            dlg.destroy()

        dialog.connect("response", response_cb)
        dialog.show()

    # -------- Find / Replace --------
    def get_insert_iter(self):
        mark = self.buffer.get_insert()
        return self.buffer.get_iter_at_mark(mark)

    def on_find(self, *_):
        text = self.search_entry.get_text()
        if not text:
            return
        start = self.get_insert_iter()
        match = self.buffer.forward_search(text, Gtk.TextSearchFlags.CASE_INSENSITIVE, start)
        if match:
            match_start, match_end = match
            self.buffer.select_range(match_start, match_end)
            self.view.scroll_to_iter(match_start, 0.25, False, 0, 0)

    def on_replace(self, *_):
        if not self.buffer.get_has_selection():
            self.on_find()
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            self.buffer.delete(start, end)
            self.buffer.insert_at_cursor(self.replace_entry.get_text())
            self.on_find()

    def on_replace_all(self, *_):
        text = self.search_entry.get_text()
        repl = self.replace_entry.get_text()
        if not text:
            return
        start = self.buffer.get_start_iter()
        while True:
            match = self.buffer.forward_search(text, Gtk.TextSearchFlags.CASE_INSENSITIVE, start)
            if not match:
                break
            match_start, match_end = match
            self.buffer.delete(match_start, match_end)
            self.buffer.insert(match_start, repl)
            start = match_start

class RawTextApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.RawTextEditor",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = RawTextEditor(self)
        win.present()

if __name__ == "__main__":
    app = RawTextApp()
    app.run()
