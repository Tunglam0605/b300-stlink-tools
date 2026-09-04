# B300 v0.18 Release Hardening and Consolidation Audit

**Baseline:** `main` / `v0.18.0` / `8b4cfcb9788aadd7b005e23c19fb4ce32c76be50`

**Audit date:** 2026-09-05

**Scope:** Establish the real v0.18 production path, legacy dependencies,
capability ownership, duplicate orchestration, packaging integrity gaps, and
documentation drift before implementation changes.

## Authoritative production path

The packaged GUI entry point is:

```text
b300_gui.__main__.main
  -> b300_gui.main_window_v18.MainWindowV18
  -> b300_gui.main_window.MainWindow (legacy/base construction)
```

The intended product architecture is unambiguous:

```text
PROGRAM | MONITOR | DEBUG | DEVICE | SETTINGS
```

Interactive debug is also unambiguous: B300 owns ST-Link, OpenOCD, managed
GDB, SSH transport, HardwareSession arbitration, cleanup, and run-state safety;
VS Code with Cortex-Debug owns the IDE experience.

The implementation is not yet equally clear. `MainWindowV18` subclasses the
2,230-line `MainWindow`. The base constructor creates the legacy Flash, Memory,
DebugTab, and Gateway widgets before v0.18 hides the legacy tabs and adds its
five new views. Production therefore still constructs legacy UI that is not
visible.

## Legacy GUI inventory

| File / module | Current production user | Used by v0.18? | Used by test? | Used by Monitor? | Used by compat? | Canonical? | Action |
|---|---|---:|---:|---:|---:|---:|---|
| `b300_gui/main_window.py` | `MainWindowV18` base class | Yes, indirectly and extensively | Yes | Yes, lifecycle owner | Yes | No; implementation base only | EXTRACT |
| `b300_gui/main_window_v15.py` | None | No | Yes, v0.15/v0.17 regressions | No | Historical | No | DEPRECATE |
| `b300_gui/main_window_v18.py` | `b300_gui.__main__` | Yes | Yes | Yes | No | Yes | KEEP |
| `b300_gui/debug_tab.py` | Hidden `MainWindow.debug_tab` | Yes, indirectly | Yes | Yes, owns live lifecycle | Yes | No | EXTRACT |
| `b300_gui/debug_tab_compat.py` | Package import monkey-patch | Yes, indirectly | Indirectly | Indirectly | Yes | No | DEPRECATE |
| `b300_gui/debug_tab_v15.py` | `MainWindowV15` | No | Yes | No production use | Historical | No | DEPRECATE |
| `b300_gui/debug_tab_v152.py` | `DebugTabV160` | No | Yes | No production use | Historical | No | DEPRECATE |
| `b300_gui/debug_tab_v160.py` | `DebugTabV170` | No | Chain coverage only | No | Historical | No | DEPRECATE |
| `b300_gui/debug_tab_v170.py` | `MainWindowV15` | No | Yes | No production use | Historical | No | DEPRECATE |
| `b300_gui/debug_ide_workbench.py` | v0.17 `DebugTabV170` only | No | Yes | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_source_view.py` | Legacy workbenches | No | Yes | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_variables_pane.py` | Legacy workbenches | No | Yes | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_breakpoints_pane.py` | Legacy workbenches | No | Yes | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_callstack_pane.py` | Legacy workbenches | No | Yes | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_registers_pane.py` | Legacy workbenches | No | Yes | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_intelligence_tabs.py` | v0.16/v0.17 chain | No | Indirect chain coverage | No | Historical | No | REMOVE after consumer removal |
| `b300_gui/debug_workspace.py` | Legacy `DebugTab` | Indirectly constructed | Yes | No | Yes | No | REMOVE after v0.18 extraction |
| `b300_gui/views/debug_studio_view.py` | No production importer found | No | No direct production coverage | No | No | No | REMOVE |
| `b300_gui/debug_live_panel.py` | `MonitorView` and legacy `DebugTab` | Yes | Yes | Yes | Yes | Yes as reusable widget | KEEP |
| `b300_gui/views/monitor_view.py` | `MainWindowV18` | Yes | Yes | Yes | No | Yes | MERGE with independent controller |

Actions are staged decisions, not permission to delete immediately. A REMOVE
action is permitted only after a failing production-contract test is added,
the production dependency is eliminated, import/search evidence shows no real
consumer, and the v0.18 regression suite passes.

