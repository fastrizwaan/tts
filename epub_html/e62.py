#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, PangoCairo

from ebooklib import epub

def _make_abs_for_resource(url: str, file_dir: str, tempdir: str):
    url = url.strip()
    if not url:
        return url
    # keep absolute/remote/data/mailto/javascript and fragments as-is
    if url.startswith(('#', 'http://', 'https://', 'data:', 'mailto:', 'javascript:', 'file://')):
        return url
    # separate fragment
    parts = url.split('#', 1)
    rel = parts[0]
    frag = ('#' + parts[1]) if len(parts) == 2 else ''
    # try candidate under file_dir
    candidates = []
    if os.path.isabs(rel):
        candidates.append(rel)
    else:
        if file_dir:
            candidates.append(os.path.normpath(os.path.join(file_dir, rel)))
        if tempdir:
            candidates.append(os.path.normpath(os.path.join(tempdir, rel)))
    # try suffixes
    suffixes = ['', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.html', '.htm', '.xhtml']
    for cand in candidates:
        for s in suffixes:
            path_try = cand if cand.endswith(s) else cand + s
            if os.path.exists(path_try):
                return "file://" + path_try + frag
    # fallback: join with tempdir base if possible
    if tempdir:
        joined = urllib.parse.urljoin("file://" + tempdir.replace(os.sep, '/') + "/", url)
        return joined
    return url

def _rewrite_resource_urls(html: str, file_dir: str, tempdir: str) -> str:
    # replace src= and href= attributes (for resources) but keep anchors/fragments/remote as-is
    def attr_repl(m):
        attr = m.group('attr')
        quote = m.group('quote')
        url = m.group('url')
        new = _make_abs_for_resource(url, file_dir, tempdir)
        return f"{attr}{quote}{new}{quote}"

    html = re.sub(r'(?P<attr>\b(?:src|href)\s*=\s*)(?P<quote>["\'])(?P<url>[^"\']+)(?P=quote)',
                  attr_repl, html, flags=re.I)

    # replace CSS url(...) occurrences
    def cssurl_repl(m):
        quote = m.group(1) or ''
        url = m.group(2)
        new = _make_abs_for_resource(url, file_dir, tempdir)
        return f'url("{new}")'
    html = re.sub(r'url\((["\']?)([^)\'"]+)(["\']?)\)', cssurl_repl, html, flags=re.I)

    return html

_INJECT_CSS = """<style id="__epub_viewer_css">
html,body{margin:0;padding:0;}
img, svg, video, iframe { max-width: 100% !important; height: auto !important; object-fit: contain !important; }
img { max-height: 80vh !important; }

/* Dark mode styles */
@media (prefers-color-scheme: dark) {
    html, body {
        background-color: #2d2d2d !important;
        color: #e6e6e6 !important;
    }
    
    /* Main content areas */
    div, p, span, article, section, main, aside, nav, header, footer {
        background-color: transparent !important;
        color: inherit !important;
    }
    
    /* Headers */
    h1, h2, h3, h4, h5, h6 {
        color: #ffffff !important;
    }
    
    /* Links */
    a, a:link, a:visited {
        color: #6db3f2 !important;
    }
    a:hover, a:active {
        color: #9cc9f7 !important;
    }
    
    /* Code and pre blocks */
    code, pre, kbd, samp {
        background-color: #1e1e1e !important;
        color: #f8f8f2 !important;
        border: 1px solid #444 !important;
    }
    
    /* Tables */
    table {
        background-color: transparent !important;
        border-color: #555 !important;
    }
    th, td {
        background-color: transparent !important;
        color: inherit !important;
        border-color: #555 !important;
    }
    th {
        background-color: #3a3a3a !important;
        color: #ffffff !important;
    }
    
    /* Blockquotes */
    blockquote {
        background-color: #3a3a3a !important;
        color: inherit !important;
        border-left-color: #6db3f2 !important;
    }
    
    /* Input elements */
    input, textarea, select {
        background-color: #3a3a3a !important;
        color: #e6e6e6 !important;
        border-color: #555 !important;
    }
    
    /* HR elements */
    hr {
        border-color: #555 !important;
    }
    
    /* Override any white/light backgrounds */
    *[style*="background-color: white"],
    *[style*="background-color: #fff"],
    *[style*="background-color: #ffffff"],
    *[style*="background: white"],
    *[style*="background: #fff"],
    *[style*="background: #ffffff"] {
        background-color: transparent !important;
    }
    
    /* Override any black text on white backgrounds */
    *[style*="color: black"],
    *[style*="color: #000"],
    *[style*="color: #000000"] {
        color: #e6e6e6 !important;
    }
}

/* Force dark mode (can be toggled) */
.force-dark-mode {
    background-color: #2d2d2d !important;
    color: #e6e6e6 !important;
}

.force-dark-mode div, .force-dark-mode p, .force-dark-mode span, 
.force-dark-mode article, .force-dark-mode section, .force-dark-mode main,
.force-dark-mode aside, .force-dark-mode nav, .force-dark-mode header, 
.force-dark-mode footer {
    background-color: transparent !important;
    color: inherit !important;
}

.force-dark-mode h1, .force-dark-mode h2, .force-dark-mode h3, 
.force-dark-mode h4, .force-dark-mode h5, .force-dark-mode h6 {
    color: #ffffff !important;
}

.force-dark-mode a, .force-dark-mode a:link, .force-dark-mode a:visited {
    color: #6db3f2 !important;
}

.force-dark-mode a:hover, .force-dark-mode a:active {
    color: #9cc9f7 !important;
}

.force-dark-mode code, .force-dark-mode pre, .force-dark-mode kbd, .force-dark-mode samp {
    background-color: #1e1e1e !important;
    color: #f8f8f2 !important;
    border: 1px solid #444 !important;
}

.force-dark-mode table {
    background-color: transparent !important;
    border-color: #555 !important;
}

.force-dark-mode th, .force-dark-mode td {
    background-color: transparent !important;
    color: inherit !important;
    border-color: #555 !important;
}

.force-dark-mode th {
    background-color: #3a3a3a !important;
    color: #ffffff !important;
}

.force-dark-mode blockquote {
    background-color: #3a3a3a !important;
    color: inherit !important;
    border-left-color: #6db3f2 !important;
}

.force-dark-mode hr {
    border-color: #555 !important;
}
</style>
"""

def inject_css_into_html(html: str) -> str:
    if re.search(r'<head\b', html, re.I):
        return re.sub(r'(<head\b[^>]*>)', r'\1' + _INJECT_CSS, html, flags=re.I)
    if re.search(r'<html\b', html, re.I):
        return re.sub(r'(<html\b[^>]*>)', r'\1<head>' + _INJECT_CSS + '</head>', html, flags=re.I)
    return "<!doctype html><head>" + _INJECT_CSS + "</head><body>" + html + "</body></html>"

class Writer(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.viewer")
        self.connect("activate", self.on_activate)
    def on_activate(self, app):
        win = ViewerWindow(application=self)
        win.present()

class ViewerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("HTML/EPUB Viewer")
        self.set_default_size(1200, 800)

        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self._flat_toc = []
        self._spine_hrefs = []
        self._sidebar_visible = False
        self._dark_mode_forced = False

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)
        
        # Header bar
        toolbar_view = Adw.ToolbarView()
        main_box.append(toolbar_view)
        header = Adw.HeaderBar()
        header.add_css_class("flat-header")
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        # Controls in header
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(controls)

        self.back_btn = Gtk.Button(icon_name="go-previous"); self.back_btn.add_css_class("flat")
        self.back_btn.connect("clicked", lambda *_: self.webview.go_back() if self.webview.can_go_back() else None)
        controls.append(self.back_btn)

        self.forward_btn = Gtk.Button(icon_name="go-next"); self.forward_btn.add_css_class("flat")
        self.forward_btn.connect("clicked", lambda *_: self.webview.go_forward() if self.webview.can_go_forward() else None)
        controls.append(self.forward_btn)

        open_btn = Gtk.Button(icon_name="document-open"); open_btn.add_css_class("flat")
        open_btn.connect("clicked", self.on_open_clicked)
        controls.append(open_btn)

        self.sidebar_btn = Gtk.Button(icon_name="view-dual-symbolic"); self.sidebar_btn.add_css_class("flat")
        self.sidebar_btn.set_sensitive(False)
        self.sidebar_btn.connect("clicked", lambda btn: self.toggle_sidebar())
        controls.append(self.sidebar_btn)

        self.dark_mode_btn = Gtk.Button(icon_name="weather-clear-night-symbolic"); self.dark_mode_btn.add_css_class("flat")
        self.dark_mode_btn.set_tooltip_text("Toggle Dark Mode")
        self.dark_mode_btn.connect("clicked", lambda btn: self.toggle_dark_mode())
        controls.append(self.dark_mode_btn)

        # Font controls in header end
        font_map = PangoCairo.FontMap.get_default()
        families = font_map.list_families()
        font_names = sorted([f.get_name() for f in families])
        font_store = Gtk.StringList(strings=font_names)
        self.font_dropdown = Gtk.DropDown(model=font_store)
        default_index = font_names.index("Sans") if "Sans" in font_names else 0
        self.font_dropdown.set_selected(default_index)
        self.font_dropdown.add_css_class("flat")
        self.font_dropdown.connect("notify::selected", self.on_font_family_changed)
        header.pack_end(self.font_dropdown)

        # Main content area with paned layout
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_paned.set_shrink_start_child(False)
        self.main_paned.set_shrink_end_child(False)
        self.main_paned.set_resize_start_child(False)
        self.main_paned.set_resize_end_child(True)
        toolbar_view.set_content(self.main_paned)

        # Sidebar (initially hidden)
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.set_size_request(300, -1)
        self.sidebar.add_css_class("sidebar")
        
        # Sidebar header
        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_header.set_margin_top(6)
        sidebar_header.set_margin_bottom(6)
        sidebar_header.set_margin_start(12)
        sidebar_header.set_margin_end(12)
        sidebar_title = Gtk.Label(label="Table of Contents")
        sidebar_title.add_css_class("heading")
        sidebar_title.set_xalign(0)
        sidebar_header.append(sidebar_title)
        self.sidebar.append(sidebar_header)

        # Sidebar separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar.append(separator)

        # TOC list in sidebar
        sidebar_scroll = Gtk.ScrolledWindow(vexpand=True)
        sidebar_scroll.set_margin_start(6)
        sidebar_scroll.set_margin_end(6)
        sidebar_scroll.set_margin_bottom(6)
        self.sidebar_listbox = Gtk.ListBox()
        self.sidebar_listbox.add_css_class("navigation-sidebar")
        sidebar_scroll.set_child(self.sidebar_listbox)
        self.sidebar.append(sidebar_scroll)

        # Main content area
        content_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.webview = WebKit.WebView()
        content_scroll.set_child(self.webview)
        self.webview.load_html("<!doctype html><html><body><p>Open an EPUB file to begin reading...</p></body></html>", "file:///")

        # Initially only show content (no sidebar)
        self.main_paned.set_end_child(content_scroll)

        self.connect("close-request", self.on_close_request)
        self.webview.connect("notify::title", self._update_nav_buttons)
        self.webview.connect("notify::uri", self._update_nav_buttons)
        self.webview.connect("decide-policy", self.on_decide_policy)

    def on_decide_policy(self, webview, decision, decision_type):
        """Handle navigation decisions to intercept external links"""
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            uri = nav_action.get_request().get_uri()
            
            # Check if this is an external link
            if (uri.startswith('http://') or uri.startswith('https://') or 
                uri.startswith('mailto:') or uri.startswith('ftp://')):
                
                decision.ignore()  # Cancel the navigation in WebKit
                self.open_external_link(uri)
                return True
        
        # For other navigation types, let WebKit handle normally
        return False

    def open_external_link(self, uri):
        """Open external links using xdg-open"""
        try:
            import subprocess
            subprocess.run(['xdg-open', uri], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: show a dialog to inform the user
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=f"External link: {uri}"
            )
            dialog.format_secondary_text("Could not open the external link automatically. You can copy and paste it in your browser.")
            dialog.connect("response", lambda d, r: d.destroy())
            dialog.show()

    def toggle_sidebar(self, *args):
        """Toggle the visibility of the sidebar"""
        if self._sidebar_visible:
            # Hide sidebar
            self.main_paned.set_start_child(None)
            self._sidebar_visible = False
            self.sidebar_btn.set_icon_name("view-dual-symbolic")
        else:
            # Show sidebar
            self.main_paned.set_start_child(self.sidebar)
            self.main_paned.set_position(300)  # Set sidebar width
            self._sidebar_visible = True
            self.sidebar_btn.set_icon_name("view-restore-symbolic")

    def toggle_dark_mode(self):
        """Toggle dark mode for the document content"""
        self._dark_mode_forced = not self._dark_mode_forced
        
        if self._dark_mode_forced:
            self.dark_mode_btn.set_icon_name("weather-clear-symbolic")
            self.dark_mode_btn.set_tooltip_text("Disable Dark Mode")
            js_code = """
            (function() {
                document.documentElement.classList.add('force-dark-mode');
                document.body.classList.add('force-dark-mode');
            })();
            """
        else:
            self.dark_mode_btn.set_icon_name("weather-clear-night-symbolic")
            self.dark_mode_btn.set_tooltip_text("Enable Dark Mode")
            js_code = """
            (function() {
                document.documentElement.classList.remove('force-dark-mode');
                document.body.classList.remove('force-dark-mode');
            })();
            """
        
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception:
            pass

    def populate_sidebar_toc(self):
        """Populate the sidebar with TOC items"""
        # Clear existing items
        while True:
            row = self.sidebar_listbox.get_first_child()
            if row is None:
                break
            self.sidebar_listbox.remove(row)

        def on_sidebar_row_activated(listbox, row):
            row_index = row.get_index()
            if 0 <= row_index < len(self._flat_toc):
                title, href, depth = self._flat_toc[row_index]
                GLib.idle_add(lambda: self.navigate_to_toc_item(href))

        for title, href, depth in self._flat_toc:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hbox.set_margin_top(6)
            hbox.set_margin_bottom(6)
            hbox.set_margin_start(12 + (depth * 16))  # Indent based on depth
            hbox.set_margin_end(12)
            
            label = Gtk.Label(label=title)
            label.set_xalign(0)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(30)
            hbox.append(label)
            
            row.set_child(hbox)
            self.sidebar_listbox.append(row)

        # Connect the row-activated signal
        self.sidebar_listbox.connect("row-activated", on_sidebar_row_activated)

    def _update_nav_buttons(self, *a):
        self.back_btn.set_sensitive(self.webview.can_go_back())
        self.forward_btn.set_sensitive(self.webview.can_go_forward())

    def on_open_clicked(self, btn):
        dialog = Gtk.FileDialog()
        filter_all = Gtk.FileFilter(); filter_all.set_name("HTML / EPUB")
        filter_all.add_pattern("*.html"); filter_all.add_pattern("*.htm"); filter_all.add_pattern("*.epub")
        dialog.set_default_filter(filter_all)
        dialog.open(self, None, self.on_open_file_dialog_response)

    def on_open_file_dialog_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file: return
            basename = file.get_basename().lower()
            # reset state before loading new content
            self._cleanup_epub_tempdir()
            self._clear_webview_history()
            if basename.endswith('.epub'):
                self.load_epub_with_ebooklib(file)
            else:
                file.load_contents_async(None, self.load_html_callback)
        except GLib.Error as e:
            print("Open error:", e.message)

    def load_html_callback(self, file, result):
        try:
            ok, content, _ = file.load_contents_finish(result)
            if ok:
                html = content.decode(errors="replace")
                base = file.get_uri() or "file:///"
                html = inject_css_into_html(html)
                self._epub_tempdir = None
                self._base_href = base
                self._flat_toc = []
                self._spine_hrefs = []
                self.sidebar_btn.set_sensitive(False)
                self.webview.load_html(html, base)
        except GLib.Error as e:
            print("Load error:", e.message)

    def load_epub_with_ebooklib(self, gio_file):
        path = gio_file.get_path()
        if not path:
            try:
                fd, tmp_epub = tempfile.mkstemp(suffix=".epub"); os.close(fd)
                stream = gio_file.read(None)
                with open(tmp_epub, "wb") as f: f.write(stream.read_bytes(stream.get_size()).get_data())
                path = tmp_epub
            except Exception:
                path = None
        if not path:
            print("Cannot access EPUB path"); return

        tempdir = tempfile.mkdtemp(prefix="epub_")
        try:
            book = epub.read_epub(path)
            self._book = book

            for item in book.get_items():
                fn = getattr(item, 'file_name', None)
                if not fn: continue
                full = os.path.join(tempdir, fn)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                content = item.get_content()
                with open(full, "wb") as f:
                    if isinstance(content, str): f.write(content.encode("utf-8"))
                    else: f.write(content)

            spine_hrefs = []
            for idref, _ in getattr(book, 'spine', []):
                try:
                    item = book.get_item_with_id(idref)
                except Exception:
                    item = None
                if item is None: continue
                href = getattr(item, 'file_name', None)
                if href: spine_hrefs.append(href)
            if not spine_hrefs:
                for item in book.get_items_of_type(epub.ITEM_DOCUMENT):
                    if getattr(item, 'file_name', None): spine_hrefs.append(item.file_name)
            if not spine_hrefs:
                print("No document items found in EPUB"); shutil.rmtree(tempdir, ignore_errors=True); return

            # Store spine hrefs for TOC navigation
            self._spine_hrefs = spine_hrefs

            bodies = []; head_html = None
            for i, rel in enumerate(spine_hrefs):
                full = os.path.join(tempdir, rel)
                if not os.path.exists(full): continue
                with open(full, "r", encoding="utf-8", errors="replace") as f: txt = f.read()
                if head_html is None:
                    m = re.search(r'(<head\b[^>]*>.*?</head>)', txt, re.S | re.I)
                    head_html = m.group(1) if m else ''

                # rewrite resource URLs in this document so images/CSS/etc become absolute file:// paths
                file_dir = os.path.dirname(full)
                full_txt = txt  # rewrite whole document (head+body) to catch linked CSS too
                rewritten = _rewrite_resource_urls(full_txt, file_dir, tempdir)

                # extract rewritten body again
                m2r = re.search(r'<body\b[^>]*>(.*?)</body>', rewritten, re.S | re.I)
                body_content = m2r.group(1) if m2r else rewritten
                
                # Add anchor for this chapter/section
                chapter_anchor = f'<div id="chapter_{i}" data-file="{rel}"></div>'
                bodies.append(chapter_anchor + body_content)

            final_head = head_html or '<meta charset="utf-8">'
            # set base_href to tempdir root so any remaining relative paths resolve
            base_href = "file://" + tempdir.replace(os.sep, '/') + "/"
            concatenated = f"<!doctype html>\n<html>\n{final_head}\n<base href=\"{base_href}\">\n<body>\n" + "\n<hr/>\n".join(bodies) + "\n</body>\n</html>"
            concatenated = inject_css_into_html(concatenated)

            # commit state
            self._epub_tempdir = tempdir
            self._base_href = base_href
            self._flat_toc = []
            self.sidebar_btn.set_sensitive(False)

            self._clear_webview_history()
            self.webview.load_html(concatenated, base_href)

            # build robust flat TOC with depth
            toc = getattr(book, "toc", []) or []
            def walk_toc(entries, depth=0):
                if not entries: return
                if isinstance(entries, (list, tuple)):
                    for e in entries:
                        if isinstance(e, tuple) and len(e) >= 2 and isinstance(e[1], str):
                            title = str(e[0]); href = e[1]; self._flat_toc.append((title, href, depth))
                            if len(e) >= 3 and e[2]: walk_toc(e[2], depth+1)
                        else:
                            walk_toc(e, depth)
                    return
                if hasattr(entries, "href"):
                    title = getattr(entries, "title", None) or getattr(entries, "label", None) or str(entries)
                    href = getattr(entries, "href", "") or getattr(entries, "src", "")
                    self._flat_toc.append((title, href, depth))
                    children = getattr(entries, "children", None) or getattr(entries, "subitems", None) or None
                    if children: walk_toc(children, depth+1)
                    return
                try:
                    for child in entries: walk_toc(child, depth)
                except Exception: return

            walk_toc(toc)
            seen = set(); dedup = []
            for t, h, d in self._flat_toc:
                if not h: continue
                if h not in seen: dedup.append((t, h, d)); seen.add(h)
            self._flat_toc = dedup
            self.sidebar_btn.set_sensitive(bool(self._flat_toc))
            
            # Populate sidebar TOC
            if self._flat_toc:
                self.populate_sidebar_toc()
                # Auto-show sidebar when EPUB is loaded
                if not self._sidebar_visible:
                    self.toggle_sidebar()

            # Add link interception after content is loaded
            GLib.timeout_add(500, self._setup_internal_link_handling)

        except Exception as e:
            print("EPUB load error:", e)
            shutil.rmtree(tempdir, ignore_errors=True)

    def _setup_internal_link_handling(self):
        """Setup JavaScript to intercept internal links and route them properly"""
        js_code = """
        (function() {
            // Function to handle internal link clicks
            function handleInternalLink(event) {
                const target = event.target.closest('a[href]');
                if (!target) return;
                
                const href = target.getAttribute('href');
                if (!href) return;
                
                // Only handle internal links (not external)
                if (!href.startsWith('http://') && !href.startsWith('https://') && !href.startsWith('mailto:')) {
                    event.preventDefault();
                    
                    // Create a temporary anchor to parse the URL
                    const parser = document.createElement('a');
                    parser.href = href;
                    
                    // Extract file and fragment
                    const filePath = parser.pathname.split('/').pop();
                    const fragment = parser.hash.substring(1);  // Remove the '#'
                    
                    // Find which chapter this corresponds to
                    const chapterDivs = document.querySelectorAll('div[data-file]');
                    for (let i = 0; i < chapterDivs.length; i++) {
                        const chapterDiv = chapterDivs[i];
                        const dataFile = chapterDiv.getAttribute('data-file');
                        
                        if (dataFile && dataFile.includes(filePath)) {
                            // Scroll to the chapter
                            chapterDiv.scrollIntoView({behavior: 'smooth', block: 'start'});
                            
                            // If there's a fragment, scroll to it within the chapter
                            if (fragment) {
                                setTimeout(() => {
                                    const targetEl = document.getElementById(fragment) || document.querySelector(`[name="${fragment}"]`);
                                    if (targetEl) {
                                        targetEl.scrollIntoView({behavior: 'smooth', block: 'start'});
                                    }
                                }, 300);  // Small delay to ensure chapter is scrolled to first
                            }
                            break;
                        }
                    }
                }
            }
            
            // Add event listener to document
            document.addEventListener('click', handleInternalLink, true);
        })();
        """
        
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            return False  # Stop the timeout
        except Exception:
            # If webkit not ready yet, try again
            return True

    def _clear_webview_history(self):
        try:
            bfl = self.webview.get_back_forward_list()
            if bfl and hasattr(bfl, "clear"):
                bfl.clear()
            else:
                self.webview.load_html("<!doctype html><html><body></body></html>", "file:///")
        except Exception:
            try: self.webview.load_html("<!doctype html><html><body></body></html>", "file:///")
            except Exception: pass

    def navigate_to_toc_item(self, href):
        """Navigate to a TOC item in the concatenated document"""
        print(f"DEBUG: Navigating to TOC item: '{href}'")
        
        if not href:
            print("DEBUG: Empty href, returning")
            return
            
        # Parse the href to get filename and fragment
        parsed = urllib.parse.urlparse(href)
        filename = parsed.path
        fragment = parsed.fragment
        
        print(f"DEBUG: Parsed href - filename: '{filename}', fragment: '{fragment}'")
        
        # Remove leading slash if present
        if filename.startswith('/'):
            filename = filename[1:]
            print(f"DEBUG: Removed leading slash, filename now: '{filename}'")
            
        print(f"DEBUG: Available spine hrefs: {self._spine_hrefs}")
        
        # Find the chapter index for this file
        chapter_index = None
        for i, spine_href in enumerate(self._spine_hrefs):
            print(f"DEBUG: Comparing '{filename}' with spine_href[{i}]: '{spine_href}'")
            if spine_href == filename or spine_href.endswith('/' + filename) or filename.endswith(spine_href):
                chapter_index = i
                print(f"DEBUG: Found match! Chapter index: {chapter_index}")
                break
                
        if chapter_index is not None:
            print(f"DEBUG: Navigating to chapter {chapter_index}")
            # Navigate to the chapter anchor
            if fragment:
                print(f"DEBUG: Navigating to fragment '{fragment}' in chapter {chapter_index}")
                # If there's a fragment, try to scroll to it within the chapter
                js_code = f"""
                (function() {{
                    console.log('Looking for chapter_{chapter_index}');
                    let chapterEl = document.getElementById('chapter_{chapter_index}');
                    if (chapterEl) {{
                        console.log('Found chapter element, looking for fragment {fragment}');
                        let targetEl = document.getElementById('{fragment}');
                        if (targetEl) {{
                            console.log('Found fragment element, scrolling');
                            targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                            return 'scrolled_to_fragment';
                        }} else {{
                            console.log('Fragment not found by ID, trying name attribute');
                            // Try to find element with name attribute
                            targetEl = document.querySelector('[name="{fragment}"]');
                            if (targetEl) {{
                                console.log('Found fragment by name, scrolling');
                                targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                                return 'scrolled_to_fragment_name';
                            }} else {{
                                console.log('Fragment not found, scrolling to chapter start');
                                // Fallback to chapter start
                                chapterEl.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                                return 'scrolled_to_chapter';
                            }}
                        }}
                    }} else {{
                        console.log('Chapter element not found!');
                        return 'chapter_not_found';
                    }}
                }})();
                """
            else:
                print(f"DEBUG: No fragment, navigating to chapter {chapter_index} start")
                # No fragment, just go to chapter start
                js_code = f"""
                (function() {{
                    console.log('Looking for chapter_{chapter_index}');
                    let chapterEl = document.getElementById('chapter_{chapter_index}');
                    if (chapterEl) {{
                        console.log('Found chapter element, scrolling');
                        chapterEl.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                        return 'scrolled_to_chapter';
                    }} else {{
                        console.log('Chapter element not found!');
                        return 'chapter_not_found';
                    }}
                }})();
                """
            
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            except Exception as e:
                print(f"DEBUG: JavaScript navigation error: {e}")
        else:
            print(f"DEBUG: No chapter found for filename '{filename}', trying fallback")
            # Fallback: try to find the fragment directly in the document
            if fragment:
                print(f"DEBUG: Fallback - looking for fragment '{fragment}' directly")
                js_code = f"""
                (function() {{
                    console.log('Fallback: looking for fragment {fragment}');
                    let targetEl = document.getElementById('{fragment}');
                    if (targetEl) {{
                        console.log('Found fragment by ID, scrolling');
                        targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                        return 'scrolled_to_fragment_fallback';
                    }} else {{
                        console.log('Fragment not found by ID, trying name');
                        targetEl = document.querySelector('[name="{fragment}"]');
                        if (targetEl) {{
                            console.log('Found fragment by name, scrolling');
                            targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                            return 'scrolled_to_fragment_name_fallback';
                        }} else {{
                            console.log('Fragment not found at all');
                            return 'fragment_not_found';
                        }}
                    }}
                }})();
                """
                try:
                    self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                except Exception as e:
                    print(f"DEBUG: Fragment navigation error: {e}")

    def _on_js_result(self, webview, result, user_data):
        """Callback for JavaScript execution results"""
        try:
            js_result = webview.evaluate_javascript_finish(result)
            if js_result:
                result_value = js_result.get_js_value().to_string()
                print(f"DEBUG: JavaScript result: {result_value}")
        except Exception as e:
            print(f"DEBUG: Error getting JavaScript result: {e}")

    def on_font_family_changed(self, dropdown, *args):
        item = dropdown.get_selected_item()
        if not item: return
        font = item.get_string().replace("'", "\\'")
        css = f"* {{ font-family: '{font}' !important; }}"
        script = f"""
        (function() {{
            let s = document.getElementById('__font_override');
            if (!s) {{
                s = document.createElement('style');
                s.id = '__font_override';
                (document.head || document.documentElement).appendChild(s);
            }}
            s.textContent = {json.dumps(css)};
        }})();
        """
        try: self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception: pass

    def _cleanup_epub_tempdir(self):
        if self._epub_tempdir and os.path.exists(self._epub_tempdir):
            try: shutil.rmtree(self._epub_tempdir)
            except Exception: pass
        self._epub_tempdir = None
        self._book = None
        self._base_href = "file:///"
        self.sidebar_btn.set_sensitive(False)
        self._flat_toc = []
        self._spine_hrefs = []

    def on_close_request(self, *args):
        self._cleanup_epub_tempdir()
        return False

if __name__ == "__main__":
    app = Writer(); app.run()
