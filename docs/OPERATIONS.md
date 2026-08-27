# Vận hành B300 ST-Link Tools

## Mục đích và phạm vi

Tool này chỉ làm việc với **Application STM32F407 của B300**. Bootloader phải
đã có provisioning marker. Tool không nạp/cập nhật bootloader và không thay
đổi Option Bytes/WRP.

## Quy trình chuẩn trước khi nạp

1. Cấp nguồn ổn định cho Main F407 và cắm ST-Link/SWD.
2. Xác nhận tool đã nhận OpenOCD:

   ```text
   b300-stlink doctor
   ```

3. Xem transaction trước khi ghi flash:

   ```text
   b300-stlink flash <application.hex> --dry-run --json
   ```

4. Kiểm tra output có đủ các phần sau:

   - `flash erase_sector 0 3 7`
   - `program {...} verify`
   - `mww 0x40002860 0x53544C4B`
   - `reset run`

Không được chạy nếu output có `mass_erase`, sector `0..2`, hoặc HEX bị từ chối
vì nằm ngoài vùng Application.

## Nạp Application

```text
b300-stlink flash <application.hex>
```

Ví dụ Windows:

```powershell
b300-stlink flash "C:\firmware\Main_V2_F407.hex"
```

Ví dụ Ubuntu:

```bash
b300-stlink flash /opt/firmware/Main_V2_F407.hex
```

Nhiều probe ST-Link:

```text
b300-stlink flash <application.hex> --probe-serial <ST-LINK-SN>
```

Kết quả thành công phải có OpenOCD báo `Verified OK`. Sau reset, bootloader tiêu
thụ marker provisioning và jump vào Application; không được vào recovery/OTA
chỉ vì lần nạp ST-Link này.

## Debug

```text
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

Giữ terminal này mở trong khi debugger kết nối tới `localhost:3333`. Dừng bằng
`Ctrl+C`. Debug không xóa/nạp flash nhưng debugger có thể halt/reset CPU; chỉ
chạy khi board được phép tạm dừng.

## Xử lý kết quả lỗi

| Hiện tượng | Hành động |
|---|---|
| `OpenOCD was not found` | Cài lại bundle đúng OS, mở terminal mới, chạy `doctor`. |
| Không nhận ST-Link | Kiểm tra USB/cáp/driver trên Windows; trên Ubuntu kiểm tra udev + `plugdev`. |
| `HEX touches protected range` | Dừng; dùng đúng HEX Application F407 link tại `0x08010000`. |
| Verify fail | Dừng; kiểm tra nguồn, SWD/cáp, chọn serial probe; không retry mù. |
| Boot vào recovery sau flash | Dừng; lưu log OpenOCD và kiểm tra bootloader/marker, không mass erase. |

## Log cho báo cáo lỗi

Chạy với `--json` để lưu log có cấu trúc:

```text
b300-stlink flash <application.hex> --json > b300-flash.log
```

Không đưa HEX firmware nội bộ, serial thiết bị hoặc log nhạy cảm lên nơi công
khai nếu chưa được phép.
