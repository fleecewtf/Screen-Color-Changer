"""Offscreen settings-only IPC; fake color effects never load display APIs."""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication
from test_live_preview_ui import FakeEffect


class InstanceReopenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["settings-reopen-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        loader = importlib.machinery.SourceFileLoader("color_ipc_tests", str(ROOT / "Screen Color Changer.pyw"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.ui = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.ui)

    def setUp(self):
        self.effect = FakeEffect()
        self.window = self.ui.ColorWindow(testing=True, effect=self.effect)
        self.name = "fleece-color-ipc-test-" + uuid.uuid4().hex
        self.reopener = self.ui.InstanceReopener(self.window, self.name)

    def tearDown(self):
        self.reopener.close()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def pump(self, milliseconds=30):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def request_from_secondary_thread(self, name=None):
        # A distinct process matches real launch behavior and avoids Python's
        # GIL delaying the main thread while a Windows pipe wait is pending.
        code = """
import importlib.machinery, importlib.util, pathlib, sys
root = pathlib.Path(sys.argv[1])
loader = importlib.machinery.SourceFileLoader('secondary_ipc_fixture', str(root / 'Screen Color Changer.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
ui = importlib.util.module_from_spec(spec)
loader.exec_module(ui)
app = ui.QApplication(['secondary-settings-test'])
raise SystemExit(0 if ui._request_existing_window(sys.argv[2]) else 1)
"""
        process = subprocess.Popen([sys.executable, "-I", "-c", code, str(ROOT), name or self.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            loop = QEventLoop()
            poll = QTimer()
            poll.setInterval(10)
            poll.timeout.connect(lambda: loop.quit() if process.poll() is not None else None)
            QTimer.singleShot(self.ui.IPC_TIMEOUT_MS + 2000, loop.quit)
            poll.start()
            loop.exec()
            poll.stop()
            self.assertIsNotNone(process.poll(), "Settings-only client exceeded its bounded deadline")
            return process.returncode == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            process.stderr.close()

    def test_user_restricted_channel_reopens_hidden_window_without_color_or_settings_write(self):
        self.assertEqual(self.reopener.server.socketOptions(), QLocalServer.UserAccessOption)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.request_from_secondary_thread())
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.effect.applications, [])
        self.assertEqual(self.effect.initializations, 0)
        self.assertIsNone(self.window._settings)

    def test_visible_or_minimized_window_is_reopened_without_duplicate_filter(self):
        self.window.show()
        self.assertTrue(self.request_from_secondary_thread())
        self.window.showMinimized()
        self.assertTrue(self.request_from_secondary_thread())
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertEqual(self.effect.applications, [])

    def test_arbitrary_or_oversized_commands_never_reopen_or_apply(self):
        for payload in (b"apply 255 150\n", self.ui.IPC_OPEN_COMMAND + b"extra", b"x" * 4096):
            with self.subTest(payload_size=len(payload)):
                socket = QLocalSocket()
                socket.connectToServer(self.name)
                self.assertTrue(socket.waitForConnected(1000))
                socket.write(payload)
                if socket.bytesToWrite():
                    socket.waitForBytesWritten(1000)
                self.pump()
                self.assertFalse(self.window.isVisible())
                self.assertEqual(self.effect.applications, [])
                self.assertEqual(bytes(socket.readAll()), b"")
                socket.abort()

    def test_partial_fixed_command_is_bounded_and_acknowledged_only_when_complete(self):
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        self.assertTrue(socket.waitForConnected(1000))
        command = self.ui.IPC_OPEN_COMMAND
        socket.write(command[:10])
        socket.waitForBytesWritten(1000)
        self.pump()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(bytes(socket.readAll()), b"")
        socket.write(command[10:])
        socket.waitForBytesWritten(1000)
        self.pump()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(bytes(socket.readAll()), self.ui.IPC_OPEN_ACK)
        socket.abort()

    def test_request_during_exit_is_rejected_without_reopening(self):
        self.window._exit_requested = True
        self.assertFalse(self.request_from_secondary_thread())
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.effect.applications, [])

    def test_unreachable_server_returns_failure_without_creating_another_window(self):
        self.reopener.close()
        self.assertFalse(self.request_from_secondary_thread())
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.effect.applications, [])

    def test_secondary_main_reopens_existing_instance_without_new_backend_or_mutex_close(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                app = Mock()
                with patch.object(self.ui.sys, "argv", ["Screen Color Changer.pyw"]), \
                     patch.object(self.ui, "QApplication", return_value=app), \
                     patch.object(self.ui, "_single_instance_mutex", side_effect=self.ui.AlreadyRunningError()), \
                     patch.object(self.ui, "_request_existing_window", return_value=accepted), \
                     patch.object(self.ui, "ColorWindow") as create_window, \
                     patch.object(self.ui.QMessageBox, "warning") as warning:
                    self.assertEqual(self.ui.main(), 0 if accepted else 1)
                    create_window.assert_not_called()
                    app.exec.assert_not_called()
                    self.assertEqual(warning.call_count, 0 if accepted else 1)

    def test_primary_mutex_remains_held_through_event_loop_and_ipc_close(self):
        app, kernel, window, channel = Mock(), Mock(), Mock(), Mock()

        def run_event_loop():
            kernel.CloseHandle.assert_not_called()
            channel.close.assert_not_called()
            return 0

        app.exec.side_effect = run_event_loop
        with patch.object(self.ui.sys, "argv", ["Screen Color Changer.pyw"]), \
             patch.object(self.ui, "QApplication", return_value=app), \
             patch.object(self.ui, "_single_instance_mutex", return_value=(kernel, 123)), \
             patch.object(self.ui, "ColorWindow", return_value=window), \
             patch.object(self.ui, "InstanceReopener", return_value=channel):
            self.assertEqual(self.ui.main(), 0)
        channel.close.assert_called_once()
        kernel.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
