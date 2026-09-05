"""Production Monitor observes the application's shared project and connection."""
from pathlib import Path
from typing import Callable, Optional
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget, QScrollArea
from b300_core.models import ProbeRef
from b300_core.remote_profile import load_remote_profile
from b300_gui.production_live_panel import ProductionLivePanel
from b300_gui.live_monitor_controller import LiveMonitorController, LiveMonitorRequest

class MonitorView(QWidget):
    operation_state_changed = Signal(bool)
    log = Signal(str)
    manage_gateways_requested = Signal()
    manage_projects_requested = Signal()
    open_vscode_requested = Signal()

    def __init__(self, parent=None, *, context=None, live_panel=None, controller=None,
                 selected_probe: Optional[Callable[[], ProbeRef]] = None,
                 openocd_executable=None, remote_session_provider=None, hardware_busy=None,
                 remote_profile_loader=load_remote_profile):
        super().__init__(parent)
        self.setObjectName("monitorViewContainer")
        self._context = None
        self._symbols = None
        self._fallback_project = None
        self._fallback_gateway = None
        self.live_panel = live_panel or ProductionLivePanel(self)
        if self.live_panel.parent() is not self:
            self.live_panel.setParent(self)
        self.controller = controller or LiveMonitorController(
            self.live_panel, self, selected_probe=selected_probe,
            remote_session_provider=remote_session_provider, hardware_busy=hardware_busy,
            openocd_executable=openocd_executable)
        if self.controller.panel is not self.live_panel:
            raise ValueError("Live Monitor controller must own the displayed panel.")
        self._build_ui()
        self.controller.operation_state_changed.connect(self.operation_state_changed.emit)
        self.controller.operation_state_changed.connect(self._render_context)
        self.controller.log.connect(self.log.emit)
        self.controller.log.connect(self.append_log)
        self.live_panel.start_button.clicked.connect(self._start_requested)
        self.live_panel.stop_button.clicked.connect(self.controller.stop)
        self.live_panel.clear_button.clicked.connect(self.controller.clear)
        self.live_panel.export_button.clicked.connect(self._export_requested)
        if isinstance(self.live_panel, ProductionLivePanel):
            self.live_panel.sample_received.connect(self._render_sample_summary)
            self.live_panel.history_cleared.connect(self._clear_sample_summary)
        self.bind_context(context)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        body_widget = QWidget()
        body = QHBoxLayout(body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self.live_panel.setMinimumHeight(620)
        body.addWidget(self.live_panel, 4)
        self.session_card = QFrame()
        self.session_card.setObjectName("engineeringCard")
        self.session_card.setMinimumWidth(170)
        self.session_card.setMaximumWidth(400)
        summary = QVBoxLayout(self.session_card)
        summary.setContentsMargins(16, 16, 16, 16)
        title = QLabel("PHIÊN THEO DÕI")
        title.setObjectName("sectionTitle")
        summary.addWidget(title)
        self.context_summary = QLabel()
        self.context_summary.setWordWrap(True)
        summary.addWidget(self.context_summary)
        self.session_state = QLabel("Chưa bắt đầu")
        summary.addWidget(self.session_state)
        self.last_sample = QLabel("Mẫu gần nhất: —")
        self.sample_health = QLabel("Chất lượng mẫu: chưa kiểm tra")
        self.sample_health.setWordWrap(True)
        summary.addWidget(self.last_sample)
        summary.addWidget(self.sample_health)
        notice = QLabel("Theo dõi zero-halt\nGiá trị chỉ xuất hiện sau khi nhận được mẫu.")
        notice.setWordWrap(True)
        summary.addWidget(notice)
        summary.addStretch()
        self.session_card.hide()
        body.addWidget(self.session_card, 1)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setWidget(body_widget)
        root.addWidget(self.content_scroll, 1)
        self.monitor_log = QPlainTextEdit()
        self.monitor_log.setReadOnly(True)
        self.monitor_log.setPlaceholderText("Nhật ký theo dõi · chưa có hoạt động")
        self.monitor_log.setMaximumBlockCount(200)
        self.monitor_log.setMaximumHeight(76)
        root.addWidget(self.monitor_log)

    def resizeEvent(self, event):
        self.session_card.setVisible(self.width() >= 1100)
        super().resizeEvent(event)

    def append_log(self, message):
        self.monitor_log.appendPlainText(str(message))
        self._render_context()

    @property
    def context(self):
        return self._context

    def bind_context(self, context):
        if self._context is not None:
            try:
                self._context.changed.disconnect(self._render_context)
            except (RuntimeError, TypeError):
                pass
        self._context = context
        if context is not None:
            context.changed.connect(self._render_context)
        self._render_context()

    def _render_context(self, *_args):
        project = self._selected_project()
        connection = getattr(self._context, "selected_connection", None)
        project_name = getattr(project, "name", "Chưa chọn dự án")
        connection_name = getattr(connection, "name", "Chưa chọn kết nối")
        self.context_summary.setText("Dự án\n%s\n\nKết nối\n%s" % (project_name, connection_name))
        self.session_state.setText("Đang theo dõi" if self.controller.active else "Đang chờ")

    def _render_sample_summary(self, sample):
        self.last_sample.setText("Mẫu gần nhất: %.3f s" % sample.captured_elapsed_seconds)
        quality = "giá trị không nhất quán" if any(not value.coherent for value in sample.values) else "giá trị nhất quán"
        self.sample_health.setText("Chất lượng mẫu: " + quality)

    def _clear_sample_summary(self):
        self.last_sample.setText("Mẫu gần nhất: —")
        self.sample_health.setText("Chất lượng mẫu: chưa kiểm tra")

    def _selected_project(self):
        return self._context.selected_project if self._context is not None else self._fallback_project

    def _selected_gateway(self):
        connection = getattr(self._context, "selected_connection", None)
        return getattr(connection, "gateway", None) if self._context is not None else self._fallback_gateway

    def _selected_symbols(self):
        project = self._selected_project()
        return Path(project.symbols) if project is not None and project.symbols else self._symbols

    def set_symbols(self, path):
        selected = Path(path).expanduser().resolve()
        if selected.suffix.lower() not in {".elf", ".axf"} or not selected.is_file():
            raise ValueError("Theo dõi trực tiếp yêu cầu tệp ELF/AXF hiện có.")
        self._symbols = selected

    def set_project_profiles(self, profiles, default_id=None):
        items = tuple(profiles)
        self._fallback_project = next((p for p in items if p.project_id == default_id), items[0] if items else None)
        self._render_context()

    def set_gateway_profiles(self, profiles, default_id=None):
        items = tuple(profiles)
        self._fallback_gateway = next((p for p in items if p.profile_id == default_id), items[0] if items else None)
        self._render_context()

    def set_hardware_busy(self, busy):
        self.live_panel.start_button.setEnabled(not busy)

    def _start_requested(self):
        symbols = self._selected_symbols()
        connection = getattr(self._context, "selected_connection", None)
        if symbols is None:
            self.live_panel.status.setText("Chọn dự án có tệp ELF/AXF trên thanh dùng chung.")
            return
        if self._context is not None and connection is None:
            self.live_panel.status.setText("Chọn kết nối trên thanh dùng chung.")
            return
        try:
            symbols = Path(symbols).expanduser().resolve()
            if not symbols.is_file():
                raise RuntimeError("Tệp ELF/AXF của dự án không còn tồn tại.")
            if connection is not None and not connection.is_local:
                gateway = self._selected_gateway()
                if gateway is None:
                    raise RuntimeError("Kết nối đã chọn chưa có cấu hình máy trung gian.")
                endpoint = gateway.endpoint.validate()
                request = LiveMonitorRequest.client(symbols, host=endpoint.host,
                                                    user=endpoint.user, ssh_port=endpoint.port)
            else:
                request = LiveMonitorRequest.local(symbols)
            self.controller.start(request)
        except (OSError, RuntimeError, ValueError) as error:
            self.live_panel.mark_failed(str(error))

    def _export_requested(self):
        try:
            self.controller.export(self)
        except (OSError, RuntimeError, ValueError) as error:
            self.live_panel.mark_failed(str(error))

    @property
    def buffer(self): return self.live_panel.buffer
    @property
    def table(self): return self.live_panel.table
    def set_control_state(self, *args, **kwargs): return self.live_panel.set_control_state(*args, **kwargs)
    def append_live_sample(self, sample): return self.live_panel.append_live_sample(sample)
    def apply_analytics(self, snapshot): return self.live_panel.apply_analytics(snapshot)
    def reset_for_sampling(self): return self.live_panel.reset_for_sampling()
    def mark_stopping(self): return self.live_panel.mark_stopping()
    def mark_live_completed(self, summary): return self.live_panel.mark_live_completed(summary)
    def mark_failed(self, message): return self.live_panel.mark_failed(message)

__all__ = ["MonitorView"]
