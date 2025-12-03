#!/usr/bin/env python3
import sys, os, mmap, gi, cairo, time, unicodedata
from threading import Thread
from array import array
import math 
import datetime
import bisect
import json

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo, GLib, Gio


CSS_OVERLAY_SCROLLBAR = """
/* ===== Vertical overlay scrollbar ===== */
.overlay-scrollbar {{
    background-color: transparent;
}}

.overlay-scrollbar trough {{
    min-width: 8px;
    border-radius: 0px;
    background-color: transparent;
}}

.overlay-scrollbar trough:hover {{
    background-color: alpha(@window_fg_color, 0.2);
    transition: background-color 200ms ease;
}}

.overlay-scrollbar trough > slider {{
    min-width: 2px;
    border-radius: 12px;
    background-color: alpha(@window_fg_color, 0.2);
    transition: min-width 200ms ease, background-color 200ms ease;
}}

.overlay-scrollbar trough:hover > slider {{
    min-width: 8px;
    background-color: alpha(@window_bg_color, 0.05);
}}

.overlay-scrollbar:hover trough {{
    background-color: alpha(@window_fg_color, 0.1);
}}

.overlay-scrollbar:hover trough > slider {{
    min-width: 8px;
    background-color: rgba(53,132,228,1);
}}

.overlay-scrollbar trough > slider:hover {{
    min-width: 8px;
    background-color: rgba(73,152,248, 1);
}}

.overlay-scrollbar trough > slider:active {{
    min-width: 8px;
    background-color: rgba(53,132,228, 1);
}}

/* ===== Horizontal overlay scrollbar ===== */
.hscrollbar-overlay {{
    background-color: transparent;
    margin-bottom: 0px;
}}

.hscrollbar-overlay trough {{
    min-height: 8px;
    border-radius: 0px;
    background-color: transparent;
    margin-bottom: 0px;    
}}

.hscrollbar-overlay trough:hover {{
    background-color: alpha(@window_fg_color, 0.2);
    transition: background-color 200ms ease;
    margin-bottom: 0px;
}}

.hscrollbar-overlay trough > slider {{
    min-height: 2px;
    border-radius: 12px;
    background-color: alpha(@window_fg_color, 0.2);
    transition: min-height 200ms ease, background-color 200ms ease;
}}

.hscrollbar-overlay trough:hover > slider {{
    min-height: 8px;
    background-color: alpha(@window_fg_color, 0.55);
}}

.hscrollbar-overlay:hover trough {{
    background-color: alpha(@window_fg_color, 0.2);
}}

.hscrollbar-overlay:hover trough > slider {{
    min-height: 8px;
    background-color: rgba(53,132,228,1);
}}

.hscrollbar-overlay trough > slider:hover {{
    min-height: 8px;
    background-color: rgba(73,152,248, 1);
}}

.hscrollbar-overlay trough > slider:active {{
    min-height: 8px;
    background-color: rgba(53,132,228, 1);
}}

.toolbarview {{
    background: @headerbar_bg_color; 
}}

/* ========================
   Editor background
   ======================== */
.editor-surface {{
    background-color: @window_bg_color;
}}

/* ========================
   Chrome Tabs
   ======================== */

.chrome-tab {{
    min-height: 32px;
    padding: 0 8px;
    border-radius: 6px 6px 6px 6px;
}}

.header-modified-dot {{
    min-width: 8px;
    min-height: 8px;
    background-color: alpha(@window_fg_color, 0.7);
    border-radius: 4px;
    margin-top: 5px;
    margin-bottom: 5px;
}}

.modified-dot {{
    min-width: 8px;
    min-height: 8px;
    background-color: alpha(@window_fg_color, 0.7);
    border-radius: 4px;
    margin-top: 8px;
    margin-bottom: 8px;
}}

.chrome-tab label {{
    font-weight: normal;
}}

.chrome-tab:hover {{
    color: @window_fg_color;
    min-height: 24px;
    background: alpha(@window_fg_color, 0.10);
}}

/* ACTIVE TAB (pilled) */
.chrome-tab.active {{
    background: alpha(@window_fg_color, 0.12);
    color: @window_fg_color;
    min-height: 24px;
}}

.chrome-tab.active label {{
    font-weight: normal;
}}

/* Dragging state */
.chrome-tab.dragging {{
    opacity: 0.5;
}}

/* Drop indicator line */
.tab-drop-indicator {{
    background: linear-gradient(to bottom, 
        transparent 0%, 
        rgba(0, 127, 255, 0.8) 20%, 
        rgba(0, 127, 255, 1) 50%, 
        rgba(0, 127, 255, 0.8) 80%, 
        transparent 100%);
    min-width: 3px;
    border-radius: 2px;
}}

/* Reset all buttons inside tab */
.chrome-tab button {{
    background: none;
    border: none;
    box-shadow: none;
    padding: 0;
    margin: 0;
    min-width: 0;
    min-height: 0;
}}

/* close button specific */
.chrome-tab .chrome-tab-close-button {{
    min-width: 10px;
    min-height: 10px;
    padding: 4px;
    opacity: 0.10;
    color: @window_fg_color;
}}

.chrome-tab:hover .chrome-tab-close-button {{
    opacity: 1;
}}

.chrome-tab.active .chrome-tab-close-button {{
    opacity: 1;
    color: @window_fg_color;
}}

/* ========================
   Separators
   ======================== */
.chrome-tab-separator {{
    min-width: 1px;
    background-color: alpha(@window_fg_color, 0.15);
    margin-top: 6px;
    margin-bottom: 6px;
}}

.chrome-tab-separator.hidden {{
    min-width: 0px;
    background-color: transparent;
}}

.chrome-tab-separator:first-child {{
    background-color: transparent;
    min-width: 0;
}}

.chrome-tab-separator:last-child {{
    background-color: transparent;
    min-width: 0;
}}

/* ========================
   Tab close button
   ======================== */
.chrome-tab-close-button {{
    opacity: 0;
    transition: opacity 300ms ease, background-color 300ms ease;
    margin-right: 0px;
    padding: 0px;
}}

.chrome-tab:hover .chrome-tab-close-button {{
    opacity: 1;
    border-radius: 20px;
}}

.chrome-tab-close-button:hover {{
    background-color: alpha(@window_fg_color, 0.1);
}}

.chrome-tab.active .chrome-tab-close-button:hover {{
    opacity: 1;
    background-color: alpha(@window_fg_color, 0.1);
}}


/* Corrected dropdown selectors  removed space after colon */
.linked dropdown:firstchild > button  {{
    bordertopleftradius: 0px; 
    borderbottomleftradius: 0px; 
    bordertoprightradius: 0px; 
    borderbottomrightradius: 0px;
}}

/* Explicit rule to ensure middle dropdowns have NO radius */
.linked dropdown:not(:firstchild):not(:lastchild) > button {{
    borderradius: 0;
}}




/* Corrected menubutton selectors  removed space after colon */
.linked menubutton:firstchild > button  {{
    bordertopleftradius: 10px; 
    borderbottomleftradius: 10px; 
    bordertoprightradius: 0px; 
    borderbottomrightradius: 0px;
}}

.linked menubutton:lastchild > button {{
    bordertopleftradius: 0px; 
    borderbottomleftradius: 0px; 
    bordertoprightradius: 10px; 
    borderbottomrightradius: 10px;
}} 

/* Additional recommended fixes for consistent styling */
.linked menubutton button {{
    background: alpha(@window_fg_color, 0.05); padding:0px; paddingright: 3px; marginleft: 0px;
}}

.linked menubutton button:hover {{
    background: alpha(@window_fg_color, 0.15);
     padding:0px; paddingright: 3px;
}}

.linked menubutton button:active, 
.linked menubutton button:checked {{
    backgroundcolor: rgba(127, 127, 127, 0.3);
    padding:0px; paddingright: 3px;
}}

.linked menubutton button:checked:hover {{
       background: alpha(@window_fg_color, 0.2);
}}


/* Corrected button selectors  removed space after colon */
.linked button  {{
    bordertopleftradius: 10px; 
    borderbottomleftradius: 10px; 
    bordertoprightradius: 0px; 
    borderbottomrightradius: 0px;
    
}}

/* Additional recommended fixes for consistent styling */
.linked button {{
    background: alpha(@window_fg_color, 0.05); paddingleft: 10px; paddingright:6px; 
}}

.linked button:hover {{
    background: alpha(@window_fg_color, 0.15);

}}


"""



