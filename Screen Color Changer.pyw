"""Fleece Screen Color Changer — compact Windows desktop color utility.

The display is never changed on launch or during the installer self-test.
Controls preview live; only Apply saves values and confirms the current preview.
"""

import ctypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parent
# The folder-local shortcut intentionally uses Python's isolated -I mode.
# Only this extracted tool folder is added for its two reviewed sibling modules.
sys.path.insert(0, str(ROOT))

MUTEX_NAME = "Local\\FleeceScreenColorChangerApp"
SETUP_GATE_NAME = "Local\\FleeceScreenColorChangerSetupGate"


class AlreadyRunningError(RuntimeError):
    """The named mutex belongs to the existing application, not this launch."""


class SetupInProgressError(RuntimeError):
    """Setup currently owns the short startup/repair exclusion gate."""


def _single_instance_mutex():
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel.ReleaseMutex.restype = ctypes.c_int
    gate = kernel.CreateMutexW(None, 0, SETUP_GATE_NAME)
    if not gate:
        raise OSError("Could not open the setup/startup lock")
    owns_gate = False
    try:
        result = kernel.WaitForSingleObject(gate, 0)
        if result == 258:
            raise SetupInProgressError("Setup or repair is running. Wait for it to finish, then open the shortcut again.")
        if result not in (0, 128):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise OSError("Could not verify the setup/startup lock")
        owns_gate = True
        handle = kernel.CreateMutexW(None, 1, MUTEX_NAME)
        existed = ctypes.get_last_error() == 183
        if not handle:
            raise OSError("Could not create the single-instance lock")
        if existed:
            kernel.CloseHandle(handle)
            raise AlreadyRunningError("Screen Color Changer is already open.")
        return kernel, handle
    finally:
        if owns_gate:
            kernel.ReleaseMutex(gate)
        kernel.CloseHandle(gate)


# Acquire before importing Qt or sibling application modules: a repair must
# never replace their files while a new process is loading them. Imports made
# by the isolated self-test and test harness deliberately do not take this lock.
_startup_mutex = None
_startup_already_running = False
if __name__ == "__main__" and len(sys.argv) == 1:
    try:
        _startup_mutex = _single_instance_mutex()
    except AlreadyRunningError:
        _startup_already_running = True
    except (RuntimeError, OSError) as error:
        if os.name == "nt":
            ctypes.WinDLL("user32").MessageBoxW(None, str(error), "Screen Color Changer", 0x30)
        raise SystemExit(1)

from color_math import ColorValues, color_matrix, gamma_ramp, identity_matrix, matrices_match
from screen_backend import ScreenEffect, ScreenEffectError

