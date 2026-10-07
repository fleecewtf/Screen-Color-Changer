<div align="center">

# screen color changer

Current release: **v1.0.0**. Includes exact live controls, ownership-aware recovery, bounded offline regression/performance checks, and shared setup hardening.

All Fleece desktop tools use the same installation workflow: download the official ZIP, extract the entire folder, run `Installer.bat`, accept the bundled Terms/Tool License, wait for final checks, then open the folder-local shortcut. Setup installs a private runtime without changing system Python or requiring administrator access. Rerun it to repair or refresh a moved shortcut. Keep the full path at most 72 characters, without percent signs. Architecture support and extra components vary by tool; File Converter remains x64-only.

A little tool I made with AI to fine-tune desktop colors locally on 64-bit Windows.

<img src="Screen%20Color%20Changer.png" alt="Screen Color Changer app window" width="760">

</div>

## features

- Fine-tune Digital vibrance, Hue, Brightness, Contrast, and Gamma
- Preview colors live when dragging or scrolling, without clicking Apply each time
- Type an exact value above each slider or adjust one step with the arrow keys
- Reach every supported slider value, including 255% vibrance and Gamma 1.03
- Confirm and save only the values you choose with **Apply colors**
- Revert unconfirmed previews after 15 seconds without editing activity
- Keep an applied profile in the tray, or restore the prior colors and fully exit
- Use a compact 496 × 496 window that fits and scrolls on smaller logical work areas
- Run display work in one background worker with bounded latest-value preview coalescing
- Check exact shared display-state ownership and retain unresolved recovery evidence

## requirements

- 64-bit x64 or ARM64 Windows with the full-screen Magnification API available
- An internet connection during first setup or dependency repair
- A normal writable local folder, not a linked or protected system folder
- Verified SDR mode on every active display and supported drivers for non-neutral Gamma
- No internet connection while adjusting colors in the installed app

Gamma **1.00** leaves existing calibration alone and lets you use the other four controls without loading the Gamma API. Compatibility with every GPU, monitor, HDR session, application, or game is not guaranteed.

## installation

1. Download the latest release ZIP.
2. Extract the complete folder.
3. Double-click `Installer.bat`.
4. Press **Y** once to accept the Terms and bundled Tool License and approve setup.
5. Leave the setup window open until every check passes.
6. Double-click the `Screen Color Changer` shortcut created in the folder.

Keep the full extracted folder path at 72 characters or fewer so Windows can install the private packages reliably. Keep `Screen Color Changer.pyw`, `color_math.py`, `screen_backend.py`, `Installer.bat`, `LICENSE`, `READ ME.txt`, `THIRD_PARTY_NOTICES.txt`, and both `requirements-win-*.txt` files together.

Setup keeps the private Python runtime, dependencies, settings, and every app component inside the extracted folder. It does not require administrator access, change PATH, or install global Python packages. The generated folder-local shortcut starts the app directly with that private runtime, so Microsoft Store or system Python is not required.

Setup pins and verifies official Python **3.14.7**, pip **26.2.1**, PySide6-Essentials **6.11.2**, and Shiboken **6.11.2**. It uses the downloaded pip bootstrap to install the private dependencies. Downloaded runtime archives and the complete PyPI wheel dependency set are checked against pinned SHA-256 hashes before use. Setup automatically selects the bundled x64 or ARM64 requirements file; keep both files if moving between architectures. Missing, changed, or unsafe selected locks stop setup before the first component download.

Spaces, carets (`^`), ampersands (`&`), parentheses, and exclamation marks (`!`) are supported. Percent signs (`%`) in the folder path are rejected before downloading because Windows shortcut creation can expand them as environment variables. Move or rename the complete folder to a percent-free path.

