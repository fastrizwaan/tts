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
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, Gst

import soundfile as sf
try:
    from kokoro_onnx import Kokoro
    TTS_AVAILABLE = True
except Exception:
    Kokoro = None
    TTS_AVAILABLE = False

Adw.init()

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_JSZIP = HERE / "jszip.min.js"
LOCAL_EPUBJS = HERE / "epub.min.js"


# -----------------------
# TTSEngine
# -----------------------
class TTSEngine:
    def __init__(self, webview_getter, temp_dir, kokoro_model_path=None, voices_bin_path=None):
        self.webview_getter = webview_getter
        self.temp_dir = temp_dir
        self.kokoro = None
        self.is_playing = False
        self.should_stop = False
        self.current_thread = None

        # Playback / navigation state
        self._tts_sentences = []
        self._tts_voice = "af_sarah"
        self._tts_speed = 1.0
        self._tts_lang = "en-us"
        self._tts_finished_callback = None
        self._tts_highlight_callback = None

        # index and audio cache
        self._current_play_index = 0
        self._audio_files = {}           # idx -> path
        self._audio_lock = threading.Lock()
        self._synthesis_done = threading.Event()

        # delayed on-demand synth timer (when user navigates)
        self._delayed_timer = None
        self._delayed_timer_lock = threading.Lock()

        # paused state (play/pause toggle)
        self.paused = False
        # resume event used for playback thread to wait while paused
        self._resume_event = threading.Event()
        self._resume_event.set()  # initially not paused

        if TTS_AVAILABLE:
            try:
                # Use provided paths or fallback
                if kokoro_model_path and os.path.exists(kokoro_model_path):
                    model_path = kokoro_model_path
                else:
                    model_path = "/app/share/kokoro-models/kokoro-v1.0.onnx"
                
                if voices_bin_path and os.path.exists(voices_bin_path):
                    voices_path = voices_bin_path
                else:
                    voices_path = "/app/share/kokoro-models/voices-v1.0.bin"

                # Additional fallback paths
                if not os.path.exists(model_path):
                    model_path = os.path.expanduser("~/.local/share/kokoro-models/kokoro-v1.0.onnx")
                    voices_path = os.path.expanduser("~/.local/share/kokoro-models/voices-v1.0.bin")

                if os.path.exists(model_path) and os.path.exists(voices_path):
                    self.kokoro = Kokoro(model_path, voices_path)
                    print("[info] Kokoro TTS initialized")
                else:
                    print(f"[warn] Kokoro models not found at {model_path}")
            except Exception as e:
                print(f"[error] Failed to initialize Kokoro: {e}")

        # Initialize GStreamer for audio playback
        Gst.init(None)
        self.player = Gst.ElementFactory.make("playbin", "player")
        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_gst_message)
        self.playback_finished = False

    def on_gst_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL)
            self.playback_finished = True
        elif t == Gst.MessageType.ERROR:
            self.player.set_state(Gst.State.NULL)
            err, debug = message.parse_error()
            print(f"[error] GStreamer error: {err}, {debug}")
            self.playback_finished = True

    def split_sentences(self, text):
        """Split text into sentences"""
        import re
        # Simple sentence splitter - splits on . ! ? followed by space or end
        sentences = re.split(r'([.!?]+(?:\s+|$))', text)
        result = []
        for i in range(0, len(sentences)-1, 2):
            sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else '')
            sentence = sentence.strip()
            if sentence:
                result.append(sentence)
        # Handle last sentence if it doesn't end with punctuation
        if len(sentences) % 2 == 1 and sentences[-1].strip():
            result.append(sentences[-1].strip())
        return result

    def synthesize_sentence(self, sentence, voice, speed, lang):
        """Synthesize a single sentence and return the audio file path"""
        try:
            samples, sample_rate = self.kokoro.create(sentence, voice=voice, speed=speed, lang=lang)
            tts_dir = os.path.join(self.temp_dir, "tts")
            os.makedirs(tts_dir, exist_ok=True)
            temp_file = os.path.join(tts_dir, f"tts_{int(time.time() * 1000)}_{os.getpid()}.wav")
            sf.write(temp_file, samples, sample_rate)
            return temp_file
        except Exception as e:
            print(f"[error] Synthesis error for sentence: {e}")
            return None

    def _cancel_delayed_timer(self):
        """Cancel any pending delayed synthesis timer"""
        with self._delayed_timer_lock:
            if self._delayed_timer:
                try:
                    self._delayed_timer.cancel()
                except Exception:
                    pass
                self._delayed_timer = None

    def _schedule_delayed_synthesis(self, idx, delay=0.5):
        """
        Schedule on-demand synthesis for a single index after 'delay' seconds.
        If another navigation occurs, the previous timer is canceled and a new one set.
        On firing, the function synthesizes the sentence if audio not already present and
        if the current index hasn't changed away.
        """
        # cancel previous
        self._cancel_delayed_timer()

        def timer_cb():
            # performed in background thread (threading.Timer)
            try:
                if self.should_stop:
                    return
                with self._audio_lock:
                    # already synthesized by pre-synthesis?
                    if self._audio_files.get(idx):
                        return
                # only synthesize if current play index is still the same (user didn't navigate further)
                if idx != self._current_play_index:
                    return
                print(f"[TTS] Delayed synthesis firing for sentence {idx+1}")
                audio_file = self.synthesize_sentence(self._tts_sentences[idx], self._tts_voice, self._tts_speed, self._tts_lang)
                if audio_file:
                    with self._audio_lock:
                        self._audio_files[idx] = audio_file
            except Exception as e:
                print(f"[error] delayed synthesis error: {e}")
            finally:
                with self._delayed_timer_lock:
                    self._delayed_timer = None

        timer = threading.Timer(delay, timer_cb)
        with self._delayed_timer_lock:
            self._delayed_timer = timer
        timer.daemon = True
        timer.start()

    def get_current_sentences(self):
        """Get current chapter sentences from webview"""
        try:
            webview = self.webview_getter()
            if not webview:
                return []
            
            js_code = """
            (function() {
                try {
                    return JSON.stringify(getCurrentChapterSentences());
                } catch(e) {
                    console.error('Error getting sentences:', e);
                    return '[]';
                }
            })();
            """
            
            result = []
            def callback(js_result):
                nonlocal result
                try:
                    # Handle different WebKit versions
                    try:
                        # Try newer API first
                        js_value = js_result.get_js_value()
                        json_str = js_value.to_string()
                    except AttributeError:
                        # Fall back to older API
                        json_str = js_result.to_string()
                    
                    sentences_data = json.loads(json_str)
                    result = [s["text"] for s in sentences_data if s.get("text")]
                except Exception as e:
                    print(f"[error] Failed to parse sentences: {e}")
                    result = []
            
            # Use GLib to run this synchronously
            from gi.repository import GObject
            main_context = GLib.MainContext.default()
            
            def idle_callback():
                webview.evaluate_javascript(js_code, -1, None, callback, None)
                return False
            
            GLib.idle_add(idle_callback)
            
            # Wait a bit for the result
            timeout = 2.0
            start_time = time.time()
            while not result and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                main_context.iteration(False)
            
            return result
        except Exception as e:
            print(f"[error] Error getting sentences: {e}")
            return []

    def highlight_current_sentence(self, index):
        """Highlight the current sentence in the webview"""
        try:
            webview = self.webview_getter()
            if not webview or index < 0 or index >= len(self._tts_sentences):
                # Clear all highlights
                js_clear = """
                (function() {
                    try {
                        var iframe = document.querySelector('iframe');
                        if (iframe && iframe.contentDocument) {
                            var spans = iframe.contentDocument.querySelectorAll('.tts-highlight');
                            spans.forEach(function(span) {
                                span.classList.remove('tts-highlight');
                            });
                        }
                    } catch(e) {
                        console.error('Error clearing highlights:', e);
                    }
                })();
                """
                webview.evaluate_javascript(js_clear, -1, None, None, None, None, None)
                return
            
            # Highlight specific sentence
            js_highlight = f"""
            (function() {{
                try {{
                    var iframe = document.querySelector('iframe');
                    if (iframe && iframe.contentDocument) {{
                        // Clear previous highlights
                        var spans = iframe.contentDocument.querySelectorAll('.tts-highlight');
                        spans.forEach(function(span) {{
                            span.classList.remove('tts-highlight');
                        }});
                        
                        // Find and highlight current sentence
                        var allSpans = iframe.contentDocument.querySelectorAll('[data-tts-id]');
                        if (allSpans.length > {index}) {{
                            var currentSpan = allSpans[{index}];
                            currentSpan.classList.add('tts-highlight');
                            currentSpan.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                        }}
                    }}
                }} catch(e) {{
                    console.error('Error highlighting sentence:', e);
                }}
            }})();
            """
            webview.evaluate_javascript(js_highlight, -1, None, None, None, None, None)
        except Exception as e:
            print(f"[error] Error highlighting sentence: {e}")

    def reapply_highlight_after_reload(self):
        """Re-apply current highlight after webview reload"""
        if self.is_playing and not self.paused:
            GLib.timeout_add(500, lambda: self.highlight_current_sentence(self._current_play_index))

    def speak_current_chapter(self):
        """Start TTS for current chapter"""
        if not self.kokoro:
            print("[warn] TTS not available")
            return
        
        sentences = self.get_current_sentences()
        if not sentences:
            print("[warn] No sentences found in current chapter")
            return
        
        self.speak_sentences_list(sentences)

    def speak_sentences_list(self, sentences, voice="af_sarah", speed=1.0, lang="en-us"):
        """Speak a list of pre-split sentences with pre-synthesis for smooth playback."""
        if not self.kokoro:
            print("[warn] TTS not available")
            return

        # Stop previous playback if any
        self.stop()
        self.should_stop = False

        self._tts_sentences = list(sentences)
        self._tts_voice = voice
        self._tts_speed = speed
        self._tts_lang = lang
        self._tts_highlight_callback = self.highlight_current_sentence
        self._audio_files = {}
        self._current_play_index = 0
        self._synthesis_done.clear()
        self._cancel_delayed_timer()
        self.paused = False
        self._resume_event.set()

        def tts_thread():
            try:
                total = len(self._tts_sentences)
                print(f"[TTS] Speaking {total} sentences (with navigation support)")

                # Synthesis worker: synthesize ahead but respect paused state.
                def synthesis_worker():
                    try:
                        synth_idx = 0
                        while not self.should_stop and synth_idx < total:
                            with self._audio_lock:
                                cur = self._current_play_index

                            # never synthesize indices that are less than current
                            if synth_idx < cur:
                                synth_idx = cur

                            # choose lookahead depending on pause state (paused -> cur+1, else -> cur+3)
                            if self.paused:
                                lookahead_limit = cur + 1
                            else:
                                lookahead_limit = cur + 3

                            if synth_idx > lookahead_limit:
                                # wait briefly and re-evaluate; gives responsiveness for user navigation
                                time.sleep(0.05)
                                continue

                            # If already synthesized, advance
                            with self._audio_lock:
                                if self._audio_files.get(synth_idx):
                                    synth_idx += 1
                                    continue

                            # Synthesize this index if it's within allowed lookahead
                            if synth_idx <= lookahead_limit:
                                if self.should_stop:
                                    break
                                print(f"[TTS] Pre-synthesizing sentence {synth_idx+1}/{total} (paused={self.paused})")
                                audio_file = self.synthesize_sentence(self._tts_sentences[synth_idx], self._tts_voice, self._tts_speed, self._tts_lang)
                                if audio_file:
                                    with self._audio_lock:
                                        if synth_idx not in self._audio_files:
                                            self._audio_files[synth_idx] = audio_file
                                synth_idx += 1
                            else:
                                time.sleep(0.05)

                        self._synthesis_done.set()
                    except Exception as e:
                        print(f"[error] Synthesis worker error: {e}")
                        import traceback
                        traceback.print_exc()
                        self._synthesis_done.set()

                synth_thread = threading.Thread(target=synthesis_worker, daemon=True)
                synth_thread.start()

                self._is_playing = True  # Changed from is_playing to _is_playing
                played_count = 0

                while self._current_play_index < total and not self.should_stop:
                    idx = self._current_play_index

                    # Immediately highlight current sentence so navigation feels responsive
                    if self._tts_highlight_callback:
                        GLib.idle_add(self._tts_highlight_callback, idx)

                    # If paused, wait here until resumed (or stopped). This prevents continuing playback when paused.
                    while self.paused and not self.should_stop:
                        # ensure we don't pre-synthesize beyond the allowed window while paused
                        self._cancel_delayed_timer()
                        # wakeable wait so stop() or resume() can take effect
                        self._resume_event.wait(0.1)
                        # loop again and check flags

                    if self.should_stop:
                        break

                    # Check if audio already available (pre-synthesized)
                    audio_file = None
                    with self._audio_lock:
                        audio_file = self._audio_files.get(idx)

                    # If not available, we will wait for either pre-synthesis, or the delayed timer
                    if not audio_file:
                        # schedule delayed on-demand synthesis; user may navigate further and cancel/reschedule
                        self._schedule_delayed_synthesis(idx, delay=0.5)

                        # Wait until audio becomes available or user navigates away or stopped
                        waited = 0.0
                        while not self.should_stop:
                            with self._audio_lock:
                                audio_file = self._audio_files.get(idx)
                            if audio_file:
                                break
                            # If user changed index, stop waiting and continue loop (playback will follow new index)
                            if self._current_play_index != idx:
                                break
                            time.sleep(0.02)
                            waited += 0.02
                            # avoid infinite waits: if synthesis worker finished and still no audio, fall through to on-demand synth attempt
                            if self._synthesis_done.is_set() and waited > 0.5:
                                break

                    if self.should_stop:
                        break

                    # Re-check audio
                    with self._audio_lock:
                        audio_file = self._audio_files.get(idx)

                    # If still no audio, attempt on-demand immediate synth (this covers rare races)
                    if not audio_file:
                        try:
                            print(f"[TTS] On-demand synth (fallback) for sentence {idx+1}")
                            audio_file = self.synthesize_sentence(self._tts_sentences[idx], self._tts_voice, self._tts_speed, self._tts_lang)
                            if audio_file:
                                with self._audio_lock:
                                    self._audio_files[idx] = audio_file
                        except Exception as e:
                            print(f"[error] on-demand synth failed: {e}")
                            audio_file = None

                    if not audio_file:
                        # nothing to play (shouldn't happen often). Advance index to avoid deadlock.
                        print(f"[warn] No audio for index {idx}, skipping forward")
                        self._current_play_index = idx + 1
                        continue

                    # Before actually playing, double-check paused (user might have paused while waiting)
                    if self.paused:
                        # do not start playback while paused; continue outer loop and wait at top
                        continue

                    # Start playback
                    print(f"[TTS] Playing sentence {idx+1}/{total}: {self._tts_sentences[idx][:50]}...")
                    self.player.set_property("uri", f"file://{audio_file}")
                    self.player.set_state(Gst.State.PLAYING)
                    self.playback_finished = False

                    # Wait until playback completes, or user navigates (changing current_play_index), or stop
                    while not self.playback_finished and not self.should_stop:
                        # if user changed index, break to move to new index
                        if self._current_play_index != idx:
                            break
                        # if paused during playback, stop playback and break
                        if self.paused:
                            try:
                                self.player.set_state(Gst.State.NULL)
                            except Exception:
                                pass
                            break
                        time.sleep(0.02)

                    # Stop playback if necessary
                    try:
                        self.player.set_state(Gst.State.NULL)
                    except Exception:
                        pass

                    # If we completed playback for this index (no manual jump and not paused), cleanup the audio file to save space
                    if (self._current_play_index == idx) and (not self.paused):
                        try:
                            with self._audio_lock:
                                af = self._audio_files.get(idx)
                                if af:
                                    try:
                                        os.remove(af)
                                    except:
                                        pass
                                    try:
                                        del self._audio_files[idx]
                                    except KeyError:
                                        pass
                        except Exception:
                            pass
                        played_count += 1
                        self._current_play_index = idx + 1
                    else:
                        # user jumped or paused; continue loop with new index or wait
                        pass

                # End playback loop
                self._is_playing = False  # Changed from is_playing to _is_playing
                self._cancel_delayed_timer()
                # Clear highlight when done (if not stopped by user)
                if self._tts_highlight_callback and not self.should_stop:
                    GLib.idle_add(self._tts_highlight_callback, -1)

            except Exception as e:
                print(f"[error] TTS thread error: {e}")
                import traceback
                traceback.print_exc()

    def next_sentence(self):
        """Skip to next sentence during playback: immediate highlight, schedule delayed synth for the new index."""
        if not self._tts_sentences:
            return
        with self._audio_lock:
            # move forward
            self._current_play_index = min(len(self._tts_sentences)-1, self._current_play_index + 1)
            idx = self._current_play_index
        # immediate highlight
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx)
        # stop current playback to cause loop to re-evaluate
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        # schedule delayed on-demand synth for this index
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def prev_sentence(self):
        """Go to previous sentence during playback: immediate highlight, schedule delayed synth for that index."""
        if not self._tts_sentences:
            return
        with self._audio_lock:
            self._current_play_index = max(0, self._current_play_index - 1)
            idx = self._current_play_index
        # immediate highlight
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx)
        # stop current playback to cause loop to re-evaluate
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        # schedule delayed on-demand synth for this index
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def pause(self):
        """Pause playback: stop player and put engine into paused state."""
        print("[TTS] Pausing TTS")
        self.paused = True
        self._resume_event.clear()
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass

    def resume(self):
        """Resume playback from paused state."""
        print("[TTS] Resuming TTS")
        self.paused = False
        # wake playback thread
        self._resume_event.set()
        # ensure delayed timer state is sane
        self._cancel_delayed_timer()

    def stop(self):
        """Stop TTS playback and cleanup resources"""
        self.should_stop = True
        self.paused = False
        self.playback_finished = True
        
        # Wake any waiting playback thread so it can exit
        try:
            self._resume_event.set()
        except Exception:
            pass
        
        # Cancel delayed timer BEFORE stopping player to avoid races
        self._cancel_delayed_timer()
        
        # Stop the player
        if self.player:
            try:
                self.player.set_state(Gst.State.NULL)
            except Exception:
                pass
        
        self.is_playing = False
        
        # Clear highlight
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, -1)
        
        # Attempt to join thread briefly
        if self.current_thread:
            self.current_thread.join(timeout=1.0)
        
        # Cleanup queued audio files (best-effort)
        try:
            with self._audio_lock:
                for idx, path in list(self._audio_files.items()):
                    try:
                        os.remove(path)
                    except:
                        pass
                self._audio_files.clear()
        except Exception:
            pass

    def is_playing(self):
        return self._is_playing and not self.paused

    def is_paused(self):
        return self.paused


