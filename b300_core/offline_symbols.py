"""Offline ELF/AXF symbol resolution for zero-halt live monitoring."""

from __future__ import annotations

import bisect
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .gdb_runtime import resolve_gdb
from .process_startup import child_process_kwargs


@dataclass(frozen=True)
class ElfSymbol:
    address: int
    size: int
    kind: str
    name: str


@dataclass(frozen=True)
class SourceLocation:
    address: int
    function: Optional[str]
    file: Optional[str]
    line: Optional[int]


def _sibling_tool(gdb_path: str, stem: str) -> str:
    gdb = Path(gdb_path)
    suffix = ".exe" if gdb.suffix.lower() == ".exe" else ""
    candidate = gdb.with_name("arm-none-eabi-%s%s" % (stem, suffix))
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("arm-none-eabi-%s" % stem)
    if found:
        return found
    raise RuntimeError("arm-none-eabi-%s was not found beside GDB or on PATH." % stem)


def resolve_arm_binutils(gdb_path: Optional[str] = None) -> Tuple[str, str]:
    gdb = resolve_gdb(gdb_path)
    return _sibling_tool(gdb, "nm"), _sibling_tool(gdb, "addr2line")


class OfflineSymbolTable:
    def __init__(self, image: Path, *, gdb_path: Optional[str] = None) -> None:
        self.image = Path(image).expanduser().resolve()
        if self.image.suffix.lower() not in {".elf", ".axf"} or not self.image.is_file():
            raise ValueError("Live Monitor symbols must be an existing ELF/AXF file.")
        nm, addr2line = resolve_arm_binutils(gdb_path)
        completed = subprocess.run(
            [nm, "-S", "-n", str(self.image)], capture_output=True, text=True,
            timeout=20.0, check=False, shell=False, **child_process_kwargs(),
        )
        if completed.returncode != 0:
            raise RuntimeError("arm-none-eabi-nm failed: %s" % (completed.stderr or completed.stdout))
        symbols = []
        by_name: Dict[str, list] = {}
        pattern = re.compile(r"^([0-9A-Fa-f]{8,16})\s+([0-9A-Fa-f]{1,16})\s+([A-Za-z])\s+(.+)$")
        for raw in completed.stdout.splitlines():
            match = pattern.match(raw.strip())
            if match is None:
                continue
            symbol = ElfSymbol(
                int(match.group(1), 16), int(match.group(2), 16),
                match.group(3), match.group(4).strip(),
            )
            symbols.append(symbol)
            by_name.setdefault(symbol.name, []).append(symbol)
        if not symbols:
            raise RuntimeError("No usable symbols were found in ELF/AXF.")
        self._symbols = tuple(symbols)
        self._addresses = tuple(item.address for item in symbols)
        self._by_name = {name: tuple(items) for name, items in by_name.items()}
        self._addr2line_executable = addr2line
        self._addr2line = None
        self._source_cache: Dict[int, SourceLocation] = {}

    def symbol(self, name: str) -> ElfSymbol:
        key = str(name).strip()
        candidates = self._by_name.get(key) if key else None
        if not candidates:
            raise ValueError("Symbol not found in ELF/AXF: %s" % key)
        unique_addresses = sorted({item.address for item in candidates})
        if len(unique_addresses) > 1:
            preview = ", ".join("0x%08X" % address for address in unique_addresses[:4])
            raise ValueError(
                "Symbol name is ambiguous in ELF/AXF: %s (%s%s). Use a unique symbol." %
                (key, preview, "..." if len(unique_addresses) > 4 else "")
            )
        return candidates[0]

    def nearest_symbol(self, address: int) -> Optional[ElfSymbol]:
        index = bisect.bisect_right(self._addresses, int(address)) - 1
        if index < 0:
            return None
        candidate = self._symbols[index]
        if candidate.size > 0 and int(address) >= candidate.address + candidate.size:
            return None
        return candidate

    def source_location(self, address: int) -> SourceLocation:
        address = int(address) & 0xFFFFFFFF
        cached = self._source_cache.get(address)
        if cached is not None:
            return cached
        if self._addr2line is None or self._addr2line.poll() is not None:
            self._addr2line = subprocess.Popen(
                [self._addr2line_executable, "-f", "-C", "-e", str(self.image)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, shell=False, **child_process_kwargs(),
            )
        assert self._addr2line.stdin is not None and self._addr2line.stdout is not None
        self._addr2line.stdin.write("0x%08X\n" % address)
        self._addr2line.stdin.flush()
        function = self._addr2line.stdout.readline().strip() or None
        location = self._addr2line.stdout.readline().strip()
        file_name = None
        line = None
        if location and not location.startswith("??:"):
            match = re.match(r"^(.*):(\d+)(?:\s.*)?$", location)
            if match:
                file_name = match.group(1)
                line = int(match.group(2))
            else:
                file_name = location
        if function == "??":
            function = None
        if function is None:
            nearest = self.nearest_symbol(address)
            function = nearest.name if nearest is not None else None
        resolved = SourceLocation(address, function, file_name, line)
        self._source_cache[address] = resolved
        return resolved

    def close(self) -> None:
        process = self._addr2line
        self._addr2line = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
