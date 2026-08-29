# Bước 4 — Debug Application F407 qua OpenOCD

`b300-stlink debug` mở OpenOCD GDB server qua ST-Link/SWD. Lệnh này không xóa,
không nạp flash. Khi debugger kết nối, CPU có
thể bị halt/reset nên chỉ dùng khi board và cơ cấu đang ở trạng thái an toàn.

## Cần chuẩn bị

- ST-Link đã kết nối với board và không bị Keil, CubeProgrammer hoặc OpenOCD
  khác chiếm dụng.
- File `.axf` hoặc `.elf` có symbol, được build từ đúng source đang chạy trên
  board. File HEX chỉ dùng để nạp, không thay thế file symbol khi debug.
- Một GDB client tương thích ARM:
  - GUI release bundle mang theo `arm-none-eabi-gdb` trong `vendor/gdb/bin`.
  - Khi chạy từ source/CLI, tool tìm `B300_GDB`, GDB trong bundle, sau đó
    `arm-none-eabi-gdb` trên `PATH`; Ubuntu cuối cùng có thể dùng `gdb-multiarch`.

Nếu GDB không có, chỉ Debug tích hợp bị chặn; Application Flash vẫn hoạt động.
Đặt `B300_GDB` tới executable GDB hợp lệ hoặc cài `arm-none-eabi-gdb`.

## Cách A — Debug trên cùng một máy

### Bước 1: Kiểm tra và xem trước lệnh

```text
b300-stlink doctor
b300-stlink debug --dry-run --json
```

Dry-run phải chỉ có cấu hình OpenOCD, `bindto`, GDB port, các listener phụ được
yêu cầu rõ ràng và `init`; không được có `erase_sector`, `program`, `mww` hoặc
`mass_erase`. Mặc định Telnet/TCL đều `disabled`.

### Bước 2: Mở GDB server

```text
b300-stlink debug --gdb-port 3333
```

Giữ terminal này mở. Mặc định server chỉ nghe tại `127.0.0.1`.

Nếu có nhiều ST-Link:

```text
b300-stlink debug --probe-serial <ST-LINK-SN> --gdb-port 3333
```

### Bước 3: Kết nối GDB và nạp symbol

Mở terminal thứ hai:

```text
arm-none-eabi-gdb <duong-dan-application.axf>
```

Trong GDB:

```text
target extended-remote 127.0.0.1:3333
monitor reset halt
```

Sau đó có thể đặt breakpoint, đọc biến/register và dùng `continue`, `step`,
`next`. Không dùng `load`, `restore` hoặc lệnh flash trong phiên debug này vì
chúng có thể ghi flash ngoài quy trình provisioning an toàn.

### Bước 4: Kết thúc an toàn

Trong GDB:

```text
monitor reset run
detach
quit
```

Sau đó nhấn `Ctrl+C` tại terminal đang chạy `b300-stlink debug` và xác nhận port
3333 đã đóng.

Telnet/TCL không cần cho luồng GDB và bị tắt mặc định. Khi cần OpenOCD TCL để
automation/read-only control trên cùng máy, dùng:

```text
b300-stlink debug --gdb-port 3333 --tcl-port 6666
```

CLI chỉ báo `READY` sau khi OpenOCD đã mở đủ listener được yêu cầu. Có thể dùng
`--telnet-port 4444` khi thực sự cần Telnet local. TCL/Telnet đều bị từ chối nếu
`--bind-address` không phải loopback, và các port đang bật không được trùng nhau.

## Integrated CLI debug — GDB/MI + Safe TCL

Từ v0.7.0, CLI có các lệnh debug one-shot tích hợp. **GDB/MI** (GDB Machine
Interface — giao diện máy của GNU Debugger) chạy qua `127.0.0.1:3333`; **TCL**
(Tool Command Language — giao diện điều khiển OpenOCD) read-only chạy qua
`127.0.0.1:6666`. Integrated mode luôn loopback-only và không bật Telnet.

```text
b300-stlink debug poll --json
b300-stlink debug read-words --address 0x08010000 --count 2 --json
b300-stlink debug registers --json
b300-stlink debug where --symbols firmware.axf --json
b300-stlink debug stack --frames 8 --symbols firmware.axf --json
b300-stlink debug variable --expression bRUN --symbols firmware.axf --json
```

`where`, `stack` và `variable` nên dùng AXF/ELF được build từ **đúng binary đang
chạy**. Nếu symbol file khác build, địa chỉ có thể resolve thành hàm/dòng sai dù
GDB vẫn nạp file thành công. Khi cần nghiệm thu, so byte code Flash với AXF/ELF
trước khi tin kết quả source-level.

GDB attach có thể halt Cortex-M4. Integrated session đọc trạng thái target bằng
OpenOCD `targets` **trước khi GDB attach**. Nếu target ban đầu là `running`, các
lệnh snapshot có thể halt tạm để đọc rồi tự `continue`; khi session kết thúc tool
cố khôi phục target về `running`. Nếu target ban đầu đã `halted`, tool giữ nguyên
trạng thái đó.

### Hardware breakpoint one-shot

