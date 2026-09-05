# Bước 4 — Debug Application F407 qua OpenOCD

`b300-stlink debug` mở OpenOCD GDB server qua ST-Link/SWD. Lệnh này không xóa,
không nạp flash. Khi debugger kết nối, CPU có
thể bị halt/reset nên chỉ dùng khi board và cơ cấu đang ở trạng thái an toàn.

## GUI hiện tại: MONITOR và DEBUG

Production có năm trang **PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS**.

- **MONITOR** quan sát qua các SWD/TCL read giới hạn, giữ MCU RUNNING. Chọn trang này khi cần theo dõi mà không halt/step/reset.
- **DEBUG** chuẩn bị bridge cho **VS Code + Cortex-Debug**. VS Code quản lý source, breakpoint/watchpoint, variables, registers, call stack và step/continue. B300 quản lý ST-Link, OpenOCD, managed GDB, SSH tunnel và cleanup.
- **LOCAL**: máy này giữ ST-Link, source và AXF/ELF.
- **GATEWAY**: máy này giữ ST-Link/OpenOCD và phục vụ Client qua SSH; các cổng debug chỉ bind loopback.
- **CLIENT**: máy này giữ source/AXF/ELF và kết nối Gateway bằng SSH.

## Cần chuẩn bị

ST-Link không bị công cụ khác chiếm dụng. Chọn AXF/ELF từ đúng build đang chạy;
HEX không thay thế symbol file. Native bundle v0.18 có managed ARM GDB và OpenOCD.
Không cần cài toàn bộ GNU Arm toolchain chỉ để debug. Có thể xem đường dẫn đã
resolve trong diagnostics; Gateway không cần source hoặc AXF/ELF.

GUI Client dùng phiên SSH nhúng đã xác thực. Tùy chọn nhớ thông tin đăng nhập dùng
credential mã hóa lưu cục bộ; khóa giải mã cũng nằm trong hồ sơ người dùng,
không bảo vệ trước người đã kiểm soát tài khoản Windows/Linux. Password không ghi
vào log hay command line. CLI dùng luồng xác thực của lệnh được chọn.

## Cách A — Debug Local trên cùng máy

Mở **DEBUG**, chọn **LOCAL**, chọn workspace và AXF/ELF tương ứng rồi dùng hành động
mở Debug trong VS Code. B300 chuẩn bị cấu hình Cortex-Debug dạng external attach;
VS Code thực hiện phiên source debugging. Đóng phiên VS Code rồi dừng bridge B300
để giải phóng ST-Link. Trước khi thao tác, bảo đảm cơ cấu an toàn vì debugger có
thể halt CPU. Không dùng GDB `load`, `restore` hoặc lệnh flash trong phiên debug.

Các giao diện **Debug Workstation / Interactive Debug** trong tài liệu nghiệm thu
v0.15–v0.17 là lịch sử; chúng không thuộc production GUI hiện tại. Các lệnh one-shot
phía dưới dành cho diagnostics nâng cao.

### Manual/legacy external GDB server

Nếu cần tự điều khiển một GDB ngoài thay vì GUI, dùng alias legacy tường minh:

```text
b300-stlink debug server --gdb-port 3333
```

`debug` không có mode **không còn có nghĩa là legacy server**; từ v0.8 nó mặc định
thành `debug gateway`. Legacy server vẫn bind loopback mặc định. Chỉ mở Telnet/TCL
legacy khi thật sự cần và không mở chúng ra non-loopback.

```text
b300-stlink debug server --dry-run --json
b300-stlink debug server --gdb-port 3333 --tcl-port 6666
```

Không dùng `load`, `restore`, raw flash command hoặc arbitrary memory write từ GDB.
OpenOCD debug profile server-side vẫn có `gdb flash_program disable` và ép hardware
breakpoint.

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

CLI không expose raw TCL. Core chỉ có các primitive allow-listed đã validate như
`version`, `targets`, bounded aligned `mdw`, đọc register và `resume` có kiểm soát.
`resume` chỉ được dùng để khôi phục trạng thái RUNNING đã được ghi nhận trước phiên
remote debug; không có raw write-memory. Các lệnh `flash erase_sector`, `program`,
`mww`, Option Bytes hoặc WRP không nằm trong Safe TCL surface. CPU state được lấy
từ cột `State` của `targets`; OpenOCD `poll`
chỉ phản ánh background polling/TAP và không được dùng để kết luận CPU đang
`running` hay `halted`.

## Realtime Live Monitor — non-halting

Trong GUI đây là đường mặc định và nổi bật nhất: **Biến theo dõi** là tab đầu tiên,
**Luồng thực thi** là tab thứ hai; thông số overrun/read-time/lag nằm trong
**Chất lượng lấy mẫu** và mặc định đóng. Các cột kỹ thuật như Address/Min/Max/Mean
cũng chỉ hiện khi người dùng mở chi tiết, nên màn hình vận hành thường ngày chỉ tập
trung vào biến, giá trị, kiểu, thời gian và đồ thị.

