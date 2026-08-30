"""Loopback-only, allow-listed OpenOCD TCL client for B300 diagnostics/control."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


_TCL_EOF = b"\x1a"
_REGISTER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class TclClientError(RuntimeError):
    """OpenOCD TCL transport or protocol failure."""


@dataclass(frozen=True)
class TclEndpoint:
    host: str = "127.0.0.1"
    port: int = 6666

    def validate(self) -> None:
        address = ipaddress.ip_address(self.host)
        if not address.is_loopback:
            raise ValueError("OpenOCD TCL is allowed only on a loopback address.")
        if not 1 <= self.port <= 65535:
            raise ValueError("TCL port must be in range 1..65535.")


SocketFactory = Callable[..., socket.socket]


class SafeTclClient:
    """Small allow-listed TCL surface; arbitrary OpenOCD commands are intentionally absent."""

    def __init__(self, endpoint: TclEndpoint = TclEndpoint(), *,
                 socket_factory: Optional[SocketFactory] = None,
                 timeout_seconds: float = 2.0,
                 max_response_bytes: int = 256 * 1024) -> None:
        endpoint.validate()
        if timeout_seconds <= 0:
            raise ValueError("TCL timeout must be positive.")
        if not 1024 <= max_response_bytes <= 4 * 1024 * 1024:
            raise ValueError("TCL response limit is outside the supported range.")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._socket_factory = socket_factory or socket.create_connection

    def version(self) -> str:
        return self._request("version")

    def targets(self) -> str:
        return self._request("targets")

    def target_state(self) -> str:
        text = self.targets()
        for line in text.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0].endswith("*") and fields[0][:-1].isdigit():
                state = fields[-1].strip().lower()
                if state in {"running", "halted", "reset", "unknown"}:
                    return state
                raise TclClientError("OpenOCD TCL returned an unsupported target state: %s" % state)
        raise TclClientError("OpenOCD TCL targets output did not identify the selected target state.")

    def wait_target_state(self, timeout_seconds: float = 2.0, poll_interval: float = 0.05) -> str:
        if timeout_seconds <= 0 or poll_interval <= 0:
            raise ValueError("Target-state wait timing must be positive.")
        deadline = time.monotonic() + timeout_seconds
        last_state = "unknown"
        while True:
            last_state = self.target_state()
            if last_state in {"running", "halted"}:
                return last_state
            if time.monotonic() >= deadline:
                raise TclClientError(
                    "OpenOCD target did not become running or halted before timeout; last state: %s" % last_state
                )
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def wait_for_target_state(self, expected: str, timeout_seconds: float = 2.0,
                              poll_interval: float = 0.05) -> str:
        expected_state = str(expected).strip().lower()
        if expected_state not in {"running", "halted"}:
            raise ValueError("Expected target state must be running or halted.")
        if timeout_seconds <= 0 or poll_interval <= 0:
            raise ValueError("Target-state wait timing must be positive.")
        deadline = time.monotonic() + timeout_seconds
        last_state = "unknown"
        while True:
            last_state = self.target_state()
            if last_state == expected_state:
                return last_state
            if time.monotonic() >= deadline:
                raise TclClientError(
                    "OpenOCD target did not become %s before timeout; last state: %s" %
                    (expected_state, last_state)
                )
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def poll(self) -> str:
        return self._request("poll")

    def resume_target(self) -> str:
        """Resume only the selected target; no raw TCL or Flash-mutating command is exposed."""
        current = self.wait_target_state()
        if current == "running":
            return current
        self._request("resume")
        return self.wait_for_target_state("running")

    def read_words(self, address: int, count: int = 1) -> Tuple[int, ...]:
        if not 0 <= address <= 0xFFFFFFFF or address % 4:
            raise ValueError("TCL word-read address must be a 32-bit aligned address.")
        if not 1 <= count <= 256:
            raise ValueError("TCL word-read count must be in range 1..256.")
        text = self._request("mdw 0x%08X %d" % (address, count))
        values = []
        for line in text.splitlines():
            match = re.search(r"0x[0-9A-Fa-f]+:\s+((?:[0-9A-Fa-f]{8}\s*)+)$", line.strip())
            if match:
                values.extend(int(item, 16) for item in match.group(1).split())
        if len(values) != count:
            raise TclClientError(
                "OpenOCD TCL returned %d words; expected %d." % (len(values), count)
            )
        return tuple(values)

    def read_word_addresses(self, addresses) -> Tuple[int, ...]:
        selected = tuple(int(address) for address in addresses)
        if not 1 <= len(selected) <= 32:
            raise ValueError("TCL multi-read requires 1..32 addresses.")
        for address in selected:
            if not 0 <= address <= 0xFFFFFFFF or address % 4:
                raise ValueError("TCL multi-read addresses must be 32-bit aligned uint32 values.")
        assignments = [
            "set b300_v%d [mdw 0x%08X 1]" % (index, address)
            for index, address in enumerate(selected)
        ]
        result = "list " + " ".join("$b300_v%d" % index for index in range(len(selected)))
        text = self._request("; ".join(assignments + [result]))
        values = []
        for match in re.finditer(r"0x[0-9A-Fa-f]+:\s+([0-9A-Fa-f]{8})", text):
            values.append(int(match.group(1), 16))
        if len(values) != len(selected):
            raise TclClientError(
                "OpenOCD TCL returned %d multi-read words; expected %d." %
                (len(values), len(selected))
            )
        return tuple(values)

    def read_register(self, name: str) -> str:
        if not _REGISTER_NAME.fullmatch(name):
            raise ValueError("Register name contains unsupported characters.")
        return self._request("reg %s" % name)

    def _request(self, command: str) -> str:
        payload = command.encode("ascii") + _TCL_EOF
        try:
            connection = self._socket_factory(
                (self.endpoint.host, self.endpoint.port), timeout=self.timeout_seconds
            )
        except OSError as error:
            raise TclClientError("Unable to connect to OpenOCD TCL: %s" % error) from error
        data = bytearray()
        try:
            if hasattr(connection, "settimeout"):
                connection.settimeout(self.timeout_seconds)
            connection.sendall(payload)
            while True:
                chunk = connection.recv(min(4096, self.max_response_bytes - len(data) + 1))
                if not chunk:
                    raise TclClientError("OpenOCD TCL closed before the response terminator.")
                data.extend(chunk)
                marker = data.find(_TCL_EOF)
                if marker >= 0:
                    response = bytes(data[:marker])
                    break
                if len(data) > self.max_response_bytes:
                    raise TclClientError("OpenOCD TCL response exceeded the configured limit.")
        except (OSError, socket.timeout) as error:
            raise TclClientError("OpenOCD TCL request failed: %s" % error) from error
        finally:
            try:
                connection.close()
            except OSError:
                pass
        return response.decode("utf-8", "replace").strip()
