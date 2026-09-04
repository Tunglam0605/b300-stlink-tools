"""v0.17 production Debug Studio with a dockable IDE-style workstation.

The legacy DebugTab implementation owns the proven controller/session lifecycle.
This version swaps only the Interactive Debug presentation during construction,
then installs the real v0.16 Target/SVD/FreeRTOS/Fault panes into the new dockable
workbench.  The normal zero-halt Live Monitor remains owned by the Studio page.
"""

from __future__ import annotations

from . import debug_tab as _debug_tab_module
from .debug_ide_workbench import DebugIdeWorkstationWidget
from .debug_intelligence_tabs import (
    FaultAnalysisPane,
    FreeRtosPane,
    PeripheralInspectorPane,
    TargetSummaryPane,
)
from .debug_tab_v160 import DebugTabV160


class DebugTabV170(DebugTabV160):
    """v0.17 Debug Studio using the Keil/STM-Studio-style dockable workbench."""

    def __init__(self, *args, **kwargs) -> None:
        # DebugTab predates the versioned workbench factory. Construction is
        # single-threaded on the Qt GUI thread, so temporarily replace its module
        # symbol while the inherited widget tree is built. Restore it immediately
        # afterwards so compatibility tests/legacy consumers remain unchanged.
        previous = _debug_tab_module.DebugWorkstationWidget
        _debug_tab_module.DebugWorkstationWidget = DebugIdeWorkstationWidget
        try:
            super().__init__(*args, **kwargs)
        finally:
            _debug_tab_module.DebugWorkstationWidget = previous

        self._restore_v170_layout()

    def _replace_tab_widget(self, tabs, placeholder, widget, title: str) -> None:
        index = tabs.indexOf(placeholder)
        if index >= 0:
            tabs.removeTab(index)
            placeholder.setParent(None)
            placeholder.deleteLater()
            tabs.insertTab(index, widget, title)
        else:
            tabs.addTab(widget, title)

    def _install_intelligence_tabs(self) -> None:
        """Install real target-aware panes into the v0.17 IDE dock locations."""
        workstation = self.workstation

        self.target_awareness_pane = TargetSummaryPane(workstation.bottom_tabs)
        self.peripheral_pane = PeripheralInspectorPane(workstation.right_tabs)
        self.freertos_pane = FreeRtosPane(workstation.bottom_tabs)
        self.fault_pane = FaultAnalysisPane(workstation.bottom_tabs)

        self._replace_tab_widget(
            workstation.right_tabs,
            workstation.peripherals_placeholder,
            self.peripheral_pane,
            "Peripherals",
        )
        self._replace_tab_widget(
            workstation.bottom_tabs,
            workstation.rtos_tab,
            self.freertos_pane,
            "FreeRTOS",
        )
        self._replace_tab_widget(
            workstation.bottom_tabs,
            workstation.fault_tab,
            self.fault_pane,
            "Fault",
        )
        workstation.bottom_tabs.addTab(self.target_awareness_pane, "Target")

        self.target_awareness_pane.refresh_requested.connect(self._v160_refresh_target)
        self.peripheral_pane.load_svd_requested.connect(self._v160_choose_svd)
        self.peripheral_pane.inspect_requested.connect(self._v160_inspect_register)
        self.freertos_pane.refresh_requested.connect(self._v160_refresh_freertos)
        self.fault_pane.analyze_requested.connect(self._v160_analyze_fault)

    def _restore_v170_layout(self) -> None:
        settings = getattr(self, "_settings", None)
        if settings is None:
            return
        try:
            state = settings.value("debug/v17/workbenchState")
            if state:
                self.workstation.restore_layout_state(state)
        except Exception:
            # A stale/corrupt layout must never block the debugger from opening.
            self.workstation.reset_default_layout()

    def _save_v170_layout(self) -> None:
        settings = getattr(self, "_settings", None)
        if settings is None:
            return
        try:
            settings.setValue("debug/v17/workbenchState", self.workstation.save_layout_state())
        except Exception:
            pass

    def prepare_shutdown(self) -> None:
        self._save_v170_layout()
        super().prepare_shutdown()


__all__ = ["DebugTabV170"]
