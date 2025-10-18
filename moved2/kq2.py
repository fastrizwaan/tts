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
import string

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
        self.sentence_word_positions = []
        self.current_process = None
        self.playback_queue = queue.Queue()

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
        self.set_title("Kokoro TTS Highlighter")

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_buffer = self.text_view.get_buffer()
        self.text_buffer.set_text("Enter your text here. This is a second sentence! And here's a third one?")
        
        # Create highlight tag
        self.highlight_tag = self.text_buffer.create_tag("highlight", background="yellow")
        
        scrolled.set_child(self.text_view)
        main_box.append(scrolled)

        # Controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls_box.set_margin_top(12)
        controls_box.set_margin_bottom(12)
        controls_box.set_margin_start(12)
        controls_box.set_margin_end(12)
        
        # Voice selection
        voice_label = Gtk.Label(label="Voice:")
        controls_box.append(voice_label)
        
        self.voice_combo = Gtk.ComboBoxText()
        self.voice_combo.append("af_heart", "af_heart")
        # Add more voices as needed
        self.voice_combo.set_active(0)
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
        self.app.text_buffer = self.text_buffer
        
        # Parse text into sentences
        self.app.sentences, self.app.sentence_positions = self.split_into_sentences(text)
        
        # Calculate word positions for each sentence
        self.app.sentence_word_positions = []
        for sentence, (start_pos, end_pos) in zip(self.app.sentences, self.app.sentence_positions):
            word_positions = self.get_word_positions_in_sentence(sentence, start_pos)
            self.app.sentence_word_positions.append(word_positions)
        
        # Start audio generation in background
        threading.Thread(target=self.generate_audio_sentences, args=(text,), daemon=True).start()
        
        # Start playback handler
        if not self.app.audio_thread or not self.app.audio_thread.is_alive():
            self.app.audio_thread = threading.Thread(target=self.audio_playback_worker, daemon=True)
            self.app.audio_thread.start()

    def on_pause_clicked(self, button):
        self.app.is_playing = False
        self.app.is_paused = True
        if self.app.current_process:
            self.app.current_process.terminate()
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)

    def on_stop_clicked(self, button):
        self.app.is_playing = False
        self.app.is_paused = False
        if self.app.current_process:
            self.app.current_process.terminate()
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        self.clear_highlight()

    def resume_playback(self):
        self.app.is_playing = True
        self.app.is_paused = False
        self.play_button.set_sensitive(False)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(True)

    def split_into_sentences(self, text):
        """Split text into sentences using punctuation"""
        # Split by sentence-ending punctuation
        sentences = []
        sentence_positions = []
        current_sentence = ""
        start_pos = 0
        
        for i, char in enumerate(text):
            current_sentence += char
            if char in '.!?':
                # End of sentence
                clean_sentence = current_sentence.strip()
                if clean_sentence:
                    sentences.append(clean_sentence)
                    end_pos = start_pos + len(current_sentence)
                    sentence_positions.append((start_pos, end_pos))
                current_sentence = ""
                start_pos = i + 1
        
        # Handle any remaining text
        if current_sentence.strip():
            clean_sentence = current_sentence.strip()
            if clean_sentence:
                sentences.append(clean_sentence)
                sentence_positions.append((start_pos, len(text)))
        
        return sentences, sentence_positions

    def get_word_positions_in_sentence(self, sentence, sentence_start_pos):
        """Get word positions within a sentence using string.punctuation"""
        # Split into words and clean punctuation
        words = sentence.split()
        clean_words = [word.strip(string.punctuation) for word in words]
        
        word_positions = []
        pos = 0
        
        for original_word, clean_word in zip(words, clean_words):
            if clean_word:  # Only process non-empty words
                # Find the clean word in the original sentence
                word_start = sentence.find(clean_word, pos)
                if word_start != -1:
                    abs_start = sentence_start_pos + word_start
                    abs_end = abs_start + len(clean_word)
                    word_positions.append((abs_start, abs_end))
                    pos = word_start + len(clean_word)
        return word_positions

    def generate_audio_sentences(self, text):
        try:
            voice = self.voice_combo.get_active_id()
            speed = self.speed_adjustment.get_value()
            
            # Generate audio for each sentence
            for i, sentence in enumerate(self.app.sentences):
                if not self.app.is_playing and not self.app.is_paused:
                    break
                    
                samples, sample_rate = self.app.kokoro.create(
                    sentence,
                    voice=voice,
                    speed=speed,
                    lang="en-us"
                )
                
                # Create temporary file for sentence audio
                temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                sf.write(temp_file.name, samples, sample_rate)
                
                # Put in queue with sentence info
                self.app.playback_queue.put((i, temp_file.name, sample_rate))
            
            # Signal end of stream
            self.app.playback_queue.put((None, None, None))
            
        except Exception as e:
            print(f"Error generating audio: {e}")
            self.app.is_playing = False

    def audio_playback_worker(self):
        while True:
            try:
                sentence_index, temp_file, sample_rate = self.app.playback_queue.get(timeout=0.1)
                
                if sentence_index is None:  # End of stream
                    break
                
                # Highlight words in this sentence
                if sentence_index < len(self.app.sentence_word_positions):
                    word_positions = self.app.sentence_word_positions[sentence_index]
                    self.highlight_words_in_sentence(word_positions)
                
                # Play audio sentence
                if self.app.is_playing:
                    # Try pulseaudio first, fallback to alsa
                    try:
                        self.app.current_process = subprocess.Popen(['paplay', temp_file])
                    except FileNotFoundError:
                        try:
                            self.app.current_process = subprocess.Popen(['aplay', temp_file])
                        except FileNotFoundError:
                            self.app.current_process = subprocess.Popen(['play', temp_file])
                    
                    # Wait for playback to complete
                    self.app.current_process.wait()
                    self.app.current_process = None
                    
                    # Clean up temp file
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
                        
                # Wait if paused
                while self.app.is_paused:
                    time.sleep(0.1)
                    
                if not self.app.is_playing and not self.app.is_paused:
                    # Clean up any remaining temp files
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
                    break
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Playback error: {e}")
                break
                
        GLib.idle_add(self.on_playback_finished)

    def highlight_words_in_sentence(self, word_positions):
        """Highlight words in a sentence with proper timing"""
        if not word_positions:
            return
            
        # Simple approach: highlight each word for equal duration
        word_count = len(word_positions)
        highlight_duration = 0.3  # seconds per word (adjust as needed)
        
        for i, (start, end) in enumerate(word_positions):
            if not self.app.is_playing:
                break
                
            # Highlight current word
            GLib.idle_add(self.highlight_word, start, end)
            
            # Wait for highlight duration, but check for pause/stop
            start_time = time.time()
            while time.time() - start_time < highlight_duration:
                if not self.app.is_playing:
                    return
                if self.app.is_paused:
                    # Handle pause
                    pause_start = time.time()
                    while self.app.is_paused and self.app.is_playing:
                        time.sleep(0.01)
                    # Adjust timing
                    start_time += (time.time() - pause_start)
                time.sleep(0.01)
        
        # Clear highlight after sentence
        GLib.idle_add(self.clear_highlight)

    def highlight_word(self, start, end):
        start_iter = self.app.text_buffer.get_iter_at_offset(start)
        end_iter = self.app.text_buffer.get_iter_at_offset(end)
        
        # Clear previous highlights
        self.app.text_buffer.remove_tag(self.highlight_tag, 
                                      self.app.text_buffer.get_start_iter(),
                                      self.app.text_buffer.get_end_iter())
        
        # Apply highlight
        self.app.text_buffer.apply_tag(self.highlight_tag, start_iter, end_iter)
        
        # Scroll to word
        self.text_view.scroll_to_iter(start_iter, 0.0, True, 0.0, 0.5)

    def clear_highlight(self):
        self.app.text_buffer.remove_tag(self.highlight_tag, 
                                      self.app.text_buffer.get_start_iter(),
                                      self.app.text_buffer.get_end_iter())

    def on_playback_finished(self):
        self.app.is_playing = False
        self.app.is_paused = False
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        self.clear_highlight()

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()
