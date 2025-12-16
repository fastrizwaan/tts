
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GLib
from typing import Optional
import re

class FindReplaceBar(Gtk.Box):
    def __init__(self, editor_view):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.editor_view = editor_view
        self.add_css_class("find-bar")
        self.set_visible(False)
        self._search_timeout_id = None
        self._scroll_refresh_timeout = None
        self._in_replace = False
        self._last_replaced_match = None  # Guard against rapid double-replace

        # Connect scroll callback for viewport-based search refresh logic (if needed)
        # edig's EditorView handles scroll internally. 
        # We can connect to scrollbar or just expose a callback.
        # self.editor_view.set_scrollbar_callback(self._on_editor_scrolled) # Existing callback is for scrollbar adjust?
        # We can hook into scroll controller? 
        # For now, let's keep it simple.
        
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
        self.find_entry.connect("search-changed", self.on_search_changed)
        self.find_entry.connect("activate", self.on_find_next)
        
        self.find_overlay.set_child(self.find_entry)
        
        # Matches Label (x of y)
        self.matches_label = Gtk.Label(label="")
        self.matches_label.add_css_class("dim-label")
        self.matches_label.add_css_class("caption")
        self.matches_label.set_margin_end(30) 
        self.matches_label.set_halign(Gtk.Align.END)
        self.matches_label.set_valign(Gtk.Align.CENTER)
        self.matches_label.set_visible(False)
        self.matches_label.set_can_target(False) 
        
        self.find_overlay.add_overlay(self.matches_label)
        
        # Capture Esc to close
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.find_entry.add_controller(key_ctrl)
        
        find_box.append(self.find_overlay)
        
        # Navigation Box
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

        # Toggle Replace Mode Button
        self.reveal_replace_btn = Gtk.Button()
        self.reveal_replace_btn.set_icon_name("edit-find-replace-symbolic")
        self.reveal_replace_btn.add_css_class("flat")
        self.reveal_replace_btn.connect("clicked", self.toggle_replace_mode)
        self.reveal_replace_btn.set_tooltip_text("Toggle Replace")
        find_box.append(self.reveal_replace_btn)

        # Search Options
        self.options_btn = Gtk.MenuButton()
        self.options_btn.set_icon_name("system-run-symbolic") 
        self.options_btn.set_tooltip_text("Search Options")
        self.options_btn.add_css_class("flat")
        
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        popover_box.set_margin_top(12)
        popover_box.set_margin_bottom(12)
        popover_box.set_margin_start(12)
        popover_box.set_margin_end(12)
        
        self.regex_check = Gtk.CheckButton(label="Regular Expressions")
        self.regex_check.connect("toggled", self.on_search_changed)
        popover_box.append(self.regex_check)
        
        self.case_check = Gtk.CheckButton(label="Case Sensitive")
        self.case_check.connect("toggled", self.on_search_changed)
        popover_box.append(self.case_check)
        
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
        
        # --- Bottom Row: Replace ---
        self.replace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.replace_box.set_margin_bottom(6)
        self.replace_box.set_margin_start(12)
        self.replace_box.set_margin_end(12)
        self.replace_box.set_visible(False)
        
        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_hexpand(True)
        self.replace_entry.set_placeholder_text("Replace")
        self.replace_entry.connect("activate", self.on_replace)
        self.replace_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find-replace-symbolic")
        
        replace_key_ctrl = Gtk.EventControllerKey()
        replace_key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.replace_entry.add_controller(replace_key_ctrl)

        self.replace_box.append(self.replace_entry)
        
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
        
        if vis:
            self.replace_entry.grab_focus()
        else:
            self.find_entry.grab_focus()

    def show_search(self):
        self.set_visible(True)
        self.replace_box.set_visible(False)
        self.find_entry.grab_focus()
        # Select all text in find entry
        # self.find_entry.select_region(0, -1) # GtkSearchEntry doesn't have select_region exposed directly sometimes? 
        # It's an Editable.
        # But GtkSearchEntry wraps GtkEditable?
        # Actually GtkSearchEntry implies structure.
        # We'll skip for now.
        
    def show_replace(self):
        self.set_visible(True)
        self.replace_box.set_visible(True)
        self.replace_entry.grab_focus()
        
    def close(self, *args):
        self.set_visible(False)
        self.editor_view.grab_focus()
        self.editor_view.set_search_results([])

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
            
        # Handle Undo/Redo - delegate to logic if needed (or user handles globally)
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval == Gdk.KEY_z or keyval == Gdk.KEY_Z:
                if state & Gdk.ModifierType.SHIFT_MASK:
                    self.editor_view.undo_manager.redo(self.editor_view.buffer)
                else:
                    self.editor_view.undo_manager.undo(self.editor_view.buffer)
                self.editor_view.queue_draw()
                return True
                
            if keyval == Gdk.KEY_y or keyval == Gdk.KEY_Y:
                self.editor_view.undo_manager.redo(self.editor_view.buffer)
                self.editor_view.queue_draw()
                return True
        return False

    def on_search_changed(self, *args):
        if self._search_timeout_id:
            GLib.source_remove(self._search_timeout_id)
        self._search_timeout_id = GLib.timeout_add(200, self._perform_search)
        
    def _perform_search(self):
        self._search_timeout_id = None
        
        query = self.find_entry.get_text()
        case_sensitive = self.case_check.get_active()
        is_regex = self.regex_check.get_active()
        whole_word = self.whole_word_check.get_active()
        
        if not query:
            self.editor_view.set_search_results([])
            self.matches_label.set_visible(False)
            return False

        if whole_word:
            if not is_regex:
                escaped_query = re.escape(query)
                query = f"\\b{escaped_query}\\b"
                is_regex = True
            else:
                query = f"\\b{query}\\b"
        
        total_lines = self.editor_view.buffer.total_lines
        
        # Simplified search strategy for now (Synchronous)
        # Assuming VirtualBuffer search is implemented
        matches = self.editor_view.buffer.search(query, case_sensitive, is_regex, max_matches=5000)
        self.editor_view.set_search_results(matches)
        
        count = len(matches)
        if count >= 5000:
            self.matches_label.set_text("5000+")
        else:
            self.matches_label.set_text(f"{count}")
        self.matches_label.set_visible(True)
        return False

    def on_find_next(self, *args):
        self.editor_view.next_match()
        self._update_label_idx()
        
    def on_find_prev(self, *args):
        self.editor_view.prev_match()
        self._update_label_idx()
        
    def _update_label_idx(self):
        count = len(self.editor_view.search_matches)
        idx = self.editor_view.current_match_idx + 1
        if count > 0:
             self.matches_label.set_text(f"{idx} of {count}")
        
    def on_replace(self, *args):
        match = self.editor_view.get_current_match()
        if not match:
            return

        # Guard against rapid clicking replacing the same match multiple times
        if self._last_replaced_match and self._last_replaced_match == match:
            return
        self._last_replaced_match = match

        sl, sc, el, ec = match[0:4]
        replacement = self.replace_entry.get_text()

        # Calculate where replacement will end
        replacement_lines = replacement.split('\n')
        if len(replacement_lines) == 1:
            new_end_ln = sl
            new_end_col = sc + len(replacement)
        else:
            new_end_ln = sl + len(replacement_lines) - 1
            new_end_col = len(replacement_lines[-1])

        # Set skip position BEFORE modifying buffer
        # This tells set_search_results to skip matches before this position
        self.editor_view._skip_to_position = (new_end_ln, new_end_col)

        # Perform replacement
        buf = self.editor_view.buf
        buf.delete(sl, sc, el, ec)
        buf.insert(sl, sc, replacement)
        buf.set_cursor(new_end_ln, new_end_col)

        # Re-search - set_search_results will use _skip_to_position
        self._perform_search()
        self._update_label_idx()
        self.editor_view.queue_draw()


            
    def on_replace_all(self, *args):
        replacement = self.replace_entry.get_text()
        query = self.find_entry.get_text()
        case_sensitive = self.case_check.get_active()
        is_regex = self.regex_check.get_active()
        whole_word = self.whole_word_check.get_active()
        
        if whole_word:
            if not is_regex:
                escaped_query = re.escape(query)
                query = f"\\b{escaped_query}\\b"
                is_regex = True
            else:
                query = f"\\b{query}\\b"

        count = self.editor_view.buffer.replace_all(query, replacement, case_sensitive, is_regex)
        self.on_search_changed()

