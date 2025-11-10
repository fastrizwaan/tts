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
    """Manages multiple DSL dictionaries with priority and enabled state."""
    def __init__(self):
        # Dictionary structure: { path: { "name": str, "entries": dict, "color": str, "priority": int, "enabled": bool } }
        self.dictionaries = {}

    def load_dictionary(self, path, color="default", priority=None, enabled=True):
        """Load a single .dsl[.dz] dictionary."""
        try:
            path_obj = Path(path)
            
            # Determine priority
            if priority is None:
                # Set priority to max + 1
                priority = max([d["priority"] for d in self.dictionaries.values()], default=-1) + 1
            
            if path_obj.suffix == ".dz":
                with gzip.open(path_obj, "rt", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            else:
                with open(path_obj, "r", encoding="utf-16-le", errors="ignore") as f:
                    text = f.read()

            parser = DSLParser(text)
            entries = parser.parse()

            # Extract name from header
            name = path_obj.stem
            for line in text.splitlines()[:10]:
                if line.startswith("#NAME"):
                    name = line.split('"')[1] if '"' in line else name
                    break

            self.dictionaries[path] = {
                "name": name,
                "entries": entries,
                "color": color,
                "priority": priority,
                "enabled": enabled,
            }
            print(f"✓ Loaded: {name} ({len(entries)} entries, priority={priority}, enabled={enabled})")
            return True

        except Exception as e:
            print(f"✗ Failed to load {path}: {e}")
            return False

    def search(self, query):
        """Search across all enabled dictionaries sorted by priority."""
        if not query:
            return []

        q = query.strip().lower()
        results = []

        # Get enabled dictionaries sorted by priority
        enabled_dicts = sorted(
            [(path, info) for path, info in self.dictionaries.items() if info["enabled"]],
            key=lambda x: x[1]["priority"]
        )

        for path, info in enabled_dicts:
            name = info["name"]
            entries = info["entries"]
            
            # Exact match
            if q in entries:
                results.append((q, [(name, entries[q], info["color"])]))
                continue

            # Case-insensitive prefix matches
            matches = [w for w in entries.keys() if w.lower().startswith(q)]
            if matches:
                for m in matches[:10]:
                    results.append((m, [(name, entries[m], info["color"])]))

        return results

    def get_sorted_dictionaries(self):
        """Return dictionaries sorted by priority."""
        return sorted(self.dictionaries.items(), key=lambda x: x[1]["priority"])


# ============================================================
#  MAIN WINDOW
# ============================================================

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=800, default_height=600)
        
        self.dict_manager = DictionaryManager()
        self.style_manager = Adw.StyleManager.get_default()
        
        # Load settings before building UI
        self._load_settings()
        
        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        
        # Settings button
        settings_btn = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self.on_settings)
        header.pack_end(settings_btn)
        
        main_box.append(header)

        # Search entry
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(12)
        search_box.set_margin_bottom(12)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text("Enter word to search...")
        self.search_entry.connect("search-changed", lambda e: self.perform_search(e.get_text()))
        search_box.append(self.search_entry)

        main_box.append(search_box)

        # WebView
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)

        self.webview = WebKit.WebView()
        self.webview.connect("decide-policy", self.on_decide_policy)
        scrolled.set_child(self.webview)

        main_box.append(scrolled)

        # Theme tracking
        self.style_manager.connect("notify::dark", self.on_theme_changed)
        self._apply_theme_to_webview()

        # Welcome message
        self.webview.load_html("<h2 style='text-align:center; margin-top:100px;'>Start typing to search...</h2>", "file:///")

    # ---------------- Search Logic ----------------

    def perform_search(self, query):
        if not query.strip():
            self.webview.load_html("<h2 style='text-align:center; margin-top:100px;'>Start typing to search...</h2>", "file:///")
            return

        results = self.dict_manager.search(query)
        
        if not results:
            html = f"<h3 style='text-align:center; margin-top:50px;'>No results for '{html.escape(query)}'</h3>"
            self.webview.load_html(html, "file:///")
            return

        html_content = self.build_html(results)
        self.webview.load_html(html_content, "file:///")

    # ---------------- HTML Generation ----------------

    def _build_theme_css(self):
        dark = self.style_manager.get_dark()

        if dark:
            bg = "#1e1e1e"
            fg = "#e0e0e0"
            border = "#3a3a3a"
            lemma_bg = "#2a2a2a"
            link_color = "#5ea3ff"
        else:
            bg = "#ffffff"
            fg = "#1a1a1a"
            border = "#d0d0d0"
            lemma_bg = "#f5f5f5"
            link_color = "#0066cc"

        return f"""
        body {{
            font-family: sans-serif;
            font-size: 14px;
            background: {bg};
            color: {fg};
            padding: 16px;
            line-height: 1.6;
        }}
        .entry {{
            margin-bottom: 20px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            padding: 8px 12px;
            background: {lemma_bg};
            border-radius: 6px;
        }}
        .lemma {{
            font-size: 20px;
            font-weight: bold;
            color: {fg};
        }}
        .dict {{
            font-size: 12px;
            opacity: 0.7;
        }}
        .defs {{
            padding-left: 12px;
        }}
        .line {{
            margin: 4px 0;
        }}
        .example {{ font-style: italic; opacity: 0.85; }}
        .translation {{ color: {link_color}; }}
        .comment {{ opacity: 0.7; font-size: 0.9em; }}
        .pos {{ font-weight: bold; color: {link_color}; }}
        .m-line {{ margin-left: 1.5em; }}
        hr {{
            border: none;
            border-top: 1px solid {border};
            margin: 16px 0;
        }}
        a.dict-link {{
            color: {link_color};
            text-decoration: none;
            cursor: pointer;
        }}
        a.dict-link:hover {{
            text-decoration: underline;
        }}
        """

    def build_html(self, results):
        dark = self.style_manager.get_dark()
        renderer = DSLRenderer(dark_mode=dark)
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
        dlg = SettingsDialog(self)
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
        """Load settings from config file."""
        if not CONFIG_FILE.exists():
            return
        
        try:
            with open(CONFIG_FILE, 'r') as f:
                cfg = json.load(f)
            
            # Load theme preference
            theme = cfg.get("theme", "default")
            if theme == "light":
                self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            elif theme == "dark":
                self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:
                self.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
            
            # Load only enabled dictionaries
            for d in cfg.get("dictionaries", []):
                path = d["path"]
                color = d.get("color", "default")
                priority = d.get("priority", 0)
                enabled = d.get("enabled", True)
                
                if os.path.exists(path):
                    self.dict_manager.load_dictionary(path, color, priority, enabled)
                    
        except Exception as e:
            print(f"Settings load error: {e}")

    def save_settings(self):
        """Save settings to config file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        # Get current theme
        color_scheme = self.style_manager.get_color_scheme()
        if color_scheme == Adw.ColorScheme.FORCE_LIGHT:
            theme = "light"
        elif color_scheme == Adw.ColorScheme.FORCE_DARK:
            theme = "dark"
        else:
            theme = "default"
        
        # Save all dictionaries with their state
        dict_list = []
        for path, info in self.dict_manager.dictionaries.items():
            dict_list.append({
                "path": path,
                "color": info["color"],
                "priority": info["priority"],
                "enabled": info["enabled"]
            })
        
        settings = {
            "theme": theme,
            "dictionaries": dict_list
        }
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(settings, f, indent=2)

    def reload_dictionaries(self):
        """Reload all dictionaries while preserving settings."""
        # Save current dictionary settings
        dict_settings = {}
        for path, info in self.dict_manager.dictionaries.items():
            dict_settings[path] = {
                "color": info["color"],
                "priority": info["priority"],
                "enabled": info["enabled"]
            }
        
        # Clear and reload
        self.dict_manager.dictionaries.clear()
        
        for path, settings in dict_settings.items():
            if os.path.exists(path):
                self.dict_manager.load_dictionary(
                    path,
                    settings["color"],
                    settings["priority"],
                    settings["enabled"]
                )
        
        # Refresh current search
        current_query = self.search_entry.get_text()
        if current_query:
            self.perform_search(current_query)


# ============================================================
#  SETTINGS DIALOG
# ============================================================

class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.set_title("Settings")
        
        # Create preferences page
        page = Adw.PreferencesPage()
        self.add(page)
        
        # ========== APPEARANCE GROUP ==========
        appearance_group = Adw.PreferencesGroup()
        appearance_group.set_title("Appearance")
        appearance_group.set_description("Customize the look and feel")
        page.add(appearance_group)
        
        # Theme selector
        theme_row = Adw.ComboRow()
        theme_row.set_title("Theme")
        theme_row.set_subtitle("Choose between light, dark, or system theme")
        
        theme_model = Gtk.StringList.new(["System Default", "Light", "Dark"])
        theme_row.set_model(theme_model)
        
        # Set current theme
        color_scheme = self.parent.style_manager.get_color_scheme()
        if color_scheme == Adw.ColorScheme.FORCE_LIGHT:
            theme_row.set_selected(1)
        elif color_scheme == Adw.ColorScheme.FORCE_DARK:
            theme_row.set_selected(2)
        else:
            theme_row.set_selected(0)
        
        theme_row.connect("notify::selected", self.on_theme_changed)
        appearance_group.add(theme_row)
        
        # ========== DICTIONARIES GROUP ==========
        dict_group = Adw.PreferencesGroup()
        dict_group.set_title("Dictionaries")
        dict_group.set_description("Manage your dictionary sources")
        page.add(dict_group)
        
        # Add dictionary button
        add_row = Adw.ActionRow()
        add_row.set_title("Add Dictionary")
        add_row.set_subtitle("Import a new DSL dictionary file")
        
        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_btn.add_css_class("circular")
        add_btn.connect("clicked", self.on_add_dictionary)
        add_row.add_suffix(add_btn)
        
        dict_group.add(add_row)
        
        # Dictionary list
        self.dict_list_box = Gtk.ListBox()
        self.dict_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.dict_list_box.add_css_class("boxed-list")
        
        dict_group.add(self.dict_list_box)
        
        # Populate dictionary list
        self._populate_dictionary_list()
        
        # Apply button at bottom
        apply_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        apply_box.set_halign(Gtk.Align.END)
        apply_box.set_margin_top(12)
        apply_box.set_margin_bottom(12)
        apply_box.set_margin_start(12)
        apply_box.set_margin_end(12)
        
        self.apply_btn = Gtk.Button(label="Apply Changes")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", self.on_apply_changes)
        self.apply_btn.set_sensitive(False)
        apply_box.append(self.apply_btn)
        
        dict_group.add(apply_box)

    def _populate_dictionary_list(self):
        """Populate the dictionary list with current dictionaries."""
        # Clear existing items
        child = self.dict_list_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.dict_list_box.remove(child)
            child = next_child
        
        # Add dictionaries sorted by priority
        sorted_dicts = self.parent.dict_manager.get_sorted_dictionaries()
        
        for idx, (path, info) in enumerate(sorted_dicts):
            row = self._create_dictionary_row(path, info, idx, len(sorted_dicts))
            self.dict_list_box.append(row)

    def _create_dictionary_row(self, path, info, index, total):
        """Create a row for a dictionary with all controls."""
        row = Adw.ActionRow()
        row.set_title(info["name"])
        
        # Subtitle with entry count and filename
        entry_count = len(info["entries"])
        filename = os.path.basename(path)
        subtitle = f"{entry_count:,} entries • {filename}"
        row.set_subtitle(subtitle)
        
        # Store path for later reference
        row.path = path
        
        # Control box for buttons
        control_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        # Enable/Disable switch
        switch = Gtk.Switch()
        switch.set_active(info["enabled"])
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_tooltip_text("Enable or disable this dictionary")
        switch.connect("notify::active", self.on_dictionary_toggled, path)
        control_box.append(switch)
        
        # Up button (disabled if first)
        up_btn = Gtk.Button()
        up_btn.set_icon_name("go-up-symbolic")
        up_btn.set_valign(Gtk.Align.CENTER)
        up_btn.set_tooltip_text("Move up in priority")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self.on_move_up, path)
        control_box.append(up_btn)
        
        # Down button (disabled if last)
        down_btn = Gtk.Button()
        down_btn.set_icon_name("go-down-symbolic")
        down_btn.set_valign(Gtk.Align.CENTER)
        down_btn.set_tooltip_text("Move down in priority")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_sensitive(index < total - 1)
        down_btn.connect("clicked", self.on_move_down, path)
        control_box.append(down_btn)
        
        # Remove button
        remove_btn = Gtk.Button()
        remove_btn.set_icon_name("user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.set_tooltip_text("Remove this dictionary")
        remove_btn.add_css_class("destructive-action")
        remove_btn.add_css_class("circular")
        remove_btn.connect("clicked", self.on_remove_dictionary, path)
        control_box.append(remove_btn)
        
        row.add_suffix(control_box)
        
        return row

    def on_theme_changed(self, combo_row, *args):
        """Handle theme change."""
        selected = combo_row.get_selected()
        
        if selected == 0:  # System Default
            self.parent.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        elif selected == 1:  # Light
            self.parent.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif selected == 2:  # Dark
            self.parent.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        
        self.parent.save_settings()

    def on_dictionary_toggled(self, switch, pspec, path):
        """Handle dictionary enable/disable toggle."""
        enabled = switch.get_active()
        self.parent.dict_manager.dictionaries[path]["enabled"] = enabled
        self._mark_changes_pending()

    def on_move_up(self, button, path):
        """Move dictionary up in priority."""
        sorted_dicts = self.parent.dict_manager.get_sorted_dictionaries()
        
        # Find current index
        for idx, (p, info) in enumerate(sorted_dicts):
            if p == path:
                if idx > 0:
                    # Swap priorities
                    prev_path = sorted_dicts[idx - 1][0]
                    curr_priority = info["priority"]
                    prev_priority = sorted_dicts[idx - 1][1]["priority"]
                    
                    self.parent.dict_manager.dictionaries[path]["priority"] = prev_priority
                    self.parent.dict_manager.dictionaries[prev_path]["priority"] = curr_priority
                    
                    self._populate_dictionary_list()
                    self._mark_changes_pending()
                break

    def on_move_down(self, button, path):
        """Move dictionary down in priority."""
        sorted_dicts = self.parent.dict_manager.get_sorted_dictionaries()
        
        # Find current index
        for idx, (p, info) in enumerate(sorted_dicts):
            if p == path:
                if idx < len(sorted_dicts) - 1:
                    # Swap priorities
                    next_path = sorted_dicts[idx + 1][0]
                    curr_priority = info["priority"]
                    next_priority = sorted_dicts[idx + 1][1]["priority"]
                    
                    self.parent.dict_manager.dictionaries[path]["priority"] = next_priority
                    self.parent.dict_manager.dictionaries[next_path]["priority"] = curr_priority
                    
                    self._populate_dictionary_list()
                    self._mark_changes_pending()
                break

    def on_remove_dictionary(self, button, path):
        """Remove a dictionary smoothly without closing dialog."""
        # Remove from dictionary manager
        if path in self.parent.dict_manager.dictionaries:
            del self.parent.dict_manager.dictionaries[path]
            
            # Update priorities to fill gap
            sorted_dicts = sorted(
                self.parent.dict_manager.dictionaries.items(),
                key=lambda x: x[1]["priority"]
            )
            for idx, (p, info) in enumerate(sorted_dicts):
                info["priority"] = idx
            
            # Refresh list
            self._populate_dictionary_list()
            self._mark_changes_pending()

    def on_add_dictionary(self, button):
        """Add a new dictionary."""
        dialog = Gtk.FileDialog()
        
        # Create filter for DSL files
        filter_dsl = Gtk.FileFilter()
        filter_dsl.set_name("DSL Dictionary Files")
        filter_dsl.add_pattern("*.dsl")
        filter_dsl.add_pattern("*.dsl.dz")
        
        filter_list = Gio.ListStore.new(Gtk.FileFilter)
        filter_list.append(filter_dsl)
        dialog.set_filters(filter_list)
        
        dialog.open(self, None, self._on_add_dictionary_response)

    def _on_add_dictionary_response(self, dialog, result):
        """Handle file selection response."""
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            
            # Check if already loaded
            if path in self.parent.dict_manager.dictionaries:
                return
            
            # Load dictionary
            if self.parent.dict_manager.load_dictionary(path):
                self._populate_dictionary_list()
                self._mark_changes_pending()
                
        except GLib.Error:
            pass  # User cancelled

    def _mark_changes_pending(self):
        """Mark that changes need to be applied."""
        self.apply_btn.set_sensitive(True)

    def on_apply_changes(self, button):
        """Apply all pending changes."""
        # Save settings
        self.parent.save_settings()
        
        # Reload dictionaries
        self.parent.reload_dictionaries()
        
        # Reset apply button
        self.apply_btn.set_sensitive(False)


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
