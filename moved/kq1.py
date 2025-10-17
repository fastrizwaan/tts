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
        self.word_positions = []
        self.word_timings = []
        self.current_process = None
        self.temp_audio_file = None

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
        self.text_buffer.set_text("Enter your text here...")
        
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
        self.app.word_positions = self.get_word_positions(text)
        
        # Start audio generation in background
        threading.Thread(target=self.generate_audio, args=(text,), daemon=True).start()

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
        # Restart playback
        if self.app.temp_audio_file:
            threading.Thread(target=self.play_audio_with_highlighting, daemon=True).start()

    def get_word_positions(self, text):
        words = text.split()
        positions = []
        pos = 0
        
        for word in words:
            # Find the start position
            start = text.find(word, pos)
            if start != -1:
                end = start + len(word)
                positions.append((start, end))
                pos = end
        return positions

    def generate_audio(self, text):
        try:
            voice = self.voice_combo.get_active_id()
            speed = self.speed_adjustment.get_value()
            
            # Generate complete audio
            samples, sample_rate = self.app.kokoro.create(
                text,
                voice=voice,
                speed=speed,
                lang="en-us"
            )
            
            # Create temporary file for complete audio
            temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            sf.write(temp_file.name, samples, sample_rate)
            self.app.temp_audio_file = temp_file.name
            
            # Estimate word timings (simple approximation)
            self.app.word_timings = self.estimate_word_timings(len(samples), sample_rate, len(self.app.word_positions))
            
            # Start playback with highlighting
            threading.Thread(target=self.play_audio_with_highlighting, daemon=True).start()
            
        except Exception as e:
            print(f"Error generating audio: {e}")
            self.app.is_playing = False
            GLib.idle_add(self.on_playback_finished)

    def estimate_word_timings(self, total_samples, sample_rate, word_count):
        """Estimate timing for each word based on total audio duration"""
        total_duration = total_samples / sample_rate
        time_per_word = total_duration / word_count if word_count > 0 else 0
        
        timings = []
        for i in range(word_count):
            start_time = i * time_per_word
            end_time = (i + 1) * time_per_word
            timings.append((start_time, end_time))
        
        return timings

    def play_audio_with_highlighting(self):
        if not self.app.temp_audio_file or not self.app.word_timings:
            return
            
        try:
            # Play audio
            try:
                self.app.current_process = subprocess.Popen(['paplay', self.app.temp_audio_file])
            except FileNotFoundError:
                try:
                    self.app.current_process = subprocess.Popen(['aplay', self.app.temp_audio_file])
                except FileNotFoundError:
                    self.app.current_process = subprocess.Popen(['play', self.app.temp_audio_file])
            
            # Start highlighting based on timing
            start_time = time.time()
            
            for i, (word_start, word_end) in enumerate(self.app.word_timings):
                if not self.app.is_playing:
                    break
                    
                # Wait until it's time to highlight this word
                while self.app.is_playing and (time.time() - start_time) < word_start:
                    if self.app.is_paused:
                        # Pause logic - wait until resumed
                        pause_start = time.time()
                        while self.app.is_paused and self.app.is_playing:
                            time.sleep(0.01)
                        # Adjust start time to account for pause duration
                        start_time += (time.time() - pause_start)
                    time.sleep(0.01)
                
                if self.app.is_playing:
                    # Highlight current word
                    GLib.idle_add(self.highlight_word, i)
            
            # Wait for audio to finish
            if self.app.current_process:
                self.app.current_process.wait()
                
        except Exception as e:
            print(f"Playback error: {e}")
        finally:
            # Cleanup
            GLib.idle_add(self.on_playback_finished)
            try:
                if self.app.temp_audio_file and os.path.exists(self.app.temp_audio_file):
                    os.unlink(self.app.temp_audio_file)
                self.app.temp_audio_file = None
            except:
                pass

    def highlight_word(self, index):
        if index < len(self.app.word_positions):
            start, end = self.app.word_positions[index]
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
