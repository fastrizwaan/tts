"""
Word Wrap System - Smooth lazy word wrap (no precomputation).

KEY INSIGHT: For smooth performance like no-wrap mode, we:
1. Track scroll position in LOGICAL lines, not visual lines
2. Only compute wrap for the ~50 visible lines per frame
3. Never precompute the entire file
4. Accept scrollbar estimation (minor inaccuracy is fine)

This makes word wrap O(viewport_size) per frame, same as no-wrap mode.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from virtual_buffer import VirtualBuffer


@dataclass
class WrapInfo:
    """Information about how a logical line wraps into visual lines."""
    line_num: int
    break_points: List[int] = field(default_factory=list)
    visual_line_count: int = 1
    
    @property
    def is_wrapped(self) -> bool:
        return self.visual_line_count > 1


class VisualLineMapper:
    """
    Lazy word wrap mapper - O(viewport) per frame.
    
    Instead of precomputing all lines, we:
    - Track scroll position in logical lines
    - Compute wrap only for visible lines during render
    - Use LRU cache for recently accessed lines
    """
    
    def __init__(self, buffer: 'VirtualBuffer'):
        self._buffer = buffer
        self._viewport_width: int = 80
        self._char_width: float = 10.0
        self._enabled: bool = False
        
        # LRU cache for wrap info (limited size)
        self._cache: Dict[int, WrapInfo] = {}
        self._cache_order: List[int] = []  # For LRU eviction
        self._max_cache_size: int = 500  # Cache ~500 lines
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool) -> None:
        if self._enabled != value:
            self._enabled = value
            self.invalidate_all()
    
    def set_viewport_width(self, width_pixels: float, char_width: float = 10.0) -> None:
        """Update viewport width in pixels."""
        new_width = max(20, int(width_pixels / char_width))
        if new_width != self._viewport_width or char_width != self._char_width:
            self._viewport_width = new_width
            self._char_width = char_width
            self.invalidate_all()
    
    def set_char_width(self, chars: int) -> None:
        """Set viewport width directly in characters."""
        if chars != self._viewport_width:
            self._viewport_width = max(20, chars)
            self.invalidate_all()
    
    def invalidate_all(self) -> None:
        """Invalidate all cached wrap info."""
        self._cache.clear()
        self._cache_order.clear()
        self._cached_total = None

    def invalidate(self, start_line: int, end_line: int = -1) -> None:
        """Invalidate wrap info for a range of lines."""
        if end_line < 0:
            end_line = start_line
        
        # If we invalidate any lines, the total cache might be wrong
        # Ideally we update incrementally, but simpler to just clear total cache
        # If edit is small, total estimation (sampled) might not change much, but
        # correctness requires re-check.
        self._cached_total = None
        
        for line in range(start_line, min(end_line + 1, start_line + 50)):
            if line in self._cache:
                del self._cache[line]
                if line in self._cache_order:
                    self._cache_order.remove(line)
    
    def _compute_wrap_info(self, line_num: int) -> WrapInfo:
        """Compute wrap info for a single line."""
        if not self._enabled:
            return WrapInfo(line_num=line_num)
        
        line_text = self._buffer.get_line(line_num)
        if not line_text:
            return WrapInfo(line_num=line_num)
        
        line_len = len(line_text)
        if line_len <= self._viewport_width:
            return WrapInfo(line_num=line_num)
        
        # Find break points - fast character-based wrap
        break_points = []
        pos = 0
        width = self._viewport_width
        
        while pos < line_len:
            remaining = line_len - pos
            if remaining <= width:
                break
            
            target = pos + width
            break_pos = target
            
            # Quick look-back for space (limited)
            for i in range(min(target, line_len - 1), max(pos, target - 10), -1):
                if line_text[i] in ' \t':
                    break_pos = i + 1
                    break
            
            break_points.append(break_pos)
            pos = break_pos
        
        return WrapInfo(
            line_num=line_num,
            break_points=break_points,
            visual_line_count=len(break_points) + 1
        )
    
    def get_wrap_info(self, line_num: int) -> WrapInfo:
        """Get wrap info for a line with LRU caching."""
        if line_num < 0 or line_num >= self._buffer.total_lines:
            return WrapInfo(line_num=line_num)
        
        if line_num in self._cache:
            # Move to front of LRU
            if line_num in self._cache_order:
                self._cache_order.remove(line_num)
            self._cache_order.append(line_num)
            return self._cache[line_num]
        
        # Compute and cache
        info = self._compute_wrap_info(line_num)
        self._cache[line_num] = info
        self._cache_order.append(line_num)
        
        # Evict old entries
        while len(self._cache) > self._max_cache_size:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)
        
        return info
    
    def get_visual_line_count(self, line_num: int) -> int:
        """Get the number of visual lines for a logical line."""
        if not self._enabled:
            return 1
        return self.get_wrap_info(line_num).visual_line_count
    
    def get_total_visual_lines(self) -> int:
        """
        Estimate total visual lines.
        Cached for performance during scrolling.
        """
        if not self._enabled:
            return self._buffer.total_lines
            
        if hasattr(self, '_cached_total') and self._cached_total is not None:
            return self._cached_total
        
        total = self._buffer.total_lines
        if total == 0:
            self._cached_total = 1
            return 1
        
        result = 0
        if total < 1000:
            # Exact calculation for small/medium files
            count = 0
            for i in range(total):
                count += self.get_visual_line_count(i)
            result = int(count * 1.05)
        else:
            # Structural Sampling for large files
            samples = 100
            step = max(1, total // samples)
            
            sampled_vis_lines = 0
            sampled_count = 0
            
            for i in range(0, total, step):
                lines = self.get_visual_line_count(i)
                sampled_vis_lines += lines
                sampled_count += 1
            
            if sampled_count > 0:
                avg_vis_per_logical = sampled_vis_lines / sampled_count
                result = int(total * avg_vis_per_logical * 1.05)
            else:
                result = int(total * 1.05)
                
        self._cached_total = result
        return result
        """Compute wrap info for a single line."""
        if not self._enabled:
            return WrapInfo(line_num=line_num)
        
        line_text = self._buffer.get_line(line_num)
        if not line_text:
            return WrapInfo(line_num=line_num)
        
        line_len = len(line_text)
        if line_len <= self._viewport_width:
            return WrapInfo(line_num=line_num)
        
        # Find break points - fast character-based wrap
        break_points = []
        pos = 0
        width = self._viewport_width
        
        while pos < line_len:
            remaining = line_len - pos
            if remaining <= width:
                break
            
            target = pos + width
            break_pos = target
            
            # Quick look-back for space (limited)
            for i in range(min(target, line_len - 1), max(pos, target - 10), -1):
                if line_text[i] in ' \t':
                    break_pos = i + 1
                    break
            
            break_points.append(break_pos)
            pos = break_pos
        
        return WrapInfo(
            line_num=line_num,
            break_points=break_points,
            visual_line_count=len(break_points) + 1
        )
    
    def get_wrap_info(self, line_num: int) -> WrapInfo:
        """Get wrap info for a line with LRU caching."""
        if line_num < 0 or line_num >= self._buffer.total_lines:
            return WrapInfo(line_num=line_num)
        
        if line_num in self._cache:
            # Move to front of LRU
            if line_num in self._cache_order:
                self._cache_order.remove(line_num)
            self._cache_order.append(line_num)
            return self._cache[line_num]
        
        # Compute and cache
        info = self._compute_wrap_info(line_num)
        self._cache[line_num] = info
        self._cache_order.append(line_num)
        
        # Evict old entries
        while len(self._cache) > self._max_cache_size:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)
        
        return info
    
    def get_visual_line_count(self, line_num: int) -> int:
        """Get the number of visual lines for a logical line."""
        if not self._enabled:
            return 1
        return self.get_wrap_info(line_num).visual_line_count
    
    def get_total_visual_lines(self) -> int:
        """
        Estimate total visual lines.
        
        For smooth scrolling, we estimate based on average line length.
        This is fast and good enough for scrollbar positioning.
        """
        if not self._enabled:
            return self._buffer.total_lines
        
        total = self._buffer.total_lines
        if total == 0:
            return 1
        
        if total < 1000:
            # Exact calculation for small/medium files
            count = 0
            for i in range(total):
                count += self.get_visual_line_count(i)
            return count
        else:
            # Structural Sampling for large files
            # Sample N lines distributed across the file
            # Calculate EXACT visual lines for each sample
            # This captures the true distribution of line lengths (sparse vs dense)
            
            samples = 100
            step = max(1, total // samples)
            
            sampled_vis_lines = 0
            sampled_count = 0
            
            for i in range(0, total, step):
                # This computes the exact wrapping for the sampled line using current viewport
                # It is far more accurate than average line length because it accounts for
                # specific wrapping thresholds.
                lines = self.get_visual_line_count(i)
                sampled_vis_lines += lines
                sampled_count += 1
            
            if sampled_count > 0:
                avg_vis_per_logical = sampled_vis_lines / sampled_count
                
                # Apply safety margin (5%) and return
                return int(total * avg_vis_per_logical * 1.05)
            
            return int(total * 1.05)
    
    def get_line_segments(self, line_num: int) -> List[Tuple[int, int]]:
        """Get the column ranges for each visual segment of a line."""
        info = self.get_wrap_info(line_num)
        line_len = len(self._buffer.get_line(line_num))
        
        if not info.break_points:
            return [(0, line_len)]
        
        segments = []
        prev = 0
        for bp in info.break_points:
            segments.append((prev, bp))
            prev = bp
        segments.append((prev, line_len))
        
        return segments
    
    def column_to_visual_offset(self, line_num: int, col: int) -> Tuple[int, int]:
        """Convert a column position to visual offset within the line."""
        if not self._enabled:
            return (0, col)
        
        info = self.get_wrap_info(line_num)
        
        if not info.break_points:
            return (0, col)
        
        for i, bp in enumerate(info.break_points):
            if col < bp:
                start = info.break_points[i - 1] if i > 0 else 0
                return (i, col - start)
        
        start = info.break_points[-1]
        return (len(info.break_points), col - start)
