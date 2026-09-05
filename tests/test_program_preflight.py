"""Production PROGRAM orchestration; only hardware IO and modal approval are fake."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from b300_core.gateway_profiles import GatewayProfileStore
from b300_core.gateway_sessions import GatewaySessionManager
from b300_core.hardware_session import HardwareSessionManager
from b300_core.models import ProbeInfo, ProbeRef, TargetInfo
from b300_core.project_profiles import ProjectProfileStore
from b300_gui.main_window_v18 import MainWindowV18
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex
from tests.test_gui_smoke import FakeService


class ProgramPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FakeService()
        self.service.session_manager = HardwareSessionManager()
        self.target = TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)
        self.service.inspect_target = mock.Mock(return_value=self.target)
        self.window = MainWindowV18(
            service=self.service,
            probe_loader=lambda: (ProbeInfo(name="ST-Link", serial="PROBE-A", source="usb", usb_identity="test"),),
            automatic_updates=False, first_run_setup=False,
            gateway_store=GatewayProfileStore(root / "gateways.json", legacy_path=root / "legacy.json"),
            project_store=ProjectProfileStore(root / "projects.json"),
            gateway_sessions=GatewaySessionManager(),
        )
        self.path = write_hex(self.temp.name, 0x08010000, APPLICATION_VECTOR)
        self.window.program_view.set_file_path(self.path)
        self.approval = mock.patch.object(self.window, "confirm_flash")
        self.confirm = self.approval.start()

    def tearDown(self):
        self.drain()
        self.approval.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def drain(self):
        deadline = time.monotonic() + 5
        while self.window._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertFalse(self.window._threads, "worker did not finish")

    def click(self):
        self.window._on_v18_flash_application(self.path, False)
        self.drain()

    def test_fresh_hex_is_pending_and_can_start_preflight(self):
        view = self.window.program_view
        self.assertEqual(view.banner.property("variant"), "info")
        self.assertIn("Chưa", view.lbl_target.text())
        self.assertTrue(view.btn_flash_app.isEnabled())

    def test_click_inspects_before_canonical_confirmation(self):
        self.click()
        self.service.inspect_target.assert_called_once()
        self.confirm.assert_called_once()
        self.assertEqual(self.window.flash_plan.erase_sectors, (3, 4, 5, 6, 7))
        self.assertEqual(self.window.flash_plan.probe.serial, "PROBE-A")

    def test_wrp_failure_blocks_before_confirmation(self):
        self.service.inspect_target.return_value = TargetInfo(0x413, 512, 3.1, "unprotected", (), True)
        self.click()
        self.confirm.assert_not_called()
        self.assertIsNone(self.window.flash_plan)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")
        self.assertIn("WRP", self.window.program_view.banner.detail_label.text())

    def test_wrong_target_is_blocked_and_not_labelled_f407(self):
        self.service.inspect_target.return_value = TargetInfo(0x450, 1024, 3.1, "protected", (0, 1, 2), True)
        self.click()
        self.confirm.assert_not_called()
        self.assertNotIn("STM32F407", self.window.program_view.lbl_target.text())
        self.assertNotIn("STM32F407", self.window.device_view.val_mcu_family.text())

    def test_rescan_invalidates_all_views(self):
        self.window.apply_target_info(self.target)
        self.window.refresh_probes()
        self.assertIsNone(self.window.target_info)
        self.assertIsNone(self.window.program_view._target_info)
        self.assertIsNone(self.window.device_view._target_info)
        self.assertIsNone(self.window.flash_plan)

    def test_probe_change_invalidates_all_views(self):
        self.window.apply_target_info(self.target)
        self.window.probe_combo.addItem("Other", "PROBE-B")
        self.window.probe_combo.setCurrentIndex(self.window.probe_combo.count() - 1)
        self.assertIsNone(self.window.program_view._target_info)
        self.assertIsNone(self.window.device_view._target_info)

    def test_hex_change_rebuilds_plan_and_invalid_hex_clears_it(self):
        self.window.apply_target_info(self.target)
        old_hash = self.window.flash_plan.image.sha256
        write_hex(self.temp.name, 0x08010000, APPLICATION_VECTOR + b"new")
        self.window.program_view.set_file_path(self.path)
        self.assertNotEqual(self.window.flash_plan.image.sha256, old_hash)
        self.window.program_view.set_file_path(Path(self.temp.name) / "missing.hex")
        self.assertIsNone(self.window.image_info)
        self.assertIsNone(self.window.flash_plan)

    def test_device_inspect_publishes_same_object_without_reopen(self):
        self.window.inspect_target()
        self.drain()
        self.assertIs(self.window.target_info, self.target)
        self.assertIs(self.window.program_view._target_info, self.target)
        self.assertIs(self.window.device_view._target_info, self.target)

    def test_program_preflight_publishes_same_object_to_device(self):
        self.click()
        self.assertIs(self.window.target_info, self.target)
        self.assertIs(self.window.device_view._target_info, self.target)
        self.assertIs(self.window.program_view._target_info, self.target)

    def test_no_duplicate_inspect_button(self):
        self.assertTrue(self.window.program_view.btn_inspect_target.isHidden())
        self.assertFalse(self.window.device_view.btn_doctor.isHidden())

    def test_top_and_program_both_show_uninspected(self):
        self.assertIn("Chưa", self.window.stats_row.target_card.value_label.text())
        self.assertIn("Chưa", self.window.program_view.lbl_target.text())

    def test_hardware_lease_blocks_preflight(self):
        lease = self.service.session_manager.acquire_debugging(ProbeRef("PROBE-A"))
        try:
            self.window._update_controls()
            self.assertFalse(self.window.program_view.btn_flash_app.isEnabled())
            self.click()
            self.service.inspect_target.assert_not_called()
            self.confirm.assert_not_called()
        finally:
            lease.release()

    def test_cached_pass_does_not_skip_fresh_inspection(self):
        self.window.apply_target_info(self.target)
        self.click()
        self.service.inspect_target.assert_called_once()

    def test_inspection_io_failure_clears_old_evidence(self):
        self.window.apply_target_info(self.target)
        self.service.inspect_target.side_effect = RuntimeError("USB disconnected")
        self.click()
        self.confirm.assert_not_called()
        self.assertIsNone(self.window.target_info)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")
        self.assertIn("USB disconnected", self.window.program_view.banner.detail_label.text())

    def during_inspection(self, action):
        started, release = threading.Event(), threading.Event()
        def inspect(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test did not release inspection")
            return self.target
        self.service.inspect_target.side_effect = inspect
        self.window._on_v18_flash_application(self.path, False)
        try:
            self.assertTrue(started.wait(2))
            action()
        finally:
            release.set()
            self.drain()

    def test_cancel_discards_even_successful_late_result(self):
        self.during_inspection(self.window.cancel_operation)
        self.confirm.assert_not_called()
        self.assertIsNone(self.window.target_info)
        self.assertEqual(self.window.program_view.banner.property("variant"), "info")

    def test_probe_change_discards_late_result(self):
        self.during_inspection(self.window.refresh_probes)
        self.confirm.assert_not_called()
        self.assertIsNone(self.window.target_info)

    def test_image_change_during_inspection_does_not_confirm(self):
        def change():
            write_hex(self.temp.name, 0x08010000, APPLICATION_VECTOR + b"new")
            self.window.program_view.set_file_path(self.path)
        self.during_inspection(change)
        self.confirm.assert_not_called()

    def test_rdp_and_missing_wrp_block(self):
        for target in (
            TargetInfo(0x413, 512, 3.1, "protected", (0, 1, 2), True, True),
            TargetInfo(0x413, 512, 3.1, "unknown", (), False),
        ):
            self.service.inspect_target.return_value = target
            self.click()
            self.confirm.assert_not_called()
            self.assertIsNone(self.window.flash_plan)
            self.assertEqual(self.window.program_view.banner.property("variant"), "fail")

    def test_approval_hands_off_to_existing_service_flash(self):
        from b300_core.service import FlashResult
        self.approval.stop()
        self.service.flash = mock.Mock(return_value=FlashResult(
            "failed", None, None, None, None, reason="simulated stop",
        ))
        with mock.patch("b300_gui.confirm_dialog.ConfirmFlashDialog.exec", return_value=1):
            self.click()
        self.service.flash.assert_called_once()
        plan = self.service.flash.call_args.args[0]
        self.assertEqual(plan.probe.serial, "PROBE-A")
        self.assertEqual(plan.erase_sectors, (3, 4, 5, 6, 7))
        self.assertEqual(plan.image.sha256, self.window.image_info.sha256)
        self.assertEqual(self.window.program_view.banner.property("variant"), "fail")

    def test_factory_failure_discards_pre_mutation_wrp_evidence(self):
        from b300_core.service import FactoryResult
        from b300_gui.workers import WorkerFailure
        self.window.apply_target_info(self.target)
        self.window._factory_finished(FactoryResult(
            "failed", reason="WRP restoration could not be verified",
        ))
        self.assertIsNone(self.window.target_info)
        self.assertIsNone(self.window.program_view._target_info)
        self.assertIsNone(self.window.device_view._target_info)
        self.window.apply_target_info(self.target)
        self.window._factory_operation_failed(WorkerFailure(
            "protect", "USB disconnected", "Verify WRP before programming", "test",
        ))
        self.assertIsNone(self.window.target_info)
        self.assertIsNone(self.window.flash_plan)

    def test_factory_mutation_start_discards_preflight_snapshot(self):
        from types import SimpleNamespace
        self.window.factory_trusted = SimpleNamespace(image=object())
        self.service.factory_plan = mock.Mock(return_value=object())
        with mock.patch.object(self.window, "_start_worker"):
            self.window._factory_preflight_finished(ProbeRef("PROBE-A"), self.target)
            self.assertIsNone(self.window.target_info)
            self.assertIsNone(self.window.device_view._target_info)
        self.window.busy = False
