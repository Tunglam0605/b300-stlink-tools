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

### Security

- Bảo vệ Sector 0–2 Bootloader và từ chối HEX ngoài Application range.
- Chặn command injection qua bind address, probe serial và đường dẫn OpenOCD.
- Validate TCP port và không cho mở Telnet khi bind ra mạng.

[Unreleased]: https://github.com/Tunglam0605/b300-stlink-tools/commits/main
