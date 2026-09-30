"""Careful wrapper for Windows' shared full-screen Magnification color effect.

Display changes require a live preview, Apply or explicit recovery. Reading a previous
session's recovery state can also load the API. Windows color state is shared.
"""

import ctypes
import json
import math
import os
import uuid
from pathlib import Path

from color_math import color_matrix, gamma_ramp, identity_matrix, matrices_match


Effect = ctypes.c_float * 25
GammaRamp = ctypes.c_uint16 * 768


class ScreenEffectError(RuntimeError):
    pass


class _Luid(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]


class _SourceInfo(ctypes.Structure):
    _fields_ = [("adapter", _Luid), ("id", ctypes.c_uint32),
                ("mode", ctypes.c_uint32), ("status", ctypes.c_uint32)]


class _TargetInfo(ctypes.Structure):
    _fields_ = [("adapter", _Luid), ("id", ctypes.c_uint32),
                ("mode", ctypes.c_uint32), ("technology", ctypes.c_uint32),
                ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32),
                ("refreshNumerator", ctypes.c_uint32), ("refreshDenominator", ctypes.c_uint32),
                ("scanline", ctypes.c_uint32), ("available", ctypes.c_int32),
                ("status", ctypes.c_uint32)]


class _PathInfo(ctypes.Structure):
    _fields_ = [("source", _SourceInfo), ("target", _TargetInfo), ("flags", ctypes.c_uint32)]


class _ModeUnion(ctypes.Union):
    # DISPLAYCONFIG_TARGET_MODE is the largest union member (48 bytes,
    # 8-byte alignment because the video signal contains a UINT64 pixelRate).
    _fields_ = [("data", ctypes.c_uint64 * 6)]


class _ModeInfo(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("id", ctypes.c_uint32),
                ("adapter", _Luid), ("data", _ModeUnion)]


class _DeviceHeader(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("adapter", _Luid), ("id", ctypes.c_uint32)]


class _SourceName(ctypes.Structure):
    _fields_ = [("header", _DeviceHeader), ("name", ctypes.c_wchar * 32)]


class _AdvancedColor(ctypes.Structure):
    _fields_ = [("header", _DeviceHeader), ("flags", ctypes.c_uint32),
                ("encoding", ctypes.c_uint32), ("bits", ctypes.c_uint32)]


