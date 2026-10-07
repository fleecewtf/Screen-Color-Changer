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

from color_math import color_matrix, gamma_ramp, identity_matrix


Effect = ctypes.c_float * 25
GammaRamp = ctypes.c_uint16 * 768
MAX_RECOVERY_BYTES = 8 * 1024 * 1024


def _finite_windows_matrix(matrix):
    try:
        return len(matrix) == 25 and all(
            type(value) in (int, float) and math.isfinite(value)
            and math.isfinite(ctypes.c_float(value).value) for value in matrix
        )
    except (TypeError, ValueError, OverflowError):
        return False


def _windows_matrices_match(left, right):
    """Compare the actual FLOAT values accepted by Windows, without an epsilon.

    Python calculates in double precision, while MAGCOLOREFFECT stores float32.
    Canonicalizing both sides preserves legitimate conversion rounding without
    claiming another program's merely similar (but different) matrix as ours.
    """
    return len(left) == len(right) == 25 and tuple(Effect(*left)) == tuple(Effect(*right))


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


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _Region(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32)]


class _Rational(ctypes.Structure):
    _fields_ = [("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32)]


class _SourceMode(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("pixelFormat", ctypes.c_uint32), ("position", _Point)]


class _VideoSignal(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_uint64), ("hSync", _Rational),
                ("vSync", _Rational), ("active", _Region), ("total", _Region),
                ("standard", ctypes.c_uint32), ("scanline", ctypes.c_uint32)]


class _ModeUnion(ctypes.Union):
    # DISPLAYCONFIG_TARGET_MODE is the largest union member (48 bytes,
    # 8-byte alignment because the video signal contains a UINT64 pixelRate).
    _fields_ = [("data", ctypes.c_uint64 * 6), ("source", _SourceMode),
                ("target", _VideoSignal)]


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


def _mode_fingerprint(path, modes, count):
    """Normalize meaningful fields, never padding or unstable mode-array indices.

    The non-virtual QDC_ONLY_ACTIVE_PATHS API supplies ordinary source/target
    mode indices. Fail closed if a driver cannot provide their identities.
    """
    resolved = []
    for reference, kind in ((path.source, 1), (path.target, 2)):
        if reference.mode >= count:
            raise ScreenEffectError("Windows could not verify display resolution and refresh rate. Leave Gamma at 1.00.")
        mode = modes[reference.mode]
        if (mode.type != kind or mode.id != reference.id
                or mode.adapter.low != reference.adapter.low
                or mode.adapter.high != reference.adapter.high):
            raise ScreenEffectError("Windows returned inconsistent display mode information. Leave Gamma at 1.00.")
        resolved.append(mode)
    source, target = resolved[0].data.source, resolved[1].data.target
    if (not source.width or not source.height or not target.active.width
            or not target.active.height or not target.vSync.denominator
            or not path.target.refreshDenominator):
        raise ScreenEffectError("Windows could not verify the active display mode. Leave Gamma at 1.00.")
    return [source.width, source.height, source.pixelFormat,
            source.position.x, source.position.y, target.pixelRate,
            target.hSync.numerator, target.hSync.denominator,
            target.vSync.numerator, target.vSync.denominator,
            target.active.width, target.active.height,
            target.total.width, target.total.height, target.standard & 0x3FFFFF,
            target.scanline, path.target.rotation, path.target.scaling,
            path.target.refreshNumerator, path.target.refreshDenominator,
            path.target.scanline, path.target.technology, int(bool(path.target.available))]


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
                entry = displays.setdefault(name.name, {"tokens": [], "modes": [], "sdr": True})
                entry["tokens"].append(token)
                # Keep each clone target's mode paired with its identity.
                entry["modes"].append(token + _mode_fingerprint(path, modes, mode_count.value)
                                      + [advanced.encoding, advanced.bits])
                entry["sdr"] = entry["sdr"] and sdr
            for entry in displays.values():
                entry["tokens"].sort()
                entry["modes"].sort()
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


def _device_matches(current, expected, *, legacy=False):
    if not current or not current.get("sdr") or not expected.get("sdr"):
        return False
    if legacy and "modes" not in expected:
        # Old recovery records did not store mode details. Exact ramp ownership
        # plus current verified SDR identity remains required; adopt today's
        # fingerprint before any recovery write, then revalidate it normally.
        return current.get("tokens") == expected.get("tokens")
    return current == expected


class GammaEffect:
    def __init__(self, api=None):
        self.api = api
        self.originals = {}
        self.last = {}
        self.devices = {}
        self.unresolved = set()
        self.intents = {}

    def _api(self):
        if self.api is None:
            self.api = WindowsGammaApi()
        return self.api

    def prepare(self, gamma):
        if gamma == 100 and not self.originals:
            return None  # All other controls work without even loading GDI.
        api = self._api()
        if self.unresolved:
            raise ScreenEffectError("A previous Gamma write could not be verified. Use Revert before adjusting colors.")
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
                "target": target, "gamma": gamma, "attempted": [],
                "unresolved": [name for name in target if not _ramps_match(target[name], previous[name])]}

    def _read_after_write(self, name):
        # Only an immediate bounded retry may identify a transiently unreadable
        # side effect of our write. A later arbitrary state is not ours.
        try:
            return self._api().read(name)
        except (ScreenEffectError, OSError):
            return self._api().read(name)

    def _check_target(self, name, devices):
        if not _device_matches(self._api().snapshot().get(name), devices[name]):
            raise ScreenEffectError("Display identity, SDR mode, resolution or refresh rate changed before Gamma could be written.")

    def verify_plan(self, plan):
        if plan is None:
            return
        if self._api().snapshot() != plan["devices"]:
            raise ScreenEffectError("Display mode changed while Gamma was being applied.")
        for name, expected in plan["target"].items():
            if name in self.unresolved or not _ramps_match(self._api().read(name), expected):
                raise ScreenEffectError("Gamma changed before its preview could be confirmed.")
        if self._api().snapshot() != plan["devices"]:
            raise ScreenEffectError("Display mode changed during Gamma verification.")

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
                if name in plan["unresolved"]:
                    plan["unresolved"].remove(name)
                continue
            self._check_target(name, plan["devices"])
            plan["attempted"].append(name)
            self.unresolved.add(name)
            self.intents[name] = target
            write_error = None
            try:
                api.write(name, target)
            except Exception as error:
                # A failed driver call is not proof that it left the ramp
                # untouched. Read back and durably identify any partial write
                # before the transaction tries to roll it back.
                write_error = error
            observed = self._read_after_write(name)
            # A driver can apply a quantized/partial curve while returning
            # success. Capture that exact state before rejecting it, so the
            # failed preview can still restore what this write changed.
            plan["target"][name] = observed
            self.last[name] = observed
            self.unresolved.discard(name)
            self.intents.pop(name, None)
            plan["unresolved"].remove(name)
            if record_progress is not None:
                record_progress()
            if write_error is not None:
                raise write_error
            if not _ramps_match(observed, target, tolerance=2):
                raise ScreenEffectError("The display driver did not apply the requested Gamma. Leave Gamma at 1.00.")
        self.verify_plan(plan)
        plan["complete"] = True

    def rollback(self, plan, names=None, record_progress=None):
        if plan is None:
            return
        api = self._api()
        if plan["attempted"] and not self.originals:
            self.devices = dict(plan["devices"])
            self.originals = dict(plan["originals"])
            self.last = dict(plan["target"])
        failures = []
        for name in reversed(list(names if names is not None else plan["attempted"])):
            try:
                if not _device_matches(api.snapshot().get(name), plan["devices"][name]):
                    if name in self.unresolved:
                        raise ScreenEffectError("An unverified Gamma write is retained because display mode changed.")
                    self._forget(name)
                    continue  # Never write to a detached/changed/HDR display.
                current = api.read(name)
                if _ramps_match(current, plan["previous"][name]):
                    self.last[name] = current
                    self.unresolved.discard(name)
                    self.intents.pop(name, None)
                    if name in plan["unresolved"]:
                        plan["unresolved"].remove(name)
                elif _ramps_match(current, plan["target"][name]) or _ramps_match(current, self.last[name]):
                    self.last[name] = current
                    self.unresolved.discard(name)
                    self.intents.pop(name, None)
                    self._restore_write(name, plan["previous"][name], record_progress)
                    plan["target"][name] = self.last[name]
                    if name in plan["unresolved"]:
                        plan["unresolved"].remove(name)
                elif name in self.unresolved:
                    raise ScreenEffectError("The result of a Gamma write is unknown. Recovery evidence was retained; no unverified ramp will be overwritten.")
                else:
                    self._forget(name)
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
                self.unresolved.clear()
                self.intents.clear()

    def verify_current(self, gamma):
        """Read-only ownership check before confirming/retaining a preview."""
        if not self.originals:
            if gamma != 100:
                raise ScreenEffectError("The requested Gamma preview is no longer active. Preview it again before applying.")
            return
        api = self._api()
        if self.unresolved:
            raise ScreenEffectError("The Gamma write has not been verified. Use Revert before Apply.")
        if api.snapshot() != self.devices:
            raise ScreenEffectError("The display configuration changed. Its current Gamma will not be overwritten.")
        for name, expected in self.last.items():
            if not _ramps_match(expected, gamma_ramp(self.originals[name], gamma), tolerance=2):
                raise ScreenEffectError("The requested Gamma preview is no longer active. Preview it again before applying.")
            # last contains the exact accepted driver readback. Do not treat a
            # newer external ramp as ours merely because it is close to it.
            if not _ramps_match(api.read(name), expected):
                raise ScreenEffectError("Another app changed display Gamma. Its settings will not be overwritten.")
        if api.snapshot() != self.devices:
            raise ScreenEffectError("Display mode changed during Gamma verification.")

    def _restore_write(self, name, target, record_progress=None):
        """Capture every immediate restore result before testing success."""
        self._check_target(name, self.devices)
        self.unresolved.add(name)
        self.intents[name] = target
        if record_progress is not None:
            try:
                record_progress()
            except Exception:
                self.unresolved.discard(name)  # No write was attempted.
                self.intents.pop(name, None)
                raise
        try:
            if not _ramps_match(self._api().read(name), self.last[name]):
                raise ScreenEffectError("Another app changed Gamma before restoration. Its settings will not be overwritten.")
            self._check_target(name, self.devices)
        except Exception:
            self.unresolved.discard(name)  # Durable intent, but no actual write.
            self.intents.pop(name, None)
            raise
        error = None
        try:
            self._api().write(name, target)
        except Exception as failure:
            error = failure
        observed = self._read_after_write(name)
        self.last[name] = observed
        self.unresolved.discard(name)
        self.intents.pop(name, None)
        if record_progress is not None:
            record_progress()
        self._check_target(name, self.devices)
        if error is not None:
            raise error
        if not _ramps_match(observed, target, tolerance=2):
            raise ScreenEffectError("Windows only partially restored Gamma. Its exact remaining state was retained; try Revert again.")

    def restore(self, record_progress=None):
        if not self.originals:
            return True
        api = self._api()
        restored = True
        failures = []
        for name, original in list(self.originals.items()):
            try:
                if not _device_matches(api.snapshot().get(name), self.devices[name]):
                    if name in self.unresolved:
                        raise ScreenEffectError("An unverified Gamma write is retained because display mode changed.")
                    restored = False
                    self._forget(name)
                    continue
                current = api.read(name)
                if _ramps_match(current, original):
                    self._forget(name)
                    continue
                if (_ramps_match(current, self.last[name])
                        or (name in self.unresolved and _ramps_match(current, self.intents[name]))):
                    self.last[name] = current
                    self.unresolved.discard(name)
                    self.intents.pop(name, None)
                    self._restore_write(name, original, record_progress)
                elif name in self.unresolved:
                    raise ScreenEffectError("The result of a Gamma write is unknown. Recovery evidence was retained; no unverified ramp will be overwritten.")
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
        self.unresolved.discard(name)
        self.intents.pop(name, None)

    def recovery_data(self, plan=None):
        if plan is not None:
            if plan.get("complete") and plan.get("gamma") == 100:
                return None  # The baseline is already restored and verified.
            return {**{key: plan[key] for key in ("devices", "originals", "previous", "target")},
                    "previous": plan["target"] if plan.get("complete") else plan["previous"],
                    "unresolved": plan.get("unresolved", [])}
        if not self.originals:
            return None
        return {"devices": self.devices, "originals": self.originals,
                "previous": self.last,
                "target": {name: self.intents.get(name, self.last[name]) for name in self.last},
                "unresolved": sorted(self.unresolved)}

    def matching_recovery(self, data, devices=None):
        if data is None:
            return {}
        api = self._api()
        if devices is None:
            devices = api.snapshot()
        matching = {}
        for name, device in data["devices"].items():
            if not _device_matches(devices.get(name), device, legacy=True):
                continue
            current = api.read(name)
            if any(_ramps_match(current, data[key][name])
                   for key in ("target", "previous", "originals")):
                matching[name] = current
        return matching

    def adopt_recovery(self, data):
        if data is None:
            return
        devices = self._api().snapshot()
        matching = self.matching_recovery(data, devices)
        unresolved = set(data.get("unresolved", ()))
        # Unknown writes must not disappear just because a later read is not a
        # known candidate. Keep them pending without claiming arbitrary state.
        names = set(matching) | unresolved
        self.originals = {name: tuple(data["originals"][name]) for name in names}
        self.devices = {name: devices[name] if name in matching else data["devices"][name]
                        for name in names}
        self.last = {name: matching.get(name, tuple(data["previous"][name])) for name in names}
        self.unresolved = unresolved - matching.keys()
        self.intents = {name: tuple(data["target"][name]) for name in self.unresolved}

    def recover(self, data, record_progress=None):
        self.adopt_recovery(data)
        self.restore(record_progress)


