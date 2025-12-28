import gi
import os
import json
import math
import requests
import concurrent.futures
from urllib.parse import quote
from bs4 import BeautifulSoup
import urllib3
import re

# Suppress insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango, Gdk

# --- Data Model ---
class TorrentItem(GObject.Object):
    title = GObject.Property(type=str)
    size = GObject.Property(type=GObject.TYPE_INT64)
    size_str = GObject.Property(type=str)
    seeders = GObject.Property(type=int)
    leechers = GObject.Property(type=int)
    magnet = GObject.Property(type=str)
    source = GObject.Property(type=str)
    icon_name = GObject.Property(type=str)

    def __init__(self, title, size, seeders, leechers, magnet, source):
        super().__init__()
        self.title = title
        self.size = size
        self.size_str = self._format_bytes(size)
        self.seeders = seeders
        self.leechers = leechers
        self.magnet = magnet
        self.source = source
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
        if any(x in t for x in ['.mkv', '.mp4', '.avi', '1080p', '720p']): return "video-x-generic-symbolic"
        if any(x in t for x in ['.mp3', '.flac', '.wav']): return "audio-x-generic-symbolic"
        if any(x in t for x in ['.exe', '.msi', '.apk']): return "application-x-executable-symbolic"
        if any(x in t for x in ['.pdf', '.epub']): return "x-office-document-symbolic"
        return "text-x-generic-symbolic"

