from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_session import LocalCredentialStore, RemoteAuthenticationError, RemoteSession


class FakeTransport:
    def __init__(self):
        self.active = True

    def is_active(self):
        return self.active


class FakeClient:
    def __init__(self, *, fail=False):
        self.transport = FakeTransport()
        self.fail = fail
        self.connect_calls = []
        self.closed = False

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        if self.fail:
            class AuthenticationException(Exception):
                pass
            raise AuthenticationException("bad password")

    def get_transport(self):
        return self.transport

    def close(self):
        self.closed = True
        self.transport.active = False


class MemoryStore:
    def __init__(self):
        self.secret = None

    def load(self, profile):
        return self.secret

    def save(self, profile, password):
        self.secret = password

    def clear(self, profile):
        existed = self.secret is not None
        self.secret = None
        return existed


class FakeForwardServer:
    def __init__(self, transport, *, name, local_host, local_port, remote_host, remote_port):
        from b300_core.remote_session import RemoteForward
        self.forward = RemoteForward(
            name, local_host, local_port or (13333 if name == "gdb" else 16666), remote_host, remote_port
        )
        self.stopped = False

    def stop(self):
        self.stopped = True


class RemoteSessionTests(unittest.TestCase):
    def setUp(self):
        self.profile = RemoteGatewayProfile("192.168.1.145", "Admin", 22)

    def test_connect_once_reuses_authenticated_session_and_remembers_password(self):
        store = MemoryStore()
        client = FakeClient()
        session = RemoteSession(self.profile, credential_store=store, ssh_client_factory=lambda: client)
        first = session.connect("secret", remember=True)
        second = session.connect()
        self.assertTrue(first.authenticated)
        self.assertTrue(second.authenticated)
        self.assertEqual(len(client.connect_calls), 1)
        self.assertEqual(store.secret, "secret")
        self.assertFalse(client.connect_calls[0]["look_for_keys"])
        self.assertFalse(client.connect_calls[0]["allow_agent"])

    def test_missing_password_fails_before_network(self):
        client = FakeClient()
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        with self.assertRaisesRegex(RemoteAuthenticationError, "required"):
            session.connect()
        self.assertEqual(client.connect_calls, [])

    def test_authentication_failure_does_not_expose_password_in_error(self):
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: FakeClient(fail=True)
        )
        with self.assertRaises(RemoteAuthenticationError) as captured:
            session.connect("super-secret")
        self.assertNotIn("super-secret", str(captured.exception))

    def test_one_session_owns_gdb_and_tcl_forwards(self):
        client = FakeClient()
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        with mock.patch("b300_core.remote_session._ForwardServer", FakeForwardServer):
            gdb, tcl = session.open_debug_forwards()
            self.assertEqual(gdb.endpoint, ("127.0.0.1", 13333))
            self.assertEqual(tcl.endpoint, ("127.0.0.1", 16666))
            self.assertEqual(session.state.forwards, ("gdb", "tcl"))
            session.disconnect()
        self.assertFalse(session.connected)
        self.assertTrue(client.closed)

    def test_local_credential_store_is_encrypted_not_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalCredentialStore(root=Path(directory))
            store.save(self.profile, "local-only-secret")
            self.assertEqual(store.load(self.profile), "local-only-secret")
            self.assertNotIn(b"local-only-secret", store.data_path.read_bytes())
            self.assertTrue(store.clear(self.profile))
            self.assertIsNone(store.load(self.profile))


if __name__ == "__main__":
    unittest.main()
