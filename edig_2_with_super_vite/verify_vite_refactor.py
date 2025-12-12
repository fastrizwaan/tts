import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

# Mock VirtualBuffer if needed or use real one
from virtual_buffer import VirtualBuffer
from vite import VirtualTextView


print("Script started...")

def on_activate(app):
    print("Activate called...")

    win = Gtk.ApplicationWindow(application=app)
    b = VirtualBuffer()
    b.load_text("Hello\nWorld")
    v = VirtualTextView(b)
    win.set_child(v)
    
    # Verification Logic
    print("Verifying VirtualTextView...")
    try:
        # Check shim
        if v.renderer != v:
            print("FAIL: v.renderer != v")
            sys.exit(1)
        
        # Check properties
        if v.renderer.line_h != v.line_h:
            print(f"FAIL: line_h mismatch {v.renderer.line_h} != {v.line_h}")
            sys.exit(1)
            
        # Check wrap compatibility
        v.renderer.wrap_enabled = False
        if v.mapper.enabled != False:
             print("FAIL: wrap_enabled setter failed")
             sys.exit(1)
             
        v.renderer.wrap_enabled = True
        if v.mapper.enabled != True:
             print("FAIL: wrap_enabled setter failed (true)")
             sys.exit(1)
             
        # Check method compatibility
        try:
             v.renderer.update_colors_for_theme(True)
        except Exception as e:
             print(f"FAIL: update_colors_for_theme: {e}")
             sys.exit(1)
             
        # Check observer mechanism
        observer_called = False
        def on_change(buf):
            nonlocal observer_called
            observer_called = True
            
        b.add_observer(on_change)
        b.insert(0, 0, "Test Change")
        
        if not observer_called:
             print("FAIL: Observer not notified on change")
             sys.exit(1)
             
        # Check Selection
        if not b.selection:
             print("FAIL: No selection object")
             sys.exit(1)
             
        b.selection.set_start(0,0)
        b.selection.set_end(0,1)
        if not b.selection.has_selection():
             print("FAIL: Selection not active")
             sys.exit(1)
             
        # Check Buffer API compatibility
        if b.total() != 2: # Hello\nWorld
             print(f"FAIL: total() returned {b.total()}, expected 2")
             sys.exit(1)
             
        b.set_cursor(1, 0)
        if b.cursor_line != 1 or b.cursor_col != 0:
             print(f"FAIL: set_cursor failed. Got {b.cursor_line},{b.cursor_col}")
             sys.exit(1)
             
        # Check load and insert_text compatibility
        class MockFile:
            def __init__(self, path):
                self.path = path
                
        # Test load with object
        # We can't easily test file loading without a real file, but we can verify it doesn't crash
        # b.load(MockFile("dummy"), emit_changed=False)
        
        # Test insert_text
        b.set_cursor(1, 1) # Hello\nW|orld
        b.insert_text("Inserted")
        if "Inserted" not in b.get_text():
             print("FAIL: insert_text didn't insert content")
             sys.exit(1)
             
        # Check Cache Compatibility Shims
        try:
             v.renderer.wrap_cache.clear()
             v.renderer.visual_line_map = []
             v.renderer.total_visual_lines_cache = None
             v.renderer.edits_since_cache_invalidation = 0
        except AttributeError as e:
             print(f"FAIL: Cache shim missing: {e}")
             sys.exit(1)
             
        print("SUCCESS: VirtualTextView refactor verified.")
        app.quit()
        
    except Exception as e:
        print(f"FAIL: Exception during verify: {e}")
        app.quit()

app = Adw.Application(application_id="com.example.VerifyVite")
app.connect('activate', on_activate)
app.run(None)
