# Xử lý lỗi

| Lỗi | Làm gì |
|---|---|
| `OpenOCD was not found` | Chạy lại setup của đúng hệ điều hành, sau đó mở terminal mới. |
| Không thấy ST-Link | Kiểm tra cáp/driver Windows; Ubuntu kiểm tra `lsusb` và `plugdev`. |
| `HEX touches protected range` | Dừng; dùng đúng HEX Application F407 tại `0x08010000`. |
| Verify fail | Dừng, kiểm tra nguồn/cáp/SWD/probe serial; không retry mù. |
| Board vào recovery sau nạp | Lưu log, không mass erase/không retry; kiểm tra `failure_phase`, PC, BKP1R và Sector 3. Với Bootloader v0.6.5, `ERASED`/`CORRUPT` không boot; ca ST-Link thành công phải có `STLM + CONFIRMED` sau boot. |
| `Address already in use` khi debug | Đóng OpenOCD/GDB server cũ hoặc chọn port khác. |
| GDB không kết nối được IPC | Chạy Gateway với GDB/TCL chỉ bind loopback, kiểm tra SSH TCP/22, xác nhận host key/password bằng OpenSSH bình thường và SSH local forwarding; không expose/NAT port 3333/6666. |
| GDB hiện sai source/biến | Dùng đúng AXF/ELF build từ firmware đang chạy; không dùng lệnh `load` để chữa tạm. |
| SSH vào được nhưng Gateway chưa chạy | Client sẽ gọi `b300-stlink debug gateway-status --json` rồi `gateway-ensure`. Nếu báo CLI chưa cài/quá cũ, cài hoặc cập nhật B300 CLI cho đúng user SSH; không dùng `sudo`. |
| Gateway đổi port nhưng VS Code vẫn giữ port cũ | Dừng phiên GDB cũ và chờ banner sẵn sàng attach lại. B300 tự nối lại tunnel và cập nhật entry do B300 quản lý trong `.vscode/launch.json`; nếu file có xung đột revision, xử lý file rồi thử lại. |
| Giá trị Monitor còn hiện sau khi mất Gateway | Giá trị cuối được giữ để đối chiếu nhưng cột trạng thái phải là `STALE`. Không coi đó là mẫu mới; quét/kết nối lại, xác minh đúng firmware rồi bấm bắt đầu theo dõi lại. |
| Không thấy kiểu hoặc thành phần struct trong cây AXF/ELF | Build AXF/ELF kèm DWARF. Biến optimized-out hoặc metadata mơ hồ chỉ được duyệt và không thể thêm Watch; B300 không đoán kiểu từ tên/kích thước. |
| Board còn halt sau debug | Trong GDB chạy `monitor reset run`, `detach`, `quit`, rồi dừng OpenOCD. |
| GUI không cho bấm Flash | Nhấn **Kiểm tra target**, chọn HEX hợp lệ và chờ thao tác hiện tại kết thúc. |
| Có nhiều ST-Link nhưng chưa chọn được target | Chọn đúng serial cụ thể; Auto-select bị vô hiệu để tránh nạp nhầm board. |
| `Application HEX changed after approval` | Chọn lại file, kiểm tra SHA-256 rồi xác nhận lại; tool chưa gửi lệnh erase. |
| Timeout/cancel khi đọc memory | Tool mở phiên recovery riêng để yêu cầu `resume`; lưu log nếu recovery cũng lỗi. |
| `Programmed, boot verification failed` | Xuất log; kiểm tra PC/BKP/Bootloader, không tự nạp lại. |
| AppImage không chạy | Kiểm quyền executable và udev; thử DEB cùng release, không dùng sudo chạy GUI. |

Lưu log dạng JSON khi cần báo lỗi:

```text
b300-stlink flash <file.hex> --json > b300-flash.log
```

Gateway Agent/lease reason codes:

| Lỗi | Làm gì |
|---|---|
| `GATEWAY_BUSY` | Một Client khác đang giữ lease; dùng Stop ở Client đó hoặc chờ lease hết hạn rồi thử lại. |
| `GATEWAY_AGENT_NOT_RUNNING` | Chạy `b300-stlink debug gateway-agent-ensure --json`, rồi kiểm tra lại status. |
| `GATEWAY_AGENT_START_TIMEOUT` | Agent không khởi động kịp; kiểm tra `gateway-agent-status --json`, xem log và thử lại. |
| `LEASE_EXPIRED` | Client mất heartbeat hoặc crash; kiểm tra status, đợi Agent dọn tài nguyên rồi acquire lại. |
| `GATEWAY_HEARTBEAT_STALE` | Gateway không còn phản hồi; chạy status/ensure và thử lại sau khi Agent sẵn sàng. |
| `GATEWAY_START_FAILED` | OpenOCD không khởi động được; kiểm tra probe/port, giữ nguyên lease và thử lại sau khi xử lý nguyên nhân. |
| `LEASE_INVALID` | Lease/token đã cũ hoặc không hợp lệ; dừng phiên hiện tại và acquire lease mới qua GUI. |
| `GATEWAY_NOT_READY` | Gateway chưa sẵn sàng; chạy status/ensure và chờ trạng thái READY. |
| `GATEWAY_PROCESS_NOT_RUNNING` | Tiến trình Gateway đã dừng; chạy `b300-stlink debug gateway-ensure --json`, rồi thử lại. |

