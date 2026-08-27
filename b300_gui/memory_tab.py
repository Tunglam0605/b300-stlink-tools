"""Read-only flash sector and OTA metadata inspection widget."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import OtaMetadata, ProbeRef
from b300_core.policy import SECTORS

from .workers import FunctionWorker


def format_hex_preview(data: bytes, limit: int = 4096) -> str:
    shown = data[:max(0, limit)]
    lines = []
    for offset in range(0, len(shown), 16):
        chunk = shown[offset:offset + 16]
        hexadecimal = " ".join("%02X" % value for value in chunk)
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        lines.append("%08X  %-47s  |%s|" % (offset, hexadecimal, ascii_text))
    if len(data) > len(shown):
        lines.append("… %d bytes omitted from preview; Export keeps the full sector." %
                     (len(data) - len(shown)))
    return "\n".join(lines)


class MemoryTab(QWidget):
    def __init__(self, service, probe_provider: Callable[[], ProbeRef]) -> None:
        super().__init__()
        self.service = service
        self.probe_provider = probe_provider
        self.current_data = b""
        self.current_sector = None
        self._threads = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        warning = QLabel(
            "READ-ONLY · thao tác này halt CPU trong lúc đọc và luôn resume trước khi disconnect."
        )
        warning.setStyleSheet(
            "background: #DBEAFE; color: #1E3A8A; border-radius: 6px; padding: 8px; font-weight: 600;"
        )
        warning.setWordWrap(True)
        root.addWidget(warning)

        controls = QHBoxLayout()
        self.sector_combo = QComboBox()
        self.sector_combo.setAccessibleName("Chọn Sector để đọc")
        for sector in SECTORS:
            self.sector_combo.addItem(
                "Sector %d · %s · 0x%08X..0x%08X" % (
                    sector.index, sector.role, sector.start_address, sector.end_address
                ),
                sector.index,
            )
        self.read_button = QPushButton("Đọc Sector")
        self.read_button.clicked.connect(self.read_selected_sector)
        self.export_button = QPushButton("Xuất binary…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_sector)
        self.metadata_button = QPushButton("Đọc OTA metadata")
        self.metadata_button.clicked.connect(self.read_metadata)
        controls.addWidget(self.sector_combo, 1)
        controls.addWidget(self.read_button)
        controls.addWidget(self.export_button)
        controls.addWidget(self.metadata_button)
        root.addLayout(controls)

        self.status_label = QLabel("Chưa đọc dữ liệu")
        self.status_label.setStyleSheet("color: #475569;")
        root.addWidget(self.status_label)

        splitter = QSplitter()
        preview_group = QGroupBox("Hex preview (tối đa 4096 byte)")
        preview_layout = QVBoxLayout(preview_group)
        self.hex_view = QPlainTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.hex_view.setAccessibleName("Nội dung Sector dạng hexadecimal")
        preview_layout.addWidget(self.hex_view)

        metadata_group = QGroupBox("OTA metadata · 0x0800C000")
        metadata_form = QFormLayout(metadata_group)
        self.metadata_values = {}
        for field in (
            "Classification", "Magic", "Format", "State", "Image size",
            "Image CRC32", "Board token", "Sequence", "Metadata CRC32",
            "Calculated CRC32",
        ):
            value = QLabel("—")
            value.setTextInteractionFlags(value.textInteractionFlags() |
                                          value.textInteractionFlags().TextSelectableByMouse)
            self.metadata_values[field] = value
            metadata_form.addRow(field + ":", value)

        splitter.addWidget(preview_group)
        splitter.addWidget(metadata_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

    def _set_busy(self, busy: bool) -> None:
        self.read_button.setEnabled(not busy)
        self.metadata_button.setEnabled(not busy)
        self.sector_combo.setEnabled(not busy)
        self.export_button.setEnabled(not busy and bool(self.current_data))

    def read_selected_sector(self) -> None:
        sector_index = int(self.sector_combo.currentData())
        probe = self.probe_provider()
        self.status_label.setText("Đang đọc Sector %d…" % sector_index)
        self._set_busy(True)
        self._start_worker(
            lambda emit: self.service.read_sector(probe, sector_index, event_sink=emit),
            lambda data: self.show_sector(sector_index, data),
        )

    def read_metadata(self) -> None:
        probe = self.probe_provider()
        self.status_label.setText("Đang đọc OTA metadata…")
        self._set_busy(True)
        self._start_worker(
            lambda emit: self.service.read_metadata(probe, event_sink=emit),
            self.show_metadata,
        )

    def _start_worker(self, operation, on_finished) -> None:
        thread = QThread(self)
        worker = FunctionWorker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_finished)
        worker.finished.connect(thread.quit)
        worker.failed.connect(self._failed)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)
        self._threads.append(thread)
        thread.start()

    def show_sector(self, sector_index: int, data: bytes) -> None:
        self.current_sector = sector_index
        self.current_data = bytes(data)
        self.hex_view.setPlainText(format_hex_preview(self.current_data))
        self.status_label.setText("Đã đọc Sector %d · %d byte" %
                                  (sector_index, len(self.current_data)))
        self._set_busy(False)

    def show_metadata(self, metadata: OtaMetadata) -> None:
        values = {
            "Classification": metadata.classification,
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
        for field, value in values.items():
            self.metadata_values[field].setText(value)
        color = "#166534" if metadata.valid else ("#475569" if
                                                   metadata.classification == "ERASED" else "#991B1B")
        self.metadata_values["Classification"].setStyleSheet(
            "color: %s; font-weight: 700;" % color
        )
        self.status_label.setText("OTA metadata: %s" % metadata.classification)
        self._set_busy(False)

    def _failed(self, message: str) -> None:
        self.status_label.setText("Đọc thất bại; target đã được yêu cầu resume")
        self._set_busy(False)
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
