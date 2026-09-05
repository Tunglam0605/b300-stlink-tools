# Gateway–Client Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phát hiện sớm Gateway mất kết nối phần cứng, cảnh báo Client và tự phục hồi endpoint/tunnel mà không tự chạy lại MCU hoặc phiên GDB.

**Architecture:** Một Gateway supervisor quản lý vòng đời DebugService và xuất snapshot/event có generation qua SSH. GatewaySessionManager trên Client nhận trạng thái chung; VsCodeDebugBridge quản lý một tunnel và cập nhật đúng configuration B300 trước khi cho attach.

**Tech Stack:** Python theo compatibility floor hiện hành của CI, PySide6, SSH/Paramiko, OpenOCD, unittest và runner cách ly hiện có.

**Spec:** `../specs/2026-09-05-gateway-recovery-keil-monitor.md`

## Global Constraints

- Mọi quy tắc trong Spec áp dụng cho tất cả nhiệm vụ.
- Chỉ lập kế hoạch trong phiên hiện tại. Khi triển khai, đọc AGENTS.md và safety docs, tạo worktree `codex/gateway-client-recovery` từ main được xác minh; bảo toàn thay đổi GUI chưa commit.
- Gateway readiness phải có USB/target evidence; TCP listen không đủ.
- Mỗi nhiệm vụ theo chu trình: test RED → triển khai → test GREEN → review → commit riêng. Không chạy thử lỗi bằng cách rút ST-Link của phiên người dùng đang debug.

## A1 — Tái hiện lỗi và chốt model trạng thái (P0)

**Files:** Tạo `b300_core/gateway_status.py`, `tests/test_gateway_status.py`; đọc `gateway_readiness.py`, `debug_service.py`, `vscode_bridge.py`, `probe.py`.

**Interfaces dự kiến:** `GatewaySnapshot` immutable với schema_version, instance_id, generation, sequence, state, reason_code, selected_probe, gdb_endpoint, tcl_endpoint, cpu_state và evidence_age_ms. State gồm STOPPED, WAITING_PROBE, WAITING_SELECTION, STARTING, READY, DISCONNECTED, FAILED. Client bổ sung freshness/connection state tại chỗ; không sửa snapshot do Gateway phát.

- [ ] Viết test giữ TCP listener nhưng USB attachment đã mất; snapshot phải DISCONNECTED và endpoint không được dùng để attach.
- [ ] Viết test event cũ/sequence ngược bị bỏ qua, Gateway restart đổi instance_id, mất freshness không giữ READY.
- [ ] Chạy `python scripts/run_unittest_module.py tests.test_gateway_status`, xác nhận RED.
- [ ] Tạo model và validator: từ chối schema không hỗ trợ, state/endpoint mâu thuẫn, generation âm; elapsed time sử dụng monotonic clock, không so giờ laptop với IPC.
- [ ] Chạy lại module, review invariant và commit `feat: model gateway health and connection generations`.

**Done:** Mọi component phân biệt được SSH connected, OpenOCD listening và target verified; có fixture trạng thái cho nhiệm vụ sau.

## A2 — Phát hiện USB mất/cắm lại và danh tính probe (P0)

**Files:** Sửa `b300_core/probe.py`, `probe_selection.py`; tạo `b300_core/probe_presence.py`, `tests/test_probe_presence.py`; mở rộng `tests/test_core_probe_selection.py`.

**Interfaces dự kiến:** `ProbePresenceTracker.observe(probes, attachment_ids)` tạo sự kiện PRESENT/REMOVED/REPLACED/AMBIGUOUS. Identity logic và USB attachment generation là hai khái niệm riêng. Không dùng USB path làm serial.

- [ ] Fixture: mất `/dev/bus/usb` node, cắm lại đổi node, cùng path nhưng thiết bị khác, hai clone cùng serial, một descriptor `J` bị OpenOCD từ chối.
- [ ] Chạy test RED trước khi thay resolver.
- [ ] Dùng discovery OS read-only; tránh gọi OpenOCD mới hoặc halt để scan. Giữ nguyên strict selection của flash.
- [ ] Chỉ cho auto-select không serial khi đúng một probe và policy xác nhận serial không dùng được; trường hợp mơ hồ trả WAITING_SELECTION. Không chuyển sang probe khác vì probe cũ mất.
- [ ] Chạy `tests.test_probe_presence`, `tests.test_core_probe_selection`, `tests.test_cli_factory_probe_policy`; review và commit `fix: track probe detach and ambiguous identities`.

**Done:** Replug làm attachment generation đổi; scan không làm mất owner hiện tại và không tạo target evidence giả.

## A3 — Gateway supervisor và API trạng thái qua SSH (P0)

