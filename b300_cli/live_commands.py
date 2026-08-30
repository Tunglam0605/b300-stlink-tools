"""CLI orchestration for the non-halting Realtime Live Monitor.

This module intentionally keeps presentation/output concerns out of the core session
facade. Runtime Local/Client monitoring is delegated to ``LiveMonitorSession`` so
GUI and CLI share the same safety/interlock/transport lifecycle.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Optional

from b300_cli.reporting import Reporter
from b300_core.live_monitor import validate_live_request, validate_live_watch_specs
from b300_core.live_service import LiveMonitorService
from b300_core.live_session import (
    ClientLiveMonitorConfig, LiveMonitorSession, LocalLiveMonitorConfig,
)
from b300_core.models import ProbeRef
from b300_core.ssh_debug_tunnel import find_available_loopback_port
from b300_core.ssh_live_tunnel import SshLiveTunnelConfig


def _validated_output_path(path: Path, force: bool) -> Path:
    output = path.expanduser().resolve()
    if not output.parent.is_dir() or output.is_dir():
        raise ValueError("Output path must name a file in an existing directory.")
    if output.exists() and not force:
        raise FileExistsError("Output file already exists; use --force to replace it.")
    return output


def validate_live_output(path) -> None:
    if path is not None and Path(path).suffix.lower() not in {".csv", ".jsonl"}:
        raise ValueError("--live-output must use .csv or .jsonl.")


def validate_live_options(args) -> None:
    """Validate all Live Monitor options before hardware/tunnel access."""
    validate_live_output(args.live_output)
    validate_live_watch_specs(args.live_watch)
    validate_live_request(args.live_interval, args.live_samples, ())


def _open_live_output(path, watch_specs, force=False):
    if path is None:
        return None, None
    target = _validated_output_path(Path(path), bool(force))
    handle = target.open("w", encoding="utf-8", newline="")
    if target.suffix.lower() == ".csv":
        fieldnames = [
            "cycle", "scheduled_elapsed_seconds", "captured_elapsed_seconds",
            "read_duration_seconds", "overrun", "pc", "function", "file", "line",
        ]
        for spec in watch_specs:
            name = str(spec).split(":", 1)[0].strip()
            fieldnames.extend((name, name + "__coherent", name + "__raw_hex"))
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        return handle, writer
    return handle, None


def _run_session_stream(session: LiveMonitorSession, args, reporter: Reporter, *, role: str) -> int:
    output_handle = None
    csv_writer = None
    emitted = []
    try:
        output_handle, csv_writer = _open_live_output(
            args.live_output, args.live_watch, args.force
        )

        def on_sample(sample) -> None:
            record = sample.to_record()
            emitted.append(record)
            reporter.emit("live_sample", role=role, **record)
            if output_handle is None:
                return
            if csv_writer is None:
                output_handle.write(json.dumps(record, sort_keys=True) + "\n")
                output_handle.flush()
                return
            row = {key: record.get(key) for key in csv_writer.fieldnames}
            for item in record.get("values", ()):
                name = item["name"]
                row[name] = item["value"]
                row[name + "__coherent"] = item["coherent"]
                row[name + "__raw_hex"] = item["raw_hex"]
            csv_writer.writerow(row)
            output_handle.flush()

        try:
            summary = session.run(on_sample=on_sample)
        except KeyboardInterrupt:
            session.cancel()
            final_state = session.target_state()
            reporter.emit(
                "live_summary", role=role, samples=len(emitted),
                interval_seconds=args.live_interval, cancelled=True,
                final_target_state=final_state,
                overruns=sum(bool(item["overrun"]) for item in emitted),
            )
            if final_state != "running":
                raise RuntimeError(
                    "Live Monitor Ctrl+C ended with target state %s." % final_state
                )
            return 0

        reporter.emit("live_summary", role=role, **summary.to_record())
        return 0
    finally:
        if output_handle is not None:
            output_handle.close()


def run_live_local(args, reporter: Reporter, *, select_probe: Callable) -> int:
    """Run Local Live Monitor through the canonical session facade."""
    if args.telnet_port is not None:
        raise ValueError("debug live does not enable Telnet.")
    if args.symbols is None:
        raise ValueError("debug live requires --symbols ELF_OR_AXF.")
    symbols = args.symbols.expanduser().resolve()
    if symbols.suffix.lower() not in {".elf", ".axf"} or not symbols.is_file():
        raise ValueError("debug live --symbols must reference an existing ELF/AXF file.")
    validate_live_options(args)
    tcl_port = args.tcl_port if args.tcl_port is not None else 6666

    # Validate the full request before probe discovery/hardware access.
    planning = LocalLiveMonitorConfig(
        probe=ProbeRef(args.probe_serial), symbols=symbols,
        interval_seconds=args.live_interval, sample_limit=args.live_samples,
        watch_specs=tuple(args.live_watch), tcl_port=tcl_port,
    )
    planning.validate()

    if args.dry_run:
        service = LiveMonitorService(executable=args.openocd)
        command = service.command(planning.probe, planning.tcl_port)
        reporter.emit(
            "debug_live_plan", role="local", command=command, symbols=str(symbols),
            tcl_endpoint="127.0.0.1:%d" % tcl_port, gdb_connected=False,
            zero_halt=True, dwt_pcsr="0xE000101C", interval_seconds=args.live_interval,
            sample_limit=args.live_samples, watches=list(args.live_watch),
            output=str(args.live_output) if args.live_output is not None else None, dry_run=True,
        )
        return 0

    selected_probe = select_probe(args, "debug live")
    if selected_probe is None:
        return 1
    config = LocalLiveMonitorConfig(
        probe=selected_probe, symbols=symbols,
        interval_seconds=args.live_interval, sample_limit=args.live_samples,
        watch_specs=tuple(args.live_watch), tcl_port=tcl_port,
    )
    session = LiveMonitorSession(openocd_executable=args.openocd)
    try:
        session.start_local(config)
        return _run_session_stream(session, args, reporter, role="local")
    finally:
        session.close()


def run_live_client(args, reporter: Reporter, symbols: Optional[Path]) -> int:
    """Run SSH Client Live Monitor through the canonical session facade."""
    validate_live_options(args)
    symbol_roots = tuple(path.expanduser().resolve() for path in (args.symbol_root or ()))
    config = ClientLiveMonitorConfig(
        host=args.ssh_host, user=args.ssh_user, symbols=symbols,
        interval_seconds=args.live_interval, sample_limit=args.live_samples,
        watch_specs=tuple(args.live_watch), ssh_port=args.ssh_port,
        preferred_local_tcl_port=args.local_tcl_port, gateway_tcl_port=6666,
        symbol_roots=symbol_roots, symbol_max_files=args.symbol_max_files,
    )
    config.validate()

    if args.dry_run:
        local_tcl = find_available_loopback_port(args.local_tcl_port)
        tunnel_config = SshLiveTunnelConfig(
            host=args.ssh_host, user=args.ssh_user, ssh_port=args.ssh_port,
            local_tcl_port=local_tcl, gateway_tcl_port=6666,
        )
        tunnel_config.validate()
        reporter.emit(
            "debug_client_plan", role="client", action="live",
            gateway="%s@%s:%d" % (args.ssh_user, args.ssh_host, args.ssh_port),
            ssh_command=tunnel_config.argv("ssh"), gdb_endpoint=None,
            tcl_endpoint="127.0.0.1:%d" % local_tcl,
            symbols=str(symbols) if symbols else None, preserve_target_state=True,
            gateway_ports={"tcl": 6666}, zero_halt=True, gdb_connected=False,
            live_interval=args.live_interval, live_samples=args.live_samples,
            live_watches=list(args.live_watch),
            live_output=str(args.live_output) if args.live_output is not None else None,
            remote_transport="ssh-tcl-local-forwarding", dry_run=True,
        )
        return 0

    session = LiveMonitorSession(openocd_executable=args.openocd)
    try:
        session.start_client(config)
        return _run_session_stream(session, args, reporter, role="client")
    finally:
        session.close()
