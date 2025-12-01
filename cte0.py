#!/usr/bin/env python3
# virtual_editor.py - Merged virtual text editor for huge files

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, PangoCairo, Gdk

import mmap
import os
import threading
import time


# ============= MappedFile =============
class MappedFile:
    def __init__(self, path, force_encoding=None):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        self.size = os.path.getsize(path)
        if self.size > 0:
            self.mm = mmap.mmap(self.fd, 0, access=mmap.ACCESS_READ)
        else:
            self.mm = None
        
        # Detect or use forced encoding
        if force_encoding:
            self.encoding = force_encoding
        else:
            self.encoding = self._detect_encoding()

    def _detect_encoding(self):
        """Detect file encoding from BOM or content"""
        if self.mm is None or self.size < 2:
            return 'utf-8'
        
        # Check for BOM
        if self.size >= 3:
            bom = self.mm[:4] if self.size >= 4 else self.mm[:3]
            
            # UTF-8 BOM (check this first as it's most specific)
            if bom[:3] == b'\xef\xbb\xbf':
                return 'utf-8-sig'
            # UTF-16 LE BOM
            if bom[:2] == b'\xff\xfe':
                return 'utf-16-le'
            # UTF-16 BE BOM
            elif bom[:2] == b'\xfe\xff':
                return 'utf-16-be'
        
        # Try to detect UTF-16 without BOM
        # For this, we look for the characteristic pattern of null bytes
        sample_size = min(4000, self.size)
        sample = self.mm[:sample_size]
        null_count = sample.count(b'\x00')
        
        # UTF-16 typically has ~50% null bytes for ASCII-range text
        # But we need to be conservative to avoid false positives
        if null_count > sample_size * 0.45:  # More than 45% null bytes
            # Check if nulls are in a regular pattern (every other byte)
            if len(sample) >= 100:
                even_nulls = sum(1 for i in range(0, min(100, len(sample)), 2) if sample[i:i+1] == b'\x00')
                odd_nulls = sum(1 for i in range(1, min(100, len(sample)), 2) if sample[i:i+1] == b'\x00')
                
                # Need a very strong pattern to classify as UTF-16
                if odd_nulls > 40 and even_nulls < 10:  # Nulls in odd positions -> UTF-16 LE
                    return 'utf-16-le'
                elif even_nulls > 40 and odd_nulls < 10:  # Nulls in even positions -> UTF-16 BE
                    return 'utf-16-be'
        
        # Default to UTF-8 (most common)
        return 'utf-8'

    def slice(self, start, end):
        if self.mm is None:
            return b""
        end = min(end, self.size)
        start = min(start, self.size)
        if start >= end:
            return b""
        return self.mm[start:end]

    def close(self):
        try:
            if self.mm:
                self.mm.close()
            os.close(self.fd)
        except:
            pass


