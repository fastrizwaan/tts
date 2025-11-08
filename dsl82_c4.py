#!/usr/bin/env python3
import gi
import os
import re
import gzip
import json
from pathlib import Path

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango

APP_NAME = "DSL Dictionary"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary"
CONFIG_FILE = CONFIG_DIR / "settings.json"

SEPARATOR_COLORS = {
    "Default": "default",
    "Blue": "blue",
    "Green": "green",
    "Purple": "purple",
    "Orange": "orange",
    "Red": "red",
    "Teal": "teal",
    "Pink": "pink",
    "Yellow": "yellow",
    "Gray": "gray",
}


# ------------------------------------------------------------
# DICTIONARY MANAGER
# ------------------------------------------------------------
class DictionaryManager:
    def __init__(self):
        self.dictionaries = {}
        self.entries = {}

    def decode_dsl_bytes(self, raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            print("Detected UTF-8 BOM")
            return raw[3:].decode("utf-8", errors="strict")
        if raw.startswith(b"\xff\xfe"):
            print("Detected UTF-16-LE BOM")
            return raw.decode("utf-16-le", errors="strict")
        if raw.startswith(b"\xfe\xff"):
            print("Detected UTF-16-BE BOM")
            return raw.decode("utf-16-be", errors="strict")

        sample = raw[:256]
        le_score = be_score = 0
        pairs = len(sample) // 2
        for i in range(pairs):
            a = sample[2 * i]
            b = sample[2 * i + 1]
            if a != 0 and b == 0:
                le_score += 1
            if a == 0 and b != 0:
                be_score += 1

        if pairs > 8:
            if le_score / pairs >= 0.7:
                print("Detected UTF-16-LE (heuristic)")
                return raw.decode("utf-16-le", errors="ignore")
            if be_score / pairs >= 0.7:
                print("Detected UTF-16-BE (heuristic)")
                return raw.decode("utf-16-be", errors="ignore")

        print("Assuming UTF-8")
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            print("Using UTF-8 (ignore)")
            return raw.decode("utf-8", errors="ignore")

    def load_dictionary(self, path, color="default"):
        path = Path(path)
        if not path.exists():
            return False

        try:
            if path.suffix == ".dz":
                raw = gzip.open(path, "rb").read()
            else:
                raw = open(path, "rb").read()
            content = self.decode_dsl_bytes(raw)
        except Exception as e:
            print("Decode error:", e)
            return False

        dict_name = path.stem
        for line in content.splitlines()[:20]:
            if line.startswith("#NAME"):
                m = re.search(r'#NAME\s+"([^"]+)"', line)
                if m:
                    dict_name = m.group(1)
                break

        entries = self._parse_dsl(content)
        print(f"Loaded {len(entries)} entries from {dict_name}")

        self.dictionaries[str(path)] = {
            "name": dict_name,
            "entries": entries,
            "color": color,
        }

        for w, defs in entries.items():
            lw = w.lower()
            if lw not in self.entries:
                self.entries[lw] = []
            self.entries[lw].append((w, dict_name, defs, color))
        return True

    def _parse_dsl(self, content):
        entries = {}
        headwords, defs = [], []
        in_def = False

        def flush():
            if headwords and defs:
                for w in headwords:
                    entries.setdefault(w, []).extend(defs)

        for raw in content.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue
            if line == "-":
                flush()
                headwords, defs, in_def = [], [], False
                continue
            if raw[:1].isspace():
                in_def = True
                cleaned = raw.lstrip()
                if cleaned:
                    defs.append(cleaned)
                continue
            if in_def:
                flush()
                headwords, defs, in_def = [], [], False
            w = self._clean_word(line)
            if w:
                headwords.append(w)
        flush()
        return entries

    def _clean_word(self, w):
        w = re.sub(r"\[/?[^\]]*\]", "", w)
        w = re.sub(r"\{.*?\}", "", w)
        return w.strip()

    def _clean_definition(self, t):
        return t.strip()

    def search(self, q):
        q = q.lower().strip()
        if not q:
            return []
        res = {}
        for lw, lst in self.entries.items():
            if q in lw:
                for orig, dn, defs, color in lst:
                    res.setdefault(orig, []).append((dn, defs, color))
        def order(x):
            w = x[0].lower()
            if w == q:
                return (0, w)
            if w.startswith(q):
                return (1, w)
            return (2, w)
        return sorted(res.items(), key=order)


# ------------------------------------------------------------
# MAIN WINDOW
# ------------------------------------------------------------
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(800, 600)
        self.dict_manager = DictionaryManager()
        self._load_css()
        self._build_ui()
        self._load_settings()

    def _load_css(self):
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).parent / "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )

    def _build_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)
        header = Adw.HeaderBar()
        box.append(header)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search dictionary…")
        self.search_entry.connect("search-changed", self.on_search)
        header.set_title_widget(self.search_entry)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.connect("clicked", self.on_settings)
        header.pack_end(settings_btn)

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(self.scrolled)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scrolled.set_child(self.listbox)
        self.show_placeholder("Load a dictionary to start searching")

    def show_placeholder(self, text):
        while (c := self.listbox.get_first_child()):
            self.listbox.remove(c)
        lbl = Gtk.Label(label=text)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(60)
        lbl.set_margin_top(40)
        lbl.set_margin_bottom(40)
        lbl.set_margin_start(20)
        lbl.set_margin_end(20)
        lbl.add_css_class("dim-label")
        self.listbox.append(lbl)

    # ------------------------------------------------------------
    # DSL text rendering
    # ------------------------------------------------------------
    def render_dsl_text(self, text):
        """Convert DSL markup to compact, hierarchical, Pango markup with progressive indentation."""

        # --- Escape reserved characters ---
        text = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )

        # Restore controlled DSL constructs
        text = re.sub(r"&lt;&lt;(.*?)&gt;&gt;", r"<<\1>>", text)

        # --- Inline formatting ---
        text = re.sub(r"\[b\](.*?)\[/b\]", r"<b>\1</b>", text, flags=re.DOTALL)
        text = re.sub(r"\[i\](.*?)\[/i\]", r"<i>\1</i>", text, flags=re.DOTALL)
        text = re.sub(r"\[u\](.*?)\[/u\]", r"<u>\1</u>", text, flags=re.DOTALL)
        text = re.sub(r"\[p\](.*?)\[/p\]", r'<span size="smaller"><i>\1</i></span>', text)
        text = re.sub(r"\[ex\](.*?)\[/ex\]", r'<span color="#228B22"><i>\1</i></span>', text)
        text = re.sub(r"\[c ([^\]]+)\](.*?)\[/c\]", r'<span color="\1">\2</span>', text)

        # --- Hide [com] and [trn] tags but keep content ---
        text = re.sub(r"\[/?com\]", "", text)
        text = re.sub(r"\[/?trn\]", "", text)

        # --- Remove unused control syntax ---
        text = re.sub(r"\[/?[*]\]", "", text)
        text = re.sub(r"\{.*?\}", "", text)

        # --- Clickable internal references like <<term>> ---
        text = re.sub(
            r"<<(.*?)>>",
            r'<a href="\1"><span foreground="#007BFF" underline="single">\1</span></a>',
            text,
        )

        # --- Label formatting for parts like Synonyms, Antonyms, etc. ---
        text = re.sub(
            r"(?i)\b(Synonyms|Antonyms|See also|Derived forms?|Hypernyms|Hyponyms|Meronyms|Holonyms):",
            r'<span size="smaller"><b>\1:</b></span>',
            text,
        )

        # --- Handle indentation for [m1]...[m9] progressively ---
        def margin_replacer(match):
            level = int(match.group(1))
            content = match.group(2).strip()
            # Each level adds increasing indentation
            indent = "\u00A0" * (level * 4)
            # Optional subtle tint for deeper levels
            color = ["#000000", "#444444", "#666666", "#777777", "#888888"]
            tint = color[min(level - 1, len(color) - 1)]
            return f"\n<span color='{tint}'>{indent}{content}</span>"

        text = re.sub(r"\[m(\d+)\](.*?)\[/m\]", margin_replacer, text, flags=re.DOTALL)

        # --- Indent numbered definitions (1., 2., etc.) ---
        text = re.sub(r"(?m)^(\d+\.)", lambda m: "\u00A0\u00A0" + m.group(1), text)

        # --- Compact spacing cleanup ---
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n([.,;:])", r"\1", text)

        return text.strip()


    # ------------------------------------------------------------
    # Search and render
    # ------------------------------------------------------------
    def on_search(self, entry):
        q = entry.get_text()
        while (c := self.listbox.get_first_child()):
            self.listbox.remove(c)

        if not q.strip():
            self.show_placeholder(
                "Enter a search term" if self.dict_manager.entries else "Load a dictionary to start searching"
            )
            return

        results = self.dict_manager.search(q)
        if not results:
            self.show_placeholder("No results found")
            return

        for word, dict_data in results[:100]:
            for dname, defs, color in dict_data:
                card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                card_box.set_margin_top(8)
                card_box.set_margin_bottom(8)
                card_box.set_margin_start(10)
                card_box.set_margin_end(10)
                card_box.set_hexpand(True)
                card_box.set_halign(Gtk.Align.FILL)
                card_box.add_css_class("dict-card")

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                header_box.set_hexpand(True)
                header_box.set_halign(Gtk.Align.FILL)

                lemma_lbl = Gtk.Label(label=word)
                lemma_lbl.add_css_class("lemma")
                lemma_lbl.set_xalign(0)
                lemma_lbl.set_wrap(True)
                lemma_lbl.set_max_width_chars(40)
                lemma_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lemma_lbl.set_halign(Gtk.Align.START)
                lemma_lbl.set_hexpand(True)

                dict_lbl = Gtk.Label(label=f"📖 {dname}")
                dict_lbl.add_css_class("dict-name")
                dict_lbl.set_xalign(1)
                dict_lbl.set_wrap(True)
                dict_lbl.set_max_width_chars(40)
                dict_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                dict_lbl.set_halign(Gtk.Align.END)

                header_box.append(lemma_lbl)
                header_box.append(dict_lbl)
                card_box.append(header_box)

                # Definition text
                full_text = "\n".join(defs)
                markup = self.render_dsl_text(full_text)
                def_lbl = Gtk.Label()
                def_lbl.set_wrap(True)
                def_lbl.set_wrap_mode(Gtk.WrapMode.WORD)
                def_lbl.set_xalign(0)
                def_lbl.set_halign(Gtk.Align.FILL)
                def_lbl.set_hexpand(True)
                def_lbl.set_selectable(True)
                def_lbl.set_use_markup(True)
                def_lbl.set_markup(markup)
                def_lbl.add_css_class("def-label")


                def_lbl.connect("activate-link", self._on_link_clicked)
                card_box.append(def_lbl)
                self.listbox.append(card_box)

    def _on_link_clicked(self, label, uri):
        if uri:
            self.search_entry.set_text(uri)
            self.on_search(self.search_entry)
        return True

    # ------------------------------------------------------------
    # SETTINGS
    # ------------------------------------------------------------
    def on_settings(self, btn):
        dlg = SettingsDialog(self, self.dict_manager.dictionaries.keys())
        dlg.present()

    def _load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            cfg = json.load(open(CONFIG_FILE))
            for d in cfg.get("dictionaries", []):
                p = d["path"]
                c = d.get("color", "default")
                if os.path.exists(p):
                    self.dict_manager.load_dictionary(p, c)
        except Exception as e:
            print("Settings load error:", e)

    def save_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        arr = [{"path": p, "color": info["color"]}
               for p, info in self.dict_manager.dictionaries.items()]
        json.dump({"dictionaries": arr}, open(CONFIG_FILE, "w"))


