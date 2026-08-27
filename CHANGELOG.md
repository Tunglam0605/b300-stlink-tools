# Changelog

Các thay đổi đáng chú ý của dự án được ghi trong file này. Định dạng dựa trên
Keep a Changelog; phiên bản phát hành dự kiến dùng Semantic Versioning.

## [Unreleased]

## [0.3.3] - 2026-08-27

### Fixed

- Dùng binary minisign Linux 0.12 từ release bất biến, kiểm SHA-256 trước khi
  thực thi; không còn phụ thuộc package minisign không tồn tại trên Ubuntu 22.04.

## [0.3.2] - 2026-08-27

### Fixed

- Bật Ubuntu Universe trước khi cài minisign trong release workflow để manifest
  và checksum được ký/xác minh nhất quán trên GitHub-hosted runner.

## [0.3.1] - 2026-08-27

### Fixed

- Hoàn tất đóng gói artifact release từ các thư mục staging của GitHub Actions;
  metadata, checksum và manifest đã ký giờ nhận đúng AppImage/DEB Linux.

## [0.3.0] - 2026-08-27

### Added

- GitHub Releases trở thành nguồn phân phối chính với link tải trực tiếp cho GUI
  và CLI trên Windows x64, Ubuntu 22.04 x64 và Ubuntu 22.04 ARM64.
- Manifest cập nhật có chữ ký, checksum toàn bộ artifact, update checker chạy nền,
  Windows managed update và luồng tải/xác minh an toàn cho Linux.

### Changed

- Chuẩn hóa version từ một nguồn duy nhất và phát hành tự động theo Git tag.
- Tách gói GUI và CLI để người dùng chỉ tải đúng thành phần cần sử dụng.

### Security

- Chặn cài đặt hoặc khởi động lại phần mềm trong khi đang flash, erase, verify,
  đọc target/memory hoặc debug.
- Mọi package cập nhật phải vượt qua xác minh chữ ký Ed25519, kích thước và
  SHA-256 trước khi được chuyển sang trạng thái sẵn sàng cài đặt.

## [0.2.0] - 2026-08-27

### Added

- Nút **Thiết lập môi trường** xuất hiện khi thiếu OpenOCD và cài runtime hoàn
  toàn offline từ native bundle đúng nền tảng.
- Bundle mang theo archive xPack gốc; setup đối chiếu SHA-256 tin cậy cố định
  theo platform trước khi giải nén và từ chối archive bị thay đổi.
- Runtime OpenOCD portable và user-local được kiểm từng file bằng manifest có
  digest cố định trong executable; sửa hoặc thêm file đều làm runtime bị từ chối.

### Changed

- GUI tự kiểm tra lại môi trường ngay sau setup, không cần khởi động lại và
  không tự quét hoặc truy cập ST-Link trong quá trình cài OpenOCD.
- Giải nén ZIP/TAR có giới hạn entry/dung lượng/tỷ lệ nén, chặn path traversal,
  dereference symlink an toàn và thay runtime theo cơ chế atomic rollback.

## [0.1.0] - 2026-08-27

### Added

- B300 ST-Link Tools branding for the GUI, EXE, installer, and Linux packages.
- PySide6 6.10.3 with official Python 3.9 Ubuntu ARM64 wheels.

- CLI `doctor`, safe Application provisioning và OpenOCD debugging.
- Native bundle cho Windows x64, Linux x64 và Linux ARM64.
- Quy trình ST-Link provisioning marker tương thích OTA recovery.
- Debug local/remote với loopback mặc định và Telnet/TCL disabled.
- Agent Skill `b300-ota-stlink` và playbook cho AI automation.
- CI kiểm thử trên Windows và Ubuntu.
- Tài liệu kiểm chứng metadata OTA cũ chặn raw ST-Link Application mới.
- Handoff thiết kế GUI PySide6 chuyên dụng cho Windows và Ubuntu.
- Core provisioning dùng chung cho CLI và GUI, kèm post-flash boot verification.
- GUI PySide6 chọn probe/HEX, inspect target, flash và đọc Sector/OTA metadata.
- Windows portable/installer và Ubuntu AppImage/DEB release pipelines.
- Ubuntu ARM64 CI/release gate, bundle metadata kèm OpenOCD source checksum.
- Flash phase events cho CLI/GUI, timeout/cancel và close guard an toàn.

### Security

- Bảo vệ Sector 0–2 Bootloader và từ chối HEX ngoài Application range.
- Chặn command injection qua bind address, probe serial và đường dẫn OpenOCD.
- Validate TCP port và không cho mở Telnet khi bind ra mạng.
- Marker/reset tách khỏi program/verify; marker không thể chạy nếu verify chưa đạt.
- Revalidate target và immutable staged HEX ngay trước khi erase.

[Unreleased]: https://github.com/Tunglam0605/b300-stlink-tools/commits/main
[0.3.3]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.3
[0.3.2]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.2
[0.3.1]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.1
[0.3.0]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.0
[0.2.0]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.2.0
