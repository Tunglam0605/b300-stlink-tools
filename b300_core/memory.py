"""Read-only STM32F407 flash access through OpenOCD."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Optional

from .models import ProbeRef
from .openocd import (
    EventSink,
    OpenOcdRunner,
    _base_command,
    build_resume_command,
    resolve_openocd,
    validate_openocd_value,
)
from .policy import validate_read_range


def build_read_memory_command(probe: ProbeRef, executable: str, output: Path,
                              address: int, length: int):
    validate_read_range(address, length)
    output = Path(output).resolve()
    validate_openocd_value(output, "Memory output path")
    guarded_dump = (
        "set B300_READ_FAILED [catch {dump_image {%s} 0x%08X %d} B300_READ_ERROR]" %
        (output, address, length)
    )
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "halt",
        "-c", guarded_dump,
        "-c", "resume",
        "-c", "if {$B300_READ_FAILED} {echo $B300_READ_ERROR; shutdown error}",
        "-c", "shutdown",
    ]


def read_memory(probe: ProbeRef, address: int, length: int,
                executable: Optional[str] = None, runner: Optional[OpenOcdRunner] = None,
                event_sink: Optional[EventSink] = None,
                cancel_event: Optional[threading.Event] = None) -> bytes:
    validate_read_range(address, length)
    active_runner = runner or OpenOcdRunner()
    with tempfile.TemporaryDirectory(prefix="b300-memory-") as directory:
        output = Path(directory) / "memory.bin"
        command = build_read_memory_command(
            probe, resolve_openocd(executable), output, address, length
        )
        result = active_runner.run(
            command,
            event_sink=event_sink,
            timeout_seconds=60.0,
            cancel_event=cancel_event,
        )
        if result.returncode != 0:
            recovery = None
            if result.timed_out or result.cancelled:
                recovery = active_runner.run(
                    build_resume_command(probe, resolve_openocd(executable)),
                    event_sink=event_sink,
                    timeout_seconds=20.0,
                )
            recovery_note = ""
            if recovery is not None and recovery.returncode != 0:
                recovery_note = " Resume recovery also failed: %s" % recovery.output
            if result.timed_out:
                raise RuntimeError(
                    "OpenOCD memory read timed out; separate resume recovery was requested.%s" %
                    recovery_note
                )
            if result.cancelled:
                raise RuntimeError(
                    "OpenOCD memory read was cancelled; separate resume recovery was requested.%s" %
                    recovery_note
                )
            raise RuntimeError("OpenOCD memory read failed: %s" % result.output)
        try:
            data = output.read_bytes()
        except OSError as error:
            raise RuntimeError("OpenOCD did not create the memory dump: %s" % error) from error
        if len(data) != length:
            raise RuntimeError("Memory dump length mismatch: expected %d, got %d." %
                               (length, len(data)))
        return data
