"""Password-only login for a preselected saved Gateway profile."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from b300_core.gateway_profiles import GatewayProfile


class GatewayLoginDialog(QDialog):
    def __init__(self, profile: GatewayProfile, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.profile = profile.validate()
        self.setWindowTitle("Kết nối Gateway")
        self.setObjectName("gatewayLoginDialog")
        self.resize(420, 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel(self.profile.name)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        endpoint = QLabel(self.profile.display_endpoint)
        endpoint.setObjectName("mutedLabel")
        layout.addWidget(endpoint)
        note = QLabel("Mật khẩu chỉ được giữ trong RAM của phiên B300 hiện tại và bị xóa khi đóng ứng dụng.")
        note.setWordWrap(True)
        note.setObjectName("pageSubtitle")
        layout.addWidget(note)
        self.password_input = QLineEdit()
        self.password_input.setObjectName("gatewaySessionPassword")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Mật khẩu SSH")
        self.password_input.returnPressed.connect(self.accept)
        layout.addWidget(self.password_input)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        self.connect_button = QPushButton("Kết nối")
        self.connect_button.setObjectName("primaryActionButton")
        self.connect_button.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.connect_button)
        layout.addLayout(row)

    def password(self) -> str:
        return self.password_input.text()

    def done(self, result: int) -> None:
        if result != QDialog.DialogCode.Accepted:
            self.password_input.clear()
        super().done(result)


__all__ = ["GatewayLoginDialog"]
