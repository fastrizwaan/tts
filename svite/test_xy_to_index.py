
import gi
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo
import cairo

def test_xy_to_index():
    font_map = PangoCairo.FontMap.get_default()
    ctx = font_map.create_context()
    layout = Pango.Layout(ctx)
    layout.set_text("Hello World", -1)
    
    # Simulate a hit test
    result = layout.xy_to_index(10 * Pango.SCALE, 5 * Pango.SCALE)
    
    print(f"Result type: {type(result)}")
    print(f"Result content: {result}")
    if isinstance(result, tuple):
        print(f"Length: {len(result)}")

if __name__ == "__main__":
    test_xy_to_index()
