---
name: b300-ota-stlink
description: Use when provisioning a B300 STM32F407 Application HEX through ST-Link, checking a safe flash transaction, verifying post-flash boot state, or starting OpenOCD debugging.
---

# B300 ST-Link Provisioning

Use `b300-stlink` for B300 F407 Application provisioning. The safety boundary is
fixed: Bootloader is Sector 0--2; metadata is Sector 3; Application is Sector
4--7 (`0x08010000..0x0807FFFF`).

## Before flash

1. Identify the board, Application HEX, and probe serial when multiple probes
   are present.
2. Run `b300-stlink doctor --json`.
3. Run `b300-stlink flash <application.hex> --dry-run --json`.
4. Confirm the transaction contains only `flash erase_sector 0 3 7`,
   `program {...} verify`, `mww 0x40002860 0x53544C4B`, and `reset run`.

Do not flash if HEX validation fails or the command includes mass/chip erase,
Sector 0--2, Option Bytes, or WRP changes. Dry-run is safe; actual flash erases
Sector 3--7 and requires the user's explicit authorization for that board and
file in the current session.

## Flash and confirm

Run `b300-stlink flash <application.hex> --json` only after authorization. Do
not retry automatically on an erase/program/verify failure. Success requires
OpenOCD `Verified OK` and exit code zero.

The marker is written after verify; Bootloader consumes it on reset so a valid
ST-Link provisioned Application is not treated as an interrupted OTA.

## Debug

Use `b300-stlink debug --gdb-port 3333 --telnet-port 4444` only when halting or
resetting the CPU is acceptable. Debug does not flash, but a connected debugger
can halt/reset the board. Stop OpenOCD cleanly when finished.

## Detailed references

- Read [Safety and authorization](references/safety.md) for recovery/verification
  rules and prohibited actions.
- Read [Commands by platform](references/commands.md) for Windows, Ubuntu IPC,
  logs, probe serial, and common errors.
