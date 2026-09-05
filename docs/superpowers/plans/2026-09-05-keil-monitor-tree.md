# Keil Variable Tree Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Duyệt biến global/static từ AXF/ELF Keil, mở các thành phần struct/union/mảng và theo dõi từng trường trên GUI mà không chủ động halt target.

**Architecture:** Tách catalog kiểu dữ liệu offline khỏi bộ đọc RAM. Cây biến tải children theo yêu cầu, biên dịch leaf được chọn thành LiveWatch hợp lệ và đi qua LiveMonitorSession/HardwareSession hiện có; freshness dùng chung lifecycle Gateway của gói A.

**Tech Stack:** Python, PySide6 tree model, ELF/DWARF offline, LiveMonitorSession, SafeTclClient, unittest. Quyết định parser/dependency sau fixture compatibility gate B1, không suy đoán cấu trúc từ nm.

**Spec:** `../specs/2026-09-05-gateway-recovery-keil-monitor.md`

## Global Constraints

- Các safety/ownership rules của Spec và gói Gateway–Client áp dụng đầy đủ.
- Giữ compatibility floor Python của CI hiện hành; không hồi sinh legacy debug workbench.
- Local variables/stack/parameters theo frame thuộc VS Code khi halted; không đưa chúng vào zero-halt MONITOR.
- Không đọc MMIO hoặc dereference pointer tùy ý; không thay đổi RAM/register khi browse hoặc watch.
- Giữ các budget hiện tại: MAX_LIVE_WATCHES=16, MAX_LIVE_READ_WORDS=32, MIN_LIVE_INTERVAL_SECONDS=0.1. Không âm thầm tăng tải SWD khi mở cây.
- Không commit firmware/AXF proprietary. Fixture nhỏ tự tạo; build artifact chỉ trong thư mục temp.
- Triển khai trong worktree riêng từ baseline đã tích hợp gói A, bảo toàn thay đổi GUI hiện có.

## B1 — Xác minh metadata Keil và catalog kiểu dữ liệu (P1)

**Files:** Mở rộng `b300_core/offline_symbols.py`; tạo `b300_core/typed_symbols.py`, `tests/test_typed_symbols.py`, `tests/fixtures/typed_variables.c`.

**Interfaces dự kiến:** `TypedSymbolCatalog.roots(query, offset, limit)` và `.children(node_id, offset, limit)` trả `VariableNode`: node_id, name, type_name, kind, address, byte_size, has_children, availability, reason. Node id gắn file hash và CU/DIE identity, không chỉ tên symbol.

- [ ] Fixture chứa typedef, enum, nested struct, union, packed struct/bitfield, mảng nhiều chiều, static trùng tên ở hai source unit, const/volatile, pointer và biến optimized-out.
- [ ] Dùng AXF thực tế được người dùng cho phép chỉ để kiểm tra read-only metadata: compiler/DWARF version, member offsets, bit offset và address location. Không mở hardware GDB session để browse type.
- [ ] Viết literal expected offsets/types độc lập với implementation; file không DWARF, DWARF lỗi và type unsupported phải trả reason, không đoán.
- [ ] Chạy `python scripts/run_unittest_module.py tests.test_typed_symbols` và ghi nhận RED.
- [ ] Chọn parser DWARF offline hỗ trợ fixture thực tế; nếu cần dependency mới, pin version theo Python/Windows/Linux của CI và cập nhật build requirements/bundles trong cùng task. Không đọc kiểu thành viên bằng regex từ nm.
- [ ] Cache catalog theo SHA256 của AXF; file đổi hủy cache và node id cũ. Enum giữ cả numeric value và nhãn; union hiển thị mọi member nhưng không suy đoán member đang active.
- [ ] Test GREEN, review compatibility evidence rồi commit `feat: index typed keil symbols offline`.

**Done:** Browse đủ kiểu fixture ở offline mode. Kiểu/địa chỉ không xác định được hiển thị unavailable thay vì gán giá trị.

