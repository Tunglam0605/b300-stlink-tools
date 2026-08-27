# B300 ST-Link Safe Updater Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a signed, non-blocking GUI update checker with verified downloads, a hardware-operation interlock, Windows managed installation, and safe Linux handoff.

**Architecture:** Pure modules in `b300_core` own SemVer, signed manifest parsing, download verification, cache policy, and platform selection. Qt workers and dialogs only render those states. Installation is delegated to a platform adapter after the main window proves all hardware operations are idle.

**Tech Stack:** Python 3.9, standard library HTTP, cryptography Ed25519 verification, PySide6, QSettings, unittest/PySide6 offscreen tests.

**Spec:** `docs/superpowers/specs/2026-08-27-b300-stlink-release-update-design.md`

## Global Constraints

- The release backend plan is complete and its signed manifest contract is frozen.
- Automatic checks never block startup and run at most once per 24 hours.
- Automatic network failures do not show modal errors; manual failures do.
- Update install/restart is impossible while any hardware operation is active.
- Updater code never imports probe, OpenOCD, memory, target, or flash services.
- Windows install is managed in v0.3.0; Linux packages use verified manual handoff.
- No update may downgrade or reinstall the current version.

---

### Task 1: SemVer and platform identity

**Files:**
- Create: `b300_core/versioning.py`
- Create: `b300_core/update_platform.py`
- Test: `tests/test_updater_versioning.py`

**Interfaces:**
- Produces immutable ordered `SemVer(major: int, minor: int, patch: int)` with `SemVer.parse(str)`.
- Produces `UpdatePlatform` enum and `detect_update_platform(executable: Path, system: str | None = None, machine: str | None = None) -> UpdatePlatform`.

- [ ] **Step 1: Write failing comparison, invalid input, architecture alias, AppImage, and DEB tests**

```python
self.assertLess(SemVer.parse("0.9.9"), SemVer.parse("0.10.0"))
self.assertEqual(detect_update_platform(Path("tool.AppImage"), "Linux", "x86_64"),
                 UpdatePlatform.LINUX_X64_APPIMAGE)
```

- [ ] **Step 2: Run and verify missing modules fail**
- [ ] **Step 3: Implement strict parser and deterministic platform normalization**
- [ ] **Step 4: Run focused/full tests; commit `feat: add updater version and platform models`**

### Task 2: Minisign-compatible manifest verification

**Files:**
- Create: `b300_core/release_manifest.py`
- Create: `b300_core/update_public_key.py`
- Modify: `requirements-gui.txt`
- Modify: `requirements-build.txt`
- Test: `tests/fixtures/update/latest.json`
- Test: `tests/fixtures/update/latest.json.minisig`
- Test: `tests/test_release_manifest.py`

**Interfaces:**
- Produces `verify_minisign(message: bytes, signature: bytes, public_key: str) -> None`.
- Produces `parse_latest_manifest(data: bytes, signature: bytes, public_key: str) -> LatestRelease`.
- `LatestRelease.select(platform: UpdatePlatform) -> ReleaseAsset`.

- [ ] **Step 1: Generate committed public test vectors and write failing valid/tampered/wrong-key tests**
- [ ] **Step 2: Run and verify implementation is missing**
- [ ] **Step 3: Implement strict Minisign packet/key-id parsing and Ed25519 verification; cap manifest and notes sizes before JSON parsing**
- [ ] **Step 4: Validate exact schema/types, HTTPS GitHub release URLs, 64-char lowercase SHA-256, positive sizes, and supported platform keys**
- [ ] **Step 5: Run focused/full tests; commit `feat: verify signed update manifests`**

### Task 3: Update client and cache policy

**Files:**
- Create: `b300_core/updater.py`
- Test: `tests/test_updater.py`

**Interfaces:**
- `UpdateClient.check(current_version: str) -> UpdateCheckResult`.
- `UpdateClient.download(asset: ReleaseAsset, destination_dir: Path, progress: Callable[[int, int], None], cancel: Event) -> Path`.
- `should_auto_check(last_check_utc: str | None, now: datetime, interval: timedelta = timedelta(hours=24)) -> bool`.

- [ ] **Step 1: Write failing local HTTP-server tests for new/no update, timeout, redirect limit, response limit, cancellation, partial files, wrong size/hash, and atomic ready file**
- [ ] **Step 2: Run and confirm missing API failures**
- [ ] **Step 3: Implement bounded HTTPS fetch with injectable opener/clock and no GitHub token requirement**
- [ ] **Step 4: Download to a same-directory temporary file, fsync, verify size/hash, then `os.replace` to the ready path**
- [ ] **Step 5: Run focused/full tests; commit `feat: add verified update client`**

### Task 4: GUI update worker and dialogs

**Files:**
- Create: `b300_gui/update_worker.py`
- Create: `b300_gui/update_dialog.py`
- Create: `b300_gui/about_dialog.py`
- Modify: `b300_gui/main_window.py`
- Modify: `b300_gui/styles.py`
- Test: `tests/test_gui_updater.py`

