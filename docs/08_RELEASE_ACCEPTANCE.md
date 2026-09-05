# Biên bản nghiệm thu và phát hành B300 ST-Link Tools

## Bản 0.19.1 - PROGRAM automatic read-only preflight

- Independent pre-merge validation: **114/114 canonical test modules PASS**, including **21/21 PROGRAM preflight cases**.
- PROGRAM now performs fresh read-only Target/flash/WRP/RDP/HEX validation before handing off to the unchanged canonical Application flash transaction.
- PROGRAM, DEVICE and top status cards share the same TargetInfo evidence; uninspected state is neutral and stale evidence is invalidated on probe/HEX/context changes.
- No flash transaction or Bootloader/metadata protection is weakened. `HW-P1-001` remains open and B300 APPLICATION ACCEPTANCE remains **DEFERRED** pending physical-board evidence.

## Bản 0.19.0 - Shared workspace / UI consolidation

- Windows canonical module-isolated regression matching CI runner: **113/113 test modules PASS** before final release metadata; the final tagged source must repeat the same gate and pass GitHub CI on Windows x64, Ubuntu x64 and Ubuntu ARM64 before publication.
- New shared resources: multiple named Gateway profiles, process-lifetime SSH sessions with password kept in RAM only, and named Debug Project profiles containing workspace + ELF/AXF.
- Production ownership is consolidated to PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS; duplicate ST-Link refresh, Target inspect, Gateway endpoint and symbol/workspace controls are removed from visible page-local flows.
- Safety boundary unchanged: HardwareSession remains authoritative, normal Application flash cannot mass erase or write S0-S2, Live Monitor remains zero-halt, OpenOCD debug listeners remain loopback-only and remote Application programming remains fail-closed.
- No new physical-board acceptance is claimed by this UI/software release. `HW-P1-001` remains open and B300 APPLICATION ACCEPTANCE remains **DEFERRED** pending hardware evidence.

## Bản 0.11.0 - Software/Hardware RC

- Software regression: 676 tests, 0 failures/errors, 2 skipped.
- Python 3.9 focused compatibility: 20/20 PASS.
- CI: Windows x64, Ubuntu x64, Ubuntu ARM64 PASS.
- Development Packages: Windows, Linux x64, Linux ARM64 PASS.
- Physical non-halting Live Monitor: 100/100 samples @ 10 Hz, 0 overrun, final target RUNNING; metadata CONFIRMED and WRP S0-S2 preserved.
- Evidence: [v0.11.0 Release Candidate Acceptance 2026-08-30](18_V0.11.0_RELEASE_CANDIDATE_ACCEPTANCE_2026-08-30.md).
- Stable publication remains blocked until cold power-cycle, full OTA -> ST-Link -> OTA, and real two-machine SSH acceptance are completed.

## Bản 0.9.0 - Software RC trước hardware/field E2E

Mục tiêu của 0.9.0 là hợp nhất AppMeta/Bootloader v0.6.5 và giữ đầy đủ ba đường remote-debug Client của Gateway: **GUI Client, CLI Client, VS Code/Cortex-Debug**. Source/package/CI có thể đạt release-candidate gate; **không tag/publish Stable cho tới khi current-code hardware acceptance, OTA ↔ ST-Link interoperability và SSH/two-machine Client↔Gateway E2E được chủ dự án nghiệm thu trên thiết bị thật**.

Các cổng bắt buộc trước tag:

- full unit/integration/GUI regression;
- `compileall`/`py_compile` và `git diff --check`;
- security/debug command audit: Gateway loopback-only, Telnet disabled, không flash/erase/Option Bytes trong debug path;
- Windows x64 + Ubuntu x64 + Ubuntu ARM64 CI;
- native package build, size budget, GUI/CLI smoke, signed updater/release metadata;
- `debug selftest` trên một máy + STM32/ST-Link thật, bao gồm AXF↔Flash exact match trước attach, restore target và release port;
- hardware debug evidence v0.9.0: [Hardware Debug Acceptance 2026-08-29](11_HARDWARE_DEBUG_ACCEPTANCE_V0.9.0_2026-08-29.md);
- GUI Client, CLI Client và VS Code/Cortex-Debug đều phải dùng Gateway loopback-only + SSH forwarding; VS Code chỉ forward GDB, không expose TCL ra LAN;
- negative selftest với AXF sai phải fail-closed trước external GDB attach và vẫn release target/ports;
- public release/updater verification sau publish.

