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
from syntax_v2 import StateAwareSyntaxEngine


class CancelledError(Exception):
    """Raised when an operation is cancelled."""
    pass


def normalize_replacement_string(replacement: str) -> str:
    r"""
    Normalize capture group references in replacement strings.
    
    Converts \1, \2, etc. and $1, $2, etc. to Python's \g<1>, \g<2> format.
    
    Carefully ignores escaped backslashes (\\1 should remain as literal \1).
    
    Args:
        replacement: The replacement string that may contain capture group references
        
    Returns:
        Normalized replacement string compatible with Python's re.sub()
        
    Examples:
        >>> normalize_replacement_string(r'\1')
        '\\g<1>'
        >>> normalize_replacement_string(r'$1')
        '\\g<1>'
        >>> normalize_replacement_string(r'\\1')
        '\\\\1'
        >>> normalize_replacement_string(r'\1 and \2')
        '\\g<1> and \\g<2>'
        >>> normalize_replacement_string(r'$1 and $2')
        '\\g<1> and \\g<2>'
    """
    # Pattern to match \1, \2, etc. but NOT \\1, \\2
    # Uses negative lookbehind to avoid matching escaped backslashes
    # The pattern matches a backslash followed by one or more digits,
    # but only if that backslash is not preceded by another backslash
    backslash_pattern = r'(?<!\\)\\(\d+)'
    
    # Replace \1 with \g<1>, \2 with \g<2>, etc.
    # The \g<...> syntax is Python's explicit group reference format
    normalized = re.sub(backslash_pattern, r'\\g<\1>', replacement)
    
    # Also convert $1, $2, etc. to \g<1>, \g<2>
    # Python's re.sub doesn't support $1 syntax natively
    dollar_pattern = r'\$(\d+)'
    normalized = re.sub(dollar_pattern, r'\\g<\1>', normalized)
    
    return normalized

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

    def build_from_file(self, filepath: str, encoding: str = 'utf-8', check_cancel: Optional[callable] = None) -> None:
        """Build line index from a file using mmap for efficiency."""
        file_size = os.path.getsize(filepath)
        self._total_size = file_size
        self._offsets = array('Q', [0])
        self._lengths = array('Q')
        self._implicit_lengths = False  # Reset
        
        if file_size == 0:
            self._lengths.append(0)
            return
        
        # Determine newline sequence and step size based on encoding
        encoding_lower = encoding.lower() if encoding else 'utf-8'
        if 'utf-16' in encoding_lower or 'utf16' in encoding_lower:
            # UTF-16 handling
            step = 2
            
            # Check BOM to determine endianness
            with open(filepath, 'rb') as f:
                bom = f.read(2)
            
            if bom == b'\xfe\xff':  # BE BOM
                newline_seq = b'\x00\n'
            else:  # LE BOM or no BOM (default to LE)
                newline_seq = b'\n\x00'
        else:
            # UTF-8 / ASCII
            newline_seq = b'\n'
            step = 1
        
        # Save newline length for potential implicit mode
        newline_len = len(newline_seq)
        
        current_pos = 0
        try:
            with open(filepath, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    # Skip BOM if present for UTF-16
                    if 'utf-16' in encoding_lower and file_size >= 2:
                        bom_check = mm[:2]
                        if bom_check in (b'\xff\xfe', b'\xfe\xff'):
                            current_pos = 2
                            self._offsets[0] = 2  # Update start position
                    
                    while current_pos < file_size:
                        next_newline = mm.find(newline_seq, current_pos)
                        
                        # Cancellation check
                        if check_cancel and check_cancel():
                            raise CancelledError("Indexing cancelled")
                        
                        if next_newline == -1:
                            # Last line without newline
                            length = file_size - current_pos
                            self._lengths.append(length)
                            break
                        else:
                            length = next_newline - current_pos
                            self._lengths.append(length)
                            
                            # Move past the full newline sequence
                            current_pos = next_newline + newline_len
                            if current_pos < file_size:
                                self._offsets.append(current_pos)
                    
                            
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"Error building index: {e}")
            self._offsets = array('Q', [0])
            self._lengths = array('Q', [0])
    
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
    
    
    def write_byte_range(self, mm: mmap.mmap, start_line: int, end_line: int, outfile) -> None:
        """Write raw bytes for a range of lines directly to outfile."""
        if start_line >= len(self._offsets): return
        
        start_off = self._offsets[start_line]
        
        # Calculate end offset
        if end_line < len(self._offsets):
            end_off = self._offsets[end_line]
        else:
            # Last line(s)
            end_off = self._total_size
            
        if end_off > start_off:
            outfile.write(mm[start_off:end_off])

    
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


