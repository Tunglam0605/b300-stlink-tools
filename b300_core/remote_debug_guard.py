"""Fail-safe run-state restoration for externally controlled GDB sessions."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .tcl_client import SafeTclClient


GuardEventSink = Callable[[str, str], None]
LastClientDetachedSink = Callable[["RemoteGuardSnapshot"], None]
ReclaimScheduler = Callable[[float, Callable[[], None]], None]


@dataclass(frozen=True)
class RemoteGuardSnapshot:
    initial_target_state: str
    restored: bool
    final_target_state: str


class RemoteDebugGuard:
    """Restore RUNNING after external GDB disconnect if the board was initially running."""

    def __init__(self, tcl: SafeTclClient, event_sink: Optional[GuardEventSink] = None,
                 last_client_detached_sink: Optional[LastClientDetachedSink] = None,
                 reclaim_delay_seconds: float = 0.25,
                 reclaim_scheduler: Optional[ReclaimScheduler] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if reclaim_delay_seconds < 0:
            raise ValueError("Remote debug reclaim delay must not be negative.")
        self.tcl = tcl
        self.event_sink = event_sink
        self.last_client_detached_sink = last_client_detached_sink
        self._reclaim_delay_seconds = float(reclaim_delay_seconds)
        self._reclaim_scheduler = reclaim_scheduler or self._schedule_reclaim
        self._clock = clock
        self.initial_target_state: Optional[str] = None
        self._gdb_connections = 0
        self._attach_generation = 0
        self._reclaim_candidate = 0
        self._lock = threading.RLock()

    @staticmethod
    def _schedule_reclaim(delay_seconds: float, callback: Callable[[], None]) -> None:
        timer = threading.Timer(delay_seconds, callback)
        timer.daemon = True
        timer.start()

    def capture_initial_state(self, state: Optional[str] = None) -> str:
        with self._lock:
            captured = self.tcl.wait_target_state() if state is None else str(state).lower()
            if captured not in {"running", "halted"}:
                raise RuntimeError("Remote debug guard requires a verified initial target state.")
            self.initial_target_state = captured
            self._emit("armed", "initial_target_state=%s" % captured)
            return captured

    def handle_openocd_line(self, line: str) -> None:
        text = str(line).lower()
        if "accepting 'gdb' connection" in text:
            with self._lock:
                self._gdb_connections += 1
                self._attach_generation += 1
                self._reclaim_candidate = 0
                self._emit(
                    "gdb_connected",
                    "external GDB connection accepted; active=%d" % self._gdb_connections,
                )
            return
        if "dropped 'gdb' connection" in text:
            reclaim = None
            with self._lock:
                if self._gdb_connections <= 0:
                    return
                self._gdb_connections -= 1
                self._emit(
                    "gdb_disconnected",
                    "external GDB connection dropped; active=%d" % self._gdb_connections,
                )
                if self._gdb_connections == 0:
                    snapshot = self.restore_initial_state(reason="last_gdb_disconnect")
                    self._reclaim_candidate += 1
                    reclaim = (
                        self._attach_generation,
                        self._reclaim_candidate,
                        self._clock() + self._reclaim_delay_seconds,
                        snapshot,
                    )
            if reclaim is not None:
                attach_generation, candidate, deadline, snapshot = reclaim
                self._reclaim_scheduler(
                    self._reclaim_delay_seconds,
                    lambda: self._reclaim_if_quiescent(
                        attach_generation, candidate, deadline, snapshot,
                    ),
                )

    def _reclaim_if_quiescent(self, attach_generation: int, candidate: int,
                              deadline: float, snapshot: RemoteGuardSnapshot) -> None:
        with self._lock:
            if (self._gdb_connections != 0
                    or self._attach_generation != attach_generation
                    or self._reclaim_candidate != candidate):
                return
            remaining = deadline - self._clock()
            if remaining > 0:
                self._reclaim_scheduler(
                    remaining,
                    lambda: self._reclaim_if_quiescent(
                        attach_generation, candidate, deadline, snapshot,
                    ),
                )
                return
            self._reclaim_candidate = 0
        if self.last_client_detached_sink is not None:
            self.last_client_detached_sink(snapshot)

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
