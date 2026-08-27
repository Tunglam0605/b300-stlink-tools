# Hướng dẫn cho AI agent

## Mục tiêu

Repo này đóng gói tool đa nền tảng để nạp Application B300 STM32F407 qua
ST-Link/SWD và chạy OpenOCD debug mà không làm bootloader hiểu nhầm lần nạp
ST-Link là OTA lỗi.

## Thuật ngữ và ràng buộc flash

- **Bootloader**: Flash Sector 0--2, `0x08000000..0x0800BFFF`.
- **Metadata**: Sector 3, `0x0800C000..0x0800FFFF`.
- **Application**: Sector 4--7, `0x08010000..0x0807FFFF`.
- **Provisioning marker**: `RTC->BKP4R = 0x53544C4B`.

Lệnh `flash` hợp lệ phải làm đúng thứ tự: validate HEX Application → erase S3--S7
→ program + verify → ghi marker → reset run.

Không được thêm `mass_erase`, erase/program Sector 0--2, sửa Option Bytes/WRP,
hoặc nạp bootloader bằng CLI này.

## Quy tắc thực thi

1. Trước mọi thao tác ghi flash, chạy `flash <hex> --dry-run --json` và kiểm tra
   transaction.
2. Chỉ chạy flash thật khi người dùng đã xác nhận rõ board/HEX được phép nạp.
3. Không tự retry flash sau verify/program error. Thu thập log và báo lỗi.
4. `debug` không ghi flash, nhưng có thể dừng CPU; báo trước nếu board đang vận
   hành cơ cấu thật.
5. Không dùng `sudo` để lách quyền USB trên Ubuntu; sửa udev/group `plugdev`.
6. Không commit binary OpenOCD, file firmware HEX, Keil objects hoặc release
   archive vào repo. Release được build riêng bằng `build_native_bundle.py`.

## Lệnh chuẩn

```text
b300-stlink doctor
b300-stlink flash <application.hex> --dry-run --json
b300-stlink flash <application.hex> --probe-serial <ST-LINK-SN> --json
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

## Kiểm thử source

```text
python3 -m unittest discover -s tests -q
```

Kiểm thử tối thiểu phải chứng minh: HEX ngoài vùng Application bị từ chối;
transaction flash chỉ xóa S3--S7 và có verify/marker/reset; debug không có
erase/program/write-register command.

Đọc thêm: [README](README.md), [Windows](docs/01_SETUP_WINDOWS.md),
[Ubuntu IPC](docs/02_SETUP_UBUNTU_IPC.md),
[Flash](docs/03_FLASH_FIRMWARE.md), [Debug](docs/04_DEBUG.md).
