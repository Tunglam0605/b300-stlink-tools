from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import math
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox

from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.project_profiles import ProjectProfileStore
from b300_core.remote_program_history import RemoteProgramHistory
from b300_gui.production_window import ProductionMainWindow
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex
from tests.test_gui_smoke import FakeService


class FakeRemoteSession:
    connected = True

    def __init__(self):
        self.commits = 0
        self.prepares = 0
        self.cancels = 0
        self.cleanups = 0

    def prepare_remote_application(self, path, grant, client_id):
        self.prepares += 1
        return {
            "job_id": "a" * 32, "state": "AWAITING_CONFIRMATION",
            "manifest": {"file_name": Path(path).name, "sha256": "b" * 64},
            "plan": {"erase_sectors": [3, 4, 5, 6, 7], "probe_serial": "SAFE123",
                     "metadata_address": "0x0800C000", "metadata_bytes": 44,
                     "device_id": 0x413, "flash_kib": 512,
                     "target_voltage": 3.1, "protection_reported": True,
                     "readout_protected": False,
                     "protected_sectors": [0, 1, 2]},
            "approval_token": "private-approval",
        }

    def commit_remote_application(self, approval, grant):
        self.commits += 1
        return {"job_id": approval["job_id"], "state": "RUNNING"}

    def remote_program_status(self, job_id):
        return {"job_id": job_id, "state": "SUCCEEDED", "pc": 0x08010101,
                "bkp1r": 0, "metadata_state": "CONFIRMED"}

    def cancel_remote_application(self, job_id, grant):
        self.cancels += 1
        return {"job_id": job_id, "state": "CANCELLED"}

    def cleanup_remote_application(self, job_id):
        self.cleanups += 1
        return {"job_id": job_id, "state": "SUCCEEDED"}


class FakeLease:
    created = []

    def __init__(self, session, **kwargs):
        self.grant = SimpleNamespace(lease_id="lease", token="secret", generation=1,
                                     public={"probe_serial": "SAFE123"})
        self.closed = False
        self.created.append(self)

    def start(self, mode, *, probe_serial=None):
        return self.grant

    def close(self):
        self.closed = True


class RemoteProgramGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeLease.created.clear()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        gateways = GatewayProfileStore(root / "gateways.json", legacy_path=root / "legacy.json")
        self.gateway = GatewayProfile.create("Gateway", "192.0.2.8", "aubot", profile_id="gateway-1")
        gateways.upsert(self.gateway)
        self.window = ProductionMainWindow(
            service=FakeService(), probe_loader=lambda: (), automatic_updates=False,
            first_run_setup=False, gateway_store=gateways,
            project_store=ProjectProfileStore(root / "projects.json"),
            remote_program_history=RemoteProgramHistory(root / "recent.json"),
        )
        self.path = write_hex(self.temp.name, 0x08010000, APPLICATION_VECTOR)
        self.window.app_context.select_connection("gateway-1")
        self.window.program_view.set_file_path(self.path)
        self.session = FakeRemoteSession()

    def tearDown(self):
        deadline = time.monotonic() + 3
        while self.window._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def _run(self, dry_run, answer):
        with mock.patch.object(self.window, "_session_matches", return_value=True), \
                mock.patch.object(self.window, "_get_or_create_remote_session", return_value=self.session), \
                mock.patch("b300_gui.production_window.GatewayLeaseClient", FakeLease), \
                mock.patch.object(QMessageBox, "question", return_value=answer):
            self.window._on_v18_flash_application(self.path, dry_run)
            deadline = time.monotonic() + 3
            while self.window._threads and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.app.processEvents()

    def test_gateway_dry_run_does_not_commit(self):
        self._run(True, QMessageBox.StandardButton.No)
        self.assertEqual(self.session.prepares, 1)
        self.assertEqual(self.session.commits, 0)
        self.assertEqual(self.session.cancels, 1)
        self.assertEqual(self.session.cleanups, 1)
        self.assertIn("Sector 3", self.window.program_view.banner.detail_label.text())
        view = self.window.program_view
        self.assertIn("STM32F407", view.lbl_target.text())
        self.assertIn("S0–S2", view.lbl_target_wrp.text())
        self.assertIn("Mức 0", view.lbl_target_rdp.text())
        self.assertIn("Gateway dry-run", view.badge_preflight.text())

    def test_remote_dry_run_evidence_is_cleared_when_file_changes(self):
        self._run(True, QMessageBox.StandardButton.No)
        self.assertIn("Gateway dry-run", self.window.program_view.badge_preflight.text())
        self.window.program_view.set_file_path(Path(self.temp.name) / "missing.hex")
        self.assertIn("Chưa kiểm tra", self.window.program_view.badge_preflight.text())

    def test_nonfinite_gateway_voltage_cannot_be_shown_as_checked_target(self):
        plan = dict(self.session.prepare_remote_application(self.path, None, "client-1")["plan"])
        plan["target_voltage"] = math.nan
        with self.assertRaises(ValueError):
            self.window.program_view.set_remote_preflight(plan)
        self.assertIn("Chưa kiểm tra", self.window.program_view.badge_preflight.text())

    def test_oversized_gateway_voltage_cannot_leave_prepared_lease_open(self):
        original = self.session.prepare_remote_application

        def malformed(*args):
            approval = original(*args)
            approval["plan"]["target_voltage"] = 10 ** 1000
            return approval

        with mock.patch.object(self.session, "prepare_remote_application", side_effect=malformed):
            self._run(True, QMessageBox.StandardButton.No)
        self.assertEqual(self.session.commits, 0)
        self.assertEqual(self.session.cancels, 1)
        self.assertTrue(FakeLease.created[-1].closed)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")

    def test_malformed_gateway_evidence_cancels_prepared_job_and_releases_lease(self):
        original = self.session.prepare_remote_application

        def malformed(*args):
            approval = original(*args)
            approval["plan"]["target_voltage"] = math.nan
            return approval

        with mock.patch.object(self.session, "prepare_remote_application", side_effect=malformed):
            self._run(True, QMessageBox.StandardButton.No)
        self.assertEqual(self.session.commits, 0)
        self.assertEqual(self.session.cancels, 1)
        self.assertEqual(self.session.cleanups, 1)
        self.assertTrue(FakeLease.created[-1].closed)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")

    def test_missing_gateway_target_evidence_blocks_confirmation_and_cleans_job(self):
        original = self.session.prepare_remote_application

        def legacy(*args):
            approval = original(*args)
            approval["plan"].pop("protection_reported")
            return approval

        with mock.patch.object(self.session, "prepare_remote_application", side_effect=legacy):
            self._run(False, QMessageBox.StandardButton.Yes)
        self.assertEqual(self.session.commits, 0)
        self.assertEqual(self.session.cancels, 1)
        self.assertEqual(self.session.cleanups, 1)
        self.assertTrue(FakeLease.created[-1].closed)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")
        self.assertIn("Gateway", self.window.program_view.banner.detail_label.text())

    def test_cancel_transport_error_still_closes_flash_lease(self):
        with mock.patch.object(self.session, "cancel_remote_application",
                               side_effect=RuntimeError("SSH lost")):
            self._run(True, QMessageBox.StandardButton.No)
        self.assertTrue(FakeLease.created[-1].closed)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")

    def test_gateway_confirmation_commits_and_shows_verified_result(self):
        self._run(False, QMessageBox.StandardButton.Yes)
        self.assertEqual(self.session.commits, 1)
        self.assertEqual(self.session.cleanups, 1)
        self.assertEqual(self.window.program_view.banner.property("variant"), "pass")

    def test_gateway_rejected_confirmation_cancels_without_flash(self):
        self._run(False, QMessageBox.StandardButton.No)
        self.assertEqual(self.session.commits, 0)
        self.assertEqual(self.session.cancels, 1)

    def test_remote_job_is_saved_and_can_be_queried_after_reopen(self):
        self._run(False, QMessageBox.StandardButton.Yes)
        stored = RemoteProgramHistory(Path(self.temp.name) / "recent.json")
        self.assertEqual(stored.get("gateway-1"), "a" * 32)
        with mock.patch.object(self.window, "_session_matches", return_value=True), \
                mock.patch.object(self.window, "_get_or_create_remote_session", return_value=self.session):
            self.window._check_recent_remote_program()
            deadline = time.monotonic() + 3
            while self.window._threads and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertIn("STLM CONFIRMED", self.window.program_view.banner.detail_label.text())


if __name__ == "__main__":
    unittest.main()
