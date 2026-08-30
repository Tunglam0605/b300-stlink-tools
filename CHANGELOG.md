# Changelog

Các thay đổi đáng chú ý của dự án được ghi trong file này. Định dạng dựa trên
Keep a Changelog; phiên bản phát hành dự kiến dùng Semantic Versioning.

## [Unreleased]

### Added

- Bổ sung Stateful Interactive Debug Workspace trên GUI cho các primitive GDB đã có: target state + last action và các tab Current Location / Call Stack / Registers / Variables / Diagnostic. Workspace chỉ định tuyến kết quả chẩn đoán đã thu được vào đúng vùng hiển thị, giữ tương thích `diagnostic_view`, không thêm GDB command, không thay đổi breakpoint/watchpoint semantics và vẫn giữ cảnh báo Interactive Debug có thể halt MCU/ảnh hưởng realtime.
- Bổ sung Runtime Dashboard cho Realtime Live Monitor bằng chính `LiveMonitorStore` đã có: hiển thị samples, overruns, mean SWD read time, max schedule lag, incoherent values, số biến và Min/Max/Mean của từng biến. Dashboard chỉ render analytics đã thu thập, không phát sinh thêm SWD/TCL read và lỗi presentation không được phép làm hỏng Live Monitor session.
- Bổ sung Offline AXF/ELF Symbol Catalog + GUI Symbol Browser cho Realtime Live Monitor: phân loại function/data/other từ `arm-none-eabi-nm`, chỉ đánh dấu watchable khi toàn bộ symbol data có kích thước xác định nằm trong CCM/SRAM STM32F407 và tên resolve duy nhất. Browser mặc định chỉ hiện safe RAM symbols, có filter/search và không suy đoán C type; kiểu `u8/i8/u16/i16/u32/i32/f32/f64` vẫn do người dùng chọn rõ ràng trước khi Add Watch. Việc browse hoàn toàn offline, không mở ST-Link/OpenOCD và không halt/reset target.

### Fixed

- Giữ đúng `--json` khi option nằm trước/sau/nằm giữa nested subcommand trên Python 3.9+ bằng suppressed parser defaults và normalize namespace sau parse; thêm regression coverage cho các vị trí option để tránh output machine-readable bị rơi về text.

### Changed

- Hợp nhất runtime `debug live` của CLI với `LiveMonitorSession` mà GUI đang dùng: Local/Client giờ chia sẻ cùng TCL-only OpenOCD, SSH forwarding, AXF matching, RUNNING-state guard, cooperative cancel và cleanup policy. Phần CSV/JSONL/reporting được tách sang `b300_cli/live_commands.py`, giúp `b300_stlink.py` trở lại vai trò entrypoint/dispatcher thay vì giữ một implementation Live Monitor thứ hai.
- Chuẩn hóa policy đường dẫn output CLI vào `b300_cli/output_paths.py`; Live Monitor và các lệnh snapshot dùng cùng rule không ghi đè file nếu thiếu `--force`, loại bỏ helper trùng trong entrypoint và giảm nguy cơ lệch safety behavior.

### Removed

- Loại bỏ `b300_gui/live_variables_panel.py` và test legacy tương ứng sau frontend redesign; import-graph production xác nhận module này không còn reachable từ `b300_gui.__main__`. Realtime Live Monitor hiện dùng `debug_live_panel.py`/`debug_plot_panel.py`, nên việc dọn bỏ giảm code/runtime surface mà không thay đổi chức năng.
- Loại bỏ `b300_core/target.py`, shim re-export cũ cho `build_boot_verify_command`/`parse_boot_verification`; toàn bộ production/test code hiện gọi API canonical từ `b300_core.openocd`, nên shim này không còn importer và chỉ làm tăng maintenance surface.
- Loại bỏ shim `b300_core/target.py` chỉ re-export hai helper từ `openocd.py`; toàn production import-graph và test suite không còn reference, và module này không nằm trong public `b300_core.__all__`.

## [0.10.0] - 2026-08-30

### Fixed

