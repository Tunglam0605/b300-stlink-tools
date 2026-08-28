"""Modern confirmation dialog for B300 ST-Link Tools following Tung Lam Design System."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import FlashPlan


class ConfirmFlashDialog(QDialog):
    """Rich visual confirmation modal for flashing STM32F407 Application."""

    def __init__(self, plan: FlashPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("Xác nhận nạp Application")
        self.setMinimumWidth(480)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        # Header with visual badge
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        icon_badge = QLabel("⚡")
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_badge.setStyleSheet(
            "font-size: 22px; background: #F0F9FF; border: 1px solid #BAE6FD; "
            "border-radius: 10px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px;"
        )
        header_row.addWidget(icon_badge)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel("Xác nhận nạp Application")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #0F172A;")
        subtitle = QLabel("Kiểm tra thông tin probe và checksum firmware trước khi ghi.")
        subtitle.setStyleSheet("font-size: 12px; color: #64748B;")
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header_row.addLayout(header_text, 1)
        root.addLayout(header_row)

        # Firmware & Probe Metadata Card
        card = QFrame()
        card.setStyleSheet(
            "background-color: #F8FAFC; border: 1px solid #E2E8F0; "
            "border-radius: 8px; padding: 10px;"
        )
        card_layout = QFormLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)

        probe_text = self.plan.probe.serial or "Tự động chọn (ST-Link duy nhất)"
        probe_label = QLabel(probe_text)
        probe_label.setStyleSheet("color: #0369A1; font-weight: 600; font-size: 12px;")
        card_layout.addRow("ST-Link Probe:", probe_label)

        fw_label = QLabel(self.plan.image.path.name)
        fw_label.setStyleSheet("color: #0F172A; font-weight: 700; font-size: 13px;")
        card_layout.addRow("File Firmware:", fw_label)

        sha = self.plan.image.sha256
        sha_label = QLabel(sha)
        sha_label.setWordWrap(True)
        sha_label.setTextInteractionFlags(
            sha_label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        sha_label.setStyleSheet(
            "color: #475569; font-family: 'Cascadia Code', 'Consolas', monospace; "
            "font-size: 10px; font-weight: 600; background: #FFFFFF; border: 1px solid #CBD5E1; "
            "border-radius: 4px; padding: 4px 6px;"
        )
        card_layout.addRow("SHA-256:", sha_label)
        root.addWidget(card)

        # Safety Transaction Notice Box
        notice = QFrame()
        notice.setStyleSheet(
            "background-color: #F0FDF4; border: 1px solid #BBF7D0; "
            "border-radius: 8px; padding: 10px 12px;"
        )
        notice_layout = QVBoxLayout(notice)
        notice_layout.setContentsMargins(4, 4, 4, 4)
        notice_layout.setSpacing(4)

        bldr_rule = QLabel("🛡️ Sector 0–2 (Bootloader): Tuyệt đối giữ nguyên và bảo vệ WRP")
        bldr_rule.setStyleSheet("color: #166534; font-weight: 600; font-size: 12px;")
        app_rule = QLabel("⚡ Sector 3–7 (Application & OTA): Xóa sạch, nạp mới và verify chuẩn")
        app_rule.setStyleSheet("color: #166534; font-size: 12px;")
        notice_layout.addWidget(bldr_rule)
        notice_layout.addWidget(app_rule)
        root.addWidget(notice)

        # Action Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch(1)

        self.cancel_button = QPushButton("Hủy bỏ")
        self.cancel_button.setStyleSheet(
            "background-color: #F1F5F9; color: #475569; border: 1px solid #CBD5E1; "
            "border-radius: 6px; padding: 8px 18px; font-weight: 600; font-size: 12px;"
        )
        self.cancel_button.clicked.connect(self.reject)

        self.confirm_button = QPushButton("🚀 Bắt đầu nạp firmware")
        self.confirm_button.setDefault(True)
        self.confirm_button.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10B981); "
            "color: #FFFFFF; border: none; border-radius: 6px; padding: 8px 20px; font-weight: 700; font-size: 12px; } "
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #059669); }"
        )
        self.confirm_button.clicked.connect(self.accept)

        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.confirm_button)
        root.addLayout(button_row)
