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
import queue
from collections import deque
import nltk.tokenize

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

@dataclass
class SentenceData:
    text: str
    start_char: int
    end_char: int
    words: list
    word_starts: list
    audio_file: str = None
    word_start_times: list = None
    word_end_times: list = None
    processed: bool = False

class TTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)
        
        # Initialize models once
        print("Loading TTS pipeline...")
        try:
            # Disable torch_compile to avoid CUDA compilation issues
            self.pipe = pipeline.Pipeline(s2a_ref='collabora/whisperspeech:s2a-q4-tiny-en+pl.model', torch_compile=False)
            print("TTS pipeline loaded successfully (torch_compile disabled)")
        except Exception as e:
            print(f"Error loading TTS pipeline: {e}")
            raise
        
        # Initialize ASR model once
        print("Loading ASR model...")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"Using device: {self.device}")
        
        try:
            self.bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
            self.asr_model = self.bundle.get_model().to(self.device)
            self.labels = self.bundle.get_labels()
            print(f"ASR model loaded successfully on {self.device}")
        except Exception as e:
            print(f"Error loading ASR model on {self.device}: {e}")
            print("Falling back to CPU...")
            self.device = torch.device("cpu")
            self.bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
            self.asr_model = self.bundle.get_model().to(self.device)
            self.labels = self.bundle.get_labels()
            print(f"ASR model loaded on CPU")
        
        # Sentence processing
        self.sentences = []
        self.current_sentence_idx = 0
        self.processing_queue = queue.Queue()
        self.processing_thread = None
        self.processing_active = False
        
        # Safety mode - skip alignment if too many errors
        self.alignment_errors = 0
        self.max_alignment_errors = 3
        self.skip_alignment = False
        
        # Playback state
        self.current_player = None
        self.current_timer = None
        self.playback_start_time = None
        self.highlight_tag = None
        self.is_playing = False
        
        print("Models loaded successfully!")

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(700, 500)
        self.window.set_title("WhisperSpeech TTS - Sentence by Sentence")
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        
        # Text input area
        text_label = Gtk.Label(label="Enter text to speak (will be processed sentence by sentence):")
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
        
        self.speak_button = Gtk.Button(label="🔊 Speak All")
        self.speak_button.add_css_class("suggested-action")
        self.speak_button.connect("clicked", self.on_speak)
        button_box.append(self.speak_button)
        
        self.stop_button = Gtk.Button(label="⏹ Stop")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self.on_stop)
        button_box.append(self.stop_button)
        
        main_box.append(button_box)
        
        # Progress info
        self.progress_label = Gtk.Label(label="")
        self.progress_label.add_css_class("dim-label")
        main_box.append(self.progress_label)
        
        # Status label
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)
        
        self.window.set_content(main_box)
        self.window.present()
        
        # Set up highlight tags
        buffer = self.textview.get_buffer()
        rgba = Gdk.RGBA()
        rgba.parse("rgba(255, 255, 0, 0.5)")
        self.highlight_tag = buffer.create_tag("highlight", 
                                             background="yellow", 
                                             background_rgba=rgba)
        
        rgba_sentence = Gdk.RGBA()
        rgba_sentence.parse("rgba(0, 255, 0, 0.2)")
        self.sentence_tag = buffer.create_tag("sentence", 
                                            background_rgba=rgba_sentence)

    def update_status(self, message):
        """Thread-safe status update"""
        GLib.idle_add(lambda: self.status_label.set_text(message))

    def update_progress(self, message):
        """Thread-safe progress update"""
        GLib.idle_add(lambda: self.progress_label.set_text(message))

    def set_buttons_state(self, speak_sensitive, stop_sensitive):
        """Thread-safe button state update"""
        def update():
            self.speak_button.set_sensitive(speak_sensitive)
            self.stop_button.set_sensitive(stop_sensitive)
        GLib.idle_add(update)

    def split_into_sentences(self, text):
        """Split text into sentences and calculate character positions"""
        try:
            # Try to use nltk sentence tokenizer
            import nltk
            try:
                nltk.data.find('tokenizers/punkt')
            except LookupError:
                print("Downloading NLTK punkt tokenizer...")
                nltk.download('punkt', quiet=True)
            
            sentences = nltk.sent_tokenize(text)
        except:
            # Fallback to simple splitting
            sentences = re.split(r'[.!?]+', text)
            sentences = [s.strip() for s in sentences if s.strip()]
        
        sentence_data = []
        current_pos = 0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Find the sentence in the original text
            start_pos = text.find(sentence, current_pos)
            if start_pos == -1:
                # Fallback: approximate position
                start_pos = current_pos
            
            end_pos = start_pos + len(sentence)
            
            # Split sentence into words and calculate positions
            words = sentence.split()
            word_starts = []
            word_pos = 0
            
            for word in words:
                word_idx = sentence.find(word, word_pos)
                if word_idx != -1:
                    word_starts.append(start_pos + word_idx)
                    word_pos = word_idx + len(word)
                else:
                    # Fallback
                    word_starts.append(start_pos + word_pos)
                    word_pos += len(word) + 1
            
            sentence_data.append(SentenceData(
                text=sentence,
                start_char=start_pos,
                end_char=end_pos,
                words=words,
                word_starts=word_starts
            ))
            
            current_pos = end_pos
        
        return sentence_data

    def on_speak(self, button):
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False).strip()
        
        if not text:
            return
        
        # Stop any current processing/playback
        self.on_stop(None)
        
        # Split text into sentences
        self.sentences = self.split_into_sentences(text)
        if not self.sentences:
            self.update_status("No sentences found")
            return
        
        print(f"Split into {len(self.sentences)} sentences")
        
        # Reset state
        self.current_sentence_idx = 0
        self.is_playing = True
        
        # Start processing and playback
        self.set_buttons_state(False, True)
        self.update_status("Processing sentences...")
        self.update_progress(f"Sentence 1 of {len(self.sentences)}")
        
        # Start background processing thread
        self.start_processing_thread()
        
        # Start with first sentence
        self.process_and_play_next()

    def start_processing_thread(self):
        """Start background thread for processing upcoming sentences"""
        self.processing_active = True
        self.processing_thread = threading.Thread(target=self.background_processor, daemon=True)
        self.processing_thread.start()

    def background_processor(self):
        """Background thread that pre-processes upcoming sentences"""
        while self.processing_active:
            try:
                # Get next sentence to process
                sentence_idx = None
                for i, sentence in enumerate(self.sentences):
                    if not sentence.processed and i > self.current_sentence_idx:
                        sentence_idx = i
                        break
                
                if sentence_idx is None:
                    time.sleep(0.1)
                    continue
                
                sentence = self.sentences[sentence_idx]
                print(f"Background processing sentence {sentence_idx + 1}: {sentence.text[:50]}...")
                
                # Generate audio with error handling
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    wav_file = f.name
                
                try:
                    self.pipe.generate_to_file(wav_file, sentence.text)
                    
                    # Process alignment
                    alignment_data = self.process_sentence_alignment(wav_file, sentence.words)
                    
                    if alignment_data:
                        sentence.audio_file = wav_file
                        sentence.word_start_times = alignment_data['word_start_times']
                        sentence.word_end_times = alignment_data['word_end_times']
                        sentence.processed = True
                        print(f"Background processing complete for sentence {sentence_idx + 1}")
                    else:
                        print(f"Background alignment failed for sentence {sentence_idx + 1}")
                        if os.path.exists(wav_file):
                            os.unlink(wav_file)
                            
                except Exception as bg_error:
                    print(f"Background processing error for sentence {sentence_idx + 1}: {bg_error}")
                    if os.path.exists(wav_file):
                        os.unlink(wav_file)
                
            except Exception as e:
                print(f"Background processor error: {e}")
                time.sleep(0.5)  # Wait a bit before retrying

    def process_and_play_next(self):
        """Process current sentence and start playback"""
        if not self.is_playing or self.current_sentence_idx >= len(self.sentences):
            self.on_playback_complete()
            return
        
        sentence = self.sentences[self.current_sentence_idx]
        
        # Highlight current sentence
        self.highlight_current_sentence()
        
        if sentence.processed:
            # Already processed in background
            self.start_sentence_playback(sentence)
        else:
            # Process current sentence
            threading.Thread(target=self.process_current_sentence, daemon=True).start()

    def process_current_sentence(self):
        """Process current sentence in thread"""
        sentence = self.sentences[self.current_sentence_idx]
        
        try:
            print(f"Processing sentence {self.current_sentence_idx + 1}: {sentence.text[:50]}...")
            
            # Generate audio with better error handling
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                wav_file = f.name
            
            try:
                self.pipe.generate_to_file(wav_file, sentence.text)
                print(f"Audio generated successfully for sentence {self.current_sentence_idx + 1}")
            except Exception as tts_error:
                print(f"TTS generation error: {tts_error}")
                if os.path.exists(wav_file):
                    os.unlink(wav_file)
                GLib.idle_add(self.move_to_next_sentence)
                return
            
            # Process alignment
            alignment_data = self.process_sentence_alignment(wav_file, sentence.words)
            
            if alignment_data:
                sentence.audio_file = wav_file
                sentence.word_start_times = alignment_data['word_start_times']
                sentence.word_end_times = alignment_data['word_end_times']
                sentence.processed = True
                
                # Start playback on main thread
                GLib.idle_add(self.start_sentence_playback, sentence)
            else:
                print(f"Alignment failed for sentence {self.current_sentence_idx + 1}")
                if os.path.exists(wav_file):
                    os.unlink(wav_file)
                GLib.idle_add(self.move_to_next_sentence)
                
        except Exception as e:
            print(f"Processing error: {e}")
            import traceback
            traceback.print_exc()
            GLib.idle_add(self.move_to_next_sentence)

    def highlight_current_sentence(self):
        """Highlight the current sentence being processed/played"""
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.sentence_tag, start, end)
        
        sentence = self.sentences[self.current_sentence_idx]
        s_iter = buffer.get_iter_at_offset(sentence.start_char)
        e_iter = buffer.get_iter_at_offset(sentence.end_char)
        buffer.apply_tag(self.sentence_tag, s_iter, e_iter)

    def start_sentence_playback(self, sentence):
        """Start playing a processed sentence"""
        if not sentence.audio_file or not os.path.exists(sentence.audio_file):
            self.move_to_next_sentence()
            return
        
        try:
            # Start audio playback
            self.current_player = subprocess.Popen(['aplay', sentence.audio_file], 
                                                 stdout=subprocess.DEVNULL, 
                                                 stderr=subprocess.DEVNULL)
            
            # Start word highlighting
            self.playback_start_time = time.time()
            self.current_timer = GLib.timeout_add(50, lambda: self.update_sentence_highlight(sentence))
            
            self.update_status(f"Playing sentence {self.current_sentence_idx + 1}")
            
            # Calculate duration and schedule next sentence
            waveform, sample_rate = torchaudio.load(sentence.audio_file)
            duration = waveform.size(1) / sample_rate
            
            # Schedule moving to next sentence
            GLib.timeout_add(int(duration * 1000) + 500, self.move_to_next_sentence)
            
        except Exception as e:
            print(f"Playback error: {e}")
            self.move_to_next_sentence()

    def update_sentence_highlight(self, sentence):
        """Update word highlighting for current sentence"""
        if not self.current_player or not self.is_playing:
            return False
            
        # Check if player is still running
        if self.current_player.poll() is not None:
            return False
        
        current_time = time.time() - self.playback_start_time
        buffer = self.textview.get_buffer()
        
        # Remove previous word highlighting
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        
        # Find and highlight current word
        if sentence.word_start_times and sentence.word_end_times:
            for i, word in enumerate(sentence.words):
                if (i < len(sentence.word_start_times) and i < len(sentence.word_end_times) and
                    sentence.word_start_times[i] <= current_time < sentence.word_end_times[i]):
                    
                    if i < len(sentence.word_starts):
                        word_start = sentence.word_starts[i]
                        word_end = word_start + len(word)
                        
                        s_iter = buffer.get_iter_at_offset(word_start)
                        e_iter = buffer.get_iter_at_offset(word_end)
                        buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
                    break
        
        return True

    def move_to_next_sentence(self):
        """Move to the next sentence"""
        # Clean up current playback
        if self.current_player:
            try:
                self.current_player.terminate()
            except:
                pass
            self.current_player = None
        
        if self.current_timer:
            GLib.source_remove(self.current_timer)
            self.current_timer = None
        
        # Clean up current audio file
        if (self.current_sentence_idx < len(self.sentences) and 
            self.sentences[self.current_sentence_idx].audio_file):
            try:
                os.unlink(self.sentences[self.current_sentence_idx].audio_file)
                self.sentences[self.current_sentence_idx].audio_file = None
            except:
                pass
        
        # Move to next sentence
        self.current_sentence_idx += 1
        
        if self.current_sentence_idx < len(self.sentences):
            self.update_progress(f"Sentence {self.current_sentence_idx + 1} of {len(self.sentences)}")
            self.process_and_play_next()
        else:
            self.on_playback_complete()
        
        return False  # Don't repeat timer

    def on_playback_complete(self):
        """Called when all sentences are complete"""
        self.is_playing = False
        self.processing_active = False
        
        # Clear highlighting
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.sentence_tag, start, end)
        
        self.set_buttons_state(True, False)
        self.update_status("Complete!")
        self.update_progress("")
        
        # Clean up any remaining audio files
        for sentence in self.sentences:
            if sentence.audio_file and os.path.exists(sentence.audio_file):
                try:
                    os.unlink(sentence.audio_file)
                except:
                    pass
                sentence.audio_file = None

    def on_stop(self, button):
        """Stop current playback and processing"""
        self.is_playing = False
        self.processing_active = False
        
        # Stop current playback
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
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.sentence_tag, start, end)
        
        # Clean up audio files
        for sentence in self.sentences:
            if sentence.audio_file and os.path.exists(sentence.audio_file):
                try:
                    os.unlink(sentence.audio_file)
                except:
                    pass
                sentence.audio_file = None
        
        self.set_buttons_state(True, False)
        self.update_status("Stopped")
        self.update_progress("")

    def process_sentence_alignment(self, wav_file, words):
        """Process alignment for a single sentence (much more reliable)"""
        
        # Skip alignment if we've had too many errors
        if self.skip_alignment:
            print("Skipping alignment due to previous errors, using fallback")
            waveform, sample_rate = torchaudio.load(wav_file)
            return self.create_fallback_alignment(words, waveform, sample_rate)
        
        try:
            # Load and process audio
            waveform, sample_rate = torchaudio.load(wav_file)
            if sample_rate != self.bundle.sample_rate:
                waveform = torchaudio.functional.resample(waveform, sample_rate, self.bundle.sample_rate)
            
            # Get ASR emissions with better error handling
            try:
                with torch.inference_mode():
                    # Clear CUDA cache before processing
                    if self.device.type == 'cuda':
                        torch.cuda.empty_cache()
                    
                    emissions, _ = self.asr_model(waveform.to(self.device))
                    emissions = torch.log_softmax(emissions, dim=-1)
            except RuntimeError as e:
                if "CUDA" in str(e) or "assert" in str(e).lower():
                    print(f"CUDA error in ASR model, switching to CPU: {e}")
                    # Move model to CPU and retry
                    self.asr_model = self.asr_model.cpu()
                    self.device = torch.device("cpu")
                    try:
                        with torch.inference_mode():
                            emissions, _ = self.asr_model(waveform.cpu())
                            emissions = torch.log_softmax(emissions, dim=-1)
                    except Exception as cpu_error:
                        print(f"CPU fallback also failed: {cpu_error}")
                        self.alignment_errors += 1
                        if self.alignment_errors >= self.max_alignment_errors:
                            self.skip_alignment = True
                            print("Too many alignment errors, disabling alignment")
                        return self.create_fallback_alignment(words, waveform, sample_rate)
                else:
                    raise e
            
            emission = emissions[0].cpu().detach()
            
            # Prepare transcript for alignment with better validation
            clean_words = []
            for word in words:
                clean_word = re.sub(r'[^A-Z]', '', word.upper())
                if clean_word:  # Only add non-empty words
                    clean_words.append(clean_word)
            
            if not clean_words:
                print("No valid words for alignment, using fallback")
                return self.create_fallback_alignment(words, waveform, sample_rate)
            
            # Create transcript
            transcript = "|" + "|".join(clean_words) + "|"
            dictionary = {c: i for i, c in enumerate(self.labels)}
            
            # Validate all characters exist and are within bounds
            tokens = []
            for c in transcript:
                if c not in dictionary:
                    print(f"Character '{c}' not in dictionary, using fallback alignment")
                    return self.create_fallback_alignment(words, waveform, sample_rate)
                
                token_id = dictionary[c]
                if token_id >= emission.size(1):  # Check bounds
                    print(f"Token ID {token_id} out of bounds (max: {emission.size(1)-1}), using fallback")
                    return self.create_fallback_alignment(words, waveform, sample_rate)
                
                tokens.append(token_id)
            
            # Additional safety checks
            if len(tokens) == 0:
                return self.create_fallback_alignment(words, waveform, sample_rate)
            
            if emission.size(0) < len(tokens):
                print(f"Not enough frames ({emission.size(0)}) for tokens ({len(tokens)}), using fallback")
                return self.create_fallback_alignment(words, waveform, sample_rate)
            
            # Perform alignment with better error handling
            try:
                trellis = self.get_trellis(emission, tokens)
                path = self.backtrack(trellis, emission, tokens)
                
                if not path:
                    print("Backtracking failed, using fallback alignment")
                    return self.create_fallback_alignment(words, waveform, sample_rate)
                    
                segments = self.merge_repeats(path, transcript)
                word_segments = self.merge_words(segments)
                
                if not word_segments:
                    print("No word segments found, using fallback alignment")
                    return self.create_fallback_alignment(words, waveform, sample_rate)
                
                # Calculate timing
                word_start_times = [max(0, w.start * 0.02) for w in word_segments]
                word_end_times = [max(w.start * 0.02, w.end * 0.02) for w in word_segments]
                
                # Ensure we have timing for all words
                while len(word_start_times) < len(words):
                    if word_start_times:
                        last_end = word_end_times[-1]
                        word_start_times.append(last_end)
                        word_end_times.append(last_end + 0.5)
                    else:
                        word_start_times.append(0.0)
                        word_end_times.append(0.5)
                
                # Ensure times are monotonic
                for i in range(1, len(word_start_times)):
                    if word_start_times[i] < word_end_times[i-1]:
                        word_start_times[i] = word_end_times[i-1]
                    if word_end_times[i] < word_start_times[i]:
                        word_end_times[i] = word_start_times[i] + 0.1
                
                print(f"Alignment successful for {len(words)} words")
                return {
                    'word_start_times': word_start_times[:len(words)],
                    'word_end_times': word_end_times[:len(words)]
                }
                
            except Exception as alignment_error:
                print(f"Alignment processing error: {alignment_error}")
                self.alignment_errors += 1
                if self.alignment_errors >= self.max_alignment_errors:
                    self.skip_alignment = True
                    print("Too many alignment errors, disabling alignment for future sentences")
                return self.create_fallback_alignment(words, waveform, sample_rate)
            
        except Exception as e:
            print(f"Sentence alignment error: {e}")
            self.alignment_errors += 1
            if self.alignment_errors >= self.max_alignment_errors:
                self.skip_alignment = True
                print("Too many alignment errors, disabling alignment")
            
            # Load waveform for fallback
            try:
                waveform, sample_rate = torchaudio.load(wav_file)
                return self.create_fallback_alignment(words, waveform, sample_rate)
            except:
                # Even simpler fallback
                return {
                    'word_start_times': [i * 0.5 for i in range(len(words))],
                    'word_end_times': [(i + 1) * 0.5 for i in range(len(words))]
                }

    def create_fallback_alignment(self, words, waveform, sample_rate):
        """Create simple uniform timing when alignment fails"""
        duration = waveform.size(1) / sample_rate
        word_duration = duration / len(words) if words else 1.0
        
        word_start_times = [i * word_duration for i in range(len(words))]
        word_end_times = [(i + 1) * word_duration for i in range(len(words))]
        
        return {
            'word_start_times': word_start_times,
            'word_end_times': word_end_times
        }

    # Alignment methods (same as before but more robust)
    def get_trellis(self, emission, tokens, blank_id=0):
        """Create trellis with better bounds checking"""
        num_frame = emission.size(0)
        num_tokens = len(tokens)
        
        # Validate inputs
        if num_frame <= 0 or num_tokens <= 0:
            raise ValueError(f"Invalid dimensions: frames={num_frame}, tokens={num_tokens}")
        
        # Check token bounds
        max_token = max(tokens) if tokens else 0
        if max_token >= emission.size(1):
            raise ValueError(f"Token {max_token} out of bounds (emission dim: {emission.size(1)})")
        
        trellis = torch.full((num_frame, num_tokens), -float("inf"))
        
        # Initialize first frame
        try:
            trellis[0, 0] = emission[0, blank_id]
        except IndexError as e:
            raise ValueError(f"Blank token {blank_id} out of bounds") from e
        
        # Fill first column
        for t in range(1, num_frame):
            trellis[t, 0] = trellis[t - 1, 0] + emission[t, blank_id]
        
        # Initialize first row (except first element)
        for j in range(1, num_tokens):
            trellis[0, j] = -float("inf")
        
        # Fill the rest of the trellis
        for t in range(1, num_frame):
            for j in range(1, min(t + 1, num_tokens)):
                try:
                    staying = trellis[t - 1, j] + emission[t, blank_id]
                    changing = trellis[t - 1, j - 1] + emission[t, tokens[j]]
                    trellis[t, j] = torch.maximum(staying, changing)
                except IndexError as e:
                    print(f"Index error at t={t}, j={j}, token={tokens[j]}: {e}")
                    raise
        
        return trellis

    def backtrack(self, trellis, emission, tokens, blank_id=0):
        """Backtrack with better error handling"""
        try:
            if trellis.size(0) == 0 or trellis.size(1) == 0:
                return []
                
            t, j = trellis.size(0) - 1, trellis.size(1) - 1
            
            # Validate starting position
            if blank_id >= emission.size(1):
                print(f"Blank ID {blank_id} out of bounds")
                return []
            
            path = [Point(j, t, emission[t, blank_id].exp().item())]
            
            while j > 0:
                if t <= 0:
                    print(f"Backtrack failed: reached t=0 with j={j} remaining")
                    return []
                
                # Validate token access
                if j >= len(tokens) or tokens[j] >= emission.size(1):
                    print(f"Invalid token access: j={j}, token={tokens[j] if j < len(tokens) else 'OOB'}")
                    return []
                
                try:
                    p_stay = emission[t - 1, blank_id]
                    p_change = emission[t - 1, tokens[j]]
                    stayed = trellis[t - 1, j] + p_stay
                    changed = trellis[t - 1, j - 1] + p_change
                    
                    t -= 1
                    if changed > stayed:
                        j -= 1
                    
                    prob = (p_change if changed > stayed else p_stay).exp().item()
                    path.append(Point(j, t, prob))
                    
                except (IndexError, RuntimeError) as e:
                    print(f"Backtrack error at t={t}, j={j}: {e}")
                    return []
            
            # Fill remaining time steps
            while t > 0:
                try:
                    prob = emission[t - 1, blank_id].exp().item()
                    path.append(Point(j, t - 1, prob))
                    t -= 1
                except (IndexError, RuntimeError) as e:
                    print(f"Final backtrack error at t={t}: {e}")
                    break
            
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
