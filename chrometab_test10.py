#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GObject, Pango, GLib

Adw.init()

TAB_CSS = """
/* Tab base - fixed visual height controlled via set_size_request in Python */
.chrome-tab {
    padding: 0 8px;
    border-radius: 6px 6px 0 0;
    background-color: transparent;
}

/* First tab: no separator (applied from Python) */
.chrome-tab.first-tab {
    /* visual: nothing special needed here */
}

/* Active tab style (applied from Python) */
.chrome-tab.active {
    background-color: alpha(@window_fg_color, 0.12);
}

/* Hover */
.chrome-tab:hover {
    background-color: alpha(@window_fg_color, 0.08);
}

/* Close button style */
.chrome-tab-close-button {
    min-width: 18px;
    min-height: 18px;
    padding: 0;
    margin: 0;
    border-radius: 4px;
}

/* Modified dot (we draw via drawing area but style can apply) */
.modified-dot {
    min-width: 6px;
    min-height: 6px;
    border-radius: 3px;
    background: #4a9eff;
}

/* Separator widget style */
.chrome-tab-separator {
    background-color: alpha(@window_fg_color, 0.15);
    border-radius: 2px;
    transition: opacity 120ms linear;
    opacity: 1.0;
}

/* Hidden class to fade separators (applied from Python) */
.chrome-tab-separator.hidden {
    opacity: 0.0;
}

/* Small drop indicator */
.tab-drop-indicator {
    background: rgba(74, 158, 255, 0.9);
    min-width: 2px;
}
"""

