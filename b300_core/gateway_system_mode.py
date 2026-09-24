"""Administrator marker selecting the isolated Ubuntu Gateway Agent."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ISOLATED_GATEWAY_MARKER = Path("/etc/b300-stlink/isolated-gateway.json")
SYSTEM_SOCKET_PATH = Path("/run/b300-stlink/agent.sock")
SYSTEM_STATE_ROOT = Path("/var/lib/b300-stlink/gateway")
SYSTEM_INGRESS_ROOT = Path("/var/spool/b300-stlink/ingress")
INGRESS_SIZE_BYTES = 65 * 1024 * 1024


def _run_findmnt(command):
    return subprocess.run(command, capture_output=True, text=True, check=False)


def ingress_mount_isolated(path: Path, *, uid: int, gid: int,
                           runner=_run_findmnt) -> bool:
    """Require the exact bounded system tmpfs before accepting SFTP bytes."""
    target = Path(path)
    if target != SYSTEM_INGRESS_ROOT and str(target) != str(SYSTEM_INGRESS_ROOT):
        return False
    command = ("findmnt", "--json", "--bytes", "--output",
               "TARGET,FSTYPE,OPTIONS,SIZE", "--mountpoint", str(target))
    try:
        result = runner(command)
        if result.returncode != 0:
            return False
        record = json.loads(result.stdout)
        items = record.get("filesystems")
        if not isinstance(items, list) or len(items) != 1:
            return False
        mount = items[0]
        if (mount.get("target") != str(target) or mount.get("fstype") != "tmpfs"
                or int(mount.get("size", -1)) != INGRESS_SIZE_BYTES):
            return False
        parts = str(mount.get("options", "")).split(",")
        options = set(parts)
        values = dict(part.split("=", 1) for part in parts if "=" in part)
        return (all(option in options for option in ("nodev", "nosuid", "noexec"))
                and values.get("nr_inodes") == "256"
                and values.get("uid") == str(uid)
                and values.get("gid") == str(gid)
                and values.get("mode", "").lstrip("0") == "710")
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False


@dataclass(frozen=True)
class IsolatedGatewayConfig:
    socket_path: Path
    state_root: Path
    ingress_root: Path
    operator_uid: int
    operator_gid: int = -1
    flash_enabled: bool = False


def load_isolated_gateway_config(
        marker_path: Path = ISOLATED_GATEWAY_MARKER, *,
        trusted_uid: int = 0) -> Optional[IsolatedGatewayConfig]:
    """Return None only for a missing marker; reject every unsafe present marker."""
    marker = Path(marker_path)
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != trusted_uid
            or info.st_mode & 0o022):
        raise ValueError("Isolated Gateway marker ownership or mode is unsafe")
    parent = marker.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != trusted_uid
            or parent.st_mode & 0o022):
        raise ValueError("Isolated Gateway marker directory is unsafe")
    if info.st_size > 4096:
        raise ValueError("Isolated Gateway marker exceeds limit")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(marker), flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != trusted_uid
                    or opened.st_mode & 0o022 or opened.st_size > 4096
                    or opened.st_dev != info.st_dev or opened.st_ino != info.st_ino):
                raise ValueError("Isolated Gateway marker changed during read")
            record = json.loads(handle.read(4097).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Isolated Gateway marker is invalid") from error
    if (not isinstance(record, dict) or set(record) != {
            "schema_version", "socket_path", "state_root", "ingress_root",
            "operator_uid", "operator_gid", "flash_enabled",
    } or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or type(record["operator_uid"]) is not int or record["operator_uid"] < 0
            or type(record["operator_gid"]) is not int or record["operator_gid"] < 0
            or type(record["flash_enabled"]) is not bool
            or record["socket_path"] != str(SYSTEM_SOCKET_PATH)
            or record["state_root"] != str(SYSTEM_STATE_ROOT)
            or record["ingress_root"] != str(SYSTEM_INGRESS_ROOT)):
        raise ValueError("Isolated Gateway marker schema is invalid")
    return IsolatedGatewayConfig(SYSTEM_SOCKET_PATH, SYSTEM_STATE_ROOT,
                                 SYSTEM_INGRESS_ROOT, record["operator_uid"],
                                 record["operator_gid"], record["flash_enabled"])


def isolated_gateway_mode(marker_path: Path = ISOLATED_GATEWAY_MARKER, *,
                          trusted_uid: int = 0) -> bool:
    return load_isolated_gateway_config(marker_path, trusted_uid=trusted_uid) is not None


__all__ = ["ISOLATED_GATEWAY_MARKER", "IsolatedGatewayConfig",
           "isolated_gateway_mode", "load_isolated_gateway_config",
           "ingress_mount_isolated"]