- Harden GUI cho session dài: Realtime Execution Timeline giờ giữ bounded window tối đa 1000 sample gần nhất và trim theo lô; bỏ `ResizeToContents` khỏi hot path của bảng timeline, giảm benchmark synthetic 5000 events từ ~58.7 s xuống ~0.75 s trên máy test. Main OpenOCD log giữ tối đa 10000 blocks và Debug Technical Log tối đa 5000 blocks để tránh tăng RAM vô hạn khi treo tool lâu.
- Hardware GUI soak sau hardening: 300/300 sample @ 10 Hz với `xTickCount:u32`, `bRUN:u8`, `v_current:f64`, 0 overrun, tick-rate ~999.93 Hz, target cuối `RUNNING`; 10/10 vòng Start→first sample→Stop PASS với cooperative Stop ~15–32 ms, GDB 3333 luôn đóng, TCL 6666 được release sau mỗi vòng và không còn OpenOCD orphan. Evidence: `docs/17_GUI_RUNTIME_HARDENING_POST_V0.9.0.md`.
- Validation sau hardening: **661 tests PASS, 2 skipped, 0 failures**; GUI **79 tests PASS**, GUI smoke, `compileall` và `git diff --check` PASS.
- Linux AppImage packaging tự retry tối đa 3 lần khi `appimagetool` trả lỗi process tạm thời (ví dụ runtime download HTTP 5xx), xóa AppImage dở trước lần thử tiếp theo và vẫn fail-closed sau khi hết retry; giảm việc phải rerun toàn bộ release job vì sự cố mạng nhất thời.

### Added

- Bổ sung **Realtime Live Monitor** non-halting cho Local (`debug live`) và remote Client (`debug client --client-action live`): lấy mẫu `DWT_PCSR @ 0xE000101C` để quan sát PC khi Cortex-M4 vẫn RUNNING, map PC sang function/file/line offline từ AXF/ELF và đọc tối đa 16 symbol RAM trong cùng bounded SWD/TCL transaction. Cadence hỗ trợ 0.1–60 s, scheduler neo theo thời gian tuyệt đối để tránh drift tích lũy, có `overrun` evidence và export CSV/JSONL.
- Local Live Monitor dùng OpenOCD **TCL-only** (`gdb port disabled`, Telnet disabled); remote Live Client chỉ forward TCL 6666 qua managed SSH, không forward GDB 3333. Watch dùng `NAME:TYPE` (`u8/i8/u16/i16/u32/i32/f32/f64`), chỉ chấp nhận CCM/SRAM F407 và fail-closed với symbol trùng tên/ngoài RAM; `f64` được double-read và chỉ trả numeric value khi hai raw read coherent. Đây là statistical execution sampling, không phải instruction trace đầy đủ và không tuyên bố zero timing impact.
- Hardware smoke trên B300 thật: 30 mẫu ở 10 Hz với `xTickCount:u32`, `bRUN:u8`, `v_current:f64`, `0` overrun, target cuối `RUNNING`; `xTickCount` tăng 2350361→2353278 trong 2.922 s (~998.3 tick/s), source mapping và CSV đều PASS. Evidence/contract: `docs/15_REALTIME_LIVE_MONITOR.md`.
- Bổ sung `LiveMonitorSession` facade cho GUI/CLI orchestration với `start_local/start_client/run/cancel/close`, cooperative cancellation bằng `threading.Event`, bounded symbol-root auto-match và hardware interlock `MONITORING` riêng biệt với Interactive `DEBUGGING`. Hardware smoke trực tiếp facade: 20 mẫu @ 10 Hz, 0 overrun, target cuối `RUNNING`, `f64` coherent 20/20.
- Bổ sung `LiveMonitorStore` thread-safe cho frontend: bounded history, compressed execution transitions, function hit/share statistics, timing/overrun/schedule-lag statistics và numeric variable series với coherence tracking; xử lý hoàn toàn offline nên không tăng SWD traffic.
- Tích hợp GUI redesign với backend non-halting: `Realtime Live Monitor` không còn gọi legacy GDB `sample_variables()`/HALT-RUN; `DebugTab` mở `LiveMonitorSession` độc lập cho Local/Client, stream DWT PC + typed RAM values vào timeline/table/plot, cooperative Stop/Shutdown và chặn đồng thời Interactive Debug/Gateway. GUI hardware acceptance trên B300 thật: 20 mẫu @ 10 Hz, 0 overrun, timeline 20 dòng, plot 20 điểm, `xTickCount` đọc tới 5704461; bài Stop với interval 60 s hoàn tất trong ~0.016 s. Trong lúc Live chạy GDB 3333 không listen, TCL 6666 listen; sau Stop cả hai đóng. Post-check WRP S0-S2/metadata CONFIRMED/vector đều PASS. Evidence: `docs/16_GUI_LIVE_MONITOR_ACCEPTANCE_POST_V0.9.0.md`.
- Full integration regression sau frontend+backend merge: **659 tests PASS, 2 skipped, 0 failures**; riêng GUI **77 tests PASS**, GUI smoke, `compileall` và `git diff --check` PASS.

