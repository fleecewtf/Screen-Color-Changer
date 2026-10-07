# Screen Color Changer development notes

This document supplements the user-facing [README](../README.md). It describes implementation and regression coverage, not a promise that every native display, driver, or game supports the utility.

## Repository and release layout

Developer scripts and the five flat `test_*.py` modules live together in `scripts`; there is no separate top-level test directory. `Test-AppSafety.py` is the offline regression entry point. The screenshot and its capture helper are repository assets, not release dependencies.

The official ZIP contains exactly nine files:

- `Screen Color Changer.pyw`, `color_math.py`, and `screen_backend.py`
- `Installer.bat`
- `requirements-win-x64.txt` and `requirements-win-arm64.txt`
- [LICENSE](../LICENSE), [READ ME.txt](../READ%20ME.txt), and [THIRD_PARTY_NOTICES.txt](../THIRD_PARTY_NOTICES.txt)

It excludes runtimes, dependencies, user settings, recovery records, logs, generated shortcuts, caches, screenshots, and developer tests. `Build-ReleaseZip.ps1` requires a clean Git tree and reviewed release inputs; do not copy a working runtime or personal recovery record into an archive.

## Exact controls and display layers

The normal window is a compact 496 × 496 logical-pixel utility. Smaller logical work areas use scrolling without reducing the inner slider travel below the precision contract. The normal-size window should not need a horizontal scrollbar. Integer controls use exact one-unit steps; Gamma uses hundredths. Typing counts as editing activity, but incomplete numeric text is not committed or previewed until Enter or focus loss. Timer updates must not erase pending text.

Digital vibrance, Hue, Brightness, and Contrast use Windows' shared full-screen Magnification color matrix. Vibrance changes saturation independently of NVIDIA Control Panel; its scale is not NVIDIA's scale. This version affects the desktop and does not independently select monitors. HDR content, protected video, exclusive-fullscreen games, and independent-flip paths can bypass the matrix. Windows Magnifier and other filters can conflict with shared color state.

Gamma uses a separate nonlinear curve composed with the captured original display calibration, not a repeatedly modified ramp. Neutral Gamma **1.00** leaves calibration alone and permits the other controls without loading the Gamma API when no Gamma state is already tracked. Non-neutral Gamma requires verified SDR on every active target and supported display drivers. Each target's SDR status, identity, and captured mode are revalidated before Gamma writes, including restoration. Ignored or partially accepted driver curves must not be reported as successful changes.

## Serialized operations and confirmation

All production display initialization, reads, apply/verification, recovery, restoration, and teardown run on one background worker. Qt receives completion events without running those native operations on its UI thread. There is at most one active operation and one pending operation; newer previews replace older pending previews. A 75 ms coalescing timer limits rapid edits. Equal values are verified rather than blindly written again.

Apply captures the values it actually submits. Successful completion saves only that verified snapshot, even if newer edits arrive meanwhile; the newer values remain unapplied previews. Reset, Disable, recovery, and Close invalidate superseded preview epochs and act as control barriers. Closing waits for serialized restoration before final teardown. Late results cannot revive a closed UI or restart its timers.

A failed preview that leaves unresolved active state pauses further previews, clears queued preview/confirmation work, and arms a restoration retry watchdog. Continuous editing must not postpone that retry indefinitely. Apply cannot jump the pending restoration barrier. Safety timers are armed before modal errors, including close failures, and completion handling clears busy state before nested dialog event loops can run. A slow native driver operation cannot safely be interrupted in the middle; serialization prevents concurrent display writes, not arbitrary driver stalls.

## Apply, inactivity, tray, and reopening

Only **Apply colors** saves values. An unapplied preview reverts after 15 seconds without editing activity to the last profile applied in the current session, or to the prior desktop colors if none was applied. Continuing to edit refreshes this timer. Incomplete text is preserved while typing. Applying and retaining a profile in the tray recheck the actual matrix and tracked Gamma rather than assuming the last preview still owns Windows state.

Closing before Apply restores prior colors and exits. After Apply, closing discards later unsaved edits and keeps the confirmed profile in the tray when available. Without a tray, close restores and exits. Disable or Esc restores prior colors; Reset also clears the confirmed profile and saves neutral values. The tray's **Exit and restore original colors** command restores and fully quits. Saved values do not automatically apply on launch or restart; tray mode lasts only for the running session.

A second launch uses a same-user, same-session local named pipe only to reopen the existing window. It does not start a second filter, transmit settings, accept paths or executable commands, or use the internet. Clients and requests are bounded and time-limited. App startup and setup share a mutex protocol described below.

## Ownership and recovery limitations

Matrix ownership compares actual Windows float32 values; Gamma ownership compares exact ramps. Merely similar effects are not treated as owned. A different newer effect, or a changed/unverified display, is left alone. An identical effect applied by another program cannot be distinguished by value comparison. Windows' shared display APIs do not provide atomic ownership: state can change between a read, ownership check, and write.

Display writes and restoration writes are read back even when an API reports failure. Observed partial effects are tracked before rollback or retry. Persistent unknown readbacks retain explicit evidence rather than guessing that arbitrary current state is owned. Recovery records include original colors, display identities/modes, intended changes, and verified results. Resolved layers can be removed while unresolved baselines remain available for retry.

