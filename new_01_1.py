#!/usr/bin/env python3
"""
epubviewer_fixed_webview.py

Complete standalone GTK4 + Adw EPUB viewer with WebKit WebView (fallback to TextView)
that extracts EPUB to a temp dir, builds a TOC, and renders EPUB documents into the webview.

Dependencies:
 - PyGObject (Gtk4, Adw, WebKit6 optional)
 - ebooklib
 - beautifulsoup4
"""
import os
import sys
import re
import tempfile
import shutil
import zipfile
import urllib.parse
import traceback

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    # WebKit may not be present on all systems; we will attempt to load it later.
    from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk
except Exception as e:
    print("PyGObject (Gtk4/Adw) import failed:", e)
    sys.exit(1)

try:
    from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE
except Exception as e:
    print("ebooklib import failed:", e)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup, Comment
except Exception as e:
    print("beautifulsoup4 import failed:", e)
    sys.exit(1)

Adw.init()

# Simple sidebar CSS
_FOLIATE_CSS = b"""
.sidebar-toc { background-color: @surface; padding-top: 6px; padding-bottom: 6px; }
.toc-contents-label { padding-left: 12px; padding-right: 12px; padding-bottom: 6px; font-weight: 600; }
.toc-expander-row { min-height: 30px; padding-top: 4px; padding-bottom: 4px; border-radius: 10px; margin-right: 4px; }
.toc-leaf { min-height: 30px; border-radius: 8px; margin-right: 4px; padding-left: 8px; padding-top: 4px; padding-bottom: 4px; }
.toc-chev { margin-left: 2px; margin-right: 8px; }
.adw-action-row:hover { background-color: rgba(0,0,0,0.03); }
.toc-active { background-color: rgba(20, 80, 160, 0.08); border-radius: 6px; }
"""

