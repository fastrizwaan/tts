#!/usr/bin/env python3
"""
Enhanced Virtual Text Buffer Editor with GtkSourceView-like features

Version: 2.1 (GTK4 Compliant)
Date: November 2025

Changes in v2.1:
- Fixed all GTK3 deprecation warnings
- Replaced StyleContext with Adwaita StyleManager
- Replaced ComboBoxText with DropDown + StringList
- Added live theme switching support
- Fixed drag gesture crash
- Added visible scrollbars
- Full dark/light theme support

Features:
- Line numbers with gutter
- Multi-language syntax highlighting
- Bracket matching
- Current line highlighting
- Right margin indicator
- Smart home/end keys
- Auto-indentation
- Code folding
- Search and replace with regex
- Comment/uncomment
- Mark occurrences
- Tab/space conversion
- Multiple cursors (basic)
- Undo/Redo with grouping
"""

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
import chardet

# ============================================================================
# SYNTAX HIGHLIGHTING PATTERNS
# ============================================================================

class SyntaxPatterns:
    """Comprehensive syntax patterns for multiple languages"""
    
    PYTHON = {
        'keywords': r'\b(False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b',
        'builtins': r'\b(abs|all|any|ascii|bin|bool|bytearray|bytes|callable|chr|classmethod|compile|complex|delattr|dict|dir|divmod|enumerate|eval|exec|filter|float|format|frozenset|getattr|globals|hasattr|hash|help|hex|id|input|int|isinstance|issubclass|iter|len|list|locals|map|max|memoryview|min|next|object|oct|open|ord|pow|print|property|range|repr|reversed|round|set|setattr|slice|sorted|staticmethod|str|sum|super|tuple|type|vars|zip|__import__)\b',
        'string': r'(""".*?"""|\'\'\'.*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
        'comment': r'#.*$',
        'decorator': r'@\w+',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?\b',
        'function': r'\bdef\s+(\w+)',
        'class': r'\bclass\s+(\w+)',
    }
    
    JAVASCRIPT = {
        'keywords': r'\b(break|case|catch|class|const|continue|debugger|default|delete|do|else|export|extends|finally|for|function|if|import|in|instanceof|let|new|return|super|switch|this|throw|try|typeof|var|void|while|with|yield)\b',
        'builtins': r'\b(Array|Boolean|Date|Error|Function|JSON|Math|Number|Object|Promise|RegExp|String|Symbol|console|document|window)\b',
        'string': r'(`(?:[^`\\]|\\.)*`|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
        'comment': r'(//.*$|/\*[\s\S]*?\*/)',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?\b',
        'function': r'\bfunction\s+(\w+)',
        'class': r'\bclass\s+(\w+)',
    }
    
    C = {
        'keywords': r'\b(auto|break|case|char|const|continue|default|do|double|else|enum|extern|float|for|goto|if|inline|int|long|register|restrict|return|short|signed|sizeof|static|struct|switch|typedef|union|unsigned|void|volatile|while)\b',
        'preprocessor': r'#\s*(include|define|undef|ifdef|ifndef|if|else|elif|endif|pragma)',
        'string': r'"(?:[^"\\]|\\.)*"',
        'comment': r'(//.*$|/\*[\s\S]*?\*/)',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?[fFuUlL]?\b',
    }
    
    RUST = {
        'keywords': r'\b(as|async|await|break|const|continue|crate|dyn|else|enum|extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while)\b',
        'types': r'\b(i8|i16|i32|i64|i128|isize|u8|u16|u32|u64|u128|usize|f32|f64|bool|char|str|String|Vec|Box|Option|Result)\b',
        'string': r'(r#".*?"#|"(?:[^"\\]|\\.)*")',
        'comment': r'(//.*$|/\*[\s\S]*?\*/)',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?\b',
        'macro': r'\b\w+!',
    }
    
    HTML = {
        'tag': r'</?[\w-]+>?',
        'attribute': r'\b[\w-]+=',
        'string': r'"[^"]*"|\'[^\']*\'',
        'comment': r'<!--[\s\S]*?-->',
        'entity': r'&\w+;',
    }
    
    CSS = {
        'selector': r'[.#]?[\w-]+(?=\s*\{)',
        'property': r'\b[\w-]+(?=:)',
        'string': r'"[^"]*"|\'[^\']*\'',
        'comment': r'/\*[\s\S]*?\*/',
        'number': r'\b\d+\.?\d*(px|em|rem|%|vh|vw)?\b',
        'color': r'#[0-9a-fA-F]{3,8}\b',
    }

    @classmethod
    def get_patterns(cls, language):
        """Get syntax patterns for a specific language"""
        if language == 'python':
            return cls.PYTHON
        elif language == 'javascript':
            return cls.JAVASCRIPT
        elif language == 'c':
            return cls.C
        elif language == 'rust':
            return cls.RUST
        elif language == 'html':
            return cls.HTML
        elif language == 'css':
            return cls.CSS
        return {}

# ============================================================================
# ENCODING DETECTION
# ============================================================================

def detect_encoding(file_path):
    """Detect the encoding of a file"""
    with open(file_path, 'rb') as f:
        start = f.read(4)
    
    # Check for BOM
    if start.startswith(b'\xef\xbb\xbf'):
        return ('utf-8-sig', 1.0, True)
    if start.startswith(b'\xff\xfe'):
        if start[2:4] == b'\x00\x00':
            return ('utf-32-le', 1.0, True)
        return ('utf-16-le', 1.0, True)
    if start.startswith(b'\xfe\xff'):
        return ('utf-16-be', 1.0, True)
    if start.startswith(b'\xff\xfe\x00\x00'):
        return ('utf-32-le', 1.0, True)
    if start.startswith(b'\x00\x00\xfe\xff'):
        return ('utf-32-be', 1.0, True)
    
    # Use chardet for detection
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(100000)
        result = chardet.detect(raw_data)
        encoding = result['encoding']
        confidence = result['confidence']
        if encoding:
            encoding = encoding.lower()
            if encoding in ['ascii', 'us-ascii']:
                encoding = 'utf-8'
        return (encoding or 'utf-8', confidence, False)
    except Exception as e:
        print(f"Encoding detection failed: {e}")
        return ('utf-8', 0.5, False)

def load_file_with_encoding(file_path, encoding=None):
    """Load a file with specified or auto-detected encoding"""
    if encoding is None:
        encoding, confidence, has_bom = detect_encoding(file_path)
        detection_info = f"Detected: {encoding} (confidence: {confidence:.0%})"
        if has_bom:
            detection_info += " [BOM]"
    else:
        detection_info = f"Specified: {encoding}"
    
    try:
        with open(file_path, 'r', encoding=encoding, errors='replace') as f:
            content = f.read()
        lines = content.split('\n')
        lines = [line.rstrip('\r') for line in lines]
        return (lines, encoding, detection_info)
    except Exception as e:
        print(f"Failed to load with {encoding}: {e}. Falling back to UTF-8.")
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            lines = content.split('\n')
            lines = [line.rstrip('\r') for line in lines]
            return (lines, 'utf-8', f"Fallback: utf-8")
        except Exception as e2:
            return (["Error loading file"], 'utf-8', f"Error: {str(e2)[:100]}")

# ============================================================================
# UNDO/REDO SYSTEM
# ============================================================================

class UndoAction:
    """Represents a single undoable action"""
    def __init__(self, action_type, line_num, old_content=None, new_content=None):
        self.action_type = action_type  # 'insert', 'delete', 'replace'
        self.line_num = line_num
        self.old_content = old_content
        self.new_content = new_content
        self.timestamp = time.time()

class UndoManager:
    """Manages undo/redo operations with action grouping"""
    def __init__(self, max_undo=1000):
        self.undo_stack = deque(maxlen=max_undo)
        self.redo_stack = deque(maxlen=max_undo)
        self.current_group = []
        self.grouping = False
        self.last_action_time = time.time()
    
    def begin_group(self):
        """Begin a group of actions that should be undone together"""
        self.grouping = True
        self.current_group = []
    
    def end_group(self):
        """End the current action group"""
        if self.current_group:
            self.undo_stack.append(self.current_group)
            self.redo_stack.clear()
        self.grouping = False
        self.current_group = []
    
    def add_action(self, action):
        """Add an action to the undo stack"""
        current_time = time.time()
        
        # Auto-group rapid actions (within 0.5 seconds)
        if not self.grouping and current_time - self.last_action_time < 0.5:
            self.begin_group()
        
        if self.grouping:
            self.current_group.append(action)
        else:
            self.undo_stack.append([action])
            self.redo_stack.clear()
        
        self.last_action_time = current_time
    
    def undo(self):
        """Undo the last action or action group"""
        if self.grouping:
            self.end_group()
        
        if not self.undo_stack:
            return None
        
        actions = self.undo_stack.pop()
        self.redo_stack.append(actions)
        return actions
    
    def redo(self):
        """Redo the last undone action or action group"""
        if not self.redo_stack:
            return None
        
        actions = self.redo_stack.pop()
        self.undo_stack.append(actions)
        return actions
    
    def clear(self):
        """Clear all undo/redo history"""
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.current_group = []
        self.grouping = False

# ============================================================================
# VIRTUAL TEXT BUFFER
# ============================================================================