**Interfaces:**
- `UpdateCheckWorker` and `UpdateDownloadWorker` emit typed success/failure/progress signals.
- `UpdateDialog.set_release(release, asset)` renders notes and actions.
- `MainWindow.check_for_updates(manual: bool = False)` is the only GUI entry point.

- [ ] **Step 1: Write failing offscreen tests for Help menu, About fields, automatic silent failure, manual visible failure, and non-blocking worker lifetime**
- [ ] **Step 2: Run and confirm widgets/actions are missing**
- [ ] **Step 3: Implement focused dialogs and worker bridge without HTTP or signature logic in Qt classes**
- [ ] **Step 4: Schedule the automatic check after the initial event loop and persist UTC `last_update_check` in QSettings**
- [ ] **Step 5: Run GUI/full tests; commit `feat: add GUI update discovery and notification`**

### Task 5: Hardware-busy installation interlock

**Files:**
- Create: `b300_gui/operation_state.py`
- Modify: `b300_gui/main_window.py`
- Modify: `b300_gui/memory_tab.py`
- Modify: `b300_gui/update_dialog.py`
- Test: `tests/test_gui_update_safety.py`

**Interfaces:**
- `OperationState.is_hardware_busy -> bool` combines main-window worker state, memory worker state, and debug state.
- `UpdateDialog.set_install_allowed(bool, reason: str)` controls install only; checking/downloading remains permitted.

- [ ] **Step 1: Write failing tests for every busy source and downloaded-while-busy behavior**

```python
window.busy = True
window._refresh_update_install_state()
self.assertFalse(window.update_dialog.install_button.isEnabled())
```

- [ ] **Step 2: Run and observe current GUI has no shared operation-state gate**
- [ ] **Step 3: Implement one derived state; do not duplicate busy decisions in dialogs**
- [ ] **Step 4: Ensure close/update cannot terminate a flash or read worker and update never auto-installs**
- [ ] **Step 5: Run safety/full tests; commit `feat: block updates during hardware operations`**

### Task 6: Platform installation adapters

**Files:**
- Create: `b300_core/update_install.py`
- Create: `b300_updater_entry.py`
- Create: `b300-updater.spec`
- Modify: `packaging/windows/b300-stlink-gui.iss`
- Modify: `build_native_bundle.py`
- Test: `tests/test_update_install.py`
- Test: `tests/test_gui_packaging.py`

**Interfaces:**
- `prepare_install(package: Path, platform: UpdatePlatform, current_executable: Path) -> InstallPlan`.
- Windows plan launches the verified installer with inherited non-elevated context after GUI shutdown.
- Linux plan opens the containing directory and returns an exact human-readable command; it never invokes sudo/pkexec in v0.3.0.

- [ ] **Step 1: Write failing command/path/injection/unsupported-platform tests**
- [ ] **Step 2: Run and confirm missing adapter failure**
- [ ] **Step 3: Implement data-only plans and a tiny helper that accepts a validated absolute package path through a file, not shell interpolation**
- [ ] **Step 4: Include the helper only in GUI packages and integrate install after all workers exit**
- [ ] **Step 5: Run focused/full tests; commit `feat: add safe platform update handoff`**

### Task 7: What's New and update preferences

**Files:**
- Modify: `b300_gui/about_dialog.py`
- Modify: `b300_gui/main_window.py`
- Create: `b300_gui/whats_new_dialog.py`
- Test: `tests/test_gui_updater.py`

**Interfaces:**
- QSettings keys: `updates/automatic`, `updates/last_check_utc`, and `updates/last_seen_version`.
- What's New appears once when current version is newer than last seen.

- [ ] **Step 1: Write failing first-run, upgraded-run, same-version, disabled-auto-check, and corrupt-settings tests**
- [ ] **Step 2: Run and verify expected failures**
- [ ] **Step 3: Implement preferences and one-time What's New behavior with safe defaults**
- [ ] **Step 4: Run focused/full tests; commit `feat: add update preferences and what's new`**

### Task 8: Updater acceptance and release integration

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `docs/08_RELEASE_ACCEPTANCE.md`
- Modify: `docs/07_GUI_WINDOWS_UBUNTU.md`
- Modify: `docs/superpowers/roadmaps/2026-08-27-b300-stlink-release-update.md`

**Interfaces:**
- CI tests updater behavior without Internet or hardware using local fixtures/server.

- [ ] **Step 1: Run all unit/GUI tests with no network and require zero failures**
- [ ] **Step 2: Compile all Python packages and entry points**
- [ ] **Step 3: Build Windows package; verify updater helper, embedded public key, About build commit, GUI smoke test, and no probe discovery during updater smoke**
- [ ] **Step 4: Run Linux x64/ARM64 release dry-run and repeat signature/hash/download handoff tests**
- [ ] **Step 5: Confirm installer is disabled for simulated flash/read/debug workers and enabled immediately after workers finish**
- [ ] **Step 6: Update docs and acceptance evidence; commit `test: complete v0.3.0 updater acceptance`**
