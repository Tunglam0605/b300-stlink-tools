from __future__ import annotations

import io
import json
import unittest

from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_session import RemoteSession, RemoteSessionError
from b300_version import __version__

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


class FailingGatewayClient(FakeClient):
    def exec_command(self, command, timeout):
        raise OSError("exec channel failed")


class OversizedGatewayClient(FakeClient):
    def exec_command(self, command, timeout):
        return _Stream(b""), _Stream(b"x" * (256 * 1024 + 1)), _Stream(b"")


def snapshot(state, **changes):
    record = {
        "schema_version": 1,
        "protocol_version": 1,
        "capabilities": ["gateway-status", "gateway-ensure", "gateway-rescan"],
        "tool_version": __version__,
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
        self.assertEqual(len(client.commands), 2)
        self.assertTrue(client.commands[0][0].endswith(
            'exec "$b300_cli" debug gateway-status --json'
        ))
        self.assertTrue(client.commands[1][0].endswith(
            'exec "$b300_cli" debug gateway-ensure --json'
        ))
        rendered = " ".join(command for command, _timeout in client.commands).lower()
        self.assertNotIn("sudo", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("0.0.0.0", rendered)

    def test_gateway_command_discovers_managed_cli_without_login_shell_path(self) -> None:
        client = GatewayClient([snapshot("READY", generation=3)])
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
        )
        session.connect("secret")

        session.ensure_gateway_ready()

        command = client.commands[0][0]
        self.assertIn("B300_CLI_PATH", command)
        self.assertIn("command -v b300-stlink", command)
        self.assertIn('$HOME/.local/bin/b300-stlink', command)
        self.assertIn('$HOME/.local/share/b300-stlink/b300-stlink', command)
        self.assertNotIn("eval", command)

    def test_profile_cli_path_is_the_first_fixed_cli_candidate(self) -> None:
        profile = RemoteGatewayProfile(
            "gateway.local", "operator", 22, "/opt/b300/b300-stlink"
        )
        client = GatewayClient([snapshot("READY")])
        session = RemoteSession(profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")

        session.ensure_gateway_ready()

        command = client.commands[0][0]
        self.assertLess(command.index('/opt/b300/b300-stlink'), command.index("B300_CLI_PATH"))
        self.assertIn('b300_cli="/opt/b300/b300-stlink"', command)

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
        with self.assertRaisesRegex(RemoteSessionError, "NO_PROBE") as captured:
            session.ensure_gateway_ready()
        self.assertEqual(captured.exception.reason_code, "GATEWAY_NOT_READY")
        self.assertEqual(captured.exception.phase, "gateway_ready")
        self.assertTrue(captured.exception.retriable)
        self.assertEqual(session.state.forwards, ())

    def test_public_status_reads_gateway_without_starting_it(self) -> None:
        client = GatewayClient([snapshot("WAITING_PROBE", reason_code="NO_PROBE")])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        result = session.gateway_status(timeout_seconds=4.0)
        self.assertEqual((result.state, result.reason_code), ("WAITING_PROBE", "NO_PROBE"))
        self.assertEqual(len(client.commands), 1)
        self.assertTrue(client.commands[0][0].endswith(
            'exec "$b300_cli" debug gateway-status --json'
        ))
        self.assertEqual(client.commands[0][1], 4.0)

    def test_public_rescan_requests_fresh_hardware_discovery(self) -> None:
        client = GatewayClient([snapshot("READY", generation=2)])
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client)
        session.connect("secret")
        result = session.gateway_rescan(timeout_seconds=9.0)
        self.assertTrue(result.attach_ready)
        self.assertEqual(len(client.commands), 1)
        self.assertTrue(client.commands[0][0].endswith(
            'exec "$b300_cli" debug gateway-rescan --json'
        ))
        self.assertEqual(client.commands[0][1], 9.0)

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
        with self.assertRaisesRegex(RemoteSessionError, "update") as captured:
            session.ensure_gateway_ready()
        self.assertEqual(captured.exception.reason_code, "CLI_TOO_OLD")

    def test_older_remote_cli_version_is_rejected_with_safe_versions(self) -> None:
        client = GatewayClient([snapshot("READY", tool_version="0.21.9")])
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
        )
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.ensure_gateway_ready()
        self.assertEqual(captured.exception.reason_code, "CLI_TOO_OLD")
        self.assertIn("0.21.9", str(captured.exception))

    def test_incompatible_gateway_protocol_is_rejected_before_attach(self) -> None:
        client = GatewayClient([snapshot("READY", protocol_version=99)])
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: client,
        )
        session.connect("secret")

        with self.assertRaisesRegex(RemoteSessionError, "protocol") as captured:
            session.ensure_gateway_ready()
        self.assertEqual(captured.exception.reason_code, "PROTOCOL_MISMATCH")
        self.assertEqual(session.state.forwards, ())

    def test_gateway_protocol_requires_a_json_integer(self) -> None:
        for malformed in (True, 1.0, 1.5, "1"):
            with self.subTest(protocol_version=malformed):
                client = GatewayClient([snapshot("READY", protocol_version=malformed)])
                session = RemoteSession(
                    self.profile, credential_store=MemoryStore(),
                    ssh_client_factory=lambda: client,
                )
                session.connect("secret")
                with self.assertRaisesRegex(RemoteSessionError, "protocol"):
                    session.ensure_gateway_ready()

    def test_remote_failures_expose_canonical_error_details(self) -> None:
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: FakeClient(fail=True))
        with self.assertRaises(RemoteSessionError) as captured:
            session.connect("secret")
        error = captured.exception
        self.assertEqual(error.reason_code, "AUTH_FAILED")
        self.assertEqual(error.phase, "ssh_auth")
        self.assertTrue(error.next_action)
        self.assertFalse(error.retriable)

        missing = RawGatewayClient("b300-stlink: command not found", 127)
        session = RemoteSession(self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: missing)
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.ensure_gateway_ready()
        self.assertEqual(captured.exception.reason_code, "CLI_NOT_FOUND")

    def test_cli_execution_failure_is_not_mislabeled_as_ssh_connect(self) -> None:
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=FailingGatewayClient,
        )
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.gateway_status()
        self.assertEqual((captured.exception.reason_code, captured.exception.phase),
                         ("CLI_EXECUTION_FAILED", "gateway_cli"))

    def test_oversized_cli_response_has_gateway_response_error_details(self) -> None:
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=OversizedGatewayClient,
        )
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.gateway_status()
        self.assertEqual((captured.exception.reason_code, captured.exception.phase),
                         ("CLI_RESPONSE_TOO_LARGE", "gateway_cli"))
        self.assertFalse(captured.exception.retriable)

    def test_cli_without_a_snapshot_has_gateway_response_error_details(self) -> None:
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(),
            ssh_client_factory=lambda: RawGatewayClient("unexpected output", 1),
        )
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.gateway_status()
        self.assertEqual((captured.exception.reason_code, captured.exception.phase),
                         ("CLI_RESPONSE_INVALID", "gateway_cli"))

    def test_invalid_gateway_snapshot_has_protocol_error_details(self) -> None:
        record = {"state": "READY", "protocol_version": 1,
                  "capabilities": ["gateway-status"], "tool_version": __version__}
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(), ssh_client_factory=lambda: GatewayClient([record]),
        )
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.gateway_status()
        self.assertEqual((captured.exception.reason_code, captured.exception.phase),
                         ("GATEWAY_RESPONSE_INVALID", "gateway_protocol"))

    def test_ready_snapshot_with_nonzero_exit_has_command_error_details(self) -> None:
        session = RemoteSession(
            self.profile, credential_store=MemoryStore(),
            ssh_client_factory=lambda: GatewayClient([snapshot("READY", exit_status=3)]),
        )
        session.connect("secret")
        with self.assertRaises(RemoteSessionError) as captured:
            session.gateway_status()
        self.assertEqual((captured.exception.reason_code, captured.exception.phase),
                         ("GATEWAY_COMMAND_FAILED", "gateway_cli"))


if __name__ == "__main__":
    unittest.main()
