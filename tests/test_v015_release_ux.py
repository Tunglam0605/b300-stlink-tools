import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from b300_gui.debug_connection_panel import DebugConnectionPanel
from b300_gui.debug_mode_selector import DebugModeSelector
from b300_gui.main_window_v15 import MainWindowV15
from b300_gui.remote_login_dialog import RemoteLoginDialog
from b300_version import __version__


class V015ReleaseUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_source_version_is_current_release(self) -> None:
        self.assertEqual(__version__, "0.16.0")

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

    def test_production_window_has_no_second_top_level_ssh_workflow(self) -> None:
        # Do not navigate into the live Gateway infrastructure page here: doing so
        # intentionally starts its asynchronous readiness inspection. This regression
        # test validates hierarchy/visibility without exercising host networking.
        window = MainWindowV15(
            probe_loader=lambda: (),
            automatic_updates=False,
            first_run_setup=False,
        )
        self.assertTrue(window.nav_gateway_btn.isHidden())
        self.assertIn("Studio Debug", window.nav_debug_btn.text())
        self.assertEqual(window.gateway_tab.role_stack.currentIndex(), 0)

        role_header = window.gateway_tab.gateway_role_button.parentWidget()
        self.assertIsNotNone(role_header)
        self.assertTrue(role_header.isHidden())

        authorize_group = window.gateway_tab.authorize_key_button.parentWidget()
        self.assertIsNotNone(authorize_group)
        self.assertTrue(authorize_group.isHidden())

        window._update_page_context(3)
        self.assertIn("Gateway", window.page_title.text())
        self.assertIn("Studio Debug", window.page_subtitle.text())
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_login_dialog_masks_password_and_stays_compact(self) -> None:
        dialog = RemoteLoginDialog("192.168.1.10", "Admin", 22)
        self.assertEqual(dialog.password_input.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.btn_connect.text(), "CONNECT")
        self.assertEqual(dialog.remember_checkbox.text(), "SAVE LOCAL")
        dialog.set_connecting(True)
        self.assertIn("CONNECTING", dialog.status_banner.text())
        dialog.set_login_error("Permission denied")
        self.assertIn("Permission denied", dialog.status_banner.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()