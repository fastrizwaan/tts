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

        # Create first section
        header_row1 = self.create_header_row("Expandable Section", "Parent Row 1")
        self.list_box.append(header_row1)

        # Create child button rows (initially hidden)
        button_row1 = Adw.ButtonRow()
        button_row1.set_title("Child Button 1")
        self.list_box.append(button_row1)
        button_row1.set_visible(False)  # Initially hidden

        button_row2 = Adw.ButtonRow()
        button_row2.set_title("Child Button 2")
        self.list_box.append(button_row2)
        button_row2.set_visible(False)  # Initially hidden

        button_row3 = Adw.ButtonRow()
        button_row3.set_title("Child Button 3")
        self.list_box.append(button_row3)
        button_row3.set_visible(False)  # Initially hidden

        # Connect signals for child buttons
        button_row1.connect('activated', self.on_button_row_activated, "Child Button 1")
        button_row2.connect('activated', self.on_button_row_activated, "Child Button 2")
        button_row3.connect('activated', self.on_button_row_activated, "Child Button 3")

        # Store child rows for this header
        header_row1.child_rows = [button_row1, button_row2, button_row3]

        # Add a second section
        header_row2 = self.create_header_row("Another Section", "Parent Row 2")
        self.list_box.append(header_row2)

        # Add more child rows to second section (initially hidden)
        button_row4 = Adw.ButtonRow()
        button_row4.set_title("Child Button 4")
        self.list_box.append(button_row4)
        button_row4.set_visible(False)

        button_row5 = Adw.ButtonRow()
        button_row5.set_title("Child Button 5")
        self.list_box.append(button_row5)
        button_row5.set_visible(False)

        button_row4.connect('activated', self.on_button_row_activated, "Child Button 4")
        button_row5.connect('activated', self.on_button_row_activated, "Child Button 5")

        # Store child rows for this header
        header_row2.child_rows = [button_row4, button_row5]

    def create_header_row(self, title, row_name):
        """Create a custom row that acts as an expander header"""
        # Create a custom row with a button-like appearance
        row = Adw.ActionRow()
        
        # Add a disclosure triangle icon (initially pointing down)
        self.disclosure_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        self.disclosure_icon.set_icon_size(Gtk.IconSize.NORMAL)
        row.set_title(title)
        row.add_prefix(self.disclosure_icon)
        
        # Create a click event for the row
        gesture = Gtk.GestureClick()
        gesture.connect("released", self.on_header_clicked, row, row_name)
        row.add_controller(gesture)
        
        # Store the row to toggle its expanded state
        row.expanded = False
        
        return row

    def on_header_clicked(self, gesture, n_press, x, y, row, row_name):
        # Toggle expansion state
        row.expanded = not row.expanded
        
        # Rotate the disclosure icon
        if row.expanded:
            self.disclosure_icon.set_from_icon_name("pan-end-symbolic")
        else:
            self.disclosure_icon.set_from_icon_name("pan-down-symbolic")
        
        # Show/hide child rows
        for child_row in row.child_rows:
            child_row.set_visible(row.expanded)
        
        # Show the dialog when header is clicked
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"{row_name} activated!",
            body="You clicked on the header row"
        )
        dialog.add_response("ok", "OK")
        dialog.present()

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
