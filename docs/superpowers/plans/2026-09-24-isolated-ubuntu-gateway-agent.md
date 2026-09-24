# Isolated Ubuntu Gateway Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Ubuntu Gateway's remote Application HEX immutable to its SSH/SFTP operator after finalize, without regressing Windows EXE/Linux Client Debug, Monitor, or flash workflows.

**Architecture:** A non-login `b300-agent` system account owns one root-installed Agent, the ST-Link, durable state, and finalized jobs. The existing `aubot` SSH CLI sends bounded Agent requests over a credential-checked Unix socket; SFTP writes only an Agent-created ingress file, which the Agent copies and hashes into private storage before prepare. All probe modes share one system-scoped owner lock.

**Tech Stack:** Python 3.9+, stdlib Unix sockets/systemd on Ubuntu x64/ARM64, Paramiko SFTP Client, PySide6 Windows/Linux Client, OpenOCD/ST-Link through the existing `B300Service`.

**Spec:** `docs/superpowers/specs/2026-09-24-isolated-ubuntu-gateway-agent-design.md`

## Global Constraints

- Ubuntu x64/ARM64 Gateway only; Windows x64 EXE and Linux Clients remain supported. Windows Gateway remote Application programming must not advertise the isolated capability.
- Keep Client SSH/SFTP login, saved profiles, allowlisted command names, and JSON response fields stable; no new LAN-facing port.
- Agent runtime is non-root. Root owns executable and systemd unit; `b300-agent` owns private state. `aubot` cannot write Agent-private files or open ST-Link USB.
- The SSH account is still a trusted Debug operator; do not claim a sandbox against malicious raw debugger traffic.
- Ingress suffix remains `program-jobs/<job-id>/artifact.part`; prepare/commit/flash use only an Agent-private verified copy.
- Bootloader S0--S2 and WRP/RDP are never changed by remote Application flash; the exact canonical S3--S7 transaction and post-verify stay in `B300Service.flash()`.
- Never flash, retry, tag, publish, or change `aubot-tech` before the required tests and authorization gates. A real GUI flash requires current-session board/probe/HEX confirmation.
- Preserve the existing uncommitted `CHANGELOG.md` diff; stage only task-owned paths until release-note review.

---

## File map

- `b300_core/gateway_unix_transport.py`: bounded length-framed Unix socket Client/Server, peer credential verification, no raw shell or arbitrary command transport.
- `b300_core/gateway_system_mode.py`: trusted marker/runtime/socket/ingress paths and fail-closed Linux mode detection.
- `b300_core/gateway_agent.py`, `b300_stlink.py`: start system Agent socket server, proxy existing request/status/ensure commands, disable legacy per-user spawn in system mode.
- `b300_core/gateway_program_jobs.py`: create constrained SFTP ingress slot and promote bytes to Agent-private staged artifact.
- `b300_core/hardware_owner.py`, `b300_core/gateway_protocol.py`, `b300_core/remote_session.py`: one system owner lock and capability negotiation; refuse old unisolated remote-flash capability.
- `packaging/linux/b300-stlink-gateway-agent-system.service`, `install.sh`, `b300_core/gateway_agent_setup.py`, `scripts/install_isolated_gateway.py`: root-owned system service setup, exact-path checks, backup/drain/migration, and rollback.
- `tests/test_remote_programming.py`, `tests/test_gateway_program_jobs.py`, and new transport/system setup tests: real behavior and adversarial regressions.
- `docs/02_SETUP_UBUNTU_IPC.md`, `docs/03_FLASH_FIRMWARE.md`, `docs/04_DEBUG.md`, `docs/05_TROUBLESHOOTING.md`, acceptance report, and `CHANGELOG.md`: accurate user and release evidence.

### Task 1: Restore Windows CI baseline

**Files:** Modify `tests/test_remote_programming.py:112-124` only.

**Interfaces:** No production API changes. The path passed to `inspect_image` must identify the selected firmware file even when Windows uses an 8.3 alias (`RUNNER~1`) for the same directory.

- [ ] **Step 1: Record the existing red test:** inspect the failed Windows x64 job on PR #29; it fails because `Path.absolute()` and `Path.resolve()` spell the same file differently. Keep the failing job URL in the commit/PR evidence.
- [ ] **Step 2: Replace only the lexical assertion:**

```python
self.assertEqual(fake.calls[0][0], "inspect_image")
self.assertTrue(os.path.samefile(fake.calls[0][1], path))
```

- [ ] **Step 3: Verify:** run `py -3 -m unittest tests.test_remote_programming -q` and `git diff --check`; push only after local pass, then require fresh Windows x64 and both Ubuntu CI jobs to pass on that commit.
- [ ] **Step 4: Commit:** `test: compare Windows firmware path by file identity`.

