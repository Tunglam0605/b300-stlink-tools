"""Best-effort cross-platform ST-Link probe discovery."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Tuple

from .models import ProbeInfo


def _unique(probes: Iterable[ProbeInfo]) -> Tuple[ProbeInfo, ...]:
    result = {}
    for probe in probes:
        if probe.serial:
            result.setdefault(probe.serial, probe)
    return tuple(sorted(result.values(), key=lambda item: item.serial))


def parse_windows_pnp_output(text: str) -> Tuple[ProbeInfo, ...]:
    if not text.strip():
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ()
    records = payload if isinstance(payload, list) else [payload]
    probes = []
    for record in records:
        if not isinstance(record, dict):
            continue
        instance_id = str(record.get("InstanceId") or "")
        upper = instance_id.upper()
        if "VID_0483&PID_374" not in upper or "\\" not in instance_id:
            continue
        serial = instance_id.rsplit("\\", 1)[-1].strip()
        if not serial or "&" in serial:
            continue
        probes.append(ProbeInfo(
            serial=serial,
            name=str(record.get("FriendlyName") or "ST-Link"),
            source="windows-pnp",
        ))
    return _unique(probes)


def parse_linux_sysfs(root: Path = Path("/sys/bus/usb/devices")) -> Tuple[ProbeInfo, ...]:
    probes = []
    try:
        devices = tuple(root.iterdir())
    except OSError:
        return ()
    for device in devices:
        try:
            vendor = (device / "idVendor").read_text(encoding="ascii").strip().lower()
            product = (device / "idProduct").read_text(encoding="ascii").strip().lower()
            serial = (device / "serial").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if vendor == "0483" and product.startswith("374") and serial:
            probes.append(ProbeInfo(serial, "ST-Link %s" % product.upper(), "linux-sysfs"))
    return _unique(probes)


def list_probes() -> Tuple[ProbeInfo, ...]:
    if platform.system().lower() == "windows":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return ()
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "
            "'USB\\VID_0483&PID_374*' } | Select-Object FriendlyName,InstanceId | "
            "ConvertTo-Json -Compress",
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace")
        except OSError:
            return ()
        return parse_windows_pnp_output(completed.stdout)
    if platform.system().lower() == "linux":
        return parse_linux_sysfs()
    return ()

