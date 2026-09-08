import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal

from b300_core.gateway_status import GatewaySnapshot
from b300_core.remote_profile import RemoteGatewayProfile
from b300_gui.gateway_health_controller import GatewayHealthController


def snapshot(state="READY", *, generation=1, sequence=1, reason="TARGET_VERIFIED", gdb_port=3333):
    ready = state == "READY"
    return GatewaySnapshot.from_record({
        "schema_version": 1,
        "instance_id": "gw-a",
        "generation": generation,
        "sequence": sequence,
        "state": state,
        "reason_code": reason,
        "selected_probe": {"serial": "ABC"} if ready else None,
        "gdb_endpoint": "127.0.0.1:%d" % gdb_port if ready else None,
        "tcl_endpoint": "127.0.0.1:6666" if ready else None,
        "cpu_state": "running" if ready else "unknown",
        "evidence_age_ms": 0 if ready else None,
    })


class FakeManager:
    def __init__(self):
        self._session = object()

    def session(self, _profile):
        return self._session


class InlineWorker(QObject):
    completed = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def start(self):
        try:
            self.completed.emit(self.operation(lambda *_: None, lambda *_: None, None))
        except Exception as error:
            self.failed.emit(error)
        finally:
            self.finished.emit()

    def isRunning(self):
        return False

    def deleteLater(self):
        pass


class GatewayHealthControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.profile = RemoteGatewayProfile("ipc", "operator", 22)
        self.controller = GatewayHealthController(FakeManager(), worker_factory=None)
        self.controller.bind(self.profile)

    def test_non_ready_snapshot_warns_immediately_and_recovery_is_emitted(self):
        warnings, recovered, received = [], [], []
        self.controller.warning_changed.connect(warnings.append)
        self.controller.recovered.connect(recovered.append)
        self.controller.snapshot_changed.connect(received.append)

        self.controller.accept_snapshot(snapshot())
        self.controller.accept_snapshot(snapshot(
            "DISCONNECTED", generation=1, sequence=2, reason="PROBE_REMOVED"
        ))
        self.assertIn("ST-Link", warnings[-1])
        self.assertFalse(self.controller.attach_ready)

        fresh = snapshot(generation=2, sequence=3)
        self.controller.accept_snapshot(fresh)
        self.assertEqual(recovered, [fresh])
        self.assertEqual(warnings[-1], "")
        self.assertTrue(self.controller.attach_ready)
        self.assertEqual(received[-1], fresh)

    def test_three_transport_failures_mark_gateway_unreachable(self):
        warnings = []
        self.controller.warning_changed.connect(warnings.append)
        self.controller.accept_snapshot(snapshot())

        self.controller.accept_failure("timeout 1")
        self.controller.accept_failure("timeout 2")
        self.assertTrue(self.controller.attach_ready)
        self.controller.accept_failure("timeout 3")

        self.assertFalse(self.controller.attach_ready)
        self.assertIn("liên lạc Gateway", warnings[-1])

    def test_stale_or_reordered_snapshot_is_ignored(self):
        received = []
        self.controller.snapshot_changed.connect(received.append)
        newest = snapshot(sequence=4)
        self.assertTrue(self.controller.accept_snapshot(newest))
        self.assertFalse(self.controller.accept_snapshot(snapshot(sequence=3)))
        self.assertEqual(received, [newest])

    def test_ready_endpoint_change_requests_client_resynchronization(self):
        recovered = []
        self.controller.recovered.connect(recovered.append)
        self.controller.accept_snapshot(snapshot(sequence=1, gdb_port=3333))

        changed = snapshot(generation=2, sequence=2, gdb_port=4333)
        self.controller.accept_snapshot(changed)

        self.assertEqual(recovered, [changed])

    def test_activity_evidence_reaches_snapshot_observers_unchanged(self):
        received = []
        self.controller.snapshot_changed.connect(received.append)
        active = GatewaySnapshot.from_record({
            **snapshot().to_record(),
            "gdb_connection_count": 2,
            "gdb_activity_generation": 5,
            "gdb_ever_attached": True,
        })

        self.controller.accept_snapshot(active)

        self.assertEqual(received[-1].gdb_connection_count, 2)
        self.assertEqual(received[-1].gdb_activity_generation, 5)

    def test_gateway_snapshot_publishes_shared_device_evidence(self):
        from b300_gui.app_context import AppContext
        context = AppContext()
        controller = GatewayHealthController(FakeManager(), context=context, worker_factory=None)
        self.assertTrue(controller.accept_snapshot(snapshot(generation=2, sequence=7, gdb_port=4333)))
        state = context.device_snapshot
        self.assertEqual(state.gateway_instance_id, "gw-a")
        self.assertEqual(state.gateway_generation, 2)
        self.assertEqual(state.sequence, 7)

    def test_authenticated_lease_evidence_is_published_and_cleared_on_rebind(self):
        from dataclasses import replace
        from b300_gui.app_context import AppContext
        from b300_core.gateway_agent import GatewayAgentStatus
        from b300_core.gateway_lease import GatewayLeasePublicSnapshot
        context = AppContext()
        controller = GatewayHealthController(FakeManager(), context=context, worker_factory=None)
        agent = GatewayAgentStatus("agent-01", 1234, 1.0, "READY", "IDLE")
        lease = GatewayLeasePublicSnapshot.from_record({
            "active": True, "lease_id": "lease-1", "generation": 2,
            "client_label": "ENG-LAPTOP-02", "mode": "VSCODE_DEBUG",
            "state": "ACTIVE", "acquired_at": "2026-09-08T01:02:03Z",
            "heartbeat_age_seconds": 1, "gateway_instance_id": "gw-a",
            "gateway_generation": 3, "probe_serial": "ABC", "reason_code": "LEASE_ACTIVE",
        })
        controller.accept_snapshot(replace(snapshot(), agent_status=agent, lease_snapshot=lease))
        self.assertEqual(context.gateway_agent_snapshot, agent)
        self.assertEqual(context.gateway_lease_snapshot, lease)
        controller.bind(RemoteGatewayProfile("new-ipc", "operator", 22))
        self.assertIsNone(context.gateway_agent_snapshot)
        self.assertIsNone(context.gateway_lease_snapshot)

    def test_control_response_publishes_agent_and_lease_evidence_end_to_end(self):
        from b300_gui.app_context import AppContext
        from b300_core.gateway_agent import GatewayAgentStatus
        from b300_core.gateway_lease import GatewayLeasePublicSnapshot
        context = AppContext()
        session = SimpleNamespace()
        session.gateway_status = lambda timeout_seconds: snapshot(sequence=2)
        session._run_gateway_control = lambda command, timeout_seconds: {
            **GatewayAgentStatus("agent-01", 1234, 1.0, "READY", "IDLE").to_record(),
            "status": "ok", "protocol_version": 1, "tool_version": "1.0.0",
            "capabilities": ["gateway-exclusive-lease-v1"],
            "lease_snapshot": GatewayLeasePublicSnapshot.from_record({
                "active": True, "lease_id": "lease-1", "generation": 2,
                "client_label": "ENG-LAPTOP-02", "mode": "VSCODE_DEBUG",
                "state": "ACTIVE", "acquired_at": "2026-09-08T01:02:03Z",
                "heartbeat_age_seconds": 1, "gateway_instance_id": "gw-a",
                "gateway_generation": 3, "probe_serial": "ABC", "reason_code": "LEASE_ACTIVE",
            }).to_record(),
        }
        manager = type("Manager", (), {"session": lambda self, profile: session})()
        controller = GatewayHealthController(manager, context=context, worker_factory=InlineWorker)
        controller.bind(self.profile)
        controller.poll_now()
        self.assertEqual(context.gateway_agent_snapshot.instance_id, "agent-01")
        self.assertEqual(context.gateway_lease_snapshot.lease_id, "lease-1")

    def test_malformed_control_response_clears_stale_evidence(self):
        from dataclasses import replace
        from b300_gui.app_context import AppContext
        from b300_core.gateway_agent import GatewayAgentStatus
        context = AppContext()
        session = SimpleNamespace()
        session.gateway_status = lambda timeout_seconds: snapshot(sequence=2)
        session._run_gateway_control = lambda command, timeout_seconds: {"state": "READY"}
        manager = type("Manager", (), {"session": lambda self, profile: session})()
        controller = GatewayHealthController(manager, context=context, worker_factory=InlineWorker)
        controller.bind(self.profile)
        controller.accept_snapshot(replace(snapshot(sequence=1), agent_status=GatewayAgentStatus("agent", 1, 1.0, "READY", "IDLE")))
        controller.poll_now()
        self.assertIsNone(context.gateway_agent_snapshot)
        self.assertIsNone(context.gateway_lease_snapshot)

    def test_late_worker_completion_is_rejected_after_rebind(self):
        old_token = self.controller._bind_token
        self.controller.bind(RemoteGatewayProfile("new-ipc", "operator", 22))
        self.controller._poll_completed(snapshot(), old_token)
        self.assertIsNone(self.controller.snapshot)

    def test_probe_removed_clears_gateway_snapshot_and_legacy_probe_list(self):
        from b300_gui.app_context import AppContext
        from b300_core.gateway_profiles import GatewayProfile
        context = AppContext()
        gateway = GatewayProfile.create("Lab", "lab.example", "operator", profile_id="lab")
        context.set_profiles((), (gateway,), default_gateway_id="lab")
        controller = GatewayHealthController(FakeManager(), context=context, worker_factory=None)
        self.assertTrue(controller.accept_snapshot(snapshot(sequence=1)))
        self.assertEqual(context.selected_probe, "ABC")
        self.assertEqual(tuple(item.serial for item in context.probes), ("ABC",))
        self.assertTrue(controller.accept_snapshot(snapshot(
            "DISCONNECTED", sequence=2, reason="PROBE_REMOVED",
        )))
        self.assertIsNone(context.device_snapshot.probe_serial)
        self.assertEqual(context.probes, ())
        self.assertIsNone(context.selected_probe)


if __name__ == "__main__":
    unittest.main()
