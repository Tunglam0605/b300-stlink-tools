"""Interactive intrusive debug panel for B300 STM32F407."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .collapsible_card import CollapsibleCard


class DebugInteractivePanel(CollapsibleCard):
    """GDB Interactive Debug panel with warning banner and intrusive control tools."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            "Interactive Debug (GDB / Source-Level)",
            "Step, halt, breakpoints & call stack",
            parent,
            expanded=False,
        )
        self._build_ui()

    def _build_ui(self) -> None:
        content_layout = self.content_layout
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(8)

        # Warning Amber Box
        warning_frame = QFrame()
        warning_frame.setObjectName("interactiveDebugWarning")
        warning_layout = QHBoxLayout(warning_frame)
        warning_layout.setContentsMargins(8, 6, 8, 6)
        warning_layout.setSpacing(8)

        warn_icon = QLabel("⚠")
        warn_icon.setStyleSheet("font-size: 16px; font-weight: 700; color: #D97706;")
        warning_layout.addWidget(warn_icon)

        warn_text = QLabel(
            "<b>Interactive Debug:</b> May halt the MCU and affect realtime control. "
            "Diagnostics will temporarily pause execution to inspect registers/stack before auto-resuming."
        )
        warn_text.setObjectName("interactiveDebugWarningText")
        warn_text.setWordWrap(True)
        warning_layout.addWidget(warn_text, 1)
        content_layout.addWidget(warning_frame)

        # Target Control Buttons Row (Halt, Continue, Reset, Step)
        ctrl_grid = QGridLayout()
        ctrl_grid.setHorizontalSpacing(8)
        ctrl_grid.setVerticalSpacing(6)

        self.halt_button = QPushButton("Tạm dừng (Halt)")
        self.halt_button.setObjectName("debugHaltButton")
        self.continue_button = QPushButton("Tiếp tục (Run)")
        self.continue_button.setObjectName("debugContinueButton")
        self.reset_button = QPushButton("Reset + Halt")
        self.reset_button.setObjectName("debugResetButton")
        self.step_into_button = QPushButton("Step Into")
        self.step_into_button.setObjectName("debugStepIntoButton")
        self.step_over_button = QPushButton("Step Over")
        self.step_over_button.setObjectName("debugStepOverButton")

        ctrl_grid.addWidget(self.halt_button, 0, 0)
        ctrl_grid.addWidget(self.continue_button, 0, 1)
        ctrl_grid.addWidget(self.reset_button, 0, 2)
        ctrl_grid.addWidget(self.step_into_button, 0, 3)
        ctrl_grid.addWidget(self.step_over_button, 0, 4)
        content_layout.addLayout(ctrl_grid)

        # Diagnostics Row (Where, Stack, Registers, Variable inspect)
        diag_grid = QGridLayout()
        diag_grid.setHorizontalSpacing(8)
        diag_grid.setVerticalSpacing(6)

        self.where_button = QPushButton("Vị trí (Where)")
        self.where_button.setObjectName("debugWhereButton")
        self.stack_button = QPushButton("Call Stack")
        self.stack_button.setObjectName("debugStackButton")
        self.registers_button = QPushButton("Registers")
        self.registers_button.setObjectName("debugRegistersButton")

        diag_grid.addWidget(self.where_button, 0, 0)
        diag_grid.addWidget(self.stack_button, 0, 1)
        diag_grid.addWidget(self.registers_button, 0, 2)

        diag_grid.addWidget(QLabel("Biến:"), 0, 3)
        self.variable_expression = QLineEdit()
        self.variable_expression.setObjectName("debugVariableExpression")
        self.variable_expression.setPlaceholderText("Ví dụ: bRUN, xTickCount")
        diag_grid.addWidget(self.variable_expression, 0, 4)

        self.variable_button = QPushButton("Đọc biến")
        self.variable_button.setObjectName("debugVariableButton")
        diag_grid.addWidget(self.variable_button, 0, 5)
        content_layout.addLayout(diag_grid)

        # One-shot Hardware Breakpoint / Watchpoint Row
        stop_actions = QGridLayout()
        stop_actions.setHorizontalSpacing(8)
        stop_actions.setVerticalSpacing(6)

        stop_actions.addWidget(QLabel("Breakpoint:"), 0, 0)
        self.break_location = QLineEdit()
        self.break_location.setObjectName("debugBreakLocation")
        self.break_location.setPlaceholderText("Hàm hoặc file.c:line")
        stop_actions.addWidget(self.break_location, 0, 1)

        self.break_once_button = QPushButton("Break Once")
        self.break_once_button.setObjectName("debugBreakOnceButton")
        self.break_once_button.setToolTip("Đặt hardware breakpoint một lần, chờ hit rồi tự xóa và Resume.")
        stop_actions.addWidget(self.break_once_button, 0, 2)

        stop_actions.addWidget(QLabel("Watch:"), 0, 3)
        self.watch_expression = QLineEdit()
        self.watch_expression.setObjectName("debugWatchExpression")
        self.watch_expression.setPlaceholderText("Biến cần watch")
        stop_actions.addWidget(self.watch_expression, 0, 4)

        self.watch_once_button = QPushButton("Watch Once")
        self.watch_once_button.setObjectName("debugWatchOnceButton")
        self.watch_once_button.setToolTip("Đặt hardware watchpoint một lần, chờ trigger rồi tự xóa và Resume.")
        stop_actions.addWidget(self.watch_once_button, 0, 5)

        stop_actions.addWidget(QLabel("Timeout:"), 0, 6)
        self.stop_timeout = QSpinBox()
        self.stop_timeout.setObjectName("debugStopTimeout")
        self.stop_timeout.setRange(1, 60)
        self.stop_timeout.setValue(5)
        self.stop_timeout.setSuffix(" s")
        stop_actions.addWidget(self.stop_timeout, 0, 7)

        content_layout.addLayout(stop_actions)

        # Stateful Debug Workspace. This reorganizes existing GDB diagnostics only;
        # it does not introduce any new target operation.
        workspace_status = QFrame()
        workspace_status.setObjectName("interactiveDebugWorkspaceStatus")
        workspace_status_layout = QHBoxLayout(workspace_status)
        workspace_status_layout.setContentsMargins(8, 5, 8, 5)
        workspace_status_layout.setSpacing(16)
        self.workspace_target_state = QLabel("Target: DISCONNECTED")
        self.workspace_target_state.setObjectName("debugWorkspaceTargetState")
        self.workspace_last_action = QLabel("Last action: —")
        self.workspace_last_action.setObjectName("debugWorkspaceLastAction")
        self.workspace_safety = QLabel("Mode: INTRUSIVE / GDB")
        self.workspace_safety.setObjectName("debugWorkspaceSafety")
        for label in (self.workspace_target_state, self.workspace_last_action, self.workspace_safety):
            label.setStyleSheet("color: #334155; font-size: 11px; font-weight: 600;")
        workspace_status_layout.addWidget(self.workspace_target_state)
        workspace_status_layout.addWidget(self.workspace_last_action, 1)
        workspace_status_layout.addWidget(self.workspace_safety)
        content_layout.addWidget(workspace_status)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("interactiveDebugWorkspaceTabs")
        self.location_view = self._make_workspace_view(
            "debugWorkspaceLocationView",
            "Current source location will appear after Where/stop diagnostics.",
        )
        self.stack_view = self._make_workspace_view(
            "debugWorkspaceStackView", "Call stack results will appear here."
        )
        self.registers_view = self._make_workspace_view(
            "debugWorkspaceRegistersView", "Register snapshots will appear here."
        )
        self.variables_view = self._make_workspace_view(
            "debugWorkspaceVariablesView", "Variable inspection results will appear here."
        )
        self.diagnostic_view = self._make_workspace_view(
            "debugDiagnosticView",
            "Other diagnostic results appear here. Interactive Debug may temporarily halt the target.",
        )
        self.workspace_tabs.addTab(self.location_view, "Current Location")
        self.workspace_tabs.addTab(self.stack_view, "Call Stack")
        self.workspace_tabs.addTab(self.registers_view, "Registers")
        self.workspace_tabs.addTab(self.variables_view, "Variables")
        self.workspace_tabs.addTab(self.diagnostic_view, "Diagnostic")
        content_layout.addWidget(self.workspace_tabs)

    @staticmethod
    def _make_workspace_view(object_name: str, placeholder: str) -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setObjectName(object_name)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setMinimumHeight(110)
        view.setMaximumHeight(190)
        view.setPlaceholderText(placeholder)
        return view

    def set_target_state(self, state: Optional[str]) -> None:
        normalized = (state or "").strip().lower()
        if normalized in {"running", "halted"}:
            label = normalized.upper()
        elif normalized == "unknown":
            label = "UNKNOWN"
        else:
            label = "DISCONNECTED"
        self.workspace_target_state.setText("Target: %s" % label)

    def set_last_action(self, action: str) -> None:
        selected = str(action).strip() or "—"
        self.workspace_last_action.setText("Last action: %s" % selected)

    def set_diagnostic_result(self, label: str, text: str, state: Optional[str] = None) -> None:
        """Route an existing diagnostic result into the workspace without new GDB traffic."""
        selected_label = str(label).strip() or "Diagnostic"
        rendered = str(text)
        mapping = {
            "Where": (self.location_view, 0),
            "Call Stack": (self.stack_view, 1),
            "Registers": (self.registers_view, 2),
            "Variable": (self.variables_view, 3),
        }
        view, index = mapping.get(selected_label, (self.diagnostic_view, 4))
        view.setPlainText(rendered)
        if view is not self.diagnostic_view:
            self.diagnostic_view.setPlainText(rendered)
        self.workspace_tabs.setCurrentIndex(index)
        self.set_last_action(selected_label)
        if state is not None:
            self.set_target_state(state)
