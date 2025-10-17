#!/usr/bin/env python3
# libadwaita + GTK4 + WebKitGTK 6 contenteditable WebView, sentence/word highlight,
# Kokoro ONNX TTS playback with play/pause/prev/next and adjustable prebuffer.
import os, re, json, time, pathlib, subprocess, sys, threading, multiprocessing as mp
from multiprocessing import Process, Queue as MPQ
import numpy as np, soundfile as sf
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, Adw, GLib, GObject, Gio, WebKit

# --- IMPORTANT: avoid forking after WebKit (prevents WebProcess issues) ---
mp.set_start_method("spawn", force=True)

# ---- Config ----
MODEL="/app/share/kokoro-models/kokoro-v1.0.onnx"
VOICES="/app/share/kokoro-models/voices-v1.0.bin"
LANG="en-us"; VOICE="af_sarah"; SPEED=1.0
SR=24000; CHUNK_FRAMES=2400
DEFAULT_BUFFER=3

INITIAL_HTML=r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<style>
:root{--hl-sent:rgba(255,200,0,.35);--hl-word:rgba(0,150,255,.45);}
html,body{margin:0;padding:0;background:#ffffff;color:#111111;font:16px/1.5 system-ui,Segoe UI,Roboto,Ubuntu,Arial,sans-serif}
@media (prefers-color-scheme:dark){html,body{background:#0b0d10;color:#e6e6e6}
:root{--hl-sent:rgba(255,210,40,.25);--hl-word:rgba(60,160,255,.35)}}
#editor{padding:16px;outline:none;min-height:80vh}
.sent.current{background:var(--hl-sent);border-radius:.25rem}
.word.current{background:var(--hl-word);border-radius:.25rem}
.word{transition:background .08s linear}
blockquote{border-inline-start:3px solid rgba(127,127,127,.35); padding-inline-start:.75rem}
pre,code,kbd{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace}
hr{border:0;border-top:1px solid rgba(127,127,127,.35); margin:1rem 0}
</style></head><body>
<div id="editor" contenteditable="true">
  <h1>Sample title</h1>
  <p>This is the 1st sentence. This is 2nd sentence. This is 3rd sentence!</p>
  <p><b>Is</b> this 4th sentence? This is 5th sentence. And this is 6th sentence.</p>
  <ul><li>And this is 7th sentence.</li><li>And while it is 8th sentence.</li></ul>
  <p>and this should be 9th sentence. And to stop the long string this is the 10th sentence.</p>
</div>
<script>
(()=> {
  const BLOCK_SEL="h1,h2,h3,h4,h5,h6,p,li,blockquote,pre,dt,dd,figcaption";
  const ucm=window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.app;
  const post=(type,data)=>{try{ucm&&ucm.postMessage(JSON.stringify({type,data}));}catch(_){}};  
  function unwrap(root){
    root.querySelectorAll("span.word,span.sent").forEach(s=>{while(s.firstChild)s.parentNode.insertBefore(s.firstChild,s); s.remove();});
  }
  function walkText(node,cb){
    const it=document.createNodeIterator(node,NodeFilter.SHOW_TEXT,{acceptNode(n){return!/^\s*$/.test(n.data)?1:2;}});
    let t; while((t=it.nextNode())) cb(t);
  }
  function wrapWords(el){
    let w=0;
    walkText(el, t=>{
      const parts=t.data.split(/(\s+)/);
      const frag=document.createDocumentFragment();
      for(let i=0;i<parts.length;i++){
        const s=parts[i];
        if(i%2===1){frag.appendChild(document.createTextNode(s));continue;}
        if(!s)continue;
        const span=document.createElement("span"); span.className="word"; span.dataset.wordIndex=String(++w); span.textContent=s;
        frag.appendChild(span);
      }
      t.parentNode.replaceChild(frag,t);
    });
    return w;
  }
  function rebuild(){
    const root=document.getElementById("editor");
    unwrap(root);
    let si=0, rep=[];
    root.querySelectorAll(BLOCK_SEL).forEach(b=>{
      const text=b.innerText.replace(/\s+/g," ").trim(); if(!text)return;
      const wc=wrapWords(b);
      const span=document.createElement("span"); span.className="sent"; span.dataset.sentIndex=String(++si);
      while(b.firstChild) span.appendChild(b.firstChild); b.appendChild(span);
      rep.push({index:si,text,wordCount:wc});
    });
    post("sentences",rep);
  }
  function clearHL(){document.querySelectorAll(".sent.current,.word.current").forEach(e=>e.classList.remove("current"));}
  window.App={
    rebuild, clearHighlights:clearHL,
    highlightSentence(i){clearHL(); const s=document.querySelector(`.sent[data-sent-index="${i}"]`); if(s){s.classList.add("current"); s.scrollIntoView({block:"center",behavior:"smooth"});}},
    highlightWord(i,w){const s=document.querySelector(`.sent[data-sent-index="${i}"]`); if(!s)return;
      s.querySelectorAll(".word.current").forEach(e=>e.classList.remove("current"));
      const ww=s.querySelector(`.word[data-word-index="${w}"]`); if(ww) ww.classList.add("current");
    }
  };
  document.addEventListener("DOMContentLoaded",rebuild);
  document.getElementById("editor").addEventListener("input",()=>{queueMicrotask(rebuild);});
})();
</script>
</body></html>"""

def f32_to_s16le(x): return (np.clip(x,-1,1)*32767.0).astype('<i2').tobytes()
def outdir():
    d=GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True); return d
def choose_play_cmd():
    for c in (["pacat","--rate",str(SR),"--channels","1","--format","s16le"],
              ["pw-cat","-p","--rate",str(SR),"--format","s16_le","--channels","1"]):
        try: subprocess.run([c[0],"--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return c
        except Exception: pass
    return None

# ---- Kokoro synth (separate process) ----
def synth_one(kok, idx, text, d):
    wav, sr = kok.create(text, voice=VOICE, speed=SPEED, lang=LANG)
    path=os.path.join(d,f"kokoro_sent_{idx:02d}.wav"); sf.write(path,wav,sr)
    return f32_to_s16le(wav), path, len(wav)/sr
def producer_proc(sents, start_idx, d, q: MPQ):
    try:
        from kokoro_onnx import Kokoro
        kok=Kokoro(MODEL, VOICES)
    except Exception as e:
        q.put(("error",str(e))); q.put((None,None,None,None)); return
    try:
        for i in range(start_idx, len(sents)+1):
            try:
                pcm, path, dur = synth_one(kok, i, sents[i-1]["text"], d)
                q.put(("ok", i, pcm, path, dur))
            except Exception as e:
                q.put(("err", i, str(e)))
    finally:
        q.put((None,None,None,None))

# ---- Controller ----
class Controller(GObject.GObject):
    __gsignals__ = {
        "status": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (int,int,int)),
    }
    def __init__(self, web: WebKit.WebView, get_buffer_size):
        super().__init__()
        self.web=web; self.get_buffer_size=get_buffer_size
        self.sents=[]; self.q=MPQ(maxsize=16); self.prod=None
        self.cmd=choose_play_cmd(); self.proc=None
        self.buf={}; self.eof=False
        self.playing=False; self.current=1; self.stop_flag=False
        self.word_timer_id=0
        self.audio_thread=None
        self.lock=threading.Lock()

    def on_script_message(self, _mgr, msg):
        try:
            data=json.loads(msg.get_js_value().to_string())
            if data.get("type")=="sentences":
                self.sents=data["data"]; self.emit("status", f"{len(self.sents)} sentences")
        except Exception: pass

    def web_eval(self, code):
        self.web.run_javascript(code, None, None, None)

    def ensure_player(self):
        if self.proc and self.proc.poll() is None: return True
        if not self.cmd: self.emit("status","No audio sink (pacat/pw-cat)"); return False
        self.proc=subprocess.Popen(self.cmd, stdin=subprocess.PIPE); return True

    def start(self):
        if not self.sents: self.emit("status","No content"); return
        if self.playing: return
        self.playing=True; self.stop_flag=False; self.emit("status","Play")
        if not (self.prod and self.prod.is_alive()):
            self.buf.clear(); self.eof=False
            self.prod=Process(target=producer_proc, args=(self.sents,1,outdir(),self.q))
            self.prod.start()
            GLib.timeout_add(30, self._pump_queue)
        self._start_sentence()

    def pause(self):
        self.playing=False; self.emit("status","Pause")

    def stop(self):
        self.playing=False; self.stop_flag=True
        try:
            if self.proc and self.proc.stdin: self.proc.stdin.close()
            if self.proc: self.proc.terminate()
        except Exception: pass
        self.proc=None
        if self.word_timer_id: GLib.source_remove(self.word_timer_id); self.word_timer_id=0
        self.web_eval("App.clearHighlights()"); self.emit("status","Stop")

    def close(self):
        self.stop()
        try:
            if self.prod and self.prod.is_alive():
                self.prod.terminate(); self.prod.join(timeout=0.5)
        except Exception: pass
        try:
            self.q.close()
        except Exception: pass

    def prev(self):
        if self.current>1: self.current-=1; self._restart_sentence()
    def next(self):
        if self.current < len(self.sents): self.current+=1; self._restart_sentence()

    def _restart_sentence(self):
        with self.lock:
            self.playing=True; self.stop_flag=False
        self.audio_thread=None
        self._start_sentence()

    def _pump_queue(self):
        try:
            while True:
                item=self.q.get_nowait()
                if item[0] is None: self.eof=True; break
                if item[0]=="ok":
                    _, idx, pcm, _path, dur=item
                    self.buf[idx]=dict(pcm=pcm,dur=dur)
                elif item[0]=="error": self.emit("status", f"Synth error: {item[1]}")
                elif item[0]=="err": self.emit("status", f"Synth err#{item[1]}: {item[2]}")
        except Exception: pass
        return (not self.eof) or bool(self.buf)

    def _start_sentence(self):
        if not self.playing or self.stop_flag: return
        if self.current not in self.buf:
            GLib.timeout_add(50, self._start_sentence); return
        self.web_eval(f"App.highlightSentence({self.current})")
        words=max(1, int(self.sents[self.current-1].get("wordCount",1)))
        dur=max(0.05, float(self.buf[self.current]["dur"]))
        interval_ms=int(max(20,(dur/words)*1000))
        st={"w":0,"idx":self.current,"total":words}
        if self.word_timer_id: GLib.source_remove(self.word_timer_id)
        def tick():
            if not self.playing or self.stop_flag or self.current!=st["idx"]: return False
            st["w"]+=1
            self.emit("progress", self.current, min(st["w"],words), words)
            self.web_eval(f"App.highlightWord({self.current},{min(st['w'],words)})")
            return st["w"]<words
        self.word_timer_id=GLib.timeout_add(interval_ms, tick)

        if not self.ensure_player(): return
        pcm=self.buf[self.current]["pcm"]; step=CHUNK_FRAMES*2
        def worker(idx, data):
            off=0; n=len(data)
            try:
                while off<n:
                    with self.lock:
                        if not self.playing or self.stop_flag or self.current!=idx: break
                    chunk=data[off:off+step]
                    if not chunk: break
                    try:
                        self.proc.stdin.write(chunk); self.proc.stdin.flush()
                    except Exception:
                        break
                    off+=len(chunk)
                    time.sleep(CHUNK_FRAMES/float(SR)*0.9)
            finally:
                GLib.idle_add(self._after_sentence, idx)
        self.audio_thread=threading.Thread(target=worker, args=(self.current, pcm), daemon=True)
        self.audio_thread.start()

    def _after_sentence(self, idx):
        if self.current!=idx or not self.playing or self.stop_flag: return False
        self.current+=1
        for k in list(self.buf.keys()):
            if k < self.current-1: self.buf.pop(k,None)
        ahead=len([k for k in self.buf if k>=self.current])
        if (ahead < self.get_buffer_size()) and not self.eof:
            GLib.timeout_add(50, self._start_sentence)
        else:
            self._start_sentence()
        return False

# ---- UI ----
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kokoro Web TTS", default_width=1024, default_height=720)

        tv=Adw.ToolbarView(); self.set_content(tv)
        hb=Adw.HeaderBar()

        ucm=WebKit.UserContentManager(); ucm.register_script_message_handler("app")
        self.web=WebKit.WebView.new_with_properties(user_content_manager=ucm) if hasattr(WebKit.WebView,"new_with_properties") else WebKit.WebView(user_content_manager=ucm)
        if hasattr(self.web,"set_focusable"): self.web.set_focusable(True)

        scroller=Gtk.ScrolledWindow(hexpand=True, vexpand=True); scroller.set_child(self.web)

        self.web.load_html(INITIAL_HTML, "file:///")
        def on_load_changed(_web, ev):
            if ev == WebKit.LoadEvent.FINISHED:
                self.status("Ready")
                self.web.run_javascript("document.getElementById('editor')?.focus()", None, None, None)
        self.web.connect("load-changed", on_load_changed)

        ucm.connect("script-message-received::app", self.on_script_message)

        self.ctrl=Controller(self.web, self._get_buffer_size)
        self.ctrl.connect("status", lambda _c,m: self.status(m))
        self.ctrl.connect("progress", self.on_progress)
        ucm.connect("script-message-received::app", self.ctrl.on_script_message)

        self.btn_play=Gtk.Button(icon_name="media-playback-start-symbolic")
        self.btn_pause=Gtk.Button(icon_name="media-playback-pause-symbolic")
        self.btn_prev=Gtk.Button(icon_name="go-previous-symbolic")
        self.btn_next=Gtk.Button(icon_name="go-next-symbolic")
        self.btn_rebuild=Gtk.Button(label="Reindex")
        self.spin_buf=Gtk.SpinButton.new_with_range(1,10,1); self.spin_buf.set_value(DEFAULT_BUFFER)
        for b in (self.btn_prev,self.btn_play,self.btn_pause,self.btn_next,self.btn_rebuild): hb.pack_start(b)
        hb.pack_end(Gtk.Label(label="Buffer:")); hb.pack_end(self.spin_buf)

        self.btn_play.connect("clicked", lambda *_: self.ctrl.start())
        self.btn_pause.connect("clicked", lambda *_: self.ctrl.pause())
        self.btn_prev.connect("clicked", lambda *_: self.ctrl.prev())
        self.btn_next.connect("clicked", lambda *_: self.ctrl.next())
        self.btn_rebuild.connect("clicked", lambda *_: self.web.run_javascript("App.rebuild()", None, None, None))

        foot=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        self.lbl_status=Gtk.Label(xalign=0); self.lbl_prog=Gtk.Label(xalign=1)
        foot.append(self.lbl_status); foot.append(Gtk.Box(hexpand=True)); foot.append(self.lbl_prog)

        tv.add_top_bar(hb); tv.set_content(scroller); tv.add_bottom_bar(foot)

        sc=Gtk.ShortcutController(); sc.set_scope(Gtk.ShortcutScope.LOCAL)
        def add(key, cb):
            trig=Gtk.ShortcutTrigger.parse_string(key)
            def handler(*_):
                if self.web.has_focus(): return False
                cb(); return True
            sc.add_shortcut(Gtk.Shortcut.new(trig, Gtk.CallbackAction.new(handler)))
        add("space", lambda: self.ctrl.start() if not self.ctrl.playing else self.ctrl.pause())
        add("a", self.ctrl.prev); add("d", self.ctrl.next); add("s", self.ctrl.stop)
        self.add_controller(sc)

        self.connect("map", lambda *_: self.web.grab_focus())
        self.connect("close-request", self._on_close)

    def _on_close(self, *_):
        try: self.ctrl.close()
        except Exception: pass
        return False

    def _get_buffer_size(self):
        try: return int(self.spin_buf.get_value())
        except Exception: return DEFAULT_BUFFER
    def status(self, msg): self.lbl_status.set_text(msg or "")
    def on_progress(self, _c, idx, w, total): self.lbl_prog.set_text(f"{idx}/{len(self.ctrl.sents)}  word {w}/{total}")
    def on_script_message(self, *_): pass

class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win=None
    def do_activate(self):
        if not self.win: self.win=MainWindow(self)
        self.win.present()
    def do_shutdown(self):
        try:
            if self.win: self.win.ctrl.close()
        except Exception: pass
        super().do_shutdown()

def main(): sys.exit(App().run(sys.argv))
if __name__=="__main__": main()

