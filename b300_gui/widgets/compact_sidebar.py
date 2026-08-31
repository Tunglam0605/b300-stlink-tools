"""Modern Compact Sidebar Navigation Panel with Expand/Collapse Support."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NavButton(QPushButton):
    """Modern Navigation button supporting compact icon-only and expanded icon+text."""

    def __init__(self, item_id: str, icon_text: str, label_text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item_id = item_id
        self.icon_text = icon_text
        self.label_text = label_text
        self._is_compact = True
        self._is_active = False

        self.setObjectName("compactNavBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(f"{icon_text}  {label_text}")
        self.setFixedHeight(44)

        self._update_appearance()

    def set_compact(self, compact: bool) -> None:
        self._is_compact = compact
        self._update_appearance()

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self.setChecked(active)
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _update_appearance(self) -> None:
        if self._is_compact:
            self.setText(self.icon_text)
        else:
            self.setText(f"  {self.icon_text}  {self.label_text}")


class CompactSidebar(QFrame):
    """Vertical compact workstation navigation sidebar."""

    nav_changed = Signal(str)  # item_id

    # Item definitions: (item_id, icon, label, modes)
    NAV_ITEMS: List[Tuple[str, str, str, List[str]]] = [
        ("op_flash", "FLASH", "Nạp Sản xuất", ["operator"]),
        ("rnd_flash", "FLASH", "Nạp Flash R&D", ["rnd"]),
        ("rnd_memory", "MEM", "Cấu trúc & Metadata", ["rnd"]),
        ("rnd_debug", "DBG", "Studio Live Debug", ["rnd"]),
        ("rnd_gateway", "SSH", "Cầu nối Từ xa (SSH)", ["rnd"]),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("compactSidebar")
        self._is_compact = True
        self._current_mode = "operator"
        self._current_item = "op_flash"
        self._buttons: Dict[str, NavButton] = {}

        self.setFixedWidth(64)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 10, 6, 10)
        main_layout.setSpacing(6)

        # Nav items container
        self.items_container = QWidget()
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(4)
        main_layout.addWidget(self.items_container)

        # Build all buttons
        for item_id, icon, label, _modes in self.NAV_ITEMS:
            btn = NavButton(item_id, icon, label, self)
            btn.clicked.connect(lambda _, i=item_id: self.select_item(i))
            self._buttons[item_id] = btn
            self.items_layout.addWidget(btn)

        main_layout.addStretch(1)

        # Bottom expand/collapse toggle button
        self.toggle_btn = QPushButton("▶")
        self.toggle_btn.setObjectName("sidebarToggleBtn")
        self.toggle_btn.setFixedSize(36, 32)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setToolTip("Mở rộng / Thu gọn thanh điều hướng")
        self.toggle_btn.clicked.connect(self.toggle_collapse)
        
        toggle_layout = QHBoxLayout()
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.addWidget(self.toggle_btn, 0, Qt.AlignmentFlag.AlignCenter)
        main_layout.addLayout(toggle_layout)

        self._refresh_visibility()

    def set_mode(self, mode: str) -> None:
        if mode not in ("operator", "rnd"):
            return
        if self._current_mode != mode:
            self._current_mode = mode
            self._refresh_visibility()
            # Select first visible item in new mode
            first_item = "op_flash" if mode == "operator" else "rnd_flash"
            self.select_item(first_item)

    def select_item(self, item_id: str) -> None:
        if item_id not in self._buttons:
            return
        self._current_item = item_id
        for bid, btn in self._buttons.items():
            btn.set_active(bid == item_id)
        self.nav_changed.emit(item_id)

    @property
    def current_item(self) -> str:
        return self._current_item

    def toggle_collapse(self) -> None:
        self._is_compact = not self._is_compact
        if self._is_compact:
            self.setFixedWidth(64)
            self.toggle_btn.setText("▶")
        else:
            self.setFixedWidth(200)
            self.toggle_btn.setText("◀  Thu gọn")
            self.toggle_btn.setFixedSize(160, 32)

        for btn in self._buttons.values():
            btn.set_compact(self._is_compact)

    def _refresh_visibility(self) -> None:
        for item_id, _icon, _label, modes in self.NAV_ITEMS:
            btn = self._buttons[item_id]
            btn.setVisible(self._current_mode in modes)
