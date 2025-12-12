"""
Editor Widget - GTK4-based text editor using virtual buffer.

This module provides:
- TopEditor: Main editor container widget
- EditorView: Drawing area with text rendering and input handling
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GLib, Gio

from virtual_buffer import VirtualBuffer
from word_wrap import VisualLineMapper
from undo_redo import (
    UndoRedoManager, InsertCommand, DeleteCommand, 
    BatchCommand, Position, Selection
)
from typing import Optional, List, Tuple
import time


class EditorView(Gtk.DrawingArea):
    """
    Custom drawing area for text editing.
    
    Handles:
    - Text rendering with virtual scrolling
    - Word wrap display
    - Cursor and selection rendering
    - Keyboard and mouse input
    """
    
    def __init__(self):
        super().__init__()
        
        # Core components
        self.buffer = VirtualBuffer()
        self.undo_manager = UndoRedoManager(max_history=10000)
        self.wrap_mapper: Optional[VisualLineMapper] = None
        
        # Cursor and selection
        self.cursor = Position(0, 0)
        self.selection_start: Optional[Position] = None
        self.selection_end: Optional[Position] = None
        
        # View state
        self.scroll_offset: int = 0  # First visible visual line
        self.horizontal_offset: int = 0  # Horizontal scroll in characters
        self.viewport_lines: int = 30  # Visible lines
        self.line_height: float = 20.0
        self.char_width: float = 10.0
        self.gutter_width: float = 60.0
        
        # Word wrap
        self.word_wrap_enabled: bool = False
        
        # Scrollbar callback
        self._scrollbar_callback: Optional[callable] = None
        
        # Font
        self.font_desc = Pango.FontDescription.from_string("Monospace 11")
        
        # Colors
        self.colors = {
            'bg': (0.12, 0.12, 0.14, 1.0),
            'fg': (0.9, 0.9, 0.9, 1.0),
            'gutter_bg': (0.1, 0.1, 0.12, 1.0),
            'gutter_fg': (0.5, 0.5, 0.55, 1.0),
            'cursor': (1.0, 1.0, 1.0, 1.0),
            'selection': (0.2, 0.4, 0.7, 0.5),
            'current_line': (0.15, 0.15, 0.18, 1.0),
        }
        
        # Setup widget
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_vexpand(True)
        self.set_hexpand(True)
        
        # Drawing
        self.set_draw_func(self.on_draw)
        
        # Input controllers
        self._setup_input_controllers()
        
        # Cursor blink
        self.cursor_visible = True
        self.cursor_blink_id: Optional[int] = None
        self._start_cursor_blink()
        
        # Initialize wrap mapper
        self.wrap_mapper = VisualLineMapper(self.buffer)
    
    def _setup_input_controllers(self) -> None:
        """Setup keyboard and mouse input handlers."""
        # Keyboard
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_controller)
        
        # Mouse click
        click_controller = Gtk.GestureClick()
        click_controller.connect('pressed', self.on_click_pressed)
        self.add_controller(click_controller)
        
        # Mouse scroll
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_controller.connect('scroll', self.on_scroll)
        self.add_controller(scroll_controller)
        
        # Focus
        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect('enter', self.on_focus_enter)
        focus_controller.connect('leave', self.on_focus_leave)
        self.add_controller(focus_controller)
    
    def _start_cursor_blink(self) -> None:
        """Start cursor blink timer."""
        if self.cursor_blink_id:
            GLib.source_remove(self.cursor_blink_id)
        self.cursor_visible = True
        self.cursor_blink_id = GLib.timeout_add(530, self._on_cursor_blink)
    
    def _on_cursor_blink(self) -> bool:
        """Toggle cursor visibility."""
        self.cursor_visible = not self.cursor_visible
        self.queue_draw()
        return True
    
    def _reset_cursor_blink(self) -> None:
        """Reset cursor to visible and restart blink."""
        self.cursor_visible = True
        self._start_cursor_blink()
    
    def load_file(self, filepath: str) -> None:
        """Load a file into the editor."""
        self.buffer.load_file(filepath)
        self.undo_manager.clear()
        self.cursor = Position(0, 0)
        self.selection_start = None
        self.selection_end = None
        self.scroll_offset = 0
        
        # Update wrap mapper
        self.wrap_mapper = VisualLineMapper(self.buffer)
        self.wrap_mapper.enabled = self.word_wrap_enabled
        self._update_wrap_width()
        
        self.queue_draw()
    
    def load_text(self, text: str) -> None:
        """Load text directly into the editor."""
        self.buffer.load_text(text)
        self.undo_manager.clear()
        self.cursor = Position(0, 0)
        self.selection_start = None
        self.selection_end = None
        self.scroll_offset = 0
        
        self.wrap_mapper = VisualLineMapper(self.buffer)
        self.wrap_mapper.enabled = self.word_wrap_enabled
        self._update_wrap_width()
        
        self.queue_draw()
    
    def save_file(self, filepath: Optional[str] = None) -> None:
        """Save buffer to file."""
        self.buffer.save_to_file(filepath)
    
    def _update_wrap_width(self) -> None:
        """Update wrap mapper with current viewport width."""
        if self.wrap_mapper:
            width = self.get_width() - self.gutter_width - 20
            self.wrap_mapper.set_viewport_width(width, self.char_width)
    
    def toggle_word_wrap(self) -> None:
        """Toggle word wrap on/off."""
        self.word_wrap_enabled = not self.word_wrap_enabled
        if self.wrap_mapper:
            self.wrap_mapper.enabled = self.word_wrap_enabled
            self._update_wrap_width()
        self.horizontal_offset = 0  # Reset horizontal scroll
        self.ensure_cursor_visible()
        self._notify_scrollbar()
        self.queue_draw()
    
    def set_word_wrap(self, enabled: bool) -> None:
        """Set word wrap state."""
        if self.word_wrap_enabled != enabled:
            self.word_wrap_enabled = enabled
            if self.wrap_mapper:
                self.wrap_mapper.enabled = enabled
                self._update_wrap_width()
            self.horizontal_offset = 0
            self.ensure_cursor_visible()
            self._notify_scrollbar()
            self.queue_draw()
    
    def set_scrollbar_callback(self, callback: callable) -> None:
        """Set callback for scrollbar updates."""
        self._scrollbar_callback = callback
    
    def _notify_scrollbar(self) -> None:
        """Notify scrollbar callback of changes."""
        if self._scrollbar_callback:
            GLib.idle_add(self._scrollbar_callback)
    
    # ==================== Drawing ====================
    
    def on_draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        """Main draw function."""
        # Update dimensions
        self.viewport_lines = max(1, int(height / self.line_height))
        self._update_wrap_width()
        
        # Background
        cr.set_source_rgba(*self.colors['bg'])
        cr.paint()
        
        # Draw gutter
        self._draw_gutter(cr, height)
        
        # Draw text content
        self._draw_content(cr, width, height)
        
        # Draw cursor
        if self.cursor_visible and self.has_focus():
            self._draw_cursor(cr)
    
    def _draw_gutter(self, cr, height: int) -> None:
        """Draw line number gutter."""
        cr.set_source_rgba(*self.colors['gutter_bg'])
        cr.rectangle(0, 0, self.gutter_width, height)
        cr.fill()
        
        # Line numbers
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        
        cr.set_source_rgba(*self.colors['gutter_fg'])
        
        y = 0.0
        
        if self.word_wrap_enabled and self.wrap_mapper:
            # WORD WRAP MODE: iterate by logical lines
            logical_line = self.scroll_offset
            
            while y < height and logical_line < self.buffer.total_lines:
                segments = self.wrap_mapper.get_line_segments(logical_line)
                
                # Show line number only on first segment
                layout.set_text(str(logical_line + 1), -1)
                cr.move_to(self.gutter_width - 10 - layout.get_pixel_size()[0], y)
                PangoCairo.show_layout(cr, layout)
                
                # Skip remaining segments
                for _ in segments:
                    if y >= height:
                        break
                    y += self.line_height
                
                logical_line += 1
        else:
            # NO WRAP MODE
            logical_line = self.scroll_offset
            
            while y < height and logical_line < self.buffer.total_lines:
                layout.set_text(str(logical_line + 1), -1)
                cr.move_to(self.gutter_width - 10 - layout.get_pixel_size()[0], y)
                PangoCairo.show_layout(cr, layout)
                
                y += self.line_height
                logical_line += 1
    
    def _draw_content(self, cr, width: int, height: int) -> None:
        """Draw text content with lazy word wrap."""
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font_desc)
        
        # Get metrics
        metrics = layout.get_context().get_metrics(self.font_desc)
        self.char_width = metrics.get_approximate_char_width() / Pango.SCALE
        self.line_height = (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
        self.viewport_lines = int(height / self.line_height)
        
        x_start = self.gutter_width + 10
        y = 0.0
        
        if self.word_wrap_enabled and self.wrap_mapper:
            # WORD WRAP MODE: iterate by logical lines, render wrap segments
            logical_line = self.scroll_offset  # scroll_offset is in logical lines
            
            while y < height and logical_line < self.buffer.total_lines:
                line_text = self.buffer.get_line(logical_line)
                segments = self.wrap_mapper.get_line_segments(logical_line)
                
                # Draw each wrap segment of this logical line
                for seg_idx, (start_col, end_col) in enumerate(segments):
                    if y >= height:
                        break
                    
                    segment_text = line_text[start_col:end_col]
                    
                    # Draw current line highlight
                    if logical_line == self.cursor.line:
                        cr.set_source_rgba(*self.colors['current_line'])
                        cr.rectangle(self.gutter_width, y, width - self.gutter_width, self.line_height)
                        cr.fill()
                    
                    # Draw selection if any
                    self._draw_selection_for_line(cr, logical_line, start_col, end_col, x_start, y)
                    
                    # Draw text
                    cr.set_source_rgba(*self.colors['fg'])
                    layout.set_text(segment_text, -1)
                    cr.move_to(x_start, y)
                    PangoCairo.show_layout(cr, layout)
                    
                    y += self.line_height
                
                logical_line += 1
        else:
            # NO WRAP MODE: simple line-by-line rendering
            x_offset = self.horizontal_offset * self.char_width
            logical_line = self.scroll_offset
            
            while y < height and logical_line < self.buffer.total_lines:
                line_text = self.buffer.get_line(logical_line)
                
                # Draw current line highlight
                if logical_line == self.cursor.line:
                    cr.set_source_rgba(*self.colors['current_line'])
                    cr.rectangle(self.gutter_width, y, width - self.gutter_width, self.line_height)
                    cr.fill()
                
                # Draw selection if any
                self._draw_selection_for_line(cr, logical_line, 0, len(line_text), x_start - x_offset, y)
                
                # Draw text with horizontal offset
                cr.set_source_rgba(*self.colors['fg'])
                layout.set_text(line_text, -1)
                cr.move_to(x_start - x_offset, y)
                PangoCairo.show_layout(cr, layout)
                
                y += self.line_height
                logical_line += 1
    
    def _draw_selection_for_line(self, cr, line: int, col_start: int, col_end: int, 
                                  x_start: float, y: float) -> None:
        """Draw selection highlight for a line segment."""
        if not self.selection_start or not self.selection_end:
            return
        
        sel_start = self.selection_start
        sel_end = self.selection_end
        if sel_end < sel_start:
            sel_start, sel_end = sel_end, sel_start
        
        if line < sel_start.line or line > sel_end.line:
            return
        
        # Calculate selection bounds within this segment
        if line == sel_start.line:
            sel_col_start = max(col_start, sel_start.col)
        else:
            sel_col_start = col_start
        
        if line == sel_end.line:
            sel_col_end = min(col_end, sel_end.col)
        else:
            sel_col_end = col_end
        
        if sel_col_start >= sel_col_end:
            return
        
        # Draw selection rectangle
        x1 = x_start + (sel_col_start - col_start) * self.char_width
        x2 = x_start + (sel_col_end - col_start) * self.char_width
        
        cr.set_source_rgba(*self.colors['selection'])
        cr.rectangle(x1, y, x2 - x1, self.line_height)
        cr.fill()
    
    def _draw_cursor(self, cr) -> None:
        """Draw cursor at current position."""
        x_start = self.gutter_width + 10
        visible_height = self.viewport_lines * self.line_height
        
        if self.word_wrap_enabled and self.wrap_mapper:
            # WORD WRAP MODE: Calculate cursor screen position
            if self.cursor.line < self.scroll_offset:
                return  # Cursor above viewport
            
            # Quick check: if logical distance > viewport lines, it's definitely below
            if self.cursor.line > self.scroll_offset + self.viewport_lines + 2:
                return

            screen_y = 0.0
            for logical in range(self.scroll_offset, self.cursor.line):
                # Optimization: Stop if we are already past the visible area
                if screen_y >= visible_height:
                    return # Cursor below viewport
                
                if logical >= self.buffer.total_lines:
                    break
                    
                segments = self.wrap_mapper.get_line_segments(logical)
                screen_y += len(segments) * self.line_height
            
            # Now we are at the start of the cursor's logical line.
            # Find which visual segment contains the cursor column.
            segments = self.wrap_mapper.get_line_segments(self.cursor.line)
            line_text = self.buffer.get_line(self.cursor.line)
            
            target_seg_idx = 0
            start_col = 0
            end_col = 0
            
            # Find cursor segment
            found = False
            for i, (seg_start, seg_end) in enumerate(segments):
                # Cursor is in this segment if it's within [start, end)
                # OR if it's exactly at end AND this is the last segment (end of line)
                is_last_seg = (i == len(segments) - 1)
                
                if (self.cursor.col >= seg_start and self.cursor.col < seg_end) or \
                   (self.cursor.col == seg_end and is_last_seg):
                    target_seg_idx = i
                    start_col = seg_start
                    end_col = seg_end
                    found = True
                    break
            
            # Fallback if logic mismatch
            if not found and segments:
                target_seg_idx = len(segments) - 1
                start_col, end_col = segments[-1]
            
            screen_y += target_seg_idx * self.line_height
            
            if screen_y >= visible_height:
                return  # Cursor below viewport
            
            # Calculate precise X using Pango
            segment_text = line_text[start_col:end_col]
            char_offset = self.cursor.col - start_col
            char_offset = max(0, min(char_offset, len(segment_text)))
            
            byte_index = len(segment_text[:char_offset].encode('utf-8'))
            
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.font_desc)
            layout.set_text(segment_text, -1)
            
            pos = layout.index_to_pos(byte_index)
            # pos.x is in Pango units
            x = x_start + (pos.x / Pango.SCALE)
            
            cr.set_source_rgba(*self.colors['cursor'])
            cr.rectangle(x, screen_y, 2, self.line_height)
            cr.fill()
            
        else:
            # NO WRAP MODE
            screen_line = self.cursor.line - self.scroll_offset
            if 0 <= screen_line < self.viewport_lines:
                y = screen_line * self.line_height
                
                line_text = self.buffer.get_line(self.cursor.line)
                
                # Calculate X using Pango for current line
                byte_index = len(line_text[:self.cursor.col].encode('utf-8'))
                
                layout = PangoCairo.create_layout(cr)
                layout.set_font_description(self.font_desc)
                layout.set_text(line_text, -1)
                
                pos = layout.index_to_pos(byte_index)
                
                # Apply horizontal scrolling (char-based offset)
                x_scroll_px = self.horizontal_offset * self.char_width
                x = x_start + (pos.x / Pango.SCALE) - x_scroll_px
                
                cr.set_source_rgba(*self.colors['cursor'])
                cr.rectangle(x, y, 2, self.line_height)
                cr.fill()
    
    # ==================== Input Handling ====================
    
    def on_key_pressed(self, controller: Gtk.EventControllerKey,
                       keyval: int, keycode: int, state: Gdk.ModifierType) -> bool:
        """Handle keyboard input."""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        
        self._reset_cursor_blink()
        
        # Undo/Redo
        if ctrl and keyval == Gdk.KEY_z:
            if shift:
                self.redo()
            else:
                self.undo()
            return True
        
        if ctrl and keyval == Gdk.KEY_y:
            self.redo()
            return True
        
        # Word wrap toggle
        if ctrl and keyval == Gdk.KEY_w:
            self.toggle_word_wrap()
            return True
        
        # Navigation
        if keyval == Gdk.KEY_Left:
            self._move_cursor_left(shift)
            return True
        if keyval == Gdk.KEY_Right:
            self._move_cursor_right(shift)
            return True
        if keyval == Gdk.KEY_Up:
            self._move_cursor_up(shift)
            return True
        if keyval == Gdk.KEY_Down:
            self._move_cursor_down(shift)
            return True
        if keyval == Gdk.KEY_Home:
            self._move_cursor_home(ctrl, shift)
            return True
        if keyval == Gdk.KEY_End:
            self._move_cursor_end(ctrl, shift)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self._page_up(shift)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self._page_down(shift)
            return True
        
        # Editing
        if keyval == Gdk.KEY_BackSpace:
            self._backspace()
            return True
        if keyval == Gdk.KEY_Delete:
            self._delete()
            return True
        if keyval == Gdk.KEY_Return or keyval == Gdk.KEY_KP_Enter:
            self._insert_newline()
            return True
        if keyval == Gdk.KEY_Tab:
            self._insert_text("    ")  # 4 spaces
            return True
        
        # Regular character input
        char = Gdk.keyval_to_unicode(keyval)
        if char and char >= 32:  # Printable character
            self._insert_text(chr(char))
            return True
        
        return False
    
    def on_click_pressed(self, gesture: Gtk.GestureClick, 
                         n_press: int, x: float, y: float) -> None:
        """Handle mouse click."""
        self.grab_focus()
        self._reset_cursor_blink()
        
        # Convert click position to buffer position
        line, col = self._coords_to_position(x, y)
        self.cursor = Position(line, col)
        
        if n_press == 1:
            # Single click - move cursor
            self.selection_start = None
            self.selection_end = None
        elif n_press == 2:
            # Double click - select word
            self._select_word_at_cursor()
        elif n_press == 3:
            # Triple click - select line
            self._select_line_at_cursor()
        
        self.queue_draw()
    
    def on_scroll(self, controller: Gtk.EventControllerScroll,
                  dx: float, dy: float) -> bool:
        """Handle mouse wheel scroll."""
        scroll_lines = int(dy * 3)  # 3 lines per scroll step
        self.scroll_offset = max(0, min(
            self.scroll_offset + scroll_lines,
            max(0, self._get_total_visual_lines() - 1)
        ))
        self._notify_scrollbar()
        self.queue_draw()
        return True
    
    def on_focus_enter(self, controller: Gtk.EventControllerFocus) -> None:
        """Handle focus gain."""
        self._start_cursor_blink()
        self.queue_draw()
    
    def on_focus_leave(self, controller: Gtk.EventControllerFocus) -> None:
        """Handle focus loss."""
        if self.cursor_blink_id:
            GLib.source_remove(self.cursor_blink_id)
            self.cursor_blink_id = None
        self.cursor_visible = True
        self.queue_draw()
    
    # ==================== Cursor Movement ====================
    
    def _move_cursor_left(self, extend_selection: bool = False) -> None:
        """Move cursor left."""
        self._update_selection_start(extend_selection)
        
        if self.cursor.col > 0:
            self.cursor.col -= 1
        elif self.cursor.line > 0:
            self.cursor.line -= 1
            self.cursor.col = self.buffer.get_line_length(self.cursor.line)
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_right(self, extend_selection: bool = False) -> None:
        """Move cursor right."""
        self._update_selection_start(extend_selection)
        
        line_len = self.buffer.get_line_length(self.cursor.line)
        if self.cursor.col < line_len:
            self.cursor.col += 1
        elif self.cursor.line < self.buffer.total_lines - 1:
            self.cursor.line += 1
            self.cursor.col = 0
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_up(self, extend_selection: bool = False) -> None:
        """Move cursor up."""
        self._update_selection_start(extend_selection)
        
        if self.word_wrap_enabled and self.wrap_mapper:
            visual = self.wrap_mapper.logical_to_visual(self.cursor.line, self.cursor.col)
            if visual > 0:
                logical, _, col_start = self.wrap_mapper.visual_to_logical(visual - 1)
                self.cursor.line = logical
                # Try to maintain column position
                segments = self.wrap_mapper.get_line_segments(logical)
                wrap_offset = 0
                for i, (s, e) in enumerate(segments):
                    if visual - 1 >= self.wrap_mapper.logical_to_visual(logical) + i:
                        wrap_offset = i
                self.cursor.col = min(
                    col_start + (self.cursor.col - col_start if col_start else self.cursor.col),
                    self.buffer.get_line_length(logical)
                )
        else:
            if self.cursor.line > 0:
                self.cursor.line -= 1
                self.cursor.col = min(self.cursor.col, self.buffer.get_line_length(self.cursor.line))
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_down(self, extend_selection: bool = False) -> None:
        """Move cursor down."""
        self._update_selection_start(extend_selection)
        
        if self.word_wrap_enabled and self.wrap_mapper:
            visual = self.wrap_mapper.logical_to_visual(self.cursor.line, self.cursor.col)
            total = self._get_total_visual_lines()
            if visual < total - 1:
                logical, _, col_start = self.wrap_mapper.visual_to_logical(visual + 1)
                self.cursor.line = logical
                self.cursor.col = min(
                    col_start + self.cursor.col,
                    self.buffer.get_line_length(logical)
                )
        else:
            if self.cursor.line < self.buffer.total_lines - 1:
                self.cursor.line += 1
                self.cursor.col = min(self.cursor.col, self.buffer.get_line_length(self.cursor.line))
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_home(self, to_start: bool = False, extend_selection: bool = False) -> None:
        """Move cursor to start of line or document."""
        self._update_selection_start(extend_selection)
        
        if to_start:
            self.cursor.line = 0
            self.cursor.col = 0
        else:
            self.cursor.col = 0
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _move_cursor_end(self, to_end: bool = False, extend_selection: bool = False) -> None:
        """Move cursor to end of line or document."""
        self._update_selection_start(extend_selection)
        
        if to_end:
            self.cursor.line = max(0, self.buffer.total_lines - 1)
        self.cursor.col = self.buffer.get_line_length(self.cursor.line)
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _page_up(self, extend_selection: bool = False) -> None:
        """Page up."""
        self._update_selection_start(extend_selection)
        
        if self.word_wrap_enabled and self.wrap_mapper:
            visual = self.wrap_mapper.logical_to_visual(self.cursor.line, self.cursor.col)
            new_visual = max(0, visual - self.viewport_lines)
            logical, _, col_start = self.wrap_mapper.visual_to_logical(new_visual)
            self.cursor.line = logical
            self.cursor.col = min(col_start + self.cursor.col, self.buffer.get_line_length(logical))
        else:
            self.cursor.line = max(0, self.cursor.line - self.viewport_lines)
            self.cursor.col = min(self.cursor.col, self.buffer.get_line_length(self.cursor.line))
        
        self.scroll_offset = max(0, self.scroll_offset - self.viewport_lines)
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _page_down(self, extend_selection: bool = False) -> None:
        """Page down."""
        self._update_selection_start(extend_selection)
        
        total = self._get_total_visual_lines()
        
        if self.word_wrap_enabled and self.wrap_mapper:
            visual = self.wrap_mapper.logical_to_visual(self.cursor.line, self.cursor.col)
            new_visual = min(total - 1, visual + self.viewport_lines)
            logical, _, col_start = self.wrap_mapper.visual_to_logical(new_visual)
            self.cursor.line = logical
            self.cursor.col = min(col_start + self.cursor.col, self.buffer.get_line_length(logical))
        else:
            self.cursor.line = min(self.buffer.total_lines - 1, self.cursor.line + self.viewport_lines)
            self.cursor.col = min(self.cursor.col, self.buffer.get_line_length(self.cursor.line))
        
        self.scroll_offset = min(total - 1, self.scroll_offset + self.viewport_lines)
        
        self._update_selection_end(extend_selection)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    # ==================== Selection ====================
    
    def _update_selection_start(self, extend: bool) -> None:
        """Update selection start when extending selection."""
        if extend and not self.selection_start:
            self.selection_start = self.cursor.copy()
    
    def _update_selection_end(self, extend: bool) -> None:
        """Update selection end when extending selection."""
        if extend:
            self.selection_end = self.cursor.copy()
        else:
            self.selection_start = None
            self.selection_end = None
    
    def _select_word_at_cursor(self) -> None:
        """Select word at cursor position."""
        line_text = self.buffer.get_line(self.cursor.line)
        if not line_text:
            return
        
        # Find word boundaries
        start = self.cursor.col
        end = self.cursor.col
        
        while start > 0 and line_text[start - 1].isalnum():
            start -= 1
        while end < len(line_text) and line_text[end].isalnum():
            end += 1
        
        self.selection_start = Position(self.cursor.line, start)
        self.selection_end = Position(self.cursor.line, end)
        self.cursor.col = end
    
    def _select_line_at_cursor(self) -> None:
        """Select entire line at cursor."""
        self.selection_start = Position(self.cursor.line, 0)
        line_len = self.buffer.get_line_length(self.cursor.line)
        self.selection_end = Position(self.cursor.line, line_len)
        self.cursor.col = line_len
    
    def _get_selection(self) -> Optional[Selection]:
        """Get normalized selection (start < end)."""
        if not self.selection_start or not self.selection_end:
            return None
        
        start = self.selection_start
        end = self.selection_end
        if end < start:
            start, end = end, start
        
        return Selection(start, end)
    
    def _delete_selection(self) -> bool:
        """Delete selected text. Returns True if there was a selection."""
        selection = self._get_selection()
        if not selection:
            return False
        
        # Create delete command
        deleted_text = self.buffer.get_text_range(
            selection.start.line, selection.start.col,
            selection.end.line, selection.end.col
        )
        cmd = DeleteCommand(selection.start, selection.end, deleted_text)
        cmd.execute(self.buffer)
        self.undo_manager.push(cmd)
        
        self.cursor = selection.start.copy()
        self.selection_start = None
        self.selection_end = None
        
        self._invalidate_wrap(selection.start.line)
        return True
    
    # ==================== Editing ====================
    
    def _insert_text(self, text: str) -> None:
        """Insert text at cursor."""
        # Delete selection first if any
        self._delete_selection()
        
        # Create and execute insert command
        cmd = InsertCommand(self.cursor.copy(), text)
        end_pos = cmd.execute(self.buffer)
        self.undo_manager.push(cmd)
        
        self.cursor = end_pos
        self._invalidate_wrap(self.cursor.line)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _insert_newline(self) -> None:
        """Insert a newline."""
        self._insert_text('\n')
    
    def _backspace(self) -> None:
        """Delete character before cursor."""
        if self._delete_selection():
            self.ensure_cursor_visible()
            self.queue_draw()
            return
        
        if self.cursor.col > 0:
            # Delete character on same line
            start = Position(self.cursor.line, self.cursor.col - 1)
            end = self.cursor.copy()
            deleted = self.buffer.get_text_range(start.line, start.col, end.line, end.col)
            
            cmd = DeleteCommand(start, end, deleted)
            cmd.execute(self.buffer)
            self.undo_manager.push(cmd)
            
            self.cursor = start
        elif self.cursor.line > 0:
            # Merge with previous line
            prev_line_len = self.buffer.get_line_length(self.cursor.line - 1)
            start = Position(self.cursor.line - 1, prev_line_len)
            end = self.cursor.copy()
            
            cmd = DeleteCommand(start, end, '\n')
            cmd.execute(self.buffer)
            self.undo_manager.push(cmd)
            
            self.cursor = start
        
        self._invalidate_wrap(self.cursor.line)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    def _delete(self) -> None:
        """Delete character after cursor."""
        if self._delete_selection():
            self.ensure_cursor_visible()
            self.queue_draw()
            return
        
        line_len = self.buffer.get_line_length(self.cursor.line)
        
        if self.cursor.col < line_len:
            # Delete character on same line
            start = self.cursor.copy()
            end = Position(self.cursor.line, self.cursor.col + 1)
            deleted = self.buffer.get_text_range(start.line, start.col, end.line, end.col)
            
            cmd = DeleteCommand(start, end, deleted)
            cmd.execute(self.buffer)
            self.undo_manager.push(cmd)
        elif self.cursor.line < self.buffer.total_lines - 1:
            # Merge with next line
            start = self.cursor.copy()
            end = Position(self.cursor.line + 1, 0)
            
            cmd = DeleteCommand(start, end, '\n')
            cmd.execute(self.buffer)
            self.undo_manager.push(cmd)
        
        self._invalidate_wrap(self.cursor.line)
        self.ensure_cursor_visible()
        self.queue_draw()
    
    # ==================== Undo/Redo ====================
    
    def undo(self) -> None:
        """Undo last action and scroll to affected area."""
        pos = self.undo_manager.undo(self.buffer)
        if pos:
            self.cursor = pos
            self._invalidate_wrap(pos.line)
            self.ensure_cursor_visible()
            self.selection_start = None
            self.selection_end = None
            self.queue_draw()
    
    def redo(self) -> None:
        """Redo last undone action and scroll to affected area."""
        pos = self.undo_manager.redo(self.buffer)
        if pos:
            self.cursor = pos
            self._invalidate_wrap(pos.line)
            self.ensure_cursor_visible()
            self.selection_start = None
            self.selection_end = None
            self.queue_draw()
    
    # ==================== Utilities ====================
    
    def _invalidate_wrap(self, line: int) -> None:
        """Invalidate wrap info for a line and onwards."""
        if self.wrap_mapper:
            self.wrap_mapper.invalidate(line, self.buffer.total_lines - 1)
    
    def _get_total_visual_lines(self) -> int:
        """Get total lines for scrollbar (logical lines, not visual)."""
        # In logical-scroll mode, we use logical line count
        return self.buffer.total_lines
    
    def _coords_to_position(self, x: float, y: float) -> Tuple[int, int]:
        """Convert screen coordinates to buffer position using precise Pango hit-testing."""
        x_start = self.gutter_width + 10
        
        def get_col_from_layout(text: str, pixel_x: float) -> int:
            layout = self.create_pango_layout(text)
            layout.set_font_description(self.font_desc)
            
            # Convert to Pango units (scaled)
            pango_x = int(pixel_x * Pango.SCALE)
            
            # Use Pango's built-in hit testing
            _, index, trailing = layout.xy_to_index(pango_x, 0)
            
            # Index is byte offset in UTF-8. Convert to character offset.
            byte_prefix = text.encode('utf-8')[:index]
            char_offset = len(byte_prefix.decode('utf-8', errors='replace'))
            
            if trailing > 0:
                char_offset += 1
            return char_offset

        if self.word_wrap_enabled and self.wrap_mapper:
            # WORD WRAP MODE: Find which logical line and segment was clicked
            screen_y = 0.0
            logical_line = self.scroll_offset
            
            while logical_line < self.buffer.total_lines:
                segments = self.wrap_mapper.get_line_segments(logical_line)
                line_visual_height = len(segments) * self.line_height
                
                # Check if y is within this logical line structure
                if screen_y <= y < screen_y + line_visual_height:
                    # Found the logical line
                    # Now match the visual segment
                    seg_idx = int((y - screen_y) / self.line_height)
                    seg_idx = max(0, min(seg_idx, len(segments) - 1))
                    
                    start_col, end_col = segments[seg_idx]
                    
                    # Resolve column within this specific segment
                    line_text = self.buffer.get_line(logical_line)
                    segment_text = line_text[start_col:end_col]
                    
                    local_x = x - x_start
                    seg_col = get_col_from_layout(segment_text, local_x)
                    
                    col = start_col + seg_col
                    return (logical_line, min(col, end_col))
                
                screen_y += line_visual_height
                logical_line += 1
                
                # Optimization: passed the click Y
                if screen_y > y:
                    break
            
            # Past end of file
            last_line = max(0, self.buffer.total_lines - 1)
            return (last_line, self.buffer.get_line_length(last_line))
        else:
            # NO WRAP MODE
            screen_line = int(y / self.line_height)
            line = self.scroll_offset + screen_line
            
            if line < self.buffer.total_lines:
                line_text = self.buffer.get_line(line)
                
                # Calculate x relative to text start (accounting for scroll)
                x_offset = self.horizontal_offset * self.char_width
                local_x = x - x_start + x_offset
                
                col = get_col_from_layout(line_text, local_x)
                return (line, min(col, len(line_text)))
            else:
                last_line = max(0, self.buffer.total_lines - 1)
                return (last_line, self.buffer.get_line_length(last_line))
    
    def ensure_cursor_visible(self) -> None:
        """Scroll to make cursor visible."""
        # In logical-scroll mode, scroll_offset is in logical lines
        
        # Scroll up if cursor is above viewport
        if self.cursor.line < self.scroll_offset:
            self.scroll_offset = self.cursor.line
            self.scroll_offset = max(0, self.scroll_offset)
            return

        if self.word_wrap_enabled and self.wrap_mapper:
            # Optimization: jump if cursor is too far ahead to avoid O(N) loop
            if self.cursor.line > self.scroll_offset + self.viewport_lines + 2:
                # Center the cursor logic line in viewport if we jump (approx)
                self.scroll_offset = max(0, self.cursor.line - (self.viewport_lines // 2))
                return

            screen_y = 0.0
            for logical in range(self.scroll_offset, self.cursor.line + 1):
                if logical >= self.buffer.total_lines:
                    break
                segments = self.wrap_mapper.get_line_segments(logical)
                if logical == self.cursor.line:
                    # Add wrap offset for cursor position
                    wrap_offset, _ = self.wrap_mapper.column_to_visual_offset(
                        self.cursor.line, self.cursor.col
                    )
                    screen_y += (wrap_offset + 1) * self.line_height
                else:
                    screen_y += len(segments) * self.line_height
            
            # If cursor is below viewport, scroll up
            max_y = self.viewport_lines * self.line_height
            while screen_y > max_y and self.scroll_offset < self.cursor.line:
                # Remove first visible line's visual height
                segments = self.wrap_mapper.get_line_segments(self.scroll_offset)
                screen_y -= len(segments) * self.line_height
                self.scroll_offset += 1
        else:
            if self.cursor.line >= self.scroll_offset + self.viewport_lines:
                self.scroll_offset = self.cursor.line - self.viewport_lines + 1
        
        self.scroll_offset = max(0, self.scroll_offset)
    
    def scroll_to_line(self, line: int) -> None:
        """Scroll to show a specific line."""
        # Simply set scroll_offset to the line (centered if possible)
        self.scroll_offset = max(0, line - self.viewport_lines // 2)
        self.queue_draw()


class TopEditor(Gtk.Box):
    """
    Top-level editor widget container.
    
    Provides:
    - Scrollable text editing area with custom scrollbar
    - File loading/saving
    - Undo/redo access
    - Word wrap toggle
    """
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        
        # Main editing area
        self.editor_view = EditorView()
        
        # Horizontal layout for editor + scrollbar
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.set_vexpand(True)
        hbox.set_hexpand(True)
        
        # Editor view
        self.editor_view.set_hexpand(True)
        self.editor_view.set_vexpand(True)
        hbox.append(self.editor_view)
        
        # Vertical scrollbar
        self.vadj = Gtk.Adjustment(
            value=0,
            lower=0,
            upper=100,
            step_increment=1,
            page_increment=10,
            page_size=10
        )
        self.vscrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=self.vadj
        )
        self.vadj.connect('value-changed', self._on_scroll_changed)
        hbox.append(self.vscrollbar)
        
        # Horizontal scrollbar for non-wrap mode
        self.hadj = Gtk.Adjustment(
            value=0,
            lower=0,
            upper=1000,
            step_increment=10,
            page_increment=100,
            page_size=100
        )
        self.hscrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=self.hadj
        )
        self.hadj.connect('value-changed', self._on_hscroll_changed)
        
        self.append(hbox)
        self.append(self.hscrollbar)
        
        # Connect editor scroll to our scrollbar
        self.editor_view.set_scrollbar_callback(self._update_scrollbar)
        
        # File reference for save operations
        self.file: Optional[Gio.File] = None
        
        # Initial scrollbar update
        GLib.idle_add(self._update_scrollbar)
    
    def _on_scroll_changed(self, adj: Gtk.Adjustment) -> None:
        """Handle vertical scrollbar change."""
        self.editor_view.scroll_offset = int(adj.get_value())
        self.editor_view.queue_draw()
    
    def _on_hscroll_changed(self, adj: Gtk.Adjustment) -> None:
        """Handle horizontal scrollbar change."""
        self.editor_view.horizontal_offset = int(adj.get_value())
        self.editor_view.queue_draw()
    
    def _update_scrollbar(self) -> None:
        """Update scrollbar to match editor state."""
        total = self.editor_view._get_total_visual_lines()
        viewport = self.editor_view.viewport_lines
        
        self.vadj.set_lower(0)
        self.vadj.set_upper(max(1, total))
        self.vadj.set_page_size(viewport)
        self.vadj.set_step_increment(1)
        self.vadj.set_page_increment(viewport)
        
        # Don't trigger signal during update
        if int(self.vadj.get_value()) != self.editor_view.scroll_offset:
            self.vadj.set_value(self.editor_view.scroll_offset)
        
        # Update horizontal scrollbar visibility based on word wrap
        if self.editor_view.word_wrap_enabled:
            self.hscrollbar.set_visible(False)
        else:
            self.hscrollbar.set_visible(True)
            # Calculate max line width (estimate)
            max_width = 1000  # Default
            self.hadj.set_upper(max_width)
            self.hadj.set_page_size(
                (self.editor_view.get_width() - self.editor_view.gutter_width - 20) 
                / self.editor_view.char_width
            )
    
    def load_file(self, path: str) -> None:
        """Load a file into the editor."""
        self.editor_view.load_file(path)
        self.file = Gio.File.new_for_path(path)
        GLib.idle_add(self._update_scrollbar)
    
    def save_file(self) -> None:
        """Save the current buffer to file."""
        if self.file:
            self.editor_view.save_file(self.file.get_path())
    
    def toggle_word_wrap(self) -> None:
        """Toggle word wrap."""
        self.editor_view.toggle_word_wrap()
        GLib.idle_add(self._update_scrollbar)
    
    def undo(self) -> None:
        """Undo last action."""
        self.editor_view.undo()
        GLib.idle_add(self._update_scrollbar)
    
    def redo(self) -> None:
        """Redo last undone action."""
        self.editor_view.redo()
        GLib.idle_add(self._update_scrollbar)



