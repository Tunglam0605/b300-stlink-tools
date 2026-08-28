"""Platform-specific handoff for already verified update packages."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .release_manifest import EXPECTED_UPDATE_FILENAMES
from .update_platform import UpdatePlatform


_LINUX_APPIMAGE_PLATFORMS = {
    UpdatePlatform.LINUX_X64_APPIMAGE,
    UpdatePlatform.LINUX_ARM64_APPIMAGE,
}
_LINUX_DEB_PLATFORMS = {
    UpdatePlatform.LINUX_X64_DEB,
    UpdatePlatform.LINUX_ARM64_DEB,
}


@dataclass(frozen=True)
class InstallPlan:
    platform: UpdatePlatform
    package: Path
    managed: bool
    program: Optional[Path] = None
    arguments: Tuple[str, ...] = ()
    open_directory: Optional[Path] = None
    instructions: str = ""


def prepare_install(package: Path, platform_name: UpdatePlatform) -> InstallPlan:
    selected = UpdatePlatform(platform_name)
    source = Path(package)
    if not source.is_absolute():
        raise ValueError("Verified update package path must be absolute.")
    source = source.resolve()
    if not source.is_file():
        raise ValueError("Verified update package must be a regular file.")
    expected_name = EXPECTED_UPDATE_FILENAMES[selected.value]
    if source.name != expected_name:
        raise ValueError("Update package filename does not match the selected platform.")
    if selected == UpdatePlatform.WINDOWS_X64:
        return InstallPlan(
            selected, source, True, program=source,
            arguments=("/CURRENTUSER", "/CLOSEAPPLICATIONS"),
        )
    if selected in _LINUX_APPIMAGE_PLATFORMS | _LINUX_DEB_PLATFORMS:
        return InstallPlan(selected, source, True)
    raise ValueError("Unsupported managed update platform: %s" % selected.value)


def _linux_helper_command(plan: InstallPlan, parent_pid: int) -> list[str]:
    if getattr(sys, "frozen", False):
        command = [str(Path(sys.executable).resolve())]
    else:
        entry = Path(__file__).resolve().parent.parent / "b300_gui_entry.py"
        if not entry.is_file():
            raise RuntimeError("B300 GUI update helper entry point is unavailable.")
        command = [str(Path(sys.executable).resolve()), str(entry)]
    command.extend([
        "--apply-verified-update",
        "--platform", plan.platform.value,
        "--package", str(plan.package),
        "--parent-pid", str(parent_pid),
    ])
    if plan.platform in _LINUX_APPIMAGE_PLATFORMS:
        current_appimage = os.environ.get("APPIMAGE")
        if not current_appimage:
            raise RuntimeError(
                "The running AppImage path is unavailable; update cannot replace it safely."
            )
        target = Path(current_appimage).expanduser()
        if not target.is_absolute():
            raise RuntimeError("The running AppImage path must be absolute.")
        command.extend(["--appimage-target", str(target.resolve())])
    return command


def launch_install_plan(plan: InstallPlan) -> None:
    if not plan.managed:
        raise ValueError("Only a managed installation plan can be launched.")
    if plan.platform == UpdatePlatform.WINDOWS_X64:
        if plan.program is None:
            raise ValueError("Windows managed installer program is missing.")
        subprocess.Popen(
            [str(plan.program), *plan.arguments],
            close_fds=True,
            shell=False,
        )
        return

    if plan.platform in _LINUX_DEB_PLATFORMS:
        if shutil.which("pkexec") is None:
            raise OSError("pkexec is required for graphical Ubuntu package installation.")
        if shutil.which("apt-get") is None:
            raise OSError("apt-get is required for Ubuntu package installation.")
    command = _linux_helper_command(plan, os.getpid())
    subprocess.Popen(
        command,
        close_fds=True,
        shell=False,
        start_new_session=True,
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
