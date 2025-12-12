import sys
import os

# Fix for VK_ERROR_DEVICE_LOST on some systems by avoiding Vulkan
#os.environ['GSK_RENDERER'] = 'gl'

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from app import EdigApp

if __name__ == '__main__':
    app = EdigApp()
    sys.exit(app.run(sys.argv))
