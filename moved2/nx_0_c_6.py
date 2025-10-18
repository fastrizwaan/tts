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
        self.book: Optional[epub.EpubBook] = None
        self.toc: List[Tuple[str, str]] = []
        self.temp_dir: Optional[str] = None
        self.current_href: Optional[str] = None
        self.current_spine_index: int = -1

        self.font_family = "Serif"
        self.font_size = 16
        self.line_height = 1.6
        self.margin = 30
        self.columns = 1
        self.column_width = 400
        self.column_gap = 20
        self.use_fixed_columns = True

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
        self.split_view.set_show_sidebar(True)

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

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.setup_toolbar()
        self.setup_webview()

        self.split_view.set_sidebar(sidebar_box)
        self.split_view.set_content(self.content_box)
        self.set_content(self.split_view)
        
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

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

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        popover = Gtk.Popover()

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(10)
        grid.set_margin_end(10)
        grid.set_margin_top(10)
        grid.set_margin_bottom(10)

        font_label = Gtk.Label(label="Font:", halign=Gtk.Align.START)
        font_model = Gtk.StringList()
        for f in ["Serif", "Sans", "Monospace"]:
            font_model.append(f)
        self.font_dropdown = Gtk.DropDown(model=font_model)
        self.font_dropdown.set_selected(0)
        self.font_dropdown.connect("notify::selected", self.on_font_changed)
        grid.attach(font_label, 0, 0, 1, 1)
        grid.attach(self.font_dropdown, 1, 0, 1, 1)

        size_label = Gtk.Label(label="Size:", halign=Gtk.Align.START)
        size_adj = Gtk.Adjustment(value=self.app.font_size, lower=8, upper=48, step_increment=1)
        self.size_spin = Gtk.SpinButton(adjustment=size_adj, numeric=True)
        self.size_spin.connect("value-changed", self.on_font_size_changed)
        grid.attach(size_label, 0, 1, 1, 1)
        grid.attach(self.size_spin, 1, 1, 1, 1)

        lh_label = Gtk.Label(label="Line Height:", halign=Gtk.Align.START)
        lh_adj = Gtk.Adjustment(value=self.app.line_height, lower=0.8, upper=3.0, step_increment=0.1)
        self.lh_spin = Gtk.SpinButton(adjustment=lh_adj, digits=1, numeric=True)
        self.lh_spin.connect("value-changed", self.on_line_height_changed)
        grid.attach(lh_label, 0, 2, 1, 1)
        grid.attach(self.lh_spin, 1, 2, 1, 1)

        margin_label = Gtk.Label(label="Margin:", halign=Gtk.Align.START)
        margin_adj = Gtk.Adjustment(value=self.app.margin, lower=0, upper=100, step_increment=5)
        self.margin_spin = Gtk.SpinButton(adjustment=margin_adj, numeric=True)
        self.margin_spin.connect("value-changed", self.on_margin_changed)
        grid.attach(margin_label, 0, 3, 1, 1)
        grid.attach(self.margin_spin, 1, 3, 1, 1)

        mode_label = Gtk.Label(label="Col Mode:", halign=Gtk.Align.START)
        mode_model = Gtk.StringList()
        mode_model.append("Fixed Count")
        mode_model.append("Fixed Width")
        self.mode_dropdown = Gtk.DropDown(model=mode_model)
        self.mode_dropdown.set_selected(0 if self.app.use_fixed_columns else 1)
        self.mode_dropdown.connect("notify::selected", self.on_mode_changed)
        grid.attach(mode_label, 0, 4, 1, 1)
        grid.attach(self.mode_dropdown, 1, 4, 1, 1)

        col_label = Gtk.Label(label="Columns:", halign=Gtk.Align.START)
        col_adj = Gtk.Adjustment(value=self.app.columns, lower=1, upper=5, step_increment=1)
        self.col_spin = Gtk.SpinButton(adjustment=col_adj, numeric=True)
        self.col_spin.connect("value-changed", self.on_columns_changed)
        grid.attach(col_label, 0, 5, 1, 1)
        grid.attach(self.col_spin, 1, 5, 1, 1)

        cw_label = Gtk.Label(label="Col Width:", halign=Gtk.Align.START)
        cw_adj = Gtk.Adjustment(value=self.app.column_width, lower=200, upper=800, step_increment=10)
        self.cw_spin = Gtk.SpinButton(adjustment=cw_adj, numeric=True)
        self.cw_spin.connect("value-changed", self.on_column_width_changed)
        grid.attach(cw_label, 0, 6, 1, 1)
        grid.attach(self.cw_spin, 1, 6, 1, 1)
        
        gap_label = Gtk.Label(label="Col Gap:", halign=Gtk.Align.START)
        gap_adj = Gtk.Adjustment(value=self.app.column_gap, lower=5, upper=50, step_increment=5)
        self.gap_spin = Gtk.SpinButton(adjustment=gap_adj, numeric=True)
        self.gap_spin.connect("value-changed", self.on_column_gap_changed)
        grid.attach(gap_label, 0, 7, 1, 1)
        grid.attach(self.gap_spin, 1, 7, 1, 1)

        popover.set_child(grid)
        menu_btn.set_popover(popover)
        header.pack_end(menu_btn)

        self.content_box.append(header)

    def setup_webview(self):
        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_child(self.webview)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL)
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.scrolled_window.add_controller(scroll_controller)
        
        self.scrolled_window.get_hadjustment().connect("value-changed", self.update_nav_buttons)
        self.scrolled_window.get_vadjustment().connect("value-changed", self.update_nav_buttons)
        
        self.content_box.append(self.scrolled_window)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Page_Down or keyval == Gdk.KEY_space:
            self.scroll_viewport(1)
            return True
        elif keyval == Gdk.KEY_Page_Up:
            self.scroll_viewport(-1)
            return True
        elif keyval == Gdk.KEY_Right:
            if self.app.columns > 1 or not self.app.use_fixed_columns:
                self.scroll_one_column(1)
            else:
                self.scroll_viewport(1)
            return True
        elif keyval == Gdk.KEY_Left:
            if self.app.columns > 1 or not self.app.use_fixed_columns:
                self.scroll_one_column(-1)
            else:
                self.scroll_viewport(-1)
            return True
        return False

    def on_scroll_event(self, controller, dx, dy):
        if abs(dy) > 0.1:
            direction = 1 if dy > 0 else -1
            self.scroll_viewport(direction)
            return True
        return False

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        
        filter_store = Gio.ListStore.new(Gtk.FileFilter)
        filter_store.append(epub_filter)
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filter_store.append(all_filter)
        
        dialog.set_filters(filter_store)
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
            else:
                print("No content found in spine or TOC.", file=sys.stderr)

        except Exception as e:
            print(f"EPUB load error: {e}", file=sys.stderr)

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
            if row:
                self.toc_list.remove(row)
            else:
                break
                
        for title, href in self.app.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=title, xalign=0, margin_start=10, margin_top=5, margin_bottom=5, ellipsize=2, wrap=True)
            row.set_child(label)
            row.href = href
            self.toc_list.append(row)

    def get_spine_index(self, href: str) -> int:
        if not self.app.book: return -1
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
            print(f"Content item not found for href: {clean_href}", file=sys.stderr)
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
                print(f"Error saving file {it.get_name()}: {e}", file=sys.stderr)

        full_path = os.path.join(self.app.temp_dir, item.get_name())
        uri = f"file://{full_path}"
        
        fragment = href.split('#')[1] if '#' in href else ''
        if fragment:
            uri += f"#{fragment}"

        self.webview.load_uri(uri)

    def on_toc_row_activated(self, listbox, row):
        if hasattr(row, 'href'):
            self.load_href(row.href)

    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            self.apply_layout()
            GLib.timeout_add(100, self.reset_scroll_position)

    def reset_scroll_position(self):
        if self.scrolled_window.get_hadjustment().get_value() != 0:
            self.scrolled_window.get_hadjustment().set_value(0)
        if self.scrolled_window.get_vadjustment().get_value() != 0:
            self.scrolled_window.get_vadjustment().set_value(0)
        self.update_nav_buttons()
        return False

    def apply_layout(self):
        margin = self.app.margin
        font_family = self.app.font_family
        font_size = self.app.font_size
        line_height = self.app.line_height
        columns = self.app.columns
        col_width = self.app.column_width
        column_gap = self.app.column_gap
        use_fixed = self.app.use_fixed_columns

        if columns > 1 or not use_fixed:
            if use_fixed:
                css = f"""
                    html {{
                        margin: 0 !important;
                        padding: 0 !important;
                        width: 100% !important;
                        height: 100% !important;
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
                        
                        column-count: {columns} !important;
                        column-gap: {column_gap}px !important;
                        column-fill: auto !important;
                        
                        word-wrap: normal !important;
                        overflow-wrap: normal !important;
                        hyphens: none !important;
                    }}
                """
            else:
                css = f"""
                    html {{
                        margin: 0 !important;
                        padding: 0 !important;
                        width: 100% !important;
                        height: 100% !important;
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
                        
                        column-width: {col_width}px !important;
                        column-gap: {column_gap}px !important;
                        column-fill: auto !important;
                        
                        word-wrap: normal !important;
                        overflow-wrap: normal !important;
                        hyphens: none !important;
                    }}
                """
            
            css += f"""
                * {{
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                    box-sizing: border-box !important;
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
                
                h1, h2, h3, h4, h5, h6 {{
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
                
                blockquote {{
                    margin: 1em 0 !important;
                    padding-left: 1em !important;
                    border-left: 3px solid #ccc !important;
                }}
                
                div, section, article {{
                    max-width: 100% !important;
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
                }}
                
                * {{
                    font-family: "{font_family}", serif !important;
                    font-size: {font_size}px !important;
                    line-height: {line_height} !important;
                }}
                
                img {{
                    max-width: 100% !important;
                    height: auto !important;
                    display: block !important;
                    margin: 10px 0 !important;
                }}
                
                h1, h2, h3, h4, h5, h6 {{
                    margin-top: 1em !important;
                    margin-bottom: 0.5em !important;
                }}
                
                p {{
                    margin: 0.5em 0 !important;
                }}
                
                blockquote {{
                    margin: 1em 0 !important;
                    padding-left: 1em !important;
                    border-left: 3px solid #ccc !important;
                }}
            """

        css_escaped = css.replace("\\", "\\\\").replace("`", "\\`")

        js_inject = f"""
        (function() {{
            let old = document.getElementById('epub-viewer-style');
            if (old) old.remove();
            let style = document.createElement('style');
            style.id = 'epub-viewer-style';
            style.textContent = `{css_escaped}`;
            document.documentElement.appendChild(style);
        }})();
        """

        try:
            self.webview.evaluate_javascript(js_inject, -1, None, None, None)
        except:
            try:
                self.webview.evaluate_javascript(js_inject, -1, None, None, None, None)
            except:
                pass

        if columns == 1 and use_fixed:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        else:
            self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            
        GLib.timeout_add(100, self.update_nav_buttons)

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

    def scroll_one_column(self, direction: int):
        h_adj = self.scrolled_window.get_hadjustment()
        
        current = h_adj.get_value()
        page_size = h_adj.get_page_size()
        upper = h_adj.get_upper()
        
        if self.app.use_fixed_columns:
            viewport_width = page_size
            total_gap = self.app.column_gap * (self.app.columns - 1)
            col_width = (viewport_width - total_gap - self.app.margin * 2) / self.app.columns
            scroll_amount = col_width + self.app.column_gap
        else:
            scroll_amount = self.app.column_width + self.app.column_gap
        
        new_val = current + direction * scroll_amount
        
        if direction == -1 and current <= 1:
            self.load_prev_spine_item()
            return
        elif direction == 1 and current >= upper - page_size - 1:
            self.load_next_spine_item()
            return
        
        col_pos = round(new_val / scroll_amount) * scroll_amount
        col_pos = max(0, min(col_pos, upper - page_size))
        
        h_adj.set_value(col_pos)
        self.update_nav_buttons()

    def scroll_viewport(self, direction: int):
        h_adj = self.scrolled_window.get_hadjustment()
        v_adj = self.scrolled_window.get_vadjustment()

        if self.app.columns == 1 and self.app.use_fixed_columns:
            page = v_adj.get_page_size()
            current = v_adj.get_value()
            upper = v_adj.get_upper()
            new_val = current + direction * page * 0.9
            
            if direction == -1 and current <= 1:
                self.load_prev_spine_item()
                return
            elif direction == 1 and current >= upper - page - 1:
                self.load_next_spine_item()
                return
            
            new_val = max(0, min(new_val, upper - page))
            v_adj.set_value(new_val)
        else:
            page_size = h_adj.get_page_size()
            current = h_adj.get_value()
            upper = h_adj.get_upper()
            
            new_val = current + direction * page_size
            
            if direction == -1 and current <= 1:
                self.load_prev_spine_item()
                return
            elif direction == 1 and current >= upper - page_size - 1:
                self.load_next_spine_item()
                return
            
            new_val = max(0, min(new_val, upper - page_size))
            h_adj.set_value(new_val)
        
        self.update_nav_buttons()

    def load_next_spine_item(self):
        if not self.app.book or self.app.current_spine_index < 0: return

        spine_length = len(self.app.book.spine)
        next_index = self.app.current_spine_index + 1
        
        if next_index < spine_length:
            item_id = self.app.book.spine[next_index][0]
            next_item = self.app.book.get_item_with_id(item_id)
            if next_item:
                self.load_href(next_item.get_name())
        
        
    def load_prev_spine_item(self):
        if not self.app.book or self.app.current_spine_index < 0: return

        prev_index = self.app.current_spine_index - 1
        
        if prev_index >= 0:
            item_id = self.app.book.spine[prev_index][0]
            prev_item = self.app.book.get_item_with_id(item_id)
            if prev_item:
                self.load_href(prev_item.get_name())
                GLib.timeout_add(200, self.scroll_to_end_of_page)

    def scroll_to_end_of_page(self):
        h_adj = self.scrolled_window.get_hadjustment()
        v_adj = self.scrolled_window.get_vadjustment()
        
        if self.app.columns == 1 and self.app.use_fixed_columns:
            page_size = v_adj.get_page_size()
            v_adj.set_value(max(0, v_adj.get_upper() - page_size))
        else:
            page_size = h_adj.get_page_size()
            h_adj.set_value(max(0, h_adj.get_upper() - page_size))
        
        self.update_nav_buttons()
        return False

    def update_nav_buttons(self, *args):
        if not self.app.book or self.app.current_spine_index < 0:
            self.prev_btn.set_sensitive(False)
            self.next_btn.set_sensitive(False)
            return

        is_first_page = False
        is_last_page = False
        
        if self.app.columns == 1 and self.app.use_fixed_columns:
            v_adj = self.scrolled_window.get_vadjustment()
            is_first_page = v_adj.get_value() <= 1
            is_last_page = v_adj.get_value() >= v_adj.get_upper() - v_adj.get_page_size() - 1 
        else:
            h_adj = self.scrolled_window.get_hadjustment()
            is_first_page = h_adj.get_value() <= 1
            is_last_page = h_adj.get_value() >= h_adj.get_upper() - h_adj.get_page_size() - 1

        spine_length = len(self.app.book.spine)
        
        can_go_prev = not is_first_page or self.app.current_spine_index > 0
        self.prev_btn.set_sensitive(can_go_prev)
        
        can_go_next = not is_last_page or self.app.current_spine_index < spine_length - 1
        self.next_btn.set_sensitive(can_go_next)
        
        return True

    def do_close_request(self):
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        return False


if __name__ == "__main__":
    app = EPUBViewer()
    app.run(sys.argv)
