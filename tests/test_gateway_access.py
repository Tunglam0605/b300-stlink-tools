from __future__ import annotations

import socket
import unittest
from unittest import mock

from b300_core.gateway_access import discover_gateway_access


class _Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class GatewayAccessTests(unittest.TestCase):
    def test_discovery_filters_unusable_addresses_and_reports_live_ssh(self):
        connection = _Connection()
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.2.3", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.0.4", 0)),
        ]
        with mock.patch("b300_core.gateway_access.socket.gethostname", return_value="B300-PC"), \
                mock.patch("b300_core.gateway_access.getpass.getuser", return_value="operator"), \
                mock.patch("b300_core.gateway_access.socket.getaddrinfo", return_value=records):
            info = discover_gateway_access(connect=lambda endpoint, timeout: connection)

        self.assertEqual(info.user, "operator")
        self.assertEqual(info.hostname, "B300-PC")
        self.assertEqual(info.addresses, ("192.168.1.50", "10.20.0.4"))
        self.assertTrue(info.ssh_ready)
        self.assertTrue(connection.closed)

    def test_discovery_keeps_identity_when_ssh_is_not_listening(self):
        def refused(_endpoint, timeout):
            raise ConnectionRefusedError("closed")

        with mock.patch("b300_core.gateway_access.socket.gethostname", return_value="B300-PC"), \
                mock.patch("b300_core.gateway_access.getpass.getuser", return_value="operator"), \
                mock.patch("b300_core.gateway_access.socket.getaddrinfo", side_effect=OSError("offline")):
            info = discover_gateway_access(connect=refused)

        self.assertEqual(info.addresses, ())
        self.assertFalse(info.ssh_ready)


if __name__ == "__main__":
    unittest.main()
