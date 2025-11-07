#!/usr/bin/env python3

import gi
import os
import re
import gzip
import json
from pathlib import Path

# --- MUST be before any gi.repository imports ---
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
# -------------------------------------------------

from gi.repository import Gtk, Adw, Gio, GLib


APP_NAME = "DSL Dictionary"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary"
CONFIG_FILE = CONFIG_DIR / "settings.json"


class DictionaryManager:
    def __init__(self):
        self.dictionaries = {}  # {path: parsed_entries}
        self.entries = {}       # {word: [definitions]}
    
    def load_dictionary(self, path):
        path = Path(path)
        if not path.exists():
            return False
            
        try:
            if path.suffix == '.dz':
                with gzip.open(path, 'rt', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            else:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            
            entries = self._parse_dsl(content)
            self.dictionaries[str(path)] = entries
            
            for word, definitions in entries.items():
                if word not in self.entries:
                    self.entries[word] = []
                self.entries[word].extend(definitions)
            return True
        except Exception as e:
            print(f"Error loading {path}: {e}")
            return False
    
    def _parse_dsl(self, content):
        entries = {}
        current_word = None
        current_defs = []
        
        for line in content.splitlines():
            line = line.rstrip()
            if not line or line.startswith('#'):
                continue
                
            if not line.startswith('\t'):
                if current_word:
                    entries[current_word] = current_defs
                    current_defs = []
                current_word = line.strip()
            else:
                current_defs.append(line.lstrip('\t'))
        
        if current_word:
            entries[current_word] = current_defs
        return entries
    
    def search(self, query):
        query = query.strip().lower()
        if not query:
            return []
        results = []
        for word, definitions in self.entries.items():
            if query in word.lower():
                results.append((word, definitions))
        return results


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(800, 600)
        self.dict_manager = DictionaryManager()
        self.setup_widgets()
        self.load_settings()
        
    def setup_widgets(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)
        
        header = Adw.HeaderBar()
        self.main_box.append(header)
        
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search dictionary...")
        self.search_entry.connect('search-changed', self.on_search)
        header.set_title_widget(self.search_entry)
        
        add_button = Gtk.Button(icon_name='document-open-symbolic')
        add_button.connect('clicked', self.on_add_dictionary)
        header.pack_start(add_button)
        
        settings_button = Gtk.Button(icon_name='preferences-system-symbolic')
        settings_button.connect('clicked', self.on_settings)
        header.pack_end(settings_button)
        
        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.main_box.append(self.scrolled)
        
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scrolled.set_child(self.listbox)
    
    def on_search(self, entry):
        query = entry.get_text()
        results = self.dict_manager.search(query)
        
        while (child := self.listbox.get_first_child()):
            self.listbox.remove(child)
            
        if not results:
            label = Gtk.Label(label="No results found")
            label.set_margin_top(20)
            label.set_margin_bottom(20)
            self.listbox.append(label)
            return
            
        for word, definitions in results[:100]:
            row = Adw.ActionRow(title=word)
            row.set_subtitle('\n'.join(definitions[:3]))
            self.listbox.append(row)
    
    def on_add_dictionary(self, button):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        
        dsl_filter = Gtk.FileFilter()
        dsl_filter.set_name("DSL Dictionary Files")
        dsl_filter.add_pattern("*.dsl")
        dsl_filter.add_pattern("*.dsl.dz")
        filters.append(dsl_filter)
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Files")
        all_filter.add_pattern("*")
        filters.append(all_filter)

        dialog = Gtk.FileDialog(
            title="Open Dictionary File",
            modal=True,
            filters=filters,
            default_filter=dsl_filter
        )
        
        dialog.open(self, None, self.on_file_dialog_complete)

    def on_file_dialog_complete(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                if self.dict_manager.load_dictionary(path):
                    self.save_settings()
                    self.on_search(self.search_entry)
                else:
                    self.show_error("Failed to load dictionary")
        except GLib.Error as e:
            print(f"File dialog error: {e}")
    
    def show_error(self, message):
        dialog = Adw.MessageDialog(
            heading="Error",
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present(self)  # ✅ Critical fix: pass parent window
        
    def on_settings(self, button):
        dialog = SettingsDialog(self, self.dict_manager.dictionaries.keys())
        dialog.present()
    
    def save_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = {"dictionary_paths": list(self.dict_manager.dictionaries.keys())}
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
    
    def load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
            paths = config.get("dictionary_paths", [])
            for path in paths:
                if os.path.exists(path):
                    self.dict_manager.load_dictionary(path)
        except Exception as e:
            print(f"Failed to load settings: {e}")


class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent, paths):
        super().__init__(transient_for=parent, modal=True)
        self.parent = parent

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Loaded Dictionaries")
        page.add(group)

        if not paths:
            empty_row = Adw.ActionRow(title="No dictionaries loaded")
            group.add(empty_row)
        else:
            for path in paths:
                entries = parent.dict_manager.dictionaries.get(path, {})
                count = len(entries)
                title = f"{os.path.basename(path)} ({count} entries)"
                row = Adw.ActionRow(title=title)
                remove_button = Gtk.Button(
                    icon_name='user-trash-symbolic',
                    valign=Gtk.Align.CENTER
                )
                remove_button.connect('clicked', self.on_remove, path)
                row.add_suffix(remove_button)
                group.add(row)

        self.set_content(page)  # ✅ Correct for Adw.PreferencesWindow

    def on_remove(self, button, path):
        self.parent.dict_manager.dictionaries.pop(path, None)
        # Rebuild global entries
        self.parent.dict_manager.entries = {}
        for d in self.parent.dict_manager.dictionaries.values():
            for word, definitions in d.items():
                self.parent.dict_manager.entries.setdefault(word, []).extend(definitions)
        self.parent.save_settings()
        self.parent.on_search(self.parent.search_entry)
        self.close()
    
    def on_remove(self, button, path):
        self.parent.dict_manager.dictionaries.pop(path, None)
        self.parent.dict_manager.entries = {}
        for d in self.parent.dict_manager.dictionaries.values():
            for word, definitions in d.items():
                self.parent.dict_manager.entries.setdefault(word, []).extend(definitions)
        self.parent.save_settings()
        self.parent.on_search(self.parent.search_entry)
        self.close()


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


if __name__ == '__main__':
    main()
