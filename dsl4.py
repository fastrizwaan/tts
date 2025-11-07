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
        self.dictionaries = {}  # {path: {name: str, entries: dict}}
        self.entries = {}       # {word: [(dict_name, definitions)]}
    
    def load_dictionary(self, path):
        path = Path(path)
        if not path.exists():
            return False
            
        try:
            content = None
            
            if path.suffix == '.dz':
                # Try reading .dz files with different encodings
                encodings = ['utf-16-le', 'utf-16', 'utf-8', 'cp1252', 'latin1']
                for enc in encodings:
                    try:
                        with gzip.open(path, 'rt', encoding=enc, errors='strict') as f:
                            content = f.read()
                        print(f"Successfully decoded .dz file with {enc}")
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                
                # If all encodings fail, try reading as binary and detecting BOM
                if content is None:
                    try:
                        with gzip.open(path, 'rb') as f:
                            raw_data = f.read()
                        
                        # Check for UTF-16 BOM
                        if raw_data.startswith(b'\xff\xfe'):
                            content = raw_data.decode('utf-16-le', errors='ignore')
                            print("Decoded .dz file with UTF-16-LE BOM")
                        elif raw_data.startswith(b'\xfe\xff'):
                            content = raw_data.decode('utf-16-be', errors='ignore')
                            print("Decoded .dz file with UTF-16-BE BOM")
                        else:
                            # Try UTF-8 as last resort
                            content = raw_data.decode('utf-8', errors='ignore')
                            print("Decoded .dz file with UTF-8 (fallback)")
                    except Exception as e:
                        print(f"Binary decode failed: {e}")
                        return False
            else:
                # For regular .dsl files
                # First try to read as binary to detect BOM
                try:
                    with open(path, 'rb') as f:
                        raw_data = f.read()
                    
                    # Check for UTF-16 BOM
                    if raw_data.startswith(b'\xff\xfe'):
                        content = raw_data.decode('utf-16-le', errors='ignore')
                        print("Decoded file with UTF-16-LE BOM")
                    elif raw_data.startswith(b'\xfe\xff'):
                        content = raw_data.decode('utf-16-be', errors='ignore')
                        print("Decoded file with UTF-16-BE BOM")
                    else:
                        # Try different encodings
                        encodings = ['utf-8', 'utf-16-le', 'utf-16', 'cp1252', 'latin1']
                        for enc in encodings:
                            try:
                                content = raw_data.decode(enc, errors='strict')
                                print(f"Successfully decoded file with {enc}")
                                break
                            except UnicodeDecodeError:
                                continue
                        
                        if content is None:
                            content = raw_data.decode('utf-8', errors='ignore')
                            print("Decoded file with UTF-8 (fallback)")
                except Exception as e:
                    print(f"Failed to decode file: {e}")
                    return False
            
            if content is None:
                print(f"Failed to decode {path}")
                return False
            
            # Extract dictionary name from #NAME tag
            dict_name = path.stem
            for line in content.splitlines()[:20]:  # Check first 20 lines
                if line.startswith('#NAME'):
                    name_match = re.search(r'#NAME\s+"([^"]+)"', line)
                    if name_match:
                        dict_name = name_match.group(1)
                    break
            
            entries = self._parse_dsl(content)
            print(f"Loaded {len(entries)} entries from {dict_name}")
            
            self.dictionaries[str(path)] = {
                'name': dict_name,
                'entries': entries
            }
            
            # Add to global entries with dictionary reference
            for word, definitions in entries.items():
                word_lower = word.lower()
                if word_lower not in self.entries:
                    self.entries[word_lower] = []
                # Store both the original word and the dict info
                self.entries[word_lower].append((word, dict_name, definitions))
            return True
        except Exception as e:
            print(f"Error loading {path}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _parse_dsl(self, content):
        entries = {}
        current_words = []  # List of headwords for current entry
        current_defs = []
        in_entry = False
        
        lines = content.splitlines()
        print(f"Processing {len(lines)} lines")
        
        # Skip header lines
        line_start = 0
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith('#'):
                line_start = i
                break
        
        for line_num, line in enumerate(lines[line_start:], start=line_start):
            line = line.rstrip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Check if this is a definition line (starts with tab or multiple spaces)
            if line.startswith('\t') or (line.startswith('  ') and len(line) > 1 and line[0].isspace()):
                in_entry = True
                cleaned_line = line.lstrip()
                if cleaned_line:
                    current_defs.append(cleaned_line)
            else:
                # This is a headword line
                if in_entry:
                    # We've hit a new entry, save the previous one
                    if current_words and current_defs:
                        for word in current_words:
                            if word not in entries:
                                entries[word] = []
                            entries[word].extend(current_defs)
                    
                    # Start new entry
                    current_words = []
                    current_defs = []
                    in_entry = False
                
                # Add this headword
                cleaned_word = self._clean_word(line)
                if cleaned_word:
                    current_words.append(cleaned_word)
                else:
                    # Debug: show what couldn't be cleaned
                    if line_num < 50:  # Only show first 50 for debugging
                        print(f"Line {line_num}: Could not clean '{line[:50]}'")
        
        # Don't forget the last entry
        if current_words and current_defs:
            for word in current_words:
                if word not in entries:
                    entries[word] = []
                entries[word].extend(current_defs)
        
        print(f"Parsed {len(entries)} dictionary entries")
        if len(entries) == 0 and len(lines) > 100:
            print("WARNING: No entries found! Showing first 20 non-empty lines for debugging:")
            count = 0
            for i, line in enumerate(lines):
                if line.strip() and not line.startswith('#'):
                    print(f"  Line {i}: '{line[:100]}'")
                    count += 1
                    if count >= 20:
                        break
        
        return entries
    
    def _clean_word(self, word):
        """Remove DSL markup from headwords"""
        # Remove common DSL tags
        word = re.sub(r'\[/?[^\]]*\]', '', word)  # Remove [tags] and [/tags]
        word = re.sub(r'\{.*?\}', '', word)  # Remove {tags}
        word = word.strip()
        return word
    
    def _clean_definition(self, text):
        """Remove DSL markup from definitions for display"""
        # Remove DSL formatting tags
        text = re.sub(r'\[/?[^\]]*\]', '', text)  # Remove all [tags]
        text = re.sub(r'\{.*?\}', '', text)  # Remove {tags}
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        return text
    
    def search(self, query):
        query = query.strip().lower()
        if not query:
            return []
        
        results = {}  # {original_word: [(dict_name, definitions)]}
        for word_lower, entries_list in self.entries.items():
            if query in word_lower:
                # Group by the original word (in case same word appears in multiple dicts)
                for original_word, dict_name, definitions in entries_list:
                    if original_word not in results:
                        results[original_word] = []
                    results[original_word].append((dict_name, definitions))
        
        # Sort by relevance (exact match first, then starts with, then contains)
        def sort_key(item):
            w = item[0].lower()
            if w == query:
                return (0, w)
            elif w.startswith(query):
                return (1, w)
            else:
                return (2, w)
        
        sorted_results = sorted(results.items(), key=sort_key)
        return sorted_results


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
        for word, dict_entries in results[:100]:
            # Word header
            word_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            word_box.set_margin_top(12)
            word_box.set_margin_bottom(2)
            word_box.set_margin_start(12)
            word_box.set_margin_end(12)
            
            word_label = Gtk.Label(label=word, xalign=0)
            word_label.add_css_class('title-3')
            word_box.append(word_label)
            self.listbox.append(word_box)
            
            # Show entries from each dictionary
            for dict_name, definitions in dict_entries:
                # Dictionary name separator
                dict_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                dict_box.set_margin_start(12)
                dict_box.set_margin_end(12)
                dict_box.set_margin_top(8)
                dict_box.set_margin_bottom(8)
                
                # Add separator line and dictionary name
                sep_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                sep_box.set_margin_bottom(4)
                
                separator1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                separator1.set_hexpand(True)
                sep_box.append(separator1)
                
                dict_label = Gtk.Label(label=dict_name)
                dict_label.add_css_class('caption')
                dict_label.add_css_class('dim-label')
                sep_box.append(dict_label)
                
                separator2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                separator2.set_hexpand(True)
                sep_box.append(separator2)
                
                dict_box.append(sep_box)
                
                # Clean and format definitions
                clean_defs = []
                for d in definitions[:5]:  # Show first 5 definitions per dictionary
                    clean_d = self.dict_manager._clean_definition(d)
                    if clean_d:
                        clean_defs.append(clean_d)
                
                # Definition label
                if clean_defs:
                    def_label = Gtk.Label(label='\n'.join(clean_defs), xalign=0, wrap=True)
                    def_label.add_css_class('body')
                    def_label.set_wrap(True)
                    def_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                    def_label.set_margin_start(8)
                    dict_box.append(def_label)
                
                self.listbox.append(dict_box)
    
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
                dict_info = parent.dict_manager.dictionaries.get(path, {})
                dict_name = dict_info.get('name', os.path.basename(path))
                entries = dict_info.get('entries', {})
                count = len(entries)
                title = f"{dict_name} ({count} entries)"
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
        for dict_info in self.parent.dict_manager.dictionaries.values():
            dict_name = dict_info['name']
            for word, definitions in dict_info['entries'].items():
                word_lower = word.lower()
                if word_lower not in self.parent.dict_manager.entries:
                    self.parent.dict_manager.entries[word_lower] = []
                self.parent.dict_manager.entries[word_lower].append((word, dict_name, definitions))
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
