"""Production MainWindow variant that activates the v0.15 Debug Studio.

The base MainWindow remains import-compatible for the established regression suite.
The executable entrypoint uses MainWindowV15, which replaces only the Debug tab after
base construction; Flash, Factory, Memory, updater and Gateway Setup stay untouched.
"""

from __future__ import annotations

from .debug_tab_v15 import DebugTabV15
from .main_window import MainWindow


class MainWindowV15(MainWindow):
    """Main B300 window with the v0.15 engineering Debug Studio enabled."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        previous = self.debug_tab
        index = self.tabs.indexOf(previous)
        if index < 0:
            raise RuntimeError("Base Debug tab is missing from MainWindow.")

        try:
            previous.prepare_shutdown()
        except Exception:
            pass
        try:
            previous.log.disconnect()
            previous.operation_state_changed.disconnect()
        except Exception:
            pass

        self.tabs.removeTab(index)
        previous.setParent(None)
        previous.deleteLater()

        self.debug_tab = DebugTabV15(
            self.debug_service,
            self._selected_probe,
            self,
            settings=self.settings,
            probe_count=lambda: len(self._probes),
        )
        self.debug_tab.log.connect(self.append_log)
        self.debug_tab.operation_state_changed.connect(self._hardware_activity_changed)
        self.tabs.insertTab(index, self.debug_tab, "Debug")
