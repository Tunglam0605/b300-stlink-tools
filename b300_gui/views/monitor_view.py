"""Dedicated Zero-Halt Live Monitor view for B300 v0.18.

The production window already owns a fully wired ``DebugTab.live_panel`` backed
by ``LiveMonitorSession``.  This view adopts that panel exactly once during
construction, preserving the proven zero-halt backend while giving it a stable
production owner.  Page navigation never reparents it and never starts sampling.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from b300_core.models import ProbeRef
from b300_core.remote_profile import RemoteGatewayProfile, load_remote_profile
from b300_gui.debug_live_panel import DebugLivePanel
from b300_gui.live_monitor_controller import LiveMonitorController, LiveMonitorRequest


class MonitorView(QWidget):
    """Zero-halt Monitor page with permanent live-panel ownership."""

    operation_state_changed = Signal(bool)
    log = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None,
                 *, live_panel: Optional[DebugLivePanel] = None,
                 controller: Optional[LiveMonitorController] = None,
                 selected_probe: Optional[Callable[[], ProbeRef]] = None,
                 openocd_executable: Optional[str] = None,
                 remote_profile_loader: Callable[[], Optional[RemoteGatewayProfile]] = load_remote_profile,
                 ) -> None:
        super().__init__(parent)
        self.setObjectName("monitorViewContainer")
        self._symbols: Optional[Path] = None
        self._remote_profile_loader = remote_profile_loader
        self.live_panel = live_panel or DebugLivePanel(self)
        if self.live_panel.parent() is not self:
            self.live_panel.setParent(self)
        self.controller = controller or LiveMonitorController(
            self.live_panel,
            self,
            selected_probe=selected_probe,
            openocd_executable=openocd_executable,
        )
        if self.controller.panel is not self.live_panel:
            raise ValueError("Live Monitor controller must own the displayed panel.")
        self.controller.operation_state_changed.connect(self.operation_state_changed.emit)
        self.controller.log.connect(self.log.emit)
        self._build_ui()
        self.live_panel.start_button.clicked.connect(self._start_requested)
        self.live_panel.stop_button.clicked.connect(self.controller.stop)
        self.live_panel.clear_button.clicked.connect(self.controller.clear)
        self.live_panel.export_button.clicked.connect(self._export_requested)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        banner = QFrame()
        banner.setObjectName("headerRibbon")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        title_row = QHBoxLayout()
        title = QLabel("LIVE MONITOR · REALTIME ZERO-HALT")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        badge = QLabel("ZERO-HALT")
        badge.setObjectName("safeBadge")
        title_row.addWidget(badge)
        title_row.addStretch(1)
        banner_layout.addLayout(title_row)
        description = QLabel(
            "Theo dõi RAM/DWT qua SWD mà không chủ động halt/reset MCU. "
            "Interactive breakpoint/step nằm riêng trong DEBUG và chỉ chạy sau hành động rõ ràng của người dùng."
        )
        description.setWordWrap(True)
        description.setObjectName("pageSubtitle")
        banner_layout.addWidget(description)
        source_row = QHBoxLayout()
        self.role_selector = QComboBox()
        self.role_selector.setObjectName("monitorRoleSelector")
        self.role_selector.setAccessibleName("Live Monitor connection")
        self.role_selector.addItem("LOCAL", "LOCAL")
        self.role_selector.addItem("CLIENT", "CLIENT")
        self.role_selector.setToolTip(
            "CLIENT uses the saved Gateway profile; transport ports stay safety-managed."
        )
        source_row.addWidget(self.role_selector)
        self.symbol_path = QLineEdit()
        self.symbol_path.setReadOnly(True)
        self.symbol_path.setPlaceholderText("Chọn ELF/AXF của firmware đang chạy")
        self.symbol_path.setAccessibleName("ELF hoặc AXF cho Live Monitor")
        source_row.addWidget(self.symbol_path, 1)
        self.symbol_button = QPushButton("ELF/AXF…")
        self.symbol_button.clicked.connect(self._choose_symbols)
        source_row.addWidget(self.symbol_button)
        banner_layout.addLayout(source_row)
        layout.addWidget(banner)
        layout.addWidget(self.live_panel, 1)

    def _choose_symbols(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self, "Chọn symbol firmware", "", "ELF / AXF (*.elf *.axf)",
        )
        if selected:
            try:
                self.set_symbols(Path(selected))
            except ValueError as error:
                self.live_panel.mark_failed(str(error))

    def set_symbols(self, path: Path) -> None:
        selected = Path(path).expanduser().resolve()
        if selected.suffix.lower() not in {".elf", ".axf"} or not selected.is_file():
            raise ValueError("Live Monitor cần file ELF/AXF hợp lệ.")
        self._symbols = selected
        self.symbol_path.setText(str(selected))

    def _start_requested(self) -> None:
        if self._symbols is None:
            self.live_panel.mark_failed("Chọn ELF/AXF trước khi bắt đầu.")
            return
        try:
            if self.role_selector.currentData() == "CLIENT":
                profile = self._remote_profile_loader()
                if profile is None:
                    raise RuntimeError(
                        "CLIENT cần Gateway profile đã lưu trong DEBUG hoặc SETTINGS."
                    )
                selected = profile.validate()
                request = LiveMonitorRequest.client(
                    self._symbols,
                    host=selected.host,
                    user=selected.user,
                    ssh_port=selected.port,
                )
            else:
                request = LiveMonitorRequest.local(self._symbols)
            self.controller.start(request)
        except (OSError, RuntimeError, ValueError) as error:
            self.live_panel.mark_failed(str(error))

    def _export_requested(self) -> None:
        try:
            self.controller.export(self)
        except (OSError, RuntimeError, ValueError) as error:
            self.live_panel.mark_failed(str(error))

    @property
    def buffer(self):
        return self.live_panel.buffer

    @property
    def table(self):
        return self.live_panel.table

    def set_control_state(self, *args, **kwargs):
        return self.live_panel.set_control_state(*args, **kwargs)

    def append_live_sample(self, sample):
        return self.live_panel.append_live_sample(sample)

    def apply_analytics(self, snapshot):
        return self.live_panel.apply_analytics(snapshot)

    def reset_for_sampling(self):
        return self.live_panel.reset_for_sampling()

    def mark_stopping(self):
        return self.live_panel.mark_stopping()

    def mark_live_completed(self, summary):
        return self.live_panel.mark_live_completed(summary)

    def mark_failed(self, message):
        return self.live_panel.mark_failed(message)


__all__ = ["MonitorView"]
