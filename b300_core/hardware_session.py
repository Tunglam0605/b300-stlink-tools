"""Fail-fast, policy-controlled ownership of one B300 ST-Link target."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional

from .models import ProbeRef


class HardwareMode(str, Enum):
    IDLE = "IDLE"
    READING = "READING"
    FLASHING = "FLASHING"
    FACTORY_PROVISIONING = "FACTORY_PROVISIONING"
    DEBUGGING = "DEBUGGING"


class HardwareBusyError(RuntimeError):
    """Raised instead of launching a competing or policy-invalid operation."""


@dataclass(frozen=True)
class HardwareSessionState:
    mode: HardwareMode
    probe_serial: Optional[str]

    @property
    def busy(self) -> bool:
        return self.mode != HardwareMode.IDLE


class HardwareSessionLease:
    """Explicit DEBUGGING lease that may be released by the GUI control thread."""

    def __init__(self, manager: "HardwareSessionManager", lease_id: int) -> None:
        self._manager = manager
        self._lease_id = lease_id
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._manager._release_detached(self._lease_id)
            self._released = True


class HardwareSessionManager:
    """One target owner with explicit, non-escalating nested mode policy."""

    _NESTED_ALLOWED = {
        HardwareMode.READING: (HardwareMode.READING,),
        HardwareMode.FLASHING: (HardwareMode.READING, HardwareMode.FLASHING),
        HardwareMode.FACTORY_PROVISIONING: (
            HardwareMode.READING, HardwareMode.FACTORY_PROVISIONING,
        ),
        HardwareMode.DEBUGGING: (HardwareMode.DEBUGGING,),
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner_ident: Optional[int] = None
        self._depth = 0
        self._state = HardwareSessionState(HardwareMode.IDLE, None)
        self._detached_lease_id: Optional[int] = None
        self._next_lease_id = 1

    def snapshot(self) -> HardwareSessionState:
        with self._lock:
            return self._state

    @contextmanager
    def acquire(self, mode: HardwareMode, probe: ProbeRef) -> Iterator[HardwareSessionState]:
        selected = HardwareMode(mode)
        if selected == HardwareMode.IDLE:
            raise ValueError("IDLE cannot be acquired as a hardware operation.")
        owner = threading.get_ident()
        with self._lock:
            self._acquire_thread_bound(selected, probe, owner)
            state = self._state
        try:
            yield state
        finally:
            with self._lock:
                self._release_thread_bound(owner)

    def acquire_debugging(self, probe: ProbeRef) -> HardwareSessionLease:
        """Acquire a long-lived DEBUGGING session releasable from another thread."""
        owner = threading.get_ident()
        with self._lock:
            if self._owner_ident is not None:
                self._raise_busy(self._state)
            lease_id = self._next_lease_id
            self._next_lease_id += 1
            self._owner_ident = owner
            self._depth = 1
            self._state = HardwareSessionState(HardwareMode.DEBUGGING, probe.serial)
            self._detached_lease_id = lease_id
            return HardwareSessionLease(self, lease_id)

    def _acquire_thread_bound(self, selected: HardwareMode, probe: ProbeRef, owner: int) -> None:
        if self._owner_ident is None:
            self._owner_ident = owner
            self._depth = 1
            self._state = HardwareSessionState(selected, probe.serial)
            return
        if self._owner_ident != owner or self._detached_lease_id is not None:
            self._raise_busy(self._state)
        active = self._state
        if active.probe_serial != probe.serial:
            raise HardwareBusyError(
                "B300 hardware is busy in %s mode for probe %s; nested operation selected a different probe %s."
                % (active.mode.value, active.probe_serial, probe.serial)
            )
        if selected not in self._NESTED_ALLOWED[active.mode]:
            raise HardwareBusyError(
                "B300 hardware is busy in %s mode; nested %s is not allowed."
                % (active.mode.value, selected.value)
            )
        self._depth += 1

    def _release_thread_bound(self, owner: int) -> None:
        if self._owner_ident != owner or self._detached_lease_id is not None:
            raise RuntimeError("Hardware session owner changed unexpectedly.")
        self._depth -= 1
        if self._depth == 0:
            self._clear()

    def _release_detached(self, lease_id: int) -> None:
        with self._lock:
            if self._detached_lease_id != lease_id:
                raise RuntimeError("Debug hardware session lease is no longer active.")
            self._clear()

    def _clear(self) -> None:
        self._owner_ident = None
        self._depth = 0
        self._detached_lease_id = None
        self._state = HardwareSessionState(HardwareMode.IDLE, None)

    @staticmethod
    def _raise_busy(active: HardwareSessionState) -> None:
        raise HardwareBusyError(
            "Another ST-Link operation is already running: B300 hardware is busy in %s mode%s."
            % (active.mode.value,
               " for probe %s" % active.probe_serial if active.probe_serial else "")
        )


DEFAULT_HARDWARE_SESSION_MANAGER = HardwareSessionManager()