Để quan sát robot đang chạy mà không dùng chu kỳ HALT/RUN, dùng `debug live`. Backend
đọc DWT PC sample register và các symbol RAM qua Safe TCL `mdw` khi target vẫn RUNNING;
source mapping được làm offline bằng AXF/ELF. Local profile tắt cả GDB/Telnet, còn Client
Live chỉ forward TCL qua SSH. Cadence hỗ trợ từ 0.1 s.

```text
b300-stlink debug live --symbols firmware.axf --live-interval 0.1 \
  --live-watch xTickCount:u32

b300-stlink debug client --ssh-host <gateway> --ssh-user <user> \
  --client-action live --symbols firmware.axf --live-interval 0.5 \
  --live-watch xTickCount:u32
```

Đây là **statistical execution sampling**, không phải trace đầy đủ từng instruction. CPU không
bị debugger halt, nhưng SWD vẫn tạo một lượng bus traffic nhỏ nên không tuyên bố zero timing
impact. Contract, giới hạn và hardware evidence nằm tại `docs/15_REALTIME_LIVE_MONITOR.md`.

## Self-test một máy cho đường Remote Debug lõi

Từ v0.8.1, khi chưa có hai máy để thử SSH thật, có thể nghiệm thu phần mềm của đường Gateway → external Client ngay trên **một máy**. Lệnh này mở Gateway OpenOCD chỉ trên loopback rồi dùng chính `DebugSession.start_external()` để attach lại qua `127.0.0.1:3333/6666`; vì vậy nó kiểm tra đúng data path GDB/Safe TCL nằm **sau lớp SSH forwarding** mà không làm yếu policy mạng.

```text
b300-stlink debug selftest \
  --symbols firmware.axf \
  --expression xTickCount \
  --location vApplicationIdleHook \
  --timeout 5 \
  --json
```

Self-test kiểm tra: Gateway listener, trạng thái RUN/HALT ban đầu, **ELF/AXF khớp Application Flash trước attach**, external Client attach, source/stack/register inspect, đọc biến, Break Once/Watch Once nếu được yêu cầu, restore trạng thái cuối và release cổng `3333/6666`. Symbol mismatch fail-closed trước external GDB attach. Nếu target ban đầu đã `HALTED`, các thao tác one-shot cần target chạy được đánh dấu `LIMITED` thay vì tự ý Resume.

Self-test **không** giả vờ đã kiểm tra SSH hoặc hai máy. JSON luôn ghi `ssh_exercised=false`, `two_machine_exercised=false` và `field_acceptance_pending=true`. Hai-device E2E vẫn là cổng nghiệm thu thực địa riêng.

## Cách B — B300 Tools Gateway + Client qua SSH

Đây là workflow remote mặc định. Không mở GDB/TCL trực tiếp ra LAN/Internet.

```text
STM32 ─SWD─ ST-Link ─ B300 Gateway
                         ├─ OpenOCD GDB 127.0.0.1:3333
                         ├─ Safe TCL    127.0.0.1:6666
                         ├─ RemoteDebugGuard
                         └─ SSH server
                                │
                                │ encrypted local forwarding
                                ▼
                         B300 GUI Client
                         ├─ VS Code + Cortex-Debug
                         ├─ AXF/ELF local
                         └─ source code local
```

### Gateway CLI

Máy cắm ST-Link có thể chạy headless, không cần GUI/GDB/AXF. Trước khi mở service, chạy preflight:

```text
b300-stlink gateway doctor
```

Preflight chỉ kiểm tra host dependency: OpenOCD, lựa chọn ST-Link, SSH server, ports `3333/6666` còn trống và IPv4 candidate. Nó không yêu cầu GDB/source/AXF và không erase/program Flash. Khi báo `READY`:

```text
b300-stlink debug          # mặc định Gateway
# tương đương: b300-stlink debug gateway
```

Nếu chỉ có một ST-Link, tool tự chọn. Nếu có nhiều probe, ghim đúng serial bằng
`--probe-serial`. Gateway profile cố định các nguyên tắc an toàn:

- bind `127.0.0.1`;
- GDB `3333`, Safe TCL `6666`, Telnet disabled;
- `gdb flash_program disable`;
- `gdb breakpoint_override hard`;
- không `erase_sector`, `program {}`, `mass_erase`, `mww`, Option Bytes hay WRP;
- RemoteDebugGuard ghi trạng thái RUN/HALT ban đầu và tự khôi phục RUNNING khi cần.

CLI vẫn giữ đầy đủ các chức năng khác như nạp Application, factory-provision
Bootloader, doctor, memory/metadata read-only. Chỉ riêng vai trò **Debug Gateway**
không cần source-level debugger trên máy cổng. `debug server` được giữ làm alias
legacy; workflow mới dùng `debug gateway`.

### CLI Client headless

Máy kỹ sư không có ST-Link có thể chạy one-shot diagnostics qua Gateway mà không cần GUI:

```text
b300-stlink debug client --ssh-host <gateway> --ssh-user <user> \
  --symbols <application.axf> --client-action inspect --json
```

