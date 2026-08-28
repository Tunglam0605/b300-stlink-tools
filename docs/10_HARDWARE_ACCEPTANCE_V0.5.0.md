# Hardware Acceptance — B300 ST-Link Tools v0.5.0

Date: 2026-08-28

## Scope

This acceptance closes the OTA/ST-Link coexistence gap found during the v0.5.0 audit and validates the updated trusted Bootloader on a real STM32F407ZE B300 board.

## Trusted Bootloader

- Firmware version: `0x00050001`
- Protocol version: `0x00030000`
- Source repository: `Tunglam0605/TungLamvsOTA-B300`
- Source commit: `92e70f8e1cc94c17be39034fcc9a20e385325a2f`
- Canonical Git blob: `b4e5be928a7524d566564b1b2b980ce854bfe68f`
- Artifact SHA-256: `657F71605E00795BEA3C5601AAF569104E74D9DEE8D5B6E602514C4D72264F05`
- Data range: `0x08000000..0x08004B4F`
- Data bytes: `19280`
- Linker maximum: `0x08000000 + 0x0000C000` (Sector 0-2 only)

Keil ARMCC5 build completed with `0 Error(s), 0 Warning(s)`. Total ROM size: 19,280 bytes.

## OTA / ST-Link coexistence fix

The Bootloader now recognizes ST-Link provisioning only when both conditions are true:

1. all 44 bytes of managed OTA metadata in Sector 3 are erased (`0xFF`), and
2. the Application vector table at `0x08010000` is valid.

Only in this state is stale `BKP1R` recovery cleared. Explicit Application requests already captured from `BKP0R` / `BKP2R` / `BKP3R` retain priority. Managed `IN_PROGRESS`, corrupt metadata, CRC failure, and invalid vector paths remain fail-closed.

## Software validation

- Bootloader source guards: 22/22 PASS.
- Raspberry Pi gateway/OTA suite: 178 PASS, 3 skipped.
- Trusted-resource / packaging / GUI focused suite: 60/60 PASS.

## Hardware validation

Target:

- STM32F407, device ID low 12-bit `0x413`
- Flash: 512 KiB
- ST-Link V2, VID:PID `0483:3748`
- Target voltage approximately 3.08 V
- RDP: Level 0

Factory transaction using the v0.5.0 backend:

1. Verified S0-S2 initially WRP protected.
2. Temporarily disabled WRP S0-S2.
3. Erased S0-S2 only.
4. Programmed and OpenOCD verified the trusted Bootloader.
5. Re-enabled WRP S0-S2.
6. Post-reset inspection confirmed S0-S2 protected and S3-S7 unprotected.

Result: PASS.

### Injected stale-recovery test

Precondition: Sector 3 metadata classified `ERASED` and Application vector valid.

Injected through SWD:

```text
BKP1R = 0x5241544F
```

After reset and 800 ms execution:

```text
BKP1R = 0x00000000
PC     = 0x0802AA8E
```

The PC is inside the Application range, proving the stale recovery marker no longer competes with a fresh ST-Link provisioned Application.

### Bit-for-bit Bootloader verification

A 48 KiB dump of S0-S2 after provisioning matched the trusted HEX reconstructed with erased fill bytes exactly.

```text
Expected full S0-S2 SHA-256:
89D120224EDECAF4137FAD9F815A3FE810CB1C52589B7DD46E920189D595E910

Actual board S0-S2 SHA-256:
89D120224EDECAF4137FAD9F815A3FE810CB1C52589B7DD46E920189D595E910

FULL_MATCH = True
```

Final target inspection:

- S0-S2 protected.
- S3-S7 not protected.
- RDP Level 0.
- Metadata S3: `ERASED`.
- `BKP1R = 0`.

## Acceptance conclusion

The updated Bootloader and B300 ST-Link Factory workflow satisfy the current coexistence contract: OTA-managed metadata remains authoritative when present, while a fresh ST-Link provisioning operation with erased metadata cannot be trapped by an old recovery marker. No mass erase or RDP mutation is used.

The current design still does not implement dual-image rollback/trial-boot confirmation; `OTA_STATE_VERIFIED` remains reserved for a later rollback phase.
