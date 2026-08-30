from __future__ import annotations

import unittest

from b300_core.tcl_client import SafeTclClient
from tests.test_tcl_client import FakeSocket


class TclLiveReadTests(unittest.TestCase):
    def make_client(self, response: bytes):
        sock = FakeSocket(response)
        client = SafeTclClient(socket_factory=lambda endpoint, timeout: sock)
        return client, sock

    def test_multi_read_parses_exact_values_and_stays_read_only(self):
        client, sock = self.make_client(
            b"{0xe000101c: 08025fda } {0x20000030: 00123456 }\x1a"
        )
        self.assertEqual(
            client.read_word_addresses((0xE000101C, 0x20000030)),
            (0x08025FDA, 0x00123456),
        )
        command = sock.sent[0].decode("ascii").lower()
        self.assertIn("mdw 0xe000101c 1", command)
        self.assertIn("mdw 0x20000030 1", command)
        for token in ("halt", "resume", "reset", "flash", "mww", "mwh", "mwb"):
            self.assertNotIn(token, command)

    def test_multi_read_rejects_unaligned_or_unbounded_request(self):
        client, _sock = self.make_client(b"\x1a")
        with self.assertRaisesRegex(ValueError, "1..32"):
            client.read_word_addresses(())
        with self.assertRaisesRegex(ValueError, "aligned"):
            client.read_word_addresses((0x20000001,))
        with self.assertRaisesRegex(ValueError, "1..32"):
            client.read_word_addresses(tuple(0x20000000 + index * 4 for index in range(33)))


if __name__ == "__main__":
    unittest.main()
