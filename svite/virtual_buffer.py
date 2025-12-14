"""
Virtual Text Buffer - High-performance text buffer using mmap for millions of lines.

This module provides:
- LineIndexer: Fast line offset lookup using memory-mapped files
- VirtualBuffer: Main buffer class for text operations
"""

import mmap
import os
from array import array
from functools import lru_cache
from dataclasses import dataclass
from typing import Any, List, Tuple, Optional
import re
import time
from contextlib import contextmanager
from gi.repository import GLib
from syntax import SyntaxEngine


@dataclass
class LineInfo:
    """Information about a line in the buffer."""
    offset: int  # Byte offset from start
    length: int  # Length in bytes (excluding newline)


class LineIndexer:
    """
    Builds and maintains an index of line offsets for fast random access.
    
    For files: Uses mmap for memory-efficient scanning
    For in-memory: Maintains offset list for modified content
    """
    
    def __init__(self):
        # Use array.array for memory efficiency:
        # - Python list: ~28 bytes per int
        # - array.array('Q'): 8 bytes per unsigned long long
        # For 127M lines: saves ~5GB of RAM!
        self._offsets = array('Q', [0])  # Start of each line
        self._lengths = array('Q')        # Length of each line (without newline)
        self._total_size: int = 0
        self._implicit_lengths: bool = False
        self._newline_len: int = 1
    
    def use_implicit_lengths(self, newline_len: int = 1):
        """Enable memory saving mode: calculate lengths from offsets on fly."""
        self._implicit_lengths = True
        self._newline_len = newline_len
        self._lengths = array('Q') # Clear lengths
        
    def build_from_arrays(self, offsets: array, total_size: int, newline_len: int = 1):
        """Fast load from existing offets array (avoids re-scanning)."""
        self._offsets = offsets
        self._total_size = total_size
        self.use_implicit_lengths(newline_len)

    def build_from_file(self, filepath: str, encoding: str = 'utf-8') -> None:
        """Build line index from a file using mmap for efficiency."""
        file_size = os.path.getsize(filepath)
        self._total_size = file_size
        self._offsets = array('Q', [0])
        self._lengths = array('Q')
        self._implicit_lengths = False # Reset
        
        if file_size == 0:
            self._lengths.append(0)
            return
            
        newline_seq = b'\n'
        step = 1
        
        # Determine newline sequence and step based on encoding
        is_utf16 = False
        if encoding:
            enc_lower = encoding.lower()
            if enc_lower.startswith('utf-16'):
                is_utf16 = True
                step = 2
                # Check BOM or endianness to decide newline sequence
                # Ideally, we should detect endianness from BOM if strictly 'utf-16'
                # But here we might just have 'utf-16' passed from detect_encoding.
                # If 'utf-16' and starts with BOM, we need to respect it.
                
                # We'll peek at the file start if checks are ambiguous
                newline_seq = b'\n\x00' # Default to LE
                
                with open(filepath, 'rb') as f:
                    head = f.read(2)
                    if head == b'\xfe\xff': # BE
                        newline_seq = b'\x00\n'
                    elif head == b'\xff\xfe': # LE
                        newline_seq = b'\n\x00'
                    elif enc_lower == 'utf-16be':
                        newline_seq = b'\x00\n'
                    # else 'utf-16le' or 'utf-16' default -> LE (most common on Windows/Linux for this)
        
        # Save newline len for implicit mode later if needed
        newline_len = len(newline_seq)

        with open(filepath, 'rb') as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                pos = 0
                while pos < file_size:
                    # Find next newline
                    if is_utf16:
                        # For UTF-16, we need to search aligned
                        newline_pos = mm.find(newline_seq, pos)
                        # Alignment check: (newline_pos - 0) must be divisible by 2
                        while newline_pos != -1 and (newline_pos % 2) != 0:
                             newline_pos = mm.find(newline_seq, newline_pos + 1)
                    else:
                        newline_pos = mm.find(newline_seq, pos)
                        
                    if newline_pos == -1:
                        # Last line without newline
                        self._lengths.append(file_size - pos)
                        break
                    else:
                        self._lengths.append(newline_pos - pos)
                        pos = newline_pos + len(newline_seq)
                        if pos < file_size:
                            self._offsets.append(pos)
                
                # Handle file ending with newline
                if file_size >= len(newline_seq):
                     suffix = mm[file_size - len(newline_seq):file_size]
                     if suffix == newline_seq:
                        self._offsets.append(file_size)
                        self._lengths.append(0)
    
    def build_from_text(self, text: str) -> None:
        """Build line index from in-memory text."""
        encoded = text.encode('utf-8')
        self._total_size = len(encoded)
        self._offsets = array('Q', [0])
        self._lengths = array('Q')
        self._implicit_lengths = False
        
        if len(encoded) == 0:
            self._lengths.append(0)
            return
        
        pos = 0
        while pos < len(encoded):
            newline_pos = encoded.find(b'\n', pos)
            if newline_pos == -1:
                self._lengths.append(len(encoded) - pos)
                break
            else:
                self._lengths.append(newline_pos - pos)
                pos = newline_pos + 1
                if pos < len(encoded):
                    self._offsets.append(pos)
        
        # Handle text ending with newline
        if len(encoded) > 0 and encoded[-1:] == b'\n':
            self._offsets.append(len(encoded))
            self._lengths.append(0)
    
    @property
    def line_count(self) -> int:
        """Total number of lines."""
        return len(self._offsets)
    
    def get_line_info(self, line_num: int) -> Optional[LineInfo]:
        """Get offset and length for a line (0-indexed)."""
        if 0 <= line_num < len(self._offsets):
            offset = self._offsets[line_num]
            
            if self._implicit_lengths:
                # Calculate length on the fly
                if line_num < len(self._offsets) - 1:
                    next_off = self._offsets[line_num + 1]
                    length = max(0, next_off - offset - self._newline_len)
                else:
                    length = max(0, self._total_size - offset)
            else:
                length = self._lengths[line_num] if line_num < len(self._lengths) else 0
                
            return LineInfo(offset=offset, length=length)
        return None
    
    
    def get_line_at_offset(self, byte_offset: int) -> Tuple[int, int]:
        """Get (line_num, byte_offset_in_line) for a global byte offset."""
        if not self._offsets:
            return 0, 0
            
        import bisect
        # Find index where byte_offset fits
        # _offsets is start offset of each line.
        idx = bisect.bisect_right(self._offsets, byte_offset) - 1
        idx = max(0, min(idx, len(self._offsets) - 1))
        
        start_off = self._offsets[idx]
        return idx, byte_offset - start_off

    def invalidate_from(self, line_num: int) -> None:
        """Invalidate index from a specific line (for incremental updates)."""
        if line_num < len(self._offsets):
            self._offsets = self._offsets[:line_num + 1]
            self._lengths = self._lengths[:line_num]
    
    def update_after_insert(self, line_num: int, col: int, text: str, new_line_count: int, 
                           new_lengths: List[int], byte_delta: int) -> None:
        """Update index after text insertion."""
        if '\n' in text:
            # Multi-line insert - need to rebuild affected region
            old_offset = self._offsets[line_num] if line_num < len(self._offsets) else self._total_size
            
            # Update offsets after insertion point
            for i in range(line_num + 1, len(self._offsets)):
                self._offsets[i] += byte_delta
            
            # Insert new line offsets
            new_offsets = []
            current_offset = old_offset + col
            for length in new_lengths[:-1]:
                current_offset += length + 1  # +1 for newline
                new_offsets.append(current_offset)
            
            # Splice in new data
            if line_num < len(self._lengths):
                # Split existing line
                old_length = self._lengths[line_num]
                remaining = old_length - col
                
                self._lengths[line_num] = new_lengths[0] if new_lengths else col
                # Convert to array for concatenation
                self._offsets = self._offsets[:line_num + 1] + array('Q', new_offsets) + self._offsets[line_num + 1:]
                self._lengths = self._lengths[:line_num + 1] + array('Q', new_lengths[1:-1]) + array('Q', [new_lengths[-1] + remaining]) + self._lengths[line_num + 1:]
        else:
            # Single line insert - just update length
            if line_num < len(self._lengths):
                self._lengths[line_num] += len(text.encode('utf-8'))
            
            # Update following offsets
            for i in range(line_num + 1, len(self._offsets)):
                self._offsets[i] += byte_delta
        
        self._total_size += byte_delta
    
    def update_after_delete(self, start_line: int, start_col: int, 
                           end_line: int, end_col: int, byte_delta: int) -> None:
        """Update index after text deletion."""
        if start_line == end_line:
            # Same line deletion
            if start_line < len(self._lengths):
                self._lengths[start_line] -= (end_col - start_col)
        else:
            # Multi-line deletion - merge lines
            if start_line < len(self._lengths) and end_line < len(self._lengths):
                new_length = start_col + (self._lengths[end_line] - end_col)
                self._lengths[start_line] = new_length
                
                # Remove deleted lines
                del self._offsets[start_line + 1:end_line + 1]
                del self._lengths[start_line + 1:end_line + 1]
        
        # Update following offsets
        for i in range(start_line + 1, len(self._offsets)):
            self._offsets[i] += byte_delta  # byte_delta is negative
        
        self._total_size += byte_delta


