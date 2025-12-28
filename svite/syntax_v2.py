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
    
    # XML states
    IN_XML_CDATA = 20           # Inside CDATA section
    IN_XML_COMMENT = 21         # Inside multi-line comment
    
    # HTML embedded language states
    IN_HTML_SCRIPT = 22         # Inside <script>...</script>
    IN_HTML_STYLE = 23          # Inside <style>...</style>
    
    # F-String Expression States
    IN_F_EXPR_SQ = 25           # Inside { ... } in f'...'
    IN_F_EXPR_DQ = 26           # Inside { ... } in f"..."
    IN_F_EXPR_TRIPLE_SQ = 27    # Inside { ... } in f'''...'''
    IN_F_EXPR_TRIPLE_DQ = 28    # Inside { ... } in f"""..."""
    
    @classmethod
    def is_string_state(cls, state: int) -> bool:
        """Check if state represents being inside a string."""
        return (state >= cls.IN_SQ_STRING and state <= cls.IN_R_TRIPLE_DQ) or (state >= 50 and state <= 65)
    
    @classmethod
    def is_triple_string(cls, state: int) -> bool:
        """Check if state represents being inside a triple-quoted string."""
        return state in (
            cls.IN_TRIPLE_SQ, cls.IN_TRIPLE_DQ,
            cls.IN_F_TRIPLE_SQ, cls.IN_F_TRIPLE_DQ,
            cls.IN_B_TRIPLE_SQ, cls.IN_B_TRIPLE_DQ,
            cls.IN_R_TRIPLE_SQ, cls.IN_R_TRIPLE_DQ
        ) or (state >= 50 and state <= 65 and (state - 50) % 4 >= 2)
    
    @classmethod
    def get_nested_state(cls, parent_state: int, string_type_idx: int) -> int:
        """Get the nested string state for a given parent f-expr state."""
        # Parent mapping: 25->0, 26->1, 27->2, 28->3
        if 25 <= parent_state <= 28:
            parent_idx = parent_state - 25
            return 50 + (parent_idx * 4) + string_type_idx
        return cls.ROOT # Should not happen

    @classmethod
    def get_nested_return_state(cls, state: int) -> int:
        """Get the parent state to return to from a nested string state."""
        if 50 <= state <= 65:
            parent_idx = (state - 50) // 4
            return 25 + parent_idx
        return cls.ROOT
    
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
        
        # Nested states
        if 50 <= state <= 65:
            type_idx = (state - 50) % 4
            if type_idx == 0: return "'"
            if type_idx == 1: return '"'
            if type_idx == 2: return "'''"
            if type_idx == 3: return '"""'
            
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
    
    # Scanner patterns for optimized string parsing
    SCAN_DQ = re.compile(r'[\\"]')
    SCAN_SQ = re.compile(r"[\\']")
    SCAN_F_DQ = re.compile(r'[\\"{}]')
    SCAN_F_SQ = re.compile(r"[\\'{}]")
    
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


