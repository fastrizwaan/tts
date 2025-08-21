#!/usr/bin/env python3
import os, re, pathlib, threading, subprocess, sys, time
import numpy as np, soundfile as sf
from multiprocessing import Process, Queue as MPQ
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gtk, Gio, Adw

# --- config ---
MODEL = "/app/share/kokoro-models/kokoro-v1.0.onnx"
VOICES = "/app/share/kokoro-models/voices-v1.0.bin"
DEFAULT_TEXT = ("This is the 1st sentence. This is 2nd sentence. This is 3rd sentence! "
                "Is this 4th sentence? This is 5th sentence. And this is 6th sentence. "
                "and this is 7th sentence. And while it is 8th sentence. "
                "and this should be 9th sentence. And to stop the long string this is the 10th sentence.")
LANG = "en-us"
VOICE = "af_sarah"
SPEED = 1.0
SR = 24000
CHUNK_FRAMES = 2400
PREROLL = 3  # ~0.1s chunks
# ---------------

try:
    from kokoro_onnx import Kokoro
except ImportError:
    print("Kokoro not available, running in text-only mode")
    Kokoro = None

def outdir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)
    return d

def tokenize(t):
    return [p.strip() for p in re.split(r'(?<=[.!?])\s+', t.strip()) if p.strip()]

def f32_to_s16le(x):
    return (np.clip(x, -1, 1) * 32767.0).astype('<i2').tobytes()

def synth_one(kok, idx, sent, d):
    print(f"[SYNTH]{idx}: {sent}")
    wav, sr = kok.create(sent, voice=VOICE, speed=SPEED, lang=LANG)
    if sr != SR:
        print(f"[WARN] sr={sr}!=SR={SR}")
    path = os.path.join(d, f"kokoro_sent_{idx:02d}.wav")
    sf.write(path, wav, sr)
    pcm = f32_to_s16le(wav)
    print(f"[FILE ]{idx}: {path} bytes={len(pcm)}")
    return pcm, path

def producer_proc(sents, start_idx, d, q: MPQ):
    if Kokoro is None:
        print("Kokoro not available, cannot synthesize")
        q.put((None, None, None))
        return
        
    kok = Kokoro(MODEL, VOICES)
    try:
        for i in range(start_idx, len(sents) + 1):
            try:
                q.put((i,) + synth_one(kok, i, sents[i - 1], d))
            except Exception as e:
                print(f"[PROD ] err#{i}: {e}")
    finally:
        q.put((None, None, None))

