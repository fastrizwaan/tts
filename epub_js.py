#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js with TTS support using Kokoro
"""
import os
import base64
import tempfile
import pathlib
import sys
import json
import threading
import time

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, WebKit, GLib, Adw, Gst

try:
    import soundfile as sf
    from kokoro_onnx import Kokoro
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    print("[warn] TTS not available: kokoro_onnx or soundfile not installed")

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_JSZIP = HERE / "jszip.min.js"
LOCAL_EPUBJS = HERE / "epub.min.js"

def writable_path(filename):
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)
    return os.path.join(d, filename)

class TTSEngine:
    def __init__(self):
        self.kokoro = None
        self.is_playing = False
        self.should_stop = False
        self.current_thread = None

        # Playback / navigation state
        self._tts_sentences = []
        self._tts_voice = None
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
                model_path = "/app/share/kokoro-models/kokoro-v1.0.onnx"
                voices_path = "/app/share/kokoro-models/voices-v1.0.bin"

                # Fallback paths
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
            temp_file = writable_path(f"tts_{int(time.time() * 1000)}_{os.getpid()}.wav")
            sf.write(temp_file, samples, sample_rate)
            return temp_file
        except Exception as e:
            print(f"[error] Synthesis error for sentence: {e}")
            return None

    def _cancel_delayed_timer(self):
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

    def speak_sentences_list(self, sentences, voice="af_sarah", speed=1.0, lang="en-us",
                            highlight_callback=None, finished_callback=None):
        """Speak a list of pre-split sentences with pre-synthesis for smooth playback.
           Supports play/pause: when paused, pre-synthesis is limited to next 2 sentences.
        """
        if not self.kokoro:
            print("[warn] TTS not available")
            if finished_callback:
                GLib.idle_add(finished_callback)
            return

        # Stop previous playback if any
        self.stop()
        self.should_stop = False

        self._tts_sentences = list(sentences)
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

                self.is_playing = True
                played_count = 0

                while self._current_play_index < total and not self.should_stop:
                    idx = self._current_play_index

                    # Immediately highlight current sentence so navigation feels responsive
                    if self._tts_highlight_callback:
                        GLib.idle_add(self._tts_highlight_callback, idx, self._tts_sentences[idx])

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
                self.is_playing = False
                self._cancel_delayed_timer()
                # Clear highlight when done (if not stopped by user)
                if self._tts_highlight_callback and not self.should_stop:
                    GLib.idle_add(self._tts_highlight_callback, -1, "")

                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

            except Exception as e:
                print(f"[error] TTS thread error: {e}")
                import traceback
                traceback.print_exc()
                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

        self.current_thread = threading.Thread(target=tts_thread, daemon=True)
        self.current_thread.start()

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
            GLib.idle_add(self._tts_highlight_callback, idx, self._tts_sentences[idx])
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
            GLib.idle_add(self._tts_highlight_callback, idx, self._tts_sentences[idx])
        # stop current playback to cause loop to re-evaluate
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        # schedule delayed on-demand synth for this index
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def pause(self):
        """Pause playback: stop player and put engine into paused state.
           While paused, the synthesis worker will only produce the next 2 sentences.
        """
        print("[TTS] Pausing TTS")
        self.paused = True
        self._resume_event.clear()
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        # synthesis worker respects paused flag and will limit lookahead to next 2 sentences.

    def resume(self):
        """Resume playback from paused state."""
        print("[TTS] Resuming TTS")
        self.paused = False
        # wake playback thread
        self._resume_event.set()
        # ensure delayed timer state is sane
        self._cancel_delayed_timer()

    def stop(self):
        self.should_stop = True
        self.paused = False
        # wake any waiting playback thread so it can exit
        try:
            self._resume_event.set()
        except Exception:
            pass
        if self.player:
            try:
                self.player.set_state(Gst.State.NULL)
            except Exception:
                pass
        self.playback_finished = True
        self.is_playing = False
        # Attempt to join thread briefly
        if self.current_thread:
            self.current_thread.join(timeout=1.0)
        # cancel any delayed timer
        self._cancel_delayed_timer()
        # cleanup queued audio files (best-effort)
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

class EpubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, application, file_path=None):
        super().__init__(application=application)
        self.set_default_size(1200, 800)
        self.set_title("EPUB/HTML Reader with TTS")
        self.temp_dir = None
        self.tts_engine = TTSEngine() if TTS_AVAILABLE else None

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        # Navigation buttons
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_bar.pack_start(nav_box)

        self.prev_button = Gtk.Button(label="←")
        self.prev_button.set_tooltip_text("Previous chapter")
        self.prev_button.connect("clicked", self.go_prev)
        nav_box.append(self.prev_button)

        self.next_button = Gtk.Button(label="→")
        self.next_button.set_tooltip_text("Next chapter")
        self.next_button.connect("clicked", self.go_next)
        nav_box.append(self.next_button)

        # TTS buttons
        if TTS_AVAILABLE and self.tts_engine and self.tts_engine.kokoro:
            tts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_bar.pack_start(tts_box)

            # Play/Pause toggle. Initially play icon.
            self.tts_play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
            self.tts_play_button.set_tooltip_text("Play / Pause TTS")
            self.tts_play_button.connect("clicked", self.on_tts_play)
            tts_box.append(self.tts_play_button)

            self.tts_stop_button = Gtk.Button(icon_name="media-playback-stop-symbolic")
            self.tts_stop_button.set_tooltip_text("Stop reading")
            self.tts_stop_button.connect("clicked", self.on_tts_stop)
            self.tts_stop_button.set_sensitive(False)
            tts_box.append(self.tts_stop_button)

            # Previous/Next sentence buttons
            self.tts_prev_button = Gtk.Button(icon_name="media-skip-backward-symbolic")
            self.tts_prev_button.set_tooltip_text("Play previous sentence")
            self.tts_prev_button.connect("clicked", self.on_tts_prev)
            self.tts_prev_button.set_sensitive(False)
            tts_box.append(self.tts_prev_button)

            self.tts_next_button = Gtk.Button(icon_name="media-skip-forward-symbolic")
            self.tts_next_button.set_tooltip_text("Play next sentence")
            self.tts_next_button.connect("clicked", self.on_tts_next)
            self.tts_next_button.set_sensitive(False)
            tts_box.append(self.tts_next_button)

        self.toc_button = Gtk.Button(icon_name="view-list-symbolic")
        self.toc_button.set_tooltip_text("Table of Contents")
        self.toc_button.connect("clicked", self.toggle_toc)
        header_bar.pack_end(self.toc_button)

        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        self.split_view = Adw.OverlaySplitView()
        main_box.append(self.split_view)

        self.toc_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toc_sidebar.set_size_request(250, -1)
        self.toc_sidebar.set_visible(False)
        self.split_view.set_sidebar(self.toc_sidebar)

        toc_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toc_header.append(Gtk.Label(label="Table of Contents", hexpand=True))
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.connect("clicked", lambda b: self.split_view.set_collapsed(True))
        toc_header.append(close_btn)
        self.toc_sidebar.append(toc_header)

        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_vexpand(True)
        self.toc_sidebar.append(toc_scroll)
        self.toc_listbox = Gtk.ListBox()
        self.toc_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        toc_scroll.set_child(self.toc_listbox)

        scrolled = Gtk.ScrolledWindow()
        self.split_view.set_content(scrolled)
        self.webview = WebKit.WebView()
        self.setup_webview()
        self.webview.set_vexpand(True)
        scrolled.set_child(self.webview)

        self.user_manager = self.webview.get_user_content_manager()
        self.user_manager.register_script_message_handler("tocLoaded")
        self.user_manager.register_script_message_handler("navChanged")
        self.user_manager.register_script_message_handler("pageText")
        self.user_manager.connect("script-message-received::tocLoaded", self.on_toc_loaded)
        self.user_manager.connect("script-message-received::navChanged", self.on_nav_changed)
        self.user_manager.connect("script-message-received::pageText", self.on_page_text)

        forwarder = WebKit.UserScript(
            """
            (function(){
              window.addEventListener('message', function(event) {
                try {
                  if (!event.data) return;
                  var handlers = ['tocLoaded', 'navChanged', 'pageText'];
                  handlers.forEach(function(handler) {
                    if (event.data.type === handler) {
                      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers[handler]) {
                        var payload = event.data.payload;
                        if (typeof payload !== 'string') payload = JSON.stringify(payload);
                        window.webkit.messageHandlers[handler].postMessage(payload);
                      }
                    }
                  });
                } catch(e) {}
              }, false);
            })();
            """,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.START,
            None,
            None
        )
        self.user_manager.add_script(forwarder)

        if file_path:
            self.load_file(file_path)

    def setup_webview(self):
        settings = self.webview.get_settings()
        try:
            settings.set_enable_javascript(True)
        except Exception:
            pass
        for name in ("set_allow_file_access_from_file_urls",
                     "set_allow_universal_access_from_file_urls",
                     "set_enable_write_console_messages_to_stdout"):
            try:
                getattr(settings, name)(True)
            except Exception:
                pass

        # --- DARK/LIGHT THEME SUPPORT (minimal changes) ---
        # Inject CSS variables + prefers-color-scheme handling into every page/frame
        # so pages (and EPUB viewer iframe content) can adapt to the system theme automatically.
        theme_inject_js = r"""
        (function() {
            try {
                // Create a <style> with CSS variables that adapt to system theme.
                var css = `
                    :root {
                        --app-bg: #ffffff;
                        --app-text: #000000;
                        --app-link: #1a73e8;
                        --tts-highlight-bg: rgba(255,235,59,0.95); /* light: yellow */
                        --tts-highlight-text: #000000;
                    }
                    @media (prefers-color-scheme: dark) {
                        :root {
                            --app-bg: #0f1113;
                            --app-text: #e6e6e6;
                            --app-link: #8ab4f8;
                            --tts-highlight-bg: rgba(255,235,59,0.95); /* keep highlight visible on dark */
                            --tts-highlight-text: #000000;
                        }
                    }
                    html, body {
                        background: var(--app-bg) !important;
                        color: var(--app-text) !important;
                        transition: background 150ms ease, color 150ms ease;
                    }
                    a { color: var(--app-link) !important; }
                    /* Make sure EPUB viewer container uses the variables as well */
                    #viewer, #viewer * {
                        background: transparent !important;
                        color: inherit !important;
                    }
                `;
                var style = document.createElement('style');
                style.setAttribute('data-app-theme','injected');
                style.textContent = css;
                (document.head || document.documentElement).appendChild(style);

                // Also set the inline computed background/text on root to help pages that have inline styles
                document.documentElement.style.background = "var(--app-bg)";
                document.body && (document.body.style.background = "var(--app-bg)");
                document.documentElement.style.color = "var(--app-text)";
                
                // Observe changes to prefers-color-scheme and re-apply inline styles to keep webview updated
                try {
                    var mq = window.matchMedia('(prefers-color-scheme: dark)');
                    mq.addEventListener && mq.addEventListener('change', function() {
                        document.documentElement.style.background = "var(--app-bg)";
                        document.body && (document.body.style.background = "var(--app-bg)");
                        document.documentElement.style.color = "var(--app-text)";
                    });
                } catch(e){}
            } catch(e){
                console.error('Theme injection failed', e);
            }
        })();
        """
        try:
            user_script = WebKit.UserScript(
                theme_inject_js,
                WebKit.UserContentInjectedFrames.ALL_FRAMES,
                WebKit.UserScriptInjectionTime.START,
                None,
                None
            )
            self.webview.get_user_content_manager().add_script(user_script)
        except Exception as e:
            print("[warn] Failed to add theme injection script:", e)

    def _extract_message_string(self, message):
        try:
            if hasattr(message, "to_string"):
                try:
                    return message.to_string()
                except Exception:
                    pass
                try:
                    return message.to_json(0)
                except Exception:
                    pass

            if hasattr(message, "get_js_value"):
                jsval = message.get_js_value()
                try:
                    return jsval.to_string()
                except Exception:
                    pass
                try:
                    return jsval.to_json(0)
                except Exception:
                    pass

            if isinstance(message, GLib.Variant):
                v = message.unpack()
                if isinstance(v, (str, bytes)):
                    return v.decode() if isinstance(v, bytes) else v
                return json.dumps(v)

            if hasattr(message, "get_string"):
                try:
                    s = message.get_string()
                    if s is not None:
                        return s
                except Exception:
                    pass

            return str(message)
        except Exception as e:
            print("extract_message_string error:", e)
            import traceback
            traceback.print_exc()
            return None

    def on_toc_loaded(self, manager, message):
        try:
            raw = self._extract_message_string(message)
            if not raw:
                print("on_toc_loaded: empty payload")
                return

            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass

            toc = None
            try:
                toc = json.loads(raw)
            except Exception:
                try:
                    toc = json.loads(raw.replace("'", '"'))
                except Exception:
                    toc = None

            if toc is None:
                print("on_toc_loaded: failed to decode toc payload")
                return

            if isinstance(toc, dict) and "toc" in toc:
                toc = toc["toc"]
            if not isinstance(toc, list):
                toc = [toc]

            self.populate_toc(toc)
        except Exception as e:
            print(f"Error processing TOC: {e}")
            import traceback
            traceback.print_exc()

    def on_nav_changed(self, manager, message):
        try:
            raw = self._extract_message_string(message)
            if not raw:
                return
            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass
            try:
                nav = json.loads(raw)
            except Exception:
                try:
                    nav = json.loads(raw.replace("'", '"'))
                except Exception:
                    nav = None
        except Exception as e:
            print(f"Error processing nav: {e}")

    def on_page_text(self, manager, message):
        """Receive text content from current page for TTS"""
        try:
            raw = self._extract_message_string(message)
            if not raw:
                return

            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass

            # Parse the JSON array of sentences
            try:
                sentences = json.loads(raw)
                if not isinstance(sentences, list):
                    sentences = [raw.strip()]
            except:
                sentences = [raw.strip()]

            # Filter out empty sentences
            sentences = [s for s in sentences if s.strip()]

            if sentences and self.tts_engine:
                print(f"[TTS] Received {len(sentences)} sentences")
                for i, s in enumerate(sentences[:5]):  # Show first 5
                    print(f"  {i+1}: {s[:60]}...")

                # Enable controls
                try:
                    self.tts_stop_button.set_sensitive(True)
                    # set play button to "pause" icon because playback is starting
                    self.tts_play_button.set_icon_name("media-playback-pause-symbolic")
                    if hasattr(self, 'tts_prev_button'):
                        self.tts_prev_button.set_sensitive(True)
                    if hasattr(self, 'tts_next_button'):
                        self.tts_next_button.set_sensitive(True)
                except Exception:
                    pass

                # Start sentence-by-sentence TTS with highlighting
                # Pass the highlight callback so TTSEngine can call it immediately on navigation
                self.tts_engine.speak_sentences_list(
                    sentences,
                    highlight_callback=self.highlight_sentence,
                    finished_callback=self.on_tts_finished
                )
        except Exception as e:
            print(f"Error processing page text: {e}")
            import traceback
            traceback.print_exc()

    def highlight_sentence(self, sentence_idx, sentence_text):
        """Highlight the current sentence being read"""
        if sentence_idx < 0:
            # Clear all highlights
            js_code = """
            (function() {
                try {
                    var iframe = document.querySelector('#viewer iframe');
                    if (iframe && iframe.contentDocument) {
                        var doc = iframe.contentDocument;
                        var highlights = doc.querySelectorAll('.tts-highlight');
                        highlights.forEach(function(el) {
                            var text = el.textContent;
                            var textNode = doc.createTextNode(text);
                            el.parentNode.replaceChild(textNode, el);
                        });
                        doc.normalize();
                    } else {
                        // fallback for plain HTML documents loaded directly
                        var doc2 = document;
                        var highlights = doc2.querySelectorAll('.tts-highlight');
                        highlights.forEach(function(el) {
                            var text = el.textContent;
                            var textNode = doc2.createTextNode(text);
                            el.parentNode.replaceChild(textNode, el);
                        });
                        doc2.normalize();
                    }
                } catch(e) {
                    console.error('Error clearing highlights:', e);
                }
            })();
            """
        else:
            # Escape special characters for JavaScript
            escaped_text = sentence_text.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r').replace('"', '\\"')

            js_code = f"""
            (function() {{
                try {{
                    var iframe = document.querySelector('#viewer iframe');
                    var doc = (iframe && iframe.contentDocument) ? iframe.contentDocument : document;

                    // Clear previous highlights
                    var oldHighlights = doc.querySelectorAll('.tts-highlight');
                    oldHighlights.forEach(function(el) {{
                        var text = el.textContent;
                        var textNode = doc.createTextNode(text);
                        el.parentNode.replaceChild(textNode, el);
                    }});
                    doc.normalize();

                    var sentenceToFind = '{escaped_text}';

                    // Normalize function - removes extra whitespace
                    function normalize(text) {{
                        return text.replace(/\\s+/g, ' ').trim();
                    }}

                    var normalizedSearch = normalize(sentenceToFind);
                    var searchWords = normalizedSearch.split(' ');
                    var firstWord = searchWords[0];
                    var lastWord = searchWords[searchWords.length - 1];

                    console.log('Searching for:', normalizedSearch.substring(0, 50));

                    // Function to highlight within an element
                    function highlightInElement(element) {{
                        var fullText = element.textContent || '';
                        var normalizedFull = normalize(fullText);

                        // Find the sentence using normalized text
                        var index = normalizedFull.indexOf(normalizedSearch);
                        if (index < 0) {{
                            // Try fuzzy match - look for first few words
                            if (searchWords.length >= 2) {{
                                var partialSearch = searchWords.slice(0, Math.min(3, searchWords.length)).join(' ');
                                index = normalizedFull.indexOf(partialSearch);
                                if (index < 0) return false;
                                // Adjust to find full sentence
                                var endIndex = index + partialSearch.length;
                                for (var i = endIndex; i < normalizedFull.length && i < endIndex + 200; i++) {{
                                    if (/[.!?]/.test(normalizedFull[i])) {{
                                        normalizedSearch = normalizedFull.substring(index, i + 1).trim();
                                        break;
                                    }}
                                }}
                            }} else {{
                                return false;
                            }}
                        }}

                        console.log('Found at index:', index, 'in element:', element.tagName);

                        // Map normalized position back to actual text position
                        var actualStartPos = -1;
                        var actualEndPos = -1;
                        var normPos = 0;
                        var inWhitespace = false;

                        for (var i = 0; i < fullText.length; i++) {{
                            var char = fullText[i];
                            var isWhitespace = /\\s/.test(char);

                            if (!isWhitespace) {{
                                if (normPos === index && actualStartPos === -1) {{
                                    actualStartPos = i;
                                }}
                                normPos++;
                                if (normPos === index + normalizedSearch.length) {{
                                    actualEndPos = i + 1;
                                    break;
                                }}
                                inWhitespace = false;
                            }} else if (!inWhitespace && normPos > 0 && normPos < index + normalizedSearch.length) {{
                                normPos++;
                                inWhitespace = true;
                            }}
                        }}

                        if (actualStartPos === -1 || actualEndPos === -1) {{
                            console.error('Could not map positions');
                            return false;
                        }}

                        console.log('Actual positions:', actualStartPos, '-', actualEndPos);

                        // Get all text nodes in the element
                        var walker = doc.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
                        var textNodes = [];
                        while (walker.nextNode()) {{
                            textNodes.push(walker.currentNode);
                        }}

                        // Highlight across text nodes
                        var currentPos = 0;
                        var highlighted = false;

                        for (var i = 0; i < textNodes.length; i++) {{
                            var node = textNodes[i];
                            var nodeLength = node.textContent.length;
                            var nodeEnd = currentPos + nodeLength;

                            // Check if this node overlaps with target range
                            if (currentPos < actualEndPos && nodeEnd > actualStartPos) {{
                                var highlightStart = Math.max(0, actualStartPos - currentPos);
                                var highlightEnd = Math.min(nodeLength, actualEndPos - currentPos);

                                var beforeText = node.textContent.substring(0, highlightStart);
                                var matchText = node.textContent.substring(highlightStart, highlightEnd);
                                var afterText = node.textContent.substring(highlightEnd);

                                var parent = node.parentNode;

                                if (beforeText) {{
                                    var before = doc.createTextNode(beforeText);
                                    parent.insertBefore(before, node);
                                }}

                                var highlight = doc.createElement('span');
                                highlight.className = 'tts-highlight';
                                // Use inline style but derive colors from host variables if possible
                                var bg = getComputedStyle(document.documentElement).getPropertyValue('--tts-highlight-bg') || 'rgba(255,235,59,0.95)';
                                var fg = getComputedStyle(document.documentElement).getPropertyValue('--tts-highlight-text') || '#000000';
                                highlight.style.backgroundColor = bg.trim();
                                highlight.style.color = fg.trim();
                                highlight.style.padding = '2px 0';
                                highlight.textContent = matchText;
                                parent.insertBefore(highlight, node);

                                if (afterText) {{
                                    var after = doc.createTextNode(afterText);
                                    parent.insertBefore(after, node);
                                }}

                                parent.removeChild(node);

                                // Scroll to first highlight
                                if (!highlighted) {{
                                    highlight.scrollIntoView({{
                                        behavior: 'smooth',
                                        block: 'center'
                                    }});
                                    highlighted = true;
                                }}
                            }}

                            currentPos = nodeEnd;
                        }}

                        return highlighted;
                    }}

                    // Search in block elements
                    var blockElements = doc.querySelectorAll('p, h1, h2, h3, h4, h5, h6, div, li, td, th, blockquote');
                    for (var i = 0; i < blockElements.length; i++) {{
                        if (highlightInElement(blockElements[i])) {{
                            console.log('Successfully highlighted in:', blockElements[i].tagName);
                            break;
                        }}
                    }}
                }} catch(e) {{
                    console.error('Error highlighting sentence:', e, e.stack);
                }}
            }})();
            """

        try:
            self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)

    def on_tts_play(self, button):
        """Play/Pause toggle button handler.
           - If not playing, this triggers extraction and starts TTS (play)
           - If playing and not paused -> pause
           - If playing and paused -> resume
        """
        # If TTS not started yet, treat as Play: request page text
        if not (self.tts_engine and self.tts_engine.is_playing):
            # Start TTS: get page text
            js_code = """
            (function() {
                try {
                    var iframe = document.querySelector('#viewer iframe');
                    var targetDoc = (iframe && iframe.contentDocument) ? iframe.contentDocument : document;
                    var body = targetDoc.body;

                    // Extract text with structure preservation
                    function extractStructuredText(element) {
                        var sentences = [];

                        function getTextContent(node) {
                            if (node.nodeType === Node.TEXT_NODE) {
                                return node.textContent;
                            } else if (node.nodeType === Node.ELEMENT_NODE) {
                                var text = '';
                                for (var i = 0; i < node.childNodes.length; i++) {
                                    text += getTextContent(node.childNodes[i]);
                                }
                                return text;
                            }
                            return '';
                        }

                        function traverse(node) {
                            if (node.nodeType === Node.ELEMENT_NODE) {
                                var tagName = node.tagName.toLowerCase();

                                // For block elements, get their complete text content
                                if (['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'blockquote', 'pre'].indexOf(tagName) !== -1) {
                                    var text = getTextContent(node).trim();
                                    if (text) {
                                        sentences.push(text);
                                    }
                                    return;
                                }

                                for (var i = 0; i < node.childNodes.length; i++) {
                                    traverse(node.childNodes[i]);
                                }
                            }
                        }

                        traverse(element);
                        return sentences;
                    }

                    var blockTexts = extractStructuredText(body);

                    var allSentences = [];
                    blockTexts.forEach(function(blockText) {
                        var parts = blockText.split(/([.!?]+(?:\\s+|$))/);
                        for (var i = 0; i < parts.length - 1; i += 2) {
                            var sentence = parts[i] + (parts[i+1] || '');
                            sentence = sentence.trim();
                            if (sentence) {
                                allSentences.push(sentence);
                            }
                        }
                        if (parts.length % 2 === 1 && parts[parts.length - 1].trim()) {
                            allSentences.push(parts[parts.length - 1].trim());
                        }
                    });

                    var text = JSON.stringify(allSentences);

                    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pageText) {
                        window.webkit.messageHandlers.pageText.postMessage(text);
                    } else {
                        window.postMessage({ type: 'pageText', payload: text }, '*');
                    }
                } catch(e) {
                    console.error('Error getting page text:', e);
                }
            })();
            """
            try:
                self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
            except TypeError:
                self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)
            # When pageText arrives, tts_engine.speak_sentences_list will be called by on_page_text.
            return

        # If we are playing currently, toggle pause/resume
        if self.tts_engine.is_playing and not self.tts_engine.paused:
            # Pause
            self.tts_engine.pause()
            # update icon to play
            try:
                self.tts_play_button.set_icon_name("media-playback-start-symbolic")
            except Exception:
                pass
            # While paused we want to synthesize only next 2 sentences; engine respects that automatically.
            return
        elif self.tts_engine.is_playing and self.tts_engine.paused:
            # Resume
            self.tts_engine.resume()
            try:
                self.tts_play_button.set_icon_name("media-playback-pause-symbolic")
            except Exception:
                pass
            return

    def on_tts_prev(self, button):
        """Go to previous sentence while TTS is playing"""
        if self.tts_engine:
            self.tts_engine.prev_sentence()

    def on_tts_next(self, button):
        """Go to next sentence while TTS is playing"""
        if self.tts_engine:
            self.tts_engine.next_sentence()

    def on_tts_stop(self, button):
        """Stop TTS playback"""
        if self.tts_engine:
            self.tts_engine.stop()
        # Clear highlights
        self.highlight_sentence(-1, "")
        self.on_tts_finished()

    def on_tts_finished(self):
        """Called when TTS finishes"""
        try:
            self.tts_play_button.set_sensitive(True)
            self.tts_play_button.set_icon_name("media-playback-start-symbolic")
            self.tts_stop_button.set_sensitive(False)
            if hasattr(self, 'tts_prev_button'):
                self.tts_prev_button.set_sensitive(False)
            if hasattr(self, 'tts_next_button'):
                self.tts_next_button.set_sensitive(False)
        except Exception:
            pass

    def clear_toc(self):
        """Clear the Table of Contents sidebar (remove rows, disconnect handler, hide sidebar)."""
        try:
            # Remove all children from the listbox (works for both ListBox and Box containers)
            child = self.toc_listbox.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                try:
                    self.toc_listbox.remove(child)
                except Exception:
                    # fallback for different GTK versions / child removal methods
                    try:
                        child.unparent()
                    except Exception:
                        pass
                child = next_child
        except Exception:
            # best-effort; ignore errors to avoid breaking flow
            pass

        # Try disconnecting the row-activated handler if it was connected
        try:
            self.toc_listbox.disconnect_by_func(self.on_toc_row_activated)
        except Exception:
            pass

        # Hide the sidebar (no TOC to show)
        try:
            self.toc_sidebar.set_visible(False)
        except Exception:
            pass

    def populate_toc(self, toc_data, parent_box=None, level=0):
        """Recursively populate TOC with nested items"""
        if parent_box is None:
            print(f"[DEBUG] populate_toc called with {len(toc_data)} items")

            child = self.toc_listbox.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.toc_listbox.remove(child)
                child = next_child

            parent_box = self.toc_listbox

        for item in toc_data:
            label_text = item.get('label', '').strip() if isinstance(item, dict) else str(item).strip()
            href = item.get('href', '') if isinstance(item, dict) else ''
            subitems = item.get('subitems', []) if isinstance(item, dict) else []

            if not label_text:
                label_text = 'Unknown'

            if subitems and len(subitems) > 0:
                row = Gtk.ListBoxRow()
                row.set_activatable(bool(href))

                expander = Gtk.Expander()
                expander.set_label(label_text)
                expander.set_margin_start(12 + (level * 16))
                expander.set_margin_end(12)
                expander.set_margin_top(4)
                expander.set_margin_bottom(4)

                subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                expander.set_child(subitems_box)

                row.set_child(expander)

                if href:
                    row.href = href
                    expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))

                parent_box.append(row)

                for subitem in subitems:
                    self.add_toc_subitem(subitems_box, subitem, level + 1)

            else:
                row = Gtk.ListBoxRow()
                row.set_activatable(True)

                label = Gtk.Label(
                    label=label_text,
                    halign=Gtk.Align.START,
                    margin_top=6,
                    margin_bottom=6,
                    margin_start=12 + (level * 16),
                    margin_end=12,
                    wrap=True,
                    xalign=0
                )

                row.set_child(label)

                if href:
                    row.href = href

                parent_box.append(row)

        if parent_box == self.toc_listbox:
            try:
                self.toc_listbox.disconnect_by_func(self.on_toc_row_activated)
            except:
                pass
            self.toc_listbox.connect('row-activated', self.on_toc_row_activated)

            self.toc_sidebar.set_visible(True)
            print(f"[DEBUG] TOC sidebar visible, {len(toc_data)} items added")

    def add_toc_subitem(self, parent_box, item, level):
        """Add a single TOC subitem"""
        label_text = item.get('label', '').strip() if isinstance(item, dict) else str(item).strip()
        href = item.get('href', '') if isinstance(item, dict) else ''
        subitems = item.get('subitems', []) if isinstance(item, dict) else []

        if not label_text:
            label_text = 'Unknown'

        if subitems and len(subitems) > 0:
            expander = Gtk.Expander()
            expander.set_label(label_text)
            expander.set_margin_start(level * 16)
            expander.set_margin_end(12)
            expander.set_margin_top(2)
            expander.set_margin_bottom(2)

            subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            expander.set_child(subitems_box)

            if href:
                expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))

            parent_box.append(expander)

            for subitem in subitems:
                self.add_toc_subitem(subitems_box, subitem, level + 1)
        else:
            button = Gtk.Button(label=label_text)
            button.set_has_frame(False)
            button.set_halign(Gtk.Align.START)
            button.set_margin_start(level * 16)
            button.set_margin_end(12)
            button.set_margin_top(2)
            button.set_margin_bottom(2)

            child = button.get_child()
            if child and isinstance(child, Gtk.Label):
                child.set_wrap(True)
                child.set_xalign(0)

            if href:
                button.connect('clicked', lambda b, h=href: self.go_to_chapter(h))

            parent_box.append(button)

    def on_toc_row_activated(self, listbox, row):
        """Handle TOC item click"""
        if hasattr(row, 'href') and row.href:
            print(f"[DEBUG] Navigating to: {row.href}")
            self.go_to_chapter(row.href)

    def go_to_chapter(self, href):
        """Navigate to a chapter by href"""
        escaped_href = href.replace("'", "\\'")
        js_code = f"if(window.rendition) {{ window.rendition.display('{escaped_href}'); }}"
        print(f"[DEBUG] Executing JS: {js_code}")
        try:
            self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)

    def toggle_toc(self, button):
        is_collapsed = self.split_view.get_collapsed()
        self.split_view.set_collapsed(not is_collapsed)

    def go_prev(self, button):
        js = "if(window.rendition) window.rendition.prev();"
        try:
            self.webview.evaluate_javascript(js, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js, len(js), None, None, None, None, None, None)

    def go_next(self, button):
        js = "if(window.rendition) window.rendition.next();"
        try:
            self.webview.evaluate_javascript(js, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js, len(js), None, None, None, None, None, None)

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        filter = Gtk.FileFilter()
        filter.set_name("EPUB and HTML files")
        filter.add_pattern("*.epub")
        filter.add_pattern("*.html")
        filter.add_pattern("*.htm")
        dialog.set_default_filter(filter)
        dialog.open(self, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            self.load_file(file.get_path())
        except GLib.Error:
            pass

    def load_file(self, file_path):
        # clear previous TOC immediately when switching files
        try:
            self.clear_toc()
        except Exception:
            pass
        # Stop and clear any running TTS / synthesis before loading new file (requirement 2)
        try:
            if self.tts_engine:
                self.tts_engine.stop()
            # clear highlights
            try:
                self.highlight_sentence(-1, "")
            except Exception:
                pass
            # disable TTS controls while new file loads
            try:
                if hasattr(self, 'tts_prev_button'):
                    self.tts_prev_button.set_sensitive(False)
                if hasattr(self, 'tts_next_button'):
                    self.tts_next_button.set_sensitive(False)
                if hasattr(self, 'tts_stop_button'):
                    self.tts_stop_button.set_sensitive(False)
                if hasattr(self, 'tts_play_button'):
                    self.tts_play_button.set_sensitive(True)
                    self.tts_play_button.set_icon_name("media-playback-start-symbolic")
            except Exception:
                pass
        except Exception:
            pass

        file_ext = pathlib.Path(file_path).suffix.lower()
        if file_ext == '.epub':
            self.load_epub(file_path)
        elif file_ext in ['.html', '.htm']:
            self.load_html(file_path)
        else:
            print(f"Unsupported file type: {file_ext}")

    def load_epub(self, epub_path):
        import zipfile
        import shutil

        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"[warn] Failed to clean up temp dir: {e}")

        self.temp_dir = tempfile.mkdtemp(prefix="epub_reader_")
        try:
            with zipfile.ZipFile(epub_path, 'r') as zip_ref:
                zip_ref.extractall(self.temp_dir)
            print(f"[info] Extracted EPUB to: {self.temp_dir}")
        except Exception as e:
            print(f"[error] Failed to extract EPUB: {e}")
            return

        extracted_uri = pathlib.Path(self.temp_dir).as_uri()

        if LOCAL_JSZIP.exists():
            jszip_snippet = f"<script>{LOCAL_JSZIP.read_text(encoding='utf-8')}</script>"
            jszip_note = "[local]"
        else:
            jszip_snippet = '<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>'
            jszip_note = "[cdn]"

        if LOCAL_EPUBJS.exists():
            epubjs_snippet = f"<script>{LOCAL_EPUBJS.read_text(encoding='utf-8')}</script>"
            epubjs_note = "[local]"
        else:
            epubjs_snippet = '<script src="https://cdn.jsdelivr.net/npm/epubjs@0.3.92/dist/epub.min.js"></script>'
            epubjs_note = "[cdn]"

        epub_uri = pathlib.Path(epub_path).as_uri()

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPUB Reader</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    /* Use CSS variables so the viewer can follow system dark/light automatically */
    :root {{
      --viewer-bg: #ffffff;
      --viewer-text: #000000;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --viewer-bg: #0f1113;
        --viewer-text: #e6e6e6;
      }}
    }}
    html, body {{ height: 100%; margin: 0; padding: 0; background: var(--viewer-bg); color: var(--viewer-text); }}
    #viewer {{ width: 100vw; height: 100vh; background: transparent; color: inherit; }}
    .epubjs-navigation {{ display: none; }}
    /* Ensure any injected highlight picks up variables */
    .tts-highlight {{ background: var(--tts-highlight-bg, rgba(255,235,59,0.95)); color: var(--tts-highlight-text, #000); }}
  </style>
</head>
<body>
  <div id="viewer"></div>

  {jszip_snippet}
  {epubjs_snippet}

  <script>
    (function(){{
      console.log("Libraries: JSZip {jszip_note}, epub.js {epubjs_note}");
      const epubUrl = "{epub_uri}";
      const extractedPath = "{extracted_uri}";

      function sendTOCString(toc) {{
        try {{
          var payload = JSON.stringify(toc);
          console.log("Sending TOC:", payload.substring(0, 200));
          if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tocLoaded) {{
            window.webkit.messageHandlers.tocLoaded.postMessage(payload);
          }} else {{
            window.postMessage({{ type: 'tocLoaded', payload: payload }}, '*');
          }}
        }} catch (e) {{ console.error('sendTOCString error', e); }}
      }}

      function sendNavString(nav) {{
        try {{
          var payload = JSON.stringify(nav);
          if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.navChanged) {{
            window.webkit.messageHandlers.navChanged.postMessage(payload);
          }} else {{
            window.postMessage({{ type: 'navChanged', payload: payload }}, '*');
          }}
        }} catch (e) {{ console.error('sendNavString error', e); }}
      }}

      try {{
        const book = ePub(epubUrl);
        
        window.rendition = book.renderTo("viewer", {{ 
          width: "100%", 
          height: "100%"
        }});
        
        window.rendition.display();

        book.loaded.navigation.then(function(nav){{
          console.log("epub.js nav:", nav);
          var toc = nav.toc || nav;
          console.log("Extracted TOC:", toc);
          sendTOCString(toc);
        }}).catch(function(err) {{
          console.error("Navigation loading error:", err);
        }});

        window.rendition.on('relocated', function(location) {{
          sendNavString({{ 
            current: location.start.cfi, 
            percent: Math.round(location.start.percentage * 100) 
          }});
        }});

        window.goToChapter = function(href) {{ 
          console.log("goToChapter called with:", href);
          window.rendition.display(href); 
        }};
      }} catch (e) {{
        console.error("EPUB rendering error:", e);
        document.body.innerHTML = "<h2>EPUB rendering error</h2><pre>" + String(e) + "</pre>";
      }}
    }})();
  </script>
</body>
</html>"""

        self.webview.load_html(html_content, "file:///")

    def __del__(self):
        """Cleanup temp directory on destruction"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                import shutil
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

    def load_html(self, html_path):
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            base_uri = f"file://{pathlib.Path(html_path).parent.absolute()}/"
            # Because we inject theme CSS via user script, arbitrary HTML files will pick up system theme.
            self.webview.load_html(html_content, base_uri)
        except Exception as e:
            print(f"Error loading HTML file: {e}")
            self.webview.load_html(f"<html><body><h1>Error loading file: {e}</h1></body></html>", "file:///")

    def encode_file(self, file_path):
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')


class EpubReader(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        file_path = None
        if len(sys.argv) > 1:
            file_path = sys.argv[1]
        win = EpubViewerWindow(application=app, file_path=file_path)
        win.present()

def main():
    app = EpubReader()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()


