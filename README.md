# B300 ST-Link Tools

<p align="center">
  <img src="branding/b300-stlink-wordmark.png" alt="B300 ST-Link Tools" width="620">
</p>

[![CI](https://github.com/Tunglam0605/b300-stlink-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/Tunglam0605/b300-stlink-tools/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/Tunglam0605/b300-stlink-tools?label=Latest&logo=github)](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest)
[![Target: STM32F407](https://img.shields.io/badge/Target-STM32F407-03234B.svg)](#phạm-vi-phần-cứng)

## Tải B300 ST-Link Tools

Nếu chỉ muốn sử dụng tool, **không cần clone repository**. Hãy chọn đúng gói theo máy đang dùng.

### Bản khuyến nghị

- **Windows 10/11 64-bit:** [Tải B300-STLink-GUI-Windows-x64.exe](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Windows-x64.exe) — bộ cài GUI, phù hợp với hầu hết laptop/PC Windows.
- **Ubuntu x64 (Intel/AMD):** [Tải b300-stlink-gui_amd64.deb](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/b300-stlink-gui_amd64.deb) — gói cài GUI cho PC/IPC Ubuntu thông thường.
- **Ubuntu ARM64 (Jetson, Raspberry Pi 64-bit, ARM IPC):** [Tải b300-stlink-gui_arm64.deb](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/b300-stlink-gui_arm64.deb) — gói cài GUI cho ARM 64-bit.

### Bản portable — không cần cài đặt

- **Windows x64:** [B300-STLink-GUI-Windows-x64.zip](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Windows-x64.zip)
- **Ubuntu x64:** [B300-STLink-GUI-Ubuntu-x64.AppImage](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Ubuntu-x64.AppImage)
- **Ubuntu ARM64:** [B300-STLink-GUI-Ubuntu-arm64.AppImage](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Ubuntu-arm64.AppImage)

### CLI — chỉ dùng cho terminal, script hoặc automation

- **Windows x64:** [B300-STLink-CLI-Windows-x64.zip](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-CLI-Windows-x64.zip)
- **Linux x64:** [B300-STLink-CLI-Linux-x64.tar.gz](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-CLI-Linux-x64.tar.gz)
- **Linux ARM64:** [B300-STLink-CLI-Linux-arm64.tar.gz](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-CLI-Linux-arm64.tar.gz)

Cài CLI trực tiếp bằng terminal (bootstrap tự xác định platform, verify signed `latest-cli.json`, SHA-256 và size trước khi cài):

```powershell
# Windows x64 PowerShell
irm https://raw.githubusercontent.com/Tunglam0605/b300-stlink-tools/main/install-cli.ps1 | iex
```

```bash
# Linux x64 / ARM64
curl -fsSL https://raw.githubusercontent.com/Tunglam0605/b300-stlink-tools/main/install-cli.sh | sh
```

Bootstrap chỉ cài vào vùng user. Nó không chạy toàn bộ B300 CLI bằng `sudo`; quyền udev trên Linux vẫn dùng flow `b300-stlink setup` riêng.

### Không biết Linux là x64 hay ARM64?

Chạy:

```bash
uname -m
```

- `x86_64` → chọn **x64 / amd64**.
- `aarch64` hoặc `arm64` → chọn **ARM64 / arm64**.

**Lưu ý:** `Source code (zip)` và `Source code (tar.gz)` trên GitHub Release là mã nguồn, **không phải bộ cài**.

Các link ở trên luôn trỏ tới [bản phát hành mới nhất](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest). Nếu cần đúng một phiên bản cụ thể, hãy mở release theo tag `vX.Y.Z` thay vì dùng `latest`.

**Hướng dẫn tải và cài đặt đầy đủ cho người dùng và AI agent:** [DOWNLOAD.md](DOWNLOAD.md). Tài liệu này dùng một luồng phát hành duy nhất: chọn bản mới nhất hoặc exact version, xác định kiến trúc CPU, chọn GUI/CLI và mapping chính xác từ platform sang artifact.

Các file kiểm chứng release: [SHA256SUMS.txt](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/SHA256SUMS.txt), [release-manifest.json](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/release-manifest.json), [latest.json](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/latest.json) cho GUI và [latest-cli.json](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/latest-cli.json) cho CLI.

CLI và GUI đa nền tảng dùng ST-Link/SWD để provisioning Application cho Main
Board B300 STM32F407. Cả hai dùng chung một core an toàn, giữ nguyên Bootloader,
bảo toàn đường OTA sau khi nạp bằng ST-Link và cung cấp cùng một quy trình trên
Windows và Ubuntu.

## Khả năng chính

| Chức năng | Mô tả |
|---|---|
| `doctor` | Kiểm tra OpenOCD đã sẵn sàng trong môi trường cài đặt. |
| `flash` | Validate Intel HEX, read WRP, chỉ xóa Sector 3–7, program, verify, reset và post-verify. |
| `provision-bootloader` | Factory-only: nạp Bootloader đã được trust vào Sector 0–2, sau đó restore/verify WRP. |
| `debug` | Gateway/Local/Client debug qua GDB/MI + Safe TCL; có `debug selftest` để nghiệm thu đường Gateway→external Client trên một máy, không ghi flash. |
| GUI PySide6 | Application provisioning, Factory Bootloader one-click có preflight tự động, Debug, updater và đọc memory/metadata. |
| Setup offline | Cài OpenOCD từ archive xPack gốc có SHA-256 tin cậy cố định; runtime portable/user-local cũng được kiểm toàn bộ cây file. |
| Agent Skill | Cung cấp skill `b300-ota-stlink` và playbook cho AI agent. |
| Native bundle | Đóng gói CLI và OpenOCD cho đúng hệ điều hành/kiến trúc đích. |

## Engineering diagnostics foundation

The GUI Debug tab provides a bounded engineering-debug workflow: start the
OpenOCD server, optionally select the matching `.elf`/`.axf` symbol file, connect
through verified GDB/MI, then use **Halt**, **Continue**, **Reset + Halt**, and
**Stop Debug**. The CLI also provides local integrated one-shot diagnostics through
GDB/MI on port 3333 and a loopback-only Safe TCL surface on port 6666: `where`,
`stack`, `registers`, `variable`, bounded `read-words`, hardware `break`, and
`watch`. A GDB command is not treated as successful until the matching MI token
receives a valid result record. Breakpoint/watchpoint transactions verify the
matching stop resource, clean it up in `finally`, and restore a target that was
running before attach. If OpenOCD exits unexpectedly, the hardware interlock is
released and the failed debug session is reported.

Flash, Factory provisioning, Memory, and Debug share one exclusive hardware
session. While Debug owns the ST-Link, destructive provisioning, probe changes,
and target-memory reads are disabled in the GUI instead of merely failing after
the operator presses a button. Debug contains no flash programming, arbitrary
memory write, or Option-Byte controls.

GUI và CLI dùng **một luồng cập nhật công khai duy nhất**. Mỗi release `vX.Y.Z`
phát hành `latest.json` / `latest-cli.json` có chữ ký Minisign; updater chỉ đề
nghị nâng lên phiên bản mới hơn và không hạ cấp. Development build chỉ là artifact
CI để kiểm thử, không có update channel riêng và không được phân phối cho người
dùng. Release workflow tải lại manifest đã ký và probe mọi platform asset sau khi
publish trước khi pipeline được coi là healthy.

## Ranh giới an toàn

| Vùng flash | Địa chỉ | Quy tắc |
|---|---|---|
| Bootloader, Sector 0–2 | `0x08000000..0x0800BFFF` | Normal `flash`: tuyệt đối không erase/program. Chỉ Factory được ủy quyền mới ghi trusted Bootloader tại đây. |
| OTA metadata, Sector 3 | `0x0800C000..0x0800FFFF` | Chỉ xóa trong transaction provisioning chuẩn. |
| Application, Sector 4–7 | `0x08010000..0x0807FFFF` | Vùng dữ liệu của normal Application HEX. |

Normal `flash` từ chối HEX chạm vùng được bảo vệ, không dùng mass erase, không
ghi Option Bytes/WRP và chỉ chạy khi OpenOCD đọc được WRP Sector 0–2 đang bật.
Luồng Bootloader v0.6.5 là `erase S3–S7 → program/verify Application → ghi/verify
đúng 44 byte STLM + VERIFIED tại 0x0800C000 → reset → Bootloader chuyển thành
STLM + CONFIRMED → post-verify BKP1R + PC`. `ERASED`/`CORRUPT` metadata không
chứng minh Application bootable. Trước khi erase, core đọc metadata cũ để nối
sequence khi hợp lệ, đọc lại đúng target F407 512 KiB, chép HEX đã duyệt vào
staging riêng và kiểm tra lại SHA-256/range/CRC.

`provision-bootloader` là workflow Factory tách biệt. Bootloader là **publisher-controlled artifact**: chỉ nhà phát hành quyết định version/file nào được bundle trong từng release. Người dùng không có đường dẫn, nút Browse hay tham số để import/thay thế Bootloader bằng artifact ngoài release chính thức. Tool chỉ dùng Bootloader B300 chính thức đã bundle và kiểm hash/provenance; khi cần mới tạm tắt WRP S0–S2,
Danh sách bản được phép dùng nằm trong **trusted Bootloader catalog** đóng gói cùng release và được pin SHA-256. GUI chỉ cho chọn các profile do nhà phát hành phát hành; thêm F407/H7 hoặc đổi cổng OTA phải đi qua release mới của B300 ST-Link Tools, không phải thao tác của người dùng. Profile hiện tại là `B300 F407 · COM3 OTA · Bootloader v0.6.5`: COM3 là tên cổng logic B300, phần cứng STM32 dùng USART1, TX PB6, RX PB7, DIR/RE PC13, 230400 baud, DMA2 Stream5 Channel 4.

reset/halt để reload Option Bytes rồi xác minh WRP đã OFF trước khi erase/program
đúng S0–S2. Sau program/verify, tool bật lại WRP S0–S2, reset/halt để reload
Option Bytes, xác minh WRP đã ON, rồi mới `reset run`. Lệnh thật yêu cầu cả
`--confirm-factory-provision` và `--probe-serial`; RDP không bao giờ bị thay đổi.
Factory không bao giờ là một phần của normal Application flash.

## Phạm vi phần cứng

- Target: Main Board B300 dùng STM32F407.
- Probe: ST-Link qua SWD.
- Máy vận hành: Windows 10/11 x64, Ubuntu/Linux x64 hoặc Linux ARM64.
- Firmware đầu vào: Intel HEX của Application link tại `0x08010000`.
- Symbol debug: AXF/ELF đúng bản firmware đang chạy trên board.

## Chạy từ source — dành cho developer

> Người dùng chỉ muốn cài và sử dụng tool **không cần làm phần này**. Hãy tải gói GUI phù hợp ở mục [Tải B300 ST-Link Tools](#tải-b300-st-link-tools) phía trên.

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
b300-stlink gateway doctor   # preflight cho máy Gateway: SSH/ST-Link/OpenOCD/ports/IP
b300-stlink target health --json       # read-only AppMeta + full image CRC/vector
b300-stlink support bundle b300-support.zip --json  # privacy-bounded support ZIP
```

### 3.5. Remote Debug hai máy — workflow tự động khuyến nghị

**Máy Gateway** (cắm STM32 + ST-Link):

```text
b300-stlink gateway quickstart
# nếu tool báo cần cài/bật SSH, xem plan rồi chạy:
b300-stlink gateway quickstart --confirm-system-change
```

Lệnh này chuẩn bị OpenSSH Server theo kiểu idempotent, giữ `3333/4444/6666` loopback-only và in sẵn một lệnh `gateway client-setup` để copy sang máy Client.

**Máy Client**:

```text
# dùng nguyên lệnh client-setup mà Gateway in ra
b300-stlink gateway client-setup --ssh-host <gateway> --ssh-user <user> \
  --confirm-host-fingerprint <SHA256:fingerprint>

# copy authorize_command mà Client in ra về Gateway và chạy đúng một lần
# sau đó quay lại Client:
b300-stlink gateway connect-check
b300-stlink gateway status
```

`client-setup` tự chuẩn bị OpenSSH Client khi được xác nhận, tạo/reuse B300 `ed25519` key, verify fingerprint trước khi trust và lưu **chỉ** `host/user/port` vào profile cục bộ. Không lưu password/private key. Sau khi profile sẵn sàng, CLI Debug/Live/VS Code tự dùng endpoint đã lưu, nên không cần lặp `--ssh-host/--ssh-user` ở mọi lệnh.

### 4. Nạp, mở GUI hoặc debug

```text
b300-stlink flash <application.hex> --dry-run --json
b300-stlink flash <application.hex>
b300-stlink provision-bootloader --dry-run --json
b300-stlink provision-bootloader --profile b300-f407ze-com3-v00060500 --dry-run --json
b300-stlink-gui
b300-stlink debug            # mặc định = debug gateway
b300-stlink debug gateway    # cách viết tường minh
b300-stlink debug client --symbols <application.axf> --client-action inspect --json  # tự dùng saved Gateway profile nếu có
b300-stlink debug selftest --symbols <application.axf> --expression xTickCount --location vApplicationIdleHook --json
# selftest kiểm Gateway→external Client + AXF↔Flash; SSH tunnel thật đã có loopback hardware acceptance riêng cho GUI/CLI/VS Code.
# Gateway CLI vẫn có đầy đủ flash/provision/doctor; riêng Debug chỉ làm cầu nối ST-Link/OpenOCD.
# GUI Debug và CLI Debug đều có Local/Gateway/Client path; Client giữ source + AXF/ELF và dùng managed SSH tunnel.
b300-stlink debug where --symbols <application.axf> --json   # compatibility/local diagnostics
b300-stlink debug vscode --program-relative Objects/F407/Main_V2_F407.axf --output-dir <workspace>  # tự dùng saved Gateway profile nếu có
```

Flash thật làm thay đổi Sector 3–7. Luôn kiểm tra dry-run và xác nhận đúng board,
file HEX, probe trước khi chạy.

## Tài liệu

| Tài liệu | Dùng khi |
|---|---|
| [Download & Install](DOWNLOAD.md) | **Người dùng/AI agent chọn đúng version và đúng file cài theo OS/CPU.** |
| [Bắt đầu từ Git clone](docs/00_START_HERE.md) | Tiếp nhận repo trên máy mới. |
| [Setup Windows](docs/01_SETUP_WINDOWS.md) | Cài tool trên Windows x64. |
| [Setup Ubuntu IPC](docs/02_SETUP_UBUNTU_IPC.md) | Cài tool và quyền USB trên IPC. |
| [Nạp firmware](docs/03_FLASH_FIRMWARE.md) | Provision Application F407 an toàn. |
| [Debug OpenOCD](docs/04_DEBUG.md) | Local/Gateway/Client bằng B300 Tools hoặc VS Code qua SSH tunnel. |
| [Xử lý lỗi](docs/05_TROUBLESHOOTING.md) | Chẩn đoán lỗi thường gặp. |
| [Hướng dẫn AI agent](docs/06_AI_AGENT_MANUAL.md) | Dùng thủ công, playbook hoặc Agent Skill. |
| [GUI Windows/Ubuntu](docs/07_GUI_WINDOWS_UBUNTU.md) | Vận hành giao diện theo 7 bước. |
| [Biên bản release/acceptance](docs/08_RELEASE_ACCEPTANCE.md) | Artifact đã build và checklist nghiệm thu phần cứng F407. |
| [Hardware acceptance 2026-08-28](docs/09_HARDWARE_ACCEPTANCE_2026-08-28.md) | Bằng chứng Application/Factory/Debug đã PASS trên STM32F407 thật. |
| [SSH loopback acceptance v0.9.0](docs/13_SSH_LOOPBACK_ACCEPTANCE_V0.9.0_2026-08-29.md) | GUI Client, CLI Client và VS Code/Cortex-Debug qua SSH tunnel thật trên main B300; variable/break/watch/reconnect/restore-state PASS. |
| [Diagnostic Support Bundle](docs/14_SUPPORT_BUNDLE.md) | Xuất ZIP read-only đã redact để gửi chẩn đoán target/WRP/AppMeta/Application Health khi hỗ trợ từ xa. |
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
