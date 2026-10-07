"""Offline app regressions: fake displays, offscreen Qt, disposable settings.

Run with .runtime/python/python.exe -I -B scripts/Test-AppSafety.py.
No real display effects, desktop capture, or production preferences are used.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path[:0] = [str(ROOT), str(SCRIPTS)]


def main() -> int:
    # Do not report skipped Qt checks as a healthy installed application.
    try:
        import PySide6.QtWidgets  # noqa: F401
    except ImportError:
        raise SystemExit("Use the installed folder-private Python after Installer.bat succeeds.")
    suite = unittest.defaultTestLoader.discover(str(SCRIPTS), pattern="test_*.py")
    if suite.countTestCases() == 0:
        raise SystemExit("No offline app regressions were discovered.")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
