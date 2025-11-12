

#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, Pango, Gdk, PangoCairo, GObject
import threading
import time
import os
import re
import cairo
import copy

# --- VirtualTextBuffer with undo/redo using multi_replace ---
# (The VirtualTextBuffer class remains unchanged from the previous code block)
class VirtualTextBuffer:
    """Virtual text buffer that can handle millions of lines efficiently"""
    def __init__(self):
        self.lines = [""]
        self.total_lines = 1
        self.line_height = 20 # Will be calculated dynamically
        self.char_width = 8 # Will be calculated dynamically
        self.modified = False
        # --- New: Track file path and word wrap state ---
        self.file_path = None
        self.word_wrap = False # Default to no word wrap
        self.undo_stack = []
        self.redo_stack = []
    def _push_command(self, command):
        self.undo_stack.append(command)
        self.redo_stack = []  # Clear redo on new action
    def load_lines(self, lines_data):
        """Load lines from data (list or generator)"""
        if isinstance(lines_data, list):
            self.lines = lines_data[:] # Make a copy for editing
        else:
            self.lines = list(lines_data)
        self.total_lines = len(self.lines)
        self.modified = False
        self.undo_stack = []
        self.redo_stack = []
    def get_line(self, line_number):
        """Get a specific line by number (0-indexed)"""
        if 0 <= line_number < self.total_lines:
            return self.lines[line_number]
        return ""
    def get_visible_lines(self, start_line, end_line):
        """Get a range of visible lines"""
        start = max(0, start_line)
        end = min(self.total_lines, end_line + 1)
        return self.lines[start:end]
    def multi_replace(self, start_line, num_lines, new_lines):
        """Replace a range of lines with new lines. Handles set, insert, delete."""
        if start_line < 0 or start_line > self.total_lines or start_line + num_lines > self.total_lines:
            raise IndexError("Invalid range for multi_replace")
        old_lines = self.lines[start_line:start_line + num_lines][:]
        command = {'type': 'multi_replace', 'start_line': start_line, 'old_lines': old_lines, 'new_lines': new_lines[:]}
        self._push_command(command)
        del self.lines[start_line:start_line + num_lines]
        self.lines[start_line:start_line] = new_lines
        self.total_lines += len(new_lines) - num_lines
        self.modified = True
    def undo(self):
        if not self.undo_stack:
            return
        command = self.undo_stack.pop()
        if command['type'] == 'multi_replace':
            start = command['start_line']
            current_num = len(command['new_lines'])
            if start + current_num > self.total_lines:
                return  # Skip if invalid
            current_lines = self.lines[start:start + current_num][:]
            del self.lines[start:start + current_num]
            self.lines[start:start] = command['old_lines']
            self.total_lines += len(command['old_lines']) - current_num
            redo_command = {'type': 'multi_replace', 'start_line': start, 'old_lines': current_lines, 'new_lines': command['new_lines']}
            self.redo_stack.append(redo_command)
        self.modified = True
    def redo(self):
        if not self.redo_stack:
            return
        command = self.redo_stack.pop()
        if command['type'] == 'multi_replace':
            start = command['start_line']
            current_num = len(command['old_lines'])
            if start + current_num > self.total_lines:
                return  # Skip if invalid
            current_lines = self.lines[start:start + current_num][:]
            del self.lines[start:start + current_num]
            self.lines[start:start] = command['new_lines']
            self.total_lines += len(command['new_lines']) - current_num
            undo_command = {'type': 'multi_replace', 'start_line': start, 'old_lines': current_lines, 'new_lines': command['old_lines']}
            self.undo_stack.append(undo_command)
        self.modified = True
    # --- New: Save method ---
    def save_to_file(self, file_path=None):
        """Save buffer content to a file."""
        path = file_path or self.file_path
        if not path:
            raise ValueError("No file path provided and buffer has no associated file.")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.lines))
            self.file_path = path
            self.modified = False
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False

