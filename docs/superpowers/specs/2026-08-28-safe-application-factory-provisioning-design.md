# Safe Application and Factory Provisioning Design

## Scope

The tool supports two deliberately separate workflows for the B300
STM32F407ZET6 512-KiB target:

- Application provisioning preserves Bootloader sectors 0--2, erases metadata
  and Application sectors 3--7, programs/verifies an Application linked at
  `0x08010000`, resets in a separate transaction, then verifies the running
  Application.
- Factory provisioning installs one bundled, pinned Bootloader artifact into
  sectors 0--2. It is exposed only as `provision-bootloader` and as a separate
  GUI tab guarded by dry-run plus typed acknowledgement.

No workflow uses mass/chip erase, RDP lock/unlock, `stm32f2x lock/unlock`, or
unrestricted Option Byte writes. Normal Application provisioning never emits a
WRP command.

## Application workflow

The unsupported legacy marker/BKP4R contract is removed. The success
sequence is:

1. stage and revalidate the approved Application HEX;
2. revalidate the exact F407/512-KiB target;
3. run one OpenOCD process containing exactly `flash erase_sector 0 3 7` and
   `program {...} verify`;
4. require the exact `** Verified OK **` event;
5. run `reset run` in a separate conditional process;
6. perform a read/halt/resume post-check without another reset, requiring PC in
   `0x08010000..0x0807FFFF` and real recovery slot BKP1R to be zero.

## Factory workflow

Factory policy accepts data records only in
`0x08000000..0x0800BFFF`, requires the image to begin at `0x08000000`, and uses
only the bundled artifact whose manifest and compiled-in SHA-256 trust anchor
match. The successful hardware sequence is:

1. validate the bundled manifest, artifact SHA-256, address range, and vector;
2. inspect target identity, flash size, and sector protection;
3. when any sector 0--2 is protected, run only
   `flash protect 0 0 2 off`, then `reset halt` to reload STM32F4 Option Bytes;
4. re-inspect and require sectors 0--2 unprotected before any erase;
5. run `flash erase_sector 0 0 2` plus `program {...} verify` and require exact
   verified success;
6. run only `flash protect 0 0 2 on`, then `reset halt` to reload Option Bytes;
7. re-inspect and require sectors 0--2 protected;
8. run the final `reset run`, then re-inspect protection once more.

If this tool disabled WRP and a later program/verify phase fails, it makes one
best-effort `protect ... on` recovery attempt. This is protection restoration,
not a flash retry. It never retries erase/program and never expands the sector
range. Every failure reports the phase, reason, next action, and protection
restoration state.

Factory dry-run performs no target access. It displays the target-inspection
boundary and all potentially mutating OpenOCD transactions with their
conditions. Real CLI execution requires `--confirm-factory-provision`.

## Trusted artifact

The candidate is the sibling repository's tracked
`firmware/bootloader/BOOTLOAER/bootloader_std.hex` at source commit
`19b42d8ec30e700a9c6bb7772e444fa538adca03`. That commit changes the HEX with
the `bootloader_std` project, B300 COM3/version profile, and current Bootloader
source; no tracked Bootloader file changes afterward. The artifact SHA-256 is
`c0fc6083eeba39ed5f2af40d97eca90feae15e4b1d32b150c1504869d18d9398`, its data
range is `0x08000000..0x08004B1B`, and its vector is plausible for F407 SRAM and
Bootloader flash. The resource manifest records this evidence. Packaging fails
closed if any resource or manifest trust field differs.

## GUI and updater

The existing Application tab keeps its current guided workflow with marker
language removed. A separate Factory/Bootloader tab shows the immutable bundled
artifact identity, warning, plan, dry-run control, exact typed acknowledgement,
and a distinct Factory Provision button. Real action is disabled until the
target was inspected, dry-run was shown for the current probe/artifact, and the
acknowledgement matches.

The signed updater already opens a visible update dialog after an enabled
startup check and does not silently install. Its signature/hash architecture is
unchanged. Debug remains loopback-first with remote GDB explicitly enabled and
Telnet/TCL disabled remotely.

## Verification

Unit tests cover policy ranges, exact OpenOCD command sequences, confirmation
gates, service failure behavior, GUI separation, trusted resource validation,
and Windows/Linux package inclusion. The complete offline suite is run with
`python -m unittest discover -s tests -q`. No hardware acceptance claim is made
without later board testing.
