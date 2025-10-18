#!/usr/bin/env python3
# EPUB viewer with libadwaita + GTK4 ListView sidebar TOC (nested, clickable)
import gi, os, tempfile, traceback, shutil, urllib.parse, glob, re, json, hashlib
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GObject, Gdk, GdkPixbuf
import cairo
import math
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import zipfile, pathlib
import threading
import time
import tempfile as _tempfile

# Try to import TTS dependencies
TTS_AVAILABLE = False
try:
    from kokoro_onnx import Kokoro
    import soundfile as sf
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    TTS_AVAILABLE = True
    print("[info] TTS dependencies available")
except ImportError as e:
    print(f"[warn] TTS not available: {e}")
    Kokoro = None
    Gst = None
    sf = None

# - Safe NCX monkey-patch (avoid crashes on some EPUBs) -
import ebooklib.epub
def _safe_parse_ncx(self, ncxFile):
    self.book.toc = []
ebooklib.epub.EpubReader._parse_ncx = _safe_parse_ncx

APP_NAME = "EPUB Viewer"
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

# cover target size for sidebar (small)
COVER_W, COVER_H = 70, 100

# persistent library locations & library cover save size
LIBRARY_DIR = os.path.join(GLib.get_user_data_dir(), "epubviewer")
LIBRARY_FILE = os.path.join(LIBRARY_DIR, "library.json")
COVERS_DIR = os.path.join(LIBRARY_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)
# persistent cover saved size (bigger so library shows large covers)
LIB_COVER_W, LIB_COVER_H = 200, 300

def _ensure_library_dir():
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)

def load_library():
    _ensure_library_dir()
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return []
    return []

def save_library(data):
    _ensure_library_dir()
    try:
        with open(LIBRARY_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Error saving library:", e)

# CSS (short) - removed unsupported text-align properties
_css = """
.epub-sidebar .adw-action-row {
    margin: 5px;
    padding: 6px;
    border-radius: 8px;
    background-color: transparent;
}
.epub-sidebar .adw-action-row:hover {
    background-color: rgba(0,0,0,0.06);
}
.epub-sidebar .adw-action-row.selected {
    background-color: rgba(0,0,0,0.12);
}
.book-title { font-weight: 600; margin-bottom: 2px; }
.book-author { opacity: 0.8; font-size: 0.9em; }
"""

# Library CSS
_LIBRARY_CSS = b"""
.library-grid { padding: 1px; }
.library-card {
    background-color: transparent;
    border-radius: 10px;
    padding-top: 10px;
    padding-bottom: 5px;
    box-shadow: none;
    border: none;
}
.library-card .cover {
    margin-top: 0px;
    margin-bottom: 5px;
    margin-left: 10px;
    margin-right: 10px;
    border-radius: 10px;
}
.library-card .title { font-weight: 600; font-size: 12px; line-height: 1.2; color: @theme_fg_color; }
.library-card .author { font-size: 10px; opacity: 0.7; color: @theme_fg_color; }
.library-card .meta { font-size: 9px; font-weight: 500; opacity: 0.6; color: @theme_fg_color; }
.library-card.active { border: 2px solid #ffcc66; box-shadow: 0 6px 18px rgba(255,204,102,0.15); }
"""

# Hover/active theme-aware providers
_LIBRARY_HOVER_LIGHT = b"""
.library-card:hover {
    box-shadow: 0 6px 16px rgba(0,0,0,0.15);
    transform: translateY(-2px);
    background-color: rgba(255,204,102,0.06);
}
.library-card.active {
    background-color: rgba(255,204,102,0.08);
    border: 2px solid #ffcc66;
    box-shadow: 0 6px 18px rgba(255,204,102,0.15);
}
"""
_LIBRARY_HOVER_DARK = b"""
.library-card:hover {
    box-shadow: 0 6px 20px rgba(0,0,0,0.5);
    transform: translateY(-2px);
    background-color: rgba(255,204,102,0.12);
}
.library-card.active {
    background-color: rgba(255,204,102,0.14);
    border: 2px solid #ffcc66;
    box-shadow: 0 6px 18px rgba(255,204,102,0.15);
}
"""

_cssp = Gtk.CssProvider()
_cssp.load_from_data(_LIBRARY_CSS)
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _cssp,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
)

def _apply_library_hover_css():
    display = Gdk.Display.get_default()
    if display:
        cssp_hover = Gtk.CssProvider()
        if Gtk.Settings.get_default().get_property("gtk-application-prefer-dark-theme"):
            cssp_hover.load_from_data(_LIBRARY_HOVER_DARK)
        else:
            cssp_hover.load_from_data(_LIBRARY_HOVER_LIGHT)
        Gtk.StyleContext.add_provider_for_display(
            display, cssp_hover, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2
        )

_apply_library_hover_css()

def _on_theme_change(settings, _):
    _apply_library_hover_css()

Gtk.Settings.get_default().connect("notify::gtk-application-prefer-dark-theme", _on_theme_change)


