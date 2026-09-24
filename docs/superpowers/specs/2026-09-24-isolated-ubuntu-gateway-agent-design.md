# Isolated Ubuntu Gateway Agent for Remote Application Programming

**Date:** 2026-09-24
**Status:** Design approved in conversation; written-spec review pending
**Scope:** Ubuntu x64/ARM64 Gateway, Windows x64 EXE and Linux Clients

## 1. Purpose and decisions

This design closes the release-critical gap in the existing [remote Application
programming design](2026-09-23-remote-application-programming-design.md): the SSH
login, SFTP upload, per-user Gateway Agent, job records, and finalized HEX all
currently belong to the same Ubuntu account. Repeated path and hash checks do
not prevent that account from replacing a file between validation and use.

The operator approved these decisions:

- Keep the existing Client SSH/SFTP login and GUI/CLI workflow. For the tested
  Gateway, that login is `aubot`.
- Run the Ubuntu Gateway Agent as a dedicated unprivileged `b300-agent` account
  without SSH login. Its installed executable and systemd system unit are
  administrator-owned; its state and finalized firmware are Agent-owned.
- Remove the `aubot` account's direct ST-Link USB permission. Factory/Bootloader
  provisioning remains local-only on a separate maintenance machine with the
  probe physically attached; it is not added to the remote Agent API.
- Route remote Application Flash, Debug, and Monitor through one Agent and one
  hardware owner. Preserve the Client-visible SSH/JSON commands and VS Code
  workflow where their safety contracts can be maintained.
- Migrate `aubot-tech` only after source tests, CI, package checks, and a
  documented rollback pass. A real GUI flash still needs separate, current-
  session authorization for the exact board, probe, and HEX.

Windows Gateway remote Application programming is outside this release; a
Windows Gateway must not advertise the isolated capability. Local Windows
flash/debug and Windows Client operation remain supported.

## 2. Trust boundary

The SSH account may create and modify an **ingress** `.part` file before
finalization. As an unprivileged process, it cannot write the Agent executable,
control state, private job records, logs, approved firmware, or ST-Link USB
device. On the tested IPC, `aubot` retains sudo because no alternate administrator
account exists; a sudo-capable operator can bypass this OS boundary. A finalized image is
an Agent-owned copy whose exact bytes were checked against the Client manifest.
The Agent never prepares or flashes from an ingress path.

The SSH account is still a **trusted administrator and debug operator**: current VS Code/GDB and
Live Monitor workflows intentionally grant it controlled access to a running
debug session through SSH forwarding. This release does not claim to withstand
a malicious operator issuing raw debugger traffic if a debug listener is
available. The project must not describe the account separation as a complete
sandbox against compromise of the SSH account. OpenOCD remains loopback-only,
Telnet disabled, and the existing application forbids raw flash, memory write,
and Option-Byte operations in Debug/Monitor workflows. A stronger hostile-debug-
operator boundary would require a separately specified protocol proxy.

## 3. Layout and identity

- `b300-agent` is a static system account with no interactive shell or SSH key.
- The installed Gateway CLI/runtime and systemd **system** unit are owned by
  root and are not writable by `aubot` or `b300-agent`.
- Agent private state, including control state, lease/owner evidence, finalized
  jobs, logs, and approval digests, lives under one Agent-owned root such as
  `/var/lib/b300-stlink/gateway`; directories are `0700`, files `0600`.
- The control endpoint is a local Unix-domain socket under `/run/b300-stlink`.
  Only the configured SSH operator group may connect; the Agent verifies peer
  credentials and processes the same bounded, allowlisted operation schemas.
  No new LAN-facing port is introduced.
- SFTP ingress retains the Client-compatible suffix
  `program-jobs/<job-id>/artifact.part` under a separate bounded transfer root.
  Per-job directories permit the authenticated SSH upload user to write only
  that ingress file, and permit the Agent to read it. They do not expose the
  Agent's private job root.
- On Ubuntu the ingress root is a dedicated system-level tmpfs mount at
  `/var/spool/b300-stlink/ingress`, limited to `size=65M,nr_inodes=256` and
  mounted `nodev,nosuid,noexec`. It bounds aggregate allocated SFTP ingress
  storage at write time; it does not replace the 32-MiB per-file finalize
  check. The Agent refuses upload slots if the exact mount is absent or its
  filesystem/options/ownership differ. Durable job records stay under
  `/var/lib/b300-stlink/gateway`, so loss of volatile ingress after reboot
  becomes an explicit incomplete-upload state, never an automatic flash.
- The hardware owner lock and all Agent/legacy Gateway commands use one
  system-scoped owner identity, not a `Path.home()` lock for each Linux user.
- The exact ST-Link USB node uses group `b300-probe` with mode `0660` and no
  `uaccess` tag; only the `b300-agent` service account joins that group. The
  SSH operator account remains outside it for unprivileged access.

The setup path must verify actual group memberships, udev rules, `uaccess`
grants, filesystem ownership, and systemd state on the target host before
claiming the boundary is active. The Agent stays non-root during operation.
System administration is limited to installation, account/group, udev, and
service setup; it must not run `sudo b300-stlink` to bypass USB permissions.
Migration removes direct `plugdev`/`uaccess` USB access from `aubot` but does not
remove its sudo membership or claim resistance to an intentionally privileged operator.

## 4. Upload and programming flow

1. Client pins the Gateway SSH host key, parses its Application Intel HEX,
   computes size/SHA-256, and asks the allowlisted SSH CLI proxy for an upload
   slot. The Agent returns an opaque job ID and ingress path. Client SFTP writes
   only `artifact.part` at that path.
