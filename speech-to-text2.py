#!/usr/bin/env python3
import os
import sys
import threading
import queue
import urllib.request
import tarfile
from pathlib import Path
import numpy as np
import pyaudio
import sherpa_onnx

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

# --- Setup Constants ---
BASE_DIR = Path(__file__).resolve().parent
MODEL_FOLDER_NAME = "sherpa-onnx-streaming-zipformer-en-2023-06-26"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_FOLDER_NAME}.tar.bz2"
ARCHIVE_PATH = BASE_DIR / f"{MODEL_FOLDER_NAME}.tar.bz2"

SAMPLE_RATE = 16000
CHUNK_SIZE = 480


class LiveTranscriberApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.live_transcriber")
        self.recognizer = None
        self.whisper_model = None
        
        self.audio_queue = queue.Queue()
        self.is_recording = False
        self.recording_thread = None
        self.processing_thread = None
        self.pyaudio_instance = None
        self.audio_stream = None
        self.history_text = ""
        
        self.model_dir = None
        self.encoder_path = ""
        self.decoder_path = ""
        self.joiner_path = ""
        self.tokens_path = ""

    def resolve_paths(self):
        path1 = BASE_DIR / MODEL_FOLDER_NAME
        path2 = BASE_DIR / MODEL_FOLDER_NAME / MODEL_FOLDER_NAME
        
        if (path2 / "tokens.txt").exists():
            self.model_dir = path2
        else:
            self.model_dir = path1
            
        self.encoder_path = str(self.model_dir / "encoder-epoch-99-avg-1-chunk-16-left-128.onnx")
        self.decoder_path = str(self.model_dir / "decoder-epoch-99-avg-1-chunk-16-left-128.onnx")
        self.joiner_path = str(self.model_dir / "joiner-epoch-99-avg-1-chunk-16-left-128.onnx")
        self.tokens_path = str(self.model_dir / "tokens.txt")

    def do_activate(self):
        window = Adw.ApplicationWindow(application=self)
        window.set_default_size(700, 500)
        window.set_title("Multi-Engine Real-time STT")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        window.set_content(main_box)

        header = Adw.HeaderBar()
        main_box.append(header)

        clamp = Adw.Clamp()
        page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page_box.set_margin_top(24)
        page_box.set_margin_bottom(24)
        clamp.set_child(page_box)
        main_box.append(clamp)
        
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_visible(False)
        self.progress_bar.set_show_text(True)
        page_box.append(self.progress_bar)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.set_min_content_height(250)
        scroller.set_has_frame(True)

        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_margin_start(12)
        self.text_view.set_margin_end(12)
        self.text_view.set_margin_top(12)
        self.text_view.set_margin_bottom(12)
        self.text_buffer = self.text_view.get_buffer()
        
        scroller.set_child(self.text_view)
        page_box.append(scroller)

        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls_box.set_halign(Gtk.Align.CENTER)
        
        # --- NEW: Engine Selector Dropdown ---
        self.engine_dropdown = Gtk.DropDown.new_from_strings([
            "Sherpa-ONNX (True Real-time)",
            "Whisper.cpp (2s Buffered)"
        ])
        self.engine_dropdown.connect("notify::selected", self.on_engine_switched)
        controls_box.append(self.engine_dropdown)
        
        self.toggle_btn = Gtk.Button(label="Start Listening")
        self.toggle_btn.add_css_class("suggested-action")
        self.toggle_btn.set_sensitive(False)
        self.toggle_btn.connect("clicked", self.on_toggle_listening)
        controls_box.append(self.toggle_btn)

        self.clear_btn = Gtk.Button(label="Clear Text")
        self.clear_btn.connect("clicked", self.on_clear_text)
        controls_box.append(self.clear_btn)

        page_box.append(controls_box)
        window.present()

        # Initialize default engine (Sherpa)
        self.check_and_prepare_sherpa()

    # --- ENGINE SWITCHING LOGIC ---
    def on_engine_switched(self, dropdown, pspec):
        """Triggered when the user changes the dropdown selection."""
        if self.is_recording:
            self.on_toggle_listening(self.toggle_btn) # Stop recording cleanly
            
        selected_idx = dropdown.get_selected()
        self.toggle_btn.set_sensitive(False)
        
        if selected_idx == 0:
            self.text_buffer.set_text("Switching to Sherpa-ONNX...")
            self.check_and_prepare_sherpa()
        else:
            self.text_buffer.set_text("Switching to Whisper.cpp...\n(Note: GGML Model will automatically download to ~/.cache/pywhispercpp if missing)")
            threading.Thread(target=self.init_whisper_cpp, daemon=True).start()

    def init_whisper_cpp(self):
        """Initializes the Whisper.cpp engine in the background."""
        try:
            from pywhispercpp.model import Model
            # Loads the tiny english model. Natively auto-downloads if not present.
            if not self.whisper_model:
                self.whisper_model = Model('tiny.en', n_threads=4, print_realtime=False, print_progress=False)
            GLib.idle_add(self.on_model_ready, "Whisper.cpp (Tiny) loaded successfully. Ready to record!")
        except ImportError:
            err = "pywhispercpp is not installed!\nRun: pip install pywhispercpp"
            GLib.idle_add(lambda: self.text_buffer.set_text(err))
        except Exception as e:
            err = f"Whisper initialization failed:\n{str(e)}"
            GLib.idle_add(lambda: self.text_buffer.set_text(err))

    # --- SHERPA DOWNLOAD LOGIC ---
    def check_and_prepare_sherpa(self):
        self.resolve_paths()
        if os.path.exists(self.tokens_path) and os.path.exists(self.encoder_path):
            threading.Thread(target=self.init_sherpa_onnx, daemon=True).start()
        else:
            self.text_buffer.set_text("Sherpa weights not found locally. Connecting to server...")
            threading.Thread(target=self.download_sherpa_worker, daemon=True).start()

    def update_download_progress(self, block_num, block_size, total_size):
        if total_size > 0:
            downloaded = block_num * block_size
            fraction = min(downloaded / total_size, 1.0)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            GLib.idle_add(self.refresh_progress_ui, fraction, mb_downloaded, mb_total)

    def refresh_progress_ui(self, fraction, mb_downloaded, mb_total):
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{mb_downloaded:.1f} MB / {mb_total:.1f} MB")
        self.text_buffer.set_text(f"Downloading model assets... {fraction:.1%}")
        return False

    def download_sherpa_worker(self):
        try:
            urllib.request.urlretrieve(MODEL_URL, ARCHIVE_PATH, reporthook=self.update_download_progress)
            GLib.idle_add(lambda: self.progress_bar.set_visible(False))
            GLib.idle_add(lambda: self.text_buffer.set_text("Download complete! Unpacking archive..."))
            
            with tarfile.open(ARCHIVE_PATH, "r:bz2") as tar:
                tar.extractall(path=BASE_DIR)
            if ARCHIVE_PATH.exists():
                ARCHIVE_PATH.unlink()
                
            self.resolve_paths()
            self.init_sherpa_onnx()
        except Exception as e:
            err_msg = f"Failed to download model:\n{str(e)}"
            GLib.idle_add(lambda: self.progress_bar.set_visible(False))
            GLib.idle_add(lambda: self.text_buffer.set_text(err_msg))

    def init_sherpa_onnx(self):
        try:
            if not self.recognizer:
                self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    encoder=self.encoder_path,
                    decoder=self.decoder_path,
                    joiner=self.joiner_path,
                    tokens=self.tokens_path,
                    num_threads=2,
                    sample_rate=SAMPLE_RATE,
                    feature_dim=80,
                    enable_endpoint_detection=True,
                    rule1_min_trailing_silence=2.4,
                    rule2_min_trailing_silence=1.2,
                    rule3_min_utterance_length=300.0,
                )
            GLib.idle_add(self.on_model_ready, "Sherpa-ONNX loaded successfully. Ready to record!")
        except Exception as e:
            err_msg = f"Sherpa initialization crash:\n{str(e)}"
            GLib.idle_add(lambda: self.text_buffer.set_text(err_msg))

    # --- SHARED UI CONTROLS ---
    def on_model_ready(self, msg_text):
        self.text_buffer.set_text(msg_text)
        self.toggle_btn.set_sensitive(True)
        self.engine_dropdown.set_sensitive(True)
        return False

    def on_toggle_listening(self, button):
        if not self.is_recording:
            self.is_recording = True
            self.toggle_btn.set_label("Stop Listening")
            self.toggle_btn.remove_css_class("suggested-action")
            self.toggle_btn.add_css_class("destructive-action")
            self.engine_dropdown.set_sensitive(False) # Lock engine switching while recording
            
            # Clear queue before starting
            with self.audio_queue.mutex:
                self.audio_queue.queue.clear()
            
            self.pyaudio_instance = pyaudio.PyAudio()
            self.audio_stream = self.pyaudio_instance.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )

            self.recording_thread = threading.Thread(target=self.capture_audio_loop, daemon=True)
            self.processing_thread = threading.Thread(target=self.process_audio_loop, daemon=True)
            
            self.recording_thread.start()
            self.processing_thread.start()
        else:
            self.is_recording = False
            self.toggle_btn.set_label("Start Listening")
            self.toggle_btn.remove_css_class("destructive-action")
            self.toggle_btn.add_css_class("suggested-action")
            self.engine_dropdown.set_sensitive(True)
            
            if self.audio_stream:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            if self.pyaudio_instance:
                self.pyaudio_instance.terminate()

    def on_clear_text(self, button):
        self.history_text = ""
        self.text_buffer.set_text("")

    # --- AUDIO THREADS ---
    def capture_audio_loop(self):
        while self.is_recording:
            try:
                data = self.audio_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                samples = np.frombuffer(data, dtype=np.float32)
                self.audio_queue.put(samples)
            except Exception:
                break

    def process_audio_loop(self):
        """Routes audio to the correct engine processor based on dropdown selection."""
        selected_engine = self.engine_dropdown.get_selected()
        
        if selected_engine == 0:
            self.run_sherpa_processor()
        else:
            self.run_whisper_processor()

    def run_sherpa_processor(self):
        online_stream = self.recognizer.create_stream()
        last_text = ""

        while self.is_recording:
            try:
                samples = self.audio_queue.get(timeout=0.2)
                online_stream.accept_waveform(SAMPLE_RATE, samples)
            except queue.Empty:
                continue

            while self.recognizer.is_ready(online_stream):
                self.recognizer.decode_stream(online_stream)

            is_endpoint = self.recognizer.is_endpoint(online_stream)
            raw_result = self.recognizer.get_result(online_stream)
            
            current_text = raw_result.strip() if isinstance(raw_result, str) else raw_result.text.strip()

            if current_text and current_text != last_text:
                last_text = current_text
                GLib.idle_add(self.update_ui_text, self.history_text + " " + current_text)

            if is_endpoint:
                self.recognizer.reset(online_stream)
                if current_text:
                    self.history_text += " " + current_text
                last_text = ""

    def run_whisper_processor(self):
        """Sliding window approach for Whisper.cpp chunked buffering."""
        whisper_buffer = np.array([], dtype=np.float32)
        CHUNK_SECONDS = 2.0  # Process every 2 seconds
        
        while self.is_recording:
            try:
                samples = self.audio_queue.get(timeout=0.2)
                whisper_buffer = np.concatenate((whisper_buffer, samples))
            except queue.Empty:
                continue

            # If we hit the 2-second mark, feed it to Whisper
            if len(whisper_buffer) >= SAMPLE_RATE * CHUNK_SECONDS:
                segments = self.whisper_model.transcribe(whisper_buffer)
                
                # Pywhispercpp returns a list of Segment objects
                text = " ".join([seg.text for seg in segments]).strip()
                
                if text:
                    self.history_text += " " + text
                    GLib.idle_add(self.update_ui_text, self.history_text)
                
                # Clear buffer for the next chunk
                whisper_buffer = np.array([], dtype=np.float32)

    def update_ui_text(self, full_text):
        self.text_buffer.set_text(full_text.strip())
        mark = self.text_buffer.get_insert()
        self.text_view.scroll_to_mark(mark, 0.0, True, 0.5, 1.0)
        return False


if __name__ == "__main__":
    app = LiveTranscriberApp()
    sys.exit(app.run(sys.argv))