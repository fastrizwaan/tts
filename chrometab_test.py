#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GObject, Pango, GLib

Adw.init()

CSS = b"""
/* Fixed tab height rules to prevent vertical expansion with RTL scripts */
.chrome-tab {
    min-height: 28px;
    padding-top: 0;
    padding-bottom: 0;
}

.chrome-tab > overlay > button {
    min-height: 28px;
    padding-top: 0;
    padding-bottom: 0;
}

.chrome-tab label {
    padding-top: 0;
    padding-bottom: 0;
}

/* (retain your other styles as needed) */
.chrome-tab.active {
    padding-top: 4px;
    padding-bottom: 4px;
}
"""

# Minimal ChromeTab + ChromeTabBar adapted to keep names but force fixed heights
class ChromeTab(Gtk.Box):
    """A custom tab widget that behaves like Chrome tabs (fixed vertical size)."""

    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, title="Untitled 1", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        FIXED_H = 28  # hard tab height enforced at widget level

        # Do NOT allow vertical expansion — RTL fonts cannot push taller metrics now
        self.set_vexpand(False)
        self.set_valign(Gtk.Align.CENTER)
        self.set_size_request(-1, FIXED_H)

        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.add_css_class("chrome-tab")

        # ────────────────────────────────────────────────────────────
        # Overlay root
        # ────────────────────────────────────────────────────────────
        overlay = Gtk.Overlay()
        overlay.set_vexpand(False)
        overlay.set_valign(Gtk.Align.CENTER)

        # ────────────────────────────────────────────────────────────
        # Dot + Title
        # ────────────────────────────────────────────────────────────
        dot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot_box.set_vexpand(False)
        dot_box.set_valign(Gtk.Align.CENTER)

        self.modified_dot = Gtk.DrawingArea()
        self.modified_dot.set_size_request(8, 8)
        self.modified_dot.set_visible(False)
        dot_box.append(self.modified_dot)

        self.label = Gtk.Label()
        self.label.set_text(title)
        self.label.set_single_line_mode(True)
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label.set_max_width_chars(20)
        self.label.set_wrap(False)

        self.label.set_halign(Gtk.Align.START)
        self.label.set_valign(Gtk.Align.CENTER)
        self.label.set_vexpand(False)

        dot_box.append(self.label)

        # ────────────────────────────────────────────────────────────
        # Tab button wrapper (gets clicks + DnD controllers)
        # ────────────────────────────────────────────────────────────
        self.tab_button = Gtk.Button()
        self.tab_button.add_css_class("flat")
        self.tab_button.set_child(dot_box)
        self.tab_button.set_hexpand(True)
        self.tab_button.set_vexpand(False)
        self.tab_button.set_valign(Gtk.Align.CENTER)

        # Force fixed height at widget level
        self.tab_button.set_size_request(-1, FIXED_H)

        overlay.set_child(self.tab_button)

        # ────────────────────────────────────────────────────────────
        # Close Button (unchanged)
        # ────────────────────────────────────────────────────────────
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

        # ────────────────────────────────────────────────────────────
        # KEEPING ALL YOUR ORIGINAL STATE, DRAGGING, CONTROLLERS
        # ────────────────────────────────────────────────────────────
        self._is_active = False
        self._original_title = title
        self.tab_bar = None  # parent ChromeTabBar assigned later

        # Dragging (unchanged)
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect('prepare', self._on_drag_prepare)
        drag_source.connect('drag-begin', self._on_drag_begin)
        drag_source.connect('drag-end', self._on_drag_end)
        self.tab_button.add_controller(drag_source)

        # Gestures (unchanged)
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(0)
        click_gesture.connect('pressed', self._on_tab_pressed)
        click_gesture.connect('released', self._on_tab_released)
        self.tab_button.add_controller(click_gesture)


    # keep existing public API names
    def set_modified(self, modified: bool):
        self.modified_dot.set_visible(modified)
        if modified:
            self.add_css_class("modified")
        else:
            self.remove_css_class("modified")
        self.queue_draw()

    def _on_tab_pressed(self, gesture, n_press, x, y):
        # hide separators or other hover effects might be triggered externally
        if self.tab_bar:
            try:
                self.tab_bar.hide_separators_for_tab(self)
            except Exception:
                pass

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
        # Show modified dot if css class present
        self.modified_dot.set_visible(self.has_css_class("modified"))
        self.label.set_text(self._original_title)

    def set_active(self, active):
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")
    # ───────────────────────────────
    #  DRAG & DROP HANDLERS (restore)
    # ───────────────────────────────

    def _on_drag_prepare(self, source, x, y):
        import json

        window = None
        if self.tab_bar and hasattr(self, '_page'):
            parent = self.tab_bar.get_parent()
            while parent:
                if isinstance(parent, Adw.ApplicationWindow):
                    window = parent
                    break
                parent = parent.get_parent()

        tab_data = {
            'window_id': id(window) if window else 0,
            'tab_index': self.tab_bar.tabs.index(self) if self.tab_bar and self in self.tab_bar.tabs else -1,
        }

        if hasattr(self, '_page'):
            page = self._page
            tab_root = page.get_child()

            def serialize(widget):
                if isinstance(widget, Gtk.Box):
                    child = widget.get_first_child()
                    return serialize(child) if child else None
                elif hasattr(widget, '_editor'):
                    editor = widget._editor
                    return {
                        'type': 'editor',
                        'content': editor.get_text(),
                        'file_path': editor.current_file_path,
                        'title': editor.get_title(),
                        'untitled_number': getattr(editor, 'untitled_number', None),
                    }
                elif isinstance(widget, Gtk.Paned):
                    return {
                        'type': 'paned',
                        'orientation': (
                            'horizontal'
                            if widget.get_orientation() == Gtk.Orientation.HORIZONTAL
                            else 'vertical'
                        ),
                        'position': widget.get_position(),
                        'start_child': serialize(widget.get_start_child()),
                        'end_child': serialize(widget.get_end_child()),
                    }
                return None

            structure = serialize(tab_root)
            tab_data['structure'] = structure

            editor = tab_root._editor
            tab_data['content'] = editor.get_text()
            tab_data['file_path'] = editor.current_file_path
            tab_data['title'] = editor.get_title()
            tab_data['is_modified'] = self.has_css_class("modified")
            tab_data['untitled_number'] = getattr(editor, 'untitled_number', None)

        json_data = json.dumps(tab_data)
        return Gdk.ContentProvider.new_for_value(json_data)
    def _on_drag_begin(self, source, drag):
        import gi
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
    """Very small wrap-like tab bar for demo; keeps API idea similar to your WrapBox."""
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tabs = []
        self.set_halign(Gtk.Align.START)

    def add_tab(self, tab: ChromeTab):
        tab.tab_bar = self
        self.tabs.append(tab)
        self.append(tab)

    # Minimal helpers used by ChromeTab methods above
    def hide_separators_for_tab(self, tab):
        # no-op in minimal demo
        return

# Small test application
class DemoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.chrometab.demo")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        # Load CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Adw.ApplicationWindow(application=app)
        win.set_default_size(640, 120)
        win.set_title("ChromeTab fixed-height demo")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_top(8)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)
        vbox.set_margin_bottom(8)
        win.set_content(vbox)

        # Tab bar
        tabbar = ChromeTabBar()
        vbox.append(tabbar)

        # English
        t1 = ChromeTab("README.md")
        t1.set_active(True)
        tabbar.add_tab(t1)

        # Urdu (RTL) example — should not increase height
        urdu_text = "سلام دنیا — اردو ٹیب"
        t2 = ChromeTab(urdu_text)
        t2.set_modified(True)
        tabbar.add_tab(t2)

        # Long LTR example
        t3 = ChromeTab("a-very-long-file-name-to-test-ellipsize.txt")
        tabbar.add_tab(t3)

        win.present()

if __name__ == "__main__":
    app = DemoApp()
    app.run(None)