class VirtualTextBuffer(GObject.Object):
    """Enhanced virtual text buffer with advanced features"""
    
    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'cursor-moved': (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }
    
    def __init__(self):
        super().__init__()
        self.lines = [""]
        self.cursor_line = 0
        self.cursor_col = 0
        
        # File properties
        self.file_path = None
        self.encoding = 'utf-8'
        self.encoding_info = 'UTF-8'
        self.modified = False
        
        # Editor settings
        self.language = None
        self.tab_width = 4
        self.insert_spaces = True  # Use spaces instead of tabs
        self.show_line_numbers = True
        self.show_right_margin = True
        self.right_margin_position = 80
        self.highlight_current_line = True
        self.auto_indent = True
        self.smart_home_end = True
        self.word_wrap = False
        
        # Syntax highlighting
        self.syntax_cache = {}
        self.syntax_patterns = {}
        
        # Bracket matching
        self.matching_brackets = {
            '(': ')', '[': ']', '{': '}',
            ')': '(', ']': '[', '}': '{'
        }
        
        # Code folding
        self.folded_lines = set()  # Set of line numbers that are folded
        
        # Search
        self.search_text = ""
        self.search_regex = False
        self.search_case_sensitive = False
        self.search_matches = []
        self.current_match_index = -1
        
        # Mark occurrences
        self.marked_text = ""
        self.marked_occurrences = []
        
        # Undo/Redo
        self.undo_manager = UndoManager()
        
        # Multiple cursors
        self.extra_cursors = []  # List of (line, col) tuples
    
    def load_lines(self, lines):
        """Load lines into the buffer"""
        self.lines = lines if lines else [""]
        self.cursor_line = 0
        self.cursor_col = 0
        self.modified = False
        self.syntax_cache.clear()
        self.undo_manager.clear()
        self.emit('changed')
    
    def get_line(self, line_num):
        """Get a specific line"""
        if 0 <= line_num < len(self.lines):
            return self.lines[line_num]
        return ""
    
    def get_line_count(self):
        """Get total number of lines"""
        return len(self.lines)
    
    def set_language(self, language):
        """Set the syntax highlighting language"""
        self.language = language
        self.syntax_patterns = SyntaxPatterns.get_patterns(language) if language else {}
        self.syntax_cache.clear()
        self.emit('changed')
    
    def get_syntax_tokens(self, line_num):
        """Get syntax highlighting tokens for a line"""
        if not self.syntax_patterns or line_num >= len(self.lines):
            return []
        
        if line_num in self.syntax_cache:
            return self.syntax_cache[line_num]
        
        line = self.lines[line_num]
        tokens = []
        
        # Apply patterns in order of priority
        pattern_order = ['comment', 'string', 'decorator', 'preprocessor', 
                        'keywords', 'builtins', 'types', 'function', 'class',
                        'number', 'tag', 'attribute', 'property', 'selector',
                        'color', 'entity', 'macro']
        
        covered = set()  # Track which character positions are already tokenized
        
        for pattern_name in pattern_order:
            if pattern_name not in self.syntax_patterns:
                continue
            
            pattern = self.syntax_patterns[pattern_name]
            try:
                for match in re.finditer(pattern, line, re.MULTILINE):
                    start, end = match.span()
                    # Check if this range overlaps with already covered positions
                    if not any(pos in covered for pos in range(start, end)):
                        tokens.append((start, end, pattern_name))
                        covered.update(range(start, end))
            except re.error:
                continue
        
        # Sort tokens by start position
        tokens.sort(key=lambda x: x[0])
        self.syntax_cache[line_num] = tokens
        return tokens
    
    def insert_text(self, line_num, col, text):
        """Insert text at position"""
        if not (0 <= line_num < len(self.lines)):
            return
        
        old_line = self.lines[line_num]
        new_line = old_line[:col] + text + old_line[col:]
        
        # Add to undo stack
        action = UndoAction('insert', line_num, old_line, new_line)
        self.undo_manager.add_action(action)
        
        self.lines[line_num] = new_line
        self.modified = True
        self.invalidate_syntax_cache(line_num)
        self.emit('changed')
    
    def delete_range(self, start_line, start_col, end_line, end_col):
        """Delete text in range"""
        if start_line == end_line:
            old_line = self.lines[start_line]
            new_line = old_line[:start_col] + old_line[end_col:]
            
            action = UndoAction('delete', start_line, old_line, new_line)
            self.undo_manager.add_action(action)
            
            self.lines[start_line] = new_line
        else:
            # Multi-line deletion
            self.undo_manager.begin_group()
            
            first_line = self.lines[start_line][:start_col]
            last_line = self.lines[end_line][end_col:]
            
            # Save old lines for undo
            for i in range(start_line, end_line + 1):
                action = UndoAction('delete', i, self.lines[i], None)
                self.undo_manager.add_action(action)
            
            # Delete intermediate lines
            del self.lines[start_line:end_line + 1]
            
            # Insert combined line
            self.lines.insert(start_line, first_line + last_line)
            
            action = UndoAction('insert', start_line, None, first_line + last_line)
            self.undo_manager.add_action(action)
            
            self.undo_manager.end_group()
        
        self.modified = True
        self.invalidate_syntax_cache(start_line)
        self.emit('changed')
    
    def insert_line_break(self, line_num, col):
        """Insert a line break at position"""
        if not (0 <= line_num < len(self.lines)):
            return
        
        line = self.lines[line_num]
        first_part = line[:col]
        second_part = line[col:]
        
        # Auto-indent: calculate indentation
        indent = ""
        if self.auto_indent:
            indent = self._get_line_indent(line)
            # If line ends with colon, increase indent
            if first_part.rstrip().endswith(':'):
                indent += '\t' if not self.insert_spaces else ' ' * self.tab_width
        
        self.undo_manager.begin_group()
        
        action = UndoAction('replace', line_num, line, first_part)
        self.undo_manager.add_action(action)
        
        self.lines[line_num] = first_part
        self.lines.insert(line_num + 1, indent + second_part)
        
        action = UndoAction('insert', line_num + 1, None, indent + second_part)
        self.undo_manager.add_action(action)
        
        self.undo_manager.end_group()
        
        self.modified = True
        self.invalidate_syntax_cache(line_num)
        self.emit('changed')
        
        return len(indent)
    
    def _get_line_indent(self, line):
        """Get the indentation of a line"""
        indent = ""
        for char in line:
            if char in ' \t':
                indent += char
            else:
                break
        return indent
    
    def get_indent_level(self, line_num):
        """Get the indentation level of a line"""
        if not (0 <= line_num < len(self.lines)):
            return 0
        line = self.lines[line_num]
        indent = self._get_line_indent(line)
        if '\t' in indent:
            return indent.count('\t')
        return len(indent) // self.tab_width
    
    def invalidate_syntax_cache(self, start_line=0):
        """Invalidate syntax cache from a line onwards"""
        keys_to_remove = [k for k in self.syntax_cache.keys() if k >= start_line]
        for key in keys_to_remove:
            del self.syntax_cache[key]
    
    def find_matching_bracket(self, line_num, col):
        """Find matching bracket for the bracket at position"""
        if not (0 <= line_num < len(self.lines)):
            return None
        
        line = self.lines[line_num]
        if col >= len(line):
            return None
        
        char = line[col]
        if char not in self.matching_brackets:
            return None
        
        matching_char = self.matching_brackets[char]
        
        # Determine search direction
        if char in '([{':
            direction = 1
            open_chars = '([{'
            close_chars = ')]}'
        else:
            direction = -1
            open_chars = ')]}'
            close_chars = '([{'
        
        depth = 1
        current_line = line_num
        current_col = col + direction
        
        while 0 <= current_line < len(self.lines):
            current_line_text = self.lines[current_line]
            
            if direction == 1:
                search_range = range(current_col, len(current_line_text))
            else:
                search_range = range(current_col, -1, -1)
            
            for i in search_range:
                c = current_line_text[i]
                if c == char:
                    depth += 1
                elif c == matching_char:
                    depth -= 1
                    if depth == 0:
                        return (current_line, i)
            
            # Move to next line
            current_line += direction
            if direction == 1:
                current_col = 0
            else:
                if current_line >= 0:
                    current_col = len(self.lines[current_line]) - 1
        
        return None
    
    def toggle_fold(self, line_num):
        """Toggle code folding for a line"""
        if line_num in self.folded_lines:
            self.folded_lines.remove(line_num)
        else:
            # Only fold if there are lines with greater indentation below
            current_indent = self.get_indent_level(line_num)
            has_children = False
            for i in range(line_num + 1, min(line_num + 100, len(self.lines))):
                if self.get_indent_level(i) > current_indent:
                    has_children = True
                    break
                elif self.lines[i].strip() and self.get_indent_level(i) <= current_indent:
                    break
            
            if has_children:
                self.folded_lines.add(line_num)
        
        self.emit('changed')
    
    def is_line_visible(self, line_num):
        """Check if a line is visible (not folded)"""
        current_indent = self.get_indent_level(line_num)
        
        # Check if any parent line is folded
        for i in range(line_num - 1, -1, -1):
            if i in self.folded_lines:
                parent_indent = self.get_indent_level(i)
                if parent_indent < current_indent:
                    return False
            # Stop checking if we reach a line with same or less indentation
            if self.lines[i].strip() and self.get_indent_level(i) <= current_indent:
                if i != line_num - 1:
                    break
        
        return True
    
    def comment_line(self, line_num):
        """Toggle comment on a line"""
        if not (0 <= line_num < len(self.lines)):
            return
        
        comment_char = self._get_comment_char()
        if not comment_char:
            return
        
        line = self.lines[line_num]
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        
        if stripped.startswith(comment_char):
            # Uncomment
            new_line = indent + stripped[len(comment_char):].lstrip()
        else:
            # Comment
            new_line = indent + comment_char + ' ' + stripped
        
        action = UndoAction('replace', line_num, line, new_line)
        self.undo_manager.add_action(action)
        
        self.lines[line_num] = new_line
        self.modified = True
        self.invalidate_syntax_cache(line_num)
        self.emit('changed')
    
    def _get_comment_char(self):
        """Get the comment character for the current language"""
        if self.language in ['python', 'bash', 'ruby', 'perl']:
            return '#'
        elif self.language in ['javascript', 'c', 'cpp', 'java', 'rust', 'go']:
            return '//'
        elif self.language in ['html', 'xml']:
            return '<!--'
        elif self.language == 'css':
            return '/*'
        return '#'  # Default
    
    def convert_tabs_to_spaces(self):
        """Convert all tabs to spaces"""
        self.undo_manager.begin_group()
        
        for i, line in enumerate(self.lines):
            if '\t' in line:
                old_line = line
                new_line = line.replace('\t', ' ' * self.tab_width)
                action = UndoAction('replace', i, old_line, new_line)
                self.undo_manager.add_action(action)
                self.lines[i] = new_line
                self.invalidate_syntax_cache(i)
        
        self.undo_manager.end_group()
        self.modified = True
        self.emit('changed')
    
    def convert_spaces_to_tabs(self):
        """Convert leading spaces to tabs"""
        self.undo_manager.begin_group()
        
        for i, line in enumerate(self.lines):
            indent = self._get_line_indent(line)
            if ' ' in indent:
                old_line = line
                space_count = len(indent.replace('\t', ' ' * self.tab_width))
                tab_count = space_count // self.tab_width
                remaining_spaces = space_count % self.tab_width
                new_indent = '\t' * tab_count + ' ' * remaining_spaces
                new_line = new_indent + line[len(indent):]
                
                action = UndoAction('replace', i, old_line, new_line)
                self.undo_manager.add_action(action)
                self.lines[i] = new_line
                self.invalidate_syntax_cache(i)
        
        self.undo_manager.end_group()
        self.modified = True
        self.emit('changed')
    
    def undo(self):
        """Undo last action"""
        actions = self.undo_manager.undo()
        if not actions:
            return False
        
        for action in reversed(actions):
            if action.action_type == 'insert':
                if action.old_content is not None:
                    self.lines[action.line_num] = action.old_content
            elif action.action_type == 'delete':
                if action.old_content is not None:
                    if action.line_num < len(self.lines):
                        self.lines[action.line_num] = action.old_content
                    else:
                        self.lines.insert(action.line_num, action.old_content)
            elif action.action_type == 'replace':
                if action.old_content is not None:
                    self.lines[action.line_num] = action.old_content
        
        self.syntax_cache.clear()
        self.emit('changed')
        return True
    
    def redo(self):
        """Redo last undone action"""
        actions = self.undo_manager.redo()
        if not actions:
            return False
        
        for action in actions:
            if action.action_type == 'insert':
                if action.new_content is not None:
                    if action.line_num < len(self.lines):
                        self.lines[action.line_num] = action.new_content
                    else:
                        self.lines.insert(action.line_num, action.new_content)
            elif action.action_type == 'delete':
                if action.new_content is not None:
                    self.lines[action.line_num] = action.new_content
            elif action.action_type == 'replace':
                if action.new_content is not None:
                    self.lines[action.line_num] = action.new_content
        
        self.syntax_cache.clear()
        self.emit('changed')
        return True
    
    def search(self, text, regex=False, case_sensitive=False):
        """Search for text in the buffer"""
        self.search_text = text
        self.search_regex = regex
        self.search_case_sensitive = case_sensitive
        self.search_matches = []
        self.current_match_index = -1
        
        if not text:
            return
        
        if regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(text, flags)
            except re.error:
                return
        
        for line_num, line in enumerate(self.lines):
            if regex:
                for match in pattern.finditer(line):
                    self.search_matches.append((line_num, match.start(), match.end()))
            else:
                search_line = line if case_sensitive else line.lower()
                search_text = text if case_sensitive else text.lower()
                start = 0
                while True:
                    pos = search_line.find(search_text, start)
                    if pos == -1:
                        break
                    self.search_matches.append((line_num, pos, pos + len(text)))
                    start = pos + 1
    
    def find_next(self):
        """Find next search match"""
        if not self.search_matches:
            return None
        
        # Find the first match after current cursor position
        current_pos = (self.cursor_line, self.cursor_col)
        
        for i, match in enumerate(self.search_matches):
            match_line, match_start, match_end = match
            if (match_line > current_pos[0] or 
                (match_line == current_pos[0] and match_start > current_pos[1])):
                self.current_match_index = i
                return match
        
        # No match found after cursor, wrap to beginning
        if self.search_matches:
            self.current_match_index = 0
            return self.search_matches[0]
        
        return None
    
    def find_previous(self):
        """Find previous search match"""
        if not self.search_matches:
            return None
        
        # Find the last match before current cursor position
        current_pos = (self.cursor_line, self.cursor_col)
        
        for i in range(len(self.search_matches) - 1, -1, -1):
            match = self.search_matches[i]
            match_line, match_start, match_end = match
            if (match_line < current_pos[0] or 
                (match_line == current_pos[0] and match_start < current_pos[1])):
                self.current_match_index = i
                return match
        
        # No match found before cursor, wrap to end
        if self.search_matches:
            self.current_match_index = len(self.search_matches) - 1
            return self.search_matches[-1]
        
        return None
    
    def mark_occurrences(self, text):
        """Mark all occurrences of text"""
        self.marked_text = text
        self.marked_occurrences = []
        
        if not text or len(text) < 2:
            return
        
        for line_num, line in enumerate(self.lines):
            start = 0
            while True:
                pos = line.find(text, start)
                if pos == -1:
                    break
                self.marked_occurrences.append((line_num, pos, pos + len(text)))
                start = pos + 1
    
    def save_to_file(self, file_path, encoding='utf-8'):
        """Save buffer to file"""
        try:
            content = '\n'.join(self.lines)
            with open(file_path, 'w', encoding=encoding, errors='replace') as f:
                f.write(content)
            self.modified = False
            self.file_path = file_path
            self.encoding = encoding
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False

