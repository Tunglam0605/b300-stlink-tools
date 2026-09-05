"""Bounded rendering of received Monitor values; never requests samples."""
from collections import deque
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

class TrendWidget(QWidget):
    CAPACITY = 240
    MAX_SIGNALS = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series = {}
        self._selected = ""
        self.setMinimumHeight(150)
        self.setObjectName("monitorTrend")
        self.setAccessibleName("Giá trị biến đã nhận theo thời gian")

    def append_value(self, name, elapsed, value, coherent=True):
        if not coherent or isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if not math.isfinite(float(value)) or not math.isfinite(float(elapsed)):
            return
        if name not in self._series:
            if len(self._series) >= self.MAX_SIGNALS:
                del self._series[next(iter(self._series))]
            self._series[name] = deque(maxlen=self.CAPACITY)
        self._series[name].append((float(elapsed), float(value)))
        self.update()

    def points(self, name):
        return tuple(self._series.get(name, ()))

    def select_signal(self, name):
        self._selected = name
        self.update()

    def clear(self):
        self._series.clear()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = self.palette().text().color()
        painter.setPen(text_color)
        points = self.points(self._selected)
        if not points:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Chưa nhận được mẫu dạng số")
            return
        plot = QRectF(64, 16, max(1, self.width()-84), max(1, self.height()-48))
        xs, ys = zip(*points)
        low, high = min(ys), max(ys)
        span = high-low or max(abs(high)*.1, 1.)
        if low == high:
            low -= span/2
            high += span/2
        duration = max(xs)-min(xs) or 1.
        painter.setPen(QPen(self.palette().mid().color(), 1))
        for fraction in (0., .5, 1.):
            y = plot.top()+fraction*plot.height()
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(text_color)
        painter.drawText(QRectF(0, 4, 58, 24), Qt.AlignmentFlag.AlignRight, "%.5g" % high)
        painter.drawText(QRectF(0, plot.bottom()-12, 58, 24), Qt.AlignmentFlag.AlignRight, "%.5g" % low)
        painter.drawText(QRectF(plot.left(), plot.bottom()+8, plot.width(), 24),
                         Qt.AlignmentFlag.AlignLeft, "%.2f s" % min(xs))
        painter.drawText(QRectF(plot.left(), plot.bottom()+8, plot.width(), 24),
                         Qt.AlignmentFlag.AlignRight, "%.2f s" % max(xs))
        path = QPainterPath()
        for index, (elapsed, value) in enumerate(points):
            point = QPointF(plot.left()+(elapsed-min(xs))/duration*plot.width(),
                            plot.bottom()-(value-low)/(high-low)*plot.height())
            path.moveTo(point) if index == 0 else path.lineTo(point)
        painter.setPen(QPen(self.palette().highlight().color(), 2))
        if len(points) == 1:
            painter.drawEllipse(path.currentPosition(), 3, 3)
        else:
            painter.drawPath(path)