# ============= LineIndex =============
class LineIndex:
    def __init__(self, mapped_file, chunk=500_000):  # Smaller chunks for better UI responsiveness
        self.mf = mapped_file
        self.chunk = chunk
        self.newlines = []
        self.finished = False
        self.callbacks = []
        self.last_emit = 0.0
        self.lock = threading.Lock()
        self.indexed_up_to = 0
        self.encoding = mapped_file.encoding

    def on_update(self, cb):
        self.callbacks.append(cb)

    def _notify(self):
        for cb in self.callbacks:
            GLib.idle_add(cb)

    def start(self):
        if self.mf.size == 0:
            self.finished = True
            return
        threading.Thread(target=self._run, daemon=True).start()

    def _find_newlines_in_block(self, block, base_offset):
        """Find newlines accounting for encoding"""
        temp_newlines = []
        
        if self.encoding in ('utf-16-le', 'utf-16-be'):
            # UTF-16: newline is \x0a\x00 (LE) or \x00\x0a (BE)
            if self.encoding == 'utf-16-le':
                pattern = b"\x0a\x00"
            else:
                pattern = b"\x00\x0a"
            
            start = 0
            while True:
                nl = block.find(pattern, start)
                if nl == -1:
                    break
                temp_newlines.append(base_offset + nl)
                start = nl + 2
        else:
            # UTF-8 and ASCII
            start = 0
            while True:
                nl = block.find(b"\n", start)
                if nl == -1:
                    break
                temp_newlines.append(base_offset + nl)
                start = nl + 1
        
        return temp_newlines

    def _run(self):
        size = self.mf.size
        pos = 0

        while pos < size:
            end = min(size, pos + self.chunk)
            block = self.mf.slice(pos, end)

            # Find newlines with encoding awareness
            temp_newlines = self._find_newlines_in_block(block, pos)

            # Update with lock
            with self.lock:
                self.newlines.extend(temp_newlines)
                self.indexed_up_to = end

            pos += self.chunk

            now = time.monotonic()
            if now - self.last_emit > 0.15:  # Longer interval between UI updates
                self._notify()
                self.last_emit = now
            
            # Yield to prevent CPU hogging
            time.sleep(0.005)  # Longer sleep

        with self.lock:
            self.finished = True
        self._notify()

    def line_count(self):
        with self.lock:
            return len(self.newlines) + 1

    def line_start_offset(self, line_no):
        with self.lock:
            if line_no == 0:
                # Skip BOM if present
                if self.encoding == 'utf-8-sig':
                    return 3  # UTF-8 BOM is 3 bytes
                elif self.encoding in ('utf-16-le', 'utf-16-be'):
                    return 2  # UTF-16 BOM is 2 bytes
                return 0
            if line_no - 1 < len(self.newlines):
                offset = self.newlines[line_no - 1]
                # Add appropriate newline size
                if self.encoding in ('utf-16-le', 'utf-16-be'):
                    return offset + 2
                return offset + 1
            return None
    
    def scan_lines_from(self, start_offset, max_lines=100):
        """Scan lines on-demand from a byte offset. Returns list of (line_text, byte_start, byte_end)"""
        lines = []
        pos = start_offset
        
        for _ in range(max_lines):
            if pos >= self.mf.size:
                break
            
            # Find next newline
            chunk_size = min(10000, self.mf.size - pos)
            chunk = self.mf.slice(pos, pos + chunk_size)
            
            nl_pos = chunk.find(b"\n")
            if nl_pos == -1:
                # No newline in chunk, this is last line or we need more data
                if pos + chunk_size >= self.mf.size:
                    # Last line
                    text = chunk.decode("utf-8", "replace")
                    lines.append((text, pos, self.mf.size))
                    break
                else:
                    # Line is longer than chunk, just take what we have
                    text = chunk.decode("utf-8", "replace")
                    lines.append((text, pos, pos + chunk_size))
                    pos += chunk_size
            else:
                # Found newline
                line_data = chunk[:nl_pos]
                text = line_data.decode("utf-8", "replace")
                lines.append((text, pos, pos + nl_pos))
                pos += nl_pos + 1
        
        return lines


# ============= Rope (for future edits) =============
LEAF_SIZE = 1024

class RopeLeaf:
    def __init__(self, text):
        self.text = text
        self.weight = len(text)

    def is_leaf(self):
        return True


class RopeNode:
    def __init__(self, left, right):
        self.left = left
        self.right = right
        self.weight = left.weight if left else 0
        self.recalc()

    def recalc(self):
        self.weight = (self.left.weight if self.left else 0)
        self.total = (
            (self.left.total if hasattr(self.left, "total") else self.left.weight if self.left else 0) +
            (self.right.total if hasattr(self.right, "total") else self.right.weight if self.right else 0)
        )

    def is_leaf(self):
        return False


def height(node):
    if node is None:
        return 0
    return getattr(node, "h", 1)


def update_height(node):
    node.h = max(height(node.left), height(node.right)) + 1


def balance_factor(node):
    return height(node.left) - height(node.right)


def rotate_right(y):
    x = y.left
    T = x.right
    x.right = y
    y.left = T
    y.recalc()
    x.recalc()
    update_height(y)
    update_height(x)
    return x


def rotate_left(x):
    y = x.right
    T = y.left
    y.left = x
    x.right = T
    x.recalc()
    y.recalc()
    update_height(x)
    update_height(y)
    return y


def balance(node):
    if node is None:
        return None
    update_height(node)
    bf = balance_factor(node)
    if bf > 1:
        if balance_factor(node.left) < 0:
            node.left = rotate_left(node.left)
        return rotate_right(node)
    if bf < -1:
        if balance_factor(node.right) > 0:
            node.right = rotate_right(node.right)
        return rotate_left(node)
    return node


def concat(a, b):
    if a is None:
        return b
    if b is None:
        return a
    n = RopeNode(a, b)
    update_height(n)
    return balance(n)


