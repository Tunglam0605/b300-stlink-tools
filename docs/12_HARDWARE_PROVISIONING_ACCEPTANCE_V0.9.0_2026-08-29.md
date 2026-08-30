# B300 ST-Link Tools v0.9.0 — Hardware Provisioning Acceptance

Date: 2026-08-29

Release candidate branch: `develop/v0.9.0`

Tested commit/artifact source: `f0d0285300a6e7c58601c0809fb2339ddce3290f` (`dry-run-windows` GitHub Actions artifact)

Target: STM32F407, 512 KiB Flash, ST-LINK V2J35S7, ~3.08 V.

## Artifacts

- Trusted Bootloader: `b300_bootloader_f407ze_com3_v00060500.hex`
  - firmware version: `0x00060500`
  - SHA-256: `085E44E8339D21EE2D136D11F86C2103295812CB2438807774B232647D3F75A1`
- Application: `B300-Main-Custom/Objects/F407/Main_V2_F407.hex`
  - file SHA-256: `CAA34896FD8850C06FE8AD084635FCABFBF18AED11C9DE01406C7703D5A04770`
  - canonical Application span: `0x08010000..0x0802EE73`
  - canonical size: `126580` bytes
  - canonical CRC32: `0xC99ED31F`
- Debug symbols: `B300-Main-Custom/Objects/F407/Main_V2_F407.axf`
  - SHA-256: `08BC6BF5FC17F0728693EE92F4B63422C682B5238945E2504A8ED217213C8944`

A full 512 KiB pre-test Flash backup was captured before destructive operations.
Backup SHA-256: `197DD03BCCE31699F15F9309FC4A640C7A6F6C7C346032E4B08526BB49F297EE`.

## 1. Pre-test state

Read-only inspection before Factory provisioning:

- STM32F407 / 512 KiB: PASS
- RDP enabled: no
- WRP S0-S2: ON
- S3-S7: unprotected
- Application vector at `0x08010000`: valid
- metadata: ERASED
- board S0-S2 matched public Bootloader v0.5.0.1 bit-for-bit

## 2. Factory provisioning Bootloader v0.6.5

Product policy: the Bootloader is publisher-controlled. Each release contains one pinned trusted Bootloader artifact selected by the publisher; end users cannot import, browse to, or replace the Bootloader with an arbitrary artifact outside the official release.

Command path: CI-built `b300-stlink.exe provision-bootloader --confirm-factory-provision --json`.

Observed transaction:

1. target/protection precheck: PASS
2. WRP S0-S2 temporarily disabled: PASS
3. erase S0-S2 once: PASS
4. `flash write_image` trusted v0.6.5 Bootloader: PASS
5. `verify_image`: `** Verified OK **`
6. WRP S0-S2 restored: PASS
7. post-reset WRP verification: PASS

Independent post-Factory readback of S0-S2:

- board SHA-256: `1250392A70528B3CACA99F2B7123688A211A1A2E28A130A2DE2BE68CB8C34D58`
- expected trusted materialized S0-S2 SHA-256: same
- bit-for-bit match: PASS

## 3. First strict ST-Link Application provisioning

CI-built `b300-stlink.exe flash Main_V2_F407.hex --json` completed successfully.

Observed contract:

- S0-S2 remained protected and untouched
- S3-S7 erased once
- Application program: PASS
- Application verify: `** Verified OK **`
- STLM metadata write/readback: exact 44 bytes
- written metadata: `STLM + VERIFIED`, sequence `1`
- Bootloader confirmation: `STLM + CONFIRMED`, sequence `2`
- image size remained `126580`
- image CRC32 remained `0xC99ED31F`
- PC after confirmation: `0x08025FDE` (Application)
- BKP1R: `0`
- final WRP: S0-S2 protected

Independent Flash readback after provisioning:

- Bootloader S0-S2 bit-for-bit match trusted v0.6.5: PASS
- Application span bit-for-bit match canonical materialized HEX: PASS
- Application readback CRC32: `0xC99ED31F`

## 4. Reset persistence

Three consecutive reset/run cycles were executed after the first provisioning.

For all three cycles:

- metadata state: `CONFIRMED`
- sequence: `2`
- CRC32: `0xC99ED31F`
- target state after boot: `RUNNING`

Result: PASS. Normal resets do not mutate an already confirmed STLM record.

## 5. Second ST-Link Application provisioning / sequence continuity

The same Application was provisioned again using the CI-built CLI.

Expected and observed sequence lifecycle:

- prior: `CONFIRMED seq=2`
- tool wrote: `VERIFIED seq=3`
- Bootloader confirmed: `CONFIRMED seq=4`

Additional result:

- Application running: true
- BKP1R: `0`
- WRP S0-S2: protected
- image CRC32: `0xC99ED31F`

Result: PASS.

## 6. Debug regression after Bootloader/Application replacement

Hardware self-test after strict v0.6.5 provisioning:

- Gateway ready: PASS
- initial target state RUNNING: PASS
- AXF ↔ Flash: 4/4 sample windows match
- external Client attach: PASS
- source/stack/register inspection: PASS
- `xTickCount` variable: PASS
- hardware breakpoint: PASS
- hardware watchpoint: PASS
- final target state RUNNING: PASS
- ports 3333/6666 released: PASS

This confirms the new provisioning path did not regress Local/Gateway/Client debug behavior.

## 7. BOOT_REQUEST one-shot handoff

A one-shot Bootloader request (`BKP0R = 0x54444B42`) was injected while backup-domain write access was already enabled by the Application.

Observed:

- first reset PC: `0x08002EB2` (Bootloader region)
- BKP0R after Bootloader consumption: `0x00000000`
- second reset: Application returned to RUNNING
- metadata remained `STLM + CONFIRMED`, sequence `4`, CRC32 `0xC99ED31F`

Result: PASS.

## 8. Application update-check request without OTA server

The real Application variable `OTA_APP_UpdateCheckRequest` was changed from `false` to `true` through the debug path. Task_OTA therefore exercised the normal Application-owned request flow.

Observed:

- request variable accepted
- after ~0.8 s PC: `0x08002ECC` (Bootloader)
- BKP2R and BKP3R already consumed to zero
- no OTA session was established
- after the Bootloader update-check window (~3 s), target returned to Application
- return PC observed at `0x08024954`
- FreeRTOS resumed (`xTickCount` readable)
- final metadata remained `CONFIRMED seq=4`

Result: PASS for update-check/no-server fallback.

## 9. Final board state

Final read-only checks after all tests:

- target: STM32F407 / 512 KiB
- voltage: ~3.08 V
- RDP: disabled
- WRP S0-S2: ON
- Application vector: valid
- metadata: VALID / STLM / CONFIRMED
- metadata sequence: `4`
- metadata CRC image: `0xC99ED31F`
- debug poll: target `RUNNING`

## Deferred field acceptance after v0.9.0 release

The following are deliberately **not** marked PASS by this acceptance run and remain scheduled for later hands-on field testing because this release session is being completed remotely:

- real cold power-cycle (power removed and restored), then metadata/Application validation
- full OTA image transfer through the real OTA server/gateway, including OTA → ST-Link → OTA interoperability

These two items are **deferred, not waived as successful**. The v0.9.0 release notes must keep their status explicit until they are executed on final hardware. Real SSH forwarding/debug correctness for GUI Client, CLI Client and VS Code/Cortex-Debug is covered separately by `docs/13_SSH_LOOPBACK_ACCEPTANCE_V0.9.0_2026-08-29.md`.
