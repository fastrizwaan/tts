#!/usr/bin/env python3
# HTML TTS editor (Adw/GTK4/WebKitGTK 6) with sentence+word highlight
# Keeps original HTML formatting; better sync.
# App ID: io.github.fastrizwaan.tts   Keys: z=pause/resume, s=stop, a=prev, d=next.

import os, re, sys, html as htmllib, threading, subprocess, time, pathlib, json
import numpy as np, soundfile as sf
from multiprocessing import Process, Queue as MPQ
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, Adw, WebKit, GLib, Gio, Gdk

# -------- TTS (Kokoro) --------
from kokoro_onnx import Kokoro
MODEL="/app/share/kokoro-models/kokoro-v1.0.onnx"
VOICES="/app/share/kokoro-models/voices-v1.0.bin"
LANG="en-us"; VOICE="af_sarah"; SPEED=1.0
SR=24000; CHUNK_FRAMES=2400; PREROLL=3

def f32_to_s16le(x): return (np.clip(x,-1,1)*32767.0).astype('<i2').tobytes()
def synth_one(kok, idx, sent, d):
    wav, sr = kok.create(sent, voice=VOICE, speed=SPEED, lang=LANG)
    path = os.path.join(d, f"kokoro_sent_{idx:02d}.wav"); sf.write(path, wav, sr)
    return f32_to_s16le(wav), path

def producer_proc(sents, start_idx, d, q: MPQ):
    kok = Kokoro(MODEL, VOICES)
    try:
        for i in range(start_idx, len(sents)+1):
            try: q.put((i,)+synth_one(kok, i, sents[i-1], d))
            except Exception as e: print(f"[PROD] {e}")
    finally: q.put((None,None,None))

