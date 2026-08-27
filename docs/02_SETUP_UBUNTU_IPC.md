# Bước 2 — Cài tool trên Ubuntu IPC

## Dùng khi nào

Đầu tiên làm [00 — Git clone](00_START_HERE.md). Dùng file này **một lần cho mỗi
IPC Ubuntu x64**. Sau khi cài xong, chuyển sang [Bước 3 — Nạp firmware](03_FLASH_FIRMWARE.md).

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

## Các bước tạo và cài bundle

1. Cài dependency build một lần:

   ```bash
   sudo apt-get update
   sudo apt-get install -y python3-pip python3-venv
   ```

2. Trong thư mục repo đã clone, tạo bundle:

   ```bash
   python3 build_native_bundle.py --internal-distribution-approved
   ```

3. Giải nén và cài:

   ```bash
   cd release
   tar -xzf b300-stlink-linux-x64.tar.gz
   cd b300-stlink-linux-x64
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
   ```

Kết quả đúng: `OpenOCD available=true`. `doctor` kiểm tra bộ OpenOCD trong máy;
probe thật được kiểm tra khi bắt đầu lệnh flash/debug.

Nếu ST-Link cắm tại IPC nhưng GDB chạy trên máy phát triển, đọc
[Bước 4 — Debug OpenOCD](04_DEBUG.md). Chỉ mở remote GDB port trong mạng nội bộ
tin cậy.
