
import os
import codecs

def create_test_files():
    # UTF-16 LE with BOM
    with open("test_utf16le_bom.txt", "wb") as f:
        f.write(codecs.BOM_UTF16_LE)
        f.write("Hello World".encode("utf-16-le"))

    # UTF-16 BE with BOM
    with open("test_utf16be_bom.txt", "wb") as f:
        f.write(codecs.BOM_UTF16_BE)
        f.write("Hello World".encode("utf-16-be"))

    print("Created test files.")

def mock_detect_encoding(file_path):
    # This simulates the logic currently in VirtualBuffer (we will inspect it later to be sure)
    # But first let's just create the files so we can run the actual buffer against them if needed,
    # or just use this script to verify our fix on the detection logic.
    
    with open(file_path, 'rb') as f:
        raw = f.read(4)
    
    if raw.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    elif raw.startswith(codecs.BOM_UTF16_LE):
        return 'utf-16' # Python's 'utf-16' handles BOM automatically
    elif raw.startswith(codecs.BOM_UTF16_BE):
        return 'utf-16'
    return 'utf-8'

if __name__ == "__main__":
    create_test_files()