@dataclass
class Selection:
    """Represents a text selection."""
    start_line: int = -1
    start_col: int = -1
    end_line: int = -1
    end_col: int = -1
    active: bool = False

    def clear(self):
        self.start_line = -1
        self.start_col = -1
        self.end_line = -1
        self.end_col = -1
        self.active = False
        
    def set_start(self, line: int, col: int):
        self.start_line = line
        self.start_col = col
        self.active = True
        
    def set_end(self, line: int, col: int):
        self.end_line = line
        self.end_col = col
        self.active = True
        
    def has_selection(self) -> bool:
        return self.active and (
            self.start_line != self.end_line or 
            self.start_col != self.end_col
        )
        
    def get_bounds(self) -> Tuple[int, int, int, int]:
        """Return normalized (min_line, min_col, max_line, max_col)."""
        if not self.active:
            return (-1, -1, -1, -1)
            
        if self.start_line < self.end_line:
            return (self.start_line, self.start_col, self.end_line, self.end_col)
        elif self.start_line > self.end_line:
            return (self.end_line, self.end_col, self.start_line, self.start_col)
        else:
            # Same line
            return (
                self.start_line, min(self.start_col, self.end_col),
                self.end_line, max(self.start_col, self.end_col)
            )


def detect_encoding(path):
    try:
        with open(path, "rb") as f:
            data = f.read(4096)  # small peek is enough
    except Exception:
        return "utf-8"

    # Handle empty files
    if len(data) == 0:
        return "utf-8"

    # --- BOM detection ---
    if data.startswith(b"\xff\xfe"):
        return "utf-16" # LE BOM
    if data.startswith(b"\xfe\xff"):
        return "utf-16" # BE BOM
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    # --- Heuristic UTF-16LE detection (no BOM) ---
    if len(data) >= 4:
        zeros_in_odd = sum(1 for i in range(1, len(data), 2) if data[i] == 0)
        ratio = zeros_in_odd / (len(data) / 2)
        if ratio > 0.4:
            return "utf-16le"

    # --- Heuristic UTF-16BE detection (no BOM) ---
    if len(data) >= 2:  # Need at least 2 bytes for this check
        zeros_in_even = sum(1 for i in range(0, len(data), 2) if data[i] == 0)
        ratio_be = zeros_in_even / (len(data) / 2)
        if ratio_be > 0.4:
            return "utf-16be"

    # Default
    return "utf-8"