# --- VirtualTextView with tab handling ---
# (The VirtualTextView class remains largely unchanged, but line number width is adjusted)
class VirtualTextView(Gtk.DrawingArea):
    """Custom text view with virtual scrolling for millions of lines"""
    __gsignals__ = {
        'buffer-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'scroll-changed': (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        'modified-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }
    def __init__(self):
        super().__init__()
        self.buffer = VirtualTextBuffer()
        self.scroll_y = 0
        self.scroll_x = 0
        self.line_height = 20
        self.char_width = 8
        self.visible_lines = 0
        self.font_desc = Pango.FontDescription("Monospace 12")
        self.wrap_width = -1
        self._wrapped_lines_cache = {}
        self._needs_wrap_recalc = True
        self.cursor_line = 0
        self.cursor_col = 0
        self.anchor_line = -1
        self.anchor_col = -1
        self.cursor_visible = True
        self.editing = False
        self.edit_line = 0
        self.edit_text = ""
        self.edit_cursor_pos = 0
        self.has_selection = False
        self.last_click_time = 0
        self.click_count = 0
        self.last_click_x = 0
        self.last_click_y = 0
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.in_drag = False
        self.max_line_width = 0
        self.in_selection_drag = False
        self.pending_line = 0
        self.pending_col = 0
        self.pending_shift = False
        self.pending_line_text = ""
        self.is_pasting = False
        self.indent_with_spaces = True
        self.tab_stops = 4  # Tab width
        # Setup widget properties for input
        self.set_can_focus(True)
        self.set_focusable(True)
        self.set_draw_func(self._on_draw)
        self.connect('realize', self._on_realize)
        # Setup scrolling
        v_scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        v_scroll_controller.connect('scroll', self._on_v_scroll)
        self.add_controller(v_scroll_controller)
        h_scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.HORIZONTAL)
        h_scroll_controller.connect('scroll', self._on_h_scroll)
        self.add_controller(h_scroll_controller)
        # Setup key input - CRITICAL: Use legacy key controller for better IME support
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        # Also handle key release for completeness
        key_controller.connect('key-released', self._on_key_released)
        self.add_controller(key_controller)
        # Setup mouse input
        click_controller = Gtk.GestureClick()
        click_controller.connect('pressed', self._on_click)
        click_controller.connect('released', self._on_click_release)
        self.add_controller(click_controller)
        # Setup right-click
        right_click_controller = Gtk.GestureClick()
        right_click_controller.set_button(3)
        right_click_controller.connect('pressed', self._on_right_click_pressed)
        self.add_controller(right_click_controller)
        # Setup drag and drop
        drag_gesture_select = Gtk.GestureDrag()
        drag_gesture_select.connect('drag-begin', self._on_drag_begin_select)
        drag_gesture_select.connect('drag-update', self._on_drag_update_select)
        drag_gesture_select.connect('drag-end', self._on_drag_end_select)
        self.add_controller(drag_gesture_select)
        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.add_controller(drag_source)
        drop_target = Gtk.DropTarget.new(str, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drop_target.connect('accept', self._on_drop_accept)
        drop_target.connect('motion', self._on_drop_motion)
        drop_target.connect('drop', self._on_drop_drop)
        self.add_controller(drop_target)
        # Setup clipboard
        self.clipboard = Gdk.Display.get_default().get_clipboard()
        # Setup cursor blinking
        self.cursor_blink_timeout = None
        self._start_cursor_blink()
        # Setup IME AFTER widget is set up
        self.im_context = None
        self._setup_ime()
        # Setup focus handling
        focus_controller = Gtk.EventControllerFocus.new()
        focus_controller.connect('enter', self._on_focus_in)
        focus_controller.connect('leave', self._on_focus_out)
        self.add_controller(focus_controller)
        # Setup context menu
        self.context_menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()
        menu_model.append("Cut", "win.cut")
        menu_model.append("Copy", "win.copy")
        menu_model.append("Paste", "win.paste")
        menu_model.append("Delete", "win.delete")
        menu_model.append("Select All", "win.select_all")
        self.context_menu.set_menu_model(menu_model)
        self.context_menu.set_parent(self)
    def _on_right_click_pressed(self, gesture, n_press, x, y):
        self.grab_focus()
        window = self.get_root()
        cut_action = window.lookup_action("cut")
        copy_action = window.lookup_action("copy")
        delete_action = window.lookup_action("delete")
        paste_action = window.lookup_action("paste")
        select_all_action = window.lookup_action("select_all")
        if cut_action:
            cut_action.set_enabled(self.has_selection)
        if copy_action:
            copy_action.set_enabled(self.has_selection)
        if delete_action:
            delete_action.set_enabled(self.has_selection)
        if paste_action:
            paste_action.set_enabled(True)
        if select_all_action:
            select_all_action.set_enabled(True)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.context_menu.set_pointing_to(rect)
        self.context_menu.popup()
    def _select_all(self):
        self.anchor_line = 0
        self.anchor_col = 0
        self.cursor_line = self.buffer.total_lines - 1
        self.cursor_col = len(self.buffer.get_line(self.buffer.total_lines - 1))
        self.has_selection = True
        self._ensure_cursor_visible()
        self.queue_draw()
    def _setup_ime(self):
        """Setup Input Method Editor support for Unicode input"""
        try:
            # Use a more comprehensive IME context
            self.im_context = Gtk.IMMulticontext()
            # Connect IME signals
            self.im_context.connect("commit", self._on_im_commit)
            self.im_context.connect("preedit-start", self._on_preedit_start)
            self.im_context.connect("preedit-end", self._on_preedit_end)
            self.im_context.connect("preedit-changed", self._on_preedit_changed)
            # Initialize preedit state
            self.preedit_string = ""
            self.preedit_attrs = None
            self.preedit_cursor_pos = 0
            self.in_preedit = False
            print("IME context set up successfully")
        except Exception as e:
            print(f"Failed to setup IME: {e}")
            # Fallback to simple context
            self.im_context = Gtk.IMContextSimple()
            self.im_context.connect("commit", self._on_im_commit)
    def _on_preedit_start(self, im_context):
        """Handle preedit start - composition begins"""
        self.in_preedit = True
        print("Preedit started")
    def _on_preedit_end(self, im_context):
        """Handle preedit end - composition finished"""
        self.in_preedit = False
        self.preedit_string = ""
        self.preedit_attrs = None
        self.preedit_cursor_pos = 0
        self.queue_draw()
        print("Preedit ended")
    def _on_preedit_changed(self, im_context):
        """Handle preedit changes - composition text changes"""
        try:
            preedit_string, attrs, cursor_pos = self.im_context.get_preedit_string()
            self.preedit_string = preedit_string or ""
            self.preedit_attrs = attrs
            self.preedit_cursor_pos = cursor_pos
            print(f"Preedit changed: '{self.preedit_string}' cursor at {cursor_pos}")
            self.queue_draw()
        except Exception as e:
            print(f"Error in preedit changed: {e}")
    def _on_focus_in(self, controller):
        """Handle focus in - important for IME"""
        print("Focus in - setting up IME")
        if self.im_context:
            self.im_context.focus_in()
            self.im_context.set_client_widget(self)
            self._update_im_cursor_location()
    def _on_focus_out(self, controller):
        """Handle focus out"""
        print("Focus out")
        if self.im_context:
            self.im_context.focus_out()
    def _update_im_cursor_location(self):
        """Update IME cursor location for better positioning of input windows"""
        if not self.im_context:
            return
        try:
            # Calculate cursor position on screen - Narrower line numbers (10 instead of 20)
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
            # Get current line text
            line_text = self.buffer.get_line(self.cursor_line)
            if self.editing and self.cursor_line == self.edit_line:
                line_text = self.edit_text
                cursor_col = self.edit_cursor_pos
            else:
                cursor_col = self.cursor_col
            # Calculate cursor x position
            cursor_text = line_text[:cursor_col]
            cursor_x_pos = self._get_text_width(cursor_text)
            screen_x = line_num_width + 5 - self.scroll_x + cursor_x_pos # Adjusted padding (10 -> 5)
            # Calculate cursor y position
            cursor_y = self.cursor_line * self.line_height - self.scroll_y
            # Create cursor rectangle
            cursor_rect = Gdk.Rectangle()
            cursor_rect.x = int(max(0, screen_x))
            cursor_rect.y = int(max(0, cursor_y))
            cursor_rect.width = 2
            cursor_rect.height = self.line_height
            # Set the cursor location for IME
            self.im_context.set_cursor_location(cursor_rect)
            print(f"Updated IME cursor location: {cursor_rect.x}, {cursor_rect.y}")
        except Exception as e:
            print(f"IME cursor location update failed: {e}")
    def _on_im_commit(self, im_context, text):
        """Handle IME text commit - this is where Unicode input happens"""
        if not text:
            return
        # If we're not in editing mode and we receive text input, start editing
        if not self.editing:
            print("Starting edit mode for IME input")
            self._start_editing()
        # Handle selection deletion first
        if self.has_selection:
            self._delete_selection()
        # Insert text in editing mode
        if self.editing:
            print(f"Inserting '{text}' at position {self.edit_cursor_pos}")
            # Insert text directly in edit mode for immediate feedback
            self.edit_text = (self.edit_text[:self.edit_cursor_pos] +
                             text +
                             self.edit_text[self.edit_cursor_pos:])
            self.edit_cursor_pos += len(text)
            self.cursor_col = self.edit_cursor_pos
            self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
            # Update line width calculations
            if not self.buffer.word_wrap:
                new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                if new_line_width > self.max_line_width:
                    self.max_line_width = new_line_width
            else:
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
            self._ensure_cursor_visible()
            self.queue_draw()
            self.emit('modified-changed', self.buffer.modified)
        else:
            # Fallback to the general text insertion method
            print("Using fallback text insertion")
            self._insert_text_at_cursor(text)
    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events"""
        # Get the actual event for IME processing
        event = controller.get_current_event()
        print(f"Key pressed: {keyval} ({Gdk.keyval_name(keyval)}) keycode: {keycode}")
        # CRITICAL: Let IME handle the input first for all keys except special navigation
        # Only bypass IME for certain control keys
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
        alt_pressed = state & Gdk.ModifierType.ALT_MASK
        # Don't send navigation and control keys to IME
        navigation_keys = {
            Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right,
            Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down,
            Gdk.KEY_Escape, Gdk.KEY_F1, Gdk.KEY_F2, Gdk.KEY_F3, Gdk.KEY_F4,
            Gdk.KEY_F5, Gdk.KEY_F6, Gdk.KEY_F7, Gdk.KEY_F8, Gdk.KEY_F9,
            Gdk.KEY_F10, Gdk.KEY_F11, Gdk.KEY_F12
        }
        # Don't send Ctrl shortcuts to IME
        if not (ctrl_pressed or keyval in navigation_keys):
            if self.im_context and self.im_context.filter_keypress(event):
                #print("Key handled by IME")
                return True
        # Handle Ctrl shortcuts
        if ctrl_pressed and not state & Gdk.ModifierType.SHIFT_MASK and not alt_pressed:
            if keyval == Gdk.KEY_c:
                if self.has_selection:
                    self._copy_to_clipboard()
                return True
            elif keyval == Gdk.KEY_x:
                if self.has_selection:
                    self._cut_to_clipboard()
                return True
            elif keyval == Gdk.KEY_v:
                self._paste_from_clipboard()
                return True
            elif keyval == Gdk.KEY_s:
                self.get_root().on_save_file(None, None)
                return True
            elif keyval == Gdk.KEY_a:
                self._select_all()
                return True
            elif keyval == Gdk.KEY_w:
                self.buffer.word_wrap = not self.buffer.word_wrap
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
                if self.buffer.word_wrap:
                    self.scroll_x = 0
                self.queue_draw()
                # Notify parent (TabContent) to update scrollbars
                parent = self.get_ancestor(TabContent)
                if parent:
                    parent.update_scrollbar_visibility()
                return True
            elif keyval == Gdk.KEY_z:
                self.buffer.undo()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return True
            elif keyval == Gdk.KEY_y:
                self.buffer.redo()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return True
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
        # Handle navigation and special keys
        if not self.editing:
            if keyval in [Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right]:
                if shift_pressed and not self.has_selection:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = self.cursor_col
                if keyval == Gdk.KEY_Up:
                    self._move_cursor_up(shift_pressed)
                elif keyval == Gdk.KEY_Down:
                    self._move_cursor_down(shift_pressed)
                elif keyval == Gdk.KEY_Left:
                    if ctrl_pressed:
                        self._move_cursor_word_left(shift_pressed)
                    else:
                        self._move_cursor_left(shift_pressed)
                elif keyval == Gdk.KEY_Right:
                    if ctrl_pressed:
                        self._move_cursor_word_right(shift_pressed)
                    else:
                        self._move_cursor_right(shift_pressed)
                return True
            elif keyval == Gdk.KEY_Page_Up:
                self._move_cursor_page_up(shift_pressed)
                return True
            elif keyval == Gdk.KEY_Page_Down:
                self._move_cursor_page_down(shift_pressed)
                return True
            elif keyval in [Gdk.KEY_Home, Gdk.KEY_End]:
                if shift_pressed and not self.has_selection:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = self.cursor_col
                if keyval == Gdk.KEY_Home:
                    if ctrl_pressed:
                        self.scroll_to_top()
                        self.cursor_line = 0
                        self.cursor_col = 0
                    else:
                        self.cursor_col = 0
                elif keyval == Gdk.KEY_End:
                    if ctrl_pressed:
                        self.scroll_to_bottom()
                        self.cursor_line = max(0, self.buffer.total_lines - 1)
                        self.cursor_col = len(self.buffer.get_line(self.cursor_line))
                    else:
                        self.cursor_col = len(self.buffer.get_line(self.cursor_line))
                if shift_pressed:
                    self.has_selection = True
                else:
                    self.has_selection = False
                self._ensure_cursor_visible()
                self.queue_draw()
                return True
            elif keyval == Gdk.KEY_Return:
                if self.has_selection:
                    self._delete_selection()
                current_line_text = self.buffer.get_line(self.cursor_line)
                part1 = current_line_text[:self.cursor_col]
                part2 = current_line_text[self.cursor_col:]
                self.buffer.multi_replace(self.cursor_line, 1, [part1])
                self.buffer.multi_replace(self.cursor_line + 1, 0, [part2])
                self.cursor_line += 1
                self.cursor_col = 0
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return True
            elif keyval == Gdk.KEY_F2:
                self._start_editing()
                return True
            elif keyval in [Gdk.KEY_Delete, Gdk.KEY_BackSpace]:
                if self.has_selection:
                    self._delete_selection()
                    return True
                else:
                    if not self.editing:
                        self._start_editing()
                # Let it fall through to editing mode handling
            # For any other printable key in non-editing mode, start editing
            elif not ctrl_pressed and not alt_pressed:
                unicode_char = Gdk.keyval_to_unicode(keyval)
                if unicode_char != 0 and unicode_char >= 32:
                    char = chr(unicode_char)
                    if char and char.isprintable():
                        print(f"Starting edit mode for printable char: '{char}'")
                        if self.has_selection:
                            self._delete_selection()
                        if not self.editing:
                            self._start_editing()
                        # Since not handled by IME, insert manually
                        if self.editing:
                            self.edit_text = self.edit_text[:self.edit_cursor_pos] + char + self.edit_text[self.edit_cursor_pos:]
                            self.edit_cursor_pos += len(char)
                            self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
                            self.cursor_col = self.edit_cursor_pos
                            if self.buffer.word_wrap:
                                self._needs_wrap_recalc = True
                                self._wrapped_lines_cache.clear()
                            else:
                                new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                                if new_line_width > self.max_line_width:
                                    self.max_line_width = new_line_width
                            self._ensure_cursor_visible()
                            self.queue_draw()
                            self.emit('modified-changed', self.buffer.modified)
                        return True
        # In editing mode
        if self.editing:
            if keyval == Gdk.KEY_Return:
                if self.has_selection:
                    self._delete_selection()
                current_edit_cursor_pos = self.edit_cursor_pos
                self._finish_editing()
                current_full_text = self.buffer.get_line(self.cursor_line)
                part1 = current_full_text[:current_edit_cursor_pos]
                part2 = current_full_text[current_edit_cursor_pos:]
                self.buffer.multi_replace(self.cursor_line, 1, [part1])
                self.buffer.multi_replace(self.cursor_line + 1, 0, [part2])
                self.cursor_line += 1
                self.cursor_col = 0
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('buffer-changed')
                self.emit('modified-changed', self.buffer.modified)
                return True
            elif keyval == Gdk.KEY_Escape:
                self._cancel_editing()
                return True
            elif keyval in [Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End]:
                if keyval == Gdk.KEY_Left:
                    if ctrl_pressed:
                        line_text = self.edit_text
                        new_pos = self._find_word_boundary(line_text, self.edit_cursor_pos, -1)
                        self.edit_cursor_pos = new_pos
                    else:
                        self.edit_cursor_pos = max(0, self.edit_cursor_pos - 1)
                elif keyval == Gdk.KEY_Right:
                    if ctrl_pressed:
                        line_text = self.edit_text
                        new_pos = self._find_word_boundary(line_text, self.edit_cursor_pos, 1)
                        self.edit_cursor_pos = new_pos
                    else:
                        self.edit_cursor_pos = min(len(self.edit_text), self.edit_cursor_pos + 1)
                elif keyval == Gdk.KEY_Home:
                    self.edit_cursor_pos = 0
                elif keyval == Gdk.KEY_End:
                    self.edit_cursor_pos = len(self.edit_text)
                self.cursor_col = self.edit_cursor_pos
                self._ensure_cursor_visible()
                self.queue_draw()
                return True
            elif keyval == Gdk.KEY_BackSpace:
                if self.has_selection:
                    self._delete_selection()
                    return True
                else:
                    if self.edit_cursor_pos > 0:
                        self.edit_text = (self.edit_text[:self.edit_cursor_pos-1] +
                                          self.edit_text[self.edit_cursor_pos:])
                        self.edit_cursor_pos -= 1
                        self.cursor_col = self.edit_cursor_pos
                        self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
                        if not self.buffer.word_wrap:
                            new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                            if new_line_width > self.max_line_width:
                                self.max_line_width = new_line_width
                        else:
                            self._needs_wrap_recalc = True
                            self._wrapped_lines_cache.clear()
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('modified-changed', self.buffer.modified)
                    elif self.edit_cursor_pos == 0 and self.edit_line > 0:
                        prev_line_text = self.buffer.get_line(self.edit_line - 1)
                        current_line_text = self.edit_text
                        merged_text = prev_line_text + current_line_text
                        self.buffer.multi_replace(self.edit_line - 1, 2, [merged_text])
                        self.cursor_line = self.edit_line - 1
                        self.cursor_col = len(prev_line_text)
                        self.editing = False
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('buffer-changed')
                        self.emit('modified-changed', self.buffer.modified)
                    return True
            elif keyval == Gdk.KEY_Delete:
                if self.has_selection:
                    self._delete_selection()
                    return True
                else:
                    if self.edit_cursor_pos < len(self.edit_text):
                        self.edit_text = (self.edit_text[:self.edit_cursor_pos] +
                                          self.edit_text[self.edit_cursor_pos+1:])
                        self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
                        if not self.buffer.word_wrap:
                            new_line_width = self._get_text_width(self.edit_text) + 20 * self.char_width
                            if new_line_width > self.max_line_width:
                                self.max_line_width = new_line_width
                        else:
                            self._needs_wrap_recalc = True
                            self._wrapped_lines_cache.clear()
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('modified-changed', self.buffer.modified)
                    elif self.edit_cursor_pos == len(self.edit_text) and self.edit_line < self.buffer.total_lines - 1:
                        next_line_text = self.buffer.get_line(self.edit_line + 1)
                        current_line_text = self.edit_text
                        merged_text = current_line_text + next_line_text
                        self.buffer.multi_replace(self.edit_line, 2, [merged_text])
                        self.editing = False
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('buffer-changed')
                        self.emit('modified-changed', self.buffer.modified)
                    return True
            elif keyval == Gdk.KEY_Up:
                self._finish_editing()
                self._move_cursor_up(shift_pressed)
                return True
            elif keyval == Gdk.KEY_Down:
                self._finish_editing()
                self._move_cursor_down(shift_pressed)
                return True
            elif keyval == Gdk.KEY_Page_Up:
                self._finish_editing()
                self._move_cursor_page_up(shift_pressed)
                return True
            elif keyval == Gdk.KEY_Page_Down:
                self._finish_editing()
                self._move_cursor_page_down(shift_pressed)
                return True
        if keyval == Gdk.KEY_Tab:
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            if self.has_selection:
                self._indent_selection(not shift)
                return True
            if not self.editing:
                self._start_editing()
            if self.editing:
                if shift:
                    # Outdent current line
                    if self.indent_with_spaces:
                        spaces = ' ' * self.tab_stops
                        if self.edit_text.startswith(spaces):
                            self.edit_text = self.edit_text[self.tab_stops:]
                            self.edit_cursor_pos = max(0, self.edit_cursor_pos - self.tab_stops)
                    else:
                        if self.edit_text.startswith('\t'):
                            self.edit_text = self.edit_text[1:]
                            self.edit_cursor_pos = max(0, self.edit_cursor_pos - 1)
                    self.cursor_col = self.edit_cursor_pos
                else:
                    # Insert indent
                    if self.indent_with_spaces:
                        indent = ' ' * self.tab_stops
                    else:
                        indent = '\t'
                    self.edit_text = self.edit_text[:self.edit_cursor_pos] + indent + self.edit_text[self.edit_cursor_pos:]
                    self.edit_cursor_pos += len(indent)
                    self.cursor_col = self.edit_cursor_pos
                self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
                if self.buffer.word_wrap:
                    self._needs_wrap_recalc = True
                    self._wrapped_lines_cache.clear()
                else:
                    self._recalculate_max_line_width()
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('modified-changed', self.buffer.modified)
                return True
        # If we get here, the key wasn't handled
        print(f"Key not handled: {keyval}")
        return False
    def _indent_selection(self, indent=True):
        bounds = self._get_selection_bounds()
        if not bounds:
            return
        start_line, _, end_line, _ = bounds
        for line in range(start_line, end_line + 1):
            line_text = self.buffer.get_line(line)
            if indent:
                if self.indent_with_spaces:
                    new_text = (' ' * self.tab_stops) + line_text
                else:
                    new_text = '\t' + line_text
            else:
                if self.indent_with_spaces:
                    spaces = ' ' * self.tab_stops
                    if line_text.startswith(spaces):
                        new_text = line_text[self.tab_stops:]
                    else:
                        continue
                else:
                    if line_text.startswith('\t'):
                        new_text = line_text[1:]
                    else:
                        continue
            self.buffer.multi_replace(line, 1, [new_text])
        self._needs_wrap_recalc = True
        self._wrapped_lines_cache.clear()
        self._recalculate_max_line_width()
        self.queue_draw()
        self.emit('modified-changed', self.buffer.modified)
    def _on_key_released(self, controller, keyval, keycode, state):
        """Handle key release - may be needed for some IME implementations"""
        if self.im_context:
            event = controller.get_current_event()
            return self.im_context.filter_keypress(event)
        return False
    def do_size_allocate(self, width, height, baseline):
        Gtk.DrawingArea.do_size_allocate(self, width, height, baseline)
        if self.buffer.word_wrap:
            self._needs_wrap_recalc = True
            self.scroll_x = 0
        self._update_visible_lines()
        self._recalculate_max_line_width()
        self.queue_draw()
    def _on_realize(self, widget):
        self._calculate_font_metrics()
        self._update_visible_lines()
        self._recalculate_max_line_width()
        self.queue_draw()
    def _calculate_font_metrics(self):
        context = self.get_pango_context()
        metrics = context.get_metrics(self.font_desc)
        self.line_height = (metrics.get_ascent() + metrics.get_descent()) // Pango.SCALE + 2
        self.char_width = metrics.get_approximate_char_width() // Pango.SCALE
    def _update_visible_lines(self):
        height = self.get_height()
        if height > 0 and self.line_height > 0:
            self.visible_lines = int(height // self.line_height) + 2
    def _get_text_width(self, text):
        if not text:
            return 0
        layout = self.create_pango_layout("")
        layout.set_font_description(self.font_desc)
        layout.set_text(text)
        logical_rect = layout.get_extents()[1]
        return logical_rect.width / Pango.SCALE
    def _get_cursor_position_from_x(self, line_text, x_position):
        if x_position <= 0:
            return 0
        if not line_text:
            return 0
        layout = self.create_pango_layout("")
        layout.set_font_description(self.font_desc)
        layout.set_text(line_text)
        pango_x = int(x_position * Pango.SCALE)
        pango_y = 0
        hit_result = layout.xy_to_index(pango_x, pango_y)
        if hit_result[0]:
            byte_index = hit_result[1]
            trailing = hit_result[2]
            try:
                char_index = len(line_text.encode('utf-8')[:byte_index].decode('utf-8'))
                char_index += trailing
                return max(0, min(char_index, len(line_text)))
            except UnicodeDecodeError:
                char_index = min(byte_index, len(line_text))
                return max(0, char_index)
        else:
            estimated_index = int(x_position / self.char_width)
            if estimated_index > len(line_text):
                return len(line_text)
            return max(0, estimated_index)
    def _find_word_boundary(self, text, pos, direction):
        if direction == -1:
            while pos > 0 and text[pos - 1].isspace():
                pos -= 1
            if pos == 0:
                return 0
            if text[pos - 1].isalnum():
                while pos > 0 and text[pos - 1].isalnum():
                    pos -= 1
            else:
                while pos > 0 and not text[pos - 1].isalnum() and not text[pos - 1].isspace():
                    pos -= 1
            return pos
        else:
            length = len(text)
            while pos < length and text[pos].isspace():
                pos += 1
            if pos == length:
                return length
            if pos < length and text[pos].isalnum():
                while pos < length and text[pos].isalnum():
                    pos += 1
            else:
                while pos < length and not text[pos].isalnum() and not text[pos].isspace():
                    pos += 1
            return pos
    def _byte_to_char_index(self, text, byte_index):
        try:
            return len(text.encode('utf-8')[:byte_index].decode('utf-8'))
        except UnicodeDecodeError:
            return byte_index # fallback
    def _wrap_line(self, line_number, line_text):
        if self.wrap_width <= 0 or len(line_text) == 0:
            return [(line_number, 0, len(line_text))]
        layout = self.create_pango_layout("")
        layout.set_font_description(self.font_desc)
        layout.set_width(self.wrap_width * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(line_text)
        lines = layout.get_lines()
        wrapped_segments = []
        for pango_line in lines:
            start_byte = pango_line.start_index
            length_byte = pango_line.length
            start_char = self._byte_to_char_index(line_text, start_byte)
            end_char = self._byte_to_char_index(line_text, start_byte + length_byte)
            wrapped_segments.append((line_number, start_char, end_char))
        return wrapped_segments
    def _get_wrapped_lines(self, start_line, end_line):
        wrapped_result = []
        if not self.buffer.word_wrap:
            for i in range(start_line, min(end_line + 1, self.buffer.total_lines)):
                line_text = self.buffer.get_line(i)
                wrapped_result.append([(i, 0, len(line_text))])
        else:
            cache_key = (start_line, end_line, self.wrap_width)
            if cache_key in self._wrapped_lines_cache and not self._needs_wrap_recalc:
                return self._wrapped_lines_cache[cache_key]
            if self._needs_wrap_recalc:
                self._wrapped_lines_cache.clear()
                self._needs_wrap_recalc = False
                # Narrower line numbers (20 -> 10)
                line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
                self.wrap_width = self.get_width() - line_num_width - 10 # Adjusted padding (20 -> 10)
                if self.wrap_width <= 0: self.wrap_width = 1
            for i in range(start_line, min(end_line + 1, self.buffer.total_lines)):
                line_text = self.buffer.get_line(i)
                wrapped_segments = self._wrap_line(i, line_text)
                wrapped_result.append(wrapped_segments)
            self._wrapped_lines_cache[cache_key] = wrapped_result
        return wrapped_result
    def _get_total_visual_lines(self):
        if not self.buffer.word_wrap:
            return self.buffer.total_lines
        return self.buffer.total_lines
    def _get_visual_line_info_from_y(self, y):
        if self.buffer.total_lines == 0:
            return 0, 0, 0
        start_line = int(self.scroll_y // self.line_height)
        end_line = min(self.buffer.total_lines - 1, start_line + self.visible_lines + 50)
        wrapped_lines_data = self._get_wrapped_lines(start_line, end_line)
        y_offset = -(self.scroll_y % self.line_height)
        visual_line_counter = 0
        logical_line_index = 0
        while logical_line_index < len(wrapped_lines_data):
            wrapped_segments = wrapped_lines_data[logical_line_index]
            logical_line_num = start_line + logical_line_index
            for segment_index, (seg_line_num, seg_start_col, seg_end_col) in enumerate(wrapped_segments):
                y_pos_top = int(y_offset + visual_line_counter * self.line_height)
                y_pos_bottom = y_pos_top + self.line_height
                if y_pos_top <= y < y_pos_bottom:
                    return logical_line_num, seg_start_col, segment_index
                visual_line_counter += 1
            logical_line_index += 1
        if len(wrapped_lines_data) > 0:
            last_logical_index = len(wrapped_lines_data) - 1
            last_wrapped_segments = wrapped_lines_data[last_logical_index]
            if len(last_wrapped_segments) > 0:
                last_segment = last_wrapped_segments[-1]
                logical_line_num = start_line + last_logical_index
                return logical_line_num, last_segment[2], len(last_wrapped_segments) - 1
        return max(0, self.buffer.total_lines - 1), 0, 0
    def _get_position_from_coords(self, x, y):
        # Narrower line numbers (20 -> 10)
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
        if x < line_num_width:
            return -1, -1
        logical_line_num, seg_start_col, segment_index = self._get_visual_line_info_from_y(y)
        line_text = self.buffer.get_line(logical_line_num)
        if self.editing and logical_line_num == self.edit_line:
            line_text = self.edit_text
        wrapped_segments = self._get_wrapped_lines(logical_line_num, logical_line_num)[0]
        col = 0
        if 0 <= segment_index < len(wrapped_segments):
            _, seg_start, seg_end = wrapped_segments[segment_index]
            segment_text = line_text[seg_start:seg_end]
            # Adjusted padding (10 -> 5)
            rel_x = x - line_num_width - 5 + self.scroll_x
            col_in_seg = self._get_cursor_position_from_x(segment_text, rel_x)
            col = seg_start + col_in_seg
        else:
            # Adjusted padding (10 -> 5)
            rel_x = x - line_num_width - 5 + self.scroll_x
            col = self._get_cursor_position_from_x(line_text, rel_x)
        return logical_line_num, col
    def _is_position_in_selection(self, line, col):
        bounds = self._get_selection_bounds()
        if not bounds:
            return False
        start_line, start_col, end_line, end_col = bounds
        if line < start_line or line > end_line:
            return False
        if line == start_line and col < start_col:
            return False
        if line == end_line and col > end_col:
            return False
        return True
    def _on_drag_begin_select(self, gesture, x, y):
        line, col = self._get_position_from_coords(x, y)
        if line == -1:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if self.has_selection and self._is_position_in_selection(line, col):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.anchor_line = line
        self.anchor_col = col
        self.cursor_line = line
        self.cursor_col = col
        self.has_selection = True
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.queue_draw()
    def _on_drag_update_select(self, gesture, offset_x, offset_y):
        success, start_x, start_y = gesture.get_start_point()
        if not success:
            return
        current_x = start_x + offset_x
        current_y = start_y + offset_y
        line, col = self._get_position_from_coords(current_x, current_y)
        if line == -1:
            return
        self.cursor_line = line
        self.cursor_col = col
        self._ensure_cursor_visible()
        self.queue_draw()
    def _on_drag_end_select(self, gesture, offset_x, offset_y):
        self._on_drag_update_select(gesture, offset_x, offset_y)
        if self.anchor_line == self.cursor_line and self.anchor_col == self.cursor_col:
            self.has_selection = False
            self.anchor_line = -1
            self.anchor_col = -1
        self.queue_draw()
    def _get_selection_bounds(self):
        if not self.has_selection or self.anchor_line < 0:
            return None
        if (self.anchor_line < self.cursor_line) or (self.anchor_line == self.cursor_line and self.anchor_col < self.cursor_col):
            return self.anchor_line, self.anchor_col, self.cursor_line, self.cursor_col
        else:
            return self.cursor_line, self.cursor_col, self.anchor_line, self.anchor_col
    def _get_selected_text(self):
        bounds = self._get_selection_bounds()
        if not bounds:
            return ""
        start_line, start_col, end_line, end_col = bounds
        if start_line == end_line:
            if self.editing and start_line == self.edit_line:
                line_text = self.edit_text
            else:
                line_text = self.buffer.get_line(start_line)
            return line_text[start_col:end_col]
        else:
            lines = []
            if self.editing and start_line == self.edit_line:
                first_line_text = self.edit_text
            else:
                first_line_text = self.buffer.get_line(start_line)
            lines.append(first_line_text[start_col:])
            for line_num in range(start_line + 1, end_line):
                if self.editing and line_num == self.edit_line:
                    lines.append(self.edit_text)
                else:
                    lines.append(self.buffer.get_line(line_num))
            if self.editing and end_line == self.edit_line:
                last_line_text = self.edit_text
            else:
                last_line_text = self.buffer.get_line(end_line)
            lines.append(last_line_text[:end_col])
            return "\n".join(lines)
    def _delete_selection(self):
        bounds = self._get_selection_bounds()
        if not bounds:
            return False
        start_line, start_col, end_line, end_col = bounds
        if start_line == end_line:
            # Selection within a single line
            if self.editing and start_line == self.edit_line:
                line_text = self.edit_text
            else:
                line_text = self.buffer.get_line(start_line)
            new_text = line_text[:start_col] + line_text[end_col:]
            if self.editing and start_line == self.edit_line:
                self.edit_text = new_text
                self.edit_cursor_pos = start_col
                self.buffer.multi_replace(start_line, 1, [new_text])
            else:
                self.buffer.multi_replace(start_line, 1, [new_text])
            self.cursor_line = start_line
            self.cursor_col = start_col
        else:
            # Selection spans multiple lines
            if self.editing and start_line == self.edit_line:
                first_line_text = self.edit_text
            else:
                first_line_text = self.buffer.get_line(start_line)
            if self.editing and end_line == self.edit_line:
                last_line_text = self.edit_text
            else:
                last_line_text = self.buffer.get_line(end_line)
            before_text = first_line_text[:start_col]
            after_text = last_line_text[end_col:]
            merged_text = before_text + after_text
            # Use multi_replace for the entire range
            self.buffer.multi_replace(start_line, end_line - start_line + 1, [merged_text])
            self.cursor_line = start_line
            self.cursor_col = start_col
            # If we were editing on a deleted line, stop editing
            if self.editing and self.edit_line > start_line:
                self.editing = False
            elif self.editing and self.edit_line == start_line:
                self.edit_text = merged_text
                self.edit_cursor_pos = start_col
        self.has_selection = False
        self.anchor_line = -1
        self.anchor_col = -1
        self._ensure_cursor_visible()
        if self.buffer.word_wrap:
            self._wrapped_lines_cache.clear()
            self._needs_wrap_recalc = True
        self.emit('buffer-changed')
        self.emit('modified-changed', self.buffer.modified)
        self.queue_draw()
        return True
    def _copy_to_clipboard(self):
        if not self.has_selection:
            return
        def build_text():
            text = self._get_selected_text()
            GLib.idle_add(self._set_clipboard_text, text, False)
        thread = threading.Thread(target=build_text)
        thread.daemon = True
        thread.start()
    def _cut_to_clipboard(self):
        if not self.has_selection:
            return
        def build_text():
            text = self._get_selected_text()
            GLib.idle_add(self._set_clipboard_text, text, True)
        thread = threading.Thread(target=build_text)
        thread.daemon = True
        thread.start()
    def _set_clipboard_text(self, text, is_cut):
        if text:
            self.clipboard.set(text)
        if is_cut:
            self._delete_selection()
            self.queue_draw()
            self.emit('buffer-changed')
            self.emit('modified-changed', self.buffer.modified)
    def _paste_from_clipboard(self):
        if self.is_pasting:
            return
        self.is_pasting = True
        def on_clipboard_contents(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
                if text:
                    self._insert_text_at_cursor(text)
            except Exception as e:
                print(f"Error pasting: {e}")
            finally:
                self.is_pasting = False
        self.clipboard.read_text_async(None, on_clipboard_contents)
    def _async_insert_text(self, text):
        has_selection = self.has_selection
        editing = self.editing
        cursor_line = self.cursor_line
        cursor_col = self.cursor_col
        edit_line = self.edit_line
        edit_cursor_pos = self.edit_cursor_pos
        buffer_total_lines = self.buffer.total_lines
        try:
            if editing:
                if edit_line < buffer_total_lines:
                    current_line_text = self.buffer.get_line(edit_line)
                    new_text = current_line_text[:edit_cursor_pos] + text + current_line_text[edit_cursor_pos:]
                    lines_added = text.count('\n')
                    final_cursor_line = edit_line + lines_added
                    if lines_added > 0:
                        final_cursor_col = len(text.split('\n')[-1])
                    else:
                        final_cursor_col = edit_cursor_pos + len(text)
                    new_lines = new_text.split('\n')
                    GLib.idle_add(self._finish_async_insert_edit_mode, edit_line, new_lines, final_cursor_line, final_cursor_col)
                else:
                    GLib.idle_add(lambda: print("Error: Edit line out of range during async paste"))
            else:
                if cursor_line < buffer_total_lines:
                    current_line_text = self.buffer.get_line(cursor_line)
                    before_text = current_line_text[:cursor_col]
                    after_text = current_line_text[cursor_col:]
                    paste_lines = text.split('\n')
                    first_line_modified = before_text + (paste_lines[0] if paste_lines else "")
                    lines_to_insert = []
                    final_cursor_line = cursor_line
                    final_cursor_col = cursor_col
                    if len(paste_lines) == 1:
                        final_text = first_line_modified + after_text
                        lines_to_insert = [final_text]
                        final_cursor_line = cursor_line
                        final_cursor_col = len(before_text) + len(paste_lines[0])
                    else:
                        lines_to_insert.append(first_line_modified)
                        if len(paste_lines) > 2:
                            lines_to_insert.extend(paste_lines[1:-1])
                        last_paste_content = paste_lines[-1]
                        last_line_modified = last_paste_content + after_text
                        lines_to_insert.append(last_line_modified)
                        final_cursor_line = cursor_line + len(paste_lines) - 1
                        final_cursor_col = len(last_paste_content)
                    GLib.idle_add(self._finish_async_insert_normal_mode, cursor_line, lines_to_insert, after_text, final_cursor_line, final_cursor_col)
                else:
                    GLib.idle_add(lambda: print("Error: Cursor line out of range during async paste"))
        except Exception as e:
            GLib.idle_add(lambda: print(f"Error during async paste processing: {e}"))
            GLib.idle_add(self.queue_draw)
    def _finish_async_insert_edit_mode(self, edit_line, new_lines, final_cursor_line, final_cursor_col):
        if not (0 <= edit_line < self.buffer.total_lines):
            print("Error: Invalid edit line for async paste finish")
            return
        try:
            self.buffer.multi_replace(edit_line, 1, new_lines)
            self.editing = False
            self.cursor_line = final_cursor_line
            self.cursor_col = final_cursor_col
            if len(new_lines) == 1:
                self.editing = True
                self.edit_line = final_cursor_line
                self.edit_text = new_lines[0]
                self.edit_cursor_pos = final_cursor_col
            if self.buffer.word_wrap:
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
            self._ensure_cursor_visible()
            self.queue_draw()
            self.emit('buffer-changed')
            self.emit('modified-changed', self.buffer.modified)
        except Exception as e:
            print(f"Error finishing async edit paste: {e}")
            self.queue_draw()
    def _finish_async_insert_normal_mode(self, cursor_line, lines_to_insert, after_text, final_cursor_line, final_cursor_col):
        if not (0 <= cursor_line < self.buffer.total_lines):
            print("Error: Invalid cursor line for async paste finish")
            return
        try:
            self.buffer.multi_replace(cursor_line, 1, lines_to_insert)
            self.cursor_line = final_cursor_line
            self.cursor_col = final_cursor_col
            if self.buffer.word_wrap:
                self._needs_wrap_recalc = True
                self._wrapped_lines_cache.clear()
            self._ensure_cursor_visible()
            self.queue_draw()
            self.emit('buffer-changed')
            self.emit('modified-changed', self.buffer.modified)
        except Exception as e:
            print(f"Error finishing async normal paste: {e}")
            self.queue_draw()
    def _insert_text_at_cursor(self, text):
        """Insert text at the current cursor position"""
        if self.has_selection:
            # Delete selection first
            self._delete_selection()
        # Ensure we're in a valid state after potential selection deletion
        if self.cursor_line >= self.buffer.total_lines:
            self.cursor_line = max(0, self.buffer.total_lines - 1)
        # Start editing if not already editing
        if not self.editing:
            self._start_editing()
        # Handle the text insertion
        if '\n' in text:
            # Multi-line paste - handle in background thread
            thread = threading.Thread(target=self._async_insert_text, args=(text,))
            thread.daemon = True
            thread.start()
        else:
            # Single line text - handle immediately
            if self.editing:
                self.edit_text = (self.edit_text[:self.edit_cursor_pos] +
                                 text +
                                 self.edit_text[self.edit_cursor_pos:])
                self.edit_cursor_pos += len(text)
                self.cursor_col = self.edit_cursor_pos
                self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
                if self.buffer.word_wrap:
                    self._needs_wrap_recalc = True
                    self._wrapped_lines_cache.clear()
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('modified-changed', self.buffer.modified)
    def _on_drag_prepare(self, source, x, y):
        if self.has_selection:
            selected_text = self._get_selected_text()
            if selected_text:
                return Gdk.ContentProvider.new_for_value(selected_text)
        return None
    def _on_drag_begin(self, source, drag):
        self.in_drag = True
        pass
    def _on_drag_end(self, source, drag, delete_data):
        self.in_drag = False
        if delete_data and self.has_selection:
            self._delete_selection()
            self.queue_draw()
    def _on_drop_accept(self, target, drop):
        formats = drop.get_formats()
        return formats.contain_gtype(str)
    def _on_drop_motion(self, target, x, y):
        return Gdk.DragAction.COPY # Return single preferred action
    def _on_drop_drop(self, target, value, x, y):
        if isinstance(value, str):
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
            if x > line_num_width:
                logical_line_num, seg_start_col, segment_index = self._get_visual_line_info_from_y(y)
                if logical_line_num is not None:
                    target_line = logical_line_num
                    line_text = self.buffer.get_line(target_line)
                    if self.editing and target_line == self.edit_line:
                        line_text = self.edit_text
                    line_wrapped_segments = self._get_wrapped_lines(target_line, target_line)[0]
                    target_col = 0
                    if 0 <= segment_index < len(line_wrapped_segments):
                        seg_line_num, seg_start_col_actual, seg_end_col_actual = line_wrapped_segments[segment_index]
                        segment_text = line_text[seg_start_col_actual:seg_end_col_actual]
                        # Adjusted padding (10 -> 5)
                        text_x_position_in_segment = x - line_num_width - 5 + self.scroll_x
                        col_in_segment = self._get_cursor_position_from_x(segment_text, text_x_position_in_segment)
                        target_col = seg_start_col_actual + col_in_segment
                    else:
                        # Adjusted padding (10 -> 5)
                        text_x_position = x - line_num_width - 5 + self.scroll_x
                        target_col = self._get_cursor_position_from_x(line_text, text_x_position)
                    old_cursor_line, old_cursor_col = self.cursor_line, self.cursor_col
                    self.cursor_line, self.cursor_col = target_line, target_col
                    if old_cursor_line != self.cursor_line or old_cursor_col != self.cursor_col:
                        self.has_selection = False
                    self._insert_text_at_cursor(value)
                    return True
        return False
    def _on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return
        font_options = cairo.FontOptions()
        font_options.set_antialias(cairo.ANTIALIAS_SUBPIXEL)
        font_options.set_hint_style(cairo.HINT_STYLE_SLIGHT)
        cr.set_font_options(font_options)
        is_dark = Adw.StyleManager.get_default().get_dark()
        if int(height // self.line_height) + 2 != self.visible_lines:
            self.visible_lines = int(height // self.line_height) + 2
        if self.buffer.word_wrap:
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
            # Adjusted padding (20 -> 10)
            new_wrap_width = width - line_num_width - 10
            wrap_width_changed = new_wrap_width > 0 and new_wrap_width != self.wrap_width
            if wrap_width_changed:
                self.wrap_width = new_wrap_width
                self._wrapped_lines_cache.clear()
                self._needs_wrap_recalc = False
            if self._needs_wrap_recalc:
                self._wrapped_lines_cache.clear()
                self._needs_wrap_recalc = False
        start_line = int(self.scroll_y // self.line_height)
        end_line = start_line + self.visible_lines + 10
        wrapped_lines_data = self._get_wrapped_lines(start_line, end_line)
        cr.set_source_rgb(1, 1, 1) if not is_dark else cr.set_source_rgb(0.1, 0.1, 0.1)
        cr.paint()
        # Narrower line numbers (20 -> 10)
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
        cr.set_source_rgb(0.95, 0.95, 0.95) if not is_dark else cr.set_source_rgb(0.15, 0.15, 0.15)
        cr.rectangle(0, 0, line_num_width, height)
        cr.fill()
        y_offset = -(self.scroll_y % self.line_height)
        visual_line_counter = 0
        line_index = 0
        while line_index < len(wrapped_lines_data) and visual_line_counter < self.visible_lines + 20:
            wrapped_segments = wrapped_lines_data[line_index]
            logical_line_num = start_line + line_index
            if wrapped_segments:
                y_pos = int(y_offset + visual_line_counter * self.line_height)
                if y_pos > height:
                    break
                cr.set_source_rgb(0.5, 0.5, 0.5) if not is_dark else cr.set_source_rgb(0.7, 0.7, 0.7)
                line_num_layout = self.create_pango_layout("")
                line_num_layout.set_font_description(self.font_desc)
                line_num_layout.set_text(str(logical_line_num + 1))
                # Adjusted padding (10 -> 5)
                cr.move_to(5, y_pos)
                PangoCairo.show_layout(cr, line_num_layout)
            visual_line_counter += len(wrapped_segments)
            line_index += 1
        separator_x = line_num_width
        cr.set_source_rgb(0.8, 0.8, 0.8) if not is_dark else cr.set_source_rgb(0.3, 0.3, 0.3)
        cr.set_line_width(1)
        cr.move_to(separator_x, 0)
        cr.line_to(separator_x, height)
        cr.stroke()
        cr.save()
        cr.rectangle(line_num_width, 0, width - line_num_width, height)
        cr.clip()
        visual_line_counter = 0
        line_index = 0
        while line_index < len(wrapped_lines_data) and visual_line_counter < self.visible_lines + 20:
            wrapped_segments = wrapped_lines_data[line_index]
            logical_line_num = start_line + line_index
            line_text_full = self.buffer.get_line(logical_line_num)
            display_text_full = line_text_full
            if self.editing and logical_line_num == self.edit_line:
                display_text_full = self.edit_text
            for segment_index, (seg_line_num, seg_start_col, seg_end_col) in enumerate(wrapped_segments):
                if visual_line_counter > self.visible_lines + 20:
                    break
                y_pos = int(y_offset + visual_line_counter * self.line_height)
                if y_pos > height:
                    break
                segment_text = display_text_full[seg_start_col:seg_end_col]
                if logical_line_num == self.cursor_line:
                    cursor_in_segment = False
                    if self.editing and logical_line_num == self.edit_line:
                        cursor_in_segment = seg_start_col <= self.edit_cursor_pos <= seg_end_col
                    else:
                        cursor_in_segment = seg_start_col <= self.cursor_col <= seg_end_col
                    if cursor_in_segment:
                        cr.set_source_rgb(0.95, 0.95, 1.0) if not is_dark else cr.set_source_rgb(0.2, 0.2, 0.3)
                        highlight_x_start = line_num_width
                        cr.rectangle(highlight_x_start, y_pos - 2, width - line_num_width, self.line_height)
                        cr.fill()
                cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                segment_layout = self.create_pango_layout("")
                segment_layout.set_font_description(self.font_desc)
                tab_array = Pango.TabArray(1, False)
                tab_array.set_tab(0, Pango.TabAlign.LEFT, self.tab_stops * self.char_width * Pango.SCALE)
                segment_layout.set_tabs(tab_array)
                segment_layout.set_text(segment_text)
                # Adjusted padding (10 -> 5)
                cr.move_to(line_num_width + 5 - self.scroll_x, y_pos)
                PangoCairo.show_layout(cr, segment_layout)
                if self.has_selection:
                    bounds = self._get_selection_bounds()
                    if bounds:
                        sel_start_line, sel_start_col, sel_end_line, sel_end_col = bounds
                        segment_selected = False
                        selection_text = ""
                        local_sel_start = 0
                        local_sel_end = len(segment_text)
                        if sel_start_line == sel_end_line == logical_line_num:
                            if seg_start_col < sel_end_col and seg_end_col > sel_start_col:
                                segment_selected = True
                                local_sel_start = max(sel_start_col, seg_start_col) - seg_start_col
                                local_sel_end = min(sel_end_col, seg_end_col) - seg_start_col
                                selection_text = segment_text[local_sel_start:local_sel_end]
                        elif sel_start_line == logical_line_num and sel_end_line > logical_line_num:
                            if seg_end_col > sel_start_col:
                                segment_selected = True
                                local_sel_start = max(sel_start_col, seg_start_col) - seg_start_col
                                local_sel_end = len(segment_text)
                                selection_text = segment_text[local_sel_start:]
                        elif sel_start_line < logical_line_num < sel_end_line:
                            segment_selected = True
                            local_sel_start = 0
                            local_sel_end = len(segment_text)
                            selection_text = segment_text
                        elif sel_end_line == logical_line_num and sel_start_line < logical_line_num:
                            if seg_start_col < sel_end_col:
                                segment_selected = True
                                local_sel_start = 0
                                local_sel_end = min(sel_end_col, seg_end_col) - seg_start_col
                                selection_text = segment_text[:local_sel_end]
                        if segment_selected and selection_text:
                            pre_sel_text = segment_text[:local_sel_start]
                            pre_width = self._get_text_width(pre_sel_text)
                            sel_width = self._get_text_width(selection_text)
                            # Adjusted padding (10 -> 5)
                            sel_x_start = line_num_width + 5 - self.scroll_x + pre_width
                            cr.set_source_rgb(0.5, 0.7, 1.0) if not is_dark else cr.set_source_rgb(0.3, 0.5, 0.8)
                            cr.rectangle(sel_x_start, y_pos, sel_width, self.line_height)
                            cr.fill()
                            cr.set_source_rgb(1, 1, 1) if not is_dark else cr.set_source_rgb(0, 0, 0)
                            sel_layout = self.create_pango_layout("")
                            sel_layout.set_font_description(self.font_desc)
                            tab_array = Pango.TabArray(1, False)
                            tab_array.set_tab(0, Pango.TabAlign.LEFT, self.tab_stops * self.char_width * Pango.SCALE)
                            sel_layout.set_tabs(tab_array)
                            sel_layout.set_text(selection_text)
                            cr.move_to(sel_x_start, y_pos)
                            PangoCairo.show_layout(cr, sel_layout)
                cursor_on_segment = False
                # Adjusted padding (10 -> 5)
                cursor_x = line_num_width + 5 - self.scroll_x
                if self.editing and logical_line_num == self.edit_line:
                    if seg_start_col <= self.edit_cursor_pos <= seg_end_col:
                        cursor_on_segment = True
                        cursor_text = display_text_full[seg_start_col:self.edit_cursor_pos]
                        cursor_x += self._get_text_width(cursor_text)
                else:
                    if logical_line_num == self.cursor_line and seg_start_col <= self.cursor_col <= seg_end_col:
                        cursor_on_segment = True
                        cursor_text = display_text_full[seg_start_col:self.cursor_col]
                        cursor_x += self._get_text_width(cursor_text)
                if cursor_on_segment and self.cursor_visible and cursor_x >= line_num_width and cursor_x <= width and not self.has_selection:
                    cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                    cr.set_line_width(2)
                    cr.move_to(cursor_x, y_pos)
                    cr.line_to(cursor_x, y_pos + self.line_height - 2)
                    cr.stroke()
                visual_line_counter += 1
            # Special handling for empty lines to ensure cursor is drawn
            if len(wrapped_segments) == 0 and logical_line_num == self.cursor_line and self.cursor_col == 0 and self.cursor_visible and not self.has_selection:
                y_pos = int(y_offset + visual_line_counter * self.line_height)
                if 0 <= y_pos < height:
                    # Adjusted padding (10 -> 5)
                    cursor_x = line_num_width + 5 - self.scroll_x
                    if cursor_x >= line_num_width and cursor_x <= width:
                        cr.set_source_rgb(0, 0, 0) if not is_dark else cr.set_source_rgb(0.9, 0.9, 0.9)
                        cr.set_line_width(2)
                        cr.move_to(cursor_x, y_pos)
                        cr.line_to(cursor_x, y_pos + self.line_height - 2)
                        cr.stroke()
                visual_line_counter += 1
            line_index += 1
    def _on_v_scroll(self, controller, dx, dy):
        scroll_amount = dy * self.line_height * 3
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll = max(0, total_visual_height - self.get_height())
        old_scroll_y = self.scroll_y
        self.scroll_y = max(0, min(max_scroll, self.scroll_y + scroll_amount))
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
        self.queue_draw()
        return True
    def _on_h_scroll(self, controller, dx, dy):
        if self.buffer.word_wrap:
            return False
        scroll_amount = dx * self.char_width * 10
        # Narrower line numbers (20 -> 10)
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
        available_width = self.get_width() - line_num_width
        max_scroll_x = max(0, self.max_line_width - available_width)
        old_scroll_x = self.scroll_x
        self.scroll_x = max(0, min(max_scroll_x, self.scroll_x + scroll_amount))
        if old_scroll_x != self.scroll_x:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
        self.queue_draw()
        return True
    def _on_click(self, gesture, n_press, x, y):
        self.drag_start_x, self.drag_start_y = x, y
        self.in_drag = False
        current_time = time.time()
        time_threshold = 0.3
        distance_threshold = 5
        is_same_click = (
            abs(x - self.last_click_x) < distance_threshold and
            abs(y - self.last_click_y) < distance_threshold
        )
        if current_time - self.last_click_time < time_threshold and is_same_click:
            self.click_count += 1
        else:
            self.click_count = 1
        self.last_click_time = current_time
        self.last_click_x = x
        self.last_click_y = y
    def _on_click_release(self, gesture, n_press, x, y):
        drag_threshold = 5
        if abs(x - self.drag_start_x) > drag_threshold or abs(y - self.drag_start_y) > drag_threshold:
            return
        # Narrower line numbers (20 -> 10)
        line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
        if x > line_num_width:
            logical_line_num, seg_start_col, segment_index = self._get_visual_line_info_from_y(y)
            if logical_line_num is not None:
                old_line = self.cursor_line
                old_col = self.cursor_col
                self.cursor_line = logical_line_num
                line_text = self.buffer.get_line(self.cursor_line)
                if self.editing and self.cursor_line == self.edit_line:
                    line_text = self.edit_text
                line_wrapped_segments = self._get_wrapped_lines(self.cursor_line, self.cursor_line)[0]
                if 0 <= segment_index < len(line_wrapped_segments):
                    seg_line_num, seg_start_col_actual, seg_end_col_actual = line_wrapped_segments[segment_index]
                    segment_text = line_text[seg_start_col_actual:seg_end_col_actual]
                    # Adjusted padding (10 -> 5)
                    text_x_position_in_segment = x - line_num_width - 5 + self.scroll_x
                    col_in_segment = self._get_cursor_position_from_x(segment_text, text_x_position_in_segment)
                    self.cursor_col = seg_start_col_actual + col_in_segment
                else:
                    # Adjusted padding (10 -> 5)
                    text_x_position = x - line_num_width - 5 + self.scroll_x
                    self.cursor_col = self._get_cursor_position_from_x(line_text, text_x_position)
                shift_pressed = Gdk.ModifierType.SHIFT_MASK & gesture.get_current_event_state()
                if self.click_count == 1:
                    if shift_pressed:
                        if not self.has_selection:
                            self.anchor_line = old_line
                            self.anchor_col = old_col
                            self.has_selection = True
                    else:
                        self.has_selection = False
                        self.anchor_line = -1
                        self.anchor_col = -1
                    if self.editing:
                        if self.edit_line != self.cursor_line:
                            self._finish_editing()
                        else:
                            self.edit_cursor_pos = self.cursor_col
                elif self.click_count == 2:
                    if self.cursor_col < len(line_text) and line_text[self.cursor_col].isspace():
                        start_pos = self.cursor_col
                        while start_pos > 0 and line_text[start_pos - 1].isspace():
                            start_pos -= 1
                        end_pos = self.cursor_col + 1
                        while end_pos < len(line_text) and line_text[end_pos].isspace():
                            end_pos += 1
                    else:
                        start_pos = self._find_word_boundary(line_text, self.cursor_col, -1)
                        end_pos = self._find_word_boundary(line_text, self.cursor_col, 1)
                    self.anchor_line = self.cursor_line
                    self.anchor_col = start_pos
                    self.cursor_col = end_pos
                    self.has_selection = True
                    if not self.editing:
                        self._start_editing()
                        self.edit_cursor_pos = self.cursor_col
                elif self.click_count >= 3:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = 0
                    self.cursor_col = len(line_text)
                    self.has_selection = True
                    self.click_count = 0
                    if not self.editing:
                        self._start_editing()
                        self.edit_cursor_pos = self.cursor_col
                self._ensure_cursor_visible()
                self.queue_draw()
    def _start_cursor_blink(self):
        def blink():
            self.cursor_visible = not self.cursor_visible
            self.queue_draw()
            return True
        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)
        self.cursor_blink_timeout = GLib.timeout_add(500, blink)
    def _move_cursor_up(self, extend_selection=False):
        if self.cursor_line > 0:
            if extend_selection:
                if not self.has_selection:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = self.cursor_col
                    self.has_selection = True
            else:
                self.has_selection = False
                self.anchor_line = -1
                self.anchor_col = -1
            self.cursor_line -= 1
            line_text = self.buffer.get_line(self.cursor_line)
            self.cursor_col = min(self.cursor_col, len(line_text))
            self._ensure_cursor_visible()
            self.queue_draw()
    def _move_cursor_down(self, extend_selection=False):
        if self.cursor_line < self.buffer.total_lines - 1:
            if extend_selection:
                if not self.has_selection:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = self.cursor_col
                    self.has_selection = True
            else:
                self.has_selection = False
                self.anchor_line = -1
                self.anchor_col = -1
            self.cursor_line += 1
            line_text = self.buffer.get_line(self.cursor_line)
            self.cursor_col = min(self.cursor_col, len(line_text))
            self._ensure_cursor_visible()
            self.queue_draw()
    def _move_cursor_left(self, extend_selection=False):
        if extend_selection:
            if not self.has_selection:
                self.anchor_line = self.cursor_line
                self.anchor_col = self.cursor_col
                self.has_selection = True
        else:
            self.has_selection = False
            self.anchor_line = -1
            self.anchor_col = -1
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_line > 0:
            self.cursor_line -= 1
            self.cursor_col = len(self.buffer.get_line(self.cursor_line))
            self._ensure_cursor_visible()
        self.queue_draw()
    def _move_cursor_right(self, extend_selection=False):
        line_text = self.buffer.get_line(self.cursor_line)
        if extend_selection:
            if not self.has_selection:
                self.anchor_line = self.cursor_line
                self.anchor_col = self.cursor_col
                self.has_selection = True
        else:
            self.has_selection = False
            self.anchor_line = -1
            self.anchor_col = -1
        if self.cursor_col < len(line_text):
            self.cursor_col += 1
        elif self.cursor_line < self.buffer.total_lines - 1:
            self.cursor_line += 1
            self.cursor_col = 0
            self._ensure_cursor_visible()
        self.queue_draw()
    def _move_cursor_word_left(self, extend_selection=False):
        line_text = self.buffer.get_line(self.cursor_line)
        new_col = self._find_word_boundary(line_text, self.cursor_col, -1)
        if new_col != self.cursor_col:
            if extend_selection:
                if not self.has_selection:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = self.cursor_col
                    self.has_selection = True
            else:
                self.has_selection = False
                self.anchor_line = -1
                self.anchor_col = -1
            self.cursor_col = new_col
            self._ensure_cursor_visible()
            self.queue_draw()
    def _move_cursor_word_right(self, extend_selection=False):
        line_text = self.buffer.get_line(self.cursor_line)
        new_col = self._find_word_boundary(line_text, self.cursor_col, 1)
        if new_col != self.cursor_col:
            if extend_selection:
                if not self.has_selection:
                    self.anchor_line = self.cursor_line
                    self.anchor_col = self.cursor_col
                    self.has_selection = True
            else:
                self.has_selection = False
                self.anchor_line = -1
                self.anchor_col = -1
            self.cursor_col = new_col
            self._ensure_cursor_visible()
            self.queue_draw()
    def _move_cursor_page_up(self, extend_selection=False):
        if extend_selection:
            if not self.has_selection:
                self.anchor_line = self.cursor_line
                self.anchor_col = self.cursor_col
                self.has_selection = True
        else:
            self.has_selection = False
            self.anchor_line = -1
            self.anchor_col = -1
        new_line = max(0, self.cursor_line - self.visible_lines + 1)
        self.cursor_line = new_line
        line_text = self.buffer.get_line(self.cursor_line)
        self.cursor_col = min(self.cursor_col, len(line_text))
        self._ensure_cursor_visible()
        self.queue_draw()
    def _move_cursor_page_down(self, extend_selection=False):
        if extend_selection:
            if not self.has_selection:
                self.anchor_line = self.cursor_line
                self.anchor_col = self.cursor_col
                self.has_selection = True
        else:
            self.has_selection = False
            self.anchor_line = -1
            self.anchor_col = -1
        new_line = min(self.buffer.total_lines - 1, self.cursor_line + self.visible_lines - 1)
        self.cursor_line = new_line
        line_text = self.buffer.get_line(self.cursor_line)
        self.cursor_col = min(self.cursor_col, len(line_text))
        self._ensure_cursor_visible()
        self.queue_draw()
    def _ensure_cursor_visible(self):
        cursor_y = self.cursor_line * self.line_height
        viewport_top = self.scroll_y
        viewport_bottom = self.scroll_y + self.get_height()
        old_scroll_y = self.scroll_y
        if cursor_y < viewport_top:
            self.scroll_y = cursor_y
        elif cursor_y + self.line_height > viewport_bottom:
            self.scroll_y = cursor_y + self.line_height - self.get_height()
        old_scroll_x = self.scroll_x
        if not self.buffer.word_wrap:
            line_text = self.buffer.get_line(self.cursor_line)
            col = self.cursor_col
            if self.editing and self.cursor_line == self.edit_line:
                line_text = self.edit_text
                col = self.edit_cursor_pos
            cursor_x_pos_in_line = self._get_text_width(line_text[:col])
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
            available_width = self.get_width() - line_num_width
            if cursor_x_pos_in_line < self.scroll_x:
                self.scroll_x = max(0, cursor_x_pos_in_line - 10)
            elif cursor_x_pos_in_line > (self.scroll_x + available_width - self.char_width):
                self.scroll_x = cursor_x_pos_in_line - available_width + self.char_width + 10
            max_scroll_x = max(0, self.max_line_width - available_width)
            self.scroll_x = max(0, min(self.scroll_x, max_scroll_x))
        if old_scroll_y != self.scroll_y or old_scroll_x != self.scroll_x:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
    def _start_editing(self):
        """Start editing mode on the current cursor line"""
        self.editing = True
        self.edit_line = self.cursor_line
        # Ensure we have a valid line to edit
        if self.edit_line >= self.buffer.total_lines:
            self.edit_line = max(0, self.buffer.total_lines - 1)
            self.cursor_line = self.edit_line
        # Get the current line text
        self.edit_text = self.buffer.get_line(self.edit_line)
        # Ensure cursor column is within bounds
        if self.cursor_col > len(self.edit_text):
            self.cursor_col = len(self.edit_text)
        self.edit_cursor_pos = self.cursor_col
        self.queue_draw()
    def _finish_editing(self):
        if self.editing:
            self.buffer.multi_replace(self.edit_line, 1, [self.edit_text])
            self.editing = False
            self.cursor_col = self.edit_cursor_pos
            self.queue_draw()
            self.emit('buffer-changed')
            self.emit('modified-changed', self.buffer.modified)
    def _cancel_editing(self):
        if self.editing:
            self.editing = False
            self.queue_draw()
    def scroll_by_lines(self, lines):
        scroll_amount = lines * self.line_height
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll = max(0, total_visual_height - self.get_height())
        old_scroll_y = self.scroll_y
        self.scroll_y = max(0, min(max_scroll, self.scroll_y + scroll_amount))
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
        self.queue_draw()
    def scroll_to_top(self):
        old_scroll_y = self.scroll_y
        self.scroll_y = 0
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
        self.queue_draw()
    def scroll_to_bottom(self):
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll = max(0, total_visual_height - self.get_height())
        old_scroll_y = self.scroll_y
        self.scroll_y = max_scroll
        if old_scroll_y != self.scroll_y:
            self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
        self.queue_draw()
    def set_scroll_position(self, scroll_y, scroll_x=0):
        total_visual_height = self._get_total_visual_lines() * self.line_height
        max_scroll_y = max(0, total_visual_height - self.get_height())
        self.scroll_y = max(0, min(max_scroll_y, scroll_y))
        if not self.buffer.word_wrap:
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(self.buffer.total_lines)) * self.char_width + 10
            available_width = self.get_width() - line_num_width
            max_scroll_x = max(0, self.max_line_width - available_width)
            self.scroll_x = max(0, min(max_scroll_x, scroll_x))
        # Notify parent (TabContent) to update scrollbars
        parent = self.get_ancestor(TabContent)
        if parent:
            parent.update_scrollbar_visibility()
        self.queue_draw()
    def _recalculate_max_line_width(self):
        if self.buffer.word_wrap:
            self.max_line_width = 0
            return
        max_width = 0
        start_line = max(0, int(self.scroll_y // self.line_height) - 100)
        end_line = min(self.buffer.total_lines, start_line + self.visible_lines + 200)
        sample_lines = []
        step = max(1, (end_line - start_line) // 100)
        for i in range(start_line, end_line, step):
            sample_lines.append(self.buffer.get_line(i))
        if self.editing and (self.edit_line < start_line or self.edit_line >= end_line):
            sample_lines.append(self.edit_text)
        for line_text in sample_lines:
            layout = self.create_pango_layout("")
            layout.set_font_description(self.font_desc)
            layout.set_text(line_text)
            logical_rect = layout.get_extents()[1]
            width = logical_rect.width / Pango.SCALE
            if width > max_width:
                max_width = width
        self.max_line_width = max_width + 20 * self.char_width
    def set_buffer(self, buffer):
        self.buffer = buffer
        self.scroll_y = 0
        self.scroll_x = 0
        self.cursor_line = 0
        self.cursor_col = 0
        self.anchor_line = -1
        self.anchor_col = -1
        self.editing = False
        self.has_selection = False
        self._wrapped_lines_cache.clear()
        self._needs_wrap_recalc = True
        self._recalculate_max_line_width()
        self.emit('buffer-changed')
        self.emit('scroll-changed', self.scroll_y, self.scroll_x)
        self.emit('modified-changed', self.buffer.modified)
        self.queue_draw()

# --- Custom Overlay for Tab Content with Scrollbars ---
class TabContent(Gtk.Box):
    """A box containing a VirtualTextView and its scrollbars, managed by Adw.ToolbarView."""
    def __init__(self, text_view):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.text_view = text_view

        # Use Adw.ToolbarView for header and status bars within the tab
        self.toolbar_view = Adw.ToolbarView()
        self.append(self.toolbar_view)

        # Header Bar for the tab content
        #self.header_bar = Adw.HeaderBar()
        #self.toolbar_view.add_top_bar(self.header_bar)

        # --- Create Tab Content with Manual Scrollbars using Gtk.Overlay ---
        self.overlay = Gtk.Overlay()
        self.overlay.set_hexpand(True)
        self.overlay.set_vexpand(True)

        # Horizontal box for text view and vertical scrollbar
        self.text_and_v_scroll_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.text_and_v_scroll_box.set_hexpand(True)
        self.text_and_v_scroll_box.set_vexpand(True)

        # Add the text view to the horizontal box
        self.text_view.set_hexpand(True)
        self.text_view.set_vexpand(True)
        self.text_and_v_scroll_box.append(self.text_view)

        # Create and add the vertical scrollbar to the horizontal box
        self.v_scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL)
        # Apply Adwaita's overlay scrollbar style
        #self.v_scrollbar.add_css_class("osd")
        # Make it smaller
        css_provider_v = Gtk.CssProvider()
        css_provider_v.load_from_data(b"""
            scrollbar.vertical slider {
                min-width: 3px;
            }
        """)
        style_context_v = self.v_scrollbar.get_style_context()
        style_context_v.add_provider(css_provider_v, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.text_and_v_scroll_box.append(self.v_scrollbar)

        # Add the text/scroll box to the overlay
        self.overlay.set_child(self.text_and_v_scroll_box)

        # Create and add the horizontal scrollbar as an overlay
        self.h_scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL)
        self.h_scrollbar.set_valign(Gtk.Align.END)
        self.h_scrollbar.set_halign(Gtk.Align.FILL)
        # Apply Adwaita's overlay scrollbar style
       # self.h_scrollbar.add_css_class("osd")
        # Make it smaller
        css_provider_h = Gtk.CssProvider()
        css_provider_h.load_from_data(b"""
            scrollbar.horizontal slider {
                min-height: 8px;
            }
        """)
        style_context_h = self.h_scrollbar.get_style_context()
        style_context_h.add_provider(css_provider_h, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.overlay.add_overlay(self.h_scrollbar)

        # Set the overlay as the main content of the toolbar view
        self.toolbar_view.set_content(self.overlay)

        # Status Bar for the tab content
        self.status_bar = Gtk.Label()
        self.status_bar.set_text("Ready")
        self.status_bar.set_halign(Gtk.Align.START)
        self.status_bar.add_css_class("dim-label")
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.append(self.status_bar)
        status_box.add_css_class("toolbar")
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)
        status_box.set_margin_top(6)
        status_box.set_margin_bottom(6)
        self.toolbar_view.add_bottom_bar(status_box)

        # --- Connect Scrollbars to the TextView ---
        # Connect scrollbar adjustments to text view methods
        self.v_scrollbar.get_adjustment().connect('value-changed', self._on_v_scrollbar_changed)
        self.h_scrollbar.get_adjustment().connect('value-changed', self._on_h_scrollbar_changed)

        # Connect text view signals to update scrollbars
        self.text_view.connect('buffer-changed', self._on_buffer_changed)
        self.text_view.connect('scroll-changed', self._on_scroll_changed)
        self.text_view.connect('modified-changed', self._on_modified_changed)

        # Initial scrollbar update
        GLib.idle_add(self.update_scrollbar) # Delay to ensure sizes are known

    def _on_buffer_changed(self, text_view):
        """Handle buffer changes (recalculate widths, update scrollbars)."""
        text_view._recalculate_max_line_width()
        if text_view.buffer.word_wrap:
            text_view._wrapped_lines_cache.clear()
            text_view._needs_wrap_recalc = True
        self.update_scrollbar()

    def _on_scroll_changed(self, text_view, scroll_y, scroll_x):
        """Handle scroll changes (update scrollbar positions)."""
        # Update vertical scrollbar
        total_height = text_view._get_total_visual_lines() * text_view.line_height
        viewport_height = text_view.get_height()
        if total_height > viewport_height:
            v_adjustment = self.v_scrollbar.get_adjustment()
            v_adjustment.handler_block_by_func(self._on_v_scrollbar_changed)
            v_adjustment.set_value(scroll_y)
            v_adjustment.handler_unblock_by_func(self._on_v_scrollbar_changed)

        # Update horizontal scrollbar
        if not text_view.buffer.word_wrap:
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(text_view.buffer.total_lines)) * text_view.char_width + 10
            available_width = text_view.get_width() - line_num_width
            total_width = text_view.max_line_width
            h_adjustment = self.h_scrollbar.get_adjustment()
            h_adjustment.handler_block_by_func(self._on_h_scrollbar_changed)
            h_adjustment.set_value(scroll_x)
            h_adjustment.handler_unblock_by_func(self._on_h_scrollbar_changed)
        else:
            h_adjustment = self.h_scrollbar.get_adjustment()
            h_adjustment.handler_block_by_func(self._on_h_scrollbar_changed)
            h_adjustment.set_value(0)
            h_adjustment.handler_unblock_by_func(self._on_h_scrollbar_changed)

    def _on_modified_changed(self, text_view, is_modified):
        """Update the tab title and potentially a save button if added."""
        # Find the page associated with this tab content
        page = self.get_ancestor(Adw.TabPage)
        if page:
            title = page.get_title()
            if is_modified and not title.endswith("*"):
                page.set_title(title + " *")
                # Update window title if this is the active tab
                tab_view = self.get_ancestor(Adw.TabView)
                if tab_view and tab_view.get_selected_page() == page:
                     window = tab_view.get_root()
                     if window:
                         window.set_title(title + " *")
            elif not is_modified and title.endswith("*"):
                new_title = title[:-2]
                page.set_title(new_title)
                # Update window title if this is the active tab
                tab_view = self.get_ancestor(Adw.TabView)
                if tab_view and tab_view.get_selected_page() == page:
                     window = tab_view.get_root()
                     if window:
                         window.set_title(new_title)

    def _on_v_scrollbar_changed(self, adjustment):
        """Handle vertical scrollbar changes."""
        new_scroll_y = adjustment.get_value()
        current_scroll_x = self.text_view.scroll_x if hasattr(self.text_view, 'scroll_x') else 0
        self.text_view.set_scroll_position(new_scroll_y, current_scroll_x)

    def _on_h_scrollbar_changed(self, adjustment):
        """Handle horizontal scrollbar changes."""
        new_scroll_x = adjustment.get_value()
        current_scroll_y = self.text_view.scroll_y
        self.text_view.set_scroll_position(current_scroll_y, new_scroll_x)

    def update_scrollbar(self):
        """Update the scrollbar adjustments."""
        if not self.text_view.get_realized():
             return # Cannot update if not realized

        # Update vertical scrollbar
        total_height = self.text_view._get_total_visual_lines() * self.text_view.line_height
        viewport_height = self.text_view.get_height()
        v_adjustment = self.v_scrollbar.get_adjustment()
        v_adjustment.set_lower(0)
        v_adjustment.set_upper(max(total_height, viewport_height))
        v_adjustment.set_page_size(viewport_height)
        v_adjustment.set_step_increment(self.text_view.line_height)
        v_adjustment.set_page_increment(viewport_height * 0.9)
        v_adjustment.set_value(self.text_view.scroll_y)

        # Update horizontal scrollbar
        if not self.text_view.buffer.word_wrap:
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(self.text_view.buffer.total_lines)) * self.text_view.char_width + 10
            available_width = self.text_view.get_width() - line_num_width
            total_width = self.text_view.max_line_width
            h_adjustment = self.h_scrollbar.get_adjustment()
            h_adjustment.set_lower(0)
            h_adjustment.set_upper(max(total_width, available_width))
            h_adjustment.set_page_size(available_width)
            h_adjustment.set_step_increment(self.text_view.char_width * 10)
            h_adjustment.set_page_increment(available_width * 0.9)
            h_adjustment.set_value(self.text_view.scroll_x)
        self.update_scrollbar_visibility()

    def update_scrollbar_visibility(self):
        """Show or hide scrollbars based on content size."""
        if not self.text_view.get_realized():
             return

        # Vertical scrollbar visibility
        total_height = self.text_view._get_total_visual_lines() * self.text_view.line_height
        viewport_height = self.text_view.get_height()
        self.v_scrollbar.set_visible(total_height > viewport_height)

        # Horizontal scrollbar visibility
        if self.text_view.buffer.word_wrap:
            self.h_scrollbar.set_visible(False)
        else:
            # Narrower line numbers (20 -> 10)
            line_num_width = len(str(self.text_view.buffer.total_lines)) * self.text_view.char_width + 10
            available_width = self.text_view.get_width() - line_num_width
            total_width = self.text_view.max_line_width
            self.h_scrollbar.set_visible(total_width > available_width)


# --- Main Application Window with Tabs ---
class VirtualTextEditorWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Virtual Text Editor")
        self.set_default_size(1000, 700)

        # Main layout box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header Bar
        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        # Tab View
        self.tab_view = Adw.TabView()
        self.tab_view.connect("notify::selected-page", self.on_tab_switched)
        main_box.append(self.tab_view)

        # Wrap Tab Bar in a box to control its size and style
        tab_bar_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tab_bar_container.set_valign(Gtk.Align.START)

        # Tab Bar with smaller ta   bs
        self.tab_bar = Adw.TabBar.new()
        self.tab_bar.set_view(self.tab_view)
        # Use CSS to make tabs smaller
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            tab {
                min-height: 24px; /* Reduce minimum height */
                padding-top: 2px;
                padding-bottom: 2px;
            }
            tab button {
                min-width: 16px; /* Reduce close button size */
                min-height: 16px;
            }
            tab label {
                font-size: 0.9em; /* Slightly smaller font */
            }
        """)
        style_context = self.tab_bar.get_style_context()
        style_context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        tab_bar_container.append(self.tab_bar)
        main_box.insert_child_after(tab_bar_container, header_bar) # Place bar below header

        # Actions (for menu)
        self.new_tab_action = Gio.SimpleAction.new("new_tab", None)
        self.new_tab_action.connect("activate", self.on_new_tab)
        self.add_action(self.new_tab_action)

        self.close_tab_action = Gio.SimpleAction.new("close_tab", None)
        self.close_tab_action.connect("activate", self.on_close_tab)
        self.add_action(self.close_tab_action)

        # Menu Button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        header_bar.pack_end(menu_button)

        menu_model = Gio.Menu()
        menu_model.append("New Tab", "win.new_tab")
        menu_model.append("Close Tab", "win.close_tab")
        menu_model.append("Open File", "app.open_file")
        menu_model.append("Save File", "app.save_file")
        menu_model.append("Generate Test Data", "app.generate_test")
        menu_model.append("Go to Top", "app.go_top")
        menu_model.append("Go to Bottom", "app.go_bottom")
        menu_model.append("Toggle Word Wrap (Ctrl+W)", "app.toggle_wrap")
        menu_button.set_menu_model(menu_model)

        # Create the first tab
        self.create_new_tab()

    def create_new_tab(self, buffer=None, title="Untitled"):
        """Creates a new tab with a VirtualTextView."""
        text_view = VirtualTextView()
        if buffer:
            text_view.set_buffer(buffer)

        # Create the custom tab content container
        tab_content = TabContent(text_view)

        # Create a new page for the tab view using the custom content
        page = self.tab_view.append(tab_content)
        page.set_title(title)

        # Store a reference to the text view and tab content in the page for easy access
        setattr(page, 'text_view', text_view)
        setattr(page, 'tab_content', tab_content) # Store the TabContent instance

        # Select the new tab
        self.tab_view.set_selected_page(page)

        # Add actions to the window (these need to be reconnected for context menu)
        # They are added once in __init__, but we need to ensure they are active
        # The text view will look these up via get_root()
        self.cut_action = Gio.SimpleAction.new("cut", None)
        self.cut_action.connect("activate", self.on_cut)
        self.add_action(self.cut_action)

        self.copy_action = Gio.SimpleAction.new("copy", None)
        self.copy_action.connect("activate", self.on_copy)
        self.add_action(self.copy_action)

        self.paste_action = Gio.SimpleAction.new("paste", None)
        self.paste_action.connect("activate", self.on_paste)
        self.add_action(self.paste_action)

        self.delete_action = Gio.SimpleAction.new("delete", None)
        self.delete_action.connect("activate", self.on_delete)
        self.add_action(self.delete_action)

        self.select_all_action = Gio.SimpleAction.new("select_all", None)
        self.select_all_action.connect("activate", self.on_select_all)
        self.add_action(self.select_all_action)

        # Grab focus on the new text view
        text_view.grab_focus()

        return page

    def on_new_tab(self, action, param):
        """Action callback for creating a new tab."""
        self.create_new_tab()

    def on_close_tab(self, action, param):
        """Action callback for closing the current tab."""
        page = self.tab_view.get_selected_page()
        if page:
            self.tab_view.close_page(page)

    def on_tab_switched(self, tab_view, pspec):
        """Callback when the selected tab changes."""
        # Update window title based on the selected tab
        selected_page = self.tab_view.get_selected_page()
        if selected_page:
            title = selected_page.get_title()
            # The title update logic is now in TabContent._on_modified_changed
            # Just ensure scrollbars are updated for the new view
            selected_page.tab_content.update_scrollbar()

    def get_current_text_view(self):
        """Helper to get the VirtualTextView of the current tab."""
        page = self.tab_view.get_selected_page()
        if page:
            return page.text_view
        return None

    def on_cut(self, action, param):
        text_view = self.get_current_text_view()
        if text_view:
            text_view._cut_to_clipboard()
            text_view.grab_focus()

    def on_copy(self, action, param):
        text_view = self.get_current_text_view()
        if text_view:
            text_view._copy_to_clipboard()
            text_view.grab_focus()

    def on_paste(self, action, param):
        text_view = self.get_current_text_view()
        if text_view:
            text_view._paste_from_clipboard()
            text_view.grab_focus()

    def on_delete(self, action, param):
        text_view = self.get_current_text_view()
        if text_view:
            text_view._delete_selection()
            text_view.grab_focus()

    def on_select_all(self, action, param):
        text_view = self.get_current_text_view()
        if text_view:
            text_view._select_all()
            text_view.grab_focus()

    def generate_test_data(self):
        """Generate test data and load it into a new tab."""
        def generate_lines():
            lines = []
            for i in range(1000000):
                if i % 10000 == 0:
                    lines.append(f"=== Section {i//10000 + 1} === Line {i+1} ===")
                elif i % 1000 == 0:
                    lines.append(f"--- Subsection {i//1000 + 1} --- Line {i+1}")
                elif i % 100 == 0:
                    lines.append(f"Line {i+1}: This is a longer line with more content to test horizontal scrolling and text rendering performance in our virtual text view.")
                else:
                    lines.append(f"Line {i+1}: Sample text content for testing virtual scrolling")
            return lines
        def load_data():
            start_time = time.time()
            lines = generate_lines()
            load_time = time.time() - start_time
            GLib.idle_add(lambda: self._on_data_loaded(lines, load_time))
        # Use the status bar of the current tab
        text_view = self.get_current_text_view()
        if text_view:
            parent = text_view.get_ancestor(TabContent)
            if parent:
                parent.status_bar.set_text("Generating 1 million lines...")
        thread = threading.Thread(target=load_data)
        thread.daemon = True
        thread.start()

    def _on_data_loaded(self, lines, load_time):
        buffer = VirtualTextBuffer()
        buffer.load_lines(lines)
        page = self.create_new_tab(buffer, title=f"Test Data ({len(lines):,} lines)")
        # Update status bar of the new tab
        if page and hasattr(page, 'tab_content'):
            page.tab_content.status_bar.set_text(f"Loaded {len(lines):,} lines in {load_time:.2f}s - Use arrow keys, Page Up/Down, Ctrl+Home/End to navigate")

    def open_file(self):
        """Open a file dialog."""
        dialog = Gtk.FileChooserDialog(
            title="Open File",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Open", Gtk.ResponseType.ACCEPT
        )
        dialog.connect('response', self._on_file_dialog_response)
        dialog.present()

    def _on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = dialog.get_files() # Returns a GList
            if files:
                file = files[0] # Get the first file
                self._load_file(file.get_path())
        dialog.destroy()
        text_view = self.get_current_text_view()
        if text_view:
            text_view.grab_focus()

    def _load_file(self, filepath):
        """Load a file into a new tab in a background thread."""
        def load_file():
            try:
                start_time = time.time()
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    lines = [line.rstrip('\n\r') for line in f]
                load_time = time.time() - start_time
                GLib.idle_add(lambda: self._on_file_loaded(lines, load_time, filepath))
            except Exception as e:
                GLib.idle_add(lambda: self._on_file_error(str(e)))
        # Use the status bar of the current tab
        text_view = self.get_current_text_view()
        if text_view:
            parent = text_view.get_ancestor(TabContent)
            if parent:
                parent.status_bar.set_text(f"Loading {os.path.basename(filepath)}...")
        thread = threading.Thread(target=load_file)
        thread.daemon = True
        thread.start()

    def _on_file_loaded(self, lines, load_time, filepath):
        """Callback when a file is loaded."""
        buffer = VirtualTextBuffer()
        buffer.load_lines(lines)
        buffer.file_path = filepath
        filename = os.path.basename(filepath)
        page = self.create_new_tab(buffer, title=filename)
        # Update status bar of the new tab
        if page and hasattr(page, 'tab_content'):
            page.tab_content.status_bar.set_text(f"Loaded {filename} - {len(lines):,} lines in {load_time:.2f}s")

    def _on_file_error(self, error):
        """Callback when a file load error occurs."""
        # Use the status bar of the current tab
        text_view = self.get_current_text_view()
        if text_view:
            parent = text_view.get_ancestor(TabContent)
            if parent:
                parent.status_bar.set_text(f"Error loading file: {error}")
        text_view = self.get_current_text_view()
        if text_view:
            text_view.grab_focus()

    def go_to_top(self):
        """Scroll the current tab to the top."""
        text_view = self.get_current_text_view()
        if text_view:
            text_view.scroll_to_top()
            text_view.grab_focus()

    def go_to_bottom(self):
        """Scroll the current tab to the bottom."""
        text_view = self.get_current_text_view()
        if text_view:
            text_view.scroll_to_bottom()
            text_view.grab_focus()

    def save_file(self, file_path=None):
        """Save the current tab's buffer."""
        text_view = self.get_current_text_view()
        if not text_view:
            return
        buffer = text_view.buffer
        path = file_path or buffer.file_path
        if not path:
            self.save_file_as()
            return
        def save_in_thread():
            success = buffer.save_to_file(path)
            GLib.idle_add(lambda: self._on_file_saved(success, path))
        # Use the status bar of the current tab
        parent = text_view.get_ancestor(TabContent)
        if parent:
            parent.status_bar.set_text(f"Saving {os.path.basename(path)}...")
        thread = threading.Thread(target=save_in_thread)
        thread.daemon = True
        thread.start()

    def save_file_as(self):
        """Open a save-as dialog."""
        dialog = Gtk.FileChooserDialog(
            title="Save As",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Save", Gtk.ResponseType.ACCEPT
        )
        # Set default filename if buffer has one or is modified
        text_view = self.get_current_text_view()
        if text_view and text_view.buffer.file_path:
             dialog.set_current_name(os.path.basename(text_view.buffer.file_path))
        elif text_view and text_view.buffer.modified:
             dialog.set_current_name("Untitled.txt")

        dialog.connect('response', self._on_save_as_dialog_response)
        dialog.present()

    def _on_save_as_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = dialog.get_files()
            if files:
                file_path = files[0].get_path()
                if not os.path.splitext(file_path)[1]:
                    file_path += ".txt"
                self.save_file(file_path)
        dialog.destroy()
        text_view = self.get_current_text_view()
        if text_view:
            text_view.grab_focus()

    def _on_file_saved(self, success, file_path):
        """Callback when a file is saved."""
        if success:
            filename = os.path.basename(file_path)
            # Use the status bar of the current tab
            text_view = self.get_current_text_view()
            if text_view:
                parent = text_view.get_ancestor(TabContent)
                if parent:
                    parent.status_bar.set_text(f"Saved {filename}")
            # Update buffer's file path and clear modified flag
            text_view = self.get_current_text_view()
            if text_view:
                text_view.buffer.file_path = file_path
                text_view.buffer.modified = False
                # Update tab title
                page = self.tab_view.get_selected_page() # Get current page
                if page:
                    page.set_title(filename)
                    # Update window title
                    self.set_title(filename)
        else:
            # Use the status bar of the current tab
            text_view = self.get_current_text_view()
            if text_view:
                parent = text_view.get_ancestor(TabContent)
                if parent:
                    parent.status_bar.set_text("Error saving file.")
        text_view = self.get_current_text_view()
        if text_view:
            text_view.grab_focus()

