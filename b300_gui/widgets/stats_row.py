"""Cockpit Tools-inspired 4-card Quick Stats Row for B300 ST-Link Tools."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class StatCard(QFrame):
    """Modern KPI / Stat card with an icon chip, uppercase title, bold value, and caption."""

    def __init__(
        self,
        icon: str,
        title: str,
        value: str,
        subtitle: str = "",
        variant: str = "primary",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setProperty("variant", variant)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # Icon Chip
        self.icon_badge = QLabel(icon)
        self.icon_badge.setObjectName("statIconBadge")
        self.icon_badge.setProperty("variant", variant)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_badge.setFixedSize(36, 36)
        layout.addWidget(self.icon_badge)

        # Text Column
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("statCardTitle")
        text_layout.addWidget(self.title_label)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("statCardValue")
        text_layout.addWidget(self.value_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("statCardSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))
        text_layout.addWidget(self.subtitle_label)

        layout.addLayout(text_layout)
        layout.addStretch(1)

    def set_value(self, value: str, subtitle: Optional[str] = None) -> None:
        self.value_label.setText(value)
        if subtitle is not None:
            self.subtitle_label.setText(subtitle)
            self.subtitle_label.setVisible(bool(subtitle))


class StatsRow(QWidget):
    """Cockpit-style 4-column KPI overview bar for ST-Link, Target, Flash, and System State."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("statsRowContainer")

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        self._layout = layout
        self._columns = 0

        # Card 1: Probe
        self.probe_card = StatCard(
            icon="🔌",
            title="ST-Link Probe",
            value="Chưa kết nối",
            subtitle="USB scan ready",
            variant="probe",
            parent=self,
        )

        # Card 2: Target MCU
        self.target_card = StatCard(
            icon="🎯",
            title="Target MCU",
            value="Chưa đọc target",
            subtitle="Dùng Kiểm tra target",
            variant="target",
            parent=self,
        )

        # Card 3: Flash Map
        self.flash_card = StatCard(
            icon="💾",
            title="Flash Memory",
            value="Chưa đọc flash",
            subtitle="Dùng Kiểm tra target",
            variant="flash",
            parent=self,
        )

        # Card 4: System State
        self.status_card = StatCard(
            icon="⚡",
            title="Trạng thái",
            value="Sẵn sàng",
            subtitle="OpenOCD Loopback",
            variant="status",
            parent=self,
        )
        self._cards = (
            self.probe_card,
            self.target_card,
            self.flash_card,
            self.status_card,
        )
        for card in self._cards:
            card.setMinimumWidth(0)
            card.setSizePolicy(card.sizePolicy().horizontalPolicy(), card.sizePolicy().verticalPolicy())
        self._arrange_cards(1)

    def _arrange_cards(self, columns: int) -> None:
        if columns == self._columns:
            return
        while self._layout.count():
            self._layout.takeAt(0)
        for index, card in enumerate(self._cards):
            row, column = divmod(index, columns)
            self._layout.addWidget(card, row, column)
        for column in range(columns):
            self._layout.setColumnStretch(column, 1)
        self._columns = columns

    def _minimum_width_for_columns(self, columns: int) -> int:
        column_widths = [0] * columns
        for index, card in enumerate(self._cards):
            column = index % columns
            column_widths[column] = max(column_widths[column], card.minimumSizeHint().width())
        return sum(column_widths) + self._layout.horizontalSpacing() * (columns - 1)

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        columns = 4 if width >= self._minimum_width_for_columns(4) else 2
        if width < self._minimum_width_for_columns(2):
            columns = 1
        self._arrange_cards(columns)
        super().resizeEvent(event)

    def update_probe(self, name: str, details: str = "") -> None:
        self.probe_card.set_value(name, details)

    def update_target(self, target: str, details: str = "") -> None:
        self.target_card.set_value(target, details)

    def clear_target(self) -> None:
        self.target_card.set_value("Chưa đọc target", "Dùng Kiểm tra target")
        self.flash_card.set_value("Chưa đọc flash", "Dùng Kiểm tra target")

    def update_flash(self, flash: str, details: str = "") -> None:
        self.flash_card.set_value(flash, details)

    def update_status(self, status: str, details: str = "") -> None:
        self.status_card.set_value(status, details)
