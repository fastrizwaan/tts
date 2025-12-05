#!/usr/bin/env python3
import sys, os, mmap, gi, cairo, time, unicodedata
from threading import Thread
from array import array
import math 
import datetime
import bisect
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

"""




# ============================================================
#   HELPER FUNCTIONS
# ============================================================

def detect_rtl_line(text):
    """Detect if a line is RTL using Unicode bidirectional properties.
    
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
        if self.encoding == "utf-16le":
            newline_bytes = b'\n\x00'  # UTF-16LE: \n = 0x0A 0x00
        else:  # utf-16be
            newline_bytes = b'\x00\n'  # UTF-16BE: \n = 0x00 0x0A
        
        # Check for BOM and set start position
        start_pos = 0
        if total_size >= 2:
            first_two = mm[0:2]
            if first_two in (b'\xff\xfe', b'\xfe\xff'):
                start_pos = 2
        
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


# ============================================================
#   BUFFER
# ============================================================

class VirtualBuffer(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self):
        super().__init__()
        self.file = None            # IndexedFile
        self.edits = {}             # sparse: logical_line → modified string
        self.deleted_lines = set()  # Track deleted logical lines
        self.inserted_lines = {}    # Track inserted lines: logical_line → content
        self.line_offsets = []      # List of (logical_line, offset) tuples - sorted by logical_line
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection = Selection()
        
        # State for Alt+Arrow movement
        self.last_move_was_partial = False
        self.expected_selection = None

    def load(self, indexed_file, emit_changed=True):
        self.file = indexed_file
        self.edits.clear()
        self.deleted_lines.clear()
        self.inserted_lines.clear()
        self.line_offsets = []
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection.clear()

        if emit_changed:
            self.emit("changed")


    def get_text(self):
        """Get full text content of the buffer"""
        lines = []
        total = self.total()
        for i in range(total):
            lines.append(self.get_line(i))
        return "\n".join(lines)
        
    def set_text(self, text):
        """Set full text content"""
        # Clear existing
        self.edits.clear()
        self.deleted_lines.clear()
        self.inserted_lines.clear()
        self.line_offsets = []
        self.file = None # Detach file if any
        
        # Insert new lines
        lines = text.split('\n')
        for i, line in enumerate(lines):
            self.inserted_lines[i] = line
            
        self.emit("changed")

    def _logical_to_physical(self, logical_line):
        """Convert logical line number to physical file line number"""
        if not self.file:
            return logical_line
        
        # Calculate cumulative offset at this logical line
        offset = 0
        for log_line, off in self.line_offsets:
            if log_line <= logical_line:
                offset = off
            else:
                break
        
        return logical_line - offset

    def total(self):
        """Return total number of logical lines in the buffer."""
        if not self.file:
            if not self.edits and not self.inserted_lines:
                return 1
            all_lines = set(self.edits.keys()) | set(self.inserted_lines.keys())
            return max(1, max(all_lines) + 1) if all_lines else 1

        # File is present - file lines plus net insertions
        base = self.file.total_lines()
        
        # Calculate net change from offsets
        if self.line_offsets:
            # The last offset tells us the total shift
            net_insertions = self.line_offsets[-1][1]
            return max(1, base + net_insertions)
        
        return max(1, base)

    def get_line(self, ln):
        # Check if it's an inserted line first
        if ln in self.inserted_lines:
            return self.inserted_lines[ln]
        
        # Check if it's an edited line
        if ln in self.edits:
            return self.edits[ln]
        
        # Check if deleted
        if ln in self.deleted_lines:
            return ""
        
        # Convert to physical line and return from file
        if self.file:
            physical = self._logical_to_physical(ln)
            return self.file[physical] if 0 <= physical < self.file.total_lines() else ""
        return ""

    def _add_offset(self, at_line, delta):
        """Add an offset delta starting at logical line at_line"""
        # Find if there's already an offset entry at this line
        found_idx = -1
        for idx, (log_line, offset) in enumerate(self.line_offsets):
            if log_line == at_line:
                found_idx = idx
                break
        
        if found_idx >= 0:
            # Update existing offset
            old_offset = self.line_offsets[found_idx][1]
            self.line_offsets[found_idx] = (at_line, old_offset + delta)
        else:
            # Add new offset entry
            # First, find what the offset was just before this line
            prev_offset = 0
            insert_idx = 0
            for idx, (log_line, offset) in enumerate(self.line_offsets):
                if log_line < at_line:
                    prev_offset = offset
                    insert_idx = idx + 1
                else:
                    break
            
            # Insert new offset entry
            self.line_offsets.insert(insert_idx, (at_line, prev_offset + delta))
        
        # Update all subsequent offset entries
        for idx in range(found_idx + 1 if found_idx >= 0 else insert_idx + 1, len(self.line_offsets)):
            log_line, offset = self.line_offsets[idx]
            self.line_offsets[idx] = (log_line, offset + delta)

    def set_cursor(self, ln, col, extend_selection=False):
        total = self.total()
        ln = max(0, min(ln, total - 1))
        line = self.get_line(ln)
        col = max(0, min(col, len(line)))
        
        if extend_selection:
            # Start selection if not already active
            if not self.selection.active:
                self.selection.set_start(self.cursor_line, self.cursor_col)
            # Update end point
            self.selection.set_end(ln, col)
        else:
            # Clear selection if not extending
            if not self.selection.selecting_with_keyboard:
                self.selection.clear()
        
        self.cursor_line = ln
        self.cursor_col = col

    def select_all(self):
        """Select all text in the buffer"""
        self.selection.set_start(0, 0)
        total = self.total()
        last_line = total - 1
        last_line_text = self.get_line(last_line)
        self.selection.set_end(last_line, len(last_line_text))
        self.cursor_line = last_line
        self.cursor_col = len(last_line_text)
        self.emit("changed")
    
    def get_selected_text(self):
        """Get the currently selected text"""
        if not self.selection.has_selection():
            return ""
        
        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        if start_line == end_line:
            line = self.get_line(start_line)
            return line[start_col:end_col]
        else:
            lines = []
            first_line = self.get_line(start_line)
            lines.append(first_line[start_col:])
            
            for ln in range(start_line + 1, end_line):
                lines.append(self.get_line(ln))
            
            last_line = self.get_line(end_line)
            lines.append(last_line[:end_col])
            
            return '\n'.join(lines)
    
    def delete_selection(self):
        """Delete the selected text"""
        if not self.selection.has_selection():
            return False
        
        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        if start_line == end_line:
            # Single line selection
            line = self.get_line(start_line)
            new_line = line[:start_col] + line[end_col:]
            
            if start_line in self.inserted_lines:
                self.inserted_lines[start_line] = new_line
            else:
                self.edits[start_line] = new_line
        else:
            # Multi-line selection
            first_line = self.get_line(start_line)
            last_line = self.get_line(end_line)
            new_line = first_line[:start_col] + last_line[end_col:]
            
            # Calculate number of lines being deleted
            lines_deleted = end_line - start_line
            
            # Shift down all virtual lines above the deleted range
            new_ins = {}
            for k, v in self.inserted_lines.items():
                if k < start_line:
                    new_ins[k] = v
                elif k == start_line:
                    # This will be set below
                    pass
                elif k <= end_line:
                    # Skip deleted lines
                    pass
                else:
                    # Shift down
                    new_ins[k - lines_deleted] = v
            
            new_ed = {}
            for k, v in self.edits.items():
                if k < start_line:
                    new_ed[k] = v
                elif k == start_line:
                    # This will be set below
                    pass
                elif k <= end_line:
                    # Skip deleted lines
                    pass
                else:
                    # Shift down
                    new_ed[k - lines_deleted] = v
            
            new_del = set()
            for k in self.deleted_lines:
                if k < start_line:
                    new_del.add(k)
                elif k <= end_line:
                    # Skip deleted lines
                    pass
                else:
                    # Shift down
                    new_del.add(k - lines_deleted)
            
            # Set the merged line
            if start_line in self.inserted_lines:
                new_ins[start_line] = new_line
            else:
                new_ed[start_line] = new_line
            
            self.inserted_lines = new_ins
            self.edits = new_ed
            self.deleted_lines = new_del
            
            # Update line offsets
            self._add_offset(start_line + 1, -lines_deleted)
        
        self.cursor_line = start_line
        self.cursor_col = start_col
        self.selection.clear()
        self.emit("changed")
        return True


    def insert_text(self, text, overwrite=False):
        # If there's a selection, delete it first
        if self.selection.has_selection():
            self.delete_selection()

        ln  = self.cursor_line
        col = self.cursor_col
        old = self.get_line(ln)

        # Split insert by newline
        parts = text.split("\n")

        if len(parts) == 1:
            # ---------------------------
            # Simple one-line insert
            # ---------------------------
            
            # Overwrite mode: replace character at cursor (only for single characters)
            if overwrite and len(text) == 1 and col < len(old):
                # Replace the character at cursor position
                new_line = old[:col] + text + old[col+1:]
            else:
                # Normal insert mode
                new_line = old[:col] + text + old[col:]

            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line

            self.cursor_col += len(text)
            self.emit("changed")
            return

        # ------------------------------------------------------------
        # Multi-line insert
        # ------------------------------------------------------------
        first = parts[0]
        last  = parts[-1]
        middle = parts[1:-1]   # may be empty

        # Left + first-line fragment
        left_part  = old[:col] + first
        right_part = last + old[col:]

        # Number of new lines being inserted
        lines_to_insert = len(parts) - 1

        # Shift up all virtual lines after current line
        new_ins = {}
        for k, v in self.inserted_lines.items():
            if k < ln:
                new_ins[k] = v
            elif k == ln:
                # This will be set below
                pass
            else:
                # Shift up
                new_ins[k + lines_to_insert] = v

        new_ed = {}
        for k, v in self.edits.items():
            if k < ln:
                new_ed[k] = v
            elif k == ln:
                # This will be set below
                pass
            else:
                # Shift up
                new_ed[k + lines_to_insert] = v

        new_del = set()
        for k in self.deleted_lines:
            if k <= ln:
                new_del.add(k)
            else:
                # Shift up
                new_del.add(k + lines_to_insert)

        # Update current line with left part
        if ln in self.inserted_lines:
            new_ins[ln] = left_part
        else:
            new_ed[ln] = left_part

        # Insert the middle lines
        cur = ln
        for m in middle:
            cur += 1
            new_ins[cur] = m

        # Insert last line (right fragment)
        new_ins[ln + lines_to_insert] = right_part

        # Apply dicts
        self.inserted_lines = new_ins
        self.edits = new_ed
        self.deleted_lines = new_del

        # Offset update (insert count = len(parts)-1)
        self._add_offset(ln + 1, lines_to_insert)

        # Final cursor
        self.cursor_line = ln + lines_to_insert
        self.cursor_col  = len(last)

        self.selection.clear()
        self.emit("changed")



    def backspace(self):
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        if col == 0:
            # Deleting at start of line - merge with previous line
            if ln > 0:
                prev_line = self.get_line(ln - 1)
                new_line = prev_line + line
                
                # Update previous line with merged content
                if ln - 1 in self.inserted_lines:
                    self.inserted_lines[ln - 1] = new_line
                else:
                    self.edits[ln - 1] = new_line
                
                # Shift down all virtual lines after current line
                new_ins = {}
                for k, v in self.inserted_lines.items():
                    if k < ln:
                        new_ins[k] = v
                    elif k == ln:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ins[k - 1] = v
                
                new_ed = {}
                for k, v in self.edits.items():
                    if k < ln:
                        new_ed[k] = v
                    elif k == ln:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ed[k - 1] = v
                
                new_del = set()
                for k in self.deleted_lines:
                    if k < ln:
                        new_del.add(k)
                    elif k == ln:
                        # Skip - already being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_del.add(k - 1)
                
                self.inserted_lines = new_ins
                self.edits = new_ed
                self.deleted_lines = new_del
                
                # Track offset change (1 line deleted)
                self._add_offset(ln + 1, -1)
                
                self.cursor_line = ln - 1
                self.cursor_col = len(prev_line)
        else:
            # Normal backspace within a line
            new_line = line[:col-1] + line[col:]
            
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
            
            self.cursor_col = col - 1

        self.selection.clear()
        self.emit("changed")
        


    def delete_key(self):
        """Handle Delete key press"""
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col
        
        if col >= len(line):
            # At end of line - merge with next line
            if ln < self.total() - 1:
                next_line = self.get_line(ln + 1)
                new_line = line + next_line
                
                # Update current line with merged content
                if ln in self.inserted_lines:
                    self.inserted_lines[ln] = new_line
                else:
                    self.edits[ln] = new_line
                
                # Shift down all virtual lines after next line
                new_ins = {}
                for k, v in self.inserted_lines.items():
                    if k <= ln:
                        new_ins[k] = v
                    elif k == ln + 1:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ins[k - 1] = v
                
                new_ed = {}
                for k, v in self.edits.items():
                    if k <= ln:
                        new_ed[k] = v
                    elif k == ln + 1:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ed[k - 1] = v
                
                new_del = set()
                for k in self.deleted_lines:
                    if k <= ln:
                        new_del.add(k)
                    elif k == ln + 1:
                        # Skip - already being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_del.add(k - 1)
                
                self.inserted_lines = new_ins
                self.edits = new_ed
                self.deleted_lines = new_del
                
                # Track offset change (1 line deleted)
                self._add_offset(ln + 2, -1)
        else:
            # Normal delete within a line
            new_line = line[:col] + line[col+1:]
            
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
        
        self.selection.clear()
        self.emit("changed")
    
    def delete_word_backward(self):
        """Delete from cursor to start of current word (Ctrl+Backspace)"""
        import unicodedata
        
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            return cat[0] in ('L', 'N', 'M')
        
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        if col == 0:
            if ln > 0:
                prev_line = self.get_line(ln - 1)
                new_line = prev_line + line
                
                if ln - 1 in self.inserted_lines:
                    self.inserted_lines[ln - 1] = new_line
                else:
                    self.edits[ln - 1] = new_line
                
                new_ins = {}
                for k, v in self.inserted_lines.items():
                    if k < ln:
                        new_ins[k] = v
                    elif k == ln:
                        pass
                    else:
                        new_ins[k - 1] = v
                
                new_ed = {}
                for k, v in self.edits.items():
                    if k < ln:
                        new_ed[k] = v
                    elif k == ln:
                        pass
                    else:
                        new_ed[k - 1] = v
                
                new_del = set()
                for k in self.deleted_lines:
                    if k < ln:
                        new_del.add(k)
                    elif k == ln:
                        pass
                    else:
                        new_del.add(k - 1)
                
                self.inserted_lines = new_ins
                self.edits = new_ed
                self.deleted_lines = new_del
                self._add_offset(ln + 1, -1)
                
                self.cursor_line = ln - 1
                self.cursor_col = len(prev_line)
            
            self.emit("changed")
            return
        
        start_col = col
        while start_col > 0 and line[start_col - 1].isspace():
            start_col -= 1
        
        if start_col > 0:
            if is_word_char(line[start_col - 1]):
                while start_col > 0 and is_word_char(line[start_col - 1]):
                    start_col -= 1
            elif not line[start_col - 1].isspace():
                while start_col > 0 and not line[start_col - 1].isspace() and not is_word_char(line[start_col - 1]):
                    start_col -= 1
        
        new_line = line[:start_col] + line[col:]
        
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        self.cursor_col = start_col
        self.emit("changed")
    
    def delete_word_forward(self):
        """Delete from cursor to end of current word (Ctrl+Delete)"""
        import unicodedata
        
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            return cat[0] in ('L', 'N', 'M')
        
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        # If at end of line, delete the newline
        if col >= len(line):
            if ln < self.total() - 1:
                next_line = self.get_line(ln + 1)
                new_line = line + next_line
                
                if ln in self.inserted_lines:
                    self.inserted_lines[ln] = new_line
                else:
                    self.edits[ln] = new_line
                
                new_ins = {}
                for k, v in self.inserted_lines.items():
                    if k <= ln:
                        new_ins[k] = v
                    elif k == ln + 1:
                        pass
                    else:
                        new_ins[k - 1] = v
                
                new_ed = {}
                for k, v in self.edits.items():
                    if k <= ln:
                        new_ed[k] = v
                    elif k == ln + 1:
                        pass
                    else:
                        new_ed[k - 1] = v
                
                new_del = set()
                for k in self.deleted_lines:
                    if k <= ln:
                        new_del.add(k)
                    elif k == ln + 1:
                        pass
                    else:
                        new_del.add(k - 1)
                
                self.inserted_lines = new_ins
                self.edits = new_ed
                self.deleted_lines = new_del
                self._add_offset(ln + 2, -1)
            
            self.emit("changed")
            return
        
        end_col = col
        while end_col < len(line) and line[end_col].isspace():
            end_col += 1
        
        if end_col < len(line):
            if is_word_char(line[end_col]):
                while end_col < len(line) and is_word_char(line[end_col]):
                    end_col += 1
            elif not line[end_col].isspace():
                while end_col < len(line) and not line[end_col].isspace() and not is_word_char(line[end_col]):
                    end_col += 1
        
        new_line = line[:col] + line[end_col:]
        
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        self.emit("changed")
    
    def delete_to_line_end(self):
        """Delete from cursor to end of line (Ctrl+Shift+Delete)"""
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        new_line = line[:col]
        
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        self.emit("changed")
    
    def delete_to_line_start(self):
        """Delete from cursor to start of line (Ctrl+Shift+Backspace)"""
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        new_line = line[col:]
        
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        self.cursor_col = 0
        self.emit("changed")
        


    def insert_newline(self):
        if self.selection.has_selection():
            self.delete_selection()
        
        ln = self.cursor_line
        col = self.cursor_col

        old_line = self.get_line(ln)
        left = old_line[:col]
        right = old_line[col:]

        # Update current line
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = left
        else:
            self.edits[ln] = left
        
        # Insert new line
        self.inserted_lines[ln + 1] = right
        
        # Track offset change (1 line inserted)
        self._add_offset(ln + 1, 1)
        
        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.selection.clear()
        self.emit("changed")
        


    

    def _logical_to_physical(self, logical_line):
        if not self.file:
            return logical_line

        if not self.line_offsets:
            return logical_line

        # Extract only logical_line keys for binary search
        keys = [lo for lo, _ in self.line_offsets]
        idx = bisect.bisect_right(keys, logical_line) - 1

        if idx < 0:
            return logical_line

        _, offset = self.line_offsets[idx]
        return logical_line - offset

    def _add_offset(self, at_line, delta):
        # Fast path: empty offsets
        if not self.line_offsets:
            self.line_offsets.append((at_line, delta))
            return

        import bisect
        keys = [lo for lo, _ in self.line_offsets]
        pos = bisect.bisect_left(keys, at_line)

        # Case 1: exact match → update
        if pos < len(self.line_offsets) and self.line_offsets[pos][0] == at_line:
            old = self.line_offsets[pos][1]
            new_val = old + delta
            self.line_offsets[pos] = (at_line, new_val)

            # Update following offsets
            for i in range(pos + 1, len(self.line_offsets)):
                lo, off = self.line_offsets[i]
                self.line_offsets[i] = (lo, off + delta)

            return

        # Case 2: insert new offset
        # Find previous offset value
        prev_offset = self.line_offsets[pos-1][1] if pos > 0 else 0

        self.line_offsets.insert(pos, (at_line, prev_offset + delta))

        # Update subsequent offsets
        for i in range(pos + 1, len(self.line_offsets)):
            lo, off = self.line_offsets[i]
            self.line_offsets[i] = (lo, off + delta)

    def insert_newline(self):
        if self.selection.has_selection():
            self.delete_selection()
            return

        ln = self.cursor_line
        col = self.cursor_col

        old = self.get_line(ln)
        left  = old[:col]
        right = old[col:]

        # Update left part
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = left
        else:
            self.edits[ln] = left

        # ---- SHIFT ONLY VIRTUAL LINES ----
        # Inserted
        new_ins = {}
        for k, v in self.inserted_lines.items():
            new_ins[k if k <= ln else k+1] = v

        # Edits
        new_ed = {}
        for k, v in self.edits.items():
            new_ed[k if k <= ln else k+1] = v

        # Deleted
        new_del = set()
        for k in self.deleted_lines:
            new_del.add(k if k <= ln else k+1)

        # Insert right half as NEW line at ln+1
        new_ins[ln + 1] = right

        self.inserted_lines = new_ins
        self.edits = new_ed
        self.deleted_lines = new_del

        # Track logical offset (1 new line)
        self._add_offset(ln + 1, 1)

        # Cursor
        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.selection.clear()
        self.emit("changed")

    def indent_selection(self):
        """Indent selected lines or current line"""
        if not self.selection.has_selection():
            # Just insert 4 spaces at cursor (handled by insert_text usually, but we can do it here)
            self.insert_text("    ")
            return

        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        # Indent all lines in range
        for ln in range(start_line, end_line + 1):
            line = self.get_line(ln)
            new_line = "    " + line
            
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
        
        # Adjust selection and cursor
        # If selection started at 0, keep it at 0 to include the new indentation
        if start_col > 0:
            self.selection.start_col += 4
            
        self.selection.end_col += 4
        self.cursor_col += 4
        self.emit("changed")

    def unindent_selection(self):
        """Unindent selected lines or current line"""
        if not self.selection.has_selection():
            # Unindent current line
            ln = self.cursor_line
            line = self.get_line(ln)
            removed = 0
            if line.startswith("    "):
                new_line = line[4:]
                removed = 4
            elif line.startswith("   "):
                new_line = line[3:]
                removed = 3
            elif line.startswith("  "):
                new_line = line[2:]
                removed = 2
            elif line.startswith(" "):
                new_line = line[1:]
                removed = 1
            else:
                return # Nothing to unindent
            
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
            
            self.cursor_col = max(0, self.cursor_col - removed)
            self.emit("changed")
            return

        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        removed_start = 0
        removed_end = 0
        
        # Unindent all lines in range
        for ln in range(start_line, end_line + 1):
            line = self.get_line(ln)
            removed = 0
            if line.startswith("    "):
                new_line = line[4:]
                removed = 4
            elif line.startswith("   "):
                new_line = line[3:]
                removed = 3
            elif line.startswith("  "):
                new_line = line[2:]
                removed = 2
            elif line.startswith(" "):
                new_line = line[1:]
                removed = 1
            else:
                new_line = line
            
            if removed > 0:
                if ln in self.inserted_lines:
                    self.inserted_lines[ln] = new_line
                else:
                    self.edits[ln] = new_line
            
            if ln == start_line:
                removed_start = removed
            if ln == end_line:
                removed_end = removed
        
        # We don't perfectly adjust selection cols for multi-line unindent 
        # because each line might lose different amount. 
        # But we should try to keep it valid.
        self.selection.start_col = max(0, self.selection.start_col - removed_start)
        self.selection.end_col = max(0, self.selection.end_col - removed_end)
        self.emit("changed")
        
    def move_word_left_with_text(self):
        """Move text left: full words swap, partial selection moves 1 char (Alt+Left)"""
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        def is_word_separator(ch):
            """Check if character is a word separator (space, underscore, or punctuation)"""
            return ch in ' _' or not ch.isalnum()
        
        # Check if we have a selection
        if self.selection.has_selection():
            bounds = self.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_ln, start_col, end_ln, end_col = bounds
                
                # Handle multi-line selections - move character wise
                if start_ln != end_ln:
                    # Get selected text first to analyze structure
                    selected_text = self.get_selected_text()
                    sel_lines = selected_text.split('\n')
                    
                    # Extract structure: content and separators
                    first_line_text = sel_lines[0]
                    first_line_content = first_line_text.rstrip()
                    first_line_trailing = first_line_text[len(first_line_content):]
                    
                    last_line_text = sel_lines[-1]
                    last_line_content = last_line_text.lstrip()
                    last_line_leading = last_line_text[:len(last_line_text) - len(last_line_content)]
                    
                    # 1. Identify char before
                    if start_col > 0:
                        # Char on same line
                        prev_ln = start_ln
                        prev_col = start_col - 1
                        char_before = self.get_line(start_ln)[start_col - 1]
                    else:
                        # Newline from previous line
                        if start_ln == 0:
                            return # Can't move left
                        prev_ln = start_ln - 1
                        prev_line_content = self.get_line(prev_ln)
                        prev_col = len(prev_line_content)
                        char_before = '\n'
                        
                    # 3. Extend selection to include char before
                    # NOTE: set_start resets end, so we must restore the end
                    self.selection.set_start(prev_ln, prev_col)
                    self.selection.set_end(end_ln, end_col)
                    
                    # 4. Delete extended range
                    self.delete_selection()
                    
                    # 5. Insert swapped: selected_text + char_before with spacing preserved
                    if char_before == '\n':
                        # Swapping with newline: preserve spacing structure
                        if len(sel_lines) == 2:
                            # Simple case: swap first and last content, keep separators in place
                            full_text = last_line_content + first_line_trailing + '\n' + last_line_leading + first_line_content + char_before
                        else:
                            # Complex case with middle lines - for now, keep original behavior
                            full_text = selected_text + char_before
                    else:
                        # Swapping with regular char: keep original behavior
                        full_text = selected_text + char_before
                    
                    # Remember start position (cursor is already at prev_ln, prev_col from delete)
                    ins_start_ln = self.cursor_line
                    ins_start_col = self.cursor_col
                    
                    self.insert_text(full_text)
                    
                    # 6. Restore selection (selected_text part)
                    # Calculate end of selected_text relative to insertion start
                    sel_lines = selected_text.split('\n')
                    if len(sel_lines) == 1:
                        sel_end_ln = ins_start_ln
                        sel_end_col = ins_start_col + len(selected_text)
                    else:
                        sel_end_ln = ins_start_ln + len(sel_lines) - 1
                        sel_end_col = len(sel_lines[-1])
                    
                    self.selection.set_start(ins_start_ln, ins_start_col)
                    self.selection.set_end(sel_end_ln, sel_end_col)
                    
                    self.emit("changed")
                    return
                
                selected_text = line[start_col:end_col]
                
                # Check if selection is full words (starts and ends at word boundaries)
                is_full_word_selection = True
                
                # Check start boundary
                if start_col > 0 and start_col <= len(line) and not is_word_separator(line[start_col - 1]):
                    is_full_word_selection = False
                if start_col < len(line) and is_word_separator(line[start_col]):
                    is_full_word_selection = False
                    
                # Check end boundary
                if end_col < len(line) and not is_word_separator(line[end_col]):
                    is_full_word_selection = False
                if end_col > 0 and end_col <= len(line) and is_word_separator(line[end_col - 1]):
                    is_full_word_selection = False
                
                # Check state: if we were moving partially, KEEP moving partially
                current_bounds = (start_ln, start_col, end_ln, end_col)
                if self.expected_selection != current_bounds:
                    # Sequence broken or new selection
                    self.last_move_was_partial = False
                
                if self.last_move_was_partial:
                    is_full_word_selection = False

                if is_full_word_selection:
                    # Full word(s) selected - swap with previous word
                    # Find previous word
                    prev_word_end = start_col - 1
                    while prev_word_end >= 0 and prev_word_end < len(line) and is_word_separator(line[prev_word_end]):
                        prev_word_end -= 1
                    prev_word_end += 1
                    
                    if prev_word_end == 0:
                        # No previous word on this line - try previous line
                        if ln == 0:
                            return  # Can't move left from first line
                        
                        prev_ln = ln - 1
                        
                        # Skip empty lines to find the previous word
                        while prev_ln >= 0:
                            prev_line = self.get_line(prev_ln)
                            
                            # Find last word on this line
                            prev_word_end = len(prev_line)
                            while prev_word_end > 0 and is_word_separator(prev_line[prev_word_end - 1]):
                                prev_word_end -= 1
                            
                            # If we found a word on this line, break
                            if prev_word_end > 0:
                                break
                            
                            # Otherwise, try previous line
                            prev_ln -= 1
                        
                        # Check if we found a word
                        if prev_ln < 0:
                            return  # No word found
                        
                        prev_word_start = prev_word_end
                        while prev_word_start > 0 and not is_word_separator(prev_line[prev_word_start - 1]):
                            prev_word_start -= 1
                        
                        prev_word = prev_line[prev_word_start:prev_word_end]
                        
                        # Get selected text
                        selected_text = line[start_col:end_col]
                        
                        # Update current line: replace selected text with prev_word at the same position
                        new_current_line = line[:start_col] + prev_word + line[end_col:]
                        if ln in self.inserted_lines:
                            self.inserted_lines[ln] = new_current_line
                        else:
                            self.edits[ln] = new_current_line
                        
                        # Update previous line: replace prev_word with selected text at the same position
                        new_prev_line = (prev_line[:prev_word_start] + 
                                       selected_text + 
                                       prev_line[prev_word_end:])
                        if prev_ln in self.inserted_lines:
                            self.inserted_lines[prev_ln] = new_prev_line
                        else:
                            self.edits[prev_ln] = new_prev_line
                        
                        # Update selection to moved position on previous line
                        self.selection.set_start(prev_ln, prev_word_start)
                        self.selection.set_end(prev_ln, prev_word_start + len(selected_text))
                        self.cursor_line = prev_ln
                        self.cursor_col = prev_word_start
                        
                        # Update state
                        self.last_move_was_partial = False
                        self.expected_selection = (prev_ln, prev_word_start, prev_ln, prev_word_start + len(selected_text))
                        
                        self.emit("changed")
                        return
                    
                    prev_word_start = prev_word_end - 1
                    while prev_word_start > 0 and prev_word_start <= len(line) and not is_word_separator(line[prev_word_start - 1]):
                        prev_word_start -= 1
                    
                    prev_word = line[prev_word_start:prev_word_end]
                    separators = line[prev_word_end:start_col]
                    
                    # Rebuild line with swapped text
                    new_line = (line[:prev_word_start] + 
                               selected_text + 
                               separators +
                               prev_word + 
                               line[end_col:])
                    
                    # Update line
                    if ln in self.inserted_lines:
                        self.inserted_lines[ln] = new_line
                    else:
                        self.edits[ln] = new_line
                    
                    # Update selection to moved position
                    self.selection.set_start(ln, prev_word_start)
                    self.selection.set_end(ln, prev_word_start + len(selected_text))
                    self.cursor_col = prev_word_start
                    
                    # Update state
                    self.last_move_was_partial = False
                    self.expected_selection = (ln, prev_word_start, ln, prev_word_start + len(selected_text))
                    
                    self.emit("changed")
                else:
                    # Partial selection - ALWAYS use character-wise movement
                    # 1. Identify char before
                    # Get the correct line for the selection start
                    start_line = self.get_line(start_ln)
                    if start_col > 0:
                        # Char on same line
                        prev_ln = start_ln
                        prev_col = start_col - 1
                        char_before = start_line[start_col - 1]
                    else:
                        # Newline from previous line
                        if start_ln == 0:
                            return  # Can't move left
                        prev_ln = start_ln - 1
                        prev_line_content = self.get_line(prev_ln)
                        prev_col = len(prev_line_content)
                        char_before = '\n'
                    
                    # 2. Get selected text
                    selected_text = self.get_selected_text()
                    
                    # 3. Extend selection to include char before
                    self.selection.set_start(prev_ln, prev_col)
                    self.selection.set_end(end_ln, end_col)
                    
                    # 4. Delete extended range
                    self.delete_selection()
                    
                    # 5. Insert swapped: selected_text + char_before
                    full_text = selected_text + char_before
                    
                    # Remember start position
                    ins_start_ln = self.cursor_line
                    ins_start_col = self.cursor_col
                    
                    self.insert_text(full_text)
                    
                    # 6. Restore selection (selected_text part)
                    sel_lines = selected_text.split('\n')
                    if len(sel_lines) == 1:
                        sel_end_ln = ins_start_ln
                        sel_end_col = ins_start_col + len(selected_text)
                    else:
                        sel_end_ln = ins_start_ln + len(sel_lines) - 1
                        sel_end_col = len(sel_lines[-1])
                    
                    self.selection.set_start(ins_start_ln, ins_start_col)
                    self.selection.set_end(sel_end_ln, sel_end_col)
                    
                    # Update cursor to end of selection
                    self.cursor_line = sel_end_ln
                    self.cursor_col = sel_end_col
                    
                    # Update state
                    self.last_move_was_partial = True
                    self.expected_selection = (ins_start_ln, ins_start_col, sel_end_ln, sel_end_col)
                    
                    self.emit("changed")
                return
        
        # No selection - swap current word with previous word
        # Only operate if cursor is on a word or right after a word
        if col > 0 and col < len(line) and is_word_separator(line[col]) and not is_word_separator(line[col - 1]):
            # Cursor is right after a word (before a separator) - find that word and move it left
            word_end = col
            word_start = col
            while word_start > 0 and word_start - 1 < len(line) and not is_word_separator(line[word_start - 1]):
                word_start -= 1
        elif col >= len(line) and col > 0 and not is_word_separator(line[col - 1]):
            # Cursor is at end of line, right after a word character
            word_end = col
            word_start = col
            while word_start > 0 and not is_word_separator(line[word_start - 1]):
                word_start -= 1
        elif col < len(line) and is_word_separator(line[col]):
            # Cursor is on a separator (not right after a word) - do nothing
            return
        elif col >= len(line):
            # Cursor is at end of line after a separator - do nothing
            return
        else:
            # Cursor is on a word character - find current word boundaries
            word_start = col
            while word_start > 0 and word_start <= len(line) and not is_word_separator(line[word_start - 1]):
                word_start -= 1
            
            word_end = col
            while word_end < len(line) and not is_word_separator(line[word_end]):
                word_end += 1
        
        # if word_start == 0:
        #    return  # Can't move left

        
        # Find previous word
        prev_word_end = word_start - 1
        while prev_word_end >= 0 and prev_word_end < len(line) and is_word_separator(line[prev_word_end]):
            prev_word_end -= 1
        prev_word_end += 1
        
        if prev_word_end == 0:
            # No previous word on this line - try previous line
            if ln == 0:
                return  # Can't move left from first line
            
            prev_ln = ln - 1
            
            # Skip empty lines to find the previous word
            while prev_ln >= 0:
                prev_line = self.get_line(prev_ln)
                
                # Find last word on this line
                prev_word_end = len(prev_line)
                while prev_word_end > 0 and is_word_separator(prev_line[prev_word_end - 1]):
                    prev_word_end -= 1
                
                # If we found a word on this line, break
                if prev_word_end > 0:
                    break
                
                # Otherwise, try previous line
                prev_ln -= 1
            
            # Check if we found a word
            if prev_ln < 0:
                return  # No word found
            
            prev_word_start = prev_word_end
            while prev_word_start > 0 and not is_word_separator(prev_line[prev_word_start - 1]):
                prev_word_start -= 1
            
            prev_word = prev_line[prev_word_start:prev_word_end]
            current_word = line[word_start:word_end]
            
            # Update current line: replace current_word with prev_word at the same position
            new_current_line = line[:word_start] + prev_word + line[word_end:]
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_current_line
            else:
                self.edits[ln] = new_current_line
            
            # Update previous line: replace prev_word with current_word at the same position
            new_prev_line = (prev_line[:prev_word_start] + 
                           current_word + 
                           prev_line[prev_word_end:])
            if prev_ln in self.inserted_lines:
                self.inserted_lines[prev_ln] = new_prev_line
            else:
                self.edits[prev_ln] = new_prev_line
            
            # Update cursor to moved position on previous line
            self.cursor_line = prev_ln
            self.cursor_col = prev_word_start + len(current_word)
            # Clear selection
            self.selection.set_start(self.cursor_line, self.cursor_col)
            self.selection.set_end(self.cursor_line, self.cursor_col)
            
            self.emit("changed")
            return
        
        prev_word_start = prev_word_end - 1
        while prev_word_start > 0 and prev_word_start <= len(line) and not is_word_separator(line[prev_word_start - 1]):
            prev_word_start -= 1
        
        current_word = line[word_start:word_end]
        prev_word = line[prev_word_start:prev_word_end]
        separators = line[prev_word_end:word_start]
        
        # Rebuild line with swapped text
        new_line = (line[:prev_word_start] + 
                   current_word + 
                   separators +
                   prev_word + 
                   line[word_end:])
        
        # Update line
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        # Move cursor and select
        self.cursor_col = prev_word_start
        self.selection.set_start(ln, prev_word_start)
        self.selection.set_end(ln, prev_word_start + len(current_word))
        
        self.emit("changed")
    
    def move_word_right_with_text(self):
        """Move text right: full words swap, partial selection moves 1 char (Alt+Right)"""
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        def is_word_separator(ch):
            """Check if character is a word separator (space, underscore, or punctuation)"""
            return ch in ' _' or not ch.isalnum()
        
        # Check if we have a selection
        if self.selection.has_selection():
            bounds = self.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_ln, start_col, end_ln, end_col = bounds
                
                # Handle multi-line selections - move character wise
                if start_ln != end_ln:
                    # Get selected text first to analyze structure
                    selected_text = self.get_selected_text()
                    sel_lines = selected_text.split('\n')
                    
                    # Extract structure: content and separators
                    first_line_text = sel_lines[0]
                    first_line_content = first_line_text.rstrip()
                    first_line_trailing = first_line_text[len(first_line_content):]
                    
                    last_line_text = sel_lines[-1]
                    last_line_content = last_line_text.lstrip()
                    last_line_leading = last_line_text[:len(last_line_text) - len(last_line_content)]
                    
                    # 1. Identify char after
                    last_line = self.get_line(end_ln)
                    if end_col < len(last_line):
                        # Char on same line
                        next_ln = end_ln
                        next_col = end_col + 1
                        char_after = last_line[end_col]
                    else:
                        # Newline at end of line
                        if end_ln >= self.total() - 1:
                            return # Can't move right
                        next_ln = end_ln + 1
                        next_col = 0
                        char_after = '\n'
                        
                    # 3. Extend selection to include char after
                    self.selection.set_end(next_ln, next_col)
                    
                    # 4. Delete extended range
                    self.delete_selection()
                    
                    # 5. Insert swapped: char_after + selected_text with spacing preserved
                    if char_after == '\n':
                        # Swapping with newline: preserve spacing structure
                        # Result should be: char_after + last_line_content + first_line_trailing + '\n' + first_line_leading + first_line_content + last_line_trailing
                        # But we need to handle middle lines too
                        if len(sel_lines) == 2:
                            # Simple case: swap first and last content, keep separators in place
                            full_text = char_after + last_line_content + first_line_trailing + '\n' + last_line_leading + first_line_content
                        else:
                            # Complex case with middle lines - for now, keep original behavior
                            full_text = char_after + selected_text
                    else:
                        # Swapping with regular char: keep original behavior
                        full_text = char_after + selected_text
                    
                    # Remember start position (cursor is already at start_ln, start_col from delete)
                    ins_start_ln = self.cursor_line
                    ins_start_col = self.cursor_col
                    
                    self.insert_text(full_text)
                    
                    # 6. Restore selection (selected_text part)
                    # Calculate start of selected_text (after char_after)
                    char_lines = char_after.split('\n')
                    if len(char_lines) == 1:
                        sel_start_ln = ins_start_ln
                        sel_start_col = ins_start_col + len(char_after)
                    else:
                        sel_start_ln = ins_start_ln + len(char_lines) - 1
                        sel_start_col = len(char_lines[-1])
                        
                    # End is current cursor
                    sel_end_ln = self.cursor_line
                    sel_end_col = self.cursor_col
                    
                    self.selection.set_start(sel_start_ln, sel_start_col)
                    self.selection.set_end(sel_end_ln, sel_end_col)
                    
                    self.emit("changed")
                    return
                
                selected_text = line[start_col:end_col]
                
                # Check if selection is full words (starts and ends at word boundaries)
                is_full_word_selection = True
                
                # Check start boundary
                if start_col > 0 and start_col <= len(line) and not is_word_separator(line[start_col - 1]):
                    is_full_word_selection = False
                if start_col < len(line) and is_word_separator(line[start_col]):
                    is_full_word_selection = False
                    
                # Check end boundary
                if end_col < len(line) and not is_word_separator(line[end_col]):
                    is_full_word_selection = False
                if end_col > 0 and end_col <= len(line) and is_word_separator(line[end_col - 1]):
                    is_full_word_selection = False
                
                # Check state: if we were moving partially, KEEP moving partially
                current_bounds = (start_ln, start_col, end_ln, end_col)
                if self.expected_selection != current_bounds:
                    # Sequence broken or new selection
                    self.last_move_was_partial = False
                
                if self.last_move_was_partial:
                    is_full_word_selection = False

                if is_full_word_selection:
                    # Full word(s) selected - swap with next word
                    # Find next word
                    next_word_start = end_col
                    while next_word_start < len(line) and is_word_separator(line[next_word_start]):
                        next_word_start += 1
                    
                    if next_word_start == len(line):
                        # No next word on this line - try next line
                        if ln >= self.total() - 1:
                            return  # Can't move right
                        
                        next_ln = ln + 1
                        
                        # Skip empty lines to find the next word
                        while next_ln < self.total():
                            next_line = self.get_line(next_ln)
                            
                            # Find first word on this line
                            next_word_start = 0
                            while next_word_start < len(next_line) and is_word_separator(next_line[next_word_start]):
                                next_word_start += 1
                            
                            # If we found a word on this line, break
                            if next_word_start < len(next_line):
                                break
                            
                            # Otherwise, try next line
                            next_ln += 1
                        
                        # Check if we found a word
                        if next_ln >= self.total():
                            return  # No word found
                        
                        next_word_end = next_word_start
                        while next_word_end < len(next_line) and not is_word_separator(next_line[next_word_end]):
                            next_word_end += 1
                        
                        next_word = next_line[next_word_start:next_word_end]
                        
                        # Get selected text
                        selected_text = line[start_col:end_col]
                        
                        # Everything after the selected word on current line (punctuation, spaces, etc.)
                        after_selected = line[end_col:]
                        
                        # Update current line: keep prefix + next_word + everything after selected word
                        new_current_line = line[:start_col] + next_word + after_selected
                        if ln in self.inserted_lines:
                            self.inserted_lines[ln] = new_current_line
                        else:
                            self.edits[ln] = new_current_line
                        
                        # Update next line: replace next_word with selected text (preserves any punctuation in selection)
                        new_next_line = next_line[:next_word_start] + selected_text + next_line[next_word_end:]
                        if next_ln in self.inserted_lines:
                            self.inserted_lines[next_ln] = new_next_line
                        else:
                            self.edits[next_ln] = new_next_line
                        
                        # Update selection to moved position on next line
                        self.selection.set_start(next_ln, next_word_start)
                        self.selection.set_end(next_ln, next_word_start + len(selected_text))
                        self.cursor_line = next_ln
                        self.cursor_col = next_word_start + len(selected_text)
                        
                        # Update state
                        self.last_move_was_partial = False
                        self.expected_selection = (next_ln, next_word_start, next_ln, next_word_start + len(selected_text))
                        
                        self.emit("changed")
                        return

                    next_word_end = next_word_start
                    while next_word_end < len(line) and not is_word_separator(line[next_word_end]):
                        next_word_end += 1
                    
                    next_word = line[next_word_start:next_word_end]
                    separators = line[end_col:next_word_start]
                    
                    # Rebuild line with swapped text
                    new_line = (line[:start_col] + 
                               next_word + 
                               separators +
                               selected_text + 
                               line[next_word_end:])
                    
                    # Update line
                    if ln in self.inserted_lines:
                        self.inserted_lines[ln] = new_line
                    else:
                        self.edits[ln] = new_line
                    
                    # Update selection to moved position
                    new_sel_start = start_col + len(next_word) + len(separators)
                    self.selection.set_start(ln, new_sel_start)
                    self.selection.set_end(ln, new_sel_start + len(selected_text))
                    self.cursor_col = new_sel_start + len(selected_text)
                    
                    # Update state
                    self.last_move_was_partial = False
                    self.expected_selection = (ln, new_sel_start, ln, new_sel_start + len(selected_text))
                    
                    self.emit("changed")
                else:
                    # Partial selection - ALWAYS use character-wise movement
                    # 1. Identify char after
                    last_line = self.get_line(end_ln)
                    if end_col < len(last_line):
                        # Char on same line
                        next_ln = end_ln
                        next_col = end_col + 1
                        char_after = last_line[end_col]
                    else:
                        # Newline at end of line
                        if end_ln >= self.total() - 1:
                            return  # Can't move right
                        next_ln = end_ln + 1
                        next_col = 0
                        char_after = '\n'
                    
                    # 2. Get selected text
                    selected_text = self.get_selected_text()
                    
                    # 3. Extend selection to include char after
                    self.selection.set_end(next_ln, next_col)
                    
                    # 4. Delete extended range
                    self.delete_selection()
                    
                    # 5. Insert swapped: char_after + selected_text
                    full_text = char_after + selected_text
                    
                    # Remember start position
                    ins_start_ln = self.cursor_line
                    ins_start_col = self.cursor_col
                    
                    self.insert_text(full_text)
                    
                    # 6. Restore selection (selected_text part)
                    # Calculate start of selected_text (after char_after)
                    char_lines = char_after.split('\n')
                    if len(char_lines) == 1:
                        sel_start_ln = ins_start_ln
                        sel_start_col = ins_start_col + len(char_after)
                    else:
                        sel_start_ln = ins_start_ln + len(char_lines) - 1
                        sel_start_col = len(char_lines[-1])
                    
                    # End is current cursor
                    sel_end_ln = self.cursor_line
                    sel_end_col = self.cursor_col
                    
                    self.selection.set_start(sel_start_ln, sel_start_col)
                    self.selection.set_end(sel_end_ln, sel_end_col)
                    
                    # Update cursor to end of selection
                    self.cursor_line = sel_end_ln
                    self.cursor_col = sel_end_col
                    
                    # Update state
                    self.last_move_was_partial = True
                    self.expected_selection = (sel_start_ln, sel_start_col, sel_end_ln, sel_end_col)
                    
                    self.emit("changed")
                return
        
        # No selection - swap current word with next word
        # Only operate if cursor is on a word or right after a word
        if col > 0 and col < len(line) and is_word_separator(line[col]) and not is_word_separator(line[col - 1]):
            # Cursor is right after a word (before a separator) - find that word and move it right
            word_end = col
            word_start = col
            while word_start > 0 and not is_word_separator(line[word_start - 1]):
                word_start -= 1
        elif col >= len(line) and col > 0 and not is_word_separator(line[col - 1]):
            # Cursor is at end of line, right after a word character
            word_end = col
            word_start = col
            while word_start > 0 and not is_word_separator(line[word_start - 1]):
                word_start -= 1
        elif col < len(line) and is_word_separator(line[col]):
            # Cursor is on a separator (not right after a word) - do nothing
            return
        elif col >= len(line):
            # Cursor is at end of line after a separator - do nothing
            return
        else:
            # Cursor is on a word character - find current word boundaries
            word_start = col
            while word_start > 0 and word_start <= len(line) and not is_word_separator(line[word_start - 1]):
                word_start -= 1
            
            word_end = col
            while word_end < len(line) and not is_word_separator(line[word_end]):
                word_end += 1
        
        # Find next word
        next_word_start = word_end
        while next_word_start < len(line) and is_word_separator(line[next_word_start]):
            next_word_start += 1
        
        if next_word_start >= len(line):
            # No next word on this line - try next line
            if ln >= self.total() - 1:
                return  # Can't move right
            
            next_ln = ln + 1
            next_line = self.get_line(next_ln)
            
            # Skip empty lines to find the next word
            while next_ln < self.total():
                next_line = self.get_line(next_ln)
                
                # Find first word on this line
                next_word_start = 0
                while next_word_start < len(next_line) and is_word_separator(next_line[next_word_start]):
                    next_word_start += 1
                
                # If we found a word on this line, break
                if next_word_start < len(next_line):
                    break
                
                # Otherwise, try next line
                next_ln += 1
            
            # Check if we found a word
            if next_ln >= self.total():
                return  # No word found
            
            next_word_end = next_word_start
            while next_word_end < len(next_line) and not is_word_separator(next_line[next_word_end]):
                next_word_end += 1
            
            next_word = next_line[next_word_start:next_word_end]
            current_word = line[word_start:word_end]
            
            # Everything after the current word on this line (includes punctuation and spaces)
            after_current_word = line[word_end:]
            
            # Update current line: prefix + next_word + everything that was after current_word
            new_current_line = line[:word_start] + next_word + after_current_word
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_current_line
            else:
                self.edits[ln] = new_current_line
            
            # Update next line: keep leading separators, add current_word, keep rest
            new_next_line = next_line[:next_word_start] + current_word + next_line[next_word_end:]
            if next_ln in self.inserted_lines:
                self.inserted_lines[next_ln] = new_next_line
            else:
                self.edits[next_ln] = new_next_line
            
            # Update cursor to moved position on next line
            self.cursor_line = next_ln
            self.cursor_col = next_word_start + len(current_word)
            # Clear selection
            self.selection.set_start(self.cursor_line, self.cursor_col)
            self.selection.set_end(self.cursor_line, self.cursor_col)
            
            self.emit("changed")
            return

        next_word_end = next_word_start
        while next_word_end < len(line) and not is_word_separator(line[next_word_end]):
            next_word_end += 1
        
        current_word = line[word_start:word_end]
        next_word = line[next_word_start:next_word_end]
        separators = line[word_end:next_word_start]
        
        # Rebuild line with swapped text
        new_line = (line[:word_start] + 
                   next_word + 
                   separators +
                   current_word + 
                   line[next_word_end:])
        
        # Update line
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        # Update cursor position
        new_cursor_col = word_start + len(next_word) + len(separators) + len(current_word)
        self.cursor_col = new_cursor_col
        self.selection.set_start(ln, new_cursor_col)
        self.selection.set_end(ln, new_cursor_col) # This line was syntactically incorrect in the instruction, assuming it meant to clear selection or set cursor as end. Reverting to original behavior of selecting the moved word.
        
        self.emit("changed")
    
    def move_line_up_with_text(self):
        """Move current line or selection up one line (Alt+Up)"""
        ln = self.cursor_line
        
        # Check if we have a selection
        if self.selection.has_selection():
            bounds = self.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_ln, start_col, end_ln, end_col = bounds
                
                # Handle multi-line selections - swap entire block with line above
                if start_ln != end_ln:
                    # Adjust end_ln if selection ends at the start of a line
                    effective_end_ln = end_ln
                    if end_col == 0:
                        effective_end_ln = end_ln - 1
                    
                    # If adjustment makes it a single line selection effectively, but start_ln != end_ln,
                    # we still treat it as a block swap of the effective lines.
                    
                    # Can't move up if first line is at top
                    if start_ln == 0:
                        return
                    
                    # Get the line above the selection
                    line_above = self.get_line(start_ln - 1)
                    
                    # Get all selected lines
                    selected_lines = []
                    for i in range(start_ln, effective_end_ln + 1):
                        selected_lines.append(self.get_line(i))
                    
                    # Move line above to the bottom of selection
                    if start_ln - 1 in self.inserted_lines:
                        self.inserted_lines[start_ln - 1] = selected_lines[0]
                    else:
                        self.edits[start_ln - 1] = selected_lines[0]
                    
                    # Shift all other selected lines up
                    for i in range(1, len(selected_lines)):
                        if start_ln - 1 + i in self.inserted_lines:
                            self.inserted_lines[start_ln - 1 + i] = selected_lines[i]
                        else:
                            self.edits[start_ln - 1 + i] = selected_lines[i]
                    
                    # Put line_above at the end
                    if effective_end_ln in self.inserted_lines:
                        self.inserted_lines[effective_end_ln] = line_above
                    else:
                        self.edits[effective_end_ln] = line_above
                    
                    # Update selection to new position
                    # We shift the original bounds up by 1
                    self.selection.set_start(start_ln - 1, start_col)
                    self.selection.set_end(end_ln - 1, end_col)
                    self.cursor_line = start_ln - 1
                    self.cursor_col = start_col
                    
                    self.emit("changed")
                    return
                
                # Single-line selection - move text to previous line
                # Can't move up if on first line
                if ln == 0:
                    return
                
                # Get current line and previous line
                current_line = self.get_line(ln)
                prev_line = self.get_line(ln - 1)
                
                # Extract selected text
                selected_text = current_line[start_col:end_col]
                
                # Remove selection from current line
                new_current_line = current_line[:start_col] + current_line[end_col:]
                
                # Insert selection into previous line at same column position
                insert_pos = min(start_col, len(prev_line))
                new_prev_line = prev_line[:insert_pos] + selected_text + prev_line[insert_pos:]
                
                # Update both lines
                if ln - 1 in self.inserted_lines:
                    self.inserted_lines[ln - 1] = new_prev_line
                else:
                    self.edits[ln - 1] = new_prev_line
                
                if ln in self.inserted_lines:
                    self.inserted_lines[ln] = new_current_line
                else:
                    self.edits[ln] = new_current_line
                
                # Update selection to new position
                self.selection.set_start(ln - 1, insert_pos)
                self.selection.set_end(ln - 1, insert_pos + len(selected_text))
                self.cursor_line = ln - 1
                self.cursor_col = insert_pos
                
                self.emit("changed")
                return
        
        # No selection - swap entire line
        # Check boundary - can't move up if on first line
        if ln == 0:
            return
        
        # Get current and previous line
        current_line = self.get_line(ln)
        prev_line = self.get_line(ln - 1)
        
        # Swap lines
        if ln - 1 in self.inserted_lines:
            self.inserted_lines[ln - 1] = current_line
        else:
            self.edits[ln - 1] = current_line
        
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = prev_line
        else:
            self.edits[ln] = prev_line
        
        # Move cursor to new line position
        self.cursor_line = ln - 1
        self.cursor_col = min(self.cursor_col, len(current_line))
        
        # Clear selection
        self.selection.clear()
        
        self.emit("changed")
    
    def move_line_down_with_text(self):
        """Move current line or selection down one line (Alt+Down)"""
        ln = self.cursor_line
        
        # Check if we have a selection
        if self.selection.has_selection():
            bounds = self.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_ln, start_col, end_ln, end_col = bounds
                
                # Handle multi-line selections - swap entire block with line below
                if start_ln != end_ln:
                    # Adjust end_ln if selection ends at the start of a line
                    effective_end_ln = end_ln
                    if end_col == 0:
                        effective_end_ln = end_ln - 1
                        
                    # Can't move down if last selected line is at bottom
                    if effective_end_ln >= self.total() - 1:
                        return
                    
                    # Get the line below the selection
                    line_below = self.get_line(effective_end_ln + 1)
                    
                    # Get all selected lines
                    selected_lines = []
                    for i in range(start_ln, effective_end_ln + 1):
                        selected_lines.append(self.get_line(i))
                    
                    # Put line_below at the start (where first selected line was)
                    if start_ln in self.inserted_lines:
                        self.inserted_lines[start_ln] = line_below
                    else:
                        self.edits[start_ln] = line_below
                    
                    # Put all selected lines after line_below
                    for i in range(len(selected_lines)):
                        if start_ln + 1 + i in self.inserted_lines:
                            self.inserted_lines[start_ln + 1 + i] = selected_lines[i]
                        else:
                            self.edits[start_ln + 1 + i] = selected_lines[i]
                    
                    # Update selection to new position (shifted down by 1)
                    self.selection.set_start(start_ln + 1, start_col)
                    self.selection.set_end(end_ln + 1, end_col)
                    self.cursor_line = start_ln + 1
                    self.cursor_col = start_col
                    
                    self.emit("changed")
                    return
                
                # Single-line selection - move text to next line
                # Can't move down if on last line
                if ln >= self.total() - 1:
                    return
                
                # Get current line and next line
                current_line = self.get_line(ln)
                next_line = self.get_line(ln + 1)
                
                # Extract selected text
                selected_text = current_line[start_col:end_col]
                
                # Remove selection from current line
                new_current_line = current_line[:start_col] + current_line[end_col:]
                
                # Insert selection into next line at same column position
                insert_pos = min(start_col, len(next_line))
                new_next_line = next_line[:insert_pos] + selected_text + next_line[insert_pos:]
                
                # Update both lines
                if ln in self.inserted_lines:
                    self.inserted_lines[ln] = new_current_line
                else:
                    self.edits[ln] = new_current_line
                
                if ln + 1 in self.inserted_lines:
                    self.inserted_lines[ln + 1] = new_next_line
                else:
                    self.edits[ln + 1] = new_next_line
                
                # Update selection to new position
                self.selection.set_start(ln + 1, insert_pos)
                self.selection.set_end(ln + 1, insert_pos + len(selected_text))
                self.cursor_line = ln + 1
                self.cursor_col = insert_pos
                
                self.emit("changed")
                return
        
        # No selection - swap entire line
        # Check boundary - can't move down if on last line
        if ln >= self.total() - 1:
            return
        
        # Get current and next line
        current_line = self.get_line(ln)
        next_line = self.get_line(ln + 1)
        
        # Swap lines
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = next_line
        else:
            self.edits[ln] = next_line
        
        if ln + 1 in self.inserted_lines:
            self.inserted_lines[ln + 1] = current_line
        else:
            self.edits[ln + 1] = current_line
        
        # Move cursor to new line position
        self.cursor_line = ln + 1
        self.cursor_col = min(self.cursor_col, len(current_line))
        
        # Clear selection
        self.selection.clear()
        
        self.emit("changed")




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
        
        # If there's a selection and not extending, move to start of selection
        if not extend_selection and b.selection.has_selection():
            start_ln, start_col, _, _ = b.selection.get_bounds()
            b.set_cursor(start_ln, start_col, extend_selection)
            return
        
        # Visual line movement when wrapping enabled
        if self.view.renderer.wrap_enabled:
            # Create cairo context for calculations
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_w = self.view.renderer.calculate_line_number_width(cr, b.total())
            alloc_w = self.view.get_width()
            
            # Get wrap points for current line
            wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, ln, ln_w, alloc_w)
            
            # Find which visual sub-line the cursor is on
            vis_idx = 0
            for i, (start, end) in enumerate(wrap_points):
                if start <= b.cursor_col <= end:
                    vis_idx = i
                    break
            
            # Calculate current visual x offset
            full_text = b.get_line(ln)
            start_col, end_col = wrap_points[vis_idx]
            
            if end_col > start_col:
                text_segment = full_text[start_col:end_col]
            else:
                text_segment = full_text[start_col:] if start_col < len(full_text) else ""
                
            col_in_segment = b.cursor_col - start_col
            
            layout = self.view.renderer.create_text_layout(cr, text_segment)
            is_rtl = detect_rtl_line(text_segment)
            text_w = self.view.renderer.get_text_width(cr, text_segment)
            base_x = self.view.renderer.calculate_text_base_x(is_rtl, text_w, alloc_w, ln_w, self.view.scroll_x)
            
            # Get pixel position of cursor
            def visual_byte_index(text, col):
                b = 0
                for ch in text[:col]:
                    b += len(ch.encode("utf-8"))
                return b
                
            idx = visual_byte_index(text_segment, col_in_segment)
            pos, _ = layout.get_cursor_pos(idx)
            cursor_x = base_x + (pos.x // Pango.SCALE)
            
            # Determine target line and visual index
            target_ln = ln
            target_vis_idx = vis_idx - 1
            
            if target_vis_idx < 0:
                # Move to previous logical line
                target_ln = ln - 1
                if target_ln >= 0:
                    target_wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, target_ln, ln_w, alloc_w)
                    target_vis_idx = len(target_wrap_points) - 1
                else:
                    # Start of file
                    if extend_selection:
                        b.set_cursor(0, 0, extend_selection)
                    return

            # Get wrap points for target line (if different)
            if target_ln != ln:
                target_wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, target_ln, ln_w, alloc_w)
            else:
                target_wrap_points = wrap_points
                
            # Get text segment for target visual line
            t_start, t_end = target_wrap_points[target_vis_idx]
            t_full_text = b.get_line(target_ln)
            
            if t_end > t_start:
                t_segment = t_full_text[t_start:t_end]
            else:
                t_segment = t_full_text[t_start:] if t_start < len(t_full_text) else ""
            
            # Find column in target segment closest to cursor_x
            t_is_rtl = detect_rtl_line(t_segment)
            t_text_w = self.view.renderer.get_text_width(cr, t_segment)
            t_base_x = self.view.renderer.calculate_text_base_x(t_is_rtl, t_text_w, alloc_w, ln_w, self.view.scroll_x)
            
            rel_x = cursor_x - t_base_x
            new_col_in_segment = self.view.pixel_to_column(cr, t_segment, rel_x)
            new_col_in_segment = max(0, min(new_col_in_segment, len(t_segment)))
            
            new_col = t_start + new_col_in_segment
            b.set_cursor(target_ln, new_col, extend_selection)
            return

        if ln > 0:
            # Can move up to previous line
            target = ln - 1
            target_line = b.get_line(target)
            
            if extend_selection:
                # When extending selection upward
                # Check if target is an empty line
                if len(target_line) == 0:
                    # Moving up to an empty line - go to position 0
                    b.set_cursor(target, 0, extend_selection)
                else:
                    # Normal selection - maintain column position if possible
                    new_col = min(b.cursor_col, len(target_line))
                    b.set_cursor(target, new_col, extend_selection)
            else:
                # Not extending selection - normal movement
                new_col = min(b.cursor_col, len(target_line))
                b.set_cursor(target, new_col, extend_selection)
        else:
            # Already on first line (line 0), can't move up
            # If extending selection, select to beginning of current line (like shift+home)
            if extend_selection:
                b.set_cursor(0, 0, extend_selection)

    def move_down(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        
        # If there's a selection and not extending, move to end of selection
        if not extend_selection and b.selection.has_selection():
            _, _, end_ln, end_col = b.selection.get_bounds()
            b.set_cursor(end_ln, end_col, extend_selection)
            return
        
        # Visual line movement when wrapping enabled
        if self.view.renderer.wrap_enabled:
            # Create cairo context for calculations
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_w = self.view.renderer.calculate_line_number_width(cr, b.total())
            alloc_w = self.view.get_width()
            
            # Get wrap points for current line
            wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, ln, ln_w, alloc_w)
            
            # Find which visual sub-line the cursor is on
            vis_idx = 0
            for i, (start, end) in enumerate(wrap_points):
                if start <= b.cursor_col <= end:
                    vis_idx = i
                    break
            
            # Calculate current visual x offset
            full_text = b.get_line(ln)
            start_col, end_col = wrap_points[vis_idx]
            
            if end_col > start_col:
                text_segment = full_text[start_col:end_col]
            else:
                text_segment = full_text[start_col:] if start_col < len(full_text) else ""
                
            col_in_segment = b.cursor_col - start_col
            
            layout = self.view.renderer.create_text_layout(cr, text_segment)
            is_rtl = detect_rtl_line(text_segment)
            text_w = self.view.renderer.get_text_width(cr, text_segment)
            base_x = self.view.renderer.calculate_text_base_x(is_rtl, text_w, alloc_w, ln_w, self.view.scroll_x)
            
            # Get pixel position of cursor
            def visual_byte_index(text, col):
                b = 0
                for ch in text[:col]:
                    b += len(ch.encode("utf-8"))
                return b
                
            idx = visual_byte_index(text_segment, col_in_segment)
            pos, _ = layout.get_cursor_pos(idx)
            cursor_x = base_x + (pos.x // Pango.SCALE)
            
            # Determine target line and visual index
            target_ln = ln
            target_vis_idx = vis_idx + 1
            
            if target_vis_idx >= len(wrap_points):
                # Move to next logical line
                target_ln = ln + 1
                target_vis_idx = 0
            
            if target_ln < b.total():
                # Get wrap points for target line (if different)
                if target_ln != ln:
                    target_wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, target_ln, ln_w, alloc_w)
                else:
                    target_wrap_points = wrap_points
                
                # Get text segment for target visual line
                t_start, t_end = target_wrap_points[target_vis_idx]
                t_full_text = b.get_line(target_ln)
                
                if t_end > t_start:
                    t_segment = t_full_text[t_start:t_end]
                else:
                    t_segment = t_full_text[t_start:] if t_start < len(t_full_text) else ""
                
                # Find column in target segment closest to cursor_x
                t_is_rtl = detect_rtl_line(t_segment)
                t_text_w = self.view.renderer.get_text_width(cr, t_segment)
                t_base_x = self.view.renderer.calculate_text_base_x(t_is_rtl, t_text_w, alloc_w, ln_w, self.view.scroll_x)
                
                rel_x = cursor_x - t_base_x
                new_col_in_segment = self.view.pixel_to_column(cr, t_segment, rel_x)
                new_col_in_segment = max(0, min(new_col_in_segment, len(t_segment)))
                
                new_col = t_start + new_col_in_segment
                b.set_cursor(target_ln, new_col, extend_selection)
                return
            elif extend_selection:
                # At end of file, select to end
                current_line = b.get_line(ln)
                b.set_cursor(ln, len(current_line), extend_selection)
                return

        if ln + 1 < b.total():
            # Can move down to next line
            target = ln + 1
            target_line = b.get_line(target)
            
            if extend_selection:
                # When extending selection downward
                current_line = b.get_line(ln)
                
                # Check if target is the last line (no newline after it)
                is_last_line = (target == b.total() - 1)
                
                # Special case: at column 0 of empty line
                if len(current_line) == 0 and b.cursor_col == 0:
                    if is_last_line:
                        # Empty line followed by last line - select to end of last line
                        b.set_cursor(target, len(target_line), extend_selection)
                    else:
                        # Empty line with more lines after - select just the newline
                        b.set_cursor(target, 0, extend_selection)
                else:
                    # Normal selection - maintain column position
                    new_col = min(b.cursor_col, len(target_line))
                    b.set_cursor(target, new_col, extend_selection)
            else:
                # Not extending selection - normal movement
                new_col = min(b.cursor_col, len(target_line))
                b.set_cursor(target, new_col, extend_selection)
        else:
            # Already on last line, can't move down
            # If extending selection, select to end of current line (like shift+end)
            if extend_selection:
                current_line = b.get_line(ln)
                b.set_cursor(ln, len(current_line), extend_selection)

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