**Files:** Tạo `b300_core/gateway_supervisor.py`, `gateway_protocol.py`, `tests/test_gateway_supervisor.py`, `tests/test_gateway_protocol.py`; sửa `b300_stlink.py`, `b300_core/debug_service.py`, `gateway_setup.py`.

**CLI dự kiến:** giữ `debug gateway`; bổ sung `debug gateway-status --json`, `debug gateway-events --json`, `debug gateway-rescan --json`. Protocol có version/capabilities. Control endpoint chỉ dành cho cùng user trên IPC, gọi qua SSH; không mở cổng LAN.

- [ ] Test fake process giữ port sau USB detach: supervisor phải bỏ READY, ngắt connection cũ, cleanup owner rồi chờ probe.
- [ ] Test process chết, target SWD lỗi, cleanup timeout, restart service, stop do user và rescan khi đang có GDB client.
- [ ] Chạy hai module mới để ghi nhận RED.
- [ ] Triển khai state machine dùng A1/A2. Sự kiện USB và log fatal phát ngay; health query qua session hiện có phải serialized, có timeout, không halt/reset và không tranh TCL với client.
- [ ] Không có phép đọc health an toàn khi session đang bận: đánh dấu stale sau deadline, không tạo probe thứ hai. Không dùng GDB attach để làm heartbeat.
- [ ] Sau detach, chỉ restart khi probe đủ danh tính quay lại; giới hạn retry. Stop thủ công ngăn auto-restart. Cleanup chưa xong không giải phóng HardwareSession giả.
- [ ] Events chứa reason_code, endpoint, instance/generation và sequence. GDB listener chỉ READY sau evidence mới; mọi GDB connection generation cũ phải đóng.
- [ ] Gắn supervisor vào đường `debug gateway` và service setup chuẩn; kiểm thử restart/stop không có `reset`, `resume`, `load`, erase hoặc option-byte writes.
- [ ] Chạy module mới và `tests.test_debug_service`, `tests.test_gateway_readiness`, `tests.test_gateway_setup`; review và commit `feat: supervise gateway hardware and publish health events`.

**Done:** Gateway tiếp tục theo dõi khi không có Client; có thể chờ probe và phục hồi đúng probe mà không mở lại phiên GDB cũ.

## A4 — Client nhận cảnh báo sớm và quét IPC (P0)

**Files:** Sửa `b300_core/gateway_sessions.py`, `remote_session.py`; tạo `b300_gui/gateway_health_controller.py`, `tests/test_gateway_health_controller.py`; sửa `gateway_manager_dialog.py`, `views/debug_vscode_view.py`, `views/monitor_view.py`, `main_window_v18.py`.

**Interfaces dự kiến:** `GatewayHealthController` phát `snapshot_changed`, `connection_lost`, `recovered`. Một controller/subscription cho mỗi profile, dùng GatewaySessionManager chung cho DEBUG/MONITOR. Snapshot từ A1 và event stream A3; disconnect tuổi dữ liệu tính bằng clock Client.

- [ ] Test event USB loss khi SSH còn sống; SSH đứt không có event cuối; event không tới dù keepalive SSH thành công; message trễ từ generation cũ.
- [ ] Chạy test RED với fake clock và transport, không dùng sleep dài.
- [ ] Tạo subscription/reconnect worker, heartbeat theo Spec, thông báo theo transition để không spam. Hết retry hiển thị Thử lại.
- [ ] Banner luôn thấy khi đổi trang: Mất ST-Link / Mất target / OpenOCD lỗi / Mất liên lạc Gateway. Tắt CTA attach và đánh dấu MONITOR stale ngay.
- [ ] Thêm action “Quét ST-Link trên Gateway” gọi IPC qua SSH; quét local vẫn thuộc global header. Khi quét không có thiết bị, giữ WAITING_PROBE. Không restart owner khỏe chỉ vì user scan.
- [ ] Gateway cũ không có protocol: hiện “Chưa hỗ trợ giám sát Gateway — cần cập nhật”, không giả định có cảnh báo sớm.
- [ ] Chạy `tests.test_gateway_health_controller`, `tests.test_shared_profiles`, `tests.test_shared_managers_ui` và UI suite cách ly; review và commit `feat: warn clients promptly on gateway disconnect`.

**Done:** Trong fault injection, cảnh báo đạt deadline Spec; mọi trang render cùng nguyên nhân và trạng thái freshness.

## A5 — Một tunnel owner, ổn định cổng và bảo toàn launch.json (P0)

**Files:** Sửa `b300_core/vscode_bridge.py`, `ssh_debug_tunnel.py`, `b300_gui/vscode_debug_controller.py`; tạo `tests/test_gateway_endpoint_sync.py`; mở rộng `tests/test_v018_vscode_bridge.py`, `tests/test_v018_vscode_controller.py`.