class WindowsGammaApi:
    """SDR-only GDI gamma access for non-neutral gamma or recorded recovery.

    The shared legacy API may be silently ignored by a driver, so its boolean
    return is insufficient: every write also needs independent ramp readback.
    HDR/advanced color and unverified modes are rejected before any gamma write.
    """
    def __init__(self):
        if os.name != "nt":
            raise ScreenEffectError("Gamma adjustments need Windows 10 or 11.")
        self.user = ctypes.WinDLL("user32.dll", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32.dll", use_last_error=True)
        self.user.GetDisplayConfigBufferSizes.argtypes = [ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        self.user.GetDisplayConfigBufferSizes.restype = ctypes.c_int32
        self.user.QueryDisplayConfig.argtypes = [ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_PathInfo),
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_ModeInfo), ctypes.c_void_p]
        self.user.QueryDisplayConfig.restype = ctypes.c_int32
        self.user.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(_DeviceHeader)]
        self.user.DisplayConfigGetDeviceInfo.restype = ctypes.c_int32
        self.gdi.CreateDCW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                     ctypes.c_wchar_p, ctypes.c_void_p]
        self.gdi.CreateDCW.restype = ctypes.c_void_p
        self.gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        self.gdi.DeleteDC.restype = ctypes.c_int32
        self.gdi.GetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi.GetDeviceGammaRamp.restype = ctypes.c_int32
        self.gdi.SetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi.SetDeviceGammaRamp.restype = ctypes.c_int32

    def snapshot(self):
        flags = 2  # QDC_ONLY_ACTIVE_PATHS, no configuration is modified.
        for _ in range(3):
            path_count, mode_count = ctypes.c_uint32(), ctypes.c_uint32()
            result = self.user.GetDisplayConfigBufferSizes(flags, ctypes.byref(path_count),
                                                            ctypes.byref(mode_count))
            if result or not 0 < path_count.value <= 128 or mode_count.value > 1024:
                raise ScreenEffectError("Windows could not verify SDR display mode. Leave Gamma at 1.00.")
            paths = (_PathInfo * path_count.value)()
            modes = (_ModeInfo * max(1, mode_count.value))()
            result = self.user.QueryDisplayConfig(flags, ctypes.byref(path_count), paths,
                                                   ctypes.byref(mode_count), modes, None)
            if result == 122:  # The display topology changed between reads.
                continue
            if result:
                raise ScreenEffectError("Windows could not verify SDR display mode. Leave Gamma at 1.00.")
            displays = {}
            for path in paths[:path_count.value]:
                source, target = path.source, path.target
                name = _SourceName()
                name.header = _DeviceHeader(1, ctypes.sizeof(name), source.adapter, source.id)
                if self.user.DisplayConfigGetDeviceInfo(ctypes.byref(name.header)) or not name.name:
                    raise ScreenEffectError("Windows could not identify a display for Gamma.")
                advanced = _AdvancedColor()
                advanced.header = _DeviceHeader(9, ctypes.sizeof(advanced), target.adapter, target.id)
                if self.user.DisplayConfigGetDeviceInfo(ctypes.byref(advanced.header)):
                    raise ScreenEffectError("Windows could not verify HDR is off. Leave Gamma at 1.00.")
                # advancedColorEnabled and wideColorEnforced are both incompatible
                # with a confidently verified ordinary SDR gamma-ramp path.
                sdr = not bool(advanced.flags & 0b110)
                token = [source.adapter.low, source.adapter.high, source.id,
                         target.adapter.low, target.adapter.high, target.id]
                entry = displays.setdefault(name.name, {"tokens": [], "sdr": True})
                entry["tokens"].append(token)
                entry["sdr"] = entry["sdr"] and sdr
            for entry in displays.values():
                entry["tokens"].sort()
            return displays
        raise ScreenEffectError("The display configuration is changing. Try Gamma again after it settles.")

    def read(self, name):
        dc = self.gdi.CreateDCW("DISPLAY", name, None, None)
        if not dc:
            raise ScreenEffectError("Windows could not open a display for Gamma.")
        try:
            ramp = GammaRamp()
            if not self.gdi.GetDeviceGammaRamp(dc, ctypes.byref(ramp)):
                raise ScreenEffectError("This display driver cannot read Gamma. Leave Gamma at 1.00.")
            return tuple(ramp)
        finally:
            self.gdi.DeleteDC(dc)

    def write(self, name, ramp):
        dc = self.gdi.CreateDCW("DISPLAY", name, None, None)
        if not dc:
            raise ScreenEffectError("Windows could not open a display to set Gamma.")
        try:
            values = GammaRamp(*ramp)
            if not self.gdi.SetDeviceGammaRamp(dc, ctypes.byref(values)):
                raise ScreenEffectError("This display driver declined Gamma. Leave Gamma at 1.00.")
        finally:
            self.gdi.DeleteDC(dc)


def _ramps_match(left, right, tolerance=0):
    return len(left) == len(right) == 768 and all(abs(a - b) <= tolerance
                                               for a, b in zip(left, right))


