"""Settings & Workstation Environment View for B300 (v0.18).

Consolidates machine setup, theme switching, updater checks, and diagnostic support.
"""

from __future__ import annotations

from typing import Optional

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

from b300_core import __version__ as CORE_VERSION
from b300_version import __version__


class SettingsView(QWidget):
    """Clean configuration & environment preparation hub."""

    machine_setup_requested = Signal()
    toggle_theme_requested = Signal()
    check_updates_requested = Signal()
    export_support_bundle_requested = Signal()
    about_requested = Signal()
    release_notes_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsViewContainer")

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("settingsContent")
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(16, 12, 16, 14)
        self.layout.setSpacing(12)

        self._build_ui()
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        # Header
        header = QFrame()
        header.setObjectName("headerRibbon")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 8, 12, 8)
        h_layout.setSpacing(3)

        title = QLabel("SETTINGS · THIẾT LẬP & MÔI TRƯỜNG MÁY")
        title.setStyleSheet("font-size: 14px; font-weight: 800; color: #F8FAFC; letter-spacing: 0.6px;")
        h_layout.addWidget(title)

        desc = QLabel("Cài đặt môi trường máy tính làm việc: OpenOCD, ST-Link Driver, giao diện và cập nhật phần mềm.")
        desc.setStyleSheet("font-size: 11px; color: #94A3B8;")
        h_layout.addWidget(desc)
        self.layout.addWidget(header)

        # 1. Machine Setup Card
        setup_card = QFrame()
        setup_card.setObjectName("cardSurface")
        s_layout = QVBoxLayout(setup_card)
        s_layout.setContentsMargins(14, 12, 14, 12)
        s_layout.setSpacing(8)

        s_title = QLabel("1. MÔI TRƯỜNG & DRIVER MÁY TÍNH")
        s_title.setObjectName("eyebrowLabel")
        s_layout.addWidget(s_title)

        s_desc = QLabel(
            "Tự động phát hiện và cài đặt các thành phần còn thiếu: ST-Link USB Driver (WinUSB), "
            "gói runtime OpenOCD 0.12.0-7 offline và cấu hình SSH/udev."
        )
        s_desc.setStyleSheet("font-size: 11px; color: #94A3B8;")
        s_desc.setWordWrap(True)
        s_layout.addWidget(s_desc)

        s_btn_row = QHBoxLayout()
        self.btn_run_setup = QPushButton("⚙ CHẠY THUẬT TOÁN THIẾT LẬP MÁY (SETUP WIZARD)")
        self.btn_run_setup.setObjectName("primaryActionButton")
        self.btn_run_setup.setStyleSheet(
            "QPushButton { background: #0284C7; color: white; font-weight: 800; "
            "font-size: 12px; padding: 8px 18px; border-radius: 4px; }"
            "QPushButton:hover { background: #0369A1; }"
        )
        self.btn_run_setup.clicked.connect(self.machine_setup_requested.emit)
        s_btn_row.addWidget(self.btn_run_setup)
        s_btn_row.addStretch(1)
        s_layout.addLayout(s_btn_row)

        self.layout.addWidget(setup_card)

        # 2. Appearance & Preferences
        pref_card = QFrame()
        pref_card.setObjectName("cardSurface")
        p_layout = QVBoxLayout(pref_card)
        p_layout.setContentsMargins(14, 12, 14, 12)
        p_layout.setSpacing(8)

        p_title = QLabel("2. GIAO DIỆN & TÙY CHỌN")
        p_title.setObjectName("eyebrowLabel")
        p_layout.addWidget(p_title)

        p_row = QHBoxLayout()
        p_row.addWidget(QLabel("Chế độ giao diện (Dark / Light Theme):"))
        self.btn_toggle_theme = QPushButton("🌓 Đổi giao diện Sáng / Tối (Ctrl+T)")
        self.btn_toggle_theme.setObjectName("ghostButton")
        self.btn_toggle_theme.clicked.connect(self.toggle_theme_requested.emit)
        p_row.addWidget(self.btn_toggle_theme)
        p_row.addStretch(1)
        p_layout.addLayout(p_row)

        self.layout.addWidget(pref_card)

        # 3. Version & Updates
        upd_card = QFrame()
        upd_card.setObjectName("cardSurface")
        u_layout = QVBoxLayout(upd_card)
        u_layout.setContentsMargins(14, 12, 14, 12)
        u_layout.setSpacing(8)

        u_title = QLabel("3. PHIÊN BẢN & CẬP NHẬT")
        u_title.setObjectName("eyebrowLabel")
        u_layout.addWidget(u_title)

        u_grid = QGridLayout()
        u_grid.setHorizontalSpacing(14)
        u_grid.setVerticalSpacing(4)

        u_grid.addWidget(QLabel("Phiên bản GUI:"), 0, 0)
        lbl_ver = QLabel(f"v{__version__} (Simplified UX)")
        lbl_ver.setStyleSheet("font-weight: 700; color: #38BDF8;")
        u_grid.addWidget(lbl_ver, 0, 1)

        u_grid.addWidget(QLabel("Phiên bản Core:"), 0, 2)
        lbl_core = QLabel(f"v{CORE_VERSION}")
        lbl_core.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        u_grid.addWidget(lbl_core, 0, 3)

        u_grid.addWidget(QLabel("OpenOCD Profile:"), 1, 0)
        lbl_ocd = QLabel("0.12.0-7 (Strict Loopback)")
        lbl_ocd.setStyleSheet("color: #94A3B8;")
        u_grid.addWidget(lbl_ocd, 1, 1)

        u_grid.addWidget(QLabel("Kênh cập nhật:"), 1, 2)
        lbl_chan = QLabel("Official Signed Releases (Minisign)")
        lbl_chan.setStyleSheet("color: #10B981;")
        u_grid.addWidget(lbl_chan, 1, 3)

        u_layout.addLayout(u_grid)

        act_row = QHBoxLayout()
        self.btn_check_updates = QPushButton("🔄 Kiểm tra cập nhật")
        self.btn_check_updates.setObjectName("ghostButton")
        self.btn_check_updates.clicked.connect(self.check_updates_requested.emit)
        act_row.addWidget(self.btn_check_updates)

        self.btn_release_notes = QPushButton("📋 Ghi chú phiên bản")
        self.btn_release_notes.setObjectName("ghostButton")
        self.btn_release_notes.clicked.connect(self.release_notes_requested.emit)
        act_row.addWidget(self.btn_release_notes)

        self.btn_export_support = QPushButton("📦 Xuất gói chẩn đoán hỗ trợ")
        self.btn_export_support.setObjectName("ghostButton")
        self.btn_export_support.clicked.connect(self.export_support_bundle_requested.emit)
        act_row.addWidget(self.btn_export_support)

        self.btn_about = QPushButton("ℹ️ Giới thiệu")
        self.btn_about.setObjectName("ghostButton")
        self.btn_about.clicked.connect(self.about_requested.emit)
        act_row.addWidget(self.btn_about)

        act_row.addStretch(1)
        u_layout.addLayout(act_row)

        self.layout.addWidget(upd_card)
        self.layout.addStretch(1)


__all__ = ["SettingsView"]
