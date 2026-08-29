# Bước 2 — Cài tool trên Ubuntu IPC

## Dùng khi nào

Tải đúng AppImage, DEB hoặc CLI tar.gz cho Ubuntu x64/ARM64 từ Release. Chỉ clone
repo khi cần phát triển hoặc tự build. Sau khi cài xong, chuyển sang [Bước 3 —
Nạp firmware](03_FLASH_FIRMWARE.md).

## Kiểm tra ST-Link

```bash
lsusb | grep -i '0483:3748'
id | grep -o plugdev
```

Phải thấy ST-Link và group `plugdev`.

Nếu chưa có quyền USB, chạy một lần:

```bash
sudo tee /etc/udev/rules.d/49-b300-stlink.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="3748", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=0483
```

Rút/cắm lại ST-Link nếu cần. Không dùng `sudo b300-stlink`.

## Cài CLI headless nhanh

Trên Gateway Ubuntu/Linux, bootstrap tự nhận `x86_64` hoặc `aarch64` và verify signed CLI manifest/package:

```bash
curl -fsSL https://raw.githubusercontent.com/Tunglam0605/b300-stlink-tools/main/install-cli.sh | sh
export PATH="$HOME/.local/bin:$PATH"
b300-stlink gateway doctor
```

Bootstrap không chạy B300 CLI bằng `sudo`. Nếu thiếu udev rule, dùng `b300-stlink setup` theo flow xác nhận riêng.

## Các bước cài thủ công từ Release

1. Xác định đúng kiến trúc:

   ```bash
   uname -m
   ```

   `x86_64` dùng gói x64; `aarch64`/`arm64` dùng gói ARM64.

2. Nếu dùng GUI, tải AppImage hoặc DEB đúng kiến trúc theo hướng dẫn
   [GUI Windows/Ubuntu](07_GUI_WINDOWS_UBUNTU.md). Không chạy GUI bằng `sudo`.

3. Nếu chỉ cần CLI, giải nén CLI tar.gz đúng kiến trúc. Ví dụ x64:

   ```bash
   mkdir -p b300-stlink-cli
   tar -xzf B300-STLink-CLI-Linux-x64.tar.gz -C b300-stlink-cli
   cd b300-stlink-cli
   chmod +x install.sh
   ./install.sh
   ```

4. Mở terminal mới hoặc chạy:

   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   ```

5. Kiểm tra:

   ```bash
   b300-stlink doctor
   b300-stlink-gui
   ```

Kết quả đúng: `OpenOCD available=true`. `doctor` kiểm tra bộ OpenOCD trong máy;
probe thật được kiểm tra khi bắt đầu lệnh flash/debug.

Release Ubuntu còn cung cấp AppImage và DEB theo
[GUI Windows/Ubuntu](07_GUI_WINDOWS_UBUNTU.md). AppImage/DEB dùng cùng core và
OpenOCD pin như native tar bundle.

Nếu ST-Link cắm tại IPC nhưng GDB chạy trên máy phát triển, đọc
[Bước 4 — Debug OpenOCD](04_DEBUG.md). Chỉ mở remote GDB port trong mạng nội bộ
tin cậy.
