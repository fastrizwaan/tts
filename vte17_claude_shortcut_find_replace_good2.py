#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, Pango, Gdk, PangoCairo, GObject
import threading
import time
import os
import re   
import cairo
from collections import deque
import chardet  # For encoding detection

# --- New class: FindReplaceBar ---
class FindReplaceBar(Gtk.Box):
    """Compact find & replace bar with regex, case, and highlight options."""

    def __init__(self, textview):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(8)
        self.set_margin_end(8)

        self.textview = textview
        # Don't store buffer reference - always get current from textview
        self.matches = []
        self.current_index = -1
        self.highlight_enabled = False

        # --- UI elements ---
        self.find_entry = Gtk.Entry(placeholder_text="Find…")
        self.replace_entry = Gtk.Entry(placeholder_text="Replace…")

        self.regex_switch = Gtk.Switch()
        self.case_switch = Gtk.Switch()
        self.highlight_switch = Gtk.Switch()

        self.regex_switch.set_tooltip_text("Enable Regular Expressions")
        self.case_switch.set_tooltip_text("Case Sensitive Search")
        self.highlight_switch.set_tooltip_text("Highlight All Matches")

        self.prev_btn = Gtk.Button(icon_name="go-up-symbolic")
        self.prev_btn.set_tooltip_text("Previous match")
        self.next_btn = Gtk.Button(icon_name="go-down-symbolic")
        self.next_btn.set_tooltip_text("Next match")
        self.replace_btn = Gtk.Button(label="Replace")
        self.replace_all_btn = Gtk.Button(label="Replace All")

        # --- Pack layout ---
        self.append(Gtk.Label(label="Find:"))
        self.append(self.find_entry)
        self.append(Gtk.Label(label="Replace:"))
        self.append(self.replace_entry)
        self.append(Gtk.Label(label="Regex"))
        self.append(self.regex_switch)
        self.append(Gtk.Label(label="Case"))
        self.append(self.case_switch)
        self.append(Gtk.Label(label="Highlight"))
        self.append(self.highlight_switch)
        self.append(self.prev_btn)
        self.append(self.next_btn)
        self.append(self.replace_btn)
        self.append(self.replace_all_btn)

        # --- Signal connections ---
        self.find_entry.connect("activate", self.on_find_next)
        self.next_btn.connect("clicked", self.on_find_next)
        self.prev_btn.connect("clicked", self.on_find_prev)
        self.replace_btn.connect("clicked", self.on_replace_one)
        self.replace_all_btn.connect("clicked", self.on_replace_all)
        self.highlight_switch.connect("notify::active", self.on_toggle_highlight)
        
        # Keep textview cursor visible when search entries get focus
        find_focus_controller = Gtk.EventControllerFocus.new()
        find_focus_controller.connect('enter', self._on_entry_focus)
        self.find_entry.add_controller(find_focus_controller)
        
        replace_focus_controller = Gtk.EventControllerFocus.new()
        replace_focus_controller.connect('enter', self._on_entry_focus)
        self.replace_entry.add_controller(replace_focus_controller)
        
        # Rebuild matches when search text or options change
        self.find_entry.connect("changed", self._on_search_changed)
        self.regex_switch.connect("notify::active", self._on_search_changed)
        self.case_switch.connect("notify::active", self._on_search_changed)
    
    def _on_search_changed(self, *args):
        """Clear matches when search parameters change"""
        self.matches.clear()
        self.current_index = -1
    
    def _on_entry_focus(self, controller):
        """Hide textview cursor when entry gets focus to avoid confusion"""
        self.textview.cursor_visible = False
        self.textview.queue_draw()

    # ------------------------------------------------------------------ #
    # Utility methods
    # ------------------------------------------------------------------ #
    def _compile_pattern(self):
        """Compile regex or plain pattern based on switches."""
        text = self.find_entry.get_text()
        if not text:
            return None
        try:
            # Interpret escape sequences: \n, \t, etc.
            text = text.encode('utf-8').decode('unicode_escape')
        except Exception:
            pass
        flags = 0 if self.case_switch.get_active() else re.IGNORECASE
        try:
            if self.regex_switch.get_active():
                return re.compile(text, flags)
            else:
                return re.compile(re.escape(text), flags)
        except re.error as e:
            print("Invalid regex:", e)
            return None
    
    def _get_replacement_text(self):
        """Get replacement text with escape sequences processed for non-regex mode."""
        replacement = self.replace_entry.get_text()
        # For regex mode, return as-is (backreferences and escape sequences handled by regex engine)
        if self.regex_switch.get_active():
            return replacement
        # For non-regex mode, interpret escape sequences like \n, \t
        try:
            return replacement.encode('utf-8').decode('unicode_escape')
        except Exception:
            return replacement

    def _collect_matches(self, pattern):
        """Collect all matches in buffer."""
        self.matches.clear()
        total_lines = len(self.textview.buffer.lines)
        for i, line in enumerate(self.textview.buffer.lines):
            # Add virtual newline for all lines except the last one
            # This allows searching for patterns that include \n
            if i < total_lines - 1:
                search_text = line + '\n'
            else:
                search_text = line
            
            for m in pattern.finditer(search_text):
                match_start = m.start()
                match_end = m.end()
                includes_newline = (match_end > len(line) and i < total_lines - 1)
                
                # Store match with info about whether it includes newline
                # Format: (line, start, end, includes_newline)
                if includes_newline:
                    self.matches.append((i, match_start, len(line), True))
                else:
                    self.matches.append((i, match_start, match_end, False))
        print(f"Found {len(self.matches)} matches")

    def _goto_match(self, index):
        """Move cursor to given match index."""
        if not self.matches:
            return
        index %= len(self.matches)
        self.current_index = index
        match_data = self.matches[index]
        line = match_data[0]
        start = match_data[1]
        end = match_data[2]
        # includes_newline = match_data[3] if len(match_data) > 3 else False
        
        # Update cursor position in textview
        self.textview.cursor_line = line
        self.textview.cursor_col = start
        
        # Set selection to highlight the match
        self.textview.selection_start_line = line
        self.textview.selection_start_col = start
        self.textview.selection_end_line = line
        self.textview.selection_end_col = end
        self.textview.has_selection = True
        
        # Ensure the match is visible
        self.textview._ensure_cursor_visible()
        self.textview.queue_draw()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def on_find_next(self, widget):
        """Find next match."""
        pattern = self._compile_pattern()
        if not pattern:
            return
        if not self.matches:
            self._collect_matches(pattern)
        if self.matches:
            self._goto_match(self.current_index + 1)

    def on_find_prev(self, widget):
        """Find previous match."""
        pattern = self._compile_pattern()
        if not pattern:
            return
        if not self.matches:
            self._collect_matches(pattern)
        if self.matches:
            self._goto_match(self.current_index - 1)

    def on_replace_one(self, widget):
        """Replace the currently selected match (if valid)."""
        pattern = self._compile_pattern()
        if not pattern:
            return
        
        # Collect matches if not already done
        if not self.matches:
            self._collect_matches(pattern)
        
        # Verify that the current selection matches the search pattern
        buffer = self.textview.buffer
        if not self.textview.has_selection:
            # No selection; just find next
            self.on_find_next(widget)
            return
        
        # Get selection bounds
        sel_start_line = self.textview.selection_start_line
        sel_start_col = self.textview.selection_start_col
        sel_end_line = self.textview.selection_end_line
        sel_end_col = self.textview.selection_end_col
        
        # For simplicity, only support single-line replacements
        if sel_start_line != sel_end_line:
            print("Multi-line replacements not supported")
            return
        
        line_idx = sel_start_line
        if 0 <= line_idx < len(buffer.lines):
            line_text = buffer.lines[line_idx]
            selected_text = line_text[sel_start_col:sel_end_col]
            
            # Check if the selected text matches the search pattern
            if pattern.fullmatch(selected_text):
                replacement = self._get_replacement_text()
                # Perform the replacement
                new_line = line_text[:sel_start_col] + replacement + line_text[sel_end_col:]
                buffer.lines[line_idx] = new_line
                buffer.modified = True
                
                # Clear selection
                self.textview.has_selection = False
                
                # Update cursor to end of replacement
                self.textview.cursor_line = line_idx
                self.textview.cursor_col = sel_start_col + len(replacement)
                
                # Clear matches to force re-collection on next search
                self.matches.clear()
                self.current_index = -1
                
                self.textview.queue_draw()
                
                # Find next match after replacement
                self.on_find_next(widget)
            else:
                # Selection doesn't match; find next
                self.on_find_next(widget)

    def on_replace_all(self, widget):
        """Replace all matches in the buffer."""
        pattern = self._compile_pattern()
        if not pattern:
            return
        
        # Collect all matches
        self._collect_matches(pattern)
        if not self.matches:
            print("No matches found")
            return
        
        replacement = self._get_replacement_text()
        buffer = self.textview.buffer
        
        # Process matches in reverse order to maintain correct indices
        for match_data in reversed(self.matches):
            line_idx = match_data[0]
            start = match_data[1]
            end = match_data[2]
            includes_newline = match_data[3] if len(match_data) > 3 else False
            
            if includes_newline:
                # Match includes newline; skip for simplicity
                print(f"Skipping match at line {line_idx} (includes newline)")
                continue
            
            if 0 <= line_idx < len(buffer.lines):
                line_text = buffer.lines[line_idx]
                new_line = line_text[:start] + replacement + line_text[end:]
                buffer.lines[line_idx] = new_line
        
        buffer.modified = True
        self.matches.clear()
        self.current_index = -1
        self.textview.has_selection = False
        self.textview.queue_draw()
        print(f"Replaced all matches")

    def on_toggle_highlight(self, switch, param):
        """Enable/disable match highlighting."""
        self.highlight_enabled = switch.get_active()
        if self.highlight_enabled:
            pattern = self._compile_pattern()
            if pattern:
                self._collect_matches(pattern)
        else:
            self.matches.clear()
            self.current_index = -1
        self.textview.queue_draw()

