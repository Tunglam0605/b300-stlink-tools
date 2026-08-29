"""Integrated local debug orchestration across OpenOCD, TCL and GDB/MI."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from .debug_service import DebugConfig, DebugService, DebugState
from .gdb_mi import GdbMiBackend
from .models import ProbeRef
from .tcl_client import SafeTclClient, TclEndpoint


EventSink = Callable[[str], None]


@dataclass(frozen=True)
class DebugSessionConfig:
    probe: ProbeRef
    symbol_file: Optional[Path] = None
    bind_address: str = "127.0.0.1"
    gdb_port: int = 3333
    tcl_port: int = 6666

    def validate(self) -> None:
        address = ipaddress.ip_address(self.bind_address)
        if not address.is_loopback:
            raise ValueError("Integrated debug sessions are loopback-only; use an SSH/VPN tunnel for remote access.")
        DebugConfig(
            self.probe, self.bind_address, self.gdb_port, None, self.tcl_port
        ).validate()
        if self.symbol_file is not None:
            path = Path(self.symbol_file).expanduser().resolve()
            if path.suffix.lower() not in (".elf", ".axf"):
                raise ValueError("Debug symbols must be an ELF or AXF file.")
            if not path.is_file():
                raise ValueError("Debug symbol file does not exist: %s" % path)


@dataclass(frozen=True)
class DebugSessionInfo:
    state: str
    gdb_endpoint: str
    tcl_endpoint: str
    symbols: Optional[str]
    tcl_version: str
    initial_target_state: str


@dataclass(frozen=True)
class DebugSnapshot:
    target_state_before: str
    resumed: bool
    frame: object
    stack: Tuple[object, ...]
    registers: Tuple[object, ...]


@dataclass(frozen=True)
class DebugStopSnapshot:
    kind: str
    number: int
    location: str
    reason: str
    frame: object
    value: Optional[object] = None


class DebugSession:
    """Own one local debug session and clean every layer up on partial failure."""

    def __init__(self, *, service: Optional[DebugService] = None,
                 gdb: Optional[GdbMiBackend] = None,
                 tcl_factory=SafeTclClient) -> None:
        self.service = service or DebugService()
        self.gdb = gdb or GdbMiBackend()
        self._tcl_factory = tcl_factory
        self.tcl: Optional[SafeTclClient] = None
        self.config: Optional[DebugSessionConfig] = None
        self.initial_target_state: Optional[str] = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active and self.service.state == DebugState.CONNECTED and self.gdb.running

    def start(self, config: DebugSessionConfig,
              event_sink: Optional[EventSink] = None) -> DebugSessionInfo:
        if self._active:
            raise RuntimeError("Integrated debug session is already active.")
        config.validate()
        symbols = None
        try:
            self.service.start(
                DebugConfig(
                    config.probe, config.bind_address, config.gdb_port,
                    None, config.tcl_port,
                ),
                event_sink=event_sink,
            )
            self.tcl = self._tcl_factory(
                TclEndpoint(config.bind_address, config.tcl_port)
            )
            tcl_version = self.tcl.version()
            initial_target_state = self.tcl.wait_target_state()
            lowered_initial = initial_target_state.lower()
            if "running" not in lowered_initial and "halted" not in lowered_initial:
                raise RuntimeError(
                    "Unable to classify initial target run state from OpenOCD TCL poll: %s" %
                    initial_target_state
                )
            self.initial_target_state = initial_target_state
            self.gdb.start()
            if config.symbol_file is not None:
                symbol_path = Path(config.symbol_file).expanduser().resolve()
                self.gdb.load_symbols(symbol_path)
                symbols = str(symbol_path)
            self.gdb.connect(config.bind_address, config.gdb_port)
            self.service.mark_connected()
        except BaseException:
            try:
                self.gdb.stop()
            finally:
                self.service.stop()
                self.tcl = None
                self.config = None
                self.initial_target_state = None
                self._active = False
            raise
        self.config = config
        self._active = True
        return DebugSessionInfo(
            state="CONNECTED",
            gdb_endpoint="%s:%d" % (config.bind_address, config.gdb_port),
            tcl_endpoint="%s:%d" % (config.bind_address, config.tcl_port),
            symbols=symbols,
            tcl_version=tcl_version,
            initial_target_state=initial_target_state,
        )

    def stop(self) -> None:
        try:
            if self._active:
                self._restore_initial_run_state_best_effort()
            self.gdb.stop()
        finally:
            self.service.stop()
            self.tcl = None
            self.config = None
            self.initial_target_state = None
            self._active = False

    def where(self):
        self._require_active()
        return self.gdb.current_frame()

    def inspect(self, max_frames: int = 8) -> DebugSnapshot:
        def capture():
            return (
                self.gdb.current_frame(),
                self.gdb.stack_frames(max_frames),
                self.gdb.register_values(),
            )
        state, resumed, result = self._with_preserved_run_state(capture)
        frame, stack, registers = result
        return DebugSnapshot(state, resumed, frame, tuple(stack), tuple(registers))

    def capture_where(self):
        _state, _resumed, result = self._with_preserved_run_state(self.gdb.current_frame)
        return result

    def capture_registers(self):
        _state, _resumed, result = self._with_preserved_run_state(self.gdb.register_values)
        return result

    def capture_stack(self, max_frames: int = 16):
        _state, _resumed, result = self._with_preserved_run_state(
            lambda: self.gdb.stack_frames(max_frames)
        )
        return result

    def capture_variable(self, expression: str):
        _state, _resumed, result = self._with_preserved_run_state(
            lambda: self.gdb.evaluate_variable(expression)
        )
        return result

    def break_once(self, location: str, timeout_seconds: float = 5.0) -> DebugStopSnapshot:
        return self._stop_once(
            kind="hardware-breakpoint", location=location, timeout_seconds=timeout_seconds,
            create=lambda: self.gdb.insert_hardware_breakpoint(location),
            accepted_reasons={"breakpoint-hit"}, capture=None,
        )

    def watch_once(self, expression: str, timeout_seconds: float = 5.0) -> DebugStopSnapshot:
        return self._stop_once(
            kind="watchpoint", location=expression, timeout_seconds=timeout_seconds,
            create=lambda: self.gdb.insert_watchpoint(expression),
            accepted_reasons={
                "watchpoint-trigger", "read-watchpoint-trigger", "access-watchpoint-trigger",
            },
            capture=lambda: self.gdb.evaluate_variable(expression),
        )

    def stack(self, max_frames: int = 16):
        self._require_active()
        return self.gdb.stack_frames(max_frames)

    def registers(self):
        self._require_active()
        return self.gdb.register_values()

    def variable(self, expression: str):
        self._require_active()
        return self.gdb.evaluate_variable(expression)

    def hardware_breakpoint(self, location: str):
        self._require_active()
        return self.gdb.insert_hardware_breakpoint(location)

    def watchpoint(self, expression: str):
        self._require_active()
        return self.gdb.insert_watchpoint(expression)

    def delete_breakpoint(self, number: int):
        self._require_active()
        return self.gdb.delete_breakpoint(number)

    def halt(self):
        self._require_active()
        return self.gdb.interrupt()

    def continue_execution(self):
        self._require_active()
        return self.gdb.continue_execution()

    def step(self):
        self._require_active()
        return self.gdb.step()

    def next(self):
        self._require_active()
        return self.gdb.next()

    def reset_halt(self):
        self._require_active()
        return self.gdb.reset_halt()

    def target_poll(self) -> str:
        self._require_active()
        assert self.tcl is not None
        return self.tcl.wait_target_state()

    def read_words(self, address: int, count: int = 1):
        self._require_active()
        assert self.tcl is not None
        return self.tcl.read_words(address, count)

    def _stop_once(self, *, kind: str, location: str, timeout_seconds: float, create,
                   accepted_reasons, capture) -> DebugStopSnapshot:
        self._require_active()
        if not 0.1 <= timeout_seconds <= 60.0:
            raise ValueError("Debug stop timeout must be in range 0.1..60 seconds.")
        if "running" not in (self.initial_target_state or "").lower():
            raise RuntimeError("One-shot breakpoint/watchpoint requires a target that was initially running.")
        current = self.target_poll().lower()
        if "running" in current:
            self.gdb.interrupt_and_wait_stopped()
        elif "halted" not in current:
            raise RuntimeError("Unable to establish a halted target before creating debug resource.")

        resource = create()
        stopped = None
        try:
            stopped = self.gdb.continue_and_wait_stopped(timeout_seconds=timeout_seconds)
            reason_match = re.search(r'(?:^|,)reason="([^"]+)"', stopped.body)
            number_match = re.search(
                r'(?:^|,)bkptno="([0-9]+)"|(?:^|,)wpt=\{number="([0-9]+)"',
                stopped.body,
            )
            reason = reason_match.group(1) if reason_match else "unknown"
            if number_match:
                number_text = number_match.group(1) or number_match.group(2)
                stop_number = int(number_text)
            else:
                stop_number = None
            if reason not in accepted_reasons or stop_number != resource.number:
                raise RuntimeError(
                    "Target stopped for unexpected reason/resource: reason=%s number=%s expected=%d" %
                    (reason, stop_number, resource.number)
                )
            frame = self.gdb.current_frame()
            captured_value = capture() if capture is not None else None
            return DebugStopSnapshot(
                kind, resource.number, location, reason, frame, captured_value
            )
        finally:
            try:
                state = self.target_poll().lower()
                if "running" in state:
                    self.gdb.interrupt_and_wait_stopped()
                self.gdb.delete_breakpoint(resource.number)
            finally:
                try:
                    state = self.target_poll().lower()
                    if "halted" in state:
                        self.gdb.continue_execution()
                except BaseException:
                    pass

    def _with_preserved_run_state(self, operation):
        self._require_active()
        initial = self.initial_target_state or ""
        initial_lower = initial.lower()
        should_run_after = "running" in initial_lower
        if not should_run_after and "halted" not in initial_lower:
            raise RuntimeError("Initial target state is unavailable or ambiguous.")
        current = self.target_poll()
        current_lower = current.lower()
        if "running" in current_lower:
            self.gdb.interrupt_and_wait_stopped()
        elif "halted" not in current_lower:
            raise RuntimeError("Unable to classify target run state from OpenOCD TCL poll: %s" % current)
        try:
            result = operation()
        finally:
            if should_run_after:
                self.gdb.continue_execution()
        return initial, should_run_after, result

    def _restore_initial_run_state_best_effort(self) -> None:
        initial = (self.initial_target_state or "").lower()
        if "running" not in initial or self.tcl is None or not self.gdb.running:
            return
        try:
            current = self.tcl.target_state().lower()
            if "halted" in current:
                self.gdb.continue_execution()
        except BaseException:
            pass

    def _require_active(self) -> None:
        if not self.active:
            raise RuntimeError("Integrated debug session is not CONNECTED.")
