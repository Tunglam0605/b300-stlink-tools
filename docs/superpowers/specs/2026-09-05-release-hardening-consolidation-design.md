# B300 Release Hardening and Consolidation Design

**Status:** Approved direction

**Baseline:** B300 ST-Link Tools v0.18.0

**Audit:** `docs/superpowers/specs/2026-09-05-release-hardening-consolidation-audit.md`

## Goal

Turn the v0.18 codebase into one coherent product with five obvious operator
tasks—program firmware, monitor the robot, debug through VS Code, inspect the
device, and prepare the workstation—without expanding the feature set or
weakening any hardware safety boundary.

## Canonical architecture

```text
GUI ----\
CLI -----+--> shared use cases/core
AI skill-/       |
                 +-- Provisioning
                 +-- Monitor
                 +-- Debug Bridge
                 +-- Remote
                 +-- Setup
                 +-- Update
                 `-- Diagnostics
```

The production GUI remains:

```text
PROGRAM | MONITOR | DEBUG | DEVICE | SETTINGS
```

The production debug roles remain:

```text
LOCAL | GATEWAY | CLIENT
```

B300 owns ST-Link, OpenOCD, managed GDB distribution/detection, HardwareSession,
SSH transport, lifecycle, cleanup, and target run-state safety. VS Code with
Cortex-Debug owns source navigation, breakpoints, watchpoints, variables,
registers, call stack, stepping, and interactive debugging.

## Product principles

Priority order is fixed:

```text
Safety -> Stability -> Consistency -> Simplicity -> Automation
       -> Cross-platform -> Maintainability -> New features
