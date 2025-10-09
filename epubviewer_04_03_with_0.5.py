#!/usr/bin/env python3
# EPUB viewer with libadwaita + GTK4 ListView sidebar TOC (nested, clickable)
# Integrated TTSEngine + "Read" sidebar tab with sentence-level highlighting.
import gi, os, tempfile, traceback, shutil, urllib.parse, glob, re, json, hashlib
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, GObject, Gdk, GdkPixbuf
import cairo

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import zipfile, pathlib
import threading, time, tempfile as _tempfile

# Optional TTS deps
TTS_AVAILABLE = False
try:
    from kokoro_onnx import Kokoro
    import gi as _gi2
    _gi2.require_version('Gst', '1.0')
    from gi.repository import Gst
    TTS_AVAILABLE = True
    Kokoro = Kokoro
    Gst = Gst
except Exception as e:
    Kokoro = None
    Gst = None
    # not fatal; engine will still be created but kokoro may be None

# --- Safe NCX monkey-patch (avoid crashes on some EPUBs) ---
import ebooklib.epub
def _safe_parse_ncx(self, ncxFile):
    self.book.toc = []
ebooklib.epub.EpubReader._parse_ncx = _safe_parse_ncx

APP_NAME = "EPUB Viewer"
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

# cover target size for sidebar (small)
COVER_W, COVER_H = 70, 100

# persistent library locations & library cover save size
LIBRARY_DIR = os.path.join(GLib.get_user_data_dir(), "epubviewer")
LIBRARY_FILE = os.path.join(LIBRARY_DIR, "library.json")
COVERS_DIR = os.path.join(LIBRARY_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)

# persistent cover saved size (bigger so library shows large covers)
LIB_COVER_W, LIB_COVER_H = 200, 300

def _ensure_library_dir():
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)

def load_library():
    _ensure_library_dir()
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return []
    return []

def save_library(data):
    _ensure_library_dir()
    try:
        with open(LIBRARY_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Error saving library:", e)

# CSS (short) - including tts-highlight styles
_css = """
.epub-sidebar .adw-action-row { margin: 5px; padding: 6px; border-radius: 8px; background-color: transparent; }
.epub-sidebar .adw-action-row:hover { background-color: rgba(0,0,0,0.06); }
.epub-sidebar .adw-action-row.selected { background-color: rgba(0,0,0,0.12); }
.book-title { font-weight: 600; margin-bottom: 2px; }
.book-author { color: rgba(0,0,0,0.6); font-size: 12px; }
.tts-highlight { background: rgba(255,255,100,0.6) !important; box-shadow: 0 0 0 3px rgba(255,200,0,0.7) !important; border-radius: 4px !important; padding: 2px 4px !important; transition: background 0.2s ease, box-shadow 0.2s ease !important; }
"""
_css_provider = Gtk.CssProvider()
_css_provider.load_from_data(_css.encode("utf-8"))
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _css_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
)

# (remaining library CSS and hover providers omitted for brevity — keep same as yours)
_LIBRARY_CSS = b"""
.library-grid { padding: 1px; }
.library-card { background-color: transparent; border-radius: 10px; padding-top: 10px; padding-bottom: 5px; box-shadow: none; border: none; }
.library-card .cover { margin-top: 0px; margin-bottom: 5px; margin-left: 10px;  margin-right: 10px; border-radius: 10px; }
.library-card .title { font-weight: 600; font-size: 12px; line-height: 1.2; color: @theme_fg_color; }
.library-card .author { font-size: 10px; opacity: 0.7; color: @theme_fg_color; }
.library-card .meta { font-size: 9px; font-weight: 500; opacity: 0.6; color: @theme_fg_color; }
.library-card.active { border: 2px solid #ffcc66; box-shadow: 0 6px 18px rgba(255,204,102,0.15); }
"""
_cssp = Gtk.CssProvider()
_cssp.load_from_data(_LIBRARY_CSS)
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    _cssp,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
)

# theme injection CSS for content (keeps tts-highlight)
THEME_INJECTION_CSS = """
@media (prefers-color-scheme: dark) {
    body { background-color:#242424; color:#e3e3e3; }
    blockquote { border-left-color:#62a0ea; }
}
"""

# TocItem and helper from your code (kept unchanged)
class TocItem(GObject.Object):
    title = GObject.Property(type=str)
    href = GObject.Property(type=str)
    index = GObject.Property(type=int, default=-1)
    def __init__(self, title, href="", index=-1, children=None):
        super().__init__()
        self.title = title or ""
        self.href = href or ""
        self.index = index if isinstance(index, int) else -1
        self.children = Gio.ListStore(item_type=TocItem)
        if children:
            for c in children:
                self.children.append(c)

def highlight_markup(text: str, query: str) -> str:
    if not query:
        return GLib.markup_escape_text(text or "")
    q = re.escape(query)
    parts = []
    last = 0
    esc_text = text or ""
    for m in re.finditer(q, esc_text, flags=re.IGNORECASE):
        start, end = m.start(), m.end()
        parts.append(GLib.markup_escape_text(esc_text[last:start]))
        match = GLib.markup_escape_text(esc_text[start:end])
        parts.append(f'<span background="#ffd54f" foreground="#000000"><b>{match}</b></span>')
        last = end
    parts.append(GLib.markup_escape_text(esc_text[last:]))
    return "".join(parts)

# -------------------------
# TTSEngine (copied & slightly trimmed for integration)
# -------------------------
class TTSEngine:
    def __init__(self, webview_getter, base_temp_dir=None, kokoro_model_path=None, voices_bin_path=None):
        self.webview_getter = webview_getter
        self.base_temp_dir = base_temp_dir or tempfile.gettempdir()
        self.kokoro = None
        self.is_playing_flag = False
        self.should_stop = False
        self.current_thread = None

        # Playback / navigation state
        self._tts_sentences = []
        self._tts_sids = []
        self._tts_voice = None
        self._tts_speed = 1.0
        self._tts_lang = "en-us"
        self._tts_finished_callback = None
        self._tts_highlight_callback = None

        # index and audio cache
        self._current_play_index = 0
        self._audio_files = {}
        self._audio_lock = threading.Lock()
        self._synthesis_done = threading.Event()

        # delayed on-demand synth timer
        self._delayed_timer = None
        self._delayed_timer_lock = threading.Lock()

        # paused state
        self.paused = False
        self._resume_event = threading.Event()
        self._resume_event.set()

        # init kokoro if available
        if TTS_AVAILABLE and Kokoro:
            try:
                model_path = os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_path = os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                if os.path.exists(model_path) and os.path.exists(voices_path):
                    self.kokoro = Kokoro(model_path, voices_path)
                    print("[info] Kokoro TTS initialized")
                else:
                    print(f"[warn] Kokoro models not found at {model_path}")
            except Exception as e:
                print(f"[error] Failed to initialize Kokoro: {e}")
                self.kokoro = None

        # Initialize GStreamer if available
        try:
            if TTS_AVAILABLE and Gst:
                Gst.init(None)
                self.player = Gst.ElementFactory.make("playbin", "player")
                bus = self.player.get_bus()
                bus.add_signal_watch()
                bus.connect("message", self.on_gst_message)
                self.playback_finished = False
            else:
                self.player = None
                self.playback_finished = True
        except Exception as e:
            print(f"[warn] GStreamer init failed: {e}")
            self.player = None
            self.playback_finished = True

    def is_playing(self):
        return bool(self.is_playing_flag) and not bool(self.paused)

    def is_paused(self):
        return bool(self.paused)

    def on_gst_message(self, bus, message):
        try:
            t = message.type
            if t == Gst.MessageType.EOS:
                if self.player:
                    self.player.set_state(Gst.State.NULL)
                self.playback_finished = True
            elif t == Gst.MessageType.ERROR:
                if self.player:
                    self.player.set_state(Gst.State.NULL)
                err, debug = message.parse_error()
                print(f"[error] GStreamer error: {err}, {debug}")
                self.playback_finished = True
        except Exception as e:
            print("on_gst_message error:", e)

    def split_sentences(self, text):
        sentences = re.split(r'([.!?]+(?:\s+|$))', text)
        result = []
        for i in range(0, len(sentences)-1, 2):
            sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else '')
            sentence = sentence.strip()
            if sentence:
                result.append(sentence)
        if len(sentences) % 2 == 1 and sentences[-1].strip():
            result.append(sentences[-1].strip())
        return result

    def synthesize_sentence(self, sentence, voice, speed, lang):
        if not self.kokoro:
            return None
        try:
            base = self.base_temp_dir or tempfile.gettempdir()
            try:
                os.makedirs(base, exist_ok=True)
            except Exception:
                base = tempfile.gettempdir()

            samples, sample_rate = self.kokoro.create(sentence, voice=voice, speed=speed, lang=lang)

            ntf = _tempfile.NamedTemporaryFile(prefix="tts_", suffix=".wav", delete=False, dir=base)
            ntf_name = ntf.name
            ntf.close()
            try:
                import soundfile as sf
                sf.write(ntf_name, samples, sample_rate)
            except Exception as e:
                print("[error] writing wav failed:", e)
                return None
            return ntf_name
        except Exception as e:
            print(f"[error] Synthesis error: {e}")
            return None

    # (speak_sentences_list and other methods kept unchanged — truncated here for brevity)
    # Full implementations follow in part 2 to keep message manageable.
