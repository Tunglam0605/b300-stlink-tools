#!/usr/bin/env python3
"""B300 STM32F407 provisioning and OpenOCD debugging command line tool."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef
from b300_core.openocd import (
    OpenOcdRunner,
    build_debug_command,
    resolve_openocd,
    validate_openocd_value,
)
from b300_core.policy import (
    APPLICATION_ADDRESS,
    FLASH_END_ADDRESS,
    STLINK_PROVISION_MAGIC,
    build_flash_plan,
    build_flash_preview,
)
from b300_core.service import B300Service, ProvisioningError


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


def validate_openocd_path(path: Path) -> None:
    validate_openocd_value(path, "Application path")


def validate_debug_args(args: argparse.Namespace) -> None:
    if args.telnet_port is not None and not ipaddress.ip_address(args.bind_address).is_loopback:
        raise ValueError("Telnet is allowed only when OpenOCD binds to a loopback address.")


class Reporter:
    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    def emit(self, event: str, **fields: object) -> None:
        record = {"event": event, **fields}
        if self.as_json:
            print(json.dumps(record, sort_keys=True), flush=True)
        else:
            print("[%s] %s" % (event, " ".join(
                "%s=%s" % (key, value) for key, value in fields.items())), flush=True)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    flash = commands.add_parser("flash", help="Provision an Application HEX safely.")
    flash.add_argument("application", type=Path)
    flash.add_argument("--openocd")
    flash.add_argument("--probe-serial", type=parse_probe_serial,
                       help="Select one ST-Link when multiple probes are connected.")
    flash.add_argument("--dry-run", action="store_true")
    flash.add_argument("--json", action="store_true")
    debug = commands.add_parser("debug", help="Start non-mutating OpenOCD debugging.")
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
    debug.add_argument("--json", action="store_true")
    doctor = commands.add_parser("doctor", help="Inspect local tool availability.")
    doctor.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def validate_application_hex(application: Path) -> None:
    inspect_image(application)


def openocd_command(args: argparse.Namespace):
    return build_debug_command(
        ProbeRef(args.probe_serial),
        resolve_openocd(args.openocd),
        args.bind_address,
        args.gdb_port,
        args.telnet_port,
    )


def flash_command(args: argparse.Namespace):
    image = inspect_image(args.application)
    plan = build_flash_preview(image, ProbeRef(args.probe_serial))
    return B300Service(executable=args.openocd).flash_command(plan)


def run_openocd(command, dry_run: bool, reporter: Reporter) -> int:
    reporter.emit("openocd", command=command, dry_run=dry_run)
    if dry_run:
        return 0
    result = OpenOcdRunner().run(
        command,
        event_sink=lambda line: reporter.emit("openocd_output", line=line),
    )
    return result.returncode


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    reporter = Reporter(args.json)
    try:
        if args.command == "doctor":
            available, executable = B300Service().doctor()
            reporter.emit("dependency", name="OpenOCD", available=available, path=executable)
            return 0 if available else 1

        if args.command == "debug":
            validate_debug_args(args)
            return run_openocd(openocd_command(args), args.dry_run, reporter)

        args.application = args.application.expanduser().resolve()
        service = B300Service(executable=args.openocd)
        try:
            validate_openocd_path(args.application)
            image = service.inspect_image(args.application)
        except ValueError as error:
            raise ProvisioningError(
                "validating",
                str(error),
                "Select a valid B300 F407 Application HEX linked at 0x08010000.",
            ) from error
        probe = ProbeRef(args.probe_serial)
        if args.dry_run:
            plan = service.preview_plan(image, probe)
        else:
            try:
                target = service.inspect_target(
                    probe,
                    event_sink=lambda line: reporter.emit("openocd_output", line=line),
                )
                plan = service.plan(image, probe, target)
            except (RuntimeError, ValueError) as error:
                if isinstance(error, ProvisioningError):
                    raise
                raise ProvisioningError(
                    "target_check",
                    str(error),
                    "Check the selected ST-Link serial, cable, power, and F407 target.",
                ) from error
            reporter.emit(
                "target",
                device_id="0x%08X" % target.device_id,
                flash_kib=target.flash_kib,
                voltage=target.target_voltage,
                protection=target.protection_summary,
            )
        reporter.emit(
            "flash_start",
            application=str(image.path),
            sha256=image.sha256,
            start="0x%08X" % image.start_address,
            end="0x%08X" % image.end_address,
            dry_run=args.dry_run,
        )
        transactions = (
            ("program_verify", service.flash_command(plan)),
            ("mark", service.marker_command(plan.probe)),
            ("reset", service.reset_command(plan.probe)),
        )
        for phase, command in transactions:
            reporter.emit(
                "openocd",
                phase=phase,
                command=command,
                dry_run=args.dry_run,
                condition=("after_verified_ok" if phase == "mark" else
                           "after_marker" if phase == "reset" else "always"),
            )
        if args.dry_run:
            return 0

        outcome = service.flash(
            plan,
            event_sink=lambda line: reporter.emit("openocd_output", line=line),
            phase_sink=lambda event: reporter.emit(
                "flash_phase",
                phase=event.phase,
                progress=event.progress,
                message=event.message,
                cancellable=event.cancellable,
            ),
        )
        fields = {
            "status": outcome.status,
            "failure_phase": outcome.failure_phase,
            "reason": outcome.reason,
            "next_action": outcome.next_action,
        }
        if outcome.boot_verification is not None:
            fields.update({
                "pc": "0x%08X" % outcome.boot_verification.pc
                if outcome.boot_verification.pc is not None else None,
                "bkp1r": outcome.boot_verification.bkp1r,
                "bkp4r": outcome.boot_verification.bkp4r,
                "reason": outcome.boot_verification.reason,
            })
        reporter.emit("flash_result", **fields)
        return 0 if outcome.succeeded else 1
    except (OSError, RuntimeError, ValueError) as error:
        fields = {"message": str(error)}
        if hasattr(error, "phase"):
            fields.update({
                "phase": error.phase,
                "reason": error.reason,
                "next_action": error.next_action,
            })
        reporter.emit("error", **fields)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
