# Managed Remote Application Programming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let GUI and CLI Clients upload and safely program an Application HEX through the managed Gateway, while stabilizing Gateway startup and preserving Debug and Monitor.

**Architecture:** Reuse SSH/SFTP for file transport and the per-user Gateway Agent for a persisted prepare/commit/status transaction. A new exclusive flash lease owns the physical probe; the Gateway reuses the canonical local `GatewayProgrammingService` and `B300Service.flash`. Repair Gateway startup before integrating the new mode.

**Tech Stack:** Python 3, Paramiko, PySide6, OpenOCD, unittest, Windows and Ubuntu x64/ARM64.

**Spec:** `docs/superpowers/specs/2026-09-23-remote-application-programming-design.md`

## Global Constraints

- Remote execution accepts Intel HEX Application only; maximum file size is 32 MiB.
- Bootloader Sectors 0--2 (`0x08000000..0x0800BFFF`) are never erased or programmed by this workflow.
- The canonical transaction erases Sector 3--7 and writes, reads back, and confirms 44-byte STLM metadata.
- Never mass erase, change RDP/WRP, bypass HEX validation, or retry a failed flash automatically.
- Multiple probes require an exact physical `--probe-serial`.
- GDB/TCL remain loopback-only; SSH is the sole remote transport.
- Flash success requires exact OpenOCD verify, reset, confirmed metadata, PC in Application, and `BKP1R == 0`.
- Preserve existing local flash, factory, Debug, Monitor, and GUI flows.
- Do not commit firmware images, Keil objects, OpenOCD binaries, or release archives.

## File map and interfaces

- `b300_core/gateway_supervisor.py`: OpenOCD lifecycle, event callback synchronization, and diagnostics.
- `b300_core/gateway_lease.py`, `gateway_lease_coordinator.py`, `gateway_agent.py`, `gateway_agent_protocol.py`: exclusive mode, Agent dispatch, and state persistence.
- `b300_core/hardware_session.py`: cross-process hardware ownership shared by local and Gateway processes.
- `b300_core/remote_programming.py`: existing manifest and canonical prepare/flash adapter.
- New `b300_core/gateway_program_jobs.py`: private upload/approval/job store and state machine.
- `b300_core/remote_session.py`: SFTP upload and fixed allowlisted Gateway control calls.
- `b300_cli/parser.py`, `b300_stlink.py`: remote flash and Agent control CLI.
- `b300_gui/production_window.py`, `b300_gui/views/program_view.py`, `b300_gui/engineering_context_controller.py`: GUI remote PROGRAM orchestration and state.
- `b300_core/gateway_protocol.py`: versioned feature capability.
- Tests mirror each component in `tests/`.

---

### Task 1: Repair Gateway startup and diagnostics

**Files:** Modify `b300_core/gateway_supervisor.py`; test `tests/test_gateway_supervisor.py`.

**Interfaces:** Preserve `GatewaySupervisor.ensure() -> GatewaySnapshot`, `observe()`, `stop()`, and OpenOCD event callback signature.

- [ ] **Step 1: Write a failing regression test** in `tests/test_gateway_supervisor.py` where a fake `DebugService.start()` invokes the supplied event callback on a second thread and waits for it. Assert `ensure().attach_ready` is true and the callback completes before the readiness timeout. Add a second test that an injected startup exception yields a bounded, accurate reason.
- [ ] **Step 2: Run** `python -m unittest tests.test_gateway_supervisor -q`; confirm the readiness test fails by timeout against the current lock behavior.
- [ ] **Step 3: Implement** a startup lock boundary in `GatewaySupervisor.ensure()`: reserve a generation while locked, release lock during blocking `service.start(...)`, reacquire to verify generation/stop state, then publish READY. Keep `_on_openocd_line` non-blocking while startup waits. Preserve the bounded original startup error in private diagnostics.
- [ ] **Step 4: Run** the focused test and existing Gateway lease/Agent tests; confirm callback completion and no stale READY publication.
- [ ] **Step 5: Commit** `fix: prevent managed gateway startup deadlock`.

