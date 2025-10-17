import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, Adw, Gio, GLib, WebKit
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import tempfile
import urllib.parse
import shutil
import pathlib
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
class EPubViewer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 700)
        self.set_title("EPUB Viewer")
        self.book = None
        self.current_item = None
        self.temp_dir = None
        self.css_content = ""
        self.item_map = {}
        self.current_index = 0
        self.items = []
        self._toc_handler_id = None
        self._load_changed_id = None
        self.setup_ui()

    def setup_ui(self):
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        self.header_bar = Adw.HeaderBar()
        main_box.append(self.header_bar)

        # Open button
        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.set_tooltip_text("Open EPUB")
        self.open_btn.connect("clicked", self.open_file)
        self.header_bar.pack_start(self.open_btn)

        # TOC toggle button
        self.toc_btn = Gtk.ToggleButton(icon_name="view-list-symbolic")
        self.toc_btn.set_tooltip_text("Toggle Table of Contents")
        self.toc_btn.connect("toggled", self.on_toc_toggle)
        self.header_bar.pack_start(self.toc_btn)

        # Navigation buttons
        self.prev_btn = Gtk.Button(label="Previous")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self.prev_page)
        self.header_bar.pack_start(self.prev_btn)

        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", self.next_page)
        self.header_bar.pack_end(self.next_btn)

        # Progress bar
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_valign(Gtk.Align.CENTER)
        self.header_bar.set_title_widget(self.progress)

        # Split view for TOC sidebar and content
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_position(Gtk.PackType.START)
        self.split_view.set_collapsed(True)
        main_box.append(self.split_view)

        # TOC Sidebar
        self.setup_toc_sidebar()

        # Content area (WebView)
        self.setup_content_area()

    def setup_toc_sidebar(self):
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.set_size_request(300, -1)

        # TOC header
        toc_header = Gtk.Label(label="Table of Contents")
        toc_header.add_css_class("title-2")
        toc_header.set_margin_top(12)
        toc_header.set_margin_bottom(12)
        sidebar_box.append(toc_header)

        # Scrolled window for TOC
        toc_scrolled = Gtk.ScrolledWindow()
        toc_scrolled.set_vexpand(True)
        toc_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_box.append(toc_scrolled)

        # TOC ListBox
        self.toc_listbox = Gtk.ListBox()
        self.toc_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.toc_listbox.add_css_class("navigation-sidebar")
        toc_scrolled.set_child(self.toc_listbox)

        self.split_view.set_sidebar(sidebar_box)

    def setup_content_area(self):
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Scrolled window for WebView
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        content_box.append(self.scrolled)

        # WebView
        self.webview = WebKit.WebView()
        self.scrolled.set_child(self.webview)
        self.webview.connect("decide-policy", self.on_decide_policy)

        self.split_view.set_content(content_box)

    def on_toc_toggle(self, button):
        is_active = button.get_active()
        self.split_view.set_show_sidebar(is_active)

    def on_decide_policy(self, webview, decision, decision_type):
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        
        try:
            uri = decision.get_request().get_uri()
        except Exception:
            return False
        
        if not uri:
            return False

        # Intercept internal file:// links
        if uri.startswith("file://") and self.temp_dir:
            local_path = uri[len("file://"):]
            try:
                local_path = urllib.parse.unquote(local_path)
            except Exception:
                pass
            
            # Extract fragment
            fragment = ''
            if '#' in local_path:
                local_path, fragment = local_path.split('#', 1)
                fragment = '#' + fragment
            
            if local_path.startswith(self.temp_dir):
                # Prevent default navigation
                try:
                    decision.ignore()
                except Exception:
                    pass
                # Handle as chapter navigation
                return self.handle_internal_link(uri)

        return False

    def handle_internal_link(self, uri):
        """Navigate between chapters like next/prev buttons"""
        frag = ''
        
        if uri.startswith("file://"):
            path = uri[len("file://"):]
            try:
                path = urllib.parse.unquote(path)
            except Exception:
                pass
            
            if '#' in path:
                path, frag = path.split('#', 1)
                frag = '#' + frag
            
            # Get relative path from temp_dir
            if self.temp_dir and path.startswith(self.temp_dir):
                internal = os.path.relpath(path, self.temp_dir).replace(os.sep, '/')
            else:
                return False
        else:
            return False

        # Find the matching item
        target_item = self.find_item_by_path(internal)
        
        if target_item:
            target_name = target_item.get_name()
            for i, it in enumerate(self.items):
                if it.get_name() == target_name:
                    self.current_index = i
                    self.update_navigation()
                    self.display_page(jump_fragment=frag)
                    return True
        
        return False

    def find_item_by_path(self, path):
        """Find an EPUB item by trying various path variations"""
        path = path.replace(os.sep, '/')
        
        # Try direct match
        if path in self.item_map:
            return self.item_map[path]
        
        # Try basename
        basename = os.path.basename(path)
        if basename in self.item_map:
            return self.item_map[basename]
        
        # Try unquoting
        try:
            unquoted = urllib.parse.unquote(path)
            if unquoted in self.item_map:
                return self.item_map[unquoted]
            
            unquoted_base = os.path.basename(unquoted)
            if unquoted_base in self.item_map:
                return self.item_map[unquoted_base]
        except Exception:
            pass
        
        # Try stripping
        stripped = path.lstrip('./').lstrip('/')
        if stripped in self.item_map:
            return self.item_map[stripped]
        
        # Search through all items
        for item in self.items:
            item_name = item.get_name()
            if item_name == path or os.path.basename(item_name) == basename:
                return item
            if item_name.endswith(path) or path.endswith(item_name):
                return item
        
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
            
            # Extract all content to temp directory
            # Extract all content to temp directory (sanitize names to avoid .. escapes)
            self.temp_dir = tempfile.mkdtemp()
            for item in self.book.get_items():
                item_path = item.get_name()
                if not item_path:
                    continue

                # normalize and disallow escaping outside temp_dir
                safe_name = item_path.replace('\\', '/')
                safe_name = os.path.normpath(safe_name).lstrip('/')

                # if normalization tries to escape, fall back to basename
                if '..' in safe_name.split(os.sep):
                    safe_name = os.path.basename(item_path)

                full_path = os.path.join(self.temp_dir, safe_name)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'wb') as f:
                    f.write(item.get_content())


            # Build item map
            self.build_item_map()
            self.extract_css()
            
            # Load TOC
            self.load_toc()
            
            # Display first page
            self.current_index = 0
            self.update_navigation()
            self.display_page()
            
        except Exception as e:
            self.show_error(f"Error loading EPUB: {str(e)}")

    def build_item_map(self):
        """Build comprehensive mapping of paths to items"""
        self.item_map = {}
        for item in self.items:
            name = item.get_name()
            if not name:
                continue
            
            name = name.replace(os.sep, '/')
            
            variations = [
                name,
                os.path.basename(name),
                name.lstrip('./').lstrip('/'),
                os.path.basename(name).lstrip('./').lstrip('/')
            ]
            
            try:
                decoded = urllib.parse.unquote(name)
                variations.extend([
                    decoded,
                    os.path.basename(decoded),
                    decoded.lstrip('./').lstrip('/'),
                    os.path.basename(decoded).lstrip('./').lstrip('/')
                ])
            except Exception:
                pass
            
            for var in variations:
                if var and var not in self.item_map:
                    self.item_map[var] = item

    def load_toc(self):
        """Extract and display table of contents"""
        try:
            toc = self.book.toc
            if not toc:
                print("[DEBUG] No TOC found in EPUB")
                return
            
            toc_data = self.parse_toc(toc)
            if toc_data:
                self.populate_toc(toc_data)
            else:
                print("[DEBUG] TOC is empty")
                
        except Exception as e:
            print(f"[DEBUG] Error loading TOC: {e}")

    def parse_toc(self, toc, level=0):
        """Parse EPUB TOC into a flat structure"""
        result = []
        
        for item in toc:
            if isinstance(item, tuple):
                # It's a section with subitems
                section = item[0]
                subitems = item[1]
                
                label = section.title if hasattr(section, 'title') else str(section)
                href = section.href if hasattr(section, 'href') else ''
                
                parsed_subitems = self.parse_toc(subitems, level + 1)
                
                result.append({
                    'label': label,
                    'href': href,
                    'subitems': parsed_subitems
                })
            else:
                # Simple item
                label = item.title if hasattr(item, 'title') else str(item)
                href = item.href if hasattr(item, 'href') else ''
                
                result.append({
                    'label': label,
                    'href': href,
                    'subitems': []
                })
        
        return result

    def clear_toc(self):
        """Clear the TOC sidebar"""
        try:
            child = self.toc_listbox.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                try:
                    self.toc_listbox.remove(child)
                except Exception:
                    try:
                        child.unparent()
                    except Exception:
                        pass
                child = next_child
        except Exception:
            pass

        try:
            if hasattr(self, '_toc_handler_id') and self._toc_handler_id:
                self.toc_listbox.disconnect(self._toc_handler_id)
                self._toc_handler_id = None
        except Exception:
            pass

    def populate_toc(self, toc_data, parent_box=None, level=0):
        """Recursively populate TOC with nested items"""
        if parent_box is None:
            print(f"[DEBUG] populate_toc called with {len(toc_data)} items")
            self.clear_toc()
            parent_box = self.toc_listbox

        for item in toc_data:
            label_text = item.get('label', '').strip()
            href = item.get('href', '')
            subitems = item.get('subitems', [])

            if not label_text:
                label_text = 'Unknown'

            if subitems and len(subitems) > 0:
                row = Gtk.ListBoxRow()
                row.set_activatable(bool(href))

                expander = Gtk.Expander()
                expander.set_label(label_text)
                expander.set_margin_start(12 + (level * 16))
                expander.set_margin_end(12)
                expander.set_margin_top(4)
                expander.set_margin_bottom(4)

                subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                expander.set_child(subitems_box)

                row.set_child(expander)

                if href:
                    row.href = href
                    expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))

                parent_box.append(row)

                for subitem in subitems:
                    self.add_toc_subitem(subitems_box, subitem, level + 1)

            else:
                row = Gtk.ListBoxRow()
                row.set_activatable(True)

                label = Gtk.Label(
                    label=label_text,
                    halign=Gtk.Align.START,
                    margin_top=6,
                    margin_bottom=6,
                    margin_start=12 + (level * 16),
                    margin_end=12,
                    wrap=True,
                    xalign=0
                )

                row.set_child(label)

                if href:
                    row.href = href

                parent_box.append(row)

        # Set up row-activated handler at top level
        if parent_box == self.toc_listbox:
            try:
                if hasattr(self, '_toc_handler_id') and self._toc_handler_id:
                    self.toc_listbox.disconnect(self._toc_handler_id)
                    self._toc_handler_id = None
            except:
                pass
            
            self._toc_handler_id = self.toc_listbox.connect('row-activated', self.on_toc_row_activated)
            print(f"[DEBUG] TOC populated with {len(toc_data)} items")

    def add_toc_subitem(self, parent_box, item, level):
        """Add a single TOC subitem"""
        label_text = item.get('label', '').strip()
        href = item.get('href', '')
        subitems = item.get('subitems', [])

        if not label_text:
            label_text = 'Unknown'

        if subitems and len(subitems) > 0:
            expander = Gtk.Expander()
            expander.set_label(label_text)
            expander.set_margin_start(level * 16)
            expander.set_margin_end(12)
            expander.set_margin_top(2)
            expander.set_margin_bottom(2)

            subitems_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            expander.set_child(subitems_box)

            if href:
                expander.connect('activate', lambda e, h=href: self.go_to_chapter(h))

            parent_box.append(expander)

            for subitem in subitems:
                self.add_toc_subitem(subitems_box, subitem, level + 1)
        else:
            button = Gtk.Button(label=label_text)
            button.set_has_frame(False)
            button.set_halign(Gtk.Align.START)
            button.set_margin_start(level * 16)
            button.set_margin_end(12)
            button.set_margin_top(2)
            button.set_margin_bottom(2)

            child = button.get_child()
            if child and isinstance(child, Gtk.Label):
                child.set_wrap(True)
                child.set_xalign(0)

            if href:
                button.connect('clicked', lambda b, h=href: self.go_to_chapter(h))

            parent_box.append(button)

    def on_toc_row_activated(self, listbox, row):
        """Handle TOC item click"""
        if hasattr(row, 'href') and row.href:
            print(f"[DEBUG] TOC navigation to: {row.href}")
            self.go_to_chapter(row.href)

    def go_to_chapter(self, href):
        """Navigate to a chapter by href"""
        print(f"[DEBUG] go_to_chapter called with: {href}")
        
        # Parse href to extract path and fragment
        fragment = ''
        if '#' in href:
            path, fragment = href.split('#', 1)
            fragment = '#' + fragment
        else:
            path = href
        
        # Find the item
        target_item = self.find_item_by_path(path)
        
        if target_item:
            target_name = target_item.get_name()
            for i, it in enumerate(self.items):
                if it.get_name() == target_name:
                    self.current_index = i
                    self.update_navigation()
                    self.display_page(jump_fragment=fragment)
                    print(f"[DEBUG] Navigated to chapter index {i}")
                    return
        
        print(f"[DEBUG] Could not find chapter for href: {href}")

    def cleanup(self):
        # stop any pending load and disconnect load-changed handler
        try:
            if hasattr(self, '_load_changed_id') and self._load_changed_id:
                try:
                    self.webview.disconnect(self._load_changed_id)
                except Exception:
                    pass
                self._load_changed_id = None
        except Exception:
            pass

        try:
            self.webview.stop_loading()
        except Exception:
            pass

        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

        self.temp_dir = None
        self.book = None
        self.items = []
        self.css_content = ""
        self.item_map = {}
        self.current_item = None
        self.current_index = 0
        self.clear_toc()
        self.update_navigation()

    def extract_css(self):
        self.css_content = ""
        for item in self.book.get_items_of_type(ebooklib.ITEM_STYLE):
            try:
                self.css_content += item.get_content().decode('utf-8') + "\n"
            except Exception:
                pass

    def resolve_resource_path(self, src, item_dir):
        """Resolve a resource path relative to the current item directory"""
        if src.startswith(('http://', 'https://', 'data:', 'file://', 'mailto:')):
            return None
        
        normalized = os.path.normpath(os.path.join(item_dir, src)).replace(os.sep, '/')
        extracted_path = os.path.join(self.temp_dir, normalized)
        
        if os.path.exists(extracted_path):
            return extracted_path
        
        basename_path = os.path.join(self.temp_dir, os.path.basename(src))
        if os.path.exists(basename_path):
            return basename_path
        
        return None

    def display_page(self, jump_fragment=''):
        if not self.book or not self.items:
            return

        item = self.items[self.current_index]
        self.current_item = item
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        item_dir = os.path.dirname(item.get_name())

        # Fix resource URLs
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
            resolved = self.resolve_resource_path(src, item_dir)
            if resolved:
                quoted = urllib.parse.quote(resolved, safe="/:\\")
                tag[attr] = f"file://{quoted}"

        # Normalize internal links to extracted files
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith(('http://', 'https://', 'mailto:')) or href.startswith('#'):
                continue
            parts = href.split('#', 1)
            target = parts[0]
            frag = f"#{parts[1]}" if len(parts) > 1 else ""
            if target:
                normalized = os.path.normpath(os.path.join(item_dir, target)).replace(os.sep, '/')
                extracted_path = os.path.join(self.temp_dir, normalized)
                if os.path.exists(extracted_path):
                    quoted = urllib.parse.quote(extracted_path, safe="/:\\")
                    link['href'] = f"file://{quoted}{frag}"

        content = str(soup)
        html_content = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width,initial-scale=1" />
        <style>
            body {{ font-family: sans-serif; margin: 60px; line-height: 1.6; color: #333; }}
            img {{ max-width: 100%; height: auto; }}
            h1,h2,h3,h4,h5,h6 {{ color: #222; }}
            {self.css_content}
        </style>
    </head>
    <body>{content}</body>
    </html>
    """

        # ensure previous load-changed handler is cleared
        if getattr(self, '_load_changed_id', None):
            try:
                self.webview.disconnect(self._load_changed_id)
            except Exception:
                pass
            self._load_changed_id = None

        # load HTML with proper base URI (ensure trailing slash)
        base_uri = pathlib.Path(os.path.join(self.temp_dir, item_dir) or self.temp_dir).as_uri() + '/'

        try:
            self.webview.load_html(html_content, base_uri)
        except Exception:
            try:
                self.webview.load_html(html_content)
            except Exception:
                pass

        # helper JS: tries id, name, anchors, and repeats attempts (up to tries) to handle delayed rendering
        if jump_fragment:
            target_raw = jump_fragment[1:] if jump_fragment.startswith('#') else jump_fragment
            target_js = repr(target_raw)

            js_body = (
                "(function(){"
                "var id = %s;"
                "function tryScroll(){"
                "  var el = document.getElementById(id) || document.getElementsByName(id)[0];"
                "  if(!el){"
                "    var a = document.querySelector('a[name=\"'+id+'\"]');"
                "    if(a) el = a;"
                "  }"
                "  if(el){"
                "    try{ el.scrollIntoView({block:'start'}); }catch(e){ try{ el.scrollIntoView(); }catch(e){} }"
                "    try{ el.focus && el.focus(); }catch(e){}"
                "    try{ history.replaceState(null,null,'#'+id); }catch(e){}"
                "    return true;"
                "  }"
                "  return false;"
                "}"
                "var tries = 0, maxTries = 10, delay = 120;"
                "function loop(){"
                "  try{ if(tryScroll()) return; }catch(e){}"
                "  tries++;"
                "  if(tries <= maxTries) setTimeout(loop, delay);"
                "}"
                "/* also set location.hash as a fallback */"
                "try{ location.hash = id; }catch(e){};"
                "setTimeout(loop, 40);"
                "})();"
            ) % target_js

            def on_load_changed(wv, load_event):
                if load_event == WebKit.LoadEvent.FINISHED:
                    try:
                        wv.run_javascript(js_body, None, None, None)
                    except Exception:
                        pass
                    # disconnect handler
                    try:
                        if getattr(self, '_load_changed_id', None):
                            wv.disconnect(self._load_changed_id)
                    except Exception:
                        pass
                    self._load_changed_id = None

            try:
                self._load_changed_id = self.webview.connect('load-changed', on_load_changed)
            except Exception:
                # fallback: try after short delay
                def fallback():
                    try:
                        self.webview.run_javascript(js_body, None, None, None)
                    except Exception:
                        pass
                    return False
                GLib.timeout_add(200, fallback)

        # update progress
        try:
            total_items = len(self.items) if self.items else 1
            progress = (self.current_index + 1) / total_items if total_items > 0 else 0
            self.progress.set_fraction(progress)
            self.progress.set_text(f"{self.current_index + 1}/{total_items}")
        except Exception:
            pass

    def update_navigation(self):
        has_items = hasattr(self, 'items') and self.items
        self.prev_btn.set_sensitive(has_items and self.current_index > 0)
        self.next_btn.set_sensitive(has_items and self.current_index < len(self.items) - 1)

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
