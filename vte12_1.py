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
# --- Add near imports ---
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Pango, Gdk
import re, threading

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
        self.buffer = textview.buffer
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
        total_lines = len(self.buffer.lines)
        for i, line in enumerate(self.buffer.lines):
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
    def on_find_next(self, *a):
        pattern = self._compile_pattern()
        if not pattern:
            return
        if not self.matches:
            # Build matches asynchronously for huge files
            def worker():
                self._collect_matches(pattern)
                GLib.idle_add(lambda: self._goto_match(0))
            threading.Thread(target=worker, daemon=True).start()
        else:
            self._goto_match(self.current_index + 1)

    def on_find_prev(self, *a):
        if self.matches:
            self._goto_match(self.current_index - 1)

    def on_replace_one(self, *a):
        if not self.matches or self.current_index == -1:
            self.on_find_next()
            return
        pattern = self._compile_pattern()
        if not pattern:
            return
        
        # Finish any editing mode to prevent selection issues
        if self.textview.editing:
            self.textview._finish_editing()
        
        # Get current match info
        match_data = self.matches[self.current_index]
        line = match_data[0]
        start = match_data[1]
        end = match_data[2]
        includes_newline = match_data[3] if len(match_data) > 3 else False
        
        text = self.buffer.get_line(line)
        old_text = text  # Store for undo
        replacement = self._get_replacement_text()
        
        # Perform replacement properly with regex
        if self.regex_switch.get_active():
            # For regex with newline, need to search with the newline included
            search_text = text + '\n' if includes_newline and line < len(self.buffer.lines) - 1 else text
            
            # Find the match again to get the match object for proper group handling
            match_found = None
            for match in pattern.finditer(search_text):
                if match.start() == start:
                    match_found = match
                    break
            
            if match_found:
                # Use expand to handle backreferences like \1, \2, etc.
                try:
                    replaced_text = match_found.expand(replacement)
                except:
                    # Fallback to literal replacement if expand fails
                    replaced_text = replacement
                new_text = search_text[:start] + replaced_text + search_text[match_found.end():]
            else:
                # Fallback: simple string replacement
                if includes_newline:
                    new_text = text[:start] + replacement
                    if line < len(self.buffer.lines) - 1:
                        new_text += self.buffer.lines[line + 1]
                else:
                    new_text = text[:start] + replacement + text[end:]
        else:
            # Simple string replacement (escape sequences already processed)
            if includes_newline:
                # Join with next line
                new_text = text[:start] + replacement
                if line < len(self.buffer.lines) - 1:
                    new_text += self.buffer.lines[line + 1]
            else:
                new_text = text[:start] + replacement + text[end:]
        
        # Add undo action
        if includes_newline and line < len(self.buffer.lines) - 1:
            # This is a line merge operation
            next_line_text = self.buffer.lines[line + 1]
            self.buffer.add_undo_action('merge_lines', {
                'line': line,
                'split_pos': len(text[:start] + replacement),
                'next_line_text': next_line_text
            })
        else:
            # Simple replace operation
            self.buffer.add_undo_action('replace', {
                'line': line,
                'old_text': old_text,
                'new_text': new_text,
                'cursor_col': start
            })
        
        # Update buffer immediately
        self.buffer.lines[line] = new_text
        
        # If match included newline, remove the next line (it's now merged)
        if includes_newline and line < len(self.buffer.lines) - 1:
            del self.buffer.lines[line + 1]
            self.buffer.total_lines -= 1
        
        self.buffer.modified = True
        
        # Clear token cache for this line and next line if merged
        if line in self.buffer._token_cache:
            del self.buffer._token_cache[line]
        if includes_newline and line + 1 in self.buffer._token_cache:
            del self.buffer._token_cache[line + 1]
        
        # Clear wrapped lines cache if word wrap is enabled
        if self.buffer.word_wrap:
            self.textview._wrapped_lines_cache.clear()
            self.textview._needs_wrap_recalc = True
        
        # Clear selection to prevent drag selection issues
        self.textview.has_selection = False
        self.textview.selection_start_line = -1
        self.textview.selection_start_col = -1
        self.textview.selection_end_line = -1
        self.textview.selection_end_col = -1
        
        # Clear all matches to force rebuild on next search
        self.matches.clear()
        self.current_index = -1
        
        # Force immediate update of textview
        self.textview._recalculate_max_line_width()
        self.textview.emit('buffer-changed')
        self.textview.queue_draw()
        
        # Move to next match
        GLib.idle_add(self.on_find_next)

    def on_replace_all(self, *a):
        pattern = self._compile_pattern()
        if not pattern:
            return
        replacement = self._get_replacement_text()
        
        # Finish any editing mode to prevent selection issues
        if self.textview.editing:
            self.textview._finish_editing()
        
        # Check if the search pattern contains \n (newline)
        search_text = self.find_entry.get_text()
        has_newline = '\\n' in search_text or '\n' in search_text
        
        # Store old lines for undo
        old_lines = self.buffer.lines.copy()

        def worker():
            # If pattern might match newlines, work on entire text
            if has_newline:
                # Pattern matches newlines, work on entire text
                full_text = '\n'.join(self.buffer.lines)
                new_text = pattern.sub(replacement, full_text)
                
                if new_text != full_text:
                    # Split back into lines
                    new_lines = new_text.split('\n')
                    
                    def on_complete():
                        old_line_count = len(self.buffer.lines)
                        
                        # Add undo action
                        self.buffer.add_undo_action('replace_all', {
                            'old_lines': old_lines,
                            'new_lines': new_lines.copy()
                        })
                        
                        self.buffer.lines = new_lines
                        self.buffer.total_lines = len(new_lines)
                        self.buffer.modified = True
                        
                        # Clear all caches
                        self.buffer._token_cache.clear()
                        self.textview._wrapped_lines_cache.clear()
                        self.textview._needs_wrap_recalc = True
                        
                        # Clear matches
                        self.matches.clear()
                        self.current_index = -1
                        
                        # Clear selection to prevent drag issues
                        self.textview.has_selection = False
                        self.textview.selection_start_line = -1
                        self.textview.selection_start_col = -1
                        self.textview.selection_end_line = -1
                        self.textview.selection_end_col = -1
                        
                        # Force immediate update
                        self.textview._recalculate_max_line_width()
                        self.textview.emit('buffer-changed')
                        self.textview.queue_draw()
                        
                        print(f"Replace all complete: {old_line_count} lines -> {len(new_lines)} lines")
                    
                    GLib.idle_add(on_complete)
                else:
                    print("No matches found for replace all")
            else:
                # Pattern doesn't match newlines, work line by line (more efficient)
                modified_lines = []
                old_line_texts = {}
                for i, line in enumerate(self.buffer.lines):
                    new_line = pattern.sub(replacement, line)
                    if new_line != line:
                        old_line_texts[i] = line
                        self.buffer.lines[i] = new_line
                        modified_lines.append(i)
                
                def on_complete():
                    if modified_lines:
                        # Add undo action
                        self.buffer.add_undo_action('replace_all', {
                            'old_lines': old_lines,
                            'new_lines': self.buffer.lines.copy()
                        })
                        
                        self.buffer.modified = True
                        
                        # Clear token cache for modified lines
                        for line_num in modified_lines:
                            if line_num in self.buffer._token_cache:
                                del self.buffer._token_cache[line_num]
                        
                        # Clear wrapped lines cache if word wrap is enabled
                        if self.buffer.word_wrap:
                            self.textview._wrapped_lines_cache.clear()
                            self.textview._needs_wrap_recalc = True
                        
                        # Clear matches after replace all
                        self.matches.clear()
                        self.current_index = -1
                        
                        # Clear selection to prevent drag issues
                        self.textview.has_selection = False
                        self.textview.selection_start_line = -1
                        self.textview.selection_start_col = -1
                        self.textview.selection_end_line = -1
                        self.textview.selection_end_col = -1
                        
                        # Force immediate update
                        self.textview._recalculate_max_line_width()
                        self.textview.emit('buffer-changed')
                        self.textview.queue_draw()
                        
                        print(f"Replaced in {len(modified_lines)} lines")
                
                GLib.idle_add(on_complete)

        threading.Thread(target=worker, daemon=True).start()

    def on_toggle_highlight(self, switch, _):
        self.highlight_enabled = switch.get_active()
        if self.highlight_enabled:
            pattern = self._compile_pattern()
            self.textview.highlight_matches = True
            self.textview.highlight_pattern = pattern
        else:
            self.textview.highlight_matches = False
            self.textview.highlight_pattern = None
        self.textview.queue_draw()


# --- VirtualTextBuffer with syntax highlighting support ---
class VirtualTextBuffer:
    """Virtual text buffer that can handle millions of lines efficiently"""
    def __init__(self):
        self.highlight_matches = False
        self.highlight_pattern = None

        self.lines = [""]
        self.total_lines = 1
        self.line_height = 20 # Will be calculated dynamically
        self.char_width = 8 # Will be calculated dynamically
        self.modified = False
        # --- New: Track file path and word wrap state ---
        self.file_path = None
        self.word_wrap = False # Default to no word wrap
        # --- Syntax highlighting support ---
        self.syntax_highlight = False
        self.language = None # e.g., "python"
        self._token_cache = {}
        
        # --- Undo/Redo system ---
        self.undo_stack = deque(maxlen=1000)  # Limit to 1000 undo operations for memory
        self.redo_stack = deque(maxlen=1000)
        self.in_undo_redo = False  # Flag to prevent adding undo during undo/redo
    def set_language(self, lang):
        """Set language for syntax highlighting"""
        self.language = lang.lower() if lang else None
        self.syntax_highlight = (self.language == "python")
        self._token_cache.clear()
    def _tokenize_python_line(self, line_text):
        """Tokenize a Python line into (text, type) tuples"""
        if not line_text:
            return []
        tokens = []
        pos = 0
        length = len(line_text)
        # Skip leading whitespace
        while pos < length and line_text[pos].isspace():
            end = pos
            while end < length and line_text[end].isspace():
                end += 1
            tokens.append((line_text[pos:end], "whitespace"))
            pos = end
        # Match comments
        if pos < length and line_text[pos] == '#':
            tokens.append((line_text[pos:], "comment"))
            return tokens
        # Match strings: single/double quoted, including triple-quoted
        string_pattern = r'''(?P(?:[rR])?(?:"""|''' + '''|'|"))(?P.*?)(?P=triple)'''
        import re
        while pos < length:
            match = None
            # Try triple-quoted strings
            for delim in ['"""', "'''"]:
                if line_text.startswith(delim, pos):
                    end = line_text.find(delim, pos + 3)
                    if end != -1:
                        tokens.append((line_text[pos:end + 3], "string"))
                        pos = end + 3
                        break
            else:
                # Try single/double quoted strings
                if line_text[pos] in "\"'":
                    quote = line_text[pos]
                    escaped = False
                    end = pos + 1
                    while end < length:
                        if line_text[end] == '\\' and not escaped:
                            escaped = True
                            end += 1
                        elif line_text[end] == quote and not escaped:
                            end += 1
                            tokens.append((line_text[pos:end], "string"))
                            pos = end
                            break
                        else:
                            escaped = False
                            end += 1
                    else:
                        tokens.append((line_text[pos:], "string"))
                        return tokens
                else:
                    # Match numbers (int, float, hex, bin, etc.)
                    if re.match(r'[0-9]', line_text[pos:]):
                        match = re.match(r'\b(0[xX][0-9a-fA-F]+|[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?)\b', line_text[pos:])
                        if match:
                            tokens.append((match.group(), "number"))
                            pos += match.end()
                            continue
                    # Match identifiers, keywords, built-ins
                    if re.match(r'[a-zA-Z_]', line_text[pos:]):
                        match = re.match(r'[a-zA-Z_][a-zA-Z0-9_]*', line_text[pos:])
                        if match:
                            word = match.group()
                            if word in [
                                'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
                                'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
                                'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield'
                            ]:
                                tokens.append((word, "keyword"))
                            elif word in ['int', 'str', 'list', 'dict', 'tuple', 'set', 'bool', 'float', 'len', 'range', 'print', 'type']:
                                tokens.append((word, "builtin"))
                            else:
                                tokens.append((word, "identifier"))
                            pos += match.end()
                            continue
                    # Match operators and punctuation
                    if line_text[pos] in '+-*/%=<>!&|^~@':
                        op = line_text[pos]
                        if pos + 1 < length and line_text[pos + 1] == '=':
                            op += '='
                            pos += 2
                        elif op in '<>' and pos + 1 < length and line_text[pos + 1] == op:
                            op += line_text[pos + 1]
                            pos += 2
                        else:
                            pos += 1
                        tokens.append((op, "operator"))
                        continue
                    # Default: single character
                    tokens.append((line_text[pos], "other"))
                    pos += 1
        return tokens
    def get_line_tokens(self, line_number):
        """Get syntax tokens for a line"""
        if not self.syntax_highlight:
            return None
        if line_number in self._token_cache:
            return self._token_cache[line_number]
        line_text = self.get_line(line_number)
        if self.language == "python":
            tokens = self._tokenize_python_line(line_text)
        else:
            tokens = None
        self._token_cache[line_number] = tokens
        return tokens
    def load_lines(self, lines_data):
        """Load lines from data (list or generator)"""
        if isinstance(lines_data, list):
            self.lines = lines_data[:] # Make a copy for editing
        else:
            self.lines = list(lines_data)
        self.total_lines = len(self.lines)
        self.modified = False
        self._token_cache.clear()
    def get_line(self, line_number):
        """Get a specific line by number (0-indexed)"""
        if 0 <= line_number < self.total_lines:
            return self.lines[line_number]
        return ""
    def get_visible_lines(self, start_line, end_line):
        """Get a range of visible lines"""
        start = max(0, start_line)
        end = min(self.total_lines, end_line + 1)
        return self.lines[start:end]
    def set_line(self, line_number, text):
        """Set the content of a specific line"""
        if 0 <= line_number < self.total_lines:
            self.lines[line_number] = text
            self.modified = True
            # Clear token cache when line changes
            if line_number in self._token_cache:
                del self._token_cache[line_number]
    def insert_line(self, line_number, text):
        """Insert a new line at the specified position"""
        if 0 <= line_number <= self.total_lines:
            self.lines.insert(line_number, text)
            self.total_lines += 1
            self.modified = True
            # Clear token cache
            self._token_cache.clear()
    def delete_line(self, line_number):
        """Delete a line at the specified position"""
        if 0 <= line_number < self.total_lines and self.total_lines > 1:
            del self.lines[line_number]
            self.total_lines -= 1
            self.modified = True
            # Clear token cache
            self._token_cache.clear()
    # --- New: Save method ---
    def save_to_file(self, file_path=None):
        """Save buffer content to a file."""
        path = file_path or self.file_path
        if not path:
            raise ValueError("No file path provided and buffer has no associated file.")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.lines))
            self.file_path = path
            self.modified = False
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False
    
    # --- Undo/Redo system methods ---
    def add_undo_action(self, action_type, data):
        """
        Add an action to the undo stack.
        action_type: 'insert', 'delete', 'replace', 'insert_line', 'delete_line', 'merge_lines'
        data: dict with action-specific data
        """
        if self.in_undo_redo:
            return
        
        self.undo_stack.append({
            'type': action_type,
            'data': data.copy()
        })
        # Clear redo stack when new action is performed
        self.redo_stack.clear()
    
    def undo(self):
        """Undo the last action"""
        if not self.undo_stack:
            return False
        
        self.in_undo_redo = True
        action = self.undo_stack.pop()
        action_type = action['type']
        data = action['data']
        
        try:
            if action_type == 'insert':
                # Undo insert: delete the text
                line = data['line']
                start = data['start']
                end = data['end']
                old_text = self.lines[line]
                new_text = old_text[:start] + old_text[end:]
                self.lines[line] = new_text
                # Add to redo stack
                self.redo_stack.append(action)
                cursor_pos = (line, start)
                
            elif action_type == 'delete':
                # Undo delete: insert the text back
                line = data['line']
                pos = data['pos']
                text = data['text']
                old_line = self.lines[line]
                new_line = old_line[:pos] + text + old_line[pos:]
                self.lines[line] = new_line
                self.redo_stack.append(action)
                cursor_pos = (line, pos + len(text))
                
            elif action_type == 'replace':
                # Undo replace: restore old text
                line = data['line']
                old_text = data['old_text']
                self.lines[line] = old_text
                self.redo_stack.append(action)
                cursor_pos = (line, data.get('cursor_col', 0))
                
            elif action_type == 'insert_line':
                # Undo insert line: delete the line
                line = data['line']
                del self.lines[line]
                self.total_lines -= 1
                self.redo_stack.append(action)
                cursor_pos = (max(0, line - 1), 0)
                
            elif action_type == 'delete_line':
                # Undo delete line: insert the line back
                line = data['line']
                text = data['text']
                self.lines.insert(line, text)
                self.total_lines += 1
                self.redo_stack.append(action)
                cursor_pos = (line, 0)
                
            elif action_type == 'merge_lines':
                # Undo merge: split lines back
                line = data['line']
                split_pos = data['split_pos']
                line_text = self.lines[line]
                part1 = line_text[:split_pos]
                part2 = line_text[split_pos:]
                self.lines[line] = part1
                self.lines.insert(line + 1, part2)
                self.total_lines += 1
                self.redo_stack.append(action)
                cursor_pos = (line, len(part1))
            
            elif action_type == 'replace_all':
                # Undo replace all: restore all old lines
                old_lines = data['old_lines']
                self.lines = old_lines.copy()
                self.total_lines = len(self.lines)
                self.redo_stack.append(action)
                cursor_pos = (0, 0)
            
            else:
                cursor_pos = (0, 0)
            
            self.modified = True
            self._token_cache.clear()
            self.in_undo_redo = False
            return cursor_pos
            
        except Exception as e:
            print(f"Error during undo: {e}")
            self.in_undo_redo = False
            return False
    
    def redo(self):
        """Redo the last undone action"""
        if not self.redo_stack:
            return False
        
        self.in_undo_redo = True
        action = self.redo_stack.pop()
        action_type = action['type']
        data = action['data']
        
        try:
            if action_type == 'insert':
                # Redo insert: insert the text again
                line = data['line']
                start = data['start']
                text = data['text']
                old_text = self.lines[line]
                new_text = old_text[:start] + text + old_text[start:]
                self.lines[line] = new_text
                self.undo_stack.append(action)
                cursor_pos = (line, start + len(text))
                
            elif action_type == 'delete':
                # Redo delete: delete the text again
                line = data['line']
                pos = data['pos']
                end = data['end']
                old_line = self.lines[line]
                new_line = old_line[:pos] + old_line[end:]
                self.lines[line] = new_line
                self.undo_stack.append(action)
                cursor_pos = (line, pos)
                
            elif action_type == 'replace':
                # Redo replace: apply new text
                line = data['line']
                new_text = data['new_text']
                self.lines[line] = new_text
                self.undo_stack.append(action)
                cursor_pos = (line, data.get('cursor_col', 0))
                
            elif action_type == 'insert_line':
                # Redo insert line: insert the line
                line = data['line']
                text = data['text']
                self.lines.insert(line, text)
                self.total_lines += 1
                self.undo_stack.append(action)
                cursor_pos = (line, 0)
                
            elif action_type == 'delete_line':
                # Redo delete line: delete the line
                line = data['line']
                del self.lines[line]
                self.total_lines -= 1
                self.undo_stack.append(action)
                cursor_pos = (max(0, line - 1), 0)
                
            elif action_type == 'merge_lines':
                # Redo merge: merge lines again
                line = data['line']
                next_line_text = data['next_line_text']
                self.lines[line] += next_line_text
                del self.lines[line + 1]
                self.total_lines -= 1
                self.undo_stack.append(action)
                cursor_pos = (line, data['split_pos'])
            
            elif action_type == 'replace_all':
                # Redo replace all: apply new lines
                new_lines = data['new_lines']
                self.lines = new_lines.copy()
                self.total_lines = len(self.lines)
                self.undo_stack.append(action)
                cursor_pos = (0, 0)
            
            else:
                cursor_pos = (0, 0)
            
            self.modified = True
            self._token_cache.clear()
            self.in_undo_redo = False
            return cursor_pos
            
        except Exception as e:
            print(f"Error during redo: {e}")
            self.in_undo_redo = False
            return False