### Task 2: Exclusive flash ownership across processes

**Files:** Modify `b300_core/hardware_session.py`, `b300_core/gateway_lease.py`, `b300_core/gateway_lease_coordinator.py`; create `b300_core/hardware_owner.py`; test `tests/test_hardware_owner.py`, `tests/test_gateway_lease_coordinator.py`.

**Interfaces:** `HardwareOwnerLock.acquire(probe: ProbeRef, mode: HardwareMode)` is a context manager. `GatewayLeaseRequest(mode="FLASH_APPLICATION", probe_serial=...)` reserves the probe without starting Debug OpenOCD.

- [ ] **Step 1: Write failing tests**: a second process cannot acquire one probe; corrupt/live lock evidence fails closed; flash lease excludes `LIVE_WATCH` and `VSCODE_DEBUG`; flash acquisition does not call `GatewaySupervisor.ensure()`.
- [ ] **Step 2: Run** `python -m unittest tests.test_hardware_owner tests.test_gateway_lease_coordinator -q`; confirm failures identify the missing ownership/mode behavior.
- [ ] **Step 3: Implement** the private owner lock with immutable process evidence and integrate it into all `HardwareSessionManager` top-level operations. Add mode-aware lease acquisition/renewal/cleanup, keeping existing Debug mode behavior.
- [ ] **Step 4: Run** focused tests plus `tests.test_hardware_session`, `tests.test_gateway_lease_client`, and `tests.test_gateway_agent`.
- [ ] **Step 5: Commit** `feat: reserve gateway hardware for application programming`.

### Task 3: Private uploaded artifact and approval job

**Files:** Create `b300_core/gateway_program_jobs.py`; modify `b300_core/gateway_agent_protocol.py`, `b300_core/gateway_agent.py`, `b300_core/gateway_protocol.py`; test `tests/test_gateway_program_jobs.py`, `tests/test_gateway_agent.py`.

**Interfaces:** `GatewayProgramJobs.create_upload(manifest, client_id, probe_serial) -> UploadSlot`; `finalize_upload(slot_id) -> StagedArtifact`; `prepare(slot_id, lease) -> ProgramApproval`; `commit(approval_id, token, lease) -> ProgramJob`; `status(job_id) -> dict`. Bytes never enter the JSON request queue.

- [ ] **Step 1: Write failing tests** using a real temporary staging directory: oversized/changed/symlink uploads are rejected; valid HEX is finalized atomically; stale approval and wrong lease are rejected; commit returns one persistent job; repeating commit does not run flash twice; Agent restart reports `RECOVERY_REQUIRED` for ambiguous running jobs.
- [ ] **Step 2: Run** `python -m unittest tests.test_gateway_program_jobs -q`; confirm the missing module/API failures.
- [ ] **Step 3: Implement** private `0700`/`0600` slot storage, quotas, `lstat`/containment checks, SHA-256 validation, canonical prepare, short approval TTL, immutable commit, worker execution, bounded logs/results, and post-result cleanup. Add exact Agent request schemas and `remote_application_flash_v1` capability.
- [ ] **Step 4: Run** focused tests plus protocol, programming, and Agent tests. Ensure no remote Bootloader operation is exposed.
- [ ] **Step 5: Commit** `feat: add managed gateway application programming jobs`.

### Task 4: SSH/SFTP Client and CLI

**Files:** Modify `b300_core/remote_session.py`, `b300_cli/parser.py`, `b300_stlink.py`; optionally create `b300_cli/remote_flash.py`; test `tests/test_remote_session.py`, `tests/test_cli_remote_flash.py`.

**Interfaces:** `RemoteSession.upload_application(path, manifest, probe_serial) -> ProgramApproval`; `commit_application(approval) -> ProgramJob`; `program_status(job_id) -> dict`. Public CLI: `flash application.hex --gateway <profile> [--dry-run | --confirm-remote-application] [--probe-serial ...] --json`.