class GammaEffect:
    def __init__(self, api=None):
        self.api = api
        self.originals = {}
        self.last = {}
        self.devices = {}

    def _api(self):
        if self.api is None:
            self.api = WindowsGammaApi()
        return self.api

    def prepare(self, gamma):
        if gamma == 100 and not self.originals:
            return None  # All other controls work without even loading GDI.
        api = self._api()
        devices = api.snapshot()
        if not devices or any(not item["sdr"] for item in devices.values()):
            raise ScreenEffectError("Gamma requires HDR and advanced color to be off on every display. Leave Gamma at 1.00.")
        if self.devices and devices != self.devices:
            raise ScreenEffectError("The display configuration changed. Revert colors before adjusting Gamma.")
        previous = {name: api.read(name) for name in devices}
        for name, ramp in self.last.items():
            if not _ramps_match(previous[name], ramp):
                raise ScreenEffectError("Another app changed display Gamma. Its settings will not be overwritten.")
        originals = self.originals or previous
        target = {name: gamma_ramp(ramp, gamma) for name, ramp in originals.items()}
        return {"devices": devices, "originals": originals, "previous": previous,
                "target": target, "gamma": gamma, "attempted": []}

    def commit(self, plan, record_progress=None):
        if plan is None:
            return
        api = self._api()
        if api.snapshot() != plan["devices"]:
            raise ScreenEffectError("Display mode changed before Gamma could be applied.")
        # Capture ownership before the first write, including unsuccessful
        # writes that a driver may have partially accepted. ScreenEffect owns
        # the transaction and performs rollback on any failure.
        self.devices = dict(plan["devices"])
        self.originals = dict(plan["originals"])
        self.last = dict(plan["previous"])
        for name, target in plan["target"].items():
            if not _ramps_match(api.read(name), plan["previous"][name]):
                raise ScreenEffectError("Another app changed Gamma during apply.")
            if _ramps_match(target, plan["previous"][name]):
                continue
            plan["attempted"].append(name)
            self.last[name] = target
            api.write(name, target)
            observed = api.read(name)
            # A driver can apply a quantized/partial curve while returning
            # success. Capture that exact state before rejecting it, so the
            # failed preview can still restore what this write changed.
            plan["target"][name] = observed
            self.last[name] = observed
            if record_progress is not None:
                record_progress()
            if not _ramps_match(observed, target, tolerance=2):
                raise ScreenEffectError("The display driver did not apply the requested Gamma. Leave Gamma at 1.00.")

    def rollback(self, plan, names=None):
        if plan is None:
            return
        api = self._api()
        devices = api.snapshot()
        if plan["attempted"] and not self.originals:
            self.devices = dict(plan["devices"])
            self.originals = dict(plan["originals"])
            self.last = dict(plan["target"])
        failures = []
        for name in reversed(list(names if names is not None else plan["attempted"])):
            if devices.get(name) != plan["devices"][name]:
                continue  # Detached/changed/HDR display is no longer ours.
            try:
                current = api.read(name)
                if _ramps_match(current, plan["previous"][name], tolerance=2):
                    self.last[name] = current
                elif _ramps_match(current, plan["target"][name], tolerance=2):
                    api.write(name, plan["previous"][name])
                    observed = api.read(name)
                    if not _ramps_match(observed, plan["previous"][name], tolerance=2):
                        raise ScreenEffectError("Windows could not restore the previous Gamma.")
                    self.last[name] = observed
            except Exception as error:
                failures.append(error)
            # A different current ramp belongs to an external actor. Never
            # overwrite it, including if Windows changed it during an apply.
        if failures:
            raise ScreenEffectError("Windows could not restore all previous Gamma settings. Try Revert again.") from failures[0]
        for name in list(self.originals):
            if _ramps_match(self.last[name], self.originals[name], tolerance=2):
                self._forget(name)

    def accept(self, plan):
        if plan is not None:
            self.devices = dict(plan["devices"])
            self.originals = dict(plan["originals"])
            self.last = dict(plan["target"])
            if plan["gamma"] == 100:
                self.originals, self.last, self.devices = {}, {}, {}

    def verify_current(self, gamma):
        """Read-only ownership check before confirming/retaining a preview."""
        if not self.originals:
            if gamma != 100:
                raise ScreenEffectError("The requested Gamma preview is no longer active. Preview it again before applying.")
            return
        api = self._api()
        if api.snapshot() != self.devices:
            raise ScreenEffectError("The display configuration changed. Its current Gamma will not be overwritten.")
        for name, expected in self.last.items():
            if not _ramps_match(expected, gamma_ramp(self.originals[name], gamma), tolerance=2):
                raise ScreenEffectError("The requested Gamma preview is no longer active. Preview it again before applying.")
            # last contains the exact accepted driver readback. Do not treat a
            # newer external ramp as ours merely because it is close to it.
            if not _ramps_match(api.read(name), expected):
                raise ScreenEffectError("Another app changed display Gamma. Its settings will not be overwritten.")

    def restore(self):
        if not self.originals:
            return True
        api = self._api()
        devices = api.snapshot()
        restored = True
        failures = []
        for name, original in list(self.originals.items()):
            if devices.get(name) != self.devices[name]:
                restored = False
                self._forget(name)
                continue
            try:
                current = api.read(name)
                if _ramps_match(current, original, tolerance=2):
                    self._forget(name)
                    continue
                if _ramps_match(current, self.last[name], tolerance=2):
                    api.write(name, original)
                    if not _ramps_match(api.read(name), original, tolerance=2):
                        raise ScreenEffectError("Windows could not restore the previous Gamma. Try Revert again.")
                else:
                    restored = False
                self._forget(name)
            except Exception as error:
                failures.append(error)
        if failures:
            raise ScreenEffectError("Windows could not restore all previous Gamma settings. Try Revert again.") from failures[0]
        return restored

    def _forget(self, name):
        self.originals.pop(name, None)
        self.last.pop(name, None)
        self.devices.pop(name, None)

    def recovery_data(self, plan=None):
        if plan is not None:
            return {key: plan[key] for key in ("devices", "originals", "previous", "target")}
        if not self.originals:
            return None
        return {"devices": self.devices, "originals": self.originals,
                "previous": self.last, "target": self.last}

    def matching_recovery(self, data):
        if data is None:
            return {}
        api = self._api()
        devices = api.snapshot()
        matching = {}
        for name, device in data["devices"].items():
            if devices.get(name) != device or not device["sdr"]:
                continue
            current = api.read(name)
            if any(_ramps_match(current, data[key][name], tolerance=2)
                   for key in ("target", "previous")):
                matching[name] = current
        return matching

    def recover(self, data):
        if data is None:
            return
        matching = self.matching_recovery(data)
        self.originals = {name: tuple(data["originals"][name]) for name in matching}
        self.devices = {name: data["devices"][name] for name in matching}
        self.last = matching
        self.restore()


