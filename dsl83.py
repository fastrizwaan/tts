#!/usr/bin/env python3

import gi
import os
import re
import gzip
import json
from pathlib import Path

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk


APP_NAME = "DSL Dictionary"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Updated color list with "default"
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
        """
        Reliable DSL decoding:
        - Detects BOM
        - Detects UTF-16-LE/BE without BOM using byte pattern analysis
        - Falls back to UTF-8 safely
        """

        # 1. --- BOM based decoding -------------------------------------
        if raw.startswith(b"\xef\xbb\xbf"):
            print("Detected UTF-8 BOM")
            return raw[3:].decode("utf-8", errors="strict")

        if raw.startswith(b"\xff\xfe"):
            print("Detected UTF-16-LE BOM")
            return raw.decode("utf-16-le", errors="strict")

        if raw.startswith(b"\xfe\xff"):
            print("Detected UTF-16-BE BOM")
            return raw.decode("utf-16-be", errors="strict")

        # 2. --- UTF-16 detection without BOM ----------------------------
        # Examine first 256 bytes (like a mini-hexdump)
        sample = raw[:256]

        # UTF-16-LE looks like: ASCII byte followed by 00
        le_score = 0
        be_score = 0
        pairs = len(sample) // 2

        for i in range(pairs):
            a = sample[2*i]
            b = sample[2*i + 1]

            if a != 0 and b == 0:
                le_score += 1
            if a == 0 and b != 0:
                be_score += 1

        # Heuristic: at least 70% of pairs match
        if pairs > 8:
            if le_score / pairs >= 0.7:
                print("Detected UTF-16-LE (heuristic)")
                try:
                    return raw.decode("utf-16-le", errors="strict")
                except UnicodeDecodeError:
                    return raw.decode("utf-16-le", errors="ignore")

            if be_score / pairs >= 0.7:
                print("Detected UTF-16-BE (heuristic)")
                try:
                    return raw.decode("utf-16-be", errors="strict")
                except UnicodeDecodeError:
                    return raw.decode("utf-16-be", errors="ignore")

        # 3. --- Default to UTF-8 ----------------------------------------
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
            "color": color
        }

        # Build global entry index
        for w, defs in entries.items():
            lw = w.lower()
            if lw not in self.entries:
                self.entries[lw] = []
            self.entries[lw].append((w, dict_name, defs, color))

        return True

    def _parse_dsl(self, content):
        entries = {}
        headwords = []
        defs = []
        in_def = False

        lines = content.splitlines()
        print("Processing", len(lines), "lines")

        def flush():
            if headwords and defs:
                for w in headwords:
                    if w not in entries:
                        entries[w] = []
                    entries[w].extend(defs)

        for raw in lines:
            line = raw.rstrip()

            # Skip comments
            if not line or line.startswith("#"):
                continue

            # Entry separator "-" starts a new entry
            if line == "-":
                flush()
                headwords = []
                defs = []
                in_def = False
                continue

            # Definition line (any leading whitespace)
            if raw[:1].isspace():
                in_def = True
                cleaned = raw.lstrip()
                if cleaned:
                    defs.append(cleaned)
                continue

            # Headword line
            # If we were collecting definitions, this begins a new entry
            if in_def:
                flush()
                headwords = []
                defs = []
                in_def = False

            # Clean headword
            w = self._clean_word(line)
            if w:
                headwords.append(w)

        # Flush last entry
        flush()

        print("Parsed", len(entries), "entries")
        return entries



    def _clean_word(self, w):
        w = re.sub(r"\[/?[^\]]*\]", "", w)
        w = re.sub(r"\{.*?\}", "", w)
        return w.strip()

    def _clean_definition(self, t):
        t = re.sub(r"\[/?[^\]]*\]", "", t)
        t = re.sub(r"\{.*?\}", "", t)
        t = re.sub(r"\s+", " ", t)
        return t.strip()

    def search(self, q):
        q = q.lower().strip()
        if not q:
            return []

        res = {}
        for lw, lst in self.entries.items():
            if q in lw:
                for orig, dn, defs, color in lst:
                    if orig not in res:
                        res[orig] = []
                    res[orig].append((dn, defs, color))

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
            Gtk.STYLE_PROVIDER_PRIORITY_USER
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
    # SEARCH HANDLER
    # ------------------------------------------------------------

    def on_search(self, entry):
        q = entry.get_text()

        # Clear listbox
        while (c := self.listbox.get_first_child()):
            self.listbox.remove(c)

        if not q.strip():
            self.show_placeholder(
                "Enter a search term"
                if self.dict_manager.entries else
                "Load a dictionary to start searching"
            )
            return

        results = self.dict_manager.search(q)
        if not results:
            self.show_placeholder("No results found")
            return

        for word, dict_data in results[:100]:

            for dname, defs, color in dict_data:

                # Outer block
                block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                block.set_margin_start(12)
                block.set_margin_end(12)
                block.set_margin_top(12)
                self.listbox.append(block)

                # --- Separator + Dictionary Name ---
                sep_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                block.append(sep_row)
                name_lbl = Gtk.Label(label=" 📖 " + dname) 
                name_lbl.set_wrap(True)
                name_lbl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                name_lbl.set_xalign(0)
                name_lbl.set_width_chars(20)
                name_lbl.set_max_width_chars(20)                                    
                name_lbl.add_css_class(f"dictionary-name")
                sep_row.append(name_lbl)


                # --- Word Label ---
                wl = Gtk.Label(label=word)
                wl.add_css_class("title-3")
                wl.set_margin_start(10)
                wl.set_wrap(True)
                wl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                wl.set_xalign(0)
                block.append(wl)
                block.add_css_class(f"result-block")

                # --- Definitions - Rich Format wrapped in a box ---
                # Create the wrap box as a container for definition elements
                wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2) # Changed to vertical for stacking lines
                wrap.set_margin_start(10) # Apply margin here
                block.append(wrap) # Add the wrap box to the main block

                for d in defs:
                    # Clean the raw DSL line
                    cleaned_line = self.dict_manager._clean_definition(d)

                    # Create a box for this definition line
                    def_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                    # No margin here, wrap box handles it
                    wrap.append(def_box) # Add def_box to the wrap box

                    # Check for specific patterns
                    if cleaned_line.startswith("[b]") and cleaned_line.endswith("[/b]"):
                        # It's a bold label like "Synonyms:", "Antonyms:", etc.
                        label_text = cleaned_line[3:-4].strip()  # Remove [b] and [/b]
                        label_widget = Gtk.Label(label=label_text + ":")
                        label_widget.add_css_class("bold-label")
                        label_widget.set_xalign(0)
                        def_box.append(label_widget)

                    elif cleaned_line.startswith("[i]") and cleaned_line.endswith("[/i]"):
                        # It's an example
                        example_text = cleaned_line[3:-4].strip()
                        # Create a clickable link-like button for the example
                        example_btn = Gtk.Button(label=example_text)
                        example_btn.add_css_class("example-button")
                        example_btn.set_relief(Gtk.ReliefStyle.NONE)
                        example_btn.set_halign(Gtk.Align.START)
                        example_btn.set_valign(Gtk.Align.CENTER)
                        # Optional: Add tooltip or make it do something when clicked
                        example_btn.connect("clicked", lambda btn, ex=example_text: print(f"Clicked example: {ex}"))
                        def_box.append(example_btn)

                    elif cleaned_line.startswith(" ") or cleaned_line.startswith("\t"):
                        # It's likely a numbered sense definition (e.g., "1. the state...")
                        # Create a label for the definition itself
                        def_label = Gtk.Label(label=cleaned_line.strip())
                        def_label.set_xalign(0)
                        def_label.set_wrap(True)
                        def_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                        def_label.add_css_class("definition-text")
                        def_box.append(def_label)

                    else:
                        # Default case: plain text
                        plain_label = Gtk.Label(label=cleaned_line)
                        plain_label.set_xalign(0)
                        plain_label.set_wrap(True)
                        plain_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                        def_box.append(plain_label)

                # Optional: Make the wrap box's content selectable if needed
                # This requires adding TextView widgets, which is more complex
                # For now, individual labels/buttons within def_box are not selectable by default
                # unless explicitly set (like the example button if needed)

    # ------------------------------------------------------------
    # SETTINGS
    # ------------------------------------------------------------

    def on_settings(self, btn):
        dlg = SettingsDialog(self, self.dict_manager.dictionaries.keys())
        dlg.present()

    # ------------------------------------------------------------
    # SETTINGS LOAD/SAVE
    # ------------------------------------------------------------

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
        arr = []
        for p, info in self.dict_manager.dictionaries.items():
            arr.append({"path": p, "color": info["color"]})
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
                subtitle=f"{len(info['entries'])} entries • {os.path.basename(path)}"
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

    # ------------------------------------------------------------

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
                    self.parent,
                    self.parent.dict_manager.dictionaries.keys()
                ).present()
        except GLib.Error:
            pass

    def color_changed(self, dd, _spec, path):
        new_color = list(SEPARATOR_COLORS.values())[dd.get_selected()]
        self.parent.dict_manager.dictionaries[path]["color"] = new_color

        # rebuild global entries
        self.parent.dict_manager.entries = {}
        for p, info in self.parent.dict_manager.dictionaries.items():
            for w, defs in info["entries"].items():
                wl = w.lower()
                if wl not in self.parent.dict_manager.entries:
                    self.parent.dict_manager.entries[wl] = []
                self.parent.dict_manager.entries[wl].append(
                    (w, info["name"], defs, info["color"])
                )

        self.parent.save_settings()
        self.parent.on_search(self.parent.search_entry)

    def remove_dictionary(self, *_args):
        path = _args[-1]
        self.parent.dict_manager.dictionaries.pop(path, None)

        # rebuild global entries
        self.parent.dict_manager.entries = {}
        for p, info in self.parent.dict_manager.dictionaries.items():
            for w, defs in info["entries"].items():
                wl = w.lower()
                if wl not in self.parent.dict_manager.entries:
                    self.parent.dict_manager.entries[wl] = []
                self.parent.dict_manager.entries[wl].append(
                    (w, info["name"], defs, info["color"])
                )

        self.parent.save_settings()
        self.close()
        if self.parent.dict_manager.dictionaries:
            SettingsDialog(
                self.parent,
                self.parent.dict_manager.dictionaries.keys()
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

