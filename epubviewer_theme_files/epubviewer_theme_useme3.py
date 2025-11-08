#!/usr/bin/env python3

# --- Standard Library Imports ---
import glob, hashlib, json, os, pathlib, re, shutil, tempfile, threading, time, traceback, urllib.parse, zipfile

# --- GTK and related Imports ---
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, PangoCairo, GObject, Gdk, GdkPixbuf, WebKit

# --- Graphics Imports ---
import cairo
import subprocess
# --- EPUB and Parsing Imports ---
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString, Tag, ProcessingInstruction, element, Comment
from pathlib import Path
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

################## Colors
def darken(hex_color: str, factor: float = 0.9) -> str:
    """Return a darker hex color by multiplying RGB channels by `factor` (0–1)."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = int(r * factor)
    g = int(g * factor)
    b = int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"

def invert_color(hex_color: str, preserve_luminance: bool = True) -> str:
    """Return an inverted hex color. Optionally preserve perceived luminance."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    if preserve_luminance:
        # Perceptual luminance using Rec. 709 coefficients
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        ir, ig, ib = 255 - r, 255 - g, 255 - b
        inv_lum = 0.2126 * ir + 0.7152 * ig + 0.0722 * ib
        # Scale inverted color so luminance matches original
        scale = lum / inv_lum if inv_lum else 1
        r = max(0, min(255, int(ir * scale)))
        g = max(0, min(255, int(ig * scale)))
        b = max(0, min(255, int(ib * scale)))
    else:
        r, g, b = 255 - r, 255 - g, 255 - b

    return f"#{r:02x}{g:02x}{b:02x}"

##############
# Theme management is now inside EPubViewer class
#############

