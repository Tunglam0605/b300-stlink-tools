---
name: b300-ota-stlink
description: Use when provisioning or verifying a B300 STM32F407 through ST-Link, monitoring a running target, or opening LOCAL, GATEWAY, or CLIENT debug with B300 v0.18+.
---

# B300 ST-Link Tools v0.18+

Use the GUI for the shortest setup and daily workflow. Use `b300-stlink` for
terminal automation and structured `--json` evidence. The safety boundary is
fixed: Bootloader is Sector 0--2; metadata is Sector 3; Application is Sector
4--7 (`0x08010000..0x0807FFFF`).

## Before flash

1. Identify the board, Application HEX, and probe serial when multiple probes
   are present.
2. Run `b300-stlink doctor --json`.
3. Run `b300-stlink flash <application.hex> --dry-run --json`.
4. Confirm the plan contains `flash erase_sector 0 3 7`, Application
   `flash write_image`, `verify_image`, the exact 44-byte metadata write/read-back,
   and `reset run` only after verification.

The preview must show two separate conditional transactions: program/verify and
reset with `condition=after_verified_ok`.
Before erase, the core must re-check an F407/512-KiB target and stage/revalidate
the approved HEX hash and address range.

Do not flash if HEX validation fails or the command includes mass/chip erase,
Sector 0--2, Option Bytes, or WRP changes. Dry-run is safe; actual flash erases
Sector 3--7 and requires the user's explicit authorization for that board and
file in the current session.

## Flash and confirm

Run `b300-stlink flash <application.hex> --json` only after authorization. Do
not retry automatically on any phase failure. Success requires the exact
`** Verified OK **` event, successful reset, and post-verify PC/BKP1R state.
On failure, report `failure_phase`, `reason`, and `next_action` from JSON.

Sector 3 is erased with the Application domain, but Bootloader v0.6.5 is strict:
`ERASED`/`CORRUPT` metadata is not bootable. After exact Application verification,
the tool must write and independently read back exactly 44 bytes of `STLM + VERIFIED`
at `0x0800C000`, then reset. Success additionally requires Bootloader consumption to
`STLM + CONFIRMED` with matching image size/CRC, the expected sequence successor,
Application PC, and BKP1R = 0. Do not use CRC workarounds or backup-register markers.

## Factory / Bootloader

`provision-bootloader` is a separate factory-maintenance command, never a
fallback for normal Application flash. It uses only the bundled trusted
Bootloader. When WRP must change, each `flash protect 0 0 2 off/on` is followed
by a reset/halt so STM32F4 reloads the Option Bytes; the tool verifies the new
state before continuing. WRP is restored and verified before the final run. Start with:

```text
b300-stlink provision-bootloader --dry-run --json
```

Real factory programming additionally requires explicit authorization from the
user and `--confirm-factory-provision`. Select `--probe-serial` when multiple
probes are present; one physical probe without a serial may use safe auto-select.
The GUI requires the exact typed acknowledgement `PROVISION BOOTLOADER`. Never use it for an
ordinary Application update. It must not use mass erase, RDP operations,
`stm32f2x lock`, or `stm32f2x unlock`.

## Monitor and debug

Choose one v0.18 role:

- **LOCAL**: ST-Link is attached to this computer. Select the matching ELF/AXF,
  then use Live Monitor or **Open Debug in VS Code**.
- **GATEWAY**: ST-Link is attached to this computer for a remote operator. Run
  `b300-stlink debug gateway`; OpenOCD remains loopback-only.
- **CLIENT**: source and ELF/AXF are local, while ST-Link is on a Gateway. Use the
  saved SSH profile and `b300-stlink debug client` or `debug vscode`.

VS Code + Cortex-Debug is the normal interactive-debug UI. B300 owns ST-Link,
OpenOCD, SSH forwarding, attach-only launch configuration, HardwareSession, and
RUNNING restoration. Live Monitor uses Safe TCL and must not halt the target.
Manual GDB and one-shot diagnostics are Advanced workflows.

`debug server` is a deprecated alias for `debug gateway`; do not use it in new
scripts. Both stay loopback-only. Never expose or NAT GDB/TCL, remotely forward
TCL for VS Code, enable Telnet, or run GDB `load`, `restore`, or flash commands.

## Detailed references

- Read [Safety and authorization](references/safety.md) for recovery/verification
  rules and prohibited actions.
- Read [Commands by platform](references/commands.md) for Windows, Ubuntu IPC,
  logs, probe serial, and common errors.
