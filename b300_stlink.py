#!/usr/bin/env python3
"""B300 STM32F407 provisioning and OpenOCD debugging command line tool."""

from __future__ import annotations

import ipaddress
import sys
import time
from pathlib import Path
from typing import List, Optional

from b300_cli.parser import (
    build_parser,
    parse_args,
    parse_bind_address,
    parse_probe_serial,
    parse_tcp_port,
)
from b300_cli.reporting import (
    Reporter, diagnostic_snapshot, emit_snapshot, format_probes_text, probe_record,
)
from b300_core.diagnostics import DiagnosticsService
from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef
from b300_core.openocd import build_debug_command, resolve_openocd, validate_openocd_value
from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.offline_setup import OPENOCD_VERSION, current_platform_name
from b300_core.policy import (
    APPLICATION_ADDRESS,
    FLASH_END_ADDRESS,
    build_flash_plan,
    build_flash_preview,
)
from b300_core.service import B300Service, ProvisioningError
from b300_core.probe import list_probes
from b300_core.probe_selection import ProbeSelectionError, select_probe
from b300_version import __version__


def validate_openocd_path(path: Path) -> None:
    validate_openocd_value(path, "Application path")


def validate_debug_args(args: argparse.Namespace) -> None:
    if args.telnet_port is not None and not ipaddress.ip_address(args.bind_address).is_loopback:
        raise ValueError("Telnet is allowed only when OpenOCD binds to a loopback address.")


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


def run_debug(args: argparse.Namespace, reporter: Reporter) -> int:
    command = openocd_command(args)
    reporter.emit("openocd", command=command, dry_run=args.dry_run)
    if args.dry_run:
        return 0
    service = DebugService(executable=args.openocd)
    config = DebugConfig(
        ProbeRef(args.probe_serial), args.bind_address, args.gdb_port, args.telnet_port,
    )
    try:
        service.start(config, event_sink=lambda line: reporter.emit("openocd_output", line=line))
        reporter.emit("debug_state", state=DebugState.READY.value)
        while service.state in (DebugState.READY, DebugState.CONNECTED):
            time.sleep(0.2)
        reporter.emit("debug_state", state=service.state.value)
        return 0 if service.state == DebugState.STOPPED else 1
    except KeyboardInterrupt:
        reporter.emit("debug_state", state="STOPPING")
        return 0
    finally:
        service.stop()


