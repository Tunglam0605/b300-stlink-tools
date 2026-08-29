# Bước 1 — Cài tool trên Windows

## Dùng khi nào

Tải `B300-STLink-GUI-Windows-x64.exe` hoặc `B300-STLink-GUI-Windows-x64.zip`
từ trang Release. Chỉ clone repo khi cần phát triển hoặc tự build. Sau khi cài
xong, chuyển sang [Bước 3 — Nạp firmware](03_FLASH_FIRMWARE.md).

## Điều kiện

- Windows 10/11 x64.
- ST-Link đã được Windows nhận trong Device Manager.
- Không cần Git, Python, STM32CubeProgrammer hoặc OpenOCD cài riêng.

## Cài CLI headless nhanh bằng PowerShell

Nếu máy chỉ làm Gateway/terminal, không cần tải ZIP thủ công:

```powershell
irm https://raw.githubusercontent.com/Tunglam0605/b300-stlink-tools/main/install-cli.ps1 | iex
```

Script verify signed CLI manifest + package trước khi cài per-user. Sau đó chạy:

```powershell
b300-stlink gateway doctor
```

`gateway doctor` không yêu cầu GDB/AXF; nó kiểm tra OpenOCD, ST-Link, SSH server, loopback ports `3333/6666` và IP candidate.

## Các bước GUI/portable

1. Nếu dùng `B300-STLink-GUI-Windows-x64.exe`, chạy file và hoàn tất wizard.
2. Nếu dùng `B300-STLink-GUI-Windows-x64.zip`, giải nén vào một thư mục riêng,
   mở PowerShell tại đó rồi chạy:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```

3. Đóng PowerShell, mở một PowerShell mới.
4. Kiểm tra:

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
