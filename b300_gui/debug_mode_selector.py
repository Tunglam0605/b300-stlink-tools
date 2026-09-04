"""Mode-first entry for the B300 engineering Debug Workstation."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ModeTile(QFrame):
    """Compact technical role tile."""

    clicked = Signal(str)

    def __init__(
        self,
        mode_id: str,
        icon_tag: str,
        title: str,
        subtitle: str,
        detail_badge: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.mode_id = mode_id
        self._is_active = False
        self.setObjectName("debugModeTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(190)
        self.setMaximumWidth(320)
        self.setMinimumHeight(112)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.tag_lbl = QLabel(icon_tag)
        self.tag_lbl.setStyleSheet(
            "font-size: 12px; font-weight: 800; font-family: 'Cascadia Code', monospace; color: #38BDF8;"
        )
        top_row.addWidget(self.tag_lbl)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("debugModeTileTitle")
        self.title_label.setStyleSheet(
            "font-size: 14px; font-weight: 900; letter-spacing: 0.8px; color: #F8FAFC;"
        )
        top_row.addWidget(self.title_label)
        top_row.addStretch(1)

        if detail_badge:
            badge = QLabel(detail_badge)
            badge.setObjectName("debugModeTileBadge")
            badge.setStyleSheet(
                "font-size: 9px; font-family: 'Cascadia Code', monospace; padding: 2px 5px; "
                "border-radius: 3px; background: rgba(56,189,248,0.10); color: #38BDF8;"
            )
            top_row.addWidget(badge)
        layout.addLayout(top_row)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("debugModeTileSubtitle")
        self.subtitle_label.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)
        layout.addStretch(1)

        # Real button for keyboard navigation and accessibility.
        self.button = QPushButton("CHỌN")
        self.button.setObjectName("debugModeTileButton")
        self.button.setMaximumWidth(74)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet(
            "font-size: 10px; font-weight: 800; font-family: 'Cascadia Code', monospace; "
            "padding: 4px 8px; border-radius: 3px; background: #111827; "
            "border: 1px solid #334155; color: #CBD5E1;"
        )
        self.button.clicked.connect(lambda: self.clicked.emit(self.mode_id))
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignRight)

    def set_active(self, active: bool) -> None:
        self._is_active = active
        border_color = "#38BDF8" if active else "#334155"
        bg_color = "rgba(56,189,248,0.07)" if active else "#0F172A"
        self.setStyleSheet(
            f"#debugModeTile {{ border: 1.5px solid {border_color}; border-radius: 6px; background: {bg_color}; }}"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.mode_id)
        super().mousePressEvent(event)

    def click(self) -> None:
        self.clicked.emit(self.mode_id)


class DebugModeSelector(QWidget):
    """Explicit LOCAL/GATEWAY/CLIENT connection selection before setup."""

    mode_selected = Signal(str)

    def __init__(
        self,
        probe_checker: Optional[Callable[[], bool]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("debugModeSelectorContainer")
        self._probe_checker = probe_checker
        self._current_mode = "local"
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.header_title = QLabel("KẾT NỐI DEBUG")
        self.header_title.setObjectName("debugModeHeaderTitle")
        self.header_title.setStyleSheet(
            "font-size: 17px; font-weight: 900; letter-spacing: 1.4px; color: #F8FAFC;"
        )
        main_layout.addWidget(self.header_title, 0, Qt.AlignmentFlag.AlignCenter)

        self.header_subtitle = QLabel("STM32F407 · CHỌN CÁCH KẾT NỐI")
        self.header_subtitle.setStyleSheet(
            "font-size: 10px; color: #64748B; font-family: 'Cascadia Code', monospace;"
        )
        main_layout.addWidget(self.header_subtitle, 0, Qt.AlignmentFlag.AlignCenter)

        tiles_layout = QHBoxLayout()
        tiles_layout.setSpacing(14)
        tiles_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.tile_local = ModeTile(
            "local", "[L]", "LOCAL",
            "Debug trực tiếp qua ST-Link trên máy này",
            "USB SWD", self,
        )
        self.tile_gateway = ModeTile(
            "gateway", "[G]", "GATEWAY",
            "Máy này cắm ST-Link và cung cấp Debug từ xa cho Client",
            "ST-LINK + SSH", self,
        )
        self.tile_client = ModeTile(
            "client", "[C]", "CLIENT",
            "Máy này Debug STM32 thông qua Gateway từ xa",
            "SSH", self,
        )
        for tile in (self.tile_local, self.tile_gateway, self.tile_client):
            tile.clicked.connect(self.select_mode)
            tiles_layout.addWidget(tile)

        main_layout.addLayout(tiles_layout)
        self._update_tile_highlights()

    def select_mode(self, mode: str) -> None:
        if mode in ("local", "gateway", "client"):
            self._current_mode = mode
            self._update_tile_highlights()
            self.mode_selected.emit(mode)

    def set_mode(self, mode: str) -> None:
        if mode in ("local", "gateway", "client"):
            self._current_mode = mode
            self._update_tile_highlights()

    def current_mode(self) -> str:
        return self._current_mode

    def _update_tile_highlights(self) -> None:
        self.tile_local.set_active(self._current_mode == "local")
        self.tile_gateway.set_active(self._current_mode == "gateway")
        self.tile_client.set_active(self._current_mode == "client")
