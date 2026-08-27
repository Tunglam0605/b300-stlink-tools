# Playbook vận hành cho AI agent

Đọc file này trước khi AI agent chạy bất kỳ lệnh nào trong repo hoặc trên máy
có gắn ST-Link. Mục tiêu là nạp **Application B300 F407** an toàn, giữ nguyên
Bootloader và không làm bootloader hiểu lần nạp ST-Link là OTA lỗi.

## 1. Phạm vi và các điều cấm

| Vùng flash | Ý nghĩa | Quy tắc |
|---|---|---|
| Sector 0--2, `0x08000000..0x0800BFFF` | Bootloader | Tuyệt đối không erase/program. |
| Sector 3, `0x0800C000..0x0800FFFF` | OTA metadata | Chỉ xóa bởi transaction flash chuẩn. |
| Sector 4--7, `0x08010000..0x0807FFFF` | Application | Là vùng HEX được phép nạp. |

AI agent không được dùng `mass_erase`, chip erase, sửa Option Bytes/WRP, nạp
bootloader qua tool này, gọi OpenOCD thủ công để bỏ validate HEX, tự retry sau
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
   mww 0x40002860 0x53544C4B
   reset run
   ```

Nếu transaction khác, HEX bị từ chối, hoặc có `mass_erase`/Sector 0--2: dừng
và báo lỗi. Không sửa transaction để ép nạp.

Dry-run là read-only. Flash thật xóa Sector 3--7, chỉ chạy khi người dùng đã
xác nhận rõ file/board được phép nạp trong phiên hiện tại.

## 3. Flash thật

1. Chạy và lưu log:

   ```text
   b300-stlink flash <application.hex> --json
   ```

2. Không chạy OpenOCD/ST-Link song song.
3. Chỉ báo thành công khi có `Verified OK` và exit code 0.
4. Nếu lỗi erase/program/verify: dừng, giữ log, báo bước lỗi; không retry mù.

Marker `0x53544C4B` chỉ được ghi sau program + verify. Sau reset Bootloader tiêu
thụ marker, xóa recovery marker cũ khi Application hợp lệ và chạy Application.

## 4. Xác minh sau flash khi user yêu cầu

Có thể dùng OpenOCD read-only, rồi `resume` trước disconnect. Điều kiện pass:

- `BKP1R` (`0x40002854`) là `0x00000000`;
- `BKP4R` (`0x40002860`) là `0x00000000`;
- PC nằm trong Application `0x08010000..0x0807FFFF`.

Không ghi register/reset board chỉ để xác minh khi chưa được phép.

## 5. Debug

Debug không flash nhưng GDB có thể halt/reset CPU; báo trước nếu board điều khiển
cơ cấu thật.

1. Có thể dry-run: `b300-stlink debug --dry-run --json`.
2. Local dùng mặc định loopback:
   `b300-stlink debug --gdb-port 3333`.
3. Remote qua IPC chỉ khi user cho phép và mạng tin cậy:
   `b300-stlink debug --bind-address 0.0.0.0 --gdb-port 3333`.
4. Telnet/TCL phải giữ disabled cho remote; không lách validation để mở cổng.
5. Dùng đúng AXF/ELF tương ứng để đọc symbol. Không chạy GDB `load`, `restore`
   hoặc lệnh flash trong mode debug.
6. Trước khi đóng, chạy `monitor reset run`, `detach`, `quit`; dừng OpenOCD và
   xác nhận GDB port đã đóng.

## 6. Ubuntu IPC và lỗi thường gặp

Không dùng sudo cho CLI. Nếu không thấy ST-Link, đọc `lsusb`, group `plugdev`,
udev rule và replug probe. Chỉ thay đổi udev khi user cho phép.

| Dấu hiệu | Hành động |
|---|---|
| `OpenOCD was not found` | Dừng; hướng dẫn cài bundle đúng OS. |
| Không nhận ST-Link | Kiểm tra USB/driver/udev/probe serial; không flash. |
| HEX protected range | Dừng; yêu cầu đúng HEX Application `0x08010000`. |
| Verify fail | Dừng, lưu log, kiểm nguồn/cáp/probe; không retry. |
| Recovery sau flash | Dừng; không mass erase; kiểm bootloader hỗ trợ marker. |

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
