# Realtime Live Monitor — non-halting SWD execution sampling

## Mục tiêu

`debug live` dùng để quan sát Application STM32F407 khi robot **tiếp tục RUNNING**. Đây là đường chẩn đoán riêng với Interactive Debug: không attach GDB, không `halt`, không `resume`, không reset, không breakpoint/watchpoint và không ghi Flash/Option Bytes.

Thuật ngữ **non-halting** /ˌnɒn ˈhɔːltɪŋ/ nghĩa là CPU không bị debugger dừng lại. Không gọi cơ chế này là **zero-impact**: mọi truy cập SWD vẫn tạo một lượng bus/debug traffic nhỏ, vì vậy đây là phương án *low-intrusion monitoring* chứ không phải phép đo timing hard real-time tuyệt đối.

## Cơ chế

Mỗi chu kỳ Live Monitor thực hiện một bounded SWD read transaction qua OpenOCD Safe TCL:

1. đọc `DWT_PCSR @ 0xE000101C` để lấy Program Counter mẫu;
2. đọc tối đa 16 symbol RAM đã chọn; các word cần thiết được gộp vào cùng một TCL round-trip;
3. map PC sang `function / file / line` **offline trên laptop** bằng AXF/ELF + GNU Arm `nm`/`addr2line`;
4. không thay đổi trạng thái RUN/HALT của target;
5. định kỳ xác nhận target vẫn `RUNNING`; nếu target dừng bất ngờ thì fail-closed.

Scheduler lấy mẫu được neo theo `t0 + n × interval`, tránh drift tích lũy kiểu `sleep(interval)` sau mỗi lần đọc. `overrun=true` được ghi nếu một SWD cycle dài hơn interval yêu cầu.

## Cadence và giới hạn

- interval: `0.1 .. 60.0 s`;
- tối đa 16 watch symbols;
- bounded run: tối đa 100000 samples;
- bỏ `--live-samples` để chạy đến khi người vận hành Stop/Ctrl+C;
- watch syntax: `NAME:TYPE`;
- type hỗ trợ: `u8`, `i8`, `u16`, `i16`, `u32`, `i32`, `f32`, `f64`;
- symbol phải nằm trọn trong CCM `0x10000000..0x1000FFFF` hoặc SRAM `0x20000000..0x2001FFFF`;
- symbol trùng tên ở nhiều địa chỉ bị từ chối thay vì tự chọn nhầm.
- watch 64-bit (`f64`) được đọc hai lần trong cùng TCL transaction; chỉ khi hai raw value khớp mới trả numeric value. Nếu không khớp, sample ghi `coherent=false` để tránh plot một torn value.

Không có raw-address watch ở UX mặc định. Địa chỉ được resolve từ AXF/ELF matching firmware.

## GUI Symbol Browser

Trong GUI, phần **Live Variables (RAM Watch)** có nút **Browse AXF Symbols…**. Browser đọc trực tiếp AXF/ELF đã chọn bằng `arm-none-eabi-nm` trên laptop; thao tác này không kết nối ST-Link/OpenOCD và không thay đổi trạng thái target.

Mặc định browser chỉ hiện symbol đủ điều kiện **Watchable RAM**. Một symbol chỉ được bật `Use Symbol` khi:

- được phân loại là data symbol;
- tên resolve duy nhất, không mơ hồ giữa nhiều địa chỉ;
- `size > 0`;
- toàn bộ byte span nằm trong CCM/SRAM STM32F407.

Có thể bỏ chọn **Watchable RAM only** để xem function/Flash data/other symbol phục vụ chẩn đoán, nhưng các dòng không an toàn vẫn không thể chọn làm Live Watch và GUI hiển thị lý do block.

Browser **không suy đoán C type** từ `nm`. Sau khi chọn symbol, GUI chỉ điền tên biến; người dùng vẫn phải chọn rõ `u8/i8/u16/i16/u32/i32/f32/f64`. Điều này tránh suy diễn sai trường hợp cùng kích thước 4 byte nhưng có thể là integer hoặc `float`.

## Local

```text
b300-stlink debug live \
  --symbols Main_V2_F407.axf \
  --live-interval 0.1 \
  --live-watch xTickCount:u32 \
  --live-watch bRUN:u8 \
  --live-watch v_current:f64 \
  --live-output trace.csv
```

Local Live Monitor mở OpenOCD profile riêng:

```text
bindto 127.0.0.1
gdb port disabled
telnet port disabled
tcl port 6666
init
```

Không có GDB listener trong local Live Monitor. Hardware-session lease vẫn được giữ để Flash/Factory/Interactive Debug không thể tranh ST-Link trong lúc monitor.

## Remote Client

```text
b300-stlink debug client \
  --ssh-host <gateway> \
  --ssh-user <user> \
  --client-action live \
  --symbols Main_V2_F407.axf \
  --live-interval 0.5 \
  --live-watch xTickCount:u32
```

Live Client dùng SSH local forwarding **TCL-only**:

