"""Safe OpenOCD debug-server lifecycle for one B300 ST-Link session."""

from __future__ import annotations

import ipaddress
import subprocess
import threading
import time
from collections import deque
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
from .process_startup import child_process_kwargs


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
    tcl_port: Optional[int] = None

    def validate(self) -> None:
        address = ipaddress.ip_address(self.bind_address)
        named_ports = (("GDB", self.gdb_port), ("telnet", self.telnet_port), ("TCL", self.tcl_port))
        for label, port in named_ports:
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("%s port must be in range 1..65535." % label)
        if self.telnet_port is not None and not address.is_loopback:
            raise ValueError("Telnet is allowed only when OpenOCD binds to a loopback address.")
        if self.tcl_port is not None and not address.is_loopback:
            raise ValueError("TCL is allowed only when OpenOCD binds to a loopback address.")
        enabled_ports = [port for _label, port in named_ports if port is not None]
        if len(enabled_ports) != len(set(enabled_ports)):
            raise ValueError("OpenOCD debug ports must be distinct.")


class DebugProcess(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[..., DebugProcess]
EventSink = Callable[[str], None]


class DebugService:
    """Starts/stops OpenOCD while retaining the shared DEBUGGING session."""

    def __init__(self, executable: Optional[str] = None,
                 session_manager: Optional[HardwareSessionManager] = None,
                 process_factory: Optional[ProcessFactory] = None,
                 platform_name: Optional[str] = None) -> None:
        self.executable = resolve_openocd(executable)
        self.session_manager = session_manager or DEFAULT_HARDWARE_SESSION_MANAGER
        self._process_factory = process_factory or subprocess.Popen
        self._platform_name = platform_name
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
                    config.gdb_port, config.telnet_port, config.tcl_port,
                )
                self._process = self._process_factory(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    shell=False, **child_process_kwargs(self._platform_name),
                )
                ready, logs, log_lock = self._forward_output(
                    self._process, config.gdb_port, event_sink,
                    config.telnet_port, config.tcl_port,
                )
                self._wait_for_listener_locked(ready, logs, log_lock, readiness_timeout_seconds)
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
    def _forward_output(process: DebugProcess, gdb_port: int,
                        event_sink: Optional[EventSink],
                        telnet_port: Optional[int] = None,
                        tcl_port: Optional[int] = None):
        stdout = getattr(process, "stdout", None)
        ready = threading.Event()
        logs = deque(maxlen=10)
        log_lock = threading.Lock()
        expected = {"Info : Listening on port %d for gdb connections" % gdb_port}
        if telnet_port is not None:
            expected.add("Info : Listening on port %d for telnet connections" % telnet_port)
        if tcl_port is not None:
            expected.add("Info : Listening on port %d for tcl connections" % tcl_port)
        seen = set()
        if stdout is None:
            return ready, logs, log_lock

        def forward() -> None:
            for line in stdout:
                text = str(line).strip()
                if text:
                    with log_lock:
                        logs.append(text)
                        if text in expected:
                            seen.add(text)
                            if expected.issubset(seen):
                                ready.set()
                    if event_sink is not None:
                        event_sink(text)

        threading.Thread(target=forward, name="b300-openocd-log", daemon=True).start()
        return ready, logs, log_lock

    def _wait_for_listener_locked(self, ready: threading.Event, logs, log_lock: threading.Lock,
                                  timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            process = self._process
            if ready.is_set():
                if process is not None and process.poll() is None:
                    return
                code = None if process is None else process.poll()
                with log_lock:
                    context = " | ".join(logs) or "(none)"
                raise RuntimeError(
                    "OpenOCD exited before requested debug listeners became ready (exit code %s). Last log: %s" %
                    (code, context)
                )
            if process is None or process.poll() is not None:
                code = None if process is None else process.poll()
                with log_lock:
                    context = " | ".join(logs) or "(none)"
                raise RuntimeError(
                    "OpenOCD exited before requested debug listeners became ready (exit code %s). Last log: %s" %
                    (code, context)
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with log_lock:
                    context = " | ".join(logs) or "(none)"
                raise RuntimeError(
                    "OpenOCD requested debug listeners were not ready before timeout. Last log: %s" %
                    context
                )
            ready.wait(min(remaining, 0.02))
