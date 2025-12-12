import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio
from editor import TopEditor

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_default_size(900, 600)
        self.set_title("Edig - High Performance Editor")

        # Main Layout
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)

        # HeaderBar
        header_bar = Adw.HeaderBar()
        box.append(header_bar)
        
        # Tools in HeaderBar
        open_button = Gtk.Button(label="Open")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        save_button = Gtk.Button(label="Save")
        save_button.connect("clicked", self.on_save_clicked)
        header_bar.pack_start(save_button)
        
        # Word wrap toggle button
        self.wrap_button = Gtk.ToggleButton(label="Wrap")
        self.wrap_button.set_tooltip_text("Toggle Word Wrap (Ctrl+W)")
        self.wrap_button.connect("toggled", self.on_wrap_toggled)
        header_bar.pack_end(self.wrap_button)

        # Editor
        self.editor = TopEditor()
        box.append(self.editor)

    def on_open_clicked(self, button):
        # Native File Chooser
        dialog = Gtk.FileDialog()
        dialog.set_title("Open File")
        dialog.open(self, None, self.on_open_response)

    def on_open_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                print(f"Opening: {path}")
                self.editor.load_file(path)
                self.set_title(f"Edig - {file.get_basename()}")
        except Exception as e:
            print(f"Error selecting file: {e}")

    def on_save_clicked(self, button):
        if self.editor.file and self.editor.file.get_location():
             self.editor.save_file()
        else:
             # Trigger Save As behavior if no file
             dialog = Gtk.FileDialog()
             dialog.set_title("Save File")
             dialog.save(self, None, self.on_save_as_response)
    
    def on_save_as_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if file:
                 path = file.get_path()
                 # Set the file location on the editor
                 f = Gio.File.new_for_path(path)
                 if not self.editor.file:
                     self.editor.file = Gio.File.new_for_path(path)
                 self.editor.file = f
                 self.editor.save_file()
                 self.set_title(f"Edig - {file.get_basename()}")
        except Exception as e:
            print(f"Error saving file: {e}")
    
    def on_wrap_toggled(self, button):
        """Toggle word wrap."""
        self.editor.toggle_word_wrap()