class ScreenEffect:
    def __init__(self, dll=None, recovery_path=None, gamma_api=None):
        self._dll = dll
        self._initialized = False
        self._original = None
        self._last_applied = None
        self._matrix_unresolved = False
        self._matrix_intent = None
        self._journal_cache = None
        self._journal_stamp = None
        self._recovery_path = Path(recovery_path) if recovery_path is not None else None
        self._gamma = GammaEffect(gamma_api)

    @property
    def active(self) -> bool:
        return self._last_applied is not None or self._matrix_unresolved or bool(self._gamma.originals)

    @property
    def replaces_existing_effect(self) -> bool:
        return self._original is not None and not _windows_matrices_match(
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
        matrix = tuple(effect)
        if not _finite_windows_matrix(matrix):
            raise ScreenEffectError("Windows returned an invalid desktop color effect. No new colors were applied.")
        return matrix

    def _write_matrix(self, matrix: tuple[float, ...]):
        if not _finite_windows_matrix(matrix):
            raise ScreenEffectError("The desktop color matrix is invalid. No new colors were applied.")
        effect = Effect(*matrix)
        if not self._dll.MagSetFullscreenColorEffect(ctypes.byref(effect)):
            raise ScreenEffectError("Windows declined the desktop color change. Check display and HDR settings.")

    def _read_after_matrix_write(self):
        try:
            return self._read_matrix()
        except (ScreenEffectError, OSError):
            return self._read_matrix()

    def _write_matrix_tracked(self, target, gamma_plan=None):
        """Journal intent and exact result even when Windows partially fails."""
        previous_pending, previous_intent = self._matrix_unresolved, self._matrix_intent
        self._matrix_unresolved, self._matrix_intent = True, target
        try:
            self._save_recovery(target, gamma_plan)
        except Exception:
            self._matrix_unresolved, self._matrix_intent = previous_pending, previous_intent
            raise  # Durable intent failed: no display write is allowed.
        try:
            expected = self._last_applied if self._last_applied is not None else self._original
            if not _windows_matrices_match(self._read_matrix(), expected):
                raise ScreenEffectError("Another app changed the desktop color effect before the write. Its colors will not be overwritten.")
        except Exception:
            self._matrix_unresolved, self._matrix_intent = previous_pending, previous_intent
            raise  # Nothing was written; do not mark an unknown side effect.
        write_error = None
        try:
            self._write_matrix(target)
        except Exception as error:
            write_error = error
        observed = self._read_after_matrix_write()
        self._last_applied = observed
        self._matrix_unresolved, self._matrix_intent = False, None
        # Memory ownership updates precede persistence. If disk IO fails the
        # active session can still retry; the older durable intent remains.
        self._save_recovery(observed, gamma_plan)
        if write_error is not None:
            raise write_error
        if not _windows_matrices_match(observed, target):
            raise ScreenEffectError("Windows only partially applied or restored the desktop color effect. Its exact remaining state was retained.")
        return observed

    def _save_current_recovery(self, gamma_plan=None):
        target = self._matrix_intent if self._matrix_unresolved else self._last_applied
        self._save_recovery(target if target is not None else self._original, gamma_plan)

    def _read_recovery(self):
        path = self._recovery_path
        if path is None or not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ScreenEffectError("The local color-recovery file is unsafe. Do not apply colors.")
        try:
            # A damaged local record must not freeze the GUI while loading an
            # unbounded file. This comfortably fits the maximum 128 displays.
            with path.open("rb") as source:
                payload = source.read(MAX_RECOVERY_BYTES + 1)
            if len(payload) > MAX_RECOVERY_BYTES:
                raise ValueError("Recovery record is too large")
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid recovery record")
            if type(data.get("schema")) is not int or data["schema"] not in (1, 2, 3):
                raise ValueError("Unsupported recovery record")
            for name in ("original", "target"):
                values = data[name]
                if not _finite_windows_matrix(values):
                    raise ValueError(f"Invalid {name} matrix")
            previous = data.get("previous")
            if previous is not None and not _finite_windows_matrix(previous):
                raise ValueError("Invalid previous matrix")
            data["previous"] = previous
            if type(data.get("matrix_unresolved", False)) is not bool:
                raise ValueError("Invalid pending matrix write")
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
                    if "modes" in device:
                        modes = device["modes"]
                        if not isinstance(modes, list) or len(modes) != len(tokens) or any(
                            not isinstance(mode, list) or len(mode) != 31 or any(
                                type(part) is not int for part in mode) for mode in modes
                        ):
                            raise ValueError("Invalid Gamma display modes")
                for key in ("originals", "previous", "target"):
                    ramps = gamma[key]
                    if not isinstance(ramps, dict) or set(ramps) != set(names):
                        raise ValueError("Invalid Gamma ramps")
                    for ramp in ramps.values():
                        gamma_ramp(ramp, 100)  # Validates length and uint16 values.
                unresolved = gamma.get("unresolved", [])
                if (not isinstance(unresolved, list) or len(unresolved) != len(set(unresolved))
                        or any(not isinstance(name, str) or name not in names for name in unresolved)):
                    raise ValueError("Invalid unverified Gamma writes")
            return data
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            raise ScreenEffectError("The local color-recovery record could not be read safely.") from error

    def _save_recovery(self, target, gamma_plan=None):
        path = self._recovery_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ScreenEffectError("The local color-recovery file is unsafe. Do not apply colors.")
        data = {
            "schema": 3,
            "original": list(self._original),
            "previous": list(self._last_applied) if self._last_applied is not None else None,
            "target": list(target),
            "gamma": self._gamma.recovery_data(gamma_plan),
            "matrix_unresolved": self._matrix_unresolved,
        }
        payload = json.dumps(data, separators=(",", ":"))
        if path.exists():
            stamp = path.stat()
            if (payload == self._journal_cache and
                    (stamp.st_size, stamp.st_mtime_ns) == self._journal_stamp):
                return  # Identical already-durable evidence needs no fsync.
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            stamp = path.stat()
            self._journal_cache = payload
            self._journal_stamp = (stamp.st_size, stamp.st_mtime_ns)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_recovery(self):
        path = self._recovery_path
        if path is not None and path.exists():
            if path.is_symlink() or not path.is_file():
                raise ScreenEffectError("The local color-recovery file is unsafe.")
            path.unlink()
        self._journal_cache = self._journal_stamp = None

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
        if (record.get("matrix_unresolved") or (record.get("gamma") or {}).get("unresolved")
                or any(_windows_matrices_match(current, item) for item in candidates)
                or self._gamma.matching_recovery(record.get("gamma"))):
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
            self._original = tuple(record["original"])
            self._last_applied = tuple(record["target"])
            self._matrix_unresolved = bool(record.get("matrix_unresolved"))
            self._matrix_intent = tuple(record["target"]) if self._matrix_unresolved else None
            gamma_loaded = False
            try:
                self._gamma.adopt_recovery(record.get("gamma"))
                gamma_loaded = True
            except (ScreenEffectError, OSError) as error:
                failures.append(error)
            pending_gamma = None if gamma_loaded else record.get("gamma")
            try:
                current = self._read_matrix()
                if _windows_matrices_match(current, self._original):
                    self._last_applied = None
                    self._matrix_unresolved, self._matrix_intent = False, None
                elif any(_windows_matrices_match(current, item) for item in candidates):
                    self._last_applied = current
                    self._write_matrix_tracked(self._original, pending_gamma)
                    self._last_applied = None
                elif self._matrix_unresolved:
                    raise ScreenEffectError("The prior matrix write is unverified. Recovery evidence was retained; another app's colors will not be overwritten.")
                else:
                    self._last_applied = None  # Definitely external; no write.
            except (ScreenEffectError, OSError) as error:
                failures.append(error)
            try:
                # The layers restore independently: a declined matrix restore
                # must not leave an otherwise recoverable Gamma ramp behind.
                if gamma_loaded:
                    self._gamma.restore(self._save_current_recovery)
            except (ScreenEffectError, OSError) as error:
                failures.append(error)
            if failures:
                try:
                    self._save_current_recovery(pending_gamma)
                except (ScreenEffectError, OSError):
                    pass  # The older write-intent record remains durable.
                raise ScreenEffectError("Windows could not fully recover previous colors. The recovery record was retained; close and reopen to retry.") from failures[0]
        else:
            # Declining after a failed recovery must not implicitly restore the
            # Gamma layer through disable(). Relinquish it without a write.
            self._gamma.originals, self._gamma.last, self._gamma.devices = {}, {}, {}
            self._gamma.unresolved.clear()
            self._gamma.intents.clear()
            self._last_applied = None
            self._matrix_unresolved, self._matrix_intent = False, None
        self._clear_recovery()
        self.disable()

    def verify_current(self, values):
        """Verify actual shared display state without reapplying any layer."""
        if not self._initialized or self._last_applied is None or self._matrix_unresolved:
            raise ScreenEffectError("The requested color preview is no longer active. Preview it again before applying.")
        current = self._read_matrix()
        if (not _windows_matrices_match(current, self._last_applied)
                or not _windows_matrices_match(current, color_matrix(values))):
            raise ScreenEffectError("Another app changed the desktop color effect. This app will not overwrite it.")
        self._gamma.verify_current(values.gamma)

    def apply(self, values):
        self.initialize()
        if self._matrix_unresolved:
            raise ScreenEffectError("A previous desktop color write could not be verified. Use Revert before adjusting colors.")
        current = self._read_matrix()
        expected = self._last_applied if self._last_applied is not None else self._original
        if not _windows_matrices_match(current, expected):
            raise ScreenEffectError(
                "Another app changed the desktop color effect. This app will not overwrite it."
            )
        target = color_matrix(values)
        # Verify Gamma support/topology before any display mutation, including
        # the matrix. A rejected Gamma request must not partially apply colors.
        plan = self._gamma.prepare(values.gamma)
        if plan is not None:
            self._save_recovery(target, plan)
        previous = self._last_applied
        matrix_attempted = False
        try:
            self._gamma.commit(plan, lambda: self._save_recovery(target, plan))
            # Save exact driver readback before changing the matrix, so a
            # terminated process can recognize its applied Gamma later.
            if not _windows_matrices_match(self._read_matrix(), expected):
                raise ScreenEffectError("Another app changed the desktop color effect during apply.")
            self._gamma.verify_plan(plan)
            matrix_attempted = True
            matrix_observed = self._write_matrix_tracked(target, plan)
            self._gamma.verify_plan(plan)
            self._gamma.accept(plan)
            self._save_recovery(matrix_observed)
        except Exception as error:
            rollback_failed = False
            try:
                current = self._read_matrix()
                if self._matrix_unresolved:
                    if (_windows_matrices_match(current, expected)
                            or _windows_matrices_match(current, self._matrix_intent)):
                        self._last_applied = current
                        self._matrix_unresolved, self._matrix_intent = False, None
                    else:
                        raise ScreenEffectError("A partial desktop write could not be verified. Its recovery evidence was retained.")
                if (matrix_attempted and self._last_applied is not None
                        and _windows_matrices_match(current, self._last_applied)
                        and not _windows_matrices_match(current, expected)):
                    self._write_matrix_tracked(expected)
                self._last_applied = previous
            except Exception:
                rollback_failed = True
            try:
                self._gamma.rollback(plan, record_progress=self._save_current_recovery)
            except Exception:
                rollback_failed = True
            if rollback_failed:
                try:
                    self._save_current_recovery()
                except Exception:
                    pass  # Never delete the pre-write evidence after failed IO.
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
        if self._last_applied is not None or self._matrix_unresolved:
            try:
                current = self._read_matrix()
                if _windows_matrices_match(current, self._original):
                    self._last_applied = None
                    self._matrix_unresolved, self._matrix_intent = False, None
                elif ((self._last_applied is not None and _windows_matrices_match(current, self._last_applied))
                      or (self._matrix_unresolved and _windows_matrices_match(current, self._matrix_intent))):
                    # Keep ownership when Windows refuses restoration so
                    # Disable can retry while the other layer is restored.
                    self._last_applied = current
                    self._matrix_unresolved, self._matrix_intent = False, None
                    self._write_matrix_tracked(self._original)
                    self._last_applied = None
                elif self._matrix_unresolved:
                    raise ScreenEffectError("The result of the desktop color write is unknown. Recovery evidence was retained; no unverified colors will be overwritten.")
                else:
                    restored = False
                    self._last_applied = None
            except Exception as error:
                failures.append(error)
        try:
            restored = self._gamma.restore(self._save_current_recovery) and restored
        except Exception as error:
            failures.append(error)
        if failures:
            # Preserve the record and retry state for either remaining layer.
            try:
                self._save_current_recovery()
            except Exception:
                pass
            raise ScreenEffectError("Windows could not fully restore colors. Try Revert again.") from failures[0]
        self._clear_recovery()
        self._last_applied = None
        self._matrix_unresolved, self._matrix_intent = False, None
        self._original = None
        if self._initialized:
            self._dll.MagUninitialize()
        self._initialized = False
        return restored