- Thêm **Legacy bounded GDB Sampling** `debug sample` cho Local và `debug client --client-action sample` qua SSH: lấy mẫu hữu hạn tối đa 16 biến trong **một HALT/RUN cycle**, giới hạn 0.1–60 s giữa các chu kỳ, tối đa 1000 chu kỳ, giữ nguyên trạng thái target và có thể xuất `.csv`/`.jsonl` để làm nguồn dữ liệu cho Live Plot sau này.
- Sampling phân biệt `raw_value` và `numeric_value`, nên enum/string vẫn được lưu nhưng chỉ scalar số rõ ràng mới được coi là dữ liệu đồ thị. Hardware smoke trên B300 thật với `xTickCount`, 5 chu kỳ ở 5 Hz, đã PASS và target/WRP/metadata vẫn nguyên trạng.

- GUI Interactive Debug giữ **Legacy Live Variables / bounded GDB Sampling** dùng chung sampling core: Start/Stop cooperative, bảng giá trị mới nhất, ring buffer 2000 điểm, lưu profile expressions/cycles/interval và export CSV/JSONL. GUI cảnh báo rõ mỗi chu kỳ GDB sampling sẽ HALT target rất ngắn rồi khôi phục RUNNING, nên đây là công cụ chẩn đoán chứ không phải phép đo timing hard real-time.
- Bổ sung **Live Plot** nhẹ bằng Qt/Painter, không thêm thư viện chart ngoài: tự vẽ tối đa 400 điểm cho mỗi numeric series từ cùng ring buffer; enum/string không bị ép sang số và vẫn chỉ xuất hiện ở bảng/file export.

- Thêm `target health` read-only để phân loại sức khỏe Application theo Bootloader v0.6.5: `BOOTABLE`, `UNMANAGED_RECOVERY`, `INVALID_METADATA`, `OTA_IN_PROGRESS`, `STLINK_VERIFIED_PENDING`, `IMAGE_READ_INCOMPLETE`, `INVALID_VECTOR`, `IMAGE_CRC_MISMATCH` và `NOT_BOOTABLE`; command đọc đúng `image_size`, đối chiếu CRC/vector/metadata và trả `next_action` nhưng không reset/erase/program/đổi Option Bytes.
- Hardware smoke trên B300 thật xác nhận `target health` = `BOOTABLE`, đọc 126580 byte, expected/actual CRC32 đều `0xC99ED31F`, vector hợp lệ, metadata `STLM + CONFIRMED seq=4`; post-check vẫn giữ WRP S0-S2 protected và target `READY_FOR_APPLICATION_FLASH`.

- GUI Memory/Metadata bổ sung **Application Health** read-only card dùng chung `target health` core: hiển thị lifecycle, bootable, expected/actual CRC32, vector, số byte đã kiểm và `Next action`; snapshot tự chuyển `STALE` sau mọi transaction làm thay đổi Flash. Nút Health dùng worker/cancel/interlock hiện có và không cung cấp repair/write tự động.

- Bổ sung **Diagnostic Support Bundle** read-only cho CLI (`support bundle`) và GUI menu Trợ giúp: ZIP chỉ chứa `support.json` + `README.txt`, tổng hợp version/runtime, diagnostic target/WRP, metadata và Application Health. Privacy contract loại probe serial/USB identity, username/hostname, SSH identity, source/AXF path, firmware bytes, environment variables và raw command logs; absolute paths được redact trước khi ghi.
- Hardware smoke trên B300 thật tạo support ZIP 1776 byte, Health `BOOTABLE`, CRC `0xC99ED31F`, WRP S0-S2 và metadata `CONFIRMED`; scan bundle không phát hiện local path/user/USB identity/SSH fields.

- Refactor GUI Debug: tách presentation của Live Variables/Live Plot khỏi `DebugTab` thành `LiveVariablesPanel`; GDB session/worker/interlock vẫn thuộc `DebugTab`. Giữ nguyên objectName, settings keys, sampling limits và compatibility aliases; `DebugTab` giảm từ 1570 xuống 1463 dòng để giảm coupling cho các nâng cấp telemetry/plot sau này.

## [0.9.0] - 2026-08-30

### Added

