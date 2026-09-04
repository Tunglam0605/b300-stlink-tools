"""Read-only VS Code/Cortex-Debug/GNU Arm readiness inspection for B300."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from platform import system
from typing import Callable, Optional

from .gdb_runtime import resolve_gdb
from .process_startup import child_process_kwargs
from .vscode_bridge import resolve_vscode


@dataclass(frozen=True)
class VsCodeEnvironmentStatus:
    vscode_ready: bool
    cortex_debug_ready: bool
    gdb_ready: bool
    vscode_path: Optional[str] = None
    gdb_path: Optional[str] = None
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.vscode_ready and self.cortex_debug_ready and self.gdb_ready


RunFactory = Callable[..., object]


def _extension_list_launcher(vscode_path: str,
                             platform_name: Optional[str]) -> str:
    """Use VS Code's CLI shim when Code.exe cannot emit CLI output."""
    selected_platform = (platform_name or system()).lower()
    selected = Path(vscode_path)
    if selected_platform in {"windows", "win32", "nt"} and selected.name.lower() == "code.exe":
        command_shim = selected.parent / "bin" / "code.cmd"
        if command_shim.is_file():
            return str(command_shim)
    return vscode_path


def inspect_vscode_environment(*, vscode: Optional[str] = None,
                               gdb: Optional[str] = None,
                               run_factory: RunFactory = subprocess.run,
                               platform_name: Optional[str] = None,
                               timeout_seconds: float = 8.0) -> VsCodeEnvironmentStatus:
    """Inspect local prerequisites without touching ST-Link or the MCU."""
    if timeout_seconds <= 0:
        raise ValueError("VS Code readiness timeout must be positive.")

    try:
        vscode_path = resolve_vscode(vscode)
    except (FileNotFoundError, OSError) as error:
        return VsCodeEnvironmentStatus(
            vscode_ready=False,
            cortex_debug_ready=False,
            gdb_ready=False,
            reason=str(error),
        )

    try:
        extension_launcher = _extension_list_launcher(vscode_path, platform_name)
        result = run_factory(
            (extension_launcher, "--list-extensions"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            **child_process_kwargs(platform_name),
        )
        output = str(getattr(result, "stdout", "") or "")
        returncode = int(getattr(result, "returncode", 1))
        cortex_ready = (
            returncode == 0 and
            any(line.strip().lower() == "marus25.cortex-debug"
                for line in output.splitlines())
        )
    except (OSError, subprocess.SubprocessError):
        cortex_ready = False

    try:
        gdb_path = resolve_gdb(gdb) if gdb else resolve_gdb()
        gdb_ready = True
    except (FileNotFoundError, RuntimeError, OSError):
        gdb_path = None
        gdb_ready = False

    missing = []
    if not cortex_ready:
        missing.append("Cortex-Debug")
    if not gdb_ready:
        missing.append("arm-none-eabi-gdb")
    reason = "" if not missing else "Missing: %s." % ", ".join(missing)
    return VsCodeEnvironmentStatus(
        vscode_ready=True,
        cortex_debug_ready=cortex_ready,
        gdb_ready=gdb_ready,
        vscode_path=vscode_path,
        gdb_path=gdb_path,
        reason=reason,
    )


__all__ = ["VsCodeEnvironmentStatus", "inspect_vscode_environment"]