class ScreenEffect:
    def __init__(self, dll=None, recovery_path=None, gamma_api=None):
        self._dll = dll
        self._initialized = False
        self._original = None
        self._last_applied = None
        self._recovery_path = Path(recovery_path) if recovery_path is not None else None
        self._gamma = GammaEffect(gamma_api)

    @property
    def active(self) -> bool:
        return self._last_applied is not None or bool(self._gamma.originals)

    @property
    def replaces_existing_effect(self) -> bool:
        return self._original is not None and not matrices_match(
            self._original, identity_matrix()
        )

    def initialize(self):
        if self._initialized:
            return
        if self._dll is None:
            if os.name != "nt":
                raise ScreenEffectError("This desktop effect needs Windows 10 or 11.")
            try:
                self._dll = ctypes.WinDLL("Magnification.dll", use_last_error=True)
            except OSError as error:
                raise ScreenEffectError("Windows Magnification is unavailable on this PC.") from error
        self._dll.MagInitialize.argtypes = []
        self._dll.MagInitialize.restype = ctypes.c_int
        self._dll.MagUninitialize.argtypes = []
        self._dll.MagUninitialize.restype = ctypes.c_int
        self._dll.MagGetFullscreenColorEffect.argtypes = [ctypes.POINTER(Effect)]
        self._dll.MagGetFullscreenColorEffect.restype = ctypes.c_int
        self._dll.MagSetFullscreenColorEffect.argtypes = [ctypes.POINTER(Effect)]
        self._dll.MagSetFullscreenColorEffect.restype = ctypes.c_int
        if not self._dll.MagInitialize():
            raise ScreenEffectError("Windows could not initialize its desktop color effect.")
        self._initialized = True
        try:
            self._original = self._read_matrix()
        except Exception:
            self._dll.MagUninitialize()
            self._initialized = False
            raise

    def _read_matrix(self) -> tuple[float, ...]:
        effect = Effect()
        if not self._dll.MagGetFullscreenColorEffect(ctypes.byref(effect)):
            raise ScreenEffectError("Windows could not read the current desktop color effect.")
        return tuple(effect)

    def _write_matrix(self, matrix: tuple[float, ...]):
        effect = Effect(*matrix)
        if not self._dll.MagSetFullscreenColorEffect(ctypes.byref(effect)):
            raise ScreenEffectError("Windows declined the desktop color change. Check display and HDR settings.")

    def _read_recovery(self):
        path = self._recovery_path
        if path is None or not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ScreenEffectError("The local color-recovery file is unsafe. Do not apply colors.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid recovery record")
            if data.get("schema") not in (1, 2):
                raise ValueError("Unsupported recovery record")
            for name in ("original", "target"):
                values = data[name]
                if len(values) != 25 or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in values
                ):
                    raise ValueError(f"Invalid {name} matrix")
            previous = data.get("previous")
            if previous is not None and (
                len(previous) != 25 or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in previous
                )
            ):
                raise ValueError("Invalid previous matrix")
            data["previous"] = previous
            gamma = data.get("gamma")
            if gamma is not None:
                if not isinstance(gamma, dict):
                    raise ValueError("Invalid Gamma record")
                names = gamma["devices"]
                if not isinstance(names, dict) or not 0 < len(names) <= 128:
                    raise ValueError("Invalid Gamma displays")
                for name, device in names.items():
                    if not isinstance(name, str) or not name.startswith("\\\\.\\DISPLAY") or len(name) > 32:
                        raise ValueError("Invalid Gamma display name")
                    if not isinstance(device, dict) or device.get("sdr") is not True:
                        raise ValueError("Unverified Gamma display")
                    tokens = device["tokens"]
                    if not isinstance(tokens, list) or not 0 < len(tokens) <= 128 or any(
                        not isinstance(token, list) or len(token) != 6 or any(
                            type(part) is not int for part in token) for token in tokens
                    ):
                        raise ValueError("Invalid Gamma display identity")
                for key in ("originals", "previous", "target"):
                    ramps = gamma[key]
                    if not isinstance(ramps, dict) or set(ramps) != set(names):
                        raise ValueError("Invalid Gamma ramps")
                    for ramp in ramps.values():
                        gamma_ramp(ramp, 100)  # Validates length and uint16 values.
            return data
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ScreenEffectError("The local color-recovery record could not be read safely.") from error

    def _save_recovery(self, target, gamma_plan=None):
        path = self._recovery_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ScreenEffectError("The local color-recovery file is unsafe. Do not apply colors.")
        data = {
            "schema": 2,
            "original": list(self._original),
            "previous": list(self._last_applied) if self._last_applied is not None else None,
            "target": list(target),
            "gamma": self._gamma.recovery_data(gamma_plan),
        }
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(data, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_recovery(self):
        path = self._recovery_path
        if path is not None and path.exists():
            if path.is_symlink() or not path.is_file():
                raise ScreenEffectError("The local color-recovery file is unsafe.")
            path.unlink()

    def recovery_needed(self) -> bool:
        """Read-only detection of a previous unclean exit; never auto-restore."""
        record = self._read_recovery()
        if record is None:
            return False
        self.initialize()
        current = self._read_matrix()
        candidates = [record["target"]]
        if record["previous"] is not None:
            candidates.append(record["previous"])
        if any(matrices_match(current, item) for item in candidates) or self._gamma.matching_recovery(record.get("gamma")):
            return True
        self._clear_recovery()
        self.disable()
        return False

    def recover_previous(self, restore: bool):
        record = self._read_recovery()
        if record is None:
            return
        self.initialize()
        candidates = [record["target"]]
        if record["previous"] is not None:
            candidates.append(record["previous"])
        if restore:
            failures = []
            try:
                current = self._read_matrix()
                if any(matrices_match(current, item) for item in candidates):
                    original = tuple(record["original"])
                    self._write_matrix(original)
                    if not matrices_match(self._read_matrix(), original):
                        raise ScreenEffectError("Windows did not restore the previous desktop color effect.")
            except (ScreenEffectError, OSError) as error:
                failures.append(error)
            try:
                # The layers restore independently: a declined matrix restore
                # must not leave an otherwise recoverable Gamma ramp behind.
                self._gamma.recover(record.get("gamma"))
            except (ScreenEffectError, OSError) as error:
                failures.append(error)
            if failures:
                # Retain the original durable record even after partial success;
                # retry recognizes any remaining owned layer without rewriting
                # restored or newer external state.
                raise ScreenEffectError("Windows could not fully recover previous colors. The recovery record was retained; close and reopen to retry.") from failures[0]
        else:
            # Declining after a failed recovery must not implicitly restore the
            # Gamma layer through disable(). Relinquish it without a write.
            self._gamma.originals, self._gamma.last, self._gamma.devices = {}, {}, {}
        self._clear_recovery()
        self.disable()

    def verify_current(self, values):
        """Verify actual shared display state without reapplying any layer."""
        if not self._initialized or self._last_applied is None:
            raise ScreenEffectError("The requested color preview is no longer active. Preview it again before applying.")
        current = self._read_matrix()
        if (not matrices_match(current, self._last_applied)
                or not matrices_match(current, color_matrix(values))):
            raise ScreenEffectError("Another app changed the desktop color effect. This app will not overwrite it.")
        self._gamma.verify_current(values.gamma)

    def apply(self, values):
        self.initialize()
        current = self._read_matrix()
        expected = self._last_applied if self._last_applied is not None else self._original
        if not matrices_match(current, expected):
            raise ScreenEffectError(
                "Another app changed the desktop color effect. This app will not overwrite it."
            )
        target = color_matrix(values)
        # Verify Gamma support/topology before any display mutation, including
        # the matrix. A rejected Gamma request must not partially apply colors.
        plan = self._gamma.prepare(values.gamma)
        self._save_recovery(target, plan)
        previous = self._last_applied
        matrix_attempted = False
        try:
            self._gamma.commit(plan, lambda: self._save_recovery(target, plan))
            # Save exact driver readback before changing the matrix, so a
            # terminated process can recognize its applied Gamma later.
            self._save_recovery(target, plan)
            if not matrices_match(self._read_matrix(), expected):
                raise ScreenEffectError("Another app changed the desktop color effect during apply.")
            self._last_applied = target
            matrix_attempted = True
            self._write_matrix(target)
            if not matrices_match(self._read_matrix(), target):
                raise ScreenEffectError("Windows did not apply the requested desktop color effect.")
            self._gamma.accept(plan)
            self._save_recovery(target)
        except Exception as error:
            rollback_failed = False
            try:
                current = self._read_matrix()
                if matrix_attempted and matrices_match(current, target) and not matrices_match(current, expected):
                    self._write_matrix(expected)
                    if not matrices_match(self._read_matrix(), expected):
                        raise ScreenEffectError("Windows could not restore the previous colors.")
                self._last_applied = previous
            except Exception:
                rollback_failed = True
            try:
                self._gamma.rollback(plan)
            except Exception:
                rollback_failed = True
            if rollback_failed:
                raise ScreenEffectError("Color apply failed and could not be fully restored. Use Revert again.") from error
            if self._last_applied is not None or self._gamma.originals:
                self._save_recovery(self._last_applied or self._original)
            else:
                self._clear_recovery()
            raise

    def disable(self) -> bool:
        """Restore the captured effect only if it is still ours.

        Returns False when another app changed the shared effect after us;
        its newer state must be left untouched.
        """
        if not self._initialized and not self._gamma.originals:
            return True
        restored = True
        failures = []
        if self._last_applied is not None:
            try:
                current = self._read_matrix()
                if matrices_match(current, self._last_applied):
                    # Keep ownership when Windows refuses restoration so
                    # Disable can retry while the other layer is restored.
                    self._write_matrix(self._original)
                    if not matrices_match(self._read_matrix(), self._original):
                        raise ScreenEffectError("Windows did not restore the previous colors.")
                    self._last_applied = None
                else:
                    restored = False
                    self._last_applied = None
            except Exception as error:
                failures.append(error)
        try:
            restored = self._gamma.restore() and restored
        except Exception as error:
            failures.append(error)
        if failures:
            # Preserve the record and retry state for either remaining layer.
            raise ScreenEffectError("Windows could not fully restore colors. Try Revert again.") from failures[0]
        self._clear_recovery()
        self._last_applied = None
        self._original = None
        if self._initialized:
            self._dll.MagUninitialize()
        self._initialized = False
        return restored
