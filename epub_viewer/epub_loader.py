import os
import glob
import re
import urllib.parse
import shutil
import hashlib
from ebooklib import epub
from bs4 import BeautifulSoup
from gi.repository import GdkPixbuf
from .constants import LIB_COVER_W, LIB_COVER_H, COVERS_DIR
from .utils import sanitize_path

def extract_css(book, temp_dir):
    css_content = ""
    if not book:
        return css_content
    try:
        for item in book.get_items_of_type(ebooklib.ITEM_STYLE):
            try:
                css_content += item.get_content().decode("utf-8") + "\n"
            except Exception:
                pass
        if temp_dir and os.path.exists(temp_dir):
            for fn in ("flow0001.css", "core.css", "se.css", "style.css"):
                p = os.path.join(temp_dir, fn)
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                            css_content += fh.read() + "\n"
                    except Exception:
                        pass
    except Exception as e:
        print(f"Error extracting CSS: {e}")
    return css_content

def find_cover_via_opf(temp_dir, extracted_paths, image_names, image_basenames):
    if not temp_dir:
        return None, None
    lc_map = {p.lower(): p for p in (extracted_paths or [])}
    pattern = os.path.join(temp_dir, "**", "*.opf")
    opf_files = sorted(glob.glob(pattern, recursive=True))
    for opf in opf_files:
        try:
            with open(opf, "rb") as fh:
                raw = fh.read()
            soup = BeautifulSoup(raw, "xml")
            cover_id = None
            meta = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "meta" and tag.has_attr("name") and tag["name"].lower() == "cover")
            if meta and meta.has_attr("content"):
                cover_id = meta["content"]
            href = None
            if cover_id:
                item_tag = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("id") and tag["id"] == cover_id)
                if item_tag and item_tag.has_attr("href"):
                    href = item_tag["href"]
            if not href:
                item_prop = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("properties") and "cover-image" in tag["properties"])
                if item_prop and item_prop.has_attr("href"):
                    href = item_prop["href"]
            if not href:
                item_cover_href = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'cover.*\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                if item_cover_href and item_cover_href.has_attr("href"):
                    href = item_cover_href["href"]
            if not href:
                first_img = soup.find(lambda tag: hasattr(tag, 'name') and tag.name == "item" and tag.has_attr("href") and re.search(r'\.(jpe?g|png|gif|webp|svg)$', tag["href"], re.I))
                if first_img and first_img.has_attr("href"):
                    href = first_img["href"]
            if not href:
                continue
            opf_dir = os.path.dirname(opf)
            candidate_abs = os.path.normpath(os.path.join(opf_dir, urllib.parse.unquote(href)))
            candidate_abs = os.path.abspath(candidate_abs)
            candidate_abs2 = os.path.abspath(os.path.normpath(os.path.join(temp_dir, urllib.parse.unquote(href))))
            try:
                rel_from_temp = os.path.relpath(candidate_abs, temp_dir).replace(os.sep, "/")
            except Exception:
                rel_from_temp = os.path.basename(candidate_abs)
            variants = [rel_from_temp, os.path.basename(rel_from_temp)]
            for pfx in ("OEBPS/", "OPS/", "oebps/", "ops/"):
                variants.append(pfx + rel_from_temp)
                variants.append(pfx + os.path.basename(rel_from_temp))
            try:
                uq = urllib.parse.unquote(rel_from_temp)
                variants.append(uq)
                variants.append(os.path.basename(uq))
            except Exception:
                pass
            if os.path.exists(candidate_abs):
                return candidate_abs, None
            if os.path.exists(candidate_abs2):
                return candidate_abs2, None
            for v in variants:
                found = lc_map.get(v.lower())
                if found:
                    abs_p = os.path.abspath(os.path.join(temp_dir, found))
                    return abs_p, None
                if v in image_names:
                    return None, image_names[v]
                bn = os.path.basename(v)
                if bn in image_basenames:
                    return None, image_basenames[bn][0]
            bn = os.path.basename(href)
            for p in extracted_paths:
                if os.path.basename(p).lower() == bn.lower():
                    abs_p = os.path.abspath(os.path.join(temp_dir, p))
                    return abs_p, None
        except Exception:
            continue
    return None, None

def update_library_entry(library, book_path, title, author, last_cover_path, current_index, progress_fraction):
    if not book_path:
        return library
    cover_dst = None
    if last_cover_path and os.path.exists(last_cover_path):
        try:
            h = hashlib.sha1(book_path.encode("utf-8")).hexdigest()[:12]
            ext = os.path.splitext(last_cover_path)[1].lower() or ".png"
            cover_dst = os.path.join(COVERS_DIR, f"{h}{ext}")
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file(last_cover_path)
                scaled = pix.scale_simple(LIB_COVER_W, LIB_COVER_H, GdkPixbuf.InterpType.BILINEAR)
                scaled.savev(cover_dst, ext.replace(".", ""), [], [])
            except Exception:
                shutil.copy2(last_cover_path, cover_dst)
        except Exception:
            cover_dst = None
    found = False
    found_entry = None
    for e in list(library):
        if e.get("path") == book_path:
            e["title"] = title
            e["author"] = author
            if cover_dst:
                e["cover"] = cover_dst
            e["index"] = int(current_index)
            e["progress"] = float(progress_fraction or 0.0)
            found = True
            found_entry = e
            break
    if found and found_entry is not None:
        library = [ee for ee in library if ee.get("path") != book_path]
        library.append(found_entry)
    if not found:
        entry = {
            "path": book_path,
            "title": title,
            "author": author,
            "cover": cover_dst,
            "index": int(current_index),
            "progress": float(progress_fraction or 0.0)
        }
        library.append(entry)
    if len(library) > 200:
        library = library[-200:]
    return library
