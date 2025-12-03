#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GObject, Pango, GLib

Adw.init()

# Global for tracking dragged tab
DRAGGED_TAB = None

# Shared CSS for all tab components
TAB_CSS = b"""
/* Fixed tab height rules to prevent vertical expansion with RTL scripts */
.chrome-tab {
    min-height: 32px;
    padding: 0 8px;
    border-radius: 6px 6px 0 0;
}

.chrome-tab label {
    padding-top: 0;
    padding-bottom: 0;
}

.chrome-tab:hover {
    background-color: alpha(@window_fg_color, 0.1);
}

.chrome-tab.active {
    background-color: alpha(@window_fg_color, 0.15);
    min-height: 32px;
}

.chrome-tab.active:hover {
    background-color: alpha(@window_fg_color, 0.2);
}

.chrome-tab.dragging {
    opacity: 0.5;
}

.chrome-tab-close-button {
    min-width: 20px;
    min-height: 20px;
    padding: 0;
    margin: 0;
}

.chrome-tab.modified .modified-dot {
    background: #4a9eff;
}

.tab-drop-indicator {
    background: rgba(74, 158, 255, 0.5);
    min-width: 2px;
}

.tab-separator {
    min-width: 1px;
    background-color: alpha(@window_fg_color, 1);
    margin-top: 8px;
    margin-bottom: 8px;
}
"""


