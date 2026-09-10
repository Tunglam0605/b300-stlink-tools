import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from b300_core.gateway_profiles import GatewayProfile
from b300_core.gateway_status import GatewaySnapshot
from b300_core.gateway_agent import GatewayAgentStatus
from b300_core.gateway_lease import GatewayLeasePublicSnapshot
from b300_gui.app_context import AppContext
from b300_gui.widgets.shared_context_bar import SharedContextBar


def disconnected():
    return GatewaySnapshot.from_record({
        "schema_version": 1,
        "instance_id": "gw",
        "generation": 2,
        "sequence": 9,
        "state": "DISCONNECTED",
        "reason_code": "PROBE_REMOVED",
        "selected_probe": None,
        "gdb_endpoint": None,
        "tcl_endpoint": None,
        "cpu_state": "unknown",
        "evidence_age_ms": None,
    })


def stopped():
    return GatewaySnapshot.from_record({
        "schema_version": 1, "instance_id": "gw", "generation": 2,
        "sequence": 10, "state": "STOPPED", "reason_code": "USER_STOPPED",
        "selected_probe": None, "gdb_endpoint": None, "tcl_endpoint": None,
        "cpu_state": "unknown", "evidence_age_ms": None,
    })


def lease(*, active=False, state="IDLE", reason="GATEWAY_IDLE"):
    return GatewayLeasePublicSnapshot.from_record({
        "active": active,
        "lease_id": "lease-1" if active else "",
        "generation": 1 if active else 0,
        "client_label": "ENG-LAPTOP-02" if active else "",
        "mode": "VSCODE_DEBUG" if active else "",
        "state": state,
        "acquired_at": "2026-09-08T01:02:03Z" if active else "",
        "heartbeat_age_seconds": 1 if active else 0,
        "gateway_instance_id": "gw" if active else "",
        "gateway_generation": 2 if active else 0,
        "probe_serial": "ABC" if active else None,
        "reason_code": reason,
    })


class GatewayHealthUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_remote_status_uses_gateway_evidence_and_exposes_rescan(self):
        context = AppContext()
        gateway = GatewayProfile.create("IPC", "ipc", "operator", 22, profile_id="ipc")
        context.set_profiles((), (gateway,), default_gateway_id="ipc")
        bar = SharedContextBar(context)
        self.addCleanup(bar.close)

        context.set_gateway_health(disconnected(), "Mất kết nối ST-Link trên Gateway.")

        self.assertIn("Mất kết nối ST-Link", bar.connection_status.text())
        self.assertEqual(bar.connection_status.property("state"), "failure")
        self.assertTrue(bar.refresh_probes_button.isEnabled())
        self.assertIn("Gateway", bar.refresh_probes_button.toolTip())

    def test_switching_connection_clears_previous_gateway_evidence(self):
        context = AppContext()
        gateway = GatewayProfile.create("IPC", "ipc", "operator", 22, profile_id="ipc")
        context.set_profiles((), (gateway,), default_gateway_id="ipc")
        context.set_gateway_health(disconnected(), "Mất kết nối ST-Link trên Gateway.")
        context.select_connection("local")
        self.assertIsNone(context.gateway_snapshot)
        self.assertEqual(context.gateway_warning, "")

    def test_remote_probe_picker_names_gateway_as_its_source(self):
        context = AppContext()
        gateway = GatewayProfile.create("PC", "192.168.1.158", "aubot", 22, profile_id="pc")
        context.set_profiles((), (gateway,), default_gateway_id="pc")
        context.set_gateway_health(None, "Gateway CLI cần được cập nhật")
        bar = SharedContextBar(context)
        self.addCleanup(bar.close)

        self.assertIn("Gateway", bar.probe_combo.currentText())
        self.assertNotEqual(bar.probe_combo.currentText(), "Chưa phát hiện ST-Link")

    def test_stopped_legacy_gateway_shows_idle_agent_reason(self):
        context = AppContext()
        gateway = GatewayProfile.create("IPC", "ipc", "operator", 22, profile_id="ipc")
        context.set_profiles((), (gateway,), default_gateway_id="ipc")
        context.set_gateway_health(stopped(), "Gateway chưa sẵn sàng: USER_STOPPED.")
        context.set_gateway_agent_status(GatewayAgentStatus("agent-1", 123, 1.0, "READY", "IDLE"))
        context.set_gateway_lease_snapshot(lease())
        bar = SharedContextBar(context)
        self.addCleanup(bar.close)

        self.assertIn("IDLE", bar.connection_status.text())
        self.assertIn("GATEWAY_IDLE", bar.connection_status.text())
        self.assertEqual(bar.connection_status.property("state"), "failure")

    def test_stopped_legacy_gateway_shows_busy_and_recovery_lease_states(self):
        context = AppContext()
        gateway = GatewayProfile.create("IPC", "ipc", "operator", 22, profile_id="ipc")
        context.set_profiles((), (gateway,), default_gateway_id="ipc")
        context.set_gateway_health(stopped(), "Gateway chưa sẵn sàng: USER_STOPPED.")
        context.set_gateway_agent_status(GatewayAgentStatus("agent-1", 123, 1.0, "READY", "IDLE"))
        bar = SharedContextBar(context)
        self.addCleanup(bar.close)

        context.set_gateway_lease_snapshot(lease(active=True, state="ACTIVE", reason="LEASE_ACTIVE"))
        self.assertIn("BUSY", bar.connection_status.text())
        self.assertIn("LEASE_ACTIVE", bar.connection_status.text())
        context.set_gateway_lease_snapshot(lease(active=True, state="RECOVERY_REQUIRED", reason="RECOVERY_OWNER_UNPROVEN"))
        self.assertIn("RECOVERY_REQUIRED", bar.connection_status.text())
        self.assertIn("RECOVERY_OWNER_UNPROVEN", bar.connection_status.text())


if __name__ == "__main__":
    unittest.main()
