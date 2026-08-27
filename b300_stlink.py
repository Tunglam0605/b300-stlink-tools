#!/usr/bin/env python3
"""B300 STM32F407 provisioning and OpenOCD debugging command line tool."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


APPLICATION_ADDRESS = 0x08010000
FLASH_END_ADDRESS = 0x08080000
STLINK_PROVISION_MAGIC = 0x53544C4B


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
    flash.add_argument("--probe-serial")
    flash.add_argument("--dry-run", action="store_true")
    flash.add_argument("--json", action="store_true")
    debug = commands.add_parser("debug", help="Start non-mutating OpenOCD debugging.")
    debug.add_argument("--openocd")
    debug.add_argument("--probe-serial")
    debug.add_argument("--gdb-port", type=int, default=3333)
    debug.add_argument("--telnet-port", type=int, default=4444)
    debug.add_argument("--dry-run", action="store_true")
    debug.add_argument("--json", action="store_true")
    doctor = commands.add_parser("doctor", help="Inspect local tool availability.")
    doctor.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def validate_application_hex(application: Path) -> None:
    """Reject malformed HEX or data outside Application flash (S4--S7)."""
    upper_address = 0
    data_records = 0
    try:
        lines = application.read_text(encoding="ascii").splitlines()
    except OSError as error:
        raise ValueError("Cannot read application HEX: %s" % error) from error
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError("HEX line %d does not start with ':'." % line_number)
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as error:
            raise ValueError("HEX line %d is not hexadecimal." % line_number) from error
        if len(record) < 5 or len(record) != record[0] + 5:
            raise ValueError("HEX line %d has an invalid length." % line_number)
        if sum(record) & 0xFF:
            raise ValueError("HEX line %d has an invalid checksum." % line_number)
        length, record_type = record[0], record[3]
        offset = (record[1] << 8) | record[2]
        data = record[4:4 + length]
        if record_type == 0x04:
            if length != 2:
                raise ValueError("HEX line %d has invalid extended address." % line_number)
            upper_address = ((data[0] << 8) | data[1]) << 16
        elif record_type == 0 and length:
            start, end = upper_address + offset, upper_address + offset + length
            if start < APPLICATION_ADDRESS or end > FLASH_END_ADDRESS:
                raise ValueError("HEX touches protected range 0x%08X..0x%08X." %
                                 (start, end - 1))
            data_records += 1
    if not data_records:
        raise ValueError("Application HEX contains no application data records.")


def openocd_command(args: argparse.Namespace) -> List[str]:
    executable = args.openocd or os.environ.get("B300_OPENOCD") or shutil.which("openocd") or "openocd"
    command = [executable, "-f", "interface/stlink.cfg", "-c", "transport select swd",
               "-f", "target/stm32f4x.cfg", "-c", "gdb port %d" % getattr(args, "gdb_port", 3333),
               "-c", "telnet port %d" % getattr(args, "telnet_port", 4444)]
    if args.probe_serial:
        command.extend(["-c", "adapter serial %s" % args.probe_serial])
    command.extend(["-c", "init"])
    return command


def flash_command(args: argparse.Namespace) -> List[str]:
    command = openocd_command(args)
    del command[-2:]
    command.extend([
        "-c", "init", "-c", "reset init",
        "-c", "flash erase_sector 0 3 7",
        "-c", "program {%s} verify" % args.application,
        "-c", "mww 0x40023840 0x10000000",
        "-c", "mww 0x40007000 0x00000100",
        "-c", "mww 0x40002860 0x%08X" % STLINK_PROVISION_MAGIC,
        "-c", "reset run", "-c", "shutdown",
    ])
    return command


def run_openocd(command: List[str], dry_run: bool, reporter: Reporter) -> int:
    reporter.emit("openocd", command=command, dry_run=dry_run)
    if dry_run:
        return 0
    if shutil.which(command[0]) is None and not Path(command[0]).is_file():
        reporter.emit("error", message="OpenOCD was not found.")
        return 1
    return subprocess.call(command)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    reporter = Reporter(args.json)
    try:
        if args.command == "doctor":
            tool = os.environ.get("B300_OPENOCD") or shutil.which("openocd")
            reporter.emit("dependency", name="OpenOCD", available=bool(tool), path=tool)
            return 0 if tool else 1
        if args.command == "debug":
            return run_openocd(openocd_command(args), args.dry_run, reporter)
        args.application = args.application.resolve()
        validate_application_hex(args.application)
        reporter.emit("flash_start", application=str(args.application), dry_run=args.dry_run)
        return run_openocd(flash_command(args), args.dry_run, reporter)
    except (OSError, RuntimeError, ValueError) as error:
        reporter.emit("error", message=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
