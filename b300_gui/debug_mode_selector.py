"""Mode-First Entry Screen for B300 Debug Workstation."""

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
    """Compact, technical mode tile without verbose paragraphs or noisy emoji."""

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
        self.setMinimumWidth(200)
        self.setMaximumWidth(340)
        self.setMinimumHeight(130)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        # Header row: technical icon tag + mode title + badge
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.tag_lbl = QLabel(icon_tag)
        self.tag_lbl.setStyleSheet(
            "font-size: 13px; font-weight: 800; font-family: 'Cascadia Code', monospace; color: #38BDF8;"
        )
        top_row.addWidget(self.tag_lbl)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("debugModeTileTitle")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 900; letter-spacing: 0.8px; color: #F8FAFC;")
        top_row.addWidget(title_lbl)
        top_row.addStretch(1)

        if detail_badge:
            badge = QLabel(detail_badge)
            badge.setObjectName("debugModeTileBadge")
            badge.setStyleSheet(
                "font-size: 10px; font-family: 'Cascadia Code', monospace; padding: 2px 6px; "
                "border-radius: 4px; background: rgba(56, 189, 248, 0.12); color: #38BDF8;"
            )
            top_row.addWidget(badge)

        layout.addLayout(top_row)

        # Subtitle
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("debugModeTileSubtitle")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        layout.addStretch(1)

        # Action trigger button (clean, minimal styling, kept for compatibility & keyboard a11y)
        self.button = QPushButton("Mở cấu hình")
        self.button.setObjectName("debugModeTileButton")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet(
            "font-size: 11px; font-weight: 700; padding: 5px 12px; border-radius: 4px; "
            "background: rgba(30, 41, 59, 0.7); border: 1px solid #334155; color: #CBD5E1;"
        )
        self.button.clicked.connect(lambda: self.clicked.emit(self.mode_id))
        layout.addWidget(self.button)

    def set_active(self, active: bool) -> None:
        self._is_active = active
        border_color = "#38BDF8" if active else "#334155"
        bg_color = "rgba(56, 189, 248, 0.08)" if active else "#0F172A"
        self.setStyleSheet(
            f"#debugModeTile {{ border: 1.5px solid {border_color}; border-radius: 8px; background: {bg_color}; }}"
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.mode_id)
        super().mousePressEvent(event)

    def click(self) -> None:
        self.clicked.emit(self.mode_id)


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
        self._current_mode = "local"
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 24, 20, 24)
        main_layout.setSpacing(18)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Title ribbon (technical and dense)
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        header_box.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("DEBUG MODE")
        title.setObjectName("debugModeHeaderTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 900; letter-spacing: 1.5px; color: #F8FAFC;")
        header_box.addWidget(title)

        subtitle = QLabel("Chọn môi trường gỡ lỗi cho STM32F407")
        subtitle.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 500;")
        header_box.addWidget(subtitle)

        main_layout.addLayout(header_box)

        # 3 balanced technical tiles (no emojis, technical identifiers)
        tiles_layout = QHBoxLayout()
        tiles_layout.setSpacing(16)
        tiles_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.tile_local = ModeTile(
            mode_id="local",
            icon_tag="⬡",
            title="LOCAL",
            subtitle="ST-Link kết nối trực tiếp trên máy này",
            detail_badge="USB SWD",
            parent=self,
        )
        self.tile_local.clicked.connect(self.select_mode)
        tiles_layout.addWidget(self.tile_local)

        self.tile_gateway = ModeTile(
            mode_id="gateway",
            icon_tag="⬢",
            title="GATEWAY",
            subtitle="Chạy OpenOCD server nhận kết nối từ xa",
            detail_badge="TCP 3333/6666",
            parent=self,
        )
        self.tile_gateway.clicked.connect(self.select_mode)
        tiles_layout.addWidget(self.tile_gateway)

        self.tile_client = ModeTile(
            mode_id="client",
            icon_tag="◈",
            title="CLIENT",
            subtitle="Gỡ lỗi qua mạng tới máy chủ Gateway",
            detail_badge="SSH TUNNEL",
            parent=self,
        )
        self.tile_client.clicked.connect(self.select_mode)
        tiles_layout.addWidget(self.tile_client)

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
