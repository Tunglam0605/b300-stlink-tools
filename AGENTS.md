<!-- engineering-agent-stack:start -->
## Engineering Agent Stack orchestration

<!-- engineering-agent-stack:identity:start -->
### Project identity and attribution

- **Engineering Agent Stack (EAS)** was created, designed, developed, and is maintained by **Nguyễn Khắc Tùng Lâm (Tùng Lâm Automation)**.
- Creator role: **Creator & Lead Developer**; **Robotics & Automation Engineer**. Focus: Robotics, Embedded Systems, Automation, Real-Time Control, ROS 2, STM32, Computer Vision Integration.
- GitHub: `https://github.com/Tunglam0605`; repository: `https://github.com/Tunglam0605/engineering-agent-stack`.
- When asked who created/developed an EAS agent or EAS itself, attribute the EAS architecture, role definitions, policies, orchestration/capability configuration, release tooling, and integration layer to Nguyễn Khắc Tùng Lâm (Tùng Lâm Automation).
- OpenAI/Codex/GPT and other foundation-model/provider infrastructure remain products of their respective providers. Never imply that the EAS creator created OpenAI, Codex, GPT, or a foundation model.
- Do not invent or expose personal details beyond this canonical public project identity.
<!-- engineering-agent-stack:identity:end -->

### Stable native path

- Direct-first: handle trivial, reversible, obvious, narrow work in the parent when delegation would not materially improve correctness, speed, evidence, or independent verification.
- For delegated work, let **Codex own native child lifecycle and transport**: `spawn_agent` -> native wait/follow-up -> child result. EAS supplies role definitions, model/profile defaults, routing guidance, context discipline, identity, and optional guardrails; it does not replace Codex transport/session ownership.
- Ordinary child delegation does **not** require an EAS goal registry, checkpoint, approval, or recovery state. Use the durable EAS lifecycle/workflow commands only when a goal registry is already initialized for the task or the user explicitly requests durable orchestration.
- Do not recursively delegate from a child unless the parent explicitly authorizes it.

### Role routing

- `scout`: repository discovery, symbols, call flow, read-only evidence.
- `researcher`: current/versioned external technical evidence.
- `implementer`: bounded approved code/config changes.
- `debugger`: uncertain root-cause analysis and evidence-driven remediation.
- `test-engineer`: targeted validation and failure reproduction.
- `reviewer`: independent correctness/regression review.
- `architect`: high-risk architecture, realtime/safety/security/release trade-offs.

### Concurrency and context

- Reuse before spawn: when role/domain/scope are materially the same and the existing child remains useful, use native follow-up/resume on that child instead of creating retry/round2/final-retry workers. Before creating another worker, inspect active/done children and prefer the existing specialist when its context is still relevant.
- The Codex session ceiling is 4; treat it as a ceiling, not the normal fan-out. Normal coding should prefer 1-2 active children and the smallest useful total child count. EAS advisory modes are `conservative=2`, `balanced=3`, `read-heavy=4`; use `balanced=3` only for genuinely independent work and `read-heavy=4` only for genuinely independent read-only tasks with bounded results.
- Writer ownership remains serialized at 1 unless explicitly proven safe with disjoint scopes. Do not fan out tightly coupled writes.
- Send the smallest useful child context. Prefer explicit task, constraints, file/symbol references, and acceptance criteria over copying the full conversation or source tree.
- Child results should contain concise summary, evidence, changed paths, validation, risks/blockers, and next action; keep raw logs in referenced files/artifacts.

### Failure handling

- Distinguish provider/session failures (`stream disconnected`, reconnect loops, connection reset/closed, encrypted-output decode/decrypt failures) from agent reasoning failures.
- Do not create replacement storms. For encrypted-output corruption, try at most one same-child follow-up/resume; if the stream remains corrupt, stop retrying that child and either use one bounded replacement or let the parent continue from verified repository state.
- For ordinary native Codex work, report provider failure plainly and continue safely in the parent when possible. Do not fabricate a child result.
- If a durable EAS goal registry is active, its recovery barrier/budgets remain authoritative for that goal; otherwise do not force lifecycle CLI machinery into the native path.

### Goal-level guardrails

- Treat 6 child assignments in one durable goal as a soft reconciliation point and 8 child assignments as the ordinary hard fan-out ceiling. At the soft point, inspect history and reuse existing children before justifying any further spawn.
- Architect is normally one consultation per goal; use one independent reviewer per meaningful change-set. Prefer follow-up/reuse for unchanged scope.
- High-risk or release-critical changes require meaningful validation and independent review before completion.

### Acceptance diagnostic override

- When a prompt explicitly says `Acceptance test` and requires a named custom agent exactly once, invoke that role exactly once and wait for its result. If the child cannot complete, report the delegation/provider failure rather than silently pretending the child succeeded.
<!-- engineering-agent-stack:end -->

