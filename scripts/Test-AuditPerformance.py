"""Bounded, display-safe benchmarks for the local Screen Color Changer candidate.

Run with the folder-private Python, using -B. No real display API is called and
no production preferences/recovery files are opened. Journal benchmarks use
temporary directories; Qt tests run offscreen with an injected fake effect.
Results are observations, not hardware-independent performance guarantees.
"""

import importlib.machinery
import importlib.util
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from color_math import ColorValues, color_matrix, gamma_ramp
from screen_backend import ScreenEffect
from test_color_effect import FakeGammaApi, FakeMagnification
from test_live_preview_ui import FakeEffect
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication


def describe(samples):
    samples = sorted(samples)
    return {"samples": len(samples), "median_ms": round(statistics.median(samples), 4),
            "p95_ms": round(samples[max(0, int(len(samples) * .95) - 1)], 4),
            "max_ms": round(max(samples), 4)}


def pump(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


class SlowFakeEffect(FakeEffect):
    """An intentionally slow fake driver; never constructs native APIs."""
    def __init__(self, delay):
        super().__init__()
        self.delay = delay
        self.worker_threads = set()
        self.renders = 0

    def apply(self, values):
        self.worker_threads.add(threading.get_ident())
        self.renders += 1
        time.sleep(self.delay)
        super().apply(values)


def main():
    values = [ColorValues(saturation=50 + (i * 37) % 251, hue=-180 + (i * 23) % 361,
                          brightness=-20 + i % 41, contrast=50 + i % 151, gamma=50 + i % 151)
              for i in range(1000)]
    baseline = tuple(i * 257 for _ in range(3) for i in range(256))
    report = {"scope": "fake display APIs; real temporary journal IO; offscreen Qt"}
    for name, operation in (("color_matrix", lambda value: color_matrix(value)),
                            ("gamma_ramp_one_display", lambda value: gamma_ramp(baseline, value.gamma))):
        samples = []
        for value in values:
            start = time.perf_counter()
            operation(value)
            samples.append((time.perf_counter() - start) * 1000)
        report[name] = describe(samples)

    report["journal_apply"] = {}
    for display_count in (1, 2, 4):
        for change_gamma in (False, True):
            with tempfile.TemporaryDirectory(prefix="scc-benchmark-") as temporary:
                effect = ScreenEffect(FakeMagnification(), gamma_api=FakeGammaApi(display_count),
                                      recovery_path=Path(temporary) / "recovery.json")
                samples, syncs = [], [0]
                original_sync = os.fsync

                def counted_sync(fd):
                    syncs[0] += 1
                    return original_sync(fd)

                with patch("screen_backend.os.fsync", side_effect=counted_sync):
                    for value in values[:40]:
                        if not change_gamma:
                            value = ColorValues(saturation=value.saturation, hue=value.hue,
                                                brightness=value.brightness, contrast=value.contrast)
                        start = time.perf_counter()
                        effect.apply(value)
                        samples.append((time.perf_counter() - start) * 1000)
                effect.disable()
                report["journal_apply"][f"{display_count}_displays_gamma_{change_gamma}"] = {
                    **describe(samples), "fsync_calls": syncs[0]}

    app = QApplication.instance() or QApplication(["safe-performance-test"])
    app.setQuitOnLastWindowClosed(False)
    loader = importlib.machinery.SourceFileLoader("scc_performance_ui", str(ROOT / "Screen Color Changer.pyw"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    ui = importlib.util.module_from_spec(spec)
    loader.exec_module(ui)
    report["background_preview"] = {}
    for delay in (0, .2, .4):
        effect = SlowFakeEffect(delay)
        window = ui.ColorWindow(testing=True, effect=effect, async_operations=True)
        window.testing = False
        window.show()
        app.processEvents()
        heartbeats, edits = [], [0]
        pulse, source = QTimer(), QTimer()
        pulse.setTimerType(Qt.PreciseTimer)
        pulse.setInterval(10)
        pulse.timeout.connect(lambda: heartbeats.append(time.perf_counter()))
        source.setTimerType(Qt.PreciseTimer)
        source.setInterval(5)

        def edit():
            edits[0] += 1
            window.saturation.set_value(100 + edits[0] % 190)

        source.timeout.connect(edit)
        try:
            pulse.start()
            source.start()
            pump(2200)
            source.stop()
            deadline = time.monotonic() + 3
            while (window._active_request is not None or window._queued_request is not None
                   or window._live_timer.isActive()) and time.monotonic() < deadline:
                pump(20)
            pulse.stop()
            gaps = [(right - left) * 1000 for left, right in zip(heartbeats, heartbeats[1:])]
            matched = effect.applied is not None and effect.applied == window.values()
            report["background_preview"][f"synthetic_{int(delay * 1000)}ms_driver"] = {
                "edits": edits[0], "renders": effect.renders, "heartbeat": describe(gaps),
                "latest_value_rendered": matched, "worker_thread_count": len(effect.worker_threads),
                "uses_ui_thread": threading.get_ident() in effect.worker_threads}
            if not matched or len(effect.worker_threads) != 1 or threading.get_ident() in effect.worker_threads:
                raise AssertionError("Latest-value/serialized-worker contract failed")
        finally:
            source.stop()
            pulse.stop()
            window._shutdown_worker(wait=True)
            window.testing = True
            window._close_ready = True
            window.close()
            window.deleteLater()
            app.processEvents()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
