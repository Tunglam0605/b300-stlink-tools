"""Read-only STM32F407 flash access through OpenOCD."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from .models import ProbeRef
from .openocd import EventSink, OpenOcdRunner, _base_command, resolve_openocd, validate_openocd_value
from .policy import validate_read_range


def build_read_memory_command(probe: ProbeRef, executable: str, output: Path,
                              address: int, length: int):
    validate_read_range(address, length)
    output = Path(output).resolve()
    validate_openocd_value(output, "Memory output path")
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "reset halt",
        "-c", "dump_image {%s} 0x%08X %d" % (output, address, length),
        "-c", "resume",
        "-c", "shutdown",
    ]


def read_memory(probe: ProbeRef, address: int, length: int,
                executable: Optional[str] = None, runner: Optional[OpenOcdRunner] = None,
                event_sink: Optional[EventSink] = None) -> bytes:
    validate_read_range(address, length)
    active_runner = runner or OpenOcdRunner()
    with tempfile.TemporaryDirectory(prefix="b300-memory-") as directory:
        output = Path(directory) / "memory.bin"
        command = build_read_memory_command(
            probe, resolve_openocd(executable), output, address, length
        )
        result = active_runner.run(command, event_sink=event_sink)
        if result.returncode != 0:
            raise RuntimeError("OpenOCD memory read failed: %s" % result.output)
        try:
            data = output.read_bytes()
        except OSError as error:
            raise RuntimeError("OpenOCD did not create the memory dump: %s" % error) from error
        if len(data) != length:
            raise RuntimeError("Memory dump length mismatch: expected %d, got %d." %
                               (length, len(data)))
        return data

