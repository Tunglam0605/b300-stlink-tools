"""Evidence-based Device / Target status view for B300 v0.18.

No healthy/protected target state is shown until B300 has actually inspected the
MCU.  This prevents a fresh GUI from visually claiming WRP/VDD/device identity
before read-only target evidence exists.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from b300_core.models import ProbeInfo, TargetInfo


class DeviceView(QWidget):
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
        content = QWidget()
        content.setObjectName("deviceContent")
        self.layout = QVBoxLayout(content)
        self.layout.setContentsMargins(16, 12, 16, 14)
        self.layout.setSpacing(12)
        self._build_ui()
        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        header = QFrame()
        header.setObjectName("headerRibbon")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        row = QHBoxLayout()
        title = QLabel("DEVICE · ST-LINK & TARGET MCU")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.btn_refresh = QPushButton("↻ Quét ST-Link")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        self.btn_doctor = QPushButton("🩺 Kiểm tra Target")
        self.btn_doctor.clicked.connect(self.doctor_requested.emit)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_doctor)
        header_layout.addLayout(row)
        description = QLabel(
            "Các giá trị Target chỉ hiển thị sau khi đọc bằng B300; trạng thái mặc định không giả định WRP/VDD/Device ID."
        )
        description.setWordWrap(True)
        description.setObjectName("pageSubtitle")
        header_layout.addWidget(description)
        self.layout.addWidget(header)

        target = QFrame()
        target.setObjectName("cardSurface")
        target_layout = QVBoxLayout(target)
        target_layout.setContentsMargins(14, 12, 14, 12)
        target_layout.addWidget(self._eyebrow("1. TARGET MCU · READ-ONLY EVIDENCE"))
        grid = QGridLayout()
        grid.addWidget(QLabel("MCU"), 0, 0)
        self.val_mcu_family = QLabel("Chưa đọc target")
        grid.addWidget(self.val_mcu_family, 0, 1)
        grid.addWidget(QLabel("Device ID"), 0, 2)
        self.val_dev_id = QLabel("Chưa kiểm tra")
        grid.addWidget(self.val_dev_id, 0, 3)
        grid.addWidget(QLabel("Flash"), 1, 0)
        self.val_flash_size = QLabel("Chưa kiểm tra")
        grid.addWidget(self.val_flash_size, 1, 1)
        grid.addWidget(QLabel("VDD"), 1, 2)
        self.val_voltage = QLabel("Chưa kiểm tra")
        grid.addWidget(self.val_voltage, 1, 3)
        target_layout.addLayout(grid)
        self.layout.addWidget(target)

        safety = QFrame()
        safety.setObjectName("cardSurface")
        safety_layout = QVBoxLayout(safety)
        safety_layout.setContentsMargins(14, 12, 14, 12)
        safety_layout.addWidget(self._eyebrow("2. FLASH SAFETY · OPTION BYTES / WRP / RDP"))
        safety_grid = QGridLayout()
        safety_grid.addWidget(QLabel("Bootloader WRP"), 0, 0)
        self.val_wrp = QLabel("Chưa kiểm tra")
        safety_grid.addWidget(self.val_wrp, 0, 1)
        safety_grid.addWidget(QLabel("RDP"), 0, 2)
        self.val_rdp = QLabel("Chưa kiểm tra")
        safety_grid.addWidget(self.val_rdp, 0, 3)
        safety_grid.addWidget(QLabel("OTA Metadata"), 1, 0)
        self.val_meta = QLabel("S3 · 0x0800C000 · contract do B300 quản lý")
        safety_grid.addWidget(self.val_meta, 1, 1)
        safety_grid.addWidget(QLabel("Normal App policy"), 1, 2)
        policy = QLabel("Không mass erase · không ghi S0–S2")
        safety_grid.addWidget(policy, 1, 3)
        safety_layout.addLayout(safety_grid)
        self.layout.addWidget(safety)

        probe = QFrame()
        probe.setObjectName("cardSurface")
        probe_layout = QVBoxLayout(probe)
        probe_layout.setContentsMargins(14, 12, 14, 12)
        probe_layout.addWidget(self._eyebrow("3. ST-LINK PROBE"))
        probe_grid = QGridLayout()
        probe_grid.addWidget(QLabel("Probe"), 0, 0)
        self.val_probe_name = QLabel("Chưa quét")
        probe_grid.addWidget(self.val_probe_name, 0, 1)
        probe_grid.addWidget(QLabel("Serial"), 0, 2)
        self.val_probe_serial = QLabel("N/A")
        probe_grid.addWidget(self.val_probe_serial, 0, 3)
        probe_grid.addWidget(QLabel("Transport"), 1, 0)
        self.val_protocol = QLabel("SWD · B300/OpenOCD managed")
        probe_grid.addWidget(self.val_protocol, 1, 1)
        probe_grid.addWidget(QLabel("Probe state"), 1, 2)
        self.val_conn_state = QLabel("○ DISCONNECTED")
        probe_grid.addWidget(self.val_conn_state, 1, 3)
        probe_layout.addLayout(probe_grid)
        self.layout.addWidget(probe)
        self.layout.addStretch(1)

    @staticmethod
    def _eyebrow(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("eyebrowLabel")
        return label

    def set_probes(self, probes: Sequence[ProbeInfo]) -> None:
        self._probes = list(probes)
        if not self._probes:
            self.val_probe_name.setText("Không tìm thấy ST-Link")
            self.val_probe_serial.setText("N/A")
            self.val_conn_state.setText("○ DISCONNECTED")
            return
        selected = self._probes[0]
        suffix = " · +%d probe" % (len(self._probes) - 1) if len(self._probes) > 1 else ""
        self.val_probe_name.setText("%s%s" % (selected.name, suffix))
        self.val_probe_serial.setText(selected.serial or "Auto-select / serial unavailable")
        self.val_conn_state.setText("● PROBE READY")
        source = selected.source or "USB"
        self.val_protocol.setText("SWD · source=%s · B300/OpenOCD managed" % source)

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        if info is None:
            self.val_mcu_family.setText("Chưa đọc target")
            self.val_dev_id.setText("Chưa kiểm tra")
            self.val_flash_size.setText("Chưa kiểm tra")
            self.val_voltage.setText("Chưa kiểm tra")
            self.val_wrp.setText("Chưa kiểm tra")
            self.val_rdp.setText("Chưa kiểm tra")
            return
        self.val_mcu_family.setText(
            "STM32F407" if info.device_id & 0xFFF == 0x413
            else "STM32 ID 0x%03X" % (info.device_id & 0xFFF)
        )
        self.val_dev_id.setText("0x%08X" % info.device_id)
        self.val_flash_size.setText("%d KB" % info.flash_kib)
        self.val_voltage.setText("%.2f V" % info.target_voltage)
        if info.protection_reported:
            protected = set(info.protected_sectors)
            required = {0, 1, 2}
            wrp_state = "PROTECTED" if required.issubset(protected) else "NOT FULLY PROTECTED"
            self.val_wrp.setText("S0–S2 · %s · %s" % (wrp_state, info.protection_summary))
        else:
            self.val_wrp.setText("Không có bằng chứng WRP · %s" % info.protection_summary)
        self.val_rdp.setText("Read protected" if info.readout_protected else "Level 0 / readable")

    def set_busy(self, busy: bool) -> None:
        """Block new target access while another HardwareSession owns the probe."""
        self.btn_refresh.setEnabled(not busy)
        self.btn_doctor.setEnabled(not busy)


__all__ = ["DeviceView"]
