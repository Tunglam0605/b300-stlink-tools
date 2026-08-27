# Commands by platform

## Windows

```powershell
b300-stlink doctor
b300-stlink flash "C:\firmware\Main_V2_F407.hex" --dry-run --json
b300-stlink flash "C:\firmware\Main_V2_F407.hex" --probe-serial <ST-LINK-SN> --json
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

## Ubuntu IPC

```bash
b300-stlink doctor
b300-stlink flash /opt/firmware/Main_V2_F407.hex --dry-run --json
b300-stlink flash /opt/firmware/Main_V2_F407.hex --probe-serial <ST-LINK-SN> --json
b300-stlink debug --gdb-port 3333 --telnet-port 4444
```

If Ubuntu does not expose the ST-Link to the non-root user, repair the udev rule
and `plugdev` membership; do not prepend `sudo` to `b300-stlink`.

## Useful output

Save a structured flash log with:

```text
b300-stlink flash <application.hex> --json > b300-flash.log
```
