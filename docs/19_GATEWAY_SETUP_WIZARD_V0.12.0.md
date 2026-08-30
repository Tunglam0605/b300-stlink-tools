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
## SSH Client + Key Bootstrap

Máy Client mới hoàn toàn không cần tự chạy `ssh-keygen` bằng Terminal. Trong tab **Gateway Setup**:

1. **Generate / Reuse Client Key** kiểm tra OpenSSH Client.
2. Nếu thiếu OpenSSH Client, GUI hỏi xác nhận trước khi dùng UAC (Windows) hoặc root/`pkexec` (Ubuntu) để cài component/package.
3. Tool tạo hoặc reuse `~/.ssh/b300_gateway_ed25519` bằng `ssh-ed25519`. Nếu chỉ một nửa key pair tồn tại hoặc public key hỏng, tool fail-closed và không overwrite.
4. **Copy Public Key** chỉ copy dòng `ssh-ed25519 ...`; private key không được đọc để export và không vào log/report.
5. Trên Gateway, **Authorize Client Public Key** validate canonical ed25519 rồi append idempotent. Windows account thuộc Administrators dùng `%ProgramData%\ssh\administrators_authorized_keys`; user thường/Linux dùng `~/.ssh/authorized_keys`.
6. Debug Client và Realtime Live Monitor tự nhận identity đã verify và thêm `IdentitiesOnly=yes` + `-i <B300 identity>` vào SSH tunnel.

CLI tương đương:

```bash
b300-stlink gateway client-key --json
b300-stlink gateway client-key --confirm-system-change --json
b300-stlink gateway authorize-key --public-key-file client.pub --confirm-system-change --json
```

`client-key` chỉ cài OpenSSH Client khi thiếu và khi có `--confirm-system-change`. `authorize-key` không nhận private key.
## Strict Host-Key Trust Bootstrap

B300 không tắt `StrictHostKeyChecking` và không dùng `accept-new`. Lần kết nối đầu tiên được bootstrap theo quy trình có đối chiếu fingerprint:

1. Trên máy Gateway vật lý, bấm **Show This Gateway Fingerprint** hoặc chạy `b300-stlink gateway host-key --json`. Tool chỉ đọc `ssh_host_ed25519_key.pub`; private host key không được đọc/export.
2. Copy fingerprint `SHA256:...` sang máy Client bằng kênh người vận hành kiểm soát.
3. Trên Client, nhập IP/hostname Gateway + SSH port + fingerprint vào **Strict SSH host trust**, rồi bấm **Scan + Verify + Trust**.
4. Tool dùng `ssh-keyscan -t ed25519` để lấy public host key từ mạng nhưng **không tin kết quả scan một mình**. Fingerprint scan phải trùng chính xác fingerprint đã lấy trực tiếp từ Gateway.
5. Khi trùng, key được append idempotent vào `~/.ssh/b300_known_hosts`. Nếu cùng host đã có key khác, thao tác fail-closed với `HOST_KEY_CONFLICT`; tool không overwrite tự động.
6. Debug Client, Realtime Live Monitor và VS Code kit tự thêm `UserKnownHostsFile=~/.ssh/b300_known_hosts` khi host đã enroll, đồng thời vẫn giữ `StrictHostKeyChecking=yes`.

CLI tương đương:

```bash
# On physical Gateway
b300-stlink gateway host-key --json

# On Client: scan only; no trust record is written yet
b300-stlink gateway trust-host --ssh-host 192.168.1.50 --json

# After comparing the exact SHA256 fingerprint
b300-stlink gateway trust-host \
  --ssh-host 192.168.1.50 \
  --confirm-host-fingerprint SHA256:EXACT_FINGERPRINT \
  --json
```

Đường này chống việc vô tình tin một Gateway giả/MITM trong lần kết nối đầu tiên tốt hơn việc tự động `accept-new`.
