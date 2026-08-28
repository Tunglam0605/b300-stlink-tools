"""Safe OpenOCD debug-server lifecycle for one B300 ST-Link session."""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Protocol

from .hardware_session import (
    DEFAULT_HARDWARE_SESSION_MANAGER,
    HardwareMode,
    HardwareSessionManager,
)
from .models import ProbeRef
from .openocd import build_debug_command, resolve_openocd


class DebugState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DebugConfig:
    probe: ProbeRef
    bind_address: str = "127.0.0.1"
    gdb_port: int = 3333
    telnet_port: Optional[int] = None

    def validate(self) -> None:
        address = ipaddress.ip_address(self.bind_address)
        for label, port in (("GDB", self.gdb_port), ("telnet", self.telnet_port)):
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("%s port must be in range 1..65535." % label)
        if self.telnet_port is not None and not address.is_loopback:
            raise ValueError("Telnet is allowed only when OpenOCD binds to a loopback address.")


class DebugProcess(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[..., DebugProcess]
PortWaiter = Callable[[str, int, float], bool]
EventSink = Callable[[str], None]


def wait_for_local_port(host: str, port: int, timeout_seconds: float) -> bool:
    """One bounded readiness probe; never exposes a non-loopback telnet server."""
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


class DebugService:
    """Starts/stops OpenOCD while retaining the shared DEBUGGING session."""

    def __init__(self, executable: Optional[str] = None,
                 session_manager: Optional[HardwareSessionManager] = None,
                 process_factory: Optional[ProcessFactory] = None,
                 port_waiter: Optional[PortWaiter] = None) -> None:
        self.executable = resolve_openocd(executable)
        self.session_manager = session_manager or DEFAULT_HARDWARE_SESSION_MANAGER
        self._process_factory = process_factory or subprocess.Popen
        self._port_waiter = port_waiter or wait_for_local_port
        self._state = DebugState.STOPPED
        self._process: Optional[DebugProcess] = None
        self._session_lease = None
        self._lock = threading.RLock()

    @property
    def state(self) -> DebugState:
        with self._lock:
            self.poll()
            return self._state

    @property
    def process(self) -> Optional[DebugProcess]:
        with self._lock:
            return self._process

    def start(self, config: DebugConfig, readiness_timeout_seconds: float = 3.0,
              event_sink: Optional[EventSink] = None) -> DebugState:
        config.validate()
        with self._lock:
            self.poll()
            if self._state in (DebugState.READY, DebugState.CONNECTED, DebugState.STARTING):
                raise RuntimeError("OpenOCD debug server is already running.")
            self._state = DebugState.STARTING
            session_lease = None
            try:
                session_lease = self.session_manager.acquire_debugging(config.probe)
                command = build_debug_command(
                    config.probe, self.executable, config.bind_address,
                    config.gdb_port, config.telnet_port,
                )
                self._process = self._process_factory(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                self._forward_output(self._process, event_sink)
                if not self._port_waiter(config.bind_address, config.gdb_port, readiness_timeout_seconds):
                    raise RuntimeError("OpenOCD GDB port is not ready.")
            except BaseException:
                self._stop_process_locked()
                if session_lease is not None:
                    session_lease.release()
                self._state = DebugState.FAILED
                raise
            self._session_lease = session_lease
            self._state = DebugState.READY
            return self._state

    def mark_connected(self) -> None:
        with self._lock:
            if self._state != DebugState.READY:
                raise RuntimeError("GDB can connect only after OpenOCD is ready.")
            self._state = DebugState.CONNECTED

    def poll(self) -> DebugState:
        """Poll OpenOCD atomically so GUI watchdogs cannot race start/stop cleanup."""
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                self._process = None
                self._release_session_locked()
                if self._state in (DebugState.READY, DebugState.CONNECTED, DebugState.STARTING):
                    self._state = DebugState.FAILED
            return self._state

    def stop(self) -> DebugState:
        with self._lock:
            self._stop_process_locked()
            self._release_session_locked()
            self._state = DebugState.STOPPED
            return self._state

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3.0)

    def _release_session_locked(self) -> None:
        if self._session_lease is not None:
            self._session_lease.release()
            self._session_lease = None

    @staticmethod
    def _forward_output(process: DebugProcess, event_sink: Optional[EventSink]) -> None:
        stdout = getattr(process, "stdout", None)
        if event_sink is None or stdout is None:
            return

        def forward() -> None:
            for line in stdout:
                text = str(line).strip()
                if text:
                    event_sink(text)

        threading.Thread(target=forward, name="b300-openocd-log", daemon=True).start()
