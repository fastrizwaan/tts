# main.py
import gi
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from editor_window import EditorWindow


class EditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="xyz.virtual.editor",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    Adw.init()
    app = EditorApp()
    app.run([])