```text
b300-stlink debug break \
  --location vApplicationIdleHook \
  --symbols firmware.axf \
  --timeout 2 \
  --json
```

`debug break` chỉ dùng **hardware breakpoint** (điểm dừng phần cứng) qua
`-break-insert -h`; không dùng software breakpoint có thể sửa instruction trong
Flash. Transaction phải nhận đúng `*stopped` với `reason="breakpoint-hit"` và
đúng breakpoint number vừa tạo. Sau hit hoặc lỗi/timeout, resource được cleanup
trong `finally`; target ban đầu đang chạy sẽ được resume.

### Watchpoint one-shot

```text
b300-stlink debug watch \
  --expression xTickCount \
  --symbols firmware.axf \
  --timeout 2 \
  --json
```

**Watchpoint** (điểm theo dõi thay đổi dữ liệu) chỉ nhận expression allow-list
như tên biến, member hoặc index đơn giản; không cho function call hay raw GDB
command. Khi hit, tool xác minh đúng watchpoint number, chụp frame và giá trị biến
ngay lúc CPU đang dừng, sau đó xóa watchpoint rồi resume. STM32F407 chỉ có số
lượng hardware breakpoint/watchpoint hữu hạn; OpenOCD trên board nghiệm thu báo
6 breakpoint và 4 watchpoint.

### Safe TCL

CLI không expose raw TCL. Core chỉ có các primitive read-only/diagnostic đã
validate như `version`, `targets`, bounded aligned `mdw` và đọc register. Các lệnh
`flash erase_sector`, `program`, `mww`, Option Bytes hoặc WRP không nằm trong
Safe TCL surface. CPU state được lấy từ cột `State` của `targets`; OpenOCD `poll`
chỉ phản ánh background polling/TAP và không được dùng để kết luận CPU đang
`running` hay `halted`.

## Cách B — ST-Link cắm vào Ubuntu IPC, GDB chạy trên máy khác

### Bước 1: Mở server trên IPC

Chỉ thực hiện trong mạng nội bộ tin cậy:

```bash
b300-stlink debug --bind-address 0.0.0.0 --gdb-port 3333
```

Nếu có nhiều probe, thêm `--probe-serial <ST-LINK-SN>`. Không mở các port này
ra Internet; dùng firewall hoặc SSH tunnel khi mạng không được tin cậy. Telnet
và TCL vẫn bị tắt trong phiên remote.

### Bước 2: Kết nối từ máy phát triển

Mở GDB với file AXF/ELF tương ứng:

```text
arm-none-eabi-gdb <duong-dan-application.axf>
```

Sau khi vào GDB, kết nối tới IP của IPC:

```text
target extended-remote <IP-IPC>:3333
monitor reset halt
```

Ví dụ với IPC `10.1.200.208`:

```text
target extended-remote 10.1.200.208:3333
```

Khi kết thúc, chạy `monitor reset run`, `detach`, `quit`, rồi `Ctrl+C` trên IPC.

## GUI Debug

Tab **Debug** trong `b300-stlink-gui` dùng cùng một `HardwareSessionManager` với
Flash, Factory và Memory. Khi một phiên Debug đang giữ ST-Link, GUI sẽ khóa các
thao tác nạp, Factory, đổi probe và đọc Memory thay vì chờ backend báo xung đột.

Luồng vận hành:

1. Giữ mặc định `127.0.0.1:3333` nếu debug tại máy local.
2. Chọn file `.elf` hoặc `.axf` khớp đúng firmware đang chạy nếu cần source/symbol.
3. Nhấn **Khởi động Debug Server**.
4. Khi OpenOCD sẵn sàng, nhấn **Kết nối GDB**.
5. Sau khi GDB/MI xác nhận kết nối, các nút **Halt**, **Continue** và
   **Reset + Halt** mới được bật.
6. Nhấn **Dừng Debug** để dừng GDB/OpenOCD và giải phóng ST-Link.

GUI không coi thao tác GDB thành công chỉ vì đã ghi lệnh vào stdin. Backend phải
nhận đúng result record có cùng MI token (`^done`, `^connected`, `^running`...)
trong timeout giới hạn; `^error` hoặc timeout được hiển thị là lỗi.

Nếu OpenOCD dừng bất ngờ, watchdog của tab Debug sẽ phát hiện trạng thái FAILED,
dừng GDB còn lại, giải phóng interlock phần cứng và cho phép người vận hành bắt
đầu một phiên mới sau khi xử lý nguyên nhân. Log OpenOCD/GDB được hiển thị ngay
trong tab Debug và đồng thời đưa vào log chung của ứng dụng.

## Phạm vi của tool

Tool hỗ trợ hai bề mặt: (1) mở OpenOCD server để IDE/GDB ngoài kết nối và (2)
integrated CLI one-shot dùng GDB/MI + Safe TCL cho source location, stack,
register, variable, hardware breakpoint và watchpoint. Keil vẫn dùng để build và
tạo AXF; chế độ debug native của Keil không phải client của OpenOCD flow này.
Integrated CLI không cung cấp flash/program, arbitrary memory write, raw TCL hay
raw GDB console.