After an unexpected exit, a later launch can offer recovery for a recognized recorded effect or unresolved state. New color changes stay blocked until the recovery choice is handled. Declining recovery abandons evidence; it is not proof of restoration. Driver, display, readback, or storage failures can prevent full restoration. Keep recovery files, review the error, and retry when the relevant devices/storage become available. Neither a closed window nor a deleted folder proves that Windows color state was restored. Do not use this tool for safety-critical color decisions.

## Setup and repair contract

Setup follows the shared Fleece workflow: download the official ZIP, extract the entire folder, run `Installer.bat`, accept the Terms and bundled Tool License with **Y**, wait for final checks, and open the folder-local shortcut. It needs no administrator access, system Python, PATH changes, global packages, service, startup entry, or uninstaller registration.

The installer pins official Python **3.14.7**, pip **26.2.1**, PySide6-Essentials **6.11.2**, and Shiboken **6.11.2**. A downloaded pip bootstrap installs private dependencies. Runtime archives and the complete PyPI wheel set must pass pinned SHA-256 verification. Architecture-specific locks select x64 or ARM64 automatically. A missing, modified, or unsafe selected lock stops setup before the first component download.

The full extracted path must be at most **72 characters**. Spaces, carets (`^`), ampersands (`&`), parentheses, and exclamation marks (`!`) are supported. Percent signs (`%`) are rejected before download because Windows shortcut creation can expand them as environment variables. Keep both locks and every bundled file together; move or rename the full folder to correct an unsupported path.

Repair preserves saved values and recreates the shortcut for the current location. Setup rejects linked/reparse-point private runtime trees, verifies runtime architecture, and recovers interrupted runtime/package transactions before reuse or an unnecessary new download. App startup acquires the setup gate before importing Qt/backend modules, establishes its stable app mutex, then releases the gate before duplicate-instance IPC. Setup holds the same gate across repair and rejects an already running app. Close the utility before repair; wait for setup to finish before reopening.

Source and Windows shortcut support are checked before large downloads. All three Python sources compile before app-package downloads. Offline self-tests never change desktop colors. Setup reports success only after dependencies, self-tests, and the shortcut pass. Errors and short **How to fix it** guidance appear immediately and are written to `setup.log`.

## Offline regression and performance checks

From the source folder, run:

```powershell
.runtime\python\python.exe -I -B scripts/Test-AppSafety.py
```

The runner discovers the flat test modules in `scripts`. Tests use fake Magnification/Gamma APIs, disposable settings and recovery files, and offscreen Qt. They do not write to the real display or the user's preferences. Coverage includes color math and boundaries, malformed input, partial failures, ownership/readbacks, SDR and mode checks, recovery transactions, same-session IPC, setup gates, and asynchronous UI state.

The actual serialized worker is exercised with slow fakes. Regression cases cover UI heartbeat responsiveness, latest-value coalescing, exact Apply snapshots, Reset/Close races, modal-error safety timers, continuous-edit restoration starvation, shutdown, typing activity, small-window precision, and visual-control layout. Passing fake tests is not evidence that every native driver accepts a particular ramp.

For the separate bounded benchmark, run:

```powershell
.runtime\python\python.exe -I -B scripts/Test-AuditPerformance.py
```

This uses fake display operations, isolated settings, temporary recovery-journal I/O, and offscreen Qt. It measures bounded operation cost, latest-value behavior, worker-thread use, GUI heartbeat, and render/work limits. Timing budgets are regression tripwires, not guarantees for another computer or GPU, or substitutes for prolonged native-display soak tests.

`Test-ReleasePreflight.ps1` and `Test-SetupLifecycle.ps1` use disposable fixtures, retain diagnostics, and do not reinstall the working app by default. Full-setup checks should target disposable folders. `Test-PipLocks.py` checks official package metadata against the architecture locks. Release and setup-check PowerShell scripts support Windows PowerShell 5.1 or newer.

The v1.0.0 release passed hosted ARM64 setup/repair and all 145 offline regression checks. Those checks, fake tests, screenshots, and read-only native inspection do not prove all-control real SDR behavior, native GPU/HDR/display-driver behavior, or compatibility with every game. Record which real-display checks were performed and which were omitted; do not silently convert these limits into a release guarantee.

## Privacy, removal, and third-party rights

There are no runtime network requests, telemetry, analytics, advertisements, accounts, uploads, or normal-use screenshots/recordings. Confirmed values stay in `.runtime\settings.ini`; recovery evidence stays in `.runtime\color-recovery.json`. Setup contacts official Python and PyPI infrastructure. Logs can contain local paths; inspect them before sharing.

Restore colors and fully exit before deleting the extracted folder. Review restoration errors before deleting evidence. Deleting files cannot restore shared Windows state or undo another application's effects. The tray process is the same utility, not an installed service; saved settings never automatically apply at startup.

Fleece's bundled [LICENSE](../LICENSE) is source-available, not an open-source license. Third-party materials retain their own terms. [THIRD_PARTY_NOTICES.txt](../THIRD_PARTY_NOTICES.txt) identifies downloaded runtimes, pinned versions, upstream sources, and licensing information. Fleece's restrictions do not remove applicable third-party rights, including library replacement and debugging rights.
