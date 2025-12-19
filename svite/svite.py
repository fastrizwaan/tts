#!/usr/bin/env python3
import sys, os, mmap, gi, cairo, time, unicodedata, signal
from threading import Thread
from array import array
import math 
import datetime
import bisect
import re
import json
from enum import Enum, auto
from virtual_buffer import VirtualBuffer, normalize_replacement_string
from word_wrap import VisualLineMapper
from syntax_v2 import StateAwareSyntaxEngine
from undo_redo import UndoRedoManager
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo, GLib, Gio

# Global variable to track dragged tab for drag and drop
DRAGGED_TAB = None

CSS_OVERLAY_SCROLLBAR = """
/* ===== Vertical overlay scrollbar ===== */
/* Vertical Scrollbar container */
.overlay-scrollbar {{
    background-color: transparent;

}}


/* Trough (track) */
.overlay-scrollbar trough {{
    min-width: 8px;
    border-radius: 0px;
    background-color: transparent;

}}

/* Trough hover highlight */
.overlay-scrollbar trough:hover {{
    background-color: alpha(@window_fg_color, 0.2);
    transition: background-color 200ms ease;

}}

/* Base slider (thumb) */
.overlay-scrollbar trough > slider {{
    min-width: 2px;
    border-radius: 12px;
    background-color: alpha(@window_fg_color, 0.2);
    transition: min-width 200ms ease, background-color 200ms ease;
}}


/* Slider expands when trough is hovered */
.overlay-scrollbar trough:hover > slider {{
    min-width: 8px;
    background-color: alpha(@window_bg_color, 0.05);
}}
/* Container hover highlights trough */
.overlay-scrollbar:hover trough {{
    background-color: alpha(@window_fg_color, 0.1);
}}

/* Container hover expands slider */
.overlay-scrollbar:hover trough > slider {{
    min-width: 8px;
    background-color: rgba(53,132,228,1);
}}


/* Slider expands when hovered directly */
.overlay-scrollbar trough > slider:hover {{
    min-width: 8px;
    background-color: rgba(73,152,248, 1);
}}
/* Slider active/dragging */
.overlay-scrollbar trough > slider:active {{
    min-width: 8px;
    background-color: rgba(53,132,228, 1);
}}



/* ===== Horizontal overlay scrollbar ===== */

/* Horizontal Scrollbar container */
.hscrollbar-overlay {{
    background-color: transparent;
    margin-bottom: 0px;
}}

/* Trough (track) */
.hscrollbar-overlay trough {{
    min-height: 8px;
    border-radius: 0px;
    background-color: transparent;
    margin-bottom: 0px;    
}}

/* Trough hover highlight */
.hscrollbar-overlay trough:hover {{
    background-color: alpha(@window_fg_color, 0.2);
    transition: background-color 200ms ease;
    margin-bottom: 0px;
}}

/* Base slider (thumb) */
.hscrollbar-overlay trough > slider {{
    min-height: 2px;
    border-radius: 12px;
    background-color: alpha(@window_fg_color, 0.2);
    transition: min-height 200ms ease, background-color 200ms ease;
}}


/* Slider expands when trough is hovered */
.hscrollbar-overlay trough:hover > slider {{
    min-height: 8px;
    background-color: alpha(@window_fg_color, 0.55);
}}

/* Container hover highlights trough */
.hscrollbar-overlay:hover trough {{
    background-color: alpha(@window_fg_color, 0.2);
}}

/* Container hover expands slider */
.hscrollbar-overlay:hover trough > slider {{
    min-height: 8px;
    background-color: rgba(53,132,228,1);
}}

/* Slider expands when hovered directly */
.hscrollbar-overlay trough > slider:hover {{
    min-height: 8px;
    background-color: rgba(73,152,248, 1);
}}

/* Slider active/dragging */
.hscrollbar-overlay trough > slider:active {{
    min-height: 8px;
    background-color: rgba(53,132,228, 1);
}}


.toolbarview {{
    background: @headerbar_bg_color; 
}}

/* ========================
   Editor background
   ======================== */
.editor-surface {{
    background-color: {bg_color};
}}

/* ========================
   Chrome Tabs
   ======================== */

.chrome-tab {{
    background: transparent;
    color: alpha(@window_fg_color, 0.85);
    min-height: 32px;
    padding-left: 10px;
    padding-right: 6px;
    border-radius: 9px 9px 9px 9px;
    margin-bottom: 1px;

}}
.header-modified-dot{{
    min-width: 8px;
    min-height: 8px;

    background-color: alpha(@window_fg_color, 0.7);
    border-radius: 4px;

    margin-top: 5px;   /* vertically center inside tab */
    margin-bottom: 5px;
}}

.modified-dot {{
    min-width: 8px;
    min-height: 8px;

    background-color: alpha(@window_fg_color, 0.7);
    border-radius: 4px;

    margin-top: 12px;   /* vertically center inside tab */
    margin-bottom: 12px;
}}

.chrome-tab label {{
    font-weight: normal;
}}

.chrome-tab:hover {{
    color: @window_fg_color;
    background: alpha(@window_fg_color, 0.10);

}}

/* ACTIVE TAB (pilled) */
.chrome-tab.active {{
    background: alpha(@window_fg_color, 0.12);
    color: @window_fg_color;
}}

.chrome-tab.active label {{
    font-weight: normal;
    opacity: 1;
}}

/* Dragging state */
.chrome-tab.dragging {{
    opacity: 0.5;
}}

/* Drop indicator line */
.tab-drop-indicator {{
    background: linear-gradient(to bottom, 
        transparent 0%, 
        rgba(0, 127, 255, 0.8) 20%, 
        rgba(0, 127, 255, 1) 50%, 
        rgba(0, 127, 255, 0.8) 80%, 
        transparent 100%);
    min-width: 3px;
    border-radius: 2px;
}}


/* Modified marker */
.chrome-tab.modified {{
    font-style: normal;
}}

/* Reset all buttons inside tab (fixes size regression) */
.chrome-tab button {{
    background: none;
    border: none;
    box-shadow: none;
    padding: 0;
    margin: 0;
    min-width: 0;
    min-height: 0;
}}

/* close button specific */
.chrome-tab .chrome-tab-close-button {{
    padding: 2px;
    opacity: 0.5;
    color: @window_fg_color;
}}

.chrome-tab:hover .chrome-tab-close-button {{
    opacity: 1;
}}

.chrome-tab.active .chrome-tab-close-button {{
    opacity: 1;
    color: @window_fg_color;
}}

/* ========================
   Separators
   ======================== */
.chrome-tab-separator {{
    min-width: 1px;
    background-color: alpha(@window_fg_color, 0.15);
    margin-top: 6px;
    margin-bottom: 6px;
}}

.chrome-tab-separator.hidden {{
    min-width: 0px;
    background-color: transparent;
}}
.chrome-tab-separator:first-child {{
    background-color: transparent;
    min-width: 0;
}}

.chrome-tab-separator:last-child {{
    background-color: transparent;
    min-width: 0;
}}
/* ========================
   Tab close button
   ======================== */
.chrome-tab-close-button {{
    opacity: 0;
    transition: opacity 300ms ease, background-color 300ms ease;
    margin-right:0px;
    padding:0px;
}}

.chrome-tab:hover .chrome-tab-close-button {{
    opacity: 1;
    border-radius: 20px;
}}

.chrome-tab-close-button:hover  {{
    background-color: alpha(@window_fg_color, 0.2);
}}

.chrome-tab.active .chrome-tab-close-button:hover {{
    opacity: 1;
    background-color: alpha(@window_fg_color, 0.2);
}}


/* Corrected dropdown selectors - removed space after colon */
.linked dropdown:first-child > button  {{
    border-top-left-radius: 0px; 
    border-bottom-left-radius: 0px; 
    border-top-right-radius: 0px; 
    border-bottom-right-radius: 0px;
}}

/* Explicit rule to ensure middle dropdowns have NO radius */
.linked dropdown:not(:first-child):not(:last-child) > button {{
    border-radius: 0;
}}




/* Corrected menubutton selectors - removed space after colon */
.linked menubutton:first-child > button  {{
    border-top-left-radius: 10px; 
    border-bottom-left-radius: 10px; 
    border-top-right-radius: 0px; 
    border-bottom-right-radius: 0px;
}}

.linked menubutton:last-child > button {{
    border-top-left-radius: 0px; 
    border-bottom-left-radius: 0px; 
    border-top-right-radius: 10px; 
    border-bottom-right-radius: 10px;
}} 

/* Additional recommended fixes for consistent styling */
.linked menubutton button {{
    background: alpha(@window_fg_color, 0.05); padding:0px; padding-right: 3px; margin-left: 0px;
}}

.linked menubutton button:hover {{
    background: alpha(@window_fg_color, 0.15);
     padding:0px; padding-right: 3px;
}}

.linked menubutton button:active, 
.linked menubutton button:checked {{
    background-color: rgba(127, 127, 127, 0.3);
    padding:0px; padding-right: 3px;
}}

.linked menubutton button:checked:hover {{
       background: alpha(@window_fg_color, 0.2);
}}


/* Corrected button selectors - removed space after colon */
.linked button  {{
    border-top-left-radius: 10px; 
    border-bottom-left-radius: 10px; 
    border-top-right-radius: 0px; 
    border-bottom-right-radius: 0px;
    
}}

/* Additional recommended fixes for consistent styling */
.linked button {{
    background: alpha(@window_fg_color, 0.05); padding-left: 10px; padding-right:6px; 
}}

.linked button:hover {{
    background: alpha(@window_fg_color, 0.15);

}}

/* Find Bar Styling */
.find-bar {{
    background-color: @headerbar_bg_color;
    border-bottom: 1px solid alpha(@window_fg_color, 0.15);
    padding: 0px;
}}

/* Progress Bar Widget Styling */
.progress-bar-widget {{
    background-color: @headerbar_bg_color;
    border-bottom: 1px solid alpha(@window_fg_color, 0.15);
    min-height: 28px;
}}

/* Status Bar Styling */
.status-bar {{
    background-color: @headerbar_bg_color;
    min-height: 28px;
    font-size: 0.9em;
}}

.status-bar button {{
    min-height: 20px;
    padding: 2px 8px;
}}

.status-bar checkbutton {{
    min-height: 20px;
}}

.status-bar label {{
    font-size: 0.9em;
}}
"""




# ============================================================
#   HELPER FUNCTIONS
# ============================================================

def detect_rtl_line(text):
    """Detect if a line is RTL using Unicode bidirectional properties.
    
    Returns True  if the first strong directional character is RTL,
    False if LTR, or False if no strong directional characters found.
    """
    for ch in text:
        t = unicodedata.bidirectional(ch)
        if t in ("L", "LRE", "LRO"):
            return False
        if t in ("R", "AL", "RLE", "RLO"):
            return True
    return False
def detect_language(path):
    """Detect language from file extension."""
    if not path: return None
    ext = os.path.splitext(path)[1].lower()
    mapping = {
        '.py': 'python',
        '.js': 'javascript',
        '.c': 'c',
        '.h': 'c',
        '.rs': 'rust',
        '.html': 'html',
        '.htm': 'html',
        '.css': 'css',
        '.dsl': 'dsl'
    }
    return mapping.get(ext, None)

# ============================================================
#   FULL INDEXING BUT MEMORY-SAFE
# ============================================================

class IndexedFile:
    """
    Fully indexes file once.
    Memory-safe: only stores offsets, not decoded lines.
    Works for UTF-8 and UTF-16 (LE/BE).
    """

    def __init__(self, path):
        print(f"Opening file: {path}")
        start = time.time()
        
        self.path = path
        self.encoding = self.detect_encoding(path)
        self.raw = open(path, "rb")
        
        # Check if file is empty
        file_size = os.path.getsize(path)
        if file_size == 0:
            # Empty file - don't create mmap
            self.mm = None
            self.is_empty = True
            print(f"File opened (empty file) in {time.time()-start:.2f}s")
        else:
            self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)
            self.is_empty = False
            print(f"File opened and mapped in {time.time()-start:.2f}s")

        # Use array.array instead of list - much faster for millions of integers
        # 'Q' = unsigned long long (8 bytes, perfect for file offsets)
        self.index = array('Q')

    def detect_encoding(self, path):
        with open(path, "rb") as f:
            data = f.read(4096)  # small peek is enough

        # Handle empty files
        if len(data) == 0:
            return "utf-8"

        # --- BOM detection ---
        if data.startswith(b"\xff\xfe"):
            return "utf-16le"
        if data.startswith(b"\xfe\xff"):
            return "utf-16be"
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


    def index_file(self, progress_callback=None):
        start_time = time.time()
        enc = self.encoding
        
        if self.is_empty:
            print(f"Indexing empty file ({enc})...")
            # Empty file has 0 lines (or 1 empty line depending on interpretation, 
            # but for indexing purposes we can just leave index as [0])
            self.index = array('Q', [0])
            return

        print(f"Indexing {len(self.mm) / (1024**3):.2f}GB file ({enc})...")


        if enc.startswith("utf-16"):
            self._index_utf16(progress_callback)
        else:
            self._index_utf8(progress_callback)
        
        elapsed = time.time() - start_time
        index_size_mb = len(self.index) * 8 / (1024**2)  # 8 bytes per entry
        
        print(f"Indexed {len(self.index)-1:,} lines in {elapsed:.2f}s ({len(self.mm)/(1024**3)/elapsed:.2f} GB/s)")
        print(f"Average line length: {len(self.mm)/(len(self.index)-1):.0f} bytes")
        print(f"Index memory: {index_size_mb:.1f} MB ({index_size_mb*100/len(self.mm)*1024:.2f}% of file size)")




    def _index_utf8(self, progress_callback=None):
        """Fast UTF-8 indexing using mmap.find() - optimized for huge files"""
        if self.is_empty:
            self.index = array('Q', [0])
            return

        mm = self.mm
        total_size = len(mm)
        
        # Use array.array for fast integer storage (10-20x faster than list for millions of items)
        self.index = array('Q', [0])
        
        # Use mmap.find() to scan for newlines
        pos = 0
        last_report = 0
        report_interval = 50_000_000  # Report every 50MB for less overhead
        
        while pos < total_size:
            # Report progress less frequently (every 50MB instead of 10MB)
            if progress_callback and pos - last_report > report_interval:
                last_report = pos
                progress = pos / total_size
                GLib.idle_add(progress_callback, progress)
            
            # Find next newline directly in mmap (fast C-level search)
            newline_pos = mm.find(b'\n', pos)
            
            if newline_pos == -1:
                # No more newlines
                break
            
            # Record position after the newline
            pos = newline_pos + 1
            self.index.append(pos)
        
        # Ensure file end is recorded
        if not self.index or self.index[-1] != total_size:
            self.index.append(total_size)
        
        if progress_callback:
            GLib.idle_add(progress_callback, 1.0)

    def _index_utf16(self, progress_callback=None):
        """Fast UTF-16 indexing using mmap.find() directly - no memory copies"""
        if self.is_empty:
            self.index = array('Q', [0])
            return
    
        mm = self.mm
        total_size = len(mm)
        
        # Determine newline pattern based on endianness
        # Note: "utf-16" without suffix defaults to LE in Python
        # Also handle "utf-16-le" and "utf-16-be" variants
        encoding_lower = self.encoding.lower().replace('-', '')
        if encoding_lower in ("utf16le", "utf16"):
            newline_bytes = b'\n\x00'  # UTF-16LE: \n = 0x0A 0x00
            bom = b'\xff\xfe'  # LE BOM
        else:  # utf-16be
            newline_bytes = b'\x00\n'  # UTF-16BE: \n = 0x00 0x0A
            bom = b'\xfe\xff'  # BE BOM
        
        # Check for BOM and set start position
        start_pos = 0
        if total_size >= 2:
            first_two = mm[0:2]
            if first_two in (b'\xff\xfe', b'\xfe\xff'):
                start_pos = 2
                # Verify BOM matches expected endianness
                if first_two != bom:
                    # BOM doesn't match detected encoding - adjust
                    if first_two == b'\xff\xfe':
                        newline_bytes = b'\n\x00'  # LE
                    else:
                        newline_bytes = b'\x00\n'  # BE
        
        # Use array.array for fast integer storage
        self.index = array('Q', [start_pos])
        
        pos = start_pos
        last_report = 0
        report_interval = 50 * 1024 * 1024  # 50MB
        
        while pos < total_size:
            # Report progress less frequently (every 50MB instead of 10MB)
            if progress_callback and pos - last_report > report_interval:
                last_report = pos
                progress = pos / total_size
                GLib.idle_add(progress_callback, progress)
            
            # Find next newline directly in mmap (fast C-level search)
            newline_pos = mm.find(newline_bytes, pos)
            
            if newline_pos == -1:
                # No more newlines
                break
            
            # Record position after the newline (skip the 2-byte newline)
            pos = newline_pos + 2
            self.index.append(pos)
        
        # Ensure file end is recorded
        if not self.index or self.index[-1] != total_size:
            self.index.append(total_size)
        
        if progress_callback:
            GLib.idle_add(progress_callback, 1.0) 
    def total_lines(self):
        return len(self.index) - 1

    def __getitem__(self, line):
        if self.is_empty:
            return ""

        if line < 0 or line >= self.total_lines():
            return ""

        start = self.index[line]
        end = self.index[line + 1]

        raw = self.mm[start:end]
        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")

    def get_byte_range(self, start_line, end_line):
        """Get raw bytes for a range of lines [start_line, end_line)"""
        if self.is_empty:
            return b""
            
        total = self.total_lines()
        if start_line >= total:
            return b""
            
        end_line = min(end_line, total)
        if start_line >= end_line:
            return b""
            
        start_idx = self.index[start_line]
        end_idx = self.index[end_line]
        
        return self.mm[start_idx:end_idx]


# ============================================================
#   SELECTION
# ============================================================

class Selection:
    """Manages text selection state"""
    
    def __init__(self):
        self.start_line = -1
        self.start_col = -1
        self.end_line = -1
        self.end_col = -1
        self.active = False
        self.selecting_with_keyboard = False
    
    def clear(self):
        """Clear the selection"""
        self.start_line = -1
        self.start_col = -1
        self.end_line = -1
        self.end_col = -1
        self.active = False
        self.selecting_with_keyboard = False
    
    def set_wrap_enabled(self, enabled):
        """Enable or disable word wrap."""
        if self.wrap_enabled == enabled:
            return
        
        self.wrap_enabled = enabled
        self.wrap_cache = {}
        self.visual_line_map = []
        # self.total_visual_lines_cache = None
        self.visual_line_anchor = (0, 0)

    def set_start(self, line, col):
        """Set selection start point"""
        self.start_line = line
        self.start_col = col
        self.end_line = line
        self.end_col = col
        self.active = True
    
    def set_end(self, line, col):
        """Set selection end point"""
        self.end_line = line
        self.end_col = col
        self.active = (self.start_line != self.end_line or self.start_col != self.end_col)
    
    def has_selection(self):
        """Check if there's an active selection"""
        return self.active and (
            self.start_line != self.end_line or 
            self.start_col != self.end_col
        )
    
    def get_bounds(self):
        """Get normalized selection bounds (start always before end)"""
        if not self.has_selection():
            return None, None, None, None
            
        # Normalize so start is always before end
        if self.start_line < self.end_line:
            return self.start_line, self.start_col, self.end_line, self.end_col
        elif self.start_line > self.end_line:
            return self.end_line, self.end_col, self.start_line, self.start_col
        else:
            # Same line
            if self.start_col <= self.end_col:
                return self.start_line, self.start_col, self.end_line, self.end_col
            else:
                return self.end_line, self.end_col, self.start_line, self.start_col
    
    def contains_position(self, line, col):
        """Check if a position is within the selection"""
        if not self.has_selection():
            return False
            
        start_line, start_col, end_line, end_col = self.get_bounds()
        
        if line < start_line or line > end_line:
            return False
        
        if line == start_line and line == end_line:
            return start_col <= col <= end_col
        elif line == start_line:
            return col >= start_col
        elif line == end_line:
            return col <= end_col
        else:
            return True
# LEGACY Undo/Redo and VirtualBuffer REMOVED - Imported from undo_redo.py and virtual_buffer.py

