#!/usr/bin/env python3
"""
Enhanced Virtual Text Buffer Editor with GtkSourceView-like features
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
import sys
from collections import deque
import chardet

# ==========================================================
# SYNTAX PATTERNS
# ==========================================================

class SyntaxPatterns:
    """Static regex definitions for all supported languages."""

    PYTHON = {
        'keywords': r'\b(False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b',
        'builtins': r'\b(abs|all|any|ascii|bin|bool|bytearray|bytes|callable|chr|classmethod|compile|complex|delattr|dict|dir|divmod|enumerate|eval|exec|filter|float|format|frozenset|getattr|globals|hasattr|hash|help|hex|id|input|int|isinstance|issubclass|iter|len|list|locals|map|max|memoryview|min|next|object|oct|open|ord|pow|print|property|range|repr|reversed|round|set|setattr|slice|sorted|staticmethod|str|sum|super|tuple|type|vars|zip|__import__)\b',
        'string': r'(""".*?"""|\'\'\'.*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
        'comment': r'#.*$',
        'decorator': r'@\w+',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?\b',
        'function': r'\bdef\s+(\w+)',
        'class': r'\bclass\s+(\w+)',
        'personal': r'\b(Adw|Gtk)\b'
    }

    JAVASCRIPT = {
        'keywords': r'\b(break|case|catch|class|const|continue|debugger|default|delete|do|else|export|extends|finally|for|function|if|import|in|instanceof|let|new|return|super|switch|this|throw|try|typeof|var|void|while|with|yield)\b',
        'builtins': r'\b(Array|Boolean|Date|Error|Function|JSON|Math|Number|Object|Promise|RegExp|String|Symbol|console|document|window)\b',
        'string': r'(`(?:[^`\\]|\\.)*`|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
        'comment': r'(//.*$|/\*[\s\S]*?\*/)',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?\b',
        'function': r'\bfunction\s+(\w+)',
        'class': r'\bclass\s+(\w+)'
    }

    C = {
        'keywords': r'\b(auto|break|case|char|const|continue|default|do|double|else|enum|extern|float|for|goto|if|inline|int|long|register|restrict|return|short|signed|sizeof|static|struct|switch|typedef|union|unsigned|void|volatile|while)\b',
        'preprocessor': r'#\s*(include|define|undef|ifdef|ifndef|if|else|elif|endif|pragma)',
        'string': r'"(?:[^"\\]|\\.)*"',
        'comment': r'(//.*$|/\*[\s\S]*?\*/)',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?[fFuUlL]?\b'
    }

    RUST = {
        'keywords': r'\b(as|async|await|break|const|continue|crate|dyn|else|enum|extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while)\b',
        'types': r'\b(i8|i16|i32|i64|i128|isize|u8|u16|u32|u64|u128|usize|f32|f64|bool|char|str|String|Vec|Box|Option|Result)\b',
        'string': r'(r#".*?"#|"(?:[^"\\]|\\.)*")',
        'comment': r'(//.*$|/\*[\s\S]*?\*/)',
        'number': r'\b\d+\.?\d*([eE][+-]?\d+)?\b',
        'macro': r'\b\w+!'
    }

    HTML = {
        'tag': r'</?[\w-]+>?',
        'attribute': r'\b[\w-]+=',
        'string': r'"[^"]*"|\'[^\']*\'',
        'comment': r'<!--[\s\S]*?-->',
        'entity': r'&\w+;'
    }

    CSS = {
        'selector': r'[.#]?[\w-]+(?=\s*\{)',
        'property': r'\b[\w-]+(?=:)',
        'string': r'"[^"]*"|\'[^\']*\'',
        'comment': r'/\*[\s\S]*?\*/',
        'number': r'\b\d+\.?\d*(px|em|rem|%|vh|vw)?\b',
        'color': r'#[0-9a-fA-F]{3,8}\b'
    }

    @classmethod
    def get(cls, lang):
        """Return dict of patterns for a given language."""
        if not lang:
            return {}
        lang = lang.lower()
        return {
            'python': cls.PYTHON,
            'javascript': cls.JAVASCRIPT,
            'c': cls.C,
            'rust': cls.RUST,
            'html': cls.HTML,
            'css': cls.CSS
        }.get(lang, {})

# ==========================================================
# SYNTAX ENGINE
# ==========================================================

class SyntaxEngine:
    """
    Performs syntax tokenization using regex patterns.
    Manages syntax cache.
    """

    TOKEN_ORDER = [
        'personal', 'comment', 'string', 'decorator', 'preprocessor',
        'keywords', 'builtins', 'types', 'function', 'class',
        'number', 'tag', 'attribute', 'property', 'selector',
        'color', 'entity', 'macro'
    ]

    def __init__(self):
        self.language = None
        self.patterns = {}
        self.cache = {}

    # -----------------------------
    # Language / pattern management
    # -----------------------------
    def set_language(self, lang):
        self.language = lang
        self.patterns = SyntaxPatterns.get(lang)
        self.cache.clear()

    # -----------------------------
    # Cache invalidation
    # -----------------------------
    def invalidate_from(self, start_line):
        for key in list(self.cache.keys()):
            if key >= start_line:
                del self.cache[key]

    # -----------------------------
    # Core tokenization
    # -----------------------------
    def tokenize(self, line_num, text):
        if not self.patterns:
            return []

        if line_num in self.cache:
            return self.cache[line_num]

        tokens = []
        covered = set()

        for name in self.TOKEN_ORDER:
            pattern = self.patterns.get(name)
            if not pattern:
                continue

            try:
                for match in re.finditer(pattern, text, re.MULTILINE):
                    start, end = match.span()

                    # avoid overlaps
                    if any(pos in covered for pos in range(start, end)):
                        continue

                    tokens.append((start, end, name))
                    covered.update(range(start, end))

            except re.error:
                continue

        tokens.sort(key=lambda t: t[0])
        self.cache[line_num] = tokens
        return tokens

# ==========================================================
# UNDO SYSTEM
# ==========================================================

class UndoAction:
    """
    Represents a single undoable edit.
    Types:
        insert   – new_content was inserted (old_content is previous line)
        delete   – content was removed (old_content is original line)
        replace  – line replaced with new content
    """

    def __init__(self, action_type, line_num,
                 old_content=None, new_content=None):
        self.action_type = action_type      # 'insert', 'delete', 'replace'
        self.line_num = line_num
        self.old_content = old_content
        self.new_content = new_content
        self.timestamp = time.time()        # For auto-group timing


class UndoManager:
    """
    Manages undo/redo stacks with grouping support.
    Behaviours:
        • Auto-groups rapid edits (typing)
        • Explicit begin/end group for multi-step edits
        • Clears redo stack on new edits
        • Bounded memory using deque(maxlen)
    """

    def __init__(self, max_undo=2000):
        self.undo_stack = deque(maxlen=max_undo)  # list[list[UndoAction]]
        self.redo_stack = deque(maxlen=max_undo)
        self.current_group = []
        self.grouping = False
        self.last_action_time = time.time()

    # ----------------------------------------------------
    # Grouping control
    # ----------------------------------------------------
    def begin_group(self):
        self.grouping = True
        self.current_group = []

    def end_group(self):
        if self.current_group:
            self.undo_stack.append(self.current_group)
            self.redo_stack.clear()
        self.grouping = False
        self.current_group = []

    # ----------------------------------------------------
    # Adding actions
    # ----------------------------------------------------
    def add_action(self, action):
        now = time.time()

        # Auto-group small rapid edits (typing)
        if not self.grouping and (now - self.last_action_time < 0.32):
            self.begin_group()

        if self.grouping:
            self.current_group.append(action)
        else:
            self.undo_stack.append([action])
            self.redo_stack.clear()

        self.last_action_time = now

    # ----------------------------------------------------
    # Undo
    # ----------------------------------------------------
    def undo(self):
        if self.grouping:
            self.end_group()

        if not self.undo_stack:
            return None

        actions = self.undo_stack.pop()
        self.redo_stack.append(actions)
        return actions

    # ----------------------------------------------------
    # Redo
    # ----------------------------------------------------
    def redo(self):
        if not self.redo_stack:
            return None

        actions = self.redo_stack.pop()
        self.undo_stack.append(actions)
        return actions

    # ----------------------------------------------------
    # Utility
    # ----------------------------------------------------
    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.current_group = []
        self.grouping = False

# ==========================================================
# FOLDING ENGINE
# ==========================================================

class FoldingEngine:
    """
    Handles code folding based on indentation rules.
    This engine is independent from the buffer except reading:
        • lines
        • indentation
        • line count
    """

    def __init__(self):
        # Set of line numbers where folding starts
        self.folded = set()

    # ------------------------------------------------------
    # Helpers
    # ------------------------------------------------------
    @staticmethod
    def get_indent(line, tab_width=4):
        """
        Returns indentation level in 'virtual columns'.
        Tabs count as tab_width columns.
        """
        count = 0
        for ch in line:
            if ch == ' ':
                count += 1
            elif ch == '\t':
                count += tab_width
            else:
                break
        return count

    # ------------------------------------------------------
    # Fold logic
    # ------------------------------------------------------
    def can_fold(self, lines, line_num, tab_width=4):
        """
        A line is foldable if the next visible line has deeper indentation.
        """
        if line_num < 0 or line_num >= len(lines) - 1:
            return False

        this_indent = self.get_indent(lines[line_num], tab_width)
        next_indent = self.get_indent(lines[line_num + 1], tab_width)

        return next_indent > this_indent

    def toggle(self, lines, line_num, tab_width=4):
        """
        Toggle folding on/off for a given line.
        If line becomes folded, its children get hidden.
        """
        if line_num in self.folded:
            self.folded.remove(line_num)
            return True

        if self.can_fold(lines, line_num, tab_width):
            self.folded.add(line_num)
            return True

        return False

    # ------------------------------------------------------
    # Visibility logic
    # ------------------------------------------------------
    def is_visible(self, lines, line_num, tab_width=4):
        """
        A line is invisible if any ancestor line above it is folded.
        """
        my_indent = self.get_indent(lines[line_num], tab_width)

        for i in range(line_num - 1, -1, -1):
            if i in self.folded:
                parent_indent = self.get_indent(lines[i], tab_width)
                if parent_indent < my_indent:
                    return False

            # Stop when indentation returns to same or less
            if lines[i].strip() and self.get_indent(lines[i], tab_width) <= my_indent:
                break

        return True

    # ------------------------------------------------------
    # Compute next visible line index (used by renderer/view)
    # ------------------------------------------------------
    def iter_visible(self, lines, tab_width=4):
        """
        Generator yielding only visible line numbers.
        This is used by the view to compute scroll ranges.
        """
        for i, line in enumerate(lines):
            if self.is_visible(lines, i, tab_width):
                yield i

    def count_visible(self, lines, tab_width=4):
        """Return number of visible lines."""
        return sum(1 for _ in self.iter_visible(lines, tab_width))

# ==========================================================
# SEARCH ENGINE
# ==========================================================

class SearchEngine:
    """
    Standalone search engine for large text buffers.
    It performs:
        • literal search
        • regex search
        • case-sensitive / insensitive search
        • find-next / find-previous
        • occurrence marking (highlighting identical words)

    This is completely independent from the text buffer.
    """

    def __init__(self):
        self.query = ""
        self.regex = False
        self.case_sensitive = False

        # Matches stored as tuples: (line, start_col, end_col)
        self.matches = []
        self.current_index = -1

        # For marking occurrences of a word
        self.mark_word = ""
        self.marked = []

    # ------------------------------------------------------
    # Core search
    # ------------------------------------------------------
    def search(self, lines, text, regex=False, case_sensitive=False,
               max_lines=10000, max_matches=2000):
        """
        Perform a global search over lines.

        Parameters:
            lines          list[str]
            text           string or regex
            regex          bool
            case_sensitive bool
            max_lines      limit scanned lines
            max_matches    bound memory usage
        """

        self.query = text
        self.regex = regex
        self.case_sensitive = case_sensitive
        self.matches = []
        self.current_index = -1

        if not text:
            return

        # Limit scanning
        limit = min(len(lines), max_lines)

        # Case handling
        if not case_sensitive and not regex:
            text_low = text.lower()

        # Regex compile
        pattern = None
        if regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(text, flags)
            except re.error:
                return

        # Iterate lines
        for ln in range(limit):
            line = lines[ln]

            if regex:
                try:
                    for m in pattern.finditer(line):
                        self.matches.append((ln, m.start(), m.end()))
                        if len(self.matches) >= max_matches:
                            return
                except Exception:
                    continue

            else:
                # plain substring search
                src = line if case_sensitive else line.lower()
                start = 0
                while True:
                    pos = src.find(text if case_sensitive else text_low, start)
                    if pos < 0:
                        break
                    self.matches.append((ln, pos, pos + len(text)))
                    start = pos + 1
                    if len(self.matches) >= max_matches:
                        return

    # ------------------------------------------------------
    # Navigation
    # ------------------------------------------------------
    def find_next(self, cursor_line, cursor_col):
        """
        Find next match after the cursor.
        cursor_line, cursor_col are used to determine wrap-around.
        """
        if not self.matches:
            return None

        for i, (ln, s, e) in enumerate(self.matches):
            if ln > cursor_line or (ln == cursor_line and s > cursor_col):
                self.current_index = i
                return (ln, s, e)

        # wrap
        self.current_index = 0
        return self.matches[0]

    def find_previous(self, cursor_line, cursor_col):
        """
        Find previous match before cursor.
        """
        if not self.matches:
            return None

        for i in range(len(self.matches) - 1, -1, -1):
            ln, s, e = self.matches[i]
            if ln < cursor_line or (ln == cursor_line and s < cursor_col):
                self.current_index = i
                return (ln, s, e)

        # wrap to end
        self.current_index = len(self.matches) - 1
        return self.matches[-1]

    # ------------------------------------------------------
    # Mark occurrences (same word)
    # ------------------------------------------------------
    def mark_occurrences(self, lines, word):
        """
        Mark all occurrences of a word (not regex).
        Used for caret-word highlighting.
        """
        self.mark_word = word
        self.marked = []

        if not word or len(word) < 2:
            return

        wlen = len(word)
        for ln, line in enumerate(lines):
            start = 0
            while True:
                pos = line.find(word, start)
                if pos < 0:
                    break
                self.marked.append((ln, pos, pos + wlen))
                start = pos + 1

################################################################################
# ==========================================================
# VIRTUAL TEXT BUFFER
# ==========================================================

class VirtualTextBuffer(GObject.Object):
    """
    The core document model.
    Stores:
        - lines
        - cursor position
        - file metadata
        - undo/redo history

    Does NOT handle:
        - syntax
        - search
        - folding
        - rendering

    Emits:
        'changed'       — when text changes
        'cursor-moved'  — when cursor moves
    """

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'cursor-moved': (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()

        # Document
        self.lines = [""]
        self.modified = False

        # Cursor
        self.cursor_line = 0
        self.cursor_col = 0

        # File metadata
        self.file_path = None
        self.encoding = "utf-8"
        self.encoding_info = "UTF-8"

        # Settings
        self.tab_width = 4
        self.insert_spaces = True
        self.auto_indent = True
        self.smart_home_end = True

        # Undo system
        self.undo = UndoManager()

    # ------------------------------------------------------
    # Basic line operations
    # ------------------------------------------------------
    def get_line(self, n):
        if 0 <= n < len(self.lines):
            return self.lines[n]
        return ""

    def line_count(self):
        return len(self.lines)

    # ------------------------------------------------------
    # Loading
    # ------------------------------------------------------
    def load_lines(self, lines):
        self.lines = lines if lines else [""]
        self.cursor_line = 0
        self.cursor_col = 0
        self.modified = False

        self.undo.clear()
        self.emit('changed')

    # ------------------------------------------------------
    # Insertion
    # ------------------------------------------------------
    def insert_text(self, line, col, text):
        """
        Insert text at (line,col).
        """
        if not (0 <= line < len(self.lines)):
            return

        old = self.lines[line]
        new = old[:col] + text + old[col:]

        self.undo.add_action(UndoAction(
            'replace', line, old_content=old, new_content=new
        ))

        self.lines[line] = new
        self.modified = True
        self.emit('changed')

    # ------------------------------------------------------
    # Deletion: ranges
    # ------------------------------------------------------
    def delete_range(self, sl, sc, el, ec):
        """
        Delete range from (sl,sc) to (el,ec).
        Handles both single and multi-line.
        """

        # Normalize
        if (el < sl) or (el == sl and ec < sc):
            sl, sc, el, ec = el, ec, sl, sc

        # Single-line delete
        if sl == el:
            old = self.lines[sl]
            new = old[:sc] + old[ec:]

            self.undo.add_action(UndoAction(
                'replace', sl, old_content=old, new_content=new
            ))

            self.lines[sl] = new
            self.modified = True
            self.emit('changed')
            return

        # Multi-line delete
        self.undo.begin_group()

        first = self.lines[sl][:sc]
        last = self.lines[el][ec:]

        # Save old lines
        for i in range(sl, el + 1):
            self.undo.add_action(UndoAction(
                'delete', i, old_content=self.lines[i], new_content=None
            ))

        # Remove them
        del self.lines[sl:el + 1]

        merged = first + last
        self.lines.insert(sl, merged)

        self.undo.add_action(UndoAction(
            'insert', sl, old_content=None, new_content=merged
        ))
        self.undo.end_group()

        self.modified = True
        self.emit('changed')

    # ------------------------------------------------------
    # Line break insertion
    # ------------------------------------------------------
    def _indent_of(self, text):
        """Return indentation string."""
        indent = ""
        for ch in text:
            if ch in " \t":
                indent += ch
            else:
                break
        return indent

    def insert_line_break(self, line, col):
        """
        Insert newline with auto-indent.
        Returns length of new indent for cursor placement.
        """

        if not (0 <= line < len(self.lines)):
            return 0

        old = self.lines[line]
        left = old[:col]
        right = old[col:]

        indent = ""
        if self.auto_indent:
            indent = self._indent_of(left)

            # Python-like: increase indent if line ends with colon
            if left.rstrip().endswith(":"):
                indent += ("\t" if not self.insert_spaces
                           else " " * self.tab_width)

        self.undo.begin_group()

        # Replace old line
        self.undo.add_action(UndoAction(
            'replace', line, old_content=old, new_content=left
        ))
        self.lines[line] = left

        # Insert new line
        new_text = indent + right
        self.undo.add_action(UndoAction(
            'insert', line + 1, old_content=None, new_content=new_text
        ))
        self.lines.insert(line + 1, new_text)

        self.undo.end_group()

        self.modified = True
        self.emit('changed')
        return len(indent)

    # ------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------
    def apply_undo_actions(self, actions, reversing=False):
        """
        Apply a batch of undo or redo actions.
        """
        for act in reversed(actions) if not reversing else actions:
            if act.action_type in ("insert", "delete", "replace"):
                if act.old_content is not None and not reversing:
                    # undo → restore old
                    num = act.line_num
                    if num < len(self.lines):
                        self.lines[num] = act.old_content
                    else:
                        self.lines.insert(num, act.old_content)

                elif act.new_content is not None and reversing:
                    # redo → apply new
                    num = act.line_num
                    if num < len(self.lines):
                        self.lines[num] = act.new_content
                    else:
                        self.lines.insert(num, act.new_content)

    def undo_last(self):
        actions = self.undo.undo()
        if not actions:
            return False
        self.apply_undo_actions(actions, reversing=False)
        self.modified = True
        self.emit('changed')
        return True

    def redo_last(self):
        actions = self.undo.redo()
        if not actions:
            return False
        self.apply_undo_actions(actions, reversing=True)
        self.modified = True
        self.emit('changed')
        return True

    # ------------------------------------------------------
    # Cursor movement
    # ------------------------------------------------------
    def set_cursor(self, line, col):
        line = max(0, min(line, len(self.lines) - 1))
        col = max(0, min(col, len(self.lines[line])))

        if line != self.cursor_line or col != self.cursor_col:
            self.cursor_line = line
            self.cursor_col = col
            self.emit('cursor-moved', line, col)

# ==========================================================
# VIRTUAL TEXT RENDERER
# ==========================================================

class VirtualTextRenderer:
    """
    Responsible for ALL drawing operations:
        - gutter (line numbers + folding arrows)
        - background
        - current line highlight
        - syntax-highlighted text
        - selections
        - search results + current match
        - marked occurrences
        - bracket matching
        - cursor + multiple cursors
        - right margin
    """

    def __init__(self):
        # Metrics
        self.font_family = "Monospace"
        self.font_size = 11
        self.font_desc = Pango.FontDescription(f"{self.font_family} {self.font_size}")
        self.line_height = 0
        self.char_width = 0

        # Layout reusable
        self._layout = None

        # Gutter
        self.gutter_width = 50

        # Theme-dependent colors
        self._init_theme()

    # ------------------------------------------------------
    # Theme handling
    # ------------------------------------------------------
    def _init_theme(self):
        style = Adw.StyleManager.get_default()
        dark = style.get_dark()

        if dark:
            self.bg = (0.18, 0.20, 0.21)
            self.text = (0.92, 0.92, 0.92)
            self.current_line_bg = (0.25, 0.27, 0.28)
            self.gutter_bg = (0.15, 0.17, 0.18)
            self.gutter_text = (0.55, 0.55, 0.55)
            self.selection_bg = (0.25, 0.35, 0.55)
            self.search_bg = (0.80, 0.80, 0.45)
            self.search_current_bg = (0.95, 0.95, 0.20)
            self.marked_bg = (0.50, 0.50, 0.65)
            self.bracket_bg = (0.20, 0.40, 0.20)
        else:
            self.bg = (1.0, 1.0, 1.0)
            self.text = (0.0, 0.0, 0.0)
            self.current_line_bg = (0.95, 0.95, 0.85)
            self.gutter_bg = (0.95, 0.95, 0.95)
            self.gutter_text = (0.40, 0.40, 0.40)
            self.selection_bg = (0.70, 0.80, 1.0)
            self.search_bg = (1.0, 1.0, 0.75)
            self.search_current_bg = (1.0, 0.9, 0.2)
            self.marked_bg = (0.85, 0.85, 1.0)
            self.bracket_bg = (0.80, 1.00, 0.80)

        # Syntax colors (theme-aware)
        self.syntax_colors = {
            'keywords': (0.75, 0.20, 0.75),
            'builtins': (0.00, 0.50, 0.50),
            'string': (0.00, 0.60, 0.00),
            'comment': (0.50, 0.50, 0.50),
            'decorator': (0.85, 0.45, 0.00),
            'number': (0.10, 0.10, 0.75),
            'function': (0.10, 0.10, 0.75),
            'class': (0.00, 0.30, 0.60),
            'types': (0.00, 0.50, 0.50),
            'tag': (0.10, 0.10, 0.75),
            'attribute': (0.60, 0.00, 0.60),
            'property': (0.60, 0.00, 0.60),
            'selector': (0.00, 0.30, 0.60),
            'color': (0.00, 0.60, 0.00),
            'entity': (0.80, 0.45, 0.00),
            'macro': (0.80, 0.45, 0.00),
            'personal': (0.60, 0.90, 1.0),
        }

        # React to theme changes
        style.connect("notify::dark", lambda *a: self._init_theme())

    # ------------------------------------------------------
    # Font metrics
    # ------------------------------------------------------
    def ensure_metrics(self, cr):
        if self.line_height > 0 and self.char_width > 0:
            return

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        layout.set_text("M", -1)

        ink, logical = layout.get_pixel_extents()
        self.char_width = logical.width
        self.line_height = max(logical.height, 13) + 2

        self._layout = layout

    # ------------------------------------------------------
    # Main draw entry point
    # ------------------------------------------------------
    def draw(self, cr, allocation,
             buffer, syntax, folding, search,
             selection_start, selection_end,
             extra_cursors,
             scroll_line, scroll_x,
             show_line_numbers=True,
             show_right_margin=True,
             right_margin_pos=80,
             highlight_current_line=True):
        """
        Central draw method invoked by VirtualTextView.
        """

        width = allocation.width
        height = allocation.height

        # Update metrics
        self.ensure_metrics(cr)

        # Background
        cr.set_source_rgb(*self.bg)
        cr.paint()

        # Gutter background
        if show_line_numbers:
            cr.set_source_rgb(*self.gutter_bg)
            cr.rectangle(0, 0, self.gutter_width, height)
            cr.fill()

        # Visible lines
        lines = buffer.lines
        total = len(lines)

        lines_visible = (height // self.line_height) + 2
        start_line = max(0, scroll_line)
        end_line = min(total, start_line + lines_visible)

        # Compute Y offset
        y0 = -((scroll_line - start_line) * self.line_height)

        # Right margin
        if show_right_margin:
            margin_x = self.gutter_width + right_margin_pos * self.char_width - scroll_x
            if 0 <= margin_x <= width:
                cr.set_source_rgb(0.80, 0.80, 0.80)
                cr.set_line_width(1)
                cr.move_to(margin_x, 0)
                cr.line_to(margin_x, height)
                cr.stroke()

        # Current line highlight
        if highlight_current_line:
            cy = buffer.cursor_line
            if start_line <= cy < end_line:
                y = y0 + (cy - start_line) * self.line_height
                cr.set_source_rgb(*self.current_line_bg)
                cr.rectangle(0, y, width, self.line_height)
                cr.fill()

        # Iterate through visible lines
        for ln in range(start_line, end_line):
            if not folding.is_visible(lines, ln, buffer.tab_width):
                continue

            y = y0 + (ln - start_line) * self.line_height

            self._draw_line_number(cr, ln, y, show_line_numbers, folding, lines, buffer.tab_width)
            self._draw_line_contents(cr, ln, lines[ln], y,
                                     buffer, syntax, search,
                                     selection_start, selection_end,
                                     scroll_x)

        # Cursor
        self._draw_cursor(cr, buffer, scroll_x, start_line, y0)

        # Extra cursors
        for (ln, col) in extra_cursors:
            self._draw_cursor(cr, buffer, scroll_x, start_line, y0,
                              line_override=ln, col_override=col, dimmed=True)

    # ------------------------------------------------------
    # Draw line number + folding arrow
    # ------------------------------------------------------
    def _draw_line_number(self, cr, ln, y, show_line_numbers, folding, lines, tab_width):
        if not show_line_numbers:
            return

        # Fold indicators
        arrow = ""
        if ln in folding.folded:
            arrow = "▼"
        elif folding.can_fold(lines, ln, tab_width):
            arrow = "▶"

        txt = f"{arrow} {ln+1}"

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        layout.set_text(txt, -1)

        cr.set_source_rgb(*self.gutter_text)
        cr.move_to(5, y)
        PangoCairo.show_layout(cr, layout)

    # ------------------------------------------------------
    # Draw a single line’s contents
    # ------------------------------------------------------
    def _draw_line_contents(self, cr, ln, text, y,
                            buffer, syntax, search,
                            sel_start, sel_end,
                            scroll_x):
        x0 = self.gutter_width - scroll_x

        # 1) Selection background
        if sel_start and sel_end:
            sline, scol = sel_start
            eline, ecol = sel_end
            if sline <= ln <= eline:
                self._draw_selection(cr, text, ln, y, x0, sline, scol, eline, ecol)

        # 2) Search matches
        self._draw_search_matches(cr, ln, y, x0, text, search)

        # 3) Marked occurrences
        for (m_ln, s, e) in search.marked:
            if m_ln == ln:
                x1 = x0 + s * self.char_width
                x2 = x0 + e * self.char_width
                cr.set_source_rgb(*self.marked_bg)
                cr.rectangle(x1, y, x2 - x1, self.line_height)
                cr.fill()

        # 4) Bracket highlight
        self._draw_bracket_match(cr, ln, y, x0, text, buffer)

        # 5) Syntax-highlighted text
        tokens = syntax.tokenize(ln, text)
        if not tokens:
            # Plain text
            self._draw_text_segment(cr, text, self.text, x0, y)
            return

        # Draw segments
        pos = 0
        for start, end, kind in tokens:
            if start > pos:
                segment = text[pos:start]
                self._draw_text_segment(cr, segment, self.text,
                                        x0 + pos * self.char_width, y)

            segment = text[start:end]
            color = self.syntax_colors.get(kind, self.text)
            self._draw_text_segment(cr, segment, color,
                                    x0 + start * self.char_width, y)
            pos = end

        # Remainder
        if pos < len(text):
            self._draw_text_segment(cr, text[pos:], self.text,
                                    x0 + pos * self.char_width, y)

    # ------------------------------------------------------
    # Draw selection highlight
    # ------------------------------------------------------
    def _draw_selection(self, cr, text, ln, y, x0,
                        sline, scol, eline, ecol):
        cr.set_source_rgb(*self.selection_bg)

        # Single line
        if sline == eline == ln:
            x1 = x0 + scol * self.char_width
            x2 = x0 + ecol * self.char_width
            cr.rectangle(x1, y, x2 - x1, self.line_height)
            cr.fill()
            return

        # First line
        if ln == sline:
            x1 = x0 + scol * self.char_width
            x2 = x0 + len(text) * self.char_width
            cr.rectangle(x1, y, x2 - x1, self.line_height)
            cr.fill()
            return

        # Last line
        if ln == eline:
            x1 = x0
            x2 = x0 + ecol * self.char_width
            cr.rectangle(x1, y, x2 - x1, self.line_height)
            cr.fill()
            return

        # Middle lines
        x1 = x0
        x2 = x0 + len(text) * self.char_width
        cr.rectangle(x1, y, x2 - x1, self.line_height)
        cr.fill()

    # ------------------------------------------------------
    # Draw search matches
    # ------------------------------------------------------
    def _draw_search_matches(self, cr, ln, y, x0, text, search):
        for idx, (m_ln, s, e) in enumerate(search.matches):
            if m_ln != ln:
                continue

            if search.current_index == idx:
                col = self.search_current_bg
            else:
                col = self.search_bg

            x1 = x0 + s * self.char_width
            x2 = x0 + e * self.char_width

            cr.set_source_rgb(*col)
            cr.rectangle(x1, y, x2 - x1, self.line_height)
            cr.fill()

    # ------------------------------------------------------
    # Draw matching bracket
    # ------------------------------------------------------
    def _draw_bracket_match(self, cr, ln, y, x0, text, buffer):
        cl = buffer.cursor_line
        cc = buffer.cursor_col

        if ln != cl or cc >= len(text):
            return

        c = text[cc]
        pairs = {'(': ')', '[': ']', '{': '}',
                 ')': '(', ']': '[', '}': '{'}

        if c not in pairs:
            return

        target = pairs[c]

        # Search direction
        forward = c in "([{"
        depth = 1
        line_iter = range(ln, len(buffer.lines)) if forward else range(ln, -1, -1)

        for i in line_iter:
            line = buffer.lines[i]

            cols = range(cc + (1 if i == ln else 0), len(line)) if forward else \
                   range(cc - 1 if i == ln else len(line) - 1, -1, -1)

            for j in cols:
                ch = line[j]
                if ch == c:
                    depth += 1
                elif ch == target:
                    depth -= 1
                    if depth == 0:
                        # Highlight both brackets
                        cr.set_source_rgb(*self.bracket_bg)
                        cr.rectangle(x0 + cc * self.char_width, y, self.char_width, self.line_height)
                        cr.fill()

                        y2 = y + (i - ln) * self.line_height
                        cr.rectangle(x0 + j * self.char_width, y2, self.char_width, self.line_height)
                        cr.fill()
                        return

    # ------------------------------------------------------
    # Draw individual text segment
    # ------------------------------------------------------
    def _draw_text_segment(self, cr, text, color, x, y):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        layout.set_text(text, -1)

        cr.set_source_rgb(*color)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    # ------------------------------------------------------
    # Draw cursor
    # ------------------------------------------------------
    def _draw_cursor(self, cr, buffer, scroll_x, start_line, y0,
                      line_override=None, col_override=None, dimmed=False):
        ln = buffer.cursor_line if line_override is None else line_override
        col = buffer.cursor_col if col_override is None else col_override

        if ln < start_line:
            return

        x0 = self.gutter_width - scroll_x
        y = y0 + (ln - start_line) * self.line_height

        cr.set_source_rgb(0.0, 0.0, 0.0 if not dimmed else 0.4)
        cr.set_line_width(2)
        cr.move_to(x0 + col * self.char_width, y)
        cr.line_to(x0 + col * self.char_width, y + self.line_height)
        cr.stroke()

# ==========================================================
# INPUT CONTROLLER
# ==========================================================

class InputController:
    """
    Handles ALL keyboard and mouse input logic.
    The View forwards events to this controller.

    Responsibilities:
        - cursor movement
        - selection logic
        - text insertion/deletion
        - indentation
        - home/end smart logic
        - double-click word selection
        - mouse drag selection
    """

    def __init__(self, view, buffer):
        self.view = view
        self.buffer = buffer

        # Selection
        self.selection_start = None   # (line, col)
        self.selection_end = None     # (line, col)

        # Mouse
        self.dragging = False
        self.drag_anchor = None

        # Word-selection
        self.last_click_time = 0
        self.double_click_threshold = 0.28   # seconds

    # ------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------
    def clear_selection(self):
        self.selection_start = None
        self.selection_end = None

    def has_selection(self):
        return self.selection_start is not None and self.selection_end is not None

    def set_selection(self, sl, sc, el, ec):
        self.selection_start = (sl, sc)
        self.selection_end = (el, ec)

    def add_selection_point(self, line, col):
        if not self.selection_start:
            self.selection_start = (line, col)
        self.selection_end = (line, col)

    def delete_selection(self):
        if not self.has_selection():
            return False

        sl, sc = self.selection_start
        el, ec = self.selection_end

        if (el < sl) or (el == sl and ec < sc):
            sl, sc, el, ec = el, ec, sl, sc

        self.buffer.delete_range(sl, sc, el, ec)
        self.buffer.set_cursor(sl, sc)

        self.clear_selection()
        return True

    # ------------------------------------------------------
    # Text insertion
    # ------------------------------------------------------
    def insert_text(self, text):
        if self.has_selection():
            self.delete_selection()

        ln, col = self.buffer.cursor_line, self.buffer.cursor_col
        self.buffer.insert_text(ln, col, text)
        self.buffer.set_cursor(ln, col + len(text))

    # ------------------------------------------------------
    # Enter / New line
    # ------------------------------------------------------
    def insert_newline(self):
        if self.has_selection():
            self.delete_selection()

        ln = self.buffer.cursor_line
        col = self.buffer.cursor_col

        indent_len = self.buffer.insert_line_break(ln, col)
        self.buffer.set_cursor(ln + 1, indent_len)

    # ------------------------------------------------------
    # Backspace
    # ------------------------------------------------------
    def backspace(self):
        if self.has_selection():
            self.delete_selection()
            return

        ln = self.buffer.cursor_line
        col = self.buffer.cursor_col

        if col > 0:
            self.buffer.delete_range(ln, col - 1, ln, col)
            self.buffer.set_cursor(ln, col - 1)
            return

        # Join with previous line
        if ln > 0:
            prev_len = len(self.buffer.lines[ln - 1])
            self.buffer.delete_range(ln - 1, prev_len, ln, 0)
            self.buffer.set_cursor(ln - 1, prev_len)

    # ------------------------------------------------------
    # Delete key
    # ------------------------------------------------------
    def delete_key(self):
        if self.has_selection():
            self.delete_selection()
            return

        ln = self.buffer.cursor_line
        col = self.buffer.cursor_col

        if col < len(self.buffer.lines[ln]):
            self.buffer.delete_range(ln, col, ln, col + 1)
            return

        # Join with next line
        if ln < len(self.buffer.lines) - 1:
            self.buffer.delete_range(ln, col, ln + 1, 0)

    # ------------------------------------------------------
    # Tab / Shift+Tab
    # ------------------------------------------------------
    def indent(self, shift=False):
        if self.has_selection():
            sl, sc = self.selection_start
            el, ec = self.selection_end
            if (el < sl) or (el == sl and ec < sc):
                sl, sc, el, ec = el, ec, sl, sc

            self.buffer.undo.begin_group()

            for ln in range(sl, el + 1):
                line = self.buffer.lines[ln]
                if shift:
                    # unindent
                    if line.startswith(" " * self.buffer.tab_width):
                        new = line[self.buffer.tab_width:]
                        self.buffer.undo.add_action(
                            UndoAction('replace', ln, old_content=line, new_content=new)
                        )
                        self.buffer.lines[ln] = new
                else:
                    # indent
                    pad = " " * self.buffer.tab_width
                    new = pad + line
                    self.buffer.undo.add_action(
                        UndoAction('replace', ln, old_content=line, new_content=new)
                    )
                    self.buffer.lines[ln] = new

            self.buffer.undo.end_group()
            self.buffer.emit('changed')
            return

        # No selection: insert tab/spaces
        if shift:
            return  # no-op

        if self.buffer.insert_spaces:
            self.insert_text(" " * self.buffer.tab_width)
        else:
            self.insert_text("\t")

    # ------------------------------------------------------
    # Home / End (smart)
    # ------------------------------------------------------
    def smart_home(self):
        ln = self.buffer.cursor_line
        line = self.buffer.lines[ln]
        col = self.buffer.cursor_col

        # Compute first non-space position
        stripped = len(line) - len(line.lstrip(" \t"))

        if self.buffer.smart_home_end:
            if col == stripped:
                self.buffer.set_cursor(ln, 0)
            else:
                self.buffer.set_cursor(ln, stripped)
        else:
            self.buffer.set_cursor(ln, 0)

    def smart_end(self):
        ln = self.buffer.cursor_line
        end = len(self.buffer.lines[ln])
        self.buffer.set_cursor(ln, end)

    # ------------------------------------------------------
    # Arrow keys
    # ------------------------------------------------------
    def move_left(self, shift=False):
        ln, col = self.buffer.cursor_line, self.buffer.cursor_col
        if shift:
            self.add_selection_point(ln, col - 1 if col > 0 else col)

        if col > 0:
            self.buffer.set_cursor(ln, col - 1)
        else:
            if ln > 0:
                prev_len = len(self.buffer.lines[ln - 1])
                self.buffer.set_cursor(ln - 1, prev_len)

        if not shift:
            self.clear_selection()

    def move_right(self, shift=False):
        ln, col = self.buffer.cursor_line, self.buffer.cursor_col
        line_len = len(self.buffer.lines[ln])

        if shift:
            self.add_selection_point(ln, col + 1)

        if col < line_len:
            self.buffer.set_cursor(ln, col + 1)
        else:
            if ln < len(self.buffer.lines) - 1:
                self.buffer.set_cursor(ln + 1, 0)

        if not shift:
            self.clear_selection()

    def move_up(self, shift=False):
        ln, col = self.buffer.cursor_line, self.buffer.cursor_col

        if ln > 0:
            new_ln = ln - 1
            new_col = min(col, len(self.buffer.lines[new_ln]))
            self.buffer.set_cursor(new_ln, new_col)

            if shift:
                self.add_selection_point(new_ln, new_col)
            else:
                self.clear_selection()

    def move_down(self, shift=False):
        ln, col = self.buffer.cursor_line, self.buffer.cursor_col

        if ln < len(self.buffer.lines) - 1:
            new_ln = ln + 1
            new_col = min(col, len(self.buffer.lines[new_ln]))
            self.buffer.set_cursor(new_ln, new_col)

            if shift:
                self.add_selection_point(new_ln, new_col)
            else:
                self.clear_selection()

    # ------------------------------------------------------
    # Page Up / Page Down
    # ------------------------------------------------------
    def page_up(self, visible_lines, shift=False):
        ln = self.buffer.cursor_line
        target = max(0, ln - visible_lines)
        col = self.buffer.cursor_col
        col = min(col, len(self.buffer.lines[target]))

        self.buffer.set_cursor(target, col)

        if shift:
            self.add_selection_point(target, col)
        else:
            self.clear_selection()

    def page_down(self, visible_lines, shift=False):
        ln = self.buffer.cursor_line
        target = min(len(self.buffer.lines) - 1, ln + visible_lines)
        col = self.buffer.cursor_col
        col = min(col, len(self.buffer.lines[target]))

        self.buffer.set_cursor(target, col)

        if shift:
            self.add_selection_point(target, col)
        else:
            self.clear_selection()

    # ------------------------------------------------------
    # Mouse handling
    # ------------------------------------------------------
    def on_button_press(self, line, col, event):
        t = time.time()
        double = (t - self.last_click_time) < self.double_click_threshold
        self.last_click_time = t

        self.buffer.set_cursor(line, col)

        if double:
            self.select_word_at(line, col)
            return

        self.clear_selection()
        self.dragging = True
        self.drag_anchor = (line, col)
        self.selection_start = (line, col)
        self.selection_end = (line, col)

    def on_button_release(self):
        self.dragging = False
        self.drag_anchor = None

    def on_mouse_drag(self, line, col):
        if not self.dragging:
            return

        self.selection_end = (line, col)
        self.buffer.set_cursor(line, col)

    # ------------------------------------------------------
    # Double-click: select word
    # ------------------------------------------------------
    def select_word_at(self, line, col):
        text = self.buffer.lines[line]
        if not text:
            return

        # Expand left
        s = col
        while s > 0 and (text[s - 1].isalnum() or text[s - 1] == '_'):
            s -= 1

        # Expand right
        e = col
        while e < len(text) and (text[e].isalnum() or text[e] == '_'):
            e += 1

        self.selection_start = (line, s)
        self.selection_end = (line, e)
        self.buffer.set_cursor(line, e)

# ==========================================================
# VIRTUAL TEXT VIEW
# ==========================================================

class VirtualTextView(Gtk.DrawingArea):
    """
    The View layer.
    It draws via VirtualTextRenderer.
    It edits via InputController.
    It holds engines (syntax, folding, search).
    """

    __gsignals__ = {
        'cursor-moved': (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self, buffer):
        print("VIEW INIT START")

        super().__init__()

        self.set_focusable(True)
        print("DRAW CALLED")
        self.set_draw_func(self.on_draw)

        # Core document buffer
        self.buffer = buffer
        self.buffer.connect("cursor-moved", self._on_buffer_cursor_moved)
        self.buffer.connect("changed", lambda *a: self.queue_draw())

        # Engines
        self.syntax = SyntaxEngine()
        self.folding = FoldingEngine()
        self.search = SearchEngine()
        self.renderer = VirtualTextRenderer()

        # Input controller
        self.input = InputController(self, buffer)

        # Scroll positions
        self.scroll_line = 0
        self.scroll_x = 0

        # Visual options
        self.show_line_numbers = True
        self.show_right_margin = True
        self.right_margin_pos = 80
        self.highlight_current_line = True

        # Event controllers
        self._install_pointer_controller()
        self._install_key_controller()
        print("KEY OK")

        self._install_scroll_controller()
        print("SCROLL OK")


        # Ensure we get focus on click
        self.add_css_class("view")


    # ------------------------------------------------------
    # Event Controllers
    # ------------------------------------------------------
    def _install_pointer_controller(self):
        controller = Gtk.GestureClick()
        controller.connect("pressed", self._on_click)
        controller.connect("released", self._on_release)
        self.add_controller(controller)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)

    def _install_key_controller(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

    def _install_scroll_controller(self):
        flags = (
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )

        scroll = Gtk.EventControllerScroll.new(flags)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)



    # ------------------------------------------------------
    # Drawing
    # ------------------------------------------------------
    def on_draw(self, area, cr, width, height):
        allocation = area.get_allocation()

        # Selection
        sel_start = self.input.selection_start
        sel_end = self.input.selection_end
        print("RENDER CALLED")
        self.renderer.draw(
            cr,
            allocation,
            self.buffer,
            self.syntax,
            self.folding,
            self.search,
            sel_start,
            sel_end,
            extra_cursors=[],
            scroll_line=self.scroll_line,
            scroll_x=self.scroll_x,
            show_line_numbers=self.show_line_numbers,
            show_right_margin=self.show_right_margin,
            right_margin_pos=self.right_margin_pos,
            highlight_current_line=self.highlight_current_line
        )

    # ------------------------------------------------------
    # View → Buffer cursor sync
    # ------------------------------------------------------
    def _on_buffer_cursor_moved(self, buf, line, col):
        self.emit("cursor-moved", line, col)
        self.ensure_cursor_visible()
        self.queue_draw()

    # ------------------------------------------------------
    # Scrolling
    # ------------------------------------------------------
    def ensure_cursor_visible(self):
        ln = self.buffer.cursor_line

        # Scroll vertically
        if ln < self.scroll_line:
            self.scroll_line = ln
        else:
            alloc = self.get_allocation()
            lines_visible = alloc.height // self.renderer.line_height
            if ln >= self.scroll_line + lines_visible - 1:
                self.scroll_line = ln - (lines_visible - 1)

    def _on_scroll(self, controller, dx, dy):
        # Vertical scroll
        if dy != 0:
            self.scroll_line += int(dy * 3)
            self.scroll_line = max(0, min(
                self.scroll_line,
                self.buffer.line_count() - 1
            ))
            self.queue_draw()

        # Horizontal scroll
        if dx != 0:
            self.scroll_x += int(dx * 30)
            self.scroll_x = max(0, self.scroll_x)
            self.queue_draw()

        return True

    # ------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------
    def _coords_to_line_col(self, x, y):
        # Convert pixel coords → (line, col)
        line = self.scroll_line + int(y // self.renderer.line_height)
        line = max(0, min(line, self.buffer.line_count() - 1))

        col_x = x - self.renderer.gutter_width + self.scroll_x
        col = max(0, int(col_x // self.renderer.char_width))
        col = min(col, len(self.buffer.lines[line]))
        return line, col

    def _on_click(self, controller, n_press, x, y):
        self.grab_focus()

        line, col = self._coords_to_line_col(x, y)
        event = controller.get_current_event()

        self.input.on_button_press(line, col, event)
        self.queue_draw()

    def _on_release(self, controller, n_press, x, y):
        self.input.on_button_release()
        self.queue_draw()

    def _on_drag(self, drag, dx, dy):
        bx, by = drag.get_start_point()
        x = bx + dx
        y = by + dy

        line, col = self._coords_to_line_col(x, y)
        self.input.on_mouse_drag(line, col)
        self.queue_draw()

    # ------------------------------------------------------
    # Keyboard events
    # ------------------------------------------------------
    def _on_key_pressed(self, controller, keyval, keycode, state):
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        ctrl  = bool(state & Gdk.ModifierType.CONTROL_MASK)

        name = Gdk.keyval_name(keyval)

        # Editing
        if name == "Return":
            self.input.insert_newline()
            return True

        if name == "BackSpace":
            self.input.backspace()
            return True

        if name == "Delete":
            self.input.delete_key()
            return True

        if name == "Tab":
            self.input.indent(shift)
            return True

        # Cursor movement
        if name == "Left":
            self.input.move_left(shift)
            return True

        if name == "Right":
            self.input.move_right(shift)
            return True

        if name == "Up":
            self.input.move_up(shift)
            return True

        if name == "Down":
            self.input.move_down(shift)
            return True

        if name == "Page_Up":
            self.input.page_up(self._visible_lines(), shift)
            return True

        if name == "Page_Down":
            self.input.page_down(self._visible_lines(), shift)
            return True

        if name == "Home":
            self.input.smart_home()
            return True

        if name == "End":
            self.input.smart_end()
            return True

        # Undo / Redo
        if ctrl and name == "z":
            self.buffer.undo_last()
            return True

        if ctrl and name == "y":
            self.buffer.redo_last()
            return True

        # Text typing
        ch = self._key_to_text(keyval, state)
        if ch:
            self.input.insert_text(ch)
            return True

        return False

    def _visible_lines(self):
        alloc = self.get_allocation()
        return max(1, alloc.height // self.renderer.line_height)

    def _key_to_text(self, keyval, state):
        # Ignore Ctrl-modified keys
        if state & Gdk.ModifierType.CONTROL_MASK:
            return None

        # Ignore Alt-modified keys (GTK4 uses ALT_MASK)
        if state & Gdk.ModifierType.ALT_MASK:
            return None

        c = Gdk.keyval_to_unicode(keyval)
        if c == 0:
            return None

        return chr(c)


    # ------------------------------------------------------
    # Public API
    # ------------------------------------------------------
    def set_language(self, language):
        self.syntax.set_language(language)
        self.queue_draw()

    def find(self, text, regex=False, case_sensitive=False):
        self.search.search(self.buffer.lines, text, regex, case_sensitive)
        self.queue_draw()

    def find_next(self):
        cur = self.search.find_next(self.buffer.cursor_line, self.buffer.cursor_col)
        if cur:
            ln, s, e = cur
            self.buffer.set_cursor(ln, e)
        self.queue_draw()

    def find_previous(self):
        cur = self.search.find_previous(self.buffer.cursor_line, self.buffer.cursor_col)
        if cur:
            ln, s, e = cur
            self.buffer.set_cursor(ln, e)
        self.queue_draw()

    def toggle_fold(self, line):
        if self.folding.toggle(self.buffer.lines, line, self.buffer.tab_width):
            self.queue_draw()

# ==========================================================
# INTEGRATION UTILITIES
# ==========================================================

class FileLoaderSaver:
    """
    Provides file load/save helpers with encoding detection.
    Cleanly separated from the buffer and view.
    """

    @staticmethod
    def load_file(path):
        if not os.path.exists(path):
            return [""]

        raw = open(path, "rb").read()
        info = chardet.detect(raw)
        enc = info.get("encoding") or "utf-8"

        try:
            text = raw.decode(enc)
        except Exception:
            # fallback
            enc = "utf-8"
            text = raw.decode(enc, errors="replace")

        lines = text.splitlines()
        # Preserve final empty line behavior like typical editors
        if text.endswith("\n"):
            lines.append("")

        return lines, enc, info

    @staticmethod
    def save_file(path, lines, encoding="utf-8"):
        txt = "\n".join(lines)
        with open(path, "wb") as f:
            f.write(txt.encode(encoding, errors="replace"))

# ==========================================================
# SIMPLE SEARCH BAR (OPTIONAL)
# ==========================================================

class SearchBar(Gtk.Box):
    """
    A small search box that can be attached to the main UI.
    Fully optional. If deleted, search still works by API.
    """

    def __init__(self, view):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.view = view

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Search")
        self.entry.connect("activate", self._on_search)
        self.append(self.entry)

        btn_prev = Gtk.Button(label="Prev")
        btn_prev.connect("clicked", lambda *a: self.view.find_previous())
        self.append(btn_prev)

        btn_next = Gtk.Button(label="Next")
        btn_next.connect("clicked", lambda *a: self.view.find_next())
        self.append(btn_next)

        btn_close = Gtk.Button(label="✕")
        btn_close.connect("clicked", self._close)
        self.append(btn_close)

    def _on_search(self, *a):
        text = self.entry.get_text()
        self.view.find(text, regex=False, case_sensitive=False)

    def _close(self, *a):
        self.set_visible(False)


# ==========================================================
# SCROLLED CONTAINER FOR VIRTUALTEXTVIEW
# ==========================================================

class VirtualTextEditor(Gtk.Box):
    """
    A complete editor widget:
        - ScrolledWindow
        - VirtualTextView
        - (optional) SearchBar

    This becomes your main embeddable widget.
    """

    def __init__(self, buffer=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        # Buffer
        if buffer is None:
            buffer = VirtualTextBuffer()

        # View
        self.view = VirtualTextView(buffer)

        # Search bar (optional)
        self.search_bar = SearchBar(self.view)
        self.search_bar.set_visible(False)

        self.append(self.search_bar)

        # Scrolling wrapper
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.scroll.set_child(self.view) 
        self.append(self.scroll)

    # ------------------------------------------------------
    # Public API wrappers
    # ------------------------------------------------------
    def load_file(self, path):
        lines, enc, info = FileLoaderSaver.load_file(path)
        self.view.buffer.load_lines(lines)
        self.view.buffer.file_path = path
        self.view.buffer.encoding = enc
        self.view.buffer.encoding_info = info

    def save_file(self, path=None):
        buf = self.view.buffer
        if path:
            buf.file_path = path
        if not buf.file_path:
            return False
        FileLoaderSaver.save_file(buf.file_path, buf.lines, buf.encoding)
        buf.modified = False
        return True

    def show_search(self):
        self.search_bar.set_visible(True)
        self.search_bar.entry.grab_focus()

# ==========================================================
# MAIN APPLICATION WINDOW
# ==========================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Text Editor")
        self.set_default_size(1000, 700)

        # The editor
        self.editor = VirtualTextEditor()

        # Status bar
        self.status = Gtk.Label()
        self.status.set_margin_top(4)
        self.status.set_margin_bottom(4)
        self.status.set_margin_start(8)

        # Proper GTK4 layout container
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.editor)  # only append ONCE
        box.append(self.status)

        # Set window content EXACTLY once
        self.set_content(box)

        # Connect cursor updates
        self.editor.view.connect("cursor-moved", self._on_cursor_moved)

        # Actions
        self._add_actions()
        self._install_shortcuts()


    # ------------------------------------------------------
    # Cursor status updates
    # ------------------------------------------------------
    def _on_cursor_moved(self, view, line, col):
        self.status.set_text(f"Line {line+1}, Col {col+1}")

    # ------------------------------------------------------
    # Actions
    # ------------------------------------------------------
    def _add_actions(self):
        # File → New
        act_new = Gio.SimpleAction.new("new", None)
        act_new.connect("activate", self._new_file)
        self.add_action(act_new)

        # File → Open
        act_open = Gio.SimpleAction.new("open", None)
        act_open.connect("activate", self._open_file)
        self.add_action(act_open)

        # File → Save
        act_save = Gio.SimpleAction.new("save", None)
        act_save.connect("activate", self._save_file)
        self.add_action(act_save)

        # File → Save As
        act_saveas = Gio.SimpleAction.new("saveas", None)
        act_saveas.connect("activate", self._save_file_as)
        self.add_action(act_saveas)

        # Search
        act_search = Gio.SimpleAction.new("search", None)
        act_search.connect("activate", lambda *a: self.editor.show_search())
        self.add_action(act_search)

    # ------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------
    def _install_shortcuts(self):
        app = self.get_application()

        app.set_accels_for_action("win.new", ["<Primary>n"])
        app.set_accels_for_action("win.open", ["<Primary>o"])
        app.set_accels_for_action("win.save", ["<Primary>s"])
        app.set_accels_for_action("win.saveas", ["<Primary><Shift>s"])
        app.set_accels_for_action("win.search", ["<Primary>f"])

    # ------------------------------------------------------
    # File operations
    # ------------------------------------------------------
    def _new_file(self, action, param):
        self.editor.view.buffer.load_lines([""])
        self.set_title("New File – Virtual Text Editor")

    def _open_file(self, action, param):
        dialog = Gtk.FileChooserNative.new(
            "Open File",
            self,
            Gtk.FileChooserAction.OPEN,
            "_Open", "_Cancel"
        )
        resp = dialog.run()
        if resp == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            self.editor.load_file(path)
            self._detect_language(path)
            self.set_title(os.path.basename(path))
        dialog.destroy()

    def _save_file(self, action, param):
        buf = self.editor.view.buffer
        if buf.file_path:
            self.editor.save_file()
            self.set_title(os.path.basename(buf.file_path))
            return

        self._save_file_as(None, None)

    def _save_file_as(self, action, param):
        dialog = Gtk.FileChooserNative.new(
            "Save File As",
            self,
            Gtk.FileChooserAction.SAVE,
            "_Save", "_Cancel"
        )
        dialog.set_current_name("untitled.txt")
        resp = dialog.run()

        if resp == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            self.editor.save_file(path)
            self.set_title(os.path.basename(path))

        dialog.destroy()

    # ------------------------------------------------------
    # Auto-detect language for syntax
    # ------------------------------------------------------
    def _detect_language(self, path):
        ext = os.path.splitext(path)[1].lower()

        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".c": "c",
            ".h": "c",
            ".rs": "rust",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
        }

        lang = mapping.get(ext, None)
        if lang:
            self.editor.view.set_language(lang)

# ==========================================================
# APPLICATION
# ==========================================================

class EditorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.VirtualTextEditor",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()

    def do_command_line(self, cmd):
        files = cmd.get_arguments()[1:]
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)

        if files:
            path = files[0]
            if os.path.exists(path):
                win.editor.load_file(path)
                win._detect_language(path)
                win.set_title(os.path.basename(path))

        win.present()
        return 0


# ==========================================================
# MAIN ENTRY
# ==========================================================

if __name__ == "__main__":
    app = EditorApplication()
    app.run(sys.argv)

