import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, GLib
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import tempfile
import urllib.parse

os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(800, 600)
        self.set_title("EPUB Viewer")
        self.book = None
        self.current_item = None
        self.temp_dir = None
        self.css_content = ""
        self.item_map = {} # Maps internal epub paths to ebooklib Item objects
        self.item_paths = set() # Stores all possible internal paths for quick lookup
        self.items = [] # Stores the ordered list of document items
        self.current_index = 0
        self.WebKit = None
        self.webview = None
        self.textview = None
        self.setup_ui()

    def setup_ui(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)
        self.header_bar = Adw.HeaderBar()
        self.main_box.append(self.header_bar)

        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.set_tooltip_text("Open EPUB")
        self.open_btn.connect("clicked", self.open_file)
        self.header_bar.pack_start(self.open_btn)

        self.prev_btn = Gtk.Button(label="Previous")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)
        self.header_bar.pack_start(self.prev_btn)

        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)
        self.header_bar.pack_end(self.next_btn)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_valign(Gtk.Align.CENTER)
        self.header_bar.set_title_widget(self.progress)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.main_box.append(self.scrolled)

        try:
            gi.require_version('WebKit', '6.0')
            from gi.repository import WebKit
            self.WebKit = WebKit
            self.webview = WebKit.WebView()
            self.scrolled.set_child(self.webview)
            self.webview.connect("decide-policy", self.on_decide_policy)
        except ValueError:
            self.textview = Gtk.TextView()
            self.textview.set_editable(False)
            self.textview.set_cursor_visible(False)
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
            self.scrolled.set_child(self.textview)
            self.webview = None
            self.WebKit = None

    def on_decide_policy(self, webview, decision, decision_type):
        if self.WebKit and decision_type == self.WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            try:
                uri = decision.get_request().get_uri()
            except Exception:
                return False
            if uri and uri.startswith("file://"):
                print(f"[DEBUG] WebView clicked URI: {uri}") # Debug print
                if self.handle_internal_link(uri):
                    print(f"[DEBUG] Handled internal link: {uri}") # Debug print
                    try:
                        decision.ignore()
                    except Exception as e:
                        print(f"[DEBUG] Error ignoring decision: {e}") # Debug print
                    return True
                else:
                    print(f"[DEBUG] Could not handle internal link: {uri}") # Debug print
        return False

    def handle_internal_link(self, uri):
        # Map clicked file:// URI back to an epub internal item and display it.
        path = uri.replace("file://", "")
        # Split path and fragment
        path_parts = path.split('#', 1)
        base_path = path_parts[0]
        fragment = path_parts[1] if len(path_parts) > 1 else None

        print(f"[DEBUG] handle_internal_link - URI: {uri}, Base Path: {base_path}, Fragment: {fragment}") # Debug print

        # Determine relative path from temp_dir
        if self.temp_dir and base_path.startswith(self.temp_dir):
            rel_path = os.path.relpath(base_path, self.temp_dir).replace(os.sep, '/')
        else:
            rel_path = base_path.replace(os.sep, '/')
            print(f"[DEBUG] Base path {base_path} is not within temp_dir {self.temp_dir}, using as rel_path") # Debug print
            return False # If it's not in our temp dir, it's not an internal link we handle

        print(f"[DEBUG] Calculated relative path: {rel_path}") # Debug print

        # Find the corresponding epub item name using the relative path
        target_item_name = self._find_item_name_for_path(rel_path)
        if target_item_name:
            print(f"[DEBUG] Found target item name: {target_item_name}") # Debug print
            # Find the index of the target item in the ordered list
            for i, item in enumerate(self.items):
                if item.get_name() == target_item_name:
                    self.current_index = i
                    self.update_navigation()
                    self.display_page()
                    # If there was a fragment, scroll to it after the page loads
                    if self.webview and fragment:
                        print(f"[DEBUG] Scrolling to fragment: #{fragment}") # Debug print
                        # Small delay to allow page to render before attempting scroll
                        GLib.timeout_add(80, lambda: self._scroll_to_fragment(fragment))
                    return True
        else:
            print(f"[DEBUG] Could not find item name for path: {rel_path}") # Debug print
        return False

    def _scroll_to_fragment(self, fragment):
        """Helper to scroll to an anchor using JavaScript."""
        # Ensure fragment doesn't start with '#'
        frag_clean = fragment.lstrip('#')
        js_code = f"""
        console.log('Scrolling to fragment: {frag_clean}');
        var element = document.getElementById('{frag_clean}');
        if (element) {{
            console.log('Found element by ID:', element);
            element.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }} else {{
            // Fallback: try to find an anchor tag with name attribute
            element = document.querySelector('a[name="{frag_clean}"]');
            if (element) {{
                console.log('Found element by name:', element);
                element.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }} else {{
                 console.log('Element with ID or name {frag_clean} not found.');
            }}
        }}
        """
        try:
            self.webview.run_javascript(js_code, None, None, None)
        except Exception as e:
            print(f"Error running JS for fragment #{fragment}: {e}")


    def _find_item_name_for_path(self, rel_path):
        """Find the internal epub item name based on the relative path from temp_dir."""
        print(f"[DEBUG] _find_item_name_for_path called with: {rel_path}") # Debug print
        # Direct match of the relative path against item names
        if rel_path in self.item_paths:
            print(f"[DEBUG] Direct match found for: {rel_path}") # Debug print
            return rel_path

        # Match basename of the path against basenames of item names
        basename = os.path.basename(rel_path)
        print(f"[DEBUG] Looking for basename match for: {basename}") # Debug print
        for item_name in self.item_paths:
            if os.path.basename(item_name) == basename:
                print(f"[DEBUG] Basename match found, item name: {item_name}") # Debug print
                return item_name
        print(f"[DEBUG] No match found for: {rel_path}") # Debug print
        return None


    def open_file(self, button):
        dialog = Gtk.FileDialog()
        filter_list = Gio.ListStore.new(Gtk.FileFilter)

        epub_filter = Gtk.FileFilter()
        epub_filter.add_pattern("*.epub")
        epub_filter.add_pattern("*.EPUB")
        epub_filter.set_name("EPUB Files")
        filter_list.append(epub_filter)

        all_filter = Gtk.FileFilter()
        all_filter.add_pattern("*")
        all_filter.set_name("All Files")
        filter_list.append(all_filter)

        dialog.set_filters(filter_list)
        dialog.open(self, None, self.on_file_opened)

    def on_file_opened(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            self.load_epub(path)
        except GLib.Error:
            pass

    def load_epub(self, path):
        try:
            self.cleanup()
            self.book = epub.read_epub(path)
            self.items = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            if not self.items:
                self.show_error("No documents found in EPUB")
                return

            self.temp_dir = tempfile.mkdtemp()
            print(f"[DEBUG] Extracted EPUB to temp dir: {self.temp_dir}") # Debug print
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue
                full_path = os.path.join(self.temp_dir, item_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'wb') as f:
                    f.write(item.get_content())

            # Build item_map and item_paths for robust lookup
            self.item_map = {}
            self.item_paths = set()
            for item in self.items: # Only map document items
                name = item.get_name()
                if name:
                    self.item_map[name] = item
                    self.item_paths.add(name)
                    # Also add basename as a potential lookup key
                    self.item_paths.add(os.path.basename(name))

            print(f"[DEBUG] Loaded {len(self.items)} items. Item paths: {sorted(self.item_paths)}") # Debug print

            self.extract_css()
            self.current_index = 0
            self.update_navigation()
            self.display_page()
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
        self.temp_dir = None
        self.book = None
        self.items = []
        self.css_content = ""
        self.item_map = {}
        self.item_paths = set()
        self.current_item = None
        self.current_index = 0
        self.update_navigation()

    def extract_css(self):
        self.css_content = ""
        for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
            try:
                self.css_content += item.get_content().decode('utf-8') + "\n"
            except Exception:
                pass

    def display_page(self):
        if not self.book or not self.items:
            return
        item = self.items[self.current_index]
        self.current_item = item
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        item_dir = os.path.dirname(item.get_name())

        print(f"[DEBUG] Displaying page: {item.get_name()}, Current directory context: {item_dir}") # Debug print

        # Fix resource URLs (images, CSS, scripts)
        for tag in soup.find_all(['img', 'link', 'script', 'source']):
            src = None
            attr = None
            if tag.name in ('img', 'source'):
                src = tag.get('src') or tag.get('xlink:href')
                attr = 'src'
            elif tag.name == 'link':
                if tag.get('rel') and 'stylesheet' in tag.get('rel'):
                    src = tag.get('href')
                    attr = 'href'
            elif tag.name == 'script':
                src = tag.get('src')
                attr = 'src'
            if not src:
                continue
            if src.startswith(('http://', 'https://', 'data:', 'file://', 'mailto:')):
                continue
            normalized = os.path.normpath(os.path.join(item_dir, src)).replace(os.sep, '/')
            extracted_path = os.path.join(self.temp_dir, normalized)
            if os.path.exists(extracted_path):
                tag[attr] = f"file://{extracted_path}"
            else:
                alt = os.path.join(self.temp_dir, os.path.basename(src))
                if os.path.exists(alt):
                    tag[attr] = f"file://{alt}"

        # Handle internal links (like TOC links)
        for link in soup.find_all('a', href=True):
            original_href = link['href']
            if original_href.startswith(('http://', 'https://', 'mailto:')):
                continue
            if original_href.startswith('#'): # Anchor link within the same doc
                print(f"[DEBUG] Skipping internal anchor: {original_href}") # Debug print
                continue

            print(f"[DEBUG] Processing link: {original_href}") # Debug print

            # Determine the full internal epub path for the target
            # The href from the TOC is often relative to the root or already a root-relative path
            full_target_path = original_href
            if not original_href.startswith('/'):
                # If relative to current item's directory, resolve it
                # For TOC links in text00003.html, they are likely already root-relative like text00005.html#a1RY
                # So we might not need to join with item_dir here.
                # Let's try using the original href directly first.
                full_target_path = original_href # Assume it's already root-relative

            # Check if the target path exists in our epub items
            target_item_name = self._find_item_name_for_path(full_target_path)
            if target_item_name:
                # Find the extracted file path in the temp directory
                extracted_target_path = os.path.join(self.temp_dir, target_item_name)
                if os.path.exists(extracted_target_path):
                    # Construct the file:// URI, preserving the fragment
                    final_uri = f"file://{extracted_target_path}"
                    # Append fragment if it exists in the original href
                    if '#' in original_href:
                        final_uri += f"#{original_href.split('#', 1)[1]}"
                    print(f"[DEBUG] Converted {original_href} -> {final_uri}") # Debug print
                    link['href'] = final_uri
                else:
                    print(f"[DEBUG] Warning: Target file path does not exist in temp dir: {extracted_target_path}") # Debug print
            else:
                print(f"[DEBUG] Warning: Target item path not found in epub: {full_target_path}") # Debug print

        content = str(soup)
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <style>
        body {{ font-family: sans-serif; margin: 20px; line-height: 1.6; color: #333; }}
        img {{ max-width: 100%; height: auto; }}
        h1,h2,h3,h4,h5,h6 {{ color: #222; }}
        {self.css_content}
    </style>
</head>
<body>{content}</body>
</html>
"""
        if self.webview:
            base_uri = f"file://{os.path.join(self.temp_dir, os.path.dirname(item.get_name()))}/"
            try:
                print(f"[DEBUG] Loading HTML with base URI: {base_uri}") # Debug print
                self.webview.load_html(html_content, base_uri)
            except TypeError:
                try:
                    print(f"[DEBUG] Loading HTML without base URI") # Debug print
                    self.webview.load_html(html_content)
                except Exception as e:
                    print(f"[DEBUG] Error loading HTML: {e}") # Debug print
        else:
            buffer = self.textview.get_buffer()
            buffer.set_text(content)

        total_items = len(self.items) if self.items else 1
        progress = (self.current_index + 1) / total_items if total_items > 0 else 0
        self.progress.set_fraction(progress)
        self.progress.set_text(f"{self.current_index + 1}/{total_items}")

    def update_navigation(self):
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.items) - 1)

    def next_page(self, button):
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
            self.update_navigation()
            self.display_page()

    def prev_page(self, button):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_navigation()
            self.display_page()

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error", message)
        dialog.add_response("ok", "OK")
        dialog.present()

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.EpubViewer')
        self.create_action('quit', self.quit, ['<primary>q'])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPubViewer(self)
        win.present()

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

def main():
    app = Application()
    return app.run(None)

if __name__ == "__main__":
    main()
