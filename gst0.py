import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")

from gi.repository import Gtk, Adw, Gst
import sys
import pathlib


class GstPlayer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(900, 600)
        self.set_title("GStreamer GTK4 Player")

        Gst.init(None)

        self.build_ui()
        self.build_pipeline()

    # -------------------------------------------------------
    # UI
    # -------------------------------------------------------
    def build_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)

        header = Adw.HeaderBar()
        box.append(header)

        # video container
        self.video_area = Gtk.Box()
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)
        box.append(self.video_area)

        # controls
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_margin_start(6)
        controls.set_margin_end(6)
        controls.set_margin_bottom(6)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Enter file path or URL")
        controls.append(self.entry)

        play_btn = Gtk.Button(label="Play")
        play_btn.connect("clicked", self.on_play_clicked)
        controls.append(play_btn)

        box.append(controls)

    # -------------------------------------------------------
    # Pipeline with gtksink → fallback to glimagesink
    # -------------------------------------------------------
    def build_pipeline(self):
        self.pipeline = Gst.ElementFactory.make("playbin")
        if not self.pipeline:
            raise RuntimeError("Could not create playbin")

        # Try gtksink first
        self.sink = Gst.ElementFactory.make("gtksink")

        if self.sink:
            w = self.sink.props.widget
            if w:
                w.set_hexpand(True)
                w.set_vexpand(True)
                self.video_area.append(w)
                self.pipeline.set_property("video-sink", self.sink)
                print("Using gtksink (best choice)")
                return

        # Fallback: accelerated OpenGL sink + Gtk.GLArea
        print("gtksink unavailable — using glimagesink fallback")

        glsink = Gst.ElementFactory.make("glimagesink")
        if not glsink:
            raise RuntimeError("Neither gtksink nor glimagesink available")

        # Tell playbin to use glimagesink
        self.pipeline.set_property("video-sink", glsink)

        # Add a GLArea (GStreamer handles rendering into it automatically)
        self.gl_area = Gtk.GLArea()
        self.gl_area.set_hexpand(True)
        self.gl_area.set_vexpand(True)
        self.video_area.append(self.gl_area)

    # -------------------------------------------------------
    # Playback
    # -------------------------------------------------------
    def on_play_clicked(self, button):
        uri = self.entry.get_text().strip()
        if not uri:
            return

        if "://" not in uri:
            uri = str(pathlib.Path(uri).absolute().as_uri())

        self.pipeline.set_property("uri", uri)
        self.pipeline.set_state(Gst.State.PLAYING)

    def __del__(self):
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except:
            pass


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.GstPlayer")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = GstPlayer(app)
        win.present()


if __name__ == "__main__":
    app = App()
    app.run(sys.argv)
