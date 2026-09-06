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
    VsCodeExternalProfile,
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

    def _require_live_listener(self, expected: VsCodeBridgeState) -> VsCodeBridgeState:
        current = self.bridge.state
        if not isinstance(current, VsCodeBridgeState):
            return expected
        if current.state != BridgeState.READY or current.gdb_target != expected.gdb_target:
            raise RuntimeError("B300 debug listener changed before VS Code could be opened.")
        if expected.binding is not None and current.binding != expected.binding:
            raise RuntimeError("Gateway generation changed before VS Code could be opened.")
        return current

    def start_local(self, *, probe: ProbeRef, workspace: Path, symbols: Path,
                    force_launch_json: bool = False) -> DebugLaunchResult:
        status = self._require_environment()
        root, image, relative = self._validate_workspace_symbols(workspace, symbols)
        launch_revision = VsCodeExternalProfile.launch_revision(root)
        started = False
        try:
            state = self.bridge.start_local(probe)
            started = True
            if state.state != BridgeState.READY:
                raise RuntimeError("B300 local debug bridge did not become READY.")
            profile = self.bridge.profile(program_relative=relative, gdb_path=status.gdb_path)
            launch = profile.write_launch_json(
                root, force=force_launch_json, expected_revision=launch_revision
            )
            state = self._require_live_listener(state)
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
                     local_gdb_port: int = 0, force_launch_json: bool = False,
                     gateway_snapshot=None, profile_id: Optional[str] = None) -> DebugLaunchResult:
        status = self._require_environment()
        root, image, relative = self._validate_workspace_symbols(workspace, symbols)
        launch_revision = VsCodeExternalProfile.launch_revision(root)
        selected_profile = str(profile_id or "").strip()
        if not selected_profile:
            raise ValueError("Gateway profile identity is required before opening VS Code.")
        started = False
        try:
            snapshot = gateway_snapshot
            if snapshot is None:
                ensure_ready = getattr(session, "ensure_gateway_ready", None)
                if not callable(ensure_ready):
                    raise RuntimeError("Remote SSH session cannot verify Gateway readiness.")
                snapshot = ensure_ready()
            client_kwargs = {
                "local_gdb_port": int(local_gdb_port),
                "snapshot": snapshot,
                "profile_id": selected_profile,
            }
            state = self.bridge.start_client(session, **client_kwargs)
            started = True
            if state.state != BridgeState.READY:
                raise RuntimeError("B300 remote GDB tunnel did not become READY.")
            profile = self.bridge.profile(program_relative=relative, gdb_path=status.gdb_path)
            launch = profile.write_launch_json(
                root, force=force_launch_json, expected_revision=launch_revision
            )
            state = self._require_live_listener(state)
            launch_vscode(root, executable=status.vscode_path)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            if started:
                self.bridge.stop()
            raise

    def synchronize_client(self, *, session: RemoteSession, workspace: Path,
                           symbols: Path, gateway_snapshot, profile_id: str,
                           local_gdb_port: int = 0,
                           force_launch_json: bool = False) -> DebugLaunchResult:
        """Rebind a recovered Gateway and update B300 config without auto-attaching."""
        status = self._require_environment()
        root, image, relative = self._validate_workspace_symbols(workspace, symbols)
        launch_revision = VsCodeExternalProfile.launch_revision(root)
        state = self.bridge.sync_client(
            session, snapshot=gateway_snapshot, profile_id=profile_id,
            local_gdb_port=int(local_gdb_port),
        )
        if state.state != BridgeState.READY:
            raise RuntimeError("B300 remote GDB tunnel did not become READY after Gateway recovery.")
        try:
            profile = self.bridge.profile(program_relative=relative, gdb_path=status.gdb_path)
            launch = profile.write_launch_json(
                root, force=force_launch_json, expected_revision=launch_revision
            )
            state = self._require_live_listener(state)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            self.bridge.stop()
            raise

    def stop(self) -> VsCodeBridgeState:
        return self.bridge.stop()


__all__ = ["DebugLaunchResult", "VsCodeDebugController"]
