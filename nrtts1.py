
#!/usr/bin/env python3
# GTK4 + RealTimeTTS (Kokoro) with word highlighting — complete, minimal

import gi, os, threading, re
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

try:
    from RealtimeTTS import TextToAudioStream, KokoroEngine
except ImportError:
    print("Please install: python -m pip install -U 'RealtimeTTS[all]'")
    raise

class KokoroTTSApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.kokorotts")
        self.model_path  = os.path.expanduser("~/kokoro-models/kokoro-v1.0.onnx")
        self.voices_path = os.path.expanduser("~/kokoro-models/voices-v1.0.bin")

        self.engine = None
        self.stream = None
        self.is_playing = False
        self.current_text = ""
        self.word_spans = []      # [(start,end,word_clean)]
        self.word_index = 0

        self.text_view = None
        self.text_buffer = None
        self.highlight_tag = None

    # ---------- UI ----------
    def do_activate(self):
        win = Adw.ApplicationWindow(application=self)
        win.set_title("Kokoro TTS (word highlight)")
        win.set_default_size(900, 600)

        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top","bottom","start","end"):
            getattr(v, f"set_margin_{side}")(12)

        # voice picker
        hb = Adw.HeaderBar()
        win.set_titlebar(hb)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(Gtk.Label(label="Voice:"))
        self.voice_combo = Gtk.ComboBoxText()
        for voice in ["af","af_bella","af_nicole","af_sarah","af_sky","am_adam","am_michael","bf_emma","bf_isabella","bm_george","bm_lewis"]:
            self.voice_combo.append_text(voice)
        self.voice_combo.set_active(0)
        box.append(self.voice_combo)
        hb.pack_start(box)

        # text
        frame = Gtk.Frame(label="Text")
        sc = Gtk.ScrolledWindow()
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_margin_top(8); tv.set_margin_bottom(8); tv.set_margin_start(8); tv.set_margin_end(8)
        self.text_view = tv
        self.text_buffer = tv.get_buffer()
        self.highlight_tag = self.text_buffer.create_tag("hl", background="yellow", weight=Pango.Weight.BOLD)
        self.text_buffer.set_text("Welcome to Kokoro TTS. This app highlights each word as it is spoken. Edit this text and press Play.")
        sc.set_child(tv)
        frame.set_child(sc)

        # buttons + status
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.play_btn = Gtk.Button(label="Play")
        self.play_btn.add_css_class("suggested-action")
        self.play_btn.connect("clicked", self.on_play)
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.connect("clicked", self.on_stop)
        self.stop_btn.set_sensitive(False)
        btn_box.append(self.play_btn); btn_box.append(self.stop_btn)

        self.status = Gtk.Label(label="Ready"); self.status.add_css_class("dim-label")

        v.append(frame); v.append(btn_box); v.append(self.status)
        win.set_content(v); win.present()

        self.init_tts()

    # ---------- TTS ----------
    def init_tts(self):
        if not os.path.exists(self.model_path):
            self.set_status(f"Model not found: {self.model_path}"); return
        if not os.path.exists(self.voices_path):
            self.set_status(f"Voices not found: {self.voices_path}); return")

        try:
            self.engine = KokoroEngine(model_path=self.model_path, voices_path=self.voices_path, voice="af")
            self.stream = TextToAudioStream(
                engine=self.engine,
                on_word=self.on_word,  # preferred for highlighting
                on_audio_stream_start=lambda: GLib.idle_add(self.play_state, True),
                on_audio_stream_stop=lambda: GLib.idle_add(self.reset_ui),
            )
            self.set_status("TTS ready")
        except Exception as e:
            self.set_status(f"TTS init error: {e}")

    # ---------- Helpers ----------
    def set_status(self, msg): GLib.idle_add(lambda: self.status.set_text(msg))
    def play_state(self, playing):
        self.play_btn.set_sensitive(not playing)
        self.stop_btn.set_sensitive(playing)
        self.voice_combo.set_sensitive(not playing)

    def on_play(self, _btn):
        if self.is_playing or not self.stream or not self.engine: return
        s = self.text_buffer.get_start_iter(); e = self.text_buffer.get_end_iter()
        text = self.text_buffer.get_text(s, e, False)
        if not text.strip(): self.set_status("No text"); return

        # voice
        v = self.voice_combo.get_active_text()
        try: 
            if v: self.engine.set_voice(v)
        except Exception: pass

        self.current_text = text
        self.build_word_spans(text)
        self.clear_highlight()
        self.word_index = 0
        self.is_playing = True
        self.set_status("Speaking...")

        threading.Thread(target=self._play_thread, daemon=True).start()

    def _play_thread(self):
        try:
            self.stream.feed(self.current_text)
            self.stream.play_async()
        except Exception as e:
            self.set_status(f"Playback error: {e}")
            GLib.idle_add(self.reset_ui)

    def on_stop(self, _btn):
        try:
            if self.stream: self.stream.stop()
        finally:
            self.reset_ui()

    # ---------- Highlighting ----------
    def build_word_spans(self, text):
        # capture words, keep a clean form for matching; include simple punctuation handling
        self.word_spans = []
        for m in re.finditer(r"\S+", text):
            raw = m.group(0)
            clean = re.sub(r"^[^\w']+|[^\w']+$", "", raw).lower()
            self.word_spans.append((m.start(), m.end(), clean if clean else raw.lower()))

    def on_word(self, timing):
        # timing.word should be the spoken word (string)
        if not self.is_playing or not self.word_spans: return
        target = (timing.word or "").strip().lower()
        if not target: return

        i = self.word_index
        # advance until we find the next matching clean word, with small lookahead
        for j in range(i, min(i + 8, len(self.word_spans))):
            s, e, w = self.word_spans[j]
            if w == target:
                self.word_index = j
                GLib.idle_add(self._apply_highlight, s, e)
                return
        # fallback: step forward to keep things moving
        if i < len(self.word_spans):
            s, e, _ = self.word_spans[i]
            self.word_index = i + 1
            GLib.idle_add(self._apply_highlight, s, e)

    def _apply_highlight(self, start, end):
        self.clear_highlight()
        it_s = self.text_buffer.get_iter_at_offset(start)
        it_e = self.text_buffer.get_iter_at_offset(end)
        self.text_buffer.apply_tag(self.highlight_tag, it_s, it_e)
        mark = self.text_buffer.create_mark(None, it_s, False)
        self.text_view.scroll_mark_onscreen(mark)

    def clear_highlight(self):
        s = self.text_buffer.get_start_iter(); e = self.text_buffer.get_end_iter()
        self.text_buffer.remove_tag(self.highlight_tag, s, e)

    # ---------- Reset ----------
    def reset_ui(self):
        self.is_playing = False
        self.play_state(False)
        self.clear_highlight()
        self.set_status("Ready")

    def do_shutdown(self):
        try:
            if self.stream: self.stream.stop()
        finally:
            super().do_shutdown()

def main():
    app = KokoroTTSApp()
    return app.run()

if __name__ == "__main__":
    main()

