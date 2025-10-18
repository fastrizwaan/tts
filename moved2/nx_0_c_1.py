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
        # Track the index of the currently displayed content file (based on spine)
        self.current_spine_index: int = -1

        self.font_family = "Serif"
        self.font_size = 16
        self.margin = 30  # Increased margin for better look
        self.columns = 1
        self.column_width = 400  # Increased default column width

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

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.connect("clicked", lambda *_: self.scroll_viewport(-1))
        self.prev_btn.set_sensitive(False) # Initial state
        header.pack_start(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.connect("clicked", lambda *_: self.scroll_viewport(1))
        self.next_btn.set_sensitive(False) # Initial state
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
        margin_adj = Gtk.Adjustment(value=self.app.margin, lower=0, upper=100, step_increment=5)
        self.margin_spin = Gtk.SpinButton(adjustment=margin_adj, numeric=True)
        self.margin_spin.connect("value-changed", self.on_margin_changed)
        grid.attach(margin_label, 0, 2, 1, 1)
        grid.attach(self.margin_spin, 1, 2, 1, 1)

        # Columns
        col_label = Gtk.Label(label="Columns:", halign=Gtk.Align.START)
        col_adj = Gtk.Adjustment(value=self.app.columns, lower=1, upper=5, step_increment=1)
        self.col_spin = Gtk.SpinButton(adjustment=col_adj, numeric=True)
        self.col_spin.connect("value-changed", self.on_columns_changed)
        grid.attach(col_label, 0, 3, 1, 1)
        grid.attach(self.col_spin, 1, 3, 1, 1)

        # Column width
        cw_label = Gtk.Label(label="Col Width:", halign=Gtk.Align.START)
        cw_adj = Gtk.Adjustment(value=self.app.column_width, lower=100, upper=600, step_increment=10)
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
        self.webview.set_hexpand(True) # Make WebView expand horizontally
        self.webview.set_vexpand(True) # Make WebView expand vertically
        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_child(self.webview)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)
        
        # Connect to adjustment signals to manage next/prev buttons
        self.scrolled_window.get_hadjustment().connect("value-changed", self.update_nav_buttons)
        self.scrolled_window.get_vadjustment().connect("value-changed", self.update_nav_buttons)
        
        self.content_box.append(self.scrolled_window)

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB files")
        epub_filter.add_pattern("*.epub")
        
        # FIX: Correctly populate Gio.ListStore
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
            
            # Find the first content item to load
            first_href = None
            if self.app.book.spine:
                # Use the first item in the spine
                first_item_id = self.app.book.spine[0][0]
                first_item = self.app.book.get_item_with_id(first_item_id)
                if first_item:
                    first_href = first_item.get_name()
                    
            if first_href:
                self.load_href(first_href)
            elif self.app.toc:
                 # Fallback to first TOC entry
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
            elif isinstance(item, tuple): # Handle nested TOC in epublib
                result.extend(self.extract_toc(item[1], base))
            elif isinstance(item, list):
                result.extend(self.extract_toc(item, base))
        return result

    def populate_toc(self):
        # Gtk.ListBox has remove_all() in GTK4
        self.toc_list.remove_all()
        for title, href in self.app.toc:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=title, xalign=0, margin_start=10, ellipsize=2)
            row.set_child(label)
            row.href = href
            self.toc_list.append(row)

    def get_spine_index(self, href: str) -> int:
        """Finds the spine index for a given href."""
        if not self.app.book: return -1

        # Clean href: remove fragment and './' prefix
        clean_href = href.split('#')[0].lstrip('./')

        for i, (item_id, _) in enumerate(self.app.book.spine):
            item = self.app.book.get_item_with_id(item_id)
            if item and item.get_name() == clean_href:
                return i
        return -1


    def load_href(self, href: str):
        if not self.app.book:
            return

        # Handle fragment links (e.g., chapter.html#section)
        clean_href = href.split('#')[0]
        
        # Update current spine index
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
        
        # Extract files to a temporary directory
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        self.app.temp_dir = tempfile.mkdtemp()

        for it in self.app.book.get_items():
            try:
                # Use os.path.join for robust path creation
                dest = os.path.join(self.app.temp_dir, it.get_name())
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    # Fix: Use it.content instead of it.get_content() for latest epublib
                    f.write(it.content) 
            except Exception as e:
                print(f"Error saving file {it.get_name()}: {e}", file=sys.stderr)


        full_path = os.path.join(self.app.temp_dir, item.get_name())
        uri = f"file://{full_path}"
        
        # Append fragment identifier if it exists
        fragment = href.split('#')[1] if '#' in href else ''
        if fragment:
            uri += f"#{fragment}"

        self.webview.load_uri(uri)

    def on_toc_row_activated(self, listbox, row):
        if hasattr(row, 'href'):
            self.load_href(row.href)
            self.split_view.set_show_sidebar(False) # Hide sidebar on selection

    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            self.apply_layout()
            # Reset scroll position to 0 on new page load
            if self.scrolled_window.get_hadjustment().get_value() != 0:
                self.scrolled_window.get_hadjustment().set_value(0)
            if self.scrolled_window.get_vadjustment().get_value() != 0:
                 self.scrolled_window.get_vadjustment().set_value(0)
            self.update_nav_buttons()


    def apply_layout(self):
        margin = self.app.margin
        font_family = self.app.font_family
        font_size = self.app.font_size
        columns = self.app.columns
        col_width = self.app.column_width
        
        # Build robust CSS with !important
        css = f"""
            /* Reset all elements to ensure our styles dominate */
            * {{
                margin: 0 !important;
                padding: 0 !important;
                border: none !important;
                font-family: "{font_family}", serif !important;
                font-size: {font_size}px !important;
                line-height: 1.5 !important;
                max-width: none !important; /* Allow content to stretch */
            }}
            /* Container for column layout */
            html, body {{
                width: 100%;
                height: 100%;
                margin: 0 !important;
                padding: {margin}px !important;
                box-sizing: border-box; /* Include padding in width/height */
                display: block !important;
                position: relative !important;
                overflow-x: {'hidden' if columns == 1 else 'visible'} !important;
                overflow-y: {'auto' if columns == 1 else 'hidden'} !important;
                
                /* Column settings */
                column-count: {'auto' if columns > 1 else 1} !important;
                column-width: {'auto' if columns == 1 else f'{col_width}px'} !important;
                column-gap: {margin}px !important; /* ✅ FIX: Use margin for column gap */
                column-fill: auto !important;
            }}
            
            body {{
                min-height: 100%;
                /* The max-content trick helps WebKit calculate the total scrollable width */
                width: {'100%' if columns == 1 else 'max-content'} !important;
                
                /* Ensure content inside body respects column layout */
                display: block !important;
            }}
            
            /* Break avoidance: Prevent things from spanning columns */
            h1, h2, h3, h4, p, img, blockquote {{
                -webkit-column-break-inside: avoid !important;
                page-break-inside: avoid !important;
            }}
        """

        css_escaped = css.replace("\\", "\\\\").replace("`", "\\`")

        # JS to inject CSS
        js_inject = f"""
        (function() {{
            let old = document.getElementById('epub-viewer-style');
            if (old) old.remove();
            let style = document.createElement('style');
            style.id = 'epub-viewer-style';
            style.textContent = `{css_escaped}`;
            document.documentElement.appendChild(style);
            
            // Re-run for iframes if needed, but the main goal is the main document.
        }})();
        """

        # Correct WebKitGTK 6 call signature
        try:
            self.webview.evaluate_javascript(js_inject, -1, None, None, None)
        except Exception:
            self.webview.evaluate_javascript(js_inject, -1, None, None, None, None)

        # Update scroll policy
        # If columns > 1, scroll horizontally, otherwise vertically.
        if columns == 1:
            self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        else:
            self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            
        self.update_nav_buttons()


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
            # Vertical scroll (single column)
            page = v_adj.get_page_size()
            new_val = v_adj.get_value() + direction * page
            
            # Check if we should move to the next/previous spine item
            if direction == -1 and new_val < 0:
                self.load_prev_spine_item()
                return
            elif direction == 1 and new_val >= v_adj.get_upper() - page:
                self.load_next_spine_item()
                return
            
            # Clamp new_val and set
            new_val = max(0, min(new_val, v_adj.get_upper() - page))
            v_adj.set_value(new_val)
        else:
            # Horizontal scroll (multi-column)
            page_size = h_adj.get_page_size() # This is the viewport width
            new_val = h_adj.get_value() + direction * page_size
            
            # Check if we should move to the next/previous spine item
            if direction == -1 and new_val < 0:
                self.load_prev_spine_item()
                return
            elif direction == 1 and new_val >= h_adj.get_upper() - page_size:
                self.load_next_spine_item()
                return
            
            # Clamp new_val and set
            new_val = max(0, min(new_val, h_adj.get_upper() - page_size))
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
                # After loading, scroll to the end of the previous page
                GLib.idle_add(self.scroll_to_end_of_page)

    def scroll_to_end_of_page(self):
        """Scrolls the viewport to the end of the current content file."""
        h_adj = self.scrolled_window.get_hadjustment()
        v_adj = self.scrolled_window.get_vadjustment()
        page_size = h_adj.get_page_size() if self.app.columns > 1 else v_adj.get_page_size()
        
        if self.app.columns == 1:
            v_adj.set_value(v_adj.get_upper() - page_size)
        else:
            h_adj.set_value(h_adj.get_upper() - page_size)
        
        self.update_nav_buttons()
        return GLib.SOURCE_REMOVE # Remove the idle handler

    def update_nav_buttons(self, *args):
        """Updates the sensitivity of the previous/next buttons based on scroll position and spine index."""
        if not self.app.book or self.app.current_spine_index < 0:
            self.prev_btn.set_sensitive(False)
            self.next_btn.set_sensitive(False)
            return

        is_first_page = False
        is_last_page = False
        
        if self.app.columns == 1:
            v_adj = self.scrolled_window.get_vadjustment()
            is_first_page = v_adj.get_value() <= 1 # near zero
            # Check if within one page size of the total scroll range
            is_last_page = v_adj.get_value() >= v_adj.get_upper() - v_adj.get_page_size() - 1 
        else:
            h_adj = self.scrolled_window.get_hadjustment()
            is_first_page = h_adj.get_value() <= 1 # near zero
            # Check if within one page size of the total scroll range
            is_last_page = h_adj.get_value() >= h_adj.get_upper() - h_adj.get_page_size() - 1

        spine_length = len(self.app.book.spine)
        
        # Previous button logic
        can_go_prev = not is_first_page or self.app.current_spine_index > 0
        self.prev_btn.set_sensitive(can_go_prev)
        
        # Next button logic
        can_go_next = not is_last_page or self.app.current_spine_index < spine_length - 1
        self.next_btn.set_sensitive(can_go_next)


    def do_close_request(self):
        if self.app.temp_dir:
            shutil.rmtree(self.app.temp_dir, ignore_errors=True)
        return False


if __name__ == "__main__":
    app = EPUBViewer()
    app.run(sys.argv)
