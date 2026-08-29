"""Fail-safe run-state restoration for externally controlled GDB sessions."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .tcl_client import SafeTclClient


GuardEventSink = Callable[[str, str], None]


@dataclass(frozen=True)
class RemoteGuardSnapshot:
    initial_target_state: str
    restored: bool
    final_target_state: str


class RemoteDebugGuard:
    """Restore RUNNING after external GDB disconnect if the board was initially running."""

    def __init__(self, tcl: SafeTclClient, event_sink: Optional[GuardEventSink] = None) -> None:
        self.tcl = tcl
        self.event_sink = event_sink
        self.initial_target_state: Optional[str] = None
        self._gdb_seen = False
        self._lock = threading.RLock()

    def capture_initial_state(self) -> str:
        with self._lock:
            state = self.tcl.wait_target_state()
            self.initial_target_state = state
            self._emit("armed", "initial_target_state=%s" % state)
            return state

    def handle_openocd_line(self, line: str) -> None:
        text = str(line).lower()
        if "accepting 'gdb' connection" in text:
            with self._lock:
                self._gdb_seen = True
                self._emit("gdb_connected", "external GDB connection accepted")
            return
        if "dropped 'gdb' connection" in text:
            with self._lock:
                if not self._gdb_seen:
                    return
                self._gdb_seen = False
            self.restore_initial_state(reason="gdb_disconnect")

    def restore_initial_state(self, *, reason: str) -> RemoteGuardSnapshot:
        with self._lock:
            initial = self.initial_target_state
            if initial not in {"running", "halted"}:
                raise RuntimeError("Remote debug guard initial target state is unavailable.")
            current = self.tcl.wait_target_state()
            restored = False
            if initial == "running" and current == "halted":
                final_state = self.tcl.resume_target()
                restored = True
            else:
                final_state = current
            if initial == "halted" and final_state != "halted":
                # Fail closed: the guard never halts a board that was already halted;
                # it only restores RUNNING sessions. Report unexpected drift instead.
                self._emit("state_drift", "%s -> %s" % (initial, final_state))
            self._emit(
                "restored" if restored else "checked",
                "reason=%s initial=%s final=%s" % (reason, initial, final_state),
            )
            return RemoteGuardSnapshot(initial, restored, final_state)

    def _emit(self, event: str, message: str) -> None:
        if self.event_sink is not None:
            self.event_sink(event, message)
