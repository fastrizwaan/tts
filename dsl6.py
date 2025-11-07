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

    def load_dictionary(self, path, color="default"):
        path = Path(path)
        if not path.exists():
            return False

        try:
            if path.suffix == ".dz":
                raw = gzip.open(path, "rb").read()
            else:
                raw = open(path, "rb").read()

            # --- BOM detection ---
            if raw.startswith(b"\xef\xbb\xbf"):
                content = raw[3:].decode("utf-8", errors="strict")
                print("Decoded as UTF-8 (BOM)")
            elif raw.startswith(b"\xff\xfe"):
                content = raw.decode("utf-16-le", errors="strict")
                print("Decoded as UTF-16-LE (BOM)")
            elif raw.startswith(b"\xfe\xff"):
                content = raw.decode("utf-16-be", errors="strict")
                print("Decoded as UTF-16-BE (BOM)")

            else:
                # --- Heuristic: UTF-16-LE without BOM ---
                # Check first 200 bytes for <ASCII><00> pattern
                sample = raw[:200]
                if len(sample) > 4 and all(sample[i+1] == 0 for i in range(0, len(sample)-1, 2)):
                    try:
                        content = raw.decode("utf-16-le", errors="strict")
                        print("Decoded as UTF-16-LE (heuristic)")
                    except UnicodeDecodeError:
                        # fallback to ignore mode
                        content = raw.decode("utf-16-le", errors="ignore")
                        print("Decoded as UTF-16-LE (heuristic, ignore errors)")

                # --- Normal UTF-8 path ---
                else:
                    try:
                        content = raw.decode("utf-8", errors="strict")
                        print("Decoded as UTF-8")
                    except UnicodeDecodeError:
                        content = raw.decode("utf-8", errors="ignore")
                        print("Decoded as UTF-8 (ignore errors)")

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
        words = []
        defs = []
        in_entry = False

        lines = content.splitlines()
        print("Processing", len(lines), "lines")

        start = 0
        for i, l in enumerate(lines):
            if l.strip() and not l.startswith("#"):
                start = i
                break

        for line in lines[start:]:
            line = line.rstrip()

            if not line or line.startswith("#"):
                continue

            if line.startswith("\t") or (line.startswith("  ") and line[0].isspace()):
                in_entry = True
                cleaned = line.lstrip()
                if cleaned:
                    defs.append(cleaned)
            else:
                if in_entry and words and defs:
                    for w in words:
                        if w not in entries:
                            entries[w] = []
                        entries[w].extend(defs)
                words = []
                defs = []
                in_entry = False

                w = self._clean_word(line)
                if w:
                    words.append(w)

        if words and defs:
            for w in words:
                if w not in entries:
                    entries[w] = []
                entries[w].extend(defs)

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

        # Populate results
        for word, dict_data in results[:100]:
            wbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            wbox.set_margin_top(12)
            wbox.set_margin_start(12)
            wbox.set_margin_end(12)

            wl = Gtk.Label(label=word)
            wl.add_css_class("title-3")
            wl.set_wrap(True)
            wl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            wl.set_xalign(0)
            wl.set_width_chars(40)
            wl.set_max_width_chars(60)
            wbox.append(wl)
            self.listbox.append(wbox)

            # Dictionary entries
            for dname, defs, color in dict_data:
                block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                block.set_margin_start(12)
                block.set_margin_end(12)
                block.set_margin_top(6)
                self.listbox.append(block)

                # Separator row
                sep_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                block.append(sep_row)

                s1 = Gtk.Separator()
                s1.set_hexpand(True)
                if color != "default":
                    s1.add_css_class(f"sep-{color}")
                sep_row.append(s1)

                name = Gtk.Label(label=dname)
                name.set_wrap(True)
                name.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                name.set_xalign(0)
                name.set_width_chars(20)
                name.set_max_width_chars(20)
                if color != "default":
                    name.add_css_class(f"dictname-{color}")
                sep_row.append(name)

                s2 = Gtk.Separator()
                s2.set_hexpand(True)
                if color != "default":
                    s2.add_css_class(f"sep-{color}")
                sep_row.append(s2)

                # Definitions
                clean_defs = []
                for d in defs[:5]:
                    c = self.dict_manager._clean_definition(d)
                    if c:
                        clean_defs.append(c)

                if clean_defs:
                    wrap = Gtk.Box()
                    #wrap.set_hexpand(True)
                    #wrap.set_halign(Gtk.Align.FILL)

                    lbl = Gtk.Label(label="\n".join(clean_defs))
                    lbl.set_wrap(True)
                    #lbl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                    lbl.set_xalign(0)
                    #lbl.set_halign(Gtk.Align.START)
                    lbl.set_selectable(True)
                    #lbl.set_width_chars(70)
                    #lbl.set_max_width_chars(70)
                    wrap.append(lbl)
                    block.append(wrap)

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

