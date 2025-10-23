import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Gdk
import whisperspeech.pipeline as pipeline
import soundfile as sf
import subprocess
import time
import tempfile
import os
import torch
import torchaudio
import re
import threading
from dataclasses import dataclass
from pathlib import Path

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
        
        # Initialize models once
        print("Loading TTS pipeline...")
        self.pipe = pipeline.Pipeline(s2a_ref='collabora/whisperspeech:s2a-q4-tiny-en+pl.model', torch_compile=True)
        
        # Initialize ASR model once
        print("Loading ASR model...")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        self.asr_model = self.bundle.get_model().to(self.device)
        self.labels = self.bundle.get_labels()
        
        # Cache for processed audio
        self.audio_cache = {}
        self.current_text = ""
        self.current_audio_file = None
        self.current_player = None
        self.current_timer = None
        self.highlight_tag = None
        
        print("Models loaded successfully!")

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(700, 500)
        self.window.set_title("WhisperSpeech TTS")
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        
        # Text input area
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
        
        # Button container
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
        
        # Status label
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)
        
        self.window.set_content(main_box)
        self.window.present()
        
        # Set up highlight tag
        buffer = self.textview.get_buffer()
        rgba = Gdk.RGBA()
        rgba.parse("rgba(255, 255, 0, 0.3)")
        self.highlight_tag = buffer.create_tag("highlight", 
                                             background="yellow", 
                                             background_rgba=rgba)

    def update_status(self, message):
        """Thread-safe status update"""
        GLib.idle_add(lambda: self.status_label.set_text(message))

    def set_buttons_state(self, speak_sensitive, stop_sensitive):
        """Thread-safe button state update"""
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
        
        # Stop any current playback
        self.on_stop(None)
        
        # Check if we already have this text cached
        if text == self.current_text and self.current_audio_file and os.path.exists(self.current_audio_file):
            self.play_cached_audio()
        else:
            # Generate new audio in thread
            self.set_buttons_state(False, False)
            self.update_status("Generating speech...")
            threading.Thread(target=self.generate_and_play, args=(text,), daemon=True).start()

    def generate_and_play(self, text):
        try:
            # Clean up old audio file
            if self.current_audio_file and os.path.exists(self.current_audio_file):
                try:
                    os.unlink(self.current_audio_file)
                except:
                    pass
            
            # Prepare text
            words = text.split()
            clean_text = ' '.join(words)
            
            # Generate audio
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                wav_file = f.name
            
            self.pipe.generate_to_file(wav_file, clean_text)
            
            # Process alignment
            self.update_status("Processing alignment...")
            alignment_data = self.process_alignment(wav_file, words)
            
            if alignment_data:
                self.current_text = text
                self.current_audio_file = wav_file
                
                # Update UI with clean text and start playback
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
        """Play already generated audio with existing alignment"""
        if not self.current_audio_file or not os.path.exists(self.current_audio_file):
            return
            
        # Get current alignment data
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False).strip()
        words = text.split()
        
        # Get cached alignment (we'll reuse the word timing data)
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
            # Load and process audio
            waveform, sample_rate = torchaudio.load(wav_file)
            if sample_rate != self.bundle.sample_rate:
                waveform = torchaudio.functional.resample(waveform, sample_rate, self.bundle.sample_rate)
            
            # Get ASR emissions
            with torch.inference_mode():
                emissions, _ = self.asr_model(waveform.to(self.device))
                emissions = torch.log_softmax(emissions, dim=-1)
            
            emission = emissions[0].cpu().detach()
            
            # Prepare transcript for alignment
            clean_words = [re.sub(r'[^A-Z]', '', word.upper()) for word in words if re.sub(r'[^A-Z]', '', word.upper())]
            if not clean_words:
                return None
                
            transcript = "|" + "|".join(clean_words) + "|"
            dictionary = {c: i for i, c in enumerate(self.labels)}
            tokens = [dictionary[c] for c in transcript]
            
            # Perform alignment
            trellis = self.get_trellis(emission, tokens)
            path = self.backtrack(trellis, emission, tokens)
            segments = self.merge_repeats(path, transcript)
            word_segments = self.merge_words(segments)
            
            # Calculate timing
            word_start_times = [w.start * 0.02 for w in word_segments]
            word_end_times = [w.end * 0.02 for w in word_segments]
            
            # Calculate character positions
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
            
        except Exception as e:
            print(f"Alignment error: {e}")
            return None

    def start_playback(self, text, alignment_data):
        """Start audio playback with highlighting (runs on main thread)"""
        try:
            # Update text buffer
            buffer = self.textview.get_buffer()
            buffer.set_text(text)
            
            # Store alignment data
            self.words = alignment_data['words']
            self.word_start_times = alignment_data['word_start_times']
            self.word_end_times = alignment_data['word_end_times']
            self.word_starts = alignment_data['word_starts']
            
            # Start audio playback
            self.current_player = subprocess.Popen(['aplay', self.current_audio_file], 
                                                 stdout=subprocess.DEVNULL, 
                                                 stderr=subprocess.DEVNULL)
            
            # Start highlighting
            self.start_time = time.time()
            self.current_timer = GLib.timeout_add(50, self.update_highlight)
            
            # Update UI
            self.set_buttons_state(False, True)
            self.update_status("Playing...")
            
            # Schedule cleanup
            waveform, sample_rate = torchaudio.load(self.current_audio_file)
            duration = waveform.size(1) / sample_rate
            GLib.timeout_add_seconds(int(duration) + 2, self.on_playback_finished)
            
        except Exception as e:
            print(f"Playback error: {e}")
            self.update_status(f"Playback error: {str(e)}")
            self.set_buttons_state(True, False)

    def update_highlight(self):
        """Update word highlighting during playback"""
        try:
            if not self.current_player:
                return False
                
            # Check if player is still running
            if self.current_player.poll() is not None:
                # Player finished
                self.on_playback_finished()
                return False
            
            current_time = time.time() - self.start_time
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
            
            # Remove previous highlighting
            buffer.remove_tag(self.highlight_tag, start, end)
            
            # Find current word and highlight it
            for i in range(len(self.words)):
                if (i < len(self.word_start_times) and i < len(self.word_end_times) and
                    self.word_start_times[i] <= current_time < self.word_end_times[i]):
                    
                    if i < len(self.word_starts):
                        s_iter = buffer.get_iter_at_offset(self.word_starts[i])
                        e_iter = buffer.get_iter_at_offset(self.word_starts[i] + len(self.words[i]))
                        buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
                    break
            
            return True
            
        except Exception as e:
            print(f"Highlight error: {e}")
            return False

    def on_stop(self, button):
        """Stop current playback"""
        if self.current_player:
            try:
                self.current_player.terminate()
                self.current_player.wait(timeout=1)
            except:
                try:
                    self.current_player.kill()
                except:
                    pass
            self.current_player = None
        
        if self.current_timer:
            GLib.source_remove(self.current_timer)
            self.current_timer = None
        
        # Clear highlighting
        if self.highlight_tag:
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
            buffer.remove_tag(self.highlight_tag, start, end)
        
        self.set_buttons_state(True, False)
        self.update_status("Stopped")

    def on_playback_finished(self):
        """Called when playback finishes"""
        self.on_stop(None)
        self.update_status("Ready")
        return False

    # Alignment methods (unchanged from original)
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

if __name__ == "__main__":
    app = TTSApp(application_id='com.example.ttsapp')
    app.run(None)
