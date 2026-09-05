"""Read-only target evidence with the canonical B300 policy memory map."""
from __future__ import annotations
from typing import Optional, Sequence
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from b300_core.models import ProbeInfo, TargetInfo
from b300_core.policy import SECTORS, SUPPORTED_DEVICE_ID
from b300_gui.widgets.engineering import ActivityLogPanel, SectionCard


class DeviceView(QWidget):
    refresh_requested = Signal()
    doctor_requested = Signal()
    read_metadata_requested = Signal()
    export_evidence_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._probes = []
        self._target_info = None
        self.setObjectName("deviceViewContainer")
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.summary_grid = QGridLayout()
        self.summary_grid.setSpacing(12)
        self.summary_cards = []
        self.kpi_probe_name, self.kpi_probe_serial = self._metric("ST-Link", "Chưa quét", "Sê-ri: —")
        self.kpi_mcu_name, self.kpi_mcu_arch = self._metric("MCU đích", "Chưa đọc MCU", "Chưa đọc kiến trúc")
        self.kpi_flash_size, self.kpi_flash_desc = self._metric("Bộ nhớ chương trình", "Chưa kiểm tra", "Theo lần kiểm tra gần nhất")
        self.kpi_prot_state, self.kpi_rdp_level = self._metric("Bảo vệ", "Chưa kiểm tra", "Chưa kiểm tra RDP")
        self.kpi_voltage, self.kpi_volt_badge = self._metric("Điện áp đích", "Chưa kiểm tra", "Chưa đánh giá")
        layout.addLayout(self.summary_grid)

        self.details_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.details_layout.setSpacing(12)
        details = SectionCard("Thông tin chi tiết MCU", "Đọc và phân tích thông tin từ MCU", self, icon="device")
        details.body.setSpacing(7)
        self.val_dev_id = self._row(details, "Mã thiết bị")
        self.val_flash_size = self._row(details, "Dung lượng bộ nhớ")
        self.val_rev_id = self._row(details, "Bản sửa đổi", "Chưa đọc")
        self.val_sram_size = self._row(details, "SRAM", "Chưa đọc")
        self.val_option_bytes = self._row(details, "Byte tùy chọn", "Chưa đọc")
        self.val_vector_table = self._row(details, "Bảng vector", "Chưa đọc")
        self.val_uid = self._row(details, "UID", "Chưa đọc")
        self.val_reset_status = self._row(details, "Nguyên nhân khởi động lại", "Chưa đọc")
        details.body.addStretch(1)
        self.details_layout.addWidget(details, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        memory = SectionCard("Bản đồ bộ nhớ & bảo vệ", "Phân vùng B300 theo chính sách lõi", self, icon="chart")
        memory.body.setSpacing(7)
        strip = QFrame()
        strip.setObjectName("memoryStrip")
        strip.setFixedHeight(28)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(2)
        roles = list(dict.fromkeys(sector.role for sector in SECTORS))
        role_labels = {"Bootloader": "Bootloader", "OTA metadata": "Siêu dữ liệu OTA", "Application": "Ứng dụng"}
        for role, region in zip(roles, ("boot", "meta", "app")):
            sectors = [s for s in SECTORS if s.role == role]
            segment = QFrame()
            segment.setObjectName("memorySegment")
            segment.setProperty("region", region)
            segment.setToolTip("%s · 0x%08X–0x%08X" % (role_labels.get(role, role), sectors[0].start_address, sectors[-1].end_address))
            strip_layout.addWidget(segment, sum(s.size for s in sectors))
        memory.body.addWidget(strip)
        for role in roles:
            sectors = [s for s in SECTORS if s.role == role]
            start, end = sectors[0].start_address, sectors[-1].end_address
            indices = "S%d" % sectors[0].index if len(sectors) == 1 else "S%d–S%d" % (sectors[0].index, sectors[-1].index)
            self._row(memory, "%s · %s" % (role_labels.get(role, role), indices), "0x%08X - 0x%08X (%d KiB)" % (start, end, (end - start + 1) // 1024))
        right_layout.addWidget(memory)
        protection = SectionCard("Kiểm tra bảo vệ", "Trạng thái từ lần kiểm tra gần nhất", self, icon="shield")
        protection.body.setSpacing(7)
        self.val_wrp = self._row(protection, "WRP S0–S2")
        self.val_rdp = self._row(protection, "RDP")
        self.val_boot_prot = self._row(protection, "Bootloader")
        self.val_meta = self._row(protection, "Siêu dữ liệu OTA", "Chưa đọc")
        right_layout.addWidget(protection)
        self.details_layout.addWidget(right, 1)
        layout.addLayout(self.details_layout)

        actions = QHBoxLayout()
        self.btn_doctor = QPushButton("KIỂM TRA MCU")
        self.btn_doctor.setObjectName("primaryActionButton")
        self.btn_doctor.setMinimumHeight(36)
        self.btn_doctor.clicked.connect(self.doctor_requested.emit)
        actions.addWidget(self.btn_doctor, 2)
        self.btn_refresh = QPushButton("Quét ST-Link")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(self.btn_refresh)
        self.btn_read_metadata = self._unsupported("Đọc siêu dữ liệu", "Chưa tích hợp đọc siêu dữ liệu tại trang này.")
        self.btn_export_evidence = self._unsupported("Xuất bằng chứng", "Chưa tích hợp xuất bằng chứng tại trang này.")
        actions.addWidget(self.btn_read_metadata, 1)
        actions.addWidget(self.btn_export_evidence, 1)
        for button in (self.btn_doctor, self.btn_refresh, self.btn_read_metadata, self.btn_export_evidence):
            button.setMinimumHeight(42)
        layout.addLayout(actions)
        self.activity_log = ActivityLogPanel(parent=self)
        self.activity_log.setMaximumHeight(140)
        self.log_terminal = self.activity_log.terminal
        layout.addWidget(self.activity_log)
        layout.addStretch(1)
        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        # Compatibility aliases point at actual visible evidence, not duplicate state.
        self.val_mcu_family = self.kpi_mcu_name
        self.val_voltage = self.kpi_voltage
        self.val_probe_name = self.kpi_probe_name
        self.val_probe_serial = self.kpi_probe_serial
        self.val_conn_state = QLabel("Chưa quét")
        self.val_conn_state.setObjectName("mutedLabel")
        self.summary_cards[0].header_text_layout.addWidget(self.val_conn_state)
        self.kpi_probe_status = self.val_conn_state
        self.btn_target_doctor = self.btn_doctor
        self.btn_guide = self._unsupported("Hướng dẫn", "Chức năng chưa được tích hợp.")
        self.btn_menu = self._unsupported("Tùy chọn", "Chức năng chưa được tích hợp.")
        self.btn_guide.setParent(self)
        self.btn_menu.setParent(self)
        self.btn_guide.hide()
        self.btn_menu.hide()
        self._arrange_summary(5)

    def _metric(self, title: str, value: str, detail: str):
        icon = {"ST-Link": "connection", "MCU đích": "device", "Bộ nhớ chương trình": "database", "Bảo vệ": "shield", "Điện áp đích": "program"}[title]
        card = SectionCard(title, parent=self, icon=icon)
        card.body.setContentsMargins(12, 10, 12, 10)
        card.body.setSpacing(5)
        card.setMinimumHeight(104)
        label = QLabel(value)
        label.setObjectName("metricValue")
        label.setWordWrap(True)
        subtitle = QLabel(detail)
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        card.header_text_layout.addWidget(label)
        card.header_text_layout.addWidget(subtitle)
        self.summary_cards.append(card)
        return label, subtitle

    @staticmethod
    def _row(card: SectionCard, name: str, value: str = "Chưa kiểm tra") -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(12)
        title = QLabel(name)
        title.setObjectName("mutedLabel")
        title.setWordWrap(True)
        title.setMinimumHeight(27)
        row.addWidget(title, 2)
        label = QLabel(value)
        label.setObjectName("monoText")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(label, 3)
        card.body.addLayout(row)
        return label

    @staticmethod
    def _unsupported(text: str, reason: str) -> QPushButton:
        button = QPushButton(text)
        button.setEnabled(False)
        button.setToolTip(reason)
        return button

    def _arrange_summary(self, columns: int) -> None:
        for card in self.summary_cards:
            self.summary_grid.removeWidget(card)
        for index, card in enumerate(self.summary_cards):
            self.summary_grid.addWidget(card, index // columns, index % columns)
        for column in range(5):
            self.summary_grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange_summary(5 if self.width() >= 1050 else 3 if self.width() >= 650 else 2)
        self.details_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 950 else QBoxLayout.Direction.TopToBottom)

    def set_probes(self, probes: Sequence[ProbeInfo], selected_serial: Optional[str] = None) -> None:
        self._probes = list(probes)
        selected = next((p for p in probes if selected_serial and p.serial == selected_serial), None)
        if selected is None and len(probes) == 1:
            selected = probes[0]
        if selected:
            self.kpi_probe_name.setText(selected.name)
            self.kpi_probe_serial.setText("Sê-ri: %s" % (selected.serial or "Chưa có số sê-ri"))
            self.kpi_probe_status.setText("ĐẦU DÒ SẴN SÀNG")
        else:
            self.kpi_probe_name.setText("Chọn ST-Link trên thanh chung" if probes else "Không tìm thấy ST-Link")
            self.kpi_probe_serial.setText("Sê-ri: —")
            self.kpi_probe_status.setText("Chưa chọn" if probes else "MẤT KẾT NỐI")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        for label in (self.val_dev_id, self.val_flash_size, self.val_wrp, self.val_rdp,
                      self.val_boot_prot, self.kpi_flash_size, self.kpi_prot_state,
                      self.kpi_voltage, self.kpi_rdp_level):
            label.setText("Chưa kiểm tra")
        self.kpi_mcu_name.setText("Chưa đọc MCU")
        self.kpi_mcu_arch.setText("Chưa đọc kiến trúc")
        self.kpi_volt_badge.setText("Chưa đánh giá")
        if info is None:
            return
        family = "STM32F407/417" if info.device_id & 0xFFF == SUPPORTED_DEVICE_ID else "MCU ID 0x%03X" % (info.device_id & 0xFFF)
        self.kpi_mcu_name.setText(family)
        self.val_dev_id.setText("0x%08X" % info.device_id)
        self.val_flash_size.setText("%d KB" % info.flash_kib)
        self.kpi_flash_size.setText("%d KiB" % info.flash_kib)
        self.kpi_mcu_arch.setText("ARM Cortex-M4" if info.device_id & 0xFFF == SUPPORTED_DEVICE_ID else "Chưa xác định kiến trúc")
        if info.target_voltage is not None:
            self.kpi_voltage.setText("%.2f V" % info.target_voltage)
            self.kpi_volt_badge.setText("Lần kiểm tra gần nhất")
        rdp = "Đã khóa đọc" if info.readout_protected else "Mức 0 / cho phép đọc"
        self.val_rdp.setText(rdp)
        self.kpi_rdp_level.setText("RDP: " + rdp)
        if info.protection_reported:
            protected = {0, 1, 2}.issubset(set(info.protected_sectors))
            self.val_wrp.setText("ĐÃ BẢO VỆ S0–S2" if protected else "CHƯA BẢO VỆ ĐỦ S0–S2")
            self.val_boot_prot.setText("Đã bảo vệ S0–S2" if protected else "Chưa bảo vệ đủ S0–S2")
            self.kpi_prot_state.setText("Đã kiểm tra" if protected and not info.readout_protected else "Không đạt")
        else:
            self.val_wrp.setText("Chưa kiểm tra")
        self.activity_log.append("[THÔNG TIN] Đã kiểm tra MCU: %s · %d KiB · %s" % (family, info.flash_kib, self.val_wrp.text()))

    def set_busy(self, busy: bool) -> None:
        self.btn_refresh.setEnabled(not busy)
        self.btn_doctor.setEnabled(not busy)
        self.btn_read_metadata.setEnabled(False)
        self.btn_export_evidence.setEnabled(False)


__all__ = ["DeviceView"]
