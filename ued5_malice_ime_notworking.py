#!/usr/bin/env python3
import sys, os, mmap, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo


# ============================================================
#   ENCODING DETECTION
# ============================================================

def detect_encoding(path):
    with open(path, "rb") as f:
        b = f.read(4)
    if b.startswith(b"\xff\xfe"):
        return "utf-16le"
    if b.startswith(b"\xfe\xff"):
        return "utf-16be"
    if b.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


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
        self.path = path
        self.encoding = detect_encoding(path)
        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)

        self.index = []
        self.index_file()

    def index_file(self):
        enc = self.encoding

        if enc.startswith("utf-16"):
            self._index_utf16()
        else:
            self._index_utf8()

    def _index_utf8(self):
        mm = self.mm
        mm.seek(0)
        self.index = [0]

        while True:
            pos = mm.tell()
            line = mm.readline()
            if not line:
                break
            self.index.append(mm.tell())

    def _index_utf16(self):
        raw = self.mm[:]
        text = raw.decode(self.encoding, errors="replace")

        self.index = [0]
        byte_offset = 0
        encoder = self.encoding

        for ch in text:
            ch_bytes = ch.encode(encoder, errors="replace")
            byte_offset += len(ch_bytes)
            if ch == "\n":
                self.index.append(byte_offset)

        if not self.index or self.index[-1] != len(raw):
            self.index.append(len(raw))



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
#   BUFFER
# ============================================================

class VirtualBuffer(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self):
        super().__init__()
        self.file = None            # IndexedFile
        self.edits = {}             # sparse: line_number → modified string
        self.cursor_line = 0
        self.cursor_col = 0

    def load(self, indexed_file):
        self.file = indexed_file
        self.edits.clear()
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def total(self):
        """Return total number of logical lines in the buffer.

        If a file is loaded, base it on file length and any edited lines.
        If no file is loaded, base it on edited lines (or at least 1).
        """
        if not self.file:
            # When editing an empty/new buffer, consider edits so added
            if not self.edits:
                return 1
            return max(1, max(self.edits.keys()) + 1)

        # File is present
        if not self.edits:
            return self.file.total_lines()

        max_edited = max(self.edits.keys())
        return max(self.file.total_lines(), max_edited + 1)


    def get_line(self, ln):
        if ln in self.edits:
            return self.edits[ln]
        if self.file:
            return self.file[ln] if 0 <= ln < self.file.total_lines() else ""
        return ""



    def set_cursor(self, ln, col):
        total = self.total()
        ln = max(0, min(ln, total - 1))
        line = self.get_line(ln)
        col = max(0, min(col, len(line)))
        self.cursor_line = ln
        self.cursor_col = col

    # ------- Editing ----------
    def insert_text(self, text):
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        new_line = line[:col] + text + line[col:]
        self.edits[ln] = new_line

        self.cursor_col = col + len(text)
        self.emit("changed")

    def backspace(self):
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        if col == 0:
            return

        new_line = line[:col-1] + line[col:]
        self.edits[ln] = new_line

        self.cursor_col = col - 1
        self.emit("changed")

    def insert_newline(self):
        ln = self.cursor_line
        col = self.cursor_col

        old_line = self.get_line(ln)
        left = old_line[:col]
        right = old_line[col:]

        # Put the left part into edits (replaces or creates this line)
        self.edits[ln] = left

        # Shift ONLY edited lines that come AFTER ln
        shifted = {}
        for k, v in self.edits.items():
            if k > ln:
                shifted[k + 1] = v
            else:
                shifted[k] = v

        # Insert new blank line or right side of old line
        shifted[ln + 1] = right

        self.edits = shifted

        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.emit("changed")




# ============================================================
#   INPUT
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf
        self.sel_start = None
        self.sel_end = None

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.sel_start = (ln, col)
        self.sel_end = (ln, col)

    def drag(self, ln, col):
        self.sel_end = (ln, col)

    def move_left(self):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        if col > 0:
            b.set_cursor(ln, col - 1)
        elif ln > 0:
            prev = b.get_line(ln - 1)
            b.set_cursor(ln - 1, len(prev))

    def move_right(self):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        if col < len(line):
            b.set_cursor(ln, col + 1)
        elif ln + 1 < b.total():
            b.set_cursor(ln + 1, 0)

    def move_up(self):
        b = self.buf
        ln = b.cursor_line
        if ln > 0:
            target = ln - 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)))

    def move_down(self):
        b = self.buf
        ln = b.cursor_line
        if ln + 1 < b.total():
            target = ln + 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)))


