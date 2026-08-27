# Bước 1 — Cài tool trên Windows

## Dùng khi nào

Đầu tiên làm [00 — Git clone](00_START_HERE.md). Dùng file này **một lần cho mỗi
máy Windows x64**. Sau khi cài xong, chuyển sang [Bước 3 — Nạp firmware](03_FLASH_FIRMWARE.md).

## Điều kiện

- Windows 10/11 x64.
- ST-Link đã được Windows nhận trong Device Manager.
- Git và Python 3 trên PATH (`git --version`, `py --version`).

Không cần STM32CubeProgrammer hay OpenOCD riêng. Python chỉ cần để tạo bundle
lần đầu từ source clone.

## Các bước

1. Trong thư mục repo đã clone, tạo bundle:

   ```powershell
   py build_native_bundle.py --internal-distribution-approved
   ```

2. Giải nén file `release\b300-stlink-windows-x64.zip` hoặc dùng installer EXE
   sinh bởi release workflow.
3. Mở PowerShell trong thư mục vừa giải nén.
4. Chạy:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```

5. Đóng PowerShell, mở một PowerShell mới.
6. Kiểm tra:

   ```powershell
   b300-stlink doctor
   b300-stlink-gui
   ```

Kết quả đúng: `OpenOCD available=true`. `doctor` kiểm tra bộ OpenOCD trong máy;
probe thật được kiểm tra khi bắt đầu lệnh flash/debug.

GUI được thêm vào Start Menu với tên **B300 ST-Link Provisioning**. Hướng dẫn
vận hành ở [GUI Windows/Ubuntu](07_GUI_WINDOWS_UBUNTU.md).

Nếu báo không tìm thấy ST-Link, kiểm tra cáp USB/driver trước khi nạp firmware.
Nếu cần debug source, đọc [Bước 4 — Debug OpenOCD](04_DEBUG.md) và chuẩn bị
`arm-none-eabi-gdb` cùng file AXF/ELF đúng bản firmware.
