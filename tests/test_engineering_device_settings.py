"""Engineering page contracts without hardware or process startup."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtWidgets import QApplication, QLabel, QWidget
from b300_core.models import TargetInfo
from b300_core.vscode_environment import VsCodeEnvironmentStatus
from b300_gui.views.device_view import DeviceView
from b300_gui.views.settings_view import SettingsView


class EngineeringDeviceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pages_use_shared_cards_without_inline_palette(self):
        for view in (DeviceView(), SettingsView()):
            cards = [w for w in view.findChildren(QWidget) if w.objectName() == "engineeringCard"]
            self.assertGreaterEqual(len(cards), 3)
            self.assertFalse(any("#" in w.styleSheet() for w in view.findChildren(QWidget)))

    def test_gateway_host_service_is_separate_and_state_driven(self):
        view = SettingsView()
        self.assertTrue(hasattr(view, "start_gateway_requested"))
        calls = []
        view.start_gateway_requested.connect(lambda: calls.append("start"))
        view.btn_start_gateway.click()
        self.assertEqual(calls, ["start"])
        view.set_gateway_status("READY", "loopback ready")
        self.assertFalse(view.btn_start_gateway.isEnabled())
        self.assertTrue(view.btn_stop_gateway.isEnabled())
        view.set_gateway_status("FAILED", "startup failed")
        self.assertIn("LỖI", view.gateway_status.text())
        self.assertIn("startup failed", view.gateway_status.toolTip())

    def test_runtime_renders_actual_readiness_and_clears(self):
        view = SettingsView()
        self.assertTrue(hasattr(view, "set_environment_status"))
        view.set_environment_status(VsCodeEnvironmentStatus(False, False, False, reason="missing tools"))
        self.assertIn("Thiếu", view.lbl_gdb.text())
        self.assertIn("Thiếu", view.lbl_cortex.text())
        view.set_environment_status(VsCodeEnvironmentStatus(True, True, True, "code", "gdb"))
        self.assertIn("Sẵn sàng", view.lbl_gdb.text())
        view.set_environment_status(None)
        self.assertIn("Chưa kiểm tra", view.lbl_gdb.text())

    def test_device_inspection_signal_remains_read_only_and_busy(self):
        view = DeviceView()
        calls = []
        view.doctor_requested.connect(lambda: calls.append("inspect"))
        view.btn_doctor.click()
        self.assertEqual(calls, ["inspect"])
        view.set_busy(True)
        view.btn_doctor.click()
        self.assertEqual(calls, ["inspect"])
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "S0-S2 protected", (0, 1, 2), True))
        self.assertIn("ĐÃ BẢO VỆ", view.val_wrp.text())
        view.set_target_info(None)
        self.assertEqual(view.val_wrp.text(), "Chưa kiểm tra")

    def test_settings_emits_density_and_real_support_requests(self):
        view = SettingsView()
        self.assertTrue(hasattr(view, "density_changed"))
        calls = []
        view.density_changed.connect(lambda value: calls.append(value))
        view.density_selector.setCurrentIndex(view.density_selector.findData("comfortable"))
        self.assertEqual(calls, ["comfortable"])
        view.set_density("compact")
        self.assertEqual(view.density_selector.currentData(), "compact")
        self.assertEqual(calls, ["comfortable"])
        view.open_logs_requested.connect(lambda: calls.append("logs"))
        view.documentation_requested.connect(lambda: calls.append("docs"))
        view.refresh_environment_requested.connect(lambda: calls.append("refresh"))
        view.btn_open_logs.click()
        view.btn_documentation.click()
        view.btn_refresh_environment.click()
        self.assertEqual(calls[-3:], ["logs", "docs", "refresh"])

    def test_settings_uses_vietnamese_labels(self):
        view = SettingsView()
        self.assertEqual(view.density_selector.itemText(0), "Thu gọn")
        self.assertEqual(view.density_selector.itemText(1), "Thoải mái")
        labels = "\n".join(widget.text() for widget in view.findChildren(QLabel))
        self.assertIn("Công cụ chạy và biên dịch", labels)
        self.assertIn("Lõi xử lý", labels)
        self.assertNotIn("Runtime / Toolchain", labels)
        self.assertNotIn("Project và Connection", labels)


if __name__ == "__main__":
    unittest.main()
