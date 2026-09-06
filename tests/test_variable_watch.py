from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

from b300_core.live_monitor import _decode_watch
from b300_core.typed_symbols import TypedSymbolCatalog, VariableNode
from b300_core.variable_watch import WatchCompileError, compile_watch, compile_watches
from tests.test_typed_symbols import _build_keil_fixture


class _Catalog:
    def __init__(self, nodes):
        self.nodes = {node.node_id: node for node in nodes}

    def node(self, node_id):
        if node_id not in self.nodes:
            raise ValueError("stale")
        return self.nodes[node_id]


def _node(node_id, address, value_type="u32", size=4, *, availability="watchable"):
    return VariableNode(
        node_id=node_id, name=node_id, type_name=value_type, kind="scalar",
        address=address, byte_size=size, has_children=False,
        availability=availability, reason=None, path=node_id, source_file="fixture.c",
        value_type=value_type,
    )


class VariableWatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.catalog = TypedSymbolCatalog(_build_keil_fixture(cls.temp.name))
        cls.root = next(item for item in cls.catalog.roots("g_machine", 0, 100)
                        if item.name == "g_machine")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def child(cls, parent, name):
        return next(item for item in cls.catalog.children(parent.node_id, 0, 100)
                    if item.name == name)

    def test_struct_and_array_leaves_compile_to_exact_bounded_ram_reads(self):
        position = self.child(self.root, "position")
        x = self.child(position, "x")
        rpm = self.child(self.root, "rpm")
        rpm1 = self.child(rpm, "[1]")

        x_watch, rpm_watch = compile_watches(self.catalog, (x.node_id, rpm1.node_id))
        self.assertEqual((x_watch.name, x_watch.value_type, x_watch.address, x_watch.size),
                         ("g_machine.position.x", "i16", 0x20000000, 2))
        self.assertEqual((rpm_watch.name, rpm_watch.value_type, rpm_watch.address, rpm_watch.size),
                         ("g_machine.rpm[1]", "u16", 0x20000006, 2))

    def test_enum_and_armcc_bitfield_decode_use_dwarf_metadata(self):
        mode_watch = compile_watch(self.catalog, self.child(self.root, "mode").node_id)
        flags_watch = compile_watch(self.catalog, self.child(self.root, "flags").node_id)

        mode = _decode_watch(mode_watch, {0x2000000C: 0x00000503})
        flags = _decode_watch(flags_watch, {0x2000000C: 0x00000503})
        self.assertEqual((mode.value, mode.enum_label), (3, "MODE_RUN"))
        self.assertEqual(flags.value, 5)
        self.assertEqual((flags_watch.bit_offset, flags_watch.bit_size), (8, 3))

    def test_pointer_reads_only_pointer_value_and_never_exposes_pointee_children(self):
        pointer = self.child(self.root, "next")
        watch = compile_watch(self.catalog, pointer.node_id)
        value = _decode_watch(watch, {0x20000018: 0x20001000})
        self.assertEqual((watch.value_type, watch.size, value.value), ("u32", 4, 0x20001000))
        self.assertFalse(pointer.has_children)
        self.assertIn("not dereferenced", pointer.reason)

    def test_container_stale_node_and_mmio_fail_before_read_plan(self):
        with self.assertRaisesRegex(WatchCompileError, "select a scalar") as container:
            compile_watch(self.catalog, self.root.node_id)
        self.assertEqual(container.exception.reason_code, "not_scalar")

        with self.assertRaises(WatchCompileError) as stale:
            compile_watch(self.catalog, "typed:old-image:node")
        self.assertEqual(stale.exception.reason_code, "stale_node")

        mmio = _node("mmio", 0x40000000)
        with self.assertRaises(WatchCompileError) as unsafe:
            compile_watch(_Catalog((mmio,)), mmio.node_id)
        self.assertEqual(unsafe.exception.reason_code, "outside_ram")

    def test_watch_and_swd_word_budgets_are_enforced_during_compile(self):
        seventeen = tuple(_node("n%d" % i, 0x20000000 + i * 4) for i in range(17))
        self.assertEqual(len(compile_watches(
            _Catalog(seventeen), tuple(node.node_id for node in seventeen)
        )), 17)

        sixty_five = tuple(
            _node("b%d" % i, 0x20000100 + i, "u8", 1) for i in range(65)
        )
        with self.assertRaises(WatchCompileError) as too_many:
            compile_watches(_Catalog(sixty_five), tuple(node.node_id for node in sixty_five))
        self.assertEqual(too_many.exception.reason_code, "too_many_watches")

        wide = tuple(_node("w%d" % i, 0x20000000 + i * 8, "f64", 8) for i in range(16))
        with self.assertRaises(WatchCompileError) as too_wide:
            compile_watches(_Catalog(wide), tuple(node.node_id for node in wide))
        self.assertEqual(too_wide.exception.reason_code, "read_budget")

    def test_distinct_dwarf_nodes_with_the_same_display_path_are_rejected_atomically(self):
        first = replace(_node("cu1:status", 0x20000000), name="status", path="status")
        second = replace(_node("cu2:status", 0x20000004), name="status", path="status")
        with self.assertRaises(WatchCompileError) as duplicate:
            compile_watches(_Catalog((first, second)), (first.node_id, second.node_id))
        self.assertEqual(duplicate.exception.reason_code, "duplicate_watch")


if __name__ == "__main__":
    unittest.main()
