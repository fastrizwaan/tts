#!/usr/bin/env python3
# Requires: pip install ebooklib
import os, json, tempfile, shutil, re, urllib.parse, signal, sys
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
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("EPUB Viewer")
        self.set_default_size(1200, 800)

        # EPUB state
        self.current_book = None
        self.chapters = []
        self.current_chapter = 0
        self.temp_dir = None

        # Columns
        self.column_count = 2
        self.base_column_width = 400
        self.column_gap = 40
        self.column_padding = 20
        self.actual_column_width = self.base_column_width

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
        columns_menu = Gio.Menu()
        for i in range(1, 11):
            columns_menu.append(f"{i} Column{'s' if i>1 else ''}", f"app.set-columns({i})")
        menu.append_submenu("Columns", columns_menu)
        menu_button.set_menu_model(menu)

        open_button = Gtk.Button()
        open_button.set_icon_name("document-open-symbolic")
        open_button.set_tooltip_text("Open EPUB")
        open_button.add_css_class("flat")
        open_button.connect("clicked", self.on_open_clicked)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav_box.set_spacing(6)

        self.prev_chapter_btn = Gtk.Button()
        self.prev_chapter_btn.set_icon_name("media-skip-backward-symbolic")
        self.prev_chapter_btn.set_tooltip_text("Previous Chapter")
        self.prev_chapter_btn.add_css_class("flat")
        self.prev_chapter_btn.connect("clicked", self.on_prev_chapter)
        self.prev_chapter_btn.set_sensitive(False)
        nav_box.append(self.prev_chapter_btn)

        self.prev_column_btn = Gtk.Button()
        self.prev_column_btn.set_icon_name("go-previous-symbolic")
        self.prev_column_btn.set_tooltip_text("Previous Column")
        self.prev_column_btn.add_css_class("flat")
        self.prev_column_btn.connect("clicked", self.on_prev_column)
        self.prev_column_btn.set_sensitive(False)
        nav_box.append(self.prev_column_btn)

        self.column_info = Gtk.Label()
        self.column_info.set_text("--/--")
        self.column_info.add_css_class("dim-label")
        self.column_info.set_margin_start(6)
        self.column_info.set_margin_end(6)
        nav_box.append(self.column_info)

        self.next_column_btn = Gtk.Button()
        self.next_column_btn.set_icon_name("go-next-symbolic")
        self.next_column_btn.set_tooltip_text("Next Column")
        self.next_column_btn.add_css_class("flat")
        self.next_column_btn.connect("clicked", self.on_next_column)
        self.next_column_btn.set_sensitive(False)
        nav_box.append(self.next_column_btn)

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

        # Ensure we get notified when web content finishes loading so adjustments can be read
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

        # Connect resize notifications to recalc columns
        self.connect("notify::default-width", self.on_window_resize)
        self.connect("notify::default-height", self.on_window_resize)

    def setup_navigation(self):
        # horizontal adjustment (may be updated later when content loads)
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        # keyboard
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)
        # scroll controller for snapping
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.HORIZONTAL)
        scroll_controller.connect("scroll", self.on_scroll_event)
        self.webview.add_controller(scroll_controller)
        self.snap_timeout_id = None

    def on_webview_load_changed(self, webview, load_event):
        # re-acquire adjustment and (re)connect handler
        if self.scrolled_window:
            self.h_adjustment = self.scrolled_window.get_hadjustment()
            if self.h_adjustment:
                try:
                    self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
                except Exception:
                    pass
                self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        # allow WebKit time to compute layout, then update UI
        GLib.timeout_add(150, self._after_load_update)

    def _after_load_update(self):
        self.calculate_column_dimensions()
        self.update_navigation()
        return False

    def on_scroll_position_changed(self, adjustment):
        self.update_column_info()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if not self.current_book:
            return False
        column_step = self.actual_column_width + self.column_gap
        if column_step <= 0:
            return False
        current_pos = 0
        if self.h_adjustment:
            current_pos = self.h_adjustment.get_value()
        new_pos = current_pos
        if keyval == 65361:  # Left
            new_pos = max(0, current_pos - column_step)
        elif keyval == 65363:  # Right
            max_pos = max(0, (self.h_adjustment.get_upper() - self.h_adjustment.get_page_size()) if self.h_adjustment else 0)
            new_pos = min(max_pos, current_pos + column_step)
        elif keyval == 65365:  # Page Up
            new_pos = max(0, current_pos - column_step)
        elif keyval == 65366:  # Page Down
            max_pos = max(0, (self.h_adjustment.get_upper() - self.h_adjustment.get_page_size()) if self.h_adjustment else 0)
            new_pos = min(max_pos, current_pos + column_step)
        elif keyval == 65360:  # Home
            new_pos = 0
        elif keyval == 65367:  # End
            new_pos = max(0, (self.h_adjustment.get_upper() - self.h_adjustment.get_page_size()) if self.h_adjustment else 0)
        else:
            return False
        if new_pos != current_pos:
            self.smooth_scroll_to(new_pos)
            return True
        return False

    def on_scroll_event(self, controller, dx, dy):
        if self.snap_timeout_id:
            try:
                GLib.source_remove(self.snap_timeout_id)
            except Exception:
                pass
        self.snap_timeout_id = GLib.timeout_add(200, self.snap_to_nearest_column)
        return False

    def snap_to_nearest_column(self):
        if not self.current_book or not self.h_adjustment:
            self.snap_timeout_id = None
            return False
        column_step = self.actual_column_width + self.column_gap
        if column_step <= 0:
            self.snap_timeout_id = None
            return False
        current_pos = self.h_adjustment.get_value()
        column_index = round(current_pos / column_step)
        target_pos = column_index * column_step
        max_pos = max(0, self.h_adjustment.get_upper() - self.h_adjustment.get_page_size())
        target_pos = max(0, min(target_pos, max_pos))
        if abs(current_pos - target_pos) > 5:
            self.smooth_scroll_to(target_pos)
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
                self.update_navigation()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def extract_chapters(self):
        self.chapters = []
        if not self.current_book:
            return
        spine_items = [item[0] for item in self.current_book.spine]
        # extract resources to temp dir first
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
        resources_dir = os.path.join(self.temp_dir, 'resources')
        css_styles = f"""
        <style>
        body {{
            margin: 0;
            padding: {self.column_padding}px;
            font-family: 'Cantarell', sans-serif;
            font-size: 16px;
            line-height: 1.6;
            background-color: #fafafa;
            color: #2e3436;
            column-count: {self.column_count};
            column-width: {self.actual_column_width}px;
            column-gap: {self.column_gap}px;
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
                    # prefer item.get_name() when available
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
        # re-acquire adjustment and connect handler
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        if self.h_adjustment:
            try:
                self.h_adjustment.disconnect_by_func(self.on_scroll_position_changed)
            except Exception:
                pass
            self.h_adjustment.connect("value-changed", self.on_scroll_position_changed)
        # refresh column buttons based on current adjustment
        self._refresh_buttons_based_on_adjustment()
        self.update_column_info()

    def _refresh_buttons_based_on_adjustment(self):
        """Enable/disable column buttons only when there's scrollable horizontal range."""
        if not self.h_adjustment:
            self.prev_column_btn.set_sensitive(False)
            self.next_column_btn.set_sensitive(False)
            return
        max_pos = max(0, self.h_adjustment.get_upper() - self.h_adjustment.get_page_size())
        if max_pos <= 5:
            self.prev_column_btn.set_sensitive(False)
            self.next_column_btn.set_sensitive(False)
        else:
            current_pos = self.h_adjustment.get_value()
            self.prev_column_btn.set_sensitive(current_pos > 0)
            self.next_column_btn.set_sensitive(current_pos < max_pos - 5)

    def on_prev_chapter(self, button):
        if self.current_chapter > 0:
            self.current_chapter -= 1
            self.load_chapter()
            self.update_navigation()

    def on_next_chapter(self, button):
        if self.current_chapter < len(self.chapters) - 1:
            self.current_chapter += 1
            self.load_chapter()
            self.update_navigation()

    def set_column_count(self, count):
        self.column_count = count
        if self.current_book:
            self.extract_chapters()
            self.load_chapter()

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

    def on_prev_column(self, button):
        if not self.current_book:
            return
        # refresh adjustment and dims
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        self.calculate_column_dimensions()
        column_step = self.actual_column_width + self.column_gap
        if not self.h_adjustment or column_step <= 0:
            return
        max_pos = max(0, self.h_adjustment.get_upper() - self.h_adjustment.get_page_size())
        if max_pos <= 5:
            return
        current_pos = self.h_adjustment.get_value()
        new_pos = max(0, current_pos - column_step)
        self.smooth_scroll_to(new_pos)
        GLib.timeout_add(200, self._refresh_buttons_based_on_adjustment)

    def on_next_column(self, button):
        if not self.current_book:
            return
        self.h_adjustment = self.scrolled_window.get_hadjustment()
        self.calculate_column_dimensions()
        column_step = self.actual_column_width + self.column_gap
        if not self.h_adjustment or column_step <= 0:
            return
        current_pos = self.h_adjustment.get_value()
        max_pos = max(0, self.h_adjustment.get_upper() - self.h_adjustment.get_page_size())
        if max_pos <= 5:
            return
        new_pos = min(max_pos, current_pos + column_step)
        self.smooth_scroll_to(new_pos)
        GLib.timeout_add(200, self._refresh_buttons_based_on_adjustment)

    def update_column_info(self):
        if not self.current_book or not self.h_adjustment:
            self.column_info.set_text("--/--")
            self.prev_column_btn.set_sensitive(False)
            self.next_column_btn.set_sensitive(False)
            return
        column_step = self.actual_column_width + self.column_gap
        current_pos = self.h_adjustment.get_value()
        max_pos = max(0, self.h_adjustment.get_upper() - self.h_adjustment.get_page_size())
        if column_step <= 0:
            self.column_info.set_text("1/1")
            return
        current_column = int(current_pos / column_step) + 1
        total_columns = int(max_pos / column_step) + 1
        self.column_info.set_text(f"{current_column}/{total_columns}")
        self.prev_column_btn.set_sensitive(current_pos > 0)
        self.next_column_btn.set_sensitive(current_pos < max_pos - 5)

    def calculate_column_dimensions(self):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            width = 1200
            height = 800
        available_width = width - (2 * self.column_padding)
        total_gap_width = (self.column_count - 1) * self.column_gap
        column_area_width = available_width - total_gap_width
        if self.column_count <= 0:
            self.actual_column_width = self.base_column_width
            return
        self.actual_column_width = max(250, column_area_width // self.column_count)
        total_needed_width = (self.actual_column_width * self.column_count) + total_gap_width + (2 * self.column_padding)
        if total_needed_width > width:
            self.actual_column_width = max(250, (width - total_gap_width - (2 * self.column_padding)) // self.column_count)

    def on_window_resize(self, *args):
        self.calculate_column_dimensions()
        if self.current_book and self.chapters:
            GLib.timeout_add(100, self._delayed_reload)

    def _delayed_reload(self):
        self.extract_chapters()
        self.load_chapter()
        return False

class EpubViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.epubviewer")

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            window = EpubViewer(self)
        # add actions for columns
        for i in range(1, 11):
            action = Gio.SimpleAction.new(f"set-columns", GLib.VariantType.new("i"))
            action.connect("activate", self.on_set_columns)
            self.add_action(action)
        window.present()

    def on_set_columns(self, action, parameter):
        count = parameter.get_int32()
        window = self.get_active_window()
        if window:
            window.set_column_count(count)

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