# --- VirtualTextView with syntax highlighting ---
class VirtualTextView(Gtk.DrawingArea):
    """Custom text view with virtual scrolling for millions of lines"""
    # Default syntax highlighting colors (Atom One theme)
    SYNTAX_COLORS = {
        "keyword": {
            "light": (0.7, 0.25, 0.3), # Red
            "dark": (0.95, 0.45, 0.55) # Red
        },
        "type": {
            "light": (0.6, 0.1, 0.8), # Purple
            "dark": (0.8, 0.5, 1.0) # Purple
        },
        "builtin": {
            "light": (0.6, 0.1, 0.8), # Purple
            "dark": (0.8, 0.5, 1.0) # Purple
        },
        "string": {
            "light": (0.1, 0.6, 0.3), # Green
            "dark": (0.3, 0.9, 0.5) # Green
        },
        "number": {
            "light": (0.8, 0.5, 0.1), # Orange
            "dark": (1.0, 0.7, 0.3) # Orange
        },
        "operator": {
            "light": (0.3, 0.3, 0.3), # Gray
            "dark": (0.7, 0.7, 0.7) # Light gray
        },
        "comment": {
            "light": (0.5, 0.5, 0.5), # Gray
            "dark": (0.6, 0.6, 0.6) # Light gray
        },
        "identifier": {
            "light": (0.1, 0.1, 0.1), # Black
            "dark": (0.9, 0.9, 0.9) # White
        },
        "other": {
            "light": (0.2, 0.2, 0.2), # Dark gray
            "dark": (0.8, 0.8, 0.8) # Light gray
        },
        "whitespace": {
            "light": (0.8, 0.8, 0.8), # Light gray
            "dark": (0.3, 0.3, 0.3) # Dark gray
        }
    }
    __gsignals__ = {
        'buffer-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'scroll-changed': (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        'modified-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }
    def __init__(self):
        super().__init__()
        self.buffer = VirtualTextBuffer()
        self.scroll_y = 0
        self.scroll_x = 0
        self.line_height = 20
        self.char_width = 8
        self.visible_lines = 0
        self.font_desc = Pango.FontDescription("Monospace 12")
        self.wrap_width = -1
        self._wrapped_lines_cache = {}
        self._needs_wrap_recalc = True
        self.cursor_line = 0
        self.cursor_col = 0
        self.cursor_visible = True
        self.editing = False
        self.edit_line = 0
        self.edit_text = ""
        self.edit_cursor_pos = 0
        self.selection_start_line = -1
        self.selection_start_col = -1
        self.selection_end_line = -1
        self.selection_end_col = -1
        self.has_selection = False
        self.last_click_time = 0
        self.click_count = 0
        self.last_click_x = 0
        self.last_click_y = 0
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.in_drag = False
        self.max_line_width = 0
        self.in_selection_drag = False
        self.pending_line = 0
        self.pending_col = 0
        self.pending_shift = False
        self.pending_line_text = ""
        self.is_pasting = False
        
        # --- Word-level undo tracking ---
        self.word_buffer = ""  # Accumulate characters until word boundary
        self.word_start_line = -1
        self.word_start_col = -1
        self.last_action_was_insert = False
        self.last_action_was_delete = False
        
        # Setup widget properties for input
        self.set_can_focus(True)
        self.set_focusable(True)
        self.set_draw_func(self._on_draw)
        self.connect('realize', self._on_realize)
        # Setup scrolling
        v_scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        v_scroll_controller.connect('scroll', self._on_v_scroll)
        self.add_controller(v_scroll_controller)
        h_scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.HORIZONTAL)
        h_scroll_controller.connect('scroll', self._on_h_scroll)
        self.add_controller(h_scroll_controller)
        # Setup key input - CRITICAL: Use legacy key controller for better IME support
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        # Also handle key release for completeness
        key_controller.connect('key-released', self._on_key_released)
        self.add_controller(key_controller)
        # Setup mouse input
        click_controller = Gtk.GestureClick()
        click_controller.connect('pressed', self._on_click)
        click_controller.connect('released', self._on_click_release)
        self.add_controller(click_controller)
        # Setup right-click
        right_click_controller = Gtk.GestureClick()
        right_click_controller.set_button(3)
        right_click_controller.connect('pressed', self._on_right_click_pressed)
        self.add_controller(right_click_controller)
        # Setup drag and drop
        drag_gesture_select = Gtk.GestureDrag()
        drag_gesture_select.connect('drag-begin', self._on_drag_begin_select)
        drag_gesture_select.connect('drag-update', self._on_drag_update_select)
        drag_gesture_select.connect('drag-end', self._on_drag_end_select)
        self.add_controller(drag_gesture_select)
        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.add_controller(drag_source)
        drop_target = Gtk.DropTarget.new(str, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drop_target.connect('accept', self._on_drop_accept)
        drop_target.connect('motion', self._on_drop_motion)
        drop_target.connect('drop', self._on_drop_drop)
        self.add_controller(drop_target)
        # Setup clipboard
        self.clipboard = Gdk.Display.get_default().get_clipboard()
        # Setup cursor blinking
        self.cursor_blink_timeout = None
        self._start_cursor_blink()
        # Setup IME AFTER widget is set up
        self.im_context = None
        self._setup_ime()
        # Setup focus handling
        focus_controller = Gtk.EventControllerFocus.new()
        focus_controller.connect('enter', self._on_focus_in)
        focus_controller.connect('leave', self._on_focus_out)
        self.add_controller(focus_controller)
        # Setup context menu
        self.context_menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()
        menu_model.append("Cut", "win.cut")
        menu_model.append("Copy", "win.copy")
        menu_model.append("Paste", "win.paste")
        menu_model.append("Delete", "win.delete")
        menu_model.append("Select All", "win.select_all")
        self.context_menu.set_menu_model(menu_model)
        self.context_menu.set_parent(self)
        # --- Find/Replace integration flags ---
        self.highlight_matches = False
        self.highlight_pattern = None

    def _on_right_click_pressed(self, gesture, n_press, x, y):
        # Grab focus and show cursor when right-clicked
        self.grab_focus()
        self.cursor_visible = True
        
        # Position cursor at click location
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
        if x > line_num_width:
            # Get position from coordinates
            line, col = self._get_position_from_coords(x, y)
            if line != -1:
                # Move cursor to click position
                self.cursor_line = line
                self.cursor_col = col
                
                # If we're clicking outside a selection, clear it
                # If clicking inside a selection, keep it for cut/copy operations
                if not self._is_position_in_selection(line, col):
                    self.has_selection = False
                    self.selection_start_line = -1
                    self.selection_start_col = -1
                    self.selection_end_line = -1
                    self.selection_end_col = -1
                
                # Finish any active editing on different line
                if self.editing and self.edit_line != self.cursor_line:
                    self._finish_editing()
                elif self.editing:
                    self.edit_cursor_pos = self.cursor_col
                
                self._ensure_cursor_visible()
                self.queue_draw()
        
        # Get the parent window to access actions
        window = self.get_root()
        if window and hasattr(window, 'cut_action'):
            window.cut_action.set_enabled(self.has_selection)
            window.copy_action.set_enabled(self.has_selection)
            window.delete_action.set_enabled(self.has_selection)
            window.paste_action.set_enabled(True)
            window.select_all_action.set_enabled(True)
        
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.context_menu.set_pointing_to(rect)
        self.context_menu.popup()
    def _select_all(self):
        self.selection_start_line = 0
        self.selection_start_col = 0
        self.selection_end_line = self.buffer.total_lines - 1
        self.selection_end_col = len(self.buffer.get_line(self.buffer.total_lines - 1))
        self.has_selection = True
        self.cursor_line = self.selection_end_line
        self.cursor_col = self.selection_end_col
        self._ensure_cursor_visible()
        self.queue_draw()
    def _setup_ime(self):
        """Setup Input Method Editor support for Unicode input"""
        try:
            # Use a more comprehensive IME context
            self.im_context = Gtk.IMMulticontext()
            # Connect IME signals
            self.im_context.connect("commit", self._on_im_commit)
            self.im_context.connect("preedit-start", self._on_preedit_start)
            self.im_context.connect("preedit-end", self._on_preedit_end)
            self.im_context.connect("preedit-changed", self._on_preedit_changed)
            # Initialize preedit state
            self.preedit_string = ""
            self.preedit_attrs = None
            self.preedit_cursor_pos = 0
            self.in_preedit = False
            print("IME context set up successfully")
        except Exception as e:
            print(f"Failed to setup IME: {e}")
            # Fallback to simple context
            self.im_context = Gtk.IMContextSimple()
            self.im_context.connect("commit", self._on_im_commit)
    def _on_preedit_start(self, im_context):
        """Handle preedit start - composition begins"""
        self.in_preedit = True
        print("Preedit started")
    def _on_preedit_end(self, im_context):
        """Handle preedit end - composition finished"""
        self.in_preedit = False
        self.preedit_string = ""
        self.preedit_attrs = None
        self.preedit_cursor_pos = 0
        self.queue_draw()
        print("Preedit ended")
    def _on_preedit_changed(self, im_context):
        """Handle preedit changes - composition text changes"""
        try:
            preedit_string, attrs, cursor_pos = self.im_context.get_preedit_string()
            self.preedit_string = preedit_string or ""
            self.preedit_attrs = attrs
            self.preedit_cursor_pos = cursor_pos
            print(f"Preedit changed: '{self.preedit_string}' cursor at {cursor_pos}")
            self.queue_draw()
        except Exception as e:
            print(f"Error in preedit changed: {e}")
    def _on_focus_in(self, controller):
        """Handle focus in - important for IME"""
        print("Focus in - setting up IME")
        # Restore cursor visibility when textview gains focus
        self.cursor_visible = True
        self.queue_draw()
        if self.im_context:
            self.im_context.focus_in()
            self.im_context.set_client_widget(self)
            self._update_im_cursor_location()
    def _on_focus_out(self, controller):
        """Handle focus out"""
        print("Focus out")
        if self.im_context:
            self.im_context.focus_out()
    def _update_im_cursor_location(self):
        """Update IME cursor location for better positioning of input windows"""
        if not self.im_context:
            return
        try:
            # Calculate cursor position on screen
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
            # Get current line text
            line_text = self.buffer.get_line(self.cursor_line)
            if self.editing and self.cursor_line == self.edit_line:
                line_text = self.edit_text
                cursor_col = self.edit_cursor_pos
                if self.in_preedit:
                    cursor_col += self.preedit_cursor_pos
            else:
                cursor_col = self.cursor_col
            # Calculate cursor x position
            cursor_text = line_text[:cursor_col]
            cursor_x_pos = self._get_text_width(cursor_text)
            screen_x = line_num_width + 10 - self.scroll_x + cursor_x_pos
            # Calculate cursor y position
            cursor_y = self.cursor_line * self.line_height - self.scroll_y
            # Create cursor rectangle
            cursor_rect = Gdk.Rectangle()
            cursor_rect.x = int(max(0, screen_x))
            cursor_rect.y = int(max(0, cursor_y))
            cursor_rect.width = 2
            cursor_rect.height = self.line_height
            # Set the cursor location for IME
            self.im_context.set_cursor_location(cursor_rect)
            print(f"Updated IME cursor location: {cursor_rect.x}, {cursor_rect.y}")
        except Exception as e:
            print(f"IME cursor location update failed: {e}")
    def _on_im_commit(self, im_context, text):
        """Handle IME text commit - this is where Unicode input happens"""
        if not text:
            return
        # If we're not in editing mode and we receive text input, start editing
        if not self.editing:
            print("Starting edit mode for IME input")
            self._start_editing()
        # Handle selection deletion first
        if self.has_selection:
            self._delete_selection()
            # Commit any pending word when deleting selection
            self._commit_word_to_undo()
        
        # Insert text in editing mode
        if self.editing:
            print(f"Inserting '{text}' at position {self.edit_cursor_pos}")
            
            # Word-level undo tracking
            for i, char in enumerate(text):
                is_boundary = self._is_word_boundary(char)
                
                # If this is a word boundary and we have accumulated text, commit it
                if is_boundary and self.word_buffer:
                    self._commit_word_to_undo()
                
                # Start new word buffer if needed
                if not is_boundary:
                    if not self.word_buffer:
                        # Starting a new word
                        self.word_start_line = self.edit_line
                        self.word_start_col = self.edit_cursor_pos + i
                        self.last_action_was_insert = True
                        self.last_action_was_delete = False
                    self.word_buffer += char
                elif is_boundary:
                    # Boundary character - treat as separate single-char undo
                    if char.strip():  # Only for non-whitespace boundaries
                        self.buffer.add_undo_action('insert', {
                            'line': self.edit_line,
                            'start': self.edit_cursor_pos + i,
                            'end': self.edit_cursor_pos + i + 1,
                            'text': char
                        })
                    else:
                        # Whitespace - add to word buffer to group consecutive spaces
                        if not self.word_buffer:
                            self.word_start_line = self.edit_line
                            self.word_start_col = self.edit_cursor_pos + i
                        self.word_buffer += char
            
            # Insert text directly in edit mode for immediate feedback
            self.edit_text = (self.edit_text[:self.edit_cursor_pos] + text + self.edit_text[self.edit_cursor_pos:])
            self.edit_cursor_pos += len(text)
            self.cursor_col = self.edit_cursor_pos
            self.buffer.set_line(self.edit_line, self.edit_text)
            # Update line width calculations
            if not self.buffer.word_wrap:
                new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                if new_line_width > self.max_line_width:
                    self.max_line_width = new_line_width
            else:
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
            self._ensure_cursor_visible()
            self.queue_draw()
            self.emit('modified-changed', self.buffer.modified)
        else:
            # Fallback to the general text insertion method
            print("Using fallback text insertion")
            self._insert_text_at_cursor(text)
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events"""
        # Get the actual event for IME processing
        event = controller.get_current_event()
        print(f"Key pressed: {keyval} ({Gdk.keyval_name(keyval)}) keycode: {keycode}")
        # CRITICAL: Let IME handle the input first for all keys except special navigation
        # Only bypass IME for certain control keys
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
        alt_pressed = state & Gdk.ModifierType.ALT_MASK
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
        # Commit preedit before handling selection or global shortcuts
        navigation_keys = {Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End}
        if self.in_preedit and (ctrl_pressed or (shift_pressed and keyval in navigation_keys)):
            self._on_im_commit(self.im_context, self.preedit_string)
            self.im_context.reset()
            self.in_preedit = False
            self.preedit_string = ""
            self.preedit_cursor_pos = 0
            self.queue_draw()
        # Don't send navigation and control keys to IME
        navigation_keys = {
            Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right,
            Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down,
            Gdk.KEY_Escape, Gdk.KEY_F1, Gdk.KEY_F2, Gdk.KEY_F3, Gdk.KEY_F4,
            Gdk.KEY_F5, Gdk.KEY_F6, Gdk.KEY_F7, Gdk.KEY_F8, Gdk.KEY_F9,
            Gdk.KEY_F10, Gdk.KEY_F11, Gdk.KEY_F12
        }
        # Don't send Ctrl shortcuts to IME
        if not (ctrl_pressed or keyval in navigation_keys):
            if self.im_context and self.im_context.filter_keypress(event):
                #print("Key handled by IME")
                return True
        # Handle Ctrl shortcuts
        if ctrl_pressed and not state & Gdk.ModifierType.SHIFT_MASK and not alt_pressed:
            if keyval == Gdk.KEY_c:
                if self.has_selection:
                    self._copy_to_clipboard()
                return True
            elif keyval == Gdk.KEY_x:
                if self.has_selection:
                    self._cut_to_clipboard()
                return True
            elif keyval == Gdk.KEY_v:
                self._paste_from_clipboard()
                return True
            elif keyval == Gdk.KEY_s:
                self.get_root().on_save_file(None, None)
                return True
            elif keyval == Gdk.KEY_z:
                # Ensure last word is committed before undo
                self._commit_word_to_undo()
                # Undo
                result = self.buffer.undo()
                if result:
                    line, col = result
                    self.cursor_line = line
                    self.cursor_col = col
                    self.has_selection = False
                    # Cancel editing mode
                    if self.editing:
                        self.editing = False
                    self._ensure_cursor_visible()
                    self.emit('buffer-changed')
                    self.queue_draw()
                self.grab_focus()
                return True
            elif keyval == Gdk.KEY_y:
                # Redo (Ctrl+Y)
                result = self.buffer.redo()
                if result:
                    line, col = result
                    self.cursor_line = line
                    self.cursor_col = col
                    self.has_selection = False
                    # Cancel editing mode
                    if self.editing:
                        self.editing = False
                    self._ensure_cursor_visible()
                    self.emit('buffer-changed')
                    self.queue_draw()
                self.grab_focus()
                return True
            elif keyval == Gdk.KEY_a:
                self._select_all()
                return True
            elif keyval == Gdk.KEY_w:
                self.buffer.word_wrap = not self.buffer.word_wrap
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
                if self.buffer.word_wrap:
                    self.scroll_x = 0
                self.queue_draw()
                self.grab_focus()
                return True
        # Handle Ctrl+Shift shortcuts
        if ctrl_pressed and shift_pressed and not alt_pressed:
            if keyval == Gdk.KEY_z or keyval == Gdk.KEY_Z:
                # Redo (Ctrl+Shift+Z)
                result = self.buffer.redo()
                if result:
                    line, col = result
                    self.cursor_line = line
                    self.cursor_col = col
                    self.has_selection = False
                    # Cancel editing mode
                    if self.editing:
                        self.editing = False
                    self._ensure_cursor_visible()
                    self.emit('buffer-changed')
                    self.queue_draw()
                self.grab_focus()
                return True
        
        # Handle Tab key - insert tab character instead of changing focus
        if keyval == Gdk.KEY_Tab and not ctrl_pressed and not alt_pressed:
            if self.has_selection:
                self._delete_selection()
            if not self.editing:
                self._start_editing()
            if self.editing:
                # Add undo action for tab insertion
                old_text = self.edit_text
                self.buffer.add_undo_action('insert', {
                    'line': self.edit_line,
                    'start': self.edit_cursor_pos,
                    'end': self.edit_cursor_pos + 1,
                    'text': '\t'
                })
                self.edit_text = self.edit_text[:self.edit_cursor_pos] + '\t' + self.edit_text[self.edit_cursor_pos:]
                self.edit_cursor_pos += 1
                self.cursor_col = self.edit_cursor_pos
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('modified-changed', self.buffer.modified)
            return True
        
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
        # Handle navigation and special keys if not in editing
        if not self.editing:
            # Commit any pending word when navigating away
            if keyval in [Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down]:
                self._commit_word_to_undo()
            
            if keyval in [Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right]:
                if shift_pressed and not self.has_selection:
                    self.selection_start_line = self.cursor_line
                    self.selection_start_col = self.cursor_col
                if keyval == Gdk.KEY_Up:
                    self._move_cursor_up(shift_pressed)
                elif keyval == Gdk.KEY_Down:
                    self._move_cursor_down(shift_pressed)
                elif keyval == Gdk.KEY_Left:
                    if ctrl_pressed:
                        self._move_cursor_word_left(shift_pressed)
                    else:
                        self._move_cursor_left(shift_pressed)
                elif keyval == Gdk.KEY_Right:
                    if ctrl_pressed:
                        self._move_cursor_word_right(shift_pressed)
                    else:
                        self._move_cursor_right(shift_pressed)
                return True
            elif keyval == Gdk.KEY_Page_Up:
                self.scroll_by_lines(-self.visible_lines)
                return True
            elif keyval == Gdk.KEY_Page_Down:
                self.scroll_by_lines(self.visible_lines)
                return True
            elif keyval in [Gdk.KEY_Home, Gdk.KEY_End]:
                if shift_pressed and not self.has_selection:
                    self.selection_start_line = self.cursor_line
                    self.selection_start_col = self.cursor_col
                if keyval == Gdk.KEY_Home:
                    if ctrl_pressed:
                        if shift_pressed:
                            self.selection_end_line = 0
                            self.selection_end_col = 0
                            self.has_selection = True
                        self.scroll_to_top()
                        self.cursor_line = 0
                        self.cursor_col = 0
                    else:
                        if shift_pressed:
                            self.selection_end_col = 0
                            self.has_selection = True
                        self.cursor_col = 0
                elif keyval == Gdk.KEY_End:
                    if ctrl_pressed:
                        if shift_pressed:
                            self.selection_end_line = max(0, self.buffer.total_lines - 1)
                            self.selection_end_col = len(self.buffer.get_line(self.selection_end_line))
                            self.has_selection = True
                        self.scroll_to_bottom()
                        self.cursor_line = max(0, self.buffer.total_lines - 1)
                        self.cursor_col = len(self.buffer.get_line(self.cursor_line))
                    else:
                        if shift_pressed:
                            self.selection_end_col = len(self.buffer.get_line(self.cursor_line))
                            self.has_selection = True
                        self.cursor_col = len(self.buffer.get_line(self.cursor_line))
                if shift_pressed:
                    self.selection_end_line = self.cursor_line
                    self.selection_end_col = self.cursor_col
                    self.has_selection = True
                self._ensure_cursor_visible()
                self.queue_draw()
                self.grab_focus()
                return True
            elif keyval == Gdk.KEY_Return:
                # Commit any pending word
                self._commit_word_to_undo()
                
                if self.has_selection:
                    self._delete_selection()
                current_line_text = self.buffer.get_line(self.cursor_line)
                part1 = current_line_text[:self.cursor_col]
                part2 = current_line_text[self.cursor_col:]
                
                # Add undo for line split
                self.buffer.add_undo_action('insert_line', {
                    'line': self.cursor_line + 1,
                    'text': part2
                })
                
                self.buffer.set_line(self.cursor_line, part1)
                self.buffer.insert_line(self.cursor_line + 1, part2)
                self.cursor_line += 1
                self.cursor_col = 0
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return True
            elif keyval == Gdk.KEY_F2:
                self._start_editing()
                return True
            elif keyval in [Gdk.KEY_Delete, Gdk.KEY_BackSpace]:
                if self.has_selection:
                    self._delete_selection()
                    return True
                else:
                    if not self.editing:
                        self._start_editing()
                    # Let it fall through to editing mode handling
            # For any other printable key in non-editing mode, start editing
            elif not ctrl_pressed and not alt_pressed:
                unicode_char = Gdk.keyval_to_unicode(keyval)
                if unicode_char != 0 and unicode_char >= 32:
                    char = chr(unicode_char)
                    if char and char.isprintable():
                        print(f"Starting edit mode for printable char: '{char}'")
                        if self.has_selection:
                            self._delete_selection()
                        if not self.editing:
                            self._start_editing()
                        # Since not handled by IME, insert manually
                        if self.editing:
                            self.edit_text = self.edit_text[:self.edit_cursor_pos] + char + self.edit_text[self.edit_cursor_pos:]
                            self.edit_cursor_pos += len(char)
                            self.cursor_col = self.edit_cursor_pos
                            if self.buffer.word_wrap:
                                self._needs_wrap_recalc = True
                                self._wrapped_lines_cache.clear()
                            else:
                                new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                                if new_line_width > self.max_line_width:
                                    self.max_line_width = new_line_width
                            self._ensure_cursor_visible()
                            self.queue_draw()
                            self.emit('modified-changed', self.buffer.modified)
                        return True
        # In editing mode
        if self.editing:
            if keyval == Gdk.KEY_Return:
                # Commit any pending word
                self._commit_word_to_undo()
                
                if self.has_selection:
                    self._delete_selection()
                current_edit_cursor_pos = self.edit_cursor_pos
                self._finish_editing()
                current_full_text = self.buffer.get_line(self.cursor_line)
                part1 = current_full_text[:current_edit_cursor_pos]
                part2 = current_full_text[current_edit_cursor_pos:]
                
                # Add undo for line split
                self.buffer.add_undo_action('insert_line', {
                    'line': self.cursor_line + 1,
                    'text': part2
                })
                
                self.buffer.set_line(self.cursor_line, part1)
                self.buffer.insert_line(self.cursor_line + 1, part2)
                self.cursor_line += 1
                self.cursor_col = 0
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return True
            elif keyval == Gdk.KEY_Escape:
                self._cancel_editing()
                return True
            elif keyval in [Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End]:
                shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
                ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
                if shift_pressed:
                    if not self.has_selection:
                        self.selection_start_line = self.cursor_line
                        self.selection_start_col = self.edit_cursor_pos
                        self.selection_end_line = self.cursor_line
                        self.selection_end_col = self.edit_cursor_pos
                        self.has_selection = True
                else:
                    self.has_selection = False
                if keyval == Gdk.KEY_Left:
                    if ctrl_pressed:
                        line_text = self.edit_text
                        new_pos = self._find_word_boundary(line_text, self.edit_cursor_pos, -1)
                        self.edit_cursor_pos = new_pos
                    else:
                        self.edit_cursor_pos = max(0, self.edit_cursor_pos - 1)
                elif keyval == Gdk.KEY_Right:
                    if ctrl_pressed:
                        line_text = self.edit_text
                        new_pos = self._find_word_boundary(line_text, self.edit_cursor_pos, 1)
                        self.edit_cursor_pos = new_pos
                    else:
                        self.edit_cursor_pos = min(len(self.edit_text), self.edit_cursor_pos + 1)
                elif keyval == Gdk.KEY_Home:
                    self.edit_cursor_pos = 0
                elif keyval == Gdk.KEY_End:
                    self.edit_cursor_pos = len(self.edit_text)
                if shift_pressed:
                    self.selection_end_col = self.edit_cursor_pos
                self.cursor_col = self.edit_cursor_pos
                self._ensure_cursor_visible()
                self.queue_draw()
                self.grab_focus()
                return True
            elif keyval == Gdk.KEY_BackSpace:
                if self.has_selection:
                    self._delete_selection()
                    return True
                else:
                    if self.edit_cursor_pos > 0:
                        # Commit any pending insert word
                        if self.last_action_was_insert:
                            self._commit_word_to_undo()
                        
                        deleted_char = self.edit_text[self.edit_cursor_pos-1]
                        is_boundary = self._is_word_boundary(deleted_char)
                        
                        # Track deletion for undo
                        if not self.last_action_was_delete or is_boundary:
                            # Start new delete undo action or single-char boundary delete
                            if is_boundary and deleted_char.strip():
                                # Single punctuation
                                self.buffer.add_undo_action('delete', {
                                    'line': self.edit_line,
                                    'pos': self.edit_cursor_pos - 1,
                                    'end': self.edit_cursor_pos,
                                    'text': deleted_char
                                })
                            else:
                                # Start tracking word deletion
                                self.word_buffer = deleted_char
                                self.word_start_col = self.edit_cursor_pos - 1
                                self.word_start_line = self.edit_line
                                self.last_action_was_delete = True
                                self.last_action_was_insert = False
                        else:
                            # Continue tracking word deletion
                            self.word_buffer = deleted_char + self.word_buffer
                            self.word_start_col = self.edit_cursor_pos - 1
                        
                        # Perform deletion
                        self.edit_text = (self.edit_text[:self.edit_cursor_pos-1] + self.edit_text[self.edit_cursor_pos:])
                        self.edit_cursor_pos -= 1
                        self.cursor_col = self.edit_cursor_pos
                        self.buffer.set_line(self.edit_line, self.edit_text)
                        
                        # Commit delete word if we hit a boundary
                        if is_boundary and self.word_buffer and len(self.word_buffer) > 1:
                            self.buffer.add_undo_action('delete', {
                                'line': self.word_start_line,
                                'pos': self.word_start_col,
                                'end': self.word_start_col + len(self.word_buffer),
                                'text': self.word_buffer
                            })
                            self.word_buffer = ""
                            self.last_action_was_delete = False
                        
                        if not self.buffer.word_wrap:
                            new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                            if new_line_width > self.max_line_width:
                                self.max_line_width = new_line_width
                        else:
                            self._needs_wrap_recalc = True
                            self._wrapped_lines_cache.clear()
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('modified-changed', self.buffer.modified)
                    elif self.edit_cursor_pos == 0 and self.edit_line > 0:
                        # Commit any pending word
                        self._commit_word_to_undo()
                        
                        prev_line_text = self.buffer.get_line(self.edit_line - 1)
                        current_line_text = self.edit_text
                        
                        # Add undo for line merge
                        self.buffer.add_undo_action('delete_line', {
                            'line': self.edit_line,
                            'text': current_line_text
                        })
                        
                        merged_text = prev_line_text + current_line_text
                        self.buffer.set_line(self.edit_line - 1, merged_text)
                        self.buffer.delete_line(self.edit_line)
                        self.cursor_line = self.edit_line - 1
                        self.cursor_col = len(prev_line_text)
                        self.editing = False
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('buffer-changed')
                        self.emit('modified-changed', self.buffer.modified)
                return True
            elif keyval == Gdk.KEY_Delete:
                if self.has_selection:
                    self._delete_selection()
                    return True
                else:
                    if self.edit_cursor_pos < len(self.edit_text):
                        # Commit any pending insert word
                        if self.last_action_was_insert:
                            self._commit_word_to_undo()
                        
                        deleted_char = self.edit_text[self.edit_cursor_pos]
                        is_boundary = self._is_word_boundary(deleted_char)
                        
                        # Track deletion for undo
                        if not self.last_action_was_delete or is_boundary:
                            # Start new delete undo action or single-char boundary delete
                            if is_boundary and deleted_char.strip():
                                # Single punctuation
                                self.buffer.add_undo_action('delete', {
                                    'line': self.edit_line,
                                    'pos': self.edit_cursor_pos,
                                    'end': self.edit_cursor_pos + 1,
                                    'text': deleted_char
                                })
                            else:
                                # Start tracking word deletion
                                self.word_buffer = deleted_char
                                self.word_start_col = self.edit_cursor_pos
                                self.word_start_line = self.edit_line
                                self.last_action_was_delete = True
                                self.last_action_was_insert = False
                        else:
                            # Continue tracking word deletion (forward delete)
                            self.word_buffer = self.word_buffer + deleted_char
                        
                        # Perform deletion
                        self.edit_text = (self.edit_text[:self.edit_cursor_pos] + self.edit_text[self.edit_cursor_pos+1:])
                        self.buffer.set_line(self.edit_line, self.edit_text)
                        
                        # Commit delete word if we hit a boundary
                        if is_boundary and self.word_buffer and len(self.word_buffer) > 1:
                            self.buffer.add_undo_action('delete', {
                                'line': self.word_start_line,
                                'pos': self.word_start_col,
                                'end': self.word_start_col + len(self.word_buffer),
                                'text': self.word_buffer
                            })
                            self.word_buffer = ""
                            self.last_action_was_delete = False
                        
                        if not self.buffer.word_wrap:
                            new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                            if new_line_width > self.max_line_width:
                                self.max_line_width = new_line_width
                        else:
                            self._needs_wrap_recalc = True
                            self._wrapped_lines_cache.clear()
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('modified-changed', self.buffer.modified)
                    elif self.edit_cursor_pos == len(self.edit_text) and self.edit_line < self.buffer.total_lines - 1:
                        # Commit any pending word
                        self._commit_word_to_undo()
                        
                        next_line_text = self.buffer.get_line(self.edit_line + 1)
                        current_line_text = self.edit_text
                        
                        # Add undo for line merge
                        self.buffer.add_undo_action('delete_line', {
                            'line': self.edit_line + 1,
                            'text': next_line_text
                        })
                        
                        merged_text = current_line_text + next_line_text
                        self.buffer.set_line(self.edit_line, merged_text)
                        self.buffer.delete_line(self.edit_line + 1)
                        self.editing = False
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('buffer-changed')
                        self.emit('modified-changed', self.buffer.modified)
                return True
        # If we get here, the key wasn't handled
        print(f"Key not handled: {keyval}")
        return False
    def _on_key_released(self, controller, keyval, keycode, state):
        """Handle key release - may be needed for some IME implementations"""
        if self.im_context:
            event = controller.get_current_event()
            return self.im_context.filter_keypress(event)
        return False
    def do_size_allocate(self, width, height, baseline):
        Gtk.DrawingArea.do_size_allocate(self, width, height, baseline)
        if self.buffer.word_wrap:
            self._needs_wrap_recalc = True
            self.scroll_x = 0
        self._update_visible_lines()
        self._recalculate_max_line_width()
        self.queue_draw()
    def _on_realize(self, widget):
        self._calculate_font_metrics()
        self._update_visible_lines()
        self._recalculate_max_line_width()
        self.queue_draw()
    def _calculate_font_metrics(self):
        context = self.get_pango_context()
        metrics = context.get_metrics(self.font_desc)
        self.line_height = (metrics.get_ascent() + metrics.get_descent()) // Pango.SCALE + 2
        self.char_width = metrics.get_approximate_char_width() // Pango.SCALE
    def _update_visible_lines(self):
        height = self.get_height()
        if height > 0 and self.line_height > 0:
            self.visible_lines = int(height // self.line_height) + 2
    def _get_text_width(self, text):
        if not text:
            return 0
        layout = self.create_pango_layout("")
        layout.set_font_description(self.font_desc)
        layout.set_text(text)
        logical_rect = layout.get_extents()[1]
        return logical_rect.width / Pango.SCALE
    def _get_cursor_position_from_x(self, line_text, x_position):
        if x_position <= 0:
            return 0
        if not line_text:
            return 0
        layout = self.create_pango_layout("")
        layout.set_font_description(self.font_desc)
        layout.set_text(line_text)
        pango_x = int(x_position * Pango.SCALE)
        pango_y = 0
        hit_result = layout.xy_to_index(pango_x, pango_y)
        if hit_result[0]:
            byte_index = hit_result[1]
            trailing = hit_result[2]
            try:
                char_index = len(line_text.encode('utf-8')[:byte_index].decode('utf-8'))
                char_index += trailing
                return max(0, min(char_index, len(line_text)))
            except UnicodeDecodeError:
                char_index = min(byte_index, len(line_text))
                return max(0, char_index)
        else:
            estimated_index = int(x_position / self.char_width)
            if estimated_index > len(line_text):
                return len(line_text)
            return max(0, estimated_index)
    def _find_word_boundary(self, text, pos, direction):
        if direction == -1:
            while pos > 0 and text[pos - 1].isspace():
                pos -= 1
            if pos == 0:
                return 0
            if text[pos - 1].isalnum():
                while pos > 0 and text[pos - 1].isalnum():
                    pos -= 1
            else:
                while pos > 0 and not text[pos - 1].isalnum() and not text[pos - 1].isspace():
                    pos -= 1
            return pos
        else:
            length = len(text)
            while pos < length and text[pos].isspace():
                pos += 1
            if pos == length:
                return length
            if pos < length and text[pos].isalnum():
                while pos < length and text[pos].isalnum():
                    pos += 1
            else:
                while pos < length and not text[pos].isalnum() and not text[pos].isspace():
                    pos += 1
            return pos
    def _byte_to_char_index(self, text, byte_index):
        try:
            return len(text.encode('utf-8')[:byte_index].decode('utf-8'))
        except UnicodeDecodeError:
            return byte_index # fallback
    def _wrap_line(self, line_number, line_text):
        if self.wrap_width <= 0 or len(line_text) == 0:
            return [(line_number, 0, len(line_text))]
        layout = self.create_pango_layout("")
        layout.set_font_description(self.font_desc)
        layout.set_width(self.wrap_width * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(line_text)
        lines = layout.get_lines()
        wrapped_segments = []
        for pango_line in lines:
            start_byte = pango_line.start_index
            length_byte = pango_line.length
            start_char = self._byte_to_char_index(line_text, start_byte)
            end_char = self._byte_to_char_index(line_text, start_byte + length_byte)
            wrapped_segments.append((line_number, start_char, end_char))
        return wrapped_segments
    def _get_wrapped_lines(self, start_line, end_line):
        wrapped_result = []
        if not self.buffer.word_wrap:
            for i in range(start_line, min(end_line + 1, self.buffer.total_lines)):
                line_text = self.buffer.get_line(i)
                wrapped_result.append([(i, 0, len(line_text))])
        else:
            cache_key = (start_line, end_line, self.wrap_width)
            if cache_key in self._wrapped_lines_cache and not self._needs_wrap_recalc:
                return self._wrapped_lines_cache[cache_key]
            if self._needs_wrap_recalc:
                self._wrapped_lines_cache.clear()
                self._needs_wrap_recalc = False
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
            self.wrap_width = self.get_width() - line_num_width - 20
            if self.wrap_width <= 0:
                self.wrap_width = 1
            for i in range(start_line, min(end_line + 1, self.buffer.total_lines)):
                line_text = self.buffer.get_line(i)
                wrapped_segments = self._wrap_line(i, line_text)
                wrapped_result.append(wrapped_segments)
            self._wrapped_lines_cache[cache_key] = wrapped_result
        return wrapped_result
    def _get_total_visual_lines(self):
        if not self.buffer.word_wrap:
            return self.buffer.total_lines
        return self.buffer.total_lines
    def _get_visual_line_info_from_y(self, y):
        if self.buffer.total_lines == 0:
            return 0, 0, 0
        start_line = int(self.scroll_y // self.line_height)
        end_line = min(self.buffer.total_lines - 1, start_line + self.visible_lines + 50)
        wrapped_lines_data = self._get_wrapped_lines(start_line, end_line)
        y_offset = -(self.scroll_y % self.line_height)
        visual_line_counter = 0
        logical_line_index = 0
        while logical_line_index < len(wrapped_lines_data):
            wrapped_segments = wrapped_lines_data[logical_line_index]
            logical_line_num = start_line + logical_line_index
            for segment_index, (seg_line_num, seg_start_col, seg_end_col) in enumerate(wrapped_segments):
                y_pos_top = int(y_offset + visual_line_counter * self.line_height)
                y_pos_bottom = y_pos_top + self.line_height
                if y_pos_top <= y < y_pos_bottom:
                    return logical_line_num, seg_start_col, segment_index
                visual_line_counter += 1
            logical_line_index += 1
        if len(wrapped_lines_data) > 0:
            last_logical_index = len(wrapped_lines_data) - 1
            last_wrapped_segments = wrapped_lines_data[last_logical_index]
            if len(last_wrapped_segments) > 0:
                last_segment = last_wrapped_segments[-1]
                logical_line_num = start_line + last_logical_index
                return logical_line_num, last_segment[2], len(last_wrapped_segments) - 1
        return max(0, self.buffer.total_lines - 1), 0, 0
    def _get_position_from_coords(self, x, y):
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
        if x < line_num_width:
            return -1, -1
        logical_line_num, seg_start_col, segment_index = self._get_visual_line_info_from_y(y)
        line_text = self.buffer.get_line(logical_line_num)
        if self.editing and logical_line_num == self.edit_line:
            line_text = self.edit_text
            if self.in_preedit:
                line_text = self.edit_text[:self.edit_cursor_pos] + self.preedit_string + self.edit_text[self.edit_cursor_pos:]
        wrapped_segments = self._get_wrapped_lines(logical_line_num, logical_line_num)[0]
        col = 0
        if 0 <= segment_index < len(wrapped_segments):
            _, seg_start, seg_end = wrapped_segments[segment_index]
            segment_text = line_text[seg_start:seg_end]
            rel_x = x - line_num_width - 10 + self.scroll_x
            col_in_seg = self._get_cursor_position_from_x(segment_text, rel_x)
            col = seg_start + col_in_seg
        else:
            rel_x = x - line_num_width - 10 + self.scroll_x
            col = self._get_cursor_position_from_x(line_text, rel_x)
        if self.editing and logical_line_num == self.edit_line and self.in_preedit and col > self.edit_cursor_pos:
            if col <= self.edit_cursor_pos + len(self.preedit_string):
                col = self.edit_cursor_pos # adjust if click in preedit
            else:
                col -= len(self.preedit_string)
        return logical_line_num, col
    def _is_position_in_selection(self, line, col):
        bounds = self._get_selection_bounds()
        if not bounds:
            return False
        start_line, start_col, end_line, end_col = bounds
        if line < start_line or line > end_line:
            return False
        if line == start_line and col < start_col:
            return False
        if line == end_line and col > end_col:
            return False
        return True
    def _on_drag_begin_select(self, gesture, x, y):
        line, col = self._get_position_from_coords(x, y)
        if line == -1:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if self.has_selection and self._is_position_in_selection(line, col):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.has_selection = False
        self.selection_start_line = line
        self.selection_start_col = col
        self.selection_end_line = line
        self.selection_end_col = col
        self.has_selection = True
        self.cursor_line = line
        self.cursor_col = col
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.queue_draw()
    def _on_drag_update_select(self, gesture, offset_x, offset_y):
        success, start_x, start_y = gesture.get_start_point()
        if not success:
            return
        current_x = start_x + offset_x
        current_y = start_y + offset_y
        line, col = self._get_position_from_coords(current_x, current_y)
        if line == -1:
            return
        self.selection_end_line = line
        self.selection_end_col = col
        self.cursor_line = line
        self.cursor_col = col
        self._ensure_cursor_visible()
        self.queue_draw()
    def _on_drag_end_select(self, gesture, offset_x, offset_y):
        self._on_drag_update_select(gesture, offset_x, offset_y)
        if self.selection_start_line == self.selection_end_line and self.selection_start_col == self.selection_end_col:
            self.has_selection = False
            self.cursor_visible = True
        self.queue_draw()
    def _get_selection_bounds(self):
        if not self.has_selection:
            return None
        s_line, s_col = self.selection_start_line, self.selection_start_col
        e_line, e_col = self.selection_end_line, self.selection_end_col
        if s_line > e_line or (s_line == e_line and s_col > e_col):
            return (e_line, e_col, s_line, s_col)
        else:
            return (s_line, s_col, e_line, e_col)
    def _get_selected_text(self):
        bounds = self._get_selection_bounds()
        if not bounds:
            return ""
        start_line, start_col, end_line, end_col = bounds
        if start_line == end_line:
            if self.editing and start_line == self.edit_line:
                line_text = self.edit_text
            else:
                line_text = self.buffer.get_line(start_line)
            return line_text[start_col:end_col]
        else:
            lines = []
            if self.editing and start_line == self.edit_line:
                first_line_text = self.edit_text
            else:
                first_line_text = self.buffer.get_line(start_line)
            lines.append(first_line_text[start_col:])
            for line_num in range(start_line + 1, end_line):
                if self.editing and line_num == self.edit_line:
                    lines.append(self.edit_text)
                else:
                    lines.append(self.buffer.get_line(line_num))
            if self.editing and end_line == self.edit_line:
                last_line_text = self.edit_text
            else:
                last_line_text = self.buffer.get_line(end_line)
            lines.append(last_line_text[:end_col])
            return "\n".join(lines)
    def _delete_selection(self):
        bounds = self._get_selection_bounds()
        if not bounds:
            return False
        start_line, start_col, end_line, end_col = bounds
        if start_line == end_line:
            # Selection within a single line
            if self.editing and start_line == self.edit_line:
                line_text = self.edit_text
            else:
                line_text = self.buffer.get_line(start_line)
            new_text = line_text[:start_col] + line_text[end_col:]
            if self.editing and start_line == self.edit_line:
                self.edit_text = new_text
                self.edit_cursor_pos = start_col
                self.buffer.set_line(start_line, new_text)
            else:
                self.buffer.set_line(start_line, new_text)
            self.cursor_line = start_line
            self.cursor_col = start_col
        else:
            # Selection spans multiple lines
            if self.editing and start_line == self.edit_line:
                first_line_text = self.edit_text
            else:
                first_line_text = self.buffer.get_line(start_line)
            if self.editing and end_line == self.edit_line:
                last_line_text = self.edit_text
            else:
                last_line_text = self.buffer.get_line(end_line)
            before_text = first_line_text[:start_col]
            after_text = last_line_text[end_col:]
            merged_text = before_text + after_text
            # Set the merged content on the first line
            self.buffer.set_line(start_line, merged_text)
            # Delete lines from start_line + 1 to end_line
            del self.buffer.lines[start_line + 1 : end_line + 1]
            self.buffer.total_lines -= (end_line - start_line)
            self.cursor_line = start_line
            self.cursor_col = start_col
            # If we were editing on a deleted line, stop editing
            if self.editing and self.edit_line > start_line:
                self.editing = False
            elif self.editing and self.edit_line == start_line:
                self.edit_text = merged_text
                self.edit_cursor_pos = start_col
        self.has_selection = False
        self.selection_start_line = -1
        self.selection_start_col = -1
        self.selection_end_line = -1
        self.selection_end_col = -1
        self.cursor_visible = True
        self._ensure_cursor_visible()
        if self.buffer.word_wrap:
            self._wrapped_lines_cache.clear()
            self._needs_wrap_recalc = True
        self.emit('buffer-changed')
        self.emit('modified-changed', self.buffer.modified)
        self.queue_draw()
        return True
    def _copy_to_clipboard(self):
        if not self.has_selection:
            return
        def build_text():
            text = self._get_selected_text()
            GLib.idle_add(self._set_clipboard_text, text, False)
        thread = threading.Thread(target=build_text)
        thread.daemon = True
        thread.start()
    def _cut_to_clipboard(self):
        if not self.has_selection:
            return
        def build_text():
            text = self._get_selected_text()
            GLib.idle_add(self._set_clipboard_text, text, True)
        thread = threading.Thread(target=build_text)
        thread.daemon = True
        thread.start()
    def _set_clipboard_text(self, text, is_cut):
        if text:
            self.clipboard.set(text)
            if is_cut:
                self._delete_selection()
                self.has_selection = False
                self.cursor_visible = True
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
    def _paste_from_clipboard(self):
        if self.is_pasting:
            return
        self.is_pasting = True
        def on_clipboard_contents(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
                if text:
                    self._insert_text_at_cursor(text)
            except Exception as e:
                print(f"Error pasting: {e}")
            finally:
                self.is_pasting = False
        self.clipboard.read_text_async(None, on_clipboard_contents)
    def _async_insert_text(self, text):
        has_selection = self.has_selection
        editing = self.editing
        cursor_line = self.cursor_line
        cursor_col = self.cursor_col
        edit_line = self.edit_line
        edit_cursor_pos = self.edit_cursor_pos
        buffer_total_lines = self.buffer.total_lines
        try:
            if editing:
                if edit_line < buffer_total_lines:
                    current_line_text = self.buffer.get_line(edit_line)
                    new_text = current_line_text[:edit_cursor_pos] + text + current_line_text[edit_cursor_pos:]
                    lines_added = text.count('\n')
                    final_cursor_line = edit_line + lines_added
                    if lines_added > 0:
                        final_cursor_col = len(text.split('\n')[-1])
                    else:
                        final_cursor_col = edit_cursor_pos + len(text)
                    new_lines = new_text.split('\n')
                    GLib.idle_add(self._finish_async_insert_edit_mode, edit_line, new_lines, final_cursor_line, final_cursor_col)
                else:
                    GLib.idle_add(lambda: print("Error: Edit line out of bounds during async paste"))
            else:
                if cursor_line < buffer_total_lines:
                    current_line_text = self.buffer.get_line(cursor_line)
                    before_text = current_line_text[:cursor_col]
                    after_text = current_line_text[cursor_col:]
                    paste_lines = text.split('\n')
                    first_line_modified = before_text + (paste_lines[0] if paste_lines else "")
                    lines_to_insert = []
                    final_cursor_line = cursor_line
                    final_cursor_col = cursor_col
                    if len(paste_lines) == 1:
                        final_text = first_line_modified + after_text
                        lines_to_insert = [final_text]
                        final_cursor_line = cursor_line
                        final_cursor_col = len(before_text) + len(paste_lines[0])
                    else:
                        lines_to_insert.append(first_line_modified)
                        if len(paste_lines) > 2:
                            lines_to_insert.extend(paste_lines[1:-1])
                        last_paste_content = paste_lines[-1]
                        last_line_modified = last_paste_content + after_text
                        lines_to_insert.append(last_line_modified)
                        final_cursor_line = cursor_line + len(paste_lines) - 1
                        final_cursor_col = len(last_paste_content)
                    GLib.idle_add(self._finish_async_insert_normal_mode, cursor_line, lines_to_insert, after_text, final_cursor_line, final_cursor_col)
                else:
                    GLib.idle_add(lambda: print("Error: Cursor line out of bounds during async paste"))
        except Exception as e:
            GLib.idle_add(lambda: print(f"Error during async paste processing: {e}"))
        GLib.idle_add(self.queue_draw)
    def _finish_async_insert_edit_mode(self, edit_line, new_lines, final_cursor_line, final_cursor_col):
        if not (0 <= edit_line < self.buffer.total_lines):
            print("Error: Invalid edit line for async paste finish")
            return
        try:
            self.buffer.modified = True
            self.buffer.lines[edit_line] = new_lines[0]
            additional_lines = new_lines[1:]
            if len(new_lines) > 1:
                self.editing = False
                self.edit_line = 0
                self.edit_text = ""
                self.edit_cursor_pos = 0
            else:
                self.edit_text = self.buffer.get_line(self.cursor_line)
                self.edit_cursor_pos = self.cursor_col
            self.cursor_line = final_cursor_line
            self.cursor_col = final_cursor_col
            if self.buffer.word_wrap:
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
            self._ensure_cursor_visible()
            self.queue_draw()
            self.emit('buffer-changed')
            self.emit('modified-changed', self.buffer.modified)
            chunk_size = 10000
            insert_pos = edit_line + 1
            added = 0
            def insert_next(start_idx=0):
                nonlocal insert_pos, added
                if start_idx >= len(additional_lines):
                    self.buffer.total_lines += added
                    self.cursor_line = final_cursor_line
                    self.cursor_col = final_cursor_col
                    if self.buffer.word_wrap:
                        self._needs_wrap_recalc = True
                        self._wrapped_lines_cache.clear()
                    self._ensure_cursor_visible()
                    self.queue_draw()
                    self.emit('buffer-changed')
                    self.emit('modified-changed', self.buffer.modified)
                    return False
                end_idx = min(start_idx + chunk_size, len(additional_lines))
                chunk = additional_lines[start_idx:end_idx]
                self.buffer.lines[insert_pos:insert_pos] = chunk
                chunk_len = len(chunk)
                added += chunk_len
                insert_pos += chunk_len
                GLib.idle_add(lambda: insert_next(end_idx))
                return False
            GLib.idle_add(insert_next)
        except Exception as e:
            print(f"Error finishing async edit paste: {e}")
            self.queue_draw()
    def _finish_async_insert_normal_mode(self, cursor_line, lines_to_insert, after_text, final_cursor_line, final_cursor_col):
        if not (0 <= cursor_line < self.buffer.total_lines):
            print("Error: Invalid cursor line for async paste finish")
            return
        try:
            self.buffer.modified = True
            self.buffer.lines[cursor_line] = lines_to_insert[0]
            additional_lines = lines_to_insert[1:]
            if not additional_lines:
                self.cursor_line = final_cursor_line
                self.cursor_col = final_cursor_col
                if self.buffer.word_wrap:
                    self._needs_wrap_recalc = True
                    self._wrapped_lines_cache.clear()
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return
            chunk_size = 10000
            insert_pos = cursor_line + 1
            added = 0
            def insert_next(start_idx=0):
                nonlocal insert_pos, added
                if start_idx >= len(additional_lines):
                    self.buffer.total_lines += added
                    self.cursor_line = final_cursor_line
                    self.cursor_col = final_cursor_col
                    if self.buffer.word_wrap:
                        self._needs_wrap_recalc = True
                        self._wrapped_lines_cache.clear()
                    self._ensure_cursor_visible()
                    self.queue_draw()
                    self.emit('buffer-changed')
                    self.emit('modified-changed', self.buffer.modified)
                    return False
                end_idx = min(start_idx + chunk_size, len(additional_lines))
                chunk = additional_lines[start_idx:end_idx]
                self.buffer.lines[insert_pos:insert_pos] = chunk
                chunk_len = len(chunk)
                added += chunk_len
                insert_pos += chunk_len
                GLib.idle_add(lambda: insert_next(end_idx))
                return False
            GLib.idle_add(insert_next)
        except Exception as e:
            print(f"Error finishing async normal paste: {e}")
            self.queue_draw()
    def _insert_text_at_cursor(self, text):
        """Insert text at the current cursor position"""
        if self.has_selection:
            # Delete selection first
            self._delete_selection()
        # Ensure we're in a valid state after potential selection deletion
        if self.cursor_line >= self.buffer.total_lines:
            self.cursor_line = max(0, self.buffer.total_lines - 1)
        # Start editing if not already editing
        if not self.editing:
            self._start_editing()
        # Handle the text insertion
        if '\n' in text:
            # Multi-line paste - handle in background thread
            thread = threading.Thread(target=self._async_insert_text, args=(text,))
            thread.daemon = True
            thread.start()
        else:
            # Single line text - handle immediately
            if self.editing:
                self.edit_text = (self.edit_text[:self.edit_cursor_pos] + text + self.edit_text[self.edit_cursor_pos:])
                self.edit_cursor_pos += len(text)
                self.cursor_col = self.edit_cursor_pos
                self.buffer.set_line(self.edit_line, self.edit_text)
                if self.buffer.word_wrap:
                    self._needs_wrap_recalc = True
                    self._wrapped_lines_cache.clear()
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('modified-changed', self.buffer.modified)
    def _on_drag_prepare(self, source, x, y):
        if self.has_selection:
            selected_text = self._get_selected_text()
            if selected_text:
                return Gdk.ContentProvider.new_for_value(selected_text)
        return None
    def _on_drag_begin(self, source, drag):
        self.in_drag = True
        pass
    def _on_drag_end(self, source, drag, delete_data):
        self.in_drag = False
        if delete_data and self.has_selection:
            self._delete_selection()
        self.queue_draw()
    def _on_drop_accept(self, target, drop):
        formats = drop.get_formats()
        return formats.contain_gtype(str)
    def _on_drop_motion(self, target, x, y):
        return Gdk.DragAction.COPY # Return single preferred action
    def _on_drop_drop(self, target, value, x, y):
        if isinstance(value, str):
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
            if x > line_num_width:
                logical_line_num, seg_start_col, segment_index = self._get_visual_line_info_from_y(y)
                if logical_line_num is not None:
                    target_line = logical_line_num
                    line_text = self.buffer.get_line(target_line)
                    if self.editing and target_line == self.edit_line:
                        line_text = self.edit_text
                    line_wrapped_segments = self._get_wrapped_lines(target_line, target_line)[0]
                    target_col = 0
                    if 0 <= segment_index < len(line_wrapped_segments):
                        seg_line_num, seg_start_col_actual, seg_end_col_actual = line_wrapped_segments[segment_index]
                        segment_text = line_text[seg_start_col_actual:seg_end_col_actual]
                        text_x_position_in_segment = x - line_num_width - 10 + self.scroll_x
                        col_in_segment = self._get_cursor_position_from_x(segment_text, text_x_position_in_segment)
                        target_col = seg_start_col_actual + col_in_segment
                    else:
                        text_x_position = x - line_num_width - 10 + self.scroll_x
                        target_col = self._get_cursor_position_from_x(line_text, text_x_position)
                    old_cursor_line, old_cursor_col = self.cursor_line, self.cursor_col
                    self.cursor_line, self.cursor_col = target_line, target_col
                    if old_cursor_line != self.cursor_line or old_cursor_col != self.cursor_col:
                        self.has_selection = False
                        self.cursor_visible = True
                    self._insert_text_at_cursor(value)
                    return True
        return False
    def _on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return
        font_options = cairo.FontOptions()
        font_options.set_antialias(cairo.ANTIALIAS_SUBPIXEL)
        font_options.set_hint_style(cairo.HINT_STYLE_SLIGHT)
        cr.set_font_options(font_options)
        is_dark = Adw.StyleManager.get_default().get_dark()
        theme_key = "dark" if is_dark else "light"
        if int(height // self.line_height) + 2 != self.visible_lines:
            self.visible_lines = int(height // self.line_height) + 2
        if self.buffer.word_wrap:
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
            new_wrap_width = width - line_num_width - 20
            wrap_width_changed = new_wrap_width > 0 and new_wrap_width != self.wrap_width
            if wrap_width_changed:
                self.wrap_width = new_wrap_width
                self._wrapped_lines_cache.clear()
                self._needs_wrap_recalc = False
        if self._needs_wrap_recalc:
            self._wrapped_lines_cache.clear()
            self._needs_wrap_recalc = False
        start_line = int(self.scroll_y // self.line_height)
        end_line = start_line + self.visible_lines + 10
        wrapped_lines_data = self._get_wrapped_lines(start_line, end_line)
        cr.set_source_rgb(1, 1, 1) if not is_dark else cr.set_source_rgb(0.1, 0.1, 0.1)
        cr.paint()
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
        cr.set_source_rgb(0.95, 0.95, 0.95) if not is_dark else cr.set_source_rgb(0.15, 0.15, 0.15)
        cr.rectangle(0, 0, line_num_width, height)
        cr.fill()
        y_offset = -(self.scroll_y % self.line_height)
        visual_line_counter = 0
        line_index = 0
        while line_index < len(wrapped_lines_data) and visual_line_counter < self.visible_lines + 20:
            wrapped_segments = wrapped_lines_data[line_index]
            logical_line_num = start_line + line_index
            if wrapped_segments:
                y_pos = int(y_offset + visual_line_counter * self.line_height)
                if y_pos > height:
                    break
                cr.set_source_rgb(0.5, 0.5, 0.5) if not is_dark else cr.set_source_rgb(0.7, 0.7, 0.7)
                line_num_layout = self.create_pango_layout("")
                line_num_layout.set_font_description(self.font_desc)
                line_num_layout.set_text(str(logical_line_num + 1))
                cr.move_to(10, y_pos)
                PangoCairo.show_layout(cr, line_num_layout)
            visual_line_counter += len(wrapped_segments)
            line_index += 1
        separator_x = line_num_width
        cr.set_source_rgb(0.8, 0.8, 0.8) if not is_dark else cr.set_source_rgb(0.3, 0.3, 0.3)
        cr.set_line_width(1)
        cr.move_to(separator_x, 0)
        cr.line_to(separator_x, height)
        cr.stroke()
        cr.save()
        cr.rectangle(line_num_width, 0, width - line_num_width, height)
        cr.clip()
        visual_line_counter = 0
        line_index = 0
        while line_index < len(wrapped_lines_data) and visual_line_counter < self.visible_lines + 20:
            wrapped_segments = wrapped_lines_data[line_index]
            logical_line_num = start_line + line_index
            line_text_full = self.buffer.get_line(logical_line_num)
            display_text_full = line_text_full
            preedit_start_col_adjust = 0
            if self.editing and logical_line_num == self.edit_line:
                display_text_full = self.edit_text
                if self.in_preedit:
                    display_text_full = self.edit_text[:self.edit_cursor_pos] + self.preedit_string + self.edit_text[self.edit_cursor_pos:]
                    preedit_start_col_adjust = self.edit_cursor_pos
            for segment_index, (seg_line_num, seg_start_col, seg_end_col) in enumerate(wrapped_segments):
                if visual_line_counter > self.visible_lines + 20:
                    break
                y_pos = int(y_offset + visual_line_counter * self.line_height)
                if y_pos > height:
                    break
                segment_text = display_text_full[seg_start_col:seg_end_col]
                if logical_line_num == self.cursor_line:
                    cursor_in_segment = False
                    if self.editing and logical_line_num == self.edit_line:
                        cursor_in_segment = seg_start_col <= self.edit_cursor_pos <= seg_end_col
                    else:
                        cursor_in_segment = seg_start_col <= self.cursor_col <= seg_end_col
                    if cursor_in_segment:
                        cr.set_source_rgb(0.95, 0.95, 1.0) if not is_dark else cr.set_source_rgb(0.2, 0.2, 0.3)
                        highlight_x_start = line_num_width
                        cr.rectangle(highlight_x_start, y_pos - 2, width - line_num_width, self.line_height)
                        cr.fill()
                if self.highlight_matches and self.highlight_pattern:
                    for match in self.highlight_pattern.finditer(segment_text):
                        mstart, mend = match.span()
                        highlight_start_x = line_num_width + 10 - self.scroll_x + self._get_text_width(segment_text[:mstart])
                        highlight_width = self._get_text_width(segment_text[mstart:mend])
                        cr.save()
                        cr.set_source_rgba(1.0, 1.0, 0.2, 0.4)  # yellow translucent highlight
                        cr.rectangle(highlight_start_x, y_pos, highlight_width, self.line_height)
                        cr.fill()
                        cr.restore()
        
                # Render syntax-highlighted text
                if self.buffer.syntax_highlight and logical_line_num < self.buffer.total_lines and not (self.editing and logical_line_num == self.edit_line and self.in_preedit):
                    tokens = self.buffer.get_line_tokens(logical_line_num)
                    if tokens:
                        x_offset = line_num_width + 10 - self.scroll_x
                        for token_text, token_type in tokens:
                            if not token_text:
                                continue
                            # Extract the part that belongs to this visual segment
                            global_start = display_text_full.find(token_text)
                            global_end = global_start + len(token_text)
                            seg_start_abs = seg_start_col
                            seg_end_abs = seg_end_col
                            # Check if token overlaps with current visual segment
                            max_start = max(global_start, seg_start_abs)
                            min_end = min(global_end, seg_end_abs)
                            if max_start >= min_end:
                                continue # No overlap
                            display_text = token_text[max_start - global_start : min_end - global_start]
                            # Create layout for this token
                            layout = self.create_pango_layout("")
                            layout.set_font_description(self.font_desc)
                            layout.set_text(display_text)
                            # Set color based on theme
                            color_def = self.SYNTAX_COLORS.get(token_type, self.SYNTAX_COLORS["identifier"])
                            r, g, b = color_def[theme_key]
                            cr.set_source_rgb(r, g, b)
                            # Draw
                            cr.move_to(x_offset, y_pos)
                            PangoCairo.show_layout(cr, layout)
                            # Advance x
                            width = self._get_text_width(display_text)
                            x_offset += width
                    else:
                        # Fallback to normal rendering
                        layout = self.create_pango_layout("")
                        layout.set_font_description(self.font_desc)
                        layout.set_text(segment_text)
                        cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                        cr.move_to(line_num_width + 10 - self.scroll_x, y_pos)
                        PangoCairo.show_layout(cr, layout)
                else:
                    # No syntax highlighting
                    layout = self.create_pango_layout("")
                    layout.set_font_description(self.font_desc)
                    layout.set_text(segment_text)
                    cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                    cr.move_to(line_num_width + 10 - self.scroll_x, y_pos)
                    PangoCairo.show_layout(cr, layout)
                # Draw preedit underline if applicable
                if self.editing and logical_line_num == self.edit_line and self.in_preedit:
                    pre_start_in_seg = max(0, preedit_start_col_adjust - seg_start_col)
                    pre_end_in_seg = min(len(segment_text), preedit_start_col_adjust + len(self.preedit_string) - seg_start_col)
                    if pre_start_in_seg < pre_end_in_seg:
                        pre_text_before = segment_text[:pre_start_in_seg]
                        pre_width_before = self._get_text_width(pre_text_before)
                        pre_width = self._get_text_width(segment_text[pre_start_in_seg:pre_end_in_seg])
                        x_start = line_num_width + 10 - self.scroll_x + pre_width_before
                        cr.set_source_rgb(0.5, 0.5, 0.5) if not is_dark else cr.set_source_rgb(0.7, 0.7, 0.7)
                        cr.set_line_width(1)
                        cr.move_to(x_start, y_pos + self.line_height - 1)
                        cr.line_to(x_start + pre_width, y_pos + self.line_height - 1)
                        cr.stroke()
                if self.has_selection:
                    bounds = self._get_selection_bounds()
                    if bounds:
                        sel_start_line, sel_start_col, sel_end_line, sel_end_col = bounds
                        segment_selected = False
                        selection_text = ""
                        local_sel_start = 0
                        local_sel_end = len(segment_text)
                        if sel_start_line == sel_end_line == logical_line_num:
                            if seg_start_col < sel_end_col and seg_end_col > sel_start_col:
                                segment_selected = True
                                local_sel_start = max(sel_start_col, seg_start_col) - seg_start_col
                                local_sel_end = min(sel_end_col, seg_end_col) - seg_start_col
                                selection_text = segment_text[local_sel_start:local_sel_end]
                        elif sel_start_line == logical_line_num and sel_end_line > logical_line_num:
                            if seg_end_col > sel_start_col:
                                segment_selected = True
                                local_sel_start = max(sel_start_col, seg_start_col) - seg_start_col
                                local_sel_end = len(segment_text)
                                selection_text = segment_text[local_sel_start:]
                        elif sel_start_line < logical_line_num < sel_end_line:
                            segment_selected = True
                            local_sel_start = 0
                            local_sel_end = len(segment_text)
                            selection_text = segment_text
                        elif sel_end_line == logical_line_num and sel_start_line < logical_line_num:
                            if seg_start_col < sel_end_col:
                                segment_selected = True
                                local_sel_start = 0
                                local_sel_end = min(sel_end_col, seg_end_col) - seg_start_col
                                selection_text = segment_text[:local_sel_end]
                        if segment_selected and selection_text:
                            pre_sel_text = segment_text[:local_sel_start]
                            pre_width = self._get_text_width(pre_sel_text)
                            sel_width = self._get_text_width(selection_text)
                            sel_x_start = line_num_width + 10 - self.scroll_x + pre_width
                            cr.set_source_rgb(0.5, 0.7, 1.0) if not is_dark else cr.set_source_rgb(0.3, 0.5, 0.8)
                            cr.rectangle(sel_x_start, y_pos, sel_width, self.line_height)
                            cr.fill()
                            cr.set_source_rgb(1, 1, 1) if not is_dark else cr.set_source_rgb(0, 0, 0)
                            sel_layout = self.create_pango_layout("")
                            sel_layout.set_font_description(self.font_desc)
                            sel_layout.set_text(selection_text)
                            cr.move_to(sel_x_start, y_pos)
                            PangoCairo.show_layout(cr, sel_layout)
                cursor_on_segment = False
                cursor_x = line_num_width + 10 - self.scroll_x
                if self.editing and logical_line_num == self.edit_line:
                    effective_cursor_pos = self.edit_cursor_pos
                    if self.in_preedit:
                        effective_cursor_pos += self.preedit_cursor_pos
                    if seg_start_col <= effective_cursor_pos <= seg_end_col:
                        cursor_on_segment = True
                        cursor_offset = effective_cursor_pos - seg_start_col
                        cursor_text = display_text_full[seg_start_col:seg_start_col + cursor_offset]
                        cursor_x += self._get_text_width(cursor_text)
                else:
                    if logical_line_num == self.cursor_line and seg_start_col <= self.cursor_col <= seg_end_col:
                        cursor_on_segment = True
                        cursor_text = display_text_full[seg_start_col:self.cursor_col]
                        cursor_x += self._get_text_width(cursor_text)
                if cursor_on_segment and self.cursor_visible and cursor_x >= line_num_width and cursor_x <= width and not self.has_selection:
                    cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                    cr.set_line_width(2)
                    cr.move_to(cursor_x, y_pos)
                    cr.line_to(cursor_x, y_pos + self.line_height - 2)
                    cr.stroke()
                visual_line_counter += 1
            # Special handling for empty lines to ensure cursor is drawn
            if len(wrapped_segments) == 0 and logical_line_num == self.cursor_line and self.cursor_col == 0 and self.cursor_visible and not self.has_selection:
                y_pos = int(y_offset + visual_line_counter * self.line_height)
                if 0 <= y_pos < height:
                    cursor_x = line_num_width + 10 - self.scroll_x
                    if cursor_x >= line_num_width and cursor_x <= width:
                        cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                        cr.set_line_width(2)
                        cr.move_to(cursor_x, y_pos)
                        cr.line_to(cursor_x, y_pos + self.line_height - 2)
                        cr.stroke()
                visual_line_counter += 1
            line_index += 1
    def _on_v_scroll(self, controller, dx, dy):
        scroll_amount = dy * self.line_height * 3
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll = max(0, total_visual_height - self.get_height())
        old_scroll_y = self.scroll_y
        self.scroll_y = max(0, min(max_scroll, self.scroll_y + scroll_amount))
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.queue_draw()
        return True
    def _on_h_scroll(self, controller, dx, dy):
        if self.buffer.word_wrap:
            return False
        scroll_amount = dx * self.char_width * 10
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
        available_width = self.get_width() - line_num_width
        max_scroll_x = max(0, self.max_line_width - available_width)
        old_scroll_x = self.scroll_x
        self.scroll_x = max(0, min(max_scroll_x, self.scroll_x + scroll_amount))
        if old_scroll_x != self.scroll_x:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.queue_draw()
        return True
    def _on_click(self, gesture, n_press, x, y):
        # Grab focus and show cursor when clicked
        self.grab_focus()
        self.cursor_visible = True
        
        self.drag_start_x, self.drag_start_y = x, y
        self.in_drag = False
        current_time = time.time()
        time_threshold = 0.3
        distance_threshold = 5
        is_same_click = (
            abs(x - self.last_click_x) < distance_threshold and
            abs(y - self.last_click_y) < distance_threshold
        )
        if current_time - self.last_click_time < time_threshold and is_same_click:
            self.click_count += 1
        else:
            self.click_count = 1
        self.last_click_time = current_time
        self.last_click_x = x
        self.last_click_y = y
    def _on_click_release(self, gesture, n_press, x, y):
        drag_threshold = 5
        if abs(x - self.drag_start_x) > drag_threshold or abs(y - self.drag_start_y) > drag_threshold:
            return
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
        if x > line_num_width:
            logical_line_num, seg_start_col, segment_index = self._get_visual_line_info_from_y(y)
            if logical_line_num is not None:
                self.cursor_line = logical_line_num
                line_text = self.buffer.get_line(self.cursor_line)
                if self.editing and self.cursor_line == self.edit_line:
                    line_text = self.edit_text
                line_wrapped_segments = self._get_wrapped_lines(self.cursor_line, self.cursor_line)[0]
                if 0 <= segment_index < len(line_wrapped_segments):
                    seg_line_num, seg_start_col_actual, seg_end_col_actual = line_wrapped_segments[segment_index]
                    segment_text = line_text[seg_start_col_actual:seg_end_col_actual]
                    text_x_position_in_segment = x - line_num_width - 10 + self.scroll_x
                    col_in_segment = self._get_cursor_position_from_x(segment_text, text_x_position_in_segment)
                    self.cursor_col = seg_start_col_actual + col_in_segment
                else:
                    text_x_position = x - line_num_width - 10 + self.scroll_x
                    self.cursor_col = self._get_cursor_position_from_x(line_text, text_x_position)
                if self.click_count == 1:
                    if not (Gdk.ModifierType.SHIFT_MASK & gesture.get_current_event_state()):
                        self.has_selection = False
                        self.cursor_visible = True
                        self.selection_start_line = -1
                        self.selection_start_col = -1
                        self.selection_end_line = -1
                        self.selection_end_col = -1
                    else:
                        if not self.has_selection:
                            self.selection_start_line = self.cursor_line
                            self.selection_start_col = self.cursor_col
                            self.selection_end_line = self.cursor_line
                            self.selection_end_col = self.cursor_col
                            self.has_selection = True
                    if self.editing:
                        if self.edit_line != self.cursor_line:
                            self._finish_editing()
                        else:
                            self.edit_cursor_pos = self.cursor_col
                elif self.click_count == 2:
                    if self.cursor_col < len(line_text) and line_text[self.cursor_col].isspace():
                        start_pos = self.cursor_col
                        while start_pos > 0 and line_text[start_pos - 1].isspace():
                            start_pos -= 1
                        end_pos = self.cursor_col + 1
                        while end_pos < len(line_text) and line_text[end_pos].isspace():
                            end_pos += 1
                    else:
                        start_pos = self._find_word_boundary(line_text, self.cursor_col, -1)
                        end_pos = self._find_word_boundary(line_text, self.cursor_col, 1)
                    self.selection_start_line = self.cursor_line
                    self.selection_start_col = start_pos
                    self.selection_end_line = self.cursor_line
                    self.selection_end_col = end_pos
                    self.has_selection = True
                    self.cursor_col = end_pos
                    if not self.editing:
                        self._start_editing()
                    self.edit_cursor_pos = self.cursor_col
                elif self.click_count >= 3:
                    self.selection_start_line = self.cursor_line
                    self.selection_start_col = 0
                    self.selection_end_line = self.cursor_line
                    self.selection_end_col = len(line_text)
                    self.has_selection = True
                    self.cursor_col = len(line_text)
                    self.click_count = 0
                    if not self.editing:
                        self._start_editing()
                    self.edit_cursor_pos = self.cursor_col
                self.queue_draw()
    def _start_cursor_blink(self):
        def blink():
            self.cursor_visible = not self.cursor_visible
            self.queue_draw()
            return True
        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)
        self.cursor_blink_timeout = GLib.timeout_add(500, blink)
    def _move_cursor_up(self, extend_selection=False):
        if self.cursor_line > 0:
            if extend_selection and not self.has_selection:
                self.selection_start_line = self.cursor_line
                self.selection_start_col = self.cursor_col
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self.cursor_line -= 1
            line_text = self.buffer.get_line(self.cursor_line)
            self.cursor_col = min(self.cursor_col, len(line_text))
            if extend_selection:
                self.selection_end_line = self.cursor_line
                self.selection_end_col = self.cursor_col
                self.has_selection = True
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self._ensure_cursor_visible()
            self.queue_draw()
    def _move_cursor_down(self, extend_selection=False):
        if self.cursor_line < self.buffer.total_lines - 1:
            if extend_selection and not self.has_selection:
                self.selection_start_line = self.cursor_line
                self.selection_start_col = self.cursor_col
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self.cursor_line += 1
            line_text = self.buffer.get_line(self.cursor_line)
            self.cursor_col = min(self.cursor_col, len(line_text))
            if extend_selection:
                self.selection_end_line = self.cursor_line
                self.selection_end_col = self.cursor_col
                self.has_selection = True
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self._ensure_cursor_visible()
            self.queue_draw()
    def _move_cursor_left(self, extend_selection=False):
        if extend_selection and not self.has_selection:
            self.selection_start_line = self.cursor_line
            self.selection_start_col = self.cursor_col
        elif not extend_selection:
            self.has_selection = False
            self.cursor_visible = True
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_line > 0:
            self.cursor_line -= 1
            self.cursor_col = len(self.buffer.get_line(self.cursor_line))
        self._ensure_cursor_visible()
        if extend_selection:
            self.selection_end_line = self.cursor_line
            self.selection_end_col = self.cursor_col
            self.has_selection = True
        elif not extend_selection:
            self.has_selection = False
            self.cursor_visible = True
        self.queue_draw()
    def _move_cursor_right(self, extend_selection=False):
        line_text = self.buffer.get_line(self.cursor_line)
        if extend_selection and not self.has_selection:
            self.selection_start_line = self.cursor_line
            self.selection_start_col = self.cursor_col
        elif not extend_selection:
            self.has_selection = False
            self.cursor_visible = True
        if self.cursor_col < len(line_text):
            self.cursor_col += 1
        elif self.cursor_line < self.buffer.total_lines - 1:
            self.cursor_line += 1
            self.cursor_col = 0
        self._ensure_cursor_visible()
        if extend_selection:
            self.selection_end_line = self.cursor_line
            self.selection_end_col = self.cursor_col
            self.has_selection = True
        elif not extend_selection:
            self.has_selection = False
            self.cursor_visible = True
        self.queue_draw()
    def _move_cursor_word_left(self, extend_selection=False):
        line_text = self.buffer.get_line(self.cursor_line)
        new_col = self._find_word_boundary(line_text, self.cursor_col, -1)
        if new_col != self.cursor_col:
            if extend_selection and not self.has_selection:
                self.selection_start_line = self.cursor_line
                self.selection_start_col = self.cursor_col
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self.cursor_col = new_col
            self._ensure_cursor_visible()
            if extend_selection:
                self.selection_end_line = self.cursor_line
                self.selection_end_col = self.cursor_col
                self.has_selection = True
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self.queue_draw()
    def _move_cursor_word_right(self, extend_selection=False):
        line_text = self.buffer.get_line(self.cursor_line)
        new_col = self._find_word_boundary(line_text, self.cursor_col, 1)
        if new_col != self.cursor_col:
            if extend_selection and not self.has_selection:
                self.selection_start_line = self.cursor_line
                self.selection_start_col = self.cursor_col
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self.cursor_col = new_col
            self._ensure_cursor_visible()
            if extend_selection:
                self.selection_end_line = self.cursor_line
                self.selection_end_col = self.cursor_col
                self.has_selection = True
            elif not extend_selection:
                self.has_selection = False
                self.cursor_visible = True
            self.queue_draw()
    def _ensure_cursor_visible(self):
        cursor_y = self.cursor_line * self.line_height
        viewport_top = self.scroll_y
        viewport_bottom = self.scroll_y + self.get_height()
        old_scroll_y = self.scroll_y
        if cursor_y < viewport_top:
            self.scroll_y = cursor_y
        elif cursor_y + self.line_height > viewport_bottom:
            self.scroll_y = cursor_y + self.line_height - self.get_height()
        old_scroll_x = self.scroll_x
        if not self.buffer.word_wrap:
            line_text = self.buffer.get_line(self.cursor_line)
            col = self.cursor_col
            if self.editing and self.cursor_line == self.edit_line:
                line_text = self.edit_text
                col = self.edit_cursor_pos
                if self.in_preedit:
                    col += self.preedit_cursor_pos
                    line_text = self.edit_text[:self.edit_cursor_pos] + self.preedit_string + self.edit_text[self.edit_cursor_pos:]
            cursor_x_pos_in_line = self._get_text_width(line_text[:col])
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
            available_width = self.get_width() - line_num_width
            if cursor_x_pos_in_line < self.scroll_x:
                self.scroll_x = max(0, cursor_x_pos_in_line - 10)
            elif cursor_x_pos_in_line > (self.scroll_x + available_width - self.char_width):
                self.scroll_x = cursor_x_pos_in_line - available_width + self.char_width + 10
            max_scroll_x = max(0, self.max_line_width - available_width)
            self.scroll_x = max(0, min(self.scroll_x, max_scroll_x))
        if old_scroll_y != self.scroll_y or old_scroll_x != self.scroll_x:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
    def _is_word_boundary(self, char):
        """Check if character is a word boundary (whitespace or punctuation)"""
        if not char:
            return True
        return char in ' \t\n.,;:!?()[]{}"\'-/\\+=*&^%$#@<>|~`' or char.isspace()
    
    def _commit_word_to_undo(self):
        """Commit accumulated word buffer to undo stack"""
        if self.word_buffer and self.word_start_line >= 0:
            # Calculate end position
            end_col = self.word_start_col + len(self.word_buffer)
            self.buffer.add_undo_action('insert', {
                'line': self.word_start_line,
                'start': self.word_start_col,
                'end': end_col,
                'text': self.word_buffer
            })
            print(f"Committed word to undo: '{self.word_buffer}' at {self.word_start_line}:{self.word_start_col}")
        # Reset word buffer
        self.word_buffer = ""
        self.word_start_line = -1
        self.word_start_col = -1
    
    def _start_editing(self):
        """Start editing mode on the current cursor line"""
        # Commit any pending word from previous editing session
        self._commit_word_to_undo()
        
        self.editing = True
        self.edit_line = self.cursor_line
        # Ensure we have a valid line to edit
        if self.edit_line >= self.buffer.total_lines:
            self.edit_line = max(0, self.buffer.total_lines - 1)
            self.cursor_line = self.edit_line
        # Get the current line text
        self.edit_text = self.buffer.get_line(self.edit_line)
        # Ensure cursor column is within bounds
        if self.cursor_col > len(self.edit_text):
            self.cursor_col = len(self.edit_text)
        self.edit_cursor_pos = self.cursor_col
        self.queue_draw()
    def _finish_editing(self):
        # Commit any pending word before finishing
        self._commit_word_to_undo()
        
        if self.editing:
            self.buffer.set_line(self.edit_line, self.edit_text)
            self.editing = False
            self.cursor_col = self.edit_cursor_pos
            self.queue_draw()
            self.emit('buffer-changed')
            self.emit('modified-changed', self.buffer.modified)
    def _cancel_editing(self):
        if self.editing:
            self.editing = False
            self.queue_draw()
    def scroll_by_lines(self, lines):
        scroll_amount = lines * self.line_height
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll = max(0, total_visual_height - self.get_height())
        old_scroll_y = self.scroll_y
        self.scroll_y = max(0, min(max_scroll, self.scroll_y + scroll_amount))
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.queue_draw()
    def scroll_to_top(self):
        old_scroll_y = self.scroll_y
        self.scroll_y = 0
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.queue_draw()
    def scroll_to_bottom(self):
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll = max(0, total_visual_height - self.get_height())
        old_scroll_y = self.scroll_y
        self.scroll_y = max_scroll
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.queue_draw()
    def set_scroll_position(self, scroll_y, scroll_x=0):
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll_y = max(0, total_visual_height - self.get_height())
        self.scroll_y = max(0, min(max_scroll_y, scroll_y))
        if not self.buffer.word_wrap:
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 20
            available_width = self.get_width() - line_num_width
            max_scroll_x = max(0, self.max_line_width - available_width)
            self.scroll_x = max(0, min(max_scroll_x, scroll_x))
        self.queue_draw()
    def _recalculate_max_line_width(self):
        if self.buffer.word_wrap:
            self.max_line_width = 0
            return
        max_width = 0
        start_line = max(0, int(self.scroll_y // self.line_height) - 100)
        end_line = min(self.buffer.total_lines, start_line + self.visible_lines + 200)
        sample_lines = []
        step = max(1, (end_line - start_line) // 100)
        for i in range(start_line, end_line, step):
            sample_lines.append(self.buffer.get_line(i))
        if self.editing and (self.edit_line < start_line or self.edit_line >= end_line):
            sample_lines.append(self.edit_text)
        for line_text in sample_lines:
            layout = self.create_pango_layout("")
            layout.set_font_description(self.font_desc)
            layout.set_text(line_text)
            logical_rect = layout.get_extents()[1]
            width = logical_rect.width / Pango.SCALE
            if width > max_width:
                max_width = width
        self.max_line_width = max_width + 20 * self.char_width
    def set_buffer(self, buffer):
        self.buffer = buffer
        self.scroll_y = 0
        self.scroll_x = 0
        self.cursor_line = 0
        self.cursor_col = 0
        self.editing = False
        self.has_selection = False
        self.cursor_visible = True
        self._wrapped_lines_cache.clear()
        self._needs_wrap_recalc = True
        self._recalculate_max_line_width()
        self.emit('buffer-changed')
        self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.emit('modified-changed', self.buffer.modified)
        self.queue_draw()
# --- Settings Dialog ---
class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__()
        self.set_title("Theme Settings")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(600, 400)
        # Application page
        app_page = Adw.PreferencesPage.new()
        app_page.set_title("Application")
        app_page.set_icon_name("preferences-desktop-symbolic")
        # Theme group
        theme_group = Adw.PreferencesGroup.new()
        theme_group.set_title("Theme")
        # Theme selection
        self.theme_row = Adw.ComboRow.new()
        self.theme_row.set_title("Theme")
        theme_model = Gtk.StringList.new(["Atom One Dark", "Atom One Light"])
        self.theme_row.set_model(theme_model)
        # Set default to dark if system is dark, light otherwise
        is_dark = Adw.StyleManager.get_default().get_dark()
        self.theme_row.set_selected(0 if is_dark else 1)
        theme_group.add(self.theme_row)
        app_page.add(theme_group)
        self.add(app_page)
        # Syntax Highlighting page
        syntax_page = Adw.PreferencesPage.new()
        syntax_page.set_title("Syntax Highlighting")
        syntax_page.set_icon_name("code-symbolic")
        # Highlighters group
        highlighters_group = Adw.PreferencesGroup.new()
        highlighters_group.set_title("Highlighters")
        # Language selection
        self.language_row = Adw.ComboRow.new()
        self.language_row.set_title("Language")
        language_model = Gtk.StringList.new(["All Languages", "Python"])
        self.language_row.set_model(language_model)
        self.language_row.set_selected(1) # Default to Python
        highlighters_group.add(self.language_row)
        syntax_page.add(highlighters_group)
        # Syntax elements group
        syntax_group = Adw.PreferencesGroup.new()
        syntax_group.set_title("Syntax Elements")
        # Create rows for each syntax element
        self.color_rows = {}
        syntax_elements = [
            ("keyword", "Keywords"),
            ("type", "Types"),
            ("builtin", "Built-ins"),
            ("string", "Strings"),
            ("number", "Numbers"),
            ("operator", "Operators"),
            ("comment", "Comments"),
            ("identifier", "Identifiers"),
            ("other", "Other")
        ]
        for key, title in syntax_elements:
            row = Adw.ActionRow.new()
            row.set_title(title)
            # Color button for light theme
            light_label = Gtk.Label.new("Light:")
            light_label.set_halign(Gtk.Align.START)
            light_label.set_margin_start(12)
            row.add_prefix(light_label)
            self.color_rows[f"{key}_light"] = Gtk.ColorButton.new()
            # Set default colors based on Atom One theme
            if key == "keyword":
                rgba = Gdk.RGBA()
                rgba.parse("#E06C75" if self.theme_row.get_selected() == 0 else "#A626A4")
                self.color_rows[f"{key}_light"].set_rgba(rgba)
            elif key == "string":
                rgba = Gdk.RGBA()
                rgba.parse("#98C379")
                self.color_rows[f"{key}_light"].set_rgba(rgba)
            elif key == "number":
                rgba = Gdk.RGBA()
                rgba.parse("#D19A66")
                self.color_rows[f"{key}_light"].set_rgba(rgba)
            elif key == "comment":
                rgba = Gdk.RGBA()
                rgba.parse("#7F848E")
                self.color_rows[f"{key}_light"].set_rgba(rgba)
            else:
                rgba = Gdk.RGBA()
                rgba.parse("#000000")
                self.color_rows[f"{key}_light"].set_rgba(rgba)
            row.add_suffix(self.color_rows[f"{key}_light"])
            # Color button for dark theme
            dark_label = Gtk.Label.new("Dark:")
            dark_label.set_halign(Gtk.Align.START)
            dark_label.set_margin_start(12)
            row.add_prefix(dark_label)
            self.color_rows[f"{key}_dark"] = Gtk.ColorButton.new()
            # Set default colors based on Atom One theme
            if key == "keyword":
                rgba = Gdk.RGBA()
                rgba.parse("#E06C75")
                self.color_rows[f"{key}_dark"].set_rgba(rgba)
            elif key == "string":
                rgba = Gdk.RGBA()
                rgba.parse("#98C379")
                self.color_rows[f"{key}_dark"].set_rgba(rgba)
            elif key == "number":
                rgba = Gdk.RGBA()
                rgba.parse("#D19A66")
                self.color_rows[f"{key}_dark"].set_rgba(rgba)
            elif key == "comment":
                rgba = Gdk.RGBA()
                rgba.parse("#7F848E")
                self.color_rows[f"{key}_dark"].set_rgba(rgba)
            else:
                rgba = Gdk.RGBA()
                rgba.parse("#FFFFFF")
                self.color_rows[f"{key}_dark"].set_rgba(rgba)
            row.add_suffix(self.color_rows[f"{key}_dark"])
            syntax_group.add(row)
        # Search highlighting
        search_row = Adw.ActionRow.new()
        search_row.set_title("Search Highlight")
        search_label = Gtk.Label.new("Color:")
        search_label.set_halign(Gtk.Align.START)
        search_label.set_margin_start(12)
        search_row.add_prefix(search_label)
        self.search_color = Gtk.ColorButton.new()
        rgba = Gdk.RGBA()
        rgba.parse("#FFCC00")
        self.search_color.set_rgba(rgba)
        search_row.add_suffix(self.search_color)
        syntax_group.add(search_row)
        # Whitespace highlighting
        whitespace_row = Adw.ActionRow.new()
        whitespace_row.set_title("Whitespace")
        whitespace_label = Gtk.Label.new("Color:")
        whitespace_label.set_halign(Gtk.Align.START)
        whitespace_label.set_margin_start(12)
        whitespace_row.add_prefix(whitespace_label)
        self.whitespace_color = Gtk.ColorButton.new()
        rgba = Gdk.RGBA()
        rgba.parse("#B0B0B0")
        self.whitespace_color.set_rgba(rgba)
        whitespace_row.add_suffix(self.whitespace_color)
        syntax_group.add(whitespace_row)
        syntax_page.add(syntax_group)
        self.add(syntax_page)
        # Document Tabs page
        tabs_page = Adw.PreferencesPage.new()
        tabs_page.set_title("Document Tabs")
        tabs_page.set_icon_name("tab-new-symbolic")
        # Tab appearance group
        tabs_group = Adw.PreferencesGroup.new()
        tabs_group.set_title("Tab Appearance")
        # Tab style
        self.tab_style_row = Adw.ComboRow.new()
        self.tab_style_row.set_title("Style")
        tab_style_model = Gtk.StringList.new(["Default", "Compact", "Large"])
        self.tab_style_row.set_model(tab_style_model)
        self.tab_style_row.set_selected(0)
        tabs_group.add(self.tab_style_row)
        # Show close buttons
        self.close_buttons_switch = Adw.SwitchRow.new()
        self.close_buttons_switch.set_title("Show Close Buttons")
        self.close_buttons_switch.set_active(True)
        tabs_group.add(self.close_buttons_switch)
        # Show modified indicator
        self.modified_indicator_switch = Adw.SwitchRow.new()
        self.modified_indicator_switch.set_title("Show Modified Indicator")
        self.modified_indicator_switch.set_active(True)
        tabs_group.add(self.modified_indicator_switch)
        tabs_page.add(tabs_group)
        self.add(tabs_page)
        # Connect signals
        self.theme_row.connect("notify::selected", self._on_theme_changed)
    def _on_theme_changed(self, combo_row, pspec):
        # Update color buttons based on theme selection
        selected = combo_row.get_selected()
        is_dark = (selected == 0) # 0 = Dark, 1 = Light
        # Update syntax element colors
        syntax_elements = ["keyword", "type", "builtin", "string", "number", "operator", "comment", "identifier", "other"]
        for element in syntax_elements:
            light_key = f"{element}_light"
            dark_key = f"{element}_dark"
            if is_dark:
                # Copy dark colors to light buttons (simulating dark mode)
                dark_rgba = self.color_rows[dark_key].get_rgba()
                self.color_rows[light_key].set_rgba(dark_rgba)
            else:
                # Copy light colors to dark buttons (simulating light mode)
                light_rgba = self.color_rows[light_key].get_rgba()
                self.color_rows[dark_key].set_rgba(light_rgba)
    def apply_settings(self, text_view):
        """Apply the current settings to the text view"""
        # Update syntax colors
        syntax_elements = ["keyword", "type", "builtin", "string", "number", "operator", "comment", "identifier", "other", "whitespace"]
        for element in syntax_elements:
            light_key = f"{element}_light"
            dark_key = f"{element}_dark"
            # Get RGBA values
            light_rgba = self.color_rows[light_key].get_rgba()
            dark_rgba = self.color_rows[dark_key].get_rgba()
            # Convert to RGB tuples (0-1 range)
            text_view.SYNTAX_COLORS[element] = {
                "light": (light_rgba.red, light_rgba.green, light_rgba.blue),
                "dark": (dark_rgba.red, dark_rgba.green, dark_rgba.blue)
            }
        # Force a redraw to apply new colors
        text_view.queue_draw()
class TextEditorWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("High-Performance Text Editor")
        self.set_default_size(1000, 700)

        # --- Top-level layout ---
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

        # --- Header Bar ---
        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)

        # --- Save Button ---
        self.save_button = Gtk.Button(icon_name="document-save-symbolic")
        self.save_button.set_tooltip_text("Save (Ctrl+S)")
        self.save_button.connect("clicked", self.on_save_clicked)
        header_bar.pack_start(self.save_button)
        self.save_button.set_sensitive(False)

        # --- Menu Button ---
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")

        # --- Main vertical box ---
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.set_hexpand(True)
        self.main_box.set_vexpand(True)

        # --- Text View setup ---
        self.text_view = VirtualTextView()
        self.text_view.connect('modified-changed', self._on_modified_changed)
        self.text_view.connect('buffer-changed', self._on_buffer_changed)
        self.text_view.connect('scroll-changed', self._on_scroll_changed)
        self.text_view.set_hexpand(True)
        self.text_view.set_vexpand(True)
        self.text_view.grab_focus()
        # --- Find/Replace bar setup ---
        self.find_revealer = Gtk.Revealer()
        self.find_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.find_bar = FindReplaceBar(self.text_view)
        self.find_revealer.set_child(self.find_bar)
        self.find_revealer.set_reveal_child(False)

        # --- Search button in header bar ---
        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.set_tooltip_text("Find and Replace")

        def toggle_find_bar(button):
            visible = not self.find_revealer.get_reveal_child()
            self.find_revealer.set_reveal_child(visible)
            if visible:
                # Hide textview cursor and focus Find entry when shown
                self.text_view.cursor_visible = False
                self.find_bar.find_entry.grab_focus()
            else:
                # Restore textview cursor and focus when hidden
                self.text_view.cursor_visible = True
                self.text_view.grab_focus()

        search_btn.connect("clicked", toggle_find_bar)
        header_bar.pack_end(search_btn)

        # --- Text and scrollbars layout ---
        text_and_v_scroll_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        text_and_v_scroll_box.set_hexpand(True)
        text_and_v_scroll_box.set_vexpand(True)
        text_and_v_scroll_box.append(self.text_view)

        self.v_scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL)
        self.v_scrollbar.set_adjustment(Gtk.Adjustment())
        text_and_v_scroll_box.append(self.v_scrollbar)

        self.h_scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL)
        self.h_scrollbar.set_hexpand(True)

        # --- Assemble main content ---
        self.main_box.append(text_and_v_scroll_box)    # editor + vertical scrollbar
        self.main_box.append(self.h_scrollbar)         # horizontal scrollbar
        self.main_box.append(self.find_revealer)       # search bar at bottom

        # --- Menu model ---
        header_bar.pack_end(menu_button)
        menu_model = Gio.Menu()
        menu_model.append("Open File", "app.open_file")
        menu_model.append("Save File", "app.save_file")
        menu_model.append("Generate Test Data", "app.generate_test")
        menu_model.append("Go to Top", "app.go_top")
        menu_model.append("Go to Bottom", "app.go_bottom")
        menu_model.append("Toggle Word Wrap (Ctrl+W)", "app.toggle_wrap")
        menu_model.append("Settings", "app.settings")
        menu_button.set_menu_model(menu_model)

        # --- Status Bar ---
        self.status_bar = Gtk.Label(label="Ready")
        self.status_bar.set_halign(Gtk.Align.START)
        self.status_bar.add_css_class("dim-label")
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.append(self.status_bar)
        status_box.add_css_class("toolbar")
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)
        status_box.set_margin_top(6)
        status_box.set_margin_bottom(6)
        self.toolbar_view.add_bottom_bar(status_box)

        # --- Set final layout ---
        self.toolbar_view.set_content(self.main_box)

        # --- Scroll connections ---
        self.v_scrollbar.get_adjustment().connect('value-changed', self._on_v_scrollbar_changed)
        self.h_scrollbar.get_adjustment().connect('value-changed', self._on_h_scrollbar_changed)

        # --- Global actions ---
        self.cut_action = Gio.SimpleAction.new("cut", None)
        self.cut_action.connect("activate", lambda a, p: self.text_view._cut_to_clipboard())
        self.add_action(self.cut_action)

        self.copy_action = Gio.SimpleAction.new("copy", None)
        self.copy_action.connect("activate", lambda a, p: self.text_view._copy_to_clipboard())
        self.add_action(self.copy_action)

        self.paste_action = Gio.SimpleAction.new("paste", None)
        self.paste_action.connect("activate", lambda a, p: self.text_view._paste_from_clipboard())
        self.add_action(self.paste_action)

        self.delete_action = Gio.SimpleAction.new("delete", None)
        self.delete_action.connect("activate", lambda a, p: self.text_view._delete_selection())
        self.add_action(self.delete_action)

        self.select_all_action = Gio.SimpleAction.new("select_all", None)
        self.select_all_action.connect("activate", lambda a, p: self.text_view._select_all())
        self.add_action(self.select_all_action)

        # --- Settings Action ---
        self.settings_action = Gio.SimpleAction.new("settings", None)
        self.settings_action.connect("activate", self._on_settings_activated)
        self.add_action(self.settings_action)

        # Add settings action
        self.settings_action = Gio.SimpleAction.new("settings", None)
        self.settings_action.connect("activate", self._on_settings_activated)
        self.add_action(self.settings_action)
    def _on_settings_activated(self, action, param):
        settings_dialog = SettingsDialog(self)
        settings_dialog.present()
    def _on_modified_changed(self, text_view, is_modified):
        self.save_button.set_sensitive(is_modified)
        title = self.get_title()
        if is_modified and not title.endswith("*"):
            self.set_title(title + " *")
        elif not is_modified and title.endswith("*"):
            self.set_title(title[:-2])
    def _on_buffer_changed(self, text_view):
        self.text_view._recalculate_max_line_width()
        if self.text_view.buffer.word_wrap:
            self.text_view._wrapped_lines_cache.clear()
            self.text_view._needs_wrap_recalc = True
        self._update_scrollbar()
    def _on_scroll_changed(self, text_view, scroll_y, scroll_x):
        total_height = text_view._get_total_visual_lines() * text_view.line_height
        viewport_height = text_view.get_height()
        if total_height > viewport_height:
            v_adjustment = self.v_scrollbar.get_adjustment()
            v_adjustment.handler_block_by_func(self._on_v_scrollbar_changed)
            v_adjustment.set_value(scroll_y)
            v_adjustment.handler_unblock_by_func(self._on_v_scrollbar_changed)
        if not text_view.buffer.word_wrap:
            line_num_width = len(str(text_view.buffer.total_lines)) * text_view.char_width + 20
            available_width = text_view.get_width() - line_num_width
            total_width = text_view.max_line_width
            h_adjustment = self.h_scrollbar.get_adjustment()
            h_adjustment.handler_block_by_func(self._on_h_scrollbar_changed)
            h_adjustment.set_value(scroll_x)
            h_adjustment.handler_unblock_by_func(self._on_h_scrollbar_changed)
        else:
            h_adjustment = self.h_scrollbar.get_adjustment()
            h_adjustment.handler_block_by_func(self._on_h_scrollbar_changed)
            h_adjustment.set_value(0)
            h_adjustment.handler_unblock_by_func(self._on_h_scrollbar_changed)
    def _on_v_scrollbar_changed(self, adjustment):
        new_scroll_y = adjustment.get_value()
        current_scroll_x = self.text_view.scroll_x if hasattr(self.text_view, 'scroll_x') else 0
        self.text_view.set_scroll_position(new_scroll_y, current_scroll_x)
    def _on_h_scrollbar_changed(self, adjustment):
        new_scroll_x = adjustment.get_value()
        current_scroll_y = self.text_view.scroll_y
        self.text_view.set_scroll_position(current_scroll_y, new_scroll_x)
    def _update_scrollbar(self):
        if not hasattr(self, 'v_scrollbar'):
            return
        total_height = self.text_view._get_total_visual_lines() * self.text_view.line_height
        viewport_height = self.text_view.get_height()
        v_adjustment = self.v_scrollbar.get_adjustment()
        v_adjustment.set_lower(0)
        v_adjustment.set_upper(max(total_height, viewport_height))
        v_adjustment.set_page_size(viewport_height)
        v_adjustment.set_step_increment(self.text_view.line_height)
        v_adjustment.set_page_increment(viewport_height * 0.9)
        v_adjustment.set_value(self.text_view.scroll_y)
        if not self.text_view.buffer.word_wrap:
            line_num_width = len(str(self.text_view.buffer.total_lines)) * self.text_view.char_width + 20
            available_width = self.text_view.get_width() - line_num_width
            total_width = self.text_view.max_line_width
            h_adjustment = self.h_scrollbar.get_adjustment()
            h_adjustment.set_lower(0)
            h_adjustment.set_upper(max(total_width, available_width))
            h_adjustment.set_page_size(available_width)
            h_adjustment.set_step_increment(self.text_view.char_width * 10)
            h_adjustment.set_page_increment(available_width * 0.9)
            h_adjustment.set_value(self.text_view.scroll_x)
            self.h_scrollbar.set_visible(True)
        else:
            self.h_scrollbar.set_visible(False)
    def generate_test_data(self):
        def generate_lines():
            lines = []
            for i in range(1000000):
                if i % 10000 == 0:
                    lines.append(f"=== Section {i//10000 + 1} === Line {i+1} ===")
                elif i % 1000 == 0:
                    lines.append(f"--- Subsection {i//1000 + 1} --- Line {i+1}")
                elif i % 100 == 0:
                    lines.append(f"Line {i+1}: This is a longer line with more content to test horizontal scrolling and text rendering performance in our virtual text view.")
                else:
                    lines.append(f"Line {i+1}: Sample text content for testing virtual scrolling")
            return lines
        def load_data():
            start_time = time.time()
            lines = generate_lines()
            load_time = time.time() - start_time
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
            files = dialog.get_files() # Returns a GList
            if files:
                file = files[0] # Get the first file
                self._load_file(file.get_path())
        dialog.destroy()
        self.text_view.grab_focus()
    def _load_file(self, filepath):
        def load_file():
            try:
                start_time = time.time()
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    lines = [line.rstrip('\n\r') for line in f]
                load_time = time.time() - start_time
                GLib.idle_add(lambda: self._on_file_loaded(lines, load_time, filepath))
            except Exception as e:
                GLib.idle_add(lambda: self._on_file_error(str(e)))
        self.status_bar.set_text(f"Loading {os.path.basename(filepath)}...")
        thread = threading.Thread(target=load_file)
        thread.daemon = True
        thread.start()
    def _on_file_loaded(self, lines, load_time, filepath):
        buffer = VirtualTextBuffer()
        buffer.load_lines(lines)
        buffer.file_path = filepath
        # Enable syntax highlighting for Python files
        _, ext = os.path.splitext(filepath)
        if ext.lower() == ".py":
            buffer.set_language("python")
        else:
            buffer.set_language(None)
        self.text_view.set_buffer(buffer)
        GLib.timeout_add(100, self._update_scrollbar)
        filename = os.path.basename(filepath)
        self.status_bar.set_text(f"Loaded {filename} - {len(lines):,} lines in {load_time:.2f}s")
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
    def save_file(self, file_path=None):
        buffer = self.text_view.buffer
        path = file_path or buffer.file_path
        if not path:
            self.save_file_as()
            return
        def save_in_thread():
            success = buffer.save_to_file(path)
            GLib.idle_add(lambda: self._on_file_saved(success, path))
        self.status_bar.set_text(f"Saving {os.path.basename(path)}...")
        thread = threading.Thread(target=save_in_thread)
        thread.daemon = True
        thread.start()
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
                self.save_file(file_path)
        dialog.destroy()
        self.text_view.grab_focus()
    def _on_file_saved(self, success, file_path):
        if success:
            filename = os.path.basename(file_path)
            self.status_bar.set_text(f"Saved {filename}")
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
