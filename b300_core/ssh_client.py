"""OpenSSH client options for B300 remote access."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple


def _null_device(system_name: Optional[str] = None) -> str:
    system = (system_name or ("windows" if os.name == "nt" else "")).lower()
    return "NUL" if system == "windows" else "/dev/null"


def _quote_ssh_config_value(value: object) -> str:
    text = str(value)
    if os.name == "nt":
        text = text.replace("\\", "/")
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def validate_managed_ssh_files(identity_file: Optional[Path], known_hosts_file: Optional[Path]) -> Tuple[Path, Path]:
    """Require the selected B300 key and matching B300 known_hosts file."""
    if identity_file is None or not Path(identity_file).is_file():
        raise ValueError("Managed B300 SSH identity file is required and must exist.")
    if known_hosts_file is None or not Path(known_hosts_file).is_file():
        raise ValueError("Managed B300 SSH known_hosts file is required and must exist.")
    return Path(identity_file), Path(known_hosts_file)


def managed_ssh_options(
        identity_file: Optional[Path], known_hosts_file: Optional[Path], *,
        system_name: Optional[str] = None,
) -> Tuple[str, ...]:
    """Return argv elements that prevent OpenSSH from inheriting ambient state."""
    identity, known_hosts = validate_managed_ssh_files(identity_file, known_hosts_file)
    return (
        "-F", "none",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "IdentityFile=none",
        "-i", str(identity),
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=%s" % _quote_ssh_config_value(known_hosts),
        "-o", "GlobalKnownHostsFile=%s" % _null_device(system_name),
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ChallengeResponseAuthentication=no",
        "-o", "GSSAPIAuthentication=no",
        "-o", "HostbasedAuthentication=no",
        "-o", "PubkeyAuthentication=yes",
        "-o", "PreferredAuthentications=publickey",
        "-o", "UpdateHostKeys=no",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "ProxyJump=none",
        "-o", "ProxyCommand=none",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "ControlPersist=no",
        "-o", "ForwardAgent=no",
        "-o", "ForwardX11=no",
        "-o", "ForwardX11Trusted=no",
        "-o", "PermitLocalCommand=no",
    )


def password_ssh_options() -> Tuple[str, ...]:
    """Return ordinary interactive OpenSSH authentication options without secrets."""
    return (
        "-o", "PreferredAuthentications=password,keyboard-interactive",
        "-o", "PasswordAuthentication=yes",
        "-o", "KbdInteractiveAuthentication=yes",
        "-o", "PubkeyAuthentication=no",
    )
