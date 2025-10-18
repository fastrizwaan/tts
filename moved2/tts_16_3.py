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
        self._paused = threading.Event()
        self._current_play_proc = None
        self._current_synth_proc = None
        self._threads_running = False
        self.current_chapter_id = None
        self.current_highlight_id = None
        self.played_files = []
        self.max_cache_files = 5
        self.current_playing_file = None

    def start(self, chapter_id, sentences):
        if self.current_chapter_id != chapter_id:
            self.stop()
        self.current_chapter_id = chapter_id
        os.makedirs(self.tts_dir, exist_ok=True)
        self._stop_event.clear()
        for sid, text in sentences:
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
        self._stop_event.set()
        try:
            while not self.synth_queue.empty():
                self.synth_queue.get_nowait()
        except Exception:
            pass
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
        if self._current_play_proc:
            try:
                self._current_play_proc.kill()
            except Exception:
                pass
            self._current_play_proc = None
        self._run_js_clear_highlight()
        self._threads_running = False
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
        self._clean_cache_files()
        try:
            if os.path.exists(self.tts_dir):
                for fn in os.listdir(self.tts_dir):
                    if fn.endswith('.wav'):
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
        while not self._stop_event.is_set():
            try:
                sid, text = self.synth_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                else:
                    continue
            outname = f"{sid}_{uuid.uuid4().hex[:8]}.wav"
            outpath = os.path.join(self.tts_dir, outname)
            proc = multiprocessing.Process(target=synth_single_process, args=(self.kokoro_model_path, self.voices_bin_path, text, outpath, self.voice, self.speed, self.lang))
            proc.start()
            self._current_synth_proc = proc
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
            if os.path.exists(outpath) and not self._stop_event.is_set():
                self.play_queue.put((sid, outpath))
            else:
                try:
                    if os.path.exists(outpath):
                        os.remove(outpath)
                except Exception:
                    pass
        return

    def _manage_file_cache(self, new_file_path, sid):
        self.played_files.append((sid, new_file_path))
        while len(self.played_files) > self.max_cache_files:
            old_sid, old_path = self.played_files.pop(0)
            try:
                if os.path.exists(old_path) and old_path != self.current_playing_file:
                    os.remove(old_path)
            except Exception:
                pass

    def _clean_cache_files(self):
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
            
            self.current_playing_file = wavpath
            self.current_highlight_id = sid
            self._run_js_highlight(sid)
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
                if not self._paused.is_set():
                    self._run_js_unhighlight(sid)
                    self.current_highlight_id = None
                
                if not self._stop_event.is_set():
                    self._manage_file_cache(wavpath, sid)
                else:
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
        return self._threads_running and not self._stop_event.is_set()

    def is_paused(self):
        return self._paused.is_set() and not self._stop_event.is_set()

    def _run_js_highlight(self, sid):
        webview = self.get_webview()
        if not webview:
            return
        js = f"""
        (function() {{
            try {{
                var el = document.querySelector('[data-tts-id="{sid}"]');
                if (!el) return;
                document.querySelectorAll('.tts-highlight').forEach(function(p){{ p.classList.remove('tts-highlight'); }});
                el.classList.add('tts-highlight');
                try {{
                    var rect = el.getBoundingClientRect();
                    var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                    var scrollWidth = document.documentElement.scrollWidth;
                    var clientWidth = document.documentElement.clientWidth;
                    
                    if (scrollWidth > clientWidth) {{
                        var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                        if (rect.left < 0 || rect.right > viewportWidth) {{
                            var targetScroll = currentScroll + rect.left - 20;
                            window.scrollTo({{ left: Math.max(0, targetScroll), behavior: 'smooth' }});
                        }}
                    }} else {{
                        el.scrollIntoView({{ behavior: 'smooth', block: 'center', inline: 'nearest' }});
                    }}
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
        if self.current_highlight_id:
            self._run_js_highlight(self.current_highlight_id)


# -----------------------
# EpubViewer
# -----------------------
class EpubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)

        self.current_book_path = None
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

        self.tts = None
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
        for w in (100, 150, 200, 250, 300, 350, 400, 450, 500):
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

        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)

        GLib.timeout_add(500, self._update_tts_button_states)

    def _update_tts_button_states(self):
        if not self.tts:
            return True
        
        is_playing = self.tts.is_playing()
        is_paused = self.tts.is_paused()
        
        if not is_playing and not is_paused:
            self.tts_play_btn.set_sensitive(bool(self.current_book_path and self.chapters))
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
        if self.current_book_path:
            self.calculate_column_dimensions()
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
        if self.current_book_path:
            self.calculate_column_dimensions()
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
        try:
            if self.temp_dir and self.tts is None:
                kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                self.tts = TTSManager(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
        except Exception as e:
            print("TTS init error:", e)
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
        if not self.current_book_path:
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
        if not self.current_book_path:
            return False
        if self.is_single_column_mode():
            return False
        
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
        if not self.current_book_path or self.is_single_column_mode():
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
            app_cache_dir = os.path.expanduser("~/.var/app/io.github.fastrizwaan.tts/cache")
            epub_cache_dir = os.path.join(app_cache_dir, "epub-temp")
            os.makedirs(epub_cache_dir, exist_ok=True)
            self.temp_dir = tempfile.mkdtemp(dir=epub_cache_dir)
            
            tts_temp_dir = os.path.join(self.temp_dir, "tts-lib-temp")
            os.makedirs(tts_temp_dir, exist_ok=True)
            os.environ['TMPDIR'] = tts_temp_dir
            os.environ['TMP'] = tts_temp_dir
            os.environ['TEMP'] = tts_temp_dir
            
            self.current_book_path = filepath
            self.extract_epub()
            if self.chapters:
                self.current_chapter = 0
                self.load_chapter()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def extract_epub(self):
        """Extract EPUB using epub.js"""
        self.chapters = []
        if not self.current_book_path:
            return
        
        try:
            with open(self.current_book_path, 'rb') as f:
                epub_data = f.read()
            epub_base64 = base64.b64encode(epub_data).decode('utf-8')
        except Exception as e:
            print(f"Error reading EPUB: {e}")
            return
        
        try:
            with open(LOCAL_JSZIP, 'r', encoding='utf-8') as f:
                jszip_code = f.read()
            with open(LOCAL_EPUBJS, 'r', encoding='utf-8') as f:
                epubjs_code = f.read()
        except Exception as e:
            print(f"Error loading libraries: {e}")
            return
        
        # Use epub.js to extract chapter HTML
        extraction_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<script>{jszip_code}</script>
<script>{epubjs_code}</script>
<script>
var epubData = atob("{epub_base64}");
var epubArray = new Uint8Array(epubData.length);
for (var i = 0; i < epubData.length; i++) {{
    epubArray[i] = epubData.charCodeAt(i);
}}
var book = ePub(epubArray.buffer);
book.ready.then(function() {{
    return book.spine.each(function(item, index) {{
        return item.load(book.load.bind(book)).then(function(doc) {{
            var content = new XMLSerializer().serializeToString(doc);
            var title = item.navItem ? item.navItem.label : 'Chapter ' + (index + 1);
            var data = {{
                index: index,
                id: item.idref ||'chapter_' + index,
                title: title,
                content: content
            }};
            window.webkit.messageHandlers.epubExtractor.postMessage(JSON.stringify(data));
        }});
    }});
}}).then(function() {{
    window.webkit.messageHandlers.epubExtractor.postMessage(JSON.stringify({{done: true}}));
}});
</script>
</body></html>"""
        
        # Create temporary extraction webview
        extraction_view = WebKit.WebView()
        extraction_manager = extraction_view.get_user_content_manager()
        extraction_manager.register_script_message_handler("epubExtractor")
        
        chapters_data = []
        extraction_done = [False]
        
        def on_extract_message(manager, js_result):
            try:
                try:
                    js_value = js_result.get_js_value()
                    msg = js_value.to_string()
                except AttributeError:
                    msg = js_result.to_string()
                
                data = json.loads(msg)
                if data.get('done'):
                    extraction_done[0] = True
                else:
                    chapters_data.append(data)
            except Exception as e:
                print(f"Extraction error: {e}")
        
        extraction_manager.connect("script-message-received::epubExtractor", on_extract_message)
        extraction_view.load_html(extraction_html, "file:///")
        
        # Wait for extraction
        timeout = 0
        while not extraction_done[0] and timeout < 1000:
            GLib.MainContext.default().iteration(False)
            timeout += 1
            time.sleep(0.01)
        
        # Process extracted chapters
        for chapter_data in sorted(chapters_data, key=lambda x: x['index']):
            chapter_file = os.path.join(self.temp_dir, f"chapter_{chapter_data['index']}.html")
            processed_content = self.process_chapter_content(chapter_data['content'], chapter_data)
            with open(chapter_file, 'w', encoding='utf-8') as f:
                f.write(processed_content)
            self.chapters.append({
                'id': chapter_data['id'],
                'title': chapter_data['title'],
                'file': chapter_file
            })

    def process_chapter_content(self, content, chapter_data):
        """Process and inject TTS spans into chapter HTML"""
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

        # Extract body content
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1) if body_match else content

        # Remove head/meta/style
        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)

        # Wrap bare text in paragraphs
        try:
            body_content = re.sub(
                r'(?<=^|>)(\s*[^<\s][^<]*?)(?=<|$)',
                lambda m: '<p>' + m.group(1).strip() + '</p>',
                body_content,
                flags=re.DOTALL
            )
        except Exception:
            pass

        # Inject TTS spans
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

        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{css_styles}</head><body>{body_content}</body></html>"""

    def load_chapter(self):
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return
        chapter = self.chapters[self.current_chapter]
        file_uri = GLib.filename_to_uri(chapter['file'])
        self.webview.load_uri(file_uri)
        chapter_info = f"Chapter {self.current_chapter + 1} of {len(self.chapters)}: {chapter['title']}"
        self.chapter_label.set_text(chapter_info)

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
        if self.current_book_path and self.chapters:
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)
        GLib.timeout_add(100, self._delayed_navigation_update)

    def _delayed_navigation_update(self):
        self._refresh_buttons_based_on_adjustment()
        self.update_page_info()
        return False

    def _refresh_buttons_based_on_adjustment(self):
        if not self.h_adjustment or not self.current_book_path:
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
        if not self.current_book_path:
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
        if not self.current_book_path:
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
        self.update_page_info()
        return False

    def update_page_info(self):
        if not self.current_book_path:
            self.page_info.set_text("--/--")
            self.prev_page_btn.set_sensitive(False)
            self.next_page_btn.set_sensitive(False)
            return
        self.page_info.set_text("Page")
        if self.current_book_path and self.chapters:
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)

    def on_window_resize(self, *args):
        self.calculate_column_dimensions()
        if self.current_book_path and self.chapters:
            if hasattr(self, 'resize_timeout_id') and self.resize_timeout_id:
                GLib.source_remove(self.resize_timeout_id)
            self.resize_timeout_id = GLib.timeout_add(250, self._delayed_resize_reload)

    def _delayed_resize_reload(self):
        self.resize_timeout_id = None
        if self.current_book_path:
            self.calculate_column_dimensions()
            self.load_chapter()
            GLib.timeout_add(600, self.update_navigation)
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
        """Collect (sid, text) pairs from chapter HTML"""
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
        if not self.current_book_path or not self.chapters:
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
        for w in (100, 150, 200, 250, 300, 350, 400, 450, 500):
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
