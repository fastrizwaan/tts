# editor_window.py
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from mapped_file import MappedFile
from line_indexer import LineIndex
from virtual_buffer import VirtualTextBuffer
from virtual_view import VirtualTextView


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Editor")
        self.set_default_size(1000, 700)

        header = Adw.HeaderBar()
        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.on_open)
        header.pack_start(open_btn)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.append(header)

        self.scroller = Gtk.ScrolledWindow()
        self.box.append(self.scroller)

        self.set_content(self.box)

    def on_open(self, btn):
        dlg = Gtk.FileDialog()
        dlg.open(self, None, self._file_selected)

    def _file_selected(self, dlg, res):
        try:
            file = dlg.open_finish(res)
        except:
            return

        path = file.get_path()
        self.mf = MappedFile(path)
        self.idx = LineIndex(self.mf)
        self.buf = VirtualTextBuffer(self.mf, self.idx)

        self.view = VirtualTextView(self.buf)
        self.scroller.set_child(self.view)

        self.idx.on_update(self._on_index_update)
        self.idx.start()

    def _on_index_update(self):
        # update height
        total = self.buf.line_count() * self.view.line_height
        self.view.set_content_height(total)
        if self.view.vadj:
            self.view.vadj.set_upper(total)
