#!/usr/bin/env python3
import os
import re
import pathlib
import threading
import subprocess
import sys
import time
import numpy as np
import soundfile as sf
from multiprocessing import Process, Queue as MPQ
from gi.repository import Gtk, Gdk, Gio, GLib, Adw, WebKit6
from kokoro_onnx import Kokoro

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

class TTSPlayer:
    def __init__(self):
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.seek_to = None
        self.seek_lock = threading.Lock()
        self.current_sentence = 1
        self.sentence_lock = threading.Lock()
        self.playing = False

class TTSApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.player = TTSPlayer()
        self.queue = MPQ(maxsize=16)
        self.producer_process = None
        self.playback_thread = None
        self.current_text = DEFAULT_TEXT
        self.sentences = tokenize(self.current_text)
        self.play_cmd = choose_play_cmd()

    def do_activate(self):
        # Create main window
        self.window = Adw.ApplicationWindow(application=self, title="Text-to-Speech")
        self.window.set_default_size(800, 600)

        # Create header bar
        header = Adw.HeaderBar()
        self.window.set_content_header_bar(header)

        # Create toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)
        
        # Play button
        self.play_button = Gtk.Button()
        play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        self.play_button.set_child(play_icon)
        self.play_button.connect("clicked", self.on_play_clicked)
        
        # Pause button
        self.pause_button = Gtk.Button()
        pause_icon = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic")
        self.pause_button.set_child(pause_icon)
        self.pause_button.connect("clicked", self.on_pause_clicked)
        
        # Stop button
        self.stop_button = Gtk.Button()
        stop_icon = Gtk.Image.new_from_icon_name("media-playback-stop-symbolic")
        self.stop_button.set_child(stop_icon)
        self.stop_button.connect("clicked", self.on_stop_clicked)
        
        # Previous button
        self.prev_button = Gtk.Button()
        prev_icon = Gtk.Image.new_from_icon_name("go-previous-symbolic")
        self.prev_button.set_child(prev_icon)
        self.prev_button.connect("clicked", self.on_prev_clicked)
        
        # Next button
        self.next_button = Gtk.Button()
        next_icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
        self.next_button.set_child(next_icon)
        self.next_button.connect("clicked", self.on_next_clicked)
        
        # Add buttons to toolbar
        toolbar.append(self.play_button)
        toolbar.append(self.pause_button)
        toolbar.append(self.stop_button)
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        toolbar.append(self.prev_button)
        toolbar.append(self.next_button)
        
        # Create main content area
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(toolbar)
        
        # Create WebView for text editing and rendering
        self.webview = WebKit6.WebView()
        self.webview.load_html(self.get_html_content(), None)
        
        # Create scrolled window for webview
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self.webview)
        main_box.append(scrolled)
        
        self.window.set_content(main_box)
        self.window.show()

    def get_html_content(self):
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: sans-serif;
                    padding: 20px;
                    line-height: 1.6;
                }}
                #editor {{
                    width: 100%;
                    height: 100%;
                    border: none;
                    outline: none;
                    font-size: 16px;
                }}
            </style>
        </head>
        <body>
            <div id="editor" contenteditable="true">{self.current_text}</div>
        </body>
        </html>
        """

    def get_text_from_webview(self):
        def callback(webview, result, user_data):
            try:
                js_result = webview.evaluate_javascript_finish(result)
                text = js_result.to_string() if js_result else ""
                self.current_text = text
                self.sentences = tokenize(text)
            except Exception as e:
                print(f"Error getting text: {e}")
        
        js = "document.getElementById('editor').innerText"
        self.webview.evaluate_javascript(js, -1, None, None, None, callback, None)

    def on_play_clicked(self, button):
        if not self.player.playing:
            self.start_playback()
        elif self.player.paused.is_set():
            self.player.paused.clear()
            print("[GUI] Resumed playback")

    def on_pause_clicked(self, button):
        if self.player.playing and not self.player.paused.is_set():
            self.player.paused.set()
            print("[GUI] Paused playback")

    def on_stop_clicked(self, button):
        print("[GUI] Stopping playback")
        self.player.stop.set()
        self.player.playing = False

    def on_prev_clicked(self, button):
        with self.player.sentence_lock:
            current = self.player.current_sentence
        with self.player.seek_lock:
            if current == 1:
                self.player.seek_to = 1
                print("[GUI] Restarting first sentence")
            else:
                new_idx = current - 1
                self.player.seek_to = new_idx
                print(f"[GUI] Seeking to sentence {new_idx} (was at {current})")

    def on_next_clicked(self, button):
        with self.player.sentence_lock:
            current = self.player.current_sentence
        with self.player.seek_lock:
            new_idx = min(len(self.sentences), current + 1)
            if new_idx <= len(self.sentences):
                self.player.seek_to = new_idx
                print(f"[GUI] Seeking to sentence {new_idx} (was at {current})")
            else:
                print(f"[GUI] Already at last sentence ({current})")

    def start_playback(self):
        # Get current text from webview
        self.get_text_from_webview()
        
        # Small delay to ensure text is updated
        GLib.timeout_add(100, self._start_playback_delayed)

    def _start_playback_delayed(self):
        if not self.sentences:
            print("[GUI] No sentences to play")
            return False
            
        print("[GUI] Starting playback")
        self.player.stop.clear()
        self.player.paused.clear()
        self.player.playing = True
        self.player.current_sentence = 1
        
        # Start producer process
        if self.producer_process and self.producer_process.is_alive():
            self.producer_process.terminate()
            self.producer_process.join()
            
        self.producer_process = Process(
            target=producer_proc, 
            args=(self.sentences, 1, outdir(), self.queue)
        )
        self.producer_process.start()
        
        # Start playback thread
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join()
            
        self.playback_thread = threading.Thread(
            target=self.player_thread_ordered,
            args=(self.queue, self.player, len(self.sentences))
        )
        self.playback_thread.start()
        
        return False

    def player_thread_ordered(self, qin: MPQ, ctrl: TTSPlayer, total: int):
        if not self.play_cmd:
            print("[ERROR] pacat/pw-cat not found")
            return
            
        print(f"[PLAY ] start: {' '.join(self.play_cmd)}")
        
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
            return subprocess.Popen(self.play_cmd, stdin=subprocess.PIPE)

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
                        print(f"[PLAY ] seeking to sentence {seek_target}")
                
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
        
        ctrl.playing = False
        print("[PLAY ] exit")

    def do_shutdown(self):
        # Cleanup
        self.player.stop.set()
        
        if self.producer_process and self.producer_process.is_alive():
            self.producer_process.terminate()
            self.producer_process.join()
            
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join()
            
        super().do_shutdown()

def main():
    app = TTSApplication()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()