## B2 — Chuyển thành phần biến thành phép đọc RAM an toàn (P1)

**Files:** Tạo `b300_core/variable_watch.py`, `tests/test_variable_watch.py`; sửa `live_monitor.py`, `live_session.py` tại adapter cần thiết; tái sử dụng `tcl_client.py` và `live_service.py`.

**Interfaces dự kiến:** `compile_watch(catalog, node_id)` trả `LiveWatch` hoặc lỗi có reason_code; bộ decode lấy type, byte offset, bit offset, signedness từ B1. Session giữ symbol hash + target generation cho mọi watch.

- [ ] Test `xAgvInfor.position.x`, `motors[0].rpm`, packed field, signed bitfield, enum, mảng biên cuối; expected bytes/values literal.
- [ ] Test offset/size overflow, null/invalid pointer, MMIO address, stale node_id, vượt 16 watches/32-word read budget: từ chối trước transport call.
- [ ] Chạy `python scripts/run_unittest_module.py tests.test_variable_watch`, xác nhận RED.
- [ ] Bước đầu pointer chỉ hiển thị địa chỉ; pointee hiển thị “Chưa hỗ trợ theo dõi an toàn”. Không dereference động hoặc đọc thanh ghi ngoại vi chỉ vì file debug có symbol.
- [ ] Với field trong allow-listed RAM: nhóm reads có giới hạn, giữ cadence hiện tại; thêm trường mới không đọc toàn bộ struct/mảng.
- [ ] Đánh dấu mẫu không coherent khi không chứng minh được tính nhất quán; không gọi một struct đang thay đổi là snapshot atomic. Giữ timestamp và quality trên từng mẫu.
- [ ] Log test phải chứng minh không phát `halt`, `reset`, `resume`, memory write hoặc GDB `load` khi MONITOR chạy.
- [ ] Chạy module mới cùng `tests.test_live_monitor`, `tests.test_live_session`, `tests.test_tcl_live_read`; review và commit `feat: compile typed fields into bounded live reads`.

**Done:** Theo dõi leaf trong RAM bằng core hiện tại, không có đường truy cập hardware riêng trong widget.

## B3 — Cây biến MONITOR, tìm kiếm và watch preset (P1)

**Files:** Tạo `b300_gui/variable_tree_model.py`, `variable_tree_panel.py`, `tests/test_variable_tree_model.py`, `tests/test_monitor_variable_tree.py`; sửa `views/monitor_view.py`, `debug_live_panel.py`, `live_monitor_controller.py`, `symbol_browser_dialog.py` nếu cần tái sử dụng browser chung.

**Interfaces dự kiến:** Tree model tiêu thụ VariableNode từ B1; action chọn leaf gửi node_id tới B2. UI không tính offset hoặc parse kiểu. Hiển thị Name/Type/Value/Address/Quality/Last update.

- [ ] Test expand/collapse struct, nhiều cấp mảng, tên trùng, search, chọn leaf để watch/plot, unsupported/optimized-out không có action watch.
- [ ] Test 10.000 symbols và mảng lớn: page children tối đa 100, độ sâu tự động tối đa 8; không materialize toàn bộ cây hoặc block GUI bằng parse đồng bộ.
- [ ] Chạy model/UI module với canonical runner, xác nhận RED.
- [ ] Tích hợp tree vào MONITOR chính; mở cây không chạy GDB hoặc sinh HardwareSession mới. Graph chỉ bật cho scalar numeric hợp lệ.
- [ ] Preset lưu symbolic path + file fingerprint/type identity, không tin địa chỉ cũ. Khi AXF thay đổi, resolve lại; field mất/đổi kiểu báo lỗi và không tiếp tục đọc địa chỉ trước.
- [ ] Giữ đọc watch đơn giản đang có tương thích; không dựng lại trang DEBUG IDE cũ.
- [ ] DEBUG/VS Code hiển thị cùng panel Watch Live để chọn trực tiếp từ catalog AXF/ELF; không bắt người dùng nhập tên/kiểu hoặc chọn file JSON preset cho luồng thông thường.
- [ ] Kiểu scalar/member (`u8/i8/u16/i16/u32/i32/f32/f64`, enum và bitfield hỗ trợ) lấy từ DWARF. Nếu metadata thiếu hoặc mơ hồ thì khóa action và giải thích, không đoán từ kích thước hoặc tên.
- [ ] Test GREEN gồm smoke GUI, review rồi commit `feat: browse and watch nested keil variables in monitor`.

