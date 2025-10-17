#!/usr/bin/env python3
import os, re, pathlib, threading, subprocess, sys, time, html, json
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
        self.highlight_sentence_enabled = True
        self.highlight_word_enabled = True
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
        
        # Highlight checkboxes
        self.sentence_highlight_check = Gtk.CheckButton(label="Highlight Line", active=True)
        self.sentence_highlight_check.connect("toggled", self.on_sentence_highlight_toggled)
        tb.append(self.sentence_highlight_check)
        
        self.word_highlight_check = Gtk.CheckButton(label="Highlight Word", active=True)
        self.word_highlight_check.connect("toggled", self.on_word_highlight_toggled)
        tb.append(self.word_highlight_check)
        
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
        self.sentences = sents
        self.total_sentences = len(sents)
        
        # Enhanced JavaScript functions that work with existing HTML
        html_doc = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<style>
:root {
  --bg:#fafafa; --fg:#2e2e2e; --muted:#777777; 
  --hover:rgba(0,0,0,0.05); 
  --highlight:#fdf6a5;   /* light blue */
  --word-highlight:#86ff58; /* soft pink */
}

@media (prefers-color-scheme: dark){
  :root {
    --bg:#14161a; --fg:#757e8f; --muted:#757e8f; 
    --hover:rgba(255,255,255,0.2); 
    --highlight:#045f1d;   /* cornflower */
    --word-highlight:#38802c; /* light salmon */
  }
}
    
