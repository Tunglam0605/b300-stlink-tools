"""Engineering Monitor presentation over the existing zero-halt panel API."""
from collections import deque
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)
from .debug_live_panel import DebugLivePanel
from .collapsible_card import CollapsibleCard
from .trend_widget import TrendWidget
from b300_core.watch_profiles import WatchPolicy, format_watch_value, watch_policy_groups

class ProductionLivePanel(DebugLivePanel):
    sample_received = Signal(object)
    history_cleared = Signal()
    watch_policies_changed = Signal(object)
    RECENT_CAPACITY = 200
    RECENT_VIEW_CAPACITY = 50

    def _build_ui(self):
        self.setObjectName("engineeringCard")
        self._watch_policies = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        toolbar = QHBoxLayout()
        self.start_button = QPushButton("Bắt đầu theo dõi")
        self.start_button.setObjectName("primaryActionButton")
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setEnabled(False)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.stop_button)
        toolbar.addWidget(QLabel("Chu kỳ"))
        self.interval_preset_combo = QComboBox()
        for value in (.1, .2, .5, 1., 2., 5.):
            self.interval_preset_combo.addItem("%g s" % value, value)
        self.interval_preset_combo.setCurrentIndex(2)
        self.interval_preset_combo.currentIndexChanged.connect(self._on_interval_preset_changed)
        toolbar.addWidget(self.interval_preset_combo)
        toolbar.addStretch()
        self.search_filter = QLineEdit()
        self.search_filter.setPlaceholderText("Lọc biến…")
        self.search_filter.setAccessibleName("Lọc biến")
        self.search_filter.setMaximumWidth(320)
        self.search_filter.textChanged.connect(self._filter_rows)
        toolbar.addWidget(self.search_filter)
        self.clear_button = QPushButton("Xóa dữ liệu")
        self.export_button = QPushButton("Xuất…")
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.export_button)
        root.addLayout(toolbar)
        self.status = QLabel("Chưa bắt đầu · chưa nhận được mẫu")
        self.status.setObjectName("monitorSessionStatus")
        root.addWidget(self.status)

        watches = QHBoxLayout()
        title = QLabel("BIẾN TRỰC TIẾP")
        title.setObjectName("sectionTitle")
        watches.addWidget(title)
        watches.addStretch()
        self.watch_source_hint = QLabel("Chọn biến từ cây AXF/ELF bên trái; kiểu được nhận dạng từ DWARF.")
        self.watch_source_hint.setWordWrap(True)
        self.remove_watch_btn = QPushButton("Xóa biến")
        for widget in (self.watch_source_hint, self.remove_watch_btn):
            watches.addWidget(widget)
        self.remove_watch_btn.clicked.connect(self._on_remove_watch_clicked)
        root.addLayout(watches)
        # Logical indices remain compatible with the proven watch/preset API.
        self.table = self._table(("Đường dẫn", "Giá trị hiện tại", "Kiểu", "Địa chỉ", "Thời gian (s)",
                                  "Đồ thị", "Nhỏ nhất", "Lớn nhất", "Trung bình", "Trạng thái",
                                  "Định dạng", "Đơn vị", "Delta", "Tốc độ"))
        self.table.horizontalHeader().moveSection(3, 1)
        self.table.horizontalHeader().moveSection(9, 4)
        for column in (4, 5, 6, 7, 8):
            self.table.setColumnHidden(column, True)
        self.table.setMinimumHeight(180)
        self.table.itemSelectionChanged.connect(self._select_table_signal)
        root.addWidget(self.table, 5)

        self.detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        trend_panel = QWidget()
        trend_layout = QVBoxLayout(trend_panel)
        trend_layout.setContentsMargins(0, 0, 8, 0)
        trend_header = QHBoxLayout()
        label = QLabel("ĐỒ THỊ")
        label.setObjectName("sectionTitle")
        trend_header.addWidget(label)
        self.signal_selector = QComboBox()
        self.signal_selector.setMinimumWidth(140)
        self.signal_selector.setAccessibleName("Biến trên đồ thị")
        trend_header.addWidget(self.signal_selector)
        trend_header.addStretch()
        trend_layout.addLayout(trend_header)
        self.trend = TrendWidget()
        self.signal_selector.currentTextChanged.connect(self.trend.select_signal)
        trend_layout.addWidget(self.trend, 1)
        self.detail_splitter.addWidget(trend_panel)
        recent_panel = QWidget()
        recent_layout = QVBoxLayout(recent_panel)
        recent_layout.setContentsMargins(8, 0, 0, 0)
        label = QLabel("MẪU GẦN ĐÂY")
        label.setObjectName("sectionTitle")
        recent_layout.addWidget(label)
        self.recent_table = self._table(("Thời gian (s)", "Biến", "Giá trị"))
        self._recent_records = deque(maxlen=self.RECENT_CAPACITY)
        self.recent_table.setMinimumHeight(120)
        recent_layout.addWidget(self.recent_table)
        self.detail_splitter.addWidget(recent_panel)
        self.detail_splitter.setStretchFactor(0, 3)
        self.detail_splitter.setStretchFactor(1, 2)
        root.addWidget(self.detail_splitter, 3)

        self.quality_details = CollapsibleCard("Nâng cao", "Giới hạn mẫu · lịch sử thực thi", expanded=False)
        options = QHBoxLayout()
        self.interval = QDoubleSpinBox()
        self.interval.setRange(.1, 60.)
        self.interval.setDecimals(2)
        self.interval.setValue(.5)
        self.interval.setSuffix(" s")
        self.interval.valueChanged.connect(self._on_custom_interval_changed)
        options.addWidget(QLabel("Chu kỳ tùy chỉnh"))
        options.addWidget(self.interval)
        self.limit_samples = QCheckBox("Giới hạn số mẫu")
        self.cycles_label = QLabel("Số mẫu")
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 100000)
        self.cycles.setValue(100)
        self.limit_samples.toggled.connect(self._on_limit_samples_toggled)
        for widget in (self.limit_samples, self.cycles_label, self.cycles):
            options.addWidget(widget)
        self.follow_latest_check = QCheckBox("Theo mẫu mới nhất")
        self.follow_latest_check.setChecked(True)
        options.addWidget(self.follow_latest_check)
        options.addStretch()
        self._on_limit_samples_toggled(False)
        self.quality_details.content_layout.addLayout(options)
        policy = QHBoxLayout()
        self.policy_group = QComboBox()
        self.policy_group.setEditable(True)
        self.policy_group.currentTextChanged.connect(self._filter_rows)
        self.policy_format = QComboBox()
        self.policy_format.addItems(("decimal", "hex", "binary", "float"))
        self.policy_unit = QLineEdit()
        self.policy_unit.setPlaceholderText("Đơn vị")
        self.policy_scale = QDoubleSpinBox(); self.policy_scale.setRange(-1e12, 1e12); self.policy_scale.setValue(1.0)
        self.policy_offset = QDoubleSpinBox(); self.policy_offset.setRange(-1e12, 1e12)
        self.policy_minimum = QLineEdit(); self.policy_minimum.setPlaceholderText("Min")
        self.policy_maximum = QLineEdit(); self.policy_maximum.setPlaceholderText("Max")
        self.policy_capture = QCheckBox("Ghi ngưỡng")
        self.save_policy_button = QPushButton("Lưu cách hiển thị")
        for label, widget in (("Nhóm", self.policy_group), ("Dạng", self.policy_format),
                              ("Đơn vị", self.policy_unit), ("Tỷ lệ", self.policy_scale),
                              ("Bù", self.policy_offset), ("", self.policy_minimum),
                              ("", self.policy_maximum), ("", self.policy_capture), ("", self.save_policy_button)):
            if label:
                policy.addWidget(QLabel(label))
            policy.addWidget(widget)
        self.save_policy_button.clicked.connect(self.save_selected_policy)
        self.quality_details.content_layout.addLayout(policy)
        stats = QHBoxLayout()
        for name, text in (("samples", "Mẫu: 0"), ("overruns", "Trễ nhịp: 0"),
                           ("mean_read", "Đọc TB: —"), ("max_lag", "Trễ tối đa: —"),
                           ("incoherent", "Không nhất quán: 0"), ("variables", "Biến: 0"),
                           ("cadence", "Chu kỳ: —"), ("render", "Vẽ: —")):
            label = QLabel(text)
            setattr(self, "stats_"+name, label)
            stats.addWidget(label)
        self.quality_details.content_layout.addLayout(stats)
        self.timeline_table = self._table(("Thời gian", "PC", "Hàm", "Tệp", "Dòng"))
        self.timeline_table.setMinimumHeight(120)
        self.quality_details.content_layout.addWidget(self.timeline_table)
        root.addWidget(self.quality_details)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(30)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _filter_rows(self):
        query = self.search_filter.text().strip().casefold()
        selected_group = self.policy_group.currentText().strip() if hasattr(self, "policy_group") else ""
        for row in range(self.table.rowCount()):
            cells = (self.table.item(row, column) for column in (0, 1, 2, 3, 9, 10, 11))
            path = self.table.item(row, 0)
            in_group = (not selected_group or self._policy_for(path.text()).group == selected_group) if path is not None else True
            matches_query = not query or any(item is not None and query in item.text().casefold() for item in cells)
            self.table.setRowHidden(row, not in_group or not matches_query)

    def _select_table_signal(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is not None:
            self.signal_selector.setCurrentText(item.text())
            self._load_policy_editor(item.text())

    def set_watch_policies(self, policies):
        selected = tuple(policies)
        if any(not isinstance(item, WatchPolicy) for item in selected):
            raise ValueError("Watch policies must be WatchPolicy values.")
        self._watch_policies = {item.path: item for item in selected}
        self.policy_group.blockSignals(True)
        self.policy_group.clear()
        self.policy_group.addItems(watch_policy_groups(selected) or ("General",))
        self.policy_group.blockSignals(False)
        for name, row in self.rows.items():
            self._render_policy_columns(row, name)
        selected_name = self.table.item(self.table.currentRow(), 0)
        if selected_name is not None:
            self._load_policy_editor(selected_name.text())

    def watch_policies(self):
        return tuple(self._watch_policies[name] for name in sorted(self._watch_policies))

    def _policy_for(self, path):
        return self._watch_policies.get(path, WatchPolicy(path))

    def _render_policy_columns(self, row, path):
        policy = self._policy_for(path)
        self.table.setItem(row, 10, QTableWidgetItem(policy.display_format))
        self.table.setItem(row, 11, QTableWidgetItem(policy.unit or "—"))

    def set_scheduler_metrics(self, summary):
        requested = getattr(summary, "requested_interval_seconds", getattr(summary, "interval_seconds", 0.0))
        effective = getattr(summary, "effective_interval_seconds", requested)
        dropped = int(getattr(summary, "dropped_frames", 0))
        self.stats_cadence.setText("Chu kỳ: %.3g/%.3g s · bỏ %d" % (requested, effective, dropped))

    def set_render_duration(self, seconds):
        self.stats_render.setText("Vẽ: %.2f ms" % (max(0.0, float(seconds)) * 1000.0))

    @staticmethod
    def _optional_number(text):
        selected = str(text).strip()
        return None if not selected else float(selected)

    def _load_policy_editor(self, path):
        policy = self._policy_for(path)
        for widget in (self.policy_group, self.policy_format, self.policy_unit, self.policy_scale,
                       self.policy_offset, self.policy_minimum, self.policy_maximum, self.policy_capture):
            widget.blockSignals(True)
        try:
            self.policy_group.setCurrentText(policy.group)
            self.policy_format.setCurrentText(policy.display_format)
            self.policy_unit.setText(policy.unit or "")
            self.policy_scale.setValue(policy.scale); self.policy_offset.setValue(policy.offset)
            self.policy_minimum.setText("" if policy.minimum is None else str(policy.minimum))
            self.policy_maximum.setText("" if policy.maximum is None else str(policy.maximum))
            self.policy_capture.setChecked(policy.capture)
        finally:
            for widget in (self.policy_group, self.policy_format, self.policy_unit, self.policy_scale,
                           self.policy_offset, self.policy_minimum, self.policy_maximum, self.policy_capture):
                widget.blockSignals(False)

    def save_selected_policy(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return
        policy = WatchPolicy(
            item.text(), group=self.policy_group.currentText(), display_format=self.policy_format.currentText(),
            unit=self.policy_unit.text(), scale=self.policy_scale.value(), offset=self.policy_offset.value(),
            minimum=self._optional_number(self.policy_minimum.text()), maximum=self._optional_number(self.policy_maximum.text()),
            capture=self.policy_capture.isChecked(),
        )
        self._watch_policies[policy.path] = policy
        if self.policy_group.findText(policy.group) < 0:
            self.policy_group.addItem(policy.group)
        self._render_policy_columns(self.rows[policy.path], policy.path)
        self.watch_policies_changed.emit(self.watch_policies())

    def _on_interval_preset_changed(self, index):
        value = self.interval_preset_combo.itemData(index)
        if value is not None and hasattr(self, "interval"):
            self.interval.blockSignals(True)
            self.interval.setValue(value)
            self.interval.blockSignals(False)

    def _on_custom_interval_changed(self, value):
        index = self.interval_preset_combo.findData(float(value))
        self.interval_preset_combo.blockSignals(True)
        self.interval_preset_combo.setCurrentIndex(index)
        self.interval_preset_combo.blockSignals(False)

    def set_control_state(self, *, start_enabled, stop_enabled, history_enabled):
        for widget in (self.remove_watch_btn, self.interval_preset_combo,
                       self.interval, self.limit_samples, self.start_button):
            widget.setEnabled(start_enabled)
        self.cycles.setEnabled(start_enabled and self.limit_samples.isChecked())
        self.stop_button.setEnabled(stop_enabled)
        self.clear_button.setEnabled(history_enabled)
        self.export_button.setEnabled(history_enabled)

    def watch_specs(self):
        """Production watches come from the typed AXF/ELF catalog only."""
        return ()

    def append_live_sample(self, sample):
        return self.append_live_samples((sample,))

    def append_live_samples(self, samples):
        """Coalesce worker batches into one bounded Qt render operation."""
        selected = tuple(samples)
        if not selected:
            return ()
        order = []
        names = set()
        for sample in selected:
            for value in sample.values:
                if value.name not in names:
                    order.append(value.name)
                    names.add(value.name)

        recorded = []
        self.table.setUpdatesEnabled(False)
        try:
            for sample in selected:
                recorded.extend(super().append_live_sample(sample))
                for value in sample.values:
                    if value.coherent:
                        self.table.setItem(self.rows[value.name], 1, QTableWidgetItem(self._formatted_value(value)))
                    self.table.setItem(self.rows[value.name], 9, QTableWidgetItem(
                        "Nhất quán" if value.coherent else "Không nhất quán"))
        finally:
            self.table.setUpdatesEnabled(True)

        if self.signal_selector.count() == 0:
            for name in order[-self.trend.MAX_SIGNALS:]:
                self.signal_selector.addItem(name)
        plotted_names = {
            self.signal_selector.itemText(index)
            for index in range(self.signal_selector.count())
        }
        trend_values = []
        for sample in selected:
            for value in sample.values:
                if value.name in plotted_names:
                    trend_values.append((
                        value.name, sample.captured_elapsed_seconds,
                        value.value, value.coherent,
                    ))
                self._recent_records.appendleft((
                    "%.3f" % sample.captured_elapsed_seconds,
                    value.name,
                    self._formatted_value(value) if value.coherent else "<không nhất quán>",
                ))
        self.trend.append_values(trend_values)

        visible = tuple(self._recent_records)[:self.RECENT_VIEW_CAPACITY]
        self.recent_table.setUpdatesEnabled(False)
        try:
            self.recent_table.setRowCount(len(visible))
            for row, record in enumerate(visible):
                for column, text in enumerate(record):
                    self.recent_table.setItem(row, column, QTableWidgetItem(text))
        finally:
            self.recent_table.setUpdatesEnabled(True)
        if self.search_filter.text().strip():
            self._filter_rows()
        for sample in selected:
            self.sample_received.emit(sample)
        return tuple(recorded)

    def _formatted_value(self, value):
        try:
            return format_watch_value(self._policy_for(value.name), value.value)
        except ValueError:
            return str(value.value)

    def mark_stale(self, reason):
        """Keep last values/timestamps visible but revoke their live quality."""
        detail = str(reason or "Gateway không còn cung cấp bằng chứng mới.").strip()
        self.status.setText("STALE · %s" % detail)
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 9, QTableWidgetItem("STALE · %s" % detail))

    def apply_rebound_watches(self, watches, stale_names=()):
        """Replace typed watch bindings after a verified AXF/ELF reload."""
        rebound = tuple(watches)
        stale = tuple(str(name) for name in stale_names)
        self._compiled_watch_by_name.clear()
        for watch in rebound:
            self.add_compiled_watch(watch)
            row = self.rows.get(watch.name)
            if row is not None:
                self.table.setItem(row, 9, QTableWidgetItem("Đã cập nhật từ AXF/ELF"))
        for name in stale:
            row = self.rows.get(name)
            if row is not None:
                self.table.setItem(row, 9, QTableWidgetItem(
                    "STALE · không còn trong AXF/ELF mới"
                ))
        return rebound, stale

    def invalidate_compiled_watches(self, reason):
        """Revoke every address derived from an obsolete AXF/ELF catalog."""
        previous = tuple(self._compiled_watch_by_name.values())
        self._compiled_watch_by_name.clear()
        detail = str(reason or "AXF/ELF đã thay đổi.").strip()
        for watch in previous:
            row = self.rows.get(watch.name)
            if row is not None:
                self.table.setItem(row, 9, QTableWidgetItem("STALE · %s" % detail))
        return previous

    def _on_add_watch_clicked(self):
        super()._on_add_watch_clicked()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 9) is None:
                self.table.setItem(row, 9, QTableWidgetItem("Chờ mẫu"))
        self._filter_rows()

    def add_compiled_watch(self, watch):
        super().add_compiled_watch(watch)
        self._render_policy_columns(self.rows[watch.name], watch.name)

    def clear_history(self):
        super().clear_history()
        self._recent_records.clear()
        self.trend.clear()
        self.signal_selector.clear()
        self.recent_table.setRowCount(0)
        self.history_cleared.emit()

    def reset_for_sampling(self):
        super().reset_for_sampling()
        self._recent_records.clear()
        self.trend.clear()
        self.signal_selector.clear()
        self.recent_table.setRowCount(0)
        self.history_cleared.emit()
