import gi
import os
import json
import math
import requests
import urllib3
import concurrent.futures
from urllib.parse import quote

# Suppress insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango, Gdk

# Optional import
try:
    import torrfetch
    TORRFETCH_AVAILABLE = True
except ImportError:
    TORRFETCH_AVAILABLE = False

# --- Data Model ---
class TorrentItem(GObject.Object):
    """
    Data object representing a single row.
    """
    title = GObject.Property(type=str)
    size = GObject.Property(type=GObject.TYPE_INT64)
    size_str = GObject.Property(type=str) # Pre-formatted for display
    date = GObject.Property(type=str)
    seeders = GObject.Property(type=int)
    leechers = GObject.Property(type=int)
    magnet = GObject.Property(type=str)
    source = GObject.Property(type=str)
    icon_name = GObject.Property(type=str)
    is_adult = GObject.Property(type=bool, default=False)

    def __init__(self, title, size, date, seeders, leechers, magnet, source, is_adult=False):
        super().__init__()
        self.title = title
        self.size = size
        self.size_str = self._format_bytes(size)
        self.date = str(date)
        self.seeders = seeders
        self.leechers = leechers
        self.magnet = magnet
        self.source = source
        self.is_adult = is_adult
        self.icon_name = self._determine_icon(title)

    def _format_bytes(self, size):
        if size == 0: return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size, 1024)))
        p = math.pow(1024, i)
        s = round(size / p, 2)
        return f"{s} {size_name[i]}"

    def _determine_icon(self, title):
        t = title.lower()
        if any(x in t for x in ['.mkv', '.mp4', '.avi', '1080p', '720p', 'camrip']):
            return "video-x-generic-symbolic"
        if any(x in t for x in ['.mp3', '.flac', '.wav', 'discography']):
            return "audio-x-generic-symbolic"
        if any(x in t for x in ['.exe', '.msi', '.apk', '.dmg']):
            return "application-x-executable-symbolic"
        if any(x in t for x in ['.zip', '.rar', '.7z', '.tar']):
            return "package-x-generic-symbolic"
        if any(x in t for x in ['.pdf', '.epub', '.mobi']):
            return "x-office-document-symbolic"
        return "text-x-generic-symbolic"