def run_openocd(command, dry_run: bool, reporter: Reporter) -> int:
    """Compatibility helper retained for external callers; debug uses DebugService."""
    reporter.emit("openocd", command=command, dry_run=dry_run)
    if dry_run:
        return 0
    raise RuntimeError("Use run_debug() so the B300 hardware session is retained.")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        version_record = {
            "schema_version": 1,
            "command": "version",
            "status": "ok",
            "version": __version__,
            "cli_version": __version__,
            "core_version": __version__,
            "openocd_version": OPENOCD_VERSION,
            "platform": current_platform_name(),
        }
        emit_snapshot(
            version_record,
            args.json,
            "CLI/Core: %s\nOpenOCD: %s\nPlatform: %s" % (
                __version__, OPENOCD_VERSION, version_record["platform"],
            ),
        )
        return 0
    if args.command is None:
        build_parser().error("the following arguments are required: command")
    reporter = Reporter(args.json)
    try:
        if args.command == "probes":
            probes = list_probes()
            if not probes:
                record = {
                    "schema_version": 1,
                    "command": "probes",
                    "status": "error",
                    "reason_code": "NO_PROBE",
                    "message": "No ST-Link probe was found.",
                    "probes": [],
                }
                emit_snapshot(record, args.json, "reason_code=NO_PROBE No ST-Link probe was found.")
                return 1
            records = [probe_record(index, probe) for index, probe in enumerate(probes, start=1)]
            emit_snapshot(
                {
                    "schema_version": 1,
                    "command": "probes",
                    "status": "ok",
                    "probes": records,
                },
                args.json,
                format_probes_text(probes),
            )
            return 0

        if args.command == "doctor":
            probes = list_probes()
            report = DiagnosticsService(
                service=B300Service(), probe_discovery=lambda: probes,
            ).run()
            emit_snapshot(
                diagnostic_snapshot("doctor", report), args.json,
                "%s (%s)" % (report.conclusion, report.reason_code),
            )
            return 0 if report.conclusion == "READY_FOR_APPLICATION_FLASH" else 1

        if args.command == "target" and args.target_command is None:
            record = {
                "schema_version": 1,
                "command": "target",
                "status": "error",
                "reason_code": "TARGET_SUBCOMMAND_REQUIRED",
                "message": "The target command requires the inspect subcommand.",
                "next_action": "Run target inspect to perform read-only target diagnostics.",
            }
            emit_snapshot(record, args.json, "%s: %s" % (record["reason_code"], record["message"]))
            return 1

        if args.command == "target" and args.target_command == "inspect":
            probes = list_probes()
            try:
                _info, probe = select_probe(probes, args.probe_serial)
            except ProbeSelectionError as error:
                record = {
                    "schema_version": 1,
                    "command": "target inspect",
                    "status": "error",
                    "reason_code": error.code,
                    "message": error.message,
                    "next_action": "Connect exactly one ST-Link or select one with --probe-serial.",
                }
                emit_snapshot(record, args.json, "%s: %s" % (error.code, error.message))
                return 1
            report = DiagnosticsService(
                service=B300Service(executable=args.openocd), probe_discovery=lambda: probes,
            ).run(probe.serial)
            emit_snapshot(
                diagnostic_snapshot("target inspect", report), args.json,
                "%s (%s)" % (report.conclusion, report.reason_code),
            )
            return 0 if report.conclusion == "READY_FOR_APPLICATION_FLASH" else 1

        if args.command == "debug":
            validate_debug_args(args)
            return run_debug(args, reporter)

        if args.command == "provision-bootloader":
            service = B300Service(executable=args.openocd)
            trusted = service.trusted_bootloader()
            probe = ProbeRef(args.probe_serial)
            preview = service.factory_preview(trusted.image, probe)
            reporter.emit(
                "factory_artifact",
                bootloader=str(trusted.image.path),
                sha256=trusted.image.sha256,
                source_commit=trusted.source_commit,
                firmware_version=trusted.firmware_version,
                board_token=trusted.board_token,
                start="0x%08X" % trusted.image.start_address,
                end="0x%08X" % trusted.image.end_address,
            )
            preview_transactions = (
                ("unprotect", service.factory_protect_command(probe, False), "if_s0_s2_protected"),
                ("program_verify", service.factory_flash_command(preview), "after_s0_s2_unprotected"),
                ("reprotect", service.factory_protect_command(probe, True), "always_after_factory_attempt"),
                ("reset", service.reset_command(probe), "after_verified_and_reprotected"),
            )
            for phase, command, condition in preview_transactions:
                reporter.emit(
                    "openocd", phase=phase, command=command,
                    dry_run=args.dry_run, condition=condition,
                )
            if args.dry_run:
                return 0
            if not args.confirm_factory_provision:
                raise ProvisioningError(
                    "authorization",
                    "Factory Bootloader provisioning requires --confirm-factory-provision.",
                    "Run --dry-run first, then repeat with explicit factory confirmation for the intended board.",
                )
            if not args.probe_serial:
                raise ProvisioningError(
                    "authorization",
                    "Real Factory provisioning requires --probe-serial to pin the intended ST-Link.",
                    "Reconnect/select the intended probe, note its serial, then repeat the confirmed factory command.",
                )
            try:
                target = service.inspect_target(
                    probe,
                    event_sink=lambda line: reporter.emit("openocd_output", line=line),
                )
                plan = service.factory_plan(trusted.image, probe, target)
            except (RuntimeError, ValueError) as error:
                raise ProvisioningError(
                    "target_check", str(error),
                    "Check ST-Link, board power, F407 identity, and reported sector WRP state.",
                ) from error
            reporter.emit(
                "target",
                device_id="0x%08X" % target.device_id,
                flash_kib=target.flash_kib, voltage=target.target_voltage,
                protection=target.protection_summary,
            )
            outcome = service.provision_bootloader(
                plan,
                event_sink=lambda line: reporter.emit("openocd_output", line=line),
                phase_sink=lambda event: reporter.emit(
                    "factory_phase", phase=event.phase, progress=event.progress,
                    message=event.message, cancellable=event.cancellable,
                ),
            )
            reporter.emit(
                "factory_result", status=outcome.status,
                failure_phase=outcome.failure_phase, reason=outcome.reason,
                next_action=outcome.next_action,
                protection=(outcome.final_target.protection_summary
                            if outcome.final_target is not None else None),
            )
            return 0 if outcome.succeeded else 1

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
            ("reset", service.reset_command(plan.probe)),
        )
        for phase, command in transactions:
            reporter.emit(
                "openocd",
                phase=phase,
                command=command,
                dry_run=args.dry_run,
                condition=("after_verified_ok" if phase == "reset" else "always"),
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
