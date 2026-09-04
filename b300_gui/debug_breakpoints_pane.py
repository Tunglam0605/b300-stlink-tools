"""Breakpoint & Watchpoint Manager Pane."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .debug_view_models import DebugBreakpoint


class DebugBreakpointsPane(QWidget):
    """Compact Breakpoints & Watchpoints manager pane."""

    add_requested = Signal()
    toggle_requested = Signal(int, bool)  # number, enabled
    delete_requested = Signal(int)        # number
    create_hardware_breakpoint_requested = Signal(str)  # location
    create_watchpoint_requested = Signal(str)           # expression
    set_breakpoint_enabled_requested = Signal(int, bool)# number, enabled
    delete_breakpoint_requested = Signal(int)           # number

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugBreakpointsPane")
        self._breakpoints: List[DebugBreakpoint] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header with compact status and action buttons
        header = QFrame(self)
        header.setObjectName("debugBreakpointsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        title = QLabel("BREAKPOINTS")
        title.setObjectName("debugBreakpointsTitle")
        title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94A3B8; letter-spacing: 0.5px;")
        header_layout.addWidget(title)

        self.status_badge = QLabel("BP 0/6 · WP 0/4")
        self.status_badge.setObjectName("debugBreakpointsStatusBadge")
        self.status_badge.setStyleSheet("font-size: 10px; font-family: monospace; color: #64748B;")
        header_layout.addWidget(self.status_badge)
        header_layout.addStretch(1)

        self.btn_add = QPushButton("+")
        self.btn_add.setObjectName("debugBpAddBtn")
        self.btn_add.setToolTip("Thêm Breakpoint / Watchpoint")
        self.btn_add.setMaximumWidth(26)
        self.btn_add.clicked.connect(self.add_requested.emit)
        header_layout.addWidget(self.btn_add)

        self.btn_toggle = QPushButton("Toggle")
        self.btn_toggle.setObjectName("debugBpToggleBtn")
        self.btn_toggle.setToolTip("Bật / tắt điểm dừng đang chọn")
        self.btn_toggle.clicked.connect(self._on_toggle_selected)
        header_layout.addWidget(self.btn_toggle)

        self.btn_delete = QPushButton("Xóa")
        self.btn_delete.setObjectName("debugBpDeleteBtn")
        self.btn_delete.setToolTip("Xóa điểm dừng đang chọn")
        self.btn_delete.clicked.connect(self._on_delete_selected)
        header_layout.addWidget(self.btn_delete)

        layout.addWidget(header)

        # Table
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("debugBreakpointsTable")
        self.table.setHorizontalHeaderLabels(("State", "#", "Type", "Location", "Address", "Hits"))
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)

        mono_font = QFont("Consolas", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.table.setFont(mono_font)

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.table, 1)

    def update_usage(self, bp_used: int, bp_max: int = 6, wp_used: int = 0, wp_max: int = 4) -> None:
        self.status_badge.setText(f"BP {bp_used}/{bp_max} · WP {wp_used}/{wp_max}")

    def set_breakpoints(self, breakpoints: Sequence[DebugBreakpoint]) -> None:
        self._breakpoints = list(breakpoints)
        self.table.setRowCount(len(self._breakpoints))

        bp_count = sum(1 for b in self._breakpoints if "break" in b.kind.lower() or "bp" in b.kind.lower())
        wp_count = sum(1 for b in self._breakpoints if "watch" in b.kind.lower() or "wp" in b.kind.lower())
        self.update_usage(bp_count, 6, wp_count, 4)

        for row, bp in enumerate(self._breakpoints):
            item_state = QTableWidgetItem("●" if bp.enabled else "○")
            item_state.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_state.setForeground(QColor("#EF4444") if bp.enabled else QColor("#64748B"))

            item_num = QTableWidgetItem(str(bp.number))
            item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_kind = QTableWidgetItem(bp.kind)
            item_loc = QTableWidgetItem(bp.location)
            item_addr = QTableWidgetItem(bp.address)
            item_hits = QTableWidgetItem(str(bp.hit_count))
            item_hits.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            for item in (item_state, item_num, item_kind, item_loc, item_addr, item_hits):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            self.table.setItem(row, 0, item_state)
            self.table.setItem(row, 1, item_num)
            self.table.setItem(row, 2, item_kind)
            self.table.setItem(row, 3, item_loc)
            self.table.setItem(row, 4, item_addr)
            self.table.setItem(row, 5, item_hits)

    def _on_toggle_selected(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            if 0 <= row < len(self._breakpoints):
                bp = self._breakpoints[row]
                self.toggle_requested.emit(bp.number, not bp.enabled)

    def _on_delete_selected(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            if 0 <= row < len(self._breakpoints):
                bp = self._breakpoints[row]
                self.delete_requested.emit(bp.number)
