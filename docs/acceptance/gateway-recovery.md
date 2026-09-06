# Gateway recovery — software acceptance

## Phạm vi đã tự động hóa

- Snapshot fail-closed phân biệt SSH, process, probe, target và endpoint READY.
- CLI per-user hỗ trợ `gateway-status`, `gateway-ensure`, `gateway-rescan`; không
  dùng `sudo`, password trong command hoặc bind debug ra ngoài loopback.
- Hai lệnh ensure không tạo thêm owner khi tiến trình Gateway đang READY hoặc đang
  chờ probe. Rescan gửi request tới owner đang sống.
- Client lấy GDB/TCL port từ snapshot, thay channel cũ khi instance/generation/port
  đổi và ghi atomic entry B300 trong `launch.json`.
- Cortex-Debug profile bật Live Watch và giữ nguyên các configuration không thuộc B300.

## Nghiệm thu phần cứng còn mở

Chưa dùng tài liệu này để tuyên bố release pass. Cần chạy trên IPC, laptop và board
được xác nhận: rút/cắm ST-Link, mất SWD, kill OpenOCD, ngắt SSH, đổi port, cắm probe
khác và nhiều probe. Phải xác nhận không flash/reset/auto-resume, GDB cũ bị đóng,
cảnh báo đúng thời gian và không còn process/tunnel orphan. `HW-P1-001` giữ OPEN.
