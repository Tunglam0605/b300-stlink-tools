# Download & Install Guide — B300 ST-Link Tools

Tài liệu này là **nguồn chuẩn để chọn đúng phiên bản và đúng file tải** cho người dùng, kỹ sư vận hành và AI agent.

> **Quy tắc mặc định:** nếu người dùng không yêu cầu khác, chọn **Stable / Latest Release**, chọn **GUI**, sau đó chọn artifact đúng theo hệ điều hành và kiến trúc CPU. Không chọn `Source code (zip)` hoặc `Source code (tar.gz)` để cài tool.

## 1. Chọn nhanh

| Thiết bị / hệ điều hành | Bản khuyến nghị | Link luôn trỏ tới Stable mới nhất |
|---|---|---|
| Windows 10/11 64-bit | **GUI Installer `.exe`** | [B300-STLink-GUI-Windows-x64.exe](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Windows-x64.exe) |
| Windows 10/11 64-bit, không muốn cài | GUI Portable `.zip` | [B300-STLink-GUI-Windows-x64.zip](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Windows-x64.zip) |
| Ubuntu PC/IPC Intel hoặc AMD 64-bit | **GUI `.deb` amd64** | [b300-stlink-gui_amd64.deb](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/b300-stlink-gui_amd64.deb) |
| Ubuntu x64, muốn chạy portable | GUI `.AppImage` x64 | [B300-STLink-GUI-Ubuntu-x64.AppImage](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Ubuntu-x64.AppImage) |
| Ubuntu ARM64 — Jetson, Raspberry Pi 64-bit, ARM IPC | **GUI `.deb` arm64** | [b300-stlink-gui_arm64.deb](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/b300-stlink-gui_arm64.deb) |
| Ubuntu ARM64, muốn chạy portable | GUI `.AppImage` ARM64 | [B300-STLink-GUI-Ubuntu-arm64.AppImage](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-GUI-Ubuntu-arm64.AppImage) |

Nếu chỉ cần terminal, script hoặc automation, xem mục **5. CLI** bên dưới.

## 2. Stable, Latest và version cố định

### Stable / Latest — mặc định cho người dùng

Trang Stable mới nhất:

- https://github.com/Tunglam0605/b300-stlink-tools/releases/latest

Các link `releases/latest/download/...` trong tài liệu này luôn tự chuyển tới bản Stable mới nhất, vì vậy README không cần sửa lại mỗi lần tăng version.

### Version cố định — khi cần tái lập môi trường

Nếu quy trình sản xuất, test hoặc báo cáo yêu cầu đúng một version, dùng URL có tag cụ thể:

```text
https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/vX.Y.Z
```

Ví dụ version đã hardware-acceptance ngày 2026-08-28:

```text
https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.4.0
```

Không thay `vX.Y.Z` bằng `latest` nếu yêu cầu là tái lập đúng một bản đã nghiệm thu.

## 3. Xác định kiến trúc máy

### Windows

Release hiện hỗ trợ **Windows x64**. Với phần lớn laptop/PC Windows 10/11 dùng Intel hoặc AMD 64-bit, chọn `Windows-x64`.

### Ubuntu / Linux

Chạy:

```bash
uname -m
```

Mapping:

| Kết quả | Kiến trúc | Artifact |
|---|---|---|
| `x86_64` | Intel/AMD 64-bit | `x64` hoặc `amd64` |
| `aarch64` | ARM 64-bit | `arm64` |
| `arm64` | ARM 64-bit | `arm64` |

Ví dụ:

- Jetson Orin/Nano chạy Ubuntu ARM64 → `arm64`.
- Raspberry Pi OS/Ubuntu 64-bit → `arm64`.
- IPC/laptop Ubuntu dùng Intel/AMD → `x64` / `amd64`.

Nếu kiến trúc không nằm trong bảng trên, **không tự chọn artifact gần giống**; cần xác nhận platform được hỗ trợ.

## 4. GUI — lựa chọn mặc định

