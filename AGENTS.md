<!-- engineering-agent-stack:start -->
## Engineering Agent Stack orchestration

Use the installed custom agents selectively. The existence of seven roles does not imply seven child runs.

### Direct-first rule

Handle trivial, reversible, obvious, and narrow work directly when delegation would not materially improve correctness, speed, or verification.

Delegate when the task benefits from isolated context, specialist evidence, independent verification, or bounded parallel read work.

### Acceptance diagnostic override

- When a prompt explicitly says `Acceptance test` and requires one named custom agent exactly once, that requirement overrides the normal direct-first rule for that diagnostic only.
- In that case, the parent MUST invoke `spawn_agent` exactly once for the requested role and MUST NOT complete the assigned task directly as a fallback.
- Use a fresh child context with `fork_turns = "none"`, put the complete assignment/context delta in the child message, and wait for the child result before answering.
- If the child cannot be spawned, report the delegation failure instead of silently doing the work in the parent.

### Role routing

- `scout`: repository discovery, symbol lookup, call-flow tracing, read-only evidence.
- `researcher`: current/versioned primary-source evidence and external technical facts.
- `implementer`: bounded approved code/config changes with minimal diff scope.
- `debugger`: uncertain root-cause analysis and evidence-driven remediation.
- `test-engineer`: targeted validation, failure reproduction, and test evidence.
- `reviewer`: independent correctness/regression review; do not self-review an implementation through the same worker.
- `architect`: high-risk architecture, realtime/safety/security/release-critical trade-offs.

### Lifecycle and fan-out control

- Resume before spawn: before creating a new child, prefer a follow-up/resume on an existing child when role, problem domain, and scope are materially the same and its evidence remains relevant.
- Spawn a fresh child only when independent parallelism materially reduces elapsed time, independent review needs a separate context, or the existing child context is stale/mismatched.
- Default to at most 3 parallel read-only children and 1 writer scope owner. Do not create multiple active children with the same role, same problem domain, and same effective scope.
- Treat 8 child assignments in one goal as a soft reconciliation point: inspect active/done work, merge overlapping investigations, and explicitly justify any further spawn.
- Treat 12 child assignments as the ordinary hard fan-out ceiling. Beyond it, integrate, serialize, or escalate instead of spawning more children. Explicit acceptance diagnostics and a required independent safety/release review may exceed the ceiling only with a stated reason.
- Architect is normally one consultation per goal. Continue the same architect by follow-up when the decision is unchanged; use another architect only if assumptions/interfaces/safety constraints materially changed.
- Reviewer is normally one independent reviewer per meaningful change-set. Reuse that reviewer for fix verification instead of spawning a new reviewer for every patch.
- Use short stable names such as `scout: gateway`, `debugger: packaging`, or `reviewer: release`; avoid version/retry suffixes when an existing child can be resumed.

### Executable lifecycle gate (when initialized)

- For a long-running goal that has an EAS goal registry, consult `eas goal gate` before creating an ordinary new child and honor `REUSE / SPAWN / ESCALATE / REJECT`.
- Use `--commit` only when the dispatch decision is actually accepted; transition the recorded assignment as work starts/completes/fails/blocks.
- Prefer the returned `REUSE` assignment rather than creating a new native child.
- This is stack-controlled enforcement. If no goal registry is initialized, the policy instructions still apply, but do not claim that EAS transparently intercepted provider-native spawning.

### Execution constraints

- The parent owns decomposition, context allocation, integration, escalation, and the final answer.
- Do not recursively delegate from a child unless the parent explicitly authorizes it.
- Prefer parallel read-only work only when subtasks are independent.
- Concurrent writers require disjoint write scopes; otherwise serialize writes.
- For high-risk or release-critical changes, use independent review before completion.
- Send the smallest useful context. Prefer file/symbol references and explicit constraints over full conversation or source-tree copies.
- Compress child results into evidence, confidence, changed paths, validation, risks, and blockers. Do not return raw transcripts or repetitive logs.
- Token/result targets are adaptive. Never omit evidence required for correctness just to satisfy a soft size target.
- If a lower-compute route is uncertain or fails the required quality gate, escalate rather than guessing.
- Treat model/profile mappings as candidates; do not claim a cheaper route is better without repeated benchmark evidence.

### Codex V2 context handoff compatibility

- Prefer `fork_turns = "none"` for normal bounded child delegation and pass the complete task/context delta explicitly in the child message. This matches the stack's minimal-context policy and avoids unnecessary parent-history coupling.
- Request inherited parent history only when the task materially depends on it. Current Codex `exec --ephemeral` releases have an upstream V2 history-fork limitation, so a history-dependent child spawn may fail before the child starts.
- When using Codex V2 `wait_agent`, omit `timeout_ms` unless a specific wait is needed; if it is explicit, use at least `10000` ms for compatibility with affected releases.
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
