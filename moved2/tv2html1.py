#!/usr/bin/env python3
# GTK4 + libadwaita TextView that pastes HTML with formatting (basic tags) from clipboard,
# and opens local HTML files. Prefers HTML MIME; falls back to plain text.

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango
import re
from html.parser import HTMLParser


# ---------------------------- HTML -> Gtk.TextBuffer parser ----------------------------
class HTMLToTextParser(HTMLParser):
    def __init__(self, text_buffer):
        super().__init__()
        self.text_buffer = text_buffer
        self.tag_stack = []
        self.current_text = ""
        self._ensure_tags()

    def _ensure_tags(self):
        tag_table = self.text_buffer.get_tag_table()
        if tag_table.lookup("bold"):
            return
        bold = Gtk.TextTag.new("bold"); bold.set_property("weight", Pango.Weight.BOLD); tag_table.add(bold)
        italic = Gtk.TextTag.new("italic"); italic.set_property("style", Pango.Style.ITALIC); tag_table.add(italic)
        underline = Gtk.TextTag.new("underline"); underline.set_property("underline", Pango.Underline.SINGLE); tag_table.add(underline)
        strike = Gtk.TextTag.new("strikethrough"); strike.set_property("strikethrough", True); tag_table.add(strike)
        for i in range(1, 7):
            h = Gtk.TextTag.new(f"h{i}")
            h.set_property("weight", Pango.Weight.BOLD)
            h.set_property("scale", 2.0 - (i - 1) * 0.2)
            tag_table.add(h)
        p = Gtk.TextTag.new("paragraph"); p.set_property("pixels-below-lines", 12); tag_table.add(p)
        code = Gtk.TextTag.new("code"); code.set_property("family", "monospace"); code.set_property("background", "#f5f5f5"); tag_table.add(code)

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        map_ = {
            'b': 'bold', 'strong': 'bold',
            'i': 'italic', 'em': 'italic',
            'u': 'underline',
            'strike': 'strikethrough', 's': 'strikethrough', 'del': 'strikethrough',
            'code': 'code', 'pre': 'code',
            'p': 'paragraph'
        }
        if t in [f'h{i}' for i in range(1, 7)]:
            self.tag_stack.append(t)
        elif t in map_:
            self.tag_stack.append(map_[t])
        elif t == 'br':
            self.insert_current_text()
            self.insert_text('\n')

    def handle_endtag(self, tag):
        t = tag.lower()
        map_ = {
            'b': 'bold', 'strong': 'bold',
            'i': 'italic', 'em': 'italic',
            'u': 'underline',
            'strike': 'strikethrough', 's': 'strikethrough', 'del': 'strikethrough',
            'code': 'code', 'pre': 'code',
            'p': 'paragraph'
        }
        self.insert_current_text()
        if t in [f'h{i}' for i in range(1, 7)]:
            if t in self.tag_stack:
                self.tag_stack.remove(t)
        elif t in map_:
            mt = map_[t]
            if mt in self.tag_stack:
                self.tag_stack.remove(mt)
        if t in ['p'] + [f'h{i}' for i in range(1, 7)]:
            self.insert_text('\n\n')

    def handle_data(self, data):
        cleaned = re.sub(r'\s+', ' ', data)
        if cleaned:
            self.current_text += cleaned

    def insert_current_text(self):
        if self.current_text.strip():
            self.insert_text(self.current_text)
            self.current_text = ""

    def insert_text(self, text):
        if not text:
            return
        end_iter = self.text_buffer.get_end_iter()
        if self.tag_stack:
            tag_table = self.text_buffer.get_tag_table()
            tags = [tag_table.lookup(n) for n in self.tag_stack if tag_table.lookup(n)]
            if tags:
                self.text_buffer.insert_with_tags(end_iter, text, *tags)
                return
        self.text_buffer.insert(end_iter, text)

    def close(self):
        self.insert_current_text()
        super().close()


