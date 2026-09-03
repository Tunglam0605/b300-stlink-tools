from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.debug_symbols import DebugSymbolBrowserBackend, DebugSymbolItem


class FakeSymbolTable:
    def __init__(self, image):
        self.image = Path(image)
        self.closed = False

    def search_catalog(self, query="", *, category=None, watchable=None, limit=256):
        rows = (
            SimpleNamespace(
                name="Motor_Update", address=0x08014620, size=64, kind="T",
                category="function", watchable=False,
            ),
            SimpleNamespace(
                name="motor", address=0x20000100, size=48, kind="D",
                category="data", watchable=True,
            ),
        )
        selected = []
        for row in rows:
            if query and query.casefold() not in row.name.casefold():
                continue
            if category is not None and row.category != category:
                continue
            if watchable is not None and row.watchable is not watchable:
                continue
            selected.append(row)
        return tuple(selected[:limit])

    def source_location(self, address):
        if int(address) == 0x08014620:
            return SimpleNamespace(
                address=0x08014620, function="Motor_Update",
                file="C:/fw/motor.c", line=124,
            )
        return SimpleNamespace(address=int(address), function=None, file=None, line=None)

    def close(self):
        self.closed = True


class DebugSymbolBrowserTests(unittest.TestCase):
    def test_function_search_returns_compact_structured_rows(self):
        backend = DebugSymbolBrowserBackend(Path("firmware.axf"), symbol_table_factory=FakeSymbolTable)
        rows = backend.functions("motor")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Motor_Update")
        self.assertEqual(rows[0].address, 0x08014620)
        self.assertEqual(rows[0].category, "function")
        self.assertFalse(rows[0].watchable)

    def test_data_search_can_filter_watchable_symbols(self):
        backend = DebugSymbolBrowserBackend(Path("firmware.axf"), symbol_table_factory=FakeSymbolTable)
        rows = backend.data_symbols("motor", watchable=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "motor")
        self.assertTrue(rows[0].watchable)

    def test_resolve_symbol_returns_source_file_line(self):
        backend = DebugSymbolBrowserBackend(Path("firmware.axf"), symbol_table_factory=FakeSymbolTable)
        row = DebugSymbolItem("Motor_Update", 0x08014620, 64, "T", "function", False)
        target = backend.resolve_symbol(row)
        self.assertTrue(target.source_available)
        self.assertEqual(target.function, "Motor_Update")
        self.assertEqual(target.file, "C:/fw/motor.c")
        self.assertEqual(target.line, 124)

    def test_missing_debug_line_info_is_explicit_not_faked(self):
        backend = DebugSymbolBrowserBackend(Path("firmware.axf"), symbol_table_factory=FakeSymbolTable)
        target = backend.resolve_address(0x08018000)
        self.assertFalse(target.source_available)
        self.assertIsNone(target.file)
        self.assertIsNone(target.line)

    def test_close_is_idempotent_and_blocks_future_queries(self):
        backend = DebugSymbolBrowserBackend(Path("firmware.axf"), symbol_table_factory=FakeSymbolTable)
        table = backend._table
        backend.close()
        backend.close()
        self.assertTrue(table.closed)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            backend.functions()


if __name__ == "__main__":
    unittest.main()
