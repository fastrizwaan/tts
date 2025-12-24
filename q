[1mdiff --git a/svite/__pycache__/word_wrap.cpython-314.pyc b/svite/__pycache__/word_wrap.cpython-314.pyc[m
[1mindex df65920..dbedcb6 100644[m
Binary files a/svite/__pycache__/word_wrap.cpython-314.pyc and b/svite/__pycache__/word_wrap.cpython-314.pyc differ
[1mdiff --git a/svite/svite.py b/svite/svite.py[m
[1mindex a5bb048..374994e 100755[m
[1m--- a/svite/svite.py[m
[1m+++ b/svite/svite.py[m
[36m@@ -896,32 +896,45 @@[m [mclass InputController:[m
         ln, col = b.cursor_line, b.cursor_col[m
         [m
         if not extend_selection and b.selection.has_selection():[m
[31m-            # Move to start of selection[m
[32m+[m[32m            # Standard behavior: Collapse to start (logical)[m
[32m+[m[32m            # TODO: Should ideally collapse to Visual Left[m
             start_ln, start_col, _, _ = b.selection.get_bounds()[m
             b.set_cursor(start_ln, start_col, extend_selection)[m
[31m-        elif col > 0:[m
[31m-            # Move left within line[m
[31m-            b.set_cursor(ln, col - 1, extend_selection)[m
[31m-        elif ln > 0:[m
[31m-            # At start of line - move to end of previous line (selecting the newline)[m
[31m-            prev = b.get_line(ln - 1)[m
[31m-            b.set_cursor(ln - 1, len(prev), extend_selection)[m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m        # Visual Move[m
[32m+[m[32m        new_ln, new_col = self.view.get_visual_cursor_move(ln, col, -1)[m
[32m+[m[41m        [m
[32m+[m[32m        if new_ln == ln and new_col == col:[m
[32m+[m[32m            # Stuck at visual left edge -> Wrap to previous line end[m
[32m+[m[32m            if ln > 0:[m
[32m+[m[32m                prev_line = b.get_line(ln - 1)[m
[32m+[m[32m                b.set_cursor(ln - 1, len(prev_line), extend_selection)[m
[32m+[m[32m            elif extend_selection:[m
[32m+[m[32m                # Top of file behavior[m
[32m+[m[32m                b.set_cursor(0, 0, True)[m
[32m+[m[32m        else:[m
[32m+[m[32m            b.set_cursor(new_ln, new_col, extend_selection)[m
 [m
     def move_right(self, extend_selection=False):[m
         b = self.buf[m
         ln, col = b.cursor_line, b.cursor_col[m
[31m-        line = b.get_line(ln)[m
         [m
         if not extend_selection and b.selection.has_selection():[m
[31m-            # Move to end of selection[m
[32m+[m[32m            # Standard behavior: Collapse to end (logical)[m
             _, _, end_ln, end_col = b.selection.get_bounds()[m
             b.set_cursor(end_ln, end_col, extend_selection)[m
[31m-        elif col < len(line):[m
[31m-            # Move right within line[m
[31m-            b.set_cursor(ln, col + 1, extend_selection)[m
[31m-        elif ln + 1 < b.total():[m
[31m-            # At end of line - move to start of next line (selecting the newline)[m
[31m-            b.set_cursor(ln + 1, 0, extend_selection)[m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m        # Visual Move[m
[32m+[m[32m        new_ln, new_col = self.view.get_visual_cursor_move(ln, col, 1)[m
[32m+[m[41m        [m
[32m+[m[32m        if new_ln == ln and new_col == col:[m
[32m+[m[32m             # Stuck at visual right edge -> Wrap to next line start[m
[32m+[m[32m             if ln + 1 < b.total():[m
[32m+[m[32m                 b.set_cursor(ln + 1, 0, extend_selection)[m
[32m+[m[32m        else:[m
[32m+[m[32m             b.set_cursor(new_ln, new_col, extend_selection)[m
 [m
     def move_up(self, extend_selection=False):[m
         b = self.buf[m
[36m@@ -1347,7 +1360,14 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
         ink, logical = layout.get_extents()[m
         self.line_h = int(logical.height / Pango.SCALE)[m
         self.char_width = logical.width / Pango.SCALE[m
[31m-        self.mapper.set_char_width(self.char_width)[m
[32m+[m[41m        [m
[32m+[m[32m        # Measure RTL char for heuristic wrapping[m
[32m+[m[32m        layout.set_text("א", -1)[m
[32m+[m[32m        ink, logical = layout.get_extents()[m
[32m+[m[32m        self.char_width_rtl = logical.width / Pango.SCALE[m
[32m+[m[41m        [m
[32m+[m[41m        [m
[32m+[m[32m        self.mapper.set_char_widths(self.char_width, self.char_width_rtl)[m
         [m
         # Update tab array if needed[m
         pass[m
[36m@@ -2299,9 +2319,10 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
             ln_width = max(30, int(len(str(self.buf.total())) * self.char_width) + 10)[m
         else:[m
             ln_width = 0[m
[31m-        text_x = x - ln_width - 2 + self.scroll_x[m
[31m-        [m
[31m-        if text_x < 0: text_x = 0[m
[32m+[m
[32m+[m[41m            [m
[32m+[m[32m        # We cannot calc text_x yet because it depends on line content (RTL/LTR)[m
[32m+[m[32m        # So we find line first, then calc col.[m
             [m
         target_y = y[m
         [m
[36m@@ -2346,7 +2367,28 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
                      [m
                      surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)[m
                      cr = cairo.Context(surface)[m
[31m-                     col_in_seg = self.pixel_to_column(cr, text, text_x)[m
[32m+[m[41m                     [m
[32m+[m[32m                     # Calc Base X[m
[32m+[m[32m                     full_text = get_line(curr_ln)[m
[32m+[m[32m                     is_rtl = detect_rtl_line(full_text)[m
[32m+[m[32m                     layout = self.create_text_layout(cr, text)[m
[32m+[m[41m                     [m
[32m+[m[32m                     # Set layout width and alignment for proper coordinate mapping[m
[32m+[m[32m                     padding = 20 if self.vscroll.get_visible() else 10[m
[32m+[m[32m                     viewport_w = self.get_width() - ln_width - padding[m
[32m+[m[32m                     layout.set_width(int(viewport_w * Pango.SCALE))[m
[32m+[m[41m                     [m
[32m+[m[32m                     if is_rtl:[m
[32m+[m[32m                         layout.set_alignment(Pango.Alignment.RIGHT)[m
[32m+[m[32m                     else:[m
[32m+[m[32m                         layout.set_alignment(Pango.Alignment.LEFT)[m
[32m+[m[41m                     [m
[32m+[m[32m                     # Click position relative to layout origin (left edge)[m
[32m+[m[32m                     base_x = ln_width + 2[m
[32m+[m[32m                     col_pixels = x - base_x[m
[32m+[m[41m                     [m
[32m+[m[32m                     col_pixels = max(0, col_pixels)[m
[32m+[m[32m                     col_in_seg = self.pixel_to_column(cr, text, col_pixels)[m
                      found_col = s_start + col_in_seg[m
                  else:[m
                      found_col = 0[m
[36m@@ -4365,6 +4407,84 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
         # Optimization: Slicing + len(encode) is faster in Python than looping char by char[m
         return len(text[:col].encode("utf-8"))[m
 [m
[32m+[m[32m    def byte_to_char_index(self, text, byte_idx):[m
[32m+[m[32m        """Convert byte index to character index (col)."""[m
[32m+[m[32m        if byte_idx <= 0: return 0[m
[32m+[m[32m        encoded_text = text.encode("utf-8")[m
[32m+[m[32m        if byte_idx >= len(encoded_text): return len(text)[m
[32m+[m[41m        [m
[32m+[m[32m        # Fast path for ASCII[m
[32m+[m[32m        if len(text) == len(encoded_text):[m
[32m+[m[32m            return byte_idx[m
[32m+[m[41m            [m
[32m+[m[32m        return len(encoded_text[:byte_idx].decode("utf-8", errors="ignore"))[m
[32m+[m
[32m+[m[32m    def get_visual_cursor_move(self, line, col, direction):[m
[32m+[m[32m        """Calculates visual cursor movement.[m
[32m+[m[41m        [m
[32m+[m[32m        Args:[m
[32m+[m[32m            line: current logical line index[m
[32m+[m[32m            col: current logical column index[m
[32m+[m[32m            direction: -1 for visual left, 1 for visual right[m
[32m+[m[41m            [m
[32m+[m[32m        Returns:[m
[32m+[m[32m            (new_line, new_col) tuple.[m[41m [m
[32m+[m[32m            Returns (line, col) (unchanged) if movement hits visual boundary.[m
[32m+[m[32m        """[m
[32m+[m[32m        text = self.buf.get_line(line)[m
[32m+[m[32m        text = text if text is not None else ""[m
[32m+[m[41m        [m
[32m+[m[32m        # Create Pango layout for measurement[m
[32m+[m[32m        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)[m
[32m+[m[32m        cr = cairo.Context(surface)[m
[32m+[m[32m        layout = self.create_text_layout(cr, text)[m
[32m+[m[41m        [m
[32m+[m[32m        curr_byte_index = self.visual_byte_index(text, col)[m
[32m+[m[41m        [m
[32m+[m[32m        # move_cursor_visually(strong, old_index, old_trailing, direction)[m
[32m+[m[32m        # We always assume strong cursor for simplicity[m
[32m+[m[32m        # Note: direction is 1 (Right) or -1 (Left)[m
[32m+[m[32m        # Pango expects positive/negative direction[m
[32m+[m[41m        [m
[32m+[m[32m        new_index, new_trailing = layout.move_cursor_visually(True, curr_byte_index, 0, direction)[m
[32m+[m[41m        [m
[32m+[m[32m        # If position didn't change (hit edge)[m
[32m+[m[32m        # Note: move_cursor_visually returns a new index. If it can't move,[m[41m [m
[32m+[m[32m        # it might return the edge index again. The best check is if it changed.[m
[32m+[m[32m        # But we must compare resolved columns because different byte indices might map to same column?[m
[32m+[m[32m        # (Shouldn't happens with valid utf8).[m
[32m+[m[32m        # However, checking byte index change is safer.[m
[32m+[m[41m        [m
[32m+[m[32m        # Calculate resulting byte index[m
[32m+[m[32m        final_byte_pos = new_index[m
[32m+[m[32m        if new_trailing > 0:[m
[32m+[m[32m             # Cursor is after the character at new_index.[m
[32m+[m[32m             # We need to add the length of that character.[m
[32m+[m[32m             # Get the byte slice starting at new_index[m
[32m+[m[32m             encoded = text.encode('utf-8')[m
[32m+[m[32m             if new_index < len(encoded):[m
[32m+[m[32m                 # Find length of char at new_index[m
[32m+[m[32m                 # Use Pango/GLib logic or simple decode of one char[m
[32m+[m[32m                 # We can try decoding byte by byte[m
[32m+[m[32m                 char_len = 1[m
[32m+[m[32m                 while True:[m
[32m+[m[32m                     try:[m
[32m+[m[32m                         encoded[new_index:new_index+char_len].decode('utf-8')[m
[32m+[m[32m                         break[m
[32m+[m[32m                     except:[m
[32m+[m[32m                         char_len += 1[m
[32m+[m[32m                         if new_index + char_len > len(encoded):[m
[32m+[m[32m                             break[m
[32m+[m[32m                 final_byte_pos = new_index + char_len[m
[32m+[m[41m        [m
[32m+[m[32m        if final_byte_pos == curr_byte_index:[m
[32m+[m[32m             # No movement[m
[32m+[m[32m             return line, col[m
[32m+[m[41m             [m
[32m+[m[32m        # Convert back to col[m
[32m+[m[32m        new_col = self.byte_to_char_index(text, final_byte_pos)[m
[32m+[m[32m        return line, new_col[m
[32m+[m
     def get_color_for_token(self, token_type):[m
         """Get color for syntax token type."""[m
         # Use pre-calculated syntax colors map[m
[36m@@ -4426,6 +4546,13 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
             [m
         return True[m
 [m
[32m+[m[32m    def calculate_text_base_x(self, is_rtl, text_w, view_w, ln_width, scroll_x):[m
[32m+[m[32m        """Calculate the starting X position for text drawing."""[m
[32m+[m[32m        # Code editors typically left-align all text regardless of direction[m
[32m+[m[32m        # Right-alignment causes issues with wrapping and clipping[m
[32m+[m[32m        # Pango handles bidi reordering within the line automatically[m
[32m+[m[32m        return ln_width + 2 - scroll_x[m
[32m+[m
     def draw_view(self, area, cr, w, h):[m
         import time[m
         draw_start = time.time()[m
[36m@@ -4476,6 +4603,7 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
 [m
         while visual_lines_drawn < visible_lines and current_log_line < total_lines:[m
             line_text = self.buf.get_line(current_log_line)[m
[32m+[m[32m            line_is_rtl = detect_rtl_line(line_text if line_text else "")[m
             segments = self.mapper.get_line_segments(current_log_line)[m
 [m
             tokens = self.syntax.get_cached(current_log_line)[m
[36m@@ -4502,6 +4630,12 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
                     _, logical = layout.get_extents()[m
                     max_line_px = max(max_line_px, logical.width / Pango.SCALE)[m
 [m
[32m+[m[32m                # Measure text width for alignment[m
[32m+[m[32m                # Note: layout.get_pixel_size() is fast (cached in PangoLayout)[m
[32m+[m[32m                text_w, _ = layout.get_pixel_size()[m
[32m+[m[32m                # Use paragraph level direction (line_is_rtl) for consistent alignment of all segments[m
[32m+[m[32m                base_x = self.calculate_text_base_x(line_is_rtl, text_w, w, ln_width, self.scroll_x)[m
[32m+[m
                 # ---- line numbers (NO CLIP) ----[m
                 if i == 0 and self.show_line_numbers:[m
                     cr.set_source_rgb(0.5, 0.5, 0.5)[m
[36m@@ -4512,8 +4646,6 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
                     )[m
                     cr.show_text(txt)[m
 [m
[31m-                base_x = ln_width + 2 - self.scroll_x[m
[31m-[m
                 # ---- current line highlight ----[m
                 if self.highlight_current_line and current_log_line == self.buf.cursor_line:[m
                     cr.set_source_rgba(*self.current_line_background_color)[m
[36m@@ -4543,8 +4675,10 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
                         idx_e = self.visual_byte_index(seg_text, seg_sel_end)[m
                         line0 = layout.get_line(0)[m
                         if line0:[m
[31m-                            x1 = base_x + line0.index_to_x(idx_s, False) / Pango.SCALE[m
[31m-                            x2 = base_x + line0.index_to_x(idx_e, False) / Pango.SCALE[m
[32m+[m[32m                            # Selection coordinates are relative to layout origin (left edge)[m
[32m+[m[32m                            sel_base = ln_width + 2[m
[32m+[m[32m                            x1 = sel_base + line0.index_to_x(idx_s, False) / Pango.SCALE[m
[32m+[m[32m                            x2 = sel_base + line0.index_to_x(idx_e, False) / Pango.SCALE[m
                             cr.set_source_rgba(0.2, 0.4, 0.6, 0.4)[m
                             cr.rectangle(x1, current_y, x2 - x1, self.line_h)[m
                             cr.fill()[m
[36m@@ -4580,11 +4714,14 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
                             b_s = self.visual_byte_index(seg_text, rel_s)[m
                             b_e = self.visual_byte_index(seg_text, rel_e)[m
 [m
[32m+[m
                             pos_s = layout.get_cursor_pos(b_s)[0][m
                             pos_e = layout.get_cursor_pos(b_e)[0][m
 [m
[31m-                            x1 = base_x + pos_s.x / Pango.SCALE[m
[31m-                            x2 = base_x + pos_e.x / Pango.SCALE[m
[32m+[m[32m                            # Search coordinates are relative to layout origin (left edge)[m
[32m+[m[32m                            search_base = ln_width + 2[m
[32m+[m[32m                            x1 = search_base + pos_s.x / Pango.SCALE[m
[32m+[m[32m                            x2 = search_base + pos_e.x / Pango.SCALE[m
 [m
                             color = (1.0, 0.5, 0.0, 0.6) if self.current_match_idx == mi else (1.0, 1.0, 0.0, 0.4)[m
                             cr.set_source_rgba(*color)[m
[36m@@ -4617,11 +4754,22 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
 [m
                 layout.set_attributes(attr_list)[m
 [m
[32m+[m[32m                # ---- Configure Pango layout for bidi ----[m
[32m+[m[32m                # Set layout width to viewport for proper alignment[m
[32m+[m[32m                layout.set_width(int(viewport_w * Pango.SCALE))[m
[32m+[m[41m                [m
[32m+[m[32m                # For RTL lines, use right alignment within the layout[m
[32m+[m[32m                if line_is_rtl:[m
[32m+[m[32m                    layout.set_alignment(Pango.Alignment.RIGHT)[m
[32m+[m[32m                else:[m
[32m+[m[32m                    layout.set_alignment(Pango.Alignment.LEFT)[m
[32m+[m
                 # ---- text draw (single clip, correct) ----[m
                 cr.save()[m
                 cr.rectangle(ln_width, current_y, viewport_w, self.line_h)[m
                 cr.clip()[m
[31m-                cr.move_to(base_x, current_y)[m
[32m+[m[32m                # Always draw at left edge - Pango handles alignment internally[m
[32m+[m[32m                cr.move_to(ln_width + 2, current_y)[m
                 # Use the properly configured text foreground color[m
                 fg = getattr(self, 'text_foreground_color', (0.9, 0.9, 0.9))[m
                 cr.set_source_rgb(*fg)[m
[36m@@ -4644,7 +4792,8 @@[m [mclass VirtualTextView(Gtk.DrawingArea):[m
                         rel = self.buf.cursor_col - seg_start[m
                         b = self.visual_byte_index(seg_text, rel)[m
                         pos = layout.get_cursor_pos(b)[0][m
[31m-                        cx = base_x + pos.x / Pango.SCALE[m
[32m+[m[32m                        # Cursor position is relative to layout origin (left edge)[m
[32m+[m[32m                        cx = (ln_width + 2) + pos.x / Pango.SCALE[m
                         [m
                         # Use theme-appropriate cursor color[m
                         if is_dark:[m
[1mdiff --git a/svite/word_wrap.py b/svite/word_wrap.py[m
[1mindex d8d7e0d..0cd294d 100644[m
[1m--- a/svite/word_wrap.py[m
[1m+++ b/svite/word_wrap.py[m
[36m@@ -43,6 +43,7 @@[m [mclass VisualLineMapper:[m
         self._buffer = buffer[m
         self._viewport_width: int = 80[m
         self._char_width: float = 10.0[m
[32m+[m[32m        self._char_width_rtl: float = 10.0[m
         self._enabled: bool = False[m
         [m
         # LRU cache for wrap info (limited size)[m
[36m@@ -61,17 +62,25 @@[m [mclass VisualLineMapper:[m
             self.invalidate_all()[m
     [m
     def set_viewport_width(self, width_pixels: float, char_width: float = 10.0) -> None:[m
[31m-        """Update viewport width in pixels."""[m
[32m+[m[32m        self._viewport_width_pixels = width_pixels # Store pixels[m
         new_width = max(20, int(width_pixels / char_width))[m
         if new_width != self._viewport_width or char_width != self._char_width:[m
             self._viewport_width = new_width[m
             self._char_width = char_width[m
[32m+[m[32m            # If no RTL width set yet, assume same[m
[32m+[m[32m            if not hasattr(self, '_char_width_rtl'): self._char_width_rtl = char_width[m
             self.invalidate_all()[m
     [m
[31m-    def set_char_width(self, chars: int) -> None:[m
[31m-        """Set viewport width directly in characters."""[m
[31m-        if chars != self._viewport_width:[m
[31m-            self._viewport_width = max(20, chars)[m
[32m+[m[32m    def set_char_widths(self, ltr_width: float, rtl_width: float = 0) -> None:[m
[32m+[m[32m        """Set LTR and RTL character widths for dynamic wrapping."""[m
[32m+[m[32m        if ltr_width != self._char_width or rtl_width != self._char_width_rtl:[m
[32m+[m[32m            self._char_width = ltr_width[m
[32m+[m[32m            self._char_width_rtl = rtl_width if rtl_width > 0 else ltr_width[m
[32m+[m[41m            [m
[32m+[m[32m            # Re-calculating viewport width chars is complex here because we store width in Pixels[m[41m [m
[32m+[m[32m            # but currently only stored viewport_width is CHARS (from set_viewport_width).[m
[32m+[m[32m            # We need to store pixels to re-calc.[m
[32m+[m[32m            # But usually set_viewport_width is called on resize.[m
             self.invalidate_all()[m
     [m
     def invalidate_all(self) -> None:[m
[36m@@ -106,14 +115,23 @@[m [mclass VisualLineMapper:[m
         if not line_text:[m
             return WrapInfo(line_num=line_num)[m
         [m
[32m+[m[32m        # Use standard character width for consistent wrapping[m
[32m+[m[32m        cw = self._char_width[m
[32m+[m[41m            [m
[32m+[m[32m        # Calculate limit based on pixels locally[m
[32m+[m[32m        # Store _viewport_width_pixels if available, else infer[m
[32m+[m[32m        vp_pixels = getattr(self, '_viewport_width_pixels', self._viewport_width * self._char_width)[m
[32m+[m[32m        limit_chars = int(vp_pixels / cw) if cw > 0 else 80[m
[32m+[m[32m        limit_chars = max(20, limit_chars)[m
[32m+[m
         line_len = len(line_text)[m
[31m-        if line_len <= self._viewport_width:[m
[32m+[m[32m        if line_len <= limit_chars:[m
             return WrapInfo(line_num=line_num)[m
         [m
         # Find break points - fast character-based wrap[m
         break_points = [][m
         pos = 0[m
[31m-        width = self._viewport_width[m
[32m+[m[32m        width = limit_chars[m
         [m
         while pos < line_len:[m
             remaining = line_len - pos[m
