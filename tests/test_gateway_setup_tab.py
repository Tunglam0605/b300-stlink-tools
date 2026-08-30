from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from b300_core.gateway_setup import GatewayHostCheck, GatewayHostReport
from b300_core.ssh_identity import (
    AuthorizedKeyResult, SshClientPrepareResult, SshClientPrerequisiteReport, SshIdentityReport,
)
from b300_core.ssh_host_trust import GatewayHostKey, HostTrustResult
from tests.test_ssh_identity import key_line
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


def identity_report(ready=False):
    private = Path("C:/Users/test/.ssh/b300_gateway_ed25519")
    return SshIdentityReport(
        private_key=private, public_key=Path(str(private) + ".pub"),
        keygen_available=True, pair_exists=ready,
        public_key_text=key_line(51) if ready else None,
        fingerprint="SHA256:CLIENT" if ready else None, ready=ready,
    )


def prereq_report(ready=True):
    return SshClientPrerequisiteReport(
        "windows", Path("C:/OpenSSH/ssh.exe") if ready else None,
        Path("C:/OpenSSH/ssh-keygen.exe") if ready else None,
        ready, ready, () if ready else ("install_openssh_client",), not ready,
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
        tab = GatewaySetupTab(identity_inspector=lambda: identity_report(False), auto_refresh=False)
        tab._render(report(True))
        self.assertIn("GATEWAY SSH READY", tab.status.text())
        self.assertEqual(tab.check_table.rowCount(), 2)
        self.assertIn("192.168.1.109", tab.client_config.toPlainText())
        self.assertTrue(tab.copy_button.isEnabled())
        tab.close()

    def test_lazy_refresh_does_not_inspect_until_requested(self):
        inspector = mock.Mock(return_value=report(True))
        tab = GatewaySetupTab(inspector=inspector, identity_inspector=lambda: identity_report(False), auto_refresh=False)
        inspector.assert_not_called()
        tab.refresh_host()
        self.wait_until(lambda: not tab.has_active_operation)
        inspector.assert_called_once_with(ssh_port=22)
        self.assertTrue(tab._report.ready)
        tab.close()

    def test_debug_exposure_blocks_prepare_without_calling_preparer(self):
        unsafe = report(False, private=False)
        preparer = mock.Mock()
        tab = GatewaySetupTab(preparer=preparer, identity_inspector=lambda: identity_report(False), auto_refresh=False)
        tab._render(unsafe)
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.critical") as dialog:
            tab.prepare_host()
        dialog.assert_called_once()
        preparer.assert_not_called()
        tab.close()

    def test_show_local_gateway_host_fingerprint_exposes_only_fingerprint(self):
        host_key = GatewayHostKey(
            "localhost", 22, "localhost", key_line(91), "SHA256:GATEWAYHOST"
        )
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            host_key_reader=lambda: host_key, auto_refresh=False,
        )
        tab.show_local_host_key()
        self.wait_until(lambda: not tab.has_active_operation)
        self.assertIn("SHA256:GATEWAYHOST", tab.host_key_status.text())
        self.assertTrue(tab.copy_host_fingerprint_button.isEnabled())
        self.assertNotIn("PRIVATE", tab.host_key_status.text())
        tab.close()

    def test_remote_host_trust_requires_exact_fingerprint_before_write(self):
        scanned = GatewayHostKey(
            "gateway.local", 22, "gateway.local", key_line(92), "SHA256:SCANNED"
        )
        truster = mock.Mock()
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            host_key_scanner=lambda host, port: scanned, host_truster=truster,
            auto_refresh=False,
        )
        with self.assertRaisesRegex(RuntimeError, "HOST_KEY_FINGERPRINT_MISMATCH"):
            tab._scan_verify_trust_host("gateway.local", 22, "SHA256:EXPECTED")
        truster.assert_not_called()
        tab.close()

    def test_remote_host_trust_matching_fingerprint_calls_truster(self):
        scanned = GatewayHostKey(
            "gateway.local", 22, "gateway.local", key_line(93), "SHA256:MATCH"
        )
        trusted = HostTrustResult(
            Path("C:/Users/test/.ssh/b300_known_hosts"), "gateway.local",
            "SHA256:MATCH", True,
        )
        truster = mock.Mock(return_value=trusted)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            host_key_scanner=lambda host, port: scanned, host_truster=truster,
            auto_refresh=False,
        )
        result = tab._scan_verify_trust_host("gateway.local", 22, "SHA256:MATCH")
        self.assertEqual(result, trusted)
        truster.assert_called_once_with(scanned)
        tab.close()

    def test_selftest_uses_host_and_full_gateway_doctor(self):
        host = report(True)
        full = SimpleNamespace(ready=True, conclusion="READY")
        inspector = mock.Mock(return_value=host)
        full_inspector = mock.Mock(return_value=full)
        tab = GatewaySetupTab(
            inspector=inspector, full_inspector=full_inspector, identity_inspector=lambda: identity_report(False), auto_refresh=False
        )
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab.run_selftest()
            self.wait_until(lambda: not tab.has_active_operation)
        full_inspector.assert_called_once_with(ssh_port=22, gdb_port=3333, tcl_port=6666)
        self.assertIn("GATEWAY SSH READY", tab.status.text())
        tab.close()


    def test_generate_client_identity_updates_public_only_status(self):
        ready = identity_report(True)
        ensurer = mock.Mock(return_value=ready)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), identity_ensurer=ensurer,
            client_prereq_inspector=lambda: prereq_report(True), auto_refresh=False,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab.prepare_client_identity()
            self.wait_until(lambda: not tab.has_active_operation)
        ensurer.assert_called_once_with()
        self.assertTrue(tab._identity.ready)
        self.assertIn("SHA256:CLIENT", tab.identity_status.text())
        self.assertTrue(tab.identity_copy_button.isEnabled())
        self.assertNotIn("PRIVATE", tab.identity_status.text())
        tab.close()

    def test_generate_client_identity_can_prepare_missing_openssh_client_after_confirmation(self):
        missing = prereq_report(False)
        ready_prereq = prereq_report(True)
        prepared = SshClientPrepareResult(missing, ready_prereq, True, True)
        identity = identity_report(True)
        prereq_preparer = mock.Mock(return_value=prepared)
        ensurer = mock.Mock(return_value=identity)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), identity_ensurer=ensurer,
            client_prereq_inspector=lambda: missing, client_prereq_preparer=prereq_preparer,
            auto_refresh=False,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab.prepare_client_identity()
            self.wait_until(lambda: not tab.has_active_operation)
        prereq_preparer.assert_called_once_with()
        ensurer.assert_called_once_with()
        self.assertTrue(tab._identity.ready)
        tab.close()

    def test_authorize_client_key_validates_then_calls_public_key_authorizer(self):
        public = key_line(61)
        result = AuthorizedKeyResult(Path("C:/ProgramData/ssh/administrators_authorized_keys"), "SHA256:GATEWAY", True, True)
        authorizer = mock.Mock(return_value=result)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), key_authorizer=authorizer,
            auto_refresh=False,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QInputDialog.getMultiLineText", return_value=(public, True)), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab.authorize_client_key()
            self.wait_until(lambda: not tab.has_active_operation)
        authorizer.assert_called_once_with(public)
        tab.close()

    def test_invalid_public_key_never_reaches_authorizer(self):
        authorizer = mock.Mock()
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), key_authorizer=authorizer,
            auto_refresh=False,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QInputDialog.getMultiLineText", return_value=("not-a-key", True)), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.critical") as dialog:
            tab.authorize_client_key()
        dialog.assert_called_once()
        authorizer.assert_not_called()
        tab.close()


if __name__ == "__main__":
    unittest.main()
