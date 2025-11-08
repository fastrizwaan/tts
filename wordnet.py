#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio
import nltk
from nltk.corpus import wordnet as wn

# Ensure WordNet is available — download automatically if missing
try:
    wn.ensure_loaded()
except LookupError:
    nltk.download("wordnet", quiet=True)
    wn.ensure_loaded()

class WordnetWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="WordNet Dictionary")
        self.set_default_size(500, 600)

        # --- ToolbarView (main Libadwaita layout container) ---
        view = Adw.ToolbarView()
        self.set_content(view)

        # --- Header bar ---
        header = Adw.HeaderBar()
        view.add_top_bar(header)

        # --- Search entry ---
        self.search_entry = Gtk.SearchEntry(placeholder_text="Type a word…")
        self.search_entry.connect("search-changed", self.on_search)
        header.pack_start(self.search_entry)

        # --- List for results ---
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        # --- Scroller (with vexpand so it fills the window) ---
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self.list_box)

        # --- Content box ---
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content_box.append(scrolled)
        view.set_content(content_box)

    # --- Helper to clear the list properly in GTK4 ---
    def clear_listbox(self):
        child = self.list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.list_box.remove(child)
            child = next_child

    def on_search(self, entry):
        query = entry.get_text().strip()
        self.clear_listbox()
        if not query:
            return

        synsets = wn.synsets(query)
        if not synsets:
            self.add_result("No results found.")
            return

        for syn in synsets:
            self.add_synset(syn)

    def add_result(self, text):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        row.append(label)
        self.list_box.append(row)

    def add_synset(self, syn):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        name_label = Gtk.Label(label=f"• {syn.name()} ({syn.pos()})", xalign=0)
        name_label.add_css_class("heading")
        row.append(name_label)

        defn_label = Gtk.Label(label=syn.definition(), xalign=0)
        defn_label.set_wrap(True)
        row.append(defn_label)

        for ex in syn.examples():
            ex_label = Gtk.Label(label=f"“{ex}”", xalign=0)
            ex_label.set_wrap(True)
            ex_label.add_css_class("dim-label")
            row.append(ex_label)

        self.list_box.append(row)
        self.list_box.show()

class WordnetApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.Wordnet",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        Adw.init()

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = WordnetWindow(self)
        win.present()

if __name__ == "__main__":
    app = WordnetApp()
    app.run()
