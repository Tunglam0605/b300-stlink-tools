# Remote Application Programming Through the Managed Gateway

**Date:** 2026-09-23
**Status:** Approved architecture; implementation planning pending
**Selected approach:** Client uploads over the existing authenticated SSH session and the per-user Gateway Agent owns a two-phase, exclusive Application programming transaction.

## 1. Purpose

Complete the existing remote-programming foundation so a Windows or Linux Client can select an Application Intel HEX in the production GUI or CLI, transfer it to a B300 Gateway, review a fresh Gateway-side dry-run, explicitly approve the exact immutable artifact and target, and let the Gateway safely program the attached STM32F407 through the canonical B300 flash transaction.

The upgrade must also harden the current Gateway lifecycle, fix the confirmed startup deadlock, preserve all existing Monitor and Debug workflows, and fail closed across concurrency, disconnects, process restarts, incompatible versions, and artifact mutation.

## 2. User decision

Normal remote Application programming uses Client-side confirmation only. The Gateway may remain headless and does not require a second physical confirmation.

The confirmation is valid only for one short-lived approval record bound to:

- the exact firmware SHA-256, size, and basename;
- the parsed canonical Application image identity;
- the selected physical probe;
- the inspected target and protection evidence;
- the exact flash plan;
- the Gateway lease generation;
- the Client identity and request;
- an expiry deadline.

Any change invalidates the approval and requires a new prepare/dry-run cycle.

## 3. Scope

### In scope

- Production GUI Client upload, dry-run, confirmation, progress, reconnect, and final-result display.
- CLI Client remote dry-run and explicitly confirmed remote Application flash.
- Authenticated file transfer over the existing SSH connection/profile.
- A new exclusive `FLASH_APPLICATION` Gateway lease mode.
- Gateway-side staging, manifest verification, preparation, execution, status, cleanup, and bounded audit evidence.
- Reuse of `GatewayProgrammingService` and the canonical `B300Service` Application transaction.
- Cross-process exclusion for local and remote ST-Link owners.
- Repair of the confirmed `GatewaySupervisor.ensure()` readiness deadlock.
- Preservation of GUI Client, CLI Client, VS Code debug, and Live Monitor behavior.
- Unit, integration, packaging, failure-injection, and physical two-machine acceptance coverage.

### Out of scope

- Remote Bootloader or factory provisioning.
- Remote Option Bytes, WRP, or RDP modification.
- Mass erase, chip erase, raw memory writes, arbitrary OpenOCD commands, GDB `load`, or GDB `restore`.
- A new HTTP/HTTPS service or any new LAN-facing port.
- Internet/NAT exposure of GDB or TCL.
- Automatic retry after a destructive flash failure.
- BIN, ELF, or AXF execution as a programming image. They may remain representable by the existing transfer contract, but remote Application execution accepts Intel HEX only.

## 4. Safety invariants

The implementation must preserve these non-negotiable invariants:

1. Bootloader Sectors 0--2 (`0x08000000..0x0800BFFF`) are never erased or programmed by remote Application programming.
2. The canonical plan is exactly:

   ```text
   flash erase_sector 0 3 7
   flash write_image {application.hex}
   verify_image {application.hex}
   metadata_plan: 0x0800C000 / 44 bytes / STLM + VERIFIED
   reset run
   ```

3. Sector 3 metadata is changed only as part of the canonical Application transaction.
4. A real flash starts only after exact Gateway-side target inspection, plan construction, Client approval, and immediate pre-execution artifact revalidation.
5. Multiple probes require an exact `--probe-serial`; no serial is invented from a USB identity.
6. Only one B300 hardware owner exists across local GUI, local CLI, Gateway Agent, Debug, and Monitor processes.
7. A disconnect after destructive execution begins does not cancel or restart the flash.
8. A failure is terminal for that job. A new user action is required after diagnosis.
9. Success requires exact `** Verified OK **`, valid 44-byte AppMeta read-back, reset success, `STLM + CONFIRMED`, PC in `0x08010000..0x0807FFFF`, and `BKP1R == 0`.
10. OpenOCD GDB/TCL listeners remain loopback-only. SSH TCP/22 is the only LAN-facing service used by this feature.

## 5. Selected architecture

### 5.1 Client responsibilities

The Client:

1. Resolves the saved Gateway profile and verifies the pinned SSH host key using the existing trust path.
2. Parses the selected Intel HEX locally with the canonical parser.
3. Creates a `RemoteFirmwareManifest` containing operation, kind, safe basename, size, SHA-256, target, board, and standard privilege.
4. Uploads the bytes over SFTP to a Gateway-created upload slot as a `.part` file.
5. Finalizes the upload atomically through an allowlisted Gateway command.
6. Requests prepare and displays the returned Gateway-side evidence.
7. Requires explicit Client confirmation for real flash.
8. Starts the immutable approved job and polls/reconnects by job ID.
9. Displays progress and the bounded final result without interpreting missing output as success.
10. Requests cleanup after consuming the result; expired artifacts are also cleaned by the Gateway.

