import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

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


if __name__ == "__main__":
    unittest.main()