def choose_play_cmd():
    for c in (["pacat","--rate",str(SR),"--channels","1","--format","s16le"],
              ["pw-cat","-p","--rate",str(SR),"--format","s16_le","--channels","1"]):
        try: subprocess.run([c[0],"--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return c
        except Exception: pass
    return None

class Controls:
    def __init__(self):
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.seek_to = None
        self.seek_lock = threading.Lock()
        self.current_sentence = 1
        self.sentence_lock = threading.Lock()

def player_thread(qin: MPQ, ctrl: Controls, total: int, on_index_cb):
    cmd = choose_play_cmd()
    if not cmd: print("[AUDIO] pacat/pw-cat not found"); return
    frame_bytes=2; step=CHUNK_FRAMES*frame_bytes; sec_per_chunk=CHUNK_FRAMES/float(SR)
    buf={}; eof=False; cur=1

    while len(buf) < PREROLL and not eof and not ctrl.stop.is_set():
        idx, pcm, _ = qin.get()
        if idx is None: eof=True; break
        buf[idx]=pcm

    def restart(): return subprocess.Popen(cmd, stdin=subprocess.PIPE)
    def play_chunk(p, pcm, idx):
        off=0; n=len(pcm)
        while off<n and not ctrl.stop.is_set():
            with ctrl.seek_lock:
                if ctrl.seek_to is not None: return False
            if ctrl.paused.is_set(): time.sleep(0.01); continue
            chunk = pcm[off:off+step]
            try: p.stdin.write(chunk); p.stdin.flush()
            except Exception: return False
            off += len(chunk); time.sleep(sec_per_chunk*0.8)
        return True

    p = restart()
    try:
        while not ctrl.stop.is_set():
            with ctrl.seek_lock:
                if ctrl.seek_to is not None:
                    cur = ctrl.seek_to; ctrl.seek_to=None
                    try:
                        if p.stdin: p.stdin.close()
                        p.terminate(); p.wait(timeout=1)
                    except: pass
                    p = restart()

            while cur not in buf and not eof and not ctrl.stop.is_set():
                try:
                    idx, pcm, _ = qin.get(timeout=0.1)
                    if idx is None: eof=True
                    else: buf[idx]=pcm
                except: pass

            if cur in buf and not ctrl.stop.is_set():
                pcm = buf[cur]
                dur = len(pcm) / (SR*frame_bytes)
                with ctrl.sentence_lock: ctrl.current_sentence = cur
                GLib.idle_add(on_index_cb, cur, float(dur))
                if play_chunk(p, pcm, cur):
                    cur += 1
                    if cur>total and eof: break
                else:
                    continue

            if not eof:
                try:
                    idx, pcm, _ = qin.get_nowait()
                    if idx is None: eof=True
                    else: buf[idx]=pcm
                except: time.sleep(0.01)
    finally:
        try:
            if p.stdin: p.stdin.close()
            p.wait(timeout=2)
        except: pass

# -------- Sentence/word utils --------
BLOCK_TAGS=("p","div","li","br","h1","h2","h3","h4","h5","h6","tr","td","th","ul","ol","section","article","header","footer","blockquote","pre")
SENT_SPLIT = re.compile(r'(?: (?<!\d)\.(?!\d) | [!?] )\s+', re.X)
WORD_RX = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[.,!?;:—-]")

def html_body_inner(html: str) -> str:
    m = re.search(r'(?is)<body[^>]*>(.*)</body>', html)
    return (m.group(1) if m else html)

def html_for_tts_only(html: str) -> str:
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html)
    s = re.sub(r'(?is)</?(?:' + "|".join(BLOCK_TAGS) + r')(?:\s[^>]*)?>', '. ', s)
    s = re.sub(r'(?is)<[^>]+>', ' ', s)
    s = htmllib.unescape(s)
    s = re.sub(r'[ \t\r\f\v]+', ' ', s)
    s = re.sub(r'\s*\.\s*\.\s*', '. ', s)
    return s.strip()

def tokenize_html_for_tts(html: str):
    """Extract sentences from HTML and return both plain text sentences and word lists"""
    text = html_for_tts_only(html)
    parts = SENT_SPLIT.split(text)
    sentences = []
    word_lists = []
    
    for p in parts:
        t = p.strip()
        if not t: continue
        if not re.search(r'[.!?]$', t): t += '.'
        
        # Extract words using the same regex as JavaScript
        words = [w for w in WORD_RX.findall(t) if w.strip()]
        if words:  # Only add if we have actual words
            sentences.append(t)
            word_lists.append(words)
    
    return sentences, word_lists

def downloads_dir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True); return d

