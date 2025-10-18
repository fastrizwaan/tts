import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

class ActionRowWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(400, 300)
        self.set_title("Adw.ActionRow Example")

        # Create the main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        self.set_content(main_box)

        # Create a preferences group
        prefs_group = Adw.PreferencesGroup()
        prefs_group.set_title("Action Rows Example")
        prefs_group.set_description("Click on the rows below")
        main_box.append(prefs_group)

        # Create first action row
        action_row1 = Adw.ActionRow()
        action_row1.set_title("Action Button Row")
        action_row1.set_subtitle("Click to trigger an action")
        
        # Add icon to the action row
        icon = Gtk.Image.new_from_icon_name("document-edit-symbolic")
        action_row1.add_prefix(icon)
        
        # Add a button to the action row
        button1 = Gtk.Button.new_with_label("Click Me")
        action_row1.add_suffix(button1)
        
        # Connect the button click signal
        button1.connect('clicked', self.on_action_row_clicked, "Action Row 1")
        prefs_group.add(action_row1)

        # Create second action row
        action_row2 = Adw.ActionRow()
        action_row2.set_title("Another Action Row")
        action_row2.set_subtitle("This also triggers an action")
        
        # Add icon to the action row
        icon2 = Gtk.Image.new_from_icon_name("emblem-system-symbolic")
        action_row2.add_prefix(icon2)
        
        # Add a button to the action row
        button2 = Gtk.Button.new_with_label("Click Me")
        action_row2.add_suffix(button2)
        
        # Connect the button click signal
        button2.connect('clicked', self.on_action_row_clicked, "Action Row 2")
        prefs_group.add(action_row2)

        # Create third action row with switch
        action_row3 = Adw.ActionRow()
        action_row3.set_title("Action with Switch")
        action_row3.set_subtitle("Toggle the switch")
        
        # Add switch to the action row
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        action_row3.add_suffix(switch)
        
        prefs_group.add(action_row3)

    def on_action_row_clicked(self, button, row_name):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"{row_name} clicked!",
            body="You clicked the button in the row"
        )
        dialog.add_response("ok", "OK")
        dialog.present()

class MyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        win = ActionRowWindow(application=app)
        win.present()

app = MyApp(application_id="com.example.ActionRowApp")
app.run(None)
