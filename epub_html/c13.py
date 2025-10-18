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

        # Resize notifications
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)

    def setup_navigation(self):
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.HORIZONTAL)
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.webview.add_controller(scroll_controller)
        self.snap_timeout_id = None

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

    def on_scroll_position_changed(self, adjustment):
        self.update_page_info()
        self._refresh_buttons_based_on_adjustment()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book:
            return False
            
        self.calculate_column_dimensions()
        column_width = int(self.actual_column_width)
        column_gap = int(self.column_gap)
        
        # Use the same snapping logic for keyboard navigation
        if keyval in (65361, 65365):  # Left or PageUp -> step back
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                
                var columnsPerView = Math.floor(viewportWidth / stepSize);
                if (columnsPerView < 1) columnsPerView = 1;
                
                var currentColumn = Math.round(currentScroll / stepSize);
                var targetColumn = Math.max(0, currentColumn - columnsPerView);
                var newScroll = targetColumn * stepSize;
                
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
            
        elif keyval in (65363, 65366):  # Right or PageDown -> step forward
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                
                var columnsPerView = Math.floor(viewportWidth / stepSize);
                if (columnsPerView < 1) columnsPerView = 1;
                
                var currentColumn = Math.round(currentScroll / stepSize);
                var targetColumn = currentColumn + columnsPerView;
                var newScroll = Math.min(maxScroll, targetColumn * stepSize);
                
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
            
        elif keyval == 65360:  # Home
            js_code = "window.scrollTo({ left: 0, behavior: 'smooth' });"
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
            
        elif keyval == 65367:  # End
            js_code = """
            (function() {
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - (window.innerWidth || document.documentElement.clientWidth));
                window.scrollTo({ left: maxScroll, behavior: 'smooth' });
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
            return True
            
        return False

    def on_scroll_event(self, controller, dx, dy):
        if self.snap_timeout_id:
            try:
                GLib.source_remove(self.snap_timeout_id)
            except Exception:
                pass
        self.snap_timeout_id = GLib.timeout_add(200, self.snap_to_nearest_step)
        return False

    def snap_to_nearest_step(self):
        if not self.current_book:
            self.snap_timeout_id = None
            return False
            
        self.calculate_column_dimensions()
        column_width = int(self.actual_column_width)
        column_gap = int(self.column_gap)
        
        # Use JavaScript to snap to nearest column boundary
        js_code = f"""
        (function() {{
            var columnWidth = {column_width};
            var columnGap = {column_gap};
            var stepSize = columnWidth + columnGap;
            
            var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
            var nearestColumn = Math.round(currentScroll / stepSize);
            var targetScroll = nearestColumn * stepSize;
            
            var maxScroll = Math.max(0, document.documentElement.scrollWidth - (window.innerWidth || document.documentElement.clientWidth));
            targetScroll = Math.max(0, Math.min(targetScroll, maxScroll));
            
            if (Math.abs(currentScroll - targetScroll) > 5) {{
                window.scrollTo({{ left: targetScroll, behavior: 'smooth' }});
            }}
        }})();
        """
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
        self.snap_timeout_id = None
        return False

    def smooth_scroll_to(self, target_pos):
        if not self.h_adjustment:
            return False
        current_pos = self.h_adjustment.get_value()
        distance = target_pos - current_pos
        if abs(distance) < 1:
            self.h_adjustment.set_value(target_pos)
            return False
        steps = 20
        step_size = distance / steps
        step_count = 0
        def animation_frame():
            nonlocal step_count
            if step_count >= steps:
                self.h_adjustment.set_value(target_pos)
                return False
            new_pos = current_pos + (step_size * (step_count + 1))
            self.h_adjustment.set_value(new_pos)
            step_count += 1
            return True
        GLib.timeout_add(16, animation_frame)

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

    def process_chapter_content(self, content, item):
        self.calculate_column_dimensions()
        if self.column_mode == 'fixed':
            column_css = f"column-count: {self.fixed_column_count}; column-gap: {self.column_gap}px;"
        else:
            column_css = f"column-width: {self.actual_column_width}px; column-gap: {self.column_gap}px;"

        css_styles = f"""
        <style>
        html, body {{ height:100%; margin:0; padding:0; }}
        body {{
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
        }}
        h1,h2,h3,h4,h5,h6 {{ margin-top:1.5em; margin-bottom:0.5em; font-weight:bold; break-after:auto; break-inside:auto; }}
        p {{ margin:0 0 1em 0; text-align:justify; hyphens:auto; break-inside:auto; orphans:1; widows:1; }}
        img {{ max-width:100%; height:auto; margin:1em 0; }}
        blockquote {{ margin:1em 2em; font-style:italic; border-left:3px solid #3584e4; padding-left:1em; }}
        div, section, article, span, ul, ol, li {{ break-inside:auto; }}
        @media (prefers-color-scheme: dark) {{
            body {{ background-color:#242424; color:#ffffff; }}
            blockquote {{ border-left-color:#62a0ea; }}
        }}
        </style>
        """
        script = """
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            document.addEventListener('keydown', function(event) {
                if (['ArrowLeft','ArrowRight','PageUp','PageDown','Home','End'].includes(event.key)) {
                    event.preventDefault();
                    return false;
                }
            });
            document.body.tabIndex = -1;
            
            // Add scroll event listener to track position
            window.addEventListener('scroll', function() {
                var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                var scrollWidth = document.documentElement.scrollWidth;
                var clientWidth = document.documentElement.clientWidth;
                
                // Store scroll state for later queries
                window.epubScrollState = {
                    scrollLeft: scrollLeft,
                    scrollWidth: scrollWidth,
                    clientWidth: clientWidth,
                    maxScroll: Math.max(0, scrollWidth - clientWidth)
                };
            });
            
            // Initialize scroll state
            window.epubScrollState = {
                scrollLeft: 0,
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth,
                maxScroll: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)
            };
        });
        </script>
        """
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body_content = body_match.group(1) if body_match else content
        body_content = re.sub(r'</?(?:html|head|meta|title)[^>]*>', '', body_content, flags=re.IGNORECASE)
        body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL | re.IGNORECASE)

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
        
        # Get fresh adjustment reference
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            try:
                self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
            except Exception:
                pass
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        
        # Enable page buttons by default if we have content
        if self.current_book and self.chapters:
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)
        
        # Update based on actual scroll position
        GLib.timeout_add(100, self._delayed_navigation_update)

    def _delayed_navigation_update(self):
        self._refresh_buttons_based_on_adjustment()
        self.update_page_info()
        return False

    def _refresh_buttons_based_on_adjustment(self):
        if not self.h_adjustment or not self.current_book:
            self.prev_page_btn.set_sensitive(False)
            self.next_page_btn.set_sensitive(False)
            return
        
        # Get current scroll values
        current = self.h_adjustment.get_value()
        upper = self.h_adjustment.get_upper()
        page_size = self.h_adjustment.get_page_size()
        max_pos = max(0, upper - page_size)
        
        # Enable/disable based on scroll position
        # Allow some tolerance for floating point precision
        self.prev_page_btn.set_sensitive(current > 1.0)
        self.next_page_btn.set_sensitive(current < max_pos - 1.0)

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

    # Page navigation uses JavaScript to scroll within the WebView
    def on_prev_page(self, button):
        if not self.current_book:
            return
        
        self.calculate_column_dimensions()
        
        if self.column_mode == 'fixed':
            # Fixed column mode - use our calculated values
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                
                var columnsPerView = Math.floor(viewportWidth / stepSize);
                if (columnsPerView < 1) columnsPerView = 1;
                
                var currentColumn = Math.round(currentScroll / stepSize);
                var targetColumn = Math.max(0, currentColumn - columnsPerView);
                var newScroll = targetColumn * stepSize;
                
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        else:
            # Width-based mode - let CSS decide and use viewport-based scrolling
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            
            js_code = f"""
            (function() {{
                var desiredColumnWidth = {desired_width};
                var columnGap = {column_gap};
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                
                // Calculate how many columns actually fit based on CSS column-width
                var availableWidth = viewportWidth - (2 * 20); // account for padding
                var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                if (actualColumns < 1) actualColumns = 1;
                
                // Calculate actual column width that CSS is using
                var totalGapWidth = (actualColumns - 1) * columnGap;
                var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                var actualStepSize = actualColumnWidth + columnGap;
                
                // Find current position and move by viewport width
                var currentColumn = Math.round(currentScroll / actualStepSize);
                var targetColumn = Math.max(0, currentColumn - actualColumns);
                var newScroll = targetColumn * actualStepSize;
                
                console.log('Width mode prev: viewport=' + viewportWidth + ', actualCols=' + actualColumns + ', actualColWidth=' + actualColumnWidth + ', step=' + actualStepSize);
                console.log('Current col=' + currentColumn + ', target=' + targetColumn + ', newScroll=' + newScroll);
                
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)

    def on_next_page(self, button):
        if not self.current_book:
            return
            
        self.calculate_column_dimensions()
        
        if self.column_mode == 'fixed':
            # Fixed column mode - use our calculated values
            column_width = int(self.actual_column_width)
            column_gap = int(self.column_gap)
            
            js_code = f"""
            (function() {{
                var columnWidth = {column_width};
                var columnGap = {column_gap};
                var stepSize = columnWidth + columnGap;
                
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                
                var columnsPerView = Math.floor(viewportWidth / stepSize);
                if (columnsPerView < 1) columnsPerView = 1;
                
                var currentColumn = Math.round(currentScroll / stepSize);
                var targetColumn = currentColumn + columnsPerView;
                var newScroll = Math.min(maxScroll, targetColumn * stepSize);
                
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        else:
            # Width-based mode - let CSS decide and use viewport-based scrolling
            desired_width = int(self.desired_column_width)
            column_gap = int(self.column_gap)
            
            js_code = f"""
            (function() {{
                var desiredColumnWidth = {desired_width};
                var columnGap = {column_gap};
                var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                var currentScroll = window.pageXOffset || document.documentElement.scrollLeft;
                var maxScroll = Math.max(0, document.documentElement.scrollWidth - viewportWidth);
                
                // Calculate how many columns actually fit based on CSS column-width
                var availableWidth = viewportWidth - (2 * 20); // account for padding
                var actualColumns = Math.floor(availableWidth / (desiredColumnWidth + columnGap));
                if (actualColumns < 1) actualColumns = 1;
                
                // Calculate actual column width that CSS is using
                var totalGapWidth = (actualColumns - 1) * columnGap;
                var actualColumnWidth = (availableWidth - totalGapWidth) / actualColumns;
                var actualStepSize = actualColumnWidth + columnGap;
                
                // Find current position and move by viewport width
                var currentColumn = Math.round(currentScroll / actualStepSize);
                var targetColumn = currentColumn + actualColumns;
                var newScroll = Math.min(maxScroll, targetColumn * actualStepSize);
                
                console.log('Width mode next: viewport=' + viewportWidth + ', actualCols=' + actualColumns + ', actualColWidth=' + actualColumnWidth + ', step=' + actualStepSize);
                console.log('Current col=' + currentColumn + ', target=' + targetColumn + ', newScroll=' + newScroll);
                
                window.scrollTo({{ left: newScroll, behavior: 'smooth' }});
                setTimeout(function() {{ window.scrollTo({{ left: newScroll, behavior: 'auto' }}); }}, 400);
            }})();
            """
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_js_result, None)
    
    def _on_js_result(self, webview, result, user_data):
        # JavaScript execution completed
        GLib.timeout_add(100, self._update_page_buttons_from_js)

    def _update_page_buttons_from_js(self):
        # Query current scroll state via JavaScript
        js_code = """
        (function() {
            return {
                scrollLeft: window.pageXOffset || document.documentElement.scrollLeft,
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth
            };
        })();
        """
        
        self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_scroll_info_result, None)
        return False
    
    def _on_scroll_info_result(self, webview, result, user_data):
        try:
            # This is a more complex way to get the result, but should work
            self._query_and_update_scroll_state()
        except Exception as e:
            print(f"Error getting scroll info: {e}")
            # Fallback to enabling buttons
            if self.current_book:
                self.prev_page_btn.set_sensitive(True)
                self.next_page_btn.set_sensitive(True)

    def _query_and_update_scroll_state(self):
        # Simple approach - just check if we have content loaded
        if self.current_book and self.chapters:
            # Use JavaScript to update page info and button states
            js_code = """
            (function() {
                var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                var scrollWidth = document.documentElement.scrollWidth;
                var clientWidth = document.documentElement.clientWidth;
                var maxScroll = Math.max(0, scrollWidth - clientWidth);
                
                // Send message back with scroll state
                if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pageState) {
                    window.webkit.messageHandlers.pageState.postMessage({
                        canScrollLeft: scrollLeft > 1,
                        canScrollRight: scrollLeft < maxScroll - 1,
                        currentScroll: scrollLeft,
                        maxScroll: maxScroll
                    });
                }
                
                return {
                    scrollLeft: scrollLeft,
                    maxScroll: maxScroll,
                    canScrollLeft: scrollLeft > 1,
                    canScrollRight: scrollLeft < maxScroll - 1
                };
            })();
            """
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_page_state_result, None)

    def _on_page_state_result(self, webview, result, user_data):
        # For now, just enable/disable based on content being loaded
        # This is a fallback since getting JS results can be complex
        if self.current_book and self.chapters:
            # Enable both buttons and let the JavaScript handle the actual scrolling
            self.prev_page_btn.set_sensitive(True)
            self.next_page_btn.set_sensitive(True)
            
            # Update page info with a simple calculation
            self.calculate_column_dimensions()
            step = max(1, int(self.actual_column_width + self.column_gap))
            
            # Use a JavaScript query to get approximate page info
            js_code = f"""
            (function() {{
                var scrollWidth = document.documentElement.scrollWidth || document.body.scrollWidth;
                var clientWidth = document.documentElement.clientWidth || window.innerWidth;
                var scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                
                var totalWidth = Math.max(0, scrollWidth - clientWidth);
                var totalPages = totalWidth > 0 ? Math.ceil((totalWidth + {step}) / {step}) : 1;
                var currentPage = totalWidth > 0 ? Math.floor(scrollLeft / {step}) + 1 : 1;
                
                // Clamp values
                currentPage = Math.max(1, Math.min(currentPage, totalPages));
                
                return currentPage + '/' + totalPages;
            }})();
            """
            
            self.webview.evaluate_javascript(js_code, -1, None, None, None, self._on_page_info_result, None)

    def _on_page_info_result(self, webview, result, user_data):
        # Try to extract the result, but fall back to default if it fails
        try:
            # This is tricky to get the actual result from JavaScript in GTK4 WebKit
            # For now, we'll just show a basic page indicator
            if self.current_book:
                self.page_info.set_text("Page")  # Simplified for now
            else:
                self.page_info.set_text("--/--")
        except:
            if self.current_book:
                self.page_info.set_text("Page")
            else:
                self.page_info.set_text("--/--")

    def update_page_info(self):
        """Update page info and button states"""
        if not self.current_book:
            self.page_info.set_text("--/--")
            self.prev_page_btn.set_sensitive(False)
            self.next_page_btn.set_sensitive(False)
            return
        
        # Use JavaScript to get current page state
        self._query_and_update_scroll_state()
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
        return

    def on_window_resize(self, *args):
        self.calculate_column_dimensions()
        if self.current_book and self.chapters:
            GLib.timeout_add(100, self._delayed_reload)

    def _delayed_reload(self):
        self.extract_chapters()
        self.load_chapter()
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
