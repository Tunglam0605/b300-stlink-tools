"""Clean Industrial Flash Sector and STLM Metadata Inspector."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
from .collapsible_card import CollapsibleCard


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
    """Ultra-streamlined, high-density STLM Metadata and Flash Sector Inspector."""

    operation_state_changed = Signal(bool)

    def __init__(self, service, probe_provider: Callable[[], ProbeRef],
                 log_sink: Callable[[str], None] = lambda _line: None) -> None:
        super().__init__()
        self.service = service
        self.probe_provider = probe_provider
        self._external_log_sink = log_sink
        self.log_sink = self._append_tab_log
        self.current_data = b""
        self.current_sector = None
        self._threads = []
        self._active_worker = None
        self._busy = False
        self._external_blocked = False
        self._build_ui()

    def _append_tab_log(self, text: str) -> None:
        if hasattr(self, "tab_log"):
            self.tab_log.appendPlainText(text)
        if hasattr(self, "_external_log_sink") and self._external_log_sink:
            self._external_log_sink(text)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        # 1. Top Compact Action Bar (1-Line Control Bar)
        toolbar = QFrame()
        toolbar.setObjectName("cardSurface")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 6, 10, 6)
        tb_layout.setSpacing(8)

        self.health_button = QPushButton("Kiểm tra Application")
        self.health_button.setObjectName("primaryButton")
        self.health_button.setToolTip("Đối chiếu STLM metadata, vector entry và CRC32 Application.")
        self.health_button.clicked.connect(self.read_application_health)
        tb_layout.addWidget(self.health_button)

        self.metadata_button = QPushButton("Đọc Metadata")
        self.metadata_button.setObjectName("ghostButton")
        self.metadata_button.setToolTip("Đọc bản ghi 44-byte Sector 3 (0x0800C000)")
        self.metadata_button.clicked.connect(self.read_metadata)
        tb_layout.addWidget(self.metadata_button)

        # Subtle separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("borderMuted")
        tb_layout.addWidget(sep)

        lbl_sec = QLabel("Sector:")
        lbl_sec.setObjectName("fieldLabel")
        tb_layout.addWidget(lbl_sec)

        self.sector_combo = QComboBox()
        self.sector_combo.setAccessibleName("Chọn Sector để đọc")
        self.sector_combo.setMinimumWidth(240)
        for sector in SECTORS:
            self.sector_combo.addItem(
                "Sector %d · %s · 0x%08X..0x%08X" % (
                    sector.index, sector.role, sector.start_address, sector.end_address
                ),
                sector.index,
            )
        self.sector_combo.currentIndexChanged.connect(self._on_display_param_changed)
        tb_layout.addWidget(self.sector_combo, 1)

        self.read_button = QPushButton("Đọc Sector")
        self.read_button.setObjectName("ghostButton")
        self.read_button.setToolTip("Đọc dữ liệu nhị phân từ Sector Flash đã chọn.")
        self.read_button.clicked.connect(self.read_selected_sector)
        tb_layout.addWidget(self.read_button)

        self.export_button = QPushButton("Xuất binary…")
        self.export_button.setObjectName("ghostButton")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_sector)
        tb_layout.addWidget(self.export_button)

        self.cancel_button = QPushButton("Hủy")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_current)
        tb_layout.addWidget(self.cancel_button)

        root_layout.addWidget(toolbar)

        # 2. Main Inspection Splitter (Hex Table Left 55%, Metadata Right 45%)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- LEFT PANEL: Hex Memory Dump ---
        left_card = QFrame()
        left_card.setObjectName("cardSurface")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(10, 8, 10, 8)
        left_layout.setSpacing(6)

        left_header = QHBoxLayout()
        left_header.setSpacing(8)
        title_hex = QLabel("BẢNG NHỚ FLASH (HEX DUMP)")
        title_hex.setObjectName("CardTitle")
        left_header.addWidget(title_hex)
        left_header.addStretch(1)

        left_header.addWidget(QLabel("Width:"))
        self.data_width_combo = QComboBox()
        self.data_width_combo.addItem("32 bits (Word)", 32)
        self.data_width_combo.addItem("16 bits (Half-word)", 16)
        self.data_width_combo.addItem("8 bits (Byte)", 8)
        self.data_width_combo.currentIndexChanged.connect(self._render_memory_table)
        left_header.addWidget(self.data_width_combo)

        left_header.addWidget(QLabel("Size:"))
        self.size_combo = QComboBox()
        self.size_combo.addItem("0x100 (256 B)", 256)
        self.size_combo.addItem("0x400 (1 KiB)", 1024)
        self.size_combo.addItem("0x1000 (4 KiB)", 4096)
        self.size_combo.addItem("Toàn bộ Sector (Full)", 0)
        self.size_combo.setCurrentIndex(2)
        self.size_combo.currentIndexChanged.connect(self._render_memory_table)
        left_header.addWidget(self.size_combo)

        left_layout.addLayout(left_header)

        self.memory_table = QTableWidget()
        self.memory_table.setObjectName("memoryTable")
        self.memory_table.setAlternatingRowColors(True)
        self.memory_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.memory_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.memory_table.verticalHeader().setVisible(False)
        self.memory_table.verticalHeader().setDefaultSectionSize(22)
        font = QFont("Cascadia Code", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.memory_table.setFont(font)
        left_layout.addWidget(self.memory_table, 1)

        self.hex_view = QPlainTextEdit()
        self.hex_view.setObjectName("hexView")
        self.hex_view.setVisible(False)
        left_layout.addWidget(self.hex_view)

        splitter.addWidget(left_card)

        # --- RIGHT PANEL: STLM Metadata & Health Overview ---
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # 1. Health Status Card
        health_card = QFrame()
        health_card.setObjectName("cardSurface")
        health_layout = QVBoxLayout(health_card)
        health_layout.setContentsMargins(10, 8, 10, 8)
        health_layout.setSpacing(6)

        title_health = QLabel("TRẠNG THÁI APPLICATION & BOOTLOADER")
        title_health.setObjectName("CardTitle")
        health_layout.addWidget(title_health)

        self.health_notice = QLabel("Nhấn 'Kiểm tra Application' để đối chiếu metadata, vector và CRC32.")
        self.health_notice.setObjectName("pageContextSubtitle")
        self.health_notice.setWordWrap(True)
        health_layout.addWidget(self.health_notice)

        health_grid = QGridLayout()
        health_grid.setHorizontalSpacing(8)
        health_grid.setVerticalSpacing(3)
        health_grid.setContentsMargins(0, 0, 0, 0)
        self.health_values = {}

        health_fields = [
            ("Lifecycle", "Vòng đời (Lifecycle)", 0, 0),
            ("Bootable", "Có thể boot (Bootable)", 0, 2),
            ("Vector", "Vector Application", 1, 0),
            ("Image CRC", "CRC image (Image CRC)", 1, 2),
            ("Expected CRC32", "CRC32 metadata (Expected)", 2, 0),
            ("Actual CRC32", "CRC32 đọc lại (Actual)", 2, 2),
            ("Bytes checked", "Số byte đã kiểm (Bytes checked)", 3, 0),
            ("Next action", "Hành động tiếp theo (Next action)", 3, 2),
        ]

        for field, display_label, r, c in health_fields:
            lbl = QLabel(display_label + ":")
            lbl.setObjectName("fieldLabel")
            health_grid.addWidget(lbl, r, c)

            val = QLabel("—")
            val.setObjectName("monoText")
            val.setWordWrap(field == "Next action")
            val.setTextInteractionFlags(val.textInteractionFlags() | val.textInteractionFlags().TextSelectableByMouse)
            self.health_values[field] = val
            health_grid.addWidget(val, r, c + 1)

        health_layout.addLayout(health_grid)
        right_layout.addWidget(health_card)

        # 2. Metadata Contract Card
        meta_card = QFrame()
        meta_card.setObjectName("cardSurface")
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(10, 8, 10, 8)
        meta_layout.setSpacing(6)

        title_meta = QLabel("BẢN GHI STLM METADATA · SECTOR 3 (0x0800C000)")
        title_meta.setObjectName("CardTitle")
        meta_layout.addWidget(title_meta)

        self.metadata_notice = QLabel("Nhấn 'Đọc Metadata' để kiểm tra bản ghi 44-byte Sector 3.")
        self.metadata_notice.setObjectName("pageContextSubtitle")
        self.metadata_notice.setWordWrap(True)
        meta_layout.addWidget(self.metadata_notice)

        meta_form = QFormLayout()
        meta_form.setVerticalSpacing(2)
        meta_form.setHorizontalSpacing(8)
        meta_form.setContentsMargins(0, 2, 0, 2)
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
            value.setObjectName("monoText")
            value.setTextInteractionFlags(value.textInteractionFlags() |
                                          value.textInteractionFlags().TextSelectableByMouse)
            self.metadata_values[field] = value
            lbl = QLabel(display_label + ":")
            lbl.setObjectName("fieldLabel")
            meta_form.addRow(lbl, value)

        meta_layout.addLayout(meta_form)
        right_layout.addWidget(meta_card)

        # 3. Flash Memory Map Card (Reference Spans)
        map_card = QFrame()
        map_card.setObjectName("cardSurface")
        map_layout = QVBoxLayout(map_card)
        map_layout.setContentsMargins(10, 8, 10, 8)
        map_layout.setSpacing(6)

        title_map = QLabel("🗺  PHÂN BỔ BỘ NHỚ FLASH STM32F407")
        title_map.setObjectName("CardTitle")
        map_layout.addWidget(title_map)

        map_grid = QGridLayout()
        map_grid.setHorizontalSpacing(8)
        map_grid.setVerticalSpacing(4)
        map_grid.setContentsMargins(0, 2, 0, 2)

        flash_spans = [
            ("Sector 0..2", "0x08000000 - 0x0800BFFF", "48 KB", "Bootloader (WRP Bảo vệ)", "#F59E0B"),
            ("Sector 3", "0x0800C000 - 0x0800FFFF", "16 KB", "STLM OTA Metadata (44B)", "#06B6D4"),
            ("Sector 4..7", "0x08010000 - 0x0807FFFF", "448 KB", "Application (Vùng nạp chính)", "#10B981"),
        ]
        for row, (sec, addr, size, role, color) in enumerate(flash_spans):
            sec_lbl = QLabel(sec)
            sec_lbl.setStyleSheet("font-weight: 700; color: %s; font-size: 11px;" % color)
            addr_lbl = QLabel(addr)
            addr_lbl.setObjectName("monoText")
            addr_lbl.setStyleSheet("font-size: 10.5px;")
            size_lbl = QLabel(size)
            size_lbl.setStyleSheet("font-size: 10.5px; color: #94A3B8;")
            role_lbl = QLabel(role)
            role_lbl.setStyleSheet("font-size: 10.5px; font-weight: 600;")
            map_grid.addWidget(sec_lbl, row, 0)
            map_grid.addWidget(addr_lbl, row, 1)
            map_grid.addWidget(size_lbl, row, 2)
            map_grid.addWidget(role_lbl, row, 3)

        map_layout.addLayout(map_grid)
        right_layout.addWidget(map_card)

        # 4. Activity & Inspection Log Card (Fills remaining height)
        log_card = QFrame()
        log_card.setObjectName("cardSurface")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(10, 8, 10, 8)
        log_layout.setSpacing(6)

        log_header = QHBoxLayout()
        log_header.setSpacing(6)
        title_log = QLabel("📜  NHẬT KÝ KIỂM TRA MEMORY & METADATA")
        title_log.setObjectName("CardTitle")
        log_header.addWidget(title_log)
        log_header.addStretch(1)

        clear_log_btn = QPushButton("Xóa log")
        clear_log_btn.setObjectName("ghostButton")
        clear_log_btn.setFixedHeight(20)
        clear_log_btn.setStyleSheet("font-size: 10px; padding: 1px 6px;")
        clear_log_btn.clicked.connect(lambda: self.tab_log.clear())
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self.tab_log = QPlainTextEdit()
        self.tab_log.setObjectName("terminalLog")
        self.tab_log.setReadOnly(True)
        self.tab_log.setPlaceholderText("Nhật ký chi tiết các bước đọc Flash, kiểm tra vector và đối chiếu STLM...")
        font_log = QFont("Cascadia Code", 9)
        font_log.setStyleHint(QFont.StyleHint.Monospace)
        self.tab_log.setFont(font_log)
        self.tab_log.setMinimumHeight(120)
        log_layout.addWidget(self.tab_log, 1)

        right_layout.addWidget(log_card, 1)

        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 55)
        splitter.setStretchFactor(1, 45)
        root_layout.addWidget(splitter, 1)

        # Status Bar / Notice Footer
        footer = QFrame()
        footer.setObjectName("cardSurface")
        ft_layout = QHBoxLayout(footer)
        ft_layout.setContentsMargins(10, 4, 10, 4)
        ft_layout.setSpacing(10)

        self.status_label = QLabel("Chưa đọc dữ liệu · nhấn 'Kiểm tra Application' hoặc chọn Sector để đọc.")
        self.status_label.setObjectName("pageContextSubtitle")
        ft_layout.addWidget(self.status_label, 1)

        self.read_only_notice = QLabel(
            "CHỈ ĐỌC (READ-ONLY) · CPU tạm dừng khi đọc Memory thủ công và tool luôn resume trước khi ngắt kết nối. Realtime không halt: dùng Live Monitor."
        )
        self.read_only_notice.setObjectName("eyebrowLabel")
        ft_layout.addWidget(self.read_only_notice)

        self.range_info_label = QLabel("Target memory: Chưa đọc dữ liệu")
        self.range_info_label.setVisible(False)
        ft_layout.addWidget(self.range_info_label)

        root_layout.addWidget(footer)

        # Backward compatibility proxy cards for test assertions
        self.manual_memory_card = CollapsibleCard("Đọc Memory thủ công", "Tùy chọn nâng cao", expanded=False)
        self.manual_memory_card.setVisible(False)
        self.memory_results_card = CollapsibleCard("Kết quả chi tiết", "Chi tiết", expanded=False)
        self.memory_results_card.setVisible(False)

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
        self._external_blocked = bool(blocked)
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        available = not self._busy and not self._external_blocked
        self.read_button.setEnabled(available)
        self.metadata_button.setEnabled(available)
        self.health_button.setEnabled(available)
        self.sector_combo.setEnabled(available)
        self.export_button.setEnabled(available and bool(self.current_data))
        cancellable = self._busy and self._active_worker is not None
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(cancellable)

    @property
    def has_active_operation(self) -> bool:
        return bool(self._threads)

    def invalidate_metadata_view(self, reason: str = "Target Flash đã thay đổi.") -> None:
        for value in self.metadata_values.values():
            value.setText("—")
        for value in self.health_values.values():
            value.setText("—")
        self.health_values["Lifecycle"].setText("STALE")
        self.health_notice.setText(
            "Application Health snapshot đã hết hiệu lực. %s Nhấn 'Kiểm tra Application Health' để đọc lại CRC/vector." % reason
        )
        self.health_notice.setObjectName("statusPillWarning")
        self.metadata_values["Classification"].setText("STALE")
        self.metadata_values["Classification"].setObjectName("statusPillWarning")
        self.metadata_notice.setText(
            "Snapshot metadata trước đó đã hết hiệu lực. %s\n"
            "Nhấn 'Đọc Application metadata' để lấy trạng thái thật hiện tại từ Sector 3." % reason
        )
        self.metadata_notice.setObjectName("statusPillWarning")
        self.status_label.setText("Application metadata: STALE · cần đọc lại")
        self.range_info_label.setText(
            "Target memory đã thay đổi · snapshot Sector 3 trước đó không còn hợp lệ"
        )
        self.health_notice.style().unpolish(self.health_notice)
        self.health_notice.style().polish(self.health_notice)
        self.metadata_notice.style().unpolish(self.metadata_notice)
        self.metadata_notice.style().polish(self.metadata_notice)
        self.metadata_values["Classification"].style().unpolish(self.metadata_values["Classification"])
        self.metadata_values["Classification"].style().polish(self.metadata_values["Classification"])

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
        self._active_worker = worker
        self._threads.append(worker)
        self._refresh_controls()
        self.operation_state_changed.emit(True)
        worker.start()

    def cancel_current(self) -> None:
        if self._active_worker:
            self._active_worker.cancel()
            self.cancel_button.setEnabled(False)

    def _worker_finished(self) -> None:
        if self._active_worker in self._threads:
            self._threads.remove(self._active_worker)
        self._active_worker = None
        self._set_busy(False)
        self.operation_state_changed.emit(bool(self._threads))

    def show_sector(self, sector_index: int, data: bytes) -> None:
        self.current_data = data
        self.current_sector = sector_index
        self.memory_results_card.set_expanded(True)
        self._render_memory_table()
        self.status_label.setText("Đã nạp Sector %d (%d bytes)" % (sector_index, len(data)))
        self.range_info_label.setText(
            "Sector %d: %s · 0x%08X..0x%08X (%d bytes)" % (
                sector_index, SECTORS[sector_index].role,
                SECTORS[sector_index].start_address, SECTORS[sector_index].end_address,
                len(data)
            )
        )
        self.hex_view.setPlainText(
            format_hex_preview(data, limit=4096, base_address=SECTORS[sector_index].start_address)
        )
        self._set_busy(False)

    def _render_memory_table(self) -> None:
        if not self.current_data or self.current_sector is None:
            self._init_empty_table()
            return
        width = int(self.data_width_combo.currentData() or 32)
        requested_size = int(self.size_combo.currentData() or 0)
        data = self.current_data[:requested_size] if requested_size > 0 else self.current_data
        base_address = SECTORS[self.current_sector].start_address

        from .theme import ThemeManager
        is_dark = ThemeManager.instance().is_dark
        addr_color = QColor("#38BDF8") if is_dark else QColor("#0284C7")
        ascii_color = QColor("#94A3B8") if is_dark else QColor("#64748B")

        if width == 32:
            cols = ["Address", "0", "4", "8", "C", "ASCII"]
            self.memory_table.setColumnCount(len(cols))
            self.memory_table.setHorizontalHeaderLabels(cols)
            row_count = (len(data) + 15) // 16
            self.memory_table.setRowCount(row_count)
            for row in range(row_count):
                addr = base_address + row * 16
                chunk = data[row * 16 : row * 16 + 16]
                addr_item = QTableWidgetItem("%08X" % addr)
                addr_item.setForeground(addr_color)
                self.memory_table.setItem(row, 0, addr_item)
                for col in range(4):
                    sub = chunk[col * 4 : col * 4 + 4]
                    if len(sub) == 4:
                        val = struct.unpack("<I", sub)[0]
                        text = "%08X" % val
                    elif sub:
                        text = sub.hex().upper()
                    else:
                        text = "—"
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.memory_table.setItem(row, col + 1, item)
                ascii_text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                ascii_item = QTableWidgetItem(ascii_text)
                ascii_item.setForeground(ascii_color)
                self.memory_table.setItem(row, 5, ascii_item)
        elif width == 16:
            cols = ["Address"] + ["%X" % (i * 2) for i in range(8)] + ["ASCII"]
            self.memory_table.setColumnCount(len(cols))
            self.memory_table.setHorizontalHeaderLabels(cols)
            row_count = (len(data) + 15) // 16
            self.memory_table.setRowCount(row_count)
            for row in range(row_count):
                addr = base_address + row * 16
                chunk = data[row * 16 : row * 16 + 16]
                addr_item = QTableWidgetItem("%08X" % addr)
                addr_item.setForeground(addr_color)
                self.memory_table.setItem(row, 0, addr_item)
                for col in range(8):
                    sub = chunk[col * 2 : col * 2 + 2]
                    if len(sub) == 2:
                        val = struct.unpack("<H", sub)[0]
                        text = "%04X" % val
                    elif sub:
                        text = sub.hex().upper()
                    else:
                        text = "—"
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.memory_table.setItem(row, col + 1, item)
                ascii_text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                ascii_item = QTableWidgetItem(ascii_text)
                ascii_item.setForeground(ascii_color)
                self.memory_table.setItem(row, 9, ascii_item)
        else:  # 8 bits
            cols = ["Address"] + ["%X" % i for i in range(16)] + ["ASCII"]
            self.memory_table.setColumnCount(len(cols))
            self.memory_table.setHorizontalHeaderLabels(cols)
            row_count = (len(data) + 15) // 16
            self.memory_table.setRowCount(row_count)
            for row in range(row_count):
                addr = base_address + row * 16
                chunk = data[row * 16 : row * 16 + 16]
                addr_item = QTableWidgetItem("%08X" % addr)
                addr_item.setForeground(addr_color)
                self.memory_table.setItem(row, 0, addr_item)
                for col in range(16):
                    if col < len(chunk):
                        text = "%02X" % chunk[col]
                    else:
                        text = "—"
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.memory_table.setItem(row, col + 1, item)
                ascii_text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                ascii_item = QTableWidgetItem(ascii_text)
                ascii_item.setForeground(ascii_color)
                self.memory_table.setItem(row, 17, ascii_item)

    def show_application_health(self, health: ApplicationHealth) -> None:
        self.memory_results_card.set_expanded(True)
        vector_text = "—"
        if getattr(health, "application_vector", None):
            vector = health.application_vector
            if getattr(vector, "valid", False) and getattr(vector, "reset_vector", None) is not None:
                vector_text = "VALID · reset=0x%08X" % vector.reset_vector
            else:
                vector_text = getattr(vector, "reason", "INVALID")
        values = {
            "Lifecycle": health.lifecycle,
            "Bootable": "YES" if health.bootable else "NO",
            "Vector": vector_text,
            "Image CRC": "MATCH" if health.image_crc_valid else "MISMATCH",
            "Expected CRC32": (
                "0x%08X" % health.metadata.image_crc32
                if health.metadata and health.metadata.image_crc32 is not None
                else "—"
            ),
            "Actual CRC32": (
                "0x%08X" % health.actual_image_crc32
                if health.actual_image_crc32 is not None
                else "—"
            ),
            "Bytes checked": str(health.bytes_checked),
            "Next action": health.next_action or "No action is required.",
        }
        for field, value in values.items():
            self.health_values[field].setText(value)

        # Style with semantic chips
        self.health_values["Vector"].setObjectName("monoAddress")
        self.health_values["Expected CRC32"].setObjectName("monoCrc")
        self.health_values["Actual CRC32"].setObjectName("monoCrc")
        self.health_values["Bytes checked"].setObjectName("monoSize")
        self.health_values["Bootable"].setObjectName("statusPillSuccess" if health.bootable else "statusPillDanger")
        self.health_values["Image CRC"].setObjectName("statusPillSuccess" if health.image_crc_valid else "statusPillDanger")

        if health.bootable:
            self.health_notice.setText("BOOTABLE · %s" % health.reason)
            self.health_notice.setObjectName("statusPillSuccess")
            self.health_values["Lifecycle"].setObjectName("statusPillSuccess")
        else:
            self.health_notice.setText("%s · %s" % (health.lifecycle, health.reason))
            self.health_notice.setObjectName("statusPillDanger")
            self.health_values["Lifecycle"].setObjectName("statusPillDanger")

        for key in ("Vector", "Expected CRC32", "Actual CRC32", "Bytes checked", "Bootable", "Image CRC", "Lifecycle"):
            w = self.health_values[key]
            w.style().unpolish(w)
            w.style().polish(w)

        self.health_notice.style().unpolish(self.health_notice)
        self.health_notice.style().polish(self.health_notice)

        self.status_label.setText("Application Health: %s" % health.lifecycle)
        self.range_info_label.setText(
            "Application Health · checked %d bytes · CRC=%s" %
            (health.bytes_checked, values["Image CRC"])
        )
        self._set_busy(False)

    def show_metadata(self, metadata: OtaMetadata) -> None:
        self.memory_results_card.set_expanded(True)
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
                "Sector 3 đang ERASED. Cần transaction OTA hoặc nạp ST-Link để tạo metadata STLM hợp lệ."
            )
            self.metadata_notice.setObjectName("statusPillWarning")
            self.metadata_values["Classification"].setObjectName("statusPillNeutral")
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
            self.metadata_notice.setText("%s · %s · Metadata CRC32 hợp lệ." % (source, metadata.state_name))
            self.metadata_notice.setObjectName("statusPillSuccess")
            self.metadata_values["Classification"].setObjectName("statusPillSuccess")
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
            self.metadata_notice.setText("Application metadata không hợp lệ hoặc sai lệch kiểm tra CRC32.")
            self.metadata_notice.setObjectName("statusPillDanger")
            self.metadata_values["Classification"].setObjectName("statusPillDanger")

        for field, value in values.items():
            self.metadata_values[field].setText(value)

        # Style with semantic chips
        self.metadata_values["Magic"].setObjectName("monoAddress")
        self.metadata_values["Image size"].setObjectName("monoSize")
        self.metadata_values["Image CRC32"].setObjectName("monoCrc")
        self.metadata_values["Metadata CRC32"].setObjectName("monoCrc")
        self.metadata_values["Calculated CRC32"].setObjectName("monoCrc")
        self.metadata_values["Board token"].setObjectName("monoAddress")

        for key in ("Magic", "Image size", "Image CRC32", "Metadata CRC32", "Calculated CRC32", "Board token", "Classification"):
            w = self.metadata_values[key]
            w.style().unpolish(w)
            w.style().polish(w)

        self.metadata_notice.style().unpolish(self.metadata_notice)
        self.metadata_notice.style().polish(self.metadata_notice)

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
