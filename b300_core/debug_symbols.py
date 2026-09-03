"""Structured ELF/AXF symbol and source navigation for the v0.15 Debug Workstation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .offline_symbols import OfflineSymbolTable


@dataclass(frozen=True)
class DebugSymbolItem:
    name: str
    address: int
    size: int
    kind: str
    category: str
    watchable: bool


@dataclass(frozen=True)
class DebugSourceTarget:
    address: int
    function: Optional[str]
    file: Optional[str]
    line: Optional[int]
    source_available: bool


class DebugSymbolBrowserBackend:
    """Bounded symbol search and source resolution without touching the STM32 target."""

    def __init__(self, image: Path, *, symbol_table_factory=OfflineSymbolTable,
                 gdb_path: Optional[str] = None) -> None:
        self.image = Path(image).expanduser().resolve()
        if symbol_table_factory is OfflineSymbolTable:
            self._table = symbol_table_factory(self.image, gdb_path=gdb_path)
        else:
            self._table = symbol_table_factory(self.image)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Debug symbol browser is closed.")

    def search(self, query: str = "", *, category: Optional[str] = None,
               watchable: Optional[bool] = None, limit: int = 256) -> Tuple[DebugSymbolItem, ...]:
        self._require_open()
        rows = self._table.search_catalog(
            query,
            category=category,
            watchable=watchable,
            limit=limit,
        )
        return tuple(DebugSymbolItem(
            name=item.name,
            address=int(item.address),
            size=int(item.size),
            kind=item.kind,
            category=item.category,
            watchable=bool(item.watchable),
        ) for item in rows)

    def functions(self, query: str = "", *, limit: int = 256) -> Tuple[DebugSymbolItem, ...]:
        return self.search(query, category="function", limit=limit)

    def data_symbols(self, query: str = "", *, watchable: Optional[bool] = None,
                     limit: int = 256) -> Tuple[DebugSymbolItem, ...]:
        return self.search(query, category="data", watchable=watchable, limit=limit)

    def resolve_address(self, address: int) -> DebugSourceTarget:
        self._require_open()
        selected = int(address)
        if not 0 <= selected <= 0xFFFFFFFF:
            raise ValueError("Source address must fit in 32 bits.")
        location = self._table.source_location(selected)
        return DebugSourceTarget(
            address=int(location.address),
            function=location.function,
            file=location.file,
            line=location.line,
            source_available=bool(location.file and location.line),
        )

    def resolve_symbol(self, item: DebugSymbolItem) -> DebugSourceTarget:
        if not isinstance(item, DebugSymbolItem):
            raise TypeError("resolve_symbol requires a DebugSymbolItem.")
        return self.resolve_address(item.address)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._table.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
