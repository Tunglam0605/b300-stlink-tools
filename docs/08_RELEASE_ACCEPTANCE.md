# Biên bản nghiệm thu và phát hành B300 ST-Link Tools

## Bản 0.2.0

- Nhánh phát hành: `main`
- Commit artifact: `7ffe377`
- OpenOCD đóng gói: xPack `0.12.0-7`, có SHA-256 archive và manifest runtime cố định
- CI nguồn: [GitHub Actions run 33059887603](https://github.com/Tunglam0605/b300-stlink-tools/actions/runs/33059887603)
- Release workflow: [GitHub Actions run 33060015914](https://github.com/Tunglam0605/b300-stlink-tools/actions/runs/33060015914)

| Cổng kiểm tra | Bằng chứng | Kết quả |
|---|---|---|
| Unit/integration/GUI tests | `python -m unittest discover -s tests -q`: 95 tests | PASS |
| CI đa nền tảng | Windows x64, Ubuntu x64, Ubuntu ARM64 | PASS |
| Release native | Windows installer/ZIP, Ubuntu x64 và ARM64 tar/AppImage/DEB | PASS |
| Ubuntu offline acceptance | `aubot-tech`, Ubuntu 26.04 x86_64, HOME tạm, không `sudo` | PASS |
| Portable CLI/GUI | `b300-stlink --help`, GUI `--smoke-test` offscreen | PASS |
| Cài offline | `install.sh`, CLI `doctor`, GUI smoke sau cài | PASS |
| OpenOCD | `0.12.0-7`, `available=True`, runtime lấy từ bundle | PASS |
| Desktop asset | `b300-stlink-gui.svg` có trong archive và sau cài | PASS |

SHA-256 của native bundle Ubuntu x64 đã nghiệm thu:
`485f8a2af95863622ed5221bf38dd33394b96569a74623b225d0f3907eace7b9`.

Phiên nghiệm thu 0.2.0 không dò probe, không kết nối ST-Link, không reset chip, không
đọc/ghi flash và không sửa Option Bytes. Nghiệm thu phần cứng vẫn là cổng riêng bên dưới.

## Bản 0.1.0

- Nhánh phát hành: `main`
- Commit hợp nhất: `2e4a426`
- OpenOCD đóng gói: xPack `0.12.0-7`, kiểm tra SHA-256 trước khi đóng gói
- GUI: PySide6 `6.10.3`
- Release workflow: [GitHub Actions run 33051432789](https://github.com/Tunglam0605/b300-stlink-tools/actions/runs/33051432789)
- CI nguồn: [GitHub Actions run 33051065372](https://github.com/Tunglam0605/b300-stlink-tools/actions/runs/33051065372)

## Cổng phần mềm đã đạt

| Cổng kiểm tra | Bằng chứng | Kết quả |
|---|---|---|
| Unit/integration/GUI tests | `python -m unittest discover -s tests -q`: 69 tests | PASS |
| GUI headless smoke | Windows x64, Ubuntu x64, Ubuntu ARM64 | PASS |
| CLI entry point | `b300-stlink --help` từ Windows portable artifact | PASS |
| Windows portable ZIP | Artifact `b300-stlink-windows-x64.zip` | PASS |
| Windows installer | Artifact `B300-STLink-GUI-Setup-0.1.0-windows-x64.exe` | PASS |
| Ubuntu x64 | Native bundle, AppImage và DEB | PASS |
| Ubuntu ARM64 | Native bundle, AppImage và DEB | PASS |
| Logo/branding | Ảnh gốc, wordmark, PNG icon và multi-resolution ICO nằm trong `branding/` | PASS |
| Agent Skill | `quick_validate.py .agents/skills/b300-ota-stlink` | PASS |

Các kiểm tra trên không kết nối ST-Link và không ghi vào board. Artifact chỉ được
coi là đạt cổng phần mềm; nghiệm thu phần cứng bên dưới vẫn là cổng riêng.

## Checklist nghiệm thu phần cứng F407

Chỉ chạy sau khi chủ dự án xác nhận rõ board, probe và Application HEX trong
phiên hiện tại. Không tự retry nếu một ca thất bại.

- [ ] Application có metadata erased được nạp bằng GUI và boot thành công.
- [ ] Application nạp raw khi còn metadata `CONFIRMED` cũ bị Bootloader từ chối.
- [ ] Cùng Application đó được nạp bằng B300 ST-Link Tools và Bootloader chấp nhận.
- [ ] HEX chạm Sector 0–2 bị từ chối trước mọi truy cập phần cứng.
- [ ] Verify fail không ghi provisioning marker và không retry.
- [ ] Mất kết nối probe giữa phiên hiển thị phase, nguyên nhân, hành động tiếp theo và giữ log.
- [ ] Sau ca thành công: PC thuộc `0x08010000..0x0807FFFF`, BKP1R = 0 và BKP4R = 0.

## Cách lấy và sử dụng artifact

Mở workflow Release ở trên, tải artifact đúng hệ điều hành:

- `b300-stlink-windows-x64`: ZIP portable và installer EXE;
- `b300-stlink-ubuntu-x64`: tar.gz, AppImage và DEB amd64;
- `b300-stlink-ubuntu-arm64`: tar.gz, AppImage và DEB arm64.

Windows có thể dùng ngay bằng cách giải nén ZIP và chạy
`b300-stlink-gui.exe`, hoặc chạy installer per-user. Ubuntu không chạy GUI bằng
`sudo`; cài udev rule theo [Setup Ubuntu IPC](02_SETUP_UBUNTU_IPC.md).
