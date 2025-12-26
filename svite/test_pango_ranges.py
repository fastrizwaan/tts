
import gi
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo
import cairo

def test_ranges():
    font_map = PangoCairo.FontMap.get_default()
    ctx = font_map.create_context()
    layout = Pango.Layout(ctx)
    layout.set_text("Hello World", -1)
    
    line = layout.get_line_readonly(0)
    
    extents = line.get_extents()
    # extents is (ink_rect, logical_rect)
    print(f"Line extents (ink): x={extents[0].x}, w={extents[0].width}")
    print(f"Line extents (log): x={extents[1].x}, w={extents[1].width}")
    
    # Range covering "Hello" (5 chars -> 5 bytes)
    ranges = line.get_x_ranges(0, 5)
    
    print(f"Ranges(0,5) type: {type(ranges)}")
    print(f"Ranges(0,5) content: {ranges}")
    
    if len(ranges) > 0:
        print(f"First element type: {type(ranges[0])}")

if __name__ == "__main__":
    test_ranges()
