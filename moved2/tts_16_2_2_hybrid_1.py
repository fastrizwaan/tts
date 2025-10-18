#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js with TTS support using Kokoro
Minimal integration of TTSEngine and related methods. Minimal other changes.
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

# optional libs
try:
    import soundfile as sf
except Exception:
    sf = None

try:
    from kokoro_onnx import Kokoro  # may be None
except Exception:
    Kokoro = None

# try gstreamer at runtime inside TTSEngine
Adw.init()

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_JSZIP = HERE / "jszip.min.js"
LOCAL_EPUBJS = HERE / "epub.min.js"

# --- Utilities ---
_s_re_split = re.compile(r'(?<=[.!?])\s+|\n+')
def split_sentences(text):
    return [p.strip() for p in _s_re_split.split(text) if p and p.strip()]

def stable_id_for_text(text):
    h = hashlib.sha1(text.encode('utf-8')).hexdigest()
    return h[:12]

# subprocess worker used by multiprocessing
def synth_single_process(model_path, voices_path, text, outpath, voice, speed, lang):
    try:
        from kokoro_onnx import Kokoro
    except Exception as e:
        print("synth_single_process: Kokoro import failed:", e, file=sys.stderr)
        return 2
    try:
        kokoro = Kokoro(model_path, voices_path)
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
        import soundfile as sf_local
        sf_local.write(outpath, samples, sample_rate)
        return 0
    except Exception as e:
        print("synth_single_process error:", e, file=sys.stderr)
        return 3

