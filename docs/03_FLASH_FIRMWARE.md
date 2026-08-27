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

## Vì sao không nạp raw bằng OpenOCD/CubeProgrammer

Sau một phiên OTA thành công, Sector 3 giữ metadata `CONFIRMED` gồm size và
CRC32 của Application đã được xác nhận. Nếu chỉ erase/program Sector 4--7 bằng
ST-Link, metadata cũ vẫn còn. Bootloader sẽ thấy vector Application mới hợp lệ
nhưng CRC/size không khớp metadata và giữ board ở recovery.

`b300-stlink flash` xử lý đúng trường hợp này bằng một transaction nguyên tử ở
mức vận hành: xóa metadata và Application, program/verify image mới, rồi mới ghi
provisioning marker để Bootloader biết đây là lần nạp ST-Link có chủ đích. OTA
sau đó vẫn hoạt động bình thường.

Đã kiểm chứng trên STM32F407 với chuỗi:

```text
OTA A CONFIRMED -> raw ST-Link B -> Bootloader từ chối B
OTA A CONFIRMED -> b300-stlink flash B -> Bootloader chấp nhận B
```

Không dùng lệnh OpenOCD thủ công để thay thế transaction của tool trong vận
hành thông thường.

## Lệnh terminal đầy đủ

Windows Terminal hoặc PowerShell:

```powershell
b300-stlink doctor --json
b300-stlink flash "C:\firmware\Main_V2_F407.hex" --dry-run --json
b300-stlink flash "C:\firmware\Main_V2_F407.hex" --json
```

Ubuntu Terminal:

```bash
b300-stlink doctor --json
b300-stlink flash /opt/firmware/Main_V2_F407.hex --dry-run --json
b300-stlink flash /opt/firmware/Main_V2_F407.hex --json
```

Với nhiều probe, thêm `--probe-serial <ST-LINK-SN>` vào cả dry-run và flash
thật. Chỉ tiếp tục khi hai lệnh cùng trỏ tới đúng probe và đúng file.
