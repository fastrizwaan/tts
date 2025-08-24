import gi
import math
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Poppler', '0.18')
gi.require_version('Gio', '2.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('cairo', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Poppler, Pango, cairo, PangoCairo, GdkPixbuf

class OverlayText:
    def __init__(self, x, y, text='New Text', font_size=12, font_family='Sans', bold=False, letter_spacing=0.0):
        self.x = x
        self.y = y
        self.text = text
        self.font_size = font_size
        self.font_family = font_family
        self.bold = bold
        self.letter_spacing = letter_spacing
        self.rotation = 0
        self.selected = False

class OverlayImage:
    def __init__(self, x, y, path, width=100, height=100):
        self.x = x
        self.y = y
        self.path = path
        self.width = width
        self.height = height
        self.rotation = 0
        self.selected = False
        self.aspect_ratio = width / height if height != 0 else 1.0

class PDFEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.pdfeditor')
        self.pdf_document = None
        self.page = None
        self.current_page_index = 0
        self.scale = 1.0
        self.rotation = 0
        self.text_overlays = {}  # page_index: list of OverlayText
        self.image_overlays = {}  # page_index: list of OverlayImage
        self.selected_overlay = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.aspect_locked = True

    def do_activate(self):
        self.win = Adw.ApplicationWindow(application=self)
        self.header = Adw.HeaderBar()
        self.setup_buttons()
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_vexpand(True)
        self.drawing_area.set_hexpand(True)
        self.drawing_area.set_draw_func(self.draw_func, None)
        self.setup_gestures()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.drawing_area)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.header)
        box.append(scrolled)
        self.win.set_content(box)
        self.win.present()

    def setup_buttons(self):
        open_button = Gtk.Button(label="Open")
        open_button.connect("clicked", self.on_open)
        self.header.pack_start(open_button)
        save_button = Gtk.Button(label="Save")
        save_button.connect("clicked", self.on_save)
        self.header.pack_end(save_button)
        add_text_button = Gtk.Button(label="Add Text")
        add_text_button.connect("clicked", self.on_add_text)
        self.header.pack_start(add_text_button)
        add_image_button = Gtk.Button(label="Add Image")
        add_image_button.connect("clicked", self.on_add_image)
        self.header.pack_start(add_image_button)
        delete_button = Gtk.Button(label="Delete")
        delete_button.connect("clicked", self.on_delete)
        self.header.pack_start(delete_button)
        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self.on_copy)
        self.header.pack_start(copy_button)
        zoom_in = Gtk.Button(label="Zoom In")
        zoom_in.connect("clicked", lambda b: self.zoom(1.2))
        self.header.pack_start(zoom_in)
        zoom_out = Gtk.Button(label="Zoom Out")
        zoom_out.connect("clicked", lambda b: self.zoom(0.8))
        self.header.pack_start(zoom_out)
        rotate_left = Gtk.Button(label="Page Rot L")
        rotate_left.connect("clicked", lambda b: self.rotate(-90))
        self.header.pack_start(rotate_left)
        rotate_right = Gtk.Button(label="Page Rot R")
        rotate_right.connect("clicked", lambda b: self.rotate(90))
        self.header.pack_start(rotate_right)
        rot_sel_left = Gtk.Button(label="Sel Rot L")
        rot_sel_left.connect("clicked", lambda b: self.rotate_selected(-90))
        self.header.pack_start(rot_sel_left)
        rot_sel_right = Gtk.Button(label="Sel Rot R")
        rot_sel_right.connect("clicked", lambda b: self.rotate_selected(90))
        self.header.pack_start(rot_sel_right)
        prev_page = Gtk.Button(label="Prev")
        prev_page.connect("clicked", self.on_prev_page)
        self.header.pack_start(prev_page)
        next_page = Gtk.Button(label="Next")
        next_page.connect("clicked", self.on_next_page)
        self.header.pack_start(next_page)
        self.text_content_entry = Gtk.Entry()
        self.text_content_entry.set_placeholder_text("Text content")
        self.text_content_entry.connect("changed", self.on_text_content_changed)
        self.header.pack_start(self.text_content_entry)
        self.font_size_spin = Gtk.SpinButton.new_with_range(8, 72, 1)
        self.font_size_spin.set_value(12)
        self.font_size_spin.connect("value-changed", self.on_font_size_changed)
        self.header.pack_start(self.font_size_spin)
        self.font_family_entry = Gtk.Entry(text="Sans")
        self.font_family_entry.connect("changed", self.on_font_family_changed)
        self.header.pack_start(self.font_family_entry)
        self.bold_toggle = Gtk.ToggleButton(label="Bold")
        self.bold_toggle.connect("toggled", self.on_bold_toggled)
        self.header.pack_start(self.bold_toggle)
        spacing_label = Gtk.Label(label="Spacing")
        self.header.pack_start(spacing_label)
        self.letter_spacing_spin = Gtk.SpinButton.new_with_range(-20, 20, 0.1)
        self.letter_spacing_spin.set_value(0)
        self.letter_spacing_spin.connect("value-changed", self.on_letter_spacing_changed)
        self.header.pack_start(self.letter_spacing_spin)
        width_label = Gtk.Label(label="Img W")
        self.header.pack_start(width_label)
        self.image_width_spin = Gtk.SpinButton.new_with_range(10, 2000, 1)
        self.image_width_spin.connect("value-changed", self.on_image_width_changed)
        self.header.pack_start(self.image_width_spin)
        height_label = Gtk.Label(label="Img H")
        self.header.pack_start(height_label)
        self.image_height_spin = Gtk.SpinButton.new_with_range(10, 2000, 1)
        self.image_height_spin.connect("value-changed", self.on_image_height_changed)
        self.header.pack_start(self.image_height_spin)
        self.aspect_lock_toggle = Gtk.ToggleButton(label="Lock Aspect")
        self.aspect_lock_toggle.set_active(True)
        self.aspect_lock_toggle.connect("toggled", self.on_aspect_lock_toggled)
        self.header.pack_start(self.aspect_lock_toggle)

    def setup_gestures(self):
        click_gesture = Gtk.GestureClick()
        click_gesture.connect("pressed", self.on_click)
        self.drawing_area.add_controller(click_gesture)
        drag_gesture = Gtk.GestureDrag()
        drag_gesture.connect("drag-begin", self.on_drag_begin)
        drag_gesture.connect("drag-update", self.on_drag_update)
        drag_gesture.connect("drag-end", self.on_drag_end)
        self.drawing_area.add_controller(drag_gesture)

    def on_open(self, button):
        dialog = Gtk.FileDialog()
        dialog.open(self.win, None, self.on_open_finish)

    def on_open_finish(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                uri = file.get_uri()
                self.pdf_document = Poppler.Document.new_from_file(uri, None)
                self.current_page_index = 0
                self.text_overlays = {i: [] for i in range(self.pdf_document.get_n_pages())}
                self.image_overlays = {i: [] for i in range(self.pdf_document.get_n_pages())}
                self.load_page()
        except GLib.Error as e:
            print(f"Error opening file: {e}")

    def load_page(self):
        if self.pdf_document:
            self.page = self.pdf_document.get_page(self.current_page_index)
            self.drawing_area.queue_draw()

    def draw_func(self, area, context, w, h, user_data):
        if not self.page:
            return
        context.save()
        context.translate(w / 2, h / 2)
        context.rotate(self.rotation * (math.pi / 180))
        context.scale(self.scale, self.scale)
        page_width, page_height = self.page.get_size()
        context.translate(-page_width / 2, -page_height / 2)
        self.page.render(context)
        context.restore()
        context.save()
        context.translate(w / 2, h / 2)
        context.rotate(self.rotation * (math.pi / 180))
        context.scale(self.scale, self.scale)
        context.translate(-page_width / 2, -page_height / 2)
        self.draw_overlays(context)
        context.restore()

    def draw_overlays(self, context):
        overlays_text = self.text_overlays.get(self.current_page_index, [])
        for text in overlays_text:
            context.save()
            context.translate(text.x, text.y)
            context.rotate(text.rotation * (math.pi / 180))
            layout = PangoCairo.create_layout(context)
            font_str = f"{text.font_family} {'Bold' if text.bold else ''} {text.font_size}"
            font_desc = Pango.FontDescription.from_string(font_str)
            layout.set_font_description(font_desc)
            if text.letter_spacing != 0:
                attrs = Pango.AttrList()
                attrs.insert(Pango.attr_letter_spacing_new(int(text.letter_spacing * Pango.SCALE)))
                layout.set_attributes(attrs)
            layout.set_text(text.text, -1)
            PangoCairo.show_layout(context, layout)
            if text.selected:
                context.set_source_rgba(0, 0, 1, 0.3)
                width, height = layout.get_pixel_size()
                context.rectangle(0, 0, width, height)
                context.fill()
            context.restore()
        overlays_image = self.image_overlays.get(self.current_page_index, [])
        for img in overlays_image:
            context.save()
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(img.path, int(img.width), int(img.height))
            context.translate(img.x + img.width / 2, img.y + img.height / 2)
            context.rotate(img.rotation * (math.pi / 180))
            context.translate(-img.width / 2, -img.height / 2)
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.paint()
            if img.selected:
                context.set_source_rgba(0, 0, 1, 0.3)
                context.rectangle(0, 0, img.width, img.height)
                context.fill()
            context.restore()

    def on_add_text(self, button):
        overlays = self.text_overlays.setdefault(self.current_page_index, [])
        font_size = self.font_size_spin.get_value()
        font_family = self.font_family_entry.get_text()
        text_content = self.text_content_entry.get_text() or 'New Text'
        bold = self.bold_toggle.get_active()
        letter_spacing = self.letter_spacing_spin.get_value()
        overlays.append(OverlayText(100, 100, text_content, font_size, font_family, bold, letter_spacing))
        self.drawing_area.queue_draw()

    def on_add_image(self, button):
        dialog = Gtk.FileDialog()
        dialog.open(self.win, None, self.on_add_image_finish)

    def on_add_image_finish(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
                width = pixbuf.get_width()
                height = pixbuf.get_height()
                max_size = 200
                if width > max_size or height > max_size:
                    scale = max_size / max(width, height)
                    width = int(width * scale)
                    height = int(height * scale)
                overlays = self.image_overlays.setdefault(self.current_page_index, [])
                img = OverlayImage(50, 50, path, width, height)
                overlays.append(img)
                self.drawing_area.queue_draw()
        except GLib.Error as e:
            print(f"Error adding image: {e}")

    def on_click(self, gesture, n_press, x, y):
        self.deselect_all()
        adjusted_x, adjusted_y = self.transform_coordinates(x, y)
        self.selected_overlay = self.find_overlay_at(adjusted_x, adjusted_y)
        if self.selected_overlay:
            self.selected_overlay.selected = True
            if isinstance(self.selected_overlay, OverlayText):
                self.text_content_entry.set_text(self.selected_overlay.text)
                self.font_size_spin.set_value(self.selected_overlay.font_size)
                self.font_family_entry.set_text(self.selected_overlay.font_family)
                self.bold_toggle.set_active(self.selected_overlay.bold)
                self.letter_spacing_spin.set_value(self.selected_overlay.letter_spacing)
            elif isinstance(self.selected_overlay, OverlayImage):
                self.image_width_spin.set_value(self.selected_overlay.width)
                self.image_height_spin.set_value(self.selected_overlay.height)
        self.drawing_area.queue_draw()

    def deselect_all(self):
        for overlays in self.text_overlays.values():
            for o in overlays:
                o.selected = False
        for overlays in self.image_overlays.values():
            for o in overlays:
                o.selected = False
        self.selected_overlay = None

    def find_overlay_at(self, x, y):
        overlays_text = self.text_overlays.get(self.current_page_index, [])
        for text in reversed(overlays_text):
            layout = self.drawing_area.create_pango_layout(text.text)
            font_str = f"{text.font_family} {'Bold' if text.bold else ''} {text.font_size}"
            font_desc = Pango.FontDescription.from_string(font_str)
            layout.set_font_description(font_desc)
            if text.letter_spacing != 0:
                attrs = Pango.AttrList()
                attrs.insert(Pango.attr_letter_spacing_new(int(text.letter_spacing * Pango.SCALE)))
                layout.set_attributes(attrs)
            width, height = layout.get_pixel_size()
            if text.x <= x <= text.x + width and text.y <= y <= text.y + height:
                return text
        overlays_image = self.image_overlays.get(self.current_page_index, [])
        for img in reversed(overlays_image):
            if img.x <= x <= img.x + img.width and img.y <= y <= img.y + img.height:
                return img
        return None

    def transform_coordinates(self, x, y):
        w, h = self.drawing_area.get_width(), self.drawing_area.get_height()
        page_width, page_height = self.page.get_size()
        adj_x = (x - w / 2) / self.scale
        adj_y = (y - h / 2) / self.scale
        rad = -self.rotation * (math.pi / 180)  # Reverse rotation
        rot_x = adj_x * math.cos(rad) - adj_y * math.sin(rad)
        rot_y = adj_x * math.sin(rad) + adj_y * math.cos(rad)
        return rot_x + page_width / 2, rot_y + page_height / 2

    def on_drag_begin(self, gesture, start_x, start_y):
        if self.selected_overlay:
            self.drag_start_x, self.drag_start_y = self.transform_coordinates(start_x, start_y)

    def on_drag_update(self, gesture, dx, dy):
        if self.selected_overlay:
            adj_dx = dx / self.scale
            adj_dy = dy / self.scale
            rad = -self.rotation * (math.pi / 180)
            rot_dx = adj_dx * math.cos(rad) - adj_dy * math.sin(rad)
            rot_dy = adj_dx * math.sin(rad) + adj_dy * math.cos(rad)
            self.selected_overlay.x += rot_dx
            self.selected_overlay.y += rot_dy
            self.drawing_area.queue_draw()

    def on_drag_end(self, gesture, dx, dy):
        pass

    def on_text_content_changed(self, entry):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.text = entry.get_text()
            self.drawing_area.queue_draw()

    def on_font_size_changed(self, spin):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.font_size = spin.get_value()
            self.drawing_area.queue_draw()

    def on_font_family_changed(self, entry):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.font_family = entry.get_text()
            self.drawing_area.queue_draw()

    def on_bold_toggled(self, toggle):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.bold = toggle.get_active()
            self.drawing_area.queue_draw()

    def on_letter_spacing_changed(self, spin):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.letter_spacing = spin.get_value()
            self.drawing_area.queue_draw()

    def on_image_width_changed(self, spin):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayImage):
            new_width = spin.get_value()
            if self.aspect_locked:
                self.selected_overlay.height = new_width / self.selected_overlay.aspect_ratio
                self.image_height_spin.set_value(self.selected_overlay.height)
            self.selected_overlay.width = new_width
            self.drawing_area.queue_draw()

    def on_image_height_changed(self, spin):
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayImage):
            new_height = spin.get_value()
            if self.aspect_locked:
                self.selected_overlay.width = new_height * self.selected_overlay.aspect_ratio
                self.image_width_spin.set_value(self.selected_overlay.width)
            self.selected_overlay.height = new_height
            self.drawing_area.queue_draw()

    def on_aspect_lock_toggled(self, toggle):
        self.aspect_locked = toggle.get_active()
        if self.selected_overlay and isinstance(self.selected_overlay, OverlayImage) and self.aspect_locked:
            self.selected_overlay.aspect_ratio = self.selected_overlay.width / self.selected_overlay.height if self.selected_overlay.height != 0 else 1.0

    def on_delete(self, button):
        if self.selected_overlay:
            if isinstance(self.selected_overlay, OverlayText):
                self.text_overlays[self.current_page_index].remove(self.selected_overlay)
            elif isinstance(self.selected_overlay, OverlayImage):
                self.image_overlays[self.current_page_index].remove(self.selected_overlay)
            self.selected_overlay = None
            self.drawing_area.queue_draw()

    def on_copy(self, button):
        if self.selected_overlay:
            if isinstance(self.selected_overlay, OverlayText):
                new = OverlayText(
                    self.selected_overlay.x + 10,
                    self.selected_overlay.y + 10,
                    self.selected_overlay.text,
                    self.selected_overlay.font_size,
                    self.selected_overlay.font_family,
                    self.selected_overlay.bold,
                    self.selected_overlay.letter_spacing
                )
                new.rotation = self.selected_overlay.rotation
                self.text_overlays[self.current_page_index].append(new)
            elif isinstance(self.selected_overlay, OverlayImage):
                new = OverlayImage(
                    self.selected_overlay.x + 10,
                    self.selected_overlay.y + 10,
                    self.selected_overlay.path,
                    self.selected_overlay.width,
                    self.selected_overlay.height
                )
                new.rotation = self.selected_overlay.rotation
                new.aspect_ratio = self.selected_overlay.aspect_ratio
                self.image_overlays[self.current_page_index].append(new)
            self.drawing_area.queue_draw()

    def zoom(self, factor):
        self.scale *= factor
        self.drawing_area.queue_draw()

    def rotate(self, angle):
        self.rotation = (self.rotation + angle) % 360
        self.drawing_area.queue_draw()

    def rotate_selected(self, angle):
        if self.selected_overlay:
            self.selected_overlay.rotation = (self.selected_overlay.rotation + angle) % 360
            self.drawing_area.queue_draw()

    def on_prev_page(self, button):
        if self.current_page_index > 0:
            self.current_page_index -= 1
            self.load_page()

    def on_next_page(self, button):
        if self.current_page_index < self.pdf_document.get_n_pages() - 1:
            self.current_page_index += 1
            self.load_page()

    def on_save(self, button):
        dialog = Gtk.FileDialog()
        dialog.save(self.win, None, self.on_save_finish)

    def on_save_finish(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if file:
                path = file.get_path()
                if not path.endswith('.pdf'):
                    path += '.pdf'
                self.save_pdf(path)
        except GLib.Error as e:
            print(f"Error saving file: {e}")

    def save_pdf(self, path):
        if not self.pdf_document:
            return
        surface = cairo.PDFSurface(path, 0, 0)  # Size set per page
        context = cairo.Context(surface)
        for i in range(self.pdf_document.get_n_pages()):
            page = self.pdf_document.get_page(i)
            w, h = page.get_size()
            surface.set_size(w, h)
            page.render_for_printing(context)
            context.save()
            self.draw_overlays_for_save(context, i)
            context.restore()
            surface.show_page()
        surface.finish()

    def draw_overlays_for_save(self, context, page_index):
        overlays_text = self.text_overlays.get(page_index, [])
        for text in overlays_text:
            context.save()
            context.translate(text.x, text.y)
            context.rotate(text.rotation * (math.pi / 180))
            layout = PangoCairo.create_layout(context)
            font_str = f"{text.font_family} {'Bold' if text.bold else ''} {text.font_size}"
            font_desc = Pango.FontDescription.from_string(font_str)
            layout.set_font_description(font_desc)
            if text.letter_spacing != 0:
                attrs = Pango.AttrList()
                attrs.insert(Pango.attr_letter_spacing_new(int(text.letter_spacing * Pango.SCALE)))
                layout.set_attributes(attrs)
            layout.set_text(text.text, -1)
            PangoCairo.show_layout(context, layout)
            context.restore()
        overlays_image = self.image_overlays.get(page_index, [])
        for img in overlays_image:
            context.save()
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(img.path, int(img.width), int(img.height))
            context.translate(img.x + img.width / 2, img.y + img.height / 2)
            context.rotate(img.rotation * (math.pi / 180))
            context.translate(-img.width / 2, -img.height / 2)
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.paint()
            context.restore()

if __name__ == "__main__":
    app = PDFEditor()
    app.run(None)
