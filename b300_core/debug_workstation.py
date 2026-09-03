"""Mode-aware Local/Client debug orchestration for the v0.15 engineering workstation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .debug_session import DebugSession, DebugSessionConfig, DebugSessionInfo
from .debug_workspace import DebugWorkspaceBackend
from .remote_session import RemoteSession, RemoteSessionState


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


class DebugWorkstationController:
    """Keep mode/session ownership out of Qt widgets.

    The controller deliberately separates SSH login lifetime from interactive GDB
    lifetime. A Client can authenticate once, stop/restart GDB, run Live Monitor, and
    reuse the same SSH transport until the operator explicitly disconnects Remote.
    """

    def __init__(self, *, debug_session: Optional[DebugSession] = None,
                 remote_session: Optional[RemoteSession] = None) -> None:
        self.debug_session = debug_session or DebugSession()
        self.remote_session = remote_session
        self.workspace: Optional[DebugWorkspaceBackend] = None
        self.mode = "disconnected"
        self._symbols: Optional[str] = None
        self._last_info: Optional[DebugSessionInfo] = None

    @property
    def interactive_active(self) -> bool:
        return bool(self.debug_session.active)

    def set_remote_session(self, session: RemoteSession) -> None:
        if self.interactive_active:
            raise RuntimeError("Cannot replace the RemoteSession while Interactive Debug is active.")
        self.remote_session = session
        self.mode = "client"

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

    def start_local(self, config: DebugSessionConfig) -> DebugSessionInfo:
        if self.interactive_active:
            raise RuntimeError("Interactive Debug is already active.")
        info = self.debug_session.start(config)
        self.mode = "local"
        self._symbols = info.symbols
        self._last_info = info
        self.workspace = DebugWorkspaceBackend(
            self.debug_session.gdb,
            target_state_provider=self.debug_session.target_poll,
        )
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
        try:
            info = self.debug_session.start_external(
                symbol_file=symbol_file,
                gdb_host=gdb_forward.local_host,
                gdb_port=gdb_forward.local_port,
                tcl_host=tcl_forward.local_host,
                tcl_port=tcl_forward.local_port,
            )
        except Exception:
            # Keep the authenticated SSH transport and loopback forwards alive so the
            # operator can correct AXF/GDB state and retry without logging in again.
            raise
        self.mode = "client"
        self._symbols = info.symbols
        self._last_info = info
        self.workspace = DebugWorkspaceBackend(
            self.debug_session.gdb,
            target_state_provider=self.debug_session.target_poll,
        )
        return info

    def stop_interactive(self) -> None:
        workspace = self.workspace
        self.workspace = None
        if workspace is not None and self.interactive_active:
            try:
                workspace.close()
            except Exception:
                pass
        if self.interactive_active:
            self.debug_session.stop()
        self._last_info = None

    def disconnect_remote(self, *, forget_password: bool = False) -> None:
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

        if not self.interactive_active:
            return DebugConnectionState(
                mode=self.mode,
                ssh=ssh_state,
                gdb="disconnected",
                tcl="disconnected",
                target="disconnected",
                pc=None,
                symbols=self._symbols,
                remote_endpoint=remote_endpoint,
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
        )

    def close(self, *, disconnect_remote: bool = True) -> None:
        self.stop_interactive()
        if disconnect_remote and self.remote_session is not None:
            self.remote_session.disconnect()
