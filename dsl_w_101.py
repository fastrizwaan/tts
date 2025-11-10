#!/usr/bin/env python3
import gi, os, re, gzip, json, colorsys
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
    """GoldenDict-style HTML renderer for DSL lines, with tracing for substitutions."""
    def __init__(self, dark_mode=False, debug=False):
        self.dark_mode = dark_mode
        self.debug = debug

    def _dbg(self, tag, text):
        """Print debug info if debug is enabled."""
        if self.debug:
            print(f"\n{'='*20} {tag.upper()} {'='*20}\n{text}\n{'='*55}", flush=True)

    def render_entry(self, headword, defs):
        parts = []
        for line in defs:
            parts.append(self._render_line(line, headword))
        return "\n".join(parts)

    def _render_line(self, line, headword):
        line = line.replace("~", headword).strip()
        self._dbg("original", line)

        # --- Basic text styling ---
        line = re.sub(r"\[b\](.*?)\[/b\]", r"<b>\1</b>", line)
        line = re.sub(r"\[i\](.*?)\[/i\]", r"<i>\1</i>", line)
        line = re.sub(r"\[u\](.*?)\[/u\]", r"<u>\1</u>", line)
        line = re.sub(r"\[sup\](.*?)\[/sup\]", r"<sup>\1</sup>", line)
        line = re.sub(r"\[sub\](.*?)\[/sub\]", r"<sub>\1</sub>", line)
        self._dbg("after-basic", line)

        # --- POS tags ---
        def render_pos_tag(m):
            text = m.group(1).strip()
            if text.startswith("<") and text.endswith(">"):
                text = text[1:-1]
            return f"<span class='pos'>&lt;{text}&gt;</span>"
        line = re.sub(r"\[p\](.*?)\[/p\]", render_pos_tag, line, flags=re.DOTALL)
        self._dbg("after-pos", line)

        # --- Examples, translations, comments ---
        line = re.sub(r"\[ex\](.*?)\[/ex\]", r"<span class='example'>\1</span>", line)
        line = re.sub(r"\[trn\](.*?)\[/trn\]", r"\1", line)
        line = re.sub(r"\[com\](.*?)\[/com\]", r"\1", line)
        self._dbg("after-ex-com", line)

        # --- [m] Transliteration block ---
        def handle_m_tags(m):
            content = m.group(1).strip()
            content = re.sub(r"\[c\s+[^\]]+\](.*?)\[/c\]", r"\1", content, flags=re.DOTALL)

            parts = re.findall(r'/[^/]+/|[^/]+', content)
            translit_html, native_html = [], []
            for p in parts:
                if not p.strip():
                    continue
                if p.startswith("/") and p.endswith("/"):
                    translit_html.append(f"<span dir='ltr' class='translit'>{p}</span>")
                elif re.search(r'[\u0600-\u06FF\u0590-\u05FF\u0750-\u08FF]', p):
                    native_html.append(f"<span dir='rtl' class='native'>{p}</span>")
                else:
                    translit_html.append(p)

            translit = " ".join(translit_html)
            native = " ".join(native_html)
            rtl = bool(re.search(r'[\u0600-\u06FF\u0750-\u08FF\u0590-\u05FF]', content))
            if rtl:
                result = f"<div class='m-line'>{translit} <span class='m-tag'>&lt;m&gt;</span> {native}</div>"
            else:
                result = f"<div class='m-line'>{native} <span class='m-tag'>&lt;m&gt;</span> {translit}</div>"

            if self.debug:
                print(f"\n-- handle_m_tags({repr(content)}) -->\n{result}")
            return result

        line = re.sub(r"\[m\d*\](.*?)\[/m\d*\]", handle_m_tags, line, flags=re.DOTALL)
        self._dbg("after-m", line)

        # --- Color tags (after [m]) ---
        def colorize(m):
            color, text = m.group(1).strip(), m.group(2)
            rgba = Gdk.RGBA()
            if rgba.parse(color):
                color = "#{:02x}{:02x}{:02x}".format(
                    int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255)
                )
            if self.dark_mode:
                color = self._lighten(color, 1.4)
            return f"<span style='color:{color}'>{text}</span>"

        line = re.sub(r"\[c\s+([^\]]+)\](.*?)\[/c\]", colorize, line, flags=re.DOTALL)
        self._dbg("after-color", line)

        # --- Links ---
        line = re.sub(r"<<(.*?)>>", r'<a href="dict://\1" class="dict-link">\1</a>', line)
        line = re.sub(r"\[ref\](.*?)\[/ref\]", r'<a href="dict://\1" class="dict-link">\1</a>', line)
        self._dbg("after-links", line)

        # --- Auto RTL wrapping ---
        if re.search(r'[\u0600-\u06FF\u0750-\u08FF\u0590-\u05FF]', line):
            line = f"<div dir='rtl' class='rtl-line'>{line}</div>"
            self._dbg("after-rtl-wrap", line)

        return line

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
        .ltr-header {{ flex-direction: row; text-align: left; }}
        .rtl-header {{ flex-direction: row-reverse; text-align: right; }}

        .ltr-entry {{ text-align: left; }}
        .rtl-entry {{ text-align: right; direction: rtl; }}

        .defs {{
            margin-top: 2px;
            line-height: 1.45;
        }}
        .rtl-entry .defs {{
            text-align: right;
            direction: rtl;
        }}

        .pos {{
            color: {pos};
            font-style: italic;
        }}

        .example {{
            color: {example};
            font-style: italic;
        }}

        .rtl-line {{
            direction: rtl;
            text-align: right;
        }}

        .dict-link {{
            color: {link};
            text-decoration: none;
            cursor: pointer;
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
            white-space: nowrap;
            line-height: 1.4;
            margin: 2px 0;
        }}
        .m-tag {{
            opacity: 0.7;
            font-style: italic;
        }}
        .translit {{
            direction: ltr;
            unicode-bidi: isolate;
        }}
        .native {{
            direction: rtl;
            unicode-bidi: isolate;
        }}
        .translit {{
            direction: ltr;
            unicode-bidi: isolate;
            color: gray;
        }}
        .native {{
            direction: rtl;
            unicode-bidi: isolate;
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
        """Build full HTML for search results.
        Automatically flips layout and text direction when lemma is RTL (Arabic, Urdu, etc.)."""
        renderer = DSLRenderer(self.style_manager.get_dark())
        body = []

        # Regex for RTL language ranges (Arabic, Hebrew, Syriac, etc.)
        rtl_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u08FF\u0590-\u05FF]')

        for word, dict_data in results[:100]:
            # Detect if the headword (lemma) is RTL
            is_rtl_lemma = bool(rtl_pattern.search(word))

            for dname, defs, _ in dict_data:
                defs_html = renderer.render_entry(word, defs)

                # Apply RTL layout if lemma itself is RTL
                if is_rtl_lemma:
                    header_html = (
                        f"<div class='header rtl-header' dir='rtl'>"
                        f"<span class='dict'>📖 {dname}</span>"
                        f"<span class='lemma'>{word}</span>"
                        f"</div>"
                    )
                    entry_html = (
                        f"<div class='entry rtl-entry' dir='rtl'>"
                        f"{header_html}"
                        f"<div class='defs rtl-text'>{defs_html}</div>"
                        f"<hr></div>"
                    )
                else:
                    header_html = (
                        f"<div class='header ltr-header' dir='ltr'>"
                        f"<span class='lemma'>{word}</span>"
                        f"<span class='dict'>📖 {dname}</span>"
                        f"</div>"
                    )
                    entry_html = (
                        f"<div class='entry ltr-entry' dir='ltr'>"
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
            <style>
                .header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: baseline;
                    margin-bottom: 4px;
                }}
                .ltr-header {{ flex-direction: row; text-align: left; }}
                .rtl-header {{ flex-direction: row-reverse; text-align: right; }}
                .ltr-entry {{ text-align: left; }}
                .rtl-entry {{ text-align: right; direction: rtl; }}
                .rtl-text {{ direction: rtl; text-align: right; }}
            </style>
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

class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent, paths):
        super().__init__(transient_for=parent, modal=True, title="Settings")
        self.parent = parent
        page = Adw.PreferencesPage()
        self.add(page)
        group = Adw.PreferencesGroup(title="Dictionaries")
        page.add(group)

        add_row = Adw.ActionRow(title="Add Dictionary")
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.connect("clicked", self.add_dictionary)
        add_row.add_suffix(add_btn)
        group.add(add_row)

        for path in paths:
            info = parent.dict_manager.dictionaries[path]
            row = Adw.ActionRow(title=info["name"], subtitle=f"{len(info['entries'])} entries • {os.path.basename(path)}")
            rm_btn = Gtk.Button(icon_name="user-trash-symbolic")
            rm_btn.add_css_class("destructive-action")
            rm_btn.connect("clicked", self.remove_dictionary, path)
            row.add_suffix(rm_btn)
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

