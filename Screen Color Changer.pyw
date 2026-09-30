"""Fleece Screen Color Changer — compact Windows desktop color prototype.

The display is never changed on launch or during the installer self-test.
Only an explicit Apply click activates Windows' full-screen color effect.
"""

import ctypes
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
# The folder-local shortcut intentionally uses Python's isolated -I mode.
# Only this extracted tool folder is added for its two reviewed sibling modules.
sys.path.insert(0, str(ROOT))

from color_math import ColorValues, color_matrix, identity_matrix, matrices_match
from screen_backend import ScreenEffect, ScreenEffectError

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Screen Color Changer"
APP_VERSION = "0.1.0"
WINDOW_SIZE = 448
SETTINGS_PATH = ROOT / ".runtime" / "settings.ini"
RECOVERY_PATH = ROOT / ".runtime" / "color-recovery.json"
MUTEX_NAME = "Local\\FleeceScreenColorChangerApp"


STYLE = """
QWidget { color: #f4f4f4; font: 12px 'Segoe UI'; }
QFrame#windowFrame { background: #070707; border: 1px solid #282828; border-radius: 16px; }
QFrame#titleBar { background: #070707; border: none; border-bottom: 1px solid #202020;
                  border-top-left-radius: 16px; border-top-right-radius: 16px; }
QLabel#windowTitle { color: #bcbcbc; font-weight: 600; font-size: 11px; }
QPushButton#closeDot, QPushButton#minimizeDot { border: none; border-radius: 6px;
    min-width: 12px; max-width: 12px; min-height: 12px; max-height: 12px; padding: 0; }
QPushButton#closeDot { background: #ff5f57; }
QPushButton#minimizeDot { background: #febc2e; }
QLabel#eyebrow { color: #7c7c7c; font: 10px 'Cascadia Mono'; }
QLabel#headline { color: #fafafa; font-size: 24px; font-weight: 700; }
QLabel#subtitle { color: #929292; font-size: 11px; }
QFrame#panel { background: #0d0d0d; border: 1px solid #252525; border-radius: 13px; }
QLabel#controlLabel { color: #d2d2d2; font-size: 11px; font-weight: 600; }
QSlider::groove:horizontal { background: #292929; border-radius: 3px; height: 6px; }
QSlider::sub-page:horizontal { background: #e8e8e8; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; border: 2px solid #111111;
                              border-radius: 9px; width: 18px; margin: -6px 0; }
QSlider::handle:horizontal:hover { background: #dedede; }
QSpinBox { background: #151515; border: 1px solid #343434; border-radius: 7px;
           color: #f5f5f5; min-height: 23px; padding: 0 7px; font-weight: 600; }
QSpinBox:focus { border-color: #f1f1f1; }
QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }
QPushButton#primary, QPushButton#secondary { border-radius: 9px; min-height: 36px;
                                            font-weight: 700; padding: 0 12px; }
QPushButton#primary { background: #f6f6f6; color: #070707; border: 1px solid #f6f6f6; }
QPushButton#primary:hover { background: #dddddd; }
QPushButton#secondary { background: #151515; color: #dddddd; border: 1px solid #333333; }
QPushButton#secondary:hover { background: #242424; }
QPushButton#secondary:disabled { background: #111111; color: #666666; border-color: #242424; }
QPushButton#reset { color: #9b9b9b; background: transparent; border: none; text-align: right; }
QPushButton#reset:hover { color: #f0f0f0; }
QLabel#status { color: #a1a1a1; font-size: 11px; }
QLabel#limit { color: #777777; font-size: 10px; }
QMessageBox { background: #0d0d0d; }
QMessageBox QPushButton { min-width: 68px; background: #202020; border: 1px solid #404040;
                          border-radius: 7px; padding: 6px; }
"""