# Continuation: TTSEngine rest + EPubViewer integration + application main
# (Start by defining the remaining TTSEngine methods that were kept in the reference.)

    def _cancel_delayed_timer(self):
        with self._delayed_timer_lock:
            if self._delayed_timer:
                try:
                    self._delayed_timer.cancel()
                except Exception:
                    pass
                self._delayed_timer = None

    def _schedule_delayed_synthesis(self, idx, delay=0.5):
        self._cancel_delayed_timer()
        def timer_cb():
            try:
                if self.should_stop:
                    return
                with self._audio_lock:
                    if self._audio_files.get(idx):
                        return
                if idx != self._current_play_index:
                    return
                audio_file = self.synthesize_sentence(self._tts_sentences[idx], self._tts_voice, self._tts_speed, self._tts_lang)
                if audio_file:
                    with self._audio_lock:
                        self._audio_files[idx] = audio_file
            except Exception as e:
                print(f"[error] delayed synthesis: {e}")
            finally:
                with self._delayed_timer_lock:
                    self._delayed_timer = None
        timer = threading.Timer(delay, timer_cb)
        with self._delayed_timer_lock:
            self._delayed_timer = timer
        timer.daemon = True
        timer.start()

    def speak_sentences_list(self, sentences_with_meta, voice="af_sarah", speed=1.0, lang="en-us", highlight_callback=None, finished_callback=None):
        if not self.kokoro:
            print("[warn] TTS not available")
            if finished_callback:
                GLib.idle_add(finished_callback)
            return

        self.stop()
        time.sleep(0.05)

        self.should_stop = False
        self._tts_sentences = []
        self._tts_sids = []
        for s in sentences_with_meta:
            if isinstance(s, dict):
                self._tts_sids.append(s.get("sid"))
                self._tts_sentences.append(s.get("text"))
            else:
                self._tts_sids.append(None)
                self._tts_sentences.append(str(s))

        self._tts_voice = voice
        self._tts_speed = speed
        self._tts_lang = lang
        self._tts_finished_callback = finished_callback
        self._tts_highlight_callback = highlight_callback
        self._audio_files = {}
        self._current_play_index = 0
        self._synthesis_done.clear()
        self._cancel_delayed_timer()
        self.paused = False
        self._resume_event.set()

        def tts_thread():
            try:
                total = len(self._tts_sentences)
                def synthesis_worker():
                    try:
                        synth_idx = 0
                        while not self.should_stop and synth_idx < total:
                            with self._audio_lock:
                                cur = self._current_play_index
                            if synth_idx < cur:
                                synth_idx = cur
                            lookahead_limit = cur + (1 if self.paused else 3)
                            if synth_idx > lookahead_limit:
                                time.sleep(0.05); continue
                            with self._audio_lock:
                                if self._audio_files.get(synth_idx):
                                    synth_idx += 1; continue
                            if synth_idx <= lookahead_limit:
                                if self.should_stop: break
                                audio_file = self.synthesize_sentence(self._tts_sentences[synth_idx], self._tts_voice, self._tts_speed, self._tts_lang)
                                if audio_file:
                                    with self._audio_lock:
                                        if synth_idx not in self._audio_files:
                                            self._audio_files[synth_idx] = audio_file
                                synth_idx += 1
                            else:
                                time.sleep(0.05)
                        self._synthesis_done.set()
                    except Exception as e:
                        print(f"[error] Synthesis worker: {e}")
                        self._synthesis_done.set()

                synth_thread = threading.Thread(target=synthesis_worker, daemon=True)
                synth_thread.start()

                self.is_playing_flag = True

                while self._current_play_index < len(self._tts_sentences) and not self.should_stop:
                    idx = self._current_play_index

                    if self._tts_highlight_callback:
                        GLib.idle_add(self._tts_highlight_callback, idx, {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]})

                    while self.paused and not self.should_stop:
                        self._cancel_delayed_timer()
                        self._resume_event.wait(0.1)

                    if self.should_stop:
                        break

                    audio_file = None
                    with self._audio_lock:
                        audio_file = self._audio_files.get(idx)

                    if not audio_file:
                        self._schedule_delayed_synthesis(idx, delay=0.5)
                        waited = 0.0
                        while not self.should_stop:
                            with self._audio_lock:
                                audio_file = self._audio_files.get(idx)
                            if audio_file:
                                break
                            if self._current_play_index != idx:
                                break
                            time.sleep(0.02); waited += 0.02
                            if self._synthesis_done.is_set() and waited > 0.5:
                                break

                    if self.should_stop:
                        break

                    with self._audio_lock:
                        audio_file = self._audio_files.get(idx)

                    if not audio_file:
                        audio_file = self.synthesize_sentence(self._tts_sentences[idx], self._tts_voice, self._tts_speed, self._tts_lang)
                        if audio_file:
                            with self._audio_lock:
                                self._audio_files[idx] = audio_file

                    if not audio_file:
                        print(f"[warn] No audio for {idx}, skipping")
                        self._current_play_index = idx + 1
                        continue

                    if self.paused:
                        continue

                    if self.player:
                        try:
                            self.player.set_property("uri", f"file://{audio_file}")
                            self.player.set_state(Gst.State.PLAYING)
                            self.playback_finished = False
                        except Exception as e:
                            print("player error:", e)
                            self.playback_finished = True
                    else:
                        self.playback_finished = True
                        time.sleep(0.05)

                    while not self.playback_finished and not self.should_stop:
                        if self._current_play_index != idx:
                            break
                        if self.paused:
                            try:
                                if self.player:
                                    self.player.set_state(Gst.State.NULL)
                            except Exception:
                                pass
                            break
                        time.sleep(0.02)

                    try:
                        if self.player:
                            self.player.set_state(Gst.State.NULL)
                    except Exception:
                        pass

                    if (self._current_play_index == idx) and (not self.paused):
                        try:
                            with self._audio_lock:
                                af = self._audio_files.get(idx)
                                if af:
                                    try: os.remove(af)
                                    except Exception: pass
                                    try: del self._audio_files[idx]
                                    except Exception: pass
                        except Exception:
                            pass
                        self._current_play_index = idx + 1

                self.is_playing_flag = False
                self._cancel_delayed_timer()
                if self._tts_highlight_callback and not self.should_stop:
                    GLib.idle_add(self._tts_highlight_callback, -1, {"sid": None, "text": ""})
                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

            except Exception as e:
                print(f"[error] TTS thread: {e}")
                import traceback
                traceback.print_exc()
                if self._tts_finished_callback:
                    GLib.idle_add(self._tts_finished_callback)

        self.current_thread = threading.Thread(target=tts_thread, daemon=True)
        self.current_thread.start()

    def next_sentence(self):
        if not self._tts_sentences:
            return
        with self._audio_lock:
            self._current_play_index = min(len(self._tts_sentences)-1, self._current_play_index + 1)
            idx = self._current_play_index
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx, {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]})
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def prev_sentence(self):
        if not self._tts_sentences:
            return
        with self._audio_lock:
            self._current_play_index = max(0, self._current_play_index - 1)
            idx = self._current_play_index
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx, {"sid": self._tts_sids[idx], "text": self._tts_sentences[idx]})
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def pause(self):
        self.paused = True
        self._resume_event.clear()
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass

    def resume(self):
        self.paused = False
        self._resume_event.set()
        self._cancel_delayed_timer()

    def stop(self):
        self.should_stop = True
        self.paused = False
        self.playback_finished = True
        try:
            self._resume_event.set()
        except Exception:
            pass
        self._cancel_delayed_timer()
        if self.player:
            try:
                self.player.set_state(Gst.State.NULL)
            except Exception:
                pass
        self.is_playing_flag = False
        if self.current_thread:
            try:
                self.current_thread.join(timeout=1.0)
            except Exception:
                pass
        try:
            with self._audio_lock:
                for idx, path in list(self._audio_files.items()):
                    try:
                        if path and os.path.exists(path):
                            os.remove(path)
                    except Exception:
                        pass
                self._audio_files.clear()
        except Exception:
            pass

