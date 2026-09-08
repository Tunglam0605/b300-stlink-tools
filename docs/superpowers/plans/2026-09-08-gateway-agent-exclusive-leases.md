# Gateway Agent Exclusive Leases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an autostart-capable, idle Gateway Agent that grants one exclusive remote Live Watch or VS Code Debug lease, survives repeated start/stop cycles, and releases OpenOCD, ports and ST-Link after explicit stop or bounded Client loss.

**Architecture:** A new lease model and coordinator sit above the existing `GatewaySupervisor`. An unprivileged per-user Agent owns the coordinator and accepts size-bounded same-user file-queue requests invoked through the existing SSH CLI; Clients acquire, renew and release a generation-bound token before opening tunnels. Windows Scheduled Task and Linux systemd-user integration provide best-effort autostart, while SSH `agent-ensure` remains the reliable fallback.

**Tech Stack:** Python 3.9-compatible standard library, existing PySide6 GUI, Paramiko SSH transport, OpenOCD/DebugService, `unittest`, fake clocks/processes/transports, PyInstaller/package scripts, EAS v0.6.2 `embedded` and `release` presets.

**Spec:** `../specs/2026-09-08-gateway-agent-exclusive-lease-design.md`

## Global Constraints

- Never access IPC `10.1.200.208`, flash firmware or touch attached hardware in automated tests.
- Preserve normal flash, Sector 0--2, metadata, WRP, RDP and Factory provisioning policy unchanged.
- Bind GDB/TCL only to `127.0.0.1`; keep Telnet disabled and never add a network control listener.
- Never run `sudo b300-stlink`, log credentials, expose a raw lease token, or kill a process B300 cannot prove it owns.
- One lease covers all remote hardware use: `LIVE_WATCH` and `VSCODE_DEBUG` are mutually exclusive across Clients.
- Use monotonic time for lease expiry; heartbeat 5 s, TTL 20 s, reconnect grace 10 s, cleanup target 5 s.
- Every production behavior follows test RED -> minimal GREEN -> focused regression -> independent review.
- Parent owns integration, commits, CI, release and installation. Concurrent writers require disjoint files.
- Final CI must pass Windows x64, Ubuntu x64 and Ubuntu ARM64 before publishing the SemVer minor release.
- `HW-P1-001` remains `OPEN / DEFERRED` without direct hardware evidence.

---

### Task 1: Lease contract and atomic store

**Files:**
- Create: `b300_core/gateway_lease.py`
- Test: `tests/test_gateway_lease.py`

**Interfaces:**
- Produces `GatewayLeasePolicy`, `GatewayLeaseRequest`, `GatewayLease`, `GatewayLeaseGrant`, `GatewayLeaseBusy`, `GatewayLeasePublicSnapshot`, `GatewayLeaseStore`, `sanitize_client_label()` and `token_digest()`.
- `GatewayLeaseStore` persists only the SHA-256 digest of the raw token and uses atomic replace under the existing Gateway runtime root.
- Later tasks consume strict `to_record()` / `from_record()` records and the mode values `LIVE_WATCH` and `VSCODE_DEBUG`.

- [ ] **Step 1: Write the failing contract tests**

```python
class GatewayLeaseContractTests(unittest.TestCase):
    def test_store_round_trip_never_persists_raw_token(self):
        lease = lease_fixture(token_digest=token_digest("secret-token"))
        store.write(lease)
        self.assertEqual(store.read(), lease)
        self.assertNotIn("secret-token", store.path.read_text(encoding="utf-8"))

    def test_invalid_mode_generation_and_deadline_fail_closed(self):
        for patch in ({"mode": "FLASH"}, {"generation": -1}, {"deadline_mono": -1.0}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                GatewayLease.from_record({**valid_record(), **patch})

    def test_client_label_is_bounded_and_removes_control_characters(self):
        self.assertEqual(sanitize_client_label(" ENG\nLAPTOP\t01 "), "ENG LAPTOP 01")
        self.assertLessEqual(len(sanitize_client_label("x" * 300)), 64)
```

