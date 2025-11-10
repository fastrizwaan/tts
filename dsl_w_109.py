#!/usr/bin/env python3
import gi, os, re, gzip, json, colorsys, html
from pathlib import Path
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, WebKit

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

APP_NAME = "DSL Dictionary"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# ============================================================
#  DSL PARSER
# ============================================================

class DSLParser:
    """Parse .dsl files into a structured dictionary."""
    def __init__(self, text):
        self.text = text

    def parse(self):
        entries, headwords, defs = {}, [], []
        in_def = False

        def flush():
            if headwords and defs:
                for w in headwords:
                    if w.strip():
                        entries.setdefault(w, []).extend(defs)

        for raw in self.text.splitlines():
            line = raw.rstrip("\n\r")
            if not line or line.startswith("#"):
                continue
            if line == "-":
                flush()
                headwords, defs, in_def = [], [], False
                continue
            if raw and raw[0] in (" ", "\t"):
                in_def = True
                defs.append(line)
                continue
            if in_def:
                flush()
                headwords, defs, in_def = [], [], False
            headwords.append(line)
        flush()
        return entries


# ============================================================
#  DSL RENDERER
# ============================================================

class DSLRenderer:
    """GoldenDict-style HTML renderer for DSL lines (fully featured, safe, dark-mode aware)."""

    TAG_MAP = {
        "b":        ("<b>", "</b>"),
        "i":        ("<i>", "</i>"),
        "u":        ("<u>", "</u>"),
        "sup":      ("<sup>", "</sup>"),
        "sub":      ("<sub>", "</sub>"),
        "ex":       ("<span class='example'>", "</span>"),
        "trn":      ("<span class='translation'>", "</span>"),
        "com":      ("<span class='comment'>", "</span>"),
        "p":        ("<span class='pos'>", "</span>"),
        "s":        ("<span class='media'>", "</span>"),
        "star":     ("<span class='full-translation' style='display:none'>", "</span>"),
        "m":        ("<div class='m-line'>", "</div>"),
    }

    def __init__(self, dark_mode=False):
        self.dark_mode = dark_mode

    # ------------------------------------------------------------
    # Color/lighten helper
    # ------------------------------------------------------------
    def _lighten(self, color: str, factor: float = 1.3) -> str:
        rgba = Gdk.RGBA()
        if rgba.parse(color):
            r, g, b = rgba.red, rgba.green, rgba.blue
        else:
            color = color.lstrip("#")
            if len(color) == 3:
                color = "".join([c * 2 for c in color])
            r = int(color[0:2], 16) / 255.0
            g = int(color[2:4], 16) / 255.0
            b = int(color[4:6], 16) / 255.0
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        l = min(1.0, l * factor)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

    # ------------------------------------------------------------
    # Tag open/close logic
    # ------------------------------------------------------------
    def _open_tag(self, tag: str, param: str = None) -> str:
        # [m#]
        if tag.startswith("m") and len(tag) > 1 and tag[1:].isdigit():
            level = int(tag[1:])
            return f"<div class='m-line' style='margin-left:{0.8 * level}em'>"

        # [c color]
        elif tag == "c":
            color = (param or "inherit").strip()
            rgba = Gdk.RGBA()

            # --- Replace too-dark named colors with lighter equivalents (dark mode) ---
            if self.dark_mode and color:
                pname = color.lower().strip()
                lighten_map = {
                    "navy": "royalblue",
                    "darkblue": "dodgerblue",
                    "mediumblue": "dodgerblue",
                    "midnightblue": "cornflowerblue",
                    "indigo": "mediumslateblue",
                    "darkviolet": "orchid",
                    "blueviolet": "mediumpurple",
                    "darkmagenta": "violet",
                    "darkred": "tomato",
                    "maroon": "indianred",
                    "darkgreen": "seagreen",
                    "darkolivegreen": "yellowgreen",
                    "darkslategray": "cadetblue",
                    "darkslateblue": "slateblue",
                    "purple": "mediumorchid",
                    "azure": "deepskyblue",
                    "aliceblue": "steelblue",
                }
                if pname in lighten_map:
                    color = lighten_map[pname]

            # --- Replace too-light named colors with darker equivalents (light mode) ---
            elif not self.dark_mode and color:
                pname = color.lower().strip()
                darken_map = {
                    "aliceblue": "steelblue",
                    "antiquewhite": "peru",
                    "azure": "deepskyblue",
                    "beige": "saddlebrown",
                    "ghostwhite": "slategray",
                    "ivory": "darkkhaki",
                    "lightgray": "gray",
                    "lightyellow": "goldenrod",
                    "palegreen": "seagreen",
                    "white": "gray",
                    "yellow": "darkgoldenrod",
                    "aqua": "teal",
                    "aquamarine": "mediumseagreen",
                    "lime": "forestgreen",
                }
                if pname in darken_map:
                    color = darken_map[pname]

            # --- Convert to hex for consistent output ---
            if rgba.parse(color):
                color = "#{:02x}{:02x}{:02x}".format(
                    int(rgba.red * 255),
                    int(rgba.green * 255),
                    int(rgba.blue * 255)
                )

            # --- Minor tone balancing ---
            if param:
                rgba.parse(color)
                r, g, b = rgba.red, rgba.green, rgba.blue
                luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
                factor = 1.0

                if self.dark_mode and luminance < 0.25:
                    factor = 1.5
                elif not self.dark_mode and luminance > 0.9:
                    factor = 0.8

                if factor != 1.0:
                    color = self._lighten(color, factor)

            return f"<span style='color:{color}'>"






        # [lang name="English"]
        elif tag.startswith("lang"):
            m = re.search(r'name\s*=\s*"([^"]+)"', param or "")
            lang_name = m.group(1) if m else (param or "")
            return f"<span class='lang' data-lang='{html.escape(lang_name)}'>"

        # <<link>>
        elif tag == "<<":
            return "<a class='dict-link' href='dict://"

        elif tag == ">>":
            return "'>"

        elif tag in self.TAG_MAP:
            return self.TAG_MAP[tag][0]

        else:
            return f"<span class='{html.escape(tag)}'>"

    def _close_tag(self, tag: str) -> str:
        if tag.startswith("m") and tag[1:].isdigit():
            return "</div>"
        elif tag in {"c", "lang"}:
            return "</span>"
        elif tag in {"<<", ">>"}:
            return "</a>"
        elif tag in self.TAG_MAP:
            return self.TAG_MAP[tag][1]
        else:
            return "</span>"

    # ------------------------------------------------------------
    # Stateful parser
    # ------------------------------------------------------------
    def _render_line(self, line: str, headword: str) -> str:
        """Parse one DSL definition line into HTML."""
        # --- Strip ABBYY Lingvo / Cambridge DSL macros ---
        line = re.sub(
            r"\{\{/?(?:"
            r"b|c|i|v|w|text|title|type|category|"
            r"def|sense_t|sense_b|inf|phrase|usage|region|"
            r"pos|posgram|posblock_h|Main entry|Derived|"
            r"Another wordform|lab|gl|xtext|xref|xeg|sp|sm|"
            r"phrasal_verb_h|idiom_h|gwblock_h|gwblock|runon_h|"
            r"clepan|collpan|upan|obj|var|infgrp|infl|picrefs|"
            r"Phrasal Verb|See also"
            r")\}\}",
            "",
            line,
            flags=re.IGNORECASE,
        )

        # Fallback: remove stray unknown {{...}} blocks
        line = re.sub(r"\{\{[^{}]+\}\}", "", line)

        #line = re.sub(r"\{\{[^{}]+\}\}", "", line)

        line = re.sub(r"\{\{/?(?:region|lab|head|pos|posgram|inf|gwblock_[a-z])\}\}", "", line, flags=re.I)

        tokens = re.split(r"(\[/?[a-zA-Z0-9\s=:_#*-]+\]|<<|>>)", line)

        stack = []
        html_fragments = []

        # --- Tag handlers ---
        def push(tagtoken: str):
            content = tagtoken[1:-1].strip()
            if not content:
                html_fragments.append(html.escape(tagtoken))
                return
            parts = content.split(None, 1)
            if not parts:
                html_fragments.append(html.escape(tagtoken))
                return
            name = parts[0]
            param = parts[1] if len(parts) > 1 else None
            stack.append(name)
            html_fragments.append(self._open_tag(name, param))

        def pop(tagtoken: str):
            if not stack:
                return
            top = stack.pop()
            html_fragments.append(self._close_tag(top))

        # --- Text handler ---
        def process_text(txt: str):
            if not txt:
                return
            txt_esc = html.escape(txt, quote=False)


            # POS markers like <n>, <adj>
            txt_esc = re.sub(
                r"&lt;([a-zA-Z0-9\-]+)&gt;",
                lambda m: f"<span class='pos-tag'>⟨{m.group(1)}⟩</span>",
                txt_esc,
            )

            # transliteration /.../
            txt_esc = re.sub(
                r"/([^/]+)/",
                lambda m: f"<span class='translit'>/{html.escape(m.group(1))}/</span>",
                txt_esc,
            )

            # media filenames
            txt_esc = re.sub(
                r"([A-Za-z0-9_\-]+\.(?:wav|mp3|ogg|flac|png|jpg|jpeg|gif))",
                lambda m: f"<span class='media-file'>🎧 {html.escape(m.group(1))}</span>",
                txt_esc,
            )

            # RTL detection
            if re.search(r"[\u0590-\u06FF\u0750-\u08FF]", txt_esc):
                html_fragments.append(f"<span dir='rtl'>{txt_esc}</span>")
            else:
                html_fragments.append(txt_esc)

        # --- Main parse loop ---
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if not tok:
                i += 1
                continue

            if tok.startswith("[") and tok.endswith("]"):
                tname = tok[1:-1].strip()

                # Closing
                if tname.startswith("/"):
                    pop(tname)
                # [ref]word[/ref]
                elif tname.lower().startswith("ref"):
                    next_token = tokens[i + 1] if i + 1 < len(tokens) else ""
                    if next_token and not next_token.startswith("["):
                        word = next_token.strip()
                        html_fragments.append(
                            f'<a href="dict://{html.escape(word)}" class="dict-link">{html.escape(word)}</a>'
                        )
                        i += 3
                        continue
                    else:
                        push(tok)
                else:
                    push(tok)

            elif tok == "<<":
                if i + 1 < len(tokens) and tokens[i + 1] not in {"<<", ">>"}:
                    word = tokens[i + 1].strip()
                    html_fragments.append(
                        f'<a href="dict://{html.escape(word)}" class="dict-link">{html.escape(word)}</a>'
                    )
                    i += 3
                    continue
                else:
                    push(tok)

            else:
                process_text(tok)

            i += 1

        # close unbalanced
        while stack:
            top = stack.pop()
            html_fragments.append(self._close_tag(top))

        out = "".join(html_fragments)
        # tidy up link formatting
        out = re.sub(
            r"<a href=\"dict://([^']+)\" class=\"dict-link\">(.*?)</a>",
            r'<a href="dict://\1" class="dict-link">\2</a>',
            out,
        )
        return out

    # ------------------------------------------------------------
    # Public render_entry
    # ------------------------------------------------------------
    def render_entry(self, headword, defs):
        return "\n".join(self._render_line(line, headword) for line in defs)

