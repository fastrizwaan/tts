#!/usr/bin/env python3
"""
EPUB reader using ebooklib + WebKitGTK6 with TTS support using Kokoro

Replaces epub.js with ebooklib for EPUB parsing while maintaining TTS functionality.
"""
import os, json, tempfile, shutil, signal, sys, threading, time, pathlib, base64, html
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, Gst

import soundfile as sf
import tempfile as _tempfile
from ebooklib import epub
from bs4 import BeautifulSoup
import re

try:
    from kokoro_onnx import Kokoro
except Exception:
    Kokoro = None

Adw.init()

TTS_AVAILABLE = Kokoro is not None

# -----------------------
# TTSEngine (unchanged from original)
# -----------------------
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

        if TTS_AVAILABLE:
            try:
                model_path = kokoro_model_path or "/app/share/kokoro-models/kokoro-v1.0.onnx"
                voices_path = voices_bin_path or "/app/share/kokoro-models/voices-v1.0.bin"
                if not os.path.exists(model_path):
                    model_path = os.path.expanduser("~/.local/share/kokoro-models/kokoro-v1.0.onnx")
                    voices_path = os.path.expanduser("~/.local/share/kokoro-models/voices-v1.0.bin")
                if os.path.exists(model_path) and os.path.exists(voices_path):
                    self.kokoro = Kokoro(model_path, voices_path)
                    print("[info] Kokoro TTS initialized")
                else:
                    print(f"[warn] Kokoro models not found (tried {model_path})")
            except Exception as e:
                print(f"[error] Failed to initialize Kokoro: {e}")
                self.kokoro = None

        try:
            Gst.init(None)
            self.player = Gst.ElementFactory.make("playbin", "player")
            bus = self.player.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_gst_message)
            self.playback_finished = False
        except Exception as e:
            print(f"[warn] GStreamer init failed: {e}")
            self.player = None
            self.playback_finished = True

    def is_playing(self):
        return bool(self.is_playing_flag) and not bool(self.paused)

    def is_paused(self):
        return bool(self.paused)

    def on_gst_message(self, bus, message):
        try:
            t = message.type
            if t == Gst.MessageType.EOS:
                if self.player:
                    self.player.set_state(Gst.State.NULL)
                self.playback_finished = True
            elif t == Gst.MessageType.ERROR:
                if self.player:
                    self.player.set_state(Gst.State.NULL)
                err, debug = message.parse_error()
                print(f"[error] GStreamer error: {err}, {debug}")
                self.playback_finished = True
        except Exception as e:
            print("on_gst_message error:", e)

    def split_sentences(self, text):
        sentences = re.split(r'([.!?]+(?:\s+|$))', text)
        result = []
        for i in range(0, len(sentences)-1, 2):
            sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else '')
            sentence = sentence.strip()
            if sentence:
                result.append(sentence)
        if len(sentences) % 2 == 1 and sentences[-1].strip():
            result.append(sentences[-1].strip())
        return result

    def synthesize_sentence(self, sentence, voice, speed, lang):
        if not self.kokoro:
            return None
        try:
            base = self.base_temp_dir or tempfile.gettempdir()
            try:
                os.makedirs(base, exist_ok=True)
            except Exception:
                base = tempfile.gettempdir()

            samples, sample_rate = self.kokoro.create(sentence, voice=voice, speed=speed, lang=lang)

            ntf = _tempfile.NamedTemporaryFile(prefix="tts_", suffix=".wav", delete=False, dir=base)
            ntf_name = ntf.name
            ntf.close()
            sf.write(ntf_name, samples, sample_rate)
            return ntf_name
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
        self._cancel_delayed_timer()

        def timer_cb():
            try:
                if self.should_stop:
                    return
                with self._audio_lock:
                    if self._audio_files.get(idx):
                        return
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

    def speak_sentences_list(self, sentences_with_meta, voice="af_sarah", speed=1.0, lang="en-us",
                            highlight_callback=None, finished_callback=None):
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

        def tts_thread():
            try:
                total = len(self._tts_sentences)
                print(f"[TTS] Speaking {total} sentences")

                def synthesis_worker():
                    try:
                        synth_idx = 0
                        while not self.should_stop and synth_idx < total:
                            with self._audio_lock:
                                cur = self._current_play_index

                            if synth_idx < cur:
                                synth_idx = cur

                            lookahead_limit = cur + (1 if self.paused else 3)

                            if synth_idx > lookahead_limit:
                                time.sleep(0.05)
                                continue

                            with self._audio_lock:
                                if self._audio_files.get(synth_idx):
                                    synth_idx += 1
                                    continue

                            if synth_idx <= lookahead_limit:
                                if self.should_stop:
                                    break
                                print(f"[TTS] Pre-synthesizing sentence {synth_idx+1}/{total}")
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
                        self._synthesis_done.set()

                synth_thread = threading.Thread(target=synthesis_worker, daemon=True)
                synth_thread.start()

                self.is_playing_flag = True

                while self._current_play_index < total and not self.should_stop:
                    idx = self._current_play_index

                    if self._tts_highlight_callback:
                        GLib.idle_add(self._tts_highlight_callback, idx, {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]})

                    while self.paused and not self.should_stop:
                        self._cancel_delayed_timer()
                        self._resume_event.wait(0.1)

                    if self.should_stop:
                        break

                    audio_file = None
                    with self._audio_lock:
                        audio_file = self._audio_files.get(idx)

                    if not audio_file:
                        self._schedule_delayed_synthesis(idx, delay=0.5)
                        waited = 0.0
                        while not self.should_stop:
                            with self._audio_lock:
                                audio_file = self._audio_files.get(idx)
                            if audio_file:
                                break
                            if self._current_play_index != idx:
                                break
                            time.sleep(0.02)
                            waited += 0.02
                            if self._synthesis_done.is_set() and waited > 0.5:
                                break

                    if self.should_stop:
                        break

                    with self._audio_lock:
                        audio_file = self._audio_files.get(idx)

                    if not audio_file:
                        print(f"[TTS] On-demand synth (fallback) for sentence {idx+1}")
                        audio_file = self.synthesize_sentence(self._tts_sentences[idx], self._tts_voice, self._tts_speed, self._tts_lang)
                        if audio_file:
                            with self._audio_lock:
                                self._audio_files[idx] = audio_file

                    if not audio_file:
                        print(f"[warn] No audio for index {idx}, skipping")
                        self._current_play_index = idx + 1
                        continue

                    if self.paused:
                        continue

                    print(f"[TTS] Playing sentence {idx+1}/{total}")
                    if self.player:
                        try:
                            self.player.set_property("uri", f"file://{audio_file}")
                            self.player.set_state(Gst.State.PLAYING)
                            self.playback_finished = False
                        except Exception as e:
                            print("player start error:", e)
                            self.playback_finished = True
                    else:
                        self.playback_finished = True
                        time.sleep(0.05)

                    while not self.playback_finished and not self.should_stop:
                        if self._current_play_index != idx:
                            break
                        if self.paused:
                            try:
                                if self.player:
                                    self.player.set_state(Gst.State.NULL)
                            except Exception:
                                pass
                            break
                        time.sleep(0.02)

                    try:
                        if self.player:
                            self.player.set_state(Gst.State.NULL)
                    except Exception:
                        pass

                    if (self._current_play_index == idx) and (not self.paused):
                        try:
                            with self._audio_lock:
                                af = self._audio_files.get(idx)
                                if af:
                                    try:
                                        os.remove(af)
                                    except Exception:
                                        pass
                                    try:
                                        del self._audio_files[idx]
                                    except KeyError:
                                        pass
                        except Exception:
                            pass
                        self._current_play_index = idx + 1

                self.is_playing_flag = False
                self._cancel_delayed_timer()
                if self._tts_highlight_callback and not self.should_stop:
                    GLib.idle_add(self._tts_highlight_callback, -1, {"sid": None, "text": ""})

                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

            except Exception as e:
                print(f"[error] TTS thread error: {e}")
                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

        self.current_thread = threading.Thread(target=tts_thread, daemon=True)
        self.current_thread.start()

    def next_sentence(self):
        if not self._tts_sentences:
            return
        with self._audio_lock:
            self._current_play_index = min(len(self._tts_sentences)-1, self._current_play_index + 1)
            idx = self._current_play_index
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx, {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]})
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def prev_sentence(self):
        if not self._tts_sentences:
            return
        with self._audio_lock:
            self._current_play_index = max(0, self._current_play_index - 1)
            idx = self._current_play_index
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx, {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]})
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def pause(self):
        print("[TTS] Pausing TTS")
        self.paused = True
        self._resume_event.clear()
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass

    def resume(self):
        print("[TTS] Resuming TTS")
        self.paused = False
        self._resume_event.set()
        self._cancel_delayed_timer()

    def stop(self):
        self.should_stop = True
        self.paused = False
        self.playback_finished = True
        try:
            self._resume_event.set()
        except Exception:
            pass

        self._cancel_delayed_timer()

        if self.player:
            try:
                self.player.set_state(Gst.State.NULL)
            except Exception:
                pass

        self.is_playing_flag = False

        if self.current_thread:
            try:
                self.current_thread.join(timeout=1.0)
            except Exception:
                pass

        try:
            with self._audio_lock:
                for idx, path in list(self._audio_files.items()):
                    try:
                        if path and os.path.exists(path):
                            os.remove(path)
                    except Exception:
                        pass
                self._audio_files.clear()
        except Exception:
            pass

