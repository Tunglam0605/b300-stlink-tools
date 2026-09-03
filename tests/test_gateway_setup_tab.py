from __future__ import annotations

import os
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from b300_core.gateway_setup import GatewayHostCheck, GatewayHostReport
from b300_core.ssh_identity import (
    AuthorizedKeyResult, SshClientPrepareResult, SshClientPrerequisiteReport, SshIdentityReport,
)
from b300_core.ssh_host_trust import GatewayHostKey, HostTrustResult
from b300_core.gateway_network import GatewayEndpointProbe
from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_connectivity import RemoteConnectivityResult
from tests.test_ssh_identity import key_line
from b300_gui.gateway_setup_tab import GatewaySetupTab
from b300_gui.styles import APP_STYLE


def report(ready=True, private=True, port=22, network_profile=True, network_profile_state=None):
    checks = (
        GatewayHostCheck("ssh_install", "PASS" if ready else "FAIL", "SSH", "OpenSSH status"),
        GatewayHostCheck("network_profile", "PASS" if network_profile else "FAIL", "NETWORK", "Network profile status"),
        GatewayHostCheck("debug_ports", "PASS" if private else "FAIL", "DEBUG", "Debug ports status"),
    )
    return GatewayHostReport(
        platform="windows", checks=checks, ssh_installed=ready,
        ssh_service_running=ready, ssh_startup_enabled=ready,
        ssh_firewall_ready=ready, ssh_port_listening=ready,
        debug_ports_private=private, ready=ready and private and network_profile,
        conclusion="READY" if ready and private and network_profile else "BLOCKED", ssh_port=port,
        username="automation", hostname="gateway", ipv4_addresses=("192.168.1.109",),
        ssh_network_profile_ready=network_profile,
        ssh_network_profile_state=network_profile_state or ("READY" if network_profile else "PUBLIC"),
    )