# --- VirtualTextEditorApp updated for new actions ---
class VirtualTextEditorApp(Adw.Application):
    """Main application class"""
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.VirtualVirtualTextEditorWithTabs")
        self.connect('activate', self.on_activate)
        self._create_actions()

    def _create_actions(self):
        """Create application actions"""
        open_action = Gio.SimpleAction.new("open_file", None)
        open_action.connect("activate", self.on_open_file)
        self.add_action(open_action)

        save_action = Gio.SimpleAction.new("save_file", None)
        save_action.connect("activate", self.on_save_file)
        self.add_action(save_action)

        generate_action = Gio.SimpleAction.new("generate_test", None)
        generate_action.connect("activate", self.on_generate_test)
        self.add_action(generate_action)

        top_action = Gio.SimpleAction.new("go_top", None)
        top_action.connect("activate", self.on_go_top)
        self.add_action(top_action)

        bottom_action = Gio.SimpleAction.new("go_bottom", None)
        bottom_action.connect("activate", self.on_go_bottom)
        self.add_action(bottom_action)

        wrap_action = Gio.SimpleAction.new("toggle_wrap", None)
        wrap_action.connect("activate", self.on_toggle_wrap)
        self.add_action(wrap_action)

    def on_activate(self, app):
        """Application activate signal handler"""
        self.window = VirtualTextEditorWindow(application=app)
        self.window.present()
        self.window_ref = self.window # Keep a reference

    def on_open_file(self, action, param):
        """Open file action"""
        self.window_ref.open_file()

    def on_save_file(self, action, param):
        """Save file action"""
        self.window_ref.save_file()

    def on_generate_test(self, action, param):
        """Generate test data action"""
        self.window_ref.generate_test_data()

    def on_go_top(self, action, param):
        """Go to top action"""
        self.window_ref.go_to_top()

    def on_go_bottom(self, action, param):
        """Go to bottom action"""
        self.window_ref.go_to_bottom()

    def on_toggle_wrap(self, action, param):
        """Toggle word wrap action"""
        text_view = self.window_ref.get_current_text_view()
        if text_view:
            text_view.buffer.word_wrap = not text_view.buffer.word_wrap
            text_view._needs_wrap_recalc = True
            text_view._wrapped_lines_cache.clear()
            if text_view.buffer.word_wrap:
                 text_view.scroll_x = 0
            text_view.queue_draw()
            # Update scrollbar visibility via the TabContent
            page = text_view.get_ancestor(Adw.TabPage)
            if page and hasattr(page, 'tab_content'):
                page.tab_content.update_scrollbar_visibility()
            state = "On" if text_view.buffer.word_wrap else "Off"
            # Update status bar
            parent = text_view.get_ancestor(TabContent)
            if parent:
                parent.status_bar.set_text(f"Word Wrap: {state}")
            text_view.grab_focus()

def main():
    """Main entry point"""
    app = VirtualTextEditorApp()
    return app.run()

if __name__ == "__main__":
    main()