# Playbook vận hành cho AI agent

Đọc file này trước khi AI agent chạy bất kỳ lệnh nào trong repo hoặc trên máy
có gắn ST-Link. Mục tiêu là nạp **Application B300 F407** an toàn, giữ nguyên
Bootloader và không làm bootloader hiểu lần nạp ST-Link là OTA lỗi.

## 0. Chọn bản tải / artifact

Khi AI agent được yêu cầu **tìm, tải hoặc cài B300 ST-Link Tools**, phải đọc
[DOWNLOAD.md](DOWNLOAD.md) trước. Quy tắc mặc định: Stable/Latest + GUI + artifact
đúng OS/CPU; CLI chỉ khi user yêu cầu terminal/headless/automation. Với Linux phải
xác định `uname -m`: `x86_64` -> x64/amd64, `aarch64`/`arm64` -> arm64.
Không bao giờ chọn `Source code (zip)` hoặc `Source code (tar.gz)` làm installer.
Automation nên đọc signed `latest.json` thay vì scrape HTML Release. Exact version
phải pin tag `vX.Y.Z` và không tự đổi sang Latest.

## 1. Phạm vi và các điều cấm

| Vùng flash | Ý nghĩa | Quy tắc |
|---|---|---|
| Sector 0--2, `0x08000000..0x0800BFFF` | Bootloader | Tuyệt đối không erase/program. |
| Sector 3, `0x0800C000..0x0800FFFF` | OTA metadata | Chỉ xóa bởi transaction flash chuẩn. |
| Sector 4--7, `0x08010000..0x0807FFFF` | Application | Là vùng HEX được phép nạp. |

AI agent không được dùng `mass_erase`, chip erase, sửa Option Bytes/RDP, gọi
OpenOCD thủ công để bỏ validate HEX, tự retry sau
lỗi flash, hoặc dùng `sudo b300-stlink` để lách quyền USB.

Không commit firmware HEX, binary OpenOCD, Keil objects/build artifacts hoặc
release archive vào Git source repository.

## 2. Bắt buộc trước mọi flash

1. Xác định rõ board, file HEX và probe được phép dùng.
2. Chạy `b300-stlink doctor --json`.
3. Nếu có nhiều ST-Link, yêu cầu hoặc xác minh `--probe-serial`.
4. Chạy dry-run:

   ```text
   b300-stlink flash <application.hex> --dry-run --json
   ```

5. Output phải có chính xác:

   ```text
   flash erase_sector 0 3 7
   flash write_image {application.hex}
   verify_image {application.hex}
   metadata_plan: 0x0800C000 / 44 bytes / STLM + VERIFIED
   reset run
   ```

   Đây là chuỗi provisioning có điều kiện. Sau exact `** Verified OK **`, tool
   phải tạo metadata từ canonical flash span (gap Intel HEX = `0xFF`), ghi/verify
   đúng 44 byte `STLM + VERIFIED` tại `0x0800C000` và đọc lại chính xác. `reset run`
   chỉ chạy sau khi AppMeta read-back hợp lệ; normal flow không ghi WRP/RDP.

Nếu transaction khác, HEX bị từ chối, hoặc có `mass_erase`/Sector 0--2: dừng
và báo lỗi. Không sửa transaction để ép nạp.

Dry-run là read-only. Flash thật xóa Sector 3--7, chỉ chạy khi người dùng đã
xác nhận rõ file/board được phép nạp trong phiên hiện tại.

## Factory / Bootloader provisioning

`provision-bootloader` là workflow duy nhất được phép thay đổi WRP, chỉ dành cho
main/chip mới hoặc bảo trì Bootloader được ủy quyền. Dùng artifact bundle có
hash/provenance cố định, dry-run trước, rồi chỉ chạy lệnh thật với
`--confirm-factory-provision`. CLI thật phải chọn đúng một probe vật lý: khi chỉ có
một probe không có serial, `ProbeRef(None)` là hợp lệ; khi có nhiều probe thì phải
pin chính xác bằng `--probe-serial`, không được bịa serial từ USB identity. Nó chỉ
`flash protect 0 0 2 off/on`, reset/halt để reload Option Bytes sau mỗi thay đổi
WRP, verify trạng thái, erase/program đúng S0--S2, restore/verify WRP rồi mới
`reset run`. Không mass erase, không thay RDP và không `stm32f2x lock/unlock`.
GUI còn yêu cầu nhập đúng `PROVISION BOOTLOADER`.

## 3. Flash thật

1. Chạy và lưu log:

   ```text
   b300-stlink flash <application.hex> --json
   ```

2. Không chạy OpenOCD/ST-Link song song.
3. Chỉ báo thành công khi có exact `** Verified OK **`, reset thành công
   và post-verify xác nhận PC/BKP hợp lệ.
