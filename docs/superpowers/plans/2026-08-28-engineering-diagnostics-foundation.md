# B300 Engineering & Diagnostics Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze safe provisioning and add a small, testable ownership, update, and debug foundation without hardware writes.

**Architecture:** Normal and Factory provisioning remain separated behind `B300Service`; a shared `HardwareSessionManager` owns all target access. `DebugService` owns OpenOCD lifecycle and delegates GDB/MI framing to `GdbMiBackend`; GUI only binds to these services. Existing signed release validation remains the update trust boundary.

**Tech Stack:** Python 3.9 standard library, `unittest`, PySide6, OpenOCD, GDB/MI.

**Spec:** User-provided engineering diagnostics scope, 2026-08-28.

## Global Constraints

- Do not flash hardware, modify RDP, or run a destructive OpenOCD command.
- Normal Application flow writes only S3--S7 and never changes WRP.
- Factory flow is the sole S0--S2/WRP mutator and restores WRP on every post-unprotect failure.
- Keep all changes uncommitted and do not push.

### Task 1: Freeze provisioning interfaces

**Files:** `b300_core/policy.py`, `b300_core/openocd.py`, `b300_core/service.py`, provisioning tests.

- [x] Add failing tests for readout protection rejection, explicit Factory serial, and Option Byte reload after Factory WRP changes.
- [x] Make target policy fail closed for readout/security protection and preserve exact S3--S7 vs S0--S2 command domains.
- [x] Run focused provisioning tests.

### Task 2: Shared hardware ownership

**Files:** Create `b300_core/hardware_session.py`; modify `b300_core/service.py`, memory/debug callers; add `tests/test_hardware_session.py`.

- [x] Add failing tests for modes, concurrency conflicts, reentrant service work, and exception-safe release.
- [x] Implement a thread-safe context manager with IDLE/READING/FLASHING/FACTORY_PROVISIONING/DEBUGGING state.
- [x] Inject one manager into B300 and Debug services; use it for every OpenOCD lifecycle.

### Task 3: Update status/channel model

**Files:** Create `b300_core/update_channel.py`; modify updater GUI model and tests.

- [x] Add tests for Stable default, Beta selection, signed metadata preservation, and update install blocking while session busy.
- [x] Expose read-only update status/channel state without weakening manifest/signature/hash checks.

### Task 4: Debug/GDB core and minimal UI

**Files:** Create `b300_core/debug_service.py`, `b300_core/gdb_mi.py`, `b300_gui/debug_tab.py`; modify GUI window, CLI/docs/tests.

- [x] Add failing tests for non-flashing OpenOCD profile, port policy, lifecycle/lock release, GDB/MI commands and symbol-file validation.
- [x] Implement DebugService process lifecycle with injected process/socket dependencies for unit tests.
- [x] Implement minimal GDB/MI backend and a minimal Debug tab; no source editor or arbitrary memory writes.

### Task 5: Audit and verification

- [x] Update README, AGENTS, agent skill, debug and GUI docs.
- [ ] Run compileall, full tests, offscreen GUI smoke, static prohibited-command audit, and diff checks.