```text
127.0.0.1:<client-tcl> -> SSH -> 127.0.0.1:6666 trên Gateway
```

Không forward GDB `3333` cho action `live`. `BatchMode=yes`, `StrictHostKeyChecking=yes` và `ExitOnForwardFailure=yes` vẫn bắt buộc.

## Ý nghĩa Execution Timeline

`DWT_PCSR` cho biết PC tại thời điểm lấy mẫu. Vì vậy timeline là **statistical execution sampling** /stəˈtɪstɪkəl ˌeksɪˈkjuːʃən ˈsɑːmplɪŋ/ — lấy mẫu thống kê luồng thực thi. Hàm rất ngắn có thể chạy hoàn toàn giữa hai lần lấy mẫu và không xuất hiện. Không được diễn giải timeline thành danh sách tuần tự đầy đủ của mọi instruction/hàm.

Muốn trace sự kiện chính xác hơn trong tương lai cần cân nhắc SWO/ITM hoặc firmware trace ring-buffer; các phương án đó cần kiểm tra pin/hardware và overhead riêng.

## Hardware evidence hiện tại

Main B300 F407 thật với `Main_V2_F407.axf` đã chạy:

- 30 samples ở interval `0.1 s`;
- watch đồng thời `xTickCount:u32`, `bRUN:u8`, `v_current:f64`;
- `0` overrun;
- target cuối `RUNNING`;
- `xTickCount` tăng `2350361 -> 2353278` trong khoảng `2.922 s`, xấp xỉ `998.3 tick/s`, phù hợp FreeRTOS tick 1 kHz ở độ phân giải của phép thử;
- timeline resolve được các điểm như `vApplicationIdleHook`, `prvIdleTask`, `xPortGetFreeHeapSize`, `bsp_can_transmit`, `GPIO_Init`;
- CSV 30 dòng được xuất đúng schema.

Evidence này chứng minh **không có debugger HALT** và scheduler tick tiếp tục tiến trong bài thử. Nó không phải chứng nhận worst-case jitter ở mức microsecond; nếu cần chứng minh deadline hard real-time, phải đo bằng timer/GPIO/logic analyzer hoặc trace phần cứng phù hợp.

## Safety invariants

Live Monitor không được thêm các primitive sau vào đường realtime:

- GDB attach;
- `halt`, `resume`, `reset halt`;
- breakpoint/watchpoint/step;
- `mww`, `mwh`, `mwb`;
- raw TCL console;
- Flash erase/program;
- Option Bytes/WRP/RDP change.

Interactive Debug vẫn được giữ riêng cho các thao tác cần halt CPU và GUI phải cảnh báo rõ rằng chế độ đó có thể ảnh hưởng realtime control.

## Backend facade cho GUI/Client

Frontend không nên tự ghép OpenOCD/TCL/SSH/symbol matcher. Dùng `b300_core.live_session.LiveMonitorSession`:

```python
from b300_core.live_session import LiveMonitorSession, LocalLiveMonitorConfig

session = LiveMonitorSession()
info = session.start_local(config)
try:
    summary = session.run(on_sample)
finally:
    session.close()
```

Nút **Stop Live** chỉ gọi `session.cancel()`. Cancellation dùng `threading.Event`; nếu interval đang là 60 s thì worker vẫn thức dậy ngay thay vì đợi hết interval. `cancel()` không gọi halt/resume/reset và không kill OpenOCD trong normal stop path.

`ClientLiveMonitorConfig` hỗ trợ cả AXF/ELF cụ thể và bounded `symbol_roots`; backend chỉ nhận unique exact Flash match. Hardware interlock dùng mode riêng `MONITORING`, tách khỏi `DEBUGGING`, nhưng vẫn khóa độc quyền ST-Link để Flash/Factory/Interactive Debug không thể chạy đồng thời.

Hardware acceptance của facade: 20 samples @ 10 Hz trên B300 F407 thật, 0 overrun, final target `RUNNING`, `f64` coherent 20/20; `xTickCount` tăng 3622787 -> 3624688 trong ~1.937 s.

## Backend analytics/ring buffer

`LiveMonitorSession` tích hợp `LiveMonitorStore` thread-safe trên laptop. Store giữ lịch sử bounded nhưng thống kê toàn phiên vẫn giữ đầy đủ: tổng samples, overrun, read duration, schedule lag, unknown source, incoherent value, function hit/share, variable min/max/mean và latest value.

API phục vụ frontend:

- `history(limit)` — raw bounded samples;
- `execution_transitions(limit)` — nén các sample liên tiếp cùng function/file/line;
- `variable_series(name, limit)` — series số cho plot, incoherent sample trả `value=None`;
- `analytics_snapshot(top_functions)` — timing/function/variable statistics.

Function statistics được gom theo function + file, không tách thành nhiều dòng chỉ vì DWT PC rơi vào các line khác nhau của cùng hàm. Timeline vẫn giữ line chi tiết. Toàn bộ analytics chạy offline trên laptop và không tạo thêm SWD traffic.
