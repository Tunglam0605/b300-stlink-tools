"""Offline AXF/ELF symbol browser for zero-halt Live Monitor watch selection."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from b300_core.offline_symbols import OfflineSymbolTable, SymbolCatalogEntry


class SymbolBrowserDialog(QDialog):
    """Browse an offline symbol catalog without reading or changing the target."""

    RESULT_LIMIT = 1000

    def __init__(self, symbols: OfflineSymbolTable, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.symbols = symbols
        self._visible_entries = ()
        self.setWindowTitle("AXF/ELF Symbol Browser")
        self.resize(920, 560)
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Offline symbol catalog only — no ST-Link traffic, no halt/reset. "
            "A symbol type is not inferred from nm; choose the Live Watch type separately."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("symbolBrowserSearch")
        self.search.setPlaceholderText("Filter symbol name…")
        filters.addWidget(self.search, 1)

        self.category = QComboBox()
        self.category.setObjectName("symbolBrowserCategory")
        self.category.addItem("All categories", None)
        self.category.addItem("Data", "data")
        self.category.addItem("Functions", "function")
        self.category.addItem("Other", "other")
        filters.addWidget(self.category)

        self.safe_only = QCheckBox("Watchable RAM only")
        self.safe_only.setObjectName("symbolBrowserSafeOnly")
        self.safe_only.setChecked(True)
        filters.addWidget(self.safe_only)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("symbolBrowserTable")
        self.table.setHorizontalHeaderLabels(
            ("Symbol", "Address", "Size", "Kind", "Category", "Watchable", "Status")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 55)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 90)
        layout.addWidget(self.table, 1)

        self.status = QLabel()
        self.status.setObjectName("symbolBrowserStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.use_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.use_button.setText("Use Symbol")
        self.use_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.search.textChanged.connect(self._refresh)
        self.category.currentIndexChanged.connect(self._refresh)
        self.safe_only.toggled.connect(self._refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)

    def _refresh(self, *_args) -> None:
        category = self.category.currentData()
        watchable = True if self.safe_only.isChecked() else None
        entries = self.symbols.search_catalog(
            self.search.text(), category=category, watchable=watchable, limit=self.RESULT_LIMIT
        )
        self._visible_entries = entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                entry.name,
                "0x%08X" % entry.address,
                str(entry.size),
                entry.kind,
                entry.category,
                "Yes" if entry.watchable else "No",
                "Ready for typed RAM watch" if entry.watchable else (entry.watch_block_reason or "Blocked"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (1, 2, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.clearSelection()
        self.use_button.setEnabled(False)
        suffix = " (limited to %d)" % self.RESULT_LIMIT if len(entries) >= self.RESULT_LIMIT else ""
        self.status.setText("Showing %d symbols%s." % (len(entries), suffix))

    def selected_entry(self) -> Optional[SymbolCatalogEntry]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._visible_entries):
            return None
        return self._visible_entries[row]

    def selected_symbol_name(self) -> Optional[str]:
        entry = self.selected_entry()
        return entry.name if entry is not None and entry.watchable else None

    def _selection_changed(self) -> None:
        entry = self.selected_entry()
        allowed = entry is not None and entry.watchable
        self.use_button.setEnabled(allowed)
        if entry is None:
            return
        if entry.watchable:
            self.status.setText(
                "%s — 0x%08X, %d byte. Choose the data type explicitly in Live Variables."
                % (entry.name, entry.address, entry.size)
            )
        else:
            self.status.setText(entry.watch_block_reason or "This symbol cannot be used for Live Watch.")

    def _double_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._visible_entries) and self._visible_entries[row].watchable:
            self.table.selectRow(row)
            self.accept()