# ------------------------------------------------------------
# SETTINGS DIALOG
# ------------------------------------------------------------
class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent, paths):
        super().__init__(transient_for=parent, modal=True, title="Settings")
        self.parent = parent
        page = Adw.PreferencesPage()
        self.add(page)
        group = Adw.PreferencesGroup(title="Dictionaries")
        page.add(group)

        add_row = Adw.ActionRow(title="Add Dictionary")
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.connect("clicked", self.add_dictionary)
        add_row.add_suffix(add_button)
        group.add(add_row)

        for path in paths:
            info = parent.dict_manager.dictionaries[path]
            row = Adw.ActionRow(
                title=info["name"],
                subtitle=f"{len(info['entries'])} entries • {os.path.basename(path)}",
            )

            names = list(SEPARATOR_COLORS.keys())
            model = Gtk.StringList.new(names)
            dd = Gtk.DropDown(model=model)
            dd.set_selected(list(SEPARATOR_COLORS.values()).index(info["color"]))
            dd.connect("notify::selected", self.color_changed, path)
            row.add_suffix(dd)

            rm = Gtk.Button(icon_name="user-trash-symbolic")
            rm.add_css_class("destructive-action")
            rm.connect("clicked", self.remove_dictionary, path)
            row.add_suffix(rm)
            group.add(row)

    def add_dictionary(self, *_):
        dlg = Gtk.FileDialog()
        dlg.open(self, None, self._add_done)

    def _add_done(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            path = f.get_path()
            if self.parent.dict_manager.load_dictionary(path, "default"):
                self.parent.save_settings()
                self.close()
                SettingsDialog(
                    self.parent, self.parent.dict_manager.dictionaries.keys()
                ).present()
        except GLib.Error:
            pass

    def color_changed(self, dd, _spec, path):
        new_color = list(SEPARATOR_COLORS.values())[dd.get_selected()]
        self.parent.dict_manager.dictionaries[path]["color"] = new_color
        self.parent.dict_manager.entries = {}
        for p, info in self.parent.dict_manager.dictionaries.items():
            for w, defs in info["entries"].items():
                wl = w.lower()
                self.parent.dict_manager.entries.setdefault(wl, []).append(
                    (w, info["name"], defs, info["color"])
                )
        self.parent.save_settings()
        self.parent.on_search(self.parent.search_entry)

    def remove_dictionary(self, *_args):
        path = _args[-1]
        self.parent.dict_manager.dictionaries.pop(path, None)
        self.parent.dict_manager.entries = {}
        for p, info in self.parent.dict_manager.dictionaries.items():
            for w, defs in info["entries"].items():
                wl = w.lower()
                self.parent.dict_manager.entries.setdefault(wl, []).append(
                    (w, info["name"], defs, info["color"])
                )
        self.parent.save_settings()
        self.close()
        if self.parent.dict_manager.dictionaries:
            SettingsDialog(
                self.parent, self.parent.dict_manager.dictionaries.keys()
            ).present()


# ------------------------------------------------------------
# APPLICATION
# ------------------------------------------------------------
class DictionaryApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.DSLDictionary")
        GLib.set_application_name(APP_NAME)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()


def main():
    app = DictionaryApp()
    return app.run(None)


if __name__ == "__main__":
    main()

