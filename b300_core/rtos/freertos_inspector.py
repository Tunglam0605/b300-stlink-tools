"""DWARF-driven, HALT-only FreeRTOS task inspection.

The inspector never hard-codes TCB/List offsets.  All layout information is read
from the verified ELF/AXF DWARF image, while target RAM access goes only through
DebugMemoryBackend (read-only, HALT-only).
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

try:
    from elftools.elf.sections import SymbolTableSection
except ImportError:  # pragma: no cover - runtime dependency guard
    SymbolTableSection = ()  # type: ignore

from b300_core.debug_memory import DebugMemoryBackend
from b300_core.debug_types import DwarfTypeInfo, DwarfTypeService
from .rtos_models import FreeRtosSnapshot, FreeRtosTask


class FreeRtosInspector:
    """Read a bounded FreeRTOS task snapshot without guessing kernel layout."""

    MAX_TASKS = 128
    _TCB_NAMES = ("TCB_t", "tskTaskControlBlock")
    _LIST_NAMES = ("List_t", "xLIST")
    _LIST_ITEM_NAMES = ("ListItem_t", "xLIST_ITEM")

    def __init__(self, memory: DebugMemoryBackend, dwarf: DwarfTypeService) -> None:
        self.memory = memory
        self.dwarf = dwarf
        if int(getattr(dwarf, "pointer_size", 0) or 0) not in (4, 8):
            raise RuntimeError("FreeRTOS Inspector requires a 32-bit or 64-bit DWARF pointer model.")
        self.pointer_size = int(dwarf.pointer_size)
        self._symbol_cache: Dict[str, int] = {}

    def _require_halted(self) -> None:
        if self.memory.target_state != "halted":
            raise RuntimeError("FreeRTOS Inspector requires a HALTED target.")

    def _symbol_address(self, name: str) -> int:
        selected = str(name).strip()
        cached = self._symbol_cache.get(selected)
        if cached is not None:
            return cached
        for section in self.dwarf.elf.iter_sections():
            if not isinstance(section, SymbolTableSection):
                continue
            for symbol in section.iter_symbols():
                if symbol.name != selected:
                    continue
                try:
                    address = int(symbol["st_value"])
                except Exception:
                    continue
                if address:
                    self._symbol_cache[selected] = address
                    return address
        raise KeyError("ELF symbol was not found: %s" % selected)

    def _optional_symbol_address(self, name: str) -> Optional[int]:
        try:
            return self._symbol_address(name)
        except KeyError:
            return None

    def _read_uint(self, address: int, size: int = 4) -> int:
        selected_size = int(size)
        if selected_size not in (1, 2, 4, 8):
            raise ValueError("FreeRTOS scalar size must be 1/2/4/8 bytes.")
        return int.from_bytes(self.memory.read(int(address), selected_size).data, "little", signed=False)

    def _read_pointer(self, address: int) -> int:
        return self._read_uint(address, self.pointer_size)

    @staticmethod
    def _member_size(member, default: int = 4) -> int:
        try:
            size = int(member.byte_size or 0)
        except Exception:
            size = 0
        return size if size in (1, 2, 4, 8) else int(default)

    def _resolve_any_type(self, names: Iterable[str]) -> DwarfTypeInfo:
        errors = []
        for name in names:
            try:
                return self.dwarf.resolve_type(name)
            except Exception as error:
                errors.append(str(error))
        raise RuntimeError("Required FreeRTOS DWARF type is unavailable: %s" % ", ".join(names))

    def _canonical_member_type(self, type_info: DwarfTypeInfo, member_name: str) -> DwarfTypeInfo:
        return self.dwarf.canonical(self.dwarf.member_type(type_info, member_name))

    def _task_layout(self):
        tcb = self._resolve_any_type(self._TCB_NAMES)
        tcb_c = self.dwarf.canonical(tcb)
        priority = self.dwarf.member(tcb_c, "uxPriority")
        name = self.dwarf.member(tcb_c, "pcTaskName")
        top = self.dwarf.member(tcb_c, "pxTopOfStack")
        try:
            base_priority = self.dwarf.member(tcb_c, "uxBasePriority")
        except Exception:
            base_priority = None
        try:
            stack = self.dwarf.member(tcb_c, "pxStack")
        except Exception:
            stack = None
        name_type = self.dwarf.canonical(self.dwarf.member_type(tcb_c, "pcTaskName"))
        name_size = int(name_type.byte_size or name.byte_size or 16)
        name_size = max(1, min(name_size, 128))
        return tcb_c, priority, name, top, base_priority, stack, name_size

    def _list_layout(self):
        list_t = self.dwarf.canonical(self._resolve_any_type(self._LIST_NAMES))
        item_t = self.dwarf.canonical(self._resolve_any_type(self._LIST_ITEM_NAMES))
        count = self.dwarf.member(list_t, "uxNumberOfItems")
        end = self.dwarf.member(list_t, "xListEnd")
        end_type = self._canonical_member_type(list_t, "xListEnd")
        end_next = self.dwarf.member(end_type, "pxNext")
        item_next = self.dwarf.member(item_t, "pxNext")
        owner = self.dwarf.member(item_t, "pvOwner")
        return list_t, item_t, count, end, end_next, item_next, owner

    def _read_task(self, tcb_address: int, state: str, current_tcb: Optional[int]) -> FreeRtosTask:
        _tcb, priority_m, name_m, top_m, base_m, stack_m, name_size = self._task_layout()
        raw_name = self.memory.read(int(tcb_address) + int(name_m.offset), name_size).data
        name = raw_name.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip() or "<unnamed>"
        priority = self._read_uint(int(tcb_address) + int(priority_m.offset), self._member_size(priority_m))
        top = self._read_pointer(int(tcb_address) + int(top_m.offset))
        base_priority = None
        if base_m is not None:
            base_priority = self._read_uint(int(tcb_address) + int(base_m.offset), self._member_size(base_m))
        stack_start = None
        if stack_m is not None:
            stack_start = self._read_pointer(int(tcb_address) + int(stack_m.offset))
        effective_state = "RUNNING" if current_tcb is not None and int(tcb_address) == int(current_tcb) else state
        return FreeRtosTask(
            name=name,
            state=effective_state,
            priority=int(priority),
            base_priority=None if base_priority is None else int(base_priority),
            tcb_address=int(tcb_address),
            stack_pointer=int(top),
            stack_start=stack_start,
        )

    def _walk_list(self, list_address: int, state: str, current_tcb: Optional[int],
                   seen: Dict[int, FreeRtosTask]) -> None:
        list_t, _item_t, count_m, end_m, end_next_m, item_next_m, owner_m = self._list_layout()
        count = self._read_uint(int(list_address) + int(count_m.offset), self._member_size(count_m))
        if count > self.MAX_TASKS:
            raise RuntimeError("FreeRTOS list item count exceeds the bounded inspector limit.")
        end_address = int(list_address) + int(end_m.offset)
        item = self._read_pointer(end_address + int(end_next_m.offset))
        visited = set()
        budget = min(self.MAX_TASKS, max(1, int(count) + 1))
        for _ in range(budget):
            if item in (0, end_address):
                break
            if item in visited:
                raise RuntimeError("FreeRTOS list contains a cycle before xListEnd.")
            visited.add(item)
            owner = self._read_pointer(item + int(owner_m.offset))
            if owner and owner not in seen:
                seen[owner] = self._read_task(owner, state, current_tcb)
            item = self._read_pointer(item + int(item_next_m.offset))

    def _ready_lists(self) -> Tuple[Tuple[int, str], ...]:
        base = self._optional_symbol_address("pxReadyTasksLists")
        if base is None:
            return ()
        symbol_type = self.dwarf.canonical(self.dwarf.resolve_symbol_type("pxReadyTasksLists"))
        if symbol_type.kind != "array" or symbol_type.element_die_offset is None:
            raise RuntimeError("pxReadyTasksLists DWARF type is not a fixed array.")
        count = int(symbol_type.element_count or 0)
        if not 1 <= count <= 64:
            raise RuntimeError("pxReadyTasksLists priority count is unavailable or out of bounds.")
        element = self.dwarf.canonical(self.dwarf.type_by_offset(symbol_type.element_die_offset))
        element_size = int(element.byte_size or 0)
        if element_size <= 0:
            raise RuntimeError("FreeRTOS List_t size is unavailable in DWARF.")
        return tuple((int(base) + index * element_size, "READY") for index in range(count))

    def _optional_list_symbols(self) -> Tuple[Tuple[int, str], ...]:
        rows = []
        for name, state in (
            ("xDelayedTaskList1", "BLOCKED"),
            ("xDelayedTaskList2", "BLOCKED"),
            ("xPendingReadyList", "READY"),
            ("xSuspendedTaskList", "SUSPENDED"),
            ("xTasksWaitingTermination", "DELETED"),
        ):
            address = self._optional_symbol_address(name)
            if address is not None:
                rows.append((address, state))
        return tuple(rows)

    def _current_tcb(self) -> Optional[int]:
        address = self._optional_symbol_address("pxCurrentTCB")
        return None if address is None else self._read_pointer(address)

    def _declared_task_count(self) -> Optional[int]:
        address = self._optional_symbol_address("uxCurrentNumberOfTasks")
        if address is None:
            return None
        try:
            info = self.dwarf.resolve_symbol_type("uxCurrentNumberOfTasks")
            size = int(self.dwarf.sizeof(info))
        except Exception:
            size = 4
        if size not in (1, 2, 4, 8):
            size = 4
        return self._read_uint(address, size)

    def capture(self) -> FreeRtosSnapshot:
        self._require_halted()
        current = self._current_tcb()
        declared = self._declared_task_count()
        seen: Dict[int, FreeRtosTask] = {}
        limited = None
        sources = list(self._ready_lists()) + list(self._optional_list_symbols())
        if not sources:
            return FreeRtosSnapshot(current, declared, (), "FreeRTOS kernel list symbols were not found in ELF/AXF.")
        try:
            for address, state in sources:
                self._walk_list(address, state, current, seen)
        except Exception as error:
            limited = str(error)
        if current and current not in seen:
            try:
                seen[current] = self._read_task(current, "RUNNING", current)
            except Exception as error:
                limited = limited or str(error)
        tasks = tuple(sorted(seen.values(), key=lambda item: (-item.priority, item.name, item.tcb_address)))
        if declared is not None and declared > self.MAX_TASKS:
            limited = limited or "Declared FreeRTOS task count exceeds bounded inspector limit."
        elif declared is not None and len(tasks) < declared:
            limited = limited or "Snapshot is partial: not every declared task was reachable from known kernel lists."
        return FreeRtosSnapshot(current, declared, tasks, limited)