# ============================================================
#   INPUT
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf
        self.dragging = False
        self.drag_start_line = -1
        self.drag_start_col = -1

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.buf.selection.clear()
        self.drag_start_line = ln
        self.drag_start_col = col
        self.dragging = False

    def start_drag(self, ln, col):
        self.dragging = True
        self.drag_start_line = ln
        self.drag_start_col = col
        
        # Set cursor first (this clears old selection and sets cursor position)
        self.buf.set_cursor(ln, col, extend_selection=False)
        
        # Now establish the new selection anchor at the current cursor position
        self.buf.selection.set_start(ln, col)
        self.buf.selection.set_end(ln, col)

    def drag_to(self, x, y):
        """Handle drag to x,y coordinates"""
        ln, col = self.view.xy_to_line_col(x, y)
        self.update_drag(ln, col)

    def update_drag(self, ln, col):

        if self.dragging:
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col, extend_selection=True)

    def end_drag(self):
        """End drag selection"""
        self.dragging = False

    def move_left(self, extend_selection=False):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        
        if not extend_selection and b.selection.has_selection():
            # Move to start of selection
            start_ln, start_col, _, _ = b.selection.get_bounds()
            b.set_cursor(start_ln, start_col, extend_selection)
        elif col > 0:
            # Move left within line
            b.set_cursor(ln, col - 1, extend_selection)
        elif ln > 0:
            # At start of line - move to end of previous line (selecting the newline)
            prev = b.get_line(ln - 1)
            b.set_cursor(ln - 1, len(prev), extend_selection)

    def move_right(self, extend_selection=False):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        
        if not extend_selection and b.selection.has_selection():
            # Move to end of selection
            _, _, end_ln, end_col = b.selection.get_bounds()
            b.set_cursor(end_ln, end_col, extend_selection)
        elif col < len(line):
            # Move right within line
            b.set_cursor(ln, col + 1, extend_selection)
        elif ln + 1 < b.total():
            # At end of line - move to start of next line (selecting the newline)
            b.set_cursor(ln + 1, 0, extend_selection)

    def move_up(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        
        # If selection active and not extending, collapse to start
        if not extend_selection and b.selection.has_selection():
            start_ln, start_col, _, _ = b.selection.get_bounds()
            b.set_cursor(start_ln, start_col, extend_selection)
            return
        
        if self.view.mapper.enabled:
            # Visual line movement
            segments = self.view.mapper.get_line_segments(ln)
            
            # Find current segment index
            vis_idx = 0
            curr_col = b.cursor_col
            for i, (s, e) in enumerate(segments):
                if s <= curr_col <= e:
                    vis_idx = i
                    break
            
            target_ln = ln
            target_vis_idx = vis_idx - 1
            
            if target_vis_idx < 0:
                if ln > 0:
                    target_ln = ln - 1
                    t_segs = self.view.mapper.get_line_segments(target_ln)
                    target_vis_idx = len(t_segs) - 1
                else:
                    if extend_selection: b.set_cursor(0, 0, True)
                    return
            else:
                t_segs = segments

            # Map column by X coordinate preservation
            # 1. Calculate X of current cursor in current segment
            s_start, s_end = segments[vis_idx]
            text_seg = b.get_line(ln)[s_start:s_end]
            col_in_seg = curr_col - s_start
            
            # We need a layout to get X
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            layout = self.view.create_text_layout(cr, text_seg)
            
            # Convert col to Pango X
            # Simplified: layout index_to_pos
            # Need byte index
            byte_idx = 0
            for ch in text_seg[:col_in_seg]: byte_idx += len(ch.encode('utf-8'))
            
            pos = layout.index_to_pos(byte_idx)
            target_x = pos.x # Pango units
            
            # 2. Map X to column in target segment
            ts_start, ts_end = t_segs[target_vis_idx]
            target_text_seg = b.get_line(target_ln)[ts_start:ts_end]
            
            col_in_target = self.view.pixel_to_column(cr, target_text_seg, target_x / Pango.SCALE)
            
            new_col = ts_start + col_in_target
            b.set_cursor(target_ln, new_col, extend_selection)
            
        else:
            # Logical movement
            if ln > 0:
                target_ln = ln - 1
                target_len = len(b.get_line(target_ln))
                new_col = min(b.cursor_col, target_len)
                b.set_cursor(target_ln, new_col, extend_selection)
            elif extend_selection:
                b.set_cursor(0, 0, True)

    def move_down(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        
        if not extend_selection and b.selection.has_selection():
            _, _, end_ln, end_col = b.selection.get_bounds()
            b.set_cursor(end_ln, end_col, extend_selection)
            return

        if self.view.mapper.enabled:
            # Visual movement
            segments = self.view.mapper.get_line_segments(ln)
            curr_col = b.cursor_col
            
            vis_idx = 0
            for i, (s, e) in enumerate(segments):
                if s <= curr_col <= e:
                    vis_idx = i
                    break
            
            target_ln = ln
            target_vis_idx = vis_idx + 1
            
            if target_vis_idx >= len(segments):
                if ln < b.total() - 1:
                    target_ln = ln + 1
                    target_vis_idx = 0
                    t_segs = self.view.mapper.get_line_segments(target_ln)
                else:
                    if extend_selection: 
                        last_len = len(b.get_line(ln))
                        b.set_cursor(ln, last_len, True)
                    return
            else:
                t_segs = segments

            # Map column by X
            s_start, s_end = segments[vis_idx]
            text_seg = b.get_line(ln)[s_start:s_end]
            col_in_seg = curr_col - s_start
            
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            layout = self.view.create_text_layout(cr, text_seg)
            
            byte_idx = 0
            for ch in text_seg[:col_in_seg]: byte_idx += len(ch.encode('utf-8'))
            pos = layout.index_to_pos(byte_idx)
            target_x = pos.x
            
            ts_start, ts_end = t_segs[target_vis_idx]
            target_text_seg = b.get_line(target_ln)[ts_start:ts_end]
            
            col_in_target = self.view.pixel_to_column(cr, target_text_seg, target_x / Pango.SCALE)
            
            new_col = ts_start + col_in_target
            b.set_cursor(target_ln, new_col, extend_selection)

        else:
            # Logical movement
            if ln < b.total() - 1:
                target_ln = ln + 1
                target_len = len(b.get_line(target_ln))
                new_col = min(b.cursor_col, target_len)
                b.set_cursor(target_ln, new_col, extend_selection)
            elif extend_selection:
                last_len = len(b.get_line(ln))
                b.set_cursor(ln, last_len, True)

    def move_word_left(self, extend_selection=False):
        """Move cursor to the start of the previous word"""
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        
        # Helper to check if character is a word character
        import unicodedata
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            return cat[0] in ('L', 'N', 'M')
        
        # If at start of line, go to end of previous line
        if col == 0:
            if ln > 0:
                prev_line = b.get_line(ln - 1)
                b.set_cursor(ln - 1, len(prev_line), extend_selection)
            return
        
        # Skip whitespace to the left
        while col > 0 and line[col - 1].isspace():
            col -= 1
        
        if col == 0:
            b.set_cursor(ln, col, extend_selection)
            return
        
        # Now we're on a non-whitespace character
        # Check what type it is and skip that type
        if is_word_char(line[col - 1]):
            # Skip word characters to the left
            while col > 0 and is_word_char(line[col - 1]):
                col -= 1
        else:
            # Skip symbols/punctuation to the left (treat as a "word")
            while col > 0 and not line[col - 1].isspace() and not is_word_char(line[col - 1]):
                col -= 1
        
        b.set_cursor(ln, col, extend_selection)
    
    def move_word_right(self, extend_selection=False):
        """Move cursor to the start of the next word"""
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        
        # Helper to check if character is a word character
        import unicodedata
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            return cat[0] in ('L', 'N', 'M')
        
        # If at end of line, go to start of next line
        if col >= len(line):
            if ln + 1 < b.total():
                b.set_cursor(ln + 1, 0, extend_selection)
            return
        
        # Special handling when cursor is on space with no selection
        if line[col].isspace() and not b.selection.has_selection():
            # Select space(s) + next word
            start_col = col
            
            # Skip whitespace on current line
            while col < len(line) and line[col].isspace():
                col += 1
            
            # If we reached end of line
            if col >= len(line):
                # Check if there's a next line
                if ln + 1 < b.total():
                    # Select space(s) + newline + next word from next line
                    next_line = b.get_line(ln + 1)
                    next_col = 0
                    
                    # Skip leading whitespace on next line
                    while next_col < len(next_line) and next_line[next_col].isspace():
                        next_col += 1
                    
                    # Select the next word on next line
                    if next_col < len(next_line):
                        if is_word_char(next_line[next_col]):
                            while next_col < len(next_line) and is_word_char(next_line[next_col]):
                                next_col += 1
                        elif not next_line[next_col].isspace():
                            while next_col < len(next_line) and not next_line[next_col].isspace() and not is_word_char(next_line[next_col]):
                                next_col += 1
                    
                    # Set selection from start_col on current line to next_col on next line
                    b.selection.set_start(ln, start_col)
                    b.selection.set_end(ln + 1, next_col)
                    b.cursor_line = ln + 1
                    b.cursor_col = next_col
                    return
                else:
                    # No next line - select spaces to end of line
                    b.selection.set_start(ln, start_col)
                    b.selection.set_end(ln, col)
                    b.cursor_col = col
                    return
            
            # We found a non-space character - select the word
            if is_word_char(line[col]):
                while col < len(line) and is_word_char(line[col]):
                    col += 1
            elif not line[col].isspace():
                while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                    col += 1
            
            # Set selection from start_col to col
            b.selection.set_start(ln, start_col)
            b.selection.set_end(ln, col)
            b.cursor_col = col
            return
        
        # Check what type of character we're on and skip that type
        if is_word_char(line[col]):
            # Skip word characters to the right
            while col < len(line) and is_word_char(line[col]):
                col += 1
        elif not line[col].isspace():
            # Skip symbols/punctuation to the right (treat as a "word")
            while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                col += 1
        
        # If extending an existing selection, skip whitespace AND select next word
        # This makes second Ctrl+Shift+Right select space + next word
        if extend_selection and b.selection.has_selection():
            # Skip whitespace
            while col < len(line) and line[col].isspace():
                col += 1
            
            # Now select the next word
            if col < len(line):
                if is_word_char(line[col]):
                    while col < len(line) and is_word_char(line[col]):
                        col += 1
                elif not line[col].isspace():
                    while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                        col += 1
        
        b.set_cursor(ln, col, extend_selection)
    def move_home(self, extend_selection=False):
        """Move to beginning of line"""
        b = self.buf
        b.set_cursor(b.cursor_line, 0, extend_selection)

    def move_end(self, extend_selection=False):
        """Move to end of line"""
        b = self.buf
        line = b.get_line(b.cursor_line)
        b.set_cursor(b.cursor_line, len(line), extend_selection)

    def move_document_start(self, extend_selection=False):
        """Move to beginning of document"""
        self.buf.set_cursor(0, 0, extend_selection)

    def move_document_end(self, extend_selection=False):
        """Move to end of document"""
        b = self.buf
        total = b.total()
        last_line = total - 1
        last_line_text = b.get_line(last_line)
        b.set_cursor(last_line, len(last_line_text), extend_selection)

class VirtualTextView(Gtk.DrawingArea):

    def __init__(self, buf):
        super().__init__()
        self.buf = buf
        # Add reference from buffer to view for drag-and-drop
        try:
            buf._view = self
        except:
            pass # In case buf is a check_output mock
            
        self.use_tabs = True
        self.auto_indent = True
        
        # Core Components from edig
        self.mapper = VisualLineMapper(buf)
        self.syntax = buf.syntax_engine
        self.syntax_queue = set()
        self.syntax_idle_id = None
        self.undo_manager = UndoRedoManager()
        
        # Initialize Metrics (formerly in Renderer)
        self.font_desc = Pango.FontDescription.from_string("Monospace 11")
        self.matching_brackets = []
        self.renderer = self
        
        # Compatibility shims for legacy renderer cache clearing
        self.wrap_cache = {} # Dummy dict that can be .clear()-ed
        self.visual_line_map = []
        self.total_visual_lines_cache = None
        self.total_visual_lines_locked = False
        self.visual_line_anchor = (0, 0)
        self.max_line_width = 0
        self.needs_full_width_scan = False
        self.estimated_total_cache = None
        self.edits_since_cache_invalidation = 0
 # Shim for legacy external access
        self.line_h = 20 # Will be updated by update_metrics
        self.char_width = 10 # Will be updated by update_metrics
        self.tab_width = 4
        self.show_line_numbers = True
        
        self.ctrl = InputController(self, buf)
        self.scroll_line = 0
        self.scroll_visual_offset = 0
        self.scroll_x = 0
        
        # Wrapping
        self.mapper.enabled = True
        self.mapper.set_viewport_width(800) # Initial guess
        
        self.needs_scrollbar_init = False
        self.overwrite_mode = False
        
        # Throttling
        self.scroll_update_pending = False
        self.pending_scroll_value = None
        self.scrollbar_dragging = False
        self.last_drag_value = None
        self.calculating = False
        self.calculation_message = ""
        self.resize_update_pending = False
        self._pending_triple_click = False
        
        # Busy Overlay
        self._busy_overlay = None
        self._busy_spinner = None
        self._busy_label = None
        self._pending_click = False
        
        # Search highlights
        self.search_matches = []
        self.highlight_cache = {}
        self.current_match_idx = -1
        self.current_match = None
        self._skip_to_position = None  # (line, col) - skip matches before this after replace
        
        self.highlight_current_line = True
        self.highlight_brackets = True
        self.matching_brackets = [] # Store matches [((ln,col), (ln,col))]
        self.on_scroll_callback = None
        
        self.set_focusable(True)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_draw_func(self.draw_view)
        
        self.install_mouse()
        self.install_keys()
        self.install_im()
        
        # Initial Metrics Update
        self.update_metrics()
        self.update_colors_for_theme()
        
    def update_metrics(self):
        """Update font metrics and notify mapper."""
        # Create a temporary context to measure font
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        layout.set_text("M", -1)
        
        ink, logical = layout.get_extents()
        self.line_h = int(logical.height / Pango.SCALE)
        self.char_width = logical.width / Pango.SCALE
        self.mapper.set_char_width(self.char_width)
        
        # Update tab array if needed
        pass




    def create_hit_test_layout(self, text=""):
        """Create a Pango layout for hit testing.
        
        Uses PangoCairo with a dummy surface to mimic Renderer.draw behavior
        and ensure metrics match as closely as possible.
        """
        # Create a dummy surface/context if one isn't passed (we create internal)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        
        if True: # Uses standard tab array
            tab_width_px = self.tab_width * self.char_width
            tabs = Pango.TabArray.new(1, True)
            tabs.set_tab(0, Pango.TabAlign.LEFT, int(tab_width_px))
            layout.set_tabs(tabs)
        layout.set_auto_dir(True)
        layout.set_text(text, -1)
        return layout

    def create_text_layout(self, cr, text="", auto_dir=True):
        """Create a Pango layout with standard settings.
        
        Args:
            cr: Cairo context
            text: Optional text to set
            auto_dir: Whether to enable auto-direction (default True)
            
        Returns:
            Configured Pango layout
        """
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)

        # Apply tab width
        if True: # Always use tabs
            tab_width_px = self.tab_width * self.char_width
            tabs = Pango.TabArray.new(1, True)
            tabs.set_tab(0, Pango.TabAlign.LEFT, int(tab_width_px))
            layout.set_tabs(tabs)

        if auto_dir:
            layout.set_auto_dir(True)
        if text:
            layout.set_text(text, -1)
        return layout

    def install_im(self):
        self.install_scroll()
        self.hadj = Gtk.Adjustment(
            value=0, lower=0, upper=1, step_increment=20, page_increment=200, page_size=100
        )
        self.vadj = Gtk.Adjustment(
            value=0, lower=0, upper=1, step_increment=1, page_increment=10, page_size=1
        )
        self.vadj.connect("value-changed", self.on_vadj_changed)
        self.hadj.connect("value-changed", self.on_hadj_changed)

        # Connect to buffer using observer pattern (not GObject signal)
        if hasattr(self.buf, 'add_observer'):
            self.buf.add_observer(self.on_buffer_changed)
        elif hasattr(self.buf, 'connect'):
             self.buf.connect("changed", self.on_buffer_changed)



        # Setup IM context with preedit support
        self.im = Gtk.IMMulticontext()
        self.im.connect("commit", self.on_commit)
        self.im.connect("preedit-changed", self.on_preedit_changed)
        self.im.connect("preedit-start", self.on_preedit_start)
        self.im.connect("preedit-end", self.on_preedit_end)
        self.connect("resize", self.on_resize)

        # Preedit state
        self.preedit_string = ""
        self.preedit_cursor = 0
        
        # Connect focus events
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self.on_focus_in)
        focus.connect("leave", self.on_focus_out)
        self.add_controller(focus)
        
        # Cursor blink state
        # Cursor blink state (smooth fade)
        self.cursor_visible = True
        self.cursor_blink_timeout = None

        self.cursor_phase = 1.0           # animation phase 0 → 2
        self.cursor_fade_speed = 0.01     # 0.02 ~ 50fps smooth fade

        self.start_cursor_blink()
        
        # Connect to size changes to update scrollbars
        self.connect('resize', self.on_resize)

    def set_search_results(self, matches, max_match_length=0, preserve_current=False, auto_scroll=True):
        """Update search results.
        
        Args:
            matches: List of match tuples (line, col, length)
            max_match_length: Maximum match length
            preserve_current: Try to preserve current match
            auto_scroll: If True, scroll to first match. If False, don't scroll (used when editing).
        """
        old_idx = self.current_match_idx
        old_match = self.current_match
        
        self.search_matches = matches
        self.max_match_length = max_match_length
        self.current_match_idx = -1
        self.current_match = None
        self.highlight_cache = {} 

        if not matches:
            self._skip_to_position = None
            self.queue_draw()
            return

        # Check if we have a skip position to honor (set by replace operation)
        if self._skip_to_position:
            skip_ln, skip_col = self._skip_to_position
            self._skip_to_position = None  # Clear after use
            
            for i, m in enumerate(matches):
                m_ln, m_col = m[0], m[1]
                if (m_ln > skip_ln) or (m_ln == skip_ln and m_col >= skip_col):
                    self.current_match_idx = i
                    self.current_match = m
                    if auto_scroll:
                        self._scroll_to_match(m)
                    self.queue_draw()
                    return
            
            # No match after skip position - wrap to first
            if matches:
                self.current_match_idx = 0
                self.current_match = matches[0]
                if auto_scroll:
                    self._scroll_to_match(matches[0])
                self.queue_draw()
                return

        # Try to preserve current match if requested
        if preserve_current and old_match is not None:
            if 0 <= old_idx < len(matches):
                if matches[old_idx] == old_match:
                    self.current_match_idx = old_idx
                    self.current_match = old_match
                    self.queue_draw()
                    return
        
        # Don't set current_match on initial search - orange highlight only shows
        # when user clicks Next/Previous buttons
        # Just show yellow highlights for all matches, no orange current match
        self.queue_draw()

    def next_match(self):
        if not self.search_matches:
            return
        
        self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)
        self.current_match = self.search_matches[self.current_match_idx]
        self._scroll_to_match(self.current_match)
        self.queue_draw()
        
        # Progressive search: trigger continuation when near end
        if hasattr(self, 'find_bar') and self.find_bar:
            self.find_bar._check_progressive_search(self.current_match_idx)

    def prev_match(self):
        if not self.search_matches:
            return
            
        self.current_match_idx = (self.current_match_idx - 1) % len(self.search_matches)
        self.current_match = self.search_matches[self.current_match_idx]
        self._scroll_to_match(self.current_match)
        self.queue_draw()
        
        # Progressive search: trigger continuation when near end  
        if hasattr(self, 'find_bar') and self.find_bar:
            self.find_bar._check_progressive_search(self.current_match_idx)

    def _scroll_to_match(self, match):
        s_ln = match[0]
        s_col = match[1]
        
        # Determine target scroll position
        if self.mapper.enabled:
            # Use visual estimation
            # We want to center the match visually
            
            # Since Mapper doesn't support "visual line index of logical line X",
            # we can only scroll to the start of the logical line + visual offset
            vis_off, col_off = self.mapper.column_to_visual_offset(s_ln, s_col)
            
            self.scroll_line = s_ln
            self.scroll_visual_offset = vis_off
            
            # Adjust to center (rough estimate of rows)
            visible_rows = max(1, self.get_height() // self.line_h)
            rows_above = visible_rows // 2
            
            # Simple backtrack to center
            # Ideally we should backtrack 'rows_above' visual rows
            # For now, just centering the logical line roughly
            if rows_above > 0:
                if self.scroll_visual_offset >= rows_above:
                    self.scroll_visual_offset -= rows_above
                else:
                    rows_left = rows_above - self.scroll_visual_offset
                    self.scroll_visual_offset = 0
                    
                    # Accurate backtracking loop to center the match
                    # We need to backtrack 'rows_left' visual lines from the start of 's_ln'
                    prev = s_ln - 1
                    while prev >= 0 and rows_left > 0:
                        # Get visual height of previous line
                        h_p = self.mapper.get_visual_line_count(prev)
                        
                        if h_p > rows_left:
                            # Previous line is taller than needed. 
                            # We can stop here and show the bottom part of 'prev'
                            self.scroll_line = prev
                            self.scroll_visual_offset = max(0, h_p - rows_left)
                            rows_left = 0
                        else:
                            # Previous line fits fully/partially within the space we need to fill
                            rows_left -= h_p
                            self.scroll_line = prev
                            prev -= 1
                    
                    # If we ran out of lines (prev < 0) but still have rows_left, 
                    # we are at the top of the file, so just stay at 0,0 (already set by loop logic effectively)

        else:
            # Logical lines
            visible_lines = max(1, self.get_height() // self.line_h)
            self.scroll_line = max(0, s_ln - visible_lines // 2)
            self.scroll_visual_offset = 0
        
        # --- Horizontal Scrolling ---
        # Ensure the match is visible horizontally
        if self.hadj:
            # Calculate target X position (approximate using char_width)
            # 50px margin/padding assumed (gutter + left padding)
            gutter_w = 50 
            if hasattr(self, 'gutter_width'):
                 gutter_w = self.gutter_width
            
            # Using char_width (approximate for variable width, but good enough for monospace/code)
            # If char_width is not available (e.g. not initialized), skip
            cw = getattr(self, 'char_width', 10) # default fallback
            
            match_x = (s_col * cw)
            
            curr_val = self.hadj.get_value()
            page_size = self.hadj.get_page_size()
            max_val = curr_val + page_size
            
            # Margins for context
            # CENTER IT:
            # target_val = match_x - (viewport_width / 2)
            # We want the match column to be in the middle of the screen
            
            target_val = curr_val
            
            # If match is on screen, maybe we don't force center?
            # User request: "center the match in the viewport so that it is visible clearly"
            # This implies forcing center is desired for clarity.
            
            center_target = match_x - (page_size / 2) + (cw / 2)
            target_val = max(0, center_target)
            
            # Don't scroll past the end (though uppper bound usually handles this, we can clamp)
            upper = self.hadj.get_upper()
            target_val = min(target_val, max(0, upper - page_size))

            if abs(target_val - curr_val) > 1: # Avoid jitter
                self.hadj.set_value(target_val)
        
        self.update_scrollbar()
        self.queue_draw()

    def on_buffer_changed(self, *args):
        """Handle buffer content changes."""
        # Invalidate layout cache
        self.mapper.invalidate_all()
        
        # Queue redraw and scrollbar update
        self.queue_draw()
        GLib.idle_add(self.update_scrollbar)

    def on_vadj_changed(self, adj):
        """Handle scrollbar value change with smooth fractional scrolling."""
        # Avoid recursive updates during scrollbar update
        if self.scroll_update_pending:
            return

        val = adj.get_value()
        
        # Scrollbar resolution for smoothness
        scroll_resolution = 1.0 # Should match update_scrollbar
        
        if self.mapper.enabled:
            # Word wrap mode: Map scrollbar position to visual lines with smooth fractional offsets
            total_vis = self.mapper.get_total_visual_lines()
            if total_vis <= 0: 
                return
            
            # Get scrollbar parameters
            actual_val = val / scroll_resolution
            upper = adj.get_upper() / scroll_resolution
            page_size = adj.get_page_size() / scroll_resolution
            max_scroll = max(1.0, upper - page_size)
            
            # Calculate target visual line position (with fractional part for smoothness)
            # This gives us a continuous float value representing visual line position
            ratio = min(1.0, actual_val / max_scroll) if max_scroll > 0 else 0.0
            target_visual_line = ratio * max(0, total_vis - 1)
            
            total_lines = self.buf.total()
            
            # --- Optimization for Large Files ---
            # Linear scanning of visual segments is O(N) and freezes for large files (e.g. 1M+ lines).
            # We use a threshold: for small files, be precise. For large files, approximate.
            if total_lines > 1000:
                # O(1) Approximation for Large Files
                
                # Check if we are at the bottom (ratio near 1.0)
                if ratio > 0.99:
                    # Align end of file to bottom of viewport
                    last_line = max(0, total_lines - 1)
                    
                    # Backtrack algorithm to fill viewport from bottom
                    visible_rows = max(1, self.get_height() // self.line_h)
                    needed = visible_rows
                    
                    # Assume last line height is 1 for speed in large files, or check cache?
                    # Checking cache is safe enough for just one line.
                    segments_last = self.mapper.get_line_segments(last_line)
                    vis_height_last = len(segments_last) if segments_last else 1
                    
                    needed -= vis_height_last
                    
                    if needed < 0:
                        # Last line is huge
                        self.scroll_line = last_line
                        self.scroll_visual_offset = max(0, vis_height_last - visible_rows)
                    else:
                        self.scroll_line = last_line
                        self.scroll_visual_offset = 0
                        
                        # Accurate backtracking loop
                        # Even for large files, we only scan the last ~50 lines, which is cheap.
                        prev = last_line - 1
                        while prev >= 0 and needed > 0:
                             h_p = self.mapper.get_visual_line_count(prev)
                             needed -= h_p
                             self.scroll_line = prev
                             prev -= 1
                        
                        if needed < 0:
                             # Overshot. We are showing the bottom part of 'self.scroll_line'.
                             self.scroll_visual_offset = abs(needed)
                        
                    self.scroll_line_frac = 0.0
                    if self.on_scroll_callback:
                        self.on_scroll_callback()
                    self.queue_draw()
                    return

                # Normal Scroll Position
                # We map ratio directly to logical line index.
                # This assumes uniform distribution of wrapping, which is standard for huge files.
                self.scroll_line = int(ratio * (total_lines - 1))
                self.scroll_line = max(0, min(self.scroll_line, total_lines - 1))
                self.scroll_visual_offset = 0
                self.scroll_line_frac = 0.0
                
                if self.on_scroll_callback:
                    self.on_scroll_callback()
                self.queue_draw()
                return

            # --- Precise Calculation for Small Files ---
            # Binary search logic could be used here if we had an Interval Tree, 
            # but we don't. Linear scan is fast enough for < 1000 lines.
            
            # Use actual scroll value directly. 
            # Adjustment value 0..X means "start showing from visual line X".
            # Max value is (Total - PageSize), so at max scroll, we start at Total-PageSize,
            # which naturally aligns the last line to the bottom of the viewport.
            target_visual_line = actual_val
            
            current_visual = 0.0
            
            # Iterate through logical lines to find which one contains our target visual line
            for i in range(total_lines):
                segments = self.mapper.get_line_segments(i)
                num_segments = len(segments) if segments else 1
                
                # Check if target is within this logical line's visual range
                if current_visual <= target_visual_line < current_visual + num_segments:
                    # Found the logical line
                    self.scroll_line = i
                    
                    # Calculate visual offset within this line
                    remaining = target_visual_line - current_visual
                    self.scroll_visual_offset = int(remaining)
                    self.scroll_line_frac = remaining - self.scroll_visual_offset
                    
                    # Clamp to valid range
                    self.scroll_visual_offset = max(0, min(self.scroll_visual_offset, num_segments - 1))
                    
                    if self.scroll_line_frac < 0: self.scroll_line_frac = 0.0
                    if self.scroll_line_frac >= 1.0: self.scroll_line_frac = 0.99
                    
                    if self.on_scroll_callback:
                        self.on_scroll_callback()
                    self.queue_draw()
                    return
                
                current_visual += num_segments
            
            # Fallback - if we ran out of lines but haven't reached target
            # This happens because get_total_visual_lines returns 1.05x estimate or due to slight miscalculation
            
            # IMPROVED FALLBACK: Instead of snapping to the very last line at the top,
            # we want to align the end of the file with the BOTTOM of the viewport.
            
            # Find the last logical line
            last_line = max(0, total_lines - 1)
            
            # Get visual height of the last line
            segments_last = self.mapper.get_line_segments(last_line)
            vis_height_last = len(segments_last) if segments_last else 1
            
            # We want to fill the viewport upwards from the bottom.
            # Start at last line, backtrack until viewport is full.
            
            self.scroll_line = last_line
            # Show the TOP of the last visual chunk of the last line? No, show the start of the last line if possible,
            # but if it's huge, show the end.
            # Actually simpler: Set scroll_line to last_line, and scroll_visual_offset such that the END is at viewport bottom.
            
            visible_rows = max(1, self.get_height() // self.line_h)
            height_lines = visible_rows
            
            # Backtrack algorithm
            curr = last_line
            needed = height_lines
            
            # We already occupy 'vis_height_last' with the last line
            needed -= vis_height_last
            
            # If the last line is TALLER than viewport, we scroll to show its end
            if needed < 0:
                 self.scroll_line = last_line
                 # Visual offset should be such that end is at bottom
                 # total_vis_lines_in_last = vis_height_last
                 # We want to see the last 'visible_rows' of it
                 self.scroll_visual_offset = max(0, vis_height_last - visible_rows)
            else:
                 # Last line fits, with space to spare. Backtrack to fill space.
                 self.scroll_line = last_line
                 self.scroll_visual_offset = 0 # Show start of last line
                 
                 # Now backtrack previous lines
                 prev = curr - 1
                 while prev >= 0 and needed > 0:
                     seg_p = self.mapper.get_line_segments(prev)
                     h_p = len(seg_p) if seg_p else 1
                     needed -= h_p
                     self.scroll_line = prev
                     prev -= 1
                 
                 # if needed < 0, it means we went back one too many lines fully.
                 # The 'scroll_line' is now the top line.
                 # If needed < 0, it means the top line is only partially visible at top.
                 # But we display integer lines at top (scroll_visual_offset).
                 
                 # The 'prev' loop moves scroll_line to the top-most fully or partially visible line.
                 # If we overshot (needed < 0), it means 'self.scroll_line' (which is prev + 1 at this point)
                 # has height 'h_current'. 'needed' is negative amount of that height that is CUT OFF at top.
                 # So we need to show the BOTTOM part of that line.
                 
                 if needed < 0:
                     # We need to skip the first 'abs(needed)' visual lines of self.scroll_line
                     self.scroll_visual_offset = abs(needed)
            
            self.scroll_line_frac = 0.0
            if self.on_scroll_callback:
                self.on_scroll_callback()
            self.queue_draw()
        else:
            # No wrap: Direct line scrolling with fractional position for smoothness
            if not hasattr(self, 'scroll_line_frac'):
                self.scroll_line_frac = 0.0
            
            # Convert scrollbar value to line position (with fraction)
            actual_val = val / scroll_resolution
            
            self.scroll_line = int(actual_val)
            self.scroll_line_frac = actual_val - self.scroll_line
            self.scroll_line = max(0, min(self.scroll_line, self.buf.total() - 1))
            
            # Clamp fraction
            if self.scroll_line_frac < 0:
                self.scroll_line_frac = 0.0
            if self.scroll_line_frac >= 1.0:
                self.scroll_line_frac = 0.99
                
            if self.on_scroll_callback:
                self.on_scroll_callback()
            self.queue_draw()
    
    def on_hadj_changed(self, adj):
        # When scrollbar moves → update internal scroll offset
        new = int(adj.get_value())
        if new != self.scroll_x:
            self.scroll_x = new
            self.queue_draw()
                
    def on_resize(self, widget, width, height):
        """Resize handler."""
        # 0. Capture current read position (approx chars into the line)
        old_char_offset = 0
        if hasattr(self, 'mapper') and self.mapper.enabled:
            ln_width = 30
            if self.show_line_numbers:
                ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
            
            old_viewport_chars = max(1, int((self.get_width() - ln_width - 20) / max(0.1, self.char_width)))
            frac = getattr(self, 'scroll_line_frac', 0.0)
            old_char_offset = (self.scroll_visual_offset + frac) * old_viewport_chars

        # Update metrics first to ensure char_width is up to date
        if hasattr(self, 'update_metrics'):
             self.update_metrics()
             
        # Update mapper with new width
        if hasattr(self, 'mapper'):
            # subtract gutter width if needed
            ln_width = 0
            if self.show_line_numbers:
                ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
            
            # --- Two-pass layout strategy ---
            # Pass 1: Try with NO scrollbar padding (optimistic), but with base padding 10px
            # This allows us to see if content fits without wrapping unnecessarily
            viewport_w = width - ln_width - 10
            self.mapper.set_viewport_width(viewport_w, self.char_width)
            
            # Restore check is tricky if width changes 2nd time, but delta is small.
            # Let's just do the check.
            self.mapper.invalidate_all()
            
            # Check if scrollbar is needed with full width
            # We must use update_scrollbar logic or call it directly.
            # Calling it directly updates self.vscroll.get_visible()
            self.update_scrollbar()
            
            if self.vscroll.get_visible():
                # Pass 2: Scrollbar IS required. 
                # Reduce viewport width by 20px to prevent text under scrollbar
                viewport_w = width - ln_width - 20
                self.mapper.set_viewport_width(viewport_w, self.char_width)
                self.mapper.invalidate_all()
                self.update_scrollbar() # Re-calc scrollbar limits with new height/lines
                
            # Restore scroll position logic
            # Use the FINAL viewport_w_chars
            if self.mapper.enabled and old_char_offset > 0:
                 new_viewport_w_chars = self.mapper._viewport_width
                 if new_viewport_w_chars < 1: new_viewport_w_chars = 1
                 
                 self.scroll_visual_offset = int(old_char_offset / new_viewport_w_chars)
                 rem = old_char_offset - (self.scroll_visual_offset * new_viewport_w_chars)
                 self.scroll_line_frac = rem / new_viewport_w_chars

        # Debounce scrollbar update to ensure it settles correctly after resize
        # (Already called above, but harmless to call again)
        # self.update_scrollbar() 
        
        if hasattr(self, '_resize_timer') and self._resize_timer:
            GLib.source_remove(self._resize_timer)
            
        self._resize_timer = GLib.timeout_add(100, self._delayed_resize_update)
        
        self.queue_draw()
        return False
        
    def _delayed_resize_update(self):
        """Final update after resize settles."""
        self._resize_timer = None
        self.update_scrollbar()
        self.queue_draw()
        return False

    def file_loaded(self):
        """Called after a new file is loaded"""
        self.mapper.invalidate_all()
        self.queue_draw()
        self.update_scrollbar()
        
    def update_scrollbar(self):
        """Update scrollbar values and visibility."""
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return
            
        self.scroll_update_pending = True # Lock
        try:
            line_h = self.line_h
            visible_rows = max(1, height // line_h)
            total_lines = self.buf.total()
            
            if self.mapper.enabled:
                total_vis = self.mapper.get_total_visual_lines()
                
                # Use 10x resolution for ultra-smooth thumb dragging
                scroll_resolution = 1.0
                
                self.vadj.set_lower(0)
                self.vadj.set_upper(total_vis * scroll_resolution)
                self.vadj.set_page_size(visible_rows * scroll_resolution)
                self.vadj.set_step_increment(scroll_resolution)
                self.vadj.set_page_increment(visible_rows * scroll_resolution)
                
                # Estimate current visual position based on BYTES for potential variable line heights
                # val = (current_byte_offset / total_bytes) * total_vis
                
                total_bytes = 1
                if hasattr(self.buf, 'total_size'):
                     total_bytes = max(1, self.buf.total_size)
                
                # Get start byte of current line
                start_byte = 0
                line_info = self.buf.get_line_info(self.scroll_line)
                if line_info:
                    start_byte = line_info.offset
                else:
                    # Fallback for unindexed lines (e.g. newly inserted at end)
                    # We estimate the byte offset linearly based on line number
                    if self.buf.total() > 0:
                         start_byte = int((self.scroll_line / self.buf.total()) * total_bytes)
                
                # Add offset from visual rows (approximate bytes)
                # We assume 1 byte/char for smoothness calculation to allow sub-line granularity
                # viewport_char_width * visual_offset
                # viewport_char_width * visual_offset
                # Use value from mapper which ensures consistency with total_vis
                # (especially during resize where get_width() might be stale)
                width_chars = self.mapper._viewport_width
                if width_chars < 1: width_chars = 1
                
                # width = self.get_width()
                # ln_width = 30 
                # if self.show_line_numbers:
                #    ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
                if self.show_line_numbers:
                    ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
                else:
                    ln_width = 0
                
                # Use reduced padding as requested (20px instead of 25px)
                # The user requested "reduce the padding" (was 40, then 25, now 10, now 20 polish).
                # User specifically asked for 10px for non-scrollbar mode.
                padding = 20 if self.vscroll.get_visible() else 10
                viewport_w = max(1, width - ln_width - padding)
                # width_chars = max(1, int(viewport_w / max(0.1, self.char_width)))
                
                frac = getattr(self, 'scroll_line_frac', 0.0)
                bytes_in_view = (self.scroll_visual_offset + frac) * width_chars
                
                curr_byte = start_byte + bytes_in_view
                
                # Ratio of TOTAL file
                ratio = curr_byte / total_bytes
                
                # Use max realizable scroll value (upper - page_size) to match on_vadj_changed
                upper = total_vis * scroll_resolution
                page_sz = visible_rows * scroll_resolution
                max_scroll = max(1.0, upper - page_sz)
                
                curr_val = ratio * max_scroll
                
                self.vadj.set_value(curr_val)
                self.vscroll.set_visible(total_vis > visible_rows)
                
                # Horizontal scrollbar disabled/hidden in wrap mode usually
                self.hscroll.set_visible(False)
            else:
                # No wrap - use 1.0 resolution (float) matches on_vadj_changed
                scroll_resolution = 1.0
                
                self.vadj.set_lower(0)
                self.vadj.set_upper(total_lines * scroll_resolution)
                self.vadj.set_page_size(visible_rows * scroll_resolution)
                self.vadj.set_value(self.scroll_line * scroll_resolution + getattr(self, 'scroll_line_frac', 0.0) * scroll_resolution)
                self.vscroll.set_visible(total_lines > visible_rows)
                
                # Horizontal
                # Need max line width.. rough estimate or scanning
                # For now assume mostly visible or fixed large width
                # Horizontal (NO-WRAP)
                padding = 20 if self.vscroll.get_visible() else 10
                viewport_w = width - padding
                
                # Compute line number gutter width (must match draw_view)
                if self.show_line_numbers:
                    ln_width = max(
                        30,
                        int(len(str(self.buf.total())) * self.char_width) + 10
                    )
                else:
                    ln_width = 0

                # Check active match to ensure it's within scrollable range
                match_limit_w = 0
                if self.current_match:
                    try:
                        # Match: (line, col, end_line, end_col)
                        # Ensure we can scroll at least to the match + some margin
                        m_col = self.current_match[1]
                        cw = getattr(self, 'char_width', 10)
                        # Allow scrolling a bit past the match start
                        match_limit_w = (m_col + 50) * cw
                    except:
                        pass

                content_w = max(
                    viewport_w,
                    int(self.max_line_width) + ln_width + 2,
                    int(match_limit_w) + ln_width + 2
                )

                
                self.hadj.set_lower(0)
                self.hadj.set_upper(content_w)
                self.hadj.set_page_size(viewport_w)
                self.hadj.set_value(min(self.scroll_x, max(0, content_w - viewport_w)))
                
                self.hscroll.set_visible(content_w > viewport_w)

                
        finally:
            self.scroll_update_pending = False

    def start_cursor_blink(self):
        """Start smooth cursor blinking with lightweight animation."""
        self.cursor_visible = True
        self.cursor_phase = 1.0

        FPS = 60
        INTERVAL = int(1000 / FPS)

        def tick():
            self.cursor_phase += 0.05 # Speed
            if self.cursor_phase >= 2.0:
                self.cursor_phase -= 2.0

            if not self.calculating: # throttle if busy
                 self.queue_draw()
            return True

        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)

        self.cursor_blink_timeout = GLib.timeout_add(INTERVAL, tick)

    def stop_cursor_blink(self):
        """Immediately stop blinking and show cursor solid."""
        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)
            self.cursor_blink_timeout = None

        self.cursor_visible = True
        self.cursor_phase = 1.0
        self.queue_draw()

    def on_commit(self, im, text):
        """Handle committed text from IM (finished composition)"""
        if text:
            self.buf.insert_text(text, overwrite=self.overwrite_mode)
            self._notify_find_bar_editing()
            self.keep_cursor_visible()
            self.stop_cursor_blink()
            self.restart_blink_after_idle()
            self.queue_draw()
            self.update_im_cursor_location()

    def _notify_find_bar_editing(self):
        """Notify find bar that user is actively editing.
        
        Only clears the orange (current match) highlight when editing.
        Yellow highlights (all matches) remain visible.
        Orange highlight only reappears when clicking Next/Previous.
        """
        # Only clear the orange (current match) highlight, keep yellow (all matches)
        if hasattr(self, 'current_match') and self.current_match is not None:
            self.current_match = None
            self.current_match_idx = -1
            self.queue_draw()

    def restart_blink_after_idle(self):
        """Restart cursor blinking only once after user stops typing."""
        if hasattr(self, "_idle_blink_timeout") and self._idle_blink_timeout:
            GLib.source_remove(self._idle_blink_timeout)
            self._idle_blink_timeout = None

        def idle_blink():
            self._idle_blink_timeout = None
            self.start_cursor_blink()
            return False

        self._idle_blink_timeout = GLib.timeout_add(700, idle_blink)

    def on_preedit_start(self, im):
        self.queue_draw()

    def on_preedit_end(self, im):
        self.preedit_string = ""
        self.preedit_cursor = 0
        self.queue_draw()

    def on_preedit_changed(self, im):
        try:
            preedit_str, attrs, cursor_pos = self.im.get_preedit_string()
            self.preedit_string = preedit_str or ""
            self.preedit_cursor = cursor_pos
            self.queue_draw()
        except Exception as e:
            print(f"Preedit error: {e}")

    def on_focus_in(self, controller):
        self.im.focus_in()
        self.im.set_client_widget(self)
        self.update_im_cursor_location()
        
    def on_focus_out(self, controller):
            self.im.focus_out()

    def update_im_cursor_location(self):
        try:
            width = self.get_width()
            height = self.get_height()
            if width <= 0 or height <= 0: return
            
            cl, cc = self.buf.cursor_line, self.buf.cursor_col
            
            # Helper to calculate visual x,y
            # We reuse the logic from draw_view or simplify it
            
            # Simple check if visible
            if cl < self.scroll_line or cl > self.scroll_line + (height // self.line_h) + 1:
                return 
                
            # Calculate Y
            # If wrapped, complex...
            # For IM, approximate is usually okay or just disable if complex.
            # Let's try to be somewhat accurate
            
            # Approximate Y relative to top of viewport
            rel_ln = cl - self.scroll_line
            y = rel_ln * self.line_h 
            
            # Calculate X
            # Get wrapped line segment for cursor
            segments = self.mapper.get_line_segments(cl)
            vis_off, col_off = self.mapper.column_to_visual_offset(cl, cc)
            
            # If line is wrapped and we are on a wrapped visual line, adjust Y
            # This is tricky because we only know scroll_line (logical).
            # If scroll_visual_offset > 0, we need to account for it.
            
            if cl == self.scroll_line:
                if vis_off < self.scroll_visual_offset:
                    return # Above view
                y = (vis_off - self.scroll_visual_offset) * self.line_h
            else:
                 # Logic for cl > scroll_line
                 # We need to know how many visual lines are between scroll_line and cl
                 # This requires iterating... expensive.
                 # Fallback: simple logical line difference * line_h
                 # This will be wrong for wrapped text but better than nothing
                 pass
            
            x = 0
            if segments:
                 # Find segment for current col
                 # vis_off is the index of segment
                 if vis_off < len(segments):
                      seg_start, seg_end = segments[vis_off]
                      seg_text = self.buf.get_line(cl)[seg_start:seg_end]
                      
                      # Calculate X offset in segment
                      rel_col = cc - seg_start
                      
                      # Measure Width
                      # Use temporary layout
                      surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                      cr = cairo.Context(surface)
                      layout = self.create_text_layout(cr, seg_text)
                      
                      idx = self.visual_byte_index(seg_text, rel_col)
                      pos = layout.get_cursor_pos(idx)[0]
                      
                      ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
                      base_x = ln_width + 2 - self.scroll_x
                      
                      x = base_x + (pos.x / Pango.SCALE)
            
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 2
            rect.height = int(self.line_h)
            
            self.im.set_cursor_location(rect)
            
        except Exception:
            pass
    def xy_to_line_col(self, x, y):
        """Convert widget coordinates to logical line/col."""
        if self.show_line_numbers:
            ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
        else:
            ln_width = 0
        text_x = x - ln_width - 2 + self.scroll_x
        
        if text_x < 0: text_x = 0
            
        target_y = y
        
        # Start at scroll_line
        curr_ln = self.scroll_line
        curr_vis_off = self.scroll_visual_offset
        
        curr_y = 0
        found_ln = -1
        found_col = 0
        
        total_lines = self.buf.total()
        get_line = self.buf.get_line
        
        # Iterate until we find the line or go off screen
        while curr_ln < total_lines:
             segments = self.mapper.get_line_segments(curr_ln)
             num_segs = len(segments)
             
             start_seg = 0
             if curr_ln == self.scroll_line:
                 start_seg = curr_vis_off
             
             if start_seg >= num_segs:
                 # Should not happen typically unless scroll is stale, assume 0 lines height for safety or skip
                 start_seg = 0
                 
             height_px = (num_segs - start_seg) * self.line_h
             
             if curr_y + height_px > target_y:
                 # Found it
                 seg_idx = int((target_y - curr_y) // self.line_h) + start_seg
                 
                 found_ln = curr_ln
                 
                 # Map text_x to column
                 if segments:
                     # Clamp seg_idx to valid range to prevent IndexError
                     seg_idx = max(0, min(seg_idx, len(segments) - 1))
                     s_start, s_end = segments[seg_idx]
                     text = get_line(curr_ln)[s_start:s_end]
                     
                     surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                     cr = cairo.Context(surface)
                     col_in_seg = self.pixel_to_column(cr, text, text_x)
                     found_col = s_start + col_in_seg
                 else:
                     found_col = 0
                     
                 break
             
             curr_y += height_px
             curr_ln += 1
             
             if curr_y > self.get_height():
                 break
                 
        if found_ln == -1:
             found_ln = max(0, total_lines - 1)
             found_col = len(get_line(found_ln))
             
        return found_ln, found_col

    def pixel_to_column(self, cr, text, px):
        """Map pixel X to column in text segment."""
        if not text: return 0
        if px <= 0: return 0
        
        layout = self.create_text_layout(cr, text)
        text_w = layout.get_pixel_size()[0]
        if px >= text_w: return len(text)
        
        success, idx, trailing = layout.xy_to_index(int(px * Pango.SCALE), 0)
        if not success: return len(text)
        
        # Check if ASCII optim
        if len(text) == len(text.encode('utf-8')):
             return idx + (1 if trailing else 0)

        # Convert byte index to char index
        byte_pos = 0
        for i, ch in enumerate(text):
             if byte_pos >= idx:
                 return i
             byte_pos += len(ch.encode('utf-8'))
             
        return len(text)

        
    def on_key(self, c, keyval, keycode, state):
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True

        name = Gdk.keyval_name(keyval)
        shift_pressed = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        alt_pressed = (state & Gdk.ModifierType.ALT_MASK) != 0

        # Undo (Ctrl+Z)
        if ctrl_pressed and not shift_pressed and not alt_pressed and (name == "z" or name == "Z"):
            pos = self.undo_manager.undo(self.buf)
            if pos:
                self.buf.set_cursor(pos.line, pos.col)
            self.update_scrollbar()
            self.keep_cursor_visible()
            self.queue_draw()
            return True
            
        # Redo (Ctrl+Y or Ctrl+Shift+Z)
        if ctrl_pressed and \
           ((not shift_pressed and (name == "y" or name == "Y")) or \
            (shift_pressed and (name == "z" or name == "Z"))):
            pos = self.undo_manager.redo(self.buf)
            if pos:
                self.buf.set_cursor(pos.line, pos.col)
            self.update_scrollbar()
            self.keep_cursor_visible()
            self.queue_draw()
            return True

        # Alt+Z - Toggle word wrap
        if alt_pressed and (name == "z" or name == "Z"):
            # Get the window and call its on_toggle_word_wrap method
            # This ensures find bar is closed and search cleared before toggling
            window = self.get_ancestor(Adw.ApplicationWindow)
            if window and hasattr(window, 'on_toggle_word_wrap'):
                window.on_toggle_word_wrap(None, None)
            return True

        # Alt+Arrow keys for text movement
        if alt_pressed:
            if name == "Left":
                self.buf.move_word_left_with_text()
            elif name == "Right":
                self.buf.move_word_right_with_text()
            elif name == "Up":
                self.buf.move_line_up_with_text()
            elif name == "Down":
                self.buf.move_line_down_with_text()
            else:
                return False 
            
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if keyval == Gdk.KEY_Tab:
            if shift_pressed:
                self.buf.unindent_selection()
            elif self.buf.selection.has_selection() and \
                 self.buf.selection.start_line != self.buf.selection.end_line:
                self.buf.indent_selection()
            else:
                if getattr(self, "use_tabs", True):
                    self.buf.insert_text("\t")
                else:
                    self.buf.insert_text(" " * self.tab_width)
            self.queue_draw()
            return True

        if keyval == Gdk.KEY_ISO_Left_Tab:
             self.buf.unindent_selection()
             self.queue_draw()
             return True

        # Ctrl+A
        if ctrl_pressed and name == "a":
            self.buf.select_all()
            self.queue_draw()
            return True

        # Clipboard
        if ctrl_pressed:
            if name == "c":
                self.copy_to_clipboard()
                return True
            elif name == "x":
                self.cut_to_clipboard()
                return True
            elif name == "v":
                self.paste_from_clipboard()
                return True

        # Insert
        if name == "Insert" and not ctrl_pressed and not shift_pressed:
            self.overwrite_mode = not self.overwrite_mode
            print(f"Overwrite mode: {'ON' if self.overwrite_mode else 'OFF'}")
            self.queue_draw()
            return True

        # Editing keys
        if name == "BackSpace":
            if ctrl_pressed and shift_pressed:
                self.buf.delete_to_line_start()
            elif ctrl_pressed:
                self.buf.delete_word_backward()
            else:
                self.buf.backspace()
            self._notify_find_bar_editing()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Delete":
            if ctrl_pressed and shift_pressed:
                self.buf.delete_to_line_end()
            elif ctrl_pressed:
                self.buf.delete_word_forward()
            else:
                self.buf.delete_key()
            self._notify_find_bar_editing()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Return":
             self.buf.insert_newline()
             if getattr(self, "auto_indent", True):
                 ln = self.buf.cursor_line - 1
                 if ln >= 0:
                     text = self.buf.get_line(ln)
                     indent = ""
                     for ch in text:
                         if ch in " \t": indent += ch
                         else: break
                     if indent: self.buf.insert_text(indent)
             self.keep_cursor_visible()
             self.update_im_cursor_location()
             self.queue_draw()
             return True

        # Navigation
        if name in ("Up", "Down", "Left", "Right", "Home", "End"):
            if name == "Up": self.ctrl.move_up(extend_selection=shift_pressed)
            elif name == "Down": self.ctrl.move_down(extend_selection=shift_pressed)
            elif name == "Left": 
                 if ctrl_pressed: self.ctrl.move_word_left(extend_selection=shift_pressed)
                 else: self.ctrl.move_left(extend_selection=shift_pressed)
            elif name == "Right":
                 if ctrl_pressed: self.ctrl.move_word_right(extend_selection=shift_pressed)
                 else: self.ctrl.move_right(extend_selection=shift_pressed)
            elif name == "Home":
                 if ctrl_pressed: self.ctrl.move_document_start(extend_selection=shift_pressed)
                 else: self.ctrl.move_home(extend_selection=shift_pressed)
            elif name == "End":
                 if ctrl_pressed: self.ctrl.move_document_end(extend_selection=shift_pressed)
                 else: self.ctrl.move_end(extend_selection=shift_pressed)
            
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Page Up/Down
        if name == "Page_Up" or name == "Page_Down":
             visible_lines = max(1, self.get_height() // self.line_h)
             steps = visible_lines
             
             if name == "Page_Up":
                 for _ in range(steps): self.ctrl.move_up(extend_selection=shift_pressed)
             else:
                 for _ in range(steps): self.ctrl.move_down(extend_selection=shift_pressed)
                 
             self.keep_cursor_visible()
             self.update_im_cursor_location()
             self.queue_draw()
             return True

        return False

    def copy_to_clipboard(self):
        """Copy selected text to clipboard with progress indicator"""
        self.show_busy("Copying...")
        
        # Defer execution to allow UI to render the busy overlay
        def _do_copy():
            try:
                text = self.buf.get_selected_text()
                if text:
                    clipboard = self.get_clipboard()
                    clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
            finally:
                self.hide_busy()
            return False
            
        GLib.timeout_add(20, _do_copy)

    def cut_to_clipboard(self):
        """Cut selected text to clipboard with progress indicator"""
        self.show_busy("Cutting...")
        
        # Defer execution
        def _do_cut():
            try:
                text = self.buf.get_selected_text()
                if text:
                    clipboard = self.get_clipboard()
                    clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
                    # Pass the text we just fetched to delete_selection to avoid re-fetching it
                    self.buf.delete_selection(provided_text=text)
                    self.queue_draw()
            finally:
                self.hide_busy()
            return False
            
        GLib.timeout_add(20, _do_cut)

    def paste_from_clipboard(self):
        """Paste text from clipboard with better error handling and progress"""
        clipboard = self.get_clipboard()
        
        def paste_ready(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
                if text:
                    self.show_busy("Pasting...")
                    
                    # Defer insert to allow UI update
                    def _do_paste():
                        try:
                            self.buf.insert_text(text)
                            
                            
                            # After paste, invalidate layout
                            if self.mapper.enabled:
                                self.mapper.invalidate_all()
                            self.update_scrollbar()
                        finally:
                            self.hide_busy()
                            self.queue_draw()
                        return False
                    
                    GLib.timeout_add(20, _do_paste)
                    
            except Exception as e:
                # Handle finish error
                error_msg = str(e)
                if "No compatible transfer format" not in error_msg:
                    print(f"Paste error: {e}")
                self.try_paste_fallback()

        clipboard.read_text_async(None, paste_ready)

    def try_paste_fallback(self):
        """Fallback method to try getting clipboard text"""
        try:
            clipboard = self.get_clipboard()
            
            # Try to get formats available
            formats = clipboard.get_formats()
            
            # Check if text is available in any format
            if formats.contain_mime_type("text/plain"):
                # Try reading as plain text with UTF-8 encoding
                def read_ready(clipboard, result):
                    try:
                        success, data = clipboard.read_finish(result)
                        if success and data:
                            # Try to decode as UTF-8
                            text = data.decode('utf-8', errors='ignore')
                            if text:
                                self.buf.insert_text(text)
                                
                                # After paste, clear wrap cache and recalculate everything
                                if self.renderer.wrap_enabled:
                                    self.renderer.wrap_cache.clear()
                                    self.renderer.total_visual_lines_cache = None
                                    self.renderer.estimated_total_cache = None
                                    self.renderer.visual_line_map = []
                                    self.renderer.edits_since_cache_invalidation = 0
                                
                                self.keep_cursor_visible()
                                self.update_scrollbar()  # Update scrollbar range after paste
                                self.update_im_cursor_location()
                                self.queue_draw()
                    except Exception as e:
                        # Silently fail - clipboard probably contains non-text data
                        pass
                
                clipboard.read_async(["text/plain"], 0, None, read_ready)
        except Exception as e:
            # Silently fail - this is just a fallback attempt
            pass

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        key.connect("key-released", self.on_key_release)
        self.add_controller(key)
        
    def on_key_release(self, c, keyval, keycode, state):
        """Filter key releases for IM"""
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True
        return False


    def install_mouse(self):
        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self.on_click_pressed)
        click.connect("released", self.on_click_released)
        self.add_controller(click)
        
        # Middle-click paste
        middle_click = Gtk.GestureClick()
        middle_click.set_button(2)  # Middle mouse button
        middle_click.connect("pressed", self.on_middle_click)
        self.add_controller(middle_click)
        
        # Right-click menu
        right_click = Gtk.GestureClick()
        right_click.set_button(3)  # Right mouse button
        right_click.connect("pressed", self.on_right_click)
        self.add_controller(right_click)
        
        # Track last click time and position for multi-click detection
        self.last_click_time = 0
        self.last_click_line = -1
        self.last_click_col = -1
        self.click_count = 0
        
        # Track word selection mode for drag-to-select-words
        self.word_selection_mode = False
        
        # Track the original anchor word boundaries (for stable bi-directional drag)
        self.anchor_word_start_line = -1
        self.anchor_word_start_col = -1
        self.anchor_word_end_line = -1
        self.anchor_word_end_col = -1
        
        # Track drag-and-drop mode for moving/copying selected text
        self.drag_and_drop_mode = False
        self.dragged_text = ""
        self.drop_position_line = -1
        self.drop_position_col = -1
        self.ctrl_pressed_during_drag = False  # Track if Ctrl is pressed during drag
        
        # Track if we clicked inside a selection (to handle click-to-clear vs drag)
        self._clicked_in_selection = False
        
        # Track if a drag might start (deferred until movement)
        # Track if a drag might start (deferred until movement)
        self._drag_pending = False
        
        # Auto-scroll on drag
        self.autoscroll_timer_id = None
        self.last_drag_x = 0
        self.last_drag_y = 0

    def on_middle_click(self, gesture, n_press, x, y):
        """Paste from primary clipboard on middle-click"""
        self.grab_focus()
        
        # Always use accurate xy_to_line_col
        ln, col = self.xy_to_line_col(x, y)
        
        # Move cursor to click position
        self.buf.set_cursor(ln, col)
        
        # Paste from PRIMARY clipboard (not CLIPBOARD)
        display = self.get_display()
        clipboard = display.get_primary_clipboard()
        clipboard.read_text_async(None, self.on_primary_paste_ready)
        
        self.queue_draw()

    def on_primary_paste_ready(self, clipboard, result):
        """Callback when primary clipboard text is ready"""
        try:
            text = clipboard.read_text_finish(result)
            if text:
                # Delete selection if any
                if self.buf.selection.has_selection():
                    self.buf.delete_selection()
                
                # Insert text at cursor
                self.buf.insert_text(text)
                
                # After paste, clear wrap cache and recalculate everything
                if self.renderer.wrap_enabled:
                    self.renderer.wrap_cache.clear()
                    self.renderer.total_visual_lines_cache = None
                    self.renderer.estimated_total_cache = None
                    self.renderer.visual_line_map = []
                    self.renderer.edits_since_cache_invalidation = 0
                
                self.keep_cursor_visible()
                self.update_scrollbar()  # Update scrollbar range after paste
                self.update_im_cursor_location()
                self.queue_draw()
        except Exception as e:
            print(f"Primary paste error: {e}")

    def on_right_click(self, gesture, n_press, x, y):
        """Show context menu on right-click"""
        self.grab_focus()
        
        # Create popover menu
        menu = Gtk.PopoverMenu()
        menu.set_parent(self)
        menu.set_has_arrow(False)
        
        # Create menu model
        menu_model = Gio.Menu()
        
        has_selection = self.buf.selection.has_selection()
        
        if has_selection:
            # Menu items for when there's a selection
            menu_model.append("Cut", "view.cut")
            menu_model.append("Copy", "view.copy")
            menu_model.append("Paste", "view.paste")
            menu_model.append("Delete", "view.delete")
        else:
            # Menu items for when there's no selection
            menu_model.append("Paste", "view.paste")
        
        # Always show these
        menu_model.append("Select All", "view.select-all")
        # Undo/Redo commented out until implemented
        menu_model.append("Undo", "view.undo")
        menu_model.append("Redo", "view.redo")
        
        menu.set_menu_model(menu_model)
        
        # Create action group if not exists
        if not hasattr(self, 'action_group'):
            self.action_group = Gio.SimpleActionGroup()
            self.insert_action_group("view", self.action_group)
            
            # Create actions using a loop
            actions = [
                ("cut", self.cut_to_clipboard),
                ("copy", self.copy_to_clipboard),
                ("paste", self.paste_from_clipboard),
                ("delete", self.on_delete_action),
                ("select-all", lambda: self.buf.select_all()),
                ("undo", self.on_undo_action),
                ("redo", self.on_redo_action),
            ]
            
            for action_name, callback in actions:
                action = Gio.SimpleAction.new(action_name, None)
                action.connect("activate", lambda a, p, cb=callback: cb())
                self.action_group.add_action(action)
        
        # Position the menu at the click location with slight offset
        rect = Gdk.Rectangle()
        rect.x = int(x) + 60
        rect.y = int(y) - 1
        rect.width = 1
        rect.height = 1
        menu.set_pointing_to(rect)
        
        menu.popup()

    def on_delete_action(self):
        """Delete selected text"""
        if self.buf.selection.has_selection():
            self.buf.delete_selection()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()

    def on_undo_action(self):
        """Undo action handler"""
        pos = self.undo_manager.undo(self.buf)
        if pos:
            self.buf.set_cursor(pos.line, pos.col)
        self.update_scrollbar()
        self.keep_cursor_visible()
        self.queue_draw()

    def on_redo_action(self):
        """Redo action handler"""
        pos = self.undo_manager.redo(self.buf)
        if pos:
            self.buf.set_cursor(pos.line, pos.col)
        self.update_scrollbar()
        self.keep_cursor_visible()
        self.queue_draw()

    def find_word_boundaries(self, line, col):
        """Find word boundaries at the given position. Words include alphanumeric and underscore."""
        import unicodedata
        
        if not line:
            return 0, 0
        
        # Check if character is a word character (letter, number, underscore, or combining mark)
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            # Letter categories: Lu, Ll, Lt, Lm, Lo
            # Number categories: Nd, Nl, No
            # Mark categories: Mn, Mc, Me (for combining characters like Devanagari vowel signs)
            return cat[0] in ('L', 'N', 'M')
        
        # If clicking beyond line or on whitespace/punctuation, select just that position
        if col >= len(line) or not is_word_char(line[col]):
            return col, min(col + 1, len(line))
        
        # Find start of word
        start = col
        while start > 0 and is_word_char(line[start - 1]):
            start -= 1
        
        # Find end of word
        end = col
        while end < len(line) and is_word_char(line[end]):
            end += 1
        
        return start, end

    def on_click_pressed(self, g, n_press, x, y):
        """Handle mouse click."""
        print(f"DEBUG: Click Pressed. Count={n_press}")
        self.grab_focus()

        # Always use accurate xy_to_line_col
        ln, col = self.xy_to_line_col(x, y)

        mods = g.get_current_event_state()
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)

        import time
        current_time = time.time()
        time_diff = current_time - self.last_click_time

        if time_diff > 0.5 or ln != self.last_click_line or abs(col - self.last_click_col) > 3:
            self.click_count = 0

        self.click_count += 1
        self.last_click_time = current_time
        self.last_click_line = ln
        self.last_click_col = col

        line_text = self.buf.get_line(ln)
        line_len = len(line_text)

        # SHIFT-extend remains unchanged
        if shift:
            if not self.buf.selection.active:
                self.buf.selection.set_start(self.buf.cursor_line, self.buf.cursor_col)
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col, extend_selection=True)
            self.update_matching_brackets()
            self.queue_draw()
            return

        # TRIPLE CLICK
        if self.click_count == 3:
            # Check if we are clicking inside an established selection (from double-click)
            # If so, defer the triple click action until release, in case user drags instead.
            if self.buf.selection.has_selection():
                s_line, s_col, e_line, e_col = self.buf.selection.get_bounds()
                is_inside = False
                if s_line == e_line:
                    if ln == s_line and s_col <= col < e_col:
                        is_inside = True
                else:
                    # Multi-line word selection? Possible.
                    if s_line <= ln <= e_line: # simplified check
                         is_inside = True
                
                if is_inside:
                    self._pending_triple_click = True
                    self._triple_click_ln = ln
                    self._triple_click_line_len = line_len
                    return # DEFER
            
            self.buf.selection.set_start(ln, 0)
            self.buf.selection.set_end(ln, line_len)
            self.buf.cursor_line = ln
            self.buf.cursor_col = line_len
            self.update_matching_brackets()
            self.queue_draw()
            return

        # DOUBLE CLICK - Context-aware selection (handles empty lines and end-of-line)
        if self.click_count == 2:

            # Case 1: empty line → context-aware selection
            if line_len == 0:
                # Check what comes next
                next_line_text = None
                if ln < self.buf.total() - 1:
                    next_line_text = self.buf.get_line(ln + 1)
                
                if next_line_text is not None and len(next_line_text) == 0:
                    # Next line is also empty: select only current empty line
                    self.buf.selection.set_start(ln, 0)
                    self.buf.selection.set_end(ln, 1)
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = 0
                    # Set anchor points for drag extension
                    self.anchor_word_start_line = ln
                    self.anchor_word_start_col = 0
                    self.anchor_word_end_line = ln
                    self.anchor_word_end_col = 1
                elif next_line_text is not None and len(next_line_text) > 0:
                    # Next line has text: select current empty line + next line's text
                    self.buf.selection.set_start(ln, 0)
                    self.buf.selection.set_end(ln + 1, len(next_line_text))
                    self.buf.cursor_line = ln + 1
                    self.buf.cursor_col = len(next_line_text)
                    # Set anchor points for drag extension
                    self.anchor_word_start_line = ln
                    self.anchor_word_start_col = 0
                    self.anchor_word_end_line = ln + 1
                    self.anchor_word_end_col = len(next_line_text)
                else:
                    # Last line (empty): don't select anything
                    self.buf.selection.clear()
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = 0
                
                # Enable word selection mode for drag (treat empty lines as "words")
                self.word_selection_mode = True
                
                self.update_matching_brackets()
                self.queue_draw()
                return

            # Case 2: double-click at or beyond end of text (context-aware like empty lines)
            if col >= line_len:
                # Check if this line has a newline (not the last line)
                has_newline = ln < self.buf.total() - 1
                
                if has_newline:
                    # Check what comes next (similar to empty line logic)
                    next_line_text = self.buf.get_line(ln + 1)
                    
                    if len(next_line_text) == 0:
                        # Next line is empty: select just the newline area (EOL to viewport)
                        self.buf.selection.set_start(ln, line_len)
                        self.buf.selection.set_end(ln, line_len + 1)
                        self.buf.cursor_line = ln
                        self.buf.cursor_col = line_len
                        # Set anchor points for drag extension
                        self.anchor_word_start_line = ln
                        self.anchor_word_start_col = line_len
                        self.anchor_word_end_line = ln
                        self.anchor_word_end_col = line_len + 1
                    else:
                        # Next line has text: select newline + next line's text
                        self.buf.selection.set_start(ln, line_len)
                        self.buf.selection.set_end(ln + 1, len(next_line_text))
                        self.buf.cursor_line = ln + 1
                        self.buf.cursor_col = len(next_line_text)
                        # Set anchor points for drag extension
                        self.anchor_word_start_line = ln
                        self.anchor_word_start_col = line_len
                        self.anchor_word_end_line = ln + 1
                        self.anchor_word_end_col = len(next_line_text)
                else:
                    # Last line (no newline): select trailing content
                    # Find what's at the end: word or spaces
                    if line_text and line_text[-1] == ' ':
                        # Find start of trailing spaces
                        start = line_len - 1
                        while start > 0 and line_text[start - 1] == ' ':
                            start -= 1
                        self.buf.selection.set_start(ln, start)
                        self.buf.selection.set_end(ln, line_len)
                        self.buf.cursor_line = ln
                        self.buf.cursor_col = line_len
                        # Set anchor points for drag extension
                        self.anchor_word_start_line = ln
                        self.anchor_word_start_col = start
                        self.anchor_word_end_line = ln
                        self.anchor_word_end_col = line_len
                    else:
                        # Select the last word
                        start_col, end_col = self.find_word_boundaries(line_text, line_len - 1)
                        self.buf.selection.set_start(ln, start_col)
                        self.buf.selection.set_end(ln, end_col)
                        self.buf.cursor_line = ln
                        self.buf.cursor_col = end_col
                        # Set anchor points for drag extension
                        self.anchor_word_start_line = ln
                        self.anchor_word_start_col = start_col
                        self.anchor_word_end_line = ln
                        self.anchor_word_end_col = end_col
                
                # Enable word selection mode for drag
                self.word_selection_mode = True
                
                self.queue_draw()
                return

            # Case 3: normal double-click → word selection
            start_col, end_col = self.find_word_boundaries(line_text, col)
            self.buf.selection.set_start(ln, start_col)
            self.buf.selection.set_end(ln, end_col)
            self.buf.cursor_line = ln
            self.buf.cursor_col = end_col
            
            # Enable word selection mode for drag AND store anchor word
            self.word_selection_mode = True
            self.anchor_word_start_line = ln
            self.anchor_word_start_col = start_col
            self.anchor_word_end_line = ln
            self.anchor_word_end_col = end_col
            
            self.queue_draw()
            return

        # SINGLE CLICK unchanged
        if self.buf.selection.has_selection():
            bounds = self.buf.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_line, start_col, end_line, end_col = bounds
                click_in_selection = False

                if start_line == end_line:
                    if ln == start_line and start_col <= col < end_col:
                        click_in_selection = True
                else:
                    if ln == start_line and col >= start_col:
                        click_in_selection = True
                    elif ln == end_line and col < end_col:
                        click_in_selection = True
                    elif start_line < ln < end_line:
                        click_in_selection = True

                if click_in_selection:
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = col
                    self._clicked_in_selection = True
                    self.queue_draw()
                    return

        # Check if we are clicking inside an established selection
        # If so, do NOT clear selection yet (wait for drag or release)
        if self.buf.selection.has_selection():
            s_line, s_col, e_line, e_col = self.buf.selection.get_bounds()
            is_inside = False
            if s_line == e_line:
                if ln == s_line and s_col <= col < e_col:
                    is_inside = True
            else:
                if ln == s_line and col >= s_col:
                    is_inside = True
                elif ln == e_line and col < e_col:
                    is_inside = True
                elif s_line < ln < e_line:
                    is_inside = True
            
            if is_inside:
                self._clicked_in_selection = True
                self._click_ln = ln
                self._click_col = col
                self._pending_click = True
                print("DEBUG: Clicked INSIDE selection. Pending Click Set. Returning.")
                self.queue_draw()
                return

        print("DEBUG: Clicked OUTSIDE selection. Clearing selection.")
        self._clicked_in_selection = False
        self.buf.selection.clear()
        self.ctrl.start_drag(ln, col)

        self._pending_click = True
        self._click_ln = ln
        self._click_col = col

        self.update_matching_brackets()
        self.queue_draw()


    def on_click(self, g, n, x, y):
        self.grab_focus()

        # Get modifiers
        modifiers = g.get_current_event_state()
        shift_pressed = (modifiers & Gdk.ModifierType.SHIFT_MASK) != 0

        # Create temporary cr for measurements
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        
        # Use cached line number width if available to ensure consistency with rendering
        if hasattr(self.renderer, 'last_ln_width') and self.renderer.last_ln_width is not None and self.renderer.last_ln_width > 0:
            ln_width = self.renderer.last_ln_width
        else:
            ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())
            
        # Adjust for scroll
        base_x_check = x + self.scroll_x
        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        # Calculate column position
        
        text = self.buf.get_line(ln)
        
        # Create layout for this line
        layout = self.create_text_layout(cr, text if text else " ")
        
        is_rtl = detect_rtl_line(text)
        text_w, _ = layout.get_pixel_size()
        view_w = self.get_width()
        
        # Calculate base_x matching the renderer
        base_x = self.renderer.calculate_text_base_x(is_rtl, text_w, view_w, ln_width, self.scroll_x)
        
        # Calculate relative pixel position from base
        col_pixels = x - base_x
        col_pixels = max(0, col_pixels)

        # Convert pixel to column
        col = self.pixel_to_column(cr, text, col_pixels)
        col = max(0, min(col, len(text)))

        # Handle shift-click for selection
        if shift_pressed:
            # Extend selection from current cursor position
            if not self.buf.selection.active:
                self.buf.selection.set_start(self.buf.cursor_line, self.buf.cursor_col)
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col, extend_selection=True)
        else:
        # Normal click - clear selection and move cursor
            self.ctrl.click(ln, col)
        
        self.queue_draw()

    def on_release(self, g, n, x, y):
        """Handle mouse button release"""
        self.stop_autoscroll()  # Stop auto-scroll on release
        self.ctrl.end_drag()



    def on_drag_begin(self, g, x, y):
        """Handle drag begin event."""
        print(f"DEBUG: Drag Begin at {x},{y}")
        # Always use accurate xy_to_line_col
        ln, col = self.xy_to_line_col(x, y)
        
        # Check if clicking on selected text
        if self.buf.selection.has_selection():
            start_line, start_col, end_line, end_col = self.buf.selection.get_bounds()
            
            # Check if click is within selection
            click_in_selection = False
            if start_line == end_line:
                # Single line selection
                if ln == start_line and start_col <= col < end_col:
                    click_in_selection = True
            else:
                # Multi-line selection
                if ln == start_line and col >= start_col:
                    click_in_selection = True
                elif ln == end_line and col < end_col:
                    click_in_selection = True
                elif start_line < ln < end_line:
                    click_in_selection = True
            
            if click_in_selection:
                # We might be starting a drag, but wait for actual movement
                self._drag_pending = True
                # Don't set drag_and_drop_mode yet - wait for on_drag_update
                self.drag_and_drop_mode = False
                
                # Store the selected text (just in case)
                self.dragged_text = self.buf.get_selected_text()
                
                # Don't start normal selection drag - this preserves the selection
                # Don't call ctrl.start_drag() to keep selection visible
                return
        
        # Normal drag behavior
    
        # Check for Shift key (Extend Selection Drag)
        mods = g.get_current_event_state()
        shift_pressed = (mods & Gdk.ModifierType.SHIFT_MASK) != 0
        
        if shift_pressed:
            # Shift+Drag: Extend selection
            self.drag_and_drop_mode = False
            self._drag_pending = False
            self._pending_click = False
            self._pending_triple_click = False # Cancel triple click if dragging with shift
            
            # Manually set dragging state to allow update_drag to work
            # But DO NOT call ctrl.start_drag() because that clears the selection!
            self.ctrl.dragging = True
            self.ctrl.drag_start_line = ln
            self.ctrl.drag_start_col = col
            
            # Ensure we are NOT in word selection mode unless we were already
            if self.click_count <= 1:
                self.word_selection_mode = False
                
            self.queue_draw()
            return

        self.drag_and_drop_mode = False
        self._drag_pending = False
        self._pending_click = False  # We are dragging, so cancel pending click
        
        if self.word_selection_mode:
            # In word selection mode (after double-click), we want to KEEP the current selection
            # and just start dragging from here.
            # So we manually set dragging state without clearing selection via start_drag()
            self.ctrl.dragging = True
            self.ctrl.drag_start_line = ln
            self.ctrl.drag_start_col = col
        else:
            # Normal selection drag - starts new selection
            self.ctrl.start_drag(ln, col)
        
        # Clear word selection mode only if this is a single-click drag
        # (click_count will be 1 for single-click, 2+ for multi-click)
        if self.click_count <= 1:
            self.word_selection_mode = False
        
        self.queue_draw()




    def start_autoscroll(self):
        """Start the auto-scroll timer if not already running"""
        if self.autoscroll_timer_id is None:
            self.autoscroll_timer_id = GLib.timeout_add(50, self.autoscroll_tick)
    
    def stop_autoscroll(self):
        """Stop the auto-scroll timer"""
        if self.autoscroll_timer_id is not None:
            try:
                GLib.source_remove(self.autoscroll_timer_id)
            except Exception:
                pass
            self.autoscroll_timer_id = None
            
    def autoscroll_tick(self):
        """Called periodically during drag to perform auto-scrolling"""
        if not self.ctrl.dragging and not self.drag_and_drop_mode:
            self.stop_autoscroll()
            return False
            
        viewport_height = self.get_height()
        viewport_width = self.get_width()
        edge_size = 30
        scroll_amount = 0
        hscroll_amount = 0
        
        # Vertical scrolling calculation
        if self.last_drag_y < edge_size:
            scroll_amount = -max(1, int((edge_size - self.last_drag_y) / 10) + 1)
        elif self.last_drag_y > viewport_height - edge_size:
            scroll_amount = max(1, int((self.last_drag_y - (viewport_height - edge_size)) / 10) + 1)
            
        # Horizontal scrolling calculation (disabled if wrapped)
        if not self.mapper.enabled:
             # Approximate gutter width
             gutter = 50 
             if self.last_drag_x < gutter + edge_size:
                 hscroll_amount = -max(5, int((gutter + edge_size - self.last_drag_x)/5)+5)
             elif self.last_drag_x > viewport_width - edge_size:
                 hscroll_amount = max(5, int((self.last_drag_x - (viewport_width - edge_size))/5)+5)
        
        did_scroll = False
        
        if scroll_amount != 0:
            if self.mapper.enabled:
                # Scroll by visual lines
                if scroll_amount < 0:
                     # Up
                     steps = abs(scroll_amount)
                     for _ in range(steps):
                         if self.scroll_visual_offset > 0:
                             self.scroll_visual_offset -= 1
                         elif self.scroll_line > 0:
                             self.scroll_line -= 1
                             count = self.mapper.get_visual_line_count(self.scroll_line)
                             self.scroll_visual_offset = max(0, count - 1)
                         else:
                             break
                     did_scroll = True
                else:
                     # Down
                     steps = scroll_amount
                     total = self.buf.total()
                     for _ in range(steps):
                         count = self.mapper.get_visual_line_count(self.scroll_line)
                         if self.scroll_visual_offset < count - 1:
                             self.scroll_visual_offset += 1
                         elif self.scroll_line < total - 1:
                             self.scroll_line += 1
                             self.scroll_visual_offset = 0
                         else:
                             break
                     did_scroll = True
                
                if did_scroll: self.update_scrollbar()
            else:
                 # Logical
                 new_line = self.scroll_line + scroll_amount
                 new_line = max(0, min(new_line, self.buf.total() - 1))
                 if new_line != self.scroll_line:
                     self.scroll_line = new_line
                     did_scroll = True
                     self.update_scrollbar()
            
        if hscroll_amount != 0:
             self.scroll_x += hscroll_amount
             if self.scroll_x < 0: self.scroll_x = 0
             self.hadj.set_value(self.scroll_x)
             did_scroll = True
             
        if did_scroll:
             self.queue_draw()
             if self.ctrl.dragging:
                 self.ctrl.drag_to(self.last_drag_x, self.last_drag_y)
             elif self.drag_and_drop_mode:
                 # In DND, we don't call drag_to explicitly unless handling selection DND?
                 # But we need queue_draw.
                 pass
             return True
             
        return True
    def on_drag_update(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        # Check if we have a pending drag that needs to be activated
        if self._drag_pending:
            # We moved! Activate drag-and-drop mode
            self.drag_and_drop_mode = True
            self._drag_pending = False
            # Now we know it's a drag, so it's NOT a click-to-clear
            print("DEBUG: Drag Update. Mode Active. Clearing Pending Click.")
            self._clicked_in_selection = False
            self._pending_click = False # Cancel pending click
            self._pending_triple_click = False # failsafe
            self.queue_draw()

        # Store current drag position for auto-scroll
        self.last_drag_x = sx + dx
        self.last_drag_y = sy + dy
        
        # Check if we're near edges and start auto-scroll if needed
        viewport_height = self.get_height()
        viewport_width = self.get_width()
        edge_size = 30
        
        near_edge = (
            self.last_drag_y < edge_size or 
            self.last_drag_y > viewport_height - edge_size or
            (not self.renderer.wrap_enabled and (
                self.last_drag_x < edge_size or 
                self.last_drag_x > viewport_width - edge_size
            ))
        )
        
        if near_edge:
            self.start_autoscroll()
        else:
            self.stop_autoscroll()

        # Always use accurate xy_to_line_col - Pango's hit-testing is fast enough
        ln, col = self.xy_to_line_col(sx + dx, sy + dy)

        # In drag-and-drop mode, track drop position for visual feedback
        # Do NOT extend selection in drag-and-drop mode
        if self.drag_and_drop_mode:
            self.drop_position_line = ln
            self.drop_position_col = col
            
            # Check if Ctrl is pressed for copy vs move visual feedback
            event = g.get_current_event()
            if event:
                state = event.get_modifier_state()
                self.ctrl_pressed_during_drag = (state & Gdk.ModifierType.CONTROL_MASK) != 0
            
            self.queue_draw()
            return
        
        if self.word_selection_mode:
            # Word-by-word selection mode
            line_text = self.buf.get_line(ln)
            
            # Get selection anchor point (start of initially selected word)
            sel_start_line = self.buf.selection.start_line if self.buf.selection.active else ln
            sel_start_col = self.buf.selection.start_col if self.buf.selection.active else col
            
            # Also track the end of original selection to determine drag direction
            sel_end_line = self.buf.selection.end_line if self.buf.selection.active else ln
            sel_end_col = self.buf.selection.end_col if self.buf.selection.active else col
            
            # Handle empty lines
            if len(line_text) == 0:
                # Empty line - check if it's the last line (skip it)
                if ln == self.buf.total() - 1:
                    # Last empty line: don't extend to it, stay at previous position
                    return
                else:
                    # Empty line not at EOF: treat entire line as one "word"
                    # Use anchor points for direction detection (same as word selection)
                    anchor_start_line = self.anchor_word_start_line
                    anchor_start_col = self.anchor_word_start_col
                    anchor_end_line = self.anchor_word_end_line
                    anchor_end_col = self.anchor_word_end_col
                    
                    # Determine drag direction based on anchor
                    is_forward = False
                    if ln > anchor_start_line:
                        is_forward = True
                    elif ln == anchor_start_line and 0 >= anchor_start_col:
                        is_forward = True
                    
                    if is_forward:
                        # Dragging Forward: anchor at start, extend from empty line newline
                        self.buf.selection.set_start(anchor_start_line, anchor_start_col)
                        self.ctrl.update_drag(ln, 1)  # Select empty line + newline
                    else:
                        # Dragging Backward: anchor at end, extend to empty line start
                        self.buf.selection.set_start(anchor_end_line, anchor_end_col)
                        self.ctrl.update_drag(ln, 0)  # Select from start of empty line
            elif line_text and 0 <= col <= len(line_text):
                # Line with text: snap to word boundaries
                start_col, end_col = self.find_word_boundaries(line_text, min(col, len(line_text) - 1))
                
                # Use the ANCHOR word (originally double-clicked word) for direction detection
                # This prevents flickering by keeping the reference point stable
                anchor_start_line = self.anchor_word_start_line
                anchor_start_col = self.anchor_word_start_col
                anchor_end_line = self.anchor_word_end_line
                anchor_end_col = self.anchor_word_end_col
                
                # Compare current position with anchor word start to determine direction
                # If we are at or after the start of the anchor word, we treat it as a forward drag
                # (even if we are inside the anchor word itself)
                is_forward = False
                if ln > anchor_start_line:
                    is_forward = True
                elif ln == anchor_start_line and col >= anchor_start_col:
                    is_forward = True
                
                if is_forward:
                    # Dragging Forward (LTR):
                    # Anchor point should be the START of the original word
                    self.buf.selection.set_start(anchor_start_line, anchor_start_col)
                    # Cursor (end point) should be the END of the current word
                    self.ctrl.update_drag(ln, end_col)
                else:
                    # Dragging Backward (RTL):
                    # Anchor point should be the END of the original word
                    self.buf.selection.set_start(anchor_end_line, anchor_end_col)
                    # Cursor (end point) should be the START of the current word
                    self.ctrl.update_drag(ln, start_col)
            else:
                # Beyond text
                self.ctrl.update_drag(ln, col)
        else:
            # Normal character-by-character selection
            self.ctrl.update_drag(ln, col)
        
        self.queue_draw()


    # --------------------------------------------------------
    # Busy Indicator Control
    # --------------------------------------------------------
    def show_busy(self, message="Processing..."):
        """Show the busy overlay with a message."""
        if self._busy_overlay:
            self._busy_label.set_text(message)
            self._busy_spinner.start()
            self._busy_overlay.set_visible(True)
            # Force UI update if possible, though usually handled by loop return
            
    def hide_busy(self):
        """Hide the busy overlay."""
        if self._busy_overlay:
            self._busy_spinner.stop()
            self._busy_overlay.set_visible(False)

    def on_click_released(self, g, n, x, y):
        print(f"DEBUG: Released. PendingClick={self._pending_click}. PendingTriple={self._pending_triple_click}")
        if self._pending_click:
            print("DEBUG: Executing Pending Click (Clear/Move)")
            self.ctrl.click(self._click_ln, self._click_col)
        self._pending_click = False
        
        if self._pending_triple_click:
            # Execute deferred triple click
            ln = self._triple_click_ln
            line_len = self._triple_click_line_len
            self.buf.selection.set_start(ln, 0)
            self.buf.selection.set_end(ln, line_len)
            self.buf.cursor_line = ln
            self.buf.cursor_col = line_len
            self._pending_triple_click = False
            self.queue_draw()
            
        self.queue_draw()

    def on_drag_end(self, g, dx, dy):
        # Stop auto-scrolling
        self.stop_autoscroll()
        
        # If we clicked in selection but didn't actually drag (drag_and_drop_mode wasn't set),
        # then we should clear the selection now
        if self._clicked_in_selection and not self.drag_and_drop_mode:
            self.buf.selection.clear()
            self._clicked_in_selection = False
            self.queue_draw()
            
        self._drag_pending = False
        
        if self.drag_and_drop_mode:
            # Drag-and-drop mode: move or copy text
            ok, sx, sy = g.get_start_point()
            if ok:
                # Always use accurate xy_to_line_col
                drop_ln, drop_col = self.xy_to_line_col(sx + dx, sy + dy)
                
                # Get current event to check for Ctrl key
                event = g.get_current_event()
                ctrl_pressed = False
                if event:
                    state = event.get_modifier_state()
                    ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
                
                # Get original selection bounds
                bounds = self.buf.selection.get_bounds()
                if not bounds or bounds[0] is None:
                    # No valid selection, exit drag mode
                    self.drag_and_drop_mode = False
                    self.dragged_text = ""
                    self.queue_draw()
                    return
                
                start_line, start_col, end_line, end_col = bounds
                
                # Check if dropping inside the original selection (no-op)
                drop_in_selection = False
                if start_line == end_line:
                    if drop_ln == start_line and start_col <= drop_col <= end_col:
                        drop_in_selection = True
                else:
                    if drop_ln == start_line and drop_col >= start_col:
                        drop_in_selection = True
                    elif drop_ln == end_line and drop_col <= end_col:
                        drop_in_selection = True
                    elif start_line < drop_ln < end_line:
                        drop_in_selection = True
                
                if not drop_in_selection and self.dragged_text:
                    if ctrl_pressed:
                        # Copy: insert at drop position, keep original
                        self.buf.set_cursor(drop_ln, drop_col)
                        self.buf.insert_text(self.dragged_text)
                    else:
                        # Move: delete original, insert at drop position
                        # Delete first
                        self.buf.delete_selection()
                        # Recalculate drop position if it's after the deleted text
                        if drop_ln > end_line or (drop_ln == end_line and drop_col > end_col):
                            # Adjust for deleted text
                            if start_line == end_line:
                                # Single line deletion
                                chars_deleted = end_col - start_col
                                if drop_ln == start_line:
                                    drop_col -= chars_deleted
                            else:
                                # Multi-line deletion
                                lines_deleted = end_line - start_line
                                if drop_ln > end_line:
                                    drop_ln -= lines_deleted
                        
                        # Insert at adjusted position
                        self.buf.set_cursor(drop_ln, drop_col)
                        self.buf.insert_text(self.dragged_text)
                    
                    # Select the inserted text (Move/Copy should leave text selected)
                    lines = self.dragged_text.split('\n')
                    new_lines_count = len(lines) - 1
                    select_start_ln = drop_ln
                    select_start_col = drop_col
                    
                    if new_lines_count == 0:
                         select_end_ln = drop_ln
                         select_end_col = drop_col + len(lines[0])
                    else:
                         select_end_ln = drop_ln + new_lines_count
                         select_end_col = len(lines[-1])
                    
                    self.buf.selection.set_start(select_start_ln, select_start_col)
                    self.buf.selection.set_end(select_end_ln, select_end_col)
                    self.buf.cursor_line = select_end_ln
                    self.buf.cursor_col = select_end_col
                    print(f"DEBUG: Drag End. Selected text: {select_start_ln},{select_start_col} - {select_end_ln},{select_end_col}")
                
                self.keep_cursor_visible()
            
            # Exit drag-and-drop mode
            self.drag_and_drop_mode = False
            self.dragged_text = ""
        else:
            # Normal drag end
            self.ctrl.end_drag()
            
            # Copy selection to PRIMARY clipboard for middle-click paste
            if self.buf.selection.has_selection():
                start_ln, start_col, end_ln, end_col = self.buf.selection.get_bounds()
                
                # Extract selected text
                if start_ln == end_ln:
                    # Single line selection
                    line = self.buf.get_line(start_ln)
                    selected_text = line[start_col:end_col]
                else:
                    # Multi-line selection
                    lines = []
                    for ln in range(start_ln, end_ln + 1):
                        line = self.buf.get_line(ln)
                        if ln == start_ln:
                            lines.append(line[start_col:])
                        elif ln == end_ln:
                            lines.append(line[:end_col])
                        else:
                            lines.append(line)
                    selected_text = '\n'.join(lines)
                
                # Copy to PRIMARY clipboard
                if selected_text:
                    display = self.get_display()
                    clipboard = display.get_primary_clipboard()
                    clipboard.set(selected_text)
        
        # Clear word selection mode
        self.word_selection_mode = False
        
        self.queue_draw()


    def update_matching_brackets(self):
        """Find matching brackets and update highlight state."""
        if not getattr(self, 'highlight_brackets', True):
            self.matching_brackets = []
            return

        cursor_ln = self.buf.cursor_line
        cursor_col = self.buf.cursor_col
        line_text = self.buf.get_line(cursor_ln)
        
        matches = []
        
        # Check char at cursor (or char before cursor if at end of line/word)
        chars_to_check = []
        if cursor_col < len(line_text):
            chars_to_check.append((cursor_ln, cursor_col, line_text[cursor_col]))
        if cursor_col > 0 and (cursor_col - 1) < len(line_text):
            chars_to_check.append((cursor_ln, cursor_col - 1, line_text[cursor_col - 1]))
            
        params = {
            '(': (')', 1), ')': ('(', -1),
            '[': (']', 1), ']': ('[', -1),
            '{': ('}', 1), '}': ('{', -1),
            '<': ('>', 1), '>': ('<', -1)
        }
        
        for ln, col, char in chars_to_check:
            if char in params:
                target, direction = params[char]
                start_match = (ln, col)
                
                # Scan
                depth = 1
                curr_ln = ln
                curr_col = col + direction
                
                total_lines = self.buf.total()
                scan_limit = 2000
                lines_scanned = 0
                
                while 0 <= curr_ln < total_lines and lines_scanned < scan_limit:
                    text = self.buf.get_line(curr_ln)
                    
                    if direction == 1:
                        start_c = curr_col if curr_ln == ln else 0
                        range_iter = range(start_c, len(text))
                    else:
                        start_c = curr_col if curr_ln == ln else len(text) - 1
                        range_iter = range(start_c, -1, -1)
                        
                    for c_idx in range_iter:
                        c = text[c_idx]
                        if c == char:
                            depth += 1
                        elif c == target:
                            depth -= 1
                            if depth == 0:
                                matches = [start_match, (curr_ln, c_idx)]
                                break
                    
                    if matches: break
                        
                    curr_ln += direction
                    lines_scanned += 1
                
                if matches: break
        
        self.matching_brackets = matches
        if matches:
            self.queue_draw()

    # Compatibility methods for legacy renderer users
    @property
    def wrap_enabled(self):
        return self.mapper.enabled
        
    @wrap_enabled.setter
    def wrap_enabled(self, value):
        self.mapper.enabled = value
        self.mapper.invalidate_all()
        self.highlight_cache = {} # Clear highlight cache as positions change
        self.update_metrics()
        self.update_scrollbar() # Force scrollbar update
        self.queue_draw()
        
    def set_font(self, font_desc):
        self.font_desc = font_desc
        self.update_metrics()
        self.queue_draw()
        
    def hex_to_rgba_floats(self, hex_str, alpha=1.0):
        hex_str = hex_str.lstrip('#')
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return r, g, b, alpha

    def update_colors_for_theme(self, is_dark=None):
        """Update colors based on current theme (GTK4)."""
        
        # Determine theme mode unless explicitly given
        if is_dark is None:
            style_manager = Adw.StyleManager.get_default()
            is_dark = style_manager.get_dark()
            
        self.is_dark = is_dark
        self.syntax.set_theme("dark" if is_dark else "light")
        
        # Use theme-appropriate background colors
        if is_dark:
            r, g, b, a = self.hex_to_rgba_floats("#191919")
            self.editor_background_color = (r, g, b, a)
            self.current_line_background_color = (1.0, 1.0, 1.0, 0.05) # 8% white tint for dark theme
        else:
            r, g, b, a = self.hex_to_rgba_floats("#fafafa")
            self.editor_background_color = (r, g, b, a)
            self.current_line_background_color = (0.0, 0.0, 0.0, 0.05) # 6% black tint for light theme

        # Helper for Pango colors
        def hex_to_pango(hex_str):
            r, g, b, a = self.hex_to_rgba_floats(hex_str)
            return (r, g, b)

        if is_dark:
            self.text_foreground_color = (0.90, 0.90, 0.90)
            self.linenumber_foreground_color = (0.60, 0.60, 0.60)
            self.selection_background_color = (0.2, 0.4, 0.6)
            self.selection_foreground_color = (1.0, 1.0, 1.0)
            
            # Syntax Colors (Atom One Dark)
            self.syntax_colors = {
                'keywords': hex_to_pango("#c678dd"),     # Purple
                'builtins': hex_to_pango("#56b6c2"),     # Cyan
                'string': hex_to_pango("#98c379"),       # Green
                'comment': hex_to_pango("#5c6370"),      # Grey
                'number': hex_to_pango("#d19a66"),       # Orange
                'function': hex_to_pango("#61afef"),     # Blue
                'class': hex_to_pango("#e5c07b"),        # Yellow/Gold
                'decorator': hex_to_pango("#56b6c2"),    # Cyan
                'personal': hex_to_pango("#e06c75"),     # Red
                'tag': hex_to_pango("#e06c75"),          # Red
                'attribute': hex_to_pango("#d19a66"),    # Orange
                'property': hex_to_pango("#56b6c2"),     # Cyan
                'selector': hex_to_pango("#c678dd"),     # Purple
                'macro': hex_to_pango("#e5c07b"),        # Yellow
                'preprocessor': hex_to_pango("#c678dd"), # Purple
                'types': hex_to_pango("#56b6c2"),        # Cyan
                'entity': hex_to_pango("#d19a66"),       # Orange
                'bool_ops': hex_to_pango("#d19a66"),     # Orange
                'brackets': hex_to_pango("#c678dd"),     # Pink (Changed from Orange)
                'raw_prefix': hex_to_pango("#c678dd"),   # Pink/Purple
                'operators': hex_to_pango("#c678dd"),    # Pink/Purple
                'docstring': hex_to_pango("#98c379"),    # Green
                'helpers': hex_to_pango("#e06c75"),     # Red
                'argument': hex_to_pango("#d19a66"),     # Orange (New)
                'byte_string': hex_to_pango("#56b6c2"),  # Cyan
                'raw_string': hex_to_pango("#98c379"),   # Green
                'f_string': hex_to_pango("#98c379"),     # Green
                'string': hex_to_pango("#98c379"),       # Green

                # String Delimiters
                'triple_start': hex_to_pango("#98c379"),
                'string_start': hex_to_pango("#98c379"),
                'f_triple_start': hex_to_pango("#98c379"),
                'f_string_start': hex_to_pango("#98c379"),
                'b_triple_start': hex_to_pango("#56b6c2"),
                'b_string_start': hex_to_pango("#56b6c2"),
                'r_triple_start': hex_to_pango("#98c379"),
                'r_string_start': hex_to_pango("#98c379"),
                'u_triple_start': hex_to_pango("#98c379"),
                'u_string_start': hex_to_pango("#98c379"),

                'byte_string_content': hex_to_pango("#56b6c2"), 
                'raw_string_content': hex_to_pango("#98c379"),
                'f_string_content': hex_to_pango("#98c379"),
                'string_content': hex_to_pango("#98c379"),
                
                # DSL-specific colors (minimal - just muted tags)
                'header': hex_to_pango("#c678dd"),          # Purple for header directives
                'tag_bracket': hex_to_pango("#5c6370"),     # Muted grey
                'color_tag': hex_to_pango("#5c6370"),       # Muted grey
                'attr_tag': hex_to_pango("#5c6370"),        # Muted grey
                'phonetic': hex_to_pango("#5c6370"),        # Muted grey
                'pos_label': hex_to_pango("#5c6370"),       # Muted grey
                'zone': hex_to_pango("#5c6370"),            # Muted grey
                'stress': hex_to_pango("#5c6370"),          # Muted grey
                'link': hex_to_pango("#5c6370"),            # Muted grey
                'color_name': hex_to_pango("#5c6370"),      # Muted grey
                'file_ref': hex_to_pango("#5c6370"),        # Muted grey
                'escape': hex_to_pango("#5c6370"),          # Muted grey
                'tilde': hex_to_pango("#5c6370"),           # Muted grey
                'at_sign': hex_to_pango("#5c6370"),         # Muted grey
            }
        else:
            self.text_foreground_color = (0.2, 0.2, 0.2)
            self.linenumber_foreground_color = (0.6, 0.6, 0.6)
            self.selection_background_color = (0.8, 0.9, 1.0)
            self.selection_foreground_color = (0.0, 0.0, 0.0)
            
            # Syntax Colors (Atom One Light)
            self.syntax_colors = {
                'keywords': hex_to_pango("#a626a4"),
                'builtins': hex_to_pango("#0184bc"),
                'string': hex_to_pango("#50a14f"),
                'comment': hex_to_pango("#a0a1a7"),
                'number': hex_to_pango("#986801"),
                'function': hex_to_pango("#4078f2"),
                'class': hex_to_pango("#c18401"),
                'decorator': hex_to_pango("#a626a4"),
                'personal': hex_to_pango("#e45649"),
                'tag': hex_to_pango("#e45649"),
                'attribute': hex_to_pango("#986801"),
                'property': hex_to_pango("#0184bc"),
                'selector': hex_to_pango("#a626a4"),
                'macro': hex_to_pango("#c18401"),
                'preprocessor': hex_to_pango("#a626a4"),
                'types': hex_to_pango("#0184bc"),
                'entity': hex_to_pango("#986801"),
                'bool_ops': hex_to_pango("#986801"),
                'brackets': hex_to_pango("#986801"),
                'operators': hex_to_pango("#0184bc"),
                'regex': hex_to_pango("#50a14f"),
                'namespace': hex_to_pango("#c18401"),
                'special': hex_to_pango("#0184bc"),
                'file_ref': hex_to_pango("#a0a1a7"),        # Muted grey
                'escape': hex_to_pango("#a0a1a7"),          # Muted grey
                'tilde': hex_to_pango("#a0a1a7"),           # Muted grey
                'at_sign': hex_to_pango("#a0a1a7"),         # Muted grey
                'docstring': hex_to_pango("#50a14f"),    # Green
                'helpers': hex_to_pango("#e45649"),     # Red
                'argument': hex_to_pango("#986801"),     # Orange (New)
                'byte_string': hex_to_pango("#0184bc"),  # Cyan
                'raw_string': hex_to_pango("#50a14f"),   # Green
                'f_string': hex_to_pango("#50a14f"),     # Green
                'string': hex_to_pango("#50a14f"),       # Green
                'raw_prefix': hex_to_pango("#a626a4"),   # Pink

                # String Delimiters
                'triple_start': hex_to_pango("#50a14f"),
                'string_start': hex_to_pango("#50a14f"),
                'f_triple_start': hex_to_pango("#50a14f"),
                'f_string_start': hex_to_pango("#50a14f"),
                'b_triple_start': hex_to_pango("#0184bc"),
                'b_string_start': hex_to_pango("#0184bc"),
                'r_triple_start': hex_to_pango("#50a14f"),
                'r_string_start': hex_to_pango("#50a14f"),
                'u_triple_start': hex_to_pango("#50a14f"),
                'u_string_start': hex_to_pango("#50a14f"),

                'byte_string_content': hex_to_pango("#0184bc"), 
                'raw_string_content': hex_to_pango("#50a14f"),
                'f_string_content': hex_to_pango("#50a14f"),
                'string_content': hex_to_pango("#50a14f"),
            }
        
        self.queue_draw()


    def keep_cursor_visible(self):
        """Keep cursor visible by scrolling if necessary."""
        self.update_matching_brackets()
        
        cl = self.buf.cursor_line
        cc = self.buf.cursor_col
        
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0: return

        visible_lines = max(1, height // self.line_h)
        
        if self.mapper.enabled:
            # Get visual offset for connection
            vis_off, _ = self.mapper.column_to_visual_offset(cl, cc)
            
            # Simple scrolling logic
            if cl < self.scroll_line:
                self.scroll_line = cl
                self.scroll_visual_offset = vis_off
                self.scroll_line_frac = 0.0 # Reset fractional scroll
                self.queue_draw()
                self.update_scrollbar()
                return

            if cl == self.scroll_line:
                if vis_off < self.scroll_visual_offset:
                     self.scroll_visual_offset = vis_off
                     self.queue_draw()
                     self.update_scrollbar()
                     return
            
            # Check bottom visibility logic (approximation for speed)
            # If current line is far below scroll line (> visible_lines), definitely scroll
            if cl > self.scroll_line + visible_lines + 5:
                diff = cl - (self.scroll_line + visible_lines) + 5
                self.scroll_line += diff
                self.scroll_visual_offset = 0 # reset offset
                self.scroll_line_frac = 0.0 # Reset fractional scroll
                self.queue_draw()
                self.update_scrollbar()
                # Refine
                self.keep_cursor_visible() 
                return

            # Precise check by iterating visual lines
            ln = self.scroll_line
            curr_vis = 0
            
            found = False
            
            while ln <= cl:
                 start_off = 0
                 if ln == self.scroll_line: start_off = self.scroll_visual_offset
                 
                 lines_in_ln = self.mapper.get_visual_line_count(ln)
                 
                 if ln == cl:
                     curr_vis += (vis_off - start_off)
                     found = True
                     break
                 
                 curr_vis += (lines_in_ln - start_off)
                 ln += 1
            
            if found:
                 if curr_vis >= visible_lines:
                     # Need to scroll down
                     # We need to advance scroll_line until curr_vis < visible_lines
                     # Simplest approach: set scroll to put cursor at bottom
                     # But accurately calculating THAT is hard without reverse iteration.
                     # So let's just increment scroll line by 1 until visible?
                     # Slow but robust.
                     while curr_vis >= visible_lines:
                         # Advance scroll
                         s_inc = 1
                         # If we are in middle of line?
                         # Just resetting visual offset or advancing line
                         s_lines = self.mapper.get_visual_line_count(self.scroll_line)
                         rem = s_lines - self.scroll_visual_offset
                         
                         if curr_vis - rem < visible_lines:
                             # Advancing past this line fixes it?
                             # We can just advance scroll_visual_offset
                             needed = curr_vis - (visible_lines - 1)
                             if needed < rem:
                                 self.scroll_visual_offset += needed
                                 curr_vis -= needed
                             else:
                                 self.scroll_visual_offset = 0
                                 self.scroll_line += 1
                                 curr_vis -= rem
                         else:
                             self.scroll_visual_offset = 0
                             self.scroll_line += 1
                             curr_vis -= rem
                             
                     self.scroll_line_frac = 0.0 # Reset fractional scroll
                     self.queue_draw()
                     self.update_scrollbar()

        else:
            # No wrap
            if cl < self.scroll_line:
                self.scroll_line = cl
                self.scroll_visual_offset = 0
                self.scroll_line_frac = 0.0
            elif cl >= self.scroll_line + visible_lines:
                self.scroll_line = max(0, cl - visible_lines + 1)
                self.scroll_visual_offset = 0
                self.scroll_line_frac = 0.0
                
            # Horizontal scrolling
            ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
            
            # Calculate cursor X position
            line_text = self.buf.get_line(cl)
            
            # Precise calculation using Pango
            # We create a temporary layout just like in draw_view/hit_test
            # This ensures we account for variable char widths, tabs, etc.
            temp_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 1, 1)
            temp_cr = cairo.Context(temp_surface)
            layout = self.create_text_layout(temp_cr, line_text)
            
            b_idx = self.visual_byte_index(line_text, cc)
            pos = layout.get_cursor_pos(b_idx)[0]
            cursor_x_precise = pos.x / Pango.SCALE
            
            # Use precise value everywhere
            cursor_x_approx = cursor_x_precise
            
            # Fix oscillation: immediately update max_line_width if we found a longer line
            # This prevents update_scrollbar from clamping us back if it thinks content is smaller
            if hasattr(self, 'max_line_width'):
                 self.max_line_width = max(self.max_line_width, cursor_x_precise)
            
            visible_w = width - ln_width
            scrolled_x = self.scroll_x
            
            # Left edge
            if cursor_x_approx < scrolled_x + 10: # Add 10px safe margin for left edge visibility
                self.scroll_x = int(max(0, cursor_x_approx - 20))
                self.queue_draw()
                self.update_scrollbar()
            # Right edge
            else:
                 padding = 20 if self.vscroll.get_visible() else 10
                 visible_w = width - ln_width - padding
                 # Check right edge with 10px margin to ensure cursor (1-2px) is strictly visible
                 if cursor_x_approx + 10 > scrolled_x + visible_w:
                     self.scroll_x = int(cursor_x_approx - visible_w + 20)
                     self.queue_draw()
                     self.update_scrollbar()
            
            # Precise Pango fallback calculation could go here if needed

    def install_scroll(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        """Handle mouse wheel scroll with pixel-wise precision."""
        if dy:
             # Calculate total visual lines (including wrapped lines)
             total_lines = self.buf.total()
             h = self.get_height()
             visible_lines = max(1, int(h / self.line_h)) if self.line_h > 0 else 1
             
             # Calculate total visual lines (accounting for word wrap)
             total_visual_lines = 0
             for i in range(total_lines):
                 segments = self.mapper.get_line_segments(i)
                 total_visual_lines += len(segments)
             
             # If all content fits in viewport, don't allow scrolling
             if total_visual_lines <= visible_lines:
                 # Reset scroll position to top
                 self.scroll_line = 0
                 self.scroll_visual_offset = 0
                 if hasattr(self, 'scroll_line_frac'):
                     self.scroll_line_frac = 0.0
                 return True
             
             # dy is typically 1.0 per click, or smaller for touchpads
             # We want smooth scrolling, so we map dy to a fraction of a line
             # Sensitivity: 1.0 dy = 3 lines roughly in old code
             # Let's try 1.0 dy = 1.0 lines (or adjustable)
             # For pixel-wise, we want direct mapping if possible, but dy is unit-less steps usually.
             
             scroll_speed = 3.0  # lines per scroll unit
             
             if not hasattr(self, 'scroll_line_frac'):
                 self.scroll_line_frac = 0.0
                 
             self.scroll_line_frac += dy * scroll_speed
             
             
             # Normalize fraction to keep it between 0.0 and 1.0 by moving visual lines
             while self.scroll_line_frac >= 1.0:
                 # Move down 1 visual line
                 segments = self.mapper.get_line_segments(self.scroll_line)
                 if self.scroll_visual_offset < len(segments) - 1:
                     self.scroll_visual_offset += 1
                     self.scroll_line_frac -= 1.0
                 else:
                     if self.scroll_line < total_lines - 1:
                         self.scroll_line += 1
                         self.scroll_visual_offset = 0
                         self.scroll_line_frac -= 1.0
                     else:
                         # End of file, clamp
                         self.scroll_line_frac = min(1.0, self.scroll_line_frac) # Allow some bounce? No.
                         self.scroll_line_frac = 0.99 
                         break
                         
             while self.scroll_line_frac < 0.0:
                 # Move up 1 visual line
                 if self.scroll_visual_offset > 0:
                     self.scroll_visual_offset -= 1
                     self.scroll_line_frac += 1.0
                 else:
                     if self.scroll_line > 0:
                         self.scroll_line -= 1
                         segments = self.mapper.get_line_segments(self.scroll_line)
                         self.scroll_visual_offset = max(0, len(segments) - 1)
                         self.scroll_line_frac += 1.0
                     else:
                         # Start of file, clamp
                         self.scroll_line_frac = 0.0
                         break
             
             self.update_scrollbar()
             self.queue_draw()
             
        if dx and not self.mapper.enabled:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))
            self.queue_draw()

        return True
    def visual_byte_index(self, text, col):
        """Convert character index to byte index."""
        # Optimization: Slicing + len(encode) is faster in Python than looping char by char
        return len(text[:col].encode("utf-8"))

    def get_color_for_token(self, token_type):
        """Get color for syntax token type."""
        # Use pre-calculated syntax colors map
        if hasattr(self, 'syntax_colors'):
            # Check direct match
            if token_type in self.syntax_colors:
                return self.syntax_colors[token_type]
            
            # Fallback for string variants
            # Fallback for string variants
            if 'string' in token_type:
                return self.syntax_colors.get('string')
            
            # Fallback for other variants (e.g. function_definition -> function)
            # This handles cases where mapping might be missed or subtle differences
            if 'function' in token_type: return self.syntax_colors.get('function')
            if 'class' in token_type: return self.syntax_colors.get('class')
            if 'keyword' in token_type: return self.syntax_colors.get('keywords')
                
        return None


    def _process_syntax_queue(self):
        """Idle callback to process syntax highlighting."""
        import time
        start_t = time.time()
        
        if not self.syntax_queue:
            self.syntax_idle_id = None
            return False
            
        # Sort by proximity to center of screen (approx scroll_line)
        # to prioritize visible area
        center = self.scroll_line + (self.get_height() // self.line_h // 2)
        sorted_q = sorted(list(self.syntax_queue), key=lambda x: abs(x - center))
        
        to_remove = set()
        processed = 0
        budget = 0.010 # 10ms budget
        
        for ln in sorted_q:
            if (time.time() - start_t) > budget:
                break
                
            if 0 <= ln < self.buf.total():
                self.syntax.tokenize(ln, self.buf.get_line(ln))
            
            to_remove.add(ln)
            processed += 1
            
        self.syntax_queue -= to_remove
        
        if processed > 0:
            self.queue_draw()
            
        if not self.syntax_queue:
            self.syntax_idle_id = None
            return False
            
        return True

    def draw_view(self, area, cr, w, h):
        import time
        draw_start = time.time()

        is_dark = getattr(self, 'is_dark', True)
        # Use the properly configured editor background color
        bg = getattr(self, 'editor_background_color', (0.1, 0.1, 0.1, 1.0))
        cr.set_source_rgba(*bg)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        show_cursor = (
            self.cursor_visible
            and not self.buf.selection.has_selection()
            and self.has_focus()
        )

        cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(11)

        # --- gutter ---
        if self.show_line_numbers:
            ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)
        else:
            ln_width = 0

        padding = 20 if self.vscroll.get_visible() else 10
        viewport_w = w - ln_width - padding
        self.mapper.set_viewport_width(viewport_w, self.char_width)

        visible_lines = int(h / self.line_h) + 2

        current_y = -int(getattr(self, "scroll_line_frac", 0) * self.line_h)
        current_log_line = self.scroll_line
        start_vis_offset = self.scroll_visual_offset

        layout = self.create_text_layout(cr, "")

        sel_start_ln = sel_start_col = sel_end_ln = sel_end_col = -1
        if self.buf.selection.has_selection():
            sel_start_ln, sel_start_col, sel_end_ln, sel_end_col = (
                self.buf.selection.get_bounds()
            )

        visual_lines_drawn = 0
        total_lines = self.buf.total()
        max_line_px = 0

        while visual_lines_drawn < visible_lines and current_log_line < total_lines:
            line_text = self.buf.get_line(current_log_line)
            segments = self.mapper.get_line_segments(current_log_line)

            tokens = self.syntax.get_cached(current_log_line)
            if tokens is None:
                if isinstance(self.syntax, StateAwareSyntaxEngine):
                    tokens = self.syntax.tokenize(current_log_line, line_text)
                else:
                    tokens = []

            if current_log_line == self.scroll_line and start_vis_offset >= len(segments):
                start_vis_offset = max(0, len(segments) - 1)
                self.scroll_visual_offset = start_vis_offset

            for i, (seg_start, seg_end) in enumerate(segments):
                if current_log_line == self.scroll_line and i < start_vis_offset:
                    continue
                if visual_lines_drawn >= visible_lines:
                    break

                seg_text = line_text[seg_start:seg_end]
                layout.set_text(seg_text, -1)

                if not self.mapper.enabled:
                    _, logical = layout.get_extents()
                    max_line_px = max(max_line_px, logical.width / Pango.SCALE)

                # ---- line numbers (NO CLIP) ----
                if i == 0 and self.show_line_numbers:
                    cr.set_source_rgb(0.5, 0.5, 0.5)
                    txt = str(current_log_line + 1)
                    cr.move_to(
                        ln_width - len(txt) * self.char_width - 5,
                        current_y + self.line_h - 5,
                    )
                    cr.show_text(txt)

                base_x = ln_width + 2 - self.scroll_x

                # ---- current line highlight ----
                if self.highlight_current_line and current_log_line == self.buf.cursor_line:
                    cr.set_source_rgba(*self.current_line_background_color)
                    cr.rectangle(0, current_y, w, self.line_h)
                    cr.fill()

                # ---- selection background ----
                if sel_start_ln != -1:
                    seg_sel_start = seg_sel_end = -1
                    if sel_start_ln < current_log_line < sel_end_ln:
                        seg_sel_start, seg_sel_end = 0, len(seg_text)
                    elif current_log_line == sel_start_ln:
                        s = max(sel_start_col, seg_start)
                        if sel_start_ln == sel_end_ln:
                            e = min(sel_end_col, seg_end)
                        else:
                            e = seg_end
                        if s < e:
                            seg_sel_start, seg_sel_end = s - seg_start, e - seg_start
                    elif current_log_line == sel_end_ln:
                        e = min(sel_end_col, seg_end)
                        if e > seg_start:
                            seg_sel_start, seg_sel_end = 0, e - seg_start

                    if seg_sel_start != -1:
                        idx_s = self.visual_byte_index(seg_text, seg_sel_start)
                        idx_e = self.visual_byte_index(seg_text, seg_sel_end)
                        line0 = layout.get_line(0)
                        if line0:
                            x1 = base_x + line0.index_to_x(idx_s, False) / Pango.SCALE
                            x2 = base_x + line0.index_to_x(idx_e, False) / Pango.SCALE
                            cr.set_source_rgba(0.2, 0.4, 0.6, 0.4)
                            cr.rectangle(x1, current_y, x2 - x1, self.line_h)
                            cr.fill()

                # ---- search highlights (FIXED) ----
                # ---- search highlights (FIXED) ----
                if self.search_matches and self.max_match_length > 0:
                    try:
                        search_start = max(0, seg_start - self.max_match_length)
                        idx = bisect.bisect_left(
                            self.search_matches,
                            (current_log_line, search_start, 0, 0),
                        )
                        for mi in range(idx, len(self.search_matches)):
                            m = self.search_matches[mi]
                            s_ln, s_col, e_ln, e_col = m[:4]
                            if s_ln > current_log_line:
                                break
                            if s_ln == current_log_line and s_col >= seg_end:
                                break
                            if not (s_ln <= current_log_line <= e_ln):
                                continue

                            ms = s_col if s_ln == current_log_line else 0
                            me = e_col if e_ln == current_log_line else seg_end
                            isect_s = max(ms, seg_start)
                            isect_e = min(me, seg_end)
                            if isect_s >= isect_e:
                                continue

                            rel_s = isect_s - seg_start
                            rel_e = isect_e - seg_start
                            b_s = self.visual_byte_index(seg_text, rel_s)
                            b_e = self.visual_byte_index(seg_text, rel_e)

                            pos_s = layout.get_cursor_pos(b_s)[0]
                            pos_e = layout.get_cursor_pos(b_e)[0]

                            x1 = base_x + pos_s.x / Pango.SCALE
                            x2 = base_x + pos_e.x / Pango.SCALE

                            color = (1.0, 0.5, 0.0, 0.6) if self.current_match_idx == mi else (1.0, 1.0, 0.0, 0.4)
                            cr.set_source_rgba(*color)
                            
                            cr.rectangle(x1, current_y, x2 - x1, self.line_h)
                            cr.fill()
                    except Exception as e:
                        # Log error but don't crash drawing (causes blank screen)
                        # print(f"Search highlight draw error: {e}")
                        pass

                # ---- syntax ----
                attr_list = Pango.AttrList()
                for s, e, tag in tokens:
                    ts, te = max(s, seg_start), min(e, seg_end)
                    if ts < te:
                        rs, re = ts - seg_start, te - seg_start
                        bs = self.visual_byte_index(seg_text, rs)
                        be = self.visual_byte_index(seg_text, re)
                        color = self.get_color_for_token(tag)
                        if color:
                            attr = Pango.attr_foreground_new(
                                int(color[0] * 65535),
                                int(color[1] * 65535),
                                int(color[2] * 65535),
                            )
                            attr.start_index = bs
                            attr.end_index = be
                            attr_list.insert(attr)

                layout.set_attributes(attr_list)

                # ---- text draw (single clip, correct) ----
                cr.save()
                cr.rectangle(ln_width, current_y, viewport_w, self.line_h)
                cr.clip()
                cr.move_to(base_x, current_y)
                # Use the properly configured text foreground color
                fg = getattr(self, 'text_foreground_color', (0.9, 0.9, 0.9))
                cr.set_source_rgb(*fg)
                PangoCairo.show_layout(cr, layout)
                cr.restore()

                layout.set_attributes(None)

                # ---- cursor ----
                if show_cursor and current_log_line == self.buf.cursor_line:
                    # Fix double cursor: only draw if cursor is strictly inside segment range,
                    # OR if it's at the end of the segment AND this is the last segment of the line.
                    # e.g. "abc def" -> segments "abc ", "def" (wrap)
                    # cursor at 4 ('d') -> in segment 2.
                    # cursor at 3 (' ') -> end of segment 1.
                    is_in_segment = (seg_start <= self.buf.cursor_col < seg_end)
                    is_at_end_of_last_segment = (self.buf.cursor_col == seg_end and i == len(segments) - 1)
                    
                    if is_in_segment or is_at_end_of_last_segment:
                        rel = self.buf.cursor_col - seg_start
                        b = self.visual_byte_index(seg_text, rel)
                        pos = layout.get_cursor_pos(b)[0]
                        cx = base_x + pos.x / Pango.SCALE
                        
                        # Use theme-appropriate cursor color
                        if is_dark:
                            cursor_r, cursor_g, cursor_b = 1.0, 1.0, 1.0  # White for dark theme
                        else:
                            cursor_r, cursor_g, cursor_b = 0.0, 0.0, 0.0  # Black for light theme
                        
                        cr.set_source_rgba(
                            cursor_r, cursor_g, cursor_b,
                            self.cursor_phase if self.cursor_phase <= 1 else 2 - self.cursor_phase
                        )

                        cr.rectangle(
                            cx,            # ← FLOAT, DO NOT ROUND
                            current_y,
                            1,             # 1px width is fine
                            self.line_h
                        )
                        cr.fill()


                current_y += self.line_h
                visual_lines_drawn += 1

            current_log_line += 1

        self.max_line_width = 0 if self.mapper.enabled else max_line_px

        if self.needs_scrollbar_init and w > 0 and h > 0:
            self.needs_scrollbar_init = False
            GLib.idle_add(self.update_scrollbar)










# ============================================================
#   PROGRESS BAR WIDGET
# ============================================================

class ProgressBarWidget(Gtk.Box):
    """Compact progress bar with cancel button for file loading"""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(2)
        self.set_margin_bottom(2)
        self.add_css_class("progress-bar-widget")
        
        # Progress bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_hexpand(True)
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("Loading...")
        self.append(self.progress_bar)
        
        # Cancel button
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.add_css_class("flat")
        self.append(self.cancel_button)
        
        # Initially hidden
        self.set_visible(False)
        
        # Cancellation flag
        self.cancelled = False
    
    def start_loading(self, callback=None):
        """Start showing progress bar"""
        self.cancelled = False
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text("Loading...")
        self.set_visible(True)
        
        if callback:
            self.cancel_button.connect("clicked", callback)
    
    def update_progress(self, fraction):
        """Update progress (0.0 to 1.0)"""
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{int(fraction * 100)}%")
    
    def finish_loading(self):
        """Hide progress bar"""
        self.set_visible(False)
        self.cancelled = False


# ============================================================
#   STATUS BAR
# ============================================================

class StatusBar(Gtk.Box):
    """Comprehensive status bar with file type, tab width, encoding, line feed, cursor position, and INS/OVR indicator"""
    
    def __init__(self, editor_window):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.editor_window = editor_window
        self.add_css_class("status-bar")
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(2)
        self.set_margin_bottom(2)
        
        # File type dropdown
        self.file_type_button = Gtk.MenuButton()
        self.file_type_label = Gtk.Label(label="Plain Text")
        self.file_type_button.set_child(self.file_type_label)
        self.file_type_button.add_css_class("flat")
        self.file_type_button.set_tooltip_text("File Type")
        self._create_file_type_menu()
        self.append(self.file_type_button)
        
        self.append(self._create_separator())
        
        # Tab width dropdown
        self.tab_width_button = Gtk.MenuButton()
        self.tab_width_label = Gtk.Label(label="Tab Width: 4")
        self.tab_width_button.set_child(self.tab_width_label)
        self.tab_width_button.add_css_class("flat")
        self.tab_width_button.set_tooltip_text("Tab Width")
        self._create_tab_width_menu()
        self.append(self.tab_width_button)
        
        self.append(self._create_separator())
        
        # Use spaces checkbox
        self.use_spaces_check = Gtk.CheckButton(label="Use Spaces")
        self.use_spaces_check.set_active(True)
        self.use_spaces_check.connect("toggled", self._on_use_spaces_toggled)
        self.append(self.use_spaces_check)
        
        self.append(self._create_separator())
        
        # Encoding dropdown
        self.encoding_button = Gtk.MenuButton()
        self.encoding_label = Gtk.Label(label="UTF-8")
        self.encoding_button.set_child(self.encoding_label)
        self.encoding_button.add_css_class("flat")
        self.encoding_button.set_tooltip_text("Encoding")
        self._create_encoding_menu()
        self.append(self.encoding_button)
        
        self.append(self._create_separator())
        
        # Line feed dropdown
        self.line_feed_button = Gtk.MenuButton()
        self.line_feed_label = Gtk.Label(label="Unix/Linux (LF)")
        self.line_feed_button.set_child(self.line_feed_label)
        self.line_feed_button.add_css_class("flat")
        self.line_feed_button.set_tooltip_text("Line Ending")
        self._create_line_feed_menu()
        self.append(self.line_feed_button)
        
        # Spacer to push cursor position to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.append(spacer)
        
        # Cursor position
        self.cursor_pos_label = Gtk.Label(label="Ln 1, Col 1")
        self.cursor_pos_label.set_margin_start(8)
        self.cursor_pos_label.set_margin_end(8)
        self.append(self.cursor_pos_label)
        
        self.append(self._create_separator())
        
        # INS/OVR indicator
        self.ins_ovr_label = Gtk.Label(label="INS")
        self.ins_ovr_label.set_margin_start(8)
        self.ins_ovr_label.set_margin_end(8)
        self.append(self.ins_ovr_label)
    
    def _create_separator(self):
        """Create a vertical separator"""
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        sep.set_margin_start(4)
        sep.set_margin_end(4)
        return sep
    
    def _create_file_type_menu(self):
        """Create file type dropdown with search"""
        # Create a popover with search
        popover = Gtk.Popover()
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        
        # Search entry
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Search file types...")
        box.append(search_entry)
        
        # Scrolled window for file types
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        scrolled.set_min_content_width(200)
        
        # List box for file types
        self.file_type_listbox = Gtk.ListBox()
        self.file_type_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.file_type_listbox.add_css_class("boxed-list")
        
        # File types
        file_types = [
            "Plain Text", "Python", "JavaScript", "Shell", "C", "C++", "Rust",
            "HTML", "CSS", "JSON", "XML", "Markdown", "YAML", "TOML",
            "Java", "Go", "Ruby", "PHP", "TypeScript", "SQL"
        ]
        
        for ft in file_types:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=ft)
            label.set_xalign(0)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.set_child(label)
            row._file_type = ft
            self.file_type_listbox.append(row)
        
        self.file_type_listbox.connect("row-activated", self._on_file_type_selected)
        
        # Search filter
        def filter_func(row):
            search_text = search_entry.get_text().lower()
            if not search_text:
                return True
            return search_text in row._file_type.lower()
        
        self.file_type_listbox.set_filter_func(filter_func)
        search_entry.connect("search-changed", lambda e: self.file_type_listbox.invalidate_filter())
        
        scrolled.set_child(self.file_type_listbox)
        box.append(scrolled)
        
        popover.set_child(box)
        self.file_type_button.set_popover(popover)
    
    def _on_file_type_selected(self, listbox, row):
        """Handle file type selection"""
        file_type = row._file_type
        self.file_type_label.set_text(file_type)
        self.file_type_button.get_popover().popdown()
        
        # Apply to current editor
        editor = self.editor_window.get_current_page()
        if editor:
            # Map display name to language ID
            lang_map = {
                "Plain Text": None,
                "Python": "python",
                "JavaScript": "javascript",
                "Shell": "sh",
                "C": "c",
                "C++": "cpp",
                "Rust": "rust",
                "HTML": "html",
                "CSS": "css",
                "JSON": "json",
                "XML": "xml",
                "Markdown": "markdown",
                "YAML": "yaml",
                "TOML": "toml",
                "Java": "java",
                "Go": "go",
                "Ruby": "ruby",
                "PHP": "php",
                "TypeScript": "typescript",
                "SQL": "sql"
            }
            lang = lang_map.get(file_type)
            editor.view.buf.set_language(lang)
            editor.view.syntax = editor.view.buf.syntax_engine
            editor.view.queue_draw()
    
    def _create_tab_width_menu(self):
        """Create tab width dropdown with radio buttons"""
        menu = Gio.Menu()
        
        menu.append("2", "win.set_tab_width::2")
        menu.append("4", "win.set_tab_width::4")
        menu.append("8", "win.set_tab_width::8")
        
        popover = Gtk.PopoverMenu.new_from_model(menu)
        self.tab_width_button.set_popover(popover)
    
    def _create_encoding_menu(self):
        """Create encoding dropdown"""
        menu = Gio.Menu()
        
        menu.append("UTF-8", "win.set_encoding::utf-8")
        menu.append("UTF-16 LE", "win.set_encoding::utf-16le")
        menu.append("UTF-16 BE", "win.set_encoding::utf-16be")
        
        popover = Gtk.PopoverMenu.new_from_model(menu)
        self.encoding_button.set_popover(popover)
    
    def _create_line_feed_menu(self):
        """Create line feed dropdown"""
        menu = Gio.Menu()
        
        menu.append("Unix/Linux (LF)", "win.set_line_feed::lf")
        menu.append("Windows (CRLF)", "win.set_line_feed::crlf")
        menu.append("Mac OS (CR)", "win.set_line_feed::cr")
        
        popover = Gtk.PopoverMenu.new_from_model(menu)
        self.line_feed_button.set_popover(popover)
    
    def _on_use_spaces_toggled(self, check_button):
        """Handle use spaces toggle"""
        # Apply to current editor
        editor = self.editor_window.get_current_page()
        if editor:
            editor.use_spaces = check_button.get_active()
    
    def update_cursor_position(self, line, col):
        """Update cursor position display"""
        self.cursor_pos_label.set_text(f"Ln {line + 1}, Col {col + 1}")
    
    def update_insert_mode(self, is_insert):
        """Update INS/OVR indicator"""
        self.ins_ovr_label.set_text("INS" if is_insert else "OVR")
    
    def update_for_editor(self, editor):
        """Update status bar for current editor"""
        if not editor:
            return
        
        # Update cursor position
        self.update_cursor_position(editor.buf.cursor_line, editor.buf.cursor_col)
        
        # Update encoding
        encoding = getattr(editor, 'current_encoding', 'utf-8')
        encoding_display = {
            'utf-8': 'UTF-8',
            'utf-8-sig': 'UTF-8',
            'utf-16le': 'UTF-16 LE',
            'utf-16be': 'UTF-16 BE'
        }.get(encoding, encoding.upper())
        self.encoding_label.set_text(encoding_display)
        
        # Update file type based on file extension
        if editor.current_file_path:
            ext = os.path.splitext(editor.current_file_path)[1].lower()
            type_map = {
                '.py': 'Python',
                '.js': 'JavaScript',
                '.sh': 'Shell',
                '.c': 'C',
                '.cpp': 'C++',
                '.rs': 'Rust',
                '.html': 'HTML',
                '.css': 'CSS',
                '.json': 'JSON',
                '.xml': 'XML',
                '.md': 'Markdown',
                '.yaml': 'YAML',
                '.yml': 'YAML',
                '.toml': 'TOML'
            }
            file_type = type_map.get(ext, 'Plain Text')
            self.file_type_label.set_text(file_type)
        else:
            self.file_type_label.set_text('Plain Text')


# ============================================================
#   LOADING DIALOG
# ============================================================

class LoadingDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(300, 150)
        self.set_title("Loading File")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        
        self.label = Gtk.Label(label="Indexing file...")
        box.append(self.label)
        
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        box.append(self.progress)
        
        spinner = Gtk.Spinner()
        spinner.start()
        box.append(spinner)
        
        self.set_content(box)
    
    def update_progress(self, fraction):
        """Update progress bar (must be called from main thread)"""
        self.progress.set_fraction(fraction)
        self.progress.set_text(f"{int(fraction * 100)}%")



# ============================================================
#   CHROME TABS
# ============================================================

# Global variable for drag and drop
DRAGGED_TAB = None

class ChromeTab(Gtk.Box):
    """A custom tab widget that behaves like Chrome tabs"""
    _drag_in_progress = False

    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
   
    def __init__(self, title="Untitled 1", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        FIXED_H = 32
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.add_css_class("chrome-tab")
        self.set_vexpand(False)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_size_request(120, FIXED_H)
        self.set_hexpand(False)       
        overlay = Gtk.Overlay()

        # Title label container
        # We use a box to hold label + close button together for centering
        label_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        label_box.set_hexpand(True)
        label_box.set_halign(Gtk.Align.CENTER) # Center the group
        label_box.set_valign(Gtk.Align.CENTER)
        
        # Title label
        self.label = Gtk.Label()
        self.label.set_text(title)
        # Remove margin_end as we now have spacing in the box
        #self.label.set_margin_end(30) 
        self.label.set_max_width_chars(20)
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label.set_single_line_mode(True)
        self.label.set_hexpand(True) # Don't expand, let box center it
        self.label.set_halign(Gtk.Align.CENTER)

        label_box.append(self.label)
        
        # State tracking
        self._is_modified = False
        self._is_hovered = False
        
        # Close button (now inside label_box)
        if closeable:
            self.close_button = Gtk.Button()
            # Default state: cross-small-symbolic (will be updated by _update_close_button_state)
            self.close_button.set_icon_name("cross-small-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("chrome-tab-close-button")
            self.close_button.set_halign(Gtk.Align.CENTER)
            self.close_button.set_valign(Gtk.Align.CENTER)
            self.close_button.connect('clicked', self._on_close_clicked)
            
            # Hover controller for the button/tab interaction
            # We want the hover effect when hovering the *tab*, not just the button
            # So we add the controller to self (the tab)
            hover_controller = Gtk.EventControllerMotion()
            hover_controller.connect("enter", self._on_hover_enter)
            hover_controller.connect("leave", self._on_hover_leave)
            self.add_controller(hover_controller)
            
            label_box.append(self.close_button)
            
            # Initial state update
            self._update_close_button_state()
        
        # We don't use overlay for the content anymore, just the box directly
        # But keeping overlay structure if we need other overlays later is fine, 
        # or we can just append label_box to self if we want.
        # The original code used overlay.set_child(label_box).
        overlay.set_child(label_box)
       
        self.append(overlay)
       
        self._is_active = False
        self._original_title = title
        self.tab_bar = None  # Set by ChromeTabBar
        
        # Dragging setup
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.add_controller(drag_source)
        
        # Explicitly claim clicks
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0) # Listen to all buttons (left, middle, right)
        click_gesture.connect('pressed', self._on_tab_pressed)
        click_gesture.connect('released', self._on_tab_released)
        self.add_controller(click_gesture)

    def _on_hover_enter(self, controller, x, y):
        self._is_hovered = True
        self._update_close_button_state()
        
        # Notify tab bar to hide separators
        if self.tab_bar and hasattr(self.tab_bar, 'hide_separators_for_tab'):
            self.tab_bar.hide_separators_for_tab(self)

    def _on_hover_leave(self, controller):
        self._is_hovered = False
        self._update_close_button_state()
        
        # Notify tab bar to restore separators
        if self.tab_bar and hasattr(self.tab_bar, 'update_separators'):
            self.tab_bar.update_separators()

    def _update_close_button_state(self):
        if not hasattr(self, 'close_button'):
            return

        if self._is_modified:
            if self._is_hovered:
                # Modified + Hover: Show Close Icon
                self.close_button.set_icon_name("cross-small-symbolic")
                self.close_button.set_opacity(1.0)
            else:
                # Modified + No Hover: Show Dot
                self.close_button.set_icon_name("big-dot-symbolic")
                self.close_button.set_opacity(1.0)
        else:
            if self._is_hovered:
                # Unmodified + Hover: Show Close Icon
                self.close_button.set_icon_name("cross-small-symbolic")
                self.close_button.set_opacity(1.0)
            else:
                # Unmodified + No Hover: Show Faint Close Icon
                self.close_button.set_icon_name("cross-small-symbolic")
                self.close_button.set_opacity(0.5) # Low opacity
                self.close_button.set_sensitive(True) # Still clickable

        # Ensure button is sensitive (unless we explicitly disabled it logic above, which we don't anymore)
        self.close_button.set_sensitive(True)

    def set_modified(self, modified: bool):
        self._is_modified = modified
        self._update_close_button_state()
        
        # Add/remove CSS class for modified state (used by close_tab detection)
        if modified:
            self.add_css_class("modified")
        else:
            self.remove_css_class("modified")

       
    def _on_tab_pressed(self, gesture, n_press, x, y):
        # Check if click is on the close button - if so, don't claim it
        if hasattr(self, 'close_button') and self.close_button.get_sensitive():
            # Convert coordinates to widget-relative (GTK4 returns tuple of x, y)
            coords = self.close_button.translate_coordinates(self, 0, 0)
            if coords is not None:
                widget_x, widget_y = coords
                # Check if click is within close button bounds
                if (widget_x <= x <= widget_x + self.close_button.get_width() and
                    widget_y <= y <= widget_y + self.close_button.get_height()):
                    # Don't claim - let the button handle it
                    return
        
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        
        # Check for right click (button 3)
        current_button = gesture.get_current_button()
        if n_press == 1 and current_button == 3:
            self._show_context_menu(x, y)
            return

        if self.tab_bar:
            self.tab_bar.hide_separators_for_tab(self)

    def _show_context_menu(self, x, y):
        """Show context menu for the tab"""
        if not self.tab_bar:
            return
            
        # Get index of this tab
        try:
            tab_index = self.tab_bar.tabs.index(self)
        except ValueError:
            return

        menu = Gio.Menu()
        
        # Helper to add item with string target
        def add_item(label, action, target_str):
            item = Gio.MenuItem.new(label, action)
            item.set_action_and_target_value(action, GLib.Variant.new_string(target_str))
            return item

        idx_str = str(tab_index)

        # Section 1: Move
        section1 = Gio.Menu()
        section1.append_item(add_item("Move Left", "win.tab_move_left", idx_str))
        section1.append_item(add_item("Move Right", "win.tab_move_right", idx_str))
        section1.append_item(add_item("Split View Horizontally", "win.tab_split_horizontal", idx_str))
        section1.append_item(add_item("Split View Vertically", "win.tab_split_vertical", idx_str))
        section1.append_item(add_item("Move to New Window", "win.tab_move_new_window", idx_str))
        menu.append_section(None, section1)
        
        # Section 2: Close
        section2 = Gio.Menu()
        section2.append_item(add_item("Close Tabs to Left", "win.tab_close_left", idx_str))
        section2.append_item(add_item("Close Tabs to Right", "win.tab_close_right", idx_str))
        section2.append_item(add_item("Close Other Tabs", "win.tab_close_other", idx_str))
        section2.append_item(add_item("Close", "win.tab_close", idx_str))
        menu.append_section(None, section2)
        
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self)
        popover.set_has_arrow(False)
        
        # Position at click
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        
        popover.popup()

        
    def _on_tab_released(self, gesture, n_press, x, y):
        self.emit('activate-requested')
       
    def _on_close_clicked(self, button):
        self.emit('close-requested')
       
    def set_title(self, title):
        self._original_title = title
        self.update_label()
       
    def get_title(self):
        return self._original_title
    


    def update_label(self):
        """Update the label text."""
        self.label.set_text(self._original_title)

       
    def set_active(self, active):
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")
           

    
    # Drag and drop handlers
    def _on_drag_prepare(self, source, x, y):
        """Prepare drag operation - return content provider with tab object"""
        # Prevent concurrent drags
        if ChromeTab._drag_in_progress:
            return None
        
        # Pass the ChromeTab object directly
        return Gdk.ContentProvider.new_for_value(self)
    
    def _on_drag_begin(self, source, drag):
        """Called when drag begins - set visual feedback"""
        global DRAGGED_TAB
        
        # Prevent concurrent drags
        if ChromeTab._drag_in_progress:
            drag.drop_done(False)
            return
        
        ChromeTab._drag_in_progress = True
        DRAGGED_TAB = self
        self.drag_success = False  # Track if drag was successful
        
        # Add a CSS class for visual feedback
        self.add_css_class("dragging")
        
        # Create drag icon from the tab widget
        paintable = Gtk.WidgetPaintable.new(self)
        source.set_icon(paintable, 0, 0)
    
    def _on_drag_end(self, source, drag, delete_data):
        """Called when drag ends - cleanup and handle cross-window transfer"""
        global DRAGGED_TAB
        
        # Reset drag success flag for next drag
        had_success = getattr(self, 'drag_success', False)
        self.drag_success = False
        
        # Check if tab was already transferred (e.g. by drop handler)
        was_transferred = getattr(self, 'was_transferred', False)
        self.was_transferred = False
        
        # Clean up visual state
        DRAGGED_TAB = None
        self.remove_css_class("dragging")
        
        # Schedule cleanup of drag lock after a delay to ensure all operations complete
        def cleanup_drag_lock():
            ChromeTab._drag_in_progress = False
            return False
        
        GLib.timeout_add(100, cleanup_drag_lock)  # 100ms delay
        
        if was_transferred:
            return

        # If drag was successful and cross-window, close the source tab
        # Only close if it was a CROSS-WINDOW drag (tab_bar changed)
        if had_success:
            # Check if this was actually a cross-window transfer
            # by checking if the tab is still in its original tab_bar
            if self.tab_bar and self not in self.tab_bar.tabs:
                # Tab was removed from original bar = cross-window transfer
                # The drop handler already took care of closing the source tab
                pass
            # If tab is still in tab_bar, it was just reordered within same window
            # Don't do anything - normal reordering handled it
            return
        
        # If drag was NOT successful (dropped on nothing), check if dropped outside window
        # But only if we still have a valid tab_bar reference
        if not self.tab_bar or self not in self.tab_bar.tabs:
            # Tab is detached or invalid, don't try to process further
            return
        
        # Find the window that owns this tab
        window = None
        parent = self.tab_bar.get_parent()
        while parent:
            if isinstance(parent, Adw.ApplicationWindow):
                window = parent
                break
            parent = parent.get_parent()
        
        if not window:
            return
        
        # Use idle_add to defer the window check to avoid GTK state issues
        def check_outside_window():
            # Get seat and pointer
            try:
                seat = Gdk.Display.get_default().get_default_seat()
                if not seat:
                    return False
                
                pointer = seat.get_pointer()
                if not pointer:
                    return False
                
                # Get window surface and coordinates
                surface = window.get_surface()
                if not surface:
                    return False
                
                # Check if outside
                # On Wayland, get_device_position returns False if pointer is not over surface
                found, x, y, mask = surface.get_device_position(pointer)
                
                is_outside = False
                if not found:
                    is_outside = True
                else:
                    # Even if found, check bounds (in case of grab)
                    width = window.get_width()
                    height = window.get_height()
                    if x < 0 or y < 0 or x > width or y > height:
                        is_outside = True
                
                if is_outside:
                    # It is outside!
                    # Trigger move to new window
                    if self.tab_bar and self in self.tab_bar.tabs:
                        idx = self.tab_bar.tabs.index(self)
                        window.activate_action('win.tab_move_new_window', GLib.Variant.new_string(str(idx)))
                
            except Exception as e:
                print(f"Error checking window bounds: {e}")
            
            return False
        
        # Defer the check to let GTK clean up drag state
        GLib.timeout_add(50, check_outside_window)  # 50ms delay



class ChromeTabBar(Adw.WrapBox):
    """
    Chrome-like tab bar with correct separator model.
    separators[i] is BEFORE tab[i]
    and there is one final separator after last tab.
    """

    __gsignals__ = {
        'tab-reordered': (GObject.SignalFlags.RUN_FIRST, None, (object, int)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)

        self.set_margin_start(6)
        self.set_margin_top(0)
        self.set_margin_bottom(0)
        self.set_child_spacing(0)

        self.tabs = []
        self.separators = []   # separator BEFORE each tab + 1 final separator
        
        # Drop indicator for drag and drop
        self.drop_indicator = Gtk.Box()
        self.drop_indicator.set_size_request(3, 24)
        self.drop_indicator.add_css_class("tab-drop-indicator")
        self.drop_indicator.set_visible(False)
        self.drop_indicator_position = -1

        # Create initial left separator (this one will be hidden)
        first_sep = Gtk.Box()
        first_sep.set_size_request(1, 1)
        first_sep.add_css_class("chrome-tab-separator")
        self.append(first_sep)
        self.separators.append(first_sep)
        
        # Setup drop target on the tab bar itself
        # Accept ChromeTab objects directly
        drop_target = Gtk.DropTarget.new(ChromeTab, Gdk.DragAction.MOVE)
        drop_target.connect('drop', self._on_tab_bar_drop)
        drop_target.connect('motion', self._on_tab_bar_motion)
        drop_target.connect('leave', self._on_tab_bar_leave)
        self.add_controller(drop_target)

    def add_tab(self, tab):
        idx = len(self.tabs)

        # Insert tab AFTER separator[idx]
        before_sep = self.separators[idx]
        self.insert_child_after(tab, before_sep)

        # Insert separator AFTER the tab
        new_sep = Gtk.Box()
        new_sep.set_size_request(1, 1)
        new_sep.add_css_class("chrome-tab-separator")
        self.insert_child_after(new_sep, tab)

        # update internal lists
        self.tabs.append(tab)
        self.separators.insert(idx + 1, new_sep)
        
        # Set tab_bar reference for drag and drop
        tab.tab_bar = self
        tab.separator = new_sep

        # setup hover handlers - REMOVED (handled by ChromeTab now)
        # self._connect_hover(tab)

        self.update_separators()
        
        # Update window UI state (visibility of tab bar)
        window = self.get_ancestor(Adw.ApplicationWindow)
        if window and hasattr(window, 'update_ui_state'):
            window.update_ui_state()



    def remove_tab(self, tab):
        if tab not in self.tabs:
            return

        idx = self.tabs.index(tab)

        # Remove tab widget
        self.remove(tab)

        # Remove separator AFTER this tab
        sep = self.separators[idx + 1]
        self.remove(sep)
        del self.separators[idx + 1]

        # Keep separator[0] (always exists)
        self.tabs.remove(tab)

        self.update_separators()
    
        # Update window UI state (visibility of tab bar)
        window = self.get_ancestor(Adw.ApplicationWindow)
        if window and hasattr(window, 'update_ui_state'):
            window.update_ui_state()

    # _connect_hover REMOVED - logic moved to ChromeTab

    def set_tab_active(self, tab):
        for t in self.tabs:
            t.set_active(t is tab)

        # update separators *immediately*
        self.update_separators()

    def _hide_pair(self, i):
        """Hide left + right separators for tab[i]."""

        # Hide left separator if not first tab
        if i > 0:
            self.separators[i].add_css_class("hidden")

        # Hide right separator if not last tab
        if i + 1 < len(self.separators) - 1:
            self.separators[i + 1].add_css_class("hidden")

    def hide_separators_for_tab(self, tab):
        """Immediately hide separators around this tab (used on press)"""
        if tab in self.tabs:
            i = self.tabs.index(tab)
            self._hide_pair(i)
    
    def reorder_tab(self, tab, new_index):
        """Reorder a tab to a new position"""
        if tab not in self.tabs:
            return
        
        old_index = self.tabs.index(tab)
        if old_index == new_index:
            return
        
        # Get the separator associated with this tab
        tab_separator = tab.separator
        
        # Remove from old position in list
        self.tabs.pop(old_index)
        
        # Insert at new position in list
        self.tabs.insert(new_index, tab)
        
        # Reorder widgets in the WrapBox
        if new_index == 0:
            anchor = self.separators[0]
        else:
            prev_tab = self.tabs[new_index - 1]
            anchor = prev_tab.separator
        
        self.reorder_child_after(tab, anchor)
        self.reorder_child_after(tab_separator, tab)
        
        # Rebuild separator list to match new tab order
        self.separators = [self.separators[0]] + [t.separator for t in self.tabs]
        
        # Update separators
        self.update_separators()
        
        # Emit signal to notify parent
        self.emit('tab-reordered', tab, new_index)

    def update_separators(self):
        # Reset all
        for sep in self.separators:
            sep.remove_css_class("hidden")

        # Hide edge separators permanently
        if self.separators:
            self.separators[0].add_css_class("hidden")
            if len(self.separators) > 1:
                self.separators[-1].add_css_class("hidden")

        # Hide around active tab
        for i, tab in enumerate(self.tabs):
            if tab.has_css_class("active"):
                self._hide_pair(i)
    
    def _calculate_drop_position(self, x, y):
        """Calculate the drop position based on mouse X and Y coordinates"""
        # Group tabs by row
        rows = {}
        for i, tab in enumerate(self.tabs):
            success, bounds = tab.compute_bounds(self)
            if not success:
                continue
                
            # Use the middle Y of the tab to identify the row
            mid_y = bounds.origin.y + bounds.size.height / 2
            
            # Find matching row (simple clustering)
            found_row = False
            for row_y in rows:
                if abs(row_y - mid_y) < bounds.size.height / 2:
                    rows[row_y].append((i, tab))
                    found_row = True
                    break
            if not found_row:
                rows[mid_y] = [(i, tab)]
        
        # Sort rows by Y coordinate
        sorted_row_ys = sorted(rows.keys())
        
        # Find which row the mouse is in
        target_row_y = None
        for row_y in sorted_row_ys:
            # Check if Y is within this row's vertical bounds (approx)
            # We assume standard height for all tabs
            if abs(y - row_y) < 20: # 20 is roughly half height
                target_row_y = row_y
                break
        
        # If no row matched, check if we are below the last row
        if target_row_y is None:
            if not sorted_row_ys:
                return len(self.tabs)
            if y > sorted_row_ys[-1] + 20:
                return len(self.tabs)
            # If above first row, return 0
            if y < sorted_row_ys[0] - 20:
                return 0
            # If between rows, find the closest one
            closest_y = min(sorted_row_ys, key=lambda ry: abs(y - ry))
            target_row_y = closest_y

        # Now find position within the target row
        row_tabs = rows[target_row_y]
        
        for i, tab in row_tabs:
            success, bounds = tab.compute_bounds(self)
            if not success:
                continue
                
            tab_center = bounds.origin.x + bounds.size.width / 2
            
            if x < tab_center:
                return i
        
        # If past the last tab in this row, return index after the last tab in this row
        last_idx_in_row = row_tabs[-1][0]
        return last_idx_in_row + 1
    
    def _show_drop_indicator(self, position):
        """Show the drop indicator line at the specified position"""
        if position == self.drop_indicator_position:
            return
        
        # Remove indicator from old position
        if self.drop_indicator.get_parent():
            self.remove(self.drop_indicator)
        
        self.drop_indicator_position = position
        
        # Insert indicator at new position
        if position == 0:
            self.insert_child_after(self.drop_indicator, self.separators[0])
        elif position < len(self.tabs):
            self.insert_child_after(self.drop_indicator, self.separators[position])
        else:
            if len(self.separators) > len(self.tabs):
                self.insert_child_after(self.drop_indicator, self.separators[-1])
        
        self.drop_indicator.set_visible(True)
    
    def _hide_drop_indicator(self):
        """Hide the drop indicator"""
        self.drop_indicator.set_visible(False)
        if self.drop_indicator.get_parent():
            self.remove(self.drop_indicator)
        self.drop_indicator_position = -1
    
    def _on_tab_bar_motion(self, target, x, y):
        """Handle drag motion over the tab bar"""
        position = self._calculate_drop_position(x, y)
        self._show_drop_indicator(position)
        return Gdk.DragAction.MOVE
    
    def _on_tab_bar_leave(self, target):
        """Handle drag leaving the tab bar"""
        self._hide_drop_indicator()
        self.update_separators()
    
    def _on_tab_bar_drop(self, target, value, x, y):
        """Handle drop on the tab bar - supports same-window and cross-window tab drops"""
        global DRAGGED_TAB
        
        # Prevent processing if drag is being finalized
        if not ChromeTab._drag_in_progress:
            return False
        
        # We now expect a ChromeTab object directly
        if not isinstance(value, ChromeTab):
            return False
            
        dragged_tab = value
        
        # Get target window
        target_window = None
        parent = self.get_parent()
        while parent:
            if isinstance(parent, Adw.ApplicationWindow):
                target_window = parent
                break
            parent = parent.get_parent()
        
        if not target_window:
            return False
            
        # Check if this is a cross-window drag (tab is from another tab bar)
        if dragged_tab.tab_bar != self:
            # Cross-window drop
            drop_position = self._calculate_drop_position(x, y)
            
            # Get source window BEFORE removing tab from bar
            source_window = None
            if dragged_tab.tab_bar:
                source_window = dragged_tab.tab_bar.get_ancestor(Adw.ApplicationWindow)
            
            # Reparent the tab
            # 1. Remove from source tab bar
            if dragged_tab.tab_bar:
                dragged_tab.tab_bar.remove_tab(dragged_tab)
            
            # 2. Add to this tab bar at the correct position
            # We need to insert it, but add_tab appends. 
            # So we append then reorder.
            self.add_tab(dragged_tab)
            
            # Mark drag as successful so source doesn't try to close it again
            dragged_tab.drag_success = True
            
            # Reorder to drop position
            # Note: add_tab puts it at the end, so index is len-1
            current_index = len(self.tabs) - 1
            if current_index != drop_position:
                self.reorder_tab(dragged_tab, drop_position)
            
            # 3. Transfer the EditorPage
            if source_window and source_window != target_window and hasattr(dragged_tab, '_page'):
                # Mark as transferred so _on_drag_end doesn't try to close it
                dragged_tab.was_transferred = True
                
                # Switch signal connections from source window to target window
                if source_window:
                    try:
                        dragged_tab.disconnect_by_func(source_window.on_tab_activated)
                        dragged_tab.disconnect_by_func(source_window.on_tab_close_requested)
                    except Exception as e:
                        print(f"Error disconnecting signals: {e}")
                
                dragged_tab.connect('activate-requested', target_window.on_tab_activated)
                dragged_tab.connect('close-requested', target_window.on_tab_close_requested)

                page = getattr(dragged_tab, '_page', None)
                if page:
                    # Transfer page to target window's tab view
                    # IMPORTANT: transfer_page returns the NEW Adw.TabPage belonging to the target view
                    new_page = source_window.tab_view.transfer_page(page, target_window.tab_view, drop_position)
                    
                    # Update the tab's page reference immediately
                    if new_page:
                        dragged_tab._page = new_page
                        
                        # Ensure the page is selected in the new window
                        def select_page():
                            if new_page.get_selected_page() != new_page:
                                 target_window.tab_view.set_selected_page(new_page)
                            return False
                        GLib.idle_add(select_page)
                else:
                    print("Error: dragged_tab has no _page")
                    return False
            
            # 4. Activate the tab
            self.set_tab_active(dragged_tab)
            dragged_tab.emit('activate-requested')
            
            # Mark drag as successful
            if DRAGGED_TAB:
                DRAGGED_TAB.drag_success = True
                
            self._hide_drop_indicator()
            return True
        
        # Same-window drag
        if dragged_tab not in self.tabs:
            return False
        
        # Calculate drop position
        drop_position = self._calculate_drop_position(x, y)
        
        # Get current position of dragged tab
        current_position = self.tabs.index(dragged_tab)
        
        # Adjust drop position if dragging from before the drop point
        if current_position < drop_position:
            drop_position -= 1
        
        # Reorder the tab
        if current_position != drop_position:
            self.reorder_tab(dragged_tab, drop_position)
        
        # Mark drag as successful
        dragged_tab.drag_success = True
        
        # Hide indicator
        self._hide_drop_indicator()
        
        return True


# ============================================================
#   WINDOW
# ============================================================

# ============================================================
#   FIND AND REPLACE BAR
# ============================================================

class FindReplaceBar(Gtk.Box):
    def __init__(self, editor_page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.editor = editor_page
        self._cancel_search = None
        self._cancel_replace = None
        self._pending_replace = None # Queue replace args if search is busy
        self._suppress_auto_search = False # Flag to prevent auto-search on buffer change (e.g. during replace all)
        self._last_replaced_match = None  # Guard against rapid double-replace
        self.add_css_class("find-bar")
        self.set_visible(False)
        self._search_timeout_id = None
        self._scroll_refresh_timeout = None
        self._in_replace = False

        # Connect scroll callback for viewport-based search refresh
        self.editor.view.on_scroll_callback = self._on_editor_scrolled
        
        # Progressive search state
        self._search_last_line = 0  # Line where search stopped
        self._search_complete = False  # Whether entire file was searched
        self._progressive_search_active = False  # Prevent concurrent continuation
        
        # We'll use CSS to style it properly
        
        # --- Top Row: Find ---
        find_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        find_box.set_margin_top(6)
        find_box.set_margin_bottom(6)
        find_box.set_margin_start(12)
        find_box.set_margin_end(12)
        
        # Find Entry Overlay logic
        self.find_overlay = Gtk.Overlay()
        self.find_entry = Gtk.SearchEntry()
        self.find_entry.set_hexpand(True)
        self.find_entry.set_placeholder_text("Find")
        # Search on typing - uses debounced on_find_field_changed with auto-jump
        self.find_entry.connect("search-changed", self.on_find_field_changed)
        self.find_entry.connect("activate", self.on_find_next)
        
        self.find_overlay.set_child(self.find_entry)
        
        # Matches Label (x of y)
        self.matches_label = Gtk.Label(label="")
        self.matches_label.add_css_class("dim-label")
        self.matches_label.add_css_class("caption")
        self.matches_label.set_margin_end(30) # Increased to avoid overlap with clear icon
        self.matches_label.set_halign(Gtk.Align.END)
        self.matches_label.set_valign(Gtk.Align.CENTER)
        self.matches_label.set_visible(False)
        
        # We need to ensure the label doesn't block input. 
        # GtkOverlay pass-through is default for overlays generally? 
        # Actually usually controls block. But this is just a label.
        self.matches_label.set_can_target(False) # Make it click-through
        
        self.find_overlay.add_overlay(self.matches_label)
        
        # Capture Esc to close
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.find_entry.add_controller(key_ctrl)
        
        find_box.append(self.find_overlay)
        
        # Navigation Box (linked)
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav_box.add_css_class("linked")
        
        self.prev_btn = Gtk.Button(icon_name="go-up-symbolic")
        self.prev_btn.set_tooltip_text("Previous Match (Shift+Enter)")
        self.prev_btn.connect("clicked", self.on_find_prev)
        nav_box.append(self.prev_btn)
        
        self.next_btn = Gtk.Button(icon_name="go-down-symbolic")
        self.next_btn.set_tooltip_text("Next Match (Enter)")
        self.next_btn.connect("clicked", self.on_find_next)
        nav_box.append(self.next_btn)
        
        find_box.append(nav_box)

        # Toggle Replace Mode Button (Icon)
        self.reveal_replace_btn = Gtk.Button()
        self.reveal_replace_btn.set_icon_name("edit-find-replace-symbolic")
        self.reveal_replace_btn.add_css_class("flat")
        self.reveal_replace_btn.connect("clicked", self.toggle_replace_mode)
        self.reveal_replace_btn.set_tooltip_text("Toggle Replace")
        find_box.append(self.reveal_replace_btn)

        # Search Options (Cog Wheel)
        self.options_btn = Gtk.MenuButton()
        self.options_btn.set_icon_name("system-run-symbolic") # or emblem-system-symbolic / preferences-system-symbolic
        self.options_btn.set_tooltip_text("Search Options")
        self.options_btn.add_css_class("flat")
        
        # Create Popover Content
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        popover_box.set_margin_top(12)
        popover_box.set_margin_bottom(12)
        popover_box.set_margin_start(12)
        popover_box.set_margin_end(12)
        
        # Regex Option
        self.regex_check = Gtk.CheckButton(label="Regular Expressions")
        self.regex_check.connect("toggled", self.on_search_changed)
        popover_box.append(self.regex_check)
        
        # Case Option
        self.case_check = Gtk.CheckButton(label="Case Sensitive")
        self.case_check.connect("toggled", self.on_search_changed)
        popover_box.append(self.case_check)
        
        # Whole Word Option
        self.whole_word_check = Gtk.CheckButton(label="Match Whole Word Only")
        self.whole_word_check.connect("toggled", self.on_search_changed)
        popover_box.append(self.whole_word_check)
        
        self.options_popover = Gtk.Popover()
        self.options_popover.set_child(popover_box)
        self.options_btn.set_popover(self.options_popover)
        
        find_box.append(self.options_btn)
        
        # Close Button
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text("Close Find Bar (Esc)")
        close_btn.connect("clicked", self.close)
        find_box.append(close_btn)
        
        self.append(find_box)
        
        # --- Bottom Row: Replace (Hidden by default) ---
        self.replace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.replace_box.set_margin_bottom(6)
        self.replace_box.set_margin_start(12) # Restored margin to align with Find bar
        self.replace_box.set_margin_end(12)
        self.replace_box.set_visible(False)
        
        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_hexpand(True)
        self.replace_entry.set_placeholder_text("Replace")
        self.replace_entry.connect("activate", self.on_replace)
        self.replace_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find-replace-symbolic")
        
        # New controller for replace entry
        replace_key_ctrl = Gtk.EventControllerKey()
        replace_key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.replace_entry.add_controller(replace_key_ctrl)

        
        self.replace_box.append(self.replace_entry)
        
        # Replace Status Label (Percent/Count)
        self.replace_status_label = Gtk.Label(label="")
        self.replace_status_label.add_css_class("dim-label")
        self.replace_status_label.add_css_class("caption")
        self.replace_status_label.set_margin_end(6) 
        self.replace_status_label.set_visible(False)
        self.replace_box.append(self.replace_status_label)
        
        # Action Buttons
        self.replace_btn = Gtk.Button(label="Replace")
        self.replace_btn.connect("clicked", self.on_replace)
        self.replace_box.append(self.replace_btn)
        
        self.replace_all_btn = Gtk.Button(label="Replace All")
        self.replace_all_btn.connect("clicked", self.on_replace_all)
        self.replace_box.append(self.replace_all_btn)
        
        self.append(self.replace_box)

    def toggle_replace_mode(self, btn):
        vis = not self.replace_box.get_visible()
        self.replace_box.set_visible(vis)
        # Always keep the replace icon, just toggle visibility
        # icon = "pan-up-symbolic" if vis else "pan-down-symbolic"
        # self.reveal_replace_btn.set_icon_name(icon)
        
        if vis:
            self.replace_entry.grab_focus()
        else:
            self.find_entry.grab_focus()

    def show_search(self):
        self.set_visible(True)
        self.replace_box.set_visible(False)
        # self.reveal_replace_btn.set_icon_name("pan-down-symbolic")
        self.find_entry.grab_focus()
        # Select all text in find entry
        self.find_entry.select_region(0, -1)
        
    def show_replace(self):
        self.set_visible(True)
        self.replace_box.set_visible(True)
        # self.reveal_replace_btn.set_icon_name("pan-up-symbolic")
        self.find_entry.grab_focus() # Focus find first usually? Or replace? 
        # Usu. focus find, but show replace options.
        
    def close(self, *args):
        self.set_visible(False)
        self.editor.view.grab_focus()
        # Clear highlights?
        # self.editor.view.set_search_results([]) 
        # Usually we might want to keep them until search changes? 
        # But standard is clearing.
        # Let's clear for now.
        # Actually user might want to see highlights while editing. 
        # VS Code clears highlights when ESC is pressed but keeps widget open?
        # ESC in widget closes widget AND clears highlights.
        self.editor.view.set_search_results([])

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
            
        # Handle Undo/Redo here to effect the EDITOR buffer, not the entry
        # This is what users typically expect when focus is in Find/Replace but they want to undo a replacement
        if state & Gdk.ModifierType.CONTROL_MASK:
            # Ctrl+Z and Ctrl+Shift+Z
            if keyval == Gdk.KEY_z or keyval == Gdk.KEY_Z:
                if state & Gdk.ModifierType.SHIFT_MASK:
                    self.editor.view.undo_manager.redo()
                else:
                    self.editor.view.undo_manager.undo()
                return True
                
            # Ctrl+Y
            if keyval == Gdk.KEY_y or keyval == Gdk.KEY_Y:
                self.editor.view.undo_manager.redo()
                return True
                
        return False

    def mark_user_editing(self):
        """Called when user edits the document.
        
        With explicit search button, we do nothing here - search only
        updates when user clicks Search button or presses Enter.
        This eliminates viewport scrolling issues during editing.
        """
        pass  # No auto-update on edit - user must explicitly search

    def on_search_clicked(self, *args):
        """Handle Search button click - perform search with auto-scroll."""
        self._perform_search(auto_scroll=True)

    def on_find_field_changed(self, *args):
        """Called when the find entry text changes - searches and jumps to first match."""
        # 🔒 Do not re-search while replacing
        if self._in_replace:
            return False

        # Debounce to prevent excessive searches while typing
        if self._search_timeout_id:
            GLib.source_remove(self._search_timeout_id)
            self._search_timeout_id = None

        self._search_timeout_id = GLib.timeout_add(
            200,
            lambda: self._perform_search(auto_scroll=True, auto_jump=True)
        )
        return False

    def on_search_changed(self, *args):
        """Called when document changes or options toggle - refreshes search without jumping."""
        # 🔒 Do not re-search while replacing
        if self._in_replace:
            return False

        # Debounce to prevent excessive searches while typing
        if self._search_timeout_id:
            GLib.source_remove(self._search_timeout_id)
            self._search_timeout_id = None

        self._search_timeout_id = GLib.timeout_add(
            200,
            lambda: self._perform_search(auto_scroll=False, auto_jump=False)
        )
        return False

    def _perform_search(self, auto_scroll=True, auto_jump=False):
        self._search_timeout_id = None
        self._auto_scroll = auto_scroll  # Store for use in callbacks
        self._auto_jump = auto_jump  # Store for use in callbacks
        
        # Reset progressive search state for new search
        self._search_last_line = 0
        self._search_complete = False
        self._progressive_search_active = False
        
        # Cancel any ongoing async search
        if hasattr(self, '_cancel_search') and self._cancel_search:
            self._cancel_search()
            self._cancel_search = None
        
        query = self.find_entry.get_text()
        case_sensitive = self.case_check.get_active()
        is_regex = self.regex_check.get_active()
        whole_word = self.whole_word_check.get_active()
        
        if not query:
            self.editor.view.set_search_results([])
            self._current_search_query = None
            return False

        # Adjust query for Whole Word if not already regex (or if we want to force it)
        # If user selected Regex AND Whole Word, we typically wrap in \b...\b, 
        # but simplistic approach: if Whole Word, treat as regex \b...\b
        if whole_word:
            if not is_regex:
                # Escape the query so it's treated as literal text inside the regex
                import re
                escaped_query = re.escape(query)
                query = f"\\b{escaped_query}\\b"
                is_regex = True
            else:
                # If already regex, just wrap it
                query = f"\\b{query}\\b"
        
        # Store search params for viewport refresh
        self._current_search_query = query
        self._current_search_case = case_sensitive
        self._current_search_regex = is_regex
        
        total_lines = self.editor.buf.total()
        
        # For small files (<50k lines), use synchronous search
        if total_lines < 50000:
            matches, max_len = self.editor.buf.search(query, case_sensitive, is_regex, max_matches=-1)
            self.editor.view.set_search_results(matches, max_len, auto_scroll=self._auto_scroll)
            # Auto-jump to first match only when triggered from find field
            if matches and self._auto_jump:
                self.editor.view.next_match()
            self.update_match_label()
            return False
            
        if total_lines < 100000000:
            def on_progress(matches, lines_searched, total, max_len):
                self.editor.view.set_search_results(matches, max_len, preserve_current=True, auto_scroll=self._auto_scroll)
                # Show progress in label
                percent = int((lines_searched / total) * 100)
                count = len(matches)
                self.matches_label.set_text(f"Finding... {percent}% ({count})")
                self.matches_label.set_visible(True)
            
            def on_complete(matches, max_len):
                self._cancel_search = None
                self.editor.view.set_search_results(matches, max_len, preserve_current=True, auto_scroll=self._auto_scroll)
                # Auto-jump to first match only when triggered from find field
                if matches and self._auto_jump:
                    self.editor.view.next_match()
                self.update_match_label()
                # self.find_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None) # Not supported on SearchEntry
                
                # Check for pending replace
                if self._pending_replace:
                    self._pending_replace = None
                    print("Executing pending replace...")
                    self.replace_all_btn.set_sensitive(True)
                    self.on_replace_all(self.replace_all_btn)

            # Cancel previous
            if self._cancel_search:
                self._cancel_search()
                self._cancel_search = None
                self._pending_replace = None # Cancel pending if we restart search
                
            self._cancel_search = self.editor.buf.search_async(
                query, case_sensitive, is_regex, 
                max_matches=-1,
                on_progress=on_progress,
                on_complete=on_complete,
                chunk_size=20000
            )
            # Show stop icon? GtkEntry doesn't easily support clickable internal icon handling for custom logic 
            # unless we connect 'icon-press'. 
            # But standard search entry usually has clear icon.
            # We can use set_progress_fraction to show activity.
            return False
        
        # For huge files (>500k lines), use viewport-only search
        # This provides instant results in the visible area
        self._update_viewport_matches()
        
        return False
    
    def _on_editor_scrolled(self):
        """Called when editor scrolls - refresh viewport matches for huge files."""
        # Only refresh for huge files (>100M lines)
        if self.editor.buf.total() < 100000000:
            return
            
        # Debounce scroll refresh
        if self._scroll_refresh_timeout:
            GLib.source_remove(self._scroll_refresh_timeout)
        self._scroll_refresh_timeout = GLib.timeout_add(100, self._do_scroll_refresh)
    
    def _do_scroll_refresh(self):
        """Debounced scroll refresh of viewport matches."""
        self._scroll_refresh_timeout = None
        self._update_viewport_matches()
        return False
    
    def _update_viewport_matches(self):
        """Update search matches for the current viewport (for huge files)."""
        if not hasattr(self, '_current_search_query') or not self._current_search_query:
            return
            
        # Get visible line range with buffer
        visible_lines = max(50, self.editor.view.get_height() // self.editor.view.renderer.line_h)
        start_line = max(0, self.editor.view.scroll_line - visible_lines)
        end_line = min(self.editor.buf.total() - 1, 
                       self.editor.view.scroll_line + visible_lines * 2)
        
        matches, max_len = self.editor.buf.search_viewport(
            self._current_search_query,
            self._current_search_case,
            self._current_search_regex,
            start_line, end_line,
            max_matches=-1  # Limit for viewport
        )
        self.editor.view.set_search_results(matches, max_len)


    def on_find_next(self, *args):
        # If no matches exist, perform search first
        if not self.editor.view.search_matches:
            self._perform_search(auto_scroll=True)
        else:
            # Navigate to next match
            self.editor.view.next_match()
            self.update_match_label()
        
    def on_find_prev(self, *args):
        self.editor.view.prev_match()
        self.update_match_label()
        
    def on_replace(self, *args):
        # Get current match
        match = self.editor.view.current_match
        if not match:
            return

        # Guard against rapid clicking replacing the same match multiple times
        if self._last_replaced_match and self._last_replaced_match == match:
            return
        self._last_replaced_match = match
            
        replacement = self.replace_entry.get_text()
        s_ln, s_col, e_ln, e_col = match[0:4]
        
        # Handle Regex Replacement with capturing groups
        if self.regex_check.get_active():
            try:
                # 1. Get original match text
                match_text = self.editor.buf.get_text_range(s_ln, s_col, e_ln, e_col)
                
                # 2. Compile pattern
                query = self.find_entry.get_text()
                flags = 0 if self.case_check.get_active() else re.IGNORECASE
                pattern = re.compile(query, flags)
                
                # 3. Normalize replacement (\1 -> \g<1>)
                norm_repl = normalize_replacement_string(replacement)
                
                # 4. Expand
                replacement = pattern.sub(norm_repl, match_text)
            except Exception as e:
                print(f"Regex replacement error: {e}")
        
        # Calculate end position after replacement
        replacement_lines = replacement.split('\n')
        if len(replacement_lines) == 1:
            new_end_ln = s_ln
            new_end_col = s_col + len(replacement)
        else:
            new_end_ln = s_ln + len(replacement_lines) - 1
            new_end_col = len(replacement_lines[-1])
        
        # Set skip position BEFORE modifying buffer
        # This tells set_search_results to skip matches before this position
        self.editor.view._skip_to_position = (new_end_ln, new_end_col)
        
        # Perform replacement
        self.editor.buf.replace_current(match, replacement)
        self.editor.buf.set_cursor(new_end_ln, new_end_col)
        
        # Re-search - set_search_results will use _skip_to_position
        self.on_search_changed()
        self.update_match_label()
        
    def on_replace_all(self, *args):
        replacement = self.replace_entry.get_text()
        query = self.find_entry.get_text()
        case_sensitive = self.case_check.get_active()
        is_regex = self.regex_check.get_active()
        whole_word = self.whole_word_check.get_active()
        
        # Apply Whole Word logic
        if whole_word:
            if not is_regex:
                import re
                escaped_query = re.escape(query)
                query = f"\\b{escaped_query}\\b"
                is_regex = True
            else:
                query = f"\\b{query}\\b"
        
        total_lines = self.editor.buf.total()
        
        # Use Async Replace for "Replace All" to ensure UI responsiveness and support huge files
        # The threshold can be lower now that we have a good async implementation
        # Use Async Replace for "Replace All" to ensure UI responsiveness and support huge files
        # The threshold can be lower now that we have a good async implementation
        if total_lines > 1000:
            # Check if already replacing
            if hasattr(self, '_cancel_replace') and self._cancel_replace:
                # User wants to cancel
                self._cancel_replace()
                self._cancel_replace = None
                self.replace_all_btn.set_label("Replace All")
                self.replace_entry.set_progress_fraction(0.0)
                self.replace_entry.set_placeholder_text("Replace")
                # Restore interactivity? 
                return

            # Cancel any ongoing search? 
            # If search is ongoing, we should wait for it to complete so we can use the results for targeted replace.
            if self._cancel_search:
                # Queue this replace operation
                print("Queueing replace command until search completes...")
                self._pending_replace = (self.replace_all_btn,) # Args for on_replace_all? on_replace_all only takes widget.
                # Actually on_replace_all(*args) just ignores args.
                self._pending_replace = True
                
                self.replace_all_btn.set_label("Waiting...")
                self.replace_all_btn.set_sensitive(False)
                return

                # OLD LOGIC: Cancel search
                # self._cancel_search()
                # self._cancel_search = None
                # self.find_entry.set_progress_fraction(0.0) # SearchEntry doesn't support this


            # Update UI for busy state
            self.replace_all_btn.set_label("Stop")
            self.replace_status_label.set_visible(True)
            self.replace_status_label.set_text("Replacing...")
            self._suppress_auto_search = True # Suppress live search updates during replace
            
            def on_progress(count, lines_processed, total):
                percent = (lines_processed / total) if total > 0 else 0
                self.replace_entry.set_progress_fraction(percent)
                
                # Show status in label
                msg = f"{int(percent*100)}% ({count})"
                self.replace_status_label.set_text(msg)
                
            def on_complete(count):
                self._cancel_replace = None
                self._suppress_auto_search = False
                self.replace_all_btn.set_label("Replace All")
                self.replace_entry.set_progress_fraction(0.0)
                
                self.replace_status_label.set_text(f"Done ({count})")
                
                # self.replace_status_label.set_visible(False) # Maybe keep result for a moment?
                # Let's keep it until next action or close?
                
                print(f"Replaced {count} occurrences.")
                # self.on_search_changed() # Force re-search? NO, user says it's redundant.
                # Since we replaced everything, matches for original query should be 0.
                self.editor.view.set_search_results([], 0)
                self.matches_label.set_text("")
                self.matches_label.set_visible(False)
            
            # Determine if we can use cached matches (Targeted Replacement)
            target_lines = None
            if self.editor.view.search_matches and ('\n' not in replacement) and ('\r' not in replacement):
                 # Matches are tuples (start_ln, ...). We need unique lines sorted.
                 target_lines = sorted(list(set(m[0] for m in self.editor.view.search_matches)))
                 # Ensure matches are for current query? 
                 # Usually they are auto-updated. We assume sync.
                 
            self._cancel_replace = self.editor.buf.replace_all_async(
                query, replacement, case_sensitive, is_regex,
                on_progress=on_progress, 
                on_complete=on_complete,
                chunk_size=2000,
                target_lines=target_lines
            )
            return

        # For small files, sync replace is fine (and maybe faster due to overhead)
        count = self.editor.buf.replace_all(query, replacement, case_sensitive, is_regex)
        
        # Clear resulting search matches since we replaced them
        self.on_search_changed()
    
    def _check_progressive_search(self, current_idx):
        """Check if we need to load more search results progressively."""
        if self._progressive_search_active or self._search_complete:
            return
        
        matches = self.editor.view.search_matches
        if not matches:
            return
        
        # Trigger continuation when user reaches 90% of current matches
        threshold = int(len(matches) * 0.9)
        if current_idx >= threshold:
            self._continue_search()
    
    def _continue_search(self):
        """Continue searching from where we left off."""
        if self._progressive_search_active or self._search_complete:
            return
        
        self._progressive_search_active = True
        
        query = getattr(self, '_current_search_query', None)
        case_sensitive = getattr(self, '_current_search_case', False)
        is_regex = getattr(self, '_current_search_regex', False)
        
        if not query:
            self._progressive_search_active = False
            return
        
        total_lines = self.editor.buf.total()
        if self._search_last_line >= total_lines:
            self._search_complete = True
            self._progressive_search_active = False
            return
        
        # Continue search from last position
        def on_progress(new_matches, lines_searched, total, new_max_len):
            # Append new matches to existing
            current_matches = self.editor.view.search_matches or []
            combined = current_matches + new_matches
            combined_max_len = max(self.editor.view.max_match_length, new_max_len)
            self.editor.view.set_search_results(combined, combined_max_len, preserve_current=True)
            self._search_last_line = lines_searched
            self.update_match_label()
        
        def on_complete(new_matches, new_max_len):
            current_matches = self.editor.view.search_matches or []
            combined = current_matches + new_matches
            combined_max_len = max(self.editor.view.max_match_length, new_max_len)
            self.editor.view.set_search_results(combined, combined_max_len, preserve_current=True)
            
            # Check if we reached end of file
            if self._search_last_line >= total_lines:
                self._search_complete = True
            
            self._progressive_search_active = False
            self._cancel_search = None
            self.update_match_label()
        
        # Use search_async_from with start_line parameter
        self._cancel_search = self.editor.buf.search_async_from(
            query, case_sensitive, is_regex,
            start_line=self._search_last_line,
            max_matches=-1,
            on_progress=on_progress,
            on_complete=on_complete,
            chunk_size=20000
        )


    def update_match_label(self):
        matches = self.editor.view.search_matches
        if not matches:
             query = self.find_entry.get_text()
             if query:
                 self.matches_label.set_text("No results")
                 self.matches_label.set_visible(True)
             else:
                 self.matches_label.set_visible(False)
             return

        total = len(matches)
        current_idx = self.editor.view.current_match_idx
        
        if 0 <= current_idx < total:
            self.matches_label.set_text(f"{current_idx + 1} of {total}")
        else:
             self.matches_label.set_text(f"{total} found")
             
        self.matches_label.set_visible(True)



class EditorPage:
    """A single editor page containing buffer and view"""
    def __init__(self, untitled_title="Untitled 1"):
        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.view._editor = self  # Back-reference for view to access parent EditorPage
        self.view.set_margin_top(0)
        self.current_encoding = "utf-8"
        self.current_file_path = None
        self.current_file_path = None
        self.untitled_title = untitled_title  # Store custom Untitled title
        self.find_bar = None
        
        # Status bar properties
        self.tab_width = 4
        self.use_spaces = True
        self.line_feed = "lf"  # Unix/Linux default
        self.insert_mode = True  # INS mode by default


        
    def get_title(self):
        if self.current_file_path:
            return os.path.basename(self.current_file_path)
        return self.untitled_title
        
    def set_title(self, title):
        """Update the untitled title"""
        self.untitled_title = title

    def get_text(self):
        return self.buf.get_text()
        
    def set_text(self, text):
        self.buf.set_text(text)

class RecentFilesManager:
    """Manages recently opened/saved files list"""
    
    def __init__(self, max_files=10):
        self.max_files = max_files
        self.recent_files = []
        self.config_dir = os.path.join(GLib.get_user_config_dir(), "svite")
        self.config_file = os.path.join(self.config_dir, "recent_files.txt")
        self.load()
    
    def load(self):
        """Load recent files from config file"""
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.recent_files = [line.strip() for line in f.readlines() if line.strip()]
                    # Keep only files that still exist
                    self.recent_files = [f for f in self.recent_files if os.path.exists(f)]
        except Exception as e:
            print(f"Error loading recent files: {e}")
            self.recent_files = []
    
    def save(self):
        """Save recent files to config file"""
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.config_file, 'w') as f:
                for file_path in self.recent_files:
                    f.write(file_path + '\n')
        except Exception as e:
            print(f"Error saving recent files: {e}")
    
    def add(self, file_path):
        """Add a file to recent files list"""
        if not file_path:
            return
        
        # Remove if already exists
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        
        # Add to beginning
        self.recent_files.insert(0, file_path)
        
        # Trim to max_files
        self.recent_files = self.recent_files[:self.max_files]
        
        # Save to disk
        self.save()
    
    def get_recent_files(self):
        """Get list of recent files"""
        return self.recent_files.copy()
    
    def clear(self):
        """Clear all recent files"""
        self.recent_files = []
        self.save()


class SaveChangesDialog(Adw.Window):
    """Dialog to prompt user to save changes before closing"""
    
    def __init__(self, parent, modified_editors):
        super().__init__()
        
        self.modified_editors = modified_editors
        self.checkboxes = []  # Store checkboxes to check which files to save
        self.filename_entries = []  # Store entry widgets for filenames
        self.response = None  # Store the user's response
        self.save_button = None  # Store save button reference for focus
        
        # Set window properties
        self.set_modal(True)
        self.set_transient_for(parent)
        self.set_default_size(400, -1)
        self.set_resizable(False)
        
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        # Header with title
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.set_margin_top(24)
        header.set_margin_bottom(12)
        header.set_margin_start(24)
        header.set_margin_end(24)
        
        # Title
        title_label = Gtk.Label(label="Save Changes?")
        title_label.add_css_class("title-2")
        title_label.set_halign(Gtk.Align.CENTER)
        header.append(title_label)
        
        # Body text
        body_label = Gtk.Label(label="Open documents contain unsaved changes.\nChanges which are not saved will be permanently lost.")
        body_label.set_halign(Gtk.Align.CENTER)
        body_label.set_justify(Gtk.Justification.CENTER)
        body_label.set_wrap(True)
        body_label.add_css_class("dim-label")
        header.append(body_label)
        
        main_box.append(header)
        
        # Create list of modified files
        if len(modified_editors) > 0:
            # Create a box to hold the file list
            files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            files_box.set_margin_top(12)
            files_box.set_margin_bottom(12)
            files_box.set_margin_start(24)
            files_box.set_margin_end(24)
            
            for editor in modified_editors:
                # Create a check button for each file
                file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                
                check = Gtk.CheckButton()
                check.set_active(True)
                check.set_focus_on_click(False)  # Don't grab focus on click
                check._editor = editor
                self.checkboxes.append(check)  # Store for later
                file_box.append(check)
                
                # File info box
                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                info_box.set_hexpand(True)
                
                # Determine filename and if it's untitled
                if editor.current_file_path:
                    filename = os.path.basename(editor.current_file_path)
                    filepath = editor.current_file_path
                    is_untitled = False
                else:
                    # Check if it's an untitled file
                    title = editor.get_title()
                    is_untitled = title.startswith("Untitled")
                    
                    if is_untitled:
                        # Get first line of text as default filename
                        text = editor.get_text()
                        first_line = text.split('\n')[0].strip() if text else ""
                        
                        # Clean up first line for filename (remove invalid chars)
                        if first_line:
                            # Remove invalid filename characters
                            first_line = "".join(c for c in first_line if c.isalnum() or c in (' ', '-', '_', '.'))
                            first_line = first_line.strip()
                            # Limit length
                            if len(first_line) > 50:
                                first_line = first_line[:50]
                        
                        filename = first_line + ".txt" if first_line else "untitled.txt"
                    else:
                        filename = title
                    
                    # Show actual save location with ~ instead of full path
                    default_dir = os.path.expanduser("~/Documents")
                    if not os.path.exists(default_dir):
                        default_dir = os.path.expanduser("~")
                    filepath = default_dir
                
                # Replace home directory with ~ in filepath
                home_dir = os.path.expanduser("~")
                if filepath.startswith(home_dir):
                    filepath = filepath.replace(home_dir, "~", 1)
                
                # Create editable entry for filename
                entry = Gtk.Entry()
                entry.set_text(filename)
                entry.set_hexpand(True)
                entry._editor = editor
                entry._is_untitled = is_untitled
                entry._original_path = editor.current_file_path if editor.current_file_path else None
                self.filename_entries.append(entry)
                
                info_box.append(entry)
                
                # File path
                path_label = Gtk.Label(label=filepath)
                path_label.set_halign(Gtk.Align.START)
                path_label.add_css_class("dim-label")
                path_label.add_css_class("caption")
                path_label.set_wrap(True)
                path_label.set_max_width_chars(40)
                path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                info_box.append(path_label)
                
                file_box.append(info_box)
                files_box.append(file_box)
            
            main_box.append(files_box)
        
        # Button box - HORIZONTAL
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_homogeneous(True)
        button_box.set_margin_top(12)
        button_box.set_margin_bottom(24)
        button_box.set_margin_start(24)
        button_box.set_margin_end(24)
        
        # Cancel button
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.on_response("cancel"))
        button_box.append(cancel_btn)
        
        # Discard All button
        discard_btn = Gtk.Button(label="Discard All")
        discard_btn.add_css_class("destructive-action")
        discard_btn.connect("clicked", lambda b: self.on_response("discard"))
        button_box.append(discard_btn)
        
        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: self.on_response("save"))
        button_box.append(save_btn)
        
        # Store save button reference
        self.save_button = save_btn
        
        main_box.append(button_box)
        
        # Set content
        self.set_content(main_box)
        
        # Set focus to Save button after dialog is shown
        def on_map(widget):
            """Called when dialog is shown - set focus to Save button"""
            if self.save_button:
                self.save_button.grab_focus()
        
        self.connect("map", on_map)
    
    def on_response(self, response):
        """Handle button click"""
        self.response = response
        self.close()
    
    def get_filename_for_editor(self, editor):
        """Get the (possibly modified) filename for an editor"""
        for entry in self.filename_entries:
            if hasattr(entry, '_editor') and entry._editor == editor:
                filename = entry.get_text().strip()
                if not filename:
                    filename = "untitled.txt"
                # Ensure .txt extension if not present
                if not '.' in filename:
                    filename += '.txt'
                return filename, entry._is_untitled, entry._original_path
        return None, False, None


class SettingsManager(GObject.Object):
    __gsignals__ = {
        'setting-changed': (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.config_dir = os.path.join(GLib.get_user_config_dir(), "svite")
        self.config_file = os.path.join(self.config_dir, "settings.json")
        self.settings = {
            "font-size": 11,
            "word-wrap": True,
            "line-numbers": True,
            "theme": "System",
            "tab-width": 4,
            "use-tabs": False,
            "auto-indent": True,
            "highlight-current-line": True,
            "highlight-brackets": True,
        }
        self.load()

    def load(self):
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    saved = json.load(f)
                    self.settings.update(saved)
        except Exception as e:
            print(f"Error loading settings: {e}")

    def save(self):
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value):
        if self.settings.get(key) != value:
            self.settings[key] = value
            self.save()
            self.emit("setting-changed", key)


class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent, settings_manager):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Preferences")
        self.settings = settings_manager

        # Appearance Page
        page_appearance = Adw.PreferencesPage()
        page_appearance.set_title("Appearance")
        page_appearance.set_icon_name("preferences-desktop-display-symbolic")

        # Display Group
        group_display = Adw.PreferencesGroup()
        group_display.set_title("Display")

        # Line Numbers
        row_lines = Adw.ActionRow()
        row_lines.set_title("Show Line Numbers")
        switch_lines = Gtk.Switch()
        switch_lines.set_active(self.settings.get_setting("line-numbers"))
        switch_lines.set_valign(Gtk.Align.CENTER)
        switch_lines.connect("notify::active", lambda w, p: self.settings.set_setting("line-numbers", w.get_active()))
        row_lines.add_suffix(switch_lines)
        group_display.add(row_lines)

        # Word Wrap
        row_wrap = Adw.ActionRow()
        row_wrap.set_title("Word Wrap")
        switch_wrap = Gtk.Switch()
        switch_wrap.set_active(self.settings.get_setting("word-wrap"))
        switch_wrap.set_valign(Gtk.Align.CENTER)
        switch_wrap.connect("notify::active", lambda w, p: self.settings.set_setting("word-wrap", w.get_active()))
        row_wrap.add_suffix(switch_wrap)
        group_display.add(row_wrap)

        # Theme
        row_theme = Adw.ActionRow()
        row_theme.set_title("Theme")
        combo_theme = Gtk.ComboBoxText()
        combo_theme.append("System", "System")
        combo_theme.append("Light", "Light")
        combo_theme.append("Dark", "Dark")

        current_theme = self.settings.get_setting("theme")
        if current_theme in ["System", "Light", "Dark"]:
            combo_theme.set_active_id(current_theme)
        else:
            combo_theme.set_active_id("System")

        combo_theme.set_valign(Gtk.Align.CENTER)
        combo_theme.connect("changed", self.on_theme_combo_changed)
        row_theme.add_suffix(combo_theme)
        group_display.add(row_theme)

        page_appearance.add(group_display)
        self.add(page_appearance)

        # Editor Page
        page_editor = Adw.PreferencesPage()
        page_editor.set_title("Editor")
        page_editor.set_icon_name("accessories-text-editor-symbolic")

        group_editor = Adw.PreferencesGroup()
        group_editor.set_title("Behavior")

        # Use Tabs
        row_tabs = Adw.ActionRow()
        row_tabs.set_title("Use Tabs")
        row_tabs.set_subtitle("Insert real tabs (\\t) instead of spaces")
        switch_tabs = Gtk.Switch()
        switch_tabs.set_active(self.settings.get_setting("use-tabs"))
        switch_tabs.set_valign(Gtk.Align.CENTER)
        switch_tabs.connect("notify::active", lambda w, p: self.settings.set_setting("use-tabs", w.get_active()))
        row_tabs.add_suffix(switch_tabs)
        group_editor.add(row_tabs)

        # Automatic Indentation
        row_indent = Adw.ActionRow()
        row_indent.set_title("Automatic Indentation")
        row_indent.set_subtitle("Preserve indentation on new line")
        switch_indent = Gtk.Switch()
        switch_indent.set_active(self.settings.get_setting("auto-indent"))
        switch_indent.set_valign(Gtk.Align.CENTER)
        switch_indent.connect("notify::active", lambda w, p: self.settings.set_setting("auto-indent", w.get_active()))
        row_indent.add_suffix(switch_indent)
        group_editor.add(row_indent)

        # Highlighting Group
        group_highlight = Adw.PreferencesGroup()
        group_highlight.set_title("Highlighting")

        # Highlight Current Line
        row_hl_line = Adw.ActionRow()
        row_hl_line.set_title("Highlight Current Line")
        switch_hl_line = Gtk.Switch()
        switch_hl_line.set_active(self.settings.get_setting("highlight-current-line"))
        switch_hl_line.set_valign(Gtk.Align.CENTER)
        switch_hl_line.connect("notify::active", lambda w, p: self.settings.set_setting("highlight-current-line", w.get_active()))
        row_hl_line.add_suffix(switch_hl_line)
        group_highlight.add(row_hl_line)

        # Highlight Matching Brackets
        row_hl_brackets = Adw.ActionRow()
        row_hl_brackets.set_title("Highlight Matching Brackets")
        switch_hl_brackets = Gtk.Switch()
        switch_hl_brackets.set_active(self.settings.get_setting("highlight-brackets"))
        switch_hl_brackets.set_valign(Gtk.Align.CENTER)
        switch_hl_brackets.connect("notify::active", lambda w, p: self.settings.set_setting("highlight-brackets", w.get_active()))
        row_hl_brackets.add_suffix(switch_hl_brackets)
        group_highlight.add(row_hl_brackets)

        page_editor.add(group_editor)
        page_editor.add(group_highlight)

        # Font Size
        row_font = Adw.ActionRow()
        row_font.set_title("Font Size")
        spin_font = Gtk.SpinButton.new_with_range(8, 72, 1)
        spin_font.set_value(self.settings.get_setting("font-size"))
        spin_font.set_valign(Gtk.Align.CENTER)
        spin_font.connect("value-changed", lambda w: self.settings.set_setting("font-size", int(w.get_value())))
        row_font.add_suffix(spin_font)
        group_editor.add(row_font)
        
        # Tab Width
        row_tab = Adw.ActionRow()
        row_tab.set_title("Tab Width")
        spin_tab = Gtk.SpinButton.new_with_range(2, 8, 1)
        spin_tab.set_value(self.settings.get_setting("tab-width"))
        spin_tab.set_valign(Gtk.Align.CENTER)
        spin_tab.connect("value-changed", lambda w: self.settings.set_setting("tab-width", int(w.get_value())))
        row_tab.add_suffix(spin_tab)
        group_editor.add(row_tab)

        self.add(page_editor)

    def on_theme_combo_changed(self, combo):
        theme = combo.get_active_id()
        if theme:
            self.settings.set_setting("theme", theme)

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Text Editor")
        self.set_default_size(800, 600)
        
        # Initialize recent files manager
        self.recent_files_manager = RecentFilesManager()

        # Create ToolbarView
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_css_class("toolbarview")
        # Header Bar
        self.header = Adw.HeaderBar()
        self.header.set_margin_top(0)
        self.header.set_margin_bottom(0)
        
        # Use Adw.WindowTitle - it's designed for header bars and handles RTL properly
        self.window_title = Adw.WindowTitle(title="Virtual Text Editor", subtitle="")
        
        # Wrapper to include modified dot + window title
        title_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_wrapper.set_halign(Gtk.Align.CENTER)
        title_wrapper.set_valign(Gtk.Align.CENTER)
        
        # Modified dot indicator
        self.header_modified_dot = Gtk.DrawingArea()
        self.header_modified_dot.set_size_request(8, 8)
        self.header_modified_dot.add_css_class("header-modified-dot")
        self.header_modified_dot.set_visible(False)
        self.header_modified_dot.set_valign(Gtk.Align.CENTER)
        
        title_wrapper.append(self.header_modified_dot)
        title_wrapper.append(self.window_title)
        
        self.header.set_title_widget(title_wrapper)

        #self.header.set_title_widget(self.window_title)
        toolbar_view.add_top_bar(self.header)

        # Container for linked buttons
        open_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        open_box.add_css_class("linked")            # <- This merges the buttons visually
        open_box.set_margin_start(2)
        open_box.set_margin_top(2) 
        # Left "Open" button
        self.open_button = Gtk.Button(label="Open")
        self.open_button.connect("clicked", self.open_file)
        self.open_button.add_css_class("flat")      # Keep Libadwaita look
        self.open_button.set_margin_start(0)
        self.open_button.set_margin_end(0)        
        open_box.append(self.open_button)

        # Right dropdown arrow button
        self.open_menu_button = Gtk.MenuButton()
        self.open_menu_button.set_icon_name("pan-down-symbolic")
        self.open_menu_button.set_margin_start(0)
        self.open_menu_button.set_margin_end(0)
        self.update_recent_files_menu()                # <- Correct
        self.open_menu_button.add_css_class("flat")
        open_box.append(self.open_menu_button)

        # Put in headerbar
        self.header.pack_start(open_box)
        # New Tab button
        btn_new = Gtk.Button()
        btn_new.set_margin_top(1)
        btn_new.set_margin_bottom(1)  
        btn_new.set_icon_name("tab-new-symbolic")
        btn_new.set_tooltip_text("New Tab (Ctrl+T)")
        btn_new.connect("clicked", self.on_new_tab)
        self.header.pack_start(btn_new)
        self.add_css_class("view")

        # Add menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        
        # Create PopoverMenu from model but add custom child for zoom
        menu_model = self.create_menu()
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.add_child(self._create_zoom_widget(), "zoom_controls")
        
        menu_button.set_popover(popover)
        self.header.pack_end(menu_button)

        # Tab dropdown button (for file list)
        self.tab_dropdown = Gtk.MenuButton()
        self.tab_dropdown.set_icon_name("pan-down-symbolic")

        self.header.pack_end(self.tab_dropdown)
        
        
        # Tab List (ChromeTabBar) as a top bar
        self.tab_bar = ChromeTabBar()
        self.tab_bar.connect('tab-reordered', self.on_tab_reordered)
        toolbar_view.add_top_bar(self.tab_bar)
        
        # Progress bar widget (shown during file loading)
        self.progress_bar_widget = ProgressBarWidget()
        toolbar_view.add_top_bar(self.progress_bar_widget)

        # Tab View (Content)
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True)
        self.tab_view.set_hexpand(True)
        self.tab_view.connect("notify::selected-page", self.on_page_selection_changed)
        toolbar_view.set_content(self.tab_view)
        
        # Status bar at bottom
        self.status_bar = StatusBar(self)
        toolbar_view.add_bottom_bar(self.status_bar)

        self.set_content(toolbar_view)
        
        # Setup actions
        self.setup_actions()
        self.setup_tab_actions()

        # Connect to settings
        self.get_application().settings_manager.connect("setting-changed", self.on_setting_changed_win)
        
        # Add initial tab
        self.add_tab()
        
        # Add key controller for shortcuts (Ctrl+Tab)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self.on_window_key_pressed)
        self.add_controller(key_ctrl)
        
        # Setup drop targets for drag-and-drop functionality
        self._setup_drop_targets()
        
        # Handle window close request
        self.connect("close-request", self.on_close_request)
        
        # Connect to theme changes
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self.on_theme_changed)
        
        # Start periodic cursor position update (every 100ms)
        GLib.timeout_add(100, self.update_status_bar_cursor_position)


    def _setup_drop_targets(self):
        """Setup drop targets for various drag-and-drop operations"""
        
        # Tab view drop target for files only
        file_drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        file_drop.connect('drop', self._on_editor_area_drop)
        self.tab_view.add_controller(file_drop)


    def _on_editor_area_drop(self, target, value, x, y):
        """Handle file drop on editor area"""
        if not isinstance(value, Gdk.FileList):
            return False
        
        files = value.get_files()
        if not files:
            return False
        
        # Open each dropped file
        for gfile in files:
            file_path = gfile.get_path()
            if file_path:
                # Check if it's a text file (basic check)
                try:
                    # Try to read as text
                    with open(file_path, 'r', encoding='utf-8') as f:
                        f.read(1024)  # Test read
                    # If successful, open it in a new tab
                    self.add_tab(file_path)
                except (UnicodeDecodeError, IOError):
                    # Not a text file or can't read
                    print(f"Skipping non-text file: {file_path}")
                    continue
        
        return True


    def on_window_key_pressed(self, controller, keyval, keycode, state):
        # Ctrl+Tab / Ctrl+Shift+Tab / Ctrl+T / Ctrl+O / Ctrl+Shift+S / Ctrl+W
        if state & Gdk.ModifierType.CONTROL_MASK:
            # Tab switching
            if keyval == Gdk.KEY_Tab or keyval == Gdk.KEY_ISO_Left_Tab:
                direction = 1
                if (state & Gdk.ModifierType.SHIFT_MASK) or keyval == Gdk.KEY_ISO_Left_Tab:
                    direction = -1
                
                n_pages = self.tab_view.get_n_pages()
                if n_pages > 1:
                    current_page = self.tab_view.get_selected_page()
                    current_idx = self.tab_view.get_page_position(current_page)
                    
                    new_idx = (current_idx + direction) % n_pages
                    new_page = self.tab_view.get_nth_page(new_idx)
                    self.tab_view.set_selected_page(new_page)
                    return True

            # Intercept Ctrl+Home/End to prevent tab switching (Adw.TabView default)
            # and force editor navigation instead
            elif keyval in (Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_KP_Home, Gdk.KEY_KP_End):
                page = self.tab_view.get_selected_page()
                if page:
                    root = page.get_child()
                    if hasattr(root, '_editor'):
                        editor = root._editor
                        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
                        
                        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
                            editor.view.ctrl.move_document_start(extend_selection=shift)
                        else:
                            editor.view.ctrl.move_document_end(extend_selection=shift)
                        
                        editor.view.keep_cursor_visible()
                        editor.view.queue_draw()
                        return True
            
            # Ctrl+T: New Tab
            elif keyval == Gdk.KEY_t or keyval == Gdk.KEY_T:
                self.on_new_tab(None)
                return True
                
            # Ctrl+O: Open File
            elif keyval == Gdk.KEY_o or keyval == Gdk.KEY_O:
                self.open_file(None)
                return True
                
            # Ctrl+Shift+S: Save As
            elif (keyval == Gdk.KEY_s or keyval == Gdk.KEY_S) and (state & Gdk.ModifierType.SHIFT_MASK):
                self.on_save_as(None, None)
                return True
            
            # Ctrl+W: Close Tab
            elif keyval == Gdk.KEY_w or keyval == Gdk.KEY_W:
                page = self.tab_view.get_selected_page()
                if page:
                    self.close_tab(page)
                return True

            # Ctrl+F: Find
            elif keyval == Gdk.KEY_f or keyval == Gdk.KEY_F:
                page = self.tab_view.get_selected_page()
                if page:
                    # page.get_child() gives tab_root
                    # tab_root._editor gives EditorPage
                    root = page.get_child()
                    if hasattr(root, '_editor'):
                        editor = root._editor
                        if editor.find_bar:
                            editor.find_bar.show_search()
                return True
                
            # Ctrl+H: Replace
            elif keyval == Gdk.KEY_h or keyval == Gdk.KEY_H:
                page = self.tab_view.get_selected_page()
                if page:
                    root = page.get_child()
                    if hasattr(root, '_editor'):
                        editor = root._editor
                        if editor.find_bar:
                            editor.find_bar.show_replace()
                return True
                
        return False
    
    def on_close_request(self, window):
        """Handle window close request - check for unsaved changes"""
        # Collect all modified editors
        modified_editors = []
        for page in [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]:
            for tab in self.tab_bar.tabs:
                if hasattr(tab, '_page') and tab._page == page:
                    if tab.has_css_class("modified"):
                        editor = page.get_child()._editor
                        modified_editors.append(editor)
                    break
        
        # If there are modified files, show save dialog
        if modified_editors:
            def on_response(response, dialog):
                if response == "cancel":
                    return
                elif response == "discard":
                    # Just close the window
                    self.destroy()
                elif response == "save":
                    # Save all checked files
                    if dialog and hasattr(dialog, 'checkboxes'):
                        for check in dialog.checkboxes:
                            if check.get_active() and hasattr(check, '_editor'):
                                editor = check._editor
                                
                                # Get filename from dialog if available
                                filename_from_dialog = None
                                is_untitled = False
                                if hasattr(dialog, 'get_filename_for_editor'):
                                    filename_from_dialog, is_untitled, _ = dialog.get_filename_for_editor(editor)
                                
                                if editor.current_file_path:
                                    # Save existing file
                                    self.save_file(editor, editor.current_file_path)
                                elif filename_from_dialog and is_untitled:
                                    # Auto-save untitled file with provided filename
                                    default_dir = os.path.expanduser("~/Documents")
                                    if not os.path.exists(default_dir):
                                        default_dir = os.path.expanduser("~")
                                    
                                    save_path = os.path.join(default_dir, filename_from_dialog)
                                    
                                    # Save directly (overwrite if exists)
                                    try:
                                        self.save_file(editor, save_path)
                                    except Exception as e:
                                        print(f"Error saving {filename_from_dialog}: {e}")
                    
                    self.destroy()
            
            self.show_save_changes_dialog(modified_editors, on_response)
            return True  # Prevent default close
        
        return False  # Allow close
    
    def on_theme_changed(self, style_manager, pspec):
        """Handle theme change - update all editor renderers"""
        is_dark = style_manager.get_dark()
        
        # Update all editor renderers
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            editor = page.get_child()._editor
            editor.view.renderer.update_colors_for_theme(is_dark)
            editor.view.queue_draw()
        
        # Update scrollbar CSS to match editor background
        # Get the app instance and update its CSS
        app = self.get_application()
        if app and hasattr(app, 'update_scrollbar_css'):
            # Use the same color as editor background
            if is_dark:
                app.update_scrollbar_css(0.10, 0.10, 0.10, 1.0)
            else:
                app.update_scrollbar_css(0.98, 0.98, 0.98, 1.0)
    
    def update_recent_files_menu(self):
        """Update the recent files dropdown menu"""
        menu = Gio.Menu()
        recent_files = self.recent_files_manager.get_recent_files()
        
        if recent_files:
            for file_path in recent_files:
                # Create menu item with filename
                filename = os.path.basename(file_path)
                menu_item = Gio.MenuItem.new(filename, None)
                # Store the full path as action target
                menu_item.set_action_and_target_value(
                    "win.open_recent",
                    GLib.Variant.new_string(file_path)
                )
                menu.append_item(menu_item)
            
            # Add separator and clear option
            menu.append_section(None, Gio.Menu())
            menu.append("Clear Recent Files", "win.clear_recent")
        else:
            menu.append("No recent files", None)
        
        self.open_menu_button.set_menu_model(menu)
    
    def find_tab_with_file(self, file_path):
        """Find and return the page that has the given file open, or None"""
        if not file_path:
            return None
        
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            editor = page.get_child()._editor
            if editor.current_file_path == file_path:
                return page
        return None
    
    def activate_tab_with_file(self, file_path):
        """Activate the tab that has the given file open. Returns True if found."""
        page = self.find_tab_with_file(file_path)
        if page:
            self.tab_view.set_selected_page(page)
            # Focus the editor
            editor = page.get_child()._editor
            editor.view.grab_focus()
            return True
        return False

    def get_current_page(self):
        page = self.tab_view.get_selected_page()
        if page:
            return page.get_child()._editor
        return None
    
    def update_status_bar_cursor_position(self):
        """Periodically update cursor position in status bar"""
        editor = self.get_current_page()
        if editor:
            self.status_bar.update_cursor_position(editor.buf.cursor_line, editor.buf.cursor_col)
            self.status_bar.update_insert_mode(editor.insert_mode)
        return True  # Continue calling


    def on_new_tab(self, btn):
        self.add_tab()
        
    def get_next_untitled_number(self):
        """Get the next available Untitled number using global counter"""
        app = self.get_application()
        if app and isinstance(app, VirtualTextEditor):
            return app.get_next_global_untitled_number()
        # Fallback if app is not available (shouldn't happen)
        return 1

    
    def add_tab(self, path=None):
        # ----- PATCH: correct initial title when loading a file -----
        if path:
            # Use the real filename immediately → prevents "Untitled" flash
            filename = os.path.basename(path)
            editor = EditorPage(filename)
            
            # Only update if the path is actually changing (e.g., after a save-as)
            if editor.current_file_path != path:
                editor.current_file_path = path
                
                # NOTE: Syntax highlighting will be set in load_file_into_editor
                # after the file content is actually loaded. Don't set it here
                # on an empty buffer or TreeSitter will parse empty content.

                # Update header title if this is the active page
                if editor == self.get_current_page():
                    self.update_header_title(path)

            editor.untitled_number = None  # Not an untitled file
        else:
            # Normal Untitled logic
            if self.tab_view.get_n_pages() == 0:
                # First tab - use global counter
                untitled_num = self.get_next_untitled_number()
                untitled_title = f"Untitled {untitled_num}"
                editor = EditorPage(untitled_title)
                editor.is_initial_empty_tab = True
                editor.untitled_number = untitled_num  # Store the number
            else:
                # New tab → "Untitled N"
                untitled_num = self.get_next_untitled_number()
                untitled_title = f"Untitled {untitled_num}"
                editor = EditorPage(untitled_title)
                editor.untitled_number = untitled_num  # Store the number

        # ----- END PATCH -----
        
        # Create overlay layout for editor (scrollbars float on top)
        overlay, editor = self._create_editor_overlay(editor)

        # Create TabRoot (Gtk.Box) to hold the overlay (and future splits)
        tab_root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tab_root.append(overlay)
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        
        # Store reference to editor on tab_root for easy access
        tab_root._editor = editor
        # Store reference to overlay on editor for split logic
        editor._overlay = overlay

        page = self.tab_view.append(tab_root)
        page.set_title(editor.get_title())
        self.tab_view.set_selected_page(page)

        # Add ChromeTab to ChromeTabBar
        self.add_tab_button(page)

        # Focus the new editor view
        editor.view.grab_focus()

        # Load file if path provided (async)
        if path:
            self.load_file_into_editor(editor, path)

        # Update UI state
        self.update_ui_state()

        # Apply settings
        self.apply_settings_to_editor(editor)

        return editor

    def _create_editor_overlay(self, editor, add_close_button=False):
        """Helper to create editor overlay with scrollbars
        
        Args:
            editor: EditorPage instance
            add_close_button: If True, adds a close button for split views
        """
        
        # Create FindReplaceBar
        editor.find_bar = FindReplaceBar(editor)
        
        # Create container for FindBar + Editor View
        # We want the FindBar to be BELOW the editor view
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        overlay = Gtk.Overlay()
        
        main_box.append(overlay)
        main_box.append(editor.find_bar)
        
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)

            
        # Setup scrollbars
        vscroll = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=editor.view.vadj
        )
        hscroll = Gtk.Scrollbar(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=editor.view.hadj
        )

        vscroll.add_css_class("overlay-scrollbar")
        hscroll.add_css_class("hscrollbar-overlay")
        vscroll.set_visible(False)
        hscroll.set_visible(False)

        # Add drag detection to vertical scrollbar
        drag_gesture = Gtk.GestureDrag()
        drag_gesture.connect("drag-begin",
                             lambda g, x, y: self.on_vscroll_drag_begin(g, x, y, editor))
        drag_gesture.connect("drag-end",
                             lambda g, x, y: self.on_vscroll_drag_end(g, x, y, editor))
        vscroll.add_controller(drag_gesture)

        # Give view references to scrollbars
        editor.view.vscroll = vscroll
        editor.view.hscroll = hscroll

        # Connect buffer changed
        if hasattr(editor.buf, 'add_observer'):
            editor.buf.add_observer(lambda *_: self.on_buffer_changed(editor))
        elif hasattr(editor.buf, 'connect'):
            editor.buf.connect("changed", lambda *_: self.on_buffer_changed(editor))


        # Set up overlay: editor as base, scrollbars on top
        overlay.set_child(editor.view)
        
        # Position scrollbars at edges using halign/valign
        vscroll.set_halign(Gtk.Align.END)
        vscroll.set_valign(Gtk.Align.FILL)
        overlay.add_overlay(vscroll)
        
        hscroll.set_halign(Gtk.Align.FILL)
        hscroll.set_valign(Gtk.Align.END)
        overlay.add_overlay(hscroll)

        # ---------------------------------------------------------
        # Busy Overlay (Spinner)
        # ---------------------------------------------------------
        busy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        busy_box.add_css_class("busy-overlay") # Add rounding/background in CSS
        busy_box.set_halign(Gtk.Align.CENTER)
        busy_box.set_valign(Gtk.Align.CENTER)
        
        # Opaque background for visibility
        # We can implement this via CSS provider, or just use a frame style
        
        spinner = Gtk.Spinner()
        spinner.set_size_request(32, 32)
        
        busy_label = Gtk.Label(label="Processing...")
        busy_label.add_css_class("title-2")
        
        busy_box.append(spinner)
        busy_box.append(busy_label)
        
        # Initially hidden
        busy_box.set_visible(False)
        
        overlay.add_overlay(busy_box)
        
        # Bind to editor view for control
        editor.view._busy_overlay = busy_box
        editor.view._busy_spinner = spinner
        editor.view._busy_label = busy_label

        # Add close button for split views
        if add_close_button:
            close_btn = Gtk.Button()
            close_btn.set_icon_name("window-close-symbolic")
            close_btn.add_css_class("flat")
            close_btn.add_css_class("circular")
            close_btn.set_tooltip_text("Close Split")
            close_btn.set_halign(Gtk.Align.END)
            close_btn.set_valign(Gtk.Align.START)
            close_btn.set_margin_top(6)
            close_btn.set_margin_end(6)
            close_btn.connect("clicked", lambda btn: self._close_split(overlay))
            overlay.add_overlay(close_btn)
            overlay._close_button = close_btn

        overlay._editor = editor
        # Return the main_box instead of just overlay, as it's now the root of this editor part
        # CAUTION: modify callers if they expect overlay specifically.
        # add_tab does: overlay, editor = self._create_editor_overlay(editor)
        # then tab_root.append(overlay)
        # returning main_box is fine as it's a widget.
        return main_box, editor


    def add_tab_button(self, page):
        editor = page.get_child()._editor
        title = editor.get_title()
        
        tab = ChromeTab(title=title)
        tab._page = page
        
        # Connect signals
        tab.connect('activate-requested', self.on_tab_activated)
        tab.connect('close-requested', self.on_tab_close_requested)
        
        self.tab_bar.add_tab(tab)
        
        # Set active state
        self.update_active_tab()
        
        # Update dropdown
        self.update_tab_dropdown()

    def on_tab_activated(self, tab):
        if hasattr(tab, '_page'):
            self.tab_view.set_selected_page(tab._page)
            # Focus the editor view
            editor = tab._page.get_child()._editor
            editor.view.grab_focus()

    def on_page_selection_changed(self, tab_view, pspec):
        self.update_active_tab()
        self.update_header_title()
        # Focus the selected editor
        editor = self.get_current_page()
        if editor:
            editor.view.grab_focus()
            # Update status bar for new editor
            self.status_bar.update_for_editor(editor)

    def on_tab_close_requested(self, tab):
        if hasattr(tab, '_page'):
            self.close_tab(tab._page)

    def on_tab_reordered(self, tab_bar, tab, new_index):
        """Sync Adw.TabView order with ChromeTabBar order"""
        if hasattr(tab, '_page'):
            # Only reorder if the page belongs to this view
            # This prevents errors during cross-window drag when the tab is added
            # to the new window's bar but the page hasn't been transferred yet.
            
            # Safe check: iterate pages to see if this page belongs to the view
            # We cannot use get_page_position() because it asserts ownership!
            page_belongs_to_view = False
            n_pages = self.tab_view.get_n_pages()
            for i in range(n_pages):
                if self.tab_view.get_nth_page(i) == tab._page:
                    page_belongs_to_view = True
                    break
            
            if page_belongs_to_view:
                # Reorder the page in Adw.TabView
                self.tab_view.reorder_page(tab._page, new_index)
                # Update dropdown to reflect new order
                self.update_tab_dropdown()

    def setup_tab_actions(self):
        """Setup actions for tab context menu"""
        
        # Helper to add action with string parameter
        def add_action(name, callback):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", callback)
            self.add_action(action)
            
        add_action("tab_move_left", self.on_tab_move_left)
        add_action("tab_move_right", self.on_tab_move_right)
        add_action("tab_move_new_window", self.on_tab_move_new_window)
        add_action("tab_split_horizontal", self.on_tab_split_horizontal)
        add_action("tab_split_vertical", self.on_tab_split_vertical)
        
        add_action("tab_close_left", self.on_tab_close_left)
        add_action("tab_close_right", self.on_tab_close_right)
        add_action("tab_close_other", self.on_tab_close_other)
        add_action("tab_close", self.on_tab_close_action)

        # Add accelerators - targeting "current" (active) tab
        app = self.get_application()
        if app:
            # Note: detailed action name includes target parameter
            app.set_accels_for_action("win.tab_move_left('current')", ["<Ctrl><Shift>Page_Up"])
            app.set_accels_for_action("win.tab_move_right('current')", ["<Ctrl><Shift>Page_Down"])
            app.set_accels_for_action("win.tab_move_new_window('current')", ["<Ctrl><Shift>n"])

    def _get_target_page(self, parameter):
        """Get page from action parameter (string: 'current' or index)"""
        val = parameter.get_string()
        
        if val == 'current':
            return self.tab_view.get_selected_page()
            
        try:
            idx = int(val)
            if 0 <= idx < self.tab_view.get_n_pages():
                return self.tab_view.get_nth_page(idx)
        except ValueError:
            pass
            
        return None

    def on_tab_move_left(self, action, parameter):
        page = self._get_target_page(parameter)
        if not page: return
        
        idx = self.tab_view.get_page_position(page)
        if idx > 0:
            # Reorder in ChromeTabBar - this emits signal to sync TabView
            for tab in self.tab_bar.tabs:
                if getattr(tab, '_page', None) == page:
                    self.tab_bar.reorder_tab(tab, idx - 1)
                    break

    def on_tab_move_right(self, action, parameter):
        page = self._get_target_page(parameter)
        if not page: return
        
        idx = self.tab_view.get_page_position(page)
        if idx < self.tab_view.get_n_pages() - 1:
            # Reorder in ChromeTabBar - this emits signal to sync TabView
            for tab in self.tab_bar.tabs:
                if getattr(tab, '_page', None) == page:
                    self.tab_bar.reorder_tab(tab, idx + 1)
                    break

    def on_tab_move_new_window(self, action, parameter):
        page = self._get_target_page(parameter)
        if not page: return
        
        # Get the tab title and modified state from the page
        page_title = page.get_title()
        page_is_modified = any(
            tab.has_css_class("modified") 
            for tab in self.tab_bar.tabs 
            if hasattr(tab, '_page') and tab._page == page
        )
        
        # Get the TabRoot which may contain splits
        tab_root = page.get_child()
        
        # OPTIMIZATION: Instead of serializing, we'll directly transfer the widget tree!
        # This avoids copying gigabytes of file content.
        
        # Create new window
        app = self.get_application()
        
        # Before creating new window, scan all existing windows to ensure counter is in sync
        if app and isinstance(app, VirtualTextEditor):
            for window in app.get_windows():
                if isinstance(window, EditorWindow):
                    app.scan_and_register_untitled_numbers(window)
        
        new_window = EditorWindow(app)
        new_window.present()
        
        # Remove the initial empty tab that was created automatically
        initial_page = new_window.tab_view.get_selected_page()
        initial_tab_root = initial_page.get_child()
        
        # Release the untitled number from the initial tab before removing it
        if app and isinstance(app, VirtualTextEditor):
            initial_editor = initial_tab_root._editor
            if hasattr(initial_editor, 'untitled_number') and initial_editor.untitled_number is not None:
                app.release_untitled_number(initial_editor.untitled_number)
        
        # Remove the initial tab completely
        new_window.tab_view.close_page(initial_page)
        for tab in new_window.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == initial_page:
                new_window.tab_bar.remove_tab(tab)
                break
        
        # CRITICAL OPTIMIZATION: Reparent the existing tab_root to the new window
        # This preserves all the live editor state without copying anything!
        
        # Remove tab_root from the old page (this unparents it)
        old_child = page.get_child()
        if old_child is not None:
            parent = old_child.get_parent()
            
            # Single-child containers (Overlay, ScrolledWindow, Paned, Frame, ListView, Bin-like)
            if hasattr(parent, "set_child"):
                parent.set_child(None)
            
            # Multi-child containers (Box, Grid, FlowBox, etc.)
            elif hasattr(parent, "remove"):
                parent.remove(old_child)

        
        # Add the same tab_root to a new page in the new window
        new_page = new_window.tab_view.append(tab_root)
        new_page.set_title(page_title)
        
        # Use idle_add to set selected page to ensure window is ready
        def select_new_page():
            if new_page:
                new_window.tab_view.set_selected_page(new_page)
            return False
        GLib.idle_add(select_new_page)
        
        # Add ChromeTab to ChromeTabBar
        new_window.add_tab_button(new_page)
        
        # Update modified state on the chrome tab
        for tab in new_window.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == new_page:
                if page_is_modified:
                    tab.add_css_class("modified")
                break
        
        # Update UI
        new_window.update_ui_state()
        new_window.update_header_title()
        
        # Focus the transferred editor
        primary_editor = tab_root._editor
        if primary_editor:
            primary_editor.view.grab_focus()
        
        # Mark the tab to not release untitled numbers since they were transferred
        page._untitled_numbers_transferred = True
        
        # Remove the ChromeTab from the old window's ChromeTabBar before closing
        # This prevents the tab from hanging in the old window
        for tab in self.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == page:
                self.tab_bar.remove_tab(tab)
                break
        
        # Close the original page (it's now empty, so this is fast)
        self.tab_view.close_page(page)
        
        # Update UI state in the old window to hide tab bar if only 1 tab remains
        self.update_ui_state()
        self.update_tab_dropdown()

    def grab_focus_editor(self):
        """Helper to grab focus on the current editor view"""
        editor = self.get_current_page()
        if editor:
            editor.view.grab_focus()


    def close_tab_after_drag(self, tab_index):
        """Close a tab after successful cross-window drag"""
        if 0 <= tab_index < self.tab_view.get_n_pages():
            page = self.tab_view.get_nth_page(tab_index)
            
            # Mark the page to not release untitled numbers since they were transferred
            page._untitled_numbers_transferred = True
            
            # Mark as unmodified to avoid save prompt (content was transferred)
            for tab in self.tab_bar.tabs:
                if hasattr(tab, '_page') and tab._page == page:
                    tab.remove_css_class("modified")
                    break
            
            # Close the tab
            self.perform_close_tab(page)
            
            # Update UI state to hide tab bar if only 1 tab remains
            self.update_ui_state()


    def on_tab_split_horizontal(self, action, parameter):
        self._split_view(parameter, Gtk.Orientation.HORIZONTAL)

    def on_tab_split_vertical(self, action, parameter):
        self._split_view(parameter, Gtk.Orientation.VERTICAL)

    def _split_view(self, parameter, orientation):
        page = self._get_target_page(parameter)
        if not page: return
        
        # Get the TabRoot (Gtk.Box)
        tab_root = page.get_child()
        if not isinstance(tab_root, Gtk.Box):
            print("Error: Tab content is not a Box (TabRoot)")
            return
            
        # We want to split the currently focused editor in this tab
        # But for simplicity, let's just split the first child of TabRoot if it's not already split complexly
        # Or better, find the child that contains the focused widget?
        # For now, let's assume simple case: split the main content.
        
        # Get current content
        current_content = tab_root.get_first_child()
        
        # Create Paned
        paned = Gtk.Paned(orientation=orientation)
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        
        # Remove current content from root and add to paned
        tab_root.remove(current_content)
        paned.set_start_child(current_content)
        
        # Create NEW editor sharing the buffer
        # We need the original editor to get the buffer
        original_editor = getattr(current_content, '_editor', None)
        if not original_editor:
            # Try to find it if current_content is already a Paned?
            # This gets recursive. For MVP, let's just grab the one from tab_root._editor (the primary one)
            original_editor = getattr(tab_root, '_editor', None)
            
        if not original_editor:
            print("Error: Could not find original editor")
            return
            
        # Create new EditorPage but share buffer
        new_editor = EditorPage(original_editor.get_title())
        new_editor.buf = original_editor.buf # SHARE BUFFER
        new_editor.view = VirtualTextView(new_editor.buf) # New view
        new_editor.current_file_path = original_editor.current_file_path
        new_editor.untitled_number = getattr(original_editor, 'untitled_number', None)  # Share untitled number
        
        # Create overlay for new editor with close button
        new_overlay, new_editor = self._create_editor_overlay(new_editor, add_close_button=True)
        new_overlay.set_hexpand(True)
        new_overlay.set_vexpand(True)
        
        paned.set_end_child(new_overlay)
        
        # Add Paned to root
        tab_root.append(paned)
        
        # Set position to 50% after the widget is realized
        def set_split_position():
            if orientation == Gtk.Orientation.HORIZONTAL:
                # Horizontal split - use width
                width = tab_root.get_width()
                if width > 0:
                    paned.set_position(width // 2)
                    return False
            else:
                # Vertical split - use height
                height = tab_root.get_height()
                if height > 0:
                    paned.set_position(height // 2)
                    return False
            # If size not available yet, try again
            return True
        
        # Try to set position immediately, or schedule for next idle
        if not set_split_position():
            pass  # Successfully set
        else:
            GLib.idle_add(set_split_position)
        
        # Focus new editor
        new_editor.view.grab_focus()

    def _close_split(self, overlay_to_close):
        """Close a split view pane"""
        # Find the parent Paned widget
        parent = overlay_to_close.get_parent()
        if not isinstance(parent, Gtk.Paned):
            print("Error: Overlay parent is not a Paned")
            return
            
        # Get the other child (the one to keep)
        start_child = parent.get_start_child()
        end_child = parent.get_end_child()
        
        if overlay_to_close == start_child:
            keep_child = end_child
        elif overlay_to_close == end_child:
            keep_child = start_child
        else:
            print("Error: Overlay not found in Paned children")
            return
            
        # Find the TabRoot by traversing up the widget hierarchy
        # The parent could be nested Paned widgets, so we need to find the Box (TabRoot)
        current = parent
        tab_root = None
        paned_widgets = [parent]  # Collect all Paned widgets in the hierarchy
        while current:
            current_parent = current.get_parent()
            if isinstance(current_parent, Gtk.Box):
                # Found the TabRoot
                tab_root = current_parent
                break
            elif isinstance(current_parent, Gtk.Paned):
                # Found another Paned in the hierarchy
                paned_widgets.append(current_parent)
            current = current_parent
            
        if not tab_root:
            print("Error: Could not find TabRoot in widget hierarchy")
            return
            
        # Now we need to handle the replacement:
        # If parent's parent is TabRoot, simple case
        # If parent's parent is another Paned, we need to replace parent in that Paned
        
        parent_of_paned = parent.get_parent()
        
        # Find and focus the editor we're keeping
        editor_to_focus = None
        if hasattr(keep_child, '_editor'):
            editor_to_focus = keep_child._editor
        elif isinstance(keep_child, Gtk.Paned):
            # If keep_child is a Paned, focus the first editor we can find
            def find_editor(widget):
                if hasattr(widget, '_editor'):
                    return widget._editor
                if isinstance(widget, Gtk.Paned):
                    start = widget.get_start_child()
                    if start:
                        result = find_editor(start)
                        if result:
                            return result
                    end = widget.get_end_child()
                    if end:
                        return find_editor(end)
                return None
            
            editor_to_focus = find_editor(keep_child)
        
        # CRITICAL: Clear focus on ALL Paned widgets in the hierarchy
        # This prevents GTK from trying to restore focus to widgets being removed
        for paned in paned_widgets:
            paned.set_focus_child(None)
        
        # Remove both children from the Paned we're closing
        parent.set_start_child(None)
        parent.set_end_child(None)
        
        if parent_of_paned == tab_root:
            # Simple case: Paned is direct child of TabRoot
            tab_root.remove(parent)
            tab_root.append(keep_child)
        elif isinstance(parent_of_paned, Gtk.Paned):
            # Nested case: Paned is child of another Paned
            # Replace the closing Paned with the kept child in the parent Paned
            if parent_of_paned.get_start_child() == parent:
                parent_of_paned.set_start_child(keep_child)
            elif parent_of_paned.get_end_child() == parent:
                parent_of_paned.set_end_child(keep_child)
        else:
            print(f"Error: Unexpected parent type: {type(parent_of_paned)}")
            return
        
        # Now grab focus to the kept editor after reparenting is complete
        if editor_to_focus:
            editor_to_focus.view.grab_focus()


    def on_tab_close_left(self, action, parameter):
        page = self._get_target_page(parameter)
        if not page: return
        
        target_idx = self.tab_view.get_page_position(page)
        
        # Close all pages with index < target_idx
        # We must be careful about indices shifting as we close
        # Easiest is to close from 0 up to target_idx-1 repeatedly
        
        # Actually, just collect pages to close first
        pages_to_close = []
        for i in range(target_idx):
            pages_to_close.append(self.tab_view.get_nth_page(i))
            
        for p in pages_to_close:
            self.close_tab(p)

    def on_tab_close_right(self, action, parameter):
        page = self._get_target_page(parameter)
        if not page: return
        
        target_idx = self.tab_view.get_page_position(page)
        n_pages = self.tab_view.get_n_pages()
        
        pages_to_close = []
        for i in range(target_idx + 1, n_pages):
            pages_to_close.append(self.tab_view.get_nth_page(i))
            
        for p in pages_to_close:
            self.close_tab(p)

    def on_tab_close_other(self, action, parameter):
        page = self._get_target_page(parameter)
        if not page: return
        
        pages_to_close = []
        n_pages = self.tab_view.get_n_pages()
        for i in range(n_pages):
            p = self.tab_view.get_nth_page(i)
            if p != page:
                pages_to_close.append(p)
                
        for p in pages_to_close:
            self.close_tab(p)

    def on_tab_close_action(self, action, parameter):
        page = self._get_target_page(parameter)
        if page:
            self.close_tab(page)

    def show_save_changes_dialog(self, modified_editors, callback):
        """Show dialog for saving changes with list of modified files"""
        dialog = SaveChangesDialog(self, modified_editors)
        
        # Flag to track if callback has been called
        callback_called = [False]
        
        def on_close(dialog_window):
            """Handle dialog close"""
            # Only call callback once
            if not callback_called[0]:
                callback_called[0] = True
                response = dialog_window.response if dialog_window.response else "cancel"
                callback(response, dialog_window)
            return False  # Allow dialog to close
        
        dialog.connect("close-request", on_close)
        dialog.present()
    
    def close_tab(self, page):
        # Get the editor for this page
        editor = page.get_child()._editor
        
        # Check if this tab is modified
        is_modified = False
        for tab in self.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == page:
                is_modified = tab.has_css_class("modified")
                break
        
        # If modified, show save dialog
        if is_modified:
            self.show_save_changes_dialog([editor], lambda response, dialog: self.finish_close_tab(page, response, dialog))
        else:
            self.finish_close_tab(page, "discard", None)
    
    def finish_close_tab(self, page, response, dialog):
        """Complete the tab closing operation after save dialog"""
        if response == "cancel":
            return
        
        editor = page.get_child()._editor
        
        if response == "discard":
            # Just close the tab without saving
            self.perform_close_tab(page)
        elif response == "save":
            # Check which files to save from dialog checkboxes
            if dialog and hasattr(dialog, 'checkboxes'):
                # Check if this editor's checkbox is selected
                should_save = False
                for check in dialog.checkboxes:
                    if hasattr(check, '_editor') and check._editor == editor:
                        should_save = check.get_active()
                        break
                
                if not should_save:
                    # Skip saving this file
                    self.perform_close_tab(page)
                    return
            
            # Get filename from dialog (if available)
            filename_from_dialog = None
            is_untitled = False
            original_path = None
            if dialog and hasattr(dialog, 'get_filename_for_editor'):
                filename_from_dialog, is_untitled, original_path = dialog.get_filename_for_editor(editor)
            
            # If file has path, save it
            if editor.current_file_path:
                self.save_file(editor, editor.current_file_path)
                self.perform_close_tab(page)
            elif filename_from_dialog and is_untitled:
                # Auto-save untitled file with the provided filename
                # Default to ~/Documents or current directory
                default_dir = os.path.expanduser("~/Documents")
                if not os.path.exists(default_dir):
                    default_dir = os.path.expanduser("~")
                
                save_path = os.path.join(default_dir, filename_from_dialog)
                
                # Check if file already exists
                if os.path.exists(save_path):
                    # Show save-as dialog with suggested filename
                    dialog_save = Gtk.FileDialog()
                    dialog_save.set_initial_name(filename_from_dialog)
                    
                    # Set initial folder
                    try:
                        gfile = Gio.File.new_for_path(default_dir)
                        dialog_save.set_initial_folder(gfile)
                    except:
                        pass
                    
                    def done(dialog_save_obj, result):
                        try:
                            f = dialog_save_obj.save_finish(result)
                            path = f.get_path()
                            self.save_file(editor, path)
                            self.perform_close_tab(page)
                        except:
                            # User cancelled, don't close
                            return
                    
                    dialog_save.save(self, None, done)
                    return
                else:
                    # Save directly
                    self.save_file(editor, save_path)
                    self.perform_close_tab(page)
            else:
                # Show save-as dialog for untitled files without filename
                dialog_save = Gtk.FileDialog()
                
                # Set suggested filename if available
                if filename_from_dialog:
                    dialog_save.set_initial_name(filename_from_dialog)
                
                def done(dialog_save_obj, result):
                    try:
                        f = dialog_save_obj.save_finish(result)
                        path = f.get_path()
                        self.save_file(editor, path)
                        # After saving, close the tab
                        self.perform_close_tab(page)
                    except:
                        # User cancelled save-as, don't close
                        return
                
                dialog_save.save(self, None, done)
                # Return early - the callback will handle closing
                return
    
    
    def perform_close_tab(self, page):
        """Actually remove the tab from the view"""
        # Check if untitled numbers were transferred (e.g., moved to another window)
        numbers_transferred = getattr(page, '_untitled_numbers_transferred', False)
        
        # Get the editor and release its untitled number if it has one
        editor = page.get_child()._editor
        if hasattr(editor, 'untitled_number') and editor.untitled_number is not None:
            if not numbers_transferred:
                print(f"[CLOSE TAB] Releasing untitled number {editor.untitled_number} for editor '{editor.get_title()}'")
                app = self.get_application()
                if app and isinstance(app, VirtualTextEditor):
                    app.release_untitled_number(editor.untitled_number)
            else:
                print(f"[CLOSE TAB] NOT releasing untitled number {editor.untitled_number} for editor '{editor.get_title()}' (transferred to another window)")
        else:
            print(f"[CLOSE TAB] Editor '{editor.get_title()}' has no untitled number to release")
        
        # If this is the last tab, close it and create a fresh new Untitled 1 tab
        if self.tab_view.get_n_pages() <= 1:
            # Remove from ChromeTabBar
            for tab in self.tab_bar.tabs:
                if hasattr(tab, '_page') and tab._page == page:
                    self.tab_bar.remove_tab(tab)
                    break
            
            # Remove from TabView
            self.tab_view.close_page(page)
            
            # Create a fresh new Untitled 1 tab (like on app start)
            self.add_tab()
            
            # Update UI
            self.update_ui_state()
            self.update_tab_dropdown()
            return
        
        # Remove from ChromeTabBar first (before closing the page)
        for tab in self.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == page:
                self.tab_bar.remove_tab(tab)
                break
        
        # Remove from TabView - this will automatically select another page
        self.tab_view.close_page(page)
        
        # Update UI state after the page is closed
        # Note: We don't call update_active_tab() here because the TabView
        # automatically selects a new page when close_page() is called,
        # and trying to set_selected_page on the removed page causes errors.
        # Instead, we rely on the "notify::selected-page" signal handler
        # (on_page_selection_changed) to update the active tab.
        self.update_ui_state()
        self.update_tab_dropdown()

    def update_active_tab(self):
        selected_page = self.tab_view.get_selected_page()
        for tab in self.tab_bar.tabs:
            if hasattr(tab, '_page'):
                is_active = (tab._page == selected_page)
                tab.set_active(is_active)
            
        # Force update of separators to hide them around the new active tab
        self.tab_bar.update_separators()

    def update_ui_state(self):
        """Update UI elements based on state (e.g. tab bar visibility)"""
        n_tabs = len(self.tab_bar.tabs)
        self.tab_bar.set_visible(n_tabs > 1)
        self.update_header_title()

    def update_header_title(self):
        """Update header bar title and subtitle based on current tab"""

        editor = self.get_current_page()

        if not editor:
            # No file open
            self.header_modified_dot.set_visible(False)
            self.window_title.set_title("Virtual Text Editor")
            self.window_title.set_subtitle("")
            return

        # Detect modified state from the current tab
        is_modified = False
        current_page = self.tab_view.get_selected_page()
        for tab in self.tab_bar.tabs:
            if getattr(tab, "_page", None) == current_page:
                is_modified = getattr(tab, "_is_modified", False)
                break

        self.header_modified_dot.set_visible(is_modified)

        # Title + subtitle
        if editor.current_file_path:
            filename = os.path.basename(editor.current_file_path)

            # Compress $HOME → '~'
            home = os.path.expanduser("~")
            parent_dir = os.path.dirname(editor.current_file_path)
            short_parent = parent_dir.replace(home, "~")

            self.window_title.set_title(filename)
            self.window_title.set_subtitle(short_parent)
            
            # Window title: "filename - Virtual Text Editor"
            self.set_title(f"{filename} - Virtual Text Editor")
            
        else:
            # Untitled file
            title = editor.get_title()
            
            # Special case: if only one tab exists, it's "Untitled 1", and it's not modified,
            # show "Virtual Text Editor" in the header
            if (self.tab_view.get_n_pages() == 1 and 
                title == "Untitled 1" and 
                not is_modified):
                self.window_title.set_title("Virtual Text Editor")
                self.set_title("Virtual Text Editor")
            else:
                # Show the actual title (Untitled 1, Untitled 2, etc.) when modified or multiple tabs
                self.window_title.set_title(title)
                self.set_title(f"{title} - Virtual Text Editor")
            
            self.window_title.set_subtitle("")



    def update_tab_title(self, page):
        """Update tab title based on file path"""
        editor = page.get_child()._editor
        path = editor.current_file_path
        
        # Get filename for tab title
        if path:
            filename = os.path.basename(path)
        else:
            filename = "Untitled"
        
        page.set_title(filename)
        
        # Update chrome tab label
        for tab in self.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == page:
                tab.set_title(filename)
                break
        
        # Update header if this is the active page
        if page == self.tab_view.get_selected_page():
            self.update_header_title()
        
        # Update dropdown
        self.update_tab_dropdown()
    


    def create_menu(self):
        """Create the application menu model"""
        menu = Gio.Menu()
        
        # Actions
        menu.append("New Window", "win.new_window")
        menu.append("Find...", "win.find")
        menu.append("Save", "win.save")
        menu.append("Save As...", "win.save-as")
        menu.append("Save Copy As...", "win.save-copy")
        
        # Zoom Section (Custom Widget)
        # We use a placeholder item with a custom attribute
        zoom_item = Gio.MenuItem.new("Zoom", None)
        zoom_item.set_attribute_value("custom", GLib.Variant.new_string("zoom_controls"))
        menu.append_item(zoom_item)
        
        # View Submenu
        view_submenu = Gio.Menu()
        view_submenu.append("Show Line Numbers", "win.toggle_line_numbers")
        view_submenu.append("Word Wrap", "win.toggle_word_wrap")
        menu.append_submenu("View", view_submenu)
    
        # Encoding Submenu
        encoding_submenu = Gio.Menu()
        encoding_submenu.append("UTF-8", "win.encoding::utf-8")
        encoding_submenu.append("UTF-8 with BOM", "win.encoding::utf-8-sig")
        encoding_submenu.append("UTF-16 LE", "win.encoding::utf-16le")
        encoding_submenu.append("UTF-16 BE", "win.encoding::utf-16be")
        menu.append_submenu("Encoding", encoding_submenu)

        menu.append("Preferences", "win.preferences")
        
        return menu

    def _create_zoom_widget(self):
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        zoom_box.set_halign(Gtk.Align.FILL)
        zoom_box.add_css_class("linked")
        zoom_box.set_margin_bottom(6)
        zoom_box.set_margin_top(6)
        zoom_box.set_margin_start(12)
        zoom_box.set_margin_end(12)

        btn_minus = Gtk.Button(label="-")
        btn_minus.set_action_name("win.zoom_out")
        btn_minus.set_hexpand(True)
        
        btn_reset = Gtk.Button(label="100%")
        btn_reset.set_action_name("win.zoom_reset")
        btn_reset.add_css_class("flat") # Make it look like a label
        btn_reset.set_hexpand(True)
        
        btn_plus = Gtk.Button(label="+")
        btn_plus.set_action_name("win.zoom_in")
        btn_plus.set_hexpand(True)
        
        zoom_box.append(btn_minus)
        zoom_box.append(btn_reset)
        zoom_box.append(btn_plus)
        return zoom_box
    
    def update_tab_dropdown(self):
        """Update the tab dropdown menu with file list"""
        self.tab_dropdown.set_visible(len(self.tab_bar.tabs) >= 8)

        if len(self.tab_bar.tabs) < 8:
            return

        menu = Gio.Menu()
        for i, tab in enumerate(self.tab_bar.tabs):
            title = tab.get_title()
            if tab.has_css_class("modified"):
                title = " ⃰" + title
            if len(title) > 32:
                title = title[:28] + "…"
            menu.append(title, f"win.tab_activate::{i}")

        self.tab_dropdown.set_menu_model(menu)
    
    def setup_actions(self):
        """Setup window actions for menu items"""
        # File Actions
        self.add_simple_action("new_tab", self.on_new_tab)
        self.add_simple_action("new_window", self.on_new_window)
        self.add_simple_action("open", lambda *_: self.open_file())
        self.add_simple_action("save", self.on_save)
        self.add_simple_action("save-as", self.on_save_as)
        self.add_simple_action("save-copy", self.on_save_copy)
        
        # View Actions (Toggles)
        self.add_simple_action("toggle_line_numbers", self.on_toggle_line_numbers)
        self.add_simple_action("toggle_word_wrap", self.on_toggle_word_wrap)
        self.add_simple_action("zoom_in", self.on_zoom_in)
        self.add_simple_action("zoom_out", self.on_zoom_out)
        self.add_simple_action("zoom_reset", self.on_zoom_reset)

        # Tools
        self.add_simple_action("preferences", self.on_preferences)
        
        # Encoding action with parameter
        encoding_action = Gio.SimpleAction.new_stateful(
            "encoding",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string("utf-8")
        )
        encoding_action.connect("activate", self.on_encoding_changed)
        self.add_action(encoding_action)
        
        # Tab activate action
        tab_activate_action = Gio.SimpleAction.new("tab_activate", GLib.VariantType.new("i"))
        tab_activate_action.connect("activate", self.on_tab_activate_from_menu)
        self.add_action(tab_activate_action)
        
        # Open recent file action
        open_recent_action = Gio.SimpleAction.new("open_recent", GLib.VariantType.new("s"))
        open_recent_action.connect("activate", self.on_open_recent)
        self.add_action(open_recent_action)
        
        # Clear recent files action
        clear_recent_action = Gio.SimpleAction.new("clear_recent", None)
        clear_recent_action.connect("activate", self.on_clear_recent)
        self.add_action(clear_recent_action)
        
        # Status bar actions
        # Tab width action
        tab_width_action = Gio.SimpleAction.new("set_tab_width", GLib.VariantType.new("s"))
        tab_width_action.connect("activate", self.on_set_tab_width)
        self.add_action(tab_width_action)
        
        # Encoding action for status bar
        encoding_sb_action = Gio.SimpleAction.new("set_encoding", GLib.VariantType.new("s"))
        encoding_sb_action.connect("activate", self.on_set_encoding)
        self.add_action(encoding_sb_action)
        
        # Line feed action
        line_feed_action = Gio.SimpleAction.new("set_line_feed", GLib.VariantType.new("s"))
        line_feed_action.connect("activate", self.on_set_line_feed)
        self.add_action(line_feed_action)



    def add_simple_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def on_new_window(self, action, param):
        app = self.get_application()
        win = EditorWindow(app)
        win.present()

    def on_save(self, action, param):
        editor = self.get_current_page()
        if editor:
            if editor.current_file_path:
                self.save_file(editor, editor.current_file_path)
            else:
                self.on_save_as(action, param)

    def on_save_copy(self, action, param):
        editor = self.get_current_page()
        if not editor: return
        
        def on_save(dialog, result):
            try:
                gfile = dialog.save_finish(result)
                path = gfile.get_path()
                text = editor.get_text()
                with open(path, 'w', encoding=editor.current_encoding) as f:
                    f.write(text)
            except Exception as e:
                print(f"Error saving copy: {e}")

        dialog = Gtk.FileDialog()
        dialog.save(self, None, on_save)

    def on_preferences(self, action, param):
        dlg = SettingsDialog(self, self.get_application().settings_manager)
        dlg.present()

    def on_toggle_line_numbers(self, action, param):
        manager = self.get_application().settings_manager
        current = manager.get_setting("line-numbers")
        manager.set_setting("line-numbers", not current)
        self.grab_focus_editor()

    def on_toggle_word_wrap(self, action, param):
        # Close find bar if active (this also clears search results)
        editor = self.get_current_page()
        if editor and hasattr(editor, 'find_bar') and editor.find_bar and editor.find_bar.get_visible():
            editor.find_bar.close()
        
        manager = self.get_application().settings_manager
        current = manager.get_setting("word-wrap")
        manager.set_setting("word-wrap", not current)
        self.grab_focus_editor()

    def on_zoom_in(self, action, param):
        manager = self.get_application().settings_manager
        current = manager.get_setting("font-size")
        manager.set_setting("font-size", current + 1)
        self.grab_focus_editor()

    def on_zoom_out(self, action, param):
        manager = self.get_application().settings_manager
        current = manager.get_setting("font-size")
        if current > 8:
            manager.set_setting("font-size", current - 1)
        self.grab_focus_editor()

    def on_zoom_reset(self, action, param):
        manager = self.get_application().settings_manager
        manager.set_setting("font-size", 11)
        self.grab_focus_editor()

    def on_setting_changed_win(self, manager, key):
        # Update all tabs
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            editor = page.get_child()._editor
            self.apply_settings_to_editor(editor)

    def apply_settings_to_editor(self, editor):
        app = self.get_application()
        if not app:
            app = Gio.Application.get_default()
        
        if not app or not hasattr(app, 'settings_manager'):
            return

        manager = app.settings_manager
        # Font size
        font_size = manager.get_setting("font-size")
        editor.view.renderer.set_font(Pango.FontDescription(f"Monospace {font_size}"))
        
        # Word wrap - handle state change properly
        new_wrap = manager.get_setting("word-wrap")
        if getattr(editor.view.renderer, 'wrap_enabled', False) != new_wrap:
            editor.view.renderer.wrap_enabled = new_wrap
            # Clear caches and force recalculation
            editor.view.renderer.wrap_cache = {}
            editor.view.renderer.visual_line_map = []
            editor.view.renderer.total_visual_lines_locked = False
            editor.view.renderer.visual_line_anchor = (0, 0)
            
            # Reset scroll if needed or re-adjust
            if new_wrap:
                editor.view.renderer.max_line_width = 0
                editor.view.scroll_x = 0
                editor.view.hadj.set_value(0)
            
            # Recalculate everything
            editor.view.on_resize(editor.view, editor.view.get_width(), editor.view.get_height())
        
        # Line numbers
        editor.view.renderer.show_line_numbers = manager.get_setting("line-numbers")
        
        # Tab width
        editor.view.renderer.tab_width = manager.get_setting("tab-width")
        
        # Use Tabs
        editor.view.use_tabs = manager.get_setting("use-tabs")

        # Auto Indent
        editor.view.auto_indent = manager.get_setting("auto-indent")

        # Highlighting
        editor.view.highlight_current_line = manager.get_setting("highlight-current-line")
        editor.view.highlight_brackets = manager.get_setting("highlight-brackets")

        editor.view.queue_draw()
    
    def on_open_recent(self, action, parameter):
        """Handle opening a recent file"""
        file_path = parameter.get_string()
        if os.path.exists(file_path):
            # Check if file is already open - if so, activate that tab
            if self.activate_tab_with_file(file_path):
                return
            
            # Check if we should replace current empty Untitled
            current_page = self.tab_view.get_selected_page()
            if current_page:
                editor = current_page.get_child()._editor
                
                # Check if current tab is modified
                is_modified = False
                for tab in self.tab_bar.tabs:
                    if hasattr(tab, '_page') and tab._page == current_page:
                        is_modified = tab.has_css_class("modified")
                        break
                
                # Check if it's an empty untitled file that's unmodified
                if (not editor.current_file_path and 
                    not is_modified and
                    editor.buf.total() == 1 and 
                    len(editor.buf.get_line(0)) == 0):
                    # Replace this tab with the opened file
                    self.load_file_into_editor(editor, file_path)
                    return
            
            # Otherwise, create new tab
            self.add_tab(file_path)
        else:
            # File doesn't exist, remove from recent
            self.recent_files_manager.recent_files.remove(file_path)
            self.recent_files_manager.save()
            self.update_recent_files_menu()
    
    def on_clear_recent(self, action, parameter):
        """Handle clearing recent files"""
        self.recent_files_manager.clear()
        self.update_recent_files_menu()
    
    def on_tab_activate_from_menu(self, action, parameter):
        """Handle tab activation from dropdown menu"""
        index = parameter.get_int32()
        if 0 <= index < len(self.tab_bar.tabs):
            tab = self.tab_bar.tabs[index]
            if hasattr(tab, '_page'):
                self.tab_view.set_selected_page(tab._page)
                # Focus the editor view
                editor = tab._page.get_child()._editor
                editor.view.grab_focus()
    
    def on_set_tab_width(self, action, parameter):
        """Handle tab width change from status bar"""
        width = parameter.get_string()
        editor = self.get_current_page()
        if editor:
            editor.tab_width = int(width)
            self.status_bar.tab_width_label.set_text(f"Tab Width: {width}")
    
    def on_set_encoding(self, action, parameter):
        """Handle encoding change from status bar"""
        encoding = parameter.get_string()
        editor = self.get_current_page()
        if editor:
            editor.current_encoding = encoding
            # Update status bar display
            encoding_display = {
                'utf-8': 'UTF-8',
                'utf-16le': 'UTF-16 LE',
                'utf-16be': 'UTF-16 BE'
            }.get(encoding, encoding.upper())
            self.status_bar.encoding_label.set_text(encoding_display)
    
    def on_set_line_feed(self, action, parameter):
        """Handle line feed change from status bar"""
        line_feed = parameter.get_string()
        editor = self.get_current_page()
        if editor:
            editor.line_feed = line_feed
            # Update status bar display
            line_feed_display = {
                'lf': 'Unix/Linux (LF)',
                'crlf': 'Windows (CRLF)',
                'cr': 'Mac OS (CR)'
            }.get(line_feed, line_feed.upper())
            self.status_bar.line_feed_label.set_text(line_feed_display)

    
    def on_save_as(self, action, parameter):
        """Handle Save As menu action"""
        editor = self.get_current_page()
        if not editor:
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Save As")
        
        # Set initial folder and filename based on current file
        # This bypasses the "Recent" view issue by opening directly in the file's folder
        if editor.current_file_path:
            current_file = Gio.File.new_for_path(editor.current_file_path)
            parent_folder = current_file.get_parent()
            
            if parent_folder:
                dialog.set_initial_folder(parent_folder)
            
            # Set the filename
            dialog.set_initial_name(os.path.basename(editor.current_file_path))
        else:
            # No current file, use a default name
            dialog.set_initial_name("untitled.txt")
        
        def done(dialog, result):
            try:
                gfile = dialog.save_finish(result)
            except Exception as e:
                print(f"Dialog cancelled or error: {e}")
                return

            print(f"\n=== DEBUG ON_SAVE_AS ===")
            print(f"GFile: {gfile}")
            print(f"URI: {gfile.get_uri()}")
            
            path = gfile.get_path()
            print(f"Path: {path}")

            if path is None:
                print("Cannot resolve local path for save destination")
                print(f"=== END DEBUG ===\n")
                return

            print(f"Calling save_file with path: {path}")
            print(f"=== END DEBUG ===\n")
            
            self.save_file(editor, path)
        
        dialog.save(self, None, done)
    
    def save_file(self, editor, path):
        """Save the editor buffer to a file using GIO (GTK4-safe)."""
        try:
            # Convert path → Gio.File
            gfile = Gio.File.new_for_path(path)
            
            print(f"\n=== DEBUG SAVE FILE ===")
            print(f"Saving to path: {path}")
            print(f"GFile path: {gfile.get_path()}")
            print(f"GFile URI: {gfile.get_uri()}")

            # Get text
            total_lines = editor.buf.total()
            lines = [editor.buf.get_line(i) for i in range(total_lines)]
            content = "\n".join(lines)
            
            print(f"Total lines: {total_lines}")
            print(f"Content length: {len(content)} characters")
            print(f"First 200 chars of content: {content[:200]}")
            print(f"Encoding: {editor.current_encoding}")

            # Open output stream (atomic replace)
            # None = no etag checking
            stream = gfile.replace(None, False, Gio.FileCreateFlags.NONE, None)

            # Encode using current encoding
            data = content.encode(editor.current_encoding, errors="replace")
            
            print(f"Encoded data length: {len(data)} bytes")

            # Write & close
            bytes_written = stream.write_bytes(GLib.Bytes.new(data), None)
            print(f"Bytes written: {bytes_written}")
            stream.close(None)
            
            print(f"Stream closed successfully")

            # Release the untitled number if this was an untitled file being saved with a name
            if hasattr(editor, 'untitled_number') and editor.untitled_number is not None:
                app = self.get_application()
                if app and isinstance(app, VirtualTextEditor):
                    app.release_untitled_number(editor.untitled_number)
                editor.untitled_number = None  # Clear it since it's now a named file

            # Update state
            if editor.current_file_path != path:
                 editor.current_file_path = path
                 
                 # Update syntax highlighting
                 lang = detect_language(path)
                 editor.view.buf.set_language(lang)
                 editor.view.syntax = editor.view.buf.syntax_engine
                 editor.view.queue_draw()
            
            # Add to recent files
            self.recent_files_manager.add(path)
            self.update_recent_files_menu()

            # Update tab title and clear modified status
            for page in [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]:
                if page.get_child()._editor == editor:
                    self.update_tab_title(page)
                    # Clear modified status in chrome tab
                    for tab in self.tab_bar.tabs:
                        if hasattr(tab, '_page') and tab._page == page:
                            tab.set_modified(False)
                            self.update_tab_dropdown()
                            # Update header title if this is the active page
                            if page == self.tab_view.get_selected_page():
                                self.update_header_title()
                            break
                    break

            print(f"File saved as {path} with encoding {editor.current_encoding}")
            print(f"=== END DEBUG ===\n")

        except Exception as e:
            print(f"Error saving file: {e}")
            import traceback
            traceback.print_exc()

    
    def on_encoding_changed(self, action, parameter):
        """Handle encoding selection from menu"""
        editor = self.get_current_page()
        if not editor:
            return
            
        encoding = parameter.get_string()
        editor.current_encoding = encoding
        action.set_state(parameter)
        
        print(f"Encoding changed to: {encoding} (will be used for next save)")

    def on_buffer_changed(self, editor):
        # Check if this is the initial empty tab being modified
        if getattr(editor, "is_initial_empty_tab", False):
            editor.is_initial_empty_tab = False
            # The initial tab is already named "Untitled 1", just update the header
            self.update_header_title()

        editor.view.queue_draw()

        width = editor.view.get_width()
        height = editor.view.get_height()
        if width <= 0 or height <= 0:
            GLib.idle_add(lambda: self.on_buffer_changed(editor))
            return

        # Mark the tab as modified
        for page in [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]:
            if page.get_child()._editor == editor:
                # Update chrome tab modified status
                for tab in self.tab_bar.tabs:
                    if hasattr(tab, '_page') and tab._page == page:
                        tab.set_modified(True)
                        self.update_tab_dropdown()
                        # Update header title if this is the active page
                        if page == self.tab_view.get_selected_page():
                            self.update_header_title()
                        break
                break
        
        # Live Search Update:
        # If the editor has an active Find Bar, trigger a re-search to update matches/count
        if hasattr(editor, 'find_bar') and editor.find_bar and editor.find_bar.get_visible():
            if not getattr(editor.find_bar, '_suppress_auto_search', False):
                # Trigger search changed (debounced)
                editor.find_bar.on_search_changed()

        # Invalidate wrap cache when buffer changes
        if editor.view.renderer.wrap_enabled:
            editor.view.renderer.wrap_cache.clear()
            editor.view.renderer.total_visual_lines_locked = False
            return

        # Non-wrap mode: updating scrollbar is cheap and correct
        editor.view.update_scrollbar()
    
    def on_vscroll_drag_begin(self, gesture, x, y, editor):
        """Handle scrollbar drag begin"""
        editor.view.scrollbar_dragging = True
        editor.view.last_drag_value = None
    
    def on_vscroll_drag_end(self, gesture, x, y, editor):
        """Handle scrollbar drag end"""
        editor.view.scrollbar_dragging = False
        
        if editor.buf.total() > 10000:
            editor.view.calculating = True
            editor.view.calculation_message = "Calculating final position..."
            editor.view.queue_draw()
        
        if editor.view.last_drag_value is not None:
            editor.view.pending_scroll_value = editor.view.last_drag_value
            editor.view.last_drag_value = None
            
            if not editor.view.scroll_update_pending:
                editor.view.scroll_update_pending = True
                GLib.idle_add(editor.view._process_scroll_update)

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return
            path = f.get_path()
            
            # Check if file is already open - if so, activate that tab
            if self.activate_tab_with_file(path):
                return
            
            # Check if the current active tab is an empty, unmodified Untitled
            current_page = self.tab_view.get_selected_page()
            if current_page:
                editor = current_page.get_child()._editor
                
                # Check if current tab is modified
                is_modified = False
                for tab in self.tab_bar.tabs:
                    if hasattr(tab, '_page') and tab._page == current_page:
                        is_modified = tab.has_css_class("modified")
                        break
                
                # Check if it's an empty untitled file that's unmodified
                if (not editor.current_file_path and 
                    not is_modified and
                    editor.buf.total() == 1 and 
                    len(editor.buf.get_line(0)) == 0):
                    # Replace this tab with the opened file
                    self.update_header_title()
                    self.load_file_into_editor(editor, path)
                    return
            
            # Otherwise, create new tab for the file
            self.add_tab(path)

        dialog.open(self, None, done)
    
    def load_file_into_editor(self, editor, path):
        """Load a file into an existing editor"""
        if not os.path.exists(path):
            # File doesn't exist - treat as new file creation
            editor.buf.load_text("")
            editor.current_file_path = path
            editor.current_encoding = "utf-8"
            editor.view.scroll_line = 0
            editor.view.scroll_x = 0
            
            # Enable Syntax Highlighting
            lang = detect_language(path)
            editor.view.buf.set_language(lang)
            editor.view.syntax = editor.view.buf.syntax_engine
            
            return

        # Show progress bar widget
        def on_cancel(button):
            self.progress_bar_widget.cancelled = True
        
        self.progress_bar_widget.start_loading(on_cancel)
        
        idx = IndexedFile(path)
        
        def progress_callback(fraction):
            if self.progress_bar_widget.cancelled:
                return False
            self.progress_bar_widget.update_progress(fraction)
            return False
        
        def index_complete():
            if self.progress_bar_widget.cancelled:
                self.progress_bar_widget.finish_loading()
                return False
                
            editor.buf.load(idx, emit_changed=False)

            editor.view.scroll_line = 0
            editor.view.scroll_x = 0
            
            # Clear all renderer caches for the new file
            editor.view.renderer.wrap_cache.clear()
            editor.view.renderer.visual_line_map = []
            editor.view.renderer.total_visual_lines_cache = None
            editor.view.renderer.total_visual_lines_locked = False
            editor.view.renderer.visual_line_anchor = (0, 0)
            editor.view.renderer.max_line_width = 0
            editor.view.renderer.needs_full_width_scan = True
            
            # Enable Syntax Highlighting
            lang = detect_language(path)
            editor.view.buf.set_language(lang)
            editor.view.syntax = editor.view.buf.syntax_engine
            
            # Clear optimization caches
            editor.view.renderer.last_ln_width = 0
            editor.view.renderer.estimated_total_cache = None
            editor.view.renderer.edits_since_cache_invalidation = 0
            
            # Set current encoding to match the loaded file
            editor.current_encoding = idx.encoding
            editor.current_file_path = path

            # Trigger width scan for the new file
            editor.view.file_loaded()
            
            # Set flag to update scrollbar on next draw
            editor.view.needs_scrollbar_init = True

            editor.view.queue_draw()

            # Update tab title
            for page in [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]:
                if page.get_child()._editor == editor:
                    self.update_tab_title(page)
                    break
            
            # Add to recent files
            self.recent_files_manager.add(path)
            self.update_recent_files_menu()
            
            # Hide progress bar
            self.progress_bar_widget.finish_loading()
            
            # Update status bar
            self.status_bar.update_for_editor(editor)
            
            # Focus the editor
            editor.view.grab_focus()
            
            return False

        def index_in_thread():
            try:
                idx.index_file(progress_callback)
                GLib.idle_add(index_complete)
            except Exception as e:
                print(f"Error indexing file: {e}")
                GLib.idle_add(loading_dialog.close)
        
        thread = Thread(target=index_in_thread)
        thread.daemon = True
        thread.start()


# ============================================================
#   APP
# ============================================================

class VirtualTextEditor(Adw.Application):
    # Global set to track which untitled numbers are currently in use across all windows
    _used_untitled_numbers = set()
    
    @classmethod
    def get_next_global_untitled_number(cls):
        """Get the next available untitled number (reuses freed numbers)"""
        # Find the smallest available number starting from 1
        num = 1
        while num in cls._used_untitled_numbers:
            num += 1
        cls._used_untitled_numbers.add(num)
        print(f"[UNTITLED] Allocated number {num}, in use: {sorted(cls._used_untitled_numbers)}")
        return num
    
    @classmethod
    def release_untitled_number(cls, num):
        """Release an untitled number so it can be reused"""
        if num is not None and num in cls._used_untitled_numbers:
            cls._used_untitled_numbers.discard(num)
            print(f"[UNTITLED] Released number {num}, in use: {sorted(cls._used_untitled_numbers)}")
        elif num is not None:
            print(f"[UNTITLED] WARNING: Tried to release {num} but it wasn't in use set: {sorted(cls._used_untitled_numbers)}")
    
    @classmethod
    def scan_and_register_untitled_numbers(cls, window):
        """Scan a window and register all untitled numbers currently in use"""
        # This is called when we need to sync up with existing windows
        # For example, when opening a second window
        for i in range(window.tab_view.get_n_pages()):
            page = window.tab_view.get_nth_page(i)
            tab_root = page.get_child()
            
            # Recursively find all editors in this tab (including splits)
            def find_all_editors(widget):
                editors = []
                if hasattr(widget, '_editor'):
                    editors.append(widget._editor)
                elif isinstance(widget, Gtk.Paned):
                    start = widget.get_start_child()
                    end = widget.get_end_child()
                    if start:
                        editors.extend(find_all_editors(start))
                    if end:
                        editors.extend(find_all_editors(end))
                elif isinstance(widget, Gtk.Box):
                    child = widget.get_first_child()
                    while child:
                        editors.extend(find_all_editors(child))
                        child = child.get_next_sibling()
                elif isinstance(widget, Gtk.Overlay):
                    child = widget.get_child()
                    if child:
                        editors.extend(find_all_editors(child))
                return editors
            
            editors = find_all_editors(tab_root)
            for editor in editors:
                if hasattr(editor, 'untitled_number') and editor.untitled_number is not None:
                    cls._used_untitled_numbers.add(editor.untitled_number)
    
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.svite",
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.files_to_open = []
        self.settings_manager = SettingsManager()
        self.settings_manager.connect("setting-changed", self.on_setting_changed)
        # Apply initial theme
        self.apply_theme()

    def on_setting_changed(self, manager, key):
        if key == "theme":
            self.apply_theme()

    def apply_theme(self):
        theme = self.settings_manager.get_setting("theme")
        style_manager = Adw.StyleManager.get_default()
        if theme == "Dark":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif theme == "Light":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    
    def update_scrollbar_css(self, r, g, b, a):
        """Update scrollbar CSS with the given background color."""
        # Convert RGBA float values (0-1) to CSS rgba format
        r_int = int(r * 255)
        g_int = int(g * 255)
        b_int = int(b * 255)
        
        # Format the CSS with actual color values
        css = CSS_OVERLAY_SCROLLBAR.format(bg_color=f"rgba({r_int},{g_int},{b_int},{a})")
        self.css_provider.load_from_data(css.encode())

    def hex_to_rgba_floats(self, hex_str, alpha=1.0):
        hex_str = hex_str.lstrip('#')
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return r, g, b, alpha

    def do_activate(self):
        # Create and store CSS provider for dynamic updates
        self.css_provider = Gtk.CssProvider()
        
        # Detect current theme and initialize with appropriate color
        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()
        if is_dark:
            r, g, b, a = self.hex_to_rgba_floats("#191919")
            self.update_scrollbar_css(r, g, b, a)
        else:
            r, g, b, a = self.hex_to_rgba_floats("#fafafa")
            self.update_scrollbar_css(r, g, b, a)

        
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        
        # Open files from command line if any (works for both new and existing windows)
        if self.files_to_open:
            # If this is a new window with only the initial empty tab, close it first
            if win.tab_view.get_n_pages() == 1:
                first_page = win.tab_view.get_nth_page(0)
                editor = first_page.get_child()._editor
                
                # Check if it's an empty untitled file
                if (not editor.current_file_path and 
                    editor.buf.total() == 1 and 
                    len(editor.buf.get_line(0)) == 0):
                    # Close the initial empty tab
                    win.tab_view.close_page(first_page)
                    for tab in win.tab_bar.tabs:
                        if hasattr(tab, '_page') and tab._page == first_page:
                            win.tab_bar.remove_tab(tab)
                            break
            
            # Open each file in a new tab
            for file_path in self.files_to_open:
                # Check if file is already open - if so, activate that tab
                if not win.activate_tab_with_file(file_path):
                    win.add_tab(file_path)
            
            self.files_to_open = []
        
        win.present()
    
    def do_open(self, files, n_files, hint):
        """Handle files passed via command line"""
        self.files_to_open = [f.get_path() for f in files]
        self.activate()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    VirtualTextEditor().run(sys.argv)
