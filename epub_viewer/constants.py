import os
from gi.repository import GLib

# App metadata
APP_NAME = "EPUB Viewer"

# Cover sizes
COVER_W, COVER_H = 70, 100
LIB_COVER_W, LIB_COVER_H = 200, 300

# Paths
LIBRARY_DIR = os.path.join(GLib.get_user_data_dir(), "epubviewer")
LIBRARY_FILE = os.path.join(LIBRARY_DIR, "library.json")
COVERS_DIR = os.path.join(LIBRARY_DIR, "covers")

# CSS strings
CSS_SIDEBAR = """
.epub-sidebar .adw-action-row {
  margin: 5px;
  padding: 6px;
  border-radius: 8px;
  background-color: transparent;
}
.epub-sidebar .adw-action-row:hover {
  background-color: rgba(0,0,0,0.06);
}
.epub-sidebar .adw-action-row.selected {
  background-color: rgba(0,0,0,0.12);
}
.book-title { font-weight: 600; margin-bottom: 2px; }
.book-author { color: rgba(0,0,0,0.6); font-size: 12px; }
"""

CSS_LIBRARY = b"""
.library-grid { padding: 1px; }
.library-card {
  background-color: transparent;
  border-radius: 10px;
  padding-top: 10px;
  padding-bottom: 5px;
  box-shadow: none;
  border: none;
}
.library-card .cover { 
  margin-top: 0px;
  margin-bottom: 5px;
  margin-left: 10px;  
  margin-right: 10px;    
  border-radius: 10px;
}
.library-card .title { font-weight: 600; font-size: 12px; line-height: 1.2; color: @theme_fg_color; }
.library-card .author { font-size: 10px; opacity: 0.7; color: @theme_fg_color; }
.library-card .meta { font-size: 9px; font-weight: 500; opacity: 0.6; color: @theme_fg_color; }
.library-card.active { border: 2px solid #ffcc66; box-shadow: 0 6px 18px rgba(255,204,102,0.15); }
"""

CSS_HOVER_LIGHT = b"""
.library-card:hover {
  box-shadow: 0 6px 16px rgba(0,0,0,0.15);
  transform: translateY(-2px);
  background-color: rgba(255,204,102,0.06);
}
.library-card.active {
  background-color: rgba(255,204,102,0.08);
  border: 2px solid #ffcc66;
  box-shadow: 0 6px 18px rgba(255,204,102,0.15);
}
"""

CSS_HOVER_DARK = b"""
.library-card:hover {
  box-shadow: 0 6px 20px rgba(0,0,0,0.5);
  transform: translateY(-2px);
  background-color: rgba(255,204,102,0.12);
}
.library-card.active {
  background-color: rgba(255,204,102,0.14);
  border: 2px solid #ffcc66;
  box-shadow: 0 6px 22px rgba(255,204,102,0.25);
}
"""

DARK_OVERRIDE_CSS = """
.epub-sidebar .book-author { color: rgba(255,255,255,0.6); }
"""

THEME_INJECTION_CSS = """
@media (prefers-color-scheme: dark) {
    body { background-color:#242424; color:#e3e3e3; }
    blockquote { border-left-color:#62a0ea; }
    .tts-highlight { background:rgba(0,127,0,0.75); box-shadow:0 0 0 2px rgba(0,127,0,0.75); }
}
"""
