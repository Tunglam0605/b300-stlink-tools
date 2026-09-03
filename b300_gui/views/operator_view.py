"""Dedicated 1-Click Production Operator Workspace."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

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
    QVBoxLayout,
    QWidget,
)

from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from b300_core.hex_image import inspect_image
from b300_gui.widgets.pass_fail_banner import PassFailBanner
from b300_gui.widgets.pipeline_stepper import PipelineStepper


class OperatorView(QWidget):
    """Clean, foolproof 1-Click Firmware Provisioning View for Factory Operators."""

    flash_requested = Signal(Path, bool)  # (hex_path, is_dry_run)
    file_selected = Signal(Path)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._selected_file: Optional[Path] = None
        self._current_image: Optional[ImageInfo] = None
        self._probes: List[ProbeInfo] = []
        self._target_info: Optional[TargetInfo] = None
        self._flash_ready = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("operatorContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        # 1. Mode Header Ribbon
        header_ribbon = QFrame()
        header_ribbon.setObjectName("headerRibbon")
        ribbon_layout = QVBoxLayout(header_ribbon)
        ribbon_layout.setContentsMargins(12, 8, 12, 8)
        ribbon_layout.setSpacing(3)

        ribbon_title = QLabel("DÂY CHUYỀN SẢN XUẤT · NẠP FIRMWARE 1-CLICK")
        ribbon_title.setObjectName("pageContextTitle")
        ribbon_layout.addWidget(ribbon_title)

        ribbon_sub = QLabel(
            "Quy trình nạp tự động chuẩn hóa: Bảo vệ Bootloader Sector 0–2, nạp Application Sector 4–7 và ghi xác thực 44-byte STLM Metadata."
        )
        ribbon_sub.setObjectName("pageContextSubtitle")
        ribbon_sub.setWordWrap(True)
        ribbon_layout.addWidget(ribbon_sub)

        layout.addWidget(header_ribbon)

        # 2. Result Banner (Top Priority HUD)
        self.banner = PassFailBanner(container)
        self.banner.hide()
        layout.addWidget(self.banner)

        # 3. Probe Status Card
        self.probe_card = QFrame()
        self.probe_card.setObjectName("cardSurface")
        probe_card_layout = QHBoxLayout(self.probe_card)
        probe_card_layout.setContentsMargins(14, 12, 14, 12)
        probe_card_layout.setSpacing(12)

        probe_info_layout = QVBoxLayout()
        probe_info_layout.setSpacing(2)
        self.probe_status_title = QLabel("1. MẠCH NẠP ST-LINK & TARGET MCU")
        self.probe_status_title.setObjectName("eyebrowLabel")
        probe_info_layout.addWidget(self.probe_status_title)

        self.probe_status_main = QLabel("ST-Link STM32F407")
        self.probe_status_main.setObjectName("pageContextTitle")
        probe_info_layout.addWidget(self.probe_status_main)

        self.probe_status_sub = QLabel("Đang chờ quét thiết bị ST-Link...")
        self.probe_status_sub.setObjectName("pageContextSubtitle")
        probe_info_layout.addWidget(self.probe_status_sub)
        probe_card_layout.addLayout(probe_info_layout, 1)

        self.probe_pill = QLabel("CHƯA KẾT NỐI")
        self.probe_pill.setObjectName("statusPillDanger")
        probe_card_layout.addWidget(self.probe_pill)

        layout.addWidget(self.probe_card)

        # 4. Firmware File Selector Card
        self.file_card = QFrame()
        self.file_card.setObjectName("cardSurface")
        file_card_layout = QVBoxLayout(self.file_card)
        file_card_layout.setContentsMargins(14, 12, 14, 12)
        file_card_layout.setSpacing(8)

        file_title = QLabel("2. FILE FIRMWARE APPLICATION (.HEX)")
        file_title.setObjectName("eyebrowLabel")
        file_card_layout.addWidget(file_title)

        file_input_h = QHBoxLayout()
        file_input_h.setSpacing(8)
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("Chọn file HEX firmware Application (0x08010000)...")
        self.file_path_edit.setReadOnly(True)
        self.file_path_edit.setMinimumHeight(34)
        file_input_h.addWidget(self.file_path_edit, 1)

        self.browse_btn = QPushButton("Chọn file HEX…")
        self.browse_btn.setObjectName("primaryButton")
        self.browse_btn.setMinimumHeight(34)
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._browse_file)
        file_input_h.addWidget(self.browse_btn)
        file_card_layout.addLayout(file_input_h)

        # Firmware Validation Details
        self.hex_info_card = QFrame()
        self.hex_info_card.setObjectName("headerRibbon")
        hex_info_layout = QHBoxLayout(self.hex_info_card)
        hex_info_layout.setContentsMargins(10, 6, 10, 6)
        hex_info_layout.setSpacing(14)

        self.info_size = QLabel("Kích thước: --")
        self.info_size.setObjectName("hexInfoLabel")
        hex_info_layout.addWidget(self.info_size)

        self.info_vector = QLabel("Vector Reset: --")
        self.info_vector.setObjectName("hexInfoLabel")
        hex_info_layout.addWidget(self.info_vector)

        self.info_crc = QLabel("CRC32: --")
        self.info_crc.setObjectName("hexInfoLabel")
        hex_info_layout.addWidget(self.info_crc)

        self.info_status = QLabel("Chưa chọn file")
        self.info_status.setObjectName("pageContextSubtitle")
        hex_info_layout.addWidget(self.info_status, 1)

        file_card_layout.addWidget(self.hex_info_card)
        layout.addWidget(self.file_card)

        # 5. Pipeline Stepper Progress
        self.stepper = PipelineStepper(container)
        layout.addWidget(self.stepper)

        # 6. Progress Bar & Large Action Area
        action_card = QFrame()
        action_card.setObjectName("cardSurface")
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(14, 12, 14, 12)
        action_layout.setSpacing(10)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(16)
        self.progress_bar.setVisible(False)
        action_layout.addWidget(self.progress_bar)

        self.status_text = QLabel("Sẵn sàng. Hãy chọn file HEX và nhấn Nạp Firmware.")
        self.status_text.setObjectName("pageContextSubtitle")
        action_layout.addWidget(self.status_text)

        btns_h = QHBoxLayout()
        btns_h.setSpacing(10)

        self.dry_run_btn = QPushButton("Chạy thử (Dry-Run)")
        self.dry_run_btn.setObjectName("primaryButton")
        self.dry_run_btn.setMinimumHeight(44)
        self.dry_run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dry_run_btn.setEnabled(False)
        self.dry_run_btn.clicked.connect(lambda: self._on_flash_clicked(dry_run=True))
        btns_h.addWidget(self.dry_run_btn)

        self.flash_btn = QPushButton("NẠP APPLICATION (1-CLICK FLASH)")
        self.flash_btn.setObjectName("operatorFlashBtn")
        self.flash_btn.setMinimumHeight(44)
        self.flash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flash_btn.setEnabled(False)
        self.flash_btn.clicked.connect(lambda: self._on_flash_clicked(dry_run=False))
        btns_h.addWidget(self.flash_btn, 1)

        action_layout.addLayout(btns_h)
        layout.addWidget(action_card)

        # 7. Collapsible Log Drawer
        self.log_toggle_btn = QPushButton("Xem nhật ký nạp chi tiết (OpenOCD log) ▼")
        self.log_toggle_btn.setObjectName("ghostButton")
        self.log_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_toggle_btn.clicked.connect(self._toggle_log)
        layout.addWidget(self.log_toggle_btn)

        self.log_drawer = QPlainTextEdit()
        self.log_drawer.setObjectName("terminalView")
        self.log_drawer.setReadOnly(True)
        self.log_drawer.setMaximumHeight(150)
        self.log_drawer.setVisible(False)
        layout.addWidget(self.log_drawer)

        layout.addStretch(1)

        scroll.setWidget(container)
        scroll_layout = QVBoxLayout(self)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addWidget(scroll)

    def set_probes(self, probes: List[ProbeInfo]) -> None:
        self._probes = list(probes)
        if not probes:
            self.probe_pill.setText("CHƯA KẾT NỐI")
            self.probe_pill.setObjectName("statusPillDanger")
            self.probe_status_main.setText("Chưa tìm thấy ST-Link")
            self.probe_status_sub.setText("Vui lòng cắm cáp USB ST-Link vào máy tính.")
            self.stepper.set_step_state(0, "pending", "Chờ kết nối ST-Link")
        else:
            p = probes[0]
            self.probe_pill.setText(f"SẴN SÀNG ({len(probes)} probe)")
            self.probe_pill.setObjectName("statusPillSuccess")
            self.probe_status_main.setText(f"{p.name} [{p.serial or 'No Serial'}]")
            if self._target_info:
                self.probe_status_sub.setText(
                    f"Target: 0x{self._target_info.device_id:08X} · Flash {self._target_info.flash_kib} KiB · "
                    f"Điện áp: {self._target_info.target_voltage:.2f}V · {self._target_info.protection_summary}"
                )
            else:
                self.probe_status_sub.setText(f"Đã kết nối qua {p.source.upper()}. Sẵn sàng kiểm tra chip.")
            self.stepper.set_step_state(0, "success", f"ST-Link OK [{p.serial or 'USB'}]")
        self.probe_pill.style().unpolish(self.probe_pill)
        self.probe_pill.style().polish(self.probe_pill)
        self._update_action_state()

    def set_target_info(self, info: TargetInfo) -> None:
        self._target_info = info
        if self._probes:
            self.probe_status_sub.setText(
                f"Target: 0x{info.device_id:08X} · Flash {info.flash_kib} KiB · "
                f"Điện áp: {info.target_voltage:.2f}V · {info.protection_summary}"
            )
            state = "success" if self._flash_ready else "pending"
            detail = (
                f"Target 0x{info.device_id:08X} OK"
                if self._flash_ready
                else "Target chưa đạt điều kiện nạp an toàn"
            )
            self.stepper.set_step_state(0, state, detail)
        self._update_action_state()

    def clear_target_info(self) -> None:
        self._target_info = None
        self._flash_ready = False
        if self._probes:
            probe = self._probes[0]
            self.probe_status_sub.setText(
                f"Đã kết nối qua {probe.source.upper()}. Sẵn sàng kiểm tra chip."
            )
            self.stepper.set_step_state(0, "pending", "Cần kiểm tra lại target/WRP")
        self._update_action_state()

    def set_flash_ready(self, ready: bool) -> None:
        """Reflect MainWindow's validated B300 flash gate in the operator UI."""
        self._flash_ready = ready
        self._update_action_state()

    def set_selected_file(self, path: Path) -> None:
        self._selected_file = path
        self.file_path_edit.setText(str(path))
        try:
            image = inspect_image(path)
            self._current_image = image
            size_kb = image.flash_span_size / 1024.0
            self.info_size.setText(f"Kích thước: {size_kb:.1f} KB")
            self.info_size.setObjectName("monoSize")
            self.info_vector.setText(f"Vector Reset: 0x{image.entry_point:08X}" if image.entry_point else "Vector: --")
            self.info_vector.setObjectName("monoAddress")
            self.info_crc.setText(f"CRC32: 0x{image.flash_crc32:08X}" if image.flash_crc32 else "CRC: --")
            self.info_crc.setObjectName("monoCrc")
            self.info_status.setText("File HEX hợp lệ (Application 0x08010000)")
            self.info_status.setObjectName("statusPillSuccess")
            self.stepper.set_step_state(1, "success", f"Vector 0x{image.entry_point:08X} OK")
        except Exception as err:
            self._current_image = None
            self.info_size.setText("Kích thước: --")
            self.info_vector.setText("Vector: --")
            self.info_crc.setText("CRC: --")
            self.info_status.setText(f"Lỗi file: {str(err)}")
            self.info_status.setObjectName("statusPillDanger")
            self.stepper.set_step_state(1, "error", str(err))

        self.info_size.style().unpolish(self.info_size)
        self.info_size.style().polish(self.info_size)
        self.info_vector.style().unpolish(self.info_vector)
        self.info_vector.style().polish(self.info_vector)
        self.info_crc.style().unpolish(self.info_crc)
        self.info_crc.style().polish(self.info_crc)
        self.info_status.style().unpolish(self.info_status)
        self.info_status.style().polish(self.info_status)

        self.file_selected.emit(path)
        self._update_action_state()

    def set_image_info(self, image: ImageInfo) -> None:
        self._current_image = image
        self._selected_file = Path(image.path) if image.path else None
        if self._selected_file:
            self.file_path_edit.setText(str(self._selected_file))
        size_kb = image.flash_span_size / 1024.0
        self.info_size.setText(f"Kích thước: {size_kb:.1f} KB")
        self.info_size.setObjectName("monoSize")
        self.info_vector.setText(f"Vector Reset: 0x{image.entry_point:08X}" if image.entry_point else "Vector: --")
        self.info_vector.setObjectName("monoAddress")
        self.info_crc.setText(f"CRC32: 0x{image.flash_crc32:08X}" if image.flash_crc32 else "CRC: --")
        self.info_crc.setObjectName("monoCrc")
        self.info_status.setText("File HEX hợp lệ (Application 0x08010000)")
        self.info_status.setObjectName("statusPillSuccess")
        self.info_size.style().unpolish(self.info_size)
        self.info_size.style().polish(self.info_size)
        self.info_vector.style().unpolish(self.info_vector)
        self.info_vector.style().polish(self.info_vector)
        self.info_crc.style().unpolish(self.info_crc)
        self.info_crc.style().polish(self.info_crc)
        self.info_status.style().unpolish(self.info_status)
        self.info_status.style().polish(self.info_status)
        self.stepper.set_step_state(1, "success", f"Vector 0x{image.entry_point:08X} OK")
        self._update_action_state()

    def _browse_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Chọn file HEX Firmware Application", "", "Intel HEX (*.hex);;All Files (*.*)"
        )
        if path_str:
            self.set_selected_file(Path(path_str))

    def _update_action_state(self) -> None:
        has_file = self._current_image is not None
        has_probe = len(self._probes) > 0
        can_run = has_file and has_probe and self._flash_ready
        self.flash_btn.setEnabled(can_run)
        self.dry_run_btn.setEnabled(can_run)
        if not has_probe:
            self.status_text.setText("Vui lòng kết nối mạch nạp ST-Link để tiếp tục.")
        elif not has_file:
            self.status_text.setText("Vui lòng chọn file HEX firmware Application hợp lệ.")
        elif not self._flash_ready:
            self.status_text.setText(
                "Chờ kiểm tra Target, WRP Bootloader và flash plan an toàn trước khi nạp."
            )
        else:
            self.status_text.setText("Sẵn sàng. Nhấn 'NẠP APPLICATION' để tiến hành nạp an toàn.")

    def _on_flash_clicked(self, dry_run: bool) -> None:
        if self._selected_file:
            self.banner.hide()
            self.stepper.reset_steps()
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(10)
            self.flash_requested.emit(self._selected_file, dry_run)

    def _toggle_log(self) -> None:
        visible = not self.log_drawer.isVisible()
        self.log_drawer.setVisible(visible)
        self.log_toggle_btn.setText("Ẩn nhật ký nạp ▲" if visible else "Xem nhật ký nạp chi tiết (OpenOCD log) ▼")

    def append_log(self, text: str) -> None:
        self.log_drawer.appendPlainText(text)
