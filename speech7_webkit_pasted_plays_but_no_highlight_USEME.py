#!/usr/bin/env python3
import os, re, pathlib, threading, subprocess, sys, time, html
import numpy as np, soundfile as sf
from multiprocessing import Process, Queue as MPQ
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
from gi.repository import GLib, Gtk, Adw, WebKit, JavaScriptCore

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
SYNTH_START_SENTENCES = 2
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
    parts = re.split(r'(?<=[.!?;:—])\s*', t.strip())
    return [p.strip() for p in parts if p.strip()]

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
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.seek_to = None
        self.seek_lock = threading.Lock()
        self.current_sentence = 1
        self.sentence_lock = threading.Lock()
        self.playing = False

class TTSWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_default_size(800, 600)
        self.set_title("Text-to-Speech Player")
        self.ctrl = Controls()
        self.q = MPQ(maxsize=16)
        self.prod_process = None
        self.play_thread = None
        self.sentences = []
        self.total_sentences = 0
        self.generated_files = []
        self.current_text = DEFAULT_TEXT
        self.build_ui()
        self.load_text_to_webview(DEFAULT_TEXT)

    # ---------- WebView / JS ----------
    def build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header = Adw.HeaderBar()
        main_box.append(header)

        tb = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        header.pack_start(tb)

        self.play_pause_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Play")
        self.play_pause_btn.connect("clicked", self.on_play_pause)
        tb.append(self.play_pause_btn)

        self.stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop", sensitive=False)
        self.stop_btn.connect("clicked", self.on_stop)
        tb.append(self.stop_btn)

        self.prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic", tooltip_text="Previous")
        self.prev_btn.connect("clicked", self.on_prev)
        tb.append(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic", tooltip_text="Next")
        self.next_btn.connect("clicked", self.on_next)
        tb.append(self.next_btn)

        tb.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        tb.append(Gtk.Label(label="Buffer:", margin_start=6))
        self.synth_spin = Gtk.SpinButton()
        self.synth_spin.set_range(1, 10); self.synth_spin.set_increments(1, 1); self.synth_spin.set_value(SYNTH_START_SENTENCES)
        self.synth_spin.set_tooltip_text("Sentences to synthesize before starting")
        tb.append(self.synth_spin)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_margin_start(12)
        tb.append(self.status_label)

        scrolled = Gtk.ScrolledWindow(); scrolled.set_hexpand(True); scrolled.set_vexpand(True)
        main_box.append(scrolled)

        self.web_view = WebKit.WebView()
        scrolled.set_child(self.web_view)
        settings = self.web_view.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_write_console_messages_to_stdout(True)

    def load_text_to_webview(self, text):
        self.current_text = text
        sents = tokenize(text)
        html_doc = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{font-family:-webkit-system-font,system-ui,sans-serif;font-size:16px;line-height:1.6;padding:20px;margin:0;color:#333}
.sentence{display:inline}
.sentence.current{background:#87CEEB;font-weight:bold;border-radius:3px;padding:2px 4px}
.sentence.spoken{color:#888}
.sentence:hover{background:#f0f0f0;cursor:pointer}
</style>
<script>
function highlightSentence(i){const c=document.querySelector('.sentence.current');if(c)c.classList.remove('current');
  const s=document.getElementById('sentence_'+i);if(s){s.classList.add('current');s.scrollIntoView({behavior:'smooth',block:'center'});}}
function markSentenceSpoken(i){const s=document.getElementById('sentence_'+i);if(s){s.classList.add('spoken');s.classList.remove('current');}}
function clearHighlights(){document.querySelectorAll('.sentence').forEach(s=>s.classList.remove('current','spoken'));}
function getAllText(){return document.body.innerText||document.body.textContent||'';}
</script></head><body contenteditable="true" spellcheck="false">"""
        for i, s in enumerate(sents, 1):
            html_doc += f'<span class="sentence" id="sentence_{i}">{html.escape(s)}</span> '
        html_doc += "</body></html>"
        self.web_view.load_html(html_doc)

    # evaluate_javascript(script, length, world_name, source_uri, cancellable, callback, user_data)
    def js_fire_and_forget(self, code: str):
        try:
            self.web_view.evaluate_javascript(code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"[JS] call error: {e}")

    def highlight_sentence(self, idx): self.js_fire_and_forget(f"highlightSentence({int(idx)});")
    def mark_sentence_spoken(self, idx): self.js_fire_and_forget(f"markSentenceSpoken({int(idx)});")
    def clear_highlights(self): self.js_fire_and_forget("clearHighlights();")

    def get_text_from_webview(self):
        def on_text_received(web_view, result, user_data):
            try:
                jsres = web_view.evaluate_javascript_finish(result)
                # Robust across API shapes:
                val = jsres
                if hasattr(jsres, "get_js_value"):  # some builds return WebKit.JavascriptResult
                    val = jsres.get_js_value()
                text = ""
                if isinstance(val, JavaScriptCore.Value):
                    try:
                        # Prefer exact string if it is one
                        if hasattr(val, "is_string") and val.is_string():
                            text = val.to_string()
                        else:
                            # Fallback to JSON stringification if not a string
                            try:
                                text = val.to_json(0) or ""
                            except Exception:
                                text = str(val)
                    except Exception:
                        text = str(val)
                else:
                    text = str(val)

                cleaned = ' '.join((text or "").strip().split())
                if cleaned:
                    if cleaned != self.current_text:
                        self.current_text = cleaned
                        self.sentences = tokenize(self.current_text)
                        self.total_sentences = len(self.sentences)
                        print(f"[TEXT] Updated: {self.total_sentences} sentences")
                    else:
                        self.sentences = tokenize(self.current_text)
                        self.total_sentences = len(self.sentences)
                        print("[TEXT] Unchanged")
                else:
                    print("[TEXT] Empty; using stored text")
                    self.sentences = tokenize(self.current_text)
                    self.total_sentences = len(self.sentences)
            except Exception as e:
                print(f"[TEXT] JS finish error: {e}")
                self.sentences = tokenize(self.current_text)
                self.total_sentences = len(self.sentences)
            finally:
                if hasattr(self, '_text_update_callback'):
                    cb = self._text_update_callback
                    delattr(self, '_text_update_callback')
                    GLib.idle_add(cb)

        try:
            self.web_view.evaluate_javascript("getAllText();", -1, None, None, None, on_text_received, None)
        except Exception as e:
            print(f"[TEXT] eval_js err: {e}")
            self.sentences = tokenize(self.current_text)
            self.total_sentences = len(self.sentences)
            if hasattr(self, '_text_update_callback'):
                cb = self._text_update_callback
                delattr(self, '_text_update_callback')
                GLib.idle_add(cb)

    def get_text_async(self, callback=None):
        if callback:
            self._text_update_callback = callback
        self.get_text_from_webview()

    # ---------- Controls ----------
    def on_play_pause(self, _):
        if not self.ctrl.playing:
            self.clear_highlights()
            def start_playback():
                if not self.sentences:
                    self.status_label.set_label("No text to play"); return
                if hasattr(self, 'prod_process') and self.prod_process and self.prod_process.is_alive():
                    try: self.prod_process.terminate(); self.prod_process.join(timeout=1)
                    except: pass
                self.ctrl = Controls()
                self.q = MPQ(maxsize=16)
                GLib.idle_add(self.highlight_sentence, 1)
                d = outdir()
                self.prod_process = Process(target=producer_proc, args=(self.sentences, 1, d, self.q))
                self.prod_process.start()
                self.play_thread = threading.Thread(
                    target=self.player_thread_ordered, args=(self.q, self.ctrl, self.total_sentences), daemon=True)
                self.play_thread.start()
                self.ctrl.playing = True
                self.status_label.set_label(f"Playing... ({self.total_sentences} sentences)")
                self.play_pause_btn.set_icon_name("media-playback-pause-symbolic"); self.play_pause_btn.set_tooltip_text("Pause")
                self.stop_btn.set_sensitive(True); self.prev_btn.set_sensitive(True); self.next_btn.set_sensitive(True)
            self.get_text_async(start_playback)
        else:
            if self.ctrl.paused.is_set():
                self.ctrl.paused.clear(); self.status_label.set_label("Playing...")
                self.play_pause_btn.set_icon_name("media-playback-pause-symbolic"); self.play_pause_btn.set_tooltip_text("Pause")
            else:
                self.ctrl.paused.set(); self.status_label.set_label("Paused")
                self.play_pause_btn.set_icon_name("media-playback-start-symbolic"); self.play_pause_btn.set_tooltip_text("Resume")

    def on_stop(self, _):
        if self.ctrl.playing:
            self.ctrl.stop.set()
            try: self.q.put((None, None, None))
            except Exception: pass
            self.cleanup_playback(); self.status_label.set_label("Stopped"); self.clear_highlights()

    def on_prev(self, _):
        if self.ctrl.playing:
            with self.ctrl.sentence_lock: current = self.ctrl.current_sentence
            with self.ctrl.seek_lock:
                new_idx = 1 if current == 1 else current - 1
                self.ctrl.seek_to = new_idx
                self.status_label.set_label(f"Seeking to sentence {new_idx}")
                GLib.idle_add(self.highlight_sentence, new_idx)

    def on_next(self, _):
        if self.ctrl.playing:
            with self.ctrl.sentence_lock: current = self.ctrl.current_sentence
            with self.ctrl.seek_lock:
                new_idx = min(self.total_sentences, current + 1)
                self.ctrl.seek_to = new_idx
                self.status_label.set_label(f"Seeking to sentence {new_idx}")
                GLib.idle_add(self.highlight_sentence, new_idx)

    def cleanup_playback(self):
        self.ctrl.playing = False; self.ctrl.paused.clear()
        self.play_pause_btn.set_icon_name("media-playback-start-symbolic"); self.play_pause_btn.set_tooltip_text("Play")
        self.stop_btn.set_sensitive(False); self.prev_btn.set_sensitive(False); self.next_btn.set_sensitive(False)

    # ---------- Player ----------
    def player_thread_ordered(self, qin: MPQ, ctrl: Controls, total: int):
        cmd = choose_play_cmd()
        if not cmd:
            print("[ERROR] pacat/pw-cat not found"); self.status_label.set_label("Error: audio player not found"); return
        print(f"[PLAY ] start: {' '.join(cmd)}")
        frame_bytes = 2; step = CHUNK_FRAMES * frame_bytes
        buf = {}; eof = False; current_playing = 1
        GLib.idle_add(self.highlight_sentence, current_playing)
        synth_start_count = int(self.synth_spin.get_value()); ready = 0

        while not ctrl.stop.is_set() and ready < synth_start_count and not eof:
            idx, pcm, path = qin.get()
            if idx is None: eof = True; break
            buf[idx] = (pcm, path); ready += 1
            if path and path not in self.generated_files: self.generated_files.append(path)
            print(f"[BUFFER] Sentence {idx} ready ({ready}/{synth_start_count})")
        print(f"[BUFFER] Starting playback with {ready} sentences ready")

        def restart_player(): return subprocess.Popen(cmd, stdin=subprocess.PIPE)

        def play_pcm_chunk(p, pcm, idx):
            off = 0; n = len(pcm); start = time.time()
            while off < n and not ctrl.stop.is_set():
                with ctrl.seek_lock:
                    if ctrl.seek_to is not None: return False
                if ctrl.paused.is_set(): time.sleep(0.01); continue
                chunk = pcm[off:off+step]
                try: p.stdin.write(chunk); p.stdin.flush()
                except Exception as e: print(f"[PLAY ] write err#{idx}: {e}"); return False
                off += len(chunk)
                expected = start + (off / (SR * frame_bytes))
                sleep = expected - time.time()
                if sleep > 0: time.sleep(sleep)
            if off >= n:
                total_dur = n / (SR * frame_bytes)
                rem = total_dur - (time.time() - start)
                if rem > 0: time.sleep(rem)
            return True

        p = restart_player()
        try:
            while not ctrl.stop.is_set():
                with ctrl.seek_lock:
                    if ctrl.seek_to is not None:
                        seek_target = ctrl.seek_to; ctrl.seek_to = None; current_playing = seek_target
                        GLib.idle_add(self.highlight_sentence, current_playing)
                        try:
                            if p.stdin: p.stdin.close()
                            p.terminate(); p.wait(timeout=1)
                        except: pass
                        p = restart_player()
                        GLib.idle_add(self.status_label.set_label, f"Playing sentence {seek_target}")

                while current_playing not in buf and not eof and not ctrl.stop.is_set():
                    idx, pcm, path = qin.get()
                    if idx is None: eof = True; break
                    buf[idx] = (pcm, path)
                    if path and path not in self.generated_files: self.generated_files.append(path)

                if current_playing in buf and not ctrl.stop.is_set():
                    pcm, _ = buf[current_playing]
                    print(f"[PLAY ] >>#{current_playing}")
                    with ctrl.sentence_lock: ctrl.current_sentence = current_playing
                    if play_pcm_chunk(p, pcm, current_playing):
                        print(f"[PLAY ] done #{current_playing}")
                        time.sleep(0.1)
                        GLib.idle_add(self.mark_sentence_spoken, current_playing)
                        current_playing += 1
                        if current_playing <= total:
                            def delayed(): time.sleep(0.05); GLib.idle_add(self.highlight_sentence, current_playing)
                            threading.Thread(target=delayed, daemon=True).start()
                        else:
                            GLib.idle_add(self.clear_highlights)
                        if current_playing > total and eof: break
                    else:
                        continue

                if not eof and len(buf) < total:
                    try:
                        idx, pcm, path = qin.get_nowait()
                        if idx is None: eof = True
                        else:
                            buf[idx] = (pcm, path)
                            if path and path not in self.generated_files: self.generated_files.append(path)
                    except:
                        time.sleep(0.01)
        finally:
            try:
                if p.stdin: p.stdin.close()
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

