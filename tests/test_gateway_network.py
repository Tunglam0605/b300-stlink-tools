import unittest

from b300_core.gateway_network import probe_gateway_ssh_endpoint


class _Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class GatewayNetworkTests(unittest.TestCase):
    def test_tcp_probe_reports_a_reachable_configured_gateway_port(self):
        calls = []
        connection = _Connection()

        def connector(address, timeout):
            calls.append((address, timeout))
            return connection

        result = probe_gateway_ssh_endpoint("192.168.1.145", 2222, connector=connector)

        self.assertTrue(result.ready)
        self.assertEqual(result.endpoint, "192.168.1.145:2222")
        self.assertEqual(calls, [(("192.168.1.145", 2222), 3.0)])
        self.assertTrue(connection.closed)

    def test_tcp_probe_classifies_an_unreachable_gateway_without_scanning_a_key(self):
        def connector(address, timeout):
            raise ConnectionRefusedError("refused")

        result = probe_gateway_ssh_endpoint("192.168.1.145", 22, connector=connector)

        self.assertFalse(result.ready)
        self.assertEqual(result.reason_code, "SSH_TCP_UNREACHABLE")
        self.assertIn("192.168.1.145:22", result.message)


if __name__ == "__main__":
    unittest.main()
