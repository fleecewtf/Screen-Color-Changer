# Screen Color Changer

A compact, local color-filter utility for 64-bit Windows. This is a **first prototype**, not a published site download.

## What it does

- Uses a small 496 × 496 window with the same title bar, inset panel, inputs, buttons, and typography as the other Fleece tools.
- Includes Digital vibrance (0–300%, neutral 100%), Hue (−180–180°, neutral 0°), Brightness (−20–20%, neutral 0%), Contrast (50–200%, neutral 100%), and Gamma (0.50–2.00, neutral 1.00).
- Shows a synchronized numeric entry above each slider. Percentage and hue controls move in single units; gamma moves in exact 0.01 steps. Each slider has enough travel to reach every step, including 255% and gamma 1.03.
- Provides **Apply**, **Disable**, and **Reset**. Apply starts a 15-second preview; the prior colors return automatically unless you press **Keep colors**. Values are saved locally, but the filter is **not** applied automatically when the app starts.
- Uses Windows' full-screen Magnification color matrix for vibrance, hue, brightness, and contrast, independently of NVIDIA Control Panel. Digital vibrance is a saturation-based color-intensity control; its scale is not equivalent to NVIDIA's Digital Vibrance scale.
- Uses a separate nonlinear gamma curve on supported SDR displays. It composes the curve with the saved display calibration, verifies the driver's response, and restores prior gamma when its current ramp still matches this app's applied ramp. Non-neutral gamma is rejected when HDR/advanced color is active, the display mode cannot be verified, or the driver does not apply it. Gamma 1.00 leaves the existing calibration alone.
- Captures the previous color effect and restores it when disabled or closed **only if its own effect is still active**, to avoid overwriting another program's newer change.
- If a session ends unexpectedly, the next launch offers to restore the previous colors when the recorded effect still matches. The safeguard compares color-matrix values, so an external program applying an identical matrix cannot be distinguished.

The filter may not affect HDR content or games using independent-flip/exclusive display paths. It can conflict with Windows Magnifier or other programs changing the same Windows color effect or display calibration. This version applies to the desktop and does not select individual monitors. Gamma requires all active targets to be verified SDR; leave it at 1.00 if it is unavailable.

## Requirements

- 64-bit x64 or ARM64 Windows with the Magnification API available
- An internet connection during first setup or dependency repair
- A normal writable local folder (not a linked or protected system folder)

## Local prototype setup

1. Keep `Screen Color Changer.pyw`, `color_math.py`, `screen_backend.py`, `Installer.bat`, `LICENSE`, `READ ME.txt`, and both `requirements-win-*.txt` files together in one folder.
2. Double-click `Installer.bat`.
3. Press **Y** once to accept the Terms and bundled Tool License and approve setup.
4. Leave the setup window open until every check passes.
5. Double-click the `Screen Color Changer` shortcut created in the folder.

Keep the full folder path at 72 characters or fewer so Windows can install the private packages reliably. Setup installs official Python 3.14.7, pip, and PySide6-Essentials 6.11.2 into this folder only. It does not require administrator access, modify PATH, or use system Python. Downloaded runtimes and every PyPI wheel are checked against pinned SHA-256 hashes; a missing or modified **selected** dependency lock stops setup. The x64 or ARM64 lock is selected automatically. Keep both files if moving this folder between architectures.

Run `Installer.bat` again to repair private components or after moving the complete folder. It preserves locally saved values and recreates the shortcut for the folder's current location. Setup runs a source preflight for all three Python files before downloading app packages and runs a non-display-changing self-test before reporting success.

## Using it

Type the exact value you want in the box above a slider, use its arrow keys, or drag the slider. Gamma accepts two decimal places, so entering **1.03** selects exactly that setting. Press **Apply** to preview the effect. It reverts after 15 seconds unless you press **Keep colors**; changing values after that starts another preview when you press **Update**. **Disable** restores the captured prior effects when their current values still match this app's last-applied values, and **Reset** returns all five controls to their defaults. **Esc** disables the effect while the window has focus. Closing the app attempts the same safe restoration as Disable. The slider controls do not change the desktop until you press Apply or Update.

## Privacy and removal

The app does not require an account or send telemetry. Saved values stay in the extracted folder. Setup contacts official Python and PyPI hosts to download verified components; normal app use requires no network connection. `setup.log` can contain local folder paths, so review it before sharing.

To remove this prototype, first disable or close it, then delete the extracted folder. This removes its private runtime, dependencies, saved values, shortcut, and app files. It does not install a background service, add itself to startup, or create an uninstaller entry.

## Troubleshooting

If setup stops, the window immediately names the failed check and shows a **How to fix it** instruction. The same guidance is saved in `setup.log`. Keep all three source files and both dependency locks with `Installer.bat`; a partial copy is not installable. Correct the issue and run setup again. Success is reported only after dependencies, offline self-tests, and the shortcut pass.

If the filter does not appear in a particular game or HDR session, try a normal SDR desktop window first. If Windows Magnifier or another display filter is active, disable the conflicting feature before applying this one. Do not assume a game or protected video path will honor the Windows color matrix.

If Gamma is unavailable, return it to **1.00** to use the other four controls. Gamma depends on SDR mode and display-driver support; a driver may reject or silently ignore a requested curve. The app checks the applied ramp and reports that case instead of claiming success.

## License

Copyright 2026 Fleece. This project is source-available, not open source. The bundled [LICENSE](LICENSE) permits downloading, installing, and running an unmodified official release for lawful personal, non-commercial use. Modification, redistribution, sale, rebranding, and derivative versions remain prohibited. Third-party materials retain their own licenses.

This project was made with AI.
