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

## Useful output

Save a structured flash log with:

```text
b300-stlink flash <application.hex> --json > b300-flash.log
```

The JSON stream includes `flash_phase` events and a final `flash_result` with
`failure_phase`, `reason`, and `next_action` when unsuccessful.
