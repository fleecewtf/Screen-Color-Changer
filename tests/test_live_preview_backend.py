"""Live-preview backend regressions using fake APIs, never real displays."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from color_math import ColorValues, color_matrix, gamma_ramp, matrices_match
from screen_backend import ScreenEffect, ScreenEffectError
from test_color_effect import FakeGammaApi, FakeMagnification


class LivePreviewBackendTests(unittest.TestCase):
    def test_read_only_confirmation_verifies_actual_matrix_and_gamma(self):
        for layer in ("matrix", "gamma", "topology", "requested_gamma"):
            with self.subTest(layer=layer):
                fake, gamma = FakeMagnification(), FakeGammaApi()
                values = ColorValues(saturation=140, gamma=120)
                effect = ScreenEffect(fake, gamma_api=gamma)
                effect.apply(values)
                effect.verify_current(values)
                if layer == "matrix":
                    fake.state = color_matrix(ColorValues(saturation=75))
                elif layer == "gamma":
                    name = next(iter(gamma.ramps))
                    gamma.ramps[name] = gamma_ramp(gamma.original, 130)
                elif layer == "topology":
                    next(iter(gamma.devices.values()))["tokens"][0][2] += 1
                else:
                    values = ColorValues(saturation=140, gamma=130)
                matrix_writes, gamma_writes = fake.write_calls, len(gamma.writes)
                actual_matrix, actual_gamma = fake.state, dict(gamma.ramps)
                with self.assertRaises(ScreenEffectError):
                    effect.verify_current(values)
                self.assertEqual(fake.write_calls, matrix_writes)
                self.assertEqual(len(gamma.writes), gamma_writes)
                self.assertEqual(fake.state, actual_matrix)
                self.assertEqual(gamma.ramps, actual_gamma)

    def test_failed_recovery_matrix_readback_retains_record_and_restores_gamma(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            original_matrix = color_matrix(ColorValues(saturation=84))
            fake.state = original_matrix
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(saturation=180, gamma=150))
            record = path.read_bytes()
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            # Replace the callback, not Windows: this fake driver reports
            # success while silently keeping the prior matrix.
            with patch.object(fake.MagSetFullscreenColorEffect, "callback", return_value=1):
                with self.assertRaisesRegex(ScreenEffectError, "record was retained"):
                    recovered.recover_previous(True)
            self.assertEqual(path.read_bytes(), record)
            self.assertTrue(matrices_match(fake.state, color_matrix(ColorValues(saturation=180))))
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            recovered.recover_previous(True)
            self.assertTrue(matrices_match(fake.state, original_matrix))
            self.assertFalse(path.exists())

    def test_partial_recovery_retries_gamma_after_matrix_already_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
            original_matrix = color_matrix(ColorValues(saturation=84))
            fake.state = original_matrix
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(saturation=180, gamma=150))
            record = path.read_bytes()
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            failed = list(gamma.ramps)[1]
            gamma.fail_names = {failed}
            with self.assertRaisesRegex(ScreenEffectError, "record was retained"):
                recovered.recover_previous(True)
            self.assertEqual(path.read_bytes(), record)
            self.assertTrue(matrices_match(fake.state, original_matrix))
            self.assertEqual(gamma.ramps[list(gamma.ramps)[0]], gamma.original)
            self.assertNotEqual(gamma.ramps[failed], gamma.original)
            writes = fake.write_calls
            gamma.fail_names.clear()
            recovered.recover_previous(True)
            self.assertEqual(fake.write_calls, writes)
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            self.assertFalse(path.exists())

    def test_recovery_matrix_read_failure_still_restores_gamma_and_retains_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(saturation=180, gamma=150))
            record = path.read_bytes()
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            fake.fail_reads = True
            with self.assertRaisesRegex(ScreenEffectError, "record was retained"):
                recovered.recover_previous(True)
            self.assertEqual(path.read_bytes(), record)
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            fake.fail_reads = False
            recovered.recover_previous(True)
            self.assertFalse(path.exists())

    def test_declining_after_partial_recovery_relinquishes_without_gamma_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(saturation=180, gamma=150))
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            gamma.fail_names = set(gamma.ramps)
            with self.assertRaises(ScreenEffectError):
                recovered.recover_previous(True)
            gamma.fail_names.clear()
            matrix_writes, gamma_writes = fake.write_calls, len(gamma.writes)
            current_gamma = dict(gamma.ramps)
            recovered.recover_previous(False)
            self.assertEqual(fake.write_calls, matrix_writes)
            self.assertEqual(len(gamma.writes), gamma_writes)
            self.assertEqual(gamma.ramps, current_gamma)
            self.assertFalse(path.exists())
            self.assertFalse(recovered.active)

    def test_many_previews_keep_original_matrix_and_gamma_baselines(self):
        fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
        original_matrix = color_matrix(ColorValues(saturation=81, brightness=-3))
        fake.state = original_matrix
        originals = dict(gamma.ramps)
        effect = ScreenEffect(fake, gamma_api=gamma)

        for step in range(30):
            values = ColorValues(saturation=101 + step, hue=step, gamma=101 + step)
            effect.apply(values)
            self.assertTrue(matrices_match(fake.state, color_matrix(values)))
            for name, original_ramp in originals.items():
                self.assertEqual(gamma.ramps[name], gamma_ramp(original_ramp, values.gamma))

        self.assertEqual(fake.initialize_calls, 1)
        self.assertTrue(effect.disable())
        self.assertTrue(matrices_match(fake.state, original_matrix))
        self.assertEqual(gamma.ramps, originals)
        self.assertFalse(effect.active)

    def test_latest_preview_recovery_still_records_pre_session_originals(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
            original_matrix = color_matrix(ColorValues(saturation=73))
            fake.state = original_matrix
            originals = dict(gamma.ramps)
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)

            for values in (ColorValues(saturation=140, gamma=115),
                           ColorValues(saturation=183, hue=51, gamma=153),
                           ColorValues(saturation=206, hue=79, gamma=169)):
                effect.apply(values)

            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(matrices_match(record["original"], original_matrix))
            self.assertTrue(matrices_match(record["target"], color_matrix(values)))
            for name, original in originals.items():
                self.assertEqual(tuple(record["gamma"]["originals"][name]), original)
                self.assertEqual(tuple(record["gamma"]["target"][name]), gamma.ramps[name])

            # A new process must recover the first baseline, not the prior preview.
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            recovered.recover_previous(restore=True)
            self.assertTrue(matrices_match(fake.state, original_matrix))
            self.assertEqual(gamma.ramps, originals)
            self.assertFalse(path.exists())

    def test_rejected_later_preview_preserves_last_successful_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
            original_matrix = color_matrix(ColorValues(saturation=84))
            fake.state = original_matrix
            originals = dict(gamma.ramps)
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            previous = ColorValues(saturation=160, hue=31, gamma=141)
            effect.apply(previous)
            previous_gamma = dict(gamma.ramps)
            fake.fail_writes = True

            with self.assertRaises(ScreenEffectError):
                effect.apply(ColorValues(saturation=190, hue=66, gamma=163))

            self.assertTrue(matrices_match(fake.state, color_matrix(previous)))
            self.assertEqual(gamma.ramps, previous_gamma)
            self.assertTrue(effect.active)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(matrices_match(record["original"], original_matrix))
            self.assertTrue(matrices_match(record["target"], color_matrix(previous)))
            fake.fail_writes = False
            self.assertTrue(effect.disable())
            self.assertTrue(matrices_match(fake.state, original_matrix))
            self.assertEqual(gamma.ramps, originals)
            self.assertFalse(path.exists())

    def test_external_change_blocks_next_preview_before_any_gamma_write(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(saturation=140, gamma=120))
        outside = color_matrix(ColorValues(saturation=75, brightness=-4))
        fake.state = outside
        writes = len(gamma.writes)

        with self.assertRaisesRegex(ScreenEffectError, "Another app changed"):
            effect.apply(ColorValues(saturation=170, gamma=150))

        self.assertEqual(len(gamma.writes), writes)
        self.assertTrue(matrices_match(fake.state, outside))
        self.assertFalse(effect.disable())
        self.assertTrue(matrices_match(fake.state, outside))
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))

    def test_hotplug_during_preview_rejects_both_layers_and_preserves_new_display(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        previous = ColorValues(saturation=144, gamma=137)
        effect.apply(previous)
        name = next(iter(gamma.devices))
        gamma.devices[name]["tokens"][0][2] += 1
        writes = len(gamma.writes)
        preview_ramp = gamma.ramps[name]

        with self.assertRaisesRegex(ScreenEffectError, "configuration changed"):
            effect.apply(ColorValues(saturation=155, gamma=148))

        self.assertTrue(matrices_match(fake.state, color_matrix(previous)))
        self.assertEqual(len(gamma.writes), writes)
        self.assertFalse(effect.disable())
        self.assertEqual(gamma.ramps[name], preview_ramp)
        self.assertEqual(len(gamma.writes), writes)


if __name__ == "__main__":
    unittest.main()
