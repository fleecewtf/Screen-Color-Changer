"""Offscreen Qt tests; all color effects and settings are isolated fakes."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PySide6.QtCore import QEventLoop, QSettings, QTimer
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QT_AVAILABLE = False
else:
    QT_AVAILABLE = True

from color_math import ColorValues, color_matrix, gamma_ramp, matrices_match
from screen_backend import ScreenEffect, ScreenEffectError
from test_color_effect import FakeGammaApi, FakeMagnification


class FakeEffect:
    """Never loads a DLL or reads/writes real Windows color state."""

    def __init__(self):
        self.active = False
        self.replaces_existing_effect = False
        self.initializations = 0
        self.applications = []
        self.disables = 0
        self.applied = None
        self.apply_error = None
        self.disable_error = None
        self.recovery_checks = 0
        self.recoveries = []

    def initialize(self):
        self.initializations += 1

    def apply(self, values):
        if self.apply_error is not None:
            raise self.apply_error
        self.applications.append(values)
        self.applied = values
        self.active = True

    def verify_current(self, values):
        if not self.active or self.applied != values:
            raise ScreenEffectError("Fake preview no longer matches")

    def disable(self):
        self.disables += 1
        if self.disable_error is not None:
            raise self.disable_error
        self.active = False
        self.applied = None
        return True

    def recovery_needed(self):
        self.recovery_checks += 1
        return False

    def recover_previous(self, restore):
        self.recoveries.append(restore)


class FakeTray:
    def __init__(self):
        self.shown = 0
        self.hidden = 0

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1


@unittest.skipUnless(QT_AVAILABLE, "Requires the folder-private PySide6 runtime")
class LivePreviewUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["live-preview-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        path = Path(__file__).resolve().parents[1] / "Screen Color Changer.pyw"
        loader = importlib.machinery.SourceFileLoader("screen_color_changer_ui_tests", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.ui = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.ui)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.effect = FakeEffect()
        self.window = self.ui.ColorWindow(testing=True, effect=self.effect)
        self.settings_path = Path(self.temporary.name) / "settings.ini"
        self.window._settings = QSettings(str(self.settings_path), QSettings.IniFormat)
        self.window.testing = False
        self.dialog_patches = [
            patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok),
            patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok),
        ]
        self.question, self.warning, self.critical = [item.start() for item in self.dialog_patches]

    def tearDown(self):
        self.window._stop_preview(clear_values=True)
        self.effect.apply_error = None
        self.effect.disable_error = None
        self.window._confirmed_values = None
        self.window._tray = None
        self.window.testing = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        for item in reversed(self.dialog_patches):
            item.stop()
        self.window._settings = None
        self.temporary.cleanup()

    def pump(self, milliseconds):
        """Run actual Qt timer events without blocking the Windows UI thread."""
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def expire_preview(self):
        self.window._preview_remaining = 1
        self.window._preview_timer.setInterval(5)
        self.window._preview_timer.start()
        self.pump(40)

    def test_launch_does_not_initialize_apply_restore_or_save(self):
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(self.effect.initializations, 0)
        self.assertEqual(self.effect.applications, [])
        self.assertEqual(self.effect.disables, 0)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(self.window._settings.allKeys(), [])
        self.assertFalse(self.settings_path.exists())

    def test_tray_reset_reopens_settings_instead_of_leaving_an_invisible_app(self):
        self.window._tray = FakeTray()
        self.window.saturation.set_value(150)
        self.window._apply_clicked()
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.effect.active)
        self.window._reset_from_tray()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.effect.active)
        self.assertIsNone(self.window._confirmed_values)
        self.assertEqual(self.window.values(), ColorValues())
        self.assertGreater(self.window._tray.hidden, 0)

    def test_sliders_and_exact_numbers_preview_latest_values_on_real_timer(self):
        self.window.saturation.slider.setValue(254)
        self.assertEqual(self.window.saturation.number.value(), 254)
        self.window.saturation.number.setValue(255)
        self.assertEqual(self.window.saturation.slider.value(), 255)
        self.window.gamma.number.setValue(1.03)
        self.assertEqual(self.window.gamma.slider.value(), 103)
        self.window.hue.slider.setValue(37)
        self.assertEqual(self.window.hue.number.value(), 37)
        self.assertEqual(self.effect.applications, [])

        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 50)

        expected = ColorValues(saturation=255, hue=37, gamma=103)
        self.assertEqual(self.effect.applications, [expected])
        self.assertEqual(self.window._preview_values, expected)
        self.assertIsNone(self.window._confirmed_values)
        self.assertTrue(self.window._preview_timer.isActive())
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_continuous_changes_coalesce_without_restarting_live_timer(self):
        self.window._live_timer.setInterval(150)
        self.window.saturation.slider.setValue(140)
        self.pump(75)
        remaining = self.window._live_timer.remainingTime()
        self.assertGreater(remaining, 0)
        self.window.saturation.slider.setValue(150)
        self.window.contrast.number.setValue(125)
        self.assertLessEqual(self.window._live_timer.remainingTime(), remaining + 5)
        self.pump(110)
        self.assertEqual(self.effect.applications, [ColorValues(saturation=150, contrast=125)])

    def test_apply_flushes_pending_values_and_only_saves_confirmation(self):
        self.window.saturation.set_value(140)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(self.window._settings.allKeys(), [])
        self.window.saturation.number.setValue(255)
        self.window.gamma.number.setValue(1.03)
        self.assertTrue(self.window._live_timer.isActive())

        self.window._apply_clicked()

        expected = ColorValues(saturation=255, gamma=103)
        self.assertEqual(self.effect.applications[-1], expected)
        self.assertEqual(self.window._confirmed_values, expected)
        self.assertEqual(self.window._saved_values, expected)
        self.assertEqual(int(self.window._settings.value("saturation")), 255)
        self.assertEqual(int(self.window._settings.value("gamma")), 103)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        calls = len(self.effect.applications)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(len(self.effect.applications), calls)

    def test_external_matrix_or_gamma_before_apply_never_confirms_or_saves(self):
        for layer in ("matrix", "gamma"):
            with self.subTest(layer=layer):
                self.window._stop_preview(clear_values=True)
                self.window._set_controls(ColorValues())
                fake, gamma = FakeMagnification(), FakeGammaApi()
                backend = ScreenEffect(fake, gamma_api=gamma)
                self.window.effect = backend
                self.window.saturation.set_value(150)
                self.window.gamma.set_value(120)
                self.assertTrue(self.window._render_live_preview())
                outside_matrix = color_matrix(ColorValues(saturation=75))
                outside_gamma = gamma_ramp(gamma.original, 130)
                if layer == "matrix":
                    fake.state = outside_matrix
                else:
                    gamma.ramps = {name: outside_gamma for name in gamma.ramps}
                matrix_writes, gamma_writes = fake.write_calls, len(gamma.writes)

                self.window._apply_clicked()

                self.assertIsNone(self.window._confirmed_values)
                self.assertEqual(self.window._settings.allKeys(), [])
                self.assertIn("Preview unavailable", self.window.status_label.text())
                self.assertEqual(fake.write_calls, matrix_writes)
                self.assertEqual(len(gamma.writes), gamma_writes)
                if layer == "matrix":
                    self.assertTrue(matrices_match(fake.state, outside_matrix))
                else:
                    self.assertTrue(all(ramp == outside_gamma for ramp in gamma.ramps.values()))
                backend.disable()

    def test_external_matrix_or_gamma_before_unchanged_tray_close_is_preserved(self):
        for layer in ("matrix", "gamma"):
            with self.subTest(layer=layer):
                self.window._closing = False
                self.window._stop_preview(clear_values=True)
                self.window._set_controls(ColorValues())
                self.window._tray = FakeTray()
                fake, gamma = FakeMagnification(), FakeGammaApi()
                backend = ScreenEffect(fake, gamma_api=gamma)
                self.window.effect = backend
                self.window.saturation.set_value(150)
                self.window.gamma.set_value(120)
                self.window._apply_clicked()
                confirmed = self.window._confirmed_values
                self.assertEqual(self.window._preview_values, confirmed)
                outside_matrix = color_matrix(ColorValues(saturation=75))
                outside_gamma = gamma_ramp(gamma.original, 130)
                if layer == "matrix":
                    fake.state = outside_matrix
                else:
                    gamma.ramps = {name: outside_gamma for name in gamma.ramps}
                matrix_writes, gamma_writes = fake.write_calls, len(gamma.writes)
                event = QCloseEvent()

                with patch.object(self.app, "quit") as quit_app:
                    self.window.closeEvent(event)

                self.assertTrue(event.isAccepted())
                self.assertFalse(backend.active)
                self.assertIsNone(self.window._confirmed_values)
                self.assertNotIn("running in tray", self.window.status_label.text())
                self.assertEqual(self.window._tray.hidden, 1)
                self.assertEqual(int(self.window._settings.value("saturation")), 150)
                quit_app.assert_called_once()
                if layer == "matrix":
                    self.assertTrue(matrices_match(fake.state, outside_matrix))
                    self.assertEqual(fake.write_calls, matrix_writes)
                else:
                    self.assertTrue(all(ramp == outside_gamma for ramp in gamma.ramps.values()))
                    self.assertEqual(len(gamma.writes), gamma_writes)

    def test_recovery_ignored_matrix_write_keeps_ui_guard_and_record_until_retry(self):
        recovery_path = Path(self.temporary.name) / "color-recovery.json"
        fake, gamma = FakeMagnification(), FakeGammaApi()
        original = color_matrix(ColorValues(saturation=84))
        fake.state = original
        previous = ScreenEffect(fake, recovery_path=recovery_path, gamma_api=gamma)
        previous.apply(ColorValues(saturation=180, gamma=150))
        record = recovery_path.read_bytes()
        recovered = ScreenEffect(fake, recovery_path=recovery_path, gamma_api=gamma)
        self.window.effect = recovered
        self.window._pending_recovery = True

        with patch.object(fake.MagSetFullscreenColorEffect, "callback", return_value=1):
            self.window._offer_recovery()

        self.assertTrue(self.window._pending_recovery)
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertFalse(self.window.disable_button.isEnabled())
        self.assertFalse(self.window.reset_button.isEnabled())
        self.assertEqual(recovery_path.read_bytes(), record)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        self.critical.assert_called_once()
        self.window._offer_recovery()
        self.assertFalse(self.window._pending_recovery)
        self.assertTrue(matrices_match(fake.state, original))
        self.assertFalse(recovery_path.exists())

    def test_timeout_restores_default_without_saving_or_retriggering(self):
        self.window.brightness.number.setValue(-20)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertTrue(self.effect.active)
        self.expire_preview()
        calls = len(self.effect.applications)
        self.assertFalse(self.effect.active)
        self.assertEqual(self.window.values(), ColorValues())
        self.assertEqual(self.window._settings.allKeys(), [])
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(len(self.effect.applications), calls)

    def test_timeout_restores_last_confirmation_without_retriggering(self):
        self.window.saturation.number.setValue(255)
        self.window.gamma.number.setValue(1.03)
        self.window._apply_clicked()
        confirmed = self.window._confirmed_values
        self.window.saturation.slider.setValue(180)
        self.window.gamma.number.setValue(1.50)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.expire_preview()
        self.assertEqual(self.effect.applied, confirmed)
        self.assertEqual(self.window.values(), confirmed)
        self.assertEqual(self.window._preview_values, confirmed)
        self.assertEqual(int(self.window._settings.value("saturation")), 255)
        self.assertEqual(int(self.window._settings.value("gamma")), 103)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        calls = len(self.effect.applications)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(len(self.effect.applications), calls)

    def test_close_cancels_unrendered_unconfirmed_edits_and_does_not_save(self):
        self.window.saturation.number.setValue(255)
        self.window.gamma.number.setValue(1.03)
        self.assertTrue(self.window._live_timer.isActive())
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(self.window._settings.allKeys(), [])
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(self.effect.applications, [])
        self.assertFalse(self.effect.active)

    def test_close_restores_rendered_unconfirmed_preview_without_saving(self):
        self.window.saturation.number.setValue(255)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertTrue(self.effect.active)
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertFalse(self.effect.active)
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_pending_recovery_blocks_preview_and_apply(self):
        self.window._pending_recovery = True
        self.window.saturation.number.setValue(255)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.window._apply_clicked()
        self.assertFalse(self.window._render_live_preview())
        self.assertEqual(self.effect.initializations, 0)
        self.assertEqual(self.effect.applications, [])
        self.assertEqual(self.window._settings.allKeys(), [])
        self.assertIsNone(self.window._confirmed_values)

    def test_pending_recovery_disables_reset_disable_and_quit_cleanup(self):
        self.window._pending_recovery = True
        self.effect.active = True
        self.window.saturation.number.setValue(255)
        unchanged = self.window.values()
        self.window._sync_state("Recovery needs attention")
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertFalse(self.window.disable_button.isEnabled())
        self.assertFalse(self.window.reset_button.isEnabled())

        self.window._disable_clicked()
        self.window._reset_values()
        self.window._cleanup_on_quit()

        self.assertEqual(self.effect.disables, 0)
        self.assertEqual(self.effect.applications, [])
        self.assertTrue(self.effect.active)
        self.assertEqual(self.window.values(), unchanged)
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_pending_recovery_close_cancels_timers_without_touching_effect(self):
        self.window._pending_recovery = True
        self.window._tray = FakeTray()
        self.effect.active = True
        self.window._live_timer.start()
        self.window._preview_remaining = 1
        self.window._preview_timer.start()
        event = QCloseEvent()

        with patch.object(self.app, "quit") as quit_app:
            self.window.closeEvent(event)
            self.window._cleanup_on_quit()

        self.assertTrue(event.isAccepted())
        self.assertTrue(self.effect.active)
        self.assertEqual(self.effect.disables, 0)
        self.assertEqual(self.effect.applications, [])
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(self.window._tray.hidden, 1)
        self.assertEqual(self.window._settings.allKeys(), [])
        quit_app.assert_called_once()

    def test_pending_recovery_close_preserves_real_backend_record_byte_for_byte(self):
        recovery_path = Path(self.temporary.name) / "color-recovery.json"
        fake = FakeMagnification()
        original = color_matrix(ColorValues(saturation=84))
        fake.state = original
        previous_session = ScreenEffect(fake, recovery_path=recovery_path)
        previous_session.apply(ColorValues(saturation=255))
        record = recovery_path.read_bytes()
        writes = fake.write_calls
        current_session = ScreenEffect(fake, recovery_path=recovery_path)
        self.assertTrue(current_session.recovery_needed())
        self.window.effect = current_session
        self.window._pending_recovery = True

        self.window._reset_values()
        self.window._disable_clicked()
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.window._cleanup_on_quit()

        self.assertTrue(event.isAccepted())
        self.assertEqual(recovery_path.read_bytes(), record)
        self.assertEqual(fake.write_calls, writes)
        self.assertTrue(matrices_match(fake.state, color_matrix(ColorValues(saturation=255))))
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_recovery_failure_remains_guarded_against_reset_or_preview(self):
        self.window._pending_recovery = True
        with patch.object(self.effect, "recovery_needed", side_effect=ScreenEffectError("Fake unreadable record")):
            self.window._offer_recovery()
        self.assertTrue(self.window._pending_recovery)
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertFalse(self.window.disable_button.isEnabled())
        self.assertFalse(self.window.reset_button.isEnabled())
        self.critical.assert_called_once()
        self.window.saturation.number.setValue(255)
        self.window._reset_values()
        self.window._disable_clicked()
        self.window._apply_clicked()
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(self.effect.disables, 0)
        self.assertEqual(self.effect.applications, [])
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_preview_error_is_inline_and_failed_apply_never_confirms_or_saves(self):
        self.effect.apply_error = ScreenEffectError("Fake unsupported Gamma")
        self.window.gamma.number.setValue(1.03)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertIn("Preview unavailable", self.window.status_label.text())
        self.assertIn("Fake unsupported Gamma", self.window.status_label.text())
        self.warning.assert_not_called()
        self.critical.assert_not_called()
        self.assertEqual(self.window._settings.allKeys(), [])
        self.assertIsNone(self.window._confirmed_values)

        self.window._apply_clicked()

        self.warning.assert_called_once()
        self.assertEqual(self.window._settings.allKeys(), [])
        self.assertIsNone(self.window._confirmed_values)
        self.assertEqual(self.effect.applications, [])

    def test_failed_close_restore_refuses_close_and_retains_retry_state(self):
        self.window.saturation.number.setValue(255)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.effect.disable_error = ScreenEffectError("Fake restore refused")
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window._closing)
        self.assertTrue(self.effect.active)
        self.assertEqual(self.window._settings.allKeys(), [])
        self.critical.assert_called_once()

    def test_reset_immediately_restores_and_neutralizes_without_a_new_preview(self):
        self.window._tray = FakeTray()
        self.window.saturation.number.setValue(255)
        self.window.gamma.number.setValue(1.03)
        self.window._apply_clicked()
        self.window.hue.slider.setValue(72)
        self.assertTrue(self.window._live_timer.isActive())
        applications = len(self.effect.applications)

        self.window._reset_values()

        self.assertFalse(self.effect.active)
        self.assertEqual(self.window.values(), ColorValues())
        self.assertEqual(self.window._saved_values, ColorValues())
        self.assertIsNone(self.window._confirmed_values)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(self.window._tray.hidden, 1)
        for key, value in vars(ColorValues()).items():
            self.assertEqual(int(self.window._settings.value(key)), value)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(len(self.effect.applications), applications)

    def test_confirmed_close_discards_later_preview_and_retains_only_confirmation(self):
        self.window._tray = FakeTray()
        self.window.saturation.number.setValue(255)
        self.window.gamma.number.setValue(1.03)
        self.window._apply_clicked()
        confirmed = self.window._confirmed_values
        self.assertEqual(self.window._tray.shown, 1)
        self.window.saturation.number.setValue(160)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertNotEqual(self.effect.applied, confirmed)
        self.window.gamma.number.setValue(1.50)
        self.assertTrue(self.window._live_timer.isActive())
        event = QCloseEvent()

        with patch.object(self.app, "quit") as quit_app:
            self.window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window._closing)
        self.assertTrue(self.effect.active)
        self.assertEqual(self.effect.applied, confirmed)
        self.assertEqual(self.window.values(), confirmed)
        self.assertEqual(self.window._preview_values, confirmed)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(int(self.window._settings.value("saturation")), 255)
        self.assertEqual(int(self.window._settings.value("gamma")), 103)
        self.assertEqual(self.window._tray.hidden, 0)
        quit_app.assert_not_called()
        calls = len(self.effect.applications)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(len(self.effect.applications), calls)

    def test_true_exit_with_confirmed_tray_restores_and_quits(self):
        self.window._tray = FakeTray()
        self.window.saturation.number.setValue(255)
        self.window._apply_clicked()
        self.window._exit_requested = True
        event = QCloseEvent()

        with patch.object(self.app, "quit") as quit_app:
            self.window.closeEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertFalse(self.effect.active)
        self.assertIsNone(self.window._confirmed_values)
        self.assertEqual(self.window._tray.hidden, 1)
        self.assertEqual(int(self.window._settings.value("saturation")), 255)
        quit_app.assert_called_once()

    def test_confirmation_restore_error_on_close_falls_back_to_safe_exit(self):
        self.window._tray = FakeTray()
        self.window.saturation.number.setValue(255)
        self.window._apply_clicked()
        self.window.saturation.number.setValue(160)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.effect.apply_error = ScreenEffectError("Fake ownership changed")
        event = QCloseEvent()

        with patch.object(self.app, "quit") as quit_app:
            self.window.closeEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertFalse(self.effect.active)
        self.assertIsNone(self.window._confirmed_values)
        self.assertEqual(int(self.window._settings.value("saturation")), 255)
        self.assertEqual(self.window._tray.hidden, 1)
        quit_app.assert_called_once()

    def test_declining_existing_effect_pauses_automatic_preview_until_explicit_apply(self):
        self.effect.replaces_existing_effect = True
        self.question.return_value = QMessageBox.No
        self.window.saturation.number.setValue(255)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertTrue(self.window._preview_paused)
        self.assertFalse(self.effect.active)
        self.assertEqual(self.effect.applications, [])
        self.question.assert_called_once()
        self.window.saturation.number.setValue(254)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.question.assert_called_once()
        self.assertEqual(self.effect.applications, [])
        self.assertEqual(self.window._settings.allKeys(), [])

        self.question.return_value = QMessageBox.Yes
        self.window._apply_clicked()

        self.assertEqual(self.question.call_count, 2)
        self.assertTrue(self.effect.active)
        self.assertEqual(self.window._confirmed_values.saturation, 254)
        self.assertEqual(int(self.window._settings.value("saturation")), 254)

    def test_timeout_does_not_rollback_while_latest_live_preview_is_queued(self):
        self.window.saturation.number.setValue(255)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.window._preview_remaining = 1
        self.window.saturation.number.setValue(254)
        self.window._preview_remaining = 1
        self.assertTrue(self.window._live_timer.isActive())
        self.window._preview_tick()
        self.assertEqual(self.window._preview_remaining, 1)
        self.assertTrue(self.effect.active)
        self.assertEqual(self.effect.disables, 0)
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(self.effect.applied.saturation, 254)
        self.assertEqual(self.window._preview_remaining, self.ui.PREVIEW_IDLE_SECONDS)


if __name__ == "__main__":
    unittest.main()
