# B300 Diagnostic Support Bundle

## Mục tiêu

Support Bundle là gói chẩn đoán **read-only** để kỹ sư hoặc người vận hành gửi một file ZIP khi cần phân tích lỗi từ xa. Workflow này không erase/program Flash, không reset target, không sửa WRP/RDP/Option Bytes và không mở debug controller mới.

## Cách dùng

CLI:

```text
b300-stlink support bundle b300-support.zip --json
```

Nếu có nhiều ST-Link, có thể chọn đúng probe bằng `--probe-serial`. Nếu không chọn, bundle vẫn được tạo và diagnostic sẽ ghi rõ trạng thái multiple-probe thay vì tự đoán target.

GUI:

1. Nhấn logo B300 để mở **Trợ giúp**.
2. Chọn **Xuất gói chẩn đoán hỗ trợ**.
3. Chọn file `.zip`.
4. Chờ read-only diagnostics hoàn tất.

GUI không cho chạy Support Bundle khi Flash/Factory/Memory/Debug đang sở hữu ST-Link.

## Nội dung ZIP

ZIP chỉ có hai file:

- `support.json`: snapshot kỹ thuật có schema version.
- `README.txt`: giải thích privacy/read-only contract.

Snapshot có thể gồm:

- B300 ST-Link Tools version, OS/CPU, Python runtime;
- trạng thái GDB/OpenOCD nhưng chỉ giữ basename executable, không giữ local path;
- diagnostic conclusion/reason code;
- MCU/Flash/voltage/RDP/WRP và protected sectors;
- Application vector;
- AppMeta source/state/CRC/sequence;
- Application Health, expected/actual image CRC32 và lifecycle.

## Privacy contract

Bundle cố ý **không chứa**:

- ST-Link serial hoặc USB instance identity;
- username/hostname;
- SSH host/user/key/identity;
- environment variables;
- source path, AXF/ELF path hoặc project path;
- firmware/image bytes;
- raw OpenOCD/GDB command logs.

Absolute host paths trong diagnostic text được thay bằng `<PATH>`; known probe identifiers được thay bằng `<REDACTED>`. Nếu Application Health read thất bại, bundle chỉ lưu tên exception class thay vì raw transport output.

## Safety

Support Bundle dùng cùng `HardwareSessionManager` và các read-only service hiện có. Nó không có command surface cho `erase_sector`, `program`, `mass_erase`, `mww`, `flash protect` hoặc Option Bytes.

Hardware smoke hậu v0.9.0 trên main B300 thật đã xác nhận bundle cho `BOOTABLE`, CRC32 `0xC99ED31F`, WRP S0-S2 protected và metadata `STLM + CONFIRMED`; scan nội dung ZIP không phát hiện local path/user/USB identity/SSH field.
