import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, Gdk, Gio, GLib
import ctypes
import ctypes.util
import sys
import locale

locale.setlocale(locale.LC_NUMERIC, 'C')
print("Set LC_NUMERIC locale to C for libmpv.")

libmpv_path = ctypes.util.find_library("mpv")
if not libmpv_path:
    print("Error: libmpv not found on the system.")
    sys.exit(1)

try:
    libmpv = ctypes.CDLL(libmpv_path)
    print(f"Successfully loaded libmpv from: {libmpv_path}")
except OSError as e:
    print(f"Error loading libmpv: {e}")
    sys.exit(1)

c_int = ctypes.c_int
c_char_p = ctypes.c_char_p
c_void_p = ctypes.c_void_p
c_uint64 = ctypes.c_uint64

libmpv.mpv_client_api_version.argtypes = []
libmpv.mpv_client_api_version.restype = c_uint64

libmpv.mpv_create.argtypes = []
libmpv.mpv_create.restype = c_void_p

libmpv.mpv_initialize.argtypes = [c_void_p]
libmpv.mpv_initialize.restype = c_int

libmpv.mpv_render_context_create.argtypes = [ctypes.POINTER(ctypes.POINTER(c_void_p)), c_void_p, c_void_p]
libmpv.mpv_render_context_create.restype = c_int

libmpv.mpv_render_context_render.argtypes = [c_void_p, c_void_p]
libmpv.mpv_render_context_render.restype = None

libmpv.mpv_render_context_free.argtypes = [c_void_p]
libmpv.mpv_render_context_free.restype = None

libmpv.mpv_destroy.argtypes = [c_void_p]
libmpv.mpv_destroy.restype = None

MPV_RENDER_PARAM_API_TYPE = 1
MPV_RENDER_PARAM_OPENGL_FBO = 257
MPV_RENDER_API_TYPE_OPENGL = b"opengl"
MPV_RENDER_PARAM_INVALID = 0

class MpvRenderParam(ctypes.Structure):
    _fields_ = [("type", c_int), ("data", c_void_p)]

class VideoPlayerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Libadwaita Video Player (Ctypes Attempt)")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.connect("clicked", self.on_open_clicked)
        header_bar.pack_start(open_button)

        self.gl_area = Gtk.GLArea()
        # self.gl_area.set_use_es(False) # Removed: deprecated
        self.gl_area.set_hexpand(True)
        self.gl_area.set_vexpand(True)
        self.gl_area.set_size_request(800, 450)

        self.gl_area.connect("realize", self.on_gl_area_realize)
        self.gl_area.connect("render", self.on_gl_area_render)

        main_box.append(self.gl_area)

        self.status_label = Gtk.Label(label="Initializing libmpv...")
        main_box.append(self.status_label)

        self.mpv_handle = None
        self.mpv_render_ctx = None
        self.initialized = False
        self.context_creation_scheduled = False # Flag to prevent multiple attempts

    def on_gl_area_realize(self, area):
        print("GLArea realized.")
        area.make_current() # Make the context current

        self.mpv_handle = libmpv.mpv_create()
        if not self.mpv_handle:
            print("Failed to create mpv client handle.")
            self.status_label.set_text("Error: Could not create mpv handle.")
            return

        print("Mpv handle created successfully.")

        if libmpv.mpv_initialize(self.mpv_handle) < 0:
            print("Failed to initialize mpv.")
            libmpv.mpv_destroy(self.mpv_handle)
            self.mpv_handle = None
            self.status_label.set_text("Error: Could not initialize mpv.")
            return

        print("Mpv initialized successfully.")
        # --- Schedule render context creation for the next idle cycle ---
        if not self.context_creation_scheduled:
            GLib.idle_add(self.create_mpv_render_context)
            self.context_creation_scheduled = True
        else:
            print("Render context creation already scheduled.")


    def create_mpv_render_context(self):
        """Called by GLib.idle_add after realize."""
        print("Attempting to create mpv render context from idle callback.")
        # Ensure the GL context is current again if necessary before setup
        # Usually, the context associated with the GLArea should still be current
        # or the render callback will make it current. Let's assume it's handled by GTK/render loop.
        # The key is that this runs *after* the realize signal processing is complete.

        # Prepare parameters: API Type = OpenGL
        api_type_param = MpvRenderParam()
        api_type_param.type = MPV_RENDER_PARAM_API_TYPE
        api_type_param.data = ctypes.cast(MPV_RENDER_API_TYPE_OPENGL, c_void_p)

        # Null-terminate the parameter list
        null_param = MpvRenderParam()
        null_param.type = MPV_RENDER_PARAM_INVALID
        null_param.data = None

        # Pack params into an array
        params_array = (MpvRenderParam * 2)()
        params_array[0] = api_type_param
        params_array[1] = null_param

        # Create the context pointer
        ctx_ptr_type = ctypes.POINTER(c_void_p)
        ctx_ptr = ctx_ptr_type()

        res = libmpv.mpv_render_context_create(
            ctypes.byref(ctx_ptr),
            self.mpv_handle,
            params_array
        )

        if res < 0:
            print(f"Failed to create mpv render context in idle callback: {res}")
            libmpv.mpv_destroy(self.mpv_handle)
            self.mpv_handle = None
            self.status_label.set_text(f"Error: Could not create render context ({res}). Check console.")
            return # Do not set initialized flag

        self.mpv_render_ctx = ctx_ptr.contents.value
        self.initialized = True
        print("MPV render context created successfully in idle callback.")
        self.status_label.set_text("MPV initialized. Ready to load video.")


    def on_gl_area_render(self, area, context):
        if not self.initialized or not self.mpv_render_ctx:
            print("Render called but MPV is not ready.")
            return False

        try:
            from OpenGL.GL import glGetIntegerv, GL_FRAMEBUFFER_BINDING
            fbo_id = ctypes.c_int()
            glGetIntegerv(GL_FRAMEBUFFER_BINDING, fbo_id)
            print(f"Current GL FBO ID: {fbo_id.value}")
        except ImportError:
            print("PyOpenGL not found. Cannot get FBO ID.")
            return False
        except Exception as e:
            print(f"Error getting FBO ID: {e}")
            return False

        fbo_id_val = ctypes.c_int(fbo_id.value)
        fbo_param = MpvRenderParam()
        fbo_param.type = MPV_RENDER_PARAM_OPENGL_FBO
        fbo_param.data = ctypes.cast(ctypes.pointer(fbo_id_val), c_void_p)

        null_param = MpvRenderParam()
        null_param.type = MPV_RENDER_PARAM_INVALID
        null_param.data = None

        params_array = (MpvRenderParam * 2)()
        params_array[0] = fbo_param
        params_array[1] = null_param

        try:
            libmpv.mpv_render_context_render(self.mpv_render_ctx, params_array)
            print("Rendered frame to GLArea.")
        except Exception as e:
            print(f"Error during mpv render: {e}")
            return False

        return True

    def on_open_clicked(self, button):
        print("Open clicked - would load file here using libmpv command API.")

class VideoPlayerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.VideoPlayerCtypes",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        win = VideoPlayerWindow(app)
        win.present()

if __name__ == "__main__":
    app = VideoPlayerApplication()
    app.run(sys.argv)
