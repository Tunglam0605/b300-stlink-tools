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
from b300_core.gateway_readiness import inspect_gateway_readiness
from b300_core.gdb_runtime import resolve_gdb
from b300_core.hex_image import inspect_image
from b300_core.linux_usb import (
    LinuxUsbSetupReport,
    SystemChangeConfirmationRequired,
    perform_linux_usb_setup,
)
from b300_core.metadata import build_stlink_metadata
from b300_core.models import ProbeRef
from b300_core.openocd import (
    build_debug_command, build_metadata_write_command, resolve_openocd,
    validate_openocd_value,
)
from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.debug_session import DebugSession, DebugSessionConfig
from b300_core.debug_selftest import run_loopback_debug_selftest
from b300_core.debug_sampling import sample_variables, validate_sampling_request, write_samples
from b300_core.elf_matcher import discover_symbol_files, find_matching_symbol_file
from b300_core.tcl_client import SafeTclClient, TclEndpoint
from b300_core.ssh_debug_tunnel import (
    SshDebugTunnel, SshDebugTunnelConfig, find_available_loopback_port,
)
from b300_core.remote_vscode import RemoteVsCodeProfile, workspace_executable
from b300_core.remote_debug_guard import RemoteDebugGuard
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


def _resolve_vscode_gdb_path(explicit: Optional[str]) -> str:
    if explicit:
        return resolve_gdb(explicit)
    try:
        return resolve_gdb()
    except (FileNotFoundError, RuntimeError):
        return "arm-none-eabi-gdb"


def run_vscode_profile(args: argparse.Namespace) -> int:
    if not args.ssh_host or not args.ssh_user:
        raise ValueError("debug vscode requires --ssh-host HOST and --ssh-user USER.")
    if not args.program_relative:
        raise ValueError("debug vscode requires --program-relative PATH_TO_AXF_OR_ELF.")
    profile = RemoteVsCodeProfile(
        ssh_host=args.ssh_host,
        ssh_user=args.ssh_user,
        ssh_port=args.ssh_port,
        local_gdb_port=args.local_gdb_port,
        remote_gdb_port=args.gdb_port,
        executable=workspace_executable(args.program_relative),
        gdb_path=_resolve_vscode_gdb_path(args.vscode_gdb_path),
        probe_serial=args.probe_serial,
    )
    record = profile.record()
    if args.output_dir is not None:
        outputs = profile.write_kit(args.output_dir, force=args.force)
        record["kit_files"] = [str(path) for path in outputs]
    if args.json:
        emit_snapshot(record, True, "")
    else:
        text = profile.instructions_text()
        if args.output_dir is not None:
            text += "\nGenerated kit: %s" % Path(args.output_dir).expanduser().resolve()
        emit_snapshot(record, False, text)
    return 0


