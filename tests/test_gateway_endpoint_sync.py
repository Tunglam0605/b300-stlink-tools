from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from b300_core.remote_session import RemoteForward, RemoteForwardError
from b300_core.gateway_status import GatewaySnapshot
from b300_core.vscode_bridge import (
    BridgeState,
    GatewayEndpointBinding,
    VsCodeDebugBridge,
    VsCodeExternalProfile,
)


@dataclass(frozen=True)
class Snapshot:
    state: str
    instance_id: str
    generation: int
    gdb_endpoint: str | None


@dataclass(frozen=True)
class SessionState:
    forwards: tuple[str, ...]
    generation: int


class EndpointSession:
    def __init__(self, ports=(45100, 45101)) -> None:
        self.connected = True
        self._ports = iter(ports)
        self._forward = None
        self._generation = 7
        self.opened = []
        self.closed = []
        self.listener_checks = []
        self.reject_local_port = None

    @property
    def state(self):
        names = ("vscode_gdb",) if self._forward is not None else ()
        return SessionState(names, self._generation)

    def require_remote_listener(self, *, remote_port, timeout_seconds=3.0):
        self.listener_checks.append(remote_port)
        return None

    def open_forward(self, name, *, remote_port, local_port=0,
                     remote_host="127.0.0.1", local_host="127.0.0.1"):
        self.opened.append((name, remote_port, local_port))
        if local_port and local_port == self.reject_local_port:
            raise RemoteForwardError("preferred port occupied")
        selected = int(local_port) or next(self._ports)
        self._forward = RemoteForward(name, local_host, selected, remote_host, remote_port)
        return self._forward

    def close_forward(self, name):
        self.closed.append(name)
        self._forward = None
        return True


def ready(*, instance="gw-a", generation=1, port=3333):
    return GatewaySnapshot.from_record({
        "schema_version": 1,
        "instance_id": instance,
        "generation": generation,
        "sequence": 1,
        "state": "READY",
        "reason_code": "TARGET_VERIFIED",
        "selected_probe": {"serial": "SAFE123", "usb_identity": "usb:1"},
        "gdb_endpoint": f"127.0.0.1:{port}",
        "tcl_endpoint": "127.0.0.1:6666",
        "cpu_state": "running",
        "evidence_age_ms": 0,
    })


class GatewayEndpointSyncTests(unittest.TestCase):
    def test_ready_snapshot_does_not_probe_gdb_with_a_phantom_connection(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()

        bridge.start_client(session, snapshot=ready(), profile_id="lab")

        self.assertEqual(session.listener_checks, [])
        self.assertEqual(session.opened, [("vscode_gdb", 3333, 0)])

    def test_partial_ready_object_cannot_bypass_snapshot_validation(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()

        with self.assertRaisesRegex(RuntimeError, "validated Gateway snapshot"):
            bridge.start_client(
                session,
                snapshot=Snapshot("READY", "gw-a", 1, "127.0.0.1:3333"),
                profile_id="lab",
            )

        self.assertEqual(session.listener_checks, [])
        self.assertEqual(session.opened, [])

    def test_direct_invalid_gateway_snapshot_cannot_bypass_canonical_validation(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()
        invalid = GatewaySnapshot(
            schema_version=1,
            instance_id="gw-a",
            generation=1,
            sequence=1,
            state="READY",
            reason_code="TARGET_UNVERIFIED",
            selected_probe=None,
            gdb_endpoint="127.0.0.1:3333",
            tcl_endpoint=None,
            cpu_state="unknown",
            evidence_age_ms=None,
        )

        with self.assertRaisesRegex(ValueError, "selected probe"):
            bridge.start_client(session, snapshot=invalid, profile_id="lab")

        self.assertEqual(session.listener_checks, [])
        self.assertEqual(session.opened, [])

    def test_configuration_rejects_binding_that_does_not_match_live_endpoint(self):
        binding = GatewayEndpointBinding(
            "lab", "gw-a", 1, "127.0.0.1:3333", "127.0.0.1:45100", 7
        )
        profile = VsCodeExternalProfile(
            "B300", "app.elf", "127.0.0.1:45101", binding=binding
        )
        with self.assertRaisesRegex(ValueError, "binding"):
            profile.configuration()

    def test_gateway_must_be_ready_before_forward_is_opened(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()
        for snapshot in (
            Snapshot("STARTING", "gw-a", 1, None),
            Snapshot("FAILED", "gw-a", 1, "127.0.0.1:3333"),
        ):
            with self.subTest(state=snapshot.state), self.assertRaisesRegex(RuntimeError, "READY"):
                bridge.start_client(session, snapshot=snapshot, profile_id="lab")
        self.assertEqual(session.opened, [])
        self.assertEqual(bridge.state.state, BridgeState.STOPPED)

    def test_remote_port_change_replaces_upstream_and_keeps_local_port(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()
        first = bridge.start_client(session, snapshot=ready(port=3333), profile_id="lab")
        second = bridge.sync_client(session, snapshot=ready(generation=2, port=4333), profile_id="lab")
        self.assertEqual(first.gdb_target, "127.0.0.1:45100")
        self.assertEqual(second.gdb_target, first.gdb_target)
        self.assertEqual(session.closed, ["vscode_gdb"])
        self.assertEqual(session.opened[-1], ("vscode_gdb", 4333, 45100))
        self.assertEqual(second.binding.generation, 2)
        self.assertIn("attach again", second.detail.lower())

    def test_sync_rebinds_dynamic_port_when_previous_local_port_is_occupied(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()
        first = bridge.start_client(session, snapshot=ready(), profile_id="lab")
        session.reject_local_port = 45100
        second = bridge.sync_client(session, snapshot=ready(generation=2), profile_id="lab")
        self.assertNotEqual(second.gdb_target, first.gdb_target)
        self.assertEqual(session.opened[-2:], [
            ("vscode_gdb", 3333, 45100),
            ("vscode_gdb", 3333, 0),
        ])

    def test_new_gateway_generation_invalidates_old_binding_and_forward_loss_is_not_ready(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()
        bridge.start_client(session, snapshot=ready(), profile_id="lab")
        session._forward = None
        self.assertEqual(bridge.state.state, BridgeState.FAILED)
        rebound = bridge.sync_client(session, snapshot=ready(instance="gw-b", generation=1), profile_id="lab")
        self.assertEqual(rebound.state, BridgeState.READY)
        self.assertEqual(rebound.binding.instance_id, "gw-b")

    def test_profile_change_replaces_old_binding(self):
        session = EndpointSession()
        bridge = VsCodeDebugBridge()
        bridge.start_client(session, snapshot=ready(), profile_id="lab-a")
        rebound = bridge.sync_client(session, snapshot=ready(), profile_id="lab-b")
        self.assertEqual(session.closed, ["vscode_gdb"])
        self.assertEqual(rebound.binding.profile_id, "lab-b")


if __name__ == "__main__":
    unittest.main()
