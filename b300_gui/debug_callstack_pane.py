"""Structured Call Stack pane for GDB backtrace navigation."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
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

from .debug_view_models import DebugFrame


class DebugCallStackPane(QWidget):
    """Structured table displaying stack frames: # | Function | File | Line | Address."""

    frame_selected = Signal(object)  # DebugFrame

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugCallStackPane")
        self._frames: List[DebugFrame] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QFrame(self)
        header.setObjectName("debugCallStackHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        title = QLabel("CALL STACK")
        title.setObjectName("debugCallStackTitle")
        title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94A3B8; letter-spacing: 0.5px;")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        layout.addWidget(header)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("debugCallStackTable")
        self.table.setHorizontalHeaderLabels(("#", "Function", "File", "Line", "Address"))
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
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, 1)

    def set_frames(self, frames: Sequence[DebugFrame]) -> None:
        self._frames = list(frames)
        self.table.setRowCount(len(self._frames))
        for row, f in enumerate(self._frames):
            item_num = QTableWidgetItem(str(f.level))
            item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_fn = QTableWidgetItem(f.function)
            item_file = QTableWidgetItem(f.file)
            item_line = QTableWidgetItem(str(f.line) if f.line > 0 else "—")
            item_line.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_addr = QTableWidgetItem(f.address)

            for item in (item_num, item_fn, item_file, item_line, item_addr):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            self.table.setItem(row, 0, item_num)
            self.table.setItem(row, 1, item_fn)
            self.table.setItem(row, 2, item_file)
            self.table.setItem(row, 3, item_line)
            self.table.setItem(row, 4, item_addr)

        if self._frames:
            self.table.selectRow(0)

    def _on_selection_changed(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            if 0 <= row < len(self._frames):
                self.frame_selected.emit(self._frames[row])
