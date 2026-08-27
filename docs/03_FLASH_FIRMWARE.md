# Bước 3 — Nạp firmware Application F407

Chỉ nạp **HEX Application F407** link tại `0x08010000`. Không dùng HEX
bootloader hoặc HEX ghép bắt đầu tại `0x08000000`.

## Bước 1: Kiểm tra trước khi ghi

```text
b300-stlink doctor
b300-stlink flash <duong-dan-file.hex> --dry-run --json
```

Output phải có:

```text
flash erase_sector 0 3 7
program {...} verify
mww 0x40002860 0x53544C4B
reset run
```

Không tiếp tục nếu thấy `mass_erase`, Sector 0--2, hoặc lỗi HEX protected range.

## Bước 2: Nạp thật

```text
b300-stlink flash <duong-dan-file.hex>
```

Ví dụ Windows:

```powershell
b300-stlink flash "C:\firmware\Main_V2_F407.hex"
```

Ví dụ Ubuntu:

```bash
b300-stlink flash /opt/firmware/Main_V2_F407.hex
```

Nếu cắm nhiều ST-Link:

```text
b300-stlink flash <duong-dan-file.hex> --probe-serial <ST-LINK-SN>
```

## Bước 3: Xác nhận kết quả

Nạp thành công khi OpenOCD báo `Verified OK`. Tool chỉ xóa Sector 3--7,
giữ Sector 0--2 Bootloader, ghi provisioning marker rồi reset chạy Application.

Không tự retry nếu verify fail. Lưu log và xem [Xử lý lỗi](05_TROUBLESHOOTING.md).
