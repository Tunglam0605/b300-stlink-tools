# B300 ST-Link GUI — thiết kế và handoff cho Antigravity

**Ngày:** 2026-08-27  
**Trạng thái:** Thiết kế đã được chủ dự án duyệt  
**Phạm vi:** STM32F407 Main Board B300, nạp Application qua ST-Link/SWD

## 1. Mục tiêu

Xây dựng giao diện desktop chuyên dụng để người vận hành nạp Application B300
qua ST-Link mà không cần hiểu OpenOCD hay bản đồ flash. Sản phẩm phải có:

- CLI hiện tại dùng được trên Windows Terminal/PowerShell và Ubuntu Terminal;
- GUI Windows dạng executable/installer;
- GUI Ubuntu dạng AppImage và gói `.deb`;
- cùng một lõi validate và flash cho CLI lẫn GUI;
- trải nghiệm gần STM32CubeProgrammer nhưng chỉ hiện các chức năng B300 được phép.

GUI không thay thế hệ thống OTA trên Raspberry Pi và không truyền firmware qua
UART. ST-Link/SWD không cần COM port, vì vậy GUI không có COM selector, Serial
Monitor hoặc xử lý `OTACHECK`.

## 2. Phạm vi phiên bản đầu

### Có trong v1

- Phát hiện và chọn ST-Link probe; yêu cầu chọn serial khi có nhiều probe.
- Đọc chip ID, điện áp target, flash size, trạng thái kết nối và WRP read-only.
- Chọn Intel HEX Application link tại `0x08010000`.
- Hiển thị tên file, SHA-256, địa chỉ đầu/cuối, dung lượng và sector bị tác động.
- Dry-run và hiển thị transaction trước khi người dùng xác nhận.
- Erase Sector 3–7, program, verify, ghi provisioning marker rồi reset.
- Đọc lại trạng thái boot và hiển thị kết quả rõ ràng.
- Đọc/hiển thị/xuất nội dung Sector 0–7 qua SWD.
- Giải mã vector Application và OTA metadata Sector 3.
- Log theo thời gian thực và xuất log phục vụ truy vết.

### Không có trong v1

- Nạp Bootloader.
- Mass/chip erase.
- Ghi tùy ý vào memory.
- Sửa Option Bytes hoặc WRP.
- OTA qua UART/COM, Serial Monitor hoặc theo dõi `OTACHECK`.
- GUI debug/GDB. Lệnh `b300-stlink debug` vẫn dành cho kỹ thuật viên qua CLI.
- Tự retry khi erase/program/verify thất bại.

## 3. Ranh giới an toàn bắt buộc

| Vùng | Địa chỉ | Chính sách |
|---|---|---|
| Bootloader, Sector 0–2 | `0x08000000..0x0800BFFF` | Chỉ đọc; không erase/program |
| OTA metadata, Sector 3 | `0x0800C000..0x0800FFFF` | Chỉ thay đổi trong transaction provisioning |
| Application, Sector 4–7 | `0x08010000..0x0807FFFF` | Vùng duy nhất nhận dữ liệu HEX |

Không được cung cấp chế độ nâng cao để lách các giới hạn trên. Không xây dựng
chuỗi lệnh OpenOCD trong widget hoặc mã GUI. Mọi thao tác phải đi qua core đã
kiểm tra policy. Legacy provisioning marker (superseded) từng được ghi vào BKP4R sau
khi program và verify thành công.

## 4. Kiến trúc mục tiêu

```text
b300_stlink.py (CLI) ─┐
                     ├─> b300_core ─> OpenOCD process ─> ST-Link/SWD ─> F407
b300_gui (PySide6) ───┘
```

Cấu trúc đề xuất:

