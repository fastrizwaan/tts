from .constants import LIBRARY_DIR
from .app import Application
import os

def main():
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    app = Application()
    app.run(None)

if __name__ == "__main__":
    main()
