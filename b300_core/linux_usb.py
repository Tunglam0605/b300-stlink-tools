"""Read-only Linux ST-Link udev diagnostics and explicitly confirmed setup."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple


UDEV_RULE_FILENAME = "49-b300-stlink.rules"
DEFAULT_UDEV_RULE_PATH = Path("/etc/udev/rules.d") / UDEV_RULE_FILENAME
B300_UDEV_RULE = (
    'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="374?", '
    'MODE="0660", GROUP="plugdev", TAG+="uaccess"\n'
)


class SystemChangeConfirmationRequired(RuntimeError):
    reason_code = "SYSTEM_CHANGE_CONFIRMATION_REQUIRED"


@dataclass(frozen=True)
class LinuxUsbSetupReport:
    supported: bool
    rule_installed: bool
    dry_run: bool
    changed: bool
    reason_code: str
    message: str
    next_action: str
    rule_path: Path
    commands: Tuple[Tuple[str, ...], ...] = ()


def _inspect_rule(rule_path: Path) -> bool:
    try:
        return Path(rule_path).read_text(encoding="utf-8") == B300_UDEV_RULE
    except FileNotFoundError:
        return False


def _dry_run_report(rule_path: Path) -> LinuxUsbSetupReport:
    return LinuxUsbSetupReport(
        supported=True,
        rule_installed=False,
        dry_run=True,
        changed=False,
        reason_code="UDEV_RULE_INSTALL_AVAILABLE",
        message="The canonical B300 ST-Link udev rule is not installed.",
        next_action=(
            "Review the proposed udev change, then rerun setup with both "
            "--install-udev-rule and --confirm-system-change."
        ),
        rule_path=rule_path,
    )


def _find_required(which: Callable[[str], Optional[str]], name: str) -> str:
    selected = which(name)
    if not selected:
        raise FileNotFoundError("Linux setup requires %s." % name)
    return str(selected)


def _install_commands(
        staged_rule: Path, rule_path: Path,
        which: Callable[[str], Optional[str]]) -> Tuple[Tuple[str, ...], ...]:
    elevator = which("pkexec")
    if not elevator:
        elevator = which("sudo")
    if not elevator:
        raise FileNotFoundError("Linux setup requires pkexec or sudo for the confirmed change.")
    install = _find_required(which, "install")
    udevadm = _find_required(which, "udevadm")
    prefix = str(elevator)
    return (
        (
            prefix, install, "-o", "root", "-g", "root", "-m", "0644",
            str(staged_rule), str(rule_path),
        ),
        (prefix, udevadm, "control", "--reload-rules"),
        (
            prefix, udevadm, "trigger", "--subsystem-match=usb",
            "--attr-match=idVendor=0483", "--attr-match=idProduct=374?",
        ),
    )


def perform_linux_usb_setup(
        *, system: Optional[str] = None,
        rule_path: Path = DEFAULT_UDEV_RULE_PATH,
        install_requested: bool = False,
        confirmed: bool = False,
        runner: Callable = subprocess.run,
        which: Callable[[str], Optional[str]] = shutil.which,
        staging_parent: Optional[Path] = None,
        announce: Optional[Callable[[LinuxUsbSetupReport], None]] = None,
        ) -> LinuxUsbSetupReport:
    """Inspect by default; mutate only after both explicit authorization flags."""
    selected_system = platform.system() if system is None else system
    destination = Path(rule_path).resolve()
    if selected_system.lower() != "linux":
        return LinuxUsbSetupReport(
            supported=False,
            rule_installed=False,
            dry_run=True,
            changed=False,
            reason_code="LINUX_SETUP_UNSUPPORTED",
            message="The udev setup operation is supported only on Linux.",
            next_action="No system change was attempted.",
            rule_path=destination,
        )
    if _inspect_rule(destination):
        return LinuxUsbSetupReport(
            supported=True,
            rule_installed=True,
            dry_run=True,
            changed=False,
            reason_code="UDEV_RULE_PRESENT",
            message="The canonical B300 ST-Link udev rule is already installed.",
            next_action="Replug the ST-Link probe, then run b300-stlink doctor as your normal user.",
            rule_path=destination,
        )
    if bool(install_requested) != bool(confirmed):
        raise SystemChangeConfirmationRequired(
            "Installing the udev rule requires both --install-udev-rule and "
            "--confirm-system-change; no system command was run."
        )
    if not install_requested:
        return _dry_run_report(destination)

    parent = Path(staging_parent) if staging_parent is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix="b300-udev-", dir=str(parent) if parent is not None else None) as temporary:
        staged_rule = Path(temporary) / UDEV_RULE_FILENAME
        with staged_rule.open("x", encoding="utf-8", newline="\n") as output:
            output.write(B300_UDEV_RULE)
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            staged_rule.chmod(0o600)
        commands = _install_commands(staged_rule, destination, which)
        plan = LinuxUsbSetupReport(
            supported=True,
            rule_installed=False,
            dry_run=False,
            changed=False,
            reason_code="UDEV_RULE_INSTALL_PLANNED",
            message="Install the canonical B300 ST-Link udev rule and reload only USB rules.",
            next_action="Authenticate only the displayed rule copy, reload, and narrow USB trigger.",
            rule_path=destination,
            commands=commands,
        )
        if announce is not None:
            announce(plan)
        for command in commands:
            command_stdin = None if Path(command[0]).name == "sudo" else subprocess.DEVNULL
            result = runner(
                list(command),
                check=False,
                shell=False,
                stdin=command_stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if int(result.returncode) != 0:
                raise RuntimeError(
                    "Confirmed udev setup command failed with exit code %d." % result.returncode
                )
        return LinuxUsbSetupReport(
            supported=True,
            rule_installed=True,
            dry_run=False,
            changed=True,
            reason_code="UDEV_RULE_INSTALLED",
            message="The canonical B300 ST-Link udev rule was installed and reloaded.",
            next_action="Unplug and replug the ST-Link probe, then run b300-stlink doctor without sudo.",
            rule_path=destination,
            commands=commands,
        )