# ============================================================
#   RENDERER
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 12")
        self.right_margin_width = 20  # Right gutter width

        # Correct GTK4/Pango method to compute line height:
        # Use logical extents, not ink extents.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_text("Ag", -1)  # Reliable glyph pair for height

        ink_rect, logical_rect = layout.get_pixel_extents()

        # These are the correct text and line heights
        self.text_h = logical_rect.height
        self.line_h = self.text_h

        # Track maximum line width for horizontal scrollbar
        self.max_line_width = 0
        self.needs_full_width_scan = False  # Flag to scan all lines after file load
        
        # Word wrap support
        self.wrap_enabled = False
        self.wrap_width = 0  # Available width for text wrapping (viewport - line numbers)
        self.wrap_cache = {}  # Cache: {logical_line: [(start_col, end_col), ...]}
        self.visual_line_map = []  # List of (logical_line, visual_line_index) tuples
        self.total_visual_lines_cache = None  # Cache for total visual lines
        self.visual_line_anchor = (0, 0)  # (visual_line, logical_line) for fast lookup
        
        # OPTIMIZATION: Performance tracking for large files
        self.use_estimation_threshold = 50000  # Use estimation for files larger than this
        self.estimated_total_cache = None  # Fast estimated total for huge files
        self.edits_since_cache_invalidation = 0  # Track edits to avoid over-invalidating
        
        # AGGRESSIVE OPTIMIZATION: Fast approximation during rapid scrolling
        self.use_fast_approximation = False  # Toggle for approximate wrapping
        self.avg_wraps_per_line = 1.2  # Running average for approximation
        
        # Calculate average character width dynamically
        layout.set_text("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", -1)
        ink, logical = layout.get_pixel_extents()
        self.avg_char_width = logical.width / 62.0
        
        # Colors - will be updated based on theme
        self.update_colors_for_theme()

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

        # Use theme-appropriate background colors
        # In GTK4, we avoid deprecated get_style_context() and lookup_color()
        # Instead, use sensible defaults based on the theme mode
        if is_dark:
            r, g, b, a = self.hex_to_rgba_floats("#191919")
            self.editor_background_color = (r, g, b, a)
        else:
            r, g, b, a = self.hex_to_rgba_floats("#fafafa")
            self.editor_background_color = (r, g, b, a)

        # The other colors stay user-defined as before
        if is_dark:
            self.text_foreground_color = (0.90, 0.90, 0.90)
            self.linenumber_foreground_color = (0.60, 0.60, 0.60)
            self.selection_background_color = (0.2, 0.4, 0.6)
            self.selection_foreground_color = (1.0, 1.0, 1.0)
        else:
            self.text_foreground_color = (0.10, 0.10, 0.10)
            self.linenumber_foreground_color = (0.50, 0.50, 0.50)
            self.selection_background_color = (0.6, 0.8, 1.0)
            self.selection_foreground_color = (0.0, 0.0, 0.0)


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
        layout.set_font_description(self.font)
        if auto_dir:
            layout.set_auto_dir(True)
        if text:
            layout.set_text(text, -1)
        return layout

    def scan_for_max_width(self, cr, buf):
        """Scan buffer to find maximum line width."""
        layout = self.create_text_layout(cr)
        total = buf.total()
        ln_width = self.calculate_line_number_width(cr, total)
        max_width = 0
        
        # Scan first 1000 lines to get a quick estimate
        # For huge files, scanning everything is too slow
        scan_limit = min(1000, total)
        for ln in range(scan_limit):
            text = buf.get_line(ln)
            if text:
                layout.set_text(text, -1)
                ink, logical = layout.get_pixel_extents()
                text_w = logical.width
                line_total_width = ln_width + text_w
                if line_total_width > max_width:
                    max_width = line_total_width
        
        self.max_line_width = max_width
        self.needs_full_width_scan = False

    def calculate_max_line_width(self, cr, buf):
        """Calculate the maximum line width across all lines in the buffer"""
        if not buf:
            self.max_line_width = 0
            return
        
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_auto_dir(True)
        
        max_width = 0
        total = buf.total()
        ln_width = self.calculate_line_number_width(cr, total)
        
        # Check all lines
        for ln in range(total):
            text = buf.get_line(ln)
            if text:
                layout.set_text(text, -1)
                ink, logical = layout.get_pixel_extents()
                text_w = logical.width
                line_total_width = ln_width + text_w
                if line_total_width > max_width:
                    max_width = line_total_width
        
        self.max_line_width = max_width
    
    def get_text_width(self, cr, text):
        """Calculate actual pixel width of text using Pango"""
        if not text:
            return 0
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_text(text, -1)
        width, _ = layout.get_pixel_size()
        return width

    def calculate_text_base_x(self, is_rtl, text_w, view_w, ln_width, scroll_x):
        """Calculate base X position for text rendering.
        
        Args:
            is_rtl: Whether the text is RTL
            text_w: Text width in pixels
            view_w: Viewport width in pixels
            ln_width: Line number column width in pixels
            scroll_x: Horizontal scroll offset
            
        Returns:
            Base X coordinate for text rendering
        """
        if is_rtl:
            available = max(0, view_w - ln_width - self.right_margin_width - 5)
            # Unified formula: right-align and apply scroll offset
            return ln_width + max(0, available - text_w) - scroll_x
        else:
            return ln_width - scroll_x

    def calculate_line_number_width(self, cr, total_lines):
        """Calculate width needed for line numbers based on total lines"""
        # Format the largest line number
        max_line_num = str(total_lines)
        width = self.get_text_width(cr, max_line_num)
        return width + 15  # Add padding (5px left + 10px right margin)

    def calculate_wrap_points(self, cr, text, max_width):
        """Calculate wrap points for a line using smart word wrapping.
        
        Returns list of (start_col, end_col) tuples for each visual line.
        Uses Pango's built-in word wrapping for accurate results.
        """
        if not text or not self.wrap_enabled or max_width <= 0:
            return [(0, len(text))]
        
        # Create layout with wrap enabled
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_auto_dir(True)
        layout.set_text(text, -1)
        layout.set_width(max_width * Pango.SCALE)  # Set wrap width in Pango units
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)  # Smart wrap: prefer words, fall back to char
        
        # Get line count from wrapped layout
        line_count = layout.get_line_count()
        
        if line_count == 1:
            return [(0, len(text))]
        
        # Extract wrap points from Pango layout
        wrap_points = []
        for i in range(line_count):
            line = layout.get_line_readonly(i)
            start_index = line.start_index
            length = line.length
            
            # Convert byte indices to character indices
            start_col = len(text.encode('utf-8')[:start_index].decode('utf-8'))
            end_byte = start_index + length
            end_col = len(text.encode('utf-8')[:end_byte].decode('utf-8'))
            
            wrap_points.append((start_col, end_col))
        
        return wrap_points
    
    def get_wrap_points_for_line(self, cr, buf, ln, ln_width, viewport_width):
        """Get wrap points for a specific line, calculating on-demand and caching.
        
        This lazy approach avoids freezing on large files.
        """
        # Check if already cached
        if ln in self.wrap_cache:
            return self.wrap_cache[ln]
        
        # Calculate wrap points for this line
        text = buf.get_line(ln)
        max_text_width = max(100, viewport_width - ln_width - self.right_margin_width)
        wrap_points = self.calculate_wrap_points(cr, text, max_text_width)
        
        # Cache the result
        self.wrap_cache[ln] = wrap_points
        return wrap_points
    
    def get_visual_line_count_for_logical(self, cr, buf, ln, ln_width, viewport_width, allow_approximation=False):
        """Get the number of visual lines for a specific logical line.
        
        Args:
            allow_approximation: If True and file is huge, use fast approximation
        """
        if not self.wrap_enabled:
            return 1
        
        # AGGRESSIVE OPTIMIZATION: For huge files during scrolling, use approximation
        if allow_approximation and self.use_fast_approximation:
            # Quick approximation based on line length
            text = buf.get_line(ln)
            if not text:
                return 1
            
            # Approximate: chars per visual line
            max_width = viewport_width - ln_width - self.right_margin_width
            if max_width <= 0:
                return 1
            
            chars_per_line = max(1, int(max_width / self.avg_char_width))
            approx_wraps = max(1, (len(text) + chars_per_line - 1) // chars_per_line)
            
            return approx_wraps
        
        wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
        return len(wrap_points)
    
    def get_total_visual_lines(self, cr, buf, ln_width, viewport_width):
        """Get total number of visual lines.
        
        OPTIMIZED: Uses fast estimation for large files (50k+ lines)
        to avoid expensive calculation on every scroll/cursor movement.
        """
        if not self.wrap_enabled:
            return buf.total()
        
        # FIRST CHECK: Return locked value immediately if cache is locked
        if hasattr(self, 'total_visual_lines_locked') and self.total_visual_lines_locked:
            if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache is not None:
                return self.total_visual_lines_cache
        
        # SECOND CHECK: Return cached value if available and valid
        if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache is not None and self.total_visual_lines_cache > 0:
            return self.total_visual_lines_cache
        
        total_logical = buf.total()
        
        # OPTIMIZATION: For huge files (>10M lines), use minimal sampling
        if total_logical > 100_000:
            # Check if we have a cached estimate
            if self.estimated_total_cache is not None:
                return self.estimated_total_cache
            
            # Ultra-fast estimation for huge files: sample only 50 lines
            sample_size = 50
            sample_step = max(1, total_logical // sample_size)
            sample_total = 0
            samples_taken = 0
            
            for i in range(0, total_logical, sample_step):
                if samples_taken >= sample_size:
                    break
                wrap_count = self.get_visual_line_count_for_logical(
                    cr, buf, i, ln_width, viewport_width,
                    allow_approximation=True  # Use fast approximation for sampling
                )
                sample_total += wrap_count
                samples_taken += 1
            
            if samples_taken > 0:
                avg_wraps = sample_total / samples_taken
                estimated = int(total_logical * avg_wraps)
                
                # Add 2% safety margin to ensure we can scroll to end
                estimated = int(estimated * 1.02)
                
                # Cache the estimate
                self.estimated_total_cache = estimated
                return estimated
            else:
                # Fallback: assume 1:1
                return total_logical
        
        # OPTIMIZATION: For large files (50k-10M lines), use moderate sampling
        if total_logical > self.use_estimation_threshold:
            # Check if we have a cached estimate
            if self.estimated_total_cache is not None:
                return self.estimated_total_cache
            
            # Create estimate by sampling lines throughout the file
            # Reduce sample size for better performance
            sample_size = min(100, total_logical)  # Reduced from 200
            sample_step = max(1, total_logical // sample_size)
            sample_total = 0
            samples_taken = 0
            
            for i in range(0, total_logical, sample_step):
                if samples_taken >= sample_size:
                    break
                wrap_count = self.get_visual_line_count_for_logical(
                    cr, buf, i, ln_width, viewport_width,
                    allow_approximation=True  # Use fast approximation for sampling
                )
                sample_total += wrap_count
                samples_taken += 1
            
            if samples_taken > 0:
                avg_wraps = sample_total / samples_taken
                estimated = int(total_logical * avg_wraps)
                
                # Add 2% safety margin to ensure we can scroll to end
                estimated = int(estimated * 1.02)
                
                # Cache the estimate
                self.estimated_total_cache = estimated
                return estimated
            else:
                # Fallback: assume 1:1
                return total_logical
        
        # For small files (< 50k lines), calculate exactly
        if total_logical <= 5000:
            total_visual = 0
            for ln in range(total_logical):
                total_visual += self.get_visual_line_count_for_logical(cr, buf, ln, ln_width, viewport_width)
            self.total_visual_lines_cache = total_visual
            return total_visual
        
        # For medium files (5k-50k lines), use cached values + estimation
        cached_count = 0
        cached_visual = 0
        
        for ln, wrap_points in self.wrap_cache.items():
            if ln < total_logical:
                cached_count += 1
                cached_visual += len(wrap_points)
        
        uncached_count = total_logical - cached_count
        if uncached_count <= 0:
            self.total_visual_lines_cache = cached_visual
            return cached_visual

        # Sample for better estimation
        if cached_count < 100:
            sample_size = min(100, total_logical)  # Reduced from 200
            sample_visual = 0
            sample_step = max(1, total_logical // sample_size)
            
            for i in range(sample_size):
                ln = (i * sample_step) % total_logical
                sample_visual += self.get_visual_line_count_for_logical(cr, buf, ln, ln_width, viewport_width)
            
            avg_visual_per_line = sample_visual / sample_size
            total_visual = int(total_logical * avg_visual_per_line)
        elif cached_count > 0:
            avg_visual_per_line = cached_visual / cached_count
            estimated_uncached = int(uncached_count * avg_visual_per_line)
            total_visual = cached_visual + estimated_uncached
        else:
            total_visual = total_logical
        
        # Ensure at least one visual line per logical line
        total_visual = max(total_visual, total_logical)
        
        # Add small buffer for estimation errors
        total_visual = int(total_visual * 1.01) + 10
        
        self.total_visual_lines_cache = total_visual
        return total_visual
    
    def get_accurate_total_visual_lines_at_end(self, cr, buf, ln_width, viewport_width, visible_lines, lines_to_calc=None):
        """Calculate accurate total visual lines for scrolling to end of document.
        
        This ensures no extra scrollable space at the end by computing exact
        visual line counts for the last portion of the document.
        
        Args:
            lines_to_calc: Number of logical lines to calculate exactly from the end.
                          If None, defaults to visible_lines * 3.
        """
        total_logical = buf.total()
        if total_logical == 0:
            return 0
        
        # Determine how many lines to calculate exactly
        if lines_to_calc is None:
            lines_to_calc = visible_lines * 3
        
        # For accuracy near the end, compute exact values for last N logical lines
        lines_to_compute_exact = min(total_logical, lines_to_calc)
        start_line = max(0, total_logical - lines_to_compute_exact)
        
        # Estimate visual lines for lines before start_line using cached data
        estimated_before = 0
        cached_count = 0
        cached_visual = 0
        
        if start_line > 0:
            # Count how many cached lines we have before start_line
            for ln in range(start_line):
                if ln in self.wrap_cache:
                    cached_count += 1
                    cached_visual += len(self.wrap_cache[ln])
            
            # Estimate uncached lines before start_line
            uncached_before = start_line - cached_count
            if uncached_before > 0:
                if cached_count > 0:
                    # Use average from cached lines
                    avg_visual_per_line = cached_visual / cached_count
                    estimated_uncached = int(uncached_before * avg_visual_per_line)
                else:
                    # No cached data - assume 1:1 ratio as conservative estimate
                    estimated_uncached = uncached_before
                
                estimated_before = cached_visual + estimated_uncached
            else:
                estimated_before = cached_visual
        
        # Calculate exact visual lines for the end portion
        exact_at_end = 0
        for ln in range(start_line, total_logical):
            exact_at_end += self.get_visual_line_count_for_logical(cr, buf, ln, ln_width, viewport_width)
        
        total_visual = estimated_before + exact_at_end
        return total_visual
    
    def logical_to_visual_line(self, cr, buf, logical_line, column, ln_width, viewport_width):
        """Convert logical line and column to visual line number.
        
        Calculates on-demand with optimization for large files.
        """
        if not self.wrap_enabled:
            return logical_line
        
        total_logical = buf.total()
        logical_line = max(0, min(logical_line, total_logical - 1))
        
        # Use anchor if available and closer than start
        anchor_vis, anchor_log = self.visual_line_anchor
        
        visual_line = 0
        start_ln = 0
        
        # Decide whether to start from 0 or anchor
        if abs(logical_line - anchor_log) < logical_line:
            visual_line = anchor_vis
            start_ln = anchor_log
            
        # Calculate visual lines difference
        if start_ln < logical_line:
            # Going forward
            
            # If gap is huge and we have file stats, use estimation for the bulk
            # But only if we are NOT close to the target (accuracy matters for scrollbar sync)
            # Actually, for scrollbar sync, we need fairly good accuracy or the thumb jumps.
            # But iterating 1 million lines is too slow.
            # We can use the cached total if we are calculating for the end.
            
            # Use cache for lines in between
            current_ln = start_ln
            
            while current_ln < logical_line:
                # Optimization: if we have a huge gap, skip using estimation
                dist = logical_line - current_ln
                
                # Check if we can use optimization (either have cache or file stats)
                can_optimize = False
                if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache:
                    can_optimize = True
                elif hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
                    can_optimize = True

                if dist > 5000 and can_optimize:
                     # Estimate a chunk
                     chunk_size = dist - 1000 # Leave 1000 lines for exact calc
                     
                     # Determine the best estimation ratio
                     est_visual_per_line = 1.0
                     
                     # BEST: Use the cached total if available (matches visual_to_logical_line)
                     if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache:
                         est_visual_per_line = self.total_visual_lines_cache / max(1, total_logical)
                     
                     # FALLBACK: Use byte-based estimation
                     elif hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
                         total_bytes = len(buf.file.mm)
                         avg_bytes_per_line = total_bytes / max(1, total_logical)
                         avg_content_len = max(0, avg_bytes_per_line - 1)
                         max_text_width = max(100, viewport_width - ln_width)
                         chars_per_line = max(1, int(max_text_width / self.avg_char_width))
                         est_visual_per_line = max(1.0, (avg_content_len + chars_per_line - 1) / chars_per_line)
                     
                     visual_line += int(chunk_size * est_visual_per_line)
                     current_ln += chunk_size
                     continue

                visual_line += self.get_visual_line_count_for_logical(
                    cr, buf, current_ln, ln_width, viewport_width, 
                    allow_approximation=self.use_fast_approximation
                )
                current_ln += 1
                
        elif start_ln > logical_line:
            # Going backward
            current_ln = start_ln - 1
            while current_ln >= logical_line:
                visual_line -= self.get_visual_line_count_for_logical(
                    cr, buf, current_ln, ln_width, viewport_width,
                    allow_approximation=self.use_fast_approximation
                )
                current_ln -= 1
    
        # Update anchor
        self.visual_line_anchor = (visual_line, logical_line)
    
        # Find which visual line within this logical line
        wrap_points = self.get_wrap_points_for_line(cr, buf, logical_line, ln_width, viewport_width)
        for vis_idx, (start_col, end_col) in enumerate(wrap_points):
            if start_col <= column <= end_col:
                return visual_line + vis_idx
        
        # Default to first visual line of this logical line
        return visual_line
    
    def estimate_visual_line_count(self, text, max_width):
        """Estimate visual line count without Pango layout.
        
        Used for skipping lines quickly in large files.
        """
        if not text:
            return 1
            
        # Estimate based on character width
        est_chars_per_line = max(1, int(max_width / self.avg_char_width))
        return max(1, (len(text) + est_chars_per_line - 1) // est_chars_per_line)

    def visual_to_logical_line(self, cr, buf, visual_line, ln_width, viewport_width):
        """Convert visual → logical safely.
           When wrap_cache is empty, avoid Pango completely (prevents freeze).
        """

        # ----- FREEZE FIX -----
        if self.wrap_enabled and not self.wrap_cache:
            total = buf.total()
            vline = max(0, min(visual_line, total - 1))
            text = buf.get_line(vline)
            return (vline, 0, 0, len(text))
        # -----------------------

        if not self.wrap_enabled or visual_line < 0:
            total = buf.total()
            vline = max(0, min(visual_line, total - 1))
            text = buf.get_line(vline)
            return (vline, 0, 0, len(text))

        total_logical = buf.total()

        # OPTIMIZATION: If we are close to the end (and have a cached total), scan backwards from the end.
        # This avoids cumulative estimation errors from the start of the file.
        if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache:
            total_visual = self.total_visual_lines_cache
            dist_from_end = total_visual - visual_line
            
            # If within 5000 visual lines of the end, scan backwards from the last line
            if 0 <= dist_from_end < 5000:
                current_visual = total_visual
                ln = total_logical - 1
                
                while ln >= 0:
                    # Get visual count for this line
                    if ln in self.wrap_cache:
                        num_visual = len(self.wrap_cache[ln])
                    else:
                        # For backward scan near end, we want accuracy, so calculate if close
                        if dist_from_end < 100:
                            wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
                            num_visual = len(wrap_points)
                        else:
                            text = buf.get_line(ln)
                            max_text_width = max(100, viewport_width - ln_width)
                            num_visual = self.estimate_visual_line_count(text, max_text_width)
                    
                    # Subtract this line's visual height
                    current_visual -= num_visual
                    
                    # Check if we passed the target
                    if current_visual <= visual_line:
                        # Found it! The target visual_line is within this logical line 'ln'
                        vis_idx = visual_line - current_visual
                        
                        # Ensure valid index
                        if ln not in self.wrap_cache:
                             wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
                        else:
                             wrap_points = self.wrap_cache[ln]
                        
                        if vis_idx >= len(wrap_points):
                            vis_idx = max(0, len(wrap_points) - 1)
                            
                        self.visual_line_anchor = (current_visual, ln)
                        col_start, col_end = wrap_points[vis_idx]
                        return (ln, vis_idx, col_start, col_end)
                    
                    ln -= 1
                    
                # Fallback if loop finishes (shouldn't happen if total_visual is correct)
                return (0, 0, 0, 0)

        anchor_vis, anchor_log = self.visual_line_anchor if self.visual_line_anchor else (0, 0)
        
        # Optimize: Check if we can search backwards from anchor
        if visual_line < anchor_vis and anchor_vis - visual_line < 100:
            # Search backwards from anchor
            current_visual = anchor_vis
            ln = anchor_log
            
            # We need to find the line containing visual_line
            # Iterate backwards
            while current_visual > visual_line and ln > 0:
                ln -= 1
                # We need the visual count of the PREVIOUS line
                if ln in self.wrap_cache:
                    num_visual = len(self.wrap_cache[ln])
                else:
                    # Estimate or calculate
                    if current_visual - visual_line < 50:
                        wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
                        num_visual = len(wrap_points)
                    else:
                        text = buf.get_line(ln)
                        max_text_width = max(100, viewport_width - ln_width)
                        num_visual = self.estimate_visual_line_count(text, max_text_width)
                
                current_visual -= num_visual
                
                if current_visual <= visual_line:
                    # Found the line!
                    # visual_line is inside this logical line 'ln'
                    # relative index: visual_line - current_visual
                    vis_idx = visual_line - current_visual
                    
                    # Ensure we have wrap points for the final result
                    if ln not in self.wrap_cache:
                         wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
                    else:
                         wrap_points = self.wrap_cache[ln]
                         
                    if vis_idx >= len(wrap_points):
                        vis_idx = len(wrap_points) - 1
                        
                    self.visual_line_anchor = (current_visual, ln)
                    col_start, col_end = wrap_points[vis_idx]
                    return (ln, vis_idx, col_start, col_end)
            
            # If we fell through (ln=0), treat as start
            if ln == 0 and current_visual > visual_line:
                 # Should not happen if logic is correct unless visual_line < 0
                 pass

        if visual_line >= anchor_vis:
            current_visual = anchor_vis
            start_log = anchor_log
        else:
            current_visual = 0
            start_log = 0

        # Binary search optimization for large jumps
        distance = visual_line - current_visual
        
        # Check if we can use optimization (either have cache or file stats)
        can_optimize = False
        if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache:
            can_optimize = True
        elif hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
            can_optimize = True
            
        if distance > 1000 and can_optimize:
            # Determine the best estimation ratio
            est_visual_per_line = 1.0
            
            # BEST: Use the cached total if available (this matches the scrollbar exactly)
            if hasattr(self, 'total_visual_lines_cache') and self.total_visual_lines_cache:
                est_visual_per_line = self.total_visual_lines_cache / max(1, total_logical)
            
            # FALLBACK: Use byte-based estimation if we have file stats
            elif hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
                total_bytes = len(buf.file.mm)
                avg_bytes_per_line = total_bytes / max(1, total_logical)
                avg_content_len = max(0, avg_bytes_per_line - 1)
                max_text_width = max(100, viewport_width - ln_width)
                chars_per_line = max(1, int(max_text_width / self.avg_char_width))
                est_visual_per_line = max(1.0, (avg_content_len + chars_per_line - 1) / chars_per_line)
            
            # Estimate logical line from visual line
            # visual_diff = logical_diff * est_visual_per_line
            # logical_diff = visual_diff / est_visual_per_line
            estimated_logical = start_log + int(distance / est_visual_per_line)
            estimated_logical = max(start_log, min(estimated_logical, total_logical - 1))
            
            # Binary search around the estimate
            # Increase range to handle estimation errors (2% of file or at least 5000 lines)
            search_range = max(5000, total_logical // 50)
            left = max(start_log, estimated_logical - search_range)
            right = min(total_logical - 1, estimated_logical + search_range)
            
            # Use pure estimation for initial visual position at left
            # Calculate relative to start_log which is the anchor
            # current_visual is the visual line at start_log
            
            # Binary search with estimation
            test_visual = current_visual # Default if loop doesn't run or finds nothing
            
            while left < right:
                mid = (left + right) // 2
                
                # Estimate visual line at mid relative to start_log
                mid_visual = current_visual + int((mid - start_log) * est_visual_per_line)
                
                if mid_visual < visual_line:
                    left = mid + 1
                    # Track the visual line at the new left
                    test_visual = current_visual + int((left - start_log) * est_visual_per_line)
                else:
                    right = mid
                    # If we move right down, we don't update test_visual because 
                    # we want the visual line corresponding to the final 'left'
            
            # Now we have an approximate logical line
            current_visual = int(test_visual)
            start_log = max(0, left - 10)  # Start a bit before for safety
            
            # Recalculate current_visual for start_log using the SAME estimation
            # This ensures we are consistent with the jump
            # FIX: Estimate up to (start_log - 10) to avoid double counting in the loop below
            fine_tune_window = 10
            base_log = max(0, start_log - fine_tune_window)
            
            current_visual = 0
            if base_log > 0:
                current_visual = int(base_log * est_visual_per_line)
            
            # Fine-tune with actual calculation for last few lines
            # This bridges the gap between our estimation and the actual state at start_log
            for ln in range(base_log, start_log):
                if ln in self.wrap_cache:
                    current_visual += len(self.wrap_cache[ln])
                else:
                    text = buf.get_line(ln)
                    max_text_width = max(100, viewport_width - ln_width)
                    current_visual += self.estimate_visual_line_count(text, max_text_width)

        # Linear search with estimation (optimized for remaining distance)
        scan_count = 0
        for ln in range(start_log, total_logical):
            scan_count += 1

            if ln in self.wrap_cache:
                wrap_points = self.wrap_cache[ln]
                num_visual_in_line = len(wrap_points)
            else:
                # close enough → compute exact
                if visual_line - current_visual < 50:
                    wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
                    num_visual_in_line = len(wrap_points)
                else:
                    # distant → estimate only (fast)
                    text = buf.get_line(ln)
                    max_text_width = max(100, viewport_width - ln_width)
                    num_visual_in_line = self.estimate_visual_line_count(text, max_text_width)
                    wrap_points = None

            if current_visual + num_visual_in_line > visual_line:

                if wrap_points is None:
                    wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)

                vis_idx = visual_line - current_visual
                # Ensure vis_idx is within valid bounds
                if vis_idx < 0:
                    vis_idx = 0
                if vis_idx >= len(wrap_points):
                    vis_idx = len(wrap_points) - 1
                
                # Additional safety check
                if vis_idx < 0 or vis_idx >= len(wrap_points) or len(wrap_points) == 0:
                    # Fallback to safe values
                    text = buf.get_line(ln)
                    self.visual_line_anchor = (current_visual, ln)
                    return (ln, 0, 0, len(text))

                self.visual_line_anchor = (current_visual, ln)

                col_start, col_end = wrap_points[vis_idx]
                return (ln, vis_idx, col_start, col_end)

            current_visual += num_visual_in_line

        # fallback
        last_ln = total_logical - 1
        text = buf.get_line(last_ln)
        return (last_ln, 0, 0, len(text))

    
    def build_visual_line_map(self, cr, buf, ln_width, viewport_width):
        """Build complete visual line mapping for the buffer.
        
        DEPRECATED: This method is no longer used.
        Kept for compatibility but does nothing.
        Visual line mapping is now done on-demand.
        """
        # Store viewport width for on-demand calculations
        if self.wrap_enabled:
            self.wrap_width = max(100, viewport_width - ln_width)
        
        # No longer build full map to avoid freezing on large files
        pass
    
    def visual_to_logical(self, visual_line):
        """Convert visual line number to (logical_line, visual_index, col_start, col_end).
        
        DEPRECATED: Use visual_to_logical_line() instead.
        This version works with cached visual_line_map if available.
        """
        if visual_line < 0 or visual_line >= len(self.visual_line_map):
            return (0, 0, 0, 0)
        
        logical_line, vis_idx = self.visual_line_map[visual_line]
        
        # Get column range from wrap cache
        if logical_line in self.wrap_cache and vis_idx < len(self.wrap_cache[logical_line]):
            col_start, col_end = self.wrap_cache[logical_line][vis_idx]
        else:
            col_start, col_end = (0, 0)
        
        return (logical_line, vis_idx, col_start, col_end)
    
    def logical_to_visual(self, logical_line, column=0):
        """Convert (logical_line, column) to visual line number.
        
        DEPRECATED: Use logical_to_visual_line() instead.
        This version works with cached visual_line_map if available.
        """
        if not self.wrap_enabled or logical_line not in self.wrap_cache:
            # Find the first visual line for this logical line
            for vis_line, (log_line, vis_idx) in enumerate(self.visual_line_map):
                if log_line == logical_line:
                    return vis_line
            return 0
        
        # Find which visual line contains this column
        wrap_points = self.wrap_cache[logical_line]
        for vis_idx, (start_col, end_col) in enumerate(wrap_points):
            if start_col <= column <= end_col:
                # Find the visual line number for this (logical, vis_idx)
                for vis_line, (log_line, v_idx) in enumerate(self.visual_line_map):
                    if log_line == logical_line and v_idx == vis_idx:
                        return vis_line
        
        # Default to first visual line of this logical line
        for vis_line, (log_line, vis_idx) in enumerate(self.visual_line_map):
            if log_line == logical_line and vis_idx == 0:
                return vis_line
        
        return 0
    
    def invalidate_wrap_cache(self, from_line=0):
        """Invalidate wrap cache from a specific line onward.
        
        Call this when text is modified to trigger recalculation.
        """
        # Clear cache entries for affected lines
        lines_to_remove = [ln for ln in self.wrap_cache.keys() if ln >= from_line]
        for ln in lines_to_remove:
            del self.wrap_cache[ln]
        
        # DON'T clear total visual lines cache - causes jumping
        # self.total_visual_lines_cache = None
        
        # Reset anchor if invalidation affects it
        if from_line <= self.visual_line_anchor[1]:
            self.visual_line_anchor = (0, 0)
        
        # Mark that visual line map needs rebuild
        # (Will be rebuilt on next draw)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x,
            cursor_visible=True, cursor_phase=0.0, scroll_visual_offset=0):

        import math
        import unicodedata

        # If we need a full width scan (e.g., after loading a file), do it first
        if self.needs_full_width_scan and buf:
            self.scan_for_max_width(cr, buf)

        # Base-direction detection
        def line_is_rtl(text):
            for ch in text:
                t = unicodedata.bidirectional(ch)
                if t in ("L", "LRE", "LRO"):
                    return False
                if t in ("R", "AL", "RLE", "RLO"):
                    return True
            return False

        # Visual UTF-8 byte index for Pango (cluster-correct)
        def visual_byte_index(text, col):
            b = 0
            for ch in text[:col]:
                b += len(ch.encode("utf-8"))
            return b

        # --- THEMED BACKGROUND (uses cached color from update_colors_for_theme) ---
        r, g, b, a = self.editor_background_color

        cr.set_source_rgba(r, g, b, a)
        cr.rectangle(0, 0, alloc.width, alloc.height)
        cr.fill()


        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_auto_dir(True)

        total = buf.total()
        ln_width = self.calculate_line_number_width(cr, total)

        # Calculate how many visual lines can fit
        max_vis = (alloc.height // self.line_h) + 1

        # Get selection bounds if any
        sel_start_ln, sel_start_col, sel_end_ln, sel_end_col = -1, -1, -1, -1
        if buf.selection.has_selection():
            sel_start_ln, sel_start_col, sel_end_ln, sel_end_col = buf.selection.get_bounds()

        # Gutter colors (tweak these to taste)
        gutter_bg = (
            0,
            0,
            0,
            1.0   # FORCE OPAQUE
        )      # default gutter background
        gutter_active = (0.1, 0.1, 0.1)  # active line highlight in gutter
        gutter_separator = (0.06, 0.06, 0.06)  # optional thin separator (drawn later if wanted)

        # Text colors (use your existing color tuples)
        r_fg, g_fg, b_fg = self.text_foreground_color
        ln_fg = self.linenumber_foreground_color if hasattr(self, "linenumber_foreground_color") else (r_fg, g_fg, b_fg)
        sel_rgba = self.selection_background_color if hasattr(self, "selection_background_color") else (0.2, 0.4, 1.0)

        # Render loop: Iterate logical lines starting from scroll_line
        y = 0
        current_vis_count = 0
        cursor_screen_pos = None  # (x, y, height)

        # Start iterating from the scroll logical line
        for ln in range(scroll_line, total):
            if current_vis_count >= max_vis:
                break

            line_text = buf.get_line(ln) or ""

            # Determine wrap points
            if self.wrap_enabled:
                wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, alloc.width)
            else:
                wrap_points = [(0, len(line_text))]

            # Determine which visual lines to draw for this logical line
            start_vis_idx = 0
            if ln == scroll_line:
                start_vis_idx = scroll_visual_offset
                if start_vis_idx >= len(wrap_points):
                    start_vis_idx = max(0, len(wrap_points) - 1)

            # Iterate visual lines for this logical line
            for vis_idx in range(start_vis_idx, len(wrap_points)):
                if current_vis_count >= max_vis:
                    break

                col_start, col_end = wrap_points[vis_idx]

                # Extract text for this visual line
                if col_end > col_start:
                    text_segment = line_text[col_start:col_end]
                else:
                    text_segment = line_text[col_start:] if col_start < len(line_text) else ""

                # ---- GUTTER: themed, opaque, no bleed-through ----
                if getattr(self, "show_line_numbers", True):
                    # Use cached background color for line number area
                    r, g, b, a = self.editor_background_color

                    cr.save()
                    cr.set_source_rgba(r, g, b, a)
                    cr.rectangle(0, y, ln_width, self.line_h)
                    cr.fill()
                    cr.restore()

                # Draw current line highlight (extends from line number to viewport)
                # Use foreground color with 5% alpha
                if ln == buf.cursor_line:
                    r_fg, g_fg, b_fg = self.text_foreground_color
                    
                    cr.save()
                    cr.set_source_rgba(r_fg, g_fg, b_fg, 0.05)
                    cr.rectangle(0, y, alloc.width, self.line_h)
                    cr.fill()
                    cr.restore()

                    # (optional separator)
                    # cr.set_source_rgba(0, 0, 0, 0.20)
                    # cr.rectangle(ln_width - 1, y, 1, self.line_h)
                    # cr.fill()


                # Draw line number or continuation marker
                if getattr(self, "show_line_numbers", True):
                    cr.set_source_rgb(*ln_fg)
                    if vis_idx == 0:
                        ln_text = str(ln + 1)
                        # Make current line number bold
                        if ln == buf.cursor_line:
                            layout.set_markup(f"<b>{ln_text}</b>", -1)
                        else:
                            layout.set_text(ln_text, -1)
                    else:
                        ln_text = "⤷"
                        layout.set_text(ln_text, -1)
                    layout.set_width(-1)
                    w, h = layout.get_pixel_size()
                    cr.move_to(ln_width - w - 4, y)
                    PangoCairo.show_layout(cr, layout)
                    # Clear markup to prevent it from affecting subsequent text
                    layout.set_attributes(None)

                # Prevent text from ever drawing inside gutter (clip to text area)
                cr.save()
                text_area_w = max(0, alloc.width - ln_width - self.right_margin_width)          # guard against negative width
                if text_area_w > 0:
                    cr.rectangle(ln_width, y, text_area_w, self.line_h)
                    cr.clip()
                else:
                    # nothing to draw in text area, keep clipped to empty rect
                    cr.rectangle(0, 0, 0, 0)
                    cr.clip()

                # Draw text segment
                cr.set_source_rgb(*self.text_foreground_color)
                layout.set_text(text_segment if text_segment else " ", -1)

                # RTL detection
                rtl = line_is_rtl(text_segment)

                text_w, text_h = layout.get_pixel_size()
                base_x = self.calculate_text_base_x(rtl, text_w, alloc.width, ln_width, scroll_x)

                cr.move_to(base_x, y)
                PangoCairo.show_layout(cr, layout)

                # Draw selection (if any)
                if sel_start_ln != -1:
                    s_col = -1
                    e_col = -1

                    if ln > sel_start_ln and ln < sel_end_ln:
                        # Fully selected logical line
                        s_col = 0
                        e_col = len(text_segment)
                        if vis_idx == len(wrap_points) - 1:
                            e_col += 1  # include newline glyph width
                    elif ln == sel_start_ln and ln == sel_end_ln:
                        seg_sel_start = max(sel_start_col, col_start)
                        seg_sel_end = min(sel_end_col, col_end)
                        
                        # Special case: selection includes newline on same line
                        # E.g., selecting from col 11 to 12 on a line with length 11
                        # IMPORTANT: Only apply this if we're on the LAST wrapped segment
                        # Otherwise, selection extending beyond col_end just means it continues to next segment
                        if sel_end_col > col_end and seg_sel_start == col_end and vis_idx == len(wrap_points) - 1:
                            # Selection is just the newline area (start at end, extends beyond)
                            s_col = seg_sel_start - col_start
                            e_col = sel_end_col - col_start  # Use original sel_end_col, not capped
                        elif seg_sel_start < seg_sel_end:
                            s_col = seg_sel_start - col_start
                            e_col = seg_sel_end - col_start
                    elif ln == sel_start_ln:
                        # Allow selection for empty lines on their last visual line, AND for starting at EOL
                        if sel_start_col < col_end or (sel_start_col == col_start and vis_idx == len(wrap_points) - 1) or (sel_start_col == col_end and vis_idx == len(wrap_points) - 1):
                            s_col = max(0, sel_start_col - col_start)
                            e_col = len(text_segment)
                            if vis_idx == len(wrap_points) - 1:
                                e_col += 1
                    elif ln == sel_end_ln:
                        # Check if selection also starts on this line (wrapped line case)
                        # If so, we need to check segment overlap properly
                        if ln == sel_start_ln:
                            # Selection starts and ends on same logical line (handled above)
                            # This should not happen as it's caught by the first elif
                            pass
                        else:
                            # Selection started on a previous line, ends on this line
                            # Only draw if this segment overlaps with the selection end
                            if sel_end_col > col_start:
                                s_col = 0
                                e_col = min(len(text_segment), sel_end_col - col_start)

                    if s_col != -1:
                        # Special handling for empty lines with newline selected
                        # This matches vted.py's behavior for empty line selection
                        if not text_segment and e_col > len(text_segment):
                            # Empty line with newline selected - draw full-width from line number area to viewport
                            cr.set_source_rgba(*sel_rgba, 0.3)
                            cr.rectangle(ln_width, y, alloc.width - ln_width, self.line_h)
                            cr.fill()
                        else:
                            # Normal selection with text or within text
                            idx1 = visual_byte_index(text_segment, s_col)
                            idx2 = visual_byte_index(text_segment, min(e_col, len(text_segment)))

                            r1_strong, r1_weak = layout.get_cursor_pos(idx1)
                            r2_strong, r2_weak = layout.get_cursor_pos(idx2)

                            x1 = r1_strong.x / Pango.SCALE
                            x2 = r2_strong.x / Pango.SCALE

                            # Check if selection extends beyond text (includes newline)
                            if e_col > len(text_segment):
                                # Draw text selection first (if any text exists)
                                if text_segment:
                                    sel_x = base_x + min(x1, x2)
                                    sel_w = abs(x2 - x1)
                                    if sel_w > 0:
                                        cr.set_source_rgba(*sel_rgba, 0.3)
                                        cr.rectangle(sel_x, y, sel_w, self.line_h)
                                        cr.fill()
                                
                                # Draw newline area extending to viewport edge
                                # This matches vted.py's newline indicator behavior
                                newline_start_x = base_x + x2 if text_segment else ln_width
                                newline_w = alloc.width - newline_start_x
                                if newline_w > 0:
                                    cr.set_source_rgba(*sel_rgba, 0.3)
                                    cr.rectangle(newline_start_x, y, newline_w, self.line_h)
                                    cr.fill()
                            else:
                                # Normal selection within text only
                                sel_x = base_x + min(x1, x2)
                                sel_w = abs(x2 - x1)
                                if sel_w > 0:
                                    cr.set_source_rgba(*sel_rgba, 0.3)
                                    cr.rectangle(sel_x, y, sel_w, self.line_h)
                                    cr.fill()

                # Draw cursor if it's on this visual line
                if ln == buf.cursor_line:
                    c_col = buf.cursor_col
                    is_cursor_here = False
                    cursor_rel_col = 0

                    if col_start <= c_col < col_end:
                        is_cursor_here = True
                        cursor_rel_col = c_col - col_start
                    elif c_col == col_end:
                        if vis_idx == len(wrap_points) - 1:
                            is_cursor_here = True
                            cursor_rel_col = c_col - col_start
                        else:
                            # cursor at wrap boundary -> treat as next visual line
                            pass

                    if is_cursor_here:
                        idx = visual_byte_index(text_segment, cursor_rel_col)
                        strong_pos, weak_pos = layout.get_cursor_pos(idx)

                        # Fix RTL cursor position: subtract right margin if RTL
                        is_rtl = line_is_rtl(text_segment)
                        cx = base_x + (strong_pos.x / Pango.SCALE)

                        # Capture cursor screen position for IME
                        cursor_screen_pos = (cx, y, self.line_h)

                        if cursor_visible:
                            opacity = 0.5 + 0.5 * math.cos(cursor_phase * math.pi)
                            r, g, b = self.text_foreground_color
                            
                            # Check if in overwrite mode (from view)
                            is_overwrite = hasattr(buf, '_view') and buf._view and hasattr(buf._view, 'overwrite_mode') and buf._view.overwrite_mode
                            
                            if is_overwrite:
                                # Block cursor for overwrite mode
                                if cursor_rel_col < len(text_segment):
                                    # On character - get character width
                                    char_at_cursor = text_segment[cursor_rel_col]
                                    
                                    # Calculate character width
                                    char_layout = PangoCairo.create_layout(cr)
                                    char_layout.set_font_description(self.font)
                                    char_layout.set_text(char_at_cursor, -1)
                                    char_width, _ = char_layout.get_pixel_size()
                                    
                                    # RTL adjustment
                                    draw_x = cx - char_width if is_rtl else cx

                                    # Draw darker block cursor (0.7 instead of 0.5)
                                    cr.set_source_rgba(r, g, b, opacity * 0.7)
                                    cr.rectangle(draw_x, y, char_width, self.line_h)
                                    cr.fill()
                                    
                                    # Draw character in inverted color
                                    cr.set_source_rgba(1 - r, 1 - g, 1 - b, opacity)
                                    cr.move_to(draw_x, y)
                                    PangoCairo.show_layout(cr, char_layout)
                                else:
                                    # At end of line - use narrow block
                                    block_width = 8
                                    # RTL adjustment
                                    draw_x = cx - block_width if is_rtl else cx
                                    
                                    cr.set_source_rgba(r, g, b, opacity * 0.7)
                                    cr.rectangle(draw_x, y, block_width, self.line_h)
                                    cr.fill()
                            else:
                                # Normal line cursor for insert mode
                                cursor_w = 1.3
                                # RTL adjustment
                                draw_x = cx - cursor_w if is_rtl else cx
                                
                                cr.set_source_rgba(r, g, b, opacity)
                                cr.rectangle(draw_x, y, cursor_w, self.line_h)
                                cr.fill()

                y += self.line_h
                current_vis_count += 1
                cr.restore()

        # Recompute selection bounds for later usage (keeps parity with original)
        has_selection = buf.selection.has_selection()
        if has_selection:
            sel_start_line, sel_start_col, sel_end_line, sel_end_col = buf.selection.get_bounds()
        else:
            sel_start_line = sel_start_col = sel_end_line = sel_end_col = -1

        # ============================================================
        # PREEDIT (IME)
        # ============================================================
        if hasattr(buf, "preedit_string") and buf.preedit_string and cursor_screen_pos:
            px, py, ph = cursor_screen_pos

            pe_l = PangoCairo.create_layout(cr)
            pe_l.set_font_description(self.font)
            pe_l.set_auto_dir(True)
            pe_l.set_text(buf.preedit_string, -1)

            cr.set_source_rgba(1, 1, 1, 0.7)
            cr.move_to(px, py)
            PangoCairo.show_layout(cr, pe_l)

            uw, _ = pe_l.get_pixel_size()
            cr.set_line_width(1.0)
            cr.move_to(px, py + ph)
            cr.line_to(px + uw, py + ph)
            cr.stroke()

            if hasattr(buf, "preedit_cursor"):
                pc = buf.preedit_cursor

                pe_l2 = PangoCairo.create_layout(cr)
                pe_l2.set_font_description(self.font)
                pe_l2.set_auto_dir(True)
                pe_l2.set_text(buf.preedit_string, -1)

                byte_index2 = visual_byte_index(buf.preedit_string, pc)
                strong_pos2, weak_pos2 = pe_l2.get_cursor_pos(byte_index2)
                cw = strong_pos2.x // Pango.SCALE

                cr.set_line_width(1.0)
                cr.move_to(px + cw, py)
                cr.line_to(px + cw, py + ph)
                cr.stroke()

        # ============================================================
        # DRAG-AND-DROP PREVIEW OVERLAY (unchanged logic)
        if hasattr(buf, '_view') and buf._view:
            view = buf._view
            if view.drag_and_drop_mode and view.drop_position_line >= 0:
                drop_ln = view.drop_position_line
                drop_col = view.drop_position_col

                drop_in_selection = False
                if buf.selection.has_selection():
                    bounds = buf.selection.get_bounds()
                    if bounds and bounds[0] is not None:
                        sel_start_line, sel_start_col, sel_end_line, sel_end_col = bounds

                        if sel_start_line == sel_end_line:
                            if drop_ln == sel_start_line and sel_start_col <= drop_col <= sel_end_col:
                                drop_in_selection = True
                        else:
                            if drop_ln == sel_start_line and drop_col >= sel_start_col:
                                drop_in_selection = True
                            elif drop_ln == sel_end_line and drop_col <= sel_end_col:
                                drop_in_selection = True
                            elif sel_start_line < drop_ln < sel_end_line:
                                drop_in_selection = True

                # Calculate drop position accounting for wrapped lines
                if self.wrap_enabled:
                    current_y = 0
                    found = False
                    ln2 = scroll_line
                    vis_offset = scroll_visual_offset

                    while ln2 <= drop_ln and current_y < alloc.height:
                        wrap_points2 = self.get_wrap_points_for_line(cr, buf, ln2, ln_width, alloc.width)

                        if ln2 == drop_ln:
                            for vis_idx2, (start_col2, end_col2) in enumerate(wrap_points2):
                                if ln2 == scroll_line and vis_idx2 < vis_offset:
                                    continue
                                if start_col2 <= drop_col <= end_col2:
                                    drop_y = current_y
                                    found = True
                                    break
                                current_y += self.line_h
                                if current_y >= alloc.height:
                                    break
                            break
                        else:
                            if ln2 == scroll_line:
                                num_vis2 = len(wrap_points2) - vis_offset
                            else:
                                num_vis2 = len(wrap_points2)
                            current_y += num_vis2 * self.line_h
                            ln2 += 1

                    if not found:
                        drop_y = -1
                else:
                    # No wrapping - simple calculation
                    visible_lines = alloc.height // self.line_h
                    if scroll_line <= drop_ln < scroll_line + visible_lines:
                        drop_y = (drop_ln - scroll_line) * self.line_h
                    else:
                        drop_y = -1

                if drop_y >= 0:
                    drop_text = buf.get_line(drop_ln) or ""

                    if self.wrap_enabled:
                        wrap_points3 = self.get_wrap_points_for_line(cr, buf, drop_ln, ln_width, alloc.width)
                        segment_start = 0
                        segment_end = len(drop_text)
                        for start_col3, end_col3 in wrap_points3:
                            if start_col3 <= drop_col <= end_col3:
                                segment_start = start_col3
                                segment_end = end_col3
                                break
                        segment_text = drop_text[segment_start:segment_end] if drop_text else " "
                        segment_col = drop_col - segment_start
                    else:
                        segment_text = drop_text if drop_text else " "
                        segment_col = drop_col

                    layout2 = self.create_text_layout(cr, segment_text)
                    is_rtl = line_is_rtl(segment_text)
                    text_w2, _ = layout2.get_pixel_size()
                    base_x2 = self.calculate_text_base_x(is_rtl, text_w2, alloc.width, ln_width, scroll_x)

                    drop_byte_idx = visual_byte_index(segment_text, min(segment_col, len(segment_text)))
                    strong_pos2, _ = layout2.get_cursor_pos(drop_byte_idx)
                    drop_x = base_x2 + (strong_pos2.x // Pango.SCALE)

                    is_copy = view.ctrl_pressed_during_drag
                    if is_copy:
                        cursor_color = (0.0, 1.0, 0.3, 0.9)
                        bg_color = (0.0, 0.8, 0.3, 1.0)
                        border_color = (0.0, 1.0, 0.3, 1.0)
                    else:
                        cursor_color = (1.0, 0.6, 0.0, 0.9)
                        bg_color = (1.0, 0.5, 0.0, 1.0)
                        border_color = (1.0, 0.6, 0.0, 1.0)

                    if not drop_in_selection:
                        cr.set_source_rgba(*cursor_color)
                        cr.set_line_width(2)
                        cr.move_to(drop_x, drop_y)
                        cr.line_to(drop_x, drop_y + self.line_h)
                        cr.stroke()

                    cr.set_source_rgba(*border_color)
                    cr.set_line_width(1)
                    cr.rectangle(0, 0, alloc.width, alloc.height)
                    cr.stroke()

                    dragged_text = view.dragged_text
                    if dragged_text:
                        is_multiline = '\n' in dragged_text
                        overlay_layout = self.create_text_layout(cr, dragged_text)
                        overlay_w, overlay_h = overlay_layout.get_pixel_size()

                        vertical_offset = 20
                        drop_y_offset = drop_y + vertical_offset

                        if not is_multiline:
                            padding = 4
                            cr.set_source_rgba(*bg_color)
                            cr.rectangle(drop_x - padding, drop_y_offset - padding,
                                         overlay_w + 2*padding, self.line_h + 2*padding)
                            cr.fill()

                        rr, gg, bb = self.text_foreground_color
                        cr.set_source_rgba(rr, gg, bb, 0.7)
                        cr.move_to(drop_x, drop_y_offset)
                        PangoCairo.show_layout(cr, overlay_layout)



# ============================================================
#   VIEW
# ============================================================

class VirtualTextView(Gtk.DrawingArea):

    def __init__(self, buf):
        super().__init__()
        self.buf = buf
        # Add reference from buffer to view for drag-and-drop
        buf._view = self
        self.renderer = Renderer()
        self.ctrl = InputController(self, buf)
        self.scroll_line = 0
        self.scroll_visual_offset = 0
        self.scroll_x = 0
        self.renderer.wrap_enabled = True
        self.needs_scrollbar_init = False

        # Overwrite mode (toggled with Insert key)
        self.overwrite_mode = False

        # Throttling for scrollbar updates
        self.scroll_update_pending = False
        self.pending_scroll_value = None

        # OPTIMIZATION: Skip calculations during scrollbar drag
        self.scrollbar_dragging = False
        self.last_drag_value = None

        # OPTIMIZATION: Calculation progress tracking
        self.calculating = False
        self.calculation_message = ""

        # NEW: debounce heavy resize handling
        self.resize_update_pending = False

        self.set_focusable(True)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_draw_func(self.draw_view)

        self.install_mouse()
        self.install_keys()
        self.install_im()


    def create_text_layout(self, cr, text="", auto_dir=True):
        """Create a Pango layout using renderer's font.
        
        Args:
            cr: Cairo context
            text: Optional text to set
            auto_dir: Whether to enable auto-direction (default True)
            
        Returns:
            Configured Pango layout
        """
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
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

        self.cursor_phase = 0.0           # animation phase 0 → 2
        self.cursor_fade_speed = 0.03     # 0.02 ~ 50fps smooth fade

        self.start_cursor_blink()
        
        # Connect to size changes to update scrollbars
        self.connect('resize', self.on_resize)

    def on_buffer_changed(self, *args):
        # Optimize: Only invalidate affected lines in wrap cache, not the entire cache
        if self.renderer.wrap_enabled:
            # Only invalidate the current line and a small range around it
            # This is much faster than clearing the entire cache
            cursor_line = self.buf.cursor_line
            
            # Only invalidate the current line and a few lines around it (in case of multi-line changes)
            for ln in range(max(0, cursor_line - 5), min(self.buf.total(), cursor_line + 6)):
                if ln in self.renderer.wrap_cache:
                    del self.renderer.wrap_cache[ln]
            
            # OPTIMIZATION: Smart cache invalidation for large files
            total_logical = self.buf.total()
            if total_logical > self.renderer.use_estimation_threshold:
                # Large file: don't invalidate estimate on every edit
                # The estimate is robust to small changes
                self.renderer.edits_since_cache_invalidation += 1
                
                # Only invalidate every 1000 edits or so
                if self.renderer.edits_since_cache_invalidation > 1000:
                    self.renderer.estimated_total_cache = None
                    self.renderer.total_visual_lines_cache = None
                    self.renderer.edits_since_cache_invalidation = 0
            else:
                # Small/medium file: invalidate normally but keep lock flag
                # Don't reset total_visual_lines_cache - let it recalculate lazily
                pass
            
            # Don't reset anchor - it's still valid for most of the file
            # Only reset if we're far from it
            if self.renderer.visual_line_anchor:
                anchor_vis, anchor_log = self.renderer.visual_line_anchor
                if abs(anchor_log - cursor_line) > 1000:
                    self.renderer.visual_line_anchor = (0, 0)
        
        # Don't update scrollbar on every keystroke - it's expensive
        # Only queue a redraw
        self.queue_draw()


    def on_vadj_changed(self, adj):
        """Handle scrollbar value change."""
        val = adj.get_value()
        
        if self.renderer.wrap_enabled:
            # OPTIMIZATION: During scrollbar drag, use improved approximation
            if self.scrollbar_dragging:
                self.last_drag_value = int(val)
                # Improved approximation using wrap cache for better accuracy
                total_logical = self.buf.total()
                if total_logical > 0:
                    # Try to use wrap cache for better approximation
                    if len(self.renderer.wrap_cache) > 10:
                        # Binary search on wrap cache to find approximate position
                        sorted_cache = sorted(self.renderer.wrap_cache.items())
                        estimated_total = self.renderer.estimated_total_cache or total_logical
                        
                        # Find the logical line that corresponds to this visual line
                        # using cached wrap information
                        target_ratio = val / max(1, estimated_total)
                        approx_logical = int(target_ratio * total_logical)
                        
                        # Refine using nearby cache entries
                        for i, (cached_ln, wrap_points) in enumerate(sorted_cache):
                            if cached_ln >= approx_logical:
                                # Use this as a better approximation
                                approx_logical = cached_ln
                                break
                        
                        self.scroll_line = max(0, min(approx_logical, total_logical - 1))
                    else:
                        # Fallback to simple ratio
                        estimated_total = self.renderer.estimated_total_cache or total_logical
                        approx_logical = int((val / max(1, estimated_total)) * total_logical)
                        self.scroll_line = max(0, min(approx_logical, total_logical - 1))
                    
                    self.scroll_visual_offset = 0
                    self.queue_draw()
                return
            
            # Not dragging: normal throttled update
            self.pending_scroll_value = int(val)
            
            if not self.scroll_update_pending:
                self.scroll_update_pending = True
                GLib.idle_add(self._process_scroll_update)
        else:
            # No wrap - scrollbar value is logical line number
            new_line = int(val)
            
            if new_line != self.scroll_line:
                total_lines = self.buf.total()
                visible = max(1, self.get_height() // self.renderer.line_h)
                max_scroll = max(0, total_lines - visible)
                
                self.scroll_line = max(0, min(new_line, max_scroll))
                self.scroll_visual_offset = 0
                self.queue_draw()
    
    def _process_scroll_update(self):
        """Process pending scroll update (called via GLib.idle_add)."""
        if self.pending_scroll_value is None:
            self.scroll_update_pending = False
            # Disable fast approximation when scrolling stops
            if self.renderer.use_fast_approximation:
                self.renderer.use_fast_approximation = False
            # Clear any progress indicator
            self.calculating = False
            return False
        
        # OPTIMIZATION: Enable fast approximation for huge files during scrolling
        total_logical = self.buf.total()
        # Lower threshold - enable approximation for files > 10k lines during scrolling
        if total_logical > 10000 and not self.renderer.use_fast_approximation:
            self.renderer.use_fast_approximation = True
        
        visual_line = self.pending_scroll_value
        
        # OPTIMIZATION: For huge files (>10M lines), skip expensive calculations entirely
        # Just use the approximation and only recalculate when scrolling stops
        if total_logical > 10_000_000:
            # Use simple approximation - no expensive calculations
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_width = self.renderer.calculate_line_number_width(cr, total_logical)
            viewport_width = self.get_width()
            visible = max(1, self.get_height() // self.renderer.line_h)
            
            # Get cached estimate (already computed in get_total_visual_lines)
            estimated_total = self.renderer.estimated_total_cache or total_logical
            max_scroll_visual = max(0, estimated_total - visible)
            
            # Clamp visual line to valid range
            visual_line = max(0, min(visual_line, max_scroll_visual))
            
            # Simple ratio-based conversion (fast)
            target_ratio = visual_line / max(1, estimated_total)
            approx_logical = int(target_ratio * total_logical)
            approx_logical = max(0, min(approx_logical, total_logical - 1))
            
            # Clear the pending value
            self.pending_scroll_value = None
            
            if approx_logical != self.scroll_line:
                self.scroll_line = approx_logical
                self.scroll_visual_offset = 0
                self.queue_draw()
            
            self.scroll_update_pending = False
            return False
        
        # For huge files, show progress and defer the actual calculation
        if total_logical > 50000 and not self.calculating:
            self.calculating = True
            self.calculation_message = f"Scrolling to line {visual_line:,}..."
            self.queue_draw()
            # Schedule the actual calculation for next idle
            return True  # Keep this idle handler, will process on next call
        
        # Clear the pending value since we're processing it now
        self.pending_scroll_value = None
        
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        ln_width = self.renderer.calculate_line_number_width(cr, total_logical)
        viewport_width = self.get_width()
        visible = max(1, self.get_height() // self.renderer.line_h)
        
        # Check if we're near the end and need accurate recalculation
        total_visual = self.renderer.get_total_visual_lines(cr, self.buf, ln_width, viewport_width)
        max_scroll_visual = max(0, total_visual - visible)
        
        # Check cache coverage
        cache_coverage = len(self.renderer.wrap_cache) / max(1, total_logical)
        
        # If scrolling to near the end (>70%) or cache coverage is poor (<20%), recalculate accurately
        # More aggressive when cache is poor or scrolling to very end
        needs_recalc = False
        if visual_line > max_scroll_visual * 0.7:
            needs_recalc = True
        elif cache_coverage < 0.2 and visual_line > max_scroll_visual * 0.5:
            # Poor cache and in second half - recalculate
            needs_recalc = True
        
        if needs_recalc:
            # Show progress indicator for expensive calculation
            if total_logical > 50000:
                self.calculating = True
                self.calculation_message = "Calculating scroll position..."
                self.queue_draw()
            
            # Calculate accurate total from the end
            # DON'T clear cache - this causes jumping
            # Just recalculate and update it
            # Use more lines for accurate calculation if cache is poor
            lines_to_calc = visible * 3
            if cache_coverage < 0.1:
                # Very poor cache - calculate more lines (5x viewport)
                lines_to_calc = visible * 5
            elif cache_coverage < 0.2:
                # Poor cache - calculate 4x viewport
                lines_to_calc = visible * 4
                
            accurate_total = self.renderer.get_accurate_total_visual_lines_at_end(
                cr, self.buf, ln_width, viewport_width, visible, lines_to_calc
            )
            
            # Clear progress indicator
            self.calculating = False
            
            # Always update the cache with the accurate value
            self.renderer.total_visual_lines_cache = accurate_total
            
            # Update total_visual if different
            if accurate_total != total_visual:
                total_visual = accurate_total
                max_scroll_visual = max(0, total_visual - visible)
                
                # Update scrollbar with new accurate total
                self.vadj.handler_block_by_func(self.on_vadj_changed)
                try:
                    self.vadj.set_upper(total_visual)
                    # Also clamp value if needed
                    if self.vadj.get_value() > max_scroll_visual:
                        self.vadj.set_value(max_scroll_visual)
                finally:
                    self.vadj.handler_unblock_by_func(self.on_vadj_changed)
        
        # Clamp visual line to valid range
        visual_line = max(0, min(visual_line, max_scroll_visual))
        
        # Convert visual line to logical line
        logical_line, vis_idx, col_start, col_end = self.renderer.visual_to_logical_line(
            cr, self.buf, visual_line, ln_width, viewport_width
        )
        
        # Clear progress indicator
        self.calculating = False
        
        if logical_line != self.scroll_line or vis_idx != self.scroll_visual_offset:
            self.scroll_line = logical_line
            self.scroll_visual_offset = vis_idx
            self.queue_draw()
        
        self.scroll_update_pending = False
        return False

    def on_hadj_changed(self, adj):
        # When scrollbar moves → update internal scroll offset
        new = int(adj.get_value())
        if new != self.scroll_x:
            self.scroll_x = new
            self.queue_draw()
                
    def on_resize(self, widget, width, height):
        """Debounced resize handler - never do heavy wrap work during resize."""
        if not self.resize_update_pending:
            self.resize_update_pending = True
            GLib.idle_add(self._process_resize_after_idle)
        return False



    def _process_resize_after_idle(self):
        self.resize_update_pending = False

        if self.renderer.wrap_enabled:
            # Clear ONLY the wrap cache. Do NOT rebuild it now.
            self.renderer.wrap_cache.clear()
            self.renderer.visual_line_map = []
            self.renderer.total_visual_lines_cache = None
            self.renderer.total_visual_lines_locked = False

        # Do NOT call update_scrollbar() yet
        # Just force a redraw so snapshot() pulls needed wraps lazily
        self.queue_draw()

        return False



    def file_loaded(self):
        """Called after a new file is loaded to trigger width calculation"""
        self.renderer.needs_full_width_scan = True
        self.queue_draw()
        # Don't call update_scrollbar here - view dimensions aren't ready yet
        # The resize signal will trigger it when view is properly sized
        
    def update_scrollbar(self):
        """Update scrollbar values and visibility."""
        
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return

        viewport_width = width

        # Vertical scrollbar
        line_h = self.renderer.line_h
        visible = max(1, height // line_h)
        total_lines = self.buf.total()
        
        # Check lock status
        # Check lock status
        if self.renderer.wrap_enabled:
            # Variables used for debug printing only, can be removed or kept if needed later
            # For now, just removing the empty if block
            pass

        
        # When word wrap is enabled, use visual line count for scrollbar
        if self.renderer.wrap_enabled:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_width = self.renderer.calculate_line_number_width(cr, total_lines)
            
            # Get total visual lines (uses caching for performance)
            total_visual = self.renderer.get_total_visual_lines(cr, self.buf, ln_width, viewport_width)
            
            self.vadj.set_lower(0)
            self.vadj.set_upper(total_visual)
            self.vadj.set_page_size(visible)
            self.vadj.set_step_increment(1)
            self.vadj.set_page_increment(visible)
            
            max_scroll = max(0, total_visual - visible)
            
            # Calculate current visual line for scrollbar position
            # We use the current scroll_line (logical) and scroll_visual_offset
            current_visual = self.renderer.logical_to_visual_line(
                cr, self.buf, self.scroll_line, 0, ln_width, viewport_width
            )
            current_visual += self.scroll_visual_offset
            
            if current_visual > max_scroll:
                current_visual = max_scroll
                # We might need to adjust scroll_line/offset here but let's just clamp the scrollbar for now
            
            # Prevent recursive updates if the value hasn't changed significantly
            if abs(self.vadj.get_value() - current_visual) > 0.5:
                self.vadj.set_value(current_visual)
            
            # Show scrollbar if content exceeds viewport
            self.vscroll.set_visible(total_visual > visible)
        else:
            # No word wrap - use logical lines
            self.vadj.set_lower(0)
            self.vadj.set_upper(total_lines)
            self.vadj.set_page_size(visible)
            self.vadj.set_step_increment(1)
            self.vadj.set_page_increment(visible)

            max_scroll = max(0, total_lines - visible)
            if self.scroll_line > max_scroll:
                self.scroll_line = max_scroll
                self.vadj.set_value(self.scroll_line)
            
            self.vscroll.set_visible(total_lines > visible)

        # horizontal - disable when wrapping
        if self.renderer.wrap_enabled:
            doc_w = 0
        else:
            # Add margins to the document width
            # max_line_width already includes ln_width (see scan_for_max_width)
            doc_w = self.renderer.max_line_width + self.renderer.right_margin_width

        self.hadj.set_lower(0)
        self.hadj.set_upper(doc_w)
        self.hadj.set_page_size(viewport_width)
        self.hadj.set_step_increment(20)
        self.hadj.set_page_increment(viewport_width // 2)

        max_hscroll = max(0, doc_w - viewport_width)
        if self.scroll_x > max_hscroll:
            self.scroll_x = max_hscroll
            self.hadj.set_value(self.scroll_x)

        self.hscroll.set_visible(doc_w > viewport_width)





    # Correct UTF-8 byte-index for logical col → Pango visual mapping
    def visual_byte_index(self, text, col):
        b = 0
        for ch in text[:col]:
            b += len(ch.encode("utf-8"))
        return b

    def pixel_to_column(self, cr, text, px):
        """Accurate pixel→column mapping using Pango layout hit-testing."""
        if not text:
            return 0
        
        # Handle clicks before text start
        if px <= 0:
            return 0

        # ----- FREEZE FIX -----
        if self.renderer.wrap_enabled and not self.renderer.wrap_cache:
            # approximate using avg_char_width when wrap cache isn't ready
            est = int(px / self.renderer.avg_char_width)
            return min(est, len(text))
        # -----------------------

        layout = self.create_text_layout(cr, text)

        text_w, _ = layout.get_pixel_size()
        if px >= text_w:
            return len(text)

        # Pango xy_to_index returns:
        # - index: byte offset to the grapheme cluster
        # - trailing: number of grapheme clusters to advance (0 = leading edge, 1 = trailing edge)
        success, byte_index, trailing = layout.xy_to_index(int(px * Pango.SCALE), 0)
        if not success:
            return len(text) if px > 0 else 0

        # Convert byte offset to character index
        byte_count = 0
        char_index = 0
        for i, ch in enumerate(text):
            if byte_count >= byte_index:
                char_index = i
                break
            byte_count += len(ch.encode("utf-8"))
        else:
            # byte_index points to or past end
            char_index = len(text)
        
        # If trailing > 0, cursor should be after the clicked character
        if trailing > 0 and char_index < len(text):
            char_index += 1
        
        return min(char_index, len(text))




    def start_cursor_blink(self):
        """Start smooth cursor blinking with lightweight animation."""
        # Cursor appears fully visible at the start
        self.cursor_visible = True
        self.cursor_phase = 0.0

        FPS = 60
        INTERVAL = int(1000 / FPS)

        def tick():
            # Phase runs 0 → 2.0 continuously
            self.cursor_phase += self.cursor_fade_speed
            if self.cursor_phase >= 2.0:
                self.cursor_phase -= 2.0

            # Redraw cursor area (cheap)
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

        # Solid cursor
        self.cursor_visible = True
        self.cursor_phase = 0.0

        self.queue_draw()




    def on_commit(self, im, text):
        """Handle committed text from IM (finished composition)"""
        if text:
            # Insert typed text with overwrite mode if enabled
            self.buf.insert_text(text, overwrite=self.overwrite_mode)

            # Keep cursor on screen
            self.keep_cursor_visible()

            # While typing → cursor MUST be solid
            self.cursor_visible = True
            self.cursor_phase = 0.25    # peak of sine wave = full opacity

            # Stop any blinking while typing
            self.stop_cursor_blink()

            # Blink will resume after user stops typing
            self.restart_blink_after_idle()

            # Redraw + update IME
            self.queue_draw()
            self.update_im_cursor_location()


    def restart_blink_after_idle(self):
        """Restart cursor blinking only once after user stops typing."""
        # Cancel any previously scheduled idle restart
        if hasattr(self, "_idle_blink_timeout") and self._idle_blink_timeout:
            GLib.source_remove(self._idle_blink_timeout)
            self._idle_blink_timeout = None

        def idle_blink():
            self._idle_blink_timeout = None
            self.start_cursor_blink()
            return False  # one-shot

        # Schedule a new one
        self._idle_blink_timeout = GLib.timeout_add(700, idle_blink)





    def on_preedit_start(self, im):
        """Preedit (composition) started"""
        self.queue_draw()

    def on_preedit_end(self, im):
        """Preedit (composition) ended"""
        self.preedit_string = ""
        self.preedit_cursor = 0
        self.queue_draw()

    def on_preedit_changed(self, im):
        """Preedit text changed - show composition"""
        try:
            preedit_str, attrs, cursor_pos = self.im.get_preedit_string()
            self.preedit_string = preedit_str or ""
            self.preedit_cursor = cursor_pos
            self.queue_draw()
        except Exception as e:
            print(f"Preedit error: {e}")

    def on_focus_in(self, controller):
        """Widget gained focus"""
        self.im.focus_in()
        self.im.set_client_widget(self)
        self.update_im_cursor_location()
        
    def on_focus_out(self, controller):
            self.im.focus_out()

    def update_im_cursor_location(self):
        try:
            width  = self.get_width()
            height = self.get_height()
            if width <= 0 or height <= 0:
                return

            cl, cc = self.buf.cursor_line, self.buf.cursor_col
            
            # Optimization: If cursor is not in viewport range, don't calculate
            # This is a rough check based on logical lines
            line_h = self.renderer.line_h
            visible_lines = height // line_h
            
            # If wrapping is disabled, simple check
            if not self.renderer.wrap_enabled:
                if cl < self.scroll_line or cl > self.scroll_line + visible_lines:
                    return
                y = (cl - self.scroll_line) * line_h
            else:
                # Wrapping enabled: calculate Y relative to scroll_line
                # Only iterate if cursor is likely in view
                if cl < self.scroll_line:
                    return
                
                # If cl is way below, skip
                if cl > self.scroll_line + visible_lines * 5:
                    return
                    
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                ln_w = self.renderer.calculate_line_number_width(cr, self.buf.total())
                
                current_vis = 0
                found_cursor = False
                cursor_vis_rel = 0
                
                ln = self.scroll_line
                while ln <= cl:
                    wrap_points = self.renderer.get_wrap_points_for_line(cr, self.buf, ln, ln_w, width)
                    num_vis = len(wrap_points)
                    
                    start_vis = 0
                    if ln == self.scroll_line:
                        start_vis = self.scroll_visual_offset
                        if start_vis >= num_vis:
                            start_vis = max(0, num_vis - 1)
                    
                    if ln == cl:
                        # Found cursor line
                        vis_idx = 0
                        for i, (start, end) in enumerate(wrap_points):
                            if start <= cc <= end:
                                vis_idx = i
                                break
                        
                        cursor_vis_rel = current_vis + (vis_idx - start_vis)
                        found_cursor = True
                        break
                    
                    current_vis += (num_vis - start_vis)
                    ln += 1
                    
                    if current_vis > visible_lines + 2:
                        break
                
                if not found_cursor:
                    return
                    
                y = cursor_vis_rel * line_h

            line_text = self.buf.get_line(cl)

            # Pango layout
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)

            layout = self.create_text_layout(cr, line_text if line_text else " ")

            is_rtl = detect_rtl_line(line_text)
            text_w, _ = layout.get_pixel_size()
            ln_w = self.renderer.calculate_line_number_width(cr, self.buf.total())

            # base_x matches draw()
            base_x = self.renderer.calculate_text_base_x(is_rtl, text_w, width, ln_w, self.scroll_x)

            # ---- FIXED: correct UTF-8 byte index ----
            byte_index = self.visual_byte_index(line_text, cc)

            strong_pos, weak_pos = layout.get_cursor_pos(byte_index)
            cursor_x = strong_pos.x // Pango.SCALE

            x = base_x + cursor_x

            # clamp
            if y < 0 or y > height - self.renderer.text_h:
                return

            x = max(ln_w, min(x, width - 50))
            y = max(0,     min(y, height - self.renderer.text_h))

            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width  = 2
            rect.height = self.renderer.text_h

            self.im.set_cursor_location(rect)

        except Exception as e:
            print(f"IM cursor location error: {e}")

                
    def on_key(self, c, keyval, keycode, state):
        # Let IM filter the event FIRST
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True

        name = Gdk.keyval_name(keyval)
        shift_pressed = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        alt_pressed = (state & Gdk.ModifierType.ALT_MASK) != 0

        # Alt+Z - Toggle word wrap
        if alt_pressed and (name == "z" or name == "Z"):
            saved_cursor_line = self.buf.cursor_line
            saved_cursor_col = self.buf.cursor_col

            # Save previous estimate
            width = self.get_width()
            height = self.get_height()
            previous_estimated_total = None
            if self.renderer.wrap_enabled and width > 0 and height > 0:
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                total_lines = self.buf.total()
                ln_width = self.renderer.calculate_line_number_width(cr, total_lines)
                viewport_width = width
                previous_estimated_total = self.renderer.get_total_visual_lines(
                    cr, self.buf, ln_width, viewport_width
                )

            self.renderer.wrap_enabled = not self.renderer.wrap_enabled

            # Clear wrap caches
            self.renderer.wrap_cache = {}
            self.renderer.visual_line_map = []
            self.renderer.total_visual_lines_locked = False
            self.renderer.visual_line_anchor = (0, 0)

            visible_lines = max(1, height // self.renderer.line_h) if height > 0 else 50
            total_lines = self.buf.total()

            if self.renderer.wrap_enabled:
                # Enabling wrap mode
                self.renderer.max_line_width = 0
                self.scroll_x = 0
                self.scroll_visual_offset = 0
                self.hadj.set_value(0)

                if width > 0 and height > 0 and total_lines > 0:
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                    cr = cairo.Context(surface)
                    ln_width = self.renderer.calculate_line_number_width(cr, total_lines)

                    # Near EOF
                    if saved_cursor_line > total_lines * 0.8:
                        buffer = visible_lines * 3
                        start_line = max(0, saved_cursor_line - buffer)
                        end_line = total_lines
                        for ln in range(start_line, end_line):
                            self.renderer.get_wrap_points_for_line(
                                cr, self.buf, ln, ln_width, width
                            )

                        saved_cache = self.renderer.wrap_cache.copy()
                        self.renderer.wrap_cache = {}
                        total_visual = self.renderer.get_total_visual_lines(
                            cr, self.buf, ln_width, width
                        )
                        self.renderer.wrap_cache = saved_cache

                        if previous_estimated_total and previous_estimated_total > total_visual:
                            total_visual = previous_estimated_total

                        self.renderer.total_visual_lines_cache = total_visual

                    else:
                        buffer = visible_lines * 3
                        start_line = max(0, saved_cursor_line - buffer)
                        end_line = min(total_lines, saved_cursor_line + buffer)
                        for ln in range(start_line, end_line):
                            self.renderer.get_wrap_points_for_line(
                                cr, self.buf, ln, ln_width, width
                            )

                        total_visual = self.renderer.get_total_visual_lines(
                            cr, self.buf, ln_width, width
                        )

                # -------------------------------------------------------
                # PATCHED SECTION — accurate cursor anchoring after wrap
                # -------------------------------------------------------
                if width > 0 and height > 0 and total_lines > 0:
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                    cr = cairo.Context(surface)
                    ln_width = self.renderer.calculate_line_number_width(cr, total_lines)

                    cursor_visual = self.renderer.logical_to_visual_line(
                        cr, self.buf, saved_cursor_line, saved_cursor_col,
                        ln_width, width
                    )

                    # Convert back to logical + visual offset
                    new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                        cr, self.buf, cursor_visual, ln_width, width
                    )

                    self.scroll_line = new_log
                    self.scroll_visual_offset = new_vis_off

                    # Sync vadj to cursor’s true visual line
                    self.vadj.handler_block_by_func(self.on_vadj_changed)
                    try:
                        upper = max(cursor_visual + 1, int(self.vadj.get_upper()))
                        self.vadj.set_upper(upper)
                        self.vadj.set_value(cursor_visual)
                    finally:
                        self.vadj.handler_unblock_by_func(self.on_vadj_changed)
                else:
                    # fallback (tiny or zero viewport) — keep original behavior
                    estimated_scroll = max(0, saved_cursor_line - visible_lines // 2)
                    self.scroll_line = estimated_scroll
                    self.scroll_visual_offset = 0
                # -------------------------------------------------------

            else:
                # Disabling wrap
                self.scroll_visual_offset = 0
                estimated_scroll = max(0, saved_cursor_line - visible_lines // 2)
                self.scroll_line = estimated_scroll

                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                self.renderer.scan_for_max_width(cr, self.buf)

            # Update scrollbar
            self.update_scrollbar()

            # Correction pass
            cursor_corrected = False
            if self.renderer.wrap_enabled and width > 0 and height > 0:
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                ln_width = self.renderer.calculate_line_number_width(cr, total_lines)

                cursor_visual = self.renderer.logical_to_visual_line(
                    cr, self.buf, saved_cursor_line, saved_cursor_col, ln_width, width
                )

                current_estimate = self.vadj.get_upper()

                if total_lines > 5000 and cursor_visual > current_estimate * 0.95:
                    if saved_cursor_line > 0:
                        actual_ratio = cursor_visual / saved_cursor_line
                        corrected_total = int(total_lines * actual_ratio)
                        corrected_total = int(corrected_total * 1.02) + 100

                        self.renderer.total_visual_lines_cache = corrected_total

                        self.vadj.handler_block_by_func(self.on_vadj_changed)
                        try:
                            self.vadj.set_upper(corrected_total)
                            max_scroll = max(0, corrected_total - visible_lines)
                            target_visual = max(0, cursor_visual - visible_lines // 2)
                            target_visual = min(target_visual, max_scroll)
                            self.vadj.set_value(target_visual)

                            new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                                cr, self.buf, target_visual, ln_width, width
                            )
                            self.scroll_line = new_log
                            self.scroll_visual_offset = new_vis_off

                            cursor_corrected = True
                        finally:
                            self.vadj.handler_unblock_by_func(self.on_vadj_changed)

            if not cursor_corrected:
                self.keep_cursor_visible()

            self.queue_draw()
            return True

        # ... rest of your key handling unchanged ...



        # Alt+Arrow keys for text movement
        if alt_pressed and name == "Left":
            self.buf.move_word_left_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif alt_pressed and name == "Right":
            self.buf.move_word_right_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif alt_pressed and name == "Up":
            self.buf.move_line_up_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif alt_pressed and name == "Down":
            self.buf.move_line_down_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if keyval == Gdk.KEY_Tab:
            # Check for Shift+Tab (Unindent)
            if (state & Gdk.ModifierType.SHIFT_MASK):
                self.buf.unindent_selection()
                self.queue_draw()
                return True
            
            # Check for Multi-line Indent
            if self.buf.selection.has_selection():
                start_line, _, end_line, _ = self.buf.selection.get_bounds()
                if start_line != end_line:
                    self.buf.indent_selection()
                    self.queue_draw()
                    return True
            
            # Normal Tab (Insert spaces)
            self.buf.insert_text("    ")
            self.queue_draw()
            return True

        if keyval == Gdk.KEY_ISO_Left_Tab:
            self.buf.unindent_selection()
            self.queue_draw()
            return True

        # Ctrl+A - Select All
        if ctrl_pressed and name == "a":
            self.buf.select_all()
            self.queue_draw()
            return True
        
        # Ctrl+C - Copy
        if ctrl_pressed and name == "c":
            self.copy_to_clipboard()
            return True
        
        # Ctrl+X - Cut
        if ctrl_pressed and name == "x":
            self.cut_to_clipboard()
            return True
        
        # Ctrl+V - Paste
        if ctrl_pressed and name == "v":
            self.paste_from_clipboard()
            return True
        
        # Insert key - Toggle overwrite mode
        if name == "Insert" and not ctrl_pressed and not shift_pressed:
            self.overwrite_mode = not self.overwrite_mode
            # Visual feedback could be added here (cursor shape change, status bar indicator, etc.)
            print(f"Overwrite mode: {'ON' if self.overwrite_mode else 'OFF'}")
            self.queue_draw()
            return True
        
        # Tab key - insert tab character
        if name == "Tab":
            self.buf.insert_text("\t")
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Editing keys
        if name == "BackSpace":
            if ctrl_pressed and shift_pressed:
                # Ctrl+Shift+Backspace: Delete to start of line
                self.buf.delete_to_line_start()
            elif ctrl_pressed:
                # Ctrl+Backspace: Delete word backward
                self.buf.delete_word_backward()
            else:
                # Normal backspace
                self.buf.backspace()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Delete":
            if ctrl_pressed and shift_pressed:
                # Ctrl+Shift+Delete: Delete to end of line
                self.buf.delete_to_line_end()
            elif ctrl_pressed:
                # Ctrl+Delete: Delete word forward
                self.buf.delete_word_forward()
            else:
                # Normal delete
                self.buf.delete_key()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Return":
            self.buf.insert_newline()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Navigation with selection support
        if name == "Up":
            self.ctrl.move_up(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Down":
            self.ctrl.move_down(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Left":
            if ctrl_pressed:
                # Proper word navigation
                self.ctrl.move_word_left(extend_selection=shift_pressed)
            else:
                self.ctrl.move_left(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Right":
            if ctrl_pressed:
                # Proper word navigation
                self.ctrl.move_word_right(extend_selection=shift_pressed)
            else:
                self.ctrl.move_right(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Home":
            if ctrl_pressed:
                self.ctrl.move_document_start(extend_selection=shift_pressed)
            else:
                self.ctrl.move_home(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "End":
            if ctrl_pressed:
                self.ctrl.move_document_end(extend_selection=shift_pressed)
            else:
                self.ctrl.move_end(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Page_Up":
            # Move up by visible lines
            visible_lines = self.get_height() // self.renderer.line_h
            for _ in range(visible_lines):
                self.ctrl.move_up(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Page_Down":
            # Move down by visible lines
            visible_lines = self.get_height() // self.renderer.line_h
            for _ in range(visible_lines):
                self.ctrl.move_down(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        return False

    def copy_to_clipboard(self):
        """Copy selected text to clipboard"""
        text = self.buf.get_selected_text()
        if text:
            clipboard = self.get_clipboard()
            clipboard.set_content(Gdk.ContentProvider.new_for_value(text))

    def cut_to_clipboard(self):
        """Cut selected text to clipboard"""
        text = self.buf.get_selected_text()
        if text:
            clipboard = self.get_clipboard()
            clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
            self.buf.delete_selection()
            self.queue_draw()

    def paste_from_clipboard(self):
        """Paste text from clipboard with better error handling"""
        clipboard = self.get_clipboard()
        
        def paste_ready(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
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
                error_msg = str(e)
                # Silently ignore "No compatible transfer format" errors
                # This happens when clipboard contains non-text data (images, etc.)
                if "No compatible transfer format" not in error_msg:
                    print(f"Paste error: {e}")
                # Optionally try to get text in a different way
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
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self.on_click_pressed)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)
        
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
        """Placeholder for undo - to be implemented"""
        print("Undo - to be implemented")

    def on_redo_action(self):
        """Placeholder for redo - to be implemented"""
        print("Redo - to be implemented")

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
        self.grab_focus()

        # Always use accurate xy_to_line_col - Pango hit-testing is fast enough
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
            self.queue_draw()
            return

        # TRIPLE CLICK unchanged
        if self.click_count == 3:
            self.buf.selection.set_start(ln, 0)
            self.buf.selection.set_end(ln, line_len)
            self.buf.cursor_line = ln
            self.buf.cursor_col = line_len
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

        self._clicked_in_selection = False
        self.buf.selection.clear()
        self.ctrl.start_drag(ln, col)

        self._pending_click = True
        self._click_ln = ln
        self._click_col = col

        self.queue_draw()


    def on_click(self, g, n, x, y):
        self.grab_focus()

        # Get modifiers
        modifiers = g.get_current_event_state()
        shift_pressed = (modifiers & Gdk.ModifierType.SHIFT_MASK) != 0

        # Temporary Pango context
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())

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




    def xy_to_line_col(self, x, y):
        """Convert pixel coordinates to logical line and column."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())
        viewport_width = self.get_width()

        # ------------------------------------------------------------
        # NORMAL PATH (wrap disabled)
        # ------------------------------------------------------------
        if not self.renderer.wrap_enabled:
            vis_line = self.scroll_line + int(y // self.renderer.line_h)
            ln = max(0, min(vis_line, self.buf.total() - 1))
            
            text = self.buf.get_line(ln)
            is_rtl = detect_rtl_line(text)

            text_w = self.renderer.get_text_width(cr, text)
            base_x = self.renderer.calculate_text_base_x(
                is_rtl, text_w, viewport_width, ln_width, self.scroll_x
            )

            col_pixels = x - base_x
            col = self.pixel_to_column(cr, text, col_pixels)
            col = max(0, min(col, len(text)))
            return ln, col

        # ------------------------------------------------------------
        # WRAP-AWARE PATH: Accurate iteration limited to viewport
        # ------------------------------------------------------------
        current_y = 0
        ln = self.scroll_line
        total_lines = self.buf.total()

        viewport_height = self.get_height()
        max_y_to_check = viewport_height + self.renderer.line_h * 2
        
        # Safety counter to prevent infinite loops
        max_iterations = 200
        iteration_count = 0

        while ln < total_lines and iteration_count < max_iterations:
            iteration_count += 1
            
            wrap_points = self.renderer.get_wrap_points_for_line(
                cr, self.buf, ln, ln_width, viewport_width
            )
            num_visual = len(wrap_points)

            start_vis_idx = 0
            if ln == self.scroll_line:
                start_vis_idx = self.scroll_visual_offset
                if start_vis_idx >= num_visual:
                    start_vis_idx = max(0, num_visual - 1)

            for vis_idx in range(start_vis_idx, num_visual):
                if current_y <= y < current_y + self.renderer.line_h:
                    # Found the visual line that was clicked
                    col_start, col_end = wrap_points[vis_idx]

                    full_text = self.buf.get_line(ln)
                    if col_end > col_start:
                        text_segment = full_text[col_start:col_end]
                    else:
                        text_segment = full_text[col_start:] if col_start < len(full_text) else ""

                    is_rtl = detect_rtl_line(text_segment)
                    text_w = self.renderer.get_text_width(cr, text_segment)

                    base_x = self.renderer.calculate_text_base_x(
                        is_rtl, text_w, viewport_width, ln_width, self.scroll_x
                    )

                    col_pixels = x - base_x
                    col_in_segment = self.pixel_to_column(cr, text_segment, col_pixels)
                    col_in_segment = max(0, min(col_in_segment, len(text_segment)))

                    col = col_start + col_in_segment
                    return ln, col

                current_y += self.renderer.line_h
                if current_y > max_y_to_check:
                    break

            if current_y > max_y_to_check:
                break

            ln += 1

        # Fallback: click was beyond visible area
        last_ln = max(0, total_lines - 1)
        last_line_text = self.buf.get_line(last_ln)
        return last_ln, len(last_line_text)



    def start_autoscroll(self):
        """Start the auto-scroll timer if not already running"""
        if self.autoscroll_timer_id is None:
            # Call autoscroll_tick every 50ms (20 times per second)
            self.autoscroll_timer_id = GLib.timeout_add(50, self.autoscroll_tick)
    
    def stop_autoscroll(self):
        """Stop the auto-scroll timer"""
        if self.autoscroll_timer_id is not None:
            GLib.source_remove(self.autoscroll_timer_id)
            self.autoscroll_timer_id = None
    
    def autoscroll_tick(self):
        """Called periodically during drag to perform auto-scrolling"""
        if not self.ctrl.dragging and not self.drag_and_drop_mode:
            # No longer dragging, stop the timer
            self.stop_autoscroll()
            return False
        
        viewport_height = self.get_height()
        viewport_width = self.get_width()
        
        # Define edge zones (pixels from edge where auto-scroll activates)
        edge_size = 30
        
        # Calculate scroll amounts based on how close to edge
        scroll_amount = 0
        hscroll_amount = 0
        
        # Vertical scrolling
        if self.last_drag_y < edge_size:
            # Near top edge - scroll up
            # Speed increases closer to edge
            scroll_amount = -max(1, int((edge_size - self.last_drag_y) / 10) + 1)
        elif self.last_drag_y > viewport_height - edge_size:
            # Near bottom edge - scroll down
            scroll_amount = max(1, int((self.last_drag_y - (viewport_height - edge_size)) / 10) + 1)
        
        # Horizontal scrolling (only when wrap is disabled)
        if not self.renderer.wrap_enabled:
            ln_width = 50  # Approximate line number width
            if self.last_drag_x < ln_width + edge_size:
                # Near left edge - scroll left
                hscroll_amount = -max(5, int((ln_width + edge_size - self.last_drag_x) / 5) + 5)
            elif self.last_drag_x > viewport_width - edge_size:
                # Near right edge - scroll right
                hscroll_amount = max(5, int((self.last_drag_x - (viewport_width - edge_size)) / 5) + 5)
        
        # Perform scrolling
        did_scroll = False
        
        if scroll_amount != 0:
            total_lines = self.buf.total()
            if total_lines == 0:
                return True
            
            visible = max(1, viewport_height // self.renderer.line_h)
            
            if self.renderer.wrap_enabled:
                # Word wrap mode: scroll by visual lines
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                ln_width = self.renderer.calculate_line_number_width(cr, total_lines)
                
                # Calculate current visual line
                current_visual = self.renderer.logical_to_visual_line(
                    cr, self.buf, self.scroll_line, 0, ln_width, viewport_width
                )
                current_visual += self.scroll_visual_offset
                
                # Calculate total visual lines for bounds checking
                total_visual = self.renderer.get_total_visual_lines(cr, self.buf, ln_width, viewport_width)
                max_scroll_visual = max(0, total_visual - visible)
                
                # Apply scroll
                new_visual = current_visual + scroll_amount
                new_visual = max(0, min(new_visual, max_scroll_visual))
                
                if new_visual != current_visual:
                    # Convert back to logical line + visual offset
                    new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                        cr, self.buf, new_visual, ln_width, viewport_width
                    )
                    
                    self.scroll_line = new_log
                    self.scroll_visual_offset = new_vis_off
                    self.vadj.set_value(new_visual)
                    did_scroll = True
            else:
                # Non-wrap mode: scroll by logical lines
                new_scroll = self.scroll_line + scroll_amount
                max_scroll = max(0, total_lines - visible)
                new_scroll = max(0, min(new_scroll, max_scroll))
                
                if new_scroll != self.scroll_line:
                    self.scroll_line = new_scroll
                    self.scroll_visual_offset = 0
                    self.vadj.set_value(self.scroll_line)
                    did_scroll = True
        
        if hscroll_amount != 0 and not self.renderer.wrap_enabled:
            new_scroll_x = self.scroll_x + hscroll_amount
            max_hscroll = max(0, self.renderer.max_line_width - viewport_width)
            new_scroll_x = max(0, min(new_scroll_x, max_hscroll))
            
            if new_scroll_x != self.scroll_x:
                self.scroll_x = new_scroll_x
                self.hadj.set_value(self.scroll_x)
                did_scroll = True
        
        # Update selection after scrolling
        if did_scroll:
            # Get the line/col at current drag position
            ln, col = self.xy_to_line_col(self.last_drag_x, self.last_drag_y)
            
            # Update drag selection to follow the cursor
            if self.drag_and_drop_mode:
                # In drag-and-drop mode, just update drop position
                self.drop_position_line = ln
                self.drop_position_col = col
            elif self.word_selection_mode:
                # Word selection mode - extend by words
                line_text = self.buf.get_line(ln)
                if line_text and 0 <= col <= len(line_text):
                    start_col, end_col = self.find_word_boundaries(line_text, min(col, len(line_text) - 1))
                    
                    # Use anchor word for direction
                    is_forward = False
                    if ln > self.anchor_word_start_line:
                        is_forward = True
                    elif ln == self.anchor_word_start_line and col >= self.anchor_word_start_col:
                        is_forward = True
                    
                    if is_forward:
                        self.buf.selection.set_start(self.anchor_word_start_line, self.anchor_word_start_col)
                        self.ctrl.update_drag(ln, end_col)
                    else:
                        self.buf.selection.set_start(self.anchor_word_end_line, self.anchor_word_end_col)
                        self.ctrl.update_drag(ln, start_col)
                else:
                    self.ctrl.update_drag(ln, col)
            else:
                # Normal character selection
                self.ctrl.update_drag(ln, col)
            
            self.queue_draw()
        
        # Keep timer running
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
            self._clicked_in_selection = False
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


    def on_click_released(self, g, n, x, y):
        if self._pending_click:
            self.ctrl.click(self._click_ln, self._click_col)
        self._pending_click = False
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


    def keep_cursor_visible(self):
        """Keep cursor visible by scrolling if necessary.
        
        OPTIMIZED: Quick check for obviously visible cursor to avoid
        expensive visual line calculations during normal typing.
        """
        cl = self.buf.cursor_line
        cc = self.buf.cursor_col

        alloc_w = self.get_width()
        alloc_h = self.get_height()
        if alloc_w <= 0 or alloc_h <= 0:
            return

        line_h = self.renderer.line_h
        visible_lines = alloc_h // line_h
        total_lines = self.buf.total()

        # Handle word wrap mode
        if self.renderer.wrap_enabled:
            # OPTIMIZATION: Quick check first - is cursor "probably" visible?
            # This avoids expensive calculations for 90% of normal typing
            logical_diff = cl - self.scroll_line
            
            # If cursor is on visible logical lines and not at far edge
            # assume it's visible (worst case: slight delay in auto-scroll)
            if 0 < logical_diff < visible_lines - 1:
                # Cursor is on a middle logical line that's definitely visible
                # Skip expensive visual line calculations
                return
            
            # If cursor is on first or last visible logical line, or outside,
            # we need to do the exact check
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_width = self.renderer.calculate_line_number_width(cr, total_lines)
            viewport_width = alloc_w

            # Calculate cursor's visual line
            cursor_visual_line = self.renderer.logical_to_visual_line(
                cr, self.buf, cl, cc, ln_width, viewport_width
            )
            
            # Calculate current scroll visual line
            scroll_visual_line = self.renderer.logical_to_visual_line(
                cr, self.buf, self.scroll_line, 0, ln_width, viewport_width
            )
            scroll_visual_line += self.scroll_visual_offset
            
            # Check if cursor is above visible area
            if cursor_visual_line < scroll_visual_line:
                # Scroll up to cursor
                new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                    cr, self.buf, cursor_visual_line, ln_width, viewport_width
                )
                
                self.scroll_line = new_log
                self.scroll_visual_offset = new_vis_off
                
                # Update vadj directly without calling update_scrollbar
                # This prevents recalculation and jumping
                self.vadj.set_value(cursor_visual_line)
                
            # Check if cursor is below visible area
            elif cursor_visual_line >= scroll_visual_line + visible_lines:
                # Scroll down so cursor is visible
                new_top_visual = cursor_visual_line - visible_lines + 1
                
                # Only calculate total_visual if we need to clamp to max_scroll
                # This is a MAJOR optimization for huge files
                total_visual = self.renderer.get_total_visual_lines(cr, self.buf, ln_width, viewport_width)
                max_scroll_visual = max(0, total_visual - visible_lines)
                
                # Ensure we don't scroll past the end
                new_top_visual = min(new_top_visual, max_scroll_visual)
                
                new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                    cr, self.buf, new_top_visual, ln_width, viewport_width
                )
                
                self.scroll_line = new_log
                self.scroll_visual_offset = new_vis_off
                
                # Update vadj directly without calling update_scrollbar
                # This prevents recalculation and jumping
                self.vadj.set_value(new_top_visual)
                
            # Reset horizontal scroll to 0 when wrapping
            if self.scroll_x != 0:
                self.scroll_x = 0
                self.hadj.set_value(0)
            return

        # Simple logical line-based scrolling for non-wrap mode
        
        # If cursor is before scroll position, scroll up
        if cl < self.scroll_line:
            self.scroll_line = cl
            self.scroll_visual_offset = 0
            self.vadj.set_value(self.scroll_line)
        
        # If cursor is after visible area, scroll down
        elif cl >= self.scroll_line + visible_lines:
            self.scroll_line = max(0, cl - visible_lines + 1)
            self.scroll_visual_offset = 0
            self.vadj.set_value(self.scroll_line)

        # Horizontal scrolling (only for non-wrap mode)
        
        line_text = self.buf.get_line(cl)

        # Build Pango layout to get exact pixel position
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        layout = self.create_text_layout(cr, line_text if line_text else " ")

        # RTL detection (mirrors renderer.draw)
        rtl = detect_rtl_line(line_text)
        byte_index = self.visual_byte_index(line_text, cc)
        strong_pos, weak_pos = layout.get_cursor_pos(byte_index)
        cursor_px = strong_pos.x // Pango.SCALE
        ln_w = self.renderer.calculate_line_number_width(cr, total_lines)

        # Calculate base X exactly as renderer.draw does
        text_w, _ = layout.get_pixel_size()
        base_x = self.renderer.calculate_text_base_x(rtl, text_w, alloc_w, ln_w, self.scroll_x)

        cursor_screen_x = base_x + cursor_px

        # Horizontal auto-scroll
        left_margin = ln_w + 2
        right_margin = alloc_w - 2

        if cursor_screen_x < left_margin:
            self.scroll_x -= (left_margin - cursor_screen_x)
            if self.scroll_x < 0:
                self.scroll_x = 0
            self.hadj.set_value(self.scroll_x)

        elif cursor_screen_x > right_margin:
            self.scroll_x += (cursor_screen_x - right_margin)
            max_hscroll = max(0, self.renderer.max_line_width - alloc_w)
            if self.scroll_x > max_hscroll:
                self.scroll_x = max_hscroll
            self.hadj.set_value(self.scroll_x)




    def install_scroll(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        """Handle mouse wheel scroll."""
        if dy:
            steps = int(dy * 3)  # Scroll speed
            
            total_lines = self.buf.total()
            if total_lines == 0:
                return True
            
            visible = max(1, self.get_height() // self.renderer.line_h)
            
            if self.renderer.wrap_enabled:
                # Word wrap mode: scroll by visual lines
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                ln_width = self.renderer.calculate_line_number_width(cr, total_lines)
                viewport_width = self.get_width()
                
                # OPTIMIZATION: For huge files (>10M lines), use fast approximation
                if total_lines > 10_000_000:
                    # Use cached estimate - no expensive calculations
                    estimated_total = self.renderer.estimated_total_cache or total_lines
                    max_scroll_visual = max(0, estimated_total - visible)
                    
                    # Calculate approximate current visual line
                    current_ratio = self.scroll_line / max(1, total_lines)
                    current_visual = int(current_ratio * estimated_total)
                    
                    # Apply scroll steps
                    new_visual = current_visual + steps
                    new_visual = max(0, min(new_visual, max_scroll_visual))
                    
                    if new_visual != current_visual:
                        # Convert back to logical line using simple ratio
                        new_ratio = new_visual / max(1, estimated_total)
                        new_log = int(new_ratio * total_lines)
                        new_log = max(0, min(new_log, total_lines - 1))
                        
                        self.scroll_line = new_log
                        self.scroll_visual_offset = 0
                        
                        # Update scrollbar
                        self.vadj.handler_block_by_func(self.on_vadj_changed)
                        try:
                            self.vadj.set_value(new_visual)
                        finally:
                            self.vadj.handler_unblock_by_func(self.on_vadj_changed)
                        
                        self.queue_draw()
                else:
                    # Normal scrolling for smaller files
                    # Calculate current visual line
                    current_visual = self.renderer.logical_to_visual_line(
                        cr, self.buf, self.scroll_line, 0, ln_width, viewport_width
                    )
                    current_visual += self.scroll_visual_offset
                    
                    # Calculate total visual lines for bounds checking
                    total_visual = self.renderer.get_total_visual_lines(cr, self.buf, ln_width, viewport_width)
                    max_scroll_visual = max(0, total_visual - visible)
                    
                    # Apply scroll steps to visual line
                    new_visual = current_visual + steps
                    
                    # Check cache coverage
                    cache_coverage = len(self.renderer.wrap_cache) / max(1, total_lines)
                    
                    # If scrolling down and approaching the estimated end, recalculate accurately
                    # More aggressive when cache is poor
                    needs_recalc = False
                    if steps > 0 and new_visual > max_scroll_visual * 0.7:
                        needs_recalc = True
                    elif steps > 0 and cache_coverage < 0.2 and new_visual > max_scroll_visual * 0.5:
                        needs_recalc = True
                    
                    if needs_recalc:
                        # We're near the end - recalculate total visual lines accurately
                        # Only clear cache if it's not manually locked
                        if not (hasattr(self.renderer, 'total_visual_lines_locked') and self.renderer.total_visual_lines_locked):
                            self.renderer.total_visual_lines_cache = None
                        
                        # Determine how many lines to calculate based on cache coverage
                        lines_to_calc = visible * 3
                        if cache_coverage < 0.1:
                            lines_to_calc = visible * 5
                        elif cache_coverage < 0.2:
                            lines_to_calc = visible * 4
                        
                        # Calculate accurate total by sampling more lines near the end
                        accurate_total = self.renderer.get_accurate_total_visual_lines_at_end(
                            cr, self.buf, ln_width, viewport_width, visible, lines_to_calc
                        )
                        
                        # Use the larger of the two values (never reduce scroll range)
                        if accurate_total > total_visual:
                            total_visual = accurate_total
                            self.renderer.total_visual_lines_cache = accurate_total
                            max_scroll_visual = max(0, total_visual - visible)
                            
                            # Update scrollbar with new accurate total
                            self.vadj.set_upper(total_visual)
                    
                    new_visual = max(0, min(new_visual, max_scroll_visual))
                    
                    if new_visual != current_visual:
                        # Convert back to logical line + visual offset
                        new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                            cr, self.buf, new_visual, ln_width, viewport_width
                        )
                        
                        self.scroll_line = new_log
                        self.scroll_visual_offset = new_vis_off
                        
                        # Update scrollbar with visual line position
                        self.vadj.handler_block_by_func(self.on_vadj_changed)
                        try:
                            self.vadj.set_value(new_visual)
                        finally:
                            self.vadj.handler_unblock_by_func(self.on_vadj_changed)
                        
                        self.queue_draw()
            else:
                # No wrap mode: scroll by logical lines
                max_scroll = max(0, total_lines - visible)
                
                new_scroll = self.scroll_line + steps
                new_scroll = max(0, min(new_scroll, max_scroll))
                
                if new_scroll != self.scroll_line:
                    self.scroll_line = new_scroll
                    self.scroll_visual_offset = 0
                    
                    # Update scrollbar
                    self.vadj.handler_block_by_func(self.on_vadj_changed)
                    try:
                        self.vadj.set_value(new_scroll)
                    finally:
                        self.vadj.handler_unblock_by_func(self.on_vadj_changed)
                    
                    self.queue_draw()

        if dx and not self.renderer.wrap_enabled:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))
            self.queue_draw()

        return True


    def draw_view(self, area, cr, w, h):
        import time
        draw_start = time.time()
        
        cr.set_source_rgb(0.10, 0.10, 0.10)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        alloc = type("Alloc", (), {"width": w, "height": h})

        # Hide cursor if there's an active selection
        show_cursor = self.cursor_visible and not self.buf.selection.has_selection()


        render_start = time.time()
        self.renderer.draw(
            cr,
            alloc,
            self.buf,
            self.scroll_line,
            self.scroll_x,
            show_cursor,
            self.cursor_phase,
            self.scroll_visual_offset  # Pass visual offset
        )
        render_elapsed = time.time() - render_start
        
        # Draw progress overlay if calculating
        if self.calculating and self.calculation_message:
            # Semi-transparent dark overlay
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.7)
            overlay_h = 60
            overlay_y = (h - overlay_h) // 2
            cr.rectangle(0, overlay_y, w, overlay_h)
            cr.fill()
            
            # Progress text
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.renderer.font)
            layout.set_text(self.calculation_message, -1)
            text_w, text_h = layout.get_pixel_size()
            
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.move_to((w - text_w) // 2, overlay_y + (overlay_h - text_h) // 2)
            PangoCairo.show_layout(cr, layout)
        
        elapsed = time.time() - draw_start
        
        # Update scrollbar after first draw when file is loaded
        if self.needs_scrollbar_init:
            if w > 0 and h > 0:
                self.needs_scrollbar_init = False
                GLib.idle_add(self.update_scrollbar)









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

    def _on_hover_leave(self, controller):
        self._is_hovered = False
        self._update_close_button_state()

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
        """Prepare drag operation - return content provider with tab data"""
        import json
        
        # Get window reference through tab_bar
        window = None
        if self.tab_bar and hasattr(self, '_page'):
            # Find the EditorWindow that owns this tab bar
            parent = self.tab_bar.get_parent()
            while parent:
                if isinstance(parent, Adw.ApplicationWindow):
                    window = parent
                    break
                parent = parent.get_parent()
        
        # Prepare tab data for cross-window transfer
        tab_data = {
            'window_id': id(window) if window else 0,
            'tab_index': self.tab_bar.tabs.index(self) if self.tab_bar and self in self.tab_bar.tabs else -1,
            # ADDITION: Include the unique ID of the live tab object.
            # This allows the target drop handler (e.g., in a new window) to 
            # retrieve and reuse the object instead of creating a copy from data.
            'tab_id': id(self),
        }
        
        # If we have a page reference, serialize the entire structure
        if hasattr(self, '_page'):
            page = self._page
            tab_root = page.get_child()
            
            # Serialize the structure (including splits)
            def serialize_for_drag(widget):
                """Serialize widget structure for drag and drop"""
                if isinstance(widget, Gtk.Box):
                    # TabRoot - serialize its first child
                    child = widget.get_first_child()
                    return serialize_for_drag(child) if child else None
                elif hasattr(widget, '_editor'):
                    # Overlay with editor
                    editor = widget._editor
                    file_path = editor.current_file_path

                    # FIX 1: Only serialize content for UNTITLED (unsaved) files 
                    # to prevent freezing with large saved files.
                    content = editor.get_text() if not file_path else None

                    return {
                        'type': 'editor',
                        'content': content,
                        'file_path': file_path,
                        'title': editor.get_title(),
                        'untitled_number': getattr(editor, 'untitled_number', None),
                    }
                elif isinstance(widget, Gtk.Paned):
                    # Paned with splits
                    return {
                        'type': 'paned',
                        'orientation': 'horizontal' if widget.get_orientation() == Gtk.Orientation.HORIZONTAL else 'vertical',
                        'position': widget.get_position(),
                        'start_child': serialize_for_drag(widget.get_start_child()),
                        'end_child': serialize_for_drag(widget.get_end_child())
                    }
                return None
            
            structure = serialize_for_drag(tab_root)
            
            # Store both the structure and legacy fields for compatibility
            tab_data['structure'] = structure
            # Legacy fields for simple tabs (backwards compatibility)
            editor = tab_root._editor

            # FIX 2: Apply the same content serialization check to the legacy field.
            file_path = editor.current_file_path
            tab_data['content'] = editor.get_text() if not file_path else None
            tab_data['file_path'] = file_path

            tab_data['title'] = editor.get_title()
            tab_data['is_modified'] = self.has_css_class("modified")
            tab_data['untitled_number'] = getattr(editor, 'untitled_number', None)
        
        json_data = json.dumps(tab_data)
        return Gdk.ContentProvider.new_for_value(json_data)
    
    def _on_drag_begin(self, source, drag):
        """Called when drag begins - set visual feedback"""
        global DRAGGED_TAB
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
        DRAGGED_TAB = None
        self.remove_css_class("dragging")
        
        # If drag was successful and cross-window, close the source tab
        if hasattr(self, 'drag_success') and self.drag_success:
            # Find the window that owns this tab
            window = None
            if self.tab_bar:
                parent = self.tab_bar.get_parent()
                while parent:
                    if isinstance(parent, Adw.ApplicationWindow):
                        window = parent
                        break
                    parent = parent.get_parent()
            
            if window and hasattr(window, 'close_tab_after_drag'):
                # Get tab index
                if self.tab_bar and self in self.tab_bar.tabs:
                    tab_index = self.tab_bar.tabs.index(self)
                    # Use GLib.idle_add to close the tab after drag completes
                    GLib.idle_add(window.close_tab_after_drag, tab_index)



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
        drop_target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
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

        # setup hover handlers
        self._connect_hover(tab)

        self._update_separators()

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

        self._update_separators()

    def _connect_hover(self, tab):
        motion = Gtk.EventControllerMotion()

        def on_enter(ctrl, x, y):
            i = self.tabs.index(tab)
            self._hide_pair(i)

        def on_leave(ctrl):
            self._update_separators()

        motion.connect("enter", on_enter)
        motion.connect("leave", on_leave)
        tab.add_controller(motion)

    def set_tab_active(self, tab):
        for t in self.tabs:
            t.set_active(t is tab)

        # update separators *immediately*
        self._update_separators()

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
        self._update_separators()
        
        # Emit signal to notify parent
        self.emit('tab-reordered', tab, new_index)

    def _update_separators(self):
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
    
    def _on_tab_bar_drop(self, target, value, x, y):
        """Handle drop on the tab bar - supports same-window and cross-window tab drops"""
        import json
        global DRAGGED_TAB
        
        # Try to parse as JSON (cross-window drag or tab data)
        tab_data = None
        if isinstance(value, str):
            try:
                tab_data = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                # Not JSON - ignore
                return False
        
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
        
        # Check if this is a cross-window drag
        if tab_data and 'window_id' in tab_data:
            source_window_id = tab_data['window_id']
            target_window_id = id(target_window)
            
            if source_window_id != target_window_id:
                # Cross-window drop
                drop_position = self._calculate_drop_position(x, y)
                
                # Transfer the tab to this window
                if hasattr(target_window, 'transfer_tab_from_data'):
                    target_window.transfer_tab_from_data(tab_data, drop_position)
                    
                    # Mark the drag as successful so source can close the tab
                    if DRAGGED_TAB:
                        DRAGGED_TAB.drag_success = True
                    
                    self._hide_drop_indicator()
                    return True
        
        # Same-window drag (existing logic)
        dragged_tab = DRAGGED_TAB if DRAGGED_TAB else value
        
        if not isinstance(dragged_tab, ChromeTab):
            return False
        
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
        
        # Hide the drop indicator
        self._hide_drop_indicator()
        
        return True


# ============================================================
#   WINDOW
# ============================================================

class EditorPage:
    """A single editor page containing buffer and view"""
    def __init__(self, untitled_title="Untitled 1"):
        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.view.set_margin_top(0)
        self.current_encoding = "utf-8"
        self.current_file_path = None
        self.untitled_title = untitled_title  # Store custom Untitled title
        
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
        self.config_dir = os.path.join(GLib.get_user_config_dir(), "vite")
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
        menu_button.set_menu_model(self.create_menu())
#        menu_button.set_size_request(16, 20)
        self.header.pack_end(menu_button)

        # Tab dropdown button (for file list)
        self.tab_dropdown = Gtk.MenuButton()
        self.tab_dropdown.set_icon_name("pan-down-symbolic")

        self.header.pack_end(self.tab_dropdown)
        
        
        # Tab List (ChromeTabBar) as a top bar
        self.tab_bar = ChromeTabBar()
        self.tab_bar.connect('tab-reordered', self.on_tab_reordered)
        toolbar_view.add_top_bar(self.tab_bar)

        # Tab View (Content)
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True)
        self.tab_view.set_hexpand(True)
        self.tab_view.connect("notify::selected-page", self.on_page_selection_changed)
        toolbar_view.set_content(self.tab_view)

        self.set_content(toolbar_view)
        
        # Setup actions
        self.setup_actions()
        self.setup_tab_actions()
        
        # Add initial tab
        self.add_tab()
        
        # Add key controller for shortcuts (Ctrl+Tab)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_window_key_pressed)
        self.add_controller(key_ctrl)
        
        # Setup drop targets for drag-and-drop functionality
        self._setup_drop_targets()
        
        # Handle window close request
        self.connect("close-request", self.on_close_request)
        
        # Connect to theme changes
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self.on_theme_changed)

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
            editor.current_file_path = path
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

        return editor

    def _create_editor_overlay(self, editor, add_close_button=False):
        """Helper to create editor overlay with scrollbars
        
        Args:
            editor: EditorPage instance
            add_close_button: If True, adds a close button for split views
        """
        overlay = Gtk.Overlay()
            
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
        return overlay, editor

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

    def on_tab_close_requested(self, tab):
        if hasattr(tab, '_page'):
            self.close_tab(tab._page)

    def on_tab_reordered(self, tab_bar, tab, new_index):
        """Sync Adw.TabView order with ChromeTabBar order"""
        if hasattr(tab, '_page'):
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
        new_window.tab_view.set_selected_page(new_page)
        
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
        
        # Close the original page (it's now empty, so this is fast)
        self.tab_view.close_page(page)
        
        # Remove the chrome tab
        for tab in self.tab_bar.tabs:
            if hasattr(tab, '_page') and tab._page == page:
                self.tab_bar.remove_tab(tab)
                break
        
        # Update UI state
        self.update_ui_state()
        self.update_tab_dropdown()
    def transfer_tab_from_data(self, tab_data, drop_position=None):
        """Create a new tab from transferred data (cross-window drag)"""
        # Create TabRoot
        tab_root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        
        # Check if we have structure data (with splits) or just simple editor data
        if 'structure' in tab_data and tab_data['structure']:
            # Reconstruct the full structure including splits
            structure = tab_data['structure']
            
            def reconstruct_from_drag(data, is_root=False):
                """Reconstruct structure from drag data"""
                if data['type'] == 'editor':
                    # Create editor
                    editor = EditorPage(data['title'])
                    
                    # Handle content: if None (saved file), reload from file_path
                    if data['content'] is not None:
                        editor.set_text(data['content'])
                    elif data['file_path']:
                        # Reload from file
                        try:
                            with open(data['file_path'], 'r', encoding='utf-8') as f:
                                content = f.read()
                            editor.set_text(content)
                        except Exception as e:
                            print(f"Error reloading file {data['file_path']}: {e}")
                            editor.set_text("")  # Fallback to empty
                    else:
                        editor.set_text("")  # Fallback for edge cases
                    
                    editor.current_file_path = data['file_path']
                    editor.set_title(data['title'])
                    editor.untitled_number = data['untitled_number']
                    
                    # Create overlay (with close button if not root)
                    overlay, editor = self._create_editor_overlay(editor, add_close_button=not is_root)
                    overlay.set_hexpand(True)
                    overlay.set_vexpand(True)
                    
                    if is_root:
                        # Store reference on tab_root for the primary editor
                        tab_root._editor = editor
                        editor._overlay = overlay
                    
                    return overlay, editor
                    
                elif data['type'] == 'paned':
                    # Create paned
                    orientation = Gtk.Orientation.HORIZONTAL if data['orientation'] == 'horizontal' else Gtk.Orientation.VERTICAL
                    paned = Gtk.Paned(orientation=orientation)
                    paned.set_hexpand(True)
                    paned.set_vexpand(True)
                    
                    # Reconstruct children
                    start_widget, start_editor = reconstruct_from_drag(data['start_child'])
                    end_widget, end_editor = reconstruct_from_drag(data['end_child'])
                    
                    paned.set_start_child(start_widget)
                    paned.set_end_child(end_widget)
                    
                    # If this is root, set the first editor as the primary editor
                    if is_root and start_editor:
                        tab_root._editor = start_editor
                        start_editor._overlay = start_widget
                    
                    # Set position
                    position = data.get('position', 400)
                    def set_pos():
                        paned.set_position(position)
                        return False
                    GLib.idle_add(set_pos)
                    
                    return paned, start_editor
                
                return None, None
            
            reconstructed_widget, primary_editor = reconstruct_from_drag(structure, is_root=True)
            
            if reconstructed_widget:
                tab_root.append(reconstructed_widget)
                reconstructed_widget.set_hexpand(True)
                reconstructed_widget.set_vexpand(True)
        else:
            # Legacy: simple editor without splits
            new_editor = EditorPage(tab_data.get('title', 'Untitled 1'))
            new_editor.set_text(tab_data.get('content', ''))
            new_editor.current_file_path = tab_data.get('file_path')
            new_editor.set_title(tab_data.get('title', 'Untitled 1'))
            new_editor.untitled_number = tab_data.get('untitled_number')
            
            # Create overlay layout for editor
            overlay, new_editor = self._create_editor_overlay(new_editor)
            overlay.set_hexpand(True)
            overlay.set_vexpand(True)
            
            tab_root.append(overlay)
            
            # Store references
            tab_root._editor = new_editor
            new_editor._overlay = overlay
        
        # Get the primary editor (always set on tab_root)
        primary_editor = tab_root._editor
        title = tab_data.get('title', primary_editor.get_title() if primary_editor else 'Untitled')
        
        # Add to tab view
        page = self.tab_view.append(tab_root)
        page.set_title(title)
        
        # Add ChromeTab to ChromeTabBar at the specified position
        chrome_tab = ChromeTab(title=title)
        chrome_tab._page = page
        
        # Connect signals
        chrome_tab.connect('activate-requested', self.on_tab_activated)
        chrome_tab.connect('close-requested', self.on_tab_close_requested)
        
        # Insert at drop position if specified
        if drop_position is not None and 0 <= drop_position <= len(self.tab_bar.tabs):
            # Insert tab at specific position
            idx = drop_position
            
            # Insert tab AFTER separator[idx]
            before_sep = self.tab_bar.separators[idx]
            self.tab_bar.insert_child_after(chrome_tab, before_sep)
            
            # Insert separator AFTER the tab
            new_sep = Gtk.Box()
            new_sep.set_size_request(1, 1)
            new_sep.add_css_class("chrome-tab-separator")
            self.tab_bar.insert_child_after(new_sep, chrome_tab)
            
            # Update internal lists
            self.tab_bar.tabs.insert(idx, chrome_tab)
            self.tab_bar.separators.insert(idx + 1, new_sep)
            
            # Set tab_bar reference
            chrome_tab.tab_bar = self.tab_bar
            chrome_tab.separator = new_sep
            
            # Setup hover handlers
            self.tab_bar._connect_hover(chrome_tab)
            self.tab_bar._update_separators()
            
            # Reorder the page in TabView to match
            self.tab_view.reorder_page(page, idx)
        else:
            # Add at end
            self.tab_bar.add_tab(chrome_tab)
        
        # Set modified state if needed
        if tab_data.get('is_modified', False):
            chrome_tab.add_css_class("modified")
        
        # Select the new tab
        self.tab_view.set_selected_page(page)
        
        # Update UI
        self.update_ui_state()
        self.update_tab_dropdown()
        
        # Focus the editor
        if primary_editor:
            primary_editor.view.grab_focus()
        
        return primary_editor

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
        self.tab_bar._update_separators()

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
        """Create the application menu"""
        menu = Gio.Menu()
        
        # File section
        file_section = Gio.Menu()
        file_section.append("Save As...", "win.save-as")
        menu.append_section("File", file_section)
        
        # Encoding section with submenu
        encoding_submenu = Gio.Menu()
        encoding_submenu.append("UTF-8", "win.encoding::utf-8")
        encoding_submenu.append("UTF-8 with BOM", "win.encoding::utf-8-sig")
        encoding_submenu.append("UTF-16 LE", "win.encoding::utf-16le")
        encoding_submenu.append("UTF-16 BE", "win.encoding::utf-16be")
        
        encoding_section = Gio.Menu()
        encoding_section.append_submenu("Encoding", encoding_submenu)
        menu.append_section(None, encoding_section)
        
        return menu
    
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
        # Save As action
        save_as_action = Gio.SimpleAction.new("save-as", None)
        save_as_action.connect("activate", self.on_save_as)
        self.add_action(save_as_action)
        
        # Encoding action with parameter
        encoding_action = Gio.SimpleAction.new_stateful(
            "encoding",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string("utf-8")
        )
        encoding_action.connect("activate", self.on_encoding_changed)
        self.add_action(encoding_action)
        
        # Tab activate action (for dropdown menu)
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
            editor.current_file_path = path
            
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
        loading_dialog = LoadingDialog(self)
        loading_dialog.present()
        
        idx = IndexedFile(path)
        
        def progress_callback(fraction):
            loading_dialog.update_progress(fraction)
            return False
        
        def index_complete():
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
            
            # Clear optimization caches
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
            
            loading_dialog.close()
            
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
        super().__init__(application_id="io.github.fastrizwaan.vite",
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.files_to_open = []

    
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
            
            # Open files from command line if any
            if self.files_to_open:
                # Close the initial empty tab
                if win.tab_view.get_n_pages() == 1:
                    first_page = win.tab_view.get_nth_page(0)
                    win.tab_view.close_page(first_page)
                    for tab in win.tab_bar.tabs:
                        if hasattr(tab, '_page') and tab._page == first_page:
                            win.tab_bar.remove_tab(tab)
                            break
                
                # Open each file in a new tab
                for file_path in self.files_to_open:
                    win.add_tab(file_path)
                
                self.files_to_open = []
        
        win.present()
    
    def do_open(self, files, n_files, hint):
        """Handle files passed via command line"""
        self.files_to_open = [f.get_path() for f in files]
        self.activate()


if __name__ == "__main__":
    VirtualTextEditor().run(sys.argv)
