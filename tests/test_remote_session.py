from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex

from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_session import (
    LocalCredentialStore, RemoteAuthenticationError, RemoteForward,
    RemoteForwardError, RemoteSession, RemoteSessionError,
)


class FakeTransport:
    def __init__(self):
        self.active = True
        self.keepalive_calls = []
        self.channel_requests = []
        self.channel_error = None
        self.channel = FakeChannel()

    def open_channel(self, kind, destination, origin, *, timeout):
        self.channel_requests.append((kind, destination, origin, timeout))
        if self.channel_error:
            raise self.channel_error
        return self.channel

    def is_active(self):
        return self.active

    def set_keepalive(self, seconds):
        self.keepalive_calls.append(seconds)


class FakeChannel:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


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


class FakeSftp:
    def __init__(self):
        self.transfers = []
        self.closed = False

    def put(self, local, remote, callback=None):
        self.transfers.append((Path(local).read_bytes(), remote))
        if callback:
            callback(len(self.transfers[-1][0]), len(self.transfers[-1][0]))

    def close(self):
        self.closed = True


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
    def test_remote_flash_rejects_legacy_flash_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=FakeClient)
            session.connect("secret")
            grant = SimpleNamespace(public={"probe_serial": "SAFE123"})
            with mock.patch.object(session, "ensure_gateway_agent", return_value={
                    "capabilities": ["remote_application_flash_v1"]}), \
                    mock.patch.object(session, "_run_program_request") as program:
                with self.assertRaises(RemoteSessionError) as captured:
                    session.prepare_remote_application(image, grant, "client-1")
            self.assertEqual(captured.exception.reason_code, "REMOTE_FLASH_UNSUPPORTED")
            program.assert_not_called()

    def test_lost_upload_slot_response_retries_same_request_id_once(self):
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=FakeClient)
        session.connect("secret")
        calls = []
        def control(command, **kwargs):
            calls.append(json.loads(kwargs["stdin_payload"]))
            if len(calls) == 1:
                raise RemoteSessionError(
                    "SSH response lost", reason_code="CLI_EXECUTION_FAILED",
                    phase="gateway_cli",
                )
            return {"job_id": "a" * 32, "state": "UPLOADING"}
        with mock.patch.object(session, "_run_gateway_control", side_effect=control):
            slot = session._run_program_request("program_create_upload", {
                "manifest": {}, "client_id": "client-1", "probe_serial": None,
            })
        self.assertEqual(slot["job_id"], "a" * 32)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["request_id"], calls[1]["request_id"])
        self.assertEqual(calls[0]["payload"], calls[1]["payload"])

    def test_interrupted_upload_requests_gateway_slot_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=FakeClient)
            session.connect("secret")
            job_id = "a" * 32
            slot = {"job_id": job_id, "upload_path": "/home/aubot/program-jobs/" + job_id + "/artifact.part"}
            operations = []
            def command(operation, payload, **kwargs):
                operations.append(operation)
                return slot if operation == "program_create_upload" else {"job_id": job_id, "state": "CANCELLED"}
            grant = SimpleNamespace(lease_id="lease", token="secret", generation=1,
                                    public={"probe_serial": "SAFE123"})
            with mock.patch.object(session, "ensure_gateway_agent", return_value={"capabilities": ["remote_application_flash_isolated_v1"]}), \
                    mock.patch.object(session, "_run_program_request", side_effect=command), \
                    mock.patch.object(session, "upload_application_file", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    session.prepare_remote_application(image, grant, "client-1")
            self.assertEqual(operations, ["program_create_upload", "program_cancel"])

    def test_remote_programming_requires_pinned_gateway_host_key(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=FakeClient)
            session.connect("secret")
            session._host_pinned = False
            with self.assertRaises(RemoteSessionError) as captured:
                session.prepare_remote_application(
                    image,
                    SimpleNamespace(public={"probe_serial": "SAFE123"}),
                    "client-1",
                )
            self.assertEqual(captured.exception.reason_code, "HOST_KEY_UNTRUSTED")

    def test_prepare_remote_application_uploads_then_requests_gateway_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            client = FakeClient()
            sftp = FakeSftp()
            client.open_sftp = lambda: sftp
            session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
            session.connect("secret")
            job_id = "b" * 32
            slot = {"job_id": job_id, "upload_path": "/home/aubot/program-jobs/" + job_id + "/artifact.part"}
            prepared = {"job_id": job_id, "state": "AWAITING_CONFIRMATION", "plan": {"erase_sectors": [3, 4, 5, 6, 7]}, "approval_token": "approval"}
            responses = iter((slot, {"state": "STAGED"}, prepared))
            with mock.patch.object(session, "ensure_gateway_agent", return_value={"capabilities": ["remote_application_flash_isolated_v1"]}), \
                    mock.patch.object(session, "_run_gateway_control", side_effect=lambda *a, **k: next(responses)) as control:
                result = session.prepare_remote_application(
                    image, SimpleNamespace(lease_id="lease", token="secret", generation=1,
                                           public={"probe_serial": "SAFE123"}), "client-1",
                )
            self.assertEqual(result, prepared)
            self.assertEqual(sftp.transfers[0][0], image.read_bytes())
            self.assertEqual([json.loads(call.kwargs["stdin_payload"])["operation"] for call in control.call_args_list], [
                "program_create_upload", "program_finalize_upload", "program_prepare",
            ])

    def test_program_upload_uses_sftp_only_for_gateway_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "application.hex"
            image.write_bytes(b":00000001FF\n")
            client = FakeClient()
            sftp = FakeSftp()
            client.open_sftp = lambda: sftp
            session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
            session.connect("secret")
            slot = {
                "job_id": "a" * 32,
                "upload_path": "/home/aubot/.b300-stlink/gateway-runtime/program-jobs/" + "a" * 32 + "/artifact.part",
            }
            session.upload_application_file(image, slot)
            self.assertEqual(sftp.transfers, [(image.read_bytes(), slot["upload_path"])])
            self.assertTrue(sftp.closed)
            with self.assertRaises(ValueError):
                session.upload_application_file(image, {
                    "job_id": "a" * 32, "upload_path": "/tmp/other.hex",
                })

    def setUp(self):
        self.profile = RemoteGatewayProfile("192.168.1.145", "Admin", 22)

    def test_listener_preflight_closes_bounded_loopback_channel_without_creating_forward(self):
        client = FakeClient()
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        session.require_remote_listener(remote_port=4333, timeout_seconds=1.5)
        self.assertEqual(client.transport.channel_requests, [
            ("direct-tcpip", ("127.0.0.1", 4333), ("127.0.0.1", 0), 1.5),
        ])
        self.assertTrue(client.transport.channel.closed)
        self.assertEqual(session.state.forwards, ())
        self.assertTrue(session.connected)

    def test_listener_preflight_refusal_timeout_or_missing_channel_fails_closed(self):
        for failure in (OSError("refused"), TimeoutError("timed out"), None):
            with self.subTest(failure=failure):
                client = FakeClient()
                client.transport.channel_error = failure
                client.transport.channel = None
                session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
                session.connect("secret")
                with self.assertRaises(RemoteForwardError):
                    session.require_remote_listener(remote_port=3333)
                self.assertEqual(session.state.forwards, ())

    def test_listener_preflight_requires_connected_session_and_bounded_arguments(self):
        session = RemoteSession(self.profile, credential_store=MemoryStore())
        with self.assertRaises(RemoteForwardError):
            session.require_remote_listener(remote_port=3333)
        for port, timeout in ((0, 3), (65536, 3), (3333, 0), (3333, float("inf")), (3333, float("nan"))):
            with self.subTest(port=port, timeout=timeout), self.assertRaises(ValueError):
                session.require_remote_listener(remote_port=port, timeout_seconds=timeout)

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
        with self.assertRaisesRegex(RemoteAuthenticationError, "required") as captured:
            session.connect()
        self.assertEqual(captured.exception.reason_code, "SSH_PASSWORD_REQUIRED")
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

    def test_tunnel_failure_exposes_structured_next_action(self):
        client = FakeClient()
        client.transport.channel_error = OSError("refused")
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        with self.assertRaises(RemoteForwardError) as captured:
            session.require_remote_listener(remote_port=3333)
        self.assertEqual(captured.exception.reason_code, "TUNNEL_FAILED")
        self.assertEqual(captured.exception.phase, "ssh_tunnel")
        self.assertTrue(captured.exception.next_action)
        self.assertTrue(captured.exception.retriable)

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

    def test_acquire_gateway_exposes_only_strict_public_lease_record(self):
        session = RemoteSession(self.profile, credential_store=MemoryStore())
        payload = {
            "active": True, "lease_id": "lease-1", "lease_token": "secret-token",
            "lease_generation": 7, "generation": 7, "client_label": "lab",
            "mode": "VSCODE_DEBUG", "state": "ACTIVE",
            "acquired_at": "2026-09-09T00:00:00Z", "heartbeat_age_seconds": 0,
            "gateway_instance_id": "gateway-1", "gateway_generation": 3,
            "probe_serial": "SAFE123", "reason_code": "LEASE_ACTIVE",
            "gdb_endpoint": "127.0.0.1:3333", "tcl_endpoint": "127.0.0.1:6666",
        }
        with mock.patch.object(session, "_run_gateway_control", return_value=payload):
            grant = session.acquire_gateway({
                "request_id": "request-1", "client_id": "lab", "client_label": "lab",
                "mode": "VSCODE_DEBUG", "probe_serial": "SAFE123",
            })
        self.assertEqual(grant.public["gdb_endpoint"], "127.0.0.1:3333")
        self.assertNotIn("lease_token", grant.public)
        self.assertNotIn("lease_generation", grant.public)


if __name__ == "__main__":
    unittest.main()
