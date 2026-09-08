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
_SAFE_CLI_PATH = re.compile(r"^/[A-Za-z0-9._/@=+:-]+(?:/[A-Za-z0-9._@=+:-]+)*$")


@dataclass(frozen=True)
class RemoteGatewayProfile:
    host: str
    user: str
    port: int = 22
    cli_path: Optional[str] = None

    def validate(self) -> "RemoteGatewayProfile":
        host = validate_gateway_host(self.host)
        user = str(self.user).strip()
        if not user or not _SAFE_USER.fullmatch(user):
            raise ValueError("SSH Gateway username contains unsupported characters.")
        port = validate_ssh_port(self.port)
        cli_path = self.cli_path
        if cli_path is not None:
            cli_path = str(cli_path).strip()
            if (not cli_path or not _SAFE_CLI_PATH.fullmatch(cli_path)
                    or any(part in {".", ".."} for part in cli_path.split("/"))):
                raise ValueError("Gateway CLI path must be an absolute safe executable path.")
        return RemoteGatewayProfile(host=host, user=user, port=port, cli_path=cli_path)

    def record(self) -> dict:
        selected = self.validate()
        record = {
            "host": selected.host,
            "user": selected.user,
            "port": selected.port,
            "contains_secrets": False,
        }
        if selected.cli_path is not None:
            record["cli_path"] = selected.cli_path
        return record


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
    allowed = {"schema_version", "host", "user", "port", "cli_path"}
    if not isinstance(raw, dict) or not {"schema_version", "host", "user", "port"} <= set(raw) <= allowed:
        raise RuntimeError("B300 remote Gateway profile schema is invalid: %s" % target)
    if raw.get("schema_version") != 1:
        raise RuntimeError("Unsupported B300 remote Gateway profile schema version.")
    try:
        return RemoteGatewayProfile(
            raw["host"], raw["user"], int(raw["port"]), raw.get("cli_path")
        ).validate()
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
    if selected.cli_path is not None:
        payload["cli_path"] = selected.cli_path
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
