"""Lazy Qt model for the offline DWARF variable catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt


@dataclass
class _Item:
    node: object
    parent: Optional["_Item"] = None
    children: List["_Item"] = field(default_factory=list)
    loaded: int = 0
    exhausted: bool = False


class VariableTreeModel(QAbstractItemModel):
    COLUMNS = ("Tên", "Kiểu", "Giá trị", "Địa chỉ", "Chất lượng", "Cập nhật")
    PAGE_SIZE = 100
    NodeRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._catalog = None
        self._roots: List[_Item] = []
        self._root_loaded = 0
        self._root_exhausted = True
        self._query = ""
        self._values = {}

    @property
    def catalog(self):
        return self._catalog

    def set_catalog(self, catalog) -> None:
        self.beginResetModel()
        self._catalog = catalog
        self._query = ""
        self._values.clear()
        nodes = catalog.roots("", 0, self.PAGE_SIZE) if catalog is not None else ()
        self._roots = [_Item(node) for node in nodes]
        self._root_loaded = len(nodes)
        self._root_exhausted = len(nodes) < self.PAGE_SIZE
        self.endResetModel()

    def search(self, query: str) -> None:
        self.beginResetModel()
        self._query = str(query).strip()
        nodes = self._catalog.roots(self._query, 0, self.PAGE_SIZE) if self._catalog is not None else ()
        self._roots = [_Item(node) for node in nodes]
        self._root_loaded = len(nodes)
        self._root_exhausted = len(nodes) < self.PAGE_SIZE
        self.endResetModel()

    def node_for_index(self, index: QModelIndex):
        item = index.internalPointer() if index.isValid() else None
        return item.node if item is not None else None

    def columnCount(self, _parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        if not parent.isValid():
            return len(self._roots)
        item = parent.internalPointer()
        return len(item.children)

    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        if row < 0 or column < 0 or column >= len(self.COLUMNS):
            return QModelIndex()
        items = self._roots if not parent.isValid() else parent.internalPointer().children
        if row >= len(items):
            return QModelIndex()
        return self.createIndex(row, column, items[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        item = index.internalPointer()
        parent_item = item.parent
        if parent_item is None:
            return QModelIndex()
        grandparent = parent_item.parent
        siblings = self._roots if grandparent is None else grandparent.children
        return self.createIndex(siblings.index(parent_item), 0, parent_item)

    def hasChildren(self, parent=QModelIndex()) -> bool:
        if not parent.isValid():
            return bool(self._roots) or not self._root_exhausted
        node = parent.internalPointer().node
        return bool(node.has_children)

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if self._catalog is None:
            return False
        if not parent.isValid():
            return not self._root_exhausted
        item = parent.internalPointer()
        return bool(item.node.has_children and not item.exhausted)

    def fetchMore(self, parent: QModelIndex) -> None:
        if not self.canFetchMore(parent):
            return
        if not parent.isValid():
            nodes = self._catalog.roots(self._query, self._root_loaded, self.PAGE_SIZE)
            if nodes:
                start = len(self._roots)
                self.beginInsertRows(QModelIndex(), start, start + len(nodes) - 1)
                self._roots.extend(_Item(node) for node in nodes)
                self.endInsertRows()
                self._root_loaded += len(nodes)
            self._root_exhausted = len(nodes) < self.PAGE_SIZE
            return
        item = parent.internalPointer()
        nodes = self._catalog.children(item.node.node_id, item.loaded, self.PAGE_SIZE)
        if nodes:
            start = len(item.children)
            self.beginInsertRows(parent.siblingAtColumn(0), start, start + len(nodes) - 1)
            item.children.extend(_Item(node, parent=item) for node in nodes)
            self.endInsertRows()
            item.loaded += len(nodes)
        item.exhausted = len(nodes) < self.PAGE_SIZE

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section] if 0 <= section < len(self.COLUMNS) else None
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer().node
        if role == self.NodeRole:
            return node
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.reason or node.path
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        value = self._values.get(node.node_id, ("—", "—", "—"))
        if index.column() == 0:
            return node.name
        if index.column() == 1:
            return node.type_name
        if index.column() == 2:
            return value[0]
        if index.column() == 3:
            return "—" if node.address is None else "0x%08X" % node.address
        if index.column() == 4:
            if node.availability == "watchable":
                return value[1] if value[1] != "—" else "Sẵn sàng"
            return "Chỉ duyệt" if node.has_children else "Không hỗ trợ"
        if index.column() == 5:
            return value[2]
        return None

    def update_live_value(self, live_value, elapsed_seconds: float) -> None:
        node_id = getattr(live_value, "node_id", None)
        if not node_id:
            return
        display = str(live_value.value)
        if getattr(live_value, "enum_label", None):
            display = "%s (%s)" % (live_value.enum_label, live_value.value)
        quality = "Nhất quán" if live_value.coherent else "Không nhất quán"
        self._values[node_id] = (display, quality, "%.3f s" % float(elapsed_seconds))
        index = self._index_for_node_id(node_id)
        if index.isValid():
            self.dataChanged.emit(index.siblingAtColumn(2), index.siblingAtColumn(5), [])

    def mark_values_stale(self, reason: str = "") -> None:
        """Preserve the last sample while making its expired quality explicit."""
        suffix = str(reason or "").strip()
        quality = "STALE" + (" · %s" % suffix if suffix else "")
        for node_id, (display, _old_quality, elapsed) in tuple(self._values.items()):
            self._values[node_id] = (display, quality, elapsed)
            index = self._index_for_node_id(node_id)
            if index.isValid():
                self.dataChanged.emit(index.siblingAtColumn(2), index.siblingAtColumn(5), [])

    def _index_for_node_id(self, node_id: str) -> QModelIndex:
        def find(items, parent=QModelIndex()):
            for row, item in enumerate(items):
                index = self.index(row, 0, parent)
                if item.node.node_id == node_id:
                    return index
                found = find(item.children, index)
                if found.isValid():
                    return found
            return QModelIndex()
        return find(self._roots)


__all__ = ["VariableTreeModel"]
