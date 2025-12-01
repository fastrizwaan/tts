#!/usr/bin/env python3
# virtual_editor.py - Lazy-loading virtual text editor for huge files

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
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        self.size = os.path.getsize(path)
        if self.size > 0:
            self.mm = mmap.mmap(self.fd, 0, access=mmap.ACCESS_READ)
        else:
            self.mm = None
        
        # Always use UTF-8 for simplicity - can add encoding detection later
        self.encoding = 'utf-8'

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


# ============= LazyLineIndex =============
class LazyLineIndex:
    """Lazy line indexer - only indexes what's needed"""
    def __init__(self, mapped_file):
        self.mf = mapped_file
        self.line_offsets = [0]  # Start of each line
        self.indexed_up_to = 0  # Byte position we've indexed up to
        self.lock = threading.Lock()
        self.avg_line_length = 80  # Initial estimate
        self.sample_lines = 0
        
    def _index_chunk(self, start, size):
        """Index a chunk of the file"""
        if start >= self.mf.size:
            return
        
        end = min(start + size, self.mf.size)
        chunk = self.mf.slice(start, end)
        
        pos = start
        line_starts = []
        i = 0
        
        while i < len(chunk):
            if chunk[i:i+1] == b'\n':
                line_starts.append(pos + i + 1)
            i += 1
        
        with self.lock:
            self.line_offsets.extend(line_starts)
            self.indexed_up_to = end
            
            # Update average line length
            if line_starts:
                self.sample_lines += len(line_starts)
                self.avg_line_length = self.indexed_up_to // max(1, self.sample_lines)
    
    def ensure_indexed_to_line(self, line_no):
        """Ensure we have indexed up to at least this line number"""
        with self.lock:
            current_lines = len(self.line_offsets) - 1
        
        if current_lines >= line_no:
            return
        
        # Index more data
        chunk_size = 1_000_000  # 1MB chunks
        while True:
            with self.lock:
                current_lines = len(self.line_offsets) - 1
                if current_lines >= line_no or self.indexed_up_to >= self.mf.size:
                    break
                start = self.indexed_up_to
            
            self._index_chunk(start, chunk_size)
    
    def ensure_indexed_to_byte(self, byte_pos):
        """Ensure we have indexed up to at least this byte position"""
        with self.lock:
            if self.indexed_up_to >= byte_pos:
                return
            start = self.indexed_up_to
        
        chunk_size = 1_000_000
        while True:
            with self.lock:
                if self.indexed_up_to >= byte_pos or self.indexed_up_to >= self.mf.size:
                    break
                start = self.indexed_up_to
            
            self._index_chunk(start, chunk_size)
    
    def estimate_total_lines(self):
        """Estimate total line count based on file size and average line length"""
        with self.lock:
            if self.indexed_up_to >= self.mf.size:
                return len(self.line_offsets) - 1
            
            # Estimate remaining lines
            indexed_lines = len(self.line_offsets) - 1
            remaining_bytes = self.mf.size - self.indexed_up_to
            estimated_remaining = remaining_bytes // max(1, self.avg_line_length)
            
            return indexed_lines + estimated_remaining
    
    def get_line_offset(self, line_no):
        """Get byte offset for a line number"""
        self.ensure_indexed_to_line(line_no + 1)
        
        with self.lock:
            if line_no >= len(self.line_offsets):
                return None
            return self.line_offsets[line_no]
    
    def find_line_for_byte(self, byte_pos):
        """Find which line contains a byte position"""
        self.ensure_indexed_to_byte(byte_pos)
        
        with self.lock:
            # Binary search
            left, right = 0, len(self.line_offsets) - 1
            while left < right:
                mid = (left + right + 1) // 2
                if self.line_offsets[mid] <= byte_pos:
                    left = mid
                else:
                    right = mid - 1
            return left


