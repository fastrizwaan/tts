#!/usr/bin/env python3
# Complete EPUB viewer with robust TTS integrated + sidebar TOC
import os, json, tempfile, shutil, re, urllib.parse, signal, sys, math, threading, queue, subprocess, uuid, time, pathlib, hashlib, multiprocessing
import html as _html

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango

from ebooklib import epub
import soundfile as sf
try:
    from kokoro_onnx import Kokoro
except Exception:
    Kokoro = None

Adw.init()

# --- Utilities ---
_s_re_split = re.compile(r'(?<=[.!?])["”’\)\]]?\s+|\n+')

def split_sentences(text: str):
    """
    Splits text into sentences cleanly.
    Fixes cases like 'M y' -> 'My' while preserving punctuation and italics.
    """
    if not text:
        return []

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text.strip())

    # Fix broken words: letter + space + letter (but not around punctuation)
    # Fix only single-letter fragments like "M y" or "I t"
    text = re.sub(r'\b([A-Za-z])\s+([A-Za-z])', r'\1\2', text)


    # Split on sentence-ending punctuation followed by space+capital/quote
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z“"\'(])', text)

    sentences = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Avoid trivial fragments
        if len(p) == 1 and p.isalpha():
            continue
        sentences.append(p)

    return sentences


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


