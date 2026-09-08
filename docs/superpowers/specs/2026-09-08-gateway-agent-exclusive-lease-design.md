# B300 Gateway Agent and Exclusive Debug Lease Design

**Status:** Proposed for user review

**Baseline:** B300 ST-Link Tools v0.22.0 (`c9bb852`)

**EAS profile:** Engineering Agent Stack v0.6.2, `embedded` preset with
`embedded.realtime-budget` and `embedded.validation-evidence`; use the `release`
preset only for the later packaging and publication gate.

## Goal

Make a machine prepared as a B300 Gateway continuously available to authenticated
B300 Clients without continuously owning ST-Link or running OpenOCD. The first
Client that requests remote Live Watch or VS Code Debug receives one exclusive,
time-bounded lease. Other Clients receive a clear busy warning and cannot debug
in parallel. When the owner stops, crashes, sleeps, loses SSH/network, or stops
renewing its lease, the Gateway restores the target's original run state where
safe, closes OpenOCD, releases the ports and ST-Link, and returns to an idle state
ready for the next Client.

The design must make repeated `start -> stop -> start` cycles deterministic and
must not flash firmware, reset the target implicitly, weaken Bootloader/Option
Bytes policy, expose debug ports outside loopback, use `sudo b300-stlink`, log
credentials, or terminate processes not owned by B300.

## Selected approach

Use a small unprivileged per-user Gateway Agent plus an SSH-invoked CLI control
plane. `gateway quickstart --confirm-system-change` installs or refreshes the
agent's autostart entry after it prepares SSH. The agent starts with the operating
system where supported, but remains idle: it does not create `DebugService`, open
GDB/TCL listeners, or acquire `HardwareSession` until a valid lease is granted.

Every control operation still enters through the existing authenticated SSH
session and executes the installed B300 CLI as the SSH user. The CLI communicates
with the same-user agent through a private runtime directory and atomic request /
response files. No new TCP control listener is introduced. If the agent did not
start at boot, the first lease request starts it as a detached per-user process,
so headless or partially configured hosts remain usable without weakening the
control boundary.

Each request has a random identifier, creation deadline and exact schema. The
agent processes an identifier at most once, rejects expired/replayed requests and
deletes bounded response artifacts. The same-user runtime directory is the trust
boundary: another operating-system user cannot submit or read requests.

This approach is preferred over using SSH as the only short-lived supervisor
because a resident agent can own lease expiry and cleanup after the SSH process
has disappeared. It is preferred over leaving OpenOCD running because an idle
Gateway then owns neither ST-Link nor ports `3333`/`6666`.

## Scope

This change includes:

- per-user Gateway Agent lifecycle and supported autostart setup;
- one exclusive remote-use lease shared by Live Watch and VS Code Debug;
- lease acquire, renew, release, status and forced-expiry cleanup;
- owner-safe busy reporting without credentials or secrets;
- GDB and monitor activity evidence;
- automatic cleanup after graceful stop and bounded cleanup after abrupt loss;
- deterministic repeated connection and stale-generation handling;
- client, GUI, VS Code bridge, support bundle, packaging and documentation updates;
- software tests on Windows and mocked Linux paths plus canonical CI on Windows
  x64, Ubuntu x64 and Ubuntu ARM64.

This change does not include parallel remote debugging, shared read-only access,
remote CLI installation, remote firmware programming changes, arbitrary process
termination, a public network API, or changes to STM32 firmware.

## Safety invariants

1. At most one active remote lease may own a Gateway at any time, regardless of
   whether its mode is `LIVE_WATCH` or `VSCODE_DEBUG`.
2. `DebugService` and `HardwareSession` may exist only while the current lease is
   valid or during its bounded cleanup grace period.
3. GDB and TCL listeners bind only to `127.0.0.1`; Telnet remains disabled.
4. The Gateway never exposes password, private key, raw client IP, command line,
   AXF path, or arbitrary user text in public status. The busy warning contains a
   sanitized client label, mode, start time and last-heartbeat age only.
5. Only the exact lease token and lease generation that acquired ownership may
   renew or release it. Stale release and heartbeat requests are harmless.
6. A new owner cannot be granted a lease until cleanup of the previous owner is
   proven complete and B300-owned listeners and HardwareSession are released.
7. B300 never kills an unknown PID or a process whose ownership token cannot be
   proven. Port conflicts fail with a bounded diagnostic.
8. On disconnect, a target captured as `running` is resumed if it is found
   halted. A target captured as `halted` is not automatically run. Failure to
   prove the state is reported and never converted to success.