- [ ] **Step 2: Run Task 1 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_lease`

Expected: import failure because `b300_core.gateway_lease` does not exist.

- [ ] **Step 3: Implement the strict immutable contract and store**

```python
@dataclass(frozen=True)
class GatewayLeasePolicy:
    heartbeat_interval_seconds: float = 5.0
    lease_ttl_seconds: float = 20.0
    reconnect_grace_seconds: float = 10.0
    cleanup_timeout_seconds: float = 5.0

@dataclass(frozen=True)
class GatewayLeaseRequest:
    request_id: str
    client_id: str
    client_label: str
    mode: str
    probe_serial: Optional[str] = None

class GatewayLeaseStore:
    def read(self) -> Optional[GatewayLease]:
        if not self.path.exists():
            return None
        return GatewayLease.from_record(json.loads(self.path.read_text(encoding="utf-8")))

    def write(self, lease: GatewayLease) -> None:
        atomic_private_json_write(self.path, lease.to_record())

    def clear_if_generation(self, generation: int) -> bool:
        current = self.read()
        if current is None or current.generation != generation:
            return False
        self.path.unlink()
        return True
```

Validate exact primitive types, bounded identifiers/labels, allowed states,
positive finite deadlines and owner/state consistency. Create files with private
permissions where supported and fsync before atomic replacement.

- [ ] **Step 4: Run Task 1 tests and core serialization regressions**

Run: `python scripts/run_unittest_module.py tests.test_gateway_lease tests.test_gateway_status tests.test_device_state`

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 1**

```text
git add b300_core/gateway_lease.py tests/test_gateway_lease.py
git commit -m "feat: define exclusive gateway lease contract"
```

### Task 2: Exclusive coordinator and deterministic cleanup

**Files:**
- Create: `b300_core/gateway_lease_coordinator.py`
- Modify: `b300_core/gateway_supervisor.py`
- Test: `tests/test_gateway_lease_coordinator.py`
- Modify tests: `tests/test_gateway_supervisor.py`

**Interfaces:**
- Consumes Task 1 lease types and existing `GatewaySupervisor.ensure()`, `rescan()`, `stop()` and `snapshot`.
- Produces `GatewayLeaseCoordinator.acquire(request)`, `renew(lease_id, token, generation)`, `release(lease_id, token, generation)`, `tick()` and `public_snapshot()`; all methods except `acquire()` return a redacted `GatewayLeasePublicSnapshot`.
- Produces reason codes `GATEWAY_BUSY`, `LEASE_INVALID`, `LEASE_EXPIRED`, `LEASE_GRACE`, `CLEANUP_IN_PROGRESS`, `DEVICE_BUSY` and `DEBUG_PORT_BUSY`.

- [ ] **Step 1: Write failing exclusivity and repeated-cycle tests**

```python
def test_two_clients_racing_acquire_produce_one_grant_and_one_busy():
    results = run_concurrently(
        lambda: coordinator.acquire(request("client-a", "VSCODE_DEBUG")),
        lambda: coordinator.acquire(request("client-b", "LIVE_WATCH")),
    )
    self.assertEqual(sum(isinstance(item, GatewayLeaseGrant) for item in results), 1)
    self.assertEqual(sum(isinstance(item, GatewayLeaseBusy) for item in results), 1)
    self.assertEqual(supervisor.ensure_calls, 1)

def test_start_stop_start_uses_new_generation_and_stale_stop_is_harmless():
    first = coordinator.acquire(request("client-a", "VSCODE_DEBUG"))
    coordinator.release(first.lease_id, first.token, first.generation)
    second = coordinator.acquire(request("client-a", "VSCODE_DEBUG"))
    stale = coordinator.release(first.lease_id, first.token, first.generation)
    self.assertGreater(second.generation, first.generation)
    self.assertEqual(stale.reason_code, "LEASE_INVALID")
    self.assertTrue(coordinator.public_snapshot().active)
```

Add fake-clock cases for heartbeat, TTL, grace, owner reconnect, explicit release,
target initially running/halted, cleanup failure, OpenOCD crash and ST-Link
removal/reinsert after lease expiry. The OpenOCD crash case asserts READY is
revoked immediately and permits at most one restart while the same lease and
probe generation remain valid.

- [ ] **Step 2: Run Task 2 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_lease_coordinator tests.test_gateway_supervisor`

Expected: coordinator import/API failures while supervisor regressions remain green.