```

This cycle is consolidation, not a full rewrite and not a return to the
v0.15-v0.17 internal IDE. Refactoring must solve an observed duplicate,
ambiguous ownership, legacy coupling, testability gap, packaging risk, or UX
complexity. It must not introduce pattern-heavy infrastructure for its own
sake.

## GUI behavior

Each page has one primary purpose:

- PROGRAM: select a B300 Application HEX, show readiness, preview, and run the
  safe transaction. Factory Bootloader provisioning is an Advanced action.
- MONITOR: own the production zero-halt Live Monitor independently of legacy
  debug UI.
- DEBUG: select LOCAL, GATEWAY, or CLIENT and perform one clear start/open or
  stop action. Normal users never enter OpenOCD/GDB/TCL paths or ports.
- DEVICE: show the shared current probe/target evidence and provide detailed
  read-only inspection without duplicating the global connection action.
- SETTINGS: one machine-setup entry, update, support bundle, appearance, About,
  and diagnostic details.

Default surfaces use short status and action copy. OpenOCD/GDB paths, ports,
TCL, raw metadata/WRP, SSH internals, and full logs live under Advanced,
Details, or Diagnostics. A global shared device state prevents PROGRAM and
DEVICE from independently presenting contradictory probe/target status.

The GUI must not construct hidden legacy Debug IDE widgets. Historical UI is
removed only after the production Monitor/controller dependency is extracted
and protected by tests.

## Monitor extraction

Reuse `LiveMonitorSession`, `LiveService`, `DebugLivePanel`, Safe TCL readers,
analytics, and existing worker primitives. Add one focused production
controller that owns configuration, start/stop, sample events, cleanup, and
HardwareSession-facing busy state. `MonitorView` owns this controller/panel.

The refactor must not change:

- non-halting SWD read behavior;
- minimum/default cadence;
- bounded watch expression policy;
- local or Client TCL-only transport policy;
- analytics calculations;
- cancellation behavior;
- cleanup or RUNNING restoration rules.

## CLI consolidation

`b300_stlink.py` becomes a thin entry/dispatch module. Cohesive command modules
own provisioning, diagnostics, debug, gateway/setup, and update handlers. They
call existing core services and use the existing reporter.

All documented v0.18 invocations remain compatible. `debug server` remains
only as a deprecated alias for `debug gateway` and emits a clear warning in
text and structured output. Help presents LOCAL/GATEWAY/CLIENT and hides
advanced transport parameters from the first-level examples. No command may
silently change meaning.

## VS Code integration

The normal flow is:

```text
select workspace/ELF -> Open Debug in VS Code
```

B300 resolves VS Code, Cortex-Debug, managed GDB, loopback endpoints, and safe
attach configuration. Existing `.vscode/launch.json` content must not be
blindly destroyed. B300 updates only its named managed configuration through an
atomic write and preserves unrelated configurations. Failures after bridge
startup stop the bridge and release HardwareSession.

No normal GUI field exposes GDB/OpenOCD paths, 3333/6666, config files, or raw
TCL. Advanced diagnostics may display read-only resolved values.

## Setup and runtime integrity

First run performs one idempotent check and offers one action for required
missing dependencies. Existing dependencies are never reinstalled. OpenSSH is
optional unless the selected role needs it.

The runtime package contains a deterministic integrity manifest derived from
the complete staged application tree and the same `b300_version.py` version.
All platform packages are built from a clean stage and verified before publish.

Windows GUI installation must never recursively overlay a new PyInstaller tree
onto an old `_internal` tree. Installation/update must stage a complete tree,
verify it, switch only after verification, and either preserve or restore the
previous complete tree on failure. Installer tests must simulate a stale
Python runtime and prove it cannot survive a successful update or replace the
working installation after a failed update.

CLI atomic installation, AppImage atomic replacement, and DEB package-manager
transactions remain in place and gain cross-checks against the shared runtime
manifest where applicable.

## Documentation and skill

Code and `b300_version.py` are the sources of truth. The skill, AGENTS.md,
README, CLI help, and current guides use the same canonical names and workflows.
The skill leads with v0.18+ LOCAL/GATEWAY/CLIENT and VS Code integration; manual
GDB/one-shot diagnostics are Advanced. Historical acceptance reports remain
available but are labeled and are not mixed into the current quick start.

README contains Download, Install, Quick Start, capabilities, safety, and links
to deeper documents; it does not reproduce the entire architecture.

## Immutable safety constraints

- Sector 0-2 (`0x08000000..0x0800BFFF`) is protected Bootloader space.
- Sector 3 (`0x0800C000..0x0800FFFF`) is OTA metadata.
- Sector 4-7 (`0x08010000..0x0807FFFF`) is the B300 Application domain.
- Normal flash validates the immutable input and target, verifies WRP, erases
  only S3-S7, programs/verifies Application, writes and reads back exactly
  44-byte `STLM + VERIFIED`, resets, waits for `STLM + CONFIRMED`, and verifies
  the running state.
- Never mass/chip erase, bypass HEX validation, weaken metadata policy, write
  Option Bytes during normal flash, blindly retry, loosen HardwareSession,
  public-bind OpenOCD, or remotely forward TCL for VS Code.
- Factory Bootloader provisioning remains a separate explicitly authorized
  flow using only publisher-trusted bundled artifacts.

## Hardware status

`HW-P1-001` stays visible until direct B300 hardware evidence closes it. Its
current state is DEFERRED: Application verification and `STLM + VERIFIED`
succeed, but the Bootloader has not been proven to reach `STLM + CONFIRMED`
within the timeout. Software must not translate this into PASS.

The attached STM32F4 Discovery bench may validate debug lifecycle without a new
flash: OpenOCD start, GDB attach, registers/variables, hardware breakpoint,
watchpoint, Step Into/Over, Continue, disconnect, cleanup, RUNNING restoration,
port release, and VS Code/Cortex-Debug LOCAL. It does not validate the B300
Application/metadata contract or remote two-machine flow.

## Release gates

No release follows directly from refactoring. The candidate gate is:

```text
SOURCE CLEAN
-> TESTS PASS
-> GUI SMOKE PASS
-> CLI PASS
-> PACKAGE PASS
-> UPDATER PASS
-> DEBUG REGRESSION PASS
-> DOC/SKILL CONSISTENT
-> RC
```

Windows, Ubuntu x64, and Ubuntu ARM64 package evidence is mandatory before a
release recommendation. Choose a patch version only if public behavior is
unchanged; choose a minor version if the GUI/CLI behavior changes materially.
Do not choose a major version.

Final reporting must distinguish LOCAL DEBUG, REMOTE DEBUG, and B300
APPLICATION ACCEPTANCE and report each as PASS, FAIL, NOT TESTED, or DEFERRED as
allowed by the approved gate. It must include the branch, HEAD, commits, dirty
files, tag, push status, actual test commands/results, retained legacy reasons,
known issues, recommended version, and exact blockers.
