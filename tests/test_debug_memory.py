from __future__ import annotations

import unittest

from b300_core.debug_memory import DebugMemoryBackend
from b300_core.gdb_mi import GdbMiCommandError


class Result:
    def __init__(self, payload):
        self.payload = payload


class FakeGdb:
    def __init__(self, payload='memory=[{begin="0x20000000",offset="0x0",end="0x20000004",contents="11223344"}]'):
        self.payload = payload
        self.calls = []

    def _request(self, command, accepted):
        self.calls.append((command, accepted))
        return Result(self.payload)


class DebugMemoryTests(unittest.TestCase):
    def test_read_returns_exact_bounded_bytes(self):
        gdb = FakeGdb()
        backend = DebugMemoryBackend(gdb, target_state_provider=lambda: "halted")
        block = backend.read(0x20000000, 4)
        self.assertEqual(block.address, 0x20000000)
        self.assertEqual(block.data, bytes.fromhex("11223344"))
        self.assertEqual(block.length, 4)
        self.assertEqual(block.end_address, 0x20000004)
        self.assertEqual(gdb.calls[0][0], "-data-read-memory-bytes 0x20000000 4")

    def test_memory_view_is_read_only_and_requires_halted_target(self):
        backend = DebugMemoryBackend(FakeGdb(), target_state_provider=lambda: "running")
        with self.assertRaisesRegex(RuntimeError, "HALTED"):
            backend.read(0x20000000, 4)
        self.assertFalse(hasattr(backend, "write"))

    def test_read_length_and_address_are_bounded(self):
        backend = DebugMemoryBackend(FakeGdb(), target_state_provider=lambda: "halted")
        with self.assertRaisesRegex(ValueError, "1..1024"):
            backend.read(0x20000000, 2048)
        with self.assertRaisesRegex(ValueError, "32 bits"):
            backend.read(-1, 4)
        with self.assertRaisesRegex(ValueError, "address space"):
            backend.read(0xFFFFFFFE, 4)

    def test_short_or_missing_gdb_payload_fails_closed(self):
        backend = DebugMemoryBackend(
            FakeGdb('memory=[{begin="0x20000000",contents="1122"}]'),
            target_state_provider=lambda: "halted",
        )
        with self.assertRaisesRegex(GdbMiCommandError, "expected 4"):
            backend.read(0x20000000, 4)
        backend = DebugMemoryBackend(FakeGdb("memory=[]"), target_state_provider=lambda: "halted")
        with self.assertRaisesRegex(GdbMiCommandError, "did not return"):
            backend.read(0x20000000, 4)


if __name__ == "__main__":
    unittest.main()
