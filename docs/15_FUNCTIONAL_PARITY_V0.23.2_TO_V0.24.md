# Functional Parity Contract — v0.23.2 → v0.24

Baseline production commit: `2eed7cec3aeba2e2eb67b76efd8d309dbb4066e5`  
Consolidation head when this contract was created: `9fa39980f115b0f5646b6175bf87a9f5ec68a245`

## Purpose

Repository cleanup must never silently remove a production capability. This document is the explicit preservation contract for the v0.24 repository-consolidation work.

A future refactor may move or rename implementation files, but it must preserve the user-visible behavior and safety invariants below, update the mapping deliberately, and keep the corresponding regression coverage green.

## Safety invariants that are not negotiable

- Normal Application flash only operates in the approved Application transaction and never becomes a Factory flow.
- Bootloader sectors remain protected from normal flash; no mass erase is introduced.
- Factory provisioning remains a separate, explicitly confirmed trusted-Bootloader workflow and restores/verifies WRP.
- HardwareSession ownership remains authoritative across flash, monitor, debug and target-memory access.
- Debug/Monitor paths remain non-programming paths.
- Gateway ownership/lease behavior remains fail-closed.
- GDB/TCL listener exposure remains constrained by the existing loopback/SSH design.
- Update/release trust continues to enforce signed manifest/integrity rules.

## Functional parity matrix

