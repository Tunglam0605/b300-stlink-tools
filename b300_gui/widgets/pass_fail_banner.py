"""High-visibility result HUD banner for Operator and R&D flashing outcomes."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PassFailBanner(QFrame):
    """Large high-contrast status banner displaying provisioning outcome."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("passFailBanner")
        self.setProperty("variant", "info")
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.icon_label = QLabel("PASS")
        self.icon_label.setFixedSize(48, 26)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setObjectName("statusPillSuccess")
        layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.title_label = QLabel("NẠP FIRMWARE THÀNH CÔNG")
        self.title_label.setObjectName("pageContextTitle")
        text_layout.addWidget(self.title_label)

        self.detail_label = QLabel("Đã ghi metadata STLM + VERIFIED và khởi động ứng dụng an toàn.")
        self.detail_label.setObjectName("pageContextSubtitle")
        self.detail_label.setWordWrap(True)
        text_layout.addWidget(self.detail_label)

        layout.addLayout(text_layout, 1)

        self.dismiss_btn = QPushButton("Đóng")
        self.dismiss_btn.setObjectName("ghostButton")
        self.dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_btn.clicked.connect(self.hide)
        layout.addWidget(self.dismiss_btn)

    def show_pass(self, title: str = "NẠP FIRMWARE THÀNH CÔNG (PASS)", detail: str = "", duration_sec: Optional[float] = None) -> None:
        self.icon_label.setText("PASS")
        self.icon_label.setObjectName("statusPillSuccess")
        self.title_label.setText(title)
        time_info = f" · Hoàn thành trong {duration_sec:.1f}s" if duration_sec else ""
        self.detail_label.setText(f"{detail}{time_info}")
        self.setProperty("variant", "pass")
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon_label.style().unpolish(self.icon_label)
        self.icon_label.style().polish(self.icon_label)
        self.show()

    def show_fail(self, title: str = "NẠP FIRMWARE THẤT BẠI (FAIL)", detail: str = "", next_action: str = "") -> None:
        self.icon_label.setText("FAIL")
        self.icon_label.setObjectName("statusPillDanger")
        self.title_label.setText(title)
        action_text = f"\nKhắc phục: {next_action}" if next_action else ""
        self.detail_label.setText(f"{detail}{action_text}")
        self.setProperty("variant", "fail")
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon_label.style().unpolish(self.icon_label)
        self.icon_label.style().polish(self.icon_label)
        self.show()

    def show_info(self, title: str, detail: str = "") -> None:
        self.icon_label.setText("INFO")
        self.icon_label.setObjectName("statusPillNeutral")
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.setProperty("variant", "info")
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon_label.style().unpolish(self.icon_label)
        self.icon_label.style().polish(self.icon_label)
        self.show()