### Task 2: Add bounded system-Agent socket transport

**Files:** Create `b300_core/gateway_unix_transport.py`, `b300_core/gateway_system_mode.py`, `tests/test_gateway_unix_transport.py`; modify `b300_core/gateway_agent.py`, `b300_stlink.py`, `tests/test_gateway_agent.py`.

**Interfaces:** `GatewayUnixClient(socket_path: Path).submit_request(request: GatewayRequest, timeout_seconds: float) -> dict`; `GatewayUnixServer(socket_path: Path, allowed_uid: int, submit: Callable[[GatewayRequest, float], dict]).serve(stop_event: threading.Event) -> None`; `isolated_gateway_mode() -> bool` reads only an administrator-owned marker and never infers mode from a missing socket.

- [ ] **Step 1: Write failing transport tests:** a same-user Client sends one real `GatewayRequest.create("status", {})` through a temporary Unix socket and gets a bounded protocol response; oversized frame, invalid schema, peer UID mismatch, and socket disappearance fail closed without invoking `submit`.
- [ ] **Step 2: Verify red:** `python3 -m unittest tests.test_gateway_unix_transport -v` fails because the transport module does not exist.
- [ ] **Step 3: Implement the framed boundary:** use a four-byte big-endian length, `MAX_REQUEST_BYTES`/`MAX_RESPONSE_BYTES`, socket timeouts, `SO_PEERCRED` on Linux, exact allowed UID, JSON schema validation through `GatewayRequest.from_record`, and `finally` closure. The request frame is exactly `json.dumps(request.to_record(), separators=(",", ":")).encode("utf-8")`; no pickle or shell fragment.

```python
payload = json.dumps(request.to_record(), separators=(",", ":")).encode("utf-8")
if len(payload) > MAX_REQUEST_BYTES:
    raise ValueError("Gateway request exceeds limit")
connection.sendall(struct.pack(">I", len(payload)) + payload)
peer_pid, peer_uid, peer_gid = struct.unpack("3i", accepted.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
if peer_uid != allowed_uid:
    raise PermissionError("Gateway socket peer is not the approved SSH operator")
```
- [ ] **Step 4: Integrate without a second Agent:** the system Agent runs the socket accept loop alongside its existing private `GatewayRequestStore` loop; socket handlers call the store's `submit_request` and retain the current replay/response rules. In isolated mode the SSH CLI uses the socket for status, ensure, lease and program requests; it returns `GATEWAY_AGENT_NOT_RUNNING` if the socket is absent and never calls `ensure_running()` for a per-user process.
- [ ] **Step 5: Verify and commit:** run transport, Agent, protocol, and lease tests; use `python3 -m unittest tests.test_gateway_unix_transport tests.test_gateway_agent tests.test_gateway_lease_coordinator -q`, `python3 -m compileall -q b300_core b300_stlink.py`, and `git diff --check`. Commit `feat: route Ubuntu gateway control through isolated agent socket`.

### Task 3: Promote SFTP bytes into Agent-private jobs

**Files:** Modify `b300_core/gateway_program_jobs.py`, `b300_core/remote_programming.py`, `b300_core/remote_session.py`; add `tests/test_gateway_isolated_staging.py`; extend `tests/test_gateway_program_jobs.py` and `tests/test_remote_session.py`.

**Interfaces:** `GatewayProgramJobs(..., ingress_root: Optional[Path] = None)` retains `staged_path(job_id) -> Path` for the private artifact; `create_upload()` returns only `ingress_root / "program-jobs" / job_id / "artifact.part"`; `finalize_upload()` copies from the opened ingress descriptor to a new private temporary file, verifies `manifest.size` and `manifest.sha256`, and atomically replaces neither an existing artifact nor a prior approval.

- [ ] **Step 1: Write failing tests:** after SFTP finalize, replace/delete/rewrite ingress and assert prepare and commit still use the original private HEX; an ingress symlink, hardlink, oversized file, short copy, or concurrent mutation fails before target inspection; a finalized private path is never exposed in public status or upload JSON.
- [ ] **Step 2: Verify red:** `python3 -m unittest tests.test_gateway_isolated_staging -v` fails against the current same-path rename implementation.
- [ ] **Step 3: Create bounded ingress:** Agent owns each job directory; on POSIX it has mode `0710` with upload group traversal only. Agent pre-creates `artifact.part` as a regular group-writable file (`0660`) so SFTP can open/truncate it but cannot rename directory entries. The installer assigns the upload group; the Agent verifies actual owner/group/mode before exposing the slot.
- [ ] **Step 4: Copy-and-hash from one descriptor:** open with `O_RDONLY | O_NOFOLLOW | O_CLOEXEC` where available, verify `fstat` regular/size/link count and parent containment, stream at most 32 MiB to `tempfile.mkstemp(dir=private_job_dir)`, hash the bytes written, `fsync`, compare exact size/SHA, set private file `0600`, then atomically promote only if no target exists. Leave a failed job non-runnable and retain bounded failure evidence. Prepare/commit/worker flash receive only the private path.

