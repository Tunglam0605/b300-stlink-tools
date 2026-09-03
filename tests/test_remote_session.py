from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_session import (
    LocalCredentialStore, RemoteAuthenticationError, RemoteForward,
    RemoteForwardError, RemoteSession, RemoteSessionError,
)


class FakeTransport:
    def __init__(self):
        self.active = True
        self.keepalive_calls = []

    def is_active(self):
        return self.active

    def set_keepalive(self, seconds):
        self.keepalive_calls.append(seconds)


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
    def __init__(self, secret=None):
        self.secret = secret
        self.clear_calls = 0

    def load(self, profile):
        return self.secret

    def save(self, profile, password):
        self.secret = password

    def clear(self, profile):
        self.clear_calls += 1
        existed = self.secret is not None
        self.secret = None
        return existed


class FakeForwardServer:
    def __init__(self, transport, *, name, local_host, local_port, remote_host, remote_port):
        self.forward = RemoteForward(
            name, local_host, local_port or (13333 if name == "gdb" else 16666), remote_host, remote_port
        )
        self.alive = True
        self.stopped = False

    def matches(self, *, local_host, local_port, remote_host, remote_port):
        return (
            self.forward.local_host == local_host
            and (local_port == 0 or self.forward.local_port == local_port)
            and self.forward.remote_host == remote_host
            and self.forward.remote_port == remote_port
        )

    def stop(self):
        self.stopped = True
        self.alive = False


class ForwardFactory:
    def __init__(self, fail_name=None):
        self.fail_name = fail_name
        self.created = []

    def __call__(self, transport, **kwargs):
        if kwargs["name"] == self.fail_name:
            raise OSError("bind failed")
        server = FakeForwardServer(transport, **kwargs)
        self.created.append(server)
        return server


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
        self.assertEqual(client.transport.keepalive_calls, [15])

    def test_unchecked_remember_clears_an_older_password_after_success(self):
        store = MemoryStore("old-secret")
        client = FakeClient()
        session = RemoteSession(self.profile, credential_store=store, ssh_client_factory=lambda: client)
        session.connect("new-secret", remember=False)
        self.assertIsNone(store.secret)
        self.assertEqual(store.clear_calls, 1)

    def test_missing_password_fails_before_network(self):
        client = FakeClient()
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        with self.assertRaisesRegex(RemoteAuthenticationError, "required"):
            session.connect()
        self.assertEqual(client.connect_calls, [])
        self.assertEqual(session.state.error_code, "SSH_PASSWORD_REQUIRED")

    def test_authentication_failure_does_not_expose_password_in_error(self):
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: FakeClient(fail=True)
        )
        with self.assertRaises(RemoteAuthenticationError) as captured:
            session.connect("super-secret")
        self.assertNotIn("super-secret", str(captured.exception))
        self.assertEqual(session.state.error_code, "SSH_AUTH_FAILED")

    def test_bad_remembered_password_is_removed_so_gui_can_prompt_again(self):
        store = MemoryStore("stale-secret")
        session = RemoteSession(
            self.profile, credential_store=store, ssh_client_factory=lambda: FakeClient(fail=True)
        )
        with self.assertRaises(RemoteAuthenticationError):
            session.connect()
        self.assertIsNone(store.secret)
        self.assertEqual(store.clear_calls, 1)

    def test_one_session_owns_gdb_and_tcl_forwards(self):
        client = FakeClient()
        factory = ForwardFactory()
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
            forward_server_factory=factory,
        )
        session.connect("secret")
        gdb, tcl = session.open_debug_forwards()
        self.assertEqual(gdb.endpoint, ("127.0.0.1", 13333))
        self.assertEqual(tcl.endpoint, ("127.0.0.1", 16666))
        self.assertEqual(session.state.forwards, ("gdb", "tcl"))
        session.disconnect()
        self.assertFalse(session.connected)
        self.assertTrue(client.closed)
        self.assertTrue(all(server.stopped for server in factory.created))

    def test_existing_forward_is_reused_only_for_same_endpoint(self):
        client = FakeClient()
        factory = ForwardFactory()
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
            forward_server_factory=factory,
        )
        session.connect("secret")
        first = session.open_forward("gdb", remote_port=3333)
        second = session.open_forward("gdb", remote_port=3333)
        self.assertEqual(first, second)
        self.assertEqual(len(factory.created), 1)
        with self.assertRaisesRegex(RemoteForwardError, "different endpoint"):
            session.open_forward("gdb", remote_port=4444)

    def test_tcl_failure_does_not_close_a_preexisting_gdb_forward(self):
        client = FakeClient()
        factory = ForwardFactory(fail_name="tcl")
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
            forward_server_factory=factory,
        )
        session.connect("secret")
        gdb = session.open_forward("gdb", remote_port=3333)
        with self.assertRaises(RemoteForwardError):
            session.open_debug_forwards()
        self.assertEqual(session.state.forwards, ("gdb",))
        self.assertEqual(session.open_forward("gdb", remote_port=3333), gdb)

    def test_health_drops_dead_forward_and_detects_lost_transport(self):
        client = FakeClient()
        factory = ForwardFactory()
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
            forward_server_factory=factory,
        )
        session.connect("secret")
        session.open_forward("tcl", remote_port=6666)
        factory.created[0].alive = False
        state = session.check_health()
        self.assertEqual(state.forwards, ())
        client.transport.active = False
        state = session.check_health()
        self.assertEqual(state.state, "error")
        self.assertEqual(state.error_code, "SSH_SESSION_LOST")

    def test_reconnect_creates_new_transport_and_generation_changes(self):
        clients = [FakeClient(), FakeClient()]
        session = RemoteSession(
            self.profile, credential_store=MemoryStore("remembered"),
            ssh_client_factory=lambda: clients.pop(0),
        )
        first = session.connect()
        first_generation = first.generation
        second = session.reconnect()
        self.assertTrue(second.authenticated)
        self.assertGreater(second.generation, first_generation)
        self.assertEqual(len(clients), 0)

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
