# HIGH RAM usage still same as 135
#!/usr/bin/env python3
import gi, os, re, gzip, json, colorsys, html, threading
from pathlib import Path
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, WebKit

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

APP_NAME = "DSL Dictionary"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary"
CONFIG_FILE = CONFIG_DIR / "settings.json"
import time
INDEX_DIR = CONFIG_DIR / "indexes"
INDEX_DIR.mkdir(parents=True, exist_ok=True)



import io

class DictionaryIndexer:
    def __init__(self, path):
        self.path = Path(path)
        self.index_path = INDEX_DIR / (self.path.name + ".index.json")

import io, gzip, re, json, os
from pathlib import Path

class DictionaryIndexer:
    """Scans a DSL dictionary and builds a lightweight word→offset index."""

    def __init__(self, path):
        self.path = Path(path)
        self.index_path = INDEX_DIR / (self.path.name + ".index.json")

    # ============================================================
    # Build Index
    # ============================================================
    def build_index(self, on_progress=None):
        """Build JSON and n-gram index with full debug tracing."""
        import struct, json, re
        from pathlib import Path

        is_gzip = self.path.suffix == ".dz"
        total_size = self.path.stat().st_size
        entries, offsets_seen = [], set()

        def detect_encoding(raw):
            if raw.startswith(b"\xff\xfe"):
                return "utf-16-le"
            if raw.startswith(b"\xfe\xff"):
                return "utf-16-be"
            if raw.startswith(b"\xef\xbb\xbf"):
                return "utf-8-sig"
            if b"\x00" in raw[:64]:
                return "utf-16-le"
            return "utf-8"

        open_func = gzip.open if is_gzip else open
        with open_func(self.path, "rb") as fb:
            sample = fb.read(256)
            encoding = detect_encoding(sample)
            fb.seek(0)

            def decode_bytes(b):
                if encoding in ("utf-16-le", "utf-16-be") and len(b) % 2:
                    b = b[:-1]
                try:
                    return b.decode(encoding, "ignore")
                except Exception:
                    return b.decode("utf-8", "ignore")

            buffer = b""
            pos = 0
            while True:
                chunk = fb.read(4096)
                if not chunk:
                    break
                buffer += chunk

                while True:
                    if encoding == "utf-16-le":
                        nl = buffer.find(b"\n\x00")
                        step = 2
                    elif encoding == "utf-16-be":
                        nl = buffer.find(b"\x00\n")
                        step = 2
                    else:
                        nl = buffer.find(b"\n")
                        step = 1
                    if nl == -1:
                        break

                    line_bytes = buffer[: nl + step]
                    buffer = buffer[nl + step :]
                    line = decode_bytes(line_bytes).replace("\ufeff", "").replace("\u200b", "").rstrip("\r\n")
                    pos += len(line_bytes)
                    if not line or line.lstrip().startswith("#") or line[0].isspace() or line[0] == "-":
                        continue
                    word = line.strip()
                    if word and word not in offsets_seen:
                        entries.append({"word": word, "key": word.lower(), "offset": pos - len(line_bytes)})
                        offsets_seen.add(word)
                    if on_progress and total_size:
                        on_progress(min(1.0, pos / total_size))
                GLib.MainContext.default().iteration(False)

        # ---------- Build n-gram index ----------
        INDEX_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary" / "indexes"
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        ngram_index = {}

        for e in entries:
            w = e["word"].lower()
            norm = re.sub(r"^[^a-zA-Z]+", "", w)
            tokens = norm.split()
            if tokens:
                prefix = tokens[0]
                ngram_index.setdefault("^" + prefix, set()).add(e["word"])
            for n in range(1, 5):
                for i in range(len(w) - n + 1):
                    ngram_index.setdefault(w[i : i + n], set()).add(e["word"])

        ngram_path = self.index_path.with_name(self.index_path.stem + ".ngrams.json")
        ngram_json = {k: list(v) for k, v in ngram_index.items()}
        with open(ngram_path, "w", encoding="utf-8") as f:
            json.dump(ngram_json, f, ensure_ascii=True, separators=(",", ":"))

        print(f"[ASSERT] N-gram JSON: {ngram_path} (exists={ngram_path.exists()}, size={ngram_path.stat().st_size})")
        print(f"[ASSERT] Keys in n-gram index: {len(ngram_json)}")
        for key in ["as", "as ", " as", "^as"]:
            print(f"[ASSERT] Key '{key}' present:", key in ngram_json)

        idx_data = {
            "file": str(self.path),
            "mtime": self.path.stat().st_mtime,
            "size": total_size,
            "encoding": encoding,
            "entries": entries,
        }
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(idx_data, f, ensure_ascii=True, separators=(",", ":"))
        print(f"[DEBUG] Saved UTF-8 index with {len(entries)} entries → {self.index_path}")
        return idx_data




    def load_index(self):
        if not self.index_path.exists():
            return None
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stat = self.path.stat()
            if (
                abs(data.get("mtime", 0) - stat.st_mtime) < 2
                and data.get("size") == stat.st_size
            ):
                return data
        except Exception as e:
            print(f"[DEBUG] Failed to load index: {e}")
        return None


