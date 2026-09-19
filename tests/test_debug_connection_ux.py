import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from b300_gui.debug_connection_panel import DebugConnectionPanel
from b300_gui.debug_mode_selector import DebugModeSelector
from b300_gui.remote_login_dialog import RemoteLoginDialog
from b300_version import __version__


class DebugConnectionUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_source_version_is_current_release(self) -> None:
        from b300_core import __version__ as core_version
        from b300_gui import __version__ as gui_version

        self.assertEqual(core_version, __version__)
        self.assertEqual(gui_version, __version__)

    def test_mode_first_surface_explains_connection_roles(self) -> None:
        selector = DebugModeSelector()
        self.assertEqual(selector.header_title.text(), "KẾT NỐI DEBUG")
        self.assertEqual(selector.tile_local.button.text(), "CHỌN")
        self.assertEqual(selector.tile_gateway.button.text(), "CHỌN")
        self.assertEqual(selector.tile_client.button.text(), "CHỌN")
        self.assertEqual(selector.tile_local.tag_lbl.text(), "[L]")
        self.assertEqual(selector.tile_gateway.tag_lbl.text(), "[G]")
        self.assertEqual(selector.tile_client.tag_lbl.text(), "[C]")
        self.assertIn("trực tiếp", selector.tile_local.subtitle_label.text())
        self.assertIn("máy này", selector.tile_gateway.subtitle_label.text().lower())
        self.assertIn("Gateway", selector.tile_client.subtitle_label.text())
        selector.close()

    def test_client_setup_has_one_visible_ssh_login_surface(self) -> None:
        panel = DebugConnectionPanel()
        panel.set_mode("client")
        panel.show()
        self.app.processEvents()
        self.assertTrue(panel.client_box.isVisible())
        self.assertTrue(panel.btn_open_login_dialog.isVisible())
        self.assertEqual(panel.btn_open_login_dialog.text(), "ĐĂNG NHẬP SSH")
        self.assertFalse(panel.client_host.isVisible())
        self.assertFalse(panel.client_user.isVisible())
        self.assertFalse(panel.client_ssh_port.isVisible())
        self.assertFalse(panel.btn_open_gateway.isVisible())
        panel.close()

    def test_gateway_setup_is_named_as_subordinate_debug_action(self) -> None:
        panel = DebugConnectionPanel()
        panel.set_mode("gateway")
        panel.show()
        self.app.processEvents()
        self.assertTrue(panel.btn_open_gateway.isVisible())
        self.assertEqual(panel.btn_open_gateway.text(), "CẤU HÌNH")
        self.assertIn("Gateway", panel.btn_open_gateway.toolTip())
        self.assertEqual(panel.remote_server_button.text(), "Bật Gateway")
        self.assertEqual(panel.gateway_stop_button.text(), "Dừng Gateway")
        self.assertEqual(panel.mode_title_label.text(), "GATEWAY · MÁY CẮM ST-LINK")
        self.assertFalse(panel.client_box.isVisible())
        panel.close()

    def test_login_dialog_masks_password_and_stays_compact(self) -> None:
        dialog = RemoteLoginDialog("192.168.1.10", "Admin", 22)
        self.assertEqual(dialog.password_input.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.btn_connect.text(), "KẾT NỐI")
        self.assertEqual(dialog.remember_checkbox.text(), "LƯU TRÊN MÁY NÀY")
        dialog.set_connecting(True)
        self.assertIn("ĐANG KẾT NỐI", dialog.status_banner.text())
        dialog.set_login_error("Permission denied")
        self.assertIn("Permission denied", dialog.status_banner.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
