import gi
import threading
import subprocess
import os
import json
from urllib.parse import quote
import time

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib

# Import PirateBayAPI
try:
    import PirateBayAPI
    PIRATE_BAY_AVAILABLE = True
except ImportError:
    PIRATE_BAY_AVAILABLE = False
    print("PirateBayAPI not found. Install with: pip install PirateBayAPI")

class TorrentSearchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.TorrentSearch')
        self.window = None
        self.search_results = []
        self.current_search = None
        self.settings = self.load_settings()

    def load_settings(self):
        config_path = os.path.expanduser("~/.config/torrent-search.json")
        default_settings = {
            "last_search": "",
            "sort_order": "seeders",
            "results_limit": 200,
            "category": "all"
        }
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return default_settings

    def save_settings(self):
        config_path = os.path.expanduser("~/.config/torrent-search.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(self.settings, f)

    def do_activate(self):
        if not self.window:
            self.window = MainWindow(self)
        self.window.present()

class CustomRow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.set_margin_start(10)
        self.set_margin_end(10)
        self.set_margin_top(5)
        self.set_margin_bottom(5)
        
        # Title column
        self.title_label = Gtk.Label()
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_valign(Gtk.Align.CENTER)
        self.title_label.set_wrap(True)
        self.title_label.set_max_width_chars(50)
        self.title_label.add_css_class("title-4")
        self.title_label.set_hexpand(True)
        
        # Size column
        self.size_label = Gtk.Label()
        self.size_label.set_halign(Gtk.Align.CENTER)
        self.size_label.set_valign(Gtk.Align.CENTER)
        self.size_label.add_css_class("dim-label")
        
        # Date column
        self.date_label = Gtk.Label()
        self.date_label.set_halign(Gtk.Align.CENTER)
        self.date_label.set_valign(Gtk.Align.CENTER)
        self.date_label.add_css_class("dim-label")
        
        # Seeders column
        self.seeders_label = Gtk.Label()
        self.seeders_label.set_halign(Gtk.Align.CENTER)
        self.seeders_label.set_valign(Gtk.Align.CENTER)
        self.seeders_label.add_css_class("success")
        
        # Leechers column
        self.leechers_label = Gtk.Label()
        self.leechers_label.set_halign(Gtk.Align.CENTER)
        self.leechers_label.set_valign(Gtk.Align.CENTER)
        self.leechers_label.add_css_class("warning")
        
        # Magnet column
        self.magnet_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.magnet_btn.add_css_class("flat")
        
        # Pack the widgets
        self.append(self.title_label)
        self.append(self.size_label)
        self.append(self.date_label)
        self.append(self.seeders_label)
        self.append(self.leechers_label)
        self.append(self.magnet_btn)

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_default_size(1200, 600)
        self.set_title("Torrent Search")

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        # Search entry
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search torrents...")
        self.search_entry.set_text(self.app.settings.get("last_search", ""))
        header_bar.set_title_widget(self.search_entry)

        # Category dropdown
        self.category_store = Gio.ListStore.new(GObject.Object)
        self.category_combo = Gtk.DropDown(
            model=self.category_store,
            factory=self.create_category_factory()
        )
        
        # Add categories with their actual TPB category IDs
        categories = [
            ("All", "all"),
            ("Audio", "100"),      # Audio
            ("Video", "200"),      # Video
            ("Applications", "300"), # Applications
            ("Games", "400"),      # Games
            ("E-books", "600"),    # Books/E-books
            ("Other", "0")         # Other
        ]
        
        for name, value in categories:
            item = GObject.Object()
            item.name = name
            item.value = value
            self.category_store.append(item)
        
        # Set default value
        default_category = self.app.settings.get("category", "all")
        self.set_dropdown_active_value(self.category_combo, default_category)
        header_bar.pack_end(self.category_combo)

        # Search button
        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.connect("clicked", self.on_search_clicked)
        header_bar.pack_end(search_btn)

        # Filter entry
        self.filter_entry = Gtk.SearchEntry(placeholder_text="Filter results...")
        self.filter_entry.connect("search-changed", self.on_filter_changed)
        header_bar.pack_end(self.filter_entry)

        # Results list with headers
        self.list_store = Gio.ListStore.new(ListItemData)
        
        # Create a custom factory
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection(model=self.list_store),
            factory=factory
        )
        
        # Scrolled window with expand properties
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(self.list_view)
        scrolled_window.set_vexpand(True)  # Expand vertically
        scrolled_window.set_hexpand(True)  # Expand horizontally
        main_box.append(scrolled_window)

        # Status bar
        self.status_bar = Gtk.Label()
        self.status_bar.set_halign(Gtk.Align.START)
        self.status_bar.set_text("Ready")
        main_box.append(self.status_bar)

        # Connect search activation
        self.search_entry.connect("activate", self.on_search_clicked)
        self.category_combo.connect("notify::selected", self.on_category_changed)

        # Initial search if there was a previous search
        if self.app.settings.get("last_search"):
            self.on_search_clicked(None)

    def create_category_factory(self):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_category_row)
        factory.connect("bind", self.bind_category_row)
        return factory

    def setup_category_row(self, factory, list_item):
        label = Gtk.Label()
        list_item.set_child(label)

    def bind_category_row(self, factory, list_item):
        label = list_item.get_child()
        item_data = list_item.get_item()
        label.set_text(item_data.name)

    def set_dropdown_active_value(self, dropdown, value):
        for i in range(self.category_store.get_n_items()):
            item = self.category_store.get_item(i)
            if item.value == value:
                dropdown.set_selected(i)
                return

    def get_dropdown_active_value(self, dropdown):
        selected_item = dropdown.get_selected_item()
        if selected_item:
            return selected_item.value
        return "all"

    def setup_row(self, factory, list_item):
        # Create a custom row widget
        row = CustomRow()
        list_item.set_child(row)

    def bind_row(self, factory, list_item):
        row = list_item.get_child()
        item_data = list_item.get_item()
        
        # Bind data to UI elements
        row.title_label.set_text(item_data.title)
        row.title_label.set_tooltip_text(item_data.title)
        row.size_label.set_text(self.format_size(item_data.size))
        row.date_label.set_text(item_data.date)
        row.seeders_label.set_text(f"↑{item_data.seeders}")
        row.leechers_label.set_text(f"↓{item_data.leechers}")
        
        # Connect the magnet button to open the magnet link
        row.magnet_btn.connect("clicked", lambda btn: self.open_magnet(item_data.magnet))

    def format_size(self, size_str):
        """Convert size string to human-readable format (MB/GB)"""
        try:
            size = int(size_str)
            if size < 1024**2:  # Less than 1 MB
                return f"{size} B"
            elif size < 1024**3:  # Less than 1 GB
                return f"{size / 1024**2:.1f} MB"
            else:  # 1 GB or more
                return f"{size / 1024**3:.1f} GB"
        except ValueError:
            return size_str

    def on_category_changed(self, dropdown, pspec):
        category = self.get_dropdown_active_value(dropdown)
        self.app.settings["category"] = category
        self.app.save_settings()

    def on_search_clicked(self, button):
        query = self.search_entry.get_text().strip()
        if not query:
            return

        self.app.settings["last_search"] = query
        self.app.save_settings()
        self.status_bar.set_text("Searching...")
        
        # Clear previous results
        self.list_store.remove_all()
        
        # Start search in background thread
        threading.Thread(target=self.perform_search, args=(query,), daemon=True).start()

    def on_filter_changed(self, entry):
        filter_text = entry.get_text().lower()
        if not filter_text:
            # Show all results if filter is empty
            self.update_results(self.full_results)
            return

        # Filter results based on title
        filtered_results = [
            item for item in self.full_results
            if filter_text in item["title"].lower()
        ]
        self.update_results(filtered_results)

    def perform_search(self, query):
        try:
            all_results = []
            
            if PIRATE_BAY_AVAILABLE:
                # Use PirateBayAPI
                category = self.get_dropdown_active_value(self.category_combo)
                
                # Convert category to int if it's a number, otherwise None
                category_id = None
                if category != "all":
                    try:
                        category_id = int(category)
                    except ValueError:
                        category_id = None
                
                # Perform search with category ID
                results = PirateBayAPI.Search(query, category_id)
                
                for result in results:
                    # --- DEBUG: Print available attributes ---
                    print(f"\n🔍 SearchElement attributes:")
                    available_attrs = dir(result)
                    for attr in available_attrs:
                        if not attr.startswith('_'):  # Skip private attributes
                            value = getattr(result, attr, None)
                            print(f"  {attr}: {value}")
                    
                    # Check if this is a "No results" message
                    if result.name == "No results returned":
                        print("⚠️  No results found for this search.")
                        continue  # Skip this item
                    
                    # --- Get title ---
                    title = result.name
                    
                    # --- Get size ---
                    size = result.size
                    
                    # --- Get date (use 'added' attribute) ---
                    date_str = str(result.added) if result.added else "Unknown"
                    
                    # --- Get seeders and leechers ---
                    seeders = result.seeders
                    leechers = result.leechers
                    
                    # --- Construct magnet link from info_hash ---
                    info_hash = result.info_hash
                    if info_hash and info_hash != "0000000000000000000000000000000000000000":
                        magnet = f"magnet:?xt=urn:btih:{info_hash}"
                    else:
                        magnet = "magnet:?xt=urn:btih:UNKNOWN"  # Fallback
                    
                    all_results.append({
                        "title": title,
                        "size": str(size),
                        "date": date_str,
                        "seeders": str(seeders),
                        "leechers": str(leechers),
                        "magnet": magnet
                    })
            else:
                # Fallback to web scraping if API is not available
                all_results = self.search_tpb_fallback(query)
            
            # Sort results by seeders
            sorted_results = sorted(
                all_results, 
                key=lambda x: int(x['seeders']) if x['seeders'].isdigit() else 0, 
                reverse=True
            )
            
            # Limit results to configurable amount
            self.full_results = sorted_results[:self.app.settings['results_limit']]
            
            # Update UI in main thread
            GLib.idle_add(self.update_results, self.full_results)
        except Exception as e:
            GLib.idle_add(self.show_error, str(e))
    def search_tpb_fallback(self, query):
        """Fallback search using The Pirate Bay API"""
        import requests
        results = []
        try:
            encoded_query = quote(query)
            url = f"https://apibay.org/q.php?q={encoded_query}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            if data and isinstance(data, list) and data[0].get("name") != "No results found":
                for item in data:
                    if item.get("name"):
                        results.append({
                            "title": item.get("name", "Unknown"),
                            "size": str(item.get("size", 0)),
                            "date": item.get("added", "Unknown"),
                            "seeders": item.get("seeders", "0"),
                            "leechers": item.get("leechers", "0"),
                            "magnet": f"magnet:?xt=urn:btih:{item.get('info_hash', '')}"
                        })
        except Exception as e:
            print(f"Error searching The Pirate Bay API: {e}")
        return results

    def update_results(self, results):
        self.list_store.remove_all()
        for item in results:
            list_item = ListItemData(
                title=item["title"],
                size=item["size"],
                date=item["date"],
                seeders=item["seeders"],
                leechers=item["leechers"],
                magnet=item["magnet"]
            )
            self.list_store.append(list_item)
        
        count = self.list_store.get_n_items()
        self.status_bar.set_text(f"Found {count} results")

    def show_error(self, message):
        self.status_bar.set_text(f"Error: {message}")
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Search Error",
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def open_magnet(self, magnet_link):
        try:
            subprocess.run(["xdg-open", magnet_link])
        except FileNotFoundError:
            self.status_bar.set_text("Error: xdg-open not found")

class ListItemData(GObject.Object):
    def __init__(self, title, size, date, seeders, leechers, magnet):
        super().__init__()
        self.title = title
        self.size = size
        self.date = date
        self.seeders = seeders
        self.leechers = leechers
        self.magnet = magnet

def main():
    app = TorrentSearchApp()
    app.run()

if __name__ == "__main__":
    main()
