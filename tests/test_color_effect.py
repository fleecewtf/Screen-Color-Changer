"""Pure-math and fake-DLL tests; never touch the real display."""

import ctypes
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from color_math import ColorValues, color_matrix, gamma_ramp, identity_matrix, matrices_match
from screen_backend import Effect, ScreenEffect, ScreenEffectError


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeMagnification:
    def __init__(self):
        self.state = identity_matrix()
        self.fail_writes = False
        self.fail_reads = False
        self.initialize_calls = 0
        self.uninitialize_calls = 0
        self.write_calls = 0
        self.MagInitialize = FakeFunction(self.initialize)
        self.MagUninitialize = FakeFunction(self.uninitialize)
        self.MagGetFullscreenColorEffect = FakeFunction(self.get)
        self.MagSetFullscreenColorEffect = FakeFunction(self.set)

    def initialize(self):
        self.initialize_calls += 1
        return 1

    def uninitialize(self):
        self.uninitialize_calls += 1
        return 1

    def get(self, pointer):
        if self.fail_reads:
            return 0
        effect = ctypes.cast(pointer, ctypes.POINTER(Effect)).contents
        for index, value in enumerate(self.state):
            effect[index] = value
        return 1

    def set(self, pointer):
        self.write_calls += 1
        if self.fail_writes:
            return 0
        self.state = tuple(ctypes.cast(pointer, ctypes.POINTER(Effect)).contents)
        return 1


