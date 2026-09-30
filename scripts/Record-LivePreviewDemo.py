"""Record an explicitly simulated UI demo without touching Windows display APIs.

The actual Qt app frame is rendered offscreen. A fake backend records successful
app calls, and reference swatches illustrate the real matrix and gamma-LUT math.
This is not a desktop/screen recording or proof of hardware color behavior.
Settings writes are confined to a temporary INI file and removed afterwards.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["QT_FONT_DPI"] = "96"

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT.parent
sys.path.insert(0, str(ROOT))

from color_math import ColorValues, color_matrix, gamma_ramp
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider


WIDTH, HEIGHT, FPS, DURATION = 560, 850, 15, 18
APP_X, APP_Y = 32, 118
REFERENCE = (
    (177, 91, 96), (92, 159, 118), (86, 126, 183),
    (183, 152, 92), (139, 97, 163), (139, 139, 139),
)
BASE_RAMP = tuple(index * 257 for _channel in range(3) for index in range(256))


class FakeEffect:
    """Never constructs ScreenEffect or calls a real display/account API."""

    def __init__(self):
        self.active = False
        self.replaces_existing_effect = False
        self.applied = ColorValues()
        self.calls = 0
        self.disabled = 0

    def initialize(self):
        return None

    def apply(self, values):
        assert isinstance(values, ColorValues)
        self.applied = values
        self.active = True
        self.calls += 1

    def disable(self):
        self.active = False
        self.applied = ColorValues()
        self.disabled += 1
        return True

    def recovery_needed(self):
        return False

    def recover_previous(self, _restore):
        return True


class FakeTray:
    """Emulate only lifecycle state; no real notification-area icon is made."""

    def __init__(self):
        self.visible = False

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def load_app():
    path = ROOT / "Screen Color Changer.pyw"
    loader = importlib.machinery.SourceFileLoader("color_demo_app", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def rgb_preview(rgb, values):
    """Illustrate the app's affine matrix, then its real sampled gamma LUT.

    Windows compositor/hardware behavior can differ; the video labels these
    synthetic patches as simulated output, not a monitor/screen capture.
    """
    matrix = color_matrix(values)
    ramp = gamma_ramp(BASE_RAMP, values.gamma)
    channels = []
    for output in range(3):
        transformed = sum(rgb[source] / 255.0 * matrix[source * 5 + output]
                          for source in range(3)) + matrix[4 * 5 + output]
        position = min(1.0, max(0.0, transformed)) * 255.0
        lower = int(position)
        upper = min(255, lower + 1)
        fraction = position - lower
        value = ramp[output * 256 + lower] * (1 - fraction) + ramp[output * 256 + upper] * fraction
        channels.append(round(value / 257.0))
    return QColor(*channels)


def draw_text(painter, x, y, width, height, text, size=13, color="#b7bec9", bold=False):
    painter.setPen(QColor(color))
    font = QFont("Segoe UI", size)
    font.setWeight(QFont.DemiBold if bold else QFont.Normal)
    painter.setFont(font)
    painter.drawText(QRect(x, y, width, height), Qt.AlignLeft | Qt.AlignVCenter, text)


def handle_point(control, window):
    slider = control.slider
    option = QStyleOptionSlider()
    option.initFrom(slider)
    option.orientation = Qt.Horizontal
    option.minimum = slider.minimum()
    option.maximum = slider.maximum()
    option.sliderPosition = slider.value()
    option.sliderValue = slider.value()
    rectangle = slider.style().subControlRect(
        QStyle.CC_Slider, option, QStyle.SC_SliderHandle, slider)
    return slider.mapTo(window, rectangle.center()) + QPoint(APP_X, APP_Y)


def compose_frame(window, fake, title, note, detail, active_control=None, click_apply=False):
    image = QImage(WIDTH, HEIGHT, QImage.Format_RGB888)
    image.fill(QColor("#0d121b"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    draw_text(painter, 32, 20, 496, 36, "Screen Color Changer", 23, "#f4f6fa", True)
    draw_text(painter, 32, 56, 496, 26, "UI demo · simulated display", 13, "#91dab5", True)
    draw_text(painter, 32, 84, 496, 26, title, 14, "#c8d0dc")

    window.render(painter, QPoint(APP_X, APP_Y))
    if active_control is not None:
        point = handle_point(active_control, window)
        painter.setBrush(QColor(145, 218, 181, 32))
        painter.setPen(QPen(QColor("#91dab5"), 2))
        painter.drawEllipse(point, 13, 13)
        painter.setBrush(QColor("#91dab5"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(point + QPoint(0, 23), 3, 3)
    if click_apply:
        rect = window.apply_button.rect()
        rect.moveTopLeft(window.apply_button.mapTo(window, QPoint()) + QPoint(APP_X, APP_Y))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#91dab5"), 3))
        painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 11, 11)

    painter.setPen(QPen(QColor("#2a3444"), 1))
    painter.setBrush(QColor("#151d29"))
    painter.drawRoundedRect(QRect(32, 633, 496, 145), 12, 12)
    draw_text(painter, 46, 643, 218, 24, "Original reference", 11, "#aab5c5", True)
    draw_text(painter, 296, 643, 218, 24, "Current output (simulated)", 11, "#aab5c5", True)
    values = fake.applied if fake.active else ColorValues()
    for index, original in enumerate(REFERENCE):
        column, row = index % 3, index // 3
        y = 677 + row * 42
        for x, color in ((46 + column * 71, QColor(*original)),
                         (296 + column * 71, rgb_preview(original, values))):
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRect(x, y, 64, 34), 5, 5)

    draw_text(painter, 32, 786, 496, 22, note, 11, "#f0f3f8", True)
    draw_text(painter, 32, 808, 496, 18, detail, 10, "#a4afbf")
    draw_text(painter, 32, 827, 496, 17,
              "Real app UI + color math. Not a desktop or hardware recording.",
              9, "#8793a5")
    painter.end()
    return image


def render_video(ffmpeg, output):
    app = QApplication.instance() or QApplication(["Screen Color Changer simulated demo"])
    app.setQuitOnLastWindowClosed(False)
    # The offscreen platform does not discover Windows system fonts itself.
    # Register local installed faces explicitly, avoiding tofu/missing glyphs.
    font_directory = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
        font_file = font_directory / filename
        if not font_file.is_file() or QFontDatabase.addApplicationFont(str(font_file)) < 0:
            raise RuntimeError(f"Could not load the installed demo font: {filename}")
    if not QFontMetrics(QFont("Segoe UI", 13)).inFontUcs4(ord("A")):
        raise RuntimeError("Offscreen demo font is missing Latin glyphs")
    module = load_app()
    fake = FakeEffect()
    # Testing mode suppresses recovery, real settings, timers and tray setup.
    # After constructing the real UI, only our injected fake backend is allowed.
    window = module.ColorWindow(testing=True, effect=fake)
    assert window.effect is fake
    window.show()
    app.processEvents()
    assert window.width() == 496 and window.height() == 496

    mp4 = output / "screen-color-changer-live-preview-simulated-demo.mp4"
    gif = output / "screen-color-changer-live-preview-simulated-demo.gif"
    poster = output / "screen-color-changer-live-preview-simulated-poster.png"
    args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
            "-r", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(mp4)]

    with tempfile.TemporaryDirectory(prefix="fleece-color-demo-") as temporary:
        settings_path = Path(temporary) / "demo-only-settings.ini"
        window._settings = QSettings(str(settings_path), QSettings.IniFormat)
        window.testing = False
        window._pending_recovery = False
        window._tray = FakeTray()
        confirmed = None
        last_ticks = 0
        applied = reset = rolled_back = False
        encoder = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for frame in range(DURATION * FPS):
                time = frame / FPS
                active_control = None
                click_apply = False
                title = "Drag a slider to preview immediately"
                note = "Preview changes are temporary. Only Apply saves."
                detail = "Demo uses isolated temporary settings and no display APIs."

                if 1.2 <= time < 4.4:
                    value = round(100 + (253 - 100) * (time - 1.2) / 3.2)
                    window.saturation.slider.setValue(value)
                    active_control = window.saturation
                    title = "Dragging Digital vibrance · live preview"
                elif 4.4 <= time < 5.4:
                    value = 253 if time < 4.73 else 254 if time < 5.07 else 255
                    window.saturation.set_value(value)
                    active_control = window.saturation
                    title = f"Exact one-point changes · {value}% vibrance"
                    detail = "253 → 254 → 255: no skipped integer values."
                elif 5.4 <= time < 7:
                    window.hue.slider.setValue(round(35 * (time - 5.4) / 1.6))
                    active_control = window.hue
                    title = "Hue changes preview while dragging"
                elif 7 <= time < 7.8:
                    window.hue.set_value(35)
                    window.gamma.slider.setValue(round(100 + 18 * (time - 7) / 0.8))
                    active_control = window.gamma
                    title = "Gamma also previews · fine 0.01 steps"
                elif 7.8 <= time < 9.8:
                    if not applied:
                        window.gamma.set_value(118)
                        window._apply_clicked()
                        confirmed = window.values()
                        assert confirmed == ColorValues(saturation=255, hue=35, gamma=118)
                        assert int(window._settings.value("saturation")) == 255
                        assert int(window._settings.value("gamma")) == 118
                        assert window._preview_remaining is None
                        applied = True
                    title = "Apply confirms and saves this preview"
                    note = "Saved: vibrance 255% · hue 35° · gamma 1.18"
                    detail = "Actual Apply handler; written to demo-only temporary INI."
                    click_apply = time < 8.5
                elif 9.8 <= time < 11.5:
                    window.contrast.slider.setValue(round(100 + 25 * (time - 9.8) / 1.7))
                    active_control = window.contrast
                    title = "Try a new contrast preview after Apply"
                    note = "New preview is not saved. Applied settings stay intact."
                    detail = "Saved contrast remains 100%; current preview rises."
                elif 11.5 <= time < 12.7:
                    window.contrast.set_value(125)
                    title = "Unapplied preview · saved contrast is still 100%"
                    note = "A later preview does not overwrite your saved values."
                    detail = "Current contrast: 125%. Last applied contrast: 100%."
                elif 12.7 <= time < 14.7:
                    title = "15-second idle timeout · sped up for this demo"
                    note = "Idle countdown below runs faster than real time."
                    detail = "The actual timeout handler restores the last applied values."
                    ticks = min(15, int((time - 12.7) / 2.0 * 15) + 1)
                    while last_ticks < ticks:
                        window._preview_tick()
                        last_ticks += 1
                elif 14.7 <= time < 15.6:
                    if not rolled_back:
                        while last_ticks < 15:
                            window._preview_tick()
                            last_ticks += 1
                        assert window.values() == confirmed
                        assert fake.applied == confirmed
                        assert window._preview_remaining is None
                        rolled_back = True
                    title = "Idle preview reverted to the last applied values"
                    note = "Restored: vibrance 255% · hue 35° · gamma 1.18"
                    detail = "Saved settings did not change during the temporary preview."
                elif time >= 15.6:
                    if not reset:
                        window._reset_values()
                        reset = True
                    title = "Reset immediately restores original colors"
                    note = "Reset also clears the applied filter and saves neutral values."
                    detail = "All five controls are neutral; the simulated effect is off."

                # Deterministic frames use the real render handler, without real
                # time, QTest sleeps, screen capture, or Windows display writes.
                if window._live_timer.isActive():
                    assert window._render_live_preview()
                window._live_timer.stop()
                window._preview_timer.stop()
                app.processEvents()
                if not applied:
                    assert window._settings.value("saturation") is None
                if applied and not reset:
                    assert int(window._settings.value("contrast")) == 100
                    assert int(window._settings.value("saturation")) == 255

                image = compose_frame(window, fake, title, note, detail, active_control, click_apply)
                if frame == round(8.8 * FPS):
                    assert image.save(str(poster))
                encoder.stdin.write(bytes(image.constBits()))

            assert applied and rolled_back and reset
            assert window.values() == ColorValues()
            assert fake.applied == ColorValues()
            assert not fake.active and not window._tray.visible
            assert int(window._settings.value("gamma")) == 100
            encoder.stdin.close()
            error = encoder.stderr.read().decode("utf-8", errors="replace")
            if encoder.wait() != 0:
                raise RuntimeError(f"MP4 encoding failed: {error}")
        except BaseException:
            encoder.kill()
            encoder.wait()
            raise
        finally:
            window._exit_requested = True
            window.close()
            app.processEvents()
            assert not fake.active
            window._settings = None

    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
                    "-filter_complex",
                    "[0:v]fps=12,scale=448:-1:flags=lanczos,split[a][b];"
                    "[a]palettegen=max_colors=128:stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
                    "-loop", "0", str(gif)], check=True)

    ffprobe = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    metadata = {"mode": "offscreen real Qt UI; fake display backend; illustrative swatches",
                "display_api_calls": 0, "duration_seconds": DURATION, "frames": DURATION * FPS,
                "portrait_size": [WIDTH, HEIGHT], "fake_effect_apply_calls": fake.calls,
                "files": [{"path": str(path), "bytes": path.stat().st_size}
                          for path in (mp4, gif, poster)]}
    if ffprobe.exists():
        result = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_name,pix_fmt,width,height,nb_frames,r_frame_rate:format=duration,size",
                                 "-of", "json", str(mp4)], capture_output=True, text=True, check=True)
        metadata["mp4_probe"] = json.loads(result.stdout)
    print(json.dumps(metadata, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=SITE / ".audit" / "color-preview-2026-09-30")
    parser.add_argument("--ffmpeg", type=Path,
                        default=SITE / "Video-Audio-Downloader" / ".runtime" / "ffmpeg" / "ffmpeg.exe")
    args = parser.parse_args()
    if not args.ffmpeg.is_file():
        parser.error("Use an existing local FFmpeg executable; this script downloads nothing.")
    args.output.mkdir(parents=True, exist_ok=True)
    render_video(args.ffmpeg.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
