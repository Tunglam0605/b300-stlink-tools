"""Offline AXF/ELF typed variable browser for the zero-halt Monitor page."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTreeView, QVBoxLayout, QWidget,
)

from .variable_tree_model import VariableTreeModel


class VariableTreePanel(QFrame):
    add_watch_requested = Signal(str)
    load_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("engineeringCard")
        self.model = VariableTreeModel(self)
        self._source = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("CÂY BIẾN AXF/ELF")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self.load_button = QPushButton("Nạp lại DWARF")
        self.load_button.setToolTip("Chỉ đọc AXF/ELF offline; không truy cập hoặc halt target.")
        self.load_button.clicked.connect(self.load_requested.emit)
        header.addWidget(self.load_button)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm global/static theo tên, kiểu hoặc source…")
        self.search.setAccessibleName("Tìm biến typed trong AXF hoặc ELF")
        self.search.textChanged.connect(self.model.search)
        layout.addWidget(self.search)
        self.tree = QTreeView()
        self.tree.setObjectName("debugVariablesTree")
        self.tree.setModel(self.model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setIndentation(16)
        header_view = self.tree.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.model.COLUMNS)):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setColumnHidden(5, True)
        self.tree.expanded.connect(self._expanded)
        self.tree.selectionModel().selectionChanged.connect(self._selection_changed)
        self.tree.doubleClicked.connect(self._double_clicked)
        layout.addWidget(self.tree, 1)
        actions = QHBoxLayout()
        self.status = QLabel("Chọn project có AXF/ELF, rồi nạp catalog DWARF offline.")
        self.status.setWordWrap(True)
        actions.addWidget(self.status, 1)
        self.add_button = QPushButton("Thêm Watch Live")
        self.add_button.setObjectName("primaryActionButton")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_selected)
        actions.addWidget(self.add_button)
        layout.addLayout(actions)

    @property
    def catalog(self):
        return self.model.catalog

    def set_source(self, path) -> None:
        self._source = path
        if path is None:
            self.status.setText("Chọn project có AXF/ELF, rồi nạp catalog DWARF offline.")
        else:
            self.status.setText("Sẵn sàng đọc kiểu offline từ %s." % path.name)

    def set_catalog(self, catalog) -> None:
        self.model.set_catalog(catalog)
        if catalog is None:
            if self._source is None:
                self.status.setText("Chọn project có AXF/ELF, rồi nạp catalog DWARF offline.")
            else:
                self.status.setText("Sẵn sàng đọc kiểu offline từ %s." % self._source.name)
        else:
            self.status.setText("Đã nạp catalog typed · %d biến đầu tiên." % self.model.rowCount())
        self.add_button.setEnabled(False)

    def _expanded(self, index: QModelIndex) -> None:
        if self.model.canFetchMore(index):
            self.model.fetchMore(index)

    def _selected_node(self):
        return self.model.node_for_index(self.tree.currentIndex())

    def _selection_changed(self, *_args) -> None:
        node = self._selected_node()
        allowed = node is not None and node.watchable and not node.has_children
        self.add_button.setEnabled(allowed)
        if node is not None:
            self.status.setText(node.reason or "%s · %s · sẵn sàng theo dõi." % (node.path, node.value_type))

    def _add_selected(self) -> None:
        node = self._selected_node()
        if node is not None and node.watchable and not node.has_children:
            self.add_watch_requested.emit(node.node_id)

    def _double_clicked(self, index: QModelIndex) -> None:
        self.tree.setCurrentIndex(index)
        self._add_selected()


__all__ = ["VariableTreePanel"]
