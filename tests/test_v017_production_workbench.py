import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_gui.debug_ide_workbench import DebugIdeWorkstationWidget
from b300_gui.debug_tab_v170 import DebugTabV170
from b300_gui.main_window_v15 import MainWindowV15


class V017ProductionWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        return MainWindowV15(
            probe_loader=lambda: (),
            automatic_updates=False,
            first_run_setup=False,
        )

    def test_production_uses_v017_dockable_workbench(self) -> None:
        window = self._window()
        try:
            self.assertIsInstance(window.debug_tab, DebugTabV170)
            self.assertIsInstance(window.debug_tab.workstation, DebugIdeWorkstationWidget)
            self.assertEqual(window.debug_tab.workstation.right_tabs.tabText(0), "Watch 1")
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_real_intelligence_panes_replace_placeholders(self) -> None:
        window = self._window()
        try:
            tab = window.debug_tab
            right_labels = [
                tab.workstation.right_tabs.tabText(i)
                for i in range(tab.workstation.right_tabs.count())
            ]
            bottom_labels = [
                tab.workstation.bottom_tabs.tabText(i)
                for i in range(tab.workstation.bottom_tabs.count())
            ]
            self.assertIn("Peripherals", right_labels)
            self.assertIn("FreeRTOS", bottom_labels)
            self.assertIn("Fault", bottom_labels)
            self.assertIn("Target", bottom_labels)
            self.assertIs(tab.peripheral_pane, tab.workstation.right_tabs.widget(right_labels.index("Peripherals")))
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_zero_halt_live_monitor_stays_owned_by_normal_studio(self) -> None:
        window = self._window()
        try:
            tab = window.debug_tab
            self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
            self.assertGreaterEqual(tab.scroll_content.layout().indexOf(tab.live_panel), 0)
            tab.show_workstation()
            self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
            tab.show_setup()
            self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
