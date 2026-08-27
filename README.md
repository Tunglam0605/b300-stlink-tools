# B300 ST-Link Tools

Tool nội bộ để nạp Application STM32F407 qua ST-Link/SWD và chạy OpenOCD debug.

## An toàn bootloader/OTA

`flash` chỉ cho phép Intel HEX trong vùng Application `0x08010000..0x0807FFFF`.
Nó xóa Sector 3--7, nạp và verify Application, ghi provisioning marker vào
`RTC->BKP4R`, rồi reset chạy Application. Sector 0--2 chứa Bootloader không bị
xóa hoặc nạp. Bootloader B300 phải có hỗ trợ marker `0x53544C4B`.

## Sử dụng bundle

Windows: giải nén bundle rồi chạy `install.ps1` một lần.

Ubuntu: giải nén bundle, chạy `./install.sh` một lần.

```text
b300-stlink doctor
b300-stlink flash /path/Main_V2_F407.hex
b300-stlink flash /path/Main_V2_F407.hex --probe-serial <ST-LINK-SN>
b300-stlink debug --gdb-port 3333
```

`debug` chỉ mở OpenOCD GDB server, không có lệnh erase/program/register write.
`flash --dry-run --json` cho phép xem transaction mà không thao tác probe.

## Tạo release native

Chạy trên đúng hệ điều hành/kiến trúc đích:

```text
python3 build_native_bundle.py --internal-distribution-approved
```

Script tải OpenOCD xPack đúng kiến trúc, kiểm SHA-256 và tạo bundle Windows x64,
Linux x64 hoặc Linux ARM64. Không commit binary/release archive vào repository.
