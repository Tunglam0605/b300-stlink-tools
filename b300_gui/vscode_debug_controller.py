"""Controller boundary between the v0.18 Qt view and B300 debug backend.

The view owns presentation only.  This controller owns explicit VS Code bridge
operations and guarantees that a failed profile/launch step releases the debug
HardwareSession again.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import uuid
from typing import Callable, Optional
from PySide6.QtCore import QObject, Signal

from b300_core.models import ProbeRef
from b300_core.gateway_lease import GatewayLeasePublicSnapshot
from b300_core.remote_session import RemoteSession
from b300_core.gateway_lease_client import GatewayLeaseClient
from b300_core.gateway_status import GatewaySnapshot
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


class GuiDispatcher(QObject):
    """Queue callbacks onto the Qt object affinity that created this helper."""
    dispatched = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.dispatched.connect(lambda callback: callback())

    def submit(self, callback: Callable[[], None]) -> None:
        self.dispatched.emit(callback)


class VsCodeDebugController:
    """Small orchestration facade used by ``MainWindowV18``."""

    def __init__(self, *, debug_service=None, context=None, ui_dispatcher=None,
                 lease_client_factory=GatewayLeaseClient) -> None:
        self.bridge = VsCodeDebugBridge(debug_service=debug_service)
        self.bridge.set_last_client_detached_handler(self._on_last_client_detached)
        self._environment: Optional[VsCodeEnvironmentStatus] = None
        self._monitor_handoff: Optional[Callable[[], bool]] = None
        self._client_reclaim_lock = threading.Lock()
        self._client_reclaimed = False
        self._context = context
        self._ui_dispatcher = ui_dispatcher
        self._lease_token = None
        self._lease_client_factory = lease_client_factory
        self._gateway_lease_client = None

    def set_ui_dispatcher(self, dispatcher) -> None:
        self._ui_dispatcher = dispatcher

    def set_context(self, context) -> None:
        self._context = context

    def _publish_debug(self, state: VsCodeBridgeState) -> None:
        if self._context is None or state.state != BridgeState.READY:
            return
        if self._lease_token is None:
            self._lease_token = uuid.uuid4().hex
        updates = dict(
            owner_kind="DEBUGGING", lease_token=self._lease_token, gdb_endpoint=state.gdb_target,
            reason=state.detail,
        )
        if state.binding is not None:
            updates["ssh_generation"] = state.binding.session_generation
        self._context.apply_device_state(**updates)

    def _release_debug(self, reason: str) -> None:
        if self._context is not None:
            updates = dict(owner_kind=None, target_state=None, gdb_endpoint=None,
                           tcl_endpoint=None, reason=reason)
            if self._lease_token is not None:
                updates["lease_token"] = self._lease_token
            self._context.apply_device_state(**updates)
        self._lease_token = None

    def _on_gateway_lease_lost(self) -> None:
        def teardown():
            try:
                self.bridge.stop()
            finally:
                self._release_debug("Gateway lease lost")
        dispatcher = self._ui_dispatcher
        if dispatcher is None:
            teardown()
        else:
            dispatcher.submit(teardown)

    @property
    def state(self) -> VsCodeBridgeState:
        return self.bridge.state

    @property
    def environment(self) -> Optional[VsCodeEnvironmentStatus]:
        return self._environment

    def inspect_environment(self) -> VsCodeEnvironmentStatus:
        self._environment = inspect_vscode_environment()
        return self._environment

    def set_monitor_handoff(self, handoff: Callable[[], bool]) -> None:
        """Register the bounded Monitor shutdown used before local OpenOCD starts."""
        self._monitor_handoff = handoff

    def _handoff_monitor(self) -> None:
        handoff = self._monitor_handoff
        if handoff is not None and not handoff():
            raise RuntimeError("Monitor did not become idle before VS Code debug started.")

    def _on_last_client_detached(self, lifecycle_generation: int) -> None:
        """Release only the B300 bridge after its final observed GDB client leaves."""
        state = self.bridge.stop_if_generation(lifecycle_generation)
        if state.state == BridgeState.STOPPED:
            with self._client_reclaim_lock:
                self._client_reclaimed = True
            release = lambda: self._release_debug("VS Code client released debug ownership")
            dispatcher = self._ui_dispatcher
            if dispatcher is None:
                release()
            else:
                dispatcher.submit(release)

    def observe_gateway_snapshot(self, snapshot) -> bool:
        observed = bool(self.bridge.observe_gateway_snapshot(snapshot))
        with self._client_reclaim_lock:
            reclaimed = self._client_reclaimed
            self._client_reclaimed = False
        return observed or reclaimed

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
            self._handoff_monitor()
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
            self._publish_debug(state)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            if started:
                self.bridge.stop()
            self._release_debug("VS Code debug launch failed")
            raise

    def start_gateway(self, *, probe: ProbeRef) -> VsCodeBridgeState:
        self._handoff_monitor()
        state = self.bridge.start_gateway(probe)
        if state.state != BridgeState.READY:
            try:
                self.bridge.stop()
                self._release_debug("Gateway debug start failed")
            finally:
                raise RuntimeError("B300 Gateway did not become READY.")
        self._publish_debug(state)
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
            if (self._gateway_lease_client is None
                    and getattr(session, "supports_gateway_leases", False) is True
                    and callable(getattr(session, "ensure_gateway_agent", None))
                    and callable(getattr(session, "acquire_gateway", None))):
                try:
                    self._gateway_lease_client = self._lease_client_factory(
                        session, client_id=selected_profile,
                        client_label=selected_profile,
                        on_lost=self._on_gateway_lease_lost,
                    )
                except TypeError:
                    self._gateway_lease_client = self._lease_client_factory(
                        session, client_id=selected_profile, client_label=selected_profile,
                    )
                self._gateway_lease_client.start("VSCODE_DEBUG")
            snapshot = gateway_snapshot
            if self._gateway_lease_client is not None and self._gateway_lease_client.grant is not None:
                # A caller supplied snapshot cannot override authoritative lease evidence.
                snapshot = None
            if snapshot is None:
                grant = self._gateway_lease_client.grant if self._gateway_lease_client else None
                lease_public = None
                if grant is not None:
                    try:
                        lease_public = GatewayLeasePublicSnapshot.from_record(grant.public)
                    except (TypeError, ValueError) as error:
                        raise RuntimeError("Gateway lease binding is invalid.") from error
                if lease_public is None or not lease_public.gdb_endpoint:
                    if (self._gateway_lease_client is None
                            and getattr(session, "supports_gateway_leases", False) is not True):
                        ensure_ready = getattr(session, "ensure_gateway_ready", None)
                        if not callable(ensure_ready):
                            raise RuntimeError("Gateway lease binding is missing.")
                        snapshot = ensure_ready()
                    else:
                        raise RuntimeError("Gateway lease binding is missing.")
                if snapshot is not None:
                    pass
                else:
                    snapshot = GatewaySnapshot.from_record({
                        "schema_version": 1,
                        "instance_id": lease_public.gateway_instance_id,
                        "generation": lease_public.gateway_generation,
                        "sequence": 0,
                        "state": "READY",
                        "reason_code": lease_public.reason_code,
                        "selected_probe": {"serial": lease_public.probe_serial or "unknown"},
                        "gdb_endpoint": lease_public.gdb_endpoint,
                        "tcl_endpoint": lease_public.tcl_endpoint,
                        "cpu_state": "halted",
                        "evidence_age_ms": 0,
                    })
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
            self._publish_debug(state)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            if started:
                self.bridge.stop()
            if self._gateway_lease_client is not None:
                self._gateway_lease_client.close()
                self._gateway_lease_client = None
            self._release_debug("VS Code remote debug launch failed")
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
            self._publish_debug(state)
            return DebugLaunchResult(state, launch, root, image)
        except Exception:
            self.bridge.stop()
            self._release_debug("Gateway debug synchronization failed")
            raise

    def stop(self) -> VsCodeBridgeState:
        state = self.bridge.stop()
        if self._gateway_lease_client is not None:
            self._gateway_lease_client.close()
            self._gateway_lease_client = None
        self._release_debug("VS Code debug stopped")
        return state


__all__ = ["DebugLaunchResult", "GuiDispatcher", "VsCodeDebugController"]
