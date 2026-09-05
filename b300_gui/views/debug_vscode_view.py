"""Shared-profile VS Code debug surface for B300 v0.19."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from b300_core.gateway_profiles import GatewayProfile
from b300_core.models import ProbeInfo, TargetInfo
from b300_core.project_profiles import ProjectProfile
from b300_core.vscode_environment import VsCodeEnvironmentStatus
from b300_gui.collapsible_card import CollapsibleCard


class DebugVsCodeView(QWidget):
    """LOCAL/GATEWAY/CLIENT view; shared profiles own endpoint/project data."""

    open_local_vscode_requested = Signal(Path, Path)
    open_remote_vscode_requested = Signal(object)
    test_client_connection_requested = Signal(object)
    start_gateway_requested = Signal()
    stop_gateway_requested = Signal()
    stop_bridge_requested = Signal()
    refresh_environment_requested = Signal()
    legacy_ide_requested = Signal()
    manage_gateways_requested = Signal()
    manage_projects_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_mode = "local"
        self._probes = ()
        self._target_info: Optional[TargetInfo] = None
        self._environment: Optional[VsCodeEnvironmentStatus] = None
        self._gateway_profiles = {}
        self._project_profiles = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(4,4,4,4); root.setSpacing(10)
        hero = QFrame(); hero.setObjectName("debugVsCodeHero")
        hero_layout = QVBoxLayout(hero); hero_layout.setContentsMargins(12,10,12,10); hero_layout.setSpacing(6)
        title = QLabel("Debug mode"); title.setObjectName("sectionTitle"); hero_layout.addWidget(title)
        subtitle = QLabel("B300 giữ ST-Link/OpenOCD/SSH và run-state safety; VS Code + Cortex-Debug thực hiện breakpoint, step và watch.")
        subtitle.setWordWrap(True); subtitle.setObjectName("pageSubtitle"); hero_layout.addWidget(subtitle)
        row=QHBoxLayout(); self.btn_mode_local=self._mode_button("Local"); self.btn_mode_gateway=self._mode_button("Gateway"); self.btn_mode_client=self._mode_button("Client")
        self.btn_mode_local.setChecked(True)
        self.btn_mode_local.clicked.connect(lambda:self.select_mode("local")); self.btn_mode_gateway.clicked.connect(lambda:self.select_mode("gateway")); self.btn_mode_client.clicked.connect(lambda:self.select_mode("client"))
        for b in (self.btn_mode_local,self.btn_mode_gateway,self.btn_mode_client): row.addWidget(b,1)
        hero_layout.addLayout(row); root.addWidget(hero)
        root.addWidget(self._build_environment_card())
        self.mode_stack=QStackedWidget(); self.mode_stack.addWidget(self._build_local_page()); self.mode_stack.addWidget(self._build_gateway_page()); self.mode_stack.addWidget(self._build_client_page()); root.addWidget(self.mode_stack,1)
        footer=QHBoxLayout(); self.bridge_status=QLabel("Debug bridge: STOPPED"); self.bridge_status.setObjectName("debugBridgeStatus"); footer.addWidget(self.bridge_status,1)
        self.btn_stop_bridge=QPushButton("Stop Debug"); self.btn_stop_bridge.setEnabled(False); self.btn_stop_bridge.clicked.connect(self.stop_bridge_requested.emit); footer.addWidget(self.btn_stop_bridge); root.addLayout(footer)

    def _mode_button(self,text):
        b=QPushButton(text); b.setObjectName("debugModeButton"); b.setCheckable(True); b.setMinimumHeight(34); return b

    def _build_environment_card(self):
        card=CollapsibleCard("Debug environment","VS Code, Cortex-Debug và managed runtime",expanded=False,parent=self); card.setObjectName("debugEnvironmentCard"); self.environment_details_card=card
        grid=QGridLayout(); self.env_vscode=QLabel("VS Code · chưa kiểm tra"); self.env_cortex=QLabel("Cortex-Debug · chưa kiểm tra"); self.env_gdb=QLabel("ARM GDB · chưa kiểm tra"); self.env_openocd=QLabel("OpenOCD · B300 managed")
        grid.addWidget(self.env_vscode,0,0); grid.addWidget(self.env_cortex,0,1); grid.addWidget(self.env_gdb,1,0); grid.addWidget(self.env_openocd,1,1)
        self.env_detail=QLabel("B300 không yêu cầu OpenOCD trên PATH."); self.env_detail.setWordWrap(True); self.env_detail.setObjectName("mutedLabel"); grid.addWidget(self.env_detail,2,0,1,2); card.content_layout.addLayout(grid)
        self.btn_refresh_environment=QPushButton("Check"); self.btn_refresh_environment.clicked.connect(self.refresh_environment_requested.emit); card.add_header_widget(self.btn_refresh_environment); return card

    def _project_row(self, combo: QComboBox):
        row=QHBoxLayout(); row.addWidget(QLabel("Project")); row.addWidget(combo,1); manage=QPushButton("Manage…"); manage.clicked.connect(self.manage_projects_requested.emit); row.addWidget(manage); return row,manage

    def _build_local_page(self):
        page=QWidget(); layout=QVBoxLayout(page); layout.setContentsMargins(8,8,8,8); layout.setSpacing(8)
        self.local_target_status=QLabel("STM32F407ZE · chưa kiểm tra Target"); self.local_target_status.setObjectName("debugTargetStatus"); layout.addWidget(self.local_target_status)
        self.local_project_combo=QComboBox(); self.local_project_combo.setObjectName("localProjectSelector"); row,self.btn_manage_local_projects=self._project_row(self.local_project_combo); layout.addLayout(row)
        self.local_workspace=QLineEdit(); self.local_elf=QLineEdit(); self.local_workspace.hide(); self.local_elf.hide()
        self.local_project_combo.currentIndexChanged.connect(self._sync_local_project)
        safety=QLabel("Attach-only · hardware breakpoint/watchpoint · B300 giữ run-state safety."); safety.setWordWrap(True); layout.addWidget(safety)
        self.btn_open_local_vscode=QPushButton("Open Debug in VS Code"); self.btn_open_local_vscode.setMinimumHeight(42); self.btn_open_local_vscode.setObjectName("primaryActionButton"); self.btn_open_local_vscode.clicked.connect(self._emit_local_request); layout.addWidget(self.btn_open_local_vscode); layout.addStretch(1); return page

    def _build_gateway_page(self):
        page=QWidget(); layout=QVBoxLayout(page); layout.setContentsMargins(8,8,8,8); layout.setSpacing(8)
        summary=QLabel("Gateway role dùng máy hiện tại làm ST-Link/OpenOCD host; GDB/TCL chỉ bind loopback."); summary.setWordWrap(True); layout.addWidget(summary)
        self.gateway_details_card=CollapsibleCard("Connection details","Loopback endpoints và run-state guard",expanded=False,parent=page)
        self.gw_openocd_lbl=QLabel("OpenOCD private endpoints · GDB 127.0.0.1:3333 · TCL 127.0.0.1:6666 · Telnet OFF"); self.gw_openocd_lbl.setWordWrap(True); self.gateway_details_card.content_layout.addWidget(self.gw_openocd_lbl); layout.addWidget(self.gateway_details_card)
        self.btn_start_gateway=QPushButton("Start Gateway"); self.btn_start_gateway.setObjectName("primaryActionButton"); self.btn_start_gateway.clicked.connect(self.start_gateway_requested.emit); layout.addWidget(self.btn_start_gateway)
        self.btn_stop_gateway=QPushButton("Stop Gateway"); self.btn_stop_gateway.hide(); self.btn_stop_gateway.clicked.connect(self.stop_gateway_requested.emit)
        self.gateway_state_label=QLabel("Gateway: STOPPED"); self.gateway_state_label.setObjectName("gatewayStateLabel"); layout.addWidget(self.gateway_state_label); layout.addStretch(1); return page

    def _build_client_page(self):
        page=QWidget(); layout=QVBoxLayout(page); layout.setContentsMargins(8,8,8,8); layout.setSpacing(8)
        gateway_row=QHBoxLayout(); gateway_row.addWidget(QLabel("Gateway")); self.client_gateway_combo=QComboBox(); self.client_gateway_combo.setObjectName("clientGatewaySelector"); gateway_row.addWidget(self.client_gateway_combo,1); self.btn_manage_gateways=QPushButton("Manage…"); self.btn_manage_gateways.clicked.connect(self.manage_gateways_requested.emit); gateway_row.addWidget(self.btn_manage_gateways); layout.addLayout(gateway_row)
        self.client_project_combo=QComboBox(); self.client_project_combo.setObjectName("clientProjectSelector"); prow,self.btn_manage_client_projects=self._project_row(self.client_project_combo); layout.addLayout(prow)
        self.client_host=QLineEdit(); self.client_user=QLineEdit(); self.client_ssh_port=QSpinBox(); self.client_ssh_port.setRange(1,65535); self.client_ssh_port.setValue(22); self.client_workspace=QLineEdit(); self.client_elf=QLineEdit()
        for widget in (self.client_host,self.client_user,self.client_ssh_port,self.client_workspace,self.client_elf): widget.hide()
        self.client_gateway_combo.currentIndexChanged.connect(self._sync_client_gateway); self.client_project_combo.currentIndexChanged.connect(self._sync_client_project)
        self.client_details_card=CollapsibleCard("Connection details","Optional connectivity preflight and local GDB port",expanded=False,parent=page)
        detail=QGridLayout(); self.client_local_gdb_spin=QSpinBox(); self.client_local_gdb_spin.setRange(0,65535); self.client_local_gdb_spin.setValue(0); self.client_local_gdb_spin.setToolTip("0 = B300 tự chọn local port trống")
        detail.addWidget(QLabel("Local GDB port"),0,0); detail.addWidget(self.client_local_gdb_spin,0,1); self.client_details_card.content_layout.addLayout(detail)
        self.btn_test_client_conn=QPushButton("Test connection"); self.btn_test_client_conn.clicked.connect(self._emit_client_connection_test); self.client_details_card.content_layout.addWidget(self.btn_test_client_conn); layout.addWidget(self.client_details_card)
        self.btn_open_remote_vscode=QPushButton("Open Remote Debug in VS Code"); self.btn_open_remote_vscode.setObjectName("primaryActionButton"); self.btn_open_remote_vscode.setMinimumHeight(42); self.btn_open_remote_vscode.clicked.connect(self._emit_remote_request); layout.addWidget(self.btn_open_remote_vscode)
        self.client_state_label=QLabel("SSH / GDB tunnel: DISCONNECTED"); self.client_state_label.setObjectName("clientTunnelStatus"); layout.addWidget(self.client_state_label); layout.addStretch(1); return page

    def select_mode(self,mode):
        selected=str(mode).lower(); mapping={"local":0,"gateway":1,"client":2}
        if selected not in mapping: return
        self._current_mode=selected; self.mode_stack.setCurrentIndex(mapping[selected]); self.btn_mode_local.setChecked(selected=="local"); self.btn_mode_gateway.setChecked(selected=="gateway"); self.btn_mode_client.setChecked(selected=="client")

    def set_gateway_profiles(self, profiles: Iterable[GatewayProfile], default_id: Optional[str] = None) -> None:
        current=self.client_gateway_combo.currentData(); items=tuple(p.validate() for p in profiles); self._gateway_profiles={p.profile_id:p for p in items}; self.client_gateway_combo.blockSignals(True); self.client_gateway_combo.clear()
        for p in items: self.client_gateway_combo.addItem("%s · %s"%(p.name,p.display_endpoint),p.profile_id)
        wanted=current if current in self._gateway_profiles else default_id
        if wanted in self._gateway_profiles: self.client_gateway_combo.setCurrentIndex(self.client_gateway_combo.findData(wanted))
        self.client_gateway_combo.blockSignals(False); self._sync_client_gateway()

    def set_project_profiles(self, profiles: Iterable[ProjectProfile], default_id: Optional[str] = None) -> None:
        items=tuple(p.validate() for p in profiles); self._project_profiles={p.project_id:p for p in items}
        for combo in (self.local_project_combo,self.client_project_combo):
            current=combo.currentData(); combo.blockSignals(True); combo.clear()
            for p in items: combo.addItem(p.name,p.project_id)
            wanted=current if current in self._project_profiles else default_id
            if wanted in self._project_profiles: combo.setCurrentIndex(combo.findData(wanted))
            combo.blockSignals(False)
        self._sync_local_project(); self._sync_client_project()

    def _sync_local_project(self):
        p=self._project_profiles.get(self.local_project_combo.currentData()); self.local_workspace.setText(str(p.workspace) if p else ""); self.local_elf.setText(str(p.symbols) if p else "")
    def _sync_client_project(self):
        p=self._project_profiles.get(self.client_project_combo.currentData()); self.client_workspace.setText(str(p.workspace) if p else ""); self.client_elf.setText(str(p.symbols) if p else "")
    def _sync_client_gateway(self):
        p=self._gateway_profiles.get(self.client_gateway_combo.currentData())
        self.client_host.setText(p.endpoint.host if p else ""); self.client_user.setText(p.endpoint.user if p else ""); self.client_ssh_port.setValue(p.endpoint.port if p else 22)

    def set_probes(self,probes:Iterable[ProbeInfo])->None:
        self._probes=tuple(probes)
        if self._probes:
            probe=self._probes[0]; self.local_target_status.setToolTip("ST-Link: %s · %s"%(probe.name,probe.serial or "auto-select"))
        else: self.local_target_status.setToolTip("Không phát hiện ST-Link")
    def set_target_info(self,info:Optional[TargetInfo])->None:
        self._target_info = info
        if info is None:
            self.local_target_status.setText("Chưa đọc target")
            return
        target = "STM32F407" if info.device_id & 0xFFF == 0x413 else "STM32 ID 0x%03X" % (info.device_id & 0xFFF)
        self.local_target_status.setText("%s · %dKB Flash · %.2fV · %s" % (
            target, info.flash_kib, info.target_voltage, info.protection_summary,
        ))
    def set_environment_status(self,status:VsCodeEnvironmentStatus)->None:
        self._environment=status; self.env_vscode.setText("VS Code · %s"%("✓ READY" if status.vscode_ready else "✕ MISSING")); self.env_cortex.setText("Cortex-Debug · %s"%("✓ READY" if status.cortex_debug_ready else "✕ MISSING")); self.env_gdb.setText("ARM GDB · %s"%("✓ READY" if status.gdb_ready else "✕ MISSING")); detail=status.reason or "Môi trường debug đã sẵn sàng."; self.env_detail.setText(detail)
    def set_bridge_state(self,role:Optional[str],state:str,detail:str="",gdb_target:Optional[str]=None)->None:
        role_text=role or "NONE"; self.bridge_status.setText("Debug: %s · %s"%(role_text,state)); self.bridge_status.setToolTip(" · ".join(x for x in (detail,gdb_target or "") if x)); active=str(state).upper()=="READY"; self.btn_stop_bridge.setEnabled(active); gateway_active=active and str(role_text).upper()=="GATEWAY"; self.btn_start_gateway.setEnabled(not active); self.btn_stop_gateway.setEnabled(gateway_active); self.gateway_state_label.setText("Gateway: READY" if gateway_active else "Gateway: STOPPED"); client_active=active and str(role_text).upper()=="CLIENT"; self.client_state_label.setText("Remote debug: READY" if client_active else "SSH / GDB tunnel: DISCONNECTED")
    def set_client_connection_status(self,connected:bool,detail:str="")->None:
        self.client_state_label.setText("SSH: CONNECTED" if connected else "SSH / GDB tunnel: DISCONNECTED"); self.client_state_label.setToolTip(detail)
    def set_hardware_busy(self,busy:bool)->None:
        enabled=not busy
        for w in (self.btn_open_local_vscode,self.btn_start_gateway,self.btn_test_client_conn,self.btn_open_remote_vscode,self.client_gateway_combo,self.local_project_combo,self.client_project_combo,self.btn_manage_gateways,self.btn_manage_local_projects,self.btn_manage_client_projects): w.setEnabled(enabled)

    def _emit_local_request(self)->None: self.open_local_vscode_requested.emit(Path(self.local_workspace.text().strip()).expanduser(),Path(self.local_elf.text().strip()).expanduser())
    def _client_request(self)->dict:
        return {"host":self.client_host.text().strip(),"user":self.client_user.text().strip(),"ssh_port":self.client_ssh_port.value(),"local_gdb_port":self.client_local_gdb_spin.value(),"workspace":Path(self.client_workspace.text().strip()).expanduser(),"elf":Path(self.client_elf.text().strip()).expanduser(),"gateway_id":self.client_gateway_combo.currentData(),"project_id":self.client_project_combo.currentData()}
    def _emit_client_connection_test(self)->None: self.test_client_connection_requested.emit(self._client_request())
    def _emit_remote_request(self)->None: self.open_remote_vscode_requested.emit(self._client_request())


__all__=["DebugVsCodeView"]
