import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib
import mpv
import sys
import os

class VideoPlayerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Libadwaita Video Player")

        # Use a main Box layout for top controls and video area
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # --- Header Bar ---
        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        # Open File Button in Header
        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        # Play/Pause Button in Header
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_button.connect("clicked", self.on_play_clicked)
        header_bar.pack_start(self.play_button)

        # --- Video Area (DrawingArea for mpv) ---
        # Note: For GLArea integration, the process is more complex and often involves
        # passing an EGL surface handle to mpv. Using DrawingArea with python-mpv
        # is simpler for a basic implementation.
        self.video_area = Gtk.DrawingArea()
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)
        # Set a minimum size to prevent the area from collapsing
        self.video_area.set_size_request(640, 360)
        main_box.append(self.video_area)

        # --- Controls Bar ---
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls_box.set_margin_top(10)
        controls_box.set_margin_bottom(10)
        controls_box.set_margin_start(10)
        controls_box.set_margin_end(10)
        main_box.append(controls_box)

        # Seek Backward Button
        seek_back_button = Gtk.Button(icon_name="media-seek-backward-symbolic")
        seek_back_button.connect("clicked", self.on_seek_backward_clicked)
        controls_box.append(seek_back_button)

        # Play/Pause Button (Center, larger)
        self.center_play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.center_play_button.set_valign(Gtk.Align.CENTER)
        self.center_play_button.set_halign(Gtk.Align.CENTER)
        self.center_play_button.set_size_request(60, 60) # Make it larger
        self.center_play_button.connect("clicked", self.on_play_clicked)
        controls_box.append(self.center_play_button)

        # Seek Forward Button
        seek_fwd_button = Gtk.Button(icon_name="media-seek-forward-symbolic")
        seek_fwd_button.connect("clicked", self.on_seek_forward_clicked)
        controls_box.append(seek_fwd_button)

        # --- Status Bar ---
        self.status_label = Gtk.Label(label="No file loaded")
        # Pack into a box to allow for potential other items later
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.append(self.status_label)
        status_box.set_margin_top(5)
        status_box.set_margin_bottom(5)
        status_box.set_margin_start(10)
        status_box.set_margin_end(10)
        main_box.append(status_box)

        # --- Initialize mpv ---
        # Get the X11 window ID or native handle after widget is realized
        # This needs to happen after the window is shown
        self.video_area.connect("realize", self.on_video_area_realize)
        self.mpv_player = None # Initialize player object

    def on_video_area_realize(self, drawing_area):
        """Called when the DrawingArea is mapped to a native window."""
        if self.mpv_player is None:
            native = drawing_area.get_native()
            if native:
                # Get the native window ID (platform-specific)
                # For X11, get the XID
                surface = native.get_surface()
                # The exact method to get the ID can vary depending on the backend
                # This is a common way for X11 and potentially Wayland with XWayland
                try:
                    # Attempt to get the X11 window ID (requires X11 backend)
                    # This might fail on pure Wayland
                    gdk_window = drawing_area.get_surface() # Gets the GDK surface
                    x11_window_id = gdk_window.get_xid() # Gets the XID if using X11
                    self._init_mpv_with_id(x11_window_id)
                except AttributeError:
                    # Fallback: try for Wayland or other methods if X11 fails
                    # Getting a native handle for mpv on Wayland is more involved
                    # For this basic example, we assume X11 compatibility for python-mpv
                    print("Could not get native window ID, likely not on X11.")
                    print("python-mpv typically requires X11 or specific setup for GTK4.")
                    self.status_label.set_text("Error: Could not initialize video output (X11 required for python-mpv).")

    def _init_mpv_with_id(self, window_id):
        """Initialize the mpv player with the native window ID."""
        try:
            self.mpv_player = mpv.MPV(
                vo='x11', # Use X11 video output
                wid=str(window_id), # Pass the window ID as a string
                log_handler=print, # Optional: handle logs
                loglevel='info'
            )
            self.mpv_player.observe_property('idle-active', self._on_idle_change)
            self.mpv_player.observe_property('playback-time', self._on_time_change)
            self.mpv_player.observe_property('duration', self._on_duration_change)
            self.status_label.set_text("Player initialized. Open a file to play.")
            print(f"MPV initialized with window ID: {window_id}")
        except Exception as e:
            print(f"Failed to initialize MPV: {e}")
            self.status_label.set_text(f"Error initializing player: {e}")

    def _on_idle_change(self, name, value):
        # 'idle-active' becomes True when playback stops or fails
        if value:
            self.play_button.set_icon_name("media-playback-start-symbolic")
            self.center_play_button.set_icon_name("media-playback-start-symbolic")
            # Optionally update status further
            # Check if playback ended due to EOF or error
            if hasattr(self.mpv_player, 'eof') and self.mpv_player.eof:
                 self.status_label.set_text("Playback finished.")
            else:
                 self.status_label.set_text("Playback stopped.")

    def _on_time_change(self, name, value):
        # Update status label with current time (basic implementation)
        # A more advanced player would have a proper time slider
        if value is not None:
            # Format time as MM:SS
            mins = int(value // 60)
            secs = int(value % 60)
            time_str = f"{mins:02d}:{secs:02d}"
            # Get duration if available
            duration = getattr(self.mpv_player, 'duration', None)
            if duration:
                d_mins = int(duration // 60)
                d_secs = int(duration % 60)
                duration_str = f" / {d_mins:02d}:{d_secs:02d}"
                time_str += duration_str
            # Update status label with time info
            # Note: This overwrites other status messages quickly
            # A better UI might have a dedicated time display
            # For now, append to the status if it doesn't already contain time
            current_status = self.status_label.get_text()
            if not ":" in current_status.split(" / ")[0]: # Simple check to avoid overwriting time
                 self.status_label.set_text(f"Playing: {time_str}")
        else:
             # Reset status if time is unknown
             if "Playing:" in self.status_label.get_text():
                 self.status_label.set_text("Playing... (time unknown)")

    def _on_duration_change(self, name, value):
        # Duration is available, can update UI elements if needed
        if value is not None:
            mins = int(value // 60)
            secs = int(value % 60)
            duration_str = f"Duration: {mins:02d}:{secs:02d}"
            print(f"Media duration: {duration_str}")
            # Could update a dedicated duration label or slider range here

    def on_open_clicked(self, button):
        """Open a file chooser dialog."""
        dialog = Gtk.FileChooserNative.new(
            "Choose a video file",
            self,
            Gtk.FileChooserAction.OPEN,
            "Open",
            "Cancel"
        )
        # Add common video filters
        filter_video = Gtk.FileFilter()
        filter_video.set_name("Video files")
        filter_video.add_mime_type("video/*")
        dialog.add_filter(filter_video)

        filter_any = Gtk.FileFilter()
        filter_any.set_name("All files")
        filter_any.add_pattern("*")
        dialog.add_filter(filter_any)

        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            filename = file.get_path()
            if filename and os.path.isfile(filename):
                if self.mpv_player:
                    try:
                        self.mpv_player.loadfile(filename)
                        self.status_label.set_text(f"Loading: {os.path.basename(filename)}")
                        # Reset play button state assumption
                        self.play_button.set_icon_name("media-playback-start-symbolic")
                        self.center_play_button.set_icon_name("media-playback-start-symbolic")
                    except Exception as e:
                        print(f"Error loading file: {e}")
                        self.status_label.set_text(f"Error loading file: {e}")
                else:
                    self.status_label.set_text("Player not initialized.")
            else:
                print("Invalid file selected.")
        dialog.destroy() # Clean up the dialog

    def on_play_clicked(self, button):
        if self.mpv_player:
            try:
                paused = self.mpv_player.pause
                self.mpv_player.pause = not paused
                if self.mpv_player.pause:
                    self.play_button.set_icon_name("media-playback-start-symbolic")
                    self.center_play_button.set_icon_name("media-playback-start-symbolic")
                    self.status_label.set_text("Paused.")
                else:
                    self.play_button.set_icon_name("media-playback-pause-symbolic")
                    self.center_play_button.set_icon_name("media-playback-pause-symbolic")
                    self.status_label.set_text("Playing...")
            except AttributeError:
                # Handle case where pause property isn't available initially
                try:
                    self.mpv_player.pause = False # Start playing
                    self.play_button.set_icon_name("media-playback-pause-symbolic")
                    self.center_play_button.set_icon_name("media-playback-pause-symbolic")
                    self.status_label.set_text("Playing...")
                except Exception as e:
                    print(f"Error toggling play/pause: {e}")
                    self.status_label.set_text(f"Play/Pause Error: {e}")
        else:
            self.status_label.set_text("Player not initialized.")

    def on_seek_backward_clicked(self, button):
        if self.mpv_player:
            try:
                self.mpv_player.seek(-10) # Seek backward 10 seconds
            except Exception as e:
                print(f"Error seeking backward: {e}")

    def on_seek_forward_clicked(self, button):
        if self.mpv_player:
            try:
                self.mpv_player.seek(10) # Seek forward 10 seconds
            except Exception as e:
                print(f"Error seeking forward: {e}")

class VideoPlayerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.VideoPlayer",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        win = VideoPlayerWindow(app)
        win.present()

# --- Main Execution ---
if __name__ == "__main__":
    app = VideoPlayerApplication()
    app.run(sys.argv)