## Monitor coupling

Current production flow:

```text
MainWindowV18
  -> MainWindow constructs hidden DebugTab
  -> DebugTab constructs DebugLivePanel and owns LiveMonitorSession/worker state
  -> MonitorView reparents DebugTab.live_panel
```

This is legacy coupling, not intentional reusable composition. `MonitorView`
can construct a panel itself, but without the hidden `DebugTab` it has no
production controller for start/stop, local/client configuration, worker
events, analytics, cleanup, or HardwareSession state.

Required target:

```text
LiveMonitorController + DebugLivePanel
                 ^
                 |
             MonitorView
```

The extraction must preserve the existing `LiveMonitorSession`, sampling
cadence, non-halting Safe TCL reads, analytics, cancel semantics, Client
TCL-only tunnel, and HardwareSession behavior. It must not reimplement the
backend or change realtime claims.

## Capability ownership map

| Capability | Current owner(s) | Finding | Canonical target |
|---|---|---|---|
| Application provisioning | `B300Service`; CLI main; base GUI handlers | Core transaction is shared; frontend orchestration/reporting remains large | `B300Service` plus thin CLI/GUI use-case adapters |
| Factory provisioning | `B300Service`; base GUI; CLI main | Safety core is shared; keep factory UI advanced and isolated | Existing core; thin frontends |
| Zero-halt monitoring | Core live modules; `DebugTab`; `MonitorView` | Backend is good; GUI lifecycle is coupled to legacy debug | Existing core + extracted production monitor controller |
| VS Code debug | `VsCodeDebugController`; `MainWindowV18`; CLI main | GUI controller is already focused; CLI path is separate and large | Shared use-case functions without weakening UI lifecycle |
| Gateway/client | Core remote modules; CLI main; base Gateway tab; v0.18 handlers | Multiple presentation/orchestration paths and legacy names remain | LOCAL/GATEWAY/CLIENT terminology and one core lifecycle per role |
| Machine setup | Core `machine_setup`; `MachineSetupDialog`; CLI main | Foundation is shared, presentation has duplicate entry points | Existing core + one GUI entry and one CLI command family |
| GUI update | `updater`, `update_install`, `update_helper`, `MainWindow` | Signature/download shared; Windows install publication is not tree-atomic | Shared verification + hardened platform installer |
| CLI update | `updater`, `cli_update`, `cli_update_install` | Staging, tree hash, atomic swap, lock, rollback are already present | KEEP and reuse its integrity principles |
| Diagnostics/support | `B300Service`, diagnostics modules, GUI/CLI handlers | Core exists; commands are embedded in large entry modules | Thin handlers over current core |
| Probe selection | Core `probe_selection`; GUI local selection; CLI helpers | Policy is central but frontends carry selection glue | Keep core reason codes; consolidate frontend adapter behavior |
| Target inspection | `B300Service`; duplicate GUI entry buttons; CLI handlers | Core is shared, presentation is duplicated | One shared current-target state in GUI |
| Version/release | `b300_version.py` plus validators | Single source of truth is correctly established | KEEP; expand validation coverage only |

## GUI duplication and operator-noise map

1. PROGRAM and DEVICE both show probe/target state and both expose refresh or
   inspect actions.
2. DEBUG and SETTINGS both show VS Code, Cortex-Debug, GDB, and OpenOCD
   readiness.
3. The base sidebar contains machine setup, update, and offline OpenOCD actions;
   SETTINGS exposes overlapping actions again while the hidden base UI remains
   constructed.
4. Base Flash/Memory/Gateway/Debug widgets remain alive behind the five-page
   v0.18 stack.
5. DEBUG exposes infrastructure text (paths, ports, TCL/GDB detail) in the
   default surface instead of progressive Details/Diagnostics disclosure.
6. Factory Bootloader and remote programming placeholders compete visually
   with the primary Application task in PROGRAM.

The five-page model remains canonical. Consolidation must create shared state
and actions, not remove DEVICE or SETTINGS.

## CLI audit

`b300_stlink.py` is 1,660 lines. Parser construction has already moved to
`b300_cli/parser.py`, and common text/JSON formatting has moved to
`b300_cli/reporting.py`, but the entry module still owns validation, VS Code
profile generation, symbol matching, selftest, integrated debug actions,
Client, Gateway, raw process lifecycle compatibility, memory/metadata/target,
setup, factory, flash, and dispatch.