9. Live Watch remains zero-halt/read-only. VS Code Debug is an explicitly
   disruptive operator action and may halt, step, reset or modify runtime state
   through Cortex-Debug, while debug-time flash commands remain disabled.
10. Normal flash safety, Sector 0--2, metadata, WRP, RDP and Factory provisioning
    policy remain unchanged.

## Runtime components

### GatewayAgent

`GatewayAgent` is the single per-user coordinator. It owns the lease store,
`GatewaySupervisor`, cleanup decisions, heartbeat deadlines and the request
queue. The process writes an atomic health snapshot at a bounded cadence and
sleeps between request/health deadlines. Idle CPU use must be negligible and no
hardware probe occurs while the state is `IDLE`.

The agent uses the existing runtime root under
`~/.b300-stlink/gateway-runtime`, strengthened with same-user permissions. Files
are size-bounded, schema-validated and atomically replaced. Corrupt state fails
closed to `RECOVERY_REQUIRED`; it does not grant a new lease over an uncertain
owner.

### GatewayLeaseStore

The lease record is immutable per revision and contains only:

- `schema_version` and protocol version;
- random `lease_id` and unguessable `lease_token` hash;
- monotonically increasing `lease_generation`;
- sanitized `client_id` and `client_label`;
- mode: `LIVE_WATCH` or `VSCODE_DEBUG`;
- `acquired_at`, monotonic deadline data and last-heartbeat age;
- Gateway instance/generation and selected probe identity;
- lifecycle state and bounded reason code.

Wall-clock timestamps are display evidence only. Expiry uses the agent's
monotonic clock, so Client/Gateway clock skew cannot keep a lease alive.

### GatewayControlClient

Short-lived SSH CLI commands enqueue a private request and wait for its matching
bounded response. Required operations are:

- `gateway-agent-status`: read-only agent, lease and hardware state;
- `gateway-acquire`: acquire exclusive ownership and start OpenOCD;
- `gateway-renew`: extend only the matching lease;
- `gateway-release`: gracefully end only the matching lease;
- `gateway-rescan`: request hardware rescan for the current owner;
- `gateway-agent-ensure`: start the lightweight agent if unavailable.

The existing `gateway-status`, `gateway-ensure` and `gateway-rescan` remain as a
compatibility surface. A protocol capability advertises the exclusive-lease
contract. New Clients refuse unsafe fallback to the v0.22 ownership behavior
when exclusive ownership is required.

### Client lease controller

One controller is shared by Monitor and VS Code flows for each Gateway profile.
It creates a fresh client session identity, acquires before opening tunnels,
renews periodically, releases on orderly stop, and invalidates its own tunnel and
UI state immediately after a failed renewal or incompatible Gateway generation.

The client never treats an SSH connection alone as proof that it owns the
Gateway. It must hold a current lease whose Gateway snapshot matches its binding.

## State machine

```text
AGENT_STOPPED
    -> STARTING_AGENT
    -> IDLE

IDLE
    -> ACQUIRING
    -> STARTING_OPENOCD
    -> ACTIVE_LIVE_WATCH | ACTIVE_VSCODE_DEBUG

ACTIVE_*
    -> RELEASING            (explicit stop)
    -> LEASE_GRACE          (heartbeat/transport lost)
    -> RECOVERING_HARDWARE  (probe/target/OpenOCD failure)

LEASE_GRACE
    -> ACTIVE_*             (same valid owner renews before deadline)
    -> CLEANING

RELEASING | RECOVERING_HARDWARE
    -> CLEANING

CLEANING
    -> RESTORING_TARGET
    -> STOPPING_OPENOCD
    -> VERIFYING_RELEASE
    -> IDLE

Any uncertain cleanup
    -> RECOVERY_REQUIRED
    -> IDLE only after ownership is proven clear
```

`IDLE` means no B300 OpenOCD process, no B300 HardwareSession and no B300-owned
GDB/TCL listener. The Agent process itself remains available.

## Timing defaults

- Client heartbeat interval: 5 seconds.
- Lease TTL after the last accepted heartbeat: 20 seconds.
- Reconnect grace after TTL: 10 seconds.
- Maximum ordinary cleanup target: 5 seconds after grace ends.
- Agent request timeout: 15 seconds for startup/rescan and 5 seconds for status,
  renew or release.
- Agent idle health snapshot: every 5 seconds without probing ST-Link.

Therefore an abruptly lost Client normally releases the hardware within 35
seconds. Explicit Stop skips lease expiry and begins cleanup immediately. Values
are constants in one policy object and are covered by fake-clock tests; the first
release does not expose arbitrary timeout tuning in the normal GUI.

## Exclusive ownership and busy warning

