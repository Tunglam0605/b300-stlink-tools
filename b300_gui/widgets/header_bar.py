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
    QWidget,
)

from b300_core.models import ProbeInfo
from b300_core import __version__ as CORE_VERSION
from b300_gui.theme import ThemeManager


class HeaderBar(QFrame):
    """Top Slim Industrial Header with Brand, Mode Segmented Control, Probe Bar, and Utilities."""

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
        self._probes: List[ProbeInfo] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        # 1. Brand Label & Version
        self.brand_container = QFrame()
        brand_layout = QHBoxLayout(self.brand_container)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(6)

        self.brand_title = QLabel("B300 TOOLS")
        self.brand_title.setObjectName("headerBrandTitle")
        brand_layout.addWidget(self.brand_title)

        self.version_badge = QLabel(f"v{CORE_VERSION}")
        self.version_badge.setObjectName("eyebrowLabel")
        brand_layout.addWidget(self.version_badge)
        self.brand_container.setVisible(False)
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
        layout.addStretch(1)

        # 3. Active Probe Status & Selector
        self.probe_container = QFrame()
        probe_layout = QHBoxLayout(self.probe_container)
        probe_layout.setContentsMargins(0, 0, 0, 0)
        probe_layout.setSpacing(6)

        self.probe_combo = QComboBox()
        self.probe_combo.setMinimumWidth(210)
        self.probe_combo.setToolTip("Chọn ST-Link Probe đang kết nối")
        self.probe_combo.currentIndexChanged.connect(self._on_probe_combo_changed)
        probe_layout.addWidget(self.probe_combo)

        self.probe_refresh_btn = QPushButton("Quét lại")
        self.probe_refresh_btn.setObjectName("ghostButton")
        self.probe_refresh_btn.setToolTip("Quét lại danh sách ST-Link (F5)")
        self.probe_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.probe_refresh_btn.clicked.connect(self.probe_refresh_requested.emit)
        probe_layout.addWidget(self.probe_refresh_btn)

        layout.addWidget(self.probe_container)
        layout.addSpacing(6)

        # 4. Quick Utility Actions
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ghostButton")
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setToolTip("Chuyển đổi giao diện Sáng / Tối (Ctrl+T)")
        self.theme_btn.clicked.connect(self._on_theme_toggle_clicked)
        self._update_theme_icon()
        layout.addWidget(self.theme_btn)

        self.machine_setup_btn = QPushButton("Cài đặt máy")
        self.machine_setup_btn.setObjectName("ghostButton")
        self.machine_setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.machine_setup_btn.setToolTip("Thiết lập môi trường (ST-Link Driver, OpenOCD, OpenSSH)")
        self.machine_setup_btn.clicked.connect(self.machine_setup_requested.emit)
        layout.addWidget(self.machine_setup_btn)

        self.help_btn = QPushButton("Trợ giúp")
        self.help_btn.setObjectName("ghostButton")
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
        else:
            self.probe_combo.setEnabled(True)
            selected_idx = 0
            for i, p in enumerate(probes):
                label = f"{p.name} [{p.serial or 'No Serial'}]"
                self.probe_combo.addItem(label, p.serial)
                if selected_serial and p.serial == selected_serial:
                    selected_idx = i
            self.probe_combo.blockSignals(False)
            self.probe_combo.setCurrentIndex(selected_idx)
            return
        self.probe_combo.blockSignals(False)

    def _on_probe_combo_changed(self, index: int) -> None:
        if 0 <= index < len(self._probes):
            self.probe_selected.emit(index)
