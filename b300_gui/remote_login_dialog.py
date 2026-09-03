"""Unified Single Remote Login Dialog for Client Mode."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class RemoteLoginDialog(QDialog):
    """Single login dialog for SSH Gateway connection.

    Guarantees:
    - Password is typed into a masked QLineEdit.
    - Password is NEVER logged or passed to status labels.
    - No external CMD or PowerShell terminal is spawned.
    - Password field is destroyed / closed upon successful connection.
    """

    connect_requested = Signal(str, str, str, int, bool)  # host, user, password, port, remember

    def __init__(
        self,
        default_host: str = "",
        default_user: str = "",
        default_port: int = 22,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Client Connection — Remote Debug")
        self.setObjectName("remoteLoginDialog")
        self.resize(380, 240)
        self._build_ui(default_host, default_user, default_port)

    def _build_ui(self, default_host: str, default_user: str, default_port: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # Header title
        title = QLabel("CLIENT CONNECTION")
        title.setObjectName("remoteLoginTitle")
        title.setStyleSheet("font-size: 14px; font-weight: 800; color: #F1F5F9; letter-spacing: 0.5px;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.host_input = QLineEdit(default_host)
        self.host_input.setObjectName("loginGatewayHost")
        self.host_input.setPlaceholderText("192.168.1.145")
        form.addRow("Gateway:", self.host_input)

        self.user_input = QLineEdit(default_user)
        self.user_input.setObjectName("loginGatewayUser")
        self.user_input.setPlaceholderText("Admin")
        form.addRow("User:", self.user_input)

        self.password_input = QLineEdit()
        self.password_input.setObjectName("loginGatewayPassword")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("••••••••••••")
        form.addRow("Password:", self.password_input)

        self.port_input = QSpinBox()
        self.port_input.setObjectName("loginGatewayPort")
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(default_port or 22)
        form.addRow("Port:", self.port_input)

        layout.addLayout(form)

        self.remember_checkbox = QCheckBox("Remember on this PC")
        self.remember_checkbox.setObjectName("loginRememberCheckbox")
        self.remember_checkbox.setChecked(True)
        layout.addWidget(self.remember_checkbox)

        # Action buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)
        btn_box.addStretch(1)

        self.btn_cancel = QPushButton("Hủy")
        self.btn_cancel.setObjectName("loginCancelBtn")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setObjectName("loginConnectBtn")
        self.btn_connect.setStyleSheet("font-weight: 700; padding: 6px 18px;")
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        btn_box.addWidget(self.btn_connect)

        layout.addLayout(btn_box)

    def _on_connect_clicked(self) -> None:
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        pwd = self.password_input.text()
        port = self.port_input.value()
        remember = self.remember_checkbox.isChecked()
        self.connect_requested.emit(host, user, pwd, port, remember)
        self.accept()

    def get_credentials(self) -> tuple[str, str, str, int, bool]:
        """Retrieve credentials and immediately clear memory password input."""
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        pwd = self.password_input.text()
        port = self.port_input.value()
        remember = self.remember_checkbox.isChecked()
        self.password_input.clear()
        return host, user, pwd, port, remember
