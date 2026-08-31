"""Detailed R&D Engineering Flash Workbench."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import ImageInfo, ProbeInfo
from b300_core.hex_image import inspect_image
from b300_gui.widgets.memory_map_widget import MemoryMapWidget
from b300_gui.widgets.pipeline_stepper import PipelineStepper


class RndFlashView(QWidget):
    """Deep engineering flash workbench with visual memory map, telemetry, and log terminal."""

    flash_requested = Signal(Path, bool)             # (hex_path, is_dry_run)
    factory_provision_requested = Signal()
    file_selected = Signal(Path)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._selected_file: Optional[Path] = None
        self._current_image: Optional[ImageInfo] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        # 1. Visual Memory Map Bar
        self.memory_map = MemoryMapWidget(self)
        layout.addWidget(self.memory_map)

        # 2. Main Work Area Splitter (Left Controls, Right Terminal)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Column: Config & Actions
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(10)

        # HEX File Card
        file_card = QFrame()
        file_card.setObjectName("cardSurface")
        file_card_layout = QVBoxLayout(file_card)
        file_card_layout.setContentsMargins(12, 10, 12, 10)
        file_card_layout.setSpacing(6)

        file_title = QLabel("TẬP TIN FIRMWARE APPLICATION (.HEX)")
        file_title.setObjectName("eyebrowLabel")
        file_card_layout.addWidget(file_title)

        file_input_h = QHBoxLayout()
        file_input_h.setSpacing(6)
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("Đường dẫn file .hex...")
        self.file_path_edit.setReadOnly(True)
        file_input_h.addWidget(self.file_path_edit, 1)

        self.browse_btn = QPushButton("📁 Chọn HEX…")
        self.browse_btn.clicked.connect(self._browse_file)
        file_input_h.addWidget(self.browse_btn)
        file_card_layout.addLayout(file_input_h)

        # HEX Metadata telemetry
        self.meta_label = QLabel("Chưa chọn file firmware")
        self.meta_label.setObjectName("monoText")
        self.meta_label.setStyleSheet("font-size: 11px; color: #64748B;")
        file_card_layout.addWidget(self.meta_label)
        left_layout.addWidget(file_card)

        # Stepper card
        self.stepper = PipelineStepper(left_container)
        left_layout.addWidget(self.stepper)

        # Action Buttons Card
        action_card = QFrame()
        action_card.setObjectName("cardSurface")
        action_card_layout = QVBoxLayout(action_card)
        action_card_layout.setContentsMargins(12, 10, 12, 10)
        action_card_layout.setSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        action_card_layout.addWidget(self.progress_bar)

        act_title = QLabel("THAO TÁC NẠP FLASH")
        act_title.setObjectName("eyebrowLabel")
        action_card_layout.addWidget(act_title)

        btns_h = QHBoxLayout()
        btns_h.setSpacing(6)

        self.dry_run_btn = QPushButton("🔍 Dry-Run (Kiểm tra)")
        self.dry_run_btn.setEnabled(False)
        self.dry_run_btn.clicked.connect(lambda: self.flash_requested.emit(self._selected_file, True))
        btns_h.addWidget(self.dry_run_btn)

        self.flash_btn = QPushButton("⚡ Nạp Application")
        self.flash_btn.setObjectName("primaryButton")
        self.flash_btn.setEnabled(False)
        self.flash_btn.clicked.connect(lambda: self.flash_requested.emit(self._selected_file, False))
        btns_h.addWidget(self.flash_btn, 1)

        action_card_layout.addLayout(btns_h)

        # Factory Provision Danger Action
        self.factory_btn = QPushButton("⚠ Cấp phát Bootloader Nhà Máy (Factory Provision S0-S2)")
        self.factory_btn.setObjectName("dangerButton")
        self.factory_btn.setToolTip("Ghi đè Sector 0-2 với Bootloader chính thức. Yêu cầu xác nhận 'PROVISION BOOTLOADER'.")
        self.factory_btn.clicked.connect(self.factory_provision_requested.emit)
        action_card_layout.addWidget(self.factory_btn)

        left_layout.addWidget(action_card)
        left_layout.addStretch(1)
        splitter.addWidget(left_container)

        # Right Column: Live Terminal & Output Stream
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        term_header = QHBoxLayout()
        term_title = QLabel("NHẬT KÝ GIAO DIỆN OPENOCD & FLASH TELEMETRY")
        term_title.setObjectName("eyebrowLabel")
        term_header.addWidget(term_title)
        term_header.addStretch(1)

        self.clear_btn = QPushButton("Xóa Log")
        self.clear_btn.setObjectName("ghostButton")
        self.clear_btn.clicked.connect(self.clear_log)
        term_header.addWidget(self.clear_btn)
        right_layout.addLayout(term_header)

        self.terminal = QPlainTextEdit()
        self.terminal.setObjectName("terminalView")
        self.terminal.setReadOnly(True)
        right_layout.addWidget(self.terminal, 1)

        splitter.addWidget(right_container)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)

        layout.addWidget(splitter, 1)

    def set_selected_file(self, path: Path) -> None:
        self._selected_file = path
        self.file_path_edit.setText(str(path))
        try:
            image = inspect_image(path)
            self._current_image = image
            self.memory_map.set_image_span(image.min_address, image.flash_span_size)
            self.meta_label.setText(
                f"Vector: 0x{image.entry_point:08X} • Span: 0x{image.min_address:08X}..0x{image.max_address:08X} "
                f"({image.flash_span_size / 1024.0:.1f} KB) • CRC32: 0x{image.flash_crc32:08X}"
            )
            self.flash_btn.setEnabled(True)
            self.dry_run_btn.setEnabled(True)
        except Exception as err:
            self._current_image = None
            self.memory_map.set_image_span(None, None)
            self.meta_label.setText(f"Lỗi phân tích HEX: {str(err)}")
            self.flash_btn.setEnabled(False)
            self.dry_run_btn.setEnabled(False)
        self.file_selected.emit(path)

    def _browse_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Chọn file HEX Firmware Application", "", "Intel HEX (*.hex);;All Files (*.*)"
        )
        if path_str:
            self.set_selected_file(Path(path_str))

    def append_log(self, line: str) -> None:
        self.terminal.appendPlainText(line)

    def clear_log(self) -> None:
        self.terminal.clear()
