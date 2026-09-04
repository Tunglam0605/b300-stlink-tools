"""Compact mode-specific setup panel for the B300 Debug Workstation."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .collapsible_card import CollapsibleCard
from .remote_login_dialog import RemoteLoginDialog


class DebugConnectionPanel(QFrame):
    """Mode-specific setup shown only after LOCAL/GATEWAY/CLIENT is selected."""

    open_gateway_requested = Signal()
    client_login_requested = Signal(str, str, str, int, bool)
    change_mode_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("headerRibbon")
        self.login_dialog: Optional[RemoteLoginDialog] = None
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(8)

        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(0, 0, 0, 0)
        nav_bar.setSpacing(8)

        self.btn_change_mode = QPushButton("← KIỂU KẾT NỐI")
        self.btn_change_mode.setObjectName("ghostButton")
        self.btn_change_mode.setToolTip("Chọn lại LOCAL / GATEWAY / CLIENT")
        self.btn_change_mode.clicked.connect(self.change_mode_requested.emit)
        nav_bar.addWidget(self.btn_change_mode)

        self.mode_title_label = QLabel("LOCAL · TRỰC TIẾP")
        self.mode_title_label.setObjectName("debugModeActiveTitle")
        self.mode_title_label.setStyleSheet(
            "font-size: 12px; font-weight: 800; color: #38BDF8; letter-spacing: 0.5px;"
        )
        nav_bar.addWidget(self.mode_title_label)
        nav_bar.addStretch(1)

        self.status_label = QLabel("OFFLINE")
        self.status_label.setObjectName("debugStateBadge")
        self.status_label.setProperty("state", "stopped")
        self.status_label.setWordWrap(False)
        self.status_label.setMinimumWidth(104)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_bar.addWidget(self.status_label)
        main_layout.addLayout(nav_bar)

        # Hidden compatibility selector. Production enters through Mode-First.
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("debugModeSelector")
        for label, value in (
            ("Auto", "auto"),
            ("Local", "local"),
            ("Gateway", "gateway"),
            ("Client", "client"),
        ):
            self.mode_combo.addItem(label, value)
        self.mode_combo.currentIndexChanged.connect(self._on_combo_mode_changed)
        self.mode_combo.setVisible(False)
        main_layout.addWidget(self.mode_combo)

        conn_bar = QGridLayout()
        conn_bar.setHorizontalSpacing(8)
        conn_bar.setVerticalSpacing(5)
        conn_bar.setColumnStretch(1, 1)

        lbl_target = QLabel("TARGET")
        lbl_target.setObjectName("fieldLabel")
        conn_bar.addWidget(lbl_target, 0, 0)

        self.probe_display = QLabel("ST-Link · Auto")
        self.probe_display.setObjectName("pageContextTitle")
        self.probe_display.setWordWrap(False)
        self.probe_display.setMinimumWidth(0)
        self.probe_display.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        conn_bar.addWidget(self.probe_display, 0, 1)

        self.btn_open_gateway = QPushButton("CẤU HÌNH GATEWAY")
        self.btn_open_gateway.setObjectName("ghostButton")
        self.btn_open_gateway.setToolTip(
            "Thiết lập máy này làm Gateway: OpenSSH, OpenOCD và trạng thái kết nối"
        )
        self.btn_open_gateway.clicked.connect(self.open_gateway_requested.emit)
        conn_bar.addWidget(self.btn_open_gateway, 0, 2)

        self.gateway_actions = QWidget()
        gateway_actions_layout = QHBoxLayout(self.gateway_actions)
        gateway_actions_layout.setContentsMargins(0, 0, 0, 0)
        gateway_actions_layout.setSpacing(6)

        self.remote_server_button = QPushButton("BẮT ĐẦU GATEWAY")
        self.remote_server_button.setObjectName("debugRemoteServerButton")
        self.remote_server_button.setToolTip("Khởi động OpenOCD loopback cho Client từ xa")
        gateway_actions_layout.addWidget(self.remote_server_button)

        self.gateway_stop_button = QPushButton("DỪNG GATEWAY")
        self.gateway_stop_button.setObjectName("debugGatewayStopButton")
        self.gateway_stop_button.setEnabled(False)
        gateway_actions_layout.addWidget(self.gateway_stop_button)

        self.remote_kit_button = QPushButton("VS Code Kit…")
        self.remote_kit_button.setObjectName("debugRemoteKitButton")
        self.remote_kit_button.setToolTip("Tạo cấu hình Cortex-Debug/VS Code cho Gateway")
        gateway_actions_layout.addWidget(self.remote_kit_button)
        gateway_actions_layout.addStretch(1)
        self.gateway_actions.setVisible(False)
        conn_bar.addWidget(self.gateway_actions, 1, 0, 1, 3)
        main_layout.addLayout(conn_bar)

        self.symbols_box = QWidget()
        symbols_layout = QHBoxLayout(self.symbols_box)
        symbols_layout.setContentsMargins(0, 0, 0, 0)
        symbols_layout.setSpacing(6)

        lbl_sym = QLabel("AXF/ELF")
        lbl_sym.setObjectName("fieldLabel")
        symbols_layout.addWidget(lbl_sym)

        self.symbol_path = QLineEdit()
        self.symbol_path.setObjectName("debugSymbolPath")
        self.symbol_path.setPlaceholderText("Debug symbols")
        self.symbol_path.setMinimumWidth(0)
        symbols_layout.addWidget(self.symbol_path, 1)

        # Keep established file labels because DebugTab remains a compatibility API.
        self.symbol_browse_button = QPushButton("Chọn file…")
        self.symbol_browse_button.setObjectName("ghostButton")
        symbols_layout.addWidget(self.symbol_browse_button)

        self.symbol_auto_button = QPushButton("AUTO MATCH")
        self.symbol_auto_button.setObjectName("ghostButton")
        self.symbol_auto_button.setToolTip("Tìm AXF/ELF khớp Application Flash")
        symbols_layout.addWidget(self.symbol_auto_button)
        main_layout.addWidget(self.symbols_box)

        self.role_summary = QLabel("")
        self.role_summary.setVisible(False)
        main_layout.addWidget(self.role_summary)

        # Client has ONE visible authentication entry. Hidden endpoint widgets retain
        # the settings/backend contract without duplicating credentials UI.
        self.client_box = QWidget()
        client_layout = QHBoxLayout(self.client_box)
        client_layout.setContentsMargins(0, 0, 0, 0)
        client_layout.setSpacing(8)

        client_label = QLabel("KẾT NỐI TỪ XA")
        client_label.setObjectName("fieldLabel")
        client_layout.addWidget(client_label)

        self.btn_open_login_dialog = QPushButton("ĐĂNG NHẬP SSH")
        self.btn_open_login_dialog.setObjectName("debugClientLoginBtn")
        self.btn_open_login_dialog.setToolTip("Gateway / User / Password / Port · chỉ nhập tại đây")
        self.btn_open_login_dialog.clicked.connect(self._open_login_dialog)
        client_layout.addWidget(self.btn_open_login_dialog)
        client_layout.addStretch(1)

        self.client_host = QLineEdit()
        self.client_host.setObjectName("debugClientHost")
        self.client_host.setVisible(False)
        self.client_user = QLineEdit()
        self.client_user.setObjectName("debugClientUser")
        self.client_user.setVisible(False)
        self.client_ssh_port = QSpinBox()
        self.client_ssh_port.setObjectName("debugClientSshPort")
        self.client_ssh_port.setRange(1, 65535)
        self.client_ssh_port.setValue(22)
        self.client_ssh_port.setVisible(False)
        for widget in (self.client_host, self.client_user, self.client_ssh_port):
            client_layout.addWidget(widget)

        self.client_box.setVisible(False)
        main_layout.addWidget(self.client_box)

        self.advanced_card = CollapsibleCard("Chi tiết kỹ thuật", "GDB / TCL loopback", expanded=False)
        advanced_layout = QGridLayout()
        advanced_layout.setContentsMargins(4, 4, 4, 4)
        advanced_layout.setHorizontalSpacing(10)
        advanced_layout.setVerticalSpacing(6)

        advanced_layout.addWidget(QLabel("BIND"), 0, 0)
        self.bind_address = QLineEdit("127.0.0.1")
        self.bind_address.setObjectName("debugBindAddress")
        self.bind_address.setReadOnly(True)
        advanced_layout.addWidget(self.bind_address, 0, 1)

        advanced_layout.addWidget(QLabel("GDB"), 0, 2)
        self.gdb_port = QLineEdit("3333")
        self.gdb_port.setObjectName("debugGdbPort")
        self.gdb_port.setReadOnly(True)
        advanced_layout.addWidget(self.gdb_port, 0, 3)

        self.tcl_display = QLabel("TCL: 6666 · tự chọn loopback")
        self.tcl_display.setStyleSheet(
            "color: #64748B; font-family: 'Cascadia Code', monospace; font-size: 11px;"
        )
        advanced_layout.addWidget(self.tcl_display, 0, 4)

        self.advanced_card.content_layout.addLayout(advanced_layout)
        self.connection_box = self.advanced_card
        main_layout.addWidget(self.advanced_card)

    def set_mode(self, mode: str) -> None:
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        else:
            self._apply_mode_visibility(mode)

    def _on_combo_mode_changed(self, index: int) -> None:
        data = self.mode_combo.itemData(index)
        self._apply_mode_visibility(str(data or "auto"))

    def _apply_mode_visibility(self, mode: str) -> None:
        if mode == "local":
            self.mode_title_label.setText("LOCAL · TRỰC TIẾP")
            self.symbols_box.setVisible(True)
            self.client_box.setVisible(False)
            self.gateway_actions.setVisible(False)
            self.btn_open_gateway.setVisible(False)
        elif mode == "gateway":
            self.mode_title_label.setText("GATEWAY · MÁY CẮM ST-LINK")
            self.symbols_box.setVisible(False)
            self.client_box.setVisible(False)
            self.gateway_actions.setVisible(True)
            self.btn_open_gateway.setVisible(True)
        elif mode == "client":
            self.mode_title_label.setText("CLIENT · DEBUG TỪ XA")
            self.symbols_box.setVisible(True)
            self.client_box.setVisible(True)
            self.gateway_actions.setVisible(False)
            self.btn_open_gateway.setVisible(False)
        else:
            self.mode_title_label.setText("DEBUG SETUP")
            self.symbols_box.setVisible(True)
            self.client_box.setVisible(False)
            self.gateway_actions.setVisible(False)
            self.btn_open_gateway.setVisible(False)

    def _open_login_dialog(self) -> None:
        self.login_dialog = RemoteLoginDialog(
            default_host=self.client_host.text().strip(),
            default_user=self.client_user.text().strip(),
            default_port=self.client_ssh_port.value(),
            parent=self.window(),
        )
        self.login_dialog.login_requested.connect(self.client_login_requested.emit)
        self.login_dialog.exec()