class TitleBar(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.drag_offset = QPoint()
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(8)
        spacer = QWidget()
        spacer.setFixedWidth(32)
        row.addWidget(spacer)
        row.addStretch()
        title = QLabel("fleece  /  screen color")
        title.setObjectName("windowTitle")
        row.addWidget(title)
        row.addStretch()
        minimize = QPushButton()
        minimize.setObjectName("minimizeDot")
        minimize.setToolTip("Minimize")
        minimize.setAccessibleName("Minimize window")
        minimize.clicked.connect(window.showMinimized)
        close = QPushButton()
        close.setObjectName("closeDot")
        close.setToolTip("Close and restore previous colors")
        close.setAccessibleName("Close and restore previous colors")
        close.clicked.connect(window.close)
        row.addWidget(minimize)
        row.addWidget(close)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()


class ExactControl(QWidget):
    def __init__(self, title: str, minimum: int, maximum: int, default: int, parent=None):
        super().__init__(parent)
        self.setObjectName("exactControl")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("controlLabel")
        heading.addWidget(label)
        heading.addStretch()
        self.number = QSpinBox()
        self.number.setRange(minimum, maximum)
        self.number.setSuffix("%")
        self.number.setKeyboardTracking(False)
        self.number.setFixedWidth(76)
        self.number.setAccessibleName(f"{title} exact percentage")
        self.number.setToolTip("Type a whole number, or press Up/Down for exact one-point changes")
        heading.addWidget(self.number)
        column.addLayout(heading)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setAccessibleName(f"{title} slider")
        self.slider.setToolTip("Drag, or use Left/Right arrows for exact one-point changes")
        column.addWidget(self.slider)
        self.slider.valueChanged.connect(self.number.setValue)
        self.number.valueChanged.connect(self.slider.setValue)
        self.number.setValue(default)

    def value(self) -> int:
        return self.number.value()

    def set_value(self, value: int):
        self.number.setValue(value)


class ColorWindow(QWidget):
    def __init__(self, testing: bool = False, effect=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.testing = testing
        self.effect = effect if effect is not None else ScreenEffect(
            recovery_path=None if testing else RECOVERY_PATH
        )
        self.setWindowTitle(APP_NAME)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WINDOW_SIZE, WINDOW_SIZE)
        self._warning_acknowledged = False
        self._pending_recovery = not testing
        self._preview_remaining = None
        self._preview_values = None
        self._preview_can_confirm = False
        self._confirmed_values = None
        self._settings = None
        if not testing:
            try:
                SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
                self._settings = QSettings(str(SETTINGS_PATH), QSettings.IniFormat)
            except OSError:
                pass  # The app still works without saving preferences.
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(1000)
        self._preview_timer.timeout.connect(self._preview_tick)
        self._confirm_delay = QTimer(self)
        self._confirm_delay.setSingleShot(True)
        self._confirm_delay.setInterval(1500)
        self._confirm_delay.timeout.connect(self._enable_keep_button)
        self._escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._escape.activated.connect(self._disable_clicked)
        QApplication.instance().setStyleSheet(STYLE)
        self._build_ui()
        self._restore_values()
        self._sync_state("Ready • Apply previews for 15 seconds")
        if self._pending_recovery:
            self.apply_button.setEnabled(False)
            QTimer.singleShot(0, self._offer_recovery)

    def _build_ui(self):
        frame = QFrame(self)
        frame.setObjectName("windowFrame")
        frame.setFixedSize(WINDOW_SIZE, WINDOW_SIZE)
        self.frame = frame
        page = QVBoxLayout(frame)
        page.setContentsMargins(1, 1, 1, 1)
        page.setSpacing(0)
        page.addWidget(TitleBar(self))

        body = QWidget()
        page.addWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(9)
        eyebrow = QLabel("DISPLAY  /  001")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        headline = QLabel("Color, your way.")
        headline.setObjectName("headline")
        layout.addWidget(headline)
        subtitle = QLabel("A tiny desktop color utility. No NVIDIA setup needed.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(15, 9, 15, 9)
        panel_layout.setSpacing(5)
        self.saturation = ExactControl("Saturation", 0, 300, 100)
        self.contrast = ExactControl("Contrast", 0, 200, 100)
        self.brightness = ExactControl("Brightness", -50, 50, 0)
        for control in (self.saturation, self.contrast, self.brightness):
            panel_layout.addWidget(control)
            control.number.valueChanged.connect(self._values_changed)
        layout.addWidget(panel)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.apply_button = QPushButton("Apply colors")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self._apply_clicked)
        actions.addWidget(self.apply_button, 2)
        self.disable_button = QPushButton("Disable")
        self.disable_button.setObjectName("secondary")
        self.disable_button.clicked.connect(self._disable_clicked)
        actions.addWidget(self.disable_button, 1)
        layout.addLayout(actions)

        footer = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        reset = QPushButton("Reset values")
        reset.setObjectName("reset")
        reset.setAccessibleName("Reset all three values to neutral")
        reset.clicked.connect(self._reset_values)
        footer.addWidget(reset)
        layout.addLayout(footer)
        limit = QLabel("Desktop effect · some HDR / full-screen games may ignore it")
        limit.setObjectName("limit")
        layout.addWidget(limit)

    def values(self) -> ColorValues:
        return ColorValues(self.saturation.value(), self.contrast.value(), self.brightness.value())

    def _restore_values(self):
        if self._settings is None:
            return
        defaults = {"saturation": (self.saturation, 100),
                    "contrast": (self.contrast, 100),
                    "brightness": (self.brightness, 0)}
        for key, (control, default) in defaults.items():
            try:
                value = int(self._settings.value(key, default))
                if control.number.minimum() <= value <= control.number.maximum():
                    control.set_value(value)
            except (TypeError, ValueError):
                pass

    def _save_values(self):
        if self._settings is None:
            return
        for key, value in vars(self.values()).items():
            self._settings.setValue(key, value)
        self._settings.sync()

    def _sync_state(self, message: str):
        self.status_label.setText(message)
        self.disable_button.setEnabled(self.effect.active)
        self.disable_button.setText("Revert now" if self._preview_remaining is not None else "Disable")
        if self._preview_remaining is not None:
            if self.values() != self._preview_values:
                self.apply_button.setText("Preview new values")
                self.apply_button.setEnabled(not self._pending_recovery)
            else:
                self.apply_button.setText(f"Keep colors ({self._preview_remaining}s)")
                self.apply_button.setEnabled(self._preview_can_confirm and not self._pending_recovery)
        else:
            self.apply_button.setText("Update colors" if self.effect.active else "Apply colors")
            self.apply_button.setEnabled(not self._pending_recovery)

    def _values_changed(self, _value: int):
        self._save_values()
        if self._preview_remaining is not None:
            self._sync_state("Values changed • preview new values or wait to revert")
        elif self.effect.active:
            self._sync_state("Values changed • press Update to preview")
        else:
            self._sync_state("Ready • nothing changes until Apply")

    def _apply_clicked(self):
        if self.testing or self._pending_recovery:
            return
        values = self.values()
        if self._preview_remaining is not None and values == self._preview_values:
            if not self._preview_can_confirm:
                return
            self._confirmed_values = values
            self._stop_preview()
            self._sync_state("Active • Esc, Disable or close restores prior colors")
            return
        try:
            if not self.effect.active:
                self.effect.initialize()
                if self.effect.replaces_existing_effect and not self._warning_acknowledged:
                    choice = QMessageBox.question(
                        self, "Existing desktop color effect",
                        "Another Windows color effect is already active. Apply will temporarily "
                        "replace it, and Disable or closing this app will restore it. Continue?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    )
                    if choice != QMessageBox.Yes:
                        self.effect.disable()
                        return
                    self._warning_acknowledged = True
            self.effect.apply(values)
            self._preview_values = values
            self._preview_remaining = 15
            self._preview_can_confirm = False
            self._preview_timer.start()
            self._confirm_delay.start()
            self._sync_state("Preview • reverts in 15s unless kept")
        except (ScreenEffectError, OSError) as error:
            self._sync_state("Could not apply these values; preview will revert")
            QMessageBox.warning(self, "Color effect unavailable", str(error))

    def _enable_keep_button(self):
        if self._preview_remaining is not None:
            self._preview_can_confirm = True
            self._sync_state(f"Preview • reverts in {self._preview_remaining}s unless kept")

    def _stop_preview(self):
        self._preview_timer.stop()
        self._confirm_delay.stop()
        self._preview_remaining = None
        self._preview_values = None
        self._preview_can_confirm = False

    def _preview_tick(self):
        if self._preview_remaining is None:
            return
        self._preview_remaining -= 1
        if self._preview_remaining > 0:
            self._sync_state(f"Preview • reverts in {self._preview_remaining}s unless kept")
            return
        try:
            if self._confirmed_values is not None:
                self.effect.apply(self._confirmed_values)
                for control, value in ((self.saturation, self._confirmed_values.saturation),
                                       (self.contrast, self._confirmed_values.contrast),
                                       (self.brightness, self._confirmed_values.brightness)):
                    control.set_value(value)
                message = "Preview ended • previous setting restored"
            else:
                restored = self.effect.disable()
                message = ("Preview ended • prior colors restored" if restored else
                           "Another app changed colors; its effect was left alone")
            self._stop_preview()
            self._sync_state(message)
        except (ScreenEffectError, OSError):
            if self._confirmed_values is not None:
                try:
                    restored = self.effect.disable()
                    self._confirmed_values = None
                    self._stop_preview()
                    self._sync_state("Preview ended • prior colors restored" if restored else
                                     "Another app changed colors; its effect was left alone")
                    return
                except (ScreenEffectError, OSError):
                    pass
            # Keep retrying once per second. Never silently leave a possibly
            # unreadable preview active when Windows temporarily refuses it.
            self._preview_remaining = 1
            self._sync_state("Restore failed • retrying automatically")

    def _offer_recovery(self):
        try:
            if self.effect.recovery_needed():
                choice = QMessageBox.question(
                    self, "Restore previous desktop colors?",
                    "A previous session may have ended while its color effect was active. "
                    "Restore the colors that were in place before it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                self.effect.recover_previous(choice == QMessageBox.Yes)
                self._sync_state(
                    "Previous colors restored" if choice == QMessageBox.Yes
                    else "Previous effect left as you chose"
                )
        except (ScreenEffectError, OSError) as error:
            self._sync_state("Recovery needs attention before colors can be applied")
            QMessageBox.critical(self, "Color recovery unavailable", str(error))
            return
        self._pending_recovery = False
        self.apply_button.setEnabled(True)

    def _disable_clicked(self):
        try:
            restored = self.effect.disable()
            self._stop_preview()
            self._confirmed_values = None
            message = ("Previous colors restored" if restored
                       else "Another app changed colors; its effect was left alone")
            self._sync_state(message)
        except ScreenEffectError as error:
            self._sync_state("Could not restore previous colors")
            QMessageBox.critical(self, "Restore failed", str(error))

    def _reset_values(self):
        self.saturation.set_value(100)
        self.contrast.set_value(100)
        self.brightness.set_value(0)

    def closeEvent(self, event):
        try:
            restored = self.effect.disable()
        except ScreenEffectError as error:
            QMessageBox.critical(
                self, "Colors could not be restored",
                f"{error}\n\nThe window will stay open so you can retry Disable.",
            )
            event.ignore()
            return
        if not restored:
            self.status_label.setText("Another app now controls the color effect")
        self._stop_preview()
        event.accept()


def _single_instance_mutex():
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateMutexW(None, 1, MUTEX_NAME)
    if not handle:
        raise OSError("Could not create the single-instance lock")
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        raise RuntimeError("Screen Color Changer is already open")
    return kernel, handle


def _self_test(folder: Path) -> int:
    if not folder.is_dir() or folder.is_symlink() or any(folder.iterdir()):
        raise RuntimeError("Self-test needs an existing, empty normal directory")
    app = QApplication.instance() or QApplication([APP_NAME])
    window = ColorWindow(testing=True)
    window.show()
    app.processEvents()
    assert window.size().width() == WINDOW_SIZE == window.size().height()
    assert window.values() == ColorValues()
    window.saturation.number.setValue(255)
    assert window.saturation.slider.value() == 255
    window.saturation.slider.setValue(254)
    assert window.saturation.number.value() == 254
    window.saturation.slider.setValue(255)
    assert window.saturation.number.value() == 255
    assert window.saturation.slider.singleStep() == 1
    assert window.saturation.slider.pageStep() == 1
    # This fixed-size square still gives the 0..300 slider enough mouse travel
    # to select every whole number (not just values reachable by typing).
    slider = window.saturation.slider
    option = QStyleOptionSlider()
    option.initFrom(slider)
    option.orientation = Qt.Horizontal
    option.minimum = slider.minimum()
    option.maximum = slider.maximum()
    option.sliderPosition = slider.value()
    option.sliderValue = slider.value()
    style = slider.style()
    groove = style.subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderGroove, slider)
    handle = style.subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, slider)
    travel = groove.width() - handle.width()
    reachable = {QStyle.sliderValueFromPosition(0, 300, x, travel)
                 for x in range(max(0, travel) + 1)}
    assert set(range(301)) <= reachable
    assert matrices_match(color_matrix(ColorValues()), identity_matrix())
    assert not window.effect.active

    class FakeEffect:
        active = False
        replaces_existing_effect = False
        disabled = 0
        applied = None

        def initialize(self):
            return None

        def apply(self, values):
            self.active = True
            self.applied = values

        def disable(self):
            self.active = False
            self.disabled += 1
            return True

    # Exercise the safety countdown without loading Magnification.dll or
    # altering the desktop, including the all-black matrix edge case.
    fake = FakeEffect()
    window.effect = fake
    window.testing = False
    window.contrast.set_value(0)
    window.brightness.set_value(-50)
    window._apply_clicked()
    assert fake.active and window._preview_remaining == 15
    assert not window.apply_button.isEnabled()  # avoid accidental double-click confirmation
    for _ in range(15):
        window._preview_tick()
    assert fake.disabled == 1 and not fake.active
    assert window._preview_remaining is None

    window.saturation.set_value(100)
    window.contrast.set_value(100)
    window.brightness.set_value(0)
    window._apply_clicked()
    window._enable_keep_button()
    window._apply_clicked()
    assert window._confirmed_values == ColorValues()
    window.saturation.set_value(255)
    window._apply_clicked()
    for _ in range(15):
        window._preview_tick()
    assert fake.active and fake.applied == ColorValues()
    assert window.saturation.value() == 100
    window.close()
    (folder / "self-test-passed.txt").write_text(
        f"{APP_NAME} {APP_VERSION}: UI, exact values, and color matrix passed.\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        # Installer validation must never flash a window or touch the display.
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        return _self_test(Path(sys.argv[2]))
    if len(sys.argv) != 1:
        raise SystemExit("Usage: Screen Color Changer.pyw [--self-test EMPTY_DIRECTORY]")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    mutex = None
    try:
        mutex = _single_instance_mutex()
    except (RuntimeError, OSError) as error:
        QMessageBox.warning(None, APP_NAME, str(error))
        return 1
    try:
        window = ColorWindow()
        window.show()
        app.aboutToQuit.connect(window.effect.disable)
        return app.exec()
    finally:
        if mutex is not None:
            kernel, handle = mutex
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
