"""Interactive intrusive debug panel for B300 STM32F407."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .collapsible_card import CollapsibleCard


class DebugInteractivePanel(CollapsibleCard):
    """GDB Interactive Debug panel with warning banner and intrusive control tools."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            "Interactive Debug (GDB / Source-Level)",
            "Step, halt, breakpoints & call stack",
            parent,
            expanded=True,
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

        # Diagnostic output console
        self.diagnostic_view = QPlainTextEdit()
        self.diagnostic_view.setObjectName("debugDiagnosticView")
        self.diagnostic_view.setReadOnly(True)
        self.diagnostic_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diagnostic_view.setMinimumHeight(95)
        self.diagnostic_view.setMaximumHeight(160)
        self.diagnostic_view.setPlaceholderText(
            "Kết quả chẩn đoán hiển thị ở đây. Nếu target đang RUNNING, tool chỉ Halt tạm thời rồi tự Resume."
        )
        content_layout.addWidget(self.diagnostic_view)