# --- Search Manager ---
class SearchManager:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://www.google.com/'
        })
        # Increased workers to handle page visiting
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    def search(self, query):
        results = []
        
        # We search LimeTorrent (Primary) and YTS (Backup)
        futures = {
            self.executor.submit(self._search_limetorrent, query): "LimeTorrent",
            self.executor.submit(self._search_yts, query): "YTS"
        }

        for future in concurrent.futures.as_completed(futures):
            try:
                data = future.result()
                if data: results.extend(data)
            except Exception: pass

        # Deduplicate
        seen_magnets = set()
        unique_results = []
        for item in results:
            if item.magnet and item.magnet not in seen_magnets:
                unique_results.append(item)
                seen_magnets.add(item.magnet)

        unique_results.sort(key=lambda x: x.seeders, reverse=True)
        return unique_results

    def _search_limetorrent(self, query):
        """
        1. Hits https://limetorrent.in/get-posts/keywords:{query}/
        2. Scrapes list of results.
        3. Visits each result page in parallel to get the Magnet.
        """
        items = []
        url = f"https://limetorrent.in/get-posts/keywords:{quote(query)}/"
        
        try:
            resp = self.session.get(url, timeout=10, verify=False)
            if resp.status_code != 200: return items

            soup = BeautifulSoup(resp.content, 'html.parser')
            rows = soup.select("table tr")
            
            # Temporary list to store basic info before fetching magnets
            temp_items = []
            
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5: continue

                try:
                    # Title & Link
                    title_col = cols[0]
                    links = title_col.select("a")
                    if not links: continue
                    
                    title_link = max(links, key=lambda l: len(l.get_text(strip=True)))
                    title = title_link.get_text(strip=True)
                    href = title_link['href']
                    
                    # Ensure full URL
                    if href.startswith("/"):
                        detail_url = f"https://limetorrent.in{href}"
                    else:
                        detail_url = href

                    # Stats
                    size_text = cols[2].get_text(strip=True)
                    size = self._parse_size_str(size_text)
                    seeds = int(cols[3].get_text(strip=True))
                    leech = int(cols[4].get_text(strip=True))
                    
                    temp_items.append({
                        'title': title,
                        'size': size,
                        'seeds': seeds,
                        'leech': leech,
                        'url': detail_url
                    })
                except: continue

            # Now, fetch Magnets in Parallel
            # This makes it feel like an API because it's fast
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                # Map future to item
                future_to_item = {executor.submit(self._fetch_lime_magnet, item['url']): item for item in temp_items}
                
                for future in concurrent.futures.as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        magnet = future.result()
                        if magnet:
                            items.append(TorrentItem(
                                item['title'], 
                                item['size'], 
                                item['seeds'], 
                                item['leech'], 
                                magnet, 
                                "LimeTorrent"
                            ))
                    except: pass
                    
        except Exception as e:
            print(f"LimeTorrent Error: {e}")
            
        return items

    def _fetch_lime_magnet(self, url):
        """
        Helper to visit the detail page and find the magnet link
        """
        try:
            resp = self.session.get(url, timeout=5, verify=False)
            if resp.status_code != 200: return None
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # 1. Look for direct magnet link
            magnet_tag = soup.select_one("a[href^='magnet:']")
            if magnet_tag: return magnet_tag['href']
            
            # 2. Look for hash in textual info or other links
            # Sometimes it's in a text field or cache link
            # Regex scan the whole HTML for a magnet pattern as a fallback
            match = re.search(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+', str(resp.content))
            if match:
                # We need the full link, but if we just get the start, we might be stuck.
                # Usually BeautifulSoup finds the 'a' tag.
                pass
                
        except: pass
        return None

    def _search_yts(self, query):
        items = []
        try:
            url = "https://yts.mx/api/v2/list_movies.json"
            params = {"query_term": query, "limit": 40}
            resp = self.session.get(url, params=params, timeout=5, verify=False)
            data = resp.json()
            if data['status'] == 'ok' and data['data']['movie_count'] > 0:
                for m in data['data']['movies']:
                    for t in m.get('torrents', []):
                        title = f"{m['title_long']} [{t['quality']}]"
                        magnet = f"magnet:?xt=urn:btih:{t['hash']}&dn={quote(title)}"
                        items.append(TorrentItem(title, t['size_bytes'], t['seeds'], t['peers'], magnet, "YTS"))
        except: pass
        return items

    def _parse_size_str(self, size_str):
        try:
            parts = size_str.replace(u'\xa0', ' ').strip().split()
            if len(parts) < 2: return 0
            num = float(parts[0])
            unit = parts[1].lower()
            multipliers = {
                'kb': 1024, 'kib': 1024,
                'mb': 1024**2, 'mib': 1024**2,
                'gb': 1024**3, 'gib': 1024**3,
                'tb': 1024**4, 'tib': 1024**4
            }
            return int(num * multipliers.get(unit, 1))
        except: return 0

# --- UI Application (Standard) ---
class TorrentSearchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.TorrentSearch', flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.settings_file = os.path.expanduser("~/.config/torrent-search.json")
        self.app_settings = self.load_settings()

    def do_activate(self):
        win = self.get_active_window()
        if not win: win = MainWindow(self)
        win.present()

    def load_settings(self):
        defaults = {"last_search": ""}
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f: return {**defaults, **json.load(f)}
        except: pass
        return defaults

    def save_settings(self):
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, 'w') as f: json.dump(self.app_settings, f)

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_default_size(1000, 700)
        self.set_title("Torrent Search")
        
        self.search_manager = SearchManager()
        self.current_search_token = 0 
        self.full_results_cache = []

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        self.set_content(self.toast_overlay)

        header = Adw.HeaderBar()
        content.append(header)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search LimeTorrent...")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_text(app.app_settings.get("last_search", ""))
        self.search_entry.connect("activate", self.on_search_triggered)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box.append(self.search_entry)
        header.set_title_widget(title_box)

        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.connect("clicked", self.on_search_triggered)
        header.pack_end(btn_refresh)

        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        content.append(self.stack)

        self.status_page = Adw.StatusPage(
            icon_name="system-search-symbolic", 
            title="Ready", 
            description="Target: limetorrent.in"
        )
        self.stack.add_named(self.status_page, "status")

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        spinner_box.append(self.spinner)
        spinner_box.append(Gtk.Label(label="Fetching Magnets..."))
        self.stack.add_named(spinner_box, "loading")

        self.store = Gio.ListStore(item_type=TorrentItem)
        self.selection = Gtk.SingleSelection(model=None)
        self.col_view = Gtk.ColumnView()
        self.col_view.add_css_class("data-table")
        self.col_view.connect("activate", self.on_row_activated)

        self.add_col("Name", self._setup_title, self._bind_title, expand=True)
        self.add_col("Size", self._setup_label, lambda f, i: i.get_child().set_text(i.get_item().size_str))
        self.add_col("Seeds", self._setup_label, self._bind_seeds)
        self.add_col("Source", self._setup_label, lambda f, i: i.get_child().set_text(i.get_item().source))

        scrolled = Gtk.ScrolledWindow(child=self.col_view)
        self.stack.add_named(scrolled, "results")

        self.sort_model = Gtk.SortListModel(model=self.store, sorter=self.col_view.get_sorter())
        self.selection.set_model(self.sort_model)
        self.col_view.set_model(self.selection)

        if self.search_entry.get_text(): self.on_search_triggered(None)

    def add_col(self, title, setup, bind, expand=False):
        col = Gtk.ColumnViewColumn(title=title)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        col.set_factory(factory)
        if expand: col.set_expand(True)
        else: col.set_fixed_width(100)
        
        prop_map = {"Name": "title", "Size": "size", "Seeds": "seeders", "Source": "source"}
        if title in prop_map:
            if title in ["Size", "Seeds"]: sorter = Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, prop_map[title]))
            else: sorter = Gtk.StringSorter(expression=Gtk.PropertyExpression.new(TorrentItem, None, prop_map[title]))
            if title == "Seeds": sorter.set_sort_order(Gtk.SortType.DESCENDING)
            col.set_sorter(sorter)
        
        self.col_view.append_column(col)

    def _setup_title(self, f, i):
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Image(icon_name="text-x-generic-symbolic"))
        lbl = Gtk.Label(xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)
        i.set_child(box)

    def _bind_title(self, f, i):
        b = i.get_child()
        obj = i.get_item()
        b.get_first_child().set_from_icon_name(obj.icon_name)
        b.get_last_child().set_text(obj.title)
        b.set_tooltip_text(obj.title)

    def _setup_label(self, f, i): i.set_child(Gtk.Label(xalign=0.5))
    
    def _bind_seeds(self, f, i):
        s = i.get_item().seeders
        c = "green" if s > 20 else "orange" if s > 5 else "red"
        i.get_child().set_markup(f"<span color='{c}' weight='bold'>{s}</span>")

    def on_search_triggered(self, w):
        q = self.search_entry.get_text().strip()
        if not q: return
        self.current_search_token += 1
        self.spinner.start()
        self.stack.set_visible_child_name("loading")
        self.app.app_settings["last_search"] = q
        self.app.save_settings()
        self.search_manager.executor.submit(self._bg_search, q, self.current_search_token)

    def _bg_search(self, q, token):
        res = self.search_manager.search(q)
        GLib.idle_add(self._on_search_complete, res, token)

    def _on_search_complete(self, res, token):
        if token != self.current_search_token: return
        self.spinner.stop()
        self.full_results_cache = res
        self.store.remove_all()
        self.store.splice(0, 0, res)
        if not res:
            self.status_page.set_title("No Results")
            self.status_page.set_description("Could not find results on limetorrent.in")
            self.stack.set_visible_child_name("status")
        else:
            self.stack.set_visible_child_name("results")

    def on_row_activated(self, v, pos):
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
        btn_close = Gtk.Button(icon_name="window-close-symbolic")
        btn_close.add_css_class("circular")
        btn_close.add_css_class("flat")
        btn_close.connect("clicked", lambda x: dialog.close())
        headerbar.pack_end(btn_close)
        outer_box.append(headerbar)
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(10)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_vexpand(True)

        lbl = Gtk.Label(label=item.title, wrap=True, xalign=0.5, justify=Gtk.Justification.CENTER)
        lbl.add_css_class("title-2")
        content.append(lbl)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8, halign=Gtk.Align.CENTER)
        def add_row(idx, n, v):
            grid.attach(Gtk.Label(label=f"<b>{n}:</b>", use_markup=True, xalign=1, css_classes=["dim-label"]), 0, idx, 1, 1)
            grid.attach(Gtk.Label(label=str(v), xalign=0), 1, idx, 1, 1)
        
        add_row(0, "Size", item.size_str)
        add_row(1, "Source", item.source)
        add_row(2, "Peers", f"{item.seeders} Seeds / {item.leechers} Leech")
        content.append(grid)

        btns = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=10)
        b1 = Gtk.Button(label="Copy Magnet", icon_name="edit-copy-symbolic", css_classes=["pill"])
        b1.connect("clicked", lambda x: (self.get_clipboard().set(item.magnet), self.toast_overlay.add_toast(Adw.Toast.new("Copied!"))))
        b2 = Gtk.Button(label="Open Magnet", icon_name="external-link-symbolic", css_classes=["suggested-action", "pill"])
        b2.connect("clicked", lambda x: Gio.AppInfo.launch_default_for_uri(item.magnet, None))
        
        btns.append(b1)
        btns.append(b2)
        content.append(btns)
        outer_box.append(content)

        handle = Gtk.WindowHandle()
        handle.set_child(outer_box)
        dialog.set_child(handle)
        dialog.present(self)

if __name__ == "__main__":
    app = TorrentSearchApp()
    app.run(None)
