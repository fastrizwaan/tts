# app.py
from gi.repository import Gio, Adw, GLib  # ✅ GLib is now imported

from .window import EPubViewer

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.EpubViewer")
        self.create_action("quit", self.quit, ["<primary>q"])

        def _action_wrapper_win(method_name, variant):
            win = self.props.active_window
            if not win:
                wins = self.get_windows()
                win = wins[0] if wins else None
            if not win:
                return
            try:
                val = int(variant.unpack()) if variant else None
                if val is not None:
                    getattr(win, method_name)(val)
                else:
                    getattr(win, method_name)()
            except Exception:
                pass

        act = Gio.SimpleAction.new("set-columns", GLib.VariantType.new("i"))
        act.connect("activate", lambda a, v: _action_wrapper_win("set_columns", v))
        self.add_action(act)

        act2 = Gio.SimpleAction.new("set-column-width", GLib.VariantType.new("i"))
        act2.connect("activate", lambda a, v: _action_wrapper_win("set_column_width", v))
        self.add_action(act2)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = EPubViewer(self)
        win.present()

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)