- [ ] **Step 3: Implement coordinator state transitions**

```python
class GatewayLeaseCoordinator:
    def acquire(self, request: GatewayLeaseRequest) -> Union[GatewayLeaseGrant, GatewayLeaseBusy]:
        with self._lock:
            if self._lease is not None:
                return GatewayLeaseBusy.from_lease(self._lease, self._clock())
            return self._reserve_start_and_publish(request)

    def renew(self, lease_id: str, token: str, generation: int) -> GatewayLeasePublicSnapshot:
        with self._lock:
            lease = self._require_owner(lease_id, token, generation)
            updated = self._replace_deadline(lease, self._clock() + self.policy.lease_ttl_seconds)
            return GatewayLeasePublicSnapshot.from_lease(updated, self._clock())

    def release(self, lease_id: str, token: str, generation: int) -> GatewayLeasePublicSnapshot:
        with self._lock:
            lease = self._require_owner(lease_id, token, generation)
            cleaned = self._cleanup_owned(lease, "CLIENT_RELEASED")
            return GatewayLeasePublicSnapshot.from_lease(cleaned, self._clock())

    def tick(self) -> GatewayLeasePublicSnapshot:
        with self._lock:
            return self._advance_deadlines_and_health()

    def public_snapshot(self) -> GatewayLeasePublicSnapshot:
        with self._lock:
            return GatewayLeasePublicSnapshot.from_lease(self._lease, self._clock())
```

Serialize all mutations under one lock. Call `GatewaySupervisor.ensure()` only
after ownership is reserved; on failure, run owned cleanup before clearing the
reservation. `tick()` enters grace after TTL and invokes target restore plus
`supervisor.stop()` after grace. Change `maintain_once()` so an idle coordinator
cannot reopen OpenOCD merely because ST-Link was reinserted.

- [ ] **Step 4: Prove GREEN and existing hardware lifecycle compatibility**

Run: `python scripts/run_unittest_module.py tests.test_gateway_lease_coordinator tests.test_gateway_supervisor tests.test_remote_debug_guard tests.test_debug_service tests.test_hardware_session`

Expected: all selected tests pass; fake services record no flash/reset command.

- [ ] **Step 5: Commit Task 2**

```text
git add b300_core/gateway_lease_coordinator.py b300_core/gateway_supervisor.py tests/test_gateway_lease_coordinator.py tests/test_gateway_supervisor.py
git commit -m "feat: enforce one remote gateway owner"
```

### Task 3: Same-user Gateway Agent and request queue

**Files:**
- Create: `b300_core/gateway_agent.py`
- Create: `b300_core/gateway_agent_protocol.py`
- Modify: `b300_core/gateway_supervisor.py`
- Modify: `b300_cli/parser.py`
- Modify: `b300_stlink.py`
- Test: `tests/test_gateway_agent.py`
- Modify tests: `tests/test_gateway_protocol.py`

**Interfaces:**
- Consumes Task 2 coordinator.
- Produces `GatewayAgentSnapshot`, `GatewayRequest`, `GatewayResponse`, `GatewayAgent.run()`, `GatewayAgentProcessManager.ensure_running()`, `GatewayRequestStore.submit()` and private request operations `status`, `acquire`, `renew`, `release`, `rescan`, `shutdown`.
- CLI modes become `gateway-agent`, `gateway-agent-status`, `gateway-acquire`, `gateway-renew`, `gateway-release`; `--managed-child` remains hidden.

- [ ] **Step 1: Write failing agent protocol tests**

```python
def test_agent_idle_does_not_discover_probe_or_start_openocd():
    agent.run_once()
    self.assertEqual(probe_discovery.calls, [])
    self.assertEqual(supervisor.ensure_calls, 0)
    self.assertEqual(agent.snapshot.state, "IDLE")

def test_request_is_processed_once_and_replay_is_rejected():
    response = request_store.submit(valid_acquire_request())
    replay = request_store.submit(valid_acquire_request())
    self.assertEqual(response.status, "ok")
    self.assertEqual(replay.reason_code, "REQUEST_REPLAYED")

def test_corrupt_or_oversized_request_never_reaches_coordinator():
    write_request_bytes(b"{" + b"x" * (MAX_REQUEST_BYTES + 1))
    agent.run_once()
    self.assertEqual(coordinator.calls, [])
```

