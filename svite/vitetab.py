import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GLib, GObject, Pango, Adw, Gio

# Global variable for drag and drop
DRAGGED_TAB = None

VITE_TAB_CSS = """
/* ========================
   Chrome Tabs
   ======================== */

.chrome-tab {
    background: @headerbar_bg_color;
    color: alpha(@window_fg_color, 0.85);
    min-height: 32px;
    padding-left: 0px;
    padding-right: 0px;
    border-radius: 9px 9px 9px 9px;
    margin-left: 0px;
    margin-bottom: 1px;

}
.chrome-tab label {
    padding-left: 0px;
    padding-right: 0px;
    margin-top: 1px;
    opacity: 0.9;
}

.chrome-tab .progress-bar {
    min-height: 2px;
    margin-top: 30px; /* Position at the very bottom of the tab (32px high) */
}

.chrome-tab .progress-bar trough {
    min-height: 2px;
    background: transparent;
    border: none;
}

.chrome-tab .progress-bar progress {
    min-height: 2px;
    background-color: alpha(@window_fg_color, 0.4);
    border-radius: 0;
}

.header-modified-dot{
    min-width: 8px;
    min-height: 8px;

    background-color: alpha(@window_fg_color, 0.7);
    border-radius: 4px;

    margin-top: 5px;   /* vertically center inside tab */
    margin-bottom: 5px;
}

.modified-dot {
    min-width: 8px;
    min-height: 8px;

    background-color: alpha(@window_fg_color, 0.7);
    border-radius: 4px;

    margin-top: 12px;   /* vertically center inside tab */
    margin-bottom: 12px;
}

.chrome-tab label {
    font-weight: normal;
}

.chrome-tab:hover {
    color: @window_fg_color;
    background: alpha(@window_fg_color, 0.1);

}

/* ACTIVE TAB (pilled) */
.chrome-tab.active {
    background-color: mix(@headerbar_bg_color, @window_fg_color, 0.1);
    color: @window_fg_color;
}

.chrome-tab.active label {
    font-weight: normal;
    opacity: 1;
}

/* Dragging state */
.chrome-tab.dragging {
    opacity: 0.5;
}

/* Drop indicator line */
.tab-drop-indicator {
    background: linear-gradient(to bottom, 
        transparent 0%, 
        rgba(0, 127, 255, 0.8) 20%, 
        rgba(0, 127, 255, 1) 50%, 
        rgba(0, 127, 255, 0.8) 80%, 
        transparent 100%);
    min-width: 3px;
    border-radius: 2px;
}


/* Modified marker */
.chrome-tab.modified {
    font-style: normal;
}

/* Reset all buttons inside tab (fixes size regression) */
.chrome-tab button {
    background: none;
    border: none;
    box-shadow: none;
    padding: 0;
    margin: 0;
    min-width: 0;
    min-height: 0;
}

/* close button specific */
.chrome-tab .chrome-tab-close-button {
    min-width: 20px;
    min-height: 20px;
    padding: 2px;
    margin: 0;
    margin-right: 2px;
    opacity: 1.0;
    border-radius: 50%;
}


/* These 3 needs to be 0.0 */
.chrome-tab.active .chrome-tab-close-button {
    background-color: alpha(@window_fg_color, 0.01);
    color: @window_fg_color;
}

.chrome-tab.active:hover .chrome-tab-close-button {
    background-color: alpha(@window_fg_color, 0.01);
    color:  @window_fg_color;    
}

.chrome-tab:hover .chrome-tab-close-button {
    background-color: alpha(@window_fg_color, 0.01); /* Visible background on hover */
    color: @window_fg_color;
}
/* These 3 needs to be 0.0 */

.chrome-tab.active:hover {
    background-color: alpha(@window_fg_color, 0.13);
    color: @window_fg_color;
}

/* ========================
   Separators
   ======================== */
.chrome-tab-separator {
    min-width: 1px;
    background-color: alpha(@window_fg_color, 0.15);
    margin-top: 6px;
    margin-bottom: 6px;
}

.chrome-tab-separator.hidden {
    opacity: 0;
    /* Keep width to prevent layout shift */
}
.chrome-tab-separator:first-child {
    background-color: transparent;
    min-width: 0;
}

.chrome-tab-separator:last-child {
    background-color: transparent;
    min-width: 0;
}
/* ========================
   Tab close button
   ======================== */
.chrome-tab-close-button {
    min-width: 20px;
    min-height: 20px;
    padding: 2px;
    margin: 0;
    margin-right: 2px;
    opacity: 1.0;
    background-color: alpha(@window_fg_color, 0.13);
    color:  @window_fg_color;
     border-radius: 50%;
}



.chrome-tab-close-button:hover  {
    opacity: 1.0;
    color: @window_fg_color;
    background-color: alpha(@window_fg_color, 0.24); /* Stronger background on direct hover */
}

.chrome-tab.active .chrome-tab-close-button:hover {
    opacity: 1;
    background-color: alpha(@window_fg_color, 0.12);
    color: @window_fg_color;
}
.chrome-tab .chrome-tab-close-button:hover  {
    opacity: 1.0;
    color: @window_fg_color;
    background-color: alpha(@window_fg_color, 0.12); 
}

.chrome-tab-fade {
    background: linear-gradient(to right, transparent 0%, @headerbar_bg_color 100%);
    min-width: 50px;
    padding-right: 0px;
    padding-left: 0px;
    opacity: 1; /* Always visible, but subtle via gradient */
    transition: opacity 0.1s;
}

.chrome-tab:hover .chrome-tab-fade {
    opacity: 1;
    /* Active tab color: mix of headerbar_bg and 10% fg (matches line 83) */
    background: linear-gradient(to right, transparent 0%, mix(@headerbar_bg_color, @window_fg_color, 0.1) 60%, mix(@headerbar_bg_color, @window_fg_color, 0.1) 100%);
}


.chrome-tab.active .chrome-tab-fade {
    opacity: 1;
    /* Active tab color: mix of headerbar_bg and 10% fg (matches line 83) */
    background: linear-gradient(to right, transparent 0%, mix(@headerbar_bg_color, @window_fg_color, 0.1) 60%, mix(@headerbar_bg_color, @window_fg_color, 0.1) 100%);
}
"""

