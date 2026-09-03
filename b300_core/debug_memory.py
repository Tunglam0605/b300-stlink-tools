"""Bounded read-only memory access for the Interactive Debug workstation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from .gdb_mi import GdbMiBackend, GdbMiCommandError


_CONTENTS = re.compile(r'contents="([0-9A-Fa-f]*)"')
TargetStateProvider = Callable[[], str]


@dataclass(frozen=True)
class DebugMemoryBlock:
    address: int
    data: bytes

    @property
    def length(self) -> int:
        return len(self.data)

    @property
    def end_address(self) -> int:
        return self.address + len(self.data)


class DebugMemoryBackend:
    """Read a small verified memory window while Interactive Debug is HALTED.

    This backend deliberately has no write primitive. Variable writes go through the
    bounded GDB variable-object API; raw memory mutation remains outside the normal
    B300 operator surface.
    """

    MAX_READ_BYTES = 1024

    def __init__(self, gdb: GdbMiBackend,
                 target_state_provider: Optional[TargetStateProvider] = None) -> None:
        self.gdb = gdb
        self._target_state_provider = target_state_provider

    @property
    def target_state(self) -> str:
        if self._target_state_provider is None:
            return "unknown"
        try:
            return str(self._target_state_provider()).strip().lower() or "unknown"
        except Exception:
            return "unknown"

    def read(self, address: int, length: int) -> DebugMemoryBlock:
        if self.target_state != "halted":
            raise RuntimeError("Memory View requires a HALTED target.")
        selected_address = int(address)
        selected_length = int(length)
        if not 0 <= selected_address <= 0xFFFFFFFF:
            raise ValueError("Memory address must fit in 32 bits.")
        if not 1 <= selected_length <= self.MAX_READ_BYTES:
            raise ValueError("Memory read length must be in range 1..%d bytes." % self.MAX_READ_BYTES)
        if selected_address + selected_length > 0x100000000:
            raise ValueError("Memory read range exceeds the 32-bit address space.")

        result = self.gdb._request(
            "-data-read-memory-bytes 0x%08X %d" % (selected_address, selected_length),
            ("done",),
        )
        chunks = _CONTENTS.findall(result.payload)
        if not chunks:
            raise GdbMiCommandError("GDB did not return memory contents.")
        try:
            data = b"".join(bytes.fromhex(chunk) for chunk in chunks)
        except ValueError as error:
            raise GdbMiCommandError("GDB returned malformed memory bytes.") from error
        if len(data) != selected_length:
            raise GdbMiCommandError(
                "GDB returned %d memory bytes; expected %d." % (len(data), selected_length)
            )
        return DebugMemoryBlock(selected_address, data)
