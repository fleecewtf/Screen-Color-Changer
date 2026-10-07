"""Source-only live-preview regressions using fake APIs, never real displays."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from color_math import ColorValues, color_matrix, gamma_ramp, matrices_match
from screen_backend import Effect, ScreenEffect, ScreenEffectError
from test_color_effect import FakeGammaApi, FakeMagnification


class LivePreviewBackendTests(unittest.TestCase):
    def test_deeply_nested_or_oversized_recovery_is_guarded_without_initializing_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake = FakeMagnification()
            effect = ScreenEffect(fake, recovery_path=path)
            path.write_text("[" * 10000 + "0" + "]" * 10000, encoding="utf-8")
            with self.assertRaises(ScreenEffectError):
                effect.recovery_needed()
            path.write_text(json.dumps({"schema": 2, "original": color_matrix(ColorValues()),
                                        "target": color_matrix(ColorValues())}), encoding="utf-8")
            with patch("screen_backend.MAX_RECOVERY_BYTES", 200):
                with self.assertRaises(ScreenEffectError):
                    effect.recovery_needed()
            self.assertEqual(fake.initialize_calls, 0)
            self.assertTrue(path.exists())

    def test_malformed_recovery_numbers_are_reported_without_display_writes(self):
        for value in (float("nan"), float("inf"), 1e100, 10 ** 1000):
            with self.subTest(value_type=type(value).__name__), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "color-recovery.json"
                original = list(color_matrix(ColorValues()))
                original[0] = value
                path.write_text(json.dumps({"schema": 2, "original": original,
                                            "target": color_matrix(ColorValues())}), encoding="utf-8")
                fake = FakeMagnification()
                effect = ScreenEffect(fake, recovery_path=path)
                with self.assertRaises(ScreenEffectError):
                    effect.recover_previous(True)
                self.assertEqual(fake.write_calls, 0)
                self.assertTrue(path.exists())

    def test_boolean_recovery_schema_is_not_mistaken_for_legacy_schema_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            path.write_text(json.dumps({"schema": True, "original": color_matrix(ColorValues()),
                                        "target": color_matrix(ColorValues())}), encoding="utf-8")
            fake = FakeMagnification()
            effect = ScreenEffect(fake, recovery_path=path)
            with self.assertRaises(ScreenEffectError):
                effect.recovery_needed()
            self.assertEqual(fake.initialize_calls, 0)

    def test_partial_matrix_write_is_restored_even_when_api_reports_failure(self):
        for result in (0, 1):
            with self.subTest(driver_result=result):
                fake = FakeMagnification()
                original_set = fake.set
                broken = [True]

                def partial_set(pointer):
                    value = original_set(pointer)
                    if broken[0]:
                        broken[0] = False
                        partial = list(fake.state)
                        partial[0] += 0.125
                        fake.state = tuple(Effect(*partial))
                        return result
                    return value

                fake.MagSetFullscreenColorEffect.callback = partial_set
                effect = ScreenEffect(fake)
                with self.assertRaises(ScreenEffectError):
                    effect.apply(ColorValues(saturation=180))
                self.assertEqual(fake.state, tuple(Effect(*color_matrix(ColorValues()))))
                self.assertFalse(effect.active)

    def test_partial_matrix_failed_rollback_keeps_exact_recovery_for_next_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake = FakeMagnification()
            original_set = fake.set
            first_write = [True]
            block_restore = [True]

            def partial_set(pointer):
                if first_write[0]:
                    first_write[0] = False
                    original_set(pointer)
                    partial = list(fake.state)
                    partial[0] += 0.125
                    fake.state = tuple(Effect(*partial))
                    return 0
                if block_restore[0]:
                    return 0
                return original_set(pointer)

            fake.MagSetFullscreenColorEffect.callback = partial_set
            effect = ScreenEffect(fake, recovery_path=path)
            with self.assertRaisesRegex(ScreenEffectError, "fully restored"):
                effect.apply(ColorValues(saturation=180))
            self.assertTrue(effect.active)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tuple(Effect(*record["target"])), fake.state)
            block_restore[0] = False
            recovered = ScreenEffect(fake, recovery_path=path)
            self.assertTrue(recovered.recovery_needed())
            recovered.recover_previous(True)
            self.assertEqual(fake.state, tuple(Effect(*color_matrix(ColorValues()))))
            self.assertFalse(path.exists())

    def test_external_matrix_after_partial_readback_is_preserved_during_rollback(self):
        fake = FakeMagnification()
        original_set = fake.set
        outside = tuple(Effect(*color_matrix(ColorValues(saturation=70))))
        effect = ScreenEffect(fake)

        def partial_set(pointer):
            original_set(pointer)
            partial = list(fake.state)
            partial[0] += 0.125
            fake.state = tuple(Effect(*partial))
            return 1

        fake.MagSetFullscreenColorEffect.callback = partial_set

        def later_external_change(*_args):
            if fake.write_calls:
                fake.state = outside

        with patch.object(effect, "_save_recovery", side_effect=later_external_change):
            with self.assertRaises(ScreenEffectError):
                effect.apply(ColorValues(saturation=180))
        self.assertEqual(fake.state, outside)
        self.assertEqual(fake.write_calls, 1)

    def test_near_identical_external_matrix_is_never_overwritten_or_confirmed(self):
        for operation in ("disable", "apply", "verify", "recover"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "color-recovery.json"
                fake = FakeMagnification()
                effect = ScreenEffect(fake, recovery_path=path)
                values = ColorValues(saturation=140)
                effect.apply(values)
                newer = list(fake.state)
                newer[0] += 0.000001
                fake.state = tuple(newer)
                writes = fake.write_calls
                if operation == "disable":
                    self.assertFalse(effect.disable())
                elif operation == "apply":
                    with self.assertRaisesRegex(ScreenEffectError, "Another app"):
                        effect.apply(ColorValues(saturation=150))
                elif operation == "verify":
                    with self.assertRaisesRegex(ScreenEffectError, "Another app"):
                        effect.verify_current(values)
                else:
                    recovered = ScreenEffect(fake, recovery_path=path)
                    self.assertFalse(recovered.recovery_needed())
                self.assertEqual(fake.write_calls, writes)
                self.assertEqual(fake.state, tuple(newer))

    def test_failed_gamma_write_with_partial_side_effect_is_read_back_and_rolled_back(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        write = gamma.write

        def partially_failing_write(name, ramp):
            if tuple(ramp) != gamma.original:
                gamma.ramps[name] = tuple(value // 256 * 256 for value in ramp)
                raise ScreenEffectError("Fake driver partially wrote then failed")
            write(name, ramp)

        gamma.write = partially_failing_write
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaisesRegex(ScreenEffectError, "partially wrote"):
            effect.apply(ColorValues(saturation=180, gamma=150))
        self.assertEqual(gamma.ramps, {name: gamma.original for name in gamma.ramps})
        self.assertFalse(effect.active)
        self.assertEqual(fake.write_calls, 0)

    def test_failed_partial_write_keeps_exact_recovery_when_rollback_also_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            blocked = [True]
            write = gamma.write

            def failing_write(name, ramp):
                if tuple(ramp) != gamma.original:
                    gamma.ramps[name] = tuple(value // 256 * 256 for value in ramp)
                    raise ScreenEffectError("Partial driver failure")
                if blocked[0]:
                    raise ScreenEffectError("Restore also failed")
                write(name, ramp)

            gamma.write = failing_write
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            with self.assertRaisesRegex(ScreenEffectError, "fully restored"):
                effect.apply(ColorValues(gamma=150))
            self.assertTrue(effect.active)
            record = json.loads(path.read_text(encoding="utf-8"))
            for name, observed in gamma.ramps.items():
                self.assertEqual(tuple(record["gamma"]["target"][name]), observed)
            blocked[0] = False
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            recovered.recover_previous(True)
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            self.assertFalse(path.exists())

    def test_near_identical_external_gamma_is_never_overwritten_on_disable(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(gamma=150))
        name = next(iter(gamma.ramps))
        newer = list(gamma.ramps[name])
        newer[128] += 1
        gamma.ramps[name] = tuple(newer)
        writes = len(gamma.writes)
        self.assertFalse(effect.disable())
        self.assertEqual(gamma.ramps[name], tuple(newer))
        self.assertEqual(len(gamma.writes), writes)

    def test_near_identical_external_gamma_is_not_claimed_for_crash_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "color-recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            first = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            first.apply(ColorValues(gamma=150))
            name = next(iter(gamma.ramps))
            newer = list(gamma.ramps[name])
            newer[128] += 1
            gamma.ramps[name] = tuple(newer)
            writes = len(gamma.writes)
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            recovered.recover_previous(True)
            self.assertEqual(gamma.ramps[name], tuple(newer))
            self.assertEqual(len(gamma.writes), writes)
            self.assertFalse(path.exists())

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
            remaining = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(remaining["original"], json.loads(record)["original"])
            self.assertEqual(tuple(Effect(*remaining["target"])), fake.state)
            self.assertIsNone(remaining["gamma"])  # Independently restored.
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
            remaining = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(remaining["original"], json.loads(record)["original"])
            self.assertEqual(set(remaining["gamma"]["devices"]), {failed})
            self.assertEqual(tuple(remaining["gamma"]["target"][failed]), gamma.ramps[failed])
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
            remaining = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(remaining["original"], json.loads(record)["original"])
            self.assertEqual(remaining["target"], json.loads(record)["target"])
            self.assertIsNone(remaining["gamma"])
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