```text
b300_core/
  models.py             Kiểu dữ liệu probe, image, memory, flash result
  hex_image.py          Parse/validate Intel HEX và tính hash
  policy.py             Bản đồ sector và các invariant an toàn
  openocd.py            Tạo/chạy command, stream log, timeout và cancel an toàn
  probe.py              Phát hiện và chọn ST-Link
  flash_service.py      Điều phối transaction provisioning
  memory_service.py     Đọc flash, vector và metadata; không ghi tùy ý
  metadata.py           Decode OtaMeta 44 byte
b300_gui/
  app.py
  main_window.py
  viewmodels/
  widgets/
  resources/
packaging/
  windows/
  linux/
tests/
  unit/
  integration/
  gui/
```

`b300_core` không phụ thuộc PySide6. CLI và GUI gọi cùng API, nhờ đó không tồn
tại hai cách validate hoặc hai transaction flash khác nhau. OpenOCD chạy trong
worker process/thread; UI không được block và log phải được đẩy lên theo thời
gian thực.

## 5. API lõi đề xuất

Tên cụ thể có thể điều chỉnh, nhưng trách nhiệm và dữ liệu trả về phải giữ ổn
định:

```python
list_probes() -> list[ProbeInfo]
inspect_target(probe: ProbeRef) -> TargetInfo
inspect_image(path: Path) -> ImageInfo
build_flash_plan(target: TargetInfo, image: ImageInfo) -> FlashPlan
flash_application(plan: FlashPlan, events: EventSink) -> FlashResult
read_memory(probe: ProbeRef, address: int, length: int) -> bytes
decode_ota_metadata(data: bytes) -> OtaMetadata
verify_boot(probe: ProbeRef) -> BootVerification
```

`FlashPlan` là immutable và phải liệt kê rõ sector erase, file, hash, probe và
các lệnh dự kiến. `flash_application()` từ chối plan không đúng policy, kể cả
khi caller là GUI.

## 6. Luồng nạp

1. `Disconnected`: tìm probe.
2. `TargetReady`: đọc target và khóa đúng probe serial.
3. `ImageSelected`: parse toàn bộ HEX và kiểm tra range.
4. `PlanReady`: hiển thị file/hash/range/sector/transaction.
5. `AwaitingConfirmation`: người dùng xác nhận thao tác xóa Sector 3–7.
6. `Erasing`: chỉ `flash erase_sector 0 3 7`.
7. `Programming`: program Application HEX.
8. `Verifying`: OpenOCD phải báo verify thành công.
9. `MarkingProvisioned`: legacy state (superseded; no longer implemented).
10. `Resetting`: reset run.
11. `PostVerifying`: đọc trạng thái boot.
12. `Succeeded` hoặc `Failed`.

Từ `Erasing` trở đi, lỗi nào cũng chuyển thẳng sang `Failed`. Không retry tự
động và tuyệt đối không ghi marker nếu chưa qua `Verifying`.

## 7. Xác minh sau nạp

Điều kiện thành công kỹ thuật:

- OpenOCD exit code bằng 0 và có `Verified OK`;
- BKP1R tại `0x40002854` bằng 0;
- Legacy BKP4R state was consumed (superseded; no longer part of the contract);
- PC nằm trong `0x08010000..0x0807FFFF` sau boot window;
- target được `resume` trước khi đóng phiên đọc trạng thái.

Nếu verify flash thành công nhưng post-verify không đạt, GUI phải hiển thị
`Programmed, boot verification failed`, giữ log và hướng dẫn kỹ thuật viên kiểm
tra Bootloader/metadata. Không tự nạp lại.

## 8. Bố cục GUI

Một cửa sổ chính, không dùng wizard nhiều tầng:

- **Thanh thiết bị:** probe, serial, điện áp, chip ID, nút Refresh/Connect.
- **Firmware:** file picker, SHA-256, size, address range và validation result.
- **Flash plan:** bảng Sector 3–7 và hành động tương ứng.
- **Actions:** Dry run, Flash Application, Cancel khi còn an toàn.
- **Progress:** trạng thái state machine và progress xác định được.
- **Memory:** bảng Sector 0–7, Read/View/Export; không có Write/Erase tùy ý.
- **Metadata:** magic, format, state, size, image CRC, board token, sequence,
  metadata CRC và kết luận valid/erased/corrupt.
