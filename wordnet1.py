#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib
import nltk

# Ensure WordNet data
try:
    from nltk.corpus import wordnet as wn
    wn.ensure_loaded()
except LookupError:
    # Lazy-load toast later after Adw init
    wn = None

class WordnetWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="WordNet Dictionary")
        self.set_default_size(480, 560)

        # --- ToolbarView structure ---
        self.view = Adw.ToolbarView()
        self.set_content(self.view)

        # --- Header bar ---
        header = Adw.HeaderBar()
        self.view.add_top_bar(header)

        # --- Search entry ---
        self.search_entry = Gtk.SearchEntry(placeholder_text="Type a word…")
        self.search_entry.connect("search-changed", self.on_search)
        header.pack_start(self.search_entry)

        # --- Toast overlay for messages ---
        self.toast_overlay = Adw.ToastOverlay()
        self.view.set_content(self.toast_overlay)

        # --- List of results ---
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        # --- Scrolled container ---
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self.list_box)
        self.toast_overlay.set_child(scrolled)

        # Initialize WordNet if missing
        GLib.idle_add(self.ensure_wordnet_ready)

    def ensure_wordnet_ready(self):
        global wn
        if wn is None:
            toast = Adw.Toast.new("Downloading WordNet data…")
            self.toast_overlay.add_toast(toast)
            import threading
            def download():
                nltk.download("wordnet", quiet=True)
                from nltk.corpus import wordnet as _wn
                _wn.ensure_loaded()
                globals()["wn"] = _wn
                GLib.idle_add(lambda: self.toast_overlay.add_toast(
                    Adw.Toast.new("WordNet data ready!")
                ))
            threading.Thread(target=download, daemon=True).start()

    def clear_listbox(self):
        child = self.list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.list_box.remove(child)
            child = next_child

    def on_search(self, entry):
        if wn is None:
            return  # still downloading
        query = entry.get_text().strip()
        self.clear_listbox()
        if not query:
            return

        synsets = wn.synsets(query)
        if not synsets:
            self.add_result("No results found.")
            return

        for i, syn in enumerate(synsets, start=1):
            self.add_synset(syn, i)

    def add_result(self, text):
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row.append(label)
        self.list_box.append(row)

    def add_synset(self, syn, index):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        # Extract readable info
        lemma = syn.lemmas()[0].name().replace("_", " ")
        pos = syn.pos()
        pos_name = {
            "n": "noun", "v": "verb", "a": "adjective",
            "s": "satellite adj", "r": "adverb"
        }.get(pos, pos)
        title = f"{lemma} ({pos_name}, sense {index})"

        # Clickable label
        link = Gtk.LinkButton.new_with_label("", f"• {title}")
        link.connect("clicked", self.on_link_clicked, lemma)
        row.append(link)

        defn = Gtk.Label(label=syn.definition(), xalign=0)
        defn.set_wrap(True)
        row.append(defn)

        examples = syn.examples()
        for ex in examples:
            ex_label = Gtk.Label(label=f"“{ex}”", xalign=0)
            ex_label.set_wrap(True)
            ex_label.add_css_class("dim-label")
            row.append(ex_label)

        self.list_box.append(row)
        self.list_box.show()

    def on_link_clicked(self, button, word):
        self.search_entry.set_text(word)

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

