#!/usr/bin/env python3
"""B300 STM32F407 provisioning and OpenOCD debugging command line tool."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import sys
import tempfile
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
    Reporter, diagnostic_snapshot, emit_snapshot, format_memory_rows, format_metadata_text,
    flash_result_fields, flash_start_fields, format_probes_text, memory_snapshot,
    metadata_snapshot, probe_record,
)
from b300_cli.update_commands import run_update_command
from b300_core.diagnostics import DiagnosticsService
from b300_core.hex_image import inspect_image
from b300_core.linux_usb import (
    LinuxUsbSetupReport,
    SystemChangeConfirmationRequired,
    perform_linux_usb_setup,
)
from b300_core.models import ProbeRef
from b300_core.openocd import build_debug_command, resolve_openocd, validate_openocd_value
from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.debug_session import DebugSession, DebugSessionConfig
from b300_core.offline_setup import OPENOCD_VERSION, current_platform_name
from b300_core.policy import (
    APPLICATION_ADDRESS,
    FLASH_END_ADDRESS,
    build_flash_plan,
    build_flash_preview,
    sector_by_index,
    validate_read_range,
)
from b300_core.service import B300Service, ProvisioningError
from b300_core.probe import list_probes
from b300_core.probe_selection import ProbeSelectionError, select_probe
from b300_version import __version__


def validate_openocd_path(path: Path) -> None:
    validate_openocd_value(path, "Application path")


def validate_debug_args(args: argparse.Namespace) -> None:
    DebugConfig(
        ProbeRef(args.probe_serial), args.bind_address, args.gdb_port,
        args.telnet_port, args.tcl_port,
    ).validate()


def validate_application_hex(application: Path) -> None:
    inspect_image(application)


def openocd_command(args: argparse.Namespace):
    return build_debug_command(
        ProbeRef(args.probe_serial),
        resolve_openocd(args.openocd),
        args.bind_address,
        args.gdb_port,
        args.telnet_port,
        args.tcl_port,
    )


def flash_command(args: argparse.Namespace):
    image = inspect_image(args.application)
    plan = build_flash_preview(image, ProbeRef(args.probe_serial))
    return B300Service(executable=args.openocd).flash_command(plan)


def _frame_record(frame):
    return {
        "level": frame.level,
        "address": ("0x%08X" % frame.address) if frame.address is not None else None,
        "function": frame.function,
        "file": frame.file,
        "fullname": frame.fullname,
        "line": frame.line,
    }


def _register_record(item):
    return {"number": item.number, "name": item.name, "value": item.value}


def run_integrated_debug(args: argparse.Namespace, reporter: Reporter) -> int:
    if args.telnet_port is not None:
        raise ValueError("Integrated debug diagnostics do not enable Telnet.")
    if not 1 <= args.frames <= 64:
        raise ValueError("--frames must be in range 1..64.")
    if args.debug_mode == "variable" and not args.expression:
        raise ValueError("debug variable requires --expression NAME.")
    if args.debug_mode == "read-words" and args.address is None:
        raise ValueError("debug read-words requires --address ADDRESS.")

    tcl_port = args.tcl_port if args.tcl_port is not None else 6666
    symbols = args.symbols.expanduser().resolve() if args.symbols is not None else None
    config = DebugSessionConfig(
        ProbeRef(args.probe_serial), symbols, args.bind_address, args.gdb_port, tcl_port
    )
    config.validate()
    command = build_debug_command(
        config.probe, resolve_openocd(args.openocd), config.bind_address,
        config.gdb_port, None, config.tcl_port,
    )
    if args.dry_run:
        reporter.emit(
            "debug_plan", mode=args.debug_mode, command=command, symbols=str(symbols) if symbols else None,
            gdb_endpoint="%s:%d" % (config.bind_address, config.gdb_port),
            tcl_endpoint="%s:%d" % (config.bind_address, config.tcl_port),
            preserve_target_state=True, dry_run=True,
        )
        return 0

    session = DebugSession(service=DebugService(executable=args.openocd))
    try:
        info = session.start(config)
        base = {
            "schema_version": 1,
            "command": "debug %s" % args.debug_mode,
            "status": "ok",
            "gdb_endpoint": info.gdb_endpoint,
            "tcl_endpoint": info.tcl_endpoint,
            "tcl_version": info.tcl_version,
            "initial_target_state": info.initial_target_state,
            "symbols": info.symbols,
        }
        if args.debug_mode == "poll":
            base["target_state"] = session.target_poll()
            text = base["target_state"]
        elif args.debug_mode == "read-words":
            values = session.read_words(args.address, args.count)
            base.update({
                "address": "0x%08X" % args.address,
                "count": args.count,
                "words": ["0x%08X" % value for value in values],
            })
            text = "%s: %s" % (base["address"], " ".join(base["words"]))
        elif args.debug_mode == "where":
            frame = session.capture_where()
            base["frame"] = _frame_record(frame)
            text = "%s %s:%s" % (
                base["frame"]["function"] or "?",
                base["frame"]["file"] or "?",
                base["frame"]["line"] if base["frame"]["line"] is not None else "?",
            )
        elif args.debug_mode == "stack":
            frames = session.capture_stack(args.frames)
            base["frames"] = [_frame_record(frame) for frame in frames]
            text = "\n".join(
                "#%d %s %s:%s" % (
                    item["level"], item["function"] or "?", item["file"] or "?",
                    item["line"] if item["line"] is not None else "?",
                ) for item in base["frames"]
            ) or "No stack frames reported."
        elif args.debug_mode == "registers":
            registers = session.capture_registers()
            base["registers"] = [_register_record(item) for item in registers]
            text = "\n".join("%s=%s" % (item["name"], item["value"]) for item in base["registers"])
        elif args.debug_mode == "variable":
            value = session.capture_variable(args.expression)
            base["variable"] = {"expression": value.expression, "value": value.value}
            text = "%s=%s" % (value.expression, value.value)
        elif args.debug_mode == "inspect":
            snapshot = session.inspect(args.frames)
            base.update({
                "target_state_before": snapshot.target_state_before,
                "resumed_to_initial_state": snapshot.resumed,
                "frame": _frame_record(snapshot.frame),
                "frames": [_frame_record(frame) for frame in snapshot.stack],
                "registers": [_register_record(item) for item in snapshot.registers],
            })
            frame = base["frame"]
            text = "state=%s\nPC=%s\n%s %s:%s" % (
                snapshot.target_state_before,
                frame["address"] or "?", frame["function"] or "?",
                frame["file"] or "?", frame["line"] if frame["line"] is not None else "?",
            )
        else:
            raise ValueError("Unsupported integrated debug mode: %s" % args.debug_mode)
        emit_snapshot(base, args.json, text)
        return 0
    finally:
        session.stop()


def run_debug(args: argparse.Namespace, reporter: Reporter) -> int:
    if not ipaddress.ip_address(args.bind_address).is_loopback:
        reporter.emit(
            "warning",
            reason_code="REMOTE_GDB_INSECURE",
            message="GDB Remote Protocol is unauthenticated and unencrypted on a non-loopback bind.",
            next_action="Prefer loopback access through an SSH tunnel.",
        )
    command = openocd_command(args)
    reporter.emit("openocd", command=command, dry_run=args.dry_run)
    if args.dry_run:
        return 0
    service = DebugService(executable=args.openocd)
    config = DebugConfig(
        ProbeRef(args.probe_serial), args.bind_address, args.gdb_port,
        args.telnet_port, args.tcl_port,
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


def _read_only_error(args: argparse.Namespace, command: str, reason_code: str,
                     message: str) -> int:
    record = {
        "schema_version": 1,
        "command": command,
        "status": "error",
        "reason_code": reason_code,
        "message": message,
    }
    emit_snapshot(record, args.json, "%s: %s" % (reason_code, message))
    return 1


def _select_read_probe(args: argparse.Namespace, command: str) -> Optional[ProbeRef]:
    try:
        _info, probe = select_probe(list_probes(), args.probe_serial)
        return probe
    except ProbeSelectionError as error:
        _read_only_error(args, command, error.code, error.message)
        return None


def _select_write_probe(args: argparse.Namespace, reporter: Reporter) -> Optional[ProbeRef]:
    """Select one discovered probe and preserve core selection reason codes."""
    try:
        _info, probe = select_probe(list_probes(), args.probe_serial)
        return probe
    except ProbeSelectionError as error:
        reporter.emit(
            "error",
            phase="probe_selection",
            reason_code=error.code,
            reason=error.message,
            next_action=(
                "Connect exactly one ST-Link or select the intended probe with --probe-serial."
            ),
        )
        return None


def _validated_output_path(path: Path, force: bool) -> Path:
    output = path.expanduser().resolve()
    if not output.parent.is_dir() or output.is_dir():
        raise ValueError("Output path must name a file in an existing directory.")
    if output.exists() and not force:
        raise FileExistsError("Output file already exists; use --force to replace it.")
    return output


def _atomic_write_snapshot(output: Path, data: bytes, force: bool) -> None:
    """Atomically replace a host snapshot only after its complete read succeeded."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".%s." % output.name, suffix=".tmp",
                dir=str(output.parent), delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if output.exists() and not force:
            raise FileExistsError("Output file already exists; use --force to replace it.")
        os.replace(str(temporary), str(output))
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _memory_dump_record(command: str, address: int, data: bytes, output: Path) -> dict:
    record = memory_snapshot(command, address, data)
    del record["data"]
    record["output"] = str(output)
    record["sha256"] = hashlib.sha256(data).hexdigest()
    return record


