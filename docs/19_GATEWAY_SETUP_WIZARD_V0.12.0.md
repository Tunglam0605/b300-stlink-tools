# B300 ST-Link Tools v0.12.0 — Gateway Setup & Remote Workflow

## Mục tiêu

Cho phép một máy Windows hoặc Ubuntu mới trở thành **B300 Remote Debug Gateway** mà không cần người dùng tự cài/cấu hình SSH bằng Terminal. Máy Client cũng được bootstrap key/trust/profile bằng tool, giảm thao tác lặp lại nhưng không làm yếu cơ chế xác thực.

## Kiến trúc an toàn

```text
STM32 + ST-Link
       |
   Gateway PC
       |
 OpenOCD localhost only
   3333 GDB
   4444 Telnet (disabled trong B300 profile)
   6666 Safe TCL
       |
      SSH TCP/22
------- LAN/Wi-Fi -------
       |
 Client GUI / CLI / VS Code
```

B300 Tools chỉ cho phép SSH là cổng ingress từ mạng. Tool **không** tạo firewall rule cho `3333/4444/6666`, không sửa `sshd_config`, không đổi password và không tự overwrite host key đã thay đổi.

## Workflow khuyến nghị: 2 máy, ít thao tác

### 1. Trên Gateway

Chạy trước ở chế độ không thay đổi hệ thống:

```bash
b300-stlink gateway quickstart
```

Nếu OpenSSH Server/service/firewall chưa sẵn sàng, tool trả `SYSTEM_CHANGE_CONFIRMATION_REQUIRED` và liệt kê chính xác action cần làm. Sau khi kiểm tra plan:

```bash
b300-stlink gateway quickstart --confirm-system-change
```

Khi PASS, output gồm:

- IP/hostname candidate;
- SSH username/port;
- fingerprint `ssh-ed25519` của Gateway;
- một `client_setup_command` hoàn chỉnh để chạy trên Client;
- xác nhận `3333/4444/6666` vẫn loopback-only.

Nếu Gateway đã READY, `quickstart` là no-op đối với hệ điều hành.

### 2. Trên Client

Dùng nguyên `client_setup_command` do Gateway in ra. Ví dụ:

```bash
b300-stlink gateway client-setup \
  --ssh-host 192.168.1.50 \
  --ssh-user automation \
  --ssh-port 22 \
  --confirm-host-fingerprint SHA256:EXACT_FINGERPRINT
```

`client-setup` thực hiện theo thứ tự:

1. kiểm tra OpenSSH Client + `ssh-keygen`;
2. nếu thiếu thì **dừng để xin `--confirm-system-change`**, không tự cài ngầm;
3. tạo/reuse `~/.ssh/b300_gateway_ed25519`;
4. scan public host key `ed25519` của Gateway;
5. so sánh fingerprint scan với fingerprint lấy trực tiếp từ Gateway;
6. chỉ khi trùng chính xác mới ghi `~/.ssh/b300_known_hosts`;
7. lưu profile chỉ gồm `host/user/port`;
8. in `authorize_command` chứa **chỉ public key** để copy về Gateway.

Private key không rời Client và không được đưa vào log/report/profile.

Nếu fingerprint không được cung cấp, tool chỉ hiển thị fingerprint scan và dừng ở `HOST_KEY_FINGERPRINT_CONFIRMATION_REQUIRED`. Nếu fingerprint sai, tool fail-closed với `HOST_KEY_FINGERPRINT_MISMATCH`.

### 3. Trở lại Gateway: authorize public key

Chạy nguyên `authorize_command` mà Client in ra:

```bash
b300-stlink gateway authorize-key \
  --public-key "ssh-ed25519 AAAA..." \
  --confirm-system-change
```

Không còn bắt buộc tạo/copy file `.pub` trung gian. `--public-key-file` vẫn được giữ để backward compatibility. Việc append key là idempotent.

### 4. Trên Client: xác minh kết nối thật

```bash
b300-stlink gateway connect-check
```

Lệnh này dùng:

- `BatchMode=yes`;
- `StrictHostKeyChecking=yes`;
- B300 managed `known_hosts`;
- `IdentitiesOnly=yes`;
- `PasswordAuthentication=no`;
- timeout hữu hạn;
- không forward `3333/6666`.

