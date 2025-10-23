import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gst', '1.0') # Require GStreamer 1.0

from gi.repository import Gtk, Adw, GLib, Gdk
import whisperspeech.pipeline as pipeline
import soundfile as sf
import time
import tempfile
import os
import torch
import torchaudio
import re
import threading
from dataclasses import dataclass
from pathlib import Path
import gi.repository.Gst as Gst # Import GStreamer

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
        return f"{self.label}\t({self.score:4.2f}): [{self.start:5d}, {self.end:5d}]"
    @property
    def length(self):
        return self.end - self.start

class TTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)

        # Initialize GStreamer
        Gst.init(None) # Initialize GStreamer
       
        # Initialize models once
        print("Loading TTS pipeline...")
        self.pipe = pipeline.Pipeline(s2a_ref='collabora/whisperspeech:s2a-q4-tiny-en+pl.model', torch_compile=True)
       
        # Initialize ASR model once
        print("Loading ASR model...")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu") # Fixed syntax error
        self.bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        self.asr_model = self.bundle.get_model().to(self.device)
        self.labels = self.bundle.get_labels()
       
        # Cache for processed audio
        self.audio_cache = {}
        self.current_text = ""
        self.current_audio_file = None
        self.current_player = None # GStreamer player instance
        self.current_playback_thread = None # Thread monitoring GStreamer bus
        self.playback_finished_event = threading.Event() # Event to signal playback end
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
                    print(f"Deleted old audio file: {self.current_audio_file}")
                except OSError as e:
                    print(f"Could not delete old audio file {self.current_audio_file}: {e}")
           
            # Prepare text
            words = text.split()
            clean_text = ' '.join(words)
           
            # Generate audio
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                temp_wav_file = f.name # Use a different variable name
                print(f"Generated temporary audio file: {temp_wav_file}")
           
            self.pipe.generate_to_file(temp_wav_file, clean_text)
            print(f"Audio generated successfully: {temp_wav_file}")

            # Verify the file was created and has content
            if not os.path.exists(temp_wav_file):
                raise RuntimeError(f"Generated file does not exist: {temp_wav_file}")
            file_size = os.path.getsize(temp_wav_file)
            print(f"Generated file size: {file_size} bytes")
            if file_size == 0:
                 raise RuntimeError(f"Generated file is empty: {temp_wav_file}")

            # --- NEW STEP: Reload and re-save the WAV file to standardize it ---
            print("Reloading and re-saving WAV file to standardize format...")
            waveform, original_sample_rate = torchaudio.load(temp_wav_file)
            # Save using torchaudio.save with explicit format parameters
            # This often avoids non-standard chunks like PEAK
            torchaudio.save(self.current_audio_file, waveform, original_sample_rate, format="wav", encoding="PCM_S", bits_per_sample=16)
            print(f"Standardized audio file saved: {self.current_audio_file}")

            # Clean up the temporary file created by whisperspeech
            os.unlink(temp_wav_file)
            print(f"Deleted temporary whisperspeech file: {temp_wav_file}")

            # Process alignment using the standardized file
            self.update_status("Processing alignment...")
            alignment_data = self.process_alignment(self.current_audio_file, words)
           
            if alignment_data:
                self.current_text = text
                # self.current_audio_file is already set correctly
               
                # Update UI with clean text and start playback
                GLib.idle_add(self.start_playback, clean_text, alignment_data)
            else:
                print("Alignment failed - this shouldn't happen with fallback")
                self.update_status("Alignment failed")
                self.set_buttons_state(True, False)
                if os.path.exists(self.current_audio_file):
                    os.unlink(self.current_audio_file)
                   
        except Exception as e:
            print(f"Error in generate_and_play: {e}")
            self.update_status(f"Error: {str(e)}")
            self.set_buttons_state(True, False)

    def play_cached_audio(self):
        """Play already generated audio with existing alignment"""
        if not self.current_audio_file or not os.path.exists(self.current_audio_file):
            print("No cached audio file found or file does not exist.")
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
        else:
            print("Cached alignment data not found.")

    def process_alignment(self, wav_file, words):
        try:
            print(f"Processing alignment for {len(words)} words: {words}")
           
            # Load and process audio
            waveform, sample_rate = torchaudio.load(wav_file)
            print(f"Audio loaded: shape={waveform.shape}, sample_rate={sample_rate}")
           
            if sample_rate != self.bundle.sample_rate:
                waveform = torchaudio.functional.resample(waveform, sample_rate, self.bundle.sample_rate)
                print(f"Audio resampled to {self.bundle.sample_rate}")
           
            # Get ASR emissions
            with torch.inference_mode():
                emissions, _ = self.asr_model(waveform.to(self.device))
                emissions = torch.log_softmax(emissions, dim=-1)
           
            emission = emissions[0].cpu().detach()
            print(f"Emissions shape: {emission.shape}")
           
            # Prepare transcript for alignment
            original_words = words
            align_words = []
            align_to_orig = []
            for i, word in enumerate(original_words):
                clean = re.sub(r'[^A-Z]', '', word.upper())
                if clean:
                    align_words.append(clean)
                    align_to_orig.append(i)
           
            print(f"Align words: {align_words}")
           
            if not align_words:
                print("No alignable words found - falling back to simple timing")
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
               
            transcript = "|" + "|".join(align_words) + "|"
            print(f"Transcript: {transcript}")
           
            dictionary = {c: i for i, c in enumerate(self.labels)}
           
            # Check if all characters in transcript exist in dictionary
            missing_chars = [c for c in transcript if c not in dictionary]
            if missing_chars:
                print(f"Missing characters in dictionary: {missing_chars}")
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
           
            tokens = [dictionary[c] for c in transcript]
            print(f"Tokens: {len(tokens)} tokens")
           
            # Perform alignment
            trellis = self.get_trellis(emission, tokens)
            path = self.backtrack(trellis, emission, tokens)
           
            if not path:
                print("Backtrack failed - using fallback alignment")
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
               
            segments = self.merge_repeats(path, transcript)
            word_segments = self.merge_words(segments)
           
            print(f"Word segments: {len(word_segments)}")
           
            if len(word_segments) != len(align_words):
                print(f"Mismatch in segments: {len(word_segments)} vs {len(align_words)} - using fallback")
                return self.create_fallback_alignment(original_words, waveform, sample_rate)
           
            # Assign timings with propagation
            word_start_times = [0.0] * len(original_words)
            word_end_times = [0.0] * len(original_words)
           
            j = 0
            for i in range(len(original_words)):
                if j < len(align_to_orig) and align_to_orig[j] == i:
                    word_start_times[i] = word_segments[j].start * 0.02
                    word_end_times[i] = word_segments[j].end * 0.02
                    j += 1
           
            # Propagate timings to non-aligned words
            current_start = 0.0
            current_end = 0.0
            for i in range(len(original_words)):
                if word_end_times[i] > 0:  # aligned
                    current_start = word_start_times[i]
                    current_end = word_end_times[i]
                else:
                    word_start_times[i] = current_start
                    word_end_times[i] = current_end
           
            # Handle initial non-aligned
            if align_to_orig and align_to_orig[0] > 0:
                first_i = align_to_orig[0]
                first_end = word_end_times[first_i]
                for i in range(first_i):
                    word_start_times[i] = 0.0
                    word_end_times[i] = first_end
           
            # Calculate character positions
            word_starts = []
            offset = 0
            for word in original_words:
                word_starts.append(offset)
                offset += len(word) + 1
           
            print(f"Alignment successful: timings for {len(original_words)} words")
           
            return {
                'words': original_words,
                'word_start_times': word_start_times,
                'word_end_times': word_end_times,
                'word_starts': word_starts
            }
           
        except Exception as e:
            print(f"Alignment error: {e}")
            import traceback
            traceback.print_exc()
            # Fall back to simple timing
            return self.create_fallback_alignment(words, waveform, sample_rate)

    def create_fallback_alignment(self, words, waveform, sample_rate):
        """Create simple uniform timing when alignment fails"""
        print("Creating fallback alignment with uniform timing")
       
        # Calculate total duration
        duration = waveform.size(1) / sample_rate
       
        # Distribute time evenly across words
        word_duration = duration / len(words) if words else 1.0
       
        word_start_times = [i * word_duration for i in range(len(words))]
        word_end_times = [(i + 1) * word_duration for i in range(len(words))]
       
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

    def start_playback(self, text, alignment_data):
        """Start audio playback with highlighting using GStreamer (runs on main thread)"""
        try:
            # Update text buffer
            buffer = self.textview.get_buffer()
            buffer.set_text(text)
           
            # Store alignment data
            self.words = alignment_data['words']
            self.word_start_times = alignment_data['word_start_times']
            self.word_end_times = alignment_data['word_end_times']
            self.word_starts = alignment_data['word_starts']
           
            # Verify the file exists before creating player
            if not self.current_audio_file or not os.path.exists(self.current_audio_file):
                 raise RuntimeError(f"Audio file does not exist: {self.current_audio_file}")

            # Create GStreamer player elements
            self.current_player = Gst.ElementFactory.make("playbin", "player")
            if not self.current_player:
                raise RuntimeError("Could not create GStreamer player element 'playbin'")

            # Set the URI to the standardized file - ensure correct formatting
            uri = f"file://{self.current_audio_file}"
            print(f"GStreamer: Setting URI to {uri}")
            self.current_player.set_property("uri", uri)

            # Clear the event in case it was set previously
            self.playback_finished_event.clear()

            # Start playback
            ret = self.current_player.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Failed to start GStreamer playback")

            print(f"GStreamer: Started playing {self.current_audio_file}")

            # Start highlighting
            self.start_time = time.time()
            self.current_timer = GLib.timeout_add(50, self.update_highlight)
           
            # Update UI
            self.set_buttons_state(False, True)
            self.update_status("Playing...")

            # Start a thread to monitor playback state and duration
            self.current_playback_thread = threading.Thread(target=self.monitor_playback, daemon=True)
            self.current_playback_thread.start()
           
        except Exception as e:
            print(f"Playback error: {e}")
            self.update_status(f"Playback error: {str(e)}")
            self.set_buttons_state(True, False)

    def monitor_playback(self):
        """Monitor GStreamer playback state in a separate thread."""
        bus = self.current_player.get_bus()
        while True:
            # Wait for a message on the bus (timeout in nanoseconds)
            msg = bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            
            if msg:
                if msg.type == Gst.MessageType.EOS:
                    # End of stream reached
                    print("GStreamer: End of stream reached")
                    self.playback_finished_event.set()
                    GLib.idle_add(self.on_playback_finished)
                    break
                elif msg.type == Gst.MessageType.ERROR:
                    # Error occurred
                    err, debug_info = msg.parse_error()
                    print(f"GStreamer: Error received from element {msg.src.get_name()}: {err.message}")
                    print(f"GStreamer: Debugging information: {debug_info}")
                    self.playback_finished_event.set()
                    GLib.idle_add(self.update_status, f"Playback error: {err.message}")
                    GLib.idle_add(self.set_buttons_state, True, False)
                    break
            # Check if the stop event was signaled (in case stop is called externally)
            elif self.playback_finished_event.is_set():
                # Stop was called, exit loop
                break

    def update_highlight(self):
        """Update word highlighting during playback"""
        try:
            if not self.current_player:
                print("update_highlight: No player instance found, stopping.")
                return False

            # Check GStreamer state to see if playback is still running
            # Get the current state of the player
            state, pending, _ = self.current_player.get_state(Gst.CLOCK_TIME_NONE)
            print(f"GStreamer State: {state}, Pending: {pending}") # Debug print
            if state == Gst.State.NULL or state == Gst.State.READY:
                # Playback has stopped or finished
                print("update_highlight: Player state indicates playback finished.")
                self.on_playback_finished()
                return False
           
            current_time = time.time() - self.start_time
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
           
            # Remove previous highlighting
            buffer.remove_tag(self.highlight_tag, start, end)
           
            # Highlight all words whose time range includes current_time
            highlighted = False
            for i in range(len(self.words)):
                if (i < len(self.word_start_times) and i < len(self.end_times) and
                    self.word_start_times[i] <= current_time < self.word_end_times[i]):
                   
                    if i < len(self.word_starts):
                        s_iter = buffer.get_iter_at_offset(self.word_starts[i])
                        e_iter = buffer.get_iter_at_offset(self.word_starts[i] + len(self.words[i]))
                        buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
                        highlighted = True
           
            return True
           
        except Exception as e:
            print(f"Highlight error: {e}")
            return False

    def on_stop(self, button):
        """Stop current playback using GStreamer"""
        if self.current_player:
            print("GStreamer: Stopping playback...")
            # Set state to NULL to stop
            ret = self.current_player.set_state(Gst.State.NULL)
            if ret == Gst.StateChangeReturn.FAILURE:
                print("GStreamer: Failed to set state to NULL during stop.")
            # Clear the player reference
            self.current_player = None
            print("GStreamer: Playback stopped.")

        # Signal the monitoring thread to stop if it's running
        self.playback_finished_event.set()

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
        """Called when playback finishes naturally"""
        # The player reference should already be cleared by the monitor thread
        # or on_stop, but clear it here just in case.
        if self.current_player:
            ret = self.current_player.set_state(Gst.State.NULL)
            if ret == Gst.StateChangeReturn.FAILURE:
                print("GStreamer: Failed to set state to NULL during finish.")
            self.current_player = None
        # Ensure the event is set
        self.playback_finished_event.set()

        # Clear the timer
        if self.current_timer:
            GLib.source_remove(self.current_timer)
            self.current_timer = None

        # Clear highlighting
        if self.highlight_tag:
            buffer = self.textview.get_buffer()
            start, end = buffer.get_bounds()
            buffer.remove_tag(self.highlight_tag, start, end)

        self.set_buttons_state(True, False)
        self.update_status("Ready")
        return False # Indicates timeout should not repeat

    # Alignment methods (unchanged from original)
    def get_trellis(self, emission, tokens, blank_id=0):
        num_frame = emission.size(0)
        num_tokens = len(tokens)
       
        # Check if we have enough frames for the tokens
        if num_frame < num_tokens:
            print(f"Warning: Not enough frames ({num_frame}) for tokens ({num_tokens})")
            # Still try, but this might cause issues
       
        trellis = torch.full((num_frame, num_tokens), -float("inf"))
        trellis[0, 0] = emission[0, blank_id]
       
        for t in range(1, num_frame):
            trellis[t, 0] = trellis[t - 1, 0] + emission[t, blank_id]
       
        for j in range(1, num_tokens):
            trellis[0, j] = -float("inf")
       
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
                    # If we reach the beginning of time but still have tokens left,
                    # something went wrong - return empty path to trigger fallback
                    print(f"Backtrack failed: t={t}, j={j}")
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
        except Exception as e:
            print(f"Backtrack exception: {e}")
            return []

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
