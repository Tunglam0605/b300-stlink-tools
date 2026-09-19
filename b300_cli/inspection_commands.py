"""Read-only CLI inspection commands for B300 targets.

This module owns the cohesive doctor/target/metadata/memory command family while
keeping mutation workflows in the top-level compatibility facade. Callers pass
runtime dependencies explicitly so historical tests and integrations that patch
b300_stlink.B300Service, DiagnosticsService or probe discovery retain the same
seam after extraction.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from b300_cli.output_paths import validated_output_path
from b300_cli.reporting import (
    application_health_snapshot,
    diagnostic_snapshot,
    emit_snapshot,
    format_application_health_text,
    format_memory_rows,
    format_metadata_text,
    memory_snapshot,
    metadata_snapshot,
)
from b300_core.models import ProbeRef
from b300_core.policy import sector_by_index, validate_read_range
from b300_core.probe_selection import ProbeSelectionError, select_probe


def read_only_error(args, command: str, reason_code: str, message: str) -> int:
    """Emit the stable read-only error snapshot used by CLI diagnostics."""
    record = {
        "schema_version": 1,
        "command": command,
        "status": "error",
        "reason_code": reason_code,
        "message": message,
    }
    emit_snapshot(record, args.json, "%s: %s" % (reason_code, message))
    return 1


def select_read_probe(
        args,
        command: str,
        *,
        probe_loader: Callable,
        select_probe_fn: Callable = select_probe) -> Optional[ProbeRef]:
    """Select one probe without creating any hardware mutation surface."""
    try:
        _info, probe = select_probe_fn(probe_loader(), args.probe_serial)
        return probe
    except ProbeSelectionError as error:
        read_only_error(args, command, error.code, error.message)
        return None


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


def run_inspection_command(
        args,
        *,
        probe_loader: Callable,
        service_factory: Callable,
        diagnostics_factory: Callable,
        select_probe_fn: Callable = select_probe) -> Optional[int]:
    """Handle doctor/target/metadata/memory or return None for other commands."""
    command_name = getattr(args, "command", None)
    if command_name not in {"doctor", "target", "metadata", "memory"}:
        return None

    if command_name == "doctor":
        probes = probe_loader()
        report = diagnostics_factory(
            service=service_factory(), probe_discovery=lambda: probes,
        ).run()
        emit_snapshot(
            diagnostic_snapshot("doctor", report), args.json,
            "%s (%s)" % (report.conclusion, report.reason_code),
        )
        return 0 if report.conclusion == "READY_FOR_APPLICATION_FLASH" else 1

    if command_name == "target" and args.target_command is None:
        record = {
            "schema_version": 1,
            "command": "target",
            "status": "error",
            "reason_code": "TARGET_SUBCOMMAND_REQUIRED",
            "message": "The target command requires the inspect or health subcommand.",
            "next_action": (
                "Run target inspect for a quick snapshot or target health for "
                "CRC/vector bootability evidence."
            ),
        }
        emit_snapshot(
            record, args.json,
            "%s: %s" % (record["reason_code"], record["message"]),
        )
        return 1

    if command_name == "metadata" and args.metadata_command is None:
        return read_only_error(
            args,
            "metadata",
            "METADATA_SUBCOMMAND_REQUIRED",
            "The metadata command requires the show subcommand.",
        )

    if command_name == "memory" and args.memory_command is None:
        return read_only_error(
            args,
            "memory",
            "MEMORY_SUBCOMMAND_REQUIRED",
            "The memory command requires read, read-sector, or dump.",
        )

    if command_name == "target" and args.target_command == "inspect":
        probes = probe_loader()
        try:
            _info, probe = select_probe_fn(probes, args.probe_serial)
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
        report = diagnostics_factory(
            service=service_factory(executable=args.openocd),
            probe_discovery=lambda: probes,
        ).run(probe.serial)
        emit_snapshot(
            diagnostic_snapshot("target inspect", report), args.json,
            "%s (%s)" % (report.conclusion, report.reason_code),
        )
        return 0 if report.conclusion == "READY_FOR_APPLICATION_FLASH" else 1

    if command_name == "target" and args.target_command == "health":
        probe = select_read_probe(
            args,
            "target health",
            probe_loader=probe_loader,
            select_probe_fn=select_probe_fn,
        )
        if probe is None:
            return 1
        try:
            health = service_factory(executable=args.openocd).inspect_application_health(probe)
        except (OSError, RuntimeError, ValueError) as error:
            return read_only_error(
                args, "target health", "APPLICATION_HEALTH_READ_FAILED", str(error)
            )
        emit_snapshot(
            application_health_snapshot(health), args.json,
            format_application_health_text(health),
        )
        return 0 if health.bootable else 1

    if command_name == "metadata" and args.metadata_command == "show":
        probe = select_read_probe(
            args,
            "metadata show",
            probe_loader=probe_loader,
            select_probe_fn=select_probe_fn,
        )
        if probe is None:
            return 1
        try:
            metadata = service_factory(executable=args.openocd).read_metadata(probe)
        except (OSError, RuntimeError, ValueError) as error:
            return read_only_error(args, "metadata show", "MEMORY_READ_FAILED", str(error))
        record = metadata_snapshot(metadata)
        emit_snapshot(record, args.json, format_metadata_text(metadata))
        return 0

    if command_name == "memory" and args.memory_command in ("read", "dump"):
        command = "memory %s" % args.memory_command
        try:
            validate_read_range(args.address, args.length)
        except ValueError as error:
            return read_only_error(args, command, "INVALID_MEMORY_RANGE", str(error))
        output = None
        if args.memory_command == "dump":
            try:
                output = validated_output_path(args.output, args.force)
            except FileExistsError as error:
                return read_only_error(args, command, "OUTPUT_EXISTS", str(error))
            except (OSError, RuntimeError, ValueError) as error:
                return read_only_error(args, command, "INVALID_OUTPUT_PATH", str(error))
        probe = select_read_probe(
            args,
            command,
            probe_loader=probe_loader,
            select_probe_fn=select_probe_fn,
        )
        if probe is None:
            return 1
        try:
            data = service_factory(executable=args.openocd).read_memory(
                probe, args.address, args.length,
            )
            if len(data) != args.length:
                raise RuntimeError("Memory read length mismatch.")
        except (OSError, RuntimeError, ValueError) as error:
            return read_only_error(args, command, "MEMORY_READ_FAILED", str(error))
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
            return read_only_error(args, command, "OUTPUT_EXISTS", str(error))
        except OSError as error:
            return read_only_error(args, command, "INVALID_OUTPUT_PATH", str(error))
        record = _memory_dump_record(command, args.address, data, output)
        text = "address=%s end_address=%s size=%d output=%s sha256=%s" % (
            record["address"], record["end_address"], record["size"], record["output"],
            record["sha256"].upper(),
        )
        emit_snapshot(record, args.json, text)
        return 0

    if command_name == "memory" and args.memory_command == "read-sector":
        command = "memory read-sector"
        try:
            sector = sector_by_index(args.sector)
        except ValueError as error:
            return read_only_error(args, command, "INVALID_SECTOR", str(error))
        probe = select_read_probe(
            args,
            command,
            probe_loader=probe_loader,
            select_probe_fn=select_probe_fn,
        )
        if probe is None:
            return 1
        try:
            data = service_factory(executable=args.openocd).read_sector(probe, args.sector)
        except (OSError, RuntimeError, ValueError) as error:
            return read_only_error(args, command, "MEMORY_READ_FAILED", str(error))
        emit_snapshot(
            memory_snapshot(command, sector.start_address, data), args.json,
            format_memory_rows(sector.start_address, data),
        )
        return 0

    return None


__all__ = [
    "read_only_error",
    "run_inspection_command",
    "select_read_probe",
]
