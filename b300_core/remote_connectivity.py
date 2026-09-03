"""Interactive SSH connectivity check for a saved B300 Gateway profile."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .remote_profile import RemoteGatewayProfile
from .ssh_client import password_ssh_options
from .ssh_identity import resolve_ssh_client_executable

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
        tuple(str(item) for item in argv), stdout=subprocess.PIPE, text=True,
        timeout=timeout, check=False,
    )


def build_connectivity_argv(
        profile: RemoteGatewayProfile, *, ssh_executable: Optional[Path] = None,
) -> tuple[str, ...]:
    selected = profile.validate()
    ssh = Path(ssh_executable) if ssh_executable is not None else resolve_ssh_client_executable("ssh")
    if ssh is None:
        raise RuntimeError("OpenSSH Client is not available. Install or enable OpenSSH Client first.")
    return (
        str(ssh), "-T",
        *password_ssh_options(),
        "-o", "ConnectTimeout=8",
        "-o", "LogLevel=ERROR",
        "-p", str(selected.port),
        "%s@%s" % (selected.user, selected.host),
        "echo %s" % _READY_TOKEN,
    )


def check_remote_connectivity(
        profile: RemoteGatewayProfile, *, runner: CommandRunner = _run,
        ssh_executable: Optional[Path] = None,
) -> RemoteConnectivityResult:
    selected = profile.validate()
    argv = build_connectivity_argv(
        selected, ssh_executable=ssh_executable,
    )
    completed = runner(argv, 15.0)
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    ready = completed.returncode == 0 and _READY_TOKEN in lines
    if ready:
        return RemoteConnectivityResult(
            True, completed.returncode, "%s@%s:%d" % (selected.user, selected.host, selected.port),
            "SSH_READY", "Password-interactive SSH connection succeeded.",
        )
    stderr = (completed.stderr or "").strip()
    message = "Password-interactive SSH connection failed."
    if stderr:
        message += " " + stderr[:500]
    return RemoteConnectivityResult(
        False, completed.returncode, "%s@%s:%d" % (selected.user, selected.host, selected.port),
        "SSH_CONNECT_FAILED", message,
    )
