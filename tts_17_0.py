#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js with TTS support using Kokoro

This version removes dependency on ebooklib and beautifulsoup4.
It unpacks the .epub (zip) using Python stdlib (zipfile, xml.etree)
and preserves the existing app behavior with minimal changes.

It also copies local jszip.min.js and epub.min.js into the extracted
resources directory for the webview to use if desired by chapter HTML.
"""
import os, json, tempfile, shutil, re, urllib.parse, signal, sys, math, threading, queue, subprocess, uuid, time, pathlib, hashlib, multiprocessing, zipfile, xml.etree.ElementTree as ET
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango

try:
    import soundfile as sf
    from kokoro_onnx import Kokoro
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False
    Kokoro = None

Adw.init()

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_JSZIP = HERE / "jszip.min.js"
LOCAL_EPUBJS = HERE / "epub.min.js"

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
# EpubViewer (complete)
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
        self.opf_path = None
        self.manifest = {}
        self.spine = []
        
        self.css_content = ""
        
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
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)
        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.main_box)

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
            # fall back for older libadwaita
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

        # resize notifications
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)

        # Add periodic TTS button state update
        GLib.timeout_add(500, self._update_tts_button_states)

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
        # ... Page Up/Down/Home/End handling retained
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
                        var totalGapWidth = (actualColumns - 1) * column_gap;
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

    # -----------------------
    # EPUB handling (no external libs)
    # -----------------------
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
            # cleanup prior temp
            if self.temp_dir and os.path.exists(self.temp_dir):
                try:
                    shutil.rmtree(self.temp_dir)
                except Exception:
                    pass

            # create app cache and temp extraction dir
            app_cache_dir = os.path.expanduser("~/.var/app/io.github.fastrizwaan.tts/cache")
            epub_cache_dir = os.path.join(app_cache_dir, "epub-temp")
            os.makedirs(epub_cache_dir, exist_ok=True)
            self.temp_dir = tempfile.mkdtemp(dir=epub_cache_dir)

            # ensure TTS temp redirect
            tts_temp_dir = os.path.join(self.temp_dir, "tts-lib-temp")
            os.makedirs(tts_temp_dir, exist_ok=True)
            os.environ['TMPDIR'] = tts_temp_dir
            os.environ['TMP'] = tts_temp_dir
            os.environ['TEMP'] = tts_temp_dir

            # unpack epub (zip)
            with zipfile.ZipFile(filepath, 'r') as zf:
                zf.extractall(self.temp_dir)

            # copy local js files into resources for use in generated HTML
            resources_dir = os.path.join(self.temp_dir, 'resources')
            os.makedirs(resources_dir, exist_ok=True)
            try:
                if LOCAL_JSZIP.exists():
                    shutil.copy2(str(LOCAL_JSZIP), os.path.join(resources_dir, LOCAL_JSZIP.name))
                if LOCAL_EPUBJS.exists():
                    shutil.copy2(str(LOCAL_EPUBJS), os.path.join(resources_dir, LOCAL_EPUBJS.name))
            except Exception:
                pass

            # parse container.xml to find package (.opf)
            cont_path = os.path.join(self.temp_dir, 'META-INF', 'container.xml')
            if not os.path.exists(cont_path):
                raise Exception("container.xml not found in EPUB")
            tree = ET.parse(cont_path)
            root = tree.getroot()
            # handle namespace
            ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
            opf_relpath = None
            for rootfile in root.findall('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile'):
                opf_relpath = rootfile.get('full-path')
                if opf_relpath:
                    break
            if not opf_relpath:
                # fallback search
                rf = root.find('.//rootfile')
                if rf is not None:
                    opf_relpath = rf.get('full-path')
            if not opf_relpath:
                raise Exception("OPF path not found in container.xml")
            self.opf_path = os.path.normpath(os.path.join(self.temp_dir, opf_relpath))
            # parse OPF manifest & spine
            self._parse_opf_and_build_lists()
            # load css
            self.extract_css()
            # build chapter files (processed), then load first
            self.extract_chapters()
            if self.chapters:
                self.current_chapter = 0
                self.load_chapter()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def _parse_opf_and_build_lists(self):
        # parse OPF (package) to gather manifest and spine order
        if not self.opf_path or not os.path.exists(self.opf_path):
            raise Exception("OPF not available")
        tree = ET.parse(self.opf_path)
        root = tree.getroot()
        # determine default namespace
        nsmap = {}
        if root.tag.startswith('{'):
            m = re.match(r'\{(.+)\}', root.tag)
            if m:
                nsmap['opf'] = m.group(1)
        # find manifest items
        manifest = {}
        for item in root.findall('.//{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item'):
            item_id = item.get('id')
            href = item.get('href')
            media_type = item.get('media-type')
            manifest[item_id] = {'href': href, 'media_type': media_type}
        # spine order
        spine_list = []
        for itemref in root.findall('.//{http://www.idpf.org/2007/opf}spine/{http://www.idpf.org/2007/opf}itemref'):
            idref = itemref.get('idref')
            if idref:
                spine_list.append(idref)
        # sometimes namespaces differ; fallback generic
        if not manifest:
            for item in root.findall('.//manifest/item'):
                item_id = item.get('id')
                href = item.get('href')
                media_type = item.get('media-type')
                manifest[item_id] = {'href': href, 'media_type': media_type}
        if not spine_list:
            for itemref in root.findall('.//spine/itemref'):
                idref = itemref.get('idref')
                if idref:
                    spine_list.append(idref)
        # map manifest entries to filesystem paths (relative to OPF)
        opf_dir = os.path.dirname(self.opf_path)
        resolved_manifest = {}
        for mid, info in manifest.items():
            href = info.get('href')
            media_type = info.get('media_type')
            if not href:
                continue
            href_decoded = urllib.parse.unquote(href)
            abs_path = os.path.normpath(os.path.join(opf_dir, href_decoded))
            resolved_manifest[mid] = {'path': abs_path, 'media_type': media_type, 'href': href_decoded}
        self.manifest = resolved_manifest
        self.spine = spine_list

    def extract_chapters(self):
        self.chapters = []
        if not self.manifest or not self.spine:
            return
        resources_dir_fs = os.path.join(self.temp_dir, 'resources')
        os.makedirs(resources_dir_fs, exist_ok=True)
        # ensure resource files (images, css) already present from extraction
        for item_id in self.spine:
            mi = self.manifest.get(item_id)
            if not mi:
                continue
            path = mi.get('path')
            media_type = mi.get('media_type') or ''
            if path and os.path.exists(path) and 'application/xhtml+xml' in media_type or path.endswith(('.xhtml', '.html', '.htm')):
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except Exception:
                    try:
                        with open(path, 'r', encoding='latin-1', errors='ignore') as f:
                            content = f.read()
                    except Exception:
                        content = ''
                # write processed content to temp file (so stable behavior remains)
                chapter_file = os.path.join(self.temp_dir, f"{os.path.basename(path)}.processed.html")
                processed_content = self.process_chapter_content(content, path)
                with open(chapter_file, 'w', encoding='utf-8') as f:
                    f.write(processed_content)
                title = self.extract_title(content)
                self.chapters.append({
                    'id': item_id,
                    'title': title,
                    'file': chapter_file,
                    'orig_path': path
                })

    def process_chapter_content(self, content, item_path):
        """
        Inject sentence spans for many block-level tags while preserving inline HTML
        (so anchors/TOC links remain clickable). Wrap stray top-level text nodes into
        <p> so they are processed. Uses stable sha1-based ids for sentences.

        Minimal changes made to support filesystem-based resources (images/css),
        and to include local epub.js/jszip into each chapter's HTML head for convenience.
        """
        # ensure column math is up-to-date
        self.calculate_column_dimensions()
        apply_columns = not self.is_single_column_mode()

        # build body_style same as the rest of the app expects
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

        # Include local jszip/epub.js in case user JS needs them (copied to resources during load)
        resources_dir_rel = 'resources'
        js_includes = ""
        # prefer copied local files
        try:
            if os.path.exists(os.path.join(self.temp_dir, resources_dir_rel, LOCAL_JSZIP.name)):
                js_includes += f'<script src="{resources_dir_rel}/{LOCAL_JSZIP.name}"></script>\n'
            if os.path.exists(os.path.join(self.temp_dir, resources_dir_rel, LOCAL_EPUBJS.name)):
                js_includes += f'<script src="{resources_dir_rel}/{LOCAL_EPUBJS.name}"></script>\n'
        except Exception:
            pass

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

        # extract body content
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1) if body_match else content

        # remove head/meta/style blocks (we don't want external styles interfering)
        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)

        # Wrap bare top-level text nodes into <p> so they get processed
        try:
            body_content = re.sub(
                r'(?<=^|>)(\s*[^<\s][^<]*?)(?=<|$)',
                lambda m: '<p>' + m.group(1).strip() + '</p>',
                body_content,
                flags=re.DOTALL
            )
        except Exception:
            pass

        # resource rewrites (images/css) - make relative paths point into extracted tree
        resources_dir_fs = os.path.join(self.temp_dir, 'resources')
        available = set(os.listdir(resources_dir_fs)) if os.path.isdir(resources_dir_fs) else set()
        def repl_src(m):
            orig = m.group(1)
            name = os.path.basename(orig)
            if name in available:
                return f'src="{resources_dir_rel}/{name}"'
            # if path is relative inside EPUB, rewrite to absolute file path to extracted resource
            candidate = os.path.normpath(os.path.join(os.path.dirname(item_path), orig))
            if os.path.exists(candidate):
                rel = os.path.relpath(candidate, self.temp_dir)
                return f'src="{rel}"'
            return f'src="{orig}"'
        body_content = re.sub(r'src=["\']([^"\']+)["\']', repl_src, body_content, flags=re.IGNORECASE)
        def repl_href(m):
            orig = m.group(1)
            name = os.path.basename(orig)
            if name in available:
                return f'href="{resources_dir_rel}/{name}"'
            candidate = os.path.normpath(os.path.join(os.path.dirname(item_path), orig))
            if os.path.exists(candidate):
                rel = os.path.relpath(candidate, self.temp_dir)
                return f'href="{rel}"'
            return f'href="{orig}"'
        body_content = re.sub(r'href=["\']([^"\']+)["\']', repl_href, body_content, flags=re.IGNORECASE)

        # TARGET_TAGS expanded to catch top-level containers and labels/anchors
        TARGET_TAGS = [
            'p','div','span','section','article','li','label',
            'blockquote','figcaption','caption','dt','dd',
            'td','th','summary','pre',
            'h1','h2','h3','h4','h5','h6'
        ]

        def make_replacer(tag):
            pattern = re.compile(rf'<{tag}([^>]*)>(.*?)</{tag}>', flags=re.DOTALL | re.IGNORECASE)

            def find_html_span_for_plain_range(html, plain_start, plain_len):
                # Map a plain-text character range to HTML indices (skip tags)
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

                # plain text (for sentence splitting)
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
                    # find next occurrence in plain text after cur_plain_pos
                    next_pos = plain.find(s_clean, cur_plain_pos)
                    if next_pos == -1:
                        next_pos = plain.find(s_clean)
                        if next_pos == -1:
                            cur_plain_pos += plen
                            continue

                    span = find_html_span_for_plain_range(inner, next_pos, plen)
                    if not span or span[0] is None or span[1] is None:
                        # fallback: escape and replace first visible occurrence in out_html
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

        # Apply replacers in order
        for tag in TARGET_TAGS:
            pat, repl = make_replacer(tag)
            body_content = pat.sub(repl, body_content)

        # final assembled HTML
        assembled = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{css_styles}{js_includes}</head><body>{body_content}{script}</body></html>"""
        return assembled


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

    # Navigation / JS integration unchanged from earlier (kept to preserve behavior)
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
        # We intentionally regenerate chapters to update column layout, but because IDs are stable they persist.
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
        # Reapply highlight (stable ids)
        if self.tts:
            GLib.timeout_add(200, lambda: self.tts.reapply_highlight_after_reload())
        return False

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "_OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.present()

    def cleanup(self):
        self.css_content = ""
        if self.tts:
            try:
                self.tts.stop()
            except Exception:
                pass
        # Clean up the entire temp directory structure when app exits
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                # Do not aggressively delete to avoid surprises in debugging; print path for manual removal.
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
                    
    def extract_css(self):
        self.css_content = ""
        for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
            try:
                self.css_content += item.get_content().decode('utf-8') + "\n"
            except Exception:
                pass
    # --- TTS controls & helpers ---
    def _collect_sentences_for_current_chapter(self):
        """
        Collect (sid, text) pairs from chapter HTML by matching data-tts-id spans.
        Because IDs are stable (sha1 of text), they survive regeneration.
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
        # Stop must kill synths and clear queues & files
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
        # actions
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
            # Stop TTS first to terminate all subprocesses
            if window.tts:
                try:
                    window.tts.stop()
                    import time
                    time.sleep(0.5)
                except Exception as e:
                    print(f"Error stopping TTS: {e}")
            # Then do general cleanup
            try:
                window.cleanup()
            except Exception as e:
                print(f"Error in cleanup: {e}")
        # Now it's safe to exit
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    try:
        app.run(sys.argv)
    finally:
        w = app.get_active_window()
        if w:
            # Stop TTS processes before final cleanup
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