class StatusBar(Gtk.Box):
    """Simple status bar to display file info and cursor position."""
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self.label = Gtk.Label(label="Ready", xalign=0)
        self.label.set_hexpand(True)
        self.append(self.label)
    def set_text(self, text):
        self.label.set_text(text)

# --- Encoding detection utilities ---
def detect_encoding(file_path):
    """
    Detect the encoding of a file.
    Returns tuple: (encoding, confidence, has_bom)
    """
    # First check for BOM
    with open(file_path, 'rb') as f:
        start = f.read(4)
    
    # Check for UTF-8 BOM
    if start.startswith(b'\xef\xbb\xbf'):
        return ('utf-8-sig', 1.0, True)
    
    # Check for UTF-16 LE BOM
    if start.startswith(b'\xff\xfe'):
        # Could be UTF-16 LE or UTF-32 LE
        if start[2:4] == b'\x00\x00':
            return ('utf-32-le', 1.0, True)
        return ('utf-16-le', 1.0, True)
    
    # Check for UTF-16 BE BOM
    if start.startswith(b'\xfe\xff'):
        return ('utf-16-be', 1.0, True)
    
    # Check for UTF-32 LE BOM
    if start.startswith(b'\xff\xfe\x00\x00'):
        return ('utf-32-le', 1.0, True)
    
    # Check for UTF-32 BE BOM
    if start.startswith(b'\x00\x00\xfe\xff'):
        return ('utf-32-be', 1.0, True)
    
    # No BOM found, use chardet for detection
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(100000)  # Read first 100KB for detection
        
        result = chardet.detect(raw_data)
        encoding = result['encoding']
        confidence = result['confidence']
        
        # Normalize encoding names
        if encoding:
            encoding = encoding.lower()
            # Map common variations
            if encoding in ['ascii', 'us-ascii']:
                encoding = 'utf-8'  # ASCII is valid UTF-8
            elif encoding.startswith('utf-16'):
                # chardet might return utf-16 without specifying endianness
                if b'\x00' in raw_data[1::2] and b'\x00' not in raw_data[::2]:
                    encoding = 'utf-16-le'
                elif b'\x00' in raw_data[::2] and b'\x00' not in raw_data[1::2]:
                    encoding = 'utf-16-be'
        
        return (encoding or 'utf-8', confidence, False)
    except Exception as e:
        print(f"Encoding detection failed: {e}")
        return ('utf-8', 0.5, False)

def load_file_with_encoding(file_path, encoding=None):
    """
    Load a file with specified or auto-detected encoding.
    Returns tuple: (lines, encoding_used, detection_info)
    """
    if encoding is None:
        encoding, confidence, has_bom = detect_encoding(file_path)
        detection_info = f"Detected: {encoding} (confidence: {confidence:.0%})"
        if has_bom:
            detection_info += " [BOM]"
    else:
        detection_info = f"Specified: {encoding}"
        has_bom = False
    
    try:
        with open(file_path, 'r', encoding=encoding, errors='replace') as f:
            lines = [line.rstrip('\n\r') for line in f]
        return (lines, encoding, detection_info)
    except Exception as e:
        # Fallback to UTF-8 with error replacement
        print(f"Failed to load with {encoding}: {e}. Falling back to UTF-8.")
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = [line.rstrip('\n\r') for line in f]
        return (lines, 'utf-8', f"Fallback: utf-8 (original encoding failed)")

# --- Encoding selection dialog ---
class EncodingDialog(Gtk.Dialog):
    """Dialog for selecting file encoding when saving."""
    
    COMMON_ENCODINGS = [
        ('UTF-8', 'utf-8'),
        ('UTF-8 with BOM', 'utf-8-sig'),
        ('UTF-16 LE', 'utf-16-le'),
        ('UTF-16 BE', 'utf-16-be'),
        ('UTF-16 LE with BOM', 'utf-16'),
        ('UTF-32 LE', 'utf-32-le'),
        ('UTF-32 BE', 'utf-32-be'),
        ('ASCII', 'ascii'),
        ('Latin-1 (ISO-8859-1)', 'latin-1'),
        ('Windows-1252', 'cp1252'),
        ('GB2312 (Chinese)', 'gb2312'),
        ('GBK (Chinese)', 'gbk'),
        ('Big5 (Chinese)', 'big5'),
        ('Shift-JIS (Japanese)', 'shift-jis'),
        ('EUC-JP (Japanese)', 'euc-jp'),
        ('ISO-2022-JP (Japanese)', 'iso-2022-jp'),
        ('EUC-KR (Korean)', 'euc-kr'),
    ]
    
    def __init__(self, parent, current_encoding='utf-8'):
        super().__init__(
            title="Select File Encoding",
            transient_for=parent,
            modal=True
        )
        
        self.selected_encoding = current_encoding
        
        # Create UI
        content = self.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        
        # Info label
        info_label = Gtk.Label(
            label="Select the character encoding for saving this file:",
            wrap=True,
            xalign=0
        )
        content.append(info_label)
        
        # Encoding dropdown
        encoding_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        encoding_label = Gtk.Label(label="Encoding:")
        self.encoding_dropdown = Gtk.DropDown()
        
        # Create string list for dropdown
        string_list = Gtk.StringList()
        selected_index = 0
        for i, (display_name, encoding_name) in enumerate(self.COMMON_ENCODINGS):
            string_list.append(display_name)
            if encoding_name == current_encoding:
                selected_index = i
        
        self.encoding_dropdown.set_model(string_list)
        self.encoding_dropdown.set_selected(selected_index)
        self.encoding_dropdown.set_hexpand(True)
        
        encoding_box.append(encoding_label)
        encoding_box.append(self.encoding_dropdown)
        content.append(encoding_box)
        
        # Warning label for non-UTF-8 encodings
        self.warning_label = Gtk.Label(
            label="⚠ Some characters may be lost with this encoding",
            wrap=True,
            xalign=0
        )
        self.warning_label.add_css_class('warning')
        self.warning_label.set_visible(not current_encoding.startswith('utf'))
        content.append(self.warning_label)
        
        # Update warning when selection changes
        self.encoding_dropdown.connect('notify::selected', self._on_encoding_changed)
        
        # Buttons
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
    
    def _on_encoding_changed(self, dropdown, param):
        """Update warning when encoding selection changes."""
        index = dropdown.get_selected()
        _, encoding = self.COMMON_ENCODINGS[index]
        self.warning_label.set_visible(not encoding.startswith('utf'))
    
    def get_selected_encoding(self):
        """Get the selected encoding name."""
        index = self.encoding_dropdown.get_selected()
        return self.COMMON_ENCODINGS[index][1]

