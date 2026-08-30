"""Read-only flash sector and OTA metadata inspection widget in STM32 ST-Link Utility style."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from b300_core.metadata import OTA_META_MAGIC_OTA, OTA_META_MAGIC_STLINK
from b300_core.models import ApplicationHealth, OtaMetadata, ProbeRef
from b300_core.policy import SECTORS
from .workers import FunctionWorker


def format_hex_preview(data: bytes, limit: int = 4096, base_address: int = 0) -> str:
    shown = data[:max(0, limit)]
    lines = [
        "Địa chỉ   00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F  |ASCII           |",
        "--------  -----------------------------------------------  ----------------",
    ]
    for offset in range(0, len(shown), 16):
        chunk = shown[offset:offset + 16]
        hexadecimal = " ".join("%02X" % value for value in chunk)
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        address = base_address + offset
        lines.append("%08X  %-47s  |%-16s|" % (address, hexadecimal, ascii_text))
    if len(data) > len(shown):
        lines.append("… Đã ẩn %d bytes (omitted); nút 'Xuất binary…' sẽ lưu đầy đủ cả Sector." %
                     (len(data) - len(shown)))
    return "\n".join(lines)


class MemoryTab(QWidget):
    operation_state_changed = Signal(bool)

    def __init__(self, service, probe_provider: Callable[[], ProbeRef],
                 log_sink: Callable[[str], None] = lambda _line: None) -> None:
        super().__init__()
        self.service = service
        self.probe_provider = probe_provider
        self.log_sink = log_sink
        self.current_data = b""
        self.current_sector = None
        self._threads = []
        self._active_worker = None
        self._busy = False
        self._external_blocked = False
        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("memoryScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("memoryScrollContent")
        root = QVBoxLayout(self.scroll_content)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.scroll_area.setWidget(self.scroll_content)
        root_layout.addWidget(self.scroll_area)

        # ST-Link Utility style Memory Display Parameters Toolbar
        display_group = QGroupBox("Khảo sát bộ nhớ Flash (Memory Display)")
        display_layout = QVBoxLayout(display_group)
        display_layout.setContentsMargins(10, 10, 10, 10)
        display_layout.setSpacing(8)

        param_grid = QGridLayout()
        param_grid.setHorizontalSpacing(10)
        param_grid.setVerticalSpacing(4)
        param_grid.addWidget(QLabel("Address / Sector:"), 0, 0)
        self.sector_combo = QComboBox()
        self.sector_combo.setAccessibleName("Chọn Sector để đọc")
        for sector in SECTORS:
            self.sector_combo.addItem(
                "Sector %d · %s · 0x%08X..0x%08X" % (
                    sector.index, sector.role, sector.start_address, sector.end_address
                ),
                sector.index,
            )
        self.sector_combo.currentIndexChanged.connect(self._on_display_param_changed)
        param_grid.addWidget(self.sector_combo, 1, 0)

        param_grid.addWidget(QLabel("Data Width:"), 0, 1)
        self.data_width_combo = QComboBox()
        self.data_width_combo.addItem("32 bits (Word)", 32)
        self.data_width_combo.addItem("16 bits (Half-word)", 16)
        self.data_width_combo.addItem("8 bits (Byte)", 8)
        self.data_width_combo.currentIndexChanged.connect(self._render_memory_table)
        param_grid.addWidget(self.data_width_combo, 1, 1)

        param_grid.addWidget(QLabel("Size:"), 0, 2)
        self.size_combo = QComboBox()
        self.size_combo.addItem("0x100 (256 B)", 256)
        self.size_combo.addItem("0x400 (1 KiB)", 1024)
        self.size_combo.addItem("0x1000 (4 KiB)", 4096)
        self.size_combo.addItem("Toàn bộ Sector (Full)", 0)
        self.size_combo.setCurrentIndex(2)  # Default 4KB preview
        self.size_combo.currentIndexChanged.connect(self._render_memory_table)
        param_grid.addWidget(self.size_combo, 1, 2)
        param_grid.setColumnStretch(0, 3)
        param_grid.setColumnStretch(1, 1)
        param_grid.setColumnStretch(2, 1)
        display_layout.addLayout(param_grid)

        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(8)
        action_grid.setVerticalSpacing(8)
        self.read_button = QPushButton("Đọc Sector")
        self.read_button.setToolTip("CPU tạm dừng khi đọc và tự động tiếp tục chạy (resume) trước khi ngắt kết nối.")
        self.read_button.clicked.connect(self.read_selected_sector)
        self.export_button = QPushButton("Xuất binary…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_sector)
        self.metadata_button = QPushButton("Đọc Application metadata")
        self.metadata_button.clicked.connect(self.read_metadata)
        self.health_button = QPushButton("Kiểm tra Application Health")
        self.health_button.setToolTip("Read-only: đọc AppMeta + đúng image_size để đối chiếu CRC32/vector; không reset hoặc ghi Flash.")
        self.health_button.clicked.connect(self.read_application_health)
        self.cancel_button = QPushButton("Hủy đọc")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_current)

        action_grid.addWidget(self.read_button, 0, 0)
        action_grid.addWidget(self.metadata_button, 0, 1)
        action_grid.addWidget(self.health_button, 0, 2)
        action_grid.addWidget(self.export_button, 1, 0)
        action_grid.addWidget(self.cancel_button, 1, 1)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        display_layout.addLayout(action_grid)
        root.addWidget(display_group)

        # Hidden labels preserved for status & test assertions
        self.read_only_notice = QLabel(
            "CHỈ ĐỌC (READ-ONLY) · CPU tạm dừng khi đọc và luôn tiếp tục chạy "
            "(resume) trước khi ngắt kết nối."
        )
        self.read_only_notice.setVisible(False)
        root.addWidget(self.read_only_notice)

        self.range_info_label = QLabel("Target memory: Chưa đọc dữ liệu")
        self.range_info_label.setVisible(False)
        root.addWidget(self.range_info_label)

        self.status_label = QLabel("Chưa đọc dữ liệu")
        self.status_label.setVisible(False)
        root.addWidget(self.status_label)

        splitter = QSplitter()
        splitter.setMinimumHeight(300)

        # Left Panel: STM32 ST-LINK Utility Style Memory Table
        self.table_group = QGroupBox("Bảng nhớ thiết bị (Device Memory)")
        table_layout = QVBoxLayout(self.table_group)
        table_layout.setContentsMargins(6, 8, 6, 6)

        self.memory_table = QTableWidget()
        self.memory_table.setObjectName("memoryTable")
        self.memory_table.setAlternatingRowColors(True)
        self.memory_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.memory_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.memory_table.verticalHeader().setVisible(False)
        self.memory_table.verticalHeader().setDefaultSectionSize(26)
        font = QFont("Cascadia Code", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.memory_table.setFont(font)
        table_layout.addWidget(self.memory_table)

        # Hidden text fallback for test/compatibility
        self.hex_view = QPlainTextEdit()
        self.hex_view.setObjectName("hexView")
        self.hex_view.setVisible(False)
        table_layout.addWidget(self.hex_view)

        # Right Panel: OTA Metadata Form
        metadata_group = QGroupBox("Application metadata · 0x0800C000")
        metadata_layout = QVBoxLayout(metadata_group)
        metadata_layout.setContentsMargins(10, 10, 10, 10)

        self.metadata_notice = QLabel("Nhấn 'Đọc Application metadata' để kiểm tra bản ghi Sector 3.")
        self.metadata_notice.setObjectName("metadataNotice")
        self.metadata_notice.setWordWrap(True)
        metadata_layout.addWidget(self.metadata_notice)

        metadata_form = QFormLayout()
        self.metadata_values = {}
        fields = (
            ("Classification", "Phân loại (Classification)"),
            ("Source", "Nguồn metadata (Source)"),
            ("Magic", "Giá trị nhận dạng (Magic)"),
            ("Format", "Phiên bản định dạng (Format)"),
            ("State", "Trạng thái (State)"),
            ("Image size", "Kích thước image (Image size)"),
            ("Image CRC32", "CRC32 image (Image CRC32)"),
            ("Board token", "Mã board (Board token)"),
            ("Sequence", "Số thứ tự (Sequence)"),
            ("Metadata CRC32", "CRC32 metadata (Metadata CRC32)"),
            ("Calculated CRC32", "CRC32 tính lại (Calculated CRC32)"),
        )
        for field, display_label in fields:
            value = QLabel("—")
            value.setStyleSheet(
                "color: #0369A1; font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace; "
                "font-weight: 600; padding: 3px 8px; background-color: #F8FAFC; "
                "border-radius: 4px; border: 1px solid #E2E8F0;"
            )
            value.setTextInteractionFlags(value.textInteractionFlags() |
                                          value.textInteractionFlags().TextSelectableByMouse)
            self.metadata_values[field] = value
            metadata_form.addRow(display_label + ":", value)

        metadata_layout.addLayout(metadata_form)
        metadata_layout.addStretch(1)

        splitter.addWidget(self.table_group)
        splitter.addWidget(metadata_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.health_group = QGroupBox("Application Health · read-only CRC/vector evidence")
        health_layout = QVBoxLayout(self.health_group)
        health_layout.setContentsMargins(10, 10, 10, 10)
        self.health_notice = QLabel(
            "Nhấn 'Kiểm tra Application Health' để đối chiếu metadata, vector và CRC32 toàn image đã cài."
        )
        self.health_notice.setObjectName("applicationHealthNotice")
        self.health_notice.setWordWrap(True)
        health_layout.addWidget(self.health_notice)
        health_form = QFormLayout()
        self.health_values = {}
        for field, display_label in (
            ("Lifecycle", "Vòng đời (Lifecycle)"),
            ("Bootable", "Có thể boot (Bootable)"),
            ("Image CRC", "CRC image (Image CRC)"),
            ("Expected CRC32", "CRC32 metadata (Expected)"),
            ("Actual CRC32", "CRC32 đọc lại (Actual)"),
            ("Vector", "Vector Application"),
            ("Bytes checked", "Số byte đã kiểm (Bytes checked)"),
            ("Next action", "Hành động tiếp theo (Next action)"),
        ):
            value = QLabel("—")
            value.setWordWrap(field == "Next action")
            value.setTextInteractionFlags(value.textInteractionFlags() | value.textInteractionFlags().TextSelectableByMouse)
            value.setStyleSheet(
                "color: #334155; font-family: 'Cascadia Code', 'Consolas', monospace; "
                "padding: 3px 8px; background-color: #F8FAFC; border-radius: 4px; "
                "border: 1px solid #E2E8F0;"
            )
            self.health_values[field] = value
            health_form.addRow(display_label + ":", value)
        health_layout.addLayout(health_form)
        root.addWidget(self.health_group)

        self.scroll_content.setMinimumHeight(650)
        self._init_empty_table()

    def _init_empty_table(self) -> None:
        self.memory_table.setColumnCount(6)
        self.memory_table.setHorizontalHeaderLabels(["Address", "0", "4", "8", "C", "ASCII"])
        self.memory_table.setRowCount(0)
        header = self.memory_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

    def _on_display_param_changed(self) -> None:
        if self.current_data:
            self._render_memory_table()

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._refresh_controls()

    def set_external_blocked(self, blocked: bool) -> None:
        """Disable new ST-Link reads while another GUI hardware mode owns the target."""
        self._external_blocked = bool(blocked)
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        available = not self._busy and not self._external_blocked
        self.read_button.setEnabled(available)
        self.metadata_button.setEnabled(available)
        self.health_button.setEnabled(available)
        self.sector_combo.setEnabled(available)
        self.export_button.setEnabled(available and bool(self.current_data))
        self.cancel_button.setEnabled(self._busy and self._active_worker is not None)

    @property
    def has_active_operation(self) -> bool:
        return bool(self._threads)

    def invalidate_metadata_view(self, reason: str = "Target Flash đã thay đổi.") -> None:
        """Mark the displayed Application metadata snapshot stale after a write transaction."""
        for value in self.metadata_values.values():
            value.setText("—")
        for value in self.health_values.values():
            value.setText("—")
        self.health_values["Lifecycle"].setText("STALE")
        self.health_notice.setText(
            "⚠ Application Health snapshot đã hết hiệu lực. %s Nhấn 'Kiểm tra Application Health' để đọc lại CRC/vector hiện tại." % reason
        )
        self.health_notice.setStyleSheet(
            "background-color: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; "
            "border-radius: 6px; padding: 8px 12px;"
        )
        self.metadata_values["Classification"].setText("STALE")
        self.metadata_values["Classification"].setStyleSheet(
            "color: #92400E; font-weight: 700; font-family: 'Cascadia Code', 'Consolas', monospace; "
            "padding: 3px 8px; background-color: #FFFBEB; border-radius: 4px; "
            "border: 1px solid #FDE68A;"
        )
        self.metadata_notice.setText(
            "⚠ Snapshot metadata trước đó đã hết hiệu lực. %s\n"
            "Nhấn 'Đọc Application metadata' để lấy trạng thái thật hiện tại từ Sector 3." % reason
        )
        self.metadata_notice.setStyleSheet(
            "background-color: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; "
            "border-radius: 6px; padding: 8px 12px; font-size: 12px; font-weight: 500;"
        )
        self.status_label.setText("Application metadata: STALE · cần đọc lại")
        self.range_info_label.setText(
            "Target memory đã thay đổi · snapshot Sector 3 trước đó không còn hợp lệ"
        )

    def read_selected_sector(self) -> None:
        sector_index = int(self.sector_combo.currentData())
        probe = self.probe_provider()
        self.status_label.setText("Đang đọc Sector %d…" % sector_index)
        self.range_info_label.setText("Đang đọc Sector %d qua ST-Link…" % sector_index)
        self._set_busy(True)
        self._start_worker(
            lambda log, phase, cancel: self.service.read_sector(
                probe, sector_index, event_sink=log, cancel_event=cancel
            ),
            lambda data: self.show_sector(sector_index, data),
        )

    def read_metadata(self) -> None:
        probe = self.probe_provider()
        self.status_label.setText("Đang đọc OTA metadata…")
        self.range_info_label.setText("Đang đọc OTA metadata (Sector 3 · 0x0800C000)…")
        self._set_busy(True)
        self._start_worker(
            lambda log, phase, cancel: self.service.read_metadata(
                probe, event_sink=log, cancel_event=cancel
            ),
            self.show_metadata,
        )

    def read_application_health(self) -> None:
        probe = self.probe_provider()
        self.status_label.setText("Đang kiểm tra Application Health…")
        self.range_info_label.setText("Đọc AppMeta + Application image để đối chiếu CRC32/vector…")
        self._set_busy(True)
        self._start_worker(
            lambda log, phase, cancel: self.service.inspect_application_health(
                probe, event_sink=log, cancel_event=cancel
            ),
            self.show_application_health,
        )

    def _start_worker(self, operation, on_finished) -> None:
        worker = FunctionWorker(operation, self)
        worker.log.connect(self.log_sink)
        worker.completed.connect(on_finished)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        self._threads.append(worker)
        self._active_worker = worker
        self.operation_state_changed.emit(True)
        self.cancel_button.setEnabled(True)
        worker.start()

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker in self._threads:
            self._threads.remove(worker)
        if worker is self._active_worker:
            self._active_worker = None
            self.cancel_button.setEnabled(False)
        self.operation_state_changed.emit(self.has_active_operation)
        worker.deleteLater()

    def cancel_current(self) -> None:
        if self._active_worker is None:
            return
        self._active_worker.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Đang hủy thao tác đọc an toàn…")
        self.range_info_label.setText("Đang hủy thao tác đọc…")

    def show_sector(self, sector_index: int, data: bytes) -> None:
        self.current_sector = sector_index
        self.current_data = bytes(data)
        base_address = 0
        for s in SECTORS:
            if s.index == sector_index:
                base_address = s.start_address
                break
        self.hex_view.setPlainText(format_hex_preview(self.current_data, base_address=base_address))
        self.status_label.setText("Đã đọc Sector %d (0x%08X) · %d byte" %
                                  (sector_index, base_address, len(self.current_data)))
        self._render_memory_table()
        self._set_busy(False)

    def _render_memory_table(self) -> None:
        if not self.current_data:
            return

        base_address = 0
        if self.current_sector is not None:
            for s in SECTORS:
                if s.index == self.current_sector:
                    base_address = s.start_address
                    break

        data_width = self.data_width_combo.currentData() or 32
        size_limit = self.size_combo.currentData() or 0
        data_to_show = self.current_data[:size_limit] if size_limit > 0 else self.current_data

        end_address = base_address + len(data_to_show)
        range_summary = "[0x%08X .. 0x%08X] · %d bytes · %d-bit %s" % (
            base_address, end_address, len(data_to_show), data_width,
            "Word" if data_width == 32 else ("Half-word" if data_width == 16 else "Byte")
        )
        self.range_info_label.setText("Target memory, Address range: %s" % range_summary)
        self.table_group.setTitle("Bảng nhớ thiết bị · %s" % range_summary)

        font = QFont("Cascadia Code", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)

        # ST-Link Utility Color Palettes
        green_bg = QColor("#ECFDF5")
        green_fg = QColor("#047857")
        erased_bg = QColor("#FFFBEB")
        erased_fg = QColor("#B45309")
        addr_fg = QColor("#0369A1")

        if data_width == 32:
            headers = ["Address", "0", "4", "8", "C", "ASCII"]
            cols = 6
            self.memory_table.setColumnCount(cols)
            self.memory_table.setHorizontalHeaderLabels(headers)
            row_count = (len(data_to_show) + 15) // 16
            self.memory_table.setRowCount(row_count)

            for row in range(row_count):
                offset = row * 16
                chunk = data_to_show[offset:offset + 16]
                addr_item = QTableWidgetItem("0x%08X" % (base_address + offset))
                addr_item.setForeground(addr_fg)
                addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.memory_table.setItem(row, 0, addr_item)

                for w_idx in range(4):
                    w_offset = w_idx * 4
                    w_chunk = chunk[w_offset:w_offset + 4]
                    if len(w_chunk) == 4:
                        val = struct.unpack("<I", w_chunk)[0]
                        val_str = "%08X" % val
                        item = QTableWidgetItem(val_str)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        if val == 0:
                            item.setBackground(green_bg)
                            item.setForeground(green_fg)
                        elif val == 0xFFFFFFFF:
                            item.setBackground(erased_bg)
                            item.setForeground(erased_fg)
                    elif w_chunk:
                        hex_str = "".join("%02X" % b for b in reversed(w_chunk))
                        item = QTableWidgetItem(hex_str)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    else:
                        item = QTableWidgetItem("—")
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.memory_table.setItem(row, 1 + w_idx, item)

                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                ascii_item = QTableWidgetItem(ascii_str)
                self.memory_table.setItem(row, 5, ascii_item)

        elif data_width == 16:
            headers = ["Address", "0", "2", "4", "6", "8", "A", "C", "E", "ASCII"]
            cols = 10
            self.memory_table.setColumnCount(cols)
            self.memory_table.setHorizontalHeaderLabels(headers)
            row_count = (len(data_to_show) + 15) // 16
            self.memory_table.setRowCount(row_count)

            for row in range(row_count):
                offset = row * 16
                chunk = data_to_show[offset:offset + 16]
                addr_item = QTableWidgetItem("0x%08X" % (base_address + offset))
                addr_item.setForeground(addr_fg)
                addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.memory_table.setItem(row, 0, addr_item)

                for h_idx in range(8):
                    h_offset = h_idx * 2
                    h_chunk = chunk[h_offset:h_offset + 2]
                    if len(h_chunk) == 2:
                        val = struct.unpack("<H", h_chunk)[0]
                        item = QTableWidgetItem("%04X" % val)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        if val == 0:
                            item.setBackground(green_bg)
                            item.setForeground(green_fg)
                        elif val == 0xFFFF:
                            item.setBackground(erased_bg)
                            item.setForeground(erased_fg)
                    elif h_chunk:
                        item = QTableWidgetItem("%02X" % h_chunk[0])
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    else:
                        item = QTableWidgetItem("—")
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.memory_table.setItem(row, 1 + h_idx, item)

                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                ascii_item = QTableWidgetItem(ascii_str)
                self.memory_table.setItem(row, 9, ascii_item)

        else:  # 8 bits (Byte)
            headers = ["Address"] + ["%02X" % i for i in range(16)] + ["ASCII"]
            cols = 18
            self.memory_table.setColumnCount(cols)
            self.memory_table.setHorizontalHeaderLabels(headers)
            row_count = (len(data_to_show) + 15) // 16
            self.memory_table.setRowCount(row_count)

            for row in range(row_count):
                offset = row * 16
                chunk = data_to_show[offset:offset + 16]
                addr_item = QTableWidgetItem("0x%08X" % (base_address + offset))
                addr_item.setForeground(addr_fg)
                addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.memory_table.setItem(row, 0, addr_item)

                for b_idx in range(16):
                    if b_idx < len(chunk):
                        val = chunk[b_idx]
                        item = QTableWidgetItem("%02X" % val)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        if val == 0:
                            item.setBackground(green_bg)
                            item.setForeground(green_fg)
                        elif val == 0xFF:
                            item.setBackground(erased_bg)
                            item.setForeground(erased_fg)
                    else:
                        item = QTableWidgetItem("—")
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.memory_table.setItem(row, 1 + b_idx, item)

                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                ascii_item = QTableWidgetItem(ascii_str)
                self.memory_table.setItem(row, 17, ascii_item)

        header = self.memory_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, cols - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.ResizeToContents)

    def show_application_health(self, health: ApplicationHealth) -> None:
        expected_crc = (
            "0x%08X" % health.metadata.image_crc32 if health.metadata.valid else "—"
        )
        actual_crc = (
            "0x%08X" % health.actual_image_crc32
            if health.actual_image_crc32 is not None else "—"
        )
        vector = health.application_vector
        vector_text = (
            "VALID · reset=0x%08X" % vector.reset_vector
            if vector is not None and vector.valid and vector.reset_vector is not None
            else ("INVALID · %s" % vector.reason if vector is not None else "UNAVAILABLE")
        )
        values = {
            "Lifecycle": health.lifecycle,
            "Bootable": "YES" if health.bootable else "NO",
            "Image CRC": (
                "MATCH" if health.image_crc_valid is True else
                "MISMATCH" if health.image_crc_valid is False else "UNKNOWN"
            ),
            "Expected CRC32": expected_crc,
            "Actual CRC32": actual_crc,
            "Vector": vector_text,
            "Bytes checked": str(health.bytes_checked),
            "Next action": health.next_action,
        }
        for field, value in values.items():
            self.health_values[field].setText(value)
        if health.bootable:
            self.health_notice.setText("✓ BOOTABLE · %s" % health.reason)
            notice_style = (
                "background-color: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; "
                "border-radius: 6px; padding: 8px 12px; font-weight: 600;"
            )
            lifecycle_style = (
                "color: #047857; font-weight: 700; padding: 3px 8px; background-color: #ECFDF5; "
                "border-radius: 4px; border: 1px solid #A7F3D0;"
            )
        else:
            self.health_notice.setText("⚠ %s · %s" % (health.lifecycle, health.reason))
            notice_style = (
                "background-color: #FFF7ED; color: #9A3412; border: 1px solid #FED7AA; "
                "border-radius: 6px; padding: 8px 12px; font-weight: 600;"
            )
            lifecycle_style = (
                "color: #B45309; font-weight: 700; padding: 3px 8px; background-color: #FFFBEB; "
                "border-radius: 4px; border: 1px solid #FDE68A;"
            )
        self.health_notice.setStyleSheet(notice_style)
        self.health_values["Lifecycle"].setStyleSheet(lifecycle_style)
        self.status_label.setText("Application Health: %s" % health.lifecycle)
        self.range_info_label.setText(
            "Application Health · checked %d bytes · CRC=%s" %
            (health.bytes_checked, values["Image CRC"])
        )
        self._set_busy(False)

    def show_metadata(self, metadata: OtaMetadata) -> None:
        if metadata.classification == "ERASED":
            values = {
                "Classification": "ERASED",
                "Source": "—",
                "Magic": "0xFFFFFFFF (Erased)",
                "Format": "—",
                "State": "—",
                "Image size": "—",
                "Image CRC32": "—",
                "Board token": "—",
                "Sequence": "—",
                "Metadata CRC32": "—",
                "Calculated CRC32": "— (Flash trống)",
            }
            self.metadata_notice.setText(
                "⚠ Sector 3 đang ERASED. Với Bootloader v0.6.5, trạng thái này không "
                "chứng minh Application bootable; cần một transaction OTA/ST-Link tạo "
                "metadata hợp lệ trước khi boot Application."
            )
            self.metadata_notice.setStyleSheet(
                "background-color: #FFF7ED; color: #9A3412; border: 1px solid #FED7AA; "
                "border-radius: 6px; padding: 8px 12px; font-size: 12px; font-weight: 500;"
            )
        elif metadata.valid:
            source = ("ST-Link (STLM)" if metadata.magic == OTA_META_MAGIC_STLINK else
                      "OTA (OTAM)" if metadata.magic == OTA_META_MAGIC_OTA else "Unknown")
            values = {
                "Classification": metadata.classification,
                "Source": source,
                "Magic": "0x%08X" % metadata.magic,
                "Format": str(metadata.format_version),
                "State": "%s (%d)" % (metadata.state_name, metadata.state),
                "Image size": str(metadata.image_size),
                "Image CRC32": "0x%08X" % metadata.image_crc32,
                "Board token": metadata.board_token or "—",
                "Sequence": str(metadata.sequence),
                "Metadata CRC32": "0x%08X" % metadata.meta_crc32,
                "Calculated CRC32": "0x%08X" % metadata.calculated_meta_crc32,
            }
            self.metadata_notice.setText("✓ %s · %s · metadata CRC32 hợp lệ." % (source, metadata.state_name))
            self.metadata_notice.setStyleSheet(
                "background-color: #F0FDF4; color: #166534; border: 1px solid #BBF7D0; "
                "border-radius: 6px; padding: 8px 12px; font-size: 12px; font-weight: 500;"
            )
        else:
            source = ("ST-Link (STLM)" if metadata.magic == OTA_META_MAGIC_STLINK else
                      "OTA (OTAM)" if metadata.magic == OTA_META_MAGIC_OTA else "Unknown")
            values = {
                "Classification": metadata.classification,
                "Source": source,
                "Magic": "0x%08X" % metadata.magic,
                "Format": str(metadata.format_version),
                "State": "%s (%d)" % (metadata.state_name, metadata.state),
                "Image size": str(metadata.image_size),
                "Image CRC32": "0x%08X" % metadata.image_crc32,
                "Board token": metadata.board_token or "—",
                "Sequence": str(metadata.sequence),
                "Metadata CRC32": "0x%08X" % metadata.meta_crc32,
                "Calculated CRC32": "0x%08X" % metadata.calculated_meta_crc32,
            }
            self.metadata_notice.setText("⚠ Application metadata không hợp lệ hoặc sai lệch kiểm tra CRC32.")
            self.metadata_notice.setStyleSheet(
                "background-color: #FEF2F2; color: #991B1B; border: 1px solid #FECACA; "
                "border-radius: 6px; padding: 8px 12px; font-size: 12px; font-weight: 500;"
            )
        for field, value in values.items():
            self.metadata_values[field].setText(value)
        color = "#059669" if metadata.valid else ("#64748B" if
                                                   metadata.classification == "ERASED" else "#DC2626")
        bg_color = "#ECFDF5" if metadata.valid else ("#F1F5F9" if
                                                      metadata.classification == "ERASED" else "#FEF2F2")
        border_color = "#A7F3D0" if metadata.valid else ("#CBD5E1" if
                                                         metadata.classification == "ERASED" else "#FECACA")
        self.metadata_values["Classification"].setStyleSheet(
            "color: %s; font-weight: 700; font-family: 'Cascadia Code', 'Consolas', monospace; "
            "padding: 3px 8px; background-color: %s; border-radius: 4px; border: 1px solid %s;" % (
                color, bg_color, border_color
            )
        )
        self.status_label.setText("Application metadata: %s" % metadata.classification)
        self.range_info_label.setText(
            "Target memory, Sector 3 (0x0800C000) · AppMeta: %s" % metadata.classification
        )
        self._set_busy(False)

    def _failed(self, failure) -> None:
        message = getattr(failure, "message", str(failure))
        next_action = getattr(failure, "next_action", "Review the log.")
        self.log_sink(getattr(failure, "traceback", str(failure)))
        self.status_label.setText(
            "Đọc thất bại: %s · Tiếp theo: %s" % (message, next_action)
        )
        self.range_info_label.setText("Lỗi đọc bộ nhớ: %s" % message)
        self._set_busy(False)
        if "cancel" not in message.lower():
            QMessageBox.critical(self, "Không thể đọc target", message)

    def export_sector(self) -> None:
        if not self.current_data:
            return
        default = "sector-%s.bin" % self.current_sector
        path, _ = QFileDialog.getSaveFileName(self, "Xuất Sector", default, "Binary (*.bin)")
        if not path:
            return
        try:
            Path(path).write_bytes(self.current_data)
        except OSError as error:
            QMessageBox.critical(self, "Không thể xuất Sector", str(error))
