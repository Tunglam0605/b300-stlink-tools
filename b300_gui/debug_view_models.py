"""Debugger data contracts and ViewModel adapters for B300 Debug Workstation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
)


@dataclass
class RemoteSessionState:
    """State of an SSH/Remote gateway session."""
    status: str = "disconnected"  # "disconnected" | "connecting" | "connected" | "error"
    host: str = ""
    user: str = ""
    port: int = 22
    error_message: Optional[str] = None


@dataclass
class DebugConnectionState:
    """Consolidated connection & runtime metrics for the top status bar."""
    mode: str = "local"  # "local" | "gateway" | "client"
    ssh: bool = False
    gdb: bool = False
    tcl: bool = False
    target: str = "DISCONNECTED"  # "RUNNING" | "HALTED" | "DISCONNECTED" | "UNKNOWN"
    pc: str = "—"
    sample_rate: str = "—"
    error_state: Optional[str] = None


@dataclass
class DebugFrame:
    """Stack frame in call stack view."""
    level: int
    function: str
    file: str
    line: int
    address: str


@dataclass
class DebugVariableNode:
    """Node in hierarchical variable tree (structs, arrays, primitives)."""
    id: str
    name: str
    value: str = ""
    type: str = ""
    address: str = ""
    editable: bool = False
    has_children: bool = False
    children_loaded: bool = False
    changed: bool = False
    in_scope: bool = True
    children: List["DebugVariableNode"] = field(default_factory=list)
    parent: Optional["DebugVariableNode"] = None

    def add_child(self, child: "DebugVariableNode") -> None:
        child.parent = self
        self.children.append(child)
        self.has_children = True
        self.children_loaded = True


@dataclass
class DebugRegister:
    """CPU Register snapshot entry."""
    name: str
    value: str
    changed: bool = False


@dataclass
class DebugBreakpoint:
    """Breakpoint / Watchpoint item."""
    number: int
    enabled: bool = True
    kind: str = "HW BP"  # "HW BP" | "WATCH"
    location: str = ""
    address: str = ""
    hit_count: int = 0


@dataclass
class SourceLocation:
    """Source file location for editor navigation."""
    file: str
    line: int
    address: str = ""


class VariablesTreeModel(QAbstractItemModel):
    """Hierarchical QAbstractItemModel for Locals & Watch variables.

    Strictly enforces: Value editing is ONLY enabled when MCU is HALTED
    and the variable node is marked editable.

    Expanded struct/array children are cached across halted-snapshot refreshes so
    a Keil-style Watch tree does not collapse every time the debugger refreshes.
    """

    variable_value_changed = Signal(str, str, str)  # id, name, new_value

    COLUMNS = ("Name", "Value", "Type", "Address")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._root_nodes: List[DebugVariableNode] = []
        self._target_state: str = "DISCONNECTED"
        self._interactive_connected: bool = False

    def set_target_state(self, state: str, interactive_connected: bool = True) -> None:
        normalized = (state or "").strip().upper()
        if self._target_state != normalized or self._interactive_connected != interactive_connected:
            self._target_state = normalized
            self._interactive_connected = interactive_connected
            if self._root_nodes:
                self.dataChanged.emit(
                    self.index(0, 1),
                    self.index(len(self._root_nodes) - 1, 1),
                    [Qt.ItemDataRole.DisplayRole],
                )

    def _collect_nodes(self) -> Dict[str, DebugVariableNode]:
        found: Dict[str, DebugVariableNode] = {}

        def visit(node: DebugVariableNode) -> None:
            found[node.id] = node
            for child in node.children:
                visit(child)

        for root in self._root_nodes:
            visit(root)
        return found

    def _restore_cached_children(
        self, node: DebugVariableNode, cached: Dict[str, DebugVariableNode]
    ) -> None:
        previous = cached.get(node.id)
        if previous is not None and previous.children_loaded:
            node.children = list(previous.children)
            node.children_loaded = True
            node.has_children = bool(node.children) or node.has_children
        for child in node.children:
            child.parent = node
            self._restore_cached_children(child, cached)

    def set_root_nodes(self, nodes: List[DebugVariableNode]) -> None:
        cached = self._collect_nodes()
        self.beginResetModel()
        self._root_nodes = list(nodes)
        for node in self._root_nodes:
            self._restore_cached_children(node, cached)
            self._bind_parents(node)
        self.endResetModel()

    def set_variables(self, nodes: List[DebugVariableNode]) -> None:
        """Alias for set_root_nodes for test and controller compatibility."""
        self.set_root_nodes(nodes)

    def insert_children(self, parent_id: str, children: Sequence[DebugVariableNode]) -> bool:
        """Insert lazily-loaded children without resetting/collapsing the whole tree."""
        target_node, parent_index = self._find_node_by_id(parent_id)
        if target_node is None or not parent_index.isValid():
            return False

        old_count = len(target_node.children)
        if old_count:
            self.beginRemoveRows(parent_index, 0, old_count - 1)
            target_node.children = []
            self.endRemoveRows()

        incoming = list(children)
        if incoming:
            self.beginInsertRows(parent_index, 0, len(incoming) - 1)
            target_node.children = incoming
            for child in target_node.children:
                child.parent = target_node
                self._bind_parents(child)
            self.endInsertRows()

        target_node.children_loaded = True
        target_node.has_children = bool(incoming)
        self.dataChanged.emit(parent_index, parent_index, [Qt.ItemDataRole.DisplayRole])
        return True

    def index_for_id(self, node_id: str) -> QModelIndex:
        """Return the Name-column index for a variable-object id."""
        _node, index = self._find_node_by_id(node_id)
        return index

    def _find_node_by_id(self, node_id: str) -> tuple[Optional[DebugVariableNode], QModelIndex]:
        for row, root in enumerate(self._root_nodes):
            if root.id == node_id:
                return root, self.createIndex(row, 0, root)
            res = self._find_child_by_id(root, node_id)
            if res[0] is not None:
                return res
        return None, QModelIndex()

    def _find_child_by_id(self, parent: DebugVariableNode, node_id: str) -> tuple[Optional[DebugVariableNode], QModelIndex]:
        for row, child in enumerate(parent.children):
            if child.id == node_id:
                return child, self.createIndex(row, 0, child)
            res = self._find_child_by_id(child, node_id)
            if res[0] is not None:
                return res
        return None, QModelIndex()

    def _bind_parents(self, node: DebugVariableNode) -> None:
        for child in node.children:
            child.parent = node
            self._bind_parents(child)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return len(self._root_nodes) > 0
        node = parent.internalPointer()
        if isinstance(node, DebugVariableNode):
            if node.children:
                return True
            return getattr(node, "has_children", False)
        return False

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._root_nodes)
        node = parent.internalPointer()
        if isinstance(node, DebugVariableNode):
            return len(node.children)
        return 0

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            if 0 <= row < len(self._root_nodes):
                return self.createIndex(row, column, self._root_nodes[row])
            return QModelIndex()
        parent_node = parent.internalPointer()
        if isinstance(parent_node, DebugVariableNode) and 0 <= row < len(parent_node.children):
            return self.createIndex(row, column, parent_node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        child_node = index.internalPointer()
        if not isinstance(child_node, DebugVariableNode) or child_node.parent is None:
            return QModelIndex()
        parent_node = child_node.parent
        grandparent = parent_node.parent
        if grandparent is None:
            row = self._root_nodes.index(parent_node) if parent_node in self._root_nodes else 0
        else:
            row = grandparent.children.index(parent_node) if parent_node in grandparent.children else 0
        return self.createIndex(row, 0, parent_node)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        node = index.internalPointer()
        if not isinstance(node, DebugVariableNode):
            return None

        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            if col == 0:
                return node.name
            elif col == 1:
                return node.value
            elif col == 2:
                return node.type
            elif col == 3:
                return node.address
        elif role == Qt.ItemDataRole.ToolTipRole:
            return f"{node.name} ({node.type}) at {node.address}: {node.value}"
        elif role == Qt.ItemDataRole.UserRole:
            return node
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        node = index.internalPointer()
        if (
            index.column() == 1
            and isinstance(node, DebugVariableNode)
            and node.editable
            and self._interactive_connected
            and self._target_state == "HALTED"
        ):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole or index.column() != 1:
            return False
        node = index.internalPointer()
        if not isinstance(node, DebugVariableNode):
            return False
        if not (self._interactive_connected and self._target_state == "HALTED" and node.editable):
            return False

        new_val = str(value).strip()
        if new_val != node.value:
            node.value = new_val
            node.changed = True
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
            self.variable_value_changed.emit(node.id, node.name, new_val)
            return True
        return False
