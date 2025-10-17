import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

class ExpandingButtonRowWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(400, 500)
        self.set_title("Expanding Adw.ButtonRow Example")

        # Create the main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        self.set_content(main_box)

        # Create a scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)  # Enable vertical expansion
        main_box.append(scrolled)

        # Create a listbox
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self.list_box)

        # Create expandable parent row using Adw.ExpanderRow
        parent_row = Adw.ExpanderRow()
        parent_row.set_title("Expandable Section")
        #parent_row.set_subtitle("Click to see more options")
        parent_row.set_enable_expansion(True)
        
      
        self.list_box.append(parent_row)

        # Add child button rows to the expander
        button_row1 = Adw.ButtonRow()
        button_row1.set_title("Child Button 1")
        parent_row.add_row(button_row1)

        button_row2 = Adw.ButtonRow()
        button_row2.set_title("Child Button 2")
        parent_row.add_row(button_row2)

        button_row3 = Adw.ButtonRow()
        button_row3.set_title("Child Button 3")
        parent_row.add_row(button_row3)

        # Connect signals for child buttons
        button_row1.connect('activated', self.on_button_row_activated, "Child Button 1")
        button_row2.connect('activated', self.on_button_row_activated, "Child Button 2")
        button_row3.connect('activated', self.on_button_row_activated, "Child Button 3")

        # Add a second section
        parent_row2 = Adw.ExpanderRow()
        parent_row2.set_title("Another Section")
        #parent_row2.set_subtitle("More expandable content")
        parent_row2.set_enable_expansion(True)
        
       
        self.list_box.append(parent_row2)

        # Add more child rows to second section
        button_row4 = Adw.ButtonRow()
        button_row4.set_title("Child Button 4")
        parent_row2.add_row(button_row4)

        button_row5 = Adw.ButtonRow()
        button_row5.set_title("Child Button 5")
        parent_row2.add_row(button_row5)

        button_row4.connect('activated', self.on_button_row_activated, "Child Button 4")
        button_row5.connect('activated', self.on_button_row_activated, "Child Button 5")

    def on_button_row_activated(self, row, button_name):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"{button_name} clicked!",
            body=f"You clicked on {row.get_title()}"
        )
        dialog.add_response("ok", "OK")
        dialog.present()

class MyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        win = ExpandingButtonRowWindow(application=app)
        win.present()

app = MyApp(application_id="com.example.ExpandingButtonRowApp")
app.run(None)
