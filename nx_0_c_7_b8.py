#!/usr/bin/env python3

import os
import tempfile
import shutil
import sys
import urllib.parse
from typing import Optional, List, Tuple

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')

from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Gdk
from ebooklib import epub


class EPUBViewer(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.EPUBViewer',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        # model / prefs
        self.book: Optional[epub.EpubBook] = None
        self.toc: List[Tuple[str, str]] = []
        self.temp_dir: Optional[str] = None
        self.current_href: Optional[str] = None
        self.current_spine_index: int = -1

        # user prefs
        self.font_family = "Serif"
        self.font_size = 16
        self.line_height = 1.6
        self.margin = 30
        self.columns = 2
        self.column_width = 420
        self.column_gap = 24
        self.use_fixed_columns = True  # True=Fixed Count, False=Fixed Width

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPUBWindow(application=self)
        win.present()


class EPUBWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app: EPUBViewer = kwargs['application']
        self.set_default_size(1100, 760)
        self.set_title("EPUB Viewer")

        # split view
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_show_sidebar(True)
        self.set_content(self.split_view)

        # sidebar (TOC)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toc_header = Adw.HeaderBar()
        toc_label = Gtk.Label(label="Table of Contents")
        toc_label.add_css_class("title-4")
        toc_header.set_title_widget(toc_label)
        sidebar_box.append(toc_header)

        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect("row-activated", self.on_toc_row_activated)
        sidebar_scrolled = Gtk.ScrolledWindow(vexpand=True)
        sidebar_scrolled.set_child(self.toc_list)
        sidebar_box.append(sidebar_scrolled)

        # main content
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.split_view.set_sidebar(sidebar_box)
        self.split_view.set_content(self.content_box)

        # header + settings
        self.setup_toolbar()

        # webview + scroller
        self.setup_webview()

        # resize handling (gtk4-safe)
        self.content_box.connect("notify::allocation", self.on_content_allocation_changed)

        # keyboard nav
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

        self.resize_timeout_id = None
        self._content_width_cached = -1

    # -------------------- UI: Header/Settings --------------------
    def setup_toolbar(self):
        header = Adw.HeaderBar()

        toggle_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        toggle_btn.set_active(True)
        toggle_btn.connect("toggled", lambda btn: self.split_view.set_show_sidebar(btn.get_active()))
        header.pack_start(toggle_btn)

        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.connect("clicked", lambda *_: self.scroll_viewport(-1))
        self.prev_btn.set_sensitive(False)
        header.pack_start(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.connect("clicked", lambda *_: self.scroll_viewport(1))
        self.next_btn.set_sensitive(False)
        header.pack_end(self.next_btn)

        # settings popover
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        pop = Gtk.Popover()
        grid = Gtk.Grid(column_spacing=10, row_spacing=10,
                        margin_start=10, margin_end=10,
                        margin_top=10, margin_bottom=10)

        # Font family
        grid.attach(Gtk.Label(label="Font:", halign=Gtk.Align.START), 0, 0, 1, 1)
        font_model = Gtk.StringList.new(["Serif", "Sans", "Monospace"])
        self.font_dropdown = Gtk.DropDown(model=font_model)
        self.font_dropdown.set_selected(0)
        self.font_dropdown.connect("notify::selected", self.on_font_changed)
        grid.attach(self.font_dropdown, 1, 0, 1, 1)

        # Font size
        grid.attach(Gtk.Label(label="Size:", halign=Gtk.Align.START), 0, 1, 1, 1)
        self.size_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=self.app.font_size, lower=8, upper=48, step_increment=1), numeric=True)
        self.size_spin.connect("value-changed", self.on_font_size_changed)
        grid.attach(self.size_spin, 1, 1, 1, 1)

        # Line height
        grid.attach(Gtk.Label(label="Line Height:", halign=Gtk.Align.START), 0, 2, 1, 1)
        self.lh_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=self.app.line_height, lower=0.8, upper=3.0, step_increment=0.1), digits=1, numeric=True)
        self.lh_spin.connect("value-changed", self.on_line_height_changed)
        grid.attach(self.lh_spin, 1, 2, 1, 1)

        # Margin
        grid.attach(Gtk.Label(label="Margin:", halign=Gtk.Align.START), 0, 3, 1, 1)
        self.margin_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=self.app.margin, lower=0, upper=100, step_increment=5), numeric=True)
        self.margin_spin.connect("value-changed", self.on_margin_changed)
        grid.attach(self.margin_spin, 1, 3, 1, 1)

        # Mode
        grid.attach(Gtk.Label(label="Col Mode:", halign=Gtk.Align.START), 0, 4, 1, 1)
        mode_model = Gtk.StringList.new(["Fixed Count", "Fixed Width"])
        self.mode_dropdown = Gtk.DropDown(model=mode_model)
        self.mode_dropdown.set_selected(0 if self.app.use_fixed_columns else 1)
        self.mode_dropdown.connect("notify::selected", self.on_mode_changed)
        grid.attach(self.mode_dropdown, 1, 4, 1, 1)

        # Columns
        grid.attach(Gtk.Label(label="Columns:", halign=Gtk.Align.START), 0, 5, 1, 1)
        self.col_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=self.app.columns, lower=1, upper=6, step_increment=1), numeric=True)
        self.col_spin.connect("value-changed", self.on_columns_changed)
        grid.attach(self.col_spin, 1, 5, 1, 1)

        # Column width
        grid.attach(Gtk.Label(label="Col Width:", halign=Gtk.Align.START), 0, 6, 1, 1)
        self.cw_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=self.app.column_width, lower=200, upper=1200, step_increment=10), numeric=True)
        self.cw_spin.connect("value-changed", self.on_column_width_changed)
        grid.attach(self.cw_spin, 1, 6, 1, 1)

        # Column gap
        grid.attach(Gtk.Label(label="Col Gap:", halign=Gtk.Align.START), 0, 7, 1, 1)
        self.gap_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=self.app.column_gap, lower=0, upper=200, step_increment=5), numeric=True)
        self.gap_spin.connect("value-changed", self.on_column_gap_changed)
        grid.attach(self.gap_spin, 1, 7, 1, 1)

        pop.set_child(grid)
        menu_btn.set_popover(pop)
        header.pack_end(menu_btn)

        self.content_box.append(header)

    # -------------------- WebView --------------------
    def setup_webview(self):
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scrolled_window.set_child(self.webview)

        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.scrolled_window.add_controller(scroll_controller)

        # keep buttons updated
        self.scrolled_window.get_hadjustment().connect("value-changed", self.update_nav_buttons)
        self.scrolled_window.get_vadjustment().connect("value-changed", self.update_nav_buttons)

        self.content_box.append(self.scrolled_window)

    # -------------------- File open / TOC --------------------
    def on_open_clicked(self, _button):
        dialog = Gtk.FileDialog()
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(epub_filter)
        dialog.open(self, None, self.on_open_response)

    def on_open_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                if path:
                    self.load_epub(path)
        except GLib.Error:
            pass

    def load_epub(self, path: str):
        try:
            self.app.book = epub.read_epub(path)
            self.app.toc = self.extract_toc(self.app.book.toc)
            self.populate_toc()
            # first spine href
            first_href = None
            if self.app.book.spine:
                first_item_id = self.app.book.spine[0][0]
                first_item = self.app.book.get_item_with_id(first_item_id)
                if first_item:
                    first_href = first_item.get_name()
            if first_href:
                self.load_href(first_href)
            elif self.app.toc:
                self.load_href(self.app.toc[0][1])
        except Exception as e:
            print("EPUB load error:", e, file=sys.stderr)

    def extract_toc(self, toc_items, base="") -> List[Tuple[str, str]]:
        result = []
        for item in toc_items:
            if isinstance(item, epub.Link):
                href = urllib.parse.urljoin(base, item.href)
                result.append((item.title, href))
            elif isinstance(item, tuple) and len(item) >= 2:
                if isinstance(item[0], epub.Link):
                    href = urllib.parse.urljoin(base, item[0].href)
                    result.append((item[0].title, href))
                result.extend(self.extract_toc(item[1], base))
            elif isinstance(item, list):
                result.extend(self.extract_toc(item, base))
        return result

    def populate_toc(self):
        while True:
            row = self.toc_list.get_row_at_index(0)
            if not row:
                break
            self.toc_list.remove(row)

        for title, href in self.app.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=title, xalign=0, margin_start=10,
                              margin_top=5, margin_bottom=5, ellipsize=2, wrap=True)
            row.href = href
            row.set_child(label)
            self.toc_list.append(row)

    def on_toc_row_activated(self, _listbox, row):
        if hasattr(row, 'href'):
            self.load_href(row.href)

    # -------------------- Column math (SplitView-aware) --------------------
    def _content_width(self) -> int:
        """Get the actual width available for content (uses proper GTK4 API)"""
        # Use get_width() instead of deprecated get_allocation()
        width = self.content_box.get_width()
        if width > 0:
            return width
        # Fallback to window width if content box width isn't ready yet
        return self.get_width()

    def calculate_column_width_exact(self) -> float:
        """Exact column width that fits columns into SplitView content without bleed."""
        width = max(0, self._content_width())
        margin = self.app.margin
        gap = self.app.column_gap
        if width <= 0:
            return float(self.app.column_width)

        available = max(50, width - 2 * margin)
        if self.app.use_fixed_columns:
            cols = max(1, int(self.app.columns))
            total_gap = (cols - 1) * gap
            col_w = (available - total_gap) / cols
            return max(50.0, col_w)
        else:
            # desired column width but clamp to available
            return max(50.0, min(float(self.app.column_width), float(available)))

    # -------------------- Apply layout (CSS injection) --------------------
    def apply_layout(self):
        margin = self.app.margin
        font_family = self.app.font_family
        font_size = self.app.font_size
        line_height = self.app.line_height
        gap = self.app.column_gap
        columns = self.app.columns
        use_fixed = self.app.use_fixed_columns
        col_width = self.calculate_column_width_exact()

        css = f"""
        html, body {{
            margin: 0;
            padding: {margin}px;
            box-sizing: border-box;
            font-family: "{font_family}", serif;
            font-size: {font_size}px;
            line-height: {line_height};
            height: 100vh;
            overflow-x: auto;
            overflow-y: hidden;
            column-gap: {gap}px;
            {'column-count:' + str(columns) + ';' if use_fixed else ''}
            column-width: {col_width}px;
            column-fill: auto;
            scroll-snap-type: x mandatory;
        }}
        * {{
            box-sizing: border-box;
        }}
        body > * {{
            font-family: "{font_family}", serif;
            font-size: {font_size}px;
            line-height: {line_height};
        }}
        /* Column snap points for smooth scrolling */
        body::after {{
            content: '';
            display: block;
            height: 1px;
        }}
        img {{
            max-width: 100%;
            height: auto;
            display: block;
            margin: 10px auto;
            break-inside: avoid;
            -webkit-column-break-inside: avoid;
            page-break-inside: avoid;
        }}
        h1,h2,h3,h4,h5,h6 {{
            margin-top: 1em;
            margin-bottom: .5em;
            break-after: avoid;
            -webkit-column-break-after: avoid;
        }}
        p {{ 
            margin: .5em 0; 
            orphans: 3; 
            widows: 3; 
        }}
        blockquote {{ 
            margin: 1em 0; 
            padding-left: 1em; 
            border-left: 3px solid #ccc; 
        }}
        """

        js = f"""
        (function() {{
            let old = document.getElementById('epub-viewer-style');
            if (old) old.remove();
            let style = document.createElement('style');
            style.id = 'epub-viewer-style';
            style.textContent = `{css.replace("\\", "\\\\").replace("`", "\\`")}`;
            document.documentElement.appendChild(style);
            // reset scroll to column boundary after layout
            let body = document.scrollingElement || document.documentElement;
            body.scrollTo({{ left: 0, behavior: 'auto' }});
        }})();
        """
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
        GLib.timeout_add(220, self.snap_to_nearest_step)
        GLib.timeout_add(250, self.update_nav_buttons)

    # -------------------- Scroll / Page turn with snapping --------------------
    def _page_step_js(self, direction: int) -> str:
        step = self.calculate_column_width_exact() + self.app.column_gap
        step = max(1.0, step)
        return f"""
        (function() {{
            var body = document.scrollingElement || document.documentElement;
            var step = {step};
            var cur = body.scrollLeft;
            var vw = window.innerWidth || document.documentElement.clientWidth;
            var maxScroll = Math.max(0, body.scrollWidth - vw);
            
            // Calculate how many columns fit in viewport
            var colsPerView = Math.max(1, Math.floor(vw / step));
            
            // Move by full viewport width (all visible columns)
            var target = cur + ({direction}) * colsPerView * step;
            
            // Snap to nearest column boundary
            target = Math.round(target / step) * step;
            if (target < 0) target = 0;
            if (target > maxScroll) target = maxScroll;
            
            body.scrollTo({{ left: target, behavior: 'smooth' }});
            
            // Ensure we're snapped after animation
            setTimeout(function(){{
                var cur2 = body.scrollLeft;
                var snap = Math.round(cur2 / step) * step;
                if (snap < 0) snap = 0;
                if (snap > maxScroll) snap = maxScroll;
                body.scrollTo({{ left: snap, behavior: 'auto' }});
            }}, 420);
            
            return JSON.stringify({{cur:cur, target:target, max:maxScroll}});
        }})();
        """

    def scroll_viewport(self, direction: int):
        if not self.app.book:
            return
        js = self._page_step_js(direction)

        def after(webview, result, user_data=None):
            try:
                val = webview.evaluate_javascript_finish(result)
                # Fixed: Use to_json() instead of get_js_value()
                json_str_raw = val.to_json(0)
                if not json_str_raw:
                    print("Warning: JS evaluation returned no value in scroll_viewport.", file=sys.stderr)
                    return
                
                import json
                try:
                    # Attempt to parse the returned value as JSON
                    parsed_val = json.loads(json_str_raw)
                    
                    # Check if the parsed value is a dictionary (object) as expected
                    if not isinstance(parsed_val, dict):
                        print(f"Warning: Expected object from JS, got {type(parsed_val).__name__}: {parsed_val}", file=sys.stderr)
                        return
                    
                    d = parsed_val
                    at_start = float(d['cur']) <= 1.0
                    at_end = float(d['cur']) >= float(d['max']) - 1.0
                    if direction < 0 and at_start and self._has_prev_spine():
                        GLib.timeout_add(180, self.load_prev_spine_item)
                    elif direction > 0 and at_end and self._has_next_spine():
                        GLib.timeout_add(180, self.load_next_spine_item)
                except (json.JSONDecodeError, TypeError) as je:
                    print(f"Error parsing JSON from JS in scroll_viewport: {je}, Raw: {json_str_raw}", file=sys.stderr)
                    return
            except Exception as e:
                print(f"Error in scroll_viewport callback: {e}", file=sys.stderr)
            self.update_nav_buttons()

        self.webview.evaluate_javascript(js, -1, None, None, None, after, None)

    def snap_to_nearest_step(self):
        step = self.calculate_column_width_exact() + self.app.column_gap
        step = max(1.0, step)
        js = f"""
        (function() {{
            var body = document.scrollingElement || document.documentElement;
            var cur = body.scrollLeft;
            var vw = window.innerWidth || document.documentElement.clientWidth;
            var maxScroll = Math.max(0, body.scrollWidth - vw);
            var snap = Math.round(cur / {step}) * {step};
            if (snap < 0) snap = 0;
            if (snap > maxScroll) snap = maxScroll;
            if (Math.abs(cur - snap) > 2) {{
                body.scrollTo({{ left: snap, behavior: 'auto' }});
            }}
        }})();
        """
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)
        return False

    # -------------------- Buttons enable/disable --------------------
    def update_nav_buttons(self, *args):
        if not self.app.book:
            self.prev_btn.set_sensitive(False)
            self.next_btn.set_sensitive(False)
            return

        js = """
        (function(){
            var body = document.scrollingElement || document.documentElement;
            var left = body.scrollLeft;
            var vw = window.innerWidth || document.documentElement.clientWidth;
            var maxv = Math.max(0, body.scrollWidth - vw);
            return JSON.stringify({left:left, max:maxv});
        })();
        """

        def finish(webview, result, user_data=None):
            try:
                val = webview.evaluate_javascript_finish(result)
                # Fixed: Use to_json() instead of get_js_value()
                json_str_raw = val.to_json(0)
                if not json_str_raw:
                    print("Warning: JS evaluation returned no value in update_nav_buttons.", file=sys.stderr)
                    self.prev_btn.set_sensitive(self._has_prev_spine())
                    self.next_btn.set_sensitive(self._has_next_spine())
                    return
                
                import json
                try:
                    # Attempt to parse the returned value as JSON
                    parsed_val = json.loads(json_str_raw)
                    
                    # Check if the parsed value is a dictionary (object) as expected
                    if not isinstance(parsed_val, dict):
                        print(f"Warning: Expected object from JS, got {type(parsed_val).__name__}: {parsed_val}", file=sys.stderr)
                        self.prev_btn.set_sensitive(self._has_prev_spine())
                        self.next_btn.set_sensitive(self._has_next_spine())
                        return
                    
                    d = parsed_val
                    left, maxv = float(d['left']), float(d['max'])
                    at_start = left <= 1
                    at_end = left >= maxv - 1
                    prev_ok = (not at_start) or self._has_prev_spine()
                    next_ok = (not at_end) or self._has_next_spine()
                    self.prev_btn.set_sensitive(prev_ok)
                    self.next_btn.set_sensitive(next_ok)
                except (json.JSONDecodeError, TypeError) as je:
                    print(f"Error parsing JSON from JS in update_nav_buttons: {je}, Raw: {json_str_raw}", file=sys.stderr)
                    self.prev_btn.set_sensitive(self._has_prev_spine())
                    self.next_btn.set_sensitive(self._has_next_spine())
                    return
            except Exception as e:
                print(f"Error updating nav buttons: {e}", file=sys.stderr)
                self.prev_btn.set_sensitive(self._has_prev_spine())
                self.next_btn.set_sensitive(self._has_next_spine())

        self.webview.evaluate_javascript(js, -1, None, None, None, finish, None)

    def _has_prev_spine(self) -> bool:
        return self.app.book is not None and self.app.current_spine_index > 0

    def _has_next_spine(self) -> bool:
        return self.app.book is not None and self.app.current_spine_index < (len(self.app.book.spine) - 1)

    # -------------------- Keyboard & Mouse --------------------
    def on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_space, Gdk.KEY_Right):
            self.scroll_viewport(1)
            return True
        if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Left):
            self.scroll_viewport(-1)
            return True
        return False

    def on_scroll_event(self, _controller, dx, dy):
        if abs(dy) > 0.4 or abs(dx) > 0.4:
            direction = 1 if (dy > 0 or dx < 0) else -1
            self.scroll_viewport(direction)
            return True
        return False

    # -------------------- WebView load/hooks --------------------
    def on_webview_load_changed(self, _webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            GLib.timeout_add(150, self.apply_layout)
            GLib.timeout_add(280, self.update_nav_buttons)

    def on_content_allocation_changed(self, widget, pspec=None):
        # Fixed: Use get_width() instead of deprecated get_allocation()
        new_width = widget.get_width()
        if new_width != self._content_width_cached and new_width > 0:
            self._content_width_cached = new_width
            if self.resize_timeout_id:
                GLib.source_remove(self.resize_timeout_id)
            self.resize_timeout_id = GLib.timeout_add(200, self._after_resize)

    def _after_resize(self):
        self.resize_timeout_id = None
        self.apply_layout()
        return False

    # -------------------- Spine / href loading --------------------
    def get_spine_index(self, href: str) -> int:
        if not self.app.book:
            return -1
        clean_href = href.split('#')[0].lstrip('./')
        for i, (item_id, _) in enumerate(self.app.book.spine):
            item = self.app.book.get_item_with_id(item_id)
            if item and item.get_name() == clean_href:
                return i
        return -1

    def load_href(self, href: str):
        if not self.app.book:
            return

        clean_href = href.split('#')[0]
        self.app.current_spine_index = self.get_spine_index(clean_href)

        item = self.app.book.get_item_with_href(clean_href)
        if not item:
            for it in self.app.book.get_items():
                if it.get_name() == clean_href:
                    item = it
                    break
        if not item:
            print(f"Missing href: {clean_href}", file=sys.stderr)
            return

        self.app.current_href = clean_href

        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        self.app.temp_dir = tempfile.mkdtemp()

        for it in self.app.book.get_items():
            try:
                dest = os.path.join(self.app.temp_dir, it.get_name())
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    f.write(it.content)
            except Exception as e:
                print("save error:", it.get_name(), e, file=sys.stderr)

        full_path = os.path.join(self.app.temp_dir, item.get_name())
        uri = f"file://{full_path}"
        self.webview.load_uri(uri)

    def load_next_spine_item(self):
        if not self._has_next_spine():
            return
        idx = self.app.current_spine_index + 1
        item_id = self.app.book.spine[idx][0]
        it = self.app.book.get_item_with_id(item_id)
        if it:
            self.load_href(it.get_name())

    def load_prev_spine_item(self):
        if not self._has_prev_spine():
            return
        idx = self.app.current_spine_index - 1
        item_id = self.app.book.spine[idx][0]
        it = self.app.book.get_item_with_id(item_id)
        if it:
            self.load_href(it.get_name())
            # jump to end of previous chapter (rightmost)
            js = """
            (function(){
                var body = document.scrollingElement || document.documentElement;
                var vw = window.innerWidth || document.documentElement.clientWidth;
                var maxv = Math.max(0, body.scrollWidth - vw);
                body.scrollTo({ left: maxv, behavior: 'auto' });
            })();
            """
            GLib.timeout_add(250, lambda: self.webview.evaluate_javascript(js, -1, None, None, None, None, None))

    # -------------------- Settings handlers --------------------
    def on_font_changed(self, dropdown, _pspec):
        families = ["Serif", "Sans", "Monospace"]
        idx = dropdown.get_selected()
        if 0 <= idx < len(families):
            self.app.font_family = families[idx]
        self.apply_layout()

    def on_font_size_changed(self, spin):
        self.app.font_size = int(spin.get_value())
        self.apply_layout()

    def on_line_height_changed(self, spin):
        self.app.line_height = float(spin.get_value())
        self.apply_layout()

    def on_margin_changed(self, spin):
        self.app.margin = int(spin.get_value())
        self.apply_layout()

    def on_mode_changed(self, dropdown, _pspec):
        self.app.use_fixed_columns = dropdown.get_selected() == 0
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

    # -------------------- Cleanup --------------------
    def do_close_request(self):
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        return False


if __name__ == "__main__":
    app = EPUBViewer()
    app.run(sys.argv)
