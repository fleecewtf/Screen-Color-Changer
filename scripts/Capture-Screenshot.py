"""Capture the actual Qt app window, already transparent around its edges.

No desktop screenshot, image synthesis, color API calls, or user settings are
involved. The real ColorWindow paints its own controls at 2x pixel density.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["QT_SCALE_FACTOR"] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Refusing to overwrite an existing screenshot.")
    if not output.parent.is_dir():
        raise SystemExit("Screenshot output directory does not exist.")

    from PySide6.QtCore import QEventLoop, QRect, QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    loader = importlib.machinery.SourceFileLoader(
        "fleece_screenshot_window", str(ROOT / "Screen Color Changer.pyw")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    app = QApplication(["Fleece app-window screenshot"])
    # Register the same installed fonts used by the real app for repeatable
    # window captures; do not substitute
    # generated text or ship font files with the screenshot/release.
    fonts = Path(os.environ["SystemRoot"]) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf", "consola.ttf", "consolab.ttf"):
        font = fonts / name
        if font.is_file() and QFontDatabase.addApplicationFont(str(font)) < 0:
            raise SystemExit(f"Could not load installed screenshot font: {name}")
    # A truthy inert effect means native Magnification/Gamma APIs are never
    # initialized. testing=True bypasses user settings and recovery prompts.
    window = module.ColorWindow(testing=True, effect=SimpleNamespace(active=False, disable=lambda: True))
    window.show()
    # Select the app's ordinary 496x496 layout rather than its small-work-area
    # fallback. This is the actual app, with only display effects disabled.
    window._fit_work_area(QRect(0, 0, 1920, 1080))
    loop = QEventLoop()
    QTimer.singleShot(100, loop.quit)
    loop.exec()
    app.processEvents()
    image = window.grab().toImage()
    corners = [(0, 0), (image.width() - 1, 0),
               (0, image.height() - 1), (image.width() - 1, image.height() - 1)]
    if not image.hasAlphaChannel() or any(image.pixelColor(x, y).alpha() != 0 for x, y in corners):
        raise SystemExit("App-window capture did not preserve transparent edges.")
    if window.scroll_area.horizontalScrollBar().maximum() != 0:
        raise SystemExit("Ordinary app-window capture has an unexpected scrollbar.")
    if not image.save(str(output), "PNG"):
        raise SystemExit("Could not save the native app-window PNG.")
    print(f"Captured actual v{module.APP_VERSION} UI: {image.width()} x {image.height()}, alpha preserved.")
    print("No desktop pixels or native display changes were captured or performed.")
    window.close()
    app.processEvents()


if __name__ == "__main__":
    main()
