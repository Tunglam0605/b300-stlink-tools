"""v0.17 production Debug Studio with a dockable IDE-style workstation.

The legacy DebugTab implementation owns the proven controller/session lifecycle.
This version swaps only the Interactive Debug presentation during construction,
then installs the real v0.16 Target/SVD/FreeRTOS/Fault panes into the new dockable
workbench. The normal zero-halt Live Monitor remains owned by the Studio page.

v0.17.1 makes the IDE a first-class, directly visible Studio surface. Merely
opening the IDE is presentation-only; attaching Interactive Debug remains an
explicit user action and therefore cannot halt/reset the MCU just by changing
views.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

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

        self._install_v170_view_switch()
        self._install_v170_ide_entry_controls()
        self._restore_v170_layout()

        # v0.17.1 UX contract: engineers see the IDE shell immediately. This does
        # not call start_selected_mode(), start_local(), start_client(), OpenOCD,
        # GDB, TCL or any target command. Interactive attach remains explicit.
        self.show_workstation()
        self._sync_workstation_state()

    # ------------------------------------------------------------------
    # First-class Studio navigation (view selection only, never target control)
    # ------------------------------------------------------------------
    def _install_v170_view_switch(self) -> None:
        bar = QFrame(self)
        bar.setObjectName("debugV170ViewSwitch")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        title = QLabel("Studio Debug")
        title.setObjectName("debugV170ViewTitle")
        title.setStyleSheet("font-weight: 700;")
        layout.addWidget(title)

        self.v170_ide_button = QPushButton("Debug IDE")
        self.v170_ide_button.setObjectName("debugV170IdeButton")
        self.v170_ide_button.setCheckable(True)
        self.v170_ide_button.setToolTip(
            "Mở không gian Debug kiểu IDE. Chỉ mở giao diện, không HALT/RESET MCU."
        )
        self.v170_ide_button.clicked.connect(self.show_workstation)
        layout.addWidget(self.v170_ide_button)

        self.v170_realtime_button = QPushButton("Theo dõi realtime")
        self.v170_realtime_button.setObjectName("debugV170RealtimeButton")
        self.v170_realtime_button.setCheckable(True)
        self.v170_realtime_button.setToolTip(
            "Mở Live Monitor zero-halt để quan sát khi robot vẫn phải chạy realtime."
        )
        self.v170_realtime_button.clicked.connect(self.show_setup)
        layout.addWidget(self.v170_realtime_button)

        layout.addStretch(1)

        self.v170_setup_button = QPushButton("Kết nối / Cấu hình")
        self.v170_setup_button.setObjectName("debugV170SetupButton")
        self.v170_setup_button.setToolTip("Chọn LOCAL/CLIENT, AXF/ELF và cấu hình kết nối Debug.")
        self.v170_setup_button.clicked.connect(self.show_setup)
        layout.addWidget(self.v170_setup_button)

        root = self.layout()
        if root is not None:
            root.insertWidget(0, bar)
        self._v170_view_switch = bar

    def _install_v170_ide_entry_controls(self) -> None:
        """Expose deliberate attach/configure actions inside the IDE itself."""
        toolbar_layout = self.workstation.toolbar.layout()
        if toolbar_layout is None:
            return

        self.ide_connect_button = QPushButton("Kết nối Debug")
        self.ide_connect_button.setObjectName("debugIdeConnectButton")
        self.ide_connect_button.setToolTip(
            "Bắt đầu Interactive Debug. Thao tác này có thể HALT MCU; chỉ chạy khi bạn bấm nút."
        )
        self.ide_connect_button.clicked.connect(self.start_selected_mode)
        toolbar_layout.insertWidget(0, self.ide_connect_button)

        self.ide_configure_button = QPushButton("Cấu hình")
        self.ide_configure_button.setObjectName("debugIdeConfigureButton")
        self.ide_configure_button.setToolTip("Mở trang cấu hình kết nối và Theo dõi realtime.")
        self.ide_configure_button.clicked.connect(self.show_setup)
        toolbar_layout.insertWidget(1, self.ide_configure_button)

        self._sync_v170_connect_control()

    def _set_v170_view_state(self, ide: bool) -> None:
        ide_button = getattr(self, "v170_ide_button", None)
        realtime_button = getattr(self, "v170_realtime_button", None)
        if ide_button is not None:
            ide_button.setChecked(bool(ide))
        if realtime_button is not None:
            realtime_button.setChecked(not bool(ide))

    def show_workstation(self) -> None:
        """Show the IDE shell without starting or altering a debug session."""
        super().show_workstation()
        self._set_v170_view_state(True)
        self._sync_v170_connect_control()

    def show_setup(self) -> None:
        """Show the zero-halt Studio/setup surface and keep panel ownership stable."""
        super().show_setup()
        self._set_v170_view_state(False)
        self._sync_v170_connect_control()

    def _sync_v170_connect_control(self) -> None:
        button = getattr(self, "ide_connect_button", None)
        if button is None:
            return
        controller = getattr(self, "_workstation_controller", None)
        active = bool(controller is not None and getattr(controller, "interactive_active", False))
        busy = getattr(self, "_worker", None) is not None
        if active:
            button.setText("Debug đã kết nối")
            button.setEnabled(False)
        else:
            button.setText("Kết nối Debug")
            button.setEnabled(not busy and not bool(getattr(self, "_external_blocked", False)))

    def _sync_workstation_state(self) -> None:
        super()._sync_workstation_state()
        self._sync_v170_connect_control()

    # ------------------------------------------------------------------
    # v0.16 intelligence panes mounted into the IDE docks
    # ------------------------------------------------------------------
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
