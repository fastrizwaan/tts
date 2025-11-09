#!/usr/bin/env python3
import gi, os, re, gzip, json
from pathlib import Path
import colorsys

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk, WebKit

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

APP_NAME = "DSL Dictionary"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# ------------------------------------------------------------
# DICTIONARY MANAGER
# ------------------------------------------------------------

class DictionaryManager:
    def __init__(self):
        self.dictionaries = {}
        self.entries = {}

    def decode_dsl_bytes(self, raw):
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw[3:].decode("utf-8", errors="ignore")
        if raw.startswith(b"\xff\xfe"):
            return raw.decode("utf-16-le", errors="ignore")
        if raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16-be", errors="ignore")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")

    def load_dictionary(self, path, color="default"):
        path = Path(path)
        if not path.exists():
            return False
        try:
            raw = gzip.open(path, "rb").read() if path.suffix == ".dz" else open(path, "rb").read()
            content = self.decode_dsl_bytes(raw)
        except Exception as e:
            print("Decode error:", e)
            return False

        dict_name = path.stem
        for line in content.splitlines()[:20]:
            if line.startswith("#NAME"):
                m = re.search(r'#NAME\s+"([^"]+)"', line)
                if m:
                    dict_name = m.group(1)
                break

        entries = self._parse_dsl(content)
        self.dictionaries[str(path)] = {"name": dict_name, "entries": entries, "color": color}
        for w, defs in entries.items():
            lw = w.lower()
            self.entries.setdefault(lw, []).append((w, dict_name, defs, color))
        print(f"Loaded {len(entries)} entries from {dict_name}")
        return True

    def _parse_dsl(self, content):
        """
        DSL parser that supports:
        - Multiple headwords per entry (Cambridge-style)
        - Tag-based structures [m0]-[m9]
        - Indentation-based legacy formats
        """
        entries = {}
        headwords = []
        defs = []

        def flush():
            if headwords and defs:
                clean_defs = [d for d in defs if d.strip()]
                if clean_defs:
                    for w in headwords:
                        entries.setdefault(w.strip(), []).extend(clean_defs)

        for raw in content.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue

            # --- Headword line: no indent, no [mX] tag ---
            if not raw[:1].isspace() and not re.match(r"^\[m\d+\]", line):
                # Flush previous entry before starting new group
                if defs:
                    flush()
                    defs = []

                # Either a continuation of headwords or a fresh one
                if headwords and not defs:
                    # If previous entry had only headwords (Cambridge multiword forms)
                    headwords.append(line.strip())
                else:
                    headwords = [line.strip()]
                continue

            # --- Structured tag-based definition ([m0]-[m9]) ---
            if re.match(r"^\[m\d+\]", line):
                defs.append(line.strip())
                continue

            # --- Indentation-based definition (WordBook-style) ---
            if raw[:1].isspace():
                defs.append(line.strip())
                continue

        flush()
        return entries




    def search(self, q):
        q = q.lower().strip()
        if not q:
            return []
        res = {}
        for lw, lst in self.entries.items():
            if q in lw:
                for orig, dn, defs, color in lst:
                    res.setdefault(orig, []).append((dn, defs, color))
        def order(x):
            w = x[0].lower()
            if w == q:
                return (0, w)
            if w.startswith(q):
                return (1, w)
            return (2, w)
        return sorted(res.items(), key=order)


