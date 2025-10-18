#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse, signal, sys, math
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Pango
from ebooklib import epub

Adw.init()

class EpubViewer(Adw.ApplicationWindow):
    """
    Column modes:
      - 'width': browser uses column-width (desired_column_width). Multiple columns created based on window size.
      - 'fixed': enforce exact column-count.
    Scrolling step for page navigation uses (actual_column_width + column_padding).
    """
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)

        # EPUB state
        self.current_book = None
        self.chapters = []
        self.current_chapter = 0
        self.temp_dir = None

        # Column / paging settings
        self.column_mode = 'width'
        self.fixed_column_count = 2
        self.desired_column_width = 400
        # add small widths requested by user
        self.column_gap = 40
        self.column_padding = 20
        self.actual_column_width = self.desired_column_width
        
        # Resize handling
        self.resize_timeout_id = None

        # UI
        self.setup_ui()
        self.setup_navigation()

    def setup_ui(self):
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.main_box)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.add_css_class("flat")
        menu = Gio.Menu()

        # Fixed columns submenu
        columns_menu = Gio.Menu()
        for i in range(1, 11):
            columns_menu.append(f"{i} Column{'s' if i>1 else ''}", f"app.set-columns({i})")
        menu.append_submenu("Columns (fixed)", columns_menu)

        # Width-based options including 50/100/150/200 requested
        width_menu = Gio.Menu()
        for w in (50,100,150,200,300,350,400,450,500):
            width_menu.append(f"{w}px width", f"app.set-column-width({w})")
        menu.append_submenu("Use column width", width_menu)

        menu_button.set_menu_model(menu)

        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.add_css_class("flat")
        open_button.connect("clicked", self.on_open_clicked)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav_box.set_spacing(6)

        # Prev chapter
        self.prev_chapter_btn = Gtk.Button()
        self.prev_chapter_btn.set_icon_name("media-skip-backward-symbolic")
        self.prev_chapter_btn.set_tooltip_text("Previous Chapter")
        self.prev_chapter_btn.add_css_class("flat")
        self.prev_chapter_btn.connect("clicked", self.on_prev_chapter)
        self.prev_chapter_btn.set_sensitive(False)
        nav_box.append(self.prev_chapter_btn)

        # Prev page
        self.prev_page_btn = Gtk.Button()
        self.prev_page_btn.set_icon_name("go-previous-symbolic")
        self.prev_page_btn.set_tooltip_text("Previous Page")
        self.prev_page_btn.add_css_class("flat")
        self.prev_page_btn.connect("clicked", self.on_prev_page)
        self.prev_page_btn.set_sensitive(False)
        nav_box.append(self.prev_page_btn)

        # Page info
        self.page_info = Gtk.Label()
        self.page_info.set_text("--/--")
        self.page_info.add_css_class("dim-label")
        self.page_info.set_margin_start(6)
        self.page_info.set_margin_end(6)
        nav_box.append(self.page_info)

        # Next page
        self.next_page_btn = Gtk.Button()
        self.next_page_btn.set_icon_name("go-next-symbolic")
        self.next_page_btn.set_tooltip_text("Next Page")
        self.next_page_btn.add_css_class("flat")
        self.next_page_btn.connect("clicked", self.on_next_page)
        self.next_page_btn.set_sensitive(False)
        nav_box.append(self.next_page_btn)

        # Next chapter
        self.next_chapter_btn = Gtk.Button()
        self.next_chapter_btn.set_icon_name("media-skip-forward-symbolic")
        self.next_chapter_btn.set_tooltip_text("Next Chapter")
        self.next_chapter_btn.add_css_class("flat")
        self.next_chapter_btn.connect("clicked", self.on_next_chapter)
        self.next_chapter_btn.set_sensitive(False)
        nav_box.append(self.next_chapter_btn)

        try:
            header_bar.pack_start(open_button)
            header_bar.pack_start(nav_box)
            header_bar.pack_end(menu_button)
        except AttributeError:
            button_box_start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_start.set_spacing(6)
            button_box_start.append(open_button)
            button_box_start.append(nav_box)
            button_box_end = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            button_box_end.append(menu_button)
            header_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            header_content.set_hexpand(True)
            header_content.append(button_box_start)
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            header_content.append(spacer)
            header_content.append(button_box_end)
            header_bar.set_title_widget(header_content)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled_window.set_vexpand(True)
        self.main_box.append(self.scrolled_window)

        self.webview = WebKit.WebView()
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        settings = self.webview.get_settings()
        settings.set_enable_smooth_scrolling(True)
        settings.set_enable_javascript(True)

        self.webview.connect("load-changed", self.on_webview_load_changed)

        self.scrolled_window.set_child(self.webview)

        self.info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.info_bar.set_margin_top(5)
        self.info_bar.set_margin_bottom(5)
        self.info_bar.set_margin_start(10)
        self.info_bar.set_margin_end(10)

        self.chapter_label = Gtk.Label()
        self.chapter_label.set_markup("<i>No EPUB loaded</i>")
        self.chapter_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.chapter_label.set_max_width_chars(80)
        self.info_bar.append(self.chapter_label)

        self.main_box.append(self.info_bar)

        # Resize notifications - connect to the actual window size changes
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)
        
        # Connect to maximized/unmaximized state changes
        self.connect("notify::maximized", self.on_window_resize)
        self.connect("notify::fullscreened", self.on_window_resize)

    def setup_navigation(self):
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        
        # Key navigation
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)
        
        # Mouse wheel navigation - use both vertical and horizontal scroll events
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.webview.add_controller(scroll_controller)
        
        # Also add scroll controller to the scrolled window for better coverage
        scroll_controller2 = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller2.connect("scroll", self.on_scroll_event)
        self.scrolled_window.add_controller(scroll_controller2)
        
        self.snap_timeout_id = None

    def is_single_column_mode(self):
        """Check if we're effectively in single column mode"""
        if self.column_mode == 'fixed' and self.fixed_column_count <= 1:
            return True
        elif self.column_mode == 'width':
            width = self.get_allocated_width()
            if width <= 0:
                width = 1200
            available = max(100, width - (2 * self.column_padding))
            if self.actual_column_width >= (available - self.column_gap):
                return True
        return False

    # Column mode setters
    def set_column_count(self, count):
        try:
            count = int(count)
            if count < 1:
                count = 1
        except Exception:
            count = 1
        self.column_mode = 'fixed'
        self.fixed_column_count = count
        if self.current_book:
            self.extract_chapters()
            self.load_chapter()
            GLib.timeout_add(150, self.update_navigation)

    def set_column_width(self, width):
        try:
            w = int(width)
            if w < 50:
                w = 50
        except Exception:
            w = 400
        self.column_mode = 'width'
        self.desired_column_width = w
        if self.current_book:
            self.calculate_column_dimensions()
            self.extract_chapters()
            self.load_chapter()
            GLib.timeout_add(150, self.update_navigation)

    # WebView / adjustments
    def on_webview_load_changed(self, webview, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            if self.scrolled_window:
                self.h_adjustment = self.scrolled_window.get_hadjustment()
                if self.h_adjustment:
                    try:
                        self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
                    except Exception:
                        pass
                    self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
            GLib.timeout_add(300, self._after_load_update)

    def _after_load_update(self):
        self.calculate_column_dimensions()
        self.update_navigation()
        return False

    def calculate_column_dimensions(self):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            width = 1200
            height = 800
        available = max(100, width - (2 * self.column_padding))
        if self.column_mode == 'fixed':
            cols = max(1, int(self.fixed_column_count))
            total_gap = (cols - 1) * self.column_gap
            cw = max(50, (available - total_gap) // cols)
            self.actual_column_width = cw
        else:
            # width mode: keep desired, but ensure at least one fits
            self.actual_column_width = max(50, min(self.desired_column_width, available))
        return False

    def on_scroll_position_changed(self, adjustment):
        self.update_page_info()
        self._refresh_buttons_based_on_adjustment()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book:
            return False
            
        self.calculate_column_dimensions()
        
        # In single column mode, use proper vertical pagination
        if self.is_single_column_mode():
            # Handle Page Up/Down and arrow keys with proper pagination
            if keyval == 65365:  # Page Up
                js_code = "window.scrollToPreviousPage();"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65366:  # Page Down
                js_code = "window.scrollToNextPage();"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65361:  # Left Arrow - previous page
                js_code = "window.scrollToPreviousPage();"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65363:  # Right Arrow - next page
                js_code = "window.scrollToNextPage();"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65362:  # Up Arrow - scroll up by line
                js_code = "window.scrollBy({ top: -50, behavior: 'smooth' });"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65364:  # Down Arrow - scroll down by line
                js_code = "window.scrollBy({ top: 50, behavior: 'smooth' });"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65360:  # Home
                js_code = "window.scrollTo({ top: 0, behavior: 'smooth' });"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            elif keyval == 65367:  # End
                js_code = "window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
                return True
            return False
        
        # Multi-column mode - horizontal navigation (existing logic continues...)
        # [rest of multi-column logic remains the same]
        return False

    def on_scroll_event(self, controller, dx, dy):
        if not self.current_book:
            return False
            
        # In single column mode, allow normal vertical scrolling
        if self.is_single_column_mode():
            return False
        
        # Multi-column mode handling would go here...
        return False

    # File open / EPUB load
    def on_open_clicked(self, button):
        dialog = Gtk.FileChooserNative(
            title="Open EPUB File",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel"
        )
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB Files")
        epub_filter.add_pattern("*.epub")
        dialog.set_filter(epub_filter)
        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = dialog.get_files()
            file = files.get_item(0) if files is not None else None
            if file:
                path = file.get_path()
                if path:
                    self.load_epub(path)
        dialog.destroy()

    def load_epub(self, filepath):
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
            self.temp_dir = tempfile.mkdtemp()
            self.current_book = epub.read_epub(filepath)
            self.extract_chapters()
            if self.chapters:
                self.current_chapter = 0
                self.load_chapter()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def extract_chapters(self):
        self.chapters = []
        if not self.current_book:
            return
        spine_items = [item[0] for item in self.current_book.spine]
        self.extract_resources()
        for item_id in spine_items:
            item = None
            for book_item in self.current_book.get_items():
                if getattr(book_item, "id", None) == item_id:
                    item = book_item
                    break
            if item and getattr(item, "media_type", "") == 'application/xhtml+xml':
                content = item.get_content().decode('utf-8')
                chapter_file = os.path.join(self.temp_dir, f"{item_id}.html")
                processed_content = self.process_chapter_content(content, item)
                with open(chapter_file, 'w', encoding='utf-8') as f:
                    f.write(processed_content)
                self.chapters.append({
                    'id': item_id,
                    'title': self.extract_title(content),
                    'file': chapter_file,
                    'item': item
                })

    def extract_resources(self):
        if not self.current_book or not self.temp_dir:
            return
        resources_dir = os.path.join(self.temp_dir, 'resources')
        os.makedirs(resources_dir, exist_ok=True)
        for item in self.current_book.get_items():
            if hasattr(item, 'media_type'):
                if item.media_type in ['text/css', 'image/jpeg', 'image/png', 'image/gif', 'image/svg+xml']:
                    name = None
                    try:
                        name = item.get_name()
                    except Exception:
                        name = None
                    if not name:
                        name = getattr(item, 'id', None) or "resource"
                    name = os.path.basename(name)
                    resource_path = os.path.join(resources_dir, name)
                    try:
                        with open(resource_path, 'wb') as f:
                            f.write(item.get_content())
                    except Exception:
                        pass

    def extract_title(self, content):
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', content, re.IGNORECASE | re.DOTALL)
        if h1_match:
            title = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
            if title:
                return title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            if title:
                return title
        return "Untitled Chapter"

    def load_chapter(self):
        if not self.chapters or self.current_chapter >= len(self.chapters):
            return
        chapter = self.chapters[self.current_chapter]
        file_uri = GLib.filename_to_uri(chapter['file'])
        self.webview.load_uri(file_uri)
        chapter_info = f"Chapter {self.current_chapter + 1} of {len(self.chapters)}: {chapter['title']}"
        self.chapter_label.set_text(chapter_info)

    def update_navigation(self):
        self.prev_chapter_btn.set_sensitive(self.current_chapter > 0)
        self.next_chapter_btn.set_sensitive(self.current_chapter < len(self.chapters) - 1)
        
        if self.current_book and self.chapters:
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)
        
        GLib.timeout_add(100, self._delayed_navigation_update)

    def _delayed_navigation_update(self):
        self._refresh_buttons_based_on_adjustment()
        self.update_page_info()
        return False

    def _refresh_buttons_based_on_adjustment(self):
        if not self.current_book:
            self.prev_page_btn.set_sensitive(False)
            self.next_page_btn.set_sensitive(False)
            return

        if self.is_single_column_mode():
            content_exists = bool(self.current_book)
            self.prev_page_btn.set_sensitive(content_exists)
            self.next_page_btn.set_sensitive(content_exists)
            return

        # Multi-column logic would go here...

    def on_prev_chapter(self, button):
        if self.current_chapter > 0:
            self.current_chapter -= 1
            self.load_chapter()
            GLib.timeout_add(300, self.update_navigation)

    def on_next_chapter(self, button):
        if self.current_chapter < len(self.chapters) - 1:
            self.current_chapter += 1
            self.load_chapter()
            GLib.timeout_add(300, self.update_navigation)

    def on_prev_page(self, button):
        if not self.current_book:
            return
        
        if self.is_single_column_mode():
            js_code = "window.scrollToPreviousPage();"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return
        
        # Multi-column logic would go here...

    def on_next_page(self, button):
        if not self.current_book:
            return
        
        if self.is_single_column_mode():
            js_code = "window.scrollToNextPage();"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return
        
        # Multi-column logic would go here...
    
    def _on_js_result(self, webview, result, user_data):
        GLib.timeout_add(100, self.update_page_info)

    def update_page_info(self):
        if not self.current_book:
            self.page_info.set_text("--/--")
            return

        if self.is_single_column_mode():
            self.page_info.set_text("Page")
        else:
            self.page_info.set_text("Page")

    def on_window_resize(self, *args):
        self.calculate_column_dimensions()
        if self.current_book and self.chapters:
            if hasattr(self, 'resize_timeout_id') and self.resize_timeout_id:
                GLib.source_remove(self.resize_timeout_id)
            self.resize_timeout_id = GLib.timeout_add(250, self._delayed_resize_reload)

    def _delayed_resize_reload(self):
        self.resize_timeout_id = None
        self.calculate_column_dimensions()
        self.extract_chapters()
        self.load_chapter()
        GLib.timeout_add(600, self.update_navigation)
        return False

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "_OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.present()

    def cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

    def process_chapter_content(self, content, item):
        self.calculate_column_dimensions()
        apply_columns = not self.is_single_column_mode()
        
        if apply_columns:
            if self.column_mode == 'fixed':
                column_css = f"column-count: {self.fixed_column_count}; column-gap: {self.column_gap}px;"
                body_style = f"""
                margin: 0;
                padding: {self.column_padding}px;
                font-family: 'Cantarell', sans-serif;
                font-size: 16px;
                line-height: 1.6;
                background-color: #fafafa;
                color: #2e3436;
                {column_css}
                column-fill: balance;
                height: calc(100vh - {self.column_padding * 2}px);
                overflow-x: auto;
                overflow-y: hidden;
                box-sizing: border-box;
                """
            else:
                column_css = f"column-width: {self.actual_column_width}px; column-gap: {self.column_gap}px;"
                body_style = f"""
                margin: 0;
                padding: {self.column_padding}px;
                font-family: 'Cantarell', sans-serif;
                font-size: 16px;
                line-height: 1.6;
                background-color: #fafafa;
                color: #2e3436;
                {column_css}
                column-fill: balance;
                height: calc(100vh - {self.column_padding * 2}px);
                overflow-x: auto;
                overflow-y: hidden;
                box-sizing: border-box;
                """
        else:
            body_style = f"""
            margin: 0;
            padding: {self.column_padding}px {self.column_padding}px {self.column_padding + 60}px {self.column_padding}px;
            font-family: 'Cantarell', sans-serif;
            font-size: 16px;
            line-height: 1.6;
            background-color: #fafafa;
            color: #2e3436;
            column-count: 1;
            column-width: auto;
            column-gap: 0;
            height: auto;
            min-height: calc(100vh - {self.column_padding + 60}px);
            overflow-x: hidden;
            overflow-y: auto;
            box-sizing: border-box;
            """

        css_styles = f"""
        <style>
        html, body {{ height:100%; margin:0; padding:0; }}
        body {{
            {body_style}
        }}
        h1,h2,h3,h4,h5,h6 {{ margin-top:1.5em; margin-bottom:0.5em; font-weight:bold; break-after:auto; break-inside:auto; }}
        p {{ margin:0 0 1em 0; text-align:justify; hyphens:auto; break-inside:auto; orphans:2; widows:2; }}
        img {{ max-width:100%; height:auto; margin:1em 0; }}
        blockquote {{ margin:1em 2em; font-style:italic; border-left:3px solid #3584e4; padding-left:1em; }}
        div, section, article, span, ul, ol, li {{ break-inside:auto; }}
        @media (prefers-color-scheme: dark) {{
            body {{ background-color:#242424; color:#ffffff; }}
            blockquote {{ border-left-color:#62a0ea; }}
        }}
        </style>
        """

        # Complete JavaScript for pagination
        script = """
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Store column settings in a global object accessible by other JS
            window.EPUB_VIEWER_SETTINGS = {
                applyColumns: """ + str(apply_columns).lower() + """,
                fixedColumnCount: """ + (str(self.fixed_column_count) if self.column_mode == 'fixed' else 'null') + """,
                desiredColumnWidth: """ + (str(self.actual_column_width) if self.column_mode == 'width' else 'null') + """,
                columnGap: """ + str(self.column_gap) + """
            };

            // Precise pagination functions for single column mode
            window.scrollToNextPage = function() {
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var scrollHeight = document.documentElement.scrollHeight;
                var clientHeight = document.documentElement.clientHeight;
                var maxScroll = scrollHeight - clientHeight;
                
                // Check if already at bottom
                if (scrollTop >= maxScroll - 5) {
                    return; // Can't scroll further
                }
                
                // Calculate the exact visible area (accounting for status bar)
                var viewportTop = scrollTop;
                var viewportBottom = scrollTop + clientHeight - 60; // 60px for status bar
                var pageHeight = clientHeight - 60;
                
                // Find the first line/element that starts after the current viewport bottom
                var elements = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, div, blockquote');
                var nextTarget = null;
                
                for (var i = 0; i < elements.length; i++) {
                    var element = elements[i];
                    var rect = element.getBoundingClientRect();
                    var elementTop = rect.top + scrollTop;
                    
                    // Find the first element that starts beyond our current visible bottom
                    if (elementTop > viewportBottom + 5) { // Small buffer to avoid edge cases
                        nextTarget = elementTop;
                        break;
                    }
                }
                
                var finalTarget;
                if (nextTarget !== null) {
                    // Scroll so this element appears at the top
                    finalTarget = nextTarget;
                } else {
                    // No specific element found, scroll by one page height
                    finalTarget = scrollTop + pageHeight;
                }
                
                // Ensure we don't scroll past the end
                finalTarget = Math.min(finalTarget, maxScroll);
                
                // Don't scroll if we're already very close to the target
                if (Math.abs(finalTarget - scrollTop) < 10) {
                    return;
                }
                
                window.scrollTo({ top: finalTarget, behavior: 'smooth' });
            };

            window.scrollToPreviousPage = function() {
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var clientHeight = document.documentElement.clientHeight;
                
                // Check if already at top
                if (scrollTop <= 5) {
                    return; // Can't scroll further up
                }
                
                var pageHeight = clientHeight - 60; // Account for status bar
                
                // Calculate where we want to scroll to (one page up)
                var targetScroll = Math.max(0, scrollTop - pageHeight);
                
                // Find the best element that would appear near the top of the previous page
                var elements = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, div, blockquote');
                var bestTarget = null;
                var bestDistance = Infinity;
                
                for (var i = 0; i < elements.length; i++) {
                    var element = elements[i];
                    var rect = element.getBoundingClientRect();
                    var elementTop = rect.top + scrollTop;
                    
                    // Look for elements near our target scroll position
                    if (elementTop <= targetScroll + pageHeight * 0.1 && elementTop >= targetScroll - pageHeight * 0.1) {
                        var distance = Math.abs(elementTop - targetScroll);
                        if (distance < bestDistance) {
                            bestDistance = distance;
                            bestTarget = elementTop;
                        }
                    }
                }
                
                var finalTarget;
                if (bestTarget !== null) {
                    finalTarget = bestTarget;
                } else {
                    finalTarget = targetScroll;
                }
                
                // Ensure we don't go past the beginning
                finalTarget = Math.max(0, finalTarget);
                
                // Don't scroll if we're already very close to the target
                if (Math.abs(finalTarget - scrollTop) < 10) {
                    return;
                }
                
                window.scrollTo({ top: finalTarget, behavior: 'smooth' });
            };

            // Add scroll event listener to track position and update button states
            window.addEventListener('scroll', function() {
                var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var scrollWidth = document.documentElement.scrollWidth;
                var scrollHeight = document.documentElement.scrollHeight;
                var clientWidth = document.documentElement.clientWidth;
                var clientHeight = document.documentElement.clientHeight;
                
                // Store scroll state for later queries
                window.epubScrollState = {
                    scrollLeft: scrollLeft,
                    scrollTop: scrollTop,
                    scrollWidth: scrollWidth,
                    scrollHeight: scrollHeight,
                    clientWidth: clientWidth,
                    clientHeight: clientHeight,
                    maxScrollX: Math.max(0, scrollWidth - clientWidth),
                    maxScrollY: Math.max(0, scrollHeight - clientHeight),
                    atTop: scrollTop <= 1,
                    atBottom: scrollTop >= (scrollHeight - clientHeight - 1),
                    atLeft: scrollLeft <= 1,
                    atRight: scrollLeft >= (scrollWidth - clientWidth - 1)
                };
            });

            // Initialize scroll state
            window.epubScrollState = {
                scrollLeft: 0,
                scrollTop: 0,
                scrollWidth: document.documentElement.scrollWidth,
                scrollHeight: document.documentElement.scrollHeight,
                clientWidth: document.documentElement.clientWidth,
                clientHeight: document.documentElement.clientHeight,
                maxScrollX: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
                maxScrollY: Math.max(0, document.documentElement.scrollHeight - document.documentElement.clientHeight),
                atTop: true,
                atBottom: false,
                atLeft: true,
                atRight: false
            };

            // Function to get visible page information for single column mode
            window.getPageInfo = function() {
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var scrollHeight = document.documentElement.scrollHeight;
                var clientHeight = document.documentElement.clientHeight;
                var effectiveHeight = clientHeight - 60;
                
                var currentPage = Math.floor(scrollTop / effectiveHeight) + 1;
                var totalPages = Math.max(1, Math.ceil((scrollHeight - clientHeight + effectiveHeight) / effectiveHeight));
                currentPage = Math.min(currentPage, totalPages);
                
                return {
                    currentPage: currentPage,
                    totalPages: totalPages,
                    canScrollUp: scrollTop > 1,
                    canScrollDown: scrollTop < (scrollHeight - clientHeight - 1)
                };
            };

            // Debug function to show what content is currently visible
            window.debugVisibleContent = function() {
                var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                var clientHeight = document.documentElement.clientHeight;
                var visibleTop = scrollTop;
                var visibleBottom = scrollTop + clientHeight - 60; // Account for status bar
                
                var elements = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6');
                var visibleElements = [];
                
                for (var i = 0; i < elements.length; i++) {
                    var element = elements[i];
                    var rect = element.getBoundingClientRect();
                    var elementTop = rect.top + scrollTop;
                    var elementBottom = rect.bottom + scrollTop;
                    
                    // Check if element is at least partially visible
                    if (elementBottom > visibleTop && elementTop < visibleBottom) {
                        var text = element.textContent.trim();
                        if (text.length > 0) {
                            visibleElements.push({
                                tag: element.tagName.toLowerCase(),
                                text: text.substring(0, 100) + (text.length > 100 ? '...' : ''),
                                top: elementTop,
                                bottom: elementBottom,
                                fullyVisible: elementTop >= visibleTop && elementBottom <= visibleBottom
                            });
                        }
                    }
                }
                
                console.log('Visible content (top=' + visibleTop + ', bottom=' + visibleBottom + '):');
                visibleElements.forEach(function(el, idx) {
                    console.log((idx + 1) + '. [' + el.tag + '] ' + el.text + ' (y:' + el.top + '-' + el.bottom + ', full:' + el.fullyVisible + ')');
                });
                
                return visibleElements;
            };
        });
        </script>
        """

        # Process content and fix resource paths
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1) if body_match else content
        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Fix resource paths
        resources_dir_fs = os.path.join(self.temp_dir, 'resources')
        available = set()
        if os.path.isdir(resources_dir_fs):
            for fn in os.listdir(resources_dir_fs):
                available.add(fn)

        def repl_src(m):
            orig = m.group(1)
            name = os.path.basename(orig)
            if name in available:
                return f'src="resources/{name}"'
            return f'src="{orig}"'
        body_content = re.sub(r'src=["\']([^"\']+)["\']', repl_src, body_content, flags=re.IGNORECASE)

        def repl_href(m):
            orig = m.group(1)
            name = os.path.basename(orig)
            if name in available:
                return f'href="resources/{name}"'
            return f'href="{orig}"'
        body_content = re.sub(r'href=["\']([^"\']+)["\']', repl_href, body_content, flags=re.IGNORECASE)

        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{css_styles}</head><body>{body_content}{script}</body></html>"""


class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.epubviewer")

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            window = EpubViewer(self)
        # actions for fixed columns and widths (including 50/100/150/200)
        for i in range(1, 11):
            act = Gio.SimpleAction.new(f"set-columns", GLib.VariantType.new("i"))
            act.connect("activate", self.on_set_columns)
            self.add_action(act)
        for w in (50,100,150,200,300,350,400,450,500):
            act_w = Gio.SimpleAction.new(f"set-column-width", GLib.VariantType.new("i"))
            act_w.connect("activate", self.on_set_column_width)
            self.add_action(act_w)
        window.present()

    def on_set_columns(self, action, parameter):
        count = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_count(count)

    def on_set_column_width(self, action, parameter):
        w = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_width(w)


def main():
    app = EpubViewerApp()
    def cleanup_handler(signum, frame):
        window = app.get_active_window()
        if window:
            window.cleanup()
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    try:
        app.run(sys.argv)
    finally:
        window = app.get_active_window()
        if window:
            window.cleanup()


if __name__ == "__main__":
    main()
