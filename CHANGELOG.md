# Changelog

Các thay đổi đáng chú ý của dự án được ghi trong file này. Định dạng dựa trên
Keep a Changelog; phiên bản phát hành dự kiến dùng Semantic Versioning.

## [Unreleased]

### Added

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