#################################
class IndexProgressDialog(Adw.Window):
    def __init__(self, parent, dict_path, dict_name, on_done):
        super().__init__(transient_for=parent, modal=True, title=f"Indexing {dict_name}")
        self.set_default_size(420, 120)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        self.set_content(vbox)

        self.label = Gtk.Label(label=f"Indexing dictionary: {dict_name}")
        vbox.append(self.label)

        self.progress = Gtk.ProgressBar()
        vbox.append(self.progress)

        self.on_done = on_done
        self.path = dict_path
        self.name = dict_name
        self.show()

        GLib.idle_add(self._start_indexing)

    def _start_indexing(self):
        indexer = DictionaryIndexer(self.path)

        def on_progress(fraction):
            GLib.idle_add(self._update_progress, fraction)

        idx_data = indexer.build_index(on_progress=on_progress)
        GLib.idle_add(self._finish, idx_data)
        return False

    def _update_progress(self, fraction):
        self.progress.set_fraction(fraction)
        self.progress.set_text(f"{fraction*100:.1f}%")
        return False

    def _finish(self, idx_data):
        self.close()
        self.on_done(idx_data)
        return False

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

        for lineno, raw in enumerate(self.text.splitlines(), 1):
            line = raw.rstrip("\n\r")

            # Debug each line
            if line.lstrip().startswith("#"):
                print(f"[DEBUG] Skipping header/comment line {lineno}: {line!r}")
                continue

            if not line:
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
        print(f"[DEBUG] Parsed {len(entries)} entries total.")
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
                    "dimgray": "slategray",
                    "darkslateblue": "slateblue",
                    "purple": "mediumorchid",
                    "azure": "deepskyblue",
                    "aliceblue": "steelblue",
                    "sienna": "orange",
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

        # Interpret single '\' (possibly with spaces) as a blank line separator
        if re.fullmatch(r'\s*\\\s*', line):
            return "<div style='margin-top:0.5em'></div>"
            
        # Replace tilde (~) with lemma
        line = line.replace("~", html.escape(headword))
        
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
            # Unescape escaped brackets and punctuation before HTML escaping
            txt = (
                txt.replace(r"\[", "[")
                   .replace(r"\]", "]")
                   .replace(r"\(", "(")
                   .replace(r"\)", ")")
                   .replace(r"\{", "{")
                   .replace(r"\}", "}")
                   .replace(r"\~", "~")
                   .replace(r"\/", "/")
            )

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
    """Handles dictionary indexing, caching and search."""

    def __init__(self):
        self.index_cache = {}
        self.loaded_dicts = {}

    # ------------------------------------------------------------
    def load_dictionary(self, path, parent_window=None, *args):
        """Load dictionary index + preload only short (≤4-char) n-grams."""
        from gi.repository import Gtk, GLib, Adw
        import json, re, time
        from pathlib import Path

        path = Path(path)
        if not path.exists():
            print(f"[ERROR] Dictionary file not found: {path}")
            return

        indexer = DictionaryIndexer(path)
        idx_path = indexer.index_path
        idx_data = None
        rebuild_index = True

        # --- Try existing index first ---
        if idx_path.exists():
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    idx_data = json.load(f)
                st = path.stat()
                if abs(idx_data.get("mtime", 0) - st.st_mtime) < 2 and idx_data.get("size") == st.st_size:
                    rebuild_index = False
            except Exception as e:
                print(f"[WARN] Failed to read index: {e}")

        # --- Build if needed ---
        if rebuild_index:
            print(f"[INFO] Rebuilding index for {path.name}")
            idx_data = indexer.build_index()
        self.index_cache[str(path)] = idx_data

        # --- Fast lookup maps ---
        em, wm = {}, {}
        for e in idx_data["entries"]:
            k = e.get("key") or e["word"].lower()
            em[k] = e
            wm[e["word"].lower()] = e
        idx_data["_exact_map"] = em
        idx_data["_word_map"] = wm

        # --- Detect dictionary name ---
        denc = idx_data.get("encoding", "utf-8")
        name = path.stem
        try:
            with open(path, "rb") as f:
                txt = f.read(256).decode(denc, "ignore")
                m = re.search(r'#NAME\s+"(.+?)"', txt)
                if m:
                    name = m.group(1)
        except Exception:
            pass

        self.loaded_dicts[path] = {"path": path, "name": name, "encoding": denc}

        print(f"[INFO] Loaded {name} ({len(em)} keys)")

        # --- Hybrid n-gram cache: load only short keys (≤4 letters) ---
        INDEX_DIR = Path(GLib.get_user_config_dir()) / "dsl-dictionary" / "indexes"
        ngr_path = next(
            (p for p in [
                INDEX_DIR / f"{path.name}.ngrams.json",
                INDEX_DIR / f"{path.name}.index.ngrams.json",
            ] if p.exists()),
            None,
        )

        if not hasattr(self, "_ngram_cache"):
            self._ngram_cache = {}

        if ngr_path:
            short_keys = {}
            start = time.time()
            try:
                with open(ngr_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                for k, v in obj.items():
                    if len(k.strip("^ ").strip()) <= 4:
                        short_keys[k] = v
                del obj
                print(f"[PRELOAD] {name}: short n-grams loaded ({len(short_keys)} keys) in {(time.time()-start)*1000:.1f} ms")
            except Exception as e:
                print(f"[WARN] Partial n-gram load failed for {name}: {e}")
                short_keys = {}
            self._ngram_cache[str(ngr_path)] = short_keys
            idx_data["_ngram_path"] = str(ngr_path)
        else:
            print(f"[INFO] {name}: no n-gram file found")
            idx_data["_ngram_path"] = None

        return idx_data

    
    # ------------------------------------------------------------
    # ------------------------------------------------------------
    def search(self, q, exact_only=True, max_preview=300, with_defs=False):
        """Fast search using hybrid n-gram cache (short keys in memory)."""
        import json, re, time
        from pathlib import Path
        from itertools import islice

        q = q.strip().lower()
        if not q:
            return []
        results, seen, candidate_hits = [], set(), []
        print(f"[DEBUG] Searching '{q}' (exact_only={exact_only})")

        # --- Exact matches first ---
        for dp, idx in self.index_cache.items():
            em = idx.get("_exact_map", {})
            if q in em:
                e = em[q]
                dname = next((ld["name"] for ld in self.loaded_dicts.values() if Path(ld["path"]) == Path(dp)), Path(dp).stem)
                candidate_hits.append((0, e["word"], dname, e, idx))
                seen.add(q)
                print(f"[DEBUG] Exact hit: {e['word']} from {dname}")

        if exact_only:
            return [
                (w, [(d, self._read_entry(Path(i['file']), e, i.get('encoding','utf-8')) if with_defs else [], "default")])
                for _, w, d, e, i in candidate_hits
            ]

        # --- Fuzzy / substring search ---
        for dp, idx in self.index_cache.items():
            start_t = time.time()
            dname = next((ld["name"] for ld in self.loaded_dicts.values() if Path(ld["path"]) == Path(dp)), Path(dp).stem)
            ngram_path = idx.get("_ngram_path")
            tier_candidates = {1: set(), 2: set(), 3: set()}

            # Use short-key cache if available
            short_cache = self._ngram_cache.get(str(ngram_path), {}) if ngram_path else {}
            found_keys = []

            if short_cache and any(k in short_cache for k in [q, f"^{q}", f" {q} ", f" {q}", f"{q} "]):
                for key in [f"^{q}", f" {q} ", f" {q}", f"{q} ", q]:
                    if key in short_cache:
                        v = short_cache[key]
                        if key.startswith("^"):
                            tier_candidates[1].update(v)
                        elif key.strip().startswith(" "):
                            tier_candidates[2].update(v)
                        else:
                            tier_candidates[3].update(v)
                        found_keys.append(key)
                print(f"[CACHE] {dname}: served '{q}' from short-key cache ({len(found_keys)} keys)")
            elif ngram_path:
                # Lazy load only when needed (for longer keys)
                try:
                    with open(ngram_path, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    for key in [f"^{q}", f" {q} ", f" {q}", f"{q} ", q]:
                        if key in obj:
                            v = obj[key]
                            if key.startswith("^"):
                                tier_candidates[1].update(v)
                            elif key.strip().startswith(" "):
                                tier_candidates[2].update(v)
                            else:
                                tier_candidates[3].update(v)
                    del obj
                    print(f"[LOAD] {dname}: lazily loaded full n-gram file for '{q}'")
                except Exception as e:
                    print(f"[WARN] {dname}: failed to read n-gram for '{q}': {e}")

            else:
                # fallback substring scan
                pat = re.compile(re.escape(q), re.I)
                for e in idx.get("entries", []):
                    key = e.get("key") or e["word"].lower()
                    if pat.search(key):
                        if key.startswith(q):
                            tier_candidates[1].add(e["word"])
                        elif f" {q} " in key:
                            tier_candidates[2].add(e["word"])
                        else:
                            tier_candidates[3].add(e["word"])

            total = sum(len(t) for t in tier_candidates.values())
            print(f"[DEBUG] {dname}: {total} candidates for '{q}'")

            wm = idx.get("_word_map", {})
            for tier in [1, 2, 3]:
                for w in islice(tier_candidates[tier], 500 if tier == 1 else 300):
                    wl = w.lower()
                    if wl in seen:
                        continue
                    e = wm.get(wl)
                    if not e:
                        continue
                    seen.add(wl)
                    candidate_hits.append((tier, e["word"], dname, e, idx))
                    if len(candidate_hits) >= max_preview * 3:
                        break
                if len(candidate_hits) >= max_preview * 3:
                    break
            print(f"[TIMING] {dname}: {(time.time()-start_t)*1000:.1f} ms")

        candidate_hits.sort(key=lambda x: (x[0], x[1].lower()))
        candidate_hits = candidate_hits[:max_preview]

        for _, word, dname, e, idx in candidate_hits:
            enc = idx.get("encoding", "utf-8")
            defs = self._read_entry(Path(idx["file"]), e, enc) if with_defs else []
            results.append((word, [(dname, defs, "default")]))
        print(f"[DEBUG] Returning {len(results)} results for '{q}'")
        return results






    # ------------------------------------------------------------
    def _read_entry(self, path, entry, encoding="utf-8"):
        """Read one DSL entry by byte offset (supports .dsl and .dsl.dz)."""
        import gzip
        offset = entry["offset"]
        lines = []
        path = Path(path)
        try:
            if path.suffix == ".dz":
                with gzip.open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read(4096)
            else:
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read(4096)
            text = data.decode(encoding, errors="ignore")
            for line in text.splitlines():
                if not line.strip():
                    continue
                if not line[0].isspace() and line != entry["word"]:
                    break
                lines.append(line.rstrip())
        except Exception as e:
            print(f"[WARN] Failed to read entry at offset {offset}: {e}")
        return lines








# ============================================================
#  MAIN WINDOW
# ============================================================

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(900, 600)
        self.dict_manager = DictionaryManager()
        from queue import Queue
        self._search_queue = Queue()
        self._worker_thread = threading.Thread(target=self._search_worker_loop, daemon=True)
        self._worker_thread.start()
        self._search_timeout_id = None
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_theme_changed)
        self._build_ui()
        self._apply_theme_to_webview()
        self.show_placeholder("Load a dictionary to start searching")
        self._load_settings()
        self._search_id = 0

    # ---------------- UI ----------------
    def _search_worker_loop(self):
        while True:
            item = self._search_queue.get()
            if item is None:
                break
            search_id, query = item
            if not query.strip():
                continue

            self._stop_flag = threading.Event()
            self._active_query = query.lower().strip()

            # Phase 1: exact matches
            results = self.dict_manager.search(query, exact_only=True, with_defs=True)
            exact_words = {word.lower() for word, _ in results}  # Track exact matches
            if not self._stop_flag.is_set() and search_id == self._search_id:
                GLib.idle_add(self._on_search_results, query, results, True)

            # Phase 2: async substring matches (may take longer)
            def async_subsearch(local_search_id=search_id, local_query=query, local_exact_words=exact_words):
                results2 = self.dict_manager.search(local_query, exact_only=False, with_defs=True)
                print(f"[DEBUG] Phase 2: Got {len(results2)} total results before filtering")
                # Filter out exact matches that were already shown in Phase 1
                filtered_results = [(word, data) for word, data in results2 if word.lower() not in local_exact_words]
                print(f"[DEBUG] Phase 2: After filtering exact matches, {len(filtered_results)} results remain")
                
                # Sample some filtered results for debugging
                if filtered_results and local_query.lower() == "as":
                    sample = [word for word, _ in filtered_results[:10]]
                    print(f"[DEBUG] Phase 2: First 10 filtered results: {sample}")
                
                # --- SAFETY CHECK: discard if outdated ---
                if (
                    not self._stop_flag.is_set()
                    and local_search_id == self._search_id
                    and local_query.lower().strip() == self._active_query
                ):
                    if filtered_results:  # Only append if there are new results
                        GLib.idle_add(self._on_search_results, local_query, filtered_results, False)
                    GLib.idle_add(self.spinner.stop)
                else:
                    print(f"[DEBUG] Discarded stale search results for '{local_query}'")

            threading.Thread(target=async_subsearch, daemon=True).start()





    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(vbox)
        header = Adw.HeaderBar()
        vbox.append(header)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search dictionary…")
        self.search_entry.connect("search-changed", self.on_search)
        header.set_title_widget(self.search_entry)
        self.spinner = Gtk.Spinner(spinning=False)
        header.pack_end(self.spinner)

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

    # ------------------------------------------------------------
    def on_search(self, entry):
        q = entry.get_text().strip()
        if self._search_timeout_id:
            GLib.source_remove(self._search_timeout_id)
            self._search_timeout_id = None

        if not q:
            self.show_placeholder("Enter a word")
            return

        self._search_id += 1  # invalidate all old searches
        self._last_html = None  # drop stale results

        def delayed_start():
            self._start_search_thread(q)
            self._search_timeout_id = None
            return GLib.SOURCE_REMOVE

        self._search_timeout_id = GLib.timeout_add(1000, delayed_start)


    # ------------------------------------------------------------
    def _start_search_thread(self, query):
        if hasattr(self, "_stop_flag"):
            self._stop_flag.set()
        try:
            while not self._search_queue.empty():
                self._search_queue.get_nowait()
        except Exception:
            pass

        self._search_id += 1  # new unique search token
        search_id = self._search_id

        self.spinner.start()
        self.show_placeholder(f"Searching “{query}”…")
        self._search_queue.put((search_id, query))





    # ------------------------------------------------------------
    def _on_search_results(self, query, results, replace=True):
        """Render results in WebView; replace or append without reloading."""
        if query.strip().lower() != getattr(self, "_active_query", ""):
            return
        if self._search_id != getattr(self, "_search_id", 0):
            return

        if not results:
            if replace:
                self.show_placeholder(f"No results for “{GLib.markup_escape_text(query)}”")
            return

        if replace:
            # Phase 1: full reload with new HTML
            html = self.build_html(results)
            self.webview.load_html(html, "file:///")
            self._last_html = results
            return

        # --------------------------------------------------------
        # Phase 2: append new results without reload
        # --------------------------------------------------------
        append_renderer = DSLRenderer(self.style_manager.get_dark())
        appended_html_parts = []

        for word, dict_data in results:
            for dname, defs, _ in dict_data:
                defs_html = append_renderer.render_entry(word, defs)

                # --- PATCH: ensure phrases without defs still show ---
                if not defs_html.strip():
                    defs_html = (
                        "<div class='placeholder-def'>"
                        "<i>No definition (phrase or cross-reference)</i>"
                        "</div>"
                    )

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
                appended_html_parts.append(entry_html)

        appended_html = "".join(appended_html_parts)

        # CSS for placeholder if not already defined
        placeholder_style = """
            const style = document.getElementById('placeholder-style');
            if (!style) {
                const s = document.createElement('style');
                s.id = 'placeholder-style';
                s.textContent = `
                    .placeholder-def {
                        color: var(--placeholder-fg, #777);
                        font-style: italic;
                        margin-top: 0.25em;
                    }
                `;
                document.head.appendChild(s);
            }
        """

        # Append HTML dynamically
        js_code = f"""
            {placeholder_style}
            const container = document.body;
            const temp = document.createElement('div');
            temp.innerHTML = `{appended_html}`;
            container.append(...temp.children);
        """

        self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        self._last_html = (self._last_html or []) + results





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

                # --- PATCH: Show phrase entries even if no definition ---
                if not defs_html.strip():
                    defs_html = (
                        "<div class='placeholder-def'>"
                        "<i>No definition (phrase or cross-reference)</i>"
                        "</div>"
                    )

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
            <style>
                .placeholder-def {{
                    color: {'#aaa' if dark else '#555'};
                    font-style: italic;
                    margin-top: 0.25em;
                }}
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
                meta = self._extract_dsl_metadata(path)
                name = meta.get("name", GLib.path_get_basename(path))
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
