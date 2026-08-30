from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.gateway_setup import GatewayHostCheck, GatewayHostReport
from b300_gui.gateway_setup_tab import GatewaySetupTab


def report(ready=True, private=True):
    checks = (
        GatewayHostCheck("ssh_install", "PASS" if ready else "FAIL", "SSH", "OpenSSH status"),
        GatewayHostCheck("debug_ports", "PASS" if private else "FAIL", "DEBUG", "Debug ports status"),
    )
    return GatewayHostReport(
        platform="windows", checks=checks, ssh_installed=ready,
        ssh_service_running=ready, ssh_startup_enabled=ready,
        ssh_firewall_ready=ready, ssh_port_listening=ready,
        debug_ports_private=private, ready=ready and private,
        conclusion="READY" if ready and private else "BLOCKED", ssh_port=22,
        username="automation", hostname="gateway", ipv4_addresses=("192.168.1.109",),
    )


class GatewaySetupTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("condition not reached")

    def test_render_ready_host_and_client_configuration(self):
        tab = GatewaySetupTab(auto_refresh=False)
        tab._render(report(True))
        self.assertIn("GATEWAY SSH READY", tab.status.text())
        self.assertEqual(tab.check_table.rowCount(), 2)
        self.assertIn("192.168.1.109", tab.client_config.toPlainText())
        self.assertTrue(tab.copy_button.isEnabled())
        tab.close()

    def test_lazy_refresh_does_not_inspect_until_requested(self):
        inspector = mock.Mock(return_value=report(True))
        tab = GatewaySetupTab(inspector=inspector, auto_refresh=False)
        inspector.assert_not_called()
        tab.refresh_host()
        self.wait_until(lambda: not tab.has_active_operation)
        inspector.assert_called_once_with(ssh_port=22)
        self.assertTrue(tab._report.ready)
        tab.close()

    def test_debug_exposure_blocks_prepare_without_calling_preparer(self):
        unsafe = report(False, private=False)
        preparer = mock.Mock()
        tab = GatewaySetupTab(preparer=preparer, auto_refresh=False)
        tab._render(unsafe)
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.critical") as dialog:
            tab.prepare_host()
        dialog.assert_called_once()
        preparer.assert_not_called()
        tab.close()

    def test_selftest_uses_host_and_full_gateway_doctor(self):
        host = report(True)
        full = SimpleNamespace(ready=True, conclusion="READY")
        inspector = mock.Mock(return_value=host)
        full_inspector = mock.Mock(return_value=full)
        tab = GatewaySetupTab(
            inspector=inspector, full_inspector=full_inspector, auto_refresh=False
        )
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab.run_selftest()
            self.wait_until(lambda: not tab.has_active_operation)
        full_inspector.assert_called_once_with(ssh_port=22, gdb_port=3333, tcl_port=6666)
        self.assertIn("GATEWAY SSH READY", tab.status.text())
        tab.close()


if __name__ == "__main__":
    unittest.main()
