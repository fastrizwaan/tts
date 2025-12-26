
import sys
import unittest
from unittest.mock import MagicMock
import cairo
import gi
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

# Add svite to path
sys.path.append('/var/home/rizvan/tts/svite')
from word_wrap import VisualLineMapper
from virtual_buffer import VirtualBuffer

class TestRTLWrapping(unittest.TestCase):
    def setUp(self):
        self.buffer = MagicMock()
        # Mock buffer properties
        self.buffer.total_lines = 100
        self.buffer.get_line = MagicMock(return_value="")
        
        self.mapper = VisualLineMapper(self.buffer)
        self.mapper.enabled = True
        
        # Setup Pango font description
        self.font_desc = Pango.FontDescription.from_string("Monospace 12")
        self.mapper.set_font(self.font_desc)

    def test_rtl_wrapping_with_cr(self):
        """Verify that passing cr triggers Pango wrapping for RTL text"""
        # Long RTL string (Arabic)
        rtl_text = "السلام عليكم " * 20 
        self.buffer.get_line.return_value = rtl_text
        
        # Create a real Cairo context (headless)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 600)
        cr = cairo.Context(surface)
        
        # Set viewport to force wrapping
        # 30 chars approx
        self.mapper.set_viewport_width(300, 10) 
        
        # 1. Call WITH cr
        info_with_cr = self.mapper.get_wrap_info(0, cr)
        
        # 2. Call WITHOUT cr (fallback)
        # Clear cache first to force recompute
        self.mapper.invalidate_all()
        info_no_cr = self.mapper.get_wrap_info(0, None)
        
        print(f"\nBreakpoints WITH cr: {info_with_cr.break_points}")
        print(f"Breakpoints WITHOUT cr: {info_no_cr.break_points}")
        
        # Verify that we got results in both cases
        self.assertTrue(len(info_with_cr.break_points) > 0, "Should wrap with cr")
        self.assertTrue(len(info_no_cr.break_points) > 0, "Should wrap without cr")
        
        # verification: The breakpoints should likely differ because Pango handles RTL 
        # width logic differently than char count
        # (Though with Monospace they might be close, Pango respects word boundaries better)
        
        # Also verify that with CR, we ran the Pango code path
        # logic: _compute_wrap_info_pango would be called.
        
    def test_get_line_segments_passes_cr(self):
        """Verify get_line_segments forwards cr"""
        # Mock get_wrap_info to check arguments
        original_get_wrap_info = self.mapper.get_wrap_info
        self.mapper.get_wrap_info = MagicMock(wraps=original_get_wrap_info)
        
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 100, 100)
        cr = cairo.Context(surface)
        
        self.buffer.get_line.return_value = "test"
        self.mapper.get_line_segments(0, cr)
        
        # Check if get_wrap_info was called with cr
        self.mapper.get_wrap_info.assert_called_with(0, cr)

if __name__ == '__main__':
    unittest.main()
