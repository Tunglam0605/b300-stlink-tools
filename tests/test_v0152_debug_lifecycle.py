import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_service import DebugService
from b300_gui.debug_tab_v152 import DebugTabV152
from b300_version import __version__


class V0152DebugLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_tab(self) -> DebugTabV152:
        return DebugTabV152(
            DebugService(),
            selected_probe=lambda: None,
            settings=None,
            probe_count=lambda: 1,
        )

    def test_lifecycle_regression_remains_enabled_in_current_release(self) -> None:
        self.assertEqual(__version__, "0.17.0")

    def test_interactive_workstation_never_steals_realtime_panel(self) -> None:
        tab = self._make_tab()
        tab.show_setup()
        self.app.processEvents()

        setup_layout = tab.scroll_content.layout()
        self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
        self.assertGreaterEqual(setup_layout.indexOf(tab.live_panel), 0)

        tab.show_workstation()
        self.app.processEvents()

        self.assertIs(tab.main_stack.currentWidget(), tab.workstation)
        self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
        self.assertGreaterEqual(setup_layout.indexOf(tab.live_panel), 0)
        self.assertNotEqual(tab.workstation.live_layout.indexOf(tab.live_panel), 0)

        tab.show_setup()
        self.app.processEvents()
        self.assertIs(tab.main_stack.currentWidget(), tab.scroll_area)
        self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
        self.assertLess(
            setup_layout.indexOf(tab.live_panel),
            setup_layout.indexOf(tab.plot_panel),
        )
        tab.close()
        tab.deleteLater()
        self.app.processEvents()

    def test_show_setup_repairs_legacy_live_panel_reparenting(self) -> None:
        tab = self._make_tab()
        setup_layout = tab.scroll_content.layout()

        # Reproduce the v0.15.1 bug: moving the live monitor into the
        # Interactive Workstation removes it from the normal Studio page.
        tab.workstation.live_layout.addWidget(tab.live_panel)
        self.app.processEvents()
        self.assertIs(tab.live_panel.parentWidget(), tab.workstation.live_tab)

        tab.show_setup()
        self.app.processEvents()

        self.assertIs(tab.live_panel.parentWidget(), tab.scroll_content)
        self.assertGreaterEqual(setup_layout.indexOf(tab.live_panel), 0)
        self.assertLess(
            setup_layout.indexOf(tab.live_panel),
            setup_layout.indexOf(tab.plot_panel),
        )
        tab.close()
        tab.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()