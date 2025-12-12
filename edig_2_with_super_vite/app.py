import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk
from window import MainWindow

class EdigApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.Edig",
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()

    def do_open(self, files, n_files, hint):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
            win.present()
        
        if n_files > 0:
            file = files[0]
            path = file.get_path()
            print(f"Opening from command line: {path}")
            win.editor.load_file(path)
            win.set_title(f"Edig - {file.get_basename()}")


