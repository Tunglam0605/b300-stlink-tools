from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.gateway_sessions import GatewaySessionManager
from b300_core.project_profiles import ProjectProfile, ProjectProfileStore
from b300_gui.gateway_login_dialog import GatewayLoginDialog
from b300_gui.gateway_manager_dialog import GatewayManagerDialog
from b300_gui.project_manager_dialog import ProjectManagerDialog
from b300_gui.views.debug_vscode_view import DebugVsCodeView
from b300_gui.views.monitor_view import MonitorView


class SharedManagersUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_gateway_manager_lists_named_endpoints_without_password_column(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = GatewayProfileStore(root / "gateways.json", legacy_path=root / "legacy.json")
            first = GatewayProfile.create("Robot Lab", "192.168.1.158", "aubot", 22)
            second = GatewayProfile.create("Company", "192.168.1.107", "aubot-tech65", 2222)
            store.upsert(first); store.upsert(second)
            dialog = GatewayManagerDialog(store, GatewaySessionManager())
            try:
                self.assertEqual(dialog.table.rowCount(), 2)
                headers = [dialog.table.horizontalHeaderItem(i).text().lower() for i in range(dialog.table.columnCount())]
                self.assertNotIn("password", " ".join(headers))
                self.assertIn("Robot Lab", [dialog.table.item(i, 0).text() for i in range(2)])
            finally:
                dialog.deleteLater(); self.app.processEvents()

    def test_login_dialog_is_password_only_for_fixed_saved_gateway(self):
        profile = GatewayProfile.create("Robot Lab", "192.168.1.158", "aubot", 22)
        dialog = GatewayLoginDialog(profile)
        try:
            edits = dialog.findChildren(QLineEdit)
            self.assertEqual(edits, [dialog.password_input])
            self.assertEqual(dialog.profile.endpoint.host, "192.168.1.158")
            self.assertNotIn("remember", " ".join(child.objectName().lower() for child in dialog.children()))
        finally:
            dialog.deleteLater(); self.app.processEvents()

    def test_project_manager_and_views_share_same_project_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "src"; workspace.mkdir()
            symbols = workspace / "main.axf"; symbols.write_bytes(b"ELF")
            store = ProjectProfileStore(root / "projects.json")
            project = ProjectProfile.create("B300 Main", workspace, symbols)
            store.upsert(project)
            dialog = ProjectManagerDialog(store)
            debug = DebugVsCodeView(); monitor = MonitorView()
            try:
                self.assertEqual(dialog.table.rowCount(), 1)
                debug.set_project_profiles(store.list(), store.default_id())
                monitor.set_project_profiles(store.list(), store.default_id())
                self.assertEqual(debug.local_project_combo.currentData(), project.project_id)
                self.assertEqual(debug.local_elf.text(), str(project.symbols))
                self.assertEqual(monitor.project_selector.currentData(), project.project_id)
                self.assertEqual(monitor.symbol_path.text(), str(project.symbols))
            finally:
                dialog.deleteLater(); debug.deleteLater(); monitor.deleteLater(); self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