- **Log:** thời gian, mức độ, command đã chuẩn hóa và output OpenOCD.

Nút Flash chỉ bật khi target và image đều hợp lệ. Hộp xác nhận cuối phải nêu
đúng probe serial, SHA-256 file và `Erase Sector 3–7`; không dùng câu xác nhận
chung chung.

## 9. Đóng gói và phân phối

PySide6 là framework GUI đã được duyệt. Mỗi artifact phải kèm đúng OpenOCD đã
pin và checksum:

- Windows x64: portable ZIP và installer EXE;
- Ubuntu x86_64: AppImage và DEB;
- Ubuntu ARM64: AppImage/DEB khi OpenOCD bundle tương ứng vượt acceptance test.

Không tải OpenOCD mới âm thầm khi ứng dụng chạy. Version GUI, core và OpenOCD
phải xuất hiện trong màn hình About và log đầu phiên. Release source không chứa
firmware HEX của sản phẩm.

## 10. Kiểm thử và CI

- Unit test: Intel HEX, checksum/range, sector policy, metadata decode, state
  transitions và command generation.
- Integration test: fake OpenOCD process với success/failure/timeout/cancel;
  kiểm tra marker không xuất hiện trước verify.
- GUI test: enable/disable action, confirmation content và state rendering.
- Packaging smoke test trên Windows x64, Ubuntu x86_64 và Ubuntu ARM64.
- Hardware acceptance thủ công trên F407 trước release; CI không flash board.

Ca hardware bắt buộc:

1. App có metadata erased được provision và boot thành công.
2. App mới nạp raw trong khi còn metadata `CONFIRMED` cũ bị Bootloader từ chối.
3. Cùng App đó nạp bằng tool được Bootloader chấp nhận.
4. HEX chạm Sector 0–2 bị từ chối trước mọi thao tác phần cứng.
5. Verify fail không ghi marker và không retry.
6. Mất kết nối probe giữa phiên được báo minh bạch, giữ log.

## 11. Phase triển khai cho Antigravity

1. Tách logic thuần từ `b300_stlink.py` sang `b300_core`, giữ CLI tương thích.
2. Viết unit/integration test cho core và chạy trên Windows/Ubuntu.
3. Tạo PySide6 shell và luồng probe/image/dry-run.
4. Tích hợp flash state machine, streaming log và post-verify.
5. Thêm Memory/Metadata read-only.
6. Hoàn thiện lỗi, accessibility, export log và About/version.
7. Đóng gói từng nền tảng, chạy packaging smoke test.
8. Chạy hardware acceptance và phát hành pre-release để chủ dự án duyệt.

Mỗi phase phải giữ CLI hiện tại hoạt động. Không gộp refactor core, toàn bộ GUI
và packaging vào một commit duy nhất.

## 12. Tiêu chí nghiệm thu

- Người dùng mới có thể cài, chọn probe, chọn HEX và nạp mà không nhập lệnh.
- Windows và Ubuntu dùng cùng policy, transaction và thông báo trạng thái.
- Không có đường UI/API công khai nào erase/program Sector 0–2.
- Raw ST-Link và provisioning tool được giải thích rõ trong UI/tài liệu.
- CLI cũ vẫn chạy và có cùng kết quả với GUI.
- Mọi lỗi có phase, nguyên nhân, log và hành động tiếp theo; không có retry ẩn.
- Artifact chạy trên máy sạch theo hướng dẫn, không cần CubeProgrammer.

## 13. Chỉ dẫn bắt đầu cho Antigravity

Đọc lần lượt `AGENTS.md`, `README.md`, `docs/03_FLASH_FIRMWARE.md`, file handoff
này và toàn bộ test hiện có. Không sửa transaction trước khi viết test mô tả
invariant. Bước đầu tiên là tạo branch riêng từ `main`, chạy test baseline và
viết kế hoạch theo các phase ở mục 11; chưa dựng GUI trước khi core được tách và
test tương thích CLI hoàn tất.