# -----------------------
# EpubViewer (modified to use ebooklib)
# -----------------------
class EpubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)

        self.current_book_path = None
        self.current_chapter_index = 0
        self.book = None
        self.chapters = []
        self.temp_dir = None
        self.tts = None
        self._last_tts_sids = []
        self._last_tts_texts = []

        self.setup_ui()
        self.setup_navigation()

    def setup_ui(self):
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)
        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.main_box)

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

        self.page_info = Gtk.Label()
        self.page_info.set_text("--/--")
        self.page_info.add_css_class("dim-label")
        self.page_info.set_margin_start(6)
        self.page_info.set_margin_end(6)
        nav_box.append(self.page_info)

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

        self.tts_prev_btn = Gtk.Button()
        self.tts_prev_btn.set_icon_name("media-skip-backward-symbolic")
        self.tts_prev_btn.set_tooltip_text("Previous sentence")
        self.tts_prev_btn.add_css_class("flat")
        self.tts_prev_btn.connect("clicked", self.on_tts_prev)
        self.tts_prev_btn.set_sensitive(False)

        self.tts_next_btn = Gtk.Button()
        self.tts_next_btn.set_icon_name("media-skip-forward-symbolic")
        self.tts_next_btn.set_tooltip_text("Next sentence")
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
        except AttributeError:
            button_box_start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_start.set_spacing(6)
            button_box_start.append(open_button)
            button_box_start.append(nav_box)
            header_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            header_content.set_hexpand(True)
            header_content.append(button_box_start)
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            header_content.append(spacer)
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

        GLib.timeout_add(500, self._update_tts_button_states)

    def _update_tts_button_states(self):
        if not self.tts:
            ok = bool(self.current_book_path and TTS_AVAILABLE)
            self.tts_play_btn.set_sensitive(ok)
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            self.tts_prev_btn.set_sensitive(False)
            self.tts_next_btn.set_sensitive(False)
            return True
        
        is_playing = self.tts.is_playing()
        is_paused = self.tts.is_paused()
        
        if not is_playing and not is_paused:
            self.tts_play_btn.set_sensitive(bool(self.current_book_path))
            self.tts_pause_btn.set_sensitive(False)
            self.tts_stop_btn.set_sensitive(False)
            self.tts_prev_btn.set_sensitive(False)
            self.tts_next_btn.set_sensitive(False)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_playing and not is_paused:
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_prev_btn.set_sensitive(True)
            self.tts_next_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-pause-symbolic")
        elif is_paused:
            self.tts_play_btn.set_sensitive(False)
            self.tts_pause_btn.set_sensitive(True)
            self.tts_stop_btn.set_sensitive(True)
            self.tts_prev_btn.set_sensitive(True)
            self.tts_next_btn.set_sensitive(True)
            self.tts_pause_btn.set_icon_name("media-playback-start-symbolic")
        
        return True

    def setup_navigation(self):
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book_path:
            return False
        
        if keyval == 65361 or keyval == 65365:  # Left / PageUp
            self.on_prev_chapter(None)
            return True
        elif keyval == 65363 or keyval == 65366:  # Right / PageDown
            self.on_next_chapter(None)
            return True
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
            if self.tts:
                try:
                    print("[info] Stopping TTS before loading new book")
                    self.tts.stop()
                    time.sleep(0.05)
                except Exception as e:
                    print("Error stopping TTS before load:", e)
                self.tts = None

            if self.temp_dir and os.path.exists(self.temp_dir):
                try:
                    shutil.rmtree(self.temp_dir)
                except Exception as e:
                    print("Warning: failed to rmtree previous temp_dir:", e)

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
            self.book = epub.read_epub(filepath)
            self.chapters = list(self.book.get_items_of_type(9))  # 9 = ITEM_DOCUMENT
            
            if not self.chapters:
                self.show_error("No readable chapters found in EPUB")
                return
                
            self.current_chapter_index = 0
            self.display_chapter(0)
            
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")
            import traceback
            traceback.print_exc()

    def extract_text_from_html(self, html_content):
        """Extract sentences from HTML content for TTS"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            sentences = []
            
            # Target tags for text extraction
            target_tags = ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 
                          'blockquote', 'figcaption', 'caption', 'dt', 'dd', 'td', 'th']
            
            for tag in soup.find_all(target_tags):
                text = tag.get_text(separator=' ', strip=True)
                if text:
                    # Split into sentences
                    sent_list = re.split(r'([.!?]+(?:\s+|$))', text)
                    for i in range(0, len(sent_list)-1, 2):
                        sentence = sent_list[i] + (sent_list[i+1] if i+1 < len(sent_list) else '')
                        sentence = sentence.strip()
                        if sentence:
                            # Create stable ID for sentence
                            sid = self._generate_sid(sentence)
                            sentences.append({"sid": sid, "text": sentence})
                    
                    if len(sent_list) % 2 == 1 and sent_list[-1].strip():
                        sentence = sent_list[-1].strip()
                        sid = self._generate_sid(sentence)
                        sentences.append({"sid": sid, "text": sentence})
            
            return sentences
        except Exception as e:
            print(f"Error extracting text: {e}")
            return []

    def _generate_sid(self, text):
        """Generate stable ID for text"""
        hash_val = 0
        for char in text:
            hash_val = ((hash_val << 5) - hash_val) + ord(char)
            hash_val = hash_val & hash_val
        return format(abs(hash_val) & 0xFFFFFFFF, 'x')[:12]

    def inject_tts_spans(self, html_content, sentences):
        """Inject TTS span tags into HTML content"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Create a mapping of text to sentence data
            text_to_sid = {sent_data['text']: sent_data['sid'] for sent_data in sentences}
            
            # Process all text nodes
            for element in soup.find_all(string=True):
                if element.parent.name in ['script', 'style', 'head']:
                    continue
                
                # Skip if already wrapped
                if element.parent.name == 'span' and element.parent.get('data-tts-id'):
                    continue
                
                text_content = str(element)
                modified = False
                new_contents = []
                last_end = 0
                
                # Find all sentence matches in this text node
                for sentence_text, sid in text_to_sid.items():
                    idx = text_content.find(sentence_text, last_end)
                    if idx != -1:
                        # Add text before match
                        if idx > last_end:
                            new_contents.append(text_content[last_end:idx])
                        
                        # Create span for matched sentence
                        new_tag = soup.new_tag('span')
                        new_tag['data-tts-id'] = sid
                        new_tag.string = sentence_text
                        new_contents.append(new_tag)
                        
                        last_end = idx + len(sentence_text)
                        modified = True
                
                # Add remaining text
                if last_end < len(text_content):
                    new_contents.append(text_content[last_end:])
                
                # Replace the text node with new contents
                if modified and new_contents:
                    parent = element.parent
                    # Insert new contents before the old element
                    for item in new_contents:
                        if isinstance(item, str):
                            parent.insert(parent.contents.index(element), item)
                        else:
                            parent.insert(parent.contents.index(element), item)
                    # Remove the old element
                    element.extract()
            
            return str(soup)
        except Exception as e:
            print(f"Error injecting TTS spans: {e}")
            import traceback
            traceback.print_exc()
            # Return original content if injection fails
            return html_content

    def display_chapter(self, index):
        """Display a chapter using ebooklib"""
        if not self.book or not self.chapters:
            return
        
        if index < 0 or index >= len(self.chapters):
            return
        
        self.current_chapter_index = index
        chapter = self.chapters[index]
        
        try:
            content = chapter.get_content().decode('utf-8', errors='ignore')
            
            # Extract sentences for TTS
            self._last_tts_sentences = self.extract_text_from_html(content)
            self._last_tts_sids = [s['sid'] for s in self._last_tts_sentences]
            self._last_tts_texts = [s['text'] for s in self._last_tts_sentences]
            
            # Inject TTS spans
            content = self.inject_tts_spans(content, self._last_tts_sentences)
            
            # Get chapter title
            title = "Untitled"
            if hasattr(chapter, 'title') and chapter.title:
                title = chapter.title
            elif hasattr(chapter, 'file_name'):
                title = chapter.file_name
            
            # Create full HTML page with proper DOCTYPE and CSS
            html_page = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                max-width: 800px;
                margin: 0 auto;
                padding: 40px 20px;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
                font-size: 18px;
                line-height: 1.6;
                color: #111;
                background-color: #fafafa;
            }}
            
            /* Heading styles with bold weight */
            h1, h2, h3, h4, h5, h6 {{
                margin: 1.5em 0 0.5em 0;
                line-height: 1.3;
                font-weight: 700 !important;
            }}
            h1 {{ font-size: 2.0em !important; font-weight: 700 !important; }}
            h2 {{ font-size: 1.75em !important; font-weight: 700 !important; }}
            h3 {{ font-size: 1.5em !important; font-weight: 700 !important; }}
            h4 {{ font-size: 1.25em !important; font-weight: 700 !important; }}
            h5 {{ font-size: 1.1em !important; font-weight: 700 !important; }}
            h6 {{ font-size: 1.0em !important; font-weight: 700 !important; }}
            
            p {{ margin: 1em 0; }}
            img {{ max-width: 100%; height: auto; }}
            strong, b {{ font-weight: 700 !important; }}
            em, i {{ font-style: italic !important; }}
            
            blockquote {{
                margin: 1em 0;
                padding-left: 1em;
                border-left: 3px solid #ccc;
                font-style: italic;
            }}
            
            ul, ol {{ margin: 1em 0; padding-left: 2em; }}
            li {{ margin: 0.5em 0; }}
            
            .tts-highlight {{
                background: rgba(255, 215, 0, 0.75) !important;
                box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.35) !important;
                color: #000 !important;
                border-radius: 2px;
            }}
            
            @media (prefers-color-scheme: dark) {{
                body {{ 
                    background-color: #242424; 
                    color: #e3e3e3; 
                }}
                blockquote {{
                    border-left-color: #555;
                }}
                .tts-highlight {{
                    background: rgba(255, 215, 0, 0.9) !important;
                    box-shadow: 0 0 0 2px rgba(255, 215, 0, 0.75) !important;
                    color: #000 !important;
                }}
                a {{ color: #6598eb !important; }}
            }}
            a {{ color: #0b66ff; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        {content}
        <script>
            window._currentHighlightedSid = null;
            
            window.highlightSentence = function(sid) {{
                var old = document.querySelectorAll('.tts-highlight');
                old.forEach(function(el) {{ el.classList.remove('tts-highlight'); }});
                
                if (sid) {{
                    window._currentHighlightedSid = sid;
                    var el = document.querySelector('[data-tts-id="' + sid + '"]');
                    if (el) {{
                        el.classList.add('tts-highlight');
                        el.scrollIntoView({{behavior: 'smooth', block: 'center', inline: 'nearest'}});
                    }}
                }} else {{
                    window._currentHighlightedSid = null;
                }}
            }};
            
            window.clearHighlight = function() {{
                window._currentHighlightedSid = null;
                var old = document.querySelectorAll('.tts-highlight');
                old.forEach(function(el) {{ el.classList.remove('tts-highlight'); }});
            }};
        </script>
    </body>
    </html>"""
            
            self.webview.load_html(html_page, f"file://{self.temp_dir}/")
            
            # Update UI
            total = len(self.chapters)
            self.page_info.set_text(f"{index + 1}/{total}")
            self.chapter_label.set_text(f"Chapter {index + 1} of {total}: {title}")
            
            self.prev_chapter_btn.set_sensitive(index > 0)
            self.next_chapter_btn.set_sensitive(index < total - 1)
            
        except Exception as e:
            self.show_error(f"Error displaying chapter: {str(e)}")
            import traceback
            traceback.print_exc()
    def on_prev_chapter(self, button):
        if self.current_chapter_index > 0:
            self.display_chapter(self.current_chapter_index - 1)

    def on_next_chapter(self, button):
        if self.current_chapter_index < len(self.chapters) - 1:
            self.display_chapter(self.current_chapter_index + 1)

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
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception as e:
                print(f"Error cleaning up temp directory: {e}")

    # TTS UI handlers
    def on_tts_play(self, button):
        if not self.current_book_path or not hasattr(self, '_last_tts_sentences'):
            return
        
        if not self.tts:
            try:
                kokoro_model = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_bin = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                self.tts = TTSEngine(lambda: self.webview, self.temp_dir, kokoro_model_path=kokoro_model, voices_bin_path=voices_bin)
            except Exception as e:
                print("TTS init error:", e)
                self.show_error("TTS initialization failed.")
                return
        
        def finished_cb():
            self._clear_tts_highlight()
        
        self.tts.speak_sentences_list(
            sentences_with_meta=self._last_tts_sentences,
            voice="af_sarah",
            speed=1.0,
            lang="en-us",
            highlight_callback=self._on_tts_highlight,
            finished_callback=finished_cb
        )

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
        self._clear_tts_highlight()

    def on_tts_prev(self, button):
        if not self.tts:
            return
        self.tts.prev_sentence()

    def on_tts_next(self, button):
        if not self.tts:
            return
        self.tts.next_sentence()

    def _on_tts_highlight(self, idx, meta):
        sid = None
        try:
            sid = meta.get("sid") if meta else None
        except Exception:
            sid = None

        if idx == -1 or not sid:
            self._clear_tts_highlight()
            return

        js = f"""
        (function(){{
            try {{
                if (typeof window.highlightSentence === 'function') {{
                    window.highlightSentence("{sid}");
                }}
            }} catch(e) {{
                console.error('TTS highlight error', e);
            }}
        }})();
        """
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)

    def _clear_tts_highlight(self):
        js = """
        (function(){
            try {
                if (typeof window.clearHighlight === 'function') {
                    window.clearHighlight();
                }
            } catch(e) {
                console.error('clear highlight error', e);
            }
        })();
        """
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)

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
                    time.sleep(0.05)
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
                    time.sleep(0.05)
                except Exception:
                    pass
            w.cleanup()

if __name__ == "__main__":
    main()