# ================================================================== #
# VirtualTextBuffer: Manages lines + undo/redo + syntax highlighting
# ================================================================== #
class VirtualTextBuffer:
    """Manages text lines with undo/redo and syntax highlighting."""
    
    def __init__(self):
        self.lines = []
        self.file_path = None
        self.modified = False
        self.word_wrap = False
        self.tab_width = 4
        
        # Encoding information
        self.encoding = 'utf-8'
        self.encoding_info = 'UTF-8'
        
        # Undo/redo stacks
        self.undo_stack = deque(maxlen=1000)
        self.redo_stack = deque(maxlen=1000)
        self._undo_group = []
        self._in_undo_redo = False
        
        # Syntax highlighting
        self.language = None
        self.tokens_cache = {}  # line_num -> list of (text, token_type)
        
    def load_lines(self, lines):
        """Load lines into the buffer."""
        self.lines = lines
        self.tokens_cache.clear()
        self.modified = False
        self.undo_stack.clear()
        self.redo_stack.clear()
    
    def set_language(self, lang):
        """Set syntax highlighting language."""
        self.language = lang
        self.tokens_cache.clear()
    
    def _begin_undo_group(self):
        """Begin grouping operations into a single undo action."""
        self._undo_group = []
    
    def _end_undo_group(self):
        """End undo group and push to stack."""
        if self._undo_group and not self._in_undo_redo:
            self.undo_stack.append(list(self._undo_group))
            self.redo_stack.clear()
            self._undo_group = []
    
    def _push_undo(self, operation):
        """Add operation to current undo group."""
        if not self._in_undo_redo:
            self._undo_group.append(operation)
    
    def insert_char(self, line, col, char):
        """Insert a character at position."""
        if 0 <= line < len(self.lines):
            old_text = self.lines[line]
            self.lines[line] = old_text[:col] + char + old_text[col:]
            self._push_undo(('delete_char', line, col, char))
            self.modified = True
            if line in self.tokens_cache:
                del self.tokens_cache[line]
    
    def delete_char(self, line, col):
        """Delete character at position."""
        if 0 <= line < len(self.lines) and col < len(self.lines[line]):
            old_char = self.lines[line][col]
            self.lines[line] = self.lines[line][:col] + self.lines[line][col + 1:]
            self._push_undo(('insert_char', line, col, old_char))
            self.modified = True
            if line in self.tokens_cache:
                del self.tokens_cache[line]
    
    def insert_newline(self, line, col):
        """Split line at column position."""
        if 0 <= line < len(self.lines):
            old_line = self.lines[line]
            self.lines[line] = old_line[:col]
            self.lines.insert(line + 1, old_line[col:])
            self._push_undo(('delete_newline', line, col))
            self.modified = True
            # Clear cache for affected lines
            keys_to_delete = [k for k in self.tokens_cache.keys() if k >= line]
            for k in keys_to_delete:
                del self.tokens_cache[k]
    
    def delete_newline(self, line):
        """Join line with next line."""
        if 0 <= line < len(self.lines) - 1:
            col = len(self.lines[line])
            next_line = self.lines.pop(line + 1)
            self.lines[line] += next_line
            self._push_undo(('insert_newline', line, col))
            self.modified = True
            # Clear cache for affected lines
            keys_to_delete = [k for k in self.tokens_cache.keys() if k >= line]
            for k in keys_to_delete:
                del self.tokens_cache[k]
    
    def undo(self):
        """Undo last operation group."""
        if not self.undo_stack:
            return False
        
        self._in_undo_redo = True
        operations = self.undo_stack.pop()
        redo_group = []
        
        for op in reversed(operations):
            if op[0] == 'insert_char':
                line, col, char = op[1:]
                self.insert_char(line, col, char)
                redo_group.append(('delete_char', line, col, char))
            elif op[0] == 'delete_char':
                line, col, char = op[1:]
                self.delete_char(line, col)
                redo_group.append(('insert_char', line, col, char))
            elif op[0] == 'insert_newline':
                line, col = op[1:]
                self.insert_newline(line, col)
                redo_group.append(('delete_newline', line, col))
            elif op[0] == 'delete_newline':
                line, col = op[1:]
                self.delete_newline(line)
                redo_group.append(('insert_newline', line, col))
        
        self.redo_stack.append(redo_group)
        self._in_undo_redo = False
        return True
    
    def redo(self):
        """Redo last undone operation group."""
        if not self.redo_stack:
            return False
        
        self._in_undo_redo = True
        operations = self.redo_stack.pop()
        undo_group = []
        
        for op in reversed(operations):
            if op[0] == 'insert_char':
                line, col, char = op[1:]
                self.insert_char(line, col, char)
                undo_group.append(('delete_char', line, col, char))
            elif op[0] == 'delete_char':
                line, col, char = op[1:]
                self.delete_char(line, col)
                undo_group.append(('insert_char', line, col, char))
            elif op[0] == 'insert_newline':
                line, col = op[1:]
                self.insert_newline(line, col)
                undo_group.append(('delete_newline', line, col))
            elif op[0] == 'delete_newline':
                line, col = op[1:]
                self.delete_newline(line)
                undo_group.append(('insert_newline', line, col))
        
        self.undo_stack.append(undo_group)
        self._in_undo_redo = False
        return True
    
    def get_tokenized_line(self, line_num):
        """Get tokenized version of a line for syntax highlighting."""
        if line_num in self.tokens_cache:
            return self.tokens_cache[line_num]
        
        if not self.language or line_num >= len(self.lines):
            return [(self.lines[line_num] if line_num < len(self.lines) else '', 'text')]
        
        line_text = self.lines[line_num]
        tokens = self._tokenize_line(line_text, self.language)
        self.tokens_cache[line_num] = tokens
        return tokens
    
    def _tokenize_line(self, line, language):
        """Tokenize a line based on language."""
        if language == 'python':
            return self._tokenize_python(line)
        return [(line, 'text')]
    
    def _tokenize_python(self, line):
        """Simple Python syntax tokenizer."""
        tokens = []
        
        # Keywords
        keywords = {
            'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
            'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from',
            'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not',
            'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
            'True', 'False', 'None'
        }
        
        # Built-in functions
        builtins = {
            'print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict',
            'set', 'tuple', 'bool', 'open', 'input', 'type', 'isinstance',
            'super', 'property', 'staticmethod', 'classmethod'
        }
        
        i = 0
        while i < len(line):
            # Skip whitespace
            if line[i].isspace():
                start = i
                while i < len(line) and line[i].isspace():
                    i += 1
                tokens.append((line[start:i], 'text'))
                continue
            
            # Comments
            if line[i] == '#':
                tokens.append((line[i:], 'comment'))
                break
            
            # Strings
            if line[i] in '"\'':
                quote = line[i]
                start = i
                i += 1
                # Check for triple quotes
                if i < len(line) - 1 and line[i:i+2] == quote * 2:
                    i += 2
                    triple = True
                else:
                    triple = False
                
                # Find end of string
                escaped = False
                while i < len(line):
                    if escaped:
                        escaped = False
                        i += 1
                        continue
                    if line[i] == '\\':
                        escaped = True
                        i += 1
                        continue
                    if triple:
                        if i < len(line) - 2 and line[i:i+3] == quote * 3:
                            i += 3
                            break
                    else:
                        if line[i] == quote:
                            i += 1
                            break
                    i += 1
                
                tokens.append((line[start:i], 'string'))
                continue
            
            # Numbers
            if line[i].isdigit():
                start = i
                while i < len(line) and (line[i].isdigit() or line[i] in '._xXbBoO'):
                    i += 1
                tokens.append((line[start:i], 'number'))
                continue
            
            # Identifiers and keywords
            if line[i].isalpha() or line[i] == '_':
                start = i
                while i < len(line) and (line[i].isalnum() or line[i] == '_'):
                    i += 1
                word = line[start:i]
                
                if word in keywords:
                    tokens.append((word, 'keyword'))
                elif word in builtins:
                    tokens.append((word, 'builtin'))
                else:
                    # Check if it's a function call
                    j = i
                    while j < len(line) and line[j].isspace():
                        j += 1
                    if j < len(line) and line[j] == '(':
                        tokens.append((word, 'function'))
                    else:
                        tokens.append((word, 'text'))
                continue
            
            # Operators and other characters
            tokens.append((line[i], 'operator'))
            i += 1
        
        return tokens if tokens else [('', 'text')]
    
    def save_to_file(self, path, encoding=None):
        """Save buffer to file with specified encoding."""
        if encoding is None:
            encoding = self.encoding
        
        try:
            with open(path, 'w', encoding=encoding, errors='replace') as f:
                for i, line in enumerate(self.lines):
                    f.write(line)
                    if i < len(self.lines) - 1:
                        f.write('\n')
            self.file_path = path
            self.encoding = encoding
            self.modified = False
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False

