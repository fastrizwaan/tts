#!/usr/bin/env python3
# GTK4 + libadwaita Kokoro TTS player — lazy, seamless playback
# No pre-build step. Synthesize current sentence immediately, prefetch next in background.
# Features: play, pause/resume, stop, next/prev sentence, play from start, play from cursor, live highlighting.
# Deps: pygobject (Gtk4, Adw1), GStreamer (playbin, wavparse, audioconvert, audioresample, autoaudiosink),
#       kokoro-onnx==0.4.9, soundfile, numpy.

import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gst

# ---------- Defaults ----------
DEFAULT_MODEL = str(Path.home() / ".local/share/app.kokoro.demo/models/kokoro-v0_19.onnx")
DEFAULT_VOICES = str(Path.home() / ".local/share/app.kokoro.demo/models/voices-v1.0.bin")
DEFAULT_VOICE = "af_heart"
DEFAULT_LANG = "en-us"
DEFAULT_SPEED = 1.0

# ---------- Sentence splitting ----------
SENT_RE = re.compile(r"\s*(.+?)([.!?]+|\n{2,}|$)", re.DOTALL)

def split_sentences(text: str) -> List[Tuple[int, int, str]]:
    out = []
    for m in SENT_RE.finditer(text):
        s, e = m.span()
        seg = text[s:e]
        chunk = seg.strip()
        if not chunk:
            continue
        # trim leading spaces for highlight accuracy
        inner_start = s + (len(seg) - len(seg.lstrip()))
        inner_end = inner_start + len(chunk)
        out.append((inner_start, inner_end, chunk))
    return out

# ---------- TTS Engine ----------
class TTSEngine:
    def __init__(self, model_path: str, voices_path: str):
        from kokoro_onnx import Kokoro
        self.kokoro = Kokoro(model_path, voices_path)

    def synth_to_wav(self, text: str, out_path: str, *, voice: str, speed: float, lang: str):
        import soundfile as sf
        samples, sr = self.kokoro.create(text, voice=voice, speed=speed, lang=lang)
        sf.write(out_path, samples, sr)

# ---------- Async Synth Manager (JIT + Prefetch) ----------
class SynthManager(GObject.GObject):
    __gsignals__ = {
        "ready": (GObject.SIGNAL_RUN_FIRST, None, (int, str,)),  # idx, path
        "error": (GObject.SIGNAL_RUN_FIRST, None, (int, str,)),  # idx, message
    }

    def __init__(self, engine: TTSEngine, tmpdir: str, voice: str, speed: float, lang: str, sentences: List[Tuple[int,int,str]]):
        super().__init__()
        self.engine = engine
        self.tmpdir = tmpdir
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.sentences = sentences
        self.paths: Dict[int, str] = {}
        self.lock = threading.Lock()
        self.inflight: Dict[int, threading.Thread] = {}

    def path_for(self, idx: int) -> str:
        return os.path.join(self.tmpdir, f"sent_{idx:04d}.wav")

    def ensure(self, idx: int):
        if idx < 0 or idx >= len(self.sentences):
            return
        with self.lock:
            if idx in self.paths and os.path.exists(self.paths[idx]):
                return
            if idx in self.inflight:
                return
            t = threading.Thread(target=self._worker, args=(idx,), daemon=True)
            self.inflight[idx] = t
            t.start()

    def _worker(self, idx: int):
        try:
            _, _, text = self.sentences[idx]
            outp = self.path_for(idx)
            self.engine.synth_to_wav(text, outp, voice=self.voice, speed=self.speed, lang=self.lang)
            with self.lock:
                self.paths[idx] = outp
                self.inflight.pop(idx, None)
            GLib.idle_add(self.emit, "ready", idx, outp)
        except Exception as e:
            with self.lock:
                self.inflight.pop(idx, None)
            GLib.idle_add(self.emit, "error", idx, str(e))