# ---------------------------- App ----------------------------
class HTMLTextViewApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.htmltextview")
        self.connect("activate", self.on_activate)

    # ---------- UI ----------
    def on_activate(self, app):
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title("HTML TextView")
        self.window.set_default_size(900, 700)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        open_button = Gtk.Button(icon_name="document-open-symbolic")
        open_button.set_tooltip_text("Open HTML File")
        open_button.connect("clicked", self.on_open_file)
        header.pack_start(open_button)

        paste_button = Gtk.Button(icon_name="edit-paste-symbolic")
        paste_button.set_tooltip_text("Paste HTML from Clipboard")
        paste_button.connect("clicked", self.on_paste_html)
        header.pack_start(paste_button)

        clear_button = Gtk.Button(icon_name="edit-clear-symbolic")
        clear_button.set_tooltip_text("Clear")
        clear_button.connect("clicked", self.on_clear_text)
        header.pack_end(clear_button)

        toolbar_view.add_top_bar(header)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_left_margin(12)
        self.textview.set_right_margin(12)
        self.textview.set_top_margin(12)
        self.textview.set_bottom_margin(12)
        self.text_buffer = self.textview.get_buffer()
        self._ensure_tags_once()

        scrolled.set_child(self.textview)
        main_box.append(scrolled)

        self.status_page = Adw.StatusPage(title="Ready", description="Open an HTML file or paste HTML content")
        self.status_page.set_visible(True)
        main_box.remove(scrolled)
        main_box.append(self.status_page)

        self.scrolled = scrolled
        self.main_box = main_box
        toolbar_view.set_content(main_box)
        self.window.set_content(toolbar_view)
        self.window.present()

    def _ensure_tags_once(self):
        tag_table = self.text_buffer.get_tag_table()
        if tag_table.lookup("bold"):
            return
        bold = Gtk.TextTag.new("bold"); bold.set_property("weight", Pango.Weight.BOLD); tag_table.add(bold)
        italic = Gtk.TextTag.new("italic"); italic.set_property("style", Pango.Style.ITALIC); tag_table.add(italic)
        underline = Gtk.TextTag.new("underline"); underline.set_property("underline", Pango.Underline.SINGLE); tag_table.add(underline)
        strike = Gtk.TextTag.new("strikethrough"); strike.set_property("strikethrough", True); tag_table.add(strike)
        for i in range(1, 7):
            h = Gtk.TextTag.new(f"h{i}")
            h.set_property("weight", Pango.Weight.BOLD)
            h.set_property("scale", 2.0 - (i - 1) * 0.2)
            tag_table.add(h)
        p = Gtk.TextTag.new("paragraph"); p.set_property("pixels-below-lines", 12); tag_table.add(p)
        code = Gtk.TextTag.new("code"); code.set_property("family", "monospace"); code.set_property("background", "#f5f5f5"); tag_table.add(code)

    def show_textview(self):
        if self.status_page.get_visible():
            self.main_box.remove(self.status_page)
            self.main_box.append(self.scrolled)
            self.status_page.set_visible(False)

    def show_status(self, title, description):
        if not self.status_page.get_visible():
            self.main_box.remove(self.scrolled)
            self.main_box.append(self.status_page)
            self.status_page.set_visible(True)
        self.status_page.set_title(title)
        self.status_page.set_description(description)

    # ---------- File open ----------
    def on_open_file(self, _button):
        dialog = Gtk.FileChooserNative.new(
            title="Open HTML File",
            parent=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Open",
            cancel_label="Cancel",
        )
        html_filter = Gtk.FileFilter(); html_filter.set_name("HTML Files")
        html_filter.add_mime_type("text/html"); html_filter.add_pattern("*.html"); html_filter.add_pattern("*.htm")
        dialog.add_filter(html_filter)
        any_filter = Gtk.FileFilter(); any_filter.set_name("All Files"); any_filter.add_pattern("*")
        dialog.add_filter(any_filter)

        dialog.connect("response", self.on_file_dialog_response)
        dialog.show()

    def on_file_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                self.load_html_file(file)
        dialog.destroy()

    def load_html_file(self, file: Gio.File):
        try:
            file.load_contents_async(None, self._on_file_loaded, None)
        except Exception as e:
            self.show_error(f"Error opening file: {e}")

    def _on_file_loaded(self, file, result, _user_data):
        try:
            ok, contents, _etag = file.load_contents_finish(result)
            if ok:
                html_content = contents.decode('utf-8', errors='replace')
                self.parse_and_display_html(html_content)
            else:
                self.show_error("Failed to load file")
        except Exception as e:
            self.show_error(f"Error reading file: {e}")

    # ---------- Clipboard paste (HTML first) ----------
    def on_paste_html(self, _button):
        cb = Gdk.Display.get_default().get_clipboard()
        # Prefer HTML, include common alternates; keep plain text as fallback
        fmts = Gdk.ContentFormats.new([
            "text/html",
            "application/xhtml+xml",
            "text/_moz_htmlcontext",   # Firefox context (ignored by our parser)
            "text/_moz_htmlinfo",      # Firefox info (ignored)
            "text/rtf",
            "application/rtf",
            "text/plain",
        ])
        cb.read_async(fmts, GLib.PRIORITY_DEFAULT, None, self._on_clipboard_read, None)

    def _read_all_bytes(self, stream: Gio.InputStream, chunk=1 << 16):
        chunks = []
        while True:
            b = stream.read_bytes(chunk, None)
            data = b.get_data()
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)

    def _on_clipboard_read(self, cb: Gdk.Clipboard, res, _data):
        try:
            stream, mime = cb.read_finish(res)  # (Gio.InputStream, chosen_mime_type)
            raw = self._read_all_bytes(stream)
            mime_l = (mime or "").lower()
            text = raw.decode("utf-8", "replace")
            if "html" in mime_l:
                self.parse_and_display_html(text)
            else:
                self.text_buffer.set_text(text)
                self.show_textview()
        except Exception as e:
            self.show_error(f"Clipboard read failed: {e}")

    # ---------- HTML handling ----------
    def parse_and_display_html(self, html_content: str):
        try:
            body = self.extract_body_content(html_content)
            self.text_buffer.set_text("")
            parser = HTMLToTextParser(self.text_buffer)
            parser.feed(body)
            parser.close()
            self.show_textview()
            start_iter = self.text_buffer.get_start_iter()
            self.text_buffer.place_cursor(start_iter)
            self.textview.scroll_mark_onscreen(self.text_buffer.get_insert())
        except Exception as e:
            self.show_error(f"Error parsing HTML: {e}")

    def extract_body_content(self, html_content: str) -> str:
        m = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1)
        head_removed = re.sub(r'<head[^>]*>.*?</head>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', head_removed, flags=re.IGNORECASE)
        cleaned = re.sub(r'</?html[^>]*>', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    # ---------- misc ----------
    def on_clear_text(self, _button):
        self.text_buffer.set_text("")
        self.show_status("Ready", "Open an HTML file or paste HTML content")

    def show_error(self, message: str):
        self.show_status("Error", message)


def main():
    app = HTMLTextViewApp()
    return app.run()


if __name__ == "__main__":
    main()

