"""Small native widgets used only by the production Pulse surface."""

from __future__ import annotations

from math import ceil

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from .branding import asset_path


class PulseTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        family = "Segoe UI Variable Display" if "Segoe UI Variable Display" in set(QFontDatabase.families()) else "Segoe UI"
        font = QFont(family); font.setPixelSize(30); font.setWeight(QFont.Weight.Medium)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.4)
        self.setFont(font); self.setObjectName("PulseTitle"); self._dark = False
        self.setAccessibleName(text)

    def setText(self, text: str) -> None:
        super().setText(text); self.setAccessibleName(text); self.updateGeometry(); self.update()

    def set_dark(self, dark: bool) -> None:
        if self._dark != bool(dark): self._dark = bool(dark); self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetricsF(self.font())
        return QSize(ceil(metrics.horizontalAdvance(self.text())) + 3, ceil(metrics.height()) + 4)

    minimumSizeHint = sizeHint

    def paintEvent(self, event) -> None:
        if not self.text(): return
        metrics = QFontMetricsF(self.font()); path = QPainterPath(); path.addText(1, 1 + metrics.ascent(), self.font(), self.text())
        bounds = path.boundingRect(); gradient = QLinearGradient(bounds.left(), bounds.top(), max(bounds.left() + 1, bounds.right()), bounds.top())
        gradient.setColorAt(0, "#F1F6FB" if self._dark else "#17233F"); gradient.setColorAt(1, "#9EC4FF" if self._dark else "#3659BB")
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setClipRect(self.rect()); painter.fillPath(path, gradient); painter.end()


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__("", parent); self._full_text = ""; self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = str(text); super().setText(self._full_text); self.setToolTip(self._full_text); self.setAccessibleName(self._full_text); self.update()

    def minimumSizeHint(self) -> QSize: return QSize(0, self.fontMetrics().height())

    def paintEvent(self, event) -> None:
        rect = self.contentsRect(); shown = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, rect.width())
        painter = QPainter(self); painter.setClipRect(rect); painter.setPen(self.palette().color(QPalette.ColorRole.WindowText)); painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown); painter.end()


class PulseBrand(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent); self._source = QPixmap(str(asset_path("pulse-wordmark.png")))
        self.setObjectName("PulseIllustration"); self.setAlignment(Qt.AlignmentFlag.AlignCenter); self.setMinimumSize(220, 180); self.setMaximumWidth(360)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("B300 ST-Link Tools")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._source.isNull(): return
        ratio = self.devicePixelRatioF() or 1.0
        width = min(360, max(1, self.contentsRect().width()))
        pixmap = self._source.scaledToWidth(max(1, round(width * ratio)), Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio); self.setPixmap(pixmap)

    def set_dark(self, dark: bool) -> None:
        """Keep the approved raster brand on its light backing across themes."""
        self.setProperty("dark", bool(dark)); self.style().unpolish(self); self.style().polish(self)
