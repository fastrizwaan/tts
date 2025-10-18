import gi
import threading
import requests
from urllib.parse import quote
import subprocess
import os
import json
from bs4 import BeautifulSoup
import time

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib

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
            "results_limit": 100
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
        
        # Title label
        self.title_label = Gtk.Label()
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_valign(Gtk.Align.CENTER)
        self.title_label.set_wrap(True)
        self.title_label.set_max_width_chars(50)
        self.title_label.add_css_class("title-4")
        self.title_label.set_hexpand(True)
        
        # Size label
        self.size_label = Gtk.Label()
        self.size_label.set_halign(Gtk.Align.END)
        self.size_label.set_valign(Gtk.Align.CENTER)
        self.size_label.add_css_class("dim-label")
        
        # Seeders/leechers
        self.seeders_label = Gtk.Label()
        self.seeders_label.set_halign(Gtk.Align.END)
        self.seeders_label.set_valign(Gtk.Align.CENTER)
        self.seeders_label.add_css_class("success")
        
        # Magnet button
        self.magnet_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.magnet_btn.add_css_class("flat")
        
        # Pack the widgets
        self.append(self.title_label)
        self.append(self.size_label)
        self.append(self.seeders_label)
        self.append(self.magnet_btn)

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_default_size(1000, 600)
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

        # Search button
        search_btn = Gtk.Button(icon_name="system-search-symbolic")
        search_btn.connect("clicked", self.on_search_clicked)
        header_bar.pack_end(search_btn)

        # Recent torrents button
        recent_btn = Gtk.Button(label="Recent Torrents")
        recent_btn.connect("clicked", self.on_recent_clicked)
        header_bar.pack_end(recent_btn)

        # Results list
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

        # Initial search if there was a previous search
        if self.app.settings.get("last_search"):
            self.on_search_clicked(None)

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
        row.size_label.set_text(f"{item_data.size} • {item_data.date}")
        row.seeders_label.set_text(f"↑{item_data.seeders} ↓{item_data.leechers}")
        
        # Connect the magnet button to open the magnet link
        row.magnet_btn.connect("clicked", lambda btn: self.open_magnet(item_data.magnet))

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

    def on_recent_clicked(self, button):
        self.status_bar.set_text("Loading recent torrents...")
        
        # Clear previous results
        self.list_store.remove_all()
        
        # Start recent torrents search in background thread
        threading.Thread(target=self.load_recent_torrents, daemon=True).start()

    def load_recent_torrents(self):
        try:
            all_results = []
            
            # Get recent torrents from multiple sources
            sources = [
                self.get_recent_rarbg,
                self.get_recent_tpb,
            ]
            
            for source in sources:
                try:
                    results = source()
                    all_results.extend(results)
                    
                    # Add delay to avoid being blocked
                    time.sleep(1)
                except Exception as e:
                    print(f"Error in {source.__name__}: {e}")
                    continue
            
            # Sort results by date (newest first)
            sorted_results = sorted(
                all_results, 
                key=lambda x: x['date'], 
                reverse=True
            )
            
            # Limit results
            limited_results = sorted_results[:self.app.settings['results_limit']]
            
            # Update UI in main thread
            GLib.idle_add(self.update_results, limited_results)
        except Exception as e:
            GLib.idle_add(self.show_error, str(e))

    def get_recent_rarbg(self):
        results = []
        try:
            # RARBG API for recent torrents
            url = "https://torrentapi.org/pubapi_v2.php?mode=list_recent&format=json_extended"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            if "torrent_results" in data:
                for item in data["torrent_results"]:
                    results.append({
                        "title": item.get("title", "Unknown"),
                        "size": str(item.get("size", 0)),
                        "date": item.get("pubdate", "Unknown"),
                        "seeders": str(item.get("seeders", 0)),
                        "leechers": str(item.get("leechers", 0)),
                        "magnet": item.get("download", "")
                    })
        except Exception as e:
            print(f"Error getting recent RARBG: {e}")
        return results

    def get_recent_tpb(self):
        results = []
        try:
            # The Pirate Bay API for recent torrents
            url = "https://apibay.org/precompiled/data.json"  # Example
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            if data and isinstance(data, list):
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
            print(f"Error getting recent The Pirate Bay: {e}")
        return results

    def perform_search(self, query):
        try:
            all_results = []
            
            # Search multiple sources (use public APIs that work in India)
            sources = [
                self.search_rarbg,
                self.search_tpb,
            ]
            
            for source in sources:
                try:
                    results = source(query)
                    all_results.extend(results)
                    
                    # Add delay to avoid being blocked
                    time.sleep(1)
                except Exception as e:
                    print(f"Error in {source.__name__}: {e}")
                    continue
            
            # Sort results by seeders
            sorted_results = sorted(
                all_results, 
                key=lambda x: int(x['seeders']) if x['seeders'].isdigit() else 0, 
                reverse=True
            )
            
            # Limit results
            limited_results = sorted_results[:self.app.settings['results_limit']]
            
            # Update UI in main thread
            GLib.idle_add(self.update_results, limited_results)
        except Exception as e:
            GLib.idle_add(self.show_error, str(e))

    def search_rarbg(self, query):
        results = []
        try:
            # RARBG API (works in most regions)
            encoded_query = quote(query)
            url = f"https://torrentapi.org/pubapi_v2.php?mode=search&search_string={encoded_query}&format=json_extended"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            if "torrent_results" in data:
                for item in data["torrent_results"]:
                    results.append({
                        "title": item.get("title", "Unknown"),
                        "size": str(item.get("size", 0)),
                        "date": item.get("pubdate", "Unknown"),
                        "seeders": str(item.get("seeders", 0)),
                        "leechers": str(item.get("leechers", 0)),
                        "magnet": item.get("download", "")
                    })
        except Exception as e:
            print(f"Error searching RARBG: {e}")
        return results

    def search_tpb(self, query):
        results = []
        try:
            # The Pirate Bay API
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
