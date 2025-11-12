#!/usr/bin/env python3
import gi, os, chardet
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
from gi.repository import Gtk, Adw, Gio, GtkSource

class LargeTextEditor(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Large Text Editor", default_width=1000, default_height=700)

        toolbar_view = Adw.ToolbarView()
        headerbar = Adw.HeaderBar()
        toolbar_view.add_top_bar(headerbar)
        self.set_content(toolbar_view)

        open_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        saveas_btn = Gtk.Button.new_from_icon_name("document-save-as-symbolic")
        headerbar.pack_start(open_btn)
        headerbar.pack_end(saveas_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True)
        vbox.set_margin_top(6)
        vbox.set_margin_bottom(6)
        vbox.set_margin_start(6)
        vbox.set_margin_end(6)
        toolbar_view.set_content(vbox)

        # --- Find / Replace bar ---
        findbar = Gtk.Box(spacing=6)
        vbox.append(findbar)
        self.search_entry = Gtk.Entry(placeholder_text="Find…")
        self.replace_entry = Gtk.Entry(placeholder_text="Replace with…")
        self.find_btn = Gtk.Button(label="Find Next")
        self.replace_btn = Gtk.Button(label="Replace")
        self.replace_all_btn = Gtk.Button(label="Replace All")
        for w in (self.search_entry, self.replace_entry,
                  self.find_btn, self.replace_btn, self.replace_all_btn):
            findbar.append(w)

        # --- GtkSourceView for large files + line numbers ---
        self.view = GtkSource.View()
        self.view.set_show_line_numbers(True)
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.buffer = self.view.get_buffer()

        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.view)
        vbox.append(scroller)

        self.current_path = None
        self.current_encoding = "utf-8"

        # Connect signals
        open_btn.connect("clicked", self.on_open_clicked)
        saveas_btn.connect("clicked", self.on_saveas_clicked)
        self.find_btn.connect("clicked", self.on_find)
        self.replace_btn.connect("clicked", self.on_replace)
        self.replace_all_btn.connect("clicked", self.on_replace_all)

    # ---------- File Handling ----------
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
        dialog = Gtk.FileChooserNative.new("Open File", self, Gtk.FileChooserAction.OPEN,
                                           "_Open", "_Cancel")
        dialog.connect("response", self.on_open_response)
        dialog.show()

    def on_open_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            try:
                text = self.read_file(path)
                self.buffer.set_text(text)
                self.current_path = path
                self.set_title(f"Large Text Editor — {os.path.basename(path)}")
            except Exception as e:
                print("Error:", e)
        dialog.destroy()

    def on_saveas_clicked(self, *_):
        dialog = Gtk.FileChooserNative.new("Save As", self, Gtk.FileChooserAction.SAVE,
                                           "_Save", "_Cancel")
        dialog.set_do_overwrite_confirmation(True)
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
                path = dlg.get_file().get_path()
                start, end = self.buffer.get_bounds()
                text = self.buffer.get_text(start, end, False)
                encoding = enc_combo.get_active_text()
                try:
                    self.write_file(path, text, encoding)
                except Exception as e:
                    print("Error saving file:", e)
            dlg.destroy()

        dialog.connect("response", response_cb)
        dialog.show()

    # ---------- Find / Replace ----------
    def on_find(self, *_):
        query = self.search_entry.get_text()
        if not query:
            return

        insert_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        end_iter = self.buffer.get_end_iter()
        text = self.buffer.get_text(insert_iter, end_iter, False)

        idx = text.lower().find(query.lower())
        if idx == -1:
            return

        start_offset = insert_iter.get_offset() + idx
        end_offset = start_offset + len(query)
        start_iter = self.buffer.get_iter_at_offset(start_offset)
        end_iter = self.buffer.get_iter_at_offset(end_offset)
        self.buffer.select_range(start_iter, end_iter)
        self.view.scroll_to_iter(start_iter, 0.25, False, 0, 0)

    def on_replace(self, *_):
        if not self.buffer.get_has_selection():
            self.on_find()
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            self.buffer.delete(start, end)
            self.buffer.insert_at_cursor(self.replace_entry.get_text())
            self.on_find()

    def on_replace_all(self, *_):
        query = self.search_entry.get_text()
        repl = self.replace_entry.get_text()
        if not query:
            return
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, False)
        new_text = text.replace(query, repl)
        self.buffer.set_text(new_text)

class LargeTextApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.LargeTextEditor",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = LargeTextEditor(self)
        win.present()

if __name__ == "__main__":
    app = LargeTextApp()
    app.run()
