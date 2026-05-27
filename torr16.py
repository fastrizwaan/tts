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
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def search(self, query):
        results = []
        # We try MirrorBay (Target) and YTS (Backup)
        futures = {
            self.executor.submit(self._search_mirrorbay, query): "MirrorBay",
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

    def _search_mirrorbay(self, query):
        items = []
        
        # 1. URL STRATEGY: 
        # The 'keywords' URL often hides seed columns. 
        # The '99' URL (sort by seeds) usually forces them to appear.
        urls_to_try = [
            f"https://mirrorbay.org/search/{quote(query)}/1/99/0", # Standard (Has Seeds)
            f"https://mirrorbay.org/search/keywords:{quote(query)}/" # Condensed (No Seeds)
        ]

        for url in urls_to_try:
            try:
                resp = self.session.get(url, timeout=10, verify=False)
                if resp.status_code != 200: continue

                soup = BeautifulSoup(resp.content, 'html.parser')
                rows = soup.select("tr")
                
                valid_items_found = False
                
                for row in rows:
                    cols = row.select("td")
                    if len(cols) < 2: continue

                    # --- PARSING LOGIC FOR BOTH LAYOUTS ---
                    
                    # 1. TITLE & MAGNET (Common to both)
                    # Try to find title in Col 0 (Condensed) or Col 1 (Standard)
                    title_tag = row.select_one("div.detName a") or \
                                row.select_one("a.detLink") or \
                                cols[0].select_one("a") # Fallback for condensed col 0

                    if not title_tag: continue
                    
                    # Skip if the link is just "Category" or "Sort"
                    href = title_tag.get('href', '')
                    if "browse" in href or "order" in href: continue

                    title = title_tag.get_text(strip=True)
                    
                    magnet_tag = row.select_one("a[href^='magnet:']")
                    if not magnet_tag: continue
                    magnet = magnet_tag['href']

                    # 2. SEEDS / LEECH (The tricky part)
                    seeds = 0
                    leech = 0
                    size = 0

                    # Detect Layout based on column count
                    # Standard View: ~8 columns (Type, Name, Uploaded, Icons, Size, SE, LE, User)
                    # Condensed View: ~5 columns (Name, Uploaded, Icons, Size, User) [NO SEEDS]
                    
                    if len(cols) >= 6:
                        # STANDARD VIEW (Has Seeds)
                        # Seeds usually at -2, Leech at -1
                        try:
                            s_txt = cols[-2].get_text(strip=True)
                            l_txt = cols[-1].get_text(strip=True)
                            if s_txt.isdigit(): seeds = int(s_txt)
                            if l_txt.isdigit(): leech = int(l_txt)
                        except: pass
                        
                        # Size is usually Col 4
                        size_tag = row.select_one("font.detDesc")
                        if size_tag:
                             size = self._parse_desc_size(size_tag.get_text(strip=True))
                        else:
                             # Try parsing raw text from col 4
                             size = self._parse_size_str(cols[4].get_text(strip=True))

                    else:
                        # CONDENSED VIEW (No Seeds)
                        # We found a valid item, but it has no seeds.
                        # We save it with 0 seeds.
                        # Size is usually Col 3
                        if len(cols) >= 4:
                            size = self._parse_size_str(cols[3].get_text(strip=True))

                    items.append(TorrentItem(title, size, seeds, leech, magnet, "MirrorBay"))
                    valid_items_found = True

                # If we found valid items using the Standard URL (which has seeds), 
                # we stop here. We don't need to try the condensed URL.
                if valid_items_found and "99" in url:
                    return items
                    
            except Exception: continue

        return items

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

    def _parse_desc_size(self, text):
        # "Uploaded 05-23, Size 1.34 GiB, ULed by..."
        try:
            import re
            match = re.search(r'Size\s+([\d\.]+)\s+([A-Za-z]+)', text)
            if match:
                num = float(match.group(1))
                unit = match.group(2)
                return self._parse_size_str(f"{num} {unit}")
        except: pass
        return 0

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
class goodSearchManager:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://www.google.com/'
        })
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def search(self, query):
        results = []
        # YTS is failing with SSL errors (common in India), so we focus on MirrorBay
        futures = {
            self.executor.submit(self._search_mirrorbay, query): "MirrorBay",
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

    def _search_mirrorbay(self, query):
        items = []
        url = f"https://mirrorbay.org/search/keywords:{quote(query)}/"
        
        try:
            resp = self.session.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                url = f"https://mirrorbay.org/search/{quote(query)}/1/99/0"
                resp = self.session.get(url, timeout=10, verify=False)

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'html.parser')
                rows = soup.select("tr")
                
                for row in rows:
                    cols = row.select("td")
                    # Based on your logs, valid rows have exactly 5 columns
                    if len(cols) < 5: continue

                    try:
                        # --- 1. TITLE (Column 0) ---
                        # In your logs, "Linux Coding Tricks..." is in Col 0
                        # We grab the first anchor tag that isn't a category link
                        title_col = cols[0]
                        
                        # MirrorBay titles usually have a class or are just the second link
                        # Strategy: Grab all links, pick the one with the longest text
                        links = title_col.select("a")
                        title = "Unknown"
                        
                        if len(links) == 1:
                            title = links[0].get_text(strip=True)
                        elif len(links) > 1:
                            # Usually the category is first (short), title is second (long)
                            # We pick the longest string to be safe
                            title = max([l.get_text(strip=True) for l in links], key=len)
                        
                        # --- 2. MAGNET (Column 2) ---
                        # Your logs show Col 2 is empty text, meaning it's an Icon link
                        magnet_col = cols[2]
                        magnet_tag = magnet_col.select_one("a[href^='magnet:']")
                        if not magnet_tag: continue # No magnet = useless result
                        magnet = magnet_tag['href']

                        # --- 3. SIZE (Column 3) ---
                        # Your logs show "16.6 MB" in Col 3
                        size_text = cols[3].get_text(strip=True)
                        size = self._parse_size_str(size_text)

                        # --- 4. SEEDS/LEECH ---
                        # Your logs do NOT show a seeds column (only 5 cols total).
                        # This table view might be "Simple View".
                        # We will set seeds to 1 so it isn't sorted to the bottom.
                        seeds = 1 
                        leech = 0

                        items.append(TorrentItem(title, size, seeds, leech, magnet, "MirrorBay"))
                    except: continue
                    
        except Exception as e:
            print(f"MirrorBay Error: {e}")
            
        return items

    def _parse_size_str(self, size_str):
        try:
            # "16.6 MB" -> 16.6, MB
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
class SxearchManager:
    def __init__(self):
        self.session = requests.Session()
        # Mimic a real Chrome browser to bypass basic anti-bot screens
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://www.google.com/'
        })
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def search(self, query):
        results = []
        
        # We try MirrorBay (with your specific URL) and YTS as a backup for movies
        futures = {
            self.executor.submit(self._search_mirrorbay, query): "MirrorBay",
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

    def _search_mirrorbay(self, query):
        """
        Scrapes https://mirrorbay.org/search/keywords:QUERY/
        """
        items = []
        # 1. Use the specific URL format you requested
        url = f"https://mirrorbay.org/search/keywords:{quote(query)}/"
        
        try:
            # print(f"Fetching {url}...") 
            resp = self.session.get(url, timeout=10, verify=False)
            
            # If 404/Redirect, try the standard path just in case
            if resp.status_code != 200:
                url_fallback = f"https://mirrorbay.org/search/{quote(query)}/1/99/0"
                resp = self.session.get(url_fallback, timeout=10, verify=False)

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'html.parser')
                
                # MirrorBay is a TPB clone, usually has table#searchResult
                rows = soup.select("tr")
                
                for row in rows:
                    # Robust Magnet Finding: Look for magnet link anywhere in the row
                    magnet_tag = row.select_one("a[href^='magnet:']")
                    if not magnet_tag: continue
                    magnet = magnet_tag['href']
                    
                    # Title Finding: usually class 'detLink' or 'detName'
                    title_tag = row.select_one("a.detLink") or row.select_one(".detName a")
                    # Fallback: Find the first link that ISN'T the magnet link
                    if not title_tag:
                        links = row.select("a")
                        for l in links:
                            if l['href'] and not l['href'].startswith("magnet:") and not l['href'].startswith("/user"):
                                title_tag = l
                                break
                    
                    if not title_tag: continue
                    title = title_tag.get_text(strip=True)
                    
                    # Size Parsing
                    size = 0
                    desc_tag = row.select_one("font.detDesc")
                    if desc_tag:
                        # Text looks like: "Uploaded 02-28, Size 2.99 GiB, ULed by..."
                        desc_text = desc_tag.get_text(strip=True)
                        size_match = re.search(r'Size\s+([\d\.]+)\s+([A-Za-z]+)', desc_text)
                        if size_match:
                            num = float(size_match.group(1))
                            unit = size_match.group(2).lower()
                            mult = {'gib': 1024**3, 'mib': 1024**2, 'kib': 1024, 'gb': 1024**3, 'mb': 1024**2}
                            size = int(num * mult.get(unit, 1))

                    # Seeds/Leech Parsing (Usually the last two columns)
                    seeds = 0
                    leech = 0
                    cols = row.select("td")
                    if len(cols) >= 2:
                        try:
                            # Try the last column and second to last
                            # Clean string to remove non-digits just in case
                            s_txt = cols[-2].get_text(strip=True)
                            l_txt = cols[-1].get_text(strip=True)
                            if s_txt.isdigit(): seeds = int(s_txt)
                            if l_txt.isdigit(): leech = int(l_txt)
                        except: pass

                    items.append(TorrentItem(title, size, seeds, leech, magnet, "MirrorBay"))
                    
        except Exception as e:
            print(f"MirrorBay Error: {e}")
            
        return items

    def _search_yts(self, query):
        # YTS Backup (Fast API)
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


