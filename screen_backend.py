"""Careful wrapper for Windows' shared full-screen Magnification color effect.

No Magnification API is loaded or called until the user explicitly applies an
effect. Do not treat this shared Windows state as exclusive to this process.
"""

import ctypes
import json
import math
import os
import uuid
from pathlib import Path

from color_math import color_matrix, identity_matrix, matrices_match


Effect = ctypes.c_float * 25


class ScreenEffectError(RuntimeError):
    pass


class ScreenEffect:
    def __init__(self, dll=None, recovery_path=None):
        self._dll = dll
        self._initialized = False
        self._original = None
        self._last_applied = None
        self._recovery_path = Path(recovery_path) if recovery_path is not None else None

    @property
    def active(self) -> bool:
        return self._last_applied is not None

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
            if data.get("schema") != 1:
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
            return data
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ScreenEffectError("The local color-recovery record could not be read safely.") from error

    def _save_recovery(self, target):
        path = self._recovery_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ScreenEffectError("The local color-recovery file is unsafe. Do not apply colors.")
        data = {
            "schema": 1,
            "original": list(self._original),
            "previous": list(self._last_applied) if self._last_applied is not None else None,
            "target": list(target),
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
        if any(matrices_match(current, item) for item in candidates):
            return True
        self._clear_recovery()
        self.disable()
        return False

    def recover_previous(self, restore: bool):
        record = self._read_recovery()
        if record is None:
            return
        self.initialize()
        current = self._read_matrix()
        candidates = [record["target"]]
        if record["previous"] is not None:
            candidates.append(record["previous"])
        if restore and any(matrices_match(current, item) for item in candidates):
            self._write_matrix(tuple(record["original"]))
        self._clear_recovery()
        self.disable()

    def apply(self, values):
        self.initialize()
        current = self._read_matrix()
        expected = self._last_applied if self.active else self._original
        if not matrices_match(current, expected):
            raise ScreenEffectError(
                "Another app changed the desktop color effect. This app will not overwrite it."
            )
        target = color_matrix(values)
        self._save_recovery(target)
        self._write_matrix(target)
        self._last_applied = target

    def disable(self) -> bool:
        """Restore the captured effect only if it is still ours.

        Returns False when another app changed the shared effect after us;
        its newer state must be left untouched.
        """
        if not self._initialized:
            return True
        restored = True
        if self.active:
            current = self._read_matrix()
            if matrices_match(current, self._last_applied):
                # Keep ownership state intact when Windows refuses restoration,
                # so the user can retry Disable instead of losing the backup.
                self._write_matrix(self._original)
            else:
                restored = False
        self._clear_recovery()
        self._last_applied = None
        self._original = None
        self._dll.MagUninitialize()
        self._initialized = False
        return restored
