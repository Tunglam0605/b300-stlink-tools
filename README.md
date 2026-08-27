# B300 ST-Link Tools

<p align="center">
  <img src="branding/b300-stlink-wordmark.png" alt="B300 ST-Link Tools" width="620">
</p>

[![CI](https://github.com/Tunglam0605/b300-stlink-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/Tunglam0605/b300-stlink-tools/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Target: STM32F407](https://img.shields.io/badge/Target-STM32F407-03234B.svg)](#phạm-vi-phần-cứng)

CLI và GUI đa nền tảng dùng ST-Link/SWD để provisioning Application cho Main
Board B300 STM32F407. Cả hai dùng chung một core an toàn, giữ nguyên Bootloader,
bảo toàn đường OTA sau khi nạp bằng ST-Link và cung cấp cùng một quy trình trên
Windows và Ubuntu.

## Khả năng chính

| Chức năng | Mô tả |
|---|---|
| `doctor` | Kiểm tra OpenOCD đã sẵn sàng trong môi trường cài đặt. |
| `flash` | Validate Intel HEX, chỉ xóa Sector 3–7, program, verify và ghi provisioning marker. |
| `debug` | Mở GDB server local hoặc remote qua IPC; không tự ghi flash. |
| GUI PySide6 | Chọn probe/HEX, inspect target, dry-run, flash, post-verify và đọc memory/metadata. |
| Agent Skill | Cung cấp skill `b300-ota-stlink` và playbook cho AI agent. |
| Native bundle | Đóng gói CLI và OpenOCD cho đúng hệ điều hành/kiến trúc đích. |

## Ranh giới an toàn

| Vùng flash | Địa chỉ | Quy tắc |
|---|---|---|
| Bootloader, Sector 0–2 | `0x08000000..0x0800BFFF` | Tuyệt đối không erase/program. |
| OTA metadata, Sector 3 | `0x0800C000..0x0800FFFF` | Chỉ xóa trong transaction provisioning chuẩn. |
| Application, Sector 4–7 | `0x08010000..0x0807FFFF` | Vùng duy nhất được phép chứa dữ liệu HEX. |

`flash` từ chối HEX chạm vùng được bảo vệ, không dùng mass erase và chỉ ghi
`STLINK_PROVISION_MAGIC = 0x53544C4B` sau khi verify thành công. Bootloader B300
phải hỗ trợ marker này. Trước khi erase, core đọc lại đúng target F407 512 KiB,
chép HEX đã duyệt vào staging riêng và kiểm tra lại SHA-256/range. Vì vậy file bị
đổi sau xác nhận hoặc plan giả mạo đều bị chặn trước lệnh ghi.

## Phạm vi phần cứng

- Target: Main Board B300 dùng STM32F407.
- Probe: ST-Link qua SWD.
- Máy vận hành: Windows 10/11 x64, Ubuntu/Linux x64 hoặc Linux ARM64.
- Firmware đầu vào: Intel HEX của Application link tại `0x08010000`.
- Symbol debug: AXF/ELF đúng bản firmware đang chạy trên board.

## Bắt đầu nhanh

### 1. Clone repository

```text
git clone https://github.com/Tunglam0605/b300-stlink-tools.git
cd b300-stlink-tools
```

### 2. Cài một lần

- Windows: [Setup Windows](docs/01_SETUP_WINDOWS.md)
- Ubuntu IPC: [Setup Ubuntu IPC](docs/02_SETUP_UBUNTU_IPC.md)

### 3. Kiểm tra môi trường

```text
b300-stlink doctor
```

### 4. Nạp, mở GUI hoặc debug

```text
b300-stlink flash <application.hex> --dry-run --json
b300-stlink flash <application.hex>
b300-stlink-gui
b300-stlink debug --gdb-port 3333
b300-stlink debug --bind-address 0.0.0.0 --gdb-port 3333
```

Flash thật làm thay đổi Sector 3–7. Luôn kiểm tra dry-run và xác nhận đúng board,
file HEX, probe trước khi chạy.

## Tài liệu

| Tài liệu | Dùng khi |
|---|---|
| [Bắt đầu từ Git clone](docs/00_START_HERE.md) | Tiếp nhận repo trên máy mới. |
| [Setup Windows](docs/01_SETUP_WINDOWS.md) | Cài tool trên Windows x64. |
| [Setup Ubuntu IPC](docs/02_SETUP_UBUNTU_IPC.md) | Cài tool và quyền USB trên IPC. |
| [Nạp firmware](docs/03_FLASH_FIRMWARE.md) | Provision Application F407 an toàn. |
| [Debug OpenOCD](docs/04_DEBUG.md) | Debug local hoặc remote qua IPC. |
| [Xử lý lỗi](docs/05_TROUBLESHOOTING.md) | Chẩn đoán lỗi thường gặp. |
| [Hướng dẫn AI agent](docs/06_AI_AGENT_MANUAL.md) | Dùng thủ công, playbook hoặc Agent Skill. |
| [GUI Windows/Ubuntu](docs/07_GUI_WINDOWS_UBUNTU.md) | Vận hành giao diện theo 7 bước. |
| [Biên bản release/acceptance](docs/08_RELEASE_ACCEPTANCE.md) | Artifact đã build và checklist nghiệm thu phần cứng F407. |
| [Handoff GUI cho Antigravity](docs/superpowers/specs/2026-08-27-b300-stlink-gui-design.md) | Thiết kế GUI nạp code Windows/Ubuntu dùng chung lõi CLI. |
| [AGENTS.md](AGENTS.md) | Quy tắc bắt buộc cho AI/automation. |

## Cấu trúc repository

```text
b300_stlink.py          CLI doctor/flash/debug
b300_core/              Core policy/OpenOCD/probe/memory dùng chung
b300_gui/               Giao diện PySide6
packaging/              Windows installer và Ubuntu AppImage/DEB staging
build_native_bundle.py  Tạo bundle đúng nền tảng
package_internal.py     Đóng gói executable + OpenOCD
install.ps1             Cài bundle Windows
install.sh              Cài bundle Linux
tests/                  Unit test không cần phần cứng
docs/                   Hướng dẫn vận hành tiếng Việt
.agents/skills/         Agent Skill portable
```

## Phát triển và kiểm thử

Yêu cầu Python 3.9 trở lên. Bộ test không kết nối ST-Link và không thao tác board:

```text
python3 -m unittest discover -s tests -q
python3 -m py_compile b300_stlink.py build_native_bundle.py package_internal.py
```

CI chạy các kiểm tra này trên Windows x64, Ubuntu x64 và Ubuntu ARM64 cho mỗi
push/pull request. Khi đóng góp thay đổi, đọc [CONTRIBUTING.md](CONTRIBUTING.md).

## Tạo native bundle

Bundle phải được build trên đúng hệ điều hành/kiến trúc sẽ sử dụng:

```text
python3 build_native_bundle.py --internal-distribution-approved
```

Script tải OpenOCD xPack `0.12.0-7`, kiểm SHA-256 và tạo archive trong
`release/`. Mỗi archive chứa `BUNDLE-METADATA.txt` ghi platform, version, đúng
tên archive OpenOCD nguồn và SHA-256 đã xác minh. Binary và release archive
không được commit vào source repository.

## AI agent

Skill canonical nằm tại `.agents/skills/b300-ota-stlink`. Cài vào skills root:

```text
python3 scripts/install_skill.py --destination ~/.agents/skills
```

Agent không hỗ trợ Agent Skills phải đọc [AGENTS.md](AGENTS.md) trước khi chạy
bất kỳ lệnh nào liên quan đến ST-Link.

## Bảo mật và đóng góp

- Báo cáo vấn đề an toàn theo [SECURITY.md](SECURITY.md).
- Lịch sử thay đổi được ghi tại [CHANGELOG.md](CHANGELOG.md).
- Dự án phát hành theo giấy phép [MIT](LICENSE).