def reachable_gateway_endpoint(host, port):
    return GatewayEndpointProbe(
        host, port, True, "SSH_TCP_REACHABLE",
        "TCP connection to %s:%d is reachable." % (host, port),
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
        tab = GatewaySetupTab(identity_inspector=lambda: identity_report(False), profile_loader=lambda: None, auto_refresh=False)
        tab._render(report(True))
        self.assertIn("GATEWAY SSH READY", tab.status.text())
        self.assertEqual(tab.check_table.rowCount(), 3)
        self.assertIn("192.168.1.109", tab.client_config.toPlainText())
        self.assertTrue(tab.copy_button.isEnabled())
        self.assertFalse(tab.gateway_check_details.is_expanded())
        self.assertFalse(tab.gateway_connection_details.is_expanded())
        self.assertFalse(tab.gateway_safety_details.is_expanded())
        tab.close()

    def test_lazy_refresh_does_not_inspect_until_requested(self):
        inspector = mock.Mock(return_value=report(True))
        tab = GatewaySetupTab(inspector=inspector, identity_inspector=lambda: identity_report(False), profile_loader=lambda: None, auto_refresh=False)
        inspector.assert_not_called()
        tab.refresh_host()
        self.wait_until(lambda: not tab.has_active_operation)
        inspector.assert_called_once_with(ssh_port=22)
        self.assertTrue(tab._report.ready)
        tab.close()

    def test_debug_exposure_blocks_prepare_without_calling_preparer(self):
        unsafe = report(False, private=False)
        preparer = mock.Mock()
        tab = GatewaySetupTab(preparer=preparer, identity_inspector=lambda: identity_report(False), profile_loader=lambda: None, auto_refresh=False)
        tab._render(unsafe)
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.critical") as dialog:
            tab.prepare_host()
        dialog.assert_called_once()
        preparer.assert_not_called()
        tab.close()

    def test_ambiguous_network_profile_blocks_prepare_with_guidance(self):
        blocked = report(ready=False, network_profile=False, network_profile_state="AMBIGUOUS")
        preparer = mock.Mock()
        tab = GatewaySetupTab(
            preparer=preparer, identity_inspector=lambda: identity_report(False),
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab._render(blocked)

        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.critical") as message, \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            tab.prepare_host()

        preparer.assert_not_called()
        self.assertIn("Network profile", message.call_args.args[1])
        tab.close()

    def test_show_local_gateway_host_fingerprint_exposes_only_fingerprint(self):
        host_key = GatewayHostKey(
            "localhost", 22, "localhost", key_line(91), "SHA256:GATEWAYHOST"
        )
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            host_key_reader=lambda **_kwargs: host_key, profile_loader=lambda: None, auto_refresh=False,
        )
        tab.show_local_host_key()
        self.wait_until(lambda: not tab.has_active_operation)
        self.assertIn("SHA256:GATEWAYHOST", tab.host_key_status.text())
        self.assertTrue(tab.copy_host_fingerprint_button.isEnabled())
        self.assertNotIn("PRIVATE", tab.host_key_status.text())
        tab.close()

    def test_fingerprint_idle_state_enables_read_and_disables_copy(self):
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            profile_loader=lambda: None, auto_refresh=False,
        )
        self.assertEqual(tab.host_key_status.property("state"), "idle")
        self.assertEqual(tab.host_key_status.text(), "Fingerprint: chưa đọc")
        self.assertTrue(tab.show_host_key_button.isEnabled())
        self.assertFalse(tab.copy_host_fingerprint_button.isEnabled())
        tab.close()

    def test_fingerprint_action_buttons_show_hover_feedback_when_enabled(self):
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab.setStyleSheet(APP_STYLE)
        tab.resize(760, 1200)
        tab.gateway_connection_details.set_expanded(True)
        tab.show()
        tab.copy_host_fingerprint_button.setEnabled(True)
        self.app.processEvents()

        for button in (tab.show_host_key_button, tab.copy_host_fingerprint_button):
            QTest.mouseMove(tab, QPoint(0, 0))
            self.app.processEvents()
            normal = button.grab().toImage().pixelColor(
                button.width() - 5, button.height() // 2
            )

            QTest.mouseMove(button, QPoint(button.width() - 5, button.height() // 2))
            self.app.processEvents()
            hovered = button.grab().toImage().pixelColor(
                button.width() - 5, button.height() // 2
            )

            self.assertNotEqual(
                normal, hovered,
                "%s must visibly react to hover" % button.objectName(),
            )
        tab.close()

    def test_destructive_action_buttons_show_hover_feedback_when_enabled(self):
        for object_name in ("cancelOperationButton", "memoryCancelButton"):
            container = QWidget()
            container.setStyleSheet(APP_STYLE)
            container.resize(180, 60)
            button = QPushButton("Cancel", container)
            button.setObjectName(object_name)
            button.resize(140, 34)
            button.move(20, 13)
            container.show()
            self.app.processEvents()

            QTest.mouseMove(container, QPoint(2, 2))
            self.app.processEvents()
            normal = button.grab().toImage().pixelColor(
                button.width() - 5, button.height() // 2
            )

            QTest.mouseMove(button, QPoint(button.width() - 5, button.height() // 2))
            self.app.processEvents()
            hovered = button.grab().toImage().pixelColor(
                button.width() - 5, button.height() // 2
            )

            self.assertNotEqual(
                normal, hovered,
                "%s must visibly react to hover" % object_name,
            )
            container.close()

    def test_fingerprint_button_passes_current_gateway_port_to_backend(self):
        host_key = GatewayHostKey(
            "localhost", 2222, "[localhost]:2222", key_line(96), "SHA256:CUSTOMPORT"
        )
        reader = mock.Mock(return_value=host_key)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), host_key_reader=reader,
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab._render(report(True, port=2222))
        tab.show_host_key_button.click()
        self.wait_until(lambda: not tab.has_active_operation)
        reader.assert_called_once_with(port=2222)
        self.assertIn("SHA256:CUSTOMPORT", tab.host_key_status.text())
        tab.close()

    def test_fingerprint_busy_state_disables_read_and_copy(self):
        release = threading.Event()
        started = threading.Event()

        def reader(**_kwargs):
            started.set()
            release.wait(2.0)
            return GatewayHostKey(
                "localhost", 22, "localhost", key_line(97), "SHA256:BUSY"
            )

        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), host_key_reader=reader,
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab.show_host_key_button.click()
        self.wait_until(started.is_set)
        self.assertEqual(tab.host_key_status.property("state"), "busy")
        self.assertEqual(tab.host_key_status.text(), "Đang đọc fingerprint...")
        self.assertFalse(tab.show_host_key_button.isEnabled())
        self.assertFalse(tab.copy_host_fingerprint_button.isEnabled())
        release.set()
        self.wait_until(lambda: not tab.has_active_operation)
        tab.close()

    def test_fingerprint_error_state_is_local_and_retryable(self):
        def reader(**_kwargs):
            raise RuntimeError("SSH Server did not respond on localhost:2222")

        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), host_key_reader=reader,
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab._render(report(True, port=2222))
        gateway_status_before = tab.status.text()
        tab.show_host_key_button.click()
        self.wait_until(lambda: not tab.has_active_operation)
        self.assertEqual(tab.host_key_status.property("state"), "error")
        self.assertIn("Không đọc được fingerprint", tab.host_key_status.text())
        self.assertIn("localhost:2222", tab.host_key_status.text())
        self.assertEqual(tab.status.text(), gateway_status_before)
        self.assertTrue(tab.show_host_key_button.isEnabled())
        self.assertFalse(tab.copy_host_fingerprint_button.isEnabled())
        tab.close()

    def test_fingerprint_ready_state_and_clipboard_contain_only_sha256(self):
        private_sentinel = "PRIVATE-HOST-KEY-MUST-NOT-LEAK"
        host_key = GatewayHostKey(
            "localhost", 22, "localhost", key_line(98, private_sentinel),
            "SHA256:READYONLY",
        )
        logs = []
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            host_key_reader=lambda **_kwargs: host_key,
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab.log.connect(logs.append)
        QApplication.clipboard().setText("stale clipboard")
        tab.show_host_key_button.click()
        self.wait_until(lambda: not tab.has_active_operation)
        self.assertEqual(tab.host_key_status.property("state"), "ready")
        self.assertEqual(
            tab.host_key_status.text(),
            "SSH host key\nED25519\nSHA256:READYONLY",
        )
        self.assertTrue(tab.copy_host_fingerprint_button.isEnabled())
        tab.copy_host_fingerprint_button.click()
        self.assertEqual(QApplication.clipboard().text(), "SHA256:READYONLY")
        exposed = "\n".join([tab.host_key_status.text(), tab.host_key_status.toolTip()] + logs)
        self.assertNotIn(private_sentinel, exposed)
        tab.close()

    def test_fingerprint_result_is_rejected_if_gateway_port_changes(self):
        release = threading.Event()
        started = threading.Event()

        def reader(**_kwargs):
            started.set()
            release.wait(2.0)
            return GatewayHostKey(
                "localhost", 2222, "[localhost]:2222", key_line(99), "SHA256:STALE"
            )

        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), host_key_reader=reader,
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab._render(report(True, port=2222))
        tab.show_host_key_button.click()
        self.wait_until(started.is_set)
        tab._render(report(True, port=2200))
        release.set()
        self.wait_until(lambda: not tab.has_active_operation)
        self.assertEqual(tab.host_key_status.property("state"), "error")
        self.assertNotIn("SHA256:STALE", tab.host_key_status.text())
        self.assertFalse(tab.copy_host_fingerprint_button.isEnabled())
        tab.close()

    def test_fingerprint_double_click_starts_only_one_worker(self):
        release = threading.Event()
        started = threading.Event()
        calls = []

        def reader(**kwargs):
            calls.append(kwargs)
            started.set()
            release.wait(2.0)
            return GatewayHostKey(
                "localhost", 22, "localhost", key_line(100), "SHA256:ONCE"
            )

        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), host_key_reader=reader,
            profile_loader=lambda: None, auto_refresh=False,
        )
        tab.show_host_key_button.click()
        self.wait_until(started.is_set)
        tab.show_host_key_button.click()
        self.app.processEvents()
        self.assertEqual(len(calls), 1)
        release.set()
        self.wait_until(lambda: not tab.has_active_operation)
        tab.close()

    def test_remote_host_trust_requires_exact_fingerprint_before_write(self):
        scanned = GatewayHostKey(
            "gateway.local", 22, "gateway.local", key_line(92), "SHA256:SCANNED"
        )
        truster = mock.Mock()
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            endpoint_prober=reachable_gateway_endpoint,
            host_key_scanner=lambda host, port: scanned, host_truster=truster,
            profile_loader=lambda: None, auto_refresh=False,
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
            endpoint_prober=reachable_gateway_endpoint,
            host_key_scanner=lambda host, port: scanned, host_truster=truster,
            profile_loader=lambda: None, auto_refresh=False,
        )
        result = tab._scan_verify_trust_host("gateway.local", 22, "SHA256:MATCH")
        self.assertEqual(result, trusted)
        truster.assert_called_once_with(scanned)
        tab.close()

    def test_unreachable_client_gateway_is_reported_before_host_key_scan(self):
        scanner = mock.Mock()
        unreachable = GatewayEndpointProbe(
            "192.168.1.145", 22, False, "SSH_TCP_UNREACHABLE",
            "TCP connection to 192.168.1.145:22 is unavailable.",
        )
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            endpoint_prober=lambda host, port: unreachable,
            host_key_scanner=scanner, profile_loader=lambda: None, auto_refresh=False,
        )

        with self.assertRaisesRegex(RuntimeError, "SSH_TCP_UNREACHABLE"):
            tab._scan_verify_trust_host("192.168.1.145", 22, "SHA256:EXPECTED")

        scanner.assert_not_called()
        tab.close()

    def test_client_network_failure_shows_an_actionable_primary_message(self):
        problem = GatewayEndpointProbe(
            "192.168.1.145", 22, False, "SSH_TCP_UNREACHABLE",
            "TCP connection to 192.168.1.145:22 is unavailable.",
        )
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(True), profile_loader=lambda: None,
            auto_refresh=False,
        )
        tab._select_role(1)
        tab._client_network_problem = problem
        tab._failed(SimpleNamespace(message="SSH_TCP_UNREACHABLE: %s" % problem.message))

        self.assertIn("Không thể tới 192.168.1.145:22", tab.client_connection_status.text())
        self.assertIn("Chuẩn bị Gateway", tab.client_connection_status.text())
        self.assertIn("Guest/AP isolation", tab.next_action.text())
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


    def test_role_selector_separates_gateway_and_client_workflows(self):
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False),
            profile_loader=lambda: None, auto_refresh=False,
        )
        self.assertEqual(tab.role_stack.currentIndex(), 0)
        self.assertTrue(tab.gateway_role_button.isChecked())
        self.assertIn("Gateway", tab.next_action.text())
        tab._select_role(1)
        self.assertEqual(tab.role_stack.currentIndex(), 1)
        self.assertTrue(tab.client_role_button.isChecked())
        self.assertIn("Client", tab.next_action.text())
        tab.close()

    def test_verified_remote_gateway_saves_nonsecret_profile(self):
        saved = []
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(True),
            endpoint_prober=reachable_gateway_endpoint,
            profile_loader=lambda: None,
            profile_saver=lambda profile: saved.append(profile) or Path("C:/profile.json"),
            auto_refresh=False,
        )
        payload = tab._trust_and_save_profile("192.168.1.95", "automation", 22)
        result, profile, path = payload
        self.assertIsNone(result)
        self.assertEqual(profile, RemoteGatewayProfile("192.168.1.95", "automation", 22))
        self.assertEqual(saved, [profile])
        self.assertEqual(path, Path("C:/profile.json"))
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab._remote_host_trusted(payload)
        self.assertIn("Profile READY", tab.client_profile_status.text())
        self.assertNotIn("private", str(profile.record()).lower())
        tab.close()

    def test_fingerprint_mismatch_never_saves_profile(self):
        saver = mock.Mock()
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(True),
            endpoint_prober=reachable_gateway_endpoint,
            profile_loader=lambda: None,
            profile_saver=saver, auto_refresh=False,
        )
        with self.assertRaises(ValueError):
            tab._trust_and_save_profile("invalid host with spaces", "automation", 22)
        saver.assert_not_called()
        tab.close()

    def test_client_connect_check_promotes_workflow_to_ready(self):
        profile = RemoteGatewayProfile("gateway.local", "automation", 22)
        result = RemoteConnectivityResult(
            True, 0, "automation@gateway.local:22", "SSH_READY",
            "Managed SSH key + strict host trust connection succeeded.",
        )
        checker = mock.Mock(return_value=result)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(True),
            profile_loader=lambda: profile, connectivity_checker=checker,
            auto_refresh=False,
        )
        tab._select_role(1)
        tab.check_client_connection()
        self.wait_until(lambda: not tab.has_active_operation)
        checker.assert_called_once_with(profile)
        self.assertIn("SSH READY", tab.client_connection_status.text())
        self.assertIn("Client READY", tab.next_action.text())
        tab.close()

    def test_generate_client_identity_updates_public_only_status(self):
        ready = identity_report(True)
        ensurer = mock.Mock(return_value=ready)
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), identity_ensurer=ensurer,
            client_prereq_inspector=lambda: prereq_report(True), profile_loader=lambda: None, auto_refresh=False,
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
            profile_loader=lambda: None, auto_refresh=False,
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
            profile_loader=lambda: None, auto_refresh=False,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QInputDialog.getMultiLineText", return_value=(public, True)), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information"):
            tab.authorize_client_key()
            self.wait_until(lambda: not tab.has_active_operation)
        authorizer.assert_called_once_with(public)
        tab.close()

    def test_authorize_result_does_not_claim_client_login_has_succeeded(self):
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), profile_loader=lambda: None,
            auto_refresh=False,
        )
        result = AuthorizedKeyResult(
            Path("C:/Users/Admin/.ssh/authorized_keys"), "SHA256:GATEWAY", True, False, True,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QMessageBox.information") as dialog:
            tab._key_authorized(result)

        message = dialog.call_args.args[2]
        self.assertIn("installed for sshd", message)
        self.assertIn("Run the Client SSH connection check", message)
        self.assertNotIn("connection succeeded", message.lower())
        tab.close()

    def test_invalid_public_key_never_reaches_authorizer(self):
        authorizer = mock.Mock()
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), key_authorizer=authorizer,
            profile_loader=lambda: None, auto_refresh=False,
        )
        with mock.patch("b300_gui.gateway_setup_tab.QInputDialog.getMultiLineText", return_value=("not-a-key", True)), \
             mock.patch("b300_gui.gateway_setup_tab.QMessageBox.critical") as dialog:
            tab.authorize_client_key()
        dialog.assert_called_once()
        authorizer.assert_not_called()
        tab.close()


    def test_compact_gateway_layout_has_equal_action_widths_and_no_idle_progress(self):
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(False), profile_loader=lambda: None, auto_refresh=False,
        )
        tab.resize(580, 520)
        tab.show()
        self.app.processEvents()
        self.assertEqual(tab.gateway_role_button.height(), tab.client_role_button.height())
        self.assertLessEqual(abs(tab.gateway_role_button.width() - tab.client_role_button.width()), 2)
        host_widths = [tab.refresh_button.width(), tab.prepare_button.width(), tab.selftest_button.width()]
        self.assertLessEqual(max(host_widths) - min(host_widths), 2)
        tab.gateway_connection_details.set_expanded(True)
        self.app.processEvents()
        detail_widths = [tab.show_host_key_button.width(), tab.copy_host_fingerprint_button.width()]
        self.assertLessEqual(max(detail_widths) - min(detail_widths), 2)
        self.assertGreater(tab.copy_button.width(), 200)
        self.assertFalse(tab.progress.isVisible())
        gateway_scroll = tab.role_stack.widget(0)
        self.assertEqual(gateway_scroll.horizontalScrollBar().maximum(), 0)
        tab.close()

    def test_client_form_remains_readable_without_horizontal_scroll_at_compact_width(self):
        tab = GatewaySetupTab(
            identity_inspector=lambda: identity_report(True), profile_loader=lambda: None, auto_refresh=False,
        )
        tab.resize(580, 520)
        tab.show()
        tab._select_role(1)
        self.app.processEvents()
        self.assertGreater(tab.trust_host.width(), tab.trust_port.width())
        self.assertGreater(tab.trust_user.width(), tab.trust_port.width())
        self.assertGreaterEqual(tab.trust_host.height(), 28)
        self.assertGreaterEqual(tab.trust_host_button.height(), 34)
        client_scroll = tab.role_stack.widget(1)
        self.assertEqual(client_scroll.horizontalScrollBar().maximum(), 0)
        tab.close()


if __name__ == "__main__":
    unittest.main()
