# Bước 4 — Debug Application F407 qua OpenOCD

`b300-stlink debug` mở OpenOCD GDB server qua ST-Link/SWD. Lệnh này không xóa,
không nạp flash. Khi debugger kết nối, CPU có
thể bị halt/reset nên chỉ dùng khi board và cơ cấu đang ở trạng thái an toàn.

## Cần chuẩn bị

- ST-Link đã kết nối với board và không bị Keil, CubeProgrammer hoặc OpenOCD
  khác chiếm dụng.
- File `.axf` hoặc `.elf` có symbol, được build từ đúng source đang chạy trên
  board. File HEX chỉ dùng để nạp, không thay thế file symbol khi debug.
- Máy thực sự phân tích source (**Local** hoặc **Client**) cần GDB ARM. Base GUI/CLI
  không nhúng toàn bộ GNU Arm toolchain để giữ package nhẹ. Tool ưu tiên `B300_GDB`,
  tự tìm GDB từ STM32CubeIDE/toolchain đã cài, sau đó tìm `arm-none-eabi-gdb` trên
  `PATH`; Ubuntu có thể dùng `gdb-multiarch` khi phù hợp.
- **Gateway** không cần GDB, source hay AXF/ELF. Gateway chỉ cần ST-Link, OpenOCD
  và SSH server; `b300-stlink debug gateway` giữ GDB/TCL ở loopback.
- **Client** giữ source + AXF/ELF. GUI Client và `b300-stlink debug client` đều tạo
  managed SSH local forwarding, xác minh AXF/ELF với Application Flash rồi mới attach GDB.

Nếu Gateway không có GDB local thì Flash, Factory provisioning và Debug Gateway vẫn
hoạt động bình thường.

## Cách A — Debug Local trên cùng máy

Luồng khuyến nghị là GUI: mở tab **Debug**, để **Auto** hoặc chọn **Local**, chọn
AXF/ELF đúng firmware rồi bấm **BẮT ĐẦU LOCAL**. Khi chỉ có một ST-Link, GUI tự
chọn probe. GDB được tự tìm từ `B300_GDB`, STM32CubeIDE hoặc `PATH`.

Sau khi attach, GUI xác nhận target `RUNNING/HALTED`. Nếu GDB attach làm một target
đang RUNNING bị HALT, `DebugSession` tự Resume ngay. Các thao tác Where, Call Stack,
Registers và Variable chỉ halt tạm khi cần rồi khôi phục trạng thái trước thao tác.
Hardware breakpoint/watchpoint là one-shot và được cleanup sau hit/timeout.

Nhấn **Dừng Debug** để restore trạng thái ban đầu, đóng GDB/OpenOCD và giải phóng
ST-Link. Không có lệnh flash trong flow này.

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
                         ├─ GDB/MI local
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

### B300 GUI: Auto / Local / Gateway / Client

Tab **Debug** có bốn lựa chọn:

- **Auto**: có ST-Link local → Local; không có ST-Link → Client.
- **Local**: máy này cắm ST-Link và dùng GUI để debug trực tiếp.
- **Gateway**: máy này cắm ST-Link và phục vụ client ngoài; GUI chỉ giám sát
  Gateway/target, không mở một GDB controller thứ hai.
- **Client**: máy này không cần ST-Link; source + AXF/ELF nằm tại đây.

Gateway GUI muốn tự debug trực tiếp thì chuyển sang **Local** sau khi remote client
đã ngắt. Tool không cho hai GDB controller cùng điều khiển một STM32.

### Interactive Debug Workspace

GUI Interactive Debug tổ chức các primitive source-level hiện có thành một workspace trạng thái. Thanh trạng thái của workspace hiển thị `Target: RUNNING/HALTED/UNKNOWN/DISCONNECTED`, thao tác gần nhất và nhắc rõ `Mode: INTRUSIVE / GDB`.

Kết quả được giữ riêng theo các tab:

- **Current Location** — kết quả `Where`;
- **Call Stack** — stack frames;
- **Registers** — register snapshot;
- **Variables** — kết quả đọc biến theo yêu cầu;
- **Diagnostic** — breakpoint/watchpoint one-shot và các kết quả tổng quát khác.

Đây là thay đổi presentation/UX. Workspace không tạo thêm GDB request ngoài thao tác người dùng đã bấm, không thay đổi cơ chế auto-resume, không thêm breakpoint/watchpoint nền và không liên quan đến Realtime Live Monitor. Realtime Live vẫn là subsystem non-halting riêng.

