# Commands by platform

## Windows

```powershell
b300-stlink doctor
b300-stlink flash "C:\firmware\Main_V2_F407.hex" --dry-run --json
b300-stlink flash "C:\firmware\Main_V2_F407.hex" --probe-serial <ST-LINK-SN> --json
b300-stlink debug gateway --json
b300-stlink debug vscode --ssh-host <IPC-IP> --ssh-user <SSH-USER> --program-relative build/Main_V2_F407.axf --output-dir . --json
```

## Ubuntu IPC

Inspect an installed Gateway before starting another owner:

```bash
b300-stlink --version --json
b300-stlink gateway doctor --json
```

Only for an authorized setup/repair:

```bash
b300-stlink gateway quickstart --confirm-system-change --json
```

The Gateway Agent owns exclusive leases and starts OpenOCD on demand. Do not
launch another raw OpenOCD alongside it. A system Gateway installation and a
per-user CLI can have separate paths; update and verify the actual service
executable, not only the command found first on PATH.

Read both possible unit identities before changing an installation:

```bash
systemctl show b300-stlink-gateway-agent.service --property=ActiveState,SubState,FragmentPath,ExecStart,User
systemctl --user show b300-stlink-gateway-agent.service --property=ActiveState,SubState,FragmentPath,ExecStart
```

Run `--version --json` on the executable reported by the active unit. Managed
one-shot `debug client --client-action inspect` cleans up its own tunnel/lease;
do not invent a separate raw lease-release command. For an interactive generated
VS Code kit, use its managed tasks and finish the debug session before stopping
the bridge. For the GUI use **Dừng**. Do not stop an Agent with another owner's
active lease merely to check its version.

```bash
b300-stlink doctor
b300-stlink flash /opt/firmware/Main_V2_F407.hex --dry-run --json
b300-stlink flash /opt/firmware/Main_V2_F407.hex --probe-serial <ST-LINK-SN> --json
b300-stlink debug gateway --json
```

If Ubuntu does not expose the ST-Link to the non-root user, repair the udev rule
and `plugdev` membership; do not prepend `sudo` to `b300-stlink`.

Local debug binds to `127.0.0.1`. When the probe is connected to an Ubuntu IPC
and the client runs on another machine, keep OpenOCD on loopback:

```text
b300-stlink debug gateway
```

From the CLIENT, use the managed SSH profile and the matching ELF/AXF:

```text
b300-stlink debug client --ssh-host <IPC-IP> --ssh-user <SSH-USER> \
  --symbols <application.axf> --client-action inspect --json
b300-stlink debug vscode --ssh-host <IPC-IP> --ssh-user <SSH-USER> \
  --program-relative build/application.axf --output-dir . --json
```

Only SSH TCP/22 is LAN-facing; do not expose or NAT GDB/TCL ports 3333/6666.
Open the generated workspace with VS Code + Cortex-Debug. Manual GDB is an
Advanced workflow only. Never use GDB `load`, `restore`, or flash commands.

In Pulse GUI v0.24.5+, the same operation is **Debug → Kết nối → IPC profile →
Mở debug**; local debugging selects **Máy này**. Both open VS Code. Use F5 to
attach, Shift+F5 to end the VS Code session, then **Dừng** to release B300.

## Useful output

Save a structured flash log with:

```text
b300-stlink flash <application.hex> --json > b300-flash.log
```

The JSON stream includes `flash_phase` events and a final `flash_result` with
`failure_phase`, `reason`, and `next_action` when unsuccessful.
