"""Pure widget checks: no controllers, hardware, processes, or SSH."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtWidgets import QApplication, QLabel
from b300_core.models import TargetInfo
from b300_gui.views.device_view import DeviceView
from b300_gui.views.settings_view import SettingsView
from b300_gui.views.debug_vscode_view import DebugVsCodeView


class FrontendDeviceTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_uninspected_device_has_no_fabricated_evidence(self):
        view = DeviceView()
        self.assertNotIn("READY", view.kpi_probe_status.text())
        for label in (view.val_option_bytes, view.val_uid, view.val_reset_status,
                      view.val_rev_id, view.kpi_volt_badge, view.val_boot_prot):
            self.assertIn("Chưa", label.text())

    def test_protection_evidence_is_updated_and_cleared(self):
        view = DeviceView()
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "WRP", (0, 1, 2), True))
        self.assertIn("Đã bảo vệ", view.val_boot_prot.text())
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "missing"))
        self.assertIn("Chưa", view.val_boot_prot.text())
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "WRP", (), True))
        self.assertIn("Chưa bảo vệ đủ", view.val_boot_prot.text())
        view.set_target_info(None)
        self.assertIn("Chưa kiểm tra", view.kpi_rdp_level.text())
        self.assertIn("Chưa kiểm tra", view.val_boot_prot.text())

    def test_b300_map_excludes_bootloader_from_application(self):
        view = DeviceView()
        text = "\n".join(x.text() for x in view.findChildren(QLabel))
        self.assertIn("0x08010000 - 0x0807FFFF", text)
        self.assertIn("0x08000000 - 0x0800BFFF", text)
        self.assertIn("0x0800C000 - 0x0800FFFF", text)
        self.assertNotIn("Sectors 13 - 15", text)

    def test_readout_protected_target_is_not_reported_normal(self):
        view = DeviceView()
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "WRP", (0, 1, 2), True, True))
        self.assertNotEqual("Bình thường", view.kpi_prot_state.text())
        self.assertNotEqual("RDP Level 1", view.kpi_rdp_level.text())

    def test_unwired_device_actions_stay_disabled_after_busy(self):
        view = DeviceView()
        view.set_busy(True)
        view.set_busy(False)
        for button in (view.btn_read_metadata, view.btn_export_evidence, view.btn_guide, view.btn_menu):
            self.assertFalse(button.isEnabled())
            self.assertTrue(button.toolTip())

    def test_failed_bridge_is_distinct_from_stopped(self):
        view = DebugVsCodeView()
        view.set_bridge_state("LOCAL", "FAILED", "connection lost")
        self.assertIn("LỖI", view.bridge_status.text().upper())
        self.assertNotIn("ĐÃ DỪNG", view.bridge_status.text().upper())

    def test_debug_does_not_promise_zero_halt(self):
        view = DebugVsCodeView()
        text = "\n".join(x.text() for x in view.findChildren(QLabel))
        self.assertNotIn("Không reset hoặc dừng CPU", text)

    def test_settings_does_not_claim_unchecked_tools_installed(self):
        view = SettingsView()
        labels = [x.text() for x in view.findChildren(QLabel)]
        self.assertNotIn("Đã cài", labels)
        self.assertNotIn("OK", labels)
        self.assertIn("Chưa kiểm tra", labels)


if __name__ == "__main__":
    unittest.main()
