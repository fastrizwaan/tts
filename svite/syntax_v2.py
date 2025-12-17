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
        if not self._patterns or self.language != 'python':
            # Fallback for unsupported languages
            return self._tokenize_simple(text)
        
        return self._tokenize_python(line_num, text)
    
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
