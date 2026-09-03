"""Engineering Symbols Pane for Functions, Globals, Statics, and Data."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class DebugSymbolsPane(QWidget):
    """Symbols inspection pane organized by Functions, Globals, Statics, and Data."""

    symbol_activated = Signal(str, str, str, int)  # name, address, file, line

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugSymbolsPane")
        self._all_symbols: List[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Search / filter header
        header = QFrame(self)
        header.setObjectName("debugSymbolsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        title = QLabel("SYMBOLS")
        title.setObjectName("debugSymbolsTitle")
        title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94A3B8; letter-spacing: 0.5px;")
        header_layout.addWidget(title)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("debugSymbolsSearch")
        self.search_input.setPlaceholderText("Filter symbols (e.g. Motor_)...")
        self.search_input.textChanged.connect(self._filter_symbols)
        header_layout.addWidget(self.search_input, 1)

        layout.addWidget(header)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setObjectName("debugSymbolsTree")
        self.tree.setHeaderLabels(("Name", "Address", "Size", "Type", "Source"))
        self.tree.setAlternatingRowColors(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)

        mono_font = QFont("Consolas", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.tree.setFont(mono_font)

        header_view = self.tree.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        # Categories
        self.cat_functions = QTreeWidgetItem(self.tree, ["Functions"])
        self.cat_globals = QTreeWidgetItem(self.tree, ["Globals"])
        self.cat_statics = QTreeWidgetItem(self.tree, ["Statics"])
        self.cat_data = QTreeWidgetItem(self.tree, ["Data"])

        for cat in (self.cat_functions, self.cat_globals, self.cat_statics, self.cat_data):
            cat.setExpanded(True)
            f = cat.font(0)
            f.setBold(True)
            cat.setFont(0, f)

        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree, 1)

    def set_symbols(self, symbols: Sequence[dict]) -> None:
        """Populate symbol tree from list of symbol dicts or SymbolCatalogEntry objects."""
        self._all_symbols = list(symbols)
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        # Clear items in categories
        for cat in (self.cat_functions, self.cat_globals, self.cat_statics, self.cat_data):
            while cat.childCount() > 0:
                cat.removeChild(cat.child(0))

        query = self.search_input.text().strip().lower()

        for s in self._all_symbols:
            name = s.get("name") or s.get("symbol") or ""
            if query and query not in name.lower():
                continue

            address = s.get("address", "")
            if isinstance(address, int):
                address = f"0x{address:08X}"
            size = str(s.get("size", ""))
            kind = s.get("kind", "")
            category = (s.get("category") or "data").lower()
            source_file = s.get("file", "")
            source_line = s.get("line", 0)
            source_loc = f"{source_file}:{source_line}" if source_file else ""

            item = QTreeWidgetItem([name, address, size, kind, source_loc])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "name": name,
                "address": address,
                "file": source_file,
                "line": source_line,
            })

            if category in ("function", "func") or kind in ("T", "t", "W", "w"):
                self.cat_functions.addChild(item)
            elif category == "static" or kind in ("b", "d", "r"):
                self.cat_statics.addChild(item)
            elif category in ("global", "data") or kind in ("B", "D", "R", "G"):
                self.cat_globals.addChild(item)
            else:
                self.cat_data.addChild(item)

    def _filter_symbols(self) -> None:
        self._refresh_tree()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            name = data.get("name", "")
            address = data.get("address", "")
            file_path = data.get("file", "")
            line = int(data.get("line", 0))
            self.symbol_activated.emit(name, address, file_path, line)