# ============================================================
#   RENDERER
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 22
        self.char_w = 10
        self.ln_width = 70

        # Clear semantic names
        self.editor_background_color = (0.10, 0.10, 0.10)
        self.text_foreground_color   = (0.50, 0.50, 0.50)
        self.linenumber_foreground_color = (6.0, 0.60, 0.60)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        # Background
        cr.set_source_rgb(*self.editor_background_color)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        total = buf.total()
        max_vis = (alloc.height // self.line_h) + 1

        y = 0
        for ln in range(scroll_line, min(scroll_line + max_vis, total)):
            text = buf.get_line(ln)

            # Line number
            layout.set_text(str(ln + 1), -1)
            cr.set_source_rgb(*self.linenumber_foreground_color)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # Line text
            layout.set_text(text, -1)
            cr.set_source_rgb(*self.text_foreground_color)
            cr.move_to(self.ln_width - scroll_x, y)
            PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # Cursor
        cl, cc = buf.cursor_line, buf.cursor_col
        if scroll_line <= cl < scroll_line + max_vis:
            cy = (cl - scroll_line) * self.line_h
            cx = self.ln_width + (cc * self.char_w) - scroll_x
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()




# ============================================================
#   VIEW
# ============================================================

class UltraView(Gtk.DrawingArea):
    def __init__(self, buf):
        super().__init__()
        self.buf = buf
        self.renderer = Renderer()
        self.ctrl = InputController(self, buf)
        self.scroll_line = 0
        self.scroll_x = 0

        self.set_focusable(True)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_draw_func(self.draw_view)

        self.install_mouse()
        self.install_keys()
        self.install_scroll()

        self.editing = False

        self._setup_ime()
        self.im_context.set_client_widget(self)
        self.im_context.connect("commit", self.on_commit)

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
    def on_commit(self, im, text):
        self.buf.insert_text(text)
        self.keep_cursor_visible()
        self.queue_draw()


    def install_mouse(self):
        g = Gtk.GestureClick()
        g.connect("pressed", self.on_click)
        self.add_controller(g)

        d = Gtk.GestureDrag()
        d.connect("drag-update", self.on_drag)
        self.add_controller(d)

    def on_click(self, g, n, x, y):
        self.grab_focus()
        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col = int((x - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        line = self.buf.get_line(ln)
        col = max(0, min(col, len(line)))

        self.ctrl.click(ln, col)
        self.queue_draw()

    def on_drag(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        ln = self.scroll_line + int((sy + dy) // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col = int((sx + dx - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        line = self.buf.get_line(ln)
        col = max(0, min(col, len(line)))

        self.ctrl.drag(ln, col)
        self.queue_draw()

    def _on_key_released(self, controller, keyval, keycode, state):
        """Handle key release - may be needed for some IME implementations"""
        if self.im_context:
            event = controller.get_current_event()
            return self.im_context.filter_keypress(event)
        return False

    def _on_im_commit(self, im_context, text):
        self.buf.insert_text(text)
        self.keep_cursor_visible()
        self.queue_draw()

    def _on_key_pressed(self, controller, keyval, keycode, state):
        # Force focus retention and finish editing when navigating
        navigation_focus_keys = {Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down, Gdk.KEY_Home, Gdk.KEY_End}
        if keyval in navigation_focus_keys:
            if getattr(self, 'editing', False):
                # finish inline editing so navigation affects main view
                try:
                    self._finish_editing()
                except Exception as e:
                    print('Error finishing edit before navigation:', e)
            self.grab_focus()
        
    
        """Handle key press events"""
        # Get the actual event for IME processing
        event = controller.get_current_event()
        print(f"Key pressed: {keyval} ({Gdk.keyval_name(keyval)}) keycode: {keycode}")
        # CRITICAL: Let IME handle the input first for all keys except special navigation
        # Only bypass IME for certain control keys
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
        alt_pressed = state & Gdk.ModifierType.ALT_MASK
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
        # Commit preedit before handling selection or global shortcuts
        navigation_keys = {Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Home, Gdk.KEY_End}
        if self.in_preedit and (ctrl_pressed or (shift_pressed and keyval in navigation_keys)):
            self._on_im_commit(self.im_context, self.preedit_string)
            self.im_context.reset()
            self.in_preedit = False
            self.preedit_string = ""
            self.preedit_cursor_pos = 0
            self.queue_draw()
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
            elif keyval == Gdk.KEY_z:
                # Ensure last word committed before undo
                try:
                    self._commit_word_to_undo()
                except Exception:
                    pass
                # Undo
                result = self.buffer.undo()
                if result:
                    # Handle new return format with cursor and selection
                    line, col = result['cursor']
                    self.cursor_line = line
                    self.cursor_col = col
                    
                    # Restore selection state
                    sel_state = result['selection']
                    self.has_selection = sel_state['has_selection']
                    if self.has_selection:
                        self.selection_start_line = sel_state['start_line']
                        self.selection_start_col = sel_state['start_col']
                        self.selection_end_line = sel_state['end_line']
                        self.selection_end_col = sel_state['end_col']
                    
                    # Cancel editing mode
                    if self.editing:
                        self.editing = False
                    self._ensure_cursor_visible()
                    self.emit('buffer-changed')
                    self.queue_draw()
                return True
            elif keyval == Gdk.KEY_y:
                # Redo (Ctrl+Y)
                result = self.buffer.redo()
                if result:
                    # Handle new return format with cursor and selection
                    line, col = result['cursor']
                    self.cursor_line = line
                    self.cursor_col = col
                    
                    # Restore selection state
                    sel_state = result['selection']
                    self.has_selection = sel_state['has_selection']
                    if self.has_selection:
                        self.selection_start_line = sel_state['start_line']
                        self.selection_start_col = sel_state['start_col']
                        self.selection_end_line = sel_state['end_line']
                        self.selection_end_col = sel_state['end_col']
                    
                    # Cancel editing mode
                    if self.editing:
                        self.editing = False
                    self._ensure_cursor_visible()
                    self.emit('buffer-changed')
                    self.queue_draw()
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
                return True
        # Handle Ctrl+Shift shortcuts
        if ctrl_pressed and shift_pressed and not alt_pressed:
            if keyval == Gdk.KEY_z or keyval == Gdk.KEY_Z:
                # Ensure last typed word is committed before Redo (Ctrl+Shift+Z)
                self._commit_word_to_undo()            
                # Redo (Ctrl+Shift+Z)
                result = self.buffer.redo()
                if result:
                    # Handle new return format with cursor and selection
                    line, col = result['cursor']
                    self.cursor_line = line
                    self.cursor_col = col
                    
                    # Restore selection state
                    sel_state = result['selection']
                    self.has_selection = sel_state['has_selection']
                    if self.has_selection:
                        self.selection_start_line = sel_state['start_line']
                        self.selection_start_col = sel_state['start_col']
                        self.selection_end_line = sel_state['end_line']
                        self.selection_end_col = sel_state['end_col']
                    
                    # Cancel editing mode
                    if self.editing:
                        self.editing = False
                    self._ensure_cursor_visible()
                    self.emit('buffer-changed')
                    self.queue_draw()
                return True
        
        # Handle Tab key - insert tab character instead of changing focus
        if keyval == Gdk.KEY_Tab and not ctrl_pressed and not alt_pressed:
            if self.has_selection:
                self._delete_selection()
            if not self.editing:
                self._start_editing()
            if self.editing:
                # Save cursor state before tab insertion
                saved_cursor_line = self.cursor_line
                saved_cursor_col = self.cursor_col
                
                # Add undo action for tab insertion
                old_text = self.edit_text
                self.buffer.add_undo_action('insert', {
                    'line': self.edit_line,
                    'start': self.edit_cursor_pos,
                    'end': self.edit_cursor_pos + 1,
                    'text': '\t'
                }, cursor_line=saved_cursor_line, cursor_col=self.edit_cursor_pos,
                   selection_start_line=None, selection_start_col=None,
                   selection_end_line=None, selection_end_col=None,
                   has_selection=False)
                self.edit_text = self.edit_text[:self.edit_cursor_pos] + '\t' + self.edit_text[self.edit_cursor_pos:]
                self.edit_cursor_pos += 1
                self.cursor_col = self.edit_cursor_pos
                self._ensure_cursor_visible()
                self.queue_draw()
                self.emit('modified-changed', self.buffer.modified)
            return True
        
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
        # Handle navigation and special keys if not in editing
        if not self.editing:
            # Commit any pending word when navigating away
            if keyval in [Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down]:
                self._commit_word_to_undo()
            
            if keyval in [Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right]:
                if shift_pressed and not self.has_selection:
                    current_line_text = self.buffer.get_line(self.cursor_line)
                    # If current line is empty, adjust selection start based on direction
                    if len(current_line_text.strip()) == 0:
                        if keyval == Gdk.KEY_Down and self.cursor_line < self.buffer.total_lines - 1:
                            # Moving down from empty line - start selection from next line
                            self.selection_start_line = self.cursor_line + 1
                            self.selection_start_col = 0
                        elif keyval == Gdk.KEY_Up and self.cursor_line > 0:
                            # Moving up from empty line - start selection from end of previous line
                            prev_line_text = self.buffer.get_line(self.cursor_line - 1)
                            self.selection_start_line = self.cursor_line - 1
                            self.selection_start_col = len(prev_line_text)
                        else:
                            # Left/Right or can't adjust
                            self.selection_start_line = self.cursor_line
                            self.selection_start_col = self.cursor_col
                    else:
                        self.selection_start_line = self.cursor_line
                        self.selection_start_col = self.cursor_col
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
                self.scroll_by_lines(-self.visible_lines)
                return True
            elif keyval == Gdk.KEY_Page_Down:
                self.scroll_by_lines(self.visible_lines)
                return True
            elif keyval in [Gdk.KEY_Home, Gdk.KEY_End]:
                if shift_pressed and not self.has_selection:
                    current_line_text = self.buffer.get_line(self.cursor_line)
                    # If current line is empty, don't start selection from it
                    if len(current_line_text.strip()) == 0:
                        if keyval == Gdk.KEY_Home:
                            # For Home, stay at current position
                            self.selection_start_line = self.cursor_line
                            self.selection_start_col = 0
                        else:  # End key
                            # For End, stay at current position
                            self.selection_start_line = self.cursor_line
                            self.selection_start_col = 0
                    else:
                        self.selection_start_line = self.cursor_line
                        self.selection_start_col = self.cursor_col
                if keyval == Gdk.KEY_Home:
                    if ctrl_pressed:
                        if shift_pressed:
                            self.selection_end_line = 0
                            self.selection_end_col = 0
                            self.has_selection = True
                        self.scroll_to_top()
                        self.cursor_line = 0
                        self.cursor_col = 0
                    else:
                        if shift_pressed:
                            self.selection_end_col = 0
                            self.has_selection = True
                        self.cursor_col = 0
                elif keyval == Gdk.KEY_End:
                    if ctrl_pressed:
                        if shift_pressed:
                            self.selection_end_line = max(0, self.buffer.total_lines - 1)
                            self.selection_end_col = len(self.buffer.get_line(self.selection_end_line))
                            self.has_selection = True
                        self.scroll_to_bottom()
                        self.cursor_line = max(0, self.buffer.total_lines - 1)
                        self.cursor_col = len(self.buffer.get_line(self.cursor_line))
                    else:
                        if shift_pressed:
                            self.selection_end_col = len(self.buffer.get_line(self.cursor_line))
                            self.has_selection = True
                        self.cursor_col = len(self.buffer.get_line(self.cursor_line))
                if shift_pressed:
                    self.selection_end_line = self.cursor_line
                    self.selection_end_col = self.cursor_col
                    self.has_selection = True
                self._ensure_cursor_visible()
                self.queue_draw()
                return True
            elif keyval == Gdk.KEY_Return:
                # Commit any pending word
                self._commit_word_to_undo()
                
                # Save cursor state before Enter
                saved_cursor_line = self.cursor_line
                saved_cursor_col = self.cursor_col
                
                if self.has_selection:
                    self._delete_selection()
                current_line_text = self.buffer.get_line(self.cursor_line)
                part1 = current_line_text[:self.cursor_col]
                part2 = current_line_text[self.cursor_col:]
                
                # Add undo for line split
                self.buffer.add_undo_action('insert_line', {
                    'line': self.cursor_line + 1,
                    'text': part2
                }, cursor_line=saved_cursor_line, cursor_col=saved_cursor_col,
                   selection_start_line=None, selection_start_col=None,
                   selection_end_line=None, selection_end_col=None,
                   has_selection=False)
                
                self.buffer.set_line(self.cursor_line, part1)
                self.buffer.insert_line(self.cursor_line + 1, part2)
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
                    # Check if we have an empty line selection (0-0)
                    bounds = self._get_selection_bounds()
                    if bounds:
                        start_line, start_col, end_line, end_col = bounds
                        # Check if this is an empty line selection (single line, 0-0 or 0-huge)
                        if start_line == end_line:
                            line_text = self.buffer.get_line(start_line)
                            # Empty line selection: 0-0 or 0-huge on empty line
                            if len(line_text) == 0 and start_col == 0:
                                # Delete the empty line itself
                                if self.buffer.total_lines > 1:
                                    # Save state for undo
                                    saved_cursor_line = self.cursor_line
                                    saved_cursor_col = self.cursor_col
                                    
                                    # Add undo action
                                    self.buffer.add_undo_action('delete_line', {
                                        'line': start_line,
                                        'text': ''
                                    }, cursor_line=saved_cursor_line, cursor_col=saved_cursor_col,
                                       selection_start_line=self.selection_start_line,
                                       selection_start_col=self.selection_start_col,
                                       selection_end_line=self.selection_end_line,
                                       selection_end_col=self.selection_end_col,
                                       has_selection=True)
                                    
                                    self.buffer.delete_line(start_line)
                                    # Position cursor at the same line (or previous if we deleted last line)
                                    self.cursor_line = min(start_line, self.buffer.total_lines - 1)
                                    self.cursor_col = 0
                                    self.has_selection = False
                                    self.selection_start_line = -1
                                    self.selection_start_col = -1
                                    self.selection_end_line = -1
                                    self.selection_end_col = -1
                                    self._ensure_cursor_visible()
                                    self.emit('buffer-changed')
                                    self.emit('modified-changed', self.buffer.modified)
                                    self.queue_draw()
                                    return True
                    
                    # Normal selection deletion
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
                # Commit any pending word
                self._commit_word_to_undo()
                
                # Save cursor state before Enter
                saved_cursor_line = self.cursor_line
                saved_cursor_col = self.edit_cursor_pos
                
                if self.has_selection:
                    self._delete_selection()
                current_edit_cursor_pos = self.edit_cursor_pos
                self._finish_editing()
                current_full_text = self.buffer.get_line(self.cursor_line)
                part1 = current_full_text[:current_edit_cursor_pos]
                part2 = current_full_text[current_edit_cursor_pos:]
                
                # Add undo for line split
                self.buffer.add_undo_action('insert_line', {
                    'line': self.cursor_line + 1,
                    'text': part2
                }, cursor_line=saved_cursor_line, cursor_col=saved_cursor_col,
                   selection_start_line=None, selection_start_col=None,
                   selection_end_line=None, selection_end_col=None,
                   has_selection=False)
                
                self.buffer.set_line(self.cursor_line, part1)
                self.buffer.insert_line(self.cursor_line + 1, part2)
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
                shift_pressed = state & Gdk.ModifierType.SHIFT_MASK
                ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
                if shift_pressed:
                    if not self.has_selection:
                        self.selection_start_line = self.cursor_line
                        self.selection_start_col = self.edit_cursor_pos
                        self.selection_end_line = self.cursor_line
                        self.selection_end_col = self.edit_cursor_pos
                        self.has_selection = True
                else:
                    self.has_selection = False
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
                if shift_pressed:
                    self.selection_end_col = self.edit_cursor_pos
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
                        # Commit any pending insert word
                        if self.last_action_was_insert:
                            self._commit_word_to_undo()
                        
                        deleted_char = self.edit_text[self.edit_cursor_pos-1]
                        is_boundary = self._is_word_boundary(deleted_char)
                        
                        # Track deletion for undo
                        if not self.last_action_was_delete or is_boundary:
                            # Start new delete undo action or single-char boundary delete
                            if is_boundary and deleted_char.strip():
                                # Single punctuation
                                self.buffer.add_undo_action('delete', {
                                    'line': self.edit_line,
                                    'pos': self.edit_cursor_pos - 1,
                                    'end': self.edit_cursor_pos,
                                    'text': deleted_char
                                })
                            else:
                                # Start tracking word deletion
                                self.word_buffer = deleted_char
                                self.word_start_col = self.edit_cursor_pos - 1
                                self.word_start_line = self.edit_line
                                self.last_action_was_delete = True
                                self.last_action_was_insert = False
                        else:
                            # Continue tracking word deletion
                            self.word_buffer = deleted_char + self.word_buffer
                            self.word_start_col = self.edit_cursor_pos - 1
                        
                        # Perform deletion
                        self.edit_text = (self.edit_text[:self.edit_cursor_pos-1] + self.edit_text[self.edit_cursor_pos:])
                        self.edit_cursor_pos -= 1
                        self.cursor_col = self.edit_cursor_pos
                        self.buffer.set_line(self.edit_line, self.edit_text)
                        
                        # Commit delete word if we hit a boundary
                        if is_boundary and self.word_buffer and len(self.word_buffer) > 1:
                            self.buffer.add_undo_action('delete', {
                                'line': self.word_start_line,
                                'pos': self.word_start_col,
                                'end': self.word_start_col + len(self.word_buffer),
                                'text': self.word_buffer
                            })
                            self.word_buffer = ""
                            self.last_action_was_delete = False
                        
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
                        # Commit any pending word
                        self._commit_word_to_undo()
                        
                        prev_line_text = self.buffer.get_line(self.edit_line - 1)
                        current_line_text = self.edit_text
                        
                        # Add undo for line merge
                        self.buffer.add_undo_action('delete_line', {
                            'line': self.edit_line,
                            'text': current_line_text
                        })
                        
                        merged_text = prev_line_text + current_line_text
                        self.buffer.set_line(self.edit_line - 1, merged_text)
                        self.buffer.delete_line(self.edit_line)
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
                        # Commit any pending insert word
                        if self.last_action_was_insert:
                            self._commit_word_to_undo()
                        
                        deleted_char = self.edit_text[self.edit_cursor_pos]
                        is_boundary = self._is_word_boundary(deleted_char)
                        
                        # Track deletion for undo
                        if not self.last_action_was_delete or is_boundary:
                            # Start new delete undo action or single-char boundary delete
                            if is_boundary and deleted_char.strip():
                                # Single punctuation
                                self.buffer.add_undo_action('delete', {
                                    'line': self.edit_line,
                                    'pos': self.edit_cursor_pos,
                                    'end': self.edit_cursor_pos + 1,
                                    'text': deleted_char
                                })
                            else:
                                # Start tracking word deletion
                                self.word_buffer = deleted_char
                                self.word_start_col = self.edit_cursor_pos
                                self.word_start_line = self.edit_line
                                self.last_action_was_delete = True
                                self.last_action_was_insert = False
                        else:
                            # Continue tracking word deletion (forward delete)
                            self.word_buffer = self.word_buffer + deleted_char
                        
                        # Perform deletion
                        self.edit_text = (self.edit_text[:self.edit_cursor_pos] + self.edit_text[self.edit_cursor_pos+1:])
                        self.buffer.set_line(self.edit_line, self.edit_text)
                        
                        # Commit delete word if we hit a boundary
                        if is_boundary and self.word_buffer and len(self.word_buffer) > 1:
                            self.buffer.add_undo_action('delete', {
                                'line': self.word_start_line,
                                'pos': self.word_start_col,
                                'end': self.word_start_col + len(self.word_buffer),
                                'text': self.word_buffer
                            })
                            self.word_buffer = ""
                            self.last_action_was_delete = False
                        
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
                        # Commit any pending word
                        self._commit_word_to_undo()
                        
                        next_line_text = self.buffer.get_line(self.edit_line + 1)
                        current_line_text = self.edit_text
                        
                        # Add undo for line merge
                        self.buffer.add_undo_action('delete_line', {
                            'line': self.edit_line + 1,
                            'text': next_line_text
                        })
                        
                        merged_text = current_line_text + next_line_text
                        self.buffer.set_line(self.edit_line, merged_text)
                        self.buffer.delete_line(self.edit_line + 1)
                        self.editing = False
                        self._ensure_cursor_visible()
                        self.queue_draw()
                        self.emit('buffer-changed')
                        self.emit('modified-changed', self.buffer.modified)
                return True
        # If we get here, the key wasn't handled
        print(f"Key not handled: {keyval}")
        return False


    def keep_cursor_visible(self):
        max_vis = max(1, (self.get_height() // self.renderer.line_h) + 1)

        cl = self.buf.cursor_line
        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

        if self.scroll_line < 0:
            self.scroll_line = 0


        self.scroll_line = max(0, self.scroll_line)

    def install_keys(self):
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        # Also handle key release for completeness
        key_controller.connect('key-released', self._on_key_released)
        self.add_controller(key_controller)
        
    def install_scroll(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        total = self.buf.total()
        max_vis = max(1, (self.get_allocated_height() // self.renderer.line_h) + 1)
        max_scroll = max(0, total - max_vis)

        if dy:
            self.scroll_line = max(
                0,
                min(self.scroll_line + int(dy * 4), max_scroll)
            )

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True


    def draw_view(self, area, cr, w, h):
        cr.set_source_rgb(0.10, 0.10, 0.10)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        alloc = type("Alloc", (), {"width": w, "height": h})

        self.renderer.draw(
            cr,
            alloc,
            self.buf,
            self.scroll_line,
            self.scroll_x,
            self.ctrl.sel_start,
            self.ctrl.sel_end
        )



# ============================================================
#   SCROLLBAR (simple)
# ============================================================

class VirtualScrollbar(Gtk.DrawingArea):
    def __init__(self, view):
        super().__init__()
        self.view = view

        self.set_size_request(14, -1)
        self.set_vexpand(True)
        self.set_hexpand(False)

        self.set_draw_func(self.draw_scrollbar)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self.on_drag)
        self.add_controller(drag)

        self.dragging = False

    def draw_scrollbar(self, area, cr, w, h):
        cr.set_source_rgb(0.20, 0.20, 0.20)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        view = self.view
        total = view.buf.total()

        max_vis = max(1, (view.get_height() // view.renderer.line_h) + 1)
        max_scroll = max(0, total - max_vis)

        thumb_h = max(20, h * (max_vis / total))
        pos = 0 if max_scroll == 0 else (view.scroll_line / max_scroll)
        y = pos * (h - thumb_h)

        cr.set_source_rgb(0.55, 0.55, 0.55)
        cr.rectangle(0, y, w, thumb_h)
        cr.fill()


    def on_click(self, g, n_press, x, y):
        self.start_y = y
        self.dragging = True

    def on_drag(self, g, dx, dy):
        if not self.dragging:
            return

        view = self.view
        h = self.get_allocated_height()
        total = view.buf.total()
        max_vis = max(1, view.get_allocated_height() // view.renderer.line_h)
        max_scroll = max(0, total - max_vis)

        thumb_h = max(20, h * (max_vis / total))
        track = h - thumb_h
        frac = (self.start_y + dy) / track
        frac = max(0, min(1, frac))

        view.scroll_line = int(frac * max_scroll)
        view.queue_draw()
        self.queue_draw()


# ============================================================
#   WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("UltraEditor v4 — Full Indexing, UTF16-Safe")
        self.set_default_size(1000, 700)

        self.buf = VirtualBuffer()
        self.view = UltraView(self.buf)
        self.scrollbar = VirtualScrollbar(self.view)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(self.view)
        box.append(self.scrollbar)

        layout.set_content(box)

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return
            path = f.get_path()

            idx = IndexedFile(path)
            self.buf.load(idx)
            self.view.scroll_line = 0
            self.view.scroll_x = 0

            self.view.queue_draw()
            self.scrollbar.queue_draw()

            self.set_title(os.path.basename(path))

        dialog.open(self, None, done)


# ============================================================
#   APP
# ============================================================

class UltraEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v4")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    UltraEditor().run(sys.argv)
