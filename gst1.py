import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")

from gi.repository import Gtk, Adw, Gst, GLib
import pathlib
import sys


class GstPlayer(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1000, 650)
        self.set_title("GTK4 GStreamer Player")

        Gst.init(None)

        self.duration_ns = 0
        self.user_dragging = False
        self.timeline_update_id = None

        self.build_ui()
        self.build_pipeline()
        self.connect_bus()

    # -------------------------------------------------------
    # UI
    # -------------------------------------------------------
    def build_ui(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main)

        header = Adw.HeaderBar()
        self.set_titlebar(header)

        # Open file button
        open_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        # Fullscreen button
        fs_btn = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        fs_btn.connect("clicked", self.on_fullscreen_clicked)
        header.pack_end(fs_btn)

        # Video area
        self.video_area = Gtk.Box()
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)
        main.append(self.video_area)

        # Controls
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_margin_top(8)
        controls.set_margin_bottom(8)
        controls.set_margin_start(8)
        controls.set_margin_end(8)

        # Play/Pause
        self.play_btn = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self.play_btn.connect("clicked", self.on_play_pause_clicked)
        controls.append(self.play_btn)

        # Seek bar (GTK4 Adjustment must use named parameters!)
        self.seek_adj = Gtk.Adjustment(
            value=0,
            lower=0,
            upper=100,
            step_increment=1,
            page_increment=10,
            page_size=0
        )
        self.seek_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.seek_adj)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.connect("value-changed", self.on_seek_changed)
        self.seek_scale.connect("grab-begin", self.on_seek_grab_begin)
        self.seek_scale.connect("grab-end", self.on_seek_grab_end)
        controls.append(self.seek_scale)

        # Volume
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01)
        self.volume.set_value(1.0)
        self.volume.set_size_request(120, -1)
        self.volume.connect("value-changed", self.on_volume_changed)
        controls.append(self.volume)

        # URL entry
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("File path or URL")
        controls.append(self.entry)

        # URL play button
        url_play = Gtk.Button(label="Play")
        url_play.connect("clicked", self.on_play_from_entry)
        controls.append(url_play)

        main.append(controls)

    # -------------------------------------------------------
    # GStreamer pipeline
    # -------------------------------------------------------
    def build_pipeline(self):
        self.pipeline = Gst.ElementFactory.make("playbin")

        # Try gtksink first
        sink = Gst.ElementFactory.make("gtksink")
        if sink and sink.props.widget:
            widget = sink.props.widget
            widget.set_hexpand(True)
            widget.set_vexpand(True)
            self.video_area.append(widget)
            self.pipeline.set_property("video-sink", sink)
            print("Using gtksink")
        else:
            print("gtksink missing → using glimagesink fallback")

            glsink = Gst.ElementFactory.make("glimagesink")
            self.pipeline.set_property("video-sink", glsink)

            gl_area = Gtk.GLArea()
            gl_area.set_hexpand(True)
            gl_area.set_vexpand(True)
            self.video_area.append(gl_area)

    def connect_bus(self):
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

    # -------------------------------------------------------
    # Open File
    # -------------------------------------------------------
    def on_open_clicked(self, *_):
        dialog = Gtk.FileDialog()
        dialog.open(self, None, self.on_file_response)

    def on_file_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            uri = pathlib.Path(path).absolute().as_uri()
            self.start_playback(uri)
        except Exception:
            pass

    # -------------------------------------------------------
    # Playback
    # -------------------------------------------------------
    def on_play_from_entry(self, *_):
        text = self.entry.get_text().strip()
        if not text:
            return
        if "://" not in text:
            text = pathlib.Path(text).absolute().as_uri()
        self.start_playback(text)

    def start_playback(self, uri):
        self.pipeline.set_property("uri", uri)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.play_btn.set_icon_name("media-playback-pause-symbolic")

        if self.timeline_update_id:
            GLib.source_remove(self.timeline_update_id)
        self.timeline_update_id = GLib.timeout_add(200, self.update_timeline)

    def on_play_pause_clicked(self, *_):
        state = self.pipeline.get_state(0).state
        if state == Gst.State.PLAYING:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.play_btn.set_icon_name("media-playback-start-symbolic")
        else:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.play_btn.set_icon_name("media-playback-pause-symbolic")

    # -------------------------------------------------------
    # Seeking
    # -------------------------------------------------------
    def on_seek_grab_begin(self, *_):
        self.user_dragging = True

    def on_seek_grab_end(self, *_):
        self.user_dragging = False
        self.apply_seek()

    def on_seek_changed(self, scale):
        if self.user_dragging:
            return

    def apply_seek(self):
        pos = self.seek_adj.get_value()
        dur = self.seek_adj.get_upper()
        if dur <= 0:
            return

        target = int((pos / dur) * self.duration_ns)
        self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, target)

    # -------------------------------------------------------
    # Timeline updates
    # -------------------------------------------------------
    def update_timeline(self):
        try:
            ok, pos = self.pipeline.query_position(Gst.Format.TIME)
            ok2, dur = self.pipeline.query_duration(Gst.Format.TIME)
            if not ok or not ok2:
                return True

            if dur > 0:
                self.duration_ns = dur
                self.seek_adj.set_upper(dur / 1e9)

                if not self.user_dragging:
                    self.seek_adj.set_value(pos / 1e9)

        except Exception:
            pass

        return True

    # -------------------------------------------------------
    # Volume
    # -------------------------------------------------------
    def on_volume_changed(self, scale):
        v = scale.get_value()
        self.pipeline.set_property("volume", v)

    # -------------------------------------------------------
    # Fullscreen
    # -------------------------------------------------------
    def on_fullscreen_clicked(self, *_):
        if self.get_fullscreen():
            self.set_fullscreen(False)
        else:
            self.set_fullscreen(True)

    # -------------------------------------------------------
    # GStreamer Bus
    # -------------------------------------------------------
    def on_bus_message(self, bus, msg):
        if msg.type == Gst.MessageType.EOS:
            self.pipeline.set_state(Gst.State.NULL)
            self.seek_adj.set_value(0)
            self.play_btn.set_icon_name("media-playback-start-symbolic")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.GstPlayerFinal")
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = GstPlayer(app)
        win.present()


if __name__ == "__main__":
    app = App()
    app.run(sys.argv)
