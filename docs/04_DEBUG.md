# Debug OpenOCD

Chỉ dùng khi board được phép halt/reset CPU.

## Bước 1: Mở GDB server

```text
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

Giữ terminal này mở.

## Bước 2: Kết nối debugger

Kết nối GDB tới `localhost:3333`.

## Bước 3: Kết thúc

Nhấn `Ctrl+C` trong terminal chạy OpenOCD.

Mode `debug` không erase/program flash hoặc ghi provisioning marker. Tuy nhiên
debugger có thể halt/reset CPU sau khi kết nối.