**Done:** User mở xAgvInfor → position → x rồi thêm watch/plot, không cần nhập địa chỉ hoặc kiểu bằng tay.

## B4 — Firmware match, stale và phục hồi watch (P0 trước bàn giao)

**Files:** Sửa `b300_core/elf_matcher.py`, `live_session.py`, `b300_gui/live_monitor_controller.py`; tạo `tests/test_monitor_freshness.py`; dùng `gateway_health_controller.py` và GatewaySnapshot của gói A.

- [ ] Test USB/network loss khi giá trị đang thay đổi: mọi row chuyển STALE trong deadline, giữ giá trị cuối kèm timestamp và không giả lập mẫu mới; graph có khoảng đứt.
- [ ] Test Gateway recovered nhưng firmware/file khác: watch không tự trở thành VALID. Late sample từ generation cũ bị loại bỏ.
- [ ] Chạy `python scripts/run_unittest_module.py tests.test_monitor_freshness`, xác nhận RED.
- [ ] Tái dùng kiểm tra tương ứng firmware–symbol hiện có. MATCH/MISMATCH/UNKNOWN hiển thị rõ mức bằng chứng; sampled match không được quảng cáo là verify toàn bộ image.
- [ ] Mismatch/unknown chặn gán tên biến thành giá trị hardware “đã xác minh”; vẫn cho browse offline metadata.
- [ ] Sau phục hồi: kiểm tra lại target, firmware evidence và resolve watch trước khi người dùng bấm Tiếp tục theo dõi. Hạ tầng reconnect không tự coi sample cũ là mới.
- [ ] Test schema event cũ không có health capability: cảnh báo giới hạn giám sát thay vì báo LIVE khỏe.
- [ ] Test GREEN, review và commit `fix: invalidate monitor values across disconnect and firmware changes`.

## B5 — Nghiệm thu tích hợp và bàn giao

**Files:** Tạo `docs/acceptance/keil-variable-tree.md`; cập nhật docs MONITOR/debug tương ứng và build packaging cho dependency được chọn ở B1.

- [ ] Full regression, compileall và diff check trên tree cuối; test parser/bundle trên Windows x64, Ubuntu x64/ARM64.
- [ ] Chạy với firmware fixture trên board thử nghiệm được xác nhận; đối chiếu kiểu/offset/giá trị với Keil hoặc VS Code ở trạng thái phù hợp.
- [ ] Đo CPU/SWD sampling overhead và GUI responsiveness; xác minh log MONITOR không chủ động halt/reset, không tuyên bố mọi trường đều atomic.
- [ ] Nghiệm thu cùng gói A: mất ST-Link → Client cảnh báo → tree/graph stale → đúng probe phục hồi → endpoint tự sync → symbol revalidate → user tiếp tục monitor hoặc attach VS Code.
- [ ] Ghi giới hạn rõ: optimized-out không đọc được, pointer chỉ xem địa chỉ, local variables thuộc debug halted, AXF phải có metadata phù hợp.
- [ ] Review/test report và hardware evidence trước khi đề xuất release. Không đổi HW-P1-001 thành PASS nếu chưa chạy đúng nghiệm thu của mục đó.

**Hoàn thành toàn bộ:** A1–A6 + B1–B5 đã có test/evidence và mọi giới hạn được ghi trong tài liệu bàn giao; build/release là bước riêng được người dùng quyết định.
