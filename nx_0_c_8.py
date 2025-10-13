#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tempfile
import shutil
import urllib.parse
from typing import Optional, List, Tuple

# Force software compositing for fewer surprises in embedded views
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Gdk
from ebooklib import epub


# ------------------------------------------------------------
# Application
# ------------------------------------------------------------
class EPUBViewer(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.EPUBViewer",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.book: Optional[epub.EpubBook] = None
        self.toc: List[Tuple[str, str]] = []
        self.temp_dir: Optional[str] = None
        self.current_href: Optional[str] = None
        self.current_spine_index: int = -1

        # Reader settings
        self.font_family = "Serif"
        self.font_size = 16
        self.line_height = 1.6
        self.margin = 30
        self.columns = 2
        self.column_width = 400
        self.column_gap = 20
        self.use_fixed_columns = True

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPUBWindow(application=self)
        win.present()


# ------------------------------------------------------------
# Main Window
# ------------------------------------------------------------
class EPUBWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app: EPUBViewer = kwargs["application"]
        self.set_default_size(1000, 700)
        self.set_title("EPUB Viewer")

        # Gesture controller reference for column-mode page turns
        self.scroll_controller: Optional[Gtk.EventControllerScroll] = None

        # Split view (sidebar + content)
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_max_sidebar_width(300)
        self.split_view.set_min_sidebar_width(200)
        self.split_view.set_show_sidebar(True)

        # ---- Sidebar (TOC) ----
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        toc_header = Adw.HeaderBar()
        toc_label = Gtk.Label(label="Table of Contents")
        toc_label.add_css_class("title")
        toc_header.set_title_widget(toc_label)
        sidebar_box.append(toc_header)

        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect("row-activated", self.on_toc_row_activated)
        sidebar_scrolled = Gtk.ScrolledWindow()
        sidebar_scrolled.set_child(self.toc_list)
        sidebar_scrolled.set_vexpand(True)
        sidebar_box.append(sidebar_scrolled)

        # ---- Content area ----
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.setup_toolbar()
        self.setup_webview()

        self.split_view.set_sidebar(sidebar_box)
        self.split_view.set_content(self.content_box)
        self.set_content(self.split_view)

        # Keyboard navigation controller
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

    def on_scroll_position_changed(self, adjustment):
        self.update_page_info()
        self._refresh_buttons_based_on_adjustment()
    # --------------------------------------------------------
    # UI: Toolbar / Controls
    # --------------------------------------------------------
    def setup_toolbar(self):
        header = Adw.HeaderBar()

        # Toggle sidebar
        toggle_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        toggle_btn.set_active(True)
        toggle_btn.connect("toggled",
            lambda btn: self.split_view.set_show_sidebar(btn.get_active()))
        header.pack_start(toggle_btn)

        # Open file
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        # Prev / Next
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.connect("clicked", lambda *_: self.scroll_viewport(-1))
        self.prev_btn.set_sensitive(False)
        header.pack_start(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.connect("clicked", lambda *_: self.scroll_viewport(1))
        self.next_btn.set_sensitive(False)
        header.pack_end(self.next_btn)

        # Settings popover
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        popover = Gtk.Popover()

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(10)
        grid.set_margin_end(10)
        grid.set_margin_top(10)
        grid.set_margin_bottom(10)

        def add_spin(label, value, lower, upper, step, callback, digits=0, col=0, row=0):
            lbl = Gtk.Label(label=label, halign=Gtk.Align.START)
            adj = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=step)
            spin = Gtk.SpinButton(adjustment=adj, digits=digits, numeric=True)
            spin.connect("value-changed", callback)
            grid.attach(lbl, col, row, 1, 1)
            grid.attach(spin, col+1, row, 1, 1)
            return spin

        # Font family
        font_label = Gtk.Label(label="Font:", halign=Gtk.Align.START)
        font_model = Gtk.StringList.new(["Serif", "Sans", "Monospace"])
        self.font_dropdown = Gtk.DropDown(model=font_model)
        self.font_dropdown.set_selected(0)
        self.font_dropdown.connect("notify::selected", self.on_font_changed)
        grid.attach(font_label, 0, 0, 1, 1)
        grid.attach(self.font_dropdown, 1, 0, 1, 1)

        # Font size
        self.size_spin = add_spin("Size:", self.app.font_size, 8, 48, 1,
                                  self.on_font_size_changed, row=1)

        # Line height
        self.lh_spin = add_spin("Line Height:", self.app.line_height, 0.8, 3.0, 0.1,
                                self.on_line_height_changed, digits=1, row=2)

        # Page margin
        self.margin_spin = add_spin("Margin:", self.app.margin, 0, 100, 5,
                                    self.on_margin_changed, row=3)

        # Column mode
        mode_label = Gtk.Label(label="Col Mode:", halign=Gtk.Align.START)
        mode_model = Gtk.StringList.new(["Fixed Count", "Fixed Width"])
        self.mode_dropdown = Gtk.DropDown(model=mode_model)
        self.mode_dropdown.set_selected(0 if self.app.use_fixed_columns else 1)
        self.mode_dropdown.connect("notify::selected", self.on_mode_changed)
        grid.attach(mode_label, 0, 4, 1, 1)
        grid.attach(self.mode_dropdown, 1, 4, 1, 1)

        # Columns
        self.col_spin = add_spin("Columns:", self.app.columns, 1, 5, 1,
                                 self.on_columns_changed, row=5)

        # Column width
        self.cw_spin = add_spin("Col Width:", self.app.column_width, 200, 800, 10,
                                self.on_column_width_changed, row=6)

        # Column gap
        self.gap_spin = add_spin("Col Gap:", self.app.column_gap, 5, 50, 5,
                                 self.on_column_gap_changed, row=7)

        popover.set_child(grid)
        menu_btn.set_popover(popover)
        header.pack_end(menu_btn)

        self.content_box.append(header)

    # --------------------------------------------------------
    # WebView / Scroller
    # --------------------------------------------------------
    def setup_webview(self):
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_child(self.webview)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)

        # For single-column (vertical) mode, we watch V adjustment
        self.scrolled_window.get_vadjustment().connect("value-changed", self.on_scroll_position_changed)
        self.scrolled_window.get_hadjustment().connect("value-changed", self.on_scroll_position_changed)

        self.content_box.append(self.scrolled_window)

    # --------------------------------------------------------
    # File Open / TOC
    # --------------------------------------------------------
    def on_open_clicked(self, _button):
        dialog = Gtk.FileDialog()
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        filters.append(all_filter)

        dialog.set_filters(filters)
        dialog.set_default_filter(epub_filter)
        dialog.open(self, None, self.on_open_response)

    def on_open_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                self.load_epub(file.get_path())
        except GLib.Error:
            pass

    def load_epub(self, path: str):
        try:
            self.app.book = epub.read_epub(path)
            self.app.toc = self.extract_toc(self.app.book.toc)
            self.populate_toc()

            first_href = None
            if self.app.book.spine:
                item_id = self.app.book.spine[0][0]
                item = self.app.book.get_item_with_id(item_id)
                if item:
                    first_href = item.get_name()

            if first_href:
                self.load_href(first_href)
            elif self.app.toc:
                self.load_href(self.app.toc[0][1])
            else:
                print("No content found in spine or TOC.", file=sys.stderr)
        except Exception as e:
            print(f"EPUB load error: {e}", file=sys.stderr)

    def extract_toc(self, toc_items, base="") -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for item in toc_items:
            if isinstance(item, epub.Link):
                out.append((item.title, urllib.parse.urljoin(base, item.href)))
            elif isinstance(item, tuple) and len(item) >= 2:
                if isinstance(item[0], epub.Link):
                    out.append((item[0].title, urllib.parse.urljoin(base, item[0].href)))
                out.extend(self.extract_toc(item[1], base))
            elif isinstance(item, list):
                out.extend(self.extract_toc(item, base))
        return out

    def populate_toc(self):
        # Clear listbox
        while True:
            row = self.toc_list.get_row_at_index(0)
            if not row:
                break
            self.toc_list.remove(row)

        for title, href in self.app.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(
                label=title, xalign=0, wrap=True, ellipsize=2,
                margin_start=10, margin_top=5, margin_bottom=5
            )
            row.set_child(label)
            row.href = href
            self.toc_list.append(row)

    def on_toc_row_activated(self, _listbox, row):
        if hasattr(row, "href"):
            self.load_href(row.href)

    # --------------------------------------------------------
    # Spine / HREF loading
    # --------------------------------------------------------
    def get_spine_index(self, href: str) -> int:
        if not self.app.book:
            return -1
        clean = href.split("#")[0].lstrip("./")
        for i, (item_id, _) in enumerate(self.app.book.spine):
            it = self.app.book.get_item_with_id(item_id)
            if it and it.get_name() == clean:
                return i
        return -1

    def load_href(self, href: str):
        if not self.app.book:
            return

        clean_href = href.split("#")[0]
        self.app.current_spine_index = self.get_spine_index(clean_href)

        item = self.app.book.get_item_with_href(clean_href)
        if not item:
            for it in self.app.book.get_items():
                if it.get_name() == clean_href:
                    item = it
                    break
        if not item:
            print(f"Content not found for href: {clean_href}", file=sys.stderr)
            return

        self.app.current_href = clean_href

        # Rebuild temp dir with all assets to satisfy relative URLs
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        self.app.temp_dir = tempfile.mkdtemp()

        for it in self.app.book.get_items():
            dest = os.path.join(self.app.temp_dir, it.get_name())
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                with open(dest, "wb") as f:
                    f.write(it.content)
            except Exception as e:
                print(f"Write error for {it.get_name()}: {e}", file=sys.stderr)

        uri = f"file://{os.path.join(self.app.temp_dir, item.get_name())}"
        self.webview.load_uri(uri)

    # --------------------------------------------------------
    # Load lifecycle
    # --------------------------------------------------------
    def on_webview_load_changed(self, _webview, event):
        if event == WebKit.LoadEvent.FINISHED:
            # Reconnect scroll handler
            if self.scrolled_window:
                self.h_adjustment = self.scrolled_window.get_hadjustment()
                if self.h_adjustment:
                    try:
                        self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
                    except Exception:
                        pass
                    self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
            # Apply layout and update navigation
            GLib.timeout_add(100, self._after_load_update)

    def _after_load_update(self):
        self.apply_layout()
        self.reset_scroll_position()
        GLib.timeout_add(100, self.update_nav_buttons)
        return False
        # Always reset to the beginning (left/top)
        is_column_mode = (self.app.columns > 1) or (not self.app.use_fixed_columns)
        
        if is_column_mode:
            # Reset horizontal scroll for column mode
            js = """
            (function() {
                var elem = document.scrollingElement || document.body;
                if (elem) {
                    elem.scrollLeft = 0;
                }
            })();
            """
            self.webview.evaluate_javascript(js, -1, None, None, None)
        
        # Reset vertical scroll
        v_adj = self.scrolled_window.get_vadjustment()
        if v_adj:
            v_adj.set_value(0)
        
        GLib.timeout_add(100, self.update_nav_buttons)
        return False

    # --------------------------------------------------------
    # Layout / CSS Injection
    # --------------------------------------------------------
    def apply_layout(self):
        """Injects responsive column/vertical layout CSS and enforces user font settings."""
        margin = self.app.margin
        font_family = self.app.font_family
        font_size = self.app.font_size
        line_height = self.app.line_height
        columns = self.app.columns
        col_width = self.app.column_width
        column_gap = self.app.column_gap
        use_fixed = self.app.use_fixed_columns

        is_column_mode = columns > 1 or not use_fixed

        if is_column_mode:
            if use_fixed:
                # Fixed column count: calculate exact width needed
                col_style = f"column-count: {columns} !important;"
            else:
                col_style = f"column-width: {col_width}px !important;"

            css = f"""
                html {{
                    margin: 0 !important;
                    padding: 0 !important;
                    height: 100vh !important;
                    width: 100vw !important;
                    overflow: hidden !important;
                }}
                body {{
                    margin: 0 !important;
                    padding: {margin}px !important;
                    box-sizing: border-box !important;
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    height: calc(100vh - {margin * 2}px) !important;
                    width: 100vw !important;

                    {col_style}
                    column-gap: {column_gap}px !important;
                    column-fill: auto !important;

                    overflow-x: auto !important;
                    overflow-y: hidden !important;

                    scroll-snap-type: x mandatory !important;
                    scroll-padding: 0 !important;
                }}
                body::after {{
                    content: '';
                    display: block;
                    scroll-snap-align: end !important;
                }}
                /* Apply user font + size universally */
                * {{
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    font-family: "{font_family}", serif !important;
                }}
                img {{
                    max-width: 100% !important;
                    height: auto !important;
                    display: block !important;
                    margin: 10px auto !important;
                    -webkit-column-break-inside: avoid !important;
                    page-break-inside: avoid !important;
                    break-inside: avoid !important;
                }}
                h1,h2,h3,h4,h5,h6 {{
                    -webkit-column-break-after: avoid !important;
                    page-break-after: avoid !important;
                    break-after: avoid !important;
                    margin-top: 1em !important;
                    margin-bottom: 0.5em !important;
                }}
                p {{
                    margin: 0.5em 0 !important;
                    orphans: 3 !important;
                    widows: 3 !important;
                }}
            """
        else:
            css = f"""
                html {{
                    margin: 0 !important;
                    padding: 0 !important;
                    width: 100% !important;
                    height: 100% !important;
                }}
                body {{
                    margin: 0 !important;
                    padding: {margin}px !important;
                    box-sizing: border-box !important;
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    width: 100% !important;
                    overflow-x: hidden !important;
                    overflow-y: auto !important;
                }}
                /* Apply user font + size universally */
                * {{
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    font-family: "{font_family}", serif !important;
                }}
                img {{
                    max-width: 100% !important;
                    height: auto !important;
                    display: block !important;
                    margin: 10px auto !important;
                }}
            """

        css = css.replace("\\", "\\\\").replace("`", "\\`")
        js = f"""
        (function() {{
            let old = document.getElementById('epub-viewer-style');
            if (old) old.remove();
            let style = document.createElement('style');
            style.id = 'epub-viewer-style';
            style.textContent = `{css}`;
            document.documentElement.appendChild(style);

            // Additional runtime font scaling enforcement (inline style override)
            Array.from(document.querySelectorAll('*')).forEach(e => {{
                e.style.fontSize = '{font_size}px';
                e.style.lineHeight = '{line_height}';
                e.style.fontFamily = '{font_family}', 'serif';
            }});
        }})();
        """

        try:
            self.webview.evaluate_javascript(js, -1, None, None, None)
        except Exception as e:
            print(f"apply_layout error: {e}", file=sys.stderr)

        # Scroller policy + gesture routing
        if is_column_mode:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            if self.scroll_controller:
                self.scrolled_window.remove_controller(self.scroll_controller)
            self.scroll_controller = Gtk.EventControllerScroll()
            self.scroll_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES)
            self.scroll_controller.connect("scroll", self.on_scroll_event)
            self.scrolled_window.add_controller(self.scroll_controller)
        else:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            if self.scroll_controller:
                self.scrolled_window.remove_controller(self.scroll_controller)
                self.scroll_controller = None

        GLib.timeout_add(100, self.update_nav_buttons)


    # --------------------------------------------------------
    # JS Result Helpers (robust across PyGObject/WebKitGTK builds)
    # --------------------------------------------------------
    def _normalize_js_result(self, result) -> str:
        """
        Safely extract a Python string from any result type that
        evaluate_javascript_finish may return (JSCValue, Variant, Value, or str).
        """
        if result is None:
            return ""

        # New WebKitGTK 6.x path: JavaScriptCore.Value (JSCValue)
        if hasattr(result, "to_string"):
            try:
                return str(result.to_string()).strip()
            except Exception:
                pass

        # Older WebKit paths: GLib.Variant / GObject.Value
        if hasattr(result, "get_value"):
            try:
                return str(result.get_value()).strip()
            except Exception:
                pass
        if hasattr(result, "unpack"):
            try:
                return str(result.unpack()).strip()
            except Exception:
                pass

        # Fallback: plain string or repr
        return str(result).strip()

    def _safe_json_loads(self, value: str):
        import json
        value = (value or "").strip()
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception as e:
            print(f"⚠️ Invalid JSON from JS: {value} ({e})", file=sys.stderr)
            return {}

    # --------------------------------------------------------
    # Input handlers
    # --------------------------------------------------------
    def on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_space, Gdk.KEY_Right):
            self.scroll_viewport(1)
            return True
        if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Left):
            self.scroll_viewport(-1)
            return True
        return False

    def on_scroll_event(self, _controller, dx, dy):
        """
        In column mode, convert trackpad/mouse scroll to page turns.
        In single-column vertical mode, let default vertical scrolling happen.
        """
        is_column_mode = (self.app.columns > 1) or (not self.app.use_fixed_columns)
        if not is_column_mode:
            # vertical reading: don't intercept
            return False

        # dead-zone
        if abs(dx) > 0.1:
            self.scroll_viewport(1 if dx > 0 else -1)
            return True
        if abs(dy) > 0.1:
            self.scroll_viewport(1 if dy > 0 else -1)
            return True
        return True  # consume tiny events in column mode

    # --------------------------------------------------------
    # Paging / Navigation
    # --------------------------------------------------------
    def scroll_viewport(self, direction: int):
        """Scroll one viewport (page) horizontally in column mode, vertically otherwise."""
        if not self.webview or not self.app.book:
            return

        is_column_mode = (self.app.columns > 1) or (not self.app.use_fixed_columns)
        if is_column_mode:
            js_code = f"""
            (function() {{
                const body = document.scrollingElement || document.body;
                const vw = window.innerWidth;
                const cur = body.scrollLeft;
                const maxScroll = Math.max(0, body.scrollWidth - vw);
                
                // Calculate column width including gap
                const cols = {self.app.columns if self.app.use_fixed_columns else 'Math.floor((vw - {self.app.margin * 2}) / ({self.app.column_width} + {self.app.column_gap}))'};
                const gap = {self.app.column_gap};
                const contentWidth = vw - {self.app.margin * 2};
                const colWidth = (contentWidth + gap) / cols;
                
                // Snap to nearest column boundary
                let curPage = Math.round(cur / colWidth);
                let targetPage = curPage + ({direction});
                let target = Math.max(0, Math.min(targetPage * colWidth, maxScroll));
                
                body.scrollTo({{ left: target, behavior: 'smooth' }});
                return JSON.stringify({{cur, target, maxScroll, vw, colWidth}});
            }})();
            """

            def process_result(webview, task):
                try:
                    raw = self._normalize_js_result(webview.evaluate_javascript_finish(task))
                    data = self._safe_json_loads(raw)
                    target = data.get("target", 0)
                    max_scroll = data.get("maxScroll", 0)

                    # Edge handoff between chapters
                    if direction > 0 and target >= max_scroll - 2:
                        GLib.timeout_add(300, self.load_next_spine_item)
                    elif direction < 0 and target <= 1:
                        GLib.timeout_add(300, self.load_prev_spine_item)
                except Exception as e:
                    print(f"scroll eval error: {e}", file=sys.stderr)

                self.update_nav_buttons()
                GLib.timeout_add(400, self.update_nav_buttons)

            # Async call
            self.webview.evaluate_javascript(js_code, -1, None, None, None, process_result)

        else:
            # Vertical, single-column
            v_adj = self.scrolled_window.get_vadjustment()
            page = v_adj.get_page_size()
            cur = v_adj.get_value()
            max_scroll = v_adj.get_upper() - page
            target = max(0, min(cur + direction * page * 0.9, max_scroll))
            GLib.timeout_add(50, lambda: v_adj.set_value(target))

            if direction < 0 and cur <= 1:
                GLib.timeout_add(200, self.load_prev_spine_item)
            elif direction > 0 and cur >= max_scroll - 1:
                GLib.timeout_add(200, self.load_next_spine_item)

            self.update_nav_buttons()

    def update_nav_buttons(self, *args):
        if not self.app.book or self.app.current_spine_index < 0:
            self.prev_btn.set_sensitive(False)
            self.next_btn.set_sensitive(False)
            return True

        is_column_mode = (self.app.columns > 1) or (not self.app.use_fixed_columns)
        if is_column_mode:
            js_code = """
            (function() {
                const body = document.scrollingElement || document.body;
                const current = body.scrollLeft;
                const viewport = window.innerWidth;
                const upper = body.scrollWidth;
                const max_pos = Math.max(0, upper - viewport);
                return JSON.stringify({ current, max_pos });
            })();
            """

            def process_result(webview, task):
                try:
                    raw = self._normalize_js_result(webview.evaluate_javascript_finish(task))
                    data = self._safe_json_loads(raw)
                    cur = data.get("current", 0)
                    max_pos = data.get("max_pos", 0)
                    is_first_page = cur <= 5
                    is_last_page = cur >= max_pos - 5
                    self._set_nav_button_sensitivity(is_first_page, is_last_page)
                except Exception as e:
                    print(f"update_nav_buttons eval error: {e}", file=sys.stderr)
                    self.prev_btn.set_sensitive(False)
                    self.next_btn.set_sensitive(False)

            self.webview.evaluate_javascript(js_code, -1, None, None, None, process_result)

        else:
            v = self.scrolled_window.get_vadjustment()
            cur = v.get_value()
            page = v.get_page_size()
            upper = v.get_upper()
            max_pos = max(0, upper - page)
            is_first_page = cur <= 1
            is_last_page = cur >= max_pos - 1
            self._set_nav_button_sensitivity(is_first_page, is_last_page)
        return True

    def _set_nav_button_sensitivity(self, is_first_page: bool, is_last_page: bool):
        spine_length = len(self.app.book.spine) if self.app.book else 0
        can_prev = (not is_first_page) or (self.app.current_spine_index > 0)
        can_next = (not is_last_page) or (self.app.current_spine_index < spine_length - 1)
        self.prev_btn.set_sensitive(can_prev)
        self.next_btn.set_sensitive(can_next)

    def load_next_spine_item(self):
        if not self.app.book or self.app.current_spine_index < 0:
            return
        nxt = self.app.current_spine_index + 1
        if nxt < len(self.app.book.spine):
            item_id = self.app.book.spine[nxt][0]
            it = self.app.book.get_item_with_id(item_id)
            if it:
                self.load_href(it.get_name())

    def load_prev_spine_item(self):
        if not self.app.book or self.app.current_spine_index < 0:
            return
        prev = self.app.current_spine_index - 1
        if prev >= 0:
            item_id = self.app.book.spine[prev][0]
            it = self.app.book.get_item_with_id(item_id)
            if it:
                self.load_href(it.get_name())

    def scroll_to_end_of_page(self):
        # In column mode, jump to last horizontal page; else go to bottom vertically
        if (self.app.columns > 1) or (not self.app.use_fixed_columns):
            js = f"""
            (function() {{
                const body = document.scrollingElement || document.body;
                const vw = window.innerWidth;
                const cols = {self.app.columns if self.app.use_fixed_columns else 'Math.floor((vw - {self.app.margin * 2}) / ({self.app.column_width} + {self.app.column_gap}))'};
                const gap = {self.app.column_gap};
                const contentWidth = vw - {self.app.margin * 2};
                const colWidth = (contentWidth + gap) / cols;
                const maxScroll = Math.max(0, body.scrollWidth - vw);
                
                // Calculate last full column page
                const lastPage = Math.floor(maxScroll / colWidth) * colWidth;
                body.scrollTo({{ left: lastPage, behavior: 'auto' }});
            }})();
            """
            self.webview.evaluate_javascript(js, -1, None, None, None)
        else:
            v = self.scrolled_window.get_vadjustment()
            v.set_value(v.get_upper() - v.get_page_size())
        GLib.timeout_add(100, self.update_nav_buttons)
        return False

    # --------------------------------------------------------
    # Setting change handlers
    # --------------------------------------------------------
    def on_font_changed(self, dropdown, _pspec):
        families = ["Serif", "Sans", "Monospace"]
        self.app.font_family = families[dropdown.get_selected()]
        self.apply_layout()

    def on_font_size_changed(self, spin):
        self.app.font_size = int(spin.get_value())
        self.apply_layout()

    def on_line_height_changed(self, spin):
        self.app.line_height = spin.get_value()
        self.apply_layout()

    def on_margin_changed(self, spin):
        self.app.margin = int(spin.get_value())
        self.apply_layout()

    def on_mode_changed(self, dropdown, _pspec):
        self.app.use_fixed_columns = (dropdown.get_selected() == 0)
        self.apply_layout()

    def on_columns_changed(self, spin):
        self.app.columns = int(spin.get_value())
        self.apply_layout()

    def on_column_width_changed(self, spin):
        self.app.column_width = int(spin.get_value())
        self.apply_layout()

    def on_column_gap_changed(self, spin):
        self.app.column_gap = int(spin.get_value())
        self.apply_layout()

    # --------------------------------------------------------
    # Cleanup
    # --------------------------------------------------------
    def do_close_request(self):
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        return False


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    app = EPUBViewer()
    app.run(sys.argv)
