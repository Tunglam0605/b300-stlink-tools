"""Coherent HALTED-state snapshots for the B300 engineering Debug Workstation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .debug_workspace import (
    DebugBreakpoint,
    DebugBreakpointUsage,
    DebugRegister,
    DebugSourceLocation,
    DebugVariableNode,
    DebugWorkspaceBackend,
)
from .gdb_mi import FrameInfo


@dataclass(frozen=True)
class DebugHaltSnapshot:
    location: DebugSourceLocation
    frames: Tuple[FrameInfo, ...]
    locals: Tuple[DebugVariableNode, ...]
    registers: Tuple[DebugRegister, ...]
    breakpoints: Tuple[DebugBreakpoint, ...]
    breakpoint_usage: DebugBreakpointUsage


class DebugSnapshotBackend:
    """Capture related debugger panes from one HALTED target context."""

    def __init__(self, workspace: DebugWorkspaceBackend) -> None:
        self.workspace = workspace

    def _require_halted(self) -> None:
        if self.workspace.target_state != "halted":
            raise RuntimeError("Debug snapshot requires a HALTED target.")

    @staticmethod
    def _usage(rows: Tuple[DebugBreakpoint, ...], *, breakpoint_limit: int,
               watchpoint_limit: int) -> DebugBreakpointUsage:
        watchpoints = sum(1 for row in rows if "watch" in row.kind.lower())
        return DebugBreakpointUsage(
            breakpoints=len(rows) - watchpoints,
            breakpoint_limit=int(breakpoint_limit),
            watchpoints=watchpoints,
            watchpoint_limit=int(watchpoint_limit),
        )

    def capture(self, *, max_frames: int = 16, breakpoint_limit: int = 6,
                watchpoint_limit: int = 4) -> DebugHaltSnapshot:
        self._require_halted()
        location = self.workspace.current_location()
        frames = tuple(self.workspace.call_stack(max_frames))
        locals_rows = self.workspace.list_locals()
        registers = self.workspace.registers()
        breakpoints = self.workspace.list_breakpoints()
        return DebugHaltSnapshot(
            location=location,
            frames=frames,
            locals=locals_rows,
            registers=registers,
            breakpoints=breakpoints,
            breakpoint_usage=self._usage(
                breakpoints,
                breakpoint_limit=breakpoint_limit,
                watchpoint_limit=watchpoint_limit,
            ),
        )

    def select_frame_and_capture(self, level: int, *, max_frames: int = 16,
                                 breakpoint_limit: int = 6,
                                 watchpoint_limit: int = 4) -> DebugHaltSnapshot:
        self._require_halted()
        self.workspace.select_frame(level)
        return self.capture(
            max_frames=max_frames,
            breakpoint_limit=breakpoint_limit,
            watchpoint_limit=watchpoint_limit,
        )