GUI là lựa chọn mặc định cho kỹ sư vận hành vì có chọn ST-Link, inspect target, flash Application, Factory Bootloader, Memory, Debug và updater trong một giao diện.

### Windows — khuyến nghị

Tải:

```text
B300-STLink-GUI-Windows-x64.exe
```

Cách dùng:

1. Tải file `.exe` từ link Stable ở đầu tài liệu.
2. Chạy installer.
3. Mở **B300 ST-Link Tools** từ Start Menu/Desktop shortcut nếu có.
4. Kết nối ST-Link và board.
5. Dùng **Inspect Target** trước khi flash.

Nếu không muốn cài, dùng `B300-STLink-GUI-Windows-x64.zip`, giải nén rồi chạy executable trong bundle.

### Ubuntu x64 — khuyến nghị `.deb`

Tải:

```text
b300-stlink-gui_amd64.deb
```

Cài:

```bash
sudo apt install ./b300-stlink-gui_amd64.deb
```

### Ubuntu ARM64 — Jetson/Raspberry Pi/ARM IPC

Tải:

```text
b300-stlink-gui_arm64.deb
```

Cài:

```bash
sudo apt install ./b300-stlink-gui_arm64.deb
```

### AppImage — portable Linux

Dùng khi không muốn cài `.deb`:

```bash
chmod +x B300-STLink-GUI-Ubuntu-x64.AppImage
./B300-STLink-GUI-Ubuntu-x64.AppImage
```

ARM64 thay filename bằng `B300-STLink-GUI-Ubuntu-arm64.AppImage`.

## 5. CLI — chỉ dùng khi cần terminal/automation

CLI dành cho headless system, script, CI, automation hoặc AI agent cần gọi command trực tiếp.

| Platform | Artifact |
|---|---|
| Windows x64 | [B300-STLink-CLI-Windows-x64.zip](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-CLI-Windows-x64.zip) |
| Linux x64 | [B300-STLink-CLI-Linux-x64.tar.gz](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-CLI-Linux-x64.tar.gz) |
| Linux ARM64 | [B300-STLink-CLI-Linux-arm64.tar.gz](https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/B300-STLink-CLI-Linux-arm64.tar.gz) |

**Không chọn CLI chỉ vì tên file nhỏ gọn hơn.** Nếu người dùng muốn thao tác bằng giao diện, chọn GUI.

## 6. Các file không phải bộ cài

Trên trang GitHub Release có thêm các asset phục vụ kiểm chứng hoặc updater:

| File | Mục đích |
|---|---|
| `SHA256SUMS.txt` | Danh sách SHA-256 của release assets. |
| `release-manifest.json` | Manifest phát hành deterministic. |
| `release-manifest.json.minisig` | Chữ ký Minisign của release manifest. |
| `latest.json` | Manifest updater của Stable channel. |
| `latest.json.minisig` | Chữ ký Minisign của updater manifest. |
| `Source code (zip)` | Source GitHub tự tạo — **không phải bộ cài**. |
| `Source code (tar.gz)` | Source GitHub tự tạo — **không phải bộ cài**. |

Người dùng thông thường chỉ cần một GUI package phù hợp platform.

## 7. Kiểm tra file tải về

### Windows PowerShell

```powershell
Get-FileHash .\B300-STLink-GUI-Windows-x64.exe -Algorithm SHA256
```

Đối chiếu với:

```text
https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/SHA256SUMS.txt
```

### Linux

```bash
sha256sum B300-STLink-GUI-Ubuntu-x64.AppImage
```

Updater trong GUI dùng signed manifest; release pipeline cũng verify chữ ký và direct-download links trước khi release được coi là healthy.

## 8. Quy tắc dành cho AI agent

AI/automation phải dùng các bước sau theo đúng thứ tự.

### Decision contract

