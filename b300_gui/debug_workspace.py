"""Engineering Debug Workstation Shell for B300 ST-Link Tools."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
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


class DebugStatusBar(QFrame):
    """Data-dense status bar: Mode | SSH | GDB | TCL | MCU RUN/HALT | PC | Rate."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugWorkstationStatusBar")
        self.setStyleSheet("background: #0D1420; border-bottom: 1px solid #2A3A52; padding: 2px 8px;")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)

        mono_font = QFont("Consolas", 10)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)

        self.mode_badge = QLabel("LOCAL")
        self.mode_badge.setObjectName("statusModeBadge")
        self.mode_badge.setStyleSheet(
            "font-weight: 800; font-size: 11px; padding: 2px 6px; border-radius: 4px; "
            "background: #192237; color: #38BDF8; font-family: monospace;"
        )
        layout.addWidget(self.mode_badge)

        self.target_badge = QLabel("STM32F407")
        self.target_badge.setObjectName("statusTargetBadge")
        self.target_badge.setStyleSheet("font-size: 11px; color: #94A3B8; font-family: monospace;")
        layout.addWidget(self.target_badge)

        # Connection dots: SSH, GDB, TCL
        self.ssh_indicator = QLabel("SSH ●")
        self.ssh_indicator.setObjectName("statusSsh")
        self.ssh_indicator.setStyleSheet("font-size: 11px; font-family: monospace; color: #10B981;")
        layout.addWidget(self.ssh_indicator)

        self.gdb_indicator = QLabel("GDB ●")
        self.gdb_indicator.setObjectName("statusGdb")
        self.gdb_indicator.setStyleSheet("font-size: 11px; font-family: monospace; color: #10B981;")
        layout.addWidget(self.gdb_indicator)

        self.tcl_indicator = QLabel("TCL ●")
        self.tcl_indicator.setObjectName("statusTcl")
        self.tcl_indicator.setStyleSheet("font-size: 11px; font-family: monospace; color: #10B981;")
        layout.addWidget(self.tcl_indicator)

        # MCU State Badge (Large Emerald RUN / Amber HALT)
        self.mcu_state_badge = QLabel("MCU RUN")
        self.mcu_state_badge.setObjectName("statusMcuState")
        self.mcu_state_badge.setStyleSheet(
            "font-weight: 800; font-size: 11px; padding: 2px 8px; border-radius: 4px; "
            "background: #064E3B; color: #10B981; font-family: monospace;"
        )
        layout.addWidget(self.mcu_state_badge)

        # PC Register Value
        layout.addWidget(QLabel("PC:"))
        self.pc_val = QLabel("0x08010000")
        self.pc_val.setObjectName("statusPcVal")
        self.pc_val.setFont(mono_font)
        self.pc_val.setStyleSheet("font-weight: 700; color: #F1F5F9;")
        layout.addWidget(self.pc_val)

        # Sampling Rate / Status
        self.sample_rate_val = QLabel("10 Hz")
        self.sample_rate_val.setObjectName("statusSampleRate")
        self.sample_rate_val.setStyleSheet("font-size: 11px; color: #94A3B8; font-family: monospace;")
        layout.addWidget(self.sample_rate_val)

        layout.addStretch(1)

        # Error tag if present
        self.error_badge = QLabel("")
        self.error_badge.setObjectName("statusErrorBadge")
        self.error_badge.setStyleSheet(
            "font-weight: 800; font-size: 10px; color: #EF4444; font-family: monospace;"
        )
        self.error_badge.setVisible(False)
        layout.addWidget(self.error_badge)

    def update_state(self, state: DebugConnectionState) -> None:
        self.mode_badge.setText(state.mode.upper())
        self.ssh_indicator.setText("SSH ●" if state.ssh else "SSH ○")
        self.ssh_indicator.setStyleSheet(
            "font-size: 11px; font-family: monospace; color: %s;" % ("#10B981" if state.ssh else "#64748B")
        )
        self.ssh_indicator.setVisible(state.mode == "client")

        self.gdb_indicator.setText("GDB ●" if state.gdb else "GDB ○")
        self.gdb_indicator.setStyleSheet(
            "font-size: 11px; font-family: monospace; color: %s;" % ("#10B981" if state.gdb else "#64748B")
        )

        self.tcl_indicator.setText("TCL ●" if state.tcl else "TCL ○")
        self.tcl_indicator.setStyleSheet(
            "font-size: 11px; font-family: monospace; color: %s;" % ("#10B981" if state.tcl else "#64748B")
        )

        target_norm = (state.target or "").upper()
        if "RUN" in target_norm:
            self.mcu_state_badge.setText("MCU RUN")
            self.mcu_state_badge.setStyleSheet(
                "font-weight: 800; font-size: 11px; padding: 2px 8px; border-radius: 4px; "
                "background: #064E3B; color: #10B981; font-family: monospace;"
            )
        elif "HALT" in target_norm:
            self.mcu_state_badge.setText("MCU HALT")
            self.mcu_state_badge.setStyleSheet(
                "font-weight: 800; font-size: 11px; padding: 2px 8px; border-radius: 4px; "
                "background: #451A03; color: #F59E0B; font-family: monospace;"
            )
        else:
            self.mcu_state_badge.setText(target_norm or "DISCONNECTED")
            self.mcu_state_badge.setStyleSheet(
                "font-weight: 700; font-size: 11px; padding: 2px 8px; border-radius: 4px; "
                "background: #192237; color: #94A3B8; font-family: monospace;"
            )

        self.pc_val.setText(state.pc or "—")
        self.sample_rate_val.setText(str(state.sample_rate or "—"))

        if state.error_state:
            self.error_badge.setText(state.error_state.upper())
            self.error_badge.setVisible(True)
        else:
            self.error_badge.setVisible(False)