# ============================================================
#  DICTIONARY MANAGER
# ============================================================

class DictionaryManager:
    def __init__(self):
        self.dictionaries = {}
        self.entries = {}

    def decode_dsl_bytes(self, raw: bytes) -> str:
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw[3:].decode("utf-8", "ignore")
        for enc in ("utf-8", "utf-16-le", "utf-16-be", "cp1251", "cp1256", "latin1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "ignore")

    def load_dictionary(self, path, color="default"):
        path = Path(path)
        if not path.exists():
            return False
        try:
            raw = gzip.open(path, "rb").read() if path.suffix == ".dz" else open(path, "rb").read()
            text = self.decode_dsl_bytes(raw)
        except Exception as e:
            print("Decode error:", e)
            return False

        dict_name = path.stem
        for line in text.splitlines()[:20]:
            if line.startswith("#NAME"):
                m = re.search(r'#NAME\s+"([^"]+)"', line)
                if m:
                    dict_name = m.group(1)
                break

        entries = DSLParser(text).parse()
        self.dictionaries[str(path)] = {"name": dict_name, "entries": entries, "color": color}
        for w, defs in entries.items():
            self.entries.setdefault(w.lower(), []).append((w, dict_name, defs, color))
        print(f"Loaded {len(entries)} entries from {dict_name}")
        return True

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
            if w == q: return (0, w)
            if w.startswith(q): return (1, w)
            return (2, w)
        return sorted(res.items(), key=order)


# ============================================================
#  MAIN WINDOW
# ============================================================

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(900, 600)
        self.dict_manager = DictionaryManager()
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_theme_changed)
        self._build_ui()
        self._apply_theme_to_webview()
        self.show_placeholder("Load a dictionary to start searching")
        self._load_settings()

    # ---------------- UI ----------------

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

        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.connect("decide-policy", self.on_decide_policy)
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(self.webview)
        vbox.append(scrolled)

    def _build_theme_css(self):
        dark = self.style_manager.get_dark()
        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        link = "#89b4ff" if dark else "#005bbb"
        border = "#444" if dark else "#ccc"
        pos = "#9ae59a" if dark else "#228B22"
        example = "#9ae59a" if dark else "#228B22"

        return f"""
        :root {{
            --base-indent: 0.4em;
        }}

        body {{
            font-family: system-ui, sans-serif;
            background: {bg};
            color: {fg};
            margin: 12px;
            line-height: 1.45;
        }}

        .lemma {{
            font-size: 1.3em;
            font-weight: bold;
            color: {link};
        }}

        .dict {{
            font-size: 0.9em;
            color: #888;
        }}

        .header {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 4px;
        }}

        .entry {{
            text-align: left;
        }}

        .defs {{
            margin-top: 2px;
            line-height: 1.45;
        }}

        .pos {{
            color: {pos};
            font-style: italic;
            display: inline-block;
        }}

        .example {{
            color: {example};
            font-style: italic;
            display: inline-block;
        }}

        .dict-link {{
            color: {link};
            text-decoration: none;
            cursor: pointer;
            display: inline-block;
        }}
        .dict-link:hover {{
            text-decoration: underline;
        }}

        hr {{
            border: none;
            border-top: 1px solid {border};
            margin: 10px 0;
        }}

        /* --- NEW for transliteration & mixed text --- */
        .m-line {{
            line-height: 1.4;
            margin: 2px 0;
        }}
        .m-tag {{
            opacity: 0.7;
            font-style: italic;
            display: inline-block;
        }}
        .translit {{
            color: gray;
            display: inline-block;
        }}
        .pos-tag {{
            opacity: 0.85;
            font-style: italic;
            display: inline-block;
            margin: 0 0.15em;
        }}
        .translit {{
            color: gray;
            font-style: italic;
        }}
        .comment {{
            opacity: 0.7;
        }}
        .media-file {{
            color: var(--link);
            cursor: pointer;
            display: inline-block;
            margin-left: 0.25em;
        }}
        .lang {{
            opacity: 0.8;
            font-style: italic;
        }}
        .full-translation {{
            display: none;
            opacity: 0.8;
            font-style: italic;
        }}

        """


    # ---------------- Logic ----------------

    def show_placeholder(self, text):
        self._current_placeholder = text  # Track for theme changes
        self._last_html = None  # Clear search results
        theme_css = self._build_theme_css()
        dark = self.style_manager.get_dark()
        html = f"""
        <html>
        <head>
            <meta charset='utf-8'>
            <meta name='color-scheme' content='{'dark' if dark else 'light'}'>
            <style id='theme-style'>{theme_css}</style>
        </head>
        <body><p style='text-align: center; margin-top: 2em; opacity: 0.7;'>{text}</p></body>
        </html>
        """
        self.webview.load_html(html, "file:///")

    def on_search(self, entry):
        q = entry.get_text()
        self.perform_search(q)

    def perform_search(self, q):
        q = q.strip()
        if not q:
            self.show_placeholder("Enter a word")
            return
        results = self.dict_manager.search(q)
        if not results:
            self.show_placeholder("No results found")
            return

        self._current_placeholder = None  # Clear placeholder tracking
        html = self.build_html(results)
        self.webview.load_html(html, "file:///")

    def build_html(self, results):
        """Build full HTML for search results - simple LTR layout."""
        renderer = DSLRenderer(self.style_manager.get_dark())
        body = []

        for word, dict_data in results[:100]:
            for dname, defs, _ in dict_data:
                defs_html = renderer.render_entry(word, defs)

                # Simple LTR layout for all entries
                header_html = (
                    f"<div class='header'>"
                    f"<span class='lemma'>{word}</span>"
                    f"<span class='dict'>📖 {dname}</span>"
                    f"</div>"
                )
                entry_html = (
                    f"<div class='entry'>"
                    f"{header_html}"
                    f"<div class='defs'>{defs_html}</div>"
                    f"<hr></div>"
                )

                body.append(entry_html)

        # --- Build final page with theme ---
        theme_css = self._build_theme_css()
        dark = self.style_manager.get_dark()
        html = f"""
        <html>
        <head>
            <meta charset='utf-8'>
            <meta name='color-scheme' content='{'dark' if dark else 'light'}'>
            <style id='theme-style'>{theme_css}</style>
        </head>
        <body>{''.join(body)}</body>
        </html>
        """

        self._last_html = results
        return html


    def on_decide_policy(self, webview, decision, decision_type):
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            uri = nav_action.get_request().get_uri()
            if uri.startswith("dict://"):
                decision.ignore()
                word = uri[7:]
                self.search_entry.set_text(word)
                GLib.idle_add(lambda: self.perform_search(word))
                return True
        return False

    def on_settings(self, *_):
        dlg = SettingsDialog(self, getattr(self, 'dictionaries', None))
        dlg.present()

    def _apply_theme_to_webview(self):
        dark = self.style_manager.get_dark()
        
        # Set dark mode in webkit settings
        try:
            self.webview.get_settings().set_property("enable-dark-mode", dark)
        except TypeError:
            pass
        
        # Set webview background color to prevent white flash
        bg_color = Gdk.RGBA()
        if dark:
            bg_color.parse("#1e1e1e")
        else:
            bg_color.parse("#ffffff")
        self.webview.set_background_color(bg_color)

    def on_theme_changed(self, *_):
        # Update webview background color
        self._apply_theme_to_webview()
        
        # Refresh content
        if hasattr(self, "_last_html") and self._last_html:
            # Refresh search results
            html = self.build_html(self._last_html)
            self.webview.load_html(html, "file:///")
        elif hasattr(self, "_current_placeholder"):
            # Refresh placeholder message
            self.show_placeholder(self._current_placeholder)

    # ---------------- Settings ----------------

    def _load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            cfg = json.load(open(CONFIG_FILE))
            self.dictionaries = cfg.get("dictionaries", [])
            
            # Load only enabled dictionaries into dict_manager
            for d in self.dictionaries:
                if d.get("enabled", True):
                    p = d["path"]
                    c = d.get("color", "default")
                    if os.path.exists(p):
                        self.dict_manager.load_dictionary(p, c)
        except Exception as e:
            print("Settings load error:", e)
            self.dictionaries = []

    def save_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Get dictionaries list from settings dialog if available
        if hasattr(self, 'dictionaries'):
            dict_list = self.dictionaries
        else:
            # Fallback: build from dict_manager
            dict_list = [{"path": p, "color": info["color"], "enabled": True} 
                        for p, info in self.dict_manager.dictionaries.items()]
        
        json.dump({"dictionaries": dict_list}, open(CONFIG_FILE, "w"), indent=2)


