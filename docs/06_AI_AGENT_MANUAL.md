# Dùng B300 ST-Link Tools với mọi AI agent

Repo hỗ trợ ba cách. Chọn **một** cách phù hợp với AI bạn đang dùng.

## Cách 1 — Người dùng chạy lệnh trực tiếp

Không cần AI. Làm theo [Bước 3 — Nạp firmware](03_FLASH_FIRMWARE.md):

```text
b300-stlink flash <application.hex>
```

## Cách 2 — Bảo bất kỳ AI nào đọc repo

Dùng cho ChatGPT, Gemini, Claude, Cursor hoặc agent không tự hỗ trợ Agent Skills.

Gửi prompt này kèm đường dẫn local hoặc URL repo:

```text
Đọc AGENTS.md và docs/06_AI_AGENT_MANUAL.md trong repo
https://github.com/Tunglam0605/b300-stlink-tools.
Tuân thủ playbook B300 ST-Link, chạy dry-run trước; chỉ flash thật khi tôi xác nhận.
```

Agent phải dùng `AGENTS.md` như quy trình chuẩn. Nếu AI chỉ đọc web mà không
điều khiển terminal/USB, nó vẫn có thể hướng dẫn nhưng không thể tự nạp board.

## Cách 3 — Cài skill portable cho agent có Agent Skills

Skill dùng chuẩn open `SKILL.md`, nên dùng được với các agent hỗ trợ Agent Skills.
Không phải mọi chatbot đều tự discovery skill; với agent không hỗ trợ thì dùng
Cách 2.

Từ repo đã clone, cài vào skills root dùng chung:

```text
python3 scripts/install_skill.py --destination ~/.agents/skills
```

Windows PowerShell:

```powershell
py scripts\install_skill.py --destination "$HOME\.agents\skills"
```

Sau đó khởi động lại agent và yêu cầu:

```text
Use the b300-ota-stlink skill to provision this B300 F407 Application HEX.
```

Với Codex chạy trong repo này, skill nằm sẵn ở
`.agents/skills/b300-ota-stlink` nên agent có thể tự phát hiện. Với agent có
thư mục skills riêng, dùng `--destination` theo thư mục skills mà agent đó công
bố; vẫn giữ bản canonical trong repo này.

## Khi agent chưa biết lệnh `b300-stlink`

Skill hướng dẫn quy trình, không tự cài executable. Trước flash, agent phải làm
setup Windows/Ubuntu theo tài liệu, rồi kiểm tra:

```text
b300-stlink doctor --json
```

`available=true` là điều kiện trước khi dry-run/flash/debug.
