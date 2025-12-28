"""
High-Performance State-Aware Syntax Highlighting Engine

This module provides a complete rewrite of syntax highlighting with proper
support for multi-line constructs (triple-quoted strings, multi-line comments).

Key features:
- StateChain: Tracks end-of-line state for each line (1 byte per line)
- State-aware tokenizer: Different pattern sets per state
- Smart invalidation: Edits trigger state propagation until stable
- LRU token cache: Efficient caching with configurable size

Author: Svite Project
"""

import re
from array import array
from typing import List, Tuple, Optional, Callable, Dict, Any
from collections import OrderedDict
import unicodedata


# =============================================================================
# TOKEN STATE CONSTANTS
# =============================================================================

class TokenState:
    """Immutable token states for syntax highlighting."""
    ROOT = 0                # Normal code
    IN_SQ_STRING = 1        # Inside '...' string
    IN_DQ_STRING = 2        # Inside "..." string
    IN_TRIPLE_SQ = 3        # Inside '''...''' string
    IN_TRIPLE_DQ = 4        # Inside """...""" string
    IN_ML_COMMENT = 5       # Inside /* */ comment
    IN_F_SQ_STRING = 6      # Inside f'...' string
    IN_F_DQ_STRING = 7      # Inside f"..." string
    IN_F_TRIPLE_SQ = 8      # Inside f'''...''' string
    IN_F_TRIPLE_DQ = 9      # Inside f"""...""" string
    IN_B_SQ_STRING = 10     # Inside b'...' string
    IN_B_DQ_STRING = 11     # Inside b"..." string
    IN_B_TRIPLE_SQ = 12     # Inside b'''...''' string
    IN_B_TRIPLE_DQ = 13     # Inside b"""...""" string
    IN_R_SQ_STRING = 14     # Inside r'...' string
    IN_R_DQ_STRING = 15     # Inside r"..." string
    IN_R_TRIPLE_SQ = 16     # Inside r'''...''' string
    IN_R_TRIPLE_DQ = 17     # Inside r"""...""" string
    
    # YAML block scalar states
    IN_YAML_BLOCK_LITERAL = 18  # Inside | block scalar
    IN_YAML_BLOCK_FOLDED = 19   # Inside > block scalar
    
    @classmethod
    def is_string_state(cls, state: int) -> bool:
        """Check if state represents being inside a string."""
        return state >= cls.IN_SQ_STRING and state <= cls.IN_R_TRIPLE_DQ
    
    @classmethod
    def is_triple_string(cls, state: int) -> bool:
        """Check if state represents being inside a triple-quoted string."""
        return state in (
            cls.IN_TRIPLE_SQ, cls.IN_TRIPLE_DQ,
            cls.IN_F_TRIPLE_SQ, cls.IN_F_TRIPLE_DQ,
            cls.IN_B_TRIPLE_SQ, cls.IN_B_TRIPLE_DQ,
            cls.IN_R_TRIPLE_SQ, cls.IN_R_TRIPLE_DQ
        )
    
    @classmethod
    def get_delimiter(cls, state: int) -> str:
        """Get the closing delimiter for a string state."""
        if state in (cls.IN_TRIPLE_SQ, cls.IN_F_TRIPLE_SQ, cls.IN_B_TRIPLE_SQ, cls.IN_R_TRIPLE_SQ):
            return "'''"
        elif state in (cls.IN_TRIPLE_DQ, cls.IN_F_TRIPLE_DQ, cls.IN_B_TRIPLE_DQ, cls.IN_R_TRIPLE_DQ):
            return '"""'
        elif state in (cls.IN_SQ_STRING, cls.IN_F_SQ_STRING, cls.IN_B_SQ_STRING, cls.IN_R_SQ_STRING):
            return "'"
        elif state in (cls.IN_DQ_STRING, cls.IN_F_DQ_STRING, cls.IN_B_DQ_STRING, cls.IN_R_DQ_STRING):
            return '"'
        return ""
    
    @classmethod
    def get_token_type(cls, state: int) -> str:
        """Get the token type name for a string state."""
        if state in (cls.IN_F_SQ_STRING, cls.IN_F_DQ_STRING, cls.IN_F_TRIPLE_SQ, cls.IN_F_TRIPLE_DQ):
            return 'f_string_content'
        elif state in (cls.IN_B_SQ_STRING, cls.IN_B_DQ_STRING, cls.IN_B_TRIPLE_SQ, cls.IN_B_TRIPLE_DQ):
            return 'byte_string_content'
        elif state in (cls.IN_R_SQ_STRING, cls.IN_R_DQ_STRING, cls.IN_R_TRIPLE_SQ, cls.IN_R_TRIPLE_DQ):
            return 'raw_string_content'
        else:
            return 'string_content'