```python
private_fd, private_name = tempfile.mkstemp(dir=str(private_job_dir))
digest = hashlib.sha256()
copied_size = 0
source_fd = os.open(str(ingress), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
source_info = os.fstat(source_fd)
if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
    raise ProgramJobError("STAGING_UNSAFE")
with os.fdopen(source_fd, "rb") as source, os.fdopen(private_fd, "wb") as target:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        copied_size += len(chunk)
        if copied_size > 32 * 1024 * 1024:
            raise ProgramJobError("UPLOAD_TOO_LARGE")
        target.write(chunk)
        digest.update(chunk)
    target.flush()
    os.fsync(target.fileno())
if copied_size != manifest.size or digest.hexdigest() != manifest.sha256:
    raise ProgramJobError("UPLOAD_HASH_MISMATCH")
```

The implementation must close/unlink the private temporary on every exception and compare pre/post source descriptor identity; the snippet shows the required byte flow, not a replacement for cleanup.
- [ ] **Step 5: Verify and commit:** run isolated staging, program jobs, remote programming, remote session, and flash-service tests; use `python3 -m unittest tests.test_gateway_isolated_staging tests.test_gateway_program_jobs tests.test_remote_programming tests.test_remote_session tests.test_flash_service -q`. Commit `feat: seal remote firmware in agent-private staging`.

### Task 4: Bind all modes and capability to one system owner

**Files:** Modify `b300_core/hardware_owner.py`, `b300_core/gateway_protocol.py`, `b300_core/remote_session.py`, `b300_stlink.py`, `b300_core/gateway_agent_setup.py`; extend `tests/test_hardware_owner.py`, `tests/test_gateway_protocol.py`, `tests/test_remote_session.py`, `tests/test_gateway_agent_setup.py`, `tests/test_v018_vscode_controller.py`.

**Interfaces:** In isolated Linux mode `default_hardware_owner_path()` resolves to the administrator-configured Agent state root, not `Path.home()`; the live Agent advertises `remote_application_flash_isolated_v1` only when its private ingress/socket/owner prerequisites are verified. The Client requires this new capability for remote flash; legacy Debug/Monitor capabilities and JSON schema remain compatible.

- [ ] **Step 1: Write failing tests:** a new Client refuses a Gateway that advertises only `remote_application_flash_v1`; a CLI status wrapper cannot claim isolated capability if the live Agent lacks it; `debug gateway/status/ensure/rescan` under the SSH user does not start a second per-user owner in isolated mode; Debug and Flash contend on the same owner path.
- [ ] **Step 2: Verify red:** run the named focused modules and observe the old static capability/per-user spawn behavior fail the new assertions.
- [ ] **Step 3: Implement one-owner routing:** use the trusted mode marker for both owner path and proxy path, not an environment variable supplied by an SSH request. Disable per-user Agent and legacy Gateway auto-spawn in isolated mode. Keep OpenOCD at loopback `127.0.0.1`, Telnet off, GDB flash disabled, and preserve VS Code attach/Live Monitor forwarding and RUNNING cleanup.

```python
if isolated_gateway_mode():
    return GatewayUnixClient(system_socket_path()).submit_request(request, timeout_seconds)
return GatewayRequestStore().submit_request(request, timeout_seconds=timeout_seconds)
```

The system-mode status/ensure branch must use this same condition even when the socket is absent, returning `GATEWAY_AGENT_NOT_RUNNING` rather than falling through to the per-user launcher.
- [ ] **Step 4: Verify and commit:** run the modules above plus GUI remote-program tests and Debug controller tests; compile sources and check diff. Commit `feat: gate remote flash on isolated gateway ownership`.

### Task 5: Install and roll back the Ubuntu service safely

**Files:** Create `packaging/linux/b300-stlink-gateway-agent-system.service`, `scripts/install_isolated_gateway.py`, `tests/test_gateway_system_install.py`; modify `install.sh`, `b300_core/gateway_agent_setup.py`, `build_native_bundle.py`, `docs/02_SETUP_UBUNTU_IPC.md`, `docs/04_DEBUG.md`, `docs/05_TROUBLESHOOTING.md`.

**Interfaces:** `scripts/install_isolated_gateway.py plan --json` is read-only; `apply --confirm-system-change --json` requires an idle Agent/owner and exact root-owned bundle; `rollback --confirm-system-change --json` preserves both old and new evidence, restores the old Debug-only service, and leaves remote Application flash disabled. No installer automatically invokes apply during a Client update.

