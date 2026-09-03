# Xử lý lỗi

| Lỗi | Làm gì |
|---|---|
| `OpenOCD was not found` | Chạy lại setup của đúng hệ điều hành, sau đó mở terminal mới. |
| Không thấy ST-Link | Kiểm tra cáp/driver Windows; Ubuntu kiểm tra `lsusb` và `plugdev`. |
| `HEX touches protected range` | Dừng; dùng đúng HEX Application F407 tại `0x08010000`. |
| Verify fail | Dừng, kiểm tra nguồn/cáp/SWD/probe serial; không retry mù. |
| Board vào recovery sau nạp | Lưu log, không mass erase/không retry; kiểm tra `failure_phase`, PC, BKP1R và Sector 3. Với Bootloader v0.6.5, `ERASED`/`CORRUPT` không boot; ca ST-Link thành công phải có `STLM + CONFIRMED` sau boot. |
| `Address already in use` khi debug | Đóng OpenOCD/GDB server cũ hoặc chọn port khác. |
| GDB không kết nối được IPC | Chạy Gateway với GDB/TCL chỉ bind loopback, kiểm tra SSH TCP/22, xác nhận host key/password bằng OpenSSH bình thường và SSH local forwarding; không expose/NAT port 3333/6666. |
| GDB hiện sai source/biến | Dùng đúng AXF/ELF build từ firmware đang chạy; không dùng lệnh `load` để chữa tạm. |
| Board còn halt sau debug | Trong GDB chạy `monitor reset run`, `detach`, `quit`, rồi dừng OpenOCD. |
| GUI không cho bấm Flash | Nhấn **Kiểm tra target**, chọn HEX hợp lệ và chờ thao tác hiện tại kết thúc. |
| Có nhiều ST-Link nhưng chưa chọn được target | Chọn đúng serial cụ thể; Auto-select bị vô hiệu để tránh nạp nhầm board. |
| `Application HEX changed after approval` | Chọn lại file, kiểm tra SHA-256 rồi xác nhận lại; tool chưa gửi lệnh erase. |
| Timeout/cancel khi đọc memory | Tool mở phiên recovery riêng để yêu cầu `resume`; lưu log nếu recovery cũng lỗi. |
| `Programmed, boot verification failed` | Xuất log; kiểm tra PC/BKP/Bootloader, không tự nạp lại. |
| AppImage không chạy | Kiểm quyền executable và udev; thử DEB cùng release, không dùng sudo chạy GUI. |

Lưu log dạng JSON khi cần báo lỗi:

```text
b300-stlink flash <file.hex> --json > b300-flash.log
```
