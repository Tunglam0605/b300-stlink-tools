from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from b300_core.offline_symbols import OfflineSymbolTable


class FakeStdin:
    def __init__(self):
        self.writes = []
    def write(self, value):
        self.writes.append(value)
    def flush(self):
        pass


class FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)
    def readline(self):
        return self.lines.pop(0)


class FakePopen:
    def __init__(self, lines):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(lines)
        self.code = None
    def poll(self):
        return self.code
    def terminate(self):
        self.code = 0
    def wait(self, timeout=None):
        return self.code or 0
    def kill(self):
        self.code = -9


NM_OUTPUT = """
08025fd8 0000000a T vApplicationIdleHook
20000030 00000004 d xTickCount
20000034 00000004 d duplicate_local
20000038 00000004 d duplicate_local
"""

CATALOG_NM_OUTPUT = """
08001000 00000010 T FlashFunction
08002000 00000004 R flash_const
10000000 00000004 b ccm_value
1000fffc 00000004 b ccm_last_word
1000fffe 00000004 b ccm_cross
20000000 00000004 B ram_value
20000010 00000000 B zero_size
20000020 00000004 D duplicate_data
20000024 00000004 D duplicate_data
20000028 00000004 A absolute_in_ram
2001fffc 00000004 D sram_last_word
2001fffe 00000004 D sram_cross
"""