def choose_play_cmd():
    for c in (["pacat", "--rate", str(SR), "--channels", "1", "--format", "s16le"],
              ["pw-cat", "-p", "--rate", str(SR), "--format", "s16_le", "--channels", "1"]):
        try:
            subprocess.run([c[0], "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return c
        except Exception:
            pass
    return None

class Controls:
    def __init__(self):
        self.paused = threading.Event()  # start playing immediately
        self.stop = threading.Event()
        self.seek_to = None  # sentence index to seek to
        self.seek_lock = threading.Lock()
        self.current_sentence = 1  # track actual playing sentence
        self.sentence_lock = threading.Lock()
        self.playing = False

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_default_size(800, 600)
        self.set_title("Text-to-Speech Player")
        
        # Initialize controls
        self.ctrl = Controls()
        self.q = MPQ(maxsize=16)
        self.prod_process = None
        self.play_thread = None
        self.sentences = []
        self.total_sentences = 0
        
        # Create UI
        self.build_ui()
        
        # Load default text
        self.text_buffer.set_text(DEFAULT_TEXT, -1)
        
    def build_ui(self):
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)
        
        # Header bar
        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)
        
        # Toolbar with buttons
        self.toolbar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        header_bar.pack_start(self.toolbar)
        
        # Play button
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Play")
        self.play_btn.connect("clicked", self.on_play)
        self.toolbar.append(self.play_btn)
        
        # Pause button
        self.pause_btn = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text="Pause", sensitive=False)
        self.pause_btn.connect("clicked", self.on_pause)
        self.toolbar.append(self.pause_btn)
        
        # Stop button
        self.stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop", sensitive=False)
        self.stop_btn.connect("clicked", self.on_stop)
        self.toolbar.append(self.stop_btn)
        
        # Previous button
        self.prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic", tooltip_text="Previous Sentence")
        self.prev_btn.connect("clicked", self.on_prev)
        self.toolbar.append(self.prev_btn)
        
        # Next button
        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic", tooltip_text="Next Sentence")
        self.next_btn.connect("clicked", self.on_next)
        self.toolbar.append(self.next_btn)
        
        # Status label
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_margin_start(12)
        self.toolbar.append(self.status_label)
        
        # TextView for text editing
        text_view_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        main_box.append(text_view_box)
        
        # Create scrolled window for TextView
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        text_view_box.append(scrolled_window)
        
        # Create TextView
        self.text_view = Gtk.TextView()
        self.text_buffer = self.text_view.get_buffer()
        scrolled_window.set_child(self.text_view)
        
        # Set some text view properties
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_top_margin(10)
        self.text_view.set_bottom_margin(10)
        self.text_view.set_left_margin(10)
        self.text_view.set_right_margin(10)
        
    def get_text(self):
        start_iter = self.text_buffer.get_start_iter()
        end_iter = self.text_buffer.get_end_iter()
        text = self.text_buffer.get_text(start_iter, end_iter, True)
        self.sentences = tokenize(text)
        self.total_sentences = len(self.sentences)
        return text
    
    def on_play(self, widget):
        if not self.ctrl.playing:
            # Start playing
            self.get_text()  # Get current text from editor
            
            if not self.sentences:
                self.status_label.set_label("No text to play")
                return
                
            self.ctrl = Controls()
            self.q = MPQ(maxsize=16)
            
            # Start producer process
            d = outdir()
            self.prod_process = Process(target=producer_proc, args=(self.sentences, 1, d, self.q))
            self.prod_process.start()
            
            # Start player thread
            self.play_thread = threading.Thread(target=self.player_thread_ordered, args=(self.q, self.ctrl, self.total_sentences))
            self.play_thread.daemon = True
            self.play_thread.start()
            
            self.ctrl.playing = True
            self.status_label.set_label("Playing...")
            
            # Update button states
            self.play_btn.set_sensitive(False)
            self.pause_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(True)
        else:
            # Resume if paused
            if self.ctrl.paused.is_set():
                self.ctrl.paused.clear()
                self.status_label.set_label("Playing...")
                self.pause_btn.set_icon_name("media-playback-pause-symbolic")
    
    def on_pause(self, widget):
        if self.ctrl.playing:
            if self.ctrl.paused.is_set():
                self.ctrl.paused.clear()
                self.status_label.set_label("Playing...")
                self.pause_btn.set_icon_name("media-playback-pause-symbolic")
            else:
                self.ctrl.paused.set()
                self.status_label.set_label("Paused")
                self.pause_btn.set_icon_name("media-playback-start-symbolic")
    
    def on_stop(self, widget):
        if self.ctrl.playing:
            self.ctrl.stop.set()
            try:
                self.q.put((None, None, None))
            except Exception:
                pass
                
            self.cleanup_playback()
            self.status_label.set_label("Stopped")
    
    def on_prev(self, widget):
        if self.ctrl.playing:
            with self.ctrl.sentence_lock:
                current = self.ctrl.current_sentence
            with self.ctrl.seek_lock:
                if current == 1:
                    # Restart first sentence
                    self.ctrl.seek_to = 1
                    self.status_label.set_label("Restarting sentence 1")
                else:
                    new_idx = current - 1
                    self.ctrl.seek_to = new_idx
                    self.status_label.set_label(f"Seeking to sentence {new_idx}")
    
    def on_next(self, widget):
        if self.ctrl.playing:
            with self.ctrl.sentence_lock:
                current = self.ctrl.current_sentence
            with self.ctrl.seek_lock:
                new_idx = min(self.total_sentences, current + 1)
                if new_idx <= self.total_sentences:
                    self.ctrl.seek_to = new_idx
                    self.status_label.set_label(f"Seeking to sentence {new_idx}")
                else:
                    self.status_label.set_label(f"Already at last sentence ({current})")
    
    def cleanup_playback(self):
        self.ctrl.playing = False
        if self.prod_process and self.prod_process.is_alive():
            self.prod_process.terminate()
            self.prod_process.join(timeout=2)
        
        # Reset button states
        self.play_btn.set_sensitive(True)
        self.pause_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(False)
        self.pause_btn.set_icon_name("media-playback-pause-symbolic")
    
    def player_thread_ordered(self, qin: MPQ, ctrl: Controls, total: int):
        cmd = choose_play_cmd()
        if not cmd:
            print("[ERROR] pacat/pw-cat not found")
            self.status_label.set_label("Error: audio player not found")
            return
            
        print(f"[PLAY ] start: {' '.join(cmd)}")
        
        frame_bytes = 2
        step = CHUNK_FRAMES * frame_bytes
        sec_per_chunk = CHUNK_FRAMES / float(SR)
        buf = {}
        eof = False
        current_playing = 1
        
        # Collect initial buffer
        while not ctrl.stop.is_set() and len(buf) < PREROLL and not eof:
            idx, pcm, _ = qin.get()
            if idx is None:
                eof = True
                break
            buf[idx] = pcm
        
        def restart_player():
            return subprocess.Popen(cmd, stdin=subprocess.PIPE)

        def play_pcm_chunk(p, pcm, idx):
            """Play PCM data in chunks, checking for interruptions"""
            off = 0
            n = len(pcm)
            while off < n and not ctrl.stop.is_set():
                # Check for seek command
                with ctrl.seek_lock:
                    if ctrl.seek_to is not None:
                        return False  # Interrupted for seek
                
                if ctrl.paused.is_set():
                    time.sleep(0.01)
                    continue
                
                chunk = pcm[off:off + step]
                try:
                    p.stdin.write(chunk)
                    p.stdin.flush()
                except Exception as e:
                    print(f"[PLAY ] write err#{idx}: {e}")
                    return False
                
                off += len(chunk)
                time.sleep(sec_per_chunk * 0.8)  # Slightly faster for smoother playback
            return True

        p = restart_player()
        
        try:
            while not ctrl.stop.is_set():
                # Handle seek requests
                with ctrl.seek_lock:
                    if ctrl.seek_to is not None:
                        seek_target = ctrl.seek_to
                        ctrl.seek_to = None
                        current_playing = seek_target
                        
                        # Close current player and start new one for cleaner audio
                        try:
                            if p.stdin:
                                p.stdin.close()
                            p.terminate()
                            p.wait(timeout=1)
                        except:
                            pass
                        
                        p = restart_player()
                        GLib.idle_add(self.status_label.set_label, f"Playing sentence {seek_target}")
                
                # Wait for required sentence to be available
                while current_playing not in buf and not eof and not ctrl.stop.is_set():
                    idx, pcm, _ = qin.get()
                    if idx is None:
                        eof = True
                        break
                    buf[idx] = pcm
                
                # Play current sentence if available
                if current_playing in buf and not ctrl.stop.is_set():
                    pcm = buf[current_playing]
                    print(f"[PLAY ] >>#{current_playing}")
                    
                    # Update current sentence tracker
                    with ctrl.sentence_lock:
                        ctrl.current_sentence = current_playing
                    
                    if play_pcm_chunk(p, pcm, current_playing):
                        print(f"[PLAY ] done #{current_playing}")
                        current_playing += 1
                        
                        # Check if we've finished all sentences
                        if current_playing > total and eof:
                            break
                    else:
                        # Playback was interrupted, continue with seek handling
                        continue
                
                # Keep collecting new sentences
                if not eof and len(buf) < total:
                    try:
                        idx, pcm, _ = qin.get_nowait()
                        if idx is None:
                            eof = True
                        else:
                            buf[idx] = pcm
                    except:
                        time.sleep(0.01)  # No new data available
        
        finally:
            try:
                if p.stdin:
                    p.stdin.close()
                p.wait(timeout=3)
            except Exception:
                pass
            
            GLib.idle_add(self.cleanup_playback)
            GLib.idle_add(self.status_label.set_label, "Playback finished")
        
        print("[PLAY ] exit")

class TTSApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs, application_id="io.github.fastrizwaan.tts")
        self.window = None
        
    def do_activate(self):
        if not self.window:
            self.window = TTSWindow(application=self)
        self.window.present()

if __name__ == "__main__":
    app = TTSApp()
    app.run(sys.argv)