def _linux_setup_record(report: LinuxUsbSetupReport) -> dict:
    return {
        "schema_version": 1,
        "command": "setup",
        "status": "ok" if report.supported else "error",
        "supported": report.supported,
        "rule_installed": report.rule_installed,
        "dry_run": report.dry_run,
        "changed": report.changed,
        "reason_code": report.reason_code,
        "message": report.message,
        "next_action": report.next_action,
        "rule_path": str(report.rule_path),
        "commands": [list(command) for command in report.commands],
    }


def main(argv: Optional[List[str]] = None) -> int:
    selected_argv = list(sys.argv[1:] if argv is None else argv)
    if selected_argv and selected_argv[0] == "--apply-cli-update":
        from b300_core.cli_update_install import main as cli_update_helper_main
        return cli_update_helper_main(selected_argv[1:])
    args = parse_args(selected_argv)
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
        if args.command in {"update", "self-update"}:
            return run_update_command(args, __version__)

        if args.command == "setup":
            def announce_setup(plan: LinuxUsbSetupReport) -> None:
                reporter.emit(
                    "setup_plan",
                    command="setup",
                    rule_path=str(plan.rule_path),
                    commands=[list(command) for command in plan.commands],
                    message=plan.message,
                )

            try:
                report = perform_linux_usb_setup(
                    install_requested=args.install_udev_rule,
                    confirmed=args.confirm_system_change,
                    announce=announce_setup,
                )
            except SystemChangeConfirmationRequired as error:
                record = {
                    "schema_version": 1,
                    "command": "setup",
                    "status": "error",
                    "reason_code": error.reason_code,
                    "message": str(error),
                }
                emit_snapshot(record, args.json, "%s: %s" % (error.reason_code, error))
                return 1
            record = _linux_setup_record(report)
            text = "%s: %s\nnext_action=%s" % (
                report.reason_code, report.message, report.next_action,
            )
            emit_snapshot(record, args.json, text)
            return 0 if report.supported else 1

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

        if args.command == "metadata" and args.metadata_command is None:
            return _read_only_error(
                args,
                "metadata",
                "METADATA_SUBCOMMAND_REQUIRED",
                "The metadata command requires the show subcommand.",
            )

        if args.command == "memory" and args.memory_command is None:
            return _read_only_error(
                args,
                "memory",
                "MEMORY_SUBCOMMAND_REQUIRED",
                "The memory command requires read, read-sector, or dump.",
            )

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

        if args.command == "metadata" and args.metadata_command == "show":
            probe = _select_read_probe(args, "metadata show")
            if probe is None:
                return 1
            try:
                metadata = B300Service(executable=args.openocd).read_metadata(probe)
            except (OSError, RuntimeError, ValueError) as error:
                return _read_only_error(args, "metadata show", "MEMORY_READ_FAILED", str(error))
            record = metadata_snapshot(metadata)
            emit_snapshot(record, args.json, format_metadata_text(metadata))
            return 0

        if args.command == "memory" and args.memory_command in ("read", "dump"):
            command = "memory %s" % args.memory_command
            try:
                validate_read_range(args.address, args.length)
            except ValueError as error:
                return _read_only_error(args, command, "INVALID_MEMORY_RANGE", str(error))
            output = None
            if args.memory_command == "dump":
                try:
                    output = _validated_output_path(args.output, args.force)
                except FileExistsError as error:
                    return _read_only_error(args, command, "OUTPUT_EXISTS", str(error))
                except (OSError, RuntimeError, ValueError) as error:
                    return _read_only_error(args, command, "INVALID_OUTPUT_PATH", str(error))
            probe = _select_read_probe(args, command)
            if probe is None:
                return 1
            try:
                data = B300Service(executable=args.openocd).read_memory(
                    probe, args.address, args.length,
                )
                if len(data) != args.length:
                    raise RuntimeError("Memory read length mismatch.")
            except (OSError, RuntimeError, ValueError) as error:
                return _read_only_error(args, command, "MEMORY_READ_FAILED", str(error))
            if args.memory_command == "read":
                emit_snapshot(
                    memory_snapshot(command, args.address, data), args.json,
                    format_memory_rows(args.address, data),
                )
                return 0
            assert output is not None
            try:
                _atomic_write_snapshot(output, data, args.force)
            except FileExistsError as error:
                return _read_only_error(args, command, "OUTPUT_EXISTS", str(error))
            except OSError as error:
                return _read_only_error(args, command, "INVALID_OUTPUT_PATH", str(error))
            record = _memory_dump_record(command, args.address, data, output)
            text = "address=%s end_address=%s size=%d output=%s sha256=%s" % (
                record["address"], record["end_address"], record["size"], record["output"],
                record["sha256"].upper(),
            )
            emit_snapshot(record, args.json, text)
            return 0

        if args.command == "memory" and args.memory_command == "read-sector":
            command = "memory read-sector"
            try:
                sector = sector_by_index(args.sector)
            except ValueError as error:
                return _read_only_error(args, command, "INVALID_SECTOR", str(error))
            probe = _select_read_probe(args, command)
            if probe is None:
                return 1
            try:
                data = B300Service(executable=args.openocd).read_sector(probe, args.sector)
            except (OSError, RuntimeError, ValueError) as error:
                return _read_only_error(args, command, "MEMORY_READ_FAILED", str(error))
            emit_snapshot(
                memory_snapshot(command, sector.start_address, data), args.json,
                format_memory_rows(sector.start_address, data),
            )
            return 0

        if args.command == "debug":
            if args.debug_mode == "server":
                validate_debug_args(args)
                return run_debug(args, reporter)
            return run_integrated_debug(args, reporter)

        if args.command == "provision-bootloader":
            if not args.dry_run and not args.confirm_factory_provision:
                raise ProvisioningError(
                    "authorization",
                    "Factory Bootloader provisioning requires --confirm-factory-provision.",
                    "Run --dry-run first, then repeat with explicit factory confirmation for the intended board.",
                )
            service = B300Service(executable=args.openocd)
            trusted = service.trusted_bootloader()
            if args.dry_run:
                probe = ProbeRef(args.probe_serial)
            else:
                probe = _select_write_probe(args, reporter)
                if probe is None:
                    return 1
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
        target = None
        if args.dry_run:
            probe = ProbeRef(args.probe_serial)
            plan = service.preview_plan(image, probe)
        else:
            probe = _select_write_probe(args, reporter)
            if probe is None:
                return 1
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
        reporter.emit("flash_start", **flash_start_fields(plan, target, dry_run=args.dry_run))
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
        assert target is not None
        reporter.emit("flash_result", **flash_result_fields(outcome, target))
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