- [ ] **Step 1: Write failing tests** for SFTP byte transfer, host/profile identity, fixed remote commands, manifest mismatch, protocol incompatibility, dry-run output, required commit flag, and reconnect/status after Client interruption.
- [ ] **Step 2: Run** `python -m unittest tests.test_remote_session tests.test_cli_remote_flash -q`; confirm expected failures.
- [ ] **Step 3: Implement** the allowlisted SSH/SFTP commands and CLI orchestration. Use profile key/host trust already established by `RemoteSession`; never place secrets in argv, JSON, or logs. The CLI must not retry a committed flash on timeout.
- [ ] **Step 4: Run** focused tests plus `tests.test_gateway_remote_ensure`, `tests.test_gateway_protocol`, and `tests.test_cli_parser_json`.
- [ ] **Step 5: Commit** `feat: upload and program application from cli client`.

### Task 5: Production GUI remote PROGRAM

**Files:** Modify `b300_gui/production_window.py`, `b300_gui/views/program_view.py`, `b300_gui/engineering_context_controller.py`; optionally create `b300_gui/remote_program_controller.py`; test `tests/test_engineering_program.py`, `tests/test_program_preflight.py`, `tests/test_remote_program_gui.py`.

**Interfaces:** The same selected `RemoteSession` and Gateway profile used by Monitor/Debug; `ProgramView` emits the existing `flash_application_requested(path, dry_run)` signal; remote Bootloader action remains disabled.

- [ ] **Step 1: Write failing GUI tests**: remote HEX selection enables upload/preflight, Gateway plan appears before confirmation, rejected confirmation causes no commit, accepted confirmation starts one job, connection/file/probe changes invalidate approval, restart reconnects to job, remote Bootloader remains disabled.
- [ ] **Step 2: Run** `python -m unittest tests.test_remote_program_gui -q` with `QT_QPA_PLATFORM=offscreen`; confirm current local-only behavior fails.
- [ ] **Step 3: Implement** a GUI worker/controller that uploads and prepares without blocking Qt, renders Gateway evidence, invokes one Client dialog, commits once, and polls job status. Keep control states tied to current context revision and job state.
- [ ] **Step 4: Run** focused GUI tests and existing PROGRAM/Monitor/Debug GUI tests.
- [ ] **Step 5: Commit** `feat: enable application programming in gui client`.

### Task 6: Documentation, complete regression, and physical acceptance

**Files:** Modify `docs/03_FLASH_FIRMWARE.md`, `docs/04_DEBUG.md`, `docs/05_TROUBLESHOOTING.md`, `docs/07_GUI_WINDOWS_UBUNTU.md`; create a dated acceptance report under `docs/acceptance/`; update release notes where required.

**Interfaces:** Document exact CLI/GUI commands, SSH/profile setup, approval display, recovery/status command, and post-flash checks.

- [ ] **Step 1: Run** `python -m unittest discover -s tests -q`, `python -m compileall -q b300_core b300_cli b300_gui b300_stlink.py`, and `git diff --check`. Repair only evidenced failures and repeat until clean.
- [ ] **Step 2: Validate** both packaged Client and Gateway build paths on their native OS/architecture; do not claim an unrun platform gate.
- [ ] **Step 3: Deploy** matching candidate builds to `aubot-tech` only after software gates; run Agent lease, dry-run, one user-authorized Application flash, metadata/PC/BKP checks, reconnect, Debug/Monitor, and cleanup. Save logs and exact versions/hashes in the acceptance report.
- [ ] **Step 4: Update** the user docs and report with actual observed outcomes, including any deferred gate. Commit `docs: record remote application programming acceptance`.

## Self-review gate

Before completion, compare all 16 sections of the spec against Tasks 1--6. In particular, check that the release candidate has no remote Bootloader surface, SSH is the only network transport, disconnects do not cause duplicate flash, all modes share one hardware owner, and the physical test was performed against the exact built artifacts. Search this plan for `TBD`, `TODO`, or unspecified test steps, then correct any gaps. Do not claim completion while a required gate remains unverified.
