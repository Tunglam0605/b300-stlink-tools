"""Platform-specific handoff for already verified update packages."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .release_manifest import EXPECTED_UPDATE_FILENAMES
from .update_platform import UpdatePlatform


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
    quoted = shlex.quote(str(source))
    if selected in {
        UpdatePlatform.LINUX_X64_APPIMAGE,
        UpdatePlatform.LINUX_ARM64_APPIMAGE,
    }:
        instructions = "chmod +x %s\n%s" % (quoted, quoted)
    else:
        instructions = "sudo apt install %s" % quoted
    return InstallPlan(
        selected, source, False, open_directory=source.parent,
        instructions=instructions,
    )


def launch_install_plan(plan: InstallPlan) -> None:
    if not plan.managed or plan.program is None:
        raise ValueError("Only a managed installation plan can be launched.")
    subprocess.Popen(
        [str(plan.program), *plan.arguments],
        close_fds=True,
        shell=False,
    )