def run_symbol_match(args: argparse.Namespace, reporter: Reporter) -> int:
    """Find one unique ELF/AXF whose sampled Application bytes match target Flash."""
    if args.telnet_port is not None:
        raise ValueError("debug symbols does not enable Telnet.")
    if not ipaddress.ip_address(args.bind_address).is_loopback:
        raise ValueError("debug symbols is loopback-only; use an SSH/VPN tunnel for remote access.")
    if not args.symbol_root:
        raise ValueError("debug symbols requires at least one --symbol-root PROJECT_DIR.")
    if not 1 <= args.symbol_max_files <= 512:
        raise ValueError("--symbol-max-files must be in range 1..512.")
    roots = tuple(Path(root).expanduser().resolve() for root in args.symbol_root)
    candidates = discover_symbol_files(roots, max_files=args.symbol_max_files)
    tcl_port = args.tcl_port if args.tcl_port is not None else 6666
    config = DebugConfig(
        ProbeRef(args.probe_serial), args.bind_address, args.gdb_port, None, tcl_port,
    )
    config.validate()
    if args.dry_run:
        record = {
            "schema_version": 1,
            "command": "debug symbols",
            "status": "planned",
            "roots": [str(root) for root in roots],
            "candidate_count": len(candidates),
            "candidates": [str(path) for path in candidates],
            "max_files": args.symbol_max_files,
            "preserve_target_state": True,
            "requires_gdb": False,
        }
        emit_snapshot(record, args.json, "Found %d ELF/AXF candidate(s)." % len(candidates))
        return 0
    if not candidates:
        record = {
            "schema_version": 1,
            "command": "debug symbols",
            "status": "no_candidates",
            "roots": [str(root) for root in roots],
            "candidate_count": 0,
            "selected": None,
        }
        emit_snapshot(record, args.json, "No ELF/AXF candidates found in the bounded search roots.")
        return 1

    service = DebugService(executable=args.openocd)
    try:
        service.start(config)
        tcl = SafeTclClient(TclEndpoint(config.bind_address, config.tcl_port))
        state_before = tcl.wait_target_state()
        selected, results = find_matching_symbol_file(candidates, tcl.read_words)
        state_after = tcl.wait_target_state()
        if state_after != state_before:
            raise RuntimeError(
                "Read-only symbol matching changed target state unexpectedly: %s -> %s" %
                (state_before, state_after)
            )
        record = {
            "schema_version": 1,
            "command": "debug symbols",
            "status": "ok" if selected is not None else "no_unique_match",
            "requires_gdb": False,
            "initial_target_state": state_before,
            "final_target_state": state_after,
            "candidate_count": len(candidates),
            "selected": str(selected.path) if selected is not None else None,
            "matches": [
                {
                    "path": str(result.path),
                    "matched": result.matched,
                    "matched_samples": result.matched_samples,
                    "total_samples": result.total_samples,
                    "score": result.score,
                    "reason": result.reason,
                }
                for result in results
            ],
        }
        exact_count = sum(1 for result in results if result.matched)
        if selected is not None:
            text = "Matched ELF/AXF: %s" % selected.path
            code = 0
        elif exact_count > 1:
            text = "Multiple ELF/AXF files match exactly; choose one explicitly."
            code = 1
        else:
            text = "No ELF/AXF candidate matches the Application Flash samples."
            code = 1
        emit_snapshot(record, args.json, text)
        return code
    finally:
        service.stop()



def run_debug_selftest(args: argparse.Namespace, reporter: Reporter) -> int:
    """Exercise the Gateway + external Client path on one machine without SSH."""
    if args.telnet_port is not None:
        raise ValueError("debug selftest does not enable Telnet.")
    if not ipaddress.ip_address(args.bind_address).is_loopback:
        raise ValueError("debug selftest is loopback-only.")
    if args.symbols is None:
        raise ValueError("debug selftest requires --symbols ELF_OR_AXF.")
    if not 1 <= args.frames <= 64:
        raise ValueError("--frames must be in range 1..64.")
    if not 0.1 <= args.timeout <= 60.0:
        raise ValueError("--timeout must be in range 0.1..60 seconds.")
    tcl_port = args.tcl_port if args.tcl_port is not None else 6666
    symbols = args.symbols.expanduser().resolve()
    if symbols.suffix.lower() not in (".elf", ".axf") or not symbols.is_file():
        raise ValueError("debug selftest requires an existing ELF/AXF symbol file.")
    if args.dry_run:
        config = DebugConfig(ProbeRef(args.probe_serial), "127.0.0.1", args.gdb_port, None, tcl_port)
        config.validate()
        reporter.emit(
            "debug_selftest_plan",
            mode="selftest",
            symbols=str(symbols),
            gdb_endpoint="127.0.0.1:%d" % args.gdb_port,
            tcl_endpoint="127.0.0.1:%d" % tcl_port,
            expression=args.expression,
            location=args.location,
            preserve_target_state=True,
            ssh_exercised=False,
            two_machine_exercised=False,
            dry_run=True,
        )
        return 0

    probe = _select_read_probe(args, "debug selftest")
    if probe is None:
        return 1
    report = run_loopback_debug_selftest(
        probe=probe,
        symbol_file=symbols,
        openocd=args.openocd,
        gdb_port=args.gdb_port,
        tcl_port=tcl_port,
        frames=args.frames,
        expression=args.expression,
        location=args.location,
        timeout_seconds=args.timeout,
    )
    record = {
        "schema_version": 1,
        "command": "debug selftest",
        "status": "ok" if report.passed else "failed",
        "conclusion": report.conclusion,
        "passed": report.passed,
        "initial_target_state": report.initial_target_state,
        "final_target_state": report.final_target_state,
        "symbols": report.symbols,
        "gdb_endpoint": report.gdb_endpoint,
        "tcl_endpoint": report.tcl_endpoint,
        "ssh_exercised": report.ssh_exercised,
        "two_machine_exercised": report.two_machine_exercised,
        "field_acceptance_pending": True,
        "checks": [
            {"name": item.name, "status": item.status, "code": item.code, "message": item.message}
            for item in report.checks
        ],
    }
    text = ["B300 DEBUG SELFTEST %s" % report.conclusion]
    text.extend("[%s] %s: %s" % (item.status, item.name, item.message) for item in report.checks)
    text.append("SSH/two-machine transport was not exercised; field acceptance remains pending.")
    emit_snapshot(record, args.json, "\n".join(text))
    return 0 if report.passed else 1