The `debug` parser has one mode positional plus a broad union of Local,
Gateway, Client, VS Code, sampling, Live Monitor, break/watch, memory, SSH, and
port options. Compatibility requires those invocations to continue working,
but canonical help and dispatch should be organized by use-case. Deprecated
`debug server` may remain as an explicit warning alias for `debug gateway`; it
must not appear as a fourth production role.

Target shape:

```text
b300_stlink.py -> parse -> command handler -> existing core service -> reporter
```

Do not create a generic manager/factory hierarchy. Extract cohesive handlers
for provisioning, diagnostics, debug, gateway, setup, and update only where
the extraction removes real entry-module orchestration.

## Packaging and mixed-runtime integrity

### Safeguards already present

- Release downloads verify signed manifests, exact filenames, size, and SHA-256.
- CLI managed installs validate archive entries, stage outside the live tree,
  hash the extracted tree, lock publication, atomically swap directories, and
  restore the previous tree if publication or durable result logging fails.
- AppImage update copies to a temporary sibling and uses `os.replace`.
- DEB updates delegate transactional package handling to `apt-get`/dpkg.

### Confirmed Windows GUI gap

`packaging/windows/b300-stlink-gui.iss` currently installs recursively into the
fixed `{localappdata}\B300-STLink` directory using `ignoreversion`. It neither
publishes a complete versioned tree nor removes files that disappeared between
PyInstaller runtimes. The GUI update path verifies the installer file and then
launches it, but does not verify the installed runtime tree after publication.

This permits the observed corruption class:

```text
launcher/exe from release A + stale _internal files from release B
```

and can surface as Python `bad magic number`. This is a release blocker until a
test demonstrates clean tree publication or fail-safe rollback for a simulated
old mixed runtime. Installer/package SHA verification alone is insufficient.

Portable ZIPs are immutable archives and do not self-update in place, but
their build must still carry a deterministic runtime-integrity manifest and a
clean-launch smoke test. GUI installer and updater should consume the same
manifest rather than inventing a second version source.

## Versioning audit

`b300_version.py` is the single source of truth for Python code and build
scripts. GUI, core, native build, GUI packaging, release metadata generation,
tag validation, updater manifests, About, and What's New all import or validate
against it. No second source should be introduced. The hardening work needs
tests that compare the runtime-integrity manifest, installer `AppVersion`,
release asset metadata, and executable-reported version.

## Skill and documentation drift

The current skill correctly preserves B300 flash/metadata safety and loopback
debug policy, but its Debug section still leads with a raw
`b300-stlink debug --gdb-port 3333` flow and manual GDB commands. For v0.18+ it
must lead with LOCAL/GATEWAY/CLIENT and VS Code/Cortex-Debug, while retaining
CLI diagnostics as an advanced path. `commands.md` similarly documents manual
GDB before the managed VS Code flow.

README and `docs/04_DEBUG.md` contain correct architecture but mix quick-start,
deep internals, legacy compatibility, and historical acceptance detail. The
cleanup should classify documents as USER, OPERATOR, ENGINEER, AI AGENT,
RELEASE, or HISTORICAL ACCEPTANCE and link outward from a shorter README rather
than duplicating safety contracts.

## Hardware issue visibility

`HW-P1-001` remains open/deferred:

```text
Application verify PASS -> STLM VERIFIED -> reset
-> Bootloader does not confirm STLM within timeout
```

No consolidation change may translate this into PASS, relax the metadata
contract, retry blindly, or claim complete B300 hardware acceptance. The
independent STM32F4 Discovery debug bench can revalidate debug lifecycle without
reflashing, but it cannot close B300 Application acceptance.

## Milestone 1 conclusion

The v0.18 direction is correct, but the production object graph and Windows GUI
installer still contain release-blocking legacy/integrity weaknesses. The first
implementation slice should extract the production Monitor lifecycle and stop
constructing the legacy Debug IDE from `MainWindowV18`. Subsequent slices can
then remove historical UI consumers, thin CLI dispatch, simplify the five-page
presentation, harden Windows GUI publication, and synchronize skill/docs.

No flash policy, metadata policy, target addresses, HardwareSession rule,
OpenOCD network binding, or run-state restoration behavior requires redesign.
