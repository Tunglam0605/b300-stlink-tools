"""Policy-backed Bootloader → Metadata → Application partition schematic."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QWidget
from b300_core.policy import SECTORS, FLASH_START_ADDRESS, FLASH_END_ADDRESS
from .engineering import SectionCard


class FlashPlanBar(SectionCard):
    view_details_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("3. Kế hoạch nạp bộ nhớ", "Phân vùng B300 theo chính sách lõi · sơ đồ không theo tỷ lệ", parent, icon="chart")
        self.body.setSpacing(6)
        self.lbl_total_flash = QLabel("B300 · %d KiB" % ((FLASH_END_ADDRESS - FLASH_START_ADDRESS) // 1024))
        self.lbl_total_flash.setObjectName("mutedLabel")
        self.header_layout.addWidget(self.lbl_total_flash)
        row = QHBoxLayout()
        row.setSpacing(8)
        role_labels = {"Bootloader": "Bootloader", "OTA metadata": "Siêu dữ liệu OTA", "Application": "Ứng dụng"}
        for role, suffix in (("Bootloader", "boot"), ("OTA metadata", "meta"), ("Application", "app")):
            sectors = [s for s in SECTORS if s.role == role]
            start, end = sectors[0].start_address, sectors[-1].end_address
            frame = QFrame()
            frame.setObjectName("memorySegment")
            frame.setProperty("region", suffix)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(10, 8, 10, 8)
            title = QLabel(role_labels[role])
            title.setObjectName("fieldLabel")
            title.setWordWrap(True)
            indices = "S%d" % sectors[0].index if len(sectors) == 1 else "S%d–S%d" % (sectors[0].index, sectors[-1].index)
            text = "%s · 0x%08X–0x%08X (%d KiB)" % (indices, start, end, (end - start + 1) // 1024)
            subtitle = QLabel(text)
            subtitle.setObjectName("monoText")
            subtitle.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(subtitle)
            row.addWidget(frame, 2 if suffix == "app" else 1)
            setattr(self, "seg_" + suffix, frame)
            setattr(self, "lbl_" + suffix + "_title", title)
            setattr(self, "lbl_" + suffix + "_sub", subtitle)
            if suffix == "app":
                self._application_region_text = text
        self.body.addLayout(row)
        self.btn_details = QPushButton("Chi tiết / Chế độ nhà máy")
        self.btn_details.setToolTip("Bootloader yêu cầu WRP · ứng dụng và siêu dữ liệu dùng quy trình chuẩn.")
        self.btn_details.clicked.connect(self.view_details_requested.emit)
        self.header_layout.addWidget(self.btn_details)

    def update_app_span(self, start: int, end: int, size: int) -> None:
        self.lbl_app_sub.setText("HEX: 0x%08X–0x%08X (%.1f KiB)" % (start, end, size / 1024.0))
        self.lbl_app_sub.setToolTip(self._application_region_text)

    def reset_app_span(self) -> None:
        self.lbl_app_sub.setText(self._application_region_text)
        self.lbl_app_sub.setToolTip("")


__all__ = ["FlashPlanBar"]