# ---------- Audio Player ----------
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
        self.playbin = Gst.ElementFactory.make("playbin")
        self.bus = self.playbin.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self._on_bus)
        self._paused = False
        self.idx = -1
        self.seq: List[Optional[str]] = []  # wav paths per sentence

    def set_sequence(self, seq: List[Optional[str]]):
        self.seq = seq
        self.idx = -1

    def _set_uri(self, path: str):
        if not path or not os.path.isabs(path) or not os.path.exists(path):
            return False
        uri = GLib.filename_to_uri(path, None)
        self.playbin.set_property("uri", uri)
        return True

    def play_index(self, idx: int):
        if idx < 0 or idx >= len(self.seq):
            self.stop(); return
        path = self.seq[idx]
        if not path or not os.path.exists(path):
            return  # wait until ready
        if not self._set_uri(path):
            return
        self.idx = idx
        self.playbin.set_state(Gst.State.PLAYING)
        self._paused = False
        self.emit("sentence-started", self.idx)

    def play_next(self):
        self.play_index(self.idx + 1)

    def play_prev(self):
        self.play_index(self.idx - 1)

    def pause(self):
        self.playbin.set_state(Gst.State.PAUSED)
        self._paused = True
        self.emit("paused")

    def resume(self):
        self.playbin.set_state(Gst.State.PLAYING)
        self._paused = False
        self.emit("resumed")

    def stop(self):
        self.playbin.set_state(Gst.State.NULL)
        self._paused = False
        self.emit("stopped")

    def toggle_pause(self):
        if self._paused:
            self.resume()
        else:
            self.pause()

    def _on_bus(self, _bus, msg):
        t = msg.type
        if t == Gst.MessageType.EOS:
            # auto-advance
            if self.idx + 1 < len(self.seq):
                GLib.idle_add(self.play_next)
            else:
                self.stop(); self.emit("finished")
        elif t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("GStreamer error:", err, dbg, file=sys.stderr)
            self.stop(); self.emit("finished")

