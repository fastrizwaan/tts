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
        self._load_settings()

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

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(self.webview)
        vbox.append(scrolled)

        self.show_placeholder("Load a dictionary to start searching")

    def show_placeholder(self, text):
        html = f"<html><body><p style='margin:2em;color:#777;font-family:sans-serif'>{text}</p></body></html>"
        self.webview.load_html(html, "file:///")

    # ------------------- Search handler ----------------------

    def on_search(self, entry):
        q = entry.get_text().strip()
        if not q:
            self.show_placeholder("Enter a search term" if self.dict_manager.entries else "Load a dictionary first")
            return

        results = self.dict_manager.search(q)
        if not results:
            self.show_placeholder("No results found")
            return

        html = self.build_html(results)
        self.webview.load_html(html, "file:///")

    # ------------------- HTML rendering ----------------------

    def build_html(self, results):
        body = ""
        for word, dict_data in results[:100]:
            for dname, defs, color in dict_data:
                defs_html = ""
                in_list = False
                current_li_content = []
                
                for i, d in enumerate(defs):
                    # Extract content from [m1] to check if it's numbered
                    m1_content = re.search(r'\[m1\](.*?)\[/m\]', d.strip(), re.DOTALL)
                    if m1_content:
                        inner = m1_content.group(1).strip()
                        # Check if the content inside [m1] starts with a number
                        is_numbered_m1 = re.match(r'^\d+\.\s', inner)
                    else:
                        is_numbered_m1 = False
                    
                    # Check if it's a numbered definition without [m] tags
                    is_numbered = re.match(r'^\d+\.\s', d.strip())
                    
                    if is_numbered_m1 or is_numbered:
                        # Close previous list item if exists
                        if current_li_content:
                            defs_html += "<li>" + "".join(current_li_content) + "</li>"
                            current_li_content = []
                        
                        # Start new list if not already in one
                        if not in_list:
                            defs_html += "<ol>"
                            in_list = True
                        
                        # Add the numbered definition to current item
                        current_li_content.append(self.render_dsl_text(d, is_main=True))
                    else:
                        # This is a sub-item (example, synonym, etc.) - could be [m1]/[m2] or other
                        if in_list:
                            # Add to current list item with indentation
                            current_li_content.append(f"<div class='sub-item'>{self.render_dsl_text(d)}</div>")
                        else:
                            # Standalone content (like "noun")
                            defs_html += f"<div class='standalone'>{self.render_dsl_text(d)}</div>"
                
                # Close any remaining list item
                if current_li_content:
                    defs_html += "<li>" + "".join(current_li_content) + "</li>"
                if in_list:
                    defs_html += "</ol>"
                    
                block = f"""
                <div class="entry">
                  <div class="header">
                    <span class="lemma">{word}</span>
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
        .example { color: #228B22; font-style: italic; }
        ol { margin: 0.5em 0; padding-left: 2em; line-height: 1.6; list-style-position: outside; }
        li { margin-bottom: 0.8em; }
        .sub-item { margin-left: 0; margin-top: 0.3em; line-height: 1.4; }
        .standalone { margin: 0.3em 0; line-height: 1.4; font-weight: 500; }
        hr { border: none; border-top: 1px solid #ccc; margin: 10px 0; }
        """
        return f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"

    def render_dsl_text(self, raw, is_main=False):
        t = raw
        
        # Handle escaped characters first
        t = t.replace(r'\(', '(')
        t = t.replace(r'\)', ')')
        t = t.replace(r'\[', '[')
        t = t.replace(r'\]', ']')
        
        # Handle [m1], [m2] tags and extract content
        t = re.sub(r'\[m1\](.*?)\[/m\]', r'\1', t, flags=re.DOTALL)
        t = re.sub(r'\[m2\](.*?)\[/m\]', r'\1', t, flags=re.DOTALL)
        t = re.sub(r'\[m(\d+)\](.*?)\[/m\]', r'\2', t, flags=re.DOTALL)
        
        # Remove the number prefix if it's a main definition (we'll add it via <li>)
        if is_main:
            t = re.sub(r'^\d+\.\s+', '', t)
        
        # Handle [*] markers for examples/bullets
        t = re.sub(r'\[\*\](.*?)\[/\*\]', r'<span class="bullet-item">\1</span>', t, flags=re.DOTALL)
        
        t = re.sub(r"\[b\](.*?)\[/b\]", r"<b>\1</b>", t, flags=re.DOTALL)
        t = re.sub(r"\[i\](.*?)\[/i\]", r"<i>\1</i>", t, flags=re.DOTALL)
        t = re.sub(r"\[p\](.*?)\[/p\]", r'<span class="pos">\1</span>', t)
        t = re.sub(r"\[ex\](.*?)\[/ex\]", r'<span class="example">\1</span>', t)
        t = re.sub(r"\[c ([^\]]+)\](.*?)\[/c\]", r'<span style="color:\1">\2</span>', t)
        t = re.sub(r"<<(.*?)>>", r"<i>&laquo;\1&raquo;</i>", t)
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
