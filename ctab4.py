#!/usr/bin/env python3
import sys, os, gi, json
import time

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, GLib, Gio

# ============================================================
#   CSS STYLES
# ============================================================
CSS_STYLE = """
.overlay-scrollbar { background-color: transparent; }
.chrome-tab {
    min-height: 32px;
    padding: 0 8px;
    border-radius: 6px 6px 0 0;
    background-color: transparent;
    transition: background-color 200ms ease;
}
.chrome-tab:hover { background: alpha(@window_fg_color, 0.05); }
.chrome-tab.active { background: alpha(@window_fg_color, 0.1); }
.chrome-tab.dragging { opacity: 0.5; }

.chrome-tab-close-button {
    min-width: 16px; min-height: 16px;
    padding: 0; margin-left: 4px;
    border-radius: 50%;
    opacity: 0;
}
.chrome-tab:hover .chrome-tab-close-button { opacity: 1; }
.chrome-tab-close-button:hover { background-color: alpha(@window_fg_color, 0.1); }

.chrome-tab-separator {
    min-width: 1px;
    background-color: alpha(@window_fg_color, 0.15);
    margin: 6px 0;
}
.chrome-tab-separator.hidden { opacity: 0; }
.tab-drop-indicator {
    background: @theme_selected_bg_color;
    min-width: 3px;
    border-radius: 2px;
}
"""

# ============================================================
#   CHROME TAB
# ============================================================
DRAGGED_TAB = None

