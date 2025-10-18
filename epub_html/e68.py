#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango, PangoCairo

from ebooklib import epub

def _make_abs_for_resource(url: str, file_dir: str, tempdir: str):
    url = url.strip()
    if not url:
        return url
    if url.startswith(('#', 'http://', 'https://', 'data:', 'mailto:', 'javascript:', 'file://')):
        return url
    parts = url.split('#', 1)
    rel = parts[0]
    frag = ('#' + parts[1]) if len(parts) == 2 else ''
    candidates = []
    if os.path.isabs(rel):
        candidates.append(rel)
    else:
        if file_dir:
            candidates.append(os.path.normpath(os.path.join(file_dir, rel)))
        if tempdir:
            candidates.append(os.path.normpath(os.path.join(tempdir, rel)))
    suffixes = ['', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.html', '.htm', '.xhtml']
    for cand in candidates:
        for s in suffixes:
            path_try = cand if cand.endswith(s) else cand + s
            if os.path.exists(path_try):
                return "file://" + path_try + frag
    if tempdir:
        joined = urllib.parse.urljoin("file://" + tempdir.replace(os.sep, '/') + "/", url)
        return joined
    return url

def _rewrite_resource_urls(html: str, file_dir: str, tempdir: str) -> str:
    def attr_repl(m):
        attr = m.group('attr')
        quote = m.group('quote')
        url = m.group('url')
        new = _make_abs_for_resource(url, file_dir, tempdir)
        return f"{attr}{quote}{new}{quote}"
    html = re.sub(r'(?P<attr>\b(?:src|href)\s*=\s*)(?P<quote>["\'])(?P<url>[^"\']+)(?P=quote)',
                  attr_repl, html, flags=re.I)
    def cssurl_repl(m):
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
    div, p, span, article, section, main, aside, nav, header, footer {
        background-color: transparent !important;
        color: inherit !important;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #ffffff !important;
    }
    a, a:link, a:visited {
        color: #6db3f2 !important;
    }
    a:hover, a:active {
        color: #9cc9f7 !important;
    }
    code, pre, kbd, samp {
        background-color: #1e1e1e !important;
        color: #f8f8f2 !important;
        border: 1px solid #444 !important;
    }
    table { background-color: transparent !important; border-color: #555 !important; }
    th, td { background-color: transparent !important; color: inherit !important; border-color: #555 !important; }
    th { background-color: #3a3a3a !important; color: #ffffff !important; }
    blockquote { background-color: #3a3a3a !important; color: inherit !important; border-left-color: #6db3f2 !important; }
    input, textarea, select { background-color: #3a3a3a !important; color: #e6e6e6 !important; border-color: #555 !important; }
    hr { border-color: #555 !important; }
    *[style*="background-color: white"], *[style*="background: white"] { background-color: transparent !important; }
    *[style*="color: black"] { color: #e6e6e6 !important; }
}
.force-dark-mode { background-color: #2d2d2d !important; color: #e6e6e6 !important; }
.force-dark-mode div, .force-dark-mode p, .force-dark-mode span, .force-dark-mode article, .force-dark-mode section, .force-dark-mode main, .force-dark-mode aside, .force-dark-mode nav, .force-dark-mode header, .force-dark-mode footer { background-color: transparent !important; color: inherit !important; }
.force-dark-mode h1, .force-dark-mode h2, .force-dark-mode h3, .force-dark-mode h4, .force-dark-mode h5, .force-dark-mode h6 { color: #ffffff !important; }
.force-dark-mode a, .force-dark-mode a:link, .force-dark-mode a:visited { color: #6db3f2 !important; }
.force-dark-mode a:hover, .force-dark-mode a:active { color: #9cc9f7 !important; }
.force-dark-mode code, .force-dark-mode pre, .force-dark-mode kbd, .force-dark-mode samp { background-color: #1e1e1e !important; color: #f8f8f2 !important; border: 1px solid #444 !important; }
.force-dark-mode table { background-color: transparent !important; border-color: #555 !important; }
.force-dark-mode th, .force-dark-mode td { background-color: transparent !important; color: inherit !important; border-color: #555 !important; }
.force-dark-mode th { background-color: #3a3a3a !important; color: #ffffff !important; }
.force-dark-mode blockquote { background-color: #3a3a3a !important; color: inherit !important; border-left-color: #6db3f2 !important; }
.force-dark-mode hr { border-color: #555 !important; }
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
        # register application-level action "set-columns"
        col_action = Gio.SimpleAction.new("set-columns", GLib.VariantType.new("s"))
        col_action.connect("activate", self._on_app_set_columns)
        self.add_action(col_action)
        self.connect("activate", self.on_activate)

    def _on_app_set_columns(self, action, param):
        # find active window and call its apply_column_layout
        win = self.get_active_window()
        if win and hasattr(win, "apply_column_layout"):
            try:
                n = int(param.get_string())
            except Exception:
                n = 0
            win.apply_column_layout(n)

    def on_activate(self, app):
        # create and keep reference to window (so app action can find it via get_active_window)
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

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        toolbar_view = Adw.ToolbarView()
        main_box.append(toolbar_view)
        header = Adw.HeaderBar()
        header.add_css_class("flat-header")
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

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

        # Column layout button (MenuButton) - put in header so it's visible
        self.column_btn = Gtk.MenuButton()
        self.column_btn.set_icon_name("view-column-symbolic")
        self.column_btn.add_css_class("flat")
        self.column_btn.set_tooltip_text("Column Layout")
        header.pack_start(self.column_btn)  # <-- ensure visible in header

        # Create column menu that calls the application action "app.set-columns"
        column_menu = Gio.Menu()
        column_menu.append("Single Column", "app.set-columns('1')")
        column_menu.append("Two Columns", "app.set-columns('2')")
        column_menu.append("Three Columns", "app.set-columns('3')")
        column_menu.append("Four Columns", "app.set-columns('4')")
        column_menu.append("Remove Columns", "app.set-columns('0')")
        self.column_btn.set_menu_model(column_menu)

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

        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_paned.set_shrink_start_child(False)
        self.main_paned.set_shrink_end_child(False)
        self.main_paned.set_resize_start_child(False)
        self.main_paned.set_resize_end_child(True)
        toolbar_view.set_content(self.main_paned)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.set_size_request(300, -1)
        self.sidebar.add_css_class("sidebar")

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

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar.append(separator)

        sidebar_scroll = Gtk.ScrolledWindow(vexpand=True)
        sidebar_scroll.set_margin_start(6)
        sidebar_scroll.set_margin_end(6)
        sidebar_scroll.set_margin_bottom(6)
        self.sidebar_listbox = Gtk.ListBox()
        self.sidebar_listbox.add_css_class("navigation-sidebar")
        sidebar_scroll.set_child(self.sidebar_listbox)
        self.sidebar.append(sidebar_scroll)

        content_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.webview = WebKit.WebView()
        content_scroll.set_child(self.webview)
        self.webview.load_html("<!doctype html><html><body><p>Open an EPUB file to begin reading...</p></body></html>", "file:///")

        self.main_paned.set_end_child(content_scroll)

        self.connect("close-request", self.on_close_request)
        self.webview.connect("notify::title", self._update_nav_buttons)
        self.webview.connect("notify::uri", self._update_nav_buttons)
        self.webview.connect("decide-policy", self.on_decide_policy)

    def on_decide_policy(self, webview, decision, decision_type):
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            uri = nav_action.get_request().get_uri()
            if (uri.startswith('http://') or uri.startswith('https://') or 
                uri.startswith('mailto:') or uri.startswith('ftp://')):
                decision.ignore()
                self.open_external_link(uri)
                return True
        return False

    def open_external_link(self, uri):
        try:
            import subprocess
            subprocess.run(['xdg-open', uri], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
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

    def apply_column_layout(self, columns):
        cols_int = int(columns)
        js_template = """
        (function() {
            const cols = __COLS__;
            let wrapper = document.getElementById('__viewer_column_wrapper');
            
            // Clean up previous event listeners
            if (wrapper && wrapper.__cleanup_handlers) {
                wrapper.__cleanup_handlers.forEach(cleanup => cleanup());
                wrapper.__cleanup_handlers = [];
            }

            // Create wrapper if it doesn't exist
            if (!wrapper) {
                wrapper = document.createElement('div');
                wrapper.id = '__viewer_column_wrapper';
                wrapper.__cleanup_handlers = [];
                
                // Move all body content into wrapper
                while (document.body.firstChild) {
                    wrapper.appendChild(document.body.firstChild);
                }
                document.body.appendChild(wrapper);
                
                // Set up basic document styles
                document.documentElement.style.height = '100%';
                document.documentElement.style.overflow = 'hidden';
                document.body.style.height = '100%';
                document.body.style.margin = '0';
                document.body.style.padding = '0';
                document.body.style.overflow = 'hidden';
            }

            const vw = window.innerWidth;
            const vh = window.innerHeight;

            // Reset to single column mode
            if (cols <= 1) {
                wrapper.style.cssText = '';
                wrapper.style.width = '100%';
                wrapper.style.height = '100%';
                wrapper.style.overflowX = 'hidden';
                wrapper.style.overflowY = 'auto';
                wrapper.style.boxSizing = 'border-box';
                
                document.body.style.overflow = 'hidden';
                return;
            }

            // Multi-column setup
            wrapper.style.cssText = '';
            wrapper.style.boxSizing = 'border-box';
            wrapper.style.height = vh + 'px';
            wrapper.style.overflowX = 'auto';
            wrapper.style.overflowY = 'hidden';
            wrapper.style.scrollBehavior = 'smooth';
            
            // Use CSS columns - set column-width for better control
            const columnGap = 20;
            const columnWidth = Math.floor((vw - (cols - 1) * columnGap) / cols);
            wrapper.style.columnWidth = columnWidth + 'px';
            wrapper.style.columnCount = cols;
            wrapper.style.columnGap = columnGap + 'px';
            wrapper.style.columnRule = '1px solid rgba(0,0,0,0.1)';
            wrapper.style.columnFill = 'auto';
            
            // Prevent breaks inside important elements
            const breakStyles = [
                'webkitColumnBreakInside: avoid',
                'MozColumnBreakInside: avoid', 
                'pageBreakInside: avoid',
                'breakInside: avoid'
            ];
            breakStyles.forEach(style => {
                try {
                    const [prop, value] = style.split(': ');
                    wrapper.style[prop] = value;
                } catch(e) {}
            });
            
            // Set wrapper width to accommodate all columns + gaps
            const totalWidth = (columnWidth * cols) + ((cols - 1) * columnGap);
            wrapper.style.width = totalWidth + 'px';

            // Enable horizontal scrolling with mouse wheel
            function handleWheel(event) {
                // Allow vertical scrolling when not in column mode or when shift is held
                if (cols <= 1 || event.shiftKey) {
                    return;
                }
                
                // Convert vertical wheel movement to horizontal scrolling
                if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
                    event.preventDefault();
                    
                    // Calculate scroll amount (adjust multiplier as needed)
                    const scrollAmount = event.deltaY * 5;
                    wrapper.scrollLeft += scrollAmount;
                }
            }

            // Keyboard navigation for columns
            function handleKeydown(event) {
                if (cols <= 1) return;
                
                // Don't handle keys when user is typing in input fields
                if (document.activeElement && 
                    (document.activeElement.tagName === 'INPUT' || 
                     document.activeElement.tagName === 'TEXTAREA' ||
                     document.activeElement.isContentEditable)) {
                    return;
                }
                
                const currentScroll = wrapper.scrollLeft;
                const columnWidth = totalWidth / cols;
                
                switch(event.key) {
                    case 'ArrowRight':
                    case 'PageDown':
                        event.preventDefault();
                        wrapper.scrollLeft = Math.min(currentScroll + columnWidth, totalWidth - vw);
                        break;
                    case 'ArrowLeft':
                    case 'PageUp':
                        event.preventDefault();
                        wrapper.scrollLeft = Math.max(currentScroll - columnWidth, 0);
                        break;
                    case 'Home':
                        event.preventDefault();
                        wrapper.scrollLeft = 0;
                        break;
                    case 'End':
                        event.preventDefault();
                        wrapper.scrollLeft = totalWidth - vw;
                        break;
                }
            }

            // Resize handler to maintain layout
            function handleResize() {
                const newVw = window.innerWidth;
                const newVh = window.innerHeight;
                const newColumnWidth = Math.floor((newVw - (cols - 1) * columnGap) / cols);
                const newTotalWidth = (newColumnWidth * cols) + ((cols - 1) * columnGap);
                
                wrapper.style.height = newVh + 'px';
                wrapper.style.columnWidth = newColumnWidth + 'px';
                wrapper.style.width = newTotalWidth + 'px';
                
                // Maintain relative scroll position
                const scrollRatio = wrapper.scrollLeft / (totalWidth - vw);
                wrapper.scrollLeft = scrollRatio * (newTotalWidth - newVw);
            }

            // Add event listeners and store cleanup functions
            wrapper.addEventListener('wheel', handleWheel, { passive: false });
            wrapper.__cleanup_handlers.push(() => {
                wrapper.removeEventListener('wheel', handleWheel);
            });

            window.addEventListener('keydown', handleKeydown);
            wrapper.__cleanup_handlers.push(() => {
                window.removeEventListener('keydown', handleKeydown);
            });

            window.addEventListener('resize', handleResize);
            wrapper.__cleanup_handlers.push(() => {
                window.removeEventListener('resize', handleResize);
            });

        })();
        """
        js_code = js_template.replace("__COLS__", str(cols_int))
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
        except Exception as e:
            print(f"JavaScript execution error: {e}")
            pass

    def toggle_sidebar(self, *args):
        if self._sidebar_visible:
            self.main_paned.set_start_child(None)
            self._sidebar_visible = False
            self.sidebar_btn.set_icon_name("view-dual-symbolic")
        else:
            self.main_paned.set_start_child(self.sidebar)
            self.main_paned.set_position(300)
            self._sidebar_visible = True
            self.sidebar_btn.set_icon_name("view-restore-symbolic")

    def toggle_dark_mode(self):
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
            hbox.set_margin_start(12 + (depth * 16))
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
        self.sidebar_listbox.connect("row-activated", on_sidebar_row_activated)

    def _update_nav_buttons(self, *a):
        try:
            self.back_btn.set_sensitive(self.webview.can_go_back())
            self.forward_btn.set_sensitive(self.webview.can_go_forward())
        except Exception:
            pass

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

            self._spine_hrefs = spine_hrefs

            bodies = []; head_html = None
            for i, rel in enumerate(spine_hrefs):
                full = os.path.join(tempdir, rel)
                if not os.path.exists(full): continue
                with open(full, "r", encoding="utf-8", errors="replace") as f: txt = f.read()
                if head_html is None:
                    m = re.search(r'(<head\b[^>]*>.*?</head>)', txt, re.S | re.I)
                    head_html = m.group(1) if m else ''
                file_dir = os.path.dirname(full)
                full_txt = txt
                rewritten = _rewrite_resource_urls(full_txt, file_dir, tempdir)
                m2r = re.search(r'<body\b[^>]*>(.*?)</body>', rewritten, re.S | re.I)
                body_content = m2r.group(1) if m2r else rewritten
                chapter_anchor = f'<div id="chapter_{i}" data-file="{rel}"></div>'
                bodies.append(chapter_anchor + body_content)

            final_head = head_html or '<meta charset="utf-8">'
            base_href = "file://" + tempdir.replace(os.sep, '/') + "/"
            concatenated = f"<!doctype html>\n<html>\n{final_head}\n<base href=\"{base_href}\">\n<body>\n" + "\n<hr/>\n".join(bodies) + "\n</body>\n</html>"
            concatenated = inject_css_into_html(concatenated)

            self._epub_tempdir = tempdir
            self._base_href = base_href
            self._flat_toc = []
            self.sidebar_btn.set_sensitive(False)

            self._clear_webview_history()
            self.webview.load_html(concatenated, base_href)

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

            if self._flat_toc:
                self.populate_sidebar_toc()
                if not self._sidebar_visible:
                    self.toggle_sidebar()

            GLib.timeout_add(500, self._setup_internal_link_handling)

        except Exception as e:
            print("EPUB load error:", e)
            shutil.rmtree(tempdir, ignore_errors=True)

    def _setup_internal_link_handling(self):
        js_code = """
        (function() {
            function handleInternalLink(event) {
                const target = event.target.closest('a[href]');
                if (!target) return;
                const href = target.getAttribute('href');
                if (!href) return;
                if (!href.startsWith('http://') && !href.startsWith('https://') && !href.startsWith('mailto:')) {
                    event.preventDefault();
                    const parser = document.createElement('a');
                    parser.href = href;
                    const filePath = parser.pathname.split('/').pop();
                    const fragment = parser.hash.substring(1);
                    const chapterDivs = document.querySelectorAll('div[data-file]');
                    for (let i = 0; i < chapterDivs.length; i++) {
                        const chapterDiv = chapterDivs[i];
                        const dataFile = chapterDiv.getAttribute('data-file');
                        if (dataFile && dataFile.includes(filePath)) {
                            chapterDiv.scrollIntoView({behavior: 'smooth', block: 'start'});
                            if (fragment) {
                                setTimeout(() => {
                                    const targetEl = document.getElementById(fragment) || document.querySelector(`[name="${fragment}"]`);
                                    if (targetEl) { targetEl.scrollIntoView({behavior: 'smooth', block: 'start'}); }
                                }, 300);
                            }
                            break;
                        }
                    }
                }
            }
            document.addEventListener('click', handleInternalLink, true);
        })();
        """
        try:
            self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            return False
        except Exception:
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
        print(f"DEBUG: Navigating to TOC item: '{href}'")
        if not href:
            print("DEBUG: Empty href, returning")
            return
        parsed = urllib.parse.urlparse(href)
        filename = parsed.path
        fragment = parsed.fragment
        if filename.startswith('/'):
            filename = filename[1:]
        chapter_index = None
        for i, spine_href in enumerate(self._spine_hrefs):
            if spine_href == filename or spine_href.endswith('/' + filename) or filename.endswith(spine_href):
                chapter_index = i
                break
        if chapter_index is not None:
            if fragment:
                js_code = f"""
                (function() {{
                    let chapterEl = document.getElementById('chapter_{chapter_index}');
                    if (chapterEl) {{
                        let targetEl = document.getElementById('{fragment}');
                        if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                        else {{
                            targetEl = document.querySelector('[name="{fragment}"]');
                            if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                            else {{ chapterEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                        }}
                    }}
                }})();
                """
            else:
                js_code = f"""
                (function() {{
                    let chapterEl = document.getElementById('chapter_{chapter_index}');
                    if (chapterEl) {{ chapterEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                }})();
                """
            try:
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception as e:
                print(f"DEBUG: JavaScript navigation error: {e}")
        else:
            if fragment:
                js_code = f"""
                (function() {{
                    let targetEl = document.getElementById('{fragment}');
                    if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                    else {{
                        targetEl = document.querySelector('[name="{fragment}"]');
                        if (targetEl) {{ targetEl.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                    }}
                }})();
                """
                try:
                    self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                except Exception as e:
                    print(f"DEBUG: Fragment navigation error: {e}")

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
    import sys, signal
    Adw.init()
    app = Writer()
    try:
        app.run(None)
    except KeyboardInterrupt:
        # clean shutdown on Ctrl-C
        try: app.quit()
        except Exception: pass
        sys.exit(0)
