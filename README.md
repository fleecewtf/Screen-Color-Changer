<div align="center">

# screen color changer

Current release: **v1.0.0**. Includes exact live controls, ownership-aware recovery, and verified private-runtime setup.

All Fleece desktop tools use the same installation workflow: download the official ZIP, extract the entire folder, run `Installer.bat`, accept the bundled Terms/Tool License, wait for final checks, then open the folder-local shortcut. Setup installs a private runtime without changing system Python or requiring administrator access. Rerun it to repair or refresh a moved shortcut. Keep the full path at most 72 characters, without percent signs. Architecture support and extra components vary by tool; File Converter remains x64-only.

A little tool I made with AI to fine-tune desktop colors locally on 64-bit Windows.

<img src="Screen%20Color%20Changer.png" alt="Screen Color Changer app window" width="760">

</div>

## features

- Fine-tune Digital vibrance, Hue, Brightness, Contrast, and Gamma
- Preview colors live while dragging or scrolling, without repeated Apply clicks
- Type exact values above sliders, including 255% vibrance and Gamma 1.03
- Save only verified values with **Apply colors**
- Revert unapplied previews after 15 seconds without editing activity
- Keep applied colors in the tray, or restore prior colors and fully exit
- Use a compact, responsive window that scrolls on smaller work areas

## requirements

- 64-bit x64 or ARM64 Windows with the full-screen Magnification API available
- An internet connection during first setup or dependency repair
- Verified SDR on every active display and supported drivers for non-neutral Gamma
- No internet connection while using the installed app

Gamma **1.00** preserves existing calibration; use it when Gamma is unavailable. HDR content, protected video, and exclusive or independent-flip games can bypass desktop filters. Compatibility with every GPU, monitor, app, or game is not guaranteed.

## installation

1. Download the latest release ZIP.
2. Extract the complete folder.
3. Double-click `Installer.bat`.
4. Press **Y** once to accept the Terms and bundled Tool License and approve setup.
5. Leave the setup window open until every check passes.
6. Double-click the `Screen Color Changer` shortcut created in the folder.

Keep all files in a writable local folder with a full path of **72 characters or fewer**, without percent signs (`%`). Setup hash-verifies private Python **3.14.7**, pip, and PySide6-Essentials **6.11.2**. Keep both locks for automatic x64/ARM64 selection. No system Python, admin access, PATH changes, or global packages are needed.

Rerun `Installer.bat` to repair components or refresh a moved shortcut without losing saved values. Close the utility before repair and wait for setup to finish before reopening it.

## usage

1. Drag or scroll a slider, use its arrow keys, or type an exact number.
2. Press Enter or leave a numeric box to commit typed values and preview them.
3. Click **Apply colors** to confirm and save the verified result.
4. Use **Disable**, **Reset**, or **Esc** while the app has focus to restore prior colors.
5. Use the tray's **Exit and restore original colors** command to restore and quit completely.

| Control | Range | Neutral | Step |
| --- | --- | --- | --- |
| Digital vibrance | 0–300% | 100% | 1% |
| Hue | −180–180° | 0° | 1° |
| Brightness | −20–20% | 0% | 1% |
| Contrast | 50–200% | 100% | 1% |
| Gamma | 0.50–2.00 | 1.00 | 0.01 |

Only **Apply colors** saves values. After **15 seconds** without editing, unapplied previews return to this session's last applied profile or the prior desktop colors. Typing counts as activity; incomplete values wait until committed.

Closing before Apply restores and exits. After Apply, close discards unsaved edits and keeps the confirmed profile in the tray; reopen from its icon or shortcut. Without a tray, close restores and exits. **Reset** clears the applied profile and saves neutral values. Saved settings never automatically apply on launch or restart.

Vibrance uses its own scale, independent of NVIDIA Control Panel. Filters affect the desktop, not selected monitors. Other filters can conflict; ownership checks cannot prevent every race or distinguish identical effects. Restoration can fail: keep recovery evidence and review errors.

## built with

- [PySide6](https://doc.qt.io/qtforpython-6/)
- Windows [Magnification color matrix](https://learn.microsoft.com/windows/win32/api/magnification/nf-magnification-magsetfullscreencoloreffect)
- Windows [GDI Gamma API](https://learn.microsoft.com/windows/win32/api/wingdi/nf-wingdi-setdevicegammaramp)
- [Python](https://www.python.org/)

## privacy and removal

No telemetry, analytics, ads, accounts, uploads, runtime network requests, or normal-use recording. Settings and recovery stay in `.runtime`. Setup contacts official Python/PyPI hosts; review local paths in `setup.log` before sharing.

Before deleting the folder, choose **Exit and restore original colors**, or Disable and close. Check errors first: file deletion cannot restore Windows colors. Deleting the folder removes the app and private components. Tray mode is not a service; there is no startup or uninstaller entry.

## troubleshooting

Setup shows failed checks and **How to fix it** guidance in its window and `setup.log`. Correct the issue and rerun `Installer.bat`; it also recreates a broken or moved shortcut. Success requires dependencies, offline self-tests, and the shortcut to pass.

Try an SDR desktop window if a game ignores filters. Use Gamma **1.00** if unavailable. For restoration errors, keep recovery files; retry **Disable** when display/storage are available or reopen to review recovery. Do not delete evidence just to dismiss an error.

## license

Copyright 2026 Fleece. This project is source-available, not open source. The bundled [LICENSE](LICENSE) permits downloading, installing, and running an unmodified official release for lawful personal, non-commercial use. Modification, redistribution, sale, rebranding, and derivative versions remain prohibited. Third-party materials retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.txt) for their terms and rights.

## note

This project was made with AI.

Use filters only where the application, game, and platform permit them. Verify restoration when fully exiting; do not use this utility for safety-critical color decisions.
