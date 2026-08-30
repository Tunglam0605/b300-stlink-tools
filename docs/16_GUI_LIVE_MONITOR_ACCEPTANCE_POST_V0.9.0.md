# GUI Realtime Live Monitor acceptance — post v0.9.0

## Scope

Acceptance này xác nhận frontend redesign đã được nối vào backend **non-halting Live Monitor** thật. Nó không dùng legacy GDB `sample_variables()` cho nút **Start Live**. Interactive Debug (break/watch/step/halt) vẫn là subsystem riêng và có thể ảnh hưởng realtime.

## Software gates

- Integrated HEAD: `79a8c60` trước documentation evidence commit.
- Full regression: **659 PASS, 2 skipped, 0 failures**.
- GUI regression: **77 PASS**.
- GUI smoke: PASS.
- `compileall`: PASS.
- `git diff --check`: PASS.

## Hardware GUI test — 10 Hz

Board B300 STM32F407 thật, ST-Link local, matching symbols:

`C:\Users\Admin\Documents\STM32\B300-Main-Custom\Objects\F407\Main_V2_F407.axf`

GUI automation dùng chính `DebugTab` production:

- Mode: Local.
- Watch: `xTickCount:u32`.
- Interval: `0.1 s`.
- Samples: 20.
- Start thông qua nút/logic `start_live_sampling()`.

Kết quả:

- Start Live enabled khi Interactive Debug chưa attach.
- 20/20 samples hoàn tất.
- 0 overrun.
- Execution Timeline: 20 rows.
- Live Variables: `xTickCount`, type `u32`, address `0x20000030`; giá trị cuối quan sát `5704461`.
- Plot: 20 points.
- UI status: `Completed 20 samples · overruns 0 · target RUNNING`.
- Live session được close và release sau completion.

## Hardware Stop/transport boundary test

Bài test thứ hai đặt interval `60 s`, 100 samples để kiểm cooperative Stop:

- sau sample đầu, Live vẫn active;
- trong lúc Live chạy: GDB port 3333 **không listen**, TCL port 6666 **listen**;
- bấm Stop: worker/session hoàn tất trong khoảng **0.016 s**, không chờ hết interval 60 s;
- summary: 1 sample, 0 overrun, target `RUNNING`;
- sau Stop: cả 3333 và 6666 đều không listen.

Điều này xác nhận GUI normal Stop dùng `LiveMonitorSession.cancel()` cooperative path thay vì kill process hoặc halt/resume target.

## Independent target post-check

Sau hai GUI hardware tests, `target inspect --json` xác nhận:

- STM32F407, 512 KiB;
- WRP S0-S2 protected;
- RDP disabled;
- Application vector valid;
- OTA metadata `VALID / CONFIRMED`;
- classification `READY_FOR_APPLICATION_FLASH`.

Không có flash/erase/reset/Option Bytes operation trong acceptance này.

## Interpretation

DWT PCSR là **statistical execution sampling**, không phải instruction trace đầy đủ. SWD read traffic vẫn tồn tại, vì vậy tính năng được mô tả là **non-halting / low-intrusion**, không phải zero timing impact. Với deadline hard real-time ở mức microsecond, cần đo riêng bằng GPIO/timer/logic analyzer hoặc hardware trace phù hợp.
