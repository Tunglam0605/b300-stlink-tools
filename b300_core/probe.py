"""Best-effort cross-platform ST-Link probe discovery."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Tuple

from .models import ProbeInfo


def _unique(probes: Iterable[ProbeInfo]) -> Tuple[ProbeInfo, ...]:
    result = {}
    for probe in probes:
        if probe.serial:
            key = ("serial", probe.serial)
        else:
            key = ("identity", probe.source, probe.usb_identity)
        result.setdefault(key, probe)
    return tuple(sorted(
        result.values(),
        key=lambda item: (
            item.serial is None,
            item.serial or "",
            item.source,
            item.usb_identity or "",
            item.name,
        ),
    ))


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
        if not serial or "&" in serial or not _SAFE_OPENOCD_SERIAL.fullmatch(serial):
            serial = None
        probes.append(ProbeInfo(
            serial=serial,
            name=str(record.get("FriendlyName") or "ST-Link"),
            source="windows-pnp",
            usb_identity=instance_id,
        ))
    return _unique(probes)


_SAFE_OPENOCD_SERIAL = re.compile(r"[A-Za-z0-9_.:-]+")


def _read_sysfs_text(path: Path) -> str:
    # USB sysfs attributes are normally ASCII, but clone/debug probes can expose
    # non-ASCII or malformed serial bytes. Discovery must never crash the GUI.
    return path.read_bytes().decode("utf-8", errors="replace").strip()


def parse_linux_sysfs(root: Path = Path("/sys/bus/usb/devices")) -> Tuple[ProbeInfo, ...]:
    probes = []
    try:
        devices = tuple(root.iterdir())
    except OSError:
        return ()
    for device in devices:
        try:
            vendor = _read_sysfs_text(device / "idVendor").lower()
            product = _read_sysfs_text(device / "idProduct").lower()
        except OSError:
            continue
        if vendor != "0483" or not product.startswith("374"):
            continue
        try:
            serial = _read_sysfs_text(device / "serial")
        except OSError:
            serial = ""
        # Only expose a serial when it is safe for OpenOCD's `adapter serial`
        # command. A clone without one remains discoverable for safe
        # single-probe auto-selection.
        probes.append(ProbeInfo(
            serial if serial and _SAFE_OPENOCD_SERIAL.fullmatch(serial) else None,
            "ST-Link %s" % product.upper(),
            "linux-sysfs",
            "%s:%s:%s" % (vendor, product, device.name),
        ))
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