# =============================================================================
# STATE CHAIN
# =============================================================================

class StateChain:
    """
    Efficient storage of end-of-line state for each line.
    
    - Index i holds the state AFTER line i is tokenized
    - Line i+1 starts with state from index i
    - Uses byte array for memory efficiency (1 byte per line)
    """
    
    def __init__(self):
        self._states: array = array('B')  # Unsigned byte array
        self._dirty_from: int = 0  # Lines >= this may need re-tokenization
    
    def get_start_state(self, line_num: int) -> int:
        """Get the starting state for tokenizing a line."""
        if line_num <= 0:
            return TokenState.ROOT
        idx = line_num - 1
        if idx < len(self._states):
            return self._states[idx]
        return TokenState.ROOT
    
    def get_end_state(self, line_num: int) -> Optional[int]:
        """Get the end state after a line (if computed)."""
        if line_num < len(self._states):
            return self._states[line_num]
        return None
    
    def set_end_state(self, line_num: int, state: int) -> bool:
        """
        Set the end state for a line.
        Returns True if state changed (propagation needed).
        """
        # Extend array if needed
        while len(self._states) <= line_num:
            self._states.append(TokenState.ROOT)
        
        old_state = self._states[line_num]
        self._states[line_num] = state
        
        return old_state != state
    
    def invalidate_from(self, line_num: int):
        """Mark lines from line_num onwards as potentially dirty."""
        self._dirty_from = min(self._dirty_from, line_num)
        # Truncate states beyond this point for correctness
        if line_num < len(self._states):
            del self._states[line_num:]
    
    def clear(self):
        """Clear all stored states."""
        self._states = array('B')
        self._dirty_from = 0
    
    def __len__(self):
        return len(self._states)


# =============================================================================
# TOKEN CACHE (LRU)
# =============================================================================

class TokenCache:
    """
    LRU cache for tokenized lines.
    
    Uses OrderedDict for O(1) access and LRU eviction.
    """
    
    def __init__(self, max_size: int = 5000):
        self._cache: OrderedDict = OrderedDict()
        self._max_size: int = max_size
    
    def get(self, line_num: int) -> Optional[List]:
        """Get cached tokens for a line, or None if not cached."""
        if line_num in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(line_num)
            return self._cache[line_num]
        return None
    
    def set(self, line_num: int, tokens: List):
        """Cache tokens for a line."""
        if line_num in self._cache:
            self._cache.move_to_end(line_num)
        else:
            if len(self._cache) >= self._max_size:
                # Evict least recently used
                self._cache.popitem(last=False)
        self._cache[line_num] = tokens
    
    def invalidate(self, line_num: int):
        """Remove a specific line from cache."""
        if line_num in self._cache:
            del self._cache[line_num]
    
    def invalidate_from(self, line_num: int):
        """Remove all lines >= line_num from cache."""
        to_remove = [k for k in self._cache if k >= line_num]
        for k in to_remove:
            del self._cache[k]
    
    def clear(self):
        """Clear entire cache."""
        self._cache.clear()


# =============================================================================
# COMPILED PATTERN SETS
# =============================================================================

