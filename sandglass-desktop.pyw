"""Double-clickable entry point for the desktop shell.

Pointing a shortcut or an autostart entry at `sandglass/desktop.py` does not
work: Python would put `sandglass/` itself on the import path rather than the
directory holding it, and every `from sandglass...` import would fail. This sits
beside the package and adds its own directory, so one absolute path is enough
and there is no working directory to get right.

The .pyw extension runs it under pythonw, which has no console window.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sandglass.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
