# Gateway recovery và cây biến Keil — đặc tả yêu cầu

## Mục tiêu

1. Gateway theo dõi ST-Link/target và quản lý dịch vụ debug trong suốt chế độ Gateway.
2. Client được cảnh báo sớm khi mất USB, target, OpenOCD hoặc SSH; tự đồng bộ endpoint/tunnel/cấu hình VS Code khi Gateway thay đổi.
3. MONITOR GUI duyệt được biến global/static và thành phần struct, union, mảng từ AXF/ELF Keil, theo dõi từng trường mà không chủ động halt MCU.
4. Khi Client kết nối SSH mà Gateway chưa chạy, Client kiểm tra capability/version của CLI trên IPC và yêu cầu chính CLI đó khởi động Gateway; chỉ mở VS Code sau khi nhận snapshot READY mới.
5. Cấu hình VS Code luôn được tạo/cập nhật từ endpoint tunnel đang sống. Đường dẫn GDB trong thư mục tạm hoặc port của generation cũ không được giữ lại.
6. Watch Live dùng catalog DWARF từ AXF/ELF để chọn biến và tự lấy kiểu scalar/member; nhập tay vẫn là đường tương thích phụ, không yêu cầu người dùng chọn JSON preset.

## Baseline và tình trạng triển khai

- Baseline đã xác minh: main `fcf70c22e5450bc28438f59c41322f7607bda176`, release v0.19.1.
- Canonical repo: `C:\Users\Admin\Documents\STM32\b300-stlink-tools`.
- Có thay đổi GUI chưa commit tại lúc lập kế hoạch. Chúng thuộc công việc đang có; không ghi đè, reset hoặc đưa lẫn vào commit tính năng mới.
- Các tài liệu này chỉ là kế hoạch, không phải tuyên bố đã triển khai.
- Sự cố quan sát: USB handle cũ bị deleted trong khi OpenOCD còn listen; USB discovery báo serial `J` nhưng OpenOCD từ chối; cổng Client `59235` thực tế hợp lệ. Không lấy TCP listen hoặc SSH connected làm bằng chứng target khỏe.
- Phiên khôi phục thủ công chạy OpenOCD trực tiếp không phải kiến trúc đích. Production phải quay về DebugService/HardwareSession và Gateway supervisor chuẩn.

## Quy tắc chung

- Giữ năm trang PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS và các shared store/manager hiện tại.
- Không phục hồi hidden legacy IDE. Local variables, stack và tham số hàm tại breakpoint tiếp tục thuộc VS Code/Cortex-Debug.
- Không flash, reset, đổi Option Bytes/RDP hoặc tự resume MCU để khôi phục kết nối.
- Không bypass HardwareSession, không mở OpenOCD thứ hai để kiểm tra target đang có owner.
- Chỉ bind GDB/TCL loopback; control/status đi qua SSH đã xác thực, không mở API điều khiển ra LAN.
- Mất liên lạc: CPU = UNKNOWN; không suy luận RUN/HALT từ trạng thái TCP hoặc poll USB.
- Không kết nối lại phiên GDB cũ vào target mới. Tự phục hồi hạ tầng; người dùng attach lại VS Code.
- Không đổi probe tự động khi danh tính chưa đủ chắc chắn. Serial ngắn/clone và USB path tái sử dụng phải được xử lý như danh tính yếu.
- Một source-of-truth cho trạng thái Gateway; Client/DEBUG/MONITOR/top cards chỉ render và sử dụng cùng snapshot.
- HW-P1-001 vẫn OPEN / DEFERRED. Không release chỉ vì unit tests PASS.

## Tiêu chí thời gian đề xuất để nghiệm thu

- Heartbeat 1 giây; mất 3 heartbeat liên tiếp: Client báo mất liên lạc trong tối đa 5 giây ở điều kiện kiểm thử.
- Lỗi USB/OpenOCD được Gateway quan sát: chuyển khỏi READY ngay; truyền cảnh báo tới Client trong tối đa 2 giây nếu SSH vẫn hoạt động.
- Không có bằng chứng target mới trong 5 giây: chuyển STALE/UNKNOWN; không giữ READY vô hạn.
- Retry phục hồi SSH/hạ tầng: 1, 2, 4, 8, 15, 30 giây, có jitter; hết vòng hiển thị action Thử lại. Không lặp restart OpenOCD liên tục trên cùng lỗi vĩnh viễn.
- Chỉ phục hồi tự động sau khi probe đã chọn được nhận diện chắc chắn. Probe khác/không rõ danh tính: WAITING_SELECTION.
- Các mốc này là mục tiêu thiết kế, cần đo với USB/network fault injection trước khi ghi vào release notes.

## Trình tự và điểm nghiệm thu

Thực hiện kế hoạch Gateway–Client trước, đạt nghiệm thu mất kết nối/phục hồi, rồi kế hoạch cây biến Keil. Hai gói có thể review và kiểm thử riêng. Cuối cùng nghiệm thu tích hợp trên IPC + laptop + board thử nghiệm được xác nhận.

Kế hoạch chi tiết:
- `../plans/2026-09-05-gateway-client-recovery.md`
- `../plans/2026-09-05-keil-monitor-tree.md`