from PySide6.QtCore import QObject, QPoint, QSettings, Qt, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QMouseEvent, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Screen Color Changer"
APP_VERSION = "1.0.0"
WINDOW_SIZE = 496
SETTINGS_PATH = ROOT / ".runtime" / "settings.ini"
RECOVERY_PATH = ROOT / ".runtime" / "color-recovery.json"
LIVE_PREVIEW_INTERVAL_MS = 75
PREVIEW_IDLE_SECONDS = 15
IPC_OPEN_COMMAND = b"FLEECE_OPEN_SETTINGS_V1\n"
IPC_OPEN_ACK = b"FLEECE_SETTINGS_OPEN_V1\n"
IPC_TIMEOUT_MS = 2000
IPC_MAX_CLIENTS = 4


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
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: #0d0d0d; }
QScrollBar:horizontal { height: 8px; background: #0d0d0d; }
QScrollBar::handle { background: #444444; border-radius: 4px; min-height: 24px; min-width: 24px; }
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
    inputActivity = Signal()

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
        self._pending_text = False
        self.number.lineEdit().textEdited.connect(self._text_edited)
        self.number.editingFinished.connect(self._editing_finished)
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

    def _text_edited(self, _text):
        self._pending_text = True
        self.inputActivity.emit()

    def _editing_finished(self):
        self._pending_text = False

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int):
        self._pending_text = False
        self.number.setValue(value / self.scale if self.scale != 1 else value)


class OperationBridge(QObject):
    """Only completion notifications cross from the display thread to Qt."""
    completed = Signal(object, object)


class ColorWindow(QWidget):
    def __init__(self, testing: bool = False, effect=None, async_operations=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.testing = testing
        self.effect = effect if effect is not None else ScreenEffect(
            recovery_path=None if testing else RECOVERY_PATH
        )
        self.setWindowTitle(APP_NAME)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(WINDOW_SIZE, WINDOW_SIZE)
        self.setMaximumSize(WINDOW_SIZE, WINDOW_SIZE)
        self._async_operations = not testing if async_operations is None else async_operations
        self._executor = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="Fleece-display")
                          if self._async_operations else None)
        self._operation_bridge = OperationBridge(self)
        self._operation_bridge.completed.connect(self._operation_finished, Qt.QueuedConnection)
        self._operation_epoch = 0
        self._active_request = None
        self._queued_request = None
        self._worker_stopped = False
        self._worker_values = None
        self._close_ready = False
        self._last_input = time.monotonic()
        self._warning_acknowledged = False
        self._pending_recovery = not testing
        self._preview_remaining = None
        self._preview_values = None
        self._confirmed_values = None
        self._loading_values = False
        self._preview_busy = False
        self._preview_paused = False
        self._safety_restoring = False
        self._closing = False
        self._exit_requested = False
        self._tray = None
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
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(LIVE_PREVIEW_INTERVAL_MS)
        self._live_timer.timeout.connect(self._render_live_preview)
        self._escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._escape.activated.connect(self._disable_clicked)
        QApplication.instance().setStyleSheet(STYLE)
        QApplication.setWheelScrollLines(1)
        self._build_ui()
        self._fit_work_area()
        self._restore_values()
        self._saved_values = self.values()
        self._sync_state("Move a slider to preview • Apply saves")
        if not testing:
            self._build_tray()
        if self._pending_recovery:
            self.apply_button.setEnabled(False)
            QTimer.singleShot(0, self._offer_recovery)

    def _build_ui(self):
        frame = QFrame(self)
        frame.setObjectName("windowFrame")
        self.frame = frame
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        page = QVBoxLayout(frame)
        page.setContentsMargins(1, 1, 1, 1)
        page.setSpacing(0)
        page.addWidget(TitleBar(self))

        body = QWidget()
        # Keep the full-size precision slider layout inside a scrollable body
        # on smaller/high-DPI work areas rather than clipping bottom controls.
        # The outer frame's border and layout margins consume four pixels.
        # Keep the precision layout without forcing a horizontal bar at 496px.
        body.setMinimumWidth(WINDOW_SIZE - 4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        body.setAutoFillBackground(False)
        # A selectorless stylesheet here also clears every child background,
        # including the primary button, leaving black text on a black surface.
        scroll.viewport().setObjectName("colorViewport")
        scroll.viewport().setStyleSheet("QWidget#colorViewport { background: transparent; }")
        self.scroll_area = scroll
        page.addWidget(scroll)
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
        reset.setAccessibleName("Reset values and restore original desktop colors")
        reset.setToolTip("Immediately restore the prior desktop colors and reset all five saved values.")
        reset.clicked.connect(self._reset_values)
        self.reset_button = reset
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
            control.inputActivity.connect(self._input_activity)

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
        self.status_label.setMinimumHeight(30)
        panel_layout.addWidget(self.status_label)
        footer = QHBoxLayout()
        note = QLabel("Esc restores colors")
        note.setObjectName("limit")
        note.setToolTip("Esc, Disable and tray Exit restore prior desktop colors. Closing before Apply restores colors; after Apply, closing keeps them in the tray when available. Only Apply saves values. Some HDR or full-screen games may bypass the filter.")
        footer.addWidget(note)
        footer.addStretch()
        version = QLabel(f"Fleece • v{APP_VERSION} • local only")
        version.setObjectName("limit")
        footer.addWidget(version)
        panel_layout.addLayout(footer)
        layout.addWidget(panel)

    def _fit_work_area(self, available=None):
        screen = self.screen() or QApplication.primaryScreen()
        available = available if available is not None else screen.availableGeometry()
        self.resize(min(WINDOW_SIZE, max(1, available.width())),
                    min(WINDOW_SIZE, max(1, available.height())))
        if self.isVisible():
            position = self.pos()
            self.move(max(available.left(), min(position.x(), available.right() - self.width() + 1)),
                      max(available.top(), min(position.y(), available.bottom() - self.height() + 1)))

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return  # Without a tray, closing always safely restores the desktop.
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#f5f5f5"))
        for y, knob in ((8, 12), (16, 22), (24, 9)):
            painter.drawLine(4, y, 28, y)
            painter.setBrush(QColor("#f5f5f5"))
            painter.drawEllipse(knob - 3, y - 3, 6, 6)
        painter.end()
        self.setWindowIcon(QIcon(pixmap))
        self._tray = QSystemTrayIcon(QIcon(pixmap), self)
        self._tray.setToolTip("Screen Color Changer • applied filter")
        menu = QMenu(self)
        show = QAction("Open settings", menu)
        show.triggered.connect(self._show_from_tray)
        menu.addAction(show)
        reset = QAction("Reset colors", menu)
        reset.triggered.connect(self._reset_from_tray)
        menu.addAction(reset)
        menu.addSeparator()
        exit_action = QAction("Exit and restore original colors", menu)
        exit_action.triggered.connect(self._exit_clicked)
        menu.addAction(exit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._tray_activated)
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self._fit_work_area()
        self.raise_()
        self.activateWindow()

    def _exit_clicked(self):
        self._exit_requested = True
        self.close()

    def _reset_from_tray(self):
        # Reset hides the tray icon once no filter is active. Reopen first so
        # this never leaves an invisible utility with no way back to settings.
        self._show_from_tray()
        self._reset_values()

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

    def _save_values(self, values=None) -> bool:
        if self._settings is None:
            return False
        for key, value in vars(values if values is not None else self.values()).items():
            self._settings.setValue(key, value)
        self._settings.sync()
        return self._settings.status() == QSettings.NoError

    def _set_controls(self, values: ColorValues, preserve_edits=False):
        self._loading_values = True
        try:
            for key, (control, _default) in self._controls.items():
                if preserve_edits and control._pending_text:
                    continue
                control.set_value(getattr(values, key))
        finally:
            self._loading_values = False

    def _sync_state(self, message: str):
        self.status_label.setText(message)
        self.reset_button.setEnabled(not self._pending_recovery)
        self.disable_button.setEnabled(not self._pending_recovery and not self._closing and
                                       (self.effect.active or self._live_timer.isActive() or self._preview_busy))
        self.disable_button.setText("Revert now" if self._preview_remaining is not None else "Disable")
        confirmed = (self.effect.active and self.values() == self._confirmed_values
                     and self.values() == self._preview_values and not self._live_timer.isActive())
        self.apply_button.setText("Applied" if confirmed else "Apply colors")
        self.apply_button.setEnabled(not self._pending_recovery and not self._preview_busy
                                     and not self._closing and not self._safety_restoring)
        locked = self._closing or any(request is not None and request["kind"] in
                                     ("disable", "reset", "timeout", "close")
                                     for request in (self._active_request, self._queued_request))
        for control, _default in self._controls.values():
            control.setEnabled(not locked and not self._pending_recovery)
        close = self.findChild(QPushButton, "closeDot")
        if close is not None:
            close_text = ("Close to tray; discard unapplied edits" if self._tray is not None
                          and self._confirmed_values is not None else "Close and restore previous colors")
            close.setToolTip(close_text)
            close.setAccessibleName(close_text)

    def _input_activity(self):
        if self._loading_values or self.testing or self._closing or self._preview_paused:
            return
        self._last_input = time.monotonic()
        if self._preview_remaining is not None:
            self._preview_remaining = PREVIEW_IDLE_SECONDS

    def _operation_request(self, kind, values=None, explicit=False, barrier=False):
        """One running operation and one replaceable pending operation, never a backlog."""
        if self._worker_stopped:
            return False
        if barrier:
            self._operation_epoch += 1
            self._queued_request = None
            self._live_timer.stop()
        request = {"kind": kind, "values": values, "explicit": explicit,
                   "epoch": self._operation_epoch, "previous": self._preview_values,
                   "confirmed": self._confirmed_values, "ack": self._warning_acknowledged,
                   "keep": self._tray is not None and self._confirmed_values is not None
                           and not self._exit_requested}
        if self._active_request is not None:
            # Safety/explicit requests cannot be replaced by a later drag.
            if kind == "preview" and self._queued_request is not None and self._queued_request["kind"] != "preview":
                return False
            self._queued_request = request
            return True
        self._start_operation(request)
        return True

    def _start_operation(self, request):
        self._active_request = request
        self._preview_busy = True
        self._sync_state(self.status_label.text())
        future = self._executor.submit(self._execute_operation, request)

        def complete(done):
            try:
                result = done.result()
            except Exception as error:
                result = {"error": error, "active": self.effect.active}
            try:
                self._operation_bridge.completed.emit(request, result)
            except RuntimeError:
                pass  # OS shutdown can remove the Qt receiver before teardown.

        future.add_done_callback(complete)

    def _execute_operation(self, request):
        """Every production display read/write runs on this single worker thread."""
        kind, values = request["kind"], request["values"]
        result = {}
        try:
            if kind in ("preview", "confirm"):
                if not self.effect.active:
                    self.effect.initialize()
                    if self.effect.replaces_existing_effect and not request["ack"]:
                        return {"permission": True, "active": self.effect.active}
                if not self.effect.active or values != self._worker_values:
                    self.effect.apply(values)
                self.effect.verify_current(values)
                self._worker_values = values
                result["values"] = values
            elif kind == "timeout":
                confirmed = request["confirmed"]
                if confirmed is not None:
                    try:
                        self.effect.apply(confirmed)
                        self.effect.verify_current(confirmed)
                        self._worker_values = confirmed
                        result["values"] = confirmed
                    except (ScreenEffectError, OSError):
                        result["restored"] = self.effect.disable()
                        self._worker_values = None
                        result["lost_confirmation"] = True
                else:
                    result["restored"] = self.effect.disable()
                    self._worker_values = None
            elif kind in ("disable", "reset", "close"):
                if kind == "close" and request["keep"]:
                    try:
                        confirmed = request["confirmed"]
                        # Never infer what is on screen from a stale queued UI
                        # snapshot: a preceding operation may have changed it.
                        self.effect.apply(confirmed)
                        self.effect.verify_current(confirmed)
                        self._worker_values = confirmed
                        result["values"] = confirmed
                        result["kept"] = True
                    except (ScreenEffectError, OSError):
                        result["restored"] = self.effect.disable()
                        self._worker_values = None
                else:
                    result["restored"] = self.effect.disable()
                    self._worker_values = None
            elif kind == "recovery_check":
                result["needed"] = self.effect.recovery_needed()
            elif kind == "recover":
                self.effect.recover_previous(values)
                result["restored"] = values
            elif kind != "close_pending":
                raise RuntimeError("Unknown display operation")
        except Exception as error:
            self._worker_values = None
            result["error"] = error
        result["active"] = self.effect.active
        return result

    @Slot(object, object)
    def _operation_finished(self, request, result):
        if self._active_request is not request:
            return
        self._active_request = None
        self._preview_busy = False
        if self._worker_stopped:
            self._queued_request = None
            return  # Final restoration already ran; never revive UI timers.
        stale = request["epoch"] != self._operation_epoch
        if not stale:
            self._handle_operation_result(request, result)
        # A modal confirmation may have allowed another Qt event to start the
        # next operation already; never dispatch a second concurrent operation.
        if self._active_request is None and self._queued_request is not None and not self._worker_stopped:
            next_request, self._queued_request = self._queued_request, None
            self._start_operation(next_request)
        self._sync_state(self.status_label.text())

    def _handle_operation_result(self, request, result):
        kind = request["kind"]
        error = result.get("error")
        if error is not None:
            if kind in ("recovery_check", "recover"):
                self._sync_state(f"Recovery needs attention • {error}")
                QMessageBox.critical(self, "Color recovery unavailable", str(error))
                return
            if result.get("active"):
                # An unsafe partial write has priority over every later drag.
                # Otherwise a continuous stream of failed previews can keep
                # the serialized worker busy and starve its safety timer.
                self._preview_paused = True
                self._safety_restoring = True
                self._live_timer.stop()
                if self._queued_request is not None and self._queued_request["kind"] in ("preview", "confirm"):
                    self._queued_request = None
                if kind == "close":
                    # A partially restored profile is no longer an Applied
                    # profile. The watchdog must restore its baseline, not
                    # reapply the profile that the user was trying to exit.
                    self._confirmed_values = None
                self._preview_remaining = 1
                self._preview_timer.start()
            if kind == "close":
                self._closing = False
                self._exit_requested = False
                self._sync_state(f"Restore failed • retry Disable • {error}")
                QMessageBox.critical(self, "Colors could not be restored",
                                     f"{error}\n\nThe window will stay open so you can retry Disable.")
            else:
                self._sync_state(f"{'Preview unavailable' if kind in ('preview', 'confirm') else 'Restore failed'} • {error}")
            # Busy is cleared before any nested dialog loop, so safety timers
            # can restore a failed filter even while this warning stays open.
            if kind == "confirm":
                QMessageBox.warning(self, "Color effect unavailable", str(error))
            return
        if result.get("permission"):
            choice = QMessageBox.question(
                self, "Existing desktop color effect",
                "Another Windows color effect is already active. Live preview will temporarily replace it. "
                "Disable, Reset or Exit restores it when this app still owns it. Applied colors can keep running "
                "in the tray. Continue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            # A close/reset can happen during the nested dialog event loop.
            if self._closing or request["epoch"] != self._operation_epoch:
                return
            if choice != QMessageBox.Yes:
                self._preview_paused = True
                self._operation_request("disable", barrier=True)
                self._sync_state("Live preview paused • existing effect left alone")
            else:
                self._warning_acknowledged = True
                self._operation_request(kind, request["values"], request["explicit"])
            return
        if kind == "recovery_check":
            if result["needed"]:
                choice = QMessageBox.question(self, "Restore previous desktop colors?",
                    "A previous session may have ended while its color effect was active. Restore the colors "
                    "that were in place before it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if not self._closing:
                    self._operation_request("recover", choice == QMessageBox.Yes)
                return
            self._pending_recovery = False
            self._sync_state("Move a slider to preview • Apply saves")
            return
        if kind == "recover":
            self._pending_recovery = False
            self._sync_state("Previous colors restored" if result["restored"] else "Previous effect left as you chose")
            return
        if kind in ("preview", "confirm"):
            shown = result["values"]
            self._preview_values = shown
            if kind == "confirm":
                self._confirmed_values = shown
                saved = self._save_values(shown)
                if saved:
                    self._saved_values = shown
                self._stop_preview()
                if self._tray is not None:
                    self._tray.show()
                self._sync_state("Applied • saved locally • close keeps in tray" if saved and self._tray is not None else
                                 "Applied • saved locally • close restores desktop" if saved else
                                 "Applied • settings could not be saved locally")
                # Edits made while confirmation was running remain unconfirmed
                # and must still be rendered, not saved as the clicked profile.
                if self.values() != shown and not self._closing:
                    self._operation_request("preview", self.values())
            elif shown == self._confirmed_values:
                self._stop_preview()
                self._sync_state("Applied values restored • close keeps in tray" if self._tray is not None else
                                 "Applied values restored • close restores desktop")
            else:
                self._preview_remaining = PREVIEW_IDLE_SECONDS
                self._preview_timer.start()
                self._sync_state("Live preview • Apply saves • reverts after 15s idle")
            return
        if kind == "close" and result.get("kept"):
            self._safety_restoring = False
            self._set_controls(result["values"])
            self._preview_values = result["values"]
            self._stop_preview()
            self._sync_state("Applied colors running in tray • Exit restores original colors")
            self.hide()
            self._closing = False
            return
        if kind in ("close", "close_pending"):
            self._safety_restoring = False
            self._stop_preview(clear_values=True)
            self._confirmed_values = None
            if self._tray is not None:
                self._tray.hide()
            self._close_ready = True
            self.close()
            if self._tray is not None:
                QApplication.instance().quit()
            return
        self._stop_preview(clear_values=True)
        self._safety_restoring = False
        if kind == "timeout" and result.get("values") is not None:
            self._preview_values = result["values"]
            self._set_controls(result["values"], preserve_edits=True)
            self._sync_state("Preview ended • last applied values restored")
            return
        self._confirmed_values = None
        if self._tray is not None:
            self._tray.hide()
        if kind == "reset":
            self._preview_paused = False
            self._set_controls(ColorValues())
            self._saved_values = ColorValues()
            saved = self._save_values()
            self._sync_state("Reset • prior desktop colors restored" if result.get("restored") and saved else
                             "Reset • settings could not be saved" if result.get("restored") else
                             "Reset • another app's color effect was left alone")
        else:
            self._set_controls(self._saved_values, preserve_edits=kind == "timeout")
            self._sync_state("Previous colors restored" if result.get("restored") else
                             "Another app changed colors; its effect was left alone")

    def _values_changed(self, _value: int):
        if self._loading_values or self.testing or self._pending_recovery or self._closing:
            return
        self._input_activity()
        if self._preview_paused:
            self._sync_state("Safety restoration in progress • Apply is available after it finishes" if self._safety_restoring else
                             "Live preview paused • Apply to try again")
            return
        if self._preview_remaining is not None:
            self._preview_remaining = PREVIEW_IDLE_SECONDS
        # Throttle, rather than restart the timer on every event: a continuous
        # drag must still update the desktop. Only the newest values are used.
        if not self._live_timer.isActive():
            self._live_timer.start()
        self._sync_state("Updating live preview • Apply saves")

    def _apply_clicked(self):
        if self.testing or self._pending_recovery or self._closing:
            return
        if self._safety_restoring:
            self._sync_state("Safety restoration in progress • Apply is available after it finishes")
            return
        self._preview_paused = False
        if self._async_operations:
            self._live_timer.stop()
            self._operation_request("confirm", self.values(), explicit=True)
            self._sync_state("Applying and verifying colors • please wait")
            return
        # Flush a queued change before confirming so Apply can never save an
        # older rendered value while newer slider values are still pending.
        if not self._render_live_preview(explicit=True):
            return
        try:
            self.effect.verify_current(self.values())
        except (ScreenEffectError, OSError) as error:
            self._sync_state(f"Apply unavailable • {error}")
            QMessageBox.warning(self, "Color effect unavailable", str(error))
            return
        self._confirmed_values = self.values()
        saved = self._save_values()
        if saved:
            self._saved_values = self._confirmed_values
        self._stop_preview()
        if self._tray is not None:
            self._tray.show()
        self._sync_state("Applied • saved locally • close keeps in tray" if saved and self._tray is not None else
                         "Applied • saved locally • close restores desktop" if saved else
                         "Applied • settings could not be saved locally")

    def _render_live_preview(self, explicit: bool = False) -> bool:
        self._live_timer.stop()
        if self.testing or self._pending_recovery or self._closing:
            return False
        if self._preview_paused:
            self._sync_state("Safety restoration in progress • Apply is available after it finishes" if self._safety_restoring else
                             "Live preview paused • Apply to try again")
            return False
        if self._async_operations:
            return self._operation_request("confirm" if explicit else "preview", self.values(), explicit)
        if self._preview_busy:
            return False
        values = self.values()
        self._preview_busy = True
        try:
            if self.effect.active and values == self._preview_values:
                self.effect.verify_current(values)
                if self._preview_remaining is not None:
                    self._preview_remaining = PREVIEW_IDLE_SECONDS
                return True
            if not self.effect.active:
                self.effect.initialize()
                if self.effect.replaces_existing_effect and not self._warning_acknowledged:
                    choice = QMessageBox.question(
                        self, "Existing desktop color effect",
                        "Another Windows color effect is already active. Live preview will temporarily "
                        "replace it. Disable, Reset or Exit restores it when this app still owns it. "
                        "Applied colors can keep running in the tray. Continue?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    )
                    if choice != QMessageBox.Yes:
                        self.effect.disable()
                        self._preview_paused = True
                        self._sync_state("Live preview paused • existing effect left alone")
                        return False
                    self._warning_acknowledged = True
            self.effect.apply(values)
            self._preview_values = values
            if values == self._confirmed_values:
                self._stop_preview()
                message = ("Applied values restored • close keeps in tray" if self._tray is not None else
                           "Applied values restored • close restores desktop")
            else:
                self._preview_remaining = PREVIEW_IDLE_SECONDS
                self._preview_timer.start()
                message = "Live preview • Apply saves • reverts after 15s idle"
            self.status_label.setText(message)
            return True
        except (ScreenEffectError, OSError) as error:
            # Inline feedback avoids repeated modal dialogs during a drag.
            if self.effect.active and self._preview_remaining is None:
                # The first preview can partially mutate a display before a
                # driver/persistence failure also blocks rollback. It needs
                # the same automatic safety restoration as a valid preview,
                # even though no successful preview values were recorded.
                self._preview_remaining = 1
                self._preview_timer.start()
            if self.effect.active:
                self._preview_paused = True
                self._safety_restoring = True
                self._live_timer.stop()
                self._preview_remaining = 1
                self._preview_timer.start()
            self.status_label.setText(f"Preview unavailable • {error}")
            self.status_label.setToolTip(str(error))
            if explicit:
                self._preview_busy = False
                QMessageBox.warning(self, "Color effect unavailable", str(error))
            return False
        finally:
            self._preview_busy = False
            self._sync_state(self.status_label.text())

    def _stop_preview(self, clear_values: bool = False):
        self._preview_timer.stop()
        self._live_timer.stop()
        self._preview_remaining = None
        if clear_values:
            self._preview_values = None

    def _preview_tick(self):
        if self._preview_remaining is None:
            return
        if self._live_timer.isActive() or self._preview_busy:
            return
        self._preview_remaining -= 1
        if self._preview_remaining > 0:
            self._sync_state(f"Live preview • Apply saves • reverts in {self._preview_remaining}s")
            return
        if self._async_operations:
            self._operation_request("timeout", barrier=True)
            self._sync_state("Restoring colors • please wait")
            return
        try:
            if self._confirmed_values is not None:
                self.effect.apply(self._confirmed_values)
                self._set_controls(self._confirmed_values, preserve_edits=True)
                self._preview_values = self._confirmed_values
                message = "Preview ended • last applied values restored"
            else:
                restored = self.effect.disable()
                self._set_controls(self._saved_values, preserve_edits=True)
                self._preview_values = None
                message = ("Preview ended • prior colors restored" if restored else
                           "Another app changed colors; its effect was left alone")
            self._stop_preview()
            self._safety_restoring = False
            self._sync_state(message)
        except (ScreenEffectError, OSError):
            if self._confirmed_values is not None:
                try:
                    restored = self.effect.disable()
                    self._confirmed_values = None
                    self._set_controls(self._saved_values)
                    self._stop_preview(clear_values=True)
                    self._safety_restoring = False
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
        if self._async_operations:
            self._operation_request("recovery_check")
            self._sync_state("Checking previous color session • please wait")
            return
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
        self._sync_state("Move a slider to preview • Apply saves")

    def _disable_clicked(self):
        if self._pending_recovery or self._closing:
            return
        self._live_timer.stop()
        if self._async_operations:
            self._operation_request("disable", barrier=True)
            self._sync_state("Restoring previous colors • please wait")
            return
        try:
            restored = self.effect.disable()
            self._stop_preview(clear_values=True)
            self._safety_restoring = False
            self._confirmed_values = None
            if self._tray is not None:
                self._tray.hide()
            self._set_controls(self._saved_values)
            message = ("Previous colors restored" if restored
                       else "Another app changed colors; its effect was left alone")
            self._sync_state(message)
        except (ScreenEffectError, OSError) as error:
            self._sync_state("Could not restore previous colors")
            QMessageBox.critical(self, "Restore failed", str(error))

    def _reset_values(self):
        if self._pending_recovery or self._closing:
            return
        self._live_timer.stop()
        if self._async_operations:
            self._operation_request("reset", barrier=True)
            self._sync_state("Resetting colors • please wait")
            return
        try:
            restored = self.effect.disable()
        except (ScreenEffectError, OSError) as error:
            self._sync_state("Reset failed • try Disable again")
            if not self.testing:
                QMessageBox.critical(self, "Reset failed", str(error))
            return
        self._stop_preview(clear_values=True)
        self._safety_restoring = False
        self._confirmed_values = None
        self._preview_paused = False
        self._set_controls(ColorValues())
        self._saved_values = ColorValues()
        saved = self._save_values() if not self.testing else True
        if self._tray is not None:
            self._tray.hide()
        self._sync_state("Reset • prior desktop colors restored" if restored and saved else
                         "Reset • settings could not be saved" if restored else
                         "Reset • another app's color effect was left alone")

    def closeEvent(self, event):
        if self._async_operations:
            if self._close_ready:
                event.accept()
                return
            event.ignore()
            if self._closing:
                return
            self._closing = True
            self._live_timer.stop()
            self._operation_request("close_pending" if self._pending_recovery else "close", barrier=True)
            self._sync_state("Finishing color restoration • please wait")
            return
        self._closing = True
        self._live_timer.stop()
        if self._pending_recovery:
            # An unresolved prior session owns the recovery record, not this
            # window. Exiting must not delete that session's saved baseline.
            self._stop_preview(clear_values=True)
            if self._tray is not None:
                self._tray.hide()
                QApplication.instance().quit()
            event.accept()
            return
        if self._tray is not None and self._confirmed_values is not None and not self._exit_requested:
            try:
                # Discard any later unconfirmed edit before keeping only the
                # last explicitly applied filter in the background.
                if not self.effect.active or self._preview_values != self._confirmed_values:
                    self.effect.apply(self._confirmed_values)
                self.effect.verify_current(self._confirmed_values)
                self._set_controls(self._confirmed_values)
                self._preview_values = self._confirmed_values
                self._stop_preview()
                self._sync_state("Applied colors running in tray • Exit restores original colors")
                self.hide()
                self._closing = False
                event.ignore()
                return
            except (ScreenEffectError, OSError):
                # If another app/display now owns a layer, do not overwrite it
                # just to retain a profile. Fall back to safe relinquishment.
                self._confirmed_values = None
        try:
            restored = self.effect.disable()
        except (ScreenEffectError, OSError) as error:
            self._closing = False
            self._exit_requested = False
            if self.effect.active:
                self._preview_paused = True
                self._safety_restoring = True
                self._confirmed_values = None
                self._preview_remaining = 1
                self._preview_timer.start()
            QMessageBox.critical(
                self, "Colors could not be restored",
                f"{error}\n\nThe window will stay open so you can retry Disable.",
            )
            event.ignore()
            return
        if not restored:
            self.status_label.setText("Another app now controls the color effect")
        self._stop_preview(clear_values=True)
        self._confirmed_values = None
        if self._tray is not None:
            self._tray.hide()
            QApplication.instance().quit()
        event.accept()

    def _cleanup_on_quit(self):
        if self._async_operations:
            self._shutdown_worker(wait=False)
            return
        if self._pending_recovery:
            return
        try:
            self.effect.disable()
        except (ScreenEffectError, OSError):
            # The backend retains recovery data when restoration fails. An OS
            # shutdown cannot keep this window open; next launch offers repair.
            pass

    def _shutdown_worker(self, wait=True):
        """Final restoration is ordered after in-flight writes on their owning thread."""
        if self._executor is None:
            return
        if not self._worker_stopped:
            self._worker_stopped = True
            self._operation_epoch += 1
            self._closing = True
            self._queued_request = None
            self._stop_preview(clear_values=True)
            if not self._pending_recovery:
                def restore_before_exit():
                    try:
                        self.effect.disable()
                    except Exception:
                        # Backend preserves unresolved ownership/recovery data.
                        pass
                self._executor.submit(restore_before_exit)
        self._executor.shutdown(wait=wait)


def _instance_ipc_name():
    # Windows pipes are not scoped by the Local mutex namespace. Include the
    # actual session ID so another session of the same user cannot be reopened.
    # The pipe ACL (UserAccessOption), not its predictable name, isolates users.
    if os.name != "nt":
        raise OSError("Settings reopening requires the Windows local session.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    session = ctypes.c_uint32()
    kernel.ProcessIdToSessionId.argtypes = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32))
    kernel.ProcessIdToSessionId.restype = ctypes.c_int
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise OSError("Could not verify the local Windows session for settings reopening.")
    return f"FleeceScreenColorChangerSettings-v1-session-{session.value}"


class InstanceReopener(QObject):
    """User-only local IPC with one fixed, bounded, display-read-only command."""
    def __init__(self, window, server_name=None):
        super().__init__(window)
        self.window = window
        self._clients = {}
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.setMaxPendingConnections(IPC_MAX_CLIENTS)
        self.server.newConnection.connect(self._accept_connections)
        if not self.server.listen(server_name or _instance_ipc_name()):
            raise OSError("Could not open the private settings-reopen channel. Close the existing utility and try again.")

    def _finish_client(self, socket, abort=True):
        state = self._clients.pop(socket, None)
        if state is None:
            return
        state["timer"].stop()
        if abort:
            socket.abort()
        socket.deleteLater()

    def _accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self._clients) >= IPC_MAX_CLIENTS:
                socket.abort()
                socket.deleteLater()
                continue
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.setInterval(IPC_TIMEOUT_MS)
            self._clients[socket] = {"timer": timer, "data": bytearray()}
            timer.timeout.connect(lambda s=socket: self._finish_client(s))
            socket.disconnected.connect(lambda s=socket: self._finish_client(s, abort=False))
            socket.readyRead.connect(lambda s=socket: self._read_command(s))
            timer.start()
            self._read_command(socket)

    def _read_command(self, socket):
        state = self._clients.get(socket)
        if state is None:
            return
        # No arbitrary text, filenames, color values or launch commands enter
        # the UI. Read at most one byte beyond the fixed command's length.
        state["data"].extend(bytes(socket.read(len(IPC_OPEN_COMMAND) + 1)))
        data = bytes(state["data"])
        if not IPC_OPEN_COMMAND.startswith(data) or socket.bytesAvailable():
            self._finish_client(socket)
            return
        if data != IPC_OPEN_COMMAND:
            return
        if self.window._closing or self.window._exit_requested:
            self._finish_client(socket)
            return
        self.window._show_from_tray()
        if not self.window.isVisible():
            self._finish_client(socket)
            return
        socket.write(IPC_OPEN_ACK)
        socket.disconnectFromServer()  # Qt drains its pending reply first.

    def close(self):
        self.server.close()
        for socket in list(self._clients):
            self._finish_client(socket)


def _request_existing_window(server_name=None):
    """Bounded secondary launch; never constructs a window or color backend."""
    socket = QLocalSocket()
    deadline = time.monotonic() + IPC_TIMEOUT_MS / 1000

    def remaining():
        return max(0, int((deadline - time.monotonic()) * 1000))

    try:
        socket.connectToServer(server_name or _instance_ipc_name())
        if not socket.waitForConnected(remaining()):
            return False
        if socket.write(IPC_OPEN_COMMAND) != len(IPC_OPEN_COMMAND):
            return False
        if socket.bytesToWrite() and not socket.waitForBytesWritten(remaining()):
            return False
        reply = bytearray()
        while remaining():
            if socket.bytesAvailable():
                reply.extend(bytes(socket.read(len(IPC_OPEN_ACK) + 1)))
                if not IPC_OPEN_ACK.startswith(bytes(reply)) or socket.bytesAvailable():
                    return False
                if bytes(reply) == IPC_OPEN_ACK:
                    return True
            if not socket.waitForReadyRead(remaining()):
                return False
        return False
    finally:
        socket.abort()


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
        calls = 0

        def initialize(self):
            return None

        def apply(self, values):
            self.active = True
            self.applied = values
            self.calls += 1

        def verify_current(self, values):
            assert self.active and self.applied == values

        def disable(self):
            self.active = False
            self.disabled += 1
            return True

    # Exercise live previews and confirmation without any Windows display API.
    fake = FakeEffect()
    window.effect = fake
    window.testing = False
    window._settings = QSettings(str(folder / "settings.ini"), QSettings.IniFormat)
    window.contrast.set_value(50)
    window.brightness.set_value(-20)
    assert not fake.active and window._live_timer.isActive()
    assert window._render_live_preview()
    assert fake.active and window._preview_remaining == 15
    assert window._settings.value("brightness") is None  # unconfirmed edits never save
    for _ in range(15):
        window._preview_tick()
    assert fake.disabled == 1 and not fake.active
    assert window._preview_remaining is None

    window._reset_values()
    window._apply_clicked()
    assert window._confirmed_values == ColorValues()
    assert window._preview_remaining is None
    assert int(window._settings.value("saturation")) == 100
    window.saturation.set_value(255)
    assert window._render_live_preview()
    assert fake.applied.saturation == 255
    assert int(window._settings.value("saturation")) == 100
    for _ in range(15):
        window._preview_tick()
    assert fake.active and fake.applied == ColorValues()
    assert window.saturation.value() == 100
    window.saturation.set_value(255)
    window._apply_clicked()  # flushes pending preview before saving exact values
    assert fake.applied.saturation == 255
    assert int(window._settings.value("saturation")) == 255
    before = fake.calls
    assert window._render_live_preview()
    assert fake.calls == before  # duplicate values never cause a display write
    window.gamma.set_value(103)
    assert window._render_live_preview()
    assert fake.applied.gamma == 103
    window.close()
    assert not fake.active and not window._live_timer.isActive()
    assert int(window._settings.value("gamma")) == 100  # close discards unapplied edits
    (folder / "self-test-passed.txt").write_text(
        f"{APP_NAME} {APP_VERSION}: exact live controls, Apply-only saving, and preview/close restoration passed.\n",
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
    mutex = _startup_mutex
    try:
        if _startup_already_running:
            raise AlreadyRunningError("Screen Color Changer is already open.")
        if mutex is None:
            mutex = _single_instance_mutex()
    except AlreadyRunningError:
        try:
            if _request_existing_window():
                return 0
        except (RuntimeError, OSError):
            pass
        QMessageBox.warning(None, APP_NAME,
            "Screen Color Changer is already open, but its settings window could not be reached. "
            "Try its tray icon or wait for it to finish starting, then run the shortcut again. "
            "No second filter session was started.")
        return 1
    except (RuntimeError, OSError) as error:
        QMessageBox.warning(None, APP_NAME, str(error))
        return 1
    reopener = None
    window = None
    try:
        window = ColorWindow()
        reopener = InstanceReopener(window)
        window.show()
        app.aboutToQuit.connect(window._cleanup_on_quit)
        return app.exec()
    except (RuntimeError, OSError) as error:
        QMessageBox.warning(None, APP_NAME, str(error))
        return 1
    finally:
        if window is not None:
            window._shutdown_worker()
        if reopener is not None:
            reopener.close()
        if mutex is not None:
            kernel, handle = mutex
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
