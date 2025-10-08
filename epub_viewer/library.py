import os
import json
from gi.repository import Gio
from .constants import LIBRARY_DIR, LIBRARY_FILE, COVERS_DIR

def _ensure_library_dir():
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)

def load_library():
    _ensure_library_dir()
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return []
    return []

def save_library(data):
    _ensure_library_dir()
    try:
        with open(LIBRARY_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Error saving library:", e)
