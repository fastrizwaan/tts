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

from gi.repository import Gtk, Adw, WebKit, Gio, GLib
from ebooklib import epub


class EPUBViewer(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.EPUBViewer',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.book: Optional[epub.EpubBook] = None
        self.toc: List[Tuple[str, str]] = []
        self.temp_dir: Optional[str] = None
        self.current_href: Optional[str] = None

        self.font_family = "Serif"
        self.font_size = 16
        self.margin = 20
        self.columns = 1
        self.column_width = 300

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPUBWindow(application=self)
        win.present()


class EPUBWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app = kwargs['application']
        self.set_default_size(1000, 700)
        self.set_title("EPUB Viewer")

        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_max_sidebar_width(300)
        self.split_view.set_min_sidebar_width(200)

        # Sidebar: TOC
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.connect("row-activated", self.on_toc_row_activated)
        sidebar_scrolled = Gtk.ScrolledWindow()
        sidebar_scrolled.set_child(self.toc_list)
        sidebar_scrolled.set_vexpand(True)

        # Main content area
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.setup_toolbar()
        self.setup_webview()

        self.split_view.set_sidebar(sidebar_scrolled)
        self.split_view.set_content(self.content_box)
        self.set_content(self.split_view)

    def setup_toolbar(self):
        header = Adw.HeaderBar()
        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        prev_btn.connect("clicked", lambda *_: self.scroll_viewport(-1))
        header.pack_start(prev_btn)

        next_btn = Gtk.Button(icon_name="go-next-symbolic")
        next_btn.connect("clicked", lambda *_: self.scroll_viewport(1))
        header.pack_end(next_btn)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        popover = Gtk.Popover()

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(10)
        grid.set_margin_end(10)
        grid.set_margin_top(10)
        grid.set_margin_bottom(10)

        # Font family
        font_label = Gtk.Label(label="Font:", halign=Gtk.Align.START)
        font_model = Gtk.StringList()
        for f in ["Serif", "Sans", "Monospace"]:
            font_model.append(f)
        self.font_dropdown = Gtk.DropDown(model=font_model)
        self.font_dropdown.set_selected(0)
        self.font_dropdown.connect("notify::selected", self.on_font_changed)
        grid.attach(font_label, 0, 0, 1, 1)
        grid.attach(self.font_dropdown, 1, 0, 1, 1)

        # Font size
        size_label = Gtk.Label(label="Size:", halign=Gtk.Align.START)
        size_adj = Gtk.Adjustment(value=self.app.font_size, lower=8, upper=48, step_increment=1)
        self.size_spin = Gtk.SpinButton(adjustment=size_adj, numeric=True)
        self.size_spin.connect("value-changed", self.on_font_size_changed)
        grid.attach(size_label, 0, 1, 1, 1)
        grid.attach(self.size_spin, 1, 1, 1, 1)

        # Margin
        margin_label = Gtk.Label(label="Margin:", halign=Gtk.Align.START)
        margin_adj = Gtk.Adjustment(value=self.app.margin, lower=0, upper=100, step_increment=1)
        self.margin_spin = Gtk.SpinButton(adjustment=margin_adj, numeric=True)
        self.margin_spin.connect("value-changed", self.on_margin_changed)
        grid.attach(margin_label, 0, 2, 1, 1)
        grid.attach(self.margin_spin, 1, 2, 1, 1)

        # Columns
        col_label = Gtk.Label(label="Columns:", halign=Gtk.Align.START)
        col_adj = Gtk.Adjustment(value=self.app.columns, lower=1, upper=3, step_increment=1)
        self.col_spin = Gtk.SpinButton(adjustment=col_adj, numeric=True)
        self.col_spin.connect("value-changed", self.on_columns_changed)
        grid.attach(col_label, 0, 3, 1, 1)
        grid.attach(self.col_spin, 1, 3, 1, 1)

        # Column width
        cw_label = Gtk.Label(label="Col Width:", halign=Gtk.Align.START)
        cw_adj = Gtk.Adjustment(value=self.app.column_width, lower=50, upper=800, step_increment=10)
        self.cw_spin = Gtk.SpinButton(adjustment=cw_adj, numeric=True)
        self.cw_spin.connect("value-changed", self.on_column_width_changed)
        grid.attach(cw_label, 0, 4, 1, 1)
        grid.attach(self.cw_spin, 1, 4, 1, 1)

        popover.set_child(grid)
        menu_btn.set_popover(popover)
        header.pack_end(menu_btn)

        self.content_box.append(header)

    def setup_webview(self):
        self.webview = WebKit.WebView()
        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_child(self.webview)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        self.content_box.append(self.scrolled_window)

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        dialog.set_default_filter(epub_filter)
        dialog.open(self, None, self.on_open_response)

    def on_open_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                self.load_epub(path)
        except GLib.Error:
            pass

    def load_epub(self, path: str):
        try:
            self.app.book = epub.read_epub(path)
            self.app.toc = self.extract_toc(self.app.book.toc)
            self.populate_toc()
            if self.app.toc:
                self.load_href(self.app.toc[0][1])
        except Exception as e:
            print(f"EPUB load error: {e}", file=sys.stderr)

    def extract_toc(self, toc_items, base="") -> List[Tuple[str, str]]:
        result = []
        for item in toc_items:
            if isinstance(item, epub.Link):
                href = urllib.parse.urljoin(base, item.href)
                result.append((item.title, href))
            elif isinstance(item, tuple):
                # Handle nested tuples
                title, children = item
                result.append((title, children[0].href if children else ""))
                result.extend(self.extract_toc(children, base))
            elif isinstance(item, list):
                result.extend(self.extract_toc(item, base))
        return result

    def populate_toc(self):
        self.toc_list.remove_all()
        for title, href in self.app.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=title, xalign=0, margin_start=10, ellipsize=2)
            row.set_child(label)
            row.href = href
            self.toc_list.append(row)

    def load_href(self, href: str):
        if not self.app.book:
            return
        self.app.current_href = href
        item = self.app.book.get_item_with_href(href)
        if not item:
            clean_name = href.split('#')[0].lstrip('./')
            for it in self.app.book.get_items():
                if it.get_name() == clean_name:
                    item = it
                    break
        if not item:
            return

        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        self.app.temp_dir = tempfile.mkdtemp()

        for it in self.app.book.get_items():
            dest = os.path.join(self.app.temp_dir, it.get_name())
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'wb') as f:
                f.write(it.get_content())

        full_path = os.path.join(self.app.temp_dir, item.get_name())
        uri = f"file://{full_path}"
        self.webview.load_uri(uri)

    def on_toc_row_activated(self, listbox, row):
        if hasattr(row, 'href'):
            self.load_href(row.href)

    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            self.apply_layout()

    def apply_layout(self):
        margin = self.app.margin
        font_family = self.app.font_family
        font_size = self.app.font_size
        columns = self.app.columns
        col_width = self.app.column_width

        # Build robust CSS with !important
        css = f"""
            * {{
                column-count: {columns} !important;
                column-width: {col_width}px !important;
                column-gap: 20px !important;
                font-family: "{font_family}", serif !important;
                font-size: {font_size}px !important;
                margin: 0 !important;
                padding: 0 !important;
                border: none !important;
                background: transparent !important;
                color: inherit !important;
            }}
            html, body {{
                margin: 0 !important;
                padding: {margin}px !important;
                height: auto !important;
                min-height: 100vh !important;
                width: {'100%' if columns == 1 else 'max-content'} !important;
                overflow: hidden !important;
                display: block !important;
            }}
            body {{
                column-fill: auto !important;
            }}
            /* Prevent EPUB from breaking layout */
            iframe, embed, object {{
                display: none !important;
            }}
        """

        # Escape backticks for JS
        css_escaped = css.replace("\\", "\\\\").replace("`", "\\`")

        js = f"""
        (function() {{
            // Remove old style
            let old = document.getElementById('epub-viewer-style');
            if (old) old.remove();

            // Inject new style
            let style = document.createElement('style');
            style.id = 'epub-viewer-style';
            style.textContent = `{css_escaped}`;
            document.documentElement.appendChild(style);

            // Also inject into all iframes (if any)
            try {{
                let frames = document.querySelectorAll('iframe');
                for (let frame of frames) {{
                    try {{
                        let frameDoc = frame.contentDocument || frame.contentWindow.document;
                        if (!frameDoc) continue;
                        let fstyle = frameDoc.getElementById('epub-viewer-style');
                        if (fstyle) fstyle.remove();
                        fstyle = frameDoc.createElement('style');
                        fstyle.id = 'epub-viewer-style';
                        fstyle.textContent = `{css_escaped}`;
                        frameDoc.documentElement.appendChild(fstyle);
                    }} catch (e) {{ /* cross-origin */ }}
                }}
            }} catch (e) {{}}

            console.log('Body width:', document.body.scrollWidth, 'columns:', {columns});
        }})();
        """

        # Correct WebKitGTK 6 call signature: length=-1, world=None, source_uri=None
        try:
            self.webview.evaluate_javascript(js, -1, None, None, None)
        except TypeError:
            # Fallback in case of slightly different introspection signature
            self.webview.evaluate_javascript(js, -1, None, None, None, None)

        # Update scroll policy
        if columns == 1:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        else:
            self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)

    def on_font_changed(self, dropdown, _pspec):
        families = ["Serif", "Sans", "Monospace"]
        self.app.font_family = families[dropdown.get_selected()]
        self.apply_layout()

    def on_font_size_changed(self, spin):
        self.app.font_size = int(spin.get_value())
        self.apply_layout()

    def on_margin_changed(self, spin):
        self.app.margin = int(spin.get_value())
        self.apply_layout()

    def on_columns_changed(self, spin):
        self.app.columns = int(spin.get_value())
        self.apply_layout()

    def on_column_width_changed(self, spin):
        self.app.column_width = int(spin.get_value())
        self.apply_layout()

    def scroll_viewport(self, direction: int):
        h_adj = self.scrolled_window.get_hadjustment()
        v_adj = self.scrolled_window.get_vadjustment()

        if self.app.columns == 1:
            page = v_adj.get_page_size()
            new_val = v_adj.get_value() + direction * page
            v_adj.set_value(max(0, min(new_val, v_adj.get_upper() - v_adj.get_page_size())))
        else:
            page = h_adj.get_page_size()
            new_val = h_adj.get_value() + direction * page
            unit = self.app.column_width + 20  # width + gap
            snapped = round(new_val / unit) * unit
            h_adj.set_value(max(0, min(snapped, h_adj.get_upper() - h_adj.get_page_size())))

    def do_close_request(self):
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        return False


if __name__ == "__main__":
    app = EPUBViewer()
    app.run(sys.argv)
