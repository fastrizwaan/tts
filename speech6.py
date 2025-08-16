
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio
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
        
        # Word highlighting optimization
        self.current_highlighted_word = -1
        self.highlight_refresh_rate = 50 # ms
        
        print("Piper TTS App initialized successfully!")

    def init_piper_tts(self):
        """Initialize Piper TTS with fallback methods"""
        try:
            if PIPER_AVAILABLE:
                print("Using Piper TTS Python library")
                self.use_cli = False
                self.load_current_voice()
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
        """Discover available voices via CLI"""
        try:
            # Try to get voice list from Piper CLI
            result = subprocess.run(['piper', '--list-voices'], capture_output=True, text=True)
            if result.returncode == 0:
                # Parse voice list (format may vary)
                voices = []
                for line in result.stdout.split('\n'):
                    if line.strip() and not line.startswith('#'):
                        voices.append(line.strip())
                self.available_voices = voices[:10] # Limit to first 10
            else:
                # Default voices
                self.available_voices = [
                    "en_US-lessac-medium",
                    "en_US-ryan-medium",
                    "en_GB-alan-medium"
                ]
        except Exception as e:
            print(f"Error discovering voices: {e}")
            self.available_voices = ["en_US-lessac-medium"]

    def load_current_voice(self):
        """Load or download the current voice model"""
        model_name = self.current_voice_name
        model_dir = os.path.join(tempfile.gettempdir(), "piper_models")
        os.makedirs(model_dir, exist_ok=True)
        
        model_onnx = os.path.join(model_dir, f"{model_name}.onnx")
        model_json = os.path.join(model_dir, f"{model_name}.onnx.json")
        
        if not os.path.exists(model_onnx) or not os.path.exists(model_json):
            print(f"Downloading Piper model {model_name}...")
            voice_parts = model_name.split('-')
            lang = voice_parts[0] # e.g., en_US
            speaker = voice_parts[1] # e.g., lessac
            quality = voice_parts[2] # e.g., medium
            
            base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/"
            
            try:
                urllib.request.urlretrieve(base_url + f"{model_name}.onnx", model_onnx)
                urllib.request.urlretrieve(base_url + f"{model_name}.onnx.json", model_json)
                print("Model downloaded successfully")
            except Exception as e:
                print(f"Error downloading model: {e}")
                raise
        
        self.current_voice_path = model_onnx

    def generate_speech_with_piper(self, text, output_path):
        """Generate speech using Piper TTS with fallback methods"""
        try:
            print(f"Generating speech for: {text[:50]}...")
            
            if self.use_cli:
                # Use CLI method
                cmd = [
                    'piper',
                    '--model', self.current_voice_path,
                    '--output_file', output_path
                ]
                
                try:
                    process = subprocess.run(cmd, input=text, text=True,
                                            capture_output=True, timeout=30)
                    
                    if process.returncode == 0 and os.path.exists(output_path):
                        print(f"Audio generated successfully via CLI: {output_path}")
                        return True
                    else:
                        print(f"CLI generation failed: {process.stderr}")
                        return False
                        
                except subprocess.TimeoutExpired:
                    print("Piper CLI timeout")
                    return False
                except Exception as e:
                    print(f"CLI error: {e}")
                    return False
            
            else:
                # Use Python API
                model_json = self.current_voice_path + '.json'
                if os.path.exists(model_json):
                    with open(model_json) as f:
                        config = json.load(f)
                    sample_rate = config.get('audio', {}).get('sample_rate', 22050)
                else:
                    sample_rate = 22050

                with wave.open(output_path, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(sample_rate)
                    self.piper_voice.synthesize(text, wav_file)
                return True
                
        except Exception as e:
            print(f"Error generating speech: {e}")
            return False

    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_default_size(800, 600)
        self.window.set_title("Piper TTS - Word Highlighting")
        
        # Create header bar
        header = Adw.HeaderBar()
        
        # Voice selection button in header
        self.voice_button = Gtk.MenuButton()
        self.voice_button.set_label(f"Voice: {self.current_voice_name}")
        self.voice_button.set_tooltip_text("Select TTS Voice")
        header.pack_start(self.voice_button)
        
        # Create voice menu
        self.create_voice_menu()
        
        # Settings button in header
        settings_button = Gtk.Button()
        settings_button.set_icon_name("preferences-system-symbolic")
        settings_button.set_tooltip_text("Performance Settings")
        settings_button.connect("clicked", self.show_performance_settings)
        header.pack_end(settings_button)
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        
        # Status info box
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        info_box.add_css_class("card")
        info_box.set_margin_bottom(6)
        
        tts_status = "✅ Piper Available"
        info_label = Gtk.Label(label=f"{tts_status}")
        info_label.add_css_class("dim-label")
        info_box.append(info_label)
        main_box.append(info_box)
        
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
        
        # Add some sample text
        buffer = self.textview.get_buffer()
        sample_text = "Welcome to Piper TTS with word highlighting! This application will speak each sentence and highlight words as they are spoken. Try typing your own text here."
        buffer.set_text(sample_text)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.textview)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(200)
        main_box.append(scrolled)
        
        # Control panel
        control_panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        control_panel.set_halign(Gtk.Align.CENTER)
        
        # Speed control
        speed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        speed_label = Gtk.Label(label="Speed")
        speed_label.add_css_class("dim-label")
        self.speed_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.0, 0.1)
        self.speed_scale.set_value(self.speech_rate)
        self.speed_scale.set_draw_value(True)
        self.speed_scale.set_value_pos(Gtk.PositionType.BOTTOM)
        self.speed_scale.connect("value-changed", self.on_speed_changed)
        speed_box.append(speed_label)
        speed_box.append(self.speed_scale)
        
        # Button container
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.speak_button = Gtk.Button(label="🔊 Speak All")
        self.speak_button.add_css_class("suggested-action")
        self.speak_button.connect("clicked", self.on_speak)
        button_box.append(self.speak_button)
        
        self.stop_button = Gtk.Button(label="⏹ Stop")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self.on_stop)
        button_box.append(self.stop_button)
        
        # Test button
        test_button = Gtk.Button(label="🎵 Test Voice")
        test_button.connect("clicked", self.test_voice)
        button_box.append(test_button)
        
        control_panel.append(speed_box)
        control_panel.append(button_box)
        main_box.append(control_panel)
        
        # Progress info
        self.progress_label = Gtk.Label(label="")
        self.progress_label.add_css_class("dim-label")
        main_box.append(self.progress_label)
        
        # Status label
        self.status_label = Gtk.Label(label=f"Ready - Using {self.current_voice_name}")
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)
        
        content = Adw.ToolbarView()
        content.add_top_bar(header)
        content.set_content(main_box)
        self.window.set_content(content)
        self.window.present()
        
        # Set up highlight tags
        buffer = self.textview.get_buffer()
        
        # Word highlighting
        rgba_word = Gdk.RGBA()
        rgba_word.parse("rgba(255, 255, 0, 0.8)")
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
                                             background_rgba=rgba_next)

    def create_voice_menu(self):
        """Create voice selection menu"""
        menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()
        
        # Add available voices (or defaults if none found)
        voices = self.available_voices if self.available_voices else ["en_US-lessac-medium", "en_US-ryan-medium"]
        
        for voice in voices:
            action_name = f"app.select_voice_{voice.replace('-', '_').replace('.', '_')}"
            menu_model.append(voice, action_name)
            
            # Create action
            action = Gio.SimpleAction.new(f"select_voice_{voice.replace('-', '_').replace('.', '_')}", None)
            action.connect("activate", lambda a, p, v=voice: self.on_voice_selected(v))
            self.add_action(action)
        
        menu.set_menu_model(menu_model)
        self.voice_button.set_popover(menu)

    def on_voice_selected(self, voice_name):
        """Handle voice selection"""
        self.current_voice_name = voice_name
        self.voice_button.set_label(f"Voice: {voice_name}")
        self.load_current_voice()
        if not self.use_cli:
            self.piper_voice = PiperVoice.load(self.current_voice_path)
        self.update_status(f"Voice changed to {voice_name}")

    def on_speed_changed(self, scale):
        """Handle speed change"""
        self.speech_rate = scale.get_value()

    def test_voice(self, button):
        """Test the current voice with a short phrase"""
        test_text = "Hello, this is a test of the current voice."
        
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
        """Play test audio file"""
        try:
            subprocess.Popen(['aplay', wav_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.update_status("Playing test audio...")
            
            # Clean up after a delay
            def cleanup():
                time.sleep(3)
                try:
                    os.unlink(wav_file)
                except:
                    pass
            threading.Thread(target=cleanup, daemon=True).start()
            
        except Exception as e:
            self.update_status(f"Playback error: {e}")

    def show_performance_settings(self, button):
        """Show performance optimization settings"""
        dialog = Gtk.Dialog(title="Performance Settings", parent=self.window, modal=True)
        dialog.set_default_size(450, 400)
        
        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        
        # TTS Status
        tts_status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tts_label = Gtk.Label(label="TTS Engine Status:")
        tts_label.add_css_class("heading")
        tts_label.set_halign(Gtk.Align.START)
        
        status_text = "Piper CLI Available" if self.use_cli else "Piper Python Available"
        status_detail = Gtk.Label(label=status_text)
        status_detail.add_css_class("dim-label")
        
        tts_status_box.append(tts_label)
        tts_status_box.append(status_detail)
        content.append(tts_status_box)
        
        # Highlight refresh rate
        rate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        rate_label = Gtk.Label(label="Word highlight refresh rate (ms):")
        rate_label.set_hexpand(True)
        rate_label.set_halign(Gtk.Align.START)
        
        rate_spin = Gtk.SpinButton()
        rate_spin.set_range(25, 200)
        rate_spin.set_value(self.highlight_refresh_rate)
        rate_spin.set_increments(25, 25)
        
        rate_box.append(rate_label)
        rate_box.append(rate_spin)
        content.append(rate_box)
        
        # Voice info
        voice_info = Gtk.Label(label=f"Current Voice: {self.current_voice_name}")
        voice_info.add_css_class("heading")
        content.append(voice_info)
        
        # Troubleshooting
        trouble_label = Gtk.Label(label="Troubleshooting:")
        trouble_label.set_halign(Gtk.Align.START)
        trouble_label.add_css_class("heading")
        content.append(trouble_label)
        
        trouble_text = Gtk.Label(label="If TTS is not working:\n• Install piper binary: sudo apt install piper\n• Or try: pip install piper-phonemize")
        trouble_text.set_halign(Gtk.Align.START)
        trouble_text.add_css_class("dim-label")
        content.append(trouble_text)
        
        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        
        apply_button = Gtk.Button(label="Apply")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", lambda b: self.apply_performance_settings(
            rate_spin.get_value_as_int(),
            dialog
        ))
        
        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", lambda b: dialog.close())
        
        button_box.append(cancel_button)
        button_box.append(apply_button)
        content.append(button_box)
        
        dialog.show()

    def apply_performance_settings(self, refresh_rate, dialog):
        """Apply performance settings"""
        self.highlight_refresh_rate = refresh_rate
        dialog.close()
        self.update_status(f"Settings applied: refresh {refresh_rate}ms")

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
        
        # Reset state
        self.current_sentence_idx = 0
        self.is_playing = True
        self.current_highlighted_word = -1
        
        # Start processing and playback
        self.set_buttons_state(False, True)
        self.update_status("Processing sentences...")
        self.update_progress(f"Sentence 1 of {len(self.sentences)}")
        
        # Start background processing
        self.start_processing_thread()
        
        # Start with first sentence
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
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        
        # Clear all highlighting
        buffer.remove_tag(self.sentence_tag, start, end)
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.next_word_tag, start, end)
        
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
            
        except Exception as e:
            print(f"Playback error: {e}")
            self.move_to_next_sentence()

    def get_audio_duration(self, wav_file):
        """Get duration of WAV file"""
        try:
            with wave.open(wav_file, 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / float(rate)
        except Exception as e:
            print(f"Error getting duration: {e}")
            return len(self.sentences[self.current_sentence_idx].words) * 0.5  # Fallback estimate

    def update_word_highlight(self, sentence):
        """More efficient and precise word highlighting"""
        if not self.current_player or not self.is_playing:
            return False
            
        # Check if player is still running
        if self.current_player.poll() is not None:
            return False
        
        current_time = time.time() - self.playback_start_time
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
            buffer.remove_tag(self.highlight_tag, start, end)
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
        self.current_highlighted_word = -1
        
        if self.current_sentence_idx < len(self.sentences):
            self.update_progress(f"Sentence {self.current_sentence_idx + 1} of {len(self.sentences)}")
            self.process_and_play_next()
        else:
            self.on_playback_complete()
        
        return False # Don't repeat timer

    def on_playback_complete(self):
        """Called when all sentences are complete"""
        self.is_playing = False
        self.processing_active = False
        self.processing_event.set() # Wake up thread to exit
        
        # Clear highlighting
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.highlight_tag, start, end)
        buffer.remove_tag(self.sentence_tag, start, end)
        buffer.remove_tag(self.next_word_tag, start, end)
        
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
        self.processing_event.set() # Wake up processing thread
        
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
        
        if self.word_highlight_timer:
            GLib.source_remove(self.word_highlight_timer)
            self.word_highlight_timer = None
        
        # Clear highlighting
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
        
        self.set_buttons_state(True, False)
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
        if not self.is_playing or self.current_sentence_idx >= len(self.sentences):
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

if __name__ == "__main__":
    app = PiperTTSApp(application_id='io.github.fastrizwaan.tts')
    app.run(None)

