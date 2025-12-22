import os
import tempfile
import stat

FILENAME = "test_perm.txt"

def setup():
    # Create a file with 755 permissions
    with open(FILENAME, "w") as f:
        f.write("Original content")
    os.chmod(FILENAME, 0o755)
    print(f"Created {FILENAME} with 755 permissions.")

def check_perms():
    st = os.stat(FILENAME)
    mode = stat.S_IMODE(st.st_mode)
    print(f"Current permissions: {oct(mode)}")
    return mode

def mimic_save():
    print("Mimicking save operation (WITH FIX)...")
    dirname, basename = os.path.split(os.path.abspath(FILENAME))
    
    # 0. Capture original permissions (THE FIX)
    original_mode = None
    if os.path.exists(FILENAME):
        st = os.stat(FILENAME)
        original_mode = stat.S_IMODE(st.st_mode)
        print(f"Captured original mode: {oct(original_mode)}")
    
    # 1. Create temp file 
    with tempfile.NamedTemporaryFile(mode='w', dir=dirname, delete=False) as tf:
        temp_path = tf.name
        tf.write("New content")
    
    # 2. Restore permissions (THE FIX)
    if original_mode is not None:
        os.chmod(temp_path, original_mode)
        print("Restored permissions to temp file.")
    
    # 3. Atomic replacement
    os.replace(temp_path, FILENAME)
    print("Replaced file.")

def main():
    setup()
    initial_mode = check_perms()
    
    mimic_save()
    
    final_mode = check_perms()
    
    if initial_mode != final_mode:
        print("FAIL: Permissions changed!")
    else:
        print("SUCCESS: Permissions preserved.")
    
    # Cleanup
    if os.path.exists(FILENAME):
        os.remove(FILENAME)

if __name__ == "__main__":
    main()