### Client one-click

Lần đầu Client cần Gateway host, SSH user và project/AXF. Các lần sau GUI ghi nhớ
profile. Khi bấm **KẾT NỐI GATEWAY**, tool tự động:

1. chọn hai local loopback port còn trống;
2. chạy OpenSSH với `BatchMode=yes`, `StrictHostKeyChecking=yes`,
   `ExitOnForwardFailure=yes`;
3. forward GDB và Safe TCL **bên trong SSH mà thôi**; cả hai endpoint vẫn là loopback
   ở Gateway và Client, không public ra mạng;
4. xác nhận forwarded TCL hoạt động;
5. nếu đã chọn AXF/ELF, so machine-code samples với Application Flash; mismatch thì
   fail-closed;
6. nếu đã lưu project root, scan bounded project tree và chọn **duy nhất một**
   AXF/ELF exact-match; nhiều hoặc không có match thì yêu cầu người dùng xử lý;
7. tự tìm GDB trên Client, load symbols và attach bằng GDB/MI async;
8. nếu attach làm target đang RUNNING bị HALT, tự Resume về RUNNING;
9. bật Where, Call Stack, Registers, Variable, hardware breakpoint/watchpoint.

Tool không lưu password SSH plaintext. Gateway host key phải đã có trong
`known_hosts`; nên dùng SSH key/agent. Đây là điểm cố ý không tự động hóa để không
đánh đổi xác thực Gateway.

Khi Stop Client: tool khôi phục target trước, đóng GDB sau đó mới đóng SSH tunnel.
Nếu tunnel chết bất ngờ, GUI fail-closed và báo mất kết nối thay vì giả vờ session
vẫn hoạt động.

### Chọn project một lần

Trong Client, nút **Tự tìm đúng AXF/ELF** khi chưa kết nối chỉ lưu một project root
bounded. Tool không quét cả ổ đĩa. Khi kết nối lần sau, matcher đọc một số cửa sổ
Application Flash qua Safe TCL và tự chọn đúng AXF/ELF.

## Cách C — VS Code là client tùy chọn

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
phiên local/gateway giữ ST-Link, GUI khóa các thao tác phần cứng xung đột. Backend
GDB/MI yêu cầu đúng result record cùng token; timeout hoặc `^error` là lỗi thật.

Local và Client dùng cùng `DebugSession`, nên cùng một logic preserve RUN/HALT.
OpenOCD server-side luôn disable GDB flash programming và ép hardware breakpoint.
Watchdog phát hiện OpenOCD/tunnel chết, cleanup session và giải phóng interlock.

## Phạm vi của tool

Tool hỗ trợ hai bề mặt: (1) mở OpenOCD server để IDE/GDB ngoài kết nối và (2)
integrated CLI one-shot dùng GDB/MI + Safe TCL cho source location, stack,
register, variable, hardware breakpoint và watchpoint. Keil vẫn dùng để build và
tạo AXF; chế độ debug native của Keil không phải client của OpenOCD flow này.
Integrated CLI không cung cấp flash/program, arbitrary memory write, raw TCL hay
raw GDB console.
## Gateway Setup Wizard (v0.12.0+)

Máy Gateway chưa có SSH có thể dùng tab **Gateway Setup** hoặc CLI `gateway plan` / `gateway prepare --confirm-system-change`. Wizard chỉ quản lý OpenSSH + TCP/22 và giữ OpenOCD GDB/TCL loopback-only. Chi tiết: [Gateway Setup Wizard v0.12.0](19_GATEWAY_SETUP_WIZARD_V0.12.0.md).
### CLI workflow tự động (RC2+)

Đối với hai máy mới, ưu tiên workflow sau thay vì gọi từng primitive SSH thủ công:

```text
# Gateway
b300-stlink gateway quickstart --confirm-system-change

# Client: dùng client_setup_command do Gateway in ra
b300-stlink gateway client-setup --ssh-host <gateway> --ssh-user <user> \
  --confirm-host-fingerprint <SHA256:...>

# Gateway: chạy authorize_command do Client in ra

# Client
b300-stlink gateway connect-check
b300-stlink gateway status
```

Sau khi saved profile đã sẵn sàng, `debug client` và `debug vscode` có thể bỏ `--ssh-host/--ssh-user`. `gateway status` chỉ phản ánh local setup; `gateway connect-check` mới xác minh SSH thật. Chi tiết đầy đủ: [Gateway Setup & Remote Workflow](19_GATEWAY_SETUP_WIZARD_V0.12.0.md).
