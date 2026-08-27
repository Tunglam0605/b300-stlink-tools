# Bắt đầu từ Git clone

## Mục tiêu

Bạn sẽ clone source tool, tạo bundle đúng hệ điều hành, cài bundle một lần rồi
dùng lệnh `b300-stlink` để nạp/debug.

## Bước 1: Chọn đúng máy để build

Bundle phải được build trên đúng hệ điều hành/kiến trúc sẽ dùng:

| Máy dùng tool | File bundle tạo ra |
|---|---|
| Windows x64 | `b300-stlink-windows-x64.zip` |
| Ubuntu x64 / IPC x64 | `b300-stlink-linux-x64.tar.gz` |
| Ubuntu ARM64 / IPC ARM64 | `b300-stlink-linux-arm64.tar.gz` |

Không dùng bundle Windows trên Ubuntu hoặc ngược lại.

## Bước 2: Clone repo

Windows PowerShell hoặc Ubuntu terminal:

```text
git clone https://github.com/Tunglam0605/b300-stlink-tools.git
cd b300-stlink-tools
```

## Bước 3: Đi tiếp theo hệ điều hành

- Windows x64: [01 — Setup Windows](01_SETUP_WINDOWS.md)
- Ubuntu IPC x64/ARM64: [02 — Setup Ubuntu IPC](02_SETUP_UBUNTU_IPC.md)

Sau setup, dùng [03 — Nạp firmware](03_FLASH_FIRMWARE.md) để nạp Application
hoặc [04 — Debug OpenOCD](04_DEBUG.md) để debug local/remote qua IPC. Người vận
hành không muốn dùng terminal đọc [07 — GUI Windows/Ubuntu](07_GUI_WINDOWS_UBUNTU.md).
Biên bản artifact và checklist nghiệm thu F407 nằm tại
[08 — Release/Acceptance](08_RELEASE_ACCEPTANCE.md).