# -------------------------
# EPubViewer (only important parts touched: TTS setup, sidebar read tab, display_page injection, handlers)
# -------------------------
class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 800)
        self.set_title(APP_NAME)

        # state
        self.book = None
        self.items = []
        self.item_map = {}
        self.current_index = 0
        self.temp_dir = None
        self.css_content = ""
        self._toc_actrows = {}
        self._tab_buttons = []
        self.href_map = {}
        self.last_cover_path = None
        self.book_path = None

        self._column_gap = 32                # px gap between columns
        
        # library
        self.library = load_library()
        self.library_search_text = ""
        self._lib_search_handler_id = None

        # main layout and sidebar setup (kept largely unchanged)
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_vbox)
        self.split = Adw.OverlaySplitView(show_sidebar=True)
        self.split.set_sidebar_width_fraction(0.32)
        main_vbox.append(self.split)

        # --- Sidebar ---
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.add_css_class("epub-sidebar")
        header = Adw.HeaderBar(); header.add_css_class("flat")
        self.library_btn = Gtk.Button(icon_name="show-library-symbolic"); self.library_btn.add_css_class("flat")
        self.library_btn.set_tooltip_text("Show Library"); self.library_btn.connect("clicked", self.on_library_clicked)
        header.pack_start(self.library_btn)
        title_lbl = Gtk.Label(label=APP_NAME); title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_title_widget(title_lbl); sidebar_box.append(header)

        # Book cover + metadata
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        book_box.set_valign(Gtk.Align.START)
        book_box.set_margin_top(6); book_box.set_margin_bottom(6)
        book_box.set_margin_start(8); book_box.set_margin_end(8)
        self.cover_image = Gtk.Image()
        placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
        placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
        self.cover_image.set_from_paintable(placeholder_tex)
        try:
            self.cover_image.set_size_request(COVER_W, COVER_H)
        except Exception:
            pass
        book_box.append(self.cover_image)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4); text_box.set_valign(Gtk.Align.CENTER); text_box.set_hexpand(True)
        self.book_title = Gtk.Label(label=""); self.book_title.add_css_class("book-title")
        self.book_title.set_halign(Gtk.Align.START); self.book_title.set_xalign(0.0)
        self.book_author = Gtk.Label(label=""); self.book_author.add_css_class("book-author")
        text_box.append(self.book_title); text_box.append(self.book_author)
        book_box.append(text_box)
        sidebar_box.append(book_box)

        # side stack (toc, annotations, bookmarks, + read)
        self.side_stack = Gtk.Stack(); self.side_stack.set_vexpand(True)

        # TOC ListView (kept)
        self.toc_factory = Gtk.SignalListItemFactory()
        self.toc_factory.connect("setup", self._toc_on_setup)
        self.toc_factory.connect("bind", self._toc_on_bind)
        self.toc_root_store = Gio.ListStore(item_type=TocItem)
        self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview = Gtk.ListView(model=self.toc_sel, factory=self.toc_factory)
        self.toc_listview.set_vexpand(True)
        toc_scrolled = Gtk.ScrolledWindow(); toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); toc_scrolled.set_vexpand(True)
        toc_scrolled.set_child(self.toc_listview)
        self.side_stack.add_titled(toc_scrolled, "toc", "TOC")

        ann_list = Gtk.ListBox(); ann_list.append(Gtk.Label(label="No annotations"))
        ann_scrolled = Gtk.ScrolledWindow(); ann_scrolled.set_child(ann_list)
        self.side_stack.add_titled(ann_scrolled, "annotations", "Annotations")

        bm_list = Gtk.ListBox(); bm_list.append(Gtk.Label(label="No bookmarks"))
        bm_scrolled = Gtk.ScrolledWindow(); bm_scrolled.set_child(bm_list)
        self.side_stack.add_titled(bm_scrolled, "bookmarks", "Bookmarks")

        # --- Read tab (TTS controls) ---
        read_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        read_box.set_margin_top(6); read_box.set_margin_bottom(6); read_box.set_margin_start(6); read_box.set_margin_end(6)
        read_box.set_hexpand(True)
        # simple label
        read_box.append(Gtk.Label(label="Read (TTS)"))
        # TTS control row
        tts_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tts_controls.set_halign(Gtk.Align.CENTER)
        # previous
        self.tts_prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic"); self.tts_prev_btn.add_css_class("flat")
        self.tts_prev_btn.set_tooltip_text("Previous sentence"); self.tts_prev_btn.set_sensitive(False)
        self.tts_prev_btn.connect("clicked", lambda b: self._tts_prev())
        tts_controls.append(self.tts_prev_btn)
        # play
        self.tts_play_btn = Gtk.Button(icon_name="media-playback-start-symbolic"); self.tts_play_btn.add_css_class("flat")
        self.tts_play_btn.set_tooltip_text("Play from current chapter"); self.tts_play_btn.set_sensitive(False)
        self.tts_play_btn.connect("clicked", lambda b: self._tts_play())
        tts_controls.append(self.tts_play_btn)
        # pause/resume
        self.tts_pause_btn = Gtk.Button(icon_name="media-playback-pause-symbolic"); self.tts_pause_btn.add_css_class("flat")
        self.tts_pause_btn.set_tooltip_text("Pause/Resume"); self.tts_pause_btn.set_sensitive(False)
        self.tts_pause_btn.connect("clicked", lambda b: self._tts_pause_toggle())
        tts_controls.append(self.tts_pause_btn)
        # stop
        self.tts_stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic"); self.tts_stop_btn.add_css_class("flat")
        self.tts_stop_btn.set_tooltip_text("Stop"); self.tts_stop_btn.set_sensitive(False)
        self.tts_stop_btn.connect("clicked", lambda b: self._tts_stop())
        tts_controls.append(self.tts_stop_btn)
        # next
        self.tts_next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic"); self.tts_next_btn.add_css_class("flat")
        self.tts_next_btn.set_tooltip_text("Next sentence"); self.tts_next_btn.set_sensitive(False)
        self.tts_next_btn.connect("clicked", lambda b: self._tts_next())
        tts_controls.append(self.tts_next_btn)
        read_box.append(tts_controls)
        self.side_stack.add_titled(read_box, "read", "Read")

        sidebar_box.append(self.side_stack)

        # bottom tabs (toc, ann, bookmarks, read)
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs_box.set_margin_top(6); tabs_box.set_margin_bottom(6)
        tabs_box.set_margin_start(6); tabs_box.set_margin_end(6)
        def make_icon_tab(icon_name, tooltip, name):
            b = Gtk.ToggleButton(); b.add_css_class("flat")
            img = Gtk.Image.new_from_icon_name(icon_name)
            b.set_child(img); b.set_tooltip_text(tooltip); b.set_hexpand(True)
            self._tab_buttons.append((b, name))
            def on_toggled(btn, nm=name):
                if btn.get_active():
                    for sib, _nm in self._tab_buttons:
                        if sib is not btn:
                            try: sib.set_active(False)
                            except Exception: pass
                    self.side_stack.set_visible_child_name(nm)
            b.connect("toggled", on_toggled)
            return b
        self.tab_toc = make_icon_tab("view-list-symbolic", "TOC", "toc")
        self.tab_ann = make_icon_tab("document-edit-symbolic", "Annotations", "annotations")
        self.tab_bm  = make_icon_tab("user-bookmarks-symbolic", "Bookmarks", "bookmarks")
        self.tab_read = make_icon_tab("media-playback-start-symbolic", "Read (TTS)", "read")
        self.tab_toc.set_active(True)
        tabs_box.append(self.tab_toc); tabs_box.append(self.tab_ann); tabs_box.append(self.tab_bm); tabs_box.append(self.tab_read)
        sidebar_box.append(tabs_box)

        self.split.set_sidebar(sidebar_box)

        # --- Content area ---
        self.toolbar = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar(); self.content_header.add_css_class("flat")
        self.content_sidebar_toggle = Gtk.Button(); self.content_sidebar_toggle.add_css_class("flat")
        self._sidebar_img = Gtk.Image.new_from_icon_name("sidebar-show-symbolic")
        self.content_sidebar_toggle.set_child(self._sidebar_img)
        self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
        self.content_sidebar_toggle.connect("clicked", self._on_sidebar_toggle)
        self.content_header.pack_start(self.content_sidebar_toggle)

        self.open_btn = Gtk.Button(icon_name="document-open-symbolic"); self.open_btn.add_css_class("flat")
        self.open_btn.set_tooltip_text("Open EPUB"); self.open_btn.connect("clicked", self.open_file)
        self.content_header.pack_start(self.open_btn)
        self.content_title_label = Gtk.Label(label=APP_NAME)
        self.content_title_label.set_ellipsize(Pango.EllipsizeMode.END); self.content_title_label.set_max_width_chars(48)
        self.content_header.set_title_widget(self.content_title_label)

        self.library_search_revealer = Gtk.Revealer(reveal_child=False)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_bar.set_margin_start(6); search_bar.set_margin_end(6); search_bar.set_margin_top(6); search_bar.set_margin_bottom(6)
        self.library_search_entry = Gtk.SearchEntry()
        self.library_search_entry.set_placeholder_text("Search library (title, author, filename)")
        self._lib_search_handler_id = self.library_search_entry.connect("search-changed", lambda e: self._on_library_search_changed(e.get_text()))
        search_bar.append(self.library_search_entry)
        self.library_search_revealer.set_child(search_bar)

        self.search_toggle_btn = Gtk.Button(icon_name="system-search-symbolic"); self.search_toggle_btn.add_css_class("flat")
        self.search_toggle_btn.set_tooltip_text("Search library"); self.search_toggle_btn.connect("clicked", self._toggle_library_search)
        self.content_header.pack_end(self.search_toggle_btn)

        menu_model = Gio.Menu(); menu_model.append("About", "app.about")
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic"); self.menu_btn.set_popover(Gtk.PopoverMenu.new_from_model(menu_model))
        self.content_header.pack_end(self.menu_btn)

        self.toolbar.add_top_bar(self.content_header)
        self.toolbar.add_top_bar(self.library_search_revealer)

        # scrolled and bottom nav
        self.scrolled = Gtk.ScrolledWindow(); self.scrolled.set_vexpand(True)
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_bar.set_margin_top(6); bottom_bar.set_margin_bottom(6); bottom_bar.set_margin_start(6); bottom_bar.set_margin_end(6)
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic"); self.prev_btn.add_css_class("flat")
        self.prev_btn.set_sensitive(False); self.prev_btn.connect("clicked", self.prev_page)
        bottom_bar.append(self.prev_btn)
        self.progress = Gtk.ProgressBar(); self.progress.set_show_text(True); self.progress.set_hexpand(True)
        bottom_bar.append(self.progress)
        self.next_btn = Gtk.Button(icon_name="go-next-symbolic"); self.next_btn.add_css_class("flat")
        self.next_btn.set_sensitive(False); self.next_btn.connect("clicked", self.next_page)
        bottom_bar.append(self.next_btn)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); content_box.set_vexpand(True)
        content_box.append(self.scrolled); content_box.append(bottom_bar)
        self._reader_content_box = content_box
        self.toolbar.set_content(content_box)
        self.split.set_content(self.toolbar)

        # WebKit fallback
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit
            self.WebKit = WebKit
            self.webview = WebKit.WebView()
            self.scrolled.set_child(self.webview)
            try: self.webview.connect("decide-policy", self.on_decide_policy)
            except Exception: pass
        except Exception:
            self.WebKit = None
            self.webview = None
            self.textview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)

        # responsive & snap features (kept)
        try:
            bp = Adw.Breakpoint()
            try: bp.set_condition("max-width: 400sp")
            except Exception: pass
            try: bp.add_setter(self.split, "collapsed", True)
            except Exception: pass
            try: self.add(bp)
            except Exception: pass
        except Exception:
            def on_size_allocate(win, alloc):
                try:
                    w = alloc.width
                    collapsed = w < 400
                    if getattr(self.split, "get_collapsed", None):
                        if self.split.get_collapsed() != collapsed:
                            self.split.set_collapsed(collapsed)
                    else:
                        self.split.set_show_sidebar(not collapsed)
                except Exception:
                    pass
            self.connect("size-allocate", on_size_allocate)

        self._setup_responsive_sidebar(); self._setup_window_size_constraints()
        self.content_sidebar_toggle.set_visible(False); self.split.set_show_sidebar(False); self.split.set_collapsed(False)
        self.open_btn.set_visible(True); self.search_toggle_btn.set_visible(True)
        self.show_library()

        # TTS engine init (base temp dir uses app temp dir; kokoro optional)
        try:
            base_tmp = tempfile.gettempdir()
            self.tts = TTSEngine(webview_getter=lambda: self.webview, base_temp_dir=base_tmp)
        except Exception as e:
            print("TTS engine init failed:", e)
            self.tts = None

        # periodic update of TTS button states
        GLib.timeout_add(400, self._update_tts_button_states)

    # ---------- minimal TTS control methods ----------
    def _update_tts_button_states(self):
        # enable play if webview + there is content
        has_web = bool(self.webview)
        has_book = bool(self.book and self.items)
        enable = has_web and has_book
        try:
            self.tts_play_btn.set_sensitive(enable)
            self.tts_stop_btn.set_sensitive(enable and getattr(self.tts, "is_playing", lambda:False)())
            self.tts_pause_btn.set_sensitive(enable and getattr(self.tts, "is_playing", lambda:False)())
            self.tts_prev_btn.set_sensitive(enable)
            self.tts_next_btn.set_sensitive(enable)
        except Exception:
            pass
        return True

    def _collect_sentences_for_current_item(self):
        """Return list of {'sid': idx, 'text': sentence} for the current item."""
        if not self.book or not self.items or self.current_index >= len(self.items):
            return []
        item = self.items[self.current_index]
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            # extract visible text
            body = soup.find("body")
            text = body.get_text(" ", strip=True) if body else soup.get_text(" ", strip=True)
            sentences = self.tts.split_sentences(text) if self.tts else re.split(r'([.!?]+(?:\s+|$))', text)
            # ensure we use same splitting as engine
            out = []
            for i, s in enumerate(sentences):
                if isinstance(s, str) and s.strip():
                    out.append({"sid": i, "text": s.strip()})
            return out
        except Exception:
            return []

    def _tts_play(self):
        if not self.tts:
            print("[tts] engine unavailable")
            return
        sentences = self._collect_sentences_for_current_item()
        if not sentences:
            print("[tts] no sentences")
            return
        # ensure page is wrapped into sentence spans (JS) before starting
        try:
            GLib.timeout_add(250, lambda: self._ensure_sentence_wrapping_and_start(sentences))
        except Exception:
            # fallback: start directly
            self.tts.speak_sentences_list(sentences, highlight_callback=self._on_tts_highlight, finished_callback=self._on_tts_finished)

    def _ensure_sentence_wrapping_and_start(self, sentences):
        # Inject JS that replaces content inside .ebook-content with sentence spans (simple but effective).
        if not self.webview:
            self.tts.speak_sentences_list(sentences, highlight_callback=self._on_tts_highlight, finished_callback=self._on_tts_finished)
            return False
        js_split = r"""
        (function(){
          try{
            var cont = document.querySelector('.ebook-content');
            if(!cont) return;
            function splitSentences(txt){
              var re = /([.!?]+(?:\s+|$))/g;
              var parts = txt.split(re);
              var out = [];
              for(var i=0;i<parts.length-1;i+=2){
                var s = (parts[i] + (parts[i+1]||'')).trim();
                if(s) out.push(s);
              }
              if(parts.length%2===1 && parts[parts.length-1].trim()) out.push(parts[parts.length-1].trim());
              return out;
            }
            // build plain text sequence by walking child nodes to avoid losing structure entirely:
            var plain = cont.innerText || cont.textContent || '';
            var sents = splitSentences(plain);
            if(!sents || !sents.length) return;
            var html = '';
            for(var i=0;i<sents.length;i++){
              var esc = sents[i].replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
              html += '<span class="tts-sentence" data-sid="'+i+'">'+esc+'</span> ';
            }
            cont.innerHTML = html;
          }catch(e){ console.log('sentence wrap error', e); }
        })();
        """
        try:
            # try newer evaluate_javascript API
            try:
                self.webview.evaluate_javascript(js_split, -1, None, None, None, None, None)
            except Exception:
                try:
                    # fallback run_javascript (older)
                    self.webview.run_javascript(js_split, None, None, None)
                except Exception:
                    pass
        except Exception:
            pass
        # start after small delay to let script run
        GLib.timeout_add(250, lambda: (self.tts.speak_sentences_list(sentences, highlight_callback=self._on_tts_highlight, finished_callback=self._on_tts_finished), False)[1])
        return False

    def _on_tts_highlight(self, idx, meta):
        # idx == -1 => clear highlight
        if not self.webview:
            return
        try:
            if idx is None or idx < 0:
                js = "(function(){document.querySelectorAll('.tts-highlight').forEach(e=>e.classList.remove('tts-highlight'));})();"
            else:
                js = f"""(function(){{
                  try {{
                    document.querySelectorAll('.tts-highlight').forEach(e=>e.classList.remove('tts-highlight'));
                    var el = document.querySelector('[data-sid="{idx}"]');
                    if(el){{ el.classList.add('tts-highlight'); el.scrollIntoView({{behavior:'smooth', block:'center'}}); }}
                  }} catch(e){{ console.log('highlight err', e); }}
                }})();"""
            try:
                self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js, None, None, None)
                except Exception:
                    pass
        except Exception as e:
            print("highlight callback error:", e)

    def _on_tts_finished(self):
        # clear highlight
        self._on_tts_highlight(-1, {"sid": None, "text": ""})

    def _tts_pause_toggle(self):
        if not self.tts:
            return
        try:
            if getattr(self.tts, "is_paused", lambda: False)():
                self.tts.resume()
            else:
                self.tts.pause()
        except Exception:
            pass

    def _tts_stop(self):
        try:
            if self.tts: self.tts.stop()
            self._on_tts_finished()
        except Exception:
            pass

    def _tts_next(self):
        try:
            if self.tts: self.tts.next_sentence()
        except Exception:
            pass

    def _tts_prev(self):
        try:
            if self.tts: self.tts.prev_sentence()
        except Exception:
            pass

    # ---------- display_page override to keep injecting THEME_INJECTION_CSS and schedule sentence wrapping ----------
    def display_page(self, fragment=None):
        if not self.book or not self.items or self.current_index >= len(self.items): return
        if not self.css_content: self.extract_css()
        item = self.items[self.current_index]
        if not item or not hasattr(item, 'get_content'): return
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            for tag in soup.find_all(['style', 'link']): tag.decompose()
            body = soup.find("body")
            if body:
                body_attrs = ' '.join([f'{k}="{v}"' if isinstance(v, str) else f'{k}="{" ".join(v)}"' for k, v in body.attrs.items()])
                if body_attrs:
                    content = f'<div {body_attrs}>{"".join(str(child) for child in body.children)}</div>'
                else:
                    content = "".join(str(child) for child in body.children)
            else:
                content = str(soup)
            base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
            # include theme + tts styles
            page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS
            wrapped_html = self._wrap_html(content, base_uri)
            if self.webview:
                self.webview.load_html(wrapped_html, base_uri)
                # schedule wrapping + small delay for JS
                GLib.timeout_add(300, lambda: self._ensure_sentence_wrapping_and_start([]))
            else:
                buf = self.textview.get_buffer(); buf.set_text(soup.get_text())
            total = len(self.items)
            self.progress.set_fraction((self.current_index + 1) / total)
            self.progress.set_text(f"{self.current_index + 1}/{total}")
            try:
                for ti in list(self.href_map.values()):
                    if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == self.current_index:
                        self._set_toc_selected(ti); break
            except Exception: pass
            self._save_progress_for_library()
        except Exception as e:
            print(f"Error displaying page: {e}"); self.show_error(f"Error displaying page: {e}")

    # (rest of class methods—TOC setup, library UI, open_file, load_epub, extract_css, cleanup etc.—keep from your original file)
    # To keep this reply focused and minimal I assume you will keep the rest of your existing methods unchanged.
    # If you want, I can produce the full file with every unchanged method copied verbatim — but it's already present in your original code.

    def on_library_clicked(self, *_):
        # if reading, keep book in memory and show library with loaded book prioritized
        if getattr(self, "book", None):
            try:
                self.content_sidebar_toggle.set_visible(False)
                self.split.set_show_sidebar(False)
                self.split.set_collapsed(False)
            except Exception:
                pass
        self.show_library()

    def _on_sidebar_toggle(self, btn):
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            if not new:
                self._user_hid_sidebar = True
            else:
                self._user_hid_sidebar = False
        except Exception:
            pass
    # ---- search helpers ----
    def _toggle_library_search(self, *_):
        reveal = not self.library_search_revealer.get_reveal_child()
        self.library_search_revealer.set_reveal_child(reveal)
        if not reveal:
            try:
                if self._lib_search_handler_id:
                    self.library_search_entry.handler_block(self._lib_search_handler_id)
                self.library_search_entry.set_text("")
                self.library_search_text = ""
                self.show_library()
            finally:
                try:
                    if self._lib_search_handler_id:
                        self.library_search_entry.handler_unblock(self._lib_search_handler_id)
                except Exception:
                    pass
        else:
            self.library_search_entry.grab_focus()

    def _safe_set_search_text(self, text: str):
        try:
            if text is None:
                text = ""
            if getattr(self, "library_search_entry", None) and self.library_search_entry.get_has_focus():
                return
            cur = ""
            try:
                cur = self.library_search_entry.get_text() or ""
            except Exception:
                cur = ""
            if cur == text:
                return
            try:
                self.library_search_entry.set_text(text)
                pos = len(text)
                try: self.library_search_entry.set_position(pos)
                except Exception: pass
            except Exception:
                pass
        except Exception:
            pass

    def _on_library_search_changed(self, arg):
        try:
            if isinstance(arg, str):
                text = arg
            else:
                text = arg.get_text() if hasattr(arg, "get_text") else str(arg or "")
            self.library_search_text = (text or "").strip()
            self.show_library()
        except Exception:
            pass


    def _setup_responsive_sidebar(self):
        self._responsive_enabled = False
        self._last_width = 0
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self.connect("notify::default-width", self._on_window_size_changed)

    def _setup_window_size_constraints(self):
        self._is_snapping = False
        self._snap_timeout_id = None
        self.connect("notify::default-width", self._on_window_width_changed)
    def _on_window_size_changed(self, *args):
        try:
            if self._user_hid_sidebar:
                return
            width = self.get_width()
            if abs(width - self._last_width) < 10:
                return
            self._last_width = width
            is_narrow = width < 768
            if is_narrow == self._last_was_narrow:
                return
            self._last_was_narrow = is_narrow
            if self._responsive_enabled and self.book and self.book_path:
                if is_narrow:
                    self.split.set_collapsed(True)
                else:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(True)
            else:
                if self._last_was_narrow is not None:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(False)
        except Exception as e:
            print(f"Error in window size handler: {e}")
            

    def _on_window_width_changed(self, *args):
        if self._responsive_enabled and self.book and self.book_path:
            return
        if self._snap_timeout_id:
            GLib.source_remove(self._snap_timeout_id)
        self._snap_timeout_id = GLib.timeout_add(200, self._snap_window_to_cards)

    def _snap_window_to_cards(self):
        self._snap_timeout_id = None
        if self._is_snapping:
            return False
        try:
            card_width = 160
            card_spacing = 10
            min_cards = 2
            max_cards = 8
            current_width = self.get_width()
            content_padding = 24
            available_width = current_width - content_padding
            cards_per_row = max(min_cards, int((available_width + card_spacing) / (card_width + card_spacing)))
            cards_per_row = min(cards_per_row, max_cards)
            ideal_content_width = (cards_per_row * card_width) + ((cards_per_row - 1) * card_spacing)
            ideal_window_width = ideal_content_width + content_padding
            if abs(current_width - ideal_window_width) > 20:
                self._is_snapping = True
                self.set_default_size(ideal_window_width, self.get_height())
                GLib.timeout_add(100, lambda: setattr(self, '_is_snapping', False))
        except Exception as e:
            print(f"Error snapping window: {e}")
        return False   
        
    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        try:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)
        except Exception as e:
            print(f"Error disabling responsive sidebar: {e}")


    def _create_rounded_cover_texture(self, cover_path, width, height, radius=10):
        try:
            original_pixbuf = GdkPixbuf.Pixbuf.new_from_file(cover_path)
            pixbuf = original_pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            context = cairo.Context(surface)
            context.arc(radius, radius, radius, 3.14159, 3 * 3.14159 / 2)
            context.arc(width - radius, radius, radius, 3 * 3.14159 / 2, 0)
            context.arc(width - radius, height - radius, radius, 0, 3.14159 / 2)
            context.arc(radius, height - radius, radius, 3.14159 / 2, 3.14159)
            context.close_path()
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.clip()
            context.paint()
            surface_bytes = surface.get_data()
            gbytes = GLib.Bytes.new(surface_bytes)
            texture = Gdk.MemoryTexture.new(
                width, height,
                Gdk.MemoryFormat.B8G8R8A8,
                gbytes,
                surface.get_stride()
            )
            return texture
        except Exception as e:
            print(f"Error creating rounded texture: {e}")
            return None

        
    # ---- Library ordering / loaded entry helpers ----
    def _get_library_entries_for_display(self):
        entries = list(reversed(self.library))
        if not entries:
            return entries
        try:
            if getattr(self, "book_path", None):
                for i, e in enumerate(entries):
                    try:
                        if os.path.abspath(e.get("path", "")) == os.path.abspath(self.book_path or ""):
                            if i != 0:
                                entries.insert(0, entries.pop(i))
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        return entries

    def _is_loaded_entry(self, entry):
        try:
            if not entry: return False
            if not getattr(self, "book_path", None): return False
            return os.path.abspath(entry.get("path", "")) == os.path.abspath(self.book_path or "")
        except Exception:
            return False

    def show_library(self):
        self._disable_responsive_sidebar()
        try:
            self.split.set_show_sidebar(False)
        except Exception: pass
        try:
            self.content_sidebar_toggle.set_visible(False)
        except Exception: pass
        try:
            self.open_btn.set_visible(True)
        except Exception: pass
        try:
            self.search_toggle_btn.set_visible(True)
            self.library_search_revealer.set_reveal_child(bool(self.library_search_text))
            try:
                if self._lib_search_handler_id:
                    self.library_search_entry.handler_block(self._lib_search_handler_id)
                self._safe_set_search_text(self.library_search_text)
            finally:
                try:
                    if self._lib_search_handler_id:
                        self.library_search_entry.handler_unblock(self._lib_search_handler_id)
                except Exception:
                    pass
        except Exception: pass

        # hide columns menu in library mode
        try:
            self.columns_menu_button.set_visible(False)
        except Exception:
            pass

        query = (self.library_search_text or "").strip().lower()
        entries = self._get_library_entries_for_display()
        if query:
            entries = [e for e in entries if query in (e.get("title") or "").lower() or query in (e.get("author") or "").lower() or query in (os.path.basename(e.get("path","")).lower())]

        if not entries:
            lbl = Gtk.Label(label="No books in library\nOpen a book to add it here.")
            lbl.set_justify(Gtk.Justification.CENTER); lbl.set_margin_top(40)
            self.toolbar.set_content(lbl); self.content_title_label.set_text("Library")
            return

        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_max_children_per_line(30)
        flowbox.set_min_children_per_line(2)
        flowbox.set_row_spacing(10)
        flowbox.set_column_spacing(10)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        flowbox.set_homogeneous(True)
        flowbox.add_css_class("library-grid")
        flowbox.set_margin_start(12)
        flowbox.set_margin_end(12)
        flowbox.set_margin_top(12)
        flowbox.set_margin_bottom(12)

        for entry in entries:
            title = entry.get("title") or os.path.basename(entry.get("path",""))
            author = entry.get("author") or ""
            cover = entry.get("cover")
            path = entry.get("path")
            idx = entry.get("index", 0)
            progress = entry.get("progress", 0.0)

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            card.add_css_class("library-card")
            card.set_size_request(160, 320)

            img = Gtk.Picture()
            img.set_size_request(140, 210)
            img.set_can_shrink(True)

            if cover and os.path.exists(cover):
                texture = self._create_rounded_cover_texture(cover, 140, 210, radius=10)
                if texture:
                    img.set_paintable(texture)
                else:
                    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 200)
                    pb.fill(0xddddddff)
                    img.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
            else:
                pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 200)
                pb.fill(0xddddddff)
                img.set_paintable(Gdk.Texture.new_for_pixbuf(pb))

            img.add_css_class("cover")
            img.set_halign(Gtk.Align.CENTER)
            card.append(img)

            t = Gtk.Label()
            t.add_css_class("title"); t.set_ellipsize(Pango.EllipsizeMode.END)
            t.set_wrap(True); t.set_max_width_chars(16); t.set_lines(2)
            t.set_halign(Gtk.Align.CENTER); t.set_justify(Gtk.Justification.CENTER)
            t.set_margin_top(4)
            t.set_margin_bottom(0)
            t.set_markup(highlight_markup(title, self.library_search_text))
            card.append(t)

            meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            meta_row.set_hexpand(True)
            meta_row.set_valign(Gtk.Align.CENTER)
            meta_row.set_margin_top(0)
            meta_row.set_margin_bottom(0)

            prog_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            prog_box.set_halign(Gtk.Align.START)
            prog_lbl = Gtk.Label()
            prog_lbl.add_css_class("meta")
            prog_lbl.set_valign(Gtk.Align.CENTER)
            prog_lbl.set_label(f"{int(progress*100)}%")
            prog_box.append(prog_lbl)
            meta_row.append(prog_box)

            author_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            author_box.set_hexpand(True)
            author_box.set_halign(Gtk.Align.CENTER)
            a = Gtk.Label()
            a.add_css_class("author")
            a.set_ellipsize(Pango.EllipsizeMode.END)
            a.set_max_width_chars(18)
            a.set_halign(Gtk.Align.CENTER)
            a.set_justify(Gtk.Justification.CENTER)
            a.set_markup(highlight_markup(author, self.library_search_text))
            author_box.append(a)
            meta_row.append(author_box)

            right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); right_box.set_halign(Gtk.Align.END)
            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic"); menu_btn.add_css_class("flat")
            pop = Gtk.Popover(); pop.set_has_arrow(False)
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            pop_box.set_margin_top(6); pop_box.set_margin_bottom(6); pop_box.set_margin_start(6); pop_box.set_margin_end(6)
            open_folder_btn = Gtk.Button(label="Open folder"); open_folder_btn.add_css_class("flat")
            rem_btn = Gtk.Button(label="Remove ebook"); rem_btn.add_css_class("flat")
            pop_box.append(open_folder_btn); pop_box.append(rem_btn)
            pop.set_child(pop_box); menu_btn.set_popover(pop)

            open_folder_btn.connect("clicked", lambda b, p=path: self._open_parent_folder(p))
            def _remove_entry(btn, p=path, coverp=cover):
                try:
                    dlg = Adw.MessageDialog.new(self, "Remove", f"Remove «{os.path.basename(p)}» from library?")
                    dlg.add_response("cancel", "Cancel"); dlg.add_response("ok", "Remove")
                    def _on_resp(d, resp):
                        try:
                            if resp == "ok":
                                self.library = [ee for ee in self.library if ee.get("path") != p]
                                try:
                                    if coverp and os.path.exists(coverp) and os.path.commonpath([os.path.abspath(COVERS_DIR)]) == os.path.commonpath([os.path.abspath(COVERS_DIR), os.path.abspath(coverp)]):
                                        os.remove(coverp)
                                except Exception:
                                    pass
                                save_library(self.library)
                                self.show_library()
                        finally:
                            try: d.destroy()
                            except Exception: pass
                    dlg.connect("response", _on_resp)
                    dlg.present()
                except Exception:
                    pass
            rem_btn.connect("clicked", _remove_entry)

            right_box.append(menu_btn); meta_row.append(right_box)
            card.append(meta_row)

            gesture = Gtk.GestureClick.new()
            def _on_click(_gesture, _n, _x, _y, p=path, resume_idx=idx):
                if p and os.path.exists(p):
                    try: self._save_progress_for_library()
                    except Exception: pass
                    try: self.cleanup()
                    except Exception: pass
                    try: self.toolbar.set_content(self._reader_content_box)
                    except Exception: pass
                    self.load_epub(p, resume=True, resume_index=resume_idx)
            gesture.connect("released", _on_click)
            card.add_controller(gesture)
            card.add_css_class("clickable")
            
            flowbox.append(card)

        scroll = Gtk.ScrolledWindow(); scroll.set_child(flowbox); scroll.set_vexpand(True); scroll.set_hexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); container.append(scroll)
        self.toolbar.set_content(container); self.content_title_label.set_text("Library")
                
    # ---- TOC setup/bind ----
    def _toc_on_setup(self, factory, list_item):
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0); hbox.set_hexpand(True)
        disc = Gtk.Image.new_from_icon_name("pan-end-symbolic"); disc.set_visible(False); hbox.append(disc)
        actrow = Adw.ActionRow(); actrow.set_activatable(True); actrow.set_title(""); actrow.set_hexpand(True); hbox.append(actrow)
        wrapper.append(hbox)
        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0); nested.set_margin_start(18); nested.set_visible(False)
        wrapper.append(nested)
        def _toggle_only():
            item = list_item.get_item()
            if not item: return
            if item.children.get_n_items() > 0:
                visible = not nested.get_visible()
                nested.set_visible(visible)
                disc.set_from_icon_name("pan-down-symbolic" if visible else "pan-end-symbolic")
                nv = getattr(list_item, "_nested_view", None)
                if nv: nv.set_visible(visible)
        g = Gtk.GestureClick(); g.connect("pressed", lambda *_: _toggle_only()); disc.add_controller(g)
        def _open_only(_):
            item = list_item.get_item()
            if not item: return
            href = item.href or ""
            fragment = href.split("#", 1)[1] if "#" in href else None
            if isinstance(item.index, int) and item.index >= 0:
                self.current_index = item.index; self.update_navigation(); self.display_page(fragment=fragment)
            elif href:
                try:
                    base = urllib.parse.unquote(href.split("#", 1)[0])
                    candidate = os.path.join(self.temp_dir or "", base)
                    if self.handle_internal_link("file://" + candidate):
                        return
                except Exception:
                    pass
            self._set_toc_selected(item)
        try: actrow.connect("activated", _open_only)
        except Exception: pass
        g2 = Gtk.GestureClick(); g2.connect("pressed", lambda *_: _open_only(None)); actrow.add_controller(g2)
        list_item.set_child(wrapper)
        list_item._hbox = hbox; list_item._disc = disc; list_item._actrow = actrow
        list_item._nested = nested; list_item._nested_view = None; list_item._bound_item = None

    def _toc_on_bind(self, factory, list_item):
        item = list_item.get_item()
        disc = getattr(list_item, "_disc", None); actrow = getattr(list_item, "_actrow", None); nested = getattr(list_item, "_nested", None)
        if disc is None or actrow is None or nested is None:
            self._toc_on_setup(factory, list_item)
            disc = list_item._disc; actrow = list_item._actrow; nested = list_item._nested
        prev = getattr(list_item, "_bound_item", None)
        if prev is not None and prev in self._toc_actrows:
            try: self._toc_actrows.pop(prev, None)
            except Exception: pass
        list_item._bound_item = item
        if not item:
            actrow.set_title(""); disc.set_visible(False)
            nv = getattr(list_item, "_nested_view", None)
            if nv: nv.set_visible(False)
            return
        try:
            self._toc_actrows[item] = actrow
            actrow.remove_css_class("selected")
        except Exception:
            pass
        has_children = item.children.get_n_items() > 0
        actrow.set_title(item.title or "")
        disc.set_visible(has_children)
        if has_children:
            disc.set_from_icon_name("pan-down-symbolic" if nested.get_visible() else "pan-end-symbolic")
        else:
            disc.set_from_icon_name(None)
        if has_children and not getattr(list_item, "_nested_view", None):
            def child_setup(f, li):
                cwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                ch_h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                ch_disc = Gtk.Image.new_from_icon_name("pan-end-symbolic"); ch_disc.set_visible(False); ch_h.append(ch_disc)
                ch_act = Adw.ActionRow(); ch_act.set_activatable(True); ch_act.set_title(""); ch_act.set_hexpand(True); ch_h.append(ch_act)
                cwrap.append(ch_h)
                ch_nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0); ch_nested.set_margin_start(18); ch_nested.set_visible(False)
                cwrap.append(ch_nested)
                def _toggle_child():
                    it = li.get_item()
                    if not it: return
                    if it.children.get_n_items() > 0:
                        vis = not ch_nested.get_visible()
                        ch_nested.set_visible(vis)
                        ch_disc.set_from_icon_name("pan-down-symbolic" if vis else "pan-end-symbolic")
                        gv = getattr(li, "_nested_view", None)
                        if gv: gv.set_visible(vis)
                gch = Gtk.GestureClick(); gch.connect("pressed", lambda *_: _toggle_child()); ch_disc.add_controller(gch)
                def _open_child(_):
                    it = li.get_item()
                    if not it: return
                    href = it.href or ""
                    fragment = href.split("#", 1)[1] if "#" in href else None
                    if isinstance(it.index, int) and it.index >= 0:
                        self.current_index = it.index; self.update_navigation(); self.display_page(fragment=fragment)
                    elif href:
                        try:
                            base = urllib.parse.unquote(href.split("#", 1)[0])
                            candidate = os.path.join(self.temp_dir or "", base)
                            if self.handle_internal_link("file://" + candidate):
                                return
                        except Exception:
                            pass
                    self._set_toc_selected(it)
                try: ch_act.connect("activated", _open_child)
                except Exception: pass
                gch2 = Gtk.GestureClick(); gch2.connect("pressed", lambda *_: _open_child(None)); ch_act.add_controller(gch2)
                li.set_child(cwrap)
                li._row = ch_act; li._disc = ch_disc; li._nested = ch_nested; li._nested_view = None; li._bound_item = None
            def child_bind(f, li):
                it = li.get_item()
                if not it: return
                ch_act = getattr(li, "_row", None); ch_disc = getattr(li, "_disc", None); ch_nested = getattr(li, "_nested", None)
                if ch_act is None or ch_disc is None or ch_nested is None: return
                prevc = getattr(li, "_bound_item", None)
                if prevc is not None and prevc in self._toc_actrows:
                    try: self._toc_actrows.pop(prevc, None)
                    except Exception: pass
                li._bound_item = it
                try:
                    self._toc_actrows[it] = ch_act
                    ch_act.remove_css_class("selected")
                except Exception:
                    pass
                kids = it.children.get_n_items() > 0
                ch_act.set_title(it.title or "")
                ch_disc.set_visible(kids)
                if kids:
                    ch_disc.set_from_icon_name("pan-down-symbolic" if ch_nested.get_visible() else "pan-end-symbolic")
                else:
                    ch_disc.set_from_icon_name(None)
                if kids and not getattr(li, "_nested_view", None):
                    sub_factory = Gtk.SignalListItemFactory()
                    sub_factory.connect("setup", child_setup)
                    sub_factory.connect("bind", child_bind)
                    sub_sel = Gtk.NoSelection(model=it.children)
                    gv = Gtk.ListView(model=sub_sel, factory=sub_factory)
                    gv.set_vexpand(False); ch_nested.append(gv); li._nested_view = gv
                if getattr(li, "_nested_view", None):
                    li._nested_view.set_visible(ch_nested.get_visible())
            nfactory = Gtk.SignalListItemFactory()
            nfactory.connect("setup", child_setup); nfactory.connect("bind", child_bind)
            nsel = Gtk.NoSelection(model=item.children)
            nested_view = Gtk.ListView(model=nsel, factory=nfactory); nested_view.set_vexpand(False)
            nested.append(nested_view); list_item._nested_view = nested_view
            nested_view.set_visible(nested.get_visible())
        nv = getattr(list_item, "_nested_view", None)
        if nv: nv.set_visible(nested.get_visible())

    # ---- selection helpers ----
    def _clear_toc_selection(self):
        try:
            for act in list(self._toc_actrows.values()):
                try: act.remove_css_class("selected")
                except Exception: pass
        except Exception: pass

    def _set_toc_selected(self, toc_item):
        try:
            self._clear_toc_selection()
            act = self._toc_actrows.get(toc_item)
            if act: act.add_css_class("selected")
        except Exception:
            pass

    # ---- canonical href registration ----
    def _register_href_variants(self, node: TocItem):
        if not node or not getattr(node, "href", None):
            return
        href = (node.href or "").strip()
        if not href:
            return
        keys = set()
        keys.add(href); keys.add(href.lstrip("./"))
        try:
            uq = urllib.parse.unquote(href); keys.add(uq); keys.add(uq.lstrip("./"))
        except Exception:
            pass
        b = os.path.basename(href)
        if b:
            keys.add(b)
            try: keys.add(urllib.parse.unquote(b))
            except Exception: pass
        if "#" in href:
            doc, frag = href.split("#", 1)
            if frag:
                keys.add(f"#{frag}"); keys.add(f"{os.path.basename(doc)}#{frag}")
                try: keys.add(f"{urllib.parse.unquote(os.path.basename(doc))}#{frag}")
                except Exception: pass
        try:
            if isinstance(node.index, int) and node.index >= 0 and node.index < len(self.items):
                it = self.items[node.index]
                iname = (it.get_name() or "").replace("\\", "/")
                if iname:
                    keys.add(iname); keys.add(os.path.basename(iname))
                    try:
                        keys.add(urllib.parse.unquote(iname)); keys.add(urllib.parse.unquote(os.path.basename(iname)))
                    except Exception:
                        pass
        except Exception:
            pass
        extras = set()
        for k in list(keys):
            for pfx in ("OEBPS/", "OPS/", "oebps/", "ops/"):
                extras.add(pfx + k)
        keys.update(extras)
        for k in keys:
            if not k:
                continue
            if k not in self.href_map:
                self.href_map[k] = node

    # ---- helper: wrapper that injects CSS & base ----
    def _wrap_html(self, raw_html, base_uri):
        """
        Wrap EPUB HTML so:
          - multi-column mode keeps columns inside viewport (no vertical scrolling),
          - single-column mode allows vertical scrolling,
          - mouse wheel scroll moves one column when columns > 1,
          - PageUp/PageDown move one column,
          - snaps to column boundaries on load/resize.
        """
        page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS

        try:
            if self.column_mode_use_width:
                col_decl = "column-width: {}px; -webkit-column-width: {}px;".format(self.column_width_px, self.column_width_px)
            else:
                col_decl = "column-count: {}; -webkit-column-count: {};".format(self.column_count, self.column_count)

            gap_decl = "column-gap: {}px; -webkit-column-gap: {}px;".format(self._column_gap, self._column_gap)
            fill_decl = "column-fill: auto; -webkit-column-fill: auto;"

            col_rules = (
                "/* Reset nested column rules from EPUB CSS to avoid N×N behavior */\n"
                ".ebook-content * {\n"
                "  -webkit-column-count: unset !important;\n"
                "  column-count: unset !important;\n"
                "  -webkit-column-width: unset !important;\n"
                "  column-width: unset !important;\n"
                "  -webkit-column-gap: unset !important;\n"
                "  column-gap: unset !important;\n"
                "  -webkit-column-fill: unset !important;\n"
                "  column-fill: unset !important;\n"
                "}\n"
                "html, body { height: 100%; min-height: 100%; margin: 0; padding: 0; overflow-x: hidden; }\n"
                ".ebook-content {\n"
            ) + "  " + col_decl + " " + gap_decl + " " + fill_decl + "\n" + (
                "  height: 100vh !important;     /* lock to viewport height for multi-column */\n"
                "  min-height: 0 !important;\n"
                "  overflow-y: hidden !important; /* prevent vertical scroll when multiple columns */\n"
                "  box-sizing: border-box !important;\n"
                "  padding: 12px; /* gentle padding so text doesn't stick to edges */\n"
                "}\n"
                "/* Single-column mode: allow normal vertical flow and scrolling */\n"
                ".single-column .ebook-content {\n"
                "  height: auto !important;\n"
                "  overflow-y: auto !important;\n"
                "  -webkit-column-width: unset !important;\n"
                "  column-width: unset !important;\n"
                "  -webkit-column-count: unset !important;\n"
                "  column-count: unset !important;\n"
                "}\n"
                ".ebook-content img, .ebook-content svg { max-width: 100%; height: auto; }\n"
            )

            page_css = col_rules + page_css
        except Exception:
            pass

        js_template = """
        <script>
        (function() {
          const GAP = __GAP__;
          function getComputedNumberStyle(el, propNames) {
            const cs = window.getComputedStyle(el);
            for (let p of propNames) {
              const v = cs.getPropertyValue(p);
              if (v && v.trim()) return v.trim();
            }
            return '';
          }
          function effectiveColumns(el) {
            try {
              let cc = parseInt(getComputedNumberStyle(el, ['column-count','-webkit-column-count']) || 0, 10);
              if (!isNaN(cc) && cc > 0 && cc !== Infinity) return cc;
              let cwRaw = getComputedNumberStyle(el, ['column-width','-webkit-column-width']);
              let cw = parseFloat(cwRaw);
              if (!isNaN(cw) && cw > 0) {
                let available = Math.max(1, el.clientWidth);
                let approx = Math.floor(available / (cw + (GAP||0)));
                return Math.max(1, approx);
              }
              return 1;
            } catch(e) { return 1; }
          }

          function columnStep(el) {
            const cs = window.getComputedStyle(el);
            const cwRaw = cs.getPropertyValue('column-width') || cs.getPropertyValue('-webkit-column-width') || '';
            const cw = parseFloat(cwRaw) || (el.clientWidth);
            const gapRaw = cs.getPropertyValue('column-gap') || cs.getPropertyValue('-webkit-column-gap') || (GAP + 'px');
            const gap = parseFloat(gapRaw) || GAP;
            const cols = effectiveColumns(el);
            let step = cw;
            if (!cwRaw || cwRaw === '' || cw === el.clientWidth) {
              step = Math.max(1, Math.floor((el.clientWidth - Math.max(0, (cols-1)*gap)) / cols));
            }
            return step + gap;
          }

          function snapToNearestColumn() {
            const cont = document.querySelector('.ebook-content');
            if (!cont) return;
            const step = columnStep(cont);
            const cur = window.scrollX || window.pageXOffset || document.documentElement.scrollLeft || 0;
            const target = Math.round(cur / step) * step;
            window.scrollTo({ left: target, top: 0, behavior: 'smooth' });
          }

          function goByColumn(delta) {
            const cont = document.querySelector('.ebook-content');
            if (!cont) return;
            const step = columnStep(cont);
            const cur = window.scrollX || window.pageXOffset || document.documentElement.scrollLeft || 0;
            const target = Math.max(0, cur + (delta>0 ? step : -step));
            window.scrollTo({ left: target, top: 0, behavior: 'smooth' });
          }

          function onWheel(e) {
            const cont = document.querySelector('.ebook-content');
            if (!cont) return;
            const cols = effectiveColumns(cont);
            if (cols <= 1) return;
            if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
              e.preventDefault();
              const dir = e.deltaY > 0 ? 1 : -1;
              goByColumn(dir);
            } else {
              if (Math.abs(e.deltaX) > 0) {
                e.preventDefault();
                const dir = e.deltaX > 0 ? 1 : -1;
                goByColumn(dir);
              }
            }
          }

          function onKey(e) {
            const cont = document.querySelector('.ebook-content');
            if (!cont) return;
            const cols = effectiveColumns(cont);
            if (cols <= 1) return;
            if (e.code === 'PageDown') {
              e.preventDefault(); goByColumn(1);
            } else if (e.code === 'PageUp') {
              e.preventDefault(); goByColumn(-1);
            } else if (e.code === 'Home') {
              e.preventDefault(); window.scrollTo({ left: 0, top: 0, behavior: 'smooth' });
            } else if (e.code === 'End') {
              e.preventDefault();
              const step = columnStep(cont);
              const max = document.documentElement.scrollWidth - window.innerWidth;
              window.scrollTo({ left: max, top: 0, behavior: 'smooth' });
            }
          }

          let rTO = null;
          function onResize() {
            if (rTO) clearTimeout(rTO);
            rTO = setTimeout(function() {
              updateMode();
              snapToNearestColumn();
              rTO = null;
            }, 120);
          }

          function updateMode() {
            const c = document.querySelector('.ebook-content');
            if (!c) return;
            const cols = effectiveColumns(c);
            if (cols <= 1) {
              document.documentElement.classList.add('single-column');
              document.body.classList.add('single-column');
              window.scrollTo({ left: 0, top: 0 });
            } else {
              document.documentElement.classList.remove('single-column');
              document.body.classList.remove('single-column');
              snapToNearestColumn();
            }
          }

          document.addEventListener('DOMContentLoaded', function() {
            try {
              updateMode();
              window.addEventListener('wheel', onWheel, { passive: false, capture: false });
              window.addEventListener('keydown', onKey, false);
              window.addEventListener('resize', onResize);
              setTimeout(updateMode, 250);
              setTimeout(snapToNearestColumn, 450);
            } catch(e) { console.error('column scripts error', e); }
          });
        })();
        </script>
        """

        js_detect_columns = js_template.replace("__GAP__", str(self._column_gap))

        link_intercept_script = """
        <script> (function(){ function updateDarkMode(){ if(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches){document.documentElement.classList.add('dark-mode');document.body.classList.add('dark-mode');}else{document.documentElement.classList.remove('dark-mode');document.body.classList.remove('dark-mode');}} updateDarkMode(); if(window.matchMedia){window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateDarkMode);} function interceptLinks(){document.addEventListener('click', function(e){var target=e.target; while(target && target.tagName!=='A'){target=target.parentElement;if(!target||target===document.body) break;} if(target && target.tagName==='A' && target.href){var href=target.href; e.preventDefault(); e.stopPropagation(); try{window.location.href=href;}catch(err){console.error('[js] navigation error:', err);} return false;} }, true);} if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded', interceptLinks);} else {interceptLinks();}})(); </script>
        """

        base_tag = ""
        try:
            if base_uri:
                base_tag = '<base href="{}"/>'.format(base_uri)
        except Exception:
            base_tag = ""

        head = (
            '<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>'
            '<meta name="color-scheme" content="light dark"/>' + base_tag +
            '<style>' + page_css + '</style>' +
            link_intercept_script + js_detect_columns
        )

        wrapped = "<!DOCTYPE html><html><head>{}</head><body><div class=\"ebook-content\">{}</div></body></html>".format(head, raw_html)
        return wrapped


    # ---- file dialog ----
    def open_file(self, *_):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        epub_filter = Gtk.FileFilter(); epub_filter.add_pattern("*.epub"); epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)
        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                target = f.get_path()
                try: self._save_progress_for_library()
                except Exception: pass
                try: self.cleanup()
                except Exception: pass
                try: self.open_btn.set_visible(False)
                except Exception: pass
                self._enable_sidebar_for_reading()
                self.load_epub(target)
        except GLib.Error:
            pass

    def _enable_sidebar_for_reading(self):
        try:
            self.content_sidebar_toggle.set_visible(True)
            self.content_sidebar_toggle.set_sensitive(True)
            self._sidebar_img.set_from_icon_name("sidebar-show-symbolic")
            self.content_sidebar_toggle.set_tooltip_text("Show/Hide sidebar")
            try:
                self.open_btn.set_visible(False)
                self.search_toggle_btn.set_visible(False)
            except Exception:
                pass
            # show columns menu in reading mode
            try:
                self.columns_menu_button.set_visible(True)
            except Exception:
                pass
        except Exception:
            pass

    # ---- cover detection (kept) ----
    def _find_cover_via_opf(self, extracted_paths, image_names, image_basenames):
        if not self.temp_dir:
            return None, None
        lc_map = {p.lower(): p for p in (extracted_paths or [])}
        pattern = os.path.join(self.temp_dir, "**", "*.opf")
        opf_files = sorted(glob.glob(pattern, recursive=True))
        for opf in opf_files:
            try:
                with open(opf, "rb") as fh:
                    raw = fh.read()
                soup = BeautifulSoup(raw, "xml")
                cover_id = None
                meta = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "meta" and tag.has_attr("name") and tag["name"].lower() == "cover")
                if meta and meta.has_attr("content"):
                    cover_id = meta["content"]
                href = None
                if cover_id:
                    item_tag = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("id") and tag["id"] == cover_id)
                    if item_tag and item_tag.has_attr("href"):
                        href = item_tag["href"]
                if not href:
                    item_prop = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("properties") and "cover-image" in tag["properties"])
                    if item_prop and item_prop.has_attr("href"):
                        href = item_prop["href"]
                if not href:
                    item_cover_href = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'cover.*\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                    if item_cover_href and item_cover_href.has_attr("href"):
                        href = item_cover_href["href"]
                if not href:
                    first_img = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                    if first_img and first_img.has_attr("href"):
                        href = first_img["href"]
                if not href:
                    continue
                opf_dir = os.path.dirname(opf)
                candidate_abs = os.path.normpath(os.path.join(opf_dir, urllib.parse.unquote(href)))
                candidate_abs = os.path.abspath(candidate_abs)
                candidate_abs2 = os.path.abspath(os.path.normpath(os.path.join(self.temp_dir, urllib.parse.unquote(href))))
                try:
                    rel_from_temp = os.path.relpath(candidate_abs, self.temp_dir).replace(os.sep, "/")
                except Exception:
                    rel_from_temp = os.path.basename(candidate_abs)
                variants = [rel_from_temp, os.path.basename(rel_from_temp)]
                for pfx in ("OEBPS/", "OPS/", "oebps/", "ops/"):
                    variants.append(pfx + rel_from_temp); variants.append(pfx + os.path.basename(rel_from_temp))
                try:
                    uq = urllib.parse.unquote(rel_from_temp); variants.append(uq); variants.append(os.path.basename(uq))
                except Exception:
                    pass
                if os.path.exists(candidate_abs): return candidate_abs, None
                if os.path.exists(candidate_abs2): return candidate_abs2, None
                for v in variants:
                    found = lc_map.get(v.lower())
                    if found:
                        abs_p = os.path.abspath(os.path.join(self.temp_dir, found)); return abs_p, None
                    if v in image_names: return None, image_names[v]
                    bn = os.path.basename(v)
                    if bn in image_basenames: return None, image_basenames[bn][0]
                bn = os.path.basename(href)
                for p in extracted_paths:
                    if os.path.basename(p).lower() == bn.lower():
                        abs_p = os.path.abspath(os.path.join(self.temp_dir, p)); return abs_p, None
            except Exception:
                continue
        return None, None

    # ---- Load EPUB ----
    def load_epub(self, path, resume=False, resume_index=None):
        try:
            try: self.toolbar.set_content(self._reader_content_box)
            except Exception: pass
            try:
                self._enable_responsive_sidebar()
                self._enable_sidebar_for_reading()
                self.open_btn.set_visible(False)
                self.search_toggle_btn.set_visible(False)
                self.library_search_revealer.set_reveal_child(False)
            except Exception: pass

            try: self.cleanup()
            except Exception: pass

            self.book_path = path
            self.book = epub.read_epub(path)
            docs = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            id_map = {}
            for it in docs:
                try:
                    iid = getattr(it, "id", None) or (it.get_id() if hasattr(it, "get_id") else None)
                except Exception:
                    iid = None
                if not iid:
                    iid = it.get_name() or os.urandom(8).hex()
                id_map[iid] = it
            ordered = []
            try:
                spine = getattr(self.book, "spine", None) or []
                for entry in spine:
                    sid = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
                    if sid in id_map:
                        ordered.append(id_map.pop(sid))
                ordered.extend(id_map.values())
                self.items = ordered
            except Exception:
                self.items = docs
            if not self.items:
                self.show_error("No document items found in EPUB"); return
            try:
                if self.reading_breakpoint and not self.reading_breakpoint.get_condition():
                    pass
            except Exception:
                pass
            self.temp_dir = tempfile.mkdtemp()
            extracted_paths = set()
            try:
                with zipfile.ZipFile(path, "r") as z:
                    z.extractall(self.temp_dir)
            except Exception:
                pass
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path: continue
                sanitized_path = self.sanitize_path(item_path)
                if sanitized_path is None: continue
                full = os.path.join(self.temp_dir, sanitized_path)
                try:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as fh:
                        fh.write(item.get_content())
                    extracted_paths.add(sanitized_path.replace("\\", "/"))
                except OSError:
                    continue

            self._extracted_paths_map = {p.lower(): p for p in extracted_paths}

            image_items = list(self.book.get_items_of_type(ebooklib.ITEM_IMAGE))
            image_names = { (im.get_name() or "").replace("\\", "/"): im for im in image_items }
            image_basenames = {}
            for im in image_items:
                bn = os.path.basename((im.get_name() or "")).replace("\\", "/")
                if bn:
                    image_basenames.setdefault(bn, []).append(im)

            self.item_map = {it.get_name(): it for it in self.items}
            self.extract_css()

            title = APP_NAME; author = ""
            try:
                meta = self.book.get_metadata("DC", "title");
                if meta and meta[0]: title = meta[0][0]
                m2 = self.book.get_metadata("DC", "creator")
                if m2 and m2[0]: author = m2[0][0]
            except Exception:
                pass
            self.book_title.set_text(title); self.book_author.set_text(author)
            self.content_title_label.set_text(title); self.set_title(title or APP_NAME)

            try:
                cover_path_to_use = None; cover_item_obj = None
                cpath, citem = self._find_cover_via_opf(extracted_paths, image_names, image_basenames)
                if cpath: cover_path_to_use = cpath
                elif citem: cover_item_obj = citem

                if not cover_path_to_use and not cover_item_obj:
                    priority_names = ("ops/cover.xhtml", "oebps/cover.xhtml", "ops/cover.html", "cover.xhtml", "cover.html", "ops/title.xhtml", "title.xhtml")
                    docs_list = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
                    lower_map = { (d.get_name() or "").lower(): d for d in docs_list }
                    for pn in priority_names:
                        if pn in lower_map:
                            cover_doc = lower_map[pn]; break
                    else:
                        cover_doc = None
                    if cover_doc:
                        try:
                            soup = BeautifulSoup(cover_doc.get_content(), "html.parser")
                            doc_dir = os.path.dirname(cover_doc.get_name() or "")
                            srcs = []
                            img = soup.find("img", src=True)
                            if img: srcs.append(img["src"])
                            for svg_im in soup.find_all("image"):
                                if svg_im.has_attr("xlink:href"): srcs.append(svg_im["xlink:href"])
                                elif svg_im.has_attr("href"): srcs.append(svg_im["href"])
                            for src in srcs:
                                if not src: continue
                                src = src.split("#", 1)[0]; src = urllib.parse.unquote(src)
                                candidate_rel = os.path.normpath(os.path.join(doc_dir, src)).replace("\\", "/")
                                found = None
                                if candidate_rel.lower() in self._extracted_paths_map:
                                    found = self._extracted_paths_map[candidate_rel.lower()]
                                elif os.path.basename(candidate_rel).lower() in self._extracted_paths_map:
                                    found = self._extracted_paths_map[os.path.basename(candidate_rel).lower()]
                                if found:
                                    cover_path_to_use = os.path.join(self.temp_dir, found); break
                        except Exception:
                            pass

                if not cover_path_to_use and not cover_item_obj:
                    for im_name, im in image_names.items():
                        if "cover" in im_name.lower() or "cover" in os.path.basename(im_name).lower():
                            cover_item_obj = im; break

                if not cover_path_to_use and not cover_item_obj:
                    for p in extracted_paths:
                        if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                            cover_path_to_use = os.path.join(self.temp_dir, p); break

                if cover_item_obj and not cover_path_to_use:
                    iname = (cover_item_obj.get_name() or "").replace("\\", "/")
                    for cand in (iname, os.path.basename(iname)):
                        if cand in extracted_paths:
                            cover_path_to_use = os.path.join(self.temp_dir, cand); break
                        for pfx in ("OEBPS/", "OPS/", "oebps/", "ops/"):
                            if (pfx + cand) in extracted_paths:
                                cover_path_to_use = os.path.join(self.temp_dir, pfx + cand); break
                        if cover_path_to_use: break

                if not cover_path_to_use and cover_item_obj:
                    try:
                        raw = cover_item_obj.get_content()
                        if raw:
                            tmpfn = os.path.join(self.temp_dir, "cover_from_item_" + os.urandom(6).hex())
                            with open(tmpfn, "wb") as fh: fh.write(raw)
                            cover_path_to_use = tmpfn
                    except Exception:
                        pass

                if cover_path_to_use and os.path.exists(cover_path_to_use):
                    try:
                        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover_path_to_use, COVER_W, COVER_H, True)
                        tex = Gdk.Texture.new_for_pixbuf(pix); self.cover_image.set_from_paintable(tex)
                        try: self.cover_image.set_size_request(COVER_W, COVER_H)
                        except Exception: pass
                        self.last_cover_path = cover_path_to_use
                    except Exception:
                        self.last_cover_path = None; cover_path_to_use = None

                if not cover_path_to_use and not self.last_cover_path:
                    placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
                    placeholder_pb.fill(0xddddddff)
                    placeholder_tex = Gdk.Texture.new_for_pixbuf(placeholder_pb)
                    self.cover_image.set_from_paintable(placeholder_tex)
                    try: self.cover_image.set_size_request(COVER_W, COVER_H)
                    except Exception: pass
            except Exception:
                pass

            self._populate_toc_tree()
            try:
                if getattr(self, "toc_root_store", None) and self.toc_root_store.get_n_items() > 0:
                    try: self.split.set_show_sidebar(True)
                    except Exception: pass
            except Exception: pass

            if resume:
                if isinstance(resume_index, int) and 0 <= resume_index < len(self.items):
                    self.current_index = resume_index
                else:
                    for e in self.library:
                        if e.get("path") == path:
                            self.current_index = int(e.get("index", 0)) if isinstance(e.get("index", 0), int) else 0
                            break
            else:
                self.current_index = 0
            self.update_navigation(); self.display_page()
            self._update_library_entry()
        except Exception:
            print(traceback.format_exc()); self.show_error("Error loading EPUB — see console")

    def sanitize_path(self, path):
        if not path: return None
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or os.path.isabs(normalized): return None
        if ".." in normalized.split(os.sep): return None
        return normalized

    def _populate_toc_tree(self):
        def href_to_index(href):
            if not href: return -1
            h = href.split("#")[0]
            candidates = [h, os.path.basename(h)]
            try:
                uq = urllib.parse.unquote(h)
                if uq != h:
                    candidates.append(uq); candidates.append(os.path.basename(uq))
            except Exception:
                pass
            for i, it in enumerate(self.items):
                if it.get_name() == h or it.get_name().endswith(h) or it.get_name() in candidates:
                    return i
            return -1

        root = Gio.ListStore(item_type=TocItem)
        def add_node(title, href, parent_store):
            idx = href_to_index(href)
            node = TocItem(title=title or "", href=href or "", index=idx)
            parent_store.append(node)
            try: self._register_href_variants(node)
            except Exception: pass
            return node

        try:
            nav_item = self.book.get_item_with_id("nav")
            if nav_item:
                soup = BeautifulSoup(nav_item.get_content(), "html.parser")
                toc_nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", {"role": "doc-toc"})
                if toc_nav:
                    def walk_list(ol, parent_store):
                        for li in ol.find_all("li", recursive=False):
                            a = li.find("a", href=True)
                            title = a.get_text(strip=True) if a else li.get_text(strip=True)
                            href = a["href"] if a else ""
                            node = add_node(title, href, parent_store)
                            child_ol = li.find("ol", recursive=False)
                            if child_ol: walk_list(child_ol, node.children)
                    ol = toc_nav.find("ol")
                    if ol:
                        walk_list(ol, root)
                        self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                        self.toc_listview.set_model(self.toc_sel); return
        except Exception:
            pass

        try:
            ncx_item = self.book.get_item_with_id("ncx")
            if ncx_item:
                soup = BeautifulSoup(ncx_item.get_content(), "xml")
                def walk_navpoints(parent, parent_store):
                    for np in parent.find_all("navPoint", recursive=False):
                        text_tag = np.find("text"); content_tag = np.find("content")
                        title = text_tag.get_text(strip=True) if text_tag else ""
                        href = content_tag["src"] if content_tag and content_tag.has_attr("src") else ""
                        node = add_node(title or os.path.basename(href), href or "", parent_store)
                        walk_navpoints(np, node.children)
                navmap = soup.find("navMap")
                if navmap:
                    walk_navpoints(navmap, root)
                    self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                    self.toc_listview.set_model(self.toc_sel); return
        except Exception:
            pass

        for i, it in enumerate(self.items):
            title = os.path.basename(it.get_name())
            add_node(title, it.get_name(), root)
        self.toc_root_store = root; self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
        self.toc_listview.set_model(self.toc_sel)

    def on_decide_policy(self, webview, decision, decision_type):
        if not self.WebKit: return False
        if decision_type == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            try:
                nav_action = decision.get_navigation_action()
                request = nav_action.get_request() if hasattr(nav_action, 'get_request') else decision.get_request()
                uri = request.get_uri() if request else None
            except Exception as e:
                print(f"Error getting URI from decision: {e}"); return False
            if not uri: return False
            if uri in ("", "about:blank", "file://"): return False
            if uri.startswith("http://") or uri.startswith("https://"):
                try: decision.ignore()
                except Exception: pass
                return True
            if uri.startswith("file://"):
                current_uri = webview.get_uri()
                if current_uri and current_uri == uri: return False
                if self.handle_internal_link(uri):
                    try: decision.ignore()
                    except Exception: pass
                    return True
        return False

    def _find_tocitem_for_candidates(self, candidates, fragment=None):
        for c in candidates:
            if not c: continue
            t = self.href_map.get(c)
            if t: return t
            bn = os.path.basename(c)
            t = self.href_map.get(bn)
            if t: return t
        if fragment:
            frag_keys = [f"#{fragment}", fragment, os.path.basename(fragment)]
            for fk in frag_keys:
                t = self.href_map.get(fk)
                if t: return t
        return None

    def handle_internal_link(self, uri):
        path = uri.replace("file://", "")
        fragment = None
        if "#" in path:
            path, fragment = path.split("#", 1)
        base = path
        if self.temp_dir and base.startswith(self.temp_dir):
            rel = os.path.relpath(base, self.temp_dir).replace(os.sep, "/")
        else:
            rel = base.replace(os.sep, "/")
        candidates = [rel, os.path.basename(rel)]
        try:
            uq = urllib.parse.unquote(rel)
            if uq != rel:
                candidates.append(uq); candidates.append(os.path.basename(uq))
        except Exception:
            pass
        toc_match = self._find_tocitem_for_candidates(candidates, fragment)
        if toc_match:
            if isinstance(toc_match.index, int) and toc_match.index >= 0:
                self.current_index = toc_match.index; self.update_navigation()
                frag = fragment or (toc_match.href.split("#", 1)[1] if "#" in (toc_match.href or "") else None)
                self.display_page(fragment=frag); return True
            else:
                href = toc_match.href or ""
                candidate_path = None
                try:
                    candidate_path = os.path.join(self.temp_dir or "", urllib.parse.unquote(href.split("#", 1)[0]))
                except Exception:
                    pass
                if candidate_path and os.path.exists(candidate_path):
                    return self._load_file_with_css(candidate_path, fragment)
                self._set_toc_selected(toc_match); return True

        for cand in candidates:
            if cand in self.item_map:
                for i, it in enumerate(self.items):
                    if it.get_name() == cand:
                        self.current_index = i; self.update_navigation(); self.display_page(fragment=fragment)
                        for ti in list(self.href_map.values()):
                            if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == i:
                                self._set_toc_selected(ti); break
                        return True

        possible_paths = []
        if self.temp_dir:
            possible_paths.append(os.path.join(self.temp_dir, rel))
            possible_paths.append(os.path.join(self.temp_dir, os.path.basename(rel)))
        possible_paths.append(path)
        for p in possible_paths:
            if not p: continue
            if os.path.exists(p):
                return self._load_file_with_css(p, fragment)
        return False

    def _load_file_with_css(self, file_path, fragment=None):
        if not os.path.exists(file_path): return False
        if not self.css_content: self.extract_css()
        ext = os.path.splitext(file_path)[1].lower()
        base_uri = "file://" + (os.path.dirname(file_path) or "/") + "/"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            img_uri = "file://" + file_path
            raw = f'<div style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;"><img src="{img_uri}" alt="image" style="max-width:100%;height:auto;"/></div>'
            html = self._wrap_html(raw, base_uri)
            try:
                if self.webview: self.webview.load_html(html, base_uri)
                else: self.textview.get_buffer().set_text(f"[Image] {file_path}")
            except Exception as e:
                print(f"Error loading image: {e}")
            return True
        if ext in (".html", ".xhtml", ".htm"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh: content = fh.read()
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup.find_all(['style', 'link']): tag.decompose()
                body = soup.find("body")
                if body:
                    body_attrs = ' '.join([f'{k}="{v}"' if isinstance(v, str) else f'{k}="{" ".join(v)}"' for k, v in body.attrs.items()])
                    if body_attrs:
                        body_content = f'<div {body_attrs}>{"".join(str(child) for child in body.children)}</div>'
                    else:
                        body_content = "".join(str(child) for child in body.children)
                else:
                    body_content = str(soup)
                html_content = self._wrap_html(body_content, base_uri)
                if self.webview:
                    self.webview.load_html(html_content, base_uri)
                    if fragment: GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                else:
                    self.textview.get_buffer().set_text(soup.get_text())
                return True
            except Exception as e:
                print(f"Error loading HTML file {file_path}: {e}"); return False
        return False

    def display_page(self, fragment=None):
        if not self.book or not self.items or self.current_index >= len(self.items): return
        if not self.css_content: self.extract_css()
        item = self.items[self.current_index]
        if not item or not hasattr(item, 'get_content'): return
        try:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            for tag in soup.find_all(['style', 'link']): tag.decompose()
            body = soup.find("body")
            if body:
                body_attrs = ' '.join([f'{k}="{v}"' if isinstance(v, str) else f'{k}="{" ".join(v)}"' for k, v in body.attrs.items()])
                if body_attrs:
                    content = f'<div {body_attrs}>{"".join(str(child) for child in body.children)}</div>'
                else:
                    content = "".join(str(child) for child in body.children)
            else:
                content = str(soup)
            base_uri = f"file://{os.path.join(self.temp_dir or '', os.path.dirname(item.get_name()))}/"
            wrapped_html = self._wrap_html(content, base_uri)
            if self.webview:
                self.webview.load_html(wrapped_html, base_uri)
                if fragment: GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
            else:
                buf = self.textview.get_buffer(); buf.set_text(soup.get_text())
            total = len(self.items)
            self.progress.set_fraction((self.current_index + 1) / total)
            self.progress.set_text(f"{self.current_index + 1}/{total}")
            try:
                for ti in list(self.href_map.values()):
                    if isinstance(ti, TocItem) and isinstance(ti.index, int) and ti.index == self.current_index:
                        self._set_toc_selected(ti); break
            except Exception: pass
            self._save_progress_for_library()
        except Exception as e:
            print(f"Error displaying page: {e}"); self.show_error(f"Error displaying page: {e}")

    def _scroll_to_fragment(self, fragment):
        if self.webview and fragment:
            js_code = f"var element = document.getElementById('{fragment}'); if (element) {{ element.scrollIntoView({{behavior:'smooth', block:'start'}}); }}"
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception:
                try: self.webview.run_javascript(js_code, None, None, None)
                except Exception: pass
        return False

    # ---- Navigation ----
    def update_navigation(self):
        total = len(self.items) if hasattr(self, "items") and self.items else 0
        self.prev_btn.set_sensitive(getattr(self, "current_index", 0) > 0)
        self.next_btn.set_sensitive(getattr(self, "current_index", 0) < total - 1)

    def next_page(self, button):
        if self.current_index < len(self.items) - 1:
            self.current_index += 1; self.update_navigation(); self.display_page(); self._save_progress_for_library()

    def prev_page(self, button):
        if self.current_index > 0:
            self.current_index -= 1; self.update_navigation(); self.display_page(); self._save_progress_for_library()

    # ---- CSS extraction ----
    def extract_css(self):
        self.css_content = ""
        if not self.book: return
        try:
            for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
                try: self.css_content += item.get_content().decode("utf-8") + "\n"
                except Exception: pass
            if self.temp_dir and os.path.exists(self.temp_dir):
                for fn in ("flow0001.css", "core.css", "se.css", "style.css"):
                    p = os.path.join(self.temp_dir, fn)
                    if os.path.exists(p):
                        try:
                            with open(p
, "r", encoding="utf-8", errors="ignore") as fh:
                                self.css_content += fh.read() + "\n"
                        except Exception:
                            pass
        except Exception as e:
            print(f"Error extracting CSS: {e}")

    def show_error(self, message):
        try:
            dialog = Adw.MessageDialog.new(self, "Error", message); dialog.add_response("ok", "OK"); dialog.present()
        except Exception:
            print("Error dialog:", message)

    def cleanup(self):
        if getattr(self, "temp_dir", None) and os.path.exists(self.temp_dir):
            try: shutil.rmtree(self.temp_dir)
            except Exception as e: print(f"Error cleaning up temp directory: {e}")
        self.temp_dir = None; self.book = None; self.items = []; self.item_map = {}; self.css_content = ""; self.current_index = 0
        try:
            if getattr(self, "toc_root_store", None):
                self.toc_root_store = Gio.ListStore(item_type=TocItem); self.toc_sel = Gtk.NoSelection(model=self.toc_root_store)
                self.toc_listview.set_model(self.toc_sel)
            self._toc_actrows = {}; self.href_map = {}
        except Exception as e:
            print(f"Error clearing TOC store: {e}")
        self.update_navigation()
        if self.webview:
            try: blank = self._wrap_html("", ""); self.webview.load_html(blank, "")
            except Exception: pass
        elif hasattr(self, 'textview'):
            try: self.textview.get_buffer().set_text("")
            except Exception: pass
        self.book_title.set_text(""); self.book_author.set_text("")
        try:
            placeholder_pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
            placeholder_pb.fill(0xddddddff)
            self.cover_image.set_from_paintable(Gdk.Texture.new_for_pixbuf(placeholder_pb))
        except Exception:
            pass
        try:
            self.content_sidebar_toggle.set_visible(True)
            self.open_btn.set_visible(False)
            self.search_toggle_btn.set_visible(False)
            self.library_search_revealer.set_reveal_child(False)
        except Exception:
            pass
        
        # DON'T disable responsive sidebar here - it will be managed by load_epub/show_library
        # Only disable if we're truly going back to library (book_path will be None)
        # This is now handled in show_library() instead

    # ---- Library helpers ----
    def _update_library_entry(self):
        path = self.book_path or ""
        if not path: return
        title = self.book_title.get_text() or os.path.basename(path)
        author = self.book_author.get_text() or ""
        cover_src = self.last_cover_path; cover_dst = None
        if cover_src and os.path.exists(cover_src):
            try:
                h = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
                ext = os.path.splitext(cover_src)[1].lower() or ".png"
                cover_dst = os.path.join(COVERS_DIR, f"{h}{ext}")
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file(cover_src)
                    scaled = pix.scale_simple(LIB_COVER_W, LIB_COVER_H, GdkPixbuf.InterpType.BILINEAR)
                    scaled.savev(cover_dst, ext.replace(".", ""), [], [])
                except Exception:
                    shutil.copy2(cover_src, cover_dst)
            except Exception:
                cover_dst = None
        found = False
        found_entry = None
        for e in list(self.library):
            if e.get("path") == path:
                e["title"] = title; e["author"] = author
                if cover_dst: e["cover"] = cover_dst
                e["index"] = int(self.current_index); e["progress"] = float(self.progress.get_fraction() or 0.0)
                found = True; found_entry = e; break
        if found and found_entry is not None:
            # move to end (most-recent)
            try:
                self.library = [ee for ee in self.library if ee.get("path") != path]
                self.library.append(found_entry)
            except Exception:
                pass
        if not found:
            entry = {"path": path, "title": title, "author": author, "cover": cover_dst, "index": int(self.current_index), "progress": float(self.progress.get_fraction() or 0.0)}
            self.library.append(entry)
        if len(self.library) > 200: self.library = self.library[-200:]
        save_library(self.library)

    def _save_progress_for_library(self):
        if not self.book_path: return
        changed = False
        for e in self.library:
            if e.get("path") == self.book_path:
                e["index"] = int(self.current_index); e["progress"] = float(self.progress.get_fraction() or 0.0)
                changed = True; break
        if changed: save_library(self.library)

    def _open_parent_folder(self, path):
        try:
            if not path: return
            parent = os.path.dirname(path) or path
            uri = GLib.filename_to_uri(parent, None)
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            pass

# -------------------------
# Application + main
# -------------------------
class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.create_action("quit", self.quit, ["<primary>q"])
        def _action_wrapper_win(method_name, variant):
            win = self.props.active_window
            if not win:
                wins = self.get_windows() if hasattr(self, "get_windows") else []
                win = wins[0] if wins else None
            if not win: return
            try:
                if variant is None:
                    getattr(win, method_name)()
                else:
                    val = None
                    try:
                        val = int(variant.unpack())
                    except Exception:
                        try:
                            val = variant.unpack()
                        except Exception:
                            val = variant
                    getattr(win, method_name)(val)
            except Exception:
                pass
        act = Gio.SimpleAction.new("set-columns", GLib.VariantType.new("i"))
        act.connect("activate", lambda a, v: _action_wrapper_win("set_columns", v)); self.add_action(act)
        act2 = Gio.SimpleAction.new("set-column-width", GLib.VariantType.new("i"))
        act2.connect("activate", lambda a, v: _action_wrapper_win("set_column_width", v)); self.add_action(act2)

    def do_activate(self):
        win = self.props.active_window
        if not win: win = EPubViewer(self)
        win.present()
    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None); action.connect("activate", callback); self.add_action(action)
        if shortcuts: self.set_accels_for_action(f"app.{name}", shortcuts)

def main():
    _ensure_library_dir()
    app = Application()
    return app.run(None)

if __name__ == "__main__":
    main()