class OfflineSymbolTests(unittest.TestCase):
    def make_table(self, directory, popen=None, nm_output=NM_OUTPUT):
        image = Path(directory) / "firmware.axf"
        image.write_bytes(b"fake")
        completed = mock.Mock(returncode=0, stdout=nm_output, stderr="")
        stack = [
            mock.patch("b300_core.offline_symbols.resolve_arm_binutils", return_value=("nm", "addr2line")),
            mock.patch("b300_core.offline_symbols.subprocess.run", return_value=completed),
        ]
        if popen is not None:
            stack.append(mock.patch("b300_core.offline_symbols.subprocess.Popen", return_value=popen))
        for item in stack:
            item.start()
            self.addCleanup(item.stop)
        return OfflineSymbolTable(image)

    def test_catalog_classifies_symbols_and_exposes_immutable_watchability(self):
        nm_output = """
20000020 00000004 D ramValue
08020000 00000008 T worker
08030000 00000004 R flashConstant
20000010 00000004 A absoluteMarker
"""
        with tempfile.TemporaryDirectory() as directory:
            entries = {
                entry.name: entry
                for entry in self.make_table(directory, nm_output=nm_output).catalog()
            }

        ram = entries["ramValue"]
        self.assertEqual(
            (ram.address, ram.size, ram.kind, ram.category),
            (0x20000020, 4, "D", "data"),
        )
        self.assertTrue(ram.watchable)
        self.assertIsNone(ram.watch_block_reason)
        self.assertTrue(ram.name_unique)
        self.assertFalse(ram.ambiguous_name)
        self.assertEqual(ram.name_occurrences, 1)
        self.assertEqual(ram.distinct_address_count, 1)
        self.assertFalse(hasattr(ram, "type"))

        self.assertEqual(entries["worker"].category, "function")
        self.assertFalse(entries["worker"].watchable)
        self.assertIn("function", entries["worker"].watch_block_reason)
        self.assertEqual(entries["flashConstant"].category, "data")
        self.assertFalse(entries["flashConstant"].watchable)
        self.assertIn("CCM/SRAM", entries["flashConstant"].watch_block_reason)
        self.assertEqual(entries["absoluteMarker"].category, "other")
        self.assertFalse(entries["absoluteMarker"].watchable)
        self.assertIn("other", entries["absoluteMarker"].watch_block_reason)

        with self.assertRaises(FrozenInstanceError):
            ram.name = "changed"

    def test_symbol_lookup_is_exact_and_duplicate_names_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            table = self.make_table(directory)
            self.assertEqual(table.symbol("xTickCount").address, 0x20000030)
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                table.symbol("duplicate_local")
            with self.assertRaisesRegex(ValueError, "not found"):
                table.symbol("missing")

    def test_unknown_addr2line_location_is_normalized_to_none(self):
        process = FakePopen(["??\n", "??:?\n"])
        with tempfile.TemporaryDirectory() as directory:
            table = self.make_table(directory, process)
            location = table.source_location(0x08025FDA)
            self.assertEqual(location.function, "vApplicationIdleHook")
            self.assertIsNone(location.file)
            self.assertIsNone(location.line)
            table.close()

    def test_catalog_classifies_symbols_and_only_marks_safe_ram_data_watchable(self):
        with tempfile.TemporaryDirectory() as directory:
            table = self.make_table(directory, nm_output=CATALOG_NM_OUTPUT)
            entries = {item.name: item for item in table.catalog() if item.name != "duplicate_data"}
            self.assertEqual(entries["FlashFunction"].category, "function")
            self.assertFalse(entries["FlashFunction"].watchable)
            self.assertEqual(entries["FlashFunction"].watch_block_code, "not_data_symbol")
            self.assertEqual(entries["flash_const"].category, "data")
            self.assertFalse(entries["flash_const"].watchable)
            self.assertEqual(entries["flash_const"].watch_block_code, "outside_f407_ram")
            self.assertTrue(entries["ccm_value"].watchable)
            self.assertTrue(entries["ram_value"].watchable)
            self.assertEqual(entries["absolute_in_ram"].category, "other")
            self.assertFalse(entries["absolute_in_ram"].watchable)

    def test_catalog_uses_full_half_open_ram_span_at_ccm_and_sram_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            table = self.make_table(directory, nm_output=CATALOG_NM_OUTPUT)
            entries = {item.name: item for item in table.catalog() if item.name != "duplicate_data"}
            self.assertTrue(entries["ccm_last_word"].watchable)
            self.assertFalse(entries["ccm_cross"].watchable)
            self.assertEqual(entries["ccm_cross"].watch_block_code, "outside_f407_ram")
            self.assertTrue(entries["sram_last_word"].watchable)
            self.assertFalse(entries["sram_cross"].watchable)
            self.assertEqual(entries["sram_cross"].watch_block_code, "outside_f407_ram")

    def test_catalog_fails_closed_for_unknown_size_and_ambiguous_names(self):
        with tempfile.TemporaryDirectory() as directory:
            table = self.make_table(directory, nm_output=CATALOG_NM_OUTPUT)
            zero = next(item for item in table.catalog() if item.name == "zero_size")
            self.assertFalse(zero.watchable)
            self.assertEqual(zero.watch_block_code, "unknown_symbol_size")
            duplicates = [item for item in table.catalog() if item.name == "duplicate_data"]
            self.assertEqual(len(duplicates), 2)
            self.assertEqual([item.address for item in duplicates], [0x20000020, 0x20000024])
            self.assertTrue(all(not item.name_unique for item in duplicates))
            self.assertTrue(all(not item.watchable for item in duplicates))
            self.assertTrue(all(item.watch_block_code == "ambiguous_name" for item in duplicates))
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                table.symbol("duplicate_data")

    def test_catalog_order_and_search_are_deterministic_case_insensitive_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            table = self.make_table(directory, nm_output=CATALOG_NM_OUTPUT)
            catalog = table.catalog()
            expected = sorted(catalog, key=lambda item: (item.name.casefold(), item.name, item.address, item.kind, item.size))
            self.assertEqual(list(catalog), expected)
            value_names = [item.name for item in table.search_catalog("VALUE")]
            self.assertEqual(value_names, ["ccm_value", "ram_value"])
            watchable_data = table.search_catalog(category="data", watchable=True)
            self.assertTrue(watchable_data)
            self.assertTrue(all(item.category == "data" and item.watchable for item in watchable_data))
            self.assertEqual(len(table.search_catalog(category="data", limit=2)), 2)
            with self.assertRaisesRegex(ValueError, "category"):
                table.search_catalog(category="bogus")
            for bad_limit in (0, 1001, True):
                with self.subTest(limit=bad_limit):
                    with self.assertRaisesRegex(ValueError, "limit"):
                        table.search_catalog(limit=bad_limit)


if __name__ == "__main__":
    unittest.main()