def _sampling_expressions(args) -> tuple:
    expressions = []
    if getattr(args, "expression", None):
        expressions.append(args.expression)
    expressions.extend(getattr(args, "sample_expression", ()) or ())
    return validate_sampling_request(
        expressions, getattr(args, "samples", 20), getattr(args, "sample_interval", 0.5)
    )


def _validate_sample_output(path) -> None:
    if path is not None and Path(path).suffix.lower() not in {".csv", ".jsonl"}:
        raise ValueError("--sample-output must use .csv or .jsonl.")


def _execute_debug_operation(session: DebugSession, mode: str, args, base: dict) -> str:
    """Execute one bounded read/debug action on an already-connected session."""
    if mode == "poll":
        base["target_state"] = session.target_poll()
        text = base["target_state"]
    elif mode == "read-words":
        values = session.read_words(args.address, args.count)
        base.update({
            "address": "0x%08X" % args.address,
            "count": args.count,
            "words": ["0x%08X" % value for value in values],
        })
        text = "%s: %s" % (base["address"], " ".join(base["words"]))
    elif mode == "where":
        frame = session.capture_where()
        base["frame"] = _frame_record(frame)
        text = "%s %s:%s" % (
            base["frame"]["function"] or "?",
            base["frame"]["file"] or "?",
            base["frame"]["line"] if base["frame"]["line"] is not None else "?",
        )
    elif mode == "stack":
        frames = session.capture_stack(args.frames)
        base["frames"] = [_frame_record(frame) for frame in frames]
        text = "\n".join(
            "#%d %s %s:%s" % (
                item["level"], item["function"] or "?", item["file"] or "?",
                item["line"] if item["line"] is not None else "?",
            ) for item in base["frames"]
        ) or "No stack frames reported."
    elif mode == "registers":
        registers = session.capture_registers()
        base["registers"] = [_register_record(item) for item in registers]
        text = "\n".join("%s=%s" % (item["name"], item["value"]) for item in base["registers"])
    elif mode == "variable":
        value = session.capture_variable(args.expression)
        base["variable"] = {"expression": value.expression, "value": value.value}
        text = "%s=%s" % (value.expression, value.value)
    elif mode == "sample":
        expressions = _sampling_expressions(args)
        samples = sample_variables(
            session.capture_variables, expressions, args.samples, args.sample_interval
        )
        output = None
        if args.sample_output is not None:
            output = str(write_samples(args.sample_output, samples))
        base["sampling"] = {
            "expressions": list(expressions),
            "sample_cycles": args.samples,
            "interval_seconds": args.sample_interval,
            "output": output,
            "samples": [sample.to_record() for sample in samples],
        }
        text = "\n".join(
            "%.3fs %s=%s" % (sample.elapsed_seconds, sample.expression, sample.raw_value)
            for sample in samples
        )
    elif mode == "break":
        hit = session.break_once(args.location, args.timeout)
        base["hit"] = {
            "kind": hit.kind, "number": hit.number, "location": hit.location,
            "reason": hit.reason, "frame": _frame_record(hit.frame),
        }
        frame = base["hit"]["frame"]
        text = "breakpoint #%d hit: %s %s:%s" % (
            hit.number, frame["function"] or "?", frame["file"] or "?",
            frame["line"] if frame["line"] is not None else "?",
        )
    elif mode == "watch":
        hit = session.watch_once(args.expression, args.timeout)
        base["hit"] = {
            "kind": hit.kind, "number": hit.number, "location": hit.location,
            "reason": hit.reason, "frame": _frame_record(hit.frame),
        }
        if hit.value is None:
            raise RuntimeError("Watchpoint hit did not capture the watched value.")
        base["variable"] = {
            "expression": hit.value.expression, "value": hit.value.value,
        }
        frame = base["hit"]["frame"]
        text = "watchpoint #%d hit: %s=%s at %s %s:%s" % (
            hit.number, hit.value.expression, hit.value.value, frame["function"] or "?",
            frame["file"] or "?", frame["line"] if frame["line"] is not None else "?",
        )
    elif mode == "inspect":
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
        raise ValueError("Unsupported integrated debug mode: %s" % mode)


