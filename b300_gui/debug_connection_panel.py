"""Connection & Target Setup panel for B300 Debug Workstation."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .collapsible_card import CollapsibleCard


class DebugConnectionPanel(QGroupBox):
    """Clean engineering connection bar with mode selection, probe info, symbols, and collapsible settings."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Kết nối thiết bị", parent)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # Top row: Probe, Mode, and prominent Status Badge
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # Mode selector
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
        mode_box.addWidget(self.mode_combo)
        top_row.addLayout(mode_box)

        # Probe info display
        self.probe_display = QLabel("ST-Link: Tự động")
        self.probe_display.setStyleSheet("font-weight: 700; color: #0F172A; font-size: 12px;")
        top_row.addWidget(self.probe_display)
        top_row.addStretch(1)

        # Status Badge
        self.status_label = QLabel("DISCONNECTED")
        self.status_label.setObjectName("debugStateBadge")
        self.status_label.setProperty("state", "stopped")
        top_row.addWidget(self.status_label)

        main_layout.addLayout(top_row)

        # Role explanation summary
        self.role_summary = QLabel("")
        self.role_summary.setStyleSheet("color: #64748B; font-size: 11px;")
        self.role_summary.setWordWrap(True)
        main_layout.addWidget(self.role_summary)

        # Symbol selection row
        self.symbols_box = QWidget()
        symbols_layout = QHBoxLayout(self.symbols_box)
        symbols_layout.setContentsMargins(0, 0, 0, 0)
        symbols_layout.setSpacing(6)
        symbols_layout.addWidget(QLabel("File chương trình:"))
        self.symbol_path = QLineEdit()
        self.symbol_path.setObjectName("debugSymbolPath")
        self.symbol_path.setPlaceholderText("Không bắt buộc · file .AXF/.ELF")
        symbols_layout.addWidget(self.symbol_path, 1)

        self.symbol_browse_button = QPushButton("Chọn ELF/AXF")
        self.symbol_browse_button.setObjectName("debugSymbolBrowseButton")
        symbols_layout.addWidget(self.symbol_browse_button)

        self.symbol_auto_button = QPushButton("Tự tìm đúng AXF/ELF")
        self.symbol_auto_button.setObjectName("debugSymbolAutoButton")
        self.symbol_auto_button.setToolTip(
            "Chọn thư mục project; tool so các mẫu Application Flash để tìm duy nhất AXF/ELF khớp."
        )
        symbols_layout.addWidget(self.symbol_auto_button)
        main_layout.addWidget(self.symbols_box)

        # Client SSH Gateway credentials (only visible in Client mode)
        self.client_box = QWidget()
        client_layout = QHBoxLayout(self.client_box)
        client_layout.setContentsMargins(0, 0, 0, 0)
        client_layout.setSpacing(6)
        client_layout.addWidget(QLabel("Gateway:"))
        self.client_host = QLineEdit()
        self.client_host.setObjectName("debugClientHost")
        self.client_host.setPlaceholderText("IP/hostname Gateway")
        client_layout.addWidget(self.client_host, 2)

        client_layout.addWidget(QLabel("SSH user:"))
        self.client_user = QLineEdit()
        self.client_user.setObjectName("debugClientUser")
        self.client_user.setPlaceholderText("SSH user")
        client_layout.addWidget(self.client_user, 1)

        client_layout.addWidget(QLabel("SSH:"))
        self.client_ssh_port = QSpinBox()
        self.client_ssh_port.setObjectName("debugClientSshPort")
        self.client_ssh_port.setRange(1, 65535)
        self.client_ssh_port.setValue(22)
        client_layout.addWidget(self.client_ssh_port)
        main_layout.addWidget(self.client_box)

        # Primary Action Bar
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.start_button = QPushButton("BẮT ĐẦU LOCAL")
        self.start_button.setObjectName("debugStartButton")
        self.start_button.setStyleSheet(
            "QPushButton { min-height: 32px; font-weight: 700; color: #FFFFFF; background-color: #0284C7; border: 1px solid #0369A1; border-radius: 6px; padding: 4px 18px; }"
            "QPushButton:hover { background-color: #0369A1; }"
            "QPushButton:disabled { background-color: #E2E8F0; color: #94A3B8; border-color: #CBD5E1; }"
        )
        action_row.addWidget(self.start_button)

        self.remote_kit_button = QPushButton("Xuất VS Code Kit…")
        self.remote_kit_button.setObjectName("debugRemoteKitButton")
        self.remote_kit_button.setToolTip(
            "Sinh launch.json, Cortex-Debug recommendation, SSH tunnel và checklist remote debug."
        )
        action_row.addWidget(self.remote_kit_button)


        self.stop_button = QPushButton("Stop Debug")
        self.stop_button.setObjectName("debugStopButton")
        self.stop_button.setStyleSheet(
            "QPushButton { min-height: 32px; font-weight: 600; color: #991B1B; background-color: #FEF2F2; border: 1px solid #FECACA; border-radius: 6px; padding: 4px 14px; }"
            "QPushButton:hover { background-color: #FEE2E2; border-color: #DC2626; color: #DC2626; }"
            "QPushButton:disabled { background-color: #F1F5F9; color: #94A3B8; border-color: #E2E8F0; }"
        )
        action_row.addWidget(self.stop_button)

        self.remote_server_button = QPushButton("Gateway nhanh")
        self.remote_server_button.setObjectName("debugRemoteServerButton")
        self.remote_server_button.setVisible(False)
        action_row.addWidget(self.remote_server_button)

        action_row.addStretch(1)
        main_layout.addLayout(action_row)

        # Advanced Settings (Collapsible)
        self.advanced_card = CollapsibleCard("Chi tiết kết nối", "Port, loopback và thông tin runtime", expanded=False)
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