- [ ] **Step 1: Write failing plan/apply/rollback tests:** fake `systemctl`, udev and filesystem adapters prove plan makes no changes; apply refuses an active lease/job, stale OpenOCD, unexpected path/symlink, wrong bundle hash, or an already-owned USB device; interrupted apply leaves recorded rollback actions and no real flash; rollback never deletes job evidence or auto-retries.
- [ ] **Step 2: Verify red:** `python3 -m unittest tests.test_gateway_system_install -v` fails because the installer entry point is absent.
- [ ] **Step 3: Implement exact host layout:** root owns `/opt/b300-stlink` and the systemd system unit; `b300-agent` has no login shell, owns `/var/lib/b300-stlink/gateway` (`0700`) and the system-scoped hardware lock; `/run/b300-stlink/agent.sock` is `0660` for the approved operator group; SFTP ingress group permits only the pre-created file write. Pin the ST-Link `0483:3748` udev permission to an Agent-only group without `uaccess`, then inspect the actual device ACL to ensure `aubot` cannot open it. Do not remove unrelated USB/group access.

```ini
[Service]
User=b300-agent
Group=b300-agent
SupplementaryGroups=b300-probe b300-upload b300-operator
ExecStart=/opt/b300-stlink/bin/b300-stlink debug gateway-agent --managed-child --json
RuntimeDirectory=b300-stlink
RuntimeDirectoryMode=0755
StateDirectory=b300-stlink
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=yes
```

The install script must write an administrator-owned isolated-mode marker before disabling per-user auto-spawn and must verify the new service/ACLs before reporting success; on failure it records the exact rollback state and does not erase old job logs.
- [ ] **Step 4: Integrate package/setup:** include the system unit and installer in Linux x64/ARM64 bundles; preserve existing user-service installation for legacy Debug-only deployments but never claim isolated remote flash there. Document the drain, backup, service switch, health check, and rollback sequence.
- [ ] **Step 5: Verify and commit:** run installer/setup/package tests under Ubuntu x64/ARM64 CI, shell syntax check, Python compile, and `git diff --check`; commit `feat: package isolated Ubuntu gateway service and rollback`.

### Task 6: Whole-branch release candidate gates

**Files:** Update `CHANGELOG.md`, `docs/03_FLASH_FIRMWARE.md`, `docs/acceptance/remote-application-programming-2026-09-23.md`; add a dated isolation/rollout acceptance report under `docs/acceptance/`.

**Interfaces:** Release documentation records exact commit, package hashes, CI run IDs, Gateway version/capability, and every physical result. It must distinguish internal candidate from public Stable.

- [ ] **Step 1: Run software gates:** `python3 -m unittest discover -s tests -q`, repository module-isolated GUI runner, `python3 -m compileall -q b300_core b300_cli b300_gui b300_stlink.py`, `git diff --check`, and native no-publish packages on Windows x64, Ubuntu x64, Ubuntu ARM64. Independent reviewer inspects the full branch and the new trust boundary.
- [ ] **Step 2: Build exact candidate:** run `python3 build_native_bundle.py --internal-distribution-approved` on each native OS/architecture, validate runtime integrity and GUI/CLI smoke, record SHA-256 and source SHA. Do not commit firmware/binaries/archives.
- [ ] **Step 3: Migrate `aubot-tech` only after candidate software gates:** verify `uname -m`, host key, Agent/owner IDLE, board/probe health, record old bundle and logs, then apply the administrator-approved migration. Read back service identity, file/USB ACLs, capability, socket, debug listener and rollback evidence. Stop and preserve state on any failed check.
- [ ] **Step 4: Field acceptance:** perform Gateway dry-run and exact plan review. Ask the user for current-session approval naming the attached board, selected probe, exact `Main_V2_F407.hex` path and SHA-256 before a **visible packaged GUI** destructive flash. Verify exact `** Verified OK **`, 44-byte AppMeta, `STLM CONFIRMED`, PC in Application, and `BKP1R == 0`; do not retry a failed transaction. Controlled SSH disconnect/Agent crash tests require a safe physical test state and separate approval if they can interrupt an active flash. Recheck Client Debug/Monitor and cleanup.
- [ ] **Step 5: Final release gate:** only when all evidence is PASS, update version/release metadata, request independent release review, merge via the approved branch process, publish signed Stable assets, and verify the public `latest.json`/`latest-cli.json` signatures and version-pinned URLs. If any physical gate is deferred, stop at candidate and report it plainly.

## Self-review

Tasks 2--5 implement the Spec's socket, private ingress/copy, one-owner capability, and host migration sections. Task 6 supplies rollback/field/software evidence and protects the no-flash-before-approval boundary. Task 1 repairs the independently observed Windows CI failure before changing Gateway architecture. Every task has a focused test command, a fail-first observation, an implementation surface, and a commit boundary.
