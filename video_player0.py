#!/usr/bin/env python3

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, GObject

import mpv
import time

# ------------------------
# Utility
# ------------------------

def fmt_time(sec):
    if sec is None:
        return "00:00"
    sec = int(sec)
    m, s = divmod(sec, 60)
    return f"{m:02d}:{s:02d}"


# ------------------------
# MPV Player Widget
# ------------------------

class MPVPlayer(Gtk.GLArea):
    __gtype_name__ = "MPVPlayer"

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)

        # mpv instance (libmpv backend)
        self.mpv = mpv.MPV(
            vo="gpu",
            gpu_api="opengl",
            opengl_cb=True,
            log_handler=self._on_mpv_log
        )

        # GLArea must not auto-clear
        self.set_auto_render(False)
        self.set_has_depth_buffer(False)
        self.set_has_stencil_buffer(False)

        # Gtk.GLArea lifecycle
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("render", self._on_render)

        # Redraw callback from mpv
        self.mpv.opengl_cb_set_update_callback(self._on_mpv_redraw)

        # Integrate mpv event loop
        fd = self.mpv._event_handle
        GLib.io_add_watch(fd, GLib.IO_IN, self._on_mpv_events)

    # -------- GLArea lifecycle --------

    def _on_realize(self, area):
        ctx = self.get_context()
        self.make_current()
        self.mpv.opengl_cb_init_gl()

    def _on_unrealize(self, area):
        self.make_current()
        self.mpv.opengl_cb_uninit_gl()

    # -------- Rendering --------

    def _on_render(self, area, ctx):
        self.make_current()
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        self.mpv.opengl_cb_draw(w, h)
        return True

    # mpv requests redraw
    def _on_mpv_redraw(self):
        self.queue_render()

    # -------- Events --------

    def _on_mpv_events(self, source, cond):
        try:
            event = self.mpv.wait_event(0)
            if event is not None and event.event_id != mpv.MpvEventID.NONE:
                pass
        except Exception:
            pass
        return True

    # -------- Logging --------

    def _on_mpv_log(self, level, prefix, text):
        # optional: print(level, prefix, text)
        pass


# ------------------------
# Main Window
# ------------------------

class PlayerWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(900, 520)
        self.set_title("Daikhan Clone")

        self.player = MPVPlayer()

        # --------------------------
        # HEADERBAR
        # --------------------------

        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Gtk.Label(label="Title – Artist"))
        self.set_titlebar(self.header)

        # --------------------------
        # WHITE BACKGROUND LAYER
        # --------------------------

        white_bg = Gtk.Box()
        white_bg.set_css_classes(["white-background"])

        # --------------------------
        # BOTTOM CONTROL BAR
        # --------------------------

        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_btn.connect("clicked", self._toggle_play)

        self.position_label = Gtk.Label(label="00:00")
        self.duration_label = Gtk.Label(label="00:00")

        self.slider = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self.slider.set_hexpand(True)
        self.slider.connect("change-value", self._on_slider_move)
        self._user_dragging = False

        self.volume_btn = Gtk.Button(icon_name="audio-volume-high-symbolic")

        # Layout
        control_bar = Gtk.Box(spacing=12)
        control_bar.set_margin_top(8)
        control_bar.set_margin_bottom(8)
        control_bar.set_margin_start(12)
        control_bar.set_margin_end(12)

        control_bar.append(self.play_btn)
        control_bar.append(self.position_label)
        control_bar.append(self.slider)
        control_bar.append(self.duration_label)
        control_bar.append(self.volume_btn)

        # --------------------------
        # OVERLAY (white bg → mpv → controls)
        # --------------------------

        overlay = Gtk.Overlay()
        overlay.set_child(self.player)

        overlay.add_overlay(white_bg)
        overlay.set_overlay_pass_through(white_bg, True)     # white background sits behind

        overlay.add_overlay(control_bar)

        self.set_content(overlay)

        # --------------------------
        # mpv property tracking
        # --------------------------

        self.player.mpv.observe_property("time-pos", self._on_timepos)
        self.player.mpv.observe_property("duration", self._on_duration)
        self.player.mpv.observe_property("pause", self._on_pause)

        # Demo: load a file on startup
        # self.player.mpv.command("loadfile", "/path/to/video.mkv")

    # --------------------------
    # Playback control handlers
    # --------------------------

    def _toggle_play(self, *a):
        p = self.player.mpv.get_property("pause")
        self.player.mpv.set_property("pause", not p)

    def _on_slider_move(self, slider, scroll, value):
        if not self._user_dragging:
            return
        dur = self.player.mpv.get_property("duration")
        if dur:
            pos = (value / 100) * dur
            self.player.mpv.command("seek", pos, "absolute")

    # --------------------------
    # mpv property updates
    # --------------------------

    def _on_timepos(self, name, value):
        if value is None:
            return
        dur = self.player.mpv.get_property("duration")
        self.position_label.set_text(fmt_time(value))

        if dur:
            pct = (value / dur) * 100
            if not self._user_dragging:
                self.slider.set_value(pct)

    def _on_duration(self, name, value):
        if value:
            self.duration_label.set_text(fmt_time(value))

    def _on_pause(self, name, value):
        if value:
            self.play_btn.set_icon_name("media-playback-start-symbolic")
        else:
            self.play_btn.set_icon_name("media-playback-pause-symbolic")


# ------------------------
# Main Application
# ------------------------

class PlayerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.daikhanclone",
                         flags=GObject.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = PlayerWindow(self)
        win.present()


# ------------------------

if __name__ == "__main__":
    app = PlayerApp()
    app.run()