class FakeGammaApi:
    def __init__(self, count=1):
        self.original = tuple(index * 257 for channel in range(3) for index in range(256))
        self.ramps = {f"\\\\.\\DISPLAY{index + 1}": self.original for index in range(count)}
        self.devices = {name: {"sdr": True, "tokens": [[0, 0, index, 0, 0, index]]}
                        for index, name in enumerate(self.ramps)}
        self.writes = []
        self.ignored = set()
        self.fail_names = set()
        self.snapshot_error = False
        self.partial_names = set()
        self.after_write = None

    def snapshot(self):
        if self.snapshot_error:
            raise ScreenEffectError("Cannot verify SDR display mode")
        return copy.deepcopy(self.devices)

    def read(self, name):
        return self.ramps[name]

    def write(self, name, ramp):
        self.writes.append((name, tuple(ramp)))
        if name in self.fail_names:
            raise ScreenEffectError("Fake Gamma write failure")
        if name not in self.ignored:
            if name in self.partial_names and tuple(ramp) != self.original:
                self.ramps[name] = tuple(value // 256 * 256 for value in ramp)
            else:
                self.ramps[name] = tuple(ramp)
        if self.after_write is not None:
            self.after_write()


class ColorMathTests(unittest.TestCase):
    def test_neutral_values_produce_identity(self):
        self.assertTrue(matrices_match(color_matrix(ColorValues()), identity_matrix()))

    def test_exact_255_percent_saturation_is_not_rounded(self):
        matrix = color_matrix(ColorValues(saturation=255))
        self.assertAlmostEqual(matrix[0], 2.55 + (1 - 2.55) * 0.2126)
        self.assertNotEqual(matrix, color_matrix(ColorValues(saturation=254)))
        self.assertNotEqual(matrix, color_matrix(ColorValues(saturation=256)))

    def test_zero_saturation_uses_luminance_for_all_outputs(self):
        matrix = color_matrix(ColorValues(saturation=0))
        for source, weight in enumerate((0.2126, 0.7152, 0.0722)):
            self.assertEqual(matrix[source * 5:source * 5 + 3], (weight,) * 3)

    def test_contrast_and_brightness_set_uniform_bias(self):
        matrix = color_matrix(ColorValues(contrast=120, brightness=-10))
        for output in range(3):
            self.assertAlmostEqual(matrix[20 + output], -0.2)
        self.assertEqual(matrix[18], 1.0)  # Alpha remains unchanged.

    def test_allowed_extremes_keep_black_and_white_distinguishable(self):
        for saturation in (0, 300):
            for contrast in (50, 200):
                for brightness in (-20, 20):
                    matrix = color_matrix(ColorValues(saturation, contrast, brightness))
                    black = max(0.0, min(1.0, matrix[20]))
                    white = max(0.0, min(1.0, sum(matrix[row * 5] for row in range(3)) + matrix[20]))
                    with self.subTest(saturation=saturation, contrast=contrast, brightness=brightness):
                        self.assertGreaterEqual(white - black, 0.49)

    def test_only_whole_numbers_inside_control_limits_are_accepted(self):
        for kwargs in ({"saturation": 301}, {"contrast": 49},
                       {"brightness": 21}, {"brightness": -21},
                       {"saturation": 255.5}, {"hue": 181},
                       {"gamma": 49}, {"gamma": 201}, {"gamma": 103.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ColorValues(**kwargs)

    def test_hue_keeps_gray_neutral_and_changes_red(self):
        matrix = color_matrix(ColorValues(hue=90))
        for output in range(3):
            self.assertAlmostEqual(sum(matrix[source * 5 + output] for source in range(3)), 1.0)
        self.assertFalse(matrices_match(matrix, identity_matrix()))
        # Gamma cannot be represented by the affine matrix.
        self.assertTrue(matrices_match(color_matrix(ColorValues(gamma=150)), identity_matrix()))

    def test_gamma_is_nonlinear_preserves_calibration_and_exact_hundredths(self):
        original = tuple(round(index / 255 * endpoint) for endpoint in (65535, 64000, 62000)
                         for index in range(256))
        curve = gamma_ramp(original, 150)
        self.assertEqual(gamma_ramp(original, 100), original)
        for channel in range(3):
            ramp = curve[channel * 256:(channel + 1) * 256]
            self.assertEqual(ramp[0], original[channel * 256])
            self.assertEqual(ramp[-1], original[channel * 256 + 255])
            self.assertTrue(all(a <= b for a, b in zip(ramp, ramp[1:])))
            self.assertGreater(ramp[128], original[channel * 256 + 128])
        self.assertNotEqual(gamma_ramp(original, 103), gamma_ramp(original, 104))


class ScreenEffectTests(unittest.TestCase):
    def test_neutral_gamma_does_not_load_or_touch_gdi(self):
        fake = FakeMagnification()
        with patch("screen_backend.WindowsGammaApi", side_effect=AssertionError("GDI loaded")):
            effect = ScreenEffect(fake)
            effect.apply(ColorValues(saturation=255, hue=37))
            effect.disable()

    def test_gamma_and_matrix_restore_together(self):
        fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(saturation=255, hue=37, gamma=150))
        for ramp in gamma.ramps.values():
            self.assertEqual(ramp, gamma_ramp(gamma.original, 150))
        effect.apply(ColorValues(gamma=160))
        for ramp in gamma.ramps.values():
            self.assertEqual(ramp, gamma_ramp(gamma.original, 160))
        self.assertTrue(effect.disable())
        self.assertTrue(matrices_match(fake.state, identity_matrix()))
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))

    def test_hdr_or_unverified_mode_rejects_all_changes_before_writing(self):
        for hdr in (True, False):
            with self.subTest(hdr=hdr):
                fake, gamma = FakeMagnification(), FakeGammaApi()
                if hdr:
                    for device in gamma.devices.values():
                        device["sdr"] = False
                else:
                    gamma.snapshot_error = True
                effect = ScreenEffect(fake, gamma_api=gamma)
                with self.assertRaises(ScreenEffectError):
                    effect.apply(ColorValues(saturation=255, gamma=150))
                self.assertEqual(fake.write_calls, 0)
                self.assertEqual(gamma.writes, [])
                self.assertFalse(effect.active)

    def test_silently_ignored_gamma_is_reported_without_partial_color_change(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        gamma.ignored = set(gamma.ramps)
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaisesRegex(ScreenEffectError, "did not apply"):
            effect.apply(ColorValues(saturation=255, gamma=150))
        self.assertEqual(fake.write_calls, 0)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        self.assertFalse(effect.active)

    def test_partial_gamma_readback_is_rejected_and_restored(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        gamma.partial_names = set(gamma.ramps)
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaisesRegex(ScreenEffectError, "did not apply"):
            effect.apply(ColorValues(saturation=255, gamma=150))
        self.assertEqual(fake.write_calls, 0)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        self.assertFalse(effect.active)

    def test_failed_partial_gamma_rollback_preserves_exact_crash_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            gamma.partial_names = set(gamma.ramps)
            write = gamma.write
            block_restore = [True]

            def refusing_restore(name, ramp):
                if tuple(ramp) == gamma.original and block_restore[0]:
                    raise ScreenEffectError("Fake failed restore")
                write(name, ramp)

            gamma.write = refusing_restore
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            with self.assertRaisesRegex(ScreenEffectError, "fully restored"):
                first.apply(ColorValues(saturation=255, gamma=150))
            self.assertTrue(first.active)
            record = json.loads(path.read_text(encoding="utf-8"))
            for name, observed in gamma.ramps.items():
                self.assertEqual(tuple(record["gamma"]["target"][name]), observed)
            block_restore[0] = False
            second = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(second.recovery_needed())
            second.recover_previous(True)
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            self.assertFalse(path.exists())

    def test_external_matrix_change_during_gamma_is_never_overwritten(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        outside = color_matrix(ColorValues(saturation=80))
        gamma.after_write = lambda: setattr(fake, "state", outside)
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaisesRegex(ScreenEffectError, "Another app"):
            effect.apply(ColorValues(saturation=255, gamma=150))
        self.assertEqual(fake.write_calls, 0)
        self.assertTrue(matrices_match(fake.state, outside))
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))

    def test_matrix_read_failure_on_revert_still_restores_gamma(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(saturation=255, gamma=150))
        fake.fail_reads = True
        with self.assertRaises(ScreenEffectError):
            effect.disable()
        self.assertTrue(effect.active)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        fake.fail_reads = False
        self.assertTrue(effect.disable())

    def test_invalid_recovery_top_level_is_reported_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            path.write_text("[]", encoding="utf-8")
            effect = ScreenEffect(FakeMagnification(), recovery_path=path)
            with self.assertRaises(ScreenEffectError):
                effect.recovery_needed()

    def test_failure_on_second_display_rolls_back_the_first(self):
        fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
        gamma.fail_names = {list(gamma.ramps)[1]}
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaises(ScreenEffectError):
            effect.apply(ColorValues(saturation=255, gamma=150))
        self.assertEqual(fake.write_calls, 0)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        self.assertFalse(effect.active)

    def test_matrix_failure_rolls_back_gamma(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        fake.fail_writes = True
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaises(ScreenEffectError):
            effect.apply(ColorValues(saturation=255, gamma=150))
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        self.assertFalse(effect.active)

    def test_external_gamma_is_preserved_on_update_and_revert(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(saturation=255, gamma=150))
        outside = gamma_ramp(gamma.original, 130)
        gamma.ramps = {name: outside for name in gamma.ramps}
        previous_writes = fake.write_calls
        with self.assertRaisesRegex(ScreenEffectError, "Another app"):
            effect.apply(ColorValues(gamma=160))
        self.assertEqual(fake.write_calls, previous_writes)
        self.assertFalse(effect.disable())
        self.assertTrue(all(ramp == outside for ramp in gamma.ramps.values()))
        self.assertTrue(matrices_match(fake.state, identity_matrix()))

    def test_failed_gamma_restore_can_retry_after_matrix_is_restored(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(gamma=150))
        gamma.fail_names = set(gamma.ramps)
        with self.assertRaises(ScreenEffectError):
            effect.disable()
        self.assertTrue(effect.active)
        self.assertTrue(matrices_match(fake.state, identity_matrix()))
        gamma.fail_names.clear()
        self.assertTrue(effect.disable())
        self.assertFalse(effect.active)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))

    def test_matrix_restore_failure_still_restores_gamma_and_keeps_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            effect.apply(ColorValues(saturation=255, gamma=150))
            fake.fail_writes = True
            with self.assertRaises(ScreenEffectError):
                effect.disable()
            self.assertTrue(effect.active)
            self.assertTrue(path.exists())
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            fake.fail_writes = False
            self.assertTrue(effect.disable())
            self.assertFalse(path.exists())

    def test_changed_gamma_topology_or_hdr_is_never_written_during_restore(self):
        for hdr in (True, False):
            with self.subTest(hdr=hdr):
                fake, gamma = FakeMagnification(), FakeGammaApi()
                effect = ScreenEffect(fake, gamma_api=gamma)
                effect.apply(ColorValues(gamma=150))
                writes = len(gamma.writes)
                device = next(iter(gamma.devices.values()))
                if hdr:
                    device["sdr"] = False
                else:
                    device["tokens"][0][2] = 9
                self.assertFalse(effect.disable())
                self.assertEqual(len(gamma.writes), writes)

    def test_legacy_schema_one_without_previous_still_recovers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake = FakeMagnification()
            target = color_matrix(ColorValues(saturation=255))
            path.write_text(json.dumps({"schema": 1, "original": identity_matrix(),
                                        "target": target}), encoding="utf-8")
            fake.state = target
            effect = ScreenEffect(fake, recovery_path=path)
            self.assertTrue(effect.recovery_needed())
            effect.recover_previous(True)
            self.assertTrue(matrices_match(fake.state, identity_matrix()))

    def test_returning_to_gamma_one_restores_then_stops_gdi_writes(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(gamma=150))
        effect.apply(ColorValues(gamma=100))
        writes = len(gamma.writes)
        effect.apply(ColorValues(saturation=255, hue=18))
        effect.disable()
        self.assertEqual(len(gamma.writes), writes)
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))

    def test_gamma_recovery_after_unclean_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(saturation=255, gamma=150))
            second = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(second.recovery_needed())
            second.recover_previous(True)
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            self.assertTrue(matrices_match(fake.state, identity_matrix()))
            self.assertFalse(path.exists())

    def test_gamma_recovery_does_not_overwrite_newer_external_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(saturation=255, gamma=150))
            outside = gamma_ramp(gamma.original, 130)
            gamma.ramps = {name: outside for name in gamma.ramps}
            second = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(second.recovery_needed())  # Matrix is still ours.
            second.recover_previous(True)
            self.assertTrue(all(ramp == outside for ramp in gamma.ramps.values()))
            self.assertTrue(matrices_match(fake.state, identity_matrix()))

    def test_next_launch_can_restore_previous_colors_after_unclean_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake = FakeMagnification()
            previous = color_matrix(ColorValues(saturation=80))
            fake.state = previous
            first = ScreenEffect(fake, recovery_path=path)
            first.apply(ColorValues(saturation=255))
            self.assertTrue(path.is_file())
            # Simulate a terminated process: there was no graceful Disable.
            second = ScreenEffect(fake, recovery_path=path)
            self.assertTrue(second.recovery_needed())
            second.recover_previous(restore=True)
            self.assertTrue(matrices_match(fake.state, previous))
            self.assertFalse(path.exists())

    def test_stale_recovery_never_overwrites_another_apps_newer_effect(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake = FakeMagnification()
            first = ScreenEffect(fake, recovery_path=path)
            first.apply(ColorValues(saturation=255))
            outside = color_matrix(ColorValues(saturation=70))
            fake.state = outside
            second = ScreenEffect(fake, recovery_path=path)
            self.assertFalse(second.recovery_needed())
            self.assertTrue(matrices_match(fake.state, outside))
            self.assertFalse(path.exists())

    def test_apply_is_explicit_and_disable_restores_previous_effect(self):
        fake = FakeMagnification()
        previous = color_matrix(ColorValues(saturation=80))
        fake.state = previous
        effect = ScreenEffect(fake)
        self.assertEqual(fake.initialize_calls, 0)
        effect.initialize()
        self.assertTrue(effect.replaces_existing_effect)
        effect.apply(ColorValues(saturation=255))
        self.assertTrue(effect.active)
        self.assertTrue(matrices_match(fake.state, color_matrix(ColorValues(saturation=255))))
        self.assertTrue(effect.disable())
        self.assertTrue(matrices_match(fake.state, previous))
        self.assertEqual(fake.uninitialize_calls, 1)

    def test_external_change_is_never_overwritten_on_disable(self):
        fake = FakeMagnification()
        effect = ScreenEffect(fake)
        effect.apply(ColorValues(saturation=255))
        outside = color_matrix(ColorValues(saturation=70))
        fake.state = outside
        self.assertFalse(effect.disable())
        self.assertTrue(matrices_match(fake.state, outside))

    def test_external_change_is_never_overwritten_on_live_update(self):
        fake = FakeMagnification()
        effect = ScreenEffect(fake)
        effect.apply(ColorValues(saturation=255))
        outside = color_matrix(ColorValues(saturation=70))
        fake.state = outside
        with self.assertRaisesRegex(ScreenEffectError, "Another app changed"):
            effect.apply(ColorValues(saturation=256))
        self.assertTrue(matrices_match(fake.state, outside))

    def test_failed_restore_keeps_original_for_retry(self):
        fake = FakeMagnification()
        previous = color_matrix(ColorValues(saturation=80))
        fake.state = previous
        effect = ScreenEffect(fake)
        effect.apply(ColorValues(saturation=255))
        fake.fail_writes = True
        with self.assertRaises(ScreenEffectError):
            effect.disable()
        self.assertTrue(effect.active)
        self.assertEqual(fake.uninitialize_calls, 0)
        fake.fail_writes = False
        self.assertTrue(effect.disable())
        self.assertTrue(matrices_match(fake.state, previous))


if __name__ == "__main__":
    unittest.main()
