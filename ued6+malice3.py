#!/usr/bin/env python3
import sys, os, mmap, re, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import cairo

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo 

# ============================================================
#   DETECT ENCODING (UTF-8 / UTF-8-BOM / UTF-16LE / UTF-16BE)
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
#   INDEXED FILE (mmap-based, UTF-16 safe)
# ============================================================

class IndexedFile:
    """Reads the file through mmap and builds an index of line boundaries.
       Works for UTF-8 and UTF-16 (both endiannesses)."""

    def __init__(self, path):
        self.path = path
        self.encoding = detect_encoding(path)
        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)
        self.index = []
        self._build()

    # ---------------------------------------------

    def _build(self):
        if self.encoding.startswith("utf-16"):
            self._index_utf16()
        else:
            self._index_utf8()

    # ---------------------------------------------

    def _index_utf8(self):
        mm = self.mm
        mm.seek(0)
        self.index = [0]
        while True:
            ln = mm.readline()
            if not ln:
                break
            self.index.append(mm.tell())

    # ---------------------------------------------

    def _index_utf16(self):
        raw = self.mm[:]
        text = raw.decode(self.encoding, errors="replace")

        w = 2   # UTF-16 code unit width
        offs = []

        for i, ch in enumerate(text):
            if ch == "\n":
                offs.append((i + 1) * w)

        offs.append(len(raw))
        self.index = [0] + offs

    # ---------------------------------------------

    def total_lines(self):
        return len(self.index) - 1

    # ---------------------------------------------

    def get_line_raw(self, line):
        if line < 0 or line >= self.total_lines():
            return ""

        s = self.index[line]
        e = self.index[line + 1]
        raw = self.mm[s:e]
        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")


# ============================================================
#   VIRTUAL ROPE BUFFER (true editable line model)
# ============================================================