# ================================================================== #
# VirtualTextView: Custom drawing widget for text rendering
# ================================================================== #
class VirtualTextView(Gtk.DrawingArea):
    """Custom text rendering widget with virtual scrolling."""
    
    def __init__(self):
        super().__init__()
        self.set_can_focus(True)
        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        
        # Text buffer
        self.buffer = VirtualTextBuffer()
        
        # Cursor and selection
        self.cursor_line = 0
        self.cursor_col = 0
        self.cursor_blink_state = True
        self.cursor_visible = True
        self.has_selection = False
        self.selection_start_line = 0
        self.selection_start_col = 0
        self.selection_end_line = 0
        self.selection_end_col = 0
        
        # Rendering properties
        self.font_desc = Pango.FontDescription("Monospace 10")
        self.line_height = 20
        self.char_width = 10
        self.left_margin = 60
        self.top_margin = 5
        
        # Scrolling
        self.scroll_y = 0
        self.scroll_x = 0
        self.visible_lines = 0
        
        # Word wrap
        self._wrapped_lines_cache = {}
        self._needs_wrap_recalc = True
        
        # Drawing context
        self.set_draw_func(self._on_draw)
        
        # Input handling
        self._setup_input_handlers()
        
        # Cursor blink
        GLib.timeout_add(500, self._blink_cursor)
        
        # Settings
        self.show_line_numbers = True
        self.highlight_current_line = True
        self.show_whitespace = False
        
    def _setup_input_handlers(self):
        """Setup keyboard and mouse input handlers."""
        # Keyboard
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_controller)
        
        # Mouse click
        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect('pressed', self._on_mouse_pressed)
        self.add_controller(click_gesture)
        
        # Mouse drag for selection
        drag_gesture = Gtk.GestureDrag.new()
        drag_gesture.connect('drag-begin', self._on_drag_begin)
        drag_gesture.connect('drag-update', self._on_drag_update)
        drag_gesture.connect('drag-end', self._on_drag_end)
        self.add_controller(drag_gesture)
        
        # Scroll
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | 
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller.connect('scroll', self._on_scroll)
        self.add_controller(scroll_controller)
        
        # Focus
        focus_controller = Gtk.EventControllerFocus.new()
        focus_controller.connect('enter', self._on_focus_in)
        focus_controller.connect('leave', self._on_focus_out)
        self.add_controller(focus_controller)
    
    def set_buffer(self, buffer):
        """Set the text buffer."""
        self.buffer = buffer
        self.cursor_line = 0
        self.cursor_col = 0
        self.scroll_y = 0
        self.scroll_x = 0
        self.has_selection = False
        self._wrapped_lines_cache.clear()
        self._needs_wrap_recalc = True
        self.queue_draw()
    
    def _blink_cursor(self):
        """Blink cursor."""
        if self.is_focus() and self.cursor_visible:
            self.cursor_blink_state = not self.cursor_blink_state
            self.queue_draw()
        return True
    
    def _get_font_metrics(self, cr):
        """Get font metrics."""
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        layout.set_text("M", -1)
        width, height = layout.get_pixel_size()
        return width, height
    
    def _wrap_line(self, line_text, max_width):
        """Wrap a line to fit within max_width."""
        if not line_text or max_width <= 0:
            return [line_text]
        
        words = []
        current_word = ""
        
        # Split into words while preserving whitespace
        for char in line_text:
            if char.isspace():
                if current_word:
                    words.append(current_word)
                    current_word = ""
                words.append(char)
            else:
                current_word += char
        
        if current_word:
            words.append(current_word)
        
        wrapped = []
        current_line = ""
        current_width = 0
        
        for word in words:
            word_width = len(word) * self.char_width
            
            if current_width + word_width <= max_width:
                current_line += word
                current_width += word_width
            else:
                if current_line:
                    wrapped.append(current_line)
                
                # Handle very long words
                if word_width > max_width and not word.isspace():
                    # Break long word
                    chars_per_line = max(1, max_width // self.char_width)
                    for i in range(0, len(word), chars_per_line):
                        wrapped.append(word[i:i + chars_per_line])
                    current_line = ""
                    current_width = 0
                else:
                    current_line = word
                    current_width = word_width
        
        if current_line:
            wrapped.append(current_line)
        
        return wrapped if wrapped else [""]
    
    def _get_wrapped_lines(self, line_num):
        """Get wrapped lines for a given line number."""
        if not self.buffer.word_wrap:
            return [self.buffer.lines[line_num]] if line_num < len(self.buffer.lines) else [""]
        
        if line_num in self._wrapped_lines_cache and not self._needs_wrap_recalc:
            return self._wrapped_lines_cache[line_num]
        
        if line_num >= len(self.buffer.lines):
            return [""]
        
        width = self.get_width()
        max_width = width - self.left_margin - 20
        
        if max_width <= 0:
            return [self.buffer.lines[line_num]]
        
        wrapped = self._wrap_line(self.buffer.lines[line_num], max_width)
        self._wrapped_lines_cache[line_num] = wrapped
        return wrapped
    
    def _on_draw(self, area, cr, width, height):
        """Draw the text view."""
        # Background
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        
        # Update metrics
        self.char_width, self.line_height = self._get_font_metrics(cr)
        self.visible_lines = max(1, height // self.line_height)
        
        if self._needs_wrap_recalc and self.buffer.word_wrap:
            self._wrapped_lines_cache.clear()
            self._needs_wrap_recalc = False
        
        # Calculate visible range
        start_line = self.scroll_y
        end_line = min(start_line + self.visible_lines + 1, len(self.buffer.lines))
        
        y = self.top_margin
        
        for line_num in range(start_line, end_line):
            if line_num >= len(self.buffer.lines):
                break
            
            wrapped_lines = self._get_wrapped_lines(line_num)
            
            for wrap_idx, wrapped_text in enumerate(wrapped_lines):
                # Highlight current line
                if self.highlight_current_line and line_num == self.cursor_line and wrap_idx == 0:
                    cr.set_source_rgba(0.95, 0.95, 0.95, 1.0)
                    cr.rectangle(0, y - 2, width, self.line_height)
                    cr.fill()
                
                # Draw line number
                if self.show_line_numbers and wrap_idx == 0:
                    cr.set_source_rgb(0.5, 0.5, 0.5)
                    layout = PangoCairo.create_layout(cr)
                    layout.set_font_description(self.font_desc)
                    layout.set_text(f"{line_num + 1:4d}", -1)
                    cr.move_to(5, y)
                    PangoCairo.show_layout(cr, layout)
                
                # Draw selection background
                if self.has_selection:
                    self._draw_selection_bg(cr, line_num, wrapped_text, wrap_idx, y, width)
                
                # Draw text with syntax highlighting
                self._draw_text(cr, line_num, wrapped_text, wrap_idx, y)
                
                y += self.line_height
                
                if y > height:
                    break
        
        # Draw cursor
        if self.is_focus() and self.cursor_blink_state and self.cursor_visible:
            self._draw_cursor(cr)
    
    def _draw_selection_bg(self, cr, line_num, text, wrap_idx, y, width):
        """Draw selection background."""
        sel_start_line = min(self.selection_start_line, self.selection_end_line)
        sel_end_line = max(self.selection_start_line, self.selection_end_line)
        sel_start_col = self.selection_start_col if self.selection_start_line <= self.selection_end_line else self.selection_end_col
        sel_end_col = self.selection_end_col if self.selection_start_line <= self.selection_end_line else self.selection_start_col
        
        if sel_start_line == sel_end_line == line_num:
            # Single line selection
            start_x = self.left_margin + (sel_start_col - self.scroll_x) * self.char_width
            end_x = self.left_margin + (sel_end_col - self.scroll_x) * self.char_width
            
            cr.set_source_rgba(0.7, 0.8, 1.0, 0.4)
            cr.rectangle(start_x, y - 2, end_x - start_x, self.line_height)
            cr.fill()
        elif line_num >= sel_start_line and line_num <= sel_end_line:
            # Multi-line selection
            if line_num == sel_start_line:
                start_x = self.left_margin + (sel_start_col - self.scroll_x) * self.char_width
                end_x = width
            elif line_num == sel_end_line:
                start_x = self.left_margin
                end_x = self.left_margin + (sel_end_col - self.scroll_x) * self.char_width
            else:
                start_x = self.left_margin
                end_x = width
            
            cr.set_source_rgba(0.7, 0.8, 1.0, 0.4)
            cr.rectangle(start_x, y - 2, end_x - start_x, self.line_height)
            cr.fill()
    
    def _draw_text(self, cr, line_num, text, wrap_idx, y):
        """Draw text with syntax highlighting."""
        x = self.left_margin - (self.scroll_x * self.char_width)
        
        # Get tokens for syntax highlighting
        tokens = self.buffer.get_tokenized_line(line_num)
        
        # Calculate which part of the original line this wrapped segment represents
        if self.buffer.word_wrap and wrap_idx > 0:
            wrapped_lines = self._get_wrapped_lines(line_num)
            offset = sum(len(wrapped_lines[i]) for i in range(wrap_idx))
        else:
            offset = 0
        
        # Track position within the wrapped line
        char_pos = 0
        
        for token_text, token_type in tokens:
            # Skip tokens that are completely before this wrapped line
            if char_pos + len(token_text) <= offset:
                char_pos += len(token_text)
                continue
            
            # Trim token if it starts before this wrapped line
            if char_pos < offset:
                skip_chars = offset - char_pos
                token_text = token_text[skip_chars:]
                char_pos = offset
            
            # Stop if we've drawn all text for this wrapped line
            if char_pos >= offset + len(text):
                break
            
            # Trim token if it extends beyond this wrapped line
            remaining_chars = offset + len(text) - char_pos
            if len(token_text) > remaining_chars:
                token_text = token_text[:remaining_chars]
            
            # Set color based on token type
            if token_type == 'keyword':
                cr.set_source_rgb(0.75, 0.0, 0.75)  # Purple
            elif token_type == 'string':
                cr.set_source_rgb(0.0, 0.5, 0.0)  # Green
            elif token_type == 'comment':
                cr.set_source_rgb(0.5, 0.5, 0.5)  # Gray
            elif token_type == 'number':
                cr.set_source_rgb(0.0, 0.5, 0.75)  # Blue
            elif token_type == 'function':
                cr.set_source_rgb(0.0, 0.0, 1.0)  # Blue
            elif token_type == 'builtin':
                cr.set_source_rgb(0.5, 0.0, 0.5)  # Dark purple
            elif token_type == 'operator':
                cr.set_source_rgb(0.5, 0.5, 0.0)  # Dark yellow
            else:
                cr.set_source_rgb(0, 0, 0)  # Black
            
            # Draw the token text
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.font_desc)
            layout.set_text(token_text, -1)
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)
            
            x += len(token_text) * self.char_width
            char_pos += len(token_text)
    
    def _draw_cursor(self, cr):
        """Draw the cursor."""
        if self.cursor_line >= len(self.buffer.lines):
            return
        
        # Calculate cursor position considering word wrap
        wrapped_lines = self._get_wrapped_lines(self.cursor_line)
        
        # Find which wrapped line contains the cursor
        char_count = 0
        wrap_line_idx = 0
        for i, wrapped in enumerate(wrapped_lines):
            if char_count + len(wrapped) >= self.cursor_col or i == len(wrapped_lines) - 1:
                wrap_line_idx = i
                break
            char_count += len(wrapped)
        
        # Calculate visual line number (accounting for wrapped lines before cursor)
        visual_line = self.cursor_line - self.scroll_y
        for i in range(self.scroll_y, self.cursor_line):
            visual_line += len(self._get_wrapped_lines(i)) - 1
        visual_line += wrap_line_idx
        
        # Calculate column within the wrapped line
        col_in_wrap = self.cursor_col - char_count
        
        x = self.left_margin + (col_in_wrap - self.scroll_x) * self.char_width
        y = self.top_margin + visual_line * self.line_height
        
        cr.set_source_rgb(0, 0, 0)
        cr.set_line_width(2)
        cr.move_to(x, y)
        cr.line_to(x, y + self.line_height - 2)
        cr.stroke()
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard input."""
        # Check modifiers
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        
        # Ctrl+Z: Undo
        if ctrl and keyval == Gdk.KEY_z and not shift:
            if self.buffer.undo():
                self.queue_draw()
            return True
        
        # Ctrl+Shift+Z or Ctrl+Y: Redo
        if (ctrl and shift and keyval == Gdk.KEY_z) or (ctrl and keyval == Gdk.KEY_y):
            if self.buffer.redo():
                self.queue_draw()
            return True
        
        # Begin undo group for text operations
        self.buffer._begin_undo_group()
        
        # Navigation keys
        if keyval == Gdk.KEY_Left:
            self._move_cursor_left(shift)
            return True
        elif keyval == Gdk.KEY_Right:
            self._move_cursor_right(shift)
            return True
        elif keyval == Gdk.KEY_Up:
            self._move_cursor_up(shift)
            return True
        elif keyval == Gdk.KEY_Down:
            self._move_cursor_down(shift)
            return True
        elif keyval == Gdk.KEY_Home:
            if ctrl:
                self.scroll_to_top()
            else:
                self._move_cursor_home(shift)
            return True
        elif keyval == Gdk.KEY_End:
            if ctrl:
                self.scroll_to_bottom()
            else:
                self._move_cursor_end(shift)
            return True
        elif keyval == Gdk.KEY_Page_Up:
            self._move_page_up(shift)
            return True
        elif keyval == Gdk.KEY_Page_Down:
            self._move_page_down(shift)
            return True
        
        # Editing keys
        elif keyval == Gdk.KEY_BackSpace:
            self._handle_backspace()
            self.buffer._end_undo_group()
            return True
        elif keyval == Gdk.KEY_Delete:
            self._handle_delete()
            self.buffer._end_undo_group()
            return True
        elif keyval == Gdk.KEY_Return or keyval == Gdk.KEY_KP_Enter:
            self._handle_return()
            self.buffer._end_undo_group()
            return True
        elif keyval == Gdk.KEY_Tab:
            self._handle_tab()
            self.buffer._end_undo_group()
            return True
        
        # Regular character input
        elif not ctrl:
            char = chr(keyval) if 32 <= keyval <= 126 else None
            if char:
                self._insert_char(char)
                self.buffer._end_undo_group()
                return True
        
        return False
    
    def _move_cursor_left(self, shift):
        """Move cursor left."""
        if not shift:
            self.has_selection = False
        
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_line > 0:
            self.cursor_line -= 1
            self.cursor_col = len(self.buffer.lines[self.cursor_line])
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_cursor_right(self, shift):
        """Move cursor right."""
        if not shift:
            self.has_selection = False
        
        if self.cursor_line < len(self.buffer.lines):
            line_len = len(self.buffer.lines[self.cursor_line])
            if self.cursor_col < line_len:
                self.cursor_col += 1
            elif self.cursor_line < len(self.buffer.lines) - 1:
                self.cursor_line += 1
                self.cursor_col = 0
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_cursor_up(self, shift):
        """Move cursor up."""
        if not shift:
            self.has_selection = False
        
        if self.cursor_line > 0:
            self.cursor_line -= 1
            self.cursor_col = min(self.cursor_col, len(self.buffer.lines[self.cursor_line]))
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_cursor_down(self, shift):
        """Move cursor down."""
        if not shift:
            self.has_selection = False
        
        if self.cursor_line < len(self.buffer.lines) - 1:
            self.cursor_line += 1
            self.cursor_col = min(self.cursor_col, len(self.buffer.lines[self.cursor_line]))
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_cursor_home(self, shift):
        """Move cursor to beginning of line."""
        if not shift:
            self.has_selection = False
        
        self.cursor_col = 0
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_cursor_end(self, shift):
        """Move cursor to end of line."""
        if not shift:
            self.has_selection = False
        
        if self.cursor_line < len(self.buffer.lines):
            self.cursor_col = len(self.buffer.lines[self.cursor_line])
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_page_up(self, shift):
        """Move cursor up one page."""
        if not shift:
            self.has_selection = False
        
        self.cursor_line = max(0, self.cursor_line - self.visible_lines)
        self.cursor_col = min(self.cursor_col, len(self.buffer.lines[self.cursor_line]))
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _move_page_down(self, shift):
        """Move cursor down one page."""
        if not shift:
            self.has_selection = False
        
        self.cursor_line = min(len(self.buffer.lines) - 1, self.cursor_line + self.visible_lines)
        self.cursor_col = min(self.cursor_col, len(self.buffer.lines[self.cursor_line]))
        
        if shift:
            self._update_selection()
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _update_selection(self):
        """Update selection based on cursor position."""
        if not self.has_selection:
            self.has_selection = True
            self.selection_start_line = self.cursor_line
            self.selection_start_col = self.cursor_col
        
        self.selection_end_line = self.cursor_line
        self.selection_end_col = self.cursor_col
    
    def _insert_char(self, char):
        """Insert character at cursor."""
        if self.has_selection:
            self._delete_selection()
        
        self.buffer.insert_char(self.cursor_line, self.cursor_col, char)
        self.cursor_col += 1
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        
        # Clear wrap cache for this line
        if self.cursor_line in self._wrapped_lines_cache:
            del self._wrapped_lines_cache[self.cursor_line]
        
        self.queue_draw()
    
    def _handle_backspace(self):
        """Handle backspace key."""
        if self.has_selection:
            self._delete_selection()
            return
        
        if self.cursor_col > 0:
            self.cursor_col -= 1
            self.buffer.delete_char(self.cursor_line, self.cursor_col)
            
            # Clear wrap cache
            if self.cursor_line in self._wrapped_lines_cache:
                del self._wrapped_lines_cache[self.cursor_line]
        elif self.cursor_line > 0:
            self.cursor_col = len(self.buffer.lines[self.cursor_line - 1])
            self.cursor_line -= 1
            self.buffer.delete_newline(self.cursor_line)
            
            # Clear wrap cache
            if self.cursor_line in self._wrapped_lines_cache:
                del self._wrapped_lines_cache[self.cursor_line]
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _handle_delete(self):
        """Handle delete key."""
        if self.has_selection:
            self._delete_selection()
            return
        
        if self.cursor_line < len(self.buffer.lines):
            if self.cursor_col < len(self.buffer.lines[self.cursor_line]):
                self.buffer.delete_char(self.cursor_line, self.cursor_col)
                
                # Clear wrap cache
                if self.cursor_line in self._wrapped_lines_cache:
                    del self._wrapped_lines_cache[self.cursor_line]
            elif self.cursor_line < len(self.buffer.lines) - 1:
                self.buffer.delete_newline(self.cursor_line)
                
                # Clear wrap cache
                if self.cursor_line in self._wrapped_lines_cache:
                    del self._wrapped_lines_cache[self.cursor_line]
        
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _handle_return(self):
        """Handle return/enter key."""
        if self.has_selection:
            self._delete_selection()
        
        self.buffer.insert_newline(self.cursor_line, self.cursor_col)
        self.cursor_line += 1
        self.cursor_col = 0
        
        # Clear wrap cache for affected lines
        keys_to_delete = [k for k in self._wrapped_lines_cache.keys() if k >= self.cursor_line - 1]
        for k in keys_to_delete:
            del self._wrapped_lines_cache[k]
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _handle_tab(self):
        """Handle tab key."""
        if self.has_selection:
            self._delete_selection()
        
        # Insert spaces for tab
        spaces = ' ' * self.buffer.tab_width
        for char in spaces:
            self.buffer.insert_char(self.cursor_line, self.cursor_col, char)
            self.cursor_col += 1
        
        # Clear wrap cache
        if self.cursor_line in self._wrapped_lines_cache:
            del self._wrapped_lines_cache[self.cursor_line]
        
        self._ensure_cursor_visible()
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _delete_selection(self):
        """Delete the current selection."""
        if not self.has_selection:
            return
        
        start_line = min(self.selection_start_line, self.selection_end_line)
        end_line = max(self.selection_start_line, self.selection_end_line)
        start_col = self.selection_start_col if self.selection_start_line <= self.selection_end_line else self.selection_end_col
        end_col = self.selection_end_col if self.selection_start_line <= self.selection_end_line else self.selection_start_col
        
        if start_line == end_line:
            # Single line selection
            line = self.buffer.lines[start_line]
            new_line = line[:start_col] + line[end_col:]
            self.buffer.lines[start_line] = new_line
            
            # Clear wrap cache
            if start_line in self._wrapped_lines_cache:
                del self._wrapped_lines_cache[start_line]
        else:
            # Multi-line selection
            start_line_text = self.buffer.lines[start_line][:start_col]
            end_line_text = self.buffer.lines[end_line][end_col:]
            
            # Remove lines in between
            for _ in range(end_line - start_line):
                self.buffer.lines.pop(start_line + 1)
            
            # Merge start and end
            self.buffer.lines[start_line] = start_line_text + end_line_text
            
            # Clear wrap cache for affected lines
            keys_to_delete = [k for k in self._wrapped_lines_cache.keys() if k >= start_line]
            for k in keys_to_delete:
                del self._wrapped_lines_cache[k]
        
        self.cursor_line = start_line
        self.cursor_col = start_col
        self.has_selection = False
        self.buffer.modified = True
        
        # Clear tokens cache
        if start_line in self.buffer.tokens_cache:
            del self.buffer.tokens_cache[start_line]
    
    def _on_mouse_pressed(self, gesture, n_press, x, y):
        """Handle mouse click."""
        self.grab_focus()
        
        # Calculate line and column from click position
        line_num = int((y - self.top_margin) / self.line_height) + self.scroll_y
        col_num = int((x - self.left_margin) / self.char_width) + self.scroll_x
        
        # Clamp to valid range
        line_num = max(0, min(line_num, len(self.buffer.lines) - 1))
        if line_num < len(self.buffer.lines):
            col_num = max(0, min(col_num, len(self.buffer.lines[line_num])))
        else:
            col_num = 0
        
        self.cursor_line = line_num
        self.cursor_col = col_num
        self.has_selection = False
        
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _on_drag_begin(self, gesture, start_x, start_y):
        """Handle drag begin for selection."""
        # Set selection start point
        line_num = int((start_y - self.top_margin) / self.line_height) + self.scroll_y
        col_num = int((start_x - self.left_margin) / self.char_width) + self.scroll_x
        
        line_num = max(0, min(line_num, len(self.buffer.lines) - 1))
        if line_num < len(self.buffer.lines):
            col_num = max(0, min(col_num, len(self.buffer.lines[line_num])))
        else:
            col_num = 0
        
        self.selection_start_line = line_num
        self.selection_start_col = col_num
        self.has_selection = True
    
    def _on_drag_update(self, gesture, offset_x, offset_y):
        """Handle drag update for selection."""
        # The get_start_point() method returns (bool, x, y)
        success, start_x, start_y = gesture.get_start_point()
        if not success:
            # If we cannot get the start point, we cannot update the selection.
            # It's safer to return early.
            print("Failed to get start point from gesture during drag update.")
            return # Exit if start point is not available

        # Calculate current pointer position based on start point and offset
        x = start_x + offset_x
        y = start_y + offset_y

        # Update selection end point
        line_num = int((y - self.top_margin) / self.line_height) + self.scroll_y
        col_num = int((x - self.left_margin) / self.char_width) + self.scroll_x
        line_num = max(0, min(line_num, len(self.buffer.lines) - 1))
        if line_num < len(self.buffer.lines):
            col_num = max(0, min(col_num, len(self.buffer.lines[line_num])))
        else:
            col_num = 0
        self.selection_end_line = line_num
        self.selection_end_col = col_num
        self.cursor_line = line_num
        self.cursor_col = col_num
        self.queue_draw()

    
    def _on_drag_end(self, gesture, offset_x, offset_y):
        """Handle drag end."""
        # If start and end are the same, clear selection
        if (self.selection_start_line == self.selection_end_line and 
            self.selection_start_col == self.selection_end_col):
            self.has_selection = False
        
        self.queue_draw()
    
    def _on_scroll(self, controller, dx, dy):
        """Handle scroll events."""
        # Vertical scroll
        if dy != 0:
            self.scroll_y = max(0, min(self.scroll_y + int(dy * 3), 
                                      max(0, len(self.buffer.lines) - self.visible_lines)))
            self.queue_draw()
            return True
        
        # Horizontal scroll (only when not wrapping)
        if dx != 0 and not self.buffer.word_wrap:
            self.scroll_x = max(0, self.scroll_x + int(dx * 3))
            self.queue_draw()
            return True
        
        return False
    
    def _ensure_cursor_visible(self):
        """Ensure cursor is visible in the viewport."""
        # Vertical scrolling
        if self.cursor_line < self.scroll_y:
            self.scroll_y = self.cursor_line
        elif self.cursor_line >= self.scroll_y + self.visible_lines:
            self.scroll_y = self.cursor_line - self.visible_lines + 1
        
        # Horizontal scrolling (only when not wrapping)
        if not self.buffer.word_wrap:
            visible_cols = (self.get_width() - self.left_margin) // self.char_width
            if self.cursor_col < self.scroll_x:
                self.scroll_x = self.cursor_col
            elif self.cursor_col >= self.scroll_x + visible_cols:
                self.scroll_x = self.cursor_col - visible_cols + 1
    
    def scroll_to_top(self):
        """Scroll to top of document."""
        self.scroll_y = 0
        self.cursor_line = 0
        self.cursor_col = 0
        self.has_selection = False
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def scroll_to_bottom(self):
        """Scroll to bottom of document."""
        self.cursor_line = len(self.buffer.lines) - 1
        self.cursor_col = len(self.buffer.lines[self.cursor_line]) if self.cursor_line >= 0 else 0
        self.scroll_y = max(0, len(self.buffer.lines) - self.visible_lines)
        self.has_selection = False
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _on_focus_in(self, controller):
        """Handle focus in."""
        self.cursor_visible = True
        self.cursor_blink_state = True
        self.queue_draw()
    
    def _on_focus_out(self, controller):
        """Handle focus out."""
        self.cursor_visible = False
        self.queue_draw()

# ================================================================== #
# Settings Dialog
# ================================================================== #
class SettingsDialog(Gtk.Window):
    """Settings dialog for editor preferences."""
    
    def __init__(self, parent, text_view):
        super().__init__(title="Settings")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(400, 300)
        
        self.text_view = text_view
        
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        
        # Font size
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_label = Gtk.Label(label="Font Size:")
        font_label.set_xalign(0)
        font_label.set_hexpand(True)
        
        self.font_spin = Gtk.SpinButton()
        self.font_spin.set_range(6, 72)
        self.font_spin.set_increments(1, 2)
        current_size = int(self.text_view.font_desc.get_size() / Pango.SCALE)
        self.font_spin.set_value(current_size)
        self.font_spin.connect('value-changed', self._on_font_size_changed)
        
        font_box.append(font_label)
        font_box.append(self.font_spin)
        main_box.append(font_box)
        
        # Tab width
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab_label = Gtk.Label(label="Tab Width:")
        tab_label.set_xalign(0)
        tab_label.set_hexpand(True)
        
        self.tab_spin = Gtk.SpinButton()
        self.tab_spin.set_range(2, 8)
        self.tab_spin.set_increments(1, 2)
        self.tab_spin.set_value(self.text_view.buffer.tab_width)
        self.tab_spin.connect('value-changed', self._on_tab_width_changed)
        
        tab_box.append(tab_label)
        tab_box.append(self.tab_spin)
        main_box.append(tab_box)
        
        # Show line numbers
        line_numbers_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        line_numbers_label = Gtk.Label(label="Show Line Numbers:")
        line_numbers_label.set_xalign(0)
        line_numbers_label.set_hexpand(True)
        
        self.line_numbers_switch = Gtk.Switch()
        self.line_numbers_switch.set_active(self.text_view.show_line_numbers)
        self.line_numbers_switch.connect('notify::active', self._on_line_numbers_changed)
        
        line_numbers_box.append(line_numbers_label)
        line_numbers_box.append(self.line_numbers_switch)
        main_box.append(line_numbers_box)
        
        # Highlight current line
        highlight_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        highlight_label = Gtk.Label(label="Highlight Current Line:")
        highlight_label.set_xalign(0)
        highlight_label.set_hexpand(True)
        
        self.highlight_switch = Gtk.Switch()
        self.highlight_switch.set_active(self.text_view.highlight_current_line)
        self.highlight_switch.connect('notify::active', self._on_highlight_changed)
        
        highlight_box.append(highlight_label)
        highlight_box.append(self.highlight_switch)
        main_box.append(highlight_box)
        
        # Close button
        close_btn = Gtk.Button(label="Close")
        close_btn.connect('clicked', lambda b: self.close())
        main_box.append(close_btn)
        
        self.set_child(main_box)
    
    def _on_font_size_changed(self, spin):
        """Apply font size change immediately."""
        size = int(spin.get_value())
        self.text_view.font_desc.set_size(size * Pango.SCALE)
        self.text_view.queue_draw()
    
    def _on_tab_width_changed(self, spin):
        """Apply tab width change immediately."""
        self.text_view.buffer.tab_width = int(spin.get_value())
    
    def _on_line_numbers_changed(self, switch, param):
        """Apply line numbers setting immediately."""
        self.text_view.show_line_numbers = switch.get_active()
        self.text_view.queue_draw()
    
    def _on_highlight_changed(self, switch, param):
        """Apply highlight current line setting immediately."""
        self.text_view.highlight_current_line = switch.get_active()
        self.text_view.queue_draw()

# ================================================================== #
# Main Window
# ================================================================== #
class TextEditorWindow(Adw.ApplicationWindow):
    """Main application window."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(1000, 700)
        self.set_title("VTE17 Text Editor")
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Header bar
        header = Adw.HeaderBar()
        
        # Open button
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text("Open File (Ctrl+O)")
        open_btn.connect('clicked', lambda b: self.open_file())
        header.pack_start(open_btn)
        
        # Save button
        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.set_tooltip_text("Save File (Ctrl+S)")
        save_btn.connect('clicked', self.on_save_clicked)
        header.pack_start(save_btn)
        
        # Menu button
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        
        menu = Gio.Menu()
        menu.append("Generate Test Data", "app.generate_test")
        menu.append("Go to Top", "app.go_top")
        menu.append("Go to Bottom", "app.go_bottom")
        menu.append("Toggle Word Wrap", "app.toggle_wrap")
        menu.append("Settings", "app.settings")
        
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)
        
        main_box.append(header)
        
        # Text view
        self.text_view = VirtualTextView()
        
        # Scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scrolled.set_child(self.text_view)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        
        # Find/Replace bar
        self.find_replace_bar = FindReplaceBar(self.text_view)
        self.find_replace_bar.set_visible(False)
        
        # Content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self.find_replace_bar)
        content_box.append(scrolled)
        
        main_box.append(content_box)
        
        # Status bar
        self.status_bar = StatusBar()
        main_box.append(self.status_bar)
        
        # Vertical scrollbar
        self.v_scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL)
        self.v_scrollbar.set_visible(False)
        
        # Horizontal scrollbar
        self.h_scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL)
        self.h_scrollbar.set_visible(False)
        
        # Scrollbar adjustment connections
        self.v_scrollbar.get_adjustment().connect('value-changed', self._on_vscroll_changed)
        self.h_scrollbar.get_adjustment().connect('value-changed', self._on_hscroll_changed)
        
        # Overlay for scrollbars
        overlay = Gtk.Overlay()
        overlay.set_child(main_box)
        
        # Position scrollbars
        self.v_scrollbar.set_halign(Gtk.Align.END)
        self.v_scrollbar.set_valign(Gtk.Align.FILL)
        overlay.add_overlay(self.v_scrollbar)
        
        self.h_scrollbar.set_halign(Gtk.Align.FILL)
        self.h_scrollbar.set_valign(Gtk.Align.END)
        overlay.add_overlay(self.h_scrollbar)
        
        self.set_content(overlay)
        
        # Keyboard shortcuts
        self._setup_shortcuts()
        
        # Initialize with empty buffer
        self.text_view.buffer.lines = [""]
        
        # Update scrollbar initially
        GLib.timeout_add(100, self._update_scrollbar)
    
    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Ctrl+O: Open
        open_action = Gio.SimpleAction.new("open", None)
        open_action.connect("activate", lambda a, p: self.open_file())
        self.add_action(open_action)
        self.get_application().set_accels_for_action("win.open", ["<Ctrl>o"])
        
        # Ctrl+S: Save
        save_action = Gio.SimpleAction.new("save", None)
        save_action.connect("activate", lambda a, p: self.save_file())
        self.add_action(save_action)
        self.get_application().set_accels_for_action("win.save", ["<Ctrl>s"])
        
        # Ctrl+F: Find
        find_action = Gio.SimpleAction.new("find", None)
        find_action.connect("activate", self._on_find_activated)
        self.add_action(find_action)
        self.get_application().set_accels_for_action("win.find", ["<Ctrl>f"])
        
        # Ctrl+H: Replace
        replace_action = Gio.SimpleAction.new("replace", None)
        replace_action.connect("activate", self._on_replace_activated)
        self.add_action(replace_action)
        self.get_application().set_accels_for_action("win.replace", ["<Ctrl>h"])
        
        # Escape: Hide find/replace
        escape_action = Gio.SimpleAction.new("escape", None)
        escape_action.connect("activate", self._on_escape_activated)
        self.add_action(escape_action)
        self.get_application().set_accels_for_action("win.escape", ["Escape"])
        
        # Settings action
        settings_action = Gio.SimpleAction.new("settings_win", None)
        settings_action.connect("activate", self._on_settings_activated)
        self.add_action(settings_action)
    
    def _on_find_activated(self, action, param):
        """Show find/replace bar."""
        self.find_replace_bar.set_visible(True)
        self.find_replace_bar.replace_entry.set_visible(False)
        self.find_replace_bar.replace_btn.set_visible(False)
        self.find_replace_bar.replace_all_btn.set_visible(False)
        self.find_replace_bar.find_entry.grab_focus()
    
    def _on_replace_activated(self, action, param):
        """Show find/replace bar with replace options."""
        self.find_replace_bar.set_visible(True)
        self.find_replace_bar.replace_entry.set_visible(True)
        self.find_replace_bar.replace_btn.set_visible(True)
        self.find_replace_bar.replace_all_btn.set_visible(True)
        self.find_replace_bar.find_entry.grab_focus()
    
    def _on_escape_activated(self, action, param):
        """Hide find/replace bar and restore cursor visibility."""
        if self.find_replace_bar.get_visible():
            self.find_replace_bar.set_visible(False)
            self.text_view.cursor_visible = True
            self.text_view.grab_focus()
    
    def _on_settings_activated(self, action, param):
        """Open settings dialog."""
        settings_dialog = SettingsDialog(self, self.text_view)
        settings_dialog.present()
    
    def _on_vscroll_changed(self, adjustment):
        """Handle vertical scrollbar changes."""
        value = int(adjustment.get_value())
        self.text_view.scroll_y = value
        self.text_view.queue_draw()
    
    def _on_hscroll_changed(self, adjustment):
        """Handle horizontal scrollbar changes."""
        if not self.text_view.buffer.word_wrap:
            value = int(adjustment.get_value())
            self.text_view.scroll_x = value
            self.text_view.queue_draw()
    
    def _update_scrollbar(self):
        """Update scrollbar ranges and visibility."""
        buffer = self.text_view.buffer
        
        # Vertical scrollbar
        total_lines = len(buffer.lines)
        visible_lines = self.text_view.visible_lines
        
        if total_lines > visible_lines:
            adjustment = self.v_scrollbar.get_adjustment()
            adjustment.set_lower(0)
            adjustment.set_upper(total_lines)
            adjustment.set_page_size(visible_lines)
            adjustment.set_step_increment(1)
            adjustment.set_page_increment(visible_lines)
            adjustment.set_value(self.text_view.scroll_y)
            self.v_scrollbar.set_visible(True)
        else:
            self.v_scrollbar.set_visible(False)
        
        # Horizontal scrollbar (only when not wrapping)
        if not buffer.word_wrap:
            max_line_len = max((len(line) for line in buffer.lines), default=0)
            visible_cols = (self.text_view.get_width() - self.text_view.left_margin) // self.text_view.char_width
            
            if max_line_len > visible_cols:
                adjustment = self.h_scrollbar.get_adjustment()
                adjustment.set_lower(0)
                adjustment.set_upper(max_line_len)
                adjustment.set_page_size(visible_cols)
                adjustment.set_step_increment(1)
                adjustment.set_page_increment(visible_cols)
                adjustment.set_value(self.text_view.scroll_x)
                self.h_scrollbar.set_visible(True)
            else:
                self.h_scrollbar.set_visible(False)
        else:
            self.h_scrollbar.set_visible(False)
        
        return False
    
    def generate_test_data(self):
        """Generate test data."""
        def load_data():
            lines = [f"Line {i+1}: This is test line number {i+1}" for i in range(1000000)]
            load_time = 0.1
            GLib.idle_add(lambda: self._on_data_loaded(lines, load_time))
        
        self.status_bar.set_text("Generating 1 million lines...")
        thread = threading.Thread(target=load_data)
        thread.daemon = True
        thread.start()
    
    def _on_data_loaded(self, lines, load_time):
        buffer = VirtualTextBuffer()
        buffer.load_lines(lines)
        self.text_view.set_buffer(buffer)
        GLib.timeout_add(100, self._update_scrollbar)
        self.status_bar.set_text(f"Loaded {len(lines):,} lines in {load_time:.2f}s - Use arrow keys, Page Up/Down, Ctrl+Home/End to navigate")
        self.text_view.grab_focus()
    
    def open_file(self):
        dialog = Gtk.FileChooserDialog(
            title="Open File",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Open", Gtk.ResponseType.ACCEPT
        )
        dialog.connect('response', self._on_file_dialog_response)
        dialog.present()
    
    def _on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = dialog.get_files()
            if files:
                file = files[0]
                self._load_file(file.get_path())
        dialog.destroy()
        self.text_view.grab_focus()
    
    def _load_file(self, filepath):
        def load_file():
            try:
                start_time = time.time()
                lines, encoding, detection_info = load_file_with_encoding(filepath)
                load_time = time.time() - start_time
                GLib.idle_add(lambda: self._on_file_loaded(lines, load_time, filepath, encoding, detection_info))
            except Exception as e:
                GLib.idle_add(lambda: self._on_file_error(str(e)))
        
        self.status_bar.set_text(f"Loading {os.path.basename(filepath)}...")
        thread = threading.Thread(target=load_file)
        thread.daemon = True
        thread.start()
    
    def _on_file_loaded(self, lines, load_time, filepath, encoding, detection_info):
        buffer = VirtualTextBuffer()
        buffer.load_lines(lines)
        buffer.file_path = filepath
        buffer.encoding = encoding
        buffer.encoding_info = detection_info
        
        # Enable syntax highlighting for Python files
        _, ext = os.path.splitext(filepath)
        if ext.lower() == ".py":
            buffer.set_language("python")
        else:
            buffer.set_language(None)
        
        self.text_view.set_buffer(buffer)
        GLib.timeout_add(100, self._update_scrollbar)
        
        filename = os.path.basename(filepath)
        self.status_bar.set_text(f"Loaded {filename} - {len(lines):,} lines in {load_time:.2f}s - {detection_info}")
        self.text_view.grab_focus()
    
    def _on_file_error(self, error):
        self.status_bar.set_text(f"Error loading file: {error}")
        self.text_view.grab_focus()
    
    def go_to_top(self):
        self.text_view.scroll_to_top()
        self.text_view.grab_focus()
    
    def go_to_bottom(self):
        self.text_view.scroll_to_bottom()
        self.text_view.grab_focus()
    
    def on_save_clicked(self, button):
        self.save_file()
    
    def save_file(self, file_path=None, encoding=None):
        buffer = self.text_view.buffer
        path = file_path or buffer.file_path
        
        if not path:
            self.save_file_as()
            return
        
        # If encoding not specified, show encoding dialog
        if encoding is None and file_path is None:
            # Show encoding selection dialog
            encoding_dialog = EncodingDialog(self, buffer.encoding)
            encoding_dialog.connect('response', lambda d, r: self._on_encoding_dialog_response(d, r, path))
            encoding_dialog.present()
            return
        
        # Use specified encoding or buffer's current encoding
        save_encoding = encoding or buffer.encoding
        
        def save_in_thread():
            success = buffer.save_to_file(path, save_encoding)
            GLib.idle_add(lambda: self._on_file_saved(success, path, save_encoding))
        
        self.status_bar.set_text(f"Saving {os.path.basename(path)}...")
        thread = threading.Thread(target=save_in_thread)
        thread.daemon = True
        thread.start()
    
    def _on_encoding_dialog_response(self, dialog, response, path):
        """Handle encoding dialog response."""
        if response == Gtk.ResponseType.OK:
            encoding = dialog.get_selected_encoding()
            dialog.close()
            self.save_file(path, encoding)
        else:
            dialog.close()
            self.text_view.grab_focus()
    
    def save_file_as(self):
        dialog = Gtk.FileChooserDialog(
            title="Save As",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Save", Gtk.ResponseType.ACCEPT
        )
        dialog.connect('response', self._on_save_as_dialog_response)
        dialog.present()
    
    def _on_save_as_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = dialog.get_files()
            if files:
                file_path = files[0].get_path()
                if not os.path.splitext(file_path)[1]:
                    file_path += ".txt"
                
                # Update buffer's file path
                self.text_view.buffer.file_path = file_path
                
                # Enable syntax highlighting based on extension
                _, ext = os.path.splitext(file_path)
                if ext.lower() == ".py":
                    self.text_view.buffer.set_language("python")
                    self.text_view.queue_draw()
                else:
                    self.text_view.buffer.set_language(None)
                
                # Show encoding dialog
                encoding_dialog = EncodingDialog(self, self.text_view.buffer.encoding)
                encoding_dialog.connect('response', lambda d, r: self._on_encoding_dialog_response(d, r, file_path))
                encoding_dialog.present()
        
        dialog.destroy()
        if response == Gtk.ResponseType.CANCEL:
            self.text_view.grab_focus()
    
    def _on_file_saved(self, success, file_path, encoding):
        if success:
            filename = os.path.basename(file_path)
            self.status_bar.set_text(f"Saved {filename} as {encoding}")
        else:
            self.status_bar.set_text("Error saving file.")
        self.text_view.grab_focus()

