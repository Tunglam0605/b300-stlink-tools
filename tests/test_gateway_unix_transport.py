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
from unittest import mock

from b300_core.gateway_agent_protocol import GatewayRequest
from b300_core.gateway_agent import GatewayAgent
from b300_core.gateway_agent_protocol import GatewayRequestStore
from b300_core.gateway_unix_transport import (
    GatewayUnixClient, GatewayUnixServer, MAX_REQUEST_BYTES,
)
from b300_core.gateway_system_mode import isolated_gateway_mode, load_isolated_gateway_config


class _SocketPeer:
    def __init__(self, payload):
        self.data = bytearray(struct.pack(">I", len(payload)) + payload)
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def settimeout(self, _timeout):
        pass

    def getsockopt(self, *_args):
        return struct.pack("3i", 42, 1234, 1234)

    def recv(self, length):
        chunk = bytes(self.data[:length])
        del self.data[:length]
        return chunk

    def sendall(self, data):
        self.sent.extend(data)


class _SocketListener:
    def __init__(self, peers):
        self.peers = iter(peers)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def bind(self, _path):
        pass

    def listen(self, _backlog):
        pass

    def settimeout(self, _timeout):
        pass

    def accept(self):
        return next(self.peers), None


class _PartialPeer(_SocketPeer):
    def __init__(self):
        super().__init__(b"")
        self.data = bytearray(b"\x00\x00")
        self.blocking = threading.Event()
        self.release = threading.Event()
        self.timeout = 60.0
        self.closed = False

    def __exit__(self, *_args):
        self.closed = True

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, length):
        if self.data:
            return super().recv(length)
        self.blocking.set()
        self.release.wait(min(self.timeout, 1.0))
        raise socket.timeout()


class GatewayUnixServerContinuityTests(unittest.TestCase):
    def test_deeply_nested_json_does_not_stop_next_valid_request(self):
        stop = threading.Event()
        invalid = _SocketPeer(b"[" * 1100 + b"0" + b"]" * 1100)
        request = GatewayRequest.create("status", {})
        valid = _SocketPeer(json.dumps(request.to_record()).encode("utf-8"))
        calls = []

        def submit(selected, _timeout):
            calls.append(selected.operation)
            stop.set()
            return {"protocol_version": 1, "request_id": selected.request_id,
                    "status": "ok", "reason_code": "OK"}

        server = GatewayUnixServer(Path("control.sock"), 1234, submit)
        with mock.patch("b300_core.gateway_unix_transport.socket.socket",
                        return_value=_SocketListener((invalid, valid))), \
                mock.patch("b300_core.gateway_unix_transport.os.name", "posix"), \
                mock.patch("b300_core.gateway_unix_transport.socket.AF_UNIX", 1, create=True), \
                mock.patch("b300_core.gateway_unix_transport.socket.SO_PEERCRED", 17, create=True), \
                mock.patch("b300_core.gateway_unix_transport.os.chmod"), \
                mock.patch("pathlib.Path.unlink"):
            server.serve(stop)
        self.assertEqual(calls, ["status"])
        self.assertEqual(invalid.sent, b"")
        self.assertTrue(valid.sent)

    def test_partial_frame_does_not_delay_agent_shutdown(self):
        peer = _PartialPeer()
        stop = threading.Event()
        shutdown = threading.Event()
        server = GatewayUnixServer(Path("control.sock"), 1234,
                                   lambda _request, _timeout: {})

        class Coordinator:
            def tick(self):
                return type("Snapshot", (), {"state": "IDLE", "reason_code": "OK",
                                              "active": False})()

            def shutdown(self, _reason):
                shutdown.set()

        with tempfile.TemporaryDirectory() as directory:
            agent = GatewayAgent(Coordinator(), request_store=GatewayRequestStore(
                Path(directory) / "requests"), socket_server=server,
                poll_interval_seconds=0.02)
            with mock.patch("b300_core.gateway_unix_transport.socket.socket",
                            return_value=_SocketListener((peer,))), \
                    mock.patch("b300_core.gateway_unix_transport.os.name", "posix"), \
                    mock.patch("b300_core.gateway_unix_transport.socket.AF_UNIX", 1, create=True), \
                    mock.patch("b300_core.gateway_unix_transport.socket.SO_PEERCRED", 17, create=True), \
                    mock.patch("b300_core.gateway_unix_transport.os.chmod"), \
                    mock.patch("pathlib.Path.unlink"):
                runner = threading.Thread(target=agent.run, args=(stop,), daemon=True)
                runner.start()
                try:
                    self.assertTrue(peer.blocking.wait(1))
                    stop.set()
                    runner.join(0.4)
                    self.assertFalse(runner.is_alive(), "Agent shutdown waited for a partial frame")
                    self.assertTrue(shutdown.is_set())
                    self.assertTrue(peer.closed)
                finally:
                    peer.release.set()
                    runner.join(2)


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
