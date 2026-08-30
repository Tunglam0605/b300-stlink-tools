"""Lightweight Qt live plot for numeric B300 debug samples."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from b300_core.debug_sampling import VariableSample


@dataclass(frozen=True)
class PlotSeries:
    expression: str
    points: Tuple[Tuple[float, float], ...]


def build_numeric_series(
    samples: Sequence[VariableSample], max_points_per_series: int = 400
) -> Tuple[PlotSeries, ...]:
    if not 2 <= int(max_points_per_series) <= 10000:
        raise ValueError("Plot point capacity must be in range 2..10000.")
    grouped = {}
    order = []
    for sample in samples:
        if sample.numeric_value is None:
            continue
        if sample.expression not in grouped:
            grouped[sample.expression] = []
            order.append(sample.expression)
        grouped[sample.expression].append(
            (float(sample.elapsed_seconds), float(sample.numeric_value))
        )
    series = []
    limit = int(max_points_per_series)
    for expression in order:
        points = grouped[expression][-limit:]
        if points:
            series.append(PlotSeries(expression, tuple(points)))
    return tuple(series)


class LivePlotWidget(QWidget):
    """Responsive multi-series plot without a third-party chart dependency."""

    def __init__(self, parent=None, *, max_points_per_series: int = 400) -> None:
        super().__init__(parent)
        if not 2 <= int(max_points_per_series) <= 10000:
            raise ValueError("Plot point capacity must be in range 2..10000.")
        self.max_points_per_series = int(max_points_per_series)
        self._series: Tuple[PlotSeries, ...] = ()
        self.setObjectName("debugLivePlot")
        self.setMinimumHeight(220)
        self.setToolTip(
            "Chỉ numeric_value được vẽ. Enum/string vẫn được giữ trong bảng và file export."
        )

    def set_samples(self, samples: Sequence[VariableSample]) -> None:
        self._series = build_numeric_series(samples, self.max_points_per_series)
        self.update()

    def clear(self) -> None:
        self._series = ()
        self.update()

    def series_snapshot(self) -> Tuple[PlotSeries, ...]:
        return self._series

    @staticmethod
    def _series_color(index: int) -> QColor:
        return QColor.fromHsv((index * 67 + 205) % 360, 185, 210)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())

        outer = self.rect().adjusted(8, 8, -8, -8)
        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawRoundedRect(outer, 5, 5)

        if not self._series:
            painter.setPen(palette.placeholderText().color())
            painter.drawText(outer, Qt.AlignmentFlag.AlignCenter, "Chưa có numeric sample để vẽ.")
            return

        plot = QRectF(outer.left() + 58, outer.top() + 30, max(10, outer.width() - 76), max(10, outer.height() - 70))
        all_points = [point for series in self._series for point in series.points]
        x_min = min(point[0] for point in all_points)
        x_max = max(point[0] for point in all_points)
        y_min = min(point[1] for point in all_points)
        y_max = max(point[1] for point in all_points)
        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            pad = max(1.0, abs(y_min) * 0.05)
            y_min -= pad
            y_max += pad

        grid_pen = QPen(palette.mid().color(), 1, Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)
        for step in range(5):
            fraction = step / 4.0
            x = plot.left() + plot.width() * fraction
            y = plot.bottom() - plot.height() * fraction
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        painter.setPen(palette.text().color())
        painter.drawText(QRectF(outer.left() + 4, plot.top() - 8, 50, 20), Qt.AlignmentFlag.AlignRight, "%g" % y_max)
        painter.drawText(QRectF(outer.left() + 4, plot.bottom() - 12, 50, 20), Qt.AlignmentFlag.AlignRight, "%g" % y_min)
        painter.drawText(QRectF(plot.left(), plot.bottom() + 5, 90, 20), Qt.AlignmentFlag.AlignLeft, "%.2fs" % x_min)
        painter.drawText(QRectF(plot.right() - 90, plot.bottom() + 5, 90, 20), Qt.AlignmentFlag.AlignRight, "%.2fs" % x_max)

        def map_point(point):
            x = plot.left() + ((point[0] - x_min) / (x_max - x_min)) * plot.width()
            y = plot.bottom() - ((point[1] - y_min) / (y_max - y_min)) * plot.height()
            return QPointF(x, y)

        legend_x = plot.left()
        legend_y = outer.top() + 5
        for index, series in enumerate(self._series):
            color = self._series_color(index)
            painter.setPen(QPen(color, 2))
            points = tuple(map_point(point) for point in series.points)
            for left, right in zip(points, points[1:]):
                painter.drawLine(left, right)
            if len(points) == 1:
                painter.drawEllipse(points[0], 2.5, 2.5)

            painter.setPen(QPen(color, 3))
            painter.drawLine(QPointF(legend_x, legend_y + 7), QPointF(legend_x + 18, legend_y + 7))
            painter.setPen(palette.text().color())
            label = series.expression
            painter.drawText(QRectF(legend_x + 23, legend_y, 130, 18), Qt.AlignmentFlag.AlignLeft, label)
            legend_x += 155
            if legend_x + 150 > plot.right():
                legend_x = plot.left()
                legend_y += 20
