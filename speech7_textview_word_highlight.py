#!/usr/bin/env python3
import os, re, pathlib, threading, subprocess, sys, time
import numpy as np, soundfile as sf
from multiprocessing import Process, Queue as MPQ
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gtk, Gio, Adw, Pango

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
SYNTH_START_SENTENCES = 2  # Number of sentences to synthesize before starting playback
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
    # Split on multiple sentence-ending punctuation marks with more flexible spacing
    # This handles cases where there might be no space or minimal space after punctuation
    sentences = re.split(r'(?<=[.!?;:—])\s*', t.strip())
    # Filter out empty strings and strip whitespace
    return [p.strip() for p in sentences if p.strip()]

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
        self.sentence_positions = []  # Store start/end positions of each sentence
        self.current_highlight_tag = None
        self.current_word_highlight_tag = None
        self.generated_files = []  # Track generated files to not delete them
        self.word_timings = {}  # Store word timing information for each sentence
        
        # Create UI
        self.build_ui()
        
        # Load default text
        self.text_buffer.set_text(DEFAULT_TEXT, -1)
        
        # Setup text buffer tags for highlighting
        self.setup_text_tags()
        
    def setup_text_tags(self):
        # Create tag for highlighting current sentence
        self.highlight_tag = self.text_buffer.create_tag(
            "highlight", 
            background="lightblue", 
            weight=Pango.Weight.BOLD
        )
        
        # Create tag for previously spoken sentences
        self.spoken_tag = self.text_buffer.create_tag(
            "spoken", 
            foreground="gray"
        )
        
        # Create tag for current word highlighting
        self.word_highlight_tag = self.text_buffer.create_tag(
            "word_highlight",
            background="yellow",
            weight=Pango.Weight.BOLD
        )
        
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
        
        # Play/Pause button (combined)
        self.play_pause_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Play")
        self.play_pause_btn.connect("clicked", self.on_play_pause)
        self.toolbar.append(self.play_pause_btn)
        
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
        
        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar.append(separator)
        
        # Synth buffer setting
        synth_label = Gtk.Label(label="Buffer:")
        synth_label.set_margin_start(6)
        self.toolbar.append(synth_label)
        
        self.synth_spin = Gtk.SpinButton()
        self.synth_spin.set_range(1, 10)
        self.synth_spin.set_increments(1, 1)
        self.synth_spin.set_value(SYNTH_START_SENTENCES)
        self.synth_spin.set_tooltip_text("Number of sentences to synthesize before starting playback")
        self.toolbar.append(self.synth_spin)
        
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
        
        # Calculate sentence positions in the text buffer
        self.sentence_positions = []
        current_pos = 0
        
        for sentence in self.sentences:
            start_idx = text.find(sentence, current_pos)
            if start_idx == -1:
                # Fallback if we can't find the exact sentence
                self.sentence_positions.append((None, None))
                continue
                
            end_idx = start_idx + len(sentence)
            self.sentence_positions.append((start_idx, end_idx))
            current_pos = end_idx
            
        return text
    
    def highlight_sentence(self, sentence_idx):
        # Remove previous sentence highlighting
        if self.current_highlight_tag:
            start_iter, end_iter = self.current_highlight_tag
            self.text_buffer.remove_tag(self.highlight_tag, start_iter, end_iter)
        
        # Apply highlighting to current sentence
        if 0 <= sentence_idx - 1 < len(self.sentence_positions):
            start_pos, end_pos = self.sentence_positions[sentence_idx - 1]
            if start_pos is not None and end_pos is not None:
                start_iter = self.text_buffer.get_iter_at_offset(start_pos)
                end_iter = self.text_buffer.get_iter_at_offset(end_pos)
                self.text_buffer.apply_tag(self.highlight_tag, start_iter, end_iter)
                self.current_highlight_tag = (start_iter, end_iter)
                
                # Scroll to make the highlighted text visible
                self.text_view.scroll_to_iter(start_iter, 0.1, False, 0, 0)
    
    def highlight_word(self, sentence_idx, word_start_pos, word_end_pos):
        # Remove previous word highlighting
        if self.current_word_highlight_tag:
            start_iter, end_iter = self.current_word_highlight_tag
            self.text_buffer.remove_tag(self.word_highlight_tag, start_iter, end_iter)
        
        # Apply highlighting to current word
        if word_start_pos is not None and word_end_pos is not None:
            start_iter = self.text_buffer.get_iter_at_offset(word_start_pos)
            end_iter = self.text_buffer.get_iter_at_offset(word_end_pos)
            self.text_buffer.apply_tag(self.word_highlight_tag, start_iter, end_iter)
            self.current_word_highlight_tag = (start_iter, end_iter)
    
    def clear_word_highlight(self):
        # Remove word highlighting only
        if self.current_word_highlight_tag:
            start_iter, end_iter = self.current_word_highlight_tag
            self.text_buffer.remove_tag(self.word_highlight_tag, start_iter, end_iter)
            self.current_word_highlight_tag = None
    
    def mark_sentence_spoken(self, sentence_idx):
        # Mark sentence as spoken (grayed out)
        if 0 <= sentence_idx - 1 < len(self.sentence_positions):
            start_pos, end_pos = self.sentence_positions[sentence_idx - 1]
            if start_pos is not None and end_pos is not None:
                start_iter = self.text_buffer.get_iter_at_offset(start_pos)
                end_iter = self.text_buffer.get_iter_at_offset(end_pos)
                self.text_buffer.apply_tag(self.spoken_tag, start_iter, end_iter)
        
    def clear_highlights(self):
        # Remove all highlighting
        start_iter = self.text_buffer.get_start_iter()
        end_iter = self.text_buffer.get_end_iter()
        self.text_buffer.remove_tag(self.highlight_tag, start_iter, end_iter)
        self.text_buffer.remove_tag(self.spoken_tag, start_iter, end_iter)
        self.text_buffer.remove_tag(self.word_highlight_tag, start_iter, end_iter)
        self.current_highlight_tag = None
        self.current_word_highlight_tag = None
        
    def on_play_pause(self, widget):
        if not self.ctrl.playing:
            # Start playing
            self.clear_highlights()
            self.get_text()  # Get current text from editor
            
            if not self.sentences:
                self.status_label.set_label("No text to play")
                return
                
            self.ctrl = Controls()
            self.q = MPQ(maxsize=16)
            
            # Highlight the first sentence immediately
            GLib.idle_add(self.highlight_sentence, 1)
            
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
            self.play_pause_btn.set_icon_name("media-playback-pause-symbolic")
            self.play_pause_btn.set_tooltip_text("Pause")
            self.stop_btn.set_sensitive(True)
            self.prev_btn.set_sensitive(True)
            self.next_btn.set_sensitive(True)
        else:
            # Toggle pause/resume
            if self.ctrl.paused.is_set():
                self.ctrl.paused.clear()
                self.status_label.set_label("Playing...")
                self.play_pause_btn.set_icon_name("media-playback-pause-symbolic")
                self.play_pause_btn.set_tooltip_text("Pause")
            else:
                self.ctrl.paused.set()
                self.status_label.set_label("Paused")
                self.play_pause_btn.set_icon_name("media-playback-start-symbolic")
                self.play_pause_btn.set_tooltip_text("Resume")
    
    def on_stop(self, widget):
        if self.ctrl.playing:
            self.ctrl.stop.set()
            try:
                self.q.put((None, None, None))
            except Exception:
                pass
                
            self.cleanup_playback()
            self.status_label.set_label("Stopped")
            
            # Clear highlights when stopped
            self.clear_highlights()
    
    def on_prev(self, widget):
        if self.ctrl.playing:
            with self.ctrl.sentence_lock:
                current = self.ctrl.current_sentence
            with self.ctrl.seek_lock:
                if current == 1:
                    # Restart first sentence
                    self.ctrl.seek_to = 1
                    self.status_label.set_label("Restarting sentence 1")
                    GLib.idle_add(self.highlight_sentence, 1)
                else:
                    new_idx = current - 1
                    self.ctrl.seek_to = new_idx
                    self.status_label.set_label(f"Seeking to sentence {new_idx}")
                    GLib.idle_add(self.highlight_sentence, new_idx)
    
    def on_next(self, widget):
        if self.ctrl.playing:
            with self.ctrl.sentence_lock:
                current = self.ctrl.current_sentence
            with self.ctrl.seek_lock:
                new_idx = min(self.total_sentences, current + 1)
                if new_idx <= self.total_sentences:
                    self.ctrl.seek_to = new_idx
                    self.status_label.set_label(f"Seeking to sentence {new_idx}")
                    GLib.idle_add(self.highlight_sentence, new_idx)
                else:
                    self.status_label.set_label(f"Already at last sentence ({current})")
    
    def cleanup_playback(self):
        self.ctrl.playing = False
        self.ctrl.paused.clear()
        
        # Don't terminate the producer process - let it finish and keep the files
        # The files will be preserved until the next playback starts
        
        # Reset button states
        self.play_pause_btn.set_icon_name("media-playback-start-symbolic")
        self.play_pause_btn.set_tooltip_text("Play")
        self.stop_btn.set_sensitive(False)
        self.prev_btn.set_sensitive(False)
        self.next_btn.set_sensitive(False)
    
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
        
        # Highlight the first sentence before starting playback
        GLib.idle_add(self.highlight_sentence, current_playing)
        
        # Collect initial buffer - wait for SYNTH_START_SENTENCES instead of PREROLL
        synth_start_count = int(self.synth_spin.get_value())  # Get current setting from UI
        sentences_ready = 0
        while not ctrl.stop.is_set() and sentences_ready < synth_start_count and not eof:
            idx, pcm, path = qin.get()
            if idx is None:
                eof = True
                break
            buf[idx] = (pcm, path)
            sentences_ready += 1
            # Track generated files but don't delete them
            if path and path not in self.generated_files:
                self.generated_files.append(path)
            print(f"[BUFFER] Sentence {idx} ready ({sentences_ready}/{synth_start_count})")
        
        print(f"[BUFFER] Starting playback with {sentences_ready} sentences ready")
        
        def restart_player():
            return subprocess.Popen(cmd, stdin=subprocess.PIPE)

        def play_pcm_chunk(p, pcm, idx):
            """Play PCM data in chunks, checking for interruptions and highlighting words"""
            off = 0
            n = len(pcm)
            start_time = time.time()
            
            # Get sentence text and calculate word positions
            sentence_text = ""
            if 0 <= idx - 1 < len(self.sentences):
                sentence_text = self.sentences[idx - 1]
            
            # Split sentence into words
            words = sentence_text.split()
            word_positions = []
            current_pos = 0
            
            # Calculate word positions in the sentence
            for word in words:
                word_start = sentence_text.find(word, current_pos)
                if word_start != -1:
                    word_positions.append((word_start, word_start + len(word)))
                    current_pos = word_start + len(word)
                else:
                    word_positions.append((None, None))
            
            # Get sentence start position in the full text
            sentence_start_pos = None
            if 0 <= idx - 1 < len(self.sentence_positions):
                sentence_start_pos = self.sentence_positions[idx - 1][0]
            
            # Calculate total audio duration for this sentence
            total_duration = n / (SR * frame_bytes)
            
            # Calculate approximate time per word
            word_duration = total_duration / max(len(words), 1) if words else 0
            
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
                
                # Calculate current playback position
                elapsed_time = (off / (SR * frame_bytes))
                
                # Determine which word should be highlighted based on elapsed time
                if words and sentence_start_pos is not None and word_duration > 0:
                    word_index = min(int(elapsed_time / word_duration), len(words) - 1)
                    if 0 <= word_index < len(word_positions):
                        word_start, word_end = word_positions[word_index]
                        if word_start is not None and word_end is not None:
                            # Calculate absolute positions in the text buffer
                            abs_word_start = sentence_start_pos + word_start
                            abs_word_end = sentence_start_pos + word_end
                            # Highlight the current word
                            GLib.idle_add(self.highlight_word, idx, abs_word_start, abs_word_end)
                
                # More accurate timing - wait for the actual audio duration to pass
                expected_time = start_time + (off / (SR * frame_bytes))
                current_time = time.time()
                sleep_time = expected_time - current_time
                if sleep_time > 0:
                    time.sleep(sleep_time)
            
            # Wait a bit more to ensure the audio buffer is fully played
            if off >= n:
                # Calculate total audio duration and wait for it
                total_duration = n / (SR * frame_bytes)
                elapsed = time.time() - start_time
                remaining = total_duration - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            
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
                        
                        # Update highlighting for the new sentence
                        GLib.idle_add(self.highlight_sentence, current_playing)
                        # Clear word highlighting when seeking
                        GLib.idle_add(self.clear_word_highlight)
                        
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
                    idx, pcm, path = qin.get()
                    if idx is None:
                        eof = True
                        break
                    buf[idx] = (pcm, path)
                    # Track generated files but don't delete them
                    if path and path not in self.generated_files:
                        self.generated_files.append(path)
                
                # Play current sentence if available
                if current_playing in buf and not ctrl.stop.is_set():
                    pcm, path = buf[current_playing]
                    print(f"[PLAY ] >>#{current_playing}")
                    
                    # Update current sentence tracker
                    with ctrl.sentence_lock:
                        ctrl.current_sentence = current_playing
                    
                    # Play the sentence completely, then update highlighting
                    if play_pcm_chunk(p, pcm, current_playing):
                        print(f"[PLAY ] done #{current_playing}")
                        
                        # Add a small delay to ensure audio output is complete before updating UI
                        time.sleep(0.1)
                        
                        # Clear word highlighting for this sentence
                        GLib.idle_add(self.clear_word_highlight)
                        
                        # Mark the just-finished sentence as spoken
                        GLib.idle_add(self.mark_sentence_spoken, current_playing)
                        
                        current_playing += 1
                        
                        # Highlight the next sentence after current one finishes
                        if current_playing <= total:
                            # Small delay before highlighting next sentence
                            def delayed_highlight():
                                time.sleep(0.05)
                                GLib.idle_add(self.highlight_sentence, current_playing)
                            threading.Thread(target=delayed_highlight, daemon=True).start()
                        else:
                            # Clear highlight if we've finished all sentences
                            GLib.idle_add(self.clear_highlights)
                        
                        # Check if we've finished all sentences
                        if current_playing > total and eof:
                            break
                    else:
                        # Playback was interrupted, continue with seek handling
                        continue
                
                # Keep collecting new sentences
                if not eof and len(buf) < total:
                    try:
                        idx, pcm, path = qin.get_nowait()
                        if idx is None:
                            eof = True
                        else:
                            buf[idx] = (pcm, path)
                            # Track generated files but don't delete them
                            if path and path not in self.generated_files:
                                self.generated_files.append(path)
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
            
            # Don't delete files - they will be preserved until next playback
        
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
