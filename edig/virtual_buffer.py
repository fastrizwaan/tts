"""
Virtual Text Buffer - High-performance text buffer using mmap for millions of lines.

This module provides:
- LineIndexer: Fast line offset lookup using memory-mapped files
- VirtualBuffer: Main buffer class for text operations
"""

import mmap
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple
from functools import lru_cache


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
        self._offsets: List[int] = [0]  # Start of each line
        self._lengths: List[int] = []   # Length of each line (without newline)
        self._total_size: int = 0
    
    def build_from_file(self, filepath: str) -> None:
        """Build line index from a file using mmap for efficiency."""
        file_size = os.path.getsize(filepath)
        self._total_size = file_size
        self._offsets = [0]
        self._lengths = []
        
        if file_size == 0:
            self._lengths.append(0)
            return
        
        with open(filepath, 'rb') as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                pos = 0
                while pos < file_size:
                    # Find next newline
                    newline_pos = mm.find(b'\n', pos)
                    if newline_pos == -1:
                        # Last line without newline
                        self._lengths.append(file_size - pos)
                        break
                    else:
                        self._lengths.append(newline_pos - pos)
                        pos = newline_pos + 1
                        if pos < file_size:
                            self._offsets.append(pos)
                
                # Handle file ending with newline
                if file_size > 0 and mm[file_size - 1:file_size] == b'\n':
                    self._offsets.append(file_size)
                    self._lengths.append(0)
    
    def build_from_text(self, text: str) -> None:
        """Build line index from in-memory text."""
        encoded = text.encode('utf-8')
        self._total_size = len(encoded)
        self._offsets = [0]
        self._lengths = []
        
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
        return len(self._lengths)
    
    def get_line_info(self, line_num: int) -> Optional[LineInfo]:
        """Get offset and length for a line (0-indexed)."""
        if 0 <= line_num < len(self._offsets):
            return LineInfo(
                offset=self._offsets[line_num],
                length=self._lengths[line_num] if line_num < len(self._lengths) else 0
            )
        return None
    
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
                self._offsets = self._offsets[:line_num + 1] + new_offsets + self._offsets[line_num + 1:]
                self._lengths = self._lengths[:line_num + 1] + new_lengths[1:-1] + [new_lengths[-1] + remaining] + self._lengths[line_num + 1:]
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
        self._lines: List[Any] = [""]  # List of str or int
        self._is_modified: bool = False
    
    def load_file(self, filepath: str) -> None:
        """Load a file using lazy index."""
        self.close()
        
        self._filepath = filepath
        file_size = os.path.getsize(filepath)
        
        if file_size == 0:
            self._lines = [""]
            return
        
        # Build line index (fast scan)
        self._indexer.build_from_file(filepath)
        
        # Open mmap for random access
        self._file = open(filepath, 'rb')
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        
        # Initialize lines as lazy indices
        # This is fast: essentially a list of integers [0, 1, 2, ... N]
        self._lines = list(range(self._indexer.line_count))
        self._is_modified = False
    
    def load_text(self, text: str) -> None:
        """Load text directly into buffer."""
        self.close()
        self._lines = text.split('\n')
        self._is_modified = True
    
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
        return len(self._lines)
    
    @property
    def is_modified(self) -> bool:
        """Whether buffer has unsaved modifications."""
        return self._is_modified
    
    def _resolve_line(self, line_idx: int) -> str:
        """
        Internal: Resolve a line content.
        If it's an int, load from mmap and cache it as str.
        """
        item = self._lines[line_idx]
        if isinstance(item, int):
            # It's a lazy index
            if self._mmap:
                info = self._indexer.get_line_info(item)
                if info:
                    self._mmap.seek(info.offset)
                    data = self._mmap.read(info.length)
                    try:
                        text = data.decode('utf-8')
                    except UnicodeDecodeError:
                        text = data.decode('utf-8', errors='replace')
                    
                    # Cache it (replace int with str in list)
                    self._lines[line_idx] = text
                    return text
            return "" # Error fallback
        return item
    
    def get_line(self, line_num: int) -> str:
        """Get content of a specific line (0-indexed)."""
        if line_num < 0 or line_num >= len(self._lines):
            return ""
        return self._resolve_line(line_num)
    
    def get_lines(self, start_line: int, count: int) -> List[str]:
        """Get multiple consecutive lines efficiently."""
        result = []
        end_line = min(start_line + count, len(self._lines))
        for i in range(start_line, end_line):
            result.append(self.get_line(i))
        return result
    
    def get_line_length(self, line_num: int) -> int:
        """Get length of a specific line in characters."""
        return len(self.get_line(line_num))
    
    def get_text(self) -> str:
        """Get full text content."""
        # Force resolve all
        return '\n'.join([self.get_line(i) for i in range(len(self._lines))])
    
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

    def insert(self, line: int, col: int, text: str) -> Tuple[int, int]:
        """Insert text at position."""
        if not self._lines: self._lines = [""]
        while len(self._lines) <= line:
            self._lines.append("")
        
        # Resolve target line to string
        current_line = self._resolve_line(line)
        
        before = current_line[:col]
        after = current_line[col:]
        
        insert_lines = text.split('\n')
        
        if len(insert_lines) == 1:
            self._lines[line] = before + text + after
            end_line = line
            end_col = col + len(text)
        else:
            first_part = before + insert_lines[0]
            last_part = insert_lines[-1] + after
            
            replacement = [first_part] + insert_lines[1:-1] + [last_part]
            self._lines[line:line+1] = replacement
            
            end_line = line + len(insert_lines) - 1
            end_col = len(insert_lines[-1])
            
        self._is_modified = True
        return (end_line, end_col)

    def delete(self, start_line: int, start_col: int, 
               end_line: int, end_col: int) -> str:
        """Delete text in range."""
        if not self._lines: self._lines = [""]
        start_line = min(start_line, len(self._lines) - 1)
        end_line = min(end_line, len(self._lines) - 1)
        
        deleted = self.get_text_range(start_line, start_col, end_line, end_col)
        
        if start_line == end_line:
            line = self._resolve_line(start_line)
            self._lines[start_line] = line[:start_col] + line[end_col:]
        else:
            first_line = self._resolve_line(start_line)
            last_line = self._resolve_line(end_line)
            
            first_kept = first_line[:start_col]
            last_kept = last_line[end_col:]
            
            self._lines[start_line] = first_kept + last_kept
            del self._lines[start_line + 1:end_line + 1]
            
        self._is_modified = True
        return deleted

    def replace(self, start_line: int, start_col: int, 
                end_line: int, end_col: int, text: str) -> Tuple[str, int, int]:
        """Replace text in range."""
        deleted = self.delete(start_line, start_col, end_line, end_col)
        new_end_line, new_end_col = self.insert(start_line, start_col, text)
        return (deleted, new_end_line, new_end_col)

    def save_to_file(self, filepath: Optional[str] = None) -> None:
        """Save buffer content to file."""
        save_path = filepath or self._filepath
        if not save_path:
            raise ValueError("No file path specified")
        
        # Stream save
        with open(save_path, 'w', encoding='utf-8') as f:
            for i in range(len(self._lines)):
                # This resolves and writes line by line
                # Without loading full file into RAM string
                line = self._resolve_line(i)
                if i > 0:
                    f.write('\n')
                f.write(line)
            
        self._filepath = save_path
        self._is_modified = False
        self.load_file(save_path)
