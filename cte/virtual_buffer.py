# virtual_buffer.py
from rope import Rope

class VirtualTextBuffer:
    def __init__(self, mapped_file, line_index):
        self.mf = mapped_file
        self.idx = line_index
        self.rope = Rope("")  # diffs on top of mmap

    def line_count(self):
        return self.idx.line_count()

    def get_line(self, ln):
        start = self.idx.line_start_offset(ln)
        if start is None:
            return ""
        if ln + 1 < self.idx.line_count():
            end = self.idx.line_start_offset(ln + 1) - 1
        else:
            end = self.mf.size

        base = self.mf.slice(start, end).decode("utf-8", "replace")
        # full diff integration not shown (simple version)
        return base

    def insert(self, pos, text):
        self.rope.insert(pos, text)

    def delete(self, pos, length):
        self.rope.delete(pos, length)

    def full_text(self):
        return self.rope.get_text()
