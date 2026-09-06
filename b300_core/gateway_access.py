"""Read-only local identity shown to operators configuring a Gateway client."""

from __future__ import annotations

import getpass
import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Tuple


@dataclass(frozen=True)
class GatewayAccessInfo:
    user: str
    hostname: str
    addresses: Tuple[str, ...]
    ssh_port: int
    ssh_ready: bool


def discover_gateway_access(*, ssh_port: int = 22,
                            connect: Callable = socket.create_connection) -> GatewayAccessInfo:
    """Discover shareable IPv4 addresses and whether local SSH is listening."""
    hostname = socket.gethostname().strip() or "localhost"
    user = getpass.getuser().strip()
    addresses = []
    try:
        records = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        records = ()
    for record in records:
        address = str(record[4][0])
        parsed = ipaddress.ip_address(address)
        if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified or parsed.is_multicast:
            continue
        if address not in addresses:
            addresses.append(address)
    try:
        connection = connect(("127.0.0.1", int(ssh_port)), timeout=0.2)
    except OSError:
        ssh_ready = False
    else:
        connection.close()
        ssh_ready = True
    return GatewayAccessInfo(
        user=user, hostname=hostname, addresses=tuple(addresses),
        ssh_port=int(ssh_port), ssh_ready=ssh_ready,
    )


__all__ = ["GatewayAccessInfo", "discover_gateway_access"]
