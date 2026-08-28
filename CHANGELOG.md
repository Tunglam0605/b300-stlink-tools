# Changelog

Các thay đổi đáng chú ý của dự án được ghi trong file này. Định dạng dựa trên
Keep a Changelog; phiên bản phát hành dự kiến dùng Semantic Versioning.

## [Unreleased]

## [0.5.2] - 2026-08-28

### Fixed

- Windows GUI packaging chuyển từ PyInstaller one-file sang onedir. Python/Qt/VC runtime nằm cố định cạnh ứng dụng thay vì giải nén vào `%TEMP%\_MEIxxxxx`, loại bỏ failure `Failed to load Python DLL ... python39.dll` đã quan sát khi nâng từ v0.4.1 lên v0.5.1 trên Windows.
- Windows portable ZIP và Inno Setup installer giờ mang toàn bộ onedir runtime; updater vẫn tải cùng asset `B300-STLink-GUI-Windows-x64.exe` và không thay đổi signed update contract. Windows CI đồng thời chuyển riêng sang Python 3.11, nên release mới không còn phụ thuộc `python39.dll`.

### Validation

- Windows release CI kiểm tra `_internal/python*.dll` và `_internal/VCRUNTIME140*.dll`, chạy smoke-test từ build onedir, từ portable ZIP sau extract, và từ ứng dụng đã cài silent bằng chính installer release trước khi upload asset.

## [0.5.1] - 2026-08-28

### Fixed

- Ubuntu/Linux probe discovery không còn đọc cứng sysfs USB serial bằng ASCII. ST-Link clone có serial chứa byte UTF-8/không hợp lệ sẽ không làm GUI crash; serial không an toàn không được truyền vào `adapter serial`, và single-probe OpenOCD auto-select vẫn được dùng.
- Packaged OpenOCD được resolve từ application root đã xác minh (`B300_APP_ROOT`), executable-adjacent runtime, AppImage `APPDIR` và `/opt/b300-stlink`; mọi runtime vẫn bắt buộc vượt qua trusted `OPENOCD-MANIFEST.sha256`.
- Linux AppImage/DEB staging ép lại executable bit cho cả GUI và bundled OpenOCD, tránh trường hợp runtime tồn tại nhưng không chạy được sau extract/copy.
- Ubuntu DEB khai báo đầy đủ các Qt/XCB runtime dependency trực tiếp, gồm `libxcb-icccm4`, `libxcb-keysyms1`, `libxcb-shape0` và `libxcb-cursor0`; Linux release CI cài cùng dependency trước X11 smoke test.

### Validation

- Focused Ubuntu runtime/probe/packaging regression: **31/31 tests PASS**; full B300 ST-Link Tools regression: **225/225 tests PASS**.
- `v0.5.0` được giữ là Windows-only Pre-release; `v0.5.1` chỉ được publish Stable nếu Windows, Linux x64, Linux ARM64, signed metadata và X11 smoke tests đều PASS.

## [0.5.0] - 2026-08-28

### Added

- Factory GUI one-click: một nút **NẠP BOOTLOADER** tự chạy preflight read-only, xác minh target/WRP/RDP, tạo Factory plan rồi mới thực hiện trusted Bootloader provisioning. Backend vẫn kiểm tra lại target ngay trước khi thay đổi WRP/erase và bắt buộc khôi phục/xác minh WRP Sector 0-2.
- Ubuntu DEB cài sẵn udev rule cho ST-Link VID `0483` / PID `374x` và reload rule sau cài đặt; AppImage mang kèm rule tham chiếu.
- Linux release CI có Xvfb/X11 smoke test cho GUI native trên cả x64 và ARM64, ngoài offscreen unit tests.

### Changed

- Làm mới frontend: Factory dùng scroll area, trạng thái theo từng tab, Debug layout/state rõ hơn, Memory hex preview dùng địa chỉ Flash tuyệt đối, metadata `ERASED` không còn hiển thị các giá trị `0xFFFFFFFF` như số hợp lệ, và release notes render Markdown.
- Main window tự co theo `availableGeometry()` và minimum giảm còn `760x460`, tránh vỡ layout trên laptop 1366x768 khi Windows scaling 125-150%; màn hình lớn vẫn mở ở kích thước tối đa 1120x780.
- Một ST-Link được tự chọn cho Factory; nhiều ST-Link vẫn bắt buộc chọn đúng probe để tránh nạp nhầm board.

### Fixed

- Bootloader tin cậy được nâng lên `0x00050001` từ firmware commit `92e70f8e1cc94c17be39034fcc9a20e385325a2f`: khi metadata S3 hoàn toàn `ERASED` và Application vector hợp lệ, Bootloader coi đây là ST-Link provisioning mới và chỉ clear stale recovery marker `BKP1R`. Các case `IN_PROGRESS`, metadata corrupt, CRC/vector lỗi vẫn fail-closed; `BKP0R`/`BKP2R`/`BKP3R` giữ nguyên contract request từ Application.
- Ubuntu target inspection nhận diện `LIBUSB_ERROR_ACCESS`/permission failure và trả hướng dẫn udev cụ thể thay vì chỉ báo generic `Phase operation / OpenOCD target inspection failed`.
- Dọn layout/whitespace và interlock test cho frontend mới mà không thay đổi normal Application flash safety contract.

### Validation

- Hardware acceptance Bootloader `0x00050001`: Factory WRP OFF/program/verify/WRP ON PASS; inject `BKP1R=0x5241544F` với S3 `ERASED` + Application hợp lệ rồi reset cho kết quả `BKP1R=0`, PC vào Application; dump S0-S2 48 KiB bit-for-bit PASS (`89D120224EDECAF4137FAD9F815A3FE810CB1C52589B7DD46E920189D595E910`).
- Full B300 ST-Link Tools regression cuối: **222/222 tests PASS**; focused trusted-resource/packaging/GUI suite **60/60 PASS**. Firmware/Gateway OTA suite: **178 tests PASS, 3 skipped**; Bootloader source guards: **22/22 PASS**.

