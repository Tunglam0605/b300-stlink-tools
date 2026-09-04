"""Target MCU and ST-Link Hardware Information View for B300 (v0.18).

Displays hardware health, Option Bytes, Write Protection (WRP), and target readiness.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import ProbeInfo, TargetInfo


class DeviceView(QWidget):
    """Clean Device & Target Health inspection dashboard."""

    refresh_requested = Signal()
    doctor_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("deviceViewContainer")
        self._probes: List[ProbeInfo] = []
        self._target_info: Optional[TargetInfo] = None

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("deviceContent")
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(16, 12, 16, 14)
        self.layout.setSpacing(12)

        self._build_ui()
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        # Header banner
        header = QFrame()
        header.setObjectName("headerRibbon")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 8, 12, 8)
        h_layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_lbl = QLabel("DEVICE · THIẾT BỊ & TARGET MCU")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 800; color: #F8FAFC; letter-spacing: 0.6px;")
        title_row.addWidget(title_lbl)
        title_row.addStretch(1)

        self.btn_refresh = QPushButton("🔄 Quét lại phần cứng")
        self.btn_refresh.setObjectName("ghostButton")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        title_row.addWidget(self.btn_refresh)

        self.btn_doctor = QPushButton("🩺 Chẩn đoán (Doctor)")
        self.btn_doctor.setObjectName("ghostButton")
        self.btn_doctor.clicked.connect(self.doctor_requested.emit)
        title_row.addWidget(self.btn_doctor)
        h_layout.addLayout(title_row)

        desc = QLabel("Kiểm tra thông số vật lý của vi điều khiển STM32F407, điện áp VDD, Option Bytes và WRP Bootloader.")
        desc.setStyleSheet("font-size: 11px; color: #94A3B8;")
        h_layout.addWidget(desc)
        self.layout.addWidget(header)

        # 1. Target MCU Card
        tgt_card = QFrame()
        tgt_card.setObjectName("cardSurface")
        tgt_layout = QVBoxLayout(tgt_card)
        tgt_layout.setContentsMargins(14, 12, 14, 12)
        tgt_layout.setSpacing(8)

        tgt_title = QLabel("1. THÔNG SỐ TARGET MCU")
        tgt_title.setObjectName("eyebrowLabel")
        tgt_layout.addWidget(tgt_title)

        grid_tgt = QGridLayout()
        grid_tgt.setHorizontalSpacing(14)
        grid_tgt.setVerticalSpacing(6)

        grid_tgt.addWidget(QLabel("Dòng vi điều khiển:"), 0, 0)
        self.val_mcu_family = QLabel("STM32F407 (ARM Cortex-M4 with FPU)")
        self.val_mcu_family.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        grid_tgt.addWidget(self.val_mcu_family, 0, 1)

        grid_tgt.addWidget(QLabel("Target Device ID:"), 0, 2)
        self.val_dev_id = QLabel("0x101F6413 (B300 Supported)")
        self.val_dev_id.setStyleSheet("font-family: monospace; font-weight: 700; color: #38BDF8;")
        grid_tgt.addWidget(self.val_dev_id, 0, 3)

        grid_tgt.addWidget(QLabel("Dung lượng Flash:"), 1, 0)
        self.val_flash_size = QLabel("512 KB (Sector 0..7)")
        self.val_flash_size.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        grid_tgt.addWidget(self.val_flash_size, 1, 1)

        grid_tgt.addWidget(QLabel("Điện áp nguồn VDD:"), 1, 2)
        self.val_voltage = QLabel("3.30 V · Bình thường")
        self.val_voltage.setStyleSheet("font-weight: 700; color: #10B981;")
        grid_tgt.addWidget(self.val_voltage, 1, 3)

        tgt_layout.addLayout(grid_tgt)
        self.layout.addWidget(tgt_card)

        # 2. Bootloader & Option Bytes Safety Card
        sec_card = QFrame()
        sec_card.setObjectName("cardSurface")
        sec_layout = QVBoxLayout(sec_card)
        sec_layout.setContentsMargins(14, 12, 14, 12)
        sec_layout.setSpacing(8)

        sec_title = QLabel("2. BẢO MẬT & BẢO VỆ BOOTLOADER (SAFETY INTERLOCKS)")
        sec_title.setObjectName("eyebrowLabel")
        sec_layout.addWidget(sec_title)

        grid_sec = QGridLayout()
        grid_sec.setHorizontalSpacing(14)
        grid_sec.setVerticalSpacing(6)

        grid_sec.addWidget(QLabel("Bảo vệ ghi Bootloader:"), 0, 0)
        self.val_wrp = QLabel("Sector 0–2 WRP: ĐƯỢC BẢO VỆ (Locked)")
        self.val_wrp.setStyleSheet("font-weight: 700; color: #10B981;")
        grid_sec.addWidget(self.val_wrp, 0, 1)

        grid_sec.addWidget(QLabel("Bảo vệ đọc RDP:"), 0, 2)
        self.val_rdp = QLabel("Level 0 (Normal / Unlocked)")
        self.val_rdp.setStyleSheet("font-weight: 700; color: #38BDF8;")
        grid_sec.addWidget(self.val_rdp, 0, 3)

        grid_sec.addWidget(QLabel("Khu vực OTA Metadata:"), 1, 0)
        self.val_meta = QLabel("Sector 3 (0x0800C000) · 44B STLM Record")
        self.val_meta.setStyleSheet("color: #94A3B8; font-family: monospace;")
        grid_sec.addWidget(self.val_meta, 1, 1)

        grid_sec.addWidget(QLabel("Chính sách an toàn Flash:"), 1, 2)
        val_policy = QLabel("Mass Erase BỊ CẤM · Chỉ xóa S4–S7")
        val_policy.setStyleSheet("font-weight: 700; color: #10B981;")
        grid_sec.addWidget(val_policy, 1, 3)

        sec_layout.addLayout(grid_sec)
        self.layout.addWidget(sec_card)

        # 3. ST-Link Hardware Card
        probe_card = QFrame()
        probe_card.setObjectName("cardSurface")
        pr_layout = QVBoxLayout(probe_card)
        pr_layout.setContentsMargins(14, 12, 14, 12)
        pr_layout.setSpacing(8)

        pr_title = QLabel("3. MẠCH NẠP ST-LINK PHẦN CỨNG")
        pr_title.setObjectName("eyebrowLabel")
        pr_layout.addWidget(pr_title)

        grid_pr = QGridLayout()
        grid_pr.setHorizontalSpacing(14)
        grid_pr.setVerticalSpacing(6)

        grid_pr.addWidget(QLabel("Tên mạch nạp:"), 0, 0)
        self.val_probe_name = QLabel("ST-Link V2 USB")
        self.val_probe_name.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        grid_pr.addWidget(self.val_probe_name, 0, 1)

        grid_pr.addWidget(QLabel("Serial phần cứng:"), 0, 2)
        self.val_probe_serial = QLabel("Chưa quét")
        self.val_probe_serial.setStyleSheet("font-family: monospace; color: #94A3B8;")
        grid_pr.addWidget(self.val_probe_serial, 0, 3)

        grid_pr.addWidget(QLabel("Giao thức nạp:"), 1, 0)
        self.val_protocol = QLabel("SWD (Serial Wire Debug) · Tốc độ: 2000 kHz")
        self.val_protocol.setStyleSheet("color: #94A3B8;")
        grid_pr.addWidget(self.val_protocol, 1, 1)

        grid_pr.addWidget(QLabel("Trạng thái kết nối:"), 1, 2)
        self.val_conn_state = QLabel("● Đã kết nối")
        self.val_conn_state.setStyleSheet("font-weight: 700; color: #10B981;")
        grid_pr.addWidget(self.val_conn_state, 1, 3)

        pr_layout.addLayout(grid_pr)
        self.layout.addWidget(probe_card)
        self.layout.addStretch(1)

    def set_probes(self, probes: Sequence[ProbeInfo]) -> None:
        self._probes = list(probes)
        if not self._probes:
            self.val_probe_name.setText("Không tìm thấy ST-Link")
            self.val_probe_serial.setText("N/A")
            self.val_conn_state.setText("○ Ngắt kết nối")
            self.val_conn_state.setStyleSheet("font-weight: 700; color: #EF4444;")
        else:
            p = self._probes[0]
            name = getattr(p, "description", None) or "ST-Link V2"
            serial = getattr(p, "serial", None) or "Auto"
            self.val_probe_name.setText(f"{name} ({len(self._probes)} probe)")
            self.val_probe_serial.setText(str(serial))
            self.val_conn_state.setText("● Sẵn sàng")
            self.val_conn_state.setStyleSheet("font-weight: 700; color: #10B981;")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        if info is not None:
            self.val_dev_id.setText(f"0x{info.device_id:08X}")
            self.val_flash_size.setText(f"{info.flash_kib} KB")
            self.val_voltage.setText(f"{info.target_voltage:.2f} V")
            self.val_wrp.setText(f"S0–S2 WRP: {info.protection_summary}")
            rdp_text = "Level 1 (Read Protected)" if info.readout_protected else "Level 0 (Normal / Unlocked)"
            self.val_rdp.setText(rdp_text)
            self.val_rdp.setStyleSheet(
                f"font-weight: 700; color: {'#EF4444' if info.readout_protected else '#10B981'};"
            )


__all__ = ["DeviceView"]
