#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, GdkPixbuf
import mpv
import os
import sys
import time
import glob

FRAME_DIR = "/tmp/mpv_frames/"
os.makedirs(FRAME_DIR, exist_ok=True)


# ============================================================================
#                             CPU IMAGE-VO PLAYER
# ============================================================================
class MpvCPUWidget(Gtk.Picture):
    """
    CPU-only mpv renderer using vo=image.
    Saves PNGs to a directory, and we watch them and display.
    """

    def __init__(self):
        super().__init__()

        # Clean old frames
        for f in glob.glob(FRAME_DIR + "*.png"):
            os.remove(f)

        # mpv instance
        self.mpv = mpv.MPV(
            vo=f"image:format=png:outdir={FRAME_DIR}",
            keep_open="yes"
        )

        # Start the frame polling timer
        self._last_frame_file = None
        GLib.timeout_add(30, self.poll_frame)

    # Poll for new images in FRAME_DIR
    def poll_frame(self):
        try:
            # Find latest PNG file
            pngs = glob.glob(FRAME_DIR + "*.png")
            if not pngs:
                return True

            latest = max(pngs, key=os.path.getmtime)

            # Only reload when it's a new frame
            if latest != self._last_frame_file:
                self._last_frame_file = latest
                pb = GdkPixbuf.Pixbuf.new_from_file(latest)
                self.set_pixbuf(pb)

        except Exception as e:
            print("poll_frame error:", e)

        return True

    # Public API
    def load(self, path):
        # Clear frame directory
        for f in glob.glob(FRAME_DIR + "*.png"):
            os.remove(f)

        self._last_frame_file = None
        self.mpv.command("loadfile", path)

    def toggle_pause(self):
        self.mpv.pause = not bool(getattr(self.mpv, "pause", False))

    def set_volume(self, vol):
        self.mpv.volume = vol


# ============================================================================
#                           Player Window
# ============================================================================
class PlayerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="GTK4 MPV CPU Player v3.9")
        self.set_default_size(900, 600)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)

        header = Adw.HeaderBar()
        box.append(header)

        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.connect("clicked", self.on_open)
        header.pack_start(open_btn)

        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_btn.connect("clicked", self.on_play)
        header.pack_start(self.play_btn)

        vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        vol.set_value(50)
        vol.connect("value-changed", lambda s: self.video.set_volume(s.get_value()))
        header.pack_end(vol)

        self.video = MpvCPUWidget()
        box.append(self.video)

    def on_open(self, *_):
        dlg = Gtk.FileDialog()
        dlg.open(self, None, self._file_chosen)

    def _file_chosen(self, dlg, res):
        try:
            f = dlg.open_finish(res)
            self.video.load(f.get_path())
            self.play_btn.set_icon_name("media-playback-pause-symbolic")
        except:
            pass

    def on_play(self, *_):
        self.video.toggle_pause()
        icon = (
            "media-playback-pause-symbolic"
            if not self.video.mpv.pause else "media-playback-start-symbolic"
        )
        self.play_btn.set_icon_name(icon)


# ============================================================================
#                            Application
# ============================================================================
class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.CPUv39")

    def do_activate(self):
        win = PlayerWindow(self)
        win.present()


if __name__ == "__main__":
    app = App()
    app.run(sys.argv)