# --- UI Application (Working GTK4 Code) ---
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

        # UI Setup
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        self.set_content(self.toast_overlay)

        # Header
        header = Adw.HeaderBar()
        content.append(header)

        # Search
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search MirrorBay...")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_text(app.app_settings.get("last_search", ""))
        self.search_entry.connect("activate", self.on_search_triggered)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box.append(self.search_entry)
        header.set_title_widget(title_box)

        # Refresh
        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.connect("clicked", self.on_search_triggered)
        header.pack_end(btn_refresh)

        # ViewStack
        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        content.append(self.stack)

        # Pages
        self.status_page = Adw.StatusPage(
            icon_name="system-search-symbolic", 
            title="Ready", 
            description="Target: https://mirrorbay.org/search/keywords:..."
        )
        self.stack.add_named(self.status_page, "status")

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        spinner_box.append(self.spinner)
        spinner_box.append(Gtk.Label(label="Searching MirrorBay..."))
        self.stack.add_named(spinner_box, "loading")

        # Results List
        self.store = Gio.ListStore(item_type=TorrentItem)
        self.selection = Gtk.SingleSelection(model=None)
        self.col_view = Gtk.ColumnView()
        self.col_view.add_css_class("data-table")
        self.col_view.connect("activate", self.on_row_activated)

        # Columns
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
        
        # Sorter mapping
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
            self.status_page.set_description("Could not find results on mirrorbay.org")
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
        
        # Header (Flat with X button)
        headerbar = Adw.HeaderBar()
        headerbar.set_show_title(False)
        headerbar.add_css_class("flat")
        btn_close = Gtk.Button(icon_name="window-close-symbolic")
        btn_close.add_css_class("circular")
        btn_close.add_css_class("flat")
        btn_close.connect("clicked", lambda x: dialog.close())
        headerbar.pack_end(btn_close)
        outer_box.append(headerbar)
        
        # Content
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
