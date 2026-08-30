# GUI runtime hardening acceptance — post v0.9.0

## Scope

Mốc này chỉ thay đổi code của **B300 ST-Link Tools GUI**. Không sửa Bootloader, Application firmware hoặc bất kỳ transaction Flash/Option Bytes nào.

## Runtime hardening

### Bounded Realtime Timeline

- Execution Timeline giữ tối đa `1000` sample gần nhất.
- Khi vượt capacity, GUI trim một phần history theo lô thay vì xóa row đầu ở từng sample.
- Các cột Time/PC/Line dùng fixed width, File dùng interactive width và Function dùng stretch.
- Không dùng `ResizeToContents` trên hot path 10 Hz.

Synthetic benchmark trên máy test:

- trước tối ưu: 5000 timeline events ~58.7 s;
- sau tối ưu: 5000 timeline events ~0.75 s;
- retained rows cuối benchmark: 984/1000.

### Bounded GUI Logs

- Main OpenOCD log: tối đa 10000 document blocks.
- Debug Technical Log: tối đa 5000 document blocks.
- Synthetic main-log test 12000 events: retained 10000 blocks, ~1.53 s.

## Software gates

- GUI regression: **79 PASS**.
- Full regression: **661 PASS, 2 skipped, 0 failures**.
- GUI smoke: PASS.
- `compileall`: PASS.
- `git diff --check`: PASS.

## Hardware GUI soak — B300 STM32F407

Matching symbols:

`C:\Users\Admin\Documents\STM32\B300-Main-Custom\Objects\F407\Main_V2_F407.axf`

Run:

- Local Realtime Live Monitor;
- interval `0.1 s` (10 Hz);
- 300 samples;
- watches: `xTickCount:u32`, `bRUN:u8`, `v_current:f64`.

Result:

- 300/300 samples PASS;
- 0 overrun;
- 300 timeline rows;
- 900 variable points / plot points;
- `xTickCount` 7603078 → 7632967;
- measured tick-rate ~999.93 Hz;
- final target `RUNNING`;
- after completion GDB 3333 closed, TCL 6666 closed, Live session released.

## Start/Stop lifecycle stress

10 consecutive cycles on the same `DebugTab` instance:

1. Start Live with interval 5 s.
2. Wait for first sample.
3. Verify GDB 3333 closed and TCL 6666 open.
4. Stop Live.
5. Verify worker/session release and both ports closed.

Result: **10/10 PASS**. Cooperative stop measured roughly 15–32 ms per cycle. `prepare_shutdown()` returned true and no OpenOCD process remained afterward.

## Independent target post-check

`target inspect --json` after soak/stress confirmed:

- STM32F407 / 512 KiB;
- WRP S0-S2 protected;
- RDP disabled;
- Application vector valid;
- metadata `VALID / CONFIRMED`;
- classification `READY_FOR_APPLICATION_FLASH`.

No firmware source was changed and no Flash/erase/reset/Option Bytes operation was performed by these hardening tests.