Include detached process idempotency, stale heartbeat, corrupt persisted state,
shutdown cleanup, private-path validation and no unknown-PID termination cases.

- [ ] **Step 2: Run Task 3 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_agent tests.test_gateway_protocol`

Expected: new module/command failures.

- [ ] **Step 3: Implement the idle agent and bounded file protocol**

```python
class GatewayRequestStore:
    def submit(self, operation: str, payload: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]:
        request = GatewayRequest.create(operation, payload, timeout_seconds)
        self._write_request_exclusive(request)
        return self._wait_for_bounded_response(request)

    def pending(self) -> Sequence[GatewayRequest]:
        return tuple(self._read_valid_request(path) for path in self._bounded_request_paths())

    def respond(self, request_id: str, record: Mapping[str, object]) -> None:
        self._write_response_atomic(request_id, validate_response_record(record))

class GatewayAgent:
    def run_once(self) -> GatewayAgentSnapshot:
        for request in self.requests.pending():
            self._dispatch_once(request)
        return self.coordinator.tick()

    def run(self, stop_event: Optional[threading.Event] = None) -> int:
        event = stop_event or threading.Event()
        while not event.wait(self._next_wait_seconds()):
            self.run_once()
        self.coordinator.shutdown("AGENT_SHUTDOWN")
        return 0
```

Use exact schemas, 64 KiB request/response bounds, request expiry and atomic
create/replace. Agent idle waits on a bounded poll interval without probing
hardware. The process manager verifies status heartbeat and B300 ownership before
starting another child.

- [ ] **Step 4: Run agent and protocol regressions**

Run: `python scripts/run_unittest_module.py tests.test_gateway_agent tests.test_gateway_protocol tests.test_gateway_remote_ensure tests.test_process_startup`

Expected: all selected tests pass and subprocess calls use `shell=False`.

- [ ] **Step 5: Commit Task 3**

```text
git add b300_core/gateway_agent.py b300_core/gateway_agent_protocol.py b300_core/gateway_supervisor.py b300_cli/parser.py b300_stlink.py tests/test_gateway_agent.py tests/test_gateway_protocol.py
git commit -m "feat: add sleeping gateway agent control plane"
```

### Task 4: Gateway autostart setup and lifecycle diagnostics

**Files:**
- Create: `b300_core/gateway_agent_setup.py`
- Modify: `b300_core/gateway_setup.py`
- Modify: `b300_cli/gateway_workflows.py`
- Modify: `b300_gui/gateway_setup_tab.py`
- Test: `tests/test_gateway_agent_setup.py`
- Modify tests: `tests/test_gateway_setup.py`, `tests/test_cli_gateway_setup.py`, `tests/test_gateway_setup_tab.py`

**Interfaces:**
- Produces `GatewayAgentSetupReport`, `GatewayAgentSetupPlan`, `GatewayAgentSetupResult`, `inspect_gateway_agent_setup()`, `build_gateway_agent_setup_plan()` and `prepare_gateway_agent_setup()`.
- Windows owns exactly one scheduled task `B300-STLink-GatewayAgent`; Linux owns exactly one systemd-user unit `b300-stlink-gateway-agent.service`.
- `gateway quickstart --confirm-system-change` composes SSH preparation and Agent preparation without storing a password.

- [ ] **Step 1: Write failing platform setup tests**

```python
def test_windows_plan_uses_exact_cli_path_hidden_on_logon_without_password():
    plan = build_gateway_agent_setup_plan(report_missing(), system_name="Windows", cli_path=CLI)
    command = " ".join(plan.commands)
    self.assertIn("B300-STLink-GatewayAgent", command)
    self.assertIn(str(CLI), command)
    self.assertNotIn("/RP", command.upper())
    self.assertNotIn("password", command.lower())

def test_linux_unit_is_user_scoped_and_never_uses_sudo_or_linger():
    plan = build_gateway_agent_setup_plan(report_missing(), system_name="Linux", cli_path=CLI)
    command = " ".join(plan.commands)
    self.assertIn("systemctl --user", command)
    self.assertNotIn("sudo", command)
    self.assertNotIn("enable-linger", command)
