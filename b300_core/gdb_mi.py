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

    def __init__(self, executable: str = "arm-none-eabi-gdb",
                 process_factory: Optional[ProcessFactory] = None,
                 response_timeout_seconds: float = 5.0) -> None:
        if response_timeout_seconds <= 0:
            raise ValueError("GDB/MI response timeout must be positive.")
        self.executable = executable
        self._process_factory = process_factory or subprocess.Popen
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
        process = self._process_factory(
            [self.executable, "--interpreter=mi2"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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

    def disconnect(self) -> MiResult:
        return self._request("-target-disconnect", ("done",))

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
