#!/usr/bin/env python3
import os
import sys
import threading
import queue
import urllib.request
import tarfile
import re
from pathlib import Path
import numpy as np
import pyaudio
import sherpa_onnx

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

# --- Setup Paths & Engine Registry ---
BASE_DIR = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
CHUNK_SIZE = 480

# Registry for automatic ONNX downloads
SHERPA_MODELS = {
    "zipformer": {
        "folder": "sherpa-onnx-streaming-zipformer-en-2023-06-26",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2"
    },
    "sensevoice": {
        "folder": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
    }
}


class LiveTranscriberApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.live_transcriber")
        # AI Engines
        self.recognizer_online = None  # For Zipformer
        self.recognizer_offline = None # For SenseVoice
        self.whisper_model = None      # For pywhispercpp
        
        # Audio & Threading
        self.audio_queue = queue.Queue()
        self.is_recording = False
        self.pyaudio_instance = None
        self.audio_stream = None
        self.history_text = ""

    def do_activate(self):
        window = Adw.ApplicationWindow(application=self)
        window.set_default_size(750, 550)
        window.set_title("Ultimate Real-time STT")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        window.set_content(main_box)
        main_box.append(Adw.HeaderBar())

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
        scroller.set_min_content_height(300)
        scroller.set_has_frame(True)

        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_margin_start(16)
        self.text_view.set_margin_end(16)
        self.text_view.set_margin_top(16)
        self.text_view.set_margin_bottom(16)
        self.text_buffer = self.text_view.get_buffer()
        scroller.set_child(self.text_view)
        page_box.append(scroller)

        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls_box.set_halign(Gtk.Align.CENTER)
        
        self.engine_dropdown = Gtk.DropDown.new_from_strings([
            "Sherpa-ONNX (Zipformer - True Streaming)",
            "Alibaba SenseVoice (Fast Auto-Punctuated)",
            "Whisper.cpp (Highest Accuracy)"
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

        # Boot default engine
        self.load_engine(0)

    # --- ENGINE ROUTER ---
    def on_engine_switched(self, dropdown, pspec):
        if self.is_recording:
            self.on_toggle_listening(self.toggle_btn)
        self.toggle_btn.set_sensitive(False)
        self.load_engine(dropdown.get_selected())

    def load_engine(self, idx):
        if idx == 0:
            self.text_buffer.set_text("Switching to Sherpa Zipformer...")
            threading.Thread(target=self.prepare_sherpa_model, args=("zipformer",), daemon=True).start()
        elif idx == 1:
            self.text_buffer.set_text("Switching to Alibaba SenseVoice...")
            threading.Thread(target=self.prepare_sherpa_model, args=("sensevoice",), daemon=True).start()
        elif idx == 2:
            self.text_buffer.set_text("Switching to Whisper.cpp...")
            threading.Thread(target=self.init_whisper_cpp, daemon=True).start()

    def init_whisper_cpp(self):
        try:
            from pywhispercpp.model import Model
            if not self.whisper_model:
                # To use the V3 Turbo model, change 'tiny.en' to 'turbo' below!
                self.whisper_model = Model('tiny.en', n_threads=4, print_realtime=False, print_progress=False)
            GLib.idle_add(self.on_model_ready, "Whisper.cpp loaded successfully. Ready to record!")
        except Exception as e:
            err = f"Whisper failed:\n{str(e)}\n\nRun: pip install pywhispercpp"
            GLib.idle_add(lambda: self.text_buffer.set_text(err))

    # --- DYNAMIC DOWNLOADER ---
    def prepare_sherpa_model(self, model_key):
        model_info = SHERPA_MODELS[model_key]
        folder_name = model_info["folder"]
        
        path1 = BASE_DIR / folder_name
        path2 = BASE_DIR / folder_name / folder_name
        active_dir = path2 if (path2 / "tokens.txt").exists() else path1
        
        # Check if assets exist
        if (active_dir / "tokens.txt").exists():
            self.init_sherpa_engine(model_key, active_dir)
        else:
            self.download_sherpa_worker(model_key, model_info["url"], folder_name)

    def download_sherpa_worker(self, model_key, url, folder_name):
        archive_path = BASE_DIR / f"{folder_name}.tar.bz2"
        try:
            GLib.idle_add(lambda: self.text_buffer.set_text(f"Downloading {model_key} weights..."))
            
            def hook(block_num, block_size, total_size):
                if total_size > 0:
                    fraction = min((block_num * block_size) / total_size, 1.0)
                    mb_dl = (block_num * block_size) / 1048576
                    mb_tot = total_size / 1048576
                    GLib.idle_add(self.refresh_progress_ui, fraction, mb_dl, mb_tot)
            
            urllib.request.urlretrieve(url, archive_path, reporthook=hook)
            GLib.idle_add(lambda: self.progress_bar.set_visible(False))
            GLib.idle_add(lambda: self.text_buffer.set_text("Unpacking archive..."))
            
            with tarfile.open(archive_path, "r:bz2") as tar:
                tar.extractall(path=BASE_DIR)
            if archive_path.exists():
                archive_path.unlink()
                
            self.prepare_sherpa_model(model_key) # Re-verify and load
        except Exception as e:
            err_msg = f"Failed to download {model_key}:\n{str(e)}"
            GLib.idle_add(lambda: self.progress_bar.set_visible(False))
            GLib.idle_add(lambda: self.text_buffer.set_text(err_msg))

    def refresh_progress_ui(self, fraction, mb_dl, mb_tot):
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{mb_dl:.1f} MB / {mb_tot:.1f} MB")
        return False

    def init_sherpa_engine(self, model_key, model_dir):
        try:
            if model_key == "zipformer":
                self.recognizer_online = sherpa_onnx.OnlineRecognizer.from_transducer(
                    encoder=str(model_dir / "encoder-epoch-99-avg-1-chunk-16-left-128.onnx"),
                    decoder=str(model_dir / "decoder-epoch-99-avg-1-chunk-16-left-128.onnx"),
                    joiner=str(model_dir / "joiner-epoch-99-avg-1-chunk-16-left-128.onnx"),
                    tokens=str(model_dir / "tokens.txt"),
                    num_threads=2, sample_rate=SAMPLE_RATE, feature_dim=80,
                    enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
                    rule2_min_trailing_silence=1.2, rule3_min_utterance_length=300.0,
                )
                msg = "Sherpa Zipformer loaded successfully. Ready!"
            
            elif model_key == "sensevoice":
                # SenseVoice supports int8 quantization to save RAM
                model_path = model_dir / "model.int8.onnx"
                if not model_path.exists(): 
                    model_path = model_dir / "model.onnx"
                    
                self.recognizer_offline = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model_path),
                    tokens=str(model_dir / "tokens.txt"),
                    num_threads=2, use_itn=True
                )
                msg = "Alibaba SenseVoice loaded successfully. Ready!"

            GLib.idle_add(self.on_model_ready, msg)
        except Exception as e:
            err = f"Engine init crash:\n{str(e)}"
            GLib.idle_add(lambda: self.text_buffer.set_text(err))

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
            self.engine_dropdown.set_sensitive(False) 
            
            with self.audio_queue.mutex:
                self.audio_queue.queue.clear()
            
            self.pyaudio_instance = pyaudio.PyAudio()
            self.audio_stream = self.pyaudio_instance.open(
                format=pyaudio.paFloat32, channels=1, rate=SAMPLE_RATE, input=True, frames_per_buffer=CHUNK_SIZE
            )

            threading.Thread(target=self.capture_audio_loop, daemon=True).start()
            threading.Thread(target=self.process_audio_loop, daemon=True).start()
        else:
            self.is_recording = False
            self.toggle_btn.set_label("Start Listening")
            self.toggle_btn.remove_css_class("destructive-action")
            self.toggle_btn.add_css_class("suggested-action")
            self.engine_dropdown.set_sensitive(True)

    def on_clear_text(self, button):
        self.history_text = ""
        self.text_buffer.set_text("")

    # --- AUDIO THREADS ---
    def capture_audio_loop(self):
        while self.is_recording:
            try:
                data = self.audio_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                self.audio_queue.put(np.frombuffer(data, dtype=np.float32))
            except Exception:
                break
                
        try:
            if self.audio_stream:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            if self.pyaudio_instance:
                self.pyaudio_instance.terminate()
        except Exception:
            pass

    def process_audio_loop(self):
        idx = self.engine_dropdown.get_selected()
        if idx == 0: self.run_zipformer()
        elif idx == 1: self.run_sensevoice()
        elif idx == 2: self.run_whisper()

    # ENGINE 1: SHERPA ZIPFORMER
    def run_zipformer(self):
        online_stream = self.recognizer_online.create_stream()
        last_text = ""
        while self.is_recording:
            try:
                samples = self.audio_queue.get(timeout=0.2)
                online_stream.accept_waveform(SAMPLE_RATE, samples)
            except queue.Empty: continue

            while self.recognizer_online.is_ready(online_stream):
                self.recognizer_online.decode_stream(online_stream)

            is_endpoint = self.recognizer_online.is_endpoint(online_stream)
            raw = self.recognizer_online.get_result(online_stream)
            current_text = raw.strip() if isinstance(raw, str) else raw.text.strip()

            if current_text and current_text != last_text:
                last_text = current_text
                GLib.idle_add(self.update_ui_text, self.history_text + " " + current_text)

            if is_endpoint:
                self.recognizer_online.reset(online_stream)
                if current_text: self.history_text += " " + current_text
                last_text = ""

    # ENGINE 2: ALIBABA SENSEVOICE
    def run_sensevoice(self):
        buffer = np.array([], dtype=np.float32)
        CHUNK_SEC = 1.0 # Fast 1-second chunks
        while self.is_recording:
            try:
                buffer = np.concatenate((buffer, self.audio_queue.get(timeout=0.2)))
            except queue.Empty: continue

            if len(buffer) >= SAMPLE_RATE * CHUNK_SEC:
                if np.sqrt(np.mean(buffer**2)) > 0.005: # Gate silence
                    stream = self.recognizer_offline.create_stream()
                    stream.accept_waveform(SAMPLE_RATE, buffer)
                    self.recognizer_offline.decode_stream(stream)
                    
                    raw = stream.result
                    text = raw.strip() if isinstance(raw, str) else raw.text.strip()
                    # Strip out language/emotion tags like <|en|><|NEUTRAL|>
                    text = re.sub(r'<\|.*?\|>', '', text).strip()
                    
                    if text:
                        self.history_text += " " + text
                        GLib.idle_add(self.update_ui_text, self.history_text)
                buffer = np.array([], dtype=np.float32)

    # ENGINE 3: WHISPER.CPP
    def run_whisper(self):
        buffer = np.array([], dtype=np.float32)
        CHUNK_SEC = 2.0 
        while self.is_recording:
            try:
                buffer = np.concatenate((buffer, self.audio_queue.get(timeout=0.2)))
            except queue.Empty: continue

            if len(buffer) >= SAMPLE_RATE * CHUNK_SEC:
                if np.sqrt(np.mean(buffer**2)) > 0.005:
                    segments = self.whisper_model.transcribe(buffer)
                    text = " ".join([seg.text for seg in segments]).strip()
                    text = re.sub(r'\[.*?\]|\(.*?\)', '', text)
                    
                    ghosts = ["ignore it. Okay.", "ignore it.", "Okay.", "Thank you.", "Thank you", "you"]
                    for g in ghosts:
                        if text.strip() == g: text = ""
                    
                    if text.strip():
                        self.history_text += " " + text.strip()
                        GLib.idle_add(self.update_ui_text, self.history_text)
                buffer = np.array([], dtype=np.float32)

    def update_ui_text(self, full_text):
        self.text_buffer.set_text(full_text.strip())
        self.text_view.scroll_to_mark(self.text_buffer.get_insert(), 0.0, True, 0.5, 1.0)
        return False

if __name__ == "__main__":
    app = LiveTranscriberApp()
    sys.exit(app.run(sys.argv))