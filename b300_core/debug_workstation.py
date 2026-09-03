"""Mode-aware Local/Client debug orchestration for the v0.15 engineering workstation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .debug_memory import DebugMemoryBackend, DebugMemoryBlock
from .debug_session import DebugSession, DebugSessionConfig, DebugSessionInfo
from .debug_snapshot import DebugHaltSnapshot, DebugSnapshotBackend
from .debug_symbols import DebugSourceTarget, DebugSymbolBrowserBackend, DebugSymbolItem
from .debug_workspace import DebugWorkspaceBackend
from .internal_remote import create_internal_remote_session
from .live_session import (
    ClientLiveMonitorConfig,
    LiveMonitorSession,
    LiveMonitorSessionInfo,
    LocalLiveMonitorConfig,
)
from .remote_profile import RemoteGatewayProfile
from .remote_session import CredentialStore, RemoteSession, RemoteSessionState


@dataclass(frozen=True)
class DebugConnectionState:
    mode: str
    ssh: str
    gdb: str
    tcl: str
    target: str
    pc: Optional[int]
    symbols: Optional[str]
    remote_endpoint: Optional[str]
    live: str = "inactive"
    sample_rate_hz: Optional[float] = None


class DebugWorkstationController:
    """Single non-Qt facade for the operator-facing v0.15 Debug Workstation.

    SSH authentication outlives individual Debug/Live operations. Qt consumers should
    use this facade rather than importing raw GDB/MI, SSH tunnel, or ELF parser modules.
    """

    def __init__(self, *, debug_session: Optional[DebugSession] = None,
                 remote_session: Optional[RemoteSession] = None,
                 live_session: Optional[LiveMonitorSession] = None) -> None:
        self.debug_session = debug_session or DebugSession()
        self.remote_session = remote_session
        self.live_session = live_session or LiveMonitorSession()
        self.workspace: Optional[DebugWorkspaceBackend] = None
        self.snapshot_backend: Optional[DebugSnapshotBackend] = None
        self.memory_backend: Optional[DebugMemoryBackend] = None
        self.symbol_browser: Optional[DebugSymbolBrowserBackend] = None
        self.mode = "disconnected"
        self._symbols: Optional[str] = None
        self._last_info: Optional[DebugSessionInfo] = None
        self._live_info: Optional[LiveMonitorSessionInfo] = None
        self._live_rate_hz: Optional[float] = None

    @property
    def interactive_active(self) -> bool:
        return bool(self.debug_session.active)

    @property
    def live_active(self) -> bool:
        return bool(self.live_session.active)

    @property
    def live_running(self) -> bool:
        return bool(self.live_session.running)

    def set_remote_session(self, session: RemoteSession) -> None:
        if self.interactive_active or self.live_active:
            raise RuntimeError("Cannot replace the RemoteSession while Debug Studio operations are active.")
        self.remote_session = session
        self.mode = "client"

    def configure_internal_remote(
        self,
        profile: RemoteGatewayProfile,
        *,
        credential_store: Optional[CredentialStore] = None,
        keepalive_seconds: int = 15,
    ) -> RemoteSession:
        """Configure normal v0.15 Client mode without known_hosts/fingerprint ceremony."""
        session = create_internal_remote_session(
            profile,
            credential_store=credential_store,
            keepalive_seconds=keepalive_seconds,
        )
        self.set_remote_session(session)
        return session

    def remote_login(self, password: Optional[str] = None, *, remember: bool = False,
                     timeout_seconds: float = 30.0) -> RemoteSessionState:
        if self.remote_session is None:
            raise RuntimeError("Client mode has no RemoteSession configured.")
        self.mode = "client"
        return self.remote_session.ensure_connected(
            password, remember=remember, timeout_seconds=timeout_seconds,
        )

    def has_remembered_remote_password(self) -> bool:
        if self.remote_session is None:
            return False
        try:
            return bool(self.remote_session.credential_store.load(self.remote_session.profile))
        except Exception:
            return False

    def _attach_workspace(self) -> None:
        self.workspace = DebugWorkspaceBackend(
            self.debug_session.gdb,
            target_state_provider=self.debug_session.target_poll,
        )
        self.snapshot_backend = DebugSnapshotBackend(self.workspace)
        self.memory_backend = DebugMemoryBackend(
            self.debug_session.gdb,
            target_state_provider=self.debug_session.target_poll,
        )

    def _require_workspace(self) -> DebugWorkspaceBackend:
        if not self.interactive_active or self.workspace is None:
            raise RuntimeError("Interactive Debug workspace is not CONNECTED.")
        return self.workspace

    def _require_snapshot_backend(self) -> DebugSnapshotBackend:
        self._require_workspace()
        if self.snapshot_backend is None:
            raise RuntimeError("Debug snapshot backend is unavailable.")
        return self.snapshot_backend

    def _require_memory_backend(self) -> DebugMemoryBackend:
        self._require_workspace()
        if self.memory_backend is None:
            raise RuntimeError("Debug Memory backend is unavailable.")
        return self.memory_backend

    def start_local(self, config: DebugSessionConfig) -> DebugSessionInfo:
        if self.interactive_active:
            raise RuntimeError("Interactive Debug is already active.")
        if self.live_active:
            raise RuntimeError("Stop Local Live Monitor before starting Local Interactive Debug.")
        info = self.debug_session.start(config)
        self.mode = "local"
        self._symbols = info.symbols
        self._last_info = info
        self._attach_workspace()
        return info

    def start_client(self, symbol_file: Optional[Path]) -> DebugSessionInfo:
        if self.interactive_active:
            raise RuntimeError("Interactive Debug is already active.")
        remote = self.remote_session
        if remote is None:
            raise RuntimeError("Client mode has no RemoteSession configured.")
        health = remote.check_health()
        if not health.authenticated:
            raise RuntimeError("Client SSH session is not connected.")
        gdb_forward, tcl_forward = remote.open_debug_forwards()
        info = self.debug_session.start_external(
            symbol_file=symbol_file,
            gdb_host=gdb_forward.local_host,
            gdb_port=gdb_forward.local_port,
            tcl_host=tcl_forward.local_host,
            tcl_port=tcl_forward.local_port,
        )
        self.mode = "client"
        self._symbols = info.symbols
        self._last_info = info
        self._attach_workspace()
        return info

    # ------------------------------------------------------------------
    # Interactive target control
    # ------------------------------------------------------------------
    def halt_target(self) -> str:
        self._require_workspace()
        return self.debug_session.halt()

    def run_target(self) -> str:
        self._require_workspace()
        return self.debug_session.continue_execution()

    def reset_halt_target(self) -> str:
        self._require_workspace()
        return self.debug_session.reset_halt()

    def step_in(self, timeout_seconds: float = 5.0) -> str:
        self._require_workspace()
        return self.debug_session.step_once(timeout_seconds=timeout_seconds)

    def step_over(self, timeout_seconds: float = 5.0) -> str:
        self._require_workspace()
        return self.debug_session.next_once(timeout_seconds=timeout_seconds)

    def step_out(self, timeout_seconds: float = 5.0):
        return self._require_workspace().step_out(timeout_seconds=timeout_seconds)

    # ------------------------------------------------------------------
    # Coherent debugger panes
    # ------------------------------------------------------------------
    def capture_halted(self, *, max_frames: int = 16) -> DebugHaltSnapshot:
        return self._require_snapshot_backend().capture(max_frames=max_frames)

    def select_frame_and_capture(self, level: int, *, max_frames: int = 16) -> DebugHaltSnapshot:
        return self._require_snapshot_backend().select_frame_and_capture(
            level, max_frames=max_frames,
        )

    def list_variable_children(self, variable_id: str):
        return self._require_workspace().list_children(variable_id)

    def create_watch(self, expression: str):
        return self._require_workspace().create_watch(expression)

    def refresh_variable_changes(self):
        return self._require_workspace().refresh_changes()

    def assign_variable(self, variable_id: str, value: str) -> str:
        return self._require_workspace().assign_variable(variable_id, value)

    def delete_watch(self, variable_id: str) -> None:
        self._require_workspace().delete_watch(variable_id)

    def list_breakpoints(self):
        return self._require_workspace().list_breakpoints()

    def breakpoint_usage(self):
        return self._require_workspace().breakpoint_usage()

    def create_hardware_breakpoint(self, location: str) -> int:
        return self._require_workspace().create_hardware_breakpoint(location)

    def create_watchpoint(self, expression: str) -> int:
        return self._require_workspace().create_watchpoint(expression)

    def delete_breakpoint(self, number: int) -> None:
        self._require_workspace().delete_breakpoint(number)

    def set_breakpoint_enabled(self, number: int, enabled: bool) -> None:
        self._require_workspace().set_breakpoint_enabled(number, enabled)

    def read_memory(self, address: int, length: int) -> DebugMemoryBlock:
        return self._require_memory_backend().read(address, length)

    # ------------------------------------------------------------------
    # Offline symbols/source navigation
    # ------------------------------------------------------------------
    def open_symbol_browser(self, image: Optional[Path] = None,
                            *, gdb_path: Optional[str] = None) -> DebugSymbolBrowserBackend:
        selected = image
        if selected is None:
            if not self._symbols:
                raise RuntimeError("No AXF/ELF is selected for symbol browsing.")
            selected = Path(self._symbols)
        if self.symbol_browser is not None:
            self.symbol_browser.close()
        self.symbol_browser = DebugSymbolBrowserBackend(Path(selected), gdb_path=gdb_path)
        return self.symbol_browser

    def search_functions(self, query: str = "", *, limit: int = 256):
        if self.symbol_browser is None:
            raise RuntimeError("Debug symbol browser is not open.")
        return self.symbol_browser.functions(query, limit=limit)

    def search_data_symbols(self, query: str = "", *, watchable: Optional[bool] = None,
                            limit: int = 256):
        if self.symbol_browser is None:
            raise RuntimeError("Debug symbol browser is not open.")
        return self.symbol_browser.data_symbols(query, watchable=watchable, limit=limit)

    def resolve_symbol_source(self, item: DebugSymbolItem) -> DebugSourceTarget:
        if self.symbol_browser is None:
            raise RuntimeError("Debug symbol browser is not open.")
        return self.symbol_browser.resolve_symbol(item)

    @staticmethod
    def _rate_hz(interval_seconds: float) -> Optional[float]:
        try:
            interval = float(interval_seconds)
        except (TypeError, ValueError):
            return None
        if interval <= 0:
            return None
        return round(1.0 / interval, 3)

    # ------------------------------------------------------------------
    # Zero-halt Live Monitor
    # ------------------------------------------------------------------
    def start_live_local(self, config: LocalLiveMonitorConfig) -> LiveMonitorSessionInfo:
        if self.live_active:
            raise RuntimeError("Live Monitor is already active.")
        if self.interactive_active:
            raise RuntimeError(
                "Local Live Monitor cannot start a second OpenOCD service while Interactive Debug is active."
            )
        info = self.live_session.start_local(config)
        self.mode = "local"
        self._live_info = info
        self._live_rate_hz = self._rate_hz(config.interval_seconds)
        if self._symbols is None:
            self._symbols = info.symbols
        return info

    def start_live_client(self, config: ClientLiveMonitorConfig) -> LiveMonitorSessionInfo:
        if self.live_active:
            raise RuntimeError("Live Monitor is already active.")
        remote = self.remote_session
        if remote is None:
            raise RuntimeError("Client mode has no RemoteSession configured.")
        health = remote.check_health()
        if not health.authenticated:
            raise RuntimeError("Client SSH session is not connected.")
        info = self.live_session.start_client(config, remote_session=remote)
        self.mode = "client"
        self._live_info = info
        self._live_rate_hz = self._rate_hz(config.interval_seconds)
        if self._symbols is None:
            self._symbols = info.symbols
        return info

    def run_live(self, on_sample: Optional[Callable] = None):
        if not self.live_active:
            raise RuntimeError("Live Monitor is not active.")
        return self.live_session.run(on_sample)

    def cancel_live(self) -> None:
        if self.live_active:
            self.live_session.cancel()

    def stop_live(self) -> None:
        if self.live_active:
            self.live_session.close()
        self._live_info = None
        self._live_rate_hz = None

    def stop_interactive(self) -> None:
        workspace = self.workspace
        self.workspace = None
        self.snapshot_backend = None
        self.memory_backend = None
        if workspace is not None and self.interactive_active:
            try:
                workspace.close()
            except Exception:
                pass
        if self.interactive_active:
            self.debug_session.stop()
        self._last_info = None

    def disconnect_remote(self, *, forget_password: bool = False) -> None:
        if self.mode == "client" and self.live_active:
            self.stop_live()
        if self.mode == "client" and self.interactive_active:
            self.stop_interactive()
        if self.remote_session is not None:
            self.remote_session.disconnect(forget_password=forget_password)

    def connection_state(self) -> DebugConnectionState:
        remote_endpoint = self.remote_session.endpoint if self.remote_session is not None else None
        if self.mode == "client":
            if self.remote_session is None:
                ssh_state = "disconnected"
            else:
                ssh_state = self.remote_session.check_health().state
        else:
            ssh_state = "n/a"

        live_state = "running" if self.live_running else ("active" if self.live_active else "inactive")

        if not self.interactive_active:
            target = "disconnected"
            if self.live_active:
                try:
                    target = str(self.live_session.target_state()).strip().lower()
                except Exception:
                    target = "unknown"
            return DebugConnectionState(
                mode=self.mode,
                ssh=ssh_state,
                gdb="disconnected",
                tcl="connected" if self.live_active else "disconnected",
                target=target,
                pc=None,
                symbols=self._symbols,
                remote_endpoint=remote_endpoint,
                live=live_state,
                sample_rate_hz=self._live_rate_hz,
            )

        gdb_state = "connected"
        tcl_state = "connected" if self.debug_session.tcl is not None else "disconnected"
        try:
            target = str(self.debug_session.target_poll()).strip().lower()
        except Exception:
            target = "unknown"
        pc = None
        if target == "halted" and self.workspace is not None:
            try:
                pc = self.workspace.current_location().address
            except Exception:
                pc = None
        return DebugConnectionState(
            mode=self.mode,
            ssh=ssh_state,
            gdb=gdb_state,
            tcl=tcl_state,
            target=target,
            pc=pc,
            symbols=self._symbols,
            remote_endpoint=remote_endpoint,
            live=live_state,
            sample_rate_hz=self._live_rate_hz,
        )

    def close(self, *, disconnect_remote: bool = True) -> None:
        self.stop_live()
        self.stop_interactive()
        if self.symbol_browser is not None:
            self.symbol_browser.close()
            self.symbol_browser = None
        if disconnect_remote and self.remote_session is not None:
            self.remote_session.disconnect()
