"""Dockable IDE-style Debug Workbench for B300 v0.17.

This module changes presentation only. It reuses the existing controller-facing
panes and keeps zero-halt monitoring ownership outside Interactive Debug.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .debug_breakpoints_pane import DebugBreakpointsPane
from .debug_callstack_pane import DebugCallStackPane
from .debug_registers_pane import DebugRegistersPane
from .debug_source_view import DebugSourceView
from .debug_symbols_pane import DebugSymbolsPane
from .debug_variables_pane import DebugVariablesPane
from .debug_view_models import DebugConnectionState
from .debug_workspace import DebugStatusBar, DebugToolbar


class DebugIdeWorkstationWidget(QWidget):
    """Keil/STM-Studio-inspired engineering workbench with dockable tool panes.

    Controller-facing attributes deliberately mirror ``DebugWorkstationWidget``
    so the backend/controller does not need to know which layout implementation
    is active.
    """

    frame_selected = Signal(int)
    request_variable_children = Signal(str)
    step_out_requested = Signal()

    _DOCK_FEATURES = (
        QDockWidget.DockWidgetFeature.DockWidgetClosable
        | QDockWidget.DockWidgetFeature.DockWidgetMovable
        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugIdeWorkstationWidget")
        self._default_layout_state = QByteArray()
        self._build_ui()
        self._wire_internal_navigation()
        self._default_layout_state = self.ide_window.saveState(1)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.ide_window = QMainWindow(self)
        self.ide_window.setObjectName("debugIdeShell")
        self.ide_window.setDockNestingEnabled(True)
        self.ide_window.setAnimated(False)
        self.ide_window.setStyleSheet(
            "QMainWindow#debugIdeShell { background: #111827; }"
            "QDockWidget { color: #CBD5E1; font-size: 11px; }"
            "QDockWidget::title { background: #172033; padding: 5px 8px; }"
            "QTabWidget::pane { border: 1px solid #2A3A52; }"
            "QTabBar::tab { padding: 5px 10px; min-height: 20px; }"
        )
        root.addWidget(self.ide_window, 1)

        self._build_menu()
        self._build_center()
        self._build_left_dock()
        self._build_right_dock()
        self._build_bottom_dock()

    def _build_menu(self) -> None:
        menu = self.ide_window.menuBar()
        menu.setNativeMenuBar(False)
        menu.setObjectName("debugIdeMenuBar")

        debug_menu = menu.addMenu("Debug")
        self.action_run = QAction("Run / Continue", self)
        self.action_halt = QAction("Halt", self)
        self.action_reset = QAction("Reset", self)
        self.action_step_in = QAction("Step Into", self)
        self.action_step_over = QAction("Step Over", self)
        self.action_step_out = QAction("Step Out", self)
        self.action_disconnect = QAction("Disconnect", self)
        for action in (
            self.action_run,
            self.action_halt,
            self.action_reset,
            self.action_step_in,
            self.action_step_over,
            self.action_step_out,
            self.action_disconnect,
        ):
            debug_menu.addAction(action)

        view_menu = menu.addMenu("View")
        self._view_menu = view_menu
        layout_menu = menu.addMenu("Window")
        self.action_reset_layout = QAction("Reset Debug Layout", self)
        self.action_reset_layout.triggered.connect(self.reset_default_layout)
        layout_menu.addAction(self.action_reset_layout)

    def _build_center(self) -> None:
        center = QWidget(self.ide_window)
        center.setObjectName("debugIdeCenter")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.status_bar = DebugStatusBar(center)
        layout.addWidget(self.status_bar)

        self.toolbar = DebugToolbar(center)
        layout.addWidget(self.toolbar)

        self.editor_tabs = QTabWidget(center)
        self.editor_tabs.setObjectName("debugEditorTabs")
        self.editor_tabs.setTabsClosable(False)
        self.editor_tabs.setMovable(True)
        self.editor_tabs.setDocumentMode(True)

        self.source_view = DebugSourceView(self.editor_tabs)
        self.editor_tabs.addTab(self.source_view, "Source")

        self.disassembly_view = QPlainTextEdit(self.editor_tabs)
        self.disassembly_view.setReadOnly(True)
        self.disassembly_view.setObjectName("debugDisassemblyView")
        self.disassembly_view.setPlainText(
            "; Disassembly view\n"
            "; Will be populated from GDB/ELF when a target is halted."
        )
        self.editor_tabs.addTab(self.disassembly_view, "Disassembly")

        self.trace_view = QPlainTextEdit(self.editor_tabs)
        self.trace_view.setReadOnly(True)
        self.trace_view.setObjectName("debugTraceView")
        self.trace_view.setPlainText(
            "; Native trace timeline\n"
            "; ITM/SWO events will appear here in the v0.17 native trace phase."
        )
        self.editor_tabs.addTab(self.trace_view, "Trace")

        layout.addWidget(self.editor_tabs, 1)
        self.ide_window.setCentralWidget(center)

        self.action_run.triggered.connect(self.toolbar.run_requested.emit)
        self.action_halt.triggered.connect(self.toolbar.halt_requested.emit)
        self.action_reset.triggered.connect(self.toolbar.reset_requested.emit)
        self.action_step_in.triggered.connect(self.toolbar.step_in_requested.emit)
        self.action_step_over.triggered.connect(self.toolbar.step_over_requested.emit)
        self.action_step_out.triggered.connect(self.toolbar.step_out_requested.emit)
        self.action_disconnect.triggered.connect(self.toolbar.disconnect_requested.emit)

    def _new_dock(self, title: str, name: str, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self.ide_window)
        dock.setObjectName(name)
        dock.setFeatures(self._DOCK_FEATURES)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.ide_window.addDockWidget(area, dock)
        self._view_menu.addAction(dock.toggleViewAction())
        return dock

    def _build_left_dock(self) -> None:
        self.left_dock = self._new_dock(
            "Navigation", "debugNavigationDock", Qt.DockWidgetArea.LeftDockWidgetArea
        )
        tabs = QTabWidget(self.left_dock)
        tabs.setObjectName("debugNavigationTabs")
        tabs.setDocumentMode(True)
        tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.symbols_pane = DebugSymbolsPane(tabs)
        self.callstack_pane = DebugCallStackPane(tabs)
        tabs.addTab(self.symbols_pane, "Symbols")
        tabs.addTab(self.callstack_pane, "Call Stack")
        self.left_tabs = tabs
        self.left_dock.setWidget(tabs)
        self.left_dock.setMinimumWidth(210)

    def _build_right_dock(self) -> None:
        self.right_dock = self._new_dock(
            "Watch / Inspect", "debugInspectDock", Qt.DockWidgetArea.RightDockWidgetArea
        )
        tabs = QTabWidget(self.right_dock)
        tabs.setObjectName("debugInspectTabs")
        tabs.setDocumentMode(True)

        # Watch 1 is intentionally the primary pane, matching desktop embedded
        # debuggers: Name | Value | Type with recursively expandable members.
        self.variables_pane = DebugVariablesPane(tabs, title="WATCH 1")
        self.registers_pane = DebugRegistersPane(tabs)
        tabs.addTab(self.variables_pane, "Watch 1")
        tabs.addTab(self.registers_pane, "CPU Registers")

        self.peripherals_placeholder = QPlainTextEdit(tabs)
        self.peripherals_placeholder.setReadOnly(True)
        self.peripherals_placeholder.setPlainText(
            "SVD Peripherals\n\n"
            "Registers are read lazily while the target is halted.\n"
            "This pane will host the v0.16 Target Awareness peripheral viewer."
        )
        tabs.addTab(self.peripherals_placeholder, "Peripherals")

        self.right_tabs = tabs
        self.right_dock.setWidget(tabs)
        self.right_dock.setMinimumWidth(340)

    def _build_bottom_dock(self) -> None:
        self.bottom_dock = self._new_dock(
            "Debug Tools", "debugToolsDock", Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.bottom_tabs = QTabWidget(self.bottom_dock)
        self.bottom_tabs.setObjectName("wsBottomTabs")
        self.bottom_tabs.setDocumentMode(True)
        self.bottom_tabs.setTabPosition(QTabWidget.TabPosition.South)

        self.breakpoints_pane = DebugBreakpointsPane(self.bottom_tabs)
        self.bottom_tabs.addTab(self.breakpoints_pane, "Breakpoints")

        self.live_tab = QWidget(self.bottom_tabs)
        self.live_tab.setObjectName("wsLiveTab")
        self.live_layout = QVBoxLayout(self.live_tab)
        self.live_layout.setContentsMargins(8, 8, 8, 8)
        self.live_expressions_tab = self.live_tab
        self.bottom_tabs.addTab(self.live_tab, "Live Watch")

        self.memory_tab = QWidget(self.bottom_tabs)
        self.memory_tab.setObjectName("wsMemoryTab")
        memory_layout = QVBoxLayout(self.memory_tab)
        memory_layout.setContentsMargins(6, 6, 6, 6)
        memory_layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Address:"))
        self.memory_addr_input = QLineEdit("0x20000000")
        self.memory_addr_input.setMaximumWidth(130)
        controls.addWidget(self.memory_addr_input)
        controls.addWidget(QLabel("Length:"))
        self.memory_len_spin = QSpinBox()
        self.memory_len_spin.setRange(16, 4096)
        self.memory_len_spin.setValue(256)
        self.memory_len_spin.setMaximumWidth(90)
        controls.addWidget(self.memory_len_spin)
        self.btn_read_memory = QPushButton("Read Memory")
        controls.addWidget(self.btn_read_memory)
        controls.addStretch(1)
        memory_layout.addLayout(controls)

        self.memory_view = QPlainTextEdit(self.memory_tab)
        self.memory_view.setReadOnly(True)
        memory_layout.addWidget(self.memory_view, 1)
        self.bottom_tabs.addTab(self.memory_tab, "Memory")

        self.console_tab = QWidget(self.bottom_tabs)
        console_layout = QVBoxLayout(self.console_tab)
        console_layout.setContentsMargins(4, 4, 4, 4)
        self.console_view = QPlainTextEdit(self.console_tab)
        self.console_view.setReadOnly(True)
        console_layout.addWidget(self.console_view, 1)
        self.bottom_tabs.addTab(self.console_tab, "Console")

        self.log_tab = QWidget(self.bottom_tabs)
        log_layout = QVBoxLayout(self.log_tab)
        log_layout.setContentsMargins(4, 4, 4, 4)
        self.log_view = QPlainTextEdit(self.log_tab)
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view, 1)
        self.bottom_tabs.addTab(self.log_tab, "Technical Log")

        self.fault_tab = QPlainTextEdit(self.bottom_tabs)
        self.fault_tab.setReadOnly(True)
        self.fault_tab.setPlainText("HardFault analysis will appear here when a fault snapshot is available.")
        self.bottom_tabs.addTab(self.fault_tab, "Fault")

        self.rtos_tab = QPlainTextEdit(self.bottom_tabs)
        self.rtos_tab.setReadOnly(True)
        self.rtos_tab.setPlainText("FreeRTOS task inspection will appear here after DWARF-backed task discovery.")
        self.bottom_tabs.addTab(self.rtos_tab, "FreeRTOS")

        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        for editor in (
            self.disassembly_view,
            self.trace_view,
            self.memory_view,
            self.console_view,
            self.log_view,
            self.fault_tab,
            self.rtos_tab,
        ):
            editor.setFont(mono)

        self.bottom_dock.setWidget(self.bottom_tabs)
        self.bottom_dock.setMinimumHeight(150)

        self.ide_window.resizeDocks([self.left_dock], [240], Qt.Orientation.Horizontal)
        self.ide_window.resizeDocks([self.right_dock], [360], Qt.Orientation.Horizontal)
        self.ide_window.resizeDocks([self.bottom_dock], [210], Qt.Orientation.Vertical)

    def _wire_internal_navigation(self) -> None:
        self.symbols_pane.symbol_activated.connect(self._on_symbol_activated)
        self.callstack_pane.frame_activated.connect(self._on_frame_selected)
        self.callstack_pane.frame_selected.connect(self.frame_selected.emit)
        self.variables_pane.request_children.connect(self.request_variable_children.emit)
        self.toolbar.step_out_requested.connect(self.step_out_requested.emit)

    def _on_symbol_activated(self, name: str, address: str, file_path: str, line: int) -> None:
        self.editor_tabs.setCurrentWidget(self.source_view)
        self.source_view.show_location(file_path, line, address, function=name)

    def _on_frame_selected(self, frame) -> None:
        self.editor_tabs.setCurrentWidget(self.source_view)
        self.source_view.show_location(frame.file, frame.line, frame.address, function=frame.function)
        if frame.line > 0:
            self.source_view.editor.set_frame_location(frame.line)

    def update_connection_state(self, state: DebugConnectionState) -> None:
        self.status_bar.update_state(state)
        self.toolbar.set_target_state(state.target)
        self.variables_pane.set_target_state(
            state.target,
            interactive_connected=state.gdb or state.tcl,
        )

    def reset_default_layout(self) -> None:
        if not self._default_layout_state.isEmpty():
            self.ide_window.restoreState(self._default_layout_state, 1)
        self.left_dock.show()
        self.right_dock.show()
        self.bottom_dock.show()

    def save_layout_state(self) -> QByteArray:
        return self.ide_window.saveState(1)

    def restore_layout_state(self, state: QByteArray) -> bool:
        if not state:
            return False
        return bool(self.ide_window.restoreState(state, 1))


__all__ = ["DebugIdeWorkstationWidget"]
