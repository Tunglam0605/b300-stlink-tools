"""Interactive Color-Coded Memory Map Bar for STM32F407 Flash Layout."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from b300_gui.theme import ThemeManager


class SectorBlock:
    def __init__(self, name: str, start: int, size: int, role: str, color_dark: str, color_light: str, protected: bool = False):
        self.name = name
        self.start = start
        self.size = size
        self.role = role
        self.color_dark = color_dark
        self.color_light = color_light
        self.protected = protected

    @property
    def end(self) -> int:
        return self.start + self.size - 1


class MemoryMapCanvas(QWidget):
    """Custom paint widget rendering the STM32F407 512KB/1MB Flash layout."""

    SECTORS: List[SectorBlock] = [
        SectorBlock("S0-S2 (48K)", 0x08000000, 48 * 1024, "Bootloader (WRP Protected)", "#0284C7", "#0284C7", protected=True),
        SectorBlock("S3 (16K)", 0x0800C000, 16 * 1024, "OTA Metadata (STLM 44B)", "#D97706", "#D97706"),
        SectorBlock("S4 (64K)", 0x08010000, 64 * 1024, "App Vector & Code", "#10B981", "#059669"),
        SectorBlock("S5 (128K)", 0x08020000, 128 * 1024, "App Code", "#059669", "#047857"),
        SectorBlock("S6 (128K)", 0x08040000, 128 * 1024, "App Code", "#059669", "#047857"),
        SectorBlock("S7 (128K)", 0x08060000, 128 * 1024, "App Data / Free", "#047857", "#065F46"),
    ]
    TOTAL_FLASH = 512 * 1024

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setMouseTracking(True)
        self._hovered_sector: Optional[SectorBlock] = None
        self._image_span: Optional[Tuple[int, int]] = None  # (start_addr, size)

    def set_image_span(self, start_addr: Optional[int], size: Optional[int]) -> None:
        if start_addr is not None and size is not None:
            self._image_span = (start_addr, size)
        else:
            self._image_span = None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_dark = ThemeManager.instance().is_dark
        w = self.width()
        h = self.height()
        bar_h = 32
        bar_y = 6

        # Draw outer boundary
        palette = ThemeManager.instance().palette
        bg_color = QColor(palette.surface_sunken)
        border_color = QColor(palette.border)
        painter.setBrush(bg_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(QRectF(0, bar_y, w, bar_h), 8, 8)

        # Draw sectors
        curr_x = 0.0
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)

        for sector in self.SECTORS:
            sec_w = (sector.size / self.TOTAL_FLASH) * w
            rect = QRectF(curr_x, bar_y, sec_w, bar_h)

            color_str = sector.color_dark if is_dark else sector.color_light
            color = QColor(color_str)
            if self._hovered_sector == sector:
                color = color.lighter(120)

            painter.setBrush(color)
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
            painter.drawRoundedRect(rect, 5, 5)

            # Draw sector label
            if sec_w > 45:
                painter.setPen(QColor("#FFFFFF"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, sector.name)

            curr_x += sec_w

        # Draw active loaded image highlight overlay if present
        if self._image_span:
            img_start, img_size = self._image_span
            if 0x08000000 <= img_start < 0x08080000:
                rel_start = img_start - 0x08000000
                img_x = (rel_start / self.TOTAL_FLASH) * w
                img_w = min((img_size / self.TOTAL_FLASH) * w, w - img_x)
                highlight_rect = QRectF(img_x, bar_y + bar_h + 3, max(img_w, 4.0), 6)
                painter.setBrush(QColor("#38BDF8"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(highlight_rect, 3, 3)

        painter.end()


class MemoryMapWidget(QFrame):
    """Complete Memory Map card with graphical canvas and legend indicators."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardSurface")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header title
        header_h = QHBoxLayout()
        header_h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("BẢN ĐỒ PHÂN VÙNG BỘ NHỚ FLASH (512KB STM32F407)")
        title.setObjectName("eyebrowLabel")
        header_h.addWidget(title)
        header_h.addStretch(1)
        self.info_chip = QLabel("0x08000000 - 0x0807FFFF")
        self.info_chip.setObjectName("monoText")
        self.info_chip.setStyleSheet("font-size: 11px; color: #94A3B8;")
        header_h.addWidget(self.info_chip)
        layout.addLayout(header_h)

        # Graphical Canvas
        self.canvas = MemoryMapCanvas(self)
        layout.addWidget(self.canvas)

        # Legend Row
        legend_h = QHBoxLayout()
        legend_h.setContentsMargins(4, 0, 4, 0)
        legend_h.setSpacing(14)

        legend_items = [
            ("● S0-S2: Bootloader (WRP Cố định)", "#0284C7"),
            ("● S3: OTA Metadata (STLM 44B)", "#D97706"),
            ("● S4-S7: Application (Cho phép nạp)", "#10B981"),
            ("━ HEX đã chọn", "#38BDF8"),
        ]
        for text, col in legend_items:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {col}; font-size: 11px; font-weight: 600;")
            legend_h.addWidget(lbl)

        legend_h.addStretch(1)
        layout.addLayout(legend_h)

        ThemeManager.instance().theme_changed.connect(lambda _: self.canvas.update())

    def set_image_span(self, start_addr: Optional[int], size: Optional[int]) -> None:
        self.canvas.set_image_span(start_addr, size)
