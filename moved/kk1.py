#!/usr/bin/env python3
# GTK4 + libadwaita Kokoro TTS player with sentence highlighting and transport controls
# Features: Play, Pause/Resume, Stop, Next/Prev sentence, Play from start, Play from cursor.
# Requires: pygobject (Gtk 4, Adw 1), GStreamer 1.0, kokoro-onnx, soundfile, numpy.

import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gst

# --- User-adjustable defaults -------------------------------------------------
DEFAULT_MODEL = str(Path.home() / ".local/share/app.kokoro.demo/models/kokoro-v0_19.onnx")
DEFAULT_VOICES = str(Path.home() / ".local/share/app.kokoro.demo/models/voices-v1.0.bin")
DEFAULT_VOICE = "af_heart"
DEFAULT_LANG = "en-us"
DEFAULT_SPEED = 1.0
SAMPLE_RATE = 24000

# --- TTS Engine wrapper -------------------------------------------------------
class TTSEngine:
    def __init__(self, model_path: str, voices_path: str):
        from kokoro_onnx import Kokoro  # import here to fail gracefully in UI
        self.kokoro = Kokoro(model_path, voices_path)

    def synth_to_wav(self, text: str, out_path: str, voice: str, speed: float, lang: str):
        import soundfile as sf
        samples, sr = self.kokoro.create(text, voice=voice, speed=speed, lang=lang)
        sf.write(out_path, samples, sr)

# --- Sentence segmentation ----------------------------------------------------
SENT_RE = re.compile(r"\s*(.+?)([.!?]+|\n{2,}|$)", re.DOTALL)

def split_sentences(text: str) -> List[Tuple[int, int, str]]:
    """
    Returns list of (start_index, end_index, sentence_text)
    """
    out = []
    for m in SENT_RE.finditer(text):
        s, e = m.span()
        chunk = text[s:e].strip()
        if chunk:
            # re-trim inside to get precise highlight span without leading spaces
            inner_start = s + (len(text[s:e]) - len(text[s:e].lstrip()))
            inner_end = inner_start + len(chunk)
            out.append((inner_start, inner_end, chunk))
    return out

