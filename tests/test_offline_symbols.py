from __future__ import annotations

import tempfile
import unittest
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


class OfflineSymbolTests(unittest.TestCase):
    def make_table(self, directory, popen=None):
        image = Path(directory) / "firmware.axf"
        image.write_bytes(b"fake")
        completed = mock.Mock(returncode=0, stdout=NM_OUTPUT, stderr="")
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


if __name__ == "__main__":
    unittest.main()
