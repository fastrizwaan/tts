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
                with gzip.open(path, 'rt', encoding='utf-16-le', errors='ignore') as f:
                    content = f.read()
            else:
                # Try different encodings
                encodings = ['utf-16-le', 'utf-16', 'utf-8', 'cp1252']
                content = None
                for enc in encodings:
                    try:
                        with open(path, 'r', encoding=enc, errors='ignore') as f:
                            content = f.read()
                        break
                    except:
                        continue
                
                if content is None:
                    print(f"Failed to decode {path}")
                    return False
            
            entries = self._parse_dsl(content)
            print(f"Loaded {len(entries)} entries from {path.name}")
            self.dictionaries[str(path)] = entries
            
            for word, definitions in entries.items():
                if word not in self.entries:
                    self.entries[word] = []
                self.entries[word].extend(definitions)
            return True
        except Exception as e:
            print(f"Error loading {path}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _parse_dsl(self, content):
        entries = {}
        current_word = None
        current_defs = []
        
        lines = content.splitlines()
        print(f"Processing {len(lines)} lines")
        
        for line in lines:
            line = line.rstrip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Check if this is a headword (doesn't start with space/tab)
            if not line[0].isspace():
                # Save previous entry
                if current_word and current_defs:
                    entries[current_word] = current_defs
                
                # Clean the word from DSL markup
                current_word = self._clean_word(line)
                current_defs = []
            else:
                # This is a definition line
                cleaned_line = line.lstrip()
                if cleaned_line:
                    current_defs.append(cleaned_line)
        
        # Don't forget the last entry
        if current_word and current_defs:
            entries[current_word] = current_defs
        
        print(f"Parsed {len(entries)} dictionary entries")
        return entries
    
    def _clean_word(self, word):
        """Remove DSL markup from headwords"""
        # Remove common DSL tags
        word = re.sub(r'\[.*?\]', '', word)  # Remove [tags]
        word = re.sub(r'\{.*?\}', '', word)  # Remove {tags}
        word = word.strip()
        return word
    
    def search(self, query):
        query = query.strip().lower()
        if not query:
            return []
        
        results = []
        for word, definitions in self.entries.items():
            if query in word.lower():
                results.append((word, definitions))
        
        # Sort by relevance (exact match first, then starts with, then contains)
        def sort_key(item):
            w = item[0].lower()
            if w == query:
                return (0, w)
            elif w.startswith(query):
                return (1, w)
            else:
                return (2, w)
        
        results.sort(key=sort_key)
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
        
        # Show initial message
        self.show_placeholder("Load a dictionary to start searching")
    
    def show_placeholder(self, message):
        """Show a placeholder message"""
        while (child := self.listbox.get_first_child()):
            self.listbox.remove(child)
        
        label = Gtk.Label(label=message)
        label.set_margin_top(40)
        label.set_margin_bottom(40)
        label.add_css_class('dim-label')
        self.listbox.append(label)
    
    def on_search(self, entry):
        query = entry.get_text()
        
        # Clear existing results
        while (child := self.listbox.get_first_child()):
            self.listbox.remove(child)
        
        if not query.strip():
            if self.dict_manager.entries:
                self.show_placeholder("Enter a search term")
            else:
                self.show_placeholder("Load a dictionary to start searching")
            return
        
        results = self.dict_manager.search(query)
        
        if not results:
            self.show_placeholder("No results found")
            return
        
        print(f"Found {len(results)} results for '{query}'")
        
        # Show results (limit to 100)
        for word, definitions in results[:100]:
            # Create a box for custom content to avoid markup parsing
            row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)
            row_box.set_margin_start(12)
            row_box.set_margin_end(12)
            
            # Word label (title)
            word_label = Gtk.Label(label=word, xalign=0)
            word_label.add_css_class('title-4')
            row_box.append(word_label)
            
            # Clean and format definitions
            clean_defs = []
            for d in definitions[:3]:  # Show first 3 definitions
                # Remove DSL markup for display
                clean_d = re.sub(r'\[.*?\]', '', d)
                clean_d = re.sub(r'\{.*?\}', '', clean_d)
                clean_d = clean_d.strip()
                if clean_d:
                    clean_defs.append(clean_d)
            
            # Definition label (subtitle)
            if clean_defs:
                def_label = Gtk.Label(label='\n'.join(clean_defs), xalign=0, wrap=True)
                def_label.add_css_class('dim-label')
                def_label.set_wrap(True)
                def_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                row_box.append(def_label)
            
            self.listbox.append(row_box)
    
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
                print(f"Loading dictionary: {path}")
                if self.dict_manager.load_dictionary(path):
                    self.save_settings()
                    # Clear search and show success message
                    self.search_entry.set_text("")
                    self.show_placeholder(f"Dictionary loaded! {len(self.dict_manager.entries)} words available")
                else:
                    self.show_error("Failed to load dictionary")
        except GLib.Error as e:
            if e.code != 2:  # Ignore dismiss/cancel
                print(f"File dialog error: {e}")
    
    def show_error(self, message):
        dialog = Adw.MessageDialog(
            heading="Error",
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present(self)  
        
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
                    print(f"Loading saved dictionary: {path}")
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

        self.set_content(page)

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
