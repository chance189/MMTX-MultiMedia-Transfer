"""Entry script for PyInstaller builds (python -m mmtx works too)."""

import sys

from mmtx.app import main

if __name__ == "__main__":
    sys.exit(main())