# --- TextEditorApp updated for new actions ---
class TextEditorApp(Adw.Application):
    """Main application class"""
    
    def __init__(self):
        super().__init__(application_id="com.example.TextEditor")
        self.connect('activate', self.on_activate)
        self._create_actions()
    
    def _create_actions(self):
        """Create application actions"""
        open_action = Gio.SimpleAction.new("open_file", None)
        open_action.connect("activate", self.on_open_file)
        self.add_action(open_action)
        
        save_action = Gio.SimpleAction.new("save_file", None)
        save_action.connect("activate", self.on_save_file)
        self.add_action(save_action)
        
        generate_action = Gio.SimpleAction.new("generate_test", None)
        generate_action.connect("activate", self.on_generate_test)
        self.add_action(generate_action)
        
        top_action = Gio.SimpleAction.new("go_top", None)
        top_action.connect("activate", self.on_go_top)
        self.add_action(top_action)
        
        bottom_action = Gio.SimpleAction.new("go_bottom", None)
        bottom_action.connect("activate", self.on_go_bottom)
        self.add_action(bottom_action)
        
        wrap_action = Gio.SimpleAction.new("toggle_wrap", None)
        wrap_action.connect("activate", self.on_toggle_wrap)
        self.add_action(wrap_action)
        
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self.on_settings)
        self.add_action(settings_action)
    
    def on_activate(self, app):
        """Application activate signal handler"""
        self.window = TextEditorWindow(application=app)
        self.window.present()
        self.window_ref = self.window
    
    def on_open_file(self, action, param):
        """Open file action"""
        self.window_ref.open_file()
        self.window_ref.text_view.grab_focus()
    
    def on_save_file(self, action, param):
        """Save file action"""
        self.window_ref.save_file()
        self.window_ref.text_view.grab_focus()
    
    def on_generate_test(self, action, param):
        """Generate test data action"""
        self.window_ref.generate_test_data()
        self.window_ref.text_view.grab_focus()
    
    def on_go_top(self, action, param):
        """Go to top action"""
        self.window_ref.go_to_top()
    
    def on_go_bottom(self, action, param):
        """Go to bottom action"""
        self.window_ref.go_to_bottom()
    
    def on_toggle_wrap(self, action, param):
        """Toggle word wrap action"""
        if hasattr(self, 'window_ref') and self.window_ref.text_view:
            self.window_ref.text_view.buffer.word_wrap = not self.window_ref.text_view.buffer.word_wrap
            self.window_ref.text_view._needs_wrap_recalc = True
            self.window_ref.text_view._wrapped_lines_cache.clear()
            if self.window_ref.text_view.buffer.word_wrap:
                self.window_ref.text_view.scroll_x = 0
            self.window_ref.text_view.queue_draw()
            # Update scrollbar visibility
            self.window_ref._update_scrollbar()
            state = "On" if self.window_ref.text_view.buffer.word_wrap else "Off"
            self.window_ref.status_bar.set_text(f"Word Wrap: {state}")
            self.window_ref.text_view.grab_focus()
    
    def on_settings(self, action, param):
        """Open settings dialog"""
        self.window_ref._on_settings_activated(action, param)

def main():
    """Main entry point"""
    app = TextEditorApp()
    return app.run()

if __name__ == "__main__":
    main()
