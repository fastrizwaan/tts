import gi
import threading
import subprocess
import os
import json
from urllib.parse import quote
import time
import math

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango

# Import torrfetch
try:
    import torrfetch
    TORRFETCH_AVAILABLE = True
    print("torrfetch loaded successfully")
except ImportError:
    TORRFETCH_AVAILABLE = False
    print("torrfetch not found. Install with: pip install torrfetch")

class ListItemData(GObject.Object):
    # Define properties with types for proper sorting
    title = GObject.Property(type=str)
    size = GObject.Property(type=int)  # Stored as bytes (int) for sorting
    date = GObject.Property(type=str)
    seeders = GObject.Property(type=int)
    leechers = GObject.Property(type=int)
    magnet = GObject.Property(type=str)

    def __init__(self, title, size, date, seeders, leechers, magnet):
        super().__init__()
        self.title = title
        self.size = size
        self.date = date
        self.seeders = seeders
        self.leechers = leechers
        self.magnet = magnet

class TorrentSearchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.TorrentSearch')
        self.window = None
        self.settings = self.load_settings()

    def load_settings(self):
        config_path = os.path.expanduser("~/.config/torrent-search.json")
        default_settings = {
            "last_search": "",
            "results_limit": 100,
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

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_default_size(1200, 700)
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
        self.search_entry.set_hexpand(True)
        header_bar.set_title_widget(self.search_entry)

        # Category dropdown
        self.category_store = Gio.ListStore.new(GObject.Object)
        self.category_combo = Gtk.DropDown(
            model=self.category_store,
            factory=self.create_category_factory()
        )
        
        categories = [("All", "all"), ("Audio", "audio"), ("Video", "video"), 
                     ("Apps", "apps"), ("Games", "games"), ("Books", "books")]
        
        for name, value in categories:
            item = GObject.Object()
            item.name = name
            item.value = value
            self.category_store.append(item)
        
        self.set_dropdown_active_value(self.category_combo, self.app.settings.get("category", "all"))
        header_bar.pack_end(self.category_combo)

        # Search button
        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.connect("clicked", self.on_search_clicked)
        header_bar.pack_end(search_btn)

        # Data Model
        self.list_store = Gio.ListStore.new(ListItemData)
        
        # --- Column View Setup ---
        self.column_view = Gtk.ColumnView()
        self.column_view.add_css_class("data-table")
        
        # Create Sort Model wrapping the list store
        self.sort_model = Gtk.SortListModel(model=self.list_store, sorter=self.column_view.get_sorter())
        
        # Selection Model
        self.selection_model = Gtk.SingleSelection(model=self.sort_model)
        self.column_view.set_model(self.selection_model)

        # 1. Title Column
        self.add_column("Title", "title", self.setup_title_col, self.bind_title_col, expand=True)

        # 2. Size Column (Numeric Sort, Formatted Display)
        self.add_column("Size", "size", self.setup_label_col, self.bind_size_col)

        # 3. Seeders Column (Numeric Sort)
        self.add_column("Seeds", "seeders", self.setup_status_col, self.bind_seeders_col)

        # 4. Leechers Column (Numeric Sort)
        self.add_column("Leech", "leechers", self.setup_status_col, self.bind_leechers_col)

        # 5. Magnet Column (No Sort)
        self.add_action_column("Link")

        # Scrolled Window
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(self.column_view)
        scrolled_window.set_vexpand(True)
        main_box.append(scrolled_window)

        # Status bar
        self.status_bar = Gtk.Label(label="Ready")
        self.status_bar.set_halign(Gtk.Align.START)
        self.status_bar.set_margin_start(10)
        self.status_bar.set_margin_bottom(5)
        main_box.append(self.status_bar)

        # Events
        self.search_entry.connect("activate", self.on_search_clicked)
        self.category_combo.connect("notify::selected", self.on_category_changed)

        if self.app.settings.get("last_search"):
            self.on_search_clicked(None)

    # --- Column Helpers ---

    def add_column(self, title, property_name, setup_func, bind_func, expand=False):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        
        # Enable sorting logic
        if property_name:
            expression = Gtk.PropertyExpression.new(ListItemData, None, property_name)
            if property_name in ["size", "seeders", "leechers"]:
                sorter = Gtk.NumericSorter.new(expression)
                sorter.set_sort_order(Gtk.SortType.DESCENDING) # Default numbers to desc
            else:
                sorter = Gtk.StringSorter.new(expression)
            column.set_sorter(sorter)
        
        if expand:
            column.set_expand(True)
        else:
            column.set_fixed_width(100)
            
        self.column_view.append_column(column)

    def add_action_column(self, title):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_magnet_col)
        factory.connect("bind", self.bind_magnet_col)
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        column.set_fixed_width(60)
        self.column_view.append_column(column)

    # --- Factories & Binders ---

    def setup_title_col(self, factory, item):
        label = Gtk.Label()
        label.set_halign(Gtk.Align.START)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_margin_start(10)
        item.set_child(label)

    def bind_title_col(self, factory, item):
        label = item.get_child()
        obj = item.get_item()
        label.set_text(obj.title)
        label.set_tooltip_text(obj.title)

    def setup_label_col(self, factory, item):
        label = Gtk.Label()
        label.set_halign(Gtk.Align.CENTER)
        item.set_child(label)

    def bind_size_col(self, factory, item):
        label = item.get_child()
        obj = item.get_item()
        # Format the integer bytes to human readable
        label.set_text(self.format_bytes(obj.size))

    def setup_status_col(self, factory, item):
        label = Gtk.Label()
        label.set_halign(Gtk.Align.CENTER)
        item.set_child(label)

    def bind_seeders_col(self, factory, item):
        label = item.get_child()
        obj = item.get_item()
        label.set_markup(f"<span color='green'>{obj.seeders}</span>")

    def bind_leechers_col(self, factory, item):
        label = item.get_child()
        obj = item.get_item()
        label.set_markup(f"<span color='orange'>{obj.leechers}</span>")

    def setup_magnet_col(self, factory, item):
        btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        btn.add_css_class("flat")
        btn.set_halign(Gtk.Align.CENTER)
        item.set_child(btn)

    def bind_magnet_col(self, factory, item):
        btn = item.get_child()
        obj = item.get_item()
        # Disconnect any old signals to prevent duplicates in recycled rows
        if hasattr(btn, "magnet_handler"):
            btn.disconnect(btn.magnet_handler)
        btn.magnet_handler = btn.connect("clicked", lambda b: self.open_magnet(obj.magnet))

    # --- Utilities ---

    def format_bytes(self, size):
        if size == 0:
            return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB")
        i = int(math.floor(math.log(size, 1024)))
        p = math.pow(1024, i)
        s = round(size / p, 2)
        return f"{s} {size_name[i]}"

    def create_category_factory(self):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda f, i: i.set_child(Gtk.Label()))
        factory.connect("bind", lambda f, i: i.get_child().set_text(i.get_item().name))
        return factory

    def set_dropdown_active_value(self, dropdown, value):
        for i in range(self.category_store.get_n_items()):
            if self.category_store.get_item(i).value == value:
                dropdown.set_selected(i)
                return

    def get_dropdown_active_value(self, dropdown):
        item = dropdown.get_selected_item()
        return item.value if item else "all"

    def on_category_changed(self, dropdown, pspec):
        self.app.settings["category"] = self.get_dropdown_active_value(dropdown)
        self.app.save_settings()

    def on_search_clicked(self, button):
        query = self.search_entry.get_text().strip()
        if not query: return
        
        self.app.settings["last_search"] = query
        self.app.save_settings()
        self.status_bar.set_text("Searching...")
        self.list_store.remove_all()
        threading.Thread(target=self.perform_search, args=(query,), daemon=True).start()

    def perform_search(self, query):
        try:
            results_data = []
            if TORRFETCH_AVAILABLE:
                raw_results = torrfetch.search_torrents(query, mode="parallel")
                for res in raw_results:
                    # Convert to strict types for GObject/Sorting
                    try:
                        size_val = self.parse_size_to_bytes(res.get("size", "0"))
                        seeds_val = int(res.get("seeders", 0))
                        leech_val = int(res.get("leechers", 0))
                    except ValueError:
                        continue # Skip malformed data

                    results_data.append(ListItemData(
                        title=res.get("title", "Unknown"),
                        size=size_val,
                        date=res.get("uploaded", "Unknown"),
                        seeders=seeds_val,
                        leechers=leech_val,
                        magnet=res.get("magnet", "")
                    ))
            else:
                results_data = self.search_tpb_fallback(query)
            
            GLib.idle_add(self.finalize_search, results_data)
        except Exception as e:
            GLib.idle_add(self.status_bar.set_text, f"Error: {e}")

    def finalize_search(self, results):
        # We don't need manual sorting here anymore because ColumnView 
        # handles it via the Sorter, but we can limit results if needed.
        limit = self.app.settings.get("results_limit", 100)
        
        # Bulk splice is faster than appending one by one
        self.list_store.splice(0, 0, results[:limit])
        self.status_bar.set_text(f"Found {len(results)} results")

    def parse_size_to_bytes(self, size_str):
        if isinstance(size_str, int): return size_str
        try:
            parts = size_str.strip().split()
            if len(parts) < 2: return 0
            num = float(parts[0])
            unit = parts[1].upper()
            multipliers = {'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
            return int(num * multipliers.get(unit, 1))
        except:
            return 0

    def search_tpb_fallback(self, query):
        import requests
        results = []
        try:
            url = f"https://apibay.org/q.php?q={quote(query)}"
            data = requests.get(url, timeout=10).json()
            if data and data[0].get("name") != "No results found":
                for item in data:
                    results.append(ListItemData(
                        title=item.get("name"),
                        size=int(item.get("size", 0)), # APIm returns bytes
                        date=item.get("added"),
                        seeders=int(item.get("seeders", 0)),
                        leechers=int(item.get("leechers", 0)),
                        magnet=f"magnet:?xt=urn:btih:{item.get('info_hash')}"
                    ))
        except Exception as e:
            print(f"Fallback error: {e}")
        return results

    def open_magnet(self, link):
        subprocess.Popen(["xdg-open", link])

def main():
    app = TorrentSearchApp()
    app.run()

if __name__ == "__main__":
    main()