# ============================================================================
# VIRTUAL TEXT VIEW
# ============================================================================

class VirtualTextView(Gtk.DrawingArea):
    """Enhanced virtual text view with GtkSourceView-like features"""
    
    def __init__(self):
        super().__init__()
        self.buffer = VirtualTextBuffer()
        self.buffer.connect('changed', self._on_buffer_changed)
        
        # Drawing properties
        self.font_family = "Monospace"
        self.font_size = 10
        self.font_desc = Pango.FontDescription(f"{self.font_family} {self.font_size}")
        self.line_height = 0
        self.char_width = 0
        self.gutter_width = 50  # Width for line numbers
        
        # Colors - will be initialized from theme
        self._init_colors()
        
        # Scrolling
        self.scroll_offset = 0
        self.scroll_x = 0
        self.max_scroll = 0
        
        # Selection
        self.selection_start = None
        self.selection_end = None
        self.drag_selecting = False
        
        # Wrapping
        self._wrapped_lines_cache = {}
        self._needs_wrap_recalc = False
        
        # Set up drawing
        self.set_draw_func(self._on_draw)
        
        # Connect to size-allocate to update scrollbars
        self.connect('resize', self._on_resize)
        
        # Event controllers
        self._setup_event_controllers()
        
        # Make focusable
        self.set_focusable(True)
        self.set_can_focus(True)
        
        # Initial size
        self.set_size_request(400, 300)
    
    def _init_colors(self):
        """Initialize colors from GTK theme"""
        # Use Adwaita StyleManager to detect dark mode in GTK4
        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()
        
        # Set base colors based on theme
        if is_dark:
            self.bg_color = (0.18, 0.2, 0.21)
            self.text_color = (0.92, 0.92, 0.92)
        else:
            self.bg_color = (1.0, 1.0, 1.0)
            self.text_color = (0.0, 0.0, 0.0)
        
        if is_dark:
            # Dark theme colors
            self.current_line_color = (0.25, 0.27, 0.28)
            self.gutter_bg_color = (0.15, 0.17, 0.18)
            self.gutter_text_color = (0.5, 0.5, 0.5)
            self.right_margin_color = (0.3, 0.3, 0.3)
            self.selection_color = (0.25, 0.35, 0.55)
            self.search_color = (0.5, 0.5, 0.2)
            self.current_search_color = (0.6, 0.5, 0.1)
            self.marked_color = (0.3, 0.3, 0.4)
            self.bracket_match_color = (0.2, 0.4, 0.2)
            
            # Dark theme syntax colors
            self.syntax_colors = {
                'keywords': (0.8, 0.5, 0.8),        # Light purple
                'builtins': (0.4, 0.8, 0.8),        # Light teal
                'string': (0.6, 0.9, 0.6),          # Light green
                'comment': (0.5, 0.5, 0.5),         # Gray
                'decorator': (1.0, 0.7, 0.4),       # Light orange
                'number': (0.7, 0.7, 1.0),          # Light blue
                'function': (0.7, 0.7, 1.0),        # Light blue
                'class': (0.6, 0.9, 1.0),           # Light cyan
                'preprocessor': (0.8, 0.8, 0.5),    # Light olive
                'types': (0.4, 0.8, 0.8),           # Light teal
                'tag': (0.7, 0.7, 1.0),             # Light blue
                'attribute': (0.8, 0.5, 0.8),       # Light purple
                'property': (0.8, 0.5, 0.8),        # Light purple
                'selector': (0.6, 0.9, 1.0),        # Light cyan
                'color': (0.6, 0.9, 0.6),           # Light green
                'entity': (1.0, 0.7, 0.4),          # Light orange
                'macro': (1.0, 0.7, 0.4),           # Light orange
            }
        else:
            # Light theme colors
            self.current_line_color = (0.95, 0.95, 0.85)
            self.gutter_bg_color = (0.95, 0.95, 0.95)
            self.gutter_text_color = (0.5, 0.5, 0.5)
            self.right_margin_color = (0.9, 0.9, 0.9)
            self.selection_color = (0.7, 0.8, 1.0)
            self.search_color = (1.0, 1.0, 0.7)
            self.current_search_color = (1.0, 0.9, 0.4)
            self.marked_color = (0.9, 0.9, 1.0)
            self.bracket_match_color = (0.8, 1.0, 0.8)
            
            # Light theme syntax colors
            self.syntax_colors = {
                'keywords': (0.53, 0.0, 0.53),      # Purple
                'builtins': (0.0, 0.5, 0.5),        # Teal
                'string': (0.0, 0.5, 0.0),          # Green
                'comment': (0.5, 0.5, 0.5),         # Gray
                'decorator': (0.7, 0.3, 0.0),       # Orange
                'number': (0.0, 0.0, 0.8),          # Blue
                'function': (0.0, 0.0, 0.8),        # Blue
                'class': (0.0, 0.4, 0.6),           # Dark cyan
                'preprocessor': (0.5, 0.5, 0.0),    # Olive
                'types': (0.0, 0.5, 0.5),           # Teal
                'tag': (0.0, 0.0, 0.8),             # Blue
                'attribute': (0.5, 0.0, 0.5),       # Purple
                'property': (0.5, 0.0, 0.5),        # Purple
                'selector': (0.0, 0.4, 0.6),        # Dark cyan
                'color': (0.0, 0.5, 0.0),           # Green
                'entity': (0.7, 0.3, 0.0),          # Orange
                'macro': (0.7, 0.3, 0.0),           # Orange
            }
        
        # Listen for theme changes
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect('notify::dark', lambda sm, p: self._on_theme_changed())
    
    def _on_theme_changed(self):
        """Handle theme changes"""
        self._init_colors()
        self.queue_draw()
    
    def set_font(self, family, size):
        """Set font family and size"""
        self.font_family = family
        self.font_size = size
        self.font_desc = Pango.FontDescription(f"{family} {size}")
        self.line_height = 0  # Force recalculation
        self.char_width = 0
        self.queue_draw()
    
    def _setup_event_controllers(self):
        """Set up event controllers for input"""
        # Click controller
        click = Gtk.GestureClick.new()
        click.connect('pressed', self._on_click_pressed)
        click.connect('released', self._on_click_released)
        self.add_controller(click)
        
        # Drag controller for selection
        drag = Gtk.GestureDrag.new()
        drag.connect('drag-begin', self._on_drag_begin)
        drag.connect('drag-update', self._on_drag_update)
        drag.connect('drag-end', self._on_drag_end)
        self.add_controller(drag)
        
        # Key controller
        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key)
        
        # Scroll controller
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | 
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll.connect('scroll', self._on_scroll)
        self.add_controller(scroll)
    
    def _calculate_metrics(self, cr):
        """Calculate font metrics"""
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        layout.set_text("M", -1)
        
        ink_rect, logical_rect = layout.get_pixel_extents()
        self.line_height = logical_rect.height + 2
        self.char_width = logical_rect.width
    
    def _on_buffer_changed(self, buffer):
        """Handle buffer changes"""
        self._needs_wrap_recalc = True
        self._wrapped_lines_cache.clear()
        self.queue_draw()
        self._update_parent_scrollbars()
    
    def _on_resize(self, widget, width, height):
        """Handle widget resize"""
        self._update_parent_scrollbars()
    
    def _on_draw(self, area, cr, width, height):
        """Draw the text view"""
        if self.line_height == 0:
            self._calculate_metrics(cr)
        
        # Draw background
        cr.set_source_rgb(*self.bg_color)
        cr.paint()
        
        # Draw gutter background
        if self.buffer.show_line_numbers:
            cr.set_source_rgb(*self.gutter_bg_color)
            cr.rectangle(0, 0, self.gutter_width, height)
            cr.fill()
        
        visible_lines = height // self.line_height + 2
        start_line = max(0, self.scroll_offset)
        end_line = min(self.buffer.get_line_count(), start_line + visible_lines)
        
        y_offset = -((self.scroll_offset - start_line) * self.line_height)
        
        # Draw right margin
        if self.buffer.show_right_margin:
            margin_x = self.gutter_width + (self.buffer.right_margin_position * self.char_width) - self.scroll_x
            if 0 <= margin_x <= width:
                cr.set_source_rgb(*self.right_margin_color)
                cr.set_line_width(1)
                cr.move_to(margin_x, 0)
                cr.line_to(margin_x, height)
                cr.stroke()
        
        # Draw current line highlight
        if self.buffer.highlight_current_line:
            cursor_y = y_offset + (self.buffer.cursor_line - start_line) * self.line_height
            if 0 <= cursor_y < height:
                cr.set_source_rgb(*self.current_line_color)
                cr.rectangle(0, cursor_y, width, self.line_height)
                cr.fill()
        
        # Create Pango layout
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        
        # Draw visible lines
        for line_num in range(start_line, end_line):
            if not self.buffer.is_line_visible(line_num):
                continue
            
            y = y_offset + (line_num - start_line) * self.line_height
            
            # Draw line number
            if self.buffer.show_line_numbers:
                # Check if line is folded
                is_folded = line_num in self.buffer.folded_lines
                fold_indicator = "▼" if is_folded else "▶" if self._can_fold_line(line_num) else " "
                
                line_num_text = f"{fold_indicator} {line_num + 1}"
                layout.set_text(line_num_text, -1)
                cr.set_source_rgb(*self.gutter_text_color)
                cr.move_to(5, y)
                PangoCairo.show_layout(cr, layout)
            
            # Get line text
            line_text = self.buffer.get_line(line_num)
            if not line_text:
                continue
            
            x_offset = self.gutter_width - self.scroll_x
            
            # Draw selection highlight
            if self.selection_start and self.selection_end:
                sel_start_line, sel_start_col = self.selection_start
                sel_end_line, sel_end_col = self.selection_end
                
                if sel_start_line <= line_num <= sel_end_line:
                    if sel_start_line == sel_end_line:
                        # Single line selection
                        if line_num == sel_start_line:
                            start_x = x_offset + sel_start_col * self.char_width
                            end_x = x_offset + sel_end_col * self.char_width
                            cr.set_source_rgb(*self.selection_color)
                            cr.rectangle(start_x, y, end_x - start_x, self.line_height)
                            cr.fill()
                    elif line_num == sel_start_line:
                        # First line of multi-line selection
                        start_x = x_offset + sel_start_col * self.char_width
                        end_x = x_offset + len(line_text) * self.char_width
                        cr.set_source_rgb(*self.selection_color)
                        cr.rectangle(start_x, y, end_x - start_x, self.line_height)
                        cr.fill()
                    elif line_num == sel_end_line:
                        # Last line of multi-line selection
                        end_x = x_offset + sel_end_col * self.char_width
                        cr.set_source_rgb(*self.selection_color)
                        cr.rectangle(x_offset, y, end_x - x_offset, self.line_height)
                        cr.fill()
                    else:
                        # Middle line of multi-line selection
                        end_x = x_offset + len(line_text) * self.char_width
                        cr.set_source_rgb(*self.selection_color)
                        cr.rectangle(x_offset, y, end_x - x_offset, self.line_height)
                        cr.fill()
            
            # Draw search highlights
            for match_line, match_start, match_end in self.buffer.search_matches:
                if match_line == line_num:
                    is_current = (line_num, match_start, match_end) == self.buffer.search_matches[self.buffer.current_match_index] if self.buffer.current_match_index >= 0 else False
                    color = self.current_search_color if is_current else self.search_color
                    start_x = x_offset + match_start * self.char_width
                    end_x = x_offset + match_end * self.char_width
                    cr.set_source_rgb(*color)
                    cr.rectangle(start_x, y, end_x - start_x, self.line_height)
                    cr.fill()
            
            # Draw marked occurrences
            for mark_line, mark_start, mark_end in self.buffer.marked_occurrences:
                if mark_line == line_num:
                    start_x = x_offset + mark_start * self.char_width
                    end_x = x_offset + mark_end * self.char_width
                    cr.set_source_rgb(*self.marked_color)
                    cr.rectangle(start_x, y, end_x - start_x, self.line_height)
                    cr.fill()
            
            # Draw bracket matching
            if line_num == self.buffer.cursor_line:
                match = self.buffer.find_matching_bracket(line_num, self.buffer.cursor_col)
                if match:
                    # Highlight cursor bracket
                    start_x = x_offset + self.buffer.cursor_col * self.char_width
                    cr.set_source_rgb(*self.bracket_match_color)
                    cr.rectangle(start_x, y, self.char_width, self.line_height)
                    cr.fill()
                    
                    # Highlight matching bracket
                    if match[0] == line_num:
                        match_x = x_offset + match[1] * self.char_width
                        cr.set_source_rgb(*self.bracket_match_color)
                        cr.rectangle(match_x, y, self.char_width, self.line_height)
                        cr.fill()
            
            # Draw text with syntax highlighting
            tokens = self.buffer.get_syntax_tokens(line_num)
            
            if tokens:
                # Draw text in segments with different colors
                last_pos = 0
                for start, end, token_type in tokens:
                    # Draw text before token
                    if start > last_pos:
                        segment = line_text[last_pos:start]
                        layout.set_text(segment, -1)
                        cr.set_source_rgb(*self.text_color)
                        cr.move_to(x_offset + last_pos * self.char_width, y)
                        PangoCairo.show_layout(cr, layout)
                    
                    # Draw token with color
                    segment = line_text[start:end]
                    layout.set_text(segment, -1)
                    color = self.syntax_colors.get(token_type, self.text_color)
                    cr.set_source_rgb(*color)
                    cr.move_to(x_offset + start * self.char_width, y)
                    PangoCairo.show_layout(cr, layout)
                    
                    last_pos = end
                
                # Draw remaining text
                if last_pos < len(line_text):
                    segment = line_text[last_pos:]
                    layout.set_text(segment, -1)
                    cr.set_source_rgb(*self.text_color)
                    cr.move_to(x_offset + last_pos * self.char_width, y)
                    PangoCairo.show_layout(cr, layout)
            else:
                # Draw entire line without syntax highlighting
                layout.set_text(line_text, -1)
                cr.set_source_rgb(*self.text_color)
                cr.move_to(x_offset, y)
                PangoCairo.show_layout(cr, layout)
        
        # Draw cursor
        cursor_y = y_offset + (self.buffer.cursor_line - start_line) * self.line_height
        if 0 <= cursor_y < height and self.buffer.is_line_visible(self.buffer.cursor_line):
            cursor_x = self.gutter_width + self.buffer.cursor_col * self.char_width - self.scroll_x
            cr.set_source_rgb(0, 0, 0)
            cr.set_line_width(2)
            cr.move_to(cursor_x, cursor_y)
            cr.line_to(cursor_x, cursor_y + self.line_height)
            cr.stroke()
        
        # Draw extra cursors
        for extra_line, extra_col in self.buffer.extra_cursors:
            cursor_y = y_offset + (extra_line - start_line) * self.line_height
            if 0 <= cursor_y < height and self.buffer.is_line_visible(extra_line):
                cursor_x = self.gutter_width + extra_col * self.char_width - self.scroll_x
                cr.set_source_rgb(0.5, 0.5, 0.5)
                cr.set_line_width(2)
                cr.move_to(cursor_x, cursor_y)
                cr.line_to(cursor_x, cursor_y + self.line_height)
                cr.stroke()
    
    def _can_fold_line(self, line_num):
        """Check if a line can be folded"""
        if line_num >= self.buffer.get_line_count() - 1:
            return False
        
        current_indent = self.buffer.get_indent_level(line_num)
        next_line = line_num + 1
        
        if next_line < self.buffer.get_line_count():
            next_indent = self.buffer.get_indent_level(next_line)
            return next_indent > current_indent
        
        return False
    
    def _on_click_pressed(self, gesture, n_press, x, y):
        """Handle mouse click"""
        self.grab_focus()
        
        # Check if click is in gutter (for folding)
        if x < self.gutter_width and self.buffer.show_line_numbers:
            line_num = int((y + self.scroll_offset * self.line_height) / self.line_height)
            if 0 <= line_num < self.buffer.get_line_count():
                if self._can_fold_line(line_num) or line_num in self.buffer.folded_lines:
                    self.buffer.toggle_fold(line_num)
                    self.queue_draw()
            return
        
        line_num = int((y + self.scroll_offset * self.line_height) / self.line_height)
        col = int((x - self.gutter_width + self.scroll_x) / self.char_width)
        
        if 0 <= line_num < self.buffer.get_line_count():
            line = self.buffer.get_line(line_num)
            col = max(0, min(col, len(line)))
            
            # Handle Ctrl+Click for multiple cursors
            state = gesture.get_current_event_state()
            if state & Gdk.ModifierType.CONTROL_MASK:
                self.buffer.extra_cursors.append((line_num, col))
            else:
                self.buffer.cursor_line = line_num
                self.buffer.cursor_col = col
                self.buffer.extra_cursors = []
            
            self.selection_start = None
            self.selection_end = None
            self.buffer.emit('cursor-moved', line_num, col)
            self.queue_draw()
    
    def _on_click_released(self, gesture, n_press, x, y):
        """Handle mouse release"""
        pass
    
    def _on_drag_begin(self, gesture, start_x, start_y):
        """Handle drag begin for selection"""
        self.drag_selecting = True
        
        line_num = int((start_y + self.scroll_offset * self.line_height) / self.line_height)
        col = int((start_x - self.gutter_width + self.scroll_x) / self.char_width)
        
        if 0 <= line_num < self.buffer.get_line_count():
            line = self.buffer.get_line(line_num)
            col = max(0, min(col, len(line)))
            self.selection_start = (line_num, col)
            self.selection_end = (line_num, col)
    
    def _on_drag_update(self, gesture, offset_x, offset_y):
        """Handle drag update for selection"""
        if not self.drag_selecting or not self.selection_start:
            return
        
        # GTK4 returns (success, x, y)
        success, start_x, start_y = gesture.get_start_point()
        if not success:
            return
        x = start_x + offset_x
        y = start_y + offset_y
        
        line_num = int((y + self.scroll_offset * self.line_height) / self.line_height)
        col = int((x - self.gutter_width + self.scroll_x) / self.char_width)
        
        if 0 <= line_num < self.buffer.get_line_count():
            line = self.buffer.get_line(line_num)
            col = max(0, min(col, len(line)))
            self.selection_end = (line_num, col)
            self.queue_draw()
    
    def _on_drag_end(self, gesture, offset_x, offset_y):
        """Handle drag end"""
        self.drag_selecting = False
        
        # Normalize selection
        if self.selection_start and self.selection_end:
            if (self.selection_start[0] > self.selection_end[0] or 
                (self.selection_start[0] == self.selection_end[0] and 
                 self.selection_start[1] > self.selection_end[1])):
                self.selection_start, self.selection_end = self.selection_end, self.selection_start
            
            # Update cursor to end of selection
            self.buffer.cursor_line = self.selection_end[0]
            self.buffer.cursor_col = self.selection_end[1]
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press"""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        alt = state & Gdk.ModifierType.ALT_MASK
        
        # Ctrl+Z - Undo
        if ctrl and keyval == Gdk.KEY_z and not shift:
            if self.buffer.undo():
                self.queue_draw()
            return True
        
        # Ctrl+Shift+Z or Ctrl+Y - Redo
        if (ctrl and keyval == Gdk.KEY_z and shift) or (ctrl and keyval == Gdk.KEY_y):
            if self.buffer.redo():
                self.queue_draw()
            return True
        
        # Ctrl+F - Find
        if ctrl and keyval == Gdk.KEY_f:
            # Trigger find dialog (handled by window)
            return False
        
        # F3 or Ctrl+G - Find next
        if keyval == Gdk.KEY_F3 or (ctrl and keyval == Gdk.KEY_g and not shift):
            match = self.buffer.find_next()
            if match:
                self.buffer.cursor_line = match[0]
                self.buffer.cursor_col = match[1]
                self._ensure_cursor_visible()
                self.queue_draw()
            return True
        
        # Shift+F3 or Ctrl+Shift+G - Find previous
        if (keyval == Gdk.KEY_F3 and shift) or (ctrl and keyval == Gdk.KEY_g and shift):
            match = self.buffer.find_previous()
            if match:
                self.buffer.cursor_line = match[0]
                self.buffer.cursor_col = match[1]
                self._ensure_cursor_visible()
                self.queue_draw()
            return True
        
        # Ctrl+/ or Ctrl+K - Comment/uncomment
        if ctrl and (keyval == Gdk.KEY_slash or keyval == Gdk.KEY_k):
            self.buffer.comment_line(self.buffer.cursor_line)
            return True
        
        # Ctrl+] - Indent
        if ctrl and keyval == Gdk.KEY_bracketright:
            self._indent_line()
            return True
        
        # Ctrl+[ - Unindent
        if ctrl and keyval == Gdk.KEY_bracketleft:
            self._unindent_line()
            return True
        
        # Arrow keys
        if keyval == Gdk.KEY_Up:
            self._move_cursor_up(shift)
            return True
        elif keyval == Gdk.KEY_Down:
            self._move_cursor_down(shift)
            return True
        elif keyval == Gdk.KEY_Left:
            self._move_cursor_left(shift)
            return True
        elif keyval == Gdk.KEY_Right:
            self._move_cursor_right(shift)
            return True
        
        # Home key
        if keyval == Gdk.KEY_Home:
            if self.buffer.smart_home_end:
                self._smart_home(shift)
            else:
                self._move_cursor_to_line_start(shift)
            return True
        
        # End key
        if keyval == Gdk.KEY_End:
            if self.buffer.smart_home_end:
                self._smart_end(shift)
            else:
                self._move_cursor_to_line_end(shift)
            return True
        
        # Page Up/Down
        if keyval == Gdk.KEY_Page_Up:
            self._page_up(shift)
            return True
        elif keyval == Gdk.KEY_Page_Down:
            self._page_down(shift)
            return True
        
        # Backspace
        if keyval == Gdk.KEY_BackSpace:
            if self.selection_start and self.selection_end:
                self._delete_selection()
            else:
                self._handle_backspace()
            return True
        
        # Delete
        if keyval == Gdk.KEY_Delete:
            if self.selection_start and self.selection_end:
                self._delete_selection()
            else:
                self._handle_delete()
            return True
        
        # Enter
        if keyval == Gdk.KEY_Return or keyval == Gdk.KEY_KP_Enter:
            if self.selection_start and self.selection_end:
                self._delete_selection()
            self._handle_enter()
            return True
        
        # Tab
        if keyval == Gdk.KEY_Tab:
            if shift:
                self._unindent_line()
            else:
                if self.selection_start and self.selection_end:
                    self._delete_selection()
                self._handle_tab()
            return True
        
        # Regular character input
        if not ctrl and not alt:
            char = chr(keyval) if 32 <= keyval < 127 else None
            if char:
                if self.selection_start and self.selection_end:
                    self._delete_selection()
                self.buffer.insert_text(self.buffer.cursor_line, self.buffer.cursor_col, char)
                self.buffer.cursor_col += 1
                self.queue_draw()
                return True
        
        return False
    
    def _move_cursor_up(self, select=False):
        """Move cursor up one line"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        if self.buffer.cursor_line > 0:
            self.buffer.cursor_line -= 1
            line = self.buffer.get_line(self.buffer.cursor_line)
            self.buffer.cursor_col = min(self.buffer.cursor_col, len(line))
            
            if select:
                self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
            else:
                self.selection_start = None
                self.selection_end = None
            
            self._ensure_cursor_visible()
            self.queue_draw()
    
    def _move_cursor_down(self, select=False):
        """Move cursor down one line"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        if self.buffer.cursor_line < self.buffer.get_line_count() - 1:
            self.buffer.cursor_line += 1
            line = self.buffer.get_line(self.buffer.cursor_line)
            self.buffer.cursor_col = min(self.buffer.cursor_col, len(line))
            
            if select:
                self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
            else:
                self.selection_start = None
                self.selection_end = None
            
            self._ensure_cursor_visible()
            self.queue_draw()
    
    def _move_cursor_left(self, select=False):
        """Move cursor left one character"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        if self.buffer.cursor_col > 0:
            self.buffer.cursor_col -= 1
        elif self.buffer.cursor_line > 0:
            self.buffer.cursor_line -= 1
            self.buffer.cursor_col = len(self.buffer.get_line(self.buffer.cursor_line))
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self._ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_right(self, select=False):
        """Move cursor right one character"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        line = self.buffer.get_line(self.buffer.cursor_line)
        if self.buffer.cursor_col < len(line):
            self.buffer.cursor_col += 1
        elif self.buffer.cursor_line < self.buffer.get_line_count() - 1:
            self.buffer.cursor_line += 1
            self.buffer.cursor_col = 0
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self._ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_to_line_start(self, select=False):
        """Move cursor to start of line"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        self.buffer.cursor_col = 0
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self.queue_draw()
    
    def _move_cursor_to_line_end(self, select=False):
        """Move cursor to end of line"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        line = self.buffer.get_line(self.buffer.cursor_line)
        self.buffer.cursor_col = len(line)
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self.queue_draw()
    
    def _smart_home(self, select=False):
        """Smart Home key behavior"""
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        line = self.buffer.get_line(self.buffer.cursor_line)
        first_non_space = len(line) - len(line.lstrip())
        
        if self.buffer.cursor_col == first_non_space or self.buffer.cursor_col == 0:
            # Toggle between start of line and first non-space
            self.buffer.cursor_col = 0 if self.buffer.cursor_col == first_non_space else first_non_space
        else:
            # Go to first non-space
            self.buffer.cursor_col = first_non_space
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self.queue_draw()
    
    def _smart_end(self, select=False):
        """Smart End key behavior"""
        self._move_cursor_to_line_end(select)
    
    def _page_up(self, select=False):
        """Page up"""
        height = self.get_height()
        lines_per_page = max(1, height // self.line_height)
        
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        self.buffer.cursor_line = max(0, self.buffer.cursor_line - lines_per_page)
        line = self.buffer.get_line(self.buffer.cursor_line)
        self.buffer.cursor_col = min(self.buffer.cursor_col, len(line))
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self._ensure_cursor_visible()
        self.queue_draw()
    
    def _page_down(self, select=False):
        """Page down"""
        height = self.get_height()
        lines_per_page = max(1, height // self.line_height)
        
        if select and not self.selection_start:
            self.selection_start = (self.buffer.cursor_line, self.buffer.cursor_col)
        
        self.buffer.cursor_line = min(self.buffer.get_line_count() - 1, 
                                      self.buffer.cursor_line + lines_per_page)
        line = self.buffer.get_line(self.buffer.cursor_line)
        self.buffer.cursor_col = min(self.buffer.cursor_col, len(line))
        
        if select:
            self.selection_end = (self.buffer.cursor_line, self.buffer.cursor_col)
        else:
            self.selection_start = None
            self.selection_end = None
        
        self._ensure_cursor_visible()
        self.queue_draw()
    
    def _handle_backspace(self):
        """Handle backspace key"""
        if self.buffer.cursor_col > 0:
            self.buffer.delete_range(
                self.buffer.cursor_line, self.buffer.cursor_col - 1,
                self.buffer.cursor_line, self.buffer.cursor_col
            )
            self.buffer.cursor_col -= 1
        elif self.buffer.cursor_line > 0:
            prev_line_len = len(self.buffer.get_line(self.buffer.cursor_line - 1))
            self.buffer.delete_range(
                self.buffer.cursor_line - 1, prev_line_len,
                self.buffer.cursor_line, 0
            )
            self.buffer.cursor_line -= 1
            self.buffer.cursor_col = prev_line_len
        
        self.queue_draw()
    
    def _handle_delete(self):
        """Handle delete key"""
        line = self.buffer.get_line(self.buffer.cursor_line)
        if self.buffer.cursor_col < len(line):
            self.buffer.delete_range(
                self.buffer.cursor_line, self.buffer.cursor_col,
                self.buffer.cursor_line, self.buffer.cursor_col + 1
            )
        elif self.buffer.cursor_line < self.buffer.get_line_count() - 1:
            self.buffer.delete_range(
                self.buffer.cursor_line, self.buffer.cursor_col,
                self.buffer.cursor_line + 1, 0
            )
        
        self.queue_draw()
    
    def _handle_enter(self):
        """Handle enter key"""
        indent_len = self.buffer.insert_line_break(self.buffer.cursor_line, self.buffer.cursor_col)
        self.buffer.cursor_line += 1
        self.buffer.cursor_col = indent_len
        self._ensure_cursor_visible()
        self.queue_draw()
    
    def _handle_tab(self):
        """Handle tab key"""
        if self.buffer.insert_spaces:
            spaces = ' ' * self.buffer.tab_width
            self.buffer.insert_text(self.buffer.cursor_line, self.buffer.cursor_col, spaces)
            self.buffer.cursor_col += self.buffer.tab_width
        else:
            self.buffer.insert_text(self.buffer.cursor_line, self.buffer.cursor_col, '\t')
            self.buffer.cursor_col += 1
        
        self.queue_draw()
    
    def _indent_line(self):
        """Indent current line"""
        indent = '\t' if not self.buffer.insert_spaces else ' ' * self.buffer.tab_width
        self.buffer.insert_text(self.buffer.cursor_line, 0, indent)
        self.buffer.cursor_col += len(indent)
        self.queue_draw()
    
    def _unindent_line(self):
        """Unindent current line"""
        line = self.buffer.get_line(self.buffer.cursor_line)
        if line.startswith('\t'):
            self.buffer.delete_range(self.buffer.cursor_line, 0, self.buffer.cursor_line, 1)
            self.buffer.cursor_col = max(0, self.buffer.cursor_col - 1)
        elif line.startswith(' ' * self.buffer.tab_width):
            self.buffer.delete_range(self.buffer.cursor_line, 0, 
                                    self.buffer.cursor_line, self.buffer.tab_width)
            self.buffer.cursor_col = max(0, self.buffer.cursor_col - self.buffer.tab_width)
        
        self.queue_draw()
    
    def _delete_selection(self):
        """Delete selected text"""
        if not self.selection_start or not self.selection_end:
            return
        
        start_line, start_col = self.selection_start
        end_line, end_col = self.selection_end
        
        # Ensure start is before end
        if (start_line > end_line or (start_line == end_line and start_col > end_col)):
            start_line, start_col, end_line, end_col = end_line, end_col, start_line, start_col
        
        self.buffer.delete_range(start_line, start_col, end_line, end_col)
        self.buffer.cursor_line = start_line
        self.buffer.cursor_col = start_col
        self.selection_start = None
        self.selection_end = None
        
        self.queue_draw()
    
    def _update_marked_occurrences(self):
        """Update marked occurrences based on word at cursor"""
        line = self.buffer.get_line(self.buffer.cursor_line)
        
        # Find word at cursor
        if self.buffer.cursor_col == 0 or self.buffer.cursor_col > len(line):
            self.buffer.mark_occurrences("")
            return
        
        # Find word boundaries
        start = self.buffer.cursor_col - 1
        while start > 0 and line[start - 1].isalnum():
            start -= 1
        
        end = self.buffer.cursor_col
        while end < len(line) and line[end].isalnum():
            end += 1
        
        if end > start:
            word = line[start:end]
            if len(word) >= 2:
                self.buffer.mark_occurrences(word)
            else:
                self.buffer.mark_occurrences("")
        else:
            self.buffer.mark_occurrences("")
    
    def _ensure_cursor_visible(self):
        """Ensure cursor is visible in viewport"""
        height = self.get_height()
        if self.line_height == 0:
            return
            
        visible_lines = height // self.line_height
        
        if self.buffer.cursor_line < self.scroll_offset:
            self.scroll_offset = self.buffer.cursor_line
        elif self.buffer.cursor_line >= self.scroll_offset + visible_lines:
            self.scroll_offset = self.buffer.cursor_line - visible_lines + 1
        
        # Update horizontal scroll
        cursor_x = self.buffer.cursor_col * self.char_width
        width = self.get_width() - self.gutter_width
        
        if cursor_x < self.scroll_x:
            self.scroll_x = max(0, cursor_x - 50)
        elif cursor_x > self.scroll_x + width - 50:
            self.scroll_x = cursor_x - width + 100
        
        # Update scrollbars
        self._update_parent_scrollbars()
    
    def _update_parent_scrollbars(self):
        """Helper to update parent window scrollbars"""
        parent = self.get_parent()
        if parent and hasattr(parent, 'get_parent'):
            window = parent.get_parent()
            if window and hasattr(window, '_update_adjustments'):
                GLib.idle_add(window._update_adjustments)
    
    def _on_scroll(self, controller, dx, dy):
        """Handle scroll events"""
        self.scroll_offset = max(0, int(self.scroll_offset + dy * 3))
        self._update_parent_scrollbars()
        self.queue_draw()
        return True
    
    def scroll_to_top(self):
        """Scroll to top of buffer"""
        self.scroll_offset = 0
        self.buffer.cursor_line = 0
        self.buffer.cursor_col = 0
        self._update_parent_scrollbars()
        self.queue_draw()
    
    def scroll_to_bottom(self):
        """Scroll to bottom of buffer"""
        self.buffer.cursor_line = self.buffer.get_line_count() - 1
        self.buffer.cursor_col = len(self.buffer.get_line(self.buffer.cursor_line))
        self._ensure_cursor_visible()
        self.queue_draw()
    
    def set_buffer(self, buffer):
        """Set a new buffer"""
        if self.buffer:
            self.buffer.disconnect_by_func(self._on_buffer_changed)
        
        self.buffer = buffer
        self.buffer.connect('changed', self._on_buffer_changed)
        self.scroll_offset = 0
        self.scroll_x = 0
        self.selection_start = None
        self.selection_end = None
        self._wrapped_lines_cache.clear()
        self.queue_draw()

# ============================================================================
# SEARCH DIALOG
# ============================================================================

class SearchDialog(Gtk.Window):
    """Search and replace dialog"""
    
    def __init__(self, parent, text_view):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Find and Replace")
        self.set_default_size(400, 200)
        
        self.text_view = text_view
        
        # Create UI
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        self.set_child(box)
        
        # Find entry
        find_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        find_label = Gtk.Label(label="Find:")
        find_label.set_size_request(80, -1)
        find_label.set_xalign(0)
        self.find_entry = Gtk.Entry()
        self.find_entry.set_hexpand(True)
        self.find_entry.connect('activate', self._on_find_next)
        find_box.append(find_label)
        find_box.append(self.find_entry)
        box.append(find_box)
        
        # Replace entry
        replace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        replace_label = Gtk.Label(label="Replace:")
        replace_label.set_size_request(80, -1)
        replace_label.set_xalign(0)
        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_hexpand(True)
        replace_box.append(replace_label)
        replace_box.append(self.replace_entry)
        box.append(replace_box)
        
        # Options
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.regex_check = Gtk.CheckButton(label="Regular Expression")
        self.case_check = Gtk.CheckButton(label="Case Sensitive")
        options_box.append(self.regex_check)
        options_box.append(self.case_check)
        box.append(options_box)
        
        # Buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_top(12)
        
        find_prev_btn = Gtk.Button(label="Find Previous")
        find_prev_btn.connect('clicked', self._on_find_previous)
        button_box.append(find_prev_btn)
        
        find_next_btn = Gtk.Button(label="Find Next")
        find_next_btn.connect('clicked', self._on_find_next)
        button_box.append(find_next_btn)
        
        replace_btn = Gtk.Button(label="Replace")
        replace_btn.connect('clicked', self._on_replace)
        button_box.append(replace_btn)
        
        replace_all_btn = Gtk.Button(label="Replace All")
        replace_all_btn.connect('clicked', self._on_replace_all)
        button_box.append(replace_all_btn)
        
        close_btn = Gtk.Button(label="Close")
        close_btn.connect('clicked', lambda w: self.close())
        button_box.append(close_btn)
        
        box.append(button_box)
        
        # Focus find entry
        self.find_entry.grab_focus()
    
    def _on_find_next(self, widget):
        """Find next occurrence"""
        text = self.find_entry.get_text()
        if not text:
            return
        
        regex = self.regex_check.get_active()
        case_sensitive = self.case_check.get_active()
        
        self.text_view.buffer.search(text, regex, case_sensitive)
        match = self.text_view.buffer.find_next()
        if match:
            self.text_view.buffer.cursor_line = match[0]
            self.text_view.buffer.cursor_col = match[1]
            self.text_view._ensure_cursor_visible()
            self.text_view.queue_draw()
    
    def _on_find_previous(self, widget):
        """Find previous occurrence"""
        text = self.find_entry.get_text()
        if not text:
            return
        
        regex = self.regex_check.get_active()
        case_sensitive = self.case_check.get_active()
        
        self.text_view.buffer.search(text, regex, case_sensitive)
        match = self.text_view.buffer.find_previous()
        if match:
            self.text_view.buffer.cursor_line = match[0]
            self.text_view.buffer.cursor_col = match[1]
            self.text_view._ensure_cursor_visible()
            self.text_view.queue_draw()
    
    def _on_replace(self, widget):
        """Replace current match"""
        find_text = self.find_entry.get_text()
        replace_text = self.replace_entry.get_text()
        
        if not find_text:
            return
        
        buffer = self.text_view.buffer
        line = buffer.get_line(buffer.cursor_line)
        
        # Check if current position matches
        if buffer.cursor_col + len(find_text) <= len(line):
            match_text = line[buffer.cursor_col:buffer.cursor_col + len(find_text)]
            if match_text == find_text or (not self.case_check.get_active() and 
                                          match_text.lower() == find_text.lower()):
                buffer.delete_range(buffer.cursor_line, buffer.cursor_col,
                                  buffer.cursor_line, buffer.cursor_col + len(find_text))
                buffer.insert_text(buffer.cursor_line, buffer.cursor_col, replace_text)
                self.text_view.queue_draw()
        
        # Find next
        self._on_find_next(widget)
    
    def _on_replace_all(self, widget):
        """Replace all occurrences"""
        find_text = self.find_entry.get_text()
        replace_text = self.replace_entry.get_text()
        
        if not find_text:
            return
        
        regex = self.regex_check.get_active()
        case_sensitive = self.case_check.get_active()
        
        buffer = self.text_view.buffer
        buffer.search(text=find_text, regex=regex, case_sensitive=case_sensitive)
        
        count = 0
        buffer.undo_manager.begin_group()
        
        # Replace from end to beginning to maintain positions
        for match in reversed(buffer.search_matches):
            line_num, start, end = match
            buffer.delete_range(line_num, start, line_num, end)
            buffer.insert_text(line_num, start, replace_text)
            count += 1
        
        buffer.undo_manager.end_group()
        self.text_view.queue_draw()
        
        print(f"Replaced {count} occurrences")

# ============================================================================
# SETTINGS DIALOG
# ============================================================================

class SettingsDialog(Gtk.Window):
    """Settings dialog for editor preferences"""
    
    def __init__(self, parent, buffer, text_view):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Editor Settings")
        self.set_default_size(450, 500)
        
        self.buffer = buffer
        self.text_view = text_view
        
        # Create UI
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        self.set_child(box)
        
        # Font settings
        font_group = self._create_group("Font")
        
        font_family_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_family_label = Gtk.Label(label="Font Family:")
        font_family_label.set_size_request(120, -1)
        font_family_label.set_xalign(0)
        
        # Font family dropdown
        font_list = Gtk.StringList()
        self.font_options = [
            "Monospace",
            "JetBrains Mono",
            "Fira Code",
            "Source Code Pro",
            "Inconsolata",
            "Ubuntu Mono",
            "DejaVu Sans Mono",
            "Liberation Mono",
            "Courier New"
        ]
        for font in self.font_options:
            font_list.append(font)
        
        self.font_family_combo = Gtk.DropDown()
        self.font_family_combo.set_model(font_list)
        try:
            current_index = self.font_options.index(text_view.font_family)
            self.font_family_combo.set_selected(current_index)
        except ValueError:
            self.font_family_combo.set_selected(0)
        
        font_family_box.append(font_family_label)
        font_family_box.append(self.font_family_combo)
        font_group.append(font_family_box)
        
        font_size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_size_label = Gtk.Label(label="Font Size:")
        font_size_label.set_size_request(120, -1)
        font_size_label.set_xalign(0)
        self.font_size_spin = Gtk.SpinButton()
        self.font_size_spin.set_range(6, 24)
        self.font_size_spin.set_increments(1, 1)
        self.font_size_spin.set_value(text_view.font_size)
        font_size_box.append(font_size_label)
        font_size_box.append(self.font_size_spin)
        font_group.append(font_size_box)
        
        box.append(font_group)
        
        # Tab settings
        tab_group = self._create_group("Indentation")
        
        tab_width_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab_width_label = Gtk.Label(label="Tab Width:")
        self.tab_width_spin = Gtk.SpinButton()
        self.tab_width_spin.set_range(2, 8)
        self.tab_width_spin.set_increments(1, 1)
        self.tab_width_spin.set_value(buffer.tab_width)
        tab_width_box.append(tab_width_label)
        tab_width_box.append(self.tab_width_spin)
        tab_group.append(tab_width_box)
        
        self.insert_spaces_check = Gtk.CheckButton(label="Insert Spaces Instead of Tabs")
        self.insert_spaces_check.set_active(buffer.insert_spaces)
        tab_group.append(self.insert_spaces_check)
        
        self.auto_indent_check = Gtk.CheckButton(label="Auto Indent")
        self.auto_indent_check.set_active(buffer.auto_indent)
        tab_group.append(self.auto_indent_check)
        
        box.append(tab_group)
        
        # Display settings
        display_group = self._create_group("Display")
        
        self.show_line_numbers_check = Gtk.CheckButton(label="Show Line Numbers")
        self.show_line_numbers_check.set_active(buffer.show_line_numbers)
        display_group.append(self.show_line_numbers_check)
        
        self.highlight_current_line_check = Gtk.CheckButton(label="Highlight Current Line")
        self.highlight_current_line_check.set_active(buffer.highlight_current_line)
        display_group.append(self.highlight_current_line_check)
        
        self.show_right_margin_check = Gtk.CheckButton(label="Show Right Margin")
        self.show_right_margin_check.set_active(buffer.show_right_margin)
        display_group.append(self.show_right_margin_check)
        
        margin_pos_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        margin_pos_label = Gtk.Label(label="Right Margin Position:")
        self.margin_pos_spin = Gtk.SpinButton()
        self.margin_pos_spin.set_range(40, 120)
        self.margin_pos_spin.set_increments(1, 10)
        self.margin_pos_spin.set_value(buffer.right_margin_position)
        margin_pos_box.append(margin_pos_label)
        margin_pos_box.append(self.margin_pos_spin)
        display_group.append(margin_pos_box)
        
        box.append(display_group)
        
        # Behavior settings
        behavior_group = self._create_group("Behavior")
        
        self.smart_home_end_check = Gtk.CheckButton(label="Smart Home/End Keys")
        self.smart_home_end_check.set_active(buffer.smart_home_end)
        behavior_group.append(self.smart_home_end_check)
        
        box.append(behavior_group)
        
        # Buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_top(12)
        
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect('clicked', self._on_apply)
        button_box.append(apply_btn)
        
        close_btn = Gtk.Button(label="Close")
        close_btn.connect('clicked', lambda w: self.close())
        button_box.append(close_btn)
        
        box.append(button_box)
    
    def _create_group(self, title):
        """Create a settings group with title"""
        frame = Gtk.Frame()
        frame.set_label(title)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        frame.set_child(box)
        
        return box
    
    def _on_apply(self, widget):
        """Apply settings"""
        # Font settings
        selected_font_index = self.font_family_combo.get_selected()
        if 0 <= selected_font_index < len(self.font_options):
            font_family = self.font_options[selected_font_index]
            font_size = int(self.font_size_spin.get_value())
            self.text_view.set_font(font_family, font_size)
        
        # Tab settings
        self.buffer.tab_width = int(self.tab_width_spin.get_value())
        self.buffer.insert_spaces = self.insert_spaces_check.get_active()
        self.buffer.auto_indent = self.auto_indent_check.get_active()
        
        # Display settings
        self.buffer.show_line_numbers = self.show_line_numbers_check.get_active()
        self.buffer.highlight_current_line = self.highlight_current_line_check.get_active()
        self.buffer.show_right_margin = self.show_right_margin_check.get_active()
        self.buffer.right_margin_position = int(self.margin_pos_spin.get_value())
        
        # Behavior settings
        self.buffer.smart_home_end = self.smart_home_end_check.get_active()
        
        self.buffer.emit('changed')

# ============================================================================
# MAIN WINDOW
# ============================================================================

class TextEditorWindow(Gtk.ApplicationWindow):
    """Main text editor window"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.set_title("Enhanced Text Editor")
        self.set_default_size(1000, 700)
        
        # Create main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(main_box)
        
        # Header bar
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        
        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        header.pack_end(menu_button)
        
        # Create menu
        menu = Gio.Menu()
        menu.append("Open", "app.open_file")
        menu.append("Save", "app.save_file")
        menu.append("Save As", "app.save_as")
        menu.append("Settings", "app.settings")
        menu_button.set_menu_model(menu)
        
        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)
        main_box.append(toolbar)
        
        find_btn = Gtk.Button(label="Find")
        find_btn.connect('clicked', self._on_find_clicked)
        toolbar.append(find_btn)
        
        comment_btn = Gtk.Button(label="Comment")
        comment_btn.connect('clicked', self._on_comment_clicked)
        toolbar.append(comment_btn)
        
        # Language selector
        lang_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lang_label = Gtk.Label(label="Language:")
        
        # Create string list for languages
        lang_list = Gtk.StringList()
        self.lang_options = [
            ("None", "none"),
            ("Python", "python"),
            ("JavaScript", "javascript"),
            ("C/C++", "c"),
            ("Rust", "rust"),
            ("HTML", "html"),
            ("CSS", "css")
        ]
        for display_name, _ in self.lang_options:
            lang_list.append(display_name)
        
        self.lang_combo = Gtk.DropDown()
        self.lang_combo.set_model(lang_list)
        self.lang_combo.set_selected(0)
        self.lang_combo.connect('notify::selected', self._on_language_changed)
        
        lang_box.append(lang_label)
        lang_box.append(self.lang_combo)
        toolbar.append(lang_box)
        
        # Text view in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        scrolled.set_has_frame(True)
        main_box.append(scrolled)
        
        self.text_view = VirtualTextView()
        scrolled.set_child(self.text_view)
        
        # Initialize scrollbar update flag
        self._updating_adjustments = False
        
        # Add vertical scrollbar adjustment
        self.v_adjustment = Gtk.Adjustment()
        self.v_adjustment.connect('value-changed', self._on_v_scroll)
        scrolled.set_vadjustment(self.v_adjustment)
        
        # Add horizontal scrollbar adjustment
        self.h_adjustment = Gtk.Adjustment()
        self.h_adjustment.connect('value-changed', self._on_h_scroll)
        scrolled.set_hadjustment(self.h_adjustment)
        
        # Update adjustments when buffer changes
        self.text_view.buffer.connect('changed', self._update_adjustments)
        
        # Status bar
        self.status_bar = Gtk.Label(label="Ready")
        self.status_bar.set_xalign(0)
        self.status_bar.set_margin_start(6)
        self.status_bar.set_margin_end(6)
        self.status_bar.set_margin_top(3)
        self.status_bar.set_margin_bottom(3)
        main_box.append(self.status_bar)
        
        # Connect signals
        self.text_view.buffer.connect('changed', self._on_buffer_changed)
    
    def _on_buffer_changed(self, buffer):
        """Handle buffer changes"""
        if buffer.file_path:
            filename = os.path.basename(buffer.file_path)
            modified = " [Modified]" if buffer.modified else ""
            self.set_title(f"{filename}{modified} - Enhanced Text Editor")
    
    def _update_adjustments(self, buffer=None):
        """Update scrollbar adjustments"""
        if not hasattr(self, 'v_adjustment') or not hasattr(self.text_view, 'line_height'):
            return
        
        if self.text_view.line_height == 0:
            return
        
        # Block signals to prevent recursion
        self._updating_adjustments = True
        
        # Vertical adjustment
        total_lines = self.text_view.buffer.get_line_count()
        visible_lines = max(1, self.text_view.get_height() // self.text_view.line_height)
        
        self.v_adjustment.set_lower(0)
        self.v_adjustment.set_upper(total_lines)
        self.v_adjustment.set_page_size(visible_lines)
        self.v_adjustment.set_step_increment(1)
        self.v_adjustment.set_page_increment(max(1, visible_lines - 1))
        self.v_adjustment.set_value(self.text_view.scroll_offset)
        
        # Horizontal adjustment
        if self.text_view.char_width > 0:
            max_line_length = 0
            for i in range(min(total_lines, 1000)):  # Sample first 1000 lines for performance
                line = self.text_view.buffer.get_line(i)
                max_line_length = max(max_line_length, len(line))
            
            visible_chars = max(1, (self.text_view.get_width() - self.text_view.gutter_width) // self.text_view.char_width)
            
            self.h_adjustment.set_lower(0)
            self.h_adjustment.set_upper(max_line_length * self.text_view.char_width)
            self.h_adjustment.set_page_size(visible_chars * self.text_view.char_width)
            self.h_adjustment.set_step_increment(self.text_view.char_width)
            self.h_adjustment.set_page_increment(max(self.text_view.char_width, visible_chars * self.text_view.char_width - self.text_view.char_width))
            self.h_adjustment.set_value(self.text_view.scroll_x)
        
        self._updating_adjustments = False
    
    def _on_v_scroll(self, adjustment):
        """Handle vertical scroll"""
        if getattr(self, '_updating_adjustments', False):
            return
        
        new_offset = int(adjustment.get_value())
        if new_offset != self.text_view.scroll_offset:
            self.text_view.scroll_offset = new_offset
            self.text_view.queue_draw()
    
    def _on_h_scroll(self, adjustment):
        """Handle horizontal scroll"""
        if getattr(self, '_updating_adjustments', False):
            return
        
        new_offset = int(adjustment.get_value())
        if new_offset != self.text_view.scroll_x:
            self.text_view.scroll_x = new_offset
            self.text_view.queue_draw()
    
    def _on_find_clicked(self, button):
        """Show find dialog"""
        dialog = SearchDialog(self, self.text_view)
        dialog.present()
    
    def _on_comment_clicked(self, button):
        """Toggle comment on current line"""
        self.text_view.buffer.comment_line(self.text_view.buffer.cursor_line)
    
    def _on_language_changed(self, dropdown, param):
        """Handle language selection change"""
        selected_index = dropdown.get_selected()
        if 0 <= selected_index < len(self.lang_options):
            _, lang_id = self.lang_options[selected_index]
            if lang_id == "none":
                self.text_view.buffer.set_language(None)
            else:
                self.text_view.buffer.set_language(lang_id)
    
    def open_file(self):
        """Open a file"""
        dialog = Gtk.FileDialog.new()
        dialog.open(self, None, self._on_open_file_finish)
    
    def _on_open_file_finish(self, dialog, result):
        """Handle file open result"""
        try:
            file = dialog.open_finish(result)
            if file:
                filepath = file.get_path()
                
                def load_file():
                    lines, encoding, detection_info = load_file_with_encoding(filepath)
                    GLib.idle_add(self._on_file_loaded, lines, filepath, encoding, detection_info)
                
                thread = threading.Thread(target=load_file)
                thread.daemon = True
                thread.start()
                
                self.status_bar.set_label(f"Loading {os.path.basename(filepath)}...")
        except Exception as e:
            print(f"Error opening file: {e}")
    
    def _on_file_loaded(self, lines, filepath, encoding, detection_info):
        """Handle file loaded"""
        buffer = VirtualTextBuffer()
        buffer.load_lines(lines)
        buffer.file_path = filepath
        buffer.encoding = encoding
        buffer.encoding_info = detection_info
        
        # Set language based on file extension
        _, ext = os.path.splitext(filepath)
        ext_lower = ext.lower()
        
        lang_index = 0  # Default to "None"
        if ext_lower == '.py':
            buffer.set_language('python')
            lang_index = 1  # Python
        elif ext_lower in ['.js', '.jsx']:
            buffer.set_language('javascript')
            lang_index = 2  # JavaScript
        elif ext_lower in ['.c', '.cpp', '.h', '.hpp']:
            buffer.set_language('c')
            lang_index = 3  # C/C++
        elif ext_lower == '.rs':
            buffer.set_language('rust')
            lang_index = 4  # Rust
        elif ext_lower in ['.html', '.htm']:
            buffer.set_language('html')
            lang_index = 5  # HTML
        elif ext_lower == '.css':
            buffer.set_language('css')
            lang_index = 6  # CSS
        else:
            buffer.set_language(None)
            lang_index = 0  # None
        
        self.lang_combo.set_selected(lang_index)        
        self.text_view.set_buffer(buffer)
        
        filename = os.path.basename(filepath)
        self.status_bar.set_label(f"Loaded {filename} - {len(lines):,} lines - {detection_info}")
        self.set_title(f"{filename} - Enhanced Text Editor")
        
        # Update scrollbars
        GLib.idle_add(self._update_adjustments)    
    def save_file(self):
        """Save the current file"""
        buffer = self.text_view.buffer
        if buffer.file_path:
            def save_in_thread():
                success = buffer.save_to_file(buffer.file_path, buffer.encoding)
                GLib.idle_add(self._on_file_saved, success, buffer.file_path)
            
            thread = threading.Thread(target=save_in_thread)
            thread.daemon = True
            thread.start()
            
            self.status_bar.set_label(f"Saving {os.path.basename(buffer.file_path)}...")
        else:
            self.save_file_as()
    
    def save_file_as(self):
        """Save the file with a new name"""
        dialog = Gtk.FileDialog.new()
        dialog.save(self, None, self._on_save_file_as_finish)
    
    def _on_save_file_as_finish(self, dialog, result):
        """Handle save as result"""
        try:
            file = dialog.save_finish(result)
            if file:
                filepath = file.get_path()
                buffer = self.text_view.buffer
                
                def save_in_thread():
                    success = buffer.save_to_file(filepath, buffer.encoding)
                    GLib.idle_add(self._on_file_saved, success, filepath)
                
                thread = threading.Thread(target=save_in_thread)
                thread.daemon = True
                thread.start()
                
                self.status_bar.set_label(f"Saving {os.path.basename(filepath)}...")
        except Exception as e:
            print(f"Error saving file: {e}")
    
    def _on_file_saved(self, success, filepath):
        """Handle file saved"""
        if success:
            filename = os.path.basename(filepath)
            self.status_bar.set_label(f"Saved {filename}")
            self.set_title(f"{filename} - Enhanced Text Editor")
        else:
            self.status_bar.set_label("Error saving file")
    
    def show_settings(self):
        """Show settings dialog"""
        dialog = SettingsDialog(self, self.text_view.buffer, self.text_view)
        dialog.present()

# ============================================================================
# APPLICATION
# ============================================================================

class TextEditorApp(Adw.Application):
    """Main application class"""
    
    def __init__(self):
        super().__init__(application_id="com.example.EnhancedTextEditor")
        self.connect('activate', self.on_activate)
        self._create_actions()
    
    def _create_actions(self):
        """Create application actions"""
        actions = [
            ('open_file', self.on_open_file),
            ('save_file', self.on_save_file),
            ('save_as', self.on_save_as),
            ('settings', self.on_settings),
        ]
        
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
    
    def on_activate(self, app):
        """Application activate signal handler"""
        self.window = TextEditorWindow(application=app)
        self.window.present()
    
    def on_open_file(self, action, param):
        """Open file action"""
        self.window.open_file()
    
    def on_save_file(self, action, param):
        """Save file action"""
        self.window.save_file()
    
    def on_save_as(self, action, param):
        """Save as action"""
        self.window.save_file_as()
    
    def on_settings(self, action, param):
        """Settings action"""
        self.window.show_settings()

def main():
    """Main entry point"""
    app = TextEditorApp()
    return app.run()

if __name__ == "__main__":
    main()
