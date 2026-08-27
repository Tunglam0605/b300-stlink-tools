# Bước 4 — Debug Application F407 qua OpenOCD

`b300-stlink debug` mở OpenOCD GDB server qua ST-Link/SWD. Lệnh này không xóa,
không nạp flash và không ghi provisioning marker. Khi debugger kết nối, CPU có
thể bị halt/reset nên chỉ dùng khi board và cơ cấu đang ở trạng thái an toàn.

## Cần chuẩn bị

- ST-Link đã kết nối với board và không bị Keil, CubeProgrammer hoặc OpenOCD
  khác chiếm dụng.
- File `.axf` hoặc `.elf` có symbol, được build từ đúng source đang chạy trên
  board. File HEX chỉ dùng để nạp, không thay thế file symbol khi debug.
- Một GDB client tương thích ARM:
  - Windows: `arm-none-eabi-gdb` từ GNU Arm Embedded Toolchain.
  - Ubuntu: `arm-none-eabi-gdb` hoặc `gdb-multiarch`.

Bundle của repo mang theo OpenOCD server nhưng không mang cả GNU toolchain để
giữ gói nhẹ. Kiểm tra GDB client bằng `arm-none-eabi-gdb --version` hoặc
`gdb-multiarch --version`.

## Cách A — Debug trên cùng một máy

### Bước 1: Kiểm tra và xem trước lệnh

```text
b300-stlink doctor
b300-stlink debug --dry-run --json
```

Dry-run phải chỉ có cấu hình OpenOCD, `bindto`, GDB port, Telnet/TCL ở trạng thái
`disabled` và `init`; không được có `erase_sector`, `program`, `mww` hoặc
`mass_erase`.

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

Telnet/TCL không cần cho luồng GDB và bị tắt mặc định. Khi thực sự cần OpenOCD
Telnet trên cùng máy, có thể thêm `--telnet-port 4444`; tool không cho mở Telnet
khi bind ra mạng.

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

## Phạm vi của tool

Tool chịu trách nhiệm kiểm tra tham số và mở OpenOCD server đúng probe/port.
Việc hiển thị source, breakpoint và watch do GDB client hoặc IDE tương thích GDB
thực hiện. Keil vẫn dùng để build và tạo file AXF; chế độ debug native của Keil
không phải là client của luồng OpenOCD trong tài liệu này.
