import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from b300_gui.debug_connection_panel import DebugConnectionPanel
from b300_gui.debug_mode_selector import DebugModeSelector
from b300_gui.remote_login_dialog import RemoteLoginDialog
from b300_version import __version__


class V015ReleaseUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_source_version_is_v0150(self) -> None:
        self.assertEqual(__version__, "0.15.0")

    def test_mode_first_surface_is_compact_and_explicit(self) -> None:
        selector = DebugModeSelector()
        self.assertEqual(selector.tile_local.button.text(), "SELECT")
        self.assertEqual(selector.tile_gateway.button.text(), "SELECT")
        self.assertEqual(selector.tile_client.button.text(), "SELECT")
        self.assertEqual(selector.tile_local.tag_lbl.text(), "[L]")
        self.assertEqual(selector.tile_gateway.tag_lbl.text(), "[G]")
        self.assertEqual(selector.tile_client.tag_lbl.text(), "[C]")
        selector.close()

    def test_client_setup_has_one_visible_ssh_login_surface(self) -> None:
        panel = DebugConnectionPanel()
        panel.set_mode("client")
        panel.show()
        self.app.processEvents()
        self.assertTrue(panel.client_box.isVisible())
        self.assertTrue(panel.btn_open_login_dialog.isVisible())
        self.assertFalse(panel.client_host.isVisible())
        self.assertFalse(panel.client_user.isVisible())
        self.assertFalse(panel.client_ssh_port.isVisible())
        self.assertFalse(panel.btn_open_gateway.isVisible())
        panel.close()

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
