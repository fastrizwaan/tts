#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, GLib, Gdk, Gst

import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="torch")
warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")

import whisperspeech.pipeline as pipeline
import soundfile as sf
import torch
import torchaudio
import re
import tempfile
import time
import os
import threading
from dataclasses import dataclass

@dataclass
class Point:
    token_index: int
    time_index: int
    score: float

@dataclass
class Segment:
    label: str
    start: int
    end: int
    score: float
    def __repr__(self):
        return f"{self.label}\t({self.score:4.2f}): [{self.start:5d}, {self.end:5d})"
    @property
    def length(self):
        return self.end - self.start

class TTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)

        Gst.init(None)

        print("Loading TTS pipeline...")
        self.pipe = pipeline.Pipeline(
            s2a_ref='collabora/whisperspeech:s2a-q4-tiny-en+pl.model',
            torch_compile=True
        )

        print("Loading ASR model...")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        self.asr_model = self.bundle.get_model().to(self.device)
        self.labels = self.bundle.get_labels()
        print("Models loaded successfully!")

        self.audio_cache = {}
        self.current_text = ""
        self.current_audio_file = None
        self.current_timer = None
        self.highlight_tag = None

        # GStreamer player setup
        self.current_player = Gst.ElementFactory.make("playbin", "player")
        self.bus = self.current_player.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message::eos", self.on_gst_eos)
        self.bus.connect("message::error", self.on_gst_error)

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(700, 500)
        self.window.set_title("WhisperSpeech TTS")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)

        text_label = Gtk.Label(label="Enter text to speak:")
        text_label.set_halign(Gtk.Align.START)
        main_box.append(text_label)

        self.textview = Gtk.TextView()
        self.textview.set_editable(True)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_top_margin(8)
        self.textview.set_bottom_margin(8)
        self.textview.set_left_margin(8)
        self.textview.set_right_margin(8)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.textview)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(200)
        main_box.append(scrolled)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.CENTER)

        self.speak_button = Gtk.Button(label="🔊 Speak")
        self.speak_button.add_css_class("suggested-action")
        self.speak_button.connect("clicked", self.on_speak)
        button_box.append(self.speak_button)

        self.stop_button = Gtk.Button(label="⏹ Stop")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self.on_stop)
        button_box.append(self.stop_button)

        main_box.append(button_box)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)

        self.window.set_content(main_box)
        self.window.present()

        buffer = self.textview.get_buffer()
        rgba = Gdk.RGBA()
        rgba.parse("rgba(255, 255, 0, 0.3)")
        self.highlight_tag = buffer.create_tag("highlight",
                                               background="yellow",
                                               background_rgba=rgba)

    def update_status(self, message):
        GLib.idle_add(lambda: self.status_label.set_text(message))

    def set_buttons_state(self, speak_sensitive, stop_sensitive):
        def update():
            self.speak_button.set_sensitive(speak_sensitive)
            self.stop_button.set_sensitive(stop_sensitive)
        GLib.idle_add(update)

    def on_speak(self, button):
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False).strip()
        if not text:
            return
        self.on_stop(None)
        if text == self.current_text and self.current_audio_file and os.path.exists(self.current_audio_file):
            self.play_cached_audio()
        else:
            self.set_buttons_state(False, False)
            self.update_status("Generating speech...")
            threading.Thread(target=self.generate_and_play, args=(text,), daemon=True).start()

    def generate_and_play(self, text):
        try:
            if self.current_audio_file and os.path.exists(self.current_audio_file):
                try:
                    os.unlink(self.current_audio_file)
                except:
                    pass
            words = text.split()
            clean_text = ' '.join(words)

            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                wav_file = f.name
            self.pipe.generate_to_file(wav_file, clean_text)

            self.update_status("Processing alignment...")
            alignment_data = self.process_alignment(wav_file, words)

            if alignment_data:
                self.current_text = text
                self.current_audio_file = wav_file
                GLib.idle_add(self.start_playback, clean_text, alignment_data)
            else:
                self.update_status("Alignment failed")
                self.set_buttons_state(True, False)
                if os.path.exists(wav_file):
                    os.unlink(wav_file)
        except Exception as e:
            print(f"Error in generate_and_play: {e}")
            self.update_status(f"Error: {str(e)}")
            self.set_buttons_state(True, False)

    def play_cached_audio(self):
        if not self.current_audio_file or not os.path.exists(self.current_audio_file):
            return
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False).strip()
        words = text.split()
        if hasattr(self, 'word_start_times') and hasattr(self, 'word_end_times'):
            alignment_data = {
                'words': words,
                'word_start_times': self.word_start_times,
                'word_end_times': self.word_end_times,
                'word_starts': self.word_starts
            }
            self.start_playback(text, alignment_data)

    def process_alignment(self, wav_file, words):
        try:
            waveform, sample_rate = torchaudio.load(wav_file)
            if sample_rate != self.bundle.sample_rate:
                waveform = torchaudio.functional.resample(waveform, sample_rate, self.bundle.sample_rate)
            with torch.inference_mode():
                emissions, _ = self.asr_model(waveform.to(self.device))
                emissions = torch.log_softmax(emissions, dim=-1)
            emission = emissions[0].cpu().detach()
            original_words = words
            align_words = []
            align_to_orig = []
            for i, word in enumerate(original_words):
                clean = re.sub(r'[^A-Z]', '', word.upper())
                if clean:
                    align_words.append(clean)
                    align_to_orig.append(i)
            if not align_words:
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
            transcript = "|" + "|".join(align_words) + "|"
            dictionary = {c: i for i, c in enumerate(self.labels)}
            missing_chars = [c for c in transcript if c not in dictionary]
            if missing_chars:
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
            tokens = [dictionary[c] for c in transcript]
            trellis = self.get_trellis(emission, tokens)
            path = self.backtrack(trellis, emission, tokens)
            if not path:
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
            segments = self.merge_repeats(path, transcript)
            word_segments = self.merge_words(segments)
            if len(word_segments) != len(align_words):
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
            word_start_times = [0.0] * len(original_words)
            word_end_times = [0.0] * len(original_words)
            j = 0
            for i in range(len(original_words)):
                if j < len(align_to_orig) and align_to_orig[j] == i:
                    word_start_times[i] = word_segments[j].start * 0.02
                    word_end_times[i] = word_segments[j].end * 0.02
                    j += 1
            current_start = 0.0
            current_end = 0.0
            for i in range(len(original_words)):
                if word_end_times[i] > 0:
                    current_start = word_start_times[i]
                    current_end = word_end_times[i]
                else:
                    word_start_times[i] = current_start
                    word_end_times[i] = current_end
            word_starts = []
            offset = 0
            for word in original_words:
                word_starts.append(offset)
                offset += len(word) + 1
            return {
                'words': original_words,
                'word_start_times': word_start_times,
                'word_end_times': word_end_times,
                'word_starts': word_starts
            }
        except Exception as e:
            print(f"Alignment error: {e}")
            return self.create_fallback_alignment(words, waveform, sample_rate)

    def create_fallback_alignment(self, words, waveform, sample_rate):
        duration = waveform.size(1) / sample_rate
        word_duration = duration / len(words) if words else 1.0
        word_start_times = [i * word_duration for i in range(len(words))]
        word_end_times = [(i + 1) * word_duration for i in range(len(words))]
        word_starts = []
        offset = 0
        for word in words:
            word_starts.append(offset)
            offset += len(word) + 1
        return {
            'words': words,
            'word_start_times': word_start_times,
            'word_end_times': word_end_times,
            'word_starts': word_starts
        }

    def start_playback(self, text, alignment_data):
        try:
            buffer = self.textview.get_buffer()
            buffer.set_text(text)
            self.words = alignment_data['words']
            self.word_start_times = alignment_data['word_start_times']
            self.word_end_times = alignment_data['word_end_times']
            self.word_starts = alignment_data['word_starts']

            if not self.current_audio_file or not os.path.exists(self.current_audio_file):
                raise FileNotFoundError("Audio file not found")

            uri = Gst.filename_to_uri(self.current_audio_file)
            self.current_player.set_property("uri", uri)
            self.current_player.set_state(Gst.State.PLAYING)

            self.start_time = time.time()
            self.current_timer = GLib.timeout_add(50, self.update_highlight)

            self.set_buttons_state(False, True)
            self.update_status("Playing...")

        except Exception as e:
            print(f"Playback error: {e}")
            self.update_status(f"Playback error: {str(e)}")
            self.set_buttons_state(True, False)

    def update_highlight(self):
        try:
            if not self.current_player:
                return False
            success, position = self.current_player.query_position(Gst.Format.TIME)
            if not success:
                return True
            current_time = position / Gst.SECOND
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
            buffer.remove_tag(self.highlight_tag, start, end)
            for i in range(len(self.words)):
                if (self.word_start_times[i] <= current_time < self.word_end_times[i]):
                    s_iter = buffer.get_iter_at_offset(self.word_starts[i])
                    e_iter = buffer.get_iter_at_offset(self.word_starts[i] + len(self.words[i]))
                    buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
            return True
        except Exception as e:
            print(f"Highlight error: {e}")
            return False

    def on_stop(self, button):
        if self.current_player:
            self.current_player.set_state(Gst.State.NULL)
        if self.current_timer:
            GLib.source_remove(self.current_timer)
            self.current_timer = None
        if self.highlight_tag:
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
            buffer.remove_tag(self.highlight_tag, start, end)
        self.set_buttons_state(True, False)
        self.update_status("Stopped")

    def on_playback_finished(self):
        self.on_stop(None)
        self.update_status("Ready")
        return False

    def on_gst_eos(self, bus, msg):
        self.on_playback_finished()

    def on_gst_error(self, bus, msg):
        err, debug = msg.parse_error()
        print(f"GStreamer Error: {err}, {debug}")
        self.update_status("Playback error")
        self.on_playback_finished()

    # Alignment helpers
    def get_trellis(self, emission, tokens, blank_id=0):
        num_frame = emission.size(0)
        num_tokens = len(tokens)
        trellis = torch.full((num_frame, num_tokens), -float("inf"))
        trellis[0, 0] = emission[0, blank_id]
        for t in range(1, num_frame):
            trellis[t, 0] = trellis[t - 1, 0] + emission[t, blank_id]
        for t in range(1, num_frame):
            for j in range(1, min(t + 1, num_tokens)):
                staying = trellis[t - 1, j] + emission[t, blank_id]
                changing = trellis[t - 1, j - 1] + emission[t, tokens[j]]
                trellis[t, j] = torch.maximum(staying, changing)
        return trellis

    def backtrack(self, trellis, emission, tokens, blank_id=0):
        try:
            t, j = trellis.size(0) - 1, trellis.size(1) - 1
            path = [Point(j, t, emission[t, blank_id].exp().item())]
            while j > 0:
                if t <= 0:
                    return []
                p_stay = emission[t - 1, blank_id]
                p_change = emission[t - 1, tokens[j]]
                stayed = trellis[t - 1, j] + p_stay
                changed = trellis[t - 1, j - 1] + p_change
                t -= 1
                if changed > stayed:
                    j -= 1
                prob = (p_change if changed > stayed else p_stay).exp().item()
                path.append(Point(j, t, prob))
            while t > 0:
                prob = emission[t - 1, blank_id].exp().item()
                path.append(Point(j, t - 1, prob))
                t -= 1
            return path[::-1]
        except Exception:
            return []

    def merge_repeats(self, path, transcript):
        i1, i2 = 0, 0
        segments = []
        while i1 < len(path):
            while i2 < len(path) and path[i1].token_index == path[i2].token_index:
                i2 += 1
            score = sum(path[k].score for k in range(i1, i2)) / (i2 - i1)
            segments.append(Segment(transcript[path[i1].token_index],
                                    path[i1].time_index,
                                    path[i2 - 1].time_index + 1,
                                    score))
            i1 = i2
        return segments

    def merge_words(self, segments, separator="|"):
        words = []
        i1, i2 = 0, 0
        while i1 < len(segments):
            if i2 >= len(segments) or segments[i2].label == separator:
                if i1 != i2:
                    segs = segments[i1:i2]
                    word = "".join([s.label for s in segs])
                    score = sum(s.score * s.length for s in segs) / sum(s.length for s in segs)
                    words.append(Segment(word, segments[i1].start, segments[i2 - 1].end, score))
                i1 = i2 + 1
                i2 = i1
            else:
                i2 += 1
        return words

if __name__ == "__main__":
    app = TTSApp(application_id='io.fastrizwaan.github.whispertts')
    app.run(None)

