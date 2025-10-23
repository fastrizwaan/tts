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

class TTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)
        self.pipe = pipeline.Pipeline()
        self.cps = 15  # default if needed

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
        words = text.split()
        text = ' '.join(words)  # normalize spaces
        buffer.set_text(text)
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            wav_file = f.name
        # Generate with cps if supported; adjust if method differs
        self.pipe.generate_to_file(wav_file, text)  # add cps=self.cps if supported
        data, sr = sf.read(wav_file)
        duration = len(data) / sr
        cps_actual = len(text) / duration if duration > 0 else self.cps
        word_starts = []
        offset = 0
        for i, word in enumerate(words):
            word_starts.append(offset)
            offset += len(word) + 1
        word_start_times = [0.0]
        for i in range(len(words)):
            add_chars = len(words[i]) + (1 if i < len(words) - 1 else 0)
            word_start_times.append(word_start_times[-1] + add_chars / cps_actual)
        self.highlight_tag = buffer.create_tag("highlight", background="yellow")
        self.start_time = time.time()
        self.words = words
        self.word_starts = word_starts
        self.word_start_times = word_start_times
        self.player = subprocess.Popen(['aplay', wav_file])
        self.timer = GLib.timeout_add(50, self.update_highlight)
        GLib.timeout_add_seconds(int(duration) + 1, lambda: os.unlink(wav_file))

    def update_highlight(self):
        current_time = time.time() - self.start_time
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        for i in range(len(self.words)):
            if self.word_start_times[i] <= current_time < self.word_start_times[i + 1]:
                s_iter = buffer.get_iter_at_offset(self.word_starts[i])
                e_iter = buffer.get_iter_at_offset(self.word_starts[i] + len(self.words[i]))
                buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
                break
        else:
            if current_time >= self.word_start_times[-1]:
                return False
        return True

app = TTSApp(application_id='com.example.ttsapp')
app.run(None)