2. On finalize, the Agent opens the exact ingress file without following links,
   validates a regular file, bounded size, safe link count, and expected job
   location, then copies from that open handle into a new Agent-private file.
   It hashes bytes **as copied**, fsyncs, verifies size/SHA-256 against the
   manifest, and atomically promotes the private file. An ingress mutation or
   short copy fails without issuing an approval. The private artifact is never
   overwritten by retry or replay.
3. After finalization, the Agent may remove the ingress copy; mutation,
   deletion, symlinking, or hardlinking of that ingress path cannot change the
   approved image. Prepare, replayed prepare, commit, worker flash, and cleanup
   refer only to the Agent-private job and perform their normal hash/target/
   plan checks there.
4. The canonical `B300Service.flash()` still owns the exact Sector 3--7
   transaction, staged-image comparison, target/WRP/RDP preflight, metadata
   write/read-back, reset, and post-verify. No remote Bootloader, raw OpenOCD,
   mass erase, or automatic destructive retry surface is added.
5. The Client still confirms once after a fresh Gateway dry-run. Disconnect
   after commit leaves the job running under Agent ownership. Ambiguous crash
   evidence remains `RECOVERY_REQUIRED`, not automatic replay.

Existing Client JSON fields and profile IDs remain stable where possible.
The new Gateway advertises a distinct isolated-programming capability; new
Clients refuse remote flash if only the older per-user capability is present.
Older Clients must not be allowed to start an unisolated Agent on an upgraded
Gateway. The migration must define the precise capability/version handshake in
tests before enabling real flash.

## 5. Debug, Monitor, and local-command compatibility

The SSH CLI remains a thin local socket proxy for Agent status, lease, and
allowlisted operations. The `debug gateway/status/ensure/rescan` legacy commands
must not auto-spawn a second per-user OpenOCD owner after migration. Debug and
Monitor obtain leases and snapshots from the same system Agent. OpenOCD GDB/TCL
listeners remain loopback-only, with the existing guarded Client forwarding and
RUN/HALT restoration; no GDB `load` or remote programming through Debug is
introduced. Port, lease, stale-owner, and process cleanup behavior must be
retested on the exact upgraded Gateway.

Direct local ST-Link CLI commands under `aubot` will fail with a clear
permission/role message after its USB grant is removed; they must not suggest
`sudo b300-stlink`. Factory maintenance is performed only on a separately
authorized local maintenance station, outside this remote release.

## 6. Migration and rollback

Migration is an explicit, audited host-administration operation, not a Client
auto-update side effect:

1. Record OS/architecture, current Agent version, service/udev/group/USB state,
   active leases/jobs, owner record, OpenOCD listeners, board health, and hashes
   of the installed bundle. Do not migrate while a job or debug lease is active.
2. Back up existing per-user records and logs without treating old ambiguous
   jobs as completed. Install the tested root-owned bundle, static account,
   private roots, group-restricted socket, and system unit. Disable the old user
   unit and its auto-spawn path before starting the new one.
3. Remove `aubot` USB access, reload/replug the ST-Link only when the board and
   mechanism are safe, and verify `b300-agent` can inspect while `aubot` cannot
   open the probe. Verify the SSH proxy, capability, status, dry-run, Debug,
   Monitor, and cleanup before considering destructive acceptance.
4. If any pre-flash migration check fails, stop the new service, preserve its
   private evidence and logs, and restore the old installed software only with
   remote Application flash disabled. Do not infer that an interrupted job
   failed or succeeded, do not retry it, and do not erase/modify the board as
   part of rollback.

Installation and rollback scripts must operate on exact validated paths and
leave pre-existing profiles, SSH host trust, and unrelated user files intact.

## 7. Verification and release gates

- Test the unprivileged boundary on Ubuntu x64 and ARM64: `aubot` without sudo cannot
  write private files or open ST-Link; `b300-agent` can read only the intended
  upload and can inspect the selected probe. Socket peer rejection, malformed
  requests, replay, quotas, symlink/hardlink/path escape, and concurrent ingress
  mutation all fail closed.
- Prove an oversized SFTP write is bounded by the dedicated mount, an absent
  mount prevents new upload slots without a disk fallback, and lost ingress
  after reboot leaves durable job evidence and requires a new manual upload.
- Unit/integration tests cover private-copy identity across finalize, prepare,
  commit, worker flash, cleanup, Agent restart, disconnect, and two-Client
  contention. Windows x64 and Linux Client tests cover unchanged GUI/CLI flow;
  unsupported Gateway/Client combinations fail with upgrade guidance.
- Run the complete repository test inventory, Python compilation, repository
  hygiene checks, and native packaging/smoke tests on Windows x64, Ubuntu x64,
  and Ubuntu ARM64. Fix the current Windows CI path-alias test assertion before
  treating CI as green. Obtain independent security/release review.
- Build exact matching candidate artifacts and record source SHA-256, package
  hashes, installed versions, and signed manifest evidence. No public tag or
  `Latest` change occurs at this stage.
- On `aubot-tech`, perform read-only board/probe/health checks and remote dry-
  run. A destructive flash from the **visible packaged Windows GUI** requires a
  new explicit operator confirmation of the exact board, probe, and HEX hash in
  that session. Success requires exact `** Verified OK **`, 44-byte AppMeta
  evidence, reset, `STLM CONFIRMED`, Application PC, and `BKP1R == 0`.
- Run controlled Client SSH disconnect and Agent crash/recovery acceptance only
  in a safe physical test state; preserve logs and do not auto-retry. Recheck
  Debug/Live Monitor, listener closure, and hardware owner release afterward.
- Only after every required gate is evidenced may the version be bumped,
  signed release artifacts published, and updater manifests verified against
  the published tag. If a physical gate is deferred, the output remains an
  internal release candidate, not Stable.