class SegmentedLineMap:
    """
    Performance-optimized map for virtual buffer lines.
    Allows O(1) initialization (via range) and O(segments) updates.
    Solves the initialization freeze and edit freeze of large arrays.
    """
    def __init__(self, total_count):
        self.segments = [range(total_count)]
        self._total_len = total_count
        self.offsets = array('Q', [0])
        self.overrides = {}

    # ---- COMPATIBILITY SHIMS ----

    def __len__(self):
        return self._total_len
        
    def _rebuild_offsets(self):
        """Rebuild offsets array from segments."""
        offs = [0]
        curr = 0
        for seg in self.segments:
            curr += len(seg)
            offs.append(curr)
        # We only need start offsets, but keeping (start) is enough if we know last end via total_len
        # Actually bisect works on starts.
        # Removing the last element which is total_len?
        # bisect_right on [0, 10, 20] for index 5 returns 1. index 1-1 = 0. Segment 0.
        # bisect_right for index 10 returns 2. index 2-1 = 1. Segment 1.
        # So we just need starts.
        self.offsets = array('Q', offs[:-1])

    def __len__(self):
        return self._total_len

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._total_len)
            return [self[i] for i in range(start, stop, step)]
    
        if idx < 0:
            idx += self._total_len
        if idx < 0 or idx >= self._total_len:
            raise IndexError(idx)
    
        if idx in self.overrides:
            return self.overrides[idx]
    
        import bisect
        seg_idx = bisect.bisect_right(self.offsets, idx) - 1
        seg_start = self.offsets[seg_idx]
        return self.segments[seg_idx][idx - seg_start]




    def _try_merge(self, idx):
        """Try to merge segment at idx with idx+1 if compliant."""
        if idx < 0 or idx >= len(self.segments) - 1:
            return False
            
        bs = self.segments[idx]
        nex = self.segments[idx+1]
        
        # Only merge mutable arrays/lists
        if isinstance(bs, (array, list)) and isinstance(nex, (array, list)):
            if isinstance(bs, list) and not isinstance(nex, list): return False
            if isinstance(bs, array) and not isinstance(nex, array): return False
            # If array, types must match? usually 'q'
            
            # Extend in place if possible?
            self.segments[idx] = bs + nex
            del self.segments[idx+1]
            return True
        return False

    def _flush_overrides(self):
        """Flatten sparse overrides into segments (expensive, O(K log S + K*S))."""
        if not self.overrides: return
        
        # Sort keys to batch updates?
        # Applying one by one for now using splitting logic.
        # But we must be careful: if we split for first override, offsets change? 
        # No, offsets change only if we INSERT/DELETE.
        # replace() splits ranges into [before, val, after]. Length constant.
        # So we can iterate. 
        # BUT: modifying segments invalidates indices for subsequent operations?
        # Our replace() logic does lookup via offsets.
        # As long as we rebuild offsets or update offsets incrementally, it works.
        # For efficiency, we can rebuild offsets once at end? 
        # But lookups need valid offsets.
        # For safety/simplicity, we reuse the old 'replace' logic but applied in loop.
        
        # Sort keys
        keys = sorted(self.overrides.keys())
        for idx in keys:
            val = self.overrides[idx]
            self._real_replace(idx, val)
        
        self.overrides.clear()
        
    def _real_replace(self, idx, value):
        """Original replace implementation that modifies segments."""
        if idx < 0: idx += self._total_len
        if idx < 0 or idx >= self._total_len: return

        import bisect
        seg_idx = bisect.bisect_right(self.offsets, idx) - 1
        seg_start = self.offsets[seg_idx]
        offset = idx - seg_start
        seg = self.segments[seg_idx]
        
        if isinstance(seg, (list, array)):
            seg[offset] = value
            return

        new_segs = []
        if offset > 0:
            new_segs.append(seg[:offset])
        
        new_segs.append(array('q', [value]))
        
        slen = len(seg)
        if offset < slen - 1:
            new_segs.append(seg[offset+1:])
        
        self.segments[seg_idx:seg_idx+1] = new_segs
        self._rebuild_offsets()
        
        # Optimization: Try merging adjacent mutables
        mut_idx = seg_idx if offset == 0 else seg_idx + 1
        if self._try_merge(mut_idx): self._rebuild_offsets()
        if self._try_merge(mut_idx - 1): self._rebuild_offsets()

    def replace(self, idx, value):
        if idx < 0: idx += self._total_len
        if idx < 0 or idx >= self._total_len: return
        
        # Fast path: just update override
        self.overrides[idx] = value


    def insert_map(self, idx, vals):
        self._flush_overrides() # Must flush before structural change
        if idx < 0: idx += self._total_len

        if idx > self._total_len: idx = self._total_len
        
        self._total_len += len(vals)
        if isinstance(vals, list):
            vals = array('q', vals)
            
        import bisect
        
        if idx == 0:
            self.segments.insert(0, vals)
            # Try merge with next
            if self._try_merge(0): pass
            self._rebuild_offsets()
            return

        seg_idx = bisect.bisect_right(self.offsets, idx) - 1
        # If exact match to next segment start?
        # bisect_right( [0, 10], 10) -> 2. index 1. Segment 1 starts at 10.
        # if idx == 10. offset = 0.
        # We want to insert BEFORE segment 1.
        
        # Handling append at very end
        if idx == self._total_len - len(vals): # Total len already increased
             # Append
             self.segments.append(vals)
             # Try merge
             if self._try_merge(len(self.segments)-2): pass
             self._rebuild_offsets()
             return

        seg_start = self.offsets[seg_idx]
        offset = idx - seg_start
        seg = self.segments[seg_idx]
        
        if offset == len(seg):
             # Insert after this segment (before next)
             self.segments.insert(seg_idx + 1, vals)
             if self._try_merge(seg_idx): pass # Merge current and new? No, new is next.
             # Merge new and next_next?
             # Let's simple rebuild
             self._rebuild_offsets()
             # Try merge logic properly: check neighbors of inserted
             if self._try_merge(seg_idx): self._rebuild_offsets() # Merge seg_idx and seg_idx+1
             if self._try_merge(seg_idx+1): self._rebuild_offsets() 
             return
             
        if offset == 0:
             # Insert before this segment
             self.segments.insert(seg_idx, vals)
             self._rebuild_offsets()
             if self._try_merge(seg_idx-1): self._rebuild_offsets()
             if self._try_merge(seg_idx): self._rebuild_offsets()
             return

        # Split
        left = seg[:offset]
        right = seg[offset:]
        self.segments[seg_idx:seg_idx+1] = [left, vals, right]
        self._rebuild_offsets()
        
        # Merge attempts
        # seg_idx + 1 is the new vals
        if self._try_merge(seg_idx+1): self._rebuild_offsets() # with right
        if self._try_merge(seg_idx): self._rebuild_offsets() # with left/vals (refshifted)

    def delete_range(self, start, end):
        self._flush_overrides() # Must flush before structural change
        if start < 0: start = 0

        if end > self._total_len: end = self._total_len
        if start >= end: return

        count = end - start
        self._total_len -= count
        
        import bisect
        start_seg_idx = bisect.bisect_right(self.offsets, start) - 1
        
        new_segments = []
        # Keep segments before start_seg_idx
        new_segments.extend(self.segments[:start_seg_idx])
        
        # Scan from start_seg_idx
        current = self.offsets[start_seg_idx] if self.offsets else 0
        
        for i in range(start_seg_idx, len(self.segments)):
            seg = self.segments[i]
            slen = len(seg)
            seg_start = current
            seg_end = current + slen
            current += slen
            
            overlap_start = max(seg_start, start)
            overlap_end = min(seg_end, end)
            
            if overlap_start < overlap_end:
                if seg_start < overlap_start:
                    new_segments.append(seg[:overlap_start - seg_start])
                if overlap_end < seg_end:
                    new_segments.append(seg[overlap_end - seg_start:])
            elif seg_start >= end:
                 new_segments.append(seg)
        
        self.segments = new_segments
        self._rebuild_offsets()


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
        self.syntax_engine = StateAwareSyntaxEngine()
        self.syntax_engine.set_text_provider(self.get_line)
        
        # Memory optimized line mapping:
        # _lines is SegmentedLineMap (Piece Table)
        # - Values >= 0: Index into original file (via indexer)
        # - Values < 0: Index into _modified_cache (bitwise NOT, so -1 -> 0, -2 -> 1)
        self._lines = SegmentedLineMap(1) 
        self._modified_cache: Dict[int, str] = {}
        self._next_mod_id = 1
        
        self._is_modified: bool = False
        self._observers = []
        self.selection = Selection()
        self._suppress_notifications = 0
        self._size_delta: int = 0 # Track size changes relative to indexer

    def set_language(self, lang):
        current_engine = self.syntax_engine
        
        if not isinstance(current_engine, StateAwareSyntaxEngine):
            print(f"Switching to StateAwareSyntaxEngine for {lang}")
            self.syntax_engine = StateAwareSyntaxEngine()
            self.syntax_engine.set_text_provider(self.get_line)
        
        self.syntax_engine.set_language(lang)

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

    def get_line_at_offset(self, byte_offset: int):
        """
        Map global byte offset to (line, col).
        Handles appended text that is not yet in the static indexer.
        """
        # 1. Try static indexer
        idx, off_in_line = self._indexer.get_line_at_offset(byte_offset)
        
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

    def load_from_indexed_file(self, idx_file, emit_changed=True, progress_callback=None) -> None:
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
        cnt = self._indexer.line_count
        
        # Use SegmentedLineMap for O(1) initialization!
        self._lines = SegmentedLineMap(cnt)

        if progress_callback:
            progress_callback(1.0)
        
        self._modified_cache = {}
        self._size_delta = 0
        self._is_modified = False
        
        if emit_changed:
            self._notify_observers()
            
        # Ensure syntax engine is reset/notified
        # If it's TreeSitter, it needs to load text if language is set.
        # But set_language will be called AFTER this usually.
        # However, if language WAS set, we might need to reset.
        
        # For robustness, we can invalidate.
        self.syntax_engine.invalidate_from(0)

    def load_file(self, filepath: str, encoding: Optional[str] = None, progress_callback: Optional[callable] = None, check_cancel: Optional[callable] = None) -> None:
        """Load a file using lazy index. Patched to detect encoding BEFORE indexing."""
        # For thread safety, do not close() here if running in background thread implicitly? 
        # But this function mutates self state significantly.
        # It is expected to be called from a worker thread but updates internal references cleanly.
        # However, accessing self.close() which might clear buffers used by UI is risky?
        # The 'safe' way is for this function to build NEW state and only apply it at end.
        # But for now, we follow the Plan: direct call, relying on UI disabled/loading state.
        self.close()
        
        self._filepath = filepath
        file_size = os.path.getsize(filepath)
        
        # 1. Handle Empty Files (Preserved)
        if file_size == 0:
            self._lines = SegmentedLineMap(1) # One empty line
            self._modified_cache = {0: ""}
            self._size_delta = 0
            self._is_modified = False 
            self._notify_observers()
            return
        
        # 2. PATCH: Detect Encoding FIRST
        # We must establish the encoding before building the index so the 
        # indexer knows if it needs to step 2 bytes for UTF-16 newlines.
        if encoding:
            self.current_encoding = encoding
        else:
            # Assuming detect_encoding is the imported function available in scope
            self.current_encoding = detect_encoding(filepath)

        # 3. Build line index using the CONFIRMED encoding
        self._indexer.build_from_file(filepath, encoding=self.current_encoding, check_cancel=check_cancel)
            
        # 4. Open mmap (Preserved)
        self._file = open(filepath, 'rb')
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        
        # 5. Initialize lines as lazy identity map (Preserved)
        cnt = self._indexer.line_count
        self._lines = SegmentedLineMap(cnt)
        
        # 6. Final Progress Report (Preserved)
        if progress_callback:
            progress_callback(1.0)

        # 7. Reset tracking structures (Preserved)
        self._modified_cache = {}
        self._size_delta = 0
        self._is_modified = False
        self._notify_observers()
    
    def load_text(self, text: str) -> None:
        """Load text directly into buffer."""
        self.close()
        lines = text.split('\n')
        if len(text) > 0 and text.endswith('\n'):
            lines.pop()
        
        self._size_delta = len(text.encode('utf-8'))
        
        # All lines are "modified" (memory residents)
        mod_ids = []
        for i, line in enumerate(lines):
            mod_id = - (i + 1)
            self._modified_cache[~mod_id] = line # Bitwise not to map -1 -> 0
            mod_ids.append(mod_id)
            
        self._lines = SegmentedLineMap(0)
        self._lines.segments = [array('q', mod_ids)]
        self._lines._total_len = len(mod_ids)
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
            self.load_from_indexed_file(source, emit_changed=emit_changed)
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
        Resolve a logical line index to actual text.

        Resolution order:
        1. Identity map (no _lines): line_idx is file line
        2. SegmentedLineMap: returns int (file line) or negative (modified cache)
        3. Decode ONLY from mmap / indexer
        """

        # ----------------------------
        # Step 1: resolve logical → physical
        # ----------------------------
        if self._lines is None:
            item = line_idx
        else:
            item = self._lines[line_idx]

        # ----------------------------
        # Step 2: modified in-memory line
        # ----------------------------
        if isinstance(item, int) and item < 0:
            cache_idx = ~item
            return self._modified_cache.get(cache_idx, "")

        # ----------------------------
        # Step 3: physical file line
        # ----------------------------
        if not isinstance(item, int) or item < 0:
            return ""

        if not self._mmap:
            return ""

        info = self._indexer.get_line_info(item)
        if not info:
            return ""

        self._mmap.seek(info.offset)
        data = self._mmap.read(info.length)
        
        encoding = getattr(self, "current_encoding", "utf-8")
        enc = encoding.lower().replace("-", "")
        
        if enc.startswith("utf16"):
            # Strip CR (Windows line endings)
            if data.endswith(b"\r\x00") or data.endswith(b"\x00\r"):
                data = data[:-2]
        
            # Strip trailing UTF-16 NUL code unit (U+0000)
            if data.endswith(b"\x00\x00"):
                data = data[:-2]
        
            # Ensure even byte length
            if len(data) & 1:
                data = data[:-1]
        
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            return data.decode(encoding, errors="replace")



             
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
            # Fallback if somehow still None (should be initialized as map now)
            self._lines = SegmentedLineMap(0)
            
        line = min(line, len(self._lines) - 1)
        current_line = self._resolve_line(line)
        col = min(col, len(current_line))
        
        insert_lines = text.split('\n')
        
        if len(insert_lines) == 1:
            self.syntax_engine.invalidate_line(line)
        else:
             self.syntax_engine.invalidate_from(line)
        self._size_delta += len(text.encode('utf-8')) # Approximate using utf-8
        
        before = current_line[:col]
        after = current_line[col:]
        
        if len(insert_lines) == 1:
            # Single line modification
            new_content = before + text + after
            mod_id = self._register_modified_line(new_content)
            self._lines.replace(line, mod_id)
            
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
            
            # Use map insert: first replaces original line, rest inserts after
            self._lines.replace(line, new_ids[0])
            if len(new_ids) > 1:
                self._lines.insert_map(line + 1, new_ids[1:])
            
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
            
        # Trigger TreeSitter re-parse if applicable
        if hasattr(self.syntax_engine, 'apply_edit'):
            # TODO: Pass real byte offsets for incremental parsing
            self.syntax_engine.apply_edit(0, 0, 0, (0,0), (0,0), (0,0))
            
        return end_line, end_col
        
    def insert_newline(self):
        """Insert a newline at cursor position."""
        self.insert(self.cursor_line, self.cursor_col, "\n")
        
    def delete(self, start_line: int, start_col: int, end_line: int, end_col: int, 
               _record_undo: bool = True, provided_text: Optional[str] = None) -> None:
        """Delete text in range."""
        if self._lines is None:
             self._lines = SegmentedLineMap(0)
             
        start_line = max(0, min(start_line, len(self._lines) - 1))
        end_line = max(0, min(end_line, len(self._lines) - 1))
        
        if start_line > end_line or (start_line == end_line and start_col >= end_col):
            return
        if start_line == end_line:
            self.syntax_engine.invalidate_line(start_line)
        else:
            self.syntax_engine.invalidate_from(start_line)
        
        if provided_text is not None:
            deleted = provided_text
        else:
            deleted = self.get_text_range(start_line, start_col, end_line, end_col)
            
        self._size_delta -= len(deleted.encode('utf-8'))
        
        if start_line == end_line:
            # Single line delete
            line = self._resolve_line(start_line)
            before = line[:start_col]
            after = line[end_col:]
            new_content = before + after
            mod_id = self._register_modified_line(new_content)
            self._lines.replace(start_line, mod_id)
        else:
            # Multi-line delete
            first_line = self._resolve_line(start_line)
            last_line = self._resolve_line(end_line)
            
            before = first_line[:start_col]
            after = last_line[end_col:]
            
            # Combine first and last line parts
            new_content = before + after
            mod_id = self._register_modified_line(new_content)
            
            # Replace start_line with merged line
            self._lines.replace(start_line, mod_id)
            
            # Delete intermediate lines (start_line+1 ... end_line)
            # We delete [start_line+1, end_line+1)
            self._lines.delete_range(start_line + 1, end_line + 1)
            
        self._is_modified = True
        self._notify_observers()
        
        if _record_undo and hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            from undo_redo import DeleteCommand, Position
            cmd = DeleteCommand(Position(start_line, start_col), Position(end_line, end_col), deleted)
            self._view.undo_manager.push(cmd)
            
        # Trigger TreeSitter re-parse if applicable
        if hasattr(self.syntax_engine, 'apply_edit'):
            # TODO: Pass real byte offsets for incremental parsing
            self.syntax_engine.apply_edit(0, 0, 0, (0,0), (0,0), (0,0))
            
        return deleted

    def replace(self, start_line: int, start_col: int, 
                end_line: int, end_col: int, text: str, _record_undo=True) -> Tuple[str, int, int]:
        """Replace text in range."""
        deleted = self.delete(start_line, start_col, end_line, end_col, _record_undo=_record_undo)
        new_end_line, new_end_col = self.insert(start_line, start_col, text, _record_undo=_record_undo)
        return (deleted, new_end_line, new_end_col)

    def save(self, save_path: str) -> None:
        """Save the current buffer content to a file."""
        self.save_optimized(save_path)

    def save_optimized(self, save_path: str) -> None:
        """Optimized save that writes raw chunks from mmap where possible."""
        # Ensure overrides are flushed to segments
        self._lines._flush_overrides()
        
        current_enc = getattr(self, 'current_encoding', 'utf-8')
        
        with open(save_path, 'wb') as f:
            for seg in self._lines.segments:
                if isinstance(seg, range):
                    # Optimized path: Contiguous range from original file
                    # Only valid if mmap exists and covers these lines
                    if self._mmap and seg.start < self._indexer.line_count:
                        # end is exclusive in range, and exclusive in write_byte_range
                        self._indexer.write_byte_range(self._mmap, seg.start, seg.stop, f)
                    else:
                        # Fallback (should not happen for valid range if originating from file)
                        for i in seg:
                            line = self._resolve_line(i)
                            f.write(line.encode(current_enc))
                            # Line from file might not have newline if it was EOF? 
                            # _resolve_line strips newlines?
                            # Wait, write_byte_range writes raw bytes including newlines.
                            # _resolve_line returns content. 
                            # If we fall back here, we need to know if we should add newline.
                            # Standard text editor behavior: separate by newline.
                            f.write("\n".encode(current_enc)) 
                            
                elif isinstance(seg, (list, array)):
                    # Check if it's a contiguous run of positive integers (file lines)
                    # For simplicity, we just iterate. optimizing array based checking is overhead unless huge run.
                    # Mix of modified (-id) and original (+id)
                    for item in seg:
                        if isinstance(item, int) and item >= 0 and self._mmap and item < self._indexer.line_count:
                            self._indexer.write_byte_range(self._mmap, item, item + 1, f)
                        else:
                            # Modified line (negative ID) or fallback
                            # Logic: retrieve text, encode, write, add newline
                            # Note: _resolve_line returns text WITHOUT newline usually?
                            # Let's check get_line.
                            line = self._resolve_line(item) if not (isinstance(item, int) and item < 0) else self._modified_cache.get(~item, "")
                            
                            # Encode newline correctly
                            if isinstance(line, str):
                                # If we resolved a string, it might not have the newline
                                # We need to append the newline using the file's encoding
                                f.write(line.encode(current_enc))
                                f.write("\n".encode(current_enc))
                            else:
                                # Fallback if _resolve_line somehow returned bytes (unlikely based on type hint)
                                f.write(line)
                                f.write("\n".encode(current_enc))
        
        self._filepath = save_path
        self._is_modified = False
        self.load_file(save_path)

    # ==================== Indentation ====================

    def indent_selection(self):
        """Indent selected lines."""
        if not self.selection.has_selection():
             return

        # Get line range
        start_ln, start_col, end_ln, end_col = self.selection.get_bounds()
        
        # Adjust end_ln if selection ends at start of line
        # e.g. selecting line 1 fully: (1, 0) to (2, 0). Should only affect line 1.
        if end_ln > start_ln and end_col == 0:
            end_ln -= 1
            
        indent_str = "    " # Default to 4 spaces
        
        with self.batch_notifications():
             for ln in range(start_ln, end_ln + 1):
                 self.insert(ln, 0, indent_str, _record_undo=True)

    def unindent_selection(self):
        """Unindent selected lines or current line."""
        start_ln = self.cursor_line
        end_ln = self.cursor_line
        
        if self.selection.has_selection():
            start_ln, _, end_ln, end_col = self.selection.get_bounds()
            if end_ln > start_ln and end_col == 0:
                end_ln -= 1
                
        with self.batch_notifications():
            for ln in range(start_ln, end_ln + 1):
                line_text = self.get_line(ln)
                if not line_text: continue
                
                # Check what to remove
                if line_text.startswith('\t'):
                    self.delete(ln, 0, ln, 1, _record_undo=True)
                elif line_text.startswith('    '):
                    self.delete(ln, 0, ln, 4, _record_undo=True)
                elif line_text.startswith(' '):
                    # Remove up to 4 spaces
                    count = 0
                    while count < 4 and count < len(line_text) and line_text[count] == ' ':
                        count += 1
                    if count > 0:
                        self.delete(ln, 0, ln, count, _record_undo=True)

    # ==================== Search & Replace ====================

    def search(self, query: str, case_sensitive: bool = False, 
               is_regex: bool = False, max_matches: int = -1) -> Tuple[List[Tuple[int, int, int, int]], int]:
        """
        Search for query in the entire buffer.
        Returns (list of (start_line, start_col, end_line, end_col), max_match_length).
        """
        if not query:
            return [], 0
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                return [], 0
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        max_len = 0
        
        # Iterate all lines (lazy)
        # Note: This forces loading of lines. For huge files, use search_viewport or async logic.
        for ln in range(len(self._lines)):
            line = self.get_line(ln)
            
            if is_regex:
                for m in pattern.finditer(line):
                    match_len = m.end() - m.start()
                    if match_len > max_len: max_len = match_len
                    matches.append((ln, m.start(), ln, m.end()))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches, max_len
            else:
                search_line = line if case_sensitive else line.lower()
                query_len = len(query)
                if query_len > max_len: max_len = query_len
                
                start = 0
                while True:
                    idx = search_line.find(query, start)
                    if idx == -1:
                        break
                    matches.append((ln, idx, ln, idx + query_len))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches, max_len
                    start = idx + 1
                    
        return matches, max_len

    def begin_action(self):
        """Start a batch of undoable actions."""
        if hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            print("VirtualBuffer: begin_action calling manager.begin_batch()")
            self._view.undo_manager.begin_batch()
        else:
            print("VirtualBuffer: begin_action failed - no view/manager")

    def end_action(self):
        """End the current batch."""
        if hasattr(self, '_view') and hasattr(self._view, 'undo_manager'):
            print("VirtualBuffer: end_action calling manager.end_batch()")
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
            if on_complete: on_complete([], 0)
            return lambda: None
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                if on_complete: on_complete([], 0)
                return lambda: None
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        max_len = 0
        current_ln = 0
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_ln, max_len
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
                        ml = m.end() - m.start()
                        if ml > max_len: max_len = ml
                        matches.append((ln, m.start(), ln, m.end()))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches, max_len)
                            return False
                else:
                    search_line = line if case_sensitive else line.lower()
                    ql = len(query)
                    if ql > max_len: max_len = ql
                    start = 0
                    while True:
                        idx = search_line.find(query, start)
                        if idx == -1: break
                        matches.append((ln, idx, ln, idx + ql))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches, max_len)
                            return False
                        start = idx + 1
            
            current_ln += processed_count
            
            if on_progress:
                on_progress(matches, current_ln, len(self._lines), max_len)
                
            if current_ln >= len(self._lines):
                if on_complete: on_complete(matches, max_len)
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
            if on_complete: on_complete([], 0)
            return lambda: None
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                if on_complete: on_complete([], 0)
                return lambda: None
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        max_len = 0
        current_ln = max(0, start_line)  # Start from specified line
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_ln, max_len
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
                        ml = m.end() - m.start()
                        if ml > max_len: max_len = ml
                        matches.append((ln, m.start(), ln, m.end()))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches, max_len)
                            return False
                else:
                    search_line = line if case_sensitive else line.lower()
                    ql = len(query)
                    if ql > max_len: max_len = ql
                    start = 0
                    while True:
                        idx = search_line.find(query, start)
                        if idx == -1: break
                        matches.append((ln, idx, ln, idx + ql))
                        if max_matches > 0 and len(matches) >= max_matches:
                            if on_complete: on_complete(matches, max_len)
                            return False
                        start = idx + 1
            
            current_ln += processed_count
            
            if on_progress:
                on_progress(matches, current_ln, len(self._lines), max_len)
                
            if current_ln >= len(self._lines):
                if on_complete: on_complete(matches, max_len)
                return False
                
            return True
            
        GLib.idle_add(process_chunk)
        
        def cancel():
            nonlocal is_cancelled
            is_cancelled = True
            
        return cancel

    def search_viewport(self, query: str, case_sensitive: bool, is_regex: bool,
                        start_line: int, end_line: int, max_matches: int = -1) -> Tuple[List[Tuple[int, int, int, int]], int]:
        """Search only within a specific range of lines. Returns (matches, max_len)."""
        if not query:
            return [], 0
            
        pattern = None
        if is_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(query, flags)
            except re.error:
                return [], 0
        else:
            if not case_sensitive:
                query = query.lower()
        
        matches = []
        max_len = 0
        start_line = max(0, start_line)
        end_line = min(len(self._lines), end_line)
        
        for ln in range(start_line, end_line):
            line = self.get_line(ln)
            
            if is_regex:
                for m in pattern.finditer(line):
                    ml = m.end() - m.start()
                    if ml > max_len: max_len = ml
                    matches.append((ln, m.start(), ln, m.end()))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches, max_len
            else:
                search_line = line if case_sensitive else line.lower()
                ql = len(query)
                if ql > max_len: max_len = ql
                start = 0
                while True:
                    idx = search_line.find(query, start)
                    if idx == -1:
                        break
                    matches.append((ln, idx, ln, idx + ql))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches, max_len
                    start = idx + 1
                    
        return matches, max_len



    def replace_all(self, query: str, replacement: str, 
                    case_sensitive: bool = False, is_regex: bool = False) -> int:
        """Replace all occurrences. Returns count."""
        # For compatibility, keeping sync version but it might be slow for huge files
        matches, _ = self.search(query, case_sensitive, is_regex)
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
                          on_progress=None, on_complete=None, chunk_size=1000,
                          target_lines: list = None):
        """
        Async replace all using line-by-line scanning with time budget.
        More robust for unlimited replacements on huge files.
        
        Args:
            target_lines: Optional list/set of line numbers to process. 
                          If provided, scans only these lines (much faster).
        """
        # Ensure suppression flag exists (monkey-patch if needed for running instance, but better in init)
        if not hasattr(self, '_suppress_notifications'):
            self._suppress_notifications = 0
            
        # Define context manager locally if method injection is messy, 
        # but we need to notify observers at end
        
        if is_regex and not isinstance(query, str):
            # Already compiled pattern?
            pattern = query
        else:
            pattern = None
            if is_regex:
                try:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    pattern = re.compile(query, flags)
                except re.error:
                    return None
            else:
                if not case_sensitive:
                    query_lower = query.lower()
        
        self.begin_action()
        count = 0
        
        # Use target_lines or full range
        line_source = sorted(list(target_lines)) if target_lines else None
        total_work = len(line_source) if line_source else self.total()
        
        # Iterator state
        current_idx = 0 
        # If full scan, current_idx corresponds to current_line
        # If target scan, it's index into line_source
        
        is_cancelled = False
        
        def process_chunk():
            nonlocal current_idx, count, is_cancelled
            
            if is_cancelled:
                self.end_action()
                return False
            
            start_time = time.time()
            
            # Use manual suppression logic since we can't easily add context manager method cleanly in this edit block
            if not hasattr(self, '_suppress_notifications'): self._suppress_notifications = 0
            self._suppress_notifications += 1
            
            # Determine chunk range
            start_idx = current_idx
            limit = total_work
            end_chunk_idx = min(start_idx + chunk_size, limit)
            
            
            try:
                # Process chunk using index
                while current_idx < end_chunk_idx:
                    # Resolve line number
                    if line_source:
                        current_line = line_source[current_idx]
                        if current_line >= self.total(): # Safety check if file shrank
                             current_idx += 1
                             continue
                    else:
                        current_line = current_idx
                        
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
                             # Changes line count. 
                             # If iterating linearly (None source), we shift index.
                             if not line_source:
                                 # We need to skip the new lines we just inserted? 
                                 # Or process them? Usually replace-all avoids reprocessing.
                                 # current_idx (which is line no) points to start of replaced block.
                                 # We want next iteration to be after the block.
                                 # Replaced block size = 1 + newlines_added.
                                 # Next loop should be at current_line + 1 + newlines_added.
                                 # current_idx incremented at end of loop is +1. 
                                 # So we add newlines_added to it.
                                 current_idx += newlines_added
                                 # Update chunk end?
                                 end_chunk_idx += newlines_added
                                 limit += newlines_added # Total increased
                                 
                             # If using line_source (targeted), line numbers shift invalidates subsequent target_lines.
                             # This is why we shouldn't use targeted replace if newlines change.
                             # But if we safeguard against it in UI, we might be ok.
                             pass
                    
                    current_idx += 1
            finally:
                 self._suppress_notifications -= 1
                 if self._suppress_notifications == 0:
                     self._notify_observers()
            
            # Update progress
            if on_progress:
                on_progress(count, current_idx, total_work)
                
            if current_idx < limit:
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
