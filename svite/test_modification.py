
import sys
import os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk

# Mock some Gtk parts if needed, but we try to use real classes
from svite import EditorPage, VirtualBuffer, VirtualTextView, UndoRedoManager

def test_modification_logic():
    print("Initializing EditorPage...")
    editor = EditorPage("Test")
    
    print(f"Initial State: path={editor.current_file_path}, modified={editor.check_modification_state()}")
    print(f"Undo count: {editor.view.undo_manager.get_undo_count()}")
    
    # Simulate typing
    print("\nSimulating typing 'Hello'...")
    # Direct buffer insertion as InputController would do
    editor.buf.insert(0, 0, "Hello", _record_undo=True)
    
    undo_count = editor.view.undo_manager.get_undo_count()
    is_mod = editor.check_modification_state()
    
    print(f"After typing: modified={is_mod}, undo_count={undo_count}")
    
    if not is_mod:
        print("FAIL: Should be modified after typing!")
    else:
        print("SUCCESS: Detected modification.")
        
    # Simulate saving (update checkpoint)
    print("\nSimulating save...")
    editor.last_saved_undo_count = undo_count
    is_mod = editor.check_modification_state()
    print(f"After save: modified={is_mod}")
    
    if is_mod:
        print("FAIL: Should be unmodified after save!")
    else:
        print("SUCCESS: Detected clean state.")
        
    # Simulate Undo
    print("\nSimulating Undo...")
    editor.view.undo_manager.undo(editor.buf)
    undo_count = editor.view.undo_manager.get_undo_count()
    is_mod = editor.check_modification_state()
    print(f"After undo: modified={is_mod}, undo_count={undo_count}")
    
    if is_mod:
        print("FAIL: Should be unmodified after undo (back to save state 0)?")
        # Wait, if we saved at count 1. Undo makes count 0.
        # last_saved = 1. current = 0.
        # 0 != 1 -> Modified.
        # This is expected behavior for simple undo count check. 
        # If I save "Hello", then Undo -> Empty. Empty is different from "Hello". So Modified is correct.
        print("Logic check: Undo count 0 vs saved 1 -> Modified.")
    
    # Reset
    print("\nSimulating Load File (clearing check)...")
    editor.view.undo_manager.clear()
    editor.last_saved_undo_count = 0
    undo_count = editor.view.undo_manager.get_undo_count()
    is_mod = editor.check_modification_state()
    print(f"After clear: modified={is_mod}, undo_count={undo_count}")

if __name__ == "__main__":
    test_modification_logic()