- Bổ sung trusted Bootloader catalog do nhà phát hành kiểm soát và thẻ `Bootloader OTA profile` trong GUI. Người dùng chỉ chọn profile được bundle/xác thực, không thể import Bootloader HEX tùy ý. CLI Factory có `--profile <trusted-id>` và cũng fail-closed với profile không nằm trong release. Profile v0.6.5 hiện hiển thị đầy đủ COM3 logic → USART1 vật lý, 230400 baud, TX PB6, RX PB7, DIR/RE PC13, DMA2 Stream5 Channel 4, protocol/Flash map/capabilities và provenance; kiến trúc sẵn sàng cho profile F407/H7/OTA transport khác qua release mới.
- Đồng bộ normal Application provisioning với Bootloader v0.6.5 strict AppMeta: sau Application verify, tool ghi/verify/read-back chính xác 44 byte `STLM + VERIFIED` tại `0x0800C000`, reset rồi chỉ báo thành công khi Bootloader chuyển thành `STLM + CONFIRMED` với image size/CRC và sequence kế tiếp khớp.
- Bổ sung sequence lifecycle dựa trên metadata trước đó, fallback xác định về sequence 1 khi metadata cũ invalid/unreadable, và bounded confirmation deadline trước post-verify PC/BKP1R.
- Bổ sung phát hiện Intel HEX ghi đè xung đột cùng một địa chỉ trước khi provisioning.
- CLI/GUI hiển thị evidence AppMeta mới: metadata đã ghi, read-back 44 byte, metadata CONFIRMED; Memory phân biệt nguồn `OTAM`/`STLM` và không còn coi `ERASED` là bootable fallback.
- CLI Debug bổ sung first-class `client` role cho one-shot remote diagnostics qua managed SSH local forwarding, dùng cùng `DebugSession.start_external()` và symbol-match policy với GUI Client; Gateway vẫn loopback-only.
- VS Code/Cortex-Debug remote kit tiếp tục là first-class Gateway client; SSH tunnel profile được harden đồng nhất với GUI/CLI Client bằng `BatchMode=yes`, `StrictHostKeyChecking=yes`, `ConnectTimeout=8` và chỉ forward GDB loopback.
- VS Code GDB selection ưu tiên auto-resolve từ B300_GDB/STM32CubeIDE/PATH khi có; nếu Client chưa cài GDB thì kit vẫn sinh portable với `arm-none-eabi-gdb` để CI/offline profile generation không bị phụ thuộc toolchain cục bộ. Explicit `--vscode-gdb-path` vẫn fail-closed nếu path không hợp lệ.
- Hardware debug acceptance trên main B300 thật: AXF↔Flash 4/4, source/stack/register, `xTickCount`, hardware breakpoint/watchpoint, Gateway→external Client selftest và 5 vòng stress đều PASS; GUI Client, CLI Client và VS Code/Cortex-Debug đều PASS qua SSH loopback thật (`127.0.0.1:2222`) với public-key auth, strict host-key checking, breakpoint/watchpoint và 5/5 reconnect. Two-machine LAN/Wi-Fi vẫn hữu ích để kiểm latency/firewall nhưng không còn là blocker cho correctness của SSH implementation.
- Hardware provisioning acceptance v0.9.0 trên main B300 thật bằng Windows CLI artifact do GitHub Actions build: Factory nâng Bootloader v0.5.0.1 → v0.6.5, independent S0-S2 bit-for-bit verify, strict ST-Link AppMeta `VERIFIED→CONFIRMED`, repeated sequence `2→3→4`, 3 reset persistence cycles, BOOT_REQUEST one-shot và Application update-check/no-server fallback đều PASS.

### Changed

- Factory provisioning nâng trusted Bootloader lên firmware `0x00060500`, artifact `b300_bootloader_f407ze_com3_v00060500.hex`, SHA-256 `085E44E8339D21EE2D136D11F86C2103295812CB2438807774B232647D3F75A1`, source commit `88b74f649497a5ea9c64b5394470407678795f42`.
- Normal Application và Factory đều dùng transaction erase-once: erase domain đúng một lần, sau đó `flash write_image` không erase và `verify_image`; loại bỏ `program` helper khỏi Factory để tránh erase lặp.
- Metadata programming dùng STM32 flash driver với staged `.bin`, `flash write_image`, `verify_image` và `dump_image` 44 byte khi CPU halt; không dùng raw memory-write cho internal Flash.
- Canonical README, Agent Skill và operator docs được đồng bộ theo contract v0.6.5; `ERASED`/`CORRUPT` metadata fail-closed.

