"""Connection & Target Setup panel for B300 Debug Workstation."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from .collapsible_card import CollapsibleCard


class DebugConnectionPanel(QGroupBox):
    """Clean engineering connection bar with mode selection, probe info, symbols, and collapsible settings."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Thiết bị & file chương trình", parent)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # Compact responsive header. Status and probe information are allowed to
        # wrap so a laptop-width window never forces horizontal scrolling.
        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(10)
        top_grid.setVerticalSpacing(5)
        mode_box = QHBoxLayout()
        mode_box.setSpacing(6)
        mode_box.addWidget(QLabel("Kết nối:"))
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("debugModeSelector")
        for label, value in (
            ("Tự động · Khuyến nghị", "auto"),
            ("Máy này · ST-Link", "local"),
            ("Máy Gateway · ST-Link", "gateway"),
            ("Máy Client · Từ xa", "client"),
        ):
            self.mode_combo.addItem(label, value)
        self.mode_combo.setToolTip(
            "Tự động: dùng ST-Link trên máy này nếu có; nếu không sẽ dùng Gateway đã lưu."
        )
        self.mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo.setMinimumContentsLength(10)
        self.mode_combo.setMinimumWidth(150)
        mode_box.addWidget(self.mode_combo)
        top_grid.addLayout(mode_box, 0, 0)

        self.status_label = QLabel("CHƯA KẾT NỐI")
        self.status_label.setObjectName("debugStateBadge")
        self.status_label.setProperty("state", "stopped")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(120)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top_grid.addWidget(self.status_label, 0, 1)

        self.probe_display = QLabel("ST-Link: Tự động")
        self.probe_display.setStyleSheet("font-weight: 700; color: #0F172A; font-size: 12px;")
        self.probe_display.setWordWrap(True)
        self.probe_display.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top_grid.addWidget(self.probe_display, 1, 0, 1, 2)
        top_grid.setColumnStretch(0, 2)
        top_grid.setColumnStretch(1, 1)
        main_layout.addLayout(top_grid)

        # Role explanation summary
        self.role_summary = QLabel("")
        self.role_summary.setStyleSheet("color: #64748B; font-size: 11px;")
        self.role_summary.setWordWrap(True)
        main_layout.addWidget(self.role_summary)

        # Symbol selection row
        self.symbols_box = QWidget()
        symbols_layout = QGridLayout(self.symbols_box)
        symbols_layout.setContentsMargins(0, 0, 0, 0)
        symbols_layout.setHorizontalSpacing(6)
        symbols_layout.setVerticalSpacing(5)
        symbols_layout.addWidget(QLabel("File chương trình:"), 0, 0)
        self.symbol_path = QLineEdit()
        self.symbol_path.setObjectName("debugSymbolPath")
        self.symbol_path.setPlaceholderText("Không bắt buộc · file .AXF/.ELF")
        self.symbol_path.setMinimumWidth(0)
        symbols_layout.addWidget(self.symbol_path, 0, 1, 1, 2)

        self.symbol_browse_button = QPushButton("Chọn file…")
        self.symbol_browse_button.setObjectName("debugSymbolBrowseButton")
        symbols_layout.addWidget(self.symbol_browse_button, 1, 1)

        self.symbol_auto_button = QPushButton("Tự tìm")
        self.symbol_auto_button.setObjectName("debugSymbolAutoButton")
        self.symbol_auto_button.setToolTip(
            "Chọn thư mục project; tool so các mẫu Application Flash để tìm duy nhất AXF/ELF khớp."
        )
        symbols_layout.addWidget(self.symbol_auto_button, 1, 2)
        symbols_layout.setColumnStretch(1, 1)
        symbols_layout.setColumnStretch(2, 1)
        main_layout.addWidget(self.symbols_box)

        # Client SSH Gateway credentials (only visible in Client mode)
        self.client_box = QWidget()
        client_layout = QGridLayout(self.client_box)
        client_layout.setContentsMargins(0, 0, 0, 0)
        client_layout.setHorizontalSpacing(6)
        client_layout.setVerticalSpacing(5)
        client_layout.addWidget(QLabel("Gateway:"), 0, 0)
        self.client_host = QLineEdit()
        self.client_host.setObjectName("debugClientHost")
        self.client_host.setPlaceholderText("IP/hostname Gateway")
        self.client_host.setMinimumWidth(0)
        client_layout.addWidget(self.client_host, 0, 1, 1, 3)

        client_layout.addWidget(QLabel("SSH user:"), 1, 0)
        self.client_user = QLineEdit()
        self.client_user.setObjectName("debugClientUser")
        self.client_user.setPlaceholderText("SSH user")
        self.client_user.setMinimumWidth(0)
        client_layout.addWidget(self.client_user, 1, 1)

        client_layout.addWidget(QLabel("Port:"), 1, 2)
        self.client_ssh_port = QSpinBox()
        self.client_ssh_port.setObjectName("debugClientSshPort")
        self.client_ssh_port.setRange(1, 65535)
        self.client_ssh_port.setValue(22)
        client_layout.addWidget(self.client_ssh_port, 1, 3)
        client_layout.setColumnStretch(1, 1)
        main_layout.addWidget(self.client_box)

        # Gateway-only actions. Local/Client interactive GDB controls live inside
        # the collapsed Interactive Debug card so the default Live Monitor path
        # never competes with another primary "Start" button.
        self.gateway_actions = QWidget()
        gateway_actions_layout = QHBoxLayout(self.gateway_actions)
        gateway_actions_layout.setContentsMargins(0, 0, 0, 0)
        gateway_actions_layout.setSpacing(8)

        self.remote_server_button = QPushButton("Bật Gateway")
        self.remote_server_button.setObjectName("debugRemoteServerButton")
        gateway_actions_layout.addWidget(self.remote_server_button)

        self.gateway_stop_button = QPushButton("Dừng")
        self.gateway_stop_button.setObjectName("debugGatewayStopButton")
        self.gateway_stop_button.setEnabled(False)
        gateway_actions_layout.addWidget(self.gateway_stop_button)

        self.remote_kit_button = QPushButton("VS Code Kit…")
        self.remote_kit_button.setObjectName("debugRemoteKitButton")
        self.remote_kit_button.setToolTip(
            "Sinh launch.json, Cortex-Debug recommendation, SSH tunnel và checklist remote debug."
        )
        gateway_actions_layout.addWidget(self.remote_kit_button)
        gateway_actions_layout.addStretch(1)
        self.gateway_actions.setVisible(False)
        main_layout.addWidget(self.gateway_actions)

        # Advanced Settings (Collapsible)
        self.advanced_card = CollapsibleCard("Chi tiết kết nối", "Port & runtime", expanded=False)
        advanced_layout = QGridLayout()
        advanced_layout.setContentsMargins(4, 4, 4, 4)
        advanced_layout.setHorizontalSpacing(10)
        advanced_layout.setVerticalSpacing(6)

        advanced_layout.addWidget(QLabel("Host Binding:"), 0, 0)
        self.bind_address = QLineEdit("127.0.0.1")
        self.bind_address.setObjectName("debugBindAddress")
        self.bind_address.setReadOnly(True)
        self.bind_address.setToolTip("Integrated debug always binds loopback (127.0.0.1) for local safety.")
        advanced_layout.addWidget(self.bind_address, 0, 1)

        advanced_layout.addWidget(QLabel("GDB Port:"), 0, 2)
        self.gdb_port = QLineEdit("3333")
        self.gdb_port.setObjectName("debugGdbPort")
        self.gdb_port.setReadOnly(True)
        advanced_layout.addWidget(self.gdb_port, 0, 3)

        self.tcl_display = QLabel("TCL: 6666 · loopback only")
        self.tcl_display.setStyleSheet("color: #64748B; font-family: 'Cascadia Code', monospace; font-size: 11px;")
        advanced_layout.addWidget(self.tcl_display, 0, 4)

        self.advanced_card.content_layout.addLayout(advanced_layout)
        self.connection_box = self.advanced_card
        main_layout.addWidget(self.advanced_card)

