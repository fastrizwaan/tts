#!/usr/bin/env python3
import gi, os, re, gzip, json
from pathlib import Path

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
        entries, headwords, defs = {}, [], []
        in_def = False

        def flush():
            if headwords and defs:
                for w in headwords:
                    entries.setdefault(w, []).extend(defs)

        for raw in content.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue
            if line == "-":
                flush()
                headwords, defs, in_def = [], [], False
                continue
            if raw[:1].isspace():
                in_def = True
                cleaned = raw.lstrip()
                if cleaned:
                    defs.append(cleaned)
                continue
            if in_def:
                flush()
                headwords, defs, in_def = [], [], False
            headwords = [line]
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
        self._build_ui()
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_theme_changed)
        self._apply_theme_to_webview()

        self._load_settings()

    def on_theme_changed(self, *_):
        """Called when the system or app theme changes."""
        self._apply_theme_to_webview()

    def _apply_theme_to_webview(self):
        """Sync WebKit WebView colors with current Adwaita theme."""
        dark = self.style_manager.get_dark()
        settings = self.webview.get_settings()

        # Tell WebKit to honor prefers-color-scheme media query
        try:
            settings.set_property("enable-dark-mode", dark)
        except TypeError:
            pass  # older builds may not support it

        # Update the in-page CSS dynamically
        theme_css = """
        :root {
            --bg: VAR_BG;
            --fg: VAR_FG;
            --link: VAR_LINK;
            --border: VAR_BORDER;
        }
        body {
            background-color: var(--bg);
            color: var(--fg);
        }
        .lemma { font-size: 1.3em; font-weight: bold; color: var(--link); }
        a { font-weight: bold; color: var(--link); }
        hr { border-color: var(--border); }
        """

        if dark:
            theme_css = theme_css.replace("VAR_BG", "#1e1e1e")
            theme_css = theme_css.replace("VAR_FG", "#dddddd")
            theme_css = theme_css.replace("VAR_LINK", "#89b4ff")
            theme_css = theme_css.replace("VAR_BORDER", "#444")
        else:
            theme_css = theme_css.replace("VAR_BG", "#ffffff")
            theme_css = theme_css.replace("VAR_FG", "#222222")
            theme_css = theme_css.replace("VAR_LINK", "#005bbb")
            theme_css = theme_css.replace("VAR_BORDER", "#ccc")

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
        dark = self.style_manager.get_dark() if hasattr(self, "style_manager") else False

        bg = "#1e1e1e" if dark else "#ffffff"
        fg = "#dddddd" if dark else "#222222"
        hint = "#aaaaaa" if dark else "#777777"

        html = """
        <html>
        <head>
        <meta charset='utf-8'>
        <meta name='color-scheme' content='light dark'>
        <style id='theme-style'>
        body {{
            font-family: system-ui, sans-serif;
            margin: 12px;
        }}
        @media (prefers-color-scheme: dark) {{
            body {{
                background-color: #1e1e1e !important;
                color: #dddddd !important;
            }}
            a, .lemma {{ color: #89b4ff !important; }}
            hr {{ border-color: #444 !important; }}
        }}
        </style>
        </head>
        <body>{body}{script}</body>
        </html>
        """


        self.webview.load_html(html, "file:///")

        # Ensure the injected style updates when the theme toggles dynamically
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

        html = self.build_html(results)
        self.webview.load_html(html, "file:///")


    def on_search(self, entry):
        q = entry.get_text()
        self.perform_search(q)

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

                    # Detect "separator" like ————
                    # (after removing [mX]...[/m] wrapper)
                    sep_check = re.sub(r'^\[m\d+\](.*?)\[/m\]$', r'\1', d_stripped, flags=re.DOTALL).strip()
                    is_separator = bool(re.fullmatch(r'[—\-]{3,}', sep_check))

                    # If a new top-level header ([m1]) or a separator arrives, close any open list
                    if lvl == 1 or is_separator:
                        if current_li_content:
                            defs_html += "<li>" + "".join(current_li_content) + "</li>"
                            current_li_content = []
                        if in_list:
                            defs_html += "</ol>"
                            in_list = False

                        # Render the header/separator as standalone block and continue
                        if not is_separator:
                            defs_html += f"<div class='standalone'>{self.render_dsl_text(d, headword=word)}</div>"
                        else:
                            defs_html += "<hr>"
                        continue

                    # ----- Numbering detection -----
                    # Extract content inside [mX] ... [/m] if present
                    m_content = re.search(r'\[m\d+\](.*?)\[/m\]', d_stripped, re.DOTALL)
                    if m_content:
                        inner = m_content.group(1).strip()
                        is_numbered_m = (
                            re.match(r'^\d+[\.\)》]\s', inner) or
                            re.match(r'^\[([biu])\]\d+[\.\)》]\[/\1\]\s*', inner) or
                            ('■' in inner[:10])
                        )
                    else:
                        is_numbered_m = False

                    is_numbered = bool(re.match(r'^\d+[\.\)》]\s', d_stripped))

                    if is_numbered_m or is_numbered:
                        # Close previous list item if we were collecting sub-items
                        if current_li_content:
                            defs_html += "<li>" + "".join(current_li_content) + "</li>"
                            current_li_content = []
                        # Start a fresh list if needed
                        if not in_list:
                            defs_html += "<ol>"
                            in_list = True
                        # Main numbered line content
                        current_li_content.append(self.render_dsl_text(d, is_main=True, headword=word))
                    else:
                        # Sub-item (examples, notes, etc.)
                        if in_list:
                            current_li_content.append(
                                f"<div class='sub-item'>{self.render_dsl_text(d, headword=word)}</div>"
                            )
                        else:
                            defs_html += f"<div class='standalone'>{self.render_dsl_text(d, headword=word)}</div>"

                
                # Close any remaining list item
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

        css = """
        body {
            font-family: system-ui, sans-serif;
            background: transparent;
            color: #222;
            margin: 12px;
        }
        .lemma { font-size: 1.3em; font-weight: bold; color: #005bbb; }
        .dict { float: right; font-size: 0.9em; color: #666; }
        .pos { color: #228B22; font-style: italic; }
        .example { color: #228B22; font-style: italic; }
        .dict-link { color: #005bbb; text-decoration: none; cursor: pointer; }
        .dict-link:hover { text-decoration: underline; }
        ol { margin: 0.5em 0; padding-left: 2em; line-height: 1.6; list-style-position: outside; }
        li { margin-bottom: 0.8em; }
        .sub-item { margin-left: 0; margin-top: 0.3em; line-height: 1.4; }
        .standalone { margin: 0.3em 0; line-height: 1.4; font-weight: 500; }
        hr { border: none; border-top: 1px solid #ccc; margin: 10px 0; }
        """
        script = ""
        return f"""
        <html>
        <head>
        <meta charset='utf-8'>
        <meta name='color-scheme' content='light dark'>
        <style id='theme-style'>
        /* ----- Base (Light) Theme ----- */
        body {{
            font-family: system-ui, sans-serif;
            background: #ffffff;
            color: #222;
            margin: 12px;
        }}
        .lemma {{ font-size: 1.3em; font-weight: bold; color: #005bbb; }}
        .dict {{ float: right; font-size: 0.9em; color: #666; }}
        .pos {{ color: #228B22; font-style: italic; }}
        .example {{ color: #228B22; font-style: italic; }}
        .dict-link {{ color: #005bbb; text-decoration: none; cursor: pointer; }}
        .dict-link:hover {{ text-decoration: underline; }}
        ol {{ margin: 0.5em 0; padding-left: 2em; line-height: 1.6; list-style-position: outside; }}
        li {{ margin-bottom: 0.8em; }}
        .sub-item {{ margin-left: 0; margin-top: 0.3em; line-height: 1.4; }}
        .standalone {{ margin: 0.3em 0; line-height: 1.4; font-weight: 500; }}
        hr {{ border: none; border-top: 1px solid #ccc; margin: 10px 0; }}

        /* ----- Dark Theme Overrides ----- */
        @media (prefers-color-scheme: dark) {{
            body {{
                background-color: #1e1e1e;
                color: #dddddd;
                margin: 12px;
            }}
            .lemma {{  font-size: 1.3em; font-weight: bold; color: #89b4ff;}}
            .dict-link {{ color: #89b4ff; }}
            .dict {{ float: right; font-size: 0.9em; color: #aaa; }}
            .pos, .example {{ color: #9ae59a; font-style: italic; }}
            hr {{ border-top: 1px solid #444; }}
        }}
        </style>
        </head>
        <body>{body}{script}</body>
        </html>
        """




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
        t = re.sub(r"\[c\s+([^\]]+)\](.*?)\[/c\]", r'<span style="color:\1">\2</span>', t, flags=re.DOTALL)
        
        # Handle links <<word>> - use custom dict:// URI scheme
        t = re.sub(r"<<(.*?)>>", r'<a href="dict://\1" class="dict-link">\1</a>', t)
        
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
