"""Fleece Screen Color Changer — compact Windows desktop color prototype.

The display is never changed on launch or during the installer self-test.
Apply starts a preview; explicit recovery can restore a prior session's colors.
"""

import ctypes
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
# The folder-local shortcut intentionally uses Python's isolated -I mode.
# Only this extracted tool folder is added for its two reviewed sibling modules.
sys.path.insert(0, str(ROOT))

from color_math import ColorValues, color_matrix, gamma_ramp, identity_matrix, matrices_match
from screen_backend import ScreenEffect, ScreenEffectError

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
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
APP_VERSION = "0.2.0"
WINDOW_SIZE = 496
SETTINGS_PATH = ROOT / ".runtime" / "settings.ini"
RECOVERY_PATH = ROOT / ".runtime" / "color-recovery.json"
MUTEX_NAME = "Local\\FleeceScreenColorChangerApp"


STYLE = """
QWidget { color: #f5f5f5; font-family: 'Segoe UI'; font-size: 13px; }
QFrame#windowFrame { background: #070707; border: 1px solid #252525; border-radius: 14px; }
QFrame#titleBar { background: #070707; border: none; border-bottom: 1px solid #1c1c1c;
                  border-top-left-radius: 14px; border-top-right-radius: 14px; }
QLabel#windowTitle { color: #bdbdbd; font-weight: 600; font-size: 12px; }
QPushButton#closeDot, QPushButton#minimizeDot { border: none; border-radius: 6px;
    min-width: 13px; max-width: 13px; min-height: 13px; max-height: 13px; padding: 0; }
QPushButton#closeDot { background: #ff5f57; }
QPushButton#minimizeDot { background: #febc2e; }
QLabel#headline { color: #f5f5f5; font-size: 18px; font-weight: 700; }
QFrame#panel { background: #0d0d0d; border: 1px solid #242424; border-radius: 14px; }
QLabel#controlLabel { color: #b8b8b8; font-size: 12px; font-weight: 600; }
QSlider::groove:horizontal { background: #292929; border-radius: 3px; height: 6px; }
QSlider::sub-page:horizontal { background: #e8e8e8; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; border: 2px solid #111111;
                              border-radius: 9px; width: 18px; margin: -6px 0; }
QSlider::handle:horizontal:hover { background: #dedede; }
QSpinBox, QDoubleSpinBox { background: #0a0a0a; border: 1px solid #292929; border-radius: 8px;
           color: #f5f5f5; min-height: 25px; max-height: 25px; padding: 0 8px; font-weight: 600;
           selection-background-color: #ffffff; selection-color: #000000; }
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #ffffff; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; border: none; }
QPushButton#primary, QPushButton#secondary { border-radius: 9px; min-height: 36px;
                                            font-weight: 600; padding: 0 12px; }
QPushButton#primary { background: #f6f6f6; color: #070707; border: 1px solid #f6f6f6; }
QPushButton#primary:hover { background: #dddddd; }
QPushButton#secondary { background: #151515; color: #dddddd; border: 1px solid #2b2b2b; }
QPushButton#secondary:hover { background: #242424; }
QPushButton#secondary:disabled { background: #111111; color: #666666; border-color: #242424; }
QPushButton#reset { color: #bdbdbd; background: #151515; border: 1px solid #2b2b2b;
                   min-height: 26px; max-height: 26px; border-radius: 8px; padding: 0 10px; font-size: 11px; }
QPushButton#reset:hover { color: #f0f0f0; background: #1d1d1d; }
QLabel#status { color: #8b8b8b; font-size: 12px; }
QLabel#limit { color: #7a7a7a; font-size: 11px; }
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
        spacer.setFixedWidth(34)
        row.addWidget(spacer)
        row.addStretch()
        title = QLabel(APP_NAME)
        title.setObjectName("windowTitle")
        row.addWidget(title)
        row.addStretch()
        minimize = QPushButton()
        minimize.setObjectName("minimizeDot")
        minimize.setCursor(Qt.PointingHandCursor)
        minimize.setToolTip("Minimize")
        minimize.setAccessibleName("Minimize window")
        minimize.clicked.connect(window.showMinimized)
        close = QPushButton()
        close.setObjectName("closeDot")
        close.setCursor(Qt.PointingHandCursor)
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
    def __init__(self, title: str, minimum: int, maximum: int, default: int,
                 suffix: str = "%", scale: int = 1, parent=None):
        super().__init__(parent)
        self.setObjectName("exactControl")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        self.scale = scale
        column.setSpacing(2)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("controlLabel")
        heading.addWidget(label)
        heading.addStretch()
        self.number = QDoubleSpinBox() if scale != 1 else QSpinBox()
        if scale != 1:
            self.number.setDecimals(2)
            self.number.setSingleStep(1 / scale)
            self.number.setRange(minimum / scale, maximum / scale)
        else:
            self.number.setRange(minimum, maximum)
        self.number.setSuffix(suffix)
        self.number.setKeyboardTracking(False)
        self.number.setFixedWidth(82)
        self.number.setAlignment(Qt.AlignCenter)
        self.number.setAccessibleName(f"{title} exact value")
        self.number.setToolTip("Type an exact value. Arrow keys change one step at a time.")
        heading.addWidget(self.number)
        column.addLayout(heading)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setFixedHeight(19)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setAccessibleName(f"{title} slider")
        self.slider.setToolTip("Drag, or use Left/Right arrows for exact one-point changes")
        column.addWidget(self.slider)
        self.slider.valueChanged.connect(lambda value: self.number.setValue(value / scale if scale != 1 else value))
        self.number.valueChanged.connect(lambda value: self.slider.setValue(int(round(value * scale))))
        self.number.setValue(default / scale if scale != 1 else default)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int):
        self.number.setValue(value / self.scale if self.scale != 1 else value)


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
        self._loading_values = False
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
        QApplication.setWheelScrollLines(1)
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
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(0)
        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 12, 16, 12)
        panel_layout.setSpacing(5)
        header = QHBoxLayout()
        headline = QLabel("Adjust display colors")
        headline.setObjectName("headline")
        header.addWidget(headline)
        header.addStretch()
        reset = QPushButton("Reset")
        reset.setObjectName("reset")
        reset.setCursor(Qt.PointingHandCursor)
        reset.setAccessibleName("Reset all five values to neutral")
        reset.clicked.connect(self._reset_values)
        header.addWidget(reset)
        panel_layout.addLayout(header)

        self.saturation = ExactControl("Digital vibrance", 0, 300, 100)
        self.saturation.setToolTip("100% keeps the original saturation. Higher values boost color intensity.")
        self.hue = ExactControl("Hue", -180, 180, 0, suffix="°")
        self.brightness = ExactControl("Brightness", -20, 20, 0)
        self.contrast = ExactControl("Contrast", 50, 200, 100)
        self.gamma = ExactControl("Gamma", 50, 200, 100, suffix="", scale=100)
        self.gamma.setToolTip("1.00 is neutral. Gamma changes midtones on supported SDR displays.")
        self._controls = {
            "saturation": (self.saturation, 100),
            "hue": (self.hue, 0),
            "brightness": (self.brightness, 0),
            "contrast": (self.contrast, 100),
            "gamma": (self.gamma, 100),
        }
        for control, _default in self._controls.values():
            panel_layout.addWidget(control)
            control.number.valueChanged.connect(self._values_changed)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.apply_button = QPushButton("Apply colors")
        self.apply_button.setObjectName("primary")
        self.apply_button.setCursor(Qt.PointingHandCursor)
        self.apply_button.clicked.connect(self._apply_clicked)
        actions.addWidget(self.apply_button, 2)
        self.disable_button = QPushButton("Disable")
        self.disable_button.setObjectName("secondary")
        self.disable_button.setCursor(Qt.PointingHandCursor)
        self.disable_button.clicked.connect(self._disable_clicked)
        actions.addWidget(self.disable_button, 1)
        panel_layout.addSpacing(3)
        panel_layout.addLayout(actions)
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(30)
        panel_layout.addWidget(self.status_label)
        footer = QHBoxLayout()
        note = QLabel("Esc restores colors")
        note.setObjectName("limit")
        note.setToolTip("Press Esc while this window has focus, or click Revert. Some HDR or full-screen games may bypass the filter.")
        footer.addWidget(note)
        footer.addStretch()
        version = QLabel(f"Fleece • v{APP_VERSION} • local only")
        version.setObjectName("limit")
        footer.addWidget(version)
        panel_layout.addLayout(footer)
        layout.addWidget(panel)

    def values(self) -> ColorValues:
        return ColorValues(saturation=self.saturation.value(), contrast=self.contrast.value(),
                           brightness=self.brightness.value(), hue=self.hue.value(), gamma=self.gamma.value())

    def _restore_values(self):
        if self._settings is None:
            return
        self._loading_values = True
        try:
            for key, (control, default) in self._controls.items():
                try:
                    value = int(self._settings.value(key, default))
                    if control.slider.minimum() <= value <= control.slider.maximum():
                        control.set_value(value)
                except (TypeError, ValueError):
                    pass
        finally:
            self._loading_values = False

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
        if self._loading_values:
            return
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
            self._sync_state("Could not apply these values; preview will revert" if
                             self._preview_remaining is not None else "Could not apply these values")
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
                for key, (control, _default) in self._controls.items():
                    control.set_value(getattr(self._confirmed_values, key))
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
        except (ScreenEffectError, OSError) as error:
            self._sync_state("Could not restore previous colors")
            QMessageBox.critical(self, "Restore failed", str(error))

    def _reset_values(self):
        for control, default in self._controls.values():
            control.set_value(default)

    def closeEvent(self, event):
        try:
            restored = self.effect.disable()
        except (ScreenEffectError, OSError) as error:
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
    window.hue.number.setValue(179)
    assert window.hue.value() == 179
    window.gamma.number.setValue(1.03)
    assert window.gamma.slider.value() == 103 and window.gamma.value() == 103
    window.gamma.slider.setValue(104)
    assert abs(window.gamma.number.value() - 1.04) < 1e-9
    assert window.gamma.number.singleStep() == 0.01
    # All five sliders have enough travel for each integer step, including
    # every hue degree and each gamma hundredth, rather than mouse skips.
    for control, _default in window._controls.values():
        slider = control.slider
        assert slider.singleStep() == 1 and slider.pageStep() == 1
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
        reachable = {QStyle.sliderValueFromPosition(slider.minimum(), slider.maximum(), x, travel)
                     for x in range(max(0, travel) + 1)}
        assert set(range(slider.minimum(), slider.maximum() + 1)) <= reachable
    window._reset_values()
    assert matrices_match(color_matrix(ColorValues()), identity_matrix())
    baseline_ramp = tuple(index * 257 for _channel in range(3) for index in range(256))
    adjusted_ramp = gamma_ramp(baseline_ramp, 103)
    assert adjusted_ramp != baseline_ramp
    assert gamma_ramp(baseline_ramp, 100) == baseline_ramp
    assert all(adjusted_ramp[start] == 0 and adjusted_ramp[start + 255] == 65535
               for start in (0, 256, 512))
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
    # altering the desktop, using the darkest allowed setting.
    fake = FakeEffect()
    window.effect = fake
    window.testing = False
    window.contrast.set_value(50)
    window.brightness.set_value(-20)
    window._apply_clicked()
    assert fake.active and window._preview_remaining == 15
    assert not window.apply_button.isEnabled()  # avoid accidental double-click confirmation
    for _ in range(15):
        window._preview_tick()
    assert fake.disabled == 1 and not fake.active
    assert window._preview_remaining is None

    window._reset_values()
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
        f"{APP_NAME} {APP_VERSION}: five controls, exact values, color math, and preview restoration passed.\n",
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
