"""Interactive SSH connectivity check for a saved B300 Gateway profile."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .remote_profile import RemoteGatewayProfile
from .ssh_client import managed_ssh_options, password_ssh_options
from .ssh_host_trust import trusted_known_hosts_file
from .ssh_identity import managed_identity_file, resolve_ssh_client_executable
from .subprocess_env import external_command_env

_READY_TOKEN = "B300_SSH_READY"
_AUTO_MANAGED_AUTH = object()


@dataclass(frozen=True)
class RemoteConnectivityResult:
    ready: bool
    exit_code: int
    gateway: str
    reason_code: str
    message: str
    auth_mode: str = "password_interactive"
    host_key_memory: str = "OpenSSH default known_hosts"


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


def _run(argv: Sequence[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        tuple(str(item) for item in argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=timeout, check=False, env=external_command_env(),
    )


def _managed_auth_files(profile: RemoteGatewayProfile):
    identity = managed_identity_file()
    known_hosts = trusted_known_hosts_file(profile.host, profile.port)
    return (identity, known_hosts) if identity is not None and known_hosts is not None else None


def build_connectivity_argv(
        profile: RemoteGatewayProfile, *, ssh_executable: Optional[Path] = None,
        managed_auth=None,
) -> tuple[str, ...]:
    selected = profile.validate()
    ssh = Path(ssh_executable) if ssh_executable is not None else resolve_ssh_client_executable("ssh")
    if ssh is None:
        raise RuntimeError("OpenSSH Client is not available. Install or enable OpenSSH Client first.")
    auth_options = (
        managed_ssh_options(managed_auth[0], managed_auth[1])
        if managed_auth is not None else password_ssh_options()
    )
    return (
        str(ssh), "-T",
        *auth_options,
        "-o", "ConnectTimeout=8",
        "-o", "LogLevel=ERROR",
        "-p", str(selected.port),
        "%s@%s" % (selected.user, selected.host),
        "echo %s" % _READY_TOKEN,
    )


def check_remote_connectivity(
        profile: RemoteGatewayProfile, *, runner: CommandRunner = _run,
        ssh_executable: Optional[Path] = None, managed_auth=_AUTO_MANAGED_AUTH,
) -> RemoteConnectivityResult:
    selected = profile.validate()
    auth = _managed_auth_files(selected) if managed_auth is _AUTO_MANAGED_AUTH else managed_auth
    argv = build_connectivity_argv(
        selected, ssh_executable=ssh_executable, managed_auth=auth,
    )
    auth_mode = "managed_key" if auth is not None else "password_interactive"
    host_key_memory = "B300 managed known_hosts" if auth is not None else "OpenSSH default known_hosts"
    try:
        completed = runner(argv, 15.0)
    except subprocess.TimeoutExpired:
        return RemoteConnectivityResult(
            False, 124, "%s@%s:%d" % (selected.user, selected.host, selected.port),
            "SSH_CONNECT_FAILED", "SSH connection timed out before authentication completed.",
            auth_mode, host_key_memory,
        )
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    ready = completed.returncode == 0 and _READY_TOKEN in lines
    if ready:
        return RemoteConnectivityResult(
            True, completed.returncode, "%s@%s:%d" % (selected.user, selected.host, selected.port),
            "SSH_READY", "SSH connection succeeded.", auth_mode, host_key_memory,
        )
    stderr = (completed.stderr or "").strip()
    message = "SSH connection failed."
    if stderr:
        message += " " + stderr[:500]
    return RemoteConnectivityResult(
        False, completed.returncode, "%s@%s:%d" % (selected.user, selected.host, selected.port),
        "SSH_CONNECT_FAILED", message, auth_mode, host_key_memory,
    )
