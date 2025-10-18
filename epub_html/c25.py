#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse, signal, sys, math
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango
from ebooklib import epub

Adw.init()

class EpubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)

        # EPUB state
        self.current_book = None
        self.chapters = []
        self.current_chapter = 0
        self.temp_dir = None

        # Column / paging settings
        self.column_mode = 'width'
        self.fixed_column_count = 2
        self.desired_column_width = 400
        self.column_gap = 40
        self.column_padding = 20
        self.actual_column_width = self.desired_column_width

        # Resize handling
        self.resize_timeout_id = None

        # UI
        self.setup_ui()
        self.setup_navigation()

    def setup_ui(self):
        self.toolbar_view = Adw.ToolbarView()

        # Try Adw.OverlaySplitView, fallback to Gtk.Box if unavailable
        try:
            container = Adw.OverlaySplitView()
            using_overlay = True
        except Exception:
            container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            using_overlay = False

        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)

        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.add_css_class("flat")
        menu = Gio.Menu()

        columns_menu = Gio.Menu()
        for i in range(1, 11):
            columns_menu.append(f"{i} Column{'s' if i>1 else ''}", f"app.set-columns({i})")
        menu.append_submenu("Columns (fixed)", columns_menu)

        width_menu = Gio.Menu()
        for w in (50,100,150,200,300,350,400,450,500):
            width_menu.append(f"{w}px width", f"app.set-column-width({w})")
        menu.append_submenu("Use column width", width_menu)

        menu_button.set_menu_model(menu)

        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.add_css_class("flat")
        open_button.connect("clicked", self.on_open_clicked)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav_box.set_spacing(6)

        # Navigation buttons
        self.prev_chapter_btn = Gtk.Button()
        self.prev_chapter_btn.set_icon_name("media-skip-backward-symbolic")
        self.prev_chapter_btn.set_tooltip_text("Previous Chapter")
        self.prev_chapter_btn.add_css_class("flat")
        self.prev_chapter_btn.connect("clicked", self.on_prev_chapter)
        self.prev_chapter_btn.set_sensitive(False)
        nav_box.append(self.prev_chapter_btn)

        self.prev_page_btn = Gtk.Button()
        self.prev_page_btn.set_icon_name("go-previous-symbolic")
        self.prev_page_btn.set_tooltip_text("Previous Page")
        self.prev_page_btn.add_css_class("flat")
        self.prev_page_btn.connect("clicked", self.on_prev_page)
        self.prev_page_btn.set_sensitive(False)
        nav_box.append(self.prev_page_btn)

        self.page_info = Gtk.Label()
        self.page_info.set_text("--/--")
        self.page_info.add_css_class("dim-label")
        self.page_info.set_margin_start(6)
        self.page_info.set_margin_end(6)
        nav_box.append(self.page_info)

        self.next_page_btn = Gtk.Button()
        self.next_page_btn.set_icon_name("go-next-symbolic")
        self.next_page_btn.set_tooltip_text("Next Page")
        self.next_page_btn.add_css_class("flat")
        self.next_page_btn.connect("clicked", self.on_next_page)
        self.next_page_btn.set_sensitive(False)
        nav_box.append(self.next_page_btn)

        self.next_chapter_btn = Gtk.Button()
        self.next_chapter_btn.set_icon_name("media-skip-forward-symbolic")
        self.next_chapter_btn.set_tooltip_text("Next Chapter")
        self.next_chapter_btn.add_css_class("flat")
        self.next_chapter_btn.connect("clicked", self.on_next_chapter)
        self.next_chapter_btn.set_sensitive(False)
        nav_box.append(self.next_chapter_btn)

        try:
            header_bar.pack_start(open_button)
            header_bar.pack_start(nav_box)
            header_bar.pack_end(menu_button)
        except AttributeError:
            # older libadwaita variants
            button_box_start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_start.set_spacing(6)
            button_box_start.append(open_button)
            button_box_start.append(nav_box)
            button_box_end = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_end.append(menu_button)
            header_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            header_content.set_hexpand(True)
            header_content.append(button_box_start)
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            header_content.append(spacer)
            header_content.append(button_box_end)
            header_bar.set_title_widget(header_content)

        # Left panel (TOC / Annotations / Bookmarks)
        left_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left_panel.set_size_request(320, -1)
        left_panel.set_spacing(6)
        left_panel.set_margin_top(6)
        left_panel.set_margin_bottom(6)
        left_panel.set_margin_start(6)
        left_panel.set_margin_end(6)

        self.left_stack = Gtk.Stack()
        try:
            self.left_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        except Exception:
            pass
        self.left_stack.set_vexpand(True)

        toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toc_scrolled = Gtk.ScrolledWindow()
        self.toc_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.toc_scrolled.set_vexpand(True)
        self.toc_listbox = Gtk.ListBox()
        self.toc_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_scrolled.set_child(self.toc_listbox)
        toc_box.append(self.toc_scrolled)
        self.left_stack.add_titled(toc_box, "toc", "TOC")

        annotations_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        annotations_label = Gtk.Label(label="Annotations will appear here.")
        annotations_label.set_margin_top(12)
        annotations_box.append(annotations_label)
        self.left_stack.add_titled(annotations_box, "annotations", "Annotations")

        bookmarks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bookmarks_label = Gtk.Label(label="Bookmarks will appear here.")
        bookmarks_label.set_margin_top(12)
        bookmarks_box.append(bookmarks_label)
        self.left_stack.add_titled(bookmarks_box, "bookmarks", "Bookmarks")

        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(self.left_stack)
        stack_switcher.set_hexpand(True)
        left_panel.append(self.left_stack)
        left_panel.append(stack_switcher)

        # Main content on right
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.set_hexpand(True)
        self.main_box.set_vexpand(True)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled_window.set_vexpand(True)
        self.main_box.append(self.scrolled_window)

        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        settings = self.webview.get_settings()
        settings.set_enable_smooth_scrolling(True)
        settings.set_enable_javascript(True)
        self.webview.connect("load-changed", self.on_webview_load_changed)
        self.scrolled_window.set_child(self.webview)

        self.info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.info_bar.set_margin_top(5)
        self.info_bar.set_margin_bottom(5)
        self.info_bar.set_margin_start(10)
        self.info_bar.set_margin_end(10)

        self.chapter_label = Gtk.Label()
        self.chapter_label.set_markup("<i>No EPUB loaded</i>")
        self.chapter_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.chapter_label.set_max_width_chars(80)
        self.info_bar.append(self.chapter_label)

        self.main_box.append(self.info_bar)

        # Attach children to chosen container robustly
        if using_overlay:
            attached = False
            ov = container
            if hasattr(ov, 'set_start_child') and hasattr(ov, 'set_end_child'):
                try:
                    ov.set_start_child(left_panel)
                    ov.set_end_child(self.main_box)
                    attached = True
                except Exception:
                    attached = False
            if not attached:
                try:
                    if hasattr(ov, 'set_start') and hasattr(ov, 'set_end'):
                        ov.set_start(left_panel)
                        ov.set_end(self.main_box)
                        attached = True
                except Exception:
                    attached = False
            if not attached:
                try:
                    ov.add(left_panel)
                    ov.add(self.main_box)
                    attached = True
                except Exception:
                    attached = False
            if not attached:
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                box.append(left_panel)
                box.append(self.main_box)
                container = box
        else:
            container.append(left_panel)
            container.append(self.main_box)

        self.toolbar_view.set_content(container)
        self.set_content(self.toolbar_view)

        # Connect resize notifications
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)

        # Hook up TOC activation
        self.toc_listbox.connect("row-activated", self.on_toc_row_activated)

    def setup_navigation(self):
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.webview.add_controller(scroll_controller)

        scroll_controller2 = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller2.connect("scroll", self.on_scroll_event)
        self.scrolled_window.add_controller(scroll_controller2)

        self.snap_timeout_id = None

    def is_single_column_mode(self):
        if self.column_mode == 'fixed' and self.fixed_column_count <= 1:
            return True
        elif self.column_mode == 'width':
            width = self.get_allocated_width()
            if width <= 0:
                width = 1200
            available = max(100, width - (2 * self.column_padding))
            if self.actual_column_width >= (available - self.column_gap):
                return True
        return False

    def set_column_count(self, count):
        try:
            count = int(count)
            if count < 1:
                count = 1
        except Exception:
            count = 1
        self.column_mode = 'fixed'
        self.fixed_column_count = count
        if self.current_book:
            self.extract_chapters()
            self.load_chapter()
            GLib.timeout_add(150, self.update_navigation)

    def set_column_width(self, width):
        try:
            w = int(width)
            if w < 50:
                w = 50
        except Exception:
            w = 400
        self.column_mode = 'width'
        self.desired_column_width = w
        if self.current_book:
            self.calculate_column_dimensions()
            self.extract_chapters()
            self.load_chapter()
            GLib.timeout_add(150, self.update_navigation)

    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            if self.scrolled_window:
                self.h_adjustment = self.scrolled_window.get_hadjustment()
                if self.h_adjustment:
                    try:
                        self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
                    except Exception:
                        pass
                    self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
            GLib.timeout_add(300, self._after_load_update)

    def _after_load_update(self):
        self.calculate_column_dimensions()
        self.update_navigation()
        return False

    def calculate_column_dimensions(self):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            width = 1200
            height = 800
        available = max(100, width - (2 * self.column_padding))
        if self.column_mode == 'fixed':
            cols = max(1, int(self.fixed_column_count))
            total_gap = (cols - 1) * self.column_gap
            cw = max(50, (available - total_gap) // cols)
            self.actual_column_width = cw
        else:
            self.actual_column_width = max(50, min(self.desired_column_width, available))

    def on_scroll_position_changed(self, adjustment):
        self.update_page_info()
        self._refresh_buttons_based_on_adjustment()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book:
            return False

        self.calculate_column_dimensions()

        if self.is_single_column_mode():
            if keyval == 65365:  # Page Up
                js_code = "(function() { var doc = document.documentElement, body = document.body; var clientHeight = doc.clientHeight; var scrollTop = window.pageYOffset || doc.scrollTop; var cs = window.getComputedStyle(body); var lineHeight = parseFloat(cs.lineHeight); if(!lineHeight || isNaN(lineHeight)){ var fs = parseFloat(cs.fontSize) || 16; lineHeight = fs * 1.2; } var firstVisibleLine = Math.floor(scrollTop / lineHeight); var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight)); var targetLine = Math.max(0, firstVisibleLine - visibleLines); var targetScroll = targetLine * lineHeight; window.scrollTo({ top: Math.max(0, targetScroll), behavior: 'smooth' }); })();"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65366:  # Page Down
                js_code = "(function() { var doc = document.documentElement, body = document.body; var clientHeight = doc.clientHeight; var scrollTop = window.pageYOffset || doc.scrollTop; var cs = window.getComputedStyle(body); var lineHeight = parseFloat(cs.lineHeight); if(!lineHeight || isNaN(lineHeight)){ var fs = parseFloat(cs.fontSize) || 16; lineHeight = fs * 1.2; } var firstVisibleLine = Math.floor(scrollTop / lineHeight); var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight)); var targetLine = firstVisibleLine + visibleLines; var targetScroll = targetLine * lineHeight; var maxScroll = Math.max(0, doc.scrollHeight - clientHeight); if (targetScroll > maxScroll) targetScroll = maxScroll; window.scrollTo({ top: targetScroll, behavior: 'smooth' }); })();"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65360:  # Home
                js_code = "window.scrollTo({ top: 0, behavior: 'smooth' });"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65367:  # End
                js_code = "window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            return False

        # Multi-column navigation (fixed or width modes)
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            if keyval in (65361, 65365):
                js_code = "(function() { var columnWidth = %d; var columnGap = %d; var stepSize = columnWidth + columnGap; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var columnsPerView = Math.floor(viewportWidth / stepSize); if (columnsPerView < 1) columnsPerView = 1; var currentColumn = Math.round(currentScroll / stepSize); var targetColumn = Math.max(0, currentColumn - columnsPerView); var newScroll = targetColumn * stepSize; window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (column_width, column_gap)
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval in (65363, 65366):
                js_code = "(function() { var columnWidth = %d; var columnGap = %d; var stepSize = columnWidth + columnGap; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth); var columnsPerView = Math.floor(viewportWidth / stepSize); if (columnsPerView < 1) columnsPerView = 1; var currentColumn = Math.round(currentScroll / stepSize); var targetColumn = currentColumn + columnsPerView; var newScroll = Math.min(maxScroll, targetColumn * stepSize); window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (column_width, column_gap)
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            if keyval in (65361, 65365):
                js_code = "(function() { var desiredColumnWidth = %d; var columnGap = %d; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var availableWidth = viewportWidth - 40; var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap)); if (actualColumns < 1) actualColumns = 1; var totalGapWidth = (actualColumns - 1) * columnGap; var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns; var actualStepSize = actualColumnWidth + columnGap; var currentColumn = Math.round(currentScroll / actualStepSize); var targetColumn = Math.max(0, currentColumn - actualColumns); var newScroll = targetColumn * actualStepSize; window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (desired_width, column_gap)
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval in (65363, 65366):
                js_code = "(function() { var desiredColumnWidth = %d; var columnGap = %d; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth); var availableWidth = viewportWidth - 40; var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap)); if (actualColumns < 1) actualColumns = 1; var totalGapWidth = (actualColumns - 1) * columnGap; var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns; var actualStepSize = actualColumnWidth + columnGap; var currentColumn = Math.round(currentScroll / actualStepSize); var targetColumn = currentColumn + actualColumns; var newScroll = Math.min(maxScroll, targetColumn * actualStepSize); window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (desired_width, column_gap)
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True

        if keyval == 65360:  # Home
            js_code = "window.scrollTo({ left: 0, behavior: 'smooth' });"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
        elif keyval == 65367:  # End
            js_code = "(function() { var maxScroll = Math.max(0, document.documentElement.scrollWidth - (window.innerWidth || document.documentElement.clientWidth)); window.scrollTo({ left: maxScroll, behavior: 'smooth' }); })();"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True

        return False

    def on_scroll_event(self, controller, dx, dy):
        if not self.current_book:
            return False
        if self.is_single_column_mode():
            return False
        if self.snap_timeout_id:
            try:
                GLib.source_remove(self.snap_timeout_id)
            except Exception:
                pass
        self.snap_timeout_id = GLib.timeout_add(200, self.snap_to_nearest_step)
        return False

    def snap_to_nearest_step(self):
        if not self.current_book or self.is_single_column_mode():
            self.snap_timeout_id = None
            return False

        self.calculate_column_dimensions()

        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            js_code = "(function() { var columnWidth = %d; var columnGap = %d; var stepSize = columnWidth + columnGap; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var nearestColumn = Math.round(currentScroll / stepSize); var targetScroll = nearestColumn * stepSize; var maxScroll = Math.max(0, document.documentElement.scrollWidth - (window.innerWidth || document.documentElement.clientWidth)); targetScroll = Math.max(0, Math.min(targetScroll, maxScroll)); if (Math.abs(currentScroll - targetScroll) > 5) { window.scrollTo({ left: targetScroll, behavior: 'smooth' }); } })();" % (column_width, column_gap)
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            js_code = "(function() { var desiredColumnWidth = %d; var columnGap = %d; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var availableWidth = viewportWidth - (2 * %d); var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap)); if (actualColumns < 1) actualColumns = 1; var totalGapWidth = (actualColumns - 1) * column_gap; var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns; var actualStepSize = actualColumnWidth + columnGap; var nearestColumn = Math.round(currentScroll / actualStepSize); var targetScroll = nearestColumn * actualStepSize; var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth); targetScroll = Math.max(0, Math.min(targetScroll, maxScroll)); if (Math.abs(currentScroll - targetScroll) > 5) { window.scrollTo({ left: targetScroll, behavior: 'smooth' }); } })();" % (desired_width, column_gap, self.column_padding)
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
        self.snap_timeout_id = None
        return False

    def smooth_scroll_to(self, target_pos):
        if not self.h_adjustment:
            return False
        current_pos = self.h_adjustment.get_value()
        distance = target_pos - current_pos
        if abs(distance) < 1:
            self.h_adjustment.set_value(target_pos)
            return False
        steps = 20
        step_size = distance / steps
        step_count = 0
        def animation_frame():
            nonlocal step_count
            if step_count >= steps:
                self.h_adjustment.set_value(target_pos)
                return False
            new_pos = current_pos + (step_size * (step_count + 1))
            self.h_adjustment.set_value(new_pos)
            step_count += 1
            return True
        GLib.timeout_add(16, animation_frame)

    def on_open_clicked(self, button):
        dialog = Gtk.FileChooserNative(
            title="Open EPUB File",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel"
        )
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB Files")
        epub_filter.add_pattern("*.epub")
        dialog.add_filter(epub_filter)  # use add_filter instead of deprecated set_filter
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()  # non-deprecated single-file API
            if file:
                path = file.get_path()
                if path:
                    self.load_epub(path)
        dialog.destroy()

    def load_epub(self, filepath):
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
            self.temp_dir = tempfile.mkdtemp()
            self.current_book = epub.read_epub(filepath)
            self.extract_chapters()
            if self.chapters:
                self.current_chapter = 0
                self.load_chapter()
                self.populate_toc()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def extract_chapters(self):
        self.chapters = []
        if not self.current_book:
            return
        spine_items = [item[0] for item in self.current_book.spine]
        self.extract_resources()
        for item_id in spine_items:
            item = None
            for book_item in self.current_book.get_items():
                if getattr(book_item, "id", None) == item_id:
                    item = book_item
                    break
            if item and getattr(item, "media_type", "") == 'application/xhtml+xml':
                content = item.get_content().decode('utf-8')
                chapter_file = os.path.join(self.temp_dir, f"{item_id}.html")
                processed_content = self.process_chapter_content(content, item)
                with open(chapter_file, 'w', encoding='utf-8') as f:
                    f.write(processed_content)
                self.chapters.append({
                    'id': item_id,
                    'title': self.extract_title(content),
                    'file': chapter_file,
                    'item': item
                })
        self.populate_toc()

    def populate_toc(self):
        # Clear existing rows safely without using get_children()
        row = self.toc_listbox.get_row_at_index(0)
        while row is not None:
            self.toc_listbox.remove(row)
            row = self.toc_listbox.get_row_at_index(0)

        for idx, ch in enumerate(self.chapters):
            btn = Gtk.Button.new()
            btn.set_label(f"{idx+1}. {ch.get('title','Untitled')}")
            btn.set_halign(Gtk.Align.FILL)
            btn.get_style_context().add_class("flat")
            btn.connect("clicked", lambda b, i=idx: self.on_toc_button_clicked(i))
            row = Gtk.ListBoxRow()
            row.set_child(btn)
            self.toc_listbox.append(row)

        # ensure selection reflects current chapter
        if self.chapters and 0 <= self.current_chapter < len(self.chapters):
            try:
                sel_row = self.toc_listbox.get_row_at_index(self.current_chapter)
                if sel_row:
                    self.toc_listbox.select_row(sel_row)
            except Exception:
                pass

    def on_toc_button_clicked(self, index):
        if 0 <= index < len(self.chapters):
            self.current_chapter = index
            self.load_chapter()
            GLib.timeout_add(200, self.update_navigation)

    def on_toc_row_activated(self, listbox, row):
        idx = row.get_index()
        self.on_toc_button_clicked(idx)

    def process_chapter_content(self, content, item):
        self.calculate_column_dimensions()
        apply_columns = not self.is_single_column_mode()

        if apply_columns:
            if self.column_mode == 'fixed':
                column_css = f"column-count: {self.fixed_column_count}; column-gap: {self.column_gap}px;"
                body_style = (
                    f"margin: 0;\n"
                    f"padding: {self.column_padding}px;\n"
                    "font-family: 'Cantarell', sans-serif;\n"
                    "font-size: 16px;\n"
                    "line-height: 1.6;\n"
                    "background-color: #fafafa;\n"
                    "color: #2e3436;\n"
                    f"{column_css}\n"
                    "column-fill: balance;\n"
                    f"height: calc(100vh - {self.column_padding * 2}px);\n"
                    "overflow-x: auto;\n"
                    "overflow-y: hidden;\n"
                    "box-sizing: border-box;\n"
                )
            else:
                column_css = f"column-width: {self.actual_column_width}px; column-gap: {self.column_gap}px;"
                body_style = (
                    f"margin: 0;\n"
                    f"padding: {self.column_padding}px;\n"
                    "font-family: 'Cantarell', sans-serif;\n"
                    "font-size: 16px;\n"
                    "line-height: 1.6;\n"
                    "background-color: #fafafa;\n"
                    "color: #2e3436;\n"
                    f"{column_css}\n"
                    "column-fill: balance;\n"
                    f"height: calc(100vh - {self.column_padding * 2}px);\n"
                    "overflow-x: auto;\n"
                    "overflow-y: hidden;\n"
                    "box-sizing: border-box;\n"
                )
        else:
            body_style = (
                f"margin: 0;\n"
                f"padding: {self.column_padding}px;\n"
                "font-family: 'Cantarell', sans-serif;\n"
                "font-size: 16px;\n"
                "line-height: 1.6;\n"
                "background-color: #fafafa;\n"
                "color: #2e3436;\n"
                "column-count: 1;\n"
                "column-width: auto;\n"
                "column-gap: 0;\n"
                "height: auto;\n"
                "overflow-x: hidden;\n"
                "overflow-y: auto;\n"
                "box-sizing: border-box;\n"
            )

        css_styles = f"""<style>
html, body {{ height:100%; margin:0; padding:0; }}
body {{
{body_style}
}}
h1,h2,h3,h4,h5,h6 {{ margin-top:1.5em; margin-bottom:0.5em; font-weight:bold; break-after:auto; break-inside:auto; }}
p {{ margin:0 0 1em 0; text-align:justify; hyphens:auto; break-inside:auto; orphans:1; widows:1; }}
img {{ max-width:100%; height:auto; margin:1em 0; }}
blockquote {{ margin:1em 2em; font-style:italic; border-left:3px solid #3584e4; padding-left:1em; }}
div, section, article, span, ul, ol, li {{ break-inside:auto; }}
@media (prefers-color-scheme: dark) {{
    body {{ background-color:#242424; color:#ffffff; }}
    blockquote {{ border-left-color:#62a0ea; }}
}}
</style>
"""

        apply_columns_js = "true" if apply_columns else "false"
        fixed_count_js = str(self.fixed_column_count) if self.column_mode == 'fixed' else 'null'
        desired_width_js = str(self.actual_column_width) if self.column_mode == 'width' else 'null'
        gap_js = str(self.column_gap)

        script = """<script>
document.addEventListener('DOMContentLoaded', function() {
    window.EPUB_VIEWER_SETTINGS = {
        applyColumns: %s,
        fixedColumnCount: %s,
        desiredColumnWidth: %s,
        columnGap: %s
    };
    window.addEventListener('scroll', function() {
        var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
        var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        var scrollWidth = document.documentElement.scrollWidth;
        var scrollHeight = document.documentElement.scrollHeight;
        var clientWidth = document.documentElement.clientWidth;
        var clientHeight = document.documentElement.clientHeight;
        window.epubScrollState = {
            scrollLeft: scrollLeft,
            scrollTop: scrollTop,
            scrollWidth: scrollWidth,
            scrollHeight: scrollHeight,
            clientWidth: clientWidth,
            clientHeight: clientHeight,
            maxScrollX: Math.max(0, scrollWidth - clientWidth),
            maxScrollY: Math.max(0, scrollHeight - clientHeight)
        };
    });
    window.epubScrollState = {
        scrollLeft: 0,
        scrollTop: 0,
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight,
        clientWidth: document.documentElement.clientWidth,
        clientHeight: document.documentElement.clientHeight,
        maxScrollX: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
        maxScrollY: Math.max(0, document.documentElement.scrollHeight - document.documentElement.clientHeight)
    };
});
</script>""" % (apply_columns_js, fixed_count_js, desired_width_js, gap_js)

        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1) if body_match else content
        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)

        resources_dir_fs = os.path.join(self.temp_dir, 'resources')
        available = set()
        if os.path.isdir(resources_dir_fs):
            for fn in os.listdir(resources_dir_fs):
                available.add(fn)

        def repl_src(m):
            orig = m.group(1)
            name = os.path.basename(orig)
            if name in available:
                return f'src="resources/{name}"'
            return f'src="{orig}"'

        body_content = re.sub(r'src=["\']([^"\']+)["\']', repl_src, body_content, flags=re.IGNORECASE)

        def repl_href(m):
            orig = m.group(1)
            name = os.path.basename(orig)
            if name in available:
                return f'href="resources/{name}"'
            return f'href="{orig}"'

        body_content = re.sub(r'href=["\']([^"\']+)["\']', repl_href, body_content, flags=re.IGNORECASE)

        return f"<!DOCTYPE html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">{css_styles}</head><body>{body_content}{script}</body></html>"

    def extract_resources(self):
        if not self.current_book or not self.temp_dir:
            return
        resources_dir = os.path.join(self.temp_dir, 'resources')
        os.makedirs(resources_dir, exist_ok=True)
        for item in self.current_book.get_items():
            if hasattr(item, 'media_type'):
                if item.media_type in ['text/css', 'image/jpeg', 'image/png', 'image/gif', 'image/svg+xml']:
                    name = None
                    try:
                        name = item.get_name()
                    except Exception:
                        name = None
                    if not name:
                        name = getattr(item, 'id', None) or "resource"
                    name = os.path.basename(name)
                    resource_path = os.path.join(resources_dir, name)
                    try:
                        with open(resource_path, 'wb') as f:
                            f.write(item.get_content())
                    except Exception:
                        pass

    def extract_title(self, content):
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', content, re.IGNORECASE | re.DOTALL)
        if h1_match:
            title = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
            if title:
                return title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            if title:
                return title
        return "Untitled Chapter"

    def load_chapter(self):
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return
        chapter = self.chapters[self.current_chapter]
        file_uri = GLib.filename_to_uri(chapter['file'])
        self.webview.load_uri(file_uri)
        chapter_info = f"Chapter {self.current_chapter + 1} of {len(self.chapters)}: {chapter['title']}"
        self.chapter_label.set_text(chapter_info)
        try:
            row = self.toc_listbox.get_row_at_index(self.current_chapter)
            if row:
                self.toc_listbox.select_row(row)
        except Exception:
            pass

    def update_navigation(self):
        self.prev_chapter_btn.set_sensitive(self.current_chapter > 0)
        self.next_chapter_btn.set_sensitive(self.current_chapter < len(self.chapters) - 1)
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            try:
                self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
            except Exception:
                pass
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        if self.current_book and self.chapters:
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)
        GLib.timeout_add(100, self._delayed_navigation_update)

    def _delayed_navigation_update(self):
        self._refresh_buttons_based_on_adjustment()
        self.update_page_info()
        return False

    def _refresh_buttons_based_on_adjustment(self):
        if not self.h_adjustment or not self.current_book:
            self.prev_page_btn.set_sensitive(False)
            self.next_page_btn.set_sensitive(False)
            return
        current = self.h_adjustment.get_value()
        upper = self.h_adjustment.get_upper()
        page_size = self.h_adjustment.get_page_size()
        max_pos = max(0, upper - page_size)
        self.prev_page_btn.set_sensitive(current > 1.0)
        self.next_page_btn.set_sensitive(current < max_pos - 1.0)

    def on_prev_chapter(self, button):
        if self.current_chapter > 0:
            self.current_chapter -= 1
            self.load_chapter()
            GLib.timeout_add(300, self.update_navigation)

    def on_next_chapter(self, button):
        if self.current_chapter < len(self.chapters) - 1:
            self.current_chapter += 1
            self.load_chapter()
            GLib.timeout_add(300, self.update_navigation)

    def on_prev_page(self, button):
        if not self.current_book:
            return
        if self.is_single_column_mode():
            js_code = "(function() { var doc = document.documentElement, body = document.body; var clientHeight = doc.clientHeight; var scrollTop = window.pageYOffset || doc.scrollTop; var cs = window.getComputedStyle(body); var lineHeight = parseFloat(cs.lineHeight); if (!lineHeight || isNaN(lineHeight)) { var fs = parseFloat(cs.fontSize) || 16; lineHeight = fs * 1.2; } var firstVisibleLine = Math.floor(scrollTop / lineHeight); var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight)); var targetLine = Math.max(0, firstVisibleLine - visibleLines); var targetScroll = targetLine * lineHeight; window.scrollTo({ top: Math.max(0, targetScroll), behavior: 'smooth' }); })();"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return
        self.calculate_column_dimensions()
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            js_code = "(function() { var columnWidth = %d; var columnGap = %d; var stepSize = columnWidth + columnGap; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var columnsPerView = Math.floor(viewportWidth / stepSize); if (columnsPerView < 1) columnsPerView = 1; var currentColumn = Math.round(currentScroll / stepSize); var targetColumn = Math.max(0, currentColumn - columnsPerView); var newScroll = targetColumn * stepSize; window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (column_width, column_gap)
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            js_code = "(function() { var desiredColumnWidth = %d; var columnGap = %d; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var availableWidth = viewportWidth - (2 * %d); var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap)); if (actualColumns < 1) actualColumns = 1; var totalGapWidth = (actualColumns - 1) * columnGap; var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns; var actualStepSize = actualColumnWidth + columnGap; var currentColumn = Math.round(currentScroll / actualStepSize); var targetColumn = Math.max(0, currentColumn - actualColumns); var newScroll = targetColumn * actualStepSize; window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (desired_width, column_gap, self.column_padding)
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)

    def on_next_page(self, button):
        if not self.current_book:
            return
        if self.is_single_column_mode():
            js_code = "(function() { var doc = document.documentElement, body = document.body; var clientHeight = doc.clientHeight; var scrollTop = window.pageYOffset || doc.scrollTop; var cs = window.getComputedStyle(body); var lineHeight = parseFloat(cs.lineHeight); if (!lineHeight || isNaN(lineHeight)) { var fs = parseFloat(cs.fontSize) || 16; lineHeight = fs * 1.2; } var firstVisibleLine = Math.floor(scrollTop / lineHeight); var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight)); var targetLine = firstVisibleLine + visibleLines; var targetScroll = targetLine * lineHeight; var maxScroll = Math.max(0, doc.scrollHeight - clientHeight); if (targetScroll > maxScroll) targetScroll = maxScroll; window.scrollTo({ top: targetScroll, behavior: 'smooth' }); })();"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return
        self.calculate_column_dimensions()
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            js_code = "(function() { var columnWidth = %d; var columnGap = %d; var stepSize = columnWidth + columnGap; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth); var columnsPerView = Math.floor(viewportWidth / stepSize); if (columnsPerView < 1) columnsPerView = 1; var currentColumn = Math.round(currentScroll / stepSize); var targetColumn = currentColumn + columnsPerView; var newScroll = Math.min(maxScroll, targetColumn * stepSize); window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (column_width, column_gap)
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            js_code = "(function() { var desiredColumnWidth = %d; var columnGap = %d; var viewportWidth = window.innerWidth || document.documentElement.clientWidth; var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth); var availableWidth = viewportWidth - (2 * %d); var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap)); if (actualColumns < 1) actualColumns = 1; var totalGapWidth = (actualColumns - 1) * columnGap; var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns; var actualStepSize = actualColumnWidth + columnGap; var currentColumn = Math.round(currentScroll / actualStepSize); var targetColumn = currentColumn + actualColumns; var newScroll = Math.min(maxScroll, targetColumn * actualStepSize); window.scrollTo({ left: newScroll, behavior: 'smooth' }); setTimeout(function() { window.scrollTo({ left: newScroll, behavior: 'auto' }); }, 400); })();" % (desired_width, column_gap, self.column_padding)
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)

    def _on_js_result(self, webview, result, user_data):
        GLib.timeout_add(100, self._update_page_buttons_from_js)

    def _update_page_buttons_from_js(self):
        js_code = "(function() { return { scrollLeft: window.pageXOffset || document.documentElement.scrollLeft, scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }; })();"
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_scroll_info_result, None)
        return False

    def _on_scroll_info_result(self, webview, result, user_data):
        try:
            self._query_and_update_scroll_state()
        except Exception as e:
            print(f"Error getting scroll info: {e}")
            if self.current_book:
                self.prev_page_btn.set_sensitive(True)
                self.next_page_btn.set_sensitive(True)

    def _query_and_update_scroll_state(self):
        if self.current_book and self.chapters:
            step = max(1, int(self.actual_column_width + self.column_gap))
            js_code = "(function() { var scrollWidth = document.documentElement.scrollWidth || document.body.scrollWidth; var clientWidth = document.documentElement.clientWidth || window.innerWidth; var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft; var totalWidth = Math.max(0, scrollWidth - clientWidth); var totalPages = totalWidth > 0 ? Math.ceil((totalWidth + %d) / %d) : 1; var currentPage = totalWidth > 0 ? Math.floor(scrollLeft / %d) + 1 : 1; currentPage = Math.max(1, Math.min(currentPage, totalPages)); return currentPage + '/' + totalPages; })();" % (step, step, step)
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_page_info_result, None)

    def _on_page_info_result(self, webview, result, user_data):
        try:
            if self.current_book:
                self.page_info.set_text("Page")
            else:
                self.page_info.set_text("--/--")
        except:
            if self.current_book:
                self.page_info.set_text("Page")
            else:
                self.page_info.set_text("--/--")

    def update_page_info(self):
        if not self.current_book:
            self.page_info.set_text("--/--")
            self.prev_page_btn.set_sensitive(False)
            self.next_page_btn.set_sensitive(False)
            return
        self._query_and_update_scroll_state()

    def on_size_allocate(self, widget, allocation, baseline=None):
        if self.current_book and self.chapters:
            if hasattr(self, 'allocation_timeout_id') and self.allocation_timeout_id:
                GLib.source_remove(self.allocation_timeout_id)
            self.allocation_timeout_id = GLib.timeout_add(150, self._on_allocation_timeout)

    def _on_allocation_timeout(self):
        self.allocation_timeout_id = None
        self.calculate_column_dimensions()
        if self.current_book and self.chapters:
            self._update_column_css()
        return False

    def _update_column_css(self):
        if self.is_single_column_mode():
            js_code = "(function() { var body = document.body; if (body) { body.style.columnCount = '1'; body.style.columnWidth = 'auto'; body.style.columnGap = '0'; body.style.height = 'auto'; body.style.overflowX = 'hidden'; body.style.overflowY = 'auto'; } })();"
        else:
            if self.column_mode == 'fixed':
                column_css = f"column-count: {self.fixed_column_count}; column-gap: {self.column_gap}px;"
            else:
                column_css = f"column-width: {self.actual_column_width}px; column-gap: {self.column_gap}px;"
            esc_css = column_css.replace("'", "\\'")
            js_code = "(function() { var body = document.body; if (body) { body.style.columnCount = ''; body.style.columnWidth = ''; body.style.cssText = body.style.cssText.replace(/column-[^;]*;?/g, ''); var newStyle = '%s'; var styles = newStyle.split(';'); for (var i = 0; i < styles.length; i++) { var style = styles[i].trim(); if (style) { var parts = style.split(':'); if (parts.length === 2) { var prop = parts[0].trim(); var val = parts[1].trim(); if (prop === 'column-count') { body.style.columnCount = val; } else if (prop === 'column-width') { body.style.columnWidth = val; } else if (prop === 'column-gap') { body.style.columnGap = val; } } } } body.style.height = 'calc(100vh - %dpx)'; body.style.overflowX = 'auto'; body.style.overflowY = 'hidden'; setTimeout(function() { var currentScroll = window.pageXOffset || document.documentElement.scrollLeft; if (currentScroll > 0) { window.dispatchEvent(new Event('resize')); } }, 100); } })();" % (esc_css, self.column_padding * 2)
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)

    def on_window_resize(self, *args):
        self.calculate_column_dimensions()
        if self.current_book and self.chapters:
            if hasattr(self, 'resize_timeout_id') and self.resize_timeout_id:
                GLib.source_remove(self.resize_timeout_id)
            self.resize_timeout_id = GLib.timeout_add(250, self._delayed_resize_reload)

    def _delayed_resize_reload(self):
        self.resize_timeout_id = None
        if self.current_book:
            js_code = "(function() { return { scrollLeft: window.pageXOffset || document.documentElement.scrollLeft, scrollTop: window.pageYOffset || document.documentElement.scrollTop, scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }; })();"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_pre_resize_scroll_info, None)
        else:
            self._do_resize_reload(0, 0)
        return False

    def _on_pre_resize_scroll_info(self, webview, result, user_data):
        self._do_resize_reload(0, 0)

    def _do_resize_reload(self, preserved_scroll_x=0, preserved_scroll_y=0):
        self.calculate_column_dimensions()
        self.extract_chapters()
        self.load_chapter()
        if preserved_scroll_x > 0 or preserved_scroll_y > 0:
            GLib.timeout_add(500, lambda: self._restore_scroll_position(preserved_scroll_x, preserved_scroll_y))
        GLib.timeout_add(600, self.update_navigation)

    def _restore_scroll_position(self, scroll_x, scroll_y):
        if self.is_single_column_mode():
            js_code = f"window.scrollTo({{ top: {scroll_y}, behavior: 'auto' }});"
        else:
            js_code = f"window.scrollTo({{ left: {scroll_x}, behavior: 'auto' }});"
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
        return False

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "_OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.present()

    def cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.epubviewer")

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            window = EpubViewer(self)
        for i in range(1, 11):
            act = Gio.SimpleAction.new(f"set-columns", GLib.VariantType.new("i"))
            act.connect("activate", self.on_set_columns)
            self.add_action(act)
        for w in (50,100,150,200,300,350,400,450,500):
            act_w = Gio.SimpleAction.new(f"set-column-width", GLib.VariantType.new("i"))
            act_w.connect("activate", self.on_set_column_width)
            self.add_action(act_w)
        window.present()

    def on_set_columns(self, action, parameter):
        count = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_count(count)

    def on_set_column_width(self, action, parameter):
        w = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_width(w)

def main():
    app = EpubViewerApp()
    def cleanup_handler(signum, frame):
        window = app.get_active_window()
        if window:
            window.cleanup()
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    try:
        app.run(sys.argv)
    finally:
        window = app.get_active_window()
        if window:
            window.cleanup()

if __name__ == "__main__":
    main()