class ChromeTab(Gtk.Box):
    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
   
    def __init__(self, title="Untitled", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        
        # Sizing: Small minimum width allows shrinking, hexpand allows filling
        self.set_size_request(60, 32)
        self.set_hexpand(True) 
        self.add_css_class("chrome-tab")
        self.set_valign(Gtk.Align.CENTER)

        # Content Box
        self.label = Gtk.Label(label=title)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_hexpand(True)
        self.label.set_halign(Gtk.Align.START)
        self.append(self.label)
        
        # Close Button
        if closeable:
            btn = Gtk.Button(icon_name="window-close-symbolic")
            btn.add_css_class("flat")
            btn.add_css_class("chrome-tab-close-button")
            btn.connect('clicked', lambda b: self.emit('close-requested'))
            self.append(btn)
        
        self._title = title
        self.tab_bar = None # Assigned when added to a bar

        # Event Controllers
        self._setup_drag_drop()
        self._setup_clicks()

    def _setup_clicks(self):
        gesture = Gtk.GestureClick()
        gesture.set_button(0)
        gesture.connect('pressed', self._on_pressed)
        gesture.connect('released', self._on_released)
        self.add_controller(gesture)

    def _on_pressed(self, gesture, n_press, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self.tab_bar: self.tab_bar.hide_separators_for_tab(self)

    def _on_released(self, gesture, n_press, x, y):
        self.emit('activate-requested')

    def _setup_drag_drop(self):
        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.MOVE)
        drag.connect('prepare', self._on_drag_prepare)
        drag.connect('drag-begin', self._on_drag_begin)
        drag.connect('drag-end', self._on_drag_end)
        self.add_controller(drag)

    def _on_drag_prepare(self, source, x, y):
        return Gdk.ContentProvider.new_for_value(self._title)

    def _on_drag_begin(self, source, drag):
        global DRAGGED_TAB
        DRAGGED_TAB = self
        self.add_css_class("dragging")
        source.set_icon(Gtk.WidgetPaintable.new(self), 0, 0)

    def _on_drag_end(self, source, drag, delete_data):
        global DRAGGED_TAB
        DRAGGED_TAB = None
        self.remove_css_class("dragging")

    def set_active(self, active):
        if active: self.add_css_class("active")
        else: self.remove_css_class("active")

    def get_title(self): return self._title


# ============================================================
#   CHROME TAB BAR (Single Row)
# ============================================================
class ChromeTabBar(Gtk.Box):
    __gsignals__ = {
        'layout-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_spacing(0)
        self.tabs = []
        self.separators = []

        # Drop Indicator
        self.drop_ind = Gtk.Box()
        self.drop_ind.set_size_request(3, 24)
        self.drop_ind.add_css_class("tab-drop-indicator")
        
        # Initial Separator
        self._add_separator()
        
        # Drop Target
        dt = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        dt.connect('motion', self._on_drag_motion)
        dt.connect('drop', self._on_drag_drop)
        dt.connect('leave', lambda t: self._hide_drop_indicator())
        self.add_controller(dt)

    def _add_separator(self):
        sep = Gtk.Box()
        sep.set_size_request(1, 16)
        sep.add_css_class("chrome-tab-separator")
        sep.set_valign(Gtk.Align.CENTER)
        self.separators.append(sep)
        self.append(sep)
        return sep

    def insert_tab(self, tab, index):
        # Insert tab after separators[index]
        # Then insert NEW separator after that tab
        if index > len(self.tabs): index = len(self.tabs)
        
        anchor_sep = self.separators[index]
        self.insert_child_after(tab, anchor_sep)
        
        new_sep = self._add_separator()
        # Move new_sep to correct position in widget tree
        self.reorder_child_after(new_sep, tab)
        
        # Update lists
        self.tabs.insert(index, tab)
        # Fix separator list order (move the one we just appended to correct index)
        self.separators.pop() # Remove from end
        self.separators.insert(index + 1, new_sep)
        
        tab.tab_bar = self
        tab.separator = new_sep # The one AFTER it
        
        self._connect_hover(tab)
        self._update_separators()
        self.emit('layout-changed')

    def add_tab(self, tab):
        self.insert_tab(tab, len(self.tabs))

    def remove_tab(self, tab):
        if tab not in self.tabs: return
        idx = self.tabs.index(tab)
        
        # Remove Tab Widget
        self.remove(tab)
        
        # Remove Separator AFTER tab
        sep = self.separators[idx+1]
        self.remove(sep)
        self.separators.pop(idx+1)
        self.tabs.pop(idx)
        
        self._update_separators()
        self.emit('layout-changed')

    def _connect_hover(self, tab):
        c = Gtk.EventControllerMotion()
        c.connect("enter", lambda c,x,y: self.hide_separators_for_tab(tab))
        c.connect("leave", lambda c: self._update_separators())
        tab.add_controller(c)

    def hide_separators_for_tab(self, tab):
        if tab not in self.tabs: return
        i = self.tabs.index(tab)
        if i > 0: self.separators[i].add_css_class("hidden")
        if i+1 < len(self.separators)-1: self.separators[i+1].add_css_class("hidden")

    def _update_separators(self):
        for s in self.separators: s.remove_css_class("hidden")
        if self.separators:
            self.separators[0].add_css_class("hidden")
            if len(self.separators) > 1: self.separators[-1].add_css_class("hidden")
        
        for i, t in enumerate(self.tabs):
            if t.has_css_class("active"):
                self.hide_separators_for_tab(t)

    # --- Drag & Drop Logic ---
    def _get_drop_index(self, x):
        for i, t in enumerate(self.tabs):
            alloc = t.get_allocation()
            center = alloc.x + alloc.width / 2
            if x < center: return i
        return len(self.tabs)

    def _on_drag_motion(self, target, x, y):
        idx = self._get_drop_index(x)
        self._show_drop_indicator(idx)
        return Gdk.DragAction.MOVE

    def _show_drop_indicator(self, idx):
        if self.drop_ind.get_parent(): self.remove(self.drop_ind)
        if idx == 0:
            self.insert_child_after(self.drop_ind, self.separators[0])
        elif idx <= len(self.tabs):
            self.insert_child_after(self.drop_ind, self.separators[idx]) # after separator before tab? No, usually after sep
        self.drop_ind.set_visible(True)

    def _hide_drop_indicator(self):
        self.drop_ind.set_visible(False)
        if self.drop_ind.get_parent(): self.remove(self.drop_ind)

    def _on_drag_drop(self, target, value, x, y):
        self._hide_drop_indicator()
        global DRAGGED_TAB
        tab = DRAGGED_TAB
        
        if not tab: return False
        
        idx = self._get_drop_index(x)
        
        # Case 1: Same Bar
        if tab in self.tabs:
            current_idx = self.tabs.index(tab)
            if current_idx < idx: idx -= 1
            if current_idx != idx:
                self.remove_tab(tab)
                self.insert_tab(tab, idx)
            return True
            
        # Case 2: Different Bar (Move)
        old_parent = tab.get_parent()
        if isinstance(old_parent, ChromeTabBar):
            old_parent.remove_tab(tab)
            self.insert_tab(tab, idx)
            return True
            
        return False


# ============================================================
#   CHROME MULTI BAR (The Auto-Wrapping Container)
# ============================================================
class ChromeMultiBar(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.rows = []
        
        # Start with one row
        self._add_row()
        
        # Debounce reflow using idle
        self._reflow_pending = False
        self.connect("notify::allocation", self._schedule_reflow)

    def _add_row(self):
        row = ChromeTabBar()
        row.connect('layout-changed', self._schedule_reflow)
        self.append(row)
        self.rows.append(row)
        return row

    def add_tab(self, tab):
        # Add to the last row, then let reflow handle if it overflows
        self.rows[-1].add_tab(tab)
        # Note: add_tab triggers layout-changed -> triggers reflow

    def set_active_tab(self, tab):
        for row in self.rows:
            for t in row.tabs:
                t.set_active(t == tab)

    def _schedule_reflow(self, *args):
        if not self._reflow_pending:
            self._reflow_pending = True
            GLib.idle_add(self._reflow)

    def _reflow(self):
        self._reflow_pending = False
        
        width = self.get_allocated_width()
        if width < 50: return # Too small to calculate
        
        # 1. Config: Minimum width per tab before wrapping
        MIN_TAB_WIDTH = 120 
        capacity_per_row = max(1, width // MIN_TAB_WIDTH)
        
        # 2. Gather all tabs (remove them from parents momentarily)
        #    This is the "nuclear option" for layout but ensures perfect ordering.
        #    Since we are just moving widgets, it's fast.
        all_tabs = []
        active_tab = None
        for row in self.rows:
            # We copy the list because remove_tab modifies it
            for t in list(row.tabs):
                if t.has_css_class("active"): active_tab = t
                all_tabs.append(t)
                row.remove_tab(t)
        
        # 3. Calculate needed rows
        import math
        needed_rows = math.ceil(len(all_tabs) / capacity_per_row)
        if needed_rows == 0: needed_rows = 1
        
        # Ensure we have enough rows
        while len(self.rows) < needed_rows:
            self._add_row()
        while len(self.rows) > needed_rows:
            row = self.rows.pop()
            self.remove(row)
            
        # 4. Distribute tabs
        current_tab_idx = 0
        for i in range(needed_rows):
            row = self.rows[i]
            # Block signal to prevent recursion during bulk add
            # (In this simple implementation, we just rely on _reflow_pending flag 
            #  but removing handlers is safer if we had complex logic)
            
            # Fill this row
            for _ in range(capacity_per_row):
                if current_tab_idx >= len(all_tabs): break
                tab = all_tabs[current_tab_idx]
                row.add_tab(tab)
                current_tab_idx += 1
                
        # Restore active state logic if needed (handled by tab internal state usually)
        if active_tab:
            self.set_active_tab(active_tab)


# ============================================================
#   DEMO APP
# ============================================================
class DemoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.chromemulti")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        # Load CSS
        css = Gtk.CssProvider()
        css.load_from_data(CSS_STYLE.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Adw.ApplicationWindow(application=self)
        win.set_default_size(800, 300)
        win.set_title("Auto-Wrapping Chrome Tabs")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_content(root)

        # THE MULTI BAR
        self.multibar = ChromeMultiBar()
        root.append(self.multibar)

        # Content Area
        self.label = Gtk.Label(label="Content Area")
        self.label.set_vexpand(True)
        root.append(self.label)

        # Controls
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctrl.set_margin_top(10); ctrl.set_margin_bottom(10); ctrl.set_margin_start(10)
        
        add_btn = Gtk.Button(label="Add Tab")
        add_btn.connect('clicked', self.add_new_tab)
        ctrl.append(add_btn)
        
        info = Gtk.Label(label="Resize window to see wrapping behavior")
        ctrl.append(info)
        
        root.append(ctrl)

        # Initial Tabs
        for i in range(8):
            self.add_new_tab(None, f"Tab {i+1}")
            
        win.present()

    def add_new_tab(self, btn, title=None):
        if not title: title = f"New Tab"
        t = ChromeTab(title)
        t.connect('activate-requested', self.on_tab_active)
        t.connect('close-requested', self.on_tab_close)
        self.multibar.add_tab(t)
        self.multibar.set_active_tab(t)

    def on_tab_active(self, tab):
        self.multibar.set_active_tab(tab)
        self.label.set_label(f"Showing content for: {tab.get_title()}")

    def on_tab_close(self, tab):
        # Remove from whichever row it is in
        parent = tab.get_parent()
        if parent:
            parent.remove_tab(tab)

if __name__ == "__main__":
    app = DemoApp()
    app.run(None)