```

Test exact ownership-safe update/removal, active-lease refusal, unsupported init,
already-ready no-op and GUI state separation between SSH and Agent.

- [ ] **Step 2: Run Task 4 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_agent_setup tests.test_gateway_setup tests.test_cli_gateway_setup tests.test_gateway_setup_tab`

Expected: missing setup contract and Agent fields.

- [ ] **Step 3: Implement idempotent autostart inspection/preparation**

```python
@dataclass(frozen=True)
class GatewayAgentSetupReport:
    supported: bool
    installed: bool
    running: bool
    version: Optional[str]
    autostart_enabled: bool
    reason_code: str

def prepare_gateway_agent_setup(*, cli_path: Path, system_name: Optional[str] = None,
                                runner: CommandRunner = _run) -> GatewayAgentSetupResult:
    before = inspect_gateway_agent_setup(cli_path=cli_path, system_name=system_name, runner=runner)
    plan = build_gateway_agent_setup_plan(before, cli_path=cli_path, system_name=system_name)
    for command in plan.commands:
        require_success(runner(command))
    after = inspect_gateway_agent_setup(cli_path=cli_path, system_name=system_name, runner=runner)
    return GatewayAgentSetupResult(before=before, plan=plan, after=after)
```

Build commands as argv or controlled script literals from the exact resolved CLI
path. Do not interpolate user-controlled command text. Verify the created entry
and Agent version after preparation; fallback on-demand ensure remains functional
when autostart is unavailable.

- [ ] **Step 4: Run setup and GUI regressions**

Run: `python scripts/run_unittest_module.py tests.test_gateway_agent_setup tests.test_gateway_setup tests.test_cli_gateway_setup tests.test_gateway_setup_tab tests.test_ssh_identity`

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 4**

```text
git add b300_core/gateway_agent_setup.py b300_core/gateway_setup.py b300_cli/gateway_workflows.py b300_gui/gateway_setup_tab.py tests/test_gateway_agent_setup.py tests/test_gateway_setup.py tests/test_cli_gateway_setup.py tests/test_gateway_setup_tab.py
git commit -m "feat: configure gateway agent autostart"
```

### Task 5: Remote lease transport and heartbeat controller

**Files:**
- Modify: `b300_core/gateway_protocol.py`
- Modify: `b300_core/remote_session.py`
- Modify: `b300_core/gateway_client.py`
- Modify: `b300_core/gateway_sessions.py`
- Create: `b300_core/gateway_lease_client.py`
- Test: `tests/test_gateway_lease_client.py`
- Modify tests: `tests/test_gateway_remote_ensure.py`, `tests/test_gateway_client.py`, `tests/test_shared_profiles.py`

**Interfaces:**
- Produces `RemoteLeaseGrant`, `GatewayBusyError`, fixed remote CLI commands for acquire/renew/release/agent-status and capability `gateway-exclusive-lease-v1`.
- `RemoteGatewaySession.acquire_gateway(mode, client_id, client_label, probe_serial)` returns a private `RemoteLeaseGrant`; renew/release require its exact token and generation.
- `GatewayLeaseClient` owns heartbeat thread cancellation and an idempotent `close()`.

- [ ] **Step 1: Write failing remote lifecycle tests**

```python
def test_client_acquires_before_opening_forward_and_releases_after_close():
    lease = client.start("VSCODE_DEBUG")
    self.assertEqual(session.calls[:2], ["agent-ensure", "acquire"])
    client.open_debug_forward(lease)
    client.close()
    self.assertEqual(session.calls[-1], "release")

def test_busy_owner_is_reported_and_no_forward_is_opened():
    session.acquire_result = busy("ENG-LAPTOP-02", "VSCODE_DEBUG", age=3)
    with self.assertRaisesRegex(GatewayBusyError, "ENG-LAPTOP-02"):
        client.start("LIVE_WATCH")
    self.assertEqual(session.opened_forwards, [])
```

Add fake-clock/fake-thread tests for renewal failure, SSH loss, close during
acquire, close twice, late renew, generation change and Client restart.

