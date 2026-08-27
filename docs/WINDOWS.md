# Hướng dẫn Windows x64

## 1. Điều kiện

- Windows 10/11 x64.
- ST-Link/V2 hoặc ST-Link/V3 đã được Windows nhận driver.
- Firmware Application dạng Intel HEX, link tại `0x08010000`.
- Bootloader B300 đã có hỗ trợ provisioning marker `0x53544C4B`.

Không cần cài STM32CubeProgrammer, Python hoặc OpenOCD riêng khi dùng bundle
release; tất cả đã nằm trong bundle.

## 2. Cài bundle một lần

Giải nén `b300-stlink-windows-x64.zip`, mở PowerShell trong thư mục vừa giải nén
rồi chạy:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Đóng/mở terminal mới, kiểm tra:

```powershell
b300-stlink doctor
```

Kết quả mong đợi là `OpenOCD available=true`.

## 3. Nạp Application an toàn

```powershell
b300-stlink flash "C:\duong-dan\Main_V2_F407.hex"
```

Nếu có nhiều ST-Link, chọn đúng probe bằng serial:

```powershell
b300-stlink flash "C:\duong-dan\Main_V2_F407.hex" --probe-serial 1330080011145157544D4E00
```

Xem transaction trước khi nạp mà không động vào board:

```powershell
b300-stlink flash "C:\duong-dan\Main_V2_F407.hex" --dry-run --json
```

Tool luôn làm theo thứ tự:

1. kiểm HEX chỉ chứa vùng Application `0x08010000..0x0807FFFF`;
2. xóa Sector 3--7; không có mass erase;
3. nạp và verify HEX;
4. ghi marker provisioning vào `BKP4R`;
5. reset chạy Application.

Sector 0--2 chứa Bootloader không bị xóa/nạp. Sau reset bootloader tiêu thụ
marker và không nhầm lần nạp ST-Link này là một OTA lỗi/recovery.

## 4. Debug OpenOCD

```powershell
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

Lệnh giữ terminal chạy trong lúc debug. GDB server lắng nghe tại
`localhost:3333`; dừng bằng `Ctrl+C`. Mode này chỉ mở OpenOCD/GDB server, không
gọi lệnh erase, program hoặc ghi backup register.

## 5. Khi có lỗi

- `OpenOCD was not found`: chạy lại `install.ps1`, sau đó mở terminal mới.
- Không thấy ST-Link: kiểm tra cáp USB/driver trong Device Manager, rồi chạy
  `b300-stlink doctor`.
- HEX bị từ chối vùng protected: dùng đúng file Application F407, không dùng
  file bootloader hoặc HEX ghép từ địa chỉ `0x08000000`.
- Nhiều ST-Link: thêm `--probe-serial`.