# ============================================================
#   CHROME TABS
# ============================================================

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
        
        self.add_css_class("chrome-tab")
        self.set_valign(Gtk.Align.CENTER)
        
        # CHANGE 1: Allow shrinking and expanding
        # Set a small minimum width (e.g., 40px) so it can shrink
        # Set hexpand=True so it tries to fill available space
        self.set_size_request(40, FIXED_H)
        self.set_hexpand(True)
        
        overlay = Gtk.Overlay()

        # Modified dot
        dot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot_box.set_hexpand(True)

        self.modified_dot = Gtk.DrawingArea()
        self.modified_dot.add_css_class("modified-dot")
        self.modified_dot.set_visible(False)
        dot_box.append(self.modified_dot)

        # Title label
        self.label = Gtk.Label()
        self.label.set_text(title)
        self.label.set_margin_end(28)
        self.label.set_max_width_chars(20) # This acts as a soft limit for sizing
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label.set_single_line_mode(True)
        self.label.set_hexpand(True)
        self.label.set_halign(Gtk.Align.START)

        dot_box.append(self.label)

        # Button wrapper
        self.tab_button = Gtk.Button()
        self.tab_button.add_css_class("flat")
        self.tab_button.set_child(dot_box)
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
        self.tab_bar = None
        
        # Dragging setup
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.tab_button.add_controller(drag_source)
        
        # Click handling
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0)
        click_gesture.connect('pressed', self._on_tab_pressed)
        click_gesture.connect('released', self._on_tab_released)
        self.tab_button.add_controller(click_gesture)

    # ... [Rest of ChromeTab methods (set_modified, handlers, etc) remain exactly the same] ...
    def set_modified(self, modified: bool):
        self.modified_dot.set_visible(modified)
        self.queue_draw()

    def _on_tab_pressed(self, gesture, n_press, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        current_button = gesture.get_current_button()
        if n_press == 1 and current_button == 3:
            self._show_context_menu(x, y)
            return
        if self.tab_bar:
            self.tab_bar.hide_separators_for_tab(self)

    def _show_context_menu(self, x, y):
        if not self.tab_bar: return
        try:
            tab_index = self.tab_bar.tabs.index(self)
        except ValueError: return

        menu = Gio.Menu()
        def add_item(label, action, target_str):
            item = Gio.MenuItem.new(label, action)
            item.set_action_and_target_value(action, GLib.Variant.new_string(target_str))
            return item
        idx_str = str(tab_index)
        section1 = Gio.Menu()
        section1.append_item(add_item("Move Left", "win.tab_move_left", idx_str))
        section1.append_item(add_item("Move Right", "win.tab_move_right", idx_str))
        section1.append_item(add_item("Move to New Window", "win.tab_move_new_window", idx_str))
        menu.append_section(None, section1)
        section2 = Gio.Menu()
        section2.append_item(add_item("Close Other Tabs", "win.tab_close_other", idx_str))
        section2.append_item(add_item("Close", "win.tab_close", idx_str))
        menu.append_section(None, section2)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
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
    
    def _on_drag_prepare(self, source, x, y):
        import json
        window = None
        if self.tab_bar:
            parent = self.tab_bar.get_parent()
            while parent:
                if isinstance(parent, Adw.ApplicationWindow):
                    window = parent
                    break
                parent = parent.get_parent()
        
        tab_data = {
            'window_id': id(window) if window else 0,
            'tab_index': self.tab_bar.tabs.index(self) if self.tab_bar and self in self.tab_bar.tabs else -1,
            'title': self.get_title(),
            # Simplified for demo:
            'content': "Tab Content Transfer", 
        }
        json_data = json.dumps(tab_data)
        return Gdk.ContentProvider.new_for_value(json_data)
    
    def _on_drag_begin(self, source, drag):
        global DRAGGED_TAB
        DRAGGED_TAB = self
        self.drag_success = False
        self.add_css_class("dragging")
        paintable = Gtk.WidgetPaintable.new(self)
        source.set_icon(paintable, 0, 0)
    
    def _on_drag_end(self, source, drag, delete_data):
        global DRAGGED_TAB
        DRAGGED_TAB = None
        self.remove_css_class("dragging")
        if hasattr(self, 'drag_success') and self.drag_success:
            window = None
            if self.tab_bar:
                parent = self.tab_bar.get_parent()
                while parent:
                    if isinstance(parent, Adw.ApplicationWindow):
                        window = parent
                        break
                    parent = parent.get_parent()
            if window and hasattr(window, 'close_tab_after_drag'):
                if self.tab_bar and self in self.tab_bar.tabs:
                    tab_index = self.tab_bar.tabs.index(self)
                    GLib.idle_add(window.close_tab_after_drag, tab_index)


class ChromeTabBar(Gtk.Box):
    # CHANGE 2: Inherit from Gtk.Box instead of Adw.WrapBox
    """
    Chrome-like tab bar.
    Inherits from Gtk.Box to ensure single-line shrinking behavior.
    """

    __gsignals__ = {
        'tab-reordered': (GObject.SignalFlags.RUN_FIRST, None, (object, int)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)

        self.set_margin_start(4)
        self.set_spacing(0) # Gtk.Box uses spacing, not child_spacing

        self.tabs = []
        self.separators = []
        
        # Drop indicator
        self.drop_indicator = Gtk.Box()
        self.drop_indicator.set_size_request(3, 24)
        self.drop_indicator.add_css_class("tab-drop-indicator")
        self.drop_indicator.set_visible(False)
        self.drop_indicator_position = -1

        # Initial separator
        first_sep = Gtk.Box()
        first_sep.set_size_request(1, 1)
        first_sep.add_css_class("chrome-tab-separator")
        self.append(first_sep)
        self.separators.append(first_sep)
        
        # Drag controllers
        drop_target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        drop_target.connect('drop', self._on_tab_bar_drop)
        drop_target.connect('motion', self._on_tab_bar_motion)
        drop_target.connect('leave', self._on_tab_bar_leave)
        self.add_controller(drop_target)

    def add_tab(self, tab):
        idx = len(self.tabs)

        # CHANGE: Use insert_child_after (Gtk.Box method)
        before_sep = self.separators[idx]
        self.insert_child_after(tab, before_sep)

        new_sep = Gtk.Box()
        new_sep.set_size_request(1, 1)
        new_sep.add_css_class("chrome-tab-separator")
        self.insert_child_after(new_sep, tab)

        self.tabs.append(tab)
        self.separators.insert(idx + 1, new_sep)
        
        tab.tab_bar = self
        tab.separator = new_sep

        self._connect_hover(tab)
        self._update_separators()

    def remove_tab(self, tab):
        if tab not in self.tabs:
            return

        idx = self.tabs.index(tab)

        self.remove(tab) # Gtk.Box method

        sep = self.separators[idx + 1]
        self.remove(sep)
        del self.separators[idx + 1]

        self.tabs.remove(tab)
        self._update_separators()

    def _connect_hover(self, tab):
        motion = Gtk.EventControllerMotion()
        def on_enter(ctrl, x, y):
            if tab in self.tabs:
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
        self._update_separators()

    def _hide_pair(self, i):
        if i > 0:
            self.separators[i].add_css_class("hidden")
        if i + 1 < len(self.separators) - 1:
            self.separators[i + 1].add_css_class("hidden")

    def hide_separators_for_tab(self, tab):
        if tab in self.tabs:
            i = self.tabs.index(tab)
            self._hide_pair(i)
    
    def reorder_tab(self, tab, new_index):
        if tab not in self.tabs: return
        
        old_index = self.tabs.index(tab)
        if old_index == new_index: return
        
        tab_separator = tab.separator
        self.tabs.pop(old_index)
        self.tabs.insert(new_index, tab)
        
        # Determine anchor
        if new_index == 0:
            anchor = self.separators[0]
        else:
            prev_tab = self.tabs[new_index - 1]
            anchor = prev_tab.separator
        
        # CHANGE: Gtk.Box uses insert_child_after to move existing children
        self.insert_child_after(tab, anchor)
        self.insert_child_after(tab_separator, tab)
        
        self.separators = [self.separators[0]] + [t.separator for t in self.tabs]
        self._update_separators()
        self.emit('tab-reordered', tab, new_index)

    def _update_separators(self):
        for sep in self.separators:
            sep.remove_css_class("hidden")
        if self.separators:
            self.separators[0].add_css_class("hidden")
            if len(self.separators) > 1:
                self.separators[-1].add_css_class("hidden")
        for i, tab in enumerate(self.tabs):
            if tab.has_css_class("active"):
                self._hide_pair(i)
    
    def _calculate_drop_position(self, x, y):
        # CHANGE 3: Simplified logic for single-line Gtk.Box
        # We no longer need row logic because they don't wrap.
        
        for i, tab in enumerate(self.tabs):
            success, bounds = tab.compute_bounds(self)
            if not success: continue
                
            tab_center = bounds.origin.x + bounds.size.width / 2
            if x < tab_center:
                return i
        
        # If past the last tab
        return len(self.tabs)
    
    def _show_drop_indicator(self, position):
        if position == self.drop_indicator_position: return
        
        if self.drop_indicator.get_parent():
            self.remove(self.drop_indicator)
        
        self.drop_indicator_position = position
        
        if position == 0:
            self.insert_child_after(self.drop_indicator, self.separators[0])
        elif position < len(self.tabs):
            self.insert_child_after(self.drop_indicator, self.separators[position])
        else:
            if len(self.separators) > len(self.tabs):
                self.insert_child_after(self.drop_indicator, self.separators[-1])
        
        self.drop_indicator.set_visible(True)
    
    def _hide_drop_indicator(self):
        self.drop_indicator.set_visible(False)
        if self.drop_indicator.get_parent():
            self.remove(self.drop_indicator)
        self.drop_indicator_position = -1
    
    def _on_tab_bar_motion(self, target, x, y):
        position = self._calculate_drop_position(x, y)
        self._show_drop_indicator(position)
        return Gdk.DragAction.MOVE
    
    def _on_tab_bar_leave(self, target):
        self._hide_drop_indicator()
    
    def _on_tab_bar_drop(self, target, value, x, y):
        global DRAGGED_TAB
        
        # (Drag drop logic remains mostly the same, just stripped cross-window complexity for brevity 
        # unless specifically needed, but keeping basic logic intact)
        tab_data = None
        if isinstance(value, str):
            try: tab_data = json.loads(value)
            except: pass
        
        target_window = None
        parent = self.get_parent()
        while parent:
            if isinstance(parent, Adw.ApplicationWindow):
                target_window = parent
                break
            parent = parent.get_parent()
        
        if not target_window: return False
        
        # Simplified same-window check
        dragged_tab = DRAGGED_TAB
        if not dragged_tab or dragged_tab not in self.tabs:
            return False
        
        drop_position = self._calculate_drop_position(x, y)
        current_position = self.tabs.index(dragged_tab)
        
        if current_position < drop_position:
            drop_position -= 1
        
        if current_position != drop_position:
            self.reorder_tab(dragged_tab, drop_position)
        
        self._hide_drop_indicator()
        return True

# --------------------------------------------------------------------
# DEMO APP (Unchanged)
# --------------------------------------------------------------------
class DemoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.chrometab.demo")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        css = Gtk.CssProvider()
        css.load_from_data(CSS_OVERLAY_SCROLLBAR.encode())
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Adw.ApplicationWindow(application=self)
        win.set_default_size(900, 250)
        win.set_title("Chrome-style Tabs Demo (Shrinking)")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_bottom(12)
        win.set_content(root)

        self.tabbar = ChromeTabBar()
        root.append(self.tabbar)

        self.content = Gtk.Label()
        self.content.set_markup("<big>Content</big>")
        self.content.set_vexpand(True)
        self.content.set_hexpand(True)
        root.append(self.content)

        btnbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_btn = Gtk.Button(label="Add Tab")
        add_btn.connect("clicked", lambda b: self._add_tab())
        btnbox.append(add_btn)
        root.append(btnbox)

        # Add initial tabs
        for title in ["README.md", "Short", "A very long file name that should shrink the tab", "Config.json"]:
            self._add_tab(title)

        win.present()

    def _on_tab_activated(self, tab):
        self.tabbar.set_tab_active(tab)
        self.content.set_markup(f"<big>{tab.get_title()}</big>")

    def _on_tab_closed(self, tab):
        self.tabbar.remove_tab(tab)

    def _add_tab(self, title="New Tab"):
        tab = ChromeTab(title)
        tab.connect("activate-requested", self._on_tab_activated)
        tab.connect("close-requested", self._on_tab_closed)
        self.tabbar.add_tab(tab)
        self.tabbar.set_tab_active(tab)

if __name__ == "__main__":
    app = DemoApp()
    app.run(None)
