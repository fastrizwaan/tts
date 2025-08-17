import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Pango
import subprocess
import time
import tempfile
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
import queue
from collections import deque
import nltk.tokenize
import json
import wave
import urllib
import string

# Piper TTS imports - try different import methods
PIPER_AVAILABLE = False
piper_tts = None
try:
    from piper.voice import PiperVoice
    PIPER_AVAILABLE = True
    print("Piper TTS imported successfully")
except ImportError:
    try:
        # Method 3: Try subprocess approach (fallback to CLI)
        result = subprocess.run(['piper', '--help'], capture_output=True, text=True)
        if result.returncode == 0:
            PIPER_CLI_AVAILABLE = True
            print("Piper CLI available")
        else:
            PIPER_CLI_AVAILABLE = False
    except FileNotFoundError:
        PIPER_CLI_AVAILABLE = False
        print("Piper not found via any method")

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

class PiperTTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)

        # Initialize voice settings FIRST (before init_piper_tts)
        self.current_voice_name = "en_US-lessac-medium" # Default voice
        self.current_voice_path = None
        self.speech_rate = 1.0
        self.speech_volume = 1.0
        self.piper_voice = None
        self.available_voices = []
        self.use_cli = False

        # Check Piper availability and initialize
        print("Checking Piper TTS availability...")
        self.init_piper_tts()

        # Sentence processing
        self.sentences = []
        self.current_sentence_idx = 0
        self.resume_sentence_idx = 0  # For pause/resume functionality

        # Processing queue and threading
        self.processing_queue = queue.Queue()
        self.processing_thread = None
        self.processing_active = False
        self.processing_event = threading.Event()

        # Playback state
        self.current_player = None
        self.current_timer = None
        self.word_highlight_timer = None
        self.playback_start_time = None
        self.highlight_tag = None
        self.is_playing = False
        self.is_paused = False
        self.highlight_all_words = False
        self.use_bold_highlight = True

        # Reading mode options
        self.reading_mode = "from_start"  # "from_start", "from_cursor", "from_current_sentence"

        # Word highlighting optimization
        self.current_highlighted_word = -1
        self.highlight_refresh_rate = 25 # ms

        # Zoom settings
        self.default_font_size = 12
        self.current_font_size = self.default_font_size

        # UI Elements to be referenced later
        self.split_view = None
        self.sidebar_button = None
        self.voice_button = None
        self.reading_mode_button = None
        self.speak_button = None
        self.pause_button = None
        self.stop_button = None
        self.textview = None
        self.speed_scale = None
        self.status_label = None
        self.progress_label = None

        print("Piper TTS App initialized successfully!")

    def init_piper_tts(self):
        """Initialize Piper TTS with fallback methods"""
        try:
            if PIPER_AVAILABLE:
                print("Using Piper TTS Python library")
                self.use_cli = False
                self.load_current_voice()
                if self.current_voice_path and os.path.exists(self.current_voice_path):
                    self.piper_voice = PiperVoice.load(self.current_voice_path)
                # Check for available voices or defaults
                self.discover_cli_voices()
                return
            elif 'PIPER_CLI_AVAILABLE' in globals() and PIPER_CLI_AVAILABLE:
                print("Using Piper CLI interface")
                self.use_cli = True
                self.load_current_voice()
                # Check for available voices via CLI
                self.discover_cli_voices()
                return
            else:
                raise Exception("No Piper TTS method available")
        except Exception as e:
            print(f"Error initializing Piper TTS: {e}")
            raise Exception("Piper TTS not available")

    def discover_cli_voices(self):
        """Discover available voices via CLI with multi-language support"""
        try:
            # Try to get voice list from Piper CLI
            result = subprocess.run(['piper', '--list-voices'], capture_output=True, text=True)
            if result.returncode == 0:
                # Parse voice list (format may vary)
                voices = []
                for line in result.stdout.split('\n'):
                    if line.strip() and not line.startswith('#'):
                        voices.append(line.strip())
                self.available_voices = voices[:15] # Limit to first 15
            else:
                # Default voices including Hindi and other languages
                self.available_voices = [
                    "en_US-lessac-medium",
                    "en_US-ryan-medium",
                    "en_GB-alan-medium",
                    "hi_IN-pratham-medium",  # Hindi voice
                    "es_ES-sharvard-medium", # Spanish
                    "fr_FR-upmc-medium",     # French
                    "de_DE-thorsten-medium", # German
                ]
        except Exception as e:
            print(f"Error discovering voices: {e}")
            self.available_voices = ["en_US-lessac-medium", "hi_IN-pratham-medium"]

    def load_current_voice(self):
        """Load or download the current voice model with multi-language support"""
        model_name = self.current_voice_name
        model_dir = os.path.expanduser("~/.var/app/io.github.fastrizwaan.tts/data/piper_models")
        os.makedirs(model_dir, exist_ok=True)
        model_onnx = os.path.join(model_dir, f"{model_name}.onnx")
        model_json = os.path.join(model_dir, f"{model_name}.onnx.json")

        if not os.path.exists(model_onnx) or not os.path.exists(model_json):
            print(f"Downloading Piper model {model_name}...")
            voice_parts = model_name.split('-')
            lang = voice_parts[0] # e.g., en_US, hi_IN
            speaker = voice_parts[1] # e.g., lessac, pratham
            quality = voice_parts[2] # e.g., medium
            # Handle different language codes for download URLs
            lang_code = lang.split('_')[0]  # en, hi, es, fr, de, etc.
            base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang_code}/{lang}/{speaker}/{quality}/"
            try:
                print(f"Downloading from: {base_url}")
                urllib.request.urlretrieve(base_url + f"{model_name}.onnx", model_onnx)
                urllib.request.urlretrieve(base_url + f"{model_name}.onnx.json", model_json)
                print("Model downloaded successfully")
            except Exception as e:
                print(f"Error downloading model: {e}")
                raise
        self.current_voice_path = model_onnx

    def get_cursor_position(self):
        """Get current cursor position in the text"""
        buffer = self.textview.get_buffer()
        cursor_mark = buffer.get_insert()
        cursor_iter = buffer.get_iter_at_mark(cursor_mark)
        return cursor_iter.get_offset()

    def find_sentence_at_cursor(self):
        """Find the sentence index that contains the cursor position"""
        cursor_pos = self.get_cursor_position()
        for i, sentence in enumerate(self.sentences):
            if sentence.start_char <= cursor_pos <= sentence.end_char:
                return i
        # If cursor is between sentences, find the next sentence
        for i, sentence in enumerate(self.sentences):
            if cursor_pos < sentence.start_char:
                return i
        return len(self.sentences) - 1 if self.sentences else 0

    def determine_start_sentence(self):
        """Determine which sentence to start reading from based on reading mode"""
        if self.reading_mode == "from_cursor":
            return self.find_sentence_at_cursor()
        elif self.reading_mode == "from_current_sentence":
            return self.find_sentence_at_cursor()
        else:  # from_start
            return 0

    def stop_audio_immediately(self):
        """Immediately stop audio playback"""
        if self.current_player:
            try:
                # Send SIGTERM first
                self.current_player.terminate()
                # Wait briefly for graceful shutdown
                try:
                    self.current_player.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    # Force kill if not terminated
                    self.current_player.kill()
                    try:
                        self.current_player.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
            except Exception as e:
                print(f"Error stopping audio: {e}")
            finally:
                self.current_player = None

    def preprocess_text_for_language(self, text, language_code):
        """Preprocess text based on language for better pronunciation"""
        if language_code.startswith('hi'):  # Hindi
            # For Hindi text, ensure proper Unicode normalization
            import unicodedata
            text = unicodedata.normalize('NFC', text)
            # Add some basic Hindi-specific preprocessing
            # Replace common English words that might be mixed in
            english_to_hindi_numbers = {
                '0': '०', '1': '१', '2': '२', '3': '३', '4': '४',
                '5': '५', '6': '६', '7': '७', '8': '८', '9': '९'
            }
            # Only replace if the text seems to contain Devanagari
            if any('\u0900' <= c <= '\u097F' for c in text):
                for eng, hin in english_to_hindi_numbers.items():
                    text = text.replace(eng, hin)
        return text

    def start_sentence_playback(self, sentence):
        """Start playing a processed sentence with better debugging"""
        if not sentence.audio_file or not os.path.exists(sentence.audio_file):
            print(f"Audio file not found for sentence: {sentence.audio_file}")
            self.move_to_next_sentence()
            return
        # Validate the audio file
        try:
            file_size = os.path.getsize(sentence.audio_file)
            print(f"Playing audio file: {sentence.audio_file}, size: {file_size} bytes")
            if file_size < 100: # WAV files should be at least 100+ bytes
                print("Audio file too small, likely empty")
                self.move_to_next_sentence()
                return
            # Validate it's a proper WAV file
            with wave.open(sentence.audio_file, 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                duration = frames / float(rate)
                print(f"Audio duration: {duration:.2f} seconds")
        except Exception as e:
            print(f"Audio file validation error: {e}")
            self.move_to_next_sentence()
            return
        try:
            # Start audio playback with better error handling
            print(f"Starting aplay for: {sentence.audio_file}")
            self.current_player = subprocess.Popen(
                ['aplay', sentence.audio_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            # Check if process started successfully
            time.sleep(0.1) # Give it a moment
            if self.current_player.poll() is not None:
                # Process already terminated
                stdout, stderr = self.current_player.communicate()
                print(f"aplay failed immediately. stderr: {stderr.decode()}")
                self.update_status(f"Audio playback failed: {stderr.decode()}")
                self.move_to_next_sentence()
                return
            print(f"Audio playback started successfully")
            # Start precise word highlighting
            self.playback_start_time = time.time()
            self.current_highlighted_word = -1
            self.word_highlight_timer = GLib.timeout_add(
                self.highlight_refresh_rate,
                lambda: self.update_word_highlight(sentence)
            )
            self.update_status(f"Playing sentence {self.current_sentence_idx + 1}")
            # Calculate duration and schedule next sentence
            duration = self.get_audio_duration(sentence.audio_file)
            # Schedule moving to next sentence
            GLib.timeout_add(int(duration * 1000) + 500, self.move_to_next_sentence)
        except FileNotFoundError:
            print("aplay command not found")
            self.update_status("aplay not found - install alsa-utils: sudo apt install alsa-utils")
            self.move_to_next_sentence()
        except Exception as e:
            print(f"Playback error: {e}")
            self.update_status(f"Playback error: {e}")
            self.move_to_next_sentence()

    def clean_text_for_tts(self, text, lang_code):
        """Clean text for TTS using string.punctuation instead of regex"""
        if not lang_code.startswith('en'):
            # For non-English languages, be more conservative with text cleaning
            # Just normalize whitespace
            return ' '.join(text.split())
        else:
            # For English text, clean more aggressively using string.punctuation
            # First handle some special characters that should become spaces
            text = text.replace('•', ' ').replace('—', ' ').replace('–', ' ').replace('…', ' ')
            # Split into words and clean each word individually
            words = text.split()
            cleaned_words = []
            for word in words:
                # Strip punctuation from both ends, but preserve apostrophes and hyphens in the middle
                cleaned_word = word.strip(string.punctuation)
                # Only keep non-empty words
                if cleaned_word:
                    cleaned_words.append(cleaned_word)
            # Join back and normalize whitespace
            return ' '.join(cleaned_words)

    def generate_speech_with_piper(self, text, output_path):
        """Generate speech using Piper TTS with language-specific preprocessing and speed control"""
        try:
            print(f"Generating speech for: {text[:50]}... at speed {self.speech_rate}")
            # Preprocess text based on current voice language
            lang_code = self.current_voice_name.split('-')[0]
            preprocessed_text = self.preprocess_text_for_language(text.strip(), lang_code)
            if not preprocessed_text:
                print("Empty text after preprocessing, skipping")
                return False
            # Clean the text using the new method
            clean_text = self.clean_text_for_tts(preprocessed_text, lang_code)
            if not clean_text:
                print("Empty text after cleaning, skipping")
                return False
            print(f"Cleaned text for {lang_code}: {clean_text}")
            if self.use_cli:
                # Use CLI method with speed control
                if not self.current_voice_path or not os.path.exists(self.current_voice_path):
                    print(f"Voice model not found: {self.current_voice_path}")
                    return False
                # Create temporary WAV file for original speech
                temp_wav = output_path + "_temp.wav"
                cmd = [
                    'piper',
                    '--model', self.current_voice_path,
                    '--output_file', temp_wav
                ]
                # Add speed control if available in Piper CLI
                # Note: Some versions of Piper CLI support --length_scale parameter
                if self.speech_rate != 1.0:
                    # length_scale is inverse of speed (smaller = faster, larger = slower)
                    length_scale = 1.0 / self.speech_rate
                    cmd.extend(['--length_scale', str(length_scale)])
                print(f"Running command: {' '.join(cmd)}")
                try:
                    process = subprocess.run(
                        cmd,
                        input=clean_text,
                        text=True,
                        capture_output=True,
                        timeout=30
                    )
                    print(f"Piper return code: {process.returncode}")
                    if process.stdout:
                        print(f"Piper stdout: {process.stdout}")
                    if process.stderr:
                        print(f"Piper stderr: {process.stderr}")
                    if process.returncode == 0 and os.path.exists(temp_wav):
                        # If Piper CLI doesn't support speed control, use sox for post-processing
                        if self.speech_rate != 1.0 and '--length_scale' not in ' '.join(cmd):
                            print(f"Applying speed change using sox: {self.speech_rate}x")
                            try:
                                sox_cmd = ['sox', temp_wav, output_path, 'tempo', str(self.speech_rate)]
                                sox_result = subprocess.run(sox_cmd, capture_output=True, text=True)
                                if sox_result.returncode == 0:
                                    os.unlink(temp_wav)  # Remove temp file
                                else:
                                    print(f"Sox failed, using original speed: {sox_result.stderr}")
                                    # Fallback: just rename temp file
                                    os.rename(temp_wav, output_path)
                            except FileNotFoundError:
                                print("Sox not found, using original speed")
                                os.rename(temp_wav, output_path)
                        else:
                            # Piper handled speed or speed is 1.0
                            os.rename(temp_wav, output_path)
                        file_size = os.path.getsize(output_path)
                        print(f"Audio generated successfully via CLI: {output_path} ({file_size} bytes)")
                        # Validate the generated file
                        try:
                            with wave.open(output_path, 'rb') as wf:
                                frames = wf.getnframes()
                                if frames > 0:
                                    print(f"Generated audio has {frames} frames")
                                    return True
                                else:
                                    print("Generated audio has no frames")
                                    return False
                        except Exception as wav_error:
                            print(f"Generated file is not valid WAV: {wav_error}")
                            return False
                    else:
                        print(f"CLI generation failed or file not created")
                        return False
                except subprocess.TimeoutExpired:
                    print("Piper CLI timeout")
                    return False
                except Exception as e:
                    print(f"CLI error: {e}")
                    return False
            else:
                # Use Python API with speed control
                if not self.piper_voice:
                    print("Piper voice not loaded")
                    return False
                try:
                    # Get config for proper audio parameters
                    model_json = self.current_voice_path + '.json'
                    if os.path.exists(model_json):
                        with open(model_json) as f:
                            config = json.load(f)
                        sample_rate = config.get('audio', {}).get('sample_rate', 22050)
                        print(f"Voice config sample rate: {sample_rate}")
                    else:
                        sample_rate = 22050
                        print("No voice config found, using default sample rate")
                    # Create temporary file for original synthesis
                    temp_wav = output_path + "_temp.wav"
                    # Try direct synthesis to bytes first
                    print("Attempting direct synthesis...")
                    audio_bytes = b""
                    try:
                        # Check if Piper voice supports length_scale parameter
                        synthesis_kwargs = {}
                        if hasattr(self.piper_voice, 'synthesize') and self.speech_rate != 1.0:
                            # Try to pass length_scale parameter (inverse of speed)
                            try:
                                length_scale = 1.0 / self.speech_rate
                                synthesis_kwargs['length_scale'] = length_scale
                                print(f"Using Piper length_scale: {length_scale}")
                            except:
                                print("Piper voice doesn't support length_scale parameter")
                        # Use the synthesize method that returns audio data
                        try:
                            if hasattr(self.piper_voice, 'synthesize_stream_raw'):
                                for audio_chunk in self.piper_voice.synthesize_stream_raw(clean_text, **synthesis_kwargs):
                                    audio_bytes += audio_chunk
                            else:
                                # Fallback method - synthesize to temp file first
                                with wave.open(temp_wav, "wb") as wav_file:
                                    wav_file.setnchannels(1)
                                    wav_file.setsampwidth(2)
                                    wav_file.setframerate(sample_rate)
                                    self.piper_voice.synthesize(clean_text, wav_file, **synthesis_kwargs)
                                # Read the temp file
                                if os.path.exists(temp_wav):
                                    with wave.open(temp_wav, 'rb') as wf:
                                        audio_bytes = wf.readframes(wf.getnframes())
                        except TypeError:
                            # synthesis_kwargs not supported, synthesize without speed control
                            print("Voice synthesis doesn't support speed parameters")
                            if hasattr(self.piper_voice, 'synthesize_stream_raw'):
                                for audio_chunk in self.piper_voice.synthesize_stream_raw(clean_text):
                                    audio_bytes += audio_chunk
                            else:
                                with wave.open(temp_wav, "wb") as wav_file:
                                    wav_file.setnchannels(1)
                                    wav_file.setsampwidth(2)
                                    wav_file.setframerate(sample_rate)
                                    self.piper_voice.synthesize(clean_text, wav_file)
                                if os.path.exists(temp_wav):
                                    with wave.open(temp_wav, 'rb') as wf:
                                        audio_bytes = wf.readframes(wf.getnframes())
                        print(f"Generated {len(audio_bytes)} bytes of raw audio")
                        if len(audio_bytes) > 0:
                            # Write to temp WAV file first
                            with wave.open(temp_wav, "wb") as wav_file:
                                wav_file.setnchannels(1)
                                wav_file.setsampwidth(2) # 16-bit
                                wav_file.setframerate(sample_rate)
                                wav_file.writeframes(audio_bytes)
                            # Apply speed change if needed and not handled by Piper
                            if self.speech_rate != 1.0 and 'length_scale' not in synthesis_kwargs:
                                print(f"Applying speed change using sox: {self.speech_rate}x")
                                try:
                                    sox_cmd = ['sox', temp_wav, output_path, 'tempo', str(self.speech_rate)]
                                    sox_result = subprocess.run(sox_cmd, capture_output=True, text=True)
                                    if sox_result.returncode == 0:
                                        os.unlink(temp_wav)  # Remove temp file
                                    else:
                                        print(f"Sox failed: {sox_result.stderr}")
                                        # Fallback: use original speed
                                        os.rename(temp_wav, output_path)
                                except FileNotFoundError:
                                    print("Sox not found, using original speed")
                                    os.rename(temp_wav, output_path)
                            else:
                                # Speed was handled by Piper or is 1.0
                                os.rename(temp_wav, output_path)
                            file_size = os.path.getsize(output_path)
                            print(f"Wrote WAV file: {file_size} bytes")
                            return file_size > 44
                        else:
                            print("No audio data generated")
                            return False
                    except AttributeError:
                        print("Direct synthesis method not available, trying file-based method")
                        # Alternative: use the file-based synthesis
                        with wave.open(temp_wav, "wb") as wav_file:
                            wav_file.setnchannels(1)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(sample_rate)
                            # Try the original method
                            try:
                                self.piper_voice.synthesize(clean_text, wav_file)
                            except Exception as synth_error:
                                print(f"Synthesis failed: {synth_error}")
                                return False
                        # Apply speed change if needed
                        if self.speech_rate != 1.0:
                            print(f"Applying speed change using sox: {self.speech_rate}x")
                            try:
                                sox_cmd = ['sox', temp_wav, output_path, 'tempo', str(self.speech_rate)]
                                sox_result = subprocess.run(sox_cmd, capture_output=True, text=True)
                                if sox_result.returncode == 0:
                                    os.unlink(temp_wav)
                                else:
                                    print(f"Sox failed: {sox_result.stderr}")
                                    os.rename(temp_wav, output_path)
                            except FileNotFoundError:
                                print("Sox not found, using original speed")
                                os.rename(temp_wav, output_path)
                        else:
                            os.rename(temp_wav, output_path)
                    # Validate the final result
                    if os.path.exists(output_path):
                        file_size = os.path.getsize(output_path)
                        print(f"Final file size: {file_size} bytes")
                        if file_size > 44:
                            try:
                                with wave.open(output_path, 'rb') as wf:
                                    frames = wf.getnframes()
                                    duration = frames / wf.getframerate()
                                    print(f"Generated audio: {frames} frames, {duration:.2f}s duration")
                                    return frames > 0
                            except Exception as wav_error:
                                print(f"WAV validation failed: {wav_error}")
                                return False
                        else:
                            print("Generated file still too small")
                            return False
                    else:
                        print("Output file not created")
                        return False
                except Exception as synth_error:
                    print(f"Synthesis error: {synth_error}")
                    import traceback
                    traceback.print_exc()
                    return False
        except Exception as e:
            print(f"Error generating speech: {e}")
            import traceback
            traceback.print_exc()
            return False

    def create_voice_menu(self):
        """Create voice selection menu with language grouping"""
        menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()
        # Group voices by language
        voice_groups = {
            'English': [],
            'हिंदी (Hindi)': [],
            'Other Languages': []
        }
        for voice in self.available_voices:
            if voice.startswith('en_'):
                voice_groups['English'].append(voice)
            elif voice.startswith('hi_'):
                voice_groups['हिंदी (Hindi)'].append(voice)
            else:
                voice_groups['Other Languages'].append(voice)
        # Add voices to menu by groups
        for group_name, voices in voice_groups.items():
            if voices:
                group_section = Gio.Menu()
                for voice in voices:
                    action_name = f"app.select_voice_{voice.replace('-', '_').replace('.', '_')}"
                    group_section.append(voice, action_name)
                    # Create action
                    action = Gio.SimpleAction.new(f"select_voice_{voice.replace('-', '_').replace('.', '_')}", None)
                    action.connect("activate", lambda a, p, v=voice: self.on_voice_selected(v))
                    self.add_action(action)
                menu_model.append_section(group_name, group_section)
        menu.set_menu_model(menu_model)
        return menu

    def on_voice_selected(self, voice_name):
        """Handle voice selection with language detection"""
        self.current_voice_name = voice_name
        self.voice_button.set_label(f"Voice: {voice_name}")
        try:
            self.load_current_voice()
            if not self.use_cli and PIPER_AVAILABLE:
                self.piper_voice = PiperVoice.load(self.current_voice_path)
            lang_info = self.get_language_info()
            self.update_status(f"Voice changed to {voice_name} ({lang_info})")
        except Exception as e:
            self.update_status(f"Error loading voice {voice_name}: {e}")

    def create_reading_mode_menu(self):
        """Create reading mode selection menu"""
        menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()
        reading_modes = [
            ("📖 From Start", "from_start"),
            ("📍 From Cursor", "from_cursor"),
            ("📄 From Current Sentence", "from_current_sentence")
        ]
        for label, mode in reading_modes:
            action_name = f"app.reading_mode_{mode}"
            menu_model.append(label, action_name)
            # Create action
            action = Gio.SimpleAction.new(f"reading_mode_{mode}", None)
            action.connect("activate", lambda a, p, m=mode: self.on_reading_mode_selected(m))
            self.add_action(action)
        menu.set_menu_model(menu_model)
        return menu

    def on_reading_mode_selected(self, mode):
        """Handle reading mode selection"""
        self.reading_mode = mode
        mode_labels = {
            "from_start": "📖 From Start",
            "from_cursor": "📍 From Cursor",
            "from_current_sentence": "📄 From Current Sentence"
        }
        self.reading_mode_button.set_label(mode_labels.get(mode, mode))
        self.update_status(f"Reading mode: {mode_labels.get(mode, mode)}")

    def get_language_info(self):
        """Get current voice language info"""
        lang_code = self.current_voice_name.split('-')[0]
        lang_map = {
            'en_US': 'English (US)',
            'en_GB': 'English (UK)',
            'hi_IN': 'हिंदी (Hindi)',
            'es_ES': 'Español',
            'fr_FR': 'Français',
            'de_DE': 'Deutsch'
        }
        return lang_map.get(lang_code, lang_code)

    def on_speed_changed(self, scale):
        """Handle speed change"""
        self.speech_rate = scale.get_value()

    def test_voice(self, button):
        """Test the current voice with language-appropriate text"""
        # Choose test text based on language
        lang_code = self.current_voice_name.split('-')[0]
        if lang_code.startswith('hi'):
            test_text = "नमस्ते, यह हिंदी आवाज़ का परीक्षण है। पाइपर टीटीएस हिंदी भाषा का समर्थन करता है।"
        elif lang_code.startswith('es'):
            test_text = "Hola, esta es una prueba de la voz en español."
        elif lang_code.startswith('fr'):
            test_text = "Bonjour, ceci est un test de la voix française."
        elif lang_code.startswith('de'):
            test_text = "Hallo, das ist ein Test der deutschen Stimme."
        else:
            test_text = "Hello, this is a test of the current voice with enhanced features."

        def test_in_thread():
            try:
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    wav_file = f.name
                success = self.generate_speech_with_piper(test_text, wav_file)
                if success and os.path.exists(wav_file):
                    # Play the test audio
                    GLib.idle_add(lambda: self.play_test_audio(wav_file))
                else:
                    GLib.idle_add(lambda: self.update_status("Test generation failed"))
            except Exception as e:
                GLib.idle_add(lambda: self.update_status(f"Test error: {e}"))

        self.update_status("Testing voice...")
        threading.Thread(target=test_in_thread, daemon=True).start()

    def play_test_audio(self, wav_file):
        """Play test audio file with better error handling"""
        try:
            print(f"Attempting to play audio file: {wav_file}")
            print(f"File exists: {os.path.exists(wav_file)}")
            print(f"File size: {os.path.getsize(wav_file) if os.path.exists(wav_file) else 0} bytes")
            # Check if file is valid WAV
            try:
                with wave.open(wav_file, 'rb') as wf:
                    print(f"Audio info: {wf.getnchannels()} channels, {wf.getframerate()} Hz, {wf.getnframes()} frames")
            except Exception as wav_error:
                print(f"WAV file validation error: {wav_error}")
                self.update_status("Generated audio file is invalid")
                return
            # Try aplay with explicit error capture
            try:
                result = subprocess.run(['aplay', wav_file],
                                      capture_output=True,
                                      text=True,
                                      timeout=10)
                if result.returncode == 0:
                    self.update_status("Test audio played successfully")
                else:
                    print(f"aplay stderr: {result.stderr}")
                    self.update_status(f"Audio playback failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                self.update_status("Audio playback timed out")
            except FileNotFoundError:
                self.update_status("aplay not found - install alsa-utils")
            # Clean up after a delay
            def cleanup():
                time.sleep(3)
                try:
                    os.unlink(wav_file)
                except:
                    pass
            threading.Thread(target=cleanup, daemon=True).start()
        except Exception as e:
            print(f"Playback error: {e}")
            self.update_status(f"Playback error: {e}")

    def show_performance_settings(self, button):
        """Show performance optimization settings using Adw.PreferencesWindow"""
        dialog = Adw.PreferencesWindow(title="Performance Settings", transient_for=self.window)
        dialog.set_default_size(500, 500) # Adjust size as needed

        # Create a preferences page
        page = Adw.PreferencesPage()
        dialog.add(page)

        # General Group
        general_group = Adw.PreferencesGroup(title="General")
        page.add(general_group)

        # TTS Status Row
        tts_status_row = Adw.ActionRow(title="TTS Engine Status")
        tts_status_label = Gtk.Label(label="Piper CLI Available" if self.use_cli else "Piper Python Available")
        tts_status_label.add_css_class("dim-label")
        tts_status_row.add_suffix(tts_status_label)
        general_group.add(tts_status_row)

        # Voice Info Row
        voice_info_row = Adw.ActionRow(title="Current Voice")
        voice_info_label = Gtk.Label(label=self.current_voice_name)
        voice_info_label.add_css_class("dim-label")
        voice_info_row.add_suffix(voice_info_label)
        general_group.add(voice_info_row)

        # Highlighting Group
        highlight_group = Adw.PreferencesGroup(title="Highlighting")
        page.add(highlight_group)

        # Highlight refresh rate Row
        rate_row = Adw.ActionRow(title="Word highlight refresh rate (ms)")
        rate_spin = Gtk.SpinButton()
        rate_spin.set_range(25, 200)
        rate_spin.set_value(self.highlight_refresh_rate)
        rate_spin.set_increments(25, 25)
        # Connect the spin button to update the setting
        rate_spin.connect("value-changed", lambda s: setattr(self, 'highlight_refresh_rate', s.get_value_as_int()))
        rate_row.add_suffix(rate_spin)
        rate_row.set_activatable_widget(rate_spin)
        highlight_group.add(rate_row)

        # Language Support Group
        lang_group = Adw.PreferencesGroup(title="Language Support")
        page.add(lang_group)

        lang_info = Gtk.Label(label="Supported Languages:\n• English (en_US, en_GB)\n• हिंदी Hindi (hi_IN)\n• Español Spanish (es_ES)\n• Français French (fr_FR)\n• Deutsch German (de_DE)")
        lang_info.set_halign(Gtk.Align.START)
        lang_info.add_css_class("dim-label")
        lang_group.add(lang_info)

        # Speed Control Group
        speed_group = Adw.PreferencesGroup(title="Speed Control")
        page.add(speed_group)

        speed_info = Gtk.Label(label="• Uses Piper's length_scale parameter when available\n• Falls back to Sox for post-processing speed changes\n• Requires Sox for full speed control: sudo apt install sox")
        speed_info.set_halign(Gtk.Align.START)
        speed_info.add_css_class("dim-label")
        speed_group.add(speed_info)

        # Troubleshooting Group
        trouble_group = Adw.PreferencesGroup(title="Troubleshooting")
        page.add(trouble_group)

        trouble_text = Gtk.Label(label="If TTS is not working:\n• Install piper binary: sudo apt install piper\n• Or try: pip install piper-phonemize\n• For speed control: sudo apt install sox\n• For Hindi: Ensure proper Unicode fonts are installed")
        trouble_text.set_halign(Gtk.Align.START)
        trouble_text.add_css_class("dim-label")
        trouble_group.add(trouble_text)

        # Note: No "Apply" button needed as changes are applied instantly or don't require explicit application.
        # The window is closed via its standard close button.

        dialog.present()

    def update_status(self, message):
        """Thread-safe status update"""
        if self.status_label:
            GLib.idle_add(lambda: self.status_label.set_text(message))

    def update_progress(self, message):
        """Thread-safe progress update"""
        if self.progress_label:
            GLib.idle_add(lambda: self.progress_label.set_text(message))

    def set_buttons_state(self, speak_sensitive, pause_sensitive, stop_sensitive):
        """Thread-safe button state update"""
        def update():
            if self.speak_button:
                self.speak_button.set_sensitive(speak_sensitive)
            if self.pause_button:
                self.pause_button.set_sensitive(pause_sensitive)
            if self.stop_button:
                self.stop_button.set_sensitive(stop_sensitive)
        GLib.idle_add(update)

    def split_into_sentences(self, text):
        """Split text into sentences with better multi-language support"""
        try:
            # Check if text contains Hindi or other non-Latin scripts
            has_devanagari = any('\u0900' <= c <= '\u097F' for c in text)
            if has_devanagari:
                # For Hindi text, use simpler sentence splitting
                # Hindi sentences typically end with । (devanagari danda) or . ! ?
                sentences = re.split(r'[।.!?]+', text)
                sentences = [s.strip() for s in sentences if s.strip()]
            else:
                # Try to use nltk sentence tokenizer for other languages
                import nltk
                try:
                    nltk.data.find('tokenizers/punkt')
                except LookupError:
                    print("Downloading NLTK punkt tokenizer...")
                    nltk.download('punkt', quiet=True)
                sentences = nltk.sent_tokenize(text)
        except:
            # Fallback to simple splitting
            sentences = re.split(r'[.!?।]+', text)
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
        """Enhanced speak function with reading mode support"""
        if not self.textview: # Safety check
            return
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False).strip()
        if not text:
            self.update_status("No text to speak")
            return

        # Stop any current processing/playback
        self.on_stop(None)

        # Split text into sentences
        self.sentences = self.split_into_sentences(text)
        if not self.sentences:
            self.update_status("No sentences found")
            return
        print(f"Split into {len(self.sentences)} sentences")

        # Determine starting sentence based on reading mode
        start_sentence = self.determine_start_sentence()

        # Reset state
        self.current_sentence_idx = start_sentence
        self.resume_sentence_idx = start_sentence
        self.is_playing = True
        self.is_paused = False
        self.current_highlighted_word = -1

        # Start processing and playback
        self.set_buttons_state(False, True, True)
        mode_info = {
            "from_start": "from beginning",
            "from_cursor": f"from cursor position (sentence {start_sentence + 1})",
            "from_current_sentence": f"from current sentence ({start_sentence + 1})"
        }
        self.update_status(f"Processing sentences {mode_info[self.reading_mode]}...")
        self.update_progress(f"Sentence {self.current_sentence_idx + 1} of {len(self.sentences)}")

        # Start background processing
        self.start_processing_thread()
        # Start with selected sentence
        self.process_and_play_next()

    def on_pause(self, button):
        """Pause/Resume functionality"""
        if not self.is_paused:
            # Pause
            self.is_paused = True
            self.is_playing = False
            self.resume_sentence_idx = self.current_sentence_idx
            # Stop current audio immediately
            self.stop_audio_immediately()
            # Stop word highlighting
            if self.word_highlight_timer:
                GLib.source_remove(self.word_highlight_timer)
                self.word_highlight_timer = None
            self.pause_button.set_label("▶ Resume")
            self.set_buttons_state(True, True, True)
            self.update_status(f"Paused at sentence {self.current_sentence_idx + 1}")
        else:
            # Resume
            self.is_paused = False
            self.is_playing = True
            self.current_sentence_idx = self.resume_sentence_idx
            self.pause_button.set_label("⏸ Pause")
            self.set_buttons_state(False, True, True)
            self.update_status(f"Resuming from sentence {self.current_sentence_idx + 1}")
            # Resume playback
            self.process_and_play_next()

    def start_processing_thread(self):
        """Start event-driven background processing thread"""
        self.processing_active = True
        self.processing_event.clear()
        self.processing_thread = threading.Thread(target=self.background_processor, daemon=True)
        self.processing_thread.start()
        # Queue initial processing tasks
        self.queue_upcoming_sentences()

    def queue_upcoming_sentences(self):
        """Queue upcoming sentences for processing"""
        for i in range(self.current_sentence_idx + 1, min(self.current_sentence_idx + 3, len(self.sentences))):
            if not self.sentences[i].processed:
                try:
                    self.processing_queue.put(i, block=False)
                except queue.Full:
                    break
        self.processing_event.set()

    def background_processor(self):
        """Event-driven background processor"""
        while self.processing_active:
            try:
                self.processing_event.wait(timeout=1.0)
                if not self.processing_active:
                    break
                processed_any = False
                while not self.processing_queue.empty() and self.processing_active:
                    try:
                        sentence_idx = self.processing_queue.get_nowait()
                        if sentence_idx < len(self.sentences) and not self.sentences[sentence_idx].processed:
                            sentence = self.sentences[sentence_idx]
                            print(f"Background processing sentence {sentence_idx + 1}: {sentence.text[:50]}...")
                            # Generate audio with Piper
                            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                                wav_file = f.name
                            try:
                                success = self.generate_speech_with_piper(sentence.text, wav_file)
                                if success:
                                    alignment_data = self.process_sentence_alignment(wav_file, sentence.words)
                                    if alignment_data:
                                        sentence.audio_file = wav_file
                                        sentence.word_start_times = alignment_data['word_start_times']
                                        sentence.word_end_times = alignment_data['word_end_times']
                                        sentence.processed = True
                                        print(f"Background processing complete for sentence {sentence_idx + 1}")
                                        processed_any = True
                                    else:
                                        print(f"Background alignment failed for sentence {sentence_idx + 1}")
                                        if os.path.exists(wav_file):
                                            os.unlink(wav_file)
                                else:
                                    print(f"Background TTS generation failed for sentence {sentence_idx + 1}")
                                    if os.path.exists(wav_file):
                                        os.unlink(wav_file)
                            except Exception as bg_error:
                                print(f"Background processing error for sentence {sentence_idx + 1}: {bg_error}")
                                if os.path.exists(wav_file):
                                    os.unlink(wav_file)
                    except queue.Empty:
                        break
                if processed_any:
                    self.processing_event.clear()
                else:
                    self.processing_event.clear()
            except Exception as e:
                print(f"Background processor error: {e}")
                time.sleep(0.5)

    def highlight_current_sentence(self):
        """Highlight the current sentence being processed/played"""
        if not self.textview: # Safety check
            return
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        # Clear all highlighting
        buffer.remove_tag(self.sentence_tag, start, end)
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.next_word_tag, start, end)
        if self.current_sentence_idx < len(self.sentences):
            sentence = self.sentences[self.current_sentence_idx]
            s_iter = buffer.get_iter_at_offset(sentence.start_char)
            e_iter = buffer.get_iter_at_offset(sentence.end_char)
            buffer.apply_tag(self.sentence_tag, s_iter, e_iter)
            # Auto-scroll to current sentence ending
            self.textview.scroll_to_iter(e_iter, 0.0, False, 0.0, 0.3)

    def get_audio_duration(self, wav_file):
        """Get duration of WAV file"""
        try:
            with wave.open(wav_file, 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / float(rate)
        except Exception as e:
            print(f"Error getting duration: {e}")
            return len(self.sentences[self.current_sentence_idx].words) * 0.5 # Fallback estimate

    def update_word_highlight(self, sentence):
        """More efficient and precise word highlighting"""
        if not self.current_player or not self.is_playing or self.is_paused:
            return False
        # Check if player is still running
        if self.current_player.poll() is not None:
            return False
        current_time = time.time() - self.playback_start_time
        if not self.textview: # Safety check
            return False
        buffer = self.textview.get_buffer()
        # Find current and next word
        current_word_idx = -1
        next_word_idx = -1
        if sentence.word_start_times and sentence.word_end_times:
            for i, word in enumerate(sentence.words):
                if (i < len(sentence.word_start_times) and i < len(sentence.word_end_times)):
                    if sentence.word_start_times[i] <= current_time < sentence.word_end_times[i]:
                        current_word_idx = i
                        next_word_idx = i + 1 if i + 1 < len(sentence.words) else -1
                        break
        # Only update highlighting if word changed
        if current_word_idx != self.current_highlighted_word:
            self.current_highlighted_word = current_word_idx
            # Remove previous highlighting
            start, end = buffer.get_bounds()
            if self.highlight_all_words:
                '''Makes each word highlight in sequence for highlighted sentence'''
                buffer.remove_tag(self.highlight_tag, start, end)
            if not self.highlight_all_words:
                buffer.remove_tag(self.next_word_tag, start, end)
            # Highlight current word
            if current_word_idx >= 0 and current_word_idx < len(sentence.word_starts):
                word = sentence.words[current_word_idx]
                word_start = sentence.word_starts[current_word_idx]
                word_end = word_start + len(word)
                s_iter = buffer.get_iter_at_offset(word_start)
                e_iter = buffer.get_iter_at_offset(word_end)
                buffer.apply_tag(self.highlight_tag, s_iter, e_iter)
                # Auto-scroll to current word
                self.textview.scroll_to_iter(s_iter, 0.0, False, 0.0, 0.5)
            # Highlight next word preview
            if next_word_idx >= 0 and next_word_idx < len(sentence.word_starts):
                next_word = sentence.words[next_word_idx]
                next_word_start = sentence.word_starts[next_word_idx]
                next_word_end = next_word_start + len(next_word)
                ns_iter = buffer.get_iter_at_offset(next_word_start)
                ne_iter = buffer.get_iter_at_offset(next_word_end)
                buffer.apply_tag(self.next_word_tag, ns_iter, ne_iter)
                self.textview.scroll_to_iter(ne_iter, 0.0, False, 0.0, 0.5)
        return True

    def move_to_next_sentence(self):
        """Move to the next sentence"""
        # Clean up current playback
        self.stop_audio_immediately()
        if self.word_highlight_timer:
            GLib.source_remove(self.word_highlight_timer)
            self.word_highlight_timer = None
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
        self.resume_sentence_idx = self.current_sentence_idx
        self.current_highlighted_word = -1
        if self.current_sentence_idx < len(self.sentences) and not self.is_paused:
            self.update_progress(f"Sentence {self.current_sentence_idx + 1} of {len(self.sentences)}")
            self.process_and_play_next()
        else:
            self.on_playback_complete()
        return False # Don't repeat timer

    def on_playback_complete(self):
        """Called when all sentences are complete"""
        self.is_playing = False
        self.is_paused = False
        self.processing_active = False
        self.processing_event.set() # Wake up thread to exit
        # Clear highlighting
        if not self.textview: # Safety check
            return
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.sentence_tag, start, end)
        buffer.remove_tag(self.next_word_tag, start, end)
        self.pause_button.set_label("⏸ Pause")
        self.set_buttons_state(True, False, False)
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
        """Stop current playback and processing with immediate audio termination"""
        self.is_playing = False
        self.is_paused = False
        self.processing_active = False
        self.processing_event.set() # Wake up processing thread
        # Stop current playback immediately
        self.stop_audio_immediately()
        if self.word_highlight_timer:
            GLib.source_remove(self.word_highlight_timer)
            self.word_highlight_timer = None
        # Clear highlighting
        if not self.textview: # Safety check
            return
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.sentence_tag, start, end)
        buffer.remove_tag(self.next_word_tag, start, end)
        # Clean up audio files
        for sentence in self.sentences:
            if sentence.audio_file and os.path.exists(sentence.audio_file):
                try:
                    os.unlink(sentence.audio_file)
                except:
                    pass
                sentence.audio_file = None
        # Clear processing queue
        while not self.processing_queue.empty():
            try:
                self.processing_queue.get_nowait()
            except queue.Empty:
                break
        self.pause_button.set_label("⏸ Pause")
        self.set_buttons_state(True, False, False)
        self.update_status("Stopped")
        self.update_progress("")

    def process_sentence_alignment(self, wav_file, words):
        return self.create_fallback_alignment(wav_file, words)

    def create_fallback_alignment(self, wav_file, words):
        """Create simple uniform timing"""
        duration = self.get_audio_duration(wav_file)
        word_duration = duration / len(words) if words else 1.0
        word_start_times = [i * word_duration for i in range(len(words))]
        word_end_times = [(i + 1) * word_duration for i in range(len(words))]
        return {
            'word_start_times': word_start_times,
            'word_end_times': word_end_times
        }

    def process_and_play_next(self):
        """Process current sentence and start playback"""
        if not self.is_playing or self.is_paused or self.current_sentence_idx >= len(self.sentences):
            if not self.is_paused:
                self.on_playback_complete()
            return
        sentence = self.sentences[self.current_sentence_idx]
        # Highlight current sentence
        self.highlight_current_sentence()
        # Queue more sentences for background processing
        self.queue_upcoming_sentences()
        if sentence.processed:
            # Already processed in background
            self.start_sentence_playback(sentence)
        else:
            # Process current sentence
            threading.Thread(target=self.process_current_sentence, daemon=True).start()

    def process_current_sentence(self):
        """Process current sentence in thread"""
        if self.current_sentence_idx >= len(self.sentences):
            return
        sentence = self.sentences[self.current_sentence_idx]
        try:
            print(f"Processing sentence {self.current_sentence_idx + 1}: {sentence.text[:50]}...")
            # Generate audio with Piper
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                wav_file = f.name
            try:
                success = self.generate_speech_with_piper(sentence.text, wav_file)
                if not success:
                    raise Exception("TTS generation failed")
                print(f"Audio generated successfully for sentence {self.current_sentence_idx + 1}")
            except Exception as tts_error:
                print(f"TTS generation error: {tts_error}")
                if os.path.exists(wav_file):
                    os.unlink(wav_file)
                GLib.idle_add(self.move_to_next_sentence)
                return
            alignment_data = self.process_sentence_alignment(wav_file, sentence.words)
            if alignment_data:
                sentence.audio_file = wav_file
                sentence.word_start_times = alignment_data['word_start_times']
                sentence.word_end_times = alignment_data['word_end_times']
                sentence.processed = True
                # Start playback on main thread
                GLib.idle_add(self.start_sentence_playback, sentence)
            else:
                print(f"Processing failed for sentence {self.current_sentence_idx + 1}")
                if os.path.exists(wav_file):
                    os.unlink(wav_file)
                GLib.idle_add(self.move_to_next_sentence)
        except Exception as e:
            print(f"Processing error: {e}")
            import traceback
            traceback.print_exc()
            GLib.idle_add(self.move_to_next_sentence)

    def init_piper_tts(self):
        """Initialize Piper TTS with better voice loading"""
        try:
            if PIPER_AVAILABLE:
                print("Using Piper TTS Python library")
                self.use_cli = False
                # Load the voice with better error handling
                try:
                    self.load_current_voice()
                    if self.current_voice_path and os.path.exists(self.current_voice_path):
                        print(f"Loading voice from: {self.current_voice_path}")
                        self.piper_voice = PiperVoice.load(self.current_voice_path)
                        print(f"Voice loaded successfully: {self.piper_voice}")
                        # Test the voice with a simple phrase
                        test_output = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                        test_output.close()
                        print("Testing voice synthesis...")
                        success = self.test_voice_synthesis("Test", test_output.name)
                        if success:
                            print("Voice synthesis test successful")
                        else:
                            print("Voice synthesis test failed - falling back to CLI")
                            self.use_cli = True
                        # Cleanup test file
                        try:
                            os.unlink(test_output.name)
                        except:
                            pass
                    else:
                        print("Voice model file not found")
                        self.use_cli = True
                except Exception as voice_error:
                    print(f"Voice loading failed: {voice_error}")
                    self.use_cli = True
                # Discover available voices
                self.discover_cli_voices()
                return
            elif 'PIPER_CLI_AVAILABLE' in globals() and PIPER_CLI_AVAILABLE:
                print("Using Piper CLI interface")
                self.use_cli = True
                self.load_current_voice()
                self.discover_cli_voices()
                return
            else:
                raise Exception("No Piper TTS method available")
        except Exception as e:
            print(f"Error initializing Piper TTS: {e}")
            raise Exception("Piper TTS not available")

    def test_voice_synthesis(self, text, output_path):
        """Test voice synthesis to ensure it works"""
        try:
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                # Try synthesis
                self.piper_voice.synthesize(text, wav_file)
            # Check if file has content
            if os.path.exists(output_path):
                size = os.path.getsize(output_path)
                return size > 44
            return False
        except Exception as e:
            print(f"Voice synthesis test error: {e}")
            return False

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(1000, 650) # Wider default window to accommodate sidebar
        self.window.set_title("Piper TTS - Enhanced Reader")

        # --- Create Sidebar Content (Compact) ---
        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar_content.set_margin_top(10)
        sidebar_content.set_margin_bottom(10)
        sidebar_content.set_margin_start(10)
        sidebar_content.set_margin_end(10)
        # Set a smaller default width request for the sidebar
        sidebar_content.set_size_request(220, -1) # Reduced width

        # Voice Selection
        voice_label = Gtk.Label(label="Voice:")
        voice_label.set_halign(Gtk.Align.START)
        voice_label.add_css_class("heading")
        sidebar_content.append(voice_label)

        self.voice_button = Gtk.MenuButton()
        self.voice_button.set_label(f"Voice: {self.current_voice_name}")
        self.voice_button.set_tooltip_text("Select TTS Voice")
        self.voice_button.set_hexpand(True)
        # Create and attach the voice menu popover
        voice_menu = self.create_voice_menu()
        self.voice_button.set_popover(voice_menu)
        sidebar_content.append(self.voice_button)

        # Reading Mode
        mode_label = Gtk.Label(label="Reading Mode:")
        mode_label.set_halign(Gtk.Align.START)
        mode_label.add_css_class("heading")
        sidebar_content.append(mode_label)

        self.reading_mode_button = Gtk.MenuButton()
        self.reading_mode_button.set_label("📖 From Start")
        self.reading_mode_button.set_tooltip_text("Select Reading Mode")
        self.reading_mode_button.set_hexpand(True)
        # Create and attach the reading mode menu popover
        mode_menu = self.create_reading_mode_menu()
        self.reading_mode_button.set_popover(mode_menu)
        sidebar_content.append(self.reading_mode_button)

        # Playback Controls
        playback_label = Gtk.Label(label="Playback:")
        playback_label.set_halign(Gtk.Align.START)
        playback_label.add_css_class("heading")
        sidebar_content.append(playback_label)

        # Button container for playback controls (Vertical stack for compactness)
        playback_button_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        # playback_button_box.set_homogeneous(True) # Remove homogeneous for better spacing

        self.speak_button = Gtk.Button(label="🔊 Speak")
        self.speak_button.add_css_class("suggested-action")
        self.speak_button.connect("clicked", self.on_speak)
        self.speak_button.set_hexpand(True)
        playback_button_box.append(self.speak_button)

        self.pause_button = Gtk.Button(label="⏸ Pause")
        self.pause_button.set_sensitive(False)
        self.pause_button.connect("clicked", self.on_pause)
        self.pause_button.set_hexpand(True)
        playback_button_box.append(self.pause_button)

        self.stop_button = Gtk.Button(label="⏹ Stop")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self.on_stop)
        self.stop_button.set_hexpand(True)
        playback_button_box.append(self.stop_button)

        sidebar_content.append(playback_button_box)

        # Test Voice Button
        test_button = Gtk.Button(label="🎵 Test Voice")
        test_button.set_hexpand(True)
        test_button.connect("clicked", self.test_voice)
        sidebar_content.append(test_button)

        # Speed Control
        speed_label = Gtk.Label(label="Speed:")
        speed_label.set_halign(Gtk.Align.START)
        speed_label.add_css_class("heading")
        sidebar_content.append(speed_label)

        self.speed_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.0, 0.1)
        self.speed_scale.set_value(self.speech_rate)
        self.speed_scale.set_draw_value(True)
        self.speed_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.speed_scale.set_hexpand(True)
        self.speed_scale.connect("value-changed", self.on_speed_changed)
        # Make the scale slightly more compact if needed
        # self.speed_scale.set_size_request(150, -1)
        sidebar_content.append(self.speed_scale)

        # Settings Button
        settings_button = Gtk.Button(label="⚙️ Settings")
        settings_button.set_hexpand(True)
        settings_button.connect("clicked", self.show_performance_settings)
        sidebar_content.append(settings_button)

        # --- End Sidebar Content ---

        # --- Create Main Content Area ---
        main_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_content_box.set_margin_top(10)
        main_content_box.set_margin_bottom(10)
        main_content_box.set_margin_start(10)
        main_content_box.set_margin_end(10)

        # Status info box
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        info_box.add_css_class("card")
        info_box.set_margin_bottom(6)
        tts_status = "✅ Piper Available"
        lang_info = self.get_language_info()
        info_label = Gtk.Label(label=f"{tts_status} | {lang_info}")
        info_label.add_css_class("dim-label")
        info_box.append(info_label)
        main_content_box.append(info_box)

        # Text input area
        text_label = Gtk.Label(label="Enter text to speak (supports multiple languages including Hindi):")
        text_label.set_halign(Gtk.Align.START)
        main_content_box.append(text_label)

        self.textview = Gtk.TextView()
        self.textview.set_editable(True)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_top_margin(8)
        self.textview.set_bottom_margin(8)
        self.textview.set_left_margin(8)
        self.textview.set_right_margin(8)

        # Add sample text with Hindi example
        buffer = self.textview.get_buffer()
        sample_text = """Welcome to Piper TTS with enhanced reading features!
This application now supports:
• Reading from cursor position
• Pause and resume functionality
• Multi-language support including Hindi
नमस्ते! यह एक हिंदी का उदाहरण है। पाइपर टीटीएस अब हिंदी भाषा का भी समर्थन करता है।
Try placing your cursor anywhere in the text and selecting "From Cursor" mode to start reading from that position."""
        buffer.set_text(sample_text)

        # Set initial font size
        self.set_font_size(self.current_font_size)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.textview)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(200)
        main_content_box.append(scrolled)

        # Progress info
        self.progress_label = Gtk.Label(label="")
        self.progress_label.add_css_class("dim-label")
        main_content_box.append(self.progress_label)

        # Status label
        self.status_label = Gtk.Label(label=f"Ready - Using {self.current_voice_name}")
        self.status_label.add_css_class("dim-label")
        main_content_box.append(self.status_label)

        # --- End Main Content Area ---

        # --- Setup Adw.OverlaySplitView ---
        # Create the OverlaySplitView widget
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_content(main_content_box) # Main content on the left
        self.split_view.set_sidebar(sidebar_content)     # Sidebar content on the right
        self.split_view.set_sidebar_position(Gtk.PackType.END) # Position sidebar on the END (right side)
        self.split_view.set_enable_hide_gesture(False) # Disable swipe to hide
        self.split_view.set_enable_show_gesture(False) # Disable swipe to show
        self.split_view.set_collapsed(False) # Start uncollapsed
        self.split_view.set_min_sidebar_width(180) # Minimum width for sidebar when resized
        self.split_view.set_max_sidebar_width(400) # Maximum width for sidebar when resized
        # Set initial sidebar width (optional, it will be resizable)
        # self.split_view.set_sidebar_width_fraction(0.25) # 25% of window width

        # Header bar
        header = Adw.HeaderBar()
        # Add a button to the header to toggle sidebar visibility (optional)
        self.sidebar_button = Gtk.ToggleButton()
        self.sidebar_button.set_icon_name("sidebar-show-right-symbolic") # Standard icon
        self.sidebar_button.set_active(True) # Sidebar starts visible
        self.sidebar_button.connect("toggled", self.on_sidebar_toggled)
        header.pack_end(self.sidebar_button)

        # --- Assemble the final window content ---
        content = Adw.ToolbarView()
        content.add_top_bar(header)
        content.set_content(self.split_view) # Set the OverlaySplitView as the main content

        self.window.set_content(content)
        self.window.present()

        # --- Setup Highlight Tags (same as before) ---
        buffer = self.textview.get_buffer()
        # Word highlighting
        rgba_word = Gdk.RGBA()
        rgba_word.parse("rgba(255, 255, 0, 0.8)")
        self.use_bold_highlight = True
        self.use_underline_highlight = False
        self.use_italics_highlight = False
        self.use_color_highlight = False
        if self.use_bold_highlight:
            self.highlight_tag = buffer.create_tag("highlight",
                                                 background="yellow",
                                                 background_rgba=rgba_word,
                                                 weight=700)
            # Sentence highlighting
            rgba_sentence = Gdk.RGBA()
            rgba_sentence.parse("rgba(135, 206, 250, 0.3)")
            self.sentence_tag = buffer.create_tag("sentence",
                                                background_rgba=rgba_sentence)
            # Next word preview
            rgba_next = Gdk.RGBA()
            rgba_next.parse("rgba(144, 238, 144, 0.4)")
            self.next_word_tag = buffer.create_tag("next_word",
                                                 background_rgba=rgba_next,
                                                 weight=700)
        # ... (rest of tag setup remains the same) ...
        else:
            '''Do not highlight individual words'''
            self.highlight_tag = buffer.create_tag("highlight",
                                                 background="yellow",
                                                 background_rgba=rgba_word
                                                 )
            # Sentence highlighting
            rgba_sentence = Gdk.RGBA()
            rgba_sentence.parse("rgba(135, 206, 250, 0.3)")
            self.sentence_tag = buffer.create_tag("sentence",
                                                background_rgba=rgba_sentence)
            # Next word preview
            rgba_next = Gdk.RGBA()
            rgba_next.parse("rgba(144, 238, 144, 0.4)")
            self.next_word_tag = buffer.create_tag("next_word",
                                                 background_rgba=rgba_next
                                                )

        # Setup keyboard shortcuts
        self.setup_keyboard_shortcuts()

    def on_sidebar_toggled(self, button):
        """Handle toggling of the sidebar visibility"""
        if self.split_view:
            self.split_view.set_show_sidebar(button.get_active())

    # ... (rest of the methods like setup_keyboard_shortcuts, set_font_size, etc. remain the same) ...

    def setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for the application"""
        # Create key controller for the window
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.window.add_controller(key_controller)
        # Setup scroll zoom
        self.setup_scroll_zoom()

    def setup_scroll_zoom(self):
        """Set up zooming with Ctrl+scroll wheel"""
        # Create a scroll controller for handling wheel events
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        # Connect to the scroll event
        scroll_controller.connect("scroll", self.on_scroll)
        # Add the controller to the textview
        self.textview.add_controller(scroll_controller)
        # Store the controller reference
        self.scroll_controller = scroll_controller

    def on_scroll(self, controller, dx, dy):
        """Handle scroll events for zooming"""
        # Check if Ctrl key is pressed
        state = controller.get_current_event_state()
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        if ctrl_pressed:
            # Calculate new font size - use larger steps for faster zooming
            step = 1  # 1 point step size
            if dy < 0:
                # Scroll up - zoom in
                new_size = min(self.current_font_size + step, 72)  # Max 72pt
            else:
                # Scroll down - zoom out
                new_size = max(self.current_font_size - step, 6)   # Min 6pt
            # Only update if there's a change
            if new_size != self.current_font_size:
                self.current_font_size = new_size
                self.set_font_size(self.current_font_size)
                self.update_status(f"Font size: {int(new_size)}pt")
            # Prevent further handling of this scroll event
            return True
        # Not zooming, let the event propagate for normal scrolling
        return False

    def on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events"""
        ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        shift = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        if ctrl and not shift:
            if keyval == Gdk.KEY_equal:  # Ctrl+Plus (usually key = on US keyboards)
                self.zoom_in()
                return True
            elif keyval == Gdk.KEY_minus:  # Ctrl+Minus
                self.zoom_out()
                return True
            elif keyval == Gdk.KEY_0:  # Ctrl+0
                self.reset_zoom()
                return True
        return False  # Let other key events propagate

    def zoom_in(self):
        """Zoom in by increasing font size"""
        new_size = min(self.current_font_size + 1, 72)  # Max 72pt
        if new_size != self.current_font_size:
            self.current_font_size = new_size
            self.set_font_size(self.current_font_size)
            self.update_status(f"Font size: {int(new_size)}pt")

    def zoom_out(self):
        """Zoom out by decreasing font size"""
        new_size = max(self.current_font_size - 1, 6)  # Min 6pt
        if new_size != self.current_font_size:
            self.current_font_size = new_size
            self.set_font_size(self.current_font_size)
            self.update_status(f"Font size: {int(new_size)}pt")

    def reset_zoom(self):
        """Reset zoom to default font size"""
        self.current_font_size = self.default_font_size
        self.set_font_size(self.current_font_size)
        self.update_status(f"Font size reset to {int(self.default_font_size)}pt")

    def set_font_size(self, size):
        """Set the font size for the text view"""
        if not self.textview: # Safety check
            return
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        # Remove any existing font size tags
        tag_table = buffer.get_tag_table()
        font_tag = tag_table.lookup("font-size")
        if font_tag:
            buffer.remove_tag(font_tag, start, end)
        else:
            # Create a new font tag
            font_tag = buffer.create_tag("font-size")
        # Set the font size
        font_tag.set_property("size", size * Pango.SCALE)
        # Apply the tag to the entire buffer
        buffer.apply_tag(font_tag, start, end)

if __name__ == "__main__":
    app = PiperTTSApp(application_id='io.github.fastrizwaan.tts')
    app.run(None)