class VirtualRopeBuffer(GObject.Object):

    __gsignals__ = {
        "changed":      (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__()
        self.indexed = None

        # rope-like storage
        self.lines = []
        self.mmap_refs = []   # True = mmap-backed, False = python string

        self.cursor_line = 0
        self.cursor_col = 0
        self.modified = False

    # ----------------------------------------------------------
    # LOAD FROM INDEXED FILE
    # ----------------------------------------------------------
    def load_indexed(self, indexed):
        self.indexed = indexed

        total = indexed.total_lines()

        self.lines = [indexed.get_line_raw(i) for i in range(total)]
        self.mmap_refs = [True] * total

        self.cursor_line = 0
        self.cursor_col = 0
        self.modified = False
        self.emit("changed")

    # ----------------------------------------------------------
    # SAFE ACCESS
    # ----------------------------------------------------------
    def total(self):
        return len(self.lines)

    def get_line(self, i):
        if 0 <= i < len(self.lines):
            return self.lines[i]
        return ""

    # ----------------------------------------------------------
    # MATERIALIZE
    # ----------------------------------------------------------
    def _materialize(self, i):
        """Convert mmap-backed line to real python string."""
        if i < 0 or i >= len(self.lines):
            return              # avoid crash, silently ignore

        if not self.mmap_refs[i]:
            return              # already materialized

        # Replace with Python string (it may still be from mmap)
        self.lines[i] = str(self.lines[i])
        self.mmap_refs[i] = False

    # ----------------------------------------------------------
    # CURSOR
    # ----------------------------------------------------------
    def set_cursor(self, line, col):
        if line < 0: line = 0
        if line >= len(self.lines): line = len(self.lines)-1

        row = self.lines[line]
        col = max(0, min(col, len(row)))

        self.cursor_line = line
        self.cursor_col = col
        self.emit("cursor-moved", line, col)

    # ----------------------------------------------------------
    # INSERT CHARACTER
    # ----------------------------------------------------------
    def insert_char(self, ch):
        l = self.cursor_line
        c = self.cursor_col

        self._materialize(l)
        row = self.lines[l]

        self.lines[l] = row[:c] + ch + row[c:]
        self.mmap_refs[l] = False

        self.cursor_col += len(ch)

        self.modified = True
        self.emit("changed")

    # ----------------------------------------------------------
    # NEWLINE SPLIT
    # ----------------------------------------------------------
    def insert_newline(self):
        l = self.cursor_line
        c = self.cursor_col

        self._materialize(l)
        row = self.lines[l]

        left = row[:c]
        right = row[c:]

        # Replace current line
        self.lines[l] = left
        self.mmap_refs[l] = False

        # Insert new line
        self.lines.insert(l+1, right)
        self.mmap_refs.insert(l+1, False)

        self.cursor_line = l + 1
        self.cursor_col = 0

        self.modified = True
        self.emit("changed")

    # ----------------------------------------------------------
    # BACKSPACE
    # ----------------------------------------------------------
    def backspace(self):
        l = self.cursor_line
        c = self.cursor_col

        # delete character inside line
        if c > 0:
            self._materialize(l)
            row = self.lines[l]

            self.lines[l] = row[:c - 1] + row[c:]
            self.mmap_refs[l] = False

            self.cursor_col -= 1
            self.modified = True
            self.emit("changed")
            return

        # merge with previous line
        if l == 0:
            return

        prev = l - 1

        self._materialize(prev)
        self._materialize(l)

        prev_len = len(self.lines[prev])

        self.lines[prev] = self.lines[prev] + self.lines[l]
        self.mmap_refs[prev] = False

        del self.lines[l]
        del self.mmap_refs[l]

        self.cursor_line = prev
        self.cursor_col = prev_len

        self.modified = True
        self.emit("changed")

    # ----------------------------------------------------------
    # DELETE (FORWARD DELETE)
    # ----------------------------------------------------------
    def delete(self):
        l = self.cursor_line
        c = self.cursor_col

        row = self.lines[l]

        if c < len(row):
            self._materialize(l)
            self.lines[l] = row[:c] + row[c+1:]
            self.mmap_refs[l] = False
            self.modified = True
            self.emit("changed")
            return

        # at end of line → merge with next
        if l + 1 >= len(self.lines):
            return

        self._materialize(l)
        self._materialize(l+1)

        self.lines[l] = self.lines[l] + self.lines[l+1]
        self.mmap_refs[l] = False

        del self.lines[l+1]
        del self.mmap_refs[l+1]

        self.modified = True
        self.emit("changed")

    # ----------------------------------------------------------
    # DELETE SELECTION
    # ----------------------------------------------------------
    def delete_selection(self, start, end):
        (sl, sc) = start
        (el, ec) = end

        # normalize order
        if (el, ec) < (sl, sc):
            sl, sc, el, ec = el, ec, sl, sc

        # single-line delete
        if sl == el:
            self._materialize(sl)
            row = self.lines[sl]

            self.lines[sl] = row[:sc] + row[ec:]
            self.mmap_refs[sl] = False

            self.cursor_line = sl
            self.cursor_col = sc

            self.modified = True
            self.emit("changed")
            return

        # multi-line delete
        self._materialize(sl)
        self._materialize(el)

        left = self.lines[sl][:sc]
        right = self.lines[el][ec:]

        # merge into a single line
        self.lines[sl] = left + right
        self.mmap_refs[sl] = False

        # remove middle lines
        del self.lines[sl+1:el+1]
        del self.mmap_refs[sl+1:el+1]

        self.cursor_line = sl
        self.cursor_col = sc

        self.modified = True
        self.emit("changed")

# ============================================================
#   SIMPLE AUTO-DETECT SYNTAX HIGHLIGHTER (Option L4)
# ============================================================

def detect_language_from_path(path):
    if not path:
        return "generic"

    lower = path.lower()

    if lower.endswith(".py"):
        return "python"

    if lower.endswith((".c", ".h", ".cpp", ".hpp", ".js", ".java")):
        return "clike"

    return "generic"


class SyntaxHighlighter:

    def __init__(self, lang="generic"):
        self.lang = lang
        self._compile_patterns()

    # --------------------------------------------------------

    def set_language(self, lang):
        if lang != self.lang:
            self.lang = lang
            self._compile_patterns()

    # --------------------------------------------------------

    def _compile_patterns(self):
        """Build regex tokens for chosen language."""

        if self.lang == "python":
            # strings, comments, keywords, numbers
            self.patterns = [
                ("comment", re.compile(r"#.*")),
                ("string",  re.compile(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")")),
                ("kw",      re.compile(r"\b(class|def|return|import|from|while|for|if|else|elif|try|except|with|as|pass|break|continue|lambda|yield)\b")),
                ("num",     re.compile(r"\b\d+(\.\d+)?\b")),
            ]

        elif self.lang == "clike":
            self.patterns = [
                ("comment", re.compile(r"//.*")),
                ("string",  re.compile(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")")),
                ("kw",      re.compile(r"\b(int|float|char|void|if|else|for|while|switch|case|break|continue|return|class|struct|static|public|private|protected|namespace)\b")),
                ("num",     re.compile(r"\b\d+(\.\d+)?\b")),
            ]

        else:
            # fallback: only strings and numbers
            self.patterns = [
                ("string", re.compile(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")")),
                ("num",    re.compile(r"\b\d+(\.\d+)?\b")),
            ]

    # --------------------------------------------------------

    def highlight(self, text):
        """Return list of (start, end, type)."""
        spans = []

        for token_type, regex in self.patterns:
            for m in regex.finditer(text):
                spans.append((m.start(), m.end(), token_type))

        # Sort in drawing order
        spans.sort(key=lambda x: x[0])
        return spans


# ============================================================
#   INPUT CONTROLLER (Selection + Cursor Motion)
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf
        self.sel_start = None
        self.sel_end = None
        self.selecting = False

    # --------------------------------------------------------

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.sel_start = (ln, col)
        self.sel_end = (ln, col)

    def drag(self, ln, col):
        if self.selecting:
            self.sel_end = (ln, col)

    def has_selection(self):
        return (
            self.sel_start
            and self.sel_end
            and self.sel_start != self.sel_end
        )

    def get_selection_range(self):
        (sl, sc) = self.sel_start
        (el, ec) = self.sel_end
        if (sl, sc) <= (el, ec):
            return (sl, sc), (el, ec)
        return (el, ec), (sl, sc)

    def clear_selection(self):
        cl = self.buf.cursor_line
        cc = self.buf.cursor_col
        self.sel_start = (cl, cc)
        self.sel_end = (cl, cc)

    # --------------------------------------------------------
    # CURSOR MOTION
    # --------------------------------------------------------

    def move_left(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col

        # collapse selection
        if not extend and self.has_selection():
            s, e = self.get_selection_range()
            b.set_cursor(s[0], s[1])
            self.clear_selection()
            return

        if c > 0:
            b.set_cursor(l, c - 1)
        elif l > 0:
            prev_line = b.get_line(l - 1)
            b.set_cursor(l - 1, len(prev_line))

        if extend:
            self.sel_end = (b.cursor_line, b.cursor_col)
        else:
            self.clear_selection()

    # --------------------------------------------------------

    def move_right(self, extend=False):
        b = self.buf
        l, c = b.cursor_line, b.cursor_col
        row = b.get_line(l)

        if not extend and self.has_selection():
            s, e = self.get_selection_range()
            b.set_cursor(e[0], e[1])
            self.clear_selection()
            return

        if c < len(row):
            b.set_cursor(l, c + 1)
        else:
            if l + 1 < b.total():
                b.set_cursor(l + 1, 0)

        if extend:
            self.sel_end = (b.cursor_line, b.cursor_col)
        else:
            self.clear_selection()

    # --------------------------------------------------------

    def move_up(self, extend=False):
        b = self.buf
        if b.cursor_line > 0:
            l = b.cursor_line - 1
            row = b.get_line(l)
            new_c = min(b.cursor_col, len(row))
            b.set_cursor(l, new_c)

            if extend:
                self.sel_end = (l, new_c)
            else:
                self.clear_selection()

    # --------------------------------------------------------

    def move_down(self, extend=False):
        b = self.buf
        l = b.cursor_line + 1
        if l < b.total():
            row = b.get_line(l)
            new_c = min(b.cursor_col, len(row))
            b.set_cursor(l, new_c)

            if extend:
                self.sel_end = (l, new_c)
            else:
                self.clear_selection()


# ============================================================
#   RENDERER (with syntax highlighting)
# ============================================================
 # already imported in your previous file; leaving here for clarity

class Renderer:

    def __init__(self, highlighter):
        self.font = Pango.FontDescription("Monospace 13")
        self.line_h = 21
        self.char_w = 9
        self.ln_width = 80

        # colors
        self.bg     = (0.10, 0.10, 0.10)
        self.fg     = (0.90, 0.90, 0.90)
        self.ln_fg  = (0.60, 0.60, 0.60)
        self.sel_bg = (0.25, 0.35, 0.55)

        self.color_kw      = (0.80, 0.45, 0.20)
        self.color_str     = (0.60, 0.80, 0.30)
        self.color_comment = (0.45, 0.55, 0.65)
        self.color_num     = (0.50, 0.65, 0.85)

        self.highlighter = highlighter

    # --------------------------------------------------------

    def draw(self, cr, alloc, buf, scroll_line, scroll_x, sel_s, sel_e):
        cr.set_source_rgb(*self.bg)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)

        total = buf.total()
        max_vis = alloc.height // self.line_h

        # selection range
        sel_range = None
        if sel_s and sel_e and sel_s != sel_e:
            if sel_s <= sel_e:
                sel_range = (sel_s, sel_e)
            else:
                sel_range = (sel_e, sel_s)

        # ------------------------
        # draw the lines
        # ------------------------

        y = 0
        for ln in range(scroll_line, min(scroll_line + max_vis, total)):
            text = buf.get_line(ln)

            # Selection background
            if sel_range:
                (sl, sc), (el, ec) = sel_range
                if sl <= ln <= el:
                    if sl == el:
                        xs = self.ln_width + sc * self.char_w - scroll_x
                        xe = self.ln_width + ec * self.char_w - scroll_x
                    elif ln == sl:
                        xs = self.ln_width + sc * self.char_w - scroll_x
                        xe = self.ln_width + (len(text) + 1) * self.char_w - scroll_x
                    elif ln == el:
                        xs = self.ln_width - scroll_x
                        xe = self.ln_width + ec * self.char_w - scroll_x
                    else:
                        xs = self.ln_width - scroll_x
                        xe = self.ln_width + max(1, len(text)) * self.char_w - scroll_x

                    cr.set_source_rgb(*self.sel_bg)
                    cr.rectangle(xs, y, xe - xs, self.line_h)
                    cr.fill()

            # Draw line number
            layout.set_text(str(ln + 1))
            cr.set_source_rgb(*self.ln_fg)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # Syntax highlighting
            spans = self.highlighter.highlight(text)
            last_x = 0

            for (start, end, token) in spans:
                if start > last_x:
                    # draw normal text segment
                    seg = text[last_x:start]
                    layout.set_text(seg)
                    cr.set_source_rgb(*self.fg)
                    cr.move_to(self.ln_width + (last_x * self.char_w) - scroll_x, y)
                    PangoCairo.show_layout(cr, layout)

                # colored token
                seg = text[start:end]
                layout.set_text(seg)

                if token == "kw":
                    cr.set_source_rgb(*self.color_kw)
                elif token == "string":
                    cr.set_source_rgb(*self.color_str)
                elif token == "comment":
                    cr.set_source_rgb(*self.color_comment)
                elif token == "num":
                    cr.set_source_rgb(*self.color_num)
                else:
                    cr.set_source_rgb(*self.fg)

                cr.move_to(self.ln_width + (start * self.char_w) - scroll_x, y)
                PangoCairo.show_layout(cr, layout)

                last_x = end

            # remainder
            if last_x < len(text):
                seg = text[last_x:]
                layout.set_text(seg)
                cr.set_source_rgb(*self.fg)
                cr.move_to(self.ln_width + last_x * self.char_w - scroll_x, y)
                PangoCairo.show_layout(cr, layout)

            y += self.line_h

        # ------------------------
        # draw cursor
        # ------------------------

        cl, cc = buf.cursor_line, buf.cursor_col

        if scroll_line <= cl < scroll_line + max_vis:
            cx = self.ln_width + cc * self.char_w - scroll_x
            cy = (cl - scroll_line) * self.line_h
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(cx, cy, 2, self.line_h)
            cr.fill()


# ============================================================
#   ULTRA VIEW (mouse, keys, scrolling)
# ============================================================

class UltraView(Gtk.DrawingArea):
    def __init__(self, buf, renderer):
        super().__init__()

        self.buf = buf
        self.renderer = renderer
        self.ctrl = InputController(self, buf)

        self.scroll_line = 0
        self.scroll_x = 0

        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.set_draw_func(self.on_draw)

        self.install_mouse()
        self.install_keys()
        self.install_scroll()

    # --------------------------------------------------------

    def on_draw(self, area, cr, width, height):
        alloc = area.get_allocation()
        self.renderer.draw(
            cr, alloc, self.buf,
            self.scroll_line, self.scroll_x,
            self.ctrl.sel_start, self.ctrl.sel_end
        )

    # ========================================================
    #   MOUSE
    # ========================================================

    def install_mouse(self):
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    # --------------------------------------------------------

    def on_click(self, gesture, n_press, x, y):
        self.grab_focus()

        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col = int((x - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(ln))))

        self.ctrl.click(ln, col)
        self.queue_draw()

    # --------------------------------------------------------

    def on_drag_begin(self, gesture, x, y):
        self.ctrl.selecting = True

    # --------------------------------------------------------

    def on_drag(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return

        ln = self.scroll_line + int((sy + dy) // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col = int((sx + dx - self.renderer.ln_width + self.scroll_x) //
                  self.renderer.char_w)
        col = max(0, min(col, len(self.buf.get_line(ln))))

        self.ctrl.drag(ln, col)
        self.buf.set_cursor(ln, col)
        self.queue_draw()

    # --------------------------------------------------------

    def on_drag_end(self, gesture, x, y):
        self.ctrl.selecting = False

    # ========================================================
    #   KEYBOARD
    # ========================================================

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        self.add_controller(key)

    # --------------------------------------------------------

    def on_key(self, c, keyval, keycode, state):
        name = Gdk.keyval_name(keyval)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        ctrl  = bool(state & Gdk.ModifierType.CONTROL_MASK)

        # ENTER
        if name in ("Return", "KP_Enter"):
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()

            self.buf.insert_newline()

        # BACKSPACE
        elif name == "BackSpace":
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            else:
                self.buf.backspace()

        # DELETE
        elif name == "Delete":
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            else:
                self.buf.delete_forward()

        # NAVIGATION
        elif name == "Left":
            self.ctrl.move_left(shift)
        elif name == "Right":
            self.ctrl.move_right(shift)
        elif name == "Up":
            self.ctrl.move_up(shift)
        elif name == "Down":
            self.ctrl.move_down(shift)

        # TEXT INPUT
        elif len(name) == 1 and not ctrl:
            if self.ctrl.has_selection():
                s, e = self.ctrl.get_selection_range()
                self.buf.delete_selection(s, e)
                self.ctrl.clear_selection()
            self.buf.insert_char(name)

        else:
            return False

        self.keep_cursor_visible()
        self.queue_draw()
        return True

    # --------------------------------------------------------

    def keep_cursor_visible(self):
        max_vis = self.get_allocated_height() // self.renderer.line_h
        cl = self.buf.cursor_line

        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

    # ========================================================
    #   SCROLL
    # ========================================================

    def install_scroll(self):
        flags = (
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc = Gtk.EventControllerScroll.new(flags)
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    # --------------------------------------------------------

    def on_scroll(self, controller, dx, dy):
        total = self.buf.total()
        vis = max(1, self.get_allocated_height() // self.renderer.line_h)
        max_scroll = max(0, total - vis)

        if dy:
            self.scroll_line = max(0, min(self.scroll_line + int(dy * 4), max_scroll))

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.queue_draw()
        return True


# ============================================================
#   ANIMATED VERTICAL SCROLLBAR (hover-expand)
# ============================================================

class VScrollbar(Gtk.DrawingArea):
    NORMAL_W = 8
    HOVER_W = 14

    def __init__(self, view):
        super().__init__()

        self.view = view
        self.hover = False

        self.set_hexpand(False)
        self.set_vexpand(True)
        self.set_draw_func(self.on_draw)

        # interactions
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.set_hover(True))
        motion.connect("leave", lambda *_: self.set_hover(False))
        self.add_controller(motion)

    # --------------------------------------------------------

    def set_hover(self, h):
        if self.hover != h:
            self.hover = h
            self.set_size_request(self.HOVER_W if h else self.NORMAL_W, -1)
            self.queue_draw()

    # --------------------------------------------------------

    def on_click(self, g, n, x, y):
        v = self.view
        total = v.buf.total()
        vis = max(1, v.get_allocated_height() // v.renderer.line_h)
        max_scroll = max(0, total - vis)

        h_alloc = self.get_allocated_height()
        thumb_h = max(20, h_alloc * (vis / total))
        track = h_alloc - thumb_h

        pos = y / max(1, track)
        v.scroll_line = int(pos * max_scroll)

        v.queue_draw()
        self.queue_draw()

    # --------------------------------------------------------

    def on_draw(self, area, cr, w, h):
        # background
        cr.set_source_rgba(0.2, 0.2, 0.2, 0.3)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        # thumb
        v = self.view
        total = v.buf.total()
        vis = max(1, v.get_allocated_height() // v.renderer.line_h)
        max_scroll = max(0, total - vis)

        thumb_h = max(20, h * (vis / total))
        pos = 0 if max_scroll == 0 else v.scroll_line / max_scroll
        y = pos * (h - thumb_h)

        cr.set_source_rgba(0.7, 0.7, 0.7, 0.8)
        cr.rectangle(2, y, w - 4, thumb_h)
        cr.fill()


# ============================================================
#   AUTO-APPEAR HORIZONTAL SCROLLBAR
# ============================================================

class HScrollbar(Gtk.DrawingArea):
    NORMAL_H = 8
    HOVER_H = 14

    def __init__(self, view):
        super().__init__()

        self.view = view
        self.hover = False

        self.set_vexpand(False)
        self.set_hexpand(True)
        self.set_draw_func(self.on_draw)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.set_hover(True))
        motion.connect("leave", lambda *_: self.set_hover(False))
        self.add_controller(motion)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

    # --------------------------------------------------------

    def set_hover(self, h):
        if self.hover != h:
            self.hover = h
            self.set_size_request(-1, self.HOVER_H if h else self.NORMAL_H)
            self.queue_draw()

    # --------------------------------------------------------

    def on_click(self, g, n, x, y):
        v = self.view
        view_chars = (v.get_allocated_width() - v.renderer.ln_width) // v.renderer.char_w

        max_x = max(v.renderer.char_w, v.renderer.char_w * 2000)
        max_scroll = max_x - view_chars
        if max_scroll <= 0:
            return

        w_alloc = self.get_allocated_width()
        thumb_w = max(30, w_alloc * (view_chars / max_x))
        track = w_alloc - thumb_w

        pos = x / max(1, track)
        char_pos = int(pos * max_scroll)
        v.scroll_x = char_pos * v.renderer.char_w

        v.queue_draw()
        self.queue_draw()

    # --------------------------------------------------------

    def on_draw(self, area, cr, w, h):
        v = self.view

        # Determine if needed
        view_chars = (v.get_allocated_width() - v.renderer.ln_width) // v.renderer.char_w
        max_chars = max(view_chars, 2000)  # constant wide space
        if max_chars <= view_chars:
            return  # not needed

        # background
        cr.set_source_rgba(0.2, 0.2, 0.2, 0.3)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        # thumb
        thumb_w = max(30, w * (view_chars / max_chars))
        max_scroll = max_chars - view_chars
        char_pos = v.scroll_x // v.renderer.char_w
        frac = char_pos / max_scroll if max_scroll > 0 else 0
        x = frac * (w - thumb_w)

        cr.set_source_rgba(0.7, 0.7, 0.7, 0.8)
        cr.rectangle(x, 2, thumb_w, h - 4)
        cr.fill()
# ============================================================
#   MAIN EDITOR WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)

        self.set_title("UltraEditor v6")
        self.set_default_size(1100, 800)

        self.current_file = None
        self.buf = VirtualRopeBuffer()
        self.highlighter = SyntaxHighlighter("generic")

        self.renderer = Renderer(self.highlighter)
        self.view = UltraView(self.buf, self.renderer)

        self.vscroll = VScrollbar(self.view)
        self.hscroll = HScrollbar(self.view)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        # Header Bar
        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.on_open)
        header.pack_start(open_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.connect("clicked", self.on_save)
        header.pack_start(save_btn)

        saveas_btn = Gtk.Button(label="Save As")
        saveas_btn.connect("clicked", self.on_save_as)
        header.pack_start(saveas_btn)

        # Grid layout: view + vscroll + hscroll
        grid = Gtk.Grid()
        grid.attach(self.view,    0, 0, 1, 1)
        grid.attach(self.vscroll, 1, 0, 1, 1)
        grid.attach(self.hscroll, 0, 1, 1, 1)

        layout.set_content(grid)

        # Update scrollbars on resize
        self.view.connect("resize", self._on_resize)
        self.buf.connect("changed", self._on_modified)

        # Inject CSS for animations
        self._install_css()

    # ========================================================
    #   CSS (smooth transitions)
    # ========================================================

    def _install_css(self):
        css = b"""
        * {
            transition: all 120ms ease;
        }
        """

        provider = Gtk.CssProvider()
        provider.load_from_data(css)

        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ========================================================
    #   EVENT HANDLERS
    # ========================================================

    def _on_resize(self, *args):
        self.vscroll.queue_draw()
        self.hscroll.queue_draw()

    def _on_modified(self, *args):
        if self.current_file:
            name = os.path.basename(self.current_file)
            if self.buf.modified:
                self.set_title("● " + name)
            else:
                self.set_title(name)

    # ========================================================
    #   FILE OPERATIONS
    # ========================================================

    def on_open(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return

            path = f.get_path()
            if not path:
                return

            idx = IndexedFile(path)
            self.buf.load_indexed(idx)
            self.current_file = path

            # Update syntax mode
            lang = detect_language_from_path(path)
            self.highlighter.set_language(lang)

            # Reset scroll
            self.view.scroll_line = 0
            self.view.scroll_x = 0

            self.set_title(os.path.basename(path))
            self.view.queue_draw()
            self.vscroll.queue_draw()
            self.hscroll.queue_draw()

        dialog.open(self, None, done)

    # --------------------------------------------------------

    def on_save(self, *_):
        if not self.current_file:
            self.on_save_as()
            return

        self._save_to(self.current_file)

    # --------------------------------------------------------

    def on_save_as(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.save_finish(result)
            except:
                return

            path = f.get_path()
            if path:
                self.current_file = path
                self._save_to(path)

        dialog.save(self, None, done)

    # --------------------------------------------------------

    def _save_to(self, path):
        try:
            text = "\n".join(self.buf.lines)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

            self.buf.modified = False
            self.set_title(os.path.basename(path))
        except Exception as e:
            print("Save error:", e)


# ============================================================
#   APPLICATION
# ============================================================

class UltraEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.ultraeditor.v6")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


# ============================================================
#   MAIN
# ============================================================

if __name__ == "__main__":
    app = UltraEditorApp()
    app.run(sys.argv)