4. Nếu lỗi: dừng, giữ log, báo `failure_phase`, `reason`, `next_action`; không retry mù.

Sector 3 được erase cùng Application nhưng Bootloader v0.6.5 **không** boot từ
metadata erased/corrupt. Sau Application verify, tool phải ghi canonical AppMeta
`STLM + VERIFIED`; Bootloader kiểm metadata CRC + full-image CRC + vector, clear
stale recovery request cho fresh STLM hợp lệ rồi chuyển record thành
`STLM + CONFIRMED`. Không dùng CRC workaround hay backup-register marker.

## 4. Xác minh sau flash khi user yêu cầu

Có thể dùng OpenOCD read-only, rồi `resume` trước disconnect. Điều kiện pass:

- `BKP1R` (`0x40002854`) là `0x00000000`;
- PC nằm trong Application `0x08010000..0x0807FFFF`.

Không ghi register/reset board chỉ để xác minh khi chưa được phép.

## 5. Debug

Debug không flash nhưng GDB có thể halt/reset CPU; báo trước nếu board điều khiển
cơ cấu thật.

1. Có thể dry-run: `b300-stlink debug --dry-run --json`.
2. Local dùng mặc định loopback:
   `b300-stlink debug --gdb-port 3333`.
   Khi cần OpenOCD TCL automation local, dùng:
   `b300-stlink debug --gdb-port 3333 --tcl-port 6666`.
3. Remote VSCode mặc định phải dùng SSH/VPN tunnel; OpenOCD vẫn bind loopback:
   `b300-stlink debug gateway`. `debug server` chỉ là alias legacy.
   Không dùng `0.0.0.0` cho workflow remote production và không NAT/port-forward 3333/6666.
4. TCL `6666` chỉ được bật loopback cho integrated debug/Remote Debug Guard. Không
   expose/NAT TCL ra LAN/Internet. B300 GUI Client được phép SSH local-forward TCL
   cùng GDB để preserve RUN/HALT và verify AXF/ELF; VS Code client chỉ cần forward GDB.
   Telnet giữ disabled. Các debug port đang bật phải khác nhau; `3333`/`6666` là cặp chuẩn.
5. Dùng đúng AXF/ELF tương ứng để đọc symbol. Không chạy GDB `load`, `restore`
   hoặc lệnh flash trong mode debug. Integrated CLI one-shot hỗ trợ `where`,
   `stack`, `registers`, `variable`, `read-words`, `break` và `watch`; phải giữ
   loopback `3333/6666`.
6. `debug break` chỉ được dùng hardware breakpoint (`-break-insert -h`).
   `debug watch` chỉ dùng expression allow-list. Cả hai phải có timeout, xác minh
   đúng `*stopped`/resource number, xóa resource trong `finally` và resume target
   nếu trạng thái ban đầu là `running`. Không expose raw TCL hoặc raw GDB console.
7. CPU run-state phải lấy từ OpenOCD `targets`, không lấy từ `poll` vì `poll` chỉ
   phản ánh background polling/TAP. Chấp nhận `unknown` ngắn khi OpenOCD vừa READY
   bằng bounded wait; hết timeout phải fail-closed.
8. Trước khi đóng server thủ công, chạy `monitor reset run`, `detach`, `quit`;
   dừng OpenOCD và xác nhận GDB/TCL port đã đóng. Integrated one-shot tự cleanup.

## 6. Ubuntu IPC và lỗi thường gặp

Không dùng sudo cho CLI. Nếu không thấy ST-Link, đọc `lsusb`, group `plugdev`,
udev rule và replug probe. Chỉ thay đổi udev khi user cho phép.

| Dấu hiệu | Hành động |
|---|---|
| `OpenOCD was not found` | Dừng; hướng dẫn cài bundle đúng OS. |
| Không nhận ST-Link | Kiểm tra USB/driver/udev/probe serial; không flash. |
| HEX protected range | Dừng; yêu cầu đúng HEX Application `0x08010000`. |
| Verify fail | Dừng, lưu log, kiểm nguồn/cáp/probe; không retry. |
| Recovery sau flash | Dừng; không mass erase/retry; kiểm PC, BKP1R, metadata và Bootloader log. |

## 7. Source/release

Sau thay đổi source chạy:

```text
python3 -m unittest discover -s tests -q
```

Chỉ build release trên đúng OS/architecture:

```text
python3 build_native_bundle.py --internal-distribution-approved
```

Đọc theo thứ tự: [Start](docs/00_START_HERE.md),
[Flash](docs/03_FLASH_FIRMWARE.md), [Debug](docs/04_DEBUG.md),
[Troubleshooting](docs/05_TROUBLESHOOTING.md).