Acquisition is serialized by the Gateway Agent. Concurrent requests are ordered
under one transaction lock. Exactly one request may transition from `IDLE` to
`ACQUIRING`; all others receive `GATEWAY_BUSY`.

The warning is actionable and bounded:

```text
Gateway đang được sử dụng bởi ENG-LAPTOP-02 (VS Code Debug).
Bắt đầu: 14:22:08; heartbeat gần nhất: 3 giây trước.
Hãy dừng phiên trên máy đó hoặc đợi Gateway tự giải phóng.
```

The second Client may refresh status or retry after the lease expires. There is
no normal “take over” button. Administrative recovery, if added, must first prove
the old lease expired and B300 ownership is clear; it cannot kill an active
Client or unknown OpenOCD process.

The same Client cannot silently create a second lease. Repeated button presses
while acquiring are coalesced. Stop is idempotent. A start issued during cleanup
waits for the matching cleanup generation and then performs a fresh acquisition;
it never reuses the old tunnel or token.

## Activity and disconnect handling

The Gateway records both ownership and transport evidence:

- GDB connection count and activity generation from OpenOCD logs;
- internal Safe TCL/Live Watch session activity owned by the lease;
- Client heartbeat revision and deadline;
- SSH command success is transport evidence but not lease liveness by itself.

GDB/TCL activity never extends a lease without an authenticated heartbeat. This
prevents an orphaned debugger socket from owning the robot forever. Conversely,
a brief gap in GDB activity does not close a healthy VS Code lease while its
heartbeat continues.

## Failure behavior

| Trigger | Required result |
|---|---|
| Explicit Client Stop | Release immediately, restore target, stop OpenOCD, verify ports/session free, return `IDLE`. |
| Tools or VS Code crash | Heartbeat expires, grace elapses, then the same cleanup sequence runs. |
| Network, SSH or Client power loss | No renewal; bounded expiry and cleanup occur without needing the Client process. |
| Same Client reconnects during grace | Matching identity/token may renew; stale tunnels are replaced using generation checks. |
| Different Client arrives during active/grace | Return `GATEWAY_BUSY`; never start a second OpenOCD. |
| Two Clients acquire simultaneously | One wins atomically; one receives `GATEWAY_BUSY`. |
| Duplicate Start click | Coalesce the in-flight request; do not create another lease or process. |
| Duplicate/late Stop | Idempotent for the current token; stale generation cannot stop the new owner. |
| ST-Link removed | Revoke endpoints immediately, stop the owned OpenOCD, mark owner `RECOVERING_HARDWARE`. |
| ST-Link reinserted while lease valid | Recover only the same selected probe; otherwise require explicit selection/retry. |
| ST-Link reinserted after lease expiry | Remain `IDLE`; do not reacquire hardware without a new Client request. |
| Multiple probes or ambiguous identity | Fail closed with `PROBE_SELECTION_REQUIRED`. |
| ST-Link owned by another process | Return `DEVICE_BUSY`; do not terminate it. |
| Target power/SWD/libusb failure | Revoke READY, perform owned cleanup and return a precise reason code. |
| OpenOCD exits | Revoke endpoints; at most one bounded restart while the lease and probe identity remain valid, otherwise clean up. |
| Port conflict | Return `DEBUG_PORT_BUSY`; do not kill the listener. A later phase may allocate a different loopback port before publishing the snapshot. |
| Gateway Agent restart | Invalidate the old instance/generation; reconcile only B300-owned recorded PID/token and never trust stale READY. |
| Host reboot | Agent returns to `IDLE`; old leases are expired and no debug session resumes automatically. |
| Corrupt lease/status file | Enter `RECOVERY_REQUIRED`; do not grant ownership until B300 ownership is proven clear. |
| CLI/protocol mismatch | Refuse acquire and direct the Client to update the Gateway CLI. |
| Authentication/host-key failure | No request reaches the Agent and no hardware process starts. |

## Autostart and installation

`gateway quickstart --confirm-system-change` remains idempotent and gains an
explicit Gateway Agent setup step:

- Windows: install a hidden per-user Scheduled Task for logon startup, using the
  exact managed CLI path and no stored password. The SSH-invoked ensure path is
  the fallback before or without an interactive logon.
- Linux: install an unprivileged systemd user unit when the user manager is
  available. Do not enable linger automatically and do not use `sudo
  b300-stlink`. SSH-invoked ensure remains the fallback.
- Unsupported init systems: report `AUTOSTART_UNAVAILABLE` while preserving the
  functional on-demand ensure path.

