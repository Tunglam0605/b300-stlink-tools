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
flash write_image {application.hex}
verify_image {application.hex}
metadata_plan: 0x0800C000 / 44 bytes / STLM + VERIFIED
reset run
```

Dry-run hiển thị rõ Application transaction, AppMeta contract và reset có điều
kiện. `reset run` chỉ hợp lệ sau khi Application có exact `** Verified OK **`,
44-byte `STLM + VERIFIED` đã được program/verify/read-back đúng tại `0x0800C000`.
Trước erase, normal flow phải đọc target F407 512 KiB và xác nhận OpenOCD report
rõ WRP cho S0–S2; service kiểm lại WRP ngay trước destructive transaction. Thiếu
WRP hoặc S0–S2 chưa protected thì fail closed, chuyển sang Factory/Bootloader.

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

Nạp thành công khi Application verify đạt, AppMeta `STLM + VERIFIED` được ghi và
read-back đúng 44 byte, reset thành công, rồi post-verify xác nhận PC ở Application
và BKP1R đã clear. Bootloader v0.6.5 sẽ kiểm metadata CRC, full-image CRC và vector
rồi consume `STLM + VERIFIED` thành `STLM + CONFIRMED`; size/CRC giữ nguyên và
sequence phải là modular successor của record vừa ghi. Tool chỉ xóa Sector 3--7
và giữ nguyên Sector 0--2 Bootloader.

Không tự retry nếu bất kỳ phase nào lỗi. Lưu `failure_phase`, `reason`,
`next_action` trong log và xem [Xử lý lỗi](05_TROUBLESHOOTING.md).

## Vì sao không nạp raw bằng OpenOCD/CubeProgrammer

Sau một phiên OTA thành công, Sector 3 giữ metadata `CONFIRMED` gồm size và
CRC32 của Application đã được xác nhận. Nếu chỉ erase/program Sector 4--7 bằng
ST-Link, metadata cũ vẫn còn. Bootloader sẽ thấy vector Application mới hợp lệ
nhưng CRC/size không khớp metadata và giữ board ở recovery.

`b300-stlink flash` xử lý đúng bằng cách xóa metadata + Application trong flash
domain S3–S7, program/verify image mới, rồi tính `imageSize` và `imageCrc32` trên
**canonical continuous flash image** từ `0x08010000` đến byte cuối; mọi gap Intel
HEX trong vùng này được tính là `0xFF` vì Flash vừa được erase. Tool tạo đúng
44-byte AppMeta `STLM + VERIFIED`, ghi tại `0x0800C000`, verify và đọc lại chính
xác trước khi reset. Bootloader v0.6.5 không có erased-metadata fallback: metadata
erased/corrupt/foreign hoặc CRC mismatch đều fail-closed về recovery.

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

## Factory / Bootloader (không phải normal flash)

Chỉ dùng khi main/chip mới hoặc khi được phép bảo trì Bootloader. Không dùng HEX
tự chọn: tool dùng Bootloader đã bundle, kiểm SHA-256/provenance, và transaction
trusted catalog do nhà phát hành kiểm soát. Người dùng chỉ được chọn các Bootloader profile có trong release chính thức; không thể import artifact bên ngoài. Việc thêm F407/H7 hoặc đổi COM/UART OTA là thay đổi của một release mới.
riêng cho S0--S2/WRP.

```text
b300-stlink provision-bootloader --dry-run --json
b300-stlink provision-bootloader --profile b300-f407ze-com3-v00060500 --dry-run --json
b300-stlink provision-bootloader --profile b300-f407ze-com3-v00060500 --probe-serial <STLINK_SERIAL> --confirm-factory-provision --json
```

Factory có thể tạm tắt rồi bắt buộc khôi phục WRP S0--S2. Sau mỗi thay đổi WRP,
tool reset/halt để STM32F4 reload Option Bytes rồi mới xác minh trạng thái và đi
tiếp. Nếu verify/reload/restore WRP lỗi, dừng và không chuyển qua normal
Application flash. RDP không được thay đổi.
