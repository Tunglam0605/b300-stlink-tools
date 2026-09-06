"""Compile typed DWARF variable nodes into bounded zero-halt LiveWatch reads."""

from __future__ import annotations

from typing import Iterable, Tuple

from .live_monitor import (
    DWT_PCSR_ADDRESS, F407_RAM_RANGES, MAX_LIVE_READ_WORDS, MAX_LIVE_WATCHES,
    LiveWatch,
)


class WatchCompileError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code)


def _inside_ram(address: int, size: int) -> bool:
    end = address + size
    return size > 0 and any(start <= address and end <= limit for start, limit in F407_RAM_RANGES)


def compile_watch(catalog, node_id: str) -> LiveWatch:
    """Compile one catalog leaf; no transport is created or called here."""
    try:
        node = catalog.node(node_id)
    except (KeyError, ValueError) as error:
        raise WatchCompileError("stale_node", "The selected variable belongs to a stale AXF/ELF catalog.") from error
    if node.has_children:
        raise WatchCompileError("not_scalar", "Expand the variable and select a scalar member to watch.")
    if node.availability != "watchable":
        raise WatchCompileError("unavailable", node.reason or "The selected DWARF variable is unavailable.")
    if node.address is None or node.byte_size is None or node.value_type is None:
        raise WatchCompileError("incomplete_dwarf", "DWARF address, size, or scalar encoding is incomplete.")
    address = int(node.address)
    size = int(node.byte_size)
    if not _inside_ram(address, size):
        raise WatchCompileError("outside_ram", "The selected variable is outside STM32F407 CCM/SRAM.")
    return LiveWatch(
        name=node.path, value_type=node.value_type, address=address, size=size,
        bit_offset=node.bit_offset, bit_size=node.bit_size,
        enum_values=tuple(node.enum_values), node_id=node.node_id,
    )


def collect_watchable_node_ids(catalog, node_id: str) -> Tuple[str, ...]:
    """Return watchable scalar leaves below one selected DWARF node."""
    result = []
    visited = set()

    def visit(selected_id: str) -> None:
        if selected_id in visited:
            raise WatchCompileError("type_cycle", "DWARF variable tree contains a cycle.")
        visited.add(selected_id)
        try:
            node = catalog.node(selected_id)
        except (KeyError, ValueError) as error:
            raise WatchCompileError(
                "stale_node", "The selected variable belongs to a stale AXF/ELF catalog."
            ) from error
        if not node.has_children:
            if node.watchable:
                result.append(node.node_id)
                if len(result) > MAX_LIVE_WATCHES:
                    raise WatchCompileError(
                        "too_many_watches",
                        "Selected variable contains more than %d watchable fields."
                        % MAX_LIVE_WATCHES,
                    )
            return
        offset = 0
        while True:
            children = tuple(catalog.children(node.node_id, offset, 100))
            for child in children:
                visit(child.node_id)
            offset += len(children)
            if len(children) < 100:
                break

    visit(str(node_id))
    if not result:
        raise WatchCompileError(
            "no_watchable_descendants",
            "The selected variable has no supported scalar fields to watch.",
        )
    return tuple(result)


def compile_watches(catalog, node_ids: Iterable[str]) -> Tuple[LiveWatch, ...]:
    selected = tuple(str(node_id) for node_id in node_ids)
    if len(selected) > MAX_LIVE_WATCHES:
        raise WatchCompileError("too_many_watches", "At most %d live watches are allowed." % MAX_LIVE_WATCHES)
    if len(set(selected)) != len(selected):
        raise WatchCompileError("duplicate_watch", "A typed variable can only be watched once.")
    watches = tuple(compile_watch(catalog, node_id) for node_id in selected)
    names = tuple(watch.name for watch in watches)
    if len(set(names)) != len(names):
        raise WatchCompileError(
            "duplicate_watch", "Typed variables must have unique display paths.",
        )
    base_addresses = {DWT_PCSR_ADDRESS}
    verification_reads = 0
    for watch in watches:
        first = watch.address & ~3
        last = (watch.address + watch.size - 1) & ~3
        words = ((last - first) // 4) + 1
        base_addresses.update(first + index * 4 for index in range(words))
        if watch.size > 4:
            verification_reads += words
    if len(base_addresses) + verification_reads > MAX_LIVE_READ_WORDS:
        raise WatchCompileError(
            "read_budget", "Typed watches need more than %d SWD word reads per cycle." % MAX_LIVE_READ_WORDS,
        )
    return watches


__all__ = [
    "WatchCompileError", "collect_watchable_node_ids", "compile_watch", "compile_watches",
]
