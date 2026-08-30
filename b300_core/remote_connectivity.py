"""Strict non-interactive SSH connectivity check for a saved B300 Gateway profile."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .remote_profile import RemoteGatewayProfile
from .ssh_host_trust import trusted_known_hosts_file
from .ssh_identity import managed_identity_file, resolve_ssh_client_executable

_READY_TOKEN = "B300_SSH_READY"


@dataclass(frozen=True)
class RemoteConnectivityResult:
    ready: bool
    exit_code: int
    gateway: str
    reason_code: str
    message: str


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


def _run(argv: Sequence[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        tuple(str(item) for item in argv), capture_output=True, text=True,
        timeout=timeout, check=False,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
    )


def build_connectivity_argv(
        profile: RemoteGatewayProfile, *, ssh_executable: Optional[Path] = None,
        identity_file: Optional[Path] = None, known_hosts_file: Optional[Path] = None,
) -> tuple[str, ...]:
    selected = profile.validate()
    ssh = Path(ssh_executable) if ssh_executable is not None else resolve_ssh_client_executable("ssh")
    if ssh is None:
        raise RuntimeError("OpenSSH Client is not available. Run `gateway client-setup` first.")
    identity = Path(identity_file) if identity_file is not None else managed_identity_file()
    if identity is None or not identity.is_file():
        raise RuntimeError("B300 Client SSH identity is not ready. Run `gateway client-setup` first.")
    known = Path(known_hosts_file) if known_hosts_file is not None else trusted_known_hosts_file(selected.host, selected.port)
    if known is None or not known.is_file():
        raise RuntimeError("Gateway host is not trusted in B300 known_hosts. Run `gateway client-setup` with the verified fingerprint.")
    return (
        str(ssh), "-T",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=%s" % known,
        "-o", "IdentitiesOnly=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ConnectTimeout=8",
        "-o", "LogLevel=ERROR",
        "-p", str(selected.port),
        "-i", str(identity),
        "%s@%s" % (selected.user, selected.host),
        "echo %s" % _READY_TOKEN,
    )


def check_remote_connectivity(
        profile: RemoteGatewayProfile, *, runner: CommandRunner = _run,
        ssh_executable: Optional[Path] = None, identity_file: Optional[Path] = None,
        known_hosts_file: Optional[Path] = None,
) -> RemoteConnectivityResult:
    selected = profile.validate()
    argv = build_connectivity_argv(
        selected, ssh_executable=ssh_executable, identity_file=identity_file,
        known_hosts_file=known_hosts_file,
    )
    completed = runner(argv, 15.0)
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    ready = completed.returncode == 0 and _READY_TOKEN in lines
    if ready:
        return RemoteConnectivityResult(
            True, completed.returncode, "%s@%s:%d" % (selected.user, selected.host, selected.port),
            "SSH_READY", "Managed SSH key + strict host trust connection succeeded.",
        )
    stderr = (completed.stderr or "").strip()
    message = "Managed SSH connection failed."
    if stderr:
        message += " " + stderr[:500]
    return RemoteConnectivityResult(
        False, completed.returncode, "%s@%s:%d" % (selected.user, selected.host, selected.port),
        "SSH_CONNECT_FAILED", message,
    )