Setup records and verifies the exact executable/version. An application update
refreshes the autostart command only after the new CLI passes its signed update
and runtime checks. Update, uninstall and autostart replacement refuse while a
lease is active. Uninstall removes only the exact B300-owned task/unit. A local
operator-requested Agent shutdown uses the normal restore/cleanup sequence before
the process exits.

## GUI and CLI behavior

The Gateway setup page shows separate facts:

- SSH startup readiness;
- Gateway Agent installed/running/version;
- Agent state (`IDLE`, active mode, grace, cleanup or recovery);
- current owner-safe label and lease age;
- ST-Link/OpenOCD ownership.

Client Debug and Monitor buttons use one state model. While another owner is
active, Start is disabled and the busy banner is visible. The Client automatically
refreshes after expiry/release. During local acquisition, repeated Start is
disabled/coalesced; Stop remains available during startup and cleanup.

VS Code is opened only after acquire, OpenOCD readiness, SSH forward and managed
`launch.json` update all match the same Gateway/lease generation. If any later
step fails, the Client releases the lease. Closing or losing Cortex-Debug does not
depend solely on GDB logs; the Client controller also releases or stops renewing.

CLI JSON uses stable reason codes and never prints the lease token. Human output
identifies the owning Client only through its sanitized label.

## Compatibility

The Gateway protocol version and advertised capabilities are authoritative. An
old Client may invoke a legacy status command, but it cannot safely manage the
new lease protocol and cannot acquire through legacy `gateway-ensure`. A new
Client connecting to an old Gateway reports `CLI_TOO_OLD` before opening VS Code
or Monitor. A new Gateway keeps read-only legacy status commands, while every
start path, including legacy `gateway-ensure`, goes through the exclusive lease
gate and cannot bypass an active owner.

GUI and CLI must come from the same release version. The Windows GUI installer
continues to install its same-version CLI companion; Linux packages carry the
same identity. Update manifests and signatures remain unchanged in trust model.

## Observability and support evidence

Support bundles include bounded, redacted evidence for:

- Agent version/instance/state and last health age;
- lease mode/generation, sanitized client label and heartbeat age;
- acquire/renew/release/expiry transitions and reason codes;
- OpenOCD owned PID identity, loopback endpoints and cleanup result;
- selected probe identity/generation and hardware fault reason;
- Client binding/tunnel generation and last recovery result.

They exclude lease tokens, passwords, keys, raw SSH commands, full paths that may
contain user names, arbitrary process lists and unbounded logs.

## Testing strategy

All lifecycle tests use fake clocks, fake processes and fake SSH transports; they
must not sleep for real deadlines, access IPC `10.1.200.208`, flash firmware or
touch attached hardware.

Required test groups:

1. Lease model validation, atomic acquire and busy reporting.
2. Heartbeat, TTL, grace and monotonic-clock expiry.
3. Explicit release and repeated start/stop/start with stale token/generation.
4. Simultaneous Clients, duplicate clicks and acquire-vs-release races.
5. Tools/VS Code crash, SSH loss, laptop sleep and delayed messages.
6. GDB attach/detach plus Live Watch activity without false lease extension.
7. ST-Link removal/reinsert, wrong probe, ambiguous probe and external ownership.
8. OpenOCD failure, cleanup timeout, corrupt state, agent restart and host reboot.
9. Windows task and Linux systemd-user setup/inspect/remove with command runners;
   no real privileged mutation in unit tests.
10. GUI busy banner, disabled actions, cleanup progress and reconnect behavior.
11. Protocol compatibility, bounded JSON, redaction and support bundle evidence.
12. Packaging of the agent entry point and same-version CLI/GUI identity.

Each implementation task follows RED -> minimal implementation -> GREEN ->
focused regression -> independent review. Final verification includes full unit
discovery, compileall, diff check, native bundle/package smoke and canonical CI on
Windows x64, Ubuntu x64 and Ubuntu ARM64.

## Release gate

This is a material public behavior change and therefore requires a SemVer minor
release after implementation and validation. Publication is allowed only when:

```text
DESIGN APPROVED
-> PLAN COMPLETE
-> LEASE/RACE TESTS PASS
-> CLIENT/GUI/VSCODE TESTS PASS
-> AUTOSTART/PACKAGING TESTS PASS
-> FULL REGRESSION PASS
-> INDEPENDENT REVIEW CLEAR
-> WINDOWS + UBUNTU X64 + UBUNTU ARM64 CI PASS
-> SIGNED RELEASE ASSETS VERIFIED
```

Hardware acceptance remains separate. `HW-P1-001` stays `OPEN / DEFERRED` until
direct evidence closes it. No test or release action in this change may access the
user's IPC or flash a board without a separate explicit instruction.
