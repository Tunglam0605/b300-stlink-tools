# Chính sách bảo mật và an toàn phần cứng

## Phiên bản được hỗ trợ

Nhánh `main` là nguồn duy nhất được duy trì. Người vận hành nên cập nhật từ
`origin/main`, kiểm tra commit và build bundle từ source tin cậy.

## Báo cáo vấn đề

Không công khai chi tiết khai thác nếu vấn đề có thể làm mất Bootloader, ghi sai
flash, thay đổi Option Bytes/WRP, thực thi lệnh OpenOCD ngoài ý muốn hoặc mở cổng
debug ra mạng.

Ưu tiên dùng mục **Security → Report a vulnerability** của GitHub repository
khi private vulnerability reporting đã được bật. Nếu chưa có mục này, liên hệ
maintainer qua kênh nội bộ riêng của công ty; không đưa nội dung khai thác vào
issue hoặc discussion công khai. Báo cáo nên có:

- commit/version đang dùng;
- hệ điều hành và kiến trúc;
- lệnh đã chạy sau khi loại bỏ credential/serial nhạy cảm;
- kết quả mong đợi và kết quả thực tế;
- ảnh hưởng tới vùng flash, OTA recovery hoặc quyền truy cập debug;
- cách tái hiện an toàn bằng dry-run nếu có.

Không gửi firmware nội bộ, private key, mật khẩu IPC hoặc log chứa credential
trong issue công khai.

## Nguyên tắc vận hành

- Luôn chạy dry-run trước flash thật.
- Chỉ flash khi người vận hành xác nhận board, file HEX và probe trong phiên đó.
- Không retry mù sau lỗi erase/program/verify.
- Không mở GDB/Telnet/TCL ra Internet.
- Không dùng tool để thay đổi WRP hoặc nạp Bootloader.

Sự cố có khả năng ảnh hưởng phần cứng phải được cô lập board và giữ nguyên log;
không mass erase để thử khôi phục.
