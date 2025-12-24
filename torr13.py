import gi
import threading
import subprocess
import os
import json
from urllib.parse import quote
import math
import requests # Added dependency
import urllib3

# Suppress insecure request warnings for proxies
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
            "results_limit": 500,
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
        self.set_title("Torrent Search")
        
        # HTTP Session for API calls
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        })
        
        # Cache for raw results to allow instant filtering
        self.all_results_cache = []

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
        
        categories = [
            ("All", "all"), 
            ("Video", "video"), 
            ("Audio", "audio"), 
            ("Apps", "apps"), 
            ("Games", "games"), 
            ("Books", "books")
        ]
        
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
        self.column_view.connect("activate", self.on_row_activated)
        
        # Sort Model
        self.sort_model = Gtk.SortListModel(model=self.list_store, sorter=self.column_view.get_sorter())
        self.selection_model = Gtk.SingleSelection(model=self.sort_model)
        self.column_view.set_model(self.selection_model)

        # Columns
        self.add_column("Title", "title", self.setup_title_col, self.bind_title_col, expand=True)
        self.add_column("Size", "size", self.setup_label_col, self.bind_size_col)
        self.add_column("Seeds", "seeders", self.setup_status_col, self.bind_seeders_col)
        self.add_column("Leech", "leechers", self.setup_status_col, self.bind_leechers_col)
        self.add_column("Source", "source", self.setup_label_col, self.bind_source_col)
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
        if property_name:
            expression = Gtk.PropertyExpression.new(ListItemData, None, property_name)
            if property_name in ["size", "seeders", "leechers"]:
                sorter = Gtk.NumericSorter.new(expression)
                sorter.set_sort_order(Gtk.SortType.DESCENDING)
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
        item.get_child().set_text(self.format_bytes(item.get_item().size))

    def bind_source_col(self, factory, item):
        item.get_child().set_text(item.get_item().source)

    def setup_status_col(self, factory, item):
        label = Gtk.Label()
        label.set_halign(Gtk.Align.CENTER)
        item.set_child(label)

    def bind_seeders_col(self, factory, item):
        item.get_child().set_markup(f"<span color='green'><b>{item.get_item().seeders}</b></span>")

    def bind_leechers_col(self, factory, item):
        item.get_child().set_markup(f"<span color='orange'>{item.get_item().leechers}</span>")

    def setup_magnet_col(self, factory, item):
        btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        btn.add_css_class("flat")
        btn.set_halign(Gtk.Align.CENTER)
        item.set_child(btn)

    def bind_magnet_col(self, factory, item):
        btn = item.get_child()
        obj = item.get_item()
        if hasattr(btn, "magnet_handler"):
            btn.disconnect(btn.magnet_handler)
        btn.magnet_handler = btn.connect("clicked", lambda b: self.open_magnet(obj.magnet))

    # --- Interaction ---
    def on_row_activated(self, view, position):
        item = self.selection_model.get_item(position)
        self.show_details_dialog(item)

    def show_details_dialog(self, item):
        dialog = Adw.Window(title="Torrent Details")
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_default_size(500, 300)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        
        def add_info_row(label_text, value_text, is_selectable=False):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            lbl = Gtk.Label(label=f"<b>{label_text}:</b>", use_markup=True, xalign=0)
            lbl.set_size_request(80, -1)
            
            if is_selectable:
                val = Gtk.TextView()
                val.set_editable(False)
                val.set_wrap_mode(Gtk.WrapMode.CHAR)
                val.get_buffer().set_text(str(value_text))
                val.set_hexpand(True)
                val.set_size_request(-1, 80)
                frame = Gtk.Frame()
                frame.set_child(val)
                frame.set_hexpand(True)
                row.append(lbl)
                row.append(frame)
            else:
                val = Gtk.Label(label=str(value_text), xalign=0)
                val.set_wrap(True)
                val.set_hexpand(True)
                row.append(lbl)
                row.append(val)
            box.append(row)

        add_info_row("Title", item.title)
        add_info_row("Size", f"{self.format_bytes(item.size)} ({item.size} bytes)")
        add_info_row("Source", item.source)
        add_info_row("Date", item.date)
        add_info_row("Peers", f"{item.seeders} Seeds / {item.leechers} Leech")
        add_info_row("Magnet", item.magnet, is_selectable=True)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        copy_btn = Gtk.Button(label="Copy Magnet")
        copy_btn.add_css_class("suggested-action")
        copy_btn.connect("clicked", lambda x: self.copy_to_clipboard(item.magnet))
        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", lambda x: self.open_magnet(item.magnet))
        
        btn_box.append(copy_btn)
        btn_box.append(open_btn)
        box.append(btn_box)

        content = Adw.Bin()
        content.set_child(box)
        dialog.set_content(content)
        dialog.present()

    def copy_to_clipboard(self, text):
        clipboard = self.get_display().get_clipboard()
        clipboard.set(text)
        self.status_bar.set_text("Magnet link copied to clipboard")

    # --- Filtering Logic ---
    def matches_category(self, item, category):
        if category == "all": return True
        
        title = item.title.lower()
        
        # Heuristic keywords and extensions
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
            # Exclude common video/audio false positives for apps
            if title.endswith(video_ext) or title.endswith(audio_ext): return False
            return title.endswith(app_ext) or any(k in title for k in app_keys)
        elif category == "games":
            # Games are tricky, usually large ISOs or Repacks
            return "repack" in title or "fitgirl" in title or "dodi" in title or "codex" in title or "game" in title
        elif category == "books":
            return title.endswith(book_ext)
            
        return True

    def refresh_view(self):
        """Filters cached results and updates the UI"""
        category = self.app.settings.get("category", "all")
        limit = self.app.settings.get("results_limit", 500)
        
        filtered_items = []
        for item in self.all_results_cache:
            if self.matches_category(item, category):
                filtered_items.append(item)
        
        # Apply limit after filtering
        final_list = filtered_items[:limit]
        
        self.list_store.remove_all()
        self.list_store.splice(0, 0, final_list)
        
        count = len(final_list)
        total = len(self.all_results_cache)
        msg = f"Showing {count} results"
        if category != "all":
            msg += f" (Filtered from {total})"
        self.status_bar.set_text(msg)

    # --- Core Search ---
    def on_search_clicked(self, button):
        query = self.search_entry.get_text().strip()
        if not query: return
        
        self.app.settings["last_search"] = query
        self.app.save_settings()
        self.status_bar.set_text("Searching (YTS, TPB, Torrfetch)...")
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
        # Sort by seeders
        combined_results.sort(key=lambda x: x.seeders, reverse=True)
        
        # Update UI safely
        GLib.idle_add(self.update_ui_with_results, combined_results)

    def update_ui_with_results(self, results):
        self.all_results_cache = results
        self.refresh_view()
        if not results:
            self.status_bar.set_text("No results found.")

    # --- API Implementations ---

    def _fetch_with_mirrors(self, domains, path, params=None):
        """Helper to try multiple domains until one works"""
        for domain in domains:
            try:
                url = f"https://{domain}{path}"
                # print(f"Trying {url}...") 
                resp = self.session.get(url, params=params, timeout=5, verify=False)
                resp.raise_for_status()
                return resp
            except Exception as e:
                # print(f"Failed {domain}: {e}")
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
                    year = str(movie.get("year", ""))
                    
                    for torrent in movie.get("torrents", []):
                        quality = torrent.get("quality", "")
                        type_ = torrent.get("type", "")
                        full_title = f"{title_base} [{quality}] [{type_}]"
                        h = torrent.get("hash")
                        
                        # Add standard trackers to magnet
                        trackers = [
                            "udp://open.demonii.com:1337/announce",
                            "udp://tracker.openbittorrent.com:80",
                            "udp://tracker.coppersurfer.tk:6969",
                            "udp://glotorrents.pw:6969/announce",
                            "udp://tracker.opentrackr.org:1337/announce"
                        ]
                        tr_str = "".join([f"&tr={quote(t)}" for t in trackers])
                        magnet = f"magnet:?xt=urn:btih:{h}&dn={quote(full_title)}{tr_str}"
                        
                        results.append(ListItemData(
                            title=full_title,
                            size=int(torrent.get("size_bytes", 0)),
                            date=year,
                            seeders=torrent.get("seeds", 0),
                            leechers=torrent.get("peers", 0),
                            magnet=magnet,
                            source="YTS"
                        ))
        except Exception as e:
            print(f"YTS Parse Error: {e}")
        return results

    def search_tpb_api(self, query):
        """The Pirate Bay (via apibay)"""
        results = []
        try:
            # apibay is usually stable, but could add mirrors here too
            url = f"https://apibay.org/q.php?q={quote(query)}"
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            
            if data and data[0].get("name") != "No results found":
                for item in data:
                    results.append(ListItemData(
                        title=item.get("name"),
                        size=int(item.get("size", 0)), 
                        date="TPB", # Date formatting varies, keeping simple
                        seeders=int(item.get("seeders", 0)),
                        leechers=int(item.get("leechers", 0)),
                        magnet=f"magnet:?xt=urn:btih:{item.get('info_hash')}&dn={quote(item.get('name'))}",
                        source="TPB API"
                    ))
        except Exception as e:
            print(f"TPB Error: {e}")
        return results

    # --- Utilities ---
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
        # Trigger instant re-filtering
        self.refresh_view()

    def format_bytes(self, size):
        if size == 0: return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
        i = int(math.floor(math.log(size, 1024)))
        p = math.pow(1024, i)
        s = round(size / p, 2)
        return f"{s} {size_name[i]}"

    def parse_size_to_bytes(self, size_str):
        if isinstance(size_str, int): return size_str
        try:
            parts = size_str.strip().split()
            if len(parts) < 2: return 0
            num = float(parts[0])
            unit = parts[1].upper()
            multipliers = {'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4, 'PB': 1024**5}
            return int(num * multipliers.get(unit, 1))
        except:
            return 0

    def open_magnet(self, link):
        subprocess.Popen(["xdg-open", link])

def main():
    app = TorrentSearchApp()
    app.run()

if __name__ == "__main__":
    main()
