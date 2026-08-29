"""Read-only host preflight for a B300 remote debug Gateway."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

from .models import ProbeInfo
from .openocd import resolve_openocd
from .probe import list_probes
from .probe_selection import ProbeSelectionError, select_probe


@dataclass(frozen=True)
class GatewayReadinessCheck:
    name: str
    status: str
    code: str
    message: str
    next_action: str


@dataclass(frozen=True)
class GatewayReadinessReport:
    checks: Tuple[GatewayReadinessCheck, ...]
    conclusion: str
    ready: bool
    ipv4_addresses: Tuple[str, ...]
    ssh_port: int
    gdb_port: int
    tcl_port: int
    probe: Optional[ProbeInfo]
    openocd: Optional[str]


def _tcp_connectable(host: str, port: int, timeout_seconds: float = 0.35) -> bool:
    try:
        connection = socket.create_connection((host, port), timeout=timeout_seconds)
    except OSError:
        return False
    connection.close()
    return True


def _tcp_port_available(host: str, port: int) -> bool:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((host, port))
    except OSError:
        return False
    finally:
        listener.close()
    return True


def discover_ipv4_addresses() -> Tuple[str, ...]:
    """Return bounded non-loopback IPv4 candidates without requiring Internet access."""
    found = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(item[4][0])
    except OSError:
        pass
    # UDP connect selects the OS route but sends no packet. It improves Windows
    # accuracy when hostname resolution only returns loopback/stale addresses.
    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route.connect(("192.0.2.1", 9))
        found.add(route.getsockname()[0])
    except OSError:
        pass
    finally:
        route.close()
    result = []
    for value in found:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback and not address.is_unspecified:
            result.append(str(address))
    return tuple(sorted(set(result), key=lambda value: tuple(int(part) for part in value.split("."))))


def inspect_gateway_readiness(
        *, openocd: Optional[str] = None, probe_serial: Optional[str] = None,
        ssh_port: int = 22, gdb_port: int = 3333, tcl_port: int = 6666,
        openocd_resolver: Callable[[Optional[str]], str] = resolve_openocd,
        probe_discovery: Callable[[], Sequence[ProbeInfo]] = list_probes,
        ssh_probe: Callable[[str, int], bool] = _tcp_connectable,
        port_probe: Callable[[str, int], bool] = _tcp_port_available,
        ipv4_discovery: Callable[[], Tuple[str, ...]] = discover_ipv4_addresses,
) -> GatewayReadinessReport:
    for label, port in (("SSH", ssh_port), ("GDB", gdb_port), ("TCL", tcl_port)):
        if not 1 <= int(port) <= 65535:
            raise ValueError("%s port must be in range 1..65535." % label)
    if len({int(ssh_port), int(gdb_port), int(tcl_port)}) != 3:
        raise ValueError("Gateway SSH, GDB, and TCL ports must be distinct.")

    checks = []
    selected = None
    openocd_path = None
    try:
        openocd_path = openocd_resolver(openocd)
        checks.append(GatewayReadinessCheck(
            "openocd", "PASS", "OPENOCD_READY",
            "OpenOCD runtime is available.", "No action is required.",
        ))
    except (OSError, RuntimeError, ValueError) as error:
        checks.append(GatewayReadinessCheck(
            "openocd", "FAIL", "OPENOCD_UNAVAILABLE", str(error),
            "Install/reinstall the B300 CLI bundle or configure B300_OPENOCD.",
        ))

    try:
        selected, _probe = select_probe(tuple(probe_discovery()), probe_serial)
        detail = selected.serial or selected.usb_identity or selected.name
        checks.append(GatewayReadinessCheck(
            "probe", "PASS", "PROBE_SELECTED", "ST-Link selected: %s" % detail,
            "No action is required.",
        ))
    except ProbeSelectionError as error:
        checks.append(GatewayReadinessCheck(
            "probe", "FAIL", error.code, error.message,
            "Connect exactly one ST-Link or pass --probe-serial when serial selection is available.",
        ))

    if ssh_probe("127.0.0.1", int(ssh_port)):
        checks.append(GatewayReadinessCheck(
            "ssh", "PASS", "SSH_SERVER_READY",
            "SSH server accepts local TCP connections on port %d." % ssh_port,
            "No action is required; keep key authentication enabled for remote use.",
        ))
    else:
        checks.append(GatewayReadinessCheck(
            "ssh", "FAIL", "SSH_SERVER_UNAVAILABLE",
            "No local SSH server is reachable on port %d." % ssh_port,
            "Install/start OpenSSH Server and allow the SSH port through the host firewall.",
        ))

    for name, port in (("gdb_port", int(gdb_port)), ("tcl_port", int(tcl_port))):
        if port_probe("127.0.0.1", port):
            checks.append(GatewayReadinessCheck(
                name, "PASS", "PORT_AVAILABLE", "127.0.0.1:%d is available." % port,
                "No action is required.",
            ))
        else:
            checks.append(GatewayReadinessCheck(
                name, "FAIL", "PORT_IN_USE", "127.0.0.1:%d is already in use." % port,
                "Stop the conflicting local process before starting B300 Debug Gateway.",
            ))

    addresses = tuple(ipv4_discovery())
    if addresses:
        checks.append(GatewayReadinessCheck(
            "network", "PASS", "IPV4_AVAILABLE",
            "Gateway IPv4 candidate(s): %s" % ", ".join(addresses),
            "Use the address reachable from the Client; do not expose GDB/TCL directly.",
        ))
    else:
        checks.append(GatewayReadinessCheck(
            "network", "LIMITED", "IPV4_NOT_DISCOVERED",
            "No non-loopback IPv4 address was discovered.",
            "Connect LAN/Wi-Fi and confirm the Gateway IP before remote Client testing.",
        ))

    failed = any(check.status == "FAIL" for check in checks)
    limited = any(check.status == "LIMITED" for check in checks)
    conclusion = "BLOCKED" if failed else ("READY_WITH_WARNINGS" if limited else "READY")
    return GatewayReadinessReport(
        tuple(checks), conclusion, not failed, addresses, int(ssh_port), int(gdb_port), int(tcl_port),
        selected, openocd_path,
    )
