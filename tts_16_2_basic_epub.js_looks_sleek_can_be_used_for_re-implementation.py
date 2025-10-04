#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js with TTS support using Kokoro
"""
import os, json, tempfile, shutil, re, signal, sys, threading, queue, subprocess, uuid, time, pathlib, hashlib, multiprocessing, base64
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango

import soundfile as sf
try:
    from kokoro_onnx import Kokoro
except Exception:
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

    # UI JS helpers - FIXED to work with iframe
    def _run_js_highlight(self, sid):
        webview = self.get_webview()
        if not webview:
            return
        js = f"""
        (function() {{
            try {{
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) return;
                var doc = iframe.contentDocument;
                var el = doc.querySelector('[data-tts-id="{sid}"]');
                if (!el) return;
                doc.querySelectorAll('.tts-highlight').forEach(function(p){{ p.classList.remove('tts-highlight'); }});
                el.classList.add('tts-highlight');
                try {{
                    el.scrollIntoView({{ behavior: 'smooth', block: 'center', inline: 'nearest' }});
                }} catch(e) {{
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
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) return;
                var doc = iframe.contentDocument;
                var el = doc.querySelector('[data-tts-id="{sid}"]');
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
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) return;
                var doc = iframe.contentDocument;
                doc.querySelectorAll('.tts-highlight').forEach(function(p){ p.classList.remove('tts-highlight'); });
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
        self.current_book_path = None
        self.current_chapter_index = 0
        self.total_chapters = 0
        self.temp_dir = None

        # column settings (for epub.js)
        self.column_width = 800  # Width per column in epub.js
        self.column_count = 1    # Number of columns (1 or 2)

        # tts manager
        self.tts = None

        # setup UI
        self.setup_ui()
        self.setup_navigation()

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

        # Add column settings menu
        columns_menu = Gio.Menu()
        columns_menu.append("1 Column", "app.set-columns(1)")
        columns_menu.append("2 Columns", "app.set-columns(2)")
        menu.append_submenu("Layout", columns_menu)
        
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
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.set_vexpand(True)
        self.main_box.append(self.scrolled_window)

        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        settings = self.webview.get_settings()
        settings.set_enable_smooth_scrolling(True)
        settings.set_enable_javascript(True)

        self.webview.connect("load-changed", self.on_webview_load_changed)
        
        # Register message handler for epub.js communication
        content_manager = self.webview.get_user_content_manager()
        content_manager.register_script_message_handler("epubHandler")
        content_manager.connect("script-message-received::epubHandler", self.on_epub_message)
        
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
            self.tts_play_btn.set_sensitive(bool(self.current_book_path))
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
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book_path:
            return False
        
        if keyval == 65361 or keyval == 65365:  # Left / PageUp
            self.on_prev_page(None)
            return True
        elif keyval == 65363 or keyval == 65366:  # Right / PageDown
            self.on_next_page(None)
            return True
        elif keyval == 65360:  # Home
            self.webview.evaluate_javascript("rendition.display(0);", -1, None, None, None, None, None)
            return True
        elif keyval == 65367:  # End
            self.webview.evaluate_javascript("rendition.display(book.spine.length - 1);", -1, None, None, None, None, None)
            return True
        return False

    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            GLib.timeout_add(300, self._after_load_update)

    def _after_load_update(self):
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

    def on_epub_message(self, content_manager, js_result):
        """Handle messages from epub.js in the WebView"""
        try:
            # Handle different WebKit versions
            try:
                # Try newer API first
                js_value = js_result.get_js_value()
                msg = js_value.to_string()
            except AttributeError:
                # Fall back to older API
                msg = js_result.to_string()
            
            data = json.loads(msg)
            
            if data.get("type") == "chapterLoaded":
                self.current_chapter_index = data.get("index", 0)
                self.total_chapters = data.get("total", 0)
                chapter_title = data.get("title", "Untitled")
                self.chapter_label.set_text(f"Chapter {self.current_chapter_index + 1} of {self.total_chapters}: {chapter_title}")
                self.prev_chapter_btn.set_sensitive(self.current_chapter_index > 0)
                self.next_chapter_btn.set_sensitive(self.current_chapter_index < self.total_chapters - 1)
                self.prev_page_btn.set_sensitive(True)
                self.next_page_btn.set_sensitive(True)
                
                # After chapter loads, force re-injection of TTS spans
                GLib.timeout_add(500, self._reinject_tts_spans)
                
        except Exception as e:
            print(f"Error handling epub message: {e}")

    def _reinject_tts_spans(self):
        """Force re-injection of TTS spans after chapter load"""
        js_code = """
        (function() {
            try {
                var iframe = document.querySelector('iframe');
                if (iframe && iframe.contentDocument) {
                    // Wait a bit for iframe to fully render
                    setTimeout(function() {
                        var section = {document: iframe.contentDocument};
                        injectTTSSpans(section);
                    }, 200);
                }
            } catch(e) {
                console.error('Error reinjecting TTS spans:', e);
            }
        })();
        """
        self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        return False

    # File open / epub handling
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
            
            self.current_book_path = filepath
            self.load_epub_viewer()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def set_column_count(self, count):
        """Update column layout in epub.js"""
        self.column_count = count
        if self.current_book_path:
            # Update the rendition with new settings
            spread = "none" if count == 1 else "auto"
            js_code = f"""
            (function() {{
                try {{
                    if (typeof rendition !== 'undefined') {{
                        rendition.spread('{spread}');
                        rendition.resize();
                    }}
                }} catch(e) {{
                    console.error('Error setting columns:', e);
                }}
            }})();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)

    def load_epub_viewer(self):
        """Load the epub.js viewer with the current EPUB file"""
        if not self.current_book_path:
            return
        
        # Read the EPUB file as base64
        try:
            with open(self.current_book_path, 'rb') as f:
                epub_data = f.read()
            epub_base64 = base64.b64encode(epub_data).decode('utf-8')
        except Exception as e:
            self.show_error(f"Error reading EPUB: {str(e)}")
            return
        
        # Read jszip and epub.js
        try:
            with open(LOCAL_JSZIP, 'r', encoding='utf-8') as f:
                jszip_code = f.read()
            with open(LOCAL_EPUBJS, 'r', encoding='utf-8') as f:
                epubjs_code = f.read()
        except Exception as e:
            self.show_error(f"Error loading epub.js libraries: {str(e)}")
            return
        
        # Determine spread mode based on column count
        spread_mode = "none" if self.column_count == 1 else "auto"
        
        # Create HTML viewer
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; width: 100%; overflow: hidden; }}
        #viewer {{
            width: 100%;
            height: 100%;
            background-color: #fafafa;
        }}
        iframe {{
            border: none;
        }}
        .tts-highlight {{
            background: rgba(255, 215, 0, 0.35) !important;
            box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.35) !important;
        }}
        @media (prefers-color-scheme: dark) {{
            #viewer {{ background-color: #242424; }}
            .tts-highlight {{
                background: rgba(0, 127, 0, 0.75) !important;
                box-shadow: 0 0 0 2px rgba(0, 127, 0, 0.75) !important;
            }}
        }}
    </style>
</head>
<body>
    <div id="viewer"></div>
    <script>{jszip_code}</script>
    <script>{epubjs_code}</script>
    <script>
        // Decode base64 EPUB data
        var epubData = atob("{epub_base64}");
        var epubArray = new Uint8Array(epubData.length);
        for (var i = 0; i < epubData.length; i++) {{
            epubArray[i] = epubData.charCodeAt(i);
        }}
        
        // Create book from array buffer
        var book = ePub(epubArray.buffer);
        var rendition = book.renderTo("viewer", {{
            width: "100%",
            height: "100%",
            flow: "paginated",
            spread: "{spread_mode}"
        }});
        
        // Display first chapter
        var displayed = rendition.display();
        
        // Track chapter changes
        rendition.on('relocated', function(location) {{
            var currentChapter = book.spine.get(location.start.href);
            if (currentChapter) {{
                var chapterIndex = book.spine.spineItems.indexOf(currentChapter);
                var chapterTitle = currentChapter.navItem ? currentChapter.navItem.label : "Untitled";
                
                // Send message to Python
                window.webkit.messageHandlers.epubHandler.postMessage(JSON.stringify({{
                    type: "chapterLoaded",
                    index: chapterIndex,
                    total: book.spine.length,
                    title: chapterTitle
                }}));
            }}
        }});
        
        // Add TTS sentence wrapping after content loads
        rendition.on('rendered', function(section) {{
            setTimeout(function() {{
                injectTTSSpans(section);
            }}, 100);
        }});
        
        function injectTTSSpans(section) {{
            try {{
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) {{
                    console.log('Iframe not ready yet');
                    return;
                }}
                
                var doc = iframe.contentDocument;
                if (!doc) return;
                
                var TARGET_TAGS = ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'figcaption', 'caption', 'dt', 'dd', 'td', 'th'];
                
                TARGET_TAGS.forEach(function(tag) {{
                    var elements = doc.getElementsByTagName(tag);
                    var elemArray = Array.from(elements);
                    
                    elemArray.forEach(function(el) {{
                        if (el.querySelector('[data-tts-id]')) return;
                        if (el.closest('[data-tts-id]')) return;
                        
                        var text = getDirectTextContent(el);
                        if (!text.trim()) return;
                        
                        var sentences = splitSentences(text);
                        if (sentences.length === 0) return;
                        
                        wrapSentencesInElement(el, sentences);
                    }});
                }});
            }} catch(e) {{
                console.error('Error in injectTTSSpans:', e);
            }}
        }}
        
        function getDirectTextContent(el) {{
            var text = '';
            for (var i = 0; i < el.childNodes.length; i++) {{
                var node = el.childNodes[i];
                if (node.nodeType === Node.TEXT_NODE) {{
                    text += node.textContent;
                }} else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'SCRIPT' && node.tagName !== 'STYLE') {{
                    text += getDirectTextContent(node);
                }}
            }}
            return text;
        }}
        
        function wrapSentencesInElement(el, sentences) {{
            var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
            var textNodes = [];
            var node;
            while (node = walker.nextNode()) {{
                if (node.parentNode.tagName !== 'SCRIPT' && node.parentNode.tagName !== 'STYLE') {{
                    textNodes.push(node);
                }}
            }}
            
            sentences.forEach(function(sentence) {{
                if (!sentence.trim()) return;
                var sid = stableIdForText(sentence.trim());
                
                for (var i = 0; i < textNodes.length; i++) {{
                    var textNode = textNodes[i];
                    var content = textNode.textContent;
                    var idx = content.indexOf(sentence);
                    
                    if (idx !== -1) {{
                        var before = content.substring(0, idx);
                        var match = content.substring(idx, idx + sentence.length);
                        var after = content.substring(idx + sentence.length);
                        
                        var parent = textNode.parentNode;
                        var span = document.createElement('span');
                        span.setAttribute('data-tts-id', sid);
                        span.textContent = match;
                        
                        if (before) parent.insertBefore(document.createTextNode(before), textNode);
                        parent.insertBefore(span, textNode);
                        if (after) {{
                            var afterNode = document.createTextNode(after);
                            parent.insertBefore(afterNode, textNode);
                            textNodes[i] = afterNode;
                        }}
                        parent.removeChild(textNode);
                        break;
                    }}
                }}
            }});
        }}
        
        function splitSentences(text) {{
            text = text.replace(/\\r/g, ' ').replace(/\\n+/g, ' ');
            var sentences = [];
            var regex = /[^.!?]+[.!?]+/g;
            var match;
            while ((match = regex.exec(text)) !== null) {{
                sentences.push(match[0].trim());
            }}
            if (sentences.length === 0 && text.trim()) {{
                sentences.push(text.trim());
            }}
            return sentences;
        }}
        
        function stableIdForText(text) {{
            var hash = 0;
            for (var i = 0; i < text.length; i++) {{
                var char = text.charCodeAt(i);
                hash = ((hash << 5) - hash) + char;
                hash = hash & hash;
            }}
            return Math.abs(hash).toString(16).substring(0, 12);
        }}
        
        // Navigation functions
        window.prevPage = function() {{
            rendition.prev();
        }};
        
        window.nextPage = function() {{
            rendition.next();
        }};
        
        window.prevChapter = function() {{
            var currentLocation = rendition.currentLocation();
            if (currentLocation) {{
                var currentChapter = book.spine.get(currentLocation.start.href);
                if (currentChapter) {{
                    var chapterIndex = book.spine.spineItems.indexOf(currentChapter);
                    if (chapterIndex > 0) {{
                        rendition.display(chapterIndex - 1);
                    }}
                }}
            }}
        }};
        
        window.nextChapter = function() {{
            var currentLocation = rendition.currentLocation();
            if (currentLocation) {{
                var currentChapter = book.spine.get(currentLocation.start.href);
                if (currentChapter) {{
                    var chapterIndex = book.spine.spineItems.indexOf(currentChapter);
                    if (chapterIndex < book.spine.length - 1) {{
                        rendition.display(chapterIndex + 1);
                    }}
                }}
            }}
        }};
        
        // Get current chapter sentences for TTS
        window.getCurrentChapterSentences = function() {{
            var sentences = [];
            try {{
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) {{
                    console.error('Could not find iframe');
                    return sentences;
                }}
                
                var doc = iframe.contentDocument;
                var spans = doc.querySelectorAll('[data-tts-id]');
                spans.forEach(function(span) {{
                    var sid = span.getAttribute('data-tts-id');
                    var text = span.textContent.trim();
                    if (sid && text) {{
                        sentences.push({{sid: sid, text: text}});
                    }}
                }});
            }} catch(e) {{
                console.error('Error in getCurrentChapterSentences:', e);
            }}
            
            return sentences;
        }};
    </script>
</body>
</html>"""
        
        # Load the HTML content
        self.webview.load_html(html_content, "file:///")

    def on_prev_chapter(self, button):
        if self.current_book_path:
            self.webview.evaluate_javascript("prevChapter();", -1, None, None, None, None, None)

    def on_next_chapter(self, button):
        if self.current_book_path:
            self.webview.evaluate_javascript("nextChapter();", -1, None, None, None, None, None)

    def on_prev_page(self, button):
        if self.current_book_path:
            self.webview.evaluate_javascript("prevPage();", -1, None, None, None, None, None)

    def on_next_page(self, button):
        if self.current_book_path:
            self.webview.evaluate_javascript("nextPage();", -1, None, None, None, None, None)

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
        # Clean up the entire temp directory structure when app exits
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

    # --- TTS controls & helpers ---
    def _collect_sentences_for_current_chapter(self):
        """
        Collect (sid, text) pairs from current chapter via JavaScript
        """
        js_code = """
        (function() {
            try {
                var sentences = getCurrentChapterSentences();
                return JSON.stringify(sentences);
            } catch(e) {
                console.error('Error getting sentences:', e);
                return JSON.stringify([]);
            }
        })();
        """
        
        self._tts_sentences = []
        self._tts_sentences_ready = False
        
        def on_sentences_result(webview, result, user_data):
            try:
                try:
                    js_value = webview.evaluate_javascript_finish(result)
                    if js_value:
                        json_str = js_value.to_string()
                        sentences_data = json.loads(json_str)
                        self._tts_sentences = [(s['sid'], s['text']) for s in sentences_data]
                except AttributeError:
                    js_result = webview.evaluate_javascript_finish(result)
                    if js_result:
                        json_str = js_result.get_string_value()
                        sentences_data = json.loads(json_str)
                        self._tts_sentences = [(s['sid'], s['text']) for s in sentences_data]
            except Exception as e:
                print(f"Error getting sentences: {e}")
                self._tts_sentences = []
            finally:
                self._tts_sentences_ready = True
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, on_sentences_result, None)
        
        # Wait for the result
        timeout = 0
        while not self._tts_sentences_ready and timeout < 200:
            GLib.MainContext.default().iteration(False)
            timeout += 1
            time.sleep(0.01)
        
        return self._tts_sentences

    def on_tts_play(self, button):
        if not self.current_book_path:
            return
        sentences = self._collect_sentences_for_current_chapter()
        if not sentences:
            self.show_error("No text found in current chapter. Please navigate through the book first.")
            return
        chap_id = str(self.current_chapter_index)
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
        
        # Add actions for column settings
        for i in [1, 2]:
            act = Gio.SimpleAction.new("set-columns", GLib.VariantType.new("i"))
            act.connect("activate", self.on_set_columns)
            self.add_action(act)
            
        window.present()

    def on_set_columns(self, action, parameter):
        count = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_count(count)

def main():
    app = EpubViewerApp()
    def cleanup_handler(signum, frame):
        print("Received signal, cleaning up...")
        window = app.get_active_window()
        if window:
            if window.tts:
                try:
                    window.tts.stop()
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
                    time.sleep(0.5)
                except Exception:
                    pass
            w.cleanup()

if __name__ == "__main__":
    main()