# --- TTS Manager ---
class TTSManager:
    """
    Manages synthesis + playback.
    - synth_queue: (sid, text)
    - spawn per-sentence subprocess (synth_single_process) so Stop can kill it
    - play_queue: (sid, wavpath) consumed by player thread which uses paplay
    """
    def __init__(self, webview_getter, base_temp_dir,
                 kokoro_model_path=None, voices_bin_path=None,
                 voice="af_sarah", speed=1.0, lang="en-us"):
        self.get_webview = webview_getter
        self.base_temp_dir = base_temp_dir
        self.tts_dir = os.path.join(self.base_temp_dir, "tts")
        os.makedirs(self.tts_dir, exist_ok=True)

        self.kokoro_model_path = kokoro_model_path or os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
        self.voices_bin_path = voices_bin_path or os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
        self.voice = voice
        self.speed = speed
        self.lang = lang

        self.synth_queue = queue.Queue()
        self.play_queue = queue.Queue()

        self._synth_thread = None
        self._player_thread = None

        self._stop_event = threading.Event()
        self._paused = threading.Event()  # when set => paused

        self._current_play_proc = None
        self._current_synth_proc = None  # multiprocessing.Process for current synth
        self._threads_running = False

        self.current_chapter_id = None
        self.current_highlight_id = None

        self._user_set_columns = False
        self._initial_layout_done = False

        
        # File cache management - keep track of played files
        self.played_files = []  # List of (sid, filepath) in play order
        self.max_cache_files = 5  # Keep last 5 played files
        self.current_playing_file = None

    def start(self, chapter_id, sentences):
        # If starting a new chapter, stop previous
        if self.current_chapter_id != chapter_id:
            self.stop()
        self.current_chapter_id = chapter_id
        # Ensure TTS directory exists before starting synthesis
        os.makedirs(self.tts_dir, exist_ok=True)
        self._stop_event.clear()
        for sid, text in sentences:
            # ensure non-empty
            if text and text.strip():
                self.synth_queue.put((sid, text))
        if not self._threads_running:
            self._threads_running = True
            self._synth_thread = threading.Thread(target=self._synth_worker, name="tts-synth", daemon=True)
            self._synth_thread.start()
            self._player_thread = threading.Thread(target=self._player_worker, name="tts-player", daemon=True)
            self._player_thread.start()

    def pause(self):
        if self._paused.is_set():
            return
        self._paused.set()
        proc = self._current_play_proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGSTOP)
            except Exception:
                pass

    def resume(self):
        if not self._paused.is_set():
            return
        self._paused.clear()
        proc = self._current_play_proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGCONT)
            except Exception:
                pass

    def stop(self):
        # clear flags
        self._stop_event.set()
        # clear synth queue
        try:
            while not self.synth_queue.empty():
                self.synth_queue.get_nowait()
        except Exception:
            pass
        # clear play queue and delete queued wavs
        try:
            while not self.play_queue.empty():
                sid, f = self.play_queue.get_nowait()
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass
        # kill current synth subprocess (if running)
        if self._current_synth_proc:
            try:
                self._current_synth_proc.terminate()
                self._current_synth_proc.join(timeout=0.5)
                if self._current_synth_proc.is_alive():
                    self._current_synth_proc.kill()
                    self._current_synth_proc.join(timeout=0.2)
            except Exception:
                pass
            self._current_synth_proc = None
        # kill current playback
        if self._current_play_proc:
            try:
                self._current_play_proc.kill()
            except Exception:
                pass
            self._current_play_proc = None
        # clear highlight in UI
        self._run_js_clear_highlight()
        # mark stopped so threads exit
        self._threads_running = False
        # wait for threads to actually finish before cleaning up files
        if self._synth_thread and self._synth_thread.is_alive():
            try:
                self._synth_thread.join(timeout=1.0)
            except Exception:
                pass
        if self._player_thread and self._player_thread.is_alive():
            try:
                self._player_thread.join(timeout=1.0)
            except Exception:
                pass
        # Clean up all cached files and any remaining TTS files
        self._clean_cache_files()
        try:
            if os.path.exists(self.tts_dir):
                for fn in os.listdir(self.tts_dir):
                    if fn.endswith('.wav'):  # Clean up any remaining TTS wav files
                        fp = os.path.join(self.tts_dir, fn)
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
        except Exception:
            pass
        self._paused.clear()
        self.current_chapter_id = None
        self.current_highlight_id = None

    def _synth_worker(self):
        # worker thread: for each sentence, spawn a subprocess to synthesize,
        # allowing stop() to terminate it immediately.
        while not self._stop_event.is_set():
            try:
                sid, text = self.synth_queue.get(timeout=0.2)
            except queue.Empty:
                # idle
                if self._stop_event.is_set():
                    break
                else:
                    continue
            # prepare path
            outname = f"{sid}_{uuid.uuid4().hex[:8]}.wav"
            outpath = os.path.join(self.tts_dir, outname)
            # spawn a multiprocessing.Process to run synth_single_process
            proc = multiprocessing.Process(target=synth_single_process, args=(self.kokoro_model_path, self.voices_bin_path, text, outpath, self.voice, self.speed, self.lang))
            proc.start()
            self._current_synth_proc = proc
            # wait loop, but respond to stop
            while True:
                if self._stop_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    proc.join(timeout=0.2)
                    break
                if not proc.is_alive():
                    proc.join(timeout=0.2)
                    break
                time.sleep(0.05)
            self._current_synth_proc = None
            # if file created, enqueue for playback
            if os.path.exists(outpath) and not self._stop_event.is_set():
                self.play_queue.put((sid, outpath))
            else:
                # cleanup if file missing
                try:
                    if os.path.exists(outpath):
                        os.remove(outpath)
                except Exception:
                    pass
        # thread exit
        return

    def _manage_file_cache(self, new_file_path, sid):
        """Manage the rolling cache of TTS files - keep current + last 5"""
        # Add new file to played files list
        self.played_files.append((sid, new_file_path))
        
        # If we exceed the cache limit, remove oldest files
        while len(self.played_files) > self.max_cache_files:
            old_sid, old_path = self.played_files.pop(0)  # Remove oldest
            try:
                if os.path.exists(old_path) and old_path != self.current_playing_file:
                    os.remove(old_path)
            except Exception:
                pass

    def _clean_cache_files(self):
        """Clean up all cached TTS files"""
        for sid, filepath in self.played_files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
        self.played_files.clear()
        self.current_playing_file = None

    def _player_worker(self):
        while not self._stop_event.is_set() or not self.play_queue.empty():
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            try:
                sid, wavpath = self.play_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            
            # Set current playing file
            self.current_playing_file = wavpath
            
            # highlight in UI
            self.current_highlight_id = sid
            self._run_js_highlight(sid)
            # play via paplay
            try:
                proc = subprocess.Popen(["paplay", wavpath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._current_play_proc = proc
                if self._paused.is_set():
                    try:
                        proc.send_signal(signal.SIGSTOP)
                    except Exception:
                        pass
                while True:
                    if self._stop_event.is_set():
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                    if self._paused.is_set():
                        time.sleep(0.05)
                        continue
                    ret = proc.poll()
                    if ret is not None:
                        break
                    time.sleep(0.05)
            except Exception as e:
                print("player error:", e)
            finally:
                # un-highlight after played (if not paused)
                if not self._paused.is_set():
                    self._run_js_unhighlight(sid)
                    self.current_highlight_id = None
                
                # Add to cache and manage file cleanup AFTER playing
                if not self._stop_event.is_set():
                    self._manage_file_cache(wavpath, sid)
                else:
                    # If stopped, delete the file immediately
                    try:
                        if os.path.exists(wavpath):
                            os.remove(wavpath)
                    except Exception:
                        pass
                
                self.current_playing_file = None
                self._current_play_proc = None
                self._current_synth_proc = None
        return

    def is_playing(self):
        """Check if TTS is currently playing (not stopped and not paused)"""
        return self._threads_running and not self._stop_event.is_set()

    def is_paused(self):
        """Check if TTS is currently paused"""
        return self._paused.is_set() and not self._stop_event.is_set()

    # UI JS helpers
    def _run_js_highlight(self, sid):
        webview = self.get_webview()
        if not webview:
            return
        # Use scrollIntoView to snap to left for multi-column layouts
        js = f"""
        (function() {{
            try {{
                var el = document.querySelector('[data-tts-id="{sid}"]');
                if (!el) return;
                // remove previous
                document.querySelectorAll('.tts-highlight').forEach(function(p){{ p.classList.remove('tts-highlight'); }});
                el.classList.add('tts-highlight');
                // For multi-column layouts, snap to left of the column containing the element
                try {{
                    var rect = el.getBoundingClientRect();
                    var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                    
                    // Check if we're in multi-column mode (scrollWidth > clientWidth)
                    var scrollWidth = document.documentElement.scrollWidth;
                    var clientWidth = document.documentElement.clientWidth;
                    
                    if (scrollWidth > clientWidth) {{
                        // Multi-column mode - snap to left of containing column
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        
                        // If element is not fully visible, scroll to make it visible at the left
                        if (rect.left < 0 || rect.right > viewportWidth) {{
                            var targetScroll = currentScroll + rect.left - 20; // 20px margin from left
                            window.scrollTo({{ left: Math.max(0, targetScroll), behavior: 'smooth' }});
                        }}
                    }} else {{
                        // Single column mode - center vertically
                        el.scrollIntoView({{ behavior: 'smooth', block: 'center', inline: 'nearest' }});
                    }}
                }} catch(e) {{
                    console.log('Fallback scroll');
                    el.scrollIntoView({{ behavior: 'smooth', block: 'nearest', inline: 'start' }});
                }}
            }} catch(e){{ console.error('highlight error', e); }}
        }})();
        """
        GLib.idle_add(lambda: webview.evaluate_javascript(js, -1, None, None, None, None, None))

    def _run_js_unhighlight(self, sid):
        webview = self.get_webview()
        if not webview:
            return
        js = f"""
        (function() {{
            try {{
                var el = document.querySelector('[data-tts-id="{sid}"]');
                if (el) el.classList.remove('tts-highlight');
            }} catch(e){{ }}
        }})();
        """
        GLib.idle_add(lambda: webview.evaluate_javascript(js, -1, None, None, None, None, None))

    def _run_js_clear_highlight(self):
        webview = self.get_webview()
        if not webview:
            return
        js = """
        (function() {
            try {
                document.querySelectorAll('.tts-highlight').forEach(function(p){ p.classList.remove('tts-highlight'); });
            } catch(e) {}
        })();
        """
        GLib.idle_add(lambda: webview.evaluate_javascript(js, -1, None, None, None, None, None))

    def reapply_highlight_after_reload(self):
        # Called after chapter reload/resize to reapply highlight if any
        if self.current_highlight_id:
            self._run_js_highlight(self.current_highlight_id)

# -----------------------
# EpubViewer (complete, with sidebar)
# -----------------------
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

        # tts manager
        self.tts = None

        # sidebar widgets (initialized in setup_ui)
        self.split_view = None
        self.sidebar = None
        self.cover_image = None
        self.book_title_label = None
        self.toc_list = None
        self.sidebar_toggle_btn = None

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
        # Split view (sidebar + content)
        self.split_view = Adw.OverlaySplitView(
            sidebar_width_fraction=0.25,
            show_sidebar=True
        )
        self.set_content(self.split_view)

        # ToolbarView (content)
        self.toolbar_view = Adw.ToolbarView()
        header_bar = Adw.HeaderBar()

        # Sidebar toggle button
        self.sidebar_toggle_btn = Gtk.Button()
        self.sidebar_toggle_btn.set_icon_name("sidebar-show-symbolic")
        self.sidebar_toggle_btn.add_css_class("flat")
        self.sidebar_toggle_btn.connect("clicked", self._on_toggle_sidebar)
        header_bar.pack_start(self.sidebar_toggle_btn)

        # Keep existing top-bar controls (open/menu etc)
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

        try:
            header_bar.pack_start(open_button)
            header_bar.pack_start(nav_box)
            header_bar.pack_end(menu_button)
        except AttributeError:
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

        self.toolbar_view.add_top_bar(header_bar)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.main_box)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled_window.set_vexpand(True)
        self.main_box.append(self.scrolled_window)

        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.set_margin_start(30)
        self.webview.set_margin_end(30)
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

        # Sidebar (left)
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sidebar.set_margin_top(12)
        self.sidebar.set_margin_bottom(12)
        self.sidebar.set_margin_start(12)
        self.sidebar.set_margin_end(12)

        # Cover image
        self.cover_image = Gtk.Image()
        self.cover_image.set_pixel_size(160)
        self.sidebar.append(self.cover_image)

        # Book title
        self.book_title_label = Gtk.Label(label="No book loaded")
        self.book_title_label.set_xalign(0)
        self.sidebar.append(self.book_title_label)

        # Separator
        self.sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # TOC header
        toc_header = Gtk.Label(label="Table of Contents")
        toc_header.add_css_class("dim-label")
        toc_header.set_xalign(0)
        self.sidebar.append(toc_header)

        # ListBox for chapters
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.set_vexpand(True)
        self.toc_list.connect("row-activated", self._on_toc_row_activated)
        self.sidebar.append(self.toc_list)

        # Small spacer and a hide/show hint
        hint = Gtk.Label(label="Click chapters to open; toggle sidebar via button.")
        hint.set_xalign(0)
        hint.add_css_class("dim-label")
        self.sidebar.append(hint)

        # attach to split view
        self.split_view.set_sidebar(self.sidebar)
        self.split_view.set_content(self.toolbar_view)

        # resize notifications
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)

        # Add periodic TTS button state update
        GLib.timeout_add(500, self._update_tts_button_states)

    def _on_toggle_sidebar(self, button):
        visible = self.split_view.get_show_sidebar()
        self.split_view.set_show_sidebar(not visible)

    def _populate_toc_list(self):
        """Fill the sidebar ListBox with chapter rows."""
        # clear - GTK4 way
        child = self.toc_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.toc_list.remove(child)
            child = next_child

        for idx, ch in enumerate(self.chapters):
            title = ch.get('title') or f"Chapter {idx+1}"
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            row.set_child(label)  # GTK4 uses set_child() instead of add()
            # store index on row for activation
            row._chapter_index = idx
            self.toc_list.append(row)

    def _on_toc_row_activated(self, listbox, row):
        # called when a row is double-clicked or activated (Enter)
        if hasattr(row, "_chapter_index"):
            idx = row._chapter_index
            if 0 <= idx < len(self.chapters):
                self.current_chapter = idx
                self.load_chapter()
                GLib.timeout_add(300, self.update_navigation)

    def _find_cover_in_resources(self):
        """Return filepath to cover image in temp resources if found, else None."""
        if not self.temp_dir:
            return None
        resources_dir_fs = os.path.join(self.temp_dir, 'resources')
        if not os.path.isdir(resources_dir_fs):
            return None
        candidates = []
        for fn in os.listdir(resources_dir_fs):
            lower = fn.lower()
            if lower.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg')):
                candidates.append(fn)
        if not candidates:
            return None
        # prefer file with 'cover' in name
        for fn in candidates:
            if 'cover' in fn.lower():
                return os.path.join(resources_dir_fs, fn)
        # fallback first image
        return os.path.join(resources_dir_fs, candidates[0])

    def _populate_sidebar_after_load(self):
        # set title from metadata if available
        title = None
        try:
            md = self.current_book.get_metadata('DC', 'title')
            if md and len(md) > 0 and md[0]:
                maybe = md[0][0]
                if maybe and len(maybe.strip()) > 0:
                    title = maybe.strip()
        except Exception:
            title = None
        if not title and self.chapters:
            title = self.chapters[0].get('title') or "Untitled"
        if not title:
            title = "Untitled Book"
        self.book_title_label.set_text(title)

        cover_path = self._find_cover_in_resources()
        if cover_path and os.path.exists(cover_path):
            try:
                # use Gtk.Image from file
                self.cover_image.set_from_file(cover_path)
            except Exception:
                try:
                    self.cover_image.set_from_icon_name("image-missing")
                except Exception:
                    pass
        else:
            try:
                self.cover_image.set_from_icon_name("book-open")
            except Exception:
                pass

        self._populate_toc_list()

    def _update_tts_button_states(self):
        """Periodically update TTS button states based on actual TTS state"""
        if not self.tts:
            return True  # Continue the timeout
        
        is_playing = self.tts.is_playing()
        is_paused = self.tts.is_paused()
        
        if not is_playing and not is_paused:
            # TTS is stopped
            self.tts_play_btn.set_sensitive(bool(self.current_book and self.chapters))
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_playing and not is_paused:
            # TTS is actively playing
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_paused:
            # TTS is paused
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-start-symbolic")
        
        return True  # Continue the timeout

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

    # ... all navigation and TTS code below remains identical to your original implementation ...
    # For brevity we reuse the existing methods you provided, unchanged. Insert them here exactly as in your original file.
    # (To keep this example compact I've kept the rest of your methods unmodified.)
    # Paste the remaining methods from your original script (is_single_column_mode, set_column_count, set_column_width,
    # on_webview_load_changed, _after_load_update, calculate_column_dimensions, on_scroll_position_changed, on_key_pressed,
    # on_scroll_event, snap_to_nearest_step, smooth_scroll_to, file open handlers, load_epub, extract_chapters, process_chapter_content,
    # extract_resources, extract_title, load_chapter, update_navigation, _delayed_navigation_update, _refresh_buttons_based_on_adjustment,
    # on_prev_chapter, on_next_chapter, on_prev_page, on_next_page, _on_js_result, _update_page_buttons_from_js, _on_scroll_info_result,
    # _query_and_update_scroll_state, _on_page_state_result, _on_page_info_result, update_page_info, on_size_allocate, _on_allocation_timeout,
    # _update_column_css, on_window_resize, _delayed_resize_reload, _on_pre_resize_scroll_info, _do_resize_reload, _restore_scroll_position,
    # show_error, cleanup, _collect_sentences_for_current_chapter, on_tts_play, on_tts_pause, on_tts_stop)
    #
    # To keep this file runnable, the full implementations follow (copied from your original script).
    # -----------------------
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
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            width = 1200; height = 800
        available = max(100, width - (2 * self.column_padding) - self._webview_horizontal_margins())
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

        # Multi-column navigation — preserve original logic
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
                return True
            elif keyval in (65363, 65366):  # Right / PageDown
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
                        var actualStepSize = actualColumnWidth + column_gap;
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

    # File open / epub handling unchanged except we call populate_sidebar_after_load
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
            # populate sidebar now we have resources/chapter titles
            GLib.idle_add(self._populate_sidebar_after_load)
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

    def process_chapter_content(self, content, item):
        from bs4 import BeautifulSoup
        from html import unescape
        import urllib.parse, os, re

        self.calculate_column_dimensions()
        apply_columns = not self.is_single_column_mode()

        # --- Styling (same as before) ---
        if apply_columns:
            if self.column_mode == 'fixed':
                column_css = f"column-count:{self.fixed_column_count}; column-gap:{self.column_gap}px;"
            else:
                column_css = f"column-width:{self.actual_column_width}px; column-gap:{self.column_gap}px;"
            body_style = f"""
                margin:0; padding:{self.column_padding}px;
                font-family:'Cantarell',sans-serif;
                font-size:16px; line-height:1.6;
                background-color:#fafafa; color:#2e3436;
                {column_css}
                column-fill:balance;
                height:calc(100vh - {self.column_padding*2}px);
                overflow-x:auto; overflow-y:hidden;
                box-sizing:border-box;
            """
        else:
            body_style = f"""
                margin:0; padding:{self.column_padding}px;
                font-family:'Cantarell',sans-serif;
                font-size:16px; line-height:1.6;
                background-color:#fafafa; color:#2e3436;
                column-count:1; column-width:auto; column-gap:0;
                height:auto;
                overflow-x:hidden; overflow-y:auto;
                box-sizing:border-box;
            """

        css_styles = f"""
        <style>
        body {{ {body_style} }}
        .tts-highlight {{ background:rgba(255,215,0,0.35);
                          box-shadow:0 0 0 2px rgba(255,215,0,0.35); }}
        h1,h2,h3,h4,h5,h6 {{ margin-top:1.5em; margin-bottom:0.5em; font-weight:bold; }}
        p {{ margin:0 0 1em 0; text-align:justify; hyphens:auto; }}
        img,figure {{ display:block; max-width:100%; height:auto; margin:1em auto; break-inside:avoid-column; }}
        blockquote {{ margin:1em 2em; font-style:italic; border-left:3px solid #3584e4; padding-left:1em; }}
        @media(prefers-color-scheme:dark) {{
          body {{ background:#242424; color:#e3e3e3; }}
          blockquote {{ border-left-color:#62a0ea; }}
          .tts-highlight {{ background:rgba(0,127,0,0.75);
                            box-shadow:0 0 0 2px rgba(0,127,0,0.75); }}
        }}
        </style>
        """

        try:
            soup = BeautifulSoup(content, "html.parser")
        except Exception:
            soup = BeautifulSoup(f"<body>{content}</body>", "html.parser")

        TARGET_TAGS = [
            "p","div","section","article","li","blockquote",
            "figcaption","caption","dt","dd","td","th",
            "summary","pre","h1","h2","h3","h4","h5","h6"
        ]

        def rebuild_block(block):
            raw_text = block.get_text(" ", strip=True)
            if not raw_text:
                return
            sentences = split_sentences(unescape(raw_text))
            if not sentences:
                return

            # Rebuild the block
            block.clear()
            for sent in sentences:
                sid = stable_id_for_text(sent)
                span = soup.new_tag("span")
                span["data-tts-id"] = sid
                span.string = sent
                block.append(span)
                block.append(" ")

        # Process each block once (no recursion into inline <i>/<b>)
        for tag in TARGET_TAGS:
            for elem in soup.find_all(tag):
                rebuild_block(elem)

        # --- Fix resource links ---
        body = soup.body or soup
        body_content = "".join(str(ch) for ch in body.contents)
        resources_dir_fs = os.path.join(self.temp_dir or "", "resources")
        available = set(os.listdir(resources_dir_fs)) if os.path.isdir(resources_dir_fs) else set()

        def repl_src(m):
            orig = m.group(1)
            if orig.startswith(("data:", "resources/", "/")):
                return f'src="{orig}"'
            name = os.path.basename(urllib.parse.urlparse(orig).path)
            if name in available:
                return f'src="resources/{name}"'
            return f'src="{orig}"'

        def repl_href(m):
            orig = m.group(1)
            if orig.startswith(("#", "resources/", "/")):
                return f'href="{orig}"'
            name = os.path.basename(urllib.parse.urlparse(orig).path)
            if name in available:
                return f'href="resources/{name}"'
            return f'href="{orig}"'

        body_content = re.sub(r'src=["\']([^"\']+)["\']', repl_src, body_content, flags=re.I)
        body_content = re.sub(r'href=["\']([^"\']+)["\']', repl_href, body_content, flags=re.I)

        return f"<!DOCTYPE html><html><head><meta charset='utf-8'>{css_styles}</head><body>{body_content}</body></html>"



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
        # apply initial layout/default columns only the first time and only if user hasn't chosen columns
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
                var actualStepSize = actualColumnWidth + column_gap;
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
                var actualStepSize = actualColumnWidth + column_gap;
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
                column_css = f"column-count: {self.fixed_column_count}; column-gap: {self.column_gap}px;"
            else:
                column_css = f"column-width: {self.actual_column_width}px; column-gap: {self.column_gap}px;"
            js_code = f"""
            (function() {{
                var body = document.body;
                if (body) {{
                    body.style.columnCount = '';
                    body.style.columnWidth = '';
                    body.style.cssText = body.style.cssText.replace(/column-[^;]*;?/g, '');
                    var newStyle = '{column_css}';
                    var styles = newStyle.split(';');
                    for (var i = 0; i < styles.length; i++) {{
                        var style = styles[i].trim();
                        if (style) {{
                            var parts = style.split(':');
                            if (parts.length === 2) {{
                                var prop = parts[0].trim();
                                var val = parts[1].trim();
                                if (prop === 'column-count') body.style.columnCount = val;
                                else if (prop === 'column-width') body.style.columnWidth = val;
                                else if (prop === 'column-gap') body.style.columnGap = val;
                            }}
                        }}
                    }}
                    body.style.height = 'calc(100vh - {self.column_padding * 2}px)';
                    body.style.overflowX = 'auto';
                    body.style.overflowY = 'hidden';
                    setTimeout(function() {{
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        if (currentScroll > 0) {{
                            window.dispatchEvent(new Event('resize'));
                        }}
                    }}, 100);
                }}
            }})();
            """
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
            js_code = """
            (function() {
                return {
                    scrollLeft: window.pageXOffset || document.documentElement.scrollLeft,
                    scrollTop: window.pageYOffset || document.documentElement.scrollTop,
                    scrollWidth: document.documentElement.scrollWidth,
                    clientWidth: document.documentElement.clientWidth
                };
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_pre_resize_scroll_info, None)
        else:
            self._do_resize_reload(0,0)
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
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return []
        chapter = self.chapters[self.current_chapter]
        try:
            with open(chapter['file'], 'r', encoding='utf-8') as f:
                html = f.read()
        except Exception:
            return []
        
        from bs4 import BeautifulSoup
        import re

        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception:
            return []
        
        pairs = []
        processed_ids = set()

        # Process each paragraph/block element to maintain reading order
        for block in soup.find_all(['p', 'div', 'blockquote', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            spans_in_block = []
            for span in block.find_all('span', attrs={'data-tts-id': True}):
                sid = span.get('data-tts-id')
                if sid in processed_ids:
                    continue
                # skip nested spans inside another tts span
                parent = span.parent
                has_tts_parent = False
                while parent and parent != block:
                    if parent.name == 'span' and parent.get('data-tts-id') and parent.get('data-tts-id') != sid:
                        has_tts_parent = True
                        break
                    parent = parent.parent
                if not has_tts_parent:
                    spans_in_block.append(span)
                    processed_ids.add(sid)

            # Merge fragments into complete sentences
            i = 0
            while i < len(spans_in_block):
                current_span = spans_in_block[i]
                sid = current_span.get('data-tts-id')
                text = current_span.get_text(strip=True)

                j = i + 1
                while j < len(spans_in_block):
                    # stop merging if this already looks like end of a sentence
                    if re.search(r'[.!?]["\'”’\)\]]*$', text):
                        break

                    next_span = spans_in_block[j]
                    next_text = next_span.get_text(strip=True)

                    if not next_text:
                        j += 1
                        continue

                    # --- FIXED MERGE RULES ---
                    if (len(text) == 1 and text.isalpha()) or (len(next_text) == 1 and next_text.isalpha()):
                        # join letters directly (avoid "M y")
                        text = text + next_text
                    elif re.fullmatch(r'[.,!?;:—\-…]+', next_text):
                        # attach punctuation directly (avoid "smooth .")
                        text = text + next_text
                    else:
                        # normal join with space
                        if not text.endswith(' '):
                            text = text + ' '
                        text = text + next_text

                    j += 1

                if text and len(text.strip()) > 0:
                    pairs.append((sid, text.strip()))

                i = j if j > i else i + 1

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