```yaml
channel:
  default: stable
  stable_release: https://github.com/Tunglam0605/b300-stlink-tools/releases/latest
  stable_manifest: https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/latest.json

interface:
  default: gui
  use_cli_only_if:
    - user explicitly requests CLI/terminal
    - environment is headless
    - automation/script integration is required

platform_mapping:
  windows_x64:
    recommended: B300-STLink-GUI-Windows-x64.exe
    portable: B300-STLink-GUI-Windows-x64.zip
    cli: B300-STLink-CLI-Windows-x64.zip
  linux_x86_64:
    recommended: b300-stlink-gui_amd64.deb
    portable: B300-STLink-GUI-Ubuntu-x64.AppImage
    cli: B300-STLink-CLI-Linux-x64.tar.gz
  linux_arm64:
    recommended: b300-stlink-gui_arm64.deb
    portable: B300-STLink-GUI-Ubuntu-arm64.AppImage
    cli: B300-STLink-CLI-Linux-arm64.tar.gz

never_select_as_installer:
  - Source code (zip)
  - Source code (tar.gz)
  - SHA256SUMS.txt
  - release-manifest.json
  - release-manifest.json.minisig
  - latest.json
  - latest.json.minisig
```

### Agent algorithm

1. Xác định người dùng cần **Stable mới nhất** hay **exact version**.
2. Nếu không nói rõ → Stable mới nhất.
3. Xác định OS.
4. Với Linux, đọc `uname -m` hoặc platform metadata.
5. Nếu không nói CLI/headless/automation → chọn GUI.
6. Chọn artifact theo bảng mapping ở trên.
7. Nếu tải Stable bằng automation, ưu tiên đọc `latest.json` để lấy metadata/version/package thay vì scrape HTML của trang Release.
8. Không chọn `Source code` làm installer.
9. Nếu exact version được yêu cầu, pin URL/tag version đó; không tự nâng lên Latest.
10. Sau download, verify SHA-256/signature nếu workflow yêu cầu integrity verification.

### Ví dụ cho agent

**User:** “Tải tool cho laptop Windows của tôi.”

→ Chọn `B300-STLink-GUI-Windows-x64.exe` từ `releases/latest/download/`.

**User:** “Cài trên Jetson Orin Ubuntu.”

→ ARM64 → chọn `b300-stlink-gui_arm64.deb`.

**User:** “Cài trên Ubuntu IPC Intel, chỉ chạy terminal.”

→ x86_64 + CLI → chọn `B300-STLink-CLI-Linux-x64.tar.gz`.

**User:** “Tôi cần đúng bản đã nghiệm thu v0.4.0.”

→ Pin release `v0.4.0`, không dùng `/latest`.

## 9. Version và nguồn thông tin chuẩn

Thứ tự ưu tiên khi cần xác định phiên bản:

1. Stable current version cho automation: `latest.json` đã ký.
2. Stable current release cho người dùng: GitHub `/releases/latest`.
3. Exact version: GitHub `/releases/tag/vX.Y.Z`.
4. Source version trong repository: `b300_version.py` — chỉ dùng khi đang làm việc với source checkout.

Không suy đoán version từ tên branch, ngày commit hoặc source-code archive.

## 10. Khi nào cần clone source?

Chỉ clone repository nếu bạn:

- phát triển hoặc sửa source;
- chạy unit tests;
- build package mới;
- audit policy/OpenOCD command;
- đóng góp pull request.

Người dùng chỉ muốn nạp/debug B300 **không cần clone repository**.

## 11. Hỗ trợ

- Release mới nhất: https://github.com/Tunglam0605/b300-stlink-tools/releases/latest
- README: [README.md](README.md)
- Flash guide: [docs/03_FLASH_FIRMWARE.md](docs/03_FLASH_FIRMWARE.md)
- Debug guide: [docs/04_DEBUG.md](docs/04_DEBUG.md)
- Troubleshooting: [docs/05_TROUBLESHOOTING.md](docs/05_TROUBLESHOOTING.md)
- Hardware acceptance: [docs/09_HARDWARE_ACCEPTANCE_2026-08-28.md](docs/09_HARDWARE_ACCEPTANCE_2026-08-28.md)
