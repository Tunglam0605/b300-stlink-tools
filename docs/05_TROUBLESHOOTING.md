# Xử lý lỗi

| Lỗi | Làm gì |
|---|---|
| `OpenOCD was not found` | Chạy lại setup của đúng hệ điều hành, sau đó mở terminal mới. |
| Không thấy ST-Link | Kiểm tra cáp/driver Windows; Ubuntu kiểm tra `lsusb` và `plugdev`. |
| `HEX touches protected range` | Dừng; dùng đúng HEX Application F407 tại `0x08010000`. |
| Verify fail | Dừng, kiểm tra nguồn/cáp/SWD/probe serial; không retry mù. |
| Board vào recovery sau nạp | Lưu log, không mass erase; kiểm tra đúng bootloader đã hỗ trợ provisioning marker. |
| `Address already in use` khi debug | Đóng OpenOCD/GDB server cũ hoặc chọn port khác. |
| GDB không kết nối được IPC | Kiểm tra `--bind-address 0.0.0.0`, firewall, IP IPC và port 3333. |
| GDB hiện sai source/biến | Dùng đúng AXF/ELF build từ firmware đang chạy; không dùng lệnh `load` để chữa tạm. |
| Board còn halt sau debug | Trong GDB chạy `monitor reset run`, `detach`, `quit`, rồi dừng OpenOCD. |

Lưu log dạng JSON khi cần báo lỗi:

```text
b300-stlink flash <file.hex> --json > b300-flash.log
```
