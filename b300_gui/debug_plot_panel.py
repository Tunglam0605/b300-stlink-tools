"""Collapsible Live Plot panel for numeric variable waveforms."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from b300_core.debug_sampling import VariableSample, write_samples
from .collapsible_card import CollapsibleCard
from .live_plot import LivePlotWidget


class DebugPlotPanel(CollapsibleCard):
    """Collapsible panel hosting multi-series numeric waveform plot with pause/clear/export."""

    def __init__(self, parent: Optional[QWidget] = None, *, max_points: int = 400) -> None:
        super().__init__(
            "Live Waveform Plot",
            "Realtime numeric signal visualization · default collapsed",
            parent,
            expanded=False,
        )
        self._paused = False
        self._samples_cache: Sequence[VariableSample] = ()
        self._build_ui(max_points)

    def _build_ui(self, max_points: int) -> None:
        content_layout = self.content_layout
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(6)

        # Plot toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.pause_btn = QPushButton("⏸ Pause Display")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        toolbar.addWidget(self.pause_btn)

        self.clear_btn = QPushButton("Clear Plot")
        self.clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(self.clear_btn)

        self.export_btn = QPushButton("Export Plot Data…")
        self.export_btn.clicked.connect(self.export_plot_data)
        toolbar.addWidget(self.export_btn)

        toolbar.addStretch(1)

        self.points_label = QLabel("0 points plotted")
        self.points_label.setStyleSheet("color: #64748B; font-size: 11px;")
        toolbar.addWidget(self.points_label)

        content_layout.addLayout(toolbar)

        # Live Plot Widget
        self.plot_widget = LivePlotWidget(self, max_points_per_series=max_points)
        self.plot_widget.setMinimumHeight(200)
        content_layout.addWidget(self.plot_widget)

    def _on_pause_toggled(self, checked: bool) -> None:
        self._paused = checked
        self.pause_btn.setText("▶ Resume Display" if checked else "⏸ Pause Display")
        if not checked and self._samples_cache:
            self.plot_widget.set_samples(self._samples_cache)

    def set_samples(self, samples: Sequence[VariableSample]) -> None:
        self._samples_cache = samples
        if not self._paused:
            self.plot_widget.set_samples(samples)
            series = self.plot_widget.series_snapshot()
            total_points = sum(len(s.points) for s in series)
            self.points_label.setText("%d points · %d series" % (total_points, len(series)))

    def clear(self) -> None:
        self._samples_cache = ()
        self.plot_widget.clear()
        self.points_label.setText("0 points plotted")

    def export_plot_data(self, parent: Optional[QWidget] = None) -> Optional[Path]:
        if not self._samples_cache:
            return None
        path, _selected = QFileDialog.getSaveFileName(
            parent or self, "Export Plot Data", "b300-plot-data.csv",
            "CSV (*.csv);;JSON Lines (*.jsonl)",
        )
        if not path:
            return None
        destination = Path(path)
        if destination.suffix.lower() not in {".csv", ".jsonl"}:
            destination = destination.with_suffix(".csv")
        return write_samples(destination, self._samples_cache)
