# PROGRAM / DEVICE preflight audit

Baseline: v0.19.0, `e89e23a980a1fe0f28d80997626ae83a4c8ac247`.
Scope: GUI state and read-only Application preflight. No release or hardware flash.

## Root cause (before implementation)

- `ProgramView._build_device_card` and `set_target_info(None)` hard-code
  `STM32F407ZET6`. This is an expected B300 part, not inspected evidence,
  a profile, or a conclusion from the HEX. The label does not explain that.
- Top target/flash cards are initialized/cleared by `StatsRow.clear_target` and
  populated by `MainWindow.apply_target_info`. They correctly remain unread
  until the shared `target_info` exists.
- `ProgramView.set_busy` enables Application flash for a selected valid HEX
  without target readiness. `_on_v18_flash_application` then checks cached
  `target_ready` and `flash_plan`, displays FAIL and sends the user to DEVICE.
  It never calls inspect. This is a GUI orchestration/UX regression exposed by
  moving the manual inspect action to DEVICE, not a core flash-policy failure.
- DEVICE inspection already publishes the same immutable `TargetInfo` through
  `MainWindowV18.apply_target_info` to PROGRAM, DEVICE and the top cards.
  No page reopen is needed. Page fields are rendering caches, not independent
  inspection services, but their invalidation was incomplete.
- Rescan, probe change and setup completion clear main `target_info`,
  `target_ready`, plan and top cards. Base `_clear_target_display` does not clear
  the production PROGRAM/DEVICE caches. Those pages can show stale evidence.
- Valid HEX changes rebuild the main plan; invalid selection returns from
  ProgramView before notifying main, leaving the previous image/plan cached.
- External reconnect/reset has no push notification into GUI. Cached evidence
  cannot prove current hardware. Core `B300Service.flash` already stages and
  compares the approved image, acquires HardwareSession, re-inspects target and
  validates identity/WRP/RDP before erase. That transaction is unchanged.
- PROGRAM/DEVICE also label every inspected device F407, including mismatches.

## Correction

Keep `MainWindow.target_info` as the canonical inspection result. Clear every
renderer together, display unread as neutral, and label actual mismatches with
their observed ID. Propagate invalid image selections. Each Application click
reloads the HEX and runs the existing service inspection in a worker, then
builds the canonical plan before confirmation. Capture probe/context so canceled
or stale results cannot proceed. Use GUI and HardwareSession busy interlocks.
DEVICE retains the only visible manual inspection action. The flash transaction
and its independent final revalidation remain untouched.

## Initial reproduction

New `tests.test_program_preflight` fails on v0.19.0: an already cached PASS
skips inspection (zero service calls). The suite also exercises fresh readiness,
state propagation/invalidation, WRP/identity rejection and shared hardware busy.

## Hardware acceptance

HW-P1-001 remains **OPEN / DEFERRED**. No hardware acceptance is claimed.
Operator acceptance must verify the chosen physical probe/board and HEX,
automatic preflight, confirmation/cancel, disconnect/reconnect, and approved
canonical programming on a test board. External resets cannot be detected
continuously from a historical snapshot; every flash request re-inspects.