html,body{
  background:var(--bg); color:var(--fg);
  font-family:"Noto Serif", noto-serif, -webkit-system-font; font-size:32px; line-height:1.6;
  padding:20px; margin:0;
}
.sentence-highlight{color:var(--word-highlight); font-weight:normal; border-radius:3px; padding:2px 4px}
.sentence-spoken{color:var(--muted)}
.sentence-hover:hover{background:var(--hover); cursor:pointer}
.word-highlight{color: var(--word-highlight); font-weight:bold; border-radius:2px; padding:1px 2px}
::selection{background:#264f78;color:inherit}
</style>
<script>
// Store original classes to restore later
function highlightSentence(i){
  // Remove previous highlights
  clearHighlights();
  // Add highlight class to current sentence
  const sentences = document.querySelectorAll('[data-sentence-id]');
  if (sentences[i-1]) {
    sentences[i-1].classList.add('sentence-highlight');
    sentences[i-1].scrollIntoView({behavior:'smooth',block:'center'});
  }
}

function markSentenceSpoken(i){
  const sentences = document.querySelectorAll('[data-sentence-id]');
  if (sentences[i-1]) {
    sentences[i-1].classList.remove('sentence-highlight');
    sentences[i-1].classList.add('sentence-spoken');
  }
}

function clearHighlights(){
  const sentences = document.querySelectorAll('[data-sentence-id]');
  sentences.forEach(s => {
    s.classList.remove('sentence-highlight', 'sentence-spoken');
  });
}

function highlightWord(sentIdx, wordIdx){
  clearWordHighlights();
  const words = document.querySelectorAll(`[data-sentence-id="${sentIdx}"] [data-word-id]`);
  if (words[wordIdx]) {
    words[wordIdx].classList.add('word-highlight');
    words[wordIdx].scrollIntoView({behavior:'smooth',block:'center'});
  }
}

function clearWordHighlights(){
  const words = document.querySelectorAll('[data-word-id]');
  words.forEach(w => {
    w.classList.remove('word-highlight');
  });
}

function getAllText(){
  return document.body.innerText || document.body.textContent || '';
}

function prepareDocument(){
  // This function will wrap existing content with data attributes for highlighting
  // without destroying existing HTML structure
  let sentenceCounter = 1;
  let walker = document.createTreeWalker(
    document.body,
    NodeFilter.SHOW_TEXT,
    null,
    false
  );
  
  let textNodes = [];
  let node;
  while(node = walker.nextNode()) {
    if (node.nodeValue.trim()) {
      textNodes.push(node);
    }
  }
  
  // Process each text node and wrap sentences
  textNodes.forEach(textNode => {
    const text = textNode.nodeValue;
    const sentences = text.split(/(?<=[.!?;:—])\s*/);
    if (sentences.length > 1 || (sentences.length === 1 && sentences[0].trim())) {
      const parent = textNode.parentNode;
      const fragment = document.createDocumentFragment();
      
      sentences.forEach((sentence, idx) => {
        if (sentence.trim()) {
          const span = document.createElement('span');
          span.setAttribute('data-sentence-id', sentenceCounter);
          span.classList.add('sentence-hover');
          span.textContent = sentence;
          
          // Add word-level spans
          const words = sentence.split(/\s+/);
          span.innerHTML = '';
          words.forEach((word, wordIdx) => {
            if (word.trim()) {
              const wordSpan = document.createElement('span');
              wordSpan.setAttribute('data-word-id', wordIdx);
              wordSpan.textContent = word + ' ';
              span.appendChild(wordSpan);
            }
          });
          
          fragment.appendChild(span);
          sentenceCounter++;
        }
      });
      
      parent.replaceChild(fragment, textNode);
    }
  });
}

// Initialize when document is loaded
document.addEventListener('DOMContentLoaded', function() {
  prepareDocument();
});
</script></head><body contenteditable="true" spellcheck="false">"""
        
        # Load the actual HTML content (preserving formatting)
        html_doc += text
        html_doc += "</body></html>"
        self.web_view.load_html(html_doc)

    # evaluate_javascript(script, length, world_name, source_uri, cancellable, callback, user_data)
    def js_fire_and_forget(self, code: str):
        try:
            self.web_view.evaluate_javascript(code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"[JS] call error: {e}")

    def highlight_sentence(self, idx): 
        if self.highlight_sentence_enabled:
            self.js_fire_and_forget(f"highlightSentence({int(idx)});")
    
    def mark_sentence_spoken(self, idx): 
        if self.highlight_sentence_enabled:
            self.js_fire_and_forget(f"markSentenceSpoken({int(idx)});")
    
    def clear_highlights(self): 
        if self.highlight_sentence_enabled:
            self.js_fire_and_forget("clearHighlights();")
    
    def highlight_word(self, sent_idx, word_idx): 
        if self.highlight_word_enabled:
            self.js_fire_and_forget(f"highlightWord({int(sent_idx)}, {int(word_idx)});")
    
    def clear_word_highlights(self): 
        if self.highlight_word_enabled:
            self.js_fire_and_forget("clearWordHighlights();")

    def on_sentence_highlight_toggled(self, check_button):
        self.highlight_sentence_enabled = check_button.get_active()
        if not self.highlight_sentence_enabled:
            self.clear_highlights()

    def on_word_highlight_toggled(self, check_button):
        self.highlight_word_enabled = check_button.get_active()
        if not self.highlight_word_enabled:
            self.clear_word_highlights()

    def get_text_from_webview(self):
        def on_text_received(web_view, result, user_data):
            try:
                jsres = web_view.evaluate_javascript_finish(result)
                val = jsres
                if hasattr(jsres, "get_js_value"):
                    val = jsres.get_js_value()
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
                if cleaned:
                    self.current_text = cleaned
                # (re)tokenize regardless so DOM and indices match
                self.sentences = tokenize(self.current_text)
                self.total_sentences = len(self.sentences)
                print(f"[TEXT] Using {self.total_sentences} sentences")
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
            self.clear_word_highlights()
            def start_playback():
                if not self.sentences:
                    self.status_label.set_label("No text to play"); return
                
                # Initialize the document with data attributes for highlighting
                self.js_fire_and_forget("prepareDocument();")
                GLib.idle_add(self.highlight_sentence, 1)

                if hasattr(self, 'prod_process') and self.prod_process and self.prod_process.is_alive():
                    try: self.prod_process.terminate(); self.prod_process.join(timeout=1)
                    except: pass
                self.ctrl = Controls()
                self.q = MPQ(maxsize=16)

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
            self.cleanup_playback(); self.status_label.set_label("Stopped"); self.clear_highlights(); self.clear_word_highlights()

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
            # Split sentence into words for word-by-word highlighting
            sentence = self.sentences[idx-1] if idx <= len(self.sentences) else ""
            words = sentence.split() if sentence else []
            word_durations = [len(word.encode('utf-8')) * 0.1 for word in words]  # Approximate durations
            total_word_duration = sum(word_durations)
            if total_word_duration > 0:
                word_durations = [d / total_word_duration * (n / (SR * frame_bytes)) for d in word_durations]
            
            word_start = 0
            for word_idx, word_duration in enumerate(word_durations):
                word_end = word_start + int(word_duration * SR * frame_bytes)
                word_end = min(word_end, n)
                
                # Highlight current word
                GLib.idle_add(self.highlight_word, idx, word_idx)
                
                # Play word audio
                word_pcm = pcm[word_start:word_end]
                word_off = 0
                word_step = step
                word_start_time = time.time()
                
                while word_off < len(word_pcm) and not ctrl.stop.is_set():
                    with ctrl.seek_lock:
                        if ctrl.seek_to is not None: return False
                    if ctrl.paused.is_set(): time.sleep(0.01); continue
                    chunk = word_pcm[word_off:word_off+word_step]
                    try: p.stdin.write(chunk); p.stdin.flush()
                    except Exception as e: print(f"[PLAY ] write err#{idx}: {e}"); return False
                    word_off += len(chunk)
                    expected = word_start_time + (word_off / (SR * frame_bytes))
                    sleep = expected - time.time()
                    if sleep > 0: time.sleep(sleep)
                
                word_start = word_end
                if ctrl.stop.is_set(): return False
            
            # Play remaining audio if any
            if word_start < n and not ctrl.stop.is_set():
                remaining_pcm = pcm[word_start:n]
                remaining_off = 0
                remaining_start_time = time.time()
                
                while remaining_off < len(remaining_pcm) and not ctrl.stop.is_set():
                    with ctrl.seek_lock:
                        if ctrl.seek_to is not None: return False
                    if ctrl.paused.is_set(): time.sleep(0.01); continue
                    chunk = remaining_pcm[remaining_off:remaining_off+step]
                    try: p.stdin.write(chunk); p.stdin.flush()
                    except Exception as e: print(f"[PLAY ] write err#{idx}: {e}"); return False
                    remaining_off += len(chunk)
                    expected = remaining_start_time + (remaining_off / (SR * frame_bytes))
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
                        GLib.idle_add(self.clear_word_highlights)
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
                        GLib.idle_add(self.clear_word_highlights)
                        current_playing += 1
                        if current_playing <= total:
                            def delayed(): time.sleep(0.05); GLib.idle_add(self.highlight_sentence, current_playing)
                            threading.Thread(target=delayed, daemon=True).start()
                        else:
                            GLib.idle_add(self.clear_highlights)
                            GLib.idle_add(self.clear_word_highlights)
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
