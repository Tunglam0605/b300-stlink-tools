"""Collapsible Technical Log panel with INFO/WARN/ERROR badges and export."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QWidget,
)

from .collapsible_card import CollapsibleCard
from .log_highlighter import format_log_html


class DebugLogPanel(CollapsibleCard):
    """Collapsible technical OpenOCD/GDB log panel with count badges and copy/save actions."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            "Technical Log",
            "OpenOCD runtime, GDB MI & TCL communication",
            parent,
            expanded=False,
        )
        self._info_count = 0
        self._warn_count = 0
        self._error_count = 0
        self._build_ui()

    def _build_ui(self) -> None:
        # Badges on header
        self.info_badge = QLabel("0 INFO")
        self.info_badge.setObjectName("badgeInfo")
        self.add_header_widget(self.info_badge)

        self.warn_badge = QLabel("0 WARN")
        self.warn_badge.setObjectName("badgeWarn")
        self.add_header_widget(self.warn_badge)

        self.error_badge = QLabel("0 ERR")
        self.error_badge.setObjectName("badgeError")
        self.add_header_widget(self.error_badge)

        content_layout = self.content_layout
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_log)
        toolbar.addWidget(self.copy_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_log)
        toolbar.addWidget(self.clear_button)

        self.save_button = QPushButton("Save Log…")
        self.save_button.clicked.connect(self.save_log)
        toolbar.addWidget(self.save_button)

        toolbar.addStretch(1)
        content_layout.addLayout(toolbar)

        # Log text view
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("debugLogView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMinimumHeight(110)
        content_layout.addWidget(self.log_view)

    def append_log(self, line: str) -> None:
        text = str(line)
        lower = text.lower()
        if "error" in lower or "failed" in lower or "fatal" in lower:
            self._error_count += 1
        elif "warn" in lower or "warning" in lower:
            self._warn_count += 1
        else:
            self._info_count += 1

        self.info_badge.setText("%d INFO" % self._info_count)
        self.warn_badge.setText("%d WARN" % self._warn_count)
        self.error_badge.setText("%d ERR" % self._error_count)

        self.log_view.appendHtml(format_log_html(text))
        scroll = self.log_view.verticalScrollBar()
        if scroll is not None:
            scroll.setValue(scroll.maximum())

    def clear_log(self) -> None:
        self.log_view.clear()
        self._info_count = 0
        self._warn_count = 0
        self._error_count = 0
        self.info_badge.setText("0 INFO")
        self.warn_badge.setText("0 WARN")
        self.error_badge.setText("0 ERR")

    def copy_log(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.log_view.toPlainText())

    def save_log(self, parent: Optional[QWidget] = None) -> Optional[Path]:
        text = self.log_view.toPlainText()
        if not text:
            return None
        path, _selected = QFileDialog.getSaveFileName(
            parent or self, "Save Debug Log", "b300-debug.log", "Log files (*.log *.txt)"
        )
        if not path:
            return None
        dest = Path(path)
        dest.write_text(text, encoding="utf-8")
        return dest
