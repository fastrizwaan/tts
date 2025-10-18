#!/usr/bin/env python3
import os, json, tempfile, shutil, re, urllib.parse, signal, sys, math, threading, queue, subprocess, uuid, time, pathlib, hashlib, multiprocessing
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, Gst

from ebooklib import epub
import soundfile as sf
try:
    from kokoro_onnx import Kokoro
except Exception:
    Kokoro = None

Adw.init()

# --- Utilities ---
_s_re_split = re.compile(r'(?<=[.!?])\s+|\n+')
def split_sentences(text):
    return [p.strip() for p in _s_re_split.split(text) if p and p.strip()]

def stable_id_for_text(text):
    """Short stable id for a sentence (sha1 hex truncated)."""
    h = hashlib.sha1(text.encode('utf-8')).hexdigest()
    return h[:12]

# This helper runs inside a subprocess to synthesize a single sentence via Kokoro.
# It is top-level so it can be pickled by multiprocessing.
def synth_single_process(model_path, voices_path, text, outpath, voice, speed, lang):
    try:
        from kokoro_onnx import Kokoro
    except Exception as e:
        print("synth_single_process: Kokoro import failed:", e, file=sys.stderr)
        return 2
    try:
        print(f"[TTS] Synthesizing: {repr(text)}")

        kokoro = Kokoro(model_path, voices_path)
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
        sf.write(outpath, samples, sample_rate)

        duration = len(samples) / float(sample_rate) if samples is not None else 0
        print(f"[TTS] Synthesized -> {outpath} (dur={duration:.2f}s)")

        return 0
    except Exception as e:
        print("synth_single_process error:", e, file=sys.stderr)
        return 3

class EpubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)

        # epub
        self.current_book = None
        self.chapters = []
        self.current_chapter = 0
        self.temp_dir = None

        # column settings
        self.column_mode = 'width'
        self.fixed_column_count = 2
        self.desired_column_width = 400
        self.column_gap = 40
        self.column_padding = 20
        self.actual_column_width = self.desired_column_width

        # resize debouncing
        self.resize_timeout_id = None
        self.allocation_timeout_id = None
        self.content_resize_timeout_id = None
        self.sidebar_toggle_timeout_id = None

        # tts manager
        self.tts = None

        # setup UI
        self.setup_ui()
        self.setup_navigation()

    def _webview_horizontal_margins(self):
        try:
            if self.webview:
                return int(self.webview.get_margin_start() or 0) + int(self.webview.get_margin_end() or 0)
        except Exception:
            pass
        return 0

    def setup_ui(self):
        # Create the main split view
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_show_sidebar(False)
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_max_sidebar_width(300)
        self.split_view.set_min_sidebar_width(250)
        
        # Sidebar content
        self.setup_sidebar()
        
        # Main content area with toolbar
        self.toolbar_view = Adw.ToolbarView()
        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)
        
        # Toggle sidebar button
        self.sidebar_toggle = Gtk.ToggleButton()
        self.sidebar_toggle.set_icon_name("view-sidebar-start-symbolic")
        self.sidebar_toggle.set_tooltip_text("Toggle Table of Contents")
        self.sidebar_toggle.add_css_class("flat")
        self.sidebar_toggle.connect("toggled", self.on_sidebar_toggle)
        
        # Open button
        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.add_css_class("flat")
        open_button.connect("clicked", self.on_open_clicked)

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

        # Navigation box
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav_box.set_spacing(6)

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

        # TTS controls
        self.tts_play_btn = Gtk.Button()
        self.tts_play_btn.set_icon_name("media-playback-start-symbolic")
        self.tts_play_btn.set_tooltip_text("Play TTS")
        self.tts_play_btn.add_css_class("flat")
        self.tts_play_btn.connect("clicked", self.on_tts_play)
        self.tts_play_btn.set_sensitive(False)

        self.tts_pause_btn = Gtk.Button()
        self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        self.tts_pause_btn.set_tooltip_text("Pause/Resume TTS")
        self.tts_pause_btn.add_css_class("flat")
        self.tts_pause_btn.connect("clicked", self.on_tts_pause)
        self.tts_pause_btn.set_sensitive(False)

        self.tts_stop_btn = Gtk.Button()
        self.tts_stop_btn.set_icon_name("media-playback-stop-symbolic")
        self.tts_stop_btn.set_tooltip_text("Stop TTS")
        self.tts_stop_btn.add_css_class("flat")
        self.tts_stop_btn.connect("clicked", self.on_tts_stop)
        self.tts_stop_btn.set_sensitive(False)

        tts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tts_box.set_spacing(4)
        tts_box.append(self.tts_play_btn)
        tts_box.append(self.tts_pause_btn)
        tts_box.append(self.tts_stop_btn)
        nav_box.append(tts_box)

        # Pack header bar
        try:
            header_bar.pack_start(self.sidebar_toggle)
            header_bar.pack_start(open_button)
            header_bar.pack_start(nav_box)
            header_bar.pack_end(menu_button)
        except AttributeError:
            # fallback for older libadwaita
            button_box_start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_start.set_spacing(6)
            button_box_start.append(self.sidebar_toggle)
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

        # Main content box
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.main_box)

        # Scrolled window for webview
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
       
        # Info bar at bottom
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

        # Set split view content
        self.split_view.set_content(self.toolbar_view)
        self.set_content(self.split_view)

        # resize notifications
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)
        
        # Monitor content area size changes
        self.toolbar_view.connect("notify::allocated-width", self.on_content_size_changed)
        self.toolbar_view.connect("notify::allocated-height", self.on_content_size_changed)

        # Add periodic TTS button state update
        GLib.timeout_add(500, self._update_tts_button_states)

    def setup_sidebar(self):
        """Setup the table of contents sidebar"""
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.set_spacing(0)
        
        # Sidebar header
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_start_title_buttons(False)
        sidebar_header.set_show_end_title_buttons(False)
        
        sidebar_title = Adw.WindowTitle()
        sidebar_title.set_title("Table of Contents")
        sidebar_header.set_title_widget(sidebar_title)
        
        sidebar_box.append(sidebar_header)
        
        # Scrolled window for TOC
        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toc_scroll.set_vexpand(True)
        
        # List box for TOC items
        self.toc_listbox = Gtk.ListBox()
        self.toc_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_listbox.add_css_class("navigation-sidebar")
        self.toc_listbox.connect("row-activated", self.on_toc_row_activated)
        
        toc_scroll.set_child(self.toc_listbox)
        sidebar_box.append(toc_scroll)
        
        self.split_view.set_sidebar(sidebar_box)
        
        # Connect to split view property changes to handle sidebar visibility
        self.split_view.connect("notify::show-sidebar", self.on_sidebar_visibility_changed)
    
    def on_sidebar_toggle(self, button):
        """Toggle sidebar visibility"""
        self.split_view.set_show_sidebar(button.get_active())
    
    def on_sidebar_visibility_changed(self, split_view, pspec):
        """Handle sidebar visibility changes to recalculate columns"""
        if self.current_book and self.chapters:
            # Debounce the recalculation
            if hasattr(self, 'sidebar_toggle_timeout_id') and self.sidebar_toggle_timeout_id:
                GLib.source_remove(self.sidebar_toggle_timeout_id)
            self.sidebar_toggle_timeout_id = GLib.timeout_add(250, self._on_sidebar_toggle_complete)
    
    def _on_sidebar_toggle_complete(self):
        """Recalculate and update columns after sidebar toggle"""
        self.sidebar_toggle_timeout_id = None
        if self.current_book and self.chapters:
            # Just recalculate dimensions and update CSS - let browser handle reflow naturally
            self.calculate_column_dimensions()
            self._update_column_css()
            GLib.timeout_add(100, self._finalize_sidebar_toggle)
        return False

    def _finalize_sidebar_toggle(self):
        """Final updates after sidebar toggle"""
        self.update_navigation()
        if self.tts:
            try:
                self.tts.reapply_highlight_after_reload()
            except Exception:
                pass
        return False
            
    def _on_sidebar_scroll_info(self, webview, result, user_data):
        """Handle scroll info and update layout by regenerating chapter"""
        # Force regeneration with new dimensions
        self.calculate_column_dimensions()
        self.extract_chapters()
        self.load_chapter()
        
        # Update navigation
        GLib.timeout_add(300, self.update_navigation)
    
    def populate_toc(self):
        """Populate the table of contents from chapters"""
        # Clear existing items
        while True:
            row = self.toc_listbox.get_row_at_index(0)
            if row is None:
                break
            self.toc_listbox.remove(row)
        
        # Add chapter items
        for idx, chapter in enumerate(self.chapters):
            row = Gtk.ListBoxRow()
            
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            box.set_spacing(8)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            
            # Chapter number
            num_label = Gtk.Label()
            num_label.set_text(f"{idx + 1}")
            num_label.add_css_class("dim-label")
            num_label.set_width_chars(3)
            box.append(num_label)
            
            # Chapter title
            title_label = Gtk.Label()
            title_label.set_text(chapter['title'])
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_xalign(0)
            title_label.set_hexpand(True)
            box.append(title_label)
            
            row.set_child(box)
            row.chapter_index = idx  # Store chapter index
            self.toc_listbox.append(row)
        
        # Select current chapter
        if self.chapters:
            row = self.toc_listbox.get_row_at_index(self.current_chapter)
            if row:
                self.toc_listbox.select_row(row)
    
    def on_toc_row_activated(self, listbox, row):
        """Handle TOC item click"""
        if hasattr(row, 'chapter_index'):
            self.current_chapter = row.chapter_index
            self.load_chapter()
            GLib.timeout_add(300, self.update_navigation)
            
            # Close sidebar on mobile/narrow screens
            if self.split_view.get_collapsed():
                self.split_view.set_show_sidebar(False)
                self.sidebar_toggle.set_active(False)

    def _update_tts_button_states(self):
        """Periodically update TTS button states"""
        has_book = self.current_book is not None
        
        if self.tts and self.tts.is_playing():
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
        elif self.tts and self.tts.is_paused():
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
        else:
            self.tts_play_btn.set_sensitive(has_book)
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
        
        return True

    def setup_navigation(self):
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.webview.add_controller(scroll_controller)
        scroll_controller2 = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
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
            available = max(100, width - (2 * self.column_padding) - self._webview_horizontal_margins())
            if self.actual_column_width >= (available - self.column_gap):
                return True
        return False

    def set_column_count(self, count):
        try:
            count = int(count)
            if count < 1: count = 1
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
            if w < 50: w = 50
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
        # init tts manager now that temp_dir exists
        try:
            if self.temp_dir and self.tts is None:
                kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                self.tts = TTSManager(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
        except Exception as e:
            print("TTS init error:", e)
        # reapply highlight after reload
        try:
            if self.tts:
                self.tts.reapply_highlight_after_reload()
        except Exception:
            pass
        return False

    def calculate_column_dimensions(self):
        # Get the actual content area width (accounting for sidebar)
        content_width = self.toolbar_view.get_allocated_width()
        height = self.toolbar_view.get_allocated_height()
        
        # If split view is showing sidebar and not collapsed, subtract sidebar width
        if self.split_view.get_show_sidebar() and not self.split_view.get_collapsed():
            # Get sidebar width (min/max/current)
            sidebar_width = self.split_view.get_sidebar_width_fraction() * self.get_allocated_width()
            if sidebar_width <= 0:
                # Fallback to min sidebar width if fraction is 0
                sidebar_width = self.split_view.get_min_sidebar_width()
            content_width = max(100, self.get_allocated_width() - sidebar_width)
        
        if content_width <= 0 or height <= 0:
            content_width = 800
            height = 800
            
        available = max(100, content_width - (2 * self.column_padding) - self._webview_horizontal_margins())
        
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
                js_code = """
                (function() {
                    var doc = document.documentElement, body = document.body;
                    var clientHeight = doc.clientHeight;
                    var scrollTop = window.pageYOffset || doc.scrollTop;
                    var cs = window.getComputedStyle(body);
                    var lineHeight = parseFloat(cs.lineHeight);
                    if (!lineHeight || isNaN(lineHeight)) {
                        var fs = parseFloat(cs.fontSize) || 16;
                        lineHeight = fs * 1.2;
                    }
                    var firstVisibleLine = Math.floor(scrollTop / lineHeight);
                    var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight));
                    var targetLine = Math.max(0, firstVisibleLine - visibleLines);
                    var targetScroll = targetLine * lineHeight;
                    window.scrollTo({ top: Math.max(0, targetScroll), behavior: 'smooth' });
                })();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65366:  # Page Down
                js_code = """
                (function() {
                    var doc = document.documentElement, body = document.body;
                    var clientHeight = doc.clientHeight;
                    var scrollTop = window.pageYOffset || doc.scrollTop;
                    var cs = window.getComputedStyle(body);
                    var lineHeight = parseFloat(cs.lineHeight);
                    if (!lineHeight || isNaN(lineHeight)) {
                        var fs = parseFloat(cs.fontSize) || 16;
                        lineHeight = fs * 1.2;
                    }
                    var firstVisibleLine = Math.floor(scrollTop / lineHeight);
                    var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight));
                    var targetLine = firstVisibleLine + visibleLines;
                    var targetScroll = targetLine * lineHeight;
                    var maxScroll = Math.max(0, doc.scrollHeight - clientHeight);
                    if (targetScroll > maxScroll) targetScroll = maxScroll;
                    window.scrollTo({ top: targetScroll, behavior: 'smooth' });
                })();
                """
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

        # Multi-column navigation
        margin_total = self._webview_horizontal_margins()
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            if keyval in (65361, 65365):  # Left / PageUp
                js_code = f"""
                (function() {{
                    var columnWidth = {column_width};
                    var columnGap = {column_gap};
                    var stepSize = columnWidth + columnGap;
                    var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                    var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                    var columnsPerView = Math.floor(viewportWidth / stepSize);
                    if (columnsPerView < 1) columnsPerView = 1;
                    var currentColumn = Math.round(currentScroll / stepSize);
                    var targetColumn = Math.max(0, currentColumn - columnsPerView);
                    var newScroll = targetColumn * stepSize;
                    window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
                }})();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval in (65363, 65366):  # Right / PageDown
                js_code = f"""
                (function() {{
                    var columnWidth = {column_width};
                    var columnGap = {column_gap};
                    var stepSize = columnWidth + columnGap;
                    var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                    var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                    var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                    var columnsPerView = Math.floor(viewportWidth / stepSize);
                    if (columnsPerView < 1) columnsPerView = 1;
                    var currentColumn = Math.round(currentScroll / stepSize);
                    var targetColumn = currentColumn + columnsPerView;
                    var newScroll = Math.min(maxScroll, targetColumn * stepSize);
                    window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
                }})();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            if keyval in (65361, 65365):  # Left / PageUp
                js_code = f"""
                (function() {{
                    var desiredColumnWidth = {desired_width};
                    var columnGap = {column_gap};
                    var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                    var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                    var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                    var availableWidth = viewportWidth - (2 * {self.column_padding} + {margin_total});
                    var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                    if (actualColumns < 1) actualColumns = 1;
                    var totalGapWidth = (actualColumns - 1) * columnGap;
                    var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                    var actualStepSize = actualColumnWidth + columnGap;
                    var currentColumn = Math.round(currentScroll / actualStepSize);
                    var targetColumn = currentColumn + actualColumns;
                    var newScroll = Math.min(maxScroll, targetColumn * actualStepSize);
                    window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
                }})();
                """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True

        if keyval == 65360:
            js_code = "window.scrollTo({ left: 0, behavior: 'smooth' });"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
        elif keyval == 65367:
            js_code = """
            (function() {
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - (window.innerWidth || document.documentElement.clientWidth));
                window.scrollTo({ left: maxScroll, behavior: 'smooth' });
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
        return False

    def on_scroll_event(self, controller, dx, dy):
        if not self.current_book:
            return False
        if self.is_single_column_mode():
            return False
        # direction detection
        if abs(dx) > 0.1 or abs(dy) > 0.1:
            scroll_left = dx > 0.1 or dy < -0.1
            scroll_right = dx < -0.1 or dy > 0.1
            margin_total = self._webview_horizontal_margins()
            if scroll_left:
                if self.column_mode == 'fixed':
                    column_width = int(self.actual_column_width)
                    column_gap = int(self.column_gap)
                    js_code = f"""
                    (function() {{
                        var columnWidth = {column_width};
                        var columnGap = {column_gap};
                        var stepSize = columnWidth + columnGap;
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                        var columnsPerView = Math.floor(viewportWidth / stepSize);
                        if (columnsPerView < 1) columnsPerView = 1;
                        var currentColumn = Math.round(currentScroll / stepSize);
                        var targetColumn = Math.max(0, currentColumn - 1);
                        var newScroll = targetColumn * stepSize;
                        window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    }})();
                    """
                else:
                    desired_width = int(self.desired_column_width)
                    column_gap = int(self.column_gap)
                    js_code = f"""
                    (function() {{
                        var desiredColumnWidth = {desired_width};
                        var columnGap = {column_gap};
                        var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        var availableWidth = viewportWidth - (2 * {self.column_padding} + {margin_total});
                        var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                        if (actualColumns < 1) actualColumns = 1;
                        var totalGapWidth = (actualColumns - 1) * columnGap;
                        var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                        var actualStepSize = actualColumnWidth + columnGap;
                        var currentColumn = Math.round(currentScroll / actualStepSize);
                        var targetColumn = Math.max(0, currentColumn - 1);
                        var newScroll = targetColumn * actualStepSize;
                        window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    }})();
                    """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            elif scroll_right:
                if self.column_mode == 'fixed':
                    column_width = int(self.actual_column_width)
                    column_gap = int(self.column_gap)
                    js_code = f"""
                    (function() {{
                        var columnWidth = {column_width};
                        var columnGap = {column_gap};
                        var stepSize = columnWidth + columnGap;
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                        var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                        var columnsPerView = Math.floor(viewportWidth / stepSize);
                        if (columnsPerView < 1) columnsPerView = 1;
                        var currentColumn = Math.round(currentScroll / stepSize);
                        var targetColumn = currentColumn + 1;
                        var newScroll = Math.min(maxScroll, targetColumn * stepSize);
                        window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    }})();
                    """
                else:
                    desired_width = int(self.desired_column_width)
                    column_gap = int(self.column_gap)
                    js_code = f"""
                    (function() {{
                        var desiredColumnWidth = {desired_width};
                        var columnGap = {column_gap};
                        var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                        var availableWidth = viewportWidth - (2 * {self.column_padding} + {margin_total});
                        var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                        if (actualColumns < 1) actualColumns = 1;
                        var totalGapWidth = (actualColumns - 1) * columnGap;
                        var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                        var actualStepSize = actualColumnWidth + columnGap;
                        var currentColumn = Math.round(currentScroll / actualStepSize);
                        var targetColumn = currentColumn + 1;
                        var newScroll = Math.min(maxScroll, targetColumn * actualStepSize);
                        window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                    }})();
                    """
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            if self.snap_timeout_id:
                try:
                    GLib.source_remove(self.snap_timeout_id)
                    self.snap_timeout_id = None
                except Exception:
                    pass
            return True
        if self.snap_timeout_id:
            try: GLib.source_remove(self.snap_timeout_id)
            except Exception: pass
        self.snap_timeout_id = GLib.timeout_add(200, self.snap_to_nearest_step)
        return False

    def snap_to_nearest_step(self):
        if not self.current_book or self.is_single_column_mode():
            self.snap_timeout_id = None
            return False
        self.calculate_column_dimensions()
        margin_total = self._webview_horizontal_margins()
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var nearestColumn = Math.round(currentScroll / stepSize);
                var targetScroll = nearestColumn * stepSize;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - (window.innerWidth || document.documentElement.clientWidth));
                targetScroll = Math.max(0, Math.min(targetScroll, maxScroll));
                if (Math.abs(currentScroll - targetScroll) > 5) {{
                    window.scrollTo({{ left: targetScroll, behavior: 'smooth' }});
                }}
            }})();
            """
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            js_code = f"""
            (function() {{
                var desiredColumnWidth = {desired_width};
                var columnGap = {column_gap};
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var availableWidth = viewportWidth - (2 * {self.column_padding} + {margin_total});
                var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                if (actualColumns < 1) actualColumns = 1;
                var totalGapWidth = (actualColumns - 1) * columnGap;
                var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                var actualStepSize = actualColumnWidth + columnGap;
                var nearestColumn = Math.round(currentScroll / actualStepSize);
                var targetScroll = nearestColumn * actualStepSize;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                targetScroll = Math.max(0, Math.min(targetScroll, maxScroll));
                if (Math.abs(currentScroll - targetScroll) > 5) {{
                    window.scrollTo({{ left: targetScroll, behavior: 'smooth' }});
                }}
            }})();
            """
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
        dialog.set_filter(epub_filter)
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = dialog.get_files()
            file = files.get_item(0) if files is not None else None
            if file:
                path = file.get_path()
                if path:
                    self.load_epub(path)
        dialog.destroy()

    def load_epub(self, filepath):
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
            # Use Flatpak app cache directory
            app_cache_dir = os.path.expanduser("~/.var/app/io.github.fastrizwaan.tts/cache")
            epub_cache_dir = os.path.join(app_cache_dir, "epub-temp")
            os.makedirs(epub_cache_dir, exist_ok=True)
            self.temp_dir = tempfile.mkdtemp(dir=epub_cache_dir)
            
            # Set environment variables to redirect TTS library temp usage
            tts_temp_dir = os.path.join(self.temp_dir, "tts-lib-temp")
            os.makedirs(tts_temp_dir, exist_ok=True)
            os.environ['TMPDIR'] = tts_temp_dir
            os.environ['TMP'] = tts_temp_dir
            os.environ['TEMP'] = tts_temp_dir
            
            self.current_book = epub.read_epub(filepath)
            self.extract_chapters()
            if self.chapters:
                self.current_chapter = 0
                self.load_chapter()
                self.populate_toc()
                
                # Enable sidebar toggle
                self.sidebar_toggle.set_sensitive(True)
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def extract_chapters(self):
        self.chapters = []
        if not self.current_book:
            return

        spine_items = []
        try:
            raw_spine = getattr(self.current_book, "spine", []) or []
            for entry in raw_spine:
                if isinstance(entry, (list, tuple)) and len(entry) > 0:
                    spine_items.append(entry[0])
                elif isinstance(entry, str):
                    spine_items.append(entry)
        except Exception:
            spine_items = []

        if not spine_items:
            return

        try:
            self.extract_resources()
        except Exception:
            pass

        for item_id in spine_items:
            try:
                item = None
                for book_item in self.current_book.get_items():
                    try:
                        if getattr(book_item, "id", None) == item_id:
                            item = book_item
                            break
                    except Exception:
                        continue
                if item is None:
                    for book_item in self.current_book.get_items():
                        try:
                            name = None
                            try:
                                name = book_item.get_name()
                            except Exception:
                                name = getattr(book_item, "id", None)
                            if name and os.path.basename(str(name)) == os.path.basename(str(item_id)):
                                item = book_item
                                break
                        except Exception:
                            continue

                if not item:
                    continue

                media_type = getattr(item, "media_type", "") or ""
                if media_type.lower() not in ('application/xhtml+xml', 'application/xml', 'text/html', 'text/xhtml'):
                    continue

                try:
                    raw = item.get_content()
                    if isinstance(raw, bytes):
                        content = raw.decode('utf-8', errors='replace')
                    else:
                        content = str(raw)
                except Exception:
                    continue

                chapter_file = os.path.join(self.temp_dir, f"{item_id}.html") if self.temp_dir else None
                processed_content = self.process_chapter_content(content, item)
                if chapter_file:
                    try:
                        with open(chapter_file, 'w', encoding='utf-8') as f:
                            f.write(processed_content)
                    except Exception:
                        chapter_file = None

                title = self.extract_title(content)
                self.chapters.append({
                    'id': item_id,
                    'title': title or "Untitled Chapter",
                    'file': chapter_file or '',
                    'item': item
                })
            except Exception:
                continue

    def process_chapter_content(self, content, item):
        """
        Inject sentence spans for many block-level tags while preserving inline HTML
        """
        self.calculate_column_dimensions()
        apply_columns = not self.is_single_column_mode()

        if apply_columns:
            if self.column_mode == 'fixed':
                column_css = f"column-count: {self.fixed_column_count}; column-gap: {self.column_gap}px;"
                body_style = f"""
                margin: 0;
                padding: {self.column_padding}px;
                font-family: 'Cantarell', sans-serif;
                font-size: 16px;
                line-height: 1.6;
                background-color: #fafafa;
                color: #2e3436;
                {column_css}
                column-fill: balance;
                height: calc(100vh - {self.column_padding * 2}px);
                overflow-x: auto;
                overflow-y: hidden;
                box-sizing: border-box;
                """
            else:
                column_css = f"column-width: {self.actual_column_width}px; column-gap: {self.column_gap}px;"
                body_style = f"""
                margin: 0;
                padding: {self.column_padding}px;
                font-family: 'Cantarell', sans-serif;
                font-size: 16px;
                line-height: 1.6;
                background-color: #fafafa;
                color: #2e3436;
                {column_css}
                column-fill: balance;
                height: calc(100vh - {self.column_padding * 2}px);
                overflow-x: auto;
                overflow-y: hidden;
                box-sizing: border-box;
                """
        else:
            body_style = f"""
            margin: 0;
            padding: {self.column_padding}px;
            font-family: 'Cantarell', sans-serif;
            font-size: 16px;
            line-height: 1.6;
            background-color: #fafafa;
            color: #2e3436;
            column-count: 1;
            column-width: auto;
            column-gap: 0;
            height: auto;
            overflow-x: hidden;
            overflow-y: auto;
            box-sizing: border-box;
            """

        css_styles = f"""
        <style>
        html, body {{ height:100%; margin:0; padding:0; }}
        body {{
            {body_style}
        }}
        .tts-highlight {{background:rgba(255, 215, 0, 0.35);box-shadow:0 0 0 2px rgba(255, 215, 0, 0.35)}}
        h1,h2,h3,h4,h5,h6 {{ margin-top:1.5em; margin-bottom:0.5em; font-weight:bold; break-after:auto; break-inside:auto; }}
        p {{ margin:0 0 1em 0; text-align:justify; hyphens:auto; break-inside:auto; orphans:1; widows:1; }}

        img, figure, figcaption {{
            display:block;
            max-width:100%;
            height:auto;
            margin:1em auto;
            break-inside: avoid-column;
            -webkit-column-break-inside: avoid;
            page-break-inside: avoid;
        }}

        body > img:first-of-type,
        body > figure:first-of-type img {{
            column-span: all;
            width: 100%;
            max-width: none;
            margin: 2em auto;
        }}

        blockquote {{ margin:1em 2em; font-style:italic; border-left:3px solid #3584e4; padding-left:1em; }}
        div, section, article, span, ul, ol, li {{ break-inside:auto; }}

        @media (prefers-color-scheme: dark) {{
            body {{ background-color:#242424; color:#e3e3e3; }}
            blockquote {{ border-left-color:#62a0ea; }}
            .tts-highlight {{background:rgba(0,127,0,0.75);box-shadow:0 0 0 2px rgba(0,127,0,0.75)}}
        }}
        </style>
        """

        script = f"""
        <script>
        document.addEventListener('DOMContentLoaded', function() {{
            window.EPUB_VIEWER_SETTINGS = {{
                applyColumns: {( 'true' if apply_columns else 'false')},
                fixedColumnCount: {self.fixed_column_count if self.column_mode=='fixed' else 'null'},
                desiredColumnWidth: {self.actual_column_width if self.column_mode=='width' else 'null'},
                columnGap: {self.column_gap}
            }};
            window.epubScrollState = {{
                scrollLeft: 0,
                scrollTop: 0,
                scrollWidth: document.documentElement.scrollWidth,
                scrollHeight: document.documentElement.scrollHeight,
                clientWidth: document.documentElement.clientWidth,
                clientHeight: document.documentElement.clientHeight,
                maxScrollX: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
                maxScrollY: Math.max(0, document.documentElement.scrollHeight - document.documentElement.clientHeight)
            }};
        }});
        </script>
        """

        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1) if body_match else content

        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)

        try:
            body_content = re.sub(
                r'(?<=^|>)(\s*[^<\s][^<]*?)(?=<|$)',
                lambda m: '<p>' + m.group(1).strip() + '</p>',
                body_content,
                flags=re.DOTALL
            )
        except Exception:
            pass

        resources_dir_fs = os.path.join(self.temp_dir, 'resources')
        available = set(os.listdir(resources_dir_fs)) if os.path.isdir(resources_dir_fs) else set()
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

        TARGET_TAGS = [
            'p','div','span','section','article','li','label',
            'blockquote','figcaption','caption','dt','dd',
            'td','th','summary','pre',
            'h1','h2','h3','h4','h5','h6'
        ]

        def make_replacer(tag):
            pattern = re.compile(rf'<{tag}([^>]*)>(.*?)</{tag}>', flags=re.DOTALL | re.IGNORECASE)

            def find_html_span_for_plain_range(html, plain_start, plain_len):
                p = 0
                html_start = None
                html_end = None
                i = 0
                L = len(html)
                while i < L and p <= plain_start + plain_len:
                    if html[i] == '<':
                        j = html.find('>', i)
                        if j == -1:
                            break
                        i = j + 1
                        continue
                    if p == plain_start and html_start is None:
                        html_start = i
                    p += 1
                    i += 1
                    if p == plain_start + plain_len:
                        html_end = i
                        break
                return (html_start, html_end)

            def repl(m):
                attrs = m.group(1) or ''
                inner = m.group(2) or ''

                plain = re.sub(r'<[^>]+>', '', inner)
                plain = plain.replace('\r', ' ').replace('\n', ' ')
                sents = split_sentences(plain)
                if not sents:
                    return m.group(0)

                out_html = inner
                offset = 0
                cur_plain_pos = 0

                for s in sents:
                    s_clean = s.strip()
                    if not s_clean:
                        continue
                    plen = len(s_clean)
                    next_pos = plain.find(s_clean, cur_plain_pos)
                    if next_pos == -1:
                        next_pos = plain.find(s_clean)
                        if next_pos == -1:
                            cur_plain_pos += plen
                            continue

                    span = find_html_span_for_plain_range(inner, next_pos, plen)
                    if not span or span[0] is None or span[1] is None:
                        sid = stable_id_for_text(s_clean)
                        esc = (s_clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                        span_html = f'<span data-tts-id="{sid}">{esc}</span>'
                        out_html = out_html.replace(s_clean, span_html, 1)
                        cur_plain_pos = next_pos + plen
                        offset += len(span_html) - plen
                        continue

                    hstart, hend = span
                    sid = stable_id_for_text(s_clean)
                    exact_fragment = inner[hstart:hend]
                    span_html = f'<span data-tts-id="{sid}">{exact_fragment}</span>'

                    out_pos = hstart + offset
                    out_html = out_html[:out_pos] + span_html + out_html[out_pos + (hend - hstart):]
                    offset += len(span_html) - (hend - hstart)
                    cur_plain_pos = next_pos + plen

                return f'<{tag}{attrs}>' + out_html + f'</{tag}>'

            return pattern, repl

        for tag in TARGET_TAGS:
            pat, repl = make_replacer(tag)
            body_content = pat.sub(repl, body_content)

        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{css_styles}</head><body>{body_content}{script}</body></html>"""

    def extract_resources(self):
        if not self.current_book or not self.temp_dir:
            return
        resources_dir = os.path.join(self.temp_dir, 'resources')
        os.makedirs(resources_dir, exist_ok=True)
        for item in self.current_book.get_items():
            if hasattr(item, 'media_type'):
                if item.media_type in ['text/css','image/jpeg','image/png','image/gif','image/svg+xml']:
                    name = None
                    try: name = item.get_name()
                    except Exception: name = None
                    if not name: name = getattr(item, 'id', None) or "resource"
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
            if title: return title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            if title: return title
        return "Untitled Chapter"

    def load_chapter(self):
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return
        chapter = self.chapters[self.current_chapter]
        file_uri = GLib.filename_to_uri(chapter['file'])
        self.webview.load_uri(file_uri)
        chapter_info = f"Chapter {self.current_chapter + 1} of {len(self.chapters)}: {chapter['title']}"
        self.chapter_label.set_text(chapter_info)
        
        # Update TOC selection
        if hasattr(self, 'toc_listbox'):
            row = self.toc_listbox.get_row_at_index(self.current_chapter)
            if row:
                self.toc_listbox.select_row(row)

        if not getattr(self, '_initial_layout_done', False):
            if not getattr(self, '_user_set_columns', False):
                self.column_mode = 'fixed'
                self.fixed_column_count = 2
            self._initial_layout_done = True
            self.calculate_column_dimensions()
            self._update_column_css()
            GLib.timeout_add(200, self.update_navigation)
        
    def update_navigation(self):
        self.prev_chapter_btn.set_sensitive(self.current_chapter > 0)
        self.next_chapter_btn.set_sensitive(self.current_chapter < len(self.chapters)-1)
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
            js_code = """
            (function() {
                var doc = document.documentElement, body = document.body;
                var clientHeight = doc.clientHeight;
                var scrollTop = window.pageYOffset || doc.scrollTop;
                var cs = window.getComputedStyle(body);
                var lineHeight = parseFloat(cs.lineHeight);
                if (!lineHeight || isNaN(lineHeight)) {
                    var fs = parseFloat(cs.fontSize) || 16;
                    lineHeight = fs * 1.2;
                }
                var firstVisibleLine = Math.floor(scrollTop / lineHeight);
                var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight));
                var targetLine = Math.max(0, firstVisibleLine - visibleLines);
                var targetScroll = targetLine * lineHeight;
                window.scrollTo({ top: Math.max(0, targetScroll), behavior: 'smooth' });
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return
        self.calculate_column_dimensions()
        margin_total = self._webview_horizontal_margins()
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var columnsPerView = Math.floor(viewportWidth / stepSize);
                if (columnsPerView < 1) columnsPerView = 1;
                var currentColumn = Math.round(currentScroll / stepSize);
                var targetColumn = Math.max(0, currentColumn - columnsPerView);
                var newScroll = targetColumn * stepSize;
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            js_code = f"""
            (function() {{
                var desiredColumnWidth = {desired_width};
                var columnGap = {column_gap};
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var availableWidth = viewportWidth - (2 * {self.column_padding} + {margin_total});
                var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                if (actualColumns < 1) actualColumns = 1;
                var totalGapWidth = (actualColumns - 1) * columnGap;
                var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                var actualStepSize = actualColumnWidth + columnGap;
                var currentColumn = Math.round(currentScroll / actualStepSize);
                var targetColumn = Math.max(0, currentColumn - actualColumns);
                var newScroll = targetColumn * actualStepSize;
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)

    def on_next_page(self, button):
        if not self.current_book:
            return
        if self.is_single_column_mode():
            js_code = """
            (function() {
                var doc = document.documentElement, body = document.body;
                var clientHeight = doc.clientHeight;
                var scrollTop = window.pageYOffset || doc.scrollTop;
                var cs = window.getComputedStyle(body);
                var lineHeight = parseFloat(cs.lineHeight);
                if (!lineHeight || isNaN(lineHeight)) {
                    var fs = parseFloat(cs.fontSize) || 16;
                    lineHeight = fs * 1.2;
                }
                var firstVisibleLine = Math.floor(scrollTop / lineHeight);
                var visibleLines = Math.max(1, Math.floor(clientHeight / lineHeight));
                var targetLine = firstVisibleLine + visibleLines;
                var targetScroll = targetLine * lineHeight;
                var maxScroll = Math.max(0, doc.scrollHeight - clientHeight);
                if (targetScroll > maxScroll) targetScroll = maxScroll;
                window.scrollTo({ top: targetScroll, behavior: 'smooth' });
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return
        self.calculate_column_dimensions()
        margin_total = self._webview_horizontal_margins()
        if self.column_mode == 'fixed':
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                var columnsPerView = Math.floor(viewportWidth / stepSize);
                if (columnsPerView < 1) columnsPerView = 1;
                var currentColumn = Math.round(currentScroll / stepSize);
                var targetColumn = currentColumn + columnsPerView;
                var newScroll = Math.min(maxScroll, targetColumn * stepSize);
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        else:
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            js_code = f"""
            (function() {{
                var desiredColumnWidth = {desired_width};
                var columnGap = {column_gap};
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                var availableWidth = viewportWidth - (2 * {self.column_padding} + {margin_total});
                var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                if (actualColumns < 1) actualColumns = 1;
                var totalGapWidth = (actualColumns - 1) * columnGap;
                var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                var actualStepSize = actualColumnWidth + columnGap;
                var currentColumn = Math.round(currentScroll / actualStepSize);
                var targetColumn = currentColumn + actualColumns;
                var newScroll = Math.min(maxScroll, targetColumn * actualStepSize);
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)

    def _on_js_result(self, webview, result, user_data):
        GLib.timeout_add(100, self._update_page_buttons_from_js)

    def _update_page_buttons_from_js(self):
        self.webview.evaluate_javascript("""
        (function() {
            return {
                scrollLeft: window.pageXOffset || document.documentElement.scrollLeft,
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth
            };
        })();
        """, -1, None, None, None, self._on_scroll_info_result, None)
        return False

    def _on_scroll_info_result(self, webview, result, user_data):
        try:
            self._query_and_update_scroll_state()
        except Exception as e:
            print("Error getting scroll info:", e)
            if self.current_book:
                self.prev_page_btn.set_sensitive(True)
                self.next_page_btn.set_sensitive(True)

    def _query_and_update_scroll_state(self):
        if self.current_book and self.chapters:
            self.webview.evaluate_javascript("""
            (function() {
                var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                var scrollWidth = document.documentElement.scrollWidth;
                var clientWidth = document.documentElement.clientWidth;
                var maxScroll = Math.max(0, scrollWidth - clientWidth);
                return {
                    scrollLeft: scrollLeft,
                    maxScroll: maxScroll,
                    canScrollLeft: scrollLeft > 1,
                    canScrollRight: scrollLeft < maxScroll - 1
                };
            })();
            """, -1, None, None, None, self._on_page_state_result, None)

    def _on_page_state_result(self, webview, result, user_data):
        if self.current_book and self.chapters:
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)
            self.calculate_column_dimensions()
            step = max(1, int(self.actual_column_width + self.column_gap))
            js_code = f"""
            (function() {{
                var scrollWidth = document.documentElement.scrollWidth || document.body.scrollWidth;
                var clientWidth = document.documentElement.clientWidth || window.innerWidth;
                var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                var totalWidth = Math.max(0, scrollWidth - clientWidth);
                var totalPages = totalWidth > 0 ? Math.ceil((totalWidth + {step}) / {step}) : 1;
                var currentPage = totalWidth > 0 ? Math.floor(scrollLeft / {step}) + 1 : 1;
                currentPage = Math.max(1, Math.min(currentPage, totalPages));
                return currentPage + '/' + totalPages;
            }})();
            """
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
        """Update column CSS dynamically without reloading the page"""
        if self.is_single_column_mode():
            js_code = """
            (function() {
                var body = document.body;
                if (body) {
                    body.style.columnCount = '1';
                    body.style.columnWidth = 'auto';
                    body.style.columnGap = '0';
                    body.style.height = 'auto';
                    body.style.overflowX = 'hidden';
                    body.style.overflowY = 'auto';
                }
            })();
            """
        else:
            if self.column_mode == 'fixed':
                js_code = f"""
                (function() {{
                    var body = document.body;
                    if (body) {{
                        body.style.columnCount = '{self.fixed_column_count}';
                        body.style.columnWidth = 'auto';
                        body.style.columnGap = '{self.column_gap}px';
                        body.style.columnFill = 'balance';
                        body.style.height = 'calc(100vh - {self.column_padding * 2}px)';
                        body.style.overflowX = 'auto';
                        body.style.overflowY = 'hidden';
                    }}
                }})();
                """
            else:
                js_code = f"""
                (function() {{
                    var body = document.body;
                    if (body) {{
                        body.style.columnCount = 'auto';
                        body.style.columnWidth = '{self.actual_column_width}px';
                        body.style.columnGap = '{self.column_gap}px';
                        body.style.columnFill = 'balance';
                        body.style.height = 'calc(100vh - {self.column_padding * 2}px)';
                        body.style.overflowX = 'auto';
                        body.style.overflowY = 'hidden';
                    }}
                }})();
                """
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_css_update_complete, None)

    def _on_css_update_complete(self, webview, result, user_data):
        """After CSS update, reapply TTS highlight if needed"""
        try:
            if self.tts:
                GLib.timeout_add(100, lambda: self.tts.reapply_highlight_after_reload() or False)
        except Exception:
            pass

    def on_window_resize(self, *args):
        self.calculate_column_dimensions()
        if self.current_book and self.chapters:
            if hasattr(self, 'resize_timeout_id') and self.resize_timeout_id:
                GLib.source_remove(self.resize_timeout_id)
            # Just reflow, don't reload
            self.resize_timeout_id = GLib.timeout_add(1, self._delayed_resize_reflow)

    
    def on_content_size_changed(self, *args):
        """Handle content area size changes (e.g., sidebar toggle)"""
        if self.current_book and self.chapters:
            if hasattr(self, 'content_resize_timeout_id') and self.content_resize_timeout_id:
                GLib.source_remove(self.content_resize_timeout_id)
            self.content_resize_timeout_id = GLib.timeout_add(150, self._on_content_resize_timeout)
    
    def _on_content_resize_timeout(self):
        """Handle debounced content resize"""
        self.content_resize_timeout_id = None
        if self.current_book and self.chapters:
            self.calculate_column_dimensions()
            self._update_column_css()
            GLib.timeout_add(100, self.update_navigation)
        return False

    def _delayed_resize_reflow(self):
        """Reflow columns without reloading content"""
        self.resize_timeout_id = None
        if self.current_book and self.chapters:
            self.calculate_column_dimensions()
            self._update_column_css()
            GLib.timeout_add(100, self.update_navigation)
        return False


    def _on_pre_resize_scroll_info(self, webview, result, user_data):
        self._do_resize_reload(0,0)

    def _do_resize_reload(self, preserved_scroll_x=0, preserved_scroll_y=0):
        self.calculate_column_dimensions()
        self.extract_chapters()
        self.load_chapter()
        if preserved_scroll_x > 0 or preserved_scroll_y > 0:
            GLib.timeout_add(500, lambda: self._restore_scroll_position(preserved_scroll_x, preserved_scroll_y))
        GLib.timeout_add(600, self.update_navigation)

    def _restore_scroll_position(self, scroll_x, scroll_y):
        if self.is_single_column_mode():
            js_code = f"""
            window.scrollTo({{ top: {scroll_y}, behavior: 'auto' }});
            """
        else:
            js_code = f"""
            window.scrollTo({{ left: {scroll_x}, behavior: 'auto' }});
            """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
        if self.tts:
            GLib.timeout_add(200, lambda: self.tts.reapply_highlight_after_reload())
        return False

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "_OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.present()

    def cleanup(self):
        if self.tts:
            try:
                self.tts.stop()
            except Exception:
                pass
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                print(f"Cleaned up temp EPUB dir: {self.temp_dir}")
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")
                try:
                    tts_dir = os.path.join(self.temp_dir, "tts")
                    if os.path.exists(tts_dir):
                        shutil.rmtree(tts_dir)
                    
                    tts_lib_temp = os.path.join(self.temp_dir, "tts-lib-temp")
                    if os.path.exists(tts_lib_temp):
                        shutil.rmtree(tts_lib_temp)
                except Exception:
                    pass

    def _collect_sentences_for_current_chapter(self):
        """
        Collect (sid, text) pairs from chapter HTML by matching data-tts-id spans.
        """
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return []
        chapter = self.chapters[self.current_chapter]
        try:
            with open(chapter['file'], 'r', encoding='utf-8') as f:
                html = f.read()
        except Exception:
            return []
        pairs = []
        for m in re.finditer(r'<span\s+[^>]*data-tts-id=["\']([^"\']+)["\'][^>]*>(.*?)</span>', html, flags=re.DOTALL|re.IGNORECASE):
            sid = m.group(1)
            inner = m.group(2)
            text = re.sub(r'<[^>]+>', '', inner).strip()
            if text:
                pairs.append((sid, text))
        return pairs

    def on_tts_play(self, button):
        if not self.current_book or not self.chapters:
            return
        sentences = self._collect_sentences_for_current_chapter()
        if not sentences:
            return
        chap_id = self.chapters[self.current_chapter]['id']
        if self.tts is None:
            kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
            voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
            self.tts = TTSManager(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
        try:
            self.tts.start(chap_id, sentences)
        except Exception as e:
            self.show_error(f"TTS start failed: {e}")

    def on_tts_pause(self, button):
        if not self.tts:
            return
        if self.tts.is_paused():
            self.tts.resume()
        else:
            self.tts.pause()

    def on_tts_stop(self, button):
        if not self.tts:
            return
        self.tts.stop()

# -----------------------
# App class & main
# -----------------------
class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")

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
        print("Received signal, cleaning up...")
        window = app.get_active_window()
        if window:
            if window.tts:
                try:
                    window.tts.stop()
                    import time
                    time.sleep(0.5)
                except Exception as e:
                    print(f"Error stopping TTS: {e}")
            try:
                window.cleanup()
            except Exception as e:
                print(f"Error in cleanup: {e}")
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    try:
        app.run(sys.argv)
    finally:
        w = app.get_active_window()
        if w:
            if w.tts:
                try:
                    w.tts.stop()
                    import time
                    time.sleep(0.5)
                except Exception:
                    pass
            w.cleanup()

if __name__ == "__main__":
    main()
