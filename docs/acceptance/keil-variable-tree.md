# Keil AXF/ELF variable tree — software acceptance

## Phạm vi đã tự động hóa

- Catalog đọc DWARF offline, cache identity theo fingerprint và phân trang tối đa
  100 node; không dùng `nm` để đoán kiểu member.
- Cây hỗ trợ global/static, struct, union, mảng, enum, packed field và bitfield.
- Leaf hợp lệ được biên dịch thành bounded `LiveWatch`; tối đa 16 watch và 32 word
  mỗi chu kỳ, chỉ đọc vùng RAM allow-list.
- Giao diện tự tải catalog ở worker nền, chọn nhiều biến từ cây và tự nhận dạng kiểu;
  không cần trường nhập kiểu hoặc preset JSON trong production flow.
- Mất Gateway làm giá trị/timestamp cuối chuyển `STALE`, hủy monitor và loại mẫu muộn.

## Giới hạn và nghiệm thu phần cứng còn mở

Pointer không tự dereference; local/parameter/stack thuộc debugger đang halted;
optimized-out hoặc DWARF mơ hồ không thể watch. Mẫu nhiều word không được tuyên bố
atomic. Cần đối chiếu kiểu/offset/giá trị với Keil hoặc VS Code trên board được xác
nhận và đo tải SWD/độ đáp ứng GUI trước khi phát hành.