class DebugToolbar(QFrame):
    """Compact debugger toolbar: Run, Halt, Reset, Step In, Step Over, Step Out, Break, Disconnect."""

    run_requested = Signal()
    halt_requested = Signal()
    reset_requested = Signal()
    step_in_requested = Signal()
    step_over_requested = Signal()
    step_out_requested = Signal()
    break_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugWorkstationToolbar")
        self.setStyleSheet("background: #131A2A; border-bottom: 1px solid #2A3A52; padding: 3px 6px;")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        self.btn_run = QPushButton("▶ Run")
        self.btn_run.setObjectName("tbRun")
        self.btn_run.setToolTip("Tiếp tục chạy (Resume execution) — F5")
        self.btn_run.clicked.connect(self.run_requested.emit)
        layout.addWidget(self.btn_run)

        self.btn_halt = QPushButton("Ⅱ Halt")
        self.btn_halt.setObjectName("tbHalt")
        self.btn_halt.setToolTip("Tạm dừng CPU (Halt target) — F6")
        self.btn_halt.clicked.connect(self.halt_requested.emit)
        layout.addWidget(self.btn_halt)

        self.btn_reset = QPushButton("↺ Reset")
        self.btn_reset.setObjectName("tbReset")
        self.btn_reset.setToolTip("Reset vi điều khiển và tạm dừng — Shift+F5")
        self.btn_reset.clicked.connect(self.reset_requested.emit)
        layout.addWidget(self.btn_reset)

        layout.addSpacing(6)

        self.btn_step_in = QPushButton("↳ Step In")
        self.btn_step_in.setObjectName("tbStepIn")
        self.btn_step_in.setToolTip("Bước vào hàm (Step Into) — F11")
        self.btn_step_in.clicked.connect(self.step_in_requested.emit)
        layout.addWidget(self.btn_step_in)

        self.btn_step_over = QPushButton("↷ Step Over")
        self.btn_step_over.setObjectName("tbStepOver")
        self.btn_step_over.setToolTip("Bước qua dòng (Step Over) — F10")
        self.btn_step_over.clicked.connect(self.step_over_requested.emit)
        layout.addWidget(self.btn_step_over)

        self.btn_step_out = QPushButton("↰ Step Out")
        self.btn_step_out.setObjectName("tbStepOut")
        self.btn_step_out.setToolTip("Thoát khỏi hàm hiện tại (Step Out) — Shift+F11")
        self.btn_step_out.clicked.connect(self.step_out_requested.emit)
        layout.addWidget(self.btn_step_out)

        layout.addSpacing(6)

        self.btn_break = QPushButton("⏹ Break")
        self.btn_break.setObjectName("tbBreak")
        self.btn_break.setToolTip("Dừng một lần (Hardware Breakpoint một lần)")
        self.btn_break.clicked.connect(self.break_requested.emit)
        layout.addWidget(self.btn_break)

        layout.addStretch(1)

        self.safety_badge = QLabel("INTERACTIVE DEBUG ⚠ HALT CAPABLE")
        self.safety_badge.setObjectName("tbSafetyBadge")
        self.safety_badge.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #F59E0B; font-family: monospace; "
            "padding: 2px 6px; border: 1px solid #78350F; border-radius: 4px;"
        )
        self.safety_badge.setToolTip("Chế độ debug tương tác có thể tạm dừng MCU và ảnh hưởng điều khiển realtime.")
        layout.addWidget(self.safety_badge)

        self.btn_disconnect = QPushButton("🔌 Disconnect")
        self.btn_disconnect.setObjectName("tbDisconnect")
        self.btn_disconnect.setToolTip("Ngắt phiên debug")
        self.btn_disconnect.setStyleSheet("font-weight: 700; color: #EF4444;")
        self.btn_disconnect.clicked.connect(self.disconnect_requested.emit)
        layout.addWidget(self.btn_disconnect)

    def set_target_state(self, state: str) -> None:
        normalized = (state or "").lower()
        is_halted = "halt" in normalized
        is_running = "run" in normalized

        self.btn_run.setEnabled(is_halted)
        self.btn_halt.setEnabled(is_running)
        self.btn_step_in.setEnabled(is_halted)
        self.btn_step_over.setEnabled(is_halted)
        self.btn_step_out.setEnabled(is_halted)


