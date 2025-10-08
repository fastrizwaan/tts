# utils.py
import re
import os
import tempfile
import hashlib
import traceback
from gi.repository import GLib, GdkPixbuf, Gdk
import cairo

def highlight_markup(text: str, query: str) -> str:
    if not query:
        return GLib.markup_escape_text(text or "")
    q = re.escape(query)
    parts = []
    last = 0
    esc_text = text or ""
    for m in re.finditer(q, esc_text, flags=re.IGNORECASE):
        start, end = m.start(), m.end()
        parts.append(GLib.markup_escape_text(esc_text[last:start]))
        match = GLib.markup_escape_text(esc_text[start:end])
        parts.append(f'<span background="#ffd54f" foreground="#000000"><b>{match}</b></span>')
        last = end
    parts.append(GLib.markup_escape_text(esc_text[last:]))
    return "".join(parts)

def sanitize_path(path):
    if not path:
        return None
    normalized = os.path.normpath(path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        return None
    if ".." in normalized.split(os.sep):
        return None
    return normalized

def create_rounded_cover_texture(cover_path, width, height, radius=10):
    try:
        original_pixbuf = GdkPixbuf.Pixbuf.new_from_file(cover_path)
        pixbuf = original_pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        context.arc(radius, radius, radius, 3.14159, 3 * 3.14159 / 2)
        context.arc(width - radius, radius, radius, 3 * 3.14159 / 2, 0)
        context.arc(width - radius, height - radius, radius, 0, 3.14159 / 2)
        context.arc(radius, height - radius, radius, 3.14159 / 2, 3.14159)
        context.close_path()
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.clip()
        context.paint()
        surface_bytes = surface.get_data()
        gbytes = GLib.Bytes.new(surface_bytes)
        texture = Gdk.MemoryTexture.new(
            width, height,
            Gdk.MemoryFormat.B8G8R8A8,
            gbytes,
            surface.get_stride()
        )
        return texture
    except Exception as e:
        print(f"Error creating rounded texture: {e}")
        return None
