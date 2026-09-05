"""Modern Industrial Top Header Bar with Mode Switcher and Live Probe Status."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import ProbeInfo
from b300_core import __version__ as CORE_VERSION
from b300_gui.theme import ThemeManager


class HeaderBar(QFrame):
    """Top Slim Industrial Header with Brand, Mode Segmented Control, Probe Bar, and Utilities."""

    connection_mode_changed = Signal(str)
    mode_changed = Signal(str)
    probe_refresh_requested = Signal()
    probe_selected = Signal(int)
    theme_toggled = Signal(str)
    machine_setup_requested = Signal()
    help_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(50)
        self._current_mode = "rnd"
        self._current_connection_mode = "local"
        self._probes: List[ProbeInfo] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        # 1. Brand Label & Subtitle
        self.brand_container = QFrame()
        brand_layout = QVBoxLayout(self.brand_container)
        brand_layout.setContentsMargins(4, 0, 8, 0)
        brand_layout.setSpacing(1)

        self.brand_title = QLabel("B300 ST-Link Tools")
        self.brand_title.setObjectName("headerBrandTitle")
        self.brand_title.setStyleSheet("font-size: 14px; font-weight: 800; color: #F8FAFC; letter-spacing: 0.3px;")
        brand_layout.addWidget(self.brand_title)

        self.brand_subtitle = QLabel("STM32 • Nạp • Gỡ lỗi • Giám sát")
        self.brand_subtitle.setStyleSheet("font-size: 10px; color: #64748B; font-weight: 600;")
        brand_layout.addWidget(self.brand_subtitle)

        self.version_badge = QLabel(f"v{CORE_VERSION}")
        self.version_badge.setObjectName("eyebrowLabel")
        self.version_badge.hide()

        self.brand_container.setVisible(True)
        layout.addWidget(self.brand_container)

        layout.addSpacing(8)

        # 2. Clean Segmented Mode Switcher (Zero childish emojis)
        self.segmented_control = QFrame()
        self.segmented_control.setObjectName("segmentedControl")
        seg_layout = QHBoxLayout(self.segmented_control)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)

        self.btn_operator = QPushButton("Vận hành Sản xuất")
        self.btn_operator.setObjectName("segmentBtn")
        self.btn_operator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_operator.setProperty("active", "false")
        self.btn_operator.clicked.connect(lambda: self.set_mode("operator"))
        seg_layout.addWidget(self.btn_operator)

        self.btn_rnd = QPushButton("Kỹ sư R&D")
        self.btn_rnd.setObjectName("segmentBtn")
        self.btn_rnd.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rnd.setProperty("active", "true")
        self.btn_rnd.clicked.connect(lambda: self.set_mode("rnd"))
        seg_layout.addWidget(self.btn_rnd)

        layout.addWidget(self.segmented_control)

        # 2b. Global Connection Mode Switcher (Local / Gateway / Client) pinned at the top
        self.conn_mode_control = QFrame()
        self.conn_mode_control.setObjectName("segmentedControl")
        conn_seg_layout = QHBoxLayout(self.conn_mode_control)
        conn_seg_layout.setContentsMargins(2, 2, 2, 2)
        conn_seg_layout.setSpacing(2)

        self.btn_conn_local = QPushButton("Cục bộ")
        self.btn_conn_local.setObjectName("segmentBtn")
        self.btn_conn_local.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_conn_local.setProperty("active", "true")
        self.btn_conn_local.clicked.connect(lambda: self.set_connection_mode("local"))
        conn_seg_layout.addWidget(self.btn_conn_local)

        self.btn_conn_gateway = QPushButton("Máy Gateway")
        self.btn_conn_gateway.setObjectName("segmentBtn")
        self.btn_conn_gateway.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_conn_gateway.setProperty("active", "false")
        self.btn_conn_gateway.clicked.connect(lambda: self.set_connection_mode("gateway"))
        conn_seg_layout.addWidget(self.btn_conn_gateway)

        self.btn_conn_client = QPushButton("Máy khách")
        self.btn_conn_client.setObjectName("segmentBtn")
        self.btn_conn_client.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_conn_client.setProperty("active", "false")
        self.btn_conn_client.clicked.connect(lambda: self.set_connection_mode("client"))
        conn_seg_layout.addWidget(self.btn_conn_client)

        layout.addWidget(self.conn_mode_control)
        layout.addStretch(1)

        # 3. Active Probe Status & Selector Pill
        self.probe_container = QFrame()
        self.probe_container.setObjectName("nestedCard")
        probe_layout = QHBoxLayout(self.probe_container)
        probe_layout.setContentsMargins(6, 3, 6, 3)
        probe_layout.setSpacing(6)

        lbl_probe_icon = QLabel("🔌")
        lbl_probe_icon.setStyleSheet("font-size: 13px;")
        probe_layout.addWidget(lbl_probe_icon)

        self.probe_combo = QComboBox()
        self.probe_combo.setMinimumWidth(150)
        self.probe_combo.setMaximumWidth(220)
        self.probe_combo.setToolTip("Chọn ST-Link đang kết nối")
        self.probe_combo.currentIndexChanged.connect(self._on_probe_combo_changed)
        probe_layout.addWidget(self.probe_combo)

        self.probe_status_badge = QLabel("● Đã kết nối")
        self.probe_status_badge.setStyleSheet("color: #10B981; font-weight: 700; font-size: 11px; padding: 0 4px;")
        probe_layout.addWidget(self.probe_status_badge)

        self.probe_refresh_btn = QPushButton("⟳")
        self.probe_refresh_btn.setObjectName("ghostButton")
        self.probe_refresh_btn.setToolTip("Quét lại danh sách ST-Link (F5)")
        self.probe_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.probe_refresh_btn.setFixedWidth(28)
        self.probe_refresh_btn.clicked.connect(self.probe_refresh_requested.emit)
        probe_layout.addWidget(self.probe_refresh_btn)
        layout.addWidget(self.probe_container)

        # 3b. Target MCU Chip Card
        self.target_mcu_badge = QFrame()
        self.target_mcu_badge.setObjectName("nestedCard")
        mcu_layout = QHBoxLayout(self.target_mcu_badge)
        mcu_layout.setContentsMargins(8, 3, 8, 3)
        mcu_layout.setSpacing(6)

        lbl_chip_icon = QLabel("🔲")
        lbl_chip_icon.setStyleSheet("font-size: 13px;")
        mcu_layout.addWidget(lbl_chip_icon)

        mcu_text_col = QVBoxLayout()
        mcu_text_col.setContentsMargins(0, 0, 0, 0)
        mcu_text_col.setSpacing(0)
        self.lbl_mcu_title = QLabel("MCU đích")
        self.lbl_mcu_title.setStyleSheet("font-size: 9.5px; color: #64748B; font-weight: 700; text-transform: uppercase;")
        mcu_text_col.addWidget(self.lbl_mcu_title)
        self.lbl_mcu_val = QLabel("Chưa đọc MCU đích")
        self.lbl_mcu_val.setStyleSheet("font-size: 11px; color: #F8FAFC; font-weight: 800;")
        mcu_text_col.addWidget(self.lbl_mcu_val)
        mcu_layout.addLayout(mcu_text_col)
        layout.addWidget(self.target_mcu_badge)

        layout.addSpacing(6)

        # 4. Quick Utility Actions
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ghostButton")
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setToolTip("Chuyển đổi giao diện Sáng / Tối (Ctrl+T)")
        self.theme_btn.clicked.connect(self._on_theme_toggle_clicked)
        self._update_theme_icon()
        self.theme_btn.hide()
        layout.addWidget(self.theme_btn)

        self.btn_open_project = QPushButton("📁 Mở dự án")
        self.btn_open_project.setObjectName("ghostButton")
        self.btn_open_project.setFixedHeight(28)
        layout.addWidget(self.btn_open_project)

        self.btn_history = QPushButton("🕒 Lịch sử")
        self.btn_history.setObjectName("ghostButton")
        self.btn_history.setFixedHeight(28)
        layout.addWidget(self.btn_history)

        self.machine_setup_btn = QPushButton("⚙ Cài đặt")
        self.machine_setup_btn.setObjectName("ghostButton")
        self.machine_setup_btn.setFixedHeight(28)
        self.machine_setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.machine_setup_btn.setToolTip("Thiết lập môi trường và cấu hình hệ thống")
        self.machine_setup_btn.clicked.connect(self.machine_setup_requested.emit)
        layout.addWidget(self.machine_setup_btn)

        self.help_btn = QPushButton("❓ Trợ giúp")
        self.help_btn.setObjectName("ghostButton")
        self.help_btn.setFixedHeight(28)
        self.help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_btn.setToolTip("Trợ giúp & Thông tin B300 Tools")
        self.help_btn.clicked.connect(self.help_requested.emit)
        layout.addWidget(self.help_btn)

        ThemeManager.instance().theme_changed.connect(lambda _: self._update_theme_icon())

    def _update_theme_icon(self) -> None:
        is_dark = ThemeManager.instance().is_dark
        self.theme_btn.setText("Giao diện: Tối" if is_dark else "Giao diện: Sáng")

    def _on_theme_toggle_clicked(self) -> None:
        new_mode = ThemeManager.instance().toggle_theme()
        self._update_theme_icon()
        self.theme_toggled.emit(new_mode)

    def set_mode(self, mode: str) -> None:
        if mode not in ("operator", "rnd"):
            return
        if self._current_mode != mode:
            self._current_mode = mode
            is_op = mode == "operator"
            self.btn_operator.setProperty("active", "true" if is_op else "false")
            self.btn_rnd.setProperty("active", "false" if is_op else "true")
            self.btn_operator.style().unpolish(self.btn_operator)
            self.btn_operator.style().polish(self.btn_operator)
            self.btn_rnd.style().unpolish(self.btn_rnd)
            self.btn_rnd.style().polish(self.btn_rnd)
            self.mode_changed.emit(mode)

    def set_connection_mode(self, mode: str, notify: bool = True) -> None:
        if mode not in ("local", "gateway", "client"):
            return
        self._current_connection_mode = mode
        self.btn_conn_local.setProperty("active", "true" if mode == "local" else "false")
        self.btn_conn_gateway.setProperty("active", "true" if mode == "gateway" else "false")
        self.btn_conn_client.setProperty("active", "true" if mode == "client" else "false")
        for btn in (self.btn_conn_local, self.btn_conn_gateway, self.btn_conn_client):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if notify:
            self.connection_mode_changed.emit(mode)

    def current_connection_mode(self) -> str:
        return self._current_connection_mode

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def set_probes(self, probes: List[ProbeInfo], selected_serial: Optional[str] = None) -> None:
        self._probes = list(probes)
        self.probe_combo.blockSignals(True)
        self.probe_combo.clear()
        if not probes:
            self.probe_combo.addItem("Chưa kết nối ST-Link")
            self.probe_combo.setEnabled(False)
            if hasattr(self, "probe_status_badge"):
                self.probe_status_badge.setText("● Chưa kết nối")
                self.probe_status_badge.setStyleSheet("color: #EF4444; font-weight: 700; font-size: 11px;")
        else:
            self.probe_combo.setEnabled(True)
            selected_idx = 0 if len(probes) == 1 else -1
            self.probe_combo.setPlaceholderText("Chọn ST-Link…")
            for i, p in enumerate(probes):
                label = "%s · %s" % (p.name, p.serial or "Không có số sê-ri")
                self.probe_combo.addItem(label, p.serial)
                if selected_serial and p.serial == selected_serial:
                    selected_idx = i
            self.probe_combo.setCurrentIndex(selected_idx)
            if hasattr(self, "probe_status_badge"):
                active_serial = (probes[selected_idx].serial or "Không có số sê-ri") if selected_idx >= 0 else ""
                self.probe_status_badge.setText("● Đã kết nối" if selected_idx >= 0 else "● Chờ chọn ST-Link")
                self.probe_status_badge.setToolTip(active_serial)
                self.probe_status_badge.setStyleSheet("color: #10B981; font-weight: 700; font-size: 11px;")
        self.probe_combo.blockSignals(False)

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        if hasattr(self, "lbl_mcu_val"):
            if info is None:
                self.lbl_mcu_val.setText("Chưa đọc MCU đích")
            else:
                self.lbl_mcu_val.setText("STM32F407/417" if info.device_id & 0xFFF == 0x413
                                         else "Mã MCU 0x%03X" % (info.device_id & 0xFFF))

    def _on_probe_combo_changed(self, index: int) -> None:
        if 0 <= index < len(self._probes):
            if hasattr(self, "probe_status_badge"):
                serial = self._probes[index].serial or "Sẵn sàng"
                self.probe_status_badge.setText("● Đã kết nối")
                self.probe_status_badge.setToolTip(serial)
            self.probe_selected.emit(index)
