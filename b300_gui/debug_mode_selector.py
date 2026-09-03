"""Mode-First Entry Screen for B300 Debug Workstation."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ModeTile(QFrame):
    """Compact, technical mode tile without verbose paragraphs."""

    clicked = Signal(str)

    def __init__(
        self,
        mode_id: str,
        icon: str,
        title: str,
        subtitle: str,
        detail_badge: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.mode_id = mode_id
        self.setObjectName("debugModeTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(180)
        self.setMaximumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # Header row with tech icon and mode tag
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 20px; font-weight: bold;")
        top_row.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("debugModeTileTitle")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 800; letter-spacing: 0.5px;")
        top_row.addWidget(title_lbl)
        top_row.addStretch(1)

        if detail_badge:
            badge = QLabel(detail_badge)
            badge.setObjectName("debugModeTileBadge")
            badge.setStyleSheet(
                "font-size: 10px; font-family: monospace; padding: 2px 6px; "
                "border-radius: 4px; background: rgba(16, 185, 129, 0.15); color: #10B981;"
            )
            top_row.addWidget(badge)

        layout.addLayout(top_row)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("debugModeTileSubtitle")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        layout.addWidget(sub_lbl)

        layout.addSpacing(4)
        btn = QPushButton(f"Chọn {title}")
        btn.setObjectName("debugModeTileButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.clicked.emit(self.mode_id))
        layout.addWidget(btn)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.mode_id)
        super().mousePressEvent(event)


class DebugModeSelector(QWidget):
    """Mode-first entry widget presenting Local, Gateway, and Client modes."""

    mode_selected = Signal(str)  # "local" | "gateway" | "client"

    def __init__(
        self,
        probe_checker: Optional[Callable[[], bool]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("debugModeSelectorContainer")
        self._probe_checker = probe_checker
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 20, 16, 20)
        main_layout.setSpacing(16)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Title ribbon (short, no paragraph)
        header = QHBoxLayout()
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("DEBUG MODE")
        title.setObjectName("debugModeHeaderTitle")
        title.setStyleSheet("font-size: 16px; font-weight: 900; letter-spacing: 1px; color: #F1F5F9;")
        header.addWidget(title)
        main_layout.addLayout(header)

        # 3 balanced technical tiles
        tiles_layout = QHBoxLayout()
        tiles_layout.setSpacing(14)
        tiles_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.tile_local = ModeTile(
            mode_id="local",
            icon="💻",
            title="LOCAL",
            subtitle="ST-Link trên máy này",
            detail_badge="USB SWD",
            parent=self,
        )
        self.tile_local.clicked.connect(self.mode_selected.emit)
        tiles_layout.addWidget(self.tile_local)

        self.tile_gateway = ModeTile(
            mode_id="gateway",
            icon="🌐",
            title="GATEWAY",
            subtitle="ST-Link + OpenOCD Server",
            detail_badge="TCP 3333/6666",
            parent=self,
        )
        self.tile_gateway.clicked.connect(self.mode_selected.emit)
        tiles_layout.addWidget(self.tile_gateway)

        self.tile_client = ModeTile(
            mode_id="client",
            icon="📡",
            title="CLIENT",
            subtitle="Debug từ máy Gateway",
            detail_badge="SSH TUNNEL",
            parent=self,
        )
        self.tile_client.clicked.connect(self.mode_selected.emit)
        tiles_layout.addWidget(self.tile_client)

        main_layout.addLayout(tiles_layout)

    def select_mode(self, mode: str) -> None:
        if mode in ("local", "gateway", "client"):
            self.mode_selected.emit(mode)
