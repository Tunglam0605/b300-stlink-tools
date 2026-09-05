"""Behavioral regressions in the reference-inspired production frontend."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QLabel, QFrame
from b300_core.models import TargetInfo
from b300_core.vscode_bridge import BridgeState
from b300_gui.app_context import AppContext
from b300_gui.views.debug_vscode_view import DebugVsCodeView
from b300_core.project_profiles import ProjectProfile
from b300_core.gateway_profiles import GatewayProfile
from b300_gui.views.program_view import ProgramView
from b300_gui.widgets.header_bar import HeaderBar
from b300_gui.widgets.device_info_panel import DeviceInfoPanel
from b300_gui.reference_style import apply_reference_palette
from b300_gui.theme import DARK_PALETTE, LIGHT_PALETTE


class FrontendReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dry_run_does_not_change_next_flash_action(self):
        view = ProgramView()
        view._selected_file = Path("application.hex")
        calls = []
        view.flash_application_requested.connect(lambda path, dry: calls.append(dry))
        view._on_dry_run_clicked()
        view._on_flash_app_clicked()
        self.assertEqual(calls, [True, False])

    def test_debug_open_uses_selected_global_connection_without_host_mode(self):
        context = AppContext()
        gateway = GatewayProfile.create("Test", "gateway.example", "operator", profile_id="gw")
        project = ProjectProfile("project", "Project", Path("work"), Path("main.axf"))
        context.set_profiles((project,), (gateway,), default_gateway_id="gw")
        view = DebugVsCodeView(context=context)
        self.addCleanup(view.close)
        requests = []
        view.open_remote_vscode_requested.connect(requests.append)
        view.btn_open_vscode.click()
        self.assertEqual(requests[0]["host"], "gateway.example")
        self.assertEqual(requests[0]["gateway_id"], "gw")
        self.assertFalse(hasattr(view, "btn_start_gateway"))

    def test_unknown_sidebar_does_not_claim_protection_or_voltage(self):
        panel = DeviceInfoPanel()
        self.assertIn("Chưa", panel.card_protection.value_label.text())
        self.assertNotIn("Ổn định", panel.card_vtarget.badge_label.text())
        panel.set_target_info(TargetInfo(0x413, 512, 3.3, "unknown", (), False))
        self.assertIn("Chưa", panel.card_protection.value_label.text())
        self.assertEqual(panel.card_flash.value_label.text(), "512 KiB")
        self.assertNotIn("16", panel.card_flash.sub_label.text())
        panel.set_target_info(None)
        self.assertNotIn("Ổn định", panel.card_vtarget.badge_label.text())

    def test_sidebar_does_not_invent_mcu_or_measurement(self):
        panel = DeviceInfoPanel()
        panel.set_target_info(TargetInfo(0x999, 512, None, "unknown"))
        self.assertNotIn("STM32F407ZET6", panel.card_mcu.value_label.text())
        self.assertEqual(panel.card_vtarget.value_label.text(), "—")

    def test_preflight_badge_rejects_unprotected_target(self):
        view = ProgramView()
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "unprotected", (), True))
        self.assertNotIn("Sẵn sàng", view.badge_preflight.text())
        self.assertEqual(view.lbl_target_flash.text(), "512 KiB")

    def test_debug_remote_launch_uses_latest_shared_project_symbols(self):
        context = AppContext()
        gateway = GatewayProfile.create("Test", "gateway.example", "operator", profile_id="gw")
        first = ProjectProfile("first", "First", Path("work"), Path("first.axf"))
        second = ProjectProfile("second", "Second", Path("other"), Path("second.axf"))
        context.set_profiles((first, second), (gateway,), default_gateway_id="gw")
        view = DebugVsCodeView(context=context)
        self.addCleanup(view.close)
        requests = []
        view.open_remote_vscode_requested.connect(requests.append)
        context.select_project("second")
        view.btn_open_vscode.click()
        self.assertEqual(requests[0]["elf"], Path("second.axf"))
        self.assertEqual(requests[0]["workspace"], Path("other"))

    def test_palette_round_trip_and_state_change(self):
        root = QFrame()
        root.setStyleSheet("background: #131D31; border: 1px solid #223452;")
        label = QLabel("Status", root)
        label.setStyleSheet("color: #F8FAFC;")
        apply_reference_palette(root, LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.text, label.styleSheet())
        self.assertIn('QFrame[referenceStyleId=', root.styleSheet())
        apply_reference_palette(root, DARK_PALETTE)
        self.assertIn(DARK_PALETTE.text, label.styleSheet())
        label.setStyleSheet("color: #F87171;")
        apply_reference_palette(root, LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.danger, label.styleSheet())


if __name__ == "__main__":
    unittest.main()