`--client-action` hỗ trợ `inspect`, `where`, `registers`, `stack`, `variable`, `poll`,
`read-words`, `break`, `watch`, `sample` và `live`. Các action Interactive Debug forward
GDB/TCL loopback bằng SSH. Riêng `live` dùng tunnel TCL-only và không forward GDB 3333.
Không expose raw OpenOCD ra LAN và không có flash/erase/WRP surface.

### B300 GUI: LOCAL / GATEWAY / CLIENT

Trang DEBUG hiện tại có ba vai trò tường minh. Chọn GATEWAY trên máy gắn ST-Link
và CLIENT trên máy chứa source/AXF. Client đăng nhập bằng hộp thoại SSH của B300,
sau đó mở Debug trong VS Code. B300 quản lý tunnel và kiểm tra target/symbol theo
core policy; VS Code thực hiện source debugging.

MONITOR dùng phiên SSH đã xác thực với TCL-only forwarding. DEBUG và MONITOR có
lifecycle riêng và cùng tuân thủ HardwareSession. Dừng phiên đang dùng probe trước
khi chuyển vai trò hoặc nạp firmware.

Các mục Auto, card Interactive Debug và debugger nội bộ của v0.15–v0.17 chỉ còn
trong compatibility/tests; không dùng chúng làm hướng dẫn GUI production.

## VS Code — client source debugging của production

VS Code không phải con đường bắt buộc. Cùng Gateway ở trên có thể phục vụ
Cortex-Debug/GDB qua SSH tunnel chỉ forward GDB. TCL không cần forward cho VS Code.

Sinh kit:

```text
b300-stlink debug vscode \
  --ssh-host <GATEWAY-IP-OR-HOSTNAME> \
  --ssh-user <SSH-USER> \
  --program-relative Objects/F407/Main_V2_F407.axf \
  --output-dir <VS-CODE-WORKSPACE> \
  --json
```

Kit gồm `.vscode/launch.json`, `.vscode/extensions.json`, lệnh Gateway, lệnh SSH
tunnel và checklist. Gateway command mới tương đương:

```text
b300-stlink debug gateway
```

Máy VS Code giữ source/AXF và GDB. Không dùng `load`/flash từ debugger. Không
NAT/port-forward trực tiếp `3333` hoặc `6666`; nếu khác mạng, dùng SSH/VPN được
quản trị và vẫn giữ OpenOCD ở loopback.

## GUI Debug safety/lifecycle

Tab Debug dùng cùng `HardwareSessionManager` với Flash, Factory và Memory. Khi một
phiên local/gateway giữ ST-Link, GUI khóa các thao tác phần cứng xung đột. Client
bridge cũng khóa Monitor/update cho đến khi dừng. Gateway guard ghi nhận trạng thái
RUN/HALT ban đầu và khôi phục RUNNING khi cần sau disconnect. VS Code quản lý GDB;
OpenOCD server-side disable GDB flash programming và ép hardware breakpoint.
Client kiểm tra listener GDB trên Gateway trước khi báo READY.

Integrated CLI one-shot vẫn dùng GDB/MI với result token, timeout và cleanup riêng.

## Phạm vi của tool

Tool hỗ trợ hai bề mặt: (1) mở OpenOCD server để IDE/GDB ngoài kết nối và (2)
integrated CLI one-shot dùng GDB/MI + Safe TCL cho source location, stack,
register, variable, hardware breakpoint và watchpoint. Keil vẫn dùng để build và
tạo AXF; chế độ debug native của Keil không phải client của OpenOCD flow này.
Integrated CLI không cung cấp flash/program, arbitrary memory write, raw TCL hay
raw GDB console.
## Gateway Setup Wizard (v0.12.0+)

Máy Gateway chưa có SSH có thể dùng chức năng chuẩn bị Gateway trong **DEBUG → GATEWAY** hoặc CLI `gateway plan` / `gateway prepare --confirm-system-change`. Wizard chỉ quản lý OpenSSH + TCP/22 và giữ OpenOCD GDB/TCL loopback-only. Chi tiết: [Gateway Setup Wizard v0.12.0](19_GATEWAY_SETUP_WIZARD_V0.12.0.md).
### CLI workflow tự động (RC2+)

Đối với hai máy mới, ưu tiên workflow sau thay vì gọi từng primitive SSH thủ công:

```text
# Gateway
b300-stlink gateway quickstart --confirm-system-change

# Client: lưu endpoint (password sẽ được OpenSSH hỏi khi kết nối)
b300-stlink gateway client-setup --ssh-host <gateway> --ssh-user <user>

# Client
b300-stlink gateway connect-check
b300-stlink gateway status
```

Sau khi saved profile đã sẵn sàng, `debug client` và `debug vscode` có thể bỏ `--ssh-host/--ssh-user`. `gateway status` chỉ phản ánh local setup; `gateway connect-check` mở OpenSSH tương tác và mới xác minh SSH thật. GDB/TCL vẫn chỉ loopback ở Gateway và chỉ đi qua SSH forwarding; không NAT/expose `3333`/`6666`. Chi tiết đầy đủ: [Gateway Setup & Remote Workflow](19_GATEWAY_SETUP_WIZARD_V0.12.0.md).
