"""Failure-injected display transactions. Never load or modify native APIs."""

import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from color_math import ColorValues, color_matrix, identity_matrix
from screen_backend import (Effect, ScreenEffect, ScreenEffectError, WindowsGammaApi,
                            _ModeInfo, _SourceName, _AdvancedColor, _Luid)
from test_color_effect import FakeGammaApi, FakeMagnification


class RestoreTransactionTests(unittest.TestCase):
    def test_partial_matrix_restore_retains_exact_owned_state_for_retry_and_reopen(self):
        for reopen in (False, True):
            with self.subTest(reopen=reopen), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "recovery.json"
                fake = FakeMagnification()
                effect = ScreenEffect(fake, recovery_path=path)
                effect.apply(ColorValues(saturation=160))
                write = fake.set
                once = [True]

                def partial_restore(pointer):
                    result = write(pointer)
                    if once[0]:
                        once[0] = False
                        observed = list(fake.state)
                        observed[0] += .125
                        fake.state = tuple(Effect(*observed))
                    return result

                fake.MagSetFullscreenColorEffect.callback = partial_restore
                with self.assertRaisesRegex(ScreenEffectError, "Try Revert"):
                    effect.disable()
                self.assertTrue(effect.active)
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(tuple(Effect(*record["target"])), fake.state)
                if reopen:
                    effect = ScreenEffect(fake, recovery_path=path)
                    self.assertTrue(effect.recovery_needed())
                    effect.recover_previous(True)
                else:
                    self.assertTrue(effect.disable())
                self.assertEqual(fake.state, tuple(Effect(*identity_matrix())))
                self.assertFalse(path.exists())

    def test_partial_gamma_restore_retains_exact_state_for_retry_and_reopen(self):
        for reopen in (False, True):
            with self.subTest(reopen=reopen), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "recovery.json"
                fake, gamma = FakeMagnification(), FakeGammaApi()
                effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
                effect.apply(ColorValues(gamma=150))
                write, once = gamma.write, [True]

                def partial_restore(name, ramp):
                    write(name, ramp)
                    if once[0]:
                        once[0] = False
                        observed = list(gamma.ramps[name])
                        observed[128] += 120
                        gamma.ramps[name] = tuple(observed)

                gamma.write = partial_restore
                with self.assertRaises(ScreenEffectError):
                    effect.disable()
                self.assertTrue(effect.active)
                record = json.loads(path.read_text(encoding="utf-8"))
                for name, observed in gamma.ramps.items():
                    self.assertEqual(tuple(record["gamma"]["target"][name]), observed)
                if reopen:
                    effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
                    self.assertTrue(effect.recovery_needed())
                    effect.recover_previous(True)
                else:
                    self.assertTrue(effect.disable())
                self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
                self.assertFalse(path.exists())

    def test_partial_previous_session_matrix_restore_updates_durable_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake = FakeMagnification()
            baseline = color_matrix(ColorValues(saturation=80))
            fake.state = baseline
            ScreenEffect(fake, recovery_path=path).apply(ColorValues(saturation=180))
            write, once = fake.set, [True]

            def partial_restore(pointer):
                result = write(pointer)
                if once[0]:
                    once[0] = False
                    observed = list(fake.state)
                    observed[0] += .1
                    fake.state = tuple(Effect(*observed))
                return result

            fake.MagSetFullscreenColorEffect.callback = partial_restore
            recovered = ScreenEffect(fake, recovery_path=path)
            with self.assertRaisesRegex(ScreenEffectError, "record was retained"):
                recovered.recover_previous(True)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tuple(Effect(*record["target"])), fake.state)
            self.assertEqual(tuple(Effect(*record["original"])), tuple(Effect(*baseline)))
            recovered = ScreenEffect(fake, recovery_path=path)
            recovered.recover_previous(True)
            self.assertEqual(fake.state, tuple(Effect(*baseline)))
            self.assertFalse(path.exists())

    def test_partial_previous_session_gamma_restore_updates_durable_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            ScreenEffect(fake, recovery_path=path, gamma_api=gamma).apply(ColorValues(gamma=150))
            write, once = gamma.write, [True]

            def partial_restore(name, ramp):
                write(name, ramp)
                if once[0]:
                    once[0] = False
                    values = list(gamma.ramps[name])
                    values[128] += 120
                    gamma.ramps[name] = tuple(values)

            gamma.write = partial_restore
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            with self.assertRaises(ScreenEffectError):
                recovered.recover_previous(True)
            record = json.loads(path.read_text(encoding="utf-8"))
            name = next(iter(gamma.ramps))
            self.assertEqual(tuple(record["gamma"]["target"][name]), gamma.ramps[name])
            ScreenEffect(fake, recovery_path=path, gamma_api=gamma).recover_previous(True)
            self.assertEqual(gamma.ramps[name], gamma.original)
            self.assertFalse(path.exists())

    def test_partial_matrix_rollback_is_retryable_with_exact_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake = FakeMagnification()
            write = fake.set

            def partial_twice(pointer):
                write(pointer)
                if fake.write_calls <= 2:
                    observed = list(fake.state)
                    observed[0] += .1 * fake.write_calls
                    fake.state = tuple(Effect(*observed))
                    return 0
                return 1

            fake.MagSetFullscreenColorEffect.callback = partial_twice
            effect = ScreenEffect(fake, recovery_path=path)
            with self.assertRaisesRegex(ScreenEffectError, "fully restored"):
                effect.apply(ColorValues(saturation=180))
            self.assertTrue(effect.active)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tuple(Effect(*record["target"])), fake.state)
            self.assertTrue(effect.disable())
            self.assertEqual(fake.state, tuple(Effect(*identity_matrix())))

    def test_transient_gamma_readback_failure_still_identifies_partial_write(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        read, write, fail = gamma.read, gamma.write, [False]

        def partial_write(name, ramp):
            write(name, ramp)
            if tuple(ramp) != gamma.original:
                values = list(gamma.ramps[name])
                values[128] += 120
                gamma.ramps[name] = tuple(values)
                fail[0] = True

        def transient_read(name):
            if fail[0]:
                fail[0] = False
                raise ScreenEffectError("Transient readback failure")
            return read(name)

        gamma.read, gamma.write = transient_read, partial_write
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaisesRegex(ScreenEffectError, "did not apply"):
            effect.apply(ColorValues(gamma=150))
        self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
        self.assertFalse(effect.active)

    def test_partial_gamma_rollback_retains_exact_remaining_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            write, calls = gamma.write, [0]

            def partial_twice(name, ramp):
                write(name, ramp)
                calls[0] += 1
                if calls[0] <= 2:
                    values = list(gamma.ramps[name])
                    values[128] += 120 * calls[0]
                    gamma.ramps[name] = tuple(values)

            gamma.write = partial_twice
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            with self.assertRaisesRegex(ScreenEffectError, "fully restored"):
                effect.apply(ColorValues(gamma=150))
            self.assertTrue(effect.active)
            record = json.loads(path.read_text(encoding="utf-8"))
            name = next(iter(gamma.ramps))
            self.assertEqual(tuple(record["gamma"]["target"][name]), gamma.ramps[name])
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            recovered.recover_previous(True)
            self.assertEqual(gamma.ramps[name], gamma.original)
            self.assertFalse(path.exists())

    def test_unknown_gamma_write_keeps_record_and_never_claims_arbitrary_later_ramp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            read, write, blocked = gamma.read, gamma.write, [False]

            def failing_read(name):
                if blocked[0]:
                    raise ScreenEffectError("Unreadable driver state")
                return read(name)

            def partial_write(name, ramp):
                write(name, ramp)
                values = list(gamma.ramps[name])
                values[128] += 120
                gamma.ramps[name] = tuple(values)
                blocked[0] = True

            gamma.read, gamma.write = failing_read, partial_write
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            with self.assertRaisesRegex(ScreenEffectError, "fully restored"):
                effect.apply(ColorValues(gamma=150))
            blocked[0] = False
            writes = len(gamma.writes)
            for _ in range(2):
                with self.assertRaises(ScreenEffectError):
                    effect.disable()
                self.assertTrue(path.exists())
                self.assertTrue(effect.active)
            self.assertEqual(len(gamma.writes), writes)
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            self.assertTrue(recovered.recovery_needed())
            with self.assertRaises(ScreenEffectError):
                recovered.recover_previous(True)
            self.assertTrue(path.exists())
            self.assertEqual(len(gamma.writes), writes)
            recovered.recover_previous(False)  # Explicit user decision only.
            self.assertFalse(path.exists())

    def test_unknown_matrix_write_stays_active_when_no_prior_preview_existed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake = FakeMagnification()
            write = fake.set

            def unknown_write(pointer):
                write(pointer)
                values = list(fake.state)
                values[0] += .15
                fake.state = tuple(Effect(*values))
                fake.fail_reads = True
                return 1

            fake.MagSetFullscreenColorEffect.callback = unknown_write
            effect = ScreenEffect(fake, recovery_path=path)
            with self.assertRaises(ScreenEffectError):
                effect.apply(ColorValues(saturation=180))
            self.assertTrue(effect.active)
            self.assertTrue(json.loads(path.read_text())["matrix_unresolved"])
            fake.fail_reads = False
            writes = fake.write_calls
            with self.assertRaises(ScreenEffectError):
                effect.disable()
            self.assertEqual(fake.write_calls, writes)
            self.assertTrue(path.exists())
            self.assertTrue(effect.active)

    def test_unknown_exact_matrix_target_can_be_reverted_when_readback_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake = FakeMagnification()
            write = fake.set

            def exact_write(pointer):
                result = write(pointer)
                if fake.write_calls == 1:
                    fake.fail_reads = True
                return result

            fake.MagSetFullscreenColorEffect.callback = exact_write
            effect = ScreenEffect(fake, recovery_path=path)
            with self.assertRaises(ScreenEffectError):
                effect.apply(ColorValues(saturation=180))
            self.assertTrue(effect.active)
            fake.fail_reads = False
            self.assertTrue(effect.disable())
            self.assertEqual(fake.state, tuple(Effect(*identity_matrix())))
            self.assertFalse(path.exists())

    def test_hdr_transition_between_targets_never_writes_the_now_hdr_display(self):
        fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
        first, second = list(gamma.ramps)
        gamma.after_write = lambda: gamma.devices[second].update(sdr=False)
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaises(ScreenEffectError):
            effect.apply(ColorValues(gamma=150))
        self.assertFalse(any(name == second for name, _ in gamma.writes))
        self.assertEqual(gamma.ramps[first], gamma.original)
        self.assertEqual(fake.write_calls, 0)

    def test_hdr_transition_during_restore_is_checked_before_each_target(self):
        fake, gamma = FakeMagnification(), FakeGammaApi(count=2)
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(gamma=150))
        first, second = list(gamma.ramps)
        writes = len(gamma.writes)
        gamma.after_write = lambda: gamma.devices[second].update(sdr=False)
        self.assertFalse(effect.disable())
        self.assertEqual(gamma.writes[writes:], [(first, gamma.original)])

    def test_mode_change_after_final_gamma_write_rejects_matrix_mutation(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        name = next(iter(gamma.devices))
        gamma.devices[name]["modes"] = [[0] * 31]
        gamma.after_write = lambda: gamma.devices[name]["modes"][0].__setitem__(6, 2560)
        effect = ScreenEffect(fake, gamma_api=gamma)
        with self.assertRaisesRegex(ScreenEffectError, "mode changed"):
            effect.apply(ColorValues(saturation=160, gamma=150))
        self.assertEqual(fake.write_calls, 0)

    def test_external_matrix_change_during_journal_write_is_not_overwritten(self):
        fake = FakeMagnification()
        effect = ScreenEffect(fake)
        outside = tuple(Effect(*color_matrix(ColorValues(saturation=75))))
        with patch.object(effect, "_save_recovery", side_effect=lambda *_: setattr(fake, "state", outside)):
            with self.assertRaisesRegex(ScreenEffectError, "Another app"):
                effect.apply(ColorValues(saturation=180))
        self.assertEqual(fake.write_calls, 0)
        self.assertEqual(fake.state, outside)

    def test_durable_intent_failure_does_not_write_any_display(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        with patch.object(effect, "_save_recovery", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                effect.apply(ColorValues(saturation=160, gamma=150))
        self.assertEqual(fake.write_calls, 0)
        self.assertEqual(gamma.writes, [])

    def test_disk_failure_after_partial_restore_preserves_in_memory_retry_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake = FakeMagnification()
            effect = ScreenEffect(fake, recovery_path=path)
            effect.apply(ColorValues(saturation=180))
            write, save, fail = fake.set, effect._save_recovery, [True]

            def partial_restore(pointer):
                result = write(pointer)
                if fail[0]:
                    values = list(fake.state)
                    values[0] += .1
                    fake.state = tuple(Effect(*values))
                return result

            def save_failure(*args):
                if fail[0] and not effect._matrix_unresolved:
                    raise OSError("Injected disk full after restore readback")
                return save(*args)

            fake.MagSetFullscreenColorEffect.callback = partial_restore
            with patch.object(effect, "_save_recovery", side_effect=save_failure):
                with self.assertRaises(ScreenEffectError):
                    effect.disable()
            self.assertTrue(effect.active)
            self.assertTrue(path.exists())
            fail[0] = False
            self.assertTrue(effect.disable())
            self.assertEqual(fake.state, tuple(Effect(*identity_matrix())))

    def test_neutral_gamma_preview_uses_only_two_durable_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            effect = ScreenEffect(FakeMagnification(), recovery_path=path)
            with patch("screen_backend.os.fsync", wraps=__import__("os").fsync) as fsync:
                effect.apply(ColorValues(saturation=180))
            self.assertEqual(fsync.call_count, 2)

    def test_post_restore_persistence_failure_keeps_unknown_evidence_after_restart(self):
        for layer in ("matrix", "gamma"):
            with self.subTest(layer=layer), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "recovery.json"
                fake, gamma = FakeMagnification(), FakeGammaApi()
                effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
                effect.apply(ColorValues(saturation=180, gamma=150 if layer == "gamma" else 100))
                save = effect._save_recovery
                if layer == "matrix":
                    write = fake.set

                    def partial_write(pointer):
                        result = write(pointer)
                        values = list(fake.state)
                        values[0] += .1
                        fake.state = tuple(Effect(*values))
                        return result

                    fake.MagSetFullscreenColorEffect.callback = partial_write
                else:
                    write = gamma.write

                    def partial_write(name, ramp):
                        write(name, ramp)
                        values = list(gamma.ramps[name])
                        values[128] += 120
                        gamma.ramps[name] = tuple(values)

                    gamma.write = partial_write

                def failing_save(*args):
                    pending = effect._matrix_unresolved if layer == "matrix" else bool(effect._gamma.unresolved)
                    if not pending:
                        raise OSError("Disk failure after observed restore result")
                    return save(*args)

                with patch.object(effect, "_save_recovery", side_effect=failing_save):
                    with self.assertRaises(ScreenEffectError):
                        effect.disable()
                retained = json.loads(path.read_text())
                self.assertTrue(retained["matrix_unresolved"] if layer == "matrix" else retained["gamma"]["unresolved"])
                recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
                self.assertTrue(recovered.recovery_needed())
                writes = fake.write_calls, len(gamma.writes)
                with self.assertRaises(ScreenEffectError):
                    recovered.recover_previous(True)
                self.assertTrue(path.exists())
                observed_writes = fake.write_calls if layer == "matrix" else len(gamma.writes)
                self.assertEqual(observed_writes, writes[0 if layer == "matrix" else 1])
                self.assertTrue(recovered.active)

    def test_unknown_exact_gamma_target_can_be_reverted_when_readback_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            read, write, blocked = gamma.read, gamma.write, [False]

            def blocked_read(name):
                if blocked[0]:
                    raise ScreenEffectError("Readback unavailable")
                return read(name)

            def exact_write(name, ramp):
                write(name, ramp)
                if tuple(ramp) != gamma.original:
                    blocked[0] = True

            gamma.read, gamma.write = blocked_read, exact_write
            effect = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            with self.assertRaises(ScreenEffectError):
                effect.apply(ColorValues(gamma=150))
            data = json.loads(path.read_text())
            name = next(iter(gamma.ramps))
            self.assertEqual(tuple(data["gamma"]["target"][name]), gamma.ramps[name])
            blocked[0] = False
            self.assertTrue(effect.disable())
            self.assertEqual(gamma.ramps[name], gamma.original)
            self.assertFalse(path.exists())

    def test_legacy_gamma_record_recovers_against_verified_modern_mode_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            ScreenEffect(fake, recovery_path=path, gamma_api=gamma).apply(ColorValues(gamma=150))
            record = json.loads(path.read_text())
            record["schema"] = 2
            record.pop("matrix_unresolved")
            record["gamma"].pop("unresolved")
            path.write_text(json.dumps(record), encoding="utf-8")
            for device in gamma.devices.values():
                device["modes"] = [[0] * 31]
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            recovered.recover_previous(True)
            self.assertTrue(all(ramp == gamma.original for ramp in gamma.ramps.values()))
            self.assertFalse(path.exists())

    def test_mode_change_during_recovery_read_does_not_adopt_changed_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recovery.json"
            fake, gamma = FakeMagnification(), FakeGammaApi()
            for device in gamma.devices.values():
                device["modes"] = [[0] * 31]
            ScreenEffect(fake, recovery_path=path, gamma_api=gamma).apply(ColorValues(gamma=150))
            read = gamma.read

            def mode_changed_read(name):
                result = read(name)
                gamma.devices[name]["modes"][0][6] = 2560
                return result

            gamma.read = mode_changed_read
            writes = len(gamma.writes)
            recovered = ScreenEffect(fake, recovery_path=path, gamma_api=gamma)
            recovered.recover_previous(True)
            self.assertEqual(len(gamma.writes), writes)

    def test_external_gamma_change_during_restore_journal_is_not_overwritten(self):
        fake, gamma = FakeMagnification(), FakeGammaApi()
        effect = ScreenEffect(fake, gamma_api=gamma)
        effect.apply(ColorValues(gamma=150))
        name = next(iter(gamma.ramps))
        outside = list(gamma.ramps[name])
        outside[128] += 1
        outside = tuple(outside)
        writes = len(gamma.writes)
        with patch.object(effect, "_save_recovery", side_effect=lambda *_: gamma.ramps.update({name: outside})):
            with self.assertRaises(ScreenEffectError):
                effect.disable()
        self.assertEqual(len(gamma.writes), writes)
        self.assertEqual(gamma.ramps[name], outside)


class DisplayModeFingerprintTests(unittest.TestCase):
    class QueryApi:
        def __init__(self):
            self.width, self.height, self.refresh, self.rotation = 1920, 1080, 60, 1
            self.mode_reordered = False

        def GetDisplayConfigBufferSizes(self, flags, path_count, mode_count):
            ctypes.cast(path_count, ctypes.POINTER(ctypes.c_uint32))[0] = 1
            ctypes.cast(mode_count, ctypes.POINTER(ctypes.c_uint32))[0] = 2
            return 0

        def QueryDisplayConfig(self, flags, path_count, paths, mode_count, modes, topology):
            source_index, target_index = (1, 0) if self.mode_reordered else (0, 1)
            path = paths[0]
            path.source.id, path.source.mode = 1, source_index
            path.target.id, path.target.mode = 2, target_index
            path.target.available = 1
            path.target.rotation, path.target.refreshNumerator = self.rotation, self.refresh
            path.target.refreshDenominator = 1
            source, target = modes[source_index], modes[target_index]
            source.type, source.id = 1, 1
            source.data.source.width, source.data.source.height = self.width, self.height
            target.type, target.id = 2, 2
            target.data.target.pixelRate = self.width * self.height * self.refresh
            target.data.target.active.width, target.data.target.active.height = self.width, self.height
            target.data.target.vSync.numerator, target.data.target.vSync.denominator = self.refresh, 1
            return 0

        def DisplayConfigGetDeviceInfo(self, header):
            kind = ctypes.cast(header, ctypes.POINTER(ctypes.c_uint32))[0]
            if kind == 1:
                ctypes.cast(header, ctypes.POINTER(_SourceName)).contents.name = "\\\\.\\DISPLAY1"
            elif kind == 9:
                advanced = ctypes.cast(header, ctypes.POINTER(_AdvancedColor)).contents
                advanced.bits = 8
            return 0

    def test_resolution_refresh_rotation_fingerprint_changes_but_array_order_does_not(self):
        api = WindowsGammaApi.__new__(WindowsGammaApi)
        api.user = self.QueryApi()
        baseline = api.snapshot()
        api.user.mode_reordered = True
        self.assertEqual(api.snapshot(), baseline)
        for field, value in (("width", 2560), ("height", 1440), ("refresh", 144), ("rotation", 2)):
            with self.subTest(field=field):
                old = getattr(api.user, field)
                setattr(api.user, field, value)
                self.assertNotEqual(api.snapshot(), baseline)
                setattr(api.user, field, old)

    def test_unverified_source_mode_is_rejected(self):
        api = WindowsGammaApi.__new__(WindowsGammaApi)
        api.user = self.QueryApi()
        api.user.width = 0
        with self.assertRaisesRegex(ScreenEffectError, "active display mode"):
            api.snapshot()


if __name__ == "__main__":
    unittest.main()