# ============= VirtualTextBuffer =============
class VirtualTextBuffer:
    def __init__(self, mapped_file, line_index):
        self.mf = mapped_file
        self.idx = line_index
        self.edits = {}  # line_no -> edited text
        self.dirty = False
        self.line_cache = {}  # line_no -> decoded text
        self.max_cache_size = 2000

    def estimate_line_count(self):
        return self.idx.estimate_total_lines()

    def get_line(self, ln):
        # Check edits first
        if ln in self.edits:
            return self.edits[ln]
        
        # Check cache
        if ln in self.line_cache:
            return self.line_cache[ln]
        
        # Get from file
        start = self.idx.get_line_offset(ln)
        if start is None:
            return ""
        
        end = self.idx.get_line_offset(ln + 1)
        if end is None:
            end = self.mf.size
        else:
            end = end - 1  # Don't include newline
        
        if start >= end:
            result = ""
        else:
            try:
                data = self.mf.slice(start, end)
                result = data.decode('utf-8', errors='replace')
            except:
                result = ""
        
        # Cache it
        self.line_cache[ln] = result
        
        # Limit cache
        if len(self.line_cache) > self.max_cache_size:
            # Remove random 500 entries
            to_remove = list(self.line_cache.keys())[:500]
            for key in to_remove:
                del self.line_cache[key]
        
        return result
    
    def set_line(self, ln, text):
        self.edits[ln] = text
        if ln in self.line_cache:
            del self.line_cache[ln]
        self.dirty = True
    
    def insert_text_at_line(self, ln, col, text):
        line = self.get_line(ln)
        new_line = line[:col] + text + line[col:]
        self.set_line(ln, new_line)
    
    def delete_text_at_line(self, ln, start_col, end_col):
        line = self.get_line(ln)
        new_line = line[:start_col] + line[end_col:]
        self.set_line(ln, new_line)


