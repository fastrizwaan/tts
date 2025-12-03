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
        self.set_size_request(120, FIXED_H)
        self.set_hexpand(False)
        overlay = Gtk.Overlay()

        # =====================================================
        # ADDED: real Adwaita-style modified dot
        # =====================================================
        dot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot_box.set_hexpand(True)

        self.modified_dot = Gtk.DrawingArea()
        #self.modified_dot.set_size_request(8, 8)
        self.modified_dot.add_css_class("modified-dot")
        self.modified_dot.set_visible(False)  # hidden by default
        dot_box.append(self.modified_dot)
        # =====================================================

        # Title label
        self.label = Gtk.Label()
        self.label.set_text(title)
        self.label.set_margin_end(28)
        self.label.set_max_width_chars(20)
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label.set_single_line_mode(True)
        self.label.set_hexpand(True)
        self.label.set_halign(Gtk.Align.START)

        # put label next to dot
        dot_box.append(self.label)

        # Button wrapper
        self.tab_button = Gtk.Button()
        self.tab_button.add_css_class("flat")
        self.tab_button.set_child(dot_box)  # PATCH: stack with dot
        self.tab_button.set_hexpand(True)
        self.tab_button.set_vexpand(True)
        
        overlay.set_child(self.tab_button)
        
        # Close button overlay
        if closeable:
            self.close_button = Gtk.Button()
            self.close_button.set_icon_name("window-close-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("chrome-tab-close-button")
            self.close_button.set_size_request(24, 24)
            self.close_button.set_halign(Gtk.Align.END)
            self.close_button.set_valign(Gtk.Align.CENTER)
            self.close_button.connect('clicked', self._on_close_clicked)
            overlay.add_overlay(self.close_button)
       
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
        self.tab_button.add_controller(drag_source)
        
        # Explicitly claim clicks
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0) # Listen to all buttons (left, middle, right)
        click_gesture.connect('pressed', self._on_tab_pressed)
        click_gesture.connect('released', self._on_tab_released)
        self.tab_button.add_controller(click_gesture)

    # ==========================================================
    # PATCH: new method to toggle dot visibility (replaces label hacks)
    # ==========================================================
    def set_modified(self, modified: bool):
        self.modified_dot.set_visible(modified)
        self.queue_draw()

       
    def _on_tab_pressed(self, gesture, n_press, x, y):
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
        """Show the real Adwaita-style modified dot."""
        if self.has_css_class("modified"):
            self.modified_dot.set_visible(True)
        else:
            self.modified_dot.set_visible(False)

        self.label.set_text(self._original_title)

       
    def set_active(self, active):
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")
           
    def set_modified(self, modified):
        if modified:
            self.add_css_class("modified")
        else:
            self.remove_css_class("modified")
        self.update_label()
    
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
                    return {
                        'type': 'editor',
                        'content': editor.get_text(),
                        'file_path': editor.current_file_path,
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
            tab_data['content'] = editor.get_text()
            tab_data['file_path'] = editor.current_file_path
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

        self.set_margin_start(4)
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
        """Handle drop on the tab bar - supports both same-window and cross-window drops"""
        import json
        global DRAGGED_TAB
        
        # Try to parse as JSON (cross-window drag)
        tab_data = None
        if isinstance(value, str):
            try:
                tab_data = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
        
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

