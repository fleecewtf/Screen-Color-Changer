"""Pure-math and fake-DLL tests; never touch the real display."""

import ctypes
import tempfile
import unittest
from pathlib import Path

from color_math import ColorValues, color_matrix, identity_matrix, matrices_match
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
        self.initialize_calls = 0
        self.uninitialize_calls = 0
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
        effect = ctypes.cast(pointer, ctypes.POINTER(Effect)).contents
        for index, value in enumerate(self.state):
            effect[index] = value
        return 1

    def set(self, pointer):
        if self.fail_writes:
            return 0
        self.state = tuple(ctypes.cast(pointer, ctypes.POINTER(Effect)).contents)
        return 1


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

    def test_only_whole_numbers_inside_control_limits_are_accepted(self):
        for kwargs in ({"saturation": 301}, {"contrast": -1},
                       {"brightness": 51}, {"saturation": 255.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ColorValues(**kwargs)


class ScreenEffectTests(unittest.TestCase):
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
