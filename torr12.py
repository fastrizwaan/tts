import gi
import threading
import subprocess
import os
import json
from urllib.parse import quote
import math
import requests
import urllib3

# Suppress insecure request warnings for mirrors
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango

# Import torrfetch
try:
    import torrfetch
    TORRFETCH_AVAILABLE = True
except ImportError:
    TORRFETCH_AVAILABLE = False
    print("torrfetch not found. Install with: pip install torrfetch")

class ListItemData(GObject.Object):
    # PROPERTIES
    title = GObject.Property(type=str)
    size = GObject.Property(type=GObject.TYPE_INT64) 
    date = GObject.Property(type=str)
    seeders = GObject.Property(type=int)
    leechers = GObject.Property(type=int)
    magnet = GObject.Property(type=str)
    source = GObject.Property(type=str)

    def __init__(self, title, size, date, seeders, leechers, magnet, source="Unknown"):
        super().__init__()
        self.title = title
        self.size = size
        self.date = date
        self.seeders = seeders
        self.leechers = leechers
        self.magnet = magnet
        self.source = source

class TorrentSearchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.TorrentSearch')
        self.window = None
        self.settings = self.load_settings()

    def load_settings(self):
        config_path = os.path.expanduser("~/.config/torrent-search.json")
        default_settings = {
            "last_search": "",
            "results_limit": 1000,
            "category": "all"
        }
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
                for k, v in default_settings.items():
                    if k not in data:
                        data[k] = v
                return data
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
        self.set_title("Torrent Search (Stable Scroll)")
        
        self.all_results_cache = []

        # Request Session with Browser Headers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        # 1. Main Search (Web)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search web (YTS, TPB...)")
        self.search_entry.set_text(self.app.settings.get("last_search", ""))
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self.on_search_clicked)
        header_bar.set_title_widget(self.search_entry)

        # 2. Local Filter
        self.filter_entry = Gtk.SearchEntry(placeholder_text="Filter list...")
        self.filter_entry.connect("search-changed", lambda w: self.refresh_view())
        header_bar.pack_end(self.filter_entry)

        # 3. Category Dropdown
        self.category_store = Gio.ListStore.new(GObject.Object)
        self.category_combo = Gtk.DropDown(
            model=self.category_store,
            factory=self.create_category_factory()
        )
        
        categories = [("All", "all"), ("Video", "video"), ("Audio", "audio"), 
                     ("Apps", "apps"), ("Games", "games"), ("Books", "books")]
        
        for name, value in categories:
            item = GObject.Object()
            item.name = name
            item.value = value
            self.category_store.append(item)
        
        self.set_dropdown_active_value(self.category_combo, self.app.settings.get("category", "all"))
        self.category_combo.connect("notify::selected", self.on_category_changed)
        header_bar.pack_end(self.category_combo)

        # 4. Search Button
        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.connect("clicked", self.on_search_clicked)
        header_bar.pack_end(search_btn)

        # Data Model & Columns
        self.list_store = Gio.ListStore.new(ListItemData)
        self.column_view = Gtk.ColumnView()
        self.column_view.add_css_class("data-table")
        self.column_view.connect("activate", self.on_row_activated)
        
        self.sort_model = Gtk.SortListModel(model=self.list_store, sorter=self.column_view.get_sorter())
        self.selection_model = Gtk.SingleSelection(model=self.sort_model)
        self.column_view.set_model(self.selection_model)

        self.add_column("Title", "title", self.setup_title_col, self.bind_title_col, expand=True)
        self.add_column("Size", "size", self.setup_label_col, self.bind_size_col)
        self.add_column("Seeds", "seeders", self.setup_status_col, self.bind_seeders_col)
        self.add_column("Leech", "leechers", self.setup_status_col, self.bind_leechers_col)
        self.add_column("Source", "source", self.setup_label_col, self.bind_source_col)
        self.add_action_column("Link")

        # --- SCROLLBAR FIX ---
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_child(self.column_view)
        scrolled_window.set_vexpand(True)
        
        # KEY FIXES:
        # 1. Don't let the list push the window size (forces scrollbar to engage)
        scrolled_window.set_propagate_natural_height(False)
        scrolled_window.set_propagate_natural_width(False)
        # 2. Ensure a minimum height so it doesn't collapse
        scrolled_window.set_min_content_height(400)
        
        main_box.append(scrolled_window)

        self.status_bar = Gtk.Label(label="Ready")
        self.status_bar.set_halign(Gtk.Align.START)
        self.status_bar.set_margin_start(10)
        self.status_bar.set_margin_bottom(5)
        main_box.append(self.status_bar)

        if self.app.settings.get("last_search"):
            self.on_search_clicked(None)

    # --- Logic ---

    def refresh_view(self):
        category = self.app.settings.get("category", "all")
        limit = self.app.settings.get("results_limit", 1000)
        filter_text = self.filter_entry.get_text().lower().strip()
        
        filtered_items = []
        for item in self.all_results_cache:
            if not self.matches_category(item, category): continue
            if filter_text and filter_text not in item.title.lower(): continue
            filtered_items.append(item)
        
        final_list = filtered_items[:limit]
        self.list_store.remove_all()
        self.list_store.splice(0, 0, final_list)
        
        count = len(final_list)
        total = len(self.all_results_cache)
        msg = f"Showing {count} results"
        if category != "all" or filter_text:
            msg += f" (Filtered from {total})"
        self.status_bar.set_text(msg)

    def matches_category(self, item, category):
        if category == "all": return True
        title = item.title.lower()
        
        video_ext = ('.mkv', '.mp4', '.avi', '.wmv', '.mov', '.flv', '.webm', '.iso')
        video_keys = ('1080p', '720p', '480p', 'h.264', 'x264', 'hevc', 'bluray', 'hdrip', 'web-dl', 'season', 'episode')
        audio_ext = ('.mp3', '.flac', '.wav', '.aac', '.ogg', '.m4a')
        audio_keys = ('320kbps', 'flac', 'discography', 'soundtrack', 'ost')
        app_ext = ('.exe', '.dmg', '.pkg', '.deb', '.rpm', '.msi')
        app_keys = ('crack', 'patch', 'keygen', 'activator', 'multilingual')
        book_ext = ('.pdf', '.epub', '.mobi', '.cbz', '.cbr', '.azw3')
        
        if category == "video":
            return title.endswith(video_ext) or any(k in title for k in video_keys)
        elif category == "audio":
            return title.endswith(audio_ext) or any(k in title for k in audio_keys)
        elif category == "apps":
            if title.endswith(video_ext) or title.endswith(audio_ext): return False
            return title.endswith(app_ext) or any(k in title for k in app_keys)
        elif category == "games":
            return "repack" in title or "fitgirl" in title or "dodi" in title or "codex" in title or "game" in title
        elif category == "books":
            return title.endswith(book_ext)
        return True

    def on_search_clicked(self, button):
        query = self.search_entry.get_text().strip()
        if not query: return
        self.app.settings["last_search"] = query
        self.app.save_settings()
        self.status_bar.set_text(f"Searching for '{query}'...")
        self.list_store.remove_all()
        threading.Thread(target=self.perform_search, args=(query,), daemon=True).start()

    def perform_search(self, query):
        combined_results = []
        threads = []
        
        yts_res = []
        tpb_res = []
        torr_res = []

        # Worker Functions
        def run_yts(): yts_res.extend(self.search_yts_api(query))
        def run_tpb(): tpb_res.extend(self.search_tpb_api(query))
        def run_torrfetch():
            if TORRFETCH_AVAILABLE:
                try:
                    for res in torrfetch.search_torrents(query, mode="parallel"):
                        torr_res.append(res)
                except: pass

        # Start threads
        t1 = threading.Thread(target=run_yts); t1.start(); threads.append(t1)
        t2 = threading.Thread(target=run_tpb); t2.start(); threads.append(t2)
        t3 = threading.Thread(target=run_torrfetch); t3.start(); threads.append(t3)

        for t in threads: t.join()

        # Process Torrfetch results
        processed_torr = []
        for res in torr_res:
             try:
                processed_torr.append(ListItemData(
                    title=res.get("title", "Unknown"),
                    size=self.parse_size_to_bytes(res.get("size", "0")),
                    date=res.get("uploaded", "Unknown"),
                    seeders=int(res.get("seeders", 0)),
                    leechers=int(res.get("leechers", 0)),
                    magnet=res.get("magnet", ""),
                    source=res.get("source", "Torrfetch")
                ))
             except: continue

        # Combine results
        combined_results = yts_res + tpb_res + processed_torr
        combined_results.sort(key=lambda x: x.seeders, reverse=True)
        GLib.idle_add(lambda: [setattr(self, 'all_results_cache', combined_results), self.refresh_view()])

    # --- API Implementations ---

    def _fetch_with_mirrors(self, domains, path, params=None):
        """Helper to try multiple domains until one works"""
        for domain in domains:
            try:
                url = f"https://{domain}{path}"
                print(f"Trying {url}...")
                resp = self.session.get(url, params=params, timeout=5, verify=False)
                resp.raise_for_status()
                return resp
            except Exception as e:
                print(f"Failed {domain}: {e}")
                continue
        return None

    def search_yts_api(self, query):
        """YTS with Mirror Rotation"""
        results = []
        # List of mirrors to try (Official + Proxies)
        mirrors = ["yts.mx", "yts.rs", "yts.lt", "yts.do", "yts.ag"]
        
        path = "/api/v2/list_movies.json"
        params = {"query_term": query, "limit": 50, "sort_by": "seeds"}
        
        resp = self._fetch_with_mirrors(mirrors, path, params)
        if not resp: return results

        try:
            data = resp.json()
            if data.get("status") == "ok" and data.get("data").get("movie_count") > 0:
                for movie in data["data"]["movies"]:
                    title_base = movie.get("title_long") or movie.get("title")
                    for torrent in movie.get("torrents", []):
                        quality = torrent.get("quality", "")
                        type_ = torrent.get("type", "")
                        full_title = f"{title_base} [{quality}] [{type_}]"
                        h = torrent.get("hash")
                        magnet = f"magnet:?xt=urn:btih:{h}&dn={quote(full_title)}"
                        results.append(ListItemData(
                            title=full_title,
                            size=int(torrent.get("size_bytes", 0)),
                            date=str(movie.get("year", "")),
                            seeders=torrent.get("seeds", 0),
                            leechers=torrent.get("peers", 0),
                            magnet=magnet,
                            source="YTS"
                        ))
        except Exception as e:
            print(f"YTS Parse Error: {e}")
        return results

    def search_tpb_api(self, query):
        results = []
        # TPB mirrors/APIs
        urls = [f"https://apibay.org/q.php?q={quote(query)}"]
        
        for url in urls:
            try:
                resp = self.session.get(url, timeout=10)
                data = resp.json()
                if data and isinstance(data, list) and data[0].get("name") != "No results found":
                    for item in data:
                        results.append(ListItemData(
                            title=item.get("name"),
                            size=int(item.get("size", 0)), 
                            date=self.format_tpb_date(item.get("added")),
                            seeders=int(item.get("seeders", 0)),
                            leechers=int(item.get("leechers", 0)),
                            magnet=f"magnet:?xt=urn:btih:{item.get('info_hash')}",
                            source="TPB"
                        ))
                    break # Success
            except: pass
        return results

    # --- Helpers ---

    def format_tpb_date(self, timestamp):
        try: return time.ctime(int(timestamp))
        except: return str(timestamp)

    def format_bytes(self, size):
        if size == 0: return "0 B"
        i = int(math.floor(math.log(size, 1024)))
        return f"{round(size / math.pow(1024, i), 2)} {('B', 'KB', 'MB', 'GB', 'TB', 'PB')[i]}"

    def parse_size_to_bytes(self, size_str):
        if isinstance(size_str, int): return size_str
        try:
            p = size_str.strip().split()
            return int(float(p[0]) * {'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4, 'MiB': 1024**2, 'GiB': 1024**3}.get(p[1].upper(), 1))
        except: return 0

    def open_magnet(self, link):
        subprocess.Popen(["xdg-open", link])

    # --- UI Factories ---
    def add_column(self, title, property_name, setup_func, bind_func, expand=False):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        if property_name:
            expression = Gtk.PropertyExpression.new(ListItemData, None, property_name)
            if property_name in ["size", "seeders", "leechers"]:
                sorter = Gtk.NumericSorter.new(expression)
                sorter.set_sort_order(Gtk.SortType.DESCENDING)
            else:
                sorter = Gtk.StringSorter.new(expression)
            column.set_sorter(sorter)
        if expand: column.set_expand(True)
        else: column.set_fixed_width(100)
        self.column_view.append_column(column)

    def add_action_column(self, title):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_magnet_col)
        factory.connect("bind", self.bind_magnet_col)
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        column.set_fixed_width(60)
        self.column_view.append_column(column)

    def setup_title_col(self, factory, item):
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.set_margin_start(10)
        item.set_child(label)
    def bind_title_col(self, factory, item):
        item.get_child().set_text(item.get_item().title)
        item.get_child().set_tooltip_text(item.get_item().title)

    def setup_label_col(self, factory, item):
        item.set_child(Gtk.Label(xalign=0.5))
    def bind_size_col(self, factory, item):
        item.get_child().set_text(self.format_bytes(item.get_item().size))
    def bind_source_col(self, factory, item):
        item.get_child().set_text(item.get_item().source)

    def setup_status_col(self, factory, item):
        item.set_child(Gtk.Label(xalign=0.5))
    def bind_seeders_col(self, factory, item):
        item.get_child().set_markup(f"<span color='green'><b>{item.get_item().seeders}</b></span>")
    def bind_leechers_col(self, factory, item):
        item.get_child().set_markup(f"<span color='orange'>{item.get_item().leechers}</span>")

    def setup_magnet_col(self, factory, item):
        btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        btn.add_css_class("flat")
        item.set_child(btn)
    def bind_magnet_col(self, factory, item):
        btn = item.get_child()
        obj = item.get_item()
        if hasattr(btn, "magnet_handler"): btn.disconnect(btn.magnet_handler)
        btn.magnet_handler = btn.connect("clicked", lambda b: self.open_magnet(obj.magnet))

    def on_row_activated(self, view, position):
        self.show_details_dialog(self.selection_model.get_item(position))

    def show_details_dialog(self, item):
        dialog = Adw.Window(title="Torrent Details", modal=True, transient_for=self)
        dialog.set_default_size(500, 300)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        
        def add_row(lbl, val, selectable=False):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.append(Gtk.Label(label=f"<b>{lbl}:</b>", use_markup=True, xalign=0, width_request=80))
            if selectable:
                v = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.CHAR, hexpand=True, height_request=80 if lbl=="Magnet" else -1)
                v.get_buffer().set_text(str(val))
                f = Gtk.Frame(child=v, hexpand=True)
                row.append(f)
            else:
                row.append(Gtk.Label(label=str(val), xalign=0, wrap=True, hexpand=True))
            box.append(row)

        add_row("Title", item.title)
        add_row("Size", f"{self.format_bytes(item.size)} ({item.size} bytes)")
        add_row("Source", item.source)
        add_row("Peers", f"{item.seeders} Seeds / {item.leechers} Leech")
        add_row("Magnet", item.magnet, True)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.END)
        copy_btn = Gtk.Button(label="Copy Magnet")
        copy_btn.connect("clicked", lambda x: [self.get_display().get_clipboard().set(item.magnet), self.status_bar.set_text("Copied!")])
        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", lambda x: self.open_magnet(item.magnet))
        btn_box.append(copy_btn)
        btn_box.append(open_btn)
        box.append(btn_box)

        content = Adw.Bin(child=box)
        dialog.set_content(content)
        dialog.present()

    def create_category_factory(self):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _, i: i.set_child(Gtk.Label()))
        f.connect("bind", lambda _, i: i.get_child().set_text(i.get_item().name))
        return f

    def set_dropdown_active_value(self, dropdown, value):
        for i in range(self.category_store.get_n_items()):
            if self.category_store.get_item(i).value == value:
                dropdown.set_selected(i)
                return

    def on_category_changed(self, dropdown, pspec):
        self.app.settings["category"] = dropdown.get_selected_item().value
        self.app.save_settings()
        self.refresh_view()

if __name__ == "__main__":
    TorrentSearchApp().run()