# ------------------------------------------------------------
# MAIN WINDOW
# ------------------------------------------------------------

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(900, 600)
        self.dict_manager = DictionaryManager()

        # Initialize style manager first
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_theme_changed)

        self._build_ui()
        self._apply_theme_to_webview()
        self._load_settings()

    def _build_theme_css(self):
        dark = self.style_manager.get_dark()
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"

        return f"""
        body {{
            font-family: system-ui, sans-serif;
            background-color: {bg};
            color: {fg};
            margin: 12px;
        }}
        .lemma {{ font-size: 1.3em; font-weight: bold; color: {link}; }}
        .dict {{ float: right; font-size: 0.9em; color: #888; }}
        .pos {{ color: {pos}; font-style: italic; }}
        .example {{ color: {example}; font-style: italic; }}
        .dict-link {{ color: {link}; text-decoration: none; cursor: pointer; }}
        .dict-link:hover {{ text-decoration: underline; }}
        ol {{ margin: 0.5em 0; padding-left: 2em; line-height: 1.6; list-style-position: outside; }}
        li {{ margin-bottom: 0.8em; }}
        .sub-item {{
            margin-left: 1.5em;
            color: {example};
            font-style: italic;
            line-height: 1.4;
        }}
        .standalone {{ margin: 0.3em 0; line-height: 1.4; font-weight: 500; }}
        hr {{ border: none; border-top: 1px solid {border}; margin: 10px 0; }}
        .def-text {{
            color: {fg};
            font-weight: 500;
            font-style: normal;

        }}
        """

    def lighten(self, color: str, factor: float = 1.5) -> str:
        """Brighten a CSS color name or hex color safely for dark mode."""

        # Normalize color name → hex using Gdk.RGBA if available
        rgba = Gdk.RGBA()
        try:
            if rgba.parse(color):  # valid color name or hex
                r, g, b = rgba.red, rgba.green, rgba.blue
            else:
                raise ValueError
        except Exception:
            # Fallback if parse() fails (e.g., before Gtk.init)
            color = color.lstrip("#")
            if len(color) == 3:
                color = "".join([c * 2 for c in color])  # expand #abc → #aabbcc
            # Clamp to a default mid-gray if parsing fails
            if len(color) != 6 or not all(c in "0123456789abcdefABCDEF" for c in color):
                color = "888888"
            r = int(color[0:2], 16) / 255.0
            g = int(color[2:4], 16) / 255.0
            b = int(color[4:6], 16) / 255.0

        # Convert to HLS and brighten the lightness
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        l = min(1.0, l * factor)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

        
    def on_theme_changed(self, *_):
        """Called when theme changes — recolor spans live without reload."""
        dark = self.style_manager.get_dark()
        
        # Build the updated theme CSS with actual colors
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"
        
        theme_css = self._build_theme_css()

        
        # Combined script: update theme CSS AND recolor spans in one go
        js = f"""
        (function() {{
            const dark = {'true' if dark else 'false'};
            const factor = 1.5;
            
            // Update theme CSS
            let style = document.getElementById('theme-style');
            if (!style) {{
                style = document.createElement('style');
                style.id = 'theme-style';
                document.head.appendChild(style);
            }}
            style.innerHTML = `{theme_css}`;
            document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
            document.body.style.backgroundColor = '{bg}';
            document.body.style.color = '{fg}';
            
            // Lighten function
            function lighten(hex, factor) {{
                if (!hex.startsWith('#')) return hex;
                let r = parseInt(hex.substr(1,2),16),
                    g = parseInt(hex.substr(3,2),16),
                    b = parseInt(hex.substr(5,2),16);
                let l = (Math.max(r,g,b)+Math.min(r,g,b))/2/255;
                l = Math.min(1, l * factor);
                const scale = l / ((Math.max(r,g,b)+Math.min(r,g,b))/2/255 || 1);
                r = Math.min(255, r * scale);
                g = Math.min(255, g * scale);
                b = Math.min(255, b * scale);
                return '#' + [r,g,b].map(x => Math.round(x).toString(16).padStart(2,'0')).join('');
            }}
            
            // Recolor custom colored spans
            document.querySelectorAll('span[data-orig-color]').forEach(span => {{
                const orig = span.getAttribute('data-orig-color');
                span.style.color = dark ? lighten(orig, factor) : orig;
            }});
        }})();
        """
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)



    def _apply_theme_to_webview(self):
        """Apply theme to WebView and update theme CSS for dynamic dark/light switching."""
        dark = self.style_manager.get_dark()
        settings = self.webview.get_settings()
        try:
            settings.set_property("enable-dark-mode", dark)
        except TypeError:
            pass

        # Build theme CSS with actual color values (no CSS variables)
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"
        
        theme_css = self._build_theme_css()


        # Inject live theme update into the webview
        script = f"""
        (function() {{
            let style = document.getElementById('theme-style');
            if (!style) {{
                style = document.createElement('style');
                style.id = 'theme-style';
                document.head.appendChild(style);
            }}
            style.innerHTML = `{theme_css}`;
            document.documentElement.style.colorScheme = '{'dark' if dark else 'light'}';
            document.body.style.backgroundColor = '{bg}';
            document.body.style.color = '{fg}';
        }})();
        """
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None)




    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(vbox)

        header = Adw.HeaderBar()
        vbox.append(header)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search dictionary…")
        self.search_entry.connect("search-changed", self.on_search)
        header.set_title_widget(self.search_entry)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.connect("clicked", self.on_settings)
        header.pack_end(settings_btn)

        # Single WebView replaces the listbox
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.set_can_focus(False)
        
        # Connect to decide-policy signal to intercept navigation
        self.webview.connect("decide-policy", self.on_decide_policy)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(self.webview)
        vbox.append(scrolled)

        self.show_placeholder("Load a dictionary to start searching")

        
    def show_placeholder(self, text):
        dark = self.style_manager.get_dark()
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        
        theme_css = self._build_theme_css()


        html = f"""
        <html>
        <head>
        <meta charset='utf-8'>
        <meta name='color-scheme' content='{'dark' if dark else 'light'}'>
        <style id='theme-style'>{theme_css}</style>
        </head>
        <body><p>{text}</p></body>
        </html>
        """
        self.webview.load_html(html, "file:///")
        GLib.idle_add(self._apply_theme_to_webview)



    def on_decide_policy(self, webview, decision, decision_type):
        """Intercept link clicks and handle dict:// URIs"""
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            request = nav_action.get_request()
            uri = request.get_uri()
            
            if uri.startswith("dict://"):
                # Extract the word from dict://word
                word = uri[7:]  # Remove "dict://"
                decision.ignore()
                
                # Perform search
                self.search_entry.set_text(word)
                GLib.idle_add(lambda: self.perform_search(word))
                return True
        
        return False

    # ------------------- Search handler ----------------------

    def perform_search(self, q):
        """Perform the actual search logic"""
        q = q.strip()
        if not q:
            self.show_placeholder("Enter a search term" if self.dict_manager.entries else "Load a dictionary first")
            return

        results = self.dict_manager.search(q)
        if not results:
            self.show_placeholder("No results found")
            return

        # Cache the raw search results for theme toggling
        self._last_raw_results = results

        html = self.build_html(results)
        self.webview.load_html(html, "file:///")



    def on_search(self, entry):
        q = entry.get_text()
        self.perform_search(q)


    def _get_base_css(self):
        """Unified base CSS used by all HTML renderers."""
        return """
        :root {
            --bg: VAR_BG;
            --fg: VAR_FG;
            --link: VAR_LINK;
            --border: VAR_BORDER;
            --pos: VAR_POS;
            --example: VAR_EXAMPLE;
        }
        body {
            font-family: system-ui, sans-serif;
            background-color: var(--bg);
            color: var(--fg);
            margin: 12px;
        }
        .lemma { font-size: 1.3em; font-weight: bold; color: var(--link); }
        .dict { float: right; font-size: 0.9em; color: #888; }
        .pos { color: var(--pos); font-style: italic; }
        .example { color: var(--example); font-style: italic; }
        .dict-link { color: var(--link); text-decoration: none; cursor: pointer; }
        .dict-link:hover { text-decoration: underline; }
        ol { margin: 0.5em 0; padding-left: 2em; line-height: 1.6; list-style-position: outside; }
        li { margin-bottom: 0.8em; }
        .sub-item { margin-left: 0; margin-top: 0.3em; line-height: 1.4; }
        .standalone { margin: 0.3em 0; line-height: 1.4; font-weight: 500; }
        hr { border: none; border-top: 1px solid var(--border); margin: 10px 0; }
        """

    # ------------------- HTML rendering ----------------------

    def build_html(self, results):
        body = ""
        for word, dict_data in results[:100]:
            for dname, defs, color in dict_data:
                sections = []  # list of (m0_title, [(pos, [defs])])
                current_section = None
                current_pos = None
                current_defs = []

                def flush_pos():
                    nonlocal current_pos, current_defs, current_section
                    if current_pos and current_defs:
                        if not current_section:
                            current_section = ("", [])
                        current_section[1].append((current_pos, current_defs))
                        current_pos, current_defs = None, []

                def flush_section():
                    nonlocal current_section
                    if current_section:
                        sections.append(current_section)
                        current_section = None

                for line in defs:
                    line = line.strip()
                    if not line:
                        continue

                    # detect single-tag form [mX]... (Cambridge)
                    m_tag = re.match(r'\[(m\d+)\](.*)', line)
                    if m_tag:
                        tag, content = m_tag.groups()
                        content = content.strip()

                        # [m0] — section header (USE VEHICLE, FORCE, etc.)
                        if tag == "m0":
                            flush_pos()
                            flush_section()
                            title = self.render_dsl_text(content)
                            current_section = (title, [])
                            continue

                        # [m1] — new sense or POS
                        if tag == "m1":
                            flush_pos()
                            current_pos = self.render_dsl_text(content)
                            current_defs = []
                            # Detect whether this is a numbered sense or a phrase heading
                            self._in_numbered_sense = bool(re.match(r'^\s*\d+[\.\)]', content))
                            continue


                        # [m2] — standard meaning
                        if tag == "m2":
                            html = self.render_dsl_text(content, is_main=self._in_numbered_sense, headword=word)
                            # If it's a phrase/idiom (non-numbered), mark it standalone
                            if not self._in_numbered_sense:
                                html = f"<div class='standalone'>{html}</div>"
                            current_defs.append(html)
                            continue


                        # [m3] — example or subexample
                        if tag == "m3":
                            current_defs.append(f"<div class='sub-item'>{self.render_dsl_text(content, headword=word)}</div>")
                            continue

                        # [m4] — extra example
                        if tag == "m4":
                            current_defs.append(f"<div class='sub-item'>{self.render_dsl_text(content, headword=word)}</div>")
                            continue

                        # fallback
                        current_defs.append(self.render_dsl_text(content, headword=word))
                        continue

                    # lines without any [mX] — fallback (still allow inlined definitions)
                    if current_pos:
                        current_defs.append(self.render_dsl_text(line, headword=word))


            flush_pos()
            flush_section()

            # now render all sections
            defs_html = ""
            for title, pos_blocks in sections:
                if title:
                    defs_html += f"<div class='m0-title'><b>{title}</b></div>"
                for pos, items in pos_blocks:
                    defs_html += f"<div class='pos-block'><div class='pos'>{pos}</div><ol>"
                    for item in items:
                        # Examples are already <div class='sub-item'>
                        if "<div class='sub-item'>" in item:
                            defs_html += f"{item}"
                        else:
                            defs_html += f"<li>{item}</li>"
                    defs_html += "</ol></div>"


            clean_word = self._unescape_dsl_text(word)
            body += f"""
            <div class="entry">
              <div class="header">
                <span class="lemma">{clean_word}</span>
                <span class="dict">📖 {dname}</span>
              </div>
              {defs_html}
              <hr>
            </div>
            """


        # Theme CSS (same variables as _apply_theme_to_webview / show_placeholder)
        dark = self.style_manager.get_dark()
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"
        
        theme_css = self._build_theme_css()


        # Cache the final rendered HTML so it can be reused by on_theme_changed()
        self._last_html = f"""
        <html>
        <head>
          <meta charset='utf-8'>
          <meta name='color-scheme' content='{'dark' if dark else 'light'}'>
          <style id='theme-style'>{theme_css}</style>
        </head>
        <body>{body}</body>
        </html>
        """
        return self._last_html





    def _unescape_dsl_text(self, text: str) -> str:
        """Unescape DSL literal escapes like \\(, \\), \\[, \\] for display."""
        return (text
                .replace(r'\(', '(')
                .replace(r'\)', ')')
                .replace(r'\[', '[')
                .replace(r'\]', ']'))

    def render_dsl_text(self, raw, is_main=False, headword=""):
        t = raw
        
        # Replace tilde with headword
        if headword:
            t = t.replace('~', headword)
        
        # Handle escaped characters first
        t = t.replace(r'\(', '(')
        t = t.replace(r'\)', ')')
        t = t.replace(r'\[', '[')
        t = t.replace(r'\]', ']')
        
        # Handle [m1], [m2], [m3] tags and extract content
        t = re.sub(r'\[m\d+\](.*?)\[/m\]', r'\1', t, flags=re.DOTALL)
        
        # Remove list markers if it's a main definition (we'll add it via <li>)
        # Patterns: "1." or "1)" or "1》" or "[b]1.[/b]" or "■"
        if is_main:
            t = re.sub(r'^\d+[\.\)》]\s+', '', t)
            t = re.sub(r'^\[([biu])\](\d+[\.\)》])\[/\1\]\s*', '', t)
            t = re.sub(r'^■\s*', '', t)
        
        # Handle [*] markers for examples/bullets
        t = re.sub(r'\[\*\](.*?)\[/\*\]', r'<span class="bullet-item">\1</span>', t, flags=re.DOTALL)
        
        # Handle formatting tags
        t = re.sub(r"\[b\](.*?)\[/b\]", r"<b>\1</b>", t, flags=re.DOTALL)
        t = re.sub(r"\[i\](.*?)\[/i\]", r"<i>\1</i>", t, flags=re.DOTALL)
        t = re.sub(r"\[u\](.*?)\[/u\]", r"<u>\1</u>", t, flags=re.DOTALL)
        t = re.sub(r"\[sup\](.*?)\[/sup\]", r"<sup>\1</sup>", t, flags=re.DOTALL)
        t = re.sub(r"\[sub\](.*?)\[/sub\]", r"<sub>\1</sub>", t, flags=re.DOTALL)
        
        # Handle special tags
        t = re.sub(r"\[p\](.*?)\[/p\]", r'<span class="pos">\1</span>', t, flags=re.DOTALL)
        t = re.sub(r"\[ex\](.*?)\[/ex\]", r'<span class="example">\1</span>', t, flags=re.DOTALL)
        t = re.sub(r"\[trn\](.*?)\[/trn\]", r'\1', t, flags=re.DOTALL)
        t = re.sub(r"\[com\](.*?)\[/com\]", r'\1', t, flags=re.DOTALL)
        t = re.sub(r"\[ref\](.*?)\[/ref\]", r'<a href="dict://\1" class="dict-link">\1</a>', t, flags=re.DOTALL)


        # Handle color tags [c color]text[/c]
        def apply_color_tag(match):
            color = match.group(1).strip()
            text = match.group(2)

            # Normalize color names → hex using Gdk.RGBA
            rgba = Gdk.RGBA()
            if rgba.parse(color):
                color = "#{:02x}{:02x}{:02x}".format(
                    int(rgba.red * 255),
                    int(rgba.green * 255),
                    int(rgba.blue * 255)
                )

            orig_color = color
            if self.style_manager.get_dark():
                color = self.lighten(color, 1.5)
            return f'<span style="color:{color}" data-orig-color="{orig_color}">{text}</span>'



        t = re.sub(r"\[c\s+([^\]]+)\](.*?)\[/c\]", apply_color_tag, t, flags=re.DOTALL)
        
        # Handle dictionary cross-links <<word>> but skip {{...}} definitions
        t = re.sub(r"(?<!\{)\<\<([^\>]+)\>\>(?!\})", r'<a href="dict://\1" class="dict-link">\1</a>', t)

  
          # ---------------- Cambridge-style placeholders ----------------

        # Handle {{def}}...{{/def}} → plain text in normal color
        t = re.sub(
            r"\{\{def\}\}(.*?)\{\{\/def\}\}",
            r"<span class='def-text'>\1</span>",
            t,
            flags=re.DOTALL
        )



        # Handle {{phrase}}...{{/phrase}} → bold for phrase names
        t = re.sub(r"\{\{phrase\}\}(.*?)\{\{\/phrase\}\}", r"<b>\1</b>", t, flags=re.DOTALL)

        # Handle {{pos}}...{{/pos}} → same as <span class='pos'>
        t = re.sub(r"\{\{pos\}\}(.*?)\{\{\/pos\}\}", r"<span class='pos'>\1</span>", t, flags=re.DOTALL)

        # Handle {{inf}}...{{/inf}} → italic (inflected forms)
        t = re.sub(r"\{\{inf\}\}(.*?)\{\{\/inf\}\}", r"<i>\1</i>", t, flags=re.DOTALL)

        # Handle {{usage}}...{{/usage}} and {{region}}...{{/region}}
        t = re.sub(r"\{\{usage\}\}(.*?)\{\{\/usage\}\}", r"<span class='example'>\1</span>", t, flags=re.DOTALL)
        t = re.sub(r"\{\{region\}\}(.*?)\{\{\/region\}\}", r"<span class='example'>\1</span>", t, flags=re.DOTALL)

        # Remove any remaining unused Cambridge placeholders ({{xxx}} or {{/xxx}})
        t = re.sub(r"\{\{/?[a-zA-Z0-9_]+\}\}", "", t)

        # Convert [s]soundfile.wav[/s] → small speaker icon
        t = re.sub(r"\[s\](.*?)\[/s\]", r"<span title='\1'>🔊</span>", t)

        # Handle Cambridge-style <?word> → <a href="dict://word">
        t = re.sub(r"<<\?(.*?)>>", r'<a href="dict://\1" class="dict-link">\\1</a>', t)


        # Ensure [ref] also works like << >> (done earlier, but redundant safety)
        t = re.sub(r"\[ref\](.*?)\[/ref\]", r'<a href="dict://\1" class="dict-link">\1</a>', t)
      
        # Handle arrows
        t = t.replace('↑', '↑')

        
        return t

    # ------------------- Settings ----------------------

    def on_settings(self, *_):
        dlg = SettingsDialog(self, self.dict_manager.dictionaries.keys())
        dlg.present()

    def _load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            cfg = json.load(open(CONFIG_FILE))
            for d in cfg.get("dictionaries", []):
                p = d["path"]
                c = d.get("color", "default")
                if os.path.exists(p):
                    self.dict_manager.load_dictionary(p, c)
        except Exception as e:
            print("Settings load error:", e)

    def save_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        arr = [{"path": p, "color": info["color"]}
               for p, info in self.dict_manager.dictionaries.items()]
        json.dump({"dictionaries": arr}, open(CONFIG_FILE, "w"))