def split(node, index):
    if node is None:
        return None, None
    if node.is_leaf():
        left = node.text[:index]
        right = node.text[index:]
        return RopeLeaf(left) if left else None, RopeLeaf(right) if right else None
    if index < node.weight:
        left1, left2 = split(node.left, index)
        return left1, concat(left2, node.right)
    else:
        right1, right2 = split(node.right, index - node.weight)
        return concat(node.left, right1), right2


def flatten(node, out):
    if node is None:
        return
    if node.is_leaf():
        out.append(node.text)
    else:
        flatten(node.left, out)
        flatten(node.right, out)


class Rope:
    def __init__(self, initial=""):
        if initial:
            self.root = RopeLeaf(initial)
        else:
            self.root = None

    def __len__(self):
        if not self.root:
            return 0
        return getattr(self.root, "total", self.root.weight)

    def insert(self, index, text):
        left, right = split(self.root, index)
        new_leafs = []
        for i in range(0, len(text), LEAF_SIZE):
            new_leafs.append(RopeLeaf(text[i:i + LEAF_SIZE]))
        mid = None
        for leaf in new_leafs:
            mid = concat(mid, leaf)
        self.root = concat(concat(left, mid), right)

    def delete(self, index, length):
        left, rest = split(self.root, index)
        _, right = split(rest, length)
        self.root = concat(left, right)

    def get_text(self):
        out = []
        flatten(self.root, out)
        return "".join(out)


