"""Offscreen contract tests for the compact production Pulse surface."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from b300_gui.pulse_widgets import PulseTitle

from b300_core.gateway_profiles import GatewayProfile
from b300_core.project_profiles import ProjectProfile
from b300_gui.pulse_view import PulseView
from b300_gui.app_context import AppContext


class PulseViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _context(self, *, projects=True, gateways=True):
        project = ProjectProfile("main", "Main", Path("C:/work"), Path("C:/work/main.axf"))
        gateway = GatewayProfile.create("Lab", "gateway.local", "admin", profile_id="lab")
        return SimpleNamespace(
            projects=(project,) if projects else (),
            gateways=(gateway,) if gateways else (),
            project_id="main" if projects else None,
            gateway_id="lab" if gateways else None,
        )

    def test_render_populates_real_profile_ids_and_emits_selection(self):
        view = PulseView()
        selected = []
        view.project_selected.connect(selected.append)
        view.render(self._context())
        self.assertEqual(view.debug_project.currentData(), "main")
        self.assertEqual(view.debug_connection.currentData(), "lab")
        self.assertEqual(view.debug_connection.count(), 2)
        view.debug_project.setCurrentIndex(0)
        self.assertEqual(selected, [])
        view.debug_project.activated.emit(0)
        self.assertEqual(selected, ["main"])

    def test_render_accepts_real_app_context_connection_ids(self):
        project = ProjectProfile("main", "Main", Path("C:/work"), Path("C:/work/main.axf"))
        gateway = GatewayProfile.create("Local Gateway", "gateway.local", "admin", profile_id="local")
        context = AppContext(); context.set_profiles((project,), (gateway,), "main", "local")
        view = PulseView(); view.render(context, ssh_connected=True)
        self.assertEqual(view.debug_project.currentData(), "main")
        self.assertEqual(view.debug_connection.currentData(), "gateway:local")
        self.assertIn("Local Gateway", view.connection_status.text())

    def test_empty_projects_offer_manager_and_program_state(self):
        view = PulseView()
        manager = []
        view.project_manager_requested.connect(lambda: manager.append(True))
        view.render(self._context(projects=False, gateways=False))
        self.assertFalse(view.debug_add_project.isHidden())
        self.assertFalse(view.debug_start.isEnabled())
        self.assertTrue(view.program_progress.isHidden())
        view.debug_add_project.click()
        self.assertEqual(manager, [True])

    def test_navigation_status_file_and_active_stop(self):
        view = PulseView()
        modes = []
        view.mode_changed.connect(modes.append)
        view.render(self._context(), debug_active=True, ssh_connected=True)
        view.set_mode("program")
        self.assertEqual(view.stack.currentIndex(), 1)
        self.assertEqual(modes, ["program"])
        self.assertFalse(view.stop_button.isHidden())
        self.assertFalse(view.setup_button.isEnabled())
        self.assertTrue(view.options_button.isEnabled())
        self.assertFalse(view.debug_project.isEnabled())
        view.set_file(Path("C:/very/long/path/main.hex"))
        self.assertEqual(view.program_file.toolTip(), str(Path("C:/very/long/path/main.hex")))
        view.set_status("Không thể kết nối", error=True)
        self.assertEqual(view.status.text(), "Không thể kết nối")
        self.assertEqual(view.program_status.text(), "Không thể kết nối")

    def test_actions_use_mode_keys(self):
        view = PulseView()
        actions = []
        view.action_requested.connect(actions.append)
        view.debug_start.click(); view.program_start.click()
        self.assertEqual(actions, ["debug", "program"])

    def test_debug_and_program_are_the_only_navigation_modes(self):
        view = PulseView()
        self.assertEqual(view.mode, "debug")
        self.assertEqual(list(view.nav), ["debug", "program"])
        self.assertEqual([item.text() for item in view.nav.values()], ["Debug", "Nạp code"])
        self.assertEqual(view.stack.count(), 2)

    def test_local_debug_is_available_without_saved_ssh_profiles(self):
        view = PulseView()
        view.render(self._context(gateways=False))
        self.assertEqual(view.debug_connection.currentData(), "local")
        self.assertEqual(view.debug_connection.currentText(), "Máy này")
        self.assertTrue(view.debug_start.isEnabled())
        self.assertIn("Máy này", view.connection_status.text())

    def test_empty_project_prompt_does_not_clip_debug_action_or_title(self):
        view = PulseView()
        view.render(self._context(projects=False, gateways=False))
        view.resize(1120, 720)
        view.show()
        try:
            self.app.processEvents()
            title = view.debug_page.findChild(PulseTitle)
            self.assertGreaterEqual(title.height(), title.minimumSizeHint().height())
            bottom = view.debug_start.mapTo(view.stack, view.debug_start.rect().bottomRight())
            self.assertTrue(view.stack.rect().contains(bottom), 'Debug action must fit inside the workspace')
        finally:
            view.close()
            view.deleteLater()
            self.app.processEvents()

    def test_pulse_title_survives_global_label_font_rule_after_show(self):
        previous = self.app.styleSheet()
        try:
            self.app.setStyleSheet("QLabel { font-size: 12px; }")
            view = PulseView(); view.resize(960, 640); view.show(); self.app.processEvents()
            title = view.findChild(PulseTitle)
            self.assertIsNotNone(title)
            self.assertEqual(title.font().pixelSize(), 30)
        finally:
            self.app.setStyleSheet(previous)
