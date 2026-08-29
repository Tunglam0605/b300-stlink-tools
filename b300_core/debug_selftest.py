"""Single-machine acceptance for the remote-debug data path.

This intentionally does not exercise SSH or a second host. It starts the
Gateway OpenOCD endpoints on loopback, then attaches through DebugSession's
external-client path so the same GDB/TCL path used after SSH forwarding is
validated without weakening the remote transport policy.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from .debug_service import DebugConfig, DebugService
from .debug_session import DebugSession
from .elf_matcher import match_symbol_file
from .models import ProbeRef
from .tcl_client import SafeTclClient, TclEndpoint


@dataclass(frozen=True)
class DebugSelfTestCheck:
    name: str
    status: str
    code: str
    message: str


@dataclass(frozen=True)
class DebugSelfTestReport:
    checks: Tuple[DebugSelfTestCheck, ...]
    conclusion: str
    passed: bool
    initial_target_state: Optional[str]
    final_target_state: Optional[str]
    symbols: str
    gdb_endpoint: str
    tcl_endpoint: str
    ssh_exercised: bool = False
    two_machine_exercised: bool = False


def _port_available(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def run_loopback_debug_selftest(
        *, probe: ProbeRef, symbol_file: Path, openocd: Optional[str] = None,
        gdb_port: int = 3333, tcl_port: int = 6666, frames: int = 4,
        expression: Optional[str] = None, location: Optional[str] = None,
        timeout_seconds: float = 5.0, service: Optional[DebugService] = None,
        session: Optional[DebugSession] = None, tcl_factory=SafeTclClient,
        port_probe: Callable[[str, int], bool] = _port_available,
) -> DebugSelfTestReport:
    """Exercise Gateway + external Client locally while preserving target state."""
    if gdb_port == tcl_port:
        raise ValueError("Self-test GDB and TCL ports must be distinct.")
    for label, port in (("GDB", gdb_port), ("TCL", tcl_port)):
        if not 1 <= int(port) <= 65535:
            raise ValueError("%s port must be in range 1..65535." % label)
    if not 1 <= int(frames) <= 64:
        raise ValueError("Self-test frames must be in range 1..64.")
    if not 0.1 <= float(timeout_seconds) <= 60.0:
        raise ValueError("Self-test timeout must be in range 0.1..60 seconds.")

    symbols = Path(symbol_file).expanduser().resolve()
    if symbols.suffix.lower() not in (".elf", ".axf") or not symbols.is_file():
        raise ValueError("Self-test requires an existing ELF/AXF symbol file.")

    gateway = service or DebugService(executable=openocd)
    client = session or DebugSession(service=gateway)
    checks = []
    tcl = None
    initial_state = None
    final_state = None
    gateway_started = False

    def passed(name: str, code: str, message: str) -> None:
        checks.append(DebugSelfTestCheck(name, "PASS", code, message))

    def limited(name: str, code: str, message: str) -> None:
        checks.append(DebugSelfTestCheck(name, "LIMITED", code, message))

    def failed(name: str, code: str, message: str) -> None:
        checks.append(DebugSelfTestCheck(name, "FAIL", code, message))

    try:
        gateway.start(DebugConfig(probe, "127.0.0.1", int(gdb_port), None, int(tcl_port)))
        gateway_started = True
        passed("gateway", "GATEWAY_READY", "Loopback Gateway OpenOCD endpoints are ready.")

        tcl = tcl_factory(TclEndpoint("127.0.0.1", int(tcl_port)))
        initial_state = tcl.wait_target_state()
        if initial_state not in {"running", "halted"}:
            raise RuntimeError("Unable to classify initial target state: %s" % initial_state)
        passed("initial_state", "TARGET_STATE_CAPTURED", "Initial target state is %s." % initial_state.upper())

        symbol_match = match_symbol_file(symbols, tcl.read_words)
        if not symbol_match.matched:
            raise RuntimeError(
                "ELF/AXF does not match Application Flash (%d/%d sample windows)." %
                (symbol_match.matched_samples, symbol_match.total_samples)
            )
        passed(
            "symbols_match", "SYMBOLS_MATCH_FLASH",
            "ELF/AXF matches Application Flash (%d/%d sample windows)." %
            (symbol_match.matched_samples, symbol_match.total_samples),
        )

        client.start_external(
            symbol_file=symbols,
            gdb_host="127.0.0.1", gdb_port=int(gdb_port),
            tcl_host="127.0.0.1", tcl_port=int(tcl_port),
        )
        passed("client_attach", "EXTERNAL_CLIENT_CONNECTED", "External Client path attached through loopback GDB/TCL.")

        post_attach = tcl.wait_target_state()
        if post_attach != initial_state:
            raise RuntimeError(
                "External attach changed target state from %s to %s." % (initial_state, post_attach)
            )
        passed("attach_state", "TARGET_STATE_PRESERVED", "External attach preserved target state %s." % initial_state.upper())

        snapshot = client.inspect(int(frames))
        frame = getattr(snapshot, "frame", None)
        function = getattr(frame, "function", None) or "?"
        passed("inspect", "SOURCE_INSPECT_OK", "Source/stack/register inspection succeeded at %s." % function)

        if expression:
            value = client.capture_variable(expression)
            passed(
                "variable", "VARIABLE_READ_OK",
                "Variable %s is readable (%s)." % (getattr(value, "expression", expression), getattr(value, "value", "?")),
            )

        if location:
            if initial_state != "running":
                limited("break_once", "BREAK_SKIPPED_HALTED", "Break Once is skipped because target was initially HALTED.")
            else:
                hit = client.break_once(location, timeout_seconds=float(timeout_seconds))
                passed("break_once", "BREAK_ONCE_OK", "Transient hardware breakpoint hit: %s." % getattr(hit, "reason", "hit"))

        if expression:
            if initial_state != "running":
                limited("watch_once", "WATCH_SKIPPED_HALTED", "Watch Once is skipped because target was initially HALTED.")
            else:
                hit = client.watch_once(expression, timeout_seconds=float(timeout_seconds))
                passed("watch_once", "WATCH_ONCE_OK", "Transient watchpoint hit: %s." % getattr(hit, "reason", "hit"))
    except Exception as error:
        failed("execution", "SELFTEST_EXECUTION_FAILED", str(error))
    finally:
        try:
            client.stop()
        except Exception as error:
            failed("client_cleanup", "CLIENT_CLEANUP_FAILED", str(error))
        if gateway_started and tcl is not None:
            try:
                final_state = tcl.wait_target_state()
                if initial_state is not None and final_state == initial_state:
                    passed("final_state", "FINAL_STATE_RESTORED", "Target final state restored to %s." % final_state.upper())
                elif initial_state is not None:
                    failed(
                        "final_state", "FINAL_STATE_DRIFT",
                        "Target state changed from %s to %s." % (initial_state, final_state),
                    )
            except Exception as error:
                failed("final_state", "FINAL_STATE_UNVERIFIED", str(error))
        try:
            gateway.stop()
        except Exception as error:
            failed("gateway_cleanup", "GATEWAY_CLEANUP_FAILED", str(error))

    for name, port in (("gdb_port_cleanup", int(gdb_port)), ("tcl_port_cleanup", int(tcl_port))):
        if port_probe("127.0.0.1", port):
            passed(name, "PORT_RELEASED", "127.0.0.1:%d is released after self-test." % port)
        else:
            failed(name, "PORT_STILL_IN_USE", "127.0.0.1:%d is still in use after self-test." % port)

    has_fail = any(check.status == "FAIL" for check in checks)
    has_limited = any(check.status == "LIMITED" for check in checks)
    conclusion = "FAILED" if has_fail else ("PASS_WITH_LIMITS" if has_limited else "PASS")
    return DebugSelfTestReport(
        checks=tuple(checks), conclusion=conclusion, passed=not has_fail,
        initial_target_state=initial_state, final_target_state=final_state,
        symbols=str(symbols),
        gdb_endpoint="127.0.0.1:%d" % int(gdb_port),
        tcl_endpoint="127.0.0.1:%d" % int(tcl_port),
    )