- [ ] **Step 2: Run Task 5 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_lease_client tests.test_gateway_remote_ensure tests.test_gateway_client`

Expected: lease transport and Client controller API failures.

- [ ] **Step 3: Implement strict remote commands and heartbeat ownership**

```python
class GatewayLeaseClient:
    def start(self, mode: str, probe_serial: Optional[str] = None) -> RemoteLeaseGrant:
        self.session.ensure_gateway_agent()
        self._grant = self.session.acquire_gateway(self._request(mode, probe_serial))
        self._start_heartbeat()
        return self._grant

    def renew_once(self) -> GatewayLeasePublicSnapshot:
        return self.session.renew_gateway(self._require_grant())

    def close(self) -> None:
        grant = self._take_grant()
        self._stop_heartbeat()
        if grant is not None:
            self.session.release_gateway(grant)

class RemoteGatewaySession:
    def acquire_gateway(self, request: GatewayLeaseRequest, *, timeout_seconds: float = 15.0) -> RemoteLeaseGrant:
        return self._run_gateway_control("acquire", request.to_control_record(), timeout_seconds)

    def renew_gateway(self, grant: RemoteLeaseGrant, *, timeout_seconds: float = 5.0) -> GatewayLeasePublicSnapshot:
        return self._run_gateway_control("renew", grant.to_private_control_record(), timeout_seconds)

    def release_gateway(self, grant: RemoteLeaseGrant, *, timeout_seconds: float = 5.0) -> GatewayLeasePublicSnapshot:
        return self._run_gateway_control("release", grant.to_private_control_record(), timeout_seconds)
```

Keep command selection fixed and encoded as bounded JSON/base64 data rather than
shell interpolation. Never log or include the raw token in snapshots/exceptions.
Stop heartbeat before release and invalidate local forwards immediately when a
renewal fails.

- [ ] **Step 4: Run remote and SSH security regressions**

Run: `python scripts/run_unittest_module.py tests.test_gateway_lease_client tests.test_gateway_remote_ensure tests.test_gateway_client tests.test_remote_session tests.test_ssh_host_trust tests.test_ssh_identity`

Expected: all selected tests pass; malformed protocol and unsafe command tests remain rejected.

- [ ] **Step 5: Commit Task 5**

```text
git add b300_core/gateway_protocol.py b300_core/remote_session.py b300_core/gateway_client.py b300_core/gateway_sessions.py b300_core/gateway_lease_client.py tests/test_gateway_lease_client.py tests/test_gateway_remote_ensure.py tests/test_gateway_client.py tests/test_shared_profiles.py
git commit -m "feat: manage remote gateway lease heartbeats"
```

### Task 6: Monitor and VS Code exclusive-owner UX

**Files:**
- Modify: `b300_gui/live_monitor_controller.py`
- Modify: `b300_gui/vscode_debug_controller.py`
- Modify: `b300_core/live_session.py`
- Modify: `b300_core/vscode_bridge.py`
- Modify: `b300_gui/views/monitor_view.py`
- Modify: `b300_gui/views/debug_vscode_view.py`
- Modify: `b300_gui/main_window.py`
- Test: `tests/test_gateway_exclusive_ui.py`
- Modify tests: `tests/test_live_monitor_controller.py`, `tests/test_v018_vscode_controller.py`, `tests/test_v018_vscode_bridge.py`, `tests/test_gui_interlocks.py`

**Interfaces:**
- Monitor acquires mode `LIVE_WATCH`; VS Code acquires `VSCODE_DEBUG`.
- Both render `GatewayBusyViewModel(client_label, mode_label, started_at, heartbeat_age_seconds)`.
- Start is coalesced/disabled while acquiring or cleaning; Stop cancels startup or releases the active lease; a new Start waits for cleanup generation completion.

- [ ] **Step 1: Write failing GUI/controller behavior tests**

```python
def test_vscode_busy_warning_names_other_client_and_does_not_open_vscode():
    lease_client.start_result = GatewayLeaseBusy("ENG-LAPTOP-02", "VSCODE_DEBUG", 3)
    controller.start(workspace, axf)
    self.assertIn("ENG-LAPTOP-02", view.busy_banner.text())
    self.assertFalse(view.start_button.isEnabled())
    vscode_launcher.assert_not_called()