def safe_decode_bytes(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except Exception:
        return b.decode("utf-8", errors="ignore")

class EPubViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="EPUB Viewer")
        self.set_default_size(1100, 720)

        # Apply CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(_FOLIATE_CSS)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # state
        self.book = None
        self.book_path = None
        self.temp_dir = None
        self.items = []
        self.item_map = {}
        self.image_names = {}
        self.image_basenames = {}
        self._row_map = {}
        self._active_href = None
        self._user_hid_sidebar = False
        self._responsive_enabled = False
        self.current_index = 0

        # split view and TOC container
        self.split = Adw.OverlaySplitView(show_sidebar=False)
        self.set_content(self.split)

        self._toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        try:
            self._toc_box.add_css_class("sidebar-toc")
        except Exception:
            pass
        self._toc_box.set_margin_top(6); self._toc_box.set_margin_bottom(6)
        self._toc_box.set_margin_start(6); self._toc_box.set_margin_end(6)

        self._toc_scroller = Gtk.ScrolledWindow()
        try:
            self._toc_scroller.set_min_content_width(320)
        except Exception:
            pass
        self._toc_scroller.set_child(self._toc_box)
        self.split.set_sidebar(self._toc_scroller)

        # header + content area
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Gtk.Label(label="Epub Viewer"))
        self.toolbar.add_top_bar(self.header)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_placeholder = Gtk.Label(label="Library — open an EPUB to start reading")
        self.content_box.append(self.content_placeholder)
        self.toolbar.set_content(self.content_box)

        self.split.set_content(self.toolbar)

        # create webview (with fallback)
        self.WebKit = None
        self.webview = None
        self.textview = None
        self._create_webview_or_fallback()

        # header actions
        self._build_header_actions()

        # connect sizing/responsiveness
        self.connect("notify::default-width", self._on_window_size_changed)
        self.connect("notify::default-height", self._on_window_size_changed)

    def _create_webview_or_fallback(self):
        """Attempt to create WebKit.WebView; fallback to TextView if unavailable."""
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit
            self.WebKit = WebKit
            # scrolled container for view
            self.scrolled = Gtk.ScrolledWindow()
            try:
                self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            except Exception:
                pass
            self.webview = WebKit.WebView()
            # connect console messages for debugging
            try:
                self.webview.connect("console-message", self._on_webconsole_message)
            except Exception:
                pass
            # connect navigation policy
            try:
                self.webview.connect("decide-policy", self.on_decide_policy)
            except Exception:
                pass
            self.scrolled.set_child(self.webview)
            # replace placeholder content_box child with scrolled webview
            self._clear_container(self.content_box)
            self.content_box.append(self.scrolled)
        except Exception:
            # fallback: TextView
            self.WebKit = None
            self.webview = None
            self.textview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
            self.scrolled = Gtk.ScrolledWindow()
            self.scrolled.set_child(self.textview)
            self._clear_container(self.content_box)
            self.content_box.append(self.scrolled)

    def _on_webconsole_message(self, webview, message, line, source_id):
        # why: useful when debugging injected JS inside webview
        print(f"[WebConsole] {message} (line {line}) source={source_id}")

    def _build_header_actions(self):
        load_btn = Gtk.Button.new_with_label("Open EPUB")
        load_btn.connect("clicked", self._on_open_clicked)
        self.header.pack_start(load_btn)

        close_btn = Gtk.Button.new_with_label("Close Book")
        close_btn.connect("clicked", lambda *_: self.set_library_mode())
        self.header.pack_end(close_btn)

        toggle_btn = Gtk.Button.new_with_label("Toggle Sidebar")
        toggle_btn.connect("clicked", lambda *_: self._on_sidebar_toggle())
        self.header.pack_end(toggle_btn)

    def _on_sidebar_toggle(self):
        try:
            new = not self.split.get_show_sidebar()
            self.split.set_show_sidebar(new)
            self._user_hid_sidebar = not new
        except Exception:
            pass

    def _on_window_size_changed(self, *args):
        try:
            if self._user_hid_sidebar:
                return
            width = self.get_width()
            is_narrow = width < 768
            if self._responsive_enabled and self.book and self.book_path:
                if is_narrow:
                    self.split.set_collapsed(True)
                else:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(True)
            else:
                if is_narrow is not None:
                    self.split.set_collapsed(False)
                    self.split.set_show_sidebar(False)
        except Exception:
            pass

    def set_library_mode(self):
        """Reset to library state and cleanup temporary extraction."""
        self.book = None
        self.book_path = None
        self._disable_responsive_sidebar()
        try:
            self.split.set_show_sidebar(False)
            self.split.set_collapsed(True)
        except Exception:
            pass
        self._clear_container(self.content_box)
        self.content_placeholder = Gtk.Label(label="Library — open an EPUB to start reading")
        self.content_box.append(self.content_placeholder)
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception:
            pass
        self.temp_dir = None
        # recreate webview in case WebKit became available/unavailable
        self._create_webview_or_fallback()

    def _clear_container(self, container):
        child = container.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            try:
                container.remove(child)
            except Exception:
                pass
            child = next_child

    # ---------- File dialog ----------
    def _on_open_clicked(self, *_):
        dialog = Gtk.FileDialog(title="Open EPUB")
        filter_epub = Gtk.FileFilter()
        filter_epub.set_name("EPUB"); filter_epub.add_pattern("*.epub")
        dialog.set_default_filter(filter_epub)
        def on_file_chosen(dlg, res, *a):
            try:
                f = dlg.open_finish(res)
                if f:
                    path = f.get_path()
                    if path:
                        self.load_epub_file(path)
            except Exception as e:
                self._show_error(f"Failed to open file: {e}")
        dialog.open(self, None, on_file_chosen)

    # ---------- EPUB loading ----------
    def load_epub_file(self, path):
        """Read EPUB, extract resources, build items list and TOC, then show first document."""
        try:
            self.book = epub.read_epub(path)
            self.book_path = path
        except Exception as e:
            self._show_error(f"Failed to read EPUB: {e}")
            return

        # build ordered items from spine or fallback
        docs = [i for i in self.book.get_items() if i.get_type() == ITEM_DOCUMENT]
        try:
            spine = getattr(self.book, "spine", None) or []
            id_map = {}
            for it in docs:
                iid = getattr(it, "id", None) or getattr(it, "get_id", lambda: None)()
                if not iid:
                    iid = it.get_name() or os.urandom(8).hex()
                id_map[iid] = it
            ordered = []
            for entry in spine:
                sid = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
                if sid in id_map:
                    ordered.append(id_map.pop(sid))
            ordered.extend(id_map.values())
            self.items = ordered if ordered else docs
        except Exception:
            self.items = docs

        if not self.items:
            self._show_error("No document items found in EPUB")
            return

        # cleanup and create temp_dir
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception:
            pass
        self.temp_dir = tempfile.mkdtemp(prefix="epub_extract_")

        # attempt full zip extract (works for most EPUBs)
        try:
            with zipfile.ZipFile(path, "r") as z:
                z.extractall(self.temp_dir)
        except Exception:
            pass

        # also write ebooklib items to disk to ensure presence
        extracted = set()
        for it in self.book.get_items():
            name = getattr(it, "get_name", lambda: getattr(it, "href", None))() or getattr(it, "href", None) or getattr(it, "id", None)
            if not name:
                continue
            name = name.replace("\\", "/")
            sanitized = self.sanitize_path(name)
            if sanitized is None:
                continue
            dest = os.path.join(self.temp_dir, sanitized)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                with open(dest, "wb") as fh:
                    fh.write(it.get_content())
                extracted.add(sanitized)
            except Exception:
                pass
        self._extracted_paths_map = {p.lower(): p for p in extracted}

        # images
        image_items = [i for i in self.book.get_items() if i.get_type() == ITEM_IMAGE]
        self.image_names = { (getattr(im, "get_name", lambda: getattr(im, "href", None))() or "").replace("\\","/"): im for im in image_items }
        self.image_basenames = {}
        for im in image_items:
            bn = os.path.basename((getattr(im, "get_name", lambda: "")() or "")).replace("\\","/")
            if bn:
                self.image_basenames.setdefault(bn, []).append(im)

        self.item_map = { (it.get_name() or "").replace("\\","/"): it for it in self.items }

        # Extract TOC nodes
        toc_nodes = self._extract_toc_nodes()
        # populate UI and show first page
        self._populate_reader_ui(toc_nodes)
        self.current_index = 0
        self.display_page(index=0)

    def sanitize_path(self, path):
        if not path:
            return None
        normalized = os.path.normpath(path).replace("\\", "/")
        if normalized.startswith("..") or os.path.isabs(normalized):
            return None
        if ".." in normalized.split("/"):
            return None
        return normalized

    # ---------- TOC extraction & UI ----------
    def _extract_toc_nodes(self):
        # try nav
        try:
            for it in self.book.get_items():
                name = getattr(it, "get_name", lambda: getattr(it, "href", None))() or ""
                if name.lower().endswith((".xhtml", ".html", ".htm")):
                    content = safe_decode_bytes(it.get_content())
                    if "<nav" in content.lower():
                        soup = BeautifulSoup(content, "html.parser")
                        nav = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", {"role": "doc-toc"}) or soup.find("nav")
                        if nav:
                            ol = nav.find(["ol","ul"])
                            if not ol:
                                continue
                            def parse_list(ol_el):
                                nodes = []
                                for li in ol_el.find_all("li", recursive=False):
                                    a = li.find("a", href=True)
                                    title = a.get_text(strip=True) if a else li.get_text(strip=True)
                                    href = a["href"] if a else None
                                    child = li.find(["ol","ul"], recursive=False)
                                    children = parse_list(child) if child else []
                                    nodes.append({"title": title or None, "href": href, "children": children})
                                return nodes
                            return parse_list(ol)
        except Exception:
            pass
        # try ncx
        try:
            for it in self.book.get_items():
                name = getattr(it, "get_name", lambda: "")() or ""
                if name.lower().endswith(".ncx"):
                    ncx = safe_decode_bytes(it.get_content())
                    soup = BeautifulSoup(ncx, "xml")
                    navmap = soup.find("navMap")
                    if navmap:
                        def walk(parent):
                            nodes = []
                            for np in parent.find_all("navPoint", recursive=False):
                                text = np.find("text"); content = np.find("content")
                                title = text.get_text(strip=True) if text else ""
                                href = content["src"] if content and content.has_attr("src") else None
                                children = walk(np)
                                nodes.append({"title": title or None, "href": href, "children": children})
                            return nodes
                        return walk(navmap)
        except Exception:
            pass
        # fallback: items list
        nodes = []
        for i, it in enumerate(self.items):
            href = getattr(it, "get_name", lambda: getattr(it, "href", None))() or f"doc-{i}"
            title = None
            try:
                html_text = safe_decode_bytes(it.get_content())
                m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                if m:
                    title = m.group(1).strip()
            except Exception:
                title = None
            if not title:
                title = os.path.basename(href)
            nodes.append({"href": href, "title": title, "children": []})
        return nodes

    def _populate_reader_ui(self, toc_nodes):
        self._clear_container(self._toc_box)
        self._row_map.clear()
        self._active_href = None
        if not toc_nodes:
            self._toc_box.append(Gtk.Label(label="NO TOC"))
            return
        hdr = Gtk.Label(label="Contents", xalign=0)
        try:
            hdr.add_css_class("toc-contents-label")
        except Exception:
            pass
        self._toc_box.append(hdr)
        self._build_foliate_toc(self._toc_box, toc_nodes)
        try:
            self.split.set_show_sidebar(True)
            self.split.set_collapsed(False)
        except Exception:
            pass

    def _build_foliate_toc(self, parent_box, nodes, level=0):
        import html as _html
        for node in nodes:
            raw_title = node.get("title") or node.get("href") or "Untitled"
            title = raw_title if not isinstance(raw_title, str) else _html.unescape(raw_title)
            safe_title = GLib.markup_escape_text(title)
            href = node.get("href")
            children = node.get("children") or []
            indent_px = 8 + (level * 10)

            if children:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                header_row = Adw.ActionRow()
                header_row.set_activatable(True)
                try:
                    header_row.add_css_class("toc-expander-row")
                except Exception:
                    pass

                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                header_box.set_margin_start(indent_px)
                try:
                    header_box.set_hexpand(True)
                except Exception:
                    pass

                chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
                try: chev.set_pixel_size(14)
                except Exception: pass
                try: chev.add_css_class("toc-chev")
                except Exception: pass

                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                try: lbl.set_max_width_chars(40)
                except Exception: pass

                header_box.append(chev); header_box.append(lbl)
                try:
                    header_row.set_child(header_box)
                except Exception:
                    try: header_row.set_title(safe_title)
                    except Exception: pass

                revealer = Gtk.Revealer()
                try: revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                except Exception: pass
                revealer.set_reveal_child(False)

                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                child_container.set_margin_start(indent_px + 8)
                self._build_foliate_toc(child_container, children, level=level+1)
                revealer.set_child(child_container)

                def _make_toggle(href_local, revealer_local, chev_local):
                    def _toggle_and_nav():
                        try:
                            new_state = not revealer_local.get_reveal_child()
                            revealer_local.set_reveal_child(new_state)
                            chev_local.set_from_icon_name("go-down-symbolic" if new_state else "go-next-symbolic")
                            if href_local:
                                self._on_toc_clicked(None, href_local)
                                self._set_active(href_local)
                        except Exception:
                            pass
                    return _toggle_and_nav

                toggle_fn = _make_toggle(href, revealer, chev)
                try: header_row.connect("activated", lambda w, fn=toggle_fn: fn())
                except Exception: pass
                try:
                    gesture = Gtk.GestureClick.new()
                    gesture.connect("pressed", lambda g, n_press, x, y, fn=toggle_fn: fn())
                    header_box.add_controller(gesture)
                except Exception:
                    pass

                outer.append(header_row); outer.append(revealer)
                parent_box.append(outer)
                if href:
                    self._row_map[href] = header_row
            else:
                row = Adw.ActionRow()
                lbl = Gtk.Label()
                lbl.set_text(safe_title)
                lbl.set_xalign(0)
                lbl.set_wrap(False)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(True)
                try: lbl.set_max_width_chars(40)
                except Exception: pass
                try:
                    row.set_child(lbl)
                except Exception:
                    try: row.set_title(safe_title)
                    except Exception: pass
                row.set_activatable(True)
                row.connect("activated", lambda w, h=href: (self._on_toc_clicked(w, h), self._set_active(h)))
                if level:
                    cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    cont.set_margin_start(indent_px + 12)
                    cont.append(row)
                    parent_box.append(cont)
                else:
                    try:
                        row.set_margin_start(indent_px + 22)
                    except Exception:
                        pass
                    parent_box.append(row)
                if href:
                    try: row.add_css_class("toc-leaf")
                    except Exception: pass
                    self._row_map[href] = row

    def _set_active(self, href):
        if self._active_href == href:
            return
        prev = self._row_map.get(self._active_href)
        if prev:
            try: prev.remove_css_class("toc-active")
            except Exception: pass
        w = self._row_map.get(href)
        if w:
            try:
                w.add_css_class("toc-active")
                self._toc_scroller.scroll_to_child(w, 0.0, True, 0.0, 0.0)
            except Exception: pass
            self._active_href = href

    def _on_toc_clicked(self, widget, href):
        if not self.book or not href:
            return
        target = href.split("#")[0].lstrip("./")
        # try matching with items
        for it in self.book.get_items():
            ihref = getattr(it, "get_name", lambda: getattr(it, "href", None))()
            if not ihref:
                continue
            if ihref.endswith(target) or os.path.basename(ihref) == target:
                try:
                    html_text = safe_decode_bytes(it.get_content())
                    base_uri = f"file://{os.path.join(self.temp_dir, os.path.dirname(ihref))}/" if self.temp_dir else "file:///"
                    wrapped = self._wrap_html(self.generic_clean_html(html_text), base_uri)
                    if self.webview:
                        self.webview.load_html(wrapped, base_uri)
                    else:
                        self.textview.get_buffer().set_text(BeautifulSoup(self.generic_clean_html(html_text), "html.parser").get_text())
                    frag = None
                    if "#" in href:
                        frag = href.split("#",1)[1]
                        if frag:
                            GLib.timeout_add(150, lambda: self._scroll_to_fragment(frag))
                except Exception as e:
                    self._show_error(f"Cannot load fragment: {e}")
                return
        # fallback: file in temp_dir
        if self.temp_dir:
            candidate = os.path.join(self.temp_dir, target)
            if os.path.exists(candidate):
                self._load_file_with_css(candidate, fragment=href.split("#",1)[1] if "#" in href else None)
                return
        self._show_error("TOC target not found in book.")

    # ---------- Wrap / injection ----------
    def _wrap_html(self, raw_html, base_uri):
        theme_css = """
html,body { margin:0; padding:0; height:100%; min-height:100%; }
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial; }
.ebook-content { padding:12px; box-sizing:border-box; }
.ebook-content img, .ebook-content svg { max-width:100%; height:auto; }
"""
        col_rules = """
.ebook-content { -webkit-column-gap: 28px; column-gap: 28px; column-fill: auto; -webkit-column-fill: auto; }
.single-column .ebook-content { -webkit-column-count: unset !important; column-count: unset !important; -webkit-column-width: unset !important; column-width: unset !important; height: auto !important; overflow-y: auto !important; }
"""
        js = """
<script>
(function(){
  const GAP = 28;
  function getComputedNumberStyle(el, props){ try{ const cs=window.getComputedStyle(el); for(let p of props){ const v=cs.getPropertyValue(p); if(v&&v.trim()) return v.trim(); } }catch(e){} return ''; }
  function effectiveColumns(el){ try{ let cc=parseInt(getComputedNumberStyle(el,['column-count','-webkit-column-count'])||0,10); if(!isNaN(cc)&&cc>0&&cc!==Infinity) return cc; let cwRaw=getComputedNumberStyle(el,['column-width','-webkit-column-width']); let cw=parseFloat(cwRaw); if(!isNaN(cw)&&cw>0){ let available=Math.max(1,el.clientWidth); let approx=Math.floor(available/(cw+GAP)); return Math.max(1,approx);} return 1;}catch(e){return 1;} }
  function columnStep(el){ const cs=window.getComputedStyle(el); const cwRaw=cs.getPropertyValue('column-width')||cs.getPropertyValue('-webkit-column-width')||''; const cw=parseFloat(cwRaw)||el.clientWidth; const gapRaw=cs.getPropertyValue('column-gap')||cs.getPropertyValue('-webkit-column-gap')||(GAP+'px'); const gap=parseFloat(gapRaw)||GAP; const cols=effectiveColumns(el); let step=cw; if(!cwRaw||cwRaw===''||cw===el.clientWidth){ step=Math.max(1, Math.floor((el.clientWidth - Math.max(0,(cols-1)*gap))/cols)); } return step+gap; }
  function snapToNearestColumn(){ const c=document.querySelector('.ebook-content'); if(!c) return; const step=columnStep(c); const cur=window.scrollX||window.pageXOffset||document.documentElement.scrollLeft||0; const target=Math.round(cur/step)*step; window.scrollTo({left:target,top:0,behavior:'smooth'}); }
  function goByColumn(delta){ const c=document.querySelector('.ebook-content'); if(!c) return; const step=columnStep(c); const cur=window.scrollX||window.pageXOffset||document.documentElement.scrollLeft||0; const target=Math.max(0,cur + (delta>0 ? step : -step)); window.scrollTo({left:target,top:0,behavior:'smooth'}); }
  function onWheel(e){ const c=document.querySelector('.ebook-content'); if(!c) return; const cols=effectiveColumns(c); if(cols<=1) return; if(Math.abs(e.deltaY)>Math.abs(e.deltaX)){ e.preventDefault(); const dir=e.deltaY>0?1:-1; goByColumn(dir); } else { if(Math.abs(e.deltaX)>0){ e.preventDefault(); const dir=e.deltaX>0?1:-1; goByColumn(dir); } } }
  function onKey(e){ const c=document.querySelector('.ebook-content'); if(!c) return; const cols=effectiveColumns(c); if(cols<=1) return; if(e.code==='PageDown'){ e.preventDefault(); goByColumn(1);} else if(e.code==='PageUp'){ e.preventDefault(); goByColumn(-1);} else if(e.code==='Home'){ e.preventDefault(); window.scrollTo({left:0,top:0,behavior:'smooth'});} else if(e.code==='End'){ e.preventDefault(); const step=columnStep(c); const max=document.documentElement.scrollWidth - window.innerWidth; window.scrollTo({left:max,top:0,behavior:'smooth'}); } }
  function interceptLinks(){ document.addEventListener('click', function(e){ var target=e.target; while(target && target.tagName!=='A'){ target=target.parentElement; if(!target||target===document.body) break; } if(target && target.tagName==='A' && target.href){ e.preventDefault(); e.stopPropagation(); try{ window.location.href = target.getAttribute('href') || target.href; }catch(err){console.error(err);} return false; }}, true); }
  function updateMode(){ const c=document.querySelector('.ebook-content'); if(!c) return; const cols=effectiveColumns(c); if(cols<=1){ document.documentElement.classList.add('single-column'); document.body.classList.add('single-column'); window.scrollTo({left:0,top:0}); } else { document.documentElement.classList.remove('single-column'); document.body.classList.remove('single-column'); snapToNearestColumn(); } }
  let rt=null; function onResize(){ if(rt) clearTimeout(rt); rt=setTimeout(function(){ updateMode(); snapToNearestColumn(); rt=null; },120); }
  document.addEventListener('DOMContentLoaded', function(){ try{ updateMode(); window.addEventListener('wheel', onWheel, {passive:false, capture:false}); window.addEventListener('keydown', onKey, false); window.addEventListener('resize', onResize); interceptLinks(); setTimeout(updateMode,250); setTimeout(snapToNearestColumn,450);}catch(e){console.error(e);} });
})();
</script>
"""
        base_tag = f'<base href="{base_uri}"/>' if base_uri else ""
        head = f'<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>{base_tag}<style>{theme_css}\n{col_rules}</style>{js}'
        wrapped = f"<!DOCTYPE html><html><head>{head}</head><body><div class='ebook-content'>{raw_html}</div></body></html>"
        return wrapped

    def _load_file_with_css(self, file_path, fragment=None):
        if not os.path.exists(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        base_uri = f"file://{os.path.dirname(file_path)}/"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            img_uri = "file://" + file_path
            raw = f'<div style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;"><img src="{img_uri}" alt="image" style="max-width:100%;height:auto;"/></div>'
            html = self._wrap_html(raw, base_uri)
            if self.webview:
                self.webview.load_html(html, base_uri)
            else:
                self.textview.get_buffer().set_text("[Image] " + file_path)
            return True
        if ext in (".html", ".xhtml", ".htm"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup.find_all(['link']):
                    tag.decompose()
                body = soup.find("body")
                if body:
                    body_content = "".join(str(child) for child in body.children)
                else:
                    body_content = str(soup)
                html_content = self._wrap_html(self.generic_clean_html(body_content), base_uri)
                if self.webview:
                    self.webview.load_html(html_content, base_uri)
                    if fragment: GLib.timeout_add(100, lambda: self._scroll_to_fragment(fragment))
                else:
                    self.textview.get_buffer().set_text(BeautifulSoup(body_content, "html.parser").get_text())
                return True
            except Exception:
                return False
        return False

    def display_page(self, index=None, fragment=None):
        try:
            if index is not None:
                self.current_index = index
            if not getattr(self, "items", None) or self.current_index is None:
                return False
            if self.current_index < 0 or self.current_index >= len(self.items):
                return False
            item = self.items[self.current_index]
            raw = item.get_content() or b""
            html_raw = safe_decode_bytes(raw)
            soup = BeautifulSoup(html_raw, "html.parser")
            for tag in soup.find_all(['script','noscript','iframe','object','embed']):
                tag.decompose()
            body = soup.find("body")
            if body:
                content = "".join(str(child) for child in body.children)
            else:
                content = str(soup)
            try:
                base_path = os.path.join(self.temp_dir or "", os.path.dirname(item.get_name() or ""))
                base_uri = f"file://{base_path}/"
            except Exception:
                base_uri = None
            cleaned = self.generic_clean_html(content)
            wrapped_html = self._wrap_html(cleaned, base_uri)
            if self.webview:
                if base_uri:
                    self.webview.load_html(wrapped_html, base_uri)
                else:
                    self.webview.load_html(wrapped_html, "")
                if fragment:
                    GLib.timeout_add(120, lambda: self._scroll_to_fragment(fragment))
            else:
                self.textview.get_buffer().set_text(BeautifulSoup(cleaned, "html.parser").get_text())
            return True
        except Exception as e:
            print("display_page error:", e)
            return False

    # ---------- Sanitizer ----------
    def generic_clean_html(self, html_text, allowed_tags=None, allowed_attrs=None,
                           remove_processing_instructions=True, strip_comments=True):
        if not isinstance(html_text, str):
            try:
                html_text = html_text.decode("utf-8", errors="replace")
            except Exception:
                html_text = str(html_text)
        if remove_processing_instructions:
            html_text = re.sub(r'<\?[^>]*\?>', '', html_text, flags=re.IGNORECASE)
        html_text = re.sub(r'dp\s*n="[^"]*"\s*folio="[^"]*"\s*\?*', '', html_text, flags=re.IGNORECASE)
        html_text = re.sub(r'(?m)^[\s\?]{1,8}\n?', '', html_text)
        soup = BeautifulSoup(html_text, "lxml")
        if strip_comments:
            for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
                c.extract()
        for bad in soup.find_all(['script', 'noscript', 'iframe', 'object', 'embed']):
            bad.decompose()
        if allowed_tags is None:
            allowed_tags = {
                'html','head','body','meta','base','style','link',
                'div','p','span','br','hr',
                'h1','h2','h3','h4','h5','h6',
                'a','img','ul','ol','li','strong','b','em','i','u','sup','sub',
                'blockquote','pre','code','table','thead','tbody','tr','td','th'
            }
        if allowed_attrs is None:
            allowed_attrs = {
                'a': ['href', 'title', 'id', 'class', 'data-tts-id'],
                'img': ['src', 'alt', 'title', 'width', 'height', 'class'],
                'link': ['rel', 'href', 'type', 'media'],
                '*': ['id', 'class', 'style', 'title', 'data-*']
            }
        def attr_allowed(tag, attr):
            allowed = allowed_attrs.get(tag, allowed_attrs.get('*', []))
            if attr.startswith('data-'):
                return any(a.endswith('*') or a == 'data-*' for a in allowed)
            return attr in allowed
        for el in list(soup.find_all()):
            name = getattr(el, 'name', None)
            if not name:
                continue
            name = name.lower()
            if name not in allowed_tags:
                try:
                    el.unwrap()
                except Exception:
                    try:
                        el.decompose()
                    except Exception:
                        pass
                continue
            if getattr(el, 'attrs', None):
                for k in list(el.attrs.keys()):
                    if not attr_allowed(name, k):
                        try:
                            del el.attrs[k]
                        except Exception:
                            pass
        for t in list(soup.find_all(string=True)):
            s = str(t)
            if re.fullmatch(r'\s*[\?]{1,4}\s*', s):
                t.extract()
                continue
            new = re.sub(r'\s+', ' ', s)
            if new != s:
                t.replace_with(new)
        return str(soup)

    # ---------- Link handling ----------
    def on_decide_policy(self, webview, decision, decision_type):
        try:
            if decision_type == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                nav_action = decision.get_navigation_action()
                request = nav_action.get_request() if hasattr(nav_action, "get_request") else decision.get_request()
                uri = request.get_uri() if request else None
                if not uri:
                    return False
                if uri.startswith("http://") or uri.startswith("https://"):
                    try: decision.ignore()
                    except Exception: pass
                    return True
                if uri.startswith("file://"):
                    if self.handle_internal_link(uri):
                        try: decision.ignore()
                        except Exception: pass
                        return True
            return False
        except Exception:
            return False

    def handle_internal_link(self, uri):
        path = uri.replace("file://", "")
        fragment = None
        if "#" in path:
            path, fragment = path.split("#",1)
        if self.temp_dir and path.startswith(self.temp_dir):
            rel = os.path.relpath(path, self.temp_dir).replace(os.sep, "/")
        else:
            rel = path.replace(os.sep, "/")
        candidates = [rel, os.path.basename(rel)]
        try:
            uq = urllib.parse.unquote(rel)
            if uq != rel:
                candidates.append(uq); candidates.append(os.path.basename(uq))
        except Exception:
            pass
        # try match to items
        for cand in candidates:
            if cand in self.item_map:
                for i, it in enumerate(self.items):
                    if it.get_name() == cand:
                        self.current_index = i
                        self.display_page(fragment=fragment)
                        return True
        # try to open file physically
        possible = []
        if self.temp_dir:
            possible.append(os.path.join(self.temp_dir, rel))
            possible.append(os.path.join(self.temp_dir, os.path.basename(rel)))
        possible.append(path)
        for p in possible:
            if p and os.path.exists(p):
                return self._load_file_with_css(p, fragment)
        return False

    def _scroll_to_fragment(self, fragment):
        if self.webview and fragment:
            js_code = f"var element = document.getElementById('{fragment}'); if (element) element.scrollIntoView({{behavior:'smooth', block:'start'}});"
            try:
                self.webview.run_javascript(js_code, None, None, None)
            except Exception:
                try:
                    self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                except Exception:
                    pass
        return False

    def _show_error(self, text):
        try:
            dlg = Gtk.Dialog(title="Error", transient_for=self, modal=True)
            dlg.add_button("OK", Gtk.ResponseType.OK)
            content = dlg.get_content_area()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12)
            label = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.LEFT)
            box.append(label)
            content.append(box)
            dlg.present()
        except Exception:
            pass

    def _enable_responsive_sidebar(self):
        self._responsive_enabled = True
        self._user_hid_sidebar = False
        self._on_window_size_changed()

    def _disable_responsive_sidebar(self):
        self._responsive_enabled = False
        self._user_hid_sidebar = False
        try:
            self.split.set_collapsed(False)
            self.split.set_show_sidebar(False)
        except Exception:
            pass

class EPubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        if not self.props.active_window:
            self.win = EPubViewerWindow(self)
        self.win.present()

def main(argv):
    app = EPubViewerApp()
    return app.run(argv)

if __name__ == "__main__":
    sys.exit(main(sys.argv))