# --- TTS Engine ---
class TTSEngine:
    """
    Minimal TTS engine: fetches per-sentence spans from the webview,
    synthesizes sequentially (multiprocessing) and plays via GStreamer.
    Provides: play(), pause(), resume(), stop(), is_playing(), is_paused(), reapply_highlight_after_reload()
    """

    def __init__(self, webview_getter, base_temp_dir, kokoro_model_path=None, voices_bin_path=None, voice='af_sarah', speed=1.0, lang='en-us'):
        self._get_webview = webview_getter  # callable returns WebKit.WebView
        self.base_temp_dir = base_temp_dir
        self.kokoro_model_path = kokoro_model_path
        self.voices_bin_path = voices_bin_path
        self.voice = voice
        self.speed = speed
        self.lang = lang

        self._play_thread = None
        self._control_lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._stop_event = threading.Event()
        self._is_playing = False
        self._is_paused = False

        self._current_sentences = []  # list of dict {sid,text}
        self._current_index = 0
        self._current_sid = None

        # make tts subdir
        self.tts_dir = os.path.join(self.base_temp_dir, "tts")
        os.makedirs(self.tts_dir, exist_ok=True)

        # gstreamer init lazily
        self._gst_inited = False
        self.player = None
        self._player_lock = threading.Lock()

    def _ensure_gst(self):
        if self._gst_inited:
            return
        try:
            import gi as _gi
            _gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self.Gst = Gst
            self.player = Gst.ElementFactory.make("playbin", "player")
            bus = self.player.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_gst_message)
            self._gst_inited = True
        except Exception as e:
            print("GStreamer init failed:", e)
            self._gst_inited = False

    def _on_gst_message(self, bus, message):
        t = message.type
        if t == self.Gst.MessageType.EOS:
            with self._player_lock:
                try:
                    self.player.set_state(self.Gst.State.NULL)
                except Exception:
                    pass
            # EOS handled by playback loop
        elif t == self.Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print("[TTS][Gst] Error:", err, debug)
            with self._player_lock:
                try:
                    self.player.set_state(self.Gst.State.NULL)
                except Exception:
                    pass

    def is_playing(self):
        return self._is_playing

    def is_paused(self):
        return self._is_paused

    def _evaluate_js_sync(self, js_code, timeout=2.0):
        """
        Evaluate JavaScript and return string result if possible.
        Uses callback-based evaluate_javascript and waits on condition.
        """
        view = self._get_webview()
        if not view:
            return None

        result_container = {"result": None, "done": False}

        def cb(view_obj, res, user_data):
            try:
                try:
                    val = view_obj.evaluate_javascript_finish(res)
                except AttributeError:
                    # older API returns a WebKitJavascriptResult directly as res
                    val = res
                # val may have get_js_value()
                s = None
                try:
                    jsval = val.get_js_value()
                    s = jsval.to_string()
                except Exception:
                    try:
                        s = val.to_string()
                    except Exception:
                        s = None
                result_container["result"] = s
            except Exception as e:
                result_container["result"] = None
            finally:
                result_container["done"] = True

        try:
            # newer API: evaluate_javascript(js, cancellable, callback, user_data)
            view.evaluate_javascript(js_code, None, cb, None)
        except TypeError:
            try:
                # alternate signature
                view.evaluate_javascript(js_code, -1, None, cb, None)
            except Exception:
                # fallback synchronous attempt (may not work)
                try:
                    val = view.run_javascript(js_code, None)
                    if val:
                        try:
                            jr = val.get_js_value()
                            result_container["result"] = jr.to_string()
                        except Exception:
                            result_container["result"] = None
                        result_container["done"] = True
                except Exception:
                    result_container["done"] = True

        # Wait for completion (bounded)
        start = time.time()
        while not result_container["done"] and (time.time() - start) < timeout:
            time.sleep(0.01)
        return result_container["result"]

    def _fetch_current_sentences(self):
        js = "JSON.stringify(getCurrentChapterSentences());"
        res = self._evaluate_js_sync(js, timeout=3.0)
        if not res:
            return []
        try:
            arr = json.loads(res)
            # expected [{sid,text}, ...]
            out = []
            for it in arr:
                if isinstance(it, dict) and it.get("sid") and it.get("text"):
                    out.append({"sid": it["sid"], "text": it["text"]})
            return out
        except Exception:
            return []

    def _highlight_sid(self, sid):
        # remove old highlights and add for sid, scroll into view
        js = f"""
        (function() {{
            try {{
                var iframe = document.querySelector('iframe');
                var doc = iframe && iframe.contentDocument ? iframe.contentDocument : document;
                var prev = doc.querySelectorAll('.tts-highlight');
                prev.forEach(function(el) {{
                    el.classList.remove('tts-highlight');
                }});
                if (!sid_placeholder) return;
            }} catch(e) {{ }}
        }})();"""
        # We'll inject proper code with sid
        safe_sid = str(sid).replace("'", "\\'")
        js = f"""
        (function() {{
            try {{
                var iframe = document.querySelector('iframe');
                var doc = iframe && iframe.contentDocument ? iframe.contentDocument : document;
                var prev = doc.querySelectorAll('.tts-highlight');
                prev.forEach(function(el) {{
                    el.classList.remove('tts-highlight');
                }});
                var el = doc.querySelector('[data-tts-id="{safe_sid}"]');
                if (el) {{
                    el.classList.add('tts-highlight');
                    try {{ el.scrollIntoView({{behavior:'smooth', block:'center', inline:'center'}}); }} catch(e){{ window.scrollTo(0, el.offsetTop - (window.innerHeight/3)); }}
                }}
            }} catch(e) {{ console.error('highlight error', e); }}
        }})();
        """
        try:
            view = self._get_webview()
            if view:
                # fire and forget
                try:
                    view.run_javascript(js, None, None, None)
                except Exception:
                    try:
                        view.evaluate_javascript(js, None, None, None)
                    except Exception:
                        pass
        except Exception:
            pass

    def reapply_highlight_after_reload(self):
        # reapply highlight for current sid (if any) after content reload
        if self._current_sid:
            GLib.idle_add(lambda: self._highlight_sid(self._current_sid))

    def play(self):
        with self._control_lock:
            if self._is_playing and not self._is_paused:
                return
            if self._is_paused:
                # resume
                self.resume()
                return

            # start new playback
            self._stop_event.clear()
            self._pause_event.set()
            self._is_paused = False
            self._is_playing = True
            self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
            self._play_thread.start()

    def pause(self):
        with self._control_lock:
            if not self._is_playing or self._is_paused:
                return
            self._is_paused = True
            self._pause_event.clear()
            # stop gst playback if active
            try:
                self._ensure_gst()
                with self._player_lock:
                    if self.player:
                        self.player.set_state(self.Gst.State.NULL)
            except Exception:
                pass

    def resume(self):
        with self._control_lock:
            if not self._is_playing or not self._is_paused:
                return
            self._is_paused = False
            self._pause_event.set()

    def stop(self):
        with self._control_lock:
            self._stop_event.set()
            self._pause_event.set()
            self._is_paused = False
            # stop gst
            try:
                self._ensure_gst()
                with self._player_lock:
                    if self.player:
                        self.player.set_state(self.Gst.State.NULL)
            except Exception:
                pass
            # join thread
            if self._play_thread and self._play_thread.is_alive():
                self._play_thread.join(timeout=1.0)
            self._is_playing = False
            self._current_index = 0
            self._current_sid = None

    def _play_worker(self):
        try:
            self._ensure_gst()
            # fetch sentences
            sentences = self._fetch_current_sentences()
            if not sentences:
                self._is_playing = False
                self._is_paused = False
                return
            self._current_sentences = sentences
            self._current_index = 0

            total = len(sentences)
            while self._current_index < total and not self._stop_event.is_set():
                # pause handling
                if self._is_paused:
                    self._pause_event.wait(0.1)
                    continue

                item = self._current_sentences[self._current_index]
                sid = item.get("sid")
                text = item.get("text")
                self._current_sid = sid

                # highlight
                try:
                    GLib.idle_add(lambda s=sid: self._highlight_sid(s))
                except Exception:
                    pass

                # synth path
                outpath = os.path.join(self.tts_dir, f"{sid}.wav")
                if not os.path.exists(outpath):
                    # run synth in separate process to avoid blocking python interpreter state
                    if self.kokoro_model_path and os.path.exists(self.kokoro_model_path) and self.voices_bin_path and os.path.exists(self.voices_bin_path):
                        p = multiprocessing.Process(target=synth_single_process, args=(self.kokoro_model_path, self.voices_bin_path, text, outpath, self.voice, self.speed, self.lang))
                        p.start()
                        # wait but allow stop/pause
                        while p.is_alive():
                            if self._stop_event.is_set() or self._is_paused:
                                try:
                                    p.terminate()
                                except Exception:
                                    pass
                                break
                            time.sleep(0.05)
                        p.join(timeout=0.1)
                        if not os.path.exists(outpath):
                            # synthesis failed; skip
                            self._current_index += 1
                            continue
                    else:
                        # no kokoro model: skip synthesis
                        self._current_index += 1
                        continue

                # play via gst
                try:
                    with self._player_lock:
                        uri = f"file://{outpath}"
                        self.player.set_property("uri", uri)
                        self.player.set_state(self.Gst.State.PLAYING)
                    # wait for playback or until user action
                    while True:
                        if self._stop_event.is_set():
                            break
                        if self._is_paused:
                            # stop playback and wait
                            with self._player_lock:
                                try:
                                    self.player.set_state(self.Gst.State.NULL)
                                except Exception:
                                    pass
                            break
                        # check position/state
                        time.sleep(0.05)
                        # crude check: if file removed or playback stopped by EOS handler it will be stopped externally
                        # we can't directly wait for EOS synchronously without deeper integration, so poll state:
                        try:
                            state = None
                            with self._player_lock:
                                state = self.player.get_state(0)[1] if self.player else None
                            # if state is NULL or READY, treat as stopped
                            if state in (self.Gst.State.NULL, self.Gst.State.READY):
                                break
                        except Exception:
                            # ignore and continue
                            pass
                    # after playback, try to remove file to conserve space
                    try:
                        if os.path.exists(outpath):
                            os.remove(outpath)
                    except Exception:
                        pass
                except Exception as e:
                    print("Playback error:", e)
                # next
                self._current_index += 1

            # finished or stopped
            self._is_playing = False
            self._is_paused = False
            # clear highlight on finish if not stopped by user? we keep highlight removal.
            try:
                GLib.idle_add(lambda: self._clear_highlights())
            except Exception:
                pass
        except Exception as e:
            print("TTS play worker error:", e)
        finally:
            self._is_playing = False
            self._is_paused = False

    def _clear_highlights(self):
        js = """
        (function() {
            try {
                var iframe = document.querySelector('iframe');
                var doc = iframe && iframe.contentDocument ? iframe.contentDocument : document;
                var prev = doc.querySelectorAll('.tts-highlight');
                prev.forEach(function(el) { el.classList.remove('tts-highlight'); });
            } catch(e) {}
        })();
        """
        try:
            view = self._get_webview()
            if view:
                try:
                    view.run_javascript(js, None, None, None)
                except Exception:
                    try:
                        view.evaluate_javascript(js, None, None, None)
                    except Exception:
                        pass
        except Exception:
            pass

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
        if not self.tts:
            # enable play only when book loaded
            self.tts_play_btn.set_sensitive(bool(self.current_book_path))
            return True
        is_playing = self.tts.is_playing()
        is_paused = self.tts.is_paused()
        if not is_playing and not is_paused:
            self.tts_play_btn.set_sensitive(bool(self.current_book_path))
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
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book_path:
            return False
        if keyval == 65361 or keyval == 65365:
            self.on_prev_page(None); return True
        elif keyval == 65363 or keyval == 65366:
            self.on_next_page(None); return True
        elif keyval == 65360:
            self.webview.evaluate_javascript("rendition.display(0);", -1, None, None, None, None, None); return True
        elif keyval == 65367:
            self.webview.evaluate_javascript("rendition.display(book.spine.length - 1);", -1, None, None, None, None, None); return True
        return False

    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            GLib.timeout_add(300, self._after_load_update)

    def _after_load_update(self):
        try:
            if self.temp_dir and self.tts is None:
                kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                self.tts = TTSEngine(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
        except Exception as e:
            print("TTS init error:", e)
        try:
            if self.tts:
                self.tts.reapply_highlight_after_reload()
        except Exception:
            pass
        return False

    def on_epub_message(self, content_manager, js_result):
        try:
            try:
                js_value = js_result.get_js_value()
                msg = js_value.to_string()
            except Exception:
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
                GLib.timeout_add(500, self._reinject_tts_spans)
        except Exception as e:
            print(f"Error handling epub message: {e}")

    def _reinject_tts_spans(self):
        js_code = """
        (function() {
            try {
                var iframe = document.querySelector('iframe');
                if (iframe && iframe.contentDocument) {
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
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception:
            try:
                self.webview.run_javascript(js_code, None, None, None)
            except Exception:
                pass
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
            self.load_epub_viewer()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def set_column_count(self, count):
        self.column_count = count
        if self.current_book_path:
            spread = "none" if count == 1 else "auto"
            js_code = f"""
            (function() {{
                try {{
                    if (typeof rendition !== 'undefined') {{
                        rendition.spread('{spread}');
                        rendition.resize();
                    }}
                }} catch(e) {{ console.error('Error setting columns:', e); }}
            }})();
            """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js_code, None, None, None)
                except Exception:
                    pass

    def load_epub_viewer(self):
        if not self.current_book_path:
            return
        try:
            with open(self.current_book_path, 'rb') as f:
                epub_data = f.read()
            epub_base64 = base64.b64encode(epub_data).decode('utf-8')
        except Exception as e:
            self.show_error(f"Error reading EPUB: {str(e)}")
            return
        try:
            with open(LOCAL_JSZIP, 'r', encoding='utf-8') as f:
                jszip_code = f.read()
            with open(LOCAL_EPUBJS, 'r', encoding='utf-8') as f:
                epubjs_code = f.read()
        except Exception as e:
            self.show_error(f"Error loading epub.js libraries: {str(e)}")
            return
        spread_mode = "none" if self.column_count == 1 else "auto"
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; width: 100%; overflow: hidden; }}
        body {{ color: #111; background-color: #fafafa; }}
        #viewer {{ width: 100%; height: 100%; background-color: #fafafa; }}
        iframe {{ border: none; }}
        .tts-highlight {{ background: rgba(255, 215, 0, 0.75) !important; box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.35) !important; color: #000 !important; }}
        @media (prefers-color-scheme: dark) {{
            body {{ background-color: #242424; color: #e3e3e3; }}
            #viewer {{ background-color: #242424; }}
            .tts-highlight {{ background: rgba(255, 215, 0, 0.9) !important; box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.75) !important; color: #000 !important; }}
        }}
    </style>
</head>
<body>
    <div id="viewer"></div>
    <script>{jszip_code}</script>
    <script>{epubjs_code}</script>
    <script>
        var epubData = atob("{epub_base64}");
        var epubArray = new Uint8Array(epubData.length);
        for (var i = 0; i < epubData.length; i++) {{ epubArray[i] = epubData.charCodeAt(i); }}
        var book = ePub(epubArray.buffer);
        var rendition = book.renderTo("viewer", {{ width: "100%", height: "100%", flow: "paginated", spread: "{spread_mode}" }});
        var displayed = rendition.display();
        rendition.on('relocated', function(location) {{
            var currentChapter = book.spine.get(location.start.href);
            if (currentChapter) {{
                var chapterIndex = book.spine.spineItems.indexOf(currentChapter);
                var chapterTitle = currentChapter.navItem ? currentChapter.navItem.label : "Untitled";
                window.webkit.messageHandlers.epubHandler.postMessage(JSON.stringify({{ type: "chapterLoaded", index: chapterIndex, total: book.spine.length, title: chapterTitle }}));
            }}
        }});
        rendition.on('rendered', function(section) {{ setTimeout(function() {{ injectTTSSpans(section); }}, 100); }});
        function injectTTSSpans(section) {{
            try {{
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) return;
                var doc = iframe.contentDocument;
                var styleId = '__tts_injected_styles__';
                if (!doc.getElementById(styleId)) {{
                    var s = doc.createElement('style');
                    s.id = styleId;
                    s.textContent = `
                        body, html {{ color: inherit; background: transparent; }}
                        .tts-highlight {{ background: rgba(255, 215, 0, 0.9) !important; box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.75) !important; color: #000 !important; }}
                        @media (prefers-color-scheme: dark) {{ body, html {{ color: #e3e3e3 !important; }} .tts-highlight {{ background: rgba(255, 215, 0, 0.9) !important; }} }}
                    `;
                    (doc.head || doc.getElementsByTagName('head')[0] || doc.documentElement).appendChild(s);
                }}
                var TARGET_TAGS = ['p','div','h1','h2','h3','h4','h5','h6','li','blockquote','figcaption','caption','dt','dd','td','th'];
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
            }} catch(e) {{ console.error('Error in injectTTSSpans:', e); }}
        }}
        function getDirectTextContent(el) {{
            var text = '';
            for (var i = 0; i < el.childNodes.length; i++) {{
                var node = el.childNodes[i];
                if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
                else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'SCRIPT' && node.tagName !== 'STYLE') text += getDirectTextContent(node);
            }}
            return text;
        }}
        function wrapSentencesInElement(el, sentences) {{
            var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
            var textNodes = []; var node;
            while (node = walker.nextNode()) {{ if (node.parentNode.tagName !== 'SCRIPT' && node.parentNode.tagName !== 'STYLE') textNodes.push(node); }}
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
            while ((match = regex.exec(text)) !== null) {{ sentences.push(match[0].trim()); }}
            if (sentences.length === 0 && text.trim()) sentences.push(text.trim());
            return sentences;
        }}
        function stableIdForText(text) {{
            var hash = 0;
            for (var i = 0; i < text.length; i++) {{ hash = ((hash<<5)-hash)+text.charCodeAt(i); hash = hash & hash; }}
            return Math.abs(hash).toString(16).substring(0,12);
        }}
        window.getCurrentChapterSentences = function() {{
            var sentences = [];
            try {{
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) return sentences;
                var doc = iframe.contentDocument;
                var spans = doc.querySelectorAll('[data-tts-id]');
                spans.forEach(function(span) {{
                    var sid = span.getAttribute('data-tts-id');
                    var text = span.textContent.trim();
                    if (sid && text) sentences.push({{sid: sid, text: text}});
                }});
            }} catch(e) {{ console.error('Error in getCurrentChapterSentences:', e); }}
            return sentences;
        }};
    </script>
</body>
</html>"""
        self.webview.load_html(html_content, "file:///")

    def on_prev_chapter(self, button):
        if self.current_book_path:
            try:
                self.webview.evaluate_javascript("prevChapter();", -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript("prevChapter();", None, None, None)
                except Exception:
                    pass

    def on_next_chapter(self, button):
        if self.current_book_path:
            try:
                self.webview.evaluate_javascript("nextChapter();", -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript("nextChapter();", None, None, None)
                except Exception:
                    pass

    def on_prev_page(self, button):
        if self.current_book_path:
            try:
                self.webview.evaluate_javascript("prevPage();", -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript("prevPage();", None, None, None)
                except Exception:
                    pass

    def on_next_page(self, button):
        if self.current_book_path:
            try:
                self.webview.evaluate_javascript("nextPage();", -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript("nextPage();", None, None, None)
                except Exception:
                    pass

    # TTS button handlers (minimal)
    def on_tts_play(self, button):
        if not self.tts:
            # init tts if possible
            if self.temp_dir:
                kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                self.tts = TTSEngine(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
        if self.tts:
            self.tts.play()

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
            except Exception:
                pass

# minimal application class
class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = EpubViewer(self)
        win.present()

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
                except Exception:
                    pass
            try:
                window.cleanup()
            except Exception:
                pass
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

