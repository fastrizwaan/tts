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
/* General Tab Styling */
.overlay-scrollbar { background-color: transparent; }

/* Styling applied to the Gtk.Button inside ChromeTab */
.chrome-tab {
    min-height: 32px;
    padding: 0 8px;
    border-radius: 6px 6px 0 0;
    background-color: transparent; /* Set background via CSS for correct rendering */
    transition: background-color 200ms ease;
}
.chrome-tab:hover { background: alpha(@window_fg_color, 0.05); }
.chrome-tab.active { background: alpha(@window_fg_color, 0.1); }
.chrome-tab.dragging { opacity: 0.5; }

/* Close Button Styling */
.chrome-tab-close-button {
    min-width: 16px; min-height: 16px;
    padding: 0; margin-left: 4px;
    border-radius: 50%;
    opacity: 0; /* Hidden until hover */
}
.chrome-tab:hover .chrome-tab-close-button { opacity: 1; }
.chrome-tab-close-button:hover { background-color: alpha(@window_fg_color, 0.1); }

/* Separator and Drop Indicator */
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
#   CHROME TAB (Fixes Visibility and Closing)
# ============================================================
DRAGGED_TAB = None

class ChromeTab(Gtk.Overlay): # Inherit from Gtk.Overlay
    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
   
    def __init__(self, title="Untitled", closeable=True):
        super().__init__()
        
        # Sizing and Expansion
        self.set_valign(Gtk.Align.CENTER)
        self.set_size_request(60, 32)
        self.set_hexpand(True) 
        
        # 1. Inner Content Box (for label and dot)
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        content_box.set_hexpand(True)
        content_box.set_halign(Gtk.Align.CENTER)
        
        # Title label: This fixes the Urdu/non-Latin visibility
        self.label = Gtk.Label(label=title)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_hexpand(True)
        content_box.append(self.label)
        
        # 2. Main Button (The clickable/draggable area)
        self.tab_button = Gtk.Button()
        self.tab_button.add_css_class("flat")
        self.tab_button.add_css_class("chrome-tab") # Apply tab styling here
        self.tab_button.set_child(content_box)
        self.set_child(self.tab_button)
        
        # 3. Close Button (Overlay) - This fixes the closing functionality
        if closeable:
            self.close_button = Gtk.Button(icon_name="window-close-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("chrome-tab-close-button")
            self.close_button.set_halign(Gtk.Align.END)
            self.close_button.set_valign(Gtk.Align.CENTER)
            
            # Connects directly to emit close-requested
            self.close_button.connect('clicked', lambda b: self.emit('close-requested'))
            self.add_overlay(self.close_button)
        
        self._title = title
        self.tab_bar = None

        # Event Controllers (Attached to the actual button now)
        self._setup_drag_drop()
        self._setup_clicks()

    def _setup_clicks(self):
        gesture = Gtk.GestureClick()
        gesture.set_button(0)
        gesture.connect('pressed', self._on_pressed)
        gesture.connect('released', self._on_released)
        self.tab_button.add_controller(gesture)

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
        self.tab_button.add_controller(drag) # Attach to button

    def _on_drag_prepare(self, source, x, y):
        return Gdk.ContentProvider.new_for_value(self._title)

    def _on_drag_begin(self, source, drag):
        global DRAGGED_TAB
        DRAGGED_TAB = self
        self.tab_button.add_css_class("dragging")
        source.set_icon(Gtk.WidgetPaintable.new(self), 0, 0)

    def _on_drag_end(self, source, drag, delete_data):
        global DRAGGED_TAB
        DRAGGED_TAB = None
        self.tab_button.remove_css_class("dragging")

    def set_active(self, active):
        if active: self.tab_button.add_css_class("active")
        else: self.tab_button.remove_css_class("active")

    def get_title(self): return self._title


# ----------------------------------------------------------------------
# CHROME TAB BAR (Single Row) - UNCHANGED from previous working version
# ----------------------------------------------------------------------
class ChromeTabBar(Gtk.Box):
    __gsignals__ = { 'layout-changed': (GObject.SignalFlags.RUN_FIRST, None, ()), }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_spacing(0)
        self.tabs = []
        self.separators = []

        self.drop_ind = Gtk.Box()
        self.drop_ind.set_size_request(3, 24)
        self.drop_ind.add_css_class("tab-drop-indicator")
        
        self._add_separator()
        
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
        if index > len(self.tabs): index = len(self.tabs)
        
        anchor_sep = self.separators[index]
        self.insert_child_after(tab, anchor_sep)
        
        new_sep = self._add_separator()
        self.reorder_child_after(new_sep, tab)
        
        self.separators.pop() 
        self.separators.insert(index + 1, new_sep)
        
        self.tabs.insert(index, tab)
        tab.tab_bar = self
        tab.separator = new_sep
        
        self._connect_hover(tab)
        self._update_separators()
        self.emit('layout-changed')

    def add_tab(self, tab):
        self.insert_tab(tab, len(self.tabs))

    def remove_tab(self, tab):
        if tab not in self.tabs: return
        idx = self.tabs.index(tab)
        
        self.remove(tab)
        
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
            if t.tab_button.has_css_class("active"): # Check the button's class now
                self.hide_separators_for_tab(t)

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
            self.insert_child_after(self.drop_ind, self.separators[idx])
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
        
        if tab in self.tabs:
            current_idx = self.tabs.index(tab)
            if current_idx < idx: idx -= 1
            if current_idx != idx:
                self.remove_tab(tab)
                self.insert_tab(tab, idx)
            return True
            
        old_parent = tab.get_parent()
        if isinstance(old_parent, ChromeTabBar):
            old_parent.remove_tab(tab)
            self.insert_tab(tab, idx)
            return True
            
        return False


# ----------------------------------------------------------------------
# CHROME MULTI BAR (Two-Row Limit) - UNCHANGED from previous step
# ----------------------------------------------------------------------
class ChromeMultiBar(Gtk.Box):
    
    MAX_ROWS = 2 

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.rows = []
        
        self._add_row()
        
        self._reflow_pending = False
        self.connect("notify::allocation", self._schedule_reflow)

    def _add_row(self):
        row = ChromeTabBar()
        row.connect('layout-changed', self._schedule_reflow)
        self.append(row)
        self.rows.append(row)
        return row

    def add_tab(self, tab):
        self.rows[-1].add_tab(tab)

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
        if width < 50: return
        
        MIN_TAB_WIDTH = 120 
        capacity_per_row = max(1, width // MIN_TAB_WIDTH)
        
        all_tabs = []
        active_tab = None
        for row in self.rows:
            for t in list(row.tabs):
                if t.tab_button.has_css_class("active"): active_tab = t
                all_tabs.append(t)
                row.remove_tab(t)
        
        import math
        needed_rows = math.ceil(len(all_tabs) / capacity_per_row)
        if needed_rows == 0: needed_rows = 1
        
        # Enforce MAX_ROWS limit (2)
        limited_rows = min(needed_rows, self.MAX_ROWS)
        
        while len(self.rows) < limited_rows:
            self._add_row()
        while len(self.rows) > limited_rows:
            row = self.rows.pop()
            self.remove(row)
            
        current_tab_idx = 0
        
        for i in range(limited_rows):
            row = self.rows[i]
            
            # Last row takes all remaining tabs (enforcing shrinking)
            if i < limited_rows - 1:
                chunk_size = capacity_per_row
            else:
                chunk_size = len(all_tabs) - current_tab_idx
                
            for _ in range(chunk_size):
                if current_tab_idx >= len(all_tabs): break
                tab = all_tabs[current_tab_idx]
                row.add_tab(tab)
                current_tab_idx += 1
                
        if active_tab:
            self.set_active_tab(active_tab)

        return GLib.SOURCE_REMOVE

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
        win.set_title("Auto-Wrapping Chrome Tabs (Max 2 Rows)")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_content(root)

        self.multibar = ChromeMultiBar()
        root.append(self.multibar)

        self.label = Gtk.Label(label="Content Area")
        self.label.set_vexpand(True)
        root.append(self.label)

        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctrl.set_margin_top(10); ctrl.set_margin_bottom(10); ctrl.set_margin_start(10)
        
        add_btn = Gtk.Button(label="Add Tab")
        add_btn.connect('clicked', self.add_new_tab)
        ctrl.append(add_btn)
        
        info = Gtk.Label(label="Resize window to see 2-row limit and shrinking.")
        ctrl.append(info)
        
        root.append(ctrl)

        # Initial Tabs, including the long and Urdu one.
        titles = ["README.md", "سلام دنیا — اردو ٹیب", "A very long file name that should shrink the tab", "Config.json"]
        for i in range(8):
            title = titles[i % len(titles)]
            self.add_new_tab(None, f"{title} ({i+1})")
            
        win.present()

    def add_new_tab(self, btn, title=None):
        if not title: title = f"New Tab {len(self.multibar.rows[-1].tabs) + 1}"
        t = ChromeTab(title)
        t.connect('activate-requested', self.on_tab_active)
        t.connect('close-requested', self.on_tab_close)
        self.multibar.add_tab(t)
        self.multibar.set_active_tab(t)

    def on_tab_active(self, tab):
        self.multibar.set_active_tab(tab)
        self.label.set_label(f"Showing content for: <b>{tab.get_title()}</b>")

    def on_tab_close(self, tab):
        # Find the correct parent ChromeTabBar and remove the tab
        for row in self.multibar.rows:
            if tab in row.tabs:
                row.remove_tab(tab)
                # If the tab that was closed was active, try activating the previous one
                if not row.tabs and len(self.multibar.rows) > 1:
                    # If this row is empty, reflow will clean it up.
                    pass
                elif row.tabs:
                    self.on_tab_active(row.tabs[-1])
                break

if __name__ == "__main__":
    app = DemoApp()
    app.run(None)
