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

    def decode_dsl_bytes(self, raw: bytes) -> str:
        """Robustly detect and decode DSL file encoding."""
        # Byte Order Marks (BOM)
        if raw.startswith(b"\xef\xbb\xbf"):
            text = raw[3:].decode("utf-8", errors="ignore")
        elif raw.startswith(b"\xff\xfe"):
            text = raw.decode("utf-16-le", errors="ignore")
        elif raw.startswith(b"\xfe\xff"):
            text = raw.decode("utf-16-be", errors="ignore")
        else:
            # Try UTF-8 first (most common)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Try UTF-16 (some DSLs omit BOM)
                try:
                    text = raw.decode("utf-16-le")
                except UnicodeDecodeError:
                    # Fallback to Windows encodings used for Arabic/Russian DSLs
                    for enc in ("cp1251", "cp1256", "latin1"):
                        try:
                            text = raw.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        # As last resort, decode ignoring errors
                        text = raw.decode("utf-8", errors="ignore")

        # Clean up nulls and extra BOM characters
        text = text.replace("\x00", "")
        return text


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
        # Debug: show first few entries
        for i, (word, defs) in enumerate(list(entries.items())[:3]):
            print(f"  '{word}' -> {len(defs)} definitions")
        return True


    def _parse_dsl(self, content):
        entries, headwords, defs = {}, [], []
        in_def = False

        def flush():
            if headwords and defs:
                for w in headwords:
                    if w.strip():  # Only add non-empty headwords
                        entries.setdefault(w, []).extend(defs)

        for raw in content.splitlines():
            line = raw.rstrip()
            
            # Skip empty lines and all header lines (starting with #)
            if not line or line.startswith("#"):
                continue
                
            # Entry separator
            if line == "-":
                flush()
                headwords, defs, in_def = [], [], False
                continue
                
            # Definition line (starts with whitespace)
            if raw and raw[0] in (' ', '\t'):
                in_def = True
                cleaned = raw.lstrip()
                if cleaned:
                    defs.append(cleaned)
                continue
                
            # Non-indented line after definitions = new entry
            if in_def:
                flush()
                headwords, defs, in_def = [], [], False
                
            # Accumulate headwords (non-indented, non-header lines)
            if line.strip():  # Only add non-empty lines
                headwords.append(line)
                
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
        """Called when theme changes – recolor spans live without reload."""
        dark = self.style_manager.get_dark()
        
        # Build the updated theme CSS with actual colors
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"
        
        theme_css = f"""
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
        .sub-item {{ margin-left: 0; margin-top: 0.3em; line-height: 1.4; }}
        .standalone {{ margin: 0.3em 0; line-height: 1.4; font-weight: 500; }}
        hr {{ border: none; border-top: 1px solid {border}; margin: 10px 0; }}
        """
        
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
        
        theme_css = f"""
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
        .sub-item {{ margin-left: 0; margin-top: 0.3em; line-height: 1.4; }}
        .standalone {{ margin: 0.3em 0; line-height: 1.4; font-weight: 500; }}
        hr {{ border: none; border-top: 1px solid {border}; margin: 10px 0; }}
        """

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
        
        theme_css = f"""
        body {{
            font-family: system-ui, sans-serif;
            background-color: {bg};
            color: {fg};
            margin: 12px;
        }}
        """

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
                defs_html = ""
                in_list = False
                current_li_content = []

                for i, d in enumerate(defs):
                    d_stripped = d.strip()

                    # Identify DSL level: [m1], [m2], ...
                    lvl_match = re.match(r'^\[m(\d+)\]', d_stripped)
                    lvl = int(lvl_match.group(1)) if lvl_match else None

                    # Detect "separator" like ——— (after removing [mX]...[/m])
                    sep_check = re.sub(r'^\[m\d+\](.*?)\[/m\]$', r'\1', d_stripped, flags=re.DOTALL).strip()
                    is_separator = bool(re.fullmatch(r'[—\-]{3,}', sep_check))

                    # New top-level header or separator closes any open list
                    if lvl == 1 or is_separator:
                        if current_li_content:
                            defs_html += "<li>" + "".join(current_li_content) + "</li>"
                            current_li_content = []
                        if in_list:
                            defs_html += "</ol>"
                            in_list = False

                        if not is_separator:
                            defs_html += f"<div class='standalone'>{self.render_dsl_text(d, headword=word)}</div>"
                        else:
                            defs_html += "<hr>"
                        continue

                    # Numbering detection inside [mX] ... [/m]
                    m_content = re.search(r'\[m\d+\](.*?)\[/m\]', d_stripped, re.DOTALL)
                    if m_content:
                        inner = m_content.group(1).strip()
                        is_numbered_m = (
                            re.match(r'^\d+[\.\)》]\s', inner) or
                            re.match(r'^\[([biu])\]\d+[\.\)》]\[/\1\]\s*', inner) or
                            ('■ ' in inner[:10])
                        )
                    else:
                        is_numbered_m = False

                    is_numbered = bool(re.match(r'^\d+[\.\)》]\s', d_stripped))

                    if is_numbered_m or is_numbered:
                        if current_li_content:
                            defs_html += "<li>" + "".join(current_li_content) + "</li>"
                            current_li_content = []
                        if not in_list:
                            defs_html += "<ol>"
                            in_list = True
                        current_li_content.append(self.render_dsl_text(d, is_main=True, headword=word))
                    else:
                        if in_list:
                            current_li_content.append(
                                f"<div class='sub-item'>{self.render_dsl_text(d, headword=word)}</div>"
                            )
                        else:
                            defs_html += f"<div class='standalone'>{self.render_dsl_text(d, headword=word)}</div>"

                # Close any remaining list item / list
                if current_li_content:
                    defs_html += "<li>" + "".join(current_li_content) + "</li>"
                if in_list:
                    defs_html += "</ol>"

                clean_word = self._unescape_dsl_text(word)
                block = f"""
                <div class="entry">
                  <div class="header">
                    <span class="lemma">{clean_word}</span>
                    <span class="dict">📖 {dname}</span>
                  </div>
                  {defs_html}
                  <hr>
                </div>
                """
                body += block

        # Theme CSS (same variables as _apply_theme_to_webview / show_placeholder)
        dark = self.style_manager.get_dark()
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"
        
        theme_css = f"""
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
        .sub-item {{ margin-left: 0; margin-top: 0.3em; line-height: 1.4; }}
        .standalone {{ margin: 0.3em 0; line-height: 1.4; font-weight: 500; }}
        hr {{ border: none; border-top: 1px solid {border}; margin: 10px 0; }}
        """

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
        
        # Handle links <<word>> - use custom dict:// URI scheme
        t = re.sub(r"<<(.*?)>>", r'<a href="dict://\1" class="dict-link">\1</a>', t)
        # Ensure [ref] also works like << >> (done earlier, but redundant safety)
        t = re.sub(r"\[ref\](.*?)\[/ref\]", r'<a href="dict://\1" class="dict-link">\1</a>', t)
        
        # Handle arrows
        t = t.replace('→', '→')
        
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
