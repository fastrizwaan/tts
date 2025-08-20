import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
import soundfile as sf
import numpy as np
import threading
import queue
import time
from kokoro_onnx import Kokoro
import sys
import subprocess
import os
import tempfile
import re
import signal

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.TTSHighlighter")
        self.model_path = "/home/rizvan/.local/share/app.kokoro.demo/models/kokoro-v0_19.onnx"
        self.voices_path = "/home/rizvan/.local/share/app.kokoro.demo/models/voices-v1.0.bin"
        self.kokoro = None
        self.setup_model()
        
        # Playback state
        self.is_playing = False
        self.is_paused = False
        self.audio_thread = None
        self.text_buffer = None
        self.sentences = []
        self.sentence_positions = []
        self.current_process = None
        self.temp_files = []
        self.audio_queue = queue.Queue()
        self.generation_thread = None
        self.playback_thread = None
        self.should_stop = False

    def setup_model(self):
        try:
            self.kokoro = Kokoro(self.model_path, self.voices_path)
        except Exception as e:
            print(f"Error loading model: {e}")
            sys.exit(1)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = self.get_application()
        self.set_default_size(800, 600)

        # Main layout using ToolbarView
        self.toolbar_view = Adw.ToolbarView()
        headerbar = Adw.HeaderBar()
        headerbar.set_title_widget(Gtk.Label(label="Kokoro TTS Highlighter"))
        
        # Create main content area
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        
        # Text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_buffer = self.text_view.get_buffer()
        self.text_buffer.set_text("Enter your text here...")
        
        # Create highlight tag
        self.highlight_tag = self.text_buffer.create_tag("highlight", background="yellow")
        
        scrolled.set_child(self.text_view)
        main_box.append(scrolled)
        
        # Controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        # Language selection with flags
        lang_label = Gtk.Label(label="Language:")
        controls_box.append(lang_label)
        
        self.lang_combo = Gtk.ComboBoxText()
        # Language options with flags (using specific dialect codes)
        languages = [
            ("en-us", "🇺🇸 American English"),
            ("en-gb", "🇬🇧 British English"),
            ("ja", "🇯🇵 Japanese"),
            ("zh", "🇨🇳 Mandarin Chinese"),
            ("es", "🇪🇸 Spanish"),
            ("fr-fr", "🇫🇷 French"),
            ("hi", "🇮🇳 Hindi"),
            ("it", "🇮🇹 Italian"),
            ("pt-br", "🇧🇷 Brazilian Portuguese")
        ]
        
        for lang_code, lang_name in languages:
            self.lang_combo.append(lang_code, lang_name)
        
        self.lang_combo.set_active(0)  # Default to American English
        controls_box.append(self.lang_combo)
        
        # Voice selection (will be updated based on language)
        voice_label = Gtk.Label(label="Voice:")
        controls_box.append(voice_label)
        
        self.voice_combo = Gtk.ComboBoxText()
        self.update_voices_for_language("en-us")  # Initialize with American English voices
        self.lang_combo.connect("changed", self.on_language_changed)
        controls_box.append(self.voice_combo)
        
        # Speed control
        speed_label = Gtk.Label(label="Speed:")
        controls_box.append(speed_label)
        
        self.speed_adjustment = Gtk.Adjustment(value=1.0, lower=0.5, upper=2.0, step_increment=0.1)
        speed_spin = Gtk.SpinButton()
        speed_spin.set_adjustment(self.speed_adjustment)
        speed_spin.set_digits(1)
        controls_box.append(speed_spin)
        
        # Play button
        self.play_button = Gtk.Button()
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.play_button.connect("clicked", self.on_play_clicked)
        controls_box.append(self.play_button)
        
        # Pause button
        self.pause_button = Gtk.Button()
        self.pause_button.set_icon_name("media-playback-pause-symbolic")
        self.pause_button.connect("clicked", self.on_pause_clicked)
        self.pause_button.set_sensitive(False)
        controls_box.append(self.pause_button)
        
        # Stop button
        self.stop_button = Gtk.Button()
        self.stop_button.set_icon_name("media-playback-stop-symbolic")
        self.stop_button.connect("clicked", self.on_stop_clicked)
        self.stop_button.set_sensitive(False)
        controls_box.append(self.stop_button)
        
        main_box.append(controls_box)
        
        # Set up toolbar view
        self.toolbar_view.add_top_bar(headerbar)
        self.toolbar_view.set_content(main_box)
        self.set_content(self.toolbar_view)

    def on_language_changed(self, combo):
        """Update voice list when language changes"""
        lang_code = combo.get_active_id()
        if lang_code:
            self.update_voices_for_language(lang_code)

    def update_voices_for_language(self, lang_code):
        """Update voice dropdown based on selected language"""
        self.voice_combo.remove_all()
        
        # Map language codes to voice prefixes
        lang_to_prefix = {
            "en-us": "a",
            "en-gb": "b", 
            "ja": "j",
            "zh": "z",
            "es": "e",
            "fr-fr": "f",
            "hi": "h",
            "it": "i",
            "pt-br": "p"
        }
        
        prefix = lang_to_prefix.get(lang_code, "a")
        
        # Define voices for each language prefix
        voice_groups = {
            "a": [  # American English
                ("af_heart", "af_heart 🚺❤️"),
                ("af_alloy", "af_alloy 🚺"),
                ("af_aoede", "af_aoede 🚺"),
                ("af_bella", "af_bella 🚺🔥"),
                ("af_jessica", "af_jessica 🚺"),
                ("af_kore", "af_kore 🚺"),
                ("af_nicole", "af_nicole 🚺🎧"),
                ("af_nova", "af_nova 🚺"),
                ("af_river", "af_river 🚺"),
                ("af_sarah", "af_sarah 🚺"),
                ("af_sky", "af_sky 🚺"),
                ("am_adam", "am_adam 🚹"),
                ("am_echo", "am_echo 🚹"),
                ("am_eric", "am_eric 🚹"),
                ("am_fenrir", "am_fenrir 🚹"),
                ("am_liam", "am_liam 🚹"),
                ("am_michael", "am_michael 🚹"),
                ("am_onyx", "am_onyx 🚹"),
                ("am_puck", "am_puck 🚹"),
                ("am_santa", "am_santa 🚹")
            ],
            "b": [  # British English
                ("bf_alice", "bf_alice 🚺"),
                ("bf_emma", "bf_emma 🚺"),
                ("bf_isabella", "bf_isabella 🚺"),
                ("bf_lily", "bf_lily 🚺"),
                ("bm_daniel", "bm_daniel 🚹"),
                ("bm_fable", "bm_fable 🚹"),
                ("bm_george", "bm_george 🚹"),
                ("bm_lewis", "bm_lewis 🚹")
            ],
            "j": [  # Japanese
                ("jf_alpha", "jf_alpha 🚺"),
                ("jf_gongitsune", "jf_gongitsune 🚺"),
                ("jf_nezumi", "jf_nezumi 🚺"),
                ("jf_tebukuro", "jf_tebukuro 🚺"),
                ("jm_kumo", "jm_kumo 🚹")
            ],
            "z": [  # Mandarin Chinese
                ("zf_xiaobei", "zf_xiaobei 🚺"),
                ("zf_xiaoni", "zf_xiaoni 🚺"),
                ("zf_xiaoxiao", "zf_xiaoxiao 🚺"),
                ("zf_xiaoyi", "zf_xiaoyi 🚺"),
                ("zm_yunjian", "zm_yunjian 🚹"),
                ("zm_yunxi", "zm_yunxi 🚹"),
                ("zm_yunxia", "zm_yunxia 🚹"),
                ("zm_yunyang", "zm_yunyang 🚹")
            ],
            "e": [  # Spanish
                ("ef_dora", "ef_dora 🚺"),
                ("em_alex", "em_alex 🚹"),
                ("em_santa", "em_santa 🚹")
            ],
            "f": [  # French
                ("ff_siwis", "ff_siwis 🚺")
            ],
            "h": [  # Hindi
                ("hf_alpha", "hf_alpha 🚺"),
                ("hf_beta", "hf_beta 🚺"),
                ("hm_omega", "hm_omega 🚹"),
                ("hm_psi", "hm_psi 🚹")
            ],
            "i": [  # Italian
                ("if_sara", "if_sara 🚺"),
                ("im_nicola", "im_nicola 🚹")
            ],
            "p": [  # Brazilian Portuguese
                ("pf_dora", "pf_dora 🚺"),
                ("pm_alex", "pm_alex 🚹"),
                ("pm_santa", "pm_santa 🚹")
            ]
        }
        
        voices = voice_groups.get(prefix, voice_groups["a"])
        for voice_id, voice_name in voices:
            self.voice_combo.append(voice_id, voice_name)
        
        if voices:
            self.voice_combo.set_active(0)

    def on_play_clicked(self, button):
        if self.app.is_paused:
            self.resume_playback()
            return
            
        start_iter = self.text_buffer.get_start_iter()
        end_iter = self.text_buffer.get_end_iter()
        text = self.text_buffer.get_text(start_iter, end_iter, False)
        
        if not text.strip():
            return
            
        self.play_button.set_sensitive(False)
        self.pause_button.set_sensitive(True)
        self.stop_button.set_sensitive(True)
        
        # Reset state
        self.app.is_playing = True
        self.app.is_paused = False
        self.app.should_stop = False
        self.app.text_buffer = self.text_buffer
        self.app.temp_files = []
        self.app.audio_queue = queue.Queue()
        
        # Clear previous highlights
        self.clear_highlight()
        
        # Split text into sentences
        self.app.sentences = self.split_into_sentences(text)
        self.app.sentence_positions = self.get_sentence_positions(text, self.app.sentences)
        
        if not self.app.sentences:
            return
            
        # Start audio generation and playback threads
        voice = self.voice_combo.get_active_id()
        lang = self.lang_combo.get_active_id()
        speed = self.speed_adjustment.get_value()
        
        self.app.generation_thread = threading.Thread(
            target=self.generate_audio_prefetch, 
            args=(voice, speed, lang), 
            daemon=True
        )
        self.app.playback_thread = threading.Thread(
            target=self.play_audio_sequential, 
            daemon=True
        )
        
        self.app.generation_thread.start()
        self.app.playback_thread.start()

    def on_pause_clicked(self, button):
        self.app.is_playing = False
        self.app.is_paused = True
        
        # Send SIGSTOP to pause the current audio process (like Ctrl+Z)
        if self.app.current_process and self.app.current_process.poll() is None:
            try:
                # On Unix-like systems, we can send SIGSTOP to pause the process
                self.app.current_process.send_signal(signal.SIGSTOP)
            except Exception as e:
                print(f"Could not pause process: {e}")
                # Fallback: terminate the process
                try:
                    self.app.current_process.terminate()
                except:
                    pass
                self.app.current_process = None
        
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)

    def on_stop_clicked(self, button):
        # Set stop flag immediately
        self.app.should_stop = True
        self.app.is_playing = False
        self.app.is_paused = False
        
        # Send SIGKILL to stop the current audio process immediately (like Ctrl+C)
        if self.app.current_process:
            try:
                # Try graceful termination first
                self.app.current_process.terminate()
                try:
                    self.app.current_process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't respond
                    self.app.current_process.kill()
            except Exception as e:
                print(f"Error stopping process: {e}")
            self.app.current_process = None
        
        # Clear the audio queue to prevent further playback
        try:
            while True:
                self.app.audio_queue.get_nowait()
        except queue.Empty:
            pass
        
        # Signal end to queue to unblock playback thread
        try:
            self.app.audio_queue.put_nowait(None)
        except:
            pass
        
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        self.clear_highlight()
        self.cleanup_temp_files()

    def resume_playback(self):
        self.app.is_playing = True
        self.app.is_paused = False
        self.app.should_stop = False  # Reset stop flag on resume
        
        # Send SIGCONT to resume the paused audio process (like fg command)
        if self.app.current_process and self.app.current_process.poll() is None:
            try:
                # On Unix-like systems, we can send SIGCONT to resume the paused process
                self.app.current_process.send_signal(signal.SIGCONT)
            except Exception as e:
                print(f"Could not resume process: {e}")
                # If we can't resume, the process might have been terminated
                self.app.current_process = None
        
        self.play_button.set_sensitive(False)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(True)

    def split_into_sentences(self, text):
        # Split text into sentences using regex
        sentences = re.split(r'[.!?]+', text)
        # Filter out empty sentences and strip whitespace
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences

    def get_sentence_positions(self, text, sentences):
        positions = []
        pos = 0
        
        for sentence in sentences:
            # Find the start position
            start = text.find(sentence, pos)
            if start != -1:
                end = start + len(sentence)
                positions.append((start, end))
                pos = end
        return positions

    def generate_audio_prefetch(self, voice, speed, lang):
        """Generate audio with prefetching"""
        try:
            for i, sentence in enumerate(self.app.sentences):
                if self.app.should_stop:
                    break
                    
                # Generate audio for current sentence
                samples, sample_rate = self.app.kokoro.create(
                    sentence,
                    voice=voice,
                    speed=speed,
                    lang=lang
                )
                
                # Create temporary file for this sentence
                temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                sf.write(temp_file.name, samples, sample_rate)
                self.app.temp_files.append(temp_file.name)
                
                # Put in queue with metadata
                sentence_info = {
                    'index': i,
                    'file': temp_file.name,
                    'sentence': sentence,
                    'sample_rate': sample_rate,
                    'start_pos': self.app.sentence_positions[i][0] if i < len(self.app.sentence_positions) else 0,
                    'end_pos': self.app.sentence_positions[i][1] if i < len(self.app.sentence_positions) else 0
                }
                
                # Only put in queue if not stopped
                if not self.app.should_stop:
                    try:
                        self.app.audio_queue.put(sentence_info, timeout=0.1)
                    except:
                        break
                
                # Small delay to prevent overwhelming the queue
                time.sleep(0.01)
                
        except Exception as e:
            print(f"Error in audio generation: {e}")
        finally:
            # Only signal end if not stopped
            if not self.app.should_stop:
                try:
                    self.app.audio_queue.put(None, timeout=0.1)
                except:
                    pass

    def play_audio_sequential(self):
        """Play audio sequentially from queue"""
        try:
            while not self.app.should_stop:
                try:
                    # Get next sentence info from queue (with short timeout)
                    sentence_info = self.app.audio_queue.get(timeout=0.5)
                    
                    if sentence_info is None:  # End signal
                        break
                        
                    if self.app.should_stop:
                        break
                        
                    # Play the sentence with word highlighting
                    self.play_sentence_sync(sentence_info)
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"Error in playback: {e}")
                    break
                    
        except Exception as e:
            print(f"Error in sequential playback: {e}")
        finally:
            GLib.idle_add(self.on_playback_finished)
            self.cleanup_temp_files()

    def play_sentence_sync(self, sentence_info):
        """Play a sentence and highlight words with precise synchronization"""
        if self.app.should_stop:
            return
            
        sentence_index = sentence_info['index']
        audio_file = sentence_info['file']
        sentence_text = sentence_info['sentence']
        sample_rate = sentence_info['sample_rate']
        sentence_start = sentence_info['start_pos']
        
        try:
            # Start playing audio
            process = None
            try:
                process = subprocess.Popen(['paplay', audio_file])
            except FileNotFoundError:
                try:
                    process = subprocess.Popen(['aplay', audio_file])
                except FileNotFoundError:
                    process = subprocess.Popen(['play', audio_file])
            
            self.app.current_process = process
            
            # Get actual audio duration
            audio_data, _ = sf.read(audio_file)
            actual_duration = len(audio_data) / sample_rate
            
            # Split sentence into words
            words = sentence_text.split()
            if not words:
                return
                
            # Calculate word timing based on actual duration
            word_timings = self.calculate_word_timings(words, actual_duration, sentence_start)
            
            # Monitor playback and highlight words
            start_time = time.time()
            word_index = 0
            
            while word_index < len(word_timings) and not self.app.should_stop:
                # Check if playback is paused
                while self.app.is_paused and not self.app.should_stop:
                    time.sleep(0.01)
                
                if self.app.should_stop:
                    break
                    
                current_time = time.time() - start_time
                
                # Check if it's time to highlight the next word
                if word_index < len(word_timings):
                    word_start_time, word_end_time, word_start_pos, word_end_pos = word_timings[word_index]
                    
                    if current_time >= word_start_time:
                        # Highlight this word
                        GLib.idle_add(self.highlight_word, word_start_pos, word_end_pos)
                        word_index += 1
                
                time.sleep(0.005)  # Small sleep to prevent busy waiting
            
            # Handle stop during playback
            if self.app.should_stop and process and process.poll() is None:
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                except:
                    pass
            
            # Wait for audio to finish (if not stopped or paused)
            elif process and process.poll() is None and not self.app.should_stop:
                process.wait()
                
        except Exception as e:
            print(f"Error playing sentence {sentence_index}: {e}")
        finally:
            if self.app.current_process == process:
                self.app.current_process = None

    def calculate_word_timings(self, words, sentence_duration, sentence_start):
        """Calculate precise timing for each word in a sentence"""
        timings = []
        
        # Simple phoneme-based duration estimation
        total_chars = sum(len(word) for word in words)
        if total_chars == 0:
            return timings
            
        char_time = sentence_duration / total_chars
        
        cumulative_time = 0
        pos_in_sentence = 0
        
        for word in words:
            word_chars = len(word)
            word_duration = word_chars * char_time
            
            # Find word position in the sentence
            word_start_in_sentence = sentence_start + pos_in_sentence
            word_end_in_sentence = word_start_in_sentence + len(word)
            
            timings.append((
                cumulative_time,  # start time
                cumulative_time + word_duration,  # end time
                word_start_in_sentence,  # start position in text
                word_end_in_sentence     # end position in text
            ))
            
            cumulative_time += word_duration
            pos_in_sentence += len(word) + 1  # +1 for space
            
        return timings

    def highlight_word(self, start_offset, end_offset):
        """Highlight a word in the text buffer"""
        try:
            if start_offset >= 0 and end_offset >= 0:
                start_iter = self.app.text_buffer.get_iter_at_offset(start_offset)
                end_iter = self.app.text_buffer.get_iter_at_offset(end_offset)
                
                # Clear previous highlights
                self.app.text_buffer.remove_tag(self.highlight_tag, 
                                              self.app.text_buffer.get_start_iter(),
                                              self.app.text_buffer.get_end_iter())
                
                # Apply highlight
                self.app.text_buffer.apply_tag(self.highlight_tag, start_iter, end_iter)
                
                # Scroll to word
                self.text_view.scroll_to_iter(start_iter, 0.0, True, 0.0, 0.5)
        except Exception as e:
            print(f"Error highlighting word: {e}")

    def clear_highlight(self):
        try:
            self.app.text_buffer.remove_tag(self.highlight_tag, 
                                          self.app.text_buffer.get_start_iter(),
                                          self.app.text_buffer.get_end_iter())
        except:
            pass

    def cleanup_temp_files(self):
        """Clean up temporary audio files"""
        for temp_file in self.app.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except:
                pass
        self.app.temp_files = []

    def on_playback_finished(self):
        self.app.is_playing = False
        self.app.is_paused = False
        self.app.should_stop = True
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        # Don't clear highlight here as it might interfere with stop functionality
        self.cleanup_temp_files()

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()
