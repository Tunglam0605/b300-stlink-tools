# B300 ST-Link Tools v0.12.0 — Gateway Setup Wizard

## Mục tiêu

Cho phép một máy Windows hoặc Ubuntu mới trở thành **B300 Remote Debug Gateway** trực tiếp từ B300 ST-Link Tools, kể cả khi máy chưa có SSH Server. Máy đã có SSH hợp lệ được giữ nguyên cấu hình và Prepare trở thành thao tác idempotent/no-op.

## Kiến trúc an toàn

```text
STM32 + ST-Link
       |
   Gateway PC
       |
 OpenOCD localhost only
   3333 GDB
   4444 Telnet (không dùng / disabled trong B300 profile)
   6666 TCL
       |
      SSH TCP/22
------- LAN/Wi-Fi -------
       |
   Client GUI / CLI / VS Code
```

Gateway Setup chỉ quản lý SSH prerequisite của hệ điều hành. Nó **không tạo firewall rule cho 3333/4444/6666**, không sửa `sshd_config`, không đổi password và không tự tắt process đang expose debug port. Nếu phát hiện debug listener trên non-loopback address, Prepare fail-closed và yêu cầu sửa thủ công.

## GUI

Sidebar có mục **Gateway Setup**. Tab này lazy-load: chỉ đọc trạng thái hệ điều hành khi người dùng mở tab hoặc bấm Refresh.

Các hành động:

- **Refresh**: read-only host inspection.
- **Prepare This PC as Gateway**: hiển thị plan, yêu cầu người dùng xác nhận, sau đó mới xin UAC/administrator privilege.
- **Run Gateway Self-Test**: kết hợp SSH host readiness với Gateway doctor hiện có (OpenOCD/ST-Link/loopback ports).
- **Copy Client Configuration**: copy host/user/SSH port để nhập vào GUI Client của máy còn lại.

## CLI

Read-only plan:

```bash
b300-stlink gateway plan --json
```

Apply có kiểm soát:

```bash
b300-stlink gateway prepare --confirm-system-change --json
```

Full Gateway readiness (OpenOCD + probe + SSH + ports):

```bash
b300-stlink gateway doctor --json
```

`gateway prepare` không có `--confirm-system-change` sẽ trả `SYSTEM_CHANGE_CONFIRMATION_REQUIRED` nếu cần thay đổi hệ điều hành.

## Windows

Khi thiếu thành phần, một UAC transaction thực hiện đúng phần cần thiết:

1. `Add-WindowsCapability OpenSSH.Server~~~~0.0.1.0` nếu OpenSSH Server chưa có.
2. `sshd` startup = Automatic nếu chưa enabled.
3. Start `sshd` nếu chưa running.
4. Tạo rule `B300-OpenSSH-Server-In-TCP` cho TCP/22 chỉ khi chưa có rule OpenSSH/B300 enabled.

Tool không ghi `sshd_config`. Máy đã có `sshd` running/startup/firewall đúng thì không gọi elevated command.

## Ubuntu/Linux

Nếu đã chạy root thì dùng trực tiếp. GUI user thường dùng `pkexec`/PolicyKit. Không fallback sang `sudo` trong background để tránh treo GUI ở password prompt không nhìn thấy.

Khi cần:

1. `apt-get update` + `apt-get install -y openssh-server` nếu package chưa installed.
2. `systemctl enable --now ssh`.
3. Nếu UFW đang active và chưa allow SSH, chỉ chạy `ufw allow 22/tcp`.

Nếu UFW inactive thì không bật UFW và không tạo rule dư thừa.

## Custom SSH port

Gateway doctor vẫn có thể kiểm tra SSH port tùy chọn. Managed Prepare chỉ tự cấu hình TCP/22. Nếu custom-port server chưa READY, tool từ chối thay đổi vì việc này sẽ yêu cầu sửa `sshd_config`, trái với safety contract.

## Acceptance tối thiểu

- Máy chưa có SSH: plan phải liệt kê install/enable/start/firewall.
- Máy đã READY: plan rỗng, không elevation, không thay đổi hệ thống.
- Debug port expose ngoài loopback: hard block.
- GUI/CLI dùng chung backend.
- Packaged Windows/Linux CLI và GUI phải chứa feature.
- Two-machine acceptance cuối cùng vẫn phải xác minh kết nối thật từ Client qua SSH.