### Safety

- Normal Application vẫn không thay đổi WRP/RDP, không mass erase và re-check WRP Sector 0-2 trước destructive transaction.
- Metadata failure hoặc read-back mismatch cấm reset; Bootloader confirmation timeout/mismatch fail-closed và không tự retry.
- Factory chỉ chạm S0-S2, bắt buộc khôi phục/xác minh WRP trước khi kết thúc; S3-S7 không bị Factory erase/program.

### Validation

- Focused core/factory/AppMeta regression: **62 tests PASS**.
- GUI Flash/Factory/Memory smoke regression sau AppMeta UX update: **21 tests PASS**.
- Remote Debug focused regression cho GUI/CLI Client + VS Code/Cortex-Debug: **78 tests PASS**.
- Full software regression sau trusted Bootloader catalog/profile integration: **563 tests PASS, 2 skipped, 0 failures/errors**.
- `compileall` và `git diff --check` PASS; trusted Bootloader v0.6.5 provenance loader xác minh artifact/hash/source commit thành công.
- Hardware provisioning acceptance: Bootloader S0-S2 trusted v0.6.5 bit-for-bit PASS; Application span bit-for-bit PASS (`CRC32 0xC99ED31F`); final metadata `STLM + CONFIRMED seq=4`; WRP S0-S2 ON; target RUNNING. Evidence: `docs/12_HARDWARE_PROVISIONING_ACCEPTANCE_V0.9.0_2026-08-29.md`.

### Release gates

- `0.9.0` được chốt phát hành Stable sau khi software/packaging, Factory v0.6.5, strict ST-Link AppMeta và real debug/SSH acceptance đã PASS trên artifact đóng gói/CI-built.
- **Cold power-cycle thật** và **full OTA image-transfer interoperability (OTA → ST-Link → OTA)** vẫn **chưa được thực thi** do phiên nghiệm thu hiện tại thực hiện từ xa; chúng được ghi là **deferred field acceptance**, không được tuyên bố PASS và sẽ được chạy bổ sung khi có điều kiện thao tác trực tiếp phần cứng. SSH correctness đã PASS bằng real loopback tunnel acceptance cho GUI/CLI/VS Code.
- Historical v0.6.5 hardware evidence chỉ dùng làm reference; current v0.9.0 evidence được ghi riêng trong `docs/12_HARDWARE_PROVISIONING_ACCEPTANCE_V0.9.0_2026-08-29.md`.

## [0.8.2] - 2026-08-29

### Fixed

- Tab Debug chuyển sang nội dung cuộn dọc responsive thay vì ép toàn bộ group vào chiều cao cửa sổ; loại bỏ hiện tượng hàng Breakpoint/Watch/Timeout và vùng kết quả chẩn đoán bị chồng lên nhau trên laptop hoặc Windows scaling cao.
- Vùng kết quả Diagnostics và OpenOCD/GDB Log có minimum height riêng; khi viewport thấp GUI hiển thị scrollbar thay vì làm widget overlap.

### Validation

- Regression GUI viewport thấp xác nhận scrollbar được kích hoạt, Diagnostics/Log giữ minimum height và hai group không giao nhau.
- Focused GUI regression sau fix: 48 tests PASS.

## [0.8.1] - 2026-08-29

### Added

- `b300-stlink debug selftest` nghiệm thu đường Gateway → external Client trên một máy qua loopback GDB/Safe TCL; hỗ trợ source inspect, variable, Break Once/Watch Once tùy chọn, xác minh restore RUN/HALT và release port sau cleanup.
- Self-test JSON phân biệt rõ software acceptance với field acceptance bằng `ssh_exercised=false`, `two_machine_exercised=false` và `field_acceptance_pending=true`.

### Changed

- Debug Gateway và integrated debug thật giờ fail-closed khi phát hiện nhiều ST-Link mà chưa chọn probe, thay vì để OpenOCD tự chọn không xác định; command report dùng đúng probe auto-selected mà OpenOCD thực thi.
- `debug selftest` xác minh ELF/AXF với Application Flash bằng các sample window read-only trước khi external GDB attach; symbol mismatch dừng trước attach.

### Fixed

- Partial GDB attach failure ở Local/Client thực hiện best-effort restore trạng thái RUNNING ban đầu trước khi teardown, kể cả lỗi xảy ra sau khi attach đã làm Cortex-M HALT.
- Restore helper chờ Safe TCL xác nhận target đã `RUNNING` sau lệnh Continue, giảm race giữa cleanup và đóng GDB/OpenOCD.

