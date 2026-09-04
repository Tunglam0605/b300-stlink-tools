"""Simplified VS Code debug surface for B300 v0.18.

This module is deliberately presentation-only.  It never starts OpenOCD/GDB,
never launches a shell, never opens SSH itself, and never touches ST-Link.  All
hardware/session work is delegated to :mod:`b300_core.vscode_bridge` by the
owning MainWindow after an explicit operator action.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from b300_core.models import ProbeInfo, TargetInfo
from b300_core.vscode_environment import VsCodeEnvironmentStatus


class DebugVsCodeView(QWidget):
    """LOCAL/GATEWAY/CLIENT debug view with no direct process ownership."""

    open_local_vscode_requested = Signal(Path, Path)
    open_remote_vscode_requested = Signal(object)
    start_gateway_requested = Signal()
    stop_gateway_requested = Signal()
    stop_bridge_requested = Signal()
    refresh_environment_requested = Signal()
    legacy_ide_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_mode = "local"
        self._probes: tuple[ProbeInfo, ...] = ()
        self._target_info: Optional[TargetInfo] = None
        self._environment: Optional[VsCodeEnvironmentStatus] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        hero = QFrame()
        hero.setObjectName("debugVsCodeHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(12, 10, 12, 10)
        hero_layout.setSpacing(6)
        title = QLabel("VS CODE DEBUG BRIDGE")
        title.setObjectName("sectionTitle")
        hero_layout.addWidget(title)
        subtitle = QLabel(
            "B300 quản lý ST-Link, OpenOCD, SSH tunnel và run-state safety; "
            "VS Code + Cortex-Debug đảm nhiệm breakpoint/step/watch."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("pageSubtitle")
        hero_layout.addWidget(subtitle)

        mode_row = QHBoxLayout()
        self.btn_mode_local = self._mode_button("LOCAL")
        self.btn_mode_gateway = self._mode_button("GATEWAY")
        self.btn_mode_client = self._mode_button("CLIENT")
        self.btn_mode_local.setChecked(True)
        self.btn_mode_local.clicked.connect(lambda: self.select_mode("local"))
        self.btn_mode_gateway.clicked.connect(lambda: self.select_mode("gateway"))
        self.btn_mode_client.clicked.connect(lambda: self.select_mode("client"))
        for button in (self.btn_mode_local, self.btn_mode_gateway, self.btn_mode_client):
            mode_row.addWidget(button, 1)
        hero_layout.addLayout(mode_row)
        root.addWidget(hero)

        self.env_card = self._build_environment_card()
        root.addWidget(self.env_card)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_local_page())
        self.mode_stack.addWidget(self._build_gateway_page())
        self.mode_stack.addWidget(self._build_client_page())
        root.addWidget(self.mode_stack, 1)

        footer = QHBoxLayout()
        self.bridge_status = QLabel("Debug bridge: STOPPED")
        self.bridge_status.setObjectName("debugBridgeStatus")
        footer.addWidget(self.bridge_status, 1)
        self.btn_stop_bridge = QPushButton("■ STOP DEBUG BRIDGE")
        self.btn_stop_bridge.setEnabled(False)
        self.btn_stop_bridge.setToolTip(
            "Dừng bridge bằng B300; LOCAL/GATEWAY sẽ kiểm tra/khôi phục run-state trước khi nhả ST-Link."
        )
        self.btn_stop_bridge.clicked.connect(self.stop_bridge_requested.emit)
        footer.addWidget(self.btn_stop_bridge)
        root.addLayout(footer)

    def _mode_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("debugModeButton")
        button.setCheckable(True)
        button.setMinimumHeight(34)
        return button

    def _build_environment_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("debugEnvironmentCard")
        layout = QGridLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(4)
        layout.addWidget(QLabel("DEBUG ENVIRONMENT"), 0, 0, 1, 2)
        self.env_vscode = QLabel("VS Code · chưa kiểm tra")
        self.env_cortex = QLabel("Cortex-Debug · chưa kiểm tra")
        self.env_gdb = QLabel("ARM GDB · chưa kiểm tra")
        self.env_openocd = QLabel("OpenOCD · B300 managed")
        layout.addWidget(self.env_vscode, 1, 0)
        layout.addWidget(self.env_cortex, 1, 1)
        layout.addWidget(self.env_gdb, 2, 0)
        layout.addWidget(self.env_openocd, 2, 1)
        self.env_detail = QLabel(
            "B300 không yêu cầu OpenOCD trên PATH. Managed GDB được ưu tiên khi có trong bundle."
        )
        self.env_detail.setWordWrap(True)
        self.env_detail.setObjectName("mutedLabel")
        layout.addWidget(self.env_detail, 3, 0, 1, 2)
        self.btn_refresh_environment = QPushButton("↻ KIỂM TRA MÔI TRƯỜNG")
        self.btn_refresh_environment.clicked.connect(self.refresh_environment_requested.emit)
        layout.addWidget(self.btn_refresh_environment, 0, 2, 3, 1)
        return card

    def _workspace_rows(self, *, prefix: str):
        workspace = QLineEdit()
        workspace.setObjectName(prefix + "Workspace")
        workspace.setPlaceholderText("Thư mục source project trên máy này")
        workspace_button = QPushButton("Chọn workspace…")
        elf = QLineEdit()
        elf.setObjectName(prefix + "Elf")
        elf.setPlaceholderText("ELF/AXF phải đúng với firmware đang chạy")
        elf_button = QPushButton("Chọn ELF/AXF…")

        def choose_workspace() -> None:
            selected = QFileDialog.getExistingDirectory(self, "Chọn VS Code workspace", workspace.text())
            if selected:
                workspace.setText(selected)

        def choose_elf() -> None:
            start = workspace.text() or ""
            selected, _ = QFileDialog.getOpenFileName(
                self, "Chọn ELF/AXF", start, "Debug symbols (*.elf *.axf);;All files (*)"
            )
            if selected:
                elf.setText(selected)

        workspace_button.clicked.connect(choose_workspace)
        elf_button.clicked.connect(choose_elf)
        return workspace, workspace_button, elf, elf_button

    def _build_local_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.local_target_status = QLabel("STM32F407ZE · chưa kiểm tra Target")
        self.local_target_status.setObjectName("debugTargetStatus")
        layout.addWidget(self.local_target_status)

        self.local_workspace, ws_btn, self.local_elf, elf_btn = self._workspace_rows(prefix="local")
        grid = QGridLayout()
        grid.addWidget(QLabel("Workspace"), 0, 0)
        grid.addWidget(self.local_workspace, 0, 1)
        grid.addWidget(ws_btn, 0, 2)
        grid.addWidget(QLabel("ELF / AXF"), 1, 0)
        grid.addWidget(self.local_elf, 1, 1)
        grid.addWidget(elf_btn, 1, 2)
        layout.addLayout(grid)

        safety = QLabel(
            "LOCAL: nút bên dưới mới được phép khởi động B300 OpenOCD. "
            "Profile dùng request=attach + hardware breakpoint/watchpoint."
        )
        safety.setWordWrap(True)
        layout.addWidget(safety)

        self.btn_open_local_vscode = QPushButton("🚀 OPEN DEBUG IN VS CODE")
        self.btn_open_local_vscode.setMinimumHeight(42)
        self.btn_open_local_vscode.clicked.connect(self._emit_local_request)
        layout.addWidget(self.btn_open_local_vscode)
        layout.addStretch(1)
        return page

    def _build_gateway_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.gw_openocd_lbl = QLabel(
            "OpenOCD private endpoints · GDB 127.0.0.1:3333 · TCL 127.0.0.1:6666 · Telnet OFF"
        )
        self.gw_openocd_lbl.setWordWrap(True)
        self.gw_openocd_lbl.setObjectName("gatewayLoopbackStatus")
        layout.addWidget(self.gw_openocd_lbl)

        warning = QLabel(
            "Gateway không expose 3333/6666 ra LAN/Internet. Client chỉ forward GDB qua SSH; "
            "TCL giữ nội bộ cho run-state guard."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        actions = QHBoxLayout()
        self.btn_start_gateway = QPushButton("▶ START GATEWAY")
        self.btn_stop_gateway = QPushButton("■ STOP GATEWAY")
        self.btn_stop_gateway.setEnabled(False)
        self.btn_start_gateway.clicked.connect(self.start_gateway_requested.emit)
        self.btn_stop_gateway.clicked.connect(self.stop_gateway_requested.emit)
        actions.addWidget(self.btn_start_gateway)
        actions.addWidget(self.btn_stop_gateway)
        layout.addLayout(actions)

        self.gateway_state_label = QLabel("Gateway: STOPPED")
        self.gateway_state_label.setObjectName("gatewayStateLabel")
        layout.addWidget(self.gateway_state_label)
        layout.addStretch(1)
        return page

    def _build_client_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        intro = QLabel(
            "CLIENT giữ source + ELF/AXF cục bộ. B300 xác thực SSH và tạo local GDB forward; "
            "không cần ST-Link/OpenOCD trên máy Client."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        self.client_host = QLineEdit()
        self.client_host.setPlaceholderText("Gateway IP / hostname")
        self.client_user = QLineEdit()
        self.client_user.setPlaceholderText("SSH user")
        self.client_ssh_port = QSpinBox()
        self.client_ssh_port.setRange(1, 65535)
        self.client_ssh_port.setValue(22)
        self.client_local_gdb_spin = QSpinBox()
        self.client_local_gdb_spin.setRange(0, 65535)
        # Keep the familiar displayed default for compatibility; 0 can be selected
        # by advanced users to ask B300 for a dynamic free loopback port.
        self.client_local_gdb_spin.setValue(43333)
        self.client_local_gdb_spin.setToolTip("0 = B300 tự chọn local port trống")
        grid.addWidget(QLabel("Gateway"), 0, 0)
        grid.addWidget(self.client_host, 0, 1)
        grid.addWidget(QLabel("SSH user"), 0, 2)
        grid.addWidget(self.client_user, 0, 3)
        grid.addWidget(QLabel("SSH port"), 1, 0)
        grid.addWidget(self.client_ssh_port, 1, 1)
        grid.addWidget(QLabel("Local GDB"), 1, 2)
        grid.addWidget(self.client_local_gdb_spin, 1, 3)
        layout.addLayout(grid)

        self.client_workspace, ws_btn, self.client_elf, elf_btn = self._workspace_rows(prefix="client")
        files = QGridLayout()
        files.addWidget(QLabel("Workspace"), 0, 0)
        files.addWidget(self.client_workspace, 0, 1)
        files.addWidget(ws_btn, 0, 2)
        files.addWidget(QLabel("ELF / AXF"), 1, 0)
        files.addWidget(self.client_elf, 1, 1)
        files.addWidget(elf_btn, 1, 2)
        layout.addLayout(files)

        actions = QHBoxLayout()
        self.btn_test_client_conn = QPushButton("⚡ TEST CONNECTION")
        self.btn_test_client_conn.setToolTip(
            "Kết nối SSH được thực hiện khi mở remote debug; không expose debug port công khai."
        )
        self.btn_test_client_conn.clicked.connect(self._emit_remote_request)
        self.btn_open_remote_vscode = QPushButton("🚀 OPEN REMOTE DEBUG IN VS CODE")
        self.btn_open_remote_vscode.clicked.connect(self._emit_remote_request)
        actions.addWidget(self.btn_test_client_conn)
        actions.addWidget(self.btn_open_remote_vscode, 1)
        layout.addLayout(actions)

        self.client_state_label = QLabel("SSH / GDB tunnel: DISCONNECTED")
        self.client_state_label.setObjectName("clientTunnelStatus")
        layout.addWidget(self.client_state_label)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Presentation state
    # ------------------------------------------------------------------
    def select_mode(self, mode: str) -> None:
        selected = str(mode).lower()
        mapping = {"local": 0, "gateway": 1, "client": 2}
        if selected not in mapping:
            return
        self._current_mode = selected
        self.mode_stack.setCurrentIndex(mapping[selected])
        self.btn_mode_local.setChecked(selected == "local")
        self.btn_mode_gateway.setChecked(selected == "gateway")
        self.btn_mode_client.setChecked(selected == "client")

    def set_probes(self, probes: Iterable[ProbeInfo]) -> None:
        self._probes = tuple(probes)
        if self._probes:
            probe = self._probes[0]
            serial = probe.serial or "auto-select"
            self.local_target_status.setToolTip("ST-Link: %s · %s" % (probe.name, serial))
        else:
            self.local_target_status.setToolTip("Không phát hiện ST-Link")

    def set_target_info(self, info: TargetInfo) -> None:
        self._target_info = info
        self.local_target_status.setText(
            "STM32F407ZE · %dKB Flash · %.2fV · %s"
            % (info.flash_kib, info.target_voltage, info.protection_summary)
        )

    def set_environment_status(self, status: VsCodeEnvironmentStatus) -> None:
        self._environment = status
        self.env_vscode.setText("VS Code · %s" % ("✓ READY" if status.vscode_ready else "✕ MISSING"))
        self.env_cortex.setText(
            "Cortex-Debug · %s" % ("✓ READY" if status.cortex_debug_ready else "✕ MISSING")
        )
        self.env_gdb.setText("ARM GDB · %s" % ("✓ READY" if status.gdb_ready else "✕ MISSING"))
        detail = status.reason or "Môi trường debug đã sẵn sàng."
        if status.gdb_path:
            detail += " · GDB: %s" % status.gdb_path
        self.env_detail.setText(detail)

    def set_bridge_state(self, role: Optional[str], state: str, detail: str = "",
                         gdb_target: Optional[str] = None) -> None:
        role_text = role or "NONE"
        suffix = (" · %s" % gdb_target) if gdb_target else ""
        self.bridge_status.setText("Debug bridge: %s · %s%s" % (role_text, state, suffix))
        self.bridge_status.setToolTip(detail)
        active = str(state).upper() == "READY"
        self.btn_stop_bridge.setEnabled(active)
        gateway_active = active and str(role_text).upper() == "GATEWAY"
        self.btn_start_gateway.setEnabled(not active)
        self.btn_stop_gateway.setEnabled(gateway_active)
        self.gateway_state_label.setText(
            "Gateway: READY · %s" % (gdb_target or "127.0.0.1:3333")
            if gateway_active else "Gateway: STOPPED"
        )
        client_active = active and str(role_text).upper() == "CLIENT"
        self.client_state_label.setText(
            "SSH / GDB tunnel: READY · %s" % (gdb_target or "loopback")
            if client_active else "SSH / GDB tunnel: DISCONNECTED"
        )

    # ------------------------------------------------------------------
    # Explicit operator requests only
    # ------------------------------------------------------------------
    def _emit_local_request(self) -> None:
        workspace = Path(self.local_workspace.text().strip()).expanduser()
        elf = Path(self.local_elf.text().strip()).expanduser()
        self.open_local_vscode_requested.emit(workspace, elf)

    def _emit_remote_request(self) -> None:
        request = {
            "host": self.client_host.text().strip(),
            "user": self.client_user.text().strip(),
            "ssh_port": self.client_ssh_port.value(),
            "local_gdb_port": self.client_local_gdb_spin.value(),
            "workspace": Path(self.client_workspace.text().strip()).expanduser(),
            "elf": Path(self.client_elf.text().strip()).expanduser(),
        }
        self.open_remote_vscode_requested.emit(request)


__all__ = ["DebugVsCodeView"]