class PythonPatterns:
    """Pre-compiled regex patterns for Python syntax highlighting."""
    
    # String start patterns (order matters - longest first!)
    # Triple-quoted with prefixes
    F_TRIPLE_DQ = re.compile(r'[fF][rR]?"""')
    F_TRIPLE_SQ = re.compile(r"[fF][rR]?'''")
    B_TRIPLE_DQ = re.compile(r'[bB][rR]?"""')
    B_TRIPLE_SQ = re.compile(r"[bB][rR]?'''")
    R_TRIPLE_DQ = re.compile(r'[rR][fFbB]?"""')
    R_TRIPLE_SQ = re.compile(r"[rR][fFbB]?'''")
    U_TRIPLE_DQ = re.compile(r'[uU]"""')
    U_TRIPLE_SQ = re.compile(r"[uU]'''")
    TRIPLE_DQ = re.compile(r'"""')
    TRIPLE_SQ = re.compile(r"'''")
    
    # Single-quoted with prefixes
    F_DQ = re.compile(r'[fF][rR]?"')
    F_SQ = re.compile(r"[fF][rR]?'")
    B_DQ = re.compile(r'[bB][rR]?"')
    B_SQ = re.compile(r"[bB][rR]?'")
    R_DQ = re.compile(r'[rR][fFbB]?"')
    R_SQ = re.compile(r"[rR][fFbB]?'")
    U_DQ = re.compile(r'[uU]"')
    U_SQ = re.compile(r"[uU]'")
    DQ = re.compile(r'"')
    SQ = re.compile(r"'")
    
    # String end patterns
    END_TRIPLE_DQ = re.compile(r'"""')
    END_TRIPLE_SQ = re.compile(r"'''")
    END_DQ = re.compile(r'(?<!\\)"')
    END_SQ = re.compile(r"(?<!\\)'")
    
    # Escape sequences
    ESCAPE = re.compile(r'\\.')
    
    # Code patterns (for ROOT state)
    COMMENT = re.compile(r'#.*$')
    DECORATOR = re.compile(r'@\w+')
    
    KEYWORDS = re.compile(
        r'\b(as|assert|async|await|break|class|continue|def|del|elif|else|'
        r'except|finally|for|from|global|if|import|in|is|lambda|nonlocal|'
        r'not|or|pass|raise|return|try|while|with|yield)\b'
    )
    
    BOOL_OPS = re.compile(r'\b(and|And|None|True|False)\b')
    
    BUILTINS = re.compile(
        r'\b(abs|all|any|ascii|bin|bool|bytearray|bytes|callable|chr|'
        r'classmethod|compile|complex|delattr|dict|dir|divmod|enumerate|'
        r'eval|exec|filter|float|format|frozenset|getattr|globals|hasattr|'
        r'hash|help|hex|id|input|int|isinstance|issubclass|iter|len|list|'
        r'locals|map|max|memoryview|min|next|object|oct|open|ord|pow|print|'
        r'property|range|repr|reversed|round|set|setattr|slice|sorted|'
        r'staticmethod|str|sum|super|tuple|type|vars|zip|__import__|__init__)\b'
    )
    
    NUMBER = re.compile(r'\b\d+\.?\d*([eE][+-]?\d+)?\b')
    
    HELPERS = re.compile(r'\b(self|__\w+__)\b')
    
    FUNCTION_DEF = re.compile(r'\b(def)\s+(\w+)')
    CLASS_DEF = re.compile(r'\b(class)\s+(\w+)')
    
    PERSONAL = re.compile(r'\b(Adw|Gtk)\b')