# ---------- Main Window ----------
class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kokoro TTS Player (Lazy)")
        self.set_default_size(1000, 720)
        Gst.init(None)

        # State
        self.tmpdir = tempfile.TemporaryDirectory(prefix="kokoro_lazy_")
        self.sentences: List[Tuple[int,int,str]] = []
        self.wav_paths: List[Optional[str]] = []
        self.current_idx = -1
        self.engine: Optional[TTSEngine] = None
        self.synth: Optional[SynthManager] = None

        # Player
        self.player = SentencePlayer()
        self.player.connect("sentence-started", self.on_sentence_started)

        self._build_ui()

    def _build_ui(self):
        # Text area
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_left_margin(8); self.textview.set_right_margin(8)
        self.buf: Gtk.TextBuffer = self.textview.get_buffer()
        self.tag_current = self.buf.create_tag("current", background="#fff6b3")

        # Inputs
        self.model_entry = Gtk.Entry(text=DEFAULT_MODEL)
        self.voices_entry = Gtk.Entry(text=DEFAULT_VOICES)
        self.voice_entry = Gtk.Entry(text=DEFAULT_VOICE)
        self.lang_entry = Gtk.Entry(text=DEFAULT_LANG)
        self.speed_adj = Gtk.Adjustment(lower=0.5, upper=2.0, step_increment=0.1, page_increment=0.1, value=DEFAULT_SPEED)
        self.speed_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.speed_adj)
        self.speed_scale.set_digits(2); self.speed_scale.set_hexpand(True)

        # Buttons
        self.btn_from_start = Gtk.Button(label="Play From Start")
        self.btn_from_cursor = Gtk.Button(label="Play From Cursor")
        self.btn_prev = Gtk.Button(label="Prev")
        self.btn_play = Gtk.Button(label="Play/Resume")
        self.btn_pause = Gtk.Button(label="Pause")
        self.btn_next = Gtk.Button(label="Next")
        self.btn_stop = Gtk.Button(label="Stop")

        self.btn_from_start.connect("clicked", self.on_from_start)
        self.btn_from_cursor.connect("clicked", self.on_from_cursor)
        self.btn_prev.connect("clicked", lambda _b: self._seek_and_play(self.current_idx - 1))
        self.btn_next.connect("clicked", lambda _b: self._seek_and_play(self.current_idx + 1))
        self.btn_play.connect("clicked", self.on_play_resume)
        self.btn_pause.connect("clicked", lambda _b: self.player.pause())
        self.btn_stop.connect("clicked", self.on_stop)

        # Layout via ToolbarView
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(self.textview)

        tvw = Adw.ToolbarView()
        top = Adw.HeaderBar()
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left.append(Gtk.Label(label="Model:")); left.append(self.model_entry)
        left.append(Gtk.Label(label="Voices:")); left.append(self.voices_entry)
        top.pack_start(left)
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right.append(Gtk.Label(label="Voice:")); right.append(self.voice_entry)
        right.append(Gtk.Label(label="Lang:")); right.append(self.lang_entry)
        right.append(Gtk.Label(label="Speed:")); right.append(self.speed_scale)
        top.pack_end(right)
        tvw.add_top_bar(top)

        bottom = Adw.HeaderBar()
        ctrls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for b in [self.btn_from_start, self.btn_from_cursor, self.btn_prev, self.btn_play, self.btn_pause, self.btn_next, self.btn_stop]:
            ctrls.append(b)
        bottom.set_title_widget(ctrls)
        tvw.add_bottom_bar(bottom)

        tvw.set_content(scroller)
        self.set_content(tvw)

    # --- Playback flows ---
    def _init_engine_and_sentences(self):
        text = self._get_text()
        self.sentences = split_sentences(text)
        self.wav_paths = [None] * len(self.sentences)
        self._clear_highlight()
        # init engine if needed
        if self.engine is None:
            try:
                self.engine = TTSEngine(self.model_entry.get_text().strip(), self.voices_entry.get_text().strip())
            except Exception as e:
                self._error(f"Kokoro init failed: {e}")
                return False
        # init synth manager
        self.synth = SynthManager(
            self.engine,
            self.tmpdir.name,
            self.voice_entry.get_text().strip() or DEFAULT_VOICE,
            float(self.speed_adj.get_value()),
            self.lang_entry.get_text().strip() or DEFAULT_LANG,
            self.sentences,
        )
        self.synth.connect("ready", self.on_wav_ready)
        self.synth.connect("error", self.on_wav_error)
        self.player.set_sequence(self.wav_paths)
        return True

    def on_from_start(self, _b):
        if not self._init_engine_and_sentences():
            return
        self._seek_and_play(0)

    def on_from_cursor(self, _b):
        if not self._init_engine_and_sentences():
            return
        insert = self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset()
        idx = self._idx_for_pos(insert)
        self._seek_and_play(idx if idx is not None else 0)

    def on_play_resume(self, _b):
        # resume if paused, else start from current or start
        if self.player._paused and self.player.idx >= 0:
            self.player.resume(); return
        if not self.sentences:
            if not self._init_engine_and_sentences():
                return
        start_idx = self.current_idx if self.current_idx >= 0 else 0
        self._seek_and_play(start_idx)

    def on_stop(self, _b):
        self.player.stop()
        self.current_idx = -1
        self._clear_highlight()

    def _seek_and_play(self, idx: int):
        if idx < 0 or idx >= len(self.sentences):
            return
        self.current_idx = idx
        # JIT ensure current and prefetch next
        assert self.synth is not None
        self.synth.ensure(idx)
        self.synth.ensure(idx + 1)
        # poll until current is ready, then play
        def try_play():
            path = self.wav_paths[idx]
            if path and os.path.exists(path):
                self.player.play_index(idx)
                return False
            return True
        GLib.timeout_add(100, try_play)

    # --- Synth callbacks ---
    def on_wav_ready(self, _mgr, idx: int, path: str):
        if 0 <= idx < len(self.wav_paths):
            self.wav_paths[idx] = path
        # smooth prefetch chain: when one becomes ready, prefetch next-next
        if idx + 2 < len(self.sentences):
            self.synth.ensure(idx + 2)

    def on_wav_error(self, _mgr, idx: int, msg: str):
        self._error(f"Synthesis error at sentence {idx+1}: {msg}")

    # --- Highlighting ---
    def on_sentence_started(self, _player, idx: int):
        self.current_idx = idx
        self._clear_highlight()
        if 0 <= idx < len(self.sentences):
            s, e, _ = self.sentences[idx]
            it_s = self.buf.get_iter_at_offset(s)
            it_e = self.buf.get_iter_at_offset(e)
            self.buf.apply_tag(self.tag_current, it_s, it_e)
            self.textview.scroll_to_iter(it_s, 0.2, True, 0.0, 0.2)

    def _clear_highlight(self):
        it_s = self.buf.get_start_iter(); it_e = self.buf.get_end_iter()
        self.buf.remove_tag(self.tag_current, it_s, it_e)

    # --- Helpers ---
    def _get_text(self) -> str:
        s = self.buf.get_start_iter(); e = self.buf.get_end_iter()
        return self.buf.get_text(s, e, True)

    def _idx_for_pos(self, pos: int) -> Optional[int]:
        for i, (s, e, _t) in enumerate(self.sentences):
            if s <= pos <= e:
                return i
        return None

    def _error(self, message: str):
        dlg = Adw.MessageDialog(transient_for=self, heading="Error", body=message)
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        dlg.present()

# ---------- App ----------
class TTSApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.example.KokoroTTS.Lazy", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TTSWindow(self)
        win.present()

if __name__ == "__main__":
    app = TTSApp()
    sys.exit(app.run(sys.argv))