# ============================================================
#  SETTINGS DIALOG
# ============================================================
import json
from gi.repository import Gtk, Adw, Gio, GLib

class SettingsDialog(Adw.PreferencesWindow):
    """
    Full-featured settings window for the dictionary app.
    GTK4 + Libadwaita–compliant (no deprecated or removed API).
    Features:
      - Theme selection (System/Light/Dark)
      - Enable/disable toggle per dictionary
      - Priority reorder buttons (Up/Down)
      - Remove dictionary button
      - Metadata display (language pair, entry count)
      - Add new dictionaries with .dsl and .dsl.dz support
      - Auto-refresh + persistence
    """

    def __init__(self, app, dictionaries=None):
        super().__init__(title="Settings", transient_for=app)
        self.set_default_size(600, 550)
        self.set_modal(True)
        self.set_search_enabled(False)

        self.app = app
        self.style_manager = Adw.StyleManager.get_default()

        # Initialize dictionaries list
        if dictionaries is not None and isinstance(dictionaries, list):
            # If passed as list, use it directly
            self.dictionaries = dictionaries
        elif hasattr(app, "dictionaries"):
            # Use app's dictionary list
            self.dictionaries = app.dictionaries
        else:
            # Load from config file or initialize empty
            self.dictionaries = []
            self.load_settings()

        # === Preferences Page ===
        self.page = Adw.PreferencesPage()
        self.set_content(self.page)

        # === Theme Section ===
        self.theme_group = Adw.PreferencesGroup(
            title="Appearance", 
            description="Customize the visual theme"
        )
        self.page.add(self.theme_group)

        self.theme_row = Adw.ComboRow(
            title="Theme",
            subtitle="Choose light or dark mode",
            model=Gtk.StringList.new(["System", "Light", "Dark"]),
        )
        self.theme_row.set_selected(self._current_theme_index())
        self.theme_row.connect("notify::selected", self.on_theme_changed)
        self.theme_group.add(self.theme_row)

        # === Dictionaries Section ===
        self.dict_group = Adw.PreferencesGroup(
            title="Dictionaries",
            description="Manage your dictionary sources • Higher position = higher priority"
        )
        self.page.add(self.dict_group)

        # List container with rounded corners
        self.dict_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.dict_list.add_css_class("boxed-list")
        
        # Empty state placeholder
        self.empty_row = Adw.ActionRow(
            title="No dictionaries loaded",
            subtitle="Click 'Add Dictionary' below to get started",
        )
        self.empty_row.set_sensitive(False)
        
        self.dict_group.add(self.dict_list)

        self._populate_dictionaries()

        # Add dictionary button with icon
        add_button = Gtk.Button(label="Add Dictionary…", halign=Gtk.Align.CENTER)
        add_button.set_icon_name("list-add-symbolic")
        add_button.add_css_class("pill")
        add_button.add_css_class("suggested-action")
        add_button.set_margin_top(12)
        add_button.connect("clicked", self.on_add_dictionary)
        self.dict_group.add(add_button)

    # ==========================================================
    # THEME HANDLING
    # ==========================================================
    def _current_theme_index(self):
        cs = self.style_manager.get_color_scheme()
        if cs == Adw.ColorScheme.FORCE_LIGHT:
            return 1
        elif cs == Adw.ColorScheme.FORCE_DARK:
            return 2
        return 0

    def on_theme_changed(self, row, _param):
        idx = row.get_selected()
        if idx == 1:
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            self.app.dark_mode = False
        elif idx == 2:
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            self.app.dark_mode = True
        else:
            self.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
            self.app.dark_mode = self.style_manager.get_dark()

        self.app.save_settings()
        if hasattr(self.app, "update_css"):
            self.app.update_css()
        if hasattr(self.app, "refresh_search_results"):
            self.app.refresh_search_results()

    def clear_listbox(self, listbox: Gtk.ListBox):
        child = listbox.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            listbox.remove(child)
            child = next_child

    # ==========================================================
    # DICTIONARY LIST MANAGEMENT
    # ==========================================================
    def _populate_dictionaries(self):
        self.clear_listbox(self.dict_list)
        
        if not self.dictionaries:
            self.dict_list.append(self.empty_row)
        else:
            for idx, d in enumerate(self.dictionaries):
                self.dict_list.append(self._make_dict_row(d, idx))

    def _make_dict_row(self, d, idx):
        """Build one row for the dictionary list."""
        meta = d.get("meta", {})
        subtitle_parts = []
        if "index_language" in meta and "contents_language" in meta:
            subtitle_parts.append(f"{meta['index_language']} → {meta['contents_language']}")
        if "word_count" in meta:
            subtitle_parts.append(f"{meta['word_count']:,} entries")
        if not subtitle_parts:
            subtitle_parts.append(d.get("path", ""))

        row = Adw.ActionRow(
            title=d.get("name", "Unknown dictionary"),
            subtitle=" • ".join(subtitle_parts),
        )

        # Switch toggle
        switch = Gtk.Switch(active=d.get("enabled", True), valign=Gtk.Align.CENTER)
        switch.connect("state-set", self.on_dict_toggled, d)
        switch.set_tooltip_text("Enable/disable dictionary")
        row.add_suffix(switch)

        # Control buttons box
        control_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        
        # Reorder controls (Up/Down)
        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        up_btn.add_css_class("flat")
        down_btn.add_css_class("flat")
        up_btn.set_tooltip_text("Move up (higher priority)")
        down_btn.set_tooltip_text("Move down (lower priority)")
        up_btn.connect("clicked", self.on_move_up, idx)
        down_btn.connect("clicked", self.on_move_down, idx)

        # Remove button
        remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("error")
        remove_btn.set_tooltip_text("Remove dictionary")
        remove_btn.connect("clicked", self.on_remove_dictionary, idx)

        control_box.append(up_btn)
        control_box.append(down_btn)
        control_box.append(remove_btn)
        row.add_suffix(control_box)

        row.set_activatable(False)
        row.dictionary = d
        return row

    def on_move_up(self, _btn, idx):
        if idx > 0:
            self.dictionaries[idx - 1], self.dictionaries[idx] = (
                self.dictionaries[idx],
                self.dictionaries[idx - 1],
            )
            self._populate_dictionaries()
            self.save_settings()
            # Note: Dictionary priority is just visual order; 
            # actual search priority depends on dict_manager implementation

    def on_move_down(self, _btn, idx):
        if idx < len(self.dictionaries) - 1:
            self.dictionaries[idx + 1], self.dictionaries[idx] = (
                self.dictionaries[idx],
                self.dictionaries[idx + 1],
            )
            self._populate_dictionaries()
            self.save_settings()
            # Note: Dictionary priority is just visual order;
            # actual search priority depends on dict_manager implementation

    def on_remove_dictionary(self, _btn, idx):
        """Remove dictionary with confirmation dialog"""
        if idx < 0 or idx >= len(self.dictionaries):
            return
        
        dict_name = self.dictionaries[idx].get("name", "Unknown")
        
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Remove {dict_name}?",
            body="This will only remove it from the list, not delete the file.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        
        dialog.connect("response", self._on_remove_dialog_response, idx)
        dialog.present()

    def _on_remove_dialog_response(self, dialog, response, idx):
        if response == "remove":
            dict_path = self.dictionaries[idx].get("path")
            self.dictionaries.pop(idx)
            self._populate_dictionaries()
            self.save_settings()
            
            # Remove from app's dict_manager
            if hasattr(self.app, "dict_manager") and dict_path:
                if dict_path in self.app.dict_manager.dictionaries:
                    del self.app.dict_manager.dictionaries[dict_path]
                    # Rebuild entries index
                    self.app.dict_manager.entries.clear()
                    for path, info in self.app.dict_manager.dictionaries.items():
                        for w, defs in info["entries"].items():
                            self.app.dict_manager.entries.setdefault(w.lower(), []).append(
                                (w, info["name"], defs, info["color"])
                            )
                
                # Refresh search if there's text
                if hasattr(self.app, "perform_search") and hasattr(self.app, "search_entry"):
                    current_text = self.app.search_entry.get_text()
                    if current_text:
                        self.app.perform_search(current_text)


    def on_dict_toggled(self, switch, state, d):
        d["enabled"] = state
        self.save_settings()
        
        # Reload dictionary in app if we have access to dict_manager
        if hasattr(self.app, "dict_manager") and d.get("path"):
            path = d["path"]
            if state and path not in self.app.dict_manager.dictionaries:
                # Re-enable: load the dictionary
                self.app.dict_manager.load_dictionary(path, d.get("color", "default"))
            elif not state and path in self.app.dict_manager.dictionaries:
                # Disable: remove from active dictionaries
                del self.app.dict_manager.dictionaries[path]
                # Rebuild entries index
                self.app.dict_manager.entries.clear()
                for p, info in self.app.dict_manager.dictionaries.items():
                    for w, defs in info["entries"].items():
                        self.app.dict_manager.entries.setdefault(w.lower(), []).append(
                            (w, info["name"], defs, info["color"])
                        )
            
            # Refresh search results
            if hasattr(self.app, "perform_search") and hasattr(self.app, "search_entry"):
                current_text = self.app.search_entry.get_text()
                if current_text:
                    self.app.perform_search(current_text)

    def on_add_dictionary(self, *_):
        dialog = Gtk.FileChooserNative(
            title="Add DSL Dictionary",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        filter_dsl = Gtk.FileFilter()
        filter_dsl.set_name("DSL Dictionaries (*.dsl, *.dsl.dz)")
        filter_dsl.add_pattern("*.dsl")
        filter_dsl.add_pattern("*.dsl.dz")
        dialog.set_filter(filter_dsl)

        dialog.connect("response", self._on_file_dialog_response)
        dialog.show()

    def _on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                path = file.get_path()
                name = GLib.path_get_basename(path)
                meta = self._extract_dsl_metadata(path)
                new_dict = {"name": name, "path": path, "enabled": True, "meta": meta}
                self.dictionaries.append(new_dict)
                self._populate_dictionaries()
                self.save_settings()
                if hasattr(self.app, "dict_manager"):
                    self.app.dict_manager.load_dictionary(path)
                    if hasattr(self.app, "perform_search"):
                        current_text = self.app.search_entry.get_text()
                        if current_text:
                            self.app.perform_search(current_text)

    # ==========================================================
    # METADATA EXTRACTION
    # ==========================================================
    def _extract_dsl_metadata(self, path):
        """Extract metadata from DSL file header"""
        meta = {}
        try:
            # Handle gzipped files
            if path.endswith('.dz'):
                with gzip.open(path, 'rb') as f:
                    raw = f.read(10000)  # Read first 10KB
            else:
                with open(path, 'rb') as f:
                    raw = f.read(10000)
            
            # Try to decode
            text = None
            if raw.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
                text = raw[3:].decode("utf-8", errors="ignore")
            else:
                for enc in ("utf-16-le", "utf-16-be", "utf-8", "cp1251", "latin1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except:
                        continue
            
            if not text:
                return meta
            
            # Extract metadata from header
            for line in text.splitlines()[:50]:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#INDEX_LANGUAGE"):
                    parts = line.split('"')
                    if len(parts) >= 2:
                        meta["index_language"] = parts[1]
                elif line.startswith("#CONTENTS_LANGUAGE"):
                    parts = line.split('"')
                    if len(parts) >= 2:
                        meta["contents_language"] = parts[1]
                elif line.lower().startswith("#name"):
                    parts = line.split('"')
                    if len(parts) >= 2:
                        meta["name"] = parts[1]
                elif not line.startswith("#"):
                    break  # End of header
            
            # Count entries (words starting with non-space)
            try:
                if path.endswith('.dz'):
                    with gzip.open(path, 'rt', encoding='utf-16-le', errors='ignore') as f:
                        count = sum(1 for line in f if line and not line[0].isspace() and not line.startswith("#"))
                else:
                    with open(path, 'r', encoding='utf-16-le', errors='ignore') as f:
                        count = sum(1 for line in f if line and not line[0].isspace() and not line.startswith("#"))
                
                if count > 0:
                    meta["word_count"] = count
            except:
                pass  # If word count fails, just skip it
                
        except Exception as e:
            print(f"Metadata extraction failed for {path}: {e}")
        
        return meta

    # ==========================================================
    # PERSISTENCE
    # ==========================================================
    def save_settings(self):
        """Save settings to both app instance and config file"""
        # Update app's dictionary list
        if hasattr(self.app, 'dictionaries'):
            self.app.dictionaries = self.dictionaries
        
        # Save to config file
        data = {
            "theme": self.theme_row.get_selected_item().get_string().lower(),
            "dictionaries": self.dictionaries,
        }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print("Failed to save settings:", e)

    def load_settings(self):
        """Load settings from config file"""
        try:
            if not CONFIG_FILE.exists():
                return
            
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self.dictionaries = data.get("dictionaries", [])
            
            theme = data.get("theme", "system")
            if theme == "light":
                self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            elif theme == "dark":
                self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:
                self.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Failed to load settings: {e}")
            
# ============================================================
#  APPLICATION
# ============================================================

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
