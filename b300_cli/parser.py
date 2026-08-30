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


def parse_integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a decimal or 0x-prefixed integer") from error


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
    factory.add_argument(
        "--profile", dest="bootloader_profile",
        help=("Select a publisher-bundled trusted Bootloader profile ID. "
              "Arbitrary Bootloader paths/HEX files are not accepted."),
    )
    factory.add_argument("--confirm-factory-provision", action="store_true",
                         help="Required for real S0-S2 Bootloader/WRP modification.")

    debug = commands.add_parser(
        "debug", help="Start non-mutating OpenOCD debugging.", parents=[json_parent],
    )
    debug.add_argument(
        "debug_mode", nargs="?",
        choices=("gateway", "client", "server", "vscode", "symbols", "selftest", "inspect", "where", "registers", "stack", "variable", "sample", "poll", "read-words", "break", "watch"),
        default="gateway", metavar="mode",
        help="Debug mode: gateway, client, selftest, server (legacy alias), or diagnostics.",
    )
    debug.add_argument("--openocd")
    debug.add_argument("--probe-serial", type=parse_probe_serial,
                       help="Select one ST-Link when multiple probes are connected.")
    debug.add_argument("--bind-address", type=parse_bind_address, default="127.0.0.1",
                       help="OpenOCD listen address (default: 127.0.0.1).")
    debug.add_argument("--gdb-port", type=parse_tcp_port, default=3333,
                       help="GDB server TCP port (default: 3333).")
    debug.add_argument("--tcl-port", type=parse_tcp_port,
                       help="Optional OpenOCD TCL port; loopback only (recommended: 6666).")
    debug.add_argument("--telnet-port", type=parse_tcp_port,
                       help="Optional OpenOCD telnet port; loopback only (default: disabled).")
    debug.add_argument("--symbols", type=Path,
                       help="ELF/AXF symbol file for integrated debug diagnostics.")
    debug.add_argument("--symbol-root", action="append", type=Path, default=[],
                       help="Bounded project root searched by debug symbols; repeatable.")
    debug.add_argument("--symbol-max-files", type=parse_integer, default=128,
                       help="Maximum ELF/AXF candidates inspected by debug symbols (default: 128).")
    debug.add_argument("--ssh-host",
                       help="SSH host of the B300 debug gateway for debug vscode.")
    debug.add_argument("--ssh-user",
                       help="Optional SSH username for debug vscode.")
    debug.add_argument("--ssh-port", type=parse_tcp_port, default=22,
                       help="SSH port for remote Client/VSCode (default: 22).")
    debug.add_argument(
        "--client-action",
        choices=("inspect", "where", "registers", "stack", "variable", "sample", "poll",
                 "read-words", "break", "watch"),
        default="inspect",
        help="One-shot operation for debug client (default: inspect).",
    )
    debug.add_argument("--local-tcl-port", type=parse_tcp_port, default=16666,
                       help="Preferred Client-side forwarded TCL port (default: 16666; auto-fallback if busy).")
    debug.add_argument("--local-gdb-port", type=parse_tcp_port, default=3333,
                       help="VSCode-side forwarded GDB port (default: 3333).")
    debug.add_argument("--program-relative",
                       help="Workspace-relative AXF/ELF path used in generated launch.json.")
    debug.add_argument("--vscode-gdb-path",
                       help="Explicit GDB path for Cortex-Debug; default: auto-resolve on this Client.")
    debug.add_argument("--output-dir", type=Path,
                       help="Directory where debug vscode writes the .vscode kit.")
    debug.add_argument("--force", action="store_true",
                       help="Allow debug vscode to replace only its generated kit files.")
    debug.add_argument("--frames", type=parse_integer, default=8,
                       help="Maximum stack frames for inspect/stack (default: 8).")
    debug.add_argument("--expression",
                       help="Allow-listed variable expression for debug variable/watch/sample.")
    debug.add_argument(
        "--sample-expression", action="append", default=[],
        help="Additional allow-listed expression for debug sample; repeatable, max 16 total.",
    )
    debug.add_argument(
        "--samples", type=parse_integer, default=20,
        help="Bounded sample cycles for debug sample (default: 20, max: 1000).",
    )
    debug.add_argument(
        "--sample-interval", type=float, default=0.5,
        help="Delay between sample cycles in seconds (default: 0.5, min: 0.1).",
    )
    debug.add_argument(
        "--sample-output", type=Path,
        help="Optional .csv or .jsonl file for bounded variable samples.",
    )
    debug.add_argument("--location",
                       help="Function or basename:line for one-shot hardware breakpoint.")
    debug.add_argument("--timeout", type=float, default=5.0,
                       help="One-shot breakpoint/watch timeout in seconds (default: 5, max: 60).")
    debug.add_argument("--address", type=parse_integer,
                       help="32-bit aligned address for debug read-words.")
    debug.add_argument("--count", type=parse_integer, default=1,
                       help="Word count for debug read-words (default: 1, max: 256).")
    debug.add_argument("--dry-run", action="store_true")

    gateway = commands.add_parser(
        "gateway", help="Inspect remote Debug Gateway host readiness.", parents=[json_parent],
    )
    gateway.add_argument("gateway_action", nargs="?", choices=("doctor",), default="doctor")
    gateway.add_argument("--openocd")
    gateway.add_argument("--probe-serial", type=parse_probe_serial,
                         help="Select one ST-Link when multiple probes are connected.")
    gateway.add_argument("--ssh-port", type=parse_tcp_port, default=22,
                         help="Local SSH server port expected by Client (default: 22).")
    gateway.add_argument("--gdb-port", type=parse_tcp_port, default=3333,
                         help="Gateway loopback GDB port (default: 3333).")
    gateway.add_argument("--tcl-port", type=parse_tcp_port, default=6666,
                         help="Gateway loopback Safe TCL port (default: 6666).")

    doctor = commands.add_parser(
        "doctor", help="Inspect local tool availability.", parents=[json_parent],
    )
    del doctor

    target = commands.add_parser(
        "target", help="Read-only STM32F407 target diagnostics.", parents=[json_parent],
    )
    target_commands = target.add_subparsers(dest="target_command")
    inspect_target = target_commands.add_parser(
        "inspect", help="Inspect one target without modifying it.", parents=[json_parent],
    )
    inspect_target.add_argument("--openocd")
    inspect_target.add_argument("--probe-serial", type=parse_probe_serial,
                                help="Select one ST-Link when multiple probes are connected.")
    health_target = target_commands.add_parser(
        "health", help="Read AppMeta + installed Application CRC/vector health.",
        parents=[json_parent],
    )
    health_target.add_argument("--openocd")
    health_target.add_argument("--probe-serial", type=parse_probe_serial,
                               help="Select one ST-Link when multiple probes are connected.")

    probes = commands.add_parser(
        "probes", help="List discovered ST-Link probes.", parents=[json_parent],
    )
    probes.add_argument("probes_action", nargs="?", choices=("list",), default="list")

    metadata = commands.add_parser(
        "metadata", help="Read-only OTA metadata inspection.", parents=[json_parent],
    )
    metadata_commands = metadata.add_subparsers(dest="metadata_command")
    metadata_show = metadata_commands.add_parser(
        "show", help="Read and decode OTA metadata without modifying the target.",
        parents=[json_parent],
    )
    metadata_show.add_argument("--openocd")
    metadata_show.add_argument("--probe-serial", type=parse_probe_serial,
                               help="Select one ST-Link when multiple probes are connected.")

    memory = commands.add_parser(
        "memory", help="Read-only bounded STM32F407 flash snapshots.", parents=[json_parent],
    )
    memory_commands = memory.add_subparsers(dest="memory_command")
    for action, help_text in (
            ("read", "Read a bounded flash range."),
            ("dump", "Atomically save a bounded flash range to a host file."),
    ):
        command = memory_commands.add_parser(action, help=help_text, parents=[json_parent])
        command.add_argument("address", type=parse_integer)
        command.add_argument("length", type=parse_integer)
        if action == "dump":
            command.add_argument("output", type=Path)
            command.add_argument("--force", action="store_true",
                                 help="Replace an existing regular output file.")
        command.add_argument("--openocd")
        command.add_argument("--probe-serial", type=parse_probe_serial,
                             help="Select one ST-Link when multiple probes are connected.")
    read_sector = memory_commands.add_parser(
        "read-sector", help="Read one whole bounded flash sector.", parents=[json_parent],
    )
    read_sector.add_argument("sector", type=parse_integer)
    read_sector.add_argument("--openocd")
    read_sector.add_argument("--probe-serial", type=parse_probe_serial,
                             help="Select one ST-Link when multiple probes are connected.")

    support = commands.add_parser(
        "support", help="Create a privacy-bounded read-only diagnostic support bundle.",
        parents=[json_parent],
    )
    support_commands = support.add_subparsers(dest="support_command")
    support_bundle = support_commands.add_parser(
        "bundle", help="Write support.json + README.txt to one bounded ZIP.",
        parents=[json_parent],
    )
    support_bundle.add_argument("output", type=Path)
    support_bundle.add_argument("--force", action="store_true",
                                help="Replace an existing support ZIP atomically.")
    support_bundle.add_argument("--openocd")
    support_bundle.add_argument("--probe-serial", type=parse_probe_serial,
                                help="Select one ST-Link when multiple probes are connected.")

    update = commands.add_parser(
        "update", help="Check for or download a signed CLI update.", parents=[json_parent],
    )
    update_commands = update.add_subparsers(dest="update_command")
    update_commands.add_parser(
        "check", help="Check the signed release manifest.", parents=[json_parent],
    )
    update_download = update_commands.add_parser(
        "download", help="Download and verify the available CLI update.",
        parents=[json_parent],
    )
    update_download.add_argument(
        "--dest", type=Path,
        help="Destination directory (default: the per-user update cache).",
    )
    update_install = update_commands.add_parser(
        "install", help="Verify and hand off a managed per-user CLI update.",
        parents=[json_parent],
    )
    update_install.add_argument(
        "--verified-package", type=Path,
        help=(
            "Reuse this package only when it matches the freshly checked signed asset; "
            "otherwise the signed asset is downloaded again."
        ),
    )

    self_update = commands.add_parser(
        "self-update", help="Alias for update install.", parents=[json_parent],
    )
    self_update.set_defaults(update_command="install")
    self_update.add_argument(
        "--verified-package", type=Path,
        help=(
            "Reuse this package only when it matches the freshly checked signed asset; "
            "otherwise the signed asset is downloaded again."
        ),
    )

    setup = commands.add_parser(
        "setup", help="Inspect Linux USB access; optionally install the canonical udev rule.",
        parents=[json_parent],
    )
    setup.add_argument(
        "--install-udev-rule", action="store_true",
        help="Request installation of the canonical 49-b300-stlink.rules file.",
    )
    setup.add_argument(
        "--confirm-system-change", action="store_true",
        help="Confirm the displayed privileged rule copy, reload, and narrow USB trigger.",
    )
    return parser


def parse_args(argv: List[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)
