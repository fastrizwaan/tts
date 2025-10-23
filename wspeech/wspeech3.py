import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import whisperspeech.pipeline as pipeline
import soundfile as sf
import subprocess
import time
import tempfile
import os
import torch
import torchaudio
import re
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
        self.pipe = pipeline.Pipeline(s2a_ref='collabora/whisperspeech:s2a-q4-tiny-en+pl.model', torch_compile=True)

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(600, 400)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.textview = Gtk.TextView(editable=True, wrap_mode=Gtk.WrapMode.WORD)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.textview)
        scrolled.set_vexpand(True)
        box.append(scrolled)
        button = Gtk.Button(label="Speak")
        button.connect("clicked", self.on_speak)
        box.append(button)
        self.window.set_content(box)
        self.window.present()

    def on_speak(self, button):
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False).strip()
        if not text:
            return
        self.words = text.split()
        text = ' '.join(self.words)
        buffer.set_text(text)
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            wav_file = f.name
        self.pipe.generate_to_file(wav_file, text)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        model = bundle.get_model().to(device)
        labels = bundle.get_labels()
        waveform, sample_rate = torchaudio.load(wav_file)
        duration = waveform.size(1) / sample_rate
        if sample_rate != bundle.sample_rate:
            waveform = torchaudio.functional.resample(waveform, sample_rate, bundle.sample_rate)
        with torch.inference_mode():
            emissions, _ = model(waveform.to(device))
            emissions = torch.log_softmax(emissions, dim=-1)
        emission = emissions[0].cpu().detach()
        clean_words = [re.sub(r'[^A-Z]', '', word.upper()) for word in self.words if re.sub(r'[^A-Z]', '', word.upper())]
        transcript = "|" + "|".join(clean_words) + "|"
        dictionary = {c: i for i, c in enumerate(labels)}
        tokens = [dictionary[c] for c in transcript]
        trellis = self.get_trellis(emission, tokens)
        path = self.backtrack(trellis, emission, tokens)
        segments = self.merge_repeats(path, transcript)
        word_segments = self.merge_words(segments)
        self.word_start_times = [w.start * 0.02 for w in word_segments]
        self.word_end_times = [w.end * 0.02 for w in word_segments]
        self.word_starts = []
        offset = 0
        for word in self.words:
            self.word_starts.append(offset)
            offset += len(word) + 1
        self.highlight_tag = buffer.create_tag("highlight", background="yellow")
        self.player = subprocess.Popen(['aplay', wav_file])
        self.start_time = time.time()
        self.timer = GLib.timeout_add(50, self.update_highlight)
        GLib.timeout_add_seconds(int(duration) + 1, lambda: os.unlink(wav_file))

    def get_trellis(self, emission, tokens, blank_id=0):
        num_frame = emission.size(0)
        num_tokens = len(tokens)
        trellis = torch.zeros((num_frame, num_tokens))
        trellis[1:, 0] = torch.cumsum(emission[1:, blank_id], 0)
        trellis[0, 1:] = -float("inf")
        trellis[-num_tokens + 1:, 0] = float("inf")
        for t in range(num_frame - 1):
            trellis[t + 1, 1:] = torch.maximum(
                trellis[t, 1:] + emission[t, blank_id],
                trellis[t, :-1] + emission[t, tokens[1:]],
            )
        return trellis

    def backtrack(self, trellis, emission, tokens, blank_id=0):
        t, j = trellis.size(0) - 1, trellis.size(1) - 1
        path = [Point(j, t, emission[t, blank_id].exp().item())]
        while j > 0:
            assert t > 0
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

    def merge_repeats(self, path, transcript):
        i1, i2 = 0, 0
        segments = []
        while i1 < len(path):
            while i2 < len(path) and path[i1].token_index == path[i2].token_index:
                i2 += 1
            score = sum(path[k].score for k in range(i1, i2)) / (i2 - i1)
            segments.append(
                Segment(
                    transcript[path[i1].token_index],
                    path[i1].time_index,
                    path[i2 - 1].time_index + 1,
                    score,
                )
            )
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

    def update_highlight(self):
        current_time = time.time() - self.start_time
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        for i in range(len(self.words)):
            if self.word_start_times[i] <= current_time < self.word_end_times[i]:
                s_iter = buffer.get_iter_at_offset(self.word_starts[i])
                e_iter = buffer.get_iter_at_offset(self.word_starts[i] + len(self.words[i]))
                buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
                break
        return True

app = TTSApp(application_id='com.example.ttsapp')
app.run(None)
