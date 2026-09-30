# Screen Color Changer

A compact, local color-filter utility for 64-bit Windows. Version **0.3.1** is a local release candidate, not a published site download.

## What it does

- Uses a small 496 × 496 window with the same title bar, inset panel, inputs, buttons, and typography as the other Fleece tools.
- Includes Digital vibrance (0–300%, neutral 100%), Hue (−180–180°, neutral 0°), Brightness (−20–20%, neutral 0%), Contrast (50–200%, neutral 100%), and Gamma (0.50–2.00, neutral 1.00).
- Shows a synchronized numeric entry above each slider. Percentage and hue controls move in single units; gamma moves in exact 0.01 steps. Each slider has enough travel to reach every step, including 255% and gamma 1.03.
- Moving a slider, scrolling its wheel, or committing a numeric entry **previews colors live**, without clicking Apply. Rapid edits are coalesced into at most one update every 75 ms; identical settings are not written again.
- **Apply colors** confirms the current preview and saves only those exact values locally. An unconfirmed preview reverts after 15 seconds of inactivity; continuing to adjust a control refreshes that safety timer. Closing without ever applying restores the prior desktop colors and discards unsaved edits.
  Explicit Apply and retaining an applied profile in the tray recheck the actual
  Windows matrix and tracked gamma, even if controls have not changed. Another
  program's newer effect is not silently treated as this app's successful preview.
- Once you apply a profile, closing the window discards any later unconfirmed edits and keeps only the applied profile running in the system tray. Use its icon to reopen settings. **Exit and restore original colors** fully exits; **Disable**, **Reset**, or Esc restores the prior desktop colors when this app still owns the effect. Reset also immediately clears the confirmed profile and saves neutral values. If no system tray is available, closing always restores and exits.
- Uses Windows' full-screen Magnification color matrix for vibrance, hue, brightness, and contrast, independently of NVIDIA Control Panel. Digital vibrance is a saturation-based color-intensity control; its scale is not equivalent to NVIDIA's Digital Vibrance scale.
- Uses a separate nonlinear gamma curve on supported SDR displays. It composes the curve with the saved display calibration, verifies the driver's response, and restores prior gamma when its current ramp still matches this app's applied ramp. Non-neutral gamma is rejected when HDR/advanced color is active, the display mode cannot be verified, or the driver does not apply it. Gamma 1.00 leaves the existing calibration alone.
- Captures the previous color effect and restores it when disabled, reset, or fully exited **only if its own effect is still active**, to avoid overwriting another program's newer change. Applied settings do not auto-apply on launch or restart; tray mode lasts only for the running session.
- If a session ends unexpectedly, the next launch offers to restore the previous colors when the recorded effect still matches. The safeguard compares color-matrix values, so an external program applying an identical matrix cannot be distinguished.
  Recovery verifies the restored matrix and gamma before clearing its record. Failed
  restoration keeps recovery evidence for a later retry instead of claiming success.

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
Missing, changed, or unsafe selected wheel locks are also rejected before the first
component download, with immediate repair guidance.

## Using it

Drag a slider, use its arrow keys, or scroll over a slider or numeric box to preview the filter immediately. Type an exact value in a numeric box and press Enter or leave the box to commit the entry. Gamma accepts two decimal places, so **1.03** selects exactly that setting; the other controls move one unit at a time, including **255%** vibrance.

Click **Apply colors** when you like the result. This confirms the latest rendered values and saves them; simply adjusting controls never saves your edits. Unconfirmed previews revert after 15 seconds without an adjustment, returning to the last profile you applied in this session, or to the prior desktop colors if you have not applied one.

Closing before Apply restores the prior desktop colors and exits. Closing after Apply keeps the applied profile in the tray, discarding later unsaved edits. Click the tray icon, choose **Open settings**, or launch the shortcut again to return to the same running utility. **Disable** or **Esc** turns the effect off; **Reset** immediately restores the prior colors, clears the confirmation, and saves all five neutral values. The tray's **Exit and restore original colors** command restores and quits entirely. Restoration remains ownership-aware; another program's newer effect or a changed display is left alone. No filter is automatically enabled at launch.

A second launch uses a same-user, same-session local named pipe to request only
reopening the existing window. It does not start a second color filter, transmit
settings, accept file paths or commands, or use the internet.

## Privacy and removal

The app does not require an account or send telemetry. Saved values stay in the extracted folder. Setup contacts official Python and PyPI hosts to download verified components; normal app use requires no network connection. `setup.log` can contain local folder paths, so review it before sharing.

To remove this prototype, choose **Exit and restore original colors** in its tray menu (or Disable and then close it), then delete the extracted folder. This removes its private runtime, dependencies, saved values, shortcut, and app files. Applied tray mode is the same running utility, not an installed background service. It does not add itself to startup or create an uninstaller entry.

## Troubleshooting

If setup stops, the window immediately names the failed check and shows a **How to fix it** instruction. The same guidance is saved in `setup.log`. Keep all three source files and both dependency locks with `Installer.bat`; a partial copy is not installable. Correct the issue and run setup again. Success is reported only after dependencies, offline self-tests, and the shortcut pass.

If the filter does not appear in a particular game or HDR session, try a normal SDR desktop window first. If Windows Magnifier or another display filter is active, disable the conflicting feature before applying this one. Do not assume a game or protected video path will honor the Windows color matrix.

If Gamma is unavailable, return it to **1.00** to use the other four controls. Gamma depends on SDR mode and display-driver support; a driver may reject or silently ignore a requested curve. The app checks the applied ramp and reports that case instead of claiming success.

## License

Copyright 2026 Fleece. This project is source-available, not open source. The bundled [LICENSE](LICENSE) permits downloading, installing, and running an unmodified official release for lawful personal, non-commercial use. Modification, redistribution, sale, rebranding, and derivative versions remain prohibited. Third-party materials retain their own licenses.

This project was made with AI.
