#!/usr/bin/env python3
# Fixed: toggle handler signature + set vexpand on ListView
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, Gtk, GObject, Adw

Adw.init()

class TocItem(GObject.Object):
    title = GObject.Property(type=str)
    def __init__(self, title, children=None):
        super().__init__()
        self.title = title
        self.children = Gio.ListStore(item_type=TocItem)
        if children:
            for c in children:
                self.children.append(c)

class AppWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TOC Sidebar", default_width=900, default_height=600)

        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb.set_margin_top(8); hb.set_margin_bottom(8); hb.set_margin_start(8); hb.set_margin_end(8)
        self.set_content(hb)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.set_size_request(300, -1)
        hb.append(sidebar)

        self.main = Gtk.Label(label="Main content area", xalign=0.5, yalign=0.5)
        hb.append(self.main)

        root_store = Gio.ListStore(item_type=TocItem)
        root_store.append(TocItem("Book Title"))
        ch1 = TocItem("Chapter 1", children=[TocItem("1.1 Intro"), TocItem("1.2 Basics")])
        ch2 = TocItem("Chapter 2", children=[TocItem("2.1 Step One"), TocItem("2.2 Step Two")])
        root_store.append(ch1); root_store.append(ch2); root_store.append(TocItem("Appendix"))

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.on_setup)
        factory.connect("bind", self.on_bind)

        sel_model = Gtk.NoSelection(model=root_store)
        listview = Gtk.ListView(model=sel_model, factory=factory)
        listview.set_vexpand(True)  # <--- vexpand set

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(listview)
        sidebar.append(scrolled)

    def on_setup(self, factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        btn = Gtk.Button()
        btn.get_style_context().add_class("flat")
        lbl = Gtk.Label(xalign=0)
        btn.set_child(lbl)
        box.append(btn)

        nested = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nested.set_margin_start(12)
        nested.set_visible(False)
        box.append(nested)

        def on_clicked(_btn):
            item = list_item.get_item()
            if not item:
                return
            if item.children.get_n_items() > 0:
                visible = not nested.get_visible()
                nested.set_visible(visible)
                lbl.set_text(item.title + (" ▾" if visible else " ▸"))
            else:
                self.main.set_text(f"Navigated to: {item.title}")

        btn.connect("clicked", on_clicked)

        list_item.set_child(box)
        list_item._lbl = lbl
        list_item._nested = nested
        list_item._nested_view = None

    def on_bind(self, factory, list_item):
        item = list_item.get_item()
        lbl = list_item._lbl
        nested = list_item._nested
        if not item:
            lbl.set_text("")
            return

        if item.children.get_n_items() > 0:
            lbl.set_text(item.title + (" ▾" if nested.get_visible() else " ▸"))
            if not list_item._nested_view:
                nfactory = Gtk.SignalListItemFactory()
                nfactory.connect("setup", lambda f, li: li.set_child(Gtk.Label(xalign=0)))
                nfactory.connect("bind", lambda f, li: li.get_child().set_text(li.get_item().title))
                nsel = Gtk.NoSelection(model=item.children)
                nested_view = Gtk.ListView(model=nsel, factory=nfactory)
                nested_view.set_vexpand(False)
                list_item._nested.append(nested_view)
                list_item._nested_view = nested_view
        else:
            lbl.set_text(item.title)

class MyApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.example.TOC")
    def do_activate(self):
        win = AppWindow(self)
        win.present()

if __name__ == "__main__":
    MyApp().run(None)
