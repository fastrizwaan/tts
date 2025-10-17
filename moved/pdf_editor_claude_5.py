#!/usr/bin/env python3
import gi, math, cairo
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Poppler', '0.18')
gi.require_version('Gio', '2.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Poppler, Pango, PangoCairo, GdkPixbuf

class OverlayText:
    def __init__(self, x, y, text='New Text', font_size=12, font_family='Sans', bold=False, letter_spacing=0.0):
        self.x=float(x); self.y=float(y)
        self.text=text; self.font_size=font_size; self.font_family=font_family
        self.bold=bold; self.letter_spacing=letter_spacing
        self.rotation=0; self.selected=False

class OverlayImage:
    def __init__(self, x, y, path, width=100, height=100):
        self.x=float(x); self.y=float(y); self.path=path
        self.width=float(width); self.height=float(height)
        self.rotation=0; self.selected=False
        self.aspect_ratio=(self.width/self.height) if self.height else 1.0

class PDFEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.example.pdfeditor')
        self.pdf_document=None; self.page=None; self.current_page_index=0
        self.scale=1.0; self.rotation=0
        self.text_overlays={}; self.image_overlays={}
        self.selected_overlay=None
        self.is_dragging=False

        # Resize state
        self.is_resizing=False
        self.resize_handle=None   # 'n','s','e','w','nw','ne','sw','se'
        self.anchor=(0.0,0.0)
        self.corner_start=(0.0,0.0)
        self.box_start=(0.0,0.0,0.0,0.0)
        self.ctrl_down=False

        # UI sync
        self._w_handler_id=None; self._h_handler_id=None
        self._syncing_spins=False

        # Context menu
        self.context_menu_x=0; self.context_menu_y=0

    # ===== UI =====
    def do_activate(self):
        self.win=Adw.ApplicationWindow(application=self)
        self.header=Adw.HeaderBar(); self._setup_header()

        self.drawing_area=Gtk.DrawingArea()
        self.drawing_area.set_vexpand(True); self.drawing_area.set_hexpand(True)
        self.drawing_area.set_draw_func(self.draw_func, None)

        self._setup_gestures()

        scrolled=Gtk.ScrolledWindow(); scrolled.set_child(self.drawing_area)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.header); box.append(scrolled)
        self.win.set_content(box); self.win.present()

    def _setup_header(self):
        def b(icon, cb, end=False, tip=None):
            btn=Gtk.Button(icon_name=icon); btn.connect("clicked", cb)
            if tip: btn.set_tooltip_text(tip)
            (self.header.pack_end if end else self.header.pack_start)(btn)

        b("document-open-symbolic", self.on_open, tip="Open PDF")
        b("document-save-symbolic", self.on_save, end=True, tip="Save PDF")
        b("insert-text-symbolic", self.on_add_text, tip="Add Text")
        b("insert-image-symbolic", self.on_add_image, tip="Add Image")
        b("edit-delete-symbolic", self.on_delete, tip="Delete")
        b("edit-copy-symbolic", self.on_copy, tip="Copy")
        b("zoom-out-symbolic", lambda _ : self.zoom(0.8))
        b("zoom-in-symbolic",  lambda _ : self.zoom(1.2))
        b("object-rotate-left-symbolic",  lambda _ : self.rotate(-90))
        b("object-rotate-right-symbolic", lambda _ : self.rotate(90))
        btn=Gtk.Button(label="↺"); btn.connect("clicked", lambda _ : self.rotate_selected(-90)); self.header.pack_start(btn)
        btn=Gtk.Button(label="↻"); btn.connect("clicked", lambda _ : self.rotate_selected(90));  self.header.pack_start(btn)
        b("go-previous-symbolic", self.on_prev_page, tip="Prev")
        b("go-next-symbolic",     self.on_next_page, tip="Next")

        # Text controls
        self.text_content_entry=Gtk.Entry(placeholder_text="Text"); self.text_content_entry.set_size_request(120,-1)
        self.text_content_entry.connect("changed", self.on_text_content_changed); self.header.pack_start(self.text_content_entry)
        self.font_size_spin=Gtk.SpinButton.new_with_range(8,200,1); self.font_size_spin.set_value(12)
        self.font_size_spin.connect("value-changed", self.on_font_size_changed); self.header.pack_start(self.font_size_spin)
        self.font_family_entry=Gtk.Entry(text="Sans"); self.font_family_entry.set_size_request(90,-1)
        self.font_family_entry.connect("changed", self.on_font_family_changed); self.header.pack_start(self.font_family_entry)
        self.bold_toggle=Gtk.ToggleButton(label="B"); self.bold_toggle.connect("toggled", self.on_bold_toggled)
        self.header.pack_start(self.bold_toggle)
        self.letter_spacing_spin=Gtk.SpinButton.new_with_range(-20,20,0.1); self.letter_spacing_spin.set_value(0)
        self.letter_spacing_spin.connect("value-changed", self.on_letter_spacing_changed); self.header.pack_start(self.letter_spacing_spin)

        # Image spins (free, only mirror UI)
        self.image_width_spin  = Gtk.SpinButton.new_with_range(1,10000,1); self.image_width_spin.set_size_request(70,-1)
        self.image_height_spin = Gtk.SpinButton.new_with_range(1,10000,1); self.image_height_spin.set_size_request(70,-1)
        self._w_handler_id=self.image_width_spin.connect("value-changed", self.on_image_width_changed)
        self._h_handler_id=self.image_height_spin.connect("value-changed", self.on_image_height_changed)
        self.header.pack_start(self.image_width_spin); self.header.pack_start(self.image_height_spin)

    def _setup_gestures(self):
        # Mouse
        click=Gtk.GestureClick(); click.connect("pressed", self.on_click)
        self.drawing_area.add_controller(click)
        right=Gtk.GestureClick(); right.set_button(Gdk.BUTTON_SECONDARY); right.connect("pressed", self.on_right_click)
        self.drawing_area.add_controller(right)
        drag=Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end",   self.on_drag_end)
        self.drawing_area.add_controller(drag)
        # Keyboard (track Ctrl reliably)
        key=Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        key.connect("key-released", self._on_key_released)
        self.win.add_controller(key)

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Control_L or keyval == Gdk.KEY_Control_R:
            self.ctrl_down=True
        return False
    def _on_key_released(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Control_L or keyval == Gdk.KEY_Control_R:
            self.ctrl_down=False
        return False

    # ===== Context menu =====
    def create_context_menu(self):
        menu=Gtk.PopoverMenu(); model=Gio.Menu()
        model.append("Insert Text Here","app.insert_text_here")
        model.append("Insert Image Here","app.insert_image_here")
        menu.set_menu_model(model); menu.set_parent(self.drawing_area)
        a=Gio.SimpleAction.new("insert_text_here", None); a.connect("activate", self.on_context_insert_text); self.add_action(a)
        a=Gio.SimpleAction.new("insert_image_here", None); a.connect("activate", self.on_context_insert_image); self.add_action(a)
        return menu

    def on_right_click(self, gesture, n_press, x, y):
        if not self.pdf_document: return
        self.context_menu_x, self.context_menu_y = self.transform_coordinates(x,y)
        menu=self.create_context_menu()
        rect=Gdk.Rectangle(); rect.x=int(x); rect.y=int(y); rect.width=1; rect.height=1
        menu.set_pointing_to(rect); menu.popup()

    def on_context_insert_text(self, *_):
        lst=self.text_overlays.setdefault(self.current_page_index,[])
        lst.append(OverlayText(self.context_menu_x,self.context_menu_y,
                               self.text_content_entry.get_text() or 'New Text',
                               self.font_size_spin.get_value(),
                               self.font_family_entry.get_text(),
                               self.bold_toggle.get_active(),
                               self.letter_spacing_spin.get_value()))
        self.drawing_area.queue_draw()

    def on_context_insert_image(self, *_):
        Gtk.FileDialog().open(self.win, None, self._on_context_add_image_finish)

    def _on_context_add_image_finish(self, dialog, result):
        try:
            f=dialog.open_finish(result)
            if not f: return
            path=f.get_path(); pb=GdkPixbuf.Pixbuf.new_from_file(path)
            w, h = pb.get_width(), pb.get_height()
            m=200
            if w>m or h>m:
                s=m/max(w,h); w=int(w*s); h=int(h*s)
            lst=self.image_overlays.setdefault(self.current_page_index,[])
            img=OverlayImage(self.context_menu_x,self.context_menu_y,path,w,h)
            lst.append(img); self._select_image(img); self.drawing_area.queue_draw()
        except GLib.Error as e:
            print("Error adding image:", e)

    # ===== File =====
    def on_open(self, _):
        Gtk.FileDialog().open(self.win, None, self._on_open_finish)
    def _on_open_finish(self, dialog, result):
        try:
            f=dialog.open_finish(result)
            if not f: return
            uri=f.get_uri()
            self.pdf_document=Poppler.Document.new_from_file(uri, None)
            self.current_page_index=0
            self.text_overlays={i:[] for i in range(self.pdf_document.get_n_pages())}
            self.image_overlays={i:[] for i in range(self.pdf_document.get_n_pages())}
            self.load_page()
        except GLib.Error as e:
            print("Error opening file:", e)
    def load_page(self):
        if not self.pdf_document: return
        self.page=self.pdf_document.get_page(self.current_page_index)
        self.deselect_all(); self.drawing_area.queue_draw()

    # ===== Drawing =====
    def draw_func(self, area, cr, w, h, _ud):
        if not self.page: return
        cr.save()
        cr.translate(w/2,h/2); cr.rotate(self.rotation*math.pi/180); cr.scale(self.scale,self.scale)
        pw,ph=self.page.get_size()
        cr.translate(-pw/2,-ph/2); self.page.render(cr)
        cr.restore()

        cr.save()
        cr.translate(w/2,h/2); cr.rotate(self.rotation*math.pi/180); cr.scale(self.scale,self.scale)
        cr.translate(-pw/2,-ph/2); self._draw_overlays(cr)
        cr.restore()

    def _draw_overlays(self, cr):
        # Texts
        for t in self.text_overlays.get(self.current_page_index,[]):
            cr.save(); cr.translate(t.x,t.y); cr.rotate(t.rotation*math.pi/180)
            layout=PangoCairo.create_layout(cr)
            fd=Pango.FontDescription.from_string(f"{t.font_family} {'Bold' if t.bold else ''} {t.font_size}")
            layout.set_font_description(fd)
            if t.letter_spacing!=0:
                attrs=Pango.AttrList(); attrs.insert(Pango.attr_letter_spacing_new(int(t.letter_spacing*Pango.SCALE)))
                layout.set_attributes(attrs)
            layout.set_text(t.text,-1); PangoCairo.show_layout(cr,layout)
            if t.selected:
                cr.set_source_rgba(0,0,1,0.25); w,h=layout.get_pixel_size()
                cr.rectangle(0,0,w,h); cr.fill()
            cr.restore()
        # Images + handles
        for img in self.image_overlays.get(self.current_page_index,[]):
            try:
                cr.save()
                pb=GdkPixbuf.Pixbuf.new_from_file_at_size(img.path, int(img.width), int(img.height))
                cr.translate(img.x+img.width/2, img.y+img.height/2)
                cr.rotate(img.rotation*math.pi/180)
                cr.translate(-img.width/2, -img.height/2)
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint()
                cr.restore()

                if img.selected:
                    cr.save()
                    cr.set_source_rgba(0,0.4,1,0.9)
                    cr.set_line_width(1/max(self.scale,1e-6))
                    cr.rectangle(img.x, img.y, img.width, img.height); cr.stroke()
                    s=8/max(self.scale,1e-6)
                    for (hx,hy) in self._image_handles(img).values():
                        cr.rectangle(hx-s/2, hy-s/2, s, s); cr.fill()
                    cr.restore()
            except GLib.Error:
                continue

    # ===== Handle geometry =====
    def _image_handles(self, img):
        midx=img.x+img.width/2; midy=img.y+img.height/2
        return {
            'nw': (img.x, img.y),
            'n' : (midx, img.y),
            'ne': (img.x+img.width, img.y),
            'e' : (img.x+img.width, midy),
            'se': (img.x+img.width, img.y+img.height),
            's' : (midx, img.y+img.height),
            'sw': (img.x, img.y+img.height),
            'w' : (img.x, midy),
        }

    def _hit_handle(self, img, px, py):
        s=10/max(self.scale,1e-6)
        for name,(hx,hy) in self._image_handles(img).items():
            if (hx-s)<=px<=(hx+s) and (hy-s)<=py<=(hy+s):
                return name
        return None

    # ===== Interaction =====
    def on_click(self, gesture, n_press, x, y):
        self.deselect_all()
        dx,dy=self.transform_coordinates(x,y)
        self.selected_overlay=self.find_overlay_at(dx,dy)
        if self.selected_overlay:
            self.selected_overlay.selected=True
            if isinstance(self.selected_overlay, OverlayText):
                self.text_content_entry.set_text(self.selected_overlay.text)
                self.font_size_spin.set_value(self.selected_overlay.font_size)
                self.font_family_entry.set_text(self.selected_overlay.font_family)
                self.bold_toggle.set_active(self.selected_overlay.bold)
                self.letter_spacing_spin.set_value(self.selected_overlay.letter_spacing)
            elif isinstance(self.selected_overlay, OverlayImage):
                self._sync_spins()
        self.drawing_area.queue_draw()

    def on_drag_begin(self, gesture, sx, sy):
        if not self.selected_overlay: return
        self.is_dragging=True
        dx,dy=self.transform_coordinates(sx,sy)
        if isinstance(self.selected_overlay, OverlayImage):
            h=self._hit_handle(self.selected_overlay, dx, dy)
            if h:
                self.is_resizing=True; self.resize_handle=h
                img=self.selected_overlay; handles=self._image_handles(img)
                opposite={'nw':'se','n':'s','ne':'sw','e':'w','se':'nw','s':'n','sw':'ne','w':'e'}[h]
                self.anchor=handles[opposite]; self.corner_start=(dx,dy)
                self.box_start=(img.x,img.y,img.width,img.height)
                return
        # move start
        self.drag_start_overlay_x=getattr(self.selected_overlay,'x',0.0)
        self.drag_start_overlay_y=getattr(self.selected_overlay,'y',0.0)

    def on_drag_update(self, gesture, dx, dy):
        if not self.selected_overlay or not self.is_dragging: return
        sdx=dx/self.scale; sdy=dy/self.scale
        if self.rotation!=0:
            rad=-self.rotation*math.pi/180.0
            sdx, sdy = (sdx*math.cos(rad)-sdy*math.sin(rad),
                        sdx*math.sin(rad)+sdy*math.cos(rad))

        if self.is_resizing and isinstance(self.selected_overlay, OverlayImage):
            img=self.selected_overlay
            x0,y0,w0,h0=self.box_start
            ax,ay=self.anchor
            # new pointer position in doc coords:
            cx=self.corner_start[0]+sdx; cy=self.corner_start[1]+sdy

            # Simple resize logic - just move the dragged edges
            left   = x0
            right  = x0+w0
            top    = y0
            bottom = y0+h0

            if 'w' in self.resize_handle: left  = cx
            if 'e' in self.resize_handle: right = cx
            if 'n' in self.resize_handle: top   = cy
            if 's' in self.resize_handle: bottom= cy

            # Allow negative dimensions for flipping
            new_x = left
            new_y = top
            new_w = right - left
            new_h = bottom - top

            # Handle flipping by adjusting position when dimensions become negative
            if new_w < 0:
                new_x = right
                new_w = -new_w
            if new_h < 0:
                new_y = bottom
                new_h = -new_h

            # Minimum size to prevent disappearing
            new_w = max(1.0, new_w)
            new_h = max(1.0, new_h)

            if self.ctrl_down:
                # Only apply aspect ratio constraint when Ctrl is held
                ar = img.aspect_ratio if img.aspect_ratio > 0 else (w0/h0 if h0 else 1.0)
                
                # Determine which dimension to constrain based on the handle being dragged
                if self.resize_handle in ('n','s'):         # vertical edge -> constrain width
                    new_w = new_h * ar
                elif self.resize_handle in ('e','w'):       # horizontal edge -> constrain height
                    new_h = new_w / ar
                else:  # corners - use the larger change to determine primary direction
                    if abs(new_w - w0) >= abs(new_h - h0):
                        new_h = new_w / ar
                    else:
                        new_w = new_h * ar

                # Recalculate position for constrained resize
                if 'w' in self.resize_handle:
                    new_x = x0 + w0 - new_w
                if 'n' in self.resize_handle:
                    new_y = y0 + h0 - new_h

            img.x, img.y, img.width, img.height = new_x, new_y, new_w, new_h
            img.aspect_ratio = (img.width/img.height) if img.height else img.aspect_ratio
            self._sync_spins()
            self.drawing_area.queue_draw()
            return

        # moving
        self.selected_overlay.x = self.drag_start_overlay_x + sdx
        self.selected_overlay.y = self.drag_start_overlay_y + sdy
        self.drawing_area.queue_draw()

    def on_drag_end(self, gesture, dx, dy):
        self.is_dragging=False; self.is_resizing=False; self.resize_handle=None

    # ===== Helpers =====
    def deselect_all(self):
        for lst in self.text_overlays.values():
            for o in lst: o.selected=False
        for lst in self.image_overlays.values():
            for o in lst: o.selected=False
        self.selected_overlay=None

    def find_overlay_at(self, x, y):
        for t in reversed(self.text_overlays.get(self.current_page_index,[])):
            layout=self.drawing_area.create_pango_layout(t.text)
            fd=Pango.FontDescription.from_string(f"{t.font_family} {'Bold' if t.bold else ''} {t.font_size}")
            layout.set_font_description(fd)
            if t.letter_spacing!=0:
                attrs=Pango.AttrList(); attrs.insert(Pango.attr_letter_spacing_new(int(t.letter_spacing*Pango.SCALE)))
                layout.set_attributes(attrs)
            w,h=layout.get_pixel_size()
            if t.x<=x<=t.x+w and t.y<=y<=t.y+h: return t
        for img in reversed(self.image_overlays.get(self.current_page_index,[])):
            if img.x<=x<=img.x+img.width and img.y<=y<=img.y+img.height: return img
        return None

    def transform_coordinates(self, x, y):
        if not self.page: return x,y
        w,h=self.drawing_area.get_width(), self.drawing_area.get_height()
        pw,ph=self.page.get_size()
        ax=(x-w/2)/self.scale; ay=(y-h/2)/self.scale
        rad=-self.rotation*math.pi/180.0
        rx=ax*math.cos(rad)-ay*math.sin(rad); ry=ax*math.sin(rad)+ay*math.cos(rad)
        return rx+pw/2, ry+ph/2

    def _select_image(self, img):
        self.deselect_all(); img.selected=True; self.selected_overlay=img; self._sync_spins()

    def _sync_spins(self):
        if not (isinstance(self.selected_overlay, OverlayImage)): return
        self._syncing_spins=True
        try:
            self.image_width_spin.handler_block(self._w_handler_id)
            self.image_height_spin.handler_block(self._h_handler_id)
            self.image_width_spin.set_value(self.selected_overlay.width)
            self.image_height_spin.set_value(self.selected_overlay.height)
        finally:
            self.image_width_spin.handler_unblock(self._w_handler_id)
            self.image_height_spin.handler_unblock(self._h_handler_id)
            self._syncing_spins=False

    # ===== Text editing =====
    def on_text_content_changed(self, e):
        if isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.text=e.get_text(); self.drawing_area.queue_draw()
    def on_font_size_changed(self, s):
        if isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.font_size=s.get_value(); self.drawing_area.queue_draw()
    def on_font_family_changed(self, e):
        if isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.font_family=e.get_text(); self.drawing_area.queue_draw()
    def on_bold_toggled(self, t):
        if isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.bold=t.get_active(); self.drawing_area.queue_draw()
    def on_letter_spacing_changed(self, s):
        if isinstance(self.selected_overlay, OverlayText):
            self.selected_overlay.letter_spacing=s.get_value(); self.drawing_area.queue_draw()

    # ===== Image spin handlers (free) =====
    def on_image_width_changed(self, s):
        if self._syncing_spins: return
        if isinstance(self.selected_overlay, OverlayImage):
            self.selected_overlay.width=max(1.0,float(s.get_value()))
            self.selected_overlay.aspect_ratio=(self.selected_overlay.width/self.selected_overlay.height) if self.selected_overlay.height else self.selected_overlay.aspect_ratio
            self.drawing_area.queue_draw()
    def on_image_height_changed(self, s):
        if self._syncing_spins: return
        if isinstance(self.selected_overlay, OverlayImage):
            self.selected_overlay.height=max(1.0,float(s.get_value()))
            self.selected_overlay.aspect_ratio=(self.selected_overlay.width/self.selected_overlay.height) if self.selected_overlay.height else self.selected_overlay.aspect_ratio
            self.drawing_area.queue_draw()

    # ===== Misc =====
    def on_add_text(self, _):
        lst=self.text_overlays.setdefault(self.current_page_index,[])
        lst.append(OverlayText(100,100,self.text_content_entry.get_text() or 'New Text',
                               self.font_size_spin.get_value(), self.font_family_entry.get_text(),
                               self.bold_toggle.get_active(), self.letter_spacing_spin.get_value()))
        self.drawing_area.queue_draw()
    def on_add_image(self, _):
        Gtk.FileDialog().open(self.win, None, self._on_add_image_finish)
    def _on_add_image_finish(self, dialog, result):
        try:
            f=dialog.open_finish(result)
            if not f: return
            path=f.get_path(); pb=GdkPixbuf.Pixbuf.new_from_file(path)
            w,h=pb.get_width(), pb.get_height()
            m=200
            if w>m or h>m:
                s=m/max(w,h); w=int(w*s); h=int(h*s)
            lst=self.image_overlays.setdefault(self.current_page_index,[])
            img=OverlayImage(50,50,path,w,h); lst.append(img); self._select_image(img)
            self.drawing_area.queue_draw()
        except GLib.Error as e:
            print("Error adding image:", e)
    def on_delete(self, _):
        if not self.selected_overlay: return
        if isinstance(self.selected_overlay, OverlayText):
            self.text_overlays[self.current_page_index].remove(self.selected_overlay)
        else:
            self.image_overlays[self.current_page_index].remove(self.selected_overlay)
        self.selected_overlay=None; self.drawing_area.queue_draw()
    def on_copy(self, _):
        if not self.selected_overlay: return
        if isinstance(self.selected_overlay, OverlayText):
            t=self.selected_overlay
            n=OverlayText(t.x+10,t.y+10,t.text,t.font_size,t.font_family,t.bold,t.letter_spacing)
            n.rotation=t.rotation; self.text_overlays[self.current_page_index].append(n)
        else:
            i=self.selected_overlay
            n=OverlayImage(i.x+10,i.y+10,i.path,i.width,i.height)
            n.rotation=i.rotation; n.aspect_ratio=i.aspect_ratio
            self.image_overlays[self.current_page_index].append(n); self._select_image(n)
        self.drawing_area.queue_draw()
    def zoom(self,f): self.scale*=f; self.drawing_area.queue_draw()
    def rotate(self,a): self.rotation=(self.rotation+a)%360; self.drawing_area.queue_draw()
    def rotate_selected(self,a):
        if self.selected_overlay:
            self.selected_overlay.rotation=(self.selected_overlay.rotation+a)%360
            self.drawing_area.queue_draw()
    def on_prev_page(self,_):
        if self.pdf_document and self.current_page_index>0:
            self.current_page_index-=1; self.load_page()
    def on_next_page(self,_):
        if self.pdf_document and self.current_page_index<self.pdf_document.get_n_pages()-1:
            self.current_page_index+=1; self.load_page()

    # ===== Save =====
    def on_save(self,_):
        Gtk.FileDialog().save(self.win, None, self._on_save_finish)
    def _on_save_finish(self, dialog, result):
        try:
            f=dialog.save_finish(result)
            if not f: return
            path=f.get_path()
            if not path.endswith('.pdf'): path+='.pdf'
            self.save_pdf(path)
        except GLib.Error as e:
            print("Error saving file:", e)
    def save_pdf(self,path):
        if not self.pdf_document: return
        first=self.pdf_document.get_page(0); w,h=first.get_size()
        surface=cairo.PDFSurface(path,w,h); cr=cairo.Context(surface)
        for i in range(self.pdf_document.get_n_pages()):
            pg=self.pdf_document.get_page(i); pw,ph=pg.get_size()
            surface.set_size(pw,ph); pg.render_for_printing(cr)
            cr.save(); self._draw_overlays_save(cr,i); cr.restore()
            if i<self.pdf_document.get_n_pages()-1: surface.show_page()
        surface.finish()
    def _draw_overlays_save(self, cr, idx):
        for t in self.text_overlays.get(idx,[]):
            cr.save(); cr.translate(t.x,t.y); cr.rotate(t.rotation*math.pi/180)
            layout=PangoCairo.create_layout(cr)
            fd=Pango.FontDescription.from_string(f"{t.font_family} {'Bold' if t.bold else ''} {t.font_size}")
            layout.set_font_description(fd)
            if t.letter_spacing!=0:
                attrs=Pango.AttrList(); attrs.insert(Pango.attr_letter_spacing_new(int(t.letter_spacing*Pango.SCALE)))
                layout.set_attributes(attrs)
            layout.set_text(t.text,-1); PangoCairo.show_layout(cr,layout); cr.restore()
        for img in self.image_overlays.get(idx,[]):
            try:
                cr.save()
                pb=GdkPixbuf.Pixbuf.new_from_file_at_size(img.path, int(img.width), int(img.height))
                cr.translate(img.x+img.width/2, img.y+img.height/2)
                cr.rotate(img.rotation*math.pi/180); cr.translate(-img.width/2, -img.height/2)
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()
            except GLib.Error:
                continue

if __name__ == "__main__":
    app=PDFEditor(); app.run(None)