# -----------------------
# ChromeTab
# -----------------------
class ChromeTab(Gtk.Box):
    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, title="Untitled", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        FIXED_H = 32

        self.add_css_class("chrome-tab")
        # enforce fixed size via set_size_request (width flexible, height fixed)
        self.set_size_request(-1, FIXED_H)
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_valign(Gtk.Align.CENTER)

        # overlay holds the main button (content) and the floating close button
        overlay = Gtk.Overlay()
        # ensure overlay does not expand vertically beyond tab
        overlay.set_size_request(-1, FIXED_H)
        overlay.set_valign(Gtk.Align.CENTER)

        # dot + label container
        dot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot_box.set_valign(Gtk.Align.CENTER)
        dot_box.set_vexpand(False)

        self.modified_dot = Gtk.DrawingArea()
        self.modified_dot.set_size_request(6, 6)
        self.modified_dot.set_visible(False)
        self.modified_dot.add_css_class("modified-dot")
        # draw the dot as filled circle
        def draw_dot(area, cr, w, h):
            if area.get_visible():
                cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 2 * 3.14159)
                cr.set_source_rgb(0.29, 0.62, 1.0)
                cr.fill()
        self.modified_dot.set_draw_func(draw_dot)
        dot_box.append(self.modified_dot)

        self.label = Gtk.Label()
        self.label.set_text(title)
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label.set_single_line_mode(True)
        self.label.set_max_width_chars(20)
        self.label.set_hexpand(True)
        self.label.set_halign(Gtk.Align.START)
        self.label.set_valign(Gtk.Align.CENTER)
        dot_box.append(self.label)

        # put dot_box inside a flat button so it responds like a tab
        self.tab_button = Gtk.Button()
        self.tab_button.add_css_class("flat")
        self.tab_button.set_child(dot_box)
        # prevent inner button from expanding vertically beyond the tab height
        self.tab_button.set_size_request(-1, FIXED_H)
        self.tab_button.set_hexpand(True)
        self.tab_button.set_vexpand(False)
        overlay.set_child(self.tab_button)

        # close button as overlay - floating, won't affect parent's allocation
        if closeable:
            self.close_button = Gtk.Button()
            self.close_button.set_icon_name("window-close-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("chrome-tab-close-button")
            self.close_button.set_size_request(18, 18)
            # align to right-center and use margin to nudge inside
            self.close_button.set_halign(Gtk.Align.END)
            self.close_button.set_valign(Gtk.Align.CENTER)
            self.close_button.set_margin_end(6)
            self.close_button.connect("clicked", lambda b: self.emit("close-requested"))
            overlay.add_overlay(self.close_button)

        self.append(overlay)

        # state
        self._original_title = title
        self._is_active = False
        self.tab_bar = None
        self.separator = None  # assigned by tabbar

        # click -> activate
        click = Gtk.GestureClick()
        click.connect("released", lambda g, n, x, y: self.emit("activate-requested"))
        self.tab_button.add_controller(click)

        # simple drag source (index as string)
        drag_src = Gtk.DragSource()
        drag_src.set_actions(Gdk.DragAction.MOVE)
        drag_src.connect("prepare", self._on_drag_prepare)
        drag_src.connect("drag-begin", self._on_drag_begin)
        drag_src.connect("drag-end", self._on_drag_end)
        self.tab_button.add_controller(drag_src)

    def set_title(self, title):
        self._original_title = title
        self.label.set_text(title)

    def get_title(self):
        return self._original_title

    def set_active(self, active: bool):
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")

    def set_modified(self, modified: bool):
        self.modified_dot.set_visible(modified)
        if modified:
            self.add_css_class("modified")
        else:
            self.remove_css_class("modified")

    # drag handlers (simple)
    def _on_drag_prepare(self, src, x, y):
        if self.tab_bar and self in self.tab_bar.tabs:
            idx = self.tab_bar.tabs.index(self)
            return Gdk.ContentProvider.new_for_value(str(idx))
        return None

    def _on_drag_begin(self, src, drag):
        # visual icon of the tab
        src.set_icon(Gtk.WidgetPaintable.new(self), 0, 0)

    def _on_drag_end(self, src, drag, data):
        pass


# -----------------------
# ChromeTabBar (wrapbox + separators)
# -----------------------
class ChromeTabBar(Adw.WrapBox):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child_spacing(0)
        self.set_margin_start(4)

        self.tabs = []
        self.separators = []  # separator before each tab and one final

        # initial leading separator (hidden)
        first_sep = Gtk.Box()
        first_sep.set_size_request(4, 15)  # thickness x height
        first_sep.add_css_class("chrome-tab-separator")
        first_sep.add_css_class("hidden")
        self.append(first_sep)
        self.separators.append(first_sep)

        # drop indicator (hidden by default)
        self.drop_indicator = Gtk.Box()
        self.drop_indicator.set_size_request(2, 20)
        self.drop_indicator.add_css_class("tab-drop-indicator")
        self.drop_indicator.set_visible(False)

    def add_tab(self, tab: ChromeTab):
        idx = len(self.tabs)

        # insert tab after separator[idx]
        before_sep = self.separators[idx]
        self.insert_child_after(tab, before_sep)

        # create separator after this tab
        new_sep = Gtk.Box()
        new_sep.set_size_request(4, 15)  # thickness x height
        new_sep.add_css_class("chrome-tab-separator")
        self.insert_child_after(new_sep, tab)

        # bookkeeping
        self.tabs.append(tab)
        self.separators.insert(idx + 1, new_sep)

        tab.tab_bar = self
        tab.separator = new_sep

        # connect signals
        tab.connect("activate-requested", lambda t: self.set_tab_active(t))
        tab.connect("close-requested", lambda t: self.remove_tab(t))

        # mark first tab with class
        for i, t in enumerate(self.tabs):
            if i == 0:
                t.add_css_class("first-tab")
            else:
                t.remove_css_class("first-tab")

        # update separators (hide around active)
        self._update_separators()

    def remove_tab(self, tab: ChromeTab):
        if tab not in self.tabs:
            return
        idx = self.tabs.index(tab)

        # remove tab widget and its trailing separator
        self.remove(tab)
        sep = self.separators[idx + 1]
        self.remove(sep)

        del self.tabs[idx]
        del self.separators[idx + 1]

        # reassign first-tab class
        if self.tabs:
            self.tabs[0].add_css_class("first-tab")
        self._update_separators()

    def set_tab_active(self, tab: ChromeTab):
        # set active flag on tabs and set adjacent-to-active for previous tab
        prev_adj_index = None
        for i, t in enumerate(self.tabs):
            if t is tab:
                t.set_active(True)
                # mark previous tab (for hiding its separator)
                prev_adj_index = i - 1
            else:
                t.set_active(False)
                t.remove_css_class("adjacent-to-active")

        # assign adjacent-to-active to previous tab if exists
        if prev_adj_index is not None and prev_adj_index >= 0:
            self.tabs[prev_adj_index].add_css_class("adjacent-to-active")

        self._update_separators()

    def _update_separators(self):
        # clear hidden on all separators first
        for sep in self.separators:
            sep.remove_css_class("hidden")

        # hide the leading and trailing separators (visual edge)
        if self.separators:
            self.separators[0].add_css_class("hidden")
            if len(self.separators) - 1 >= 0:
                self.separators[-1].add_css_class("hidden")

        # hide separators adjacent to active tab:
        # for each tab that is active, hide sep before and after it
        for i, t in enumerate(self.tabs):
            if t._is_active:
                # hide separator before tab (i)
                if i >= 0 and i < len(self.separators):
                    self.separators[i].add_css_class("hidden")
                # hide separator after tab (i+1)
                if (i + 1) < len(self.separators):
                    self.separators[i + 1].add_css_class("hidden")

            # also hide separator before tab if tab has 'adjacent-to-active' class
            if t.has_css_class("adjacent-to-active"):
                if i >= 0 and i < len(self.separators):
                    self.separators[i].add_css_class("hidden")


# -----------------------
# Demo application
# -----------------------
class DemoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.chrometab.demo")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        css = Gtk.CssProvider()
        css.load_from_data(TAB_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = Adw.ApplicationWindow(application=app)
        win.set_default_size(900, 220)
        win.set_title("Chrome-style Tabs Demo")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_bottom(12)
        win.set_content(root)

        # tabbar
        self.tabbar = ChromeTabBar()
        root.append(self.tabbar)

        # content placeholder
        self.content = Gtk.Label()
        self.content.set_markup("<big>README.md</big>\n\nTab content area")
        self.content.set_vexpand(True)
        root.append(self.content)

        # control
        btnbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_btn = Gtk.Button(label="Add Tab")
        add_btn.connect("clicked", lambda b: self._add_tab())
        btnbox.append(add_btn)
        root.append(btnbox)

        # initial tabs
        t1 = ChromeTab("README.md")
        self.tabbar.add_tab(t1)
        self.tabbar.set_tab_active(t1)

        t2 = ChromeTab("سلام دنیا — اردو ٹیب")
        t2.set_modified(True)
        self.tabbar.add_tab(t2)

        t3 = ChromeTab("a-very-long-file-name-to-test-ellipsize.txt")
        self.tabbar.add_tab(t3)

        win.present()

    def _add_tab(self):
        tab = ChromeTab("New Tab")
        self.tabbar.add_tab(tab)
        self.tabbar.set_tab_active(tab)


if __name__ == "__main__":
    app = DemoApp()
    app.run(None)