_LIBRARY_CSS = b"""
.library-grid { padding: 1px; }
.library-card {
  background-color: transparent;
  border-radius: 10px;
  padding-top: 10px;
  padding-bottom: 5px;
  box-shadow: none;
  border: none;
}

.library-card .cover { 
  margin-top: 0px;
  margin-bottom: 5px;
  margin-left: 10px;  
  margin-right: 10px;    
  border-radius: 10px;
}

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

# --- NEW: Hover/active theme-aware providers ---
_LIBRARY_HOVER_LIGHT = b"""
.library-card:hover {
  box-shadow: 0 6px 16px rgba(0,0,0,0.15);
  transform: translateY(-2px);
  background-color: rgba(255,204,102,0.06);
}
.library-card.active {
  background-color: rgba(255,204,102,0.08);
  border: 2px solid #ffcc66;
  box-shadow: 0 6px 18px rgba(255,204,102,0.15);
}
"""

_LIBRARY_HOVER_DARK = b"""
.library-card:hover {
  box-shadow: 0 6px 20px rgba(0,0,0,0.5);
  transform: translateY(-2px);
  background-color: rgba(255,204,102,0.12);
}
.library-card.active {
  background-color: rgba(255,204,102,0.14);
  border: 2px solid #ffcc66;
  box-shadow: 0 6px 22px rgba(255,204,102,0.25);
}
"""

# Hover providers for library cards
_hover_light_provider = Gtk.CssProvider()
_hover_light_provider.load_from_data(_LIBRARY_HOVER_LIGHT)
_hover_dark_provider = Gtk.CssProvider()
_hover_dark_provider.load_from_data(_LIBRARY_HOVER_DARK)

# Apply library hover providers globally (once)
settings = Gtk.Settings.get_default()
display = Gdk.Display.get_default()
prefer_dark = settings.get_property("gtk-application-prefer-dark-theme")
if prefer_dark:
    Gtk.StyleContext.add_provider_for_display(
        display, _hover_dark_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )
else:
    Gtk.StyleContext.add_provider_for_display(
        display, _hover_light_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )

# TTS highlight CSS
THEME_INJECTION_CSS = """
.tts-highlight {
    background: rgba(0,255,0,0.25);
    border-radius: 4px;
    transition: background 0.2s ease;
}
@media (prefers-color-scheme: dark) {
    .tts-highlight {
        background: rgba(0,127,0,0.5);
        box-shadow: 0 0 0 2px rgba(0,127,0,0.75);
    }
}
"""


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
import os
import re
import time
import tempfile
import threading
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------------
# Parallel Optimized TTSEngine
# -------------------------
class TTSEngine:
    def __init__(self, webview_getter, base_temp_dir=None, kokoro_model_path=None, voices_bin_path=None, piper_model_path=None):
        self.webview_getter = webview_getter
        self.base_temp_dir = base_temp_dir or tempfile.gettempdir()
        self.kokoro = None
        self.player = None
        self.playback_finished = True
        self.is_playing_flag = False
        self.should_stop = False
        self.current_thread = None
        self.paused = False
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._tts_sentences = []
        self._tts_sids = []
        self._tts_voice = None
        self._tts_speed = 1.0
        self._tts_lang = "en-us"
        self._tts_finished_callback = None
        self._tts_highlight_callback = None
        self._current_play_index = 0
        self._audio_files = {}
        self._audio_lock = threading.Lock()
        self._synthesis_done = threading.Event()
        self._delayed_timer = None
        self._delayed_timer_lock = threading.Lock()
        
        # NEW: Parallel synthesis control
        self.SYNTHESIS_BUFFER_SIZE = 5  # Synthesize 5 sentences ahead
        self.MAX_PARALLEL_WORKERS = 2   # Number of parallel synthesis threads
        self._synthesis_thread = None
        self._synthesis_stop = threading.Event()
        self._executor = None
        self._active_futures = {}  # Track ongoing synthesis tasks
        self.CLEANUP_THRESHOLD = 8  # Keep last 8 files

        # --------------------------------------------------
        # Optional Dependencies Setup
        # --------------------------------------------------
        self._tts_backend = "piper"  # default
        self.PIPER_AVAILABLE = False
        self.TTS_AVAILABLE = False
        self.Kokoro = None
        self.Gst = None

        # Piper model path
        self.piper_model_path = piper_model_path or os.environ.get(
            "PIPER_MODEL_PATH", str(Path.home() / "Downloads/en_US-libritts-high.onnx")
        )

        # --------------------------------------------------
        # Detect Piper
        # --------------------------------------------------
        try:
            subprocess.run(["piper", "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if os.path.exists(self.piper_model_path):
                self.PIPER_AVAILABLE = True
                print(f"[info] Piper available (model: {self.piper_model_path})")
            else:
                print(f"[warn] Piper model missing: {self.piper_model_path}")
        except FileNotFoundError:
            print("[warn] Piper binary not found; skipping Piper support.")

        # --------------------------------------------------
        # Try to import Kokoro
        # --------------------------------------------------
        try:
            from kokoro_onnx import Kokoro as _Kokoro
            self.Kokoro = _Kokoro
            self.TTS_AVAILABLE = True
            print("[info] Kokoro module available")
        except ImportError as e:
            print(f"[warn] Kokoro unavailable: {e}")

        # --------------------------------------------------
        # Try to import GStreamer
        # --------------------------------------------------
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self.Gst = Gst
            Gst.init(None)
            print("[info] GStreamer initialized")
        except Exception as e:
            print(f"[warn] GStreamer unavailable: {e}")

        # --------------------------------------------------
        # Select Backend (priority: Kokoro > Piper)
        # --------------------------------------------------
        if self.PIPER_AVAILABLE:
            self._tts_backend = "piper"
        elif self.Kokoro:
            self._tts_backend = "kokoro"

        # --------------------------------------------------
        # Initialize Kokoro if selected
        # --------------------------------------------------
        if self._tts_backend == "kokoro":
            try:
                model_path = kokoro_model_path or os.environ.get("KOKORO_ONNX_PATH", "/app/share/kokoro-models/kokoro-v1.0.onnx")
                voices_path = voices_bin_path or os.environ.get("KOKORO_VOICES_PATH", "/app/share/kokoro-models/voices-v1.0.bin")
                if os.path.exists(model_path) and os.path.exists(voices_path):
                    self.kokoro = self.Kokoro(model_path, voices_path)
                    print("[info] Kokoro TTS initialized")
                else:
                    print(f"[warn] Kokoro models not found at {model_path}")
            except Exception as e:
                print(f"[error] Failed to initialize Kokoro: {e}")
                self.kokoro = None
                self._tts_backend = "piper" if self.PIPER_AVAILABLE else None

        # --------------------------------------------------
        # Initialize GStreamer Player
        # --------------------------------------------------
        if self.Gst:
            try:
                self.player = self.Gst.ElementFactory.make("playbin", "player")
                bus = self.player.get_bus()
                bus.add_signal_watch()
                bus.connect("message", self.on_gst_message)
                self.playback_finished = False
            except Exception as e:
                print(f"[warn] GStreamer init failed: {e}")
                self.player = None
                self.playback_finished = True

    def set_backend(self, backend: str):
        backend = backend.lower().strip()
        if backend not in ("kokoro", "piper"):
            print(f"[error] Invalid backend: {backend}")
            return
        if backend == "kokoro" and not self.Kokoro:
            print("[error] Kokoro backend not available.")
            return
        if backend == "piper" and not self.PIPER_AVAILABLE:
            print("[error] Piper backend not available.")
            return
        self._tts_backend = backend
        print(f"[info] TTS backend set to: {backend}")

    def synthesize_piper(self, text, out_path=None):
        """Run Piper TTS and save to file."""
        if not self.PIPER_AVAILABLE:
            print("[error] Piper not available.")
            return None

        if not out_path:
            out_path = os.path.join(self.base_temp_dir, "piper_tts.wav")

        try:
            subprocess.run(
                ["piper", "--model", self.piper_model_path, "--output_file", out_path],
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            print(f"[info] Piper synthesis complete: {out_path}")
            return out_path
        except subprocess.CalledProcessError as e:
            print(f"[error] Piper synthesis failed: {e.stderr.decode().strip()}")
            return None


    def is_playing(self):
        return bool(self.is_playing_flag) and not bool(self.paused)

    def is_paused(self):
        return bool(self.paused)

    def on_gst_message(self, bus, message):
        Gst = self.Gst
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


    def synthesize_sentence(self, sentence, voice, speed, lang):
        """Synthesize a single sentence using the selected TTS backend."""
        base = self.base_temp_dir or tempfile.gettempdir()
        os.makedirs(base, exist_ok=True)
        out_wav = tempfile.NamedTemporaryFile(prefix="tts_", suffix=".wav", delete=False, dir=base).name

        try:
            # --------------------------------------------------
            # Kokoro backend
            # --------------------------------------------------
            if self._tts_backend == "kokoro" and self.kokoro:
                samples, sample_rate = self.kokoro.create(sentence, voice=voice, speed=speed, lang=lang)
                import soundfile as sf
                
                # Write the file
                sf.write(out_wav, samples, sample_rate)
                
                # FIX: Ensure file is fully written and flushed
                try:
                    # Force flush to disk
                    with open(out_wav, 'r+b') as f:
                        f.flush()
                        os.fsync(f.fileno())
                    
                    # Verify file is complete by reading it back
                    time.sleep(0.05)  # Brief pause
                    test_data, test_sr = sf.read(out_wav)
                    if len(test_data) < len(samples) * 0.9:  # Check if at least 90% present
                        print(f"[warn] Audio file may be incomplete, waiting...")
                        time.sleep(0.1)
                except Exception as e:
                    print(f"[warn] Could not verify audio file: {e}")
                    time.sleep(0.1)  # Safety delay
                
                print(f"[info] Kokoro synthesis complete: {out_wav}")
                return out_wav

            # --------------------------------------------------
            # Piper backend
            # --------------------------------------------------
            elif self._tts_backend == "piper" and self.PIPER_AVAILABLE:
                cmd = [
                    "piper",
                    "--model", str(self.piper_model_path),
                    "--output_file", out_wav,
                ]
                try:
                    result = subprocess.run(
                        cmd,
                        input=sentence.encode("utf-8"),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                    
                    # CRITICAL: Wait for Piper to fully complete
                    time.sleep(0.12)  # Increased delay
                    
                    if os.path.exists(out_wav) and os.path.getsize(out_wav) > 0:
                        # Validate WAV file structure
                        try:
                            import wave
                            with wave.open(out_wav, 'rb') as wav:
                                nframes = wav.getnframes()
                                if nframes == 0:
                                    print(f"[error] WAV has 0 frames, skipping")
                                    return None
                                print(f"[info] Piper OK: {nframes} frames")
                        except Exception as e:
                            print(f"[error] WAV validation failed: {e}")
                            return None
                        
                        # Force flush
                        try:
                            with open(out_wav, 'r+b') as f:
                                f.flush()
                                os.fsync(f.fileno())
                        except Exception:
                            pass
                        
                        time.sleep(0.1)  # Extra safety
                        
                        print(f"[info] Piper synthesis complete: {out_wav}")
                        return out_wav
                    else:
                        print(f"[error] Piper produced no output file: {out_wav}")
                        if result.stderr:
                            print(result.stderr.decode(errors="ignore"))
                        return None
                except subprocess.CalledProcessError as e:
                    print(f"[error] Piper synthesis failed: {e.stderr.decode(errors='ignore')}")
                    return None

            # --------------------------------------------------
            # No valid backend
            # --------------------------------------------------
            else:
                print("[warn] No valid TTS backend for synthesis.")
                return None

        except subprocess.CalledProcessError as e:
            print(f"[error] Piper subprocess failed: {e.stderr.decode(errors='ignore')}")
        except Exception as e:
            print(f"[error] TTS synthesis error: {e}")
        return None

    def _synthesize_with_index(self, idx, sentence, voice, speed, lang):
        """
        Wrapper for synthesize_sentence that includes the index.
        Used by parallel synthesis to track which sentence is being synthesized.
        """
        try:
            audio_file = self.synthesize_sentence(sentence, voice, speed, lang)
            return (idx, audio_file)
        except Exception as e:
            print(f"[error] Parallel synthesis failed for idx {idx}: {e}")
            return (idx, None)

    def _parallel_synthesis_worker(self):
        """
        Continuously synthesizes sentences in parallel using a thread pool.
        Maintains a buffer of SYNTHESIS_BUFFER_SIZE sentences ahead.
        """
        try:
            # Create thread pool executor
            self._executor = ThreadPoolExecutor(max_workers=self.MAX_PARALLEL_WORKERS)
            print(f"[parallel] Started synthesis worker with {self.MAX_PARALLEL_WORKERS} workers")
            
            while not self._synthesis_stop.is_set() and not self.should_stop:
                current_idx = self._current_play_index
                
                # Determine which sentences need synthesis
                sentences_to_synthesize = []
                with self._audio_lock:
                    for offset in range(self.SYNTHESIS_BUFFER_SIZE):
                        idx = current_idx + offset
                        if idx >= len(self._tts_sentences):
                            break
                        # Only synthesize if not already done and not currently being synthesized
                        if idx not in self._audio_files and idx not in self._active_futures:
                            sentences_to_synthesize.append(idx)
                
                # Submit synthesis tasks in parallel
                for idx in sentences_to_synthesize:
                    if self._synthesis_stop.is_set() or self.should_stop:
                        break
                    
                    # Submit to thread pool
                    future = self._executor.submit(
                        self._synthesize_with_index,
                        idx,
                        self._tts_sentences[idx],
                        self._tts_voice,
                        self._tts_speed,
                        self._tts_lang
                    )
                    self._active_futures[idx] = future
                    print(f"[parallel] Submitted synthesis for sentence {idx}")
                
                # Check for completed synthesis tasks
                completed_indices = []
                for idx, future in list(self._active_futures.items()):
                    if future.done():
                        try:
                            result_idx, audio_file = future.result(timeout=0.1)
                            if audio_file:
                                with self._audio_lock:
                                    self._audio_files[result_idx] = audio_file
                                print(f"[parallel] Completed synthesis for sentence {result_idx}")
                        except Exception as e:
                            print(f"[error] Failed to get result for idx {idx}: {e}")
                        completed_indices.append(idx)
                
                # Remove completed futures
                for idx in completed_indices:
                    del self._active_futures[idx]
                
                # Brief sleep to avoid busy-waiting
                time.sleep(0.05)
                
        except Exception as e:
            print(f"[error] Parallel synthesis worker: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Shutdown thread pool
            if self._executor:
                print("[parallel] Shutting down synthesis worker...")
                self._executor.shutdown(wait=True, cancel_futures=True)
                print("[parallel] Synthesis worker stopped")

    def speak_sentences_list(self, sentences_with_meta, voice="af_sarah", speed=1.0, lang="en-us",
                             highlight_callback=None, finished_callback=None):
        """
        Start speaking a list of sentences with parallel buffered synthesis.
        
        Args:
            sentences_with_meta: List of sentences (strings or dicts with 'sid' and 'text')
            voice: Voice to use for TTS
            speed: Speed multiplier for TTS
            lang: Language code
            highlight_callback: Callback function for highlighting current sentence
            finished_callback: Callback function when playback completes
        """
        try:
            from gi.repository import GLib
        except ImportError:
            GLib = None
            
        # Ensure at least one backend is ready before continuing
        if not (
            (self._tts_backend == "kokoro" and self.kokoro)
            or (self._tts_backend == "piper" and self.PIPER_AVAILABLE)
        ):
            print(f"[warn] TTS not available (backend={self._tts_backend})")
            if finished_callback:
                if GLib:
                    GLib.idle_add(finished_callback)
                else:
                    finished_callback()
            return

        # Stop any current playback
        self.stop()
        time.sleep(0.05)

        # Reset state
        self.should_stop = False
        self._tts_sentences = []
        self._tts_sids = []
        
        # Parse sentences with metadata
        for s in sentences_with_meta:
            if isinstance(s, dict):
                self._tts_sids.append(s.get("sid"))
                self._tts_sentences.append(s.get("text"))
            else:
                self._tts_sids.append(None)
                self._tts_sentences.append(str(s))

        # Set TTS parameters
        self._tts_voice = voice
        self._tts_speed = speed
        self._tts_lang = lang
        self._tts_finished_callback = finished_callback
        self._tts_highlight_callback = highlight_callback
        self._audio_files = {}
        self._active_futures = {}
        self._current_play_index = 0
        self._synthesis_done.clear()
        self._synthesis_stop.clear()
        self._cancel_delayed_timer()
        self.paused = False
        self._resume_event.set()

        # Start playback using parallel synthesis
        self.start_playback(
            self._tts_sentences,
            self._tts_sids,
            voice,
            speed,
            lang,
            finished_callback,
            highlight_callback
        )

    def _cancel_delayed_timer(self):
        """Cancel delayed synthesis timer"""
        with self._delayed_timer_lock:
            if self._delayed_timer:
                try:
                    self._delayed_timer.cancel()
                except Exception:
                    pass
                self._delayed_timer = None

    def _schedule_delayed_synthesis(self, idx, delay=0.5):
        """Schedule synthesis with a delay"""
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

    def start_playback(self, sentences, sids, voice, speed, lang, finished_callback, highlight_callback):
        """
        Start playback with parallel buffered synthesis.
        Synthesizes multiple sentences at once for faster preparation.
        """
        try:
            from gi.repository import GLib
        except ImportError:
            GLib = None

        self.stop()
        self.should_stop = False
        self.paused = False
        self._resume_event.set()
        self._tts_sentences = sentences
        self._tts_sids = sids
        self._tts_voice = voice
        self._tts_speed = speed
        self._tts_lang = lang
        self._tts_finished_callback = finished_callback
        self._tts_highlight_callback = highlight_callback
        self._current_play_index = 0
        self._audio_files = {}
        self._active_futures = {}
        self._synthesis_done.clear()
        self._synthesis_stop.clear()

        Gst = self.Gst

        def tts_thread():
            try:
                # =====================================================
                # FIX 1: PRIME THE PIPELINE
                # =====================================================
                # Play silent audio first to initialize GStreamer properly
                print("[fix] Priming GStreamer pipeline...")
                try:
                    import numpy as np
                    import soundfile as sf
                    
                    # Create a tiny silent WAV file
                    silence = np.zeros(int(0.1 * 22050))  # 0.1 second of silence at 22050 Hz
                    silent_file = os.path.join(self.base_temp_dir, "prime.wav")
                    sf.write(silent_file, silence, 22050)
                    
                    # Play it to initialize GStreamer properly
                    if self.player:
                        self.player.set_property("uri", f"file://{silent_file}")
                        self.player.set_state(Gst.State.PLAYING)
                        time.sleep(0.15)  # Let it "play" briefly
                        self.player.set_state(Gst.State.NULL)
                        time.sleep(0.05)
                        
                        # Clean up
                        try:
                            os.remove(silent_file)
                        except Exception:
                            pass
                    print("[fix] Pipeline primed successfully")
                except Exception as e:
                    print(f"[warn] Could not prime pipeline: {e}")
                
                # =====================================================
                # ORIGINAL CODE: Start the parallel synthesis worker
                # =====================================================
                self._synthesis_thread = threading.Thread(
                    target=self._parallel_synthesis_worker,
                    daemon=True
                )
                self._synthesis_thread.start()

                self.is_playing_flag = True

                # =====================================================
                # Main playback loop
                # =====================================================
                while self._current_play_index < len(self._tts_sentences) and not self.should_stop:
                    idx = self._current_play_index

                    # Highlight current sentence
                    if self._tts_highlight_callback:
                        GLib.idle_add(self._tts_highlight_callback, idx, {
                            "sid": self._tts_sids[idx],
                            "text": self._tts_sentences[idx]
                        })

                    # Handle pause
                    while self.paused and not self.should_stop:
                        self._cancel_delayed_timer()
                        self._resume_event.wait(0.1)

                    if self.should_stop:
                        break

                    # Wait for audio file (with timeout)
                    audio_file = None
                    waited = 0.0
                    max_wait = 5.0  # Maximum 5 seconds wait
                    
                    while not self.should_stop and waited < max_wait:
                        with self._audio_lock:
                            audio_file = self._audio_files.get(idx)
                        
                        if audio_file:
                            break
                        
                        if self._current_play_index != idx:
                            break
                        
                        time.sleep(0.05)
                        waited += 0.05

                    if self.should_stop:
                        break

                    # If still no audio, synthesize immediately (fallback)
                    if not audio_file:
                        print(f"[fallback] Synthesizing {idx} immediately")
                        audio_file = self.synthesize_sentence(
                            self._tts_sentences[idx],
                            self._tts_voice,
                            self._tts_speed,
                            self._tts_lang
                        )
                        if audio_file:
                            with self._audio_lock:
                                self._audio_files[idx] = audio_file

                    if not audio_file:
                        print(f"[warn] No audio for {idx}, skipping")
                        self._current_play_index = idx + 1
                        continue

                    if self.paused:
                        continue

                    # =====================================================
                    # FIX 2: PROPER PLAYBACK WITH STATE TRANSITIONS
                    # =====================================================
                    # Play the audio
                    if self.player:
                        try:
                            self.player.set_property("uri", f"file://{audio_file}")
                            
                            # FIX: Proper state transitions for first sentence
                            if idx == 0:
                                print("[fix] First sentence - using proper state transitions")
                                # Go through proper GStreamer state progression
                                self.player.set_state(Gst.State.NULL)
                                time.sleep(0.05)
                                
                                self.player.set_state(Gst.State.READY)
                                time.sleep(0.15)
                                
                                # PAUSED state allows GStreamer to preroll/buffer
                                self.player.set_state(Gst.State.PAUSED)
                                time.sleep(0.15)
                                
                                # Now play
                                self.player.set_state(Gst.State.PLAYING)
                                print("[fix] First sentence playback started")
                            else:
                                # Normal playback for subsequent sentences
                                self.player.set_state(Gst.State.PLAYING)
                            
                            self.playback_finished = False
                        except Exception as e:
                            print("player error:", e)
                            self.playback_finished = True
                    else:
                        self.playback_finished = True
                        time.sleep(0.05)

                    # Wait for playback to finish
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

                    # Stop playback
                    try:
                        if self.player:
                            self.player.set_state(Gst.State.NULL)
                    except Exception:
                        pass

                    time.sleep(0.1)  # Give GStreamer time to close file handles    
                    
                    # Clean up old audio files to save memory
                    if (self._current_play_index == idx) and (not self.paused):
                        try:
                            with self._audio_lock:
                                cleanup_threshold = max(0, idx - 8)  # Keep last 8 files
                                files_to_remove = [i for i in self._audio_files.keys() if i < cleanup_threshold]
                                
                                for i in files_to_remove:
                                    af = self._audio_files.get(i)
                                    if af:
                                        try:
                                            os.remove(af)
                                            del self._audio_files[i]
                                            # No logging unless it fails
                                        except Exception as e:
                                            # File still in use, skip silently
                                            pass
                        except Exception:
                            pass
                        
                        self._current_play_index = idx + 1

                # Cleanup
                self.is_playing_flag = False
                self._synthesis_stop.set()  # Stop the synthesis worker
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
        Gst = self.Gst    
        with self._audio_lock:
            self._current_play_index = min(len(self._tts_sentences)-1, self._current_play_index + 1)
            idx = self._current_play_index
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx, {
                "sid": self._tts_sids[idx],
                "text": self._tts_sentences[idx]
            })
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def prev_sentence(self):
        if not self._tts_sentences:
            return
        Gst = self.Gst    
        with self._audio_lock:
            self._current_play_index = max(0, self._current_play_index - 1)
            idx = self._current_play_index
        if self._tts_highlight_callback:
            GLib.idle_add(self._tts_highlight_callback, idx, {
                "sid": self._tts_sids[idx],
                "text": self._tts_sentences[idx]
            })
        try:
            if self.player:
                self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._schedule_delayed_synthesis(idx, delay=0.5)

    def pause(self):
        Gst = self.Gst        
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
        Gst = self.Gst
        self.should_stop = True
        self.paused = False
        self.playback_finished = True
        self._synthesis_stop.set()  # Stop synthesis worker
        
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
        
        # Cancel all pending futures
        if self._active_futures:
            for future in self._active_futures.values():
                future.cancel()
            self._active_futures.clear()
        
        # Wait for threads to finish
        if self.current_thread:
            try:
                self.current_thread.join(timeout=1.0)
            except Exception:
                pass
        
        if self._synthesis_thread:
            try:
                self._synthesis_thread.join(timeout=1.0)
            except Exception:
                pass
        
        # Shutdown executor
        if self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        
        # Clean up all audio files
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

        # Theme system - initialize CSS providers first (before _init_themes)
        self._css_light_provider = None
        self._css_dark_provider = None
        self._init_themes()

        # NEW: column settings - only width-based mode
        self.column_width_px = 300           # 50..500 px
        self._column_gap = 50                # px gap between columns
        
        # Font and text settings defaults
        # No default font size - use epub's original fonts
        self.user_justify = "full"           # default justification
        self.user_line_height = 1.50         # default line height

        
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
        header = Adw.HeaderBar(); header.add_css_class("flat"); 
        self.library_btn = Gtk.Button(icon_name="show-library-symbolic"); self.library_btn.add_css_class("flat")
        self.library_btn.set_tooltip_text("Show Library"); self.library_btn.connect("clicked", self.on_library_clicked)
        header.pack_start(self.library_btn)
        title_lbl = Gtk.Label(label=APP_NAME); title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_title_widget(title_lbl); sidebar_box.append(header)

        # Book cover + metadata
        book_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        book_box.set_valign(Gtk.Align.CENTER)
        book_box.set_margin_top(0); book_box.set_margin_bottom(0)
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

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_hexpand(True)

        self.book_title = Gtk.Label(label="")
        self.book_title.add_css_class("book-title")
        self.book_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_title.set_lines(2)
        self.book_title.set_halign(Gtk.Align.START)
        self.book_title.set_valign(Gtk.Align.CENTER)
        self.book_title.set_xalign(0.0)
        
        self.book_author = Gtk.Label(label="")
        self.book_author.add_css_class("book-author")
        self.book_author.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_author.set_halign(Gtk.Align.START)
        self.book_author.set_valign(Gtk.Align.CENTER)
        self.book_author.set_lines(2)
        self.book_author.set_margin_top(0)
        self.book_author.set_xalign(0.0)

        text_box.append(self.book_title)
        text_box.append(self.book_author)
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

        font_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        font_box.set_margin_top(6); font_box.set_margin_bottom(6); font_box.set_margin_start(6); font_box.set_margin_end(6)
        font_box.set_hexpand(True)
        font_box.append(Gtk.Label(label="Font"))

        # horizontal row: family + size
        font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_row.set_valign(Gtk.Align.CENTER)
        # build dropdowns (methods below will set self.font_dropdown / self.font_size_dropdown)
        try:
            self.setup_font_dropdown()
            self.setup_font_size_dropdown()
            # place them
            if hasattr(self, "font_dropdown"):
                font_row.append(self.font_dropdown)
            if hasattr(self, "font_size_dropdown"):
                font_row.append(self.font_size_dropdown)
        except Exception as e:
            print("font tab init error:", e)

        font_box.append(font_row)
        self.side_stack.add_titled(font_box, "font", "Font")
        
        # --- append after the existing font_row creation in your sidebar setup ---

        # ensure default column gap exists
        if not hasattr(self, "_column_gap"):
            self._column_gap = 24

        # row for extra font controls: column gap, justification, line-height
        extra_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        extra_row.set_valign(Gtk.Align.CENTER)

        # Column gap spin (px)
        gap_adj = Gtk.Adjustment(value=getattr(self, "_column_gap", 50), lower=0, upper=200, step_increment=1, page_increment=10)
        self.col_gap_spin = Gtk.SpinButton.new(gap_adj, climb_rate=1.0, digits=0)
        self.col_gap_spin.set_tooltip_text("Column gap (px)")
        def on_gap_changed(spin):
            try:
                self._column_gap = int(spin.get_value())
                # rebuild current page so col_rules are applied with new gap
                self.display_page()
            except Exception:
                pass
        self.col_gap_spin.connect("value-changed", lambda s: on_gap_changed(s))
        extra_row.append(self.col_gap_spin)

        # Justification dropdown: None, Full, With hyphenation
        justify_list = Gtk.StringList()
        for t in ("None", "Full", "With hyphenation"):
            justify_list.append(t)
        self.justify_dropdown = Gtk.DropDown(model=justify_list)
        self.justify_dropdown.set_tooltip_text("Justification")
        # set initial selection from stored setting
        try:
            curj = getattr(self, "user_justify", "full")  # default to "full"
            idx = 1  # default to Full
            if curj == "none": idx = 0
            elif curj == "full": idx = 1
            elif curj == "hyphen": idx = 2
            self.justify_dropdown.set_selected(idx)
        except Exception:
            self.justify_dropdown.set_selected(1)  # default to Full
        def on_justify_notify(dd, prop):
            try:
                sel = dd.get_selected_item()
                if not sel: return
                s = sel.get_string()
                if s == "None": val = "none"
                elif s == "Full": val = "full"
                else: val = "hyphen"
                self.user_justify = val
                # rebuild page so overrides are re-injected
                self.display_page()
            except Exception:
                pass
        self.justify_dropdown.connect("notify::selected", on_justify_notify)
        extra_row.append(self.justify_dropdown)

        # Line-height spin (0.80 .. 3.00 step 0.05)
        lh_adj = Gtk.Adjustment(value=getattr(self, "user_line_height", 1.50), lower=0.8, upper=3.0, step_increment=0.05, page_increment=0.1)
        self.line_height_spin = Gtk.SpinButton.new(lh_adj, climb_rate=0.05, digits=2)
        self.line_height_spin.set_tooltip_text("Line height (0.8 - 3.0)")
        def on_lh_changed(spin):
            try:
                self.user_line_height = round(float(spin.get_value()), 2)
                self.display_page()
            except Exception:
                pass
        self.line_height_spin.connect("value-changed", lambda s: on_lh_changed(s))
        extra_row.append(self.line_height_spin)

        # Put extra_row under the font_row in the font_box
        font_box.append(extra_row)

        # defaults
        if not hasattr(self, "page_margin_top"):
            self.page_margin_top = 50
            self.page_margin_right = 50
            self.page_margin_bottom = 50
            self.page_margin_left = 50
        if not hasattr(self, "_margins_linked"):
            self._margins_linked = True

        m_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        m_row.set_valign(Gtk.Align.CENTER)

        label = Gtk.Label(label="Page margins (px)", halign=Gtk.Align.START)
        m_row.append(label)

        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # individual spins
        def make_margin_spin(attr_name):
            val = int(getattr(self, attr_name, 12))
            adj = Gtk.Adjustment(value=val, lower=0, upper=500, step_increment=1, page_increment=10)
            sp = Gtk.SpinButton.new(adj, climb_rate=1.0, digits=0)
            sp.set_tooltip_text(attr_name.replace("_"," ").title())
            return sp

        self.margin_top_spin = make_margin_spin("page_margin_top")
        self.margin_right_spin = make_margin_spin("page_margin_right")
        self.margin_bottom_spin = make_margin_spin("page_margin_bottom")
        self.margin_left_spin = make_margin_spin("page_margin_left")

        # store spins dict
        _spins = {
            "top": self.margin_top_spin,
            "right": self.margin_right_spin,
            "bottom": self.margin_bottom_spin,
            "left": self.margin_left_spin,
        }

        # handler ids store
        self._margin_handler_ids = {}

        def _apply_margins_to_attrs(values):
            self.page_margin_top = int(values["top"])
            self.page_margin_right = int(values["right"])
            self.page_margin_bottom = int(values["bottom"])
            self.page_margin_left = int(values["left"])

        def on_margin_changed(spin, side):
            try:
                new = int(spin.get_value())
                if getattr(self, "_margins_linked", False):
                    # block other handlers, sync values
                    for sname, sspin in _spins.items():
                        if sname == side:
                            continue
                        hid = self._margin_handler_ids.get(sname)
                        if hid:
                            sspin.handler_block(hid)
                        sspin.set_value(new)
                        if hid:
                            sspin.handler_unblock(hid)
                    _apply_margins_to_attrs({"top": new, "right": new, "bottom": new, "left": new})
                else:
                    setattr(self, f"page_margin_{side}", new)
                # rebuild page so new padding is used
                try:
                    self.display_page()
                except Exception:
                    pass
            except Exception:
                pass

        # connect and save handler ids
        self._margin_handler_ids["top"] = self.margin_top_spin.connect("value-changed", lambda s: on_margin_changed(s, "top"))
        self._margin_handler_ids["right"] = self.margin_right_spin.connect("value-changed", lambda s: on_margin_changed(s, "right"))
        self._margin_handler_ids["bottom"] = self.margin_bottom_spin.connect("value-changed", lambda s: on_margin_changed(s, "bottom"))
        self._margin_handler_ids["left"] = self.margin_left_spin.connect("value-changed", lambda s: on_margin_changed(s, "left"))

        # layout: show as Top Right Bottom Left and a Link checkbox
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.attach(Gtk.Label(label="Top"), 0, 0, 1, 1)
        grid.attach(self.margin_top_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Right"), 2, 0, 1, 1)
        grid.attach(self.margin_right_spin, 3, 0, 1, 1)
        grid.attach(Gtk.Label(label="Bottom"), 0, 1, 1, 1)
        grid.attach(self.margin_bottom_spin, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Left"), 2, 1, 1, 1)
        grid.attach(self.margin_left_spin, 3, 1, 1, 1)

        # link checkbox
        self.link_margins_chk = Gtk.CheckButton(label="Link margins")
        self.link_margins_chk.set_active(getattr(self, "_margins_linked", True))
        def on_link_toggled(cb):
            self._margins_linked = cb.get_active()
            if self._margins_linked:
                # sync all to top value
                topv = int(self.margin_top_spin.get_value())
                # block handlers while syncing
                for sname, sspin in _spins.items():
                    hid = self._margin_handler_ids.get(sname)
                    if hid:
                        sspin.handler_block(hid)
                    sspin.set_value(topv)
                    if hid:
                        sspin.handler_unblock(hid)
                _apply_margins_to_attrs({"top": topv, "right": topv, "bottom": topv, "left": topv})
                try:
                    self.display_page()
                except Exception:
                    pass

        self.link_margins_chk.connect("toggled", on_link_toggled)

        m_row.append(grid)
        m_row.append(self.link_margins_chk)

        # append to your font box (or appropriate container)
        font_box.append(m_row)
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
        self.tab_font = make_icon_tab("format-text-rich-symbolic", "Font", "font")
        self.tab_toc.set_active(True)
        tabs_box.append(self.tab_toc); tabs_box.append(self.tab_ann); tabs_box.append(self.tab_bm); tabs_box.append(self.tab_read); tabs_box.append(self.tab_font)
        sidebar_box.append(tabs_box)

        self.split.set_sidebar(sidebar_box)

        # --- Content area ---
        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_css_class("pg-bg")
        self.content_header = Adw.HeaderBar(); 
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



        # --- NEW: Columns menu button - only width-based columns. Hidden by default; shown in reading mode only. ---
        self.columns_menu_button = Gtk.MenuButton()
        self.columns_menu_button.set_icon_name("columns-symbolic")
        self.columns_menu_button.add_css_class("flat")
        # build Gio.Menu model with column width options
        menu = Gio.Menu()

        width_menu = Gio.Menu()
        for w in (50,100,150, 180, 200,250,300,350,400,450,500, 550, 600, 650, 700, 750, 800, 850, 900, 950, 1000):
            width_menu.append(f"{w}px width", f"app.set-column-width({w})")
        menu.append_submenu("Column width", width_menu)
        self.columns_menu_button.set_menu_model(menu)
        self.columns_menu_button.set_visible(False)
        self.content_header.pack_end(self.columns_menu_button)
        # --- end columns menu ---


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


        menu_model = Gio.Menu();
        # --- Theme submenu ---
        self.themes = {
            "Sepia": ("#5b4636", "#f1e8d0"),
            "Gray": ("#222222", "#e0e0e0"),
            "Grass": ("#242d17", "#d7dbbd"),
            "Cherry": ("#4e1609", "#f0d1d5"),
            "Sky": ("#262d48", "#cedef5"),
            "Green": ("#111111", "#8acf00"),
            "Solarized": ("#002b36", "#fdf6e3"),
            "Turmeric": ("#28282c", "#FFcf00"),
            "Purple Gold": ("#451843", "#FFcf00"),
            "Green2": ("#8acf00", "#004b01"),
            "Blue Yellow": ("#fbfc33", "#010745"),
            "Blue Black": ("#050505", "#71cfef"),
        }

        theme_menu = Gio.Menu()
        for name in self.themes.keys():
            theme_menu.append(name, f"app.set-theme('{name}')")
        menu_model.append_submenu("Theme", theme_menu)

        # --- About and other items ---
        menu_model.append("About", "app.about")
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
        # WebKit fallback
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit
            self.WebKit = WebKit
            self.webview = WebKit.WebView()
            
            # ENABLE JAVASCRIPT
            try:
                settings = self.webview.get_settings()
                settings.set_enable_javascript(True)
                settings.set_javascript_can_access_clipboard(False)
                settings.set_enable_developer_extras(True)
                print("✓ WebKit JavaScript enabled:", settings.get_enable_javascript())
            except Exception as e:
                print(f"⚠ Could not configure WebKit settings: {e}")
            
            self.scrolled.set_child(self.webview)
            try: 
                self.webview.connect("decide-policy", self.on_decide_policy)
                # REMOVED: self.webview.connect("console-message", self._on_webconsole_message)
                print("✓ WebKit decide-policy connected")
            except Exception as e:
                print(f"⚠ Could not connect WebKit handlers: {e}")
                
            # ADD CONSOLE MESSAGE HANDLER (like scrollEvent)
            try:
                content_manager = self.webview.get_user_content_manager()
                content_manager.connect("script-message-received::consoleLog", 
                                        self._on_console_log_received)
                content_manager.register_script_message_handler("consoleLog")
                print("✓ Console log handler registered")
            except Exception as e:
                print(f"⚠ Could not register console handler: {e}")
            # after creating self.webview (inside __init__), add:
            try:
                def _on_load_changed(webview, load_event):
                    print(f"🔄 load-changed event: {load_event}")
                    # WebKit2.LoadEvent.FINISHED is enum; use numeric check to be safe
                    try:
                        # 3 == FINISHED in many WebKit builds; do robust check if WebKit.LoadEvent exists
                        finished = False
                        try:
                            finished = (load_event == getattr(self.WebKit, 'LoadEvent').FINISHED)
                            print(f"  ✓ Checked via LoadEvent.FINISHED: {finished}")
                        except Exception as e:
                            print(f"  ⚠ LoadEvent check failed: {e}")
                            finished = (int(load_event) == 3)
                            print(f"  ✓ Checked via int==3: {finished}, load_event={load_event}")
                        
                        print(f"  → finished = {finished}")
                        
                        if finished:
                            # install observer and reapply user settings after page is ready
                            self._install_persistent_user_style()
                            self._reapply_user_font_override()
                            
                            print("📊 Page load finished, running font detection...")
                            
                            # Log the EPUB's original font and font-size
                            js_detect_font = """
                            console.log('🔤 Starting font detection...');
                            setTimeout(function() {
                                console.log('🔤 Font detection timer fired');
                                try {
                                    var content = document.querySelector('.ebook-content') || document.body;
                                    console.log('🔤 Content element found:', content ? 'YES' : 'NO');
                                    if (content) {
                                        console.log('=== EPUB FONT INFO ===');
                                        
                                        // Check container
                                        var style = window.getComputedStyle(content);
                                        console.log('Container (.ebook-content):');
                                        console.log('  Font Family: ' + style.fontFamily);
                                        console.log('  Font Size: ' + style.fontSize);
                                        
                                        // Check various text elements
                                        var elements = [
                                            {selector: 'p', name: 'Paragraph <p>'},
                                            {selector: 'div', name: 'Div <div>'},
                                            {selector: 'span', name: 'Span <span>'},
                                            {selector: 'h1, h2, h3, h4, h5, h6', name: 'Heading'},
                                            {selector: '.body, .bodytext, .text', name: 'Body class'}
                                        ];
                                        
                                        var fontSizes = {};
                                        
                                        elements.forEach(function(el) {
                                            var elem = content.querySelector(el.selector);
                                            if (elem) {
                                                var s = window.getComputedStyle(elem);
                                                var size = s.fontSize;
                                                var family = s.fontFamily;
                                                console.log(el.name + ':');
                                                console.log('  Font: ' + family);
                                                console.log('  Size: ' + size);
                                                
                                                // Track unique sizes
                                                if (!fontSizes[size]) {
                                                    fontSizes[size] = [];
                                                }
                                                fontSizes[size].push(el.name);
                                            }
                                        });
                                        
                                        // Show summary of sizes
                                        console.log('---');
                                        console.log('Font sizes found:');
                                        Object.keys(fontSizes).sort().forEach(function(size) {
                                            console.log('  ' + size + ': ' + fontSizes[size].join(', '));
                                        });
                                        
                                        console.log('======================');
                                    }
                                } catch(e) {
                                    console.log('❌ Error detecting font: ' + e.message);
                                }
                            }, 200);
                            """
                            try:
                                webview.evaluate_javascript(js_detect_font, -1, None, None, None, None, None)
                                print("✓ Font detection JS executed via evaluate_javascript")
                            except Exception as e:
                                print(f"⚠ evaluate_javascript failed: {e}")
                                try:
                                    webview.run_javascript(js_detect_font, None, None, None)
                                    print("✓ Font detection JS executed via run_javascript")
                                except Exception as e2:
                                    print(f"❌ run_javascript also failed: {e2}")
                    except Exception as e:
                        print(f"❌ Exception in load-changed handler: {e}")
                        import traceback
                        traceback.print_exc()
                    return False
                # connect if available
                if getattr(self, "webview", None) and getattr(self.webview, "connect", None):
                    try:
                        self.webview.connect("load-changed", _on_load_changed)
                    except Exception:
                        # older/newer signatures may differ; attempt connect with two-arg lambda
                        try:
                            self.webview.connect("load-changed", lambda w, e: _on_load_changed(w, e))
                        except Exception:
                            pass
            except Exception:
                pass
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

        # Column width action
        action = Gio.SimpleAction.new("set-column-width", GLib.VariantType.new("i"))
        action.connect("activate", lambda a, p: self.set_column_width(p.get_int32()))
        self.add_action(action)

        # Theme change action
        action = Gio.SimpleAction.new("set-theme", GLib.VariantType.new("s"))
        action.connect("activate", lambda a, p: self.apply_theme(p.get_string()))
        self.add_action(action)

    def _init_themes(self):
        """Initialize all theme-related data as instance variables"""
        # Available themes (text_color, page_bg_color)
        self.themes = {
            "Default Light": ("#000000", "#ffffff"),  # Default light mode
            "Default Dark": ("#e0e0e0", "#000000"),   # Default dark mode
            "Sepia": ("#5b4636", "#f1e8d0"),
            "Gray": ("#222222", "#e0e0e0"),
            "Grass": ("#242d17", "#d7dbbd"),
            "Cherry": ("#4e1609", "#f0d1d5"),
            "Sky": ("#262d48", "#cedef5"),
            "Green": ("#111111", "#8acf00"),
            "Solarized": ("#002b36", "#fdf6e3"),
            "Turmeric": ("#28282c", "#FFcf00"),
            "Purple Gold": ("#451843", "#FFcf00"),
            "Green2": ("#8acf00", "#004b01"),
            "Blue Yellow": ("#fbfc33", "#010745"),
            "Blue Black": ("#050505", "#71cfef"),
        }
        
        # Check system theme preference to set initial theme
        settings = Gtk.Settings.get_default()
        prefer_dark = settings.get_property("gtk-application-prefer-dark-theme")
        
        # Set initial theme based on system preference
        if prefer_dark:
            initial_theme = "Default Dark"
        else:
            initial_theme = "Default Light"
        
        # Current theme colors (will be updated by apply_theme)
        self.text_fg, self.page_bg = self.themes.get(initial_theme, ("#000000", "#ffffff"))
        self.sidebar_bg = darken(self.page_bg, 0.9)
        
        # Light theme colors
        self.page_bg_light = "#ffffff"
        self.text_fg_light = "#000000"
        self.sidebar_bg_light = "#ececec"
        
        # Dark theme colors  
        self.page_bg_dark = "#000000"
        self.text_fg_dark = "#e0e0e0"
        self.sidebar_bg_dark = "#2d2d2d"
        
        # Apply the initial theme
        self.apply_theme(initial_theme)

    def _on_console_log_received(self, content_manager, js_result):
        """Handle console.log messages from JavaScript"""
        try:
            msg = js_result.to_string()
            print(f"[JS] {msg}")
        except Exception as e:
            print(f"[JS Error] Could not read message: {e}")

    def apply_theme(self, theme_name):
        """Apply a theme by name, updating instance variables and regenerating CSS"""
        # Get theme colors and update instance variables
        self.text_fg, self.page_bg = self.themes.get(theme_name, ("#000000", "#FFFFFF"))
        
        # Update derived colors
        self.sidebar_bg = darken(self.page_bg, 0.9)
        
        # Detect if we're using a "Default" theme to use proper light/dark colors
        settings = Gtk.Settings.get_default()
        prefer_dark = settings.get_property("gtk-application-prefer-dark-theme")
        
        # Use proper defaults for light/dark modes
        if theme_name == "Default Light" or (not prefer_dark and theme_name.startswith("Default")):
            self.text_fg = self.text_fg_light
            self.page_bg = self.page_bg_light
            self.sidebar_bg = self.sidebar_bg_light
        elif theme_name == "Default Dark" or (prefer_dark and theme_name.startswith("Default")):
            self.text_fg = self.text_fg_dark
            self.page_bg = self.page_bg_dark
            self.sidebar_bg = self.sidebar_bg_dark
        
        # Regenerate light theme CSS
        css_light = f"""
            .pg-bg {{
                background-color: {self.page_bg};
                color: {self.text_fg};
            }}
            .epub-sidebar {{
                background-color: {self.sidebar_bg};
                color: {self.text_fg};
            }}
            .epub-sidebar .adw-action-row:hover {{
                background-color: rgba(0,0,0,0.06);
            }}
            .epub-sidebar .adw-action-row.selected {{
                background-color: rgba(0,0,0,0.12);
            }}
            .epub-sidebar .adw-action-row {{
                background-color: {self.sidebar_bg};
            }}
            .book-title {{
                font-weight: 600;
            }}
            .book-author {{
                color: rgba(0,0,0,0.6);
                font-size: 12px;
            }}
        """
        
        # Regenerate dark theme CSS (for when system is in dark mode)
        css_dark = f"""
            .pg-bg {{
                background-color: {self.page_bg};
                color: {self.text_fg};
            }}
            .epub-sidebar {{
                background-color: {self.sidebar_bg};
                color: {self.text_fg};
            }}
            .epub-sidebar .adw-action-row:hover {{
                background-color: rgba(255,255,255,0.1);
            }}
            .epub-sidebar .adw-action-row.selected {{
                background-color: rgba(255,255,255,0.15);
            }}
            .epub-sidebar .adw-action-row {{
                background-color: {self.sidebar_bg};
            }}
            .book-title {{
                font-weight: 600;
            }}
            .book-author {{
                color: rgba(255,255,255,0.7);
                font-size: 12px;
            }}
        """
        
        # Remove old providers if they exist
        display = Gdk.Display.get_default()
        if hasattr(self, '_css_light_provider') and self._css_light_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(display, self._css_light_provider)
            except:
                pass
        if hasattr(self, '_css_dark_provider') and self._css_dark_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(display, self._css_dark_provider)
            except:
                pass
        
        # Create new CSS providers
        self._css_light_provider = Gtk.CssProvider()
        self._css_light_provider.load_from_data(css_light.encode())
        
        self._css_dark_provider = Gtk.CssProvider()
        self._css_dark_provider.load_from_data(css_dark.encode())
        
        # Add appropriate provider based on system theme
        if prefer_dark:
            Gtk.StyleContext.add_provider_for_display(
                display, self._css_dark_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            Gtk.StyleContext.add_provider_for_display(
                display, _hover_dark_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        else:
            Gtk.StyleContext.add_provider_for_display(
                display, self._css_light_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            Gtk.StyleContext.add_provider_for_display(
                display, _hover_light_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        
        # CRITICAL: Update webview by reloading the current page with new colors
        # This ensures the wrap_html uses the updated self.page_bg and self.text_fg
        if hasattr(self, 'webview') and self.webview and hasattr(self, 'book') and self.book:
            try:
                # Force a full reload of the current page to update colors in the HTML
                self.display_page()
            except Exception as e:
                print(f"Error updating webview theme: {e}")
        
        print(f"✓ Theme '{theme_name}' applied: fg={self.text_fg}, bg={self.page_bg}")


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
        """Return list of {'sid': idx, 'text': sentence} for the current item.
        Extracts sentences from the sanitized/cleaned HTML so TTS and highlighting match."""
        if not self.book or not self.items or self.current_index >= len(self.items):
            return []
        item = self.items[self.current_index]
        try:
            raw = ""
            try:
                raw = item.get_content() or ""
            except Exception:
                raw = ""
            # use the same sanitizer so sentence extraction matches displayed DOM
            try:
                cleaned_html = self.generic_clean_html(raw)
            except Exception:
                cleaned_html = raw

            soup = BeautifulSoup(cleaned_html, "html.parser")

            # ----- NORMALIZE DROPCAP / single-letter inline elements -----
            # Merge <span class="dropcap">I</span> + following text into one text node
            try:
                def is_drop_like(tag):
                    if not getattr(tag, "name", None):
                        return False
                    # class contains 'drop' / 'dropcap' etc
                    if tag.has_attr("class"):
                        for c in tag.get("class", []):
                            try:
                                if "drop" in c.lower():
                                    return True
                            except Exception:
                                continue
                    # inline style like float:left often used for dropcaps
                    if tag.has_attr("style") and "float:left" in (tag["style"] or "").replace(" ", "").lower():
                        return True
                    return False

                # Handle inline dropcap elements first (span, a, i, b, etc.)
                for tag in list(soup.find_all(is_drop_like)):
                    # only act when tag is small (single letter or very short)
                    try:
                        txt = tag.get_text(strip=True) or ""
                        if not txt:
                            tag.decompose()
                            continue
                        if len(txt) <= 3:
                            nxt = tag.next_sibling
                            if isinstance(nxt, NavigableString):
                                # attach without extra space (trim leading space of next)
                                new = txt + re.sub(r'^\s+', '', str(nxt))
                                nxt.replace_with(new)
                                tag.decompose()
                            else:
                                # no direct text sibling: replace tag with its text (unwrap)
                                tag.replace_with(txt)
                        else:
                            # longer element that happens to have 'drop' class: unwrap to keep text continuity
                            tag.replace_with(txt)
                    except Exception:
                        try:
                            tag.replace_with(tag.get_text(separator=' ', strip=True) or "")
                        except Exception:
                            try: tag.decompose()
                            except Exception: pass
            except Exception:
                pass
            # ----- end dropcap normalization -----

            # Block-level tags that should create sentence boundaries
            block_tags = {'h1','h2','h3','h4','h5','h6','p','div','li',
                          'blockquote','td','th','section','article','header',
                          'footer','nav','aside','pre'}

            sentences = []

            def extract_sentences_from_element(element):
                """Recursively extract sentences from an element."""
                if isinstance(element, NavigableString):
                    return  # skip raw strings at this level

                if not isinstance(element, Tag):
                    return

                if element.name in block_tags:
                    # Collect only direct inline text (exclude nested block element text)
                    text_parts = []
                    for child in element.children:
                        if isinstance(child, NavigableString):
                            txt = str(child).strip()
                            if txt:
                                text_parts.append(txt)
                        elif isinstance(child, Tag) and child.name not in block_tags:
                            txt = child.get_text(separator=' ', strip=True)
                            if txt:
                                text_parts.append(txt)

                    block_text = ' '.join(text_parts).strip()
                    if block_text:
                        block_sentences = self._split_text_into_sentences(block_text)
                        sentences.extend(block_sentences)

                    # Recurse into nested block elements only
                    for child in element.children:
                        if isinstance(child, Tag) and child.name in block_tags:
                            extract_sentences_from_element(child)
                else:
                    # Non-block container: traverse children to find block elements or inline text inside
                    for child in element.children:
                        if isinstance(child, Tag):
                            extract_sentences_from_element(child)
                        elif isinstance(child, NavigableString):
                            txt = str(child).strip()
                            if txt:
                                block_sentences = self._split_text_into_sentences(txt)
                                sentences.extend(block_sentences)

            body = soup.find("body") or soup
            extract_sentences_from_element(body)

            out = []
            sid = 0
            for s in sentences:
                s2 = s.strip()
                if s2:
                    out.append({"sid": sid, "text": s2})
                    sid += 1

            return out
        except Exception as e:
            print(f"Error collecting sentences: {e}")
            return []

###################
    # --- Add these methods inside class EPubViewer (minimal, GTK4) ---
    def _apply_font_global(self, font_name=None, size_pt=None):
        js = f"""
        (function(){{
          let style = document.getElementById('userFontOverride');
          if(!style) {{
            style = document.createElement('style');
            style.id = 'userFontOverride';
            document.head.appendChild(style);
          }}
          let css = '';
          if({json.dumps(font_name)} !== null) {{
            css += `body, .ebook-content {{ font-family: '{font_name}' !important; }}`;
            css += `.ebook-content * {{ font-family: '{font_name}' !important; }}`;
          }}
          if({json.dumps(size_pt)} !== null) {{
            css += `html, body, .ebook-content {{ font-size: {size_pt}pt !important; }}`;
          }}
          style.textContent = css;
        }})();"""
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
        except Exception:
            try: self.webview.run_javascript(js, None, None, None)
            except Exception: pass

    def setup_font_dropdown(self):
        """Create font family dropdown with no selection marks and improved font handling."""
        try:
            font_map = PangoCairo.FontMap.get_default()
            families = font_map.list_families()
            font_names = Gtk.StringList()
            names = sorted([f.get_name() for f in families])
            for n in names:
                font_names.append(n)

            self.font_dropdown = Gtk.DropDown()
            self.font_dropdown.set_tooltip_text("Font Family")
            self.font_dropdown.set_focus_on_click(False)
            self.font_dropdown.set_model(font_names)
            self.font_dropdown.set_size_request(163, -1)
            self.font_dropdown.set_hexpand(False)

            # button factory (display)
            button_factory = Gtk.SignalListItemFactory()
            def setup_button(factory, li):
                lbl = Gtk.Label()
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_xalign(0)
                li.set_child(lbl)
            def bind_button(factory, li):
                pos = li.get_position()
                lbl = li.get_child()
                lbl.set_text(font_names.get_string(pos))
            button_factory.connect("setup", setup_button)
            button_factory.connect("bind", bind_button)
            self.font_dropdown.set_factory(button_factory)

            # list factory (popup)
            list_factory = Gtk.SignalListItemFactory()
            def setup_list(factory, li):
                lbl = Gtk.Label()
                lbl.set_xalign(0)
                li.set_child(lbl)
            def bind_list(factory, li):
                pos = li.get_position()
                li.get_child().set_text(font_names.get_string(pos))
            list_factory.connect("setup", setup_list)
            list_factory.connect("bind", bind_list)
            self.font_dropdown.set_list_factory(list_factory)

            if font_names.get_n_items() > 0:
                try:
                    cur = getattr(self, "user_font_family", None)
                    if cur and cur in names:
                        idx = names.index(cur)
                    else:
                        # Default to a good reading font
                        idx = 0
                        for i, name in enumerate(names):
                            if "serif" in name.lower() and "sans" not in name.lower():
                                idx = i
                                break
                    self.font_dropdown.set_selected(idx)
                except Exception:
                    self.font_dropdown.set_selected(0)

            def _on_font_activate(dd, prop):
                """Handler that applies font family changes with proper heading sizes."""
                try:
                    sel = dd.get_selected_item()
                    if not sel: 
                        return
                    
                    name = sel.get_string()
                    self.user_font_family = name
                    
                    # Apply font family via JavaScript with full heading support
                    js = f"""
                    (function(){{
                      try {{
                        console.log('Applying font family: {name}');
                        
                        window.__user_font_settings = window.__user_font_settings || {{family: null, size: null}};
                        window.__user_font_settings.family = {json.dumps(name)};
                        
                        var styleEl = document.getElementById('userFontOverride');
                        if(!styleEl) {{
                          styleEl = document.createElement('style');
                          styleEl.id = 'userFontOverride';
                          document.head.appendChild(styleEl);
                        }}
                        
                        var fam = window.__user_font_settings.family || '';
                        var sz = window.__user_font_settings.size || '';
                        var css = '';
                        
                        if(fam) {{
                          css += `body, .ebook-content {{ font-family: '${{fam.replace(/'/g, "\\\\'")}}' !important; }}\\n`;
                          css += `.ebook-content * {{ font-family: '${{fam.replace(/'/g, "\\\\'")}}' !important; }}\\n`;
                        }}
                        
                        if(sz) {{
                          var baseFontSize = parseFloat(sz);
                          css += `html, body, .ebook-content {{ font-size: ${{sz}} !important; }}\\n`;
                          css += `.ebook-content h1 {{ font-size: ${{baseFontSize * 2.0}}pt !important; }}\\n`;
                          css += `.ebook-content h2 {{ font-size: ${{baseFontSize * 1.7}}pt !important; }}\\n`;
                          css += `.ebook-content h3 {{ font-size: ${{baseFontSize * 1.4}}pt !important; }}\\n`;
                          css += `.ebook-content h4 {{ font-size: ${{baseFontSize * 1.2}}pt !important; }}\\n`;
                          css += `.ebook-content h5 {{ font-size: ${{baseFontSize * 1.1}}pt !important; }}\\n`;
                          css += `.ebook-content h6 {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                          css += `.ebook-content p, .ebook-content div, .ebook-content span {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                          css += `.ebook-content li {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                          css += `.ebook-content td, .ebook-content th {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        }}
                        
                        styleEl.textContent = css;
                        console.log('Font family applied successfully');
                      }} catch(e) {{ 
                        console.log('Error applying font:', e); 
                      }}
                    }})();
                    """
                    
                    try:
                        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
                    except Exception:
                        try: 
                            self.webview.run_javascript(js, None, None, None)
                        except Exception: 
                            pass
                            
                except Exception as e:
                    print(f"Font family change error: {e}")

            # connect
            self.font_handler_id = self.font_dropdown.connect("notify::selected", _on_font_activate)
        except Exception as e:
            print("setup_font_dropdown error:", e)

             
    def _install_persistent_user_style(self):
        """
        Ensure a persistent <style id="userFontOverride"> is present and kept after any head changes.
        Also installs window.__user_font_settings and a helper to update the style from Python.
        Safe to call repeatedly (idempotent).
        """
        if not getattr(self, "webview", None):
            return
        js = r"""
        (function(){
          try {
            window.__user_font_settings = window.__user_font_settings || {family: null, size: null};
            // create or update style element
            function ensureStyle(){
              var s = document.getElementById('userFontOverride');
              if(!s){
                s = document.createElement('style');
                s.id = 'userFontOverride';
                // low-risk reset rules with high specificity + !important
                s.textContent = '';
                document.head.appendChild(s);
              }
              return s;
            }
            function applySettings(){
              var s = ensureStyle();
              var fam = window.__user_font_settings.family || '';
              var sz  = window.__user_font_settings.size || '';
              var css = '';
              if(fam) css += "body, .ebook-content { font-family: '" + fam.replace(/'/g,"\\'") + "' !important; }\\n";
              if(sz)  css += "html, body, .ebook-content { font-size: " + sz + " !important; }\\n";
              // also apply to common selectors inside EPUB (increase specificity)
              if(fam) css += ".ebook-content * { font-family: '" + fam.replace(/'/g,"\\'") + "' !important; }\\n";
              s.textContent = css;
            }
            // observer: keep our style last in <head> so it overrides earlier rules
            if(!window.__user_font_observer_installed){
              var head = document.head || document.getElementsByTagName('head')[0] || document.documentElement;
              var mo = new MutationObserver(function(muts){
                try {
                  // ensure style exists and is last child of head
                  var s = ensureStyle();
                  if(s.parentNode !== head) head.appendChild(s);
                  else if(head.lastChild !== s) head.appendChild(s);
                } catch(e){}
              });
              try { mo.observe(head, {childList:true, subtree:false}); } catch(e){}
              window.__user_font_observer_installed = true;
            }
            // expose updater for quick calls
            window.__apply_user_font_settings = function(fam, size){
              try {
                window.__user_font_settings = window.__user_font_settings || {family: null, size: null};
                if(typeof fam !== 'undefined' && fam !== null) window.__user_font_settings.family = fam;
                if(typeof size !== 'undefined' && size !== null) window.__user_font_settings.size = size;
                applySettings();
                return true;
              } catch(e){ return false; }
            };
            // apply now if settings present
            applySettings();
          } catch(e) { console.log('user_style_install_error', e); }
        })();
        """
        try:
            # prefer evaluate_javascript, fallback to run_javascript
            try:
                self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js, None, None, None)
                except Exception:
                    pass
        except Exception:
            pass

    def _reapply_user_font_override(self):
        """
        Call this whenever you want to push current self.user_font_family / user_font_size
        into the page. Uses the page's exposed window.__apply_user_font_settings if available,
        otherwise calls a small fallback installer then sets values.
        """
        if not getattr(self, "webview", None):
            return
        fam = getattr(self, "user_font_family", None)
        sz  = getattr(self, "user_font_size", None)
        size_val = (str(sz)+'pt') if sz else None
        
        js = f"""
        (function(){{
          try{{
            if(window.__apply_user_font_settings) {{
              window.__apply_user_font_settings({json.dumps(fam)}, {json.dumps(size_val)});
              return true;
            }}
          }}catch(e){{ }}
          // fallback: (re)install persistent helper then apply
          (function(){{
              try {{
                window.__user_font_settings = window.__user_font_settings || {{family:null,size:null}};
                window.__user_font_settings.family = {json.dumps(fam)};
                window.__user_font_settings.size = {json.dumps(size_val)};
                var s = document.getElementById('userFontOverride');
                if(!s){{ s = document.createElement('style'); s.id='userFontOverride'; document.head.appendChild(s); }}
                var fam = window.__user_font_settings.family || '';
                var sz = window.__user_font_settings.size || '';
                var css = '';
                if(fam) css += "body, .ebook-content {{ font-family: '" + fam.replace(/'/g,"\\\\'") + "' !important; }}\\n";
                if(sz) css += "html, body, .ebook-content {{ font-size: " + sz + " !important; }}\\n";
                if(fam) css += ".ebook-content * {{ font-family: '" + fam.replace(/'/g,"\\\\'") + "' !important; }}\\n";
                s.textContent = css;
                return true;
              }} catch(e){{ return false; }}
          }})();
        }})();
        """
        try:
            try:
                self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js, None, None, None)
                except Exception:
                    pass
        except Exception:
            pass

    def setup_font_size_dropdown(self):
        """Create font size dropdown with standard sizes and improved CSS application."""
        try:
            # Standard font sizes (pt) with better range
            sizes = [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48]
            sl = Gtk.StringList()
            for s in sizes:
                sl.append(f"{s} pt")  # Show "pt" in dropdown for clarity
            
            self.font_size_dropdown = Gtk.DropDown()
            self.font_size_dropdown.set_tooltip_text("Font Size")
            self.font_size_dropdown.set_focus_on_click(False)
            self.font_size_dropdown.set_model(sl)
            self.font_size_dropdown.set_size_request(80, -1)

            # Set default size (15pt is a good reading size)
            try:
                cur_sz = getattr(self, "user_font_size", 15)
                if cur_sz and int(cur_sz) in sizes:
                    idx = sizes.index(int(cur_sz))
                else:
                    idx = sizes.index(15) if 15 in sizes else 6  # Default to 15pt
            except Exception:
                idx = 6  # Fallback to 15pt (index 6 in our list)
            
            self.font_size_dropdown.set_selected(idx)

            def _on_size_activate(dd, prop):
                """Handler that applies font size changes with proper heading scale."""
                try:
                    sel = dd.get_selected_item()
                    if not sel: 
                        return
                    
                    # Parse size from "X pt" format
                    size_str = sel.get_string()
                    size_pt = int(size_str.split()[0])  # Extract number from "X pt"
                    
                    # Store the size
                    self.user_font_size = size_pt
                    
                    # Apply via JavaScript with comprehensive CSS that handles all text elements
                    js = f"""
                    (function(){{
                      try {{
                        console.log('Applying font size: {size_pt}pt');
                        
                        // Store settings globally
                        window.__user_font_settings = window.__user_font_settings || {{family: null, size: null}};
                        window.__user_font_settings.size = '{size_pt}pt';
                        
                        // Get or create the override style element
                        var styleEl = document.getElementById('userFontOverride');
                        if(!styleEl) {{
                          styleEl = document.createElement('style');
                          styleEl.id = 'userFontOverride';
                          document.head.appendChild(styleEl);
                        }}
                        
                        // Build comprehensive CSS
                        var fam = window.__user_font_settings.family || '';
                        var baseFontSize = {size_pt};
                        
                        var css = '';
                        
                        // Set base font size on html and body
                        css += `html, body {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        css += `.ebook-content {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        
                        // Standard heading sizes (relative to base)
                        // These multipliers create a proper type scale
                        css += `.ebook-content h1 {{ font-size: ${{baseFontSize * 2.0}}pt !important; }}\\n`;
                        css += `.ebook-content h2 {{ font-size: ${{baseFontSize * 1.7}}pt !important; }}\\n`;
                        css += `.ebook-content h3 {{ font-size: ${{baseFontSize * 1.4}}pt !important; }}\\n`;
                        css += `.ebook-content h4 {{ font-size: ${{baseFontSize * 1.2}}pt !important; }}\\n`;
                        css += `.ebook-content h5 {{ font-size: ${{baseFontSize * 1.1}}pt !important; }}\\n`;
                        css += `.ebook-content h6 {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        
                        // Body text elements
                        css += `.ebook-content p, .ebook-content div, .ebook-content span {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        css += `.ebook-content li {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        css += `.ebook-content td, .ebook-content th {{ font-size: ${{baseFontSize}}pt !important; }}\\n`;
                        
                        // Special text elements
                        css += `.ebook-content blockquote {{ font-size: ${{baseFontSize * 0.95}}pt !important; }}\\n`;
                        css += `.ebook-content code, .ebook-content pre {{ font-size: ${{baseFontSize * 0.9}}pt !important; }}\\n`;
                        css += `.ebook-content small {{ font-size: ${{baseFontSize * 0.85}}pt !important; }}\\n`;
                        css += `.ebook-content sup, .ebook-content sub {{ font-size: ${{baseFontSize * 0.75}}pt !important; }}\\n`;
                        
                        // Add font family if set
                        if(fam) {{
                          css += `body, .ebook-content {{ font-family: '${{fam.replace(/'/g, "\\\\'")}}' !important; }}\\n`;
                          css += `.ebook-content * {{ font-family: '${{fam.replace(/'/g, "\\\\'")}}' !important; }}\\n`;
                        }}
                        
                        // Apply the CSS
                        styleEl.textContent = css;
                        
                        console.log('Font size applied successfully');
                      }} catch(e) {{ 
                        console.log('Error applying font size:', e); 
                      }}
                    }})();
                    """
                    
                    # Execute the JavaScript
                    try:
                        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
                    except Exception:
                        try: 
                            self.webview.run_javascript(js, None, None, None)
                        except Exception as e:
                            print(f"Error executing font size JS: {e}")
                    
                except Exception as e:
                    print(f"Font size change error: {e}")

            # Connect the signal
            self.font_size_handler_id = self.font_size_dropdown.connect("notify::selected", _on_size_activate)
            
        except Exception as e:
            print("setup_font_size_dropdown error:", e)


    def _split_text_into_sentences(self, text):
        """Improved sentence splitter ignoring common abbreviations and handling smart quotes."""
        if not text or not text.strip():
            return []

        # Convert smart quotes to regular quotes
        # Single quotes
        text = text.replace('\'', "'")  # Left single quotation mark
        text = text.replace('\'', "'")  # Right single quotation mark  
        text = text.replace('`', "'")   # Grave accent (backtick)
        text = text.replace('´', "'")   # Acute accent
        
        # Double quotes
        text = text.replace('"', '"')   # Left double quotation mark
        text = text.replace('"', '"')   # Right double quotation mark
        text = text.replace('``', '"')  # Double grave accent
        text = text.replace('""', '"')  # Double acute accent
        
        # Comprehensive list of common abbreviations (case-insensitive)
        abbrev_pattern = r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Mt|vs|etc|Fig|fig|Eq|eq|Dept|No|pp|Rev|Lt|Col|Gen|Sgt|Capt|Sen|Rep|Gov|Pres|Ave|Rd|Blvd|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Inc|Corp|Ltd|Co|PhD|MD|BA|MA|Bros|viz|al|cf|e\.g|i\.e|et|al)\b'
        
        # Protect abbreviations by replacing dots with a placeholder
        protected_text = re.sub(f'({abbrev_pattern})\\.', 
                            lambda m: m.group(0).replace('.', '∯'), 
                            text, 
                            flags=re.IGNORECASE)

        # Split on sentence-ending punctuation followed by whitespace and capital letter/digit/quotation
        # This pattern looks for: [.!?] + whitespace + [A-Z0-9"'`([] 
        # Using a simpler character class with proper escaping
        sentence_end_pattern = r'(?<=[.!?])\s+(?=[A-Z0-9\'"`\(\[])'
        
        parts = re.split(sentence_end_pattern, protected_text)
        
        # Clean up and restore original dots
        sentences = []
        for part in parts:
            cleaned = part.replace('∯', '.').strip()
            if cleaned:
                sentences.append(cleaned)
        
        return sentences


    def _tts_play(self):
        """Start TTS playback from current chapter (safe wrapper + debug)."""
        if not self.tts:
            print("[TTS] Engine unavailable")
            return

        sentences = self._collect_sentences_for_current_item()
        if not sentences:
            print("[TTS] No sentences to read")
            return

        print(f"[TTS] Starting playback with {len(sentences)} sentences")

        try:
            # Prefer robust wrapping path; if it raises, fall back to direct speak.
            self._ensure_sentence_wrapping_and_start(sentences)
        except Exception as e:
            import traceback
            print("[TTS] Error starting playback:", e)
            traceback.print_exc()
            try:
                self.tts.speak_sentences_list(
                    sentences,
                    highlight_callback=self._on_tts_highlight,
                    finished_callback=self._on_tts_finished
                )
            except Exception as e2:
                print("[TTS] speak_sentences_list fallback failed:", e2)
                import traceback as _tb; _tb.print_exc()


    def _ensure_sentence_wrapping_and_start(self, sentences, auto_start=True):
        """
        Wrap sentences into the webview DOM. Uses a single global text concat + cursor
        so repeated short strings (e.g. "Why?") match in the reading order rather than
        jumping to a later identical occurrence.
        
        Args:
            sentences: List of sentence dicts with 'sid' and 'text'
            auto_start: If True, starts TTS playback. If False, just wraps sentences.
        """
        try:
            if not getattr(self, "webview", None):
                if auto_start:
                    self.tts.speak_sentences_list(
                        sentences,
                        highlight_callback=self._on_tts_highlight,
                        finished_callback=self._on_tts_finished
                    )
                return False

            import json, traceback
            from bs4 import BeautifulSoup

            snippets = []
            try:
                raw_html = ""
                try:
                    item = self.items[self.current_index]
                    raw_html = item.get_content() or ""
                except Exception:
                    raw_html = ""
                try:
                    cleaned_raw_html = self.generic_clean_html(raw_html)
                except Exception:
                    cleaned_raw_html = raw_html
                soup = BeautifulSoup(cleaned_raw_html, "html.parser")

                block_tags = ['p','div','li','section','article','blockquote',
                              'td','th','h1','h2','h3','h4','h5','h6']

                for s_idx, s in enumerate(sentences):
                    st = s["text"] if isinstance(s, dict) else str(s)
                    st_norm = " ".join(st.split())
                    found_html = None
                    if soup and st_norm:
                        candidates = []
                        for tag in soup.find_all(block_tags):
                            try:
                                txt = " ".join(tag.get_text(" ", strip=True).split())
                                if txt and st_norm in txt:
                                    candidates.append((len(txt), tag))
                            except Exception:
                                continue
                        if candidates:
                            candidates.sort(key=lambda x: x[0])
                            tag = candidates[0][1]
                            try:
                                found_html = str(tag)
                            except Exception:
                                found_html = None
                    snippets.append({"sid": s_idx, "text": st, "snippet": found_html})
            except Exception as e:
                print("[TTS] build-snippets error:", e)
                traceback.print_exc()
                for i, s in enumerate(sentences):
                    snippets.append({"sid": i, "text": s.get("text") if isinstance(s, dict) else str(s), "snippet": None})

            snippets_json = json.dumps(snippets)

            js = """
            (function(){
              try {
                var cont = document.querySelector('.ebook-content') || document.body;
                if(!cont) { console.log('[TTS-DBG] no content container'); return; }

                if(!document.querySelector('style[data-tts-style]')) {
                  var st = document.createElement('style');
                  st.dataset.ttsStyle = '1';
                  st.textContent = '.tts-highlight{background:rgba(0,200,0,0.14);border-radius:4px;} .tts-sentence{display:inline;}';
                  document.head.appendChild(st);
                }

                // unwrap previous wraps
                (function(){
                  try {
                    var olds = cont.querySelectorAll('.tts-sentence');
                    olds.forEach(function(e){
                      var parent = e.parentNode;
                      while(e.firstChild) parent.insertBefore(e.firstChild, e);
                      parent.removeChild(e);
                    });
                  } catch(e){ console.log('[TTS-DBG] pre-unwrap error', e); }
                })();

                var data = %s;

                // build global text concat + mapping once
                function buildGlobalMapping(root){
                  var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
                  var nodes = [], n = walker.nextNode();
                  while(n){ nodes.push(n); n = walker.nextNode(); }
                  var concat = '', mapping = [];
                  for(var i=0;i<nodes.length;i++){
                    var txt = nodes[i].nodeValue || '';
                    var start = concat.length;
                    concat += txt;
                    mapping.push({node: nodes[i], start: start, end: concat.length});
                  }
                  return {concat: concat, mapping: mapping};
                }

                var global = buildGlobalMapping(cont);
                var concat = global.concat;
                var mapping = global.mapping;
                var lastPos = 0;

                function findInGlobal(needle, startPos){
                  if(!needle) return null;
                  var idx = concat.indexOf(needle, startPos);
                  if(idx === -1) {
                    // normalized fallback
                    var normConcat = concat.replace(/\\s+/g,' ').trim();
                    var normNeedle = needle.replace(/\\s+/g,' ').trim();
                    var nidx = normConcat.indexOf(normNeedle);
                    if(nidx !== -1){
                      // try to map norm position to raw concat by searching first word
                      var first = normNeedle.split(' ')[0];
                      var r = new RegExp(first.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&'));
                      var m = r.exec(concat);
                      if(m) idx = m.index;
                    }
                  }
                  if(idx === -1) return null;
                  // find startNode/endNode
                  var startNode=null,endNode=null,startOffset=0,endOffset=0;
                  for(var i=0;i<mapping.length;i++){
                    if(mapping[i].start <= idx && mapping[i].end > idx){
                      startNode = mapping[i].node; startOffset = idx - mapping[i].start;
                    }
                    if(mapping[i].start < idx + needle.length && mapping[i].end >= idx + needle.length){
                      endNode = mapping[i].node; endOffset = idx + needle.length - mapping[i].start;
                      break;
                    }
                  }
                  if(!startNode || !endNode) return null;
                  return {startNode:startNode, startOffset:startOffset, endNode:endNode, endOffset:endOffset, index: idx};
                }

                function isDescendant(candidate, node){
                  try {
                    var p=node;
                    while(p){
                      if(p === candidate) return true;
                      p = p.parentNode;
                    }
                    return false;
                  } catch(e){ return false; }
                }

                function surroundRange(root, startNode, startOffset, endNode, endOffset, sid){
                  try {
                    var range = document.createRange();
                    range.setStart(startNode, startOffset);
                    range.setEnd(endNode, endOffset);
                    var span = document.createElement('span');
                    span.className = 'tts-sentence';
                    span.dataset.sid = sid;
                    try {
                      range.surroundContents(span);
                      return true;
                    } catch(e){
                      try {
                        var frag = range.extractContents();
                        span.appendChild(frag);
                        range.insertNode(span);
                        return true;
                      } catch(err){
                        return false;
                      }
                    }
                  } catch(e){
                    return false;
                  }
                }

                var applied = 0;

                data.forEach(function(entry){
                  try {
                    var sid = entry.sid;
                    var text = (entry.text||'').toString().trim();
                    var snippet = entry.snippet || null;
                    var ok = false;
                    if(!text) return;

                    // 1) Try a global ordered match starting at lastPos
                    var found = findInGlobal(text, lastPos);
                    if(found){
                      if(surroundRange(cont, found.startNode, found.startOffset, found.endNode, found.endOffset, sid)){
                        ok = true;
                        lastPos = found.index + text.length;
                        applied++;
                        // update global mapping since DOM changed: rebuild and continue
                        global = buildGlobalMapping(cont);
                        concat = global.concat; mapping = global.mapping;
                        return;
                      }
                    }

                    // 2) If global failed, try snippet-local candidates but ensure mapped node is descendant
                    if(snippet){
                      var els = Array.from(cont.querySelectorAll('*')).filter(function(el){
                        try { return el.outerHTML && el.outerHTML.indexOf(snippet) !== -1; } catch(e){ return false; }
                      }).sort(function(a,b){ return a.outerHTML.length - b.outerHTML.length; });

                      for(var ci=0; ci<els.length && !ok; ci++){
                        var cand = els[ci];
                        // try find text occurrence in global concat at or after lastPos whose node is descendant of cand
                        var searchIdx = concat.indexOf(text, lastPos);
                        while(searchIdx !== -1){
                          // map searchIdx -> node
                          var mapped = null;
                          for(var mi=0; mi<mapping.length; mi++){
                            if(mapping[mi].start <= searchIdx && mapping[mi].end > searchIdx){
                              // compute mapping for full length
                              var endPos = searchIdx + text.length;
                              // ensure endPos maps inside mapping array
                              for(var mj=mi; mj<mapping.length; mj++){
                                if(mapping[mj].end >= endPos){
                                  mapped = {
                                    startNode: mapping[mi].node,
                                    startOffset: searchIdx - mapping[mi].start,
                                    endNode: mapping[mj].node,
                                    endOffset: endPos - mapping[mj].start,
                                    index: searchIdx
                                  };
                                  break;
                                }
                              }
                              break;
                            }
                          }
                          if(mapped){
                            // ensure startNode or its ancestor is inside candidate element
                            if(isDescendant(cand, mapped.startNode) || isDescendant(cand, mapped.endNode)){
                              if(surroundRange(cont, mapped.startNode, mapped.startOffset, mapped.endNode, mapped.endOffset, sid)){
                                ok = true;
                                lastPos = mapped.index + text.length;
                                applied++;
                                // rebuild mapping after DOM change
                                global = buildGlobalMapping(cont);
                                concat = global.concat; mapping = global.mapping;
                                break;
                              }
                            }
                          }
                          // try next occurrence of text in concat
                          searchIdx = concat.indexOf(text, searchIdx+1);
                        }
                      }
                    }

                    if(!ok){
                      // 3) try multi-node sequence mapping inside cont with normalized fallback
                      var seq = findInGlobal(text, lastPos);
                      if(seq){
                        if(surroundRange(cont, seq.startNode, seq.startOffset, seq.endNode, seq.endOffset, sid)){
                          ok = true;
                          lastPos = seq.index + text.length;
                          applied++;
                          global = buildGlobalMapping(cont);
                          concat = global.concat; mapping = global.mapping;
                        }
                      }
                    }

                    if(!ok){
                      // 4) last-resort: replace first occurrence in innerHTML at/after lastPos by scanning innerHTML slice
                      var h = cont.innerHTML;
                      // search in HTML string but attempt to limit to area near lastPos by slicing
                      var startSearchHtml = Math.max(0, lastPos - 200);
                      var slice = h.slice(startSearchHtml);
                      var pos = slice.indexOf(text);
                      if(pos !== -1){
                        var realPos = startSearchHtml + pos;
                        cont.innerHTML = h.slice(0, realPos) + '<span class="tts-sentence" data-sid="' + sid + '">' + text + '</span>' + h.slice(realPos + text.length);
                        ok = true;
                        lastPos += text.length;
                        applied++;
                        global = buildGlobalMapping(cont);
                        concat = global.concat; mapping = global.mapping;
                      } else {
                        // relaxed ellipsis mapping
                        var alt = text.replace(/\\u2026/g,'...').replace(/â€¦/g,'...');
                        var aidx = h.indexOf(alt);
                        if(aidx !== -1){
                          cont.innerHTML = h.slice(0,aidx) + '<span class="tts-sentence" data-sid="' + sid + '">' + alt + '</span>' + h.slice(aidx+alt.length);
                          ok = true;
                          applied++;
                          global = buildGlobalMapping(cont);
                          concat = global.concat; mapping = global.mapping;
                        }
                      }
                    }

                  } catch(e){
                    console.log('[TTS-DBG] per-entry error', e);
                  }
                });

                console.log('[TTS-DBG] applied wraps=', applied);
              } catch(e){
                console.log('[TTS-DBG] wrapper fatal error', e);
              }
            })();
            """ % snippets_json

            try:
                try:
                    self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
                except Exception:
                    try:
                        self.webview.run_javascript(js, None, None, None)
                    except Exception:
                        pass
            except Exception as e:
                print("[TTS] html-snippet wrapper injection error:", e)
                traceback.print_exc()

            # Start TTS playback if auto_start is True
            if auto_start:
                # start TTS after short delay so wrappers apply in order
                GLib.timeout_add(250, lambda: (self.tts.speak_sentences_list(
                    sentences,
                    highlight_callback=self._on_tts_highlight,
                    finished_callback=self._on_tts_finished
                ), False)[1])

            return False
        except Exception as e:
            print("[TTS] _ensure_sentence_wrapping_and_start fatal:", e)
            return False


    def _on_tts_highlight(self, idx, meta):
        """Highlight the sentence being spoken and auto-scroll to keep it visible."""
        if not self.webview:
            return

        try:
            # prefer original sid from meta if present; fallback to idx
            sid = None
            try:
                if isinstance(meta, dict):
                    sid = meta.get("sid", None)
            except Exception:
                sid = None
            if sid is None:
                sid = idx

            # debug synth text
            synth_text = ""
            try:
                if isinstance(meta, dict):
                    synth_text = (meta.get("text") or "")[:1200]
                else:
                    synth_text = str(meta or "")[:1200]
            except Exception:
                synth_text = ""
            print(f"[TTS] Highlighting sid={sid!r}")

            # clear highlight
            if sid is None or (isinstance(sid, int) and sid < 0):
                js_clear = """(function(){
                  document.querySelectorAll('.tts-highlight').forEach(e => e.classList.remove('tts-highlight'));
                  console.log('[TTS] Cleared highlights');
                })();"""
                try:
                    self.webview.evaluate_javascript(js_clear, -1, None, None, None, None, None)
                except Exception:
                    try:
                        self.webview.run_javascript(js_clear, None, None, None)
                    except Exception:
                        pass
                return

            import json
            safe_text = synth_text or ""

            # JS: highlight and auto-scroll with column-aware logic
            js = f"""(function(){{
              try {{
                // Clear previous highlights
                document.querySelectorAll('.tts-highlight').forEach(e => e.classList.remove('tts-highlight'));
                
                var sid = "{sid}";
                var fr = Array.from(document.querySelectorAll('[data-sid=\"{sid}\"]'));
                
                if(fr && fr.length) {{
                  fr.forEach(function(e,i){{ 
                    e.classList.add('tts-highlight'); 
                  }});
                  
                  // Smart scroll to keep sentence visible
                  if(fr[0]) {{
                    var container = document.querySelector('.ebook-content');
                    if(!container) return;
                    
                    var element = fr[0];
                    var elementRect = element.getBoundingClientRect();
                    var containerRect = container.getBoundingClientRect();
                    
                    // Get actual column count
                    var style = getComputedStyle(container);
                    var actualColCount = parseFloat(style.columnCount) || 1;
                    if(style.columnCount === 'auto' || actualColCount === 0) {{
                      var colWidth = parseFloat(style.columnWidth);
                      var gap = parseFloat(style.columnGap) || 0;
                      if(colWidth > 0) {{
                        actualColCount = Math.max(1, Math.floor((container.clientWidth + gap) / (colWidth + gap)));
                      }}
                    }}
                    actualColCount = Math.max(1, Math.floor(actualColCount));
                    
                    if(actualColCount > 1) {{
                      // Multi-column: horizontal scrolling
                      var gap = parseFloat(style.columnGap) || 0;
                      var paddingLeft = parseFloat(style.paddingLeft) || 0;
                      var paddingRight = parseFloat(style.paddingRight) || 0;
                      var availableWidth = container.clientWidth - paddingLeft - paddingRight;
                      var totalGap = gap * (actualColCount - 1);
                      var columnWidth = (availableWidth - totalGap) / actualColCount;
                      var pageWidth = columnWidth + gap;
                      
                      // Calculate which column the element is in
                      var elementLeft = elementRect.left - containerRect.left + container.scrollLeft;
                      var elementColumn = Math.floor(elementLeft / pageWidth);
                      
                      // Check if element is visible in current viewport
                      var currentScrollLeft = container.scrollLeft;
                      var viewportRight = currentScrollLeft + container.clientWidth;
                      var elementRight = elementLeft + elementRect.width;
                      
                      var isVisible = (elementLeft >= currentScrollLeft - 50) && 
                                      (elementRight <= viewportRight + 50);
                      
                      if(!isVisible) {{
                        // Scroll to the column containing this element
                        var targetScroll = elementColumn * pageWidth;
                        var maxScroll = container.scrollWidth - container.clientWidth;
                        targetScroll = Math.max(0, Math.min(maxScroll, targetScroll));
                        
                        console.log('[TTS] Auto-scroll to column ' + elementColumn + ' (scroll: ' + targetScroll.toFixed(0) + 'px)');
                        
                        // Smooth scroll to column
                        var start = container.scrollLeft;
                        var distance = targetScroll - start;
                        var duration = 350;
                        var startTime = performance.now();
                        
                        function animateScroll(time) {{
                          var elapsed = time - startTime;
                          var t = Math.min(elapsed / duration, 1);
                          var ease = t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t+2, 3)/2;
                          container.scrollLeft = start + distance * ease;
                          if(t < 1) requestAnimationFrame(animateScroll);
                        }}
                        requestAnimationFrame(animateScroll);
                      }}
                    }} else {{
                      // Single column: scroll only when TTS highlights a partially cut-off sentence
                      var elementTop = elementRect.top - containerRect.top + container.scrollTop;
                      var elementBottom = elementTop + elementRect.height;
                      var viewportTop = container.scrollTop;
                      var viewportBottom = viewportTop + container.clientHeight;
                      
                      // Check if the CURRENT highlight (sentence being spoken) is FULLY visible
                      var isFullyVisible = (elementTop >= viewportTop + 5) && 
                                           (elementBottom <= viewportBottom - 5);
                      
                      if(!isFullyVisible) {{
                        // Current sentence is partially cut off - scroll to show it fully at top
                        var targetScroll = elementTop - 10; // Minimal padding (10px from absolute top)
                        targetScroll = Math.max(0, Math.min(container.scrollHeight - container.clientHeight, targetScroll));
                        
                        console.log('[TTS] Scroll to top (current sentence partial), Y:' + targetScroll.toFixed(0));
                        container.scrollTo({{
                          top: targetScroll,
                          behavior: 'smooth'
                        }});
                      }} else {{
                        console.log('[TTS] Current sentence fully visible, no scroll');
                      }}
                    }}
                  }}
                  return;
                }}

                // Fallback: data-sid not found, try text search
                var text = {json.dumps(safe_text)};
                if(!text) {{
                  console.log('[TTS] No text for fallback');
                  return;
                }}
                
                // Build text node mapping
                var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                var nodes = [], n = walker.nextNode();
                while(n){{ nodes.push(n); n = walker.nextNode(); }}
                var concat = '', mapping = [];
                for(var i=0;i<nodes.length;i++){{
                  var txt = nodes[i].nodeValue || '';
                  var start = concat.length;
                  concat += txt;
                  mapping.push({{node: nodes[i], start: start, end: concat.length}});
                }}

                var needle = text.replace(/\\s+/g,' ').trim();
                var idx = concat.toLowerCase().indexOf(needle.toLowerCase());
                
                if(idx === -1) {{
                  console.log('[TTS] Text not found in fallback');
                  return;
                }}

                // Find nodes
                var startNode=null, endNode=null, startOffset=0, endOffset=0;
                var endPos = idx + needle.length;
                for(var i=0;i<mapping.length;i++){{
                  if(mapping[i].start <= idx && mapping[i].end > idx){{
                    startNode = mapping[i].node;
                    startOffset = idx - mapping[i].start;
                  }}
                  if(mapping[i].start < endPos && mapping[i].end >= endPos){{
                    endNode = mapping[i].node;
                    endOffset = endPos - mapping[i].start;
                    break;
                  }}
                }}

                if(startNode && endNode) {{
                  var range = document.createRange();
                  range.setStart(startNode, Math.max(0,startOffset));
                  range.setEnd(endNode, Math.max(0,endOffset));
                  var span = document.createElement('span');
                  span.className = 'tts-sentence tts-highlight';
                  span.dataset.sid = sid;
                  try {{
                    range.surroundContents(span);
                  }} catch(e) {{
                    var frag = range.extractContents();
                    span.appendChild(frag);
                    range.insertNode(span);
                  }}
                  
                  // Auto-scroll the fallback highlight (same logic as above)
                  span.scrollIntoView({{behavior:'smooth', block:'center'}});
                  console.log('[TTS] Fallback wrapped and scrolled');
                }}
              }} catch(err) {{ 
                console.log('[TTS] Highlight error', err); 
              }}
            }})();"""

            try:
                self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
            except Exception:
                try:
                    self.webview.run_javascript(js, None, None, None)
                except Exception:
                    pass

        except Exception as e:
            print(f"[TTS] Highlight callback error: {e}")

    def _on_tts_finished(self):
        """Clear highlighting when TTS finishes."""
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


    # ---- new window methods invoked by app actions ----
    def _update_column_css_via_js(self):
        """Update column CSS rules via JavaScript without reloading the page.
        
        Dynamically switches between column layout and single-column vertical scroll
        based on available viewport width.
        """
        if not getattr(self, "webview", None):
            return
        
        gap_val = getattr(self, "_column_gap", 50)
        mt = int(getattr(self, "page_margin_top", 50))
        mr = int(getattr(self, "page_margin_right", 50))
        mb = int(getattr(self, "page_margin_bottom", 50))
        ml = int(getattr(self, "page_margin_left", 50))
        
        # JavaScript to dynamically apply correct layout
        js = f"""
        (function(){{
            try {{
                console.log('🎨 Updating column CSS via JS');
                const container = document.querySelector('.ebook-content');
                if (!container) {{
                    console.log('⚠️ Container not found');
                    return;
                }}
                
                // Helper function to get container metrics
                function getMetrics() {{
                    const style = getComputedStyle(container);
                    const paddingLeft = parseFloat(style.paddingLeft) || 0;
                    const paddingRight = parseFloat(style.paddingRight) || 0;
                    const gap = parseFloat(style.columnGap) || 0;
                    const clientWidth = container.clientWidth;
                    const availableWidth = clientWidth - paddingLeft - paddingRight;
                    const colWidth = parseFloat(style.columnWidth) || window.currentColumnWidth || 300;
                    let actualColCount = Math.max(1, Math.floor((availableWidth + gap) / (colWidth + gap)));
                    const totalGap = gap * (actualColCount - 1);
                    const columnWidth = (availableWidth - totalGap) / actualColCount;
                    
                    return {{
                        container: container,
                        clientWidth: clientWidth,
                        availableWidth: availableWidth,
                        paddingLeft: paddingLeft,
                        paddingRight: paddingRight,
                        gap: gap,
                        colCount: actualColCount,
                        columnWidth: columnWidth,
                        pageWidth: columnWidth + gap
                    }};
                }}
                
                // Helper function to get current column
                function getCurrentCol() {{
                    const metrics = getMetrics();
                    if (!metrics || metrics.colCount <= 1) return 0;
                    const scrollLeft = metrics.container.scrollLeft;
                    return Math.round(scrollLeft / metrics.pageWidth);
                }}
                
                // Helper function to scroll to column
                function scrollToCol(index) {{
                    const metrics = getMetrics();
                    if (!metrics) return;
                    const targetScroll = index * metrics.pageWidth;
                    const maxScroll = metrics.container.scrollWidth - metrics.clientWidth;
                    const clampedScroll = Math.max(0, Math.min(maxScroll, targetScroll));
                    metrics.container.scrollLeft = clampedScroll;
                    console.log('→ Column ' + index + ' (scroll: ' + clampedScroll.toFixed(0) + 'px, maxScroll: ' + maxScroll.toFixed(0) + 'px)');
                }}
                
                // Store current position before layout change
                const wasMultiColumn = !window.isSingleColumnMode;
                const currentCol = wasMultiColumn ? getCurrentCol() : 0;
                
                // UPDATE GLOBAL SETTINGS
                window.currentColumnWidth = {self.column_width_px};
                window.currentGap = {gap_val};
                window.currentPadding = {{
                    top: {mt},
                    right: {mr},
                    bottom: {mb},
                    left: {ml}
                }};
                
                console.log('✓ Updated globals: colWidth=' + window.currentColumnWidth + 'px, gap=' + window.currentGap + 'px');
                
                // Calculate if we can fit multiple columns
                const clientWidth = container.clientWidth || window.innerWidth;
                const availableWidth = clientWidth - {ml} - {mr};
                const colWidth = {self.column_width_px};
                const gap = {gap_val};
                
                // Calculate how many columns would fit
                const wouldFitCols = Math.max(1, Math.floor((availableWidth + gap) / (colWidth + gap)));
                
                console.log('📐 Layout calc: availableW=' + availableWidth + 'px, colW=' + colWidth + 'px, would fit ' + wouldFitCols + ' cols');
                
                if (wouldFitCols === 1) {{
                    // SINGLE COLUMN MODE: Disable columns, use vertical scroll
                    console.log('📖 Switching to single-column vertical scroll mode');
                    container.style.cssText = `
                        column-width: unset;
                        -webkit-column-width: unset;
                        column-count: unset;
                        -webkit-column-count: unset;
                        column-gap: unset;
                        -webkit-column-gap: unset;
                        column-fill: unset;
                        -webkit-column-fill: unset;
                        padding: {mt}px {mr}px {mb}px {ml}px;
                        width: 100%;
                        height: 100vh;
                        overflow-x: hidden;
                        overflow-y: auto;
                        box-sizing: border-box;
                        position: relative;
                    `;
                    window.isSingleColumnMode = true;
                    
                    // Hide the extra column spacer in single-column mode
                    const spacer = document.querySelector('.extra-column-spacer');
                    if (spacer) {{
                        spacer.style.display = 'none';
                        console.log('🚫 Hidden extra column spacer (single-column mode)');
                    }}
                }} else {{
                    // MULTI-COLUMN MODE: Use column layout with horizontal scroll
                    console.log('📰 Switching to multi-column mode (' + wouldFitCols + ' cols)');
                    container.style.cssText = `
                        column-width: {self.column_width_px}px;
                        -webkit-column-width: {self.column_width_px}px;
                        column-gap: {gap_val}px;
                        -webkit-column-gap: {gap_val}px;
                        column-fill: auto;
                        -webkit-column-fill: auto;
                        padding: {mt}px {mr}px {mb}px {ml}px;
                        width: 100vw;
                        height: 100vh;
                        overflow-x: auto;
                        overflow-y: hidden;
                        box-sizing: border-box;
                        position: relative;
                    `;
                    window.isSingleColumnMode = false;
                    
                    // Show the extra column spacer in multi-column mode
                    const spacer = document.querySelector('.extra-column-spacer');
                    if (spacer) {{
                        spacer.style.display = 'block';
                        console.log('✅ Shown extra column spacer (multi-column mode)');
                    }}
                    
                    // Snap to column position after layout change
                    setTimeout(() => {{
                        const metrics = getMetrics();
                        if (metrics && metrics.colCount > 1) {{
                            const targetCol = Math.min(currentCol, metrics.colCount - 1);
                            console.log('🔄 Snapping to column ' + targetCol + ' after CSS update');
                            scrollToCol(targetCol);
                        }}
                    }}, 50);
                }}
                
                console.log('✓ Column CSS updated successfully');
            }} catch(e) {{
                console.log('❌ Error updating column CSS:', e);
            }}
        }})();"""
        
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
        except Exception:
            try:
                self.webview.run_javascript(js, None, None, None)
            except Exception:
                pass


    def set_column_width(self, w):
        """Set column width in pixels (from menu action).
        
        Updates the column width and applies changes via JavaScript, maintaining
        the user's current reading position.
        """
        try:
            w = int(w)
        except Exception:
            return
        
        self.column_width_px = max(50, min(1000, w))
        print(f"✓ Set column width to {self.column_width_px}px")
        
        # Update CSS via JavaScript instead of reloading page
        try:
            if self.book and self.items:
                self._update_column_css_via_js()
                print(f"✓ Updated column CSS (staying at current position)")
        except Exception as e:
            print(f"✗ Column CSS update failed: {e}, falling back to display_page")
            # Fallback to full reload only if JS update fails
            try:
                self.display_page()
            except Exception:
                pass

    # ---- column control handlers (kept for compatibility but widgets replaced) ----
    def _on_column_control_changed(self):
        try:
            # kept in case code elsewhere calls this, but our UI now uses app actions
            try:
                self.column_count = max(1, min(10, int(getattr(self, "column_count", 1))))
            except Exception:
                self.column_count = 1
            # column_mode_use_width and column_width_px are controlled by app actions now
            try:
                if getattr(self, "book") and getattr(self, "items") and self.items:
                    self.display_page()
            except Exception:
                pass
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

    # ---- Library UI ----
    def on_library_clicked(self, *_):
        try:
            if getattr(self, "book", None):
                try:
                    self.content_sidebar_toggle.set_visible(False)
                    self.split.set_show_sidebar(False)
                    self.split.set_collapsed(False)
                except Exception:
                    pass
            self.show_library()
        except Exception:
            pass

    def _stop_reading(self, path=None):
        try:
            if path and getattr(self, "book_path", None) and os.path.abspath(path) != os.path.abspath(self.book_path):
                return
            try: self._save_progress_for_library()
            except Exception: pass
            try: self.cleanup()
            except Exception: pass
            try:
                self.book_path = None
                self.open_btn.set_visible(True)
                self.search_toggle_btn.set_visible(True)
                try: self.content_sidebar_toggle.set_visible(False)
                except Exception: pass
            except Exception:
                pass
            try: self.show_library()
            except Exception: pass
        except Exception:
            pass

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


    # ---- UI helpers ----
    def _setup_window_size_constraints(self):
        self._is_snapping = False
        self._snap_timeout_id = None
        self.connect("notify::default-width", self._on_window_width_changed)

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
    
    def _setup_responsive_sidebar(self):
        self._responsive_enabled = False
        self._last_width = 0
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self.connect("notify::default-width", self._on_window_size_changed)

    def _on_sidebar_toggle(self, btn):
        """Toggle sidebar visibility and snap to current column."""
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            
            if not new:
                self._user_hid_sidebar = True
            else:
                self._user_hid_sidebar = False
            
            # Trigger column snapping after sidebar animation
            if self.book and self.items and self.webview:
                GLib.timeout_add(450, self._snap_to_current_column)
            
        except Exception as e:
            print(f"Sidebar toggle error: {e}")

    def _snap_to_current_column(self):
        """Snap to current column position (called after resize/sidebar toggle)."""
        if not getattr(self, "webview", None):
            return False
        
        js = """
        (function(){
            try {
                if (!window.isSingleColumnMode) {
                    const metrics = getContainerMetrics();
                    if (metrics && metrics.colCount > 1) {
                        const currentCol = getCurrentColumnIndex();
                        console.log('🔄 Snapping to column ' + currentCol);
                        scrollToColumnIndex(currentCol, false);
                    }
                }
            } catch(e) {
                console.log('❌ Snap error:', e);
            }
        })();
        """
        
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
        except Exception:
            try:
                self.webview.run_javascript(js, None, None, None)
            except Exception:
                pass
        
        return False
    def _on_window_size_changed(self, *args):
        """Handle responsive layout changes.
        
        The browser automatically fires a resize event when the layout changes,
        and the JavaScript resize handler maintains the current column position.
        """
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
                
                # Browser resize event will automatically fire and maintain column position
                print(f"🔄 Responsive change - letting resize handler maintain position")
            else:
                if self._last_was_narrow is not None:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(False)
                    
        except Exception as e:
            print(f"Error in window size handler: {e}")
            
    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        self._on_window_size_changed()

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._last_was_narrow = None
        self._user_hid_sidebar = False
        try:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)
        except Exception as e:
            print(f"Error disabling responsive sidebar: {e}")

    def setup_column_gap_spinner(self, extra_row):
        """Setup column gap spinner with JS-based updates."""
        gap_adj = Gtk.Adjustment(
            value=getattr(self, "_column_gap", 50),
            lower=0,
            upper=200,
            step_increment=1,
            page_increment=10
        )
        
        self.col_gap_spin = Gtk.SpinButton.new(gap_adj, climb_rate=1.0, digits=0)
        self.col_gap_spin.set_tooltip_text("Column gap (px)")
        
        def on_gap_changed(spin):
            try:
                self._column_gap = int(spin.get_value())
                # Use JS update instead of full page reload
                if self.book and self.items:
                    self._update_column_css_via_js()
                    print(f"✓ Updated column gap to {self._column_gap}px (staying at current position)")
            except Exception as e:
                print(f"Error updating column gap: {e}")
        
        self.col_gap_spin.connect("value-changed", lambda s: on_gap_changed(s))
        extra_row.append(self.col_gap_spin)

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
    def _on_webconsole_message(self, webview, message):
        """Debug: Print JavaScript console messages to terminal"""
        try:
            # Try different WebKit API versions
            try:
                # WebKit 6.0 API
                msg = message.get_message()
            except:
                try:
                    # Older API
                    msg = message.props.message if hasattr(message, 'props') else str(message)
                except:
                    msg = str(message)
            print(f"[WebView Console] {msg}")
        except Exception as e:
            print(f"[WebView Console Error] {e}")
            
                
    def _wrap_html(self, raw_html, base_uri):
        """
        Wrap EPUB HTML and inject user overrides: font, size, line-height, justification,
        column gap and page margins so they persist across rebuilds.
        """
        print(f"📄 _wrap_html: column_count={getattr(self, 'column_count', '?')}, use_width={getattr(self, 'column_mode_use_width', '?')}")
        
        try:
            page_css_base = (self.css_content or "") + "\n" + THEME_INJECTION_CSS

            # Always use column-width mode
            col_decl = "column-width: {}px; -webkit-column-width: {}px;".format(self.column_width_px, self.column_width_px)

            gap_val = getattr(self, "_column_gap", 50)
            gap_decl = "column-gap: {}px; -webkit-column-gap: {}px;".format(gap_val, gap_val)
            fill_decl = "column-fill: auto; -webkit-column-fill: auto;"

            # page margins
            mt = int(getattr(self, "page_margin_top", 50))
            mr = int(getattr(self, "page_margin_right", 50))
            mb = int(getattr(self, "page_margin_bottom", 50))
            ml = int(getattr(self, "page_margin_left", 50))
            padding_decl = f"padding: {mt}px {mr}px {mb}px {ml}px;"

            # Use instance theme colors (not globals)
            page_bg = getattr(self, 'page_bg', '#ffffff')
            text_fg = getattr(self, 'text_fg', '#000000')

            # Always enable both scroll directions - JavaScript will manage based on actual column count
            # light sepia like color background: #e5e0dd;
            # Old magazine #fbfcee
            # newspaper #e1e1e1
            col_rules = f"""
                html {{
                    height: 100vh;
                    overflow: hidden;
                    background: {page_bg};     
                    color: {text_fg};
                }}
                body {{
                    margin: 0;
                    padding: 0;
                    height: 100vh;
                    overflow: hidden;
                    /* Default fallback font-family if epub has no font specified */
                    font-family: sans-serif;
                }}
                .ebook-content {{
                    {col_decl}
                    {gap_decl}
                    {fill_decl}
                    {padding_decl}
                    
                    /* Container dimensions */
                    width: 100vw;
                    height: 100vh;
                    
                    /* Reset nested columns */
                    * {{
                        -webkit-column-count: unset !important;
                        column-count: unset !important;
                    }}
                    
                    /* Enable both scroll directions - JS will manage based on column count */
                    overflow-x: auto;
                    overflow-y: auto;
                    
                    /* Important for columns to work */
                    box-sizing: border-box;
                    position: relative;
                }}
                .ebook-content img, .ebook-content svg {{
                    max-width: 100%;
                    height: auto;
                    break-inside: avoid;
                }}
                .ebook-content p, .ebook-content div {{
                    break-inside: auto;
                }}
                /* Extra column spacer at the end */
                .extra-column-spacer {{
                    display: block;
                    height: 100%;
                    min-height: 100vh;
                    /* Make it as wide as a column to ensure it creates a full column */
                    width: 100%;
                    max-width: 100%;
                    /* Force this element to start in a new column */
                    break-before: column;
                    -webkit-column-break-before: auto;
                    visibility: hidden;
                }}
            """

            # user overrides (font/size/line-height/justify)
            extra = ""
            fam = getattr(self, "user_font_family", None)
            sz  = getattr(self, "user_font_size", None)
            
            # Parse font size
            if sz:
                try:
                    if isinstance(sz, (int, float)):
                        base_size = float(sz)
                    else:
                        s = str(sz).replace("pt", "").strip()
                        base_size = float(s)
                except Exception:
                    base_size = 15.0  # Default
            else:
                base_size = None

            lineh = getattr(self, "user_line_height", None)
            justify = getattr(self, "user_justify", None)

            # Apply font family
            if fam:
                safe_fam = str(fam).replace("'", "\\'")
                extra += f"body, .ebook-content {{ font-family: '{safe_fam}' !important; }}\n"
                extra += f".ebook-content * {{ font-family: '{safe_fam}' !important; }}\n"
            
            # Apply font size with standard heading scale
            if base_size:
                # Base size for body text
                extra += f"html, body, .ebook-content {{ font-size: {base_size}pt !important; }}\n"
                
                # Standard heading sizes (proper typographic scale)
                extra += f".ebook-content h1 {{ font-size: {base_size * 2.0}pt !important; }}\n"
                extra += f".ebook-content h2 {{ font-size: {base_size * 1.7}pt !important; }}\n"
                extra += f".ebook-content h3 {{ font-size: {base_size * 1.4}pt !important; }}\n"
                extra += f".ebook-content h4 {{ font-size: {base_size * 1.2}pt !important; }}\n"
                extra += f".ebook-content h5 {{ font-size: {base_size * 1.1}pt !important; }}\n"
                extra += f".ebook-content h6 {{ font-size: {base_size}pt !important; }}\n"
                
                # Body text elements
                extra += f".ebook-content p, .ebook-content div, .ebook-content span {{ font-size: {base_size}pt !important; }}\n"
                extra += f".ebook-content li {{ font-size: {base_size}pt !important; }}\n"
                extra += f".ebook-content td, .ebook-content th {{ font-size: {base_size}pt !important; }}\n"
                
                # Special elements
                extra += f".ebook-content blockquote {{ font-size: {base_size * 0.95}pt !important; }}\n"
                extra += f".ebook-content code, .ebook-content pre {{ font-size: {base_size * 0.9}pt !important; }}\n"
                extra += f".ebook-content small {{ font-size: {base_size * 0.85}pt !important; }}\n"
                extra += f".ebook-content sup, .ebook-content sub {{ font-size: {base_size * 0.75}pt !important; }}\n"
            
            # Apply line height
            if lineh:
                try:
                    lh = float(lineh)
                    extra += f".ebook-content {{ line-height: {lh:.2f} !important; }}\n"
                except Exception:
                    pass
            
            # Apply justification
            if justify:
                if justify == "none":
                    extra += ".ebook-content { text-align: left !important; -webkit-hyphens: none !important; hyphens: none !important; }\n"
                elif justify == "full":
                    extra += ".ebook-content { text-align: justify !important; -webkit-hyphens: none !important; hyphens: none !important; }\n"
                elif justify == "hyphen":
                    extra += ".ebook-content { text-align: justify !important; -webkit-hyphens: auto !important; hyphens: auto !important; }\n"

            page_css = page_css_base + "\n" + col_rules + "\n" + extra

        except Exception as e:
            print(f"CSS generation error: {e}")
            page_css = (self.css_content or "") + "\n" + THEME_INJECTION_CSS

        # Pass column width to JavaScript for dynamic detection
        print(f"📊 Injecting JS with column width: {self.column_width_px}px")
        
        # ENHANCED COLUMN NAVIGATION JAVASCRIPT WITH DYNAMIC COLUMN DETECTION
        js_detect_columns = f"""<script>
            (function() {{
                const originalLog = console.log;
                console.log = function(...args) {{
                    const msg = args.map(a => String(a)).join(' ');
                    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.consoleLog) {{
                        window.webkit.messageHandlers.consoleLog.postMessage(msg);
                    }}
                    originalLog.apply(console, args);
                }};
                
                console.log('=== COLUMN SCRIPT LOADED ===');
                console.log('Column width: {self.column_width_px}px');
                
                window.currentColumnWidth = {self.column_width_px};
                window.currentGap = {getattr(self, "_column_gap", 50)};
                window.currentPadding = {{
                    top: {getattr(self, "page_margin_top", 50)},
                    right: {getattr(self, "page_margin_right", 50)},
                    bottom: {getattr(self, "page_margin_bottom", 50)},
                    left: {getattr(self, "page_margin_left", 50)}
                }};
                
                window.getContainerMetrics = function() {{
                    const container = document.querySelector('.ebook-content');
                    if (!container) return null;
                    
                    const style = getComputedStyle(container);
                    const paddingLeft = parseFloat(style.paddingLeft) || 0;
                    const paddingRight = parseFloat(style.paddingRight) || 0;
                    const gap = parseFloat(style.columnGap) || 0;
                    
                    const clientWidth = container.clientWidth;
                    const clientHeight = container.clientHeight;
                    const scrollWidth = container.scrollWidth;
                    const availableWidth = clientWidth - paddingLeft - paddingRight;
                    
                    // Calculate actual column count based on column-width
                    const colWidth = parseFloat(style.columnWidth) || window.currentColumnWidth || 300;
                    let viewportColCount = Math.max(1, Math.floor((availableWidth + gap) / (colWidth + gap)));
                    
                    const totalGap = gap * (viewportColCount - 1);
                    const columnWidth = (availableWidth - totalGap) / viewportColCount;
                    const pageWidth = columnWidth + gap;
                    
                    // Calculate TOTAL columns in the entire document
                    const totalColumns = Math.max(1, Math.round(scrollWidth / pageWidth));
                    
                    // Maximum column index we can navigate to
                    // In multi-column mode, exclude the last column (spacer) from navigation
                    const spacer = document.querySelector('.extra-column-spacer');
                    const hasSpacerColumn = spacer && window.getComputedStyle(spacer).display !== 'none';
                    const maxCol = hasSpacerColumn ? Math.max(0, totalColumns - 3) : totalColumns - 3;
                    
                    if (hasSpacerColumn) {{
                        console.log('📊 Column metrics: total=' + totalColumns + ', maxCol=' + maxCol + ' (excluded spacer column)');
                    }}
                    
                    
                    return {{
                        container: container,
                        clientWidth: clientWidth,
                        availableWidth: availableWidth,
                        paddingLeft: paddingLeft,
                        paddingRight: paddingRight,
                        gap: gap,
                        colCount: viewportColCount,  // Columns visible at once
                        totalCols: totalColumns,      // Total columns in document
                        maxCol: maxCol,               // Max column index (0-based)
                        columnWidth: columnWidth,
                        pageWidth: pageWidth
                    }};
                }};
                
                window.getCurrentColumnIndex = function() {{
                    const metrics = getContainerMetrics();
                    if (!metrics || metrics.colCount <= 1) return 0;
                    
                    const scrollLeft = metrics.container.scrollLeft;
                    const columnIndex = Math.round(scrollLeft / metrics.pageWidth);
                    return Math.min(columnIndex, metrics.maxCol);
                }};
                
                window.scrollToColumnIndex = function(index, smooth = true) {{
                    const metrics = getContainerMetrics();
                    if (!metrics) return;
                    
                    // Clamp to valid range
                    const targetCol = Math.max(0, Math.min(index, metrics.maxCol));
                    const targetScroll = targetCol * metrics.pageWidth;
                    
                    // Also ensure we don't scroll past actual content (excluding spacer)
                    const maxScroll = metrics.container.scrollWidth - metrics.clientWidth;
                    const clampedScroll = Math.min(targetScroll, maxScroll);
                    
                    if (smooth) {{
                        smoothScrollTo(clampedScroll, metrics.container.scrollTop);
                    }} else {{
                        metrics.container.scrollLeft = clampedScroll;
                    }}
                    
                    console.log('→ Column ' + targetCol + ' (scroll: ' + clampedScroll.toFixed(0) + 'px, max: ' + metrics.maxCol + ')');
                }};
                
                function smoothScrollTo(xTarget, yTarget) {{
                    const container = document.querySelector('.ebook-content');
                    if (!container) return;
                    
                    const startX = container.scrollLeft;
                    const startY = container.scrollTop;
                    const distX = xTarget - startX;
                    const distY = yTarget - startY;
                    
                    // Skip animation if distance is tiny
                    if (Math.abs(distX) < 1 && Math.abs(distY) < 1) {{
                        container.scrollLeft = xTarget;
                        container.scrollTop = yTarget;
                        return;
                    }}
                    
                    const duration = 350;
                    const start = performance.now();
                    
                    function step(time) {{
                        const t = Math.min((time - start) / duration, 1);
                        const ease = t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t+2, 3)/2;
                        container.scrollLeft = startX + distX * ease;
                        container.scrollTop = startY + distY * ease;
                        if (t < 1) requestAnimationFrame(step);
                    }}
                    requestAnimationFrame(step);
                }}
                
                function snapScroll() {{
                    const metrics = getContainerMetrics();
                    if (!metrics || metrics.colCount <= 1) return;
                    
                    const currentScroll = metrics.container.scrollLeft;
                    const columnIndex = Math.round(currentScroll / metrics.pageWidth);
                    const clampedIndex = Math.max(0, Math.min(columnIndex, metrics.maxCol));
                    const targetScroll = clampedIndex * metrics.pageWidth;
                    
                    // Also ensure we don't snap past actual content
                    const maxScroll = metrics.container.scrollWidth - metrics.clientWidth;
                    const finalScroll = Math.min(targetScroll, maxScroll);
                    
                    if (Math.abs(finalScroll - currentScroll) > 2) {{
                        console.log('↹ Snap to col ' + clampedIndex + ' (' + currentScroll.toFixed(0) + '→' + finalScroll.toFixed(0) + ')');
                        metrics.container.scrollLeft = finalScroll;
                    }}
                }}
                
                // Scroll event listener
                const container = document.querySelector('.ebook-content');
                if (container) {{
                    let scrollTimer;
                    container.addEventListener('scroll', function() {{
                        clearTimeout(scrollTimer);
                        scrollTimer = setTimeout(() => {{
                            const metrics = getContainerMetrics();
                            if (metrics && metrics.colCount > 1) snapScroll();
                        }}, 150);
                    }});
                }}
                
                // Mouse wheel navigation
                window.addEventListener('wheel', function(e) {{
                    // In single-column mode, allow natural scrolling
                    if (window.isSingleColumnMode) return;
                    
                    const metrics = getContainerMetrics();
                    if (!metrics) return;
                    
                    // Multi-column mode: navigate by columns
                    e.preventDefault();
                    
                    const currentCol = getCurrentColumnIndex();
                    const direction = e.deltaY > 0 ? 1 : -1;
                    const targetCol = Math.max(0, Math.min(metrics.maxCol, currentCol + direction));
                    
                    if (targetCol !== currentCol) {{
                        console.log('🖱️ ' + (direction>0?'→':'←') + ' col ' + currentCol + '→' + targetCol + ' (of ' + metrics.maxCol + ')');
                        scrollToColumnIndex(targetCol, true);
                    }}
                }}, {{passive: false, capture: true}});
                
                // Keyboard navigation
                document.addEventListener('keydown', function(e) {{
                    if (e.ctrlKey || e.metaKey || e.altKey) return;
                    
                    const container = document.querySelector('.ebook-content');
                    if (!container) return;
                    
                    const metrics = getContainerMetrics();
                    if (!metrics) return;
                    
                    if (window.isSingleColumnMode) {{
                        // Single column: Vertical scrolling only (standard browser behavior)
                        const viewH = container.clientHeight;
                        const maxY = container.scrollHeight - viewH;
                        let y = container.scrollTop;
                        let scroll = false;
                        
                        switch(e.key) {{
                            case 'ArrowUp': 
                                e.preventDefault(); 
                                y = Math.max(0, y - 40); 
                                scroll = true; 
                                break;
                            case 'ArrowDown': 
                                e.preventDefault(); 
                                y = Math.min(maxY, y + 40); 
                                scroll = true; 
                                break;
                            case 'PageUp': 
                                e.preventDefault(); 
                                y = Math.max(0, y - viewH * 0.9); 
                                scroll = true; 
                                break;
                            case 'PageDown': 
                            case ' ': 
                                e.preventDefault(); 
                                y = Math.min(maxY, y + viewH * 0.9); 
                                scroll = true; 
                                break;
                            case 'Home': 
                                e.preventDefault(); 
                                y = 0; 
                                scroll = true; 
                                break;
                            case 'End': 
                                e.preventDefault(); 
                                y = maxY; 
                                scroll = true; 
                                break;
                        }}
                        
                        if (scroll) {{
                            console.log('⬆️⬇️ ' + e.key + ' (1-col mode)');
                            smoothScrollTo(0, y);
                        }}
                    }} else {{
                        // Multi-column: horizontal navigation
                        const currentCol = getCurrentColumnIndex();
                        const maxCol = metrics.maxCol;
                        let targetCol = currentCol;
                        let scroll = false;
                        
                        switch(e.key) {{
                            case 'ArrowLeft': 
                                e.preventDefault(); 
                                targetCol = Math.max(0, currentCol - 1); 
                                scroll = true; 
                                break;
                            case 'ArrowRight': 
                                e.preventDefault(); 
                                targetCol = Math.min(maxCol, currentCol + 1); 
                                scroll = true; 
                                break;
                            case 'PageUp': 
                                e.preventDefault(); 
                                targetCol = Math.max(0, currentCol - metrics.colCount); 
                                scroll = true; 
                                break;
                            case 'PageDown': 
                                e.preventDefault(); 
                                targetCol = Math.min(maxCol, currentCol + metrics.colCount); 
                                scroll = true; 
                                break;
                            case 'Home': 
                                e.preventDefault(); 
                                targetCol = 0; 
                                scroll = true; 
                                break;
                            case 'End': 
                                e.preventDefault(); 
                                targetCol = maxCol; 
                                scroll = true; 
                                break;
                        }}
                        
                        if (scroll) {{
                            console.log('⬅️➡️ ' + e.key + ' col ' + currentCol + '→' + targetCol + ' (of ' + maxCol + ')');
                            scrollToColumnIndex(targetCol, true);
                        }}
                    }}
                }}, {{passive: false, capture: true}});
                
                // Window resize handler - maintain column position
                let resizeTimer;
                window.addEventListener('resize', function() {{
                    clearTimeout(resizeTimer);
                    resizeTimer = setTimeout(function() {{
                        checkAndApplyLayout();
                    }}, 400);  // Wait for sidebar animation
                }});
                
                // Function to check layout and apply single-column or multi-column mode
                function checkAndApplyLayout() {{
                    const container = document.querySelector('.ebook-content');
                    if (!container) return;
                    
                    // Store current position BEFORE any changes
                    const wasMultiColumn = !window.isSingleColumnMode;
                    const currentCol = wasMultiColumn ? getCurrentColumnIndex() : 0;
                    
                    // USE CURRENT DYNAMIC VALUES - This is the key fix!
                    const colWidth = window.currentColumnWidth || 300;
                    const gap = window.currentGap || 32;
                    const padding = window.currentPadding || {{
                        top: 12,
                        right: 12,
                        bottom: 12,
                        left: 12
                    }};
                    
                    const style = getComputedStyle(container);
                    const paddingLeft = parseFloat(style.paddingLeft) || padding.left;
                    const paddingRight = parseFloat(style.paddingRight) || padding.right;
                    const clientWidth = container.clientWidth;
                    const availableWidth = clientWidth - paddingLeft - paddingRight;
                    
                    // Calculate how many columns would fit
                    const wouldFitCols = Math.max(1, Math.floor((availableWidth + gap) / (colWidth + gap)));
                    
                    console.log('🔍 Layout check: ' + wouldFitCols + ' cols would fit (availW=' + availableWidth + 'px, colW=' + colWidth + 'px)');
                    
                    if (wouldFitCols === 1 && !window.isSingleColumnMode) {{
                        // Switch to single-column mode
                        console.log('📖 Switching to single-column vertical scroll mode');
                        container.style.cssText = `
                            column-width: unset;
                            -webkit-column-width: unset;
                            column-count: unset;
                            -webkit-column-count: unset;
                            column-gap: unset;
                            -webkit-column-gap: unset;
                            column-fill: unset;
                            -webkit-column-fill: unset;
                            padding: ${{padding.top}}px ${{padding.right}}px ${{padding.bottom}}px ${{padding.left}}px;
                            width: 100%;
                            height: 100vh;
                            overflow-x: hidden;
                            overflow-y: auto;
                            box-sizing: border-box;
                            position: relative;
                        `;
                        window.isSingleColumnMode = true;
                        
                        // Hide the extra column spacer in single-column mode
                        const spacer = document.querySelector('.extra-column-spacer');
                        if (spacer) {{
                            spacer.style.display = 'none';
                            console.log('🚫 Hidden extra column spacer (single-column mode)');
                        }}
                    }} else if (wouldFitCols > 1) {{
                        const needsLayoutUpdate = window.isSingleColumnMode !== false;
                        
                        if (needsLayoutUpdate) {{
                            // Switch to multi-column mode
                            console.log('📰 Switching to multi-column mode (' + wouldFitCols + ' cols)');
                            container.style.cssText = `
                                column-width: ${{colWidth}}px;
                                -webkit-column-width: ${{colWidth}}px;
                                column-gap: ${{gap}}px;
                                -webkit-column-gap: ${{gap}}px;
                                column-fill: auto;
                                -webkit-column-fill: auto;
                                padding: ${{padding.top}}px ${{padding.right}}px ${{padding.bottom}}px ${{padding.left}}px;
                                width: 100vw;
                                height: 100vh;
                                overflow-x: auto;
                                overflow-y: hidden;
                                box-sizing: border-box;
                                position: relative;
                            `;
                            window.isSingleColumnMode = false;
                            
                            // Show the extra column spacer in multi-column mode
                            const spacer = document.querySelector('.extra-column-spacer');
                            if (spacer) {{
                                spacer.style.display = 'block';
                                console.log('✅ Shown extra column spacer (multi-column mode)');
                            }}
                        }}
                        
                        // Snap to column - whether switching or just resizing
                        setTimeout(() => {{
                            const metrics = getContainerMetrics();
                            if (metrics && metrics.colCount > 1) {{
                                const targetCol = Math.min(currentCol, metrics.maxCol);
                                console.log('🔄 Resize - snapping to column ' + targetCol + ' (max: ' + metrics.maxCol + ')');
                                scrollToColumnIndex(targetCol, false);
                            }}
                        }}, 50);
                    }}
                }}
                
                // Initial layout check on page load
                setTimeout(() => {{
                    checkAndApplyLayout();
                }}, 100);
                
                // Initial metrics logging
                setTimeout(() => {{
                    const m = getContainerMetrics();
                    if (m) {{
                        console.log('📏 Metrics:');
                        console.log('  Column width: ' + window.currentColumnWidth + 'px');
                        console.log('  Viewport cols: ' + m.colCount);
                        console.log('  Total cols: ' + m.totalCols);
                        console.log('  Max col index: ' + m.maxCol);
                        console.log('  Single-column mode: ' + (window.isSingleColumnMode ? 'YES' : 'NO'));
                        console.log('  clientW: ' + m.clientWidth + 'px');
                        console.log('  availableW: ' + m.availableWidth + 'px (padding: ' + m.paddingLeft + '/' + m.paddingRight + ')');
                        console.log('  gap: ' + m.gap + 'px');
                        console.log('  columnW: ' + m.columnWidth.toFixed(1) + 'px');
                        console.log('  pageW: ' + m.pageWidth.toFixed(1) + 'px');
                        console.log('  scrollW: ' + m.container.scrollWidth + 'px');
                        console.log('  scrollH: ' + m.container.scrollHeight + 'px');
                        
                        if (m.colCount > 1 && !window.isSingleColumnMode) {{
                            snapScroll();
                        }}
                    }}
                }}, 200);
                
                console.log('=== SCRIPT READY ===');
            }})();
            </script>"""

        link_intercept_script = """
        <script>
        (function(){
            document.addEventListener('click', function(e) {
                var target = e.target;
                while (target && target.tagName !== 'A') {
                    target = target.parentElement;
                }
                if (target && target.tagName === 'A' && target.href) {
                    e.preventDefault();
                    window.location.href = target.href;
                }
            });
        })();
        </script>
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

        # Add extra empty column at the end for proper spacing in multi-column mode
        extra_column_html = '<div class="extra-column-spacer" style="height: 100%; width: 1px;"></div>'
        wrapped = "<!DOCTYPE html><html><head>{}</head><body><div class=\"ebook-content\">{}{}</div></body></html>".format(head, raw_html, extra_column_html)
        return wrapped

    def update_webview_theme(self):
        js = f"""
            (function() {{
                const html = document.documentElement;
                const body = document.body;
                if (html) html.style.background = "{page_bg}";
                if (body) body.style.color = "{text_fg}";
                return "theme_updated";
            }})();
        """

        if not hasattr(self, "webview") or not self.webview:
            return

        def _on_js_finished(webview, result, user_data=None):
            try:
                webview.evaluate_javascript_finish(result)
                print("✅ WebView theme update: completed")
            except Exception as e:
                print("⚠️ JS eval failed:", e)

        try:
            # Fixed: proper parameter order for evaluate_javascript
            self.webview.evaluate_javascript(
                js,                  # script
                -1,                  # length (-1 = null-terminated)
                None,                # world_name
                None,                # source_uri
                None,                # cancellable (MUST be None or Gio.Cancellable)
                _on_js_finished,     # callback
                None                 # user_data
            )
        except Exception as e:
            print("❌ Failed to inject theme JS:", e)

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

            def _safe_parse_ncx(reader_self, ncxFile): 
                """Local safe patch to avoid crashes on problematic NCX files."""
                print(f"[DEBUG] Applying safe NCX patch for {path}") # Optional debug print
                # Use 'reader_self' (the EpubReader instance) to modify its book's TOC
                reader_self.book.toc = [] 

            # Store the original method to restore it later
            import ebooklib.epub # Ensure ebooklib is imported in this scope if not at module level for the getattr
            original_parse_ncx = getattr(ebooklib.epub.EpubReader, '_parse_ncx', None) 

            # Apply the patch to the CLASS just before reading
            ebooklib.epub.EpubReader._parse_ncx = _safe_parse_ncx

            # Now read the EPUB - the internal EpubReader will use the patched method

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
            print(f"extracted_paths = {self.temp_dir}")
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
          
          # set title and app name
            title = APP_NAME; author = ""
            try:
                meta = self.book.get_metadata("DC", "title");
                if meta and meta[0]: title = meta[0][0]
                m2 = self.book.get_metadata("DC", "creator")
                if m2 and m2[0]: author = m2[0][0]
            except Exception:
                pass
            self.book_title.set_text(title)
            self.book_author.set_text(author)

            # Dynamically adjust title margin based on its length
            if len(title) > 35:  # Adjust threshold as needed
                self.book_title.set_margin_bottom(15)  # Small margin for long/wrapped titles
            else:
                self.book_title.set_margin_bottom(6)  # Larger margin for short/single-line titles

            self.content_title_label.set_text(title)
            self.set_title(title or APP_NAME)


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
                if self.webview: 
                    self.webview.load_html(html, base_uri)

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

    def display_page(self, index=None, fragment=None):
        """
        Show item at self.current_index (or `index` if provided).
        Clean only the BODY/content fragment (so injected <style>/<head> from _wrap_html
        is preserved), then wrap and load into the WebView. Fallback to textview if no webview.
        """
        try:
            if index is not None:
                self.current_index = index

            if not getattr(self, "items", None) or self.current_index is None:
                return False

            if self.current_index < 0 or self.current_index >= len(self.items):
                return False

            # Check if TTS is currently active
            tts_was_playing = False
            current_tts_index = -1
            current_tts_sentences = []
            try:
                if self.tts and self.tts.is_playing():
                    tts_was_playing = True
                    current_tts_index = getattr(self.tts, '_current_play_index', -1)
                    current_tts_sentences = getattr(self.tts, '_tts_sentences', [])
                    print(f"[TTS] Active during display_page, current index: {current_tts_index}")
            except Exception:
                pass

            item = self.items[self.current_index]

            # get raw item content
            try:
                raw = item.get_content() or ""
            except Exception:
                raw = ""

            # parse and strip dangerous embeds but keep style/link tags
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup.find_all(['script', 'noscript', 'iframe', 'object', 'embed']):
                tag.decompose()

            body = soup.find("body")
            if body:
                # preserve body attributes if any
                if body.attrs:
                    attr_str = " ".join(f'{k}="{v}"' for k, v in body.attrs.items())
                    content = f'<div {attr_str}>{"".join(str(child) for child in body.children)}</div>'
                else:
                    content = "".join(str(child) for child in body.children)
            else:
                content = str(soup)

            # base URI for relative resources
            try:
                base_path = os.path.join(self.temp_dir or "", os.path.dirname(item.get_name() or ""))
                base_uri = f"file://{base_path}/"
            except Exception:
                base_uri = None

            # sanitize only the content fragment (not the final wrapped HTML)
            try:
                cleaned_content = self.generic_clean_html(content)
            except Exception:
                cleaned_content = content

            # wrap (this injects CSS/HEAD safely)
            try:
                wrapped_html = self._wrap_html(cleaned_content, base_uri)
            except Exception:
                # fallback: wrap minimally if _wrap_html fails
                wrapped_html = f"<!doctype html><html><head></head><body>{cleaned_content}</body></html>"

            # load into webview if present
            if getattr(self, "webview", None):
                try:
                    if base_uri:
                        self.webview.load_html(wrapped_html, base_uri)
                    else:
                        # if base_uri isn't available, pass empty string
                        self.webview.load_html(wrapped_html, "")

                    if fragment:
                        GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                    
                    # If TTS was playing, re-wrap sentences and restore highlight
                    if tts_was_playing and current_tts_index >= 0 and current_tts_sentences:
                        print(f"[TTS] Re-wrapping after column change")
                        
                        def restore_tts_highlight():
                            try:
                                # Re-wrap sentences in the new layout
                                sentences_meta = []
                                for i, sent in enumerate(current_tts_sentences):
                                    if isinstance(sent, dict):
                                        sentences_meta.append(sent)
                                    else:
                                        sentences_meta.append({"sid": i, "text": str(sent)})
                                
                                # Use the same wrapping logic
                                self._ensure_sentence_wrapping_and_start(
                                    sentences_meta,
                                    auto_start=False  # Don't restart TTS, just wrap
                                )
                                
                                # Re-highlight current sentence after wrapping
                                GLib.timeout_add(100, lambda: (
                                    self._on_tts_highlight(
                                        current_tts_index,
                                        {"sid": current_tts_index, "text": current_tts_sentences[current_tts_index]}
                                    ),
                                    False
                                )[1])
                                
                                print(f"[TTS] Restored highlight at index {current_tts_index}")
                            except Exception as e:
                                print(f"[TTS] Error restoring highlight: {e}")
                            return False
                        
                        # Wait for page to fully load, then restore
                        GLib.timeout_add(500, restore_tts_highlight)
                        
                except Exception as e:
                    print("Failed to load webview:", e)
                    # fallback to plain text view
                    if getattr(self, "textview", None):
                        buf = self.textview.get_buffer()
                        buf.set_text(BeautifulSoup(cleaned_content, "html.parser").get_text())
            else:
                # no webview: put plain text into textview
                if getattr(self, "textview", None):
                    buf = self.textview.get_buffer()
                    buf.set_text(BeautifulSoup(cleaned_content, "html.parser").get_text())

            return True
        except Exception as e:
            print("display_page error:", e)
            return False



    def generic_clean_html(self, html: str,
                           allowed_tags=None,
                           allowed_attrs=None,
                           remove_processing_instructions=True,
                           strip_comments=True) -> str:
        """
        Generic sanitizer for EPUB XHTML before parsing/rendering/TTS.
        - conservative allowlist, unwraps disallowed tags (keeps children).
        - strips disallowed attributes.
        - removes processing instructions, xml decls, comments, tiny punctuation-only nodes.
        """
        if not isinstance(html, str):
            try:
                html = html.decode("utf-8", errors="replace")
            except Exception:
                html = str(html)

        # 1) quick removals by regex (pre-parse)
        if remove_processing_instructions:
            html = re.sub(r'<\?[^>]*\?>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'dp\s*n="[^"]*"\s*folio="[^"]*"\s*\?*', '', html, flags=re.IGNORECASE)
        html = re.sub(r'(?m)^[\s\?]{1,8}\n?', '', html)   # remove isolated punctuation-only lines
        html = html.replace('??', '')

        # 2) parse for structured cleaning
        soup = BeautifulSoup(html, "lxml")

        # remove ProcessingInstruction nodes if parser exposed them
        for pi in list(soup.find_all(string=lambda s: isinstance(s, ProcessingInstruction))):
            pi.extract()

        # remove comments if requested
        if strip_comments:
            for c in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
                c.extract()

        # --- IMPORTANT: fully remove script-like and embed tags so their text doesn't appear ---
        for bad in soup.find_all(['script', 'noscript', 'iframe', 'object']):
            bad.decompose()

        # defaults: conservative allowlist (add tags you need)
        if allowed_tags is None:
            allowed_tags = {
                'html','head','body','meta','base','style','link',
                'div','p','span','br','hr',
                'h1','h2','h3','h4','h5','h6',
                'a','img','ul','ol','li','strong','b','em','i','u','sup','sub',
                'blockquote','pre','code','table','thead','tbody','tr','td','th'
            }
        if allowed_attrs is None:
            allowed_attrs = {
                'a': ['href', 'title', 'id', 'class', 'data-tts-id', 'data-sid'],
                'img': ['src', 'alt', 'title', 'width', 'height', 'class'],
                'link': ['rel', 'href', 'type', 'media', 'as', 'crossorigin'],
                '*': ['id', 'class', 'style', 'title', 'data-*']
            }

        # helper to check allowed attrs (supports data-* wildcard)
        def attr_allowed(tag, attr):
            if tag in allowed_attrs:
                allowed = allowed_attrs[tag]
            else:
                allowed = allowed_attrs.get('*', [])
            if attr.startswith('data-'):
                return any(a.endswith('*') or a == 'data-*' for a in allowed)
            return attr in allowed

        # remove disallowed tags (unwrap) and strip disallowed attributes
        for el in list(soup.find_all()):
            name = getattr(el, 'name', None)
            if not name:
                continue
            name = name.lower()
            if name not in allowed_tags:
                # unwrap the tag (keep children)
                try:
                    el.unwrap()
                except Exception:
                    try:
                        el.decompose()
                    except Exception:
                        pass
                continue
            # clean attributes
            if getattr(el, 'attrs', None):
                for k in list(el.attrs.keys()):
                    if not attr_allowed(name, k):
                        try:
                            del el.attrs[k]
                        except KeyError:
                            pass

        # remove tiny nodes that are only punctuation-artifacts and normalize whitespace
        for t in list(soup.find_all(string=True)):
            s = str(t)
            # remove nodes that are purely a few question marks / whitespace (artifact)
            if re.fullmatch(r'\s*[\?]{1,4}\s*', s):
                t.extract()
                continue
            # normalize whitespace in text nodes (collapse runs)
            new = re.sub(r'\s+', ' ', s)
            if new != s:
                t.replace_with(new)

        return str(soup)


        # helper to check allowed attrs (supports data-* wildcard)
        def attr_allowed(tag, attr):
            if tag in allowed_attrs:
                allowed = allowed_attrs[tag]
            else:
                allowed = allowed_attrs.get('*', [])
            if attr.startswith('data-'):
                return any(a.endswith('*') or a == 'data-*' for a in allowed)
            return attr in allowed

        # remove disallowed tags (unwrap) and strip disallowed attributes
        for el in list(soup.find_all()):
            name = getattr(el, 'name', None)
            if not name:
                continue
            name = name.lower()
            if name not in allowed_tags:
                # unwrap the tag (keep children)
                try:
                    el.unwrap()
                except Exception:
                    try:
                        el.decompose()
                    except Exception:
                        pass
                continue
            # clean attributes
            if getattr(el, 'attrs', None):
                for k in list(el.attrs.keys()):
                    if not attr_allowed(name, k):
                        try:
                            del el.attrs[k]
                        except KeyError:
                            pass

        # remove tiny nodes that are only punctuation-artifacts and normalize whitespace
        for t in list(soup.find_all(string=True)):
            s = str(t)
            # remove nodes that are purely a few question marks / whitespace (artifact)
            if re.fullmatch(r'\s*[\?]{1,4}\s*', s):
                t.extract()
                continue
            # normalize whitespace in text nodes (collapse runs)
            new = re.sub(r'\s+', ' ', s)
            if new != s:
                t.replace_with(new)

        return str(soup)


        # helper to check allowed attrs (supports data-* wildcard)
        def attr_allowed(tag, attr):
            if tag in allowed_attrs:
                allowed = allowed_attrs[tag]
            else:
                allowed = allowed_attrs.get('*', [])
            if attr.startswith('data-'):
                return any(a.endswith('*') or a == 'data-*' for a in allowed)
            return attr in allowed

        # remove disallowed tags (unwrap) and strip disallowed attributes
        for el in list(soup.find_all()):
            name = getattr(el, 'name', None)
            if not name:
                continue
            name = name.lower()
            if name not in allowed_tags:
                # unwrap the tag (keep children)
                try:
                    el.unwrap()
                except Exception:
                    try:
                        el.decompose()
                    except Exception:
                        pass
                continue
            # clean attributes
            if getattr(el, 'attrs', None):
                for k in list(el.attrs.keys()):
                    if not attr_allowed(name, k):
                        try:
                            del el.attrs[k]
                        except KeyError:
                            pass

        # remove tiny nodes that are only punctuation-artifacts and normalize whitespace
        for t in list(soup.find_all(string=True)):
            s = str(t)
            # remove nodes that are purely a few question marks / whitespace (artifact)
            if re.fullmatch(r'\s*[\?]{1,4}\s*', s):
                t.extract()
                continue
            # normalize whitespace in text nodes (collapse runs)
            new = re.sub(r'\s+', ' ', s)
            if new != s:
                t.replace_with(new)

        return str(soup)


        # helper: check attr allowed
        def attr_allowed(tag, attr):
            if tag in allowed_attrs:
                allowed = allowed_attrs[tag]
            else:
                allowed = allowed_attrs.get('*', [])
            # allow data-* wildcard
            if attr.startswith('data-'):
                return any(a.endswith('*') or a == 'data-*' for a in allowed)
            return attr in allowed

        # remove disallowed tags and attributes (but keep their children for benign tags)
        for el in list(soup.find_all()):
            name = el.name.lower() if getattr(el, 'name', None) else None
            if not name:
                continue
            if name not in allowed_tags:
                # unwrap: replace tag with its children text / nodes
                el.unwrap()
                continue
            # clean attributes
            if el.attrs:
                # iterate copy of keys
                for k in list(el.attrs.keys()):
                    if not attr_allowed(name, k):
                        del el.attrs[k]

        # 3) remove tiny nodes that are just punctuation left over (e.g., '? ?' or '??')
        for t in list(soup.find_all(string=True)):
            s = str(t)
            if re.fullmatch(r'\s*[\?]{1,4}\s*', s):
                t.extract()
            else:
                # normalize whitespace in text nodes
                new = re.sub(r'\s+', ' ', s)
                if new != s:
                    t.replace_with(new)

        return str(soup)


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

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.epubviewer")
        # simple quit
        self.create_action("quit", self.quit, ["<primary>q"])
        self.create_action("set-theme", self.on_set_theme)

        # create parameterized actions that call into the active window
        def _action_wrapper_win(method_name, variant):
            win = self.props.active_window
            if not win:
                wins = self.get_windows() if hasattr(self, "get_windows") else []
                win = wins[0] if wins else None
            if not win:
                return
            try:
                # variant is a GLib.Variant when provided
                if variant is None:
                    getattr(win, method_name)()
                else:
                    # if variant is integer-like, extract
                    val = None
                    try:
                        # works for int parameters
                        val = int(variant.unpack())
                    except Exception:
                        try:
                            val = variant.unpack()
                        except Exception:
                            val = variant
                    getattr(win, method_name)(val)
            except Exception:
                pass

        # set-column-width (int) - only action needed for width-based columns
        act2 = Gio.SimpleAction.new("set-column-width", GLib.VariantType.new("i"))
        act2.connect("activate", lambda a, v: _action_wrapper_win("set_column_width", v))
        self.add_action(act2)

    def do_activate(self):
        win = self.props.active_window
        if not win: win = EPubViewer(self)
        win.present()
    def create_action(self, name, callback, shortcuts=None, variant_type="s"):
        # variant_type: "s"=string, "i"=int, None=no parameter
        variant = GLib.VariantType.new(variant_type) if variant_type else None
        action = Gio.SimpleAction.new(name, variant)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


    def on_set_theme(self, action, parameter):
        """Handle app.set-theme('light'|'dark'|'sepia'|'auto')"""
        if not parameter:
            return

        theme_name = parameter.get_string()

        win = self.props.active_window
        if not win:
            return

        # Call your window’s theme apply function
        if hasattr(win, "apply_theme"):
            win.apply_theme(theme_name)

def main():
    _ensure_library_dir()
    app = Application()
    return app.run(None)

if __name__ == "__main__":
    main()
