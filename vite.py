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

CSS_OVERLAY_SCROLLBAR = """
/* Vertical container */
.overlay-scrollbar {
    background-color: rgb(25,25,25);
    min-width: 2px;
}

/* Vertical thumb */
.overlay-scrollbar trough > slider {
    min-width: 8px;
    border-radius: 12px;
    background-color: rgba(0,127,255,0.52);
    transition: min-width 200ms ease, background-color 200ms ease;
}

/* Hover → wider */
.overlay-scrollbar trough > slider:hover {
    min-width: 8px;
    background-color: rgba(0,127,255,0.52);
}

/* Dragging → :active (GTK4-native) */
.overlay-scrollbar trough > slider:active {
    min-width: 8px;
    background-color: rgba(255,255,255,0.50);
}


/* ---------------- HORIZONTAL ---------------- */
.hscrollbar-overlay  {
    background-color: rgb(25,25,25);
    min-width: 2px;
}
.hscrollbar-overlay trough > slider {
    min-height: 2px;
    border-radius: 12px;
    background-color: rgba(0,127,255,0.52);
    transition: min-height 200ms ease, background-color 200ms ease;
}

.hscrollbar-overlay trough > slider:hover {
    min-height: 8px;
    background-color: rgba(0,127,255,0.52);
}

/* Dragging (GTK4-native) */
.hscrollbar-overlay trough > slider:active {
    min-height: 8px;
    background-color: rgba(255,255,255,0.50);
}
.editor-surface {
    background-color: rgb(25,25,25); /* same as your renderer’s bg */
}

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
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)

        print(f"File opened and mapped in {time.time()-start:.2f}s")
        
        # Use array.array instead of list - much faster for millions of integers
        # 'Q' = unsigned long long (8 bytes, perfect for file offsets)
        self.index = array('Q')

    def detect_encoding(self, path):
        with open(path, "rb") as f:
            data = f.read(4096)  # small peek is enough

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
        zeros_in_even = sum(1 for i in range(0, len(data), 2) if data[i] == 0)
        ratio_be = zeros_in_even / (len(data) / 2)
        if ratio_be > 0.4:
            return "utf-16be"

        # Default
        return "utf-8"


    def index_file(self, progress_callback=None):
        start_time = time.time()
        enc = self.encoding
        
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
        
        # Use mmap.find() to scan for newlines
        pos = start_pos
        last_report = 0
        report_interval = 50_000_000  # Report every 50MB for less overhead
        
        while pos < total_size:
            # Report progress less frequently
            if progress_callback and pos - last_report > report_interval:
                last_report = pos
                progress = pos / total_size
                GLib.idle_add(progress_callback, progress)
            
            # Find next newline directly in mmap (no copy!)
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

    def load(self, indexed_file):
        self.file = indexed_file
        self.edits.clear()
        self.deleted_lines.clear()
        self.inserted_lines.clear()
        self.line_offsets = []
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection.clear()
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
            return base + net_insertions
        
        return base

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


    def insert_text(self, text):
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
        
        # Calculate average character width dynamically
        layout.set_text("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", -1)
        ink, logical = layout.get_pixel_extents()
        self.avg_char_width = logical.width / 62.0
        
        # Colors
        self.editor_background_color = (0.10, 0.10, 0.10)
        self.text_foreground_color   = (0.90, 0.90, 0.90)
        self.linenumber_foreground_color = (0.60, 0.60, 0.60)
        self.selection_background_color = (0.2, 0.4, 0.6)
        self.selection_foreground_color = (1.0, 1.0, 1.0)

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
            available = max(0, view_w - ln_width)
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
        max_text_width = max(100, viewport_width - ln_width)
        wrap_points = self.calculate_wrap_points(cr, text, max_text_width)
        
        # Cache the result
        self.wrap_cache[ln] = wrap_points
        return wrap_points
    
    def get_visual_line_count_for_logical(self, cr, buf, ln, ln_width, viewport_width):
        """Get the number of visual lines for a specific logical line."""
        if not self.wrap_enabled:
            return 1
        
        wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, viewport_width)
        return len(wrap_points)
    
    def get_total_visual_lines(self, cr, buf, ln_width, viewport_width):
        """Get total number of visual lines.
        
        For word wrap to behave like real line rendering, we need accurate counts.
        Uses progressive calculation and caching.
        """
        if not self.wrap_enabled:
            return buf.total()
        
        # Return cached value if available and valid
        if self.total_visual_lines_cache is not None and self.total_visual_lines_cache > 0:
            return self.total_visual_lines_cache
        
        total_logical = buf.total()
        
        # For small files, calculate exactly
        if total_logical <= 5000:
            total_visual = 0
            for ln in range(total_logical):
                total_visual += self.get_visual_line_count_for_logical(cr, buf, ln, ln_width, viewport_width)
            self.total_visual_lines_cache = total_visual
            return total_visual
        
        # For large files, use cached values + estimation for uncached
        # Count cached lines and estimate the rest
        cached_count = 0
        cached_visual = 0
        
        for ln, wrap_points in self.wrap_cache.items():
            if ln < total_logical:  # Ensure cache isn't stale
                cached_count += 1
                cached_visual += len(wrap_points)
        
        # Estimate uncached lines
        uncached_count = total_logical - cached_count
        if uncached_count <= 0:
            self.total_visual_lines_cache = cached_visual
            return cached_visual

        # Better estimation using file statistics if available
        if hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
            # Use total file size to estimate average line length
            total_bytes = len(buf.file.mm)
            avg_bytes_per_line = total_bytes / max(1, total_logical)
            
            # Subtract 1 byte for newline (approximate)
            avg_content_len = max(0, avg_bytes_per_line - 1)
            
            # Estimate visual lines based on average line length and viewport width
            max_text_width = max(100, viewport_width - ln_width)
            chars_per_line = max(1, int(max_text_width / self.avg_char_width))
            
            # Estimate visual lines per logical line
            # (avg_content_len + chars_per_line - 1) // chars_per_line
            est_visual_per_line = max(1, (avg_content_len + chars_per_line - 1) // chars_per_line)
            
            estimated_uncached = int(uncached_count * est_visual_per_line)
            total_visual = cached_visual + estimated_uncached
        elif cached_count > 0:
            # Use average from cached lines
            avg_visual_per_line = cached_visual / cached_count
            estimated_uncached = int(uncached_count * avg_visual_per_line)
            total_visual = cached_visual + estimated_uncached
        else:
            # No cache and no file stats - use simple estimate (sample first 100 lines)
            sample_size = min(100, total_logical)
            sample_visual = 0
            for ln in range(sample_size):
                sample_visual += self.get_visual_line_count_for_logical(cr, buf, ln, ln_width, viewport_width)
            avg_visual_per_line = sample_visual / sample_size
            total_visual = int(total_logical * avg_visual_per_line)
        
        # Ensure at least one visual line per logical line
        total_visual = max(total_visual, total_logical)
        self.total_visual_lines_cache = total_visual
        return total_visual
    
    def get_accurate_total_visual_lines_at_end(self, cr, buf, ln_width, viewport_width, visible_lines):
        """Calculate accurate total visual lines for scrolling to end of document.
        
        This ensures no extra scrollable space at the end by computing exact
        visual line counts for the last portion of the document.
        """
        total_logical = buf.total()
        if total_logical == 0:
            return 0
        
        # Calculate visual lines for all logical lines from the end
        # going backwards until we've covered enough to fill the viewport
        total_visual = 0
        
        # For accuracy near the end, compute exact values for last N logical lines
        # that would roughly fill 2x the viewport
        lines_to_compute_exact = min(total_logical, visible_lines * 3)
        start_line = max(0, total_logical - lines_to_compute_exact)
        
        # Estimate visual lines for lines before start_line
        estimated_before = 0
        if start_line > 0:
            if start_line in self.wrap_cache:
                # We have some cached data, use it
                for ln in range(start_line):
                    if ln in self.wrap_cache:
                        estimated_before += len(self.wrap_cache[ln])
                    else:
                        text = buf.get_line(ln)
                        max_text_width = max(100, viewport_width - ln_width)
                        estimated_before += self.estimate_visual_line_count(text, max_text_width) if text else 1
            else:
                # Simple estimate: assume average of 1 visual line per logical line
                estimated_before = start_line
        
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
                if dist > 5000 and hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
                     # Estimate a chunk
                     chunk_size = dist - 1000 # Leave 1000 lines for exact calc
                     
                     # Use global average for estimation
                     total_bytes = len(buf.file.mm)
                     avg_bytes_per_line = total_bytes / max(1, total_logical)
                     avg_content_len = max(0, avg_bytes_per_line - 1)
                     max_text_width = max(100, viewport_width - ln_width)
                     chars_per_line = max(1, int(max_text_width / self.avg_char_width))
                     est_visual_per_line = max(1, (avg_content_len + chars_per_line - 1) // chars_per_line)
                     
                     visual_line += int(chunk_size * est_visual_per_line)
                     current_ln += chunk_size
                     continue

                visual_line += self.get_visual_line_count_for_logical(cr, buf, current_ln, ln_width, viewport_width)
                current_ln += 1
                
        elif start_ln > logical_line:
            # Going backward
            current_ln = start_ln - 1
            while current_ln >= logical_line:
                visual_line -= self.get_visual_line_count_for_logical(cr, buf, current_ln, ln_width, viewport_width)
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
        if distance > 1000 and hasattr(buf, 'file') and buf.file and hasattr(buf.file, 'mm'):
            # Use binary search with estimation
            total_bytes = len(buf.file.mm)
            avg_bytes_per_line = total_bytes / max(1, total_logical)
            avg_content_len = max(0, avg_bytes_per_line - 1)
            max_text_width = max(100, viewport_width - ln_width)
            chars_per_line = max(1, int(max_text_width / self.avg_char_width))
            est_visual_per_line = max(1, (avg_content_len + chars_per_line - 1) // chars_per_line)
            
            # Estimate logical line from visual line
            estimated_logical = start_log + int(distance / est_visual_per_line)
            estimated_logical = max(start_log, min(estimated_logical, total_logical - 1))
            
            # Binary search around the estimate
            search_range = 100  # Reduced from 200 for faster search
            left = max(start_log, estimated_logical - search_range)
            right = min(total_logical - 1, estimated_logical + search_range)
            
            # Use pure estimation for initial visual position at left
            # This avoids iterating through potentially thousands of lines
            estimated_visual_at_left = current_visual + int((left - start_log) * est_visual_per_line)
            test_visual = estimated_visual_at_left
            
            # Binary search with estimation
            while left < right:
                mid = (left + right) // 2
                
                # Estimate visual line at mid using average
                mid_visual = estimated_visual_at_left + int((mid - left) * est_visual_per_line)
                
                if mid_visual < visual_line:
                    left = mid + 1
                    test_visual = mid_visual + est_visual_per_line
                else:
                    right = mid
            
            # Now we have an approximate logical line
            # Do precise calculation only for a small range around it
            current_visual = test_visual
            start_log = max(0, left - 10)  # Start a bit before for safety
            
            # Recalculate current_visual for start_log
            current_visual = 0
            if start_log > 0:
                # Use estimation for the bulk
                current_visual = int(start_log * est_visual_per_line)
            
            # Fine-tune with actual calculation for last few lines
            for ln in range(max(0, start_log - 10), start_log):
                if ln in self.wrap_cache:
                    current_visual += len(self.wrap_cache[ln])
                else:
                    text = buf.get_line(ln)
                    current_visual += self.estimate_visual_line_count(text, max_text_width)

        # Linear search with estimation (optimized for remaining distance)
        for ln in range(start_log, total_logical):
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
        
        # Clear total visual lines cache
        self.total_visual_lines_cache = None
        
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

        # Background
        cr.set_source_rgb(*self.editor_background_color)
        cr.paint()

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

        # Render loop: Iterate logical lines starting from scroll_line
        # For each logical line, iterate its visual lines
        # Skip initial visual lines based on scroll_visual_offset (for the first line only)
        
        y = 0
        current_vis_count = 0
        cursor_screen_pos = None  # (x, y, height)
        
        # Start iterating from the scroll logical line
        for ln in range(scroll_line, total):
            if current_vis_count >= max_vis:
                break
                
            line_text = buf.get_line(ln)
            
            # Determine wrap points
            if self.wrap_enabled:
                wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, alloc.width)
            else:
                wrap_points = [(0, len(line_text))]
            
            # Determine which visual lines to draw for this logical line
            # For the FIRST line (scroll_line), skip 'scroll_visual_offset' lines
            start_vis_idx = 0
            if ln == scroll_line:
                start_vis_idx = scroll_visual_offset
                # Ensure offset is valid
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
                
                # Draw line number only for the first visual line of the logical line
                if vis_idx == 0:
                    cr.set_source_rgb(*self.linenumber_foreground_color)
                    layout.set_text(str(ln + 1), -1)
                    layout.set_width(-1) # Reset width
                    w, h = layout.get_pixel_size()
                    cr.move_to(ln_width - w - 4, y)
                    PangoCairo.show_layout(cr, layout)
                else:
                    # Draw continuation indicator for wrapped lines
                    cr.set_source_rgb(*self.linenumber_foreground_color)
                    layout.set_text("⤷", -1)
                    layout.set_width(-1)
                    w, h = layout.get_pixel_size()
                    cr.move_to(ln_width - w - 4, y)
                    PangoCairo.show_layout(cr, layout)

                # Draw text
                cr.set_source_rgb(*self.text_foreground_color)
                layout.set_text(text_segment if text_segment else " ", -1)
                
                # RTL detection
                rtl = line_is_rtl(text_segment)
                
                text_w, text_h = layout.get_pixel_size()
                base_x = self.calculate_text_base_x(rtl, text_w, alloc.width, ln_width, scroll_x)
                
                cr.move_to(base_x, y)
                PangoCairo.show_layout(cr, layout)

                # Draw selection
                if sel_start_ln != -1:
                    # Check if this visual line is within selection
                    # We need to map selection columns to this visual line's columns
                    
                    # Logic:
                    # 1. Determine intersection of [col_start, col_end] with selection
                    # 2. If intersection exists, draw it
                    
                    # Adjust selection range for this line
                    s_col = -1
                    e_col = -1
                    
                    if ln > sel_start_ln and ln < sel_end_ln:
                        # Fully selected line
                        s_col = 0
                        e_col = len(text_segment)
                        if vis_idx == len(wrap_points) - 1:
                             e_col += 1 # Include newline
                    elif ln == sel_start_ln and ln == sel_end_ln:
                        # Selection within single line
                        # Map global cols to local segment cols
                        # Intersection of [sel_start, sel_end] and [col_start, col_end]
                        
                        seg_sel_start = max(sel_start_col, col_start)
                        seg_sel_end = min(sel_end_col, col_end)
                        
                        if seg_sel_start < seg_sel_end:
                            s_col = seg_sel_start - col_start
                            e_col = seg_sel_end - col_start
                    elif ln == sel_start_ln:
                        # Start line
                        if sel_start_col < col_end:
                            s_col = max(0, sel_start_col - col_start)
                            e_col = len(text_segment)
                            if vis_idx == len(wrap_points) - 1:
                                e_col += 1
                    elif ln == sel_end_ln:
                        # End line
                        if sel_end_col > col_start:
                            s_col = 0
                            e_col = min(len(text_segment), sel_end_col - col_start)

                    if s_col != -1:
                        # Draw selection rect
                        # Convert cols to pixels
                        idx1 = visual_byte_index(text_segment, s_col)
                        idx2 = visual_byte_index(text_segment, e_col)
                        
                        r1 = layout.index_to_pos(idx1)
                        r2 = layout.index_to_pos(idx2)
                        
                        x1 = r1.x / Pango.SCALE
                        x2 = r2.x / Pango.SCALE
                        
                        # Handle newline selection
                        if e_col > len(text_segment):
                             x2 += 5 # Width of newline selection
                        
                        sel_x = base_x + min(x1, x2)
                        sel_w = abs(x2 - x1)
                        
                        cr.set_source_rgba(*self.selection_background_color, 0.3)
                        cr.rectangle(sel_x, y, sel_w, self.line_h)
                        cr.fill()

                # Draw Cursor
                if ln == buf.cursor_line:
                    # Check if cursor is on this visual line
                    c_col = buf.cursor_col
                    
                    # Cursor is on this line if:
                    # 1. col_start <= c_col < col_end
                    # 2. OR c_col == col_end AND this is the last visual line (newline)
                    # 3. OR c_col == col_end AND next visual line starts at col_end (cursor at wrap point)
                    #    Standard behavior: cursor stays at end of previous line or start of next?
                    #    Usually start of next. But if we type, it goes to next.
                    #    Let's say cursor at wrap point belongs to NEXT line (start of next).
                    #    EXCEPT if it's the very end of file/line.
                    
                    is_cursor_here = False
                    cursor_rel_col = 0
                    
                    if col_start <= c_col < col_end:
                        is_cursor_here = True
                        cursor_rel_col = c_col - col_start
                    elif c_col == col_end:
                        # Cursor at end of segment
                        if vis_idx == len(wrap_points) - 1:
                            # End of logical line -> cursor is here (after last char)
                            is_cursor_here = True
                            cursor_rel_col = c_col - col_start
                        else:
                            # Wrap point. Cursor should be on NEXT visual line (start of it)
                            # So NOT here.
                            pass
                            
                    if is_cursor_here:
                        idx = visual_byte_index(text_segment, cursor_rel_col)
                        pos = layout.index_to_pos(idx)
                        cx = base_x + (pos.x / Pango.SCALE)
                        
                        # Capture cursor screen position for IME
                        cursor_screen_pos = (cx, y, self.line_h)
                        
                        # Draw cursor
                        if cursor_visible:
                            cr.set_source_rgba(*self.text_foreground_color, 0.8 * (math.sin(cursor_phase * 2 * math.pi) * 0.5 + 0.5))
                            cr.rectangle(cx, y, 2, self.line_h)
                            cr.fill()
                        

                y += self.line_h
                current_vis_count += 1
        has_selection = buf.selection.has_selection()
        if has_selection:
            sel_start_line, sel_start_col, sel_end_line, sel_end_col = buf.selection.get_bounds()
        else:
            sel_start_line = sel_start_col = sel_end_line = sel_end_col = -1


        # ============================================================
        # PREEDIT (IME)
        # ============================================================
        
        # Only draw preedit if cursor is visible on screen
        if hasattr(buf, "preedit_string") and buf.preedit_string and cursor_screen_pos:
            px, py, ph = cursor_screen_pos
            
            # Use the captured cursor position directly
            # We need to recreate layout for preedit text
            pe_l = PangoCairo.create_layout(cr)
            pe_l.set_font_description(self.renderer.font)
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

            # Preedit cursor
            if hasattr(buf, "preedit_cursor"):
                pc = buf.preedit_cursor

                pe_l2 = PangoCairo.create_layout(cr)
                pe_l2.set_font_description(self.renderer.font)
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
        # DRAG-AND-DROP PREVIEW OVERLAY
        # ============================================================
        # Draw preview overlay at drop position
        if hasattr(buf, '_view') and buf._view:
            view = buf._view
            if view.drag_and_drop_mode and view.drop_position_line >= 0:
                drop_ln = view.drop_position_line
                drop_col = view.drop_position_col
                
                # Check if drop position is within original selection (no-op)
                drop_in_selection = False
                if buf.selection.has_selection():
                    bounds = buf.selection.get_bounds()
                    if bounds and bounds[0] is not None:
                        sel_start_line, sel_start_col, sel_end_line, sel_end_col = bounds
                        
                        if sel_start_line == sel_end_line:
                            # Single line selection
                            if drop_ln == sel_start_line and sel_start_col <= drop_col <= sel_end_col:
                                drop_in_selection = True
                        else:
                            # Multi-line selection
                            if drop_ln == sel_start_line and drop_col >= sel_start_col:
                                drop_in_selection = True
                            elif drop_ln == sel_end_line and drop_col <= sel_end_col:
                                drop_in_selection = True
                            elif sel_start_line < drop_ln < sel_end_line:
                                drop_in_selection = True
                
                # Draw overlay even if over selection, but skip cursor
                # Calculate drop position accounting for wrapped lines
                if self.wrap_enabled:
                    # Need to find visual Y position
                    current_y = 0
                    found = False
                    
                    ln = scroll_line
                    vis_offset = scroll_visual_offset
                    
                    while ln <= drop_ln and current_y < alloc.height:
                        wrap_points = self.get_wrap_points_for_line(cr, buf, ln, ln_width, alloc.width)
                        
                        if ln == drop_ln:
                            # Find which visual sub-line contains drop_col
                            for vis_idx, (start_col, end_col) in enumerate(wrap_points):
                                # Skip lines before scroll_visual_offset
                                if ln == scroll_line and vis_idx < vis_offset:
                                    continue
                                
                                # Check if drop_col is in this wrap segment
                                if start_col <= drop_col <= end_col:
                                    drop_y = current_y
                                    found = True
                                    break
                                
                                # Move to next visual line
                                current_y += self.line_h
                                if current_y >= alloc.height:
                                    break
                            break
                        else:
                            # Count visual lines for this logical line
                            if ln == scroll_line:
                                # Start from vis_offset
                                num_vis = len(wrap_points) - vis_offset
                            else:
                                num_vis = len(wrap_points)
                            current_y += num_vis * self.line_h
                            ln += 1
                    
                    if not found:
                        # Drop position not visible
                        drop_y = -1
                else:
                    # No wrapping - simple calculation
                    if scroll_line <= drop_ln < scroll_line + max_vis:
                        drop_y = (drop_ln - scroll_line) * self.line_h
                    else:
                        drop_y = -1
                
                if drop_y >= 0:
                    drop_text = buf.get_line(drop_ln)
                    
                    # For wrapped lines, we need to use the correct text segment
                    if self.wrap_enabled:
                        wrap_points = self.get_wrap_points_for_line(cr, buf, drop_ln, ln_width, alloc.width)
                        
                        # Find which wrap segment contains drop_col
                        segment_start = 0
                        segment_end = len(drop_text)
                        for start_col, end_col in wrap_points:
                            if start_col <= drop_col <= end_col:
                                segment_start = start_col
                                segment_end = end_col
                                break
                        
                        # Use only the text segment for this wrap
                        segment_text = drop_text[segment_start:segment_end] if drop_text else " "
                        # Column relative to segment start
                        segment_col = drop_col - segment_start
                    else:
                        segment_text = drop_text if drop_text else " "
                        segment_col = drop_col
                    
                    # Calculate drop position
                    layout = self.create_text_layout(cr, segment_text)
                    is_rtl = detect_rtl_line(segment_text)
                    text_w, _ = layout.get_pixel_size()
                    view_w = alloc.width
                    base_x = self.calculate_text_base_x(is_rtl, text_w, view_w, ln_width, scroll_x)
                    
                    # Get x position for drop column (relative to segment)
                    drop_byte_idx = visual_byte_index(segment_text, min(segment_col, len(segment_text)))
                    strong_pos, _ = layout.get_cursor_pos(drop_byte_idx)
                    drop_x = base_x + (strong_pos.x // Pango.SCALE)
                    
                    # Determine colors based on copy (Ctrl) vs move mode
                    is_copy = view.ctrl_pressed_during_drag
                    if is_copy:
                        # Green for copy
                        cursor_color = (0.0, 1.0, 0.3, 0.9)
                        bg_color = (0.0, 0.8, 0.3, 1.0)  # Opaque green background
                        border_color = (0.0, 1.0, 0.3, 1.0)
                    else:
                        # Orange for move
                        cursor_color = (1.0, 0.6, 0.0, 0.9)
                        bg_color = (1.0, 0.5, 0.0, 1.0)  # Opaque orange background
                        border_color = (1.0, 0.6, 0.0, 1.0)
                    
                    # Draw cursor at drop position ONLY if not over selection
                    if not drop_in_selection:
                        cr.set_source_rgba(*cursor_color)
                        cr.set_line_width(2)
                        cr.move_to(drop_x, drop_y)
                        cr.line_to(drop_x, drop_y + self.line_h)
                        cr.stroke()
                    
                    # Draw viewport border (1 pixel) - always show
                    cr.set_source_rgba(*border_color)
                    cr.set_line_width(1)
                    cr.rectangle(0, 0, alloc.width, alloc.height)
                    cr.stroke()
                    
                    # Draw the dragged text as overlay with background (no border) - always show
                    dragged_text = view.dragged_text
                    if dragged_text:
                        # Check if multi-line selection
                        is_multiline = '\n' in dragged_text
                        
                        # Create layout for dragged text
                        overlay_layout = self.create_text_layout(cr, dragged_text)
                        overlay_w, overlay_h = overlay_layout.get_pixel_size()
                        
                        # Offset the overlay below the cursor so pointer is above it
                        vertical_offset = 20  # Pixels below the cursor
                        drop_y_offset = drop_y + vertical_offset
                        
                        # Draw background only for single-line selections
                        if not is_multiline:
                            padding = 4
                            cr.set_source_rgba(*bg_color)
                            cr.rectangle(drop_x - padding, drop_y_offset - padding, 
                                       overlay_w + 2*padding, self.line_h + 2*padding)
                            cr.fill()
                        
                        # Draw the text with transparency
                        r, g, b = self.text_foreground_color
                        cr.set_source_rgba(r, g, b, 0.7)  # 70% opacity
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
        self.scroll_line = 0  # Logical line at top of viewport
        self.scroll_visual_offset = 0  # Visual line offset within the logical line
        self.scroll_x = 0
        
        # Throttling for scrollbar updates
        self.scroll_update_pending = False
        self.pending_scroll_value = None

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
        # Clear wrap cache as content has changed
        if self.renderer.wrap_enabled:
            self.renderer.wrap_cache.clear()
            self.renderer.total_visual_lines_cache = -1
            self.renderer.visual_line_anchor = (0, 0)
            
        # Update scrollbars after width changes
        self.update_scrollbar()
        self.queue_draw()


    def on_vadj_changed(self, adj):
        """Handle scrollbar value change."""
        val = adj.get_value()
        
        if self.renderer.wrap_enabled:
            # Throttle updates during rapid scrolling
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
            return False
        
        visual_line = self.pending_scroll_value
        self.pending_scroll_value = None
        
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())
        viewport_width = self.get_width()
        
        # Convert visual line to logical line
        logical_line, vis_idx, col_start, col_end = self.renderer.visual_to_logical_line(
            cr, self.buf, visual_line, ln_width, viewport_width
        )
        
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
        """Handle window resize to update scrollbar visibility"""
        # Clear wrap cache on resize to force recalculation with new width
        if self.renderer.wrap_enabled:
            self.renderer.wrap_cache = {}
            self.renderer.visual_line_map = []
            self.renderer.total_visual_lines_cache = None
            self.renderer.visual_line_anchor = (0, 0)
        self.update_scrollbar()
        return False


    def file_loaded(self):
        """Called after a new file is loaded to trigger width calculation"""
        self.renderer.needs_full_width_scan = True
        self.queue_draw()
        self.update_scrollbar()
        
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
            doc_w = self.renderer.max_line_width

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
        # Always start blinking from fully visible
        self.cursor_phase = 0.0

        def blink():
            self.cursor_phase += self.cursor_fade_speed
            if self.cursor_phase >= 2.0:
                self.cursor_phase -= 2.0

            self.queue_draw()
            return True

        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)

        self.cursor_blink_timeout = GLib.timeout_add(20, blink)


    def stop_cursor_blink(self):
        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)
            self.cursor_blink_timeout = None

        self.cursor_visible = True
        self.cursor_phase = 0.0   # NOT 1.0
        self.queue_draw()



    def on_commit(self, im, text):
        """Handle committed text from IM (finished composition)"""
        if text:
            # Insert typed text
            self.buf.insert_text(text)

            # Keep cursor on screen
            self.keep_cursor_visible()

            # While typing → cursor MUST be solid
            self.cursor_visible = True
            self.cursor_phase = 0.0     # brightest point of fade

            # Stop any blinking while typing
            self.stop_cursor_blink()

            # Blink will resume after user stops typing
            self.restart_blink_after_idle()

            # Redraw + update IME
            self.queue_draw()
            self.update_im_cursor_location()


    def restart_blink_after_idle(self):
        def idle_blink():
            self.start_cursor_blink()
            return False  # one-shot
        GLib.timeout_add(700, idle_blink)  # restart after 700ms idle




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
            self.renderer.wrap_enabled = not self.renderer.wrap_enabled

            # Save current cursor position
            saved_cursor_line = self.buf.cursor_line
            saved_cursor_col = self.buf.cursor_col

            # Clear wrap caches
            self.renderer.wrap_cache = {}
            self.renderer.visual_line_map = []
            self.renderer.total_visual_lines_cache = None
            self.renderer.visual_line_anchor = (0, 0)

            # Keep cursor at same logical line, adjust scroll position
            # Set scroll_line to cursor line (or slightly above to keep it visible)
            visible_lines = max(1, self.get_height() // self.renderer.line_h)
            self.scroll_line = max(0, saved_cursor_line - visible_lines // 2)
            self.scroll_visual_offset = 0

            if self.renderer.wrap_enabled:
                self.renderer.max_line_width = 0
                self.scroll_x = 0
                self.hadj.set_value(0)
                
                # Approximate visual line for scrollbar
                # We'll let keep_cursor_visible fine-tune it
                total_logical = self.buf.total()
                if total_logical > 0:
                    ratio = self.scroll_line / total_logical
                    # Use current upper as estimate (will be updated by update_scrollbar)
                    approx_vis = int(ratio * max(total_logical, self.vadj.get_upper()))
                    self.vadj.set_value(approx_vis)
                else:
                    self.vadj.set_value(0)
            else:
                # Force recalculation of max line width when disabling wrap
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                self.renderer.scan_for_max_width(cr, self.buf)
                
                # In unwrapped mode, vadj uses logical lines
                self.vadj.set_value(self.scroll_line)

            self.update_scrollbar()
            self.keep_cursor_visible()  # Ensure cursor stays visible after wrap toggle
            self.queue_draw()
            return True


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
        
        # Tab key - insert tab character
        if name == "Tab":
            self.buf.insert_text("\t")
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Editing keys
        if name == "BackSpace":
            self.buf.backspace()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Delete":
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
            self.update_scrollbar() 
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
                    self.keep_cursor_visible()
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
                                self.keep_cursor_visible()
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
        self._drag_pending = False

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
                self.keep_cursor_visible()
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

        # DOUBLE CLICK - Select word
        if self.click_count == 2:
            
            # Find word boundaries
            import re
            # Word characters include alphanumeric and underscore
            word_pattern = re.compile(r'\w')
            
            # Find start of word
            start_col = col
            while start_col > 0 and word_pattern.match(line_text[start_col - 1]):
                start_col -= 1
            
            # Find end of word
            end_col = col
            while end_col < line_len and word_pattern.match(line_text[end_col]):
                end_col += 1
            
            # If we didn't find a word, select the character at cursor
            if start_col == end_col:
                if col < line_len:
                    end_col = col + 1
                else:
                    start_col = max(0, col - 1)
            
            # Set selection
            self.buf.selection.set_start(ln, start_col)
            self.buf.selection.set_end(ln, end_col)
            self.buf.cursor_line = ln
            self.buf.cursor_col = end_col
            
            # Enable word selection mode for drag extension
            self.word_selection_mode = True
            
            # Store anchor word boundaries for word-by-word drag extension
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
                    # Use start or end based on direction
                    if ln > sel_end_line or (ln == sel_end_line and 0 >= sel_end_col):
                        # Dragging forward from end of selection
                        self.ctrl.update_drag(ln, 0)
                    else:
                        # Dragging backward from start of selection
                        self.ctrl.update_drag(ln, 0)
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
        """Keep cursor visible by scrolling if necessary."""
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
                # We need to find the logical line and visual offset for this visual line
                # But logical_to_visual_line returns a visual line index.
                # We need the reverse: visual_to_logical_line
                
                new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                    cr, self.buf, cursor_visual_line, ln_width, viewport_width
                )
                
                self.scroll_line = new_log
                self.scroll_visual_offset = new_vis_off
                self.vadj.set_value(cursor_visual_line)
                
            # Check if cursor is below visible area
            elif cursor_visual_line >= scroll_visual_line + visible_lines:
                # Scroll down so cursor is at bottom
                new_top_visual = cursor_visual_line - visible_lines + 1
                
                new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                    cr, self.buf, new_top_visual, ln_width, viewport_width
                )
                
                self.scroll_line = new_log
                self.scroll_visual_offset = new_vis_off
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
        
        elapsed = time.time() - draw_start
        # Update scrollbars after drawing (this updates visibility based on content)
        #GLib.idle_add(lambda: (self.update_scrollbar(), False))
        # Update scrollbars after drawing (this updates visibility based on content)
        #GLib.idle_add(lambda: (self.update_scrollbar(), False))









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
#   WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Text Editor")
        self.set_default_size(800, 600)
        
        # Track current encoding
        self.current_encoding = "utf-8"

        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.vscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.view.vadj)
        self.hscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.view.hadj)

        self.vscroll.add_css_class("overlay-scrollbar")
        self.hscroll.add_css_class("hscrollbar-overlay")
        self.vscroll.set_visible(False)
        self.hscroll.set_visible(False)


        # IMPORTANT: give the view references to both scrollbars
        self.view.vscroll = self.vscroll
        self.view.hscroll = self.hscroll

        self.buf.connect("changed", self.on_buffer_changed)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)
        
        # Add menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(self.create_menu())
        header.pack_end(menu_button)
        
        # Setup actions
        self.setup_actions()

        # Clean GTK4 layout: scrollbars OUTSIDE the text viewport
        grid = Gtk.Grid()
        grid.set_column_spacing(0)
        grid.set_row_spacing(0)

        # Text view occupies top-left cell
        grid.attach(self.view, 0, 0, 1, 1)

        # Vertical scrollbar on right
        self.vscroll.set_hexpand(False)
        self.vscroll.set_vexpand(True)
        grid.attach(self.vscroll, 1, 0, 1, 1)

        # Horizontal scrollbar at bottom
        self.hscroll.set_hexpand(True)
        self.hscroll.set_vexpand(False)
        grid.attach(self.hscroll, 0, 1, 1, 1)

        # Corner filler (bottom-right)
        corner = Gtk.Box()
        corner.set_size_request(12, 12)
        grid.attach(corner, 1, 1, 1, 1)
        # Match viewport/editor background
        grid.set_css_classes(["editor-surface"])
        # Put grid into main window
        layout.set_content(grid)
    
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
            GLib.Variant.new_string(self.current_encoding)
        )
        encoding_action.connect("activate", self.on_encoding_changed)
        self.add_action(encoding_action)
    
    def on_save_as(self, action, parameter):
        """Handle Save As menu action"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Save As")
        
        def done(dialog, result):
            try:
                f = dialog.save_finish(result)
            except:
                return
            path = f.get_path()
            self.save_file(path)
        
        dialog.save(self, None, done)
    
    def save_file(self, path):
        """Save the current buffer to a file with the current encoding"""
        try:
            total_lines = self.buf.total()
            lines = []
            for i in range(total_lines):
                lines.append(self.buf.get_line(i))
            
            content = "\n".join(lines)
            
            # Write with current encoding
            with open(path, "w", encoding=self.current_encoding) as f:
                f.write(content)
            
            self.set_title(os.path.basename(path))
            print(f"File saved as {path} with encoding {self.current_encoding}")
        except Exception as e:
            print(f"Error saving file: {e}")
    
    def on_encoding_changed(self, action, parameter):
        """Handle encoding selection from menu"""
        encoding = parameter.get_string()
        self.current_encoding = encoding
        action.set_state(parameter)
        
        print(f"Encoding changed to: {encoding} (will be used for next save)")
        # Note: We don't change self.buf.file.encoding because that would
        # re-decode the file with the wrong encoding, showing garbage.
        # The encoding change only affects how the file is saved.


    def on_buffer_changed(self, *_):
        self.view.queue_draw()

        width = self.view.get_width()
        height = self.view.get_height()

        if width <= 0 or height <= 0:
            GLib.idle_add(self.on_buffer_changed)
            return

        # Invalidate wrap cache when buffer changes
        if self.view.renderer.wrap_enabled:
            self.view.renderer.wrap_cache.clear()
            self.view.renderer.total_visual_lines_cache = None
        
        # Use update_scrollbar which handles both wrap and non-wrap modes correctly
        self.view.update_scrollbar()

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return
            path = f.get_path()
            
            loading_dialog = LoadingDialog(self)
            loading_dialog.present()
            
            idx = IndexedFile(path)
            
            def progress_callback(fraction):
                loading_dialog.update_progress(fraction)
                return False
            
            def index_complete():
                self.buf.load(idx)

                self.view.scroll_line = 0
                self.view.scroll_x = 0
                
                # Set current encoding to match the loaded file
                self.current_encoding = idx.encoding
                # Update the encoding action state
                encoding_action = self.lookup_action("encoding")
                if encoding_action:
                    encoding_action.set_state(GLib.Variant.new_string(self.current_encoding))
                
                # Trigger width scan for the new file
                self.view.file_loaded()

                # update scrollbars after loading new file
                #GLib.idle_add(lambda: (self.hscroll.update_visibility(),
                 #      self.vscroll.update_visibility(),
                  #     False))


                self.view.queue_draw()
                self.vscroll.queue_draw()
                self.hscroll.queue_draw()

                self.set_title(os.path.basename(path))
                loading_dialog.close()
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

        dialog.open(self, None, done)


# ============================================================
#   APP
# ============================================================

class VirtualTextEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.vite")

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS_OVERLAY_SCROLLBAR)

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    VirtualTextEditor().run(sys.argv)
