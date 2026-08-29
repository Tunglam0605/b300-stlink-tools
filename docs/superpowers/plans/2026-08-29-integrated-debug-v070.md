# v0.7.0 — Integrated STM32 debug acceptance

## Architecture

```text
CLI one-shot command
  -> DebugSession (single lifecycle owner)
     -> DebugService / HardwareSessionManager
        -> OpenOCD
           -> GDB server 127.0.0.1:3333
           -> TCL server 127.0.0.1:6666
     -> SafeTclClient
        -> version / targets / bounded mdw / register read
     -> GdbMiBackend
        -> symbols / frame / stack / registers / variable
        -> hardware breakpoint / watchpoint
```

Integrated mode is loopback-only. Remote operation continues to use the explicit
OpenOCD GDB-server flow and should be transported through SSH/VPN when possible.

## Safety invariants

1. No raw TCL or raw GDB console is exposed by integrated CLI.
2. No erase/program/mass-erase/Option Bytes/WRP operation exists in the debug path.
3. `break` uses `-break-insert -h` only; software breakpoints are not used.
4. `watch` accepts only validated simple expressions.
5. CPU state is read from OpenOCD `targets`; `poll` is not a CPU-state source.
6. Transient `unknown` after OpenOCD READY is tolerated only within a bounded wait.
7. Initial target state is captured before GDB attach. A target that started
   `running` is resumed after one-shot diagnostics.
8. Breakpoint/watchpoint stop reason and resource number must match the resource
   created by the current transaction. Resource deletion and resume run in cleanup.
9. AXF/ELF symbol results are trusted only when the symbol file matches the flashed
   binary; hardware acceptance compares Flash machine code when selecting symbols.

## Hardware acceptance — 2026-08-29

Probe/target: ST-Link V2 + STM32F407, ~3.07 V. OpenOCD reports 6 hardware
breakpoints and 4 watchpoints. Application vector remains valid:

- MSP: `0x200185C8`
- Reset vector: `0x08010361`

Verified operations on the real board:

- OpenOCD listeners `3333`/`6666` and Safe TCL target state.
- `debug read-words 0x08010000 2` returned the exact application vector.
- `debug registers` returned live Cortex-M4 registers and restored `running`.
- Flashed machine code at `0x0802AA80` matched only
  `B300-Main-Custom/Objects/F407/Main_V2_F407.axf` among the tested AXF files.
- `debug where` resolved `vApplicationIdleHook` -> `User/main.c:87`.
- `debug stack` resolved FreeRTOS/task frames including `Task_LOG`.
- `debug variable --expression bRUN` returned `BSP_IO_RESET`.
- Hardware breakpoint one-shot hit `vApplicationIdleHook`, verified
  `breakpoint-hit`, deleted breakpoint, and restored target to `running`.
- Hardware watchpoint one-shot on `xTickCount` hit `xTaskIncrementTick` at
  `FreeRTOS Source/tasks.c:2813`, captured the value while halted, deleted the
  watchpoint, and restored target to `running`.
- After final acceptance, both TCP ports 3333 and 6666 were closed.

No flash erase/program, mass erase, WRP, Option Bytes or RDP changes were run as
part of these debug acceptance tests.

## Release gates

- Focused debug regression must pass.
- Full unit regression must pass on the feature branch and merged main.
- `git diff --check` and Python compile checks must pass.
- GitHub CI must pass Windows x64, Ubuntu x64 and Ubuntu ARM64 before tagging.
- Publish workflow must preserve the v0.6.1 updater compatibility split:
  `latest.json` for legacy-compatible GUI and `latest-cli.json` for CLI.
- After publish, unchanged GUI 0.5.3 updater must still discover v0.7.0.
