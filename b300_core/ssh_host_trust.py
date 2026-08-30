"""Strict SSH host-key enrollment for B300 remote gateways."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .ssh_identity import (
    public_key_fingerprint, public_key_identity, resolve_ssh_client_executable, validate_public_key,
)

_SAFE_HOST = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass(frozen=True)
class GatewayHostKey:
    host: str
    port: int
    host_field: str
    public_key: str
    fingerprint: str


@dataclass(frozen=True)
class HostTrustResult:
    known_hosts_file: Path
    host_field: str
    fingerprint: str
    changed: bool


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


def _run(argv: Sequence[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        tuple(str(item) for item in argv), capture_output=True, text=True,
        timeout=timeout, check=False,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
    )


def validate_gateway_host(host: str) -> str:
    value = str(host).strip()
    if not value or not _SAFE_HOST.fullmatch(value):
        raise ValueError("SSH Gateway host contains unsupported characters.")
    return value


def validate_ssh_port(port: int) -> int:
    value = int(port)
    if not 1 <= value <= 65535:
        raise ValueError("SSH port must be in range 1..65535.")
    return value


def expected_known_hosts_field(host: str, port: int) -> str:
    value = validate_gateway_host(host)
    selected_port = validate_ssh_port(port)
    return value if selected_port == 22 else "[%s]:%d" % (value, selected_port)


def managed_known_hosts_path(home: Optional[Path] = None) -> Path:
    return Path(home or Path.home()) / ".ssh" / "b300_known_hosts"


def _host_field_matches(field: str, host: str, port: int) -> bool:
    expected = expected_known_hosts_field(host, port)
    if field == expected:
        return True
    # ssh-keyscan -p 22 may emit either host or [host]:22 depending on build.
    if int(port) == 22 and field == "[%s]:22" % host:
        return True
    return False


def scan_gateway_host_key(
        host: str, port: int = 22, *, runner: CommandRunner = _run,
        executable: Optional[str] = None,
) -> GatewayHostKey:
    selected_host = validate_gateway_host(host)
    selected_port = validate_ssh_port(port)
    keyscan = executable
    if keyscan is None:
        resolved = resolve_ssh_client_executable("ssh-keyscan")
        keyscan = str(resolved) if resolved is not None else None
    if not keyscan:
        raise RuntimeError("ssh-keyscan was not found. Prepare OpenSSH Client first.")
    command = (keyscan, "-T", "5", "-p", str(selected_port), "-t", "ed25519", selected_host)
    result = runner(command, 15.0)
    if result.returncode not in (0, 1):
        raise RuntimeError("ssh-keyscan failed with exit code %d: %s" % (
            result.returncode, (result.stderr or "").strip()
        ))
    candidates = []
    for raw_line in (result.stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3 or not _host_field_matches(parts[0], selected_host, selected_port):
            continue
        public_key = validate_public_key("%s %s" % (parts[1], parts[2]))
        candidates.append((parts[0], public_key, public_key_fingerprint(public_key)))
    unique = {(public, fingerprint) for _field, public, fingerprint in candidates}
    if not candidates:
        raise RuntimeError("No ssh-ed25519 host key was returned by the Gateway.")
    if len(unique) != 1:
        raise RuntimeError("Gateway returned multiple different ssh-ed25519 host keys; refusing enrollment.")
    field, public_key, fingerprint = candidates[0]
    return GatewayHostKey(selected_host, selected_port, field, public_key, fingerprint)


def _line_host_fields(line: str) -> tuple[str, ...]:
    parts = line.strip().split()
    if len(parts) < 3 or line.lstrip().startswith("#"):
        return ()
    return tuple(item.strip() for item in parts[0].split(",") if item.strip())


def _line_key_identity(line: str) -> Optional[str]:
    parts = line.strip().split()
    if len(parts) < 3:
        return None
    try:
        return public_key_identity("%s %s" % (parts[1], parts[2]))
    except ValueError:
        return None


def trust_gateway_host_key(
        scanned: GatewayHostKey, *, known_hosts_file: Optional[Path] = None,
) -> HostTrustResult:
    target = Path(known_hosts_file or managed_known_hosts_path()).expanduser()
    expected = expected_known_hosts_field(scanned.host, scanned.port)
    accepted_fields = {expected}
    if scanned.port == 22:
        accepted_fields.add("[%s]:22" % scanned.host)
    existing_lines = []
    if target.is_file():
        existing_lines = target.read_text(encoding="utf-8").splitlines()
    for line in existing_lines:
        fields = set(_line_host_fields(line))
        if not fields.intersection(accepted_fields):
            continue
        identity = _line_key_identity(line)
        if identity == public_key_identity(scanned.public_key):
            return HostTrustResult(target, scanned.host_field, scanned.fingerprint, False)
        raise RuntimeError(
            "HOST_KEY_CONFLICT: this Gateway host already has a different key in %s. "
            "Do not overwrite it automatically; verify the Gateway before removing the old entry." % target
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(str(target.parent), 0o700)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        if target.stat().st_size:
            handle.write("\n")
        handle.write("%s %s\n" % (scanned.host_field, scanned.public_key))
    if os.name != "nt":
        os.chmod(str(target), 0o600)
    return HostTrustResult(target, scanned.host_field, scanned.fingerprint, True)


def trusted_known_hosts_file(
        host: str, port: int = 22, *, known_hosts_file: Optional[Path] = None,
) -> Optional[Path]:
    target = Path(known_hosts_file or managed_known_hosts_path()).expanduser()
    if not target.is_file():
        return None
    expected = expected_known_hosts_field(host, port)
    accepted = {expected}
    if int(port) == 22:
        accepted.add("[%s]:22" % validate_gateway_host(host))
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        if set(_line_host_fields(line)).intersection(accepted) and _line_key_identity(line) is not None:
            return target
    return None


def local_gateway_host_key(
        *, system_name: Optional[str] = None, program_data: Optional[Path] = None,
        etc_ssh: Optional[Path] = None,
) -> GatewayHostKey:
    system = (system_name or platform.system()).strip().lower()
    if system == "windows":
        root = Path(program_data or os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ssh"
    elif system in {"linux", "ubuntu"}:
        root = Path(etc_ssh or "/etc/ssh")
    else:
        raise RuntimeError("Gateway host-key inspection supports Windows and Ubuntu/Linux only.")
    public_path = root / "ssh_host_ed25519_key.pub"
    if not public_path.is_file():
        raise RuntimeError("Gateway ssh-ed25519 host public key was not found: %s" % public_path)
    public_key = validate_public_key(public_path.read_text(encoding="utf-8"))
    return GatewayHostKey(
        host="localhost", port=22, host_field="localhost",
        public_key=public_key, fingerprint=public_key_fingerprint(public_key),
    )