# --- TTS Engine Class ---
class TTSEngine:
    def __init__(self, webview_getter, base_temp_dir, kokoro_model_path=None, voices_bin_path=None):
        self.webview_getter = webview_getter
        self.base_temp_dir = base_temp_dir
        self.kokoro = None
        self.is_playing_flag = False
        self.should_stop = False
        self.current_thread = None
        self._tts_sentences = []
        self._tts_sids = []
        self._tts_voice = None
        self._tts_speed = 1.0
        self._tts_lang = "en-us"
        self._tts_finished_callback = None
        self._tts_highlight_callback = None
        self._current_play_index = 0
        self._audio_files = {}
        self._audio_lock = threading.Lock()
        self._synthesis_done = threading.Event()
        self._delayed_timer = None
        self._delayed_timer_lock = threading.Lock()
        self.paused = False
        self._resume_event = threading.Event()
        self._resume_event.set()

        if TTS_AVAILABLE and Kokoro:
            try:
                model_path = kokoro_model_path or os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_path = voices_bin_path or os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                if os.path.exists(model_path) and os.path.exists(voices_path):
                    self.kokoro = Kokoro(model_path, voices_path)
                    print("[info] Kokoro TTS initialized")
                else:
                    print(f"[warn] Kokoro models not found at {model_path} or {voices_path}")
            except Exception as e:
                print(f"[error] Failed to initialize Kokoro: {e}")
                self.kokoro = None

        try:
            if TTS_AVAILABLE and Gst:
                Gst.init(None)
                self.player = Gst.ElementFactory.make("playbin", "player")
                bus = self.player.get_bus()
                bus.add_signal_watch()
                bus.connect("message", self.on_gst_message)
                self.playback_finished = False
            else:
                self.player = None
                self.playback_finished = True
        except Exception as e:
            print(f"[warn] GStreamer init failed: {e}")
            self.player = None
            self.playback_finished = True

    def split_sentences(self, text):
        """Simple sentence splitter."""
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    def on_gst_message(self, bus, message):
        if message.type == Gst.MessageType.EOS or message.type == Gst.MessageType.ERROR:
            self.playback_finished = True
            if self.player:
                self.player.set_state(Gst.State.NULL)

    def speak_sentences_list(self, sentences_with_meta, voice="af_sarah", speed=1.0, lang="en-us", highlight_callback=None, finished_callback=None):
        if not self.kokoro:
            print("[warn] TTS not available")
            if finished_callback:
                GLib.idle_add(finished_callback)
            return

        self.stop()
        time.sleep(0.05)
        self.should_stop = False
        self._tts_sentences = []
        self._tts_sids = []
        for s in sentences_with_meta:
            if isinstance(s, dict):
                self._tts_sids.append(s.get("sid"))
                self._tts_sentences.append(s.get("text"))
            else:
                self._tts_sids.append(None)
                self._tts_sentences.append(str(s))

        self._tts_voice = voice
        self._tts_speed = speed
        self._tts_lang = lang
        self._tts_finished_callback = finished_callback
        self._tts_highlight_callback = highlight_callback
        self._audio_files = {}
        self._current_play_index = 0
        self._synthesis_done.clear()
        self._cancel_delayed_timer()
        self.paused = False
        self._resume_event.set()
        self.is_playing_flag = True

        def tts_thread():
            try:
                total = len(self._tts_sentences)
                print(f"[TTS] Speaking {total} sentences")

                idx = 0
                while idx < total and not self.should_stop:
                    while self.paused and not self.should_stop:
                        self._resume_event.wait(0.1)
                    if self.should_stop:
                        break

                    if self._tts_highlight_callback:
                        meta = {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]}
                        try:
                            GLib.idle_add(self._tts_highlight_callback, idx, meta)
                        except Exception:
                            pass

                    audio_file = self.synthesize_sentence(self._tts_sentences[idx], self._tts_voice, self._tts_speed, self._tts_lang)
                    
                    if not audio_file:
                        print(f"[warn] No audio for {idx}, skipping")
                        self._current_play_index = idx + 1
                        idx += 1
                        continue

                    if self.paused:
                        continue

                    print(f"[TTS] Playing {idx+1}/{total}")
                    if self.player:
                        try:
                            self.player.set_property("uri", f"file://{audio_file}")
                            self.player.set_state(Gst.State.PLAYING)
                            self.playback_finished = False
                        except Exception as e:
                            print("player error:", e)
                            self.playback_finished = True
                    else:
                        self.playback_finished = True

                    time.sleep(0.05)
                    while not self.playback_finished and not self.should_stop:
                        time.sleep(0.05)
                        if self.paused:
                            break
                    
                    # Cleanup audio file
                    try:
                        if audio_file and os.path.exists(audio_file):
                            os.remove(audio_file)
                    except Exception:
                        pass
                    
                    idx += 1
                    self._current_play_index = idx

            except Exception as e:
                print(f"[error] TTS thread: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if self.player:
                    try:
                        self.player.set_state(Gst.State.NULL)
                    except Exception:
                        pass
                self.is_playing_flag = False
                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

        self.current_thread = threading.Thread(target=tts_thread, daemon=True)
        self.current_thread.start()

    def pause(self):
        if self.is_playing_flag and not self.paused:
            self.paused = True
            self._resume_event.clear()

    def resume(self):
        if self.is_playing_flag and self.paused:
            self.paused = False
            self._resume_event.set()

    def is_paused(self):
        return self.paused

    def stop(self):
        self.should_stop = True
        self.paused = False
        self._resume_event.set()
        if self.current_thread:
            try:
                self.current_thread.join(timeout=1.0)
            except Exception:
                pass
        self.is_playing_flag = False
        if self._tts_highlight_callback:
             GLib.idle_add(self._tts_highlight_callback, -1, {"sid": None, "text": ""})

    def next_sentence(self):
        pass  # Simplified for now

    def prev_sentence(self):
        pass  # Simplified for now

    def is_playing(self):
        return self.is_playing_flag

    def synthesize_sentence(self, text, voice, speed, lang):
        if not self.kokoro or not sf:
            return None
        try:
            audio_data, sr = self.kokoro.synthesize(text, voice=voice, speed=speed, lang=lang)
            audio_file_path = os.path.join(self.base_temp_dir or tempfile.gettempdir(), f"tts_{hash(text)}.wav")
            sf.write(audio_file_path, audio_data, sr)
            return audio_file_path
        except Exception as e:
            print(f"[TTS] Synthesis error: {e}")
            return None

    def _cancel_delayed_timer(self):
        with self._delayed_timer_lock:
            if self._delayed_timer:
                self._delayed_timer.cancel()
                self._delayed_timer = None


# --- TOC Data Class ---
class TocItem(GObject.Object):
    __gtype_name__ = "TocItem"

    title = GObject.Property(type=str)
    href = GObject.Property(type=str)
    index = GObject.Property(type=int, default=-1)
    level = GObject.Property(type=int, default=0)
    has_children = GObject.Property(type=bool, default=False)
    expanded = GObject.Property(type=bool, default=False)

    def __init__(self, title="", href="", index=-1, level=0):
        super().__init__()
        self.title = title
        self.href = href
        self.index = index
        self.level = level
        self.has_children = False
        self.expanded = False
        self.children = Gio.ListStore.new(TocItem)

    def append(self, item):
        self.children.append(item)
        self.has_children = True

    def clear(self):
        self.children.remove_all()
        self.has_children = False
        self.expanded = False


# --- EPub Viewer Class ---
class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 800)
        self.set_title(APP_NAME)
        self.temp_dir = None
        self.tts = None
        self._tts_sentences_cache = []

        # Initialize WebView first
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit
            self.webview = WebKit.WebView()
            print("[info] WebKit WebView initialized.")
        except (ImportError, ValueError):
            print("[warn] WebKit not available, using fallback.")
            self.webview = None

        # state
        self.book = None
        self.items = []
        self.item_map = {}
        self.current_index = 0
        self.css_content = ""
        self._toc_actrows = {}
        self._tab_buttons = []
        self.href_map = {}
        self.last_cover_path = None
        self.book_path = None
        self.library = load_library()
        self.library_search_text = ""
        self._lib_search_handler_id = None

        # main layout
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_vbox)

        # Overlay split view
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.32)
        main_vbox.append(self.split)

        # - Sidebar -
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.add_css_class("epub-sidebar")

        header = Adw.HeaderBar()
        header.add_css_class("flat")

        self.library_btn = Gtk.Button(icon_name="view-list-symbolic")
        self.library_btn.set_tooltip_text("Show Library")
        self.library_btn.add_css_class("flat")
        self.library_btn.connect("clicked", self.on_library_clicked)
        header.pack_start(self.library_btn)

        sidebar_box.append(header)

        # Book info in sidebar
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        book_box.set_margin_start(6); book_box.set_margin_end(6); book_box.set_margin_top(6); book_box.set_margin_bottom(3)
        self.cover_image = Gtk.Image()
        self.cover_image.set_size_request(COVER_W, COVER_H)
        self.cover_image.set_valign(Gtk.Align.START)
        book_box.append(self.cover_image)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.book_title = Gtk.Label(label="")
        self.book_title.add_css_class("book-title")
        self.book_title.set_halign(Gtk.Align.START); self.book_title.set_xalign(0.0)
        self.book_title.set_max_width_chars(18); self.book_title.set_wrap(True); self.book_title.set_lines(2)
        self.book_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_author = Gtk.Label(label="")
        self.book_author.add_css_class("book-author")
        self.book_author.set_halign(Gtk.Align.START); self.book_author.set_xalign(0.0)
        text_box.append(self.book_title); text_box.append(self.book_author)
        book_box.append(text_box)

        sidebar_box.append(book_box)

        # TOC placeholder
        self.side_stack = Gtk.Stack()
        self.side_stack.set_vexpand(True)
        self.toc_list = Gtk.Label(label="TOC will appear here")
        self.side_stack.add_titled(self.toc_list, "toc", "TOC")
        sidebar_box.append(self.side_stack)

        self.split.set_sidebar(sidebar_box)

        # - Content Area (Reader) -
        self._reader_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Toolbar
        self.toolbar = Adw.ToolbarView()
        self._reader_content_box.append(self.toolbar)

        # Header Bar (Content)
        self.content_header = Adw.HeaderBar()
        self.content_header.add_css_class("flat")

        # Sidebar toggle button
        self.content_sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.content_sidebar_toggle.add_css_class("flat")
        self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
        self.content_sidebar_toggle.connect("toggled", self._on_sidebar_toggle)
        self.content_header.pack_start(self.content_sidebar_toggle)

        # Open button
        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.add_css_class("flat")
        self.open_btn.set_tooltip_text("Open EPUB")
        self.open_btn.connect("clicked", self.open_file)
        self.content_header.pack_start(self.open_btn)

        # title
        self.content_title_label = Gtk.Label(label=APP_NAME)
        self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)

        # search
        self.library_search_revealer = Gtk.Revealer(reveal_child=False)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_bar.set_margin_start(6); search_bar.set_margin_end(6); search_bar.set_margin_top(6); search_bar.set_margin_bottom(6)
        self.library_search_entry = Gtk.SearchEntry()
        self.library_search_entry.set_placeholder_text("Search library")
        self._lib_search_handler_id = self.library_search_entry.connect("search-changed", lambda e: self._on_library_search_changed(e.get_text()))
        search_bar.append(self.library_search_entry)
        self.library_search_revealer.set_child(search_bar)

        # search toggle
        self.search_toggle_btn = Gtk.Button(icon_name="system-search-symbolic")
        self.search_toggle_btn.add_css_class("flat")
        self.search_toggle_btn.set_tooltip_text("Search library")
        self.search_toggle_btn.connect("clicked", self._toggle_library_search)
        self.content_header.pack_end(self.search_toggle_btn)

        # menu
        menu_model = Gio.Menu()
        menu_model.append("About", "app.about")
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.set_popover(Gtk.PopoverMenu.new_from_model(menu_model))
        self.content_header.pack_end(self.menu_btn)

        self.toolbar.add_top_bar(self.content_header)
        self.toolbar.add_top_bar(self.library_search_revealer)

        # WebView container
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        if self.webview:
            self.scrolled.set_child(self.webview)
        else:
            label = Gtk.Label(label="WebKit not available. TTS highlighting requires WebKit.")
            self.scrolled.set_child(label)

        self._reader_content_box.append(self.scrolled)

        # bottom navigation
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6)
        bottom_bar.set_margin_bottom(6)
        bottom_bar.set_margin_start(6)
        bottom_bar.set_margin_end(6)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("flat")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("flat")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)

        self.progress = Gtk.ProgressBar()
        self.progress.set_hexpand(True)

        bottom_bar.append(self.prev_btn)
        bottom_bar.append(self.progress)
        bottom_bar.append(self.next_btn)

        self._reader_content_box.append(bottom_bar)

        # TTS controls bar
        tts_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tts_bar.set_margin_top(6)
        tts_bar.set_margin_bottom(6)
        tts_bar.set_margin_start(6)
        tts_bar.set_margin_end(6)
        tts_bar.set_halign(Gtk.Align.CENTER)

        self.tts_play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.tts_play_btn.add_css_class("flat")
        self.tts_play_btn.set_tooltip_text("Play TTS")
        self.tts_play_btn.set_sensitive(False)
        self.tts_play_btn.connect("clicked", self.on_tts_play)
        tts_bar.append(self.tts_play_btn)

        self.tts_pause_btn = Gtk.Button(icon_name="media-playback-pause-symbolic")
        self.tts_pause_btn.add_css_class("flat")
        self.tts_pause_btn.set_tooltip_text("Pause/Resume TTS")
        self.tts_pause_btn.set_sensitive(False)
        self.tts_pause_btn.connect("clicked", self.on_tts_pause)
        tts_bar.append(self.tts_pause_btn)

        self.tts_stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self.tts_stop_btn.add_css_class("flat")
        self.tts_stop_btn.set_tooltip_text("Stop TTS")
        self.tts_stop_btn.set_sensitive(False)
        self.tts_stop_btn.connect("clicked", self.on_tts_stop)
        tts_bar.append(self.tts_stop_btn)

        self._reader_content_box.append(tts_bar)

        # Set initial content to reader view
        self.toolbar.set_content(self._reader_content_box)

        # Responsive sidebar breakpoint
        self.reading_breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900px"))
        self.reading_breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(self.reading_breakpoint)

        # Initial state
        self.split.set_collapsed(True)
        self.content_sidebar_toggle.set_active(False)
        self.split.set_show_sidebar(False)

        # Start periodic TTS button state updates if TTS is available
        if TTS_AVAILABLE:
            GLib.timeout_add(500, self._update_tts_button_states)

        # Show library initially
        self.show_library()

    def _update_tts_button_states(self):
        """Periodically update TTS button states"""
        if not self.tts or not TTS_AVAILABLE:
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            return True

        is_playing = self.tts.is_playing()
        is_paused = self.tts.is_paused()
        has_content = bool(self.book and self.items)

        if not is_playing and not is_paused:
            self.tts_play_btn.set_sensitive(has_content)
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_playing and not is_paused:
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_paused:
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-start-symbolic")

        return True

    def on_tts_play(self, *args):
        """Start TTS playback."""
        if not self.tts or not TTS_AVAILABLE:
            print("[warn] TTS not available")
            return

        self._tts_sentences_cache = self._extract_page_text()
        if not self._tts_sentences_cache:
            print("[warn] No text to read")
            return

        print(f"[TTS] Starting playback of {len(self._tts_sentences_cache)} sentences")
        self.tts.speak_sentences_list(
            self._tts_sentences_cache,
            voice="af_sarah",
            speed=1.0,
            lang="en-us",
            highlight_callback=self._on_tts_highlight,
            finished_callback=self._on_tts_finished
        )

    def on_tts_pause(self, *args):
        """Pause/Resume TTS playback."""
        if self.tts:
            if self.tts.is_paused():
                self.tts.resume()
            else:
                self.tts.pause()

    def on_tts_stop(self, *args):
        """Stop TTS playback."""
        if self.tts:
            self.tts.stop()

    def _on_tts_highlight(self, idx, meta):
        """Callback to highlight sentence in WebView."""
        if idx < 0:
            if self.webview:
                js_code = "if(window.tts_clearHighlight) { window.tts_clearHighlight(); }"
                try:
                    self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                except (AttributeError, Exception):
                    pass
            return False

        if meta:
            text_preview = meta.get('text', '')[:50]
            print(f"[TTS] Now reading sentence {idx}: {text_preview}...")
        return False

    def _on_tts_finished(self):
        """Callback when TTS finishes."""
        print("[TTS] Playback finished")

    def _extract_page_text(self):
        """Extract text from current page for TTS."""
        if not self.book or not self.items or self.current_index >= len(self.items):
            return []

        try:
            item = self.items[self.current_index]
            soup = BeautifulSoup(item.get_content(), "html.parser")
            for tag in soup.find_all(['script', 'style']):
                tag.decompose()
            text = soup.get_text(separator=' ', strip=True)
            if not self.tts:
                return []
            sentences = self.tts.split_sentences(text)
            result = []
            for i, sent in enumerate(sentences):
                result.append({
                    "text": sent,
                    "sid": f"sent_{i}"
                })
            return result
        except Exception as e:
            print(f"[error] Failed to extract text: {e}")
            return []

    def _wrap_html(self, raw_html, base_uri):
        """Wraps raw HTML content with necessary CSS and scripts."""
        page_css = (self.css_content or "") + _css + """
        .tts-highlight {
            background: rgba(255,255,100,0.6) !important;
            box-shadow: 0 0 0 3px rgba(255,200,0,0.7) !important;
            border-radius: 4px !important;
            padding: 2px 4px !important;
            transition: background 0.2s ease, box-shadow 0.2s ease !important;
        }
        @media (prefers-color-scheme: dark) {
            .tts-highlight {
                background: rgba(100,200,100,0.5) !important;
                box-shadow: 0 0 0 3px rgba(100,200,100,0.7) !important;
            }
        }
        """

        link_intercept_script = f"""<script>(function(){{
window.tts_clearHighlight = function() {{
    var highlighted = document.querySelectorAll('.tts-highlight');
    highlighted.forEach(function(el) {{
        var parent = el.parentNode;
        while (el.firstChild) parent.insertBefore(el.firstChild, el);
        parent.removeChild(el);
        if(parent.normalize) parent.normalize();
    }});
}};

function updateDarkMode() {{
    if(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {{
        document.documentElement.classList.add('dark-mode');
        document.body.classList.add('dark-mode');
    }} else {{
        document.documentElement.classList.remove('dark-mode');
        document.body.classList.remove('dark-mode');
    }}
}}
updateDarkMode();
if(window.matchMedia) {{
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateDarkMode);
}}

function interceptLinks() {{
    document.addEventListener('click', function(e) {{
        var target=e.target;
        while(target && target.tagName!=='A') {{
            target=target.parentElement;
            if(!target||target===document.body) break;
        }}
        if(target && target.tagName==='A' && target.href) {{
            var href=target.href;
            e.preventDefault();
            e.stopPropagation();
            try {{
                window.location.href=href;
            }} catch(err) {{
                console.error('[js] navigation error:', err);
            }}
            return false;
        }}, true);
}}
if(document.readyState==='loading') {{
    document.addEventListener('DOMContentLoaded', interceptLinks);
}} else {{
    interceptLinks();
}}
}})();</script>"""

        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <base href="{base_uri}">
    <style>{page_css}</style>
    {link_intercept_script}
