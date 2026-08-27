# Đóng góp cho B300 ST-Link Tools

Repository ưu tiên an toàn Bootloader/OTA và khả năng vận hành giống nhau trên
Windows, Ubuntu IPC và Linux ARM64. Thay đổi nhỏ, dễ kiểm tra được ưu tiên hơn
refactor rộng không liên quan.

## 1. Chuẩn bị

```text
git clone https://github.com/Tunglam0605/b300-stlink-tools.git
cd b300-stlink-tools
python3 -m unittest discover -s tests -q
```

Sử dụng Python 3.9 trở lên. Unit test không yêu cầu ST-Link hoặc board.

## 2. Tạo thay đổi

1. Tạo branch ngắn, mô tả đúng mục tiêu.
2. Giữ mỗi commit tập trung vào một thay đổi có thể review độc lập.
3. Viết test trước khi thay đổi hành vi CLI, validation, packaging hoặc safety.
4. Cập nhật tài liệu vận hành và Agent Skill nếu interface thay đổi.
5. Không thêm binary, firmware, credential, hardware log nhạy cảm hoặc archive
   trong `release/` vào Git.

## 3. Ranh giới bắt buộc

- Sector 0–2 Bootloader không được erase/program.
- Không thêm mass erase, chip erase hoặc thao tác Option Bytes/WRP vào CLI.
- Application HEX phải nằm hoàn toàn trong `0x08010000..0x0807FFFF`.
- Provisioning marker chỉ được ghi sau khi program + verify thành công.
- Không tự retry khi erase/program/verify thất bại.
- Debug remote phải giữ Telnet/TCL disabled.

Thay đổi ranh giới trên cần có thiết kế, phân tích recovery/power-loss và review
riêng trước khi triển khai.

## 4. Kiểm tra trước pull request

```text
python3 -m unittest discover -s tests -q
python3 -m py_compile b300_stlink.py build_native_bundle.py package_internal.py scripts/install_skill.py
python3 b300_stlink.py debug --dry-run --json
```

Nếu sửa packaging, build bundle trên đúng hệ điều hành/kiến trúc đích. Nếu sửa
flash/debug phần cứng, ghi rõ thao tác đã được người vận hành cho phép và lưu kết
quả xác minh; không đưa log chứa thông tin nhạy cảm vào repository.

## 5. Pull request

Pull request phải nêu mục tiêu, rủi ro, lệnh kiểm thử và phần cứng đã tác động.
CI Windows/Ubuntu phải pass. Không gộp thay đổi firmware B300 vào repository
tooling này.
