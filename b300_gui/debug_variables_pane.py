"""Keil-style Watch/Locals pane with recursive expandable variables."""

from __future__ import annotations

from typing import List, Optional, Sequence, Set

from PySide6.QtCore import QModelIndex, Qt, Signal
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
    """Hierarchical Watch tree for structs, unions, arrays and scalar variables.

    A node is expanded lazily through GDB/MI ``-var-list-children``. Expansion
    state is retained across debugger snapshot refreshes so nested structs remain
    open like Keil Watch windows instead of collapsing after every HALT/step.
    """

    variable_write_requested = Signal(str, str, str)  # id, name, new_value
    add_watch_requested = Signal(str)                 # expression
    request_children = Signal(str)                    # variable_id (lazy load)
    children_requested = Signal(str)                  # compatibility alias

    def __init__(self, parent: Optional[QWidget] = None, *, title: str = "LOCALS / WATCH") -> None:
        super().__init__(parent)
        self.setObjectName("debugVariablesPane")
        self._title = str(title or "WATCH 1")
        self.model = VariablesTreeModel(self)
        self.model.variable_value_changed.connect(self.variable_write_requested.emit)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QFrame(self)
        header.setObjectName("debugVariablesHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        self.title_label = QLabel(self._title)
        self.title_label.setObjectName("debugVariablesTitle")
        self.title_label.setStyleSheet("font-size: 11px; font-weight: 800; color: #94A3B8; letter-spacing: 0.5px;")
        header_layout.addWidget(self.title_label)

        self.watch_input = QLineEdit()
        self.watch_input.setObjectName("debugWatchInput")
        self.watch_input.setPlaceholderText("+ Add Watch expression…")
        self.watch_input.setToolTip(
            "Thêm biến/biểu thức. Struct, union và array có thể mở rộng để xem từng member."
        )
        self.watch_input.returnPressed.connect(self._on_add_watch)
        header_layout.addWidget(self.watch_input, 1)

        self.btn_add_watch = QPushButton("+")
        self.btn_add_watch.setObjectName("debugAddWatchButton")
        self.btn_add_watch.setToolTip("Thêm biểu thức theo dõi")
        self.btn_add_watch.setMaximumWidth(28)
        self.btn_add_watch.clicked.connect(self._on_add_watch)
        header_layout.addWidget(self.btn_add_watch)

        layout.addWidget(header)

        self.tree = QTreeView()
        self.tree.setObjectName("debugVariablesTree")
        self.tree.setModel(self.model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setIndentation(16)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )

        mono_font = QFont("Consolas", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.tree.setFont(mono_font)

        header_view = self.tree.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header_view.resizeSection(0, 190)
        header_view.resizeSection(1, 130)
        header_view.resizeSection(3, 110)

        # Match Keil Watch's primary three-column presentation. Address remains
        # available programmatically and can be exposed later from the View menu.
        self.tree.setColumnHidden(3, True)

        self.tree.expanded.connect(self._on_node_expanded)
        layout.addWidget(self.tree, 1)

    def set_target_state(self, state: str, interactive_connected: bool = True) -> None:
        """Update target state so that in-place editing is strictly disabled when RUNNING."""
        self.model.set_target_state(state, interactive_connected)

    def _iter_name_indexes(self, parent: QModelIndex = QModelIndex()):
        for row in range(self.model.rowCount(parent)):
            index = self.model.index(row, 0, parent)
            if not index.isValid():
                continue
            yield index
            yield from self._iter_name_indexes(index)

    def expanded_variable_ids(self) -> Set[str]:
        expanded: Set[str] = set()
        for index in self._iter_name_indexes():
            if self.tree.isExpanded(index):
                node = index.internalPointer()
                node_id = getattr(node, "id", "")
                if node_id:
                    expanded.add(str(node_id))
        return expanded

    def _restore_expansion(self, expanded_ids: Set[str]) -> None:
        if not expanded_ids:
            return
        self.tree.blockSignals(True)
        try:
            for variable_id in expanded_ids:
                index = self.model.index_for_id(variable_id)
                if index.isValid():
                    self.tree.setExpanded(index, True)
        finally:
            self.tree.blockSignals(False)

    def set_variables(self, nodes: List[DebugVariableNode]) -> None:
        """Refresh roots while preserving opened struct/array branches."""
        expanded = self.expanded_variable_ids()
        self.model.set_root_nodes(nodes)
        self._restore_expansion(expanded)

    def insert_children(self, parent_id: str, children: Sequence[DebugVariableNode]) -> None:
        """Insert lazily loaded members without collapsing the selected parent."""
        was_expanded = parent_id in self.expanded_variable_ids()
        if self.model.insert_children(parent_id, children):
            index = self.model.index_for_id(parent_id)
            if index.isValid() and (was_expanded or children):
                self.tree.setExpanded(index, True)

    def show_address_column(self, visible: bool) -> None:
        self.tree.setColumnHidden(3, not bool(visible))

    def _on_node_expanded(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        node = index.internalPointer()
        if getattr(node, "has_children", False) and not getattr(node, "children_loaded", False):
            self.request_children.emit(node.id)
            self.children_requested.emit(node.id)

    def _on_add_watch(self) -> None:
        expr = self.watch_input.text().strip()
        if expr:
            self.add_watch_requested.emit(expr)
            self.watch_input.clear()
