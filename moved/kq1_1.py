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
        self.playback_queue = queue.Queue()
        self.current_sentence_index = 0

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
        
        # Voice selection
        voice_label = Gtk.Label(label="Voice:")
        controls_box.append(voice_label)
        
        self.voice_combo = Gtk.ComboBoxText()
        self.voice_combo.append("af_heart", "af_heart", "af_alloy", "af_alloy",)
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
        
        # Set up toolbar view
        self.toolbar_view.add_top_bar(headerbar)
        self.toolbar_view.set_content(main_box)
        self.set_content(self.toolbar_view)

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
        self.app.current_sentence_index = 0
        self.app.temp_files = []
        
        # Split text into sentences
        self.app.sentences = self.split_into_sentences(text)
        self.app.sentence_positions = self.get_sentence_positions(text, self.app.sentences)
        
        # Clear previous highlights
        self.clear_highlight()
        
        # Start audio generation and playback in background
        threading.Thread(target=self.generate_and_play_audio, daemon=True).start()

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
        self.cleanup_temp_files()

    def resume_playback(self):
        self.app.is_playing = True
        self.app.is_paused = False
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

    def generate_and_play_audio(self):
        """Generate and play audio sentence by sentence"""
        try:
            voice = self.voice_combo.get_active_id()
            speed = self.speed_adjustment.get_value()
            
            # Generate and play each sentence
            for i, sentence in enumerate(self.app.sentences):
                if not self.app.is_playing:
                    break
                    
                # Generate audio for current sentence
                samples, sample_rate = self.app.kokoro.create(
                    sentence,
                    voice=voice,
                    speed=speed,
                    lang="en-us"
                )
                
                # Create temporary file for this sentence
                temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                sf.write(temp_file.name, samples, sample_rate)
                self.app.temp_files.append(temp_file.name)
                
                # Play the sentence and highlight simultaneously
                self.play_sentence_with_highlighting(i, temp_file.name, sentence, sample_rate)
                
                # Small gap between sentences
                if self.app.is_playing and i < len(self.app.sentences) - 1:
                    time.sleep(0.1)
                    
        except Exception as e:
            print(f"Error in audio generation/playback: {e}")
        finally:
            GLib.idle_add(self.on_playback_finished)
            self.cleanup_temp_files()

    def play_sentence_with_highlighting(self, sentence_index, audio_file, sentence, sample_rate):
        """Play a single sentence and highlight words in sync"""
        if not self.app.is_playing:
            return
            
        try:
            # Split sentence into words for highlighting
            words = sentence.split()
            if not words:
                return
                
            # Calculate approximate duration of the sentence
            audio_data, _ = sf.read(audio_file)
            sentence_duration = len(audio_data) / sample_rate
            
            # Calculate time per word
            time_per_word = sentence_duration / len(words) if len(words) > 0 else 0
            
            # Start playing audio
            try:
                self.app.current_process = subprocess.Popen(['paplay', audio_file])
            except FileNotFoundError:
                try:
                    self.app.current_process = subprocess.Popen(['aplay', audio_file])
                except FileNotFoundError:
                    self.app.current_process = subprocess.Popen(['play', audio_file])
            
            # Highlight words in sync with audio playback
            sentence_start, sentence_end = self.app.sentence_positions[sentence_index]
            sentence_text = self.app.text_buffer.get_text(
                self.app.text_buffer.get_iter_at_offset(sentence_start),
                self.app.text_buffer.get_iter_at_offset(sentence_end),
                False
            )
            
            word_positions_in_sentence = self.get_word_positions_in_sentence(sentence_text, sentence_start)
            
            start_time = time.time()
            for word_index, (word_start, word_end) in enumerate(word_positions_in_sentence):
                if not self.app.is_playing:
                    break
                    
                # Calculate when this word should be highlighted
                target_time = start_time + (word_index * time_per_word)
                
                # Wait until it's time to highlight this word
                while self.app.is_playing and time.time() < target_time:
                    if self.app.is_paused:
                        # Handle pause
                        pause_start = time.time()
                        while self.app.is_paused and self.app.is_playing:
                            time.sleep(0.01)
                        # Adjust timing for pause duration
                        start_time += (time.time() - pause_start)
                        target_time = start_time + (word_index * time_per_word)
                    time.sleep(0.005)  # Small sleep to prevent busy waiting
                
                if self.app.is_playing:
                    # Highlight current word
                    GLib.idle_add(self.highlight_word, word_start, word_end)
            
            # Wait for audio to finish playing
            if self.app.current_process:
                self.app.current_process.wait()
                
        except Exception as e:
            print(f"Error playing sentence: {e}")

    def get_word_positions_in_sentence(self, sentence_text, sentence_offset):
        """Get word positions within a sentence"""
        words = sentence_text.split()
        positions = []
        pos = 0
        
        for word in words:
            # Find the start position within the sentence
            start = sentence_text.find(word, pos)
            if start != -1:
                absolute_start = sentence_offset + start
                absolute_end = sentence_offset + start + len(word)
                positions.append((absolute_start, absolute_end))
                pos = start + len(word)
        return positions

    def highlight_word(self, start_offset, end_offset):
        """Highlight a word in the text buffer"""
        try:
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
        self.app.text_buffer.remove_tag(self.highlight_tag, 
                                      self.app.text_buffer.get_start_iter(),
                                      self.app.text_buffer.get_end_iter())

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
        self.play_button.set_sensitive(True)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.pause_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        self.clear_highlight()
        self.cleanup_temp_files()

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()
