"""Argument parsing shared by the backward-compatible CLI entry point."""

from __future__ import annotations

import argparse
import ipaddress
import re
from pathlib import Path
from typing import List


DESCRIPTION = "B300 STM32F407 provisioning and OpenOCD debugging command line tool."


def parse_bind_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError("bind address must be a valid IP address") from error


def parse_tcp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be in range 1..65535")
    return port


def parse_probe_serial(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise argparse.ArgumentTypeError("probe serial contains unsupported characters")
    return value


def _json_option_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """Build the public parser while retaining legacy command spellings."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--version", action="store_true", help="Report CLI version information.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    json_parent = _json_option_parent()
    commands = parser.add_subparsers(dest="command")

    flash = commands.add_parser(
        "flash", help="Provision an Application HEX safely.", parents=[json_parent],
    )
    flash.add_argument("application", type=Path)
    flash.add_argument("--openocd")
    flash.add_argument("--probe-serial", type=parse_probe_serial,
                       help="Select one ST-Link when multiple probes are connected.")
    flash.add_argument("--dry-run", action="store_true")

    factory = commands.add_parser(
        "provision-bootloader",
        help="Factory-provision the trusted bundled B300 Bootloader and restore S0-S2 WRP.",
        parents=[json_parent],
    )
    factory.add_argument("--openocd")
    factory.add_argument("--probe-serial", type=parse_probe_serial,
                         help="Select one ST-Link when multiple probes are connected.")
    factory.add_argument("--dry-run", action="store_true")
    factory.add_argument("--confirm-factory-provision", action="store_true",
                         help="Required for real S0-S2 Bootloader/WRP modification.")

    debug = commands.add_parser(
        "debug", help="Start non-mutating OpenOCD debugging.", parents=[json_parent],
    )
    debug.add_argument("--openocd")
    debug.add_argument("--probe-serial", type=parse_probe_serial,
                       help="Select one ST-Link when multiple probes are connected.")
    debug.add_argument("--bind-address", type=parse_bind_address, default="127.0.0.1",
                       help="OpenOCD listen address (default: 127.0.0.1).")
    debug.add_argument("--gdb-port", type=parse_tcp_port, default=3333,
                       help="GDB server TCP port (default: 3333).")
    debug.add_argument("--telnet-port", type=parse_tcp_port,
                       help="Optional OpenOCD telnet port; loopback only (default: disabled).")
    debug.add_argument("--dry-run", action="store_true")

    doctor = commands.add_parser(
        "doctor", help="Inspect local tool availability.", parents=[json_parent],
    )
    del doctor

    probes = commands.add_parser(
        "probes", help="List discovered ST-Link probes.", parents=[json_parent],
    )
    probes.add_argument("probes_action", nargs="?", choices=("list",), default="list")
    return parser


def parse_args(argv: List[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)
