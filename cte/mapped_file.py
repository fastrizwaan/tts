# mapped_file.py
import mmap
import os

class MappedFile:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        self.size = os.path.getsize(path)
        self.mm = mmap.mmap(self.fd, 0, access=mmap.ACCESS_READ)

    def slice(self, start, end):
        return self.mm[start:end]

    def close(self):
        try:
            self.mm.close()
            os.close(self.fd)
        except:
            pass