def test_monitor_stop_then_immediate_start_waits_for_cleanup_and_uses_new_lease():
    first = controller.start_monitor()
    controller.stop_monitor()
    second = controller.start_monitor()
    self.assertNotEqual(first.generation, second.generation)
    self.assertEqual(lease_client.max_parallel_active, 1)
```

Cover close/crash callbacks, renewal loss, busy refresh, profile change, Gateway
restart, no duplicate threads/tunnels and cleanup after launch.json failure.

- [ ] **Step 2: Run Task 6 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_exclusive_ui tests.test_live_monitor_controller tests.test_v018_vscode_controller`

Expected: busy view model and lease integration failures.

- [ ] **Step 3: Integrate one lease controller with each product flow**

```python
def _format_gateway_busy(busy: GatewayLeaseBusy) -> str:
    return (
        "Gateway đang được sử dụng bởi %s (%s). Heartbeat gần nhất: %d giây trước."
        % (busy.client_label, mode_label(busy.mode), busy.heartbeat_age_seconds)
    )
```

Acquire before any tunnel or VS Code launch. Release on every failure branch.
Keep the token only inside `GatewayLeaseClient`; UI receives public snapshots.
Use generation guards so late workers cannot reopen buttons, tunnels or an old
`launch.json` binding.

- [ ] **Step 4: Run GUI, Monitor and VS Code regressions**

Run: `python scripts/run_unittest_module.py tests.test_gateway_exclusive_ui tests.test_live_monitor_controller tests.test_v018_vscode_controller tests.test_v018_vscode_bridge tests.test_gui_interlocks tests.test_gateway_health_ui`

Expected: all selected tests pass without visible subprocess windows.

- [ ] **Step 5: Commit Task 6**

```text
git add b300_gui/live_monitor_controller.py b300_gui/vscode_debug_controller.py b300_core/live_session.py b300_core/vscode_bridge.py b300_gui/views/monitor_view.py b300_gui/views/debug_vscode_view.py b300_gui/main_window.py tests/test_gateway_exclusive_ui.py tests/test_live_monitor_controller.py tests/test_v018_vscode_controller.py tests/test_v018_vscode_bridge.py tests/test_gui_interlocks.py
git commit -m "feat: show exclusive gateway ownership in clients"
```

### Task 7: Diagnostics, packaging and operator documentation

**Files:**
- Modify: `b300_core/support_bundle.py`
- Modify: `build_native_bundle.py`
- Modify: `package_internal.py`
- Modify: `packaging/windows/b300-stlink-gui.iss`
- Modify: `docs/04_DEBUG.md`
- Modify: `docs/05_TROUBLESHOOTING.md`
- Modify: `README.md`
- Test: `tests/test_gateway_support_evidence.py`
- Modify tests: `tests/test_support_bundle.py`, `tests/test_build_native_bundle.py`, `tests/test_gui_packaging.py`

**Interfaces:**
- Support bundle adds bounded `gateway_agent` and `gateway_lease` public evidence while redacting tokens, credentials, raw commands and user-bearing paths.
- Native bundles include the Agent entry point and autostart setup uses the installed same-version CLI path.
- Documentation defines setup-once, wake, busy warning, Stop, crash expiry and recovery commands.

- [ ] **Step 1: Write failing redaction and bundle identity tests**

```python
def test_support_bundle_contains_public_owner_evidence_without_token_or_password():
    bundle = create_bundle(evidence=lease_evidence(token="secret", password="pw"))
    text = read_bundle_text(bundle)
    self.assertIn('"reason_code": "GATEWAY_BUSY"', text)
    self.assertIn("ENG-LAPTOP-02", text)
    self.assertNotIn("secret", text)
    self.assertNotIn("pw", text)

def test_packaged_cli_exposes_same_version_gateway_agent_commands():
    result = run_packaged_cli("debug", "gateway-agent-status", "--json")
    self.assertEqual(result.record["tool_version"], VERSION)
```

- [ ] **Step 2: Run Task 7 tests and verify RED**

Run: `python scripts/run_unittest_module.py tests.test_gateway_support_evidence tests.test_support_bundle tests.test_build_native_bundle tests.test_gui_packaging`

Expected: missing evidence/packaging assertions.

- [ ] **Step 3: Add bounded evidence, package entry points and exact workflows**

Document these operator commands and outcomes:

