#!/usr/bin/env python3
"""
LazyLineIndexer - Instant file loading with on-demand line indexing.

Key features:
- O(1) file open via mmap (no upfront scanning)
- Estimated line count from file size
- On-demand indexing for visible viewport
- Background progressive indexing via GLib idle
"""
import mmap
import os
from typing import Optional, Callable, List, Tuple
from gi.repository import GLib


class LazyLineIndexer:
    """Memory-mapped file with lazy line indexing for instant huge file loading."""
    
    # Average bytes per line estimate (adjust based on file type)
    AVG_LINE_LENGTH = 45
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.mmap_obj: Optional[mmap.mmap] = None
        self.file_obj = None
        
        # Line offset storage: indexed_offsets[i] = byte offset of line i
        # We start with just [0] (first line starts at byte 0)
        self.indexed_offsets: List[int] = [0]
        
        # Track which byte ranges have been indexed
        # List of (start_byte, end_byte) tuples
        self.indexed_ranges: List[Tuple[int, int]] = []
        
        # Estimated total lines (refined as we index more)
        self._estimated_lines = max(1, self.file_size // self.AVG_LINE_LENGTH)
        
        # True when entire file has been indexed
        self.fully_indexed = False
        
        # Background indexing state
        self._bg_index_offset = 0
        self._bg_idle_id: Optional[int] = None
        
        # Callback for when indexing completes
        self.on_index_complete: Optional[Callable] = None
    
    def open(self):
        """Open file with mmap - O(1) instant."""
        self.file_obj = open(self.filepath, 'rb')
        if self.file_size > 0:
            self.mmap_obj = mmap.mmap(self.file_obj.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            self.mmap_obj = None
    
    def close(self):
        """Close mmap and file."""
        self.stop_background_indexing()
        if self.mmap_obj:
            self.mmap_obj.close()
            self.mmap_obj = None
        if self.file_obj:
            self.file_obj.close()
            self.file_obj = None
    
    def get_estimated_line_count(self) -> int:
        """Get estimated total lines (fast, may be approximate)."""
        if self.fully_indexed:
            return len(self.indexed_offsets)
        return self._estimated_lines
    
    def get_actual_line_count(self) -> int:
        """Get actual indexed line count (only accurate after full indexing)."""
        return len(self.indexed_offsets)
    
    def ensure_lines_indexed(self, start_line: int, end_line: int):
        """Ensure lines in range are indexed (on-demand indexing)."""
        if self.fully_indexed:
            return
        
        if not self.mmap_obj:
            return
        
        # If we need lines beyond what we've indexed, extend indexing
        needed_line = end_line + 1
        
        if needed_line >= len(self.indexed_offsets):
            # Start from last known offset
            if len(self.indexed_offsets) > 0:
                start_offset = self.indexed_offsets[-1]
            else:
                start_offset = 0
            
            # Index until we have enough lines or hit EOF
            self._index_from_offset(start_offset, target_lines=needed_line + 100)
    
    def _index_from_offset(self, start_offset: int, target_lines: int = None, max_bytes: int = None):
        """Index lines starting from a byte offset."""
        if not self.mmap_obj or start_offset >= self.file_size:
            self.fully_indexed = True
            return
        
        # Default: index 1MB at a time or until target lines reached
        if max_bytes is None:
            max_bytes = 1024 * 1024  # 1MB chunk
        
        end_offset = min(start_offset + max_bytes, self.file_size)
        chunk = self.mmap_obj[start_offset:end_offset]
        
        pos = 0
        while pos < len(chunk):
            nl_pos = chunk.find(b'\n', pos)
            if nl_pos == -1:
                break
            
            line_start = start_offset + nl_pos + 1
            if line_start < self.file_size:
                self.indexed_offsets.append(line_start)
            
            pos = nl_pos + 1
            
            # Check if we've reached target
            if target_lines and len(self.indexed_offsets) >= target_lines:
                break
        
        # Check if we've reached EOF
        if end_offset >= self.file_size:
            self.fully_indexed = True
            # Update estimate to actual
            self._estimated_lines = len(self.indexed_offsets)
    
    def start_background_indexing(self, progress_callback: Optional[Callable[[float], None]] = None):
        """Start progressive background indexing."""
        if self.fully_indexed or self._bg_idle_id is not None:
            return
        
        self._bg_index_offset = self.indexed_offsets[-1] if self.indexed_offsets else 0
        
        def bg_index_step():
            if self.fully_indexed or not self.mmap_obj:
                self._bg_idle_id = None
                if self.on_index_complete:
                    self.on_index_complete()
                return False  # Stop idle
            
            # Index a chunk
            old_count = len(self.indexed_offsets)
            self._index_from_offset(self._bg_index_offset, max_bytes=512 * 1024)  # 512KB per step
            
            # Update offset for next step
            if len(self.indexed_offsets) > old_count:
                self._bg_index_offset = self.indexed_offsets[-1]
            else:
                self._bg_index_offset = self.file_size
            
            # Report progress
            if progress_callback and self.file_size > 0:
                progress = min(1.0, self._bg_index_offset / self.file_size)
                progress_callback(progress)
            
            if self.fully_indexed:
                self._bg_idle_id = None
                if self.on_index_complete:
                    self.on_index_complete()
                return False
            
            return True  # Continue idle
        
        self._bg_idle_id = GLib.idle_add(bg_index_step)
    
    def stop_background_indexing(self):
        """Stop background indexing."""
        if self._bg_idle_id is not None:
            GLib.source_remove(self._bg_idle_id)
            self._bg_idle_id = None
    
    def get_line(self, line_num: int) -> str:
        """Get a specific line by number (0-indexed)."""
        # Ensure line is indexed
        self.ensure_lines_indexed(line_num, line_num)
        
        if line_num < 0 or line_num >= len(self.indexed_offsets):
            return ""
        
        if not self.mmap_obj:
            return ""
        
        start = self.indexed_offsets[line_num]
        
        # Find end of line
        if line_num + 1 < len(self.indexed_offsets):
            end = self.indexed_offsets[line_num + 1] - 1  # Exclude newline
        else:
            # Last indexed line - scan for newline or EOF
            end = self.mmap_obj.find(b'\n', start)
            if end == -1:
                end = self.file_size
        
        try:
            return self.mmap_obj[start:end].decode('utf-8', errors='replace')
        except:
            return self.mmap_obj[start:end].decode('latin-1', errors='replace')
    
    def get_lines(self, start_line: int, end_line: int) -> List[str]:
        """Get range of lines efficiently."""
        # Ensure lines are indexed
        self.ensure_lines_indexed(start_line, end_line)
        
        start_line = max(0, start_line)
        end_line = min(end_line, len(self.indexed_offsets) - 1)
        
        if start_line > end_line or not self.mmap_obj:
            return []
        
        lines = []
        for i in range(start_line, end_line + 1):
            lines.append(self.get_line(i))
        
        return lines
    
    def get_byte_offset_for_line(self, line_num: int) -> int:
        """Get byte offset for a line number."""
        self.ensure_lines_indexed(line_num, line_num)
        
        if line_num < 0:
            return 0
        if line_num >= len(self.indexed_offsets):
            return self.file_size
        
        return self.indexed_offsets[line_num]
    
    def get_line_for_byte_offset(self, byte_offset: int) -> int:
        """Get line number for a byte offset (binary search)."""
        if byte_offset <= 0:
            return 0
        if byte_offset >= self.file_size:
            return self.get_estimated_line_count() - 1
        
        # Binary search in indexed offsets
        left, right = 0, len(self.indexed_offsets) - 1
        
        while left < right:
            mid = (left + right + 1) // 2
            if self.indexed_offsets[mid] <= byte_offset:
                left = mid
            else:
                right = mid - 1
        
        return left
