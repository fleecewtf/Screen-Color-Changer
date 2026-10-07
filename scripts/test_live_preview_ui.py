"""Source-only offscreen Qt checks; effects and settings are isolated fakes."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PySide6.QtCore import QEventLoop, QRect, QSettings, QTimer, Qt
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox, QStyle, QStyleOptionSlider
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

    def test_every_slider_step_and_exact_number_stays_synchronized(self):
        self.window.testing = True
        for control, _default in self.window._controls.values():
            for value in range(control.slider.minimum(), control.slider.maximum() + 1):
                control.slider.setValue(value)
                self.assertEqual(control.value(), value)
                self.assertAlmostEqual(control.number.value(), value / control.scale)
            for value in range(control.slider.maximum(), control.slider.minimum() - 1, -1):
                control.number.setValue(value / control.scale)
                self.assertEqual(control.value(), value)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertEqual(self.effect.applications, [])

    def test_corrupted_saved_values_leave_safe_defaults_without_preview(self):
        for key, corrupt in (("saturation", "nan"), ("hue", "999"),
                             ("brightness", "17.5"), ("contrast", "-999"),
                             ("gamma", "True")):
            self.window._settings.setValue(key, corrupt)
        self.window._settings.sync()
        self.window._restore_values()
        self.assertEqual(self.window.values(), ColorValues())
        self.assertFalse(self.window._live_timer.isActive())
        self.assertEqual(self.effect.applications, [])

    def test_saved_profile_round_trips_on_restart_without_automatically_applying(self):
        expected = ColorValues(saturation=255, hue=-179, brightness=-19, contrast=199, gamma=103)
        self.window._set_controls(expected)
        self.window._apply_clicked()
        restart_effect = FakeEffect()
        with patch.object(self.ui, "SETTINGS_PATH", self.settings_path), patch.object(self.ui.ColorWindow, "_build_tray"):
            restarted = self.ui.ColorWindow(effect=restart_effect)
            try:
                self.pump(40)
                self.assertEqual(restarted.values(), expected)
                self.assertEqual(restarted._saved_values, expected)
                self.assertIsNone(restarted._confirmed_values)
                self.assertFalse(restarted._live_timer.isActive())
                self.assertFalse(restarted._preview_timer.isActive())
                self.assertEqual(restart_effect.applications, [])
                self.assertEqual(restart_effect.initializations, 0)
            finally:
                restarted.close()
                restarted.deleteLater()

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
                self.window._preview_paused = False
                self.window._safety_restoring = False
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
        remaining = json.loads(recovery_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining["original"], json.loads(record)["original"])
        self.assertIsNone(remaining["gamma"])  # Gamma was restored; retain only the unresolved matrix.
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

    def test_first_failed_preview_with_remaining_effect_starts_safety_restore_timer(self):
        def partially_failed_apply(values):
            self.effect.active = True
            raise ScreenEffectError("Color apply failed and could not be fully restored")

        with patch.object(self.effect, "apply", side_effect=partially_failed_apply):
            self.window.gamma.set_value(150)
            self.assertFalse(self.window._render_live_preview())
        self.assertTrue(self.effect.active)
        self.assertTrue(self.window._preview_timer.isActive())
        self.assertEqual(self.window._preview_remaining, 1)
        self.assertEqual(self.window._settings.allKeys(), [])
        self.window._preview_tick()
        self.assertFalse(self.effect.active)
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(self.window.values(), ColorValues())

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

    def test_typing_refreshes_idle_count_and_restoration_preserves_uncommitted_text(self):
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.window._preview_remaining = 1
        control = self.window.saturation
        control.number.lineEdit().setText("255%")
        control.number.lineEdit().textEdited.emit("255%")
        self.assertEqual(self.window._preview_remaining, self.ui.PREVIEW_IDLE_SECONDS)
        self.window._preview_remaining = 1
        self.window._preview_tick()
        self.assertFalse(self.effect.active)
        self.assertEqual(control.number.lineEdit().text(), "255%")
        control.number.interpretText()
        control.number.editingFinished.emit()
        self.pump(self.ui.LIVE_PREVIEW_INTERVAL_MS + 40)
        self.assertEqual(self.effect.applied.saturation, 255)

    def test_apply_failure_modal_loop_does_not_prevent_safety_restoration(self):
        def failed_apply(values):
            self.effect.active = True
            raise ScreenEffectError("Partially applied fake color effect")

        def warning_with_nested_loop(*_args):
            self.assertFalse(self.window._preview_busy)
            self.window._preview_timer.setInterval(5)
            self.pump(40)
            self.assertFalse(self.effect.active)
            return QMessageBox.Ok

        self.warning.side_effect = warning_with_nested_loop
        with patch.object(self.effect, "apply", side_effect=failed_apply):
            self.window.saturation.set_value(180)
            self.window._apply_clicked()
        self.assertFalse(self.effect.active)
        self.assertIsNone(self.window._confirmed_values)

    def test_applied_value_return_and_footer_match_tray_close_behavior(self):
        self.window._tray = FakeTray()
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.window.saturation.set_value(190)
        self.window._render_live_preview()
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.assertIn("close keeps in tray", self.window.status_label.text())
        notes = [label for label in self.window.findChildren(self.ui.QLabel)
                 if label.text() == "Esc restores colors"]
        self.assertIn("after Apply, closing keeps them in the tray", notes[0].toolTip())

    def test_regular_window_has_no_horizontal_scrollbar(self):
        self.window.show()
        self.window._fit_work_area(QRect(0, 0, 1920, 1080))
        self.app.processEvents()
        self.assertEqual(self.window.width(), self.ui.WINDOW_SIZE)
        self.assertEqual(self.window.scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertFalse(self.window.scroll_area.widget().autoFillBackground())

    def test_viewport_transparency_does_not_clear_primary_button_background(self):
        self.window.show()
        self.window._fit_work_area(QRect(0, 0, 1920, 1080))
        self.app.processEvents()
        image = self.window.apply_button.grab().toImage()
        ratio = image.devicePixelRatio()
        background = image.pixelColor(round(10 * ratio), image.height() // 2)
        self.assertEqual(self.window.apply_button.text(), "Apply colors")
        self.assertGreater(background.red(), 200)
        self.assertGreater(background.green(), 200)
        self.assertGreater(background.blue(), 200)
        self.assertEqual(background.alpha(), 255)

    def test_small_work_area_is_scrollable_and_preserves_precise_slider_travel(self):
        self.window.show()
        self.window._fit_work_area(QRect(0, 0, 400, 400))
        self.app.processEvents()
        self.assertLessEqual(self.window.width(), 400)
        self.assertLessEqual(self.window.height(), 400)
        self.assertGreater(self.window.scroll_area.horizontalScrollBar().maximum(), 0)
        self.assertGreater(self.window.scroll_area.verticalScrollBar().maximum(), 0)
        for control, _default in self.window._controls.values():
            slider = control.slider
            option = QStyleOptionSlider()
            option.initFrom(slider)
            option.orientation = Qt.Horizontal
            option.minimum, option.maximum = slider.minimum(), slider.maximum()
            option.sliderPosition = option.sliderValue = slider.value()
            groove = slider.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderGroove, slider)
            handle = slider.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, slider)
            travel = groove.width() - handle.width()
            reachable = {QStyle.sliderValueFromPosition(slider.minimum(), slider.maximum(), x, travel)
                         for x in range(travel + 1)}
            self.assertTrue(set(range(slider.minimum(), slider.maximum() + 1)) <= reachable)


class SlowFakeEffect(FakeEffect):
    """Deterministic slow driver, with proof of serialized off-UI-thread calls."""
    def __init__(self, delay=0.05):
        super().__init__()
        self.delay = delay
        self.thread_ids = []
        self.calls = []
        self.inflight = 0
        self.max_inflight = 0

    def _enter(self, kind):
        self.thread_ids.append(threading.get_ident())
        self.calls.append(kind)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        time.sleep(self.delay)

    def initialize(self):
        self._enter("initialize")
        try:
            super().initialize()
        finally:
            self.inflight -= 1

    def apply(self, values):
        self._enter("apply")
        try:
            super().apply(values)
        finally:
            self.inflight -= 1

    def verify_current(self, values):
        self._enter("verify")
        try:
            super().verify_current(values)
        finally:
            self.inflight -= 1

    def disable(self):
        self._enter("disable")
        try:
            return super().disable()
        finally:
            self.inflight -= 1

    def recovery_needed(self):
        self._enter("recovery_check")
        try:
            return super().recovery_needed()
        finally:
            self.inflight -= 1

    def recover_previous(self, restore):
        self._enter("recover")
        try:
            return super().recover_previous(restore)
        finally:
            self.inflight -= 1


@unittest.skipUnless(QT_AVAILABLE, "Requires the folder-private PySide6 runtime")
class AsyncLivePreviewUiTests(unittest.TestCase):
    setUpClass = classmethod(LivePreviewUiTests.setUpClass.__func__)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.effect = SlowFakeEffect()
        self.window = self.ui.ColorWindow(testing=True, effect=self.effect, async_operations=True)
        self.window._settings = QSettings(str(Path(self.temporary.name) / "settings.ini"), QSettings.IniFormat)
        self.window.testing = False
        self.patches = [patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                        patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok),
                        patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok)]
        self.question, self.warning, self.critical = [item.start() for item in self.patches]

    def tearDown(self):
        self.effect.apply_error = self.effect.disable_error = None
        self.window._shutdown_worker(wait=True)
        self.app.processEvents()
        self.window._tray = None
        self.window._close_ready = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.window._settings = None
        self.temporary.cleanup()
        for item in reversed(self.patches):
            item.stop()

    pump = LivePreviewUiTests.pump

    def wait_until(self, predicate, timeout=3000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            self.pump(5)
        self.fail("Timed out waiting for the isolated display worker")

    def wait_idle(self):
        self.wait_until(lambda: self.window._active_request is None and
                        self.window._queued_request is None and not self.window._live_timer.isActive())

    def test_slow_calls_remain_responsive_serialized_and_off_ui_thread(self):
        self.effect.delay = 0.12
        heartbeats = []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
        timer.start()
        self.window.saturation.set_value(255)
        self.window._apply_clicked()
        self.wait_idle()
        timer.stop()
        self.assertGreater(len(heartbeats), 30)
        gaps = [right - left for left, right in zip(heartbeats, heartbeats[1:])]
        self.assertLess(max(gaps), 0.1)
        self.assertEqual(self.effect.max_inflight, 1)
        self.assertEqual(len(set(self.effect.thread_ids)), 1)
        self.assertNotIn(threading.get_ident(), self.effect.thread_ids)
        self.assertEqual(self.window._confirmed_values.saturation, 255)

    def test_continuous_edits_coalesce_to_latest_values_with_bounded_queue(self):
        self.effect.delay = 0.015
        self.window.saturation.set_value(150)
        self.window._render_live_preview()
        for index in range(200):
            self.window.saturation.set_value(100 + index % 200)
            self.window._render_live_preview()
            self.assertIsNotNone(self.window._active_request)
            self.assertIn(type(self.window._queued_request), (dict, type(None)))
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        self.wait_idle()
        self.assertEqual(self.effect.applied.saturation, 255)
        self.assertLessEqual(len(self.effect.applications), 2)
        self.assertEqual(self.effect.max_inflight, 1)

    def test_apply_saves_only_clicked_verified_values_when_edits_arrive_during_apply(self):
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.window.saturation.set_value(255)
        self.wait_idle()
        self.assertEqual(self.window._confirmed_values.saturation, 180)
        self.assertEqual(int(self.window._settings.value("saturation")), 180)
        self.assertEqual(self.effect.applied.saturation, 255)
        self.assertEqual(self.window._preview_values.saturation, 255)
        self.assertIsNotNone(self.window._preview_remaining)

    def test_reset_supersedes_pending_preview_and_ignores_inflight_result(self):
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        self.window._reset_values()
        self.wait_idle()
        self.assertFalse(self.effect.active)
        self.assertEqual(self.window.values(), ColorValues())
        self.assertIsNone(self.window._preview_values)
        self.assertIsNone(self.window._confirmed_values)
        self.assertFalse(self.window._preview_timer.isActive())
        self.assertEqual(int(self.window._settings.value("saturation")), 100)
        self.assertEqual(len(self.effect.applications), 1)

    def test_disable_supersedes_inflight_confirm_without_saving(self):
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.window._disable_clicked()
        self.wait_idle()
        self.assertFalse(self.effect.active)
        self.assertIsNone(self.window._confirmed_values)
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_close_waits_for_inflight_write_then_restores_without_late_preview(self):
        self.window.show()
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window._closing)
        self.wait_until(lambda: self.window._close_ready)
        self.assertFalse(self.effect.active)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(len(self.effect.applications), 1)
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_close_to_tray_restores_only_previous_confirmation_then_exit_restores_baseline(self):
        self.window.show()
        self.window._tray = FakeTray()
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.wait_idle()
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        self.window.close()
        self.wait_until(lambda: not self.window._closing and not self.window.isVisible())
        self.assertTrue(self.effect.active)
        self.assertEqual(self.effect.applied.saturation, 180)
        self.assertEqual(self.window.values().saturation, 180)
        with patch.object(self.app, "quit") as quit_app:
            self.window._exit_clicked()
            self.wait_until(lambda: self.window._close_ready)
        self.assertFalse(self.effect.active)
        quit_app.assert_called_once()

    def test_failed_close_stays_open_and_recovery_retry_runs_on_worker(self):
        self.window.show()
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.wait_idle()
        self.effect.disable_error = ScreenEffectError("Fake partial restore failure")
        self.window.close()
        self.wait_idle()
        self.assertFalse(self.window._closing)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.effect.active)
        self.critical.assert_called_once()
        self.effect.disable_error = None
        self.window._disable_clicked()
        self.wait_idle()
        self.assertFalse(self.effect.active)

    def test_failed_close_arms_watchdog_before_critical_dialog_nested_loop(self):
        self.window.show()
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.wait_idle()
        self.effect.disable_error = ScreenEffectError("Fake first restoration failed")

        def nested_critical(*_args):
            self.assertFalse(self.window._preview_busy)
            self.assertTrue(self.window._preview_timer.isActive())
            self.assertIsNone(self.window._confirmed_values)
            self.effect.disable_error = None
            self.window._preview_timer.setInterval(5)
            self.wait_until(lambda: not self.effect.active)
            return QMessageBox.Ok

        self.critical.side_effect = nested_critical
        self.window.close()
        self.wait_idle()
        self.assertFalse(self.effect.active)
        self.critical.assert_called_once()

    def test_idle_restoration_retains_typed_uncommitted_value(self):
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.wait_idle()
        control = self.window.saturation
        control.number.lineEdit().setText("255%")
        control.number.lineEdit().textEdited.emit("255%")
        self.window._preview_remaining = 1
        self.window._preview_tick()
        self.wait_idle()
        self.assertFalse(self.effect.active)
        self.assertEqual(control.number.lineEdit().text(), "255%")

    def test_timeout_restores_confirmed_values_off_thread_and_does_not_resave_edits(self):
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.wait_idle()
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        self.wait_idle()
        self.window._preview_remaining = 1
        self.window._preview_tick()
        self.wait_idle()
        self.assertEqual(self.effect.applied.saturation, 180)
        self.assertEqual(self.window.values().saturation, 180)
        self.assertEqual(int(self.window._settings.value("saturation")), 180)

    def test_error_warning_nested_loop_does_not_block_async_safety_restore(self):
        original_apply = self.effect.apply
        def failed_apply(values):
            original_apply(values)
            raise ScreenEffectError("Fake write left a residual effect")
        self.effect.apply = failed_apply

        def nested_warning(*_args):
            self.assertFalse(self.window._preview_busy)
            self.assertTrue(self.window._safety_restoring)
            self.window._apply_clicked()  # An explicit retry cannot jump ahead of safety restoration.
            self.window._preview_timer.setInterval(5)
            self.wait_until(lambda: not self.effect.active)
            return QMessageBox.Ok
        self.warning.side_effect = nested_warning
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.wait_idle()
        self.assertFalse(self.effect.active)
        self.warning.assert_called_once()
        self.assertEqual(self.window._settings.allKeys(), [])

    def test_recovery_read_and_restore_run_on_same_background_thread(self):
        original_check = self.effect.recovery_needed
        def needs_recovery():
            original_check()
            return True
        self.effect.recovery_needed = needs_recovery
        self.window._pending_recovery = True
        self.window._offer_recovery()
        self.wait_idle()
        self.assertFalse(self.window._pending_recovery)
        self.assertEqual(self.effect.recoveries, [True])
        self.assertEqual(self.effect.calls, ["recovery_check", "recover"])
        self.assertNotIn(threading.get_ident(), self.effect.thread_ids)

    def test_shutdown_queues_final_restore_after_inflight_write(self):
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        self.window._cleanup_on_quit()
        self.window._shutdown_worker(wait=True)
        self.assertFalse(self.effect.active)
        self.assertEqual(len(self.effect.applications), 1)
        self.assertEqual(self.effect.calls[-1], "disable")
        self.assertNotIn(threading.get_ident(), self.effect.thread_ids)

    def test_queued_return_to_old_profile_is_actually_rendered_not_stale_cache_verified(self):
        self.window.saturation.set_value(180)
        self.window._apply_clicked()
        self.wait_idle()
        self.window.saturation.set_value(255)
        self.window._render_live_preview()
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.wait_idle()
        self.assertEqual(self.effect.applied.saturation, 180)
        self.assertNotIn("unavailable", self.window.status_label.text())

    def test_continuous_edits_cannot_starve_restoration_after_partial_preview_failure(self):
        self.effect.delay = 0.06
        original_apply = self.effect.apply
        def failed_apply(values):
            original_apply(values)
            raise ScreenEffectError("Fake driver left partial live preview")
        self.effect.apply = failed_apply
        self.window._preview_timer.setInterval(5)
        edits = []
        editor = QTimer()
        editor.setInterval(1)
        def keep_editing():
            value = 101 + len(edits) % 150
            edits.append(value)
            self.window.saturation.set_value(value)
        editor.timeout.connect(keep_editing)
        editor.start()
        self.window.saturation.set_value(180)
        self.window._render_live_preview()
        self.wait_until(lambda: self.effect.disables > 0 and not self.effect.active)
        self.assertTrue(editor.isActive())  # Restoration did not require input to stop.
        self.assertTrue(self.window._preview_paused)
        self.assertEqual(len(self.effect.applications), 1)
        self.assertGreater(len(edits), 50)
        self.pump(120)
        editor.stop()
        self.wait_idle()
        self.assertFalse(self.effect.active)
        self.assertEqual(len(self.effect.applications), 1)
        self.assertFalse(self.window._live_timer.isActive())
        self.assertFalse(self.window._safety_restoring)


if __name__ == "__main__":
    unittest.main()
