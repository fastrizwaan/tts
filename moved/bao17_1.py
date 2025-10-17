#!/usr/bin/env python3
"""Complete EPUB & HTML Viewer with Advanced Settings"""
import os, tempfile, shutil, urllib.parse, html as _html, json, gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, Gio, WebKit, GLib, Gdk, Pango
import re, sys
from ebooklib import epub
import base64

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

_FOLIATE_CSS = b"""
.sidebar-toc { background-color: @surface; padding: 6px; }
.toc-contents-label { padding: 12px 12px 6px; font-weight: 600; }
.toc-expander-row { min-height: 30px; padding: 4px 4px 4px 10px; border-radius: 10px; }
.toc-leaf { min-height: 30px; border-radius: 8px; margin-right: 4px; padding: 4px 4px 4px 20px; }
.toc-chev { margin: 0 8px 0 2px; }
.adw-action-row:hover { background-color: rgba(0,0,0,0.03); }
.toc-active { background-color: rgba(20, 80, 160, 0.08); border-radius: 6px; }
"""

try:
    from ebooklib import ITEM_DOCUMENT, ITEM_STYLE, ITEM_IMAGE
except ImportError:
    ITEM_DOCUMENT, ITEM_STYLE, ITEM_IMAGE = 9, 3, 2

class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="EPUB & HTML Viewer")
        self.set_default_size(1100, 720)
        
        provider = Gtk.CssProvider()
        provider.load_from_data(_FOLIATE_CSS)
        display = Gdk.Display.get_default()
        if display: Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        
        self.book, self.book_path, self._row_map, self._active_href = None, None, {}, None
        self.is_epub_mode, self.temp_dir, self.items, self.item_map = False, None, [], {}
        self.original_html_content = "<h1>Welcome</h1><p>Select an HTML file or EPUB to view.</p>"
        
        # Settings
        self.font_family = "serif"
        self.font_size = 16
        self.line_height = 1.6
        self.margin = 20
        self.use_fixed_columns = True
        self.column_count = 2
        self.column_width = 400
        self.column_gap = 20
        
        self.split = Adw.OverlaySplitView(show_sidebar=False)
        self.set_content(self.split)
        
        self._toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        try: self._toc_box.add_css_class("sidebar-toc")
        except: pass
        self._toc_box.set_margin_top(6); self._toc_box.set_margin_bottom(6)
        self._toc_box.set_margin_start(6); self._toc_box.set_margin_end(6)
        
        self._toc_scroller = Gtk.ScrolledWindow()
        try: self._toc_scroller.set_min_content_width(320)
        except: pass
        self._toc_scroller.set_child(self._toc_box)
        self.split.set_sidebar(self._toc_scroller)
        
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        
        toggle_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        toggle_btn.connect("clicked", self.on_toggle_sidebar)
        header.pack_start(toggle_btn)
        
        html_btn = Gtk.Button(icon_name="document-open-symbolic")
        html_btn.set_tooltip_text("Open HTML File")
        html_btn.connect("clicked", self.on_open_html_file)
        header.pack_start(html_btn)
        
        epub_btn = Gtk.Button(icon_name="book-open-symbolic")
        epub_btn.set_tooltip_text("Open EPUB File")
        epub_btn.connect("clicked", self.on_open_epub_file)
        header.pack_start(epub_btn)
        
        # Settings menu button
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_tooltip_text("Settings")
        popover = Gtk.Popover()
        
        settings_grid = Gtk.Grid()
        settings_grid.set_column_spacing(10)
        settings_grid.set_row_spacing(10)
        settings_grid.set_margin_start(10)
        settings_grid.set_margin_end(10)
        settings_grid.set_margin_top(10)
        settings_grid.set_margin_bottom(10)
        
        row = 0
        
        # Font family
        font_label = Gtk.Label(label="Font:", halign=Gtk.Align.START)
        font_model = Gtk.StringList.new(["Serif", "Sans-serif", "Monospace", "Georgia", "Times New Roman"])
        self.font_dropdown = Gtk.DropDown(model=font_model, selected=0)
        self.font_dropdown.connect("notify::selected", self.on_font_family_changed)
        settings_grid.attach(font_label, 0, row, 1, 1)
        settings_grid.attach(self.font_dropdown, 1, row, 1, 1)
        row += 1
        
        # Font size
        size_label = Gtk.Label(label="Font Size:", halign=Gtk.Align.START)
        size_adj = Gtk.Adjustment(value=16, lower=8, upper=48, step_increment=1)
        self.size_spin = Gtk.SpinButton(adjustment=size_adj, digits=0, numeric=True)
        self.size_spin.connect("value-changed", self.on_font_size_changed)
        settings_grid.attach(size_label, 0, row, 1, 1)
        settings_grid.attach(self.size_spin, 1, row, 1, 1)
        row += 1
        
        # Line height
        lh_label = Gtk.Label(label="Line Height:", halign=Gtk.Align.START)
        lh_adj = Gtk.Adjustment(value=1.6, lower=0.8, upper=3.0, step_increment=0.1)
        self.lh_spin = Gtk.SpinButton(adjustment=lh_adj, digits=1, numeric=True)
        self.lh_spin.connect("value-changed", self.on_line_height_changed)
        settings_grid.attach(lh_label, 0, row, 1, 1)
        settings_grid.attach(self.lh_spin, 1, row, 1, 1)
        row += 1
        
        # Margin
        margin_label = Gtk.Label(label="Margin:", halign=Gtk.Align.START)
        margin_adj = Gtk.Adjustment(value=20, lower=0, upper=100, step_increment=5)
        self.margin_spin = Gtk.SpinButton(adjustment=margin_adj, digits=0, numeric=True)
        self.margin_spin.connect("value-changed", self.on_margin_changed)
        settings_grid.attach(margin_label, 0, row, 1, 1)
        settings_grid.attach(self.margin_spin, 1, row, 1, 1)
        row += 1
        
        # Column mode
        mode_label = Gtk.Label(label="Column Mode:", halign=Gtk.Align.START)
        mode_model = Gtk.StringList.new(["Fixed Count", "Fixed Width"])
        self.mode_dropdown = Gtk.DropDown(model=mode_model, selected=0)
        self.mode_dropdown.connect("notify::selected", self.on_column_mode_changed)
        settings_grid.attach(mode_label, 0, row, 1, 1)
        settings_grid.attach(self.mode_dropdown, 1, row, 1, 1)
        row += 1
        
        # Columns (for fixed count mode)
        col_label = Gtk.Label(label="Columns:", halign=Gtk.Align.START)
        col_adj = Gtk.Adjustment(value=2, lower=1, upper=10, step_increment=1)
        self.col_spin = Gtk.SpinButton(adjustment=col_adj, digits=0, numeric=True)
        self.col_spin.connect("value-changed", self.on_columns_changed)
        settings_grid.attach(col_label, 0, row, 1, 1)
        settings_grid.attach(self.col_spin, 1, row, 1, 1)
        row += 1
        
        # Column width (for fixed width mode)
        cw_label = Gtk.Label(label="Column Width:", halign=Gtk.Align.START)
        cw_adj = Gtk.Adjustment(value=400, lower=200, upper=800, step_increment=10)
        self.cw_spin = Gtk.SpinButton(adjustment=cw_adj, digits=0, numeric=True)
        self.cw_spin.connect("value-changed", self.on_column_width_changed)
        settings_grid.attach(cw_label, 0, row, 1, 1)
        settings_grid.attach(self.cw_spin, 1, row, 1, 1)
        row += 1
        
        # Column gap
        gap_label = Gtk.Label(label="Column Gap:", halign=Gtk.Align.START)
        gap_adj = Gtk.Adjustment(value=20, lower=5, upper=50, step_increment=5)
        self.gap_spin = Gtk.SpinButton(adjustment=gap_adj, digits=0, numeric=True)
        self.gap_spin.connect("value-changed", self.on_column_gap_changed)
        settings_grid.attach(gap_label, 0, row, 1, 1)
        settings_grid.attach(self.gap_spin, 1, row, 1, 1)
        
        popover.set_child(settings_grid)
        menu_btn.set_popover(popover)
        header.pack_end(menu_btn)
        
        header.set_title_widget(Gtk.Label(label="EPUB & HTML Viewer"))
        toolbar.add_top_bar(header)
        
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True); self.webview.set_hexpand(True)
        try: self.webview.connect("decide-policy", self.on_decide_policy)
        except: pass
        
        content_manager = self.webview.get_user_content_manager()
        content_manager.connect("script-message-received::scrollEvent", self.on_scroll_event_received)
        content_manager.register_script_message_handler("scrollEvent")
        self.webview.load_html("<html><body><h1>Welcome</h1><p>Select an HTML file or EPUB to view.</p></body></html>")
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.webview.set_margin_top(10); self.webview.set_margin_bottom(10)
        self.webview.set_margin_start(10); self.webview.set_margin_end(10)
        content_box.append(self.webview)
        toolbar.set_content(content_box)
        self.split.set_content(toolbar)
        
        self.current_columns = 2
        GLib.idle_add(lambda: self.apply_column_layout(self.current_columns))
        
        self.connect("notify::default-width", self.on_size_changed)
        self.connect("notify::default-height", self.on_size_changed)
        
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 768px"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.connect("close-request", self.on_close_request)
    
    def on_close_request(self, window): self.cleanup(); return False
    
    def cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            try: shutil.rmtree(self.temp_dir)
            except Exception as e: print(f"Cleanup error: {e}")
        self.temp_dir, self.book, self.items, self.item_map = None, None, [], {}
    
    def sanitize_path(self, path):
        if not path: return None
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or os.path.isabs(normalized) or ".." in normalized.split(os.sep): return None
        return normalized
    
    def on_decide_policy(self, webview, decision, decision_type):
        try:
            from gi.repository import WebKit as WK
            NAVIGATION_ACTION = WK.PolicyDecisionType.NAVIGATION_ACTION
        except: NAVIGATION_ACTION = 0
        try:
            if int(decision_type) == int(NAVIGATION_ACTION):
                try:
                    nav_action = decision.get_navigation_action()
                    request = nav_action.get_request() if hasattr(nav_action, 'get_request') else decision.get_request()
                    uri = request.get_uri() if request else None
                except: return False
                if not uri or uri in ("", "about:blank", "file://"): return False
                if uri.startswith(("http://", "https://")):
                    try: decision.ignore()
                    except: pass
                    return True
                if uri.startswith("file://") and self.is_epub_mode:
                    if webview.get_uri() == uri: return False
                    if self.handle_internal_link(uri):
                        try: decision.ignore()
                        except: pass
                        return True
        except: pass
        return False
    
    def handle_internal_link(self, uri):
        if not self.book or not self.temp_dir: return False
        path, fragment = (uri.replace("file://", "").split("#", 1) + [None])[:2]
        try: rel_path = os.path.relpath(path, self.temp_dir).replace(os.sep, "/") if path.startswith(self.temp_dir) else path.replace(os.sep, "/")
        except: rel_path = os.path.basename(path)
        candidates = [rel_path, os.path.basename(rel_path)]
        try:
            unquoted = urllib.parse.unquote(rel_path)
            if unquoted != rel_path: candidates.extend([unquoted, os.path.basename(unquoted)])
        except: pass
        for cand in candidates:
            if cand in self.item_map:
                try:
                    item = self.item_map[cand]
                    self.original_html_content = self._process_epub_content(item.get_content().decode("utf-8", errors="ignore"), item)
                    self.apply_column_layout(self.current_columns)
                    if fragment: GLib.timeout_add(250, lambda f=fragment: self.webview.evaluate_javascript(f'setTimeout(()=>{{const e=document.getElementById("{f}")||document.querySelector("[name=\'{f}\']");e&&e.scrollIntoView({{behavior:"smooth",block:"start"}})}},200)', -1, None, None, None))
                    return True
                except: pass
        for item in self.items:
            name = item.get_name() or ""
            for cand in candidates:
                if name == cand or name.endswith(cand) or os.path.basename(name) == os.path.basename(cand):
                    try:
                        self.original_html_content = self._process_epub_content(item.get_content().decode("utf-8", errors="ignore"), item)
                        self.apply_column_layout(self.current_columns)
                        if fragment: GLib.timeout_add(250, lambda f=fragment: self.webview.evaluate_javascript(f'setTimeout(()=>{{const e=document.getElementById("{f}")||document.querySelector("[name=\'{f}\']");e&&e.scrollIntoView({{behavior:"smooth",block:"start"}})}},200)', -1, None, None, None))
                        return True
                    except: pass
        return False
    
    def _clear_container(self, container):
        child = container.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            try: container.remove(child)
            except: pass
            child = next_child
    
    def _set_active(self, href):
        if self._active_href == href: return
        if self._active_href in self._row_map:
            try: self._row_map[self._active_href].remove_css_class("toc-active")
            except: pass
        if href in self._row_map:
            try:
                self._row_map[href].add_css_class("toc-active")
                self._toc_scroller.scroll_to_child(self._row_map[href], 0.0, True, 0.0, 0.0)
            except: pass
            self._active_href = href
    
    def on_toggle_sidebar(self, button):
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        GLib.timeout_add(350, lambda: self.apply_column_layout(self.current_columns, restore_position=True))
    
    def on_scroll_event_received(self, content_manager, js_result):
        try:
            data = json.loads(js_result.to_string())
            icons = {'wheel':'🖱️','wheel-y':'↕️','arrow-left':'⬅️','arrow-right':'➡️','page-up':'⬆️','page-down':'⬇️','home':'🏠','end':'🔚'}
            et = data.get('type','')
            print(f"{icons.get(et,'📜')} {et:12s} | X:{data.get('scrollX',0):5.0f} Y:{data.get('scrollY',0):5.0f} Col:{data.get('column',0)}")
        except: pass
    
    def on_open_html_file(self, button):
        dialog = Gtk.FileDialog(); dialog.set_title("Open HTML File")
        html_filter = Gtk.FileFilter(); html_filter.set_name("HTML files")
        html_filter.add_pattern("*.html"); html_filter.add_pattern("*.htm")
        all_filter = Gtk.FileFilter(); all_filter.set_name("All files"); all_filter.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter); filters.append(html_filter); filters.append(all_filter)
        dialog.set_filters(filters); dialog.open(self, None, self.on_html_file_dialog_response)
    
    def on_html_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                def load():
                    try:
                        self.original_html_content = file.load_bytes(None)[0].get_data().decode('utf-8')
                        self.is_epub_mode = False; self.cleanup(); self._clear_container(self._toc_box)
                        GLib.idle_add(lambda: (self.split.set_show_sidebar(False), self.apply_column_layout(self.current_columns)))
                    except Exception as e: GLib.idle_add(lambda: self.show_error_dialog(f"Error: {e}"))
                GLib.Thread.new(None, load)
        except: pass
    
    def on_open_epub_file(self, button):
        dialog = Gtk.FileDialog(); dialog.set_title("Open EPUB File")
        epub_filter = Gtk.FileFilter(); epub_filter.set_name("EPUB files"); epub_filter.add_pattern("*.epub")
        all_filter = Gtk.FileFilter(); all_filter.set_name("All files"); all_filter.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter); filters.append(epub_filter); filters.append(all_filter)
        dialog.set_filters(filters); dialog.open(self, None, self.on_epub_file_dialog_response)
    
    def on_epub_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file and file.get_path(): self.load_epub(file.get_path())
        except: pass
    
    def _parse_nav_toc_from_string(self, html_text):
        safe = re.sub(r'&(?!#?\w+;)', '&amp;', html_text)
        m = re.search(r'(<nav\b[^>]*>.*?</nav>)', safe, flags=re.I|re.S)
        if not m: return None
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(f"<root>{m.group(1)}</root>")
        except: return None
        strip_ns = lambda t: t.split("}")[-1].lower() if isinstance(t, str) else ""
        list_elem = next((el for el in root.iter() if strip_ns(el.tag) in ("ol","ul")), None)
        if not list_elem: return None
        def parse_list(el):
            nodes = []
            for li in el:
                if strip_ns(li.tag) != "li": continue
                a = next((c for c in li if strip_ns(c.tag) == "a"), None)
                title = "".join(a.itertext()).strip() if a else "".join(li.itertext()).strip()
                href = a.attrib.get("href") if a else None
                sub = next((c for c in li if strip_ns(c.tag) in ("ol","ul")), None)
                nodes.append({"title": title or None, "href": href, "children": parse_list(sub) if sub else []})
            return nodes
        return parse_list(list_elem) or None
    
    def load_epub(self, path):
        try: self.book = epub.read_epub(path); self.book_path, self.is_epub_mode = path, True
        except Exception as e: self.show_error_dialog(f"Failed: {e}"); return
        import zipfile
        self.temp_dir = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(path, "r") as z: z.extractall(self.temp_dir)
        except: pass
        for item in self.book.get_items():
            if not item.get_name(): continue
            san = self.sanitize_path(item.get_name())
            if not san: continue
            full = os.path.join(self.temp_dir, san)
            try: os.makedirs(os.path.dirname(full), exist_ok=True); open(full, "wb").write(item.get_content())
            except: pass
        docs = list(self.book.get_items_of_type(ITEM_DOCUMENT))
        id_map = {}
        for it in docs:
            try: iid = getattr(it, "id", None) or (it.get_id() if hasattr(it, "get_id") else None)
            except: iid = None
            if not iid: iid = it.get_name() or os.urandom(8).hex()
            id_map[iid] = it
        ordered = []
        try:
            for entry in getattr(self.book, "spine", []):
                sid = entry[0] if isinstance(entry, (list,tuple)) and entry else entry
                if sid in id_map: ordered.append(id_map.pop(sid))
            ordered.extend(id_map.values())
            self.items = ordered
        except: self.items = docs
        self.item_map = {it.get_name(): it for it in self.items}
        toc_nodes = None
        
        # First try: Parse HTML5 nav element
        try:
            for item in self.book.get_items():
                try:
                    raw = item.get_content()
                    if raw and "<nav" in raw.decode("utf-8", errors="ignore").lower():
                        toc_nodes = self._parse_nav_toc_from_string(raw.decode("utf-8", errors="ignore"))
                        if toc_nodes:
                            print("[DEBUG] Parsed TOC from HTML5 nav")
                            break
                except:
                    pass
        except:
            pass

        # Second try: Parse NCX file
        if not toc_nodes:
            toc_nodes = self._parse_ncx_toc()
            if toc_nodes:
                print("[DEBUG] Parsed TOC from NCX file")

        # Third try: Use book.toc attribute
        if not toc_nodes:
            raw = getattr(self.book, "toc", None) or (self.book.get_toc() if hasattr(self.book, "get_toc") else None)
            if raw:
                def recurse(it):
                    n = {"href": None, "title": None, "children": []}
                    if isinstance(it, (list,tuple)):
                        if len(it) > 1 and isinstance(it[-1], (list,tuple)):
                            n["href"] = getattr(it[0], "href", None) or getattr(it[0], "src", None)
                            n["title"] = getattr(it[0], "title", None) or getattr(it[0], "text", None) or str(it[0])
                            for s in it[-1]: n["children"].append(recurse(s))
                        else:
                            for e in it:
                                if getattr(e, "href", None) and not n["href"]: n["href"] = getattr(e, "href", None)
                                if (getattr(e, "title", None) or getattr(e, "text", None)) and not n["title"]: n["title"] = getattr(e, "title", None) or getattr(e, "text", None)
                        return n
                    if isinstance(it, dict):
                        n["href"] = it.get("href") or it.get("src")
                        n["title"] = it.get("title") or it.get("text") or it.get("name")
                        for c in it.get("children", []) or it.get("subitems", []): n["children"].append(recurse(c))
                        return n
                    n["href"] = getattr(it, "href", None) or getattr(it, "src", None)
                    n["title"] = getattr(it, "title", None) or getattr(it, "text", None) or str(it)
                    for c in getattr(it, "children", None) or getattr(it, "subitems", None) or []: n["children"].append(recurse(c))
                    return n
                try:
                    toc_nodes = [recurse(it) for it in raw]
                    print("[DEBUG] Parsed TOC from book.toc")
                except:
                    pass

        # Fourth try: Fallback to spine order
        if not toc_nodes:
            print("[DEBUG] Using fallback: generating TOC from spine")
            toc_nodes = []
            for i, item in enumerate(self.items):
                href = item.get_name() or f"doc-{i}"
                try:
                    m = re.search(r"<title[^>]*>(.*?)</title>", item.get_content().decode("utf-8", errors="ignore"), re.I|re.S)
                    title = m.group(1).strip() if m else href.split("/")[-1]
                except:
                    title = href.split("/")[-1]
                toc_nodes.append({"href": href, "title": title, "children": []})

        self._populate_epub_ui(toc_nodes)
    
    def _populate_epub_ui(self, toc_nodes):
        loaded = False
        try:
            if self.book and self.items:
                self.original_html_content = self._process_epub_content(self.items[0].get_content().decode("utf-8", errors="ignore"), self.items[0])
                self.apply_column_layout(self.current_columns)
                loaded = True
        except: pass
        if not loaded:
            self.original_html_content = "<html><body><p>Could not render EPUB</p></body></html>"
            self.apply_column_layout(self.current_columns)
        self._clear_container(self._toc_box); self._row_map.clear(); self._active_href = None
        if not toc_nodes: self._toc_box.append(Gtk.Label(label="No TOC"))
        else:
            hdr = Gtk.Label(label="Contents", xalign=0)
            try: hdr.add_css_class("toc-contents-label")
            except: pass
            self._toc_box.append(hdr)
            self._build_foliate_toc(self._toc_box, toc_nodes)
        try: self.split.set_show_sidebar(True); self.split.set_collapsed(False)
        except: pass
    
    def _build_foliate_toc(self, parent_box, nodes, level=0):
        for node in nodes:
            title = GLib.markup_escape_text(_html.unescape(node.get("title") or node.get("href") or "Untitled"))
            href, children = node.get("href"), node.get("children") or []
            if children:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                header_row = Adw.ActionRow(); header_row.set_activatable(True)
                try: header_row.add_css_class("toc-expander-row")
                except: pass
                header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                try: header_box.set_hexpand(True)
                except: pass
                chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
                try: chev.set_pixel_size(14); chev.add_css_class("toc-chev")
                except: pass
                lbl = Gtk.Label(label=title, xalign=0, wrap=False, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
                try: lbl.set_max_width_chars(40)
                except: pass
                header_box.append(chev); header_box.append(lbl)
                try: header_row.set_child(header_box)
                except: header_row.set_title(title)
                revealer = Gtk.Revealer()
                try: revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
                except: pass
                revealer.set_reveal_child(False)
                child_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                child_container.set_margin_start(8)
                self._build_foliate_toc(child_container, children, level+1)
                revealer.set_child(child_container)
                def toggle(h, r, c):
                    new = not r.get_reveal_child(); r.set_reveal_child(new)
                    c.set_from_icon_name("go-down-symbolic" if new else "go-next-symbolic")
                    if h: self._on_toc_clicked(None, h); self._set_active(h)
                toggle_fn = lambda hr=href, rv=revealer, cv=chev: toggle(hr, rv, cv)
                try: header_row.connect("activated", lambda w, fn=toggle_fn: fn())
                except: pass
                try:
                    g = Gtk.GestureClick.new()
                    g.connect("pressed", lambda g, n, x, y, fn=toggle_fn: fn())
                    header_box.add_controller(g)
                except: pass
                outer.append(header_row); outer.append(revealer); parent_box.append(outer)
                if href: self._row_map[href] = header_row
            else:
                row = Adw.ActionRow()
                lbl = Gtk.Label(label=title, xalign=0, wrap=False, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
                try: lbl.set_max_width_chars(40)
                except: pass
                try: row.set_child(lbl)
                except: row.set_title(title)
                row.set_activatable(True)
                row.connect("activated", lambda w, h=href: (self._on_toc_clicked(w, h), self._set_active(h)))
                if level:
                    cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                    cont.append(row); parent_box.append(cont)
                else: parent_box.append(row)
                if href:
                    try: row.add_css_class("toc-leaf")
                    except: pass
                    self._row_map[href] = row
    
    def _process_epub_content(self, html_content, item):
        """Process EPUB content to handle embedded resources (CSS, images)."""
        # Embed all CSS stylesheets
        css_content = ""
        try:
            for css_item in self.book.get_items():
                if css_item.get_type() == ITEM_STYLE:
                    css_text = css_item.get_content().decode("utf-8", errors="ignore")
                    css_content += f"\n{css_text}\n"
        except Exception:
            pass

        # Resolve base path for current item
        item_name = item.get_name() or ""
        base_path = os.path.dirname(item_name)
        
        # Convert images to data URIs - with better path matching
        try:
            for img_item in self.book.get_items():
                if img_item.get_type() == ITEM_IMAGE:
                    img_file_name = img_item.get_name() or ""
                    img_data = img_item.get_content()
                    
                    # Determine MIME type
                    ext = img_file_name.lower().split('.')[-1] if '.' in img_file_name else 'jpeg'
                    mime_map = {
                        'png': 'image/png',
                        'jpg': 'image/jpeg',
                        'jpeg': 'image/jpeg',
                        'gif': 'image/gif',
                        'svg': 'image/svg+xml',
                        'webp': 'image/webp'
                    }
                    mime_type = mime_map.get(ext, 'image/jpeg')

                    img_base64 = base64.b64encode(img_data).decode("utf-8")
                    data_uri = f"data:{mime_type};base64,{img_base64}"

                    # Generate ALL possible path variants for replacement
                    patterns = set()
                    
                    # Absolute path from EPUB root
                    patterns.add(img_file_name)
                    
                    # Just the filename
                    patterns.add(os.path.basename(img_file_name))
                    
                    # Relative from current chapter's directory
                    if base_path:
                        try:
                            rel_path = os.path.relpath(img_file_name, base_path)
                            patterns.add(rel_path)
                            patterns.add(rel_path.replace("\\", "/"))
                        except:
                            pass
                    
                    # Common prefixes
                    patterns.add(f"./{os.path.basename(img_file_name)}")
                    patterns.add(f"../{os.path.basename(img_file_name)}")
                    patterns.add(f"../Images/{os.path.basename(img_file_name)}")
                    patterns.add(f"../images/{os.path.basename(img_file_name)}")
                    patterns.add(f"Images/{os.path.basename(img_file_name)}")
                    patterns.add(f"images/{os.path.basename(img_file_name)}")
                    
                    # URL-encoded versions
                    try:
                        patterns.add(urllib.parse.quote(img_file_name))
                        patterns.add(urllib.parse.quote(os.path.basename(img_file_name)))
                    except:
                        pass

                    # Replace all variants in the HTML
                    for p in patterns:
                        if p:  # Skip empty strings
                            # Replace in src attributes with both quote styles
                            html_content = html_content.replace(f'src="{p}"', f'src="{data_uri}"')
                            html_content = html_content.replace(f"src='{p}'", f"src='{data_uri}'")
                            # Also handle xlink:href for SVG images
                            html_content = html_content.replace(f'xlink:href="{p}"', f'xlink:href="{data_uri}"')
                            html_content = html_content.replace(f"xlink:href='{p}'", f"xlink:href='{data_uri}'")
                            
        except Exception as e:
            print(f"Error processing images: {e}")
            import traceback
            traceback.print_exc()

        # Inject CSS at the beginning
        if css_content:
            if "<head>" in html_content or "<HEAD>" in html_content:
                html_content = re.sub(
                    r'<head>',
                    f"<head><style>{css_content}</style>",
                    html_content,
                    count=1,
                    flags=re.IGNORECASE
                )
            else:
                html_content = f"<html><head><style>{css_content}</style></head><body>{html_content}</body></html>"

        return html_content
 
    def _parse_ncx_toc(self):
        """Parse TOC from NCX file"""
        try:
            ncx_item = None
            for item in self.book.get_items():
                if item.get_name() and item.get_name().endswith('.ncx'):
                    ncx_item = item
                    break
            
            if not ncx_item:
                return None
            
            from xml.etree import ElementTree as ET
            ncx_content = ncx_item.get_content().decode('utf-8', errors='ignore')
            root = ET.fromstring(ncx_content)
            
            # NCX uses namespaces
            ns = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
            
            def parse_navpoint(navpoint):
                """Recursively parse navPoint elements"""
                node = {"href": None, "title": None, "children": []}
                
                # Get title
                nav_label = navpoint.find('ncx:navLabel', ns)
                if nav_label is not None:
                    text_elem = nav_label.find('ncx:text', ns)
                    if text_elem is not None:
                        node["title"] = text_elem.text or ""
                
                # Get href
                content_elem = navpoint.find('ncx:content', ns)
                if content_elem is not None:
                    node["href"] = content_elem.get('src')
                
                # Get children (nested navPoints)
                for child_navpoint in navpoint.findall('ncx:navPoint', ns):
                    child_node = parse_navpoint(child_navpoint)
                    if child_node["title"] or child_node["href"]:
                        node["children"].append(child_node)
                
                return node
            
            # Find navMap and parse all top-level navPoints
            nav_map = root.find('.//ncx:navMap', ns)
            if nav_map is None:
                return None
            
            toc_nodes = []
            for navpoint in nav_map.findall('ncx:navPoint', ns):
                node = parse_navpoint(navpoint)
                if node["title"] or node["href"]:
                    toc_nodes.append(node)
            
            return toc_nodes if toc_nodes else None
            
        except Exception as e:
            print(f"Error parsing NCX: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _on_toc_clicked(self, widget, href):
        """Handle TOC item click with improved path resolution"""
        if not self.book or not href:
            return

        print(f"[DEBUG] TOC clicked: {href}")
        
        # Split into path and fragment (anchor)
        href_path, _, fragment = href.partition("#")
        href_path = href_path.lstrip("./")
        
        # Try to find the item
        target_item = None
        
        # Strategy 1: Direct match in item_map
        if href_path in self.item_map:
            target_item = self.item_map[href_path]
            print(f"[DEBUG] Found via item_map (exact): {href_path}")
        
        # Strategy 2: Basename match in item_map
        if not target_item:
            href_basename = os.path.basename(href_path)
            for name, item in self.item_map.items():
                if os.path.basename(name) == href_basename:
                    target_item = item
                    print(f"[DEBUG] Found via item_map (basename): {name}")
                    break
        
        # Strategy 3: Search all items with various matching strategies
        if not target_item:
            for item in self.items:
                if item.get_type() != ITEM_DOCUMENT:
                    continue
                
                item_name = item.get_name() or ""
                
                # Multiple matching strategies
                if (href_path == item_name or 
                    href_path == os.path.basename(item_name) or
                    os.path.basename(href_path) == os.path.basename(item_name) or
                    item_name.endswith(href_path) or
                    item_name.endswith("/" + href_path)):
                    target_item = item
                    print(f"[DEBUG] Found via name matching: {item_name}")
                    break

        if target_item:
            try:
                print(f"[DEBUG] Loading item: {target_item.get_name()}")
                html_text = target_item.get_content().decode("utf-8", errors="ignore")
                processed_content = self._process_epub_content(html_text, target_item)
                self.original_html_content = processed_content
                self.apply_column_layout(self.current_columns)

                # Jump to fragment if present
                if fragment:
                    print(f"[DEBUG] Scrolling to fragment: {fragment}")
                    js = f"""
                    setTimeout(function() {{
                        const el = document.getElementById("{fragment}") || document.querySelector("[name='{fragment}']");
                        if (el) {{
                            el.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                            console.log('Scrolled to fragment: {fragment}');
                        }} else {{
                            console.log('Fragment not found: {fragment}');
                        }}
                    }}, 300);
                    """
                    GLib.timeout_add(350, lambda: self.webview.evaluate_javascript(js, -1, None, None, None))
            except Exception as e:
                self.show_error_dialog(f"Cannot load chapter: {e}")
                print(f"[DEBUG] Error loading chapter: {e}")
                import traceback
                traceback.print_exc()
            return

        print(f"[DEBUG] TOC target not found: {href}")
        self.show_error_dialog(f"TOC target '{href}' not found in book.")
    
    def show_error_dialog(self, message):
        """Show error dialog"""
        try:
            dlg = Adw.MessageDialog.new(self, "Error", message)
            dlg.add_response("ok", "OK")
            dlg.present()
        except Exception:
            print(f"Error: {message}")
    
    def on_font_family_changed(self, dropdown, pspec):
        """Handle font family change"""
        fonts = ["serif", "sans-serif", "monospace", "Georgia", "Times New Roman"]
        self.font_family = fonts[dropdown.get_selected()]
        self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_font_size_changed(self, spin):
        """Handle font size change"""
        self.font_size = int(spin.get_value())
        self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_line_height_changed(self, spin):
        """Handle line height change"""
        self.line_height = spin.get_value()
        self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_margin_changed(self, spin):
        """Handle margin change"""
        self.margin = int(spin.get_value())
        self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_column_mode_changed(self, dropdown, pspec):
        """Handle column mode change"""
        self.use_fixed_columns = dropdown.get_selected() == 0
        self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_columns_changed(self, spin):
        """Handle columns count change"""
        self.column_count = int(spin.get_value())
        if self.use_fixed_columns:
            self.current_columns = self.column_count
            self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_column_width_changed(self, spin):
        """Handle column width change"""
        self.column_width = int(spin.get_value())
        if not self.use_fixed_columns:
            self.apply_column_layout(self.current_columns, restore_position=True)
    
    def on_column_gap_changed(self, spin):
        """Handle column gap change"""
        self.column_gap = int(spin.get_value())
        self.apply_column_layout(self.current_columns, restore_position=True)

    def apply_column_layout(self, num_columns, restore_position=False):
        """Apply column layout with user settings"""
        self.current_columns = num_columns
        
        is_column_mode = num_columns > 1
        
        if is_column_mode:
            if self.use_fixed_columns:
                col_style = f"column-count: {num_columns};"
            else:
                col_style = f"column-width: {self.column_width}px;"
            
            # For column mode, we need proper height constraint
            css = f"""
            html {{
                height: 100vh;
                width: 100vw;
                margin: 0;
                padding: 0;
                overflow: hidden;
            }}
            body {{
                font-family: {self.font_family};
                font-size: {self.font_size}px;
                line-height: {self.line_height};
                margin: 0;
                padding: {self.margin}px;
                box-sizing: border-box;
                
                /* Critical: Set exact height for columns to work */
                height: calc(100vh - {self.margin * 2}px);
                width: 100vw;
                
                {col_style}
                column-gap: {self.column_gap}px;
                column-fill: auto;
                
                overflow-x: auto;
                overflow-y: hidden;
                
                /* Smooth scrolling */
                scroll-behavior: smooth;
            }}
            * {{
                font-family: {self.font_family};
                font-size: inherit;
                line-height: inherit;
            }}
            p, div, span {{
                font-size: {self.font_size}px;
                line-height: {self.line_height};
            }}
            img {{
                max-width: 100%;
                height: auto;
                display: block;
                margin: 10px auto;
                break-inside: avoid;
            }}
            h1, h2, h3, h4, h5, h6 {{
                break-after: avoid;
                margin-top: 1em;
                margin-bottom: 0.5em;
            }}
            body::-webkit-scrollbar {{
                height: 10px;
            }}
            body::-webkit-scrollbar-track {{
                background: #f1f1f1;
            }}
            body::-webkit-scrollbar-thumb {{
                background: #888;
                border-radius: 5px;
            }}
            body::-webkit-scrollbar-thumb:hover {{
                background: #555;
            }}
            """
        else:
            css = f"""
            html {{
                width: 100%;
                height: 100%;
                margin: 0;
                padding: 0;
            }}
            body {{
                font-family: {self.font_family};
                font-size: {self.font_size}px;
                line-height: {self.line_height};
                margin: 0;
                padding: {self.margin}px;
                box-sizing: border-box;
                width: 100%;
                overflow-x: hidden;
                overflow-y: auto;
            }}
            * {{
                font-family: {self.font_family};
                font-size: inherit;
                line-height: inherit;
            }}
            p, div, span {{
                font-size: {self.font_size}px;
                line-height: {self.line_height};
            }}
            img {{
                max-width: 100%;
                height: auto;
                display: block;
                margin: 10px auto;
            }}
            """
        
        js_script = f"""
        window.currentColumnCount = {num_columns};

        function getColumnWidth() {{
            const body = document.body;
            if (!body) return 0;
            const style = window.getComputedStyle(body);
            const colCount = window.currentColumnCount;
            const gap = parseFloat(style.columnGap) || {self.column_gap};
            const viewportWidth = window.innerWidth - ({self.margin} * 2);
            const totalGap = gap * (colCount - 1);
            const columnWidth = (viewportWidth - totalGap) / colCount;
            return columnWidth + gap;
        }}

        function sendScrollEvent(eventType) {{
            if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.scrollEvent) {{
                const colWidth = getColumnWidth();
                const currentColumn = colWidth > 0 ? Math.round(window.scrollX / colWidth) : 0;
                window.webkit.messageHandlers.scrollEvent.postMessage(JSON.stringify({{
                    type: eventType,
                    scrollX: window.scrollX,
                    scrollY: window.scrollY,
                    column: currentColumn
                }}));
            }}
        }}

        function smoothScrollTo(xTarget, yTarget) {{
            const startX = window.scrollX;
            const startY = window.scrollY;
            const distanceX = xTarget - startX;
            const distanceY = yTarget - startY;
            const duration = 400;
            const startTime = performance.now();
            function step(time) {{
                const elapsed = time - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const t = progress < 0.5 ? 4 * progress * progress * progress : (progress - 1) * (2 * progress - 2) * (2 * progress - 2) + 1;
                window.scrollTo(startX + distanceX * t, startY + distanceY * t);
                if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
        }}

        function snapScroll() {{
            if (window.currentColumnCount === 1) return;
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const currentScroll = window.scrollX;
            const target = Math.round(currentScroll / colWidth) * colWidth;
            if (Math.abs(currentScroll - target) > 1) window.scrollTo(target, window.scrollY);
        }}

        let scrollTimeout;
        window.addEventListener('scroll', function() {{
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {{
                if (window.currentColumnCount > 1) {{
                    snapScroll();
                }}
            }}, 100);
        }});

        document.addEventListener('wheel', function(e) {{
            if (window.currentColumnCount === 1) {{
                sendScrollEvent('wheel-y');
                return;
            }}
            e.preventDefault();
            const colWidth = getColumnWidth();
            if (colWidth <= 0) return;
            const scrollDist = e.deltaY > 0 ? colWidth : -colWidth;
            const target = Math.round((window.scrollX + scrollDist) / colWidth) * colWidth;
            smoothScrollTo(target, window.scrollY);
            sendScrollEvent('wheel');
        }}, {{ passive: false }});

        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey || e.altKey || e.metaKey) return;
            
            const colWidth = getColumnWidth();
            const viewportH = window.innerHeight;
            const maxScrollX = document.body.scrollWidth - window.innerWidth;
            const maxScrollY = document.body.scrollHeight - viewportH;

            let x = window.scrollX, y = window.scrollY, type = null;

            console.log('Key pressed:', e.key, 'Current mode:', window.currentColumnCount === 1 ? 'vertical' : 'horizontal');

            if (window.currentColumnCount === 1) {{
                switch (e.key) {{
                    case 'ArrowUp': e.preventDefault(); y = Math.max(0, y - viewportH * 0.8); type = 'arrow-up'; break;
                    case 'ArrowDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH * 0.8); type = 'arrow-down'; break;
                    case 'PageUp': e.preventDefault(); y = Math.max(0, y - viewportH); type = 'page-up'; break;
                    case 'PageDown': e.preventDefault(); y = Math.min(maxScrollY, y + viewportH); type = 'page-down'; break;
                    case 'Home': e.preventDefault(); y = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); y = maxScrollY; type = 'end'; break;
                }}
            }} else {{
                console.log('Horizontal mode - colWidth:', colWidth, 'current x:', x, 'maxScrollX:', maxScrollX);
                switch (e.key) {{
                    case 'ArrowLeft': 
                        e.preventDefault(); 
                        x = Math.max(0, x - colWidth); 
                        type = 'arrow-left';
                        console.log('Arrow Left - moving to:', x);
                        break;
                    case 'ArrowRight': 
                        e.preventDefault(); 
                        x = Math.min(maxScrollX, x + colWidth); 
                        type = 'arrow-right';
                        console.log('Arrow Right - moving to:', x);
                        break;
                    case 'PageUp': 
                        e.preventDefault(); 
                        x = Math.max(0, x - colWidth * 2); 
                        type = 'page-up';
                        console.log('PageUp - moving to:', x);
                        break;
                    case 'PageDown': 
                        e.preventDefault(); 
                        x = Math.min(maxScrollX, x + colWidth * 2); 
                        type = 'page-down';
                        console.log('PageDown - moving to:', x);
                        break;
                    case 'Home': e.preventDefault(); x = 0; type = 'home'; break;
                    case 'End': e.preventDefault(); x = maxScrollX; type = 'end'; break;
                }}
            }}

            if (type) {{
                console.log('Scrolling to x:', x, 'y:', y);
                smoothScrollTo(x, y);
                setTimeout(() => {{
                    sendScrollEvent(type);
                }}, 450);
            }}
        }});
        
        // Debug logging
        setTimeout(() => {{
            console.log('Column count:', window.currentColumnCount);
            console.log('Column width:', getColumnWidth());
            console.log('Body scrollWidth:', document.body.scrollWidth);
            console.log('Window innerWidth:', window.innerWidth);
            console.log('Can scroll:', document.body.scrollWidth > window.innerWidth);
        }}, 500);
        """
        
        original_html = self.original_html_content
        if '<body>' in original_html.lower() and '</body>' in original_html.lower():
            start = original_html.lower().find('<body>') + 6
            end = original_html.lower().find('</body>', start)
            if end != -1:
                body_content = original_html[start:end]
            else:
                body_content = original_html
        else:
            body_content = original_html
        
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>{css}</style>
</head>
<body>{body_content}<script>{js_script}</script></body>
</html>"""
        
        self.webview.load_html(html_content)

    def on_size_changed(self, *args):
        """Handle window resize"""
        GLib.timeout_add(100, lambda: self.apply_column_layout(self.current_columns, restore_position=True))

class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EPUBViewer",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
    
    def do_activate(self, *a):
        if not self.props.active_window:
            self.win = Win(self)
        self.win.present()
    
    def do_shutdown(self):
        """Cleanup temp directory before shutdown"""
        if hasattr(self, 'win') and self.win:
            if hasattr(self.win, 'temp_dir') and self.win.temp_dir:
                try:
                    if os.path.exists(self.win.temp_dir):
                        shutil.rmtree(self.win.temp_dir)
                except Exception as e:
                    print(f"Error cleaning up temp directory: {e}")
        Adw.Application.do_shutdown(self)

if __name__ == "__main__":
    import sys
    app = App()
    sys.exit(app.run(sys.argv))
