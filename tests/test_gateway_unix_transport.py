"""Real local-socket checks for the isolated Gateway request boundary."""

import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from b300_core.gateway_agent_protocol import GatewayRequest
from b300_core.gateway_unix_transport import (
    GatewayUnixClient, GatewayUnixServer, MAX_REQUEST_BYTES,
)
from b300_core.gateway_system_mode import isolated_gateway_mode, load_isolated_gateway_config


@unittest.skipUnless(os.name == "posix" and hasattr(socket, "SO_PEERCRED"),
                     "Linux Unix peer credentials required")
class GatewayUnixTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "control.sock"
        self.stop = threading.Event()
        self.calls = []
        self.server = GatewayUnixServer(self.path, os.getuid(), self._submit)
        self.thread = threading.Thread(target=self.server.serve, args=(self.stop,), daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 2
        while not self.path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.path.exists())
        self.addCleanup(self._stop)

    def _stop(self):
        self.stop.set()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def _submit(self, request, timeout):
        self.calls.append((request, timeout))
        return {"protocol_version": 1, "request_id": request.request_id,
                "status": "ok", "reason_code": "OK", "result": {"state": "IDLE"}}

    def _raw(self, data):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(str(self.path))
            connection.sendall(data)
            try:
                return connection.recv(4096)
            except (ConnectionResetError, BrokenPipeError):
                return b""

    def test_same_uid_status_round_trip(self):
        request = GatewayRequest.create("status", {})
        response = GatewayUnixClient(self.path).submit_request(request, 1)
        self.assertEqual(response["result"], {"state": "IDLE"})
        self.assertEqual(self.calls[0][0], request)

    def test_oversized_frame_never_submits(self):
        self._raw(struct.pack(">I", MAX_REQUEST_BYTES + 1))
        self.assertEqual(self.calls, [])

    def test_invalid_schema_never_submits(self):
        payload = json.dumps({"operation": "status"}).encode()
        self._raw(struct.pack(">I", len(payload)) + payload)
        self.assertEqual(self.calls, [])

    def test_peer_uid_mismatch_never_submits(self):
        self.server.allowed_uid = os.getuid() + 1
        with self.assertRaises((OSError, ValueError)):
            GatewayUnixClient(self.path).submit_request(GatewayRequest.create("status", {}), 1)
        self.assertEqual(self.calls, [])

    def test_missing_socket_fails_without_submit(self):
        with self.assertRaises(OSError):
            GatewayUnixClient(self.path.with_name("absent.sock")).submit_request(
                GatewayRequest.create("status", {}), 1)
        self.assertEqual(self.calls, [])


class GatewaySystemModeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX ownership metadata required")
    def test_missing_marker_selects_legacy_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(isolated_gateway_mode(Path(directory) / "absent.json",
                                                   trusted_uid=os.getuid()))

    @unittest.skipUnless(os.name == "posix", "POSIX ownership metadata required")
    def test_invalid_present_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "marker.json"
            marker.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                isolated_gateway_mode(marker, trusted_uid=os.getuid())

    @unittest.skipUnless(os.name == "posix", "POSIX ownership metadata required")
    def test_valid_marker_provides_socket_and_operator_uid(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "marker.json"
            marker.write_text(json.dumps({
                "schema_version": 1,
                "socket_path": "/run/b300-stlink/agent.sock",
                "state_root": "/var/lib/b300-stlink/gateway",
                "ingress_root": "/var/spool/b300-stlink/ingress",
                "operator_uid": 1234,
            }), encoding="utf-8")
            self.assertTrue(isolated_gateway_mode(marker, trusted_uid=os.getuid()))
            self.assertEqual(load_isolated_gateway_config(
                marker, trusted_uid=os.getuid()).operator_uid, 1234)

if __name__ == "__main__":
    unittest.main()
