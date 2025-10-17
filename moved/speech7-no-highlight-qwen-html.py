#!/usr/bin/env python3
import os, re, pathlib, threading, subprocess, sys, time, json
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
DEFAULT_HTML = '''<h1 xmlns="http://www.w3.org/1999/xhtml" class="h1"><b>How Goal Setting</b> <i>Actually</i> <b>Works: The Mystery ‘Secret’ Revealed</b> </h1>
<div xmlns="http://www.w3.org/1999/xhtml" class="tx">You only see, experience, and get what you look for. If you don’t know what to look for, you certainly won’t get it. By our very nature, we are goal-seeking creatures. Our brain is always trying to align our outer world with what we’re seeing and expecting in our inner world. So, when you instruct your brain to look for the things you want, you will begin to see them. In fact, the object of your desire has probably always existed around you, but your mind and eyes weren’t open to “seeing” it.</div>
<div xmlns="http://www.w3.org/1999/xhtml" class="tx">In reality, this is how the <i>Law of Attraction</i> really works. It is not the mysterious, esoteric voodoo it sometimes sounds like. It’s far simpler and more practical than that.</div>
<div xmlns="http://www.w3.org/1999/xhtml" class="tx">We are bombarded with billions of sensory (visual, audio, physical) bites of information each day. To keep ourselves from going insane, we ignore 99.9 percent of them, only really seeing, hearing, or experiencing those upon which our mind focuses. This is why, when you “think” something, it appears that you are miraculously drawing it into your life. In reality, you’re now just seeing what was already there. You are truly “attracting” it into your life. It wasn’t there before or accessible to you until your thoughts focused and directed your mind to see it.</div>'''
LANG = "en-us"
VOICE = "af_sarah"
SPEED = 1.0
SR = 24000
CHUNK_FRAMES = 2400
SYNTH_START_SENTENCES = 2
DECIMAL_WORD = "point"
# ----------------

try:
    from kokoro_onnx import Kokoro
except ImportError:
    print("Kokoro not available, running in text-only mode")
    Kokoro = None

def outdir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)
    return d

# ---- AUDIO-ONLY preprocessing ----
def expand_abbreviations(s: str) -> str:
    s = re.sub(r'\be\.g\.,', 'for example,', s, flags=re.IGNORECASE)
    s = re.sub(r'\be\.g\.',  'for example',  s, flags=re.IGNORECASE)
    s = re.sub(r'\bi\.e\.,', 'that is,',    s, flags=re.IGNORECASE)
    s = re.sub(r'\bi\.e\.',  'that is',     s, flags=re.IGNORECASE)
    s = re.sub(r'\bvs\.',    'versus',      s, flags=re.IGNORECASE)
    s = re.sub(r'\betc\.',   'et cetera',   s, flags=re.IGNORECASE)
    return s

def speak_decimal_points(s: str) -> str:
    return re.sub(r'(?<=\d)\.(?=\d)', f' {DECIMAL_WORD} ', s)

def preprocess_text(s: str) -> str:
    return speak_decimal_points(expand_abbreviations(s))

# ---- tokenization (UI shows RAW text; audio uses preprocessed per sentence) ----
def tokenize_ui(t: str):
    return [p.strip() for p in re.split(r'(?<!\d)[.!?](?!\d)\s+', (t or "").strip()) if p.strip()]

def f32_to_s16le(x):
    return (np.clip(x, -1, 1) * 32767.0).astype('<i2').tobytes()

def synth_one(kok, idx, sent, d):
    print(f"[SYNTH]{idx}: {sent}")
    wav, sr = kok.create(sent, voice=VOICE, speed=SPEED, lang=LANG)
    if sr != SR: print(f"[WARN] sr={sr}!=SR={SR}")
    path = os.path.join(d, f"kokoro_sent_{idx:02d}.wav")
    sf.write(path, wav, sr)
    pcm = f32_to_s16le(wav)
    print(f"[FILE ]{idx}: {path} bytes={len(pcm)}")
    return pcm, path

