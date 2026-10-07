# Screen Color Changer

A compact, local color-filter utility for 64-bit Windows. Version **0.3.2** is prepared for its first fleece.wtf download; preparation does not mean it has been published.

## What it does

- Uses a small 496 × 496 window with the same title bar, inset panel, inputs, buttons, and typography as the other Fleece tools. On smaller logical work areas or high display scaling, it fits the available space and lets the controls scroll instead of putting buttons offscreen.
- Includes Digital vibrance (0–300%, neutral 100%), Hue (−180–180°, neutral 0°), Brightness (−20–20%, neutral 0%), Contrast (50–200%, neutral 100%), and Gamma (0.50–2.00, neutral 1.00).
- Shows a synchronized numeric entry above each slider. Percentage and hue controls move in single units; gamma moves in exact 0.01 steps. Each slider has enough travel to reach every step, including 255% and gamma 1.03.
- Moving a slider, scrolling its wheel, or committing a numeric entry **previews colors live**, without clicking Apply. Rapid edits are coalesced into at most one update every 75 ms; identical settings are not written again.
- Display operations run in one serialized background worker. A slow driver does not block the controls; newer preview edits replace pending preview work rather than building an unlimited queue. Applying, resetting, recovering, and closing still wait for the appropriate display operation to finish before reporting success.
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

Failed display writes are also read back: a driver can partly change colors even
when it reports failure. The app records that exact observed effect before trying
to restore it. Ownership checks compare the actual Windows float32 matrix and
exact gamma ramps, not merely similar values. If restoration fails, recovery
evidence remains and an unconfirmed effect gets an automatic retry timer.
Partial restoration writes are tracked too. An unresolved readback must retain
recovery evidence instead of being silently classified as another app's change.
Gamma revalidates each display's SDR status, identity, and captured mode before
writing it; changed or unverifiable targets are not overwritten.

The filter may not affect HDR content or games using independent-flip/exclusive display paths. It can conflict with Windows Magnifier or other programs changing the same Windows color effect or display calibration. This version applies to the desktop and does not select individual monitors. Gamma requires all active targets to be verified SDR; leave it at 1.00 if it is unavailable.

## Requirements

- 64-bit x64 or ARM64 Windows with the Magnification API available
- An internet connection during first setup or dependency repair
- A normal writable local folder (not a linked or protected system folder)

## Setup

1. Extract every ZIP file together into one folder, including `Screen Color Changer.pyw`, `color_math.py`, `screen_backend.py`, `Installer.bat`, `LICENSE`, `READ ME.txt`, `THIRD_PARTY_NOTICES.txt`, and both `requirements-win-*.txt` files.
2. Double-click `Installer.bat`.
3. Press **Y** once to accept the Terms and bundled Tool License and approve setup.
4. Leave the setup window open until every check passes.
5. Double-click the `Screen Color Changer` shortcut created in the folder.

Keep the full folder path at 72 characters or fewer so Windows can install the private packages reliably. Setup installs official Python 3.14.7, pip, and PySide6-Essentials 6.11.2 into this folder only. It does not require administrator access, modify PATH, or use system Python. Downloaded runtimes and every PyPI wheel are checked against pinned SHA-256 hashes; a missing or modified **selected** dependency lock stops setup. The x64 or ARM64 lock is selected automatically. Keep both files if moving this folder between architectures.

Spaces, carets (`^`), ampersands (`&`), parentheses, and exclamation marks (`!`)
are supported. Percent signs (`%`) in the folder path are rejected before any
download because Windows shortcut creation can expand them as environment
variables. Rename that folder or move the complete tool to a percent-free path.
Repair rejects linked/reparse-point private runtime trees before running their
Python executable, verifies its native architecture, and recovers interrupted
package transactions before reusing apparently healthy dependencies.
Pending runtime replacements are recovered before setup decides that another
Python download is needed. Setup and application startup share an exclusion
gate: close the running utility before repair, and wait for setup to finish
before opening its shortcut. This does not require elevation or add a service.

Run `Installer.bat` again to repair private components or after moving the complete folder. It preserves locally saved values and recreates the shortcut for the folder's current location. Setup runs a source preflight for all three Python files before downloading app packages and runs a non-display-changing self-test before reporting success.
Missing, changed, or unsafe selected wheel locks are also rejected before the first
component download, with immediate repair guidance.

