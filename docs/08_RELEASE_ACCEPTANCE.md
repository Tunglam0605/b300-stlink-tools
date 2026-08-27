# Biên bản nghiệm thu và phát hành B300 ST-Link Tools

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

