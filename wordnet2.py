#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio
from nltk.corpus import wordnet as wn

# -----------------------------------------------------------
#  GTK4 + Adwaita WordNet Browser (Artha-style)
# -----------------------------------------------------------

class WordnetWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("WordNet Thesaurus")
        self.set_default_size(820, 680)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search a word...")
        self.search_entry.connect("activate", self.on_search)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.list_box.set_vexpand(True)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_child(self.list_box)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_vexpand(True)

        header = Adw.HeaderBar()
        header.set_title_widget(self.search_entry)

        clamp = Adw.Clamp()
        clamp.set_child(self.scroll)
        clamp.set_maximum_size(800)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.append(header)
        content.append(clamp)

        self.set_content(content)

    # -------------------------------------------------------

    def clear_results(self):
        child = self.list_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.list_box.remove(child)
            child = next_child

    def on_search(self, entry):
        word = entry.get_text().strip()
        if not word:
            return
        self.clear_results()
        synsets = wn.synsets(word)
        if not synsets:
            self.list_box.append(Gtk.Label(label=f"No results for “{word}”.", xalign=0))
            return

        for i, syn in enumerate(synsets, 1):
            self.add_synset(word, syn, i)

    # -------------------------------------------------------

    def add_synset(self, word, syn, index):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.set_margin_top(8)
        row.set_margin_bottom(8)

        lemma = syn.lemmas()[0]
        title = f"{lemma.name().replace('_',' ')} ({syn.pos()}, sense {index})"
        title_lbl = Gtk.Label(label=title, xalign=0)
        title_lbl.add_css_class("title-2")
        row.append(title_lbl)

        defn = Gtk.Label(label=syn.definition(), xalign=0)
        defn.set_wrap(True)
        defn.add_css_class("body")
        row.append(defn)

        # Examples
        for ex in syn.examples():
            ex_lbl = Gtk.Label(label=f"“{ex}”", xalign=0)
            ex_lbl.set_wrap(True)
            ex_lbl.add_css_class("dim-label")
            row.append(ex_lbl)

        # Related words sections
        related_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        related_box.add_css_class("boxed-list")

        def add_relatives(title, words):
            if not words:
                return
            title_lbl = Gtk.Label(label=f"{title}:", xalign=0)
            title_lbl.add_css_class("title-4")
            related_box.append(title_lbl)
            flow = Gtk.FlowBox()
            flow.set_max_children_per_line(8)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            for w in sorted(set(words)):
                btn = Gtk.Button(label=w)
                btn.add_css_class("pill")
                btn.connect("clicked", self.on_link_clicked, w)
                flow.insert(btn, -1)
            related_box.append(flow)

        synonyms = [l.name().replace('_',' ') for l in syn.lemmas()]
        antonyms = [a.name().replace('_',' ') for l in syn.lemmas() for a in l.antonyms()]
        derivs = [d.name().replace('_',' ') for l in syn.lemmas() for d in l.derivationally_related_forms()]
        kinds = [h.lemmas()[0].name().replace('_',' ') for h in syn.hyponyms()]
        supers = [h.lemmas()[0].name().replace('_',' ') for h in syn.hypernyms()]

        add_relatives("Synonyms", synonyms)
        add_relatives("Antonyms", antonyms)
        add_relatives("Derivatives", derivs)
        add_relatives("Kinds of", kinds)
        add_relatives("Kind of", supers)

        row.append(related_box)
        self.list_box.append(row)
        self.list_box.show()

    # -------------------------------------------------------

    def on_link_clicked(self, button, word):
        self.search_entry.set_text(word)
        self.on_search(self.search_entry)


# -----------------------------------------------------------

class WordnetApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.WordnetGTK",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = WordnetWindow(self)
        win.present()


def main():
    import nltk
    try:
        wn.synsets("test")
    except LookupError:
        nltk.download("wordnet")

    app = WordnetApp()
    app.run([])


if __name__ == "__main__":
    main()

