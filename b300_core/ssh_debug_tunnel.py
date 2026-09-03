"""Managed SSH local-port forwarding for B300 remote debug clients."""

from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Tuple

from .ssh_client import password_ssh_options
from .ssh_identity import resolve_ssh_client_executable
from .tcl_client import SafeTclClient, TclEndpoint

_SAFE_HOST = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9._-]+$")


def find_available_loopback_port(preferred: int, *, avoid: Tuple[int, ...] = ()) -> int:
    """Return a currently free loopback TCP port, preferring a stable B300 default."""
    if not 1 <= int(preferred) <= 65535:
        raise ValueError("Preferred port must be in range 1..65535.")
    blocked = {int(port) for port in avoid}
    candidates = [int(preferred)] if int(preferred) not in blocked else []
    for port in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            pass
        finally:
            sock.close()
    for _ in range(32):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        finally:
            sock.close()
        if port not in blocked:
            return port
    raise RuntimeError("Unable to allocate a loopback port for the SSH debug tunnel.")


class TunnelProcess(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[..., TunnelProcess]


@dataclass(frozen=True)
class SshDebugTunnelConfig:
    host: str
    user: str
    ssh_port: int = 22
    local_gdb_port: int = 3333
    local_tcl_port: int = 6666
    gateway_gdb_port: int = 3333
    gateway_tcl_port: int = 6666
    show_console: bool = False

    def validate(self) -> None:
        if not self.host or not _SAFE_HOST.fullmatch(self.host):
            raise ValueError("SSH gateway host contains unsupported characters.")
        if not self.user or not _SAFE_USER.fullmatch(self.user):
            raise ValueError("SSH user contains unsupported characters.")
        for label, port in (
            ("SSH", self.ssh_port),
            ("local GDB", self.local_gdb_port),
            ("local TCL", self.local_tcl_port),
            ("gateway GDB", self.gateway_gdb_port),
            ("gateway TCL", self.gateway_tcl_port),
        ):
            if not 1 <= int(port) <= 65535:
                raise ValueError("%s port must be in range 1..65535." % label)
        if self.local_gdb_port == self.local_tcl_port:
            raise ValueError("Local GDB and TCL forwarded ports must be distinct.")

    @property
    def destination(self) -> str:
        return "%s@%s" % (self.user, self.host)

    def argv(self, ssh_executable: str = "ssh") -> Tuple[str, ...]:
        self.validate()
        command = [
            ssh_executable,
            "-N",
            *password_ssh_options(),
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-L", "127.0.0.1:%d:127.0.0.1:%d" %
                  (self.local_gdb_port, self.gateway_gdb_port),
            "-L", "127.0.0.1:%d:127.0.0.1:%d" %
                  (self.local_tcl_port, self.gateway_tcl_port),
        ]
        if self.ssh_port != 22:
            command.extend(("-p", str(self.ssh_port)))
        command.append(self.destination)
        return tuple(command)


class SshDebugTunnel:
    """Own one SSH process and expose only loopback forwarded debug endpoints."""

    def __init__(self, config: SshDebugTunnelConfig, *, ssh_executable: Optional[str] = None,
                 process_factory: Optional[ProcessFactory] = None,
                 platform_name: Optional[str] = None,
                 tcl_factory=SafeTclClient) -> None:
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
    def gdb_endpoint(self) -> Tuple[str, int]:
        return "127.0.0.1", self.config.local_gdb_port

    @property
    def tcl_endpoint(self) -> Tuple[str, int]:
        return "127.0.0.1", self.config.local_tcl_port

    def start(self, timeout_seconds: float = 10.0) -> str:
        if self.active:
            raise RuntimeError("SSH debug tunnel is already active.")
        if timeout_seconds <= 0:
            raise ValueError("SSH tunnel readiness timeout must be positive.")
        resolved = resolve_ssh_client_executable("ssh")
        executable = self.ssh_executable or (str(resolved) if resolved is not None else None)
        if not executable:
            raise RuntimeError("SSH client was not found. Prepare OpenSSH Client first.")
        startup_kwargs = {}
        platform = (self._platform_name or __import__("platform").system()).lower()
        if self.config.show_console and platform in {"windows", "win32", "nt"}:
            startup_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        process = self._process_factory(
            list(self.config.argv(executable)),
            stdin=None,
            stdout=None,
            stderr=None,
            shell=False,
            **startup_kwargs,
        )
        self._process = process
        deadline = time.monotonic() + timeout_seconds
        last_error = None
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                self._process = None
                raise RuntimeError(
                    "SSH debug tunnel exited before readiness (exit code %s). "
                    "Verify host-key prompt, password, gateway address and sshd service." % code
                )
            try:
                client = self._tcl_factory(TclEndpoint("127.0.0.1", self.config.local_tcl_port))
                return client.version()
            except Exception as error:
                last_error = error
                time.sleep(0.1)
        self.stop()
        raise RuntimeError(
            "SSH tunnel opened but forwarded TCL did not become ready before timeout: %s" %
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