### Validation

- Single-machine hardware self-test trên STM32F407 thật: AXF đúng match `4/4`, Gateway READY, external Client CONNECTED, inspect/`xTickCount`/Break Once/Watch Once PASS, target cuối `RUNNING`, cổng `3333/6666` được release.
- Negative hardware acceptance: AXF cũ/sai bị chặn `0/4` trước external Client attach; target vẫn `RUNNING` và hai cổng debug được release. SSH và two-machine E2E được giữ là field acceptance riêng.

## [0.8.0] - 2026-08-29

### Added

- GUI Debug có `Auto / Local / Gateway / Client`; Client giữ source + AXF/ELF, tự mở SSH local forwarding cho GDB/Safe TCL và dùng cùng `DebugSession` preserve-state với Local.
- GUI `Break Once` / `Watch Once` dùng hardware breakpoint/watchpoint one-shot, tự cleanup resource và đưa target ban đầu `RUNNING` trở lại chạy.
- GUI Local có `Halt`, `Continue`, `Reset + Halt`, `Step Into` và `Step Over`; cổng GDB/TCL Local được tự chọn trên loopback để giảm xung đột session cũ.
- `b300-stlink gateway doctor` preflight riêng cho máy cổng: OpenOCD, ST-Link selection, SSH server, loopback ports `3333/6666` và IPv4 candidate; không yêu cầu GDB/AXF/source.
- Bootstrap CLI một lệnh cho Windows x64 (`install-cli.ps1`) và Linux x64/ARM64 (`install-cli.sh`). Bootstrap verify signed `latest-cli.json`, bootstrap Minisign 0.12 bằng SHA-256 pin, rồi verify package SHA-256/size trước managed per-user install.

### Changed

- Local Debug xác minh AXF/ELF với Application Flash trước khi load symbols; load symbol table chỉ halt tạm khi GDB yêu cầu rồi khôi phục chính xác trạng thái RUN/HALT ban đầu.
- `b300-stlink debug` mặc định trở thành vai trò `gateway`; `debug server` được giữ làm legacy alias. Gateway chỉ làm cầu nối ST-Link/OpenOCD và không cần local GDB.
- Base GUI/CLI không còn bundle toàn bộ GNU Arm GDB toolchain; Local/Client tự resolve GDB từ `B300_GDB`, STM32CubeIDE/toolchain hoặc PATH.
- Remote VS Code kit sinh lệnh Gateway canonical thay vì legacy `debug server`.

### Fixed

- Sau Application flash, Memory/Metadata không còn hiển thị snapshot OTA cũ như dữ liệu hiện tại; snapshot được đánh dấu `STALE` và yêu cầu đọc lại Sector 3.
- Sửa lifecycle GUI worker khiến Memory/Metadata có thể tiếp tục bị khóa sau khi flash/inspect đã kết thúc; ST-Link được sử dụng lại ngay trong cùng GUI, không cần restart ứng dụng.
- Sửa Local symbol loading khi target đang `RUNNING`: GDB không còn lỗi `Cannot execute this command while the target is running`; tool halt tạm, load symbols và resume tự động.

### Security

- Debug Gateway ép bind loopback, Telnet disabled, Safe TCL chỉ được forward bên trong SSH tunnel; `gdb flash_program disable` và `gdb breakpoint_override hard` luôn được áp dụng.
- Client fail-closed nếu AXF/ELF đã chọn không khớp firmware; project auto-match chỉ scan bounded tree và chỉ chấp nhận duy nhất một exact match.
- Bootstrap Linux không chạy toàn bộ B300 CLI bằng `sudo`; udev/system changes vẫn đi qua flow `b300-stlink setup` có xác nhận riêng.

### Hardware Validation

- GUI Local trên STM32F407 thật: Auto→Local, `Where`, variable `xTickCount`, Break Once tại `vApplicationIdleHook`, Watch Once tại `xTickCount`, cleanup và Stop đều PASS; target được giữ/khôi phục về `RUNNING`.
- Packaged CLI Local debug tự resolve GDB từ STM32CubeIDE và source-map AXF thật thành công mà không bundle GDB.
- Cùng một GUI/STM32/ST-Link thật: main ST-Link operation hoàn tất, interlock tự release, sau đó `Đọc OTA metadata` ngay lập tức PASS (`ERASED`) mà không restart GUI hoặc rút/cắm ST-Link.

