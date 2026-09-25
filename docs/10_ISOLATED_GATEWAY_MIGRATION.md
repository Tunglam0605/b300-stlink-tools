# Isolated Ubuntu Gateway migration

B300 ST-Link Tools v0.24 uses an isolated system Gateway before managed remote
Application programming is enabled. The migration is deliberately two-phase:
**PREPARE** installs the boundary with flash disabled, then **ACTIVATE** enables
remote Application flash only after the USB/mount/owner verifier passes.

This workflow changes Linux accounts, groups, udev rules and systemd units. It
does not erase/program the STM32, change Option Bytes, or run an Application
flash.

## Preconditions

- Ubuntu Gateway, one intended ST-Link, no active OpenOCD process.
- Existing Gateway Agent is idle; no active lease or non-terminal program job.
- Exact Linux CLI release archive and its SHA-256 are known.
- Run the read-only plan before any privileged change.
- The candidate archive passed CI/package verification and is copied into a
  root-owned, non-group/world-writable path before PREPARE.

Example root-owned candidate staging:

```bash
sudo install -o root -g root -m 0600 \
  B300-STLink-CLI-Linux-x64.tar.gz \
  /var/lib/b300-stlink-v0.24.0-linux-x64.tar.gz
```

The standalone admin tools are shipped in the Linux archive under `tools/`.

## 1. Read-only plan

```bash
sudo python3 tools/install_isolated_gateway.py plan \
  --bundle /var/lib/b300-stlink-v0.24.0-linux-x64.tar.gz \
  --expected-sha256 <64-hex-sha256> \
  --json
```

Continue only when the result is `"decision":"GO"`. A NO_GO result is a hard
stop; do not bypass path, service, lease, job, probe or OpenOCD blockers.

## 2. PREPARE — flash remains disabled

```bash
sudo python3 tools/activate_isolated_gateway.py prepare \
  --bundle /var/lib/b300-stlink-v0.24.0-linux-x64.tar.gz \
  --expected-sha256 <64-hex-sha256> \
  --operator aubot \
  --confirm-system-change \
  --json
```

PREPARE:

- creates the dedicated `b300-agent`, `b300-probe`, `b300-upload` and
  `b300-operator` identities;
- stages and re-verifies the exact archive;
- installs a root-owned runtime at `/opt/b300-stlink/bin`;
- installs the isolated system Agent and bounded tmpfs ingress mount;
- installs the ST-Link udev boundary;
- stops the legacy per-user Agent;
- writes `/etc/b300-stlink/isolated-gateway.json` with
  `flash_enabled=false`;
- starts the system Agent in fail-closed pending mode.

No MCU flash command is issued by PREPARE.

If USB ownership has not changed on the already-connected ST-Link after the
udev reload/trigger, physically replug that ST-Link before ACTIVATE.

## 3. ACTIVATE — boundary proof

```bash
sudo python3 tools/activate_isolated_gateway.py activate \
  --confirm-system-change \
  --json
```

ACTIVATE succeeds only when all of these are proven:

- system Agent is idle;
- no active/non-terminal program job exists;
- no OpenOCD process owns hardware;
- ingress is the exact bounded tmpfs mount;
- the intended ST-Link node is `root:b300-probe 0660`;
- `b300-agent` can open the ST-Link;
- the SSH operator cannot open the ST-Link directly;
- the same probe identity remains stable across the proof;
- the persistent hardware-owner flock is idle.

Only then is the marker changed to `flash_enabled=true`. The running Agent
must subsequently advertise `remote_application_flash_isolated_v1`.

Reconnect the Windows Client after migration so the new SSH login receives the
new `b300-operator` / `b300-upload` supplementary groups.

## 4. Rollback

Rollback is also quiescence-gated and never touches MCU flash:

```bash
sudo python3 tools/activate_isolated_gateway.py rollback \
  --confirm-system-change \
  --json
```

If isolated flash is active, rollback first transitions the marker back to
pending under the persistent hardware-owner flock. It then disables the system
Agent/mount, removes the activation surface and restores the legacy user Agent
according to the captured rollback inventory. Candidate and state evidence are
preserved for audit/recovery.

Never remove the marker, owner lock, job records or service files manually
while a lease/job/OpenOCD owner is active.
