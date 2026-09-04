from __future__ import annotations

import unittest
from types import SimpleNamespace

from b300_core.rtos import FreeRtosInspector


class _Block:
    def __init__(self, address, data):
        self.address = address
        self.data = bytes(data)


class _Memory:
    target_state = "halted"

    def __init__(self):
        self.bytes = {}

    def put(self, address, data):
        for index, value in enumerate(bytes(data)):
            self.bytes[address + index] = value

    def put_u32(self, address, value):
        self.put(address, int(value).to_bytes(4, "little"))

    def read(self, address, length):
        return _Block(address, bytes(self.bytes.get(address + index, 0) for index in range(length)))


class _Dwarf:
    pointer_size = 4
    elf = SimpleNamespace(iter_sections=lambda: ())

    def __init__(self):
        self.tcb = SimpleNamespace(name="TCB_t", kind="structure", byte_size=64, die_offset=1)
        self.list_t = SimpleNamespace(name="List_t", kind="structure", byte_size=24, die_offset=2)
        self.item_t = SimpleNamespace(name="ListItem_t", kind="structure", byte_size=20, die_offset=3)
        self.mini_t = SimpleNamespace(name="MiniListItem_t", kind="structure", byte_size=12, die_offset=4)
        self.name_array = SimpleNamespace(name="char[16]", kind="array", byte_size=16, die_offset=5)
        self.ready_array = SimpleNamespace(
            name="pxReadyTasksLists", kind="array", byte_size=48,
            die_offset=6, element_die_offset=100, element_count=2,
        )
        self.members = {
            (1, "uxPriority"): SimpleNamespace(offset=4, byte_size=4),
            (1, "pcTaskName"): SimpleNamespace(offset=16, byte_size=16),
            (1, "pxTopOfStack"): SimpleNamespace(offset=0, byte_size=4),
            (1, "uxBasePriority"): SimpleNamespace(offset=8, byte_size=4),
            (1, "pxStack"): SimpleNamespace(offset=12, byte_size=4),
            (2, "uxNumberOfItems"): SimpleNamespace(offset=0, byte_size=4),
            (2, "xListEnd"): SimpleNamespace(offset=4, byte_size=12),
            (4, "pxNext"): SimpleNamespace(offset=4, byte_size=4),
            (3, "pxNext"): SimpleNamespace(offset=4, byte_size=4),
            (3, "pvOwner"): SimpleNamespace(offset=12, byte_size=4),
        }

    def resolve_type(self, name):
        return {
            "TCB_t": self.tcb,
            "List_t": self.list_t,
            "ListItem_t": self.item_t,
        }[name]

    def canonical(self, info):
        return info

    def member(self, info, name):
        return self.members[(info.die_offset, name)]

    def member_type(self, info, name):
        if info.die_offset == 1 and name == "pcTaskName":
            return self.name_array
        if info.die_offset == 2 and name == "xListEnd":
            return self.mini_t
        raise KeyError(name)

    def resolve_symbol_type(self, name):
        if name == "pxReadyTasksLists":
            return self.ready_array
        if name == "uxCurrentNumberOfTasks":
            return SimpleNamespace(name="UBaseType_t", kind="base", byte_size=4, die_offset=7)
        raise KeyError(name)

    def type_by_offset(self, offset):
        if offset == 100:
            return self.list_t
        raise KeyError(offset)

    def sizeof(self, info):
        return int(info.byte_size)


class _Inspector(FreeRtosInspector):
    def __init__(self, memory, dwarf, symbols):
        super().__init__(memory, dwarf)
        self._symbols = dict(symbols)

    def _symbol_address(self, name):
        if name not in self._symbols:
            raise KeyError(name)
        return self._symbols[name]


class FreeRtosInspectorTests(unittest.TestCase):
    def test_ready_lists_are_decoded_without_hardcoded_tcb_offsets(self):
        memory = _Memory()
        ready = 0x20000000
        list_size = 24
        item_a = 0x20000100
        item_b = 0x20000120
        tcb_a = 0x20000200
        tcb_b = 0x20000280
        current_ptr = 0x20001000
        count_addr = 0x20001004

        for list_address, item in ((ready, item_a), (ready + list_size, item_b)):
            end = list_address + 4
            memory.put_u32(list_address + 0, 1)
            memory.put_u32(end + 4, item)
            memory.put_u32(item + 4, end)
        memory.put_u32(item_a + 12, tcb_a)
        memory.put_u32(item_b + 12, tcb_b)
        memory.put_u32(current_ptr, tcb_b)
        memory.put_u32(count_addr, 2)

        for address, name, priority, stack_top, stack_base in (
            (tcb_a, b"worker\0", 2, 0x20002000, 0x20001800),
            (tcb_b, b"control\0", 5, 0x20003000, 0x20002800),
        ):
            memory.put_u32(address + 0, stack_top)
            memory.put_u32(address + 4, priority)
            memory.put_u32(address + 8, priority)
            memory.put_u32(address + 12, stack_base)
            memory.put(address + 16, name.ljust(16, b"\0"))

        inspector = _Inspector(memory, _Dwarf(), {
            "pxReadyTasksLists": ready,
            "pxCurrentTCB": current_ptr,
            "uxCurrentNumberOfTasks": count_addr,
        })
        snapshot = inspector.capture()
        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.declared_task_count, 2)
        self.assertEqual([task.name for task in snapshot.tasks], ["control", "worker"])
        self.assertEqual(snapshot.tasks[0].state, "RUNNING")
        self.assertEqual(snapshot.tasks[1].state, "READY")
        self.assertEqual(snapshot.tasks[0].priority, 5)
        self.assertEqual(snapshot.tasks[0].stack_start, 0x20002800)

    def test_running_target_fails_closed(self):
        memory = _Memory()
        memory.target_state = "running"
        inspector = _Inspector(memory, _Dwarf(), {})
        with self.assertRaises(RuntimeError):
            inspector.capture()


if __name__ == "__main__":
    unittest.main()
