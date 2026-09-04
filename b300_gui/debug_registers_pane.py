"""Registers Pane displaying Cortex-M4 CPU core registers with changed highlight."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .debug_view_models import DebugRegister

DEFAULT_REGISTERS = (
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc", "xPSR"
)


class DebugRegistersPane(QWidget):
    """Structured table for CPU registers: Register | Value | Changed."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugRegistersPane")
        self._registers: Dict[str, DebugRegister] = {
            r: DebugRegister(name=r.upper(), value="0x00000000") for r in DEFAULT_REGISTERS
        }
        self._build_ui()
        self._populate_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QFrame(self)
        header.setObjectName("debugRegistersHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        title = QLabel("REGISTERS")
        title.setObjectName("debugRegistersTitle")
        title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94A3B8; letter-spacing: 0.5px;")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        layout.addWidget(header)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("debugRegistersTable")
        self.table.setHorizontalHeaderLabels(("Register", "Value", "Δ"))
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)

        mono_font = QFont("Consolas", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.table.setFont(mono_font)

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.table, 1)

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._registers))
        for row, (name, reg) in enumerate(self._registers.items()):
            item_name = QTableWidgetItem(reg.name)
            if reg.name in ("PC", "SP", "LR"):
                f = item_name.font()
                f.setBold(True)
                item_name.setFont(f)
                item_name.setForeground(QColor("#38BDF8"))  # Accent cyan/sky for key pointers

            item_val = QTableWidgetItem(reg.value)
            item_val.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if reg.changed:
                item_val.setForeground(QColor("#F59E0B"))  # Amber highlight for changed registers

            item_delta = QTableWidgetItem("●" if reg.changed else "")
            item_delta.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if reg.changed:
                item_delta.setForeground(QColor("#F59E0B"))

            for item in (item_name, item_val, item_delta):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_val)
            self.table.setItem(row, 2, item_delta)

    def set_registers(self, registers: Sequence[DebugRegister]) -> None:
        for r in registers:
            key = r.name.lower()
            old = self._registers.get(key)
            changed = old is not None and old.value != r.value
            self._registers[key] = DebugRegister(name=r.name.upper(), value=r.value, changed=changed)
        self._populate_table()
