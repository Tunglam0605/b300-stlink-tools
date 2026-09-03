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
   `program {...} verify`, and `reset run`.

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
user, `--confirm-factory-provision`, an explicit CLI `--probe-serial`, and the
exact typed GUI acknowledgement `PROVISION BOOTLOADER`. Never use it for an
ordinary Application update. It must not use mass erase, RDP operations,
`stm32f2x lock`, or `stm32f2x unlock`.

## Debug

Use `b300-stlink debug --gdb-port 3333` only when halting or
resetting the CPU is acceptable. Debug does not flash, but a connected debugger
can halt/reset the board. It binds to `127.0.0.1` by default. For remote work,
run `b300-stlink debug gateway` with GDB/TCL still on loopback, then use
`b300-stlink debug client` or an SSH/VPN local-forwarding workflow. Do not expose
or NAT GDB/TCL ports to the LAN or Internet. Keep Telnet disabled.

Use the AXF/ELF matching the firmware already on the board for symbols. Do not
run GDB `load`, `restore`, or flash commands. Before stopping OpenOCD, resume the
target, detach GDB, and confirm the server ports close.

## Detailed references

- Read [Safety and authorization](references/safety.md) for recovery/verification
  rules and prohibited actions.
- Read [Commands by platform](references/commands.md) for Windows, Ubuntu IPC,
  logs, probe serial, and common errors.
