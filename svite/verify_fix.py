import sys
import os

# Mock modules to test svite logic
class MockBuffer:
    def __init__(self, content):
        self.lines = content.split('\n')
        self._language = None
        self.syntax_engine = "SyntaxEngine"
        
    def total(self):
        return len(self.lines)
        
    def get_line(self, n):
        return self.lines[n]
        
    def set_language(self, lang):
        print(f"Buffer language set to: {lang}")
        self._language = lang
        
    @property
    def language(self):
        return self._language

class MockView:
    def __init__(self, buf):
        self.buf = buf
        self.renderer = type('obj', (object,), {'attr_list_cache': {}})
        self.attr_list_cache = {}
        self.attr_list_cache_order = []
        self.syntax = None
        
    def queue_draw(self):
        print("View queue_draw() called")

class MockEditor:
    def __init__(self, content):
        self.buf = MockBuffer(content)
        self.view = MockView(self.buf)
        self.current_file_path = "untitled"
        
class MockStatusBar:
    def update_for_editor(self, editor):
        print("StatusBar update_for_editor() called")

class MockWindow:
    def __init__(self):
        self.status_bar = MockStatusBar()
        
    # Simulate the on_buffer_changed logic we added
    def on_buffer_changed(self, editor):
        if editor.buf.total() > 0:
            first_line = editor.buf.get_line(0)
            
            # Simple detect_language mock for test
            new_lang = None
            if first_line.startswith("#!/usr/bin/env python3"):
                new_lang = "python"
            elif first_line.startswith("#!/bin/bash"):
                new_lang = "bash"
                
            current_lang = getattr(editor.view.buf, 'language', None)
            
            if new_lang != current_lang:
                 editor.view.buf.set_language(new_lang)
                 editor.view.syntax = editor.view.buf.syntax_engine
                 
                 # Clear highlight cache logic
                 if hasattr(editor.view, 'attr_list_cache'):
                      editor.view.attr_list_cache.clear()
                      print("Cache cleared")
                 
                 self.status_bar.update_for_editor(editor)
                 editor.view.queue_draw()

# Test Case 1: Detect Python
print("--- Test 1: Python Shebang ---")
editor = MockEditor("#!/usr/bin/env python3\nprint('hello')")
window = MockWindow()
window.on_buffer_changed(editor)
if editor.buf.language == "python":
    print("SUCCESS: Language is python")
else:
    print(f"FAIL: Language is {editor.buf.language}")

# Test Case 2: Change to Bash
print("\n--- Test 2: Change to Bash Shebang ---")
editor.buf.lines[0] = "#!/bin/bash"
window.on_buffer_changed(editor)
if editor.buf.language == "bash":
    print("SUCCESS: Language is bash")
else:
    print(f"FAIL: Language is {editor.buf.language}")

# Test Case 3: No Change
print("\n--- Test 3: No Change ---")
window.on_buffer_changed(editor)
