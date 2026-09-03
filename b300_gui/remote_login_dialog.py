"""Single embedded-SSH login dialog for Client mode."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class RemoteLoginDialog(QDialog):
    """One Client authentication surface; frontend never persists password text."""

    login_requested = Signal(str, str, str, int, bool)
    connect_requested = Signal(str, str, str, int, bool)  # compatibility alias

    def __init__(
        self,
        default_host: str = "",
        default_user: str = "",
        default_port: int = 22,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("B300 Client Login")
        self.setObjectName("remoteLoginDialog")
        self.resize(380, 238)
        self._is_connecting = False
        self._has_remembered = False
        self._build_ui(default_host, default_user, default_port)

    def _build_ui(self, default_host: str, default_user: str, default_port: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("SSH CLIENT")
        title.setObjectName("remoteLoginTitle")
        title.setStyleSheet(
            "font-size: 13px; font-weight: 900; color: #F1F5F9; letter-spacing: 0.8px;"
        )
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.host_input = QLineEdit(default_host)
        self.host_input.setObjectName("loginGatewayHost")
        self.host_input.setPlaceholderText("192.168.1.145")
        form.addRow("GATEWAY", self.host_input)

        self.user_input = QLineEdit(default_user)
        self.user_input.setObjectName("loginGatewayUser")
        self.user_input.setPlaceholderText("Admin")
        form.addRow("USER", self.user_input)

        self.password_input = QLineEdit()
        self.password_input.setObjectName("loginGatewayPassword")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("••••••••••••")
        form.addRow("PASSWORD", self.password_input)

        self.port_input = QSpinBox()
        self.port_input.setObjectName("loginGatewayPort")
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(default_port or 22)
        form.addRow("PORT", self.port_input)
        layout.addLayout(form)

        self.remember_checkbox = QCheckBox("SAVE LOCAL")
        self.remember_checkbox.setObjectName("loginRememberCheckbox")
        self.remember_checkbox.setChecked(True)
        self.remember_checkbox.setToolTip("Lưu credential trên máy này; password không vào profile/log.")
        layout.addWidget(self.remember_checkbox)

        self.status_banner = QLabel("")
        self.status_banner.setObjectName("loginStatusBanner")
        self.status_banner.setWordWrap(False)
        self.status_banner.setVisible(False)
        layout.addWidget(self.status_banner)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)
        btn_box.addStretch(1)

        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setObjectName("loginCancelBtn")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setObjectName("loginConnectBtn")
        self.btn_connect.setStyleSheet("font-weight: 800; padding: 6px 18px;")
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        btn_box.addWidget(self.btn_connect)
        layout.addLayout(btn_box)

    def set_has_remembered_credential(self, has_remembered: bool) -> None:
        self._has_remembered = has_remembered
        if has_remembered:
            self.password_input.setPlaceholderText("Saved on this PC")
            self.remember_checkbox.setChecked(True)

    def set_connecting(self, connecting: bool = True) -> None:
        self._is_connecting = connecting
        self.btn_connect.setEnabled(not connecting)
        self.host_input.setEnabled(not connecting)
        self.user_input.setEnabled(not connecting)
        self.password_input.setEnabled(not connecting)
        self.port_input.setEnabled(not connecting)
        self.remember_checkbox.setEnabled(not connecting)
        if connecting:
            self.btn_connect.setText("CONNECTING…")
            self.status_banner.setText("SSH · CONNECTING")
            self.status_banner.setStyleSheet(
                "color: #38BDF8; font-size: 10px; font-family: monospace; font-weight: 700;"
            )
            self.status_banner.setVisible(True)
        else:
            self.btn_connect.setText("CONNECT")

    def set_login_success(self) -> None:
        self.status_banner.setText("SSH ● CONNECTED")
        self.status_banner.setStyleSheet(
            "color: #10B981; font-size: 10px; font-family: monospace; font-weight: 800;"
        )
        self.status_banner.setVisible(True)
        self.password_input.clear()
        self.accept()

    def set_login_error(self, error_message: str) -> None:
        self.set_connecting(False)
        clean_msg = error_message.strip().split("\n")[0] if error_message else "SSH authentication failed."
        self.status_banner.setText("SSH ✕ %s" % clean_msg)
        self.status_banner.setStyleSheet(
            "color: #EF4444; font-size: 10px; font-family: monospace; font-weight: 700;"
        )
        self.status_banner.setVisible(True)
        self.password_input.setFocus()
        self.password_input.selectAll()

    def _on_connect_clicked(self) -> None:
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        password = self.password_input.text()
        port = self.port_input.value()
        remember = self.remember_checkbox.isChecked()
        self.set_connecting(True)
        self.login_requested.emit(host, user, password, port, remember)
        self.connect_requested.emit(host, user, password, port, remember)

    def reject(self) -> None:
        self.password_input.clear()
        super().reject()

    def get_credentials(self) -> tuple[str, str, str, int, bool]:
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        password = self.password_input.text()
        port = self.port_input.value()
        remember = self.remember_checkbox.isChecked()
        self.password_input.clear()
        return host, user, password, port, remember
