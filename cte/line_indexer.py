# line_indexer.py
import threading
from gi.repository import GLib

class LineIndex:
    def __init__(self, mapped_file, chunk=4_000_000):
        self.mf = mapped_file
        self.chunk = chunk
        self.newlines = []
        self.finished = False
        self.callbacks = []

    def on_update(self, cb):
        self.callbacks.append(cb)

    def _notify(self):
        for cb in self.callbacks:
            GLib.idle_add(cb)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        size = self.mf.size
        pos = 0
        while pos < size:
            end = min(size, pos + self.chunk)
            block = self.mf.slice(pos, end)
            start_pos = pos

            idx = block.find(b'\n')
            while idx != -1:
                self.newlines.append(start_pos + idx)
                idx = block.find(b'\n', idx + 1)

            pos += self.chunk
            self._notify()

        self.finished = True
        self._notify()

    def line_count(self):
        return len(self.newlines) + 1

    def line_start_offset(self, line_no):
        if line_no == 0:
            return 0
        if line_no - 1 < len(self.newlines):
            return self.newlines[line_no - 1] + 1
        return None
