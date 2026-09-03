"""Frontend-Backend Adapter seam for B300 Debug Workstation v0.15.0.

Decouples Qt GUI presentation from b300_core controllers.
When backend DebugWorkstationController is merged, DebugTab connects
directly to this adapter without modifying GUI views.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence
from .debug_view_models import (
    DebugBreakpoint,
    DebugConnectionState,
    DebugFrame,
    DebugRegister,
    DebugVariableNode,
)


class DebugWorkstationAdapter(Protocol):
    """Contract that backend DebugWorkstationController must implement."""

    def connection_state(self) -> DebugConnectionState:
        """Return canonical transport and target state."""
        ...

    def remote_login(self, password: str, remember: bool = True) -> None:
        """Authenticate with remote gateway using single login session."""
        ...

    def start_client(self, symbol_file: Optional[str] = None) -> None:
        """Start client debug session using verified remote tunnel."""
        ...

    def step_out(self) -> None:
        """Step out of current function to caller."""
        ...

    def select_frame(self, level: int) -> None:
        """Select call stack frame by index level."""
        ...

    def request_variable_children(self, variable_id: str) -> None:
        """Lazy-load child variables for struct/array."""
        ...

    def list_breakpoints(self) -> Sequence[DebugBreakpoint]:
        """List active hardware breakpoints and watchpoints."""
        ...

    def create_hardware_breakpoint(self, location: str) -> None:
        """Create hardware breakpoint at file:line or function/address."""
        ...

    def create_watchpoint(self, expression: str) -> None:
        """Create hardware watchpoint on expression."""
        ...

    def set_breakpoint_enabled(self, number: int, enabled: bool) -> None:
        """Enable or disable breakpoint by number."""
        ...

    def delete_breakpoint(self, number: int) -> None:
        """Delete breakpoint by number."""
        ...


class DefaultDebugAdapter:
    """Default adapter bridging existing DebugSession/Service to workstation views."""

    def __init__(
        self,
        session: Any = None,
        service: Any = None,
        log_sink: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.session = session
        self.service = service
        self.log_sink = log_sink or (lambda msg: None)

    def connection_state(
        self,
        mode: str = "local",
        ssh_active: bool = False,
        target_state: str = "DISCONNECTED",
        pc: str = "—",
        sample_rate: str = "—",
    ) -> DebugConnectionState:
        gdb_active = bool(self.session and getattr(self.session, "active", False))
        tcl_active = gdb_active
        return DebugConnectionState(
            mode=mode,
            ssh=ssh_active,
            gdb=gdb_active,
            tcl=tcl_active,
            target=target_state,
            pc=pc,
            sample_rate=sample_rate,
        )

    def step_out(self) -> None:
        if self.session and hasattr(self.session, "step_out"):
            self.session.step_out()
        else:
            self.log_sink("Step Out: Đang đợi backend DebugWorkspaceBackend.step_out() tích hợp.")

    def select_frame(self, level: int) -> None:
        if self.session and hasattr(self.session, "select_frame"):
            self.session.select_frame(level)
        else:
            self.log_sink(f"Frame #{level} selected (controller adapter pending backend merge).")

    def request_variable_children(self, variable_id: str) -> None:
        if self.session and hasattr(self.session, "list_variable_children"):
            self.session.list_variable_children(variable_id)
        else:
            self.log_sink(f"Request children for variable {variable_id} (lazy load adapter pending).")