# --- GStreamer audio player for a list of wav files --------------------------
class SentencePlayer(GObject.GObject):
    __gsignals__ = {
        "sentence-started": (GObject.SIGNAL_RUN_FIRST, None, (int,)),
        "finished": (GObject.SIGNAL_RUN_FIRST, None, ()),
        "paused": (GObject.SIGNAL_RUN_FIRST, None, ()),
        "resumed": (GObject.SIGNAL_RUN_FIRST, None, ()),
        "stopped": (GObject.SIGNAL_RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self._playbin = Gst.ElementFactory.make("playbin", None)
        self._files: List[str] = []
        self._idx = -1
        self._paused = False
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus)

    def set_files(self, files: List[str]):
        self._files = files
        self._idx = -1

    def _set_uri(self, path: str):
        uri = GLib.filename_to_uri(path, None)
        self._playbin.set_property("uri", uri)

    def play_index(self, idx: int):
        if idx < 0 or idx >= len(self._files):
            self.stop()
            return
        self._idx = idx
        self._set_uri(self._files[self._idx])
        self._playbin.set_state(Gst.State.PLAYING)
        self._paused = False
        self.emit("sentence-started", self._idx)

    def play_next(self):
        self.play_index(self._idx + 1)

    def play_prev(self):
        self.play_index(self._idx - 1)

    def pause(self):
        self._playbin.set_state(Gst.State.PAUSED)
        self._paused = True
        self.emit("paused")

    def resume(self):
        self._playbin.set_state(Gst.State.PLAYING)
        self._paused = False
        self.emit("resumed")

    def stop(self):
        self._playbin.set_state(Gst.State.NULL)
        self._paused = False
        self.emit("stopped")

    def toggle_pause(self):
        if self._paused:
            self.resume()
        else:
            self.pause()

    def _on_bus(self, bus, msg):
        t = msg.type
        if t == Gst.MessageType.EOS:
            # End of current file -> advance
            if self._idx + 1 < len(self._files):
                GLib.idle_add(self.play_next)
            else:
                self.stop()
                self.emit("finished")
        elif t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("GStreamer error:", err, dbg, file=sys.stderr)
            self.stop()
            self.emit("finished")

# --- Main App -----------------------------------------------------------------
class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kokoro TTS Player")
        self.set_default_size(1000, 700)

        # State
        self.tmpdir = tempfile.TemporaryDirectory(prefix="kokoro_tts_")
        self.sentences: List[Tuple[int, int, str]] = []
        self.wav_files: List[str] = []
        self.current_idx = -1
        self.engine: TTSEngine | None = None

        # GStreamer init
        Gst.init(None)
        self.player = SentencePlayer()
        self.player.connect("sentence-started", self.on_sentence_started)

        # UI
        self.build_ui()

    # UI construction
    def build_ui(self):
        self.tv = Gtk.TextView()
        self.tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.tv.set_left_margin(8)
        self.tv.set_right_margin(8)
        self.buf: Gtk.TextBuffer = self.tv.get_buffer()
        self.tag_current = self.buf.create_tag("current", background="#fff6b3")

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(self.tv)

        # Controls
        self.model_entry = Gtk.Entry(text=DEFAULT_MODEL)
        self.voices_entry = Gtk.Entry(text=DEFAULT_VOICES)
        self.voice_entry = Gtk.Entry(text=DEFAULT_VOICE)
        self.lang_entry = Gtk.Entry(text=DEFAULT_LANG)

        self.speed_adj = Gtk.Adjustment(lower=0.5, upper=2.0, step_increment=0.1, page_increment=0.1, value=DEFAULT_SPEED)
        self.speed_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.speed_adj)
        self.speed_scale.set_digits(2)
        self.speed_scale.set_hexpand(True)

        # Buttons
        self.btn_build = Gtk.Button(label="Build Audio")
        self.btn_play = Gtk.Button(label="Play")
        self.btn_pause = Gtk.Button(label="Pause/Resume")
        self.btn_stop = Gtk.Button(label="Stop")
        self.btn_prev = Gtk.Button(label="Prev")
        self.btn_next = Gtk.Button(label="Next")
        self.btn_from_start = Gtk.Button(label="From Start")
        self.btn_from_cursor = Gtk.Button(label="From Cursor")

        self.btn_build.connect("clicked", self.on_build_clicked)
        self.btn_play.connect("clicked", self.on_play_clicked)
        self.btn_pause.connect("clicked", self.on_pause_clicked)
        self.btn_stop.connect("clicked", self.on_stop_clicked)
        self.btn_prev.connect("clicked", self.on_prev_clicked)
        self.btn_next.connect("clicked", self.on_next_clicked)
        self.btn_from_start.connect("clicked", self.on_from_start_clicked)
        self.btn_from_cursor.connect("clicked", self.on_from_cursor_clicked)

        # Layout with Adw.ToolbarView
        tvw = Adw.ToolbarView()
        header = Adw.HeaderBar()
        tvw.add_top_bar(header)

        # Left controls box
        paths_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        paths_box.append(Gtk.Label(label="Model:"))
        paths_box.append(self.model_entry)
        paths_box.append(Gtk.Label(label="Voices:"))
        paths_box.append(self.voices_entry)
        header.pack_start(paths_box)

        # Right controls
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right_box.append(Gtk.Label(label="Voice:"))
        right_box.append(self.voice_entry)
        right_box.append(Gtk.Label(label="Lang:"))
        right_box.append(self.lang_entry)
        right_box.append(Gtk.Label(label="Speed:"))
        right_box.append(self.speed_scale)
        header.pack_end(right_box)

        # Bottom bar with transport
        bottom_bar = Adw.HeaderBar()
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for b in [self.btn_build, self.btn_from_start, self.btn_from_cursor, self.btn_prev, self.btn_play, self.btn_pause, self.btn_next, self.btn_stop]:
            bottom_box.append(b)
        bottom_bar.set_title_widget(bottom_box)
        tvw.add_bottom_bar(bottom_bar)

        tvw.set_content(scroller)
        self.set_content(tvw)

    # Build audio for sentences (runs in a worker thread)
    def on_build_clicked(self, _btn):
        text = self._get_text()
        self.sentences = split_sentences(text)
        self._clear_highlight()
        self.wav_files = [""] * len(self.sentences)
        self.current_idx = -1

        model = self.model_entry.get_text().strip()
        voices = self.voices_entry.get_text().strip()
        voice = self.voice_entry.get_text().strip() or DEFAULT_VOICE
        lang = self.lang_entry.get_text().strip() or DEFAULT_LANG
        speed = float(self.speed_adj.get_value())

        # Lazy init engine
        try:
            if self.engine is None:
                self.engine = TTSEngine(model, voices)
        except Exception as e:
            self._error_dialog(f"Failed to init Kokoro: {e}")
            return

        def worker():
            for i, (_s, _e, sent) in enumerate(self.sentences):
                # Skip if already built
                if self.wav_files[i]:
                    continue
                out = os.path.join(self.tmpdir.name, f"sent_{i:04d}.wav")
                try:
                    self.engine.synth_to_wav(sent, out, voice, speed, lang)
                    self.wav_files[i] = out
                except Exception as e:
                    GLib.idle_add(self._error_dialog, f"Synthesis failed at sentence {i+1}: {e}")
                    return
            GLib.idle_add(self._info_toast, "Audio built")

        threading.Thread(target=worker, daemon=True).start()

    # Transport handlers
    def on_play_clicked(self, _btn):
        if not self.wav_files:
            self.on_build_clicked(None)
            # give builder a moment to create first file, then attempt play
            GLib.timeout_add(300, self._play_first_if_ready)
            return
        # if nothing selected, start from start
        if self.current_idx < 0:
            self.current_idx = 0
        self.player.set_files(self.wav_files)
        self.player.play_index(self.current_idx)

    def _play_first_if_ready(self):
        if self.wav_files and self.wav_files[0]:
            self.player.set_files(self.wav_files)
            self.player.play_index(0)
            return False
        return True  # keep waiting a bit

    def on_pause_clicked(self, _btn):
        self.player.toggle_pause()

    def on_stop_clicked(self, _btn):
        self.player.stop()
        self._clear_highlight()
        self.current_idx = -1

    def on_prev_clicked(self, _btn):
        if not self.wav_files:
            return
        if self.current_idx <= 0:
            self.current_idx = 0
        else:
            self.current_idx -= 1
        self.player.set_files(self.wav_files)
        self.player.play_index(self.current_idx)

    def on_next_clicked(self, _btn):
        if not self.wav_files:
            return
        if self.current_idx + 1 < len(self.wav_files):
            self.current_idx += 1
        self.player.set_files(self.wav_files)
        self.player.play_index(self.current_idx)

    def on_from_start_clicked(self, _btn):
        if not self.wav_files:
            self.on_build_clicked(None)
            GLib.timeout_add(300, self._play_first_if_ready)
            return
        self.current_idx = 0
        self.player.set_files(self.wav_files)
        self.player.play_index(0)

    def on_from_cursor_clicked(self, _btn):
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        pos = it.get_offset()
        idx = self._sentence_index_for_pos(pos)
        if idx is None:
            return
        self.current_idx = idx
        if not self.wav_files:
            self.on_build_clicked(None)
            def later():
                if self.wav_files and self.wav_files[self.current_idx]:
                    self.player.set_files(self.wav_files)
                    self.player.play_index(self.current_idx)
                    return False
                return True
            GLib.timeout_add(300, later)
        else:
            self.player.set_files(self.wav_files)
            self.player.play_index(self.current_idx)

    # Helpers
    def _get_text(self) -> str:
        start = self.buf.get_start_iter()
        end = self.buf.get_end_iter()
        return self.buf.get_text(start, end, True)

    def _sentence_index_for_pos(self, pos: int) -> int | None:
        for i, (s, e, _t) in enumerate(self.sentences):
            if s <= pos <= e:
                return i
        return None

    def _clear_highlight(self):
        s = self.buf.get_start_iter()
        e = self.buf.get_end_iter()
        self.buf.remove_tag(self.tag_current, s, e)

    def on_sentence_started(self, _player, idx: int):
        self.current_idx = idx
        self._clear_highlight()
        if 0 <= idx < len(self.sentences):
            s_off, e_off, _ = self.sentences[idx]
            s_iter = self.buf.get_iter_at_offset(s_off)
            e_iter = self.buf.get_iter_at_offset(e_off)
            self.buf.apply_tag(self.tag_current, s_iter, e_iter)
            # scroll into view
            self.tv.scroll_to_iter(s_iter, 0.2, True, 0.0, 0.2)

    # UI messages
    def _error_dialog(self, msg: str):
        dlg = Adw.MessageDialog(transient_for=self, heading="Error", body=msg)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        dlg.present()

    def _info_toast(self, msg: str):
        # Quick unobtrusive message via dialog for simplicity
        dlg = Adw.MessageDialog(transient_for=self, heading="Info", body=msg)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        dlg.present()

class TTSApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.example.KokoroTTS", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TTSWindow(self)
        win.present()

if __name__ == "__main__":
    app = TTSApp()
    sys.exit(app.run(sys.argv))
