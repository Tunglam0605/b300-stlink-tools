"""Resolve the GDB runtime used by the optional integrated debugger."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class GdbRuntimeInfo:
    path: Optional[str]
    version: Optional[str]
    available: bool
    platform: str
    reason: Optional[str] = None

    @classmethod
    def from_path(cls, path: Optional[str], *, platform_name: Optional[str] = None,
                  version: Optional[str] = None, reason: Optional[str] = None) -> "GdbRuntimeInfo":
        normalized = (platform_name or platform.system()).lower()
        return cls(path, version, bool(path), normalized, reason)


def _runtime_roots() -> List[Path]:
    roots: List[Path] = []
    configured_root = os.environ.get("B300_APP_ROOT")
    if configured_root:
        roots.append(Path(configured_root))
    roots.append(Path(sys.executable).resolve().parent)
    appdir = os.environ.get("APPDIR")
    if appdir:
        roots.append(Path(appdir) / "usr" / "lib" / "b300-stlink")
    if os.name != "nt":
        roots.append(Path("/opt/b300-stlink"))
    return roots


def _validate_candidate(value: str, label: str) -> str:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("%s contains an unsafe control character." % label)
    return value


def _existing_path(value: str, label: str) -> str:
    candidate = Path(_validate_candidate(value, label)).expanduser()
    if not candidate.is_file():
        raise RuntimeError("%s does not exist: %s" % (label, candidate))
    return str(candidate)


def resolve_gdb(explicit: Optional[str] = None) -> str:
    """Prefer explicit/configured GDB, then bundled, then the system PATH."""
    if explicit is not None:
        return _existing_path(explicit, "GDB path")
    configured = os.environ.get("B300_GDB")
    if configured:
        return _existing_path(configured, "B300_GDB")
    executable_name = "arm-none-eabi-gdb.exe" if os.name == "nt" else "arm-none-eabi-gdb"
    seen = set()
    for root in _runtime_roots():
        candidate = root / "vendor" / "gdb" / "bin" / executable_name
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            if candidate.is_file():
                return str(candidate)
    arm_gdb = shutil.which("arm-none-eabi-gdb")
    if arm_gdb:
        return arm_gdb
    if platform.system().lower() == "linux":
        multiarch = shutil.which("gdb-multiarch")
        if multiarch:
            return multiarch
    raise RuntimeError(
        "GDB was not found. Install arm-none-eabi-gdb, or set B300_GDB to a valid executable."
    )


def gdb_runtime_info(explicit: Optional[str] = None) -> GdbRuntimeInfo:
    try:
        resolved = resolve_gdb(explicit)
        completed = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, timeout=5.0, check=False,
        )
        version = completed.stdout.splitlines()[0].strip() if completed.stdout else None
        if completed.returncode != 0:
            return GdbRuntimeInfo.from_path(
                None, version=version,
                reason="GDB --version exited with code %s." % completed.returncode,
            )
        return GdbRuntimeInfo.from_path(resolved, version=version)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        return GdbRuntimeInfo.from_path(None, reason=str(error))
