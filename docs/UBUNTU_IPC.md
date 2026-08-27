# Hướng dẫn Ubuntu IPC x64

Tài liệu này áp dụng cho IPC Ubuntu 22.04 x64 dùng ST-Link trực tiếp qua USB.

## 1. Kiểm tra nền

```bash
uname -m                         # kỳ vọng: x86_64
python3 --version
lsusb | grep -i '0483:3748'      # ST-Link/V2
id                                # cần có group plugdev
```

Nếu ST-Link hiện `root:root`, thêm udev rule một lần:

```bash
sudo tee /etc/udev/rules.d/49-b300-stlink.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="3748", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=0483
```

Rút/cắm lại ST-Link nếu quyền chưa được cập nhật. Kiểm tra device phải có group
`plugdev`, ví dụ `/dev/bus/usb/001/004` là `root plugdev`.

## 2. Dùng bundle release

Copy `b300-stlink-linux-x64.tar.gz` sang IPC, rồi:

```bash
tar -xzf b300-stlink-linux-x64.tar.gz
cd b300-stlink-linux-x64
chmod +x install.sh
./install.sh
```

Mở terminal mới hoặc nạp PATH cho phiên hiện tại:

```bash
export PATH="$HOME/.local/bin:$PATH"
b300-stlink doctor
```

Không cần cài OpenOCD hệ thống khi dùng bundle: launcher chọn OpenOCD nằm trong
`vendor/openocd` của bundle.

## 3. Nạp firmware

```bash
b300-stlink flash /duong-dan/Main_V2_F407.hex
```

Nhiều ST-Link:

```bash
b300-stlink flash /duong-dan/Main_V2_F407.hex --probe-serial <ST-LINK-SN>
```

Kiểm tra transaction trước:

```bash
b300-stlink flash /duong-dan/Main_V2_F407.hex --dry-run --json
```

Không dùng `sudo` để nạp. Nếu chỉ `sudo` mới thấy ST-Link, udev/group chưa đúng;
sửa quyền USB trước thay vì chạy tool bằng root.

## 4. Debug

```bash
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

Kết nối GDB tới `localhost:3333`. Dừng server bằng `Ctrl+C`. Lệnh debug không
xóa/nạp flash và không ghi marker provisioning.

## 5. Tự tạo bundle Ubuntu x64

Chỉ dành cho người phát hành tool, không cần làm trên IPC khi đã có release.

```bash
git clone https://github.com/Tunglam0605/b300-stlink-tools.git
cd b300-stlink-tools
python3 build_native_bundle.py --internal-distribution-approved
```

Script tải xPack OpenOCD cho Linux x64, kiểm SHA-256, cài PyInstaller nếu thiếu
và tạo `release/b300-stlink-linux-x64.tar.gz`.

## 6. Chẩn đoán nhanh

```bash
lsusb | grep -i st-link
ls -l /dev/bus/usb/*/*
b300-stlink doctor
b300-stlink debug --dry-run --json
```

- `permission denied` hoặc không thấy ST-Link: kiểm tra udev rule và group
  `plugdev`, sau đó replug probe.
- `OpenOCD was not found`: cài lại bundle với `./install.sh`.
- HEX bị từ chối: chỉ dùng Application F407 link ở `0x08010000`, không dùng HEX
  bootloader hoặc image ghép bắt đầu tại `0x08000000`.