def producer_proc(sents, start_idx, d, q: MPQ):
    if Kokoro is None:
        print("Kokoro not available, cannot synthesize")
        q.put((None, None, None)); return
    kok = Kokoro(MODEL, VOICES)
    try:
        for i in range(start_idx, len(sents) + 1):
            try:
                clean_sent = preprocess_text(sents[i - 1])  # AUDIO-ONLY preprocessing
                q.put((i,) + synth_one(kok, i, clean_sent, d))
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
        self.set_default_size(900, 700)
        self.set_title("Text-to-Speech Player")
        self.ctrl = Controls()
        self.q = MPQ(maxsize=16)
        self.prod_process = None
        self.play_thread = None
        self.sentences = []
        self.total_sentences = 0
        self.generated_files = []
        self.current_html = DEFAULT_HTML
        self.build_ui()
        self.load_text_to_webview(DEFAULT_HTML)

    # ---------- WebView / JS ----------
    def build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header = Adw.HeaderBar(); main_box.append(header)
        tb = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        header.pack_start(tb)

        self.play_pause_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Play")
        self.play_pause_btn.connect("clicked", self.on_play_pause); tb.append(self.play_pause_btn)

        self.stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop", sensitive=False)
        self.stop_btn.connect("clicked", self.on_stop); tb.append(self.stop_btn)

        self.prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic", tooltip_text="Previous", sensitive=False)
        self.prev_btn.connect("clicked", self.on_prev); tb.append(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic", tooltip_text="Next", sensitive=False)
        self.next_btn.connect("clicked", self.on_next); tb.append(self.next_btn)

        tb.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        tb.append(Gtk.Label(label="Buffer:", margin_start=6))
        self.synth_spin = Gtk.SpinButton()
        self.synth_spin.set_range(1, 10); self.synth_spin.set_increments(1, 1); self.synth_spin.set_value(SYNTH_START_SENTENCES)
        self.synth_spin.set_tooltip_text("Sentences to synthesize before starting"); tb.append(self.synth_spin)

        self.status_label = Gtk.Label(label="Ready"); self.status_label.set_halign(Gtk.Align.START); self.status_label.set_margin_start(12)
        tb.append(self.status_label)

        scrolled = Gtk.ScrolledWindow(); scrolled.set_hexpand(True); scrolled.set_vexpand(True); main_box.append(scrolled)
        self.web_view = WebKit.WebView(); scrolled.set_child(self.web_view)
        settings = self.web_view.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_write_console_messages_to_stdout(True)

    def load_text_to_webview(self, html_fragment):
        """Render EXACT pasted HTML; preserve formatting; include highlighter JS."""
        self.current_html = html_fragment

        html_head = '''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="color-scheme" content="light dark">
  <style>
    html,body { margin:0; padding:20px; font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif; }
    .word.sent-current { background:rgba(0,0,0,.10); border-radius:3px; }
    .word.word-current { background:rgba(0,0,0,.18); border-radius:3px; }
  </style>
  <script>
    function prepareHighlighting(){
      if (document.body.__prepared) return;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: n => n.nodeValue.trim().length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
      });
      let s = 1, w = 0;
      const reSplit = /((?<!\\\\d)[.!?](?!\\\\d)|\\\\s+)/g;
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const textNode of nodes){
        const txt = textNode.nodeValue;
        const parts = txt.split(reSplit).filter(x => x !== "");
        const frag = document.createDocumentFragment();
        for (const part of parts){
          if (/^\\\\s+$/.test(part)){
            frag.appendChild(document.createTextNode(part));
            continue;
          }
          if (/^(?<!\\\\d)[.!?](?!\\\\d)$/.test(part)){
            frag.appendChild(document.createTextNode(part));
            s += 1; w = 0;
            continue;
          }
          const span = document.createElement('span');
          span.className = 'word';
          span.dataset.sent = String(s);
          span.dataset.word = String(w);
          span.textContent = part;
          frag.appendChild(span);
          frag.appendChild(document.createTextNode(' '));
          w += 1;
        }
        textNode.parentNode.replaceChild(frag, textNode);
      }
      document.body.__prepared = true;
    }
    function clearHighlights(){
      document.querySelectorAll('.word.sent-current').forEach(x=>x.classList.remove('sent-current'));
      document.querySelectorAll('.word.word-current').forEach(x=>x.classList.remove('word-current'));
    }
    function highlightSentence(i){
      clearHighlights();
      document.querySelectorAll('.word[data-sent="'+i+'"]').forEach(x=>x.classList.add('sent-current'));
      const first = document.querySelector('.word[data-sent="'+i+'"]');
      if (first) first.scrollIntoView({behavior:'smooth', block:'center'});
    }
    function highlightWord(si, wi){
      document.querySelectorAll('.word.word-current').forEach(x=>x.classList.remove('word-current'));
      const w = document.querySelector('.word[data-sent="'+si+'"][data-word="'+wi+'"]');
      if (w){ w.classList.add('word-current'); w.scrollIntoView({behavior:'smooth', block:'center'}); }
    }
    function getAllText(){ return document.body.innerText || ''; }
    function getWordCountInSentence(sentenceIndex){
      return document.querySelectorAll('.word[data-sent="'+sentenceIndex+'"]').length;
    }
  </script>
</head>
'''
        final_html = html_head + '<body contenteditable="true" spellcheck="false">\n' + html_fragment + '\n</body></html>'
        self.web_view.load_html(final_html)

    def js_fire_and_forget(self, code: str):
        try:
            self.web_view.evaluate_javascript(code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"[JS] call error: {e}")

    def highlight_sentence(self, idx):
        self.js_fire_and_forget(f"highlightSentence({int(idx)});")

    def highlight_word(self, sent_idx, word_idx):
        self.js_fire_and_forget(f"highlightWord({int(sent_idx)}, {int(word_idx)});")

    def clear_highlights(self):
        self.js_fire_and_forget("clearHighlights();")

    def get_text_from_webview(self):
        def on_text_received(web_view, result, user_data):
            try:
                jsres = web_view.evaluate_javascript_finish(result)
                val = jsres.get_js_value() if hasattr(jsres, "get_js_value") else jsres
                text = ""
                if isinstance(val, JavaScriptCore.Value):
                    try:
                        if hasattr(val, "is_string") and val.is_string(): text = val.to_string()
                        else:
                            try: text = val.to_json(0) or ""
                            except Exception: text = str(val)
                    except Exception:
                        text = str(val)
                else:
                    text = str(val)
                cleaned = ' '.join((text or "").strip().split())
                self.sentences = tokenize_ui(cleaned)  # RAW innerText for sentence list
                self.total_sentences = len(self.sentences)
                print(f"[TEXT] Using {self.total_sentences} sentences")
            except Exception as e:
                print(f"[TEXT] JS finish error: {e}")
                self.sentences = []; self.total_sentences = 0
            finally:
                if hasattr(self, '_text_update_callback'):
                    cb = self._text_update_callback
                    delattr(self, '_text_update_callback')
                    GLib.idle_add(cb)
        try:
            self.web_view.evaluate_javascript("getAllText();", -1, None, None, None, on_text_received, None)
        except Exception as e:
            print(f"[TEXT] eval_js err: {e}")
            self.sentences = []; self.total_sentences = 0
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
            def start_playback():
                if not self.sentences:
                    self.status_label.set_label("No text to play"); return
                # Prepare in-place wrappers once; preserves formatting.
                self.js_fire_and_forget("prepareHighlighting();")
                GLib.idle_add(self.highlight_sentence, 1)

                if hasattr(self, 'prod_process') and self.prod_process and self.prod_process.is_alive():
                    try: self.prod_process.terminate(); self.prod_process.join(timeout=1)
                    except: pass
                self.ctrl = Controls(); self.q = MPQ(maxsize=16)

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
            self.cleanup_playback(); self.status_label.set_label("Stopped")
            self.clear_highlights()

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
        synth_start_count = int(self.synth_spin.get_value()); ready = 0

        while not ctrl.stop.is_set() and ready < synth_start_count and not eof:
            idx, pcm, path = qin.get()
            if idx is None: eof = True; break
            buf[idx] = (pcm, path); ready += 1
            print(f"[BUFFER] Sentence {idx} ready ({ready}/{synth_start_count})")
        print(f"[BUFFER] Starting playback with {ready} sentences ready")

        def restart_player(): return subprocess.Popen(cmd, stdin=subprocess.PIPE)

        p = restart_player()
        try:
            while not ctrl.stop.is_set():
                with ctrl.seek_lock:
                    if ctrl.seek_to is not None:
                        seek_target = ctrl.seek_to; ctrl.seek_to = None; current_playing = seek_target
                        try:
                            if p.stdin: p.stdin.close()
                            p.terminate(); p.wait(timeout=1)
                        except: pass
                        p = restart_player()
                        GLib.idle_add(self.status_label.set_label, f"Playing sentence {seek_target}")
                        GLib.idle_add(self.highlight_sentence, seek_target)

                while current_playing not in buf and not eof and not ctrl.stop.is_set():
                    try:
                        idx, pcm, path = qin.get(timeout=0.05)
                    except Exception:
                        continue
                    if idx is None: eof = True; break
                    buf[idx] = (pcm, path)

                if current_playing in buf and not ctrl.stop.is_set():
                    pcm, _ = buf[current_playing]
                    print(f"[PLAY ] >>#{current_playing}")
                    with ctrl.sentence_lock: ctrl.current_sentence = current_playing

                    # Get word count for current sentence
                    word_count = 0
                    word_count_lock = threading.Lock()
                    word_count_received = threading.Event()
                    
                    def on_word_count_received(web_view, result, user_data):
                        nonlocal word_count
                        try:
                            jsres = web_view.evaluate_javascript_finish(result)
                            val = jsres.get_js_value() if hasattr(jsres, "get_js_value") else jsres
                            with word_count_lock:
                                word_count = int(val.to_string() if hasattr(val, "to_string") else str(val))
                            word_count_received.set()
                        except Exception as e:
                            print(f"[WORD] Error getting word count: {e}")
                            word_count_received.set()
                    
                    try:
                        self.web_view.evaluate_javascript(
                            f"getWordCountInSentence({current_playing});", -1, None, None, None, 
                            on_word_count_received, None)
                        # Wait for result with timeout
                        if word_count_received.wait(timeout=0.1):
                            with word_count_lock:
                                wc = word_count
                        else:
                            wc = 0
                    except Exception as e:
                        print(f"[WORD] JS error: {e}")
                        wc = 0

                    print(f"[DEBUG] Sentence {current_playing} has {wc} words")

                    # Highlight sentence first
                    GLib.idle_add(self.highlight_sentence, current_playing)
                    
                    # Stream PCM with word-by-word highlighting
                    off = 0; n = len(pcm); t0 = time.time()
                    bytes_per_word = n // max(wc, 1) if wc > 0 else n
                    
                    # Highlight first word
                    if wc > 0:
                        GLib.idle_add(self.highlight_word, current_playing, 0)
                    
                    word_positions = [i * bytes_per_word for i in range(wc)] if wc > 0 else []
                    current_word = 0
                    
                    while off < n and not ctrl.stop.is_set():
                        with ctrl.seek_lock:
                            if ctrl.seek_to is not None: break
                        if ctrl.paused.is_set(): time.sleep(0.01); continue
                        chunk = pcm[off:off+step]
                        try: p.stdin.write(chunk); p.stdin.flush()
                        except Exception as e: print(f"[PLAY ] write err#{current_playing}: {e}"); break
                        off += len(chunk)
                        
                        # Update word highlighting based on position
                        if wc > 0 and bytes_per_word > 0:
                            # Find which word we're currently at
                            new_word = min(off // bytes_per_word, wc - 1)
                            if new_word != current_word:
                                current_word = new_word
                                GLib.idle_add(self.highlight_word, current_playing, current_word)
                        
                        expected = t0 + (off / (SR * frame_bytes))
                        sleep = expected - time.time()
                        if sleep > 0: time.sleep(sleep)

                    # next sentence
                    current_playing += 1
                    if current_playing <= total:
                        GLib.idle_add(self.highlight_sentence, current_playing)
                    if current_playing > total and eof: break

                if not eof and len(buf) < total:
                    try:
                        idx, pcm, path = qin.get_nowait()
                        if idx is None: eof = True
                        else: buf[idx] = (pcm, path)
                    except: time.sleep(0.01)
        finally:
            try:
                if p.stdin: p.stdin.close()
                p.wait(timeout=3)
            except Exception:
                pass
            GLib.idle_add(self.cleanup_playback)
            GLib.idle_add(self.clear_highlights)
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
