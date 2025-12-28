import sys
import csv
import string
import gi
import os

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GObject, GLib, Pango

# --- Data Model ---
class RowItem(GObject.Object):
    def __init__(self, index, data, num_cols):
        super().__init__()
        self.index = index
        self.data = data + [""] * (num_cols - len(data))

    def get_cell(self, col_idx):
        if 0 <= col_idx < len(self.data):
            return self.data[col_idx]
        return ""

    def set_cell(self, col_idx, value):
        if 0 <= col_idx < len(self.data):
            self.data[col_idx] = value

class SpreadsheetWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(900, 600)
        self.set_title("Spreadsheet")

        self.num_cols = 26
        self.num_rows = 50
        
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(vbox)

        # HeaderBar
        header = Adw.HeaderBar()
        vbox.append(header)

        btn_open = Gtk.Button(icon_name="document-open-symbolic")
        btn_open.connect("clicked", self.on_open_clicked)
        header.pack_start(btn_open)

        btn_save = Gtk.Button(icon_name="document-save-symbolic")
        btn_save.connect("clicked", self.on_save_clicked)
        header.pack_start(btn_save)

        self.lbl_filename = Gtk.Label(label="Untitled.csv")
        self.lbl_filename.add_css_class("title")
        header.set_title_widget(self.lbl_filename)

        btn_add = Gtk.Button(icon_name="list-add-symbolic")
        btn_add.connect("clicked", self.add_new_row)
        header.pack_end(btn_add)

        # --- Spreadsheet View ---
        self.store = Gio.ListStore(item_type=RowItem)
        self.selection = Gtk.NoSelection(model=self.store)
        
        self.col_view = Gtk.ColumnView()
        self.col_view.add_css_class("data-table")
        self.col_view.set_show_row_separators(True)
        self.col_view.set_show_column_separators(True)

        # 1. Row Number Column
        col_nums = Gtk.ColumnViewColumn(title="#")
        f_nums = Gtk.SignalListItemFactory()
        f_nums.connect("setup", self._setup_row_header)
        f_nums.connect("bind", self._bind_row_header)
        col_nums.set_factory(f_nums)
        col_nums.set_fixed_width(40)
        self.col_view.append_column(col_nums)

        # 2. Data Columns
        for i in range(self.num_cols):
            col_name = string.ascii_uppercase[i]
            col = Gtk.ColumnViewColumn(title=col_name)
            
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._setup_cell)
            factory.connect("bind", self._bind_cell, i)
            
            col.set_factory(factory)
            col.set_resizable(True)
            col.set_fixed_width(100)
            self.col_view.append_column(col)

        # *** THIS WAS MISSING: Attach Model to View ***
        self.col_view.set_model(self.selection)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.col_view)
        scrolled.set_vexpand(True)
        vbox.append(scrolled)

        self.create_empty_sheet()

    # --- Setup / Bind Factories ---
    def _setup_row_header(self, factory, item):
        lbl = Gtk.Label()
        lbl.add_css_class("dim-label")
        item.set_child(lbl)

    def _bind_row_header(self, factory, item):
        row_obj = item.get_item()
        item.get_child().set_text(str(row_obj.index + 1))

    def _setup_cell(self, factory, item):
        entry = Gtk.Text()
        entry.set_hexpand(True)
        entry.add_css_class("flat") 
        item.set_child(entry)

    def _bind_cell(self, factory, item, col_idx):
        entry = item.get_child()
        row_obj = item.get_item()
        
        if hasattr(entry, "changed_id"):
            entry.disconnect(entry.changed_id)
            
        entry.set_text(row_obj.get_cell(col_idx))
        
        def on_changed(widget):
            row_obj.set_cell(col_idx, widget.get_text())
        entry.changed_id = entry.connect("changed", on_changed)

    # --- Logic ---
    def create_empty_sheet(self):
        self.store.remove_all()
        new_rows = []
        for i in range(self.num_rows):
            new_rows.append(RowItem(i, [], self.num_cols))
        self.store.splice(0, 0, new_rows)

    def add_new_row(self, widget):
        idx = self.store.get_n_items()
        self.store.append(RowItem(idx, [], self.num_cols))

    # --- File I/O ---
    def on_open_clicked(self, widget):
        dialog = Gtk.FileDialog(title="Open CSV")
        filter_csv = Gtk.FileFilter()
        filter_csv.set_name("CSV Files")
        filter_csv.add_pattern("*.csv")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filter_csv)
        dialog.set_filters(filters)
        dialog.open(self, None, self._open_finish)

    def _open_finish(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            self.lbl_filename.set_label(os.path.basename(path))
            with open(path, newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                rows = list(reader)
            self.store.remove_all()
            parsed_rows = [RowItem(idx, row_data, self.num_cols) for idx, row_data in enumerate(rows)]
            self.store.splice(0, 0, parsed_rows)
        except Exception as e:
            print(f"Error: {e}")

    def on_save_clicked(self, widget):
        dialog = Gtk.FileDialog(title="Save CSV")
        dialog.set_initial_name(self.lbl_filename.get_label())
        dialog.save(self, None, self._save_finish)

    def _save_finish(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            path = file.get_path()
            self.lbl_filename.set_label(os.path.basename(path))
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                for i in range(self.store.get_n_items()):
                    writer.writerow([str(x) for x in self.store.get_item(i).data])
        except Exception as e:
            print(f"Error: {e}")

class SpreadsheetApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.Spreadsheet', flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            win = SpreadsheetWindow(self)
        win.present()

if __name__ == "__main__":
    app = SpreadsheetApp()
    app.run(sys.argv)
