import sys
import os
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib

# Mock necessary components
sys.path.append('/var/home/rizvan/tts/svite')
from undo_redo import UndoRedoManager, Command
from virtual_buffer import VirtualBuffer

class MockView:
    def __init__(self):
        self.undo_manager = UndoRedoManager()

# Init
buf = VirtualBuffer()
view = MockView()
buf.set_view(view)

# Load some text
buf.load_text("foo\nfoo\nfoo\nfoo\n")

print(f"Initial Lines: {buf.total()}")

# Perform Replace All Async
print("Starting Replace All...")
cancel = buf.replace_all_async("foo", "bar", chunk_size=2)

# Run loop until complete
loop = GLib.MainLoop()

def check_status(count=None):
    if count is not None:
        print(f"Replace Complete! Count: {count}")
        loop.quit()

# Hack: hook into on_complete by modifying the internal callback?
# replace_all_async takes on_complete.
# Let's call it again properly.

buf.replace_all_async("foo", "bar", 
                      on_complete=check_status,
                      chunk_size=2)

# Run the loop
# We need to timeout in case it hangs
GLib.timeout_add(2000, lambda: (print("Timeout!"), loop.quit()))

try:
    loop.run()
except KeyboardInterrupt:
    pass

# Check Undo Stack
manager = view.undo_manager
print(f"Undo Stack Size: {len(manager._undo_stack)}")
if len(manager._undo_stack) > 0:
    cmd = manager._undo_stack[0]
    print(f"Top Command Type: {type(cmd).__name__}")
    if hasattr(cmd, 'commands'):
        print(f"Batch Size: {len(cmd.commands)}")
    else:
        print("Not a batch command!")
else:
    print("Undo stack empty!")

# Check content
print(f"Line 0 content: {buf.get_line(0)}")
