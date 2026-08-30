"""Presentation-only Live Variables panel shared by DebugTab sampling workflows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import (
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from b300_core.debug_sampling import (
    VariableSample, VariableSampleBuffer, validate_sampling_request, write_samples,
)
from .live_plot import LivePlotWidget


class LiveVariablesPanel(QGroupBox):
    """Own Live Variables controls/history/rendering while DebugTab owns GDB/session state."""

    BUFFER_CAPACITY = 2000
    PLOT_POINTS_PER_SERIES = 400

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Live Variables · bounded sampling", parent)
        self.buffer = VariableSampleBuffer(max_samples=self.BUFFER_CAPACITY)
        self.rows = {}
        self._build_ui()

    def _build_ui(self) -> None:
        live_layout = QVBoxLayout(self)
        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(8)

        controls.addWidget(QLabel("Biến:"), 0, 0)
        self.expressions = QLineEdit()
        self.expressions.setObjectName("debugSampleExpressions")
        self.expressions.setPlaceholderText("xTickCount, motorSpeed, current")
        self.expressions.setToolTip(
            "Tối đa 16 biểu thức an toàn, phân tách bằng dấu phẩy hoặc chấm phẩy."
        )
        controls.addWidget(self.expressions, 0, 1, 1, 4)

        controls.addWidget(QLabel("Chu kỳ:"), 1, 0)
        self.cycles = QSpinBox()
        self.cycles.setObjectName("debugSampleCycles")
        self.cycles.setRange(1, 1000)
        self.cycles.setValue(100)
        controls.addWidget(self.cycles, 1, 1)

        controls.addWidget(QLabel("Khoảng:"), 1, 2)
        self.interval = QDoubleSpinBox()
        self.interval.setObjectName("debugSampleInterval")
        self.interval.setRange(0.1, 60.0)
        self.interval.setDecimals(2)
        self.interval.setSingleStep(0.1)
        self.interval.setValue(0.5)
        self.interval.setSuffix(" s")
        controls.addWidget(self.interval, 1, 3)

        self.start_button = QPushButton("Start Sampling")
        self.start_button.setObjectName("debugSampleStartButton")
        self.stop_button = QPushButton("Stop Sampling")
        self.stop_button.setObjectName("debugSampleStopButton")
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("debugSampleClearButton")
        self.export_button = QPushButton("Export CSV/JSONL")
        self.export_button.setObjectName("debugSampleExportButton")
        controls.addWidget(self.start_button, 2, 0)
        controls.addWidget(self.stop_button, 2, 1)
        controls.addWidget(self.clear_button, 2, 2)
        controls.addWidget(self.export_button, 2, 3)

        self.status = QLabel("Chưa có mẫu · buffer tối đa 2000 điểm")
        self.status.setObjectName("debugSampleStatus")
        self.status.setStyleSheet("color: #64748B;")
        controls.addWidget(self.status, 2, 4)
        controls.setColumnStretch(4, 1)
        live_layout.addLayout(controls)

        self.impact = QLabel(
            "Lưu ý: GDB sampling sẽ HALT target rất ngắn ở mỗi chu kỳ rồi khôi phục RUNNING. "
            "Dùng để chẩn đoán/quan sát, không dùng làm phép đo timing hard real-time."
        )
        self.impact.setObjectName("debugSampleImpact")
        self.impact.setWordWrap(True)
        self.impact.setStyleSheet("color: #92400E; font-size: 12px;")
        live_layout.addWidget(self.impact)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("debugSampleTable")
        self.table.setHorizontalHeaderLabels(
            ("Variable", "Raw value", "Numeric", "Cycle", "Time (s)")
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.setMinimumHeight(150)
        live_layout.addWidget(self.table)

        self.plot = LivePlotWidget(max_points_per_series=self.PLOT_POINTS_PER_SERIES)
        live_layout.addWidget(self.plot)

    def validated_request(self):
        raw = self.expressions.text().strip()
        expressions = tuple(
            item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()
        )
        return validate_sampling_request(expressions, self.cycles.value(), self.interval.value())

    def set_control_state(self, *, start_enabled: bool, stop_enabled: bool,
                          history_enabled: bool) -> None:
        self.expressions.setEnabled(start_enabled)
        self.cycles.setEnabled(start_enabled)
        self.interval.setEnabled(start_enabled)
        self.start_button.setEnabled(start_enabled)
        self.stop_button.setEnabled(stop_enabled)
        self.clear_button.setEnabled(history_enabled)
        self.export_button.setEnabled(history_enabled)

    def reset_for_sampling(self) -> None:
        self.buffer.clear()
        self.rows.clear()
        self.table.setRowCount(0)
        self.plot.clear()
        self.status.setText("Đang lấy mẫu 0/%d chu kỳ..." % self.cycles.value())

    def mark_stopping(self) -> None:
        self.status.setText("Đang dừng sampling an toàn...")
        self.stop_button.setEnabled(False)

    def append_batch(self, batch: Sequence[VariableSample]) -> None:
        selected = tuple(batch)
        if not selected:
            return
        self.buffer.extend(selected)
        self.plot.set_samples(self.buffer.snapshot())
        for sample in selected:
            row = self.rows.get(sample.expression)
            if row is None:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.rows[sample.expression] = row
                self.table.setItem(row, 0, QTableWidgetItem(sample.expression))
            self.table.setItem(row, 1, QTableWidgetItem(sample.raw_value))
            numeric = "—" if sample.numeric_value is None else "%g" % sample.numeric_value
            self.table.setItem(row, 2, QTableWidgetItem(numeric))
            self.table.setItem(row, 3, QTableWidgetItem(str(sample.cycle)))
            self.table.setItem(row, 4, QTableWidgetItem("%.3f" % sample.elapsed_seconds))
        last_cycle = max(sample.cycle for sample in selected) + 1
        self.status.setText(
            "%d/%d chu kỳ · %d biến · buffer %d/%d" % (
                last_cycle, self.cycles.value(), len(selected), len(self.buffer), self.BUFFER_CAPACITY
            )
        )

    def mark_completed(self, samples: Sequence[VariableSample]) -> int:
        selected = tuple(samples)
        cycles = 0 if not selected else max(sample.cycle for sample in selected) + 1
        self.status.setText(
            "Hoàn tất %d chu kỳ · %d điểm trong buffer" % (cycles, len(self.buffer))
        )
        return cycles

    def mark_failed(self, message: str) -> None:
        self.status.setText("Sampling lỗi: %s" % message)

    def clear_history(self) -> None:
        self.buffer.clear()
        self.rows.clear()
        self.table.setRowCount(0)
        self.plot.clear()
        self.status.setText("Chưa có mẫu · buffer tối đa %d điểm" % self.BUFFER_CAPACITY)

    def export_samples(self, parent: QWidget | None = None) -> Path | None:
        samples = self.buffer.snapshot()
        if not samples:
            raise ValueError("Chưa có sample để export.")
        path, _selected = QFileDialog.getSaveFileName(
            parent or self, "Export Live Variables", "b300-debug-samples.csv",
            "CSV (*.csv);;JSON Lines (*.jsonl)",
        )
        if not path:
            return None
        destination = Path(path)
        if destination.suffix.lower() not in {".csv", ".jsonl"}:
            destination = destination.with_suffix(".csv")
        saved = write_samples(destination, samples)
        self.status.setText("Đã export %d điểm → %s" % (len(samples), saved.name))
        return saved
