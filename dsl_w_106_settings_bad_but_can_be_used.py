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
        line = line.replace("~", html.escape(headword)).strip()
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
            txt_esc = html.escape(txt)

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
        self.show_placeholder("Load a dictionary to start searching")

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
        html = f"<html><body><p>{text}</p></body></html>"
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
        dlg = SettingsDialog(self, self.dict_manager.dictionaries.keys())
        dlg.present()

    def _apply_theme_to_webview(self):
        dark = self.style_manager.get_dark()
        try:
            self.webview.get_settings().set_property("enable-dark-mode", dark)
        except TypeError:
            pass

    def on_theme_changed(self, *_):
        if hasattr(self, "_last_html"):
            html = self.build_html(self._last_html)
            self.webview.load_html(html, "file:///")

    # ---------------- Settings ----------------

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
        arr = [{"path": p, "color": info["color"]} for p, info in self.dict_manager.dictionaries.items()]
        json.dump({"dictionaries": arr}, open(CONFIG_FILE, "w"))


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
      - Metadata display (language pair, entry count)
      - Add new dictionaries
      - Auto-refresh + persistence
    """

    SETTINGS_FILE = GLib.get_user_config_dir() + "/dsl_reader_settings.json"

    def __init__(self, app, dictionaries=None):
        super().__init__(title="Settings", transient_for=app)
        self.set_default_size(560, 520)
        self.set_modal(True)

        self.app = app
        self.style_manager = Adw.StyleManager.get_default()

        # Accept dictionary data
        if dictionaries is not None:
            self.dictionaries = []
            for d in dictionaries:
                if isinstance(d, dict):
                    self.dictionaries.append(d)
                else:
                    self.dictionaries.append({
                        "name": getattr(d, "name", str(d)),
                        "path": str(getattr(d, "path", d)),
                        "enabled": getattr(d, "enabled", True),
                        "meta": getattr(d, "meta", {}),
                    })
        else:
            self.dictionaries = getattr(app, "dictionaries", [])

        # === Preferences Page ===
        self.page = Adw.PreferencesPage()
        self.set_content(self.page)

        # === Theme Section ===
        self.theme_group = Adw.PreferencesGroup(title="Theme")
        self.page.add(self.theme_group)

        self.theme_row = Adw.ComboRow(
            title="Theme mode",
            subtitle="Choose color scheme",
            model=Gtk.StringList.new(["System", "Light", "Dark"]),
        )
        self.theme_row.set_selected(self._current_theme_index())
        self.theme_row.connect("notify::selected", self.on_theme_changed)
        self.theme_group.add(self.theme_row)

        # === Dictionaries Section ===
        self.dict_group = Adw.PreferencesGroup(title="Dictionaries")
        self.page.add(self.dict_group)

        # List container (no reorderable property in GTK4)
        self.dict_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.dict_group.add(self.dict_list)

        self._populate_dictionaries()

        # Add dictionary button
        add_button = Gtk.Button(label="Add Dictionary…")
        add_button.add_css_class("suggested-action")
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
        switch = Gtk.Switch(active=d.get("enabled", True))
        switch.connect("state-set", self.on_dict_toggled, d)
        row.add_suffix(switch)

        # Reorder controls (Up/Down)
        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        up_btn.set_tooltip_text("Move up (higher priority)")
        down_btn.set_tooltip_text("Move down (lower priority)")
        up_btn.connect("clicked", self.on_move_up, idx)
        down_btn.connect("clicked", self.on_move_down, idx)

        control_box = Gtk.Box(spacing=6)
        control_box.append(up_btn)
        control_box.append(down_btn)
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
            if hasattr(self.app, "refresh_search_results"):
                self.app.refresh_search_results()

    def on_move_down(self, _btn, idx):
        if idx < len(self.dictionaries) - 1:
            self.dictionaries[idx + 1], self.dictionaries[idx] = (
                self.dictionaries[idx],
                self.dictionaries[idx + 1],
            )
            self._populate_dictionaries()
            self.save_settings()
            if hasattr(self.app, "refresh_search_results"):
                self.app.refresh_search_results()

    def on_dict_toggled(self, switch, state, d):
        d["enabled"] = state
        self.save_settings()
        if hasattr(self.app, "refresh_search_results"):
            self.app.refresh_search_results()

    def on_add_dictionary(self, *_):
        dialog = Gtk.FileChooserNative(
            title="Add DSL Dictionary",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        filter_dsl = Gtk.FileFilter()
        filter_dsl.set_name("DSL Dictionaries")
        filter_dsl.add_pattern("*.dsl")
        dialog.add_filter(filter_dsl)

        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            name = GLib.path_get_basename(path)
            meta = self._extract_dsl_metadata(path)
            new_dict = {"name": name, "path": path, "enabled": True, "meta": meta}
            self.dictionaries.append(new_dict)
            self._populate_dictionaries()
            self.save_settings()
            if hasattr(self.app, "refresh_search_results"):
                self.app.refresh_search_results()

        dialog.destroy()

    # ==========================================================
    # METADATA EXTRACTION
    # ==========================================================
    def _extract_dsl_metadata(self, path):
        meta = {}
        try:
            with open(path, "r", encoding="utf-16-le", errors="ignore") as f:
                for _ in range(100):
                    line = f.readline().strip()
                    if not line:
                        continue
                    if line.startswith("#INDEX_LANGUAGE"):
                        meta["index_language"] = line.split('"', 1)[1].strip('"')
                    elif line.startswith("#CONTENTS_LANGUAGE"):
                        meta["contents_language"] = line.split('"', 1)[1].strip('"')
                    elif line.lower().startswith("#name"):
                        meta["name"] = line.split('"', 1)[1].strip('"')
                    elif line.startswith("0"):
                        break
            # crude word count
            with open(path, "r", encoding="utf-16-le", errors="ignore") as f:
                meta["word_count"] = sum(1 for line in f if line.strip().startswith("0"))
        except Exception as e:
            print("Metadata extraction failed:", e)
        return meta

    # ==========================================================
    # PERSISTENCE
    # ==========================================================
    def save_settings(self):
        data = {
            "theme": self.theme_row.get_selected_item().get_string().lower(),
            "dictionaries": self.dictionaries,
        }
        try:
            with open(self.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print("Failed to save settings:", e)

    def load_settings(self):
        try:
            with open(self.SETTINGS_FILE, "r", encoding="utf-8") as f:
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