# ============= VirtualTextView =============
class VirtualTextView(Gtk.DrawingArea):
    def __init__(self, buffer):
        super().__init__()
        self.buf = buffer

        self.font = Pango.FontDescription("Monospace 10")
        self.line_height = 16
        self.char_width = 8
        self.start_line = 0
        
        self.cursor_line = 0
        self.cursor_col = 0
        
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_draw_func(self.on_draw)

        self.add_controller(self._scroll_controller())
        self.add_controller(self._key_controller())
        self.add_controller(self._click_controller())
        
        self.im_context = Gtk.IMMulticontext()
        self.im_context.connect("commit", self._on_text_input)
        
        self.connect("map", self.on_map)
        self.vadj = None

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
            new_val = self.vadj.get_value() + dy * 3 * self.line_height
            new_val = max(0, min(new_val, self.vadj.get_upper() - self.vadj.get_page_size()))
            self.vadj.set_value(new_val)
        return True
    
    def _on_click(self, gesture, n_press, x, y):
        self.grab_focus()
        
        line = self.start_line + int(y / self.line_height)
        line_num_width = 8 * self.char_width
        text_x = x - line_num_width
        col = max(0, int(text_x / self.char_width))
        
        line_text = self.buf.get_line(line)
        col = min(col, len(line_text))
        
        self.cursor_line = line
        self.cursor_col = col
        
        self.queue_draw()
    
    def _on_key_pressed(self, controller, keyval, keycode, state):
        key = Gdk.keyval_name(keyval)
        
        if key == "Up":
            self.cursor_line = max(0, self.cursor_line - 1)
            self._ensure_cursor_visible()
            self.queue_draw()
            return True
        elif key == "Down":
            self.cursor_line = min(self.buf.estimate_line_count() - 1, self.cursor_line + 1)
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
            elif self.cursor_line < self.buf.estimate_line_count() - 1:
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
            self.start_line = max(0, self.start_line - visible_lines)  # Scroll view
            if self.vadj:
                self.vadj.set_value(self.start_line * self.line_height)
            self.queue_draw()
            return True
        elif key == "Page_Down":
            visible_lines = int(self.get_height() / self.line_height)
            self.cursor_line = min(self.buf.estimate_line_count() - 1, self.cursor_line + visible_lines)
            self.start_line = min(self.buf.estimate_line_count() - visible_lines, self.start_line + visible_lines)  # Scroll view
            if self.vadj:
                self.vadj.set_value(self.start_line * self.line_height)
            self.queue_draw()
            return True
        elif key == "BackSpace":
            if self.cursor_col > 0:
                self.buf.delete_text_at_line(self.cursor_line, self.cursor_col - 1, self.cursor_col)
                self.cursor_col -= 1
            self.queue_draw()
            return True
        elif key == "Delete":
            line = self.buf.get_line(self.cursor_line)
            if self.cursor_col < len(line):
                self.buf.delete_text_at_line(self.cursor_line, self.cursor_col, self.cursor_col + 1)
            self.queue_draw()
            return True
        elif key == "Return" or key == "KP_Enter":
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
        
        if self.im_context.filter_keypress(controller.get_current_event()):
            return True
        
        return False
    
    def _on_text_input(self, im_context, text):
        self.buf.insert_text_at_line(self.cursor_line, self.cursor_col, text)
        self.cursor_col += len(text)
        self.queue_draw()
    
    def _ensure_cursor_visible(self):
        if not self.vadj:
            return
        
        cursor_y = self.cursor_line * self.line_height
        viewport_top = self.vadj.get_value()
        viewport_bottom = viewport_top + self.vadj.get_page_size()
        
        if cursor_y < viewport_top:
            self.vadj.set_value(cursor_y)
        elif cursor_y + self.line_height > viewport_bottom:
            self.vadj.set_value(cursor_y + self.line_height - self.vadj.get_page_size())

    def on_map(self, *args):
        parent = self.get_parent()
        if isinstance(parent, Gtk.ScrolledWindow):
            self.vadj = parent.get_vadjustment()
            self.vadj.connect("value-changed", self._adj_changed)
            
            # Set content height based on estimated lines
            estimated_lines = self.buf.estimate_line_count()
            total_height = estimated_lines * self.line_height
            self.vadj.set_upper(total_height)
            self.vadj.set_page_size(self.get_height())
            self.vadj.set_step_increment(self.line_height * 3)
            self.vadj.set_page_increment(self.get_height())
            
            # Important: set the size request so scrolling works
            self.set_size_request(-1, total_height)

    def _adj_changed(self, adj):
        new_start = int(adj.get_value() // self.line_height)
        if new_start != self.start_line:
            self.start_line = new_start
            self.queue_draw()

    def on_draw(self, area, cr, w, h):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        visible = (h // self.line_height) + 2
        y = 0
        
        estimated_total = self.buf.estimate_line_count()

        for i in range(visible):
            ln = self.start_line + i
            if ln >= estimated_total:
                break
            
            try:
                line = self.buf.get_line(ln)
                
                # Truncate long lines
                if len(line) > 300:
                    line = line[:300] + "..."
                
                # Line number
                cr.set_source_rgb(0.5, 0.5, 0.5)
                layout.set_text(f"{ln+1:6d}")
                cr.move_to(0, y)
                PangoCairo.show_layout(cr, layout)
                
                # Line text
                cr.set_source_rgb(0, 0, 0)
                layout.set_text(f"  {line}")
                cr.move_to(8 * self.char_width, y)
                PangoCairo.show_layout(cr, layout)
                
                # Cursor
                if ln == self.cursor_line and self.has_focus():
                    cursor_x = 8 * self.char_width + min(self.cursor_col, len(line)) * self.char_width
                    cursor_y = y
                    
                    cr.set_source_rgb(0, 0, 0)
                    cr.set_line_width(2)
                    cr.move_to(cursor_x, cursor_y)
                    cr.line_to(cursor_x, cursor_y + self.line_height)
                    cr.stroke()
            except:
                pass
            
            y += self.line_height


# ============= EditorWindow =============
class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Editor - Huge File Editor")
        self.set_default_size(1000, 700)

        header = Adw.HeaderBar()
        
        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.on_open)
        header.pack_start(open_btn)
        
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

    def load_file(self, path):
        if self.mf:
            self.mf.close()

        self.mf = MappedFile(path)
        size_mb = self.mf.size / (1024 * 1024)
        
        self.idx = LazyLineIndex(self.mf)
        self.buf = VirtualTextBuffer(self.mf, self.idx)
        
        self.view = VirtualTextView(self.buf)
        self.scroller.set_child(self.view)
        
        estimated = self.buf.estimate_line_count()
        
        # Update scrollbar after view is added
        def setup_scroll():
            if self.view.vadj:
                total_height = estimated * self.view.line_height
                self.view.vadj.set_upper(total_height)
                self.view.vadj.set_page_size(self.scroller.get_height())
                self.view.vadj.set_step_increment(self.view.line_height * 3)
                self.view.vadj.set_page_increment(self.scroller.get_height())
            return False
        
        GLib.timeout_add(50, setup_scroll)
        
        self.status_label.set_text(
            f"{os.path.basename(path)} - ~{estimated:,} lines ({size_mb:.1f} MB)"
        )
        
        GLib.timeout_add(100, lambda: self.view.grab_focus() or False)


# ============= Application =============
class EditorApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.fastrizwaan.virted",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )

    def do_activate(self):
        win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    import sys
    
    Adw.init()
    app = EditorApp()
    
    if len(sys.argv) > 1:
        def open_file(app):
            win = app.get_active_window()
            if win and os.path.exists(sys.argv[1]):
                win.load_file(sys.argv[1])
        app.connect("activate", open_file)
    
    app.run(sys.argv[:1])