Run `Installer.bat` again to repair the private components or after moving the complete folder. Setup preserves saved values and recreates the shortcut for the folder's current location. It rejects linked/reparse-point private runtime trees, verifies the runtime architecture, and recovers interrupted runtime/package transactions before reuse or an unnecessary new download. App startup and setup share an exclusion gate: close the utility before repair and wait for setup to finish before opening its shortcut.

Setup checks the source and Windows shortcut support before large downloads, compiles all three Python sources before app-package downloads, and runs an offline self-test without changing desktop colors before reporting success.

## usage

1. Drag or scroll a slider, use its arrow keys, or type an exact number above it.
2. Press Enter or leave a numeric box to commit typed values and preview them.
3. Click **Apply colors** when you want to confirm and save the latest result.
4. Use **Disable**, **Reset**, or **Esc** while the app has focus to restore prior colors.
5. Use the tray's **Exit and restore original colors** command to restore and quit completely.

| Control | Range | Neutral | Exact step |
| --- | --- | --- | --- |
| Digital vibrance | 0–300% | 100% | 1% |
| Hue | −180–180° | 0° | 1° |
| Brightness | −20–20% | 0% | 1% |
| Contrast | 50–200% | 100% | 1% |
| Gamma | 0.50–2.00 | 1.00 | 0.01 |

Moving or scrolling a control previews the filter live. Typing counts as editing activity, but incomplete numbers are not applied until committed. Rapid edits are coalesced into at most one preview update every 75 ms; identical values are not written again. A single background worker serializes display operations, and newer pending preview values replace older ones rather than building an unlimited queue. Apply, Reset, recovery, and close wait for the corresponding display operation before reporting completion.

Only **Apply colors** saves values. An unconfirmed preview reverts after 15 seconds without an adjustment, returning to the last profile applied in the current session or to the prior desktop colors if none was applied. Continuing to edit refreshes this safety timer. Apply and retaining a profile in the tray recheck the actual Windows matrix and tracked Gamma; another program's newer effect is not silently reported as this app's successful preview.

Closing before Apply restores prior colors and exits. After Apply, close discards later unsaved edits and keeps only the confirmed profile running in the tray when a tray is available. Click the tray icon, choose **Open settings**, or launch the shortcut again to return to the same utility. If no tray is available, close restores and exits. **Reset** also clears the confirmed profile and saves all five neutral values. Saved values do not automatically apply on launch or restart; tray mode lasts only for the running session.

A second launch uses a same-user, same-session local named pipe only to reopen the existing window. It does not start a second filter, transmit settings, accept file paths or commands, or use the internet.

### display and restoration limits

Vibrance, hue, brightness, and contrast use Windows' shared full-screen Magnification color matrix independently of NVIDIA Control Panel. Digital vibrance boosts saturation; its scale is not equivalent to NVIDIA's Digital Vibrance scale. This release affects the desktop and does not independently select monitors.

Gamma uses a separate nonlinear curve composed with the saved display calibration. It requires verified SDR on every active target and supported display drivers. The app checks the driver's response and rejects ignored or partially accepted curves instead of claiming success. Each target's SDR status, identity, and captured mode are revalidated before Gamma writes. Leave Gamma at **1.00** when it is unavailable.

HDR content, protected video, and games using exclusive or independent-flip display paths can bypass desktop effects. Windows Magnifier and other programs changing the same color matrix or calibration can conflict with the utility. Do not assume every game or video honors these filters.

Restoration is ownership-aware: the app restores captured colors only when its exact effect is still active. It leaves another program's different, newer matrix or ramp and changed/unverified displays alone. Matrix ownership compares actual Windows float32 values, and Gamma ownership compares exact ramps—not merely similar values. Another program applying an identical effect cannot be distinguished by value comparisons alone.

After an unexpected exit, the next launch can offer to restore prior colors when it recognizes the recorded effect. Failed display writes and restoration writes are read back, and observed partial effects are tracked before rollback or retry. Persistent unknown readbacks retain explicit recovery evidence instead of guessing that an arbitrary current state is owned. A failed restoration keeps evidence for a later safe retry; an unconfirmed active effect gets a safety retry timer.

