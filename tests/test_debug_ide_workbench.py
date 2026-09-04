import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDockWidget

from b300_gui.debug_ide_workbench import DebugIdeWorkstationWidget


class DebugIdeWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = DebugIdeWorkstationWidget()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def test_uses_dockable_ide_shell(self):
        docks = self.widget.findChildren(QDockWidget)
        names = {dock.objectName() for dock in docks}
        self.assertIn("debugNavigationDock", names)
        self.assertIn("debugInspectDock", names)
        self.assertIn("debugToolsDock", names)

    def test_center_is_source_first_and_keeps_debug_tabs(self):
        self.assertEqual(self.widget.editor_tabs.tabText(0), "Source")
        self.assertEqual(self.widget.editor_tabs.tabText(1), "Disassembly")
        self.assertEqual(self.widget.editor_tabs.tabText(2), "Trace")
        self.assertIs(self.widget.editor_tabs.widget(0), self.widget.source_view)

    def test_controller_compatibility_surface_is_present(self):
        for name in (
            "status_bar",
            "toolbar",
            "symbols_pane",
            "callstack_pane",
            "source_view",
            "variables_pane",
            "registers_pane",
            "breakpoints_pane",
            "live_layout",
            "btn_read_memory",
            "memory_addr_input",
            "memory_len_spin",
            "memory_view",
            "console_view",
            "log_view",
        ):
            self.assertTrue(hasattr(self.widget, name), name)

    def test_live_watch_host_is_not_normal_studio_live_panel(self):
        self.assertEqual(self.widget.bottom_tabs.tabText(1), "Live Watch")
        self.assertIs(self.widget.live_tab.layout(), self.widget.live_layout)

    def test_layout_can_be_saved_and_reset(self):
        state = self.widget.save_layout_state()
        self.assertFalse(state.isEmpty())
        self.widget.left_dock.hide()
        self.assertFalse(self.widget.left_dock.isVisible())
        self.widget.reset_default_layout()
        self.assertFalse(self.widget.save_layout_state().isEmpty())


if __name__ == "__main__":
    unittest.main()
