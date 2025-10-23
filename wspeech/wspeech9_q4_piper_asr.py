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

import subprocess
import tempfile
import time
import os
import threading
from dataclasses import dataclass
from pathlib import Path
import torch
import whisper
import re

@dataclass
class WordTiming:
    word: str
    start_time: float  # in seconds
    end_time: float    # in seconds

class TTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)

        Gst.init(None)

        # Piper TTS setup
        self.piper_model_path = Path.home() / "Downloads/en_US-libritts-high.onnx"
        if not self.piper_model_path.exists():
            print(f"Error: Piper model not found at {self.piper_model_path}")
            exit(1)

        # Check if piper is installed
        try:
            subprocess.run(["piper", "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            print("Error: 'piper' command not found. Please install Piper TTS.")
            exit(1)

        print("Loading Whisper ASR model...")
        self.asr_model = whisper.load_model("tiny.en", device="cuda" if torch.cuda.is_available() else "cpu")
        print("Piper TTS and ASR initialized successfully!")

        self.current_text = ""
        self.current_audio_file = None
        self.current_timer = None
        self.highlight_tag = None
        self.word_timings = []

        # GStreamer player setup
        self.current_player = Gst.ElementFactory.make("playbin", "player")
        self.bus = self.current_player.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message::eos", self.on_gst_eos)
        self.bus.connect("message::error", self.on_gst_error)

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(700, 500)
        self.window.set_title("Piper TTS with ASR Timing")

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
            self.play_cached_audio(text)
        else:
            self.set_buttons_state(False, False)
            self.update_status("Generating speech and aligning...")
            threading.Thread(target=self.generate_and_play, args=(text,), daemon=True).start()

    def generate_and_play(self, text):
        try:
            if self.current_audio_file and os.path.exists(self.current_audio_file):
                try:
                    os.unlink(self.current_audio_file)
                except:
                    pass

            # Generate audio with Piper
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                wav_file = f.name

            # Run Piper TTS
            cmd = [
                "piper",
                "--model", str(self.piper_model_path),
                "--output_file", wav_file
            ]
            proc = subprocess.run(cmd, input=text, text=True, capture_output=True, check=True)

            # Use Whisper ASR to get word-level timing
            self.update_status("Aligning speech...")
            result = self.asr_model.transcribe(wav_file, word_timestamps=True)
            
            # Extract word timings
            self.word_timings = []
            for segment in result['segments']:
                for word_info in segment['words']:
                    word = word_info['word'].strip()
                    if word:  # Only add non-empty words
                        self.word_timings.append(
                            WordTiming(
                                word=word,
                                start_time=word_info['start'],
                                end_time=word_info['end']
                            )
                        )

            self.current_text = text
            self.current_audio_file = wav_file
            GLib.idle_add(self.start_playback, text)

        except subprocess.CalledProcessError as e:
            print(f"Piper error: {e.stderr}")
            self.update_status(f"TTS Error: {e.stderr.decode()[:100]}")
            self.set_buttons_state(True, False)
            if os.path.exists(wav_file):
                os.unlink(wav_file)
        except Exception as e:
            print(f"Error in generate_and_play: {e}")
            self.update_status(f"Error: {str(e)}")
            self.set_buttons_state(True, False)

    def play_cached_audio(self, text):
        if not self.current_audio_file or not os.path.exists(self.current_audio_file):
            return
        self.start_playback(text)

    def start_playback(self, text):
        try:
            buffer = self.textview.get_buffer()
            buffer.set_text(text)

            if not self.current_audio_file or not os.path.exists(self.current_audio_file):
                raise FileNotFoundError("Audio file not found")

            uri = Gst.filename_to_uri(self.current_audio_file)
            self.current_player.set_property("uri", uri)
            self.current_player.set_state(Gst.State.PLAYING)

            # Start the accurate timer for highlighting
            self.current_timer = GLib.timeout_add(50, self.update_highlight)

            self.set_buttons_state(False, True)
            self.update_status("Playing...")

        except Exception as e:
            print(f"Playback error: {e}")
            self.update_status(f"Playback error: {str(e)}")
            self.set_buttons_state(True, False)

    def normalize_word(self, word):
        """Normalize word for comparison - remove punctuation and convert to lowercase"""
        return re.sub(r'[^\w]', '', word).lower()

    def update_highlight(self):
        try:
            if not self.current_player:
                return False
                
            # Query actual playback position from GStreamer
            success, position = self.current_player.query_position(Gst.Format.TIME)
            if not success:
                return True
                
            current_time = position / Gst.SECOND  # Convert to seconds
            
            # Find the current word based on timing
            current_word_idx = -1
            for i, timing in enumerate(self.word_timings):
                if timing.start_time <= current_time < timing.end_time:
                    current_word_idx = i
                    break
            
            # Update the text view highlighting
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
            buffer.remove_tag(self.highlight_tag, start, end)
            
            if current_word_idx >= 0 and current_word_idx < len(self.word_timings):
                text_content = buffer.get_text(start, end, False)
                
                # Find all word boundaries in the text (including contractions)
                # This pattern matches words with apostrophes like "don't", "I'm", etc.
                matches = list(re.finditer(r"\b[\w']+\b", text_content))
                
                # Get the word at the current index in the text sequence
                if current_word_idx < len(matches):
                    match = matches[current_word_idx]
                    word_start = match.start()
                    word_end = match.end()
                    s_iter = buffer.get_iter_at_offset(word_start)
                    e_iter = buffer.get_iter_at_offset(word_end)
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

if __name__ == "__main__":
    app = TTSApp(application_id='io.fastrizwaan.github.pipertts_asr')
    app.run(None)
