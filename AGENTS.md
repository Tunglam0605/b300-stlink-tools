# Playbook vận hành cho AI agent

Đọc file này trước khi AI agent chạy bất kỳ lệnh nào trong repo hoặc trên máy
có gắn ST-Link. Mục tiêu là nạp **Application B300 F407** an toàn, giữ nguyên
Bootloader và không làm bootloader hiểu lần nạp ST-Link là OTA lỗi.

## 0. Chọn bản tải / artifact

Khi AI agent được yêu cầu **tìm, tải hoặc cài B300 ST-Link Tools**, phải đọc
[DOWNLOAD.md](DOWNLOAD.md) trước. Quy tắc mặc định: Stable/Latest + GUI + artifact
đúng OS/CPU; CLI chỉ khi user yêu cầu terminal/headless/automation. Với Linux phải
xác định `uname -m`: `x86_64` -> x64/amd64, `aarch64`/`arm64` -> arm64.
Không bao giờ chọn `Source code (zip)` hoặc `Source code (tar.gz)` làm installer.
Automation nên đọc signed `latest.json` thay vì scrape HTML Release. Exact version
phải pin tag `vX.Y.Z` và không tự đổi sang Latest.

## 1. Phạm vi và các điều cấm

| Vùng flash | Ý nghĩa | Quy tắc |
|---|---|---|
| Sector 0--2, `0x08000000..0x0800BFFF` | Bootloader | Tuyệt đối không erase/program. |
| Sector 3, `0x0800C000..0x0800FFFF` | OTA metadata | Chỉ xóa bởi transaction flash chuẩn. |
| Sector 4--7, `0x08010000..0x0807FFFF` | Application | Là vùng HEX được phép nạp. |

AI agent không được dùng `mass_erase`, chip erase, sửa Option Bytes/RDP, gọi
OpenOCD thủ công để bỏ validate HEX, tự retry sau
lỗi flash, hoặc dùng `sudo b300-stlink` để lách quyền USB.

Không commit firmware HEX, binary OpenOCD, Keil objects/build artifacts hoặc
release archive vào Git source repository.

## 2. Bắt buộc trước mọi flash

1. Xác định rõ board, file HEX và probe được phép dùng.
2. Chạy `b300-stlink doctor --json`.
3. Nếu có nhiều ST-Link, yêu cầu hoặc xác minh `--probe-serial`.
4. Chạy dry-run:

   ```text
   b300-stlink flash <application.hex> --dry-run --json
   ```

5. Output phải có chính xác:

   ```text
   flash erase_sector 0 3 7
   program {...} verify
   reset run
   ```

   Đây là hai transaction nối tiếp có điều kiện. Reset chỉ chạy sau exact
   `** Verified OK **`; normal flow không ghi backup register hay WRP.

Nếu transaction khác, HEX bị từ chối, hoặc có `mass_erase`/Sector 0--2: dừng
và báo lỗi. Không sửa transaction để ép nạp.

Dry-run là read-only. Flash thật xóa Sector 3--7, chỉ chạy khi người dùng đã
xác nhận rõ file/board được phép nạp trong phiên hiện tại.

## Factory / Bootloader provisioning

`provision-bootloader` là workflow duy nhất được phép thay đổi WRP, chỉ dành cho
main/chip mới hoặc bảo trì Bootloader được ủy quyền. Dùng artifact bundle có
hash/provenance cố định, dry-run trước, rồi chỉ chạy lệnh thật với
`--confirm-factory-provision`. CLI thật phải chọn đúng một probe vật lý: khi chỉ có
một probe không có serial, `ProbeRef(None)` là hợp lệ; khi có nhiều probe thì phải
pin chính xác bằng `--probe-serial`, không được bịa serial từ USB identity. Nó chỉ
`flash protect 0 0 2 off/on`, reset/halt để reload Option Bytes sau mỗi thay đổi
WRP, verify trạng thái, erase/program đúng S0--S2, restore/verify WRP rồi mới
`reset run`. Không mass erase, không thay RDP và không `stm32f2x lock/unlock`.
GUI còn yêu cầu nhập đúng `PROVISION BOOTLOADER`.

## 3. Flash thật

1. Chạy và lưu log:

   ```text
   b300-stlink flash <application.hex> --json
   ```

