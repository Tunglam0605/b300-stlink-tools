"""Safe, bounded network preflight for a remote B300 SSH Gateway."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable, Tuple

from .ssh_host_trust import validate_gateway_host, validate_ssh_port


@dataclass(frozen=True)
class GatewayEndpointProbe:
    host: str
    port: int
    ready: bool
    reason_code: str
    message: str

    @property
    def endpoint(self) -> str:
        return "%s:%d" % (self.host, self.port)


ConnectionFactory = Callable[[Tuple[str, int], float], object]


def probe_gateway_ssh_endpoint(
        host: str, port: int, *, connector: ConnectionFactory = socket.create_connection,
) -> GatewayEndpointProbe:
    """Check only TCP reachability before scanning a public host key.

    This does not authenticate, transmit credentials, or alter the Gateway.
    It lets the UI distinguish a blocked/offline network path from a host-key
    validation failure, which must remain fail-closed.
    """
    selected_host = validate_gateway_host(host)
    selected_port = validate_ssh_port(port)
    try:
        connection = connector((selected_host, selected_port), 3.0)
    except OSError:
        return GatewayEndpointProbe(
            selected_host, selected_port, False, "SSH_TCP_UNREACHABLE",
            "TCP connection to %s:%d is unavailable." % (selected_host, selected_port),
        )
    try:
        return GatewayEndpointProbe(
            selected_host, selected_port, True, "SSH_TCP_REACHABLE",
            "TCP connection to %s:%d is reachable." % (selected_host, selected_port),
        )
    finally:
        connection.close()