class VirtualBuffer:
    """
    High-performance text buffer supporting millions of lines.
    
    Uses hybrid lazy loading:
    - Maintains a list `_lines` where elements are either:
      a) `int`: Original line index in the backing file (lazy)
      b) `str`: Loaded/Modified line content (eager)
    - Loads text from mmap only when a line is accessed or modified.
    - Eliminates initial load delay for editing.
    """
    
    def __init__(self):
        self._filepath: Optional[str] = None
        self._mmap: Optional[mmap.mmap] = None
        self._file: Optional[object] = None
        self._indexer = LineIndexer()
        self.syntax_engine = SyntaxEngine()
        self.syntax_engine.set_text_provider(self.get_line)
        
        # Memory optimized line mapping:
        # _lines is array('q') (signed 8-byte int)
        # - Values >= 0: Index into original file (via indexer)
        # - Values < 0: Index into _modified_cache (bitwise NOT, so -1 -> 0, -2 -> 1)
        self._lines = array('q', [0]) 
        self._modified_cache: Dict[int, str] = {}
        self._next_mod_id = 1
        
        self._is_modified: bool = False
        self._observers = []
        self.selection = Selection()
        self._suppress_notifications = 0
        self._size_delta: int = 0 # Track size changes relative to indexer

    @contextmanager
    def batch_notifications(self):
        """Context manager to suppress notifications during batch operations."""
        self._suppress_notifications += 1
        try:
            yield
        finally:
            self._suppress_notifications -= 1
            if self._suppress_notifications == 0:
                self._notify_observers()

    @property
    def total_size(self) -> int:
        """Total estimate size in bytes."""
        base = 0
        if self._mmap: base = self._mmap.size()
        return base + self._size_delta

    def get_line_info(self, line: int):
        """Get line info, respecting the virtual map."""
        # 1. Resolve logical line to item
        if self._lines is None:
            # Identity map
            item = line
        else:
            if line < 0 or line >= len(self._lines):
                return None
            item = self._lines[line]
            
        # 2. If item is index into file, get info
        if item >= 0:
            return self._indexer.get_line_info(item)
            
        # 3. If modified (negative), we don't have file offset
        return None

    def get_line_at_offset(self, offset: int):
        """
        Map global byte offset to (line, col).
        Handles appended text that is not yet in the static indexer.
        """
        # 1. Try static indexer
        idx, off_in_line = self._indexer.get_line_at_offset(offset)
        
        # 2. Check if we need to scan forward (appended text)
        # If indexer returned the last known line, check if we overflowed it
        indexer_count = self._indexer.line_count
        if idx >= indexer_count - 1 and self.total_lines > indexer_count:
             current_ln_len = 0
             line_text = self.get_line(idx)
             if line_text is not None:
                 current_ln_len = len(line_text.encode('utf-8')) # Bytes
                 
             # Check if offset is beyond this line (plus newline)
             # Indexer offsets usually include newline, but off_in_line is pure delta.
             # We assume 1 byte newline for simplicity in this fallback logic
             if off_in_line > current_ln_len:
                 # Scan forward through new lines
                 consumed = current_ln_len + 1 # +1 for newline
                 curr_idx = idx
                 current_rem = off_in_line
                 
                 while curr_idx < self.total_lines - 1 and current_rem > (len(self.get_line(curr_idx).encode('utf-8')) + 1):
                     ln_bytes = len(self.get_line(curr_idx).encode('utf-8')) + 1
                     current_rem -= ln_bytes
                     curr_idx += 1
                     
                 return curr_idx, max(0, current_rem)
                 
        return idx, off_in_line
        
    @property
    def total_size(self) -> int:
        """Get total size of buffer in bytes."""
        return max(0, self._indexer._total_size + self._size_delta)
        

        
    def add_observer(self, callback):
        """Add an observer to be notified of changes."""
        self._observers.append(callback)
        
    def remove_observer(self, callback):
        """Remove an observer."""
        if callback in self._observers:
            self._observers.remove(callback)
            
    def _notify_observers(self):
        """Notify all observers of a change."""
        if self._suppress_notifications > 0:
            return

        for callback in self._observers:
            try:
                callback(self)
            except Exception as e:
                print(f"Error in observer callback: {e}")
    
    def load_from_indexed_file(self, idx_file) -> None:
        """Load optimized from pre-indexed file object (avoids double indexing)."""
        self.close()
        
        filepath = idx_file.path
        self._filepath = filepath
        
        if idx_file.is_empty:
             self.load_file(filepath)
             return
             
        # Steal index data
        # Note: idx_file.index is array('Q') of offsets
        self._indexer.build_from_arrays(
            offsets=idx_file.index, 
            total_size=os.path.getsize(filepath),
            newline_len=1 # Assume 1 for now, or detect
        )
        
        self.current_encoding = idx_file.encoding
        self._file = open(filepath, 'rb')
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        
        # Initialize identity mapping
        # Create array 0..N-1
        cnt = self._indexer.line_count
        self._lines = array('q')
        
        # Fast initialization using extend for range
        # Note: range(127M) -> list -> extend is slow/heavy
        # array('q', range(N)) is better but still iterates
        # We can do this in blocks if N is huge?
        # Or just array('q', range(cnt)) is standard python valid way
        cnt = len(idx_file.index)
        # Optimization: Don't create the huge _lines array yet!
        # use Lazy Identity Map. _lines = None implies map[i] == i.
        # Eagerly create the identity map
        self._lines = array('q', range(cnt))
        
        self._modified_cache = {}
        self._size_delta = 0
        self._is_modified = False
        self._notify_observers()

    def load_file(self, filepath: str, encoding: Optional[str] = None, progress_callback: Optional[callable] = None) -> None:
        """Load a file using lazy index."""
        self.close()
        
        self._filepath = filepath
        file_size = os.path.getsize(filepath)
        
        if file_size == 0:
            self._lines = array('q', [-1]) # Use modified cache for empty?
            self._modified_cache = {0: ""}
            self._size_delta = 0
            self._is_modified = False 
            self._notify_observers()
            return
        
        # Build line index (fast scan)
        self._indexer.build_from_file(filepath, encoding=encoding)
        # self._lines = [] # Clear cached lines - handled by init
        
        # Open mmap for random access with detected encoding
        if encoding:
            self.current_encoding = encoding
        else:
            self.current_encoding = detect_encoding(filepath)
        self._file = open(filepath, 'rb')
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        
        # Initialize lines as an eager identity map in batches to avoid UI freeze
        cnt = self._indexer.line_count
        self._lines = array('q')
        batch = 100_000  # adjust as needed
        for start in range(0, cnt, batch):
            end = min(start + batch, cnt)
            # extend with range of indices
            self._lines.extend(range(start, end))
            if progress_callback:
                progress_callback(end / cnt)
        # Ensure final progress reported
        if progress_callback:
            progress_callback(1.0)

        # Reset modification tracking structures
        self._modified_cache = {}
        self._size_delta = 0
        self._is_modified = False
        self._notify_observers()
    
    def load_text(self, text: str) -> None:
        """Load text directly into buffer."""
        self.close()
        lines = text.split('\n')
        self._size_delta = len(text.encode('utf-8'))
        
        # All lines are "modified" (memory residents)
        self._lines = array('q') # This should be populated with negative indices
        self._modified_cache = {}
        self._next_mod_id = 1      # Populate cache and negative indices
        mod_ids = []
        for i, line in enumerate(lines):
            mod_id = - (i + 1)
            self._modified_cache[~mod_id] = line # Bitwise not to map -1 -> 0
            mod_ids.append(mod_id)
            
        self._lines.extend(mod_ids)
        self._next_mod_id = len(lines) + 1
        
        self._is_modified = True
        self.syntax_engine.invalidate_from(0)
        self._notify_observers()
    
    def close(self) -> None:
        """Close any open file handles."""
        if self._mmap:
            self._mmap.close()
            self._mmap = None
        if self._file:
            self._file.close()
            self._file = None
        # Don't clear _lines here, they might be needed (converted to strs)
        # But if we close mmap, lazy ints become invalid.
        # So we should resolve all if closing?
        # Usually close is called before load_new.
        pass
            
    @property
    def total_lines(self) -> int:
        """Total number of lines in the buffer."""
        if self._lines is None:
             if self._indexer and self._indexer._offsets:
                 return len(self._indexer._offsets)
             return 0
        return len(self._lines)
        
    # Compatibility shim for legacy code
    def total(self) -> int:
        """Alias for total_lines (legacy compatibility)."""
        return self.total_lines
        
    # Cursor state management (simple implementation directly on buffer for now)
    @property
    def cursor_line(self) -> int:
        return getattr(self, '_cursor_line', 0)
        
    @cursor_line.setter
    def cursor_line(self, value: int):
        self._cursor_line = value
        
    @property
    def cursor_col(self) -> int:
        return getattr(self, '_cursor_col', 0)
        
    @cursor_col.setter
    def cursor_col(self, value: int):
        self._cursor_col = value

    def set_cursor(self, line: int, col: int, extend_selection: bool = False):
        """Set cursor position with optional selection extension."""
        if extend_selection:
            if not self.selection.active:
                self.selection.set_start(self.cursor_line, self.cursor_col)
            self.selection.set_end(line, col)
        else:
            self.selection.clear()
            
        self.cursor_line = max(0, min(line, self.total() - 1)) if self.total() > 0 else 0
        # Ensure col is valid? 
        # Typically we clamp to line length, but let's just minimal set for now
        self.cursor_col = col 
        
    def load(self, source, emit_changed=True):
        """Compatibility shim for load."""
        # Detect if source is an IndexedFile (has index array)
        if hasattr(source, 'index') and hasattr(source, 'encoding') and hasattr(source, 'path'):
            self.load_from_indexed_file(source)
            return

        path = source
        encoding = None
        if hasattr(source, 'path'):
            path = source.path
        if hasattr(source, 'encoding'):
            encoding = source.encoding

        if isinstance(path, str):
            self.load_file(path, encoding=encoding)
        else:
            # Fallback for unknown types (maybe just empty or text?)
            pass
            
    def insert_text(self, text, overwrite=False):
        """Compatibility shim for insert_text at cursor."""
        if overwrite:
             # Logic to handle overwrite mode or selection replacement
             # For now, just insert
             pass
        
        has_selection = self.selection.has_selection()
        if has_selection:
             self.begin_action()
             
        try:
            # Handle selection logic
            if has_selection:
                 self.delete_selection()
                 
            # Insert at current cursor position
            new_line, new_col = self.insert(self.cursor_line, self.cursor_col, text)
            self.cursor_line = new_line
            self.cursor_col = new_col
        finally:
            if has_selection:
                self.end_action()
        

    
    @property
    def is_modified(self) -> bool:
        """Whether buffer has unsaved modifications."""
        return self._is_modified
    
    def _resolve_line(self, line_idx: int) -> str:
        """
        Internal: Resolve a line content.
        If _lines is None (identity map), item = line_idx.
        """
        if self._lines is None:
             item = line_idx
        else:
             item = self._lines[line_idx]
             
        if item >= 0:
             # It's a lazy index into file
             if self._mmap:
                 info = self._indexer.get_line_info(item)
                 if info:
                     self._mmap.seek(info.offset)
                     data = self._mmap.read(info.length)
                     encoding = getattr(self, 'current_encoding', 'utf-8')
                     try:
                         text = data.decode(encoding)
                     except UnicodeDecodeError:
                         text = data.decode(encoding, errors='replace')
                     return text
             return "" 
        else:
             cache_idx = ~item
             return self._modified_cache.get(cache_idx, "")
             
    def get_line(self, line_num: int) -> str:
        """Get content of a specific line (0-indexed)."""
        if self._lines is None:
             cnt = 0
             if self._indexer and self._indexer._offsets:
                 cnt = len(self._indexer._offsets)
             if line_num < 0 or line_num >= cnt:
                 return ""
        else:
             if line_num < 0 or line_num >= len(self._lines):
                 return ""
        return self._resolve_line(line_num)
        
    def get_lines(self, start_line: int, count: int) -> List[str]:
        """Get multiple consecutive lines efficiently."""
        result = []
        # Calculate limit
        if self._lines is None:
             limit = 0
             if self._indexer and self._indexer._offsets:
                 limit = len(self._indexer._offsets)
        else:
             limit = len(self._lines)
             
        end_line = min(start_line + count, limit)
        for i in range(start_line, end_line):
           result.append(self.get_line(i))
        return result
    
    def get_line_length(self, line_num: int) -> int:
        """Get length of a specific line in characters."""
        return len(self.get_line(line_num))

    # ==================== Advanced Deletion ====================

    def delete_word_backward(self):
        """Delete word before cursor."""
        if self.selection.has_selection():
            self.delete_selection()
            return
            
        ln, col = self.cursor_line, self.cursor_col
        if col == 0:
            # At start of line, backspace to previous line
            # This is a simplified backspace, not a full word delete across lines
            if ln > 0:
                prev_line_len = self.get_line_length(ln - 1)
                self.delete(ln - 1, prev_line_len, ln, 0)
                self.cursor_line = ln - 1
                self.cursor_col = prev_line_len
            return

        line = self.get_line(ln)
        
        # Helper to check word
        import unicodedata
        def is_word_char(ch):
            if ch == '_': return True
            return unicodedata.category(ch)[0] in ('L', 'N', 'M')
            
        # Skip whitespace left
        target_col = col
        while target_col > 0 and line[target_col - 1].isspace():
            target_col -= 1
            
        if target_col > 0:
            if is_word_char(line[target_col - 1]):
                while target_col > 0 and is_word_char(line[target_col - 1]):
                    target_col -= 1
            else:
                 while target_col > 0 and not line[target_col - 1].isspace() and not is_word_char(line[target_col - 1]):
                     target_col -= 1
        
        self.delete(ln, target_col, ln, col)
        self.cursor_col = target_col

    def delete_word_forward(self):
        """Delete word after cursor."""
        if self.selection.has_selection():
            self.delete_selection()
            return
            
        ln, col = self.cursor_line, self.cursor_col
        line = self.get_line(ln)
        
        if col >= len(line):
            if ln < self.total() - 1:
                # Join with next line
                self.delete(ln, len(line), ln + 1, 0)
            return

        import unicodedata
        def is_word_char(ch):
             if ch == '_': return True
             return unicodedata.category(ch)[0] in ('L', 'N', 'M')
        
        target_col = col
        
        # Skip current word/non-whitespace/non-word-char sequence
        if target_col < len(line):
            if is_word_char(line[target_col]):
                 while target_col < len(line) and is_word_char(line[target_col]):
                     target_col += 1
            elif not line[target_col].isspace():
                 while target_col < len(line) and not line[target_col].isspace() and not is_word_char(line[target_col]):
                     target_col += 1
                 
        # Skip whitespace right
        while target_col < len(line) and line[target_col].isspace():
            target_col += 1
            
        self.delete(ln, col, ln, target_col)
        
    def delete_to_line_start(self):
        """Delete from cursor to start of line."""
        ln, col = self.cursor_line, self.cursor_col
        if col > 0:
            self.delete(ln, 0, ln, col)
            self.cursor_col = 0
            
    def delete_to_line_end(self):
        """Delete from cursor to end of line."""
        ln, col = self.cursor_line, self.cursor_col
        line = self.get_line(ln)
        if col < len(line):
            self.delete(ln, col, ln, len(line))
    
    def get_text(self) -> str:
        """Get full text content."""
        # Force resolve all
        return '\n'.join([self.get_line(i) for i in range(self.total_lines)])
    
    def get_text_range(self, start_line: int, start_col: int, 
                       end_line: int, end_col: int) -> str:
        """Get text in a specific range."""
        if start_line == end_line:
            line = self.get_line(start_line)
            return line[start_col:end_col]
        
        result = []
        result.append(self.get_line(start_line)[start_col:])
        
        # Middle lines
        # Optimization: Don't use get_text_range loop if possible, utilize get_lines
        # But verify step by step mostly fine
        for i in range(start_line + 1, end_line):
            result.append(self.get_line(i))
            
        result.append(self.get_line(end_line)[:end_col])
        return '\n'.join(result)

    def get_selected_text(self) -> str:
        """Get the currently selected text."""
        if not self.selection.has_selection():
            return ""
        
        start_ln, start_col, end_ln, end_col = self.selection.get_bounds()
        return self.get_text_range(start_ln, start_col, end_ln, end_col)

    def delete_selection(self, provided_text: Optional[str] = None):
        """Delete the currently selected text."""
        if not self.selection.has_selection():
            return
            
        start_ln, start_col, end_ln, end_col = self.selection.get_bounds()
        self.delete(start_ln, start_col, end_ln, end_col, provided_text=provided_text)
        self.selection.clear()
        self.cursor_line = start_ln
        self.cursor_col = start_col

    def select_all(self):
        """Select all text in the buffer."""
        total_lines = self.total_lines
        if total_lines == 0:
            return
            
        last_line_idx = total_lines - 1
        last_line_len = self.get_line_length(last_line_idx)
        
        self.selection.set_start(0, 0)
        self.selection.set_end(last_line_idx, last_line_len)
        self.cursor_line = last_line_idx
        self.cursor_col = last_line_len
        self._notify_observers()

    def _register_modified_line(self, content: str) -> int:
        """Helper to register a new modified line content and get its ID."""
        mod_id = - (self._next_mod_id)
        self._next_mod_id += 1
        # Map -1 to 0, -2 to 1, etc using bitwise NOT (~)
        # -1 = ~0, -2 = ~1
        cache_idx = ~mod_id
        self._modified_cache[cache_idx] = content
        return mod_id

    def insert(self, line: int, col: int, text: str, _record_undo: bool = True) -> Tuple[int, int]:
        """Insert text at position."""
        if self._lines is None:
            # Lazy materialization on first edit!
            cnt = 0
            if self._indexer and self._indexer._offsets:
                 cnt = len(self._indexer._offsets)
            self._lines = array('q', range(cnt))
            
        line = min(line, len(self._lines) - 1)
        current_line = self._resolve_line(line)
        col = min(col, len(current_line))
        
        self.syntax_engine.invalidate_from(line)
        self._size_delta += len(text.encode('utf-8')) # Approximate using utf-8
        
        before = current_line[:col]
        after = current_line[col:]
        
        insert_lines = text.split('\n')
        
        if len(insert_lines) == 1:
            # Single line modification
            new_content = before + text + after
            mod_id = self._register_modified_line(new_content)
            self._lines[line] = mod_id
            
            end_line = line
            end_col = col + len(text)
        else:
            # Multi-line insertion
            first_part = before + insert_lines[0]
            last_part = insert_lines[-1] + after
            
            # Create new line IDs
            new_ids = array('q')
            
            # First line (replacement)
            new_ids.append(self._register_modified_line(first_part))
            
            # Middle lines
            for mid_line in insert_lines[1:-1]:
                new_ids.append(self._register_modified_line(mid_line))
                
            # Last line
            new_ids.append(self._register_modified_line(last_part))
            
            # Splice into array
            self._lines[line:line+1] = new_ids
            
            end_line = line + len(insert_lines) - 1
            end_col = len(insert_lines[-1])
            
        self._is_modified = True
        self._notify_observers()
        
        if _record_undo and hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            from undo_redo import InsertCommand, Position
            cmd = InsertCommand(Position(line, col), text)
            cmd.end_position = Position(end_line, end_col)
            self._view.undo_manager.push(cmd)
            
        # Update cursor position to end of insertion
        self.cursor_line = end_line
        self.cursor_col = end_col
            
        return end_line, end_col
        
    def insert_newline(self):
        """Insert a newline at cursor position."""
        self.insert(self.cursor_line, self.cursor_col, "\n")
        
    def delete(self, start_line: int, start_col: int, end_line: int, end_col: int, provided_text: Optional[str] = None, _record_undo: bool = True):
        """Delete text in range."""
        if self._lines is None:
            # Lazy materialization
            cnt = 0
            if self._indexer and self._indexer._offsets:
                 cnt = len(self._indexer._offsets)
            self._lines = array('q', range(cnt))
            
        if start_line > end_line or (start_line == end_line and start_col >= end_col):
            return
        self.syntax_engine.invalidate_from(start_line)
        
        if provided_text is not None:
            deleted = provided_text
        else:
            deleted = self.get_text_range(start_line, start_col, end_line, end_col)
            
        self._size_delta -= len(deleted.encode('utf-8'))
        
        if start_line == end_line:
            line = self._resolve_line(start_line)
            new_content = line[:start_col] + line[end_col:]
            mod_id = self._register_modified_line(new_content)
            self._lines[start_line] = mod_id
        else:
            first_line = self._resolve_line(start_line)
            last_line = self._resolve_line(end_line)
            
            first_kept = first_line[:start_col]
            last_kept = last_line[end_col:]
            
            new_content = first_kept + last_kept
            mod_id = self._register_modified_line(new_content)
            
            # Replace start_line with fused content, remove intermediate lines
            self._lines[start_line] = mod_id
            del self._lines[start_line + 1:end_line + 1]
            
        self._is_modified = True
        self._notify_observers()
        
        if _record_undo and hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            from undo_redo import DeleteCommand, Position
            cmd = DeleteCommand(Position(start_line, start_col), Position(end_line, end_col), deleted)
            self._view.undo_manager.push(cmd)
            
        return deleted

    def replace(self, start_line: int, start_col: int, 
                end_line: int, end_col: int, text: str, _record_undo=True) -> Tuple[str, int, int]:
        """Replace text in range."""
        deleted = self.delete(start_line, start_col, end_line, end_col, _record_undo=_record_undo)
        new_end_line, new_end_col = self.insert(start_line, start_col, text, _record_undo=_record_undo)
        return (deleted, new_end_line, new_end_col)

        self._filepath = save_path
        self._is_modified = False
        self.load_file(save_path)

    # ==================== Search & Replace ====================

    def search(self, query: str, case_sensitive: bool = False, 
               is_regex: bool = False, max_matches: int = -1) -> List[Tuple[int, int, int, int]]:
        """
        Search for query in the entire buffer.
        Returns list of (start_line, start_col, end_line, end_col).
        """
        if not query:
            return []
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                return []
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        
        # Iterate all lines (lazy)
        # Note: This forces loading of lines. For huge files, use search_viewport or async logic.
        for ln in range(len(self._lines)):
            line = self.get_line(ln)
            
            if is_regex:
                for m in pattern.finditer(line):
                    matches.append((ln, m.start(), ln, m.end()))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches
            else:
                search_line = line if case_sensitive else line.lower()
                start = 0
                while True:
                    idx = search_line.find(query, start)
                    if idx == -1:
                        break
                    matches.append((ln, idx, ln, idx + len(query)))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches
                    start = idx + 1
                    
        return matches

    def begin_action(self):
        """Start a batch of undoable actions."""
        if hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            self._view.undo_manager.begin_batch()

    def end_action(self):
        """End the current batch."""
        if hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            self._view.undo_manager.end_batch()
            
    def replace_current(self, match, replacement, _record_undo=True):
        """
        Replace a single match tuple (start_ln, start_col, end_ln, end_col, [optional_text]).
        """
        s_ln, s_col, e_ln, e_col = match[0:4]
        
        if _record_undo:
            self.begin_action()
            
        try:
            self.delete(s_ln, s_col, e_ln, e_col, _record_undo=_record_undo)
            self.insert(s_ln, s_col, replacement, _record_undo=_record_undo)
        finally:
             if _record_undo:
                 self.end_action()

    def search_async(self, query: str, case_sensitive: bool = False, 
                     is_regex: bool = False, max_matches: int = -1, 
                     on_progress=None, on_complete=None, chunk_size=5000) -> Any:
        """Async search using GLib idle loop."""
        if not query:
            if on_complete: on_complete([])
            return lambda: None
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                if on_complete: on_complete([])
                return lambda: None
        else:
            if not case_sensitive:
                query = query.lower()

        
        matches = []
        current_ln = 0
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_ln
            if is_cancelled: return False
            
            start_time = time.time()
            end_ln = min(current_ln + chunk_size, len(self._lines))
            
            # Optimization: Try to use local variables for loop
            lines_to_process = end_ln - current_ln
            processed_count = 0
            
            for i in range(lines_to_process):
                # Check time budget (12ms)
                if (time.time() - start_time) > 0.012:
                     break
                
                ln = current_ln + i
                line = self.get_line(ln)
                processed_count = i + 1
                
                if is_regex:
                    for m in pattern.finditer(line):
                        matches.append((ln, m.start(), ln, m.end()))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches)
                            return False
                else:
                    search_line = line if case_sensitive else line.lower()
                    start = 0
                    while True:
                        idx = search_line.find(query, start)
                        if idx == -1: break
                        matches.append((ln, idx, ln, idx + len(query)))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches)
                            return False
                        start = idx + 1
            
            current_ln += processed_count
            
            if on_progress:
                on_progress(matches, current_ln, len(self._lines))
                
            if current_ln >= len(self._lines):
                if on_complete: on_complete(matches)
                return False
                
            return True
            
        GLib.idle_add(process_chunk)
        
        def cancel():
            nonlocal is_cancelled
            is_cancelled = True
            
        return cancel
    
    def search_async_from(self, query: str, case_sensitive: bool = False,
                         is_regex: bool = False, start_line: int = 0,
                         max_matches: int = -1, on_progress=None,
                         on_complete=None, chunk_size=5000) -> Any:
        """Async search starting from specific line for progressive loading."""
        if not query:
            if on_complete: on_complete([])
            return lambda: None
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                if on_complete: on_complete([])
                return lambda: None
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        current_ln = max(0, start_line)  # Start from specified line
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_ln
            if is_cancelled: return False
            
            start_time = time.time()
            end_ln = min(current_ln + chunk_size, len(self._lines))
            
            lines_to_process = end_ln - current_ln
            processed_count = 0
            
            for i in range(lines_to_process):
                if (time.time() - start_time) > 0.012:
                     break
                
                ln = current_ln + i
                line = self.get_line(ln)
                processed_count = i + 1
                
                if is_regex:
                    for m in pattern.finditer(line):
                        matches.append((ln, m.start(), ln, m.end()))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches)
                            return False
                else:
                    search_line = line if case_sensitive else line.lower()
                    start = 0
                    while True:
                        idx = search_line.find(query, start)
                        if idx == -1: break
                        matches.append((ln, idx, ln, idx + len(query)))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches)
                            return False
                        start = idx + 1
            
            current_ln += processed_count
            
            if on_progress:
                on_progress(matches, current_ln, len(self._lines))
                
            if current_ln >= len(self._lines):
                if on_complete: on_complete(matches)
                return False
                
            return True
            
        GLib.idle_add(process_chunk)
        
        def cancel():
            nonlocal is_cancelled
            is_cancelled = True
            
        return cancel

    def search_viewport(self, query: str, case_sensitive: bool, is_regex: bool,
                        start_line: int, end_line: int, max_matches: int = -1) -> List[Tuple[int, int, int, int]]:
        """Search only within a specific range of lines."""
        if not query:
            return []
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                return []
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        start_line = max(0, start_line)
        end_line = min(len(self._lines), end_line)
        
        for ln in range(start_line, end_line):
            line = self.get_line(ln)
            
            if is_regex:
                for m in pattern.finditer(line):
                    matches.append((ln, m.start(), ln, m.end()))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches
            else:
                search_line = line if case_sensitive else line.lower()
                start = 0
                while True:
                    idx = search_line.find(query, start)
                    if idx == -1:
                        break
                    matches.append((ln, idx, ln, idx + len(query)))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches
                    start = idx + 1
                    
        return matches

    def replace_all(self, query: str, replacement: str, 
                    case_sensitive: bool = False, is_regex: bool = False) -> int:
        """Replace all occurrences. Returns count."""
        # For compatibility, keeping sync version but it might be slow for huge files
        matches = self.search(query, case_sensitive, is_regex)
        if not matches:
            return 0
            
        self.begin_action()
        try:
            # Reverse order to prevent index invalidation
            count = 0
            for i in range(len(matches) - 1, -1, -1):
                start_ln, start_col, end_ln, end_col = matches[i]
                self.replace(start_ln, start_col, end_ln, end_col, replacement, _record_undo=True)
                count += 1
        finally:
            self.end_action()
            
        return count

    def replace_all_async(self, query: str, replacement: str, 
                          case_sensitive: bool = False, is_regex: bool = False,
                          on_progress=None, on_complete=None, chunk_size=1000):
        """
        Async replace all using line-by-line scanning with time budget.
        More robust for unlimited replacements on huge files.
        """
        # Ensure suppression flag exists (monkey-patch if needed for running instance, but better in init)
        if not hasattr(self, '_suppress_notifications'):
            self._suppress_notifications = 0
            
        # Define context manager locally if method injection is messy, 
        # but better to have it as method. 
        # I will implement the logic inline or use a helper.
        
        total_lines = self.total()
        current_line = 0
        count = 0
        
        # Compile regex if needed
        pattern = None
        if is_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(query, flags)
            except:
                if on_complete: on_complete(0)
                return lambda: None
        else:
            if not case_sensitive:
                query_lower = query.lower()
                
        # Start batch undo
        self.begin_action()
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_line, count, is_cancelled
            
            if is_cancelled:
                self.end_action()
                return False
            
            start_time = time.time()
            
            # Use manual suppression logic since we can't easily add context manager method cleanly in this edit block
            if not hasattr(self, '_suppress_notifications'): self._suppress_notifications = 0
            self._suppress_notifications += 1
            
            try:
                while current_line < total_lines:
                    # Check time budget (e.g. 12ms target for >60fps)
                    if (time.time() - start_time) > 0.012:
                        break

                    line_text = self.get_line(current_line)
                    new_text = None
                    
                    if is_regex:
                        if pattern.search(line_text):
                            new_text, subs = pattern.subn(replacement, line_text)
                            if subs > 0:
                                count += subs
                    else:
                        # Simple string replace
                        if case_sensitive:
                            if query in line_text:
                                new_text = line_text.replace(query, replacement)
                                count += line_text.count(query)
                        else:
                            if query_lower in line_text.lower():
                                flags = re.IGNORECASE
                                new_text, subs = re.subn(re.escape(query), lambda m: replacement, line_text, flags=flags)
                                if subs > 0:
                                    count += subs

                    if new_text is not None and new_text != line_text:
                        # Apply change using replace(), propagating _record_undo
                        self.replace(current_line, 0, current_line, len(line_text), new_text, _record_undo=True)
                        
                        # Handle newline shifts
                        newlines_added = new_text.count('\n') - line_text.count('\n')
                        if newlines_added != 0:
                             current_line += newlines_added 
                    
                    current_line += 1
            finally:
                 self._suppress_notifications -= 1
                 if self._suppress_notifications == 0:
                     self._notify_observers()
            
            # Update progress
            if on_progress:
                on_progress(count, current_line, self.total())
                
            if current_line < self.total():
                return True # Continue
            else:
                self.end_action()
                if on_complete:
                    on_complete(count)
                return False
                
        GLib.idle_add(process_chunk)
        
        def cancel():
            nonlocal is_cancelled
            is_cancelled = True
            
        return cancel

    # ... (rest of methods)

    def replace_all_async(self, query: str, replacement: str, 
                          case_sensitive: bool = False, is_regex: bool = False,
                          on_progress=None, on_complete=None, chunk_size=1000):
        """
        Async replace all using line-by-line scanning with time budget.
        More robust for unlimited replacements on huge files.
        """
        total_lines = self.total()
        current_line = 0
        count = 0
        
        # Compile regex if needed
        pattern = None
        if is_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(query, flags)
            except:
                if on_complete: on_complete(0)
                return lambda: None
        else:
            if not case_sensitive:
                query_lower = query.lower()
                
        # Start batch undo
        self.begin_action()
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_line, count, is_cancelled
            
            if is_cancelled:
                self.end_action()
                return False
            
            start_time = time.time()
            
            # Use batch_notifications to suppress UI updates for every single line replace
            with self.batch_notifications():
                while current_line < total_lines:
                    # Check time budget (e.g. 12ms target for >60fps)
                    if (time.time() - start_time) > 0.012:
                        break

                    line_text = self.get_line(current_line)
                    new_text = None
                    
                    if is_regex:
                        if pattern.search(line_text):
                            new_text, subs = pattern.subn(replacement, line_text)
                            if subs > 0:
                                count += subs
                    else:
                        # Simple string replace
                        if case_sensitive:
                            if query in line_text:
                                new_text = line_text.replace(query, replacement)
                                count += line_text.count(query)
                        else:
                            if query_lower in line_text.lower():
                                flags = re.IGNORECASE
                                new_text, subs = re.subn(re.escape(query), lambda m: replacement, line_text, flags=flags)
                                if subs > 0:
                                    count += subs

                    if new_text is not None and new_text != line_text:
                        # Apply change using replace(), propagating _record_undo
                        self.replace(current_line, 0, current_line, len(line_text), new_text, _record_undo=True)
                        
                        # Handle newline shifts if any
                        newlines_added = new_text.count('\n') - line_text.count('\n')
                        if newlines_added != 0:
                             # If lines were added, skip them in this pass
                             current_line += newlines_added 
                             # Note: total_lines increases implicitly by buffer ops, 
                             # but our loop bound 'total_lines' variable is static?
                             # self.total() changes. We should update cached limit or check self.total()
                             # But 'process_chunk' re-reads self.total() via property? No.
                             # Let's update loop limit dynamic check
                    
                    current_line += 1
            
            # Update progress
            if on_progress:
                on_progress(count, current_line, self.total())
                
            if current_line < self.total():
                return True # Continue
            else:
                self.end_action()
                if on_complete:
                    on_complete(count)
                return False
                
        GLib.idle_add(process_chunk)
        
        def cancel():
            nonlocal is_cancelled
            is_cancelled = True
            
        return cancel
    
        start_ln, start_col, end_ln, end_col = match
        # Check validity? (Line length might have changed if we are not careful)
        # Assuming caller manages state.
        return self.replace(start_ln, start_col, end_ln, end_col, replacement)

    # ==================== Editing Methods ====================

    def backspace(self):
        """Delete character before cursor or delete selection."""
        if self.selection.has_selection():
            self.delete_selection()
            return
            
        ln, col = self.cursor_line, self.cursor_col
        
        if col > 0:
            # Delete char in current line
            self.delete(ln, col - 1, ln, col)
            self.cursor_col = col - 1
        elif ln > 0:
            # Join with previous line
            prev_line = self.get_line(ln - 1)
            prev_len = len(prev_line)
            
            self.delete(ln - 1, prev_len, ln, 0)
            self.cursor_line = ln - 1
            self.cursor_col = prev_len
            
    def delete_key(self):
        """Delete character at cursor (Delete key behavior)."""
        if self.selection.has_selection():
            self.delete_selection()
            return
            
        ln, col = self.cursor_line, self.cursor_col
        line = self.get_line(ln)
        
        if col < len(line):
             # Delete char at cursor
             self.delete(ln, col, ln, col + 1)
        elif ln < self.total() - 1:
             # Join with next line
             self.delete(ln, len(line), ln + 1, 0)

    def move_line_up_with_text(self):
        """Swap current line with previous line."""
        ln = self.cursor_line
        if ln <= 0: return
        
        curr_text = self.get_line(ln)
        prev_text = self.get_line(ln - 1)
        
        # We can implement this as a replace of both lines
        # Or delete and insert. 
        # Ideally atomic for undo? 
        # For now, simplistic approach:
        
        self.replace(ln - 1, 0, ln, len(curr_text), curr_text + "\n" + prev_text)
        self.cursor_line = ln - 1
        
    def move_line_down_with_text(self):
        """Swap current line with next line."""
        ln = self.cursor_line
        if ln >= self.total() - 1: return
        
        curr_text = self.get_line(ln)
        next_text = self.get_line(ln + 1)
        
        self.replace(ln, 0, ln + 1, len(next_text), next_text + "\n" + curr_text)
        self.cursor_line = ln + 1

    def move_word_left_with_text(self):
        """Move selected text (or word under cursor) left by one word."""
        if not self.selection.has_selection():
             # Select current word if no selection?
             # For now, let's assume we operate on selection or nothing
             return
             
        # Get selected text
        start_ln, start_col, end_ln, end_col = self.selection.get_bounds()
        text = self.get_selected_text()
        
        # Remove selection temporarily
        # We need to find the word to the left of start_ln, start_col
        # This is tricky because "inserting" text at a new position invalidates indices
        
        # Delete the selection first
        self.delete(start_ln, start_col, end_ln, end_col)
        
        # Find new insertion point (word left from start_ln, start_col)
        # Since we deleted, the cursor is effectively at start_ln, start_col
        # We simulate "move word left" logic to find new pos
        
        # Move temporary cursor left
        curr_ln, curr_col = start_ln, start_col
        
        # Helper to check word
        import unicodedata
        def is_word_char(ch):
            if ch == '_': return True
            return unicodedata.category(ch)[0] in ('L', 'N', 'M')

        # Logic from move_word_left (simplified)
        line = self.get_line(curr_ln)
        
        # If at start of line, go to end of prev line
        if curr_col == 0:
            if curr_ln > 0:
                curr_ln -= 1
                curr_col = len(self.get_line(curr_ln))
        else:
             # Skip whitespace left
             while curr_col > 0 and line[curr_col - 1].isspace():
                 curr_col -= 1
             
             # Skip word or symbols
             if curr_col > 0:
                 if is_word_char(line[curr_col - 1]):
                     while curr_col > 0 and is_word_char(line[curr_col - 1]):
                         curr_col -= 1
                 else:
                     while curr_col > 0 and not line[curr_col - 1].isspace() and not is_word_char(line[curr_col - 1]):
                         curr_col -= 1
                         
        # Insert text at new position
        new_end_ln, new_end_col = self.insert(curr_ln, curr_col, text)
        
        # Restore selection
        # new position is from curr_ln, curr_col to new_end_ln, new_end_col
        self.selection.set_start(curr_ln, curr_col)
        self.selection.set_end(new_end_ln, new_end_col)
        self.cursor_line = curr_ln
        self.cursor_col = curr_col # Keep cursor at start?

    def move_word_right_with_text(self):
        """Move selected text (or word under cursor) right by one word."""
        if not self.selection.has_selection():
             return
             
        start_ln, start_col, end_ln, end_col = self.selection.get_bounds()
        text = self.get_selected_text()
        
        self.delete(start_ln, start_col, end_ln, end_col)
        
        # Current position is start_ln, start_col
        curr_ln, curr_col = start_ln, start_col
        line = self.get_line(curr_ln)

        import unicodedata
        def is_word_char(ch):
            if ch == '_': return True
            return unicodedata.category(ch)[0] in ('L', 'N', 'M')
            
        # Move right logic
        if curr_col >= len(line):
             if curr_ln + 1 < self.total():
                 curr_ln += 1
                 curr_col = 0
        else:
             if is_word_char(line[curr_col]):
                 while curr_col < len(line) and is_word_char(line[curr_col]):
                     curr_col += 1
             elif not line[curr_col].isspace():
                 while curr_col < len(line) and not line[curr_col].isspace() and not is_word_char(line[curr_col]):
                     curr_col += 1
             
             # Skip whitespace
             while curr_col < len(line) and line[curr_col].isspace():
                 curr_col += 1
                 
        new_end_ln, new_end_col = self.insert(curr_ln, curr_col, text)
        
        self.selection.set_start(curr_ln, curr_col)
        self.selection.set_end(new_end_ln, new_end_col)
        self.cursor_line = new_end_ln
        self.cursor_col = new_end_col