# ------------------------------------------------------------
# SETTINGS DIALOG
# ------------------------------------------------------------

class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent, paths):
        super().__init__(transient_for=parent, modal=True, title="Settings")
        self.parent = parent
        page = Adw.PreferencesPage()
        self.add(page)
        group = Adw.PreferencesGroup(title="Dictionaries")
        page.add(group)

        add_row = Adw.ActionRow(title="Add Dictionary")
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.connect("clicked", self.add_dictionary)
        add_row.add_suffix(add_button)
        group.add(add_row)

        for path in paths:
            info = parent.dict_manager.dictionaries[path]
            row = Adw.ActionRow(
                title=info["name"],
                subtitle=f"{len(info['entries'])} entries • {os.path.basename(path)}"
            )
            dd = Gtk.DropDown.new_from_strings(["Default"])
            row.add_suffix(dd)
            rm = Gtk.Button(icon_name="user-trash-symbolic")
            rm.add_css_class("destructive-action")
            rm.connect("clicked", self.remove_dictionary, path)
            row.add_suffix(rm)
            group.add(row)

    def add_dictionary(self, *_):
        dlg = Gtk.FileDialog()
        dlg.open(self, None, self._add_done)

    def _add_done(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            path = f.get_path()
            if self.parent.dict_manager.load_dictionary(path):
                self.parent.save_settings()
                self.close()
                SettingsDialog(self.parent, self.parent.dict_manager.dictionaries.keys()).present()
        except GLib.Error:
            pass

    def remove_dictionary(self, *_args):
        path = _args[-1]
        self.parent.dict_manager.dictionaries.pop(path, None)
        self.parent.save_settings()
        self.close()
        if self.parent.dict_manager.dictionaries:
            SettingsDialog(self.parent, self.parent.dict_manager.dictionaries.keys()).present()


# ------------------------------------------------------------
# APPLICATION
# ------------------------------------------------------------

class DictionaryApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.DSLDictionary")
        GLib.set_application_name(APP_NAME)

    def do_activate(self):
        win = self.props.active_window or MainWindow(self)
        win.present()


def main():
    app = DictionaryApp()
    return app.run(None)


if __name__ == "__main__":
    main()