The Client never sends an OpenOCD command, flash command sequence, remote shell path chosen by the user, or Bootloader artifact.

### 5.2 Gateway Agent responsibilities

The Gateway Agent is the only remote programming coordinator. It:

- validates protocol and capability compatibility;
- creates private bounded staging slots;
- validates path containment, file type, ownership, size, manifest, and SHA-256;
- atomically promotes a completed upload into content-addressed staging;
- acquires an exclusive `FLASH_APPLICATION` lease without starting the persistent Debug Gateway;
- inspects the target and builds the canonical plan through `GatewayProgrammingService.prepare_application()`;
- returns a short-lived approval record and human-readable plan summary;
- revalidates approval, lease, target binding, file identity, and SHA-256 immediately before execution;
- executes through `GatewayProgrammingService.flash_application()` and `B300Service.flash()`;
- persists a bounded state/result record and append-only job log;
- keeps the job alive if the SSH Client disconnects;
- releases hardware ownership only after the terminal state and cleanup evidence are established.

### 5.3 SSH transport

The existing authenticated Paramiko session and saved Gateway profile are reused. File bytes travel through SFTP; control operations continue through fixed, allowlisted `b300-stlink` commands. No caller-provided shell fragment is executed.

All control arguments are validated and shell-quoted as individual words. Remote paths are opaque slot/job identifiers, not arbitrary Client paths.

## 6. Protocol and capability model

The Gateway advertises a versioned capability such as:

```text
remote_application_flash_v1
```

Older Client/Gateway combinations fail closed with a clear upgrade action. Existing Debug and Monitor capabilities continue to negotiate independently.

The private Agent protocol gains bounded operations equivalent to:

- `program_create_upload`
- `program_finalize_upload`
- `program_prepare`
- `program_commit`
- `program_status`
- `program_cancel`
- `program_cleanup`

Names may be normalized during implementation, but each operation must have an exact schema and reject unknown or missing fields.

Large firmware bytes never enter the JSON request queue. Requests and responses retain their current bounded sizes.

Every mutating request carries a unique request ID. Create/finalize/prepare/commit are idempotent for the same request and immutable inputs; replay with different inputs is rejected.

## 7. Artifact staging

Gateway staging is per-user under a private runtime/data directory, never under a Client-selected filesystem location.

Required controls:

- maximum firmware size: 32 MiB;
- maximum active/retained slots and total retained bytes;
- directory mode `0700` and file mode `0600` on POSIX;
- random unguessable slot IDs;
- `.part` upload followed by atomic rename only after successful verification;
- resolved path must remain beneath the staging root;
- regular files only; reject symlinks, devices, directories, and unsafe link counts;
- no overwrite of an existing finalized artifact;
- content-addressed final identity using SHA-256;
- bounded expiry for abandoned uploads, approvals, jobs, results, and logs;
- cleanup must never follow links or operate outside the exact staging root.

The Gateway hashes the received file at finalize, at prepare, and immediately before destructive execution. The canonical flash service performs its existing additional staging/re-hash validation.

## 8. Lease and hardware ownership

`FLASH_APPLICATION` is added to the validated lease modes. It is mutually exclusive with `LIVE_WATCH` and `VSCODE_DEBUG`.

Debug modes retain the existing Gateway Supervisor/OpenOCD lifecycle. Flash mode instead reserves the exact probe and starts no persistent GDB/TCL listeners. Its mode handler owns the programming job and canonical service invocation.

The current process-local `HardwareSessionManager` is insufficient to prevent a separate local CLI process from racing the Agent. A private cross-process hardware owner lock is therefore required for every ST-Link operation path. The lock record must contain validated immutable owner evidence and must fail closed on corruption or uncertain liveness. It must not be reclaimed using PID alone.

For a real flash:

- the Client heartbeat maintains the lease before commit;
- after commit, the Gateway job owns and renews the lease internally until terminal completion;
- SSH loss cannot expire ownership during erase/write/verify/metadata/reset;
- Debug/Monitor acquisition reports `GATEWAY_BUSY` with the current sanitized Client label and mode;
- flash acquisition fails similarly while Debug/Monitor is active.

No cancellation is accepted after the first destructive phase starts. Pre-commit and pre-erase cancellation removes the approval/job safely.

## 9. Programming state machine

The persisted public job states are:

```text
UPLOADING
STAGED
PREPARING
AWAITING_CONFIRMATION
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCELLED
RECOVERY_REQUIRED
EXPIRED
```

Key transitions:

- `UPLOADING -> STAGED` only after exact size/hash validation and atomic promotion.
- `STAGED -> PREPARING` only after exclusive lease acquisition.
- `PREPARING -> AWAITING_CONFIRMATION` only after fresh target inspection and canonical dry-run.
- `AWAITING_CONFIRMATION -> QUEUED` only with the matching unexpired approval token.
- `QUEUED -> RUNNING` only after immediate immutable artifact and ownership revalidation.
- `RUNNING -> SUCCEEDED` only after complete canonical post-verification.
- Any verified failure becomes `FAILED` with `failure_phase`, `reason`, and `next_action`.
- Agent restart or ambiguous owner/job evidence becomes `RECOVERY_REQUIRED`; it is never silently retried.

The Client may reconnect and query by job ID. Secrets such as lease and approval bearer tokens are never returned by public status and are persisted only as digests where persistence is required.

## 10. GUI behavior

When a remote Gateway connection is selected, PROGRAM is enabled if:

- the Gateway is authenticated and compatible;
- a valid Application HEX is selected;
- a usable probe is selected or exactly one unambiguous probe is reported;
- no conflicting lease or local operation exists.

The existing local-only banner is replaced with remote readiness and progress. Bootloader controls remain disabled for every remote connection.

The GUI flow is:

1. **Select HEX** -- show local size, address span, CRC32, and SHA-256.
2. **Upload and check** -- show upload progress and Gateway-side verification.
3. **Review dry-run** -- show Gateway, board, probe serial, target evidence, sectors 3--7, metadata address/length, and exact SHA-256.
4. **Confirm Application flash** -- one explicit Client dialog for the returned approval.
5. **Program** -- show phases without offering destructive-phase cancellation.
6. **Verify** -- show terminal evidence or the exact failure phase/action.

Closing the GUI or losing SSH while `RUNNING` does not imply cancellation. Reopening the same profile discovers and resumes display of the active/recent job.

## 11. CLI behavior

The preferred public syntax extends the existing `flash` command:

```text
b300-stlink flash application.hex --gateway <profile> --dry-run --json
b300-stlink flash application.hex --gateway <profile> --confirm-remote-application --json
```

Exact parser spelling may be adjusted only if required to avoid an existing profile identifier ambiguity. The following behavior is mandatory:

- `--gateway` switches to the managed remote path; without it, current local behavior is unchanged.
- remote execution without `--confirm-remote-application` is dry-run/prepare only;
- `--confirm-remote-application` is required for real remote flash and has no effect on local flash;
- `--probe-serial` selects the physical Gateway probe;
- JSON output includes protocol version, job ID, manifest, dry-run plan, state, phase, reason, next action, and terminal verification evidence;
- Ctrl+C before commit cancels safely; Ctrl+C after commit exits the Client while the Gateway job continues and prints the command needed to query status;
- no password, lease token, approval token, or private staging path appears in logs or JSON.

## 12. Confirmed Gateway defect and stability work

The current `GatewaySupervisor.ensure()` holds its main lock while `DebugService.start()` waits for OpenOCD readiness. The OpenOCD output thread calls `_on_openocd_line()`, which attempts to acquire the same lock, blocking readiness processing until startup times out. The Agent then collapses the failure into `TARGET_UNVERIFIED` even though foreground Gateway startup succeeds.

The fix must:

- avoid holding the supervisor state lock across blocking OpenOCD startup/readiness work;
- process output events through a non-blocking event handoff or separately scoped synchronization;
- revalidate generation and ownership before publishing the result;
- preserve hardware-error revocation and GDB activity accounting;
- retain structured startup/readiness diagnostics rather than mapping all exceptions to one reason;
- make stop/start/observe races deterministic and testable;
- tolerate shutdown ordering where OpenOCD exits after the target has already been positively restored, without reporting a false restore failure.

The stability audit also covers:

- request expiry and replay boundaries;
- Agent shutdown while a job is active;
- bounded logs and staging quotas;
- atomic state persistence and corrupt-state recovery;
- SSH reconnect and heartbeat races;
- thread/process cleanup and listener closure;
- error redaction and UI control-state invalidation;
- frozen Linux subprocess environment restoration;
- version/capability drift between packaged Client and Gateway.

## 13. Failure behavior

Representative reason codes include:

```text
REMOTE_FLASH_UNSUPPORTED
UPLOAD_SLOT_INVALID
UPLOAD_TOO_LARGE
UPLOAD_HASH_MISMATCH
ARTIFACT_CHANGED
APPROVAL_EXPIRED
APPROVAL_MISMATCH
GATEWAY_BUSY
PROBE_SELECTION_REQUIRED
TARGET_UNVERIFIED
TARGET_UNSUPPORTED
BOOTLOADER_WRP_INVALID
RDP_POLICY_VIOLATION
FLASH_PLAN_INVALID
FLASH_FAILED
VERIFY_FAILED
METADATA_VERIFY_FAILED
POST_VERIFY_FAILED
JOB_RECOVERY_REQUIRED
```

Every terminal failure returns a stable `failure_phase`, a bounded human-readable reason, and a concrete `next_action`. The Client never retries the flash automatically.

## 14. Test strategy

### Unit and contract tests

- Manifest validation, canonical parsing, basename/path restrictions, and size/hash limits.
- Upload-slot creation, permissions, containment, symlink/hardlink rejection, atomic finalize, quotas, and expiry.
- Protocol exact schemas, unknown fields, replay, request expiry, and capability negotiation.
- Approval binding and expiry across artifact, probe, target, plan, lease, Client, and generation changes.
- `FLASH_APPLICATION` lease exclusion against both existing modes.
- Cross-process owner lock acquisition, stale/corrupt evidence, and cleanup.
- Job state transitions, disconnect behavior, cancellation boundary, restart recovery, and secret redaction.
- GUI enablement, confirmation, reconnect, progress, failure, and remote Bootloader disablement.
- CLI parsing, JSON contracts, Ctrl+C behavior, and unchanged local flash behavior.

### Supervisor regression tests

- OpenOCD readiness callback can run during startup without deadlock.
- Startup timeout exposes the original bounded diagnostic.
- Concurrent stop/ensure and output callbacks cannot publish stale READY state.
- Target restore evidence is not downgraded solely because OpenOCD has already exited.

### Integration tests

- SFTP upload through a fake/loopback SSH transport and real Agent request store.
- Client disconnect before commit, during flash, and after flash.
- Two Clients contending for one probe.
- Debug/Monitor to Flash and Flash to Debug/Monitor exclusion.
- Artifact mutation after upload, after prepare, and just before execution.
- Agent restart in each non-terminal state.
- Full canonical test suite on Windows x64, Ubuntu x64, and Ubuntu ARM64 packaging targets.

### Physical acceptance

After software tests pass, deploy matching candidate builds to the Client and `aubot-tech` Gateway and run a two-machine acceptance on the attached B300 board:

1. Verify Gateway Agent lease acquisition after the deadlock fix.
2. Remote dry-run of the authorized Application HEX.
3. Review exact Sector 3--7 plan and manifest hash.
4. Perform one confirmed remote flash.
5. Verify exact OpenOCD success, AppMeta `CONFIRMED`, PC range, and `BKP1R`.
6. Reconnect GUI and CLI to the completed job evidence.
7. Run remote Debug and Live Monitor afterward.
8. Confirm no leftover OpenOCD listeners, lease, staging file, or hardware owner.
9. Run negative mismatch and contention cases without modifying flash.

No release-success claim is made until this physical acceptance passes on the exact release candidate artifacts.

## 15. Rollout and compatibility

Implementation is staged so existing local flash and remote debug remain usable during development:

1. Fix and independently validate Gateway Supervisor lifecycle defects.
2. Add cross-process ownership and mode-aware lease infrastructure.
3. Add staging, protocol, and persistent programming jobs behind a capability gate.
4. Integrate CLI Client.
5. Integrate production GUI PROGRAM.
6. Complete regression, packaging, and two-machine hardware acceptance.
7. Update user documentation, troubleshooting, release notes, and signed release metadata.

Old Gateways do not expose the capability, so new Clients display an upgrade-required message. Old Clients do not invoke the new operations. Local Application flash, local factory provisioning, and all existing debug modes retain their current public contracts unless a separately documented compatibility fix is required.

## 16. Acceptance criteria

The feature is complete only when:

- GUI and CLI can upload and remotely flash an authorized Application HEX through the same managed Gateway path;
- Gateway-side dry-run precedes Client confirmation;
- the exact file is independently verified on both machines and immediately before execution;
- no remote surface can program Bootloader, modify Option Bytes, mass erase, or issue raw OpenOCD commands;
- remote flash, Debug, Monitor, and local operations cannot compete for ST-Link;
- an SSH disconnect cannot abort, duplicate, or silently mark a destructive job successful;
- the confirmed Gateway Agent readiness defect is fixed with regression evidence;
- all automated tests pass; and
- physical two-machine acceptance proves flash, post-verification, reconnect, Debug, Monitor, and cleanup on the target board.
