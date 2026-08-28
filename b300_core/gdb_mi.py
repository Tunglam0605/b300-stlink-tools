"""Bounded, token-correlated GDB/MI transport for the B300 debug surface."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from .gdb_runtime import resolve_gdb
from .process_startup import child_process_kwargs


class GdbProcess(Protocol):
    stdin: object
    stdout: object

    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[..., GdbProcess]


class GdbMiError(RuntimeError):
    """Base class for verified GDB/MI transport failures."""


class GdbMiCommandError(GdbMiError):
    """GDB returned a matching ``^error`` result record."""


class GdbMiTimeoutError(GdbMiError):
    """A matching result record was not received before the bounded timeout."""


class GdbMiProcessError(GdbMiError):
    """GDB exited or cannot provide the required MI stdin/stdout streams."""


@dataclass(frozen=True)
class MiRecord:
    token: Optional[int]
    prefix: str
    body: str
    raw: str


@dataclass(frozen=True)
class MiResult:
    token: int
    result_class: str
    payload: str
    raw: str

    @property
    def message(self) -> Optional[str]:
        match = re.search(r'(?:^|,)msg="((?:\\.|[^"\\])*)"', self.payload)
        if match is None:
            return None
        return bytes(match.group(1), "utf-8").decode("unicode_escape")


@dataclass(frozen=True)
class FrameInfo:
    level: int
    address: Optional[int]
    function: Optional[str]
    file: Optional[str]
    fullname: Optional[str]
    line: Optional[int]


@dataclass(frozen=True)
class RegisterValue:
    number: int
    name: str
    value: str


@dataclass(frozen=True)
class EvaluatedValue:
    expression: str
    value: str


@dataclass(frozen=True)
class BreakpointInfo:
    number: int
    kind: str
    location: str


_SAFE_EXPRESSION = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|->)[A-Za-z_][A-Za-z0-9_]*|\[[0-9]+\])*$"
)
_SAFE_BREAK_LOCATION = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_:]*|[A-Za-z0-9_.-]+:[1-9][0-9]*)$"
)
_MI_FIELD = re.compile(r'([A-Za-z0-9_-]+)="((?:\\.|[^"\\])*)"')


def _decode_mi_string(value: str) -> str:
    return bytes(value, "utf-8").decode("unicode_escape")


def _mi_fields(payload: str) -> Dict[str, str]:
    return {key: _decode_mi_string(value) for key, value in _MI_FIELD.findall(payload)}


def _parse_address(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _frame_from_payload(payload: str) -> FrameInfo:
    fields = _mi_fields(payload)
    try:
        level = int(fields.get("level", "0"))
    except ValueError:
        level = 0
    try:
        line = int(fields["line"]) if "line" in fields else None
    except ValueError:
        line = None
    return FrameInfo(
        level=level,
        address=_parse_address(fields.get("addr")),
        function=fields.get("func"),
        file=fields.get("file"),
        fullname=fields.get("fullname"),
        line=line,
    )


_MI_RECORD = re.compile(r"^(?:(?P<token>[0-9]+))?(?P<prefix>[\^*+=~@&])(?P<body>.*)$")


def parse_mi_record(raw: str) -> Optional[MiRecord]:
    """Parse only the record boundary needed for token-safe command handling."""
    text = str(raw).rstrip("\r\n")
    match = _MI_RECORD.match(text)
    if match is None:
        return None
    token_text = match.group("token")
    return MiRecord(
        int(token_text) if token_text is not None else None,
        match.group("prefix"), match.group("body"), text,
    )


class GdbMiBackend:
    """A minimal allow-list of GDB commands that succeed only on verified MI results."""

    def __init__(self, executable: Optional[str] = None,
                 process_factory: Optional[ProcessFactory] = None,
                 response_timeout_seconds: float = 5.0,
                 platform_name: Optional[str] = None) -> None:
        if response_timeout_seconds <= 0:
            raise ValueError("GDB/MI response timeout must be positive.")
        self.executable: Optional[str] = None
        self._configured_executable = executable
        self._process_factory = process_factory or subprocess.Popen
        self._platform_name = platform_name
        self.response_timeout_seconds = response_timeout_seconds
        self._process: Optional[GdbProcess] = None
        self._next_token = 1
        self._condition = threading.Condition(threading.RLock())
        self._command_lock = threading.Lock()
        self._results: Dict[int, MiResult] = {}
        self._async_records: List[MiRecord] = []
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_finished = True

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def reader_alive(self) -> bool:
        return self._reader_thread is not None and self._reader_thread.is_alive()

    @property
    def async_records(self) -> Tuple[MiRecord, ...]:
        with self._condition:
            return tuple(self._async_records)

    def start(self) -> None:
        if self.running:
            raise GdbMiProcessError("GDB is already running.")
        self.executable = resolve_gdb(self._configured_executable)
        process = self._process_factory(
            [self.executable, "--interpreter=mi2"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            shell=False,
            **child_process_kwargs(self._platform_name),
        )
        if getattr(process, "stdin", None) is None or getattr(process, "stdout", None) is None:
            try:
                process.terminate()
            finally:
                raise GdbMiProcessError("GDB MI stdin/stdout pipes are unavailable.")
        with self._condition:
            self._process = process
            self._next_token = 1
            self._results.clear()
            self._async_records.clear()
            self._reader_finished = False
            self._reader_thread = threading.Thread(
                target=self._read_stdout, args=(process,), name="b300-gdb-mi-reader", daemon=True,
            )
            self._reader_thread.start()

    def connect(self, host: str, port: int) -> MiResult:
        address = ipaddress.ip_address(host)
        if not 1 <= port <= 65535:
            raise ValueError("GDB port must be in range 1..65535.")
        return self._request(
            "-target-select remote %s:%d" % (address, port), ("connected", "done"),
        )

    def load_symbols(self, symbol_file: Path) -> MiResult:
        path = Path(symbol_file).expanduser().resolve()
        if path.suffix.lower() not in (".elf", ".axf"):
            raise ValueError("Symbols must be loaded from an ELF or AXF file.")
        if not path.is_file():
            raise ValueError("Symbol file does not exist: %s" % path)
        return self._request(
            '-file-exec-and-symbols "%s"' % self._quote(str(path)), ("done",),
        )

    def reset_halt(self) -> MiResult:
        return self._request('-interpreter-exec console "monitor reset halt"', ("done",))

    def continue_execution(self) -> MiResult:
        return self._request("-exec-continue", ("running", "done"))

    def interrupt(self) -> MiResult:
        return self._request("-exec-interrupt", ("done", "running"))

    def interrupt_and_wait_stopped(self, timeout_seconds: Optional[float] = None) -> MiRecord:
        with self._condition:
            start_index = len(self._async_records)
        self.interrupt()
        return self.wait_for_stopped(start_index=start_index, timeout_seconds=timeout_seconds)

    def wait_for_stopped(self, *, start_index: int = 0,
                         timeout_seconds: Optional[float] = None) -> MiRecord:
        timeout = self.response_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("GDB stop timeout must be positive.")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for record in self._async_records[max(0, start_index):]:
                    if record.prefix == "*" and record.body.startswith("stopped"):
                        return record
                process = self._process
                if process is None or process.poll() is not None:
                    code = None if process is None else process.poll()
                    raise GdbMiProcessError(
                        "GDB exited before target stop notification (exit code %s)." % code
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GdbMiTimeoutError("Timed out waiting for GDB *stopped notification.")
                self._condition.wait(timeout=min(remaining, 0.05))

    def disconnect(self) -> MiResult:
        return self._request("-target-disconnect", ("done",))

    def current_frame(self) -> FrameInfo:
        result = self._request("-stack-info-frame", ("done",))
        match = re.search(r"(?:^|,)frame=\{(.*)\}(?:,|$)", result.payload)
        if match is None:
            raise GdbMiCommandError("GDB did not return the current stack frame.")
        return _frame_from_payload(match.group(1))

    def stack_frames(self, max_frames: int = 16) -> Tuple[FrameInfo, ...]:
        if not 1 <= max_frames <= 64:
            raise ValueError("Stack frame limit must be in range 1..64.")
        result = self._request("-stack-list-frames 0 %d" % (max_frames - 1), ("done",))
        return tuple(
            _frame_from_payload(match.group(1))
            for match in re.finditer(r"frame=\{([^{}]*)\}", result.payload)
        )

    def register_values(self) -> Tuple[RegisterValue, ...]:
        names_result = self._request("-data-list-register-names", ("done",))
        values_result = self._request("-data-list-register-values x", ("done",))
        names_match = re.search(r"register-names=\[(.*)\]$", names_result.payload)
        if names_match is None:
            raise GdbMiCommandError("GDB did not return register names.")
        names = [
            _decode_mi_string(item)
            for item in re.findall(r'"((?:\\.|[^"\\])*)"', names_match.group(1))
        ]
        values = []
        for match in re.finditer(
                r'\{number="([0-9]+)",value="((?:\\.|[^"\\])*)"\}',
                values_result.payload):
            number = int(match.group(1))
            name = names[number] if number < len(names) and names[number] else "reg%d" % number
            values.append(RegisterValue(number, name, _decode_mi_string(match.group(2))))
        return tuple(values)

    def evaluate_variable(self, expression: str) -> EvaluatedValue:
        if not _SAFE_EXPRESSION.fullmatch(expression):
            raise ValueError("Variable expression contains unsupported characters or operations.")
        result = self._request(
            '-data-evaluate-expression "%s"' % self._quote(expression), ("done",)
        )
        fields = _mi_fields(result.payload)
        if "value" not in fields:
            raise GdbMiCommandError("GDB did not return a value for %s." % expression)
        return EvaluatedValue(expression, fields["value"])

    def insert_hardware_breakpoint(self, location: str) -> BreakpointInfo:
        if not _SAFE_BREAK_LOCATION.fullmatch(location):
            raise ValueError("Breakpoint location must be a function or basename:line.")
        result = self._request(
            '-break-insert -h "%s"' % self._quote(location), ("done",)
        )
        fields = _mi_fields(result.payload)
        try:
            number = int(fields["number"])
        except (KeyError, ValueError) as error:
            raise GdbMiCommandError("GDB did not return a hardware breakpoint number.") from error
        return BreakpointInfo(number, "hardware-breakpoint", location)

    def insert_watchpoint(self, expression: str) -> BreakpointInfo:
        if not _SAFE_EXPRESSION.fullmatch(expression):
            raise ValueError("Watch expression contains unsupported characters or operations.")
        result = self._request(
            '-break-watch "%s"' % self._quote(expression), ("done",)
        )
        match = re.search(r'(?:number|wpt)="([0-9]+)"', result.payload)
        if match is None:
            raise GdbMiCommandError("GDB did not return a watchpoint number.")
        return BreakpointInfo(int(match.group(1)), "watchpoint", expression)

    def delete_breakpoint(self, number: int) -> MiResult:
        if not 1 <= number <= 9999:
            raise ValueError("Breakpoint number must be in range 1..9999.")
        return self._request("-break-delete %d" % number, ("done",))

    def step(self) -> MiResult:
        return self._request("-exec-step", ("running", "done"))

    def next(self) -> MiResult:
        return self._request("-exec-next", ("running", "done"))

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._write_raw("%d-gdb-exit\n" % self._next_token)
                self._next_token += 1
            except GdbMiError:
                pass
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        reader = self._reader_thread
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=3.0)
        with self._condition:
            self._process = None
            self._reader_finished = True
            self._condition.notify_all()

    def _request(self, command: str, accepted_classes: Sequence[str]) -> MiResult:
        with self._command_lock:
            if not self.running:
                code = None if self._process is None else self._process.poll()
                raise GdbMiProcessError("GDB is not running (exit code %s)." % code)
            with self._condition:
                token = self._next_token
                self._next_token += 1
            self._write_raw("%d%s\n" % (token, command))
            result = self._wait_for_result(token)
            if result.result_class == "error":
                raise GdbMiCommandError(
                    "GDB command %d failed: %s" % (token, result.message or result.payload or result.raw)
                )
            if result.result_class not in accepted_classes:
                raise GdbMiCommandError(
                    "GDB command %d returned unexpected ^%s." % (token, result.result_class)
                )
            return result

    def _wait_for_result(self, token: int) -> MiResult:
        deadline = time.monotonic() + self.response_timeout_seconds
        with self._condition:
            while True:
                result = self._results.pop(token, None)
                if result is not None:
                    return result
                process = self._process
                if process is None or process.poll() is not None:
                    code = None if process is None else process.poll()
                    raise GdbMiProcessError(
                        "GDB exited before MI response for token %d (exit code %s)." % (token, code)
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GdbMiTimeoutError("Timed out waiting for GDB MI response token %d." % token)
                self._condition.wait(timeout=min(remaining, 0.05))

    def _read_stdout(self, process: GdbProcess) -> None:
        try:
            for raw in process.stdout:
                record = parse_mi_record(raw)
                if record is None:
                    continue
                with self._condition:
                    if record.prefix == "^" and record.token is not None:
                        result_class, separator, payload = record.body.partition(",")
                        self._results[record.token] = MiResult(
                            record.token, result_class, payload if separator else "", record.raw,
                        )
                    else:
                        self._async_records.append(record)
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._reader_finished = True
                self._condition.notify_all()

    def _write_raw(self, command: str) -> None:
        process = self._process
        if process is None or getattr(process, "stdin", None) is None:
            raise GdbMiProcessError("GDB standard input is unavailable.")
        try:
            process.stdin.write(command)
            process.stdin.flush()
        except Exception as error:
            raise GdbMiProcessError("Unable to write GDB MI command: %s" % error) from error

    @staticmethod
    def _quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
