"""Lifecycle for zero-halt OpenOCD TCL-only Live Monitor sessions."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from .hardware_session import DEFAULT_HARDWARE_SESSION_MANAGER, HardwareSessionManager
from .models import ProbeRef
from .openocd import build_live_monitor_command, resolve_openocd
from .process_startup import child_process_kwargs


class LiveMonitorService:
    def __init__(self, executable: Optional[str] = None,
                 session_manager: Optional[HardwareSessionManager] = None,
                 process_factory=None, platform_name: Optional[str] = None) -> None:
        self.executable = resolve_openocd(executable)
        self.session_manager = session_manager or DEFAULT_HARDWARE_SESSION_MANAGER
        self._process_factory = process_factory or subprocess.Popen
        self._platform_name = platform_name
        self._process = None
        self._lease = None

    def command(self, probe: ProbeRef, tcl_port: int = 6666):
        return build_live_monitor_command(probe, self.executable, tcl_port)

    def start(self, probe: ProbeRef, tcl_port: int = 6666,
              readiness_timeout_seconds: float = 3.0,
              event_sink: Optional[Callable[[str], None]] = None) -> None:
        if self._process is not None:
            raise RuntimeError("Live Monitor OpenOCD is already running.")
        lease = self.session_manager.acquire_debugging(probe)
        command = self.command(probe, tcl_port)
        try:
            process = self._process_factory(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                shell=False, **child_process_kwargs(self._platform_name),
            )
            self._process = process
            ready = threading.Event()
            logs = deque(maxlen=20)
            expected = "Info : Listening on port %d for tcl connections" % tcl_port

            def forward():
                stdout = getattr(process, "stdout", None)
                if stdout is None:
                    return
                for raw in stdout:
                    line = raw.rstrip("\r\n")
                    logs.append(line)
                    if event_sink is not None:
                        event_sink(line)
                    if expected in line:
                        ready.set()
            threading.Thread(target=forward, daemon=True).start()
            deadline = time.monotonic() + readiness_timeout_seconds
            while time.monotonic() < deadline:
                if ready.wait(0.05):
                    self._lease = lease
                    return
                if process.poll() is not None:
                    raise RuntimeError("Live Monitor OpenOCD exited before TCL became ready: %s" % " | ".join(logs))
            raise RuntimeError("Live Monitor TCL listener did not become ready: %s" % " | ".join(logs))
        except BaseException:
            self._stop_process()
            lease.release()
            raise

    def stop(self) -> None:
        self._stop_process()
        if self._lease is not None:
            self._lease.release()
            self._lease = None

    def _stop_process(self) -> None:
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
