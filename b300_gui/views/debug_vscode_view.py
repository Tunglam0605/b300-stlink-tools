"""VS Code-oriented Debug Bridge View for B300 ST-Link Tools (v0.18).

Delegates source-level and interactive debugging to VS Code + Cortex-Debug.
Presents 3 clean modes:
  1. LOCAL: Debug STM32 connected to this computer.
  2. GATEWAY: Expose this ST-Link safely through loopback OpenOCD & SSH tunnel.
  3. CLIENT: Debug a remote STM32 via a B300 Gateway using local VS Code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from b300_core.gdb_runtime import resolve_gdb
from b300_core.models import ProbeInfo, ProbeRef, TargetInfo
from b300_core.remote_vscode import RemoteVsCodeProfile, workspace_executable
from b300_gui.collapsible_card import CollapsibleCard


def _detect_vscode() -> bool:
    """Check if VS Code 'code' binary is available."""
    if shutil.which("code") or shutil.which("code.cmd"):
        return True
    # Common Windows locations
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        p = Path(local_app_data) / "Programs" / "Microsoft VS Code" / "Code.exe"
        if p.exists():
            return True
    program_files = os.environ.get("ProgramFiles", "")
    if program_files:
        p = Path(program_files) / "Microsoft VS Code" / "Code.exe"
        if p.exists():
            return True
    return False


def _detect_cortex_debug() -> bool:
    """Check if marus25.cortex-debug extension is installed in user directory."""
    ext_dir = Path.home() / ".vscode" / "extensions"
    if ext_dir.is_dir():
        for item in ext_dir.iterdir():
            if "cortex-debug" in item.name.lower():
                return True
    return False


def _detect_openocd() -> bool:
    """Check if OpenOCD is available."""
    return shutil.which("openocd") is not None


class DebugVsCodeView(QWidget):
    """VS Code Debug Bridge presenting LOCAL, GATEWAY, and CLIENT modes."""

    mode_changed = Signal(str)
    open_local_vscode_requested = Signal(Path, Path)          # (workspace, elf)
    open_remote_vscode_requested = Signal(dict)               # (profile_dict)
    start_gateway_requested = Signal()
    stop_gateway_requested = Signal()
    test_client_connection_requested = Signal(str, str, int)  # (host, user, port)
    legacy_ide_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("debugVsCodeViewContainer")
        self._current_mode = "local"
        self._probes: List[ProbeInfo] = []
        self._target_info: Optional[TargetInfo] = None
        self._gateway_running = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("debugVsCodeContent")
        self.container_layout = QVBoxLayout(container)
        self.container_layout.setContentsMargins(16, 12, 16, 14)
        self.container_layout.setSpacing(10)

        self._build_ui()
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        # Header banner
        header = QFrame()
        header.setObjectName("headerRibbon")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_lbl = QLabel("DEBUG WITH VS CODE")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 800; color: #38BDF8; letter-spacing: 0.6px;")
        title_row.addWidget(title_lbl)

        bridge_badge = QLabel("VS CODE BRIDGE")
        bridge_badge.setStyleSheet(
            "font-size: 10px; font-weight: 800; font-family: monospace; "
            "padding: 2px 6px; border-radius: 3px; background: rgba(56, 189, 248, 0.15); color: #38BDF8;"
        )
        title_row.addWidget(bridge_badge)
        title_row.addStretch(1)
        header_layout.addLayout(title_row)

        desc = QLabel(
            "Cortex-Debug & OpenOCD Bridge · B300 đảm nhiệm phần cứng và an toàn Flash/Option Bytes; "
            "VS Code đảm nhiệm Source code, Breakpoints, Stepping, Watch và Call Stack."
        )
        desc.setStyleSheet("font-size: 11px; color: #94A3B8;")
        header_layout.addWidget(desc)
        self.container_layout.addWidget(header)

        # Mode Selection Ribbon
        self._build_mode_selector()

        # Mode Stack (LOCAL, GATEWAY, CLIENT)
        self.mode_stack = QStackedWidget()
        self.local_widget = self._build_local_view()
        self.gateway_widget = self._build_gateway_view()
        self.client_widget = self._build_client_view()

        self.mode_stack.addWidget(self.local_widget)     # Index 0: LOCAL
        self.mode_stack.addWidget(self.gateway_widget)   # Index 1: GATEWAY
        self.mode_stack.addWidget(self.client_widget)    # Index 2: CLIENT

        self.container_layout.addWidget(self.mode_stack, 1)

    def _build_mode_selector(self) -> None:
        sel_card = QFrame()
        sel_card.setObjectName("cardSurface")
        sel_layout = QVBoxLayout(sel_card)
        sel_layout.setContentsMargins(12, 8, 12, 8)
        sel_layout.setSpacing(6)

        title = QLabel("CHỌN CHẾ ĐỘ DEBUG:")
        title.setObjectName("eyebrowLabel")
        sel_layout.addWidget(title)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_mode_local = QPushButton("💻  LOCAL\nDebug STM32 cắm máy này")
        self.btn_mode_local.setCheckable(True)
        self.btn_mode_local.setChecked(True)
        self.btn_mode_local.setStyleSheet(
            "QPushButton { padding: 8px 14px; text-align: left; font-weight: 700; "
            "border: 1.5px solid #38BDF8; background: rgba(56, 189, 248, 0.10); border-radius: 6px; color: #F8FAFC; }"
            "QPushButton:hover { background: rgba(56, 189, 248, 0.18); }"
        )
        self.btn_mode_local.clicked.connect(lambda: self.select_mode("local"))
        btn_row.addWidget(self.btn_mode_local, 1)

        self.btn_mode_gateway = QPushButton("🌐  GATEWAY\nChia sẻ ST-Link cho Client")
        self.btn_mode_gateway.setCheckable(True)
        self.btn_mode_gateway.setStyleSheet(
            "QPushButton { padding: 8px 14px; text-align: left; font-weight: 700; "
            "border: 1.5px solid #334155; background: #0F172A; border-radius: 6px; color: #94A3B8; }"
            "QPushButton:hover { background: #1E293B; color: #F8FAFC; }"
        )
        self.btn_mode_gateway.clicked.connect(lambda: self.select_mode("gateway"))
        btn_row.addWidget(self.btn_mode_gateway, 1)

        self.btn_mode_client = QPushButton("📡  CLIENT\nDebug STM32 qua Gateway từ xa")
        self.btn_mode_client.setCheckable(True)
        self.btn_mode_client.setStyleSheet(
            "QPushButton { padding: 8px 14px; text-align: left; font-weight: 700; "
            "border: 1.5px solid #334155; background: #0F172A; border-radius: 6px; color: #94A3B8; }"
            "QPushButton:hover { background: #1E293B; color: #F8FAFC; }"
        )
        self.btn_mode_client.clicked.connect(lambda: self.select_mode("client"))
        btn_row.addWidget(self.btn_mode_client, 1)

        sel_layout.addLayout(btn_row)
        self.container_layout.addWidget(sel_card)

    def select_mode(self, mode: str) -> None:
        self._current_mode = mode
        active_style = (
            "QPushButton { padding: 8px 14px; text-align: left; font-weight: 700; "
            "border: 1.5px solid #38BDF8; background: rgba(56, 189, 248, 0.10); border-radius: 6px; color: #F8FAFC; }"
        )
        inactive_style = (
            "QPushButton { padding: 8px 14px; text-align: left; font-weight: 700; "
            "border: 1.5px solid #334155; background: #0F172A; border-radius: 6px; color: #94A3B8; }"
            "QPushButton:hover { background: #1E293B; color: #F8FAFC; }"
        )
        self.btn_mode_local.setChecked(mode == "local")
        self.btn_mode_local.setStyleSheet(active_style if mode == "local" else inactive_style)

        self.btn_mode_gateway.setChecked(mode == "gateway")
        self.btn_mode_gateway.setStyleSheet(active_style if mode == "gateway" else inactive_style)

        self.btn_mode_client.setChecked(mode == "client")
        self.btn_mode_client.setStyleSheet(active_style if mode == "client" else inactive_style)

        if mode == "local":
            self.mode_stack.setCurrentIndex(0)
        elif mode == "gateway":
            self.mode_stack.setCurrentIndex(1)
        elif mode == "client":
            self.mode_stack.setCurrentIndex(2)

        self.mode_changed.emit(mode)

    # ----------------------------------------------------
    # 1. LOCAL DEBUG SUB-VIEW
    # ----------------------------------------------------
    def _build_local_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("cardSurface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(10)

        # ST-Link & Target Status Row
        st_row = QHBoxLayout()
        st_row.setSpacing(14)

        # ST-Link
        stlink_box = QVBoxLayout()
        stlink_box.setSpacing(2)
        lbl_st_tag = QLabel("ST-Link:")
        lbl_st_tag.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self.local_probe_status = QLabel("ST-LINK V2 · ● Connected")
        self.local_probe_status.setStyleSheet("font-weight: 700; color: #10B981;")
        stlink_box.addWidget(lbl_st_tag)
        stlink_box.addWidget(self.local_probe_status)
        st_row.addLayout(stlink_box, 1)

        # Target
        target_box = QVBoxLayout()
        target_box.setSpacing(2)
        lbl_tgt_tag = QLabel("Target:")
        lbl_tgt_tag.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self.local_target_status = QLabel("STM32F407ZET6 · ● Ready")
        self.local_target_status.setStyleSheet("font-weight: 700; color: #38BDF8;")
        target_box.addWidget(lbl_tgt_tag)
        target_box.addWidget(self.local_target_status)
        st_row.addLayout(target_box, 1)
        c_layout.addLayout(st_row)

        # Project / Symbols form
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        lbl_ws = QLabel("Workspace:")
        lbl_ws.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.local_ws_edit = QLineEdit()
        self.local_ws_edit.setPlaceholderText("Đường dẫn thư mục dự án (workspace chứa mã nguồn)...")
        btn_browse_ws = QPushButton("📁 Chọn…")
        btn_browse_ws.clicked.connect(self._browse_local_workspace)
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.local_ws_edit, 1)
        ws_row.addWidget(btn_browse_ws)
        form.addWidget(lbl_ws, 0, 0)
        form.addLayout(ws_row, 0, 1)

        lbl_elf = QLabel("ELF / AXF:")
        lbl_elf.setStyleSheet("font-weight: 600; color: #94A3B8;")
        self.local_elf_edit = QLineEdit()
        self.local_elf_edit.setPlaceholderText("Objects/application.axf hoặc build/application.elf...")
        btn_browse_elf = QPushButton("📁 Chọn…")
        btn_browse_elf.clicked.connect(self._browse_local_elf)
        btn_auto_elf = QPushButton("⚡ Auto-match")
        btn_auto_elf.setToolTip("Tìm file ELF/AXF khớp với Application Flash")
        btn_auto_elf.clicked.connect(self._auto_match_elf)
        elf_row = QHBoxLayout()
        elf_row.addWidget(self.local_elf_edit, 1)
        elf_row.addWidget(btn_browse_elf)
        elf_row.addWidget(btn_auto_elf)
        form.addWidget(lbl_elf, 1, 0)
        form.addLayout(elf_row, 1, 1)
        c_layout.addLayout(form)

        # Environment Check Row
        env_row = QHBoxLayout()
        env_row.setSpacing(14)

        has_vscode = _detect_vscode()
        has_cortex = _detect_cortex_debug()
        has_openocd = _detect_openocd()

        self.lbl_vscode_status = QLabel(
            f"VS Code: {'● Installed' if has_vscode else '○ Not detected'}"
        )
        self.lbl_vscode_status.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {'#10B981' if has_vscode else '#F59E0B'};"
        )
        env_row.addWidget(self.lbl_vscode_status)

        self.lbl_cortex_status = QLabel(
            f"Cortex-Debug: {'● Installed' if has_cortex else '○ Missing'}"
        )
        self.lbl_cortex_status.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {'#10B981' if has_cortex else '#F59E0B'};"
        )
        env_row.addWidget(self.lbl_cortex_status)

        self.lbl_openocd_status = QLabel(
            f"OpenOCD: {'● Ready' if has_openocd else '○ Missing'}"
        )
        self.lbl_openocd_status.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {'#10B981' if has_openocd else '#EF4444'};"
        )
        env_row.addWidget(self.lbl_openocd_status)
        env_row.addStretch(1)
        c_layout.addLayout(env_row)

        # Primary CTA Button
        cta_row = QHBoxLayout()
        self.btn_open_local_vscode = QPushButton("🚀 OPEN DEBUG IN VS CODE")
        self.btn_open_local_vscode.setObjectName("primaryCtaButton")
        self.btn_open_local_vscode.setStyleSheet(
            "QPushButton { background: #0284C7; color: white; font-weight: 800; "
            "font-size: 13px; padding: 10px 22px; border-radius: 5px; }"
            "QPushButton:hover { background: #0369A1; }"
            "QPushButton:disabled { background: #334155; color: #64748B; }"
        )
        self.btn_open_local_vscode.clicked.connect(self._on_open_local_vscode_clicked)
        cta_row.addWidget(self.btn_open_local_vscode)
        cta_row.addStretch(1)
        c_layout.addLayout(cta_row)

        layout.addWidget(card)

        # Advanced Local Collapsible
        adv_local = CollapsibleCard(
            "Advanced > Cấu hình chi tiết",
            "GDB path, OpenOCD port, SVD, toolchain, logs, Internal IDE diagnostics",
            expanded=False,
            parent=widget,
        )
        adv_layout = adv_local.content_layout

        adv_grid = QGridLayout()
        adv_grid.setHorizontalSpacing(8)
        adv_grid.setVerticalSpacing(6)

        adv_grid.addWidget(QLabel("GDB Path:"), 0, 0)
        try:
            default_gdb = resolve_gdb()
        except Exception:
            default_gdb = "arm-none-eabi-gdb"
        self.local_gdb_edit = QLineEdit(default_gdb)
        adv_grid.addWidget(self.local_gdb_edit, 0, 1)

        adv_grid.addWidget(QLabel("OpenOCD Port:"), 1, 0)
        self.local_port_spin = QSpinBox()
        self.local_port_spin.setRange(1024, 65535)
        self.local_port_spin.setValue(3333)
        self.local_port_spin.setToolTip("GDB server loopback port (127.0.0.1)")
        adv_grid.addWidget(self.local_port_spin, 1, 1)

        adv_grid.addWidget(QLabel("TCL Port:"), 2, 0)
        self.local_tcl_spin = QSpinBox()
        self.local_tcl_spin.setRange(1024, 65535)
        self.local_tcl_spin.setValue(6666)
        adv_grid.addWidget(self.local_tcl_spin, 2, 1)

        adv_grid.addWidget(QLabel("SVD Path:"), 3, 0)
        self.local_svd_edit = QLineEdit()
        self.local_svd_edit.setPlaceholderText("STM32F407.svd...")
        adv_grid.addWidget(self.local_svd_edit, 3, 1)

        adv_layout.addLayout(adv_grid)

        # Diagnostic launcher for legacy internal IDE (preserved for test compatibility / developer diagnostics)
        legacy_row = QHBoxLayout()
        self.btn_launch_legacy_ide = QPushButton("🛠 Mở Internal Debug Workbench (Legacy / Chẩn đoán)")
        self.btn_launch_legacy_ide.setObjectName("ghostButton")
        self.btn_launch_legacy_ide.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self.btn_launch_legacy_ide.clicked.connect(self.legacy_ide_requested.emit)
        legacy_row.addWidget(self.btn_launch_legacy_ide)
        legacy_row.addStretch(1)
        adv_layout.addLayout(legacy_row)

        layout.addWidget(adv_local)
        layout.addStretch(1)
        return widget

    # ----------------------------------------------------
    # 2. GATEWAY DEBUG SUB-VIEW
    # ----------------------------------------------------
    def _build_gateway_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("cardSurface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(10)

        gw_title = QLabel("REMOTE DEBUG GATEWAY")
        gw_title.setObjectName("eyebrowLabel")
        c_layout.addWidget(gw_title)

        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(14)
        status_grid.setVerticalSpacing(6)

        status_grid.addWidget(QLabel("ST-Link:"), 0, 0)
        self.gw_probe_lbl = QLabel("ST-LINK V2 · ● Connected")
        self.gw_probe_lbl.setStyleSheet("font-weight: 700; color: #10B981;")
        status_grid.addWidget(self.gw_probe_lbl, 0, 1)

        status_grid.addWidget(QLabel("Target:"), 0, 2)
        self.gw_target_lbl = QLabel("STM32F407ZET6")
        self.gw_target_lbl.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        status_grid.addWidget(self.gw_target_lbl, 0, 3)

        status_grid.addWidget(QLabel("Gateway:"), 1, 0)
        self.gw_status_lbl = QLabel("○ Sẵn sàng bật")
        self.gw_status_lbl.setStyleSheet("font-weight: 700; color: #94A3B8;")
        status_grid.addWidget(self.gw_status_lbl, 1, 1)

        status_grid.addWidget(QLabel("OpenOCD:"), 1, 2)
        # CRITICAL SAFETY: Loopback 127.0.0.1, never 0.0.0.0
        self.gw_openocd_lbl = QLabel("127.0.0.1:3333 (Loopback bảo mật)")
        self.gw_openocd_lbl.setStyleSheet("font-family: monospace; font-weight: 700; color: #38BDF8;")
        status_grid.addWidget(self.gw_openocd_lbl, 1, 3)

        status_grid.addWidget(QLabel("SSH:"), 2, 0)
        self.gw_ssh_lbl = QLabel("● Available (Port 22)")
        self.gw_ssh_lbl.setStyleSheet("font-weight: 700; color: #10B981;")
        status_grid.addWidget(self.gw_ssh_lbl, 2, 1)

        status_grid.addWidget(QLabel("Remote access:"), 2, 2)
        self.gw_access_lbl = QLabel("Only through secure tunnel (SSH -L)")
        self.gw_access_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        status_grid.addWidget(self.gw_access_lbl, 2, 3)

        c_layout.addLayout(status_grid)

        # Gateway Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_start_gateway = QPushButton("▶ START GATEWAY")
        self.btn_start_gateway.setStyleSheet(
            "QPushButton { background: #059669; color: white; font-weight: 800; "
            "font-size: 12px; padding: 8px 18px; border-radius: 4px; }"
            "QPushButton:hover { background: #047857; }"
        )
        self.btn_start_gateway.clicked.connect(self._on_start_gateway_clicked)
        btn_row.addWidget(self.btn_start_gateway)

        self.btn_stop_gateway = QPushButton("⏹ STOP GATEWAY")
        self.btn_stop_gateway.setEnabled(False)
        self.btn_stop_gateway.setStyleSheet(
            "QPushButton { background: #DC2626; color: white; font-weight: 800; "
            "font-size: 12px; padding: 8px 18px; border-radius: 4px; }"
            "QPushButton:hover { background: #B91C1C; }"
            "QPushButton:disabled { background: #334155; color: #64748B; }"
        )
        self.btn_stop_gateway.clicked.connect(self._on_stop_gateway_clicked)
        btn_row.addWidget(self.btn_stop_gateway)
        btn_row.addStretch(1)
        c_layout.addLayout(btn_row)

        layout.addWidget(card)

        # Advanced Gateway Collapsible
        adv_gw = CollapsibleCard(
            "Advanced > Thông số Gateway",
            "Probe serial, SSH info, loopback ports, gateway logs",
            expanded=False,
            parent=widget,
        )
        adv_gw_layout = adv_gw.content_layout

        gw_detail_grid = QGridLayout()
        gw_detail_grid.setHorizontalSpacing(8)
        gw_detail_grid.setVerticalSpacing(4)

        gw_detail_grid.addWidget(QLabel("Probe Serial:"), 0, 0)
        self.gw_probe_serial_edit = QLineEdit()
        self.gw_probe_serial_edit.setPlaceholderText("Tự động chọn nếu chỉ có 1 probe")
        gw_detail_grid.addWidget(self.gw_probe_serial_edit, 0, 1)

        gw_detail_grid.addWidget(QLabel("OpenOCD Bind:"), 1, 0)
        lbl_bind = QLabel("127.0.0.1 (Bắt buộc an toàn, cấm 0.0.0.0)")
        lbl_bind.setStyleSheet("color: #10B981; font-family: monospace;")
        gw_detail_grid.addWidget(lbl_bind, 1, 1)

        adv_gw_layout.addLayout(gw_detail_grid)

        self.gw_log_terminal = QPlainTextEdit()
        self.gw_log_terminal.setReadOnly(True)
        self.gw_log_terminal.setMaximumHeight(100)
        self.gw_log_terminal.setStyleSheet(
            "background: #090D16; color: #CBD5E1; font-family: monospace; font-size: 11px;"
        )
        adv_gw_layout.addWidget(self.gw_log_terminal)

        layout.addWidget(adv_gw)
        layout.addStretch(1)
        return widget

    # ----------------------------------------------------
    # 3. CLIENT DEBUG SUB-VIEW
    # ----------------------------------------------------
    def _build_client_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("cardSurface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(10)

        cl_title = QLabel("REMOTE DEBUG CLIENT")
        cl_title.setObjectName("eyebrowLabel")
        c_layout.addWidget(cl_title)

        # Gateway connection parameters
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        form.addWidget(QLabel("Gateway:"), 0, 0)
        self.client_host_edit = QLineEdit()
        self.client_host_edit.setPlaceholderText("192.168.1.109 hoặc b300-gateway.local")
        form.addWidget(self.client_host_edit, 0, 1)

        form.addWidget(QLabel("SSH User:"), 1, 0)
        self.client_user_edit = QLineEdit()
        self.client_user_edit.setPlaceholderText("automation")
        form.addWidget(self.client_user_edit, 1, 1)

        form.addWidget(QLabel("SSH Port:"), 2, 0)
        self.client_port_spin = QSpinBox()
        self.client_port_spin.setRange(1, 65535)
        self.client_port_spin.setValue(22)
        form.addWidget(self.client_port_spin, 2, 1)

        form.addWidget(QLabel("Workspace:"), 3, 0)
        self.client_ws_edit = QLineEdit()
        self.client_ws_edit.setPlaceholderText("Thư mục workspace trên máy này...")
        btn_ws = QPushButton("📁 Chọn…")
        btn_ws.clicked.connect(self._browse_client_workspace)
        ws_r = QHBoxLayout()
        ws_r.addWidget(self.client_ws_edit, 1)
        ws_r.addWidget(btn_ws)
        form.addLayout(ws_r, 3, 1)

        form.addWidget(QLabel("ELF / AXF:"), 4, 0)
        self.client_elf_edit = QLineEdit()
        self.client_elf_edit.setPlaceholderText("build/application.elf trong workspace...")
        btn_elf = QPushButton("📁 Chọn…")
        btn_elf.clicked.connect(self._browse_client_elf)
        elf_r = QHBoxLayout()
        elf_r.addWidget(self.client_elf_edit, 1)
        elf_r.addWidget(btn_elf)
        form.addLayout(elf_r, 4, 1)

        c_layout.addLayout(form)

        # Status row
        status_row = QHBoxLayout()
        status_row.setSpacing(12)

        self.lbl_client_ssh = QLabel("SSH: ○ Disconnected")
        self.lbl_client_ssh.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        status_row.addWidget(self.lbl_client_ssh)

        self.lbl_client_tunnel = QLabel("Tunnel: ○ Stopped")
        self.lbl_client_tunnel.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        status_row.addWidget(self.lbl_client_tunnel)

        self.lbl_client_gw = QLabel("Gateway: ○ Ready")
        self.lbl_client_gw.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        status_row.addWidget(self.lbl_client_gw)

        self.lbl_client_target = QLabel("Target: ● STM32F407ZET6")
        self.lbl_client_target.setStyleSheet("font-size: 11px; font-weight: 600; color: #38BDF8;")
        status_row.addWidget(self.lbl_client_target)
        status_row.addStretch(1)
        c_layout.addLayout(status_row)

        # Tunnel description
        tunnel_desc = QLabel("Tunnel: localhost:43333 → Gateway:3333 (SSH Port Forwarding bảo mật)")
        tunnel_desc.setStyleSheet("font-size: 11px; color: #38BDF8; font-family: monospace;")
        c_layout.addWidget(tunnel_desc)

        # Action Buttons
        act_row = QHBoxLayout()
        act_row.setSpacing(10)

        self.btn_open_remote_vscode = QPushButton("🚀 OPEN REMOTE DEBUG IN VS CODE")
        self.btn_open_remote_vscode.setObjectName("primaryCtaButton")
        self.btn_open_remote_vscode.setStyleSheet(
            "QPushButton { background: #0284C7; color: white; font-weight: 800; "
            "font-size: 13px; padding: 10px 22px; border-radius: 5px; }"
            "QPushButton:hover { background: #0369A1; }"
        )
        self.btn_open_remote_vscode.clicked.connect(self._on_open_remote_vscode_clicked)
        act_row.addWidget(self.btn_open_remote_vscode)

        self.btn_test_client_conn = QPushButton("⚡ TEST CONNECTION")
        self.btn_test_client_conn.setObjectName("ghostButton")
        self.btn_test_client_conn.clicked.connect(self._on_test_client_conn_clicked)
        act_row.addWidget(self.btn_test_client_conn)
        act_row.addStretch(1)
        c_layout.addLayout(act_row)

        layout.addWidget(card)

        # Advanced Client Collapsible
        adv_cl = CollapsibleCard(
            "Advanced > Cấu hình chi tiết Client",
            "local GDB port, GDB path, Cortex-Debug, generated launch.json, SSH logs",
            expanded=False,
            parent=widget,
        )
        adv_cl_layout = adv_cl.content_layout

        cl_adv_grid = QGridLayout()
        cl_adv_grid.setHorizontalSpacing(8)
        cl_adv_grid.setVerticalSpacing(6)

        cl_adv_grid.addWidget(QLabel("Local GDB Port:"), 0, 0)
        self.client_local_gdb_spin = QSpinBox()
        self.client_local_gdb_spin.setRange(1024, 65535)
        self.client_local_gdb_spin.setValue(43333)
        cl_adv_grid.addWidget(self.client_local_gdb_spin, 0, 1)

        cl_adv_grid.addWidget(QLabel("GDB trên máy này:"), 1, 0)
        try:
            default_gdb = resolve_gdb()
        except Exception:
            default_gdb = "arm-none-eabi-gdb"
        self.client_gdb_edit = QLineEdit(default_gdb)
        cl_adv_grid.addWidget(self.client_gdb_edit, 1, 1)

        adv_cl_layout.addLayout(cl_adv_grid)

        self.client_launch_preview = QPlainTextEdit()
        self.client_launch_preview.setReadOnly(True)
        self.client_launch_preview.setMaximumHeight(120)
        self.client_launch_preview.setPlaceholderText("launch.json cấu hình Cortex-Debug...")
        self.client_launch_preview.setStyleSheet(
            "background: #090D16; color: #CBD5E1; font-family: monospace; font-size: 11px;"
        )
        adv_cl_layout.addWidget(self.client_launch_preview)

        layout.addWidget(adv_cl)
        layout.addStretch(1)
        return widget

    # ----------------------------------------------------
    # Event Handlers & Helpers
    # ----------------------------------------------------
    def _browse_local_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục Workspace dự án", str(Path.home()))
        if folder:
            self.local_ws_edit.setText(folder)

    def _browse_local_elf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file symbols ELF / AXF", str(Path.home()),
            "Executable / Symbols (*.elf *.axf);;All (*.*)"
        )
        if path:
            self.local_elf_edit.setText(path)

    def _browse_client_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục Workspace Client", str(Path.home()))
        if folder:
            self.client_ws_edit.setText(folder)

    def _browse_client_elf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file symbols ELF / AXF", str(Path.home()),
            "Executable / Symbols (*.elf *.axf);;All (*.*)"
        )
        if path:
            self.client_elf_edit.setText(path)

    def _auto_match_elf(self) -> None:
        # TODO(v0.18-backend): GatewayController.auto_match_symbols(...)
        ws = self.local_ws_edit.text().strip()
        if ws and Path(ws).is_dir():
            candidates = list(Path(ws).glob("**/*.axf")) + list(Path(ws).glob("**/*.elf"))
            if candidates:
                self.local_elf_edit.setText(str(candidates[0]))
                QMessageBox.information(self, "Auto-match", f"Đã tìm thấy: {candidates[0].name}")
                return
        QMessageBox.information(self, "Auto-match", "Chưa tìm thấy AXF/ELF tương ứng trong workspace.")

    def _on_open_local_vscode_clicked(self) -> None:
        ws_text = self.local_ws_edit.text().strip()
        elf_text = self.local_elf_edit.text().strip()
        if not ws_text:
            QMessageBox.warning(self, "Thiếu Workspace", "Vui lòng chọn thư mục Workspace dự án.")
            return

        ws_path = Path(ws_text)
        elf_path = Path(elf_text) if elf_text else None

        # Generate / update launch.json in workspace if possible
        try:
            vscode_dir = ws_path / ".vscode"
            vscode_dir.mkdir(parents=True, exist_ok=True)
            launch_path = vscode_dir / "launch.json"

            rel_elf = "${workspaceFolder}/build/application.elf"
            if elf_path is not None:
                try:
                    rel = elf_path.relative_to(ws_path).as_posix()
                    rel_elf = f"${{workspaceFolder}}/{rel}"
                except ValueError:
                    rel_elf = str(elf_path).replace("\\", "/")

            config = {
                "version": "0.2.0",
                "configurations": [
                    {
                        "name": "B300 STM32F407 · Local Debug",
                        "type": "cortex-debug",
                        "request": "attach",
                        "cwd": "${workspaceFolder}",
                        "executable": rel_elf,
                        "servertype": "external",
                        "gdbTarget": f"127.0.0.1:{self.local_port_spin.value()}",
                        "gdbPath": self.local_gdb_edit.text().strip() or "arm-none-eabi-gdb",
                        "device": "STM32F407ZE",
                        "hardwareBreakpoints": {"require": True, "limit": 6},
                        "hardwareWatchpoints": {"require": True, "limit": 4},
                    }
                ],
            }
            if not launch_path.exists():
                launch_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        # Try launching VS Code
        launched = False
        try:
            subprocess.Popen(["code", str(ws_path)], shell=True)
            launched = True
        except Exception:
            launched = False

        self.open_local_vscode_requested.emit(ws_path, elf_path or Path(""))
        if launched:
            QMessageBox.information(
                self,
                "Đã mở VS Code",
                f"Đã mở VS Code tại thư mục:\n{ws_path}\n\n"
                "Chọn 'B300 STM32F407 · Local Debug' trong Run and Debug (Ctrl+Shift+D) để bắt đầu.",
            )
        else:
            QMessageBox.information(
                self,
                "Đã cấu hình Workspace",
                f"Đã cập nhật .vscode/launch.json tại:\n{ws_path}\n\n"
                "Vui lòng mở VS Code thủ công và chọn 'B300 STM32F407 · Local Debug'.",
            )

    def _on_start_gateway_clicked(self) -> None:
        # TODO(v0.18-backend): GatewayController.start_gateway(...)
        self._gateway_running = True
        self.gw_status_lbl.setText("● Running")
        self.gw_status_lbl.setStyleSheet("font-weight: 700; color: #10B981;")
        self.btn_start_gateway.setEnabled(False)
        self.btn_stop_gateway.setEnabled(True)
        self.gw_log_terminal.appendPlainText("Khởi động OpenOCD loopback 127.0.0.1:3333...")
        self.gw_log_terminal.appendPlainText("SSH tunnel endpoint sẵn sàng tiếp nhận kết nối Client.")
        self.start_gateway_requested.emit()

    def _on_stop_gateway_clicked(self) -> None:
        # TODO(v0.18-backend): GatewayController.stop_gateway(...)
        self._gateway_running = False
        self.gw_status_lbl.setText("○ Đã dừng")
        self.gw_status_lbl.setStyleSheet("font-weight: 700; color: #94A3B8;")
        self.btn_start_gateway.setEnabled(True)
        self.btn_stop_gateway.setEnabled(False)
        self.gw_log_terminal.appendPlainText("Đã dừng Gateway OpenOCD.")
        self.stop_gateway_requested.emit()

    def _on_open_remote_vscode_clicked(self) -> None:
        host = self.client_host_edit.text().strip()
        user = self.client_user_edit.text().strip()
        ws = self.client_ws_edit.text().strip()
        if not host or not user or not ws:
            QMessageBox.warning(
                self, "Thiếu thông tin", "Vui lòng nhập Gateway host, SSH user và thư mục Workspace."
            )
            return

        profile = {
            "host": host,
            "user": user,
            "port": self.client_port_spin.value(),
            "workspace": ws,
            "executable": self.client_elf_edit.text().strip(),
            "local_gdb_port": self.client_local_gdb_spin.value(),
        }

        # Update launch preview
        launch_cfg = {
            "name": f"B300 STM32F407 · Remote via {host}",
            "type": "cortex-debug",
            "request": "attach",
            "cwd": "${workspaceFolder}",
            "executable": self.client_elf_edit.text().strip() or "${workspaceFolder}/build/application.elf",
            "servertype": "external",
            "gdbTarget": f"127.0.0.1:{self.client_local_gdb_spin.value()}",
            "gdbPath": self.client_gdb_edit.text().strip() or "arm-none-eabi-gdb",
            "device": "STM32F407ZE",
        }
        self.client_launch_preview.setPlainText(json.dumps(launch_cfg, indent=2))

        # TODO(v0.18-backend): ClientSessionController.open_remote_vscode(profile)
        self.open_remote_vscode_requested.emit(profile)
        QMessageBox.information(
            self,
            "Remote Debug",
            f"Đã chuẩn bị cấu hình Cortex-Debug kết nối tới Gateway {host} qua port 127.0.0.1:{self.client_local_gdb_spin.value()}.",
        )

    def _on_test_client_conn_clicked(self) -> None:
        host = self.client_host_edit.text().strip()
        user = self.client_user_edit.text().strip()
        port = self.client_port_spin.value()
        if not host or not user:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập Gateway host và SSH user để kiểm tra kết nối.")
            return
        # TODO(v0.18-backend): ClientSessionController.test_connection(host, user, port)
        self.lbl_client_ssh.setText(f"SSH: ● Checking {host}...")
        self.lbl_client_ssh.setStyleSheet("font-size: 11px; font-weight: 600; color: #EAB308;")
        self.test_client_connection_requested.emit(host, user, port)

    def set_probes(self, probes: Sequence[ProbeInfo]) -> None:
        self._probes = list(probes)
        if not self._probes:
            self.local_probe_status.setText("ST-Link: ○ Not detected")
            self.local_probe_status.setStyleSheet("font-weight: 700; color: #EF4444;")
            self.gw_probe_lbl.setText("ST-Link: ○ Not detected")
            self.gw_probe_lbl.setStyleSheet("font-weight: 700; color: #EF4444;")
        else:
            p = self._probes[0]
            name = getattr(p, "description", None) or getattr(p, "serial", None) or "ST-Link V2"
            self.local_probe_status.setText(f"{name} · ● Connected")
            self.local_probe_status.setStyleSheet("font-weight: 700; color: #10B981;")
            self.gw_probe_lbl.setText(f"{name} · ● Connected")
            self.gw_probe_lbl.setStyleSheet("font-weight: 700; color: #10B981;")

    def set_target_info(self, info: Optional[TargetInfo]) -> None:
        self._target_info = info
        if info is not None:
            self.local_target_status.setText(f"STM32F407 · {info.flash_kib}KB · ● Ready")
            self.gw_target_lbl.setText(f"STM32F407 · {info.flash_kib}KB Flash")
        else:
            self.local_target_status.setText("STM32F407 · ○ Chưa kiểm tra")
            self.gw_target_lbl.setText("STM32F407ZET6")


__all__ = ["DebugVsCodeView"]
