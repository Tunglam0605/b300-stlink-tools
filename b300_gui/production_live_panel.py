"""Engineering Monitor presentation over the existing zero-halt panel API."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)
from .debug_live_panel import DebugLivePanel
from .collapsible_card import CollapsibleCard
from .trend_widget import TrendWidget

class ProductionLivePanel(DebugLivePanel):
    sample_received = Signal(object)
    history_cleared = Signal()
    RECENT_CAPACITY = 200

    def _build_ui(self):
        self.setObjectName("engineeringCard")
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
        self.expressions = QLineEdit()
        self.expressions.setPlaceholderText("Tên biến toàn cục")
        self.expressions.setAccessibleName("Biến cần theo dõi")
        self.expressions.setMaximumWidth(280)
        self.type_combo = QComboBox()
        self.type_combo.addItems(("u32", "i32", "u16", "i16", "u8", "i8", "f32", "f64"))
        self.add_watch_btn = QPushButton("Thêm")
        self.remove_watch_btn = QPushButton("Xóa biến")
        self.load_preset_btn = QPushButton("Mở danh sách…")
        self.save_preset_btn = QPushButton("Lưu danh sách…")
        for widget in (self.expressions, self.type_combo, self.add_watch_btn,
                       self.remove_watch_btn, self.load_preset_btn, self.save_preset_btn):
            watches.addWidget(widget)
        self.add_watch_btn.clicked.connect(self._on_add_watch_clicked)
        self.remove_watch_btn.clicked.connect(self._on_remove_watch_clicked)
        self.load_preset_btn.clicked.connect(self._on_load_preset_clicked)
        self.save_preset_btn.clicked.connect(self._on_save_preset_clicked)
        root.addLayout(watches)
        # Logical indices remain compatible with the proven watch/preset API.
        self.table = self._table(("Biến", "Giá trị hiện tại", "Kiểu", "Địa chỉ", "Thời gian (s)",
                                  "Đồ thị", "Nhỏ nhất", "Lớn nhất", "Trung bình", "Trạng thái"))
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
        stats = QHBoxLayout()
        for name, text in (("samples", "Mẫu: 0"), ("overruns", "Trễ nhịp: 0"),
                           ("mean_read", "Đọc TB: —"), ("max_lag", "Trễ tối đa: —"),
                           ("incoherent", "Không nhất quán: 0"), ("variables", "Biến: 0")):
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
        for row in range(self.table.rowCount()):
            cells = (self.table.item(row, column) for column in (0, 1, 2, 3, 9))
            self.table.setRowHidden(row, bool(query) and not any(
                item is not None and query in item.text().casefold() for item in cells))

    def _select_table_signal(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is not None:
            self.signal_selector.setCurrentText(item.text())

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
        for widget in (self.expressions, self.type_combo, self.add_watch_btn, self.remove_watch_btn,
                       self.load_preset_btn, self.save_preset_btn, self.interval_preset_combo,
                       self.interval, self.limit_samples, self.start_button):
            widget.setEnabled(start_enabled)
        self.cycles.setEnabled(start_enabled and self.limit_samples.isChecked())
        self.stop_button.setEnabled(stop_enabled)
        self.clear_button.setEnabled(history_enabled)
        self.export_button.setEnabled(history_enabled)

    def append_live_sample(self, sample):
        converted = super().append_live_sample(sample)
        for value in sample.values:
            self.table.setItem(self.rows[value.name], 9, QTableWidgetItem(
                "Nhất quán" if value.coherent else "Không nhất quán"))
            self.trend.append_value(value.name, sample.captured_elapsed_seconds, value.value, value.coherent)
            if self.signal_selector.findText(value.name) < 0:
                if self.signal_selector.count() >= self.trend.MAX_SIGNALS:
                    self.signal_selector.removeItem(0)
                self.signal_selector.addItem(value.name)
            self.recent_table.insertRow(0)
            for column, text in enumerate(("%.3f" % sample.captured_elapsed_seconds,
                                           value.name, str(value.value) if value.coherent else "<không nhất quán>")):
                self.recent_table.setItem(0, column, QTableWidgetItem(text))
            if self.recent_table.rowCount() > self.RECENT_CAPACITY:
                self.recent_table.removeRow(self.RECENT_CAPACITY)
        self._filter_rows()
        self.sample_received.emit(sample)
        return converted

    def _on_add_watch_clicked(self):
        super()._on_add_watch_clicked()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 9) is None:
                self.table.setItem(row, 9, QTableWidgetItem("Chờ mẫu"))
        self._filter_rows()

    def clear_history(self):
        super().clear_history()
        self.trend.clear()
        self.signal_selector.clear()
        self.recent_table.setRowCount(0)
        self.history_cleared.emit()

    def reset_for_sampling(self):
        super().reset_for_sampling()
        self.trend.clear()
        self.signal_selector.clear()
        self.recent_table.setRowCount(0)
        self.history_cleared.emit()