# ============= VirtualTextBuffer =============
class VirtualTextBuffer:
    def __init__(self, mapped_file, line_index):
        self.mf = mapped_file
        self.idx = line_index
        self.edits = {}  # line_no -> edited text
        self.dirty = False
        self.line_cache = {}  # line_no -> decoded text (LRU cache)
        self.max_cache_size = 1000  # Cache up to 1000 lines
        self.encoding = mapped_file.encoding
        
        # Adjust byte increment for UTF-16
        if self.encoding in ('utf-16-le', 'utf-16-be'):
            self.newline_size = 2
        else:
            self.newline_size = 1

    def line_count(self):
        # Use on-demand scanning if not fully indexed
        if not self.idx.finished:
            # Estimate based on file size and encoding
            bytes_per_line = 160 if self.encoding.startswith('utf-16') else 80
            return max(self.idx.line_count(), self.mf.size // bytes_per_line)
        return self.idx.line_count()

    def _decode_bytes(self, data):
        """Decode bytes to string with error handling"""
        if not data:
            return ""
        
        try:
            # First try the detected encoding
            return data.decode(self.encoding, errors='replace')
        except (UnicodeDecodeError, LookupError):
            # If that fails, try UTF-8
            try:
                return data.decode('utf-8', errors='replace')
            except:
                # Last resort: latin-1 (never fails)
                return data.decode('latin-1', errors='replace')

    def get_line(self, ln):
        # Check if this line has been edited
        if ln in self.edits:
            return self.edits[ln]
        
        # Check cache
        if ln in self.line_cache:
            return self.line_cache[ln]
        
        # Try to get from index
        start = self.idx.line_start_offset(ln)
        if start is not None:
            if ln + 1 < self.idx.line_count():
                end = self.idx.line_start_offset(ln + 1)
                if end is None:
                    end = self.mf.size
                else:
                    end = end - self.newline_size  # Skip newline bytes
            else:
                end = self.mf.size

            if start >= end:
                result = ""
            else:
                try:
                    data = self.mf.slice(start, end)
                    result = self._decode_bytes(data)
                except:
                    result = ""
            
            # Add to cache
            self.line_cache[ln] = result
            
            # Limit cache size (simple FIFO eviction)
            if len(self.line_cache) > self.max_cache_size:
                # Remove oldest entries (first 200)
                to_remove = list(self.line_cache.keys())[:200]
                for key in to_remove:
                    del self.line_cache[key]
            
            return result
        
        # If line not indexed yet, return empty
        return ""
    
    def get_lines_range(self, start_ln, count):
        """Get multiple lines efficiently"""
        lines = []
        for i in range(count):
            ln = start_ln + i
            if ln >= self.line_count():
                break
            lines.append(self.get_line(ln))
        return lines
    
    def set_line(self, ln, text):
        """Modify a line"""
        self.edits[ln] = text
        # Invalidate cache for this line
        if ln in self.line_cache:
            del self.line_cache[ln]
        self.dirty = True
    
    def insert_text_at_line(self, ln, col, text):
        """Insert text at a specific line and column"""
        line = self.get_line(ln)
        new_line = line[:col] + text + line[col:]
        self.set_line(ln, new_line)
    
    def delete_text_at_line(self, ln, start_col, end_col):
        """Delete text from a line"""
        line = self.get_line(ln)
        new_line = line[:start_col] + line[end_col:]
        self.set_line(ln, new_line)
    
    def save_to_file(self, path):
        """Save the buffer including edits to a file"""
        with open(path, 'w', encoding='utf-8') as f:
            for ln in range(self.line_count()):
                line = self.get_line(ln)
                f.write(line)
                if ln < self.line_count() - 1:
                    f.write('\n')


# ============= VirtualTextView =============
class VirtualTextView(Gtk.DrawingArea):
    def __init__(self, buffer):
        super().__init__()
        self.buf = buffer

        self.font = Pango.FontDescription("Monospace 11")
        self.line_height = 18
        self.char_width = 9  # Approximate for monospace
        self.start_line = 0
        self.content_height = 1000
        
        # Cursor position
        self.cursor_line = 0
        self.cursor_col = 0
        
        # Selection
        self.selection_start = None  # (line, col)
        self.selection_end = None
        
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_draw_func(self.on_draw)

        # Event controllers
        self.add_controller(self._scroll_controller())
        self.add_controller(self._key_controller())
        self.add_controller(self._click_controller())
        
        # Input method for text input
        self.im_context = Gtk.IMMulticontext()
        self.im_context.connect("commit", self._on_text_input)
        
        self.connect("map", self.on_map)
        self.vadj = None

    def set_content_height(self, height):
        self.content_height = height
        self.set_size_request(-1, height)

    def _scroll_controller(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        sc.connect("scroll", self._on_scroll)
        return sc
    
    def _key_controller(self):
        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self._on_key_pressed)
        return kc
    
    def _click_controller(self):
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        return click

    def _on_scroll(self, ctrl, dx, dy):
        if self.vadj:
            new_val = self.vadj.get_value() + dy * 40
            new_val = max(0, min(new_val, self.vadj.get_upper() - self.vadj.get_page_size()))
            self.vadj.set_value(new_val)
        return True
    
    def _on_click(self, gesture, n_press, x, y):
        """Handle mouse clicks to position cursor"""
        self.grab_focus()
        
        # Calculate which line was clicked
        line = self.start_line + int(y / self.line_height)
        
        # Calculate column (rough estimate based on char width)
        line_num_width = 8 * self.char_width  # "999999  " width
        text_x = x - line_num_width
        col = max(0, int(text_x / self.char_width))
        
        # Clamp to actual line length
        line_text = self.buf.get_line(line)
        col = min(col, len(line_text))
        
        self.cursor_line = line
        self.cursor_col = col
        self.selection_start = None
        self.selection_end = None
        
        self.queue_draw()
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard input"""
        # Get key name
        key = Gdk.keyval_name(keyval)
        
        # Check modifiers
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        
        # Navigation keys
        if key == "Up":
            self.cursor_line = max(0, self.cursor_line - 1)
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        elif key == "Down":
            self.cursor_line = min(self.buf.line_count() - 1, self.cursor_line + 1)
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        elif key == "Left":
            if self.cursor_col > 0:
                self.cursor_col -= 1
            elif self.cursor_line > 0:
                self.cursor_line -= 1
                self.cursor_col = len(self.buf.get_line(self.cursor_line))
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        elif key == "Right":
            line = self.buf.get_line(self.cursor_line)
            if self.cursor_col < len(line):
                self.cursor_col += 1
            elif self.cursor_line < self.buf.line_count() - 1:
                self.cursor_line += 1
                self.cursor_col = 0
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        elif key == "Home":
            self.cursor_col = 0
            self.queue_draw()
            return True
        elif key == "End":
            line = self.buf.get_line(self.cursor_line)
            self.cursor_col = len(line)
            self.queue_draw()
            return True
        elif key == "Page_Up":
            visible_lines = int(self.get_height() / self.line_height)
            self.cursor_line = max(0, self.cursor_line - visible_lines)
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        elif key == "Page_Down":
            visible_lines = int(self.get_height() / self.line_height)
            self.cursor_line = min(self.buf.line_count() - 1, self.cursor_line + visible_lines)
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        
        # Editing keys
        elif key == "BackSpace":
            if self.cursor_col > 0:
                self.buf.delete_text_at_line(self.cursor_line, self.cursor_col - 1, self.cursor_col)
                self.cursor_col -= 1
            elif self.cursor_line > 0:
                # Join with previous line
                prev_line = self.buf.get_line(self.cursor_line - 1)
                curr_line = self.buf.get_line(self.cursor_line)
                self.cursor_col = len(prev_line)
                self.buf.set_line(self.cursor_line - 1, prev_line + curr_line)
                # Remove current line (simplified - just clear it)
                self.buf.set_line(self.cursor_line, "")
                self.cursor_line -= 1
            self.queue_draw()
            return True
        elif key == "Delete":
            line = self.buf.get_line(self.cursor_line)
            if self.cursor_col < len(line):
                self.buf.delete_text_at_line(self.cursor_line, self.cursor_col, self.cursor_col + 1)
            self.queue_draw()
            return True
        elif key == "Return" or key == "KP_Enter":
            # Split line at cursor
            line = self.buf.get_line(self.cursor_line)
            left = line[:self.cursor_col]
            right = line[self.cursor_col:]
            self.buf.set_line(self.cursor_line, left)
            self.cursor_line += 1
            self.cursor_col = 0
            self.buf.set_line(self.cursor_line, right)
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        
        # Let IM context handle printable characters
        if self.im_context.filter_keypress(controller.get_current_event()):
            return True
        
        return False
    
    def _on_text_input(self, im_context, text):
        """Handle text input from IM context"""
        self.buf.insert_text_at_line(self.cursor_line, self.cursor_col, text)
        self.cursor_col += len(text)
        self.queue_draw()
    
    def _ensure_cursor_visible(self):
        """Scroll to make cursor visible"""
        if not self.vadj:
            return
        
        cursor_y = (self.cursor_line - self.start_line) * self.line_height
        viewport_height = self.get_height()
        
        if cursor_y < 0:
            # Cursor above viewport
            new_start = self.cursor_line
            self.vadj.set_value(new_start * self.line_height)
        elif cursor_y > viewport_height - self.line_height:
            # Cursor below viewport
            new_start = self.cursor_line - int(viewport_height / self.line_height) + 1
            self.vadj.set_value(new_start * self.line_height)

    def on_map(self, *args):
        parent = self.get_parent()
        if isinstance(parent, Gtk.ScrolledWindow):
            self.vadj = parent.get_vadjustment()
            self.vadj.connect("value-changed", self._adj_changed)
            # Set initial upper bound
            self.vadj.set_upper(self.content_height)

    def _adj_changed(self, adj):
        new_start = int(adj.get_value() // self.line_height)
        if new_start != self.start_line:
            self.start_line = new_start
            self.queue_draw()

    def on_draw(self, area, cr, w, h):
        # White background
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        
        # Create layout for text
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        # Calculate visible lines (don't cap it - we need to draw all visible lines)
        visible = (h // self.line_height) + 2
        y = 0
        
        # Get line count once
        total_lines = self.buf.line_count()

        for i in range(visible):
            ln = self.start_line + i
            if ln >= total_lines:
                break
            
            try:
                line = self.buf.get_line(ln)
                
                # Truncate very long lines for display
                if len(line) > 500:
                    line = line[:500] + "..."
                
                # Draw line number (gray)
                cr.set_source_rgb(0.5, 0.5, 0.5)
                layout.set_text(f"{ln+1:6d}")
                cr.move_to(0, y)
                PangoCairo.show_layout(cr, layout)
                
                # Draw line text (black)
                cr.set_source_rgb(0, 0, 0)
                layout.set_text(f"  {line}")
                cr.move_to(8 * self.char_width, y)
                PangoCairo.show_layout(cr, layout)
                
                # Draw cursor if on this line
                if ln == self.cursor_line and self.has_focus():
                    cursor_x = 8 * self.char_width + min(self.cursor_col, len(line)) * self.char_width
                    cursor_y = y
                    
                    cr.set_source_rgb(0, 0, 0)
                    cr.set_line_width(2)
                    cr.move_to(cursor_x, cursor_y)
                    cr.line_to(cursor_x, cursor_y + self.line_height)
                    cr.stroke()
            except Exception as e:
                # If we can't render a line, just skip it
                pass
            
            y += self.line_height


# ============= EditorWindow =============
class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Editor - Huge File Viewer & Editor")
        self.set_default_size(1000, 700)

        header = Adw.HeaderBar()
        
        # Open button
        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.on_open)
        header.pack_start(open_btn)
        
        # Save button
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.connect("clicked", self.on_save)
        self.save_btn.set_sensitive(False)
        header.pack_start(self.save_btn)

        # Status label
        self.status_label = Gtk.Label(label="No file loaded")
        header.pack_end(self.status_label)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.append(header)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_vexpand(True)
        self.scroller.set_hexpand(True)
        self.box.append(self.scroller)

        self.set_content(self.box)

        self.mf = None
        self.idx = None
        self.buf = None
        self.view = None
        self.current_path = None

    def on_open(self, btn):
        dlg = Gtk.FileDialog()
        dlg.open(self, None, self._file_selected)

    def _file_selected(self, dlg, res):
        try:
            file = dlg.open_finish(res)
        except:
            return

        path = file.get_path()
        self.load_file(path)
    
    def on_save(self, btn):
        if not self.buf or not self.current_path:
            return
        
        # Save to a .new file first, then rename
        new_path = self.current_path + ".new"
        try:
            self.buf.save_to_file(new_path)
            
            # Backup original
            backup_path = self.current_path + ".backup"
            if os.path.exists(self.current_path):
                os.replace(self.current_path, backup_path)
            
            # Move new file to original location
            os.replace(new_path, self.current_path)
            
            self.buf.dirty = False
            self.status_label.set_text(f"Saved: {os.path.basename(self.current_path)}")
            
        except Exception as e:
            self.status_label.set_text(f"Error saving: {e}")

    def load_file(self, path):
        # Clean up previous file
        if self.mf:
            self.mf.close()

        self.current_path = path

        # Load new file
        self.mf = MappedFile(path)
        
        # Update status immediately
        size_mb = self.mf.size / (1024 * 1024)
        self.status_label.set_text(f"Opening: {os.path.basename(path)} ({size_mb:.1f} MB, {self.mf.encoding})")
        
        # Create index and buffer
        self.idx = LineIndex(self.mf)
        self.buf = VirtualTextBuffer(self.mf, self.idx)

        # Create view immediately
        self.view = VirtualTextView(self.buf)
        self.scroller.set_child(self.view)

        # Use VERY conservative initial height estimate to avoid slowdown
        # Start with just 100 lines worth of height
        initial_height = 100 * self.view.line_height
        self.view.set_content_height(initial_height)

        # Enable save button
        self.save_btn.set_sensitive(True)

        # Set up update callback
        self.idx.on_update(self._on_index_update)
        
        # Start indexing in background (non-blocking)
        GLib.idle_add(self.idx.start)
        
        # Grab focus so user can start editing
        GLib.timeout_add(50, lambda: self.view.grab_focus() or False)

    def _on_index_update(self):
        if not self.view or not self.buf:
            return

        # Throttle updates - only update every few notifications
        if not hasattr(self, '_update_counter'):
            self._update_counter = 0
        
        self._update_counter += 1
        
        # Only update UI every 3rd callback to reduce overhead
        if self._update_counter % 3 != 0 and not self.idx.finished:
            return

        # Update height based on current line count
        total = self.buf.line_count() * self.view.line_height
        
        # Only update if significantly different
        if abs(total - self.view.content_height) > 1000:
            self.view.set_content_height(total)
            
            if self.view.vadj:
                self.view.vadj.set_upper(total)

        # Update status
        if self.idx.finished:
            size_mb = self.mf.size / (1024 * 1024)
            dirty_str = " (modified)" if self.buf.dirty else ""
            self.status_label.set_text(
                f"{os.path.basename(self.mf.path)} - {self.buf.line_count():,} lines ({size_mb:.1f} MB, {self.mf.encoding}){dirty_str}"
            )
        else:
            # Show progress
            progress_pct = (self.idx.indexed_up_to / self.mf.size) * 100 if self.mf.size > 0 else 0
            self.status_label.set_text(
                f"Indexing... {progress_pct:.0f}% - {self.buf.line_count():,} lines"
            )

        # Only redraw if we're near the visible area or indexing is complete
        if self.idx.finished or self.view.start_line < self.buf.line_count():
            self.view.queue_draw()


# ============= Application =============
class EditorApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="xyz.virtual.editor",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self):
        win = EditorWindow(self)
        win.present()


# ============= Main =============
if __name__ == "__main__":
    import sys
    
    Adw.init()
    app = EditorApp()
    
    # If a file is passed as argument, open it
    if len(sys.argv) > 1:
        def open_file(app):
            win = app.get_active_window()
            if win and os.path.exists(sys.argv[1]):
                win.load_file(sys.argv[1])
        app.connect("activate", open_file)
    
    app.run(sys.argv[:1])
