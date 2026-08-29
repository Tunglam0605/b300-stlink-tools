# Hardware Debug Acceptance - B300 ST-Link Tools v0.9.0 - 2026-08-29

## Phạm vi

Biên bản này ghi lại nghiệm thu debug trên main B300 STM32F407 thật đang chạy firmware thực tế. Phiên thử **không erase/program Flash, không sửa WRP/RDP và không nạp firmware test riêng**. Mục tiêu là xác nhận Local Debug, Gateway/external Client core và VS Code/Cortex-Debug external-attach transport trước khi phát hành v0.9.0.

## Thiết bị và firmware

- MCU: STM32F407, family ID `0x413`, Flash 512 KiB.
- ST-Link: STLINK V2J35S7, VID:PID `0483:3748`.
- Target voltage: khoảng `3.08 V`.
- WRP: Sector 0-2 protected; Sector 3-7 not protected.
- Application vector: valid, MSP `0x200185C8`, Reset `0x08010361`.
- Metadata tại thời điểm thử: `ERASED`; phiên debug không thay đổi metadata.
- OpenOCD: xPack Open On-Chip Debugger `0.12.0+dev-02228-ge5888bda3-dirty`.
- GDB thực tế được B300 resolver tìm từ STM32CubeIDE 2.2.0.

## AXF exact-match

Tool quét bounded symbol candidates và chỉ chấp nhận AXF khớp Flash:

```text
C:\Users\Admin\Documents\STM32\B300-Main-Custom\Objects\F407\Main_V2_F407.axf
```

Kết quả: `4/4` sampled Application Flash windows match, score `1.0`.

Các AXF khác trong `TungLamvsOTA-B300\firmware\B300-Main-1` đều `0/4` và bị fail-closed; target được trả về `RUNNING`.

## Local source-level debug

`debug inspect` trên AXF khớp Flash đọc được:

- `vApplicationIdleHook()` tại `User\main.c:87`, PC `0x08025FDA`;
- stack tới `prvIdleTask()` trong `FreeRTOS Source\tasks.c:3483`;
- core/FPU registers, MSP/PSP, PC/SP/LR;
- target state trước operation `RUNNING` và sau operation vẫn `RUNNING`.

Đọc biến thật:

```text
xTickCount = 7918762
```

**Local source/stack/register/variable: PASS.**

## Hardware breakpoint và watchpoint

Hardware breakpoint tại `vApplicationIdleHook`:

- hit thành công;
- source `User\main.c:87`;
- PC `0x08025FDA`;
- không patch Flash.

Hardware watchpoint trên `xTickCount`:

- trigger thành công tại `xTaskIncrementTick()`;
- source `FreeRTOS Source\tasks.c:2813`;
- đọc được giá trị `xTickCount` tại thời điểm trigger.

**Hardware breakpoint/watchpoint: PASS.**

## Gateway -> external Client core selftest

`debug selftest` trên board thật đạt toàn bộ gate:

| Check | Result |
|---|---|
| Gateway loopback OpenOCD READY | PASS |
| Initial target state RUNNING | PASS |
| AXF <-> Flash exact match 4/4 | PASS |
| External Client GDB/TCL attach | PASS |
| Attach preserves target state | PASS |
| Source/stack/register inspect | PASS |
| Variable `xTickCount` | PASS |
| Break Once | PASS |
| Watch Once | PASS |
| Final target state RUNNING | PASS |
| GDB port 3333 released | PASS |
| TCL port 6666 released | PASS |

Selftest vẫn ghi đúng `field_acceptance_pending=true`, `ssh_exercised=false`, `two_machine_exercised=false` vì transport SSH hai máy chưa được chạy trong phiên này.

## Stress stability

Chạy 5 vòng hardware selftest liên tiếp, mỗi vòng gồm Gateway, external Client, symbol match, inspect, variable, breakpoint, watchpoint, state restore và port cleanup.

```text
cycle 1 PASS   xTickCount=8177027
cycle 2 PASS   xTickCount=8179080
cycle 3 PASS   xTickCount=8181197
cycle 4 PASS   xTickCount=8183320
cycle 5 PASS   xTickCount=8185386
```

`xTickCount` tăng monotonically giữa các vòng; không quan sát thấy target bị kẹt HALTED, GDB/OpenOCD session leak hoặc port leak.

**5x debug stress: PASS.**

## VS Code / Cortex-Debug path

Máy Client thử nghiệm có:

- Visual Studio Code;
- `marus25.cortex-debug` 1.12.1;
- Remote SSH;
- Memory Viewer, Peripheral Viewer và RTOS Views.

Gateway thật được chạy loopback-only:

- GDB `127.0.0.1:3333`;
- TCL `127.0.0.1:6666`;
- Telnet disabled;
- `gdb flash_program disable`;
- hardware breakpoint override.

Dùng chính `arm-none-eabi-gdb` mà B300 resolver tìm từ STM32CubeIDE để external-attach vào endpoint GDB của Gateway. Kết quả đọc được source/line, PC và biến thật:

```text
CmdResp7Error() at BSP\Center\BSP_sdio_sd.c:1906
xTickCount = 8224794
PC = 0x0801917E
```

Sau GDB detach, remote guard restore target từ `RUNNING -> RUNNING`; read-only poll sau đó xác nhận target vẫn `RUNNING`.

VS Code remote kit đã được harden và auto-resolve GDB trên Client; `launch.json` dùng `request=attach`, `servertype=external`, hardware breakpoints/watchpoints và không có load/program command.

**VS Code/Cortex-Debug external transport and generated profile: PASS.**

Lưu ý: chưa tự động hóa thao tác bấm F5 trong UI VS Code; transport, GDB semantics và launch-profile mà Cortex-Debug sử dụng đã được kiểm trực tiếp trên hardware.

## SSH / two-machine field gate

`gateway doctor` trên máy thử hiện báo:

- OpenOCD: PASS;
- ST-Link: PASS;
- GDB/TCL loopback ports: PASS;
- IPv4 candidates: `10.6.0.101`, `192.168.1.95`;
- SSH server port 22: **BLOCKED** vì Windows hiện không cài/chạy `sshd`.

Không tự ý cài OpenSSH Server hoặc mở Windows Firewall trong phiên này. Vì vậy GUI Client/CLI Client/VS Code qua **SSH giữa hai laptop thật** vẫn là field gate phải nghiệm thu riêng.

## Software regression liên quan

Sau các thay đổi v0.9.0 hiện tại:

- full regression: **557 PASS, 2 skipped**;
- GUI regression: **59/59 PASS**;
- Remote Debug focused regression: **77/77 PASS**.

## Kết luận

| Capability | Result |
|---|---|
| Local source-level debug | PASS |
| Registers / stack / source line | PASS |
| Read real variable | PASS |
| Hardware breakpoint | PASS |
| Hardware watchpoint | PASS |
| Target state restoration | PASS |
| Gateway loopback debug | PASS |
| External Client core | PASS |
| 5x repeated debug stability | PASS |
| VS Code/Cortex-Debug external attach transport | PASS |
| VS Code kit GDB auto-resolution | PASS |
| GUI/CLI software regression | PASS |
| SSH two-machine GUI Client | PENDING |
| SSH two-machine CLI Client | PENDING |
| SSH two-machine VS Code Client | PENDING |

Kết quả này đủ để xác nhận **debug core và Gateway architecture của v0.9.0 khả thi, ổn định trên B300 thật**. Stable release vẫn phải giữ fail-closed cho các gate chưa chạy: current AppMeta/Factory hardware acceptance, OTA <-> ST-Link interoperability và SSH/two-machine E2E.
