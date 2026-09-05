"""Shared-profile Zero-Halt Live Monitor view."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from b300_core.gateway_profiles import GatewayProfile
from b300_core.models import ProbeRef
from b300_core.project_profiles import ProjectProfile
from b300_core.remote_profile import RemoteGatewayProfile, load_remote_profile
from b300_gui.debug_live_panel import DebugLivePanel
from b300_gui.live_monitor_controller import LiveMonitorController, LiveMonitorRequest


class MonitorView(QWidget):
    operation_state_changed = Signal(bool)
    log = Signal(str)
    manage_gateways_requested = Signal()
    manage_projects_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, *, live_panel: Optional[DebugLivePanel] = None,
                 controller: Optional[LiveMonitorController] = None,
                 selected_probe: Optional[Callable[[], ProbeRef]] = None,
                 openocd_executable: Optional[str] = None,
                 remote_session_provider=None,
                 hardware_busy: Optional[Callable[[], bool]] = None,
                 remote_profile_loader: Callable[[], Optional[RemoteGatewayProfile]] = load_remote_profile) -> None:
        super().__init__(parent)
        self.setObjectName("monitorViewContainer")
        self._symbols: Optional[Path] = None
        self._remote_profile_loader = remote_profile_loader
        self._gateway_profiles = {}
        self._project_profiles = {}
        self.live_panel = live_panel or DebugLivePanel(self)
        if self.live_panel.parent() is not self: self.live_panel.setParent(self)
        self.controller = controller or LiveMonitorController(
            self.live_panel, self, selected_probe=selected_probe, remote_session_provider=remote_session_provider,
            hardware_busy=hardware_busy, openocd_executable=openocd_executable)
        if self.controller.panel is not self.live_panel:
            raise ValueError("Live Monitor controller must own the displayed panel.")
        self.controller.operation_state_changed.connect(self.operation_state_changed.emit)
        self.controller.log.connect(self.log.emit)
        self._build_ui()
        if hasattr(self.live_panel,"browse_symbols_btn"):
            self.live_panel.browse_symbols_btn.hide()
        self.live_panel.start_button.clicked.connect(self._start_requested)
        self.live_panel.stop_button.clicked.connect(self.controller.stop)
        self.live_panel.clear_button.clicked.connect(self.controller.clear)
        self.live_panel.export_button.clicked.connect(self._export_requested)

    def _build_ui(self) -> None:
        layout=QVBoxLayout(self); layout.setContentsMargins(14,10,14,10); layout.setSpacing(8)
        banner=QFrame(); banner.setObjectName("headerRibbon"); bl=QVBoxLayout(banner); bl.setContentsMargins(12,8,12,8)
        title_row=QHBoxLayout(); title=QLabel("Live Monitor"); title.setObjectName("sectionTitle"); title_row.addWidget(title); badge=QLabel("ZERO-HALT"); badge.setObjectName("safeBadge"); title_row.addWidget(badge); title_row.addStretch(1); bl.addLayout(title_row)
        desc=QLabel("Đọc RAM/DWT qua SWD mà không chủ động halt/reset MCU. Project và Gateway dùng chung với DEBUG."); desc.setWordWrap(True); desc.setObjectName("pageSubtitle"); bl.addWidget(desc)
        source=QHBoxLayout(); source.addWidget(QLabel("Mode")); self.role_selector=QComboBox(); self.role_selector.addItem("Local","LOCAL"); self.role_selector.addItem("Client","CLIENT"); source.addWidget(self.role_selector)
        source.addWidget(QLabel("Project")); self.project_selector=QComboBox(); self.project_selector.setObjectName("monitorProjectSelector"); source.addWidget(self.project_selector,1); self.btn_manage_projects=QPushButton("Manage…"); self.btn_manage_projects.clicked.connect(self.manage_projects_requested.emit); source.addWidget(self.btn_manage_projects); bl.addLayout(source)
        self.gateway_row=QWidget(); gr=QHBoxLayout(self.gateway_row); gr.setContentsMargins(0,0,0,0); gr.addWidget(QLabel("Gateway")); self.gateway_selector=QComboBox(); self.gateway_selector.setObjectName("monitorGatewaySelector"); gr.addWidget(self.gateway_selector,1); self.btn_manage_gateways=QPushButton("Manage…"); self.btn_manage_gateways.clicked.connect(self.manage_gateways_requested.emit); gr.addWidget(self.btn_manage_gateways); bl.addWidget(self.gateway_row)
        self.symbol_path=QLineEdit(); self.symbol_path.setReadOnly(True); self.symbol_path.hide(); self.symbol_button=QPushButton("ELF/AXF…"); self.symbol_button.hide()
        layout.addWidget(banner); layout.addWidget(self.live_panel,1)
        self.role_selector.currentIndexChanged.connect(self._role_changed); self.project_selector.currentIndexChanged.connect(self._project_changed); self._role_changed()

    def set_gateway_profiles(self, profiles: Iterable[GatewayProfile], default_id: Optional[str] = None) -> None:
        items=tuple(p.validate() for p in profiles); self._gateway_profiles={p.profile_id:p for p in items}; current=self.gateway_selector.currentData(); self.gateway_selector.blockSignals(True); self.gateway_selector.clear()
        for p in items: self.gateway_selector.addItem("%s · %s"%(p.name,p.display_endpoint),p.profile_id)
        wanted=current if current in self._gateway_profiles else default_id
        if wanted in self._gateway_profiles: self.gateway_selector.setCurrentIndex(self.gateway_selector.findData(wanted))
        self.gateway_selector.blockSignals(False)

    def set_project_profiles(self, profiles: Iterable[ProjectProfile], default_id: Optional[str] = None) -> None:
        items=tuple(p.validate() for p in profiles); self._project_profiles={p.project_id:p for p in items}; current=self.project_selector.currentData(); self.project_selector.blockSignals(True); self.project_selector.clear()
        for p in items: self.project_selector.addItem(p.name,p.project_id)
        wanted=current if current in self._project_profiles else default_id
        if wanted in self._project_profiles: self.project_selector.setCurrentIndex(self.project_selector.findData(wanted))
        self.project_selector.blockSignals(False); self._project_changed()

    def _role_changed(self) -> None:
        self.gateway_row.setVisible(self.role_selector.currentData()=="CLIENT")

    def _project_changed(self) -> None:
        profile=self._project_profiles.get(self.project_selector.currentData())
        if profile is not None:
            self._symbols=Path(profile.symbols); self.symbol_path.setText(str(profile.symbols))

    def set_hardware_busy(self,busy:bool)->None:
        enabled=not busy
        for w in (self.live_panel.start_button, self.role_selector, self.project_selector,
                  self.gateway_selector, self.btn_manage_projects, self.btn_manage_gateways,
                  self.symbol_button):
            w.setEnabled(enabled)
        if hasattr(self.live_panel, "browse_symbols_btn"):
            self.live_panel.browse_symbols_btn.setEnabled(enabled)

    def set_symbols(self,path:Path)->None:
        selected=Path(path).expanduser().resolve()
        if selected.suffix.lower() not in {".elf",".axf"} or not selected.is_file(): raise ValueError("Live Monitor cần file ELF/AXF hợp lệ.")
        self._symbols=selected; self.symbol_path.setText(str(selected))

    def _selected_symbols(self)->Optional[Path]:
        project=self._project_profiles.get(self.project_selector.currentData())
        if project is not None: return Path(project.symbols)
        return self._symbols

    def _selected_gateway(self)->Optional[GatewayProfile]:
        return self._gateway_profiles.get(self.gateway_selector.currentData())

    def _start_requested(self)->None:
        symbols=self._selected_symbols()
        if symbols is None:
            self.live_panel.mark_failed("Chọn Debug Project trước khi bắt đầu."); return
        try:
            symbols=Path(symbols).expanduser().resolve()
            if not symbols.is_file(): raise RuntimeError("ELF/AXF của Debug Project không còn tồn tại.")
            if self.role_selector.currentData()=="CLIENT":
                gateway=self._selected_gateway()
                if gateway is None:
                    legacy=self._remote_profile_loader()
                    if legacy is None: raise RuntimeError("CLIENT cần một Gateway đã lưu trong Gateway Manager.")
                    endpoint=legacy.validate()
                else: endpoint=gateway.endpoint.validate()
                request=LiveMonitorRequest.client(symbols,host=endpoint.host,user=endpoint.user,ssh_port=endpoint.port)
            else:
                request=LiveMonitorRequest.local(symbols)
            self.controller.start(request)
        except (OSError,RuntimeError,ValueError) as error: self.live_panel.mark_failed(str(error))

    def _export_requested(self)->None:
        try: self.controller.export(self)
        except (OSError,RuntimeError,ValueError) as error: self.live_panel.mark_failed(str(error))

    @property
    def buffer(self): return self.live_panel.buffer
    @property
    def table(self): return self.live_panel.table
    def set_control_state(self,*args,**kwargs): return self.live_panel.set_control_state(*args,**kwargs)
    def append_live_sample(self,sample): return self.live_panel.append_live_sample(sample)
    def apply_analytics(self,snapshot): return self.live_panel.apply_analytics(snapshot)
    def reset_for_sampling(self): return self.live_panel.reset_for_sampling()
    def mark_stopping(self): return self.live_panel.mark_stopping()
    def mark_live_completed(self,summary): return self.live_panel.mark_live_completed(summary)
    def mark_failed(self,message): return self.live_panel.mark_failed(message)


__all__=["MonitorView"]
