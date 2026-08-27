# GUI nạp Application B300 trên Windows và Ubuntu

GUI chỉ nạp Application STM32F407 qua ST-Link/SWD. Không có COM port, UART OTA,
mass erase, ghi Bootloader, sửa Option Bytes hoặc WRP.

## Bước 1 — Cài ứng dụng

### Windows

Dùng một trong hai artifact của cùng release:

- `B300-STLink-GUI-Windows-x64.exe`: installer per-user;
- `B300-STLink-GUI-Windows-x64.zip`: giải nén, chạy `install.ps1` hoặc chạy portable.

Sau khi cài, mở **B300 ST-Link Provisioning** từ Start Menu hoặc chạy:

```powershell
b300-stlink-gui
```

### Ubuntu x86_64

Dùng AppImage:

```bash
chmod +x B300-STLink-GUI-Ubuntu-x64.AppImage
./B300-STLink-GUI-Ubuntu-x64.AppImage
```

Hoặc cài DEB:

```bash
sudo apt install ./b300-stlink-gui_amd64.deb
b300-stlink-gui
```

Quyền USB/udev vẫn phải được setup theo [Setup Ubuntu IPC](02_SETUP_UBUNTU_IPC.md).
Không chạy GUI bằng `sudo`.

### Setup offline khi GUI báo thiếu OpenOCD

1. Giữ nguyên native bundle ZIP/tar.gz đầy đủ trên máy; không chỉ chép riêng
   `b300-stlink-gui.exe`.
2. Nhấn **Thiết lập môi trường** trên thanh cảnh báo.
3. Nếu bundle nằm cạnh GUI, tool tự chọn; nếu không, chọn đúng ZIP/tar.gz cho
   hệ điều hành và kiến trúc hiện tại.
4. Xác nhận OpenOCD `0.12.0-7`. Tool kiểm platform và SHA-256 tin cậy cố định
   của archive xPack gốc trước khi giải nén an toàn vào thư mục người dùng;
   manifest runtime cũng được neo vào digest cố định trong executable.
5. Chờ GUI báo `OpenOCD sẵn sàng`; không cần Internet hoặc khởi động lại.

Setup này không kết nối ST-Link, không reset chip và không ghi flash. Bundle
sai nền tảng, thiếu archive xPack gốc hoặc sai SHA-256 tin cậy sẽ bị từ chối.

### Ubuntu ARM64

Dùng artifact ARM64 của cùng release:

```bash
chmod +x B300-STLink-GUI-Ubuntu-arm64.AppImage
./B300-STLink-GUI-Ubuntu-arm64.AppImage
```

Hoặc cài `b300-stlink-gui_arm64.deb`. Không chạy artifact x86_64 trên
ARM64 hoặc ngược lại.

## Bước 2 — Chọn và kiểm tra ST-Link

1. Cắm ST-Link và cấp nguồn board.
2. Nhấn **Làm mới**.
3. Nếu có một probe, có thể giữ `Auto-select (single ST-Link)`.
4. Nếu có nhiều probe, GUI bỏ Auto-select và bắt buộc chọn đúng serial.
5. Nhấn **Kiểm tra target**.
6. Chỉ tiếp tục khi GUI xác nhận đúng STM32F407ZE 512 KiB và hiển thị điện áp,
   device ID cùng thông tin protection/WRP đọc được.

Kiểm tra target dùng `flash info` read-only; không halt, reset hoặc ghi flash.

## Bước 3 — Chọn firmware

1. Nhấn **Chọn file…**.
2. Chọn Application `.hex` link tại `0x08010000`.
3. Đối chiếu tên file, size, range và SHA-256 hiển thị.
4. Nếu GUI báo protected range hoặc không bắt đầu tại `0x08010000`, dừng và lấy
   đúng file Application.

## Bước 4 — Dry-run

1. Nhấn **Kiểm tra dry-run**.
2. Log phải thể hiện đúng transaction:

   ```text
   flash erase_sector 0 3 7
   program {...} verify       # transaction program/verify, chưa có marker
   mww 0x40002860 0x53544C4B
   reset run                  # transaction riêng sau marker
   ```

3. Không tiếp tục nếu thấy Sector 0–2, mass erase hoặc file/probe không đúng.

Dry-run không kết nối ghi flash.

## Bước 5 — Nạp Application

1. Nhấn **Nạp Application**.
2. Hộp xác nhận phải đúng probe serial, tên file, SHA-256 và `Erase Sector 3–7`.
3. Nhấn **Yes** một lần.
4. Không rút ST-Link hoặc mất nguồn trong khi trạng thái đang chạy. Sau phase
   `erasing`, nút hủy bị khóa; đóng cửa sổ cũng bị từ chối cho tới khi worker kết thúc.
5. Chờ GUI báo một trong ba kết quả tường minh:

   - `Nạp thành công`: verify đạt, PC ở Application, BKP1R/BKP4R đã clear;
   - `Đã program nhưng Boot verification thất bại`: không tự nạp lại;
   - `Nạp/verify thất bại`: dừng, xuất log và xử lý nguyên nhân.

GUI không tự retry bất kỳ lỗi erase/program/verify nào. Mọi lỗi hiển thị phase,
nguyên nhân và hành động tiếp theo.

## Bước 6 — Đọc Sector hoặc metadata

1. Mở tab **Memory / Metadata**.
2. Chọn Sector 0–7 rồi nhấn **Đọc Sector**.
3. Xem tối đa 4096 byte preview; nhấn **Xuất binary…** để lưu toàn bộ Sector.
4. Nhấn **Đọc OTA metadata** để xem magic, state, size, CRC, board token và
   classification `VALID`, `ERASED` hoặc `CORRUPT`.
5. Có thể nhấn **Hủy đọc**; tool sẽ kết thúc tiến trình và mở phiên recovery để
   yêu cầu CPU `resume` trước khi báo kết quả.

Tab này chỉ có thao tác Read/View/Export. Không có Write/Erase/Option Bytes.

## Bước 7 — Lưu bằng chứng

Nhấn **Xuất log…**, lưu log cùng SHA-256 firmware và kết quả cuối. Khi báo lỗi,
gửi file log cho kỹ thuật viên; không thử mass erase hoặc nạp lại mù.