# -----------------------
# EpubViewerApp
# -----------------------
class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.window = None

    def do_activate(self):
        if not self.window:
            self.window = EpubViewer(self)
        self.window.present()

    def do_startup(self):
        Adw.Application.do_startup(self)
        
        # Add column settings actions
        action = Gio.SimpleAction.new("set-columns", GLib.VariantType.new("i"))
        action.connect("activate", self.on_set_columns)
        self.add_action(action)

    def on_set_columns(self, action, param):
        columns = param.get_int32()
        if self.window:
            self.window.set_column_count(columns)


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

        # TTS navigation buttons
        self.tts_prev_btn = Gtk.Button()
        self.tts_prev_btn.set_icon_name("media-skip-backward-symbolic")
        self.tts_prev_btn.set_tooltip_text("Previous Sentence")
        self.tts_prev_btn.add_css_class("flat")
        self.tts_prev_btn.connect("clicked", self.on_tts_prev)
        self.tts_prev_btn.set_sensitive(False)

        self.tts_next_btn = Gtk.Button()
        self.tts_next_btn.set_icon_name("media-skip-forward-symbolic")
        self.tts_next_btn.set_tooltip_text("Next Sentence")
        self.tts_next_btn.add_css_class("flat")
        self.tts_next_btn.connect("clicked", self.on_tts_next)
        self.tts_next_btn.set_sensitive(False)

        tts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tts_box.set_spacing(4)
        tts_box.append(self.tts_prev_btn)
        tts_box.append(self.tts_play_btn)
        tts_box.append(self.tts_pause_btn)
        tts_box.append(self.tts_stop_btn)
        tts_box.append(self.tts_next_btn)
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
            self.tts_play_btn.set_sensitive(bool(self.current_book_path and TTS_AVAILABLE))
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            self.tts_prev_btn.set_sensitive(False)
            self.tts_next_btn.set_sensitive(False)
            return True  # Continue the timeout
        
        is_playing = self.tts.is_playing()
        is_paused = self.tts.is_paused()
        
        if not is_playing and not is_paused:
            # TTS is stopped
            self.tts_play_btn.set_sensitive(bool(self.current_book_path))
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            self.tts_prev_btn.set_sensitive(False)
            self.tts_next_btn.set_sensitive(False)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_playing and not is_paused:
            # TTS is actively playing
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_prev_btn.set_sensitive(True)
            self.tts_next_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_paused:
            # TTS is paused
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_prev_btn.set_sensitive(True)
            self.tts_next_btn.set_sensitive(True)
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
            if self.temp_dir and self.tts is None and TTS_AVAILABLE:
                kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                self.tts = TTSEngine(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
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

    # TTS button handlers
    def on_tts_play(self, button):
        if self.tts and self.current_book_path:
            self.tts.speak_current_chapter()

    def on_tts_pause(self, button):
        if self.tts:
            if self.tts.is_playing() and not self.tts.is_paused():
                self.tts.pause()
            else:
                self.tts.resume()

    def on_tts_stop(self, button):
        if self.tts:
            self.tts.stop()

    def on_tts_prev(self, button):
        if self.tts:
            self.tts.prev_sentence()

    def on_tts_next(self, button):
        if self.tts:
            self.tts.next_sentence()

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
        /* Ensure default text color is dark-mode friendly when user prefers dark */
        body {{ color: #111; background-color: #fafafa; }}
        #viewer {{
            width: 100%;
            height: 100%;
            background-color: #fafafa;
        }}
        iframe {{
            border: none;
        }}
        /* Default highlight (light theme) */
        .tts-highlight {{
            background: rgba(255, 215, 0, 0.75) !important;
            box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.35) !important;
            color: #000 !important;
        }}
        @media (prefers-color-scheme: dark) {{
            /* Dark-mode page background and text color */
            body {{ background-color: #242424; color: #e3e3e3; }}
            #viewer {{ background-color: #242424; }}
            /* Use a high-contrast highlight and ensure readable text */
            .tts-highlight {{
                background: rgba(255, 215, 0, 0.9) !important;
                box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.75) !important;
                color: #000 !important;
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

                // Inject styles into the iframe document so .tts-highlight is applied inside the iframe.
                // Minimal, idempotent injection.
                try {{
                    var styleId = '__tts_injected_styles__';
                    if (!doc.getElementById(styleId)) {{
                        var s = doc.createElement('style');
                        s.id = styleId;
                        s.textContent = `
                            body, html {{ color: inherit; background: transparent; }}
                            .tts-highlight {{
                                background: rgba(255, 215, 0, 0.9) !important;
                                box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.75) !important;
                                color: #000 !important;
                            }}
                            @media (prefers-color-scheme: dark) {{
                                body, html {{ color: #e3e3e3 !important; }}
                                .tts-highlight {{
                                    background: rgba(255, 215, 0, 0.9) !important;
                                    box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.75) !important;
                                    color: #000 !important;
                                }}
                            }}
                        `;
                        (doc.head || doc.getElementsByTagName('head')[0] || doc.documentElement).appendChild(s);
                    }}
                }} catch(e) {{
                    console.error('inject style into iframe failed', e);
                }}
                
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
                shutil.rmtree(self.temp_dir)
                print(f"Cleaned up temp EPUB dir: {self.temp_dir}")
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")


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