2. Không chạy OpenOCD/ST-Link song song.
3. Chỉ báo thành công khi có exact `** Verified OK **`, reset thành công
   và post-verify xác nhận PC/BKP hợp lệ.
4. Nếu lỗi: dừng, giữ log, báo `failure_phase`, `reason`, `next_action`; không retry mù.

Sector 3 được erase cùng Application nên Bootloader dùng erased-metadata
fallback hiện có. Không tạo synthetic OTA metadata, CRC workaround hay marker.

## 4. Xác minh sau flash khi user yêu cầu

Có thể dùng OpenOCD read-only, rồi `resume` trước disconnect. Điều kiện pass:

- `BKP1R` (`0x40002854`) là `0x00000000`;
- PC nằm trong Application `0x08010000..0x0807FFFF`.

Không ghi register/reset board chỉ để xác minh khi chưa được phép.

## 5. Debug

Debug không flash nhưng GDB có thể halt/reset CPU; báo trước nếu board điều khiển
cơ cấu thật.

1. Có thể dry-run: `b300-stlink debug --dry-run --json`.
2. Local dùng mặc định loopback:
   `b300-stlink debug --gdb-port 3333`.
   Khi cần OpenOCD TCL automation local, dùng:
   `b300-stlink debug --gdb-port 3333 --tcl-port 6666`.
3. Remote qua IPC chỉ khi user cho phép và mạng tin cậy:
   `b300-stlink debug --bind-address 0.0.0.0 --gdb-port 3333`.
4. Telnet/TCL phải giữ disabled cho remote; không lách validation để mở cổng.
   Các debug port đang bật phải khác nhau; `3333`/`6666` là cặp chuẩn local.
5. Dùng đúng AXF/ELF tương ứng để đọc symbol. Không chạy GDB `load`, `restore`
   hoặc lệnh flash trong mode debug. Integrated CLI one-shot hỗ trợ `where`,
   `stack`, `registers`, `variable`, `read-words`, `break` và `watch`; phải giữ
   loopback `3333/6666`.
6. `debug break` chỉ được dùng hardware breakpoint (`-break-insert -h`).
   `debug watch` chỉ dùng expression allow-list. Cả hai phải có timeout, xác minh
   đúng `*stopped`/resource number, xóa resource trong `finally` và resume target
   nếu trạng thái ban đầu là `running`. Không expose raw TCL hoặc raw GDB console.
7. CPU run-state phải lấy từ OpenOCD `targets`, không lấy từ `poll` vì `poll` chỉ
   phản ánh background polling/TAP. Chấp nhận `unknown` ngắn khi OpenOCD vừa READY
   bằng bounded wait; hết timeout phải fail-closed.
8. Trước khi đóng server thủ công, chạy `monitor reset run`, `detach`, `quit`;
   dừng OpenOCD và xác nhận GDB/TCL port đã đóng. Integrated one-shot tự cleanup.

## 6. Ubuntu IPC và lỗi thường gặp

Không dùng sudo cho CLI. Nếu không thấy ST-Link, đọc `lsusb`, group `plugdev`,
udev rule và replug probe. Chỉ thay đổi udev khi user cho phép.

| Dấu hiệu | Hành động |
|---|---|
| `OpenOCD was not found` | Dừng; hướng dẫn cài bundle đúng OS. |
| Không nhận ST-Link | Kiểm tra USB/driver/udev/probe serial; không flash. |
| HEX protected range | Dừng; yêu cầu đúng HEX Application `0x08010000`. |
| Verify fail | Dừng, lưu log, kiểm nguồn/cáp/probe; không retry. |
| Recovery sau flash | Dừng; không mass erase/retry; kiểm PC, BKP1R, metadata và Bootloader log. |

## 7. Source/release

Sau thay đổi source chạy:

```text
python3 -m unittest discover -s tests -q
```

Chỉ build release trên đúng OS/architecture:

```text
python3 build_native_bundle.py --internal-distribution-approved
```

Đọc theo thứ tự: [Start](docs/00_START_HERE.md),
[Flash](docs/03_FLASH_FIRMWARE.md), [Debug](docs/04_DEBUG.md),
[Troubleshooting](docs/05_TROUBLESHOOTING.md).
