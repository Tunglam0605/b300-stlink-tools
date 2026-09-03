"""Locals & Watch Pane with hierarchical expandable QTreeView and HALT-only editing."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .debug_view_models import DebugVariableNode, VariablesTreeModel


class DebugVariablesPane(QWidget):
    """Hierarchical Locals & Watch variables tree supporting in-place editing under HALT."""

    variable_write_requested = Signal(str, str, str)  # id, name, new_value
    add_watch_requested = Signal(str)                 # expression

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugVariablesPane")
        self.model = VariablesTreeModel(self)
        self.model.variable_value_changed.connect(self.variable_write_requested.emit)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header with Add Watch input
        header = QFrame(self)
        header.setObjectName("debugVariablesHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        title = QLabel("LOCALS / WATCH")
        title.setObjectName("debugVariablesTitle")
        title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94A3B8; letter-spacing: 0.5px;")
        header_layout.addWidget(title)

        self.watch_input = QLineEdit()
        self.watch_input.setObjectName("debugWatchInput")
        self.watch_input.setPlaceholderText("+ Add Watch expression…")
        self.watch_input.returnPressed.connect(self._on_add_watch)
        header_layout.addWidget(self.watch_input, 1)

        self.btn_add_watch = QPushButton("+")
        self.btn_add_watch.setObjectName("debugAddWatchButton")
        self.btn_add_watch.setToolTip("Thêm biểu thức theo dõi")
        self.btn_add_watch.setMaximumWidth(28)
        self.btn_add_watch.clicked.connect(self._on_add_watch)
        header_layout.addWidget(self.btn_add_watch)

        layout.addWidget(header)

        # Tree View
        self.tree = QTreeView()
        self.tree.setObjectName("debugVariablesTree")
        self.tree.setModel(self.model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)

        mono_font = QFont("Consolas", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.tree.setFont(mono_font)

        header_view = self.tree.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.tree, 1)

    def set_target_state(self, state: str, interactive_connected: bool = True) -> None:
        """Update target state so that in-place editing is strictly disabled when RUNNING."""
        self.model.set_target_state(state, interactive_connected)

    def set_variables(self, nodes: List[DebugVariableNode]) -> None:
        self.model.set_root_nodes(nodes)
        self.tree.expandAll()

    def _on_add_watch(self) -> None:
        expr = self.watch_input.text().strip()
        if expr:
            self.add_watch_requested.emit(expr)
            self.watch_input.clear()
