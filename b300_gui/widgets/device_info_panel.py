"""Pinned hardware information side panel matching the B300 ST-Link Tools v2.0 design."""
from __future__ import annotations

from typing import Optional, Sequence
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QScrollArea,
)

from b300_core.models import ProbeInfo, TargetInfo
from .engineering import engineering_icon


class DeviceMetricCard(QFrame):
    """Clean elevated card with an icon, title, value, and subtitle/badges."""

    def __init__(self, icon_name: str, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("infoMetricCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Header row with icon + title + badge
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(engineering_icon(icon_name, 18).pixmap(18, 18))
        header.addWidget(self.icon_label)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricTitle")
        self.title_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #94A3B8;")
        header.addWidget(self.title_label, 1)

        self.badge_label = QLabel("")
        self.badge_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #10B981;")
        header.addWidget(self.badge_label)
        layout.addLayout(header)

        # Main value
        self.value_label = QLabel("—")
        self.value_label.setObjectName("metricValue")
        self.value_label.setStyleSheet("font-size: 13px; font-weight: 800; color: #F8FAFC;")
        self.value_label.setWordWrap(True)
        layout.addWidget(self.value_label)

        # Subtitle
        self.sub_label = QLabel("")
        self.sub_label.setObjectName("metricSub")
        self.sub_label.setStyleSheet("font-size: 10.5px; color: #64748B;")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.sub_label)


class DeviceInfoPanel(QFrame):
    """Permanent right sidebar displaying live hardware parameters and flash result."""

    def __init__(self, parent: Optional[QWidget] = None, *, summary_only=False) -> None:
        super().__init__(parent)
        self.setObjectName("deviceInfoSidebar")
        self.setFixedWidth(270)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Panel Title Header
        title_row = QHBoxLayout()
        title_row.setContentsMargins(4, 2, 4, 6)
        title_row.setSpacing(6)
        lbl_icon = QLabel()
        lbl_icon.setPixmap(engineering_icon('history', 18).pixmap(18, 18))
        title_row.addWidget(lbl_icon)
        lbl_title = QLabel("Thông tin thiết bị")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #F8FAFC; letter-spacing: 0.3px;")
        title_row.addWidget(lbl_title, 1)
        main_layout.addLayout(title_row)

        # Scrollable container for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        cards_layout = QVBoxLayout(content)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)

        # 1. ST-Link Probe Card
        self.card_probe = DeviceMetricCard("connection", "Đầu dò ST-Link")
        self.card_probe.badge_label.setText("● SẴN SÀNG")
        self.card_probe.value_label.setText("Chưa phát hiện ST-Link")
        self.card_probe.sub_label.setText("Sê-ri: —\nPhiên bản: —")
        cards_layout.addWidget(self.card_probe)

        # 2. VTarget Card
        self.card_vtarget = DeviceMetricCard("program", "Điện áp đích")
        self.card_vtarget.badge_label.setText("Ổn định")
        self.card_vtarget.badge_label.setStyleSheet("background: #064E3B; color: #34D399; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700;")
        self.card_vtarget.value_label.setText("—")
        self.card_vtarget.sub_label.setText("Đo từ chân điện áp đích của ST-Link")
        cards_layout.addWidget(self.card_vtarget)

        # 3. Target MCU Card
        self.card_mcu = DeviceMetricCard("device", "MCU đích")
        self.card_mcu.value_label.setText("Chưa đọc MCU")
        self.card_mcu.sub_label.setText("ARM Cortex-M4")
        cards_layout.addWidget(self.card_mcu)

        # 4. Flash Memory Card
        self.card_flash = DeviceMetricCard("database", "Bộ nhớ chương trình")
        self.card_flash.value_label.setText("Chưa đọc bộ nhớ")
        self.card_flash.sub_label.setText("16 phân vùng | 64 KiB/phân vùng")
        cards_layout.addWidget(self.card_flash)

        # 5. Protection State Card
        self.card_protection = DeviceMetricCard("shield", "Trạng thái bảo vệ")
        self.card_protection.value_label.setText("Bình thường")
        self.card_protection.sub_label.setText("WRP S0-S2: Bảo vệ\nRDP: Mức 0\nSiêu dữ liệu OTA: Được quản lý")
        cards_layout.addWidget(self.card_protection)

        # 6. Latest Flash Result Card
        self.card_result = DeviceMetricCard("history", "Kết quả lần nạp gần nhất")
        self.card_result.value_label.setText("Chưa có")
        self.card_result.sub_label.setText("Hãy nạp ứng dụng để xem kết quả.")
        cards_layout.addWidget(self.card_result)
        if summary_only:
            lbl_title.setText('Lần nạp gần nhất')
            for card in (self.card_probe, self.card_vtarget, self.card_mcu, self.card_flash, self.card_protection):
                card.hide()

        cards_layout.addStretch(1)
        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)
        self.set_probes(())
        self.set_target_info(None)

    def set_probes(self, probes: Sequence[ProbeInfo], selected_serial: Optional[str] = None) -> None:
        probe = next((p for p in probes if selected_serial and p.serial == selected_serial), None)
        if probe is None and len(probes) == 1:
            probe = probes[0]
        if probe is not None:
            self.card_probe.badge_label.setText("● SẴN SÀNG")
            self.card_probe.badge_label.setStyleSheet("color: #10B981; font-weight: 800; font-size: 10px;")
            self.card_probe.value_label.setText(probe.name)
            serial = probe.serial or "Chưa có số sê-ri"
            self.card_probe.sub_label.setText("Sê-ri: %s" % serial)
        elif probes:
            self.card_probe.badge_label.setText("● Chờ chọn")
            self.card_probe.value_label.setText("%d ST-Link" % len(probes))
            self.card_probe.sub_label.setText("Chọn đúng probe trên thanh công cụ")
        else:
            self.card_probe.badge_label.setText("● MẤT KẾT NỐI")
            self.card_probe.badge_label.setStyleSheet("color: #EF4444; font-weight: 800; font-size: 10px;")
            self.card_probe.value_label.setText("Chưa cắm ST-Link")
            self.card_probe.sub_label.setText("Kiểm tra cáp USB và trình điều khiển")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        if info is None:
            self.card_mcu.value_label.setText("Chưa đọc MCU")
            self.card_mcu.sub_label.setText("Bấm kiểm tra MCU")
            self.card_vtarget.value_label.setText("—")
            self.card_vtarget.badge_label.setText("Chưa kiểm tra")
            self.card_vtarget.badge_label.setStyleSheet("color: #94A3B8;")
            self.card_flash.value_label.setText("Chưa đọc bộ nhớ")
            self.card_flash.sub_label.setText("Dung lượng và sơ đồ chờ kiểm tra")
            self.card_protection.value_label.setText("Chưa kiểm tra")
            self.card_protection.sub_label.setText("WRP S0-S2: —\nRDP: —")
            return

        # MCU đích
        self.card_mcu.value_label.setText("STM32F407/417" if info.device_id & 0xFFF == 0x413
                                         else "MCU ID 0x%03X" % (info.device_id & 0xFFF))
        self.card_mcu.sub_label.setText("Mã thiết bị · 0x%08X" % info.device_id)

        # Voltage
        if info.target_voltage is not None:
            self.card_vtarget.value_label.setText("%.2f V" % info.target_voltage)
            self.card_vtarget.badge_label.setText("Lần kiểm tra gần nhất")
        else:
            self.card_vtarget.value_label.setText("—")
            self.card_vtarget.badge_label.setText("Chưa đo")

        # Dung lượng bộ nhớ
        self.card_flash.value_label.setText("%d KiB" % info.flash_kib)
        self.card_flash.sub_label.setText("B300: Khởi động S0–S2 · Siêu dữ liệu S3 · Ứng dụng S4–S7")

        # Bảo vệ
        protected = set(getattr(info, "protected_sectors", ()))
        wrp_text = "Chưa kiểm tra" if not info.protection_reported else (
            "Đã bảo vệ" if {0, 1, 2}.issubset(protected) else "Chưa bảo vệ đủ")
        rdp_text = "Đã khóa đọc" if getattr(info, "readout_protected", False) else "Mức 0"
        self.card_protection.value_label.setText("Chưa đủ bằng chứng" if not info.protection_reported
                                                else "Kết quả kiểm tra gần nhất")
        self.card_protection.sub_label.setText("WRP S0–S2: %s\nRDP: %s" % (wrp_text, rdp_text))

    def set_latest_result(self, success: bool, detail: str, timestamp_str: str = "") -> None:
        if success:
            self.card_result.badge_label.setText("● Thành công")
            self.card_result.badge_label.setStyleSheet("color: #10B981; font-weight: 800; font-size: 10px;")
            self.card_result.value_label.setText(timestamp_str or "Hoàn tất")
            self.card_result.sub_label.setText(detail)
        else:
            self.card_result.badge_label.setText("● Thất bại")
            self.card_result.badge_label.setStyleSheet("color: #EF4444; font-weight: 800; font-size: 10px;")
            self.card_result.value_label.setText(timestamp_str or "Lỗi")
            self.card_result.sub_label.setText(detail)


__all__ = ["DeviceInfoPanel", "DeviceMetricCard"]
