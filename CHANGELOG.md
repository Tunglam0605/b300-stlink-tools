# Changelog

Các thay đổi đáng chú ý của dự án được ghi trong file này. Định dạng dựa trên
Keep a Changelog; phiên bản phát hành dự kiến dùng Semantic Versioning.

## [Unreleased]

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
[0.2.0]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.2.0