class DebugWorkstationWidget(QWidget):
    """Full Engineering Debug Workstation container integrating 3 split columns and bottom dock tabs."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugWorkstationWidget")
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Top Status Bar
        self.status_bar = DebugStatusBar(self)
        main_layout.addWidget(self.status_bar)

        # 2. Debug Toolbar
        self.toolbar = DebugToolbar(self)
        main_layout.addWidget(self.toolbar)

        # 3. Main Workspace Splitters (Vertical split: Upper Panes / Bottom Tabs)
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.vertical_splitter.setObjectName("wsVerticalSplitter")

        # Upper Area: 3-column horizontal splitter
        self.horizontal_splitter = QSplitter(Qt.Orientation.Horizontal, self.vertical_splitter)
        self.horizontal_splitter.setObjectName("wsHorizontalSplitter")

        # Left Column: Symbols (top) & Call Stack (bottom)
        self.left_splitter = QSplitter(Qt.Orientation.Vertical, self.horizontal_splitter)
        self.symbols_pane = DebugSymbolsPane(self.left_splitter)
        self.callstack_pane = DebugCallStackPane(self.left_splitter)
        self.left_splitter.addWidget(self.symbols_pane)
        self.left_splitter.addWidget(self.callstack_pane)
        self.left_splitter.setSizes([300, 200])
        self.horizontal_splitter.addWidget(self.left_splitter)

        # Center Column: Source View (top) & Breakpoint Manager (bottom)
        self.center_splitter = QSplitter(Qt.Orientation.Vertical, self.horizontal_splitter)
        self.source_view = DebugSourceView(self.center_splitter)
        self.breakpoints_pane = DebugBreakpointsPane(self.center_splitter)
        self.center_splitter.addWidget(self.source_view)
        self.center_splitter.addWidget(self.breakpoints_pane)
        self.center_splitter.setSizes([420, 160])
        self.horizontal_splitter.addWidget(self.center_splitter)

        # Right Column: Locals/Watch (top) & Registers (bottom)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical, self.horizontal_splitter)
        self.variables_pane = DebugVariablesPane(self.right_splitter)
        self.registers_pane = DebugRegistersPane(self.right_splitter)
        self.right_splitter.addWidget(self.variables_pane)
        self.right_splitter.addWidget(self.registers_pane)
        self.right_splitter.setSizes([320, 240])
        self.horizontal_splitter.addWidget(self.right_splitter)

        # Proportionally size columns: Left ~24%, Center ~50%, Right ~26%
        self.horizontal_splitter.setSizes([260, 520, 280])
        self.vertical_splitter.addWidget(self.horizontal_splitter)

        # 4. Bottom Dock Tabs: Live Expressions, Memory, Console, Technical Log
        self.bottom_tabs = QTabWidget(self.vertical_splitter)
        self.bottom_tabs.setObjectName("wsBottomTabs")
        self.bottom_tabs.setTabPosition(QTabWidget.TabPosition.South)

        self.vertical_splitter.addWidget(self.bottom_tabs)
        self.vertical_splitter.setSizes([580, 200])

        main_layout.addWidget(self.vertical_splitter, 1)

        # Connect internal cross-pane navigation
        self.symbols_pane.symbol_activated.connect(self._on_symbol_activated)
        self.callstack_pane.frame_selected.connect(self._on_frame_selected)

    def _on_symbol_activated(self, name: str, address: str, file_path: str, line: int) -> None:
        self.source_view.show_location(file_path, line, address, function=name)

    def _on_frame_selected(self, frame) -> None:
        self.source_view.show_location(frame.file, frame.line, frame.address, function=frame.function)
        if frame.line > 0:
            self.source_view.editor.set_frame_location(frame.line)

    def update_connection_state(self, state: DebugConnectionState) -> None:
        self.status_bar.update_state(state)
        self.toolbar.set_target_state(state.target)
        self.variables_pane.set_target_state(state.target, interactive_connected=state.gdb or state.tcl)