</head>
<body>
    {raw_html}
</body>
</html>"""
        return full_html

    def _on_sidebar_toggle(self, button):
        if button.get_active():
            self.split.set_show_sidebar(True)
        else:
            self.split.set_show_sidebar(False)

    def on_library_clicked(self, *_):
        if getattr(self, "book", None):
            try:
                self.content_sidebar_toggle.set_visible(False)
                self.split.set_show_sidebar(False)
                self.split.set_collapsed(False)
            except Exception:
                pass
        self.show_library()

    def open_file(self, *_):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        epub_filter = Gtk.FileFilter()
        epub_filter.add_pattern("*.epub")
        epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)
        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                target = f.get_path()
                try:
                    self._save_progress_for_library()
                except Exception:
                    pass
                try:
                    self.cleanup()
                except Exception:
                    pass
                try:
                    self.open_btn.set_visible(False)
                except Exception:
                    pass
                self._enable_sidebar_for_reading()
                self.load_epub(target)
        except GLib.Error:
            pass

    def _enable_sidebar_for_reading(self):
        try:
            self.content_sidebar_toggle.set_visible(True)
            self.content_sidebar_toggle.set_sensitive(True)
            self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
            try:
                self.open_btn.set_visible(False)
                self.search_toggle_btn.set_visible(False)
            except Exception:
                pass
        except Exception:
            pass

    def extract_css(self):
        self.css_content = ""
        if not self.book:
            return
        try:
            for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
                try:
                    self.css_content += item.get_content().decode("utf-8") + "\n"
                except Exception:
                    pass
        except Exception:
            pass

    def load_epub(self, path, resume=False, resume_index=None):
        try:
            try:
                self.toolbar.set_content(self._reader_content_box)
            except Exception:
                pass
            try:
                self._enable_sidebar_for_reading()
                self.open_btn.set_visible(False)
                self.search_toggle_btn.set_visible(False)
                self.library_search_revealer.set_reveal_child(False)
            except Exception:
                pass
            try:
                self.cleanup()
            except Exception:
                pass
            self.book_path = path

            self.book = epub.read_epub(path)
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
                try:
                    iid = getattr(it, "id", None) or (it.get_id() if hasattr(it, "get_id") else None)
                except Exception:
                    iid = None
                if not iid:
                    iid = it.get_name() or os.urandom(8).hex()
                id_map[iid] = it
            ordered = []
            try:
                spine = getattr(self.book, "spine", None) or []
                for entry in spine:
                    sid = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
                    if sid in id_map:
                        ordered.append(id_map.pop(sid))
                ordered.extend(id_map.values())
                self.items = ordered
            except Exception:
                self.items = docs

            if not self.items:
                self.show_error("No document items found in EPUB")
                return

            # extract
            self.temp_dir = tempfile.mkdtemp()

            # Initialize TTS with the new temp directory
            if TTS_AVAILABLE and not self.tts:
                try:
                    model_path = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                    voices_path = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                    self.tts = TTSEngine(webview_getter=lambda: self.webview, base_temp_dir=self.temp_dir, kokoro_model_path=model_path, voices_bin_path=voices_path)
                    print("[info] TTS initialized for this book")
                except Exception as e:
                    print(f"[warn] Could not initialize TTS: {e}")
                    self.tts = None

            try:
                with zipfile.ZipFile(path, "r") as z:
                    z.extractall(self.temp_dir)
            except Exception:
                pass

            self.item_map = {it.get_name(): it for it in self.items}
            self.extract_css()

            # metadata
            title = APP_NAME
            author = ""
            try:
                meta = self.book.get_metadata("DC", "title")
                if meta and meta[0]:
                    title = meta[0][0]
                m2 = self.book.get_metadata("DC", "creator")
                if m2 and m2[0]:
                    author = m2[0][0]
            except Exception:
                pass
            self.book_title.set_text(title)
            self.book_author.set_text(author)
            self.content_title_label.set_text(title)
            self.set_title(title or APP_NAME)

            if resume:
                if isinstance(resume_index, int) and 0 <= resume_index < len(self.items):
                    self.current_index = resume_index
                else:
                    for e in self.library:
                        if e.get("path") == path:
                            self.current_index = int(e.get("index", 0)) if isinstance(e.get("index", 0), int) else 0
                            break
                self.update_navigation()
                self.display_page()
            else:
                self.current_index = 0
                self.update_navigation()
                self.display_page()

            self._update_library_entry()
        except Exception:
            print(traceback.format_exc())
            self.show_error("Error loading EPUB – see console")

    def cleanup(self):
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception:
            pass
        self.temp_dir = None
        self.book = None
        self.items = []
        self.item_map = {}
        self.current_index = 0
        self.css_content = ""
        self.href_map = {}
        self.book_path = None
        self.last_cover_path = None

    def update_navigation(self):
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.items) - 1)
        if self.items:
            self.progress.set_fraction((self.current_index + 1) / len(self.items))
        else:
            self.progress.set_fraction(0.0)

    def display_page(self):
        if not self.book or not self.items or self.current_index < 0 or self.current_index >= len(self.items):
            return
        item = self.items[self.current_index]
        try:
            content = item.get_content()
            if not content:
                if self.webview:
                    self.webview.load_html("<p>Empty page</p>", "file:///")
                return
            soup = BeautifulSoup(content, "html.parser")
            wrapped_html = self._wrap_html(str(soup), f"file://{self.temp_dir}/")
            if self.webview:
                self.webview.load_html(wrapped_html, f"file://{self.temp_dir}/")
        except Exception as e:
            print(f"[error] Failed to display page: {e}")
            self.show_error(f"Error displaying page: {e}")

    def next_page(self, *_):
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
            self.update_navigation()
            self.display_page()
            self._save_progress_for_library()

    def prev_page(self, *_):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_navigation()
            self.display_page()
            self._save_progress_for_library()

    def show_error(self, message):
        label = Gtk.Label(label=message)
        label.set_valign(Gtk.Align.CENTER)
        label.set_halign(Gtk.Align.CENTER)
        self.toolbar.set_content(label)

    def _create_rounded_cover_texture(self, path, width, height, radius=10):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, height, True)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            ctx = cairo.Context(surface)

            ctx.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
            ctx.arc(width - radius, radius, radius, 3 * math.pi / 2, 2 * math.pi)
            ctx.arc(width - radius, height - radius, radius, 0, math.pi / 2)
            ctx.arc(radius, height - radius, radius, math.pi / 2, math.pi)
            ctx.close_path()

            ctx.set_source_rgba(0, 0, 0, 0)
            ctx.fill()

            Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
            ctx.paint()

            texture = Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_surface(surface, 0, 0, width, height))
            return texture
        except Exception:
            return None

    def _update_library_entry(self):
        path = self.book_path or ""
        if not path:
            return
        title = self.book_title.get_text() or os.path.basename(path)
        author = self.book_author.get_text() or ""
        cover_src = self.last_cover_path
        cover_dst = None

        found = False
        found_entry = None
        for e in list(self.library):
            if e.get("path") == path:
                e["title"] = title
                e["author"] = author
                if cover_dst:
                    e["cover"] = cover_dst
                e["index"] = int(self.current_index)
                e["progress"] = float(self.progress.get_fraction() or 0.0)
                found = True
                found_entry = e
                break

        if found and found_entry is not None:
            try:
                self.library = [ee for ee in self.library if ee.get("path") != path]
                self.library.append(found_entry)
            except Exception:
                pass
        else:
            if not found:
                entry = {
                    "path": path,
                    "title": title,
                    "author": author,
                    "cover": cover_dst,
                    "index": int(self.current_index),
                    "progress": float(self.progress.get_fraction() or 0.0)
                }
                self.library.append(entry)
                if len(self.library) > 200:
                    self.library = self.library[-200:]
        save_library(self.library)

    def _save_progress_for_library(self):
        if not self.book_path:
            return
        changed = False
        for e in self.library:
            if e.get("path") == self.book_path:
                e["index"] = int(self.current_index)
                e["progress"] = float(self.progress.get_fraction() or 0.0)
                changed = True
                break
        if changed:
            save_library(self.library)

    def _toggle_library_search(self, *_):
        reveal = not self.library_search_revealer.get_reveal_child()
        self.library_search_revealer.set_reveal_child(reveal)
        if not reveal:
            try:
                if self._lib_search_handler_id:
                    self.library_search_entry.handler_block(self._lib_search_handler_id)
                self.library_search_entry.set_text("")
                self.library_search_text = ""
                self.show_library()
            finally:
                try:
                    if self._lib_search_handler_id:
                        self.library_search_entry.handler_unblock(self._lib_search_handler_id)
                except Exception:
                    pass
        else:
            self.library_search_entry.grab_focus()

    def _on_library_search_changed(self, arg):
        try:
            if isinstance(arg, str):
                text = arg
            else:
                text = arg.get_text() if hasattr(arg, "get_text") else str(arg or "")
            self.library_search_text = (text or "").strip()
            self.show_library()
        except Exception:
            pass

    def _get_library_entries_for_display(self):
        entries = list(reversed(self.library))
        if not entries:
            return entries
        try:
            if getattr(self, "book_path", None):
                for i, e in enumerate(entries):
                    try:
                        if os.path.abspath(e.get("path", "")) == os.path.abspath(self.book_path or ""):
                            if i != 0:
                                entries.insert(0, entries.pop(i))
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        return entries

    def _is_loaded_entry(self, entry):
        try:
            if not entry:
                return False
            if not getattr(self, "book_path", None):
                return False
            return os.path.abspath(entry.get("path", "")) == os.path.abspath(self.book_path or "")
        except Exception:
            return False

    def show_library(self):
        self.library_search_revealer.set_reveal_child(bool(self.library_search_text))
        try:
            if self._lib_search_handler_id:
                self.library_search_entry.handler_block(self._lib_search_handler_id)
            self.library_search_entry.set_text(self.library_search_text or "")
        finally:
            try:
                if self._lib_search_handler_id:
                    self.library_search_entry.handler_unblock(self._lib_search_handler_id)
            except Exception:
                pass

        query = (self.library_search_text or "").strip().lower()
        entries = self._get_library_entries_for_display()

        if query:
            entries = [e for e in entries if query in (e.get("title") or "").lower() or query in (e.get("author") or "").lower() or query in (os.path.basename(e.get("path","")).lower())]

        if not entries:
            lbl = Gtk.Label(label="No books in library\nOpen a book to add it here.")
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.set_margin_top(40)
            self.toolbar.set_content(lbl)
            self.content_title_label.set_text("Library")
            return

        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_max_children_per_line(30)
        flowbox.set_min_children_per_line(2)
        flowbox.set_row_spacing(10)
        flowbox.set_column_spacing(10)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        flowbox.add_css_class("library-grid")
        flowbox.set_margin_start(12)
        flowbox.set_margin_end(12)
        flowbox.set_margin_top(12)
        flowbox.set_margin_bottom(12)

        for entry in entries:
            title = entry.get("title") or os.path.basename(entry.get("path",""))
            author = entry.get("author") or ""
            cover = entry.get("cover")
            path = entry.get("path")
            idx = entry.get("index", 0)

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            card.add_css_class("library-card")
            if self._is_loaded_entry(entry):
                card.add_css_class("active")
            card.set_size_request(160, 320)

            img = Gtk.Picture()
            img.set_size_request(140, 210)
            img.set_can_shrink(True)
            if cover and os.path.exists(cover):
                texture = self._create_rounded_cover_texture(cover, 140, 210, radius=10)
                if texture:
                    img.set_paintable(texture)
                else:
                    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 200)
                    pb.fill(0xddddddff)
                    img.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
            else:
                pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 200)
                pb.fill(0xddddddff)
                img.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
            img.add_css_class("cover")
            img.set_halign(Gtk.Align.CENTER)
            card.append(img)

            t = Gtk.Label()
            t.add_css_class("title")
            t.set_ellipsize(Pango.EllipsizeMode.END)
            t.set_wrap(True)
            t.set_max_width_chars(16)
            t.set_lines(2)
            t.set_halign(Gtk.Align.CENTER)
            t.set_justify(Gtk.Justification.CENTER)
            t.set_margin_top(4)
            t.set_markup(GLib.markup_escape_text(title))
            card.append(t)

            a = Gtk.Label()
            a.add_css_class("author")
            a.set_ellipsize(Pango.EllipsizeMode.END)
            a.set_max_width_chars(18)
            a.set_halign(Gtk.Align.CENTER)
            a.set_markup(GLib.markup_escape_text(author))
            card.append(a)

            gesture = Gtk.GestureClick.new()
            def _on_click(_gesture, _n, _x, _y, p=path, resume_idx=idx):
                if p and os.path.exists(p):
                    try:
                        self._save_progress_for_library()
                    except Exception:
                        pass
                    try:
                        self.cleanup()
                    except Exception:
                        pass
                    try:
                        self.toolbar.set_content(self._reader_content_box)
                    except Exception:
                        pass
                    self.load_epub(p, resume=True, resume_index=resume_idx)
            gesture.connect("released", _on_click)
            card.add_controller(gesture)
            flowbox.append(card)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(flowbox)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        container.append(scroll)
        self.toolbar.set_content(container)
        self.content_title_label.set_text("Library")


# --- Application Class ---
class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.create_action("quit", self.quit, ["<primary>q"])
        self.create_action("about", self.show_about)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            try:
                win = EPubViewer(self)
                print("[info] EPubViewer created successfully.")
            except Exception as e:
                print(f"[error] Failed to create EPubViewer: {e}")
                import traceback
                traceback.print_exc()
                error_dialog = Adw.MessageDialog.new(
                    None,
                    "Error Creating Window",
                    f"An error occurred: {e}\n\nCheck console for details."
                )
                error_dialog.add_response("close", "Close")
                error_dialog.set_default_response("close")
                error_dialog.set_close_response("close")
                error_dialog.present()
                return
        win.present()
        print("[info] EPubViewer presented.")

    def show_about(self, *_):
        about = Adw.AboutWindow(
            transient_for=self.props.active_window,
            application_name="EPUB Viewer",
            application_icon="io.github.fastrizwaan.tts",
            developer_name="Your Name",
            version="1.0.0",
            website="https://github.com/fastrizwaan/tts",
            issue_url="https://github.com/fastrizwaan/tts/issues",
            license_type=Gtk.License.GPL_3_0
        )
        about.present()

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main():
    _ensure_library_dir()
    app = Application()
    return app.run(None)


if __name__ == "__main__":
    main()
