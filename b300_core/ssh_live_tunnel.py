"""Managed TCL-only SSH forwarding for zero-halt B300 Live Monitor clients."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Tuple

from .process_startup import child_process_kwargs
from .tcl_client import SafeTclClient, TclEndpoint

_SAFE_HOST = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9._-]+$")


class TunnelProcess(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[..., TunnelProcess]


@dataclass(frozen=True)
class SshLiveTunnelConfig:
    host: str
    user: str
    ssh_port: int = 22
    local_tcl_port: int = 16666
    gateway_tcl_port: int = 6666

    def validate(self) -> None:
        if not self.host or not _SAFE_HOST.fullmatch(self.host):
            raise ValueError("SSH gateway host contains unsupported characters.")
        if not self.user or not _SAFE_USER.fullmatch(self.user):
            raise ValueError("SSH user contains unsupported characters.")
        for label, port in (("SSH", self.ssh_port), ("local TCL", self.local_tcl_port),
                            ("gateway TCL", self.gateway_tcl_port)):
            if not 1 <= int(port) <= 65535:
                raise ValueError("%s port must be in range 1..65535." % label)

    @property
    def destination(self) -> str:
        return "%s@%s" % (self.user, self.host)

    def argv(self, ssh_executable: str = "ssh") -> Tuple[str, ...]:
        self.validate()
        command = [
            ssh_executable, "-N",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-L", "127.0.0.1:%d:127.0.0.1:%d" %
                  (self.local_tcl_port, self.gateway_tcl_port),
        ]
        if self.ssh_port != 22:
            command.extend(("-p", str(self.ssh_port)))
        command.append(self.destination)
        return tuple(command)


class SshLiveTunnel:
    """Own one SSH process and expose only a loopback TCL forwarding endpoint."""

    def __init__(self, config: SshLiveTunnelConfig, *, ssh_executable: Optional[str] = None,
                 process_factory: Optional[ProcessFactory] = None,
                 platform_name: Optional[str] = None, tcl_factory=SafeTclClient) -> None:
        config.validate()
        self.config = config
        self.ssh_executable = ssh_executable
        self._process_factory = process_factory or subprocess.Popen
        self._platform_name = platform_name
        self._tcl_factory = tcl_factory
        self._process: Optional[TunnelProcess] = None

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def tcl_endpoint(self) -> Tuple[str, int]:
        return "127.0.0.1", self.config.local_tcl_port

    def start(self, timeout_seconds: float = 10.0) -> str:
        if self.active:
            raise RuntimeError("SSH Live Monitor tunnel is already active.")
        if timeout_seconds <= 0:
            raise ValueError("SSH Live Monitor readiness timeout must be positive.")
        executable = self.ssh_executable or shutil.which("ssh")
        if not executable:
            raise RuntimeError("SSH client was not found. Install/configure OpenSSH client first.")
        process = self._process_factory(
            list(self.config.argv(executable)), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
            **child_process_kwargs(self._platform_name),
        )
        self._process = process
        deadline = time.monotonic() + timeout_seconds
        last_error = None
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                self._process = None
                raise RuntimeError(
                    "SSH Live Monitor tunnel exited before readiness (exit code %s). "
                    "Verify host key, SSH key/agent, gateway address and sshd service." % code
                )
            try:
                client = self._tcl_factory(TclEndpoint("127.0.0.1", self.config.local_tcl_port))
                return client.version()
            except Exception as error:
                last_error = error
                time.sleep(0.1)
        self.stop()
        raise RuntimeError(
            "SSH Live Monitor tunnel opened but forwarded TCL was not ready before timeout: %s" %
            (last_error or "unknown error")
        )

    def stop(self) -> None:
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
