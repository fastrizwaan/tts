#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, WebKit, GLib

Adw.init()


class HtmlViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(900, 700)
        self.set_title("HTML Horizontal Scrolling Viewer")

        # ---------------- ToolbarView + Header ----------------
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar(title_widget=Gtk.Label(label="HTML Horizontal Scrolling Viewer"))
        toolbar_view.add_top_bar(header)

        # ---------------- WebView in ScrolledWindow ----------------
        self.webview = WebKit.WebView()
        self.load_html()

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.webview)
        # Allow both horizontal and vertical scrolling
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_kinetic_scrolling(True)
        scroller.set_overlay_scrolling(False)

        # Make the scroller expand to fill available space
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)

        # ---------------- Margin Controls ----------------
        control_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        control_box.set_margin_top(12)
        control_box.set_margin_bottom(12)
        control_box.set_margin_start(12)
        control_box.set_margin_end(12)

        # Add expand=False to prevent control_box from taking too much space
        control_box.set_hexpand(False)
        control_box.set_vexpand(False)

        self.top_adj = Gtk.Adjustment(value=40, lower=0, upper=300, step_increment=5)
        self.bottom_adj = Gtk.Adjustment(value=40, lower=0, upper=300, step_increment=5)
        self.left_adj = Gtk.Adjustment(value=80, lower=0, upper=300, step_increment=5)
        self.right_adj = Gtk.Adjustment(value=150, lower=0, upper=300, step_increment=5)

        self.top_spin = Adw.SpinRow(title="Top margin", adjustment=self.top_adj)
        self.bottom_spin = Adw.SpinRow(title="Bottom margin", adjustment=self.bottom_adj)
        self.left_spin = Adw.SpinRow(title="Left margin", adjustment=self.left_adj)
        self.right_spin = Adw.SpinRow(title="Right margin", adjustment=self.right_adj)

        for spin in (self.top_spin, self.bottom_spin, self.left_spin, self.right_spin):
            spin.connect("notify::value", self.on_margin_changed)
            control_box.append(spin)

        # ---------------- Split Layout ----------------
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(control_box)
        paned.set_end_child(scroller)

        # Set initial position and resize/end expand properties
        paned.set_position(250)  # Set initial split position (pixels from left)
        # Ensure the end child (webview area) gets the expand behavior
        paned.set_hexpand(True)
        paned.set_vexpand(True)

        toolbar_view.set_content(paned)

    # ---------------- HTML Loader ----------------
    def load_html(self):
        html = """
        <html>
        <head>
        <style>
        body {
            margin: var(--top-margin,40px)
                    var(--right-margin,150px)
                    var(--bottom-margin,40px)
                    var(--left-margin,80px);
            /* Removed column-width and column-gap */
            background: #fdfdfd;
            color: #333;
            font-family: sans-serif;
            /* Ensure content can cause horizontal scroll if needed */
            white-space: nowrap;
        }
        /* Style paragraphs to be wide enough to trigger horizontal scrolling */
        p {
            display: inline-block;
            width: 2000px; /* Very wide paragraph to ensure horizontal scroll */
            margin-right: 20px; /* Space between paragraphs */
        }
        </style>
        </head>
        <body>
        """ + "<p>" + ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 120) + "</p>" * 3 + """</body></html>"""
        self.webview.load_html(html, "file:///")

    # ---------------- Margin Logic ----------------
    def on_margin_changed(self, *args):
        # throttle updates slightly to avoid flooding JS
        GLib.idle_add(self.update_margins)

    def eval_js(self, script: str):
        # WebKitGTK 6 requires all 8 arguments
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None, None)

    def update_margins(self):
        top = self.top_spin.get_value()
        bottom = self.bottom_spin.get_value()
        left = self.left_spin.get_value()
        right = self.right_spin.get_value()

        script = f"""
            document.body.style.setProperty('--top-margin', '{top}px');
            document.body.style.setProperty('--bottom-margin', '{bottom}px');
            document.body.style.setProperty('--left-margin', '{left}px');
            document.body.style.setProperty('--right-margin', '{right}px');
        """
        self.eval_js(script)


class HtmlViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.htmlviewer.example")

    def do_activate(self, *args):
        win = HtmlViewerWindow(self)
        win.present()


if __name__ == "__main__":
    app = HtmlViewerApp()
    app.run([])
