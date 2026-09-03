"""Realtime Live Monitor panel for zero-halt DWT PC and RAM variable monitoring."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from b300_core.debug_sampling import (
    VariableSample, VariableSampleBuffer, validate_sampling_request, write_samples,
)
from b300_core.live_monitor import LiveSample, validate_live_watch_specs, save_watch_preset, load_watch_preset

from .collapsible_card import CollapsibleCard


class DebugLivePanel(QFrame):
    """Zero-halt realtime SWD monitor displaying Execution Timeline and Live Variables."""

    symbol_browser_requested = Signal()

    TIMELINE_CAPACITY = 1000
    VARIABLES_CAPACITY = 2000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardSurface")
        self.buffer = VariableSampleBuffer(max_samples=self.VARIABLES_CAPACITY)
        self.rows: Dict[str, int] = {}
        self._timeline_samples: List[dict] = []
        self._live_variable_items: List[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        panel_layout = QVBoxLayout(self)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.setSpacing(8)

        # Zone Header: Clean Title
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        card_title = QLabel("📊  THEO DÕI BIẾN REALTIME")
        card_title.setObjectName("CardTitle")
        header_row.addWidget(card_title)
        header_row.addStretch(1)

        self.workflow_hint = QLabel("")
        self.workflow_hint.setVisible(False)
        self.statistical_notice = QLabel("")
        self.statistical_notice.setVisible(False)

        panel_layout.addLayout(header_row)

        # Responsive sampling toolbar.  Status occupies its own line so the
        # compact Debug workspace never clips an action or connection state.
        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(5)
        action_row = QGridLayout()
        action_row.setHorizontalSpacing(6)
        action_row.setVerticalSpacing(5)
        action_row.setColumnStretch(0, 1)
        action_row.setColumnStretch(1, 1)

        self.start_button = QPushButton("▶ Bắt đầu")
        self.start_button.setObjectName("primaryButton")
        action_row.addWidget(self.start_button, 0, 0)

        self.stop_button = QPushButton("⏹ Dừng")
        self.stop_button.setObjectName("ghostButton")
        self.stop_button.setEnabled(False)
        action_row.addWidget(self.stop_button, 0, 1)

        lbl_spd = QLabel("Tốc độ:")
        lbl_spd.setObjectName("fieldLabel")
        self.clear_button = QPushButton("Xóa")
        self.clear_button.setObjectName("ghostButton")
        self.export_button = QPushButton("Xuất…")
        self.export_button.setObjectName("ghostButton")
        action_row.addWidget(self.clear_button, 1, 0)

        self.interval_preset_combo = QComboBox()
        self.interval_preset_combo.addItem("0.1 s", 0.1)
        self.interval_preset_combo.addItem("0.2 s", 0.2)
        self.interval_preset_combo.addItem("0.5 s", 0.5)
        self.interval_preset_combo.addItem("1.0 s", 1.0)
        self.interval_preset_combo.addItem("2.0 s", 2.0)
        self.interval_preset_combo.addItem("5.0 s", 5.0)
        self.interval_preset_combo.addItem("Tùy chỉnh", -1.0)
        self.interval_preset_combo.setCurrentIndex(2)  # default 0.5s
        self.interval_preset_combo.currentIndexChanged.connect(self._on_interval_preset_changed)
        action_row.addWidget(self.export_button, 1, 1)
        controls.addLayout(action_row)

        sampling_row = QGridLayout()
        sampling_row.setHorizontalSpacing(6)
        sampling_row.setVerticalSpacing(5)
        sampling_row.setColumnStretch(1, 1)
        sampling_row.addWidget(lbl_spd, 0, 0)
        sampling_row.addWidget(self.interval_preset_combo, 0, 1)

        self.interval = QDoubleSpinBox()
        self.interval.setObjectName("debugSampleInterval")
        self.interval.setRange(0.1, 60.0)
        self.interval.setDecimals(2)
        self.interval.setSingleStep(0.1)
        self.interval.setValue(0.5)
        self.interval.setSuffix(" s")
        self.interval.setVisible(False)
        self.interval.valueChanged.connect(self._on_custom_interval_changed)

        self.limit_samples = QCheckBox("Giới hạn")
        self.limit_samples.setObjectName("debugLimitSamples")
        self.limit_samples.setChecked(False)
        self.limit_samples.setToolTip("Mặc định chạy liên tục đến khi bấm Dừng.")
        sampling_row.addWidget(self.limit_samples, 1, 0, 1, 2)
        controls.addLayout(sampling_row)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(6)
        detail_row.addWidget(self.interval)

        self.cycles_label = QLabel("Số mẫu:")
        self.cycles_label.setObjectName("fieldLabel")
        self.cycles = QSpinBox()
        self.cycles.setObjectName("debugSampleCycles")
        self.cycles.setRange(1, 100000)
        self.cycles.setValue(100)
        self.cycles_label.setVisible(False)
        self.cycles.setVisible(False)
        self.limit_samples.toggled.connect(self._on_limit_samples_toggled)
        detail_row.addWidget(self.cycles_label)
        detail_row.addWidget(self.cycles)
        detail_row.addStretch(1)
        controls.addLayout(detail_row)

        self.status = QLabel("Sẵn sàng · 0 mẫu")
        self.status.setObjectName("debugSampleStatus")
        self.status.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.status.setWordWrap(False)
        controls.addWidget(self.status)

        panel_layout.addLayout(controls)

        self.stats_frame = QFrame()
        self.stats_frame.setObjectName("debugLiveStatsFrame")
        stats = QGridLayout(self.stats_frame)
        stats.setContentsMargins(8, 5, 8, 5)
        stats.setHorizontalSpacing(18)
        stats.setVerticalSpacing(2)
        self.stats_samples = QLabel("Mẫu: 0")
        self.stats_overruns = QLabel("Trễ nhịp: 0")
        self.stats_mean_read = QLabel("Đọc TB: —")
        self.stats_max_lag = QLabel("Trễ max: —")
        self.stats_incoherent = QLabel("Không nhất quán: 0")
        self.stats_variables = QLabel("Biến: 0")
        for label in (self.stats_samples, self.stats_overruns, self.stats_mean_read,
                      self.stats_max_lag, self.stats_incoherent, self.stats_variables):
            label.setStyleSheet("color: #334155; font-size: 11px; font-weight: 600;")
        stats.addWidget(self.stats_samples, 0, 0)
        stats.addWidget(self.stats_overruns, 0, 1)
        stats.addWidget(self.stats_mean_read, 0, 2)
        stats.addWidget(self.stats_max_lag, 0, 3)
        stats.addWidget(self.stats_incoherent, 0, 4)
        stats.addWidget(self.stats_variables, 0, 5)
        stats.setColumnStretch(6, 1)
        self.quality_details = CollapsibleCard(
            "Chất lượng lấy mẫu",
            "Overrun · độ trễ",
            expanded=False,
        )
        self.quality_details.content_layout.addWidget(self.statistical_notice)
        self.quality_details.content_layout.addWidget(self.stats_frame)
        panel_layout.addWidget(self.quality_details)

        # Focused views use tabs instead of side-by-side panes. This cuts visual
        # density and removes the wide minimum size imposed by two live tables.
        self.view_tabs = QTabWidget()
        self.view_tabs.setObjectName("debugLiveViewTabs")

        # Execution Timeline
        timeline_container = QWidget()
        timeline_layout = QVBoxLayout(timeline_container)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(4)

        timeline_header = QHBoxLayout()
        self.follow_latest_check = QCheckBox("Theo mẫu mới nhất")
        self.follow_latest_check.setChecked(True)
        timeline_header.addWidget(self.follow_latest_check)
        timeline_header.addStretch(1)
        timeline_layout.addLayout(timeline_header)

        self.timeline_table = QTableWidget(0, 5)
        self.timeline_table.setObjectName("timelineTable")
        self.timeline_table.setHorizontalHeaderLabels(("Thời gian", "PC", "Hàm", "File", "Dòng"))
        self.timeline_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.timeline_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.verticalHeader().setDefaultSectionSize(22)
        header = self.timeline_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.timeline_table.setColumnWidth(0, 84)
        self.timeline_table.setColumnWidth(1, 104)
        self.timeline_table.setColumnWidth(3, 180)
        self.timeline_table.setColumnWidth(4, 58)
        self.timeline_table.setMinimumHeight(240)
        timeline_layout.addWidget(self.timeline_table)


        # Live Variables
        variables_container = QWidget()
        variables_layout = QVBoxLayout(variables_container)
        variables_layout.setContentsMargins(0, 4, 0, 0)
        variables_layout.setSpacing(6)

        variable_toolbar = QGridLayout()
        variable_toolbar.setHorizontalSpacing(2)
        variable_toolbar.setVerticalSpacing(6)

        lbl_v = QLabel("Biến:")
        lbl_v.setObjectName("fieldLabel")
        variable_toolbar.addWidget(lbl_v, 0, 0)

        self.expressions = QLineEdit()
        self.expressions.setObjectName("debugSampleExpressions")
        self.expressions.setPlaceholderText("Tên biến global (vd: xTickCount hoặc Com3RxDmaBuffer)")
        self.expressions.setToolTip("Nhập tên biến global có trong file AXF/ELF.")
        self.expressions.setMinimumWidth(180)
        self.expressions.setMaximumWidth(320)
        variable_toolbar.addWidget(self.expressions, 0, 1)

        self.type_combo = QComboBox()
        for t in ("u32", "i32", "u16", "i16", "u8", "i8", "f32", "f64"):
            self.type_combo.addItem(t)
        variable_toolbar.addWidget(self.type_combo, 1, 0)

        self.add_watch_btn = QPushButton("+ Thêm")
        self.add_watch_btn.setObjectName("primaryButton")
        self.add_watch_btn.clicked.connect(self._on_add_watch_clicked)
        variable_toolbar.addWidget(self.add_watch_btn, 1, 1)

        self.remove_watch_btn = QPushButton("Xóa biến")
        self.remove_watch_btn.setObjectName("ghostButton")
        self.remove_watch_btn.clicked.connect(self._on_remove_watch_clicked)
        variable_toolbar.addWidget(self.remove_watch_btn, 2, 0)

        self.browse_symbols_btn = QPushButton("AXF/ELF…")
        self.browse_symbols_btn.setObjectName("ghostButton")
        self.browse_symbols_btn.setToolTip(
            "Duyệt symbol offline từ AXF/ELF; thao tác này không truy cập hoặc halt STM32."
        )
        self.browse_symbols_btn.clicked.connect(self.symbol_browser_requested.emit)
        variable_toolbar.addWidget(self.browse_symbols_btn, 2, 1)

        self.load_preset_btn = QPushButton("Nạp…")
        self.load_preset_btn.setObjectName("ghostButton")
        self.load_preset_btn.setToolTip("Nạp danh sách biến theo dõi từ file JSON.")
        self.load_preset_btn.clicked.connect(self._on_load_preset_clicked)
        variable_toolbar.addWidget(self.load_preset_btn, 3, 0)

        self.save_preset_btn = QPushButton("Lưu…")
        self.save_preset_btn.setObjectName("ghostButton")
        self.save_preset_btn.setToolTip("Lưu danh sách biến hiện tại ra file JSON.")
        self.save_preset_btn.clicked.connect(self._on_save_preset_clicked)
        variable_toolbar.addWidget(self.save_preset_btn, 3, 1)
        variable_toolbar.setColumnStretch(1, 1)

        variables_layout.addLayout(variable_toolbar)

        self.table = QTableWidget(0, 9)
        self.table.setObjectName("debugSampleTable")
        self.table.setHorizontalHeaderLabels((
            "Biến", "Giá trị", "Kiểu", "Địa chỉ", "Thời gian", "Đồ thị",
            "Min", "Max", "Mean",
        ))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(240)
        variables_layout.addWidget(self.table)

        self.view_tabs.addTab(variables_container, "Biến theo dõi")
        self.view_tabs.addTab(timeline_container, "Luồng thực thi")
        self.view_tabs.setCurrentIndex(0)
        panel_layout.addWidget(self.view_tabs)
        # Backward-compatible attribute name for code that only expects a
        # container; no production code relies on QSplitter-specific methods.
        self.splitter = self.view_tabs
        self.quality_details.expanded_changed.connect(self._on_quality_details_changed)
        self._on_quality_details_changed(False)

        # Legacy compatibility alias
        self.impact = self.statistical_notice

    def _on_limit_samples_toggled(self, enabled: bool) -> None:
        self.cycles_label.setVisible(bool(enabled))
        self.cycles.setVisible(bool(enabled))
        self.cycles.setEnabled(bool(enabled) and self.start_button.isEnabled())

    def _on_quality_details_changed(self, expanded: bool) -> None:
        # Keep the normal operator table focused on value/type/time/plot. Raw
        # address and statistical columns appear only with technical details.
        for column in (3, 6, 7, 8):
            self.table.setColumnHidden(column, not bool(expanded))

    def sample_limit(self):
        """Return None for continuous mode or the explicit bounded sample count."""
        return int(self.cycles.value()) if self.limit_samples.isChecked() else None

    def _on_interval_preset_changed(self, index: int) -> None:
        val = self.interval_preset_combo.itemData(index)
        is_custom = val is None or float(val) <= 0
        self.interval.setVisible(is_custom)
        if not is_custom:
            self.interval.blockSignals(True)
            self.interval.setValue(float(val))
            self.interval.blockSignals(False)

    def _on_custom_interval_changed(self, val: float) -> None:
        matched = False
        for i in range(self.interval_preset_combo.count()):
            data_val = self.interval_preset_combo.itemData(i)
            if abs(data_val - val) < 0.01:
                self.interval_preset_combo.setCurrentIndex(i)
                matched = True
                break
        if not matched:
            self.interval_preset_combo.setCurrentIndex(self.interval_preset_combo.count() - 1)

    def select_symbol(self, name: str) -> None:
        """Populate the manual watch field without inferring its C data type."""
        self.expressions.setText(str(name).strip())
        self.expressions.setFocus()

    def _on_add_watch_clicked(self) -> None:
        name = self.expressions.text().strip()
        if not name:
            return
        type_str = self.type_combo.currentText()
        spec = "%s:%s" % (name, type_str)
        current = [s.strip() for s in self.expressions.text().split(",") if s.strip()]
        if spec not in current:
            # Add row to variable table
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.rows[name] = row
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem(type_str))
            self.table.setItem(row, 3, QTableWidgetItem("—"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 5, check_item)

    def _on_remove_watch_clicked(self) -> None:
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        for row in selected_rows:
            var_name = self.table.item(row, 0).text() if self.table.item(row, 0) else None
            if var_name in self.rows:
                del self.rows[var_name]
            self.table.removeRow(row)
        # Re-index rows
        self.rows.clear()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                self.rows[item.text()] = r

    def watch_specs(self) -> Tuple[str, ...]:
        specs = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            type_item = self.table.item(row, 2)
            if name_item is None or not name_item.text().strip():
                continue
            name = name_item.text().strip()
            value_type = type_item.text().strip() if type_item and type_item.text().strip() else self.type_combo.currentText()
            specs.append("%s:%s" % (name, value_type))
        if not specs:
            raw = self.expressions.text().strip()
            if raw:
                import re
                for item in (part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()):
                    specs.append(item if ":" in item else "%s:%s" % (item, self.type_combo.currentText()))
        validate_live_watch_specs(specs)
        return tuple(specs)

    def append_live_sample(self, sample: LiveSample) -> Tuple[VariableSample, ...]:
        self.append_timeline_sample(
            sample.captured_elapsed_seconds, sample.pc, sample.source.function or "??",
            sample.source.file or "??", sample.source.line,
        )
        converted = []
        for value in sample.values:
            row = self.rows.get(value.name)
            if row is None:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.rows[value.name] = row
                self.table.setItem(row, 0, QTableWidgetItem(value.name))
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                check_item.setCheckState(Qt.CheckState.Checked)
                self.table.setItem(row, 5, check_item)
            raw_value = str(value.value) if value.coherent else "<incoherent>"
            numeric = None
            if value.coherent and isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
                numeric = float(value.value)
            self.table.setItem(row, 1, QTableWidgetItem(raw_value))
            self.table.setItem(row, 2, QTableWidgetItem(value.value_type))
            self.table.setItem(row, 3, QTableWidgetItem("0x%08X" % value.address))
            self.table.setItem(row, 4, QTableWidgetItem("%.3f" % sample.captured_elapsed_seconds))
            converted.append(VariableSample(
                cycle=sample.cycle, elapsed_seconds=sample.captured_elapsed_seconds,
                captured_at_unix_ms=0, expression=value.name, raw_value=raw_value,
                numeric_value=numeric,
            ))
        self.buffer.extend(converted)
        coherence_failures = sum(1 for value in sample.values if not value.coherent)
        suffix = " · incoherent %d" % coherence_failures if coherence_failures else ""
        limit = self.sample_limit()
        progress = "%d/%d mẫu" % (sample.cycle + 1, limit) if limit is not None else "%d mẫu · Liên tục" % (sample.cycle + 1)
        self.status.setText(
            "%s · %d biến · đọc %.1f ms%s" % (
                progress, len(sample.values), sample.read_duration_seconds * 1000.0, suffix,
            )
        )
        return tuple(converted)

    @staticmethod
    def _format_stat_value(value) -> str:
        if value is None:
            return "—"
        try:
            return "%.6g" % float(value)
        except (TypeError, ValueError, OverflowError):
            return str(value)

    def apply_analytics(self, snapshot) -> None:
        """Render already-collected LiveMonitorStore statistics without new target reads."""
        timing = snapshot.timing
        variables = tuple(getattr(snapshot, "variables", ()) or ())
        self.stats_samples.setText("Mẫu: %d" % int(getattr(timing, "total_samples", 0)))
        self.stats_overruns.setText("Trễ nhịp: %d" % int(getattr(timing, "overruns", 0)))
        self.stats_mean_read.setText(
            "Đọc TB: %.2f ms" % (float(getattr(timing, "mean_read_duration_seconds", 0.0)) * 1000.0)
        )
        self.stats_max_lag.setText(
            "Trễ max: %.2f ms" % (float(getattr(timing, "max_schedule_lag_seconds", 0.0)) * 1000.0)
        )
        self.stats_incoherent.setText(
            "Không nhất quán: %d" % int(getattr(timing, "incoherent_values", 0))
        )
        self.stats_variables.setText("Biến: %d" % len(variables))
        for stat in variables:
            row = self.rows.get(stat.name)
            if row is None:
                continue
            self.table.setItem(row, 6, QTableWidgetItem(self._format_stat_value(stat.minimum)))
            self.table.setItem(row, 7, QTableWidgetItem(self._format_stat_value(stat.maximum)))
            self.table.setItem(row, 8, QTableWidgetItem(self._format_stat_value(stat.mean)))

    def reset_analytics(self) -> None:
        self.stats_samples.setText("Mẫu: 0")
        self.stats_overruns.setText("Trễ nhịp: 0")
        self.stats_mean_read.setText("Đọc TB: —")
        self.stats_max_lag.setText("Trễ max: —")
        self.stats_incoherent.setText("Không nhất quán: 0")
        self.stats_variables.setText("Biến: 0")

    def mark_live_completed(self, summary) -> None:
        action = "Đã dừng" if getattr(summary, "cancelled", False) else "Đã hoàn tất"
        self.status.setText(
            "%s · %d mẫu · overrun %d · target %s" % (
                action, summary.samples, summary.overruns, str(summary.final_target_state).upper(),
            )
        )

    def validated_request(self) -> Tuple[str, ...]:
        raw = self.expressions.text().strip()
        if not raw and self.table.rowCount() > 0:
            # Harvest from table
            names = []
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item and item.text().strip():
                    names.append(item.text().strip())
            return tuple(names)
        import re
        expressions = tuple(
            item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()
        )
        return validate_sampling_request(expressions, self.cycles.value(), self.interval.value())

    def set_control_state(
        self, *, start_enabled: bool, stop_enabled: bool, history_enabled: bool
    ) -> None:
        self.expressions.setEnabled(start_enabled)
        self.browse_symbols_btn.setEnabled(start_enabled)
        self.load_preset_btn.setEnabled(start_enabled)
        self.save_preset_btn.setEnabled(start_enabled)
        self.type_combo.setEnabled(start_enabled)
        self.add_watch_btn.setEnabled(start_enabled)
        self.remove_watch_btn.setEnabled(start_enabled)
        self.interval_preset_combo.setEnabled(start_enabled)
        self.interval.setEnabled(start_enabled)
        self.limit_samples.setEnabled(start_enabled)
        self.cycles.setEnabled(start_enabled and self.limit_samples.isChecked())
        self.start_button.setEnabled(start_enabled)
        self.stop_button.setEnabled(stop_enabled)
        self.clear_button.setEnabled(history_enabled)
        self.export_button.setEnabled(history_enabled)

    def reset_for_sampling(self) -> None:
        self.buffer.clear()
        self.rows.clear()
        self.table.setRowCount(0)
        self.timeline_table.setRowCount(0)
        self._timeline_samples.clear()
        self.reset_analytics()
        limit = self.sample_limit()
        self.status.setText("Đang theo dõi · 0/%d mẫu" % limit if limit is not None else "Đang theo dõi liên tục · 0 mẫu")

    def mark_stopping(self) -> None:
        self.status.setText("Đang dừng an toàn...")
        self.stop_button.setEnabled(False)

    def append_batch(self, batch: Sequence[VariableSample]) -> None:
        selected = tuple(batch)
        if not selected:
            return
        self.buffer.extend(selected)
        for sample in selected:
            row = self.rows.get(sample.expression)
            if row is None:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.rows[sample.expression] = row
                self.table.setItem(row, 0, QTableWidgetItem(sample.expression))
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                check_item.setCheckState(Qt.CheckState.Checked)
                self.table.setItem(row, 5, check_item)
            self.table.setItem(row, 1, QTableWidgetItem(sample.raw_value))
            numeric = "—" if sample.numeric_value is None else "%g" % sample.numeric_value
            self.table.setItem(row, 2, QTableWidgetItem(type(sample.numeric_value).__name__ if sample.numeric_value is not None else "str"))
            self.table.setItem(row, 3, QTableWidgetItem("RAM"))
            self.table.setItem(row, 4, QTableWidgetItem("%.3f" % sample.elapsed_seconds))

        last_cycle = max(sample.cycle for sample in selected) + 1
        limit = self.sample_limit()
        progress = "%d/%d mẫu" % (last_cycle, limit) if limit is not None else "%d mẫu · Liên tục" % last_cycle
        self.status.setText(
            "%s · %d biến · buffer %d/%d" % (
                progress, len(selected), len(self.buffer), self.VARIABLES_CAPACITY,
            )
        )

    def _append_timeline_row(self, item: dict) -> None:
        row = self.timeline_table.rowCount()
        self.timeline_table.insertRow(row)
        self.timeline_table.setItem(row, 0, QTableWidgetItem("%.3fs" % item["elapsed_s"]))
        self.timeline_table.setItem(row, 1, QTableWidgetItem(item["pc"]))
        self.timeline_table.setItem(row, 2, QTableWidgetItem(item["function"] or "??"))
        self.timeline_table.setItem(row, 3, QTableWidgetItem(item["file"] or "??"))
        line = item["line"]
        self.timeline_table.setItem(row, 4, QTableWidgetItem(str(line) if line is not None else "??"))

    def _rebuild_timeline_view(self) -> None:
        self.timeline_table.setUpdatesEnabled(False)
        try:
            self.timeline_table.setRowCount(0)
            for item in self._timeline_samples:
                self._append_timeline_row(item)
        finally:
            self.timeline_table.setUpdatesEnabled(True)

    def append_timeline_sample(
        self, elapsed_s: float, pc: int, function_name: str, file_name: str, line: int
    ) -> None:
        item = {
            "elapsed_s": elapsed_s, "pc": "0x%08X" % pc,
            "function": function_name, "file": file_name, "line": line,
        }
        self._timeline_samples.append(item)

        if len(self._timeline_samples) > self.TIMELINE_CAPACITY:
            # Removing row 0 on every 10 Hz sample becomes O(n) and makes long
            # sessions progressively expensive.  Trim one quarter in a batch,
            # then rebuild the bounded viewport once.
            retain = max(1, self.TIMELINE_CAPACITY * 3 // 4)
            self._timeline_samples[:] = self._timeline_samples[-retain:]
            self._rebuild_timeline_view()
        else:
            self._append_timeline_row(item)

        if self.follow_latest_check.isChecked():
            self.timeline_table.scrollToBottom()

    def mark_completed(self, samples: Sequence[VariableSample]) -> int:
        selected = tuple(samples)
        cycles = 0 if not selected else max(sample.cycle for sample in selected) + 1
        self.status.setText(
            "Completed %d cycles · %d points in buffer" % (cycles, len(self.buffer))
        )
        return cycles

    def mark_failed(self, message: str) -> None:
        self.status.setText("Live error: %s" % message)

    def clear_history(self) -> None:
        self.buffer.clear()
        self.rows.clear()
        self.table.setRowCount(0)
        self.timeline_table.setRowCount(0)
        self._timeline_samples.clear()
        self.reset_analytics()
        self.status.setText("Sẵn sàng · 0 mẫu")

    def export_samples(self, parent: Optional[QWidget] = None) -> Optional[Path]:
        samples = self.buffer.snapshot()
        if not samples and not self._timeline_samples:
            raise ValueError("Chưa có sample để export.")
        path, _selected = QFileDialog.getSaveFileName(
            parent or self, "Xuất dữ liệu theo dõi", "b300-debug-samples.csv",
            "CSV (*.csv);;JSON Lines (*.jsonl)",
        )
        if not path:
            return None
        destination = Path(path)
        if destination.suffix.lower() not in {".csv", ".jsonl"}:
            destination = destination.with_suffix(".csv")

        if samples and not self._timeline_samples:
            saved = write_samples(destination, samples)
            self.status.setText("Đã xuất %d điểm → %s" % (len(samples), saved.name))
            return saved

        if destination.suffix.lower() == ".jsonl":
            with destination.open("w", encoding="utf-8") as f:
                for item in self._timeline_samples:
                    f.write(json.dumps({"type": "timeline", **item}) + "\n")
                for s in samples:
                    f.write(json.dumps({
                        "type": "variable", "cycle": s.cycle, "elapsed_seconds": s.elapsed_seconds,
                        "expression": s.expression, "raw_value": s.raw_value, "numeric_value": s.numeric_value,
                    }) + "\n")
        else:
            with destination.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Type", "Time_s", "PC_or_Variable", "Function_or_Value", "File_or_Type", "Line_or_Numeric"])
                for item in self._timeline_samples:
                    writer.writerow(["TIMELINE", "%.3f" % item["elapsed_s"], item["pc"], item["function"], item["file"], item["line"]])
                for s in samples:
                    writer.writerow(["VARIABLE", "%.3f" % s.elapsed_seconds, s.expression, s.raw_value, type(s.numeric_value).__name__, s.numeric_value])

        self.status.setText("Đã xuất %d điểm → %s" % (len(samples) + len(self._timeline_samples), destination.name))
        return destination

    def export_preset(self, path: Optional[Union[str, Path]] = None, name: Optional[str] = None) -> Optional[Path]:
        """Export current watch variables and settings to a JSON preset file."""
        specs = []
        plot_flags = {}
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            type_item = self.table.item(row, 2)
            check_item = self.table.item(row, 5)
            if not name_item or not name_item.text().strip():
                continue
            v_name = name_item.text().strip()
            v_type = type_item.text().strip() if type_item and type_item.text().strip() else self.type_combo.currentText()
            specs.append("%s:%s" % (v_name, v_type))
            plot_flags[v_name] = check_item.checkState() == Qt.CheckState.Checked if check_item else True
        if not specs:
            raw = self.expressions.text().strip()
            if raw:
                import re
                for item in (part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()):
                    v_name = item.split(":", 1)[0].strip() if ":" in item else item
                    specs.append(item if ":" in item else "%s:%s" % (item, self.type_combo.currentText()))
                    plot_flags[v_name] = True
        if not specs:
            raise ValueError("Không có biến nào trong danh sách để lưu preset.")

        interval_val = float(self.interval.value())
        sample_lim = int(self.cycles.value()) if self.limit_samples.isChecked() else None

        if path is None:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Lưu danh sách biến (Preset)", "b300-watch-preset.json",
                "JSON Preset (*.json);;All Files (*)",
            )
            if not file_path:
                return None
            target_path = Path(file_path)
        else:
            target_path = Path(path)

        if target_path.suffix.lower() != ".json":
            target_path = target_path.with_suffix(".json")

        saved = save_watch_preset(
            target_path, specs, name=name or "B300 Live Watch Preset",
            interval_seconds=interval_val, sample_limit=sample_lim,
            plot_flags=plot_flags,
        )
        self.status.setText("Đã lưu preset %d biến → %s" % (len(specs), saved.name))
        return saved

    def import_preset(self, path: Optional[Union[str, Path]] = None, mode: str = "replace") -> Optional[dict]:
        """Import watch variables from a JSON preset file."""
        if path is None:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Nạp danh sách biến (Preset)", "",
                "JSON Preset (*.json);;All Files (*)",
            )
            if not file_path:
                return None
            target_path = Path(file_path)
        else:
            target_path = Path(path)

        data = load_watch_preset(target_path)
        if mode == "replace":
            self.table.setRowCount(0)
            self.rows.clear()

        # Update interval if specified in preset
        if data.get("interval_seconds") is not None:
            self._set_interval_value(data["interval_seconds"])

        # Update sample limit if specified
        if data.get("sample_limit") is not None:
            self.limit_samples.setChecked(True)
            self.cycles.setValue(int(data["sample_limit"]))

        # Populate rows
        for watch in data.get("watches", []):
            name = watch["name"]
            val_type = watch["type"]
            plot_state = watch.get("plot", True)
            if name in self.rows:
                row = self.rows[name]
            else:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.rows[name] = row

            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem(val_type))
            self.table.setItem(row, 3, QTableWidgetItem("—"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Checked if plot_state else Qt.CheckState.Unchecked)
            self.table.setItem(row, 5, check_item)

        self.status.setText("Đã nạp preset %d biến từ %s" % (len(data.get("watches", [])), target_path.name))
        return data

    def _set_interval_value(self, val: float) -> None:
        self.interval.setValue(float(val))
        self._on_custom_interval_changed(float(val))

    def _on_save_preset_clicked(self) -> None:
        try:
            self.export_preset()
        except Exception as exc:
            self.status.setText("Lỗi lưu preset: %s" % exc)

    def _on_load_preset_clicked(self) -> None:
        try:
            self.import_preset()
        except Exception as exc:
            self.status.setText("Lỗi nạp preset: %s" % exc)
