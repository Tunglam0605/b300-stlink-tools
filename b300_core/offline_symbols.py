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


@dataclass(frozen=True)
class SymbolCatalogEntry:
    name: str
    address: int
    size: int
    kind: str
    category: str
    watchable: bool
    watch_block_code: Optional[str]
    watch_block_reason: Optional[str]
    name_unique: bool
    ambiguous_name: bool
    name_occurrences: int
    distinct_address_count: int


_F407_RAM_RANGES = ((0x10000000, 0x10010000), (0x20000000, 0x20020000))
_FUNCTION_KINDS = frozenset(("T", "t"))
_DATA_KINDS = frozenset(("B", "b", "C", "c", "D", "d", "G", "g",
                         "R", "r", "S", "s", "V", "v"))
_CATALOG_CATEGORIES = frozenset(("function", "data", "other"))
_MAX_CATALOG_RESULTS = 1000


def _symbol_category(kind: str) -> str:
    if kind in _FUNCTION_KINDS:
        return "function"
    if kind in _DATA_KINDS:
        return "data"
    return "other"


def _span_inside_f407_ram(address: int, size: int) -> bool:
    if size <= 0:
        return False
    end = address + size
    return any(start <= address and end <= limit for start, limit in _F407_RAM_RANGES)


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
        catalog = []
        for symbol in symbols:
            same_name = self._by_name[symbol.name]
            distinct_address_count = len({item.address for item in same_name})
            name_unique = distinct_address_count == 1
            category = _symbol_category(symbol.kind)
            watchable = False
            block_code = None
            block_reason = None
            if not name_unique:
                block_code = "ambiguous_name"
                block_reason = "Symbol name is ambiguous because it resolves to multiple addresses."
            elif category != "data":
                block_code = "not_data_symbol"
                block_reason = "Symbol category %s is not RAM data." % category
            elif symbol.size <= 0:
                block_code = "unknown_symbol_size"
                block_reason = "Symbol size is zero, so a safe RAM byte span cannot be proven."
            elif not _span_inside_f407_ram(symbol.address, symbol.size):
                block_code = "outside_f407_ram"
                block_reason = "Symbol byte span is not fully inside STM32F407 CCM/SRAM."
            else:
                watchable = True
            catalog.append(SymbolCatalogEntry(
                name=symbol.name, address=symbol.address, size=symbol.size, kind=symbol.kind,
                category=category, watchable=watchable, watch_block_code=block_code,
                watch_block_reason=block_reason, name_unique=name_unique,
                ambiguous_name=not name_unique, name_occurrences=len(same_name),
                distinct_address_count=distinct_address_count,
            ))
        self._catalog = tuple(sorted(
            catalog, key=lambda item: (item.name.casefold(), item.name, item.address, item.kind, item.size)
        ))
        self._addr2line_executable = addr2line
        self._addr2line = None
        self._source_cache: Dict[int, SourceLocation] = {}

    def catalog(self) -> Tuple[SymbolCatalogEntry, ...]:
        """Return the deterministic offline catalog without touching the target."""
        return self._catalog

    def search_catalog(
        self, query: str = "", *, category: Optional[str] = None,
        watchable: Optional[bool] = None, limit: int = 256,
    ) -> Tuple[SymbolCatalogEntry, ...]:
        """Filter the offline catalog for bounded GUI/CLI browsing."""
        if category is not None and category not in _CATALOG_CATEGORIES:
            raise ValueError("Symbol category must be function, data, other, or omitted.")
        if watchable is not None and not isinstance(watchable, bool):
            raise ValueError("watchable filter must be true, false, or omitted.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_CATALOG_RESULTS:
            raise ValueError("Symbol catalog limit must be 1..%d." % _MAX_CATALOG_RESULTS)
        needle = str(query).strip().casefold()
        selected = []
        for item in self._catalog:
            if needle and needle not in item.name.casefold():
                continue
            if category is not None and item.category != category:
                continue
            if watchable is not None and item.watchable is not watchable:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
        return tuple(selected)

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