class JsonPatterns:
    """Pre-compiled regex patterns for JSON syntax highlighting.
    
    JSON has a simpler structure than YAML:
    - Keys are always quoted strings
    - Values can be strings, numbers, booleans, null, objects, or arrays
    """
    
    # String (double-quoted only in JSON)
    STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
    STRING_UNTERMINATED = re.compile(r'"(?:[^"\\]|\\.)*$')
    
    # Numbers (integers and floats, with optional exponent)
    NUMBER = re.compile(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?')
    
    # Boolean values
    BOOLEAN = re.compile(r'\b(?:true|false)\b')
    
    # Null value
    NULL = re.compile(r'\bnull\b')
    
    # Structural characters
    OBJECT_START = re.compile(r'\{')
    OBJECT_END = re.compile(r'\}')
    ARRAY_START = re.compile(r'\[')
    ARRAY_END = re.compile(r'\]')
    COLON = re.compile(r':')
    COMMA = re.compile(r',')
    
    # Key pattern - string followed by colon (we'll use this to identify keys vs values)
    KEY_STRING = re.compile(r'"(?:[^"\\]|\\.)*"\s*(?=:)')


class JavaScriptPatterns:
    """Pre-compiled regex patterns for JavaScript syntax highlighting.
    
    Based on VSCode's JavaScript TextMate grammar.
    """
    
    # Keywords
    KEYWORD = re.compile(
        r'\b(async|await|break|case|catch|class|const|continue|debugger|default|'
        r'delete|do|else|export|extends|finally|for|from|function|get|if|import|'
        r'in|instanceof|let|new|of|return|set|static|super|switch|this|throw|try|'
        r'typeof|var|void|while|with|yield)\b'
    )
    
    # Boolean and null literals
    CONSTANT = re.compile(r'\b(true|false|null|undefined|NaN|Infinity)\b')
    
    # Built-in objects and constructors
    BUILTIN = re.compile(
        r'\b(Array|Boolean|Date|Error|Function|JSON|Map|Math|Number|Object|'
        r'Promise|Proxy|Reflect|RegExp|Set|String|Symbol|WeakMap|WeakSet|'
        r'console|document|window|global|module|exports|require|process)\b'
    )
    
    # Function definition: function name( or name(
    FUNCTION_DEF = re.compile(r'\b(function)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)')
    
    # Arrow function parameter
    ARROW_FUNC = re.compile(r'=>')
    
    # Method/function call: name(
    FUNCTION_CALL = re.compile(r'\b([a-zA-Z_$][a-zA-Z0-9_$]*)\s*(?=\()')
    
    # Numbers (integers, floats, hex, binary, octal, exponential)
    NUMBER = re.compile(
        r'\b(?:'
        r'0[xX][0-9a-fA-F]+|'  # Hex
        r'0[bB][01]+|'         # Binary
        r'0[oO][0-7]+|'        # Octal
        r'[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?|'  # Decimal/float
        r'\.[0-9]+(?:[eE][+-]?[0-9]+)?'          # .5 style
        r')\b'
    )
    
    # Strings (single and double quoted)
    STRING_DOUBLE = re.compile(r'"(?:[^"\\]|\\.)*"')
    STRING_SINGLE = re.compile(r"'(?:[^'\\]|\\.)*'")
    STRING_DOUBLE_UNTERM = re.compile(r'"(?:[^"\\]|\\.)*$')
    STRING_SINGLE_UNTERM = re.compile(r"'(?:[^'\\]|\\.)*$")
    
    # Template literal (backtick strings)
    TEMPLATE_START = re.compile(r'`')
    TEMPLATE_END = re.compile(r'`')
    TEMPLATE_EXPR_START = re.compile(r'\$\{')
    TEMPLATE_EXPR_END = re.compile(r'\}')
    
    # Comments
    COMMENT_LINE = re.compile(r'//.*$')
    COMMENT_BLOCK_START = re.compile(r'/\*')
    COMMENT_BLOCK_END = re.compile(r'\*/')
    
    # Regex literal - simplified matching
    REGEX = re.compile(r'/(?![/*])(?:[^/\\\n]|\\.)+/[gimsuvy]*')
    
    # Operators
    OPERATOR = re.compile(r'===|!==|==|!=|<=|>=|&&|\|\||<<|>>|>>>|\+\+|--|[+\-*/%&|^~<>!?:]')
    
    # Punctuation
    PAREN_OPEN = re.compile(r'\(')
    PAREN_CLOSE = re.compile(r'\)')
    BRACE_OPEN = re.compile(r'\{')
    BRACE_CLOSE = re.compile(r'\}')
    BRACKET_OPEN = re.compile(r'\[')
    BRACKET_CLOSE = re.compile(r'\]')
    SEMICOLON = re.compile(r';')
    COMMA = re.compile(r',')
    DOT = re.compile(r'\.')
    
    # Property access: .propertyName
    PROPERTY = re.compile(r'\.([a-zA-Z_$][a-zA-Z0-9_$]*)')
    
    # Variable/identifier
    IDENTIFIER = re.compile(r'[a-zA-Z_$][a-zA-Z0-9_$]*')


class XmlPatterns:
    """Pre-compiled regex patterns for XML/XSL syntax highlighting.
    
    Based on VSCode's XML TextMate grammar.
    """
    
    # Comments: <!-- ... -->
    COMMENT_START = re.compile(r'<!--')
    COMMENT_END = re.compile(r'-->')
    
    # CDATA: <![CDATA[ ... ]]>
    CDATA_START = re.compile(r'<!\[CDATA\[')
    CDATA_END = re.compile(r'\]\]>')
    
    # Processing instruction: <?xml ... ?>
    PI_START = re.compile(r'<\?')
    PI_END = re.compile(r'\?>')
    PI_TARGET = re.compile(r'<\?\s*([-_a-zA-Z0-9]+)')
    
    # DOCTYPE: <!DOCTYPE ...>
    DOCTYPE = re.compile(r'<!DOCTYPE\b')
    
    # Tag patterns
    # Opening tag: <tagname or <ns:tagname
    TAG_OPEN = re.compile(r'<(?![-!?/])(?:([-\w.]+)(:))?([-\w.:]+)')
    # Closing tag: </tagname>
    TAG_CLOSE = re.compile(r'</(?:([-\w.]+)(:))?([-\w.:]+)\s*>')
    # Self-closing end: />
    TAG_SELF_CLOSE = re.compile(r'/>')
    # Tag end: >
    TAG_END = re.compile(r'>')
    # Tag start bracket
    TAG_BRACKET_OPEN = re.compile(r'</?')
    TAG_BRACKET_CLOSE = re.compile(r'/?>') 
    
    # Attributes: name="value" or name='value'
    # Match attribute name with optional namespace prefix
    ATTRIBUTE_NAME = re.compile(r'\s*([-\w.]+(?::[-\w.]+)?)\s*=')
    
    # Strings (attribute values)
    DOUBLE_QUOTED = re.compile(r'"[^"]*"')
    SINGLE_QUOTED = re.compile(r"'[^']*'")
    
    # Entity references: &amp; &#123; &#x1F;
    ENTITY = re.compile(r'&(?:[:a-zA-Z_][:a-zA-Z0-9_.-]*|#[0-9]+|#x[0-9a-fA-F]+);')
    
    # Equal sign
    EQUALS = re.compile(r'=')


# =============================================================================
# STATE-AWARE SYNTAX ENGINE
# =============================================================================


class BashPatterns:
    """Pre-compiled regex patterns for Bash/Shell syntax highlighting."""
    
    # Comments
    COMMENT = re.compile(r'#.*$')
    
    # Assignments (NAME= or export NAME=)
    # Match LHS variable name followed by =
    # No ^ anchor - we check position manually in tokenizer
    ASSIGNMENT = re.compile(r'([a-zA-Z_]\w*)(?=\=)')
    
    # Function Definition: name() { or function name {
    FUNCTION_DEF = re.compile(r'([a-zA-Z_]\w*)(?=\s*\(\))')
    FUNCTION_DEF_KEYWORD = re.compile(r'(function)\s+([a-zA-Z_]\w*)')
    
    # Switches/Flags (-f, --help, -1)
    SWITCH = re.compile(r'(-[a-zA-Z0-9-]+)')
    
    # Strings
    DOUBLE_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
    SINGLE_QUOTED = re.compile(r"'(?:[^']|'\\''[^']*)*'") 
    STRICT_SINGLE_QUOTED = re.compile(r"'[^']*'") 
    
    DOUBLE_QUOTED_START = re.compile(r'"(?:[^"\\]|\\.)*$')
    STRICT_SINGLE_QUOTED_START = re.compile(r"'[^']*$")
    
    # Backticks
    BACKTICK_QUOTED = re.compile(r'`[^`]*`')
    
    # Keywords
    # Use strict lookahead to avoid matching start of hyphenated words (e.g. if-else)
    KEYWORD = re.compile(
        r'\b(if|then|else|elif|fi|case|esac|for|select|while|until|do|done|in|'
        r'function|time|coproc|declare|typeset|local|readonly|export|unset|'
        r'set|shopt|trap|source|alias|unalias|break|continue|return|exit|eval|exec)(?![a-zA-Z0-9_-])'
    )
    
    # Builtins/Common Commands (subset)
    COMMAND = re.compile(
        r'\b(echo|printf|cd|pwd|ls|cp|mv|rm|mkdir|rmdir|touch|cat|grep|sed|awk|'
        r'find|chmod|chown|kill|ps|jobs|bg|fg|history|read|wait|sleep|true|false)(?![a-zA-Z0-9_-])'
    )
    
    # Variables
    # $VAR, ${VAR}, $1, $?
    VARIABLE = re.compile(r'\$(\w+|{[:#]?\w+(?:[^\}]*)?}|[0-9*@#?!$-])')
    
    # Test operators
    TEST_OP = re.compile(r'(-(?:eq|ne|gt|lt|ge|le|a|o|f|d|e|s|L|h|r|w|x|n|z))(?![a-zA-Z0-9_-])')
    
    # Redirection & Pipes
    OPERATOR = re.compile(r'\|&?|>>?|<<[-<]?|&&|\|\||;;|!|\*|[(){};]|\$\(')
    
    # Numeric
    NUMBER = re.compile(r'\b\d+\b')
    
    # Escape sequences
    ESCAPE = re.compile(r'\\.')
    
    # Generic Word (for command/arg fallback)
    # Allow alphanumeric, underscore, dot, colon, slash, hyphen (but hyphen at start handled by SWITCH)
    GENERIC_WORD = re.compile(r'[a-zA-Z0-9_./:][a-zA-Z0-9_./:-]*')


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
        elif self.language == 'json':
            self._patterns = JsonPatterns
        elif self.language in ('xml', 'xsl', 'html'):
            self._patterns = XmlPatterns
        elif self.language in ('javascript', 'js'):
            self._patterns = JavaScriptPatterns
        elif self.language in ('bash', 'sh', 'shell', 'zsh'):
            self._patterns = BashPatterns
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
        elif self.language == 'json':
            return self._tokenize_json(line_num, text)
        elif self.language in ('xml', 'xsl', 'html'):
            return self._tokenize_xml(line_num, text)
        elif self.language in ('javascript', 'js'):
            return self._tokenize_javascript(line_num, text)
        elif self.language in ('bash', 'sh', 'shell', 'zsh'):
            return self._tokenize_bash(line_num, text)
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
            is_f_expr = state in (
                TokenState.IN_F_EXPR_SQ, TokenState.IN_F_EXPR_DQ,
                TokenState.IN_F_EXPR_TRIPLE_SQ, TokenState.IN_F_EXPR_TRIPLE_DQ
            )
            
            if is_f_expr:
                 # Check for closing brace (end of expression)
                if text.startswith('}', pos):
                    tokens.append((pos, pos + 1, 'f_expression_end'))
                    
                    # Return to appropriate string state
                    if state == TokenState.IN_F_EXPR_SQ:
                        state = TokenState.IN_F_SQ_STRING
                    elif state == TokenState.IN_F_EXPR_DQ:
                        state = TokenState.IN_F_DQ_STRING
                    elif state == TokenState.IN_F_EXPR_TRIPLE_SQ:
                        state = TokenState.IN_F_TRIPLE_SQ
                    elif state == TokenState.IN_F_EXPR_TRIPLE_DQ:
                        state = TokenState.IN_F_TRIPLE_DQ
                    
                    pos += 1
                    continue

            if state == TokenState.ROOT or is_f_expr:
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
                    new_state = TokenState.IN_TRIPLE_DQ
                    if is_f_expr:
                         state = TokenState.get_nested_state(state, 3) # 3 = TRIPLE_DQ
                    else:
                         state = new_state
                    pos = m.end()
                    continue
                
                m = P.U_TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    new_state = TokenState.IN_TRIPLE_SQ
                    if is_f_expr:
                         state = TokenState.get_nested_state(state, 2) # 2 = TRIPLE_SQ
                    else:
                         state = new_state
                    pos = m.end()
                    continue
                
                # Plain triple-quoted
                m = P.TRIPLE_DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    new_state = TokenState.IN_TRIPLE_DQ
                    if is_f_expr:
                         state = TokenState.get_nested_state(state, 3)
                    else:
                         state = new_state
                    pos = m.end()
                    continue
                
                m = P.TRIPLE_SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'triple_start'))
                    new_state = TokenState.IN_TRIPLE_SQ
                    if is_f_expr:
                         state = TokenState.get_nested_state(state, 2)
                    else:
                         state = new_state
                    pos = m.end()
                    continue
                
                # 3. Single-quoted strings
                # f-strings (nested f inside f? Allowed but complex. Let's start with basic nesting support)
                # Actually python allows f"{f'nested'}" 
                # For now let's treat prefixed strings in nested expr as standard structure if possible
                # But our current logic for F/B/R doesn't support nested variants yet (requires more states: 16 * 4 prefixes?)
                # Simplification: Treat prefixed strings inside F-Expr as standard strings (losing prefix features) 
                # OR just support plain strings for now which covers 90% of cases like dict keys.
                # Let's support standard strings correctly first.
                
                # Plain strings
                m = P.DQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_start'))
                    new_state = TokenState.IN_DQ_STRING
                    if is_f_expr:
                         state = TokenState.get_nested_state(state, 1) # 1 = DQ
                    else:
                         state = new_state
                    pos = m.end()
                    continue
                
                m = P.SQ.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_start'))
                    new_state = TokenState.IN_SQ_STRING
                    if is_f_expr:
                         state = TokenState.get_nested_state(state, 0) # 0 = SQ
                    else:
                         state = new_state
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
            
            elif state in (TokenState.IN_F_EXPR_SQ, TokenState.IN_F_EXPR_DQ, 
                           TokenState.IN_F_EXPR_TRIPLE_SQ, TokenState.IN_F_EXPR_TRIPLE_DQ):
                # Inside F-String Expression - parse as normal python unless } found
                
                # Check for closing brace (end of expression)
                if text.startswith('}', pos):
                    tokens.append((pos, pos + 1, 'f_expression_end'))
                    
                    # Return to appropriate string state
                    if state == TokenState.IN_F_EXPR_SQ:
                        state = TokenState.IN_F_SQ_STRING
                    elif state == TokenState.IN_F_EXPR_DQ:
                        state = TokenState.IN_F_DQ_STRING
                    elif state == TokenState.IN_F_EXPR_TRIPLE_SQ:
                        state = TokenState.IN_F_TRIPLE_SQ
                    elif state == TokenState.IN_F_EXPR_TRIPLE_DQ:
                        state = TokenState.IN_F_TRIPLE_DQ
                    
                    pos += 1
                    continue
                
                # Temporarily switch state to ROOT to reuse logic
                # But we must be careful not to allow comments or triple strings that might consume too much?
                # Actually, expressions can contain strings.
                # We reuse the logic by running one iteration of ROOT logic manually-ish
                # Or just let it flow since 'state' variable is local to loop?
                # But loop checks `if state == TokenState.ROOT`.
                # We make `state` ROOT for the check, but we need to know we are in EXPR for the `}` check.
                # Easier: Copy-paste/Abstract the ROOT logic or make ROOT a set of states.
                
                # Refactored: Treat EXPR states as ROOT-like but with extra `}` check
                
                # 1. Check for `}` (Already done above)
                
                # 2. Run standard ROOT matchers
                # We set a flag to break loop if match found, to avoid duplication code
                
                # ... OR we just copy the ROOT block logic since it's cleaner than refactoring whole method now.
                # Actually, there's a lot of logic.
                
                # Let's change the top condition:
                # if state == TokenState.ROOT or state in (IN_F_EXPR...):
                
                # But we need to handle `}` priority correctly.
                # `}` is not matched by ROOT patterns except as error or unexpected?
                # Actually, `}` is NOT a python operator in the regex list?
                # OPERATORS regex? Not in PythonPatterns list.
                # So `}` would usually be skipped by ROOT looper as unknown char.
                
                # So, modify top 'if' to include EXPR states.
                # BUT we need to check `}` first.
                
                # Hack: Just change valid states for the main block
                # And inside the block, handle `}` for EXPR states.
                pass # Logic handled by modifying the loop structure below

        
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
        is_f_string = state in (
            TokenState.IN_F_SQ_STRING, TokenState.IN_F_DQ_STRING,
            TokenState.IN_F_TRIPLE_SQ, TokenState.IN_F_TRIPLE_DQ
        )
        
        # Select scanner pattern for fast jumping
        scan_pat = None
        if is_f_string:
            if '"""' in delimiter or '"' in delimiter:
                scan_pat = P.SCAN_F_DQ
            else:
                scan_pat = P.SCAN_F_SQ
        else:
            if '"""' in delimiter or '"' in delimiter:
                scan_pat = P.SCAN_DQ
            else:
                scan_pat = P.SCAN_SQ

        while pos < length:
            # Fast scan to next interesting character
            m_scan = scan_pat.search(text, pos)
            if m_scan:
                # Jump to the interesting character
                pos = m_scan.start()
            else:
                # No more interesting characters, assume rest is content
                pos = length
                break

            # Check for F-String expressions
            if is_f_string:
                # Check for double braces (escape)
                if text.startswith('{{', pos):
                    pos += 2
                    continue
                if text.startswith('}}', pos):
                    pos += 2
                    continue
                
                # Check for start of expression
                if text.startswith('{', pos):
                    # End current string token
                    if pos > content_start:
                        tokens.append((content_start, pos, token_type))
                    
                    # Emit punctuation for brace
                    tokens.append((pos, pos + 1, 'f_expression_start'))
                    
                    # Determine new expression state based on current string type
                    new_state = TokenState.IN_F_EXPR_SQ
                    if state == TokenState.IN_F_DQ_STRING:
                        new_state = TokenState.IN_F_EXPR_DQ
                    elif state == TokenState.IN_F_TRIPLE_SQ:
                        new_state = TokenState.IN_F_EXPR_TRIPLE_SQ
                    elif state == TokenState.IN_F_TRIPLE_DQ:
                        new_state = TokenState.IN_F_EXPR_TRIPLE_DQ
                        
                    return tokens, pos + 1, new_state

            # Look for end delimiter
            if is_triple:
                if delimiter == '"""':
                    m = P.END_TRIPLE_DQ.match(text, pos)
                else:
                    m = P.END_TRIPLE_SQ.match(text, pos)
            elif is_raw:
                # Raw strings need lookbehind to avoid escaped quotes
                if delimiter == '"':
                    m = P.END_DQ.match(text, pos)
                else:
                    m = P.END_SQ.match(text, pos)
            else:
                # Normal strings: escapes processed consumed manually below
                # So we just match the quote (if we reached here, it's not escaped)
                if delimiter == '"':
                    m = P.DQ.match(text, pos)
                else:
                    m = P.SQ.match(text, pos)
            
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
                
                if 'f_' in token_type:
                    end_token = 'f_string'
                elif 'b_' in token_type or 'byte' in token_type:
                    end_token = 'byte_string'
                elif 'r_' in token_type or 'raw' in token_type:
                    end_token = 'raw_string'
                
                tokens.append((pos, m.end(), end_token))
                
                # Check for return to nested state
                return_state = TokenState.get_nested_return_state(state)
                return tokens, m.end(), return_state
            
            # Check for escape sequence (except in raw strings)
            if not is_raw:
                m = P.ESCAPE.match(text, pos)
                if m:
                    pos = m.end()
                    continue
            
            # No special match matched (e.g. single quote inside double string, or single brace not starting expr)
            # Advance one character to bypass the scanner hit
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
    
    def _tokenize_json(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize JSON content.
        
        JSON is simpler than YAML:
        - Keys are always quoted strings followed by colon
        - Values can be strings, numbers, booleans, null, objects, or arrays
        """
        P = self._patterns
        tokens = []
        pos = 0
        length = len(text)
        
        while pos < length:
            ch = text[pos]
            
            # Skip whitespace
            if ch in ' \t\n\r':
                pos += 1
                continue
            
            # 1. Key string (string followed by colon) - highlight as json_key
            m = P.KEY_STRING.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_key'))
                pos = m.end()
                continue
            
            # 2. Regular string (value) - highlight as string
            m = P.STRING.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 3. Colon
            m = P.COLON.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_colon'))
                pos = m.end()
                continue
            
            # 4. Boolean values
            m = P.BOOLEAN.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'bool_ops'))
                pos = m.end()
                continue
            
            # 5. Null value
            m = P.NULL.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_null'))
                pos = m.end()
                continue
            
            # 6. Numbers
            m = P.NUMBER.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # 7. Structural characters (brackets, braces, comma)
            m = P.OBJECT_START.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_bracket'))
                pos = m.end()
                continue
            
            m = P.OBJECT_END.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_bracket'))
                pos = m.end()
                continue
            
            m = P.ARRAY_START.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_bracket'))
                pos = m.end()
                continue
            
            m = P.ARRAY_END.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_bracket'))
                pos = m.end()
                continue
            
            m = P.COMMA.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'json_comma'))
                pos = m.end()
                continue
            
            # No match - skip character
            pos += 1
        
        # JSON doesn't need multi-line state tracking
        self.state_chain.set_end_state(line_num, TokenState.ROOT)
        
        return tokens
    
    def _tokenize_javascript(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize JavaScript code.
        
        Handles keywords, builtins, strings, comments, numbers, operators,
        functions, template literals.
        """
        P = self._patterns
        tokens = []
        pos = 0
        length = len(text)
        
        # Check for multi-line block comment continuation
        start_state = self.state_chain.get_start_state(line_num)
        if start_state == TokenState.IN_ML_COMMENT:
            end_match = P.COMMENT_BLOCK_END.search(text)
            if end_match:
                tokens.append((0, end_match.end(), 'comment'))
                pos = end_match.end()
            else:
                tokens.append((0, length, 'comment'))
                self.state_chain.set_end_state(line_num, TokenState.IN_ML_COMMENT)
                return tokens
        
        while pos < length:
            ch = text[pos]
            
            # Skip whitespace
            if ch in ' \t\n\r':
                pos += 1
                continue
            
            # 1. Line comment: //
            m = P.COMMENT_LINE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'comment'))
                pos = m.end()
                continue
            
            # 2. Block comment start: /*
            m = P.COMMENT_BLOCK_START.match(text, pos)
            if m:
                end_match = P.COMMENT_BLOCK_END.search(text, m.end())
                if end_match:
                    tokens.append((pos, end_match.end(), 'comment'))
                    pos = end_match.end()
                else:
                    tokens.append((pos, length, 'comment'))
                    self.state_chain.set_end_state(line_num, TokenState.IN_ML_COMMENT)
                    return tokens
                continue
            
            # 3. Double-quoted string
            m = P.STRING_DOUBLE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 4. Single-quoted string
            m = P.STRING_SINGLE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 5. Template literal (backtick)
            if ch == '`':
                # Find matching closing backtick (simplified - no nesting)
                end_pos = text.find('`', pos + 1)
                if end_pos >= 0:
                    tokens.append((pos, end_pos + 1, 'string'))
                    pos = end_pos + 1
                else:
                    tokens.append((pos, length, 'string'))
                    pos = length
                continue
            
            # 6. Function definition
            m = P.FUNCTION_DEF.match(text, pos)
            if m:
                tokens.append((pos, pos + len('function'), 'keywords'))
                func_name = m.group(2)
                name_start = text.find(func_name, pos + len('function'))
                if name_start >= 0:
                    tokens.append((name_start, name_start + len(func_name), 'function'))
                pos = m.end()
                continue
            
            # 7. Keywords
            m = P.KEYWORD.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'keywords'))
                pos = m.end()
                continue
            
            # 8. Constants (true, false, null, undefined)
            m = P.CONSTANT.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'bool_ops'))
                pos = m.end()
                continue
            
            # 9. Built-in objects
            m = P.BUILTIN.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'builtins'))
                pos = m.end()
                continue
            
            # 10. Numbers
            m = P.NUMBER.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # 11. Arrow function
            m = P.ARROW_FUNC.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'keywords'))
                pos = m.end()
                continue
            
            # 12. Function call: name(
            m = P.FUNCTION_CALL.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'function'))
                pos = m.end()
                continue
            
            # 13. Operators
            m = P.OPERATOR.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'operator'))
                pos = m.end()
                continue
            
            # 14. Property access: .name
            m = P.PROPERTY.match(text, pos)
            if m:
                tokens.append((pos, pos + 1, 'operator'))  # dot
                prop_name = m.group(1)
                tokens.append((pos + 1, pos + 1 + len(prop_name), 'identifier'))
                pos = m.end()
                continue
            
            # 15. Punctuation (skip but don't highlight)
            if ch in '(){}[];,:':
                pos += 1
                continue
            
            # 16. Identifier
            m = P.IDENTIFIER.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'identifier'))
                pos = m.end()
                continue
            
            # No match - skip character
            pos += 1
        
        # Set end state
        self.state_chain.set_end_state(line_num, TokenState.ROOT)
        
        return tokens
    
    def _tokenize_javascript_inline(self, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize JavaScript without state tracking - for embedded script content.
        Returns tokens for a single line/segment.
        """
        P = JavaScriptPatterns
        tokens = []
        pos = 0
        length = len(text)
        
        while pos < length:
            ch = text[pos]
            
            # Skip whitespace
            if ch in ' \t\n\r':
                pos += 1
                continue
            
            # Line comment
            m = P.COMMENT_LINE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'comment'))
                pos = m.end()
                continue
            
            # Block comment (simplified - no multi-line tracking)
            m = P.COMMENT_BLOCK_START.match(text, pos)
            if m:
                end_match = P.COMMENT_BLOCK_END.search(text, m.end())
                if end_match:
                    tokens.append((pos, end_match.end(), 'comment'))
                    pos = end_match.end()
                else:
                    tokens.append((pos, length, 'comment'))
                    pos = length
                continue
            
            # Double-quoted string
            m = P.STRING_DOUBLE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # Single-quoted string
            m = P.STRING_SINGLE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # Template literal
            if ch == '`':
                end_pos = text.find('`', pos + 1)
                if end_pos >= 0:
                    tokens.append((pos, end_pos + 1, 'string'))
                    pos = end_pos + 1
                else:
                    tokens.append((pos, length, 'string'))
                    pos = length
                continue
            
            # Function definition
            m = P.FUNCTION_DEF.match(text, pos)
            if m:
                tokens.append((pos, pos + len('function'), 'keywords'))
                func_name = m.group(2)
                name_start = text.find(func_name, pos + len('function'))
                if name_start >= 0:
                    tokens.append((name_start, name_start + len(func_name), 'function'))
                pos = m.end()
                continue
            
            # Keywords
            m = P.KEYWORD.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'keywords'))
                pos = m.end()
                continue
            
            # Constants
            m = P.CONSTANT.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'bool_ops'))
                pos = m.end()
                continue
            
            # Built-ins
            m = P.BUILTIN.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'builtins'))
                pos = m.end()
                continue
            
            # Numbers
            m = P.NUMBER.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'number'))
                pos = m.end()
                continue
            
            # Arrow function
            m = P.ARROW_FUNC.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'keywords'))
                pos = m.end()
                continue
            
            # Function call
            m = P.FUNCTION_CALL.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'function'))
                pos = m.end()
                continue
            
            # Operators
            m = P.OPERATOR.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'operator'))
                pos = m.end()
                continue
            
            # Property access
            m = P.PROPERTY.match(text, pos)
            if m:
                tokens.append((pos, pos + 1, 'operator'))
                prop_name = m.group(1)
                tokens.append((pos + 1, pos + 1 + len(prop_name), 'identifier'))
                pos = m.end()
                continue
            
            # Punctuation
            if ch in '(){}[];,:':
                pos += 1
                continue
            
            # Identifier
            m = P.IDENTIFIER.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'identifier'))
                pos = m.end()
                continue
            
            pos += 1
        
        return tokens
    
    def _tokenize_xml(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize XML/XSL content.
        
        Handles tags, attributes, comments, CDATA, processing instructions,
        DOCTYPE, and entity references. Supports multi-line CDATA and comments.
        """
        P = self._patterns
        tokens = []
        pos = 0
        length = len(text)
        
        # Check if we're continuing from a multi-line state
        start_state = self.state_chain.get_start_state(line_num)
        
        # Handle CDATA continuation
        if start_state == TokenState.IN_XML_CDATA:
            end_match = P.CDATA_END.search(text)
            if end_match:
                # CDATA content before ]]>
                if end_match.start() > 0:
                    tokens.append((0, end_match.start(), 'string'))
                tokens.append((end_match.start(), end_match.end(), 'xml_cdata_end'))
                pos = end_match.end()
                # Continue normal parsing after CDATA ends
            else:
                # Still inside CDATA - entire line is string content
                tokens.append((0, length, 'string'))
                self.state_chain.set_end_state(line_num, TokenState.IN_XML_CDATA)
                return tokens
        
        # Handle multi-line comment continuation
        if start_state == TokenState.IN_XML_COMMENT:
            end_match = P.COMMENT_END.search(text)
            if end_match:
                tokens.append((0, end_match.end(), 'comment'))
                pos = end_match.end()
            else:
                tokens.append((0, length, 'comment'))
                self.state_chain.set_end_state(line_num, TokenState.IN_XML_COMMENT)
                return tokens
        
        # Handle script tag continuation - use JavaScript tokenizer
        if start_state == TokenState.IN_HTML_SCRIPT:
            # Look for closing </script> tag
            close_match = re.search(r'</script\s*>', text, re.IGNORECASE)
            if close_match:
                # Tokenize JavaScript content before </script>
                if close_match.start() > 0:
                    js_tokens = self._tokenize_javascript_inline(text[:close_match.start()])
                    tokens.extend(js_tokens)
                # Tokenize the closing tag
                tokens.append((close_match.start(), close_match.start() + 2, 'xml_bracket'))  # </
                tokens.append((close_match.start() + 2, close_match.end() - 1, 'xml_tag'))  # script
                tokens.append((close_match.end() - 1, close_match.end(), 'xml_bracket'))  # >
                pos = close_match.end()
                # Continue with normal XML parsing
            else:
                # Still inside script - tokenize entire line as JavaScript
                js_tokens = self._tokenize_javascript_inline(text)
                tokens.extend(js_tokens)
                self.state_chain.set_end_state(line_num, TokenState.IN_HTML_SCRIPT)
                return tokens
        
        while pos < length:
            ch = text[pos]
            
            # Skip whitespace
            if ch in ' \t\n\r':
                pos += 1
                continue
            
            # 1. Comment start: <!--
            m = P.COMMENT_START.match(text, pos)
            if m:
                # Find end of comment
                end_match = P.COMMENT_END.search(text, m.end())
                if end_match:
                    tokens.append((pos, end_match.end(), 'comment'))
                    pos = end_match.end()
                else:
                    # Comment continues to next line
                    tokens.append((pos, length, 'comment'))
                    self.state_chain.set_end_state(line_num, TokenState.IN_XML_COMMENT)
                    return tokens
                continue
            
            # 2. CDATA: <![CDATA[ ... ]]>
            m = P.CDATA_START.match(text, pos)
            if m:
                end_match = P.CDATA_END.search(text, m.end())
                if end_match:
                    tokens.append((pos, m.end(), 'xml_cdata_start'))
                    if end_match.start() > m.end():
                        tokens.append((m.end(), end_match.start(), 'string'))
                    tokens.append((end_match.start(), end_match.end(), 'xml_cdata_end'))
                    pos = end_match.end()
                else:
                    # CDATA continues to next line
                    tokens.append((pos, m.end(), 'xml_cdata_start'))
                    if m.end() < length:
                        tokens.append((m.end(), length, 'string'))
                    self.state_chain.set_end_state(line_num, TokenState.IN_XML_CDATA)
                    return tokens
                continue
            
            # 3. Processing instruction: <?xml ... ?>
            m = P.PI_TARGET.match(text, pos)
            if m:
                # Highlight <? and target name
                tokens.append((pos, pos + 2, 'xml_pi_bracket'))
                tokens.append((pos + 2, m.end(), 'xml_pi_target'))
                pos = m.end()
                
                # Find ?>
                pi_end = P.PI_END.search(text, pos)
                if pi_end:
                    # Parse attributes in between
                    inner_text = text[pos:pi_end.start()]
                    inner_pos = 0
                    while inner_pos < len(inner_text):
                        # Attribute name
                        attr_m = P.ATTRIBUTE_NAME.match(inner_text, inner_pos)
                        if attr_m:
                            # group(1) is the attribute name
                            attr_name = attr_m.group(1)
                            if attr_name:
                                name_start = inner_text.find(attr_name, inner_pos)
                                if name_start >= 0:
                                    tokens.append((pos + name_start, pos + name_start + len(attr_name), 'xml_attribute'))
                            inner_pos = attr_m.end()
                            continue
                        
                        # String value
                        dq_m = P.DOUBLE_QUOTED.match(inner_text, inner_pos)
                        if dq_m:
                            tokens.append((pos + dq_m.start(), pos + dq_m.end(), 'string'))
                            inner_pos = dq_m.end()
                            continue
                        
                        sq_m = P.SINGLE_QUOTED.match(inner_text, inner_pos)
                        if sq_m:
                            tokens.append((pos + sq_m.start(), pos + sq_m.end(), 'string'))
                            inner_pos = sq_m.end()
                            continue
                        
                        inner_pos += 1
                    
                    tokens.append((pi_end.start(), pi_end.end(), 'xml_pi_bracket'))
                    pos = pi_end.end()
                continue
            
            # 4. DOCTYPE: <!DOCTYPE ...>
            m = P.DOCTYPE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'xml_doctype'))
                pos = m.end()
                continue
            
            # 5. Closing tag: </tagname>
            m = P.TAG_CLOSE.match(text, pos)
            if m:
                tokens.append((pos, pos + 2, 'xml_bracket'))  # </
                # Namespace
                if m.group(1):
                    ns_start = pos + 2
                    tokens.append((ns_start, ns_start + len(m.group(1)), 'xml_tag'))
                    tokens.append((ns_start + len(m.group(1)), ns_start + len(m.group(1)) + 1, 'xml_bracket'))  # :
                # Tag name
                tag_name = m.group(3)
                tag_start = text.find(tag_name, pos + 2)
                if tag_start >= 0:
                    tokens.append((tag_start, tag_start + len(tag_name), 'xml_tag'))
                tokens.append((m.end() - 1, m.end(), 'xml_bracket'))  # >
                pos = m.end()
                continue
            
            # 6. Opening tag: <tagname ...>
            m = P.TAG_OPEN.match(text, pos)
            if m:
                tokens.append((pos, pos + 1, 'xml_bracket'))  # <
                # Namespace
                if m.group(1):
                    ns_start = pos + 1
                    tokens.append((ns_start, ns_start + len(m.group(1)), 'xml_tag'))
                    tokens.append((ns_start + len(m.group(1)), ns_start + len(m.group(1)) + 1, 'xml_bracket'))  # :
                # Tag name
                tag_name = m.group(3)
                tag_start = text.find(tag_name, pos + 1)
                if tag_start >= 0:
                    tokens.append((tag_start, tag_start + len(tag_name), 'xml_tag'))
                
                # Check for script or style tag (HTML only)
                if self.language == 'html' and tag_name.lower() == 'script':
                    # Find the > that closes the opening tag
                    tag_end = text.find('>', m.end())
                    if tag_end >= 0:
                        # Tokenize attributes before >
                        attr_text = text[m.end():tag_end]
                        attr_pos = 0
                        while attr_pos < len(attr_text):
                            attr_m = P.ATTRIBUTE_NAME.match(attr_text, attr_pos)
                            if attr_m:
                                attr_name = attr_m.group(1)
                                if attr_name:
                                    name_real_start = text.find(attr_name, m.end() + attr_pos)
                                    if name_real_start >= 0:
                                        tokens.append((name_real_start, name_real_start + len(attr_name), 'xml_attribute'))
                                attr_pos = attr_m.end()
                                continue
                            dq_m = P.DOUBLE_QUOTED.match(attr_text, attr_pos)
                            if dq_m:
                                tokens.append((m.end() + dq_m.start(), m.end() + dq_m.end(), 'string'))
                                attr_pos = dq_m.end()
                                continue
                            sq_m = P.SINGLE_QUOTED.match(attr_text, attr_pos)
                            if sq_m:
                                tokens.append((m.end() + sq_m.start(), m.end() + sq_m.end(), 'string'))
                                attr_pos = sq_m.end()
                                continue
                            attr_pos += 1
                        
                        tokens.append((tag_end, tag_end + 1, 'xml_bracket'))  # >
                        
                        # Check if </script> is on this same line
                        close_match = re.search(r'</script\s*>', text[tag_end + 1:], re.IGNORECASE)
                        if close_match:
                            js_start = tag_end + 1
                            js_end = tag_end + 1 + close_match.start()
                            if js_end > js_start:
                                js_tokens = self._tokenize_javascript_inline(text[js_start:js_end])
                                for s, e, t in js_tokens:
                                    tokens.append((js_start + s, js_start + e, t))
                            # Tokenize closing </script>
                            close_abs = tag_end + 1 + close_match.start()
                            tokens.append((close_abs, close_abs + 2, 'xml_bracket'))  # </
                            tokens.append((close_abs + 2, close_abs + close_match.end() - close_match.start() - 1, 'xml_tag'))  # script
                            tokens.append((tag_end + 1 + close_match.end() - 1, tag_end + 1 + close_match.end(), 'xml_bracket'))  # >
                            pos = tag_end + 1 + close_match.end()
                        else:
                            # Script continues to next line
                            self.state_chain.set_end_state(line_num, TokenState.IN_HTML_SCRIPT)
                            pos = length
                            return tokens
                    else:
                        pos = m.end()
                    continue
                
                pos = m.end()
                continue
            
            # 7. Self-closing end: />
            m = P.TAG_SELF_CLOSE.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'xml_bracket'))
                pos = m.end()
                continue
            
            # 8. Tag end: >
            m = P.TAG_END.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'xml_bracket'))
                pos = m.end()
                continue
            
            # 9. Attribute name (pattern: \s*name\s*=)
            m = P.ATTRIBUTE_NAME.match(text, pos)
            if m:
                # group(1) is the attribute name (including namespace if present)
                attr_name = m.group(1)
                if attr_name:
                    # Find where the name actually starts (after leading whitespace)
                    name_start = text.find(attr_name, pos)
                    if name_start >= 0:
                        tokens.append((name_start, name_start + len(attr_name), 'xml_attribute'))
                pos = m.end()
                continue
            
            # 10. Equal sign
            m = P.EQUALS.match(text, pos)
            if m:
                pos = m.end()
                continue
            
            # 11. Double-quoted string
            m = P.DOUBLE_QUOTED.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 12. Single-quoted string
            m = P.SINGLE_QUOTED.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'string'))
                pos = m.end()
                continue
            
            # 13. Entity reference: &amp;
            m = P.ENTITY.match(text, pos)
            if m:
                tokens.append((pos, m.end(), 'xml_entity'))
                pos = m.end()
                continue
            
            # No match - skip character
            pos += 1
        
        # XML doesn't need complex multi-line state for basic highlighting
        self.state_chain.set_end_state(line_num, TokenState.ROOT)
        
        return tokens
    
    def _tokenize_bash(self, line_num: int, text: str) -> List[Tuple[int, int, str]]:
        """
        Tokenize Bash/Shell scripts.
        """
        P = self._patterns
        tokens = []
        state = self.state_chain.get_start_state(line_num)
        pos = 0
        length = len(text)
        
        # Command Position States
        # Command Position States
        CMD = 0
        ARG = 1
        ASSIGN_VAL = 2
        LIST = 3 # For 'in' lists
        
        cmd_state = CMD

        while pos < length:
            if state == TokenState.ROOT:
                # 1. Comments
                m = P.COMMENT.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'comment'))
                    pos = m.end()
                    continue

                # Check for escape sequence (outside string)
                m = P.ESCAPE.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'escape'))
                    pos = m.end()
                    continue
                
                # 2. Strings
                # Double Quote
                if text[pos] == '"':
                    tokens.append((pos, pos + 1, 'string'))
                    state = TokenState.IN_DQ_STRING
                    pos += 1
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                    
                # Single Quote
                m = P.STRICT_SINGLE_QUOTED.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_single'))
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                m = P.STRICT_SINGLE_QUOTED_START.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_single'))
                    state = TokenState.IN_SQ_STRING
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                
                # Backticks
                m = P.BACKTICK_QUOTED.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'string_interpolated'))
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue

                # 3. Variables
                m = P.VARIABLE.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'variable'))
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                
                # 4. Assignments (VAR=)
                m = P.ASSIGNMENT.match(text, pos)
                if m:
                    name_start = m.start(1)
                    name_end = m.end(1)
                    tokens.append((name_start, name_end, 'variable')) 
                    pos = m.end() # '=' is next
                    if pos < length and text[pos] == '=':
                         tokens.append((pos, pos+1, 'operator'))
                         pos += 1
                    # After assignment, we expect a value
                    cmd_state = ASSIGN_VAL
                    continue

                # 5. Function Definitions
                m = P.FUNCTION_DEF.match(text, pos)
                if m:
                    name_start = m.start(1)
                    name_end = m.end(1)
                    tokens.append((name_start, name_end, 'function'))
                    pos = m.end()
                    cmd_state = CMD 
                    continue
                m = P.FUNCTION_DEF_KEYWORD.match(text, pos)
                if m:
                     kw_start = m.start(1)
                     kw_end = m.end(1)
                     name_start = m.start(2)
                     name_end = m.end(2)
                     tokens.append((kw_start, kw_end, 'keyword'))
                     tokens.append((name_start, name_end, 'function'))
                     pos = m.end()
                     cmd_state = CMD
                     continue

                # 6. Keywords
                m = P.KEYWORD.match(text, pos)
                if m:
                    word = m.group(1)
                    
                    # In LIST state, keywords (except do/done) are just strings
                    if cmd_state == LIST and word not in ('do', 'done'):
                        tokens.append((pos, m.end(), 'string'))
                        pos = m.end()
                        continue
                        
                    tokens.append((pos, m.end(), 'keyword'))
                    pos = m.end()
                    
                    # Reset command pos for control flow
                    if word == 'in':
                        cmd_state = LIST
                    elif word in ('if', 'then', 'else', 'elif', 'do', 'while', 'until', 'time', 'coproc'):
                        cmd_state = CMD
                    # for loop special handling
                    elif word == 'for':
                        # Look ahead for variable name
                        t_pos = pos
                        while t_pos < length and text[t_pos].isspace():
                            t_pos += 1
                        var_start = t_pos
                        while t_pos < length and (text[t_pos].isalnum() or text[t_pos] == '_'):
                            t_pos += 1
                        if t_pos > var_start:
                             tokens.append((var_start, t_pos, 'variable')) 
                             pos = t_pos
                        cmd_state = ARG # Expecting 'in'
                    else:
                        cmd_state = CMD 
                        
                    continue
                
                # 7. Switches (-f)
                m = P.SWITCH.match(text, pos)
                if m:
                    # In LIST state, -f is string
                    color = 'string' if cmd_state == LIST else 'number'
                    tokens.append((pos, m.end(), color))
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                
                # 5. Commands (Builtins)
                m = P.COMMAND.match(text, pos)
                if m:
                    if cmd_state == LIST:
                        tokens.append((pos, m.end(), 'string'))
                    else:
                        tokens.append((pos, m.end(), 'function')) # Blue
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                    
                # 6. Test Operators
                m = P.TEST_OP.match(text, pos)
                if m:
                    if cmd_state == LIST:
                         tokens.append((pos, m.end(), 'string'))
                    else:
                         tokens.append((pos, m.end(), 'operator'))
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue

                # 7. Operators
                m = P.OPERATOR.match(text, pos)
                if m:
                    op = m.group(0)
                    tokens.append((pos, m.end(), 'operator'))
                    pos = m.end()
                    
                    # Reset command pos after pipe, sequence, background, open-paren
                    if op in ('|', '||', '&&', ';', '&', '(', '{', '$('):
                        cmd_state = CMD
                    elif op in (')'):
                        cmd_state = ARG
                    elif op == '}':
                         cmd_state = CMD
                    
                    continue
                    
                # 8. Numbers
                m = P.NUMBER.match(text, pos)
                if m:
                    tokens.append((pos, m.end(), 'number'))
                    pos = m.end()
                    cmd_state = CMD if cmd_state == ASSIGN_VAL else (LIST if cmd_state == LIST else ARG)
                    continue
                
                # 9. Generic Word
                m = P.GENERIC_WORD.match(text, pos)
                if m:
                    if cmd_state == CMD:
                        word_type = 'function'
                        cmd_state = ARG
                    elif cmd_state == ASSIGN_VAL:
                        word_type = 'string'
                        cmd_state = CMD
                    elif cmd_state == LIST:
                        word_type = 'string'
                    else:
                        word_type = 'string'
                        
                    tokens.append((pos, m.end(), word_type))
                    pos = m.end()
                    continue

                # Skip unknown character
                pos += 1
            
            elif state == TokenState.IN_DQ_STRING:
                # Find end quote or escaped
                next_q = -1
                search_start = pos
                while True:
                    try:
                        next_q = text.index('"', search_start)
                        # Check escape
                        backslashes = 0
                        idx = next_q - 1
                        while idx >= 0 and text[idx] == '\\':
                            backslashes += 1
                            idx -= 1
                        if backslashes % 2 == 0:
                            break # Not escaped
                        search_start = next_q + 1
                    except ValueError:
                        next_q = -1
                        break
                
                # Also look for variables inside DQ
                m_var = P.VARIABLE.search(text, pos)
                
                end_pos = length
                new_state = state
                
                if next_q != -1:
                    end_pos = next_q
                    new_state = TokenState.ROOT
                
                if m_var and (next_q == -1 or m_var.start() < next_q):
                    # Found variable before end of string
                    if m_var.start() > pos:
                        tokens.append((pos, m_var.start(), 'string'))
                    tokens.append((m_var.start(), m_var.end(), 'variable'))
                    pos = m_var.end()
                    # State remains same
                    continue
                
                # No variable, just string
                tokens.append((pos, end_pos, 'string'))
                if next_q != -1:
                    tokens.append((end_pos, end_pos + 1, 'string'))
                    pos = end_pos + 1
                    state = TokenState.ROOT
                else:
                    pos = length
            
            elif state == TokenState.IN_SQ_STRING:
                # Find end quote
                next_q = text.find("'", pos)
                if next_q != -1:
                     tokens.append((pos, next_q + 1, 'string_single'))
                     pos = next_q + 1
                     state = TokenState.ROOT
                else:
                    tokens.append((pos, length, 'string_single'))
                    pos = length

        # Update state chain
        self.state_chain.set_end_state(line_num, state)
             
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