Chỉ PASS khi SSH trả đúng token `B300_SSH_READY`. Đây mới là bằng chứng public key đã được authorize và kết nối thật hoạt động.

### 5. Xem trạng thái local

```bash
b300-stlink gateway status
```

`status` chỉ kết luận **LOCAL SETUP READY** khi OpenSSH Client, B300 key, saved profile và managed host trust đều có. Nó luôn ghi `connectivity_verified=false`; muốn xác minh Gateway thật phải chạy `gateway connect-check`.

### 6. Debug/Live/VS Code không cần lặp host/user

Sau `client-setup`, endpoint được lấy từ saved profile:

```bash
b300-stlink debug client \
  --client-action inspect \
  --symbols Main_V2_F407.axf \
  --json

b300-stlink debug client \
  --client-action live \
  --symbols Main_V2_F407.axf \
  --live-interval 0.5 \
  --live-watch xTickCount:u32

b300-stlink debug vscode \
  --program-relative Objects/F407/Main_V2_F407.axf \
  --output-dir .
```

Nếu endpoint được lấy tự động từ saved profile nhưng managed private key hoặc managed host trust bị mất, tool **không fallback âm thầm** sang SSH mặc định; nó yêu cầu chạy `gateway status`/`gateway client-setup` để khôi phục trạng thái rõ ràng.

## Saved profile

Profile không chứa secret. Nội dung duy nhất:

```json
{
  "schema_version": 1,
  "host": "192.168.1.50",
  "user": "automation",
  "port": 22
}
```

Vị trí mặc định:

- Windows: `%LOCALAPPDATA%\B300-STLink\remote_gateway.json`
- Linux: `$XDG_CONFIG_HOME/b300-stlink/remote_gateway.json` hoặc `~/.config/b300-stlink/remote_gateway.json`

Xóa riêng endpoint profile:

```bash
b300-stlink gateway profile-clear
```

Lệnh này **không xóa** Client private key và **không xóa** `b300_known_hosts`.

## Các primitive thấp hơn vẫn được giữ

Khi cần chẩn đoán chi tiết:

```bash
b300-stlink gateway doctor --json
b300-stlink gateway plan --json
b300-stlink gateway prepare --confirm-system-change --json
b300-stlink gateway client-key --json
b300-stlink gateway host-key --json
b300-stlink gateway trust-host --ssh-host <gateway> --json
```

Workflow mới chỉ orchestration phía trên các primitive hiện có, không thay đổi tunnel/debug/Live Monitor safety contract.

## Windows

Khi được xác nhận, Gateway Prepare chỉ làm những việc cần thiết:

1. cài `OpenSSH.Server~~~~0.0.1.0` nếu thiếu;
2. đặt `sshd` startup Automatic nếu chưa enabled;
3. start `sshd` nếu chưa chạy;
4. thêm allow rule SSH TCP/22 nếu chưa có.

Tool không ghi `sshd_config`. Máy đã READY không gọi elevated command.

## Ubuntu/Linux

GUI dùng root hoặc `pkexec`; không fallback sang `sudo` background có thể treo vì prompt password. Khi cần, tool cài `openssh-server`, enable/start `ssh`, và chỉ thêm `ufw allow 22/tcp` nếu UFW đang active mà SSH chưa được allow. Tool không tự bật UFW.

## Custom SSH port

Client profile và `connect-check` hỗ trợ custom SSH port. Managed Gateway Prepare chỉ tự cấu hình TCP/22; nếu một custom-port server chưa READY và việc sửa cần đụng `sshd_config`, tool yêu cầu xử lý thủ công thay vì tự thay đổi policy.

## Acceptance tối thiểu

- Gateway thiếu SSH: quickstart chỉ báo plan cho tới khi có confirmation.
- Gateway READY: quickstart không thay đổi hệ điều hành.
- Debug port exposed ngoài loopback: hard block.
- Client thiếu OpenSSH: client-setup yêu cầu confirmation trước OS change.
- Host fingerprint mismatch/conflict: hard block, không overwrite.
- Profile không chứa password/private key.
- `status` không giả vờ kết nối thật đã PASS.
- `connect-check` phải dùng strict host trust + public-key-only authentication.
- Debug/Live/VS Code profile-backed mode fail-closed nếu managed key/trust bị mất.
- Two-machine real acceptance vẫn là gate thực địa cuối cùng.
