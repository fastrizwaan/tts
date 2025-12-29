"""
Word Wrap System - Pango-based word wrap for proper bidi/RTL support.

KEY FEATURES:
1. Uses Pango for accurate text measurement (proper bidi/RTL handling)
2. Lazy evaluation - only compute wrap for visible lines
3. LRU cache for recently accessed lines
4. Fallback to character-based estimation when no Cairo context available

This makes word wrap O(viewport_size) per frame, same as no-wrap mode.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, TYPE_CHECKING

# Pango imports for proper text measurement
try:
    import gi
    gi.require_version('Pango', '1.0')
    gi.require_version('PangoCairo', '1.0')
    from gi.repository import Pango, PangoCairo
    HAS_PANGO = True
except (ImportError, ValueError):
    HAS_PANGO = False
    Pango = None
    PangoCairo = None

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
    Pango-based word wrap mapper with proper bidi/RTL support.
    
    Uses Pango for accurate text measurement which handles:
    - Variable glyph widths (important for RTL scripts)
    - Bidirectional text shaping
    - Proper word break opportunities
    
    Falls back to character-based estimation when no Cairo context is available.
    """
    
    def __init__(self, buffer: 'VirtualBuffer'):
        self._buffer = buffer
        self._viewport_width_px: float = 800.0  # Width in pixels
        self._viewport_width_chars: int = 80    # Fallback character width
        self._char_width: float = 10.0          # Average char width for fallback
        self._enabled: bool = False
        
        # Font for Pango measurement (set by renderer)
        self._font_desc: Optional['Pango.FontDescription'] = None
        
        # Tab stops (set by renderer)  
        self._tab_array: Optional['Pango.TabArray'] = None
        
        # LRU cache for wrap info (limited size)
        self._cache: Dict[int, WrapInfo] = {}
        self._cache_order: List[int] = []  # For LRU eviction
        self._max_cache_size: int = 500    # Cache ~500 lines
        
        # Cached total visual lines
        self._cached_total: Optional[int] = None
    
    @property
    def _viewport_width(self) -> int:
        """Backward-compatible property for character-based width."""
        return self._viewport_width_chars

    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool) -> None:
        if self._enabled != value:
            self._enabled = value
            self.invalidate_all()
    
    def set_font(self, font_desc: 'Pango.FontDescription') -> None:
        """Set font description for accurate text measurement."""
        if self._font_desc == font_desc:
            return
            
        if self._font_desc and font_desc and self._font_desc.equal(font_desc):
            return

        self._font_desc = font_desc
        self.invalidate_all()
    
    def _tab_arrays_equal(self, t1, t2) -> bool:
        """Compare two Pango.TabArrays for equality."""
        if t1 is t2: return True
        if t1 is None or t2 is None: return False
        if t1.get_size() != t2.get_size(): return False
        if t1.get_positions_in_pixels() != t2.get_positions_in_pixels(): return False
        
        for i in range(t1.get_size()):
             a1, l1 = t1.get_tab(i)
             a2, l2 = t2.get_tab(i)
             if a1 != a2 or l1 != l2: return False
        return True

    def set_tab_array(self, tab_array: 'Pango.TabArray') -> None:
        """Set tab stops for layout."""
        if self._tab_arrays_equal(self._tab_array, tab_array):
            return
            
        self._tab_array = tab_array
        self.invalidate_all()
    
    def set_viewport_width(self, width_pixels: float, char_width: float = 10.0) -> None:
        """
        Update viewport width in pixels.
        
        Optimized: Avoids invalidation if change is negligible (< 1px and same char width).
        """
        # Epsilon check to prevent thrashing on sub-pixel layout changes
        if (abs(width_pixels - self._viewport_width_px) > 1.0 or 
            abs(char_width - self._char_width) > 0.001):
            
            self._viewport_width_px = max(100, width_pixels)
            self._char_width = char_width
            self._viewport_width_chars = max(20, int(width_pixels / char_width))
            self.invalidate_all()
    
    def set_char_width(self, chars: int) -> None:
        """Set viewport width directly in characters (fallback mode)."""
        if chars != self._viewport_width_chars:
            self._viewport_width_chars = max(20, chars)
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
        
        self._cached_total = None
        
        for line in range(start_line, min(end_line + 1, start_line + 50)):
            if line in self._cache:
                del self._cache[line]
                if line in self._cache_order:
                    self._cache_order.remove(line)
    
    def _compute_wrap_info_pango(self, line_num: int, cr) -> WrapInfo:
        """Compute wrap info using Pango for accurate bidi/RTL measurement."""
        line_text = self._buffer.get_line(line_num)
        if not line_text:
            return WrapInfo(line_num=line_num)
        
        # Create layout with wrap enabled
        layout = PangoCairo.create_layout(cr)
        if self._font_desc:
            layout.set_font_description(self._font_desc)
        
        # Enable automatic direction detection for bidi text
        layout.set_auto_dir(True)
        
        if self._tab_array:
            layout.set_tabs(self._tab_array)
        
        layout.set_text(line_text, -1)
        
        # Detect RTL text to adjust wrap width
        import unicodedata
        def is_rtl_text(text):
            for ch in text:
                bidi_type = unicodedata.bidirectional(ch)
                if bidi_type in ("L", "LRE", "LRO"):
                    return False
                if bidi_type in ("R", "AL", "RLE", "RLO"):
                    return True
            return False
        
        # For RTL text, increase the wrap width to use more available space
        # This prevents premature wrapping that leaves unused space
        # Need larger bonus to compensate for padding subtracted from viewport
        wrap_width = self._viewport_width_px
        if is_rtl_text(line_text):
            wrap_width += 50  # Substantial increase to fill available space for RTL text
        
        # Set wrap width in Pango units (pixels * SCALE)
        layout.set_width(int(wrap_width * Pango.SCALE))
        
        # Use WORD_CHAR wrap: prefer word boundaries, fall back to character
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        
        # Get line count from wrapped layout
        line_count = layout.get_line_count()
        
        if line_count <= 1:
            return WrapInfo(line_num=line_num)
        
        # Extract wrap points from Pango layout
        break_points = []
        text_bytes = line_text.encode('utf-8')
        
        for i in range(line_count - 1):  # Don't need break point after last line
            pango_line = layout.get_line_readonly(i)
            end_byte = pango_line.start_index + pango_line.length
            
            # Convert byte offset to character offset
            end_col = len(text_bytes[:end_byte].decode('utf-8', errors='replace'))
            break_points.append(end_col)
        
        return WrapInfo(
            line_num=line_num,
            break_points=break_points,
            visual_line_count=line_count
        )
    
    def _compute_wrap_info_fallback(self, line_num: int) -> WrapInfo:
        """Compute wrap info using simple character counting (fallback)."""
        line_text = self._buffer.get_line(line_num)
        if not line_text:
            return WrapInfo(line_num=line_num)
        
        line_len = len(line_text)
        if line_len <= self._viewport_width_chars:
            return WrapInfo(line_num=line_num)
        
        # Find break points - simple character-based wrap
        break_points = []
        pos = 0
        width = self._viewport_width_chars
        
        while pos < line_len:
            remaining = line_len - pos
            if remaining <= width:
                break
            
            target = pos + width
            break_pos = target
            
            # Quick look-back for space (limited)
            for i in range(min(target, line_len - 1), max(pos, target - 15), -1):
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
    
    def _compute_wrap_info(self, line_num: int, cr=None) -> WrapInfo:
        """Compute wrap info for a single line."""
        if not self._enabled:
            return WrapInfo(line_num=line_num)
        
        # Use Pango for accurate measurement if available
        if HAS_PANGO and cr is not None and self._font_desc is not None:
            return self._compute_wrap_info_pango(line_num, cr)
        
        # Fallback to character-based
        return self._compute_wrap_info_fallback(line_num)
    
    def get_wrap_info(self, line_num: int, cr=None) -> WrapInfo:
        """Get wrap info for a line with LRU caching."""
        if line_num < 0 or line_num >= self._buffer.total_lines:
            return WrapInfo(line_num=line_num)
        
        # If Pango context provided, always compute fresh for accuracy
        # (cache might have been computed without Pango)
        if HAS_PANGO and cr is not None and self._font_desc is not None:
            # Check if we have a Pango-computed cache entry
            # For simplicity, recompute when cr is provided
            info = self._compute_wrap_info(line_num, cr)
            self._cache[line_num] = info
            if line_num not in self._cache_order:
                self._cache_order.append(line_num)
            
            # Evict old entries
            while len(self._cache) > self._max_cache_size:
                old = self._cache_order.pop(0)
                self._cache.pop(old, None)
            
            return info
        
        # Use cached value if available
        if line_num in self._cache:
            # Move to front of LRU
            if line_num in self._cache_order:
                self._cache_order.remove(line_num)
            self._cache_order.append(line_num)
            return self._cache[line_num]
        
        # Compute and cache (fallback mode)
        info = self._compute_wrap_info(line_num)
        self._cache[line_num] = info
        self._cache_order.append(line_num)
        
        # Evict old entries
        while len(self._cache) > self._max_cache_size:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)
        
        return info
    
    def get_visual_line_count(self, line_num: int, cr=None) -> int:
        """Get the number of visual lines for a logical line."""
        if not self._enabled:
            return 1
        return self.get_wrap_info(line_num, cr).visual_line_count
    
    def get_total_visual_lines(self, cr=None) -> int:
        """
        Estimate total visual lines.
        Cached for performance during scrolling.
        """
        if not self._enabled:
            return self._buffer.total_lines
            
        if self._cached_total is not None:
            return self._cached_total
        
        total = self._buffer.total_lines
        if total == 0:
            self._cached_total = 1
            return 1
        
        if total < 1000:
            # Exact calculation for small/medium files
            count = 0
            for i in range(total):
                count += self.get_visual_line_count(i, cr)
            result = int(count * 1.05)  # Small buffer
        else:
            # Structural Sampling for large files
            samples = 100
            step = max(1, total // samples)
            
            sampled_vis_lines = 0
            sampled_count = 0
            
            for i in range(0, total, step):
                lines = self.get_visual_line_count(i, cr)
                sampled_vis_lines += lines
                sampled_count += 1
            
            if sampled_count > 0:
                avg_vis_per_logical = sampled_vis_lines / sampled_count
                result = int(total * avg_vis_per_logical * 1.05)
            else:
                result = int(total * 1.05)
                
        self._cached_total = result
        return result
    
    def get_line_segments(self, line_num: int, cr=None) -> List[Tuple[int, int]]:
        """Get the column ranges for each visual segment of a line."""
        info = self.get_wrap_info(line_num, cr)
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
    
    def column_to_visual_offset(self, line_num: int, col: int, cr=None) -> Tuple[int, int]:
        """Convert a column position to visual offset within the line."""
        if not self._enabled:
            return (0, col)
        
        info = self.get_wrap_info(line_num, cr)
        
        if not info.break_points:
            return (0, col)
        
        for i, bp in enumerate(info.break_points):
            if col < bp:
                start = info.break_points[i - 1] if i > 0 else 0
                return (i, col - start)
        
        start = info.break_points[-1]
        return (len(info.break_points), col - start)