## Using it

Drag a slider, use its arrow keys, or scroll over a slider or numeric box to preview the filter immediately. Type an exact value in a numeric box and press Enter or leave the box to commit the entry. Gamma accepts two decimal places, so **1.03** selects exactly that setting; the other controls move one unit at a time, including **255%** vibrance.

Click **Apply colors** when you like the result. This confirms the latest rendered values and saves them; simply adjusting controls never saves your edits. Unconfirmed previews revert after 15 seconds without an adjustment, returning to the last profile you applied in this session, or to the prior desktop colors if you have not applied one. Typing in an exact-value box counts as editing activity; its value is not previewed until you commit it.

Closing before Apply restores the prior desktop colors and exits. Closing after Apply keeps the applied profile in the tray, discarding later unsaved edits. Click the tray icon, choose **Open settings**, or launch the shortcut again to return to the same running utility. **Disable** or **Esc** turns the effect off; **Reset** immediately restores the prior colors, clears the confirmation, and saves all five neutral values. The tray's **Exit and restore original colors** command restores and quits entirely. Restoration remains ownership-aware; another program's newer effect or a changed display is left alone. No filter is automatically enabled at launch.

A second launch uses a same-user, same-session local named pipe to request only
reopening the existing window. It does not start a second color filter, transmit
settings, accept file paths or commands, or use the internet.

## Privacy and removal

The app does not require an account or send telemetry. Saved values stay in the extracted folder. Setup contacts official Python and PyPI hosts to download verified components; normal app use requires no network connection. `setup.log` can contain local folder paths, so review it before sharing.

To remove this tool, choose **Exit and restore original colors** in its tray menu (or Disable and then close it), then delete the extracted folder. This removes its private runtime, dependencies, saved values, shortcut, and app files. Applied tray mode is the same running utility, not an installed background service. It does not add itself to startup or create an uninstaller entry.

## Troubleshooting

If setup stops, the window immediately names the failed check and shows a **How to fix it** instruction. The same guidance is saved in `setup.log`. Keep all three source files and both dependency locks with `Installer.bat`; a partial copy is not installable. Correct the issue and run setup again. Success is reported only after dependencies, offline self-tests, and the shortcut pass.

If the filter does not appear in a particular game or HDR session, try a normal SDR desktop window first. If Windows Magnifier or another display filter is active, disable the conflicting feature before applying this one. Do not assume a game or protected video path will honor the Windows color matrix.

If Gamma is unavailable, return it to **1.00** to use the other four controls. Gamma depends on SDR mode and display-driver support; a driver may reject or silently ignore a requested curve. The app checks the applied ramp and reports that case instead of claiming success.

## Local verification

Release and setup-check PowerShell scripts support Windows PowerShell 5.1 or
newer; no PowerShell 7 installation is required. The release builder still
requires a clean Git tree and does not package an uncommitted working copy.

The Python tests inject fake display APIs and isolated settings/recovery paths.
Run them with the folder-private runtime and bytecode writing disabled. The
`scripts/Test-AuditPerformance.py` benchmark uses the same safe fakes, real
temporary journal IO, and an offscreen Qt window. It measures latest-value
coalescing and UI responsiveness with intentionally slow fake drivers; it does
not apply a real color filter or prove native GPU/HDR/ARM64 compatibility.

Installer preflight/lifecycle scripts keep their disposable fixtures for
diagnosis. Their default cases do not reinstall the working utility. Explicit
full-setup checks should only target disposable test folders.

## License

Copyright 2026 Fleece. This project is source-available, not open source. The bundled [LICENSE](LICENSE) permits downloading, installing, and running an unmodified official release for lawful personal, non-commercial use. Modification, redistribution, sale, rebranding, and derivative versions remain prohibited. Third-party materials retain their own licenses.

The bundled [third-party notices](THIRD_PARTY_NOTICES.txt) identify the official downloaded runtimes, package versions, upstream sources, and license terms. Their rights are not restricted by the Fleece license. The small ZIP deliberately excludes runtimes, user settings, recovery records, logs, shortcuts, caches and developer tests.

This project was made with AI.