Deferred field gate: SSH host-key/authentication, forwarding qua LAN giữa hai máy, GUI Client reconnect/disconnect, CLI Client one-shot diagnostics, VS Code/Cortex-Debug attach/break/watch trong mạng thật và remote operator acceptance.

## Bản 0.4.0 - Engineering diagnostics + hardware acceptance

- Ngày nghiệm thu phần cứng: `2026-08-28`.
- Target thật: STM32F407, 512 KiB, ST-Link V2J35S7.
- Full local regression trước release: `217 tests` PASS.
- Normal Application provisioning: PASS trên hardware, chỉ erase S3-S7.
- Factory trusted Bootloader provisioning: PASS trên hardware, chỉ erase/program S0-S2.
- WRP S0-S2: restore + persistence qua cold power-cycle PASS.
- RDP: giữ Level 0, không bị tool thay đổi.
- Trusted Bootloader: dump 48 KiB sau Factory và sau power-cycle đều bit-for-bit match.
- OpenOCD + GDB/MI thật: connect/halt/continue/disconnect PASS.
- Chi tiết bằng chứng: [Hardware Acceptance 2026-08-28](09_HARDWARE_ACCEPTANCE_2026-08-28.md).

Release 0.4.0 chỉ được publish sau khi source version, changelog, full tests,
`compileall`, `git diff --check` và release workflow đều PASS.

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

- [ ] Metadata `ERASED` hoặc `CORRUPT` với vector Application hợp lệ vẫn bị Bootloader v0.6.5 fail-closed.
- [ ] Application nạp raw khi còn metadata `CONFIRMED` cũ bị Bootloader từ chối nếu CRC/size không khớp.
- [ ] B300 ST-Link Tools erase đúng S3–S7, program/verify Application, ghi/read-back đúng 44-byte `STLM + VERIFIED` rồi mới reset.
- [ ] Boot kế tiếp chuyển `STLM + VERIFIED` thành `STLM + CONFIRMED`; full-image CRC và vector đều hợp lệ.
- [ ] Intel HEX sparse có gap được tính canonical CRC với gap = `0xFF`, khớp CRC Bootloader đọc từ Flash.
- [ ] HEX chạm Sector 0–2 bị từ chối trước mọi truy cập phần cứng.
- [ ] Application verify fail hoặc AppMeta write/read-back fail đều không reset và không retry.
- [ ] WRP S0–S2 được re-check ngay trước erase; thiếu WRP chặn transaction trước destructive command.
- [ ] Mất kết nối probe giữa phiên hiển thị phase, nguyên nhân, hành động tiếp theo và giữ log.
- [ ] Sau ca thành công: PC thuộc `0x08010000..0x0807FFFF` và BKP1R = 0.

## Cách lấy và sử dụng artifact

Mở workflow Release ở trên, tải artifact đúng hệ điều hành:

- `b300-stlink-windows-x64`: ZIP portable và installer EXE;
- `b300-stlink-ubuntu-x64`: tar.gz, AppImage và DEB amd64;
- `b300-stlink-ubuntu-arm64`: tar.gz, AppImage và DEB arm64.

Windows có thể dùng ngay bằng cách giải nén ZIP và chạy
`b300-stlink-gui.exe`, hoặc chạy installer per-user. Ubuntu không chạy GUI bằng
`sudo`; cài udev rule theo [Setup Ubuntu IPC](02_SETUP_UBUNTU_IPC.md).
## Cổng kiểm tra sau khi publish

Release workflow không dừng ở bước upload asset. Sau khi draft được publish thành
`Latest`, CI chạy `python -m scripts.release.verify_published` để kiểm tra lại trạng
thái công khai trên GitHub:

- tải lại `latest.json` từ `releases/latest/download`;
- tải lại `latest.json.minisig`;
- verify chữ ký bằng Minisign public key của release environment;
- yêu cầu version/product/schema/platform set đúng contract;
- yêu cầu mọi URL asset trỏ tới tag bất biến `releases/download/v<version>/...`;
- probe từng package update cho Windows x64, Linux x64 AppImage/DEB và Linux ARM64
  AppImage/DEB;
- retry có giới hạn để xử lý độ trễ CDN/GitHub sau publish.

Nếu chữ ký, manifest hoặc bất kỳ asset update nào không kiểm tra được thì release
pipeline bị đánh dấu lỗi. Gate này chỉ kiểm tra artifact công khai; nó không kết
nối ST-Link và không thực hiện thao tác phần cứng.