**Interfaces dự kiến:** Bridge nhận `GatewaySnapshot`; lưu binding `(profile_id, instance_id, generation, remote_endpoint, local_endpoint)`. Controller xuất endpoint thực tế sau bind. Configuration B300 được đánh dấu owner/id, không nhận ownership chỉ dựa vào tên trùng.

- [ ] Test remote GDB đổi port trong khi local port vẫn giữ; laptop port đã bị chiếm; Gateway restart cùng port nhưng generation mới; profile đổi trong lúc reconnect.
- [ ] Test launch.json có comment, cấu hình riêng, chỉnh sửa chưa lưu/xung đột hash, đường GDB temp bị xóa. Test GDB đang chạy không bị âm thầm nối sang target mới.
- [ ] Chạy module mới và bridge/controller tests để xác nhận RED.
- [ ] Giữ listener laptop khi khả thi; chỉ đổi upstream sau khi đóng các channel GDB cũ. Khi local port mất quyền sở hữu, bind port mới rồi cập nhật configuration B300 bằng ghi atomic và kiểm tra file revision.
- [ ] Nếu không chứng minh được quyền sở hữu configuration, hiện diff/action xác nhận trước overwrite. Không ghi đè file người dùng hoặc sửa buffer chưa lưu. Chỉ báo attach-ready khi listener và config khớp.
- [ ] Resolve GDB từ runtime quản lý đang tồn tại; không giữ đường thư mục test/temp trong cấu hình lâu dài.
- [ ] Gateway phục hồi: tunnel tự sync, banner “Sẵn sàng attach lại”; không tự F5/continue/reset. Dừng dùng tunnel thủ công song song trong workflow sản phẩm.
- [ ] Chạy các module ở Files với canonical runner; review và commit `fix: synchronize gateway endpoints and managed vscode profiles`.

**Done:** User không phải biết `59235` hay `43328`; endpoint hiển thị và launch.json luôn tương ứng tunnel đang sở hữu.

## A5b — Client tự bảo đảm Gateway CLI đang chạy (P0)

**Files:** Sửa `b300_core/gateway_sessions.py`, `remote_session.py`, `b300_gui/vscode_debug_controller.py`; tạo `tests/test_gateway_remote_ensure.py`.

- [ ] Test SSH reachable nhưng Gateway stopped, CLI v0.20+ có capability phù hợp: Client gọi lệnh ensure idempotent, chờ snapshot READY rồi mới tạo tunnel/config.
- [ ] Test CLI thiếu, quá cũ, khởi động lỗi hoặc không có probe: báo nguyên nhân cụ thể, không ghi `launch.json`, không mở VS Code.
- [ ] Lệnh remote không chứa password; chỉ chạy CLI per-user đã xác minh qua SSH session hiện có, không sudo và không mở GDB/TCL ra LAN.
- [ ] Hai Client gọi đồng thời không tạo hai OpenOCD owner; ensure trả cùng instance/generation đang khỏe.
- [ ] Khi endpoint/generation thay đổi, đóng channel cũ, lấy endpoint mới và cập nhật atomic configuration B300 trước khi báo attach-ready.

## A6 — Nghiệm thu và bàn giao (P0)

**Files:** Cập nhật `docs/04_DEBUG.md`, `docs/05_TROUBLESHOOTING.md`; tạo `docs/acceptance/gateway-recovery.md`; sửa `.github/workflows/ci.yml` nếu suite mới cần split cases.

- [ ] Full regression một lần trên tree cuối bằng `scripts/run_unittest_module.py`; GUI suite dùng `--split-cases --case-timeout 60`, timeout là FAIL, native teardown chỉ chấp nhận khi có PASS sentinel theo runner.
- [ ] Chạy `python -m compileall -q b300_core b300_gui tests` và `git diff --check`.
- [ ] Trên board thử nghiệm đã được xác nhận, ghi log/timestamp: rút/cắm USB, mất SWD, tắt OpenOCD, ngắt SSH, restart IPC, đổi port, cắm probe khác, nhiều probe, stop thủ công.
- [ ] Xác minh GUI cảnh báo đúng deadline; MONITOR stale; VS Code phiên cũ ngắt; probe khác đòi chọn; không flash/reset/auto-resume; không còn process/tunnel orphan.
- [ ] Review toàn bộ, lưu bằng chứng. Cập nhật IPC và Client cùng protocol khi triển khai được cho phép; không tự cài vào máy đang debug.
- [ ] Chỉ đề xuất build/release sau nghiệm thu; giữ HW-P1-001 OPEN / DEFERRED nếu chưa có bằng chứng tương ứng.

**Gate sang gói B:** A1–A5 PASS, state/freshness contract ổn định; A6 hardware status báo trung thực.