def run_integrated_debug(args: argparse.Namespace, reporter: Reporter) -> int:
    if args.telnet_port is not None:
        raise ValueError("Integrated debug diagnostics do not enable Telnet.")
    if not 1 <= args.frames <= 64:
        raise ValueError("--frames must be in range 1..64.")
    if args.debug_mode in {"variable", "watch"} and not args.expression:
        raise ValueError("debug %s requires --expression NAME." % args.debug_mode)
    if args.debug_mode == "sample":
        _sampling_expressions(args)
        _validate_sample_output(args.sample_output)
        if args.symbols is None:
            raise ValueError("debug sample requires --symbols ELF_OR_AXF.")
    if args.debug_mode == "break" and not args.location:
        raise ValueError("debug break requires --location FUNCTION_OR_FILE_LINE.")
    if args.debug_mode in {"break", "watch"} and args.symbols is None:
        raise ValueError("debug %s requires --symbols ELF_OR_AXF." % args.debug_mode)
    if not 0.1 <= args.timeout <= 60.0:
        raise ValueError("--timeout must be in range 0.1..60 seconds.")
    if args.debug_mode == "read-words" and args.address is None:
        raise ValueError("debug read-words requires --address ADDRESS.")

    tcl_port = args.tcl_port if args.tcl_port is not None else 6666
    symbols = args.symbols.expanduser().resolve() if args.symbols is not None else None
    probe = ProbeRef(args.probe_serial)
    if not args.dry_run:
        selected_probe = _select_read_probe(args, "debug %s" % args.debug_mode)
        if selected_probe is None:
            return 1
        probe = selected_probe
    config = DebugSessionConfig(
        probe, symbols, args.bind_address, args.gdb_port, tcl_port
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
            preserve_target_state=True, timeout=args.timeout, location=args.location,
            expression=args.expression,
            sample_expressions=list(_sampling_expressions(args)) if args.debug_mode == "sample" else None,
            sample_cycles=args.samples if args.debug_mode == "sample" else None,
            sample_interval=args.sample_interval if args.debug_mode == "sample" else None,
            sample_output=str(args.sample_output) if args.sample_output is not None else None,
            dry_run=True,
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
        text = _execute_debug_operation(session, args.debug_mode, args, base)
        emit_snapshot(base, args.json, text)
        return 0
    finally:
        session.stop()


def run_debug_client(args, reporter: Reporter) -> int:
    """Run one bounded debug action through the canonical SSH Gateway tunnel."""
    action = args.client_action
    if args.telnet_port is not None:
        raise ValueError("debug client does not enable Telnet.")
    if not args.ssh_host or not args.ssh_user:
        raise ValueError("debug client requires --ssh-host HOST and --ssh-user USER.")
    if not 1 <= args.frames <= 64:
        raise ValueError("--frames must be in range 1..64.")
    if action in {"variable", "watch"} and not args.expression:
        raise ValueError("debug client %s requires --expression NAME." % action)
    if action == "sample":
        _sampling_expressions(args)
        _validate_sample_output(args.sample_output)
        if args.symbols is None and not args.symbol_root:
            raise ValueError("debug client sample requires --symbols or --symbol-root.")
    if action == "break" and not args.location:
        raise ValueError("debug client break requires --location FUNCTION_OR_FILE_LINE.")
    if action in {"break", "watch"} and args.symbols is None:
        raise ValueError("debug client %s requires --symbols ELF_OR_AXF." % action)
    if action == "read-words" and args.address is None:
        raise ValueError("debug client read-words requires --address ADDRESS.")
    if not 0.1 <= args.timeout <= 60.0:
        raise ValueError("--timeout must be in range 0.1..60 seconds.")

    symbols = args.symbols.expanduser().resolve() if args.symbols is not None else None
    if symbols is not None and (symbols.suffix.lower() not in {".elf", ".axf"} or not symbols.is_file()):
        raise ValueError("debug client --symbols must reference an existing ELF/AXF file.")

    local_gdb = find_available_loopback_port(args.local_gdb_port)
    local_tcl = find_available_loopback_port(args.local_tcl_port, avoid=(local_gdb,))
    tunnel_config = SshDebugTunnelConfig(
        host=args.ssh_host, user=args.ssh_user, ssh_port=args.ssh_port,
        local_gdb_port=local_gdb, local_tcl_port=local_tcl,
        gateway_gdb_port=3333, gateway_tcl_port=6666,
    )
    tunnel_config.validate()

    if args.dry_run:
        reporter.emit(
            "debug_client_plan", role="client", action=action,
            gateway="%s@%s:%d" % (args.ssh_user, args.ssh_host, args.ssh_port),
            ssh_command=tunnel_config.argv("ssh"),
            gdb_endpoint="127.0.0.1:%d" % local_gdb,
            tcl_endpoint="127.0.0.1:%d" % local_tcl,
            symbols=str(symbols) if symbols else None,
            preserve_target_state=True,
            gateway_ports={"gdb": 3333, "tcl": 6666},
            sample_expressions=list(_sampling_expressions(args)) if action == "sample" else None,
            sample_cycles=args.samples if action == "sample" else None,
            sample_interval=args.sample_interval if action == "sample" else None,
            sample_output=str(args.sample_output) if args.sample_output is not None else None,
            remote_transport="ssh-local-forwarding", dry_run=True,
        )
        return 0

    tunnel = SshDebugTunnel(tunnel_config)
    session = DebugSession(service=DebugService(executable=args.openocd))
    try:
        tunnel_version = tunnel.start()
        tcl = SafeTclClient(TclEndpoint("127.0.0.1", local_tcl))
        selected_symbols = symbols
        if selected_symbols is not None:
            selected, results = find_matching_symbol_file((selected_symbols,), tcl.read_words)
            if selected is None:
                detail = results[0].reason if results else "ELF/AXF could not be sampled"
                raise RuntimeError("Client AXF/ELF does not match Gateway firmware: %s" % detail)
            selected_symbols = selected.path
        elif args.symbol_root:
            candidates = discover_symbol_files(
                [path.expanduser().resolve() for path in args.symbol_root],
                max_files=args.symbol_max_files, max_depth=8,
            )
            selected, results = find_matching_symbol_file(candidates, tcl.read_words)
            if selected is None:
                exact_count = sum(1 for item in results if item.matched)
                if exact_count > 1:
                    raise RuntimeError("Multiple AXF/ELF files match remote firmware; select --symbols explicitly.")
                raise RuntimeError("No AXF/ELF under --symbol-root matches remote firmware.")
            selected_symbols = selected.path

        info = session.start_external(
            symbol_file=selected_symbols,
            gdb_host="127.0.0.1", gdb_port=local_gdb,
            tcl_host="127.0.0.1", tcl_port=local_tcl,
        )
        base = {
            "schema_version": 1,
            "command": "debug client",
            "role": "client",
            "action": action,
            "status": "ok",
            "gateway": "%s@%s:%d" % (args.ssh_user, args.ssh_host, args.ssh_port),
            "remote_transport": "ssh-local-forwarding",
            "gdb_endpoint": info.gdb_endpoint,
            "tcl_endpoint": info.tcl_endpoint,
            "tcl_version": info.tcl_version or tunnel_version,
            "initial_target_state": info.initial_target_state,
            "symbols": info.symbols,
        }
        text = _execute_debug_operation(session, action, args, base)
        emit_snapshot(base, args.json, text)
        return 0
    finally:
        try:
            session.stop()
        finally:
            tunnel.stop()


def run_debug_gateway(args: argparse.Namespace, reporter: Reporter) -> int:
    """Run the headless debug-gateway role with fixed loopback safety defaults."""
    if not ipaddress.ip_address(args.bind_address).is_loopback:
        raise ValueError("debug gateway is loopback-only; remote clients must use SSH forwarding.")
    if args.telnet_port is not None:
        raise ValueError("debug gateway does not enable Telnet.")
    args.bind_address = "127.0.0.1"
    if args.tcl_port is None:
        args.tcl_port = 6666
    DebugConfig(
        ProbeRef(args.probe_serial), args.bind_address, args.gdb_port, None, args.tcl_port,
    ).validate()
    reporter.emit(
        "debug_role", role="gateway", gdb_endpoint="127.0.0.1:%d" % args.gdb_port,
        tcl_endpoint="127.0.0.1:%d" % args.tcl_port,
        remote_transport="ssh-local-forwarding", requires_local_gdb=False,
    )
    return run_debug(args, reporter)


def run_debug(args: argparse.Namespace, reporter: Reporter) -> int:
    if not ipaddress.ip_address(args.bind_address).is_loopback:
        reporter.emit(
            "warning",
            reason_code="REMOTE_GDB_INSECURE",
            message="GDB Remote Protocol is unauthenticated and unencrypted on a non-loopback bind.",
            next_action="Prefer loopback access through an SSH tunnel.",
        )
    if args.dry_run:
        command = openocd_command(args)
        reporter.emit("openocd", command=command, dry_run=True)
        return 0
    selected_probe = _select_read_probe(args, "debug %s" % args.debug_mode)
    if selected_probe is None:
        return 1
    probe = selected_probe
    config = DebugConfig(
        probe, args.bind_address, args.gdb_port,
        args.telnet_port, args.tcl_port,
    )
    config.validate()
    command = build_debug_command(
        probe, resolve_openocd(args.openocd), args.bind_address,
        args.gdb_port, args.telnet_port, args.tcl_port,
    )
    reporter.emit("openocd", command=command, dry_run=False)
    service = DebugService(executable=args.openocd)
    guard = None

    def openocd_event(line: str) -> None:
        reporter.emit("openocd_output", line=line)
        if guard is not None:
            try:
                guard.handle_openocd_line(line)
            except Exception as error:
                reporter.emit(
                    "remote_guard", guard_event="error",
                    message="Remote run-state guard failed: %s" % error,
                )

    try:
        service.start(config, event_sink=openocd_event)
        if args.tcl_port is not None and ipaddress.ip_address(args.bind_address).is_loopback:
            tcl = SafeTclClient(TclEndpoint(args.bind_address, args.tcl_port))
            guard = RemoteDebugGuard(
                tcl, event_sink=lambda event, message: reporter.emit(
                    "remote_guard", guard_event=event, message=message,
                ),
            )
            initial = guard.capture_initial_state()
            reporter.emit(
                "remote_guard", guard_event="armed", initial_target_state=initial,
                policy="restore-running-on-gdb-disconnect-and-server-shutdown",
            )
        reporter.emit("debug_state", state=DebugState.READY.value)
        while service.state in (DebugState.READY, DebugState.CONNECTED):
            time.sleep(0.2)
        reporter.emit("debug_state", state=service.state.value)
        return 0 if service.state == DebugState.STOPPED else 1
    except KeyboardInterrupt:
        reporter.emit("debug_state", state="STOPPING")
        return 0
    finally:
        if guard is not None:
            try:
                snapshot = guard.restore_initial_state(reason="server_shutdown")
                reporter.emit(
                    "remote_guard", guard_event="shutdown_restore",
                    initial_target_state=snapshot.initial_target_state,
                    final_target_state=snapshot.final_target_state,
                    restored=snapshot.restored,
                )
            except Exception as error:
                reporter.emit(
                    "remote_guard", guard_event="shutdown_restore_failed", message=str(error),
                )
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

        if args.command == "gateway":
            report = inspect_gateway_readiness(
                openocd=args.openocd, probe_serial=args.probe_serial,
                ssh_port=args.ssh_port, gdb_port=args.gdb_port, tcl_port=args.tcl_port,
            )
            checks = [
                {
                    "name": check.name, "status": check.status, "code": check.code,
                    "message": check.message, "next_action": check.next_action,
                }
                for check in report.checks
            ]
            probe = None
            if report.probe is not None:
                probe = {
                    "name": report.probe.name,
                    "serial": report.probe.serial,
                    "serial_available": report.probe.serial_available,
                    "source": report.probe.source,
                    "usb_identity": report.probe.usb_identity,
                }
            record = {
                "schema_version": 1,
                "command": "gateway doctor",
                "status": "ok" if report.ready else "blocked",
                "conclusion": report.conclusion,
                "ready": report.ready,
                "ipv4_addresses": list(report.ipv4_addresses),
                "ssh_port": report.ssh_port,
                "gdb_endpoint": "127.0.0.1:%d" % report.gdb_port,
                "tcl_endpoint": "127.0.0.1:%d" % report.tcl_port,
                "openocd": report.openocd,
                "probe": probe,
                "checks": checks,
                "start_command": "b300-stlink debug",
                "remote_transport": "ssh-local-forwarding",
            }
            text_lines = ["B300 DEBUG GATEWAY %s" % report.conclusion]
            text_lines.extend(
                "[%s] %s: %s" % (check.status, check.name, check.message)
                for check in report.checks
            )
            if report.ipv4_addresses:
                text_lines.append("Client SSH IP candidate(s): %s" % ", ".join(report.ipv4_addresses))
            text_lines.append("Start Gateway: b300-stlink debug")
            text_lines.append("GDB/TCL remain loopback-only; Client connects through SSH forwarding.")
            emit_snapshot(record, args.json, "\n".join(text_lines))
            return 0 if report.ready else 1

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
            if args.debug_mode == "gateway":
                return run_debug_gateway(args, reporter)
            if args.debug_mode == "client":
                return run_debug_client(args, reporter)
            if args.debug_mode == "server":
                validate_debug_args(args)
                return run_debug(args, reporter)
            if args.debug_mode == "vscode":
                return run_vscode_profile(args)
            if args.debug_mode == "symbols":
                return run_symbol_match(args, reporter)
            if args.debug_mode == "selftest":
                return run_debug_selftest(args, reporter)
            return run_integrated_debug(args, reporter)

        if args.command == "provision-bootloader":
            if not args.dry_run and not args.confirm_factory_provision:
                raise ProvisioningError(
                    "authorization",
                    "Factory Bootloader provisioning requires --confirm-factory-provision.",
                    "Run --dry-run first, then repeat with explicit factory confirmation for the intended board.",
                )
            service = B300Service(executable=args.openocd)
            try:
                trusted = (service.trusted_bootloader(args.bootloader_profile)
                           if args.bootloader_profile else service.trusted_bootloader())
            except ValueError as error:
                raise ProvisioningError(
                    "bootloader_profile", str(error),
                    "Use only a Bootloader profile shipped by this B300 ST-Link Tools release.",
                ) from error
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
                profile_id=trusted.profile.profile_id,
                profile_name=trusted.profile.display_name,
                firmware_version=trusted.firmware_version,
                board_token=trusted.board_token,
                ota_logical_port=trusted.profile.logical_port,
                ota_peripheral=trusted.profile.peripheral,
                ota_baudrate=trusted.profile.baudrate,
                ota_tx_pin=trusted.profile.tx_pin,
                ota_rx_pin=trusted.profile.rx_pin,
                ota_direction_pin=trusted.profile.direction_pin,
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
        reporter.emit(
            "metadata_plan",
            address="0x0800C000",
            size=44,
            magic="STLM",
            state="VERIFIED",
            image_size=image.flash_span_size,
            image_crc32=("0x%08X" % image.flash_crc32
                         if image.flash_crc32 is not None else None),
            condition="after_application_verified",
            dry_run=args.dry_run,
        )
        with tempfile.TemporaryDirectory(prefix="b300-stlink-preview-") as preview_dir:
            preview_metadata = Path(preview_dir) / "stlm-verified.bin"
            preview_readback = Path(preview_dir) / "stlm-readback.bin"
            preview_metadata.write_bytes(build_stlink_metadata(image, sequence=1))
            transactions = (
                ("program_verify", service.flash_command(plan), "always"),
                ("metadata_write_verify",
                 build_metadata_write_command(
                     plan.probe, preview_metadata, preview_readback, resolve_openocd(args.openocd)
                 ),
                 "after_application_verified"),
                ("reset", service.reset_command(plan.probe), "after_exact_stlm_verified_readback"),
            )
            for phase, command, condition in transactions:
                reporter.emit(
                    "openocd",
                    phase=phase,
                    command=command,
                    dry_run=args.dry_run,
                    condition=condition,
                )
        reporter.emit(
            "confirmation_plan",
            condition="after_reset",
            required_magic="STLM",
            required_state="CONFIRMED",
            image_size=image.flash_span_size,
            image_crc32=("0x%08X" % image.flash_crc32
                         if image.flash_crc32 is not None else None),
            sequence_policy="written_sequence_plus_1_mod_2^32",
            timeout_seconds=5.0,
            final_gate="application_pc_and_bkp1r_zero",
            dry_run=args.dry_run,
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