def apply_css(provider):
    """Load the tab CSS into the provider"""
    provider.load_from_data(VITE_TAB_CSS.encode('utf-8'))

class ChromeTab(Gtk.Box):
    """A custom tab widget that behaves like Chrome tabs"""
    _drag_in_progress = False

    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'cancel-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
   
    def __init__(self, title="Untitled 1", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        FIXED_H = 32
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.FILL)  # Fill allocated space to ensure equal width
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("chrome-tab")
        self.set_overflow(Gtk.Overflow.HIDDEN) # Clip overflowing text
        self.set_size_request(150, FIXED_H)
        
        # Overlay for label and close button
        self.overlay = Gtk.Overlay()
        self.overlay.set_hexpand(True)
        self.append(self.overlay)
        
        # Title label - main child of overlay
        self.label = Gtk.Label()
        self.label.set_text(title) # Restore set_text!
        # Wrapper for label to clip text without expanding tab
        self.scroll_wrapper = Gtk.ScrolledWindow()
        self.scroll_wrapper.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER) # EXTERNAL allows propagate_natural_width=False to work
        self.scroll_wrapper.set_has_frame(False)
        self.scroll_wrapper.set_hexpand(True)
        self.scroll_wrapper.set_propagate_natural_width(False) # CRITICAL: Allows shrinking below content size
        self.scroll_wrapper.set_min_content_width(1) # Ensure wrapper can be very small
        self.scroll_wrapper.set_can_target(False) # Pass clicks through to tab
        self.scroll_wrapper.set_margin_start(15)
        self.scroll_wrapper.set_margin_end(15) # 6px margin as requested
        
        self.label.set_ellipsize(Pango.EllipsizeMode.NONE) # No ellipsis, just run
        self.label.set_single_line_mode(True)
        self.label.set_hexpand(True)
        self.label.set_halign(Gtk.Align.CENTER) # Center align text as requested
        self.label.set_xalign(0.5)
        
        self.scroll_wrapper.set_child(self.label)
        self.overlay.set_child(self.scroll_wrapper)
        
        # State tracking
        self._is_modified = False
        self._is_hovered = False
        self._is_active = False
        self.loading = False
        self.cancelled = False
        
        # Close button - overlay child
        if closeable:
            self.close_button = Gtk.Button()
            self.close_button.set_icon_name("cross-small-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("chrome-tab-close-button")
            self.close_button.set_halign(Gtk.Align.END)
            self.close_button.set_valign(Gtk.Align.CENTER)
            self.close_button.set_hexpand(False)
            self.close_button.set_margin_end(0) # Moved right (was 2)
            self.close_button.connect('clicked', self._on_close_clicked)
            
            # Hide initially (opacity 0 to keep layout if needed, or just invisible)
            # Using set_opacity gives smoother transition possibility
            self.close_button.set_opacity(0)
            
            # Fade Overlay - Added BEFORE close button (so it's under close button if stacked, wait, overlay stack. 
            # We want fade ON TOP of label. Label is child. 
            # We want fade UNDER close button? Or ON TOP? 
            # Close button needs to be clickable. Fade is just visual.
            # Add fade first.
            self.fade_overlay = Gtk.Box()
            self.fade_overlay.set_halign(Gtk.Align.END)
            # Use margin right to perfectly align under close button but not cover it partially? 
            # Actually, standard design: fade GOES UNDER close button.
            # Close button sits on RIGHT edge. Fade sits on RIGHT edge. They overlap.
            self.fade_overlay.set_hexpand(False)
            self.fade_overlay.set_size_request(50, -1) # Wide fade
            self.fade_overlay.add_css_class("chrome-tab-fade")
            self.fade_overlay.set_margin_end(0) # Flush right
            # For clickthrough? Gtk.Box consumes clicks? 
            # Set pickable=False to allow clicks to pass to close button if overlapped? 
            # But Close Button is added AFTER, so it's on top.
            self.fade_overlay.set_can_target(False)
            
            self.overlay.add_overlay(self.fade_overlay)

            self.overlay.add_overlay(self.close_button)
            self.overlay.set_measure_overlay(self.close_button, False)
            
            # Spinner for loading state - START (left)
            self.spinner = Gtk.Spinner()
            self.spinner.set_halign(Gtk.Align.START)
            self.spinner.set_valign(Gtk.Align.CENTER)
            self.spinner.set_hexpand(False)
            self.spinner.set_margin_start(6)
            self.overlay.add_overlay(self.spinner)
            self.overlay.set_measure_overlay(self.spinner, False)
            
            # Progress bar for the tab - Thin line at the bottom
            self.progress_bar = Gtk.ProgressBar()
            self.progress_bar.set_valign(Gtk.Align.END)
            self.progress_bar.add_css_class("progress-bar")
            self.progress_bar.set_visible(False)
            self.overlay.add_overlay(self.progress_bar)
            self.overlay.set_measure_overlay(self.progress_bar, False)
            
            # Hover controller for the tab
            hover_controller = Gtk.EventControllerMotion()
            hover_controller.connect("enter", self._on_hover_enter)
            hover_controller.connect("leave", self._on_hover_leave)
            self.add_controller(hover_controller)
            
            # Initial state update
            self._update_close_button_state()
       

        self._original_title = title
        self.tab_bar = None  # Set by ChromeTabBar
        
        # Dragging setup
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.add_controller(drag_source)
        
        # Explicitly claim clicks
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0) # Listen to all buttons (left, middle, right)
        click_gesture.connect('pressed', self._on_tab_pressed)
        click_gesture.connect('released', self._on_tab_released)
        self.add_controller(click_gesture)
        
    def set_loading(self, loading):
        """Set loading state. If loading, show spinner, progress bar and make close button explicitly visible as Cancel."""
        self.loading = loading
        if loading:
            self.cancelled = False
            self.spinner.set_visible(True)
            self.spinner.start()
            self.progress_bar.set_visible(True)
            self.progress_bar.set_fraction(0.0)
            # Show close button permanently during load (as cancel button)
            self.close_button.set_opacity(1)
            self.close_button.set_icon_name("process-stop-symbolic") # Use Stop icon
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)
            self.progress_bar.set_visible(False)
            self.close_button.set_icon_name("cross-small-symbolic") # Revert to Close icon
            self.close_button.set_sensitive(True) # Ensure clickable
            self._update_close_button_state()

    def update_progress(self, fraction):
        """Update progress bar (0.0 to 1.0)"""
        if hasattr(self, 'progress_bar'):
            self.progress_bar.set_fraction(fraction)
            # Optionally update tooltip or label with percentage
            # self.set_tooltip_text(f"Loading... {int(fraction * 100)}%")

    def _on_hover_enter(self, controller, x, y):
        self._is_hovered = True
        self._update_close_button_state()
        
        # Notify tab bar to hide separators
        if self.tab_bar and hasattr(self.tab_bar, 'hide_separators_for_tab'):
            self.tab_bar.hide_separators_for_tab(self)

    def _on_hover_leave(self, controller):
        self._is_hovered = False
        self._update_close_button_state()
        
        # Notify tab bar to restore separators
        if self.tab_bar and hasattr(self.tab_bar, 'update_separators'):
            self.tab_bar.update_separators()

    def _update_close_button_state(self):
        if not hasattr(self, 'close_button'):
            return

        # Always show close button on active tab
        if self._is_active:
            self.close_button.set_icon_name("cross-small-symbolic")
            self.close_button.set_opacity(1.0)
            self.close_button.set_sensitive(True)
            return

        if self._is_hovered:
            # Hovered: Show Close Icon
            self.close_button.set_icon_name("cross-small-symbolic")
            # Keep slightly different opacity for modified/unmodified if desired, 
            # or just use standard. Let's keep it visible.
            self.close_button.set_opacity(1.0 if self._is_modified else 0.9)
        else:
            # Not hovered: COMPLETELY HIDDEN
            self.close_button.set_opacity(0.0)
                
        # Ensure button is sensitive
        self.close_button.set_sensitive(True)

    def set_modified(self, modified: bool):
        self._is_modified = modified
        self._update_close_button_state()
        self.update_label()
        
        # Add/remove CSS class for modified state (used by close_tab detection)
        if modified:
            self.add_css_class("modified")
        else:
            self.remove_css_class("modified")

       
    def _on_tab_pressed(self, gesture, n_press, x, y):
        # Check if click is on the close button - if so, don't claim it
        if hasattr(self, 'close_button') and self.close_button.get_sensitive():
            # Convert coordinates to widget-relative (GTK4 returns tuple of x, y)
            coords = self.close_button.translate_coordinates(self, 0, 0)
            if coords is not None:
                widget_x, widget_y = coords
                # Check if click is within close button bounds
                if (widget_x <= x <= widget_x + self.close_button.get_width() and
                    widget_y <= y <= widget_y + self.close_button.get_height()):
                    # Don't claim - let the button handle it
                    return
        
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
        if self.loading:
            # Cancel loading
            self.cancelled = True
            self.emit('cancel-requested')
            # We don't close immediately; wait for text loader to see flag
            # But the user expects feedback.
            self.spinner.stop()
            self.close_button.set_sensitive(False)
            return

        self.emit('close-requested')
       
    def set_title(self, title):
        self._original_title = title
        self.update_label()
       
    def get_title(self):
        return self._original_title
    


    def update_label(self):
        """Update the label text."""
        if self._is_modified:
            safe_title = GLib.markup_escape_text(self._original_title)
            # Use smaller font size for the dot
            self.label.set_markup(f"<span size='smaller'>●</span> {safe_title}")
        else:
            self.label.set_text(self._original_title)

       
    def set_active(self, active):
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")
        
        # Update close button visibility
        self._update_close_button_state()
           

    
    # Drag and drop handlers
    def _on_drag_prepare(self, source, x, y):
        """Prepare drag operation - return content provider with tab object"""
        # Prevent concurrent drags
        if ChromeTab._drag_in_progress:
            return None
        
        # Pass the ChromeTab object directly
        return Gdk.ContentProvider.new_for_value(self)
    
    def _on_drag_begin(self, source, drag):
        """Called when drag begins - set visual feedback"""
        global DRAGGED_TAB
        
        # Prevent concurrent drags
        if ChromeTab._drag_in_progress:
            drag.drop_done(False)
            return
        
        ChromeTab._drag_in_progress = True
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
        
        # Reset drag success flag for next drag
        had_success = getattr(self, 'drag_success', False)
        self.drag_success = False
        
        # Check if tab was already transferred (e.g. by drop handler)
        was_transferred = getattr(self, 'was_transferred', False)
        self.was_transferred = False
        
        # Clean up visual state
        DRAGGED_TAB = None
        self.remove_css_class("dragging")
        
        # Schedule cleanup of drag lock after a delay to ensure all operations complete
        def cleanup_drag_lock():
            ChromeTab._drag_in_progress = False
            return False
        
        GLib.timeout_add(100, cleanup_drag_lock)  # 100ms delay
        
        if was_transferred:
            return

        # If drag was successful and cross-window, close the source tab
        # Only close if it was a CROSS-WINDOW drag (tab_bar changed)
        if had_success:
            # Check if this was actually a cross-window transfer
            # by checking if the tab is still in its original tab_bar
            if self.tab_bar and self not in self.tab_bar.tabs:
                # Tab was removed from original bar = cross-window transfer
                # The drop handler already took care of closing the source tab
                pass
            # If tab is still in tab_bar, it was just reordered within same window
            # Don't do anything - normal reordering handled it
            return
        
        # If drag was NOT successful (dropped on nothing), check if dropped outside window
        # But only if we still have a valid tab_bar reference
        if not self.tab_bar or self not in self.tab_bar.tabs:
            # Tab is detached or invalid, don't try to process further
            return
        
        # Find the window that owns this tab
        window = None
        parent = self.tab_bar.get_parent()
        while parent:
            if isinstance(parent, Adw.ApplicationWindow):
                window = parent
                break
            parent = parent.get_parent()
        
        if not window:
            return
        
        # Use idle_add to defer the window check to avoid GTK state issues
        def check_outside_window():
            # Get seat and pointer
            try:
                seat = Gdk.Display.get_default().get_default_seat()
                if not seat:
                    return False
                
                pointer = seat.get_pointer()
                if not pointer:
                    return False
                
                # Get window surface and coordinates
                surface = window.get_surface()
                if not surface:
                    return False
                
                # Check if outside
                # On Wayland, get_device_position returns False if pointer is not over surface
                found, x, y, mask = surface.get_device_position(pointer)
                
                is_outside = False
                if not found:
                    is_outside = True
                else:
                    # Even if found, check bounds (in case of grab)
                    width = window.get_width()
                    height = window.get_height()
                    if x < 0 or y < 0 or x > width or y > height:
                        is_outside = True
                
                if is_outside:
                    # It is outside!
                    # Trigger move to new window
                    if self.tab_bar and self in self.tab_bar.tabs:
                        idx = self.tab_bar.tabs.index(self)
                        window.activate_action('win.tab_move_new_window', GLib.Variant.new_string(str(idx)))
                
            except Exception as e:
                print(f"Error checking window bounds: {e}")
            
            return False
        
        # Defer the check to let GTK clean up drag state
        GLib.timeout_add(50, check_outside_window)  # 50ms delay



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

        self.set_margin_start(6)  # User requested 3px margin start
        self.set_margin_end(0)
        self.set_margin_top(0)
        self.set_margin_bottom(0)
        self.set_child_spacing(0)
        
        # Make the tab bar expand to fill available horizontal space
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        
        # Don't justify - we manually calculate tab sizes for equal width
        try:
            self.set_justify(0)  # Adw.Justify.NONE - no stretching/spreading
        except Exception:
            pass  # Fallback for older libadwaita versions

        self.tabs = []
        self.separators = []   # separator BEFORE each tab + 1 final separator
        self._cached_cols = 0  # Cache for column count optimization
        
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
        # Accept ChromeTab objects directly
        drop_target = Gtk.DropTarget.new(ChromeTab, Gdk.DragAction.MOVE)
        drop_target.connect('drop', self._on_tab_bar_drop)
        drop_target.connect('motion', self._on_tab_bar_motion)
        drop_target.connect('leave', self._on_tab_bar_leave)
        self.add_controller(drop_target)
        
        # Connect to size allocation to update tab widths dynamically
        self.connect('notify::visible', self._on_visibility_changed)
        
        # Connect to size-allocate to update tabs when layout changes
        # This ensures tabs recalculate when the tab bar is resized
        self._size_allocate_handler_id = None
        self._setup_size_allocate_handler()
        
        # Force update when mapped (window loaded)
        self.connect('map', lambda w: self.update_tab_sizes())
        
        
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

        # Immediate update of separators and sizes
        self.update_separators()
        
        # Pre-calculate and apply size immediately to avoid "pop"
        # Use last known width if current allocation is 0 (e.g. during init)
        current_width = self.get_width()
        if current_width <= 0 and hasattr(self, '_last_allocated_width'):
            current_width = self._last_allocated_width
            
        # Update logic: Use max of cached, current, or window width heuristic
        # Biases towards "Start Large then shrink" which is smoother than expanding from 0
        win_width = 0
        window = self.get_ancestor(Gtk.Window)
        if window:
            win_width = window.get_width() # Use full window width as strong hint
            
        cached = getattr(self, '_last_allocated_width', 0)
        current = self.get_width()
        target_width = max(cached, current, win_width)
            
        if target_width > 0:
            self.update_tab_sizes(allocated_width=target_width)
        
        # Always schedule updates to ensure final consistency after layout
        GLib.idle_add(self.update_tab_sizes)
        GLib.timeout_add(50, self.update_tab_sizes) # Add delay to allow layout to settle
        
        # Update window UI state (visibility of tab bar)
        window = self.get_ancestor(Adw.ApplicationWindow)
        if window and hasattr(window, 'update_ui_state'):
            window.update_ui_state()

        # Note: Removed redundant GLib.timeout_add(50, self.update_tab_sizes)
        # The immediate update_tab_sizes() call above is sufficient


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

        self.update_separators()
        
        # Update tab sizes immediately with a small delay
        GLib.timeout_add(50, self.update_tab_sizes)
    
        # Update window UI state (visibility of tab bar)
        window = self.get_ancestor(Adw.ApplicationWindow)
        if window and hasattr(window, 'update_ui_state'):
            window.update_ui_state()

    def set_tab_active(self, tab):
        for t in self.tabs:
            t.set_active(t is tab)

        # update separators *immediately*
        self.update_separators()

    def _hide_pair(self, i):
        """Hide left + right separators for tab[i]."""

        # Hide left separator if not first tab
        if i > 0:
            self.separators[i].add_css_class("hidden")

        # Hide right separator if not last tab
        if i + 1 < len(self.separators) - 1:
            self.separators[i + 1].add_css_class("hidden")

    def get_tab_for_page(self, page):
        """Get ChromeTab associated with a given Adw.TabView page"""
        for tab in self.tabs:
            if hasattr(tab, '_page') and tab._page == page:
                return tab
        return None

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
        self.update_separators()
        
        # Emit signal to notify parent
        self.emit('tab-reordered', tab, new_index)

    def update_separators(self):
        # Reset all (show all initially)
        for sep in self.separators:
            sep.set_visible(True)
            sep.remove_css_class("hidden")

        # Hide edge separators permanently
        if self.separators:
            self.separators[0].set_visible(False)
            if len(self.separators) > 1:
                self.separators[-1].set_visible(False)

        # Hide separator at the end of every row
        allocated_width = self.get_width()
        allocated_width = self.get_width()
        if allocated_width > 0:
            available_width = allocated_width - 6  # Account for margin_start=6
            cols, _, _, _ = self._calculate_grid_cols(available_width)
            
            # If we have multiple rows, hide the separator at the end of each row
            # Separators are indexed such that separators[i+1] is after tab[i]
            # So if a row has 'cols' tabs, the separator after tab[cols-1] is index cols
            if cols > 0:
                for i in range(cols, len(self.separators), cols):
                    if i < len(self.separators):
                        self.separators[i].set_visible(False)

        # Hide around active tab
        for i, tab in enumerate(self.tabs):
            if tab.has_css_class("active"):
                self._hide_pair(i)
    
    def _calculate_grid_cols(self, available_width):
        """Calculate number of effective columns based on available width"""
        min_tab_width = 150  # Updated to 150 as requested
        max_tab_width = 4000
        separator_width = 1
        
        # Calculate how many tabs can fit per row at minimum width (Theoretical Capacity)
        capacity_per_row = (available_width + separator_width) // (min_tab_width + separator_width)
        if capacity_per_row < 1:
            capacity_per_row = 1
            
        # Determine effective columns: use actual tab count, but don't exceed capacity
        num_tabs = len(self.tabs)
        effective_cols = min(num_tabs, capacity_per_row)
        if effective_cols < 1: 
            effective_cols = 1
            
        return effective_cols, separator_width, min_tab_width, max_tab_width

    def update_tab_sizes(self, allocated_width=None):
        """Update tab sizes dynamically - equal width filling rows"""
        if not self.tabs:
            return False
        
        # Get the actual allocated width of the tab bar if not provided
        if allocated_width is None:
            allocated_width = self.get_width()
        
        if allocated_width <= 0:
            return False
            
        # Update cache immediately so subsequent calls (e.g. add_tab) use this valid width
        if hasattr(self, '_last_allocated_width'):
            self._last_allocated_width = allocated_width
        
        # Calculate available width for tabs
        margin_start = 6
        available_width = allocated_width - margin_start
        
        if available_width <= 0:
            return False
        
        # Sizing constants
        MIN_TAB_WIDTH = 150
        MAX_TAB_WIDTH = 4000  # Allow tabs to grow to fill entire viewport
        TAB_HEIGHT = 32
        SEPARATOR_WIDTH = 1
        
        # 1. Determine columns (how many tabs fit per row)
        # Capacity based on minimum width
        capacity = max(1, (available_width + SEPARATOR_WIDTH) // (MIN_TAB_WIDTH + SEPARATOR_WIDTH))
        
        # Effective columns (can't have more cols than tabs)
        cols = min(len(self.tabs), capacity)
        if cols < 1: cols = 1
        
        # 2. Calculate dynamic width to fill the available space evenly
        # available = (cols * width) + ((cols - 1) * separator)
        # width = (available - (cols-1)*sep) / cols
        total_sep_width = (cols - 1) * SEPARATOR_WIDTH
        total_sep_width = (cols - 1) * SEPARATOR_WIDTH
        available_for_tabs = available_width - total_sep_width # exact fit
        
        raw_tab_width = available_for_tabs // cols
        
        # Clamp width
        final_tab_width = max(MIN_TAB_WIDTH, min(raw_tab_width, MAX_TAB_WIDTH))
        
        # 3. Apply size to ALL tabs
        # This ensures every tab is exactly same width, preventing grid misalignment
        for tab in self.tabs:
            current_req = tab.get_size_request()
            if current_req[0] != final_tab_width or current_req[1] != TAB_HEIGHT:
                tab.set_size_request(final_tab_width, TAB_HEIGHT)
            
            
            # Update label ellipsization
            # Dynamic sizing handled by ScrolledWindow clipping
            pass
            # max_chars = int(max(1, (final_tab_width - 40) / 9))
            # if hasattr(tab, 'label'):
            #     tab.label.set_max_width_chars(max_chars)
        
        # 4. Update separators if column count changed
        if cols != getattr(self, '_cached_cols', 0):
            self._cached_cols = cols
            self.update_separators()
        
        return False
        
        # Update separators only when column count changes
        if effective_cols != self._cached_cols:
            self._cached_cols = effective_cols
            self.update_separators()
        
        return False
    
    def _on_visibility_changed(self, widget, param):
        """Handle visibility changes to update tab sizes"""
        if self.get_visible():
            GLib.idle_add(self.update_tab_sizes)
    
    def _setup_size_allocate_handler(self):
        """Setup width change monitoring using signals for instant resize response"""
        self._last_allocated_width = 0
        self._resize_timeout_id = None  # For debouncing rapid resize events
        self._pending_width = 0
        
        # Connect to width property change for responsive resize
        self.connect('notify::width', self._on_width_changed)
        
    def _on_width_changed(self, widget, param):
        """Handle width changes instantly for smooth resize"""
        current_width = self.get_width()
        
        if current_width <= 0:
            return
            
        # Only update if width actually changed significantly (avoid subpixel noise)
        if hasattr(self, '_last_allocated_width') and abs(current_width - self._last_allocated_width) < 2:
            return
        
        self._last_allocated_width = current_width
        self.update_tab_sizes(allocated_width=current_width)
    
    def _do_resize_update(self):
        """Execute the debounced resize update"""
        self._resize_timeout_id = None
        
        if self._pending_width > 0 and self._pending_width != self._last_allocated_width:
            self._last_allocated_width = self._pending_width
            self.update_tab_sizes(allocated_width=self._pending_width)
        
        return False  # Don't repeat
    
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
        self.update_separators()
    
    def _on_tab_bar_drop(self, target, value, x, y):
        """Handle drop on the tab bar - supports same-window and cross-window tab drops"""
        global DRAGGED_TAB
        
        # Prevent processing if drag is being finalized
        if not ChromeTab._drag_in_progress:
            return False
        
        # We now expect a ChromeTab object directly
        if not isinstance(value, ChromeTab):
            return False
            
        dragged_tab = value
        
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
            
        # Check if this is a cross-window drag (tab is from another tab bar)
        if dragged_tab.tab_bar != self:
            # Cross-window drop
            drop_position = self._calculate_drop_position(x, y)
            
            # Get source window BEFORE removing tab from bar
            source_window = None
            if dragged_tab.tab_bar:
                source_window = dragged_tab.tab_bar.get_ancestor(Adw.ApplicationWindow)
            
            # Reparent the tab
            # 1. Remove from source tab bar
            if dragged_tab.tab_bar:
                dragged_tab.tab_bar.remove_tab(dragged_tab)
            
            # 2. Add to this tab bar at the correct position
            # We need to insert it, but add_tab appends. 
            # So we append then reorder.
            self.add_tab(dragged_tab)
            
            # Mark drag as successful so source doesn't try to close it again
            dragged_tab.drag_success = True
            
            # Reorder to drop position
            # Note: add_tab puts it at the end, so index is len-1
            current_index = len(self.tabs) - 1
            if current_index != drop_position:
                self.reorder_tab(dragged_tab, drop_position)
            
            # 3. Transfer the EditorPage
            if source_window and source_window != target_window and hasattr(dragged_tab, '_page'):
                # Mark as transferred so _on_drag_end doesn't try to close it
                dragged_tab.was_transferred = True
                
                # Switch signal connections from source window to target window
                if source_window:
                    try:
                        dragged_tab.disconnect_by_func(source_window.on_tab_activated)
                        dragged_tab.disconnect_by_func(source_window.on_tab_close_requested)
                    except Exception as e:
                        print(f"Error disconnecting signals: {e}")
                
                dragged_tab.connect('activate-requested', target_window.on_tab_activated)
                dragged_tab.connect('close-requested', target_window.on_tab_close_requested)

                page = getattr(dragged_tab, '_page', None)
                if page:
                    # Transfer page to target window's tab view
                    # IMPORTANT: transfer_page returns the NEW Adw.TabPage belonging to the target view
                    new_page = source_window.tab_view.transfer_page(page, target_window.tab_view, drop_position)
                    
                    # Update the tab's page reference immediately
                    if new_page:
                        dragged_tab._page = new_page
                        
                        # Ensure the page is selected in the new window
                        def select_page():
                            if new_page.get_selected_page() != new_page:
                                 target_window.tab_view.set_selected_page(new_page)
                            return False
                        GLib.idle_add(select_page)
                else:
                    print("Error: dragged_tab has no _page")
                    return False
            
            # 4. Activate the tab
            self.set_tab_active(dragged_tab)
            dragged_tab.emit('activate-requested')
            
            # Mark drag as successful
            if DRAGGED_TAB:
                DRAGGED_TAB.drag_success = True
                
            self._hide_drop_indicator()
            return True
        
        # Same-window drag
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
        
        # Mark drag as successful
        dragged_tab.drag_success = True
        
        # Hide indicator
        self._hide_drop_indicator()
        
        return True
