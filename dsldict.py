#!/usr/bin/env python3
# Minimal GTK4 + Libadwaita DSL dictionary viewer
# Loads .dsl files (StarDict DSL format) and allows simple lookup.
# This is a very small, clean starting template.

import gi
import re

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio

class DSLDictionary:
    def __init__(self, path):
        self.entries = {}
        self.load(path)

    def load(self, path):
        current_word = None
        buffer = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('#'):
                    continue
                if line.strip() == '':
                    continue
                if not line.startswith(' '):
                    # new word
                    if current_word and buffer:
                        self.entries[current_word.lower()] = ''.join(buffer)
                    current_word = line.strip()
                    buffer = []
                else:
                    buffer.append(line)
        if current_word and buffer:
            self.entries[current_word.lower()] = ''.join(buffer)

    def lookup(self, word):
        word = word.lower().strip()
        return self.entries.get(word, "Not found.")

class DictionaryWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("DSL Dictionary")
        self.set_default_size(600, 500)

        self.dict_obj = None

        # Header bar
        header = Adw.HeaderBar()

        open_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.connect("search-changed", self.on_search)
        header.pack_end(self.search_entry)

        # Use a ToolbarView to properly display header content
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        # Text view
        self.buffer = Gtk.TextBuffer()
        self.textview = Gtk.TextView(buffer=self.buffer)
        self.textview.set_editable(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.textview)

        # Place scrolled window inside the toolbar view
        toolbar_view.set_content(scrolled)
        # set_content returns None; remove accidental extra call
        self.set_content(toolbar_view)

    def open_file(self, *_):
        dialog = Gtk.FileChooserNative(
            title="Open DSL File",
            action=Gtk.FileChooserAction.OPEN,
            transient_for=self,
            accept_label="Open"
        )

        filter_dsl = Gtk.FileFilter()
        filter_dsl.set_name("DSL files")
        filter_dsl.add_pattern("*.dsl")
        dialog.add_filter(filter_dsl)

        def on_response(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = dlg.get_file()
                if file:
                    path = file.get_path()
                    self.dict_obj = DSLDictionary(path)
                    self.buffer.set_text(f"Loaded: {path}\nEnter a word in the search bar.")
            dlg.destroy()


        dialog.connect("response", on_response)
        dialog.show()()(path)
            self.buffer.set_text(f"Loaded: {path}\nEnter a word in the search bar.")

    def on_search(self, entry):
        if not self.dict_obj:
            return
        word = entry.get_text()
        if not word.strip():
            self.buffer.set_text("")
            return
        result = self.dict_obj.lookup(word)
        self.buffer.set_text(result)

class DictionaryApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.DSLDict")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = DictionaryWindow(self)
        win.present()

if __name__ == "__main__":
    app = DictionaryApp()
    app.run([])
