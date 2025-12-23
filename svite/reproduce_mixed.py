
import sys
sys.path.append('/var/home/rizvan/tts/svite')
from virtual_buffer import LineIndexer, detect_encoding
import os

def test_mixed_endings():
    filename = "test_mixed_endings.txt"
    # Create file with:
    # Line 1 (LF)
    # Line 2 (CRLF)
    # Line 3 (CR) -> This is likely the one skipped
    # Line 4 (LF)
    content = b"Line1\nLine2\r\nLine3\rLine4\n"
    
    with open(filename, "wb") as f:
        f.write(content)
        
    print(f"Created file with {len(content)} bytes")
    
    indexer = LineIndexer()
    indexer.build_from_file(filename)
    
    print(f"Total lines indexed: {indexer.line_count}")
    
    # We expect 4 lines (or 5 if there's an empty one at end? No, 4 lines of content)
    # Line1\n -> Line 1
    # Line2\r\n -> Line 2
    # Line3\r -> Line 3
    # Line4\n -> Line 4
    # (Empty line after last \n? usually yes, but let's count segments)
    
    expected_lines = 4
    # Actually if python splitlines is used:
    # "Line1\nLine2\r\nLine3\rLine4\n".splitlines() -> ['Line1', 'Line2', 'Line3', 'Line4']
    # VirtualBuffer usually counts lines.
    
    if indexer.line_count < expected_lines:
        print(f"FAIL: Expected {expected_lines} lines, got {indexer.line_count}")
        # Identify what was merged
        offsets = indexer._offsets
        for i in range(indexer.line_count):
             len_i = indexer._lengths[i]
             print(f"Line {i} length: {len_i}")
    else:
        print(f"PASS: Got {indexer.line_count} lines")
        
    os.remove(filename)

if __name__ == "__main__":
    test_mixed_endings()