| Capability | v0.23.2 contract | v0.24 implementation status | Regression evidence | Status |
|---|---|---|---|---|
| Application flash | Validate HEX, protect Bootloader, erase/program only approved Application transaction, verify, reset, post-verify | Core unchanged from baseline | `test_b300_stlink.py`, `test_flash_service.py`, `test_core_hex_policy.py`, `test_core_openocd.py`, `test_cli_flash_debug_ux.py` | PASS |
| Factory Bootloader provisioning | Trusted bundled artifact only; explicit confirmation/probe; WRP off only for S0-S2 transaction then restored/verified | Core unchanged | `test_factory_policy.py`, `test_factory_resource.py`, `test_factory_service.py`, `test_factory_openocd.py`, `test_cli_factory_probe_policy.py` | PASS |
| Target/device truth | Probe identity, target validation, WRP/AppMeta/Application health and read-only device state remain available | Core unchanged | `test_core_probe_memory_metadata.py`, `test_device_state.py`, `test_application_health_service.py`, `test_app_health.py` | PASS |
| Hardware arbitration | Flash/Factory/Debug/Monitor cannot concurrently own the same ST-Link resource | Core unchanged | `test_hardware_session.py`, `test_gui_interlocks.py` | PASS |
| Local debug | GDB/MI + Safe TCL diagnostics, no implicit flash programming | Core unchanged | `test_debug_service.py`, `test_debug_session.py`, `test_gdb_mi.py`, `test_debug_memory.py`, `test_debug_sampling.py` | PASS |
| Gateway debug | Gateway retains ST-Link/OpenOCD ownership and safe lifecycle/recovery | Core unchanged | `test_gateway_supervisor.py`, `test_gateway_agent.py`, `test_gateway_readiness.py`, `test_gateway_status.py` | PASS |
| Client remote debug | Authenticated saved Gateway profile + SSH transport + bounded remote debug path | Core unchanged | `test_gateway_client.py`, `test_gateway_access.py`, `test_remote_session.py`, `test_debug_connection_ux.py` | PASS |
| Gateway lease/recovery | Exclusive lease and fail-closed recovery semantics remain authoritative | Core unchanged | `test_gateway_lease.py`, `test_gateway_lease_client.py`, `test_gateway_lease_coordinator.py`, `test_gateway_health_controller.py` | PASS |
| VS Code bridge | LOCAL/GATEWAY/CLIENT orchestration and managed launch remain available | Core unchanged; production GUI is exposed canonically as ProductionMainWindow | `test_v018_vscode_bridge.py`, `test_v018_vscode_controller.py`, `test_v018_simplified_ui.py` | PASS |
| Local live monitor | Continuous zero-halt/read-only monitoring and typed watch decoding remain available | Core unchanged | `test_live_monitor.py`, `test_live_session.py`, `test_live_service.py` | PASS |
| Remote live monitor | Gateway/client TCL tunnel, restart/recovery and shared ownership remain available | Core unchanged | `test_live_monitor_controller.py`, `test_cli_live_monitor.py`, `test_engineering_monitor.py` | PASS |
| Engineering diagnostics | FreeRTOS, target-aware/SVD peripheral inspection, stack/register/variable diagnostics remain available | Core unchanged | `test_freertos_inspector.py`, `test_target_awareness.py`, `test_gui_workstation.py`, `test_core_diagnostics.py` | PASS |
| Production GUI | Five primary workspaces PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS remain the production surface | Canonical implementation is ProductionMainWindow; MainWindowV18 remains a compatibility alias | `test_v018_simplified_ui.py`, `test_gui_smoke.py`, `test_production_window_compat.py`, `test_engineering_integration.py`, `test_engineering_program.py`, `test_engineering_device_settings.py` | PASS |
| Compatibility DebugTab import | Historical package-level DebugTab compatibility remains available while version-layer stack is retired | `debug_tab_compat.py` retained as thin wrapper over canonical DebugTab | `test_debug_tab.py`, `test_debug_connection_ux.py` | PASS |
| Update / self-update | Public signed update channel, version policy and install flow remain available | Core unchanged | `test_updater.py`, `test_updater_versioning.py`, `test_cli_update.py`, `test_cli_update_install.py`, `test_gui_updater.py` | PASS |
| Machine/offline setup | New-machine prerequisite checks, Linux USB setup and trusted offline runtime setup remain available | Core unchanged | `test_machine_setup.py`, `test_machine_setup_dialog.py`, `test_linux_usb_setup.py`, `test_offline_setup.py` | PASS |
| Packaging / native artifacts | GUI/CLI packaging and native bundle behavior remain covered | Build/release core unchanged | `test_gui_packaging.py`, `test_build_native_bundle.py`, `test_release_documentation.py` | PASS |
| CLI surface | Existing doctor/flash/provision/debug/gateway/monitor/update command families remain present | `b300_stlink.py` remains the compatibility façade; read-only doctor/target/metadata/memory orchestration is extracted to `b300_cli/inspection_commands.py` without changing syntax or reason codes | `test_b300_stlink.py` plus all `test_cli_*.py`; CI CLI help smoke | PASS |

## Consolidation diff boundary

For the initial v0.24 consolidation commits, the baseline-to-head diff intentionally does **not** modify:

- `b300_core/**`
- `b300_cli/**`
- `b300_stlink.py`

The removed GUI files were historical version-layer workbenches not selected by the production executable. After the follow-up canonicalization slice, `b300_gui/__main__.py` selects `ProductionMainWindow` from `production_window.py`; `main_window_v18.py` remains only as a compatibility alias.

## CI acceptance

GitHub Actions run `35454209181` passed on:

- Ubuntu x64 / Python 3.9
- Ubuntu ARM64 / Python 3.9
- Windows x64 / Python 3.9

Each platform runs the repository hygiene checks, the complete `tests/test_*.py` suite for that platform, package/source compilation and CLI/GUI smoke checks.

## Rules for the next refactors

1. Do not remove a capability merely because its current implementation is old or large.
2. Before moving implementation, first identify the regression tests that define its behavior.
3. Refactor one surface at a time; GUI canonicalization and CLI decomposition are separate changes.
4. Any intentional change to this contract must be explicit in the PR and accompanied by replacement regression coverage.
5. Hardware-mutating acceptance is not required for a repository-only cleanup that leaves hardware core code unchanged; any later hardware-path code change requires a separate hardware acceptance step.