### Validation

- Pre-release full regression sau các fix Local/Metadata: **521 tests PASS, 2 skipped**.

## [0.7.0] - 2026-08-29

### Added

- Integrated CLI debug trên loopback dùng GDB/MI `3333` + Safe TCL `6666`: `debug inspect`, `where`, `stack`, `registers`, `variable`, `poll` và bounded `read-words`.
- Source-aware AXF/ELF diagnostics: resolve program counter thành function/file/line, đọc stack frame, register và biến qua token-correlated GDB/MI.
- `debug break` one-shot chỉ dùng hardware breakpoint (`-break-insert -h`), có timeout, xác minh đúng `breakpoint-hit`/breakpoint number, cleanup rồi resume.
- `debug watch` one-shot cho expression allow-list, xác minh đúng watchpoint trigger/number, chụp frame + giá trị biến ngay tại thời điểm hit, cleanup rồi resume.
- Safe TCL client loopback-only cho `version`, `targets`, bounded aligned memory read và register diagnostics; không expose raw TCL.

### Changed

- Integrated debug ghi nhận CPU state bằng OpenOCD `targets` trước GDB attach và giữ lại trạng thái ban đầu. Target ban đầu `running` được resume sau snapshot/breakpoint/watchpoint transaction.
- OpenOCD transient `State=unknown` ngay sau listener READY được xử lý bằng bounded readiness wait; hết timeout vẫn fail-closed.
- CLI JSON debug output được chuẩn hóa cho agent/automation, gồm endpoint, initial target state, symbol path, frame/hit và watched value khi có.

### Security

- Integrated mode luôn loopback-only; Telnet bị cấm và TCL không được expose ra remote. Remote debug server vẫn chỉ mở GDB theo policy hiện có.
- Debug path không có erase/program/mass-erase, arbitrary memory write, Option Bytes, RDP hoặc WRP operations.
- Breakpoint luôn là hardware breakpoint; watch/variable expression và breakpoint location đều bị allow-list để ngăn command injection.
- Breakpoint/watchpoint resource được xóa trong cleanup kể cả lỗi/timeout; target ban đầu đang chạy được khôi phục về `running`.

### Hardware Validation

- ST-Link V2 + STM32F407 thật (~3.07 V): application vector đọc qua `debug read-words` khớp MSP `0x200185C8` và reset vector `0x08010361`.
- Machine code tại `0x0802AA80` được đối chiếu với các AXF; chỉ `B300-Main-Custom/Objects/F407/Main_V2_F407.axf` khớp binary đang chạy.
- `debug where` resolve `vApplicationIdleHook` tại `User/main.c:87`; `debug stack` resolve các FreeRTOS/task frames; `debug variable bRUN` trả `BSP_IO_RESET`.
- Hardware breakpoint one-shot hit `vApplicationIdleHook` tại `0x08025FDA` và resume target thành công.
- Hardware watchpoint one-shot trên `xTickCount` hit `xTaskIncrementTick` tại `FreeRTOS Source/tasks.c:2813`, chụp giá trị tại thời điểm hit và resume thành công.
- Sau acceptance, target xác nhận `running` và cả port `3333`/`6666` đều đóng. Không thực hiện flash/erase/Option Bytes/WRP trong các debug test.

### Compatibility

- Giữ nguyên updater contract từ v0.6.1: `latest.json` chỉ chứa 5 GUI platform legacy-compatible; CLI tiếp tục dùng signed `latest-cli.json`.
- Exact GUI 0.5.3 updater đã được dùng làm acceptance harness và phải tiếp tục nhìn thấy v0.7.0 sau khi publish.

### Validation

- Pre-bump full regression: **460 tests PASS, 2 skipped**.
- Version/updater/release-contract focused regression sau bump: **82 tests PASS, 2 skipped**.
- Full regression trên source `0.7.0`: **460 tests PASS, 2 skipped**.

## [0.6.1] - 2026-08-29

### Fixed

- Khôi phục khả năng **Kiểm tra cập nhật** cho GUI `0.5.3`: `latest.json` được giữ đúng contract GUI legacy gồm 5 platform ban đầu, không còn chèn các key CLI khiến parser `0.5.3` từ chối toàn bộ signed manifest với lỗi `Update manifest contains an unsupported platform.`
- Tách updater CLI sang signed manifest riêng `latest-cli.json`, tránh làm thay đổi schema/platform set mà các GUI đã phát hành trước đó đang tin cậy.