Driver, display, or storage failures can still prevent full restoration. Read errors, wait for the display and storage to become available, and retry. Do not delete recovery evidence while you need it or assume colors were restored merely because a window closed. This tool is not intended for safety-critical color decisions.

## built with

- [PySide6](https://doc.qt.io/qtforpython-6/)
- Windows [Magnification color matrix](https://learn.microsoft.com/windows/win32/api/magnification/nf-magnification-magsetfullscreencoloreffect)
- Windows [GDI Gamma API](https://learn.microsoft.com/windows/win32/api/wingdi/nf-wingdi-setdevicegammaramp)
- [Python](https://www.python.org/)

## privacy and removal

The app has no telemetry, analytics, advertisements, accounts, uploads, or runtime internet requests. It does not capture screenshots or recordings during normal use. Confirmed values stay in `.runtime\settings.ini`. Original colors, display identities/modes, intended changes, and verified results used for safe recovery stay in `.runtime\color-recovery.json` inside the extracted folder. Setup contacts official Python and PyPI infrastructure to obtain verified components. Setup logs can contain local paths, so review `setup.log` before sharing it.

To remove Screen Color Changer, choose **Exit and restore original colors** in its tray menu, or Disable and then close, before deleting the extracted folder. Check any restoration error before deleting recovery evidence. Folder deletion by itself cannot restore shared Windows color state. Deletion removes the folder-local shortcut, private runtime, dependencies, saved values, recovery files, and app files. Applied tray mode is the same running utility, not an installed background service. The app does not add itself to startup or create an uninstaller entry.

## troubleshooting

If setup stops, the window immediately identifies the failed check and shows a short **How to fix it** instruction; the same guidance is saved in `setup.log`. Keep the complete extracted folder together, correct the listed issue, and run `Installer.bat` again. Success is reported only after dependencies, offline self-tests, and the shortcut all pass.

If the shortcut does not open, rerun setup after closing the utility. Wait for setup to finish before reopening it. Setup recreates and validates the shortcut for the folder's current location.

If a filter does not appear in a game or HDR session, first try an ordinary SDR desktop window. Disable a conflicting Windows Magnifier or calibration/filter app before applying this utility. If Gamma is unavailable, set it to **1.00** to use the other four controls.

If restoration reports an error, leave the folder and its recovery evidence intact. Retry **Disable** when the display and storage are available, or reopen the app to review previous-session recovery. Unknown state is not automatically overwritten; review the error rather than deleting the record to suppress it.

The source repository includes fake-display tests and `scripts/Test-AuditPerformance.py`. These use isolated settings/recovery paths, temporary journal IO, and offscreen Qt; they do not alter the desktop or prove native GPU/HDR/ARM64 compatibility. Installer fixture scripts retain disposable diagnostics and do not reinstall the working app by default. Full-setup checks should target disposable folders. Release and setup-check scripts support Windows PowerShell 5.1 or newer; the release builder requires a clean Git tree.

## license

Copyright 2026 Fleece. This project is source-available, not open source. The bundled [LICENSE](LICENSE) permits downloading, installing, and running an unmodified official release for lawful personal, non-commercial use. Modification, redistribution, sale, rebranding, and derivative versions remain prohibited. Third-party materials retain their own licenses.

The bundled [third-party notices](THIRD_PARTY_NOTICES.txt) identify downloaded runtimes, pinned versions, upstream sources, and license terms. Fleece's restrictions do not remove applicable third-party rights, including library replacement and debugging rights. The small release ZIP deliberately excludes runtimes, user settings, recovery records, logs, generated shortcuts, caches, and developer tests.

## note

This project was made with AI.

Use color filters only where permitted by the application, game, and platform rules. Verify restoration when you fully exit, and do not rely on this utility for safety-critical color decisions.