```text
b300-stlink gateway quickstart --confirm-system-change
b300-stlink debug gateway-agent-status --json
b300-stlink debug gateway-acquire --mode VSCODE_DEBUG --json
b300-stlink debug gateway-release --json
```

Normal GUI users do not copy tokens or run acquire/release manually; Tools owns
them. Troubleshooting maps every new reason code to a safe next action.

- [ ] **Step 4: Run documentation/packaging regressions and static checks**

Run: `python scripts/run_unittest_module.py tests.test_gateway_support_evidence tests.test_support_bundle tests.test_build_native_bundle tests.test_gui_packaging tests.test_release_metadata tests.test_release_manifest`

Run: `python -m compileall -q b300_core b300_cli b300_gui tests`

Run: `git diff --check`

Expected: all commands exit zero.

- [ ] **Step 5: Commit Task 7**

```text
git add b300_core/support_bundle.py build_native_bundle.py package_internal.py packaging/windows/b300-stlink-gui.iss docs/04_DEBUG.md docs/05_TROUBLESHOOTING.md README.md tests/test_gateway_support_evidence.py tests/test_support_bundle.py tests/test_build_native_bundle.py tests/test_gui_packaging.py
git commit -m "docs: package and explain gateway lease lifecycle"
```

### Task 8: Full verification, review and SemVer release

**Files:**
- Modify: `b300_version.py`
- Modify: `CHANGELOG.md`
- Create: `docs/releases/0.23.0.md`

**Interfaces:**
- Release version is `0.23.0` because remote Gateway behavior and CLI protocol change materially.
- Release notes distinguish software/mock validation from deferred direct hardware acceptance.

- [ ] **Step 1: Bump the authoritative version and release notes**

Change only `b300_version.__version__` from `0.22.0` to `0.23.0`, move the
completed Gateway lease entries from `Unreleased` into a dated `0.23.0` section
in `CHANGELOG.md`, and add `docs/releases/0.23.0.md`. Package modules continue to
read the authoritative source version. Do not hand-edit generated update
manifests.

Run: `python scripts/run_unittest_module.py tests.test_v015_release_ux tests.test_build_native_bundle tests.test_cli_version_probes tests.test_release_version_tools tests.test_gateway_protocol`

- [ ] **Step 2: Run one complete source verification on the final tree**

Run: `python -m unittest discover -s tests -q`

Run: `python -m compileall -q b300_core b300_cli b300_gui tests`

Run: `git diff --check`

Expected: zero failures and only documented platform skips.

- [ ] **Step 3: Build and verify native packages without hardware or flashing**

Run: `python build_native_bundle.py --internal-distribution-approved`

Run the existing Windows installer verifier and CLI/GUI smoke commands against
the generated `0.23.0` artifacts. Verify GUI and companion CLI report the same
version and preserve hashed user profiles/credentials across the upgrade fixture.

- [ ] **Step 4: Dispatch independent whole-branch review and resolve findings**

Review the diff from baseline `c9bb852` through the final candidate against the
Spec, emphasizing lease races, stale release protection, secret redaction,
unknown-process safety, loopback binding and installer ownership. Fix every
Critical or Important finding with a regression test and scoped re-review.

- [ ] **Step 5: Commit the release candidate**

```text
git add b300_version.py CHANGELOG.md docs/releases/0.23.0.md
git commit -m "release: prepare B300 ST-Link Tools 0.23.0"
```

- [ ] **Step 6: Integrate and publish only after exact-SHA CI**

Push the reviewed branch/main according to the repository's existing release
workflow. Watch one canonical CI run for the exact commit on Windows x64, Ubuntu
x64 and Ubuntu ARM64. After all three pass, create the single tag `v0.23.0`, watch
the release workflow, verify the public non-draft/non-prerelease Latest release,
verify signed manifests and asset SHA-256 values, then install the exact public
Windows installer locally and re-check GUI/CLI versions and preserved user data.

- [ ] **Step 7: Record final evidence honestly**

Report branch, exact SHA, commits, clean-tree state, test counts, package smoke,
CI URLs, tag, public release URL, signatures, local install identity and all
remaining limitations. Keep `HW-P1-001` as `OPEN / DEFERRED`; state that IPC and
physical disconnect scenarios were not exercised unless separately authorized.