### Changed

- Release pipeline tạo, checksum, ký Minisign, upload và post-publish verify độc lập cho `latest.json` (GUI) và `latest-cli.json` (CLI).
- CLI từ `0.6.1` dùng endpoint `releases/latest/download/latest-cli.json`; GUI tiếp tục dùng `latest.json`, giữ backward compatibility cho đường nâng cấp `0.5.3 -> 0.6.1`.

### Compatibility

- `latest.json` được đóng băng platform contract theo GUI `0.5.3`; regression test sẽ fail nếu một CLI platform vô tình được thêm lại vào manifest GUI.
- CLI `0.6.0` là bản chuyển tiếp duy nhất đã dùng chung `latest.json`; từ `0.6.1` trở đi CLI có channel manifest riêng để các lần phát hành sau không tái diễn xung đột contract.

### Validation

- Focused updater/release compatibility regression: **47/47 tests PASS**.
- Full regression trước release: **428 tests PASS, 2 skipped**.

## [0.6.0] - 2026-08-28

### Added

- CLI feature parity: bổ sung version/probe discovery, diagnostics `doctor`/`target inspect`, đọc memory/metadata read-only, managed CLI update/self-update và Linux USB setup có xác nhận rõ ràng.
- OpenOCD debug local hỗ trợ TCL listener tùy chọn bằng `--tcl-port 6666` song song GDB server `3333`; CLI chỉ chuyển sang `READY` sau khi tất cả listener được yêu cầu thực sự mở.
- Plan vận hành riêng cho luồng debug `3333/6666`, giữ `HardwareSessionManager` làm owner duy nhất của ST-Link debug lifecycle.

### Changed

- CLI được tách parser/reporting/update command theo module thay vì dồn toàn bộ logic vào entrypoint, giữ backward compatibility cho các lệnh cũ.
- Debug runtime/process startup được harden cho Windows/Linux; GDB có thể resolve từ `B300_GDB`, bundled runtime hoặc toolchain ngoài tùy artifact.

### Security

- TCL và Telnet chỉ được phép trên loopback; remote bind chỉ cho GDB. Các OpenOCD debug port đang bật phải khác nhau.
- Debug flow tiếp tục không chứa erase/program/mass-erase, Option Bytes hay WRP operations.

### Validation

- Full regression trên `main` sau merge: **425 tests PASS, 2 skipped**.
- Hardware test trên ST-Link V2 + STM32F407: OpenOCD nhận target, `127.0.0.1:3333` và `127.0.0.1:6666` cùng LISTEN, CLI báo `READY`; TCL RPC `version` trả response hợp lệ và cả hai port đóng sau khi dừng session.
- GDB từ STM32CubeIDE 2.2.0 được resolve qua `B300_GDB`; `doctor` báo `GDB_AVAILABLE`.

## [0.5.3] - 2026-08-28

### Fixed

- Ubuntu/Linux GUI updater không còn yêu cầu người dùng tự mở Terminal rồi chạy `sudo apt install ...`. Gói `.deb` đã xác minh giờ được bàn giao cho detached update helper và cài qua `pkexec` + `apt-get`, sử dụng hộp thoại xác thực quyền quản trị chuẩn của Ubuntu.
- AppImage update giờ chờ GUI cũ thoát, thay AppImage theo kiểu atomic trong cùng thư mục, khôi phục executable bit và tự mở lại phiên bản mới. Nếu cần quyền ghi cao hơn, helper dùng `pkexec install` thay vì yêu cầu lệnh Terminal thủ công.
- GUI cũ chủ động thoát Qt event loop sau khi helper đã khởi chạy, loại bỏ trạng thái cửa sổ cũ đứng im/không thao tác được trong lúc cập nhật. Nếu xác thực `.deb` bị hủy hoặc quá trình cài/thay AppImage thất bại, helper cố gắng mở lại bản đang có để người dùng không bị mất GUI.
- Linux release CI smoke-test thêm entry `--apply-verified-update --help` trên cả x64 và ARM64 để đảm bảo update-helper thực sự được đóng gói trong executable phát hành.

### Validation

- Focused updater/release workflow regression: **35/35 tests PASS**; full regression: **239/239 tests PASS**.
- Windows native GUI rebuild PASS; `--smoke-test` và packaged `--apply-verified-update --help` đều exit `0`, xác nhận thay đổi entrypoint không làm hỏng Windows runtime.

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