class YamlPatterns:
    """Pre-compiled regex patterns for YAML syntax highlighting.
    
    Based on VSCode's YAML TextMate grammars:
    - yaml-1.2.tmLanguage.json (primary)
    - yaml-1.1.tmLanguage.json (additional patterns)
    """
    
    # Comments - must start with # preceded by whitespace or at line start
    COMMENT = re.compile(r'(?:^|\s)#.*$')
    
    # Document markers
    DOC_START = re.compile(r'^---(?:\s|$)')
    DOC_END = re.compile(r'^\.\.\.(?:\s|$)')
    
    # Directives (e.g., %YAML 1.2, %TAG)
    # Captures: group(1) = %YAML or %TAG, group(2) = rest (version/args)
    DIRECTIVE = re.compile(r'^(%(?:YAML|TAG))\b(.*)$')
    
    # Block scalar indicators (| and >)
    BLOCK_SCALAR = re.compile(r'[|>][+-]?(?:\d)?(?:\s*$)')
    
    # Anchor (&name) and Alias (*name)
    ANCHOR = re.compile(r'&[^\s,\[\]{}]+')
    ALIAS = re.compile(r'\*[^\s,\[\]{}]+')
    
    # Tags (!, !!, !<...>, !tag!suffix)
    TAG_VERBATIM = re.compile(r'!<[^>]+>')
    TAG_NAMED = re.compile(r'![a-zA-Z0-9-]+![^\s,\[\]{}]*')
    TAG_SECONDARY = re.compile(r'!![^\s,\[\]{}]+')
    TAG_PRIMARY = re.compile(r'![^\s,\[\]{}!]*')
    
    # Null values
    NULL = re.compile(r'\b(?:null|Null|NULL|~)\b')
    
    # Boolean values (YAML 1.1 and 1.2 compatible)
    BOOLEAN = re.compile(
        r'\b(?:true|True|TRUE|false|False|FALSE|'
        r'yes|Yes|YES|no|No|NO|y|Y|n|N|'
        r'on|On|ON|off|Off|OFF)\b'
    )
    
    # Numbers - integers (decimal, hex, octal, binary)
    INT_DECIMAL = re.compile(r'[+-]?(?:0|[1-9][0-9_]*)\b')
    INT_HEX = re.compile(r'0x[0-9a-fA-F_]+\b')
    INT_OCTAL = re.compile(r'0o[0-7_]+\b')
    INT_BINARY = re.compile(r'0b[01_]+\b')
    
    # Numbers - floats (including special values)
    FLOAT = re.compile(r'[+-]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)\b')
    FLOAT_INF = re.compile(r'[+-]?\.(?:inf|Inf|INF)\b')
    FLOAT_NAN = re.compile(r'\.(?:nan|NaN|NAN)\b')
    
    # Sexagesimal (base 60) numbers - YAML 1.1
    SEXAGESIMAL_INT = re.compile(r'[+-]?[1-9][0-9_]*(?::[0-5]?[0-9])+\b')
    SEXAGESIMAL_FLOAT = re.compile(r'[+-]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*\b')
    
    # Timestamp (ISO 8601 style)
    TIMESTAMP = re.compile(
        r'\d{4}-\d{2}-\d{2}(?:[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}(?::\d{2})?)?)?'
    )
    
    # Merge key
    MERGE_KEY = re.compile(r'<<(?=\s|$)')
    
    # Double-quoted strings  
    DOUBLE_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
    DOUBLE_QUOTED_START = re.compile(r'"(?:[^"\\]|\\.)*$')  # Unterminated
    
    # Single-quoted strings
    SINGLE_QUOTED = re.compile(r"'(?:[^']|'')*'")
    SINGLE_QUOTED_START = re.compile(r"'(?:[^']|'')*$")  # Unterminated
    
    # Map key - matches key: pattern (key can be plain, quoted, etc.)
    # Plain key (unquoted) followed by colon
    MAP_KEY = re.compile(r'^(\s*)([^\s#:,\[\]{}!&*|>\'"%-][^:#]*?)?(\s*):(?:\s|$)')
    FLOW_MAP_KEY = re.compile(r'([^\s#:,\[\]{}!&*|>\'"%-][^:#,\[\]{}]*?)\s*:(?=\s|,|\]|\}|$)')
    
    # Sequence item indicator
    SEQUENCE_ITEM = re.compile(r'^(\s*)-(?:\s|$)')
    
    # Explicit key indicator
    EXPLICIT_KEY = re.compile(r'^\s*\?(?:\s|$)')
    
    # Flow collection brackets
    FLOW_MAP_START = re.compile(r'\{')
    FLOW_MAP_END = re.compile(r'\}')
    FLOW_SEQ_START = re.compile(r'\[')
    FLOW_SEQ_END = re.compile(r'\]')
    FLOW_SEPARATOR = re.compile(r',')
    
    # Escape sequences in double-quoted strings
    ESCAPE = re.compile(r'\\(?:[0abtnvfre "\\N_LP/]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})')
    
    # Plain (unquoted) value - everything after special tokens until end of line or comment
    # This captures values like: localhost, postgres, dev_db, etc.
    PLAIN_VALUE = re.compile(r'[^\s#][^#]*')
    
    # Block scalar content line - indented text (used for | and > block content)
    BLOCK_CONTENT = re.compile(r'^(\s+)(.+)$')


# =============================================================================
# STATE-AWARE SYNTAX ENGINE
# =============================================================================