Các lệnh khôi phục an toàn:

```text
b300-stlink debug gateway-agent-status --json
b300-stlink debug gateway-agent-ensure --json
b300-stlink debug gateway-release --json
```

Nếu tiến trình sở hữu ST-Link bị crash và để lại durable owner marker cũ,
kiểm tra board/job hiện tại rồi chạy **trên máy đang gắn ST-Link**:

```text
b300-stlink hardware recover --confirm-hardware-recovery --json
```

Lệnh chỉ xóa marker owner cũ sau khi xác minh không còn tiến trình OpenOCD;
nếu OpenOCD còn chạy, recovery bị từ chối. Lệnh không nạp firmware, không
erase và không xác nhận rằng job flash trước đó đã thành công. Xem lại
`program-status` và trạng thái board trước mọi lần nạp mới.

## Remote Application flash qua Gateway

Giữ `job_id` và JSON/log của lần nạp. Trạng thái `PENDING`, mất SSH, timeout
hoặc Ctrl+C sau commit **không** chứng minh flash đã thất bại hay kết thúc.
Gateway có thể tiếp tục erase/program/verify sau khi Client ngắt kết nối.
Không gửi lại lệnh flash cho tới khi biết trạng thái job cũ và kiểm tra board.
Trên Client, hỏi lại cùng job và saved profile bằng:

```text
b300-stlink program-status <job-id> --gateway default --json
```

Trong GUI, chọn lại Gateway profile rồi bấm **Kiểm tra job gần nhất** ở PROGRAM.
Nếu status không đọc được, giữ job ID và kiểm tra SSH/Gateway/board trước khi
thao tác mới.

| Mã/trạng thái | Hành động |
|---|---|
| `REMOTE_FLASH_UNSUPPORTED` | Cập nhật Client và Gateway lên cùng release có capability `remote_application_flash_v1`; không ép dùng lệnh flash local qua SSH. |
| `HOST_KEY_UNTRUSTED` | Trên Gateway lấy fingerprint bằng `gateway host-key --json`, đối chiếu và pin bằng `gateway client-setup --confirm-host-fingerprint` hoặc `gateway trust-host` trên Client; sau đó kết nối lại. |
| `GATEWAY_BUSY` | Probe đang thuộc Flash, Debug hoặc Monitor khác. Xác định owner/job hiện tại và chờ kết thúc; không dừng job đang erase. |
| `PROBE_SELECTION_REQUIRED` | Chọn đúng serial ST-Link vật lý ở Gateway bằng `--probe-serial`, chạy dry-run mới. |
| `UPLOAD_HASH_MISMATCH`, `ARTIFACT_CHANGED` | Dừng. Kiểm file/SHA-256 trên Client; chọn lại HEX và tạo approval mới. |
| `APPROVAL_EXPIRED`, `APPROVAL_MISMATCH` | Approval không còn khớp file, target, probe hoặc lease. Chạy prepare/dry-run mới; không dùng lại approval cũ. |
| `TARGET_UNVERIFIED`, `BOOTLOADER_WRP_INVALID`, `RDP_POLICY_VIOLATION`, `FLASH_PLAN_INVALID` | Không nạp. Kiểm board, nguồn/SWD, WRP S0–S2 và HEX Application; Factory chỉ khi được ủy quyền. |
| `FLASH_FAILED`, `VERIFY_FAILED`, `METADATA_VERIFY_FAILED`, `POST_VERIFY_FAILED` | Dừng, lưu `failure_phase`, `reason`, `next_action` và log Gateway; kiểm nguồn/cáp/probe/metadata. Không retry tự động hoặc mass erase. |
| `RECOVERY_REQUIRED`, `JOB_RECOVERY_REQUIRED` | Agent restart hoặc bằng chứng job/owner không chắc chắn. Điều tra job và trạng thái MCU trước thao tác mới; không coi là thành công. |

Remote flash thành công cần exact `** Verified OK **`, AppMeta 44 byte hợp lệ
được Bootloader chuyển thành `STLM + CONFIRMED`, reset thành công, PC trong
`0x08010000..0x0807FFFF` và `BKP1R == 0`. Một dòng upload thành công hoặc
trạng thái SSH ổn định không thay thế bằng chứng này.
