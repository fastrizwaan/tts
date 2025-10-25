#!/usr/bin/env python3
# GTK4 + WebKit 6.0 + kokoro-onnx (naive word timings) with espeak_wrapper stub
import gi, os, pathlib, re, json, time, types, sys
gi.require_version("Gtk","4.0")
gi.require_version("WebKit","6.0")
from gi.repository import Gtk, GLib, WebKit
import soundfile as sf

APP_ID = "io.github.fastrizwaan.tts"
TEXT = ("This is the 1st sentence in 1995. This is 2nd sentence in 2004. This is 3rd sentence! "
        "Is this 4th sentence? This is 5th sentence. And this is 6th sentence. "
        "and this is 7th sentence. And while it is 8th sentence. "
        "and this should be 9th sentence. And to stop the long string this is the 10th sentence.")
LANG, VOICE, SPEED = "en-us", "af_sarah", 1.0
MODEL  = "~/kokoro-models/kokoro-v1.0.onnx"
VOICES = "~/kokoro-models/voices-v1.0.bin"

HTML_TMPL = """<!doctype html><meta charset="utf-8">
<style>body{font:16px system-ui;line-height:1.6;padding:16px}.w{transition:background .12s}.cur{background:rgba(255,225,120,.9);border-radius:4px}</style>
<div id="container">%s</div>
<script>
let cur=-1;
function highlightWord(i){
  if(cur===i)return;
  if(cur>=0){const p=document.querySelector(`[data-i="${cur}"]`); if(p)p.classList.remove('cur');}
  const el=document.querySelector(`[data-i="${i}"]`);
  if(el){el.classList.add('cur');el.scrollIntoView({block:"center",inline:"nearest"});cur=i;}
}
</script>"""

def wrap_tokens_for_html(text:str):
    tokens = re.findall(r"\w+|\s+|[^\w\s]", text, flags=re.UNICODE)
    out, idx = [], 0
    for t in tokens:
        if re.match(r"\w+", t):
            out.append(f'<span class="w" data-i="{idx}">{t}</span>'); idx+=1
        else:
            out.append(t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
    return "".join(out), idx

def writable_path(filename):
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)
    return os.path.join(d, filename)

def _expand(p:str)->str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))

# --- fix env + provide missing kokoro_onnx.espeak_wrapper with set_data_path ---
os.environ.setdefault("ESPEAK_DATA", "/usr/share/espeak-ng-data")
mod = types.ModuleType("kokoro_onnx.espeak_wrapper")
class _EspeakWrapper: pass
def _set_data_path(p): setattr(_EspeakWrapper, "data_path", p)
_EspeakWrapper.set_data_path = staticmethod(_set_data_path)
mod.EspeakWrapper = _EspeakWrapper
sys.modules["kokoro_onnx.espeak_wrapper"] = mod
# -----------------------------------------------------------------------------

from kokoro_onnx import Kokoro
# -----------------------------------------------------------------------------

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.word_idx=-1; self.timings=[]; self.t0=None; self.tick_id=None; self.wav_path=None

    def do_activate(self, *args):
        win = Gtk.ApplicationWindow(application=self, title="Kokoro Highlight (WebKit 6.0)")
        win.set_default_size(900,520)
        self.view = WebKit.WebView()
        sc = Gtk.ScrolledWindow(); sc.set_child(self.view)
        self.play_btn = Gtk.Button(label="Play"); self.play_btn.connect("clicked", self.on_play); self.play_btn.set_sensitive(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); box.append(sc); box.append(self.play_btn)
        win.set_child(box); win.present()
        html, _ = wrap_tokens_for_html(TEXT); self.view.load_html(HTML_TMPL % html, "file:///")

        try:
            self.wav_path, self.timings = self.synthesize(TEXT); self.play_btn.set_sensitive(True)
        except Exception as e:
            self.view.load_html(HTML_TMPL % f"<p><b>Error:</b> {e}</p>", "file:///"); print("Synthesis error:", e)

    def synthesize(self, text:str):
        model_path=_expand(MODEL); voices_path=_expand(VOICES)
        if not os.path.isfile(model_path): raise FileNotFoundError(f"Model file not found: {model_path}")
        if not os.path.isfile(voices_path): raise FileNotFoundError(
            f"Voices file not found: {voices_path}\n"
            "wget https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
        )
        kokoro = Kokoro(model_path, voices_path)
        samples, sr = kokoro.create(text, voice=VOICE, speed=SPEED, lang=LANG)
        dur = len(samples)/float(sr)

        tokens = re.findall(r"\w+|\s+|[^\w\s]", text, flags=re.UNICODE)
        words  = [t for t in tokens if re.match(r"\w+", t)]
        total  = sum(len(w) for w in words) or 1

        t=0.0; timings=[]
        for i,w in enumerate(words):
            d = dur*(len(w)/total); timings.append({"i":i,"word":w,"start":t,"end":t+d}); t+=d
        if timings: timings[-1]["end"]=dur

        wav_path = writable_path("kokoro.wav"); sf.write(wav_path, samples, sr)
        with open(writable_path("kokoro_words.json"),"w",encoding="utf-8") as f:
            json.dump({"sample_rate":sr,"audio_seconds":dur,"words":timings}, f, ensure_ascii=False, indent=2)
        return wav_path, timings

    def on_play(self, _btn):
        if not self.wav_path: return
        self.media = Gtk.MediaFile.new_for_filename(self.wav_path)
        self.media.set_loop(False); self.media.play()
        self.word_idx=-1; self.t0=time.monotonic()
        if self.tick_id: GLib.source_remove(self.tick_id)
        self.tick_id = GLib.timeout_add(16, self._tick)

    def _tick(self):
        if not self.media.get_playing(): return False
        elapsed = time.monotonic()-self.t0
        while (self.word_idx+1)<len(self.timings) and self.timings[self.word_idx+1]["start"]<=elapsed:
            self.word_idx+=1; self._hl(self.word_idx)
        if self.word_idx>=len(self.timings)-1 and elapsed>=self.timings[-1]["end"]: return False
        return True

    def _hl(self, i:int):
        self.view.evaluate_javascript(f"highlightWord({i});", -1, None, None, None, None); return False

if __name__=="__main__":
    App().run()