class StateAwareSyntaxEngine:
    """
    High-performance syntax highlighting engine with proper multi-line support.
    
    Key features:
    - State chain tracks context across lines
    - Different pattern sets per state (ROOT, IN_STRING, etc.)
    - Smart invalidation with state propagation
    - LRU token cache for performance
    """
    
    def __init__(self):
        self.language: Optional[str] = None
        self.state_chain = StateChain()
        self.cache = TokenCache()
        self.text_provider: Optional[Callable[[int], str]] = None
        self._total_lines_provider: Optional[Callable[[], int]] = None
        self._patterns = None
        self.theme: Optional[str] = None
    
    def set_text_provider(self, provider: Callable[[int], str]):
        """Set function to get line text: provider(line_num) -> str"""
        self.text_provider = provider
    
    def set_total_lines_provider(self, provider: Callable[[], int]):
        """Set function to get total line count."""
        self._total_lines_provider = provider
    
    def set_language(self, lang: str):
        """Set the language for syntax highlighting."""
        self.language = lang.lower() if lang else None
        self.state_chain.clear()
        self.cache.clear()
        
        if self.language == 'python':
            self._patterns = PythonPatterns
        elif self.language == 'yaml':
            self._patterns = YamlPatterns
        else:
            self._patterns = None

    def set_theme(self, theme: str):
        """Set the current theme (light/dark)."""
        self.theme = theme
    
    def invalidate_from(self, start_line: int):
        """Invalidate cache and state chain from a line onwards."""
        if start_line == 0:
            self.cache.clear()
            self.state_chain.clear()
        else:
            self.cache.invalidate_from(start_line)
            self.state_chain.invalidate_from(start_line)
    
    def invalidate_line(self, line_num: int):
        """Invalidate a single line (for minor edits)."""
        self.cache.invalidate(line_num)
        # State chain invalidation triggers propagation on next tokenize
        self.state_chain.invalidate_from(line_num)
    
    def on_text_changed(self, start_line: int, end_line: int):
        """
        Called when text is edited.
        Invalidates affected lines and triggers state propagation.
        """
        # Invalidate cache for changed range
        for ln in range(start_line, end_line + 1):
            self.cache.invalidate(ln)
        
        # Invalidate state chain
        self.state_chain.invalidate_from(start_line)
    
    def get_cached(self, line_num: int) -> Optional[List]:
        """Get cached tokens if available."""
        return self.cache.get(line_num)
    
    def tokenize(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize a line, using cache if available.
        
        Returns list of (start, end, token_type) tuples.
        """
        # Check cache first
        cached = self.cache.get(line_num)
        if cached is not None:
            return cached
        
        # Ensure we have valid start state by processing any gap
        self._ensure_state_chain(line_num)
        
        # Tokenize the line
        tokens = self._tokenize_line(line_num, text)
        
        # Cache the result
        self.cache.set(line_num, tokens)
        
        return tokens
    
    def _ensure_state_chain(self, line_num: int):
        """
        Ensure state chain is valid up to line_num.
        Processes any gap in the chain.
        """
        if line_num == 0:
            return
        
        # Check if previous line's state is known
        if line_num - 1 < len(self.state_chain):
            return  # State is known
        
        # Need to fill gap - find nearest known state
        start = len(self.state_chain)
        
        # Limit gap filling to prevent freeze
        MAX_GAP = 2000
        if line_num - start > MAX_GAP:
            # Too big gap, just start fresh from ROOT
            # This is a tradeoff for performance
            return
        
        # Fill the gap
        if self.text_provider:
            for ln in range(start, line_num):
                text = self.text_provider(ln)
                self._tokenize_line(ln, text)  # This updates state chain
    
    def _tokenize_line(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Internal tokenization with state tracking.
        """
        if not self._patterns:
            # Fallback for unsupported languages
            return self._tokenize_simple(text)
        
        if self.language == 'python':
            return self._tokenize_python(line_num, text)
        elif self.language == 'yaml':
            return self._tokenize_yaml(line_num, text)
        else:
            return self._tokenize_simple(text)
    
    def _tokenize_python(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize Python code with full multi-line string support.
        """
        P = self._patterns
        tokens = []
        state = self.state_chain.get_start_state(line_num)
        pos = 0
        length = len(text)
        
        while pos < length:
            if state == TokenState.ROOT:
                # Try to match patterns in priority order
                
                # 1. Comments (highest priority - consumes rest of line)
                m = P.COMMENT.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'comment'))
                    pos = m.end()
                    continue
                
                # 2. Triple-quoted strings (before single-quoted!)
                # f-strings
                m = P.F_TRIPLE_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'f_triple_start'))
                    state = TokenState.IN_F_TRIPLE_DQ
                    pos = m.end()
                    continue
                
                m = P.F_TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'f_triple_start'))
                    state = TokenState.IN_F_TRIPLE_SQ
                    pos = m.end()
                    continue
                
                # b-strings
                m = P.B_TRIPLE_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'b_triple_start'))
                    state = TokenState.IN_B_TRIPLE_DQ
                    pos = m.end()
                    continue
                
                m = P.B_TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'b_triple_start'))
                    state = TokenState.IN_B_TRIPLE_SQ
                    pos = m.end()
                    continue
                
                # r-strings
                m = P.R_TRIPLE_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'r_triple_start'))
                    state = TokenState.IN_R_TRIPLE_DQ
                    pos = m.end()
                    continue
                
                m = P.R_TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'r_triple_start'))
                    state = TokenState.IN_R_TRIPLE_SQ
                    pos = m.end()
                    continue
                
                # u-strings
                m = P.U_TRIPLE_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    state = TokenState.IN_TRIPLE_DQ
                    pos = m.end()
                    continue
                
                m = P.U_TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    state = TokenState.IN_TRIPLE_SQ
                    pos = m.end()
                    continue
                
                # Plain triple-quoted
                m = P.TRIPLE_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    state = TokenState.IN_TRIPLE_DQ
                    pos = m.end()
                    continue
                
                m = P.TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    state = TokenState.IN_TRIPLE_SQ
                    pos = m.end()
                    continue
                
                # 3. Single-quoted strings
                # f-strings
                m = P.F_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'f_string_start'))
                    state = TokenState.IN_F_DQ_STRING
                    pos = m.end()
                    continue
                
                m = P.F_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'f_string_start'))
                    state = TokenState.IN_F_SQ_STRING
                    pos = m.end()
                    continue
                
                # b-strings
                m = P.B_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'b_string_start'))
                    state = TokenState.IN_B_DQ_STRING
                    pos = m.end()
                    continue
                
                m = P.B_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'b_string_start'))
                    state = TokenState.IN_B_SQ_STRING
                    pos = m.end()
                    continue
                
                # r-strings
                m = P.R_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'r_string_start'))
                    state = TokenState.IN_R_DQ_STRING
                    pos = m.end()
                    continue
                
                m = P.R_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'r_string_start'))
                    state = TokenState.IN_R_SQ_STRING
                    pos = m.end()
                    continue
                
                # u-strings
                m = P.U_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_start'))
                    state = TokenState.IN_DQ_STRING
                    pos = m.end()
                    continue
                
                m = P.U_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_start'))
                    state = TokenState.IN_SQ_STRING
                    pos = m.end()
                    continue
                
                # Plain strings
                m = P.DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_start'))
                    state = TokenState.IN_DQ_STRING
                    pos = m.end()
                    continue
                
                m = P.SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_start'))
                    state = TokenState.IN_SQ_STRING
                    pos = m.end()
                    continue
                
                # 4. Decorator
                m = P.DECORATOR.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'decorator'))
                    pos = m.end()
                    continue
                
                # 5. Function/Class definitions (keyword + name)
                m = P.FUNCTION_DEF.match(text, pos)
                if m:
                    kw_start, kw_end = m.span(1)
                    name_start, name_end = m.span(2)
                    tokens.append((kw_start, kw_end, 'keywords'))
                    tokens.append((name_start, name_end, 'function'))
                    pos = m.end()
                    continue
                
                m = P.CLASS_DEF.match(text, pos)
                if m:
                    kw_start, kw_end = m.span(1)
                    name_start, name_end = m.span(2)
                    tokens.append((kw_start, kw_end, 'keywords'))
                    tokens.append((name_start, name_end, 'class'))
                    pos = m.end()
                    continue
                
                # 6. Keywords
                m = P.KEYWORDS.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'keywords'))
                    pos = m.end()
                    continue
                
                # 7. Bool ops
                m = P.BOOL_OPS.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'bool_ops'))
                    pos = m.end()
                    continue
                
                # 8. Builtins
                m = P.BUILTINS.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'builtins'))
                    pos = m.end()
                    continue
                
                # 9. Number
                m = P.NUMBER.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'number'))
                    pos = m.end()
                    continue
                
                # 10. Helpers (self, __xxx__)
                m = P.HELPERS.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'helpers'))
                    pos = m.end()
                    continue
                
                # 11. Personal (Adw, Gtk)
                m = P.PERSONAL.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'personal'))
                    pos = m.end()
                    continue
                
                # No match - skip this character
                pos += 1
            
            elif TokenState.is_string_state(state):
                # We're inside a string - look for end delimiter
                tokens, pos, state = self._tokenize_string_content(
                    text, pos, state, tokens
                )
        
        # Store end state
        self.state_chain.set_end_state(line_num, state)
        
        return tokens
    
    def _tokenize_string_content(
        self, 
        text: str, 
        pos: int, 
        state: int, 
        tokens: List
    ) -> Tuple[List, int, int]:
        """
        Tokenize content inside a string until end of line or closing delimiter.
        
        Returns (tokens, new_pos, new_state)
        """
        P = self._patterns
        length = len(text)
        content_start = pos
        token_type = TokenState.get_token_type(state)
        
        # Determine end pattern based on state
        is_triple = TokenState.is_triple_string(state)
        delimiter = TokenState.get_delimiter(state)
        is_raw = state in (
            TokenState.IN_R_SQ_STRING, TokenState.IN_R_DQ_STRING,
            TokenState.IN_R_TRIPLE_SQ, TokenState.IN_R_TRIPLE_DQ
        )
        
        while pos < length:
            # Look for end delimiter
            if is_triple:
                if delimiter == '"""':
                    m = P.END_TRIPLE_DQ.match(text, pos)
                else:
                    m = P.END_TRIPLE_SQ.match(text, pos)
            else:
                if delimiter == '"':
                    m = P.END_DQ.match(text, pos)
                else:
                    m = P.END_SQ.match(text, pos)
            
            if m:
                # Found end delimiter
                # Add content before delimiter
                if pos > content_start:
                    tokens.append((content_start, pos, token_type))
                
                # Add delimiter
                end_token = 'string'  # Generic end token
                if 'f_' in token_type:
                    end_token = 'f_string'
                elif 'b_' in token_type or 'byte' in token_type:
                    end_token = 'byte_string'
                elif 'r_' in token_type or 'raw' in token_type:
                    end_token = 'raw_string'
                
                tokens.append((pos, m.end(), end_token))
                return tokens, m.end(), TokenState.ROOT
            
            # Check for escape sequence (except in raw strings)
            if not is_raw:
                m = P.ESCAPE.match(text, pos)
                if m:
                    pos = m.end()
                    continue
            
            # No special match, advance one character
            pos += 1
        
        # End of line - still in string
        # Add remaining content as string token
        if pos > content_start:
            tokens.append((content_start, pos, token_type))
        
        return tokens, pos, state
    
    def _tokenize_yaml(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize YAML content.
        
        Supports multi-line block scalars (| and >) and plain value highlighting.
        """
        P = self._patterns
        tokens = []
        pos = 0
        length = len(text)
        
        # Check if we're continuing a block scalar from previous line
        start_state = self.state_chain.get_start_state(line_num)
        if start_state in (TokenState.IN_YAML_BLOCK_LITERAL, TokenState.IN_YAML_BLOCK_FOLDED):
            # Check if this line is indented (block scalar content)
            # Block scalar content ends when we hit a line with less/no indentation
            stripped = text.lstrip()
            indent = len(text) - len(stripped)
            
            if indent > 0 and stripped and not stripped.startswith('#'):
                # This is block scalar content - highlight entire line as value
                content_end = len(text.rstrip())
                if content_end > 0:
                    tokens.append((0, content_end, 'yaml_value'))
                # Stay in block scalar state
                self.state_chain.set_end_state(line_num, start_state)
                return tokens
            # Otherwise, we're out of the block scalar - fall through to normal parsing
        
        # Check for full-line patterns first
        
        # Document markers (--- and ...)
        m = P.DOC_START.match(text)
        if m:
            tokens.append((0, 3, 'yaml_doc_marker'))
            pos = 3
        
        m = P.DOC_END.match(text)
        if m:
            tokens.append((0, 3, 'yaml_doc_marker'))
            pos = 3
        
        # Directive (%YAML, %TAG)
        m = P.DIRECTIVE.match(text)
        if m:
            # Tokenize the directive keyword (%YAML or %TAG) as purple
            keyword = m.group(1)
            tokens.append((0, len(keyword), 'yaml_directive'))
            
            # Tokenize the rest (version number like 1.2) - find numbers in it
            rest = m.group(2)
            if rest:
                rest_start = len(keyword)
                # Find version numbers like 1.2 in the rest
                import re as re_mod
                for num_match in re_mod.finditer(r'[\d.]+', rest):
                    num_start = rest_start + num_match.start()
                    num_end = rest_start + num_match.end()
                    tokens.append((num_start, num_end, 'number'))
            
            # Store end state as ROOT (no multi-line for directives)
            self.state_chain.set_end_state(line_num, TokenState.ROOT)
            return tokens
        
        # Check for sequence item at start of line
        m = P.SEQUENCE_ITEM.match(text)
        if m:
            dash_pos = m.group(1)  # Indentation
            indent_len = len(dash_pos) if dash_pos else 0
            tokens.append((indent_len, indent_len + 1, 'yaml_sequence_indicator'))
            pos = max(pos, indent_len + 1)
        
        # Check for explicit key marker (?)
        m = P.EXPLICIT_KEY.match(text)
        if m:
            tokens.append((0, 1, 'yaml_explicit_key'))
            pos = max(pos, 1)
        
        # Check for map key at start of line
        m = P.MAP_KEY.match(text)
        if m:
            indent = m.group(1) or ''
            key = m.group(2) or ''
            space_before_colon = m.group(3) or ''
            key_start = len(indent)
            key_end = key_start + len(key)
            colon_pos = key_end + len(space_before_colon)
            
            if key:
                tokens.append((key_start, key_end, 'yaml_key'))
            tokens.append((colon_pos, colon_pos + 1, 'yaml_colon'))
            pos = max(pos, colon_pos + 1)
        
        while pos < length:
            ch = text[pos]
            
            # Skip whitespace
            if ch in ' \t':
                pos += 1
                continue
            
            # 1. Comments (highest priority after we're past structural elements)
            if ch == '#':
                # Make sure it's preceded by whitespace or at start
                if pos == 0 or text[pos-1] in ' \t':
                    tokens.append((pos, length, 'comment'))
                    break
            
            # 2. Anchors (&name)
            m = P.ANCHOR.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_anchor'))
                pos = m.end()
                continue
            
            # 3. Aliases (*name)
            m = P.ALIAS.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_alias'))
                pos = m.end()
                continue
            
            # 4. Tags (order matters - longest patterns first)
            m = P.TAG_VERBATIM.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_tag'))
                pos = m.end()
                continue
            
            m = P.TAG_NAMED.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_tag'))
                pos = m.end()
                continue
            
            m = P.TAG_SECONDARY.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_tag'))
                pos = m.end()
                continue
            
            m = P.TAG_PRIMARY.match(text, pos)
            if m and m.end() > pos:  # Must match at least one char
                tokens.append((pos, m.end(), 'yaml_tag'))
                pos = m.end()
                continue
            
            # 5. Double-quoted strings
            m = P.DOUBLE_QUOTED.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 6. Single-quoted strings
            m = P.SINGLE_QUOTED.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 7. Block scalar indicators (| or >)
            m = P.BLOCK_SCALAR.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_block_indicator'))
                pos = m.end()
                continue
            
            # 8. Flow collection brackets
            m = P.FLOW_MAP_START.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_flow_indicator'))
                pos = m.end()
                continue
            
            m = P.FLOW_MAP_END.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_flow_indicator'))
                pos = m.end()
                continue
            
            m = P.FLOW_SEQ_START.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_flow_indicator'))
                pos = m.end()
                continue
            
            m = P.FLOW_SEQ_END.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_flow_indicator'))
                pos = m.end()
                continue
            
            # 9. Merge key (<<)
            m = P.MERGE_KEY.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_merge'))
                pos = m.end()
                continue
            
            # 10. Null values
            m = P.NULL.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_null'))
                pos = m.end()
                continue
            
            # 11. Boolean values
            m = P.BOOLEAN.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'bool_ops'))
                pos = m.end()
                continue
            
            # 12. Timestamps (before general numbers)
            m = P.TIMESTAMP.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'yaml_timestamp'))
                pos = m.end()
                continue
            
            # 13. Special float values (before general floats)
            m = P.FLOAT_INF.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            m = P.FLOAT_NAN.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # 14. Hex/Octal/Binary integers (before decimal)
            m = P.INT_HEX.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            m = P.INT_OCTAL.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            m = P.INT_BINARY.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # 15. Sexagesimal numbers (YAML 1.1)
            m = P.SEXAGESIMAL_FLOAT.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            m = P.SEXAGESIMAL_INT.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # 16. Regular floats
            m = P.FLOAT.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # 17. Flow map keys (inside { })
            m = P.FLOW_MAP_KEY.match(text, pos)
            if m:
                tokens.append((pos, m.end(1), 'yaml_key'))
                pos = m.end(1)
                continue
            
            # 18. Plain (unquoted) value - capture remaining text as value
            # This captures: localhost, postgres, Syntax Test, etc.
            m = P.PLAIN_VALUE.match(text, pos)
            if m:
                # Check if there's a comment in the value and trim
                value_text = m.group(0)
                comment_pos = value_text.find(' #')
                if comment_pos > 0:
                    # Has inline comment, split
                    actual_value = value_text[:comment_pos].rstrip()
                    if actual_value:
                        tokens.append((pos, pos + len(actual_value), 'yaml_value'))
                    pos += comment_pos
                else:
                    # No inline comment, use full value (trimmed)
                    actual_value = value_text.rstrip()
                    if actual_value:
                        tokens.append((pos, pos + len(actual_value), 'yaml_value'))
                    pos = m.end()
                continue
            
            # No match - skip character
            pos += 1
        
        # Check if we detected a block scalar indicator (| or >)
        # Set state for next lines to be highlighted as values
        block_state = TokenState.ROOT
        for start, end, ttype in tokens:
            if ttype == 'yaml_block_indicator':
                block_state = TokenState.IN_YAML_BLOCK_LITERAL
                break
        
        self.state_chain.set_end_state(line_num, block_state)
        
        return tokens
    
    def _tokenize_simple(self, text: str) -> List[Tuple[int, int, str]]:
        """
        Simple fallback tokenizer for unsupported languages.
        Just returns empty tokens (no highlighting).
        """
        return []


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def detect_rtl_line(text: str) -> bool:
    """
    Detect if a line is RTL using Unicode bidirectional properties.
    
    Returns True if the first strong directional character is RTL,
    False if LTR, or False if no strong directional characters found.
    """
    for ch in text:
        t = unicodedata.bidirectional(ch)
        if t in ("L", "LRE", "LRO"):
            return False
        if t in ("R", "AL", "RLE", "RLO"):
            return True
    return False