# --- Search Logic Manager ---
class SearchManager:
    """
    Handles API logic and threading, decoupled from UI.
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        })
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def search(self, query):
        """
        Returns a list of TorrentItem objects.
        """
        results = []
        
        futures = {
            self.executor.submit(self._search_yts, query): "YTS",
            self.executor.submit(self._search_tpb, query): "TPB"
        }

        if TORRFETCH_AVAILABLE:
             futures[self.executor.submit(self._search_torrfetch, query)] = "Torrfetch"

        for future in concurrent.futures.as_completed(futures):
            provider_name = futures[future]
            try:
                data = future.result()
                results.extend(data)
            except Exception as e:
                print(f"Provider {provider_name} failed: {e}")

        # Deduplicate
        seen_magnets = set()
        unique_results = []
        for item in results:
            if item.magnet not in seen_magnets:
                unique_results.append(item)
                seen_magnets.add(item.magnet)

        unique_results.sort(key=lambda x: x.seeders, reverse=True)
        return unique_results

    def _search_yts(self, query):
        # YTS is strictly movies, usually safe unless searching for specific unrated content
        items = []
        mirrors = ["yts.mx", "yts.rs", "yts.lt"]
        path = "/api/v2/list_movies.json"
        params = {"query_term": query, "limit": 50, "sort_by": "seeds"}

        for domain in mirrors:
            try:
                resp = self.session.get(f"https://{domain}{path}", params=params, timeout=4, verify=False)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "ok" and data["data"]["movie_count"] > 0:
                    for m in data["data"]["movies"]:
                        # Check MPA rating if available
                        mpa_rating = m.get('mpa_rating', '').lower()
                        is_adult = mpa_rating in ['nc-17'] # rare but possible
                        
                        for t in m.get("torrents", []):
                            title = f"{m.get('title_long')} [{t['quality']}]"
                            magnet = self._build_magnet(t['hash'], title)
                            items.append(TorrentItem(
                                title=title,
                                size=t.get('size_bytes', 0),
                                date=str(m.get('year', '')),
                                seeders=t.get('seeds', 0),
                                leechers=t.get('peers', 0),
                                magnet=magnet,
                                source="YTS",
                                is_adult=is_adult
                            ))
                    return items
            except: continue
        return items

    def _search_tpb(self, query):
        items = []
        try:
            resp = self.session.get(f"https://apibay.org/q.php?q={quote(query)}", timeout=8)
            data = resp.json()
            if data and data[0].get("name") != "No results found":
                for i in data:
                    # TPB CATEGORY LOGIC:
                    # 100=Audio, 200=Video, 300=Apps, 400=Games
                    # 500-599 = Porn
                    category = int(i.get('category', 0))
                    is_adult = 500 <= category < 600

                    magnet = f"magnet:?xt=urn:btih:{i['info_hash']}&dn={quote(i['name'])}"
                    items.append(TorrentItem(
                        title=i['name'],
                        size=int(i['size']),
                        date="TPB",
                        seeders=int(i['seeders']),
                        leechers=int(i['leechers']),
                        magnet=magnet,
                        source="TPB",
                        is_adult=is_adult
                    ))
        except Exception as e:
            print(f"TPB Error: {e}")
        return items

    def _search_torrfetch(self, query):
        items = []
        if not TORRFETCH_AVAILABLE: return items
        try:
            raw_res = torrfetch.search_torrents(query, mode="parallel")
            for r in raw_res:
                try:
                    size_str = r.get("size", "0")
                    size_bytes = self._parse_size(size_str)
                    items.append(TorrentItem(
                        title=r.get("title", "Unknown"),
                        size=size_bytes,
                        date=r.get("uploaded", "?"),
                        seeders=int(r.get("seeders", 0)),
                        leechers=int(r.get("leechers", 0)),
                        magnet=r.get("magnet", ""),
                        source=r.get("source", "TF"),
                        is_adult=False # Torrfetch lacks category ID, handled by fallback keywords
                    ))
                except: continue
        except: pass
        return items

    def _build_magnet(self, hash_str, title):
        trackers = [
            "udp://open.demonii.com:1337/announce",
            "udp://tracker.openbittorrent.com:80",
            "udp://tracker.opentrackr.org:1337/announce"
        ]
        tr = "".join([f"&tr={quote(t)}" for t in trackers])
        return f"magnet:?xt=urn:btih:{hash_str}&dn={quote(title)}{tr}"

    def _parse_size(self, size_str):
        try:
            p = size_str.lower().split()
            num = float(p[0])
            unit = p[1] if len(p) > 1 else ""
            mult = {'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4}
            return int(num * mult.get(unit, 1))
        except: return 0

# --- UI Application ---
class TorrentSearchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.TorrentSearch', flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.settings_file = os.path.expanduser("~/.config/torrent-search.json")
        self.app_settings = self.load_settings()

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            win = MainWindow(self)
        win.present()

    def load_settings(self):
        defaults = {"last_search": "", "theme": "system", "adult_content": False}
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    return {**defaults, **json.load(f)}
        except: pass
        return defaults

    def save_settings(self):
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, 'w') as f:
            json.dump(self.app_settings, f)

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_default_size(1000, 700)
        self.set_title("Torrent Search")
        
        self.search_manager = SearchManager()
        self.current_search_token = 0 
        self.full_results_cache = []

        # --- Actions Setup (for Menu) ---
        is_adult_enabled = self.app.app_settings.get("adult_content", False)
        
        action_adult = Gio.SimpleAction.new_stateful(
            "toggle-adult", 
            None, 
            GLib.Variant.new_boolean(is_adult_enabled)
        )
        action_adult.connect("change-state", self.on_toggle_adult)
        self.add_action(action_adult)

        # --- UI Construction ---
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        self.set_content(self.toast_overlay)

        # HeaderBar
        header = Adw.HeaderBar()
        content.append(header)

        # Title Widget (Search Box)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search movies, games, audio...")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_text(app.app_settings.get("last_search", ""))
        self.search_entry.connect("activate", self.on_search_triggered)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_box.set_spacing(10)
        title_box.append(self.search_entry)
        header.set_title_widget(title_box)

        # --- Menu Button Setup ---
        menu = Gio.Menu()
        menu.append("Enable Adult Content", "win.toggle-adult")
        
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text("Menu")
        header.pack_end(menu_btn)

        # Refresh Button
        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.set_tooltip_text("Refresh Search")
        btn_refresh.connect("clicked", self.on_search_triggered)
        header.pack_end(btn_refresh)

        # Category Filter
        filter_model = Gtk.StringList.new(["All", "Video", "Audio", "Apps", "Games", "Books"])
        self.filter_dropdown = Gtk.DropDown(model=filter_model)
        self.filter_dropdown.connect("notify::selected-item", self.on_filter_changed)
        header.pack_end(self.filter_dropdown)

        # --- View Stack (Pages) ---
        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        content.append(self.stack)

        # Page 1: Status
        self.status_page = Adw.StatusPage()
        self.status_page.set_icon_name("system-search-symbolic")
        self.status_page.set_title("Ready to Search")
        self.status_page.set_description("Enter a query above to begin.")
        self.stack.add_named(self.status_page, "status")

        # Page 2: Loading
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_halign(Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        lbl_loading = Gtk.Label(label="Searching providers...")
        lbl_loading.add_css_class("title-4")
        spinner_box.append(self.spinner)
        spinner_box.append(lbl_loading)
        self.stack.add_named(spinner_box, "loading")

        # Page 3: Results
        self.store = Gio.ListStore(item_type=TorrentItem)
        self.selection = Gtk.SingleSelection(model=None)
        
        self.col_view = Gtk.ColumnView()
        self.col_view.add_css_class("data-table")
        self.col_view.connect("activate", self.on_row_activated)

        # -- Columns --
        col_title = Gtk.ColumnViewColumn(title="Name")
        f_title = Gtk.SignalListItemFactory()
        f_title.connect("setup", self._setup_title)
        f_title.connect("bind", self._bind_title)
        col_title.set_factory(f_title)
        col_title.set_expand(True)
        col_title.set_sorter(Gtk.StringSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, "title")))
        self.col_view.append_column(col_title)

        col_size = Gtk.ColumnViewColumn(title="Size")
        f_size = Gtk.SignalListItemFactory()
        f_size.connect("setup", self._setup_label_center)
        f_size.connect("bind", lambda f, i: i.get_child().set_text(i.get_item().size_str))
        col_size.set_factory(f_size)
        col_size.set_fixed_width(100)
        col_size.set_sorter(Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, "size")))
        self.col_view.append_column(col_size)

        col_seeds = Gtk.ColumnViewColumn(title="Seeds")
        f_seeds = Gtk.SignalListItemFactory()
        f_seeds.connect("setup", self._setup_label_center)
        f_seeds.connect("bind", self._bind_seeds)
        col_seeds.set_factory(f_seeds)
        col_seeds.set_fixed_width(80)
        col_seeds.set_sorter(Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, "seeders")))
        self.col_view.append_column(col_seeds)

        col_src = Gtk.ColumnViewColumn(title="Source")
        f_src = Gtk.SignalListItemFactory()
        f_src.connect("setup", self._setup_label_center)
        f_src.connect("bind", lambda f, i: i.get_child().set_text(i.get_item().source))
        col_src.set_factory(f_src)
        col_src.set_fixed_width(80)
        self.col_view.append_column(col_src)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.col_view)
        self.stack.add_named(scrolled, "results")

        self.sort_model = Gtk.SortListModel(model=self.store, sorter=self.col_view.get_sorter())
        self.selection.set_model(self.sort_model)
        self.col_view.set_model(self.selection)

        if self.search_entry.get_text():
            self.on_search_triggered(None)

    # --- Action Callback ---
    def on_toggle_adult(self, action, value):
        action.set_state(value)
        enabled = value.get_boolean()
        self.app.app_settings["adult_content"] = enabled
        self.app.save_settings()
        
        self.apply_filter()
        
        status = "enabled" if enabled else "disabled"
        self.toast_overlay.add_toast(Adw.Toast.new(f"Adult content {status}"))

    # --- Factory Setup/Bind ---
    def _setup_title(self, factory, item):
        box = Gtk.Box(spacing=12)
        img = Gtk.Image(icon_name="text-x-generic-symbolic")
        lbl = Gtk.Label(xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(img)
        box.append(lbl)
        item.set_child(box)

    def _bind_title(self, factory, item):
        box = item.get_child()
        obj = item.get_item()
        box.get_first_child().set_from_icon_name(obj.icon_name)
        box.get_last_child().set_text(obj.title)
        box.set_tooltip_text(obj.title)

    def _setup_label_center(self, factory, item):
        lbl = Gtk.Label(xalign=0.5)
        item.set_child(lbl)

    def _bind_seeds(self, factory, item):
        lbl = item.get_child()
        seeds = item.get_item().seeders
        color = "green" if seeds > 20 else "orange" if seeds > 5 else "red"
        lbl.set_markup(f"<span color='{color}' weight='bold'>{seeds}</span>")

    # --- Logic ---
    def on_search_triggered(self, widget):
        query = self.search_entry.get_text().strip()
        if not query: return

        self.current_search_token += 1
        search_id = self.current_search_token

        self.spinner.start()
        self.stack.set_visible_child_name("loading")
        self.app.app_settings["last_search"] = query
        self.app.save_settings()

        self.search_manager.executor.submit(self._bg_search, query, search_id)

    def _bg_search(self, query, search_id):
        results = self.search_manager.search(query)
        GLib.idle_add(self._on_search_complete, results, search_id)

    def _on_search_complete(self, results, search_id):
        if search_id != self.current_search_token:
            return

        self.spinner.stop()
        self.full_results_cache = results
        self.apply_filter()

        if not results:
            self.status_page.set_title("No Results")
            self.status_page.set_description(f"Could not find anything for '{self.search_entry.get_text()}'")
            self.status_page.set_icon_name("edit-find-symbolic")
            self.stack.set_visible_child_name("status")
        else:
            self.stack.set_visible_child_name("results")

    def on_filter_changed(self, dropdown, _):
        self.apply_filter()

    def _is_adult_content(self, item):
        # 1. Check if the source specifically tagged it as adult (e.g., TPB Category 500)
        if item.is_adult:
            return True

        # 2. Fallback: Extensive keyword blocking
        # These are commonly used in titles that slip past category filters
        adult_keywords = [
            "xxx", "porn", "adult", "hentai", "18+", "sex", "erotic", "nude",
            "uncensored", "blowjob", "creampie", "milf", "threesome", "gangbang",
            "playboy", "brazzers", "realitykings", "mofos", "bangbros", "naughty",
            "nsfw", "leaked", "onlyfans", "amateur", "deepthroat", "anal"
        ]
        
        title_lower = item.title.lower()
        
        # Check title
        if any(k in title_lower for k in adult_keywords):
            return True
            
        return False

    def apply_filter(self):
        selected_item = self.filter_dropdown.get_selected_item()
        cat = selected_item.get_string() if selected_item else "All"
        
        # Check adult setting
        show_adult = self.app.app_settings.get("adult_content", False)

        if not self.full_results_cache: return

        filtered = []
        
        # Map Category string to keywords/extensions
        keywords = []
        if cat == "Video": keywords = ["video", "mkv", "mp4", "1080p", "720p"]
        elif cat == "Audio": keywords = ["audio", "mp3", "flac"]
        elif cat == "Apps": keywords = ["exe", "dmg", "apk", "application"]
        elif cat == "Games": keywords = ["repack", "fitgirl", "dodi", "codex"]
        elif cat == "Books": keywords = ["pdf", "epub", "office"]

        for item in self.full_results_cache:
            # 1. Adult Filter check
            if not show_adult and self._is_adult_content(item):
                continue

            # 2. Category Filter check
            if cat == "All":
                filtered.append(item)
            else:
                if any(k in item.icon_name for k in keywords) or \
                   (cat == "Games" and any(x in item.title.lower() for x in keywords)):
                    filtered.append(item)

        self.store.remove_all()
        self.store.splice(0, 0, filtered)
        
        # If we filtered everything out, show empty state
        if not filtered and self.full_results_cache:
             self.stack.set_visible_child_name("status")
             self.status_page.set_title("Hidden Results")
             self.status_page.set_description("Results were found but hidden by filters (Category or Adult settings).")
        elif filtered:
             self.stack.set_visible_child_name("results")

    # --- Row Activation (Details) ---
    def on_row_activated(self, view, pos):
        item = self.selection.get_item(pos)
        self.show_details(item)

    def show_details(self, item):
        dialog = Adw.Dialog()
        dialog.set_content_width(450)
        dialog.set_content_height(350)
        
        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        headerbar = Adw.HeaderBar()
        headerbar.set_show_title(False)
        headerbar.add_css_class("flat")
        outer_box.append(headerbar)
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_vexpand(True) 
        
        lbl_title = Gtk.Label(
            label=item.title, 
            wrap=True, 
            xalign=0.5, 
            justify=Gtk.Justification.CENTER
        )
        lbl_title.add_css_class("title-2")
        content_box.append(lbl_title)
        
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_halign(Gtk.Align.CENTER)
        
        def add_row(idx, name, val):
            l1 = Gtk.Label(label=f"<b>{name}:</b>", use_markup=True, xalign=1)
            l1.add_css_class("dim-label")
            l2 = Gtk.Label(label=str(val), xalign=0)
            grid.attach(l1, 0, idx, 1, 1)
            grid.attach(l2, 1, idx, 1, 1)
        
        add_row(0, "Size", item.size_str)
        add_row(1, "Source", item.source)
        add_row(2, "Peers", f"{item.seeders} Seeds / {item.leechers} Leech")
        
        content_box.append(grid)
        
        btn_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        btn_box.set_margin_top(10)
        
        btn_copy = Gtk.Button(label="Copy Magnet", icon_name="edit-copy-symbolic")
        btn_copy.add_css_class("pill")
        btn_copy.connect("clicked", lambda x: self.copy_magnet(item.magnet))
        
        btn_open = Gtk.Button(label="Open Magnet", icon_name="external-link-symbolic")
        btn_open.add_css_class("suggested-action")
        btn_open.add_css_class("pill")
        btn_open.connect("clicked", lambda x: self.open_magnet(item.magnet))
        
        btn_box.append(btn_copy)
        btn_box.append(btn_open)
        content_box.append(btn_box)
        
        outer_box.append(content_box)
        
        window_handle = Gtk.WindowHandle()
        window_handle.set_child(outer_box)
        
        dialog.set_child(window_handle)
        dialog.present(self)

    def copy_magnet(self, magnet):
        self.get_clipboard().set(magnet)
        self.toast_overlay.add_toast(Adw.Toast.new("Magnet link copied!"))

    def open_magnet(self, magnet):
        Gio.AppInfo.launch_default_for_uri(magnet, None)

if __name__ == "__main__":
    app = TorrentSearchApp()
    app.run(None)
