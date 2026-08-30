"""Managed non-secret remote Gateway profile for B300 CLI/GUI workflows."""

from __future__ import annotations

import json
import os
import platform
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .ssh_host_trust import validate_gateway_host, validate_ssh_port

_SAFE_USER = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class RemoteGatewayProfile:
    host: str
    user: str
    port: int = 22

    def validate(self) -> "RemoteGatewayProfile":
        host = validate_gateway_host(self.host)
        user = str(self.user).strip()
        if not user or not _SAFE_USER.fullmatch(user):
            raise ValueError("SSH Gateway username contains unsupported characters.")
        port = validate_ssh_port(self.port)
        return RemoteGatewayProfile(host=host, user=user, port=port)

    def record(self) -> dict:
        selected = self.validate()
        return {
            "host": selected.host,
            "user": selected.user,
            "port": selected.port,
            "contains_secrets": False,
        }


def default_remote_profile_path(
        *, home: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None,
        system_name: Optional[str] = None,
) -> Path:
    env = dict(os.environ if environ is None else environ)
    selected_home = Path(home or Path.home())
    system = (system_name or platform.system()).strip().lower()
    override = env.get("B300_REMOTE_PROFILE")
    if override:
        return Path(override).expanduser()
    if system == "windows":
        root = Path(env.get("LOCALAPPDATA") or (selected_home / "AppData" / "Local"))
        return root / "B300-STLink" / "remote_gateway.json"
    config_home = env.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else selected_home / ".config"
    return root / "b300-stlink" / "remote_gateway.json"


def load_remote_profile(path: Optional[Path] = None) -> Optional[RemoteGatewayProfile]:
    target = Path(path or default_remote_profile_path()).expanduser()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("B300 remote Gateway profile is unreadable/corrupt: %s" % target) from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "host", "user", "port"}:
        raise RuntimeError("B300 remote Gateway profile schema is invalid: %s" % target)
    if raw.get("schema_version") != 1:
        raise RuntimeError("Unsupported B300 remote Gateway profile schema version.")
    try:
        return RemoteGatewayProfile(raw["host"], raw["user"], int(raw["port"])).validate()
    except (TypeError, ValueError) as error:
        raise RuntimeError("B300 remote Gateway profile values are invalid: %s" % target) from error


def save_remote_profile(profile: RemoteGatewayProfile, path: Optional[Path] = None) -> Path:
    selected = profile.validate()
    target = Path(path or default_remote_profile_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "host": selected.host,
        "user": selected.user,
        "port": selected.port,
    }
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(str(temp), 0o600)
        os.replace(str(temp), str(target))
        if os.name != "nt":
            os.chmod(str(target), 0o600)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    return target


def clear_remote_profile(path: Optional[Path] = None) -> bool:
    target = Path(path or default_remote_profile_path()).expanduser()
    if not target.exists():
        return False
    if not target.is_file():
        raise RuntimeError("B300 remote Gateway profile path is not a regular file: %s" % target)
    target.unlink()
    return True