class ChromeTab(Gtk.Box):
    """A custom tab widget that behaves like Chrome tabs (fixed vertical size)."""

    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, title="Untitled", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        FIXED_H = 32
        
        # Do NOT allow vertical expansion
        self.set_vexpand(False)
        self.set_valign(Gtk.Align.CENTER)
        self.set_size_request(120, FIXED_H)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.add_css_class("chrome-tab")

        # Overlay root
        overlay = Gtk.Overlay()
        overlay.set_vexpand(False)
        overlay.set_valign(Gtk.Align.CENTER)

        # Dot + Title
        dot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot_box.set_vexpand(False)
        dot_box.set_valign(Gtk.Align.CENTER)

        self.modified_dot = Gtk.DrawingArea()
        self.modified_dot.set_size_request(8, 8)
        self.modified_dot.set_visible(False)
        self.modified_dot.add_css_class("modified-dot")
        self.modified_dot.set_draw_func(self._draw_dot)
        dot_box.append(self.modified_dot)

        self.label = Gtk.Label()
        self.label.set_text(title)
        self.label.set_single_line_mode(True)
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label.set_max_width_chars(15)
        self.label.set_wrap(False)
        self.label.set_halign(Gtk.Align.START)
        self.label.set_valign(Gtk.Align.CENTER)
        self.label.set_vexpand(False)
        dot_box.append(self.label)

        # Tab content container (Box instead of Button)
        self.tab_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.tab_content.append(dot_box)
        self.tab_content.set_hexpand(True)
        self.tab_content.set_vexpand(False)
        self.tab_content.set_valign(Gtk.Align.CENTER)
        self.tab_content.set_size_request(-1, FIXED_H)
        
        # Add click gesture to the content box
        click_gesture = Gtk.GestureClick()
        click_gesture.connect('released', self._on_tab_clicked)
        self.tab_content.add_controller(click_gesture)
        
        overlay.set_child(self.tab_content)

        # Close Button
        if closeable:
            self.close_button = Gtk.Button()
            self.close_button.set_icon_name("window-close-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("chrome-tab-close-button")
            self.close_button.set_size_request(20, 20)
            self.close_button.set_halign(Gtk.Align.END)
            self.close_button.set_valign(Gtk.Align.CENTER)
            self.close_button.connect('clicked', self._on_close_clicked)
            overlay.add_overlay(self.close_button)

        self.append(overlay)

        # State
        self._is_active = False
        self._original_title = title
        self.tab_bar = None

        # Setup drag source
        self._setup_drag_source()

    def _draw_dot(self, area, cr, width, height):
        """Draw the modified indicator dot"""
        if self.modified_dot.get_visible():
            cr.arc(width / 2, height / 2, 3, 0, 2 * 3.14159)
            cr.set_source_rgb(0.29, 0.62, 1.0)  # Blue color
            cr.fill()

    def _setup_drag_source(self):
        """Setup drag source for tab reordering"""
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.tab_content.add_controller(drag_source)

    def _on_drag_prepare(self, source, x, y):
        """Prepare drag data"""
        global DRAGGED_TAB
        DRAGGED_TAB = self
        
        # Create simple content with tab index
        if self.tab_bar and self in self.tab_bar.tabs:
            tab_index = self.tab_bar.tabs.index(self)
            content = Gdk.ContentProvider.new_for_value(str(tab_index))
            return content
        return None

    def _on_drag_begin(self, source, drag):
        """Visual feedback when drag starts"""
        self.add_css_class("dragging")
        paintable = Gtk.WidgetPaintable.new(self)
        source.set_icon(paintable, 0, 0)

    def _on_drag_end(self, source, drag, delete_data):
        """Clean up after drag"""
        global DRAGGED_TAB
        DRAGGED_TAB = None
        self.remove_css_class("dragging")

    def _on_tab_clicked(self, gesture, n_press, x, y):
        """Handle tab activation"""
        self.emit('activate-requested')

    def _on_close_clicked(self, button):
        """Handle close button"""
        self.emit('close-requested')

    def set_modified(self, modified: bool):
        """Set modified state"""
        self.modified_dot.set_visible(modified)
        if modified:
            self.add_css_class("modified")
        else:
            self.remove_css_class("modified")
        self.queue_draw()

    def set_title(self, title):
        """Set tab title"""
        self._original_title = title
        self.label.set_text(title)

    def get_title(self):
        """Get tab title"""
        return self._original_title

    def set_active(self, active):
        """Set active state"""
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")

    def is_active(self):
        """Check if tab is active"""
        return self._is_active


class ChromeTabBar(Gtk.Box):
    """Custom tab bar with drag-and-drop reordering"""
    
    __gsignals__ = {
        'tab-activated': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'tab-closed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tabs = []
        self.separators = []  # Track separators between tabs
        self.active_tab_index = -1
        self.set_halign(Gtk.Align.START)
        
        # Setup drop target for reordering
        self._setup_drop_target()
        
        # Drop indicator
        self.drop_indicator = Gtk.Box()
        self.drop_indicator.add_css_class("tab-drop-indicator")
        self.drop_indicator.set_size_request(2, 32)
        self.drop_indicator.set_visible(False)

    def _setup_drop_target(self):
        """Setup drop target for receiving dragged tabs"""
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect('enter', self._on_drop_enter)
        drop_target.connect('motion', self._on_drop_motion)
        drop_target.connect('leave', self._on_drop_leave)
        drop_target.connect('drop', self._on_drop)
        self.add_controller(drop_target)

    def _on_drop_enter(self, drop_target, x, y):
        """Handle drag entering the tab bar"""
        return Gdk.DragAction.MOVE

    def _on_drop_motion(self, drop_target, x, y):
        """Handle drag motion over tab bar"""
        global DRAGGED_TAB
        if DRAGGED_TAB is None or DRAGGED_TAB not in self.tabs:
            return Gdk.DragAction.NONE
        
        # Find drop position
        drop_index = self._get_drop_index(x)
        
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, drop_target):
        """Handle drag leaving the tab bar"""
        pass

    def _on_drop(self, drop_target, value, x, y):
        """Handle tab drop"""
        global DRAGGED_TAB
        if DRAGGED_TAB is None or DRAGGED_TAB not in self.tabs:
            return False
        
        old_index = self.tabs.index(DRAGGED_TAB)
        new_index = self._get_drop_index(x)
        
        if old_index != new_index:
            self.reorder_tab(old_index, new_index)
        
        return True

    def _get_drop_index(self, x):
        """Calculate where to drop the tab based on x position"""
        for i, tab in enumerate(self.tabs):
            allocation = tab.get_allocation()
            tab_x = allocation.x + allocation.width / 2
            if x < tab_x:
                return i
        return len(self.tabs)

    def add_tab(self, tab: ChromeTab):
        """Add a new tab"""
        tab.tab_bar = self
        tab.connect('activate-requested', self._on_tab_activate)
        tab.connect('close-requested', self._on_tab_close)
        
        # Add separator before tab (except for first tab)
        if len(self.tabs) > 0:
            separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            separator.add_css_class("tab-separator")
            self.separators.append(separator)
            self.append(separator)
        
        self.tabs.append(tab)
        self.append(tab)
        
        # Activate first tab by default
        if len(self.tabs) == 1:
            self.set_active_tab(0)
        else:
            # Update separators when adding new tab
            self._update_separators()

    def remove_tab(self, index: int):
        """Remove a tab"""
        if 0 <= index < len(self.tabs):
            tab = self.tabs[index]
            self.tabs.pop(index)
            self.remove(tab)
            
            # Remove associated separator
            # Separator is before the tab (except first tab has no separator)
            if index > 0 and len(self.separators) >= index:
                sep = self.separators.pop(index - 1)
                self.remove(sep)
            elif index == 0 and len(self.separators) > 0:
                # If removing first tab, remove separator that was after it
                sep = self.separators.pop(0)
                self.remove(sep)
            
            # Activate adjacent tab if needed
            if self.active_tab_index == index:
                if self.tabs:
                    new_index = min(index, len(self.tabs) - 1)
                    self.set_active_tab(new_index)
                else:
                    self.active_tab_index = -1
            elif self.active_tab_index > index:
                # Adjust active index if tab before it was removed
                self.active_tab_index -= 1
                
            # Update separators after removal
            if self.tabs:
                self._update_separators()

    def reorder_tab(self, old_index: int, new_index: int):
        """Reorder tabs via drag and drop"""
        if old_index == new_index:
            return
        
        # This is complex with separators, so rebuild the tab bar
        tab = self.tabs.pop(old_index)
        
        # Adjust new index if needed
        if new_index > old_index:
            new_index -= 1
        
        self.tabs.insert(new_index, tab)
        
        # Update active index
        if self.active_tab_index == old_index:
            self.active_tab_index = new_index
        elif old_index < self.active_tab_index <= new_index:
            self.active_tab_index -= 1
        elif new_index <= self.active_tab_index < old_index:
            self.active_tab_index += 1
        
        # Rebuild the UI
        self._rebuild_tab_bar()

    def _rebuild_tab_bar(self):
        """Rebuild the entire tab bar with tabs and separators"""
        # Remove all children
        child = self.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child
        
        # Clear separators list
        self.separators.clear()
        
        # Re-add tabs with separators
        for i, tab in enumerate(self.tabs):
            if i > 0:
                separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                separator.add_css_class("tab-separator")
                self.separators.append(separator)
                self.append(separator)
            self.append(tab)
        
        # Update separator visibility
        self._update_separators()

    def set_active_tab(self, index: int):
        """Set the active tab"""
        if 0 <= index < len(self.tabs):
            # Deactivate old tab
            if 0 <= self.active_tab_index < len(self.tabs):
                self.tabs[self.active_tab_index].set_active(False)
            
            # Activate new tab
            self.active_tab_index = index
            self.tabs[index].set_active(True)
            
            # Update separators
            self._update_separators()
            
            self.emit('tab-activated', index)

    def _update_separators(self):
        """Update separator visibility based on active tab position"""
        # Separators are between tabs
        # separator[i] is between tabs[i] and tabs[i+1]
        for i, separator in enumerate(self.separators):
            # Hide separator if either adjacent tab is active
            left_tab_index = i
            right_tab_index = i + 1
            
            if left_tab_index == self.active_tab_index or right_tab_index == self.active_tab_index:
                separator.set_visible(False)
            else:
                separator.set_visible(True)

    def _on_tab_activate(self, tab):
        """Handle tab activation"""
        if tab in self.tabs:
            index = self.tabs.index(tab)
            self.set_active_tab(index)

    def _on_tab_close(self, tab):
        """Handle tab close request"""
        if tab in self.tabs:
            index = self.tabs.index(tab)
            self.emit('tab-closed', index)
            self.remove_tab(index)


# Demo application
class DemoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.chrometab.demo")
        self.connect("activate", self.on_activate)
        self.tab_counter = 0

    def on_activate(self, app):
        # Load CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(TAB_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), 
            css_provider, 
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = Adw.ApplicationWindow(application=app)
        win.set_default_size(800, 200)
        win.set_title("Chrome-style Tabs Demo")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_bottom(12)
        win.set_content(vbox)

        # Tab bar
        self.tabbar = ChromeTabBar()
        self.tabbar.connect('tab-activated', self._on_tab_activated)
        self.tabbar.connect('tab-closed', self._on_tab_closed)
        vbox.append(self.tabbar)

        # Content area placeholder
        self.content_label = Gtk.Label()
        self.content_label.set_markup("<big>Tab content area</big>\n\nClick tabs to switch, drag to reorder")
        self.content_label.set_vexpand(True)
        vbox.append(self.content_label)

        # Button to add new tabs
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        add_button = Gtk.Button(label="Add Tab")
        add_button.connect('clicked', self._on_add_tab)
        button_box.append(add_button)
        
        vbox.append(button_box)

        # Add initial tabs
        t1 = ChromeTab("README.md")
        self.tabbar.add_tab(t1)

        # Urdu (RTL) example
        t2 = ChromeTab("سلام دنیا — اردو ٹیب")
        t2.set_modified(True)
        self.tabbar.add_tab(t2)

        # Long name example
        t3 = ChromeTab("a-very-long-file-name-to-test-ellipsize.txt")
        self.tabbar.add_tab(t3)

        self.tab_counter = 3
        win.present()

    def _on_add_tab(self, button):
        """Add a new tab"""
        self.tab_counter += 1
        tab = ChromeTab(f"New Tab {self.tab_counter}")
        self.tabbar.add_tab(tab)
        self.tabbar.set_active_tab(len(self.tabbar.tabs) - 1)

    def _on_tab_activated(self, tabbar, index):
        """Handle tab activation"""
        if 0 <= index < len(tabbar.tabs):
            tab_title = tabbar.tabs[index].get_title()
            self.content_label.set_markup(
                f"<big><b>{tab_title}</b></big>\n\nTab {index + 1} is now active"
            )

    def _on_tab_closed(self, tabbar, index):
        """Handle tab closing"""
        print(f"Tab {index} closed")


if __name__ == "__main__":
    app = DemoApp()
    app.run(None)
