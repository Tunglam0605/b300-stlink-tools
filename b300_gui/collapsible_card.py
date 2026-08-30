"""Reusable collapsible card container for B300 engineering workstation panels."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class CollapsibleCard(QFrame):
    """Clean collapsible card with title, subtitle, optional badge, and expand/collapse control."""

    expanded_changed = Signal(bool)

    def __init__(
        self,
        title: str,
        subtitle: Optional[str] = None,
        parent: Optional[QWidget] = None,
        *,
        expanded: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleCard")
        self._expanded = expanded

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Header bar
        self.header_frame = QFrame()
        self.header_frame.setObjectName("collapsibleHeader")
        self.header_frame.setProperty("expanded", "true" if expanded else "false")
        self.header_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_frame.mousePressEvent = self._on_header_clicked

        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(8)

        self.toggle_btn = QPushButton("▼" if expanded else "▶")
        self.toggle_btn.setFixedSize(22, 22)
        self.toggle_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #0284C7; font-size: 11px; font-weight: 700; padding: 0; }"
            "QPushButton:hover { background: #E0F2FE; border-radius: 4px; }"
        )
        self.toggle_btn.clicked.connect(self.toggle)
        header_layout.addWidget(self.toggle_btn)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("collapsibleTitle")
        title_col.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle or "")
        self.subtitle_label.setObjectName("collapsibleSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))
        title_col.addWidget(self.subtitle_label)

        header_layout.addLayout(title_col)
        header_layout.addStretch(1)

        self.header_actions = QHBoxLayout()
        self.header_actions.setContentsMargins(0, 0, 0, 0)
        self.header_actions.setSpacing(6)
        header_layout.addLayout(self.header_actions)

        root_layout.addWidget(self.header_frame)

        # Content container
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(12, 10, 12, 12)
        self.content_layout.setSpacing(8)
        self.content_widget.setVisible(expanded)

        root_layout.addWidget(self.content_widget)

    def _on_header_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))

    def add_header_widget(self, widget: QWidget) -> None:
        self.header_actions.addWidget(widget)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self.toggle_btn.setText("▼" if expanded else "▶")
        self.content_widget.setVisible(expanded)
        self.header_frame.setProperty("expanded", "true" if expanded else "false")
        if self.header_frame.style() is not None:
            self.header_frame.style().unpolish(self.header_frame)
            self.header_frame.style().polish(self.header_frame)
        self.expanded_changed.emit(expanded)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)
