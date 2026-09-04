"""Controller boundary between the v0.18 Qt view and B300 debug backend.

The view owns presentation only.  This controller owns explicit VS Code bridge
operations and guarantees that a failed profile/launch step releases the debug
HardwareSession again.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from b300_core.models import ProbeRef
from b300_core.remote_session import RemoteSession
from b300_core.vscode_bridge import (
    BridgeState,
    VsCodeBridgeState,
    VsCodeDebugBridge,
    launch_vscode,
)
from b300_core.vscode_environment import VsCodeEnvironmentStatus, inspect_vscode_environment


@dataclass(frozen=True)
class DebugLaunchResult:
    state: VsCodeBridgeState
    launch_json: Path
    workspace: Path
    symbols: Path


class VsCodeDebugController:
    """Small orchestration facade used by ``MainWindowV18``."""

    def __init__(self, *, debug_service=None) -> None:
        self.bridge = VsCodeDebugBridge(debug_service=debug_service)
        self._environment: Optional[VsCodeEnvironmentStatus] = None

    @property
    def state(self) -> VsCodeBridgeState:
        return self.bridge.state

    @property
    def environment(self) -> Optional[VsCodeEnvironmentStatus]:
        return self._environment

    def inspect_environment(self) -> VsCodeEnvironmentStatus:
        self._environment = inspect_vscode_environment()
        return self._environment

    def _require_environment(self) -> VsCodeEnvironmentStatus:
        status = self._environment or self.inspect_environment()
        if not status.ready:
            raise RuntimeError(status.reason or "VS Code debug environment is not ready.")
        return status

    @staticmethod
    def _validate_workspace_symbols(workspace: Path, symbols: Path) -> tuple[Path, Path, str]:
        root = Path(workspace).expanduser().resolve()
        image = Path(symbols).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("VS Code workspace directory does not exist.")
        if not image.is_file():
            raise ValueError("ELF/AXF symbol file does not exist.")
        if image.suffix.lower() not in {".elf", ".axf"}:
            raise ValueError("Debug symbols must be an .elf or .axf file.")
        try:
            relative = image.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError(
                "ELF/AXF must be inside the selected VS Code workspace so launch.json remains portable."
            ) from error
        return root, image, relative

    @staticmethod
    def _check_launch_json(workspace: Path, force: bool) -> None:
        launch = workspace / ".vscode" / "launch.json"
        if launch.exists() and not force:
            raise FileExistsError("VS Code launch.json already exists: %s" % launch)

    def start_local(self, *, probe: ProbeRef, workspace: Path, symbols: Path,
                    force_launch_json: bool = False) -> DebugLaunchResult:
        status = self._require_environment()
        root, image, relative = self._validate_workspace_symbols(workspace, symbols)
        self._check_launch_json(root, force_launch_json)
        started = False
        try:
            state = self.bridge.start_local(probe)
            started = True
            if state.state != BridgeState.READY:
                raise RuntimeError("B300 local debug bridge did not become READY.")
            profile = self.bridge.profile(program_relative=relative, gdb_path=status.gdb_path)
            launch = profile.write_launch_json(root, force=force_launch_json)
            launch_vscode(root, executable=status.vscode_path)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            if started:
                self.bridge.stop()
            raise

    def start_gateway(self, *, probe: ProbeRef) -> VsCodeBridgeState:
        state = self.bridge.start_gateway(probe)
        if state.state != BridgeState.READY:
            try:
                self.bridge.stop()
            finally:
                raise RuntimeError("B300 Gateway did not become READY.")
        return state

    def start_client(self, *, session: RemoteSession, workspace: Path, symbols: Path,
                     local_gdb_port: int = 0, force_launch_json: bool = False) -> DebugLaunchResult:
        status = self._require_environment()
        root, image, relative = self._validate_workspace_symbols(workspace, symbols)
        self._check_launch_json(root, force_launch_json)
        started = False
        try:
            state = self.bridge.start_client(session, local_gdb_port=int(local_gdb_port))
            started = True
            if state.state != BridgeState.READY:
                raise RuntimeError("B300 remote GDB tunnel did not become READY.")
            profile = self.bridge.profile(program_relative=relative, gdb_path=status.gdb_path)
            launch = profile.write_launch_json(root, force=force_launch_json)
            launch_vscode(root, executable=status.vscode_path)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            if started:
                self.bridge.stop()
            raise

    def stop(self) -> VsCodeBridgeState:
        return self.bridge.stop()


__all__ = ["DebugLaunchResult", "VsCodeDebugController"]