## [0.4.1] - 2026-08-28

### Fixed

- Sửa updater khi gọi `urllib.request.urlopen`: timeout giờ được truyền bằng keyword
  `timeout=` thay vì positional argument thứ hai (vốn bị Python hiểu là HTTP request
  body). Lỗi này làm GUI v0.4.0 báo `message_body should be a bytes-like object ...
  got <class 'float'>` ngay khi tải `latest.json`.
- Bổ sung regression test với opener chỉ chấp nhận keyword-only timeout để ngăn lỗi
  tương tự quay lại.

### Validation

- Live updater check đã tải và xác minh `latest.json` + `latest.json.minisig` trực tiếp
  từ GitHub Stable endpoint thành công trước khi phát hành patch.

## [0.4.0] - 2026-08-28

### Added

- Factory Bootloader provisioning riêng biệt cho STM32F407ZET6: trusted bundled
  Bootloader, WRP Sector 0-2 có kiểm soát, Option-Byte reload, verify và mandatory
  WRP restoration; normal Application flow vẫn chỉ erase Sector 3-7.
- `HardwareSessionManager` dùng chung cho Flash, Factory, Memory và Debug để ngăn
  nhiều OpenOCD/process tranh cùng một ST-Link.
- Debug foundation bằng OpenOCD + GDB/MI có token correlation, bounded timeout,
  verified `^result`, symbol `.elf/.axf`, Halt, Continue và Reset + Halt.
- GUI Debug tab, hardware interlock, OpenOCD watchdog và log riêng cho debug.
- Stable/Beta update-channel foundation và post-publish verifier cho signed
  `latest.json`, Minisign signature và toàn bộ platform update assets.

### Changed

- Chuẩn hóa provisioning contract cho flash map B300: S0-S2 Bootloader được bảo
  vệ, S3 metadata, S4-S7 Application; loại bỏ hoàn toàn ST-Link provisioning marker
  BKP4R cũ khỏi production flow.
- GUI phản ánh trạng thái hardware ownership ngay tại button/probe/memory controls
  thay vì chỉ chờ backend từ chối thao tác cạnh tranh.

### Security

- Normal Application provisioning fail-closed nếu không đọc được WRP, nếu S0-S2
  chưa protected hoặc RDP/security đang active; normal flow không thay WRP/RDP.
- Factory chỉ program trusted Bootloader và luôn best-effort restore WRP nếu bất kỳ
  bước nào sau WRP OFF thất bại; không mass erase và không dùng `stm32f2x lock/unlock`.
- OpenOCD debug mặc định chỉ bind `127.0.0.1`, GDB port 3333; Telnet/TCL bị tắt
  mặc định và không dùng làm debugger API chính.

### Validation

- Full local regression suite: 217 tests PASS trước hardware acceptance.
- Hardware acceptance trên B300 STM32F407 512 KiB: Normal Application provisioning,
  Factory Bootloader provisioning, WRP persistence, RDP preservation, GDB/MI debug
  và cold power-cycle đều PASS; xem `docs/09_HARDWARE_ACCEPTANCE_2026-08-28.md`.

## [0.3.4] - 2026-08-28

### Fixed

- Phát hành GitHub Release dùng action uploader chuyên dụng, upload tuần tự đúng 14
  asset đã ký thay vì truyền wildcard qua GitHub CLI. Rerun workflow sẽ ghi đè asset
  trùng tên trong draft, còn release chỉ được publish sau khi đủ asset.

## [0.3.3] - 2026-08-27

### Fixed

- Dùng binary minisign Linux 0.12 từ release bất biến, kiểm SHA-256 trước khi
  thực thi; không còn phụ thuộc package minisign không tồn tại trên Ubuntu 22.04.

## [0.3.2] - 2026-08-27

### Fixed

- Bật Ubuntu Universe trước khi cài minisign trong release workflow để manifest
  và checksum được ký/xác minh nhất quán trên GitHub-hosted runner.

## [0.3.1] - 2026-08-27

### Fixed

- Hoàn tất đóng gói artifact release từ các thư mục staging của GitHub Actions;
  metadata, checksum và manifest đã ký giờ nhận đúng AppImage/DEB Linux.

## [0.3.0] - 2026-08-27

### Added

- GitHub Releases trở thành nguồn phân phối chính với link tải trực tiếp cho GUI
  và CLI trên Windows x64, Ubuntu 22.04 x64 và Ubuntu 22.04 ARM64.
- Manifest cập nhật có chữ ký, checksum toàn bộ artifact, update checker chạy nền,
  Windows managed update và luồng tải/xác minh an toàn cho Linux.

### Changed

- Chuẩn hóa version từ một nguồn duy nhất và phát hành tự động theo Git tag.
- Tách gói GUI và CLI để người dùng chỉ tải đúng thành phần cần sử dụng.

### Security

- Chặn cài đặt hoặc khởi động lại phần mềm trong khi đang flash, erase, verify,
  đọc target/memory hoặc debug.
- Mọi package cập nhật phải vượt qua xác minh chữ ký Ed25519, kích thước và
  SHA-256 trước khi được chuyển sang trạng thái sẵn sàng cài đặt.

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
[0.3.4]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.4
[0.3.3]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.3
[0.3.2]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.2
[0.3.1]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.1
[0.3.0]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.0
[0.2.0]: https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.2.0
