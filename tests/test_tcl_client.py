from __future__ import annotations

import unittest

from b300_core.tcl_client import SafeTclClient, TclClientError, TclEndpoint


class FakeSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = []
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, value: bytes):
        self.sent.append(value)

    def recv(self, _size: int) -> bytes:
        value, self.response = self.response, b""
        return value

    def close(self):
        self.closed = True


class SafeTclClientTests(unittest.TestCase):
    def make_client(self, response: bytes):
        sock = FakeSocket(response)
        calls = []
        client = SafeTclClient(
            socket_factory=lambda endpoint, timeout: calls.append((endpoint, timeout)) or sock
        )
        return client, sock, calls

    def test_version_uses_openocd_tcl_terminator(self) -> None:
        client, sock, calls = self.make_client(b"OpenOCD 0.12.0\x1a")
        self.assertEqual(client.version(), "OpenOCD 0.12.0")
        self.assertEqual(sock.sent, [b"version\x1a"])
        self.assertEqual(calls[0][0], ("127.0.0.1", 6666))
        self.assertTrue(sock.closed)

    def test_read_words_is_bounded_and_parses_exact_word_count(self) -> None:
        client, sock, _calls = self.make_client(
            b"0x20000000: 12345678 9abcdef0\n\x1a"
        )
        self.assertEqual(client.read_words(0x20000000, 2), (0x12345678, 0x9ABCDEF0))
        self.assertEqual(sock.sent, [b"mdw 0x20000000 2\x1a"])

    def test_read_words_rejects_unaligned_or_large_request(self) -> None:
        client, _sock, _calls = self.make_client(b"\x1a")
        with self.assertRaisesRegex(ValueError, "aligned"):
            client.read_words(0x20000001, 1)
        with self.assertRaisesRegex(ValueError, "1..256"):
            client.read_words(0x20000000, 257)

    def test_read_register_rejects_command_injection(self) -> None:
        client, _sock, _calls = self.make_client(b"\x1a")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            client.read_register("pc; flash erase_sector 0 0 7")

    def test_non_loopback_endpoint_is_rejected_before_socket(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            SafeTclClient(TclEndpoint("0.0.0.0", 6666))

    def test_missing_response_terminator_fails_closed(self) -> None:
        client, _sock, _calls = self.make_client(b"partial response")
        with self.assertRaisesRegex(TclClientError, "terminator"):
            client.version()


if __name__ == "__main__":
    unittest.main()
