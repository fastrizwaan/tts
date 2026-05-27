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
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango

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
    Handles API logic and threading.
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        })
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def search(self, query, providers_config):
        """
        Returns a list of TorrentItem objects.
        providers_config: dict e.g. {"yts": True, "tpb": False}
        """
        results = []
        futures = {}

        # Schedule tasks based on settings
        if providers_config.get("yts", True):
            futures[self.executor.submit(self._search_yts, query)] = "YTS"
        
        if providers_config.get("tpb", True):
            futures[self.executor.submit(self._search_tpb, query)] = "TPB"

        # Collect results
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
                        mpa_rating = m.get('mpa_rating', '').lower()
                        is_adult = mpa_rating in ['nc-17']
                        
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

    def _build_magnet(self, hash_str, title):
        trackers = [
            "udp://open.demonii.com:1337/announce",
            "udp://tracker.openbittorrent.com:80",
            "udp://tracker.opentrackr.org:1337/announce"
        ]
        tr = "".join([f"&tr={quote(t)}" for t in trackers])
        return f"magnet:?xt=urn:btih:{hash_str}&dn={quote(title)}{tr}"

# --- Settings Window ---
class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, app, settings):
        super().__init__()
        self.set_transient_for(app.get_active_window())
        self.set_modal(True)
        self.app = app
        self.settings = settings
        self.providers = settings.get("providers", {})

        # Page 1: General
        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")
        self.add(page)

        # Group 1: Providers
        group_prov = Adw.PreferencesGroup()
        group_prov.set_title("Search Providers")
        group_prov.set_description("Enable or disable specific torrent sources.")
        page.add(group_prov)

        self._add_switch(group_prov, "The Pirate Bay (TPB)", "tpb")
        self._add_switch(group_prov, "YIFY Torrents (YTS)", "yts")

        # Group 2: Content
        group_content = Adw.PreferencesGroup()
        group_content.set_title("Content")
        page.add(group_content)

        # Adult Switch
        row_adult = Adw.ActionRow()
        row_adult.set_title("Enable Adult Content")
        row_adult.set_subtitle("Show results flagged as adult/pornographic.")
        
        switch_adult = Gtk.Switch()
        switch_adult.set_active(settings.get("adult_content", False))
        switch_adult.set_valign(Gtk.Align.CENTER)
        switch_adult.connect("state-set", self._on_adult_toggled)
        
        row_adult.add_suffix(switch_adult)
        group_content.add(row_adult)

    def _add_switch(self, group, title, key):
        row = Adw.ActionRow()
        row.set_title(title)
        
        switch = Gtk.Switch()
        switch.set_active(self.providers.get(key, True))
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("state-set", lambda s, state: self._on_provider_toggled(key, state))
        
        row.add_suffix(switch)
        group.add(row)

    def _on_provider_toggled(self, key, state):
        self.providers[key] = state
        self.settings["providers"] = self.providers
        self.app.save_settings()
        return True

    def _on_adult_toggled(self, switch, state):
        self.settings["adult_content"] = state
        self.app.save_settings()
        
        # Notify main window to refresh filter if open
        win = self.app.get_active_window()
        if win:
            win.apply_filter()
        return True

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
        defaults = {
            "last_search": "", 
            "adult_content": False,
            "providers": {"yts": True, "tpb": True}
        }
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    data = json.load(f)
                    saved_providers = data.get("providers", {})
                    defaults["providers"].update(saved_providers)
                    data["providers"] = defaults["providers"]
                    return {**defaults, **data}
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
        self.current_category = "All"

        # --- Actions ---
        # Preferences Action
        action_pref = Gio.SimpleAction.new("preferences", None)
        action_pref.connect("activate", self.on_open_preferences)
        self.add_action(action_pref)
        
        # Category Filter Action (Stateful)
        action_cat = Gio.SimpleAction.new_stateful(
            "category", 
            GLib.VariantType.new("s"), 
            GLib.Variant.new_string("All")
        )
        action_cat.connect("activate", self.on_category_selected)
        self.add_action(action_cat)

        # --- UI Construction ---
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        self.set_content(self.toast_overlay)

        # HeaderBar
        header = Adw.HeaderBar()
        content.append(header)

        # Title Widget (Search Box)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search movies, games...")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_text(app.app_settings.get("last_search", ""))
        self.search_entry.connect("activate", self.on_search_triggered)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_box.set_spacing(10)
        title_box.append(self.search_entry)
        header.set_title_widget(title_box)

        # --- Filter Menu Button ---
        filter_menu = Gio.Menu()
        filter_menu.append("All", "win.category('All')")
        filter_menu.append("Video", "win.category('Video')")
        filter_menu.append("Audio", "win.category('Audio')")
        filter_menu.append("Applications", "win.category('Apps')")
        filter_menu.append("Games", "win.category('Games')")
        filter_menu.append("Books", "win.category('Books')")
        
        btn_filter = Gtk.MenuButton()
        btn_filter.set_icon_name("content-filter-symbolic")
        btn_filter.set_menu_model(filter_menu)
        btn_filter.set_tooltip_text("Filter Results")
        header.pack_end(btn_filter)

        # --- Main Menu Button ---
        main_menu = Gio.Menu()
        main_menu.append("Preferences", "win.preferences")
        
        btn_menu = Gtk.MenuButton()
        btn_menu.set_icon_name("open-menu-symbolic")
        btn_menu.set_menu_model(main_menu)
        header.pack_end(btn_menu)

        # Refresh Button
        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.set_tooltip_text("Refresh Search")
        btn_refresh.connect("clicked", self.on_search_triggered)
        header.pack_end(btn_refresh)

        # --- View Stack ---
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
        lbl_loading = Gtk.Label(label="Searching enabled providers...")
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

        # Columns
        self._add_column("Name", "title", True, self._setup_title, self._bind_title)
        self._add_column("Size", "size", False, self._setup_label, lambda f, i: i.get_child().set_text(i.get_item().size_str), 100)
        self._add_column("Seeds", "seeders", False, self._setup_label, self._bind_seeds, 80)
        self._add_column("Source", "source", False, self._setup_label, lambda f, i: i.get_child().set_text(i.get_item().source), 80)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.col_view)
        self.stack.add_named(scrolled, "results")

        self.sort_model = Gtk.SortListModel(model=self.store, sorter=self.col_view.get_sorter())
        self.selection.set_model(self.sort_model)
        self.col_view.set_model(self.selection)

        if self.search_entry.get_text():
            self.on_search_triggered(None)

    def _add_column(self, title, prop_name, expand, setup_func, bind_func, width=-1):
        col = Gtk.ColumnViewColumn(title=title)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup_func)
        factory.connect("bind", bind_func)
        col.set_factory(factory)
        if expand: col.set_expand(True)
        if width > 0: col.set_fixed_width(width)
        
        if prop_name == "size":
             sorter = Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, prop_name))
        elif prop_name == "seeders":
             sorter = Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, prop_name))
        else:
             sorter = Gtk.StringSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, prop_name))
        
        col.set_sorter(sorter)
        self.col_view.append_column(col)

    # --- Actions Callbacks ---
    def on_open_preferences(self, action, param):
        pref_win = PreferencesWindow(self.app, self.app.app_settings)
        pref_win.present()

    def on_category_selected(self, action, value):
        action.set_state(value)
        self.current_category = value.get_string()
        self.apply_filter()

    # --- Search Logic ---
    def on_search_triggered(self, widget):
        query = self.search_entry.get_text().strip()
        if not query: return

        self.current_search_token += 1
        search_id = self.current_search_token

        self.spinner.start()
        self.stack.set_visible_child_name("loading")
        self.app.app_settings["last_search"] = query
        self.app.save_settings()

        # Pass current providers config
        providers_config = self.app.app_settings.get("providers", {})
        self.search_manager.executor.submit(self._bg_search, query, search_id, providers_config)

    def _bg_search(self, query, search_id, providers_config):
        results = self.search_manager.search(query, providers_config)
        GLib.idle_add(self._on_search_complete, results, search_id)

    def _on_search_complete(self, results, search_id):
        if search_id != self.current_search_token: return
        self.spinner.stop()
        self.full_results_cache = results
        self.apply_filter()

        if not results:
            self.status_page.set_title("No Results")
            desc = f"No results for '{self.search_entry.get_text()}'."
            if not any(self.app.app_settings.get("providers", {}).values()):
                desc += "\n(All search providers are disabled in Preferences)"
            self.status_page.set_description(desc)
            self.stack.set_visible_child_name("status")
        else:
            self.stack.set_visible_child_name("results")

    # --- Filter Logic ---
    def _is_adult_content(self, item):
        if item.is_adult: return True
        keywords = ["xxx", "porn", "adult", "hentai", "18+", "sex", "erotic", "uncensored"]
        return any(k in item.title.lower() for k in keywords)

    def apply_filter(self):
        cat = self.current_category
        show_adult = self.app.app_settings.get("adult_content", False)
        
        if not self.full_results_cache: return

        filtered = []
        keywords = []
        if cat == "Video": keywords = ["video", "mkv", "mp4", "1080p", "720p"]
        elif cat == "Audio": keywords = ["audio", "mp3", "flac"]
        elif cat == "Apps": keywords = ["exe", "dmg", "apk", "application"]
        elif cat == "Games": keywords = ["repack", "fitgirl", "dodi", "codex"]
        elif cat == "Books": keywords = ["pdf", "epub", "office"]

        for item in self.full_results_cache:
            if not show_adult and self._is_adult_content(item):
                continue

            if cat == "All":
                filtered.append(item)
            else:
                if any(k in item.icon_name for k in keywords) or \
                   (cat == "Games" and any(x in item.title.lower() for x in keywords)):
                    filtered.append(item)

        self.store.remove_all()
        self.store.splice(0, 0, filtered)
        
        if not filtered and self.full_results_cache:
             self.stack.set_visible_child_name("status")
             self.status_page.set_title("Hidden Results")
             self.status_page.set_description("Results hidden by filters.")
        elif filtered:
             self.stack.set_visible_child_name("results")

    # --- Row Factories ---
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

    def _setup_label(self, factory, item):
        lbl = Gtk.Label(xalign=0.5)
        item.set_child(lbl)

    def _bind_seeds(self, factory, item):
        lbl = item.get_child()
        seeds = item.get_item().seeders
        color = "green" if seeds > 20 else "orange" if seeds > 5 else "red"
        lbl.set_markup(f"<span color='{color}' weight='bold'>{seeds}</span>")

    # --- Details Dialog ---
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
        content_box.set_valign(Gtk.Align.CENTER)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        
        lbl_title = Gtk.Label(label=item.title, wrap=True, xalign=0.5, justify=Gtk.Justification.CENTER)
        lbl_title.add_css_class("title-2")
        content_box.append(lbl_title)
        
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_halign(Gtk.Align.CENTER)
        
        self._add_detail_row(grid, 0, "Size", item.size_str)
        self._add_detail_row(grid, 1, "Source", item.source)
        self._add_detail_row(grid, 2, "Status", f"{item.seeders} Seeds / {item.leechers} Leech")
        
        content_box.append(grid)
        
        btn_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        btn_box.set_margin_top(10)
        
        btn_copy = Gtk.Button(label="Copy Magnet", icon_name="edit-copy-symbolic")
        btn_copy.add_css_class("pill")
        btn_copy.connect("clicked", lambda x: (self.get_clipboard().set(item.magnet), self.toast_overlay.add_toast(Adw.Toast.new("Magnet copied!"))))
        
        btn_open = Gtk.Button(label="Open Magnet", icon_name="external-link-symbolic")
        btn_open.add_css_class("suggested-action")
        btn_open.add_css_class("pill")
        btn_open.connect("clicked", lambda x: Gio.AppInfo.launch_default_for_uri(item.magnet, None))
        
        btn_box.append(btn_copy)
        btn_box.append(btn_open)
        content_box.append(btn_box)
        
        outer_box.append(content_box)
        dialog.set_child(outer_box)
        dialog.present(self)

    def _add_detail_row(self, grid, idx, name, val):
        l1 = Gtk.Label(label=f"<b>{name}:</b>", use_markup=True, xalign=1)
        l1.add_css_class("dim-label")
        l2 = Gtk.Label(label=str(val), xalign=0)
        grid.attach(l1, 0, idx, 1, 1)
        grid.attach(l2, 1, idx, 1, 1)

if __name__ == "__main__":
    app = TorrentSearchApp()
    app.run(None)
