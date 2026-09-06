import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from b300_core.gateway_profiles import GatewayProfile
from b300_core.gateway_status import GatewaySnapshot
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


if __name__ == "__main__":
    unittest.main()
