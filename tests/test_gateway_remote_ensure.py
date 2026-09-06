from __future__ import annotations

import io
import json
import unittest

from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_session import RemoteSession, RemoteSessionError

from tests.test_remote_session import FakeClient, MemoryStore


class _Stream(io.BytesIO):
    def __init__(self, payload, exit_status=0):
        super().__init__(payload)
        self.channel = type("Channel", (), {"recv_exit_status": lambda _self: exit_status})()


class GatewayClient(FakeClient):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.commands = []

    def exec_command(self, command, timeout):
        self.commands.append((command, timeout))
        response = self.responses.pop(0)
        stdout = _Stream((json.dumps(response) + "\n").encode("utf-8"), response.get("exit_status", 0))
        return _Stream(b""), stdout, _Stream(b"")


class RawGatewayClient(FakeClient):
    def __init__(self, stderr, exit_status):
        super().__init__()
        self.stderr = stderr
        self.exit_status = exit_status

    def exec_command(self, command, timeout):
        return (
            _Stream(b""), _Stream(b"", self.exit_status),
            _Stream(self.stderr.encode("utf-8")),
        )


def snapshot(state, **changes):
    record = {
        "schema_version": 1,
        "instance_id": "gateway-a",
        "generation": 0,
        "sequence": 1,
        "state": state,
        "reason_code": "USER_STOPPED" if state == "STOPPED" else "TARGET_VERIFIED",
        "selected_probe": None if state == "STOPPED" else {"serial": "SAFE123", "usb_identity": "usb:1"},
        "gdb_endpoint": None if state == "STOPPED" else "127.0.0.1:3333",
        "tcl_endpoint": None if state == "STOPPED" else "127.0.0.1:6666",
        "cpu_state": "unknown" if state == "STOPPED" else "running",
        "evidence_age_ms": None if state == "STOPPED" else 0,
    }
    record.update(changes)
    return record


class GatewayRemoteEnsureTests(unittest.TestCase):
    def setUp(self):
        self.profile = RemoteGatewayProfile("gateway.local", "operator", 22)

    def test_stopped_gateway_is_started_by_verified_cli_before_ready_is_returned(self) -> None:
        client = GatewayClient([snapshot("STOPPED"), snapshot("READY", generation=1)])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        result = session.ensure_gateway_ready()
        self.assertEqual(result.state, "READY")
        self.assertEqual([item[0] for item in client.commands], [
            'env PATH="$HOME/.local/bin:$PATH" b300-stlink debug gateway-status --json',
            'env PATH="$HOME/.local/bin:$PATH" b300-stlink debug gateway-ensure --json',
        ])
        rendered = " ".join(command for command, _timeout in client.commands).lower()
        self.assertNotIn("sudo", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("0.0.0.0", rendered)

    def test_ready_gateway_does_not_start_a_second_owner(self) -> None:
        client = GatewayClient([snapshot("READY", generation=7)])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        result = session.ensure_gateway_ready()
        self.assertEqual(result.generation, 7)
        self.assertEqual(len(client.commands), 1)

    def test_gateway_start_failure_reports_remote_reason_without_opening_a_forward(self) -> None:
        blocked = snapshot("WAITING_PROBE", reason_code="NO_PROBE")
        client = GatewayClient([snapshot("STOPPED"), blocked])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        with self.assertRaisesRegex(RemoteSessionError, "NO_PROBE"):
            session.ensure_gateway_ready()
        self.assertEqual(session.state.forwards, ())

    def test_public_status_reads_gateway_without_starting_it(self) -> None:
        client = GatewayClient([snapshot("WAITING_PROBE", reason_code="NO_PROBE")])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        result = session.gateway_status(timeout_seconds=4.0)
        self.assertEqual((result.state, result.reason_code), ("WAITING_PROBE", "NO_PROBE"))
        self.assertEqual(client.commands, [(
            'env PATH="$HOME/.local/bin:$PATH" b300-stlink debug gateway-status --json', 4.0
        )])

    def test_public_rescan_requests_fresh_hardware_discovery(self) -> None:
        client = GatewayClient([snapshot("READY", generation=2)])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        result = session.gateway_rescan(timeout_seconds=9.0)
        self.assertTrue(result.attach_ready)
        self.assertEqual(client.commands, [(
            'env PATH="$HOME/.local/bin:$PATH" b300-stlink debug gateway-rescan --json', 9.0
        )])

    def test_missing_remote_cli_reports_install_action(self) -> None:
        client = RawGatewayClient("b300-stlink: command not found", 127)
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
        )
        session.connect("secret")
        with self.assertRaisesRegex(RemoteSessionError, "not installed"):
            session.ensure_gateway_ready()

    def test_old_remote_cli_reports_update_action(self) -> None:
        client = RawGatewayClient("invalid choice: 'gateway-status'", 2)
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
        )
        session.connect("secret")
        with self.assertRaisesRegex(RemoteSessionError, "update"):
            session.ensure_gateway_ready()


if __name__ == "__main__":
    unittest.main()