# -------- App --------
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, initial_html=None, file_gfile=None):
        super().__init__(application=app)
        self.set_default_size(1000, 720); self.set_title("HTML TTS")
        self.current_file = file_gfile
        self.sents=[]; self.words=[]
        self.ctrl=None; self.producer=None; self.play_thread=None; self.q=None
        self.word_timer_id=None; self.playview_built=False
        self.word_schedule=[]; self.word_idx=0

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(); tv.add_top_bar(hb)

        self.btn_open = Gtk.Button.new_from_icon_name("document-open-symbolic")
        self.btn_play = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self.btn_pause = Gtk.Button.new_from_icon_name("media-playback-pause-symbolic")
        self.btn_prev = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.btn_next = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.btn_stop = Gtk.Button.new_from_icon_name("media-playback-stop-symbolic")
        for b in (self.btn_open,self.btn_prev,self.btn_play,self.btn_pause,self.btn_next,self.btn_stop): b.add_css_class("flat")
        hb.pack_start(self.btn_open)
        hb.pack_end(self.btn_stop); hb.pack_end(self.btn_next); hb.pack_end(self.btn_pause); hb.pack_end(self.btn_play); hb.pack_end(self.btn_prev)

        self.status = Gtk.Label(xalign=0)
        self.status.set_margin_start(8); self.status.set_margin_end(8)
        self.status.set_margin_top(4); self.status.set_margin_bottom(4)

        self.web = WebKit.WebView(); self.web.set_hexpand(True); self.web.set_vexpand(True)
        try: self.web.get_settings().set_enable_developer_extras(True)
        except: pass

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(self.web); box.append(self.status)
        tv.set_content(box); self.set_content(tv)

        self._load_editor_html(initial_html)

        self.btn_open.connect("clicked", self.on_open_clicked)
        self.btn_play.connect("clicked", self.on_play_clicked)
        self.btn_pause.connect("clicked", self.on_pause_clicked)
        self.btn_prev.connect("clicked", self.on_prev_clicked)
        self.btn_next.connect("clicked", self.on_next_clicked)
        self.btn_stop.connect("clicked", self.on_stop_clicked)

        key = Gtk.EventControllerKey(); key.connect("key-pressed", self.on_key); self.add_controller(key)
        self.connect("close-request", self.on_close)

        if self.current_file and (initial_html is None): self.load_file(self.current_file)

    # ---- Editor HTML (no f-string; avoids brace parsing) ----
    def _load_editor_html(self, body_html=None):
        html = """<!doctype html>
<html style="height:100%">
<head>
<meta charset="utf-8"><title>HTML TTS</title>
<style>
html,body { height:100%; margin:0; }
#wrap { position:relative; height:100%; }
#editor { height:100%; padding:16px; outline:none; font-family: Sans; font-size: 12pt; }
#editor:empty::before { content:"Type or paste HTML here…"; color:#888; font-style:italic; }
#playview { display:none; height:100%; padding:16px; overflow:auto; font-family: Sans; font-size: 12pt; }
.sent.playing { background: rgba(255,183,61,0.35); outline: 1px solid rgba(229,151,40,0.6); border-radius:4px; }
.w.playing { background: rgba(135,206,250,0.45); border-radius:3px; }
@media (prefers-color-scheme: dark) {
  html,body { background:#1e1e1e; color:#c0c0c0; }
  .sent.playing { background: rgba(255,183,61,0.25); outline-color: rgba(229,151,40,0.5); }
  .w.playing { background: rgba(135,206,250,0.35); }
}
@media (prefers-color-scheme: light) {
  html,body { background:#ffffff; color:#000; }
}
</style>
<script>
let CUR_S=-1, CUR_W=-1;
let SENTENCE_DATA = []; // Will hold {text: "...", words: ["word1", "word2", ...]}

function init(){
  const ed=document.getElementById('editor'); ed.setAttribute('contenteditable','true');
  ed.addEventListener('paste', (e)=>{
    const html = e.clipboardData.getData('text/html');
    if (html) {
      e.preventDefault();
      const inner = extractBody(html);
      document.execCommand('insertHTML', false, inner);
    }
  });
}

function extractBody(html){
  const m = /<body[^>]*>([\\s\\S]*?)<\\/body>/i.exec(html);
  return m?m[1]:html;
}

function getHTML(){ return document.getElementById('editor').innerHTML; }

function setSentenceData(data) {
  SENTENCE_DATA = data;
}

/* Build play view keeping original HTML formatting but adding sentence/word spans */
function buildPlayViewFromSentenceData(){
  const ed = document.getElementById('editor');
  const pv = document.getElementById('playview');
  
  // Start with original HTML formatting
  pv.innerHTML = ed.innerHTML;
  
  if (SENTENCE_DATA.length === 0) {
    return;
  }
  
  // Get all text content to build a word sequence
  const allTextContent = pv.textContent || pv.innerText || '';
  const WORD_RX = /[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[.,!?;:—-]/g;
  
  // Extract all words from the text content
  const allWords = [];
  let match;
  while ((match = WORD_RX.exec(allTextContent)) !== null) {
    allWords.push(match[0]);
  }
  
  // Build a mapping from our extracted words to sentence/word positions
  const wordPositions = [];
  let sentIdx = 0;
  let wordInSentIdx = 0;
  
  for (let i = 0; i < allWords.length && sentIdx < SENTENCE_DATA.length; i++) {
    const word = allWords[i];
    const currentSentWords = SENTENCE_DATA[sentIdx].words;
    
    if (wordInSentIdx < currentSentWords.length && word === currentSentWords[wordInSentIdx]) {
      wordPositions.push({
        globalWordIndex: i,
        sentenceIndex: sentIdx + 1, // 1-based
        wordIndex: wordInSentIdx + 1, // 1-based
        word: word
      });
      
      wordInSentIdx++;
      if (wordInSentIdx >= currentSentWords.length) {
        sentIdx++;
        wordInSentIdx = 0;
      }
    }
  }
  
  // Now walk through text nodes and apply highlighting
  let globalWordCounter = 0;
  
  function processTextNode(node) {
    const text = node.nodeValue;
    if (!text.trim()) return;
    
    const words = text.match(WORD_RX) || [];
    if (words.length === 0) return;
    
    const parent = node.parentNode;
    const fragment = document.createDocumentFragment();
    let textPos = 0;
    
    let currentSentenceSpan = null;
    let currentSentenceIndex = -1;
    
    for (const word of words) {
      // Find this word's position in the text
      const wordStart = text.indexOf(word, textPos);
      
      // Add any text before this word
      if (wordStart > textPos) {
        const beforeText = text.slice(textPos, wordStart);
        if (currentSentenceSpan) {
          currentSentenceSpan.appendChild(document.createTextNode(beforeText));
        } else {
          fragment.appendChild(document.createTextNode(beforeText));
        }
      }
      
      // Check if this word should be highlighted
      const wordPos = wordPositions.find(wp => wp.globalWordIndex === globalWordCounter);
      
      if (wordPos) {
        // Start new sentence span if needed
        if (wordPos.sentenceIndex !== currentSentenceIndex) {
          currentSentenceIndex = wordPos.sentenceIndex;
          currentSentenceSpan = document.createElement('span');
          currentSentenceSpan.className = 'sent';
          currentSentenceSpan.dataset.i = String(currentSentenceIndex);
          fragment.appendChild(currentSentenceSpan);
        }
        
        // Create word span
        const wordSpan = document.createElement('span');
        wordSpan.className = 'w';
        wordSpan.dataset.j = String(wordPos.wordIndex);
        wordSpan.textContent = word;
        currentSentenceSpan.appendChild(wordSpan);
      } else {
        // Not a highlighted word, add as text
        if (currentSentenceSpan) {
          currentSentenceSpan.appendChild(document.createTextNode(word));
        } else {
          fragment.appendChild(document.createTextNode(word));
        }
      }
      
      globalWordCounter++;
      textPos = wordStart + word.length;
    }
    
    // Add any remaining text
    if (textPos < text.length) {
      const remainingText = text.slice(textPos);
      if (currentSentenceSpan) {
        currentSentenceSpan.appendChild(document.createTextNode(remainingText));
      } else {
        fragment.appendChild(document.createTextNode(remainingText));
      }
    }
    
    parent.replaceChild(fragment, node);
  }
  
  // Collect all text nodes first to avoid modification during iteration
  const textNodes = [];
  const walker = document.createTreeWalker(
    pv,
    NodeFilter.SHOW_TEXT,
    null,
    false
  );
  
  let node;
  while (node = walker.nextNode()) {
    if (node.nodeValue && node.nodeValue.trim()) {
      textNodes.push(node);
    }
  }
  
  // Process text nodes in order
  textNodes.forEach(processTextNode);
}

function showPlayView(show){
  const ed=document.getElementById('editor'); const pv=document.getElementById('playview');
  pv.style.display = show ? 'block' : 'none';
  ed.style.display = show ? 'none' : 'block';
}

function highlightSentence(idx){
  const pv=document.getElementById('playview');
  pv.querySelectorAll('.sent.playing').forEach(n=>n.classList.remove('playing'));
  const s = pv.querySelector('.sent[data-i="'+idx+'"]');
  if (s) { 
    s.classList.add('playing'); 
    s.scrollIntoView({block:'nearest', inline:'nearest'}); 
  }
  CUR_S=idx; CUR_W=-1;
  pv.querySelectorAll('.w.playing').forEach(n=>n.classList.remove('playing'));
}

function highlightWord(idx, wj){
  if (idx!==CUR_S) return;
  const pv=document.getElementById('playview');
  pv.querySelectorAll('.w.playing').forEach(n=>n.classList.remove('playing'));
  const s = pv.querySelector('.sent[data-i="'+idx+'"]'); if (!s) return;
  const w = s.querySelector('.w[data-j="'+wj+'"]');
  if (w) { 
    w.classList.add('playing'); 
    w.scrollIntoView({block:'nearest', inline:'nearest'}); 
    CUR_W=wj; 
  }
}

window.addEventListener('DOMContentLoaded', init);
</script>
</head>
<body>
<div id="wrap">
  <div id="editor">""" + (body_html or "") + """</div>
  <div id="playview"></div>
</div>
</body>
</html>"""
        self.web.load_html(html, None)

    def js(self, code): self.web.evaluate_javascript(code, -1, None, None, None, None, None)
    def js_get_html(self, cb):
        self.web.evaluate_javascript("getHTML();", -1, None, None, None,
                                     lambda w, res, data: self._on_js_result(w, res, cb), None)
    def _on_js_result(self, webview, result, cb):
        try:
            v = webview.evaluate_javascript_finish(result)
            s = v.get_js_value().to_string() if hasattr(v,'get_js_value') else (v.to_string() if hasattr(v,'to_string') else str(v))
            cb(s)
        except Exception as e: self.set_status(f"JS error: {e}")

    # ---- File I/O ----
    def on_open_clicked(self, *_):
        dlg = Gtk.FileDialog(); f = Gtk.FileFilter(); f.add_suffix("html"); f.add_suffix("htm"); dlg.set_default_filter(f)
        dlg.open(self, None, self._on_open_finish)
    def _on_open_finish(self, dlg, res):
        try:
            gfile = dlg.open_finish(res); self.current_file = gfile; self.load_file(gfile)
        except Exception as e: self.set_status(f"Open failed: {e}")
    def load_file(self, gfile):
        try:
            src = gfile.load_contents(None)[1].decode('utf-8', errors='replace')
            inner = html_body_inner(src)
            self._load_editor_html(inner)
            self.set_title(f"{gfile.get_basename()} — HTML TTS"); self.set_status("Loaded.")
        except Exception as e: self.set_status(f"Read error: {e}")

    # ---- Playback ----
    def on_play_clicked(self, *_):
        if self.play_thread and self.play_thread.is_alive(): return
        self.js_get_html(self._start_tts_from_html)

    def _start_tts_from_html(self, html_str):
        # Use the improved tokenization that returns both sentences and word lists
        sents, words = tokenize_html_for_tts(html_str)
        if not sents: self.set_status("No sentences."); return
        
        self.sents = sents
        self.words = words
        
        # Pass sentence data to JavaScript for exact matching
        sentence_data = []
        for i, (sent, word_list) in enumerate(zip(sents, words)):
            sentence_data.append({"text": sent, "words": word_list})
        
        # Send the sentence data to JavaScript
        sentence_data_json = json.dumps(sentence_data).replace('"', '\\"')
        self.js(f'setSentenceData(JSON.parse("{sentence_data_json}"));')
        self.js("buildPlayViewFromSentenceData(); showPlayView(true);")
        
        self.set_status(f"Sentences: {len(sents)}")
        self.q = MPQ(maxsize=16); self.ctrl = Controls()
        outdir = downloads_dir()
        self.producer = Process(target=producer_proc, args=(self.sents, 1, outdir, self.q), daemon=True); self.producer.start()
        self.play_thread = threading.Thread(target=player_thread,
                                            args=(self.q, self.ctrl, len(self.sents), self._on_index_change),
                                            daemon=True)
        self.play_thread.start()

    def _clear_word_timer(self):
        if self.word_timer_id:
            try:
                GLib.source_remove(self.word_timer_id)
            except:
                pass  # Timer may have already been removed
            self.word_timer_id = None

    def _build_word_schedule(self, words, dur_s):
        if not words: return []
        weights=[]
        for w in words:
            if w in ('.','!','?'): weights.append(2.5)
            elif w in (',',';','—','-'): weights.append(1.5)
            else: weights.append(max(1.0, len(w)*0.6))
        total = sum(weights); t=0.0; sched=[]
        for i,w in enumerate(words, start=1):
            frac = weights[i-1]/total; t += dur_s*frac; sched.append((i, t))
        return sched

    def _on_index_change(self, idx, dur):
        self._clear_word_timer()
        self.js(f"highlightSentence({int(idx)});")
        self.set_status(f"Playing {idx}/{len(self.sents)}")
        words = self.words[idx-1] if 0<idx<=len(self.words) else []
        self.word_schedule = self._build_word_schedule(words, dur)
        self.word_idx = 0
        start_ms = int(time.time()*1000)

        def tick():
            if not (self.ctrl and not self.ctrl.stop.is_set()): return False
            if self.ctrl.paused.is_set(): return True
            if self.word_idx >= len(self.word_schedule): return False
            now_ms = int(time.time()*1000)
            target_ms = int(self.word_schedule[self.word_idx][1]*1000)
            elapsed_ms = now_ms - start_ms
            if elapsed_ms + 25 >= target_ms:
                wj = self.word_schedule[self.word_idx][0]
                self.js(f"highlightWord({int(idx)}, {int(wj)});")
                self.word_idx += 1
            return True

        self.word_timer_id = GLib.timeout_add(30, tick)
        return False

    def on_pause_clicked(self, *_):
        if not self.ctrl: return
        if self.ctrl.paused.is_set(): self.ctrl.paused.clear(); self.set_status("Resume")
        else: self.ctrl.paused.set(); self.set_status("Pause")

    def on_prev_clicked(self, *_):
        if not self.ctrl: return
        self._clear_word_timer()
        with self.ctrl.sentence_lock: cur = self.ctrl.current_sentence
        with self.ctrl.seek_lock: self.ctrl.seek_to = 1 if cur<=1 else cur-1
        self.set_status(f"Seek {self.ctrl.seek_to}")

    def on_next_clicked(self, *_):
        if not self.ctrl: return
        self._clear_word_timer()
        with self.ctrl.sentence_lock: cur = self.ctrl.current_sentence
        with self.ctrl.seek_lock: self.ctrl.seek_to = min(len(self.sents), cur+1)
        self.set_status(f"Seek {self.ctrl.seek_to}")

    def on_stop_clicked(self, *_): self._stop_playback()
    def _stop_playback(self):
        self._clear_word_timer()
        if self.ctrl: self.ctrl.stop.set()
        if self.q:
            try: self.q.put((None,None,None))
            except Exception: pass
        if self.play_thread and self.play_thread.is_alive(): self.play_thread.join(timeout=2)
        if self.producer and self.producer.is_alive(): self.producer.terminate(); self.producer.join(timeout=2)
        self.ctrl=None; self.play_thread=None; self.producer=None; self.q=None
        self.js("showPlayView(false);")
        self.set_status("Stopped.")

    def on_key(self, _ctrl, keyval, keycode, state):
        ch=(Gdk.keyval_name(keyval) or "").lower()
        if ch=='z': self.on_pause_clicked()
        elif ch=='s': self.on_stop_clicked()
        elif ch=='a': self.on_prev_clicked()
        elif ch=='d': self.on_next_clicked()
        return False

    def on_close(self, *_): self._stop_playback(); return False
    def set_status(self, s): self.status.set_text(s)

class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts",
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.connect("activate", self.on_activate)
        self.connect("open", self.on_open)
    def on_activate(self, *_): MainWindow(self).present()
    def on_open(self, app, files, n_files, hint):
        (MainWindow(self, file_gfile=files[0]) if files else MainWindow(self)).present()

def main():
    Adw.init(); return App().run(sys.argv)
if __name__ == "__main__": main()
