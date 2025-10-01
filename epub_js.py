#!/usr/bin/env python3
"""
EPUB/HTML reader for WebKitGTK6 + epub.js with TTS support using Kokoro
"""
import os
import base64
import tempfile
import pathlib
import sys
import json
import threading
import time

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, WebKit, GLib, Adw, Gst

try:
    import soundfile as sf
    from kokoro_onnx import Kokoro
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    print("[warn] TTS not available: kokoro_onnx or soundfile not installed")

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_JSZIP = HERE / "jszip.min.js"
LOCAL_EPUBJS = HERE / "epub.min.js"

def writable_path(filename):
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)
    return os.path.join(d, filename)

class TTSEngine:
    def __init__(self):
        self.kokoro = None
        self.is_playing = False
        self.should_stop = False
        self.current_thread = None
        self.highlight_callback = None
        
        if TTS_AVAILABLE:
            try:
                model_path = "/app/share/kokoro-models/kokoro-v1.0.onnx"
                voices_path = "/app/share/kokoro-models/voices-v1.0.bin"
                
                # Fallback paths
                if not os.path.exists(model_path):
                    model_path = os.path.expanduser("~/.local/share/kokoro-models/kokoro-v1.0.onnx")
                    voices_path = os.path.expanduser("~/.local/share/kokoro-models/voices-v1.0.bin")
                
                if os.path.exists(model_path) and os.path.exists(voices_path):
                    self.kokoro = Kokoro(model_path, voices_path)
                    print("[info] Kokoro TTS initialized")
                else:
                    print(f"[warn] Kokoro models not found at {model_path}")
            except Exception as e:
                print(f"[error] Failed to initialize Kokoro: {e}")
        
        # Initialize GStreamer for audio playback
        Gst.init(None)
        self.player = Gst.ElementFactory.make("playbin", "player")
        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_gst_message)
        self.playback_finished = False
    
    def on_gst_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL)
            self.playback_finished = True
        elif t == Gst.MessageType.ERROR:
            self.player.set_state(Gst.State.NULL)
            err, debug = message.parse_error()
            print(f"[error] GStreamer error: {err}, {debug}")
            self.playback_finished = True
    
    def split_sentences(self, text):
        """Split text into sentences"""
        import re
        # Simple sentence splitter - splits on . ! ? followed by space or end
        sentences = re.split(r'([.!?]+(?:\s+|$))', text)
        result = []
        for i in range(0, len(sentences)-1, 2):
            sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else '')
            sentence = sentence.strip()
            if sentence:
                result.append(sentence)
        # Handle last sentence if it doesn't end with punctuation
        if len(sentences) % 2 == 1 and sentences[-1].strip():
            result.append(sentences[-1].strip())
        return result
    
    def synthesize_sentence(self, sentence, voice, speed, lang):
        """Synthesize a single sentence and return the audio file path"""
        try:
            samples, sample_rate = self.kokoro.create(sentence, voice=voice, speed=speed, lang=lang)
            temp_file = writable_path(f"tts_{int(time.time() * 1000)}_{os.getpid()}.wav")
            sf.write(temp_file, samples, sample_rate)
            return temp_file
        except Exception as e:
            print(f"[error] Synthesis error for sentence: {e}")
            return None
    
    def speak_sentences(self, text, voice="af_sarah", speed=1.0, lang="en-us", 
                       highlight_callback=None, finished_callback=None):
        """Speak text sentence by sentence with pre-synthesis for smooth playback"""
        if not self.kokoro:
            print("[warn] TTS not available")
            return
        
        self.stop()
        self.should_stop = False
        self.highlight_callback = highlight_callback
        
        def tts_thread():
            try:
                sentences = self.split_sentences(text)
                print(f"[TTS] Speaking {len(sentences)} sentences")
                
                # Queue to hold pre-synthesized audio files
                from queue import Queue
                audio_queue = Queue(maxsize=3)  # Buffer up to 3 sentences ahead
                synthesis_complete = threading.Event()
                
                def synthesis_worker():
                    """Worker thread to synthesize sentences ahead of playback"""
                    try:
                        for idx, sentence in enumerate(sentences):
                            if self.should_stop:
                                break
                            
                            print(f"[TTS] Synthesizing sentence {idx+1}/{len(sentences)}")
                            audio_file = self.synthesize_sentence(sentence, voice, speed, lang)
                            
                            if audio_file and not self.should_stop:
                                audio_queue.put((idx, sentence, audio_file))
                            elif self.should_stop:
                                break
                        
                        synthesis_complete.set()
                    except Exception as e:
                        print(f"[error] Synthesis worker error: {e}")
                        import traceback
                        traceback.print_exc()
                        synthesis_complete.set()
                
                # Start synthesis worker thread
                synth_thread = threading.Thread(target=synthesis_worker, daemon=True)
                synth_thread.start()
                
                # Playback loop
                played_count = 0
                while played_count < len(sentences) and not self.should_stop:
                    try:
                        # Wait for next audio file (with timeout)
                        idx, sentence, audio_file = audio_queue.get(timeout=30)
                        
                        if self.should_stop:
                            try:
                                os.remove(audio_file)
                            except:
                                pass
                            break
                        
                        print(f"[TTS] Playing sentence {idx+1}/{len(sentences)}: {sentence[:50]}...")
                        
                        # Highlight current sentence
                        if highlight_callback:
                            GLib.idle_add(highlight_callback, idx, sentence)
                        
                        # Play the audio file
                        self.player.set_property("uri", f"file://{audio_file}")
                        self.player.set_state(Gst.State.PLAYING)
                        self.playback_finished = False
                        
                        # Wait for playback to finish
                        while not self.playback_finished and not self.should_stop:
                            time.sleep(0.02)
                        
                        # Cleanup audio file
                        try:
                            os.remove(audio_file)
                        except:
                            pass
                        
                        played_count += 1
                        
                    except Exception as e:
                        if not synthesis_complete.is_set():
                            print(f"[error] Playback error: {e}")
                        break
                
                # Wait for synthesis to complete and clean up any remaining files
                synth_thread.join(timeout=2.0)
                while not audio_queue.empty():
                    try:
                        _, _, audio_file = audio_queue.get_nowait()
                        os.remove(audio_file)
                    except:
                        pass
                
                # Clear highlight when done
                if highlight_callback and not self.should_stop:
                    GLib.idle_add(highlight_callback, -1, "")
                
                if finished_callback:
                    GLib.idle_add(finished_callback)
                    
            except Exception as e:
                print(f"[error] TTS error: {e}")
                import traceback
                traceback.print_exc()
                if finished_callback:
                    GLib.idle_add(finished_callback)
        
        self.current_thread = threading.Thread(target=tts_thread, daemon=True)
        self.current_thread.start()
    
    def stop(self):
        self.should_stop = True
        if self.player:
            self.player.set_state(Gst.State.NULL)
        self.playback_finished = True
        if self.current_thread:
            self.current_thread.join(timeout=1.0)

class EpubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, application, file_path=None):
        super().__init__(application=application)
        self.set_default_size(1200, 800)
        self.set_title("EPUB/HTML Reader with TTS")
        self.temp_dir = None
        self.tts_engine = TTSEngine() if TTS_AVAILABLE else None
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        # Navigation buttons
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_bar.pack_start(nav_box)

        self.prev_button = Gtk.Button(label="←")
        self.prev_button.set_tooltip_text("Previous chapter")
        self.prev_button.connect("clicked", self.go_prev)
        nav_box.append(self.prev_button)

        self.next_button = Gtk.Button(label="→")
        self.next_button.set_tooltip_text("Next chapter")
        self.next_button.connect("clicked", self.go_next)
        nav_box.append(self.next_button)

        # TTS buttons
        if TTS_AVAILABLE and self.tts_engine and self.tts_engine.kokoro:
            tts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_bar.pack_start(tts_box)
            
            self.tts_play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
            self.tts_play_button.set_tooltip_text("Read current page")
            self.tts_play_button.connect("clicked", self.on_tts_play)
            tts_box.append(self.tts_play_button)
            
            self.tts_stop_button = Gtk.Button(icon_name="media-playback-stop-symbolic")
            self.tts_stop_button.set_tooltip_text("Stop reading")
            self.tts_stop_button.connect("clicked", self.on_tts_stop)
            self.tts_stop_button.set_sensitive(False)
            tts_box.append(self.tts_stop_button)

        self.toc_button = Gtk.Button(icon_name="view-list-symbolic")
        self.toc_button.set_tooltip_text("Table of Contents")
        self.toc_button.connect("clicked", self.toggle_toc)
        header_bar.pack_end(self.toc_button)

        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        self.split_view = Adw.OverlaySplitView()
        main_box.append(self.split_view)

        self.toc_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toc_sidebar.set_size_request(250, -1)
        self.toc_sidebar.set_visible(False)
        self.split_view.set_sidebar(self.toc_sidebar)

        toc_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toc_header.append(Gtk.Label(label="Table of Contents", hexpand=True))
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.connect("clicked", lambda b: self.split_view.set_collapsed(True))
        toc_header.append(close_btn)
        self.toc_sidebar.append(toc_header)

        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_vexpand(True)
        self.toc_sidebar.append(toc_scroll)
        self.toc_listbox = Gtk.ListBox()
        self.toc_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        toc_scroll.set_child(self.toc_listbox)

        scrolled = Gtk.ScrolledWindow()
        self.split_view.set_content(scrolled)
        self.webview = WebKit.WebView()
        self.setup_webview()
        self.webview.set_vexpand(True)
        scrolled.set_child(self.webview)

        self.user_manager = self.webview.get_user_content_manager()
        self.user_manager.register_script_message_handler("tocLoaded")
        self.user_manager.register_script_message_handler("navChanged")
        self.user_manager.register_script_message_handler("pageText")
        self.user_manager.connect("script-message-received::tocLoaded", self.on_toc_loaded)
        self.user_manager.connect("script-message-received::navChanged", self.on_nav_changed)
        self.user_manager.connect("script-message-received::pageText", self.on_page_text)

        forwarder = WebKit.UserScript(
            """
            (function(){
              window.addEventListener('message', function(event) {
                try {
                  if (!event.data) return;
                  var handlers = ['tocLoaded', 'navChanged', 'pageText'];
                  handlers.forEach(function(handler) {
                    if (event.data.type === handler) {
                      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers[handler]) {
                        var payload = event.data.payload;
                        if (typeof payload !== 'string') payload = JSON.stringify(payload);
                        window.webkit.messageHandlers[handler].postMessage(payload);
                      }
                    }
                  });
                } catch(e) {}
              }, false);
            })();
            """,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.START,
            None,
            None
        )
        self.user_manager.add_script(forwarder)

        if file_path:
            self.load_file(file_path)

    def setup_webview(self):
        settings = self.webview.get_settings()
        try:
            settings.set_enable_javascript(True)
        except Exception:
            pass
        for name in ("set_allow_file_access_from_file_urls",
                     "set_allow_universal_access_from_file_urls",
                     "set_enable_write_console_messages_to_stdout"):
            try:
                getattr(settings, name)(True)
            except Exception:
                pass

    def _extract_message_string(self, message):
        try:
            if hasattr(message, "to_string"):
                try:
                    return message.to_string()
                except Exception:
                    pass
                try:
                    return message.to_json(0)
                except Exception:
                    pass
            
            if hasattr(message, "get_js_value"):
                jsval = message.get_js_value()
                try:
                    return jsval.to_string()
                except Exception:
                    pass
                try:
                    return jsval.to_json(0)
                except Exception:
                    pass
            
            if isinstance(message, GLib.Variant):
                v = message.unpack()
                if isinstance(v, (str, bytes)):
                    return v.decode() if isinstance(v, bytes) else v
                return json.dumps(v)
            
            if hasattr(message, "get_string"):
                try:
                    s = message.get_string()
                    if s is not None:
                        return s
                except Exception:
                    pass
            
            return str(message)
        except Exception as e:
            print("extract_message_string error:", e)
            import traceback
            traceback.print_exc()
            return None

    def on_toc_loaded(self, manager, message):
        try:
            raw = self._extract_message_string(message)
            if not raw:
                print("on_toc_loaded: empty payload")
                return
            
            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass
            
            toc = None
            try:
                toc = json.loads(raw)
            except Exception:
                try:
                    toc = json.loads(raw.replace("'", '"'))
                except Exception:
                    toc = None
            
            if toc is None:
                print("on_toc_loaded: failed to decode toc payload")
                return
            
            if isinstance(toc, dict) and "toc" in toc:
                toc = toc["toc"]
            if not isinstance(toc, list):
                toc = [toc]
            
            self.populate_toc(toc)
        except Exception as e:
            print(f"Error processing TOC: {e}")
            import traceback
            traceback.print_exc()

    def on_nav_changed(self, manager, message):
        try:
            raw = self._extract_message_string(message)
            if not raw:
                return
            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass
            try:
                nav = json.loads(raw)
            except Exception:
                try:
                    nav = json.loads(raw.replace("'", '"'))
                except Exception:
                    nav = None
        except Exception as e:
            print(f"Error processing nav: {e}")
    
    def on_page_text(self, manager, message):
        """Receive text content from current page for TTS"""
        try:
            raw = self._extract_message_string(message)
            if not raw:
                return
            
            try:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
            except Exception:
                pass
            
            text = raw.strip()
            if text and self.tts_engine:
                print(f"[TTS] Received text: {len(text)} chars")
                self.tts_stop_button.set_sensitive(True)
                self.tts_play_button.set_sensitive(False)
                
                # Start sentence-by-sentence TTS with highlighting
                self.tts_engine.speak_sentences(
                    text, 
                    highlight_callback=self.highlight_sentence,
                    finished_callback=self.on_tts_finished
                )
        except Exception as e:
            print(f"Error processing page text: {e}")
            import traceback
            traceback.print_exc()
    
    def highlight_sentence(self, sentence_idx, sentence_text):
        """Highlight the current sentence being read"""
        if sentence_idx < 0:
            # Clear all highlights
            js_code = """
            (function() {
                try {
                    var iframe = document.querySelector('#viewer iframe');
                    if (iframe && iframe.contentDocument) {
                        var doc = iframe.contentDocument;
                        var highlights = doc.querySelectorAll('.tts-highlight');
                        highlights.forEach(function(el) {
                            var text = el.textContent;
                            var textNode = doc.createTextNode(text);
                            el.parentNode.replaceChild(textNode, el);
                        });
                        doc.normalize();
                    }
                } catch(e) {
                    console.error('Error clearing highlights:', e);
                }
            })();
            """
        else:
            # Escape special characters for JavaScript
            escaped_text = sentence_text.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
            
            js_code = f"""
            (function() {{
                try {{
                    var iframe = document.querySelector('#viewer iframe');
                    if (iframe && iframe.contentDocument) {{
                        var doc = iframe.contentDocument;
                        
                        // Clear previous highlights
                        var oldHighlights = doc.querySelectorAll('.tts-highlight');
                        oldHighlights.forEach(function(el) {{
                            var text = el.textContent;
                            var textNode = doc.createTextNode(text);
                            el.parentNode.replaceChild(textNode, el);
                        }});
                        doc.normalize();
                        
                        // Find and highlight current sentence
                        var sentenceToFind = '{escaped_text}';
                        
                        // Function to search and highlight in text nodes
                        function highlightInNode(node) {{
                            if (node.nodeType === Node.TEXT_NODE) {{
                                var text = node.textContent;
                                var index = text.indexOf(sentenceToFind);
                                
                                if (index >= 0) {{
                                    var beforeText = text.substring(0, index);
                                    var matchText = text.substring(index, index + sentenceToFind.length);
                                    var afterText = text.substring(index + sentenceToFind.length);
                                    
                                    var parent = node.parentNode;
                                    var before = doc.createTextNode(beforeText);
                                    var highlight = doc.createElement('span');
                                    highlight.className = 'tts-highlight';
                                    highlight.style.backgroundColor = '#ffeb3b';
                                    highlight.style.color = '#000';
                                    highlight.style.padding = '2px 0';
                                    highlight.textContent = matchText;
                                    var after = doc.createTextNode(afterText);
                                    
                                    parent.insertBefore(before, node);
                                    parent.insertBefore(highlight, node);
                                    parent.insertBefore(after, node);
                                    parent.removeChild(node);
                                    
                                    // Scroll to highlight
                                    highlight.scrollIntoView({{
                                        behavior: 'smooth',
                                        block: 'center'
                                    }});
                                    
                                    return true;
                                }}
                            }} else if (node.nodeType === Node.ELEMENT_NODE) {{
                                // Recursively search in all child nodes, including headers
                                for (var i = 0; i < node.childNodes.length; i++) {{
                                    if (highlightInNode(node.childNodes[i])) {{
                                        return true;
                                    }}
                                }}
                            }}
                            return false;
                        }}
                        
                        // Start search from body
                        highlightInNode(doc.body);
                    }}
                }} catch(e) {{
                    console.error('Error highlighting sentence:', e);
                }}
            }})();
            """
        
        try:
            self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)
    
    def on_tts_play(self, button):
        """Request current page text for TTS"""
        js_code = """
        (function() {
            try {
                var iframe = document.querySelector('#viewer iframe');
                if (iframe && iframe.contentDocument) {
                    var body = iframe.contentDocument.body;
                    var text = body.innerText || body.textContent || '';
                    text = text.trim();
                    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pageText) {
                        window.webkit.messageHandlers.pageText.postMessage(text);
                    } else {
                        window.postMessage({ type: 'pageText', payload: text }, '*');
                    }
                } else {
                    console.error('Could not find iframe content');
                }
            } catch(e) {
                console.error('Error getting page text:', e);
            }
        })();
        """
        try:
            self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)
    
    def on_tts_stop(self, button):
        """Stop TTS playback"""
        if self.tts_engine:
            self.tts_engine.stop()
        # Clear highlights
        self.highlight_sentence(-1, "")
        self.on_tts_finished()
    
    def on_tts_finished(self):
        """Called when TTS finishes"""
        self.tts_play_button.set_sensitive(True)
        self.tts_stop_button.set_sensitive(False)

    def populate_toc(self, toc_data, parent_box=None, level=0):
        """Recursively populate TOC with nested items"""
        if parent_box is None:
            print(f"[DEBUG] populate_toc called with {len(toc_data)} items")
            
            child = self.toc_listbox.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.toc_listbox.remove(child)
                child = next_child
            
            parent_box = self.toc_listbox
        
        for item in toc_data:
            label_text = item.get('label', '').strip() if isinstance(item, dict) else str(item).strip()
            href = item.get('href', '') if isinstance(item, dict) else ''
            subitems = item.get('subitems', []) if isinstance(item, dict) else []
            
            if not label_text:
                label_text = 'Unknown'
            
            if subitems and len(subitems) > 0:
                row = Gtk.ListBoxRow()
                row.set_activatable(bool(href))
                
                expander = Gtk.Expander()
                expander.set_label(label_text)
                expander.set_margin_start(12 + (level * 16))
                expander.set_margin_end(12)
                expander.set_margin_top(4)
                expander.set_margin_bottom(4)
                
                subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                expander.set_child(subitems_box)
                
                row.set_child(expander)
                
                if href:
                    row.href = href
                    expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))
                
                parent_box.append(row)
                
                for subitem in subitems:
                    self.add_toc_subitem(subitems_box, subitem, level + 1)
                    
            else:
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                
                label = Gtk.Label(
                    label=label_text,
                    halign=Gtk.Align.START,
                    margin_top=6,
                    margin_bottom=6,
                    margin_start=12 + (level * 16),
                    margin_end=12,
                    wrap=True,
                    xalign=0
                )
                
                row.set_child(label)
                
                if href:
                    row.href = href
                
                parent_box.append(row)
        
        if parent_box == self.toc_listbox:
            try:
                self.toc_listbox.disconnect_by_func(self.on_toc_row_activated)
            except:
                pass
            self.toc_listbox.connect('row-activated', self.on_toc_row_activated)
            
            self.toc_sidebar.set_visible(True)
            print(f"[DEBUG] TOC sidebar visible, {len(toc_data)} items added")
    
    def add_toc_subitem(self, parent_box, item, level):
        """Add a single TOC subitem"""
        label_text = item.get('label', '').strip() if isinstance(item, dict) else str(item).strip()
        href = item.get('href', '') if isinstance(item, dict) else ''
        subitems = item.get('subitems', []) if isinstance(item, dict) else []
        
        if not label_text:
            label_text = 'Unknown'
        
        if subitems and len(subitems) > 0:
            expander = Gtk.Expander()
            expander.set_label(label_text)
            expander.set_margin_start(level * 16)
            expander.set_margin_end(12)
            expander.set_margin_top(2)
            expander.set_margin_bottom(2)
            
            subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            expander.set_child(subitems_box)
            
            if href:
                expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))
            
            parent_box.append(expander)
            
            for subitem in subitems:
                self.add_toc_subitem(subitems_box, subitem, level + 1)
        else:
            button = Gtk.Button(label=label_text)
            button.set_has_frame(False)
            button.set_halign(Gtk.Align.START)
            button.set_margin_start(level * 16)
            button.set_margin_end(12)
            button.set_margin_top(2)
            button.set_margin_bottom(2)
            
            child = button.get_child()
            if child and isinstance(child, Gtk.Label):
                child.set_wrap(True)
                child.set_xalign(0)
            
            if href:
                button.connect('clicked', lambda b, h=href: self.go_to_chapter(h))
            
            parent_box.append(button)
    
    def on_toc_row_activated(self, listbox, row):
        """Handle TOC item click"""
        if hasattr(row, 'href') and row.href:
            print(f"[DEBUG] Navigating to: {row.href}")
            self.go_to_chapter(row.href)

    def go_to_chapter(self, href):
        """Navigate to a chapter by href"""
        escaped_href = href.replace("'", "\\'")
        js_code = f"if(window.rendition) {{ window.rendition.display('{escaped_href}'); }}"
        print(f"[DEBUG] Executing JS: {js_code}")
        try:
            self.webview.evaluate_javascript(js_code, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js_code, len(js_code), None, None, None, None, None, None)

    def toggle_toc(self, button):
        is_collapsed = self.split_view.get_collapsed()
        self.split_view.set_collapsed(not is_collapsed)

    def go_prev(self, button):
        js = "if(window.rendition) window.rendition.prev();"
        try:
            self.webview.evaluate_javascript(js, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js, len(js), None, None, None, None, None, None)

    def go_next(self, button):
        js = "if(window.rendition) window.rendition.next();"
        try:
            self.webview.evaluate_javascript(js, None, None, None, None, None, None, None)
        except TypeError:
            self.webview.evaluate_javascript(js, len(js), None, None, None, None, None, None)

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        filter = Gtk.FileFilter()
        filter.set_name("EPUB and HTML files")
        filter.add_pattern("*.epub")
        filter.add_pattern("*.html")
        filter.add_pattern("*.htm")
        dialog.set_default_filter(filter)
        dialog.open(self, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            self.load_file(file.get_path())
        except GLib.Error:
            pass

    def load_file(self, file_path):
        file_ext = pathlib.Path(file_path).suffix.lower()
        if file_ext == '.epub':
            self.load_epub(file_path)
        elif file_ext in ['.html', '.htm']:
            self.load_html(file_path)
        else:
            print(f"Unsupported file type: {file_ext}")

    def load_epub(self, epub_path):
        import zipfile
        import shutil
        
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"[warn] Failed to clean up temp dir: {e}")
        
        self.temp_dir = tempfile.mkdtemp(prefix="epub_reader_")
        try:
            with zipfile.ZipFile(epub_path, 'r') as zip_ref:
                zip_ref.extractall(self.temp_dir)
            print(f"[info] Extracted EPUB to: {self.temp_dir}")
        except Exception as e:
            print(f"[error] Failed to extract EPUB: {e}")
            return
        
        extracted_uri = pathlib.Path(self.temp_dir).as_uri()
        
        if LOCAL_JSZIP.exists():
            jszip_snippet = f"<script>{LOCAL_JSZIP.read_text(encoding='utf-8')}</script>"
            jszip_note = "[local]"
        else:
            jszip_snippet = '<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>'
            jszip_note = "[cdn]"

        if LOCAL_EPUBJS.exists():
            epubjs_snippet = f"<script>{LOCAL_EPUBJS.read_text(encoding='utf-8')}</script>"
            epubjs_note = "[local]"
        else:
            epubjs_snippet = '<script src="https://cdn.jsdelivr.net/npm/epubjs@0.3.92/dist/epub.min.js"></script>'
            epubjs_note = "[cdn]"

        epub_uri = pathlib.Path(epub_path).as_uri()

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPUB Reader</title>
  <style>
    html, body {{ height: 100%; margin: 0; padding: 0; background: #fff; }}
    #viewer {{ width: 100vw; height: 100vh; }}
    .epubjs-navigation {{ display: none; }}
  </style>
</head>
<body>
  <div id="viewer"></div>

  {jszip_snippet}
  {epubjs_snippet}

  <script>
    (function(){{
      console.log("Libraries: JSZip {jszip_note}, epub.js {epubjs_note}");
      const epubUrl = "{epub_uri}";
      const extractedPath = "{extracted_uri}";

      function sendTOCString(toc) {{
        try {{
          var payload = JSON.stringify(toc);
          console.log("Sending TOC:", payload.substring(0, 200));
          if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tocLoaded) {{
            window.webkit.messageHandlers.tocLoaded.postMessage(payload);
          }} else {{
            window.postMessage({{ type: 'tocLoaded', payload: payload }}, '*');
          }}
        }} catch (e) {{ console.error('sendTOCString error', e); }}
      }}

      function sendNavString(nav) {{
        try {{
          var payload = JSON.stringify(nav);
          if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.navChanged) {{
            window.webkit.messageHandlers.navChanged.postMessage(payload);
          }} else {{
            window.postMessage({{ type: 'navChanged', payload: payload }}, '*');
          }}
        }} catch (e) {{ console.error('sendNavString error', e); }}
      }}

      try {{
        const book = ePub(epubUrl);
        
        window.rendition = book.renderTo("viewer", {{ 
          width: "100%", 
          height: "100%"
        }});
        
        window.rendition.display();

        book.loaded.navigation.then(function(nav){{
          console.log("epub.js nav:", nav);
          var toc = nav.toc || nav;
          console.log("Extracted TOC:", toc);
          sendTOCString(toc);
        }}).catch(function(err) {{
          console.error("Navigation loading error:", err);
        }});

        window.rendition.on('relocated', function(location) {{
          sendNavString({{ 
            current: location.start.cfi, 
            percent: Math.round(location.start.percentage * 100) 
          }});
        }});

        window.goToChapter = function(href) {{ 
          console.log("goToChapter called with:", href);
          window.rendition.display(href); 
        }};
      }} catch (e) {{
        console.error("EPUB rendering error:", e);
        document.body.innerHTML = "<h2>EPUB rendering error</h2><pre>" + String(e) + "</pre>";
      }}
    }})();
  </script>
</body>
</html>"""

        self.webview.load_html(html_content, "file:///")
    
    def __del__(self):
        """Cleanup temp directory on destruction"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                import shutil
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

    def load_html(self, html_path):
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            base_uri = f"file://{pathlib.Path(html_path).parent.absolute()}/"
            self.webview.load_html(html_content, base_uri)
        except Exception as e:
            print(f"Error loading HTML file: {e}")
            self.webview.load_html(f"<html><body><h1>Error loading file: {e}</h1></body></html>", "file:///")

    def encode_file(self, file_path):
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')


class EpubReader(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.tts")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        file_path = None
        if len(sys.argv) > 1:
            file_path = sys.argv[1]
        win = EpubViewerWindow(application=app, file_path=file_path)
        win.present()

def main():
    app = EpubReader()
    return app.run(sys.argv)

if __name__ == "__main__":
    main()
