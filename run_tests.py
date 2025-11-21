import os
import sys
import subprocess
import time

def main():
    # Rename __init__.py to hide it from pytest
    renamed = False
    if os.path.exists("__init__.py"):
        try:
            os.rename("__init__.py", "__init__.py.disabled")
            print("Renamed __init__.py to __init__.py.disabled")
            renamed = True
        except OSError as e:
            print(f"Failed to rename __init__.py: {e}")
    
    try:
        # Run pytest with args forwarded
        print("Running pytest...")
        cmd = [sys.executable, "-m", "pytest"] + sys.argv[1:]
        subprocess.run(cmd, check=False)
    finally:
        # Restore __init__.py
        if renamed and os.path.exists("__init__.py.disabled"):
            os.rename("__init__.py.disabled", "__init__.py")
            print("Restored __init__.py")

if __name__ == "__main__":
    main()